"""
System Detection Module - PHP-FPM Automation Agent
====================================================
Detects installed software, OS version, package manager,
and system state. All detection is non-destructive.
"""

import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

from modules.logger import DeployLogger


class SystemDetector:
    """
    Detects system state including:
    - OS distribution and version
    - Package manager (apt/yum/dnf)
    - Installed web servers (Nginx, Apache)
    - Installed PHP versions
    - Installed PHP extensions per version
    - Running services
    - Port usage
    """

    def __init__(self, log: DeployLogger):
        self.log = log
        self._os_info: Optional[Dict] = None
        self._pkg_manager: Optional[str] = None

    # ── Command Execution ───────────────────────────────────────

    def _run(self, cmd: str, check: bool = False) -> Tuple[int, str, str]:
        """Run a shell command and return (returncode, stdout, stderr)."""
        self.log.debug(f"exec: {cmd}")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=120
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            self.log.warn(f"Command timed out: {cmd}")
            return 1, "", "timeout"
        except Exception as e:
            self.log.warn(f"Command failed: {cmd} → {e}")
            return 1, "", str(e)

    def _cmd_exists(self, cmd: str) -> bool:
        """Check if a command exists on the system."""
        rc, _, _ = self._run(f"command -v {cmd}")
        return rc == 0

    # ── OS Detection ────────────────────────────────────────────

    def detect_os(self) -> Dict[str, str]:
        """Detect OS distribution, version, and codename."""
        if self._os_info:
            return self._os_info

        info = {"distro": "unknown", "version": "unknown", "codename": "unknown", "family": "unknown"}

        if os.path.exists("/etc/os-release"):
            rc, out, _ = self._run("cat /etc/os-release")
            if rc == 0:
                for line in out.splitlines():
                    if line.startswith("ID="):
                        info["distro"] = line.split("=", 1)[1].strip('"').lower()
                    elif line.startswith("VERSION_ID="):
                        info["version"] = line.split("=", 1)[1].strip('"')
                    elif line.startswith("VERSION_CODENAME="):
                        info["codename"] = line.split("=", 1)[1].strip('"')
                    elif line.startswith("ID_LIKE="):
                        info["family"] = line.split("=", 1)[1].strip('"').lower()

        # Determine family
        if info["distro"] in ("ubuntu", "debian", "linuxmint", "pop"):
            info["family"] = "debian"
        elif info["distro"] in ("centos", "rhel", "rocky", "almalinux", "fedora", "ol"):
            info["family"] = "rhel"

        self._os_info = info
        self.log.info(f"OS detected: {info['distro']} {info['version']} ({info['family']} family)")
        return info

    # ── Package Manager ─────────────────────────────────────────

    def detect_package_manager(self) -> str:
        """Detect the system package manager."""
        if self._pkg_manager:
            return self._pkg_manager

        if self._cmd_exists("apt-get"):
            self._pkg_manager = "apt"
        elif self._cmd_exists("dnf"):
            self._pkg_manager = "dnf"
        elif self._cmd_exists("yum"):
            self._pkg_manager = "yum"
        else:
            self._pkg_manager = "unknown"

        self.log.info(f"Package manager: {self._pkg_manager}")
        return self._pkg_manager

    # ── Nginx Detection ─────────────────────────────────────────

    def is_nginx_installed(self) -> bool:
        return self._cmd_exists("nginx")

    def get_nginx_version(self) -> Optional[str]:
        if not self.is_nginx_installed():
            return None
        rc, out, _ = self._run("nginx -v 2>&1")
        # nginx version output goes to stderr
        rc2, _, err = self._run("nginx -v")
        version_str = out or err
        match = re.search(r"nginx/(\S+)", version_str)
        return match.group(1) if match else None

    def is_nginx_running(self) -> bool:
        rc, _, _ = self._run("systemctl is-active nginx")
        return rc == 0

    def nginx_config_test(self) -> Tuple[bool, str]:
        """Run nginx -t and return (success, output)."""
        rc, out, err = self._run("nginx -t 2>&1")
        # nginx -t outputs to stderr
        _, _, err2 = self._run("nginx -t")
        output = out or err or err2
        return rc == 0 or "successful" in output.lower(), output

    # ── Apache Detection ────────────────────────────────────────

    def is_apache_installed(self) -> bool:
        return self._cmd_exists("apache2") or self._cmd_exists("httpd")

    def get_apache_command(self) -> str:
        """Return correct apache command name for this distro."""
        if self._cmd_exists("apache2"):
            return "apache2"
        return "httpd"

    def get_apache_ctl(self) -> str:
        """Return correct apachectl command name."""
        if self._cmd_exists("apache2ctl"):
            return "apache2ctl"
        return "apachectl"

    def get_apache_version(self) -> Optional[str]:
        if not self.is_apache_installed():
            return None
        ctl = self.get_apache_ctl()
        rc, out, _ = self._run(f"{ctl} -v")
        match = re.search(r"Apache/(\S+)", out)
        return match.group(1) if match else None

    def is_apache_running(self) -> bool:
        cmd = self.get_apache_command()
        rc, _, _ = self._run(f"systemctl is-active {cmd}")
        return rc == 0

    def apache_config_test(self) -> Tuple[bool, str]:
        """Run apache configtest and return (success, output)."""
        ctl = self.get_apache_ctl()
        rc, out, err = self._run(f"{ctl} configtest 2>&1")
        output = out or err
        return rc == 0 or "syntax ok" in output.lower(), output

    # ── PHP Detection ───────────────────────────────────────────

    def get_installed_php_versions(self) -> List[str]:
        """Return list of installed PHP versions (e.g., ['7.4', '8.1', '8.2'])."""
        versions = set()

        # Method 1: Check for php-fpm binaries
        for check_dir in ["/usr/sbin", "/usr/bin", "/usr/local/bin"]:
            if os.path.isdir(check_dir):
                for f in os.listdir(check_dir):
                    match = re.match(r"php-fpm(\d+\.\d+)", f)
                    if match:
                        versions.add(match.group(1))

        # Method 2: Check for php-fpm service files
        rc, out, _ = self._run("systemctl list-unit-files 'php*-fpm*' --no-pager 2>/dev/null")
        if rc == 0:
            for line in out.splitlines():
                match = re.search(r"php(\d+\.\d+)-fpm", line)
                if match:
                    versions.add(match.group(1))

        # Method 3: Check /etc/php directory (Debian/Ubuntu)
        if os.path.isdir("/etc/php"):
            for d in os.listdir("/etc/php"):
                if re.match(r"\d+\.\d+", d):
                    versions.add(d)

        # Method 4: Check for Remi/RHEL style paths
        if os.path.isdir("/etc/opt/remi"):
            for d in os.listdir("/etc/opt/remi"):
                match = re.match(r"php(\d)(\d)", d)
                if match:
                    versions.add(f"{match.group(1)}.{match.group(2)}")

        result = sorted(versions)
        self.log.info(f"Installed PHP versions: {result or 'none'}")
        return result

    def is_php_version_installed(self, version: str) -> bool:
        """Check if a specific PHP version is installed."""
        return version in self.get_installed_php_versions()

    def is_php_fpm_running(self, version: str) -> bool:
        """Check if PHP-FPM for a given version is running."""
        # Debian/Ubuntu style
        rc, _, _ = self._run(f"systemctl is-active php{version}-fpm")
        if rc == 0:
            return True
        # RHEL style
        ver_nodot = version.replace(".", "")
        rc, _, _ = self._run(f"systemctl is-active php{ver_nodot}-php-fpm")
        return rc == 0

    def get_php_fpm_service_name(self, version: str) -> str:
        """Get the correct systemd service name for PHP-FPM."""
        os_info = self.detect_os()
        if os_info["family"] == "debian":
            return f"php{version}-fpm"
        else:
            ver_nodot = version.replace(".", "")
            return f"php{ver_nodot}-php-fpm"

    def get_installed_php_extensions(self, version: str) -> List[str]:
        """Get list of installed PHP extensions for a version."""
        extensions = []
        os_info = self.detect_os()

        if os_info["family"] == "debian":
            rc, out, _ = self._run(f"dpkg -l 'php{version}-*' 2>/dev/null | grep '^ii'")
            if rc == 0:
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        pkg = parts[1]
                        ext = pkg.replace(f"php{version}-", "")
                        extensions.append(ext)
        else:
            ver_nodot = version.replace(".", "")
            rc, out, _ = self._run(f"rpm -qa 'php{ver_nodot}-php-*' 2>/dev/null")
            if rc == 0:
                for line in out.splitlines():
                    match = re.search(rf"php{ver_nodot}-php-(\S+)", line)
                    if match:
                        extensions.append(match.group(1).split("-")[0])

        self.log.debug(f"PHP {version} extensions: {extensions}")
        return extensions

    def get_missing_extensions(self, version: str, required: List[str]) -> List[str]:
        """Return list of required extensions not yet installed."""
        installed = self.get_installed_php_extensions(version)
        # Normalize names
        installed_lower = [e.lower() for e in installed]
        missing = [ext for ext in required if ext.lower() not in installed_lower]
        return missing

    # ── Port Detection ──────────────────────────────────────────

    def get_used_ports(self) -> List[int]:
        """Get list of ports in use."""
        ports = []
        rc, out, _ = self._run("ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null")
        if rc == 0:
            for line in out.splitlines():
                match = re.search(r":(\d+)\s", line)
                if match:
                    ports.append(int(match.group(1)))
        return sorted(set(ports))

    def is_port_in_use(self, port: int) -> bool:
        return port in self.get_used_ports()

    # ── Socket Detection ────────────────────────────────────────

    def get_existing_fpm_sockets(self) -> List[str]:
        """Find all existing PHP-FPM unix sockets."""
        sockets = []
        search_paths = ["/run/php", "/var/run/php", "/run", "/var/run"]
        for path in search_paths:
            if os.path.isdir(path):
                for f in os.listdir(path):
                    if f.endswith(".sock") and "php" in f.lower():
                        sockets.append(os.path.join(path, f))
        return sockets

    # ── Existing VHost Detection ────────────────────────────────

    def get_existing_nginx_vhosts(self) -> List[str]:
        """List existing Nginx vhost config files."""
        vhosts = []
        for d in ["/etc/nginx/sites-enabled", "/etc/nginx/conf.d"]:
            if os.path.isdir(d):
                vhosts.extend(
                    os.path.join(d, f) for f in os.listdir(d) if not f.startswith(".")
                )
        return vhosts

    def get_existing_apache_vhosts(self) -> List[str]:
        """List existing Apache vhost config files."""
        vhosts = []
        for d in ["/etc/apache2/sites-enabled", "/etc/httpd/conf.d"]:
            if os.path.isdir(d):
                vhosts.extend(
                    os.path.join(d, f) for f in os.listdir(d) if not f.startswith(".")
                )
        return vhosts

    # ── User Detection ──────────────────────────────────────────

    def user_exists(self, username: str) -> bool:
        rc, _, _ = self._run(f"id -u {username}")
        return rc == 0

    def group_exists(self, groupname: str) -> bool:
        rc, _, _ = self._run(f"getent group {groupname}")
        return rc == 0

    # ── Comprehensive System Report ────────────────────────────

    def full_report(self) -> Dict:
        """Generate a comprehensive system state report."""
        self.log.banner("SYSTEM DETECTION")

        os_info = self.detect_os()
        pkg_mgr = self.detect_package_manager()

        report = {
            "os": os_info,
            "package_manager": pkg_mgr,
            "nginx": {
                "installed": self.is_nginx_installed(),
                "version": self.get_nginx_version(),
                "running": self.is_nginx_running() if self.is_nginx_installed() else False,
                "vhosts": self.get_existing_nginx_vhosts(),
            },
            "apache": {
                "installed": self.is_apache_installed(),
                "version": self.get_apache_version(),
                "running": self.is_apache_running() if self.is_apache_installed() else False,
                "vhosts": self.get_existing_apache_vhosts(),
            },
            "php_versions": self.get_installed_php_versions(),
            "used_ports": self.get_used_ports(),
            "fpm_sockets": self.get_existing_fpm_sockets(),
        }

        self.log.info(f"  Nginx:    {'✓' if report['nginx']['installed'] else '✗'} "
                      f"{'(v' + report['nginx']['version'] + ')' if report['nginx']['version'] else ''}")
        self.log.info(f"  Apache:   {'✓' if report['apache']['installed'] else '✗'} "
                      f"{'(v' + report['apache']['version'] + ')' if report['apache']['version'] else ''}")
        self.log.info(f"  PHP:      {', '.join(report['php_versions']) or 'none'}")
        self.log.info(f"  Sockets:  {len(report['fpm_sockets'])} FPM sockets found")

        return report
