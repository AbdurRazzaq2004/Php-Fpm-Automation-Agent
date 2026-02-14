# Universal Deployment Automation Agent

**Production-grade, YAML-driven deployment engine for multi-language applications on Linux servers.**

Deploy applications in **PHP, Python, Node.js, Next.js, Ruby, Go, Java, Rust, .NET**, and **static sites** — all from a single YAML config. Each language gets its own runtime, process manager, and web server configuration, fully automated.

**Smart Auto-Detection** — the agent analyzes your repository and automatically configures the runtime version, framework, dependencies, database, and process manager. Deploy any app with just a few lines of config!

---

## Table of Contents

- [Supported Languages](#supported-languages)
- [Smart Auto-Detection](#smart-auto-detection)
- [Architecture Overview](#architecture-overview)
- [Features](#features)
- [Quick Start](#quick-start)
- [Docker Deployment](#docker-deployment)
- [Installation](#installation)
- [Configuration Reference](#configuration-reference)
- [Commands](#commands)
- [Execution Flow](#execution-flow)
- [Examples](#examples)
- [Safety Guarantees](#safety-guarantees)
- [Security Hardening](#security-hardening)
- [Production Recommendations](#production-recommendations)
- [Extending the Agent](#extending-the-agent)
- [Troubleshooting](#troubleshooting)
- [File Structure](#file-structure)

---

## Supported Languages

| Language | Runtime | Process Manager | Web Server Mode |
|----------|---------|-----------------|-----------------|
| **PHP** | PHP-FPM | PHP-FPM pools | FastCGI proxy |
| **Python** | CPython + venv | systemd (Gunicorn/Uvicorn) | Reverse proxy |
| **Node.js** | Node.js (NodeSource) | PM2 (cluster mode) | Reverse proxy |
| **Next.js** | Node.js | PM2 | Reverse proxy |
| **Ruby** | Ruby + Bundler | systemd (Puma) | Reverse proxy |
| **Go** | Go (official binary) | systemd | Reverse proxy |
| **Java** | OpenJDK + Maven/Gradle | systemd | Reverse proxy |
| **Rust** | rustup + Cargo | systemd | Reverse proxy |
| **.NET** | .NET SDK (Microsoft) | systemd (Kestrel) | Reverse proxy |
| **Static** | Node.js/Hugo/Jekyll (build) | None | Direct file serving |

---

## Smart Auto-Detection

The agent intelligently analyzes your repository after cloning and automatically configures the deployment. **No expertise in any specific language required!**

### Language Detection

If you don't specify `language:` in your config, the agent auto-detects it from your repository:

| Indicator File | Detected Language |
|---------------|-------------------|
| `composer.json` | PHP |
| `requirements.txt`, `pyproject.toml`, `Pipfile` | Python |
| `package.json` (with `next` dep) | Next.js |
| `package.json` (with `express`, etc.) | Node.js |
| `package.json` (with `react-scripts`, `vite`) | Static |
| `Gemfile`, `config.ru` | Ruby |
| `go.mod` | Go |
| `pom.xml`, `build.gradle` | Java |
| `Cargo.toml` | Rust |
| `*.csproj`, `global.json` | .NET |
| `hugo.toml` | Static |

### What Gets Auto-Detected (All Languages)

| Feature | How It Works |
|---------|-------------|
| **Language** | Detected from indicator files (composer.json, go.mod, package.json, etc.) |
| **Runtime Version** | Read from language-specific config files (.python-version, go.mod, .nvmrc, etc.) |
| **Framework** | Detected from dependencies (Laravel, Django, Express, Spring Boot, Rails, Gin, etc.) |
| **Database** | Read from config files (.env, settings.py, application.properties, database.yml) |
| **Package Manager** | Auto-detected (pip/poetry, npm/yarn/pnpm, bundler, maven/gradle, cargo) |
| **Document Root** | Auto-set per framework (Laravel→/public, React→/build, Vue→/dist, Hugo→/public) |
| **Post-Deploy** | Framework-specific commands (migrations, asset compilation, cache clearing) |
| **Process Manager** | PHP→FPM, Node/Next→PM2, everything else→systemd |

### Minimal Config (Auto-Detection Handles the Rest)

```yaml
services:
  - service_name: my-app
    domain: app.example.com
    repo_url: https://github.com/your-org/your-app.git
    branch: main
    deploy_path: /var/www/my-app
    # language: auto  ← detected automatically!
```

### PHP-Specific Auto-Detection

For PHP apps, the agent also reads `composer.json` to detect:
- PHP version constraints (`^8.2`, `>=8.1`, `~7.4`)
- Required extensions (`ext-bcmath`, `ext-mbstring`, etc.)
- Framework type (Laravel, Symfony, WordPress, CodeIgniter, Slim)
- Database driver from `.env` files
- Composer is installed automatically from getcomposer.org

### Database Safety

The agent **never overwrites existing databases**:
- Checks for pre-installed MySQL/MariaDB, PostgreSQL, and SQLite
- Only installs a new database engine if none exists for that type
- Never drops databases or removes data
- Creates SQLite database files only if they don't exist

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    YAML Configuration                       │
│              (services.yml / multi-app.yml)                  │
│              language: php | python | node | ...             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   Config Parser                             │
│          (Parse → Validate → Apply Defaults)                │
│     (Language-specific defaults, conflict detection)        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              Language Detection & Auto-Detection             │
│  ┌──────────────┐ ┌────────────┐ ┌───────────────────────┐  │
│  │   Language   │ │  Runtime   │ │ Framework Detection  │  │
│  │  Detection   │ │  Version   │ │ (Django, Express,    │  │
│  │(indicator    │ │ Detection  │ │  Rails, Spring, etc) │  │
│  │ files)       │ │            │ │                      │  │
│  └──────────────┘ └────────────┘ └───────────────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┼───────────────┐
          ▼            ▼               ▼
   ┌────────────┐ ┌──────────┐ ┌─────────────┐
   │  Package   │ │   Git    │ │   Backup    │
   │ Installer  │ │ Manager  │ │  Manager    │
   └─────┬──────┘ └────┬─────┘ └──────┬──────┘
         │             │              │
         ▼             ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│           ★ Language-Specific Installation ★                │
│                                                             │
│  PHP Path:                                                  │
│  ┌──────────────┐ ┌────────────┐ ┌───────────────────────┐  │
│  │ Install PHP  │ │  Composer  │ │ PHP-FPM Pool Config  │  │
│  │ + Extensions │ │ (official) │ │ (isolated per app)   │  │
│  └──────────────┘ └────────────┘ └───────────────────────┘  │
│                                                             │
│  Non-PHP Path:                                              │
│  ┌──────────────┐ ┌────────────┐ ┌───────────────────────┐  │
│  │ Install      │ │ Install    │ │ Build Application    │  │
│  │ Runtime      │ │ Deps       │ │ (compile/bundle)     │  │
│  │(Python/Node/ │ │(pip/npm/   │ │                      │  │
│  │ Go/Java/etc) │ │ cargo/etc) │ │                      │  │
│  └──────────────┘ └────────────┘ └───────────────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│               Deployment Engine (per service)               │
│  ┌──────────┐ ┌──────────────┐ ┌─────────────────────────┐  │
│  │ Process  │ │  Web Server  │ │    Permissions &        │  │
│  │ Manager  │ │  Configurator│ │    Hooks Runner         │  │
│  │(FPM/syst │ │ (Nginx/Apach │ │                         │  │
│  │ emd/PM2) │ │ e+proxy)     │ │                         │  │
│  └──────────┘ └──────────────┘ └─────────────────────────┘  │
│                                                             │
│  Web Server Modes:                                          │
│  PHP    → FastCGI proxy to FPM socket                       │
│  Others → HTTP reverse proxy to app_port                    │
│  Static → Direct file serving (SPA routing)                 │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│             Validation Engine (Post-deploy)                  │
│   Service check │ Port check │ HTTP health │ Permissions    │
└─────────────────────────────────────────────────────────────┘
```

---

## Features

### Smart Auto-Detection
- **Language detection** — auto-detects from repository indicator files
- **Runtime version detection** — reads composer.json, .python-version, go.mod, .nvmrc, etc.
- **Framework detection** — Laravel, Django, Flask, Express, Spring Boot, Rails, Gin, and more
- **Database detection** — MySQL, PostgreSQL, SQLite from config files across all languages
- **Package manager detection** — pip/poetry, npm/yarn/pnpm, bundler, maven/gradle, cargo
- **Smart branch handling** — detects default branch, falls back if specified branch missing
- **Process manager selection** — PHP-FPM for PHP, PM2 for Node/Next, systemd for others

### Core Capabilities
- **YAML-driven** — define services declaratively, deploy with one command
- **Multi-language** — PHP, Python, Node.js, Next.js, Ruby, Go, Java, Rust, .NET, Static
- **Multi-service** — deploy N applications on the same server safely
- **Multi-web-server** — Nginx and Apache support (even mixed on same server)
- **Private repos** — secure PAT token cloning (tokens never logged)
- **Idempotent** — safe to run multiple times without side effects

### Safety
- **Never overwrites** foreign configurations
- **Backs up** all configs before changes
- **Validates** configuration syntax before reload
- **Tests** web server config (`nginx -t` / `configtest`) before reload
- **Uses reload** instead of restart (zero downtime)
- **Automatic rollback** on deployment failure
- **Per-service isolation** (user, socket, pool, logs)

### Production Features
- Security headers (X-Frame-Options, HSTS, CSP, etc.)
- Gzip compression
- Static asset caching
- Hidden file protection
- Upload directory PHP execution prevention
- Open basedir restrictions
- OPcache configuration
- SSL/TLS via Let's Encrypt or custom certificates
- Cron job management
- Shared directories (persistent storage)
- Pre/post deploy hooks

---

## Quick Start

```bash
# 1. Clone this repo to your server
git clone https://github.com/your-org/php-fpm-automation.git /opt/php-deployer
cd /opt/php-deployer

# 2. Run the installer
sudo ./install.sh

# 3. Create your config (or use an example)
cp examples/single-app.yml my-services.yml
# Edit my-services.yml with your settings

# 4. Validate the config
sudo php-deployer validate --config my-services.yml

# 5. Dry run (checks everything, changes nothing)
sudo php-deployer deploy --config my-services.yml --dry-run

# 6. Deploy!
sudo php-deployer deploy --config my-services.yml
```

---

## Docker Deployment

Don't want to install anything on the host? Run the agent inside a Docker container — it configures the **host server** using `nsenter`, not the container itself.

### Prerequisites
- Docker installed on the target server
- Your `services.yml` config ready

### Option 1: Using `deploy.sh` (easiest)
```bash
# Clone this repo to the server
git clone https://github.com/your-org/php-fpm-automation.git /opt/php-deployer
cd /opt/php-deployer

# Edit config with your real values
nano services.yml

# Deploy (builds image automatically on first run)
./deploy.sh deploy

# Validate only
./deploy.sh validate

# Dry run
./deploy.sh deploy --dry-run
```

### Option 2: Using `docker compose`
```bash
# Build the image
docker compose build

# Deploy
docker compose run --rm deployer deploy

# Validate
docker compose run --rm deployer validate

# Dry run
docker compose run --rm deployer deploy --dry-run

# Status check
docker compose run --rm deployer status
```

### Option 3: Raw `docker run`
```bash
# Build
docker build -t php-deployer .

# Deploy
docker run --rm --privileged --pid=host --network=host \
  -v /:/host \
  -v ./services.yml:/app/services.yml \
  php-deployer deploy

# Use a different config
docker run --rm --privileged --pid=host --network=host \
  -v /:/host \
  -v ./production.yml:/app/services.yml \
  php-deployer deploy
```

### How Docker Mode Works

```
┌─────────────────────────────────────────────────────┐
│               Docker Container                       │
│  ┌───────────────────────────────────────────────┐   │
│  │  docker-entrypoint.sh                         │   │
│  │  1. Copy tool to host's /tmp                  │   │
│  │  2. Ensure Python + PyYAML on host            │   │
│  │  3. nsenter into host namespace               │   │
│  │  4. Run deployer.py on HOST                   │   │
│  │  5. Clean up /tmp staging                     │   │
│  └────────────────────┬──────────────────────────┘   │
│                       │ nsenter                      │
│                       ▼                              │
│  ┌───────────────────────────────────────────────┐   │
│  │  HOST NAMESPACE (via --privileged --pid=host) │   │
│  │  • apt/yum install packages                   │   │
│  │  • systemctl manage services                  │   │
│  │  • Write /etc/nginx, /etc/php configs         │   │
│  │  • Create users, set permissions              │   │
│  │  • Git clone to /var/www                      │   │
│  └───────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

> **Note:** `--privileged`, `--pid=host`, and `-v /:/host` are required because the container needs to execute commands directly on the host (install packages, manage services, write configs). The container itself is ephemeral — it does its job and is removed (`--rm`).

---

## Installation

### Prerequisites
- **OS:** Ubuntu 20.04+, Debian 11+, CentOS/RHEL 8+, Rocky Linux 8+, AlmaLinux 8+
- **Python:** 3.8+ (installer handles this)
- **Root access:** Required for package installation and service management

### Automated Install
```bash
sudo ./install.sh
```

This will:
1. Install Python 3.8+ if missing
2. Install `pip` and `PyYAML`
3. Install system utilities (git, curl, wget, unzip, rsync)
4. Create log/backup directory structure
5. Install `php-deployer` command globally

### Manual Install
```bash
# Install Python dependencies
pip3 install -r requirements.txt

# Create directories
sudo mkdir -p /var/log/php-deployer/sessions
sudo mkdir -p /var/log/php-fpm
sudo mkdir -p /var/backups/php-deployer
sudo mkdir -p /etc/php-deployer/envs
sudo mkdir -p /run/php

# Run directly
sudo python3 deployer.py deploy --config services.yml
```

---

## Configuration Reference

### Single Service Config

```yaml
service_name: my-app              # Required. Unique name (alphanumeric, hyphens, underscores)
domain: app.example.com           # Required. Domain name
repo_url: https://github.com/org/repo.git  # Required. Git repository URL
branch: main                      # Git branch (default: main)
deploy_path: /var/www/my-app      # Required. Absolute deployment path
php_version: "8.2"                # PHP version (default: 8.2)
web_server: nginx                 # nginx or apache (default: nginx)
```

### Multi-Service Config

```yaml
services:
  - service_name: app-one
    domain: one.example.com
    repo_url: https://github.com/org/app-one.git
    deploy_path: /var/www/app-one
    php_version: "8.2"
    web_server: nginx

  - service_name: app-two
    domain: two.example.com
    repo_url: https://github.com/org/app-two.git
    deploy_path: /var/www/app-two
    php_version: "8.1"
    web_server: nginx
```

### All Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `service_name` | string | **required** | Unique identifier (2-64 chars, alphanumeric) |
| `domain` | string | **required** | Domain name (e.g., `app.example.com`) |
| `repo_url` | string | **required** | Git repository URL (HTTPS or SSH) |
| `deploy_path` | string | **required** | Absolute path for code deployment |
| `branch` | string | `main` | Git branch to deploy |
| `php_version` | string | `8.2` | PHP version (`7.4`, `8.0`–`8.4`) |
| `web_server` | string | `nginx` | `nginx` or `apache` |
| `php_extensions` | list | [common set] | PHP extensions to install |
| `pat_token` | string | `null` | GitHub/GitLab Personal Access Token |
| `enable_ssl` | bool | `false` | Enable HTTPS (Let's Encrypt or custom) |
| `ssl_cert_path` | string | auto | Path to SSL certificate file |
| `ssl_key_path` | string | auto | Path to SSL private key |
| `environment_file` | string | `null` | Path to .env file to deploy |
| `user` | string | auto | System user (auto: `svc_<service_name>`) |
| `group` | string | `www-data` | System group |
| `document_root_suffix` | string | `""` | Subdirectory as doc root (e.g., `public`) |
| `max_upload_size` | string | `64M` | Max upload size |
| `php_memory_limit` | string | `256M` | PHP memory limit |
| `php_max_execution_time` | int | `300` | Max execution time (seconds) |
| `php_pool_max_children` | int | `10` | Max FPM child processes |
| `php_pool_start_servers` | int | `2` | Initial FPM child processes |
| `php_pool_min_spare` | int | `1` | Minimum spare FPM processes |
| `php_pool_max_spare` | int | `4` | Maximum spare FPM processes |
| `php_pool_max_requests` | int | `500` | Requests before worker recycling |
| `pre_deploy_commands` | list | `[]` | Commands before deployment |
| `post_deploy_commands` | list | `[]` | Commands after deployment |
| `writable_dirs` | list | `[]` | Directories needing write access |
| `shared_dirs` | list | `[]` | Directories persisted across deployments |
| `cron_jobs` | list | `[]` | Cron entries for this service |
| `extra_nginx_config` | string | `""` | Additional Nginx config directives |
| `extra_apache_config` | string | `""` | Additional Apache config directives |

---

## Commands

### `deploy` — Deploy services
```bash
sudo php-deployer deploy --config services.yml [--verbose] [--dry-run]
```
- `--config` / `-c` — YAML configuration file (required)
- `--verbose` / `-v` — Enable debug-level logging
- `--dry-run` — Validate everything without making changes

### `validate` — Validate configuration
```bash
sudo php-deployer validate --config services.yml
```
Checks YAML syntax, required fields, value constraints, and cross-service conflicts.

### `status` — Show service status
```bash
sudo php-deployer status --config services.yml
```
Shows: deployment state, PHP-FPM status, socket existence, web server state, vhost config, git info, backup count.

### `rollback` — Rollback a service
```bash
sudo php-deployer rollback --service myapp [--timestamp 20260214_120000]
```
Restores all backed-up configs and reloads services. Uses latest backup by default.

---

## Execution Flow

Each service goes through this **20-step pipeline**:

```
 1. Parse & validate YAML configuration
 2. Run pre-deployment system checks
 3. Detect installed software
 4. Create backups of existing configs
 5. Install missing packages (idempotent)
 6. Create service user (if needed)
 7. Clone/update repository
 8. Deploy environment file
 9. Run pre-deploy hooks
10. Create PHP-FPM pool configuration
11. Generate web server vhost
12. Set file permissions
13. Setup SSL (if enabled)
14. Validate all configurations
15. Reload PHP-FPM (safe: validate → reload)
16. Reload web server (safe: test → reload)
17. Run post-deploy hooks (composer install, etc.)
18. Post-deployment health checks
19. Save backup manifest
20. Print deployment summary
```

**On failure at any step:** Automatic rollback to backed-up state.

---

## Examples

### Deploy a single Laravel app
```bash
sudo php-deployer deploy --config examples/laravel-app.yml
```

### Deploy 3 apps on the same server
```bash
sudo php-deployer deploy --config examples/multi-app.yml
```

### Deploy WordPress with Apache
```bash
sudo php-deployer deploy --config examples/wordpress-app.yml
```

### Mixed PHP versions (7.4 + 8.2 + 8.3 simultaneously)
```bash
sudo php-deployer deploy --config examples/mixed-stack.yml
```

---

## Safety Guarantees

| Guarantee | How |
|-----------|-----|
| **Never overwrites foreign configs** | Each generated config has a marker comment; agent refuses to overwrite configs it didn't create |
| **Never removes existing services** | Only additive operations; no deletions of other service configs |
| **Never restarts blindly** | Always validates config (`nginx -t` / `configtest`) before reload; uses `reload` not `restart` |
| **Automatic rollback** | All configs backed up before changes; restored on failure |
| **No socket conflicts** | Each service gets unique socket: `/run/php/php8.2-fpm-<service_name>.sock` |
| **No user conflicts** | Each service gets unique user: `svc_<service_name>` |
| **Idempotent** | Safe to run multiple times; only installs missing packages |
| **Backup retention** | Keeps last 5 backups per service; automatic cleanup |

---

## Security Hardening

### What the agent configures automatically:

**Web Server Level:**
- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Strict-Transport-Security` (with SSL)
- Hidden files denied (`/\.`)
- Sensitive files denied (`.env`, `.git`, `composer.lock`)
- PHP execution denied in upload directories
- Server signature hidden

**PHP-FPM Level:**
- `open_basedir` restricted to deploy path
- Dangerous functions disabled (`exec`, `passthru`, `shell_exec`, etc.)
- `expose_php = off`
- Secure session cookies (`httponly`, `secure`, `strict_mode`)
- OPcache enabled with sane defaults
- Per-pool resource limits

**System Level:**
- Dedicated non-login system user per service
- File permissions: directories `750`, files `640`
- `.env` file permissions: `600`
- PAT tokens never logged (masked in all output)

### Additional Recommendations:

```bash
# 1. Enable and configure UFW firewall
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# 2. Install and configure Fail2Ban
sudo apt install fail2ban
sudo systemctl enable fail2ban

# 3. Disable root SSH login
# Edit /etc/ssh/sshd_config:
#   PermitRootLogin no
#   PasswordAuthentication no

# 4. Set up automatic security updates
sudo apt install unattended-upgrades
sudo dpkg-reconfigure unattended-upgrades

# 5. Configure log rotation
cat > /etc/logrotate.d/php-deployer << EOF
/var/log/php-deployer/**/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
}
EOF

cat > /etc/logrotate.d/php-fpm-services << EOF
/var/log/php-fpm/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        systemctl reload php*-fpm 2>/dev/null || true
    endscript
}
EOF
```

---

## Production Recommendations

### Server Sizing

| Services | RAM | CPU | Disk |
|----------|-----|-----|------|
| 1-2 apps | 2 GB | 2 vCPU | 40 GB SSD |
| 3-5 apps | 4 GB | 4 vCPU | 80 GB SSD |
| 5-10 apps | 8 GB | 4-8 vCPU | 160 GB SSD |

### PHP-FPM Pool Tuning

```
Available Memory for PHP = Total RAM - OS - Web Server - DB
max_children = Available Memory / Average PHP Process Size

Example (4GB RAM, 50MB avg process):
  Available: 4096 - 512 (OS) - 128 (Nginx) - 512 (MySQL) = 2944 MB
  max_children per pool: 2944 / 50 / number_of_pools
```

### Monitoring Checklist

- [ ] Set up monitoring for PHP-FPM pools (`pm.status_path`)
- [ ] Monitor FPM slow logs (`/var/log/php-fpm/<service>-slow.log`)
- [ ] Set up disk space alerts
- [ ] Monitor SSL certificate expiry
- [ ] Set up log aggregation (ELK, Loki, etc.)
- [ ] Configure alerting for service failures

---

## Extending the Agent

### Adding Docker Support (Future)

The modular architecture makes it straightforward to add Docker containerization:

```python
# modules/docker.py (future extension)
class DockerBuilder:
    def generate_dockerfile(self, config):
        """Generate Dockerfile from service config."""
        pass

    def generate_compose(self, services):
        """Generate docker-compose.yml for all services."""
        pass
```

### Adding CI/CD Integration

```yaml
# In your .github/workflows/deploy.yml
- name: Deploy to production
  run: |
    ssh deploy@server "sudo php-deployer deploy -c /opt/configs/services.yml"
```

### Adding New Web Servers (e.g., Caddy)

1. Create `modules/caddy.py` implementing `generate_vhost()`, `test_config()`, `safe_reload()`
2. Add `"caddy"` to `SUPPORTED_WEB_SERVERS` in `config/schema.py`
3. Add Caddy branch in `deployer.py`'s deployment pipeline

### Adding Database Migration Support

```python
# modules/database.py (future extension)
class DatabaseManager:
    def run_migrations(self, config):
        """Run framework-specific database migrations."""
        pass

    def backup_database(self, config):
        """Backup database before deployment."""
        pass
```

---

## Troubleshooting

### Common Issues

**1. "Must run as root"**
```bash
sudo python3 deployer.py deploy --config services.yml
```

**2. PHP version not available**
```bash
# Check available versions
apt-cache search php | grep fpm

# If using Ubuntu, ensure PPA is added
sudo add-apt-repository ppa:ondrej/php
sudo apt update
```

**3. Nginx config test fails**
```bash
# Check the generated config
cat /etc/nginx/sites-available/<service>.conf

# Test manually
sudo nginx -t

# Check error details
sudo nginx -t 2>&1
```

**4. PHP-FPM won't start**
```bash
# Check config
php-fpm8.2 -t

# Check logs
journalctl -u php8.2-fpm -n 50

# Check pool config
cat /etc/php/8.2/fpm/pool.d/<service>.conf
```

**5. Permission denied errors**
```bash
# Check file ownership
ls -la /var/www/<service>/

# Check socket permissions
ls -la /run/php/

# Check user exists
id svc_<service_name>
```

### Log Locations

| Log | Path |
|-----|------|
| Agent logs | `/var/log/php-deployer/sessions/` |
| PHP-FPM access | `/var/log/php-fpm/<service>-access.log` |
| PHP-FPM errors | `/var/log/php-fpm/<service>-error.log` |
| PHP-FPM slow | `/var/log/php-fpm/<service>-slow.log` |
| Nginx access | `/var/log/nginx/<service>-access.log` |
| Nginx error | `/var/log/nginx/<service>-error.log` |
| Apache access | `/var/log/apache2/<service>-access.log` |
| Apache error | `/var/log/apache2/<service>-error.log` |

---

## File Structure

```
php-fpm-automation/
├── deployer.py              # Main orchestrator & CLI entry point
├── services.yml             # Default deployment config (edit this)
├── install.sh               # Bootstrap installer script (bare metal)
├── deploy.sh                # Quick deploy script (Docker mode)
├── Dockerfile               # Container image definition
├── docker-compose.yml       # Docker Compose for easy usage
├── docker-entrypoint.sh     # Container → host bridge (nsenter)
├── .dockerignore            # Files excluded from Docker build
├── requirements.txt         # Python dependencies
├── README.md                # This file
│
├── config/
│   ├── __init__.py
│   ├── parser.py            # YAML parser, validator, conflict detector
│   └── schema.py            # Config schema, defaults, allowed values
│
├── modules/
│   ├── __init__.py
│   ├── logger.py            # Structured logging (console + file)
│   ├── system.py            # OS/software detection (non-destructive)
│   ├── backup.py            # Backup & rollback manager
│   ├── packages.py          # Idempotent package installer
│   ├── git.py               # Secure git operations (PAT + smart branch)
│   ├── phpfpm.py            # PHP-FPM pool manager
│   ├── nginx.py             # Nginx/Apache vhost generator (FastCGI + reverse proxy)
│   ├── apache.py            # Apache vhost generator (FastCGI + reverse proxy)
│   ├── ssl.py               # SSL/TLS certificate manager
│   ├── permissions.py       # File ownership & permissions
│   ├── hooks.py             # Pre/post deploy hooks runner
│   ├── validation.py        # Pre-flight & post-deploy checks
│   ├── autodetect.py        # ★ Smart PHP/framework/DB auto-detection
│   ├── database.py          # ★ Safe database & Composer management
│   ├── language_detect.py   # ★ Multi-language auto-detection
│   ├── process_manager.py   # ★ Systemd & PM2 process management
│   │
│   └── runtimes/            # ★ Language-specific runtime modules
│       ├── __init__.py      # Runtime registry & factory
│       ├── base.py          # Abstract base class for all runtimes
│       ├── python_runtime.py  # Python (Django, Flask, FastAPI)
│       ├── node_runtime.py    # Node.js (Express, Koa, NestJS)
│       ├── nextjs_runtime.py  # Next.js SSR
│       ├── ruby_runtime.py    # Ruby (Rails, Sinatra)
│       ├── go_runtime.py      # Go (Gin, Echo, Fiber)
│       ├── java_runtime.py    # Java (Spring Boot, Quarkus)
│       ├── rust_runtime.py    # Rust (Actix, Axum, Rocket)
│       ├── dotnet_runtime.py  # .NET (ASP.NET Core)
│       └── static_runtime.py  # Static sites (React, Vue, Hugo)
│
└── examples/
    ├── single-app.yml       # Single PHP app (minimal config)
    ├── multi-app.yml        # Multiple apps on same server
    ├── laravel-app.yml      # Laravel with auto-detection
    ├── wordpress-app.yml    # WordPress with Apache
    ├── mixed-stack.yml      # Mixed PHP versions + web servers
    ├── python-django-app.yml  # Python Django/Flask
    ├── node-app.yml           # Node.js Express
    ├── nextjs-app.yml         # Next.js SSR
    ├── go-app.yml             # Go API
    ├── java-spring-app.yml    # Java Spring Boot
    ├── ruby-rails-app.yml     # Ruby on Rails
    ├── rust-app.yml           # Rust API
    ├── dotnet-app.yml         # .NET ASP.NET Core
    └── static-site.yml        # Static sites (React, Vue, Hugo)
```

---

## License

MIT License. Use freely in production.
