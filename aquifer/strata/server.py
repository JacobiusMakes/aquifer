"""Strata API server — the main FastAPI application.

Mounts all route modules, configures middleware (auth, CORS, rate limiting),
and manages application lifecycle (DB, vault manager).

Run with:
    aquifer server                          # Uses env vars
    aquifer server --debug --port 8443      # Dev mode
    AQUIFER_DEBUG=1 uvicorn aquifer.strata.server:app  # Direct uvicorn
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aquifer.strata.auth import AuthContext, resolve_auth
from aquifer.strata.cloud_vault import CloudVaultManager
from aquifer.strata.config import StrataConfig
from aquifer.strata.database import StrataDB
from aquifer.strata.routes import auth_routes, deid_routes, files_routes, vault_routes, practice_routes, dashboard_routes

logger = logging.getLogger(__name__)
_ALLOW_INSECURE_BOOT = os.getenv("AQUIFER_ALLOW_INSECURE_BOOT", "").lower() in {"1", "true", "yes"}

# Public endpoints that don't require authentication
PUBLIC_PATHS = {
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# Dashboard paths use cookie-based auth (handled in dashboard_routes)
DASHBOARD_PATHS_PREFIX = "/dashboard"


def create_app(config: StrataConfig | None = None) -> FastAPI:
    """Create and configure the Strata API application."""
    if config is None:
        config = StrataConfig.from_env()

    config.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        db = StrataDB(config.db_path)
        db.connect()
        app.state.db = db
        app.state.config = config
        app.state.vault_manager = CloudVaultManager(config)
        logger.info(f"Strata server started — data: {config.data_dir}")
        yield
        # Shutdown
        app.state.vault_manager.close_all()
        db.close()
        logger.info("Strata server stopped")

    app = FastAPI(
        title="Aquifer Strata API",
        description=(
            "HIPAA de-identification API with cloud vault storage. "
            "Upload medical/dental files, get de-identified .aqf output."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # --- CORS ---
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if config.debug else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Auth Middleware ---
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next) -> Response:
        # Skip auth for public paths and dashboard (dashboard handles its own cookie auth)
        if (request.url.path in PUBLIC_PATHS
                or request.url.path.startswith("/docs")
                or request.url.path.startswith(DASHBOARD_PATHS_PREFIX)):
            return await call_next(request)

        # Skip auth for OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        auth_ctx = resolve_auth(auth_header, app.state.db, config.jwt_secret)

        if auth_ctx is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing authentication. "
                                   "Provide 'Authorization: Bearer <token>' header."},
            )

        request.state.auth = auth_ctx
        return await call_next(request)

    # --- Request Logging ---
    @app.middleware("http")
    async def log_requests(request: Request, call_next) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        if config.debug:
            logger.info(
                f"{request.method} {request.url.path} → {response.status_code} "
                f"({duration:.3f}s)"
            )
        return response

    # --- Routes ---
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.include_router(deid_routes.router, prefix="/api/v1")
    app.include_router(files_routes.router, prefix="/api/v1")
    app.include_router(vault_routes.router, prefix="/api/v1")
    app.include_router(practice_routes.router, prefix="/api/v1")

    # --- Dashboard (web UI) ---
    app.include_router(dashboard_routes.router)

    # --- Health Check ---
    @app.get("/api/v1/health")
    async def health():
        from aquifer import __version__
        return {
            "status": "healthy",
            "version": __version__,
            "service": "aquifer-strata",
        }

    return app


def _build_runtime_app() -> FastAPI:
    """Create the runtime ASGI app, failing closed unless insecure boot is explicit."""
    try:
        return create_app()
    except Exception as exc:
        if _ALLOW_INSECURE_BOOT:
            logger.warning("Starting Aquifer with insecure boot fallback (dev only)")
            cfg = StrataConfig()
            cfg.debug = True
            cfg.master_key = "INSECURE-DEV-MASTER-KEY-REPLACE-IN-PRODUCTION"
            cfg.jwt_secret = "INSECURE-DEV-JWT-SECRET-REPLACE-IN-PRODUCTION"
            return create_app(cfg)
        raise RuntimeError(
            "Aquifer failed to start safely. Configure AQUIFER_MASTER_KEY and "
            "AQUIFER_JWT_SECRET (or call create_app with an explicit StrataConfig). "
            "Set AQUIFER_ALLOW_INSECURE_BOOT=1 only for isolated local development."
        ) from exc


class _LazyASGIApp:
    """Delay app creation until the ASGI server actually starts handling traffic."""

    def __init__(self) -> None:
        self._app: FastAPI | None = None

    def _get_app(self) -> FastAPI:
        if self._app is None:
            self._app = _build_runtime_app()
        return self._app

    async def __call__(self, scope, receive, send) -> None:
        await self._get_app()(scope, receive, send)

    def __getattr__(self, name: str):
        return getattr(self._get_app(), name)


# For `uvicorn aquifer.strata.server:app`
app = _LazyASGIApp()
