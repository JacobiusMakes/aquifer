#!/usr/bin/env bash
# =============================================================================
# Aquifer Strata Server — Docker Entrypoint
#
# Secret resolution order (for AQUIFER_MASTER_KEY and AQUIFER_JWT_SECRET):
#   1. Environment variable (highest priority)
#   2. Key file on the mounted data volume (/data/strata/.master_key etc.)
#   3. Auto-generate and save to key file (only if AQUIFER_ALLOW_INSECURE_DEFAULTS=1)
#   4. Exit with error (default — prevents silent data loss)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Color helpers (disabled if not a terminal)
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    GREEN='\033[0;32m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    YELLOW=''
    GREEN=''
    BOLD=''
    NC=''
fi

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo -e "${BOLD}================================================${NC}"
echo -e "${BOLD}  Aquifer Strata API Server${NC}"
echo -e "${BOLD}================================================${NC}"
echo ""

# ---------------------------------------------------------------------------
# Ensure data directory exists before we try to read/write key files
# ---------------------------------------------------------------------------
DATA_DIR="${AQUIFER_DATA_DIR:-/data/strata}"
mkdir -p "$DATA_DIR" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Resolve AQUIFER_MASTER_KEY
# ---------------------------------------------------------------------------
MASTER_KEY_FILE="$DATA_DIR/.master_key"
MASTER_KEY_SOURCE=""

if [ -n "${AQUIFER_MASTER_KEY:-}" ]; then
    MASTER_KEY_SOURCE="environment variable"
elif [ -f "$MASTER_KEY_FILE" ]; then
    export AQUIFER_MASTER_KEY=$(cat "$MASTER_KEY_FILE")
    MASTER_KEY_SOURCE="key file ($MASTER_KEY_FILE)"
elif [ "${AQUIFER_ALLOW_INSECURE_DEFAULTS:-0}" = "1" ]; then
    export AQUIFER_MASTER_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    printf '%s' "$AQUIFER_MASTER_KEY" > "$MASTER_KEY_FILE"
    chmod 600 "$MASTER_KEY_FILE"
    MASTER_KEY_SOURCE="generated + saved to $MASTER_KEY_FILE"
    echo -e "${RED}=====================================================${NC}"
    echo -e "${RED}  WARNING: AQUIFER_MASTER_KEY was not set.${NC}"
    echo -e "${RED}  A new key was generated and saved to:${NC}"
    echo -e "${RED}    $MASTER_KEY_FILE${NC}"
    echo -e "${RED}  This key persists as long as the volume is intact.${NC}"
    echo -e "${RED}  DO NOT USE INSECURE DEFAULTS IN PRODUCTION.${NC}"
    echo -e "${RED}=====================================================${NC}"
    echo ""
else
    echo -e "${RED}=====================================================${NC}"
    echo -e "${RED}  ERROR: AQUIFER_MASTER_KEY is not set.${NC}"
    echo -e "${RED}${NC}"
    echo -e "${RED}  Set it via environment variable:${NC}"
    echo -e "${RED}    AQUIFER_MASTER_KEY=\$(python -c \"import secrets; print(secrets.token_urlsafe(32))\")${NC}"
    echo -e "${RED}${NC}"
    echo -e "${RED}  Or place a key file at: $MASTER_KEY_FILE${NC}"
    echo -e "${RED}${NC}"
    echo -e "${RED}  To auto-generate on first run (dev only), set:${NC}"
    echo -e "${RED}    AQUIFER_ALLOW_INSECURE_DEFAULTS=1${NC}"
    echo -e "${RED}=====================================================${NC}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve AQUIFER_JWT_SECRET
# ---------------------------------------------------------------------------
JWT_SECRET_FILE="$DATA_DIR/.jwt_secret"
JWT_SECRET_SOURCE=""

if [ -n "${AQUIFER_JWT_SECRET:-}" ]; then
    JWT_SECRET_SOURCE="environment variable"
elif [ -f "$JWT_SECRET_FILE" ]; then
    export AQUIFER_JWT_SECRET=$(cat "$JWT_SECRET_FILE")
    JWT_SECRET_SOURCE="key file ($JWT_SECRET_FILE)"
elif [ "${AQUIFER_ALLOW_INSECURE_DEFAULTS:-0}" = "1" ]; then
    export AQUIFER_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    printf '%s' "$AQUIFER_JWT_SECRET" > "$JWT_SECRET_FILE"
    chmod 600 "$JWT_SECRET_FILE"
    JWT_SECRET_SOURCE="generated + saved to $JWT_SECRET_FILE"
    echo -e "${YELLOW}WARNING: AQUIFER_JWT_SECRET was not set.${NC}"
    echo -e "${YELLOW}  A new secret was generated and saved to:${NC}"
    echo -e "${YELLOW}    $JWT_SECRET_FILE${NC}"
    echo -e "${YELLOW}  Sessions will persist across container restarts.${NC}"
    echo ""
else
    echo -e "${RED}=====================================================${NC}"
    echo -e "${RED}  ERROR: AQUIFER_JWT_SECRET is not set.${NC}"
    echo -e "${RED}${NC}"
    echo -e "${RED}  Set it via environment variable:${NC}"
    echo -e "${RED}    AQUIFER_JWT_SECRET=\$(python -c \"import secrets; print(secrets.token_urlsafe(32))\")${NC}"
    echo -e "${RED}${NC}"
    echo -e "${RED}  Or place a key file at: $JWT_SECRET_FILE${NC}"
    echo -e "${RED}${NC}"
    echo -e "${RED}  To auto-generate on first run (dev only), set:${NC}"
    echo -e "${RED}    AQUIFER_ALLOW_INSECURE_DEFAULTS=1${NC}"
    echo -e "${RED}=====================================================${NC}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Print configuration summary
# ---------------------------------------------------------------------------
echo -e "${GREEN}Configuration:${NC}"
echo "  Host:         ${AQUIFER_HOST:-0.0.0.0}"
echo "  Port:         ${AQUIFER_PORT:-8443}"
echo "  Data dir:     ${AQUIFER_DATA_DIR:-/data/strata}"
echo "  Debug:        ${AQUIFER_DEBUG:-false}"
echo "  NER:          ${AQUIFER_USE_NER:-true}"
echo "  Master key:   $MASTER_KEY_SOURCE"
echo "  JWT secret:   $JWT_SECRET_SOURCE"
echo ""

# ---------------------------------------------------------------------------
# Ensure data subdirectories exist
# ---------------------------------------------------------------------------
mkdir -p "$DATA_DIR/practices" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Build the command arguments
# ---------------------------------------------------------------------------
CMD_ARGS=(
    --host "${AQUIFER_HOST:-0.0.0.0}"
    --port "${AQUIFER_PORT:-8443}"
)

# Only pass --debug if AQUIFER_DEBUG is explicitly truthy
case "${AQUIFER_DEBUG:-}" in
    1|true|yes|True|TRUE|YES)
        CMD_ARGS+=(--debug)
        ;;
esac

# Pass --data-dir if set
if [ -n "${AQUIFER_DATA_DIR:-}" ]; then
    CMD_ARGS+=(--data-dir "$AQUIFER_DATA_DIR")
fi

# ---------------------------------------------------------------------------
# Start the server
# ---------------------------------------------------------------------------
echo -e "${GREEN}Starting Aquifer Strata server...${NC}"
echo ""

exec python -m aquifer server "${CMD_ARGS[@]}"
