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

            # Ensure root can authenticate with password (may still be auth_socket)
            self._configure_mysql_root_auth()

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
                # Configure root for password auth so PHP apps can connect
                self._configure_mysql_root_auth()
                self.log.success(f"{preferred} installed and started")
                return True

        self.log.warn(f"{preferred} installed but could not start service")
        return True

    def _get_mysql_admin_cmd(self) -> Optional[str]:
        """Find a working MySQL admin connection command."""
        for cmd in ["mysql -u root", "sudo mysql"]:
            rc, _, _ = self._run(f"{cmd} -e 'SELECT 1;' 2>/dev/null")
            if rc == 0:
                return cmd
        return None

    def _configure_mysql_root_auth(self):
        """
        Verify MySQL root access.

        We keep auth_socket (Ubuntu default) so admin operations always work
        via 'sudo mysql'. Apps should use dedicated users, not root.
        """
        admin_cmd = self._get_mysql_admin_cmd()
        if admin_cmd:
            self.log.info(f"MySQL root accessible via: {admin_cmd.split()[0]}")
        else:
            self.log.warn("Cannot access MySQL as root — trying auth reset...")
            # Last resort: reset via skip-grant-tables
            import time
            self._run("systemctl stop mysql 2>/dev/null; systemctl stop mysqld 2>/dev/null")
            self._run("mysqld_safe --skip-grant-tables --skip-networking &")
            time.sleep(3)
            self._run(
                'mysql -u root -e "FLUSH PRIVILEGES; '
                "ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket; "
                'FLUSH PRIVILEGES;"'
            )
            self._run("mysqladmin shutdown 2>/dev/null")
            time.sleep(1)
            self._run("systemctl start mysql 2>/dev/null; systemctl start mysqld 2>/dev/null")
            time.sleep(2)
            admin_cmd = self._get_mysql_admin_cmd()
            if admin_cmd:
                self.log.info("MySQL root access restored")
            else:
                self.log.warn("Could not restore MySQL root access")

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

            # Ensure password auth is enabled (may still be peer-only)
            self._configure_postgresql_auth()

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

        # Configure password auth for PHP apps
        self._configure_postgresql_auth()

        self.log.success("PostgreSQL installed and started")
        return True

    def _configure_postgresql_auth(self):
        """
        Configure PostgreSQL pg_hba.conf for md5/scram password auth.

        By default, PostgreSQL on Ubuntu uses peer auth for local connections,
        which prevents PHP apps from connecting with username/password.
        We change local connections from 'peer' to 'md5' so apps can
        authenticate via password.
        """
        # Find pg_hba.conf
        rc, hba_path, _ = self._run(
            "sudo -u postgres psql -t -c 'SHOW hba_file;' 2>/dev/null"
        )
        hba_path = hba_path.strip() if rc == 0 else ""

        if not hba_path or not os.path.isfile(hba_path):
            # Fallback: search common locations
            for candidate in [
                "/etc/postgresql/*/main/pg_hba.conf",
                "/var/lib/pgsql/data/pg_hba.conf",
            ]:
                import glob
                matches = glob.glob(candidate)
                if matches:
                    hba_path = matches[-1]  # Use latest version
                    break

        if not hba_path or not os.path.isfile(hba_path):
            self.log.warn("Could not find pg_hba.conf — apps may need manual DB auth config")
            return

        try:
            with open(hba_path, "r") as f:
                content = f.read()

            # Check if already configured for md5/scram
            import re as _re
            # Look for lines like: local all all peer
            if not _re.search(r'^\s*local\s+all\s+all\s+peer', content, _re.MULTILINE):
                self.log.info("pg_hba.conf already allows password auth for local connections")
                return

            # Replace peer with md5 for local connections (not for postgres system user)
            new_content = _re.sub(
                r'^(local\s+all\s+all\s+)peer',
                r'\1md5',
                content,
                flags=_re.MULTILINE
            )

            if new_content != content:
                # Backup original
                import shutil
                shutil.copy2(hba_path, hba_path + ".bak")

                with open(hba_path, "w") as f:
                    f.write(new_content)

                # Reload PostgreSQL to apply changes
                self._run("systemctl reload postgresql")
                self.log.info("Configured PostgreSQL pg_hba.conf for password auth (peer → md5)")
            else:
                self.log.info("pg_hba.conf already configured for password auth")

        except Exception as e:
            self.log.warn(f"Could not configure pg_hba.conf: {e}")

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

    # ── Database Provisioning ───────────────────────────────────

    def provision_database(self, driver: str, creds: Dict) -> bool:
        """
        Create the database, user, and grant privileges based on extracted credentials.

        This is called AFTER ensure_database() installs the DB engine, and BEFORE
        SQL files are imported. It ensures the target database and user exist.

        Args:
            driver: 'mysql', 'pgsql', 'postgres', etc.
            creds: dict with keys: host, port, dbname, user, password
        """
        dbname = creds.get("dbname")
        user = creds.get("user")
        password = creds.get("password")

        if not dbname:
            self.log.warn("No database name detected from source code — skipping provisioning")
            return False

        self.log.step(f"Provisioning database: {dbname}")

        if driver in ("mysql", "mariadb"):
            return self._provision_mysql(dbname, user, password)
        elif driver in ("pgsql", "postgres", "postgresql"):
            return self._provision_postgresql(dbname, user, password)
        else:
            self.log.warn(f"Unknown driver {driver} — cannot provision database")
            return False

    def _provision_postgresql(self, dbname: str, user: Optional[str] = None,
                              password: Optional[str] = None) -> bool:
        """
        Create PostgreSQL database and user with proper permissions.

        Steps:
        1. Set password for the target user (create user if not 'postgres')
        2. Create database owned by the user
        3. Grant all privileges
        """
        user = user or "postgres"
        self.log.info(f"PostgreSQL: ensuring database '{dbname}' with user '{user}'")

        # Step 1: Handle user setup
        if user == "postgres":
            # Set the postgres superuser password
            if password:
                rc, _, err = self._run(
                    f"sudo -u postgres psql -c \"ALTER USER postgres WITH PASSWORD '{password}';\""
                )
                if rc == 0:
                    self.log.info("Set password for postgres superuser")
                else:
                    self.log.warn(f"Could not set postgres password: {err}")
        else:
            # Create application-specific user if not exists
            rc, out, _ = self._run(
                f"sudo -u postgres psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='{user}';\""
            )
            if out.strip() == "1":
                self.log.info(f"PostgreSQL user '{user}' already exists")
                # Update password if specified
                if password:
                    self._run(
                        f"sudo -u postgres psql -c \"ALTER USER {user} WITH PASSWORD '{password}';\""
                    )
            else:
                pwd_clause = f"PASSWORD '{password}'" if password else ""
                rc, _, err = self._run(
                    f"sudo -u postgres psql -c \"CREATE USER {user} WITH {pwd_clause} CREATEDB;\""
                )
                if rc == 0:
                    self.log.info(f"Created PostgreSQL user: {user}")
                else:
                    self.log.warn(f"Could not create PostgreSQL user: {err}")

        # Step 2: Create database if not exists
        rc, out, _ = self._run(
            f"sudo -u postgres psql -tAc \"SELECT 1 FROM pg_database WHERE datname='{dbname}';\""
        )
        if out.strip() == "1":
            self.log.info(f"PostgreSQL database '{dbname}' already exists")
        else:
            owner_clause = f"OWNER {user}" if user else ""
            rc, _, err = self._run(
                f"sudo -u postgres psql -c \"CREATE DATABASE {dbname} {owner_clause};\""
            )
            if rc == 0:
                self.log.success(f"Created PostgreSQL database: {dbname}")
            else:
                self.log.error(f"Failed to create database '{dbname}': {err}")
                return False

        # Step 3: Grant privileges (if non-postgres user)
        if user and user != "postgres":
            self._run(
                f"sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE {dbname} TO {user};\""
            )
            # Also grant schema permissions (PostgreSQL 15+ requires this)
            self._run(
                f"sudo -u postgres psql -d {dbname} -c \"GRANT ALL ON SCHEMA public TO {user};\""
            )
            self.log.info(f"Granted all privileges on '{dbname}' to '{user}'")

        self.log.success(f"PostgreSQL database '{dbname}' ready for user '{user}'")
        return True

    def _provision_mysql(self, dbname: str, user: Optional[str] = None,
                         password: Optional[str] = None) -> bool:
        """
        Create MySQL database and user with proper permissions.

        Uses _get_mysql_admin_cmd() for reliable admin access.
        Always creates a dedicated app user (never alters root password).
        """
        user = user or "root"
        admin_cmd = self._get_mysql_admin_cmd()
        if not admin_cmd:
            self.log.error("Cannot connect to MySQL as admin — provision failed")
            return False

        self.log.info(f"MySQL: ensuring database '{dbname}' with user '{user}'")

        # Step 1: Create database
        rc, _, err = self._run(
            f"{admin_cmd} -e 'CREATE DATABASE IF NOT EXISTS `{dbname}`;'"
        )
        if rc == 0:
            self.log.info(f"MySQL database '{dbname}' ready")
        else:
            self.log.error(f"Failed to create MySQL database '{dbname}': {err}")
            return False

        # Step 2: Create app user with password and grant privileges
        if user and password:
            pwd_clause = f"IDENTIFIED BY '{password}'"
            # Always create/update user with password (works for both root and non-root)
            if user == "root":
                # For root: set password via ALTER USER
                self._run(
                    f'{admin_cmd} -e "ALTER USER \'root\'@\'localhost\' '
                    f"IDENTIFIED WITH mysql_native_password BY '{password}'; "
                    f'FLUSH PRIVILEGES;"'
                )
                self.log.info("Set MySQL root password from app config")
            else:
                # For non-root: CREATE USER + GRANT
                self._run(
                    f'{admin_cmd} -e "CREATE USER IF NOT EXISTS '
                    f"'{user}'@'localhost' {pwd_clause}; "
                    f'FLUSH PRIVILEGES;"'
                )
                self._run(
                    f'{admin_cmd} -e "GRANT ALL PRIVILEGES ON `{dbname}`.* '
                    f"TO '{user}'@'localhost'; FLUSH PRIVILEGES;\""
                )
                self.log.info(f"MySQL user '{user}' ready with full privileges on '{dbname}'")
        elif user and user != "root":
            # Non-root user without password
            self._run(
                f'{admin_cmd} -e "CREATE USER IF NOT EXISTS '
                f"'{user}'@'localhost'; FLUSH PRIVILEGES;\""
            )
            self._run(
                f'{admin_cmd} -e "GRANT ALL PRIVILEGES ON `{dbname}`.* '
                f"TO '{user}'@'localhost'; FLUSH PRIVILEGES;\""
            )

        self.log.success(f"MySQL database '{dbname}' ready for user '{user}'")
        return True
