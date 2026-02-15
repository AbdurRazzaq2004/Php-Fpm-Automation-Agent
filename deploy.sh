#!/bin/bash
# =============================================================================
# PHP-FPM Automation Agent — Quick Deploy Script
# =============================================================================
# One-liner convenience script. Run this on your server instead of
# remembering the full docker run command.
#
# Usage:
#   ./deploy.sh                              # deploy using services.yml
#   ./deploy.sh deploy                       # same as above
#   ./deploy.sh deploy --config custom.yml   # use a different config
#   ./deploy.sh validate                     # validate config
#   ./deploy.sh status                       # check service status
#   ./deploy.sh deploy --dry-run             # dry run
#   ./deploy.sh deploy --verbose             # verbose output
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="php-deployer"
CONFIG_FILE="${SCRIPT_DIR}/services.yml"

# ── Color output ────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

# ── Check prerequisites ────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo -e "${CYAN}Docker not found. Installing Docker Engine...${NC}"
    # Detect OS
    . /etc/os-release 2>/dev/null || true
    OS_ID="${ID:-ubuntu}"
    CODENAME="${VERSION_CODENAME:-noble}"

    # Install Docker via official repo
    apt-get update -y 2>/dev/null && apt-get install -y ca-certificates curl gnupg 2>/dev/null
    install -m 0755 -d /etc/apt/keyrings 2>/dev/null
    curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" | \
        gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null || true
    chmod a+r /etc/apt/keyrings/docker.gpg 2>/dev/null
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${OS_ID} ${CODENAME} stable" | \
        tee /etc/apt/sources.list.d/docker.list > /dev/null 2>/dev/null
    apt-get update -y 2>/dev/null
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin 2>/dev/null
    systemctl start docker 2>/dev/null || true
    systemctl enable docker 2>/dev/null || true

    if ! command -v docker &>/dev/null; then
        echo -e "${RED}Error: Docker auto-install failed.${NC}"
        echo "Install Docker manually: https://docs.docker.com/engine/install/"
        exit 1
    fi
    echo -e "${GREEN}Docker installed successfully!${NC}"
fi

# ── Build image if it doesn't exist ────────────────────────────
if ! docker image inspect "${IMAGE_NAME}" &>/dev/null; then
    echo -e "${CYAN}Building ${IMAGE_NAME} image...${NC}"
    docker build -t "${IMAGE_NAME}" "${SCRIPT_DIR}"
    echo ""
fi

# ── Check that services.yml exists ─────────────────────────────
if [ ! -f "${CONFIG_FILE}" ]; then
    echo -e "${RED}Error: ${CONFIG_FILE} not found.${NC}"
    echo "Copy an example and edit it:"
    echo "  cp examples/single-app.yml services.yml"
    exit 1
fi

# ── Default to "deploy" if no command given ────────────────────
COMMAND="${1:-deploy}"

# ── Run the deployer container ─────────────────────────────────
echo -e "${GREEN}Running PHP-FPM Automation Agent (Docker mode)...${NC}"
echo ""

docker run --rm \
    --privileged \
    --pid=host \
    --network=host \
    -v /:/host \
    -v "${CONFIG_FILE}":/app/services.yml \
    "${IMAGE_NAME}" \
    "$@"
