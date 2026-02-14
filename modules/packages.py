"""
Package Installer Module - PHP-FPM Automation Agent
=====================================================
Idempotent package installation for PHP, web servers,
and system dependencies. Only installs what's missing.
"""

import subprocess
from typing import Dict, List, Optional, Tuple

from modules.logger import DeployLogger
from modules.system import SystemDetector


class PackageInstaller:
    """
    Handles idempotent installation of:
    - Nginx
    - Apache (with event MPM + proxy_fcgi)
    - PHP (specific versions via PPA/Remi)
    - PHP-FPM
    - PHP extensions
    - System utilities (git, curl, unzip, etc.)

    Never reinstalls existing packages.
    Never removes existing packages.
    """

    def __init__(self, system: SystemDetector, log: DeployLogger):
        self.system = system
        self.log = log
        self.os_info = system.detect_os()
        self.pkg_manager = system.detect_package_manager()
        self._repo_added = False

    # ── Command Execution ───────────────────────────────────────

    def _run(self, cmd: str, check: bool = True) -> Tuple[int, str, str]:
        """Execute a shell command."""
        self.log.debug(f"exec: {cmd}")
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=600
        )
        if check and result.returncode != 0:
            self.log.debug(f"stderr: {result.stderr}")
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def _apt_install(self, packages: List[str]) -> bool:
        """Install packages via apt (non-interactive)."""
        pkg_str = " ".join(packages)
        self.log.info(f"Installing via apt: {pkg_str}")
        env = "DEBIAN_FRONTEND=noninteractive"
        rc, out, err = self._run(
            f"{env} apt-get install -y --no-install-recommends {pkg_str}"
        )
        if rc != 0:
            self.log.error(f"apt install failed: {err}")
            return False
        return True

    def _yum_install(self, packages: List[str]) -> bool:
        """Install packages via yum/dnf."""
        pkg_str = " ".join(packages)
        cmd = "dnf" if self.pkg_manager == "dnf" else "yum"
        self.log.info(f"Installing via {cmd}: {pkg_str}")
        rc, out, err = self._run(f"{cmd} install -y {pkg_str}")
        if rc != 0:
            self.log.error(f"{cmd} install failed: {err}")
            return False
        return True

    def _install(self, packages: List[str]) -> bool:
        """Install packages using the detected package manager."""
        if self.pkg_manager == "apt":
            return self._apt_install(packages)
        elif self.pkg_manager in ("yum", "dnf"):
            return self._yum_install(packages)
        else:
            self.log.error(f"Unsupported package manager: {self.pkg_manager}")
            return False

    # ── PHP Repository Setup ────────────────────────────────────

    def ensure_php_repository(self) -> bool:
        """
        Add the PHP PPA/repository if not already present.
        - Ubuntu/Debian: ondrej/php PPA
        - RHEL/CentOS: Remi repository
        """
        if self._repo_added:
            return True

        self.log.step("Ensuring PHP package repository is available")

        if self.os_info["family"] == "debian":
            # Check if ondrej PPA is already added
            rc, out, _ = self._run("apt-cache policy 2>/dev/null | grep -q 'ondrej/php'")
            if rc == 0:
                self.log.skip("PHP PPA already configured")
                self._repo_added = True
                return True

            # Add ondrej/php PPA
            self.log.info("Adding ondrej/php PPA...")
            self._run("apt-get update -y")
            self._install(["software-properties-common", "apt-transport-https", "ca-certificates"])
            rc, _, err = self._run("add-apt-repository -y ppa:ondrej/php")
            if rc != 0:
                # Try LC_ALL=C workaround
                rc, _, err = self._run("LC_ALL=C.UTF-8 add-apt-repository -y ppa:ondrej/php")
            if rc != 0:
                self.log.error(f"Failed to add PHP PPA: {err}")
                return False
            self._run("apt-get update -y")

        elif self.os_info["family"] == "rhel":
            # Check if Remi repo exists
            rc, _, _ = self._run("rpm -q remi-release")
            if rc == 0:
                self.log.skip("Remi repository already configured")
                self._repo_added = True
                return True

            # Install EPEL + Remi
            self.log.info("Adding Remi PHP repository...")
            self._run(f"yum install -y epel-release")
            version = self.os_info.get("version", "8").split(".")[0]
            remi_url = f"https://rpms.remirepo.net/enterprise/remi-release-{version}.rpm"
            rc, _, err = self._run(f"yum install -y {remi_url}")
            if rc != 0:
                self.log.error(f"Failed to install Remi repo: {err}")
                return False

        self._repo_added = True
        self.log.success("PHP repository configured")
        return True

    # ── System Utilities ────────────────────────────────────────

    def install_system_utilities(self) -> bool:
        """Install required system utilities if missing."""
        self.log.step("Checking system utilities")

        needed = []
        utilities = {
            "git": "git",
            "curl": "curl",
            "unzip": "unzip",
            "wget": "wget",
            "tar": "tar",
        }

        for cmd, pkg in utilities.items():
            if not self.system._cmd_exists(cmd):
                needed.append(pkg)
            else:
                self.log.debug(f"  {cmd}: already installed")

        if not needed:
            self.log.skip("All system utilities present")
            return True

        self.log.info(f"Installing missing utilities: {', '.join(needed)}")
        if self.pkg_manager == "apt":
            self._run("apt-get update -y")

        return self._install(needed)

    # ── Nginx Installation ──────────────────────────────────────

    def install_nginx(self) -> bool:
        """Install Nginx if not already present."""
        self.log.step("Checking Nginx installation")

        if self.system.is_nginx_installed():
            version = self.system.get_nginx_version()
            self.log.skip(f"Nginx already installed (v{version})")
            return True

        self.log.info("Installing Nginx...")
        if self.pkg_manager == "apt":
            self._run("apt-get update -y")
            success = self._install(["nginx"])
        else:
            success = self._install(["nginx"])

        if success:
            # Enable but don't start yet
            self._run("systemctl enable nginx")
            self.log.success("Nginx installed")
        return success

    # ── Apache Installation ─────────────────────────────────────

    def install_apache(self) -> bool:
        """Install Apache with event MPM and proxy_fcgi if not present."""
        self.log.step("Checking Apache installation")

        if self.system.is_apache_installed():
            version = self.system.get_apache_version()
            self.log.skip(f"Apache already installed (v{version})")
            self._configure_apache_modules()
            return True

        self.log.info("Installing Apache...")
        if self.pkg_manager == "apt":
            self._run("apt-get update -y")
            success = self._install(["apache2"])
        else:
            success = self._install(["httpd"])

        if success:
            cmd = self.system.get_apache_command()
            self._run(f"systemctl enable {cmd}")
            self._configure_apache_modules()
            self.log.success("Apache installed")
        return success

    def _configure_apache_modules(self):
        """Ensure required Apache modules are enabled."""
        if self.os_info["family"] == "debian":
            required_modules = [
                "proxy", "proxy_fcgi", "rewrite", "headers",
                "ssl", "setenvif", "mpm_event",
            ]
            # Disable prefork if enabled (we want event MPM)
            self._run("a2dismod mpm_prefork 2>/dev/null")

            for mod in required_modules:
                rc, _, _ = self._run(f"a2query -m {mod} 2>/dev/null")
                if rc != 0:
                    self.log.info(f"Enabling Apache module: {mod}")
                    self._run(f"a2enmod {mod}")
                else:
                    self.log.debug(f"Apache module already enabled: {mod}")

    # ── PHP Installation ────────────────────────────────────────

    def install_php(self, version: str, extensions: List[str]) -> bool:
        """
        Install PHP-FPM for specified version with extensions.
        Only installs missing components.
        """
        self.log.step(f"Checking PHP {version} installation")

        # Ensure repo is set up
        if not self.ensure_php_repository():
            return False

        # Check if version is already installed
        version_installed = self.system.is_php_version_installed(version)

        if version_installed:
            self.log.skip(f"PHP {version} already installed")
        else:
            self.log.info(f"Installing PHP {version}...")

        # Build package list
        packages_needed = []

        if self.os_info["family"] == "debian":
            # Core PHP-FPM packages
            core_packages = [f"php{version}-fpm", f"php{version}-cli", f"php{version}-common"]
            if not version_installed:
                packages_needed.extend(core_packages)

            # Extension packages
            missing_exts = self.system.get_missing_extensions(version, extensions)
            for ext in missing_exts:
                if ext in ("fpm", "cli", "common"):
                    continue  # Already handled above
                packages_needed.append(f"php{version}-{ext}")

        elif self.os_info["family"] == "rhel":
            ver_nodot = version.replace(".", "")
            # Enable Remi module for this PHP version
            self._run(f"yum module reset php -y 2>/dev/null")
            self._run(f"yum module enable php:remi-{version} -y 2>/dev/null")

            core_packages = [
                f"php{ver_nodot}-php-fpm",
                f"php{ver_nodot}-php-cli",
                f"php{ver_nodot}-php-common",
            ]
            if not version_installed:
                packages_needed.extend(core_packages)

            missing_exts = self.system.get_missing_extensions(version, extensions)
            for ext in missing_exts:
                if ext in ("fpm", "cli", "common"):
                    continue
                packages_needed.append(f"php{ver_nodot}-php-{ext}")

        if not packages_needed:
            self.log.skip(f"All PHP {version} packages and extensions present")
            return True

        self.log.info(f"Installing {len(packages_needed)} PHP packages...")
        for pkg in packages_needed:
            self.log.debug(f"  → {pkg}")

        if self.pkg_manager == "apt":
            self._run("apt-get update -y")

        success = self._install(packages_needed)

        if success:
            # Enable PHP-FPM service
            fpm_service = self.system.get_php_fpm_service_name(version)
            self._run(f"systemctl enable {fpm_service}")
            self.log.success(f"PHP {version} with extensions installed")
        else:
            self.log.error(f"Failed to install some PHP {version} packages")

        return success

    # ── Composer Installation ────────────────────────────────────

    def install_composer(self) -> bool:
        """Install Composer globally if not present."""
        if self.system._cmd_exists("composer"):
            self.log.skip("Composer already installed")
            return True

        self.log.info("Installing Composer...")
        cmds = [
            "curl -sS https://getcomposer.org/installer -o /tmp/composer-setup.php",
            "php /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer",
            "rm -f /tmp/composer-setup.php",
        ]
        for cmd in cmds:
            rc, _, err = self._run(cmd)
            if rc != 0:
                self.log.error(f"Composer install failed: {err}")
                return False

        self.log.success("Composer installed")
        return True

    # ── Certbot (Let's Encrypt) ─────────────────────────────────

    def install_certbot(self, web_server: str) -> bool:
        """Install Certbot for SSL certificate management."""
        if self.system._cmd_exists("certbot"):
            self.log.skip("Certbot already installed")
            return True

        self.log.info("Installing Certbot...")
        if self.pkg_manager == "apt":
            packages = ["certbot"]
            if web_server == "nginx":
                packages.append("python3-certbot-nginx")
            else:
                packages.append("python3-certbot-apache")
            return self._install(packages)
        else:
            packages = ["certbot"]
            if web_server == "nginx":
                packages.append("python3-certbot-nginx")
            else:
                packages.append("python3-certbot-apache")
            return self._install(packages)
