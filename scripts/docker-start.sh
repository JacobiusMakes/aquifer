#!/usr/bin/env bash
# =============================================================================
# Aquifer Strata Server — Docker Entrypoint
#
# This script handles first-run setup:
#   1. Auto-generates AQUIFER_MASTER_KEY and AQUIFER_JWT_SECRET if not set
#   2. Prints clear warnings when using auto-generated secrets
#   3. Starts the Strata API server
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
# Auto-generate secrets if not provided
# ---------------------------------------------------------------------------
GENERATED_SECRETS=false

if [ -z "${AQUIFER_MASTER_KEY:-}" ]; then
    export AQUIFER_MASTER_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    GENERATED_SECRETS=true
    echo -e "${YELLOW}WARNING: AQUIFER_MASTER_KEY was not set.${NC}"
    echo -e "${YELLOW}  Auto-generated a random key for this session.${NC}"
    echo -e "${YELLOW}  Data encrypted with this key will be UNRECOVERABLE if the container restarts.${NC}"
    echo ""
fi

if [ -z "${AQUIFER_JWT_SECRET:-}" ]; then
    export AQUIFER_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
    GENERATED_SECRETS=true
    echo -e "${YELLOW}WARNING: AQUIFER_JWT_SECRET was not set.${NC}"
    echo -e "${YELLOW}  Auto-generated a random secret for this session.${NC}"
    echo -e "${YELLOW}  All user sessions will be invalidated on container restart.${NC}"
    echo ""
fi

if [ "$GENERATED_SECRETS" = true ]; then
    echo -e "${RED}=====================================================${NC}"
    echo -e "${RED}  DO NOT USE AUTO-GENERATED SECRETS IN PRODUCTION!${NC}"
    echo -e "${RED}=====================================================${NC}"
    echo ""
    echo "  Generate persistent secrets with:"
    echo "    export AQUIFER_MASTER_KEY=\$(python -c \"import secrets; print(secrets.token_urlsafe(32))\")"
    echo "    export AQUIFER_JWT_SECRET=\$(python -c \"import secrets; print(secrets.token_urlsafe(32))\")"
    echo ""
    echo "  Then pass them via docker-compose.yml or 'docker run -e'."
    echo ""
fi

# ---------------------------------------------------------------------------
# Print configuration summary
# ---------------------------------------------------------------------------
echo -e "${GREEN}Configuration:${NC}"
echo "  Host:      ${AQUIFER_HOST:-0.0.0.0}"
echo "  Port:      ${AQUIFER_PORT:-8443}"
echo "  Data dir:  ${AQUIFER_DATA_DIR:-/data/strata}"
echo "  Debug:     ${AQUIFER_DEBUG:-false}"
echo "  NER:       ${AQUIFER_USE_NER:-true}"
echo ""

# ---------------------------------------------------------------------------
# Ensure data directories exist
# ---------------------------------------------------------------------------
DATA_DIR="${AQUIFER_DATA_DIR:-/data/strata}"
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
