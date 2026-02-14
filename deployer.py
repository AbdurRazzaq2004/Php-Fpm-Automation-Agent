#!/usr/bin/env python3
"""
PHP-FPM Automation Agent - Main Orchestrator
==============================================
Production-grade deployment engine for PHP applications.

Usage:
    sudo python3 deployer.py deploy                           # uses services.yml (default)
    sudo python3 deployer.py deploy   --config production.yml  # use a custom config
    sudo python3 deployer.py validate                          # validate default config
    sudo python3 deployer.py validate --config production.yml
    sudo python3 deployer.py status
    sudo python3 deployer.py rollback --service myapp --timestamp 20260214_120000

Architecture:
    YAML Config → Parser → Validator → Detector → Installer →
    Git Clone → PHP-FPM Pool → Web Server Config → Permissions →
    Hooks → Validation → Done

Author: PHP-FPM Automation Agent
License: MIT
"""

import argparse
import os
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


class PHPDeployer:
    """
    Main deployment orchestrator.

    Execution Flow:
    ───────────────────────────────────────────────
    1.  Parse & validate YAML configuration
    2.  Run pre-deployment system checks
    3.  Detect installed software
    4.  Create backups of existing configs
    5.  Install missing packages (idempotent)
    6.  Create service user (if needed)
    7.  Clone/update repository
    8.  ** AUTO-DETECT app requirements **
        - PHP version from composer.json
        - Framework type (Laravel, Symfony, etc.)
        - Database driver requirements
        - Required PHP extensions
    9.  ** Install correct PHP version (auto-detected) **
    10. ** Ensure Composer (latest from getcomposer.org) **
    11. ** Ensure database engine (safe: never overwrite) **
    12. Deploy environment file
    13. Run pre-deploy hooks
    14. Create PHP-FPM pool configuration
    15. Generate web server vhost
    16. Set file permissions
    17. Setup SSL (if enabled)
    18. Validate all configurations
    19. Reload PHP-FPM (safe)
    20. Reload web server (safe: test → reload)
    21. Run post-deploy hooks (composer install, etc.)
    22. Fix framework-specific permissions
    23. Post-deployment health checks
    24. Save backup manifest
    25. Print deployment summary
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
        self.global_log.banner(f"PHP-FPM AUTOMATION AGENT v{self.VERSION}")
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
            self.global_log.info(
                f"  → {svc['service_name']} ({svc['domain']}) "
                f"PHP {svc['php_version']} / {svc['web_server']}"
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

        log.banner(f"DEPLOYING: {service_name}")
        log.info(f"Domain:      {config['domain']}")
        log.info(f"PHP:         {config['php_version']}")
        log.info(f"Web Server:  {config['web_server']}")
        log.info(f"Deploy Path: {config['deploy_path']}")
        log.info(f"Branch:      {config.get('branch', 'main')}")
        log.divider()

        try:
            # Instantiate modules
            installer = PackageInstaller(system, log)
            git = GitManager(log)
            fpm = PHPFPMManager(system, log)
            perms = PermissionsManager(log)
            hooks = HooksRunner(log)
            validator = ValidationEngine(system, log)
            ssl_mgr = SSLManager(system, log)
            autodetect = AppAutoDetector(log)
            db_mgr = DatabaseManager(system, log)

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
            if not fpm.ensure_service_user(config):
                log.error("Failed to create service user")
                return self._handle_failure(backup, log)

            # ── Create deploy directory ─────────────────────────
            perms.create_deploy_directories(config)

            # ── Clone/update repository ─────────────────────────
            if not git.clone(config):
                log.error("Git clone/update failed")
                return self._handle_failure(backup, log)

            # ════════════════════════════════════════════════════
            #   AUTO-DETECTION PHASE
            # ════════════════════════════════════════════════════
            log.banner("AUTO-DETECTING APP REQUIREMENTS")

            # Detect PHP version from composer.json
            detected_php = autodetect.detect_php_version(
                config["deploy_path"], config.get("php_version")
            )
            if detected_php != config.get("php_version"):
                log.info(
                    f"Adjusting PHP version: {config.get('php_version')} → {detected_php}"
                )
                config["php_version"] = detected_php
                # Update derived paths
                config["fpm_socket"] = (
                    f"/run/php/php{detected_php}-fpm-{service_name}.sock"
                )
                config["fpm_pool_config"] = (
                    f"/etc/php/{detected_php}/fpm/pool.d/{service_name}.conf"
                )

            # Detect framework and requirements
            framework_info = autodetect.detect_framework(config["deploy_path"])
            log.info(f"Detected framework: {framework_info['name']}")

            # Auto-set document_root_suffix if not explicitly configured
            if not config.get("document_root_suffix") and framework_info.get("document_root_suffix"):
                config["document_root_suffix"] = framework_info["document_root_suffix"]
                config["document_root"] = (
                    f"{config['deploy_path']}{framework_info['document_root_suffix']}"
                )
                log.info(f"Auto-set document root: {config['document_root']}")

            # Merge auto-detected extensions with configured ones
            composer_extensions = autodetect.detect_required_extensions(config["deploy_path"])
            framework_extensions = framework_info.get("extra_extensions", [])
            all_extensions = list(set(
                config.get("php_extensions", []) +
                composer_extensions +
                framework_extensions
            ))
            config["php_extensions"] = all_extensions
            log.info(f"PHP extensions: {', '.join(sorted(all_extensions))}")

            # Detect database requirements
            db_driver = framework_info.get("database_driver")
            if db_driver:
                log.info(f"Database requirement detected: {db_driver}")
                # Add database PHP extensions
                db_extensions = db_mgr.get_required_php_extensions(db_driver)
                for ext in db_extensions:
                    if ext not in config["php_extensions"]:
                        config["php_extensions"].append(ext)

            # Store framework info for later use
            config["_framework"] = framework_info
            config["_db_driver"] = db_driver

            log.divider()

            # ── Install PHP (with auto-detected version) ────────
            if not installer.install_php(config["php_version"], config["php_extensions"]):
                log.error("Failed to install PHP")
                return self._handle_failure(backup, log)

            # ── Ensure Composer (latest, from getcomposer.org) ──
            if os.path.isfile(os.path.join(config["deploy_path"], "composer.json")):
                if not db_mgr.ensure_composer(config["php_version"]):
                    log.warn("Composer installation had issues — continuing")

            # ── Ensure Database ──────────────────────────────────
            if db_driver:
                if not db_mgr.ensure_database(db_driver, config):
                    log.warn(f"Database setup for {db_driver} had issues — continuing")

            # ── Deploy environment file ─────────────────────────
            hooks.setup_environment_file(config)

            # ── Pre-deploy hooks ────────────────────────────────
            if not hooks.run_pre_deploy(config):
                log.warn("Pre-deploy hooks had failures")
                # Continue — hooks are advisory by default

            # ── Create PHP-FPM pool ─────────────────────────────
            if not fpm.create_pool(config, backup):
                log.error("Failed to create PHP-FPM pool")
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

            # ── Reload PHP-FPM ──────────────────────────────────
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

            hooks.run_composer_install(config)
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
            self.global_log.info(f"\nService: {svc['service_name']}")
            self.global_log.info(f"  Domain:       {svc['domain']}")
            self.global_log.info(f"  PHP:          {svc['php_version']}")
            self.global_log.info(f"  Web Server:   {svc['web_server']}")
            self.global_log.info(f"  Deploy Path:  {svc['deploy_path']}")
            self.global_log.info(f"  FPM Socket:   {svc['fpm_socket']}")
            self.global_log.info(f"  FPM Pool:     {svc['fpm_pool_config']}")
            self.global_log.info(f"  User:         {svc['user']}")
            self.global_log.info(f"  SSL:          {svc.get('enable_ssl', False)}")
            self.global_log.info(f"  Extensions:   {', '.join(svc['php_extensions'])}")
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
            self.global_log.divider()
            self.global_log.info(f"Service: {name}")

            # Check deployment
            deploy_path = svc["deploy_path"]
            deployed = os.path.isdir(deploy_path) and os.listdir(deploy_path)
            self.global_log.info(f"  Deployed:     {'YES' if deployed else 'NO'}")

            # Check PHP-FPM
            fpm_running = system.is_php_fpm_running(svc["php_version"])
            self.global_log.info(f"  PHP-FPM:      {'RUNNING' if fpm_running else 'STOPPED'}")

            # Check socket
            socket_exists = os.path.exists(svc["fpm_socket"])
            self.global_log.info(f"  FPM Socket:   {'EXISTS' if socket_exists else 'MISSING'}")

            # Check pool config
            pool_exists = os.path.isfile(svc["fpm_pool_config"])
            self.global_log.info(f"  Pool Config:  {'EXISTS' if pool_exists else 'MISSING'}")

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
        prog="php-deployer",
        description="PHP-FPM Automation Agent - Production Deployment Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
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

    deployer = PHPDeployer(verbose=verbose, dry_run=dry_run)

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
