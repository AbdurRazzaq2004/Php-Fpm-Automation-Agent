#!/usr/bin/env bash
# ============================================================
# Universal Deployment Agent - Installer
# ============================================================
# Installs Docker (the only host prerequisite).
# The agent runs inside a Docker container and handles
# everything else (runtimes, web servers, databases, etc.).
#
# Usage:
#   chmod +x install.sh && sudo ./install.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'
log_info()  { echo -e "${GREEN}[✓]${NC} $*"; }
log_error() { echo -e "${RED}[✗]${NC} $*"; }
log_step()  { echo -e "${CYAN}${BOLD}[>]${NC} $*"; }

if [ "$(id -u)" -ne 0 ]; then
    log_error "Run as root: sudo ./install.sh"
    exit 1
fi

echo ""
echo -e "${BOLD}  Universal Deployment Agent — Installer${NC}"
echo ""

# ── Detect OS ───────────────────────────────────────────────────
. /etc/os-release 2>/dev/null || { log_error "Cannot detect OS"; exit 1; }
OS_ID="${ID:-unknown}"
log_info "OS: ${PRETTY_NAME:-$OS_ID}"

# ── Install Docker if missing ──────────────────────────────────
if command -v docker &>/dev/null; then
    log_info "Docker already installed: $(docker --version)"
else
    log_step "Installing Docker Engine..."

    if command -v apt-get &>/dev/null; then
        # Debian / Ubuntu
        for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
            apt-get remove -y "$pkg" 2>/dev/null || true
        done
        apt-get update -y
        apt-get install -y ca-certificates curl gnupg
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL "https://download.docker.com/linux/${OS_ID}/gpg" | \
            gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null || true
        chmod a+r /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${OS_ID} ${VERSION_CODENAME:-noble} stable" | \
            tee /etc/apt/sources.list.d/docker.list > /dev/null
        apt-get update -y
        apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    elif command -v dnf &>/dev/null; then
        # Fedora / RHEL 8+
        dnf install -y dnf-plugins-core
        dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo 2>/dev/null || true
        dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    elif command -v yum &>/dev/null; then
        # CentOS / RHEL 7
        yum install -y yum-utils
        yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
        yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    else
        log_error "Unsupported package manager — install Docker manually: https://docs.docker.com/engine/install/"
        exit 1
    fi

    if ! command -v docker &>/dev/null; then
        log_error "Docker installation failed — install manually: https://docs.docker.com/engine/install/"
        exit 1
    fi
    log_info "Docker installed: $(docker --version)"
fi

# ── Ensure Docker is running ───────────────────────────────────
systemctl start docker 2>/dev/null || true
systemctl enable docker 2>/dev/null || true

if systemctl is-active --quiet docker 2>/dev/null; then
    log_info "Docker daemon running"
else
    log_error "Docker daemon failed to start"
    exit 1
fi

echo ""
echo -e "${GREEN}${BOLD}✓ Ready! Deploy with:${NC}"
echo -e "  ${CYAN}sudo ./deploy.sh${NC}"
echo ""
