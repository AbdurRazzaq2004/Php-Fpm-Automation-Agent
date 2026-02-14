"""
Config Parser - Universal Deployment Automation Agent
=====================================================
Parses and validates YAML configuration files.
Applies defaults, normalizes values, and detects conflicts.
Supports multi-language deployments (PHP, Python, Node, etc.)
"""

import os
import re
import yaml
from typing import Any, Dict, List, Optional, Tuple

from config.schema import (
    FIELD_VALIDATORS,
    LANGUAGE_DEFAULTS,
    REQUIRED_FIELDS,
    SERVICE_DEFAULTS,
    SUPPORTED_LANGUAGES,
    SUPPORTED_PHP_VERSIONS,
    SUPPORTED_WEB_SERVERS,
)
from modules.logger import DeployLogger


class ConfigParser:
    """
    Parses YAML config, validates all fields, applies defaults,
    and detects conflicts between services.
    """

    def __init__(self, log: DeployLogger):
        self.log = log
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def parse_file(self, filepath: str) -> Optional[Dict]:
        """Parse a YAML config file and return validated config."""
        if not os.path.exists(filepath):
            self.log.error(f"Config file not found: {filepath}")
            return None

        self.log.step(f"Parsing configuration: {filepath}")

        try:
            with open(filepath, "r") as f:
                raw = yaml.safe_load(f)
        except yaml.YAMLError as e:
            self.log.error(f"YAML parse error: {e}")
            return None

        if not isinstance(raw, dict):
            self.log.error("Config must be a YAML dictionary at root level")
            return None

        return self._process_config(raw)

    def parse_string(self, yaml_string: str) -> Optional[Dict]:
        """Parse a YAML string and return validated config."""
        try:
            raw = yaml.safe_load(yaml_string)
        except yaml.YAMLError as e:
            self.log.error(f"YAML parse error: {e}")
            return None

        if not isinstance(raw, dict):
            self.log.error("Config must be a YAML dictionary at root level")
            return None

        return self._process_config(raw)

    # ── Internal Processing ────────────────────────────────────

    def _process_config(self, raw: Dict) -> Optional[Dict]:
        """Process raw config dict into validated config."""
        self.errors = []
        self.warnings = []

        # Handle both single service and multi-service configs
        services = []
        if "services" in raw:
            # Multi-service config
            if not isinstance(raw["services"], list):
                self.log.error("'services' must be a list")
                return None
            for i, svc in enumerate(raw["services"]):
                validated = self._validate_service(svc, index=i)
                if validated:
                    services.append(validated)
        else:
            # Single service config
            validated = self._validate_service(raw)
            if validated:
                services.append(validated)

        if not services:
            self.log.error("No valid services found in configuration")
            return None

        # Cross-service conflict detection
        self._detect_conflicts(services)

        if self.errors:
            for err in self.errors:
                self.log.error(err)
            return None

        for warn in self.warnings:
            self.log.warn(warn)

        # Return global config with services list
        config = {
            "global": raw.get("global", {}),
            "services": services,
        }

        self.log.success(f"Configuration valid: {len(services)} service(s)")
        return config

    def _validate_service(self, svc: Dict, index: int = 0) -> Optional[Dict]:
        """Validate a single service config and apply defaults."""
        if not isinstance(svc, dict):
            self.errors.append(f"Service #{index + 1}: must be a dictionary")
            return None

        # Check required fields
        for field in REQUIRED_FIELDS:
            if field not in svc or not svc[field]:
                self.errors.append(
                    f"Service #{index + 1}: missing required field '{field}'"
                )

        if self.errors:
            return None

        # Apply defaults
        config = dict(SERVICE_DEFAULTS)
        config.update(svc)

        # Normalize service_name
        config["service_name"] = self._sanitize_name(config["service_name"])

        # Auto-generate user if not specified
        if not config.get("user"):
            config["user"] = f"svc_{config['service_name']}"

        # Normalize deploy_path
        config["deploy_path"] = config["deploy_path"].rstrip("/")

        # Apply language-specific defaults (if language is specified)
        language = config.get("language")
        if language and language in LANGUAGE_DEFAULTS:
            lang_defaults = LANGUAGE_DEFAULTS[language]
            for key, value in lang_defaults.items():
                # Only apply language default if user didn't specify
                if key not in svc and value is not None:
                    config[key] = value

            # Map runtime_version → php_version for PHP backward compatibility
            if language == "php":
                if "runtime_version" in svc and "php_version" not in svc:
                    config["php_version"] = svc["runtime_version"]
                if "php_version" in svc and "runtime_version" not in svc:
                    config["runtime_version"] = svc["php_version"]

        # Build document_root
        suffix = config.get("document_root_suffix", "").strip("/")
        if suffix:
            config["document_root"] = f"{config['deploy_path']}/{suffix}"
        else:
            config["document_root"] = config["deploy_path"]

        # Build PHP-specific paths (only for PHP or when language not yet detected)
        if not language or language == "php":
            php_ver = config.get("php_version", config.get("runtime_version", "8.2"))
            config["fpm_socket"] = f"/run/php/php{php_ver}-fpm-{config['service_name']}.sock"
            config["fpm_pool_config"] = (
                f"/etc/php/{php_ver}/fpm/pool.d/{config['service_name']}.conf"
            )

        # Build systemd service name for non-PHP languages
        if language and language != "php":
            config["systemd_service"] = f"app-{config['service_name']}"

        # Validate individual fields
        self._validate_fields(config, index)

        # PHP-specific: Ensure fpm is in extensions
        if (not language or language == "php") and config.get("php_extensions"):
            if "fpm" not in config["php_extensions"]:
                config["php_extensions"].append("fpm")
            if "cli" not in config["php_extensions"]:
                config["php_extensions"].append("cli")

        return config

    def _validate_fields(self, config: Dict, index: int):
        """Validate individual field values."""
        svc_label = f"Service '{config.get('service_name', index + 1)}'"

        for field, rules in FIELD_VALIDATORS.items():
            if field not in config:
                continue

            value = config[field]

            # Type check
            expected_type = rules.get("type")
            if expected_type and not isinstance(value, expected_type):
                # Allow int for string types (YAML can parse "8.2" as float)
                if expected_type == str and isinstance(value, (int, float)):
                    config[field] = str(value)
                    value = config[field]
                else:
                    self.errors.append(
                        f"{svc_label}: '{field}' must be {expected_type.__name__}, got {type(value).__name__}"
                    )
                    continue

            # Pattern check
            pattern = rules.get("pattern")
            if pattern and isinstance(value, str):
                if not re.match(pattern, value):
                    self.errors.append(
                        f"{svc_label}: '{field}' value '{value}' is invalid. {rules.get('description', '')}"
                    )

            # Allowed values check
            allowed = rules.get("allowed")
            if allowed and value not in allowed:
                self.errors.append(
                    f"{svc_label}: '{field}' must be one of {allowed}, got '{value}'"
                )

            # Range check
            min_val = rules.get("min")
            max_val = rules.get("max")
            if isinstance(value, (int, float)):
                if min_val is not None and value < min_val:
                    self.errors.append(
                        f"{svc_label}: '{field}' must be >= {min_val}"
                    )
                if max_val is not None and value > max_val:
                    self.errors.append(
                        f"{svc_label}: '{field}' must be <= {max_val}"
                    )

        # SSL validation
        if config.get("enable_ssl"):
            if config.get("ssl_cert_path") and not os.path.exists(config["ssl_cert_path"]):
                self.warnings.append(
                    f"{svc_label}: SSL cert path does not exist yet: {config['ssl_cert_path']}"
                )

    def _detect_conflicts(self, services: List[Dict]):
        """Detect conflicts between services."""
        domains = {}
        deploy_paths = {}
        service_names = {}
        sockets = {}
        ports = {}

        for svc in services:
            name = svc["service_name"]
            domain = svc["domain"]
            path = svc["deploy_path"]
            language = svc.get("language") or "php"

            # Duplicate service names
            if name in service_names:
                self.errors.append(
                    f"Duplicate service_name: '{name}'"
                )
            service_names[name] = True

            # Duplicate domains (same web_server)
            domain_key = f"{domain}:{svc['web_server']}"
            if domain_key in domains:
                self.errors.append(
                    f"Duplicate domain '{domain}' for same web server '{svc['web_server']}'"
                )
            domains[domain_key] = name

            # Overlapping deploy paths
            if path in deploy_paths:
                self.errors.append(
                    f"Conflicting deploy_path '{path}' between "
                    f"'{deploy_paths[path]}' and '{name}'"
                )
            deploy_paths[path] = name

            # PHP: Socket conflicts
            socket = svc.get("fpm_socket")
            if socket:
                if socket in sockets:
                    self.errors.append(
                        f"Socket conflict: '{socket}' used by both "
                        f"'{sockets[socket]}' and '{name}'"
                    )
                sockets[socket] = name

            # Non-PHP: Port conflicts
            app_port = svc.get("app_port")
            if app_port and language != "php":
                if app_port in ports:
                    self.errors.append(
                        f"Port conflict: port {app_port} used by both "
                        f"'{ports[app_port]}' and '{name}'"
                    )
                ports[app_port] = name

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize a service name for use in file paths and configs."""
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower())
        return sanitized[:64]

    def is_valid(self) -> bool:
        return len(self.errors) == 0
