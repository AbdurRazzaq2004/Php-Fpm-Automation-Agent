"""
Database Module - PHP-FPM Automation Agent
============================================
Smart database detection, installation, and configuration.

Handles:
- Detecting pre-installed databases (MySQL, PostgreSQL, SQLite)
- Safe installation of required database (NEVER overwrites existing)
- Basic configuration for fresh installations
- Extension mapping for PHP database drivers
"""

import os
import subprocess
from typing import Dict, List, Optional, Tuple

from modules.logger import DeployLogger
from modules.system import SystemDetector


class DatabaseManager:
    """
    Manages database detection and installation with safety guarantees:

    SAFETY RULES:
    1. NEVER remove or overwrite an existing database installation
    2. NEVER drop or modify existing databases
    3. Only install a database engine if none of the required type exists
    4. Always confirm state before and after operations
    5. Log all decisions for audit trail
    """

    # Map database drivers to their PHP extensions
    DB_EXTENSION_MAP = {
        "mysql": ["mysql", "pdo_mysql"],
        "mariadb": ["mysql", "pdo_mysql"],
        "pgsql": ["pgsql", "pdo_pgsql"],
        "postgres": ["pgsql", "pdo_pgsql"],
        "sqlite": ["sqlite3"],
        "sqlite3": ["sqlite3"],
    }

    # Map database drivers to system packages (Debian/Ubuntu)
    DB_PACKAGES_DEBIAN = {
        "mysql": ["mysql-server", "mysql-client"],
        "mariadb": ["mariadb-server", "mariadb-client"],
        "pgsql": ["postgresql", "postgresql-client"],
        "postgres": ["postgresql", "postgresql-client"],
        "sqlite": ["sqlite3"],
        "sqlite3": ["sqlite3"],
    }

    # Map database drivers to system packages (RHEL/CentOS)
    DB_PACKAGES_RHEL = {
        "mysql": ["mysql-server"],
        "mariadb": ["mariadb-server"],
        "pgsql": ["postgresql-server", "postgresql"],
        "postgres": ["postgresql-server", "postgresql"],
        "sqlite": ["sqlite"],
        "sqlite3": ["sqlite"],
    }

    def __init__(self, system: SystemDetector, log: DeployLogger):
        self.system = system
        self.log = log
        self.os_info = system.detect_os()

    def _run(self, cmd: str) -> Tuple[int, str, str]:
        """Execute a shell command."""
        self.log.debug(f"db exec: {cmd}")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=120
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return 1, "", "timeout"

    # ── Detection ───────────────────────────────────────────────

    def detect_installed_databases(self) -> Dict[str, Dict]:
        """
        Detect all installed database engines on the system.

        Returns dict like:
        {
            "mysql": {"installed": True, "running": True, "version": "8.0.35"},
            "postgresql": {"installed": False, "running": False, "version": None},
            "sqlite": {"installed": True, "running": None, "version": "3.37.0"},
        }
        """
        self.log.step("Detecting installed databases")
        result = {}

        # MySQL / MariaDB
        mysql_info = self._detect_mysql()
        result["mysql"] = mysql_info

        # PostgreSQL
        pgsql_info = self._detect_postgresql()
        result["postgresql"] = pgsql_info

        # SQLite (library, no server)
        sqlite_info = self._detect_sqlite()
        result["sqlite"] = sqlite_info

        # Log summary
        for name, info in result.items():
            if info["installed"]:
                status = "RUNNING" if info.get("running") else "INSTALLED"
                version = f" (v{info['version']})" if info.get("version") else ""
                self.log.info(f"  {name}: {status}{version}")
            else:
                self.log.debug(f"  {name}: not installed")

        return result

    def _detect_mysql(self) -> Dict:
        """Detect MySQL or MariaDB installation."""
        info = {"installed": False, "running": False, "version": None, "variant": None}

        # Check for MySQL
        if self.system._cmd_exists("mysql"):
            info["installed"] = True
            rc, out, _ = self._run("mysql --version 2>/dev/null")
            if rc == 0:
                if "MariaDB" in out:
                    info["variant"] = "mariadb"
                else:
                    info["variant"] = "mysql"
                import re
                match = re.search(r'(\d+\.\d+\.\d+)', out)
                if match:
                    info["version"] = match.group(1)

        # Check if running
        for service in ["mysql", "mysqld", "mariadb"]:
            rc, _, _ = self._run(f"systemctl is-active {service} 2>/dev/null")
            if rc == 0:
                info["running"] = True
                break

        return info

    def _detect_postgresql(self) -> Dict:
        """Detect PostgreSQL installation."""
        info = {"installed": False, "running": False, "version": None}

        if self.system._cmd_exists("psql"):
            info["installed"] = True
            rc, out, _ = self._run("psql --version 2>/dev/null")
            if rc == 0:
                import re
                match = re.search(r'(\d+\.\d+)', out)
                if match:
                    info["version"] = match.group(1)

        # Check if running
        rc, _, _ = self._run("systemctl is-active postgresql 2>/dev/null")
        if rc == 0:
            info["running"] = True

        return info

    def _detect_sqlite(self) -> Dict:
        """Detect SQLite installation (library-level)."""
        info = {"installed": False, "running": None, "version": None}

        if self.system._cmd_exists("sqlite3"):
            info["installed"] = True
            rc, out, _ = self._run("sqlite3 --version 2>/dev/null")
            if rc == 0:
                parts = out.split()
                if parts:
                    info["version"] = parts[0]

        return info

    # ── Smart Setup ─────────────────────────────────────────────

    def ensure_database(self, required_driver: str, config: Dict) -> bool:
        """
        Ensure the required database is available.

        SAFETY: Never removes or overwrites existing databases.

        Strategy:
        1. Detect what's already installed
        2. If required DB is already installed → use it (log and skip)
        3. If a compatible DB is installed (e.g., MariaDB for mysql) → use it
        4. If nothing compatible is installed → install fresh
        5. Install PHP extensions for the database driver

        Returns True if the database is ready (installed or already present).
        """
        if not required_driver:
            self.log.debug("No database driver required — skipping")
            return True

        # Normalize driver name
        driver = required_driver.lower().strip()
        self.log.step(f"Ensuring database: {driver}")

        installed_dbs = self.detect_installed_databases()

        # SQLite is simple — just needs the library
        if driver in ("sqlite", "sqlite3"):
            return self._ensure_sqlite(installed_dbs)

        # MySQL/MariaDB
        if driver in ("mysql", "mariadb"):
            return self._ensure_mysql(installed_dbs, driver)

        # PostgreSQL
        if driver in ("pgsql", "postgres", "postgresql"):
            return self._ensure_postgresql(installed_dbs)

        self.log.warn(f"Unknown database driver: {driver} — skipping setup")
        return True

    def _ensure_sqlite(self, installed_dbs: Dict) -> bool:
        """Ensure SQLite is available."""
        if installed_dbs["sqlite"]["installed"]:
            self.log.info("✓ SQLite already available")
            return True

        self.log.info("Installing SQLite...")
        if self.os_info["family"] == "debian":
            rc, _, err = self._run("DEBIAN_FRONTEND=noninteractive apt-get install -y sqlite3")
        else:
            rc, _, err = self._run("yum install -y sqlite")

        if rc != 0:
            self.log.error(f"Failed to install SQLite: {err}")
            return False

        self.log.success("SQLite installed")
        return True

    def _ensure_mysql(self, installed_dbs: Dict, preferred: str) -> bool:
        """
        Ensure MySQL/MariaDB is available.

        SAFETY: If any MySQL-compatible DB is already installed,
        we use it instead of installing a new one.
        """
        mysql_info = installed_dbs["mysql"]

        if mysql_info["installed"]:
            variant = mysql_info.get("variant", "mysql")
            version = mysql_info.get("version", "unknown")
            self.log.info(f"✓ {variant} already installed (v{version}) — using existing")

            # Ensure it's running
            if not mysql_info["running"]:
                self.log.info(f"Starting {variant}...")
                for svc in ["mysql", "mysqld", "mariadb"]:
                    rc, _, _ = self._run(f"systemctl start {svc} 2>/dev/null")
                    if rc == 0:
                        self._run(f"systemctl enable {svc}")
                        break

            return True

        # Not installed — install fresh
        self.log.info(f"Installing {preferred}...")

        if self.os_info["family"] == "debian":
            packages = self.DB_PACKAGES_DEBIAN.get(preferred, ["mysql-server"])
            pkg_str = " ".join(packages)
            rc, _, err = self._run(
                f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg_str}"
            )
        else:
            packages = self.DB_PACKAGES_RHEL.get(preferred, ["mysql-server"])
            pkg_str = " ".join(packages)
            rc, _, err = self._run(f"yum install -y {pkg_str}")

        if rc != 0:
            self.log.error(f"Failed to install {preferred}: {err}")
            return False

        # Start and enable
        for svc in ["mysql", "mysqld", "mariadb"]:
            rc, _, _ = self._run(f"systemctl start {svc} 2>/dev/null")
            if rc == 0:
                self._run(f"systemctl enable {svc}")
                self.log.success(f"{preferred} installed and started")
                return True

        self.log.warn(f"{preferred} installed but could not start service")
        return True

    def _ensure_postgresql(self, installed_dbs: Dict) -> bool:
        """
        Ensure PostgreSQL is available.

        SAFETY: If PostgreSQL is already installed, use it.
        """
        pgsql_info = installed_dbs["postgresql"]

        if pgsql_info["installed"]:
            version = pgsql_info.get("version", "unknown")
            self.log.info(f"✓ PostgreSQL already installed (v{version}) — using existing")

            if not pgsql_info["running"]:
                self.log.info("Starting PostgreSQL...")
                self._run("systemctl start postgresql")
                self._run("systemctl enable postgresql")

            return True

        # Not installed — install fresh
        self.log.info("Installing PostgreSQL...")

        if self.os_info["family"] == "debian":
            rc, _, err = self._run(
                "DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql postgresql-client"
            )
        else:
            rc, _, err = self._run("yum install -y postgresql-server postgresql")
            if rc == 0:
                # Initialize database on RHEL
                self._run("postgresql-setup --initdb 2>/dev/null")

        if rc != 0:
            self.log.error(f"Failed to install PostgreSQL: {err}")
            return False

        self._run("systemctl start postgresql")
        self._run("systemctl enable postgresql")
        self.log.success("PostgreSQL installed and started")
        return True

    # ── PHP Extension Mapping ───────────────────────────────────

    def get_required_php_extensions(self, driver: str) -> List[str]:
        """
        Get the PHP extensions needed for a database driver.

        Returns a list of extension names (without 'php8.x-' prefix).
        """
        if not driver:
            return []

        driver = driver.lower().strip()
        return self.DB_EXTENSION_MAP.get(driver, [])

    # ── Composer Version Check ──────────────────────────────────

    def ensure_composer(self, php_version: str) -> bool:
        """
        Ensure a working, recent Composer installation.

        ALWAYS installs from getcomposer.org rather than using
        system packages, because system composer is often outdated
        and can crash with newer PHP versions.

        Strategy:
        1. Check if composer exists and is functional
        2. If system composer exists, check if it's recent enough (>=2.5)
        3. If not, install latest from getcomposer.org to /usr/local/bin
        """
        self.log.step("Ensuring Composer is available")

        # Check if composer already works
        if self.system._cmd_exists("composer"):
            rc, out, _ = self._run("composer --version 2>/dev/null")
            if rc == 0 and out:
                import re
                match = re.search(r'Composer version (\d+\.\d+\.\d+)', out)
                if match:
                    version = match.group(1)
                    major, minor, _ = version.split(".")
                    if int(major) >= 2 and int(minor) >= 5:
                        self.log.info(f"✓ Composer {version} is available and up-to-date")
                        return True
                    else:
                        self.log.warn(
                            f"System Composer {version} is outdated — "
                            f"upgrading to latest"
                        )
                        # Remove old system composer to avoid conflicts
                        self._run("apt-get remove -y composer 2>/dev/null")

        # Install latest Composer from getcomposer.org
        self.log.info("Installing latest Composer...")

        php_bin = f"php{php_version}" if self.system._cmd_exists(f"php{php_version}") else "php"

        cmds = [
            f"curl -sS https://getcomposer.org/installer -o /tmp/composer-setup.php",
            f"{php_bin} /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer",
            "rm -f /tmp/composer-setup.php",
        ]

        for cmd in cmds:
            rc, _, err = self._run(cmd)
            if rc != 0:
                self.log.error(f"Composer installation failed: {err}")
                return False

        # Verify
        rc, out, _ = self._run("composer --version 2>/dev/null")
        if rc == 0:
            self.log.success(f"Composer installed: {out}")
            return True

        self.log.error("Composer installation verification failed")
        return False
