"""
Config Schema - PHP-FPM Automation Agent
==========================================
Defines the YAML configuration schema, defaults, and
allowed values for all service configuration fields.
"""

from typing import Any, Dict, List

# ── Supported Values ────────────────────────────────────────────

SUPPORTED_PHP_VERSIONS = ["7.4", "8.0", "8.1", "8.2", "8.3", "8.4"]

SUPPORTED_WEB_SERVERS = ["nginx", "apache"]

COMMON_PHP_EXTENSIONS = [
    "cli", "fpm", "common", "mysql", "pgsql", "sqlite3",
    "curl", "gd", "mbstring", "xml", "zip", "bcmath",
    "intl", "soap", "redis", "memcached", "imagick",
    "opcache", "readline", "tokenizer", "json", "iconv",
    "fileinfo", "dom", "pdo", "pdo_mysql", "pdo_pgsql",
]

# ── Default Values ──────────────────────────────────────────────

SERVICE_DEFAULTS: Dict[str, Any] = {
    "branch": "main",
    "php_version": "8.2",
    "web_server": "nginx",
    "php_extensions": ["cli", "fpm", "common", "curl", "mbstring", "xml", "zip", "mysql", "opcache"],
    "enable_ssl": False,
    "user": None,           # Will be auto-generated: svc_<service_name>
    "group": "www-data",
    "environment_file": None,
    "pat_token": None,
    "extra_nginx_config": "",
    "extra_apache_config": "",
    "document_root_suffix": "",  # e.g., "/public" for Laravel
    "max_upload_size": "64M",
    "php_memory_limit": "256M",
    "php_max_execution_time": 300,
    "php_pool_max_children": 10,
    "php_pool_start_servers": 2,
    "php_pool_min_spare": 1,
    "php_pool_max_spare": 4,
    "php_pool_max_requests": 500,
    "ssl_cert_path": None,
    "ssl_key_path": None,
    "pre_deploy_commands": [],
    "post_deploy_commands": [],
    "shared_dirs": [],       # Directories preserved across deployments
    "writable_dirs": [],     # Directories that need write permissions
    "cron_jobs": [],         # Cron entries for this service
}

# ── Required Fields ─────────────────────────────────────────────

REQUIRED_FIELDS = [
    "service_name",
    "domain",
    "repo_url",
    "deploy_path",
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
        "pattern": r"^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?(\.[a-zA-Z]{2,})+$",
        "description": "Valid domain name (e.g., example.com)",
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
    "php_version": {
        "type": str,
        "allowed": SUPPORTED_PHP_VERSIONS,
        "description": f"One of: {', '.join(SUPPORTED_PHP_VERSIONS)}",
    },
    "web_server": {
        "type": str,
        "allowed": SUPPORTED_WEB_SERVERS,
        "description": f"One of: {', '.join(SUPPORTED_WEB_SERVERS)}",
    },
    "branch": {
        "type": str,
        "description": "Git branch name",
    },
    "enable_ssl": {
        "type": bool,
        "description": "Enable HTTPS via SSL/TLS",
    },
    "php_extensions": {
        "type": list,
        "description": "List of PHP extensions to install",
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
        "description": "Shell commands to run after deployment (e.g., composer install)",
    },
}
