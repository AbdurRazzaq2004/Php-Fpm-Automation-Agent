#!/usr/bin/env bash
# ============================================================
# PHP-FPM Automation Agent - Bootstrap Installer
# ============================================================
# This script prepares the system to run the deployer agent.
# It installs Python3, pip, and the required Python packages.
#
# Usage:
#   chmod +x install.sh
#   sudo ./install.sh
# ============================================================

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}${BOLD}[STEP]${NC} $*"; }

# ── Root Check ──────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     PHP-FPM Automation Agent - Bootstrap Installer      ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Detect OS ──────────────────────────────────────────
log_step "Detecting operating system..."

if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_VERSION="${VERSION_ID:-unknown}"
    OS_NAME="${PRETTY_NAME:-unknown}"
else
    log_error "Cannot detect OS (no /etc/os-release)"
    exit 1
fi

log_info "OS: ${OS_NAME}"

# Determine package manager
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
elif command -v yum &>/dev/null; then
    PKG_MGR="yum"
else
    log_error "Unsupported package manager"
    exit 1
fi

log_info "Package manager: ${PKG_MGR}"

# ── Step 2: Install Python3 & pip ──────────────────────────────
log_step "Checking Python3 installation..."

install_python() {
    case "$PKG_MGR" in
        apt)
            apt-get update -y
            apt-get install -y python3 python3-pip python3-venv
            ;;
        dnf|yum)
            $PKG_MGR install -y python3 python3-pip
            ;;
    esac
}

if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    log_info "Python3 already installed: v${PYTHON_VERSION}"

    # Check minimum version (3.8+)
    MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
    MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
    if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 8 ]); then
        log_warn "Python 3.8+ required (found ${PYTHON_VERSION})"
        log_info "Installing newer Python..."
        install_python
    fi
else
    log_info "Installing Python3..."
    install_python
fi

# Ensure pip is available
if ! python3 -m pip --version &>/dev/null; then
    log_info "Installing pip..."
    case "$PKG_MGR" in
        apt)
            apt-get install -y python3-pip
            ;;
        dnf|yum)
            $PKG_MGR install -y python3-pip
            ;;
    esac
fi

# ── Step 3: Install Python dependencies ────────────────────────
log_step "Installing Python dependencies..."

REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
if [ -f "$REQUIREMENTS_FILE" ]; then
    python3 -m pip install -r "$REQUIREMENTS_FILE" --quiet
    log_info "Python dependencies installed"
else
    log_warn "requirements.txt not found, installing PyYAML directly..."
    python3 -m pip install PyYAML --quiet
fi

# ── Step 4: Install system utilities ───────────────────────────
log_step "Installing system utilities..."

UTILS="git curl wget unzip rsync"
MISSING=""

for util in $UTILS; do
    if ! command -v "$util" &>/dev/null; then
        MISSING="$MISSING $util"
    fi
done

if [ -n "$MISSING" ]; then
    log_info "Installing missing utilities:${MISSING}"
    case "$PKG_MGR" in
        apt)
            apt-get install -y $MISSING
            ;;
        dnf|yum)
            $PKG_MGR install -y $MISSING
            ;;
    esac
else
    log_info "All system utilities present"
fi

# ── Step 5: Create directory structure ─────────────────────────
log_step "Creating directory structure..."

mkdir -p /var/log/php-deployer
mkdir -p /var/log/php-deployer/sessions
mkdir -p /var/log/php-fpm
mkdir -p /var/backups/php-deployer
mkdir -p /etc/php-deployer/envs
mkdir -p /run/php

# Set permissions
chmod 750 /var/log/php-deployer
chmod 750 /var/backups/php-deployer
chmod 700 /etc/php-deployer/envs

log_info "Directories created"

# ── Step 6: Verify installation ────────────────────────────────
log_step "Verifying installation..."

echo ""
echo -e "${BOLD}Installation Summary:${NC}"
echo "  Python3:    $(python3 --version 2>&1)"
echo "  Pip:        $(python3 -m pip --version 2>&1 | head -1)"
echo "  PyYAML:     $(python3 -c 'import yaml; print(yaml.__version__)' 2>/dev/null || echo 'MISSING')"
echo "  Git:        $(git --version 2>&1)"
echo "  Curl:       $(curl --version 2>&1 | head -1)"
echo ""
echo -e "${BOLD}Directory Structure:${NC}"
echo "  Logs:       /var/log/php-deployer/"
echo "  FPM Logs:   /var/log/php-fpm/"
echo "  Backups:    /var/backups/php-deployer/"
echo "  Env Files:  /etc/php-deployer/envs/"
echo ""

# ── Step 7: Create convenience symlink ─────────────────────────
log_step "Creating convenience command..."

DEPLOYER_PATH="${SCRIPT_DIR}/deployer.py"
if [ -f "$DEPLOYER_PATH" ]; then
    chmod +x "$DEPLOYER_PATH"

    # Create wrapper script
    cat > /usr/local/bin/php-deployer << EOF
#!/usr/bin/env bash
# PHP-FPM Automation Agent wrapper
exec python3 "${DEPLOYER_PATH}" "\$@"
EOF
    chmod +x /usr/local/bin/php-deployer
    log_info "Command 'php-deployer' installed globally"
    echo ""
    echo -e "${GREEN}${BOLD}✓ Installation complete!${NC}"
    echo ""
    echo -e "Usage:"
    echo -e "  ${CYAN}sudo php-deployer deploy --config services.yml${NC}"
    echo -e "  ${CYAN}sudo php-deployer validate --config services.yml${NC}"
    echo -e "  ${CYAN}sudo php-deployer status --config services.yml${NC}"
    echo -e "  ${CYAN}sudo php-deployer rollback --service myapp${NC}"
else
    log_warn "deployer.py not found at ${DEPLOYER_PATH}"
    echo ""
    echo -e "${GREEN}${BOLD}✓ Dependencies installed!${NC}"
    echo -e "Run the deployer with: sudo python3 deployer.py deploy --config services.yml"
fi

echo ""
