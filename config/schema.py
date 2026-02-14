"""
Config Schema - Universal Deployment Automation Agent
======================================================
Defines the YAML configuration schema, defaults, and
allowed values for all service configuration fields.

Supports multiple languages:
- PHP (FPM pools, Composer, Laravel/WordPress/Symfony/etc.)
- Python (virtualenv, pip/poetry, Gunicorn/Uvicorn, Django/Flask/FastAPI)
- Node.js (npm/yarn/pnpm, PM2/systemd, Express/Koa/NestJS)
- Next.js (SSR/SSG, npm/yarn, standalone server)
- Ruby (Bundler, Puma/Unicorn, Rails/Sinatra)
- Go (compiled binary, systemd)
- Java (Maven/Gradle, Spring Boot JAR)
- Rust (Cargo, compiled binary)
- .NET (dotnet CLI, Kestrel, ASP.NET Core)
- Static (React/Vue/Angular/Svelte — build + serve)

Smart Defaults:
- Language: auto-detected from repository files
- Runtime version: auto-detected from lock files / config
- Document root: auto-detected from framework type
- Package manager: auto-detected (composer/pip/npm/yarn/etc.)
- Database: auto-detected from framework config
- Process manager: auto-selected (FPM for PHP, systemd/PM2 for others)
"""

from typing import Any, Dict, List

# ── Supported Values ────────────────────────────────────────────

SUPPORTED_LANGUAGES = [
    "php", "python", "node", "nextjs", "ruby",
    "go", "java", "rust", "dotnet", "static",
]

SUPPORTED_PHP_VERSIONS = ["7.4", "8.0", "8.1", "8.2", "8.3", "8.4"]

SUPPORTED_PYTHON_VERSIONS = ["3.8", "3.9", "3.10", "3.11", "3.12", "3.13"]

SUPPORTED_NODE_VERSIONS = ["16", "18", "20", "22"]

SUPPORTED_RUBY_VERSIONS = ["3.0", "3.1", "3.2", "3.3"]

SUPPORTED_GO_VERSIONS = ["1.21", "1.22", "1.23"]

SUPPORTED_JAVA_VERSIONS = ["11", "17", "21"]

SUPPORTED_DOTNET_VERSIONS = ["6.0", "7.0", "8.0", "9.0"]

SUPPORTED_RUST_VERSIONS = ["stable", "nightly"]

SUPPORTED_WEB_SERVERS = ["nginx", "apache"]

SUPPORTED_PROCESS_MANAGERS = ["systemd", "pm2"]

# ── Language-Specific Defaults ──────────────────────────────────

LANGUAGE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "php": {
        "runtime_version": "8.2",
        "package_manager": "composer",
        "process_manager": "fpm",       # PHP-FPM (special case)
        "app_port": None,                # FPM uses sockets, not ports
        "entry_point": "index.php",
        "build_command": None,
        "start_command": None,           # FPM manages processes
        "install_command": "composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev",
        "extensions": ["cli", "fpm", "common", "curl", "mbstring", "xml", "zip", "opcache"],
    },
    "python": {
        "runtime_version": "3.11",
        "package_manager": "pip",        # or poetry, pipenv
        "process_manager": "systemd",
        "app_port": 8000,
        "entry_point": None,             # Auto-detected (manage.py, app.py, main.py, wsgi.py)
        "build_command": None,
        "start_command": None,           # Auto-detected (gunicorn, uvicorn, etc.)
        "install_command": None,         # Auto-detected from requirements.txt/pyproject.toml
        "extensions": [],
    },
    "node": {
        "runtime_version": "20",
        "package_manager": "npm",        # or yarn, pnpm
        "process_manager": "pm2",        # or systemd
        "app_port": 3000,
        "entry_point": None,             # Auto-detected from package.json "main"
        "build_command": None,           # Auto-detected (npm run build)
        "start_command": None,           # Auto-detected (npm start or node server.js)
        "install_command": None,         # Auto-detected (npm install / yarn install)
        "extensions": [],
    },
    "nextjs": {
        "runtime_version": "20",
        "package_manager": "npm",
        "process_manager": "pm2",
        "app_port": 3000,
        "entry_point": None,
        "build_command": None,           # Auto: npm run build / next build
        "start_command": None,           # Auto: npm start / next start
        "install_command": None,
        "extensions": [],
    },
    "ruby": {
        "runtime_version": "3.2",
        "package_manager": "bundler",
        "process_manager": "systemd",
        "app_port": 3000,
        "entry_point": "config.ru",
        "build_command": None,
        "start_command": None,           # Auto-detected (puma, unicorn, etc.)
        "install_command": "bundle install --deployment --without development test",
        "extensions": [],
    },
    "go": {
        "runtime_version": "1.22",
        "package_manager": "go",
        "process_manager": "systemd",
        "app_port": 8080,
        "entry_point": None,             # Auto-detected (main.go, cmd/)
        "build_command": "go build -o app .",
        "start_command": "./app",
        "install_command": "go mod download",
        "extensions": [],
    },
    "java": {
        "runtime_version": "17",
        "package_manager": "maven",      # or gradle
        "process_manager": "systemd",
        "app_port": 8080,
        "entry_point": None,             # Auto: target/*.jar or build/libs/*.jar
        "build_command": None,           # Auto: mvn package or gradle build
        "start_command": None,           # Auto: java -jar app.jar
        "install_command": None,
        "extensions": [],
    },
    "rust": {
        "runtime_version": "stable",
        "package_manager": "cargo",
        "process_manager": "systemd",
        "app_port": 8080,
        "entry_point": None,
        "build_command": "cargo build --release",
        "start_command": None,           # Auto: ./target/release/<binary_name>
        "install_command": None,
        "extensions": [],
    },
    "dotnet": {
        "runtime_version": "8.0",
        "package_manager": "dotnet",
        "process_manager": "systemd",
        "app_port": 5000,
        "entry_point": None,             # Auto: *.csproj
        "build_command": "dotnet publish -c Release -o ./publish",
        "start_command": None,           # Auto: dotnet ./publish/<app>.dll
        "install_command": "dotnet restore",
        "extensions": [],
    },
    "static": {
        "runtime_version": "20",         # Node for building
        "package_manager": "npm",
        "process_manager": None,         # No process — web server serves directly
        "app_port": None,
        "entry_point": "index.html",
        "build_command": None,           # Auto: npm run build
        "start_command": None,
        "install_command": None,
        "extensions": [],
    },
}

# ── Default Values ──────────────────────────────────────────────

SERVICE_DEFAULTS: Dict[str, Any] = {
    "branch": "main",
    "language": None,               # Auto-detected from repository
    "runtime_version": None,        # Auto-detected per language
    "web_server": "nginx",
    "process_manager": None,        # Auto-selected per language
    "package_manager": None,        # Auto-detected per language
    "app_port": None,               # Auto-set per language (3000, 8000, 8080, etc.)
    "entry_point": None,            # Auto-detected (index.php, app.py, server.js, etc.)
    "build_command": None,          # Auto-detected from framework
    "start_command": None,          # Auto-detected from framework
    "install_command": None,        # Auto-detected from package manager
    "enable_ssl": False,
    "auto_detect": True,
    "user": None,
    "group": "www-data",
    "environment_file": None,
    "environment_vars": {},         # Key-value env vars injected into process
    "pat_token": None,
    "extra_nginx_config": "",
    "extra_apache_config": "",
    "document_root_suffix": "",
    "max_upload_size": "64M",
    "ssl_cert_path": None,
    "ssl_key_path": None,
    "pre_deploy_commands": [],
    "post_deploy_commands": [],
    "shared_dirs": [],
    "writable_dirs": [],
    "cron_jobs": [],

    # PHP-specific (only used when language=php)
    "php_version": "8.2",
    "php_extensions": ["cli", "fpm", "common", "curl", "mbstring", "xml", "zip", "opcache"],
    "php_memory_limit": "256M",
    "php_max_execution_time": 300,
    "php_pool_max_children": 10,
    "php_pool_start_servers": 2,
    "php_pool_min_spare": 1,
    "php_pool_max_spare": 4,
    "php_pool_max_requests": 500,

    # Node/Next.js-specific
    "node_instances": 0,            # PM2 instances (0 = auto/cluster mode)
    "node_max_memory": 512,            # MB — PM2/systemd max memory before restart
}

# ── Required Fields ─────────────────────────────────────────────

REQUIRED_FIELDS = [
    "service_name",
    "domain",
    "repo_url",
    "deploy_path",
]

# Fields that are strongly recommended but will use smart defaults
RECOMMENDED_FIELDS = [
    "branch",
    "language",             # Can be auto-detected, but recommended to specify
]

# ── Field Validators ────────────────────────────────────────────

FIELD_VALIDATORS = {
    "service_name": {
        "type": str,
        "pattern": r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$",
        "description": "Alphanumeric with hyphens/underscores, 2-64 chars, starts with letter",
    },
    "domain": {
        "type": str,
        "pattern": r"^([a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+|(\d{1,3}\.){3}\d{1,3})$",
        "description": "Valid domain name (e.g., example.com) or IPv4 address",
    },
    "repo_url": {
        "type": str,
        "pattern": r"^https?://.*\.git$|^git@.*\.git$|^https?://.*$",
        "description": "Git repository URL (HTTPS or SSH)",
    },
    "deploy_path": {
        "type": str,
        "pattern": r"^/[a-zA-Z0-9/_.-]+$",
        "description": "Absolute path for deployment",
    },
    "language": {
        "type": str,
        "allowed": SUPPORTED_LANGUAGES,
        "description": f"Programming language. Auto-detected if omitted. One of: {', '.join(SUPPORTED_LANGUAGES)}",
        "optional": True,
    },
    "runtime_version": {
        "type": str,
        "description": "Runtime version (e.g., '8.2' for PHP, '3.11' for Python, '20' for Node). Auto-detected if omitted.",
        "optional": True,
    },
    "php_version": {
        "type": str,
        "allowed": SUPPORTED_PHP_VERSIONS,
        "description": f"PHP version (legacy field, use runtime_version instead). One of: {', '.join(SUPPORTED_PHP_VERSIONS)}",
        "optional": True,
    },
    "web_server": {
        "type": str,
        "allowed": SUPPORTED_WEB_SERVERS,
        "description": f"One of: {', '.join(SUPPORTED_WEB_SERVERS)}",
    },
    "process_manager": {
        "type": str,
        "allowed": SUPPORTED_PROCESS_MANAGERS,
        "description": f"Process manager. Auto-selected per language. One of: {', '.join(SUPPORTED_PROCESS_MANAGERS)}",
        "optional": True,
    },
    "branch": {
        "type": str,
        "description": "Git branch name",
    },
    "auto_detect": {
        "type": bool,
        "description": "Enable auto-detection of language, version, framework, DB, and dependencies from code",
    },
    "enable_ssl": {
        "type": bool,
        "description": "Enable HTTPS via SSL/TLS",
    },
    "app_port": {
        "type": int,
        "min": 1024,
        "max": 65535,
        "description": "Application port for reverse proxy (auto-set per language)",
        "optional": True,
    },
    "max_upload_size": {
        "type": str,
        "pattern": r"^\d+[KMG]?$",
        "description": "Max upload size (e.g., 64M, 128M)",
    },
    "php_pool_max_children": {
        "type": int,
        "min": 1,
        "max": 500,
        "description": "Max PHP-FPM child processes",
    },
    "pre_deploy_commands": {
        "type": list,
        "description": "Shell commands to run before deployment",
    },
    "post_deploy_commands": {
        "type": list,
        "description": "Shell commands to run after deployment",
    },
    "environment_vars": {
        "type": dict,
        "description": "Key-value environment variables to inject into the app process",
        "optional": True,
    },
}
