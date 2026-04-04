"""Strata API server — the main FastAPI application.

Mounts all route modules, configures middleware (auth, CORS, rate limiting),
and manages application lifecycle (DB, vault manager).

Run with:
    aquifer server                          # Uses env vars
    aquifer server --debug --port 8443      # Dev mode
    AQUIFER_DEBUG=1 uvicorn aquifer.strata.server:app  # Direct uvicorn
"""

from __future__ import annotations

import collections
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aquifer.strata.auth import AuthContext, AuthResult, resolve_auth
from aquifer.strata.cloud_vault import CloudVaultManager
from aquifer.strata.config import StrataConfig
from aquifer.strata.database import StrataDB
from aquifer.strata.patient_hub import PatientHub
from aquifer.strata.responses import http_exception_handler
from aquifer.strata.routes import auth_routes, audit_routes, deid_routes, files_routes, vault_routes, practice_routes, dashboard_routes
from aquifer.strata.routes import patient_routes
from aquifer.patient_app import routes as patient_app_routes

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

# Patient-app routes use share-key auth, not practice JWTs — skip the JWT middleware
PATIENT_APP_PREFIX = "/api/v1/patient/"

# Dashboard paths use cookie-based auth (handled in dashboard_routes)
DASHBOARD_PATHS_PREFIX = "/dashboard"


def _check_ner_available() -> bool:
    """Return True if spaCy and at least one NER model are importable."""
    try:
        import spacy
        for model in ("en_core_web_lg", "en_core_web_md", "en_core_web_sm"):
            try:
                spacy.load(model)
                return True
            except OSError:
                continue
        return False
    except ImportError:
        return False


def _check_ocr_available() -> bool:
    """Return True if pytesseract is importable."""
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


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
        app.state.email_config = config.email
        app.state.patient_hub = PatientHub(db, app.state.vault_manager, config)

        # Check optional feature availability
        ner_available = _check_ner_available()
        ocr_available = _check_ocr_available()
        logger.info("Optional features: NER=%s, OCR=%s", ner_available, ocr_available)
        if not ner_available:
            logger.warning(
                "NER detection unavailable. Install spaCy + model: "
                "pip install spacy && python -m spacy download en_core_web_sm"
            )
        if not ocr_available:
            logger.warning(
                "OCR unavailable. Install pytesseract: pip install pytesseract"
            )

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

    # --- Rate Limiting ---
    # In-memory sliding window per client key (practice_id or IP).
    _rate_buckets: dict[str, collections.deque] = {}

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next) -> Response:
        # Skip rate limiting for public/dashboard paths
        if (request.url.path in PUBLIC_PATHS
                or request.url.path.startswith(DASHBOARD_PATHS_PREFIX)
                or request.method == "OPTIONS"):
            return await call_next(request)

        # Identify client: use practice_id from auth state if set, else IP
        client_key = request.client.host if request.client else "unknown"
        if hasattr(request.state, "auth") and request.state.auth:
            client_key = f"practice:{request.state.auth.practice_id}"

        # Select limit based on path
        is_deid = request.url.path.startswith("/api/v1/deid")
        limit = config.rate_limit_deid if is_deid else config.rate_limit_default
        window = 60.0  # 1 minute sliding window

        now = time.monotonic()
        bucket = _rate_buckets.setdefault(client_key, collections.deque())

        # Purge expired entries
        while bucket and bucket[0] < now - window:
            bucket.popleft()

        if len(bucket) >= limit:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded. Max {limit} requests per minute."},
                headers={"Retry-After": "60"},
            )

        bucket.append(now)
        return await call_next(request)

    # --- Auth Middleware ---
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next) -> Response:
        # Skip auth for public paths, dashboard, and patient-app (uses share-key auth)
        if (request.url.path in PUBLIC_PATHS
                or request.url.path.startswith("/docs")
                or request.url.path.startswith(DASHBOARD_PATHS_PREFIX)
                or request.url.path.startswith(PATIENT_APP_PREFIX)):
            return await call_next(request)

        # Skip auth for OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        auth_result = resolve_auth(auth_header, app.state.db, config.jwt_secret)

        if not auth_result.ok:
            return JSONResponse(
                status_code=401,
                content={"detail": auth_result.error},
            )

        request.state.auth = auth_result.context
        ctx = auth_result.context
        ip = request.client.host if request.client else None
        app.state.db.log_audit(
            practice_id=ctx.practice_id,
            action="auth.success",
            resource_type="session",
            user_id=ctx.user_id,
            detail=f"method={ctx.auth_method} path={request.url.path}",
            ip_address=ip,
        )
        return await call_next(request)

    # --- Request Logging ---
    @app.middleware("http")
    async def log_requests(request: Request, call_next) -> Response:
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        if config.debug:
            request_id = getattr(request.state, "request_id", "--------")
            logger.info(
                f"[{request_id[:8]}] {request.method} {request.url.path} → {response.status_code} "
                f"({duration:.3f}s)"
            )
        return response

    # --- Request ID ---
    # NOTE: FastAPI middleware is LIFO — this runs BEFORE log_requests.
    @app.middleware("http")
    async def add_request_id(request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # --- Routes ---
    app.include_router(auth_routes.router, prefix="/api/v1")
    app.include_router(audit_routes.router, prefix="/api/v1")
    app.include_router(deid_routes.router, prefix="/api/v1")
    app.include_router(files_routes.router, prefix="/api/v1")
    app.include_router(vault_routes.router, prefix="/api/v1")
    app.include_router(practice_routes.router, prefix="/api/v1")
    app.include_router(patient_routes.router, prefix="/api/v1")
    app.include_router(patient_app_routes.router, prefix="/api/v1")

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

    # --- Error Handlers ---
    from fastapi import HTTPException as _HTTPException
    app.add_exception_handler(_HTTPException, http_exception_handler)

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
