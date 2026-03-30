# =============================================================================
# Aquifer — HIPAA De-Identification Engine
# Multi-stage Docker build for CLI and Strata API server
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Builder — install deps, build wheel, download models
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed at build time (some Python packages need C headers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency metadata first (better layer caching)
COPY pyproject.toml README.md LICENSE ./

# Create a minimal package so pip can resolve the editable install
COPY aquifer/__init__.py aquifer/__init__.py

# Install all runtime deps into a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the package with all extras (ner + ocr)
COPY aquifer/ aquifer/
RUN pip install --no-cache-dir ".[all]" \
    && python -m spacy download en_core_web_sm

# ---------------------------------------------------------------------------
# Stage 2: Runtime — minimal image with only what we need
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL maintainer="Aquifer Health <hello@aquifer.health>"
LABEL description="Aquifer HIPAA De-Identification Engine and Strata API Server"
LABEL org.opencontainers.image.source="https://github.com/aquifer-health/aquifer"
LABEL org.opencontainers.image.licenses="Apache-2.0"

WORKDIR /app

# System dependencies required at runtime
#   tesseract-ocr  — OCR engine for scanned documents
#   libgl1         — OpenCV/Pillow rendering support
#   curl           — health check probe
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY aquifer/ aquifer/
COPY pyproject.toml README.md LICENSE ./

# Re-install in the runtime image so the CLI entrypoint is registered
RUN pip install --no-cache-dir --no-deps -e .

# Copy entrypoint script
COPY scripts/docker-start.sh /app/scripts/docker-start.sh
RUN chmod +x /app/scripts/docker-start.sh

# Create non-root user for security
RUN groupadd --gid 1000 aquifer \
    && useradd --uid 1000 --gid aquifer --shell /bin/bash --create-home aquifer

# Create data directory and set ownership
RUN mkdir -p /data/strata /data/vault /data/output /data/input \
    && chown -R aquifer:aquifer /data /app

# Default environment variables
ENV AQUIFER_DATA_DIR=/data/strata
ENV AQUIFER_HOST=0.0.0.0
ENV AQUIFER_PORT=8443
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to non-root user
USER aquifer

# Expose the Strata API port
EXPOSE 8443

# Health check — polls the /api/v1/health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8443/api/v1/health || exit 1

# Default: run the Strata API server via the entrypoint script
CMD ["/app/scripts/docker-start.sh"]
