"""
Auto-Detection Module - PHP-FPM Automation Agent
===================================================
Automatically detects application requirements by
inspecting the codebase (composer.json, etc.).

Detects:
- Required PHP version from composer.json
- Framework type (Laravel, Symfony, WordPress, etc.)
- Required PHP extensions from composer.json
- Database requirements
- Document root convention
"""

import json
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

from modules.logger import DeployLogger


class AppAutoDetector:
    """
    Inspects a deployed application's codebase to auto-detect:
    - PHP version requirements (from composer.json "require.php")
    - Framework type (Laravel, Symfony, WordPress, CodeIgniter, etc.)
    - Required PHP extensions (from composer.json "require.ext-*")
    - Database driver needs (MySQL, PostgreSQL, SQLite)
    - Document root suffix (/public, /web, /htdocs, etc.)
    - Writable directories the framework needs
    """

    # Map of PHP constraint patterns to recommended versions
    # We prefer the HIGHEST compatible version for security/performance
    PHP_VERSION_MAP = {
        "8.4": "8.4",
        "8.3": "8.3",
        "8.2": "8.2",
        "8.1": "8.1",
        "8.0": "8.0",
        "7.4": "7.4",
        "7.3": "7.4",  # 7.3 is EOL, use 7.4
        "7.2": "7.4",
        "7.1": "7.4",
        "7.0": "7.4",
    }

    SUPPORTED_VERSIONS = ["7.4", "8.0", "8.1", "8.2", "8.3", "8.4"]

    def __init__(self, log: DeployLogger):
        self.log = log

    # ── PHP Version Detection ───────────────────────────────────

    def detect_php_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        """
        Detect the best PHP version for the app.

        Strategy:
        1. Read composer.json "require.php" constraint
        2. Parse the version constraint (^7.3, >=8.1, ~8.2, etc.)
        3. Find the best matching installable version
        4. If configured_version is set and compatible, prefer it
        5. Fall back to configured_version or 8.2 as default

        Returns: PHP version string like "8.2"
        """
        composer_data = self._read_composer_json(deploy_path)
        if not composer_data:
            self.log.info("No composer.json found — using configured PHP version")
            return configured_version or "8.2"

        php_constraint = composer_data.get("require", {}).get("php", "")
        if not php_constraint:
            self.log.info("No PHP version constraint in composer.json — using configured version")
            return configured_version or "8.2"

        self.log.info(f"composer.json requires PHP: {php_constraint}")

        best_version = self._resolve_php_constraint(php_constraint)

        if best_version:
            if configured_version and configured_version != best_version:
                self.log.warn(
                    f"Config specifies PHP {configured_version}, but app requires '{php_constraint}'. "
                    f"Auto-selecting PHP {best_version} for compatibility."
                )
            else:
                self.log.info(f"Auto-detected PHP version: {best_version}")
            return best_version

        # Fall back to configured or default
        self.log.warn(
            f"Could not resolve PHP constraint '{php_constraint}' — "
            f"using {'configured ' + configured_version if configured_version else 'default 8.2'}"
        )
        return configured_version or "8.2"

    def _resolve_php_constraint(self, constraint: str) -> Optional[str]:
        """
        Resolve a composer PHP version constraint to a concrete version.

        Handles:
        - ^7.3     → highest 7.x that works (7.4)
        - ^8.1     → highest 8.x that works (8.4)
        - ^8.2     → highest 8.x >= 8.2
        - >=7.3    → highest available
        - ~8.1     → 8.1.x or 8.2.x etc
        - >=8.1 <8.4 → range
        - 8.2.*    → 8.2
        - ^7.3|^8.0 → highest version satisfying either
        - Multiple constraints with spaces or commas

        Returns the HIGHEST compatible installable version.
        """
        # Handle OR constraints (|)
        if "|" in constraint:
            parts = [p.strip() for p in constraint.split("|")]
            candidates = []
            for part in parts:
                v = self._resolve_single_constraint(part)
                if v:
                    candidates.append(v)
            if candidates:
                # Return highest version
                return self._highest_version(candidates)
            return None

        return self._resolve_single_constraint(constraint)

    def _resolve_single_constraint(self, constraint: str) -> Optional[str]:
        """Resolve a single PHP version constraint."""
        constraint = constraint.strip()

        # Exact version: 8.2.* or 8.2
        match = re.match(r'^(\d+\.\d+)(?:\.\*)?$', constraint)
        if match:
            version = match.group(1)
            if version in self.SUPPORTED_VERSIONS:
                return version
            return None

        # Caret constraint: ^7.3 means >=7.3.0, <8.0.0
        match = re.match(r'^\^(\d+)\.(\d+)', constraint)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            min_version = f"{major}.{minor}"
            # Find highest version with same major
            candidates = [
                v for v in self.SUPPORTED_VERSIONS
                if self._version_tuple(v)[0] == major
                and self._version_tuple(v) >= self._version_tuple(min_version)
            ]
            if candidates:
                return candidates[-1]  # Highest
            return None

        # Tilde constraint: ~8.1 means >=8.1.0, <9.0.0 (roughly same as ^)
        match = re.match(r'^~(\d+)\.(\d+)', constraint)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            min_version = f"{major}.{minor}"
            candidates = [
                v for v in self.SUPPORTED_VERSIONS
                if self._version_tuple(v)[0] == major
                and self._version_tuple(v) >= self._version_tuple(min_version)
            ]
            if candidates:
                return candidates[-1]
            return None

        # Greater-than-or-equal: >=8.1
        match = re.match(r'^>=\s*(\d+\.\d+)', constraint)
        if match:
            min_version = match.group(1)
            # Check for upper bound: >=8.1 <8.4
            upper_match = re.search(r'<\s*(\d+)\.(\d+)', constraint)
            candidates = [
                v for v in self.SUPPORTED_VERSIONS
                if self._version_tuple(v) >= self._version_tuple(min_version)
            ]
            if upper_match:
                upper = f"{upper_match.group(1)}.{upper_match.group(2)}"
                candidates = [
                    v for v in candidates
                    if self._version_tuple(v) < self._version_tuple(upper)
                ]
            if candidates:
                return candidates[-1]
            return None

        # Greater-than: >8.0
        match = re.match(r'^>\s*(\d+)\.(\d+)', constraint)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            min_version = f"{major}.{minor}"
            candidates = [
                v for v in self.SUPPORTED_VERSIONS
                if self._version_tuple(v) > self._version_tuple(min_version)
            ]
            if candidates:
                return candidates[-1]
            return None

        # If constraint has multiple parts separated by space/comma (AND constraints)
        # e.g., ">=7.3 <8.0" or ">=7.3,<8.0"
        parts = re.split(r'[,\s]+', constraint)
        if len(parts) > 1:
            min_v = None
            max_v = None
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                ge_match = re.match(r'^>=\s*(\d+\.\d+)', part)
                lt_match = re.match(r'^<\s*(\d+\.\d+)', part)
                gt_match = re.match(r'^>\s*(\d+\.\d+)', part)
                if ge_match:
                    min_v = ge_match.group(1)
                elif gt_match:
                    min_v = gt_match.group(1)
                elif lt_match:
                    max_v = lt_match.group(1)

            candidates = self.SUPPORTED_VERSIONS[:]
            if min_v:
                candidates = [v for v in candidates if self._version_tuple(v) >= self._version_tuple(min_v)]
            if max_v:
                candidates = [v for v in candidates if self._version_tuple(v) < self._version_tuple(max_v)]
            if candidates:
                return candidates[-1]

        return None

    def _version_tuple(self, version: str) -> Tuple[int, int]:
        """Convert '8.2' to (8, 2) for comparison."""
        parts = version.split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)

    def _highest_version(self, versions: List[str]) -> str:
        """Return the highest version from a list."""
        return sorted(versions, key=self._version_tuple)[-1]

    # ── Framework Detection ─────────────────────────────────────

    def detect_framework(self, deploy_path: str) -> Dict:
        """
        Detect which PHP framework is used.

        Returns dict with:
        - name: framework name (laravel, symfony, wordpress, codeigniter, etc.)
        - version: framework version (if detectable)
        - document_root_suffix: recommended doc root
        - writable_dirs: directories needing write permissions
        - post_deploy_commands: recommended post-deploy hooks
        - database_driver: likely database driver
        """
        composer_data = self._read_composer_json(deploy_path)

        # Check for Laravel
        if self._is_laravel(deploy_path, composer_data):
            return self._laravel_info(deploy_path, composer_data)

        # Check for Symfony
        if self._is_symfony(deploy_path, composer_data):
            return self._symfony_info(deploy_path, composer_data)

        # Check for WordPress
        if self._is_wordpress(deploy_path):
            return self._wordpress_info(deploy_path)

        # Check for CodeIgniter
        if self._is_codeigniter(deploy_path, composer_data):
            return self._codeigniter_info(deploy_path)

        # Check for Slim
        if self._is_slim(composer_data):
            return self._slim_info(deploy_path)

        # Generic PHP app
        return self._generic_info(deploy_path)

    def _is_laravel(self, path: str, composer_data: Optional[Dict]) -> bool:
        """Detect Laravel framework."""
        if composer_data:
            requires = composer_data.get("require", {})
            if "laravel/framework" in requires:
                return True
        # Check for artisan file
        return os.path.isfile(os.path.join(path, "artisan"))

    def _is_symfony(self, path: str, composer_data: Optional[Dict]) -> bool:
        """Detect Symfony framework."""
        if composer_data:
            requires = composer_data.get("require", {})
            if any(k.startswith("symfony/framework-bundle") for k in requires):
                return True
        return os.path.isfile(os.path.join(path, "bin", "console"))

    def _is_wordpress(self, path: str) -> bool:
        """Detect WordPress."""
        return os.path.isfile(os.path.join(path, "wp-config.php")) or \
               os.path.isfile(os.path.join(path, "wp-config-sample.php"))

    def _is_codeigniter(self, path: str, composer_data: Optional[Dict]) -> bool:
        """Detect CodeIgniter."""
        if composer_data:
            requires = composer_data.get("require", {})
            if "codeigniter4/framework" in requires:
                return True
        return os.path.isfile(os.path.join(path, "spark"))

    def _is_slim(self, composer_data: Optional[Dict]) -> bool:
        """Detect Slim framework."""
        if composer_data:
            requires = composer_data.get("require", {})
            return "slim/slim" in requires
        return False

    def _laravel_info(self, path: str, composer_data: Optional[Dict]) -> Dict:
        """Get Laravel-specific deployment info."""
        version = "unknown"
        if composer_data:
            lv = composer_data.get("require", {}).get("laravel/framework", "")
            version = lv.strip("^~>=<")

        # Detect database driver from .env or .env.example
        db_driver = self._detect_laravel_db_driver(path)

        info = {
            "name": "laravel",
            "version": version,
            "document_root_suffix": "/public",
            "writable_dirs": ["storage", "bootstrap/cache"],
            "post_deploy_commands": [],
            "database_driver": db_driver,
            "extra_extensions": ["tokenizer", "fileinfo", "bcmath"],
        }

        # Build smart post-deploy commands
        cmds = []
        cmds.append("cp -n .env.example .env || true")
        cmds.append("composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev")
        cmds.append("php artisan key:generate --force --no-interaction")

        # Database-specific commands
        if db_driver == "sqlite":
            cmds.append("touch database/database.sqlite")
            info["extra_extensions"].append("sqlite3")
        elif db_driver == "mysql":
            info["extra_extensions"].append("mysql")
        elif db_driver == "pgsql":
            info["extra_extensions"].append("pgsql")

        cmds.append("php artisan migrate --force --no-interaction")
        cmds.append("php artisan config:cache --no-interaction")
        cmds.append("php artisan route:cache --no-interaction")
        cmds.append("php artisan view:cache --no-interaction")

        info["post_deploy_commands"] = cmds
        return info

    def _symfony_info(self, path: str, composer_data: Optional[Dict]) -> Dict:
        """Get Symfony-specific deployment info."""
        return {
            "name": "symfony",
            "version": "unknown",
            "document_root_suffix": "/public",
            "writable_dirs": ["var/cache", "var/log"],
            "post_deploy_commands": [
                "composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev",
                "php bin/console cache:clear --env=prod --no-debug",
                "php bin/console assets:install public --no-interaction",
            ],
            "database_driver": self._detect_symfony_db_driver(path),
            "extra_extensions": ["intl", "tokenizer"],
        }

    def _wordpress_info(self, path: str) -> Dict:
        """Get WordPress-specific deployment info."""
        return {
            "name": "wordpress",
            "version": "unknown",
            "document_root_suffix": "",
            "writable_dirs": ["wp-content/uploads", "wp-content/cache"],
            "post_deploy_commands": [],
            "database_driver": "mysql",
            "extra_extensions": ["mysql", "gd", "imagick", "intl"],
        }

    def _codeigniter_info(self, path: str) -> Dict:
        """Get CodeIgniter-specific deployment info."""
        return {
            "name": "codeigniter",
            "version": "unknown",
            "document_root_suffix": "/public",
            "writable_dirs": ["writable"],
            "post_deploy_commands": [
                "composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev",
            ],
            "database_driver": "mysql",
            "extra_extensions": ["intl", "mbstring"],
        }

    def _slim_info(self, path: str) -> Dict:
        """Get Slim-specific deployment info."""
        return {
            "name": "slim",
            "version": "unknown",
            "document_root_suffix": "/public",
            "writable_dirs": [],
            "post_deploy_commands": [
                "composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev",
            ],
            "database_driver": None,
            "extra_extensions": [],
        }

    def _generic_info(self, path: str) -> Dict:
        """Get generic PHP app deployment info."""
        # Try to guess document root
        doc_root = ""
        for candidate in ["public", "web", "htdocs", "www", "html"]:
            if os.path.isdir(os.path.join(path, candidate)):
                doc_root = f"/{candidate}"
                break

        # Detect database from config files
        db_driver = self._detect_generic_db_driver(path)
        extra_extensions = []
        if db_driver == "mysql":
            extra_extensions = ["mysql", "pdo_mysql"]
        elif db_driver in ("pgsql", "postgres"):
            extra_extensions = ["pgsql", "pdo_pgsql"]
        elif db_driver == "sqlite":
            extra_extensions = ["sqlite3"]

        # Auto-generate post_deploy if composer.json exists
        post_deploy = []
        if os.path.isfile(os.path.join(path, "composer.json")):
            post_deploy.append("composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev")

        # Discover SQL schema files for auto-import
        sql_files = self._discover_sql_files(path)
        db_names = []
        if db_driver == "mysql" and sql_files:
            # Parse SQL files to find CREATE DATABASE statements
            db_names = self._extract_db_names_from_sql(sql_files)

        return {
            "name": "generic",
            "version": "unknown",
            "document_root_suffix": doc_root,
            "writable_dirs": [],
            "post_deploy_commands": post_deploy,
            "database_driver": db_driver,
            "extra_extensions": extra_extensions,
            "sql_files": sql_files,
            "database_names": db_names,
        }

    def _discover_sql_files(self, path: str) -> List[str]:
        """
        Find .sql files in the repository root (not in vendor/).
        These are likely schema/migration files that need to be imported.
        """
        import glob
        sql_files = []
        # Check root-level SQL files first
        for f in glob.glob(os.path.join(path, "*.sql")):
            sql_files.append(f)
        # Check common schema directories
        for subdir in ["database", "db", "sql", "schema", "migrations"]:
            sql_dir = os.path.join(path, subdir)
            if os.path.isdir(sql_dir):
                for f in glob.glob(os.path.join(sql_dir, "*.sql")):
                    sql_files.append(f)

        if sql_files:
            names = [os.path.basename(f) for f in sql_files]
            self.log.info(f"Found SQL schema files: {', '.join(names)}")
        return sql_files

    def _extract_db_names_from_sql(self, sql_files: List[str]) -> List[str]:
        """
        Parse SQL files to extract database names from CREATE DATABASE statements.
        Returns list of database names that need to be created.
        """
        db_names = []
        for sql_file in sql_files:
            try:
                with open(sql_file, "r", errors="ignore") as f:
                    content = f.read()
                # Find CREATE DATABASE statements
                matches = re.findall(
                    r'create\s+database\s+(?:if\s+not\s+exists\s+)?[`"\']?(\w+)[`"\']?',
                    content, re.IGNORECASE
                )
                for name in matches:
                    if name not in db_names:
                        db_names.append(name)
                        self.log.info(f"Found database to create: {name}")
            except Exception:
                continue
        return db_names

    def _extract_table_sql(self, sql_files: List[str]) -> List[Dict]:
        """
        Parse SQL files and extract the database name + table creation statements,
        so the deployer can auto-import them.
        Returns list of dicts: [{"file": path, "database": name_or_None}]
        """
        results = []
        for sql_file in sql_files:
            try:
                with open(sql_file, "r", errors="ignore") as f:
                    content = f.read()
                # Find which database this SQL uses
                db_match = re.search(
                    r'create\s+database\s+(?:if\s+not\s+exists\s+)?[`"\']?(\w+)',
                    content, re.IGNORECASE
                )
                # Check if it has CREATE TABLE
                has_tables = bool(re.search(r'create\s+table', content, re.IGNORECASE))
                if has_tables or db_match:
                    results.append({
                        "file": sql_file,
                        "database": db_match.group(1) if db_match else None,
                    })
            except Exception:
                continue
        return results

    def _detect_generic_db_driver(self, path: str) -> Optional[str]:
        """
        Detect database driver for a generic PHP app by scanning config files
        for database connection patterns (PDO DSN, mysqli, pg_connect, etc.).
        """
        import glob

        # Patterns that indicate database usage
        mysql_patterns = [
            r'mysql:host=',             # PDO MySQL DSN
            r'mysqli_connect',          # mysqli procedural
            r'new\s+mysqli\s*\(',       # mysqli OOP
            r'mysql_connect',           # legacy mysql_connect
            r"'driver'\s*=>\s*'mysql'", # config arrays
            r"DB_CONNECTION.*mysql",    # .env style
        ]
        pgsql_patterns = [
            r'pgsql:host=',             # PDO PostgreSQL DSN
            r'pg_connect\s*\(',         # pg_connect
            r"'driver'\s*=>\s*'pgsql'",
            r"DB_CONNECTION.*pgsql",
        ]
        sqlite_patterns = [
            r'sqlite:',                  # PDO SQLite DSN
            r'sqlite3\s*\(',            # SQLite3 class
            r"'driver'\s*=>\s*'sqlite'",
            r"DB_CONNECTION.*sqlite",
        ]

        # Scan config files and PHP files in config/ directory
        scan_files = []
        # Direct config files
        for fname in [".env", ".env.example", ".env.production"]:
            fpath = os.path.join(path, fname)
            if os.path.isfile(fpath):
                scan_files.append(fpath)

        # Config directory PHP files
        config_dir = os.path.join(path, "config")
        if os.path.isdir(config_dir):
            for php_file in glob.glob(os.path.join(config_dir, "*.php")):
                scan_files.append(php_file)

        # Also check app/ directory (first level only) for database classes
        app_dir = os.path.join(path, "app")
        if os.path.isdir(app_dir):
            for root, dirs, files in os.walk(app_dir):
                for f in files:
                    if f.endswith(".php") and any(kw in f.lower() for kw in ["database", "db", "connection"]):
                        scan_files.append(os.path.join(root, f))
                # Limit depth to 3 levels
                depth = root.replace(app_dir, "").count(os.sep)
                if depth >= 3:
                    dirs.clear()

        # Read and scan all collected files
        combined_content = ""
        for fpath in scan_files:
            try:
                with open(fpath, "r", errors="ignore") as f:
                    combined_content += f.read() + "\n"
            except Exception:
                continue

        if not combined_content:
            return None

        # Check patterns (order: mysql first since it's most common)
        for pattern in mysql_patterns:
            if re.search(pattern, combined_content, re.IGNORECASE):
                self.log.info("Detected MySQL database usage in config files")
                return "mysql"

        for pattern in pgsql_patterns:
            if re.search(pattern, combined_content, re.IGNORECASE):
                self.log.info("Detected PostgreSQL database usage in config files")
                return "pgsql"

        for pattern in sqlite_patterns:
            if re.search(pattern, combined_content, re.IGNORECASE):
                self.log.info("Detected SQLite database usage in config files")
                return "sqlite"

        return None

    # ── Database Detection ──────────────────────────────────────

    def _detect_laravel_db_driver(self, path: str) -> str:
        """
        Detect the database driver a Laravel app is configured to use.

        Checks (in order):
        1. .env file (DB_CONNECTION=...)
        2. .env.example file
        3. config/database.php default
        4. Fall back to 'sqlite' (Laravel 11 default)
        """
        # Check .env first
        for env_file in [".env", ".env.example"]:
            env_path = os.path.join(path, env_file)
            if os.path.isfile(env_path):
                try:
                    with open(env_path, "r") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("DB_CONNECTION="):
                                driver = line.split("=", 1)[1].strip().strip("'\"")
                                if driver:
                                    self.log.info(f"Detected DB driver from {env_file}: {driver}")
                                    return driver
                except (IOError, PermissionError):
                    pass

        # Check config/database.php
        db_config_path = os.path.join(path, "config", "database.php")
        if os.path.isfile(db_config_path):
            try:
                with open(db_config_path, "r") as f:
                    content = f.read()
                    # Look for 'default' => env('DB_CONNECTION', 'sqlite')
                    match = re.search(
                        r"'default'\s*=>\s*env\s*\(\s*'DB_CONNECTION'\s*,\s*'(\w+)'\s*\)",
                        content
                    )
                    if match:
                        driver = match.group(1)
                        self.log.info(f"Detected DB driver from config/database.php: {driver}")
                        return driver
            except (IOError, PermissionError):
                pass

        # Laravel 11+ default is sqlite
        return "sqlite"

    def _detect_symfony_db_driver(self, path: str) -> Optional[str]:
        """Detect database driver for Symfony apps."""
        env_path = os.path.join(path, ".env")
        for check_path in [env_path, os.path.join(path, ".env.local")]:
            if os.path.isfile(check_path):
                try:
                    with open(check_path, "r") as f:
                        for line in f:
                            if "DATABASE_URL" in line:
                                if "mysql" in line:
                                    return "mysql"
                                elif "pgsql" in line or "postgres" in line:
                                    return "pgsql"
                                elif "sqlite" in line:
                                    return "sqlite"
                except (IOError, PermissionError):
                    pass
        return None

    # ── Extension Detection ─────────────────────────────────────

    def detect_required_extensions(self, deploy_path: str) -> List[str]:
        """
        Detect required PHP extensions from composer.json.

        Reads "require" for "ext-*" entries and merges with
        common framework-required extensions.
        """
        composer_data = self._read_composer_json(deploy_path)
        if not composer_data:
            return []

        extensions = []
        requires = composer_data.get("require", {})

        for key in requires:
            if key.startswith("ext-"):
                ext_name = key[4:]  # Remove "ext-" prefix
                extensions.append(ext_name)

        self.log.debug(f"Extensions from composer.json: {extensions}")
        return extensions

    # ── Helpers ──────────────────────────────────────────────────

    def _read_composer_json(self, deploy_path: str) -> Optional[Dict]:
        """Read and parse composer.json from the deploy path."""
        composer_path = os.path.join(deploy_path, "composer.json")
        if not os.path.isfile(composer_path):
            return None

        try:
            with open(composer_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, PermissionError) as e:
            self.log.warn(f"Could not read composer.json: {e}")
            return None
