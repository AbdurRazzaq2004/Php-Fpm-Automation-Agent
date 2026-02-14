#!/usr/bin/env python3
"""
Universal Deployment Automation Agent - Main Orchestrator
==========================================================
Production-grade deployment engine for multi-language applications.

Supported Languages:
    PHP, Python, Node.js, Next.js, Ruby, Go, Java, Rust, .NET, Static Sites

Usage:
    sudo python3 deployer.py deploy                           # uses services.yml (default)
    sudo python3 deployer.py deploy   --config production.yml  # use a custom config
    sudo python3 deployer.py validate                          # validate default config
    sudo python3 deployer.py validate --config production.yml
    sudo python3 deployer.py status
    sudo python3 deployer.py rollback --service myapp --timestamp 20260214_120000

Architecture:
    YAML Config → Parser → Validator → Language Detection →
    Runtime Install → Dependencies → Build → Process Manager →
    Web Server Config → Permissions → Hooks → Validation → Done

    PHP path:     PHP-FPM pool → FastCGI socket → Web Server
    Non-PHP path: Runtime → Process Manager (systemd/PM2) → Reverse Proxy

Author: Universal Deployment Automation Agent
License: MIT
"""

import argparse
import os
import re
import platform
import subprocess
import sys
import traceback
from typing import Dict, List, Optional

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.parser import ConfigParser
from modules.logger import DeployLogger
from modules.system import SystemDetector
from modules.backup import BackupManager
from modules.packages import PackageInstaller
from modules.git import GitManager
from modules.phpfpm import PHPFPMManager
from modules.nginx import NginxConfigurator
from modules.apache import ApacheConfigurator
from modules.ssl import SSLManager
from modules.permissions import PermissionsManager
from modules.validation import ValidationEngine
from modules.hooks import HooksRunner
from modules.autodetect import AppAutoDetector
from modules.database import DatabaseManager
from modules.language_detect import detect_language
from modules.runtimes import get_runtime
from modules.process_manager import ProcessManager


class UniversalDeployer:
    """
    Main deployment orchestrator — multi-language support.

    Execution Flow:
    ───────────────────────────────────────────────
    1.  Parse & validate YAML configuration
    2.  Run pre-deployment system checks
    3.  Detect installed software
    4.  Create backups of existing configs
    5.  Install missing packages (idempotent)
    6.  Create service user (if needed)
    7.  Clone/update repository
    8.  ** AUTO-DETECT language (if not explicit) **
    9.  ** Language-specific setup **
        PHP:       Auto-detect PHP version/framework/extensions
                   Install PHP → Composer → FPM pool
        Non-PHP:   Runtime install → dependencies → build
                   Process manager (systemd / PM2)
    10. Detect & provision database
    11. Deploy environment file
    12. Run pre-deploy hooks
    13. Generate web server vhost
        PHP:    FastCGI proxy to FPM socket
        Others: HTTP reverse proxy to app_port
        Static: Direct file serving
    14. Set file permissions
    15. Setup SSL (if enabled)
    16. Reload services
    17. Run post-deploy hooks
    18. Post-deployment health checks
    19. Save backup manifest
    20. Print deployment summary
    ───────────────────────────────────────────────

    On failure: automatic rollback to backed-up state.
    """

    VERSION = "1.0.0"

    def __init__(self, verbose: bool = False, dry_run: bool = False):
        self.verbose = verbose
        self.dry_run = dry_run
        self.global_log = DeployLogger(service_name="orchestrator", verbose=verbose)

    # ── Deploy Command ──────────────────────────────────────────

    def deploy(self, config_path: str) -> bool:
        """
        Deploy all services defined in the configuration file.
        Each service is deployed independently with its own
        backup, rollback, and validation context.
        """
        self.global_log.banner(f"UNIVERSAL DEPLOYMENT AGENT v{self.VERSION}")
        self.global_log.info(f"Config: {config_path}")
        self.global_log.info(f"Mode:   {'DRY RUN' if self.dry_run else 'LIVE'}")
        self.global_log.divider()

        # ── Step 1: Parse configuration ─────────────────────────
        parser = ConfigParser(self.global_log)
        config = parser.parse_file(config_path)
        if not config:
            self.global_log.error("Configuration parsing failed — aborting")
            return False

        services = config["services"]
        self.global_log.info(f"Services to deploy: {len(services)}")
        for svc in services:
            lang = svc.get("language", "auto")
            self.global_log.info(
                f"  → {svc['service_name']} ({svc['domain']}) "
                f"[{lang}] / {svc['web_server']}"
            )
        self.global_log.divider()

        # ── Step 2: System detection ────────────────────────────
        system = SystemDetector(self.global_log)
        system.full_report()

        # ── Step 3: Deploy each service ─────────────────────────
        results = {}
        for svc_config in services:
            success = self._deploy_service(svc_config, system)
            results[svc_config["service_name"]] = success

        # ── Final Summary ───────────────────────────────────────
        self.global_log.banner("DEPLOYMENT RESULTS")
        all_success = True
        for name, success in results.items():
            status = "✓ SUCCESS" if success else "✗ FAILED"
            self.global_log.info(f"  {status}: {name}")
            if not success:
                all_success = False

        if all_success:
            self.global_log.success("All services deployed successfully")
        else:
            self.global_log.error("Some services failed to deploy")

        self.global_log.info(f"Logs: {self.global_log.get_log_path()}")
        return all_success

    def _deploy_service(self, config: Dict, system: SystemDetector) -> bool:
        """Deploy a single service with full lifecycle management."""
        service_name = config["service_name"]
        log = DeployLogger(service_name=service_name, verbose=self.verbose)
        backup = BackupManager(service_name, log)

        language = config.get("language", "php")

        log.banner(f"DEPLOYING: {service_name}")
        log.info(f"Domain:      {config['domain']}")
        log.info(f"Language:    {language}")
        if language == "php":
            log.info(f"PHP:         {config.get('php_version', 'auto')}")
        else:
            log.info(f"Runtime:     {config.get('runtime_version', 'latest')}")
        log.info(f"Web Server:  {config['web_server']}")
        log.info(f"Deploy Path: {config['deploy_path']}")
        log.info(f"Branch:      {config.get('branch', 'main')}")
        log.divider()

        try:
            # Instantiate modules
            installer = PackageInstaller(system, log)
            git = GitManager(log)
            perms = PermissionsManager(log)
            hooks = HooksRunner(log)
            validator = ValidationEngine(system, log)
            ssl_mgr = SSLManager(system, log)
            autodetect = AppAutoDetector(log)
            db_mgr = DatabaseManager(system, log)

            # PHP-FPM only needed for PHP
            fpm = PHPFPMManager(system, log) if language == "php" else None

            # Non-PHP runtimes
            runtime = None
            proc_mgr = None
            if language != "php":
                _os = system.detect_os()
                os_info = {
                    "distro": _os.get("distro", "unknown"),
                    "distro_version": _os.get("version", "unknown"),
                    "arch": platform.machine(),
                    "pkg_manager": system.detect_package_manager(),
                }
                runtime = get_runtime(language, log, os_info)
                proc_mgr = ProcessManager(log, os_info)

            web_server = config.get("web_server", "nginx")
            if web_server == "nginx":
                web_config = NginxConfigurator(system, log)
            else:
                web_config = ApacheConfigurator(system, log)

            # ── Pre-flight checks ───────────────────────────────
            if not validator.pre_deploy_checks(config):
                log.error("Pre-deployment checks failed")
                return False

            if self.dry_run:
                log.info("DRY RUN — skipping actual deployment")
                log.success("Dry run validation passed")
                return True

            # ── Install system utilities ────────────────────────
            if not installer.install_system_utilities():
                log.error("Failed to install system utilities")
                return self._handle_failure(backup, log)

            # ── Install web server (before PHP to avoid conflicts) ─
            if web_server == "nginx":
                if not installer.install_nginx():
                    log.error("Failed to install Nginx")
                    return self._handle_failure(backup, log)
            else:
                if not installer.install_apache():
                    log.error("Failed to install Apache")
                    return self._handle_failure(backup, log)

            # ── Create service user ─────────────────────────────
            if language == "php" and fpm:
                if not fpm.ensure_service_user(config):
                    log.error("Failed to create service user")
                    return self._handle_failure(backup, log)
            else:
                # For non-PHP: create user via standard useradd
                user = config.get("user", "root")
                if user != "root":
                    rc = subprocess.run(
                        f"id -u {user} 2>/dev/null || useradd --system --shell /usr/sbin/nologin --home {config['deploy_path']} {user}",
                        shell=True, capture_output=True
                    )
                    if rc.returncode == 0:
                        log.info(f"Service user ready: {user}")
                    else:
                        log.warn(f"User creation had issues — continuing")

            # ── Create deploy directory ─────────────────────────
            perms.create_deploy_directories(config)

            # ── Clone/update repository ─────────────────────────
            if not git.clone(config):
                log.error("Git clone/update failed")
                return self._handle_failure(backup, log)

            # ════════════════════════════════════════════════════
            #   LANGUAGE DETECTION & AUTO-DETECTION PHASE
            # ════════════════════════════════════════════════════

            # Auto-detect language if set to "auto" or not specified
            if language == "auto" or not language:
                detected_lang = detect_language(config["deploy_path"], log)
                log.info(f"Auto-detected language: {detected_lang}")
                config["language"] = detected_lang
                language = detected_lang

                # Re-instantiate runtime if language changed from auto
                if language != "php":
                    _os = system.detect_os()
                    os_info = {
                        "distro": _os.get("distro", "unknown"),
                        "distro_version": _os.get("version", "unknown"),
                        "arch": platform.machine(),
                        "pkg_manager": system.detect_package_manager(),
                    }
                    runtime = get_runtime(language, log, os_info)
                    proc_mgr = ProcessManager(log, os_info)

            log.banner("AUTO-DETECTING APP REQUIREMENTS")

            framework_info = {}
            db_driver = None
            db_credentials = {}

            if language == "php":
                # ── PHP-specific auto-detection ─────────────────
                detected_php = autodetect.detect_php_version(
                    config["deploy_path"], config.get("php_version")
                )
                if detected_php != config.get("php_version"):
                    log.info(
                        f"Adjusting PHP version: {config.get('php_version')} → {detected_php}"
                    )
                    config["php_version"] = detected_php
                    config["fpm_socket"] = (
                        f"/run/php/php{detected_php}-fpm-{service_name}.sock"
                    )
                    config["fpm_pool_config"] = (
                        f"/etc/php/{detected_php}/fpm/pool.d/{service_name}.conf"
                    )

                # Detect framework and requirements
                framework_info = autodetect.detect_framework(config["deploy_path"])
                log.info(f"Detected framework: {framework_info['name']}")

                # Auto-set document_root_suffix
                if not config.get("document_root_suffix") and framework_info.get("document_root_suffix"):
                    config["document_root_suffix"] = framework_info["document_root_suffix"]
                    config["document_root"] = (
                        f"{config['deploy_path']}{framework_info['document_root_suffix']}"
                    )
                    log.info(f"Auto-set document root: {config['document_root']}")

                # Merge auto-detected extensions
                composer_extensions = autodetect.detect_required_extensions(config["deploy_path"])
                framework_extensions = framework_info.get("extra_extensions", [])
                all_extensions = list(set(
                    config.get("php_extensions", []) +
                    composer_extensions +
                    framework_extensions
                ))
                config["php_extensions"] = all_extensions
                log.info(f"PHP extensions: {', '.join(sorted(all_extensions))}")

                # Detect database requirements from PHP framework
                db_driver = framework_info.get("database_driver")
                if not db_driver:
                    exts = config.get("php_extensions", [])
                    if "mysql" in exts or "pdo_mysql" in exts:
                        db_driver = "mysql"
                    elif "pgsql" in exts or "pdo_pgsql" in exts:
                        db_driver = "pgsql"
                    elif "sqlite3" in exts:
                        db_driver = "sqlite"

                if not db_driver:
                    for cmd in config.get("post_deploy_commands", []):
                        cmd_lower = cmd.lower()
                        if "mysql" in cmd_lower:
                            db_driver = "mysql"
                            break
                        elif "psql" in cmd_lower or "postgres" in cmd_lower:
                            db_driver = "pgsql"
                            break

                if db_driver:
                    log.info(f"Database requirement detected: {db_driver}")
                    db_extensions = db_mgr.get_required_php_extensions(db_driver)
                    for ext in db_extensions:
                        if ext not in config["php_extensions"]:
                            config["php_extensions"].append(ext)

                db_credentials = framework_info.get("database_credentials", {})

            else:
                # ── Non-PHP auto-detection ──────────────────────
                log.info(f"Running {language} runtime detection...")

                # Detect runtime version
                configured_ver = config.get("runtime_version")
                detected_ver = runtime.detect_version(config["deploy_path"], configured_ver)
                if detected_ver:
                    config["runtime_version"] = detected_ver
                    log.info(f"Runtime version: {detected_ver}")

                # Detect framework
                framework_info = runtime.detect_framework(config["deploy_path"])
                if framework_info.get("name"):
                    log.info(f"Detected framework: {framework_info['name']}")

                # Auto-set document root for static sites
                if language == "static":
                    doc_root = runtime.get_document_root(config["deploy_path"])
                    if doc_root:
                        config["document_root"] = doc_root
                        log.info(f"Auto-set document root: {doc_root}")

                # Detect database from runtime
                db_driver = framework_info.get("database_driver")
                db_credentials = framework_info.get("database_credentials", {})

                if db_driver:
                    log.info(f"Database requirement detected: {db_driver}")

            # Store framework info for later use
            config["_framework"] = framework_info
            config["_db_driver"] = db_driver

            log.divider()

            # ════════════════════════════════════════════════════
            #   LANGUAGE-SPECIFIC INSTALLATION
            # ════════════════════════════════════════════════════

            if language == "php":
                # ── Install PHP (with auto-detected version) ────
                if not installer.install_php(config["php_version"], config["php_extensions"]):
                    log.error("Failed to install PHP")
                    return self._handle_failure(backup, log)

                # ── Ensure Composer ─────────────────────────────
                if os.path.isfile(os.path.join(config["deploy_path"], "composer.json")):
                    if not db_mgr.ensure_composer(config["php_version"]):
                        log.warn("Composer installation had issues — continuing")

            else:
                # ── Install runtime ─────────────────────────────
                log.banner(f"INSTALLING {language.upper()} RUNTIME")
                version = config.get("runtime_version", "latest")
                if not runtime.install(version, config):
                    log.error(f"Failed to install {language} runtime")
                    return self._handle_failure(backup, log)

                # ── Install dependencies ────────────────────────
                log.step("Installing dependencies")
                if not runtime.install_dependencies(config):
                    log.warn("Dependency installation had issues — continuing")

                # ── Build application ───────────────────────────
                log.step("Building application")
                if not runtime.build(config):
                    log.warn("Build step had issues — continuing")

            # ════════════════════════════════════════════════════
            #   DATABASE PROVISIONING (all languages)
            # ════════════════════════════════════════════════════
            if db_driver:
                if not db_mgr.ensure_database(db_driver, config):
                    log.warn(f"Database setup for {db_driver} had issues — continuing")

                if db_credentials and db_credentials.get("dbname"):
                    db_mgr.provision_database(db_driver, db_credentials)

                # Auto-import SQL schema files if found
                sql_files = framework_info.get("sql_files", [])
                db_names = framework_info.get("database_names", [])

                target_db = (
                    db_credentials.get("dbname")
                    or self._detect_pgsql_dbname(config["deploy_path"])
                    or (db_names[0] if db_names else None)
                )

                if db_driver == "mysql" and (sql_files or db_names):
                    log.step("Auto-importing MySQL schema files")
                    for db_name in db_names:
                        if db_name != target_db:
                            log.info(f"Creating database: {db_name}")
                            subprocess.run(
                                f"mysql -u root -e 'CREATE DATABASE IF NOT EXISTS `{db_name}`;'",
                                shell=True, capture_output=True
                            )
                    import_db = target_db or (db_names[0] if db_names else None)
                    for sql_file in sql_files:
                        fname = os.path.basename(sql_file)
                        log.info(f"Importing SQL: {fname}")
                        sql_info = autodetect._extract_table_sql([sql_file])
                        file_target_db = None
                        if sql_info and sql_info[0].get("database"):
                            file_target_db = sql_info[0]["database"]
                        use_db = file_target_db or import_db
                        if use_db:
                            rc = subprocess.run(
                                f"grep -iv 'create database' '{sql_file}' | mysql -u root {use_db}",
                                shell=True, capture_output=True
                            )
                            if rc.returncode == 0:
                                log.success(f"  ✓ Imported {fname} → {use_db}")
                            else:
                                err_msg = rc.stderr.decode()[:200] if rc.stderr else "unknown"
                                if "already exists" in err_msg.lower():
                                    log.info(f"  ⊘ {fname}: tables already exist — skipped")
                                else:
                                    log.warn(f"  SQL import had issues: {err_msg}")
                        else:
                            log.warn(f"  No target database for {fname} — skipped")

                elif db_driver in ("pgsql", "postgres") and sql_files:
                    log.step("Auto-importing PostgreSQL schema files")
                    for sql_file in sql_files:
                        fname = os.path.basename(sql_file)
                        log.info(f"Importing SQL: {fname}")
                        if target_db:
                            rc = subprocess.run(
                                f"cat '{sql_file}' | sudo -u postgres psql -d {target_db}",
                                shell=True, capture_output=True
                            )
                        else:
                            rc = subprocess.run(
                                f"cat '{sql_file}' | sudo -u postgres psql",
                                shell=True, capture_output=True
                            )
                        if rc.returncode == 0:
                            log.success(f"  ✓ Imported {fname}" + (f" → {target_db}" if target_db else ""))
                        else:
                            err_msg = rc.stderr.decode()[:200] if rc.stderr else "unknown"
                            if "already exists" in err_msg.lower():
                                log.info(f"  ⊘ {fname}: tables already exist — skipped")
                            else:
                                log.warn(f"  SQL import had issues: {err_msg}")

                # Store credentials for env var injection
                config["_db_credentials"] = db_credentials

            # ── Deploy environment file ─────────────────────────
            hooks.setup_environment_file(config)

            # ── Pre-deploy hooks ────────────────────────────────
            if not hooks.run_pre_deploy(config):
                log.warn("Pre-deploy hooks had failures")
                # Continue — hooks are advisory by default

            # ── Create PHP-FPM pool (PHP only) ──────────────────
            if language == "php" and fpm:
                if not fpm.create_pool(config, backup):
                    log.error("Failed to create PHP-FPM pool")
                    return self._handle_failure(backup, log)

            # ── Setup process manager (non-PHP) ─────────────────
            if language != "php" and language != "static" and proc_mgr:
                if not proc_mgr.setup_service(config, runtime):
                    log.error("Failed to setup process manager")
                    return self._handle_failure(backup, log)

            # ── Generate web server config ──────────────────────
            if not web_config.generate_vhost(config, backup):
                log.error("Failed to generate web server config")
                return self._handle_failure(backup, log)

            # ── Set permissions ─────────────────────────────────
            if not perms.setup_permissions(config):
                log.error("Failed to set permissions")
                return self._handle_failure(backup, log)

            # ── Setup SSL ───────────────────────────────────────
            if config.get("enable_ssl"):
                ssl_mgr.setup_ssl(config)
                # SSL failure is non-fatal — service works on HTTP

            # ── Reload PHP-FPM (PHP only) ───────────────────────
            if language == "php" and fpm:
                if not fpm.reload_fpm(config):
                    log.error("PHP-FPM reload failed")
                    return self._handle_failure(backup, log)

            # ── Reload web server ───────────────────────────────
            if not web_config.safe_reload():
                log.error("Web server reload failed")
                return self._handle_failure(backup, log)

            # ── Smart Post-Deploy (framework-aware) ─────────
            framework = config.get("_framework", {})
            framework_name = framework.get("name", "generic")

            # If user has custom post_deploy_commands, use those;
            # otherwise, use auto-detected framework commands
            user_commands = config.get("post_deploy_commands", [])
            if not user_commands and framework.get("post_deploy_commands"):
                log.info(
                    f"Using auto-detected {framework_name} post-deploy commands"
                )
                config["post_deploy_commands"] = framework["post_deploy_commands"]

            hooks.run_composer_install(config) if language == "php" else None
            if not hooks.run_post_deploy(config):
                log.warn("Post-deploy hooks had failures")

            # ── Setup cron jobs ─────────────────────────────────
            hooks.setup_cron_jobs(config)

            # ── Fix framework-specific permissions ──────────────
            writable_dirs = framework.get("writable_dirs", [])
            if writable_dirs:
                log.step(f"Setting framework writable directories ({framework_name})")
                deploy_path = config["deploy_path"]
                user = config.get("user", "root")
                group = config.get("group", "www-data")
                for wdir in writable_dirs:
                    full_path = os.path.join(deploy_path, wdir)
                    if os.path.isdir(full_path):
                        import subprocess
                        subprocess.run(
                            f"chmod -R 775 '{full_path}'",
                            shell=True, capture_output=True
                        )
                        subprocess.run(
                            f"chown -R {user}:{group} '{full_path}'",
                            shell=True, capture_output=True
                        )
                        log.info(f"  ✓ {wdir} → writable (775)")
                    else:
                        log.debug(f"  Writable dir not found: {wdir}")

            # ── Fix permissions again after hooks ───────────────
            perms.setup_permissions(config)

            # ── Post-deployment validation ──────────────────────
            validator.post_deploy_checks(config)

            # ── Save backup manifest ────────────────────────────
            backup.save_manifest()
            backup.cleanup_old_backups()

            # ── Success ─────────────────────────────────────────
            log.summary()
            return not log.has_errors()

        except Exception as e:
            log.critical(f"Unexpected error: {e}")
            log.debug(traceback.format_exc())
            return self._handle_failure(backup, log)

    def _detect_pgsql_dbname(self, deploy_path: str) -> Optional[str]:
        """
        Detect PostgreSQL database name from config files.

        Searches for common patterns:
        - DSN: pgsql:...;dbname=xxx
        - Config arrays: 'dbname' => 'xxx'
        - Environment: DB_NAME=xxx
        """
        import glob as _glob

        scan_files = []
        config_dir = os.path.join(deploy_path, "config")
        if os.path.isdir(config_dir):
            scan_files.extend(_glob.glob(os.path.join(config_dir, "*.php")))

        for d in ["src", "app"]:
            d_path = os.path.join(deploy_path, d)
            if os.path.isdir(d_path):
                for root, dirs, files in os.walk(d_path):
                    for f in files:
                        if f.endswith(".php") and any(kw in f.lower() for kw in ["database", "db", "connection"]):
                            scan_files.append(os.path.join(root, f))
                    depth = root.replace(d_path, "").count(os.sep)
                    if depth >= 3:
                        dirs.clear()

        for env_name in [".env", ".env.production"]:
            env_path = os.path.join(deploy_path, env_name)
            if os.path.isfile(env_path):
                scan_files.append(env_path)

        for fpath in scan_files:
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue

            # pgsql:...;dbname=xxx
            m = re.search(r'dbname[=:]\s*[\'"]?(\w+)', content)
            if m:
                return m.group(1)

            # DB_NAME=xxx
            m = re.search(r'DB_NAME\s*=\s*[\'"]?(\w+)', content)
            if m:
                return m.group(1)

        return None

    def _handle_failure(self, backup: BackupManager, log: DeployLogger) -> bool:
        """Handle deployment failure with rollback."""
        log.banner("DEPLOYMENT FAILED — INITIATING ROLLBACK")
        try:
            backup.rollback()
            log.info("Rollback completed — previous state restored")
        except Exception as e:
            log.critical(f"Rollback also failed: {e}")
        log.summary()
        return False

    # ── Validate Command ────────────────────────────────────────

    def validate(self, config_path: str) -> bool:
        """Validate configuration without deploying."""
        self.global_log.banner("CONFIGURATION VALIDATION")

        parser = ConfigParser(self.global_log)
        config = parser.parse_file(config_path)
        if not config:
            self.global_log.error("Configuration is INVALID")
            return False

        self.global_log.success("Configuration is VALID")

        # Show parsed config summary
        for svc in config["services"]:
            lang = svc.get("language", "php")
            self.global_log.info(f"\nService: {svc['service_name']}")
            self.global_log.info(f"  Language:     {lang}")
            self.global_log.info(f"  Domain:       {svc['domain']}")
            if lang == "php":
                self.global_log.info(f"  PHP:          {svc.get('php_version', 'auto')}")
                self.global_log.info(f"  FPM Socket:   {svc.get('fpm_socket', 'N/A')}")
                self.global_log.info(f"  FPM Pool:     {svc.get('fpm_pool_config', 'N/A')}")
                self.global_log.info(f"  Extensions:   {', '.join(svc.get('php_extensions', []))}")
            else:
                self.global_log.info(f"  Runtime:      {svc.get('runtime_version', 'latest')}")
                self.global_log.info(f"  App Port:     {svc.get('app_port', 'N/A')}")
                self.global_log.info(f"  Process Mgr:  {svc.get('process_manager', 'systemd')}")
            self.global_log.info(f"  Web Server:   {svc['web_server']}")
            self.global_log.info(f"  Deploy Path:  {svc['deploy_path']}")
            self.global_log.info(f"  User:         {svc['user']}")
            self.global_log.info(f"  SSL:          {svc.get('enable_ssl', False)}")
            self.global_log.info(f"  Doc Root:     {svc['document_root']}")

        return True

    # ── Status Command ──────────────────────────────────────────

    def status(self, config_path: str) -> bool:
        """Show status of all services in the configuration."""
        self.global_log.banner("SERVICE STATUS")

        parser = ConfigParser(self.global_log)
        config = parser.parse_file(config_path)
        if not config:
            return False

        system = SystemDetector(self.global_log)

        for svc in config["services"]:
            name = svc["service_name"]
            lang = svc.get("language", "php")
            self.global_log.divider()
            self.global_log.info(f"Service: {name} [{lang}]")

            # Check deployment
            deploy_path = svc["deploy_path"]
            deployed = os.path.isdir(deploy_path) and os.listdir(deploy_path)
            self.global_log.info(f"  Deployed:     {'YES' if deployed else 'NO'}")

            # Language-specific process checks
            if lang == "php":
                # Check PHP-FPM
                fpm_running = system.is_php_fpm_running(svc.get("php_version", "8.2"))
                self.global_log.info(f"  PHP-FPM:      {'RUNNING' if fpm_running else 'STOPPED'}")

                # Check socket
                socket_path = svc.get("fpm_socket", "")
                if socket_path:
                    socket_exists = os.path.exists(socket_path)
                    self.global_log.info(f"  FPM Socket:   {'EXISTS' if socket_exists else 'MISSING'}")

                # Check pool config
                pool_path = svc.get("fpm_pool_config", "")
                if pool_path:
                    pool_exists = os.path.isfile(pool_path)
                    self.global_log.info(f"  Pool Config:  {'EXISTS' if pool_exists else 'MISSING'}")

            elif lang == "static":
                self.global_log.info(f"  Process:      N/A (static site)")

            else:
                # Check systemd/PM2 service
                svc_name = svc.get("systemd_service") or name
                proc_manager = svc.get("process_manager", "systemd")
                if proc_manager == "pm2" or lang in ("node", "nextjs"):
                    # Check PM2
                    rc = subprocess.run(
                        f"pm2 show {name} 2>/dev/null | grep status",
                        shell=True, capture_output=True, text=True
                    )
                    if rc.returncode == 0 and "online" in rc.stdout.lower():
                        self.global_log.info(f"  PM2:          RUNNING")
                    else:
                        self.global_log.info(f"  PM2:          STOPPED")
                else:
                    # Check systemd
                    rc = subprocess.run(
                        f"systemctl is-active {svc_name}.service 2>/dev/null",
                        shell=True, capture_output=True, text=True
                    )
                    status_str = rc.stdout.strip()
                    self.global_log.info(f"  Systemd:      {status_str.upper() if status_str else 'UNKNOWN'}")

                # Check app port
                app_port = svc.get("app_port")
                if app_port:
                    rc = subprocess.run(
                        f"ss -tlnp | grep :{app_port}",
                        shell=True, capture_output=True, text=True
                    )
                    port_listening = rc.returncode == 0 and str(app_port) in rc.stdout
                    self.global_log.info(f"  Port {app_port}:     {'LISTENING' if port_listening else 'NOT LISTENING'}")

            # Check web server
            web = svc.get("web_server", "nginx")
            if web == "nginx":
                running = system.is_nginx_running()
            else:
                running = system.is_apache_running()
            self.global_log.info(f"  {web.title()}:      {'RUNNING' if running else 'STOPPED'}")

            # Check vhost
            vhost_found = False
            for path in [
                f"/etc/nginx/sites-enabled/{name}.conf",
                f"/etc/nginx/conf.d/{name}.conf",
                f"/etc/apache2/sites-enabled/{name}.conf",
                f"/etc/httpd/conf.d/{name}.conf",
            ]:
                if os.path.exists(path):
                    vhost_found = True
                    break
            self.global_log.info(f"  VHost Config: {'EXISTS' if vhost_found else 'MISSING'}")

            # Check backups
            backup_mgr = BackupManager(name, self.global_log)
            backups = backup_mgr.list_backups()
            self.global_log.info(f"  Backups:      {len(backups)}")

            # Git info
            if deployed:
                git = GitManager(self.global_log)
                commit = git.get_current_commit(deploy_path)
                tag = git.get_latest_tag(deploy_path)
                self.global_log.info(f"  Git Commit:   {commit or 'N/A'}")
                self.global_log.info(f"  Git Tag:      {tag or 'N/A'}")

        return True

    # ── Rollback Command ────────────────────────────────────────

    def rollback(self, service_name: str, timestamp: Optional[str] = None) -> bool:
        """Rollback a service to a previous backup."""
        self.global_log.banner(f"ROLLBACK: {service_name}")

        backup_mgr = BackupManager(service_name, self.global_log)
        backups = backup_mgr.list_backups()

        if not backups:
            self.global_log.error(f"No backups found for service: {service_name}")
            return False

        if timestamp:
            # Use specific backup
            manifest = backup_mgr.load_manifest(timestamp)
            if not manifest:
                self.global_log.error(f"Backup not found: {timestamp}")
                return False
        else:
            # Use latest backup
            latest = backups[-1]
            timestamp = latest["timestamp"]
            manifest = backup_mgr.load_manifest(timestamp)
            if not manifest:
                self.global_log.error("Could not load latest backup manifest")
                return False

        self.global_log.info(f"Rolling back to: {timestamp}")
        backup_mgr.manifest = manifest
        success = backup_mgr.rollback()

        if success:
            # Reload services
            system = SystemDetector(self.global_log)
            # Try reloading nginx
            if system.is_nginx_installed():
                ok, _ = system.nginx_config_test()
                if ok:
                    subprocess.run("systemctl reload nginx", shell=True)
            # Try reloading apache
            if system.is_apache_installed():
                ok, _ = system.apache_config_test()
                if ok:
                    cmd = system.get_apache_command()
                    subprocess.run(f"systemctl reload {cmd}", shell=True)

            self.global_log.success("Rollback completed")
        else:
            self.global_log.error("Rollback had errors")

        return success


# ── CLI Entry Point ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="universal-deployer",
        description="Universal Deployment Agent - Multi-Language Production Deployment Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supported Languages: PHP, Python, Node.js, Next.js, Ruby, Go, Java, Rust, .NET, Static

Examples:
  # Deploy services from config
  sudo python3 deployer.py deploy --config services.yml

  # Validate config without deploying
  sudo python3 deployer.py validate --config services.yml

  # Dry run (validate + pre-checks, no changes)
  sudo python3 deployer.py deploy --config services.yml --dry-run

  # Check status of deployed services
  sudo python3 deployer.py status --config services.yml

  # Rollback to latest backup
  sudo python3 deployer.py rollback --service myapp

  # Rollback to specific backup
  sudo python3 deployer.py rollback --service myapp --timestamp 20260214_120000
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Default config file path (services.yml in same directory as deployer.py)
    default_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "services.yml")

    # Deploy command
    deploy_parser = subparsers.add_parser("deploy", help="Deploy services")
    deploy_parser.add_argument("--config", "-c", default=default_config, help="YAML config file (default: services.yml)")
    deploy_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    deploy_parser.add_argument("--dry-run", action="store_true", help="Validate without deploying")

    # Validate command
    validate_parser = subparsers.add_parser("validate", help="Validate configuration")
    validate_parser.add_argument("--config", "-c", default=default_config, help="YAML config file (default: services.yml)")
    validate_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show service status")
    status_parser.add_argument("--config", "-c", default=default_config, help="YAML config file (default: services.yml)")
    status_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # Rollback command
    rollback_parser = subparsers.add_parser("rollback", help="Rollback a service")
    rollback_parser.add_argument("--service", "-s", required=True, help="Service name")
    rollback_parser.add_argument("--timestamp", "-t", help="Backup timestamp")
    rollback_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Check root
    if os.geteuid() != 0 and args.command in ("deploy", "rollback"):
        print("\033[31mError: This tool must be run as root (use sudo)\033[0m")
        sys.exit(1)

    verbose = getattr(args, "verbose", False)
    dry_run = getattr(args, "dry_run", False)

    deployer = UniversalDeployer(verbose=verbose, dry_run=dry_run)

    if args.command == "deploy":
        success = deployer.deploy(args.config)
    elif args.command == "validate":
        success = deployer.validate(args.config)
    elif args.command == "status":
        success = deployer.status(args.config)
    elif args.command == "rollback":
        success = deployer.rollback(args.service, args.timestamp)
    else:
        parser.print_help()
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
