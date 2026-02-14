"""
Node.js Runtime - Express, Koa, Hapi, NestJS, etc.
====================================================
Handles Node.js application deployment:
- Node.js version detection from .nvmrc, package.json engines
- npm/yarn/pnpm dependency installation
- PM2/systemd process management
- Express/Koa/NestJS/Hapi framework detection
"""

import json
import os
import re
from typing import Dict, List, Optional

from modules.runtimes.base import BaseRuntime


class NodeRuntime(BaseRuntime):

    FRAMEWORK_INDICATORS = {
        "express": {"packages": ["express"]},
        "koa": {"packages": ["koa"]},
        "hapi": {"packages": ["@hapi/hapi", "hapi"]},
        "nestjs": {"packages": ["@nestjs/core"]},
        "fastify": {"packages": ["fastify"]},
        "adonis": {"packages": ["@adonisjs/core"]},
        "meteor": {"files": [".meteor"]},
        "strapi": {"packages": ["strapi", "@strapi/strapi"]},
    }

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        """Detect Node.js version from repo files."""
        if configured_version:
            return configured_version

        # Check .nvmrc
        nvmrc_path = os.path.join(deploy_path, ".nvmrc")
        if os.path.isfile(nvmrc_path):
            try:
                with open(nvmrc_path) as f:
                    ver = f.read().strip().lstrip("v")
                    match = re.match(r"(\d+)", ver)
                    if match:
                        self.log.info(f"Node version from .nvmrc: {match.group(1)}")
                        return match.group(1)
            except Exception:
                pass

        # Check .node-version
        nodever_path = os.path.join(deploy_path, ".node-version")
        if os.path.isfile(nodever_path):
            try:
                with open(nodever_path) as f:
                    ver = f.read().strip().lstrip("v")
                    match = re.match(r"(\d+)", ver)
                    if match:
                        return match.group(1)
            except Exception:
                pass

        # Check package.json engines
        pkg_path = os.path.join(deploy_path, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path) as f:
                    pkg = json.load(f)
                engines = pkg.get("engines", {}).get("node", "")
                match = re.search(r"(\d+)", engines)
                if match:
                    self.log.info(f"Node version from package.json engines: {match.group(1)}")
                    return match.group(1)
            except Exception:
                pass

        return "20"  # Default LTS

    def install(self, version: str, config: Dict) -> bool:
        """Install Node.js via NodeSource repository."""
        self.log.step(f"Installing Node.js {version}")

        # Check if already installed with correct version
        rc, out, _ = self._run("node --version 2>/dev/null")
        if rc == 0:
            match = re.search(r"v(\d+)", out)
            if match and match.group(1) == str(version):
                self.log.info(f"✓ Node.js v{version} already installed")
                return True

        # Install via NodeSource setup script
        if self.os_info["family"] == "debian":
            self.log.info("Setting up NodeSource repository...")
            # Download and run NodeSource setup
            rc, _, err = self._run(
                f"curl -fsSL https://deb.nodesource.com/setup_{version}.x | bash -",
                timeout=120,
            )
            if rc != 0:
                self.log.error(f"NodeSource setup failed: {err[:200]}")
                return False

            if not self._apt_install(["nodejs"]):
                return False
        else:
            # RHEL/CentOS
            self._run(f"curl -fsSL https://rpm.nodesource.com/setup_{version}.x | bash -", timeout=120)
            if not self._yum_install(["nodejs"]):
                return False

        # Install build tools (needed for native modules)
        if self.os_info["family"] == "debian":
            self._apt_install(["build-essential"])
        else:
            self._yum_install(["gcc-c++", "make"])

        # Verify installation
        rc, out, _ = self._run("node --version")
        if rc == 0:
            self.log.success(f"Node.js {out.strip()} installed")
            return True

        self.log.error("Node.js installation failed")
        return False

    def _detect_package_manager(self, deploy_path: str) -> str:
        """Detect npm/yarn/pnpm from lock files."""
        if os.path.isfile(os.path.join(deploy_path, "pnpm-lock.yaml")):
            return "pnpm"
        if os.path.isfile(os.path.join(deploy_path, "yarn.lock")):
            return "yarn"
        if os.path.isfile(os.path.join(deploy_path, "bun.lockb")):
            return "bun"
        return "npm"

    def install_dependencies(self, config: Dict) -> bool:
        """Install Node.js dependencies."""
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        pm = config.get("package_manager") or self._detect_package_manager(deploy_path)

        self.log.step(f"Installing Node.js dependencies ({pm})")

        # Ensure package.json exists
        if not os.path.isfile(os.path.join(deploy_path, "package.json")):
            self.log.info("No package.json found — skipping")
            return True

        # Install the package manager if not npm
        if pm == "yarn" and not self._cmd_exists("yarn"):
            self.log.info("Installing Yarn...")
            self._run("npm install -g yarn", timeout=120)
        elif pm == "pnpm" and not self._cmd_exists("pnpm"):
            self.log.info("Installing pnpm...")
            self._run("npm install -g pnpm", timeout=120)
        elif pm == "bun" and not self._cmd_exists("bun"):
            self.log.info("Installing Bun...")
            self._run("npm install -g bun", timeout=120)

        # Custom install command
        if config.get("install_command"):
            rc, _, err = self._run(config["install_command"], cwd=deploy_path, user=user, timeout=600)
        else:
            install_cmds = {
                "npm": "npm ci --production 2>/dev/null || npm install --production",
                "yarn": "yarn install --frozen-lockfile --production 2>/dev/null || yarn install --production",
                "pnpm": "pnpm install --frozen-lockfile --prod 2>/dev/null || pnpm install --prod",
                "bun": "bun install --production",
            }
            cmd = install_cmds.get(pm, "npm install --production")
            rc, _, err = self._run(cmd, cwd=deploy_path, timeout=600)

        if rc == 0:
            self.log.success(f"Node.js dependencies installed ({pm})")
        else:
            self.log.warn(f"Dependency installation had issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        """Run build command."""
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")

        build_cmd = config.get("build_command")
        if not build_cmd:
            # Check if there's a build script in package.json
            pkg_path = os.path.join(deploy_path, "package.json")
            if os.path.isfile(pkg_path):
                try:
                    with open(pkg_path) as f:
                        pkg = json.load(f)
                    if "build" in pkg.get("scripts", {}):
                        pm = config.get("package_manager") or self._detect_package_manager(deploy_path)
                        # Reinstall dev deps for build
                        self.log.info("Installing dev dependencies for build...")
                        reinstall_cmds = {
                            "npm": "npm install",
                            "yarn": "yarn install",
                            "pnpm": "pnpm install",
                            "bun": "bun install",
                        }
                        self._run(reinstall_cmds.get(pm, "npm install"), cwd=deploy_path, timeout=600)
                        build_cmd = f"{pm} run build"
                except Exception:
                    pass

        if not build_cmd:
            # No root build needed, but still check for client subdirectory
            self._build_client_app(deploy_path)
            return True

        self.log.step(f"Building application: {build_cmd}")
        rc, out, err = self._run(build_cmd, cwd=deploy_path, user=user, timeout=600)
        if rc != 0:
            self.log.warn(f"Build had issues: {err[:300]}")
        else:
            self.log.success("Build completed")

        # ── Handle client/frontend subdirectory (MERN/fullstack pattern) ──
        self._build_client_app(deploy_path)

        return True

    def _build_client_app(self, deploy_path: str) -> bool:
        """Build client/frontend app if present (MERN, MEAN, fullstack)."""
        client_dir = self._detect_client_dir(deploy_path)
        if not client_dir:
            return True

        client_path = os.path.join(deploy_path, client_dir)
        pm = self._detect_package_manager(client_path)

        self.log.step(f"Building client ({client_dir}/)")

        # Install client dependencies
        self.log.info(f"Installing {client_dir} dependencies ({pm})...")
        install_cmds = {
            "npm": "npm install",
            "yarn": "yarn install",
            "pnpm": "pnpm install",
            "bun": "bun install",
        }
        rc, _, err = self._run(
            install_cmds.get(pm, "npm install"),
            cwd=client_path, timeout=600,
        )
        if rc != 0:
            self.log.warn(f"Client dependency install had issues: {err[:200]}")

        # Build client
        build_cmd = f"{pm} run build"
        self.log.info(f"Running: {build_cmd} in {client_dir}/")
        rc, _, err = self._run(build_cmd, cwd=client_path, timeout=600)
        if rc != 0:
            self.log.warn(f"Client build had issues: {err[:300]}")
            return False

        # Verify build output exists
        for out_dir in ["build", "dist", "out", ".next"]:
            if os.path.isdir(os.path.join(client_path, out_dir)):
                self.log.success(f"Client build completed ({client_dir}/{out_dir}/)")
                return True

        self.log.success(f"Client build completed ({client_dir}/)")
        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        """Return the command to start the Node.js application."""
        if config.get("start_command"):
            return config["start_command"]

        deploy_path = config["deploy_path"]
        port = config.get("app_port", 3000)

        # Check package.json for start script
        pkg_path = os.path.join(deploy_path, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path) as f:
                    pkg = json.load(f)
                scripts = pkg.get("scripts", {})
                main = pkg.get("main")

                if "start" in scripts:
                    return scripts["start"]

                if main and os.path.isfile(os.path.join(deploy_path, main)):
                    return f"node {main}"
            except Exception:
                pass

        # Common entry points
        for entry in ["server.js", "app.js", "index.js", "main.js", "src/index.js", "dist/index.js"]:
            if os.path.isfile(os.path.join(deploy_path, entry)):
                return f"node {entry}"

        return None

    def detect_framework(self, deploy_path: str) -> Dict:
        """Detect Node.js framework from package.json."""
        deps = self._read_package_json_deps(deploy_path)

        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            # Check for indicator files
            for fname in indicators.get("files", []):
                fpath = os.path.join(deploy_path, fname)
                if os.path.isfile(fpath) or os.path.isdir(fpath):
                    return self._get_framework_info(framework, deploy_path)

            # Check packages
            for pkg in indicators.get("packages", []):
                if pkg in deps:
                    return self._get_framework_info(framework, deploy_path)

        return self._get_framework_info("generic-node", deploy_path)

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        """Return environment variables for Node.js apps."""
        env_vars = {
            "NODE_ENV": "production",
            "PORT": str(config.get("app_port", 3000)),
        }

        if config.get("node_max_memory"):
            env_vars["NODE_OPTIONS"] = f"--max-old-space-size={config['node_max_memory']}"

        env_vars.update(config.get("environment_vars", {}))
        return env_vars

    def needs_reverse_proxy(self) -> bool:
        return True

    # ── Helpers ──────────────────────────────────────────────────

    def _read_package_json_deps(self, deploy_path: str) -> Dict:
        """Read all dependencies from package.json."""
        pkg_path = os.path.join(deploy_path, "package.json")
        deps = {}
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path) as f:
                    pkg = json.load(f)
                deps.update(pkg.get("dependencies", {}))
                deps.update(pkg.get("devDependencies", {}))
            except Exception:
                pass
        return deps

    def _get_framework_info(self, framework: str, deploy_path: str) -> Dict:
        base = {
            "name": framework,
            "version": "unknown",
            "document_root_suffix": "/public",
            "writable_dirs": ["uploads", "logs", "tmp"],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": None,
            "extra_extensions": [],
            "sql_files": [],
        }

        if framework == "nestjs":
            base["build_command"] = "npm run build"
            base["entry_point"] = "dist/main.js"

        elif framework == "strapi":
            base["build_command"] = "npm run build"
            base["writable_dirs"] = ["public/uploads", ".tmp"]

        elif framework == "adonis":
            base["build_command"] = "node ace build --production"
            base["entry_point"] = "build/server.js"
            base["post_deploy_commands"] = ["node ace migration:run --force"]

        # ── Auto-detect database from package.json ──────────────
        db_driver = self._detect_node_db(deploy_path)
        if db_driver:
            base["database_driver"] = db_driver

        # ── Extract DB credentials from .env.example ────────────
        env_creds = self._extract_env_credentials(deploy_path)
        if env_creds:
            base["database_credentials"] = env_creds

        # ── Auto-detect app port ────────────────────────────────
        detected_port = self._detect_app_port(deploy_path)
        if detected_port:
            base["app_port"] = detected_port

        # ── Detect client/frontend directory (fullstack apps) ───
        client_dir = self._detect_client_dir(deploy_path)
        if client_dir:
            base["client_dir"] = client_dir

        return base

    def _detect_node_db(self, deploy_path: str) -> Optional[str]:
        """Detect database type from package.json dependencies."""
        deps = self._read_package_json_deps(deploy_path)

        # Direct DB driver packages
        driver_map = {
            "mysql2": "mysql",
            "mysql": "mysql",
            "pg": "pgsql",
            "pg-promise": "pgsql",
            "mongoose": "mongodb",
            "mongodb": "mongodb",
            "better-sqlite3": "sqlite",
            "sqlite3": "sqlite",
            "tedious": "mssql",
            "mssql": "mssql",
        }

        for pkg, driver in driver_map.items():
            if pkg in deps:
                self.log.info(f"Detected {driver} requirement from dependency: {pkg}")
                return driver

        # ORMs — need to detect DB type from config/env
        orm_packages = ["sequelize", "typeorm", "knex", "prisma", "@prisma/client", "objection"]
        has_orm = any(pkg in deps for pkg in orm_packages)
        if has_orm:
            env_driver = self._detect_db_from_env(deploy_path)
            if env_driver:
                return env_driver

        return None

    def _detect_db_from_env(self, deploy_path: str) -> Optional[str]:
        """Detect DB type from .env.example content."""
        for fname in [".env.example", ".env.sample", ".env.template"]:
            fpath = os.path.join(deploy_path, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath) as f:
                        content = f.read().lower()
                    if "mysql" in content or "db_port=3306" in content:
                        self.log.info(f"Detected mysql from {fname}")
                        return "mysql"
                    if "postgres" in content or "db_port=5432" in content:
                        self.log.info(f"Detected pgsql from {fname}")
                        return "pgsql"
                    if "mongodb" in content or "mongo_uri" in content:
                        return "mongodb"
                except Exception:
                    pass
        return None

    def _extract_env_credentials(self, deploy_path: str) -> Dict:
        """Extract DB credentials from .env.example or similar template."""
        for fname in [".env.example", ".env.sample", ".env.template"]:
            fpath = os.path.join(deploy_path, fname)
            if os.path.isfile(fpath):
                try:
                    creds = {}
                    with open(fpath) as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" not in line:
                                continue
                            key, _, val = line.partition("=")
                            key = key.strip().upper()
                            val = val.strip()

                            if key in ("DB_NAME", "DB_DATABASE", "MYSQL_DATABASE", "POSTGRES_DB"):
                                creds["dbname"] = val
                            elif key in ("DB_USER", "DB_USERNAME", "MYSQL_USER", "POSTGRES_USER"):
                                creds["user"] = val
                            elif key in ("DB_PASSWORD", "DB_PASS", "MYSQL_PASSWORD", "POSTGRES_PASSWORD"):
                                creds["password"] = val
                            elif key in ("DB_HOST", "MYSQL_HOST", "POSTGRES_HOST"):
                                creds["host"] = val
                            elif key in ("DB_PORT", "MYSQL_PORT", "POSTGRES_PORT"):
                                creds["port"] = val

                    if creds:
                        self.log.info(
                            f"Extracted DB credentials from {fname}: "
                            f"db={creds.get('dbname')}, user={creds.get('user')}"
                        )
                        return creds
                except Exception:
                    pass
        return {}

    def _detect_app_port(self, deploy_path: str) -> Optional[int]:
        """Detect app port from .env.example and source code."""
        port_vars = ["PORT", "APP_PORT", "SERVER_PORT", "HTTP_PORT"]

        # Check .env.example first
        for fname in [".env.example", ".env.sample", ".env.template"]:
            fpath = os.path.join(deploy_path, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath) as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            if "=" in line:
                                key, _, val = line.partition("=")
                                if key.strip() in port_vars:
                                    try:
                                        port = int(val.strip())
                                        if 1024 <= port <= 65535:
                                            self.log.info(f"Detected app port {port} from {fname}")
                                            return port
                                    except ValueError:
                                        pass
                except Exception:
                    pass

        # Fall back to scanning source files
        for entry in ["server/index.js", "server.js", "index.js", "app.js", "src/index.js"]:
            fpath = os.path.join(deploy_path, entry)
            if os.path.isfile(fpath):
                try:
                    with open(fpath) as f:
                        content = f.read()
                    match = re.search(r"(?:PORT|port)\s*(?:=|:|\|\|)\s*(\d{4,5})", content)
                    if match:
                        port = int(match.group(1))
                        if 1024 <= port <= 65535:
                            self.log.info(f"Detected app port {port} from {entry}")
                            return port
                except Exception:
                    pass

        return None

    def _detect_client_dir(self, deploy_path: str) -> Optional[str]:
        """Detect client/frontend subdirectory for fullstack apps (MERN, MEAN, etc.)."""
        for candidate in ["client", "frontend", "web"]:
            client_path = os.path.join(deploy_path, candidate)
            client_pkg = os.path.join(client_path, "package.json")
            if os.path.isdir(client_path) and os.path.isfile(client_pkg):
                try:
                    with open(client_pkg) as f:
                        pkg = json.load(f)
                    if "build" in pkg.get("scripts", {}):
                        self.log.info(f"Detected client directory: {candidate}/")
                        return candidate
                except Exception:
                    pass
        return None
