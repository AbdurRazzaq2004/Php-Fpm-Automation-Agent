"""
Validation Engine - PHP-FPM Automation Agent
===============================================
Pre-flight and post-flight checks to ensure
deployment safety and correctness.
"""

import os
import subprocess
import socket
from typing import Dict, List, Tuple

from modules.logger import DeployLogger
from modules.system import SystemDetector


class ValidationEngine:
    """
    Performs comprehensive validation:
    - Pre-deployment checks (config, system state, conflicts)
    - Post-deployment verification (services, connectivity)
    - Health checks
    """

    def __init__(self, system: SystemDetector, log: DeployLogger):
        self.system = system
        self.log = log
        self.checks_passed = 0
        self.checks_failed = 0
        self.checks_warned = 0

    def _run(self, cmd: str) -> Tuple[int, str, str]:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def _pass(self, msg: str):
        self.checks_passed += 1
        self.log.info(f"  ✓ PASS: {msg}")

    def _fail(self, msg: str):
        self.checks_failed += 1
        self.log.error(f"  ✗ FAIL: {msg}")

    def _warn(self, msg: str):
        self.checks_warned += 1
        self.log.warn(f"  ⚠ WARN: {msg}")

    # ── Pre-Deployment Validation ───────────────────────────────

    def pre_deploy_checks(self, config: Dict) -> bool:
        """
        Run all pre-deployment checks.
        Returns True if deployment can proceed.
        """
        self.log.banner("PRE-DEPLOYMENT VALIDATION")
        self.checks_passed = 0
        self.checks_failed = 0
        self.checks_warned = 0

        service_name = config["service_name"]
        web_server = config.get("web_server", "nginx")

        # 1. Check root privileges
        if os.geteuid() != 0:
            self._fail("Must run as root (use sudo)")
        else:
            self._pass("Running as root")

        # 2. Check disk space
        self._check_disk_space(config["deploy_path"])

        # 3. Check if deploy path parent exists
        parent = os.path.dirname(config["deploy_path"])
        if os.path.isdir(parent):
            self._pass(f"Parent directory exists: {parent}")
        else:
            self._warn(f"Parent directory will be created: {parent}")

        # 4. Check deploy path conflicts
        if os.path.isdir(config["deploy_path"]):
            self._warn(f"Deploy path already exists: {config['deploy_path']}")
        else:
            self._pass(f"Deploy path is clear: {config['deploy_path']}")

        # 5. Check socket conflicts (PHP only)
        language = config.get("language", "php")
        if language == "php":
            socket_path = config.get("fpm_socket", "")
            existing_sockets = self.system.get_existing_fpm_sockets()
            if socket_path in existing_sockets:
                self._warn(f"FPM socket already exists (will be replaced): {socket_path}")
            else:
                self._pass(f"No socket conflict: {socket_path}")
        else:
            self._pass(f"Non-PHP runtime ({language}) — FPM socket check skipped")

        # 6. Check domain DNS (optional, non-blocking)
        self._check_dns(config["domain"])

        # 7. Check web server port availability
        if web_server == "nginx":
            self._check_web_server_port("nginx", 80)
        else:
            self._check_web_server_port("apache", 80)

        # 8. Check for vhost conflicts
        self._check_vhost_conflicts(config)

        # Summary
        self.log.divider()
        total = self.checks_passed + self.checks_failed + self.checks_warned
        self.log.info(
            f"Pre-flight: {self.checks_passed}/{total} passed, "
            f"{self.checks_failed} failed, {self.checks_warned} warnings"
        )

        if self.checks_failed > 0:
            self.log.error("Pre-deployment validation FAILED — fix issues above")
            return False

        self.log.success("Pre-deployment validation PASSED")
        return True

    # ── Post-Deployment Validation ──────────────────────────────

    def post_deploy_checks(self, config: Dict) -> bool:
        """
        Run post-deployment verification.
        """
        self.log.banner("POST-DEPLOYMENT VALIDATION")
        self.checks_passed = 0
        self.checks_failed = 0
        self.checks_warned = 0

        service_name = config["service_name"]
        web_server = config.get("web_server", "nginx")
        language = config.get("language", "php")

        # 1. Check deploy path exists and has files
        deploy_path = config["deploy_path"]
        if os.path.isdir(deploy_path) and os.listdir(deploy_path):
            self._pass(f"Code deployed to: {deploy_path}")
        else:
            self._fail(f"Deploy path empty or missing: {deploy_path}")

        if language == "php":
            php_version = config.get("php_version", "8.2")

            # 2. Check index.php exists (in document_root)
            doc_root = config["document_root"]
            index_file = os.path.join(doc_root, "index.php")
            if os.path.isfile(index_file):
                self._pass(f"index.php exists in document root")
            else:
                self._warn(f"index.php not found in {doc_root}")

            # 3. Check PHP-FPM pool config
            pool_config = config.get("fpm_pool_config", "")
            if pool_config and os.path.isfile(pool_config):
                self._pass(f"FPM pool config exists: {pool_config}")
            else:
                self._fail(f"FPM pool config missing: {pool_config}")

            # 4. Check PHP-FPM is running
            fpm_service = self.system.get_php_fpm_service_name(php_version)
            if self.system.is_php_fpm_running(php_version):
                self._pass(f"{fpm_service} is running")
            else:
                self._fail(f"{fpm_service} is NOT running")

            # 5. Check FPM socket exists
            socket_path = config.get("fpm_socket", "")
            if socket_path and os.path.exists(socket_path):
                self._pass(f"FPM socket exists: {socket_path}")
            else:
                self._fail(f"FPM socket missing: {socket_path}")
        else:
            # Non-PHP: check for app entry point and process running
            self._pass(f"Non-PHP runtime ({language}) — FPM checks skipped")

            # Check if systemd service is active (for non-static apps)
            if language != "static":
                rc, out, _ = self._run(f"systemctl is-active {service_name} 2>/dev/null")
                if rc == 0 and "active" in out:
                    self._pass(f"Service {service_name} is active")
                else:
                    self._warn(f"Service {service_name} may not be running yet")

        # 6. Check web server config
        if web_server == "nginx":
            self._check_nginx_config(config)
        else:
            self._check_apache_config(config)

        # 7. Check web server is running
        if web_server == "nginx":
            if self.system.is_nginx_running():
                self._pass("Nginx is running")
            else:
                self._fail("Nginx is NOT running")
        else:
            if self.system.is_apache_running():
                self._pass("Apache is running")
            else:
                self._fail("Apache is NOT running")

        # 8. Check file permissions
        self._check_permissions(config)

        # 9. HTTP health check
        self._http_health_check(config)

        # Summary
        self.log.divider()
        total = self.checks_passed + self.checks_failed + self.checks_warned
        self.log.info(
            f"Post-deploy: {self.checks_passed}/{total} passed, "
            f"{self.checks_failed} failed, {self.checks_warned} warnings"
        )

        if self.checks_failed > 0:
            self.log.warn("Some post-deployment checks failed — review above")
            return False

        self.log.success("Post-deployment validation PASSED")
        return True

    # ── Individual Checks ───────────────────────────────────────

    def _check_disk_space(self, path: str):
        """Check available disk space."""
        try:
            parent = os.path.dirname(path)
            while not os.path.exists(parent) and parent != "/":
                parent = os.path.dirname(parent)
            stat = os.statvfs(parent)
            free_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            if free_gb < 1:
                self._fail(f"Low disk space: {free_gb:.1f} GB free")
            elif free_gb < 5:
                self._warn(f"Disk space low: {free_gb:.1f} GB free")
            else:
                self._pass(f"Disk space OK: {free_gb:.1f} GB free")
        except Exception:
            self._warn("Could not check disk space")

    def _check_dns(self, domain: str):
        """Check if domain resolves."""
        try:
            addr = socket.gethostbyname(domain)
            self._pass(f"DNS resolves: {domain} → {addr}")
        except socket.gaierror:
            self._warn(f"DNS not resolving for {domain} (may be configured later)")

    def _check_web_server_port(self, server: str, port: int):
        """Check if web server port is available or owned by correct service."""
        if self.system.is_port_in_use(port):
            # Check who owns it
            rc, out, _ = self._run(f"ss -tlnp | grep ':{port} '")
            if server in out.lower() or (server == "apache" and "httpd" in out.lower()):
                self._pass(f"Port {port} in use by {server} (expected)")
            else:
                self._warn(f"Port {port} in use by another process")
        else:
            self._pass(f"Port {port} is available")

    def _check_vhost_conflicts(self, config: Dict):
        """Check for vhost domain conflicts with existing configs."""
        domain = config["domain"]
        web_server = config["web_server"]

        if web_server == "nginx":
            vhosts = self.system.get_existing_nginx_vhosts()
        else:
            vhosts = self.system.get_existing_apache_vhosts()

        for vhost_path in vhosts:
            try:
                with open(vhost_path, "r") as f:
                    content = f.read()
                if domain in content:
                    # Check if it's OUR config
                    if config["service_name"] in content:
                        self._pass(f"Existing vhost is ours: {vhost_path}")
                    else:
                        self._warn(
                            f"Domain '{domain}' found in another vhost: {vhost_path}"
                        )
            except (IOError, PermissionError):
                pass

    def _check_nginx_config(self, config: Dict):
        """Verify Nginx config files exist."""
        service_name = config["service_name"]
        for path in [
            f"/etc/nginx/sites-available/{service_name}.conf",
            f"/etc/nginx/sites-enabled/{service_name}.conf",
            f"/etc/nginx/conf.d/{service_name}.conf",
        ]:
            if os.path.exists(path) or os.path.islink(path):
                self._pass(f"Nginx config found: {path}")
                return
        self._fail(f"No Nginx config found for {service_name}")

    def _check_apache_config(self, config: Dict):
        """Verify Apache config files exist."""
        service_name = config["service_name"]
        for path in [
            f"/etc/apache2/sites-available/{service_name}.conf",
            f"/etc/apache2/sites-enabled/{service_name}.conf",
            f"/etc/httpd/conf.d/{service_name}.conf",
        ]:
            if os.path.exists(path) or os.path.islink(path):
                self._pass(f"Apache config found: {path}")
                return
        self._fail(f"No Apache config found for {service_name}")

    def _check_permissions(self, config: Dict):
        """Verify file permissions are correct."""
        deploy_path = config["deploy_path"]
        user = config["user"]

        rc, out, _ = self._run(f"stat -c '%U' '{deploy_path}' 2>/dev/null")
        if rc == 0 and out == user:
            self._pass(f"Ownership correct: {user}")
        elif rc == 0:
            self._warn(f"Owner is {out}, expected {user}")
        else:
            self._warn("Could not verify ownership")

    def _http_health_check(self, config: Dict):
        """Perform HTTP health check."""
        domain = config["domain"]
        scheme = "https" if config.get("enable_ssl") else "http"

        # Try localhost with Host header (works even without DNS)
        rc, out, _ = self._run(
            f"curl -s -o /dev/null -w '%{{http_code}}' "
            f"-H 'Host: {domain}' --connect-timeout 5 "
            f"http://127.0.0.1/"
        )
        if rc == 0:
            status = out.strip("'\"")
            if status.startswith("2") or status.startswith("3"):
                self._pass(f"HTTP health check: {status}")
            elif status == "000":
                self._warn("HTTP health check: no response (service may need time)")
            else:
                self._warn(f"HTTP health check returned: {status}")
        else:
            self._warn("HTTP health check: could not connect")
