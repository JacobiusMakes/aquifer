#!/usr/bin/env bash
# =============================================================================
# Aquifer — One-command production deployment
#
# Usage:
#   ./scripts/deploy.sh          # Deploy with PostgreSQL + nginx + TLS
#   ./scripts/deploy.sh --dev    # Deploy with SQLite only (no nginx/TLS)
#
# Prerequisites:
#   - Docker and Docker Compose installed
#   - .env file configured (copy from .env.example)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[aquifer]${NC} $1"; }
warn() { echo -e "${YELLOW}[aquifer]${NC} $1"; }
error() { echo -e "${RED}[aquifer]${NC} $1" >&2; }

# --- Parse args ---
DEV_MODE=false
if [[ "${1:-}" == "--dev" ]]; then
    DEV_MODE=true
fi

# --- Check prerequisites ---
if ! command -v docker &>/dev/null; then
    error "Docker is not installed. Install from https://docker.com"
    exit 1
fi

if ! docker compose version &>/dev/null; then
    error "Docker Compose v2 is required. Update Docker Desktop."
    exit 1
fi

# --- Check .env ---
if [[ ! -f .env ]]; then
    warn ".env file not found. Creating from template..."
    cp .env.example .env

    # Auto-generate secrets
    MASTER_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)
    JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || openssl rand -base64 32)

    if [[ "$DEV_MODE" == "false" ]]; then
        PG_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))" 2>/dev/null || openssl rand -base64 24)
        echo "" >> .env
        echo "# PostgreSQL (auto-generated)" >> .env
        echo "POSTGRES_USER=aquifer" >> .env
        echo "POSTGRES_PASSWORD=$PG_PASSWORD" >> .env
    fi

    # Write secrets
    sed -i.bak "s/^AQUIFER_MASTER_KEY=$/AQUIFER_MASTER_KEY=$MASTER_KEY/" .env
    sed -i.bak "s/^AQUIFER_JWT_SECRET=$/AQUIFER_JWT_SECRET=$JWT_SECRET/" .env
    rm -f .env.bak

    log "Generated secrets in .env"
    warn "IMPORTANT: Back up your .env file. These keys encrypt patient data."
fi

# --- Generate self-signed TLS cert if needed ---
if [[ "$DEV_MODE" == "false" ]]; then
    CERT_DIR="$PROJECT_DIR/nginx/certs"
    if [[ ! -f "$CERT_DIR/fullchain.pem" ]]; then
        log "Generating self-signed TLS certificate..."
        mkdir -p "$CERT_DIR"
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "$CERT_DIR/privkey.pem" \
            -out "$CERT_DIR/fullchain.pem" \
            -subj '/CN=localhost/O=Aquifer/C=US' 2>/dev/null
        log "Self-signed cert created. Replace with real certs for production."
    fi
fi

# --- Deploy ---
if [[ "$DEV_MODE" == "true" ]]; then
    log "Deploying in development mode (SQLite, no nginx)..."
    docker compose up -d --build
    COMPOSE_FILE="docker-compose.yml"
else
    log "Deploying production stack (PostgreSQL + nginx + TLS)..."
    docker compose -f docker-compose.prod.yml up -d --build
    COMPOSE_FILE="docker-compose.prod.yml"
fi

# --- Wait for healthy ---
log "Waiting for services to be healthy..."
RETRIES=30
until docker compose -f "$COMPOSE_FILE" ps --format json 2>/dev/null | grep -q '"healthy"' || [[ $RETRIES -eq 0 ]]; do
    sleep 2
    RETRIES=$((RETRIES - 1))
done

if [[ $RETRIES -eq 0 ]]; then
    warn "Services may still be starting. Check with: docker compose -f $COMPOSE_FILE ps"
else
    log "All services healthy!"
fi

# --- Print info ---
echo ""
echo -e "${BLUE}=============================================${NC}"
echo -e "${BLUE}  Aquifer is running!${NC}"
echo -e "${BLUE}=============================================${NC}"
echo ""

if [[ "$DEV_MODE" == "true" ]]; then
    echo "  API:        http://localhost:8443"
    echo "  Dashboard:  http://localhost:8443/dashboard"
    echo "  Patient App: http://localhost:8443/app"
    echo "  API Docs:   http://localhost:8443/docs"
else
    echo "  API:        https://localhost"
    echo "  Dashboard:  https://localhost/dashboard"
    echo "  Patient App: https://localhost/app"
    echo "  FHIR:       https://localhost/api/v1/fhir/metadata"
    echo "  API Docs:   https://localhost/docs"
fi

echo ""
echo "  Logs:       docker compose -f $COMPOSE_FILE logs -f"
echo "  Stop:       docker compose -f $COMPOSE_FILE down"
echo ""

if [[ "$DEV_MODE" == "false" ]]; then
    warn "Using self-signed TLS cert. For production:"
    echo "  1. Get a real cert (Let's Encrypt, Cloudflare, etc.)"
    echo "  2. Place at nginx/certs/fullchain.pem and privkey.pem"
    echo "  3. Restart: docker compose -f docker-compose.prod.yml restart nginx"
    echo ""
fi
