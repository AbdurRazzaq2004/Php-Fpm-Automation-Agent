#!/bin/bash
# =============================================================================
# PHP-FPM Automation Agent — Docker Entrypoint
# =============================================================================
# This script bridges the container to the host server:
#   1. Copies the tool into the host filesystem (via /host mount)
#   2. Uses nsenter to run the deployer in the host's namespace
#   3. Cleans up after itself
#
# All commands (apt install, systemctl, useradd, git clone, etc.) execute
# directly on the HOST — not inside the container.
# =============================================================================

set -euo pipefail

# ── Color output ────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Preflight checks ───────────────────────────────────────────

# Check that /host is mounted (the host's root filesystem)
if [ ! -d "/host/etc" ] || [ ! -d "/host/usr" ]; then
    echo -e "${RED}ERROR: Host filesystem not mounted at /host${NC}"
    echo ""
    echo "You must mount the host root filesystem into the container:"
    echo ""
    echo "  docker run --rm --privileged --pid=host --network=host \\"
    echo "    -v /:/host \\"
    echo "    -v ./services.yml:/app/services.yml \\"
    echo "    php-deployer deploy"
    echo ""
    exit 1
fi

# Check that we can see host PID 1 (need --pid=host)
if [ ! -d "/proc/1/ns" ]; then
    echo -e "${RED}ERROR: Cannot access host PID namespace${NC}"
    echo ""
    echo "You must run with --pid=host flag:"
    echo "  docker run --privileged --pid=host ..."
    echo ""
    exit 1
fi

# Check for --privileged (needed for nsenter)
if ! nsenter --target 1 --mount --uts --ipc --net --pid -- echo "ok" >/dev/null 2>&1; then
    echo -e "${RED}ERROR: Cannot nsenter into host namespace${NC}"
    echo ""
    echo "You must run with --privileged flag:"
    echo "  docker run --privileged --pid=host ..."
    echo ""
    exit 1
fi

# ── Handle --help ───────────────────────────────────────────────
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ] || [ $# -eq 0 ]; then
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║      PHP-FPM Automation Agent — Docker Mode                 ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${GREEN}Usage:${NC}"
    echo "  docker run --rm --privileged --pid=host --network=host \\"
    echo "    -v /:/host \\"
    echo "    -v ./services.yml:/app/services.yml \\"
    echo "    php-deployer <command> [options]"
    echo ""
    echo -e "${GREEN}Commands:${NC}"
    echo "  deploy              Deploy services (uses services.yml by default)"
    echo "  deploy --config X   Deploy using a custom config file"
    echo "  validate            Validate configuration without deploying"
    echo "  status              Show status of deployed services"
    echo "  rollback            Rollback a service to a previous state"
    echo ""
    echo -e "${GREEN}Flags:${NC}"
    echo "  --config, -c FILE   Path to YAML config (default: services.yml)"
    echo "  --verbose, -v       Enable verbose output"
    echo "  --dry-run           Validate only, don't make changes"
    echo ""
    echo -e "${GREEN}Examples:${NC}"
    echo "  # Deploy single app"
    echo "  docker run --rm --privileged --pid=host --network=host \\"
    echo "    -v /:/host -v ./services.yml:/app/services.yml \\"
    echo "    php-deployer deploy"
    echo ""
    echo "  # Deploy with custom config"
    echo "  docker run --rm --privileged --pid=host --network=host \\"
    echo "    -v /:/host -v ./production.yml:/app/services.yml \\"
    echo "    php-deployer deploy"
    echo ""
    echo "  # Dry run (validate without deploying)"
    echo "  docker run --rm --privileged --pid=host --network=host \\"
    echo "    -v /:/host -v ./services.yml:/app/services.yml \\"
    echo "    php-deployer deploy --dry-run"
    echo ""
    echo -e "${GREEN}Using docker compose:${NC}"
    echo "  docker compose run --rm deployer deploy"
    echo "  docker compose run --rm deployer validate"
    echo ""
    exit 0
fi

# ── Stage tool onto host filesystem ────────────────────────────
STAGING_DIR="/host/tmp/php-deployer-$$"

echo -e "${CYAN}[Docker] Staging deployment tool onto host...${NC}"

# Copy the tool to the host's /tmp (accessible via the /host mount)
mkdir -p "${STAGING_DIR}"
cp -r /app/* "${STAGING_DIR}/"
cp -r /app/config "${STAGING_DIR}/"
cp -r /app/modules "${STAGING_DIR}/"

# If user mounted a custom services.yml, copy it into staging
if [ -f /app/services.yml ]; then
    cp /app/services.yml "${STAGING_DIR}/services.yml"
fi

# Ensure Python dependencies exist on host
# PyYAML is needed — install it into the staging dir if host Python lacks it
HOST_PYTHON=""
for candidate in python3 python3.12 python3.11 python3.10 python3.9 python3.8; do
    if nsenter --target 1 --mount --uts --ipc --net --pid -- which "$candidate" >/dev/null 2>&1; then
        HOST_PYTHON="$candidate"
        break
    fi
done

if [ -z "$HOST_PYTHON" ]; then
    echo -e "${YELLOW}[Docker] Python3 not found on host — installing via package manager...${NC}"
    # Detect host package manager
    if nsenter --target 1 --mount --uts --ipc --net --pid -- which apt-get >/dev/null 2>&1; then
        nsenter --target 1 --mount --uts --ipc --net --pid -- apt-get update -qq
        nsenter --target 1 --mount --uts --ipc --net --pid -- apt-get install -y -qq python3 python3-pip python3-yaml
    elif nsenter --target 1 --mount --uts --ipc --net --pid -- which dnf >/dev/null 2>&1; then
        nsenter --target 1 --mount --uts --ipc --net --pid -- dnf install -y -q python3 python3-pip python3-pyyaml
    elif nsenter --target 1 --mount --uts --ipc --net --pid -- which yum >/dev/null 2>&1; then
        nsenter --target 1 --mount --uts --ipc --net --pid -- yum install -y -q python3 python3-pip python3-pyyaml
    else
        echo -e "${RED}ERROR: No supported package manager found on host${NC}"
        rm -rf "${STAGING_DIR}"
        exit 1
    fi
    HOST_PYTHON="python3"
fi

# Ensure PyYAML is available on host
if ! nsenter --target 1 --mount --uts --ipc --net --pid -- "$HOST_PYTHON" -c "import yaml" 2>/dev/null; then
    echo -e "${YELLOW}[Docker] Installing PyYAML on host...${NC}"
    nsenter --target 1 --mount --uts --ipc --net --pid -- "$HOST_PYTHON" -m pip install --quiet PyYAML 2>/dev/null || \
    nsenter --target 1 --mount --uts --ipc --net --pid -- pip3 install --quiet PyYAML 2>/dev/null || \
    echo -e "${YELLOW}[Docker] Warning: Could not install PyYAML via pip, trying system package...${NC}" && \
    nsenter --target 1 --mount --uts --ipc --net --pid -- apt-get install -y -qq python3-yaml 2>/dev/null || true
fi

# ── Execute deployer on the host ───────────────────────────────
# The /tmp path inside staging corresponds to host's /tmp (since /host is host root)
HOST_TOOL_DIR="/tmp/php-deployer-$$"

echo -e "${CYAN}[Docker] Running deployer on host via nsenter...${NC}"
echo -e "${CYAN}[Docker] Command: $HOST_PYTHON ${HOST_TOOL_DIR}/deployer.py $*${NC}"
echo ""

# Run the deployer in the host's full namespace
EXIT_CODE=0
nsenter --target 1 --mount --uts --ipc --net --pid -- \
    "$HOST_PYTHON" "${HOST_TOOL_DIR}/deployer.py" "$@" || EXIT_CODE=$?

# ── Cleanup ─────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[Docker] Cleaning up staging files...${NC}"
rm -rf "${STAGING_DIR}"

if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}[Docker] Deployment completed successfully.${NC}"
else
    echo -e "${RED}[Docker] Deployment failed (exit code: ${EXIT_CODE}).${NC}"
fi

exit $EXIT_CODE
