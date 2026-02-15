#!/bin/bash
# =============================================================================
# Universal Deployment Agent — Destroy / Cleanup Script
# =============================================================================
# Removes everything the agent created for deployed services.
#
# Usage:
#   sudo ./destroy.sh                  # interactive — asks before removing
#   sudo ./destroy.sh --yes            # skip confirmations (non-interactive)
#   sudo ./destroy.sh --dry-run        # show what would be removed, touch nothing
#   sudo ./destroy.sh --keep-db        # remove everything EXCEPT databases
#   sudo ./destroy.sh --keep-packages  # remove everything EXCEPT installed packages
#
# What gets removed:
#   ✗ Application code (deploy_path)
#   ✗ Web server vhosts (Apache / Nginx)
#   ✗ PHP-FPM pool configs & sockets
#   ✗ Systemd services created by the agent
#   ✗ PM2 processes & ecosystem files
#   ✗ Database & database user (unless --keep-db)
#   ✗ System user created for the service
#   ✗ Cron jobs added by the agent
#   ✗ SSL certificates (Let's Encrypt)
#   ✗ Log files & backups
#   ✗ Shared directories
#   ✗ Git safe.directory entries
#   ✗ Agent Docker image
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/services.yml"

# ── Color output ────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ── Flags ───────────────────────────────────────────────────────
AUTO_YES=false
DRY_RUN=false
KEEP_DB=false
KEEP_PACKAGES=false

for arg in "$@"; do
    case "$arg" in
        --yes|-y)       AUTO_YES=true ;;
        --dry-run)      DRY_RUN=true ;;
        --keep-db)      KEEP_DB=true ;;
        --keep-packages) KEEP_PACKAGES=true ;;
        --help|-h)
            head -28 "$0" | tail -25
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            echo "Usage: sudo ./destroy.sh [--yes] [--dry-run] [--keep-db] [--keep-packages]"
            exit 1
            ;;
    esac
done

# ── Root check ──────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root (sudo ./destroy.sh)${NC}"
    exit 1
fi

# ── services.yml check ─────────────────────────────────────────
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: $CONFIG_FILE not found.${NC}"
    echo "The destroy script reads services.yml to know what to clean up."
    exit 1
fi

# ── Parse services.yml (lightweight — no Python required) ──────
# Extracts service configs from the flat or multi-service YAML.
# Handles both single-service (flat keys) and multi-service (services: list).

parse_services() {
    local file="$1"

    # Check if this is a multi-service file (has "services:" key)
    if grep -qE '^\s*services\s*:' "$file"; then
        # Multi-service: extract each service block
        # We look for "- service_name:" entries within the services list
        awk '
        /^\s*services\s*:/ { in_list=1; next }
        in_list && /^\s*-\s*service_name\s*:/ {
            count++
            gsub(/^\s*-\s*service_name\s*:\s*/, "")
            gsub(/\s*$/, "")
            names[count] = $0
        }
        in_list && /^\s*domain\s*:/ {
            gsub(/^\s*domain\s*:\s*/, "")
            gsub(/\s*$/, "")
            domains[count] = $0
        }
        in_list && /^\s*deploy_path\s*:/ {
            gsub(/^\s*deploy_path\s*:\s*/, "")
            gsub(/\s*$/, "")
            paths[count] = $0
        }
        in_list && /^\s*language\s*:/ {
            gsub(/^\s*language\s*:\s*/, "")
            gsub(/\s*$/, "")
            langs[count] = $0
        }
        in_list && /^\s*web_server\s*:/ {
            gsub(/^\s*web_server\s*:\s*/, "")
            gsub(/\s*$/, "")
            servers[count] = $0
        }
        in_list && /^\s*user\s*:/ {
            gsub(/^\s*user\s*:\s*/, "")
            gsub(/\s*$/, "")
            users[count] = $0
        }
        END {
            for (i=1; i<=count; i++) {
                lang = (langs[i] ? langs[i] : "auto")
                ws = (servers[i] ? servers[i] : "apache")
                usr = (users[i] ? users[i] : "svc_" names[i])
                dp = (paths[i] ? paths[i] : "/var/www/" names[i])
                dom = (domains[i] ? domains[i] : "localhost")
                print names[i] "|" dom "|" dp "|" lang "|" ws "|" usr
            }
        }
        ' "$file"
    else
        # Single-service (flat keys)
        local name domain deploy_path language web_server user
        name=$(grep -E '^\s*service_name\s*:' "$file" | head -1 | sed 's/.*:\s*//' | xargs)
        domain=$(grep -E '^\s*domain\s*:' "$file" | head -1 | sed 's/.*:\s*//' | xargs)
        deploy_path=$(grep -E '^\s*deploy_path\s*:' "$file" | head -1 | sed 's/.*:\s*//' | xargs)
        language=$(grep -E '^\s*language\s*:' "$file" | head -1 | sed 's/.*:\s*//' | xargs)
        web_server=$(grep -E '^\s*web_server\s*:' "$file" | head -1 | sed 's/.*:\s*//' | xargs)
        user=$(grep -E '^\s*user\s*:' "$file" | head -1 | sed 's/.*:\s*//' | xargs)

        [ -z "$name" ] && { echo -e "${RED}Cannot parse service_name from $file${NC}"; exit 1; }
        deploy_path="${deploy_path:-/var/www/$name}"
        language="${language:-auto}"
        web_server="${web_server:-apache}"
        user="${user:-svc_$name}"
        domain="${domain:-localhost}"

        echo "${name}|${domain}|${deploy_path}|${language}|${web_server}|${user}"
    fi
}

# ── Helper: run or print ───────────────────────────────────────
run() {
    if $DRY_RUN; then
        echo -e "  ${CYAN}[dry-run]${NC} $*"
    else
        eval "$@" 2>/dev/null || true
    fi
}

confirm() {
    if $AUTO_YES || $DRY_RUN; then
        return 0
    fi
    echo ""
    echo -e "${YELLOW}$1${NC}"
    read -rp "Continue? [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]]
}

section() {
    echo ""
    echo -e "${BOLD}── $1 ──${NC}"
}

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
skip() { echo -e "  ${YELLOW}⊘${NC} $1 (not found)"; }
kept() { echo -e "  ${CYAN}⊘${NC} $1 (kept — flag)"; }

# =============================================================================
#   MAIN
# =============================================================================

echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  UNIVERSAL DEPLOYMENT AGENT — DESTROY${NC}"
echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"
echo ""

SERVICES=$(parse_services "$CONFIG_FILE")
SERVICE_COUNT=$(echo "$SERVICES" | wc -l)

echo -e "Config:   ${CONFIG_FILE}"
echo -e "Services: ${SERVICE_COUNT}"
$DRY_RUN && echo -e "Mode:     ${CYAN}DRY RUN (no changes will be made)${NC}"
$KEEP_DB && echo -e "Flag:     ${CYAN}--keep-db (databases will be preserved)${NC}"
$KEEP_PACKAGES && echo -e "Flag:     ${CYAN}--keep-packages (installed packages will be preserved)${NC}"
echo ""

echo "Services to destroy:"
echo "$SERVICES" | while IFS='|' read -r name domain deploy_path language web_server user; do
    echo -e "  ${RED}✗${NC} ${name} (${language}) → ${deploy_path}"
done

if ! confirm "This will permanently remove all deployed services and their data."; then
    echo -e "${YELLOW}Aborted.${NC}"
    exit 0
fi

# ── Per-service cleanup ─────────────────────────────────────────
echo "$SERVICES" | while IFS='|' read -r name domain deploy_path language web_server user; do

    echo ""
    echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}  DESTROYING: ${name}${NC}"
    echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"

    systemd_service="app-${name}"

    # ── 1. Stop & remove PM2 processes ──────────────────────────
    section "PM2 Processes"
    if command -v pm2 &>/dev/null; then
        if pm2 describe "$systemd_service" &>/dev/null 2>&1; then
            run "pm2 delete '$systemd_service'"
            run "pm2 save --force"
            ok "PM2 process '$systemd_service' deleted"
        else
            skip "PM2 process '$systemd_service'"
        fi
        # Also try as the service user
        if id "$user" &>/dev/null 2>&1; then
            run "su - '$user' -s /bin/bash -c 'pm2 delete $systemd_service 2>/dev/null; pm2 save --force 2>/dev/null' || true"
        fi
    else
        skip "PM2 not installed"
    fi
    # Remove ecosystem file
    if [ -f "${deploy_path}/ecosystem.config.js" ]; then
        run "rm -f '${deploy_path}/ecosystem.config.js'"
        ok "Removed ecosystem.config.js"
    fi

    # ── 2. Stop & remove systemd service ────────────────────────
    section "Systemd Service"
    if [ -f "/etc/systemd/system/${systemd_service}.service" ]; then
        run "systemctl stop '${systemd_service}' || true"
        run "systemctl disable '${systemd_service}' || true"
        run "rm -f '/etc/systemd/system/${systemd_service}.service'"
        run "systemctl daemon-reload"
        ok "Removed systemd service: ${systemd_service}"
    else
        skip "Systemd service: ${systemd_service}.service"
    fi

    # ── 3. Remove web server vhosts ─────────────────────────────
    # Always check BOTH web servers — the config may have changed
    # since the original deployment (e.g. apache → nginx switch).
    section "Web Server Vhost"
    vhost_found=false

    # Apache (Debian/Ubuntu)
    if [ -f "/etc/apache2/sites-available/${name}.conf" ]; then
        run "a2dissite '${name}' || true"
        run "rm -f '/etc/apache2/sites-available/${name}.conf'"
        run "rm -f '/etc/apache2/sites-enabled/${name}.conf'"
        ok "Removed Apache vhost: ${name}.conf"
        vhost_found=true
    fi
    # Apache (RHEL/CentOS)
    if [ -f "/etc/httpd/conf.d/${name}.conf" ]; then
        run "rm -f '/etc/httpd/conf.d/${name}.conf'"
        ok "Removed httpd vhost: ${name}.conf"
        vhost_found=true
    fi
    # Nginx (Debian/Ubuntu)
    if [ -f "/etc/nginx/sites-available/${name}.conf" ]; then
        run "rm -f '/etc/nginx/sites-enabled/${name}.conf'"
        run "rm -f '/etc/nginx/sites-available/${name}.conf'"
        ok "Removed Nginx vhost: ${name}.conf"
        vhost_found=true
    fi
    # Nginx (RHEL / conf.d)
    if [ -f "/etc/nginx/conf.d/${name}.conf" ]; then
        run "rm -f '/etc/nginx/conf.d/${name}.conf'"
        ok "Removed Nginx conf.d: ${name}.conf"
        vhost_found=true
    fi
    if ! $vhost_found; then
        skip "No vhosts found for ${name}"
    fi

    # ── 4. Remove PHP-FPM pool config ──────────────────────────
    section "PHP-FPM Pool"
    fpm_removed=false
    for php_ver in /etc/php/*/fpm/pool.d/ ; do
        if [ -f "${php_ver}${name}.conf" ]; then
            run "rm -f '${php_ver}${name}.conf'"
            ok "Removed FPM pool: ${php_ver}${name}.conf"
            fpm_removed=true
        fi
    done
    if ! $fpm_removed; then
        skip "PHP-FPM pool for ${name}"
    fi
    # Remove FPM socket
    for sock in /run/php/php*-fpm-${name}.sock; do
        if [ -e "$sock" ]; then
            run "rm -f '$sock'"
            ok "Removed FPM socket: $sock"
        fi
    done

    # ── 5. Remove SSL certificates ──────────────────────────────
    section "SSL Certificates"
    if [ -d "/etc/letsencrypt/live/${domain}" ]; then
        run "certbot delete --cert-name '${domain}' --non-interactive || true"
        ok "Removed SSL cert for ${domain}"
    else
        skip "SSL cert for ${domain}"
    fi

    # ── 6. Remove cron jobs ─────────────────────────────────────
    section "Cron Jobs"
    if crontab -u "$user" -l 2>/dev/null | grep -q "php-deployer:${name}"; then
        if $DRY_RUN; then
            echo -e "  ${CYAN}[dry-run]${NC} Would remove cron entries for ${name} from ${user}'s crontab"
        else
            crontab -u "$user" -l 2>/dev/null | \
                sed "/# BEGIN php-deployer:${name}/,/# END php-deployer:${name}/d" | \
                crontab -u "$user" -
            ok "Removed cron jobs for ${name}"
        fi
    else
        skip "Cron jobs for ${name}"
    fi

    # ── 7. Remove database ──────────────────────────────────────
    section "Database"
    if $KEEP_DB; then
        kept "Database (--keep-db flag)"
    else
        # Try to find DB name from .env in deploy_path
        db_name=""
        db_user=""
        if [ -f "${deploy_path}/.env" ]; then
            db_name=$(grep -E '^DB_DATABASE=|^DB_NAME=|^DATABASE_NAME=|^POSTGRES_DB=' "${deploy_path}/.env" 2>/dev/null | head -1 | cut -d'=' -f2 | xargs)
            db_user=$(grep -E '^DB_USERNAME=|^DB_USER=|^DATABASE_USER=|^POSTGRES_USER=' "${deploy_path}/.env" 2>/dev/null | head -1 | cut -d'=' -f2 | xargs)
        fi

        # MySQL
        if command -v mysql &>/dev/null && [ -n "$db_name" ]; then
            mysql_admin_cmd="mysql -u root"
            # Try sudo mysql first (works on Ubuntu with auth_socket)
            if sudo mysql -e "SELECT 1" &>/dev/null 2>&1; then
                mysql_admin_cmd="sudo mysql"
            fi
            run "$mysql_admin_cmd -e \"DROP DATABASE IF EXISTS \\\`${db_name}\\\`;\" || true"
            ok "Dropped MySQL database: ${db_name}"
            if [ -n "$db_user" ] && [ "$db_user" != "root" ]; then
                run "$mysql_admin_cmd -e \"DROP USER IF EXISTS '${db_user}'@'localhost';\" || true"
                ok "Dropped MySQL user: ${db_user}"
            fi
        fi

        # PostgreSQL
        if command -v psql &>/dev/null && [ -n "$db_name" ]; then
            run "sudo -u postgres psql -c \"DROP DATABASE IF EXISTS ${db_name};\" || true"
            ok "Dropped PostgreSQL database: ${db_name}"
            if [ -n "$db_user" ] && [ "$db_user" != "postgres" ]; then
                run "sudo -u postgres psql -c \"DROP ROLE IF EXISTS ${db_user};\" || true"
                ok "Dropped PostgreSQL role: ${db_user}"
            fi
        fi

        if [ -z "$db_name" ]; then
            skip "Database (no DB_DATABASE found in .env)"
        fi
    fi

    # ── 8. Remove application code ──────────────────────────────
    section "Application Code"
    if [ -d "$deploy_path" ]; then
        run "rm -rf '$deploy_path'"
        ok "Removed deploy path: ${deploy_path}"
    else
        skip "Deploy path: ${deploy_path}"
    fi
    # Shared directories
    shared_dir="$(dirname "$deploy_path")/shared/${name}"
    if [ -d "$shared_dir" ]; then
        run "rm -rf '$shared_dir'"
        ok "Removed shared dir: ${shared_dir}"
    fi

    # ── 9. Remove log files ─────────────────────────────────────
    section "Log Files"
    for logfile in \
        "/var/log/${name}.log" \
        "/var/log/${name}.error.log" \
        "/var/log/${name}-out.log" \
        "/var/log/${name}-error.log" \
        "/var/log/php-fpm/${name}-access.log" \
        "/var/log/php-fpm/${name}-slow.log" \
        "/var/log/php-fpm/${name}-error.log" \
        "/var/log/nginx/${name}-access.log" \
        "/var/log/nginx/${name}-error.log" \
        "/var/log/nginx/${name}-ssl-access.log" \
        "/var/log/nginx/${name}-ssl-error.log" \
    ; do
        if [ -f "$logfile" ]; then
            run "rm -f '$logfile'"
            ok "Removed: $logfile"
        fi
    done
    # Apache logs use ${APACHE_LOG_DIR} which is usually /var/log/apache2
    for apache_logdir in /var/log/apache2 /var/log/httpd; do
        for logfile in \
            "${apache_logdir}/${name}-access.log" \
            "${apache_logdir}/${name}-error.log" \
            "${apache_logdir}/${name}-ssl-access.log" \
            "${apache_logdir}/${name}-ssl-error.log" \
        ; do
            if [ -f "$logfile" ]; then
                run "rm -f '$logfile'"
                ok "Removed: $logfile"
            fi
        done
    done

    # ── 10. Remove system user ──────────────────────────────────
    section "System User"
    if id "$user" &>/dev/null 2>&1; then
        # Kill any remaining processes
        run "pkill -u '$user' || true"
        sleep 1
        run "userdel '$user' || true"
        ok "Removed system user: ${user}"
    else
        skip "System user: ${user}"
    fi

    # ── 11. Remove git safe.directory entry ─────────────────────
    section "Git Config"
    if git config --global --get-all safe.directory 2>/dev/null | grep -qF "$deploy_path"; then
        run "git config --global --unset-all safe.directory '$deploy_path' || true"
        ok "Removed git safe.directory: ${deploy_path}"
    else
        skip "Git safe.directory for ${deploy_path}"
    fi

    echo ""
    echo -e "  ${GREEN}✓ ${name} destroyed${NC}"

done

# ── Global cleanup ──────────────────────────────────────────────

echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  GLOBAL CLEANUP${NC}"
echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"

# ── Backups ─────────────────────────────────────────────────────
section "Backups"
if [ -d "/var/backups/php-deployer" ]; then
    run "rm -rf /var/backups/php-deployer"
    ok "Removed /var/backups/php-deployer"
else
    skip "/var/backups/php-deployer"
fi

# ── Deployer session logs ───────────────────────────────────────
section "Deployer Logs"
if [ -d "/var/log/php-deployer" ]; then
    run "rm -rf /var/log/php-deployer"
    ok "Removed /var/log/php-deployer"
else
    skip "/var/log/php-deployer"
fi

# ── PHP-FPM log directory (if empty) ───────────────────────────
if [ -d "/var/log/php-fpm" ]; then
    if [ -z "$(ls -A /var/log/php-fpm 2>/dev/null)" ]; then
        run "rmdir /var/log/php-fpm"
        ok "Removed empty /var/log/php-fpm"
    fi
fi

# ── Reload web servers ─────────────────────────────────────────
section "Reload Web Servers"
if systemctl is-active apache2 &>/dev/null; then
    run "systemctl reload apache2 || true"
    ok "Reloaded Apache"
elif systemctl is-active httpd &>/dev/null; then
    run "systemctl reload httpd || true"
    ok "Reloaded httpd"
fi
if systemctl is-active nginx &>/dev/null; then
    run "systemctl reload nginx || true"
    ok "Reloaded Nginx"
fi

# ── Reload PHP-FPM ─────────────────────────────────────────────
for fpm_service in $(systemctl list-units --type=service --no-legend 2>/dev/null | grep 'php.*fpm' | awk '{print $1}'); do
    run "systemctl reload '$fpm_service' || true"
    ok "Reloaded ${fpm_service}"
done

# ── Remove Docker image ────────────────────────────────────────
section "Docker Image"
if command -v docker &>/dev/null && docker image inspect php-deployer &>/dev/null 2>&1; then
    run "docker rmi php-deployer || true"
    ok "Removed Docker image: php-deployer"
else
    skip "Docker image: php-deployer"
fi

# ── Optional: remove installed packages ─────────────────────────
if ! $KEEP_PACKAGES; then
    section "Installed Packages (optional)"
    if $AUTO_YES; then
        REMOVE_PKGS=true
    elif $DRY_RUN; then
        REMOVE_PKGS=true
    else
        echo ""
        echo -e "${YELLOW}The agent may have installed system packages (apache2, nginx, php, mysql, postgresql, nodejs, etc.)${NC}"
        echo -e "${YELLOW}Removing them could affect other applications on this server.${NC}"
        read -rp "Remove agent-installed packages? [y/N] " pkg_answer
        REMOVE_PKGS=false
        [[ "$pkg_answer" =~ ^[Yy]$ ]] && REMOVE_PKGS=true
    fi

    if $REMOVE_PKGS; then
        # Detect which packages were installed by checking what's present
        PKGS_TO_REMOVE=""

        # Web servers
        dpkg -l apache2 2>/dev/null | grep -q '^ii' && PKGS_TO_REMOVE="$PKGS_TO_REMOVE apache2 apache2-utils libapache2-mod-fcgid"
        dpkg -l nginx 2>/dev/null | grep -q '^ii' && PKGS_TO_REMOVE="$PKGS_TO_REMOVE nginx nginx-common"

        # PHP
        for pkg in $(dpkg -l 2>/dev/null | grep '^ii' | awk '{print $2}' | grep -E '^php[0-9]'); do
            PKGS_TO_REMOVE="$PKGS_TO_REMOVE $pkg"
        done

        # Databases
        dpkg -l mysql-server 2>/dev/null | grep -q '^ii' && PKGS_TO_REMOVE="$PKGS_TO_REMOVE mysql-server mysql-client mysql-common"
        dpkg -l postgresql 2>/dev/null | grep -q '^ii' && PKGS_TO_REMOVE="$PKGS_TO_REMOVE postgresql postgresql-client postgresql-common"

        # Node.js & PM2
        dpkg -l nodejs 2>/dev/null | grep -q '^ii' && PKGS_TO_REMOVE="$PKGS_TO_REMOVE nodejs"
        command -v pm2 &>/dev/null && run "npm uninstall -g pm2 || true" && ok "Removed PM2"
        command -v yarn &>/dev/null && run "npm uninstall -g yarn || true"
        command -v pnpm &>/dev/null && run "npm uninstall -g pnpm || true"

        # Python (deadsnakes — only non-system Python versions)
        for pkg in $(dpkg -l 2>/dev/null | grep '^ii' | awk '{print $2}' | grep -E '^python3\.[0-9]+-' | grep -v "$(python3 --version 2>/dev/null | grep -oP '\d+\.\d+')"); do
            PKGS_TO_REMOVE="$PKGS_TO_REMOVE $pkg"
        done

        # Certbot
        dpkg -l certbot 2>/dev/null | grep -q '^ii' && PKGS_TO_REMOVE="$PKGS_TO_REMOVE certbot python3-certbot-nginx python3-certbot-apache"

        # Composer
        if [ -f /usr/local/bin/composer ]; then
            run "rm -f /usr/local/bin/composer"
            ok "Removed Composer"
        fi

        if [ -n "$PKGS_TO_REMOVE" ]; then
            echo -e "  Packages to remove: ${PKGS_TO_REMOVE}"
            run "apt-get purge -y $PKGS_TO_REMOVE || true"
            run "apt-get autoremove -y || true"
            ok "Packages removed"
        else
            skip "No agent-installed packages detected"
        fi

        # Remove added APT repos
        for repo_file in \
            /etc/apt/sources.list.d/ondrej-*.list \
            /etc/apt/sources.list.d/ondrej-*.sources \
            /etc/apt/sources.list.d/deadsnakes-*.list \
            /etc/apt/sources.list.d/deadsnakes-*.sources \
            /etc/apt/sources.list.d/nodesource*.list \
            /etc/apt/sources.list.d/nodesource*.sources \
        ; do
            for f in $repo_file; do
                if [ -f "$f" ]; then
                    run "rm -f '$f'"
                    ok "Removed APT repo: $f"
                fi
            done
        done

        # Remove repo GPG keys
        for key_file in \
            /etc/apt/keyrings/nodesource.gpg \
            /usr/share/keyrings/nodesource.gpg \
        ; do
            if [ -f "$key_file" ]; then
                run "rm -f '$key_file'"
                ok "Removed GPG key: $key_file"
            fi
        done
    else
        echo -e "  ${CYAN}⊘${NC} Skipped package removal"
    fi
fi

# ── Summary ─────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"
if $DRY_RUN; then
    echo -e "${CYAN}  DRY RUN COMPLETE — no changes were made${NC}"
else
    echo -e "${GREEN}  ✓ CLEANUP COMPLETE${NC}"
fi
echo -e "${BOLD}════════════════════════════════════════════════════════════${NC}"
echo ""
