"""
Python Runtime - Django, Flask, FastAPI, etc.
==============================================
Handles Python application deployment:
- Python version detection from Pipfile, pyproject.toml, .python-version
- virtualenv creation and management
- pip/poetry/pipenv dependency installation
- Gunicorn/Uvicorn process management
- Django/Flask/FastAPI framework detection
"""

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from modules.runtimes.base import BaseRuntime


class PythonRuntime(BaseRuntime):

    # Framework detection patterns
    FRAMEWORK_INDICATORS = {
        "django": {
            "files": ["manage.py"],
            "packages": ["django", "Django"],
            "settings_patterns": [r"DJANGO_SETTINGS_MODULE", r"django\.conf"],
        },
        "flask": {
            "files": ["wsgi.py"],
            "packages": ["flask", "Flask"],
        },
        "fastapi": {
            "packages": ["fastapi", "FastAPI"],
        },
        "tornado": {
            "packages": ["tornado"],
        },
        "starlette": {
            "packages": ["starlette"],
        },
        "sanic": {
            "packages": ["sanic"],
        },
    }

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        """Detect Python version from repo files."""
        if configured_version:
            return configured_version

        # Check .python-version (pyenv)
        pyver_path = os.path.join(deploy_path, ".python-version")
        if os.path.isfile(pyver_path):
            try:
                with open(pyver_path) as f:
                    ver = f.read().strip()
                    match = re.match(r"(\d+\.\d+)", ver)
                    if match:
                        self.log.info(f"Python version from .python-version: {match.group(1)}")
                        return match.group(1)
            except Exception:
                pass

        # Check Pipfile
        pipfile_path = os.path.join(deploy_path, "Pipfile")
        if os.path.isfile(pipfile_path):
            try:
                with open(pipfile_path) as f:
                    content = f.read()
                match = re.search(r'python_version\s*=\s*["\'](\d+\.\d+)', content)
                if match:
                    self.log.info(f"Python version from Pipfile: {match.group(1)}")
                    return match.group(1)
            except Exception:
                pass

        # Check pyproject.toml
        pyproject_path = os.path.join(deploy_path, "pyproject.toml")
        if os.path.isfile(pyproject_path):
            try:
                with open(pyproject_path) as f:
                    content = f.read()
                match = re.search(r'requires-python\s*=\s*["\']>=?(\d+\.\d+)', content)
                if match:
                    self.log.info(f"Python version from pyproject.toml: {match.group(1)}")
                    return match.group(1)
            except Exception:
                pass

        # Check runtime.txt (Heroku-style)
        runtime_path = os.path.join(deploy_path, "runtime.txt")
        if os.path.isfile(runtime_path):
            try:
                with open(runtime_path) as f:
                    content = f.read().strip()
                match = re.search(r'python-(\d+\.\d+)', content)
                if match:
                    self.log.info(f"Python version from runtime.txt: {match.group(1)}")
                    return match.group(1)
            except Exception:
                pass

        return "3.11"  # Default

    def install(self, version: str, config: Dict) -> bool:
        """Install Python runtime."""
        self.log.step(f"Installing Python {version}")

        # Check if already installed
        for py_cmd in [f"python{version}", f"python3.{version.split('.')[-1] if '.' in version else version}"]:
            if self._cmd_exists(py_cmd):
                self.log.info(f"✓ Python {version} already installed ({py_cmd})")
                config["_python_bin"] = py_cmd
                return True

        # Check if python3 exists and matches
        rc, out, _ = self._run("python3 --version 2>/dev/null")
        if rc == 0:
            match = re.search(r"(\d+\.\d+)", out)
            if match and match.group(1) == version:
                self.log.info(f"✓ Python {version} available as python3")
                config["_python_bin"] = "python3"
                return True

        # Install via system package manager
        if self.os_info["family"] == "debian":
            # Add deadsnakes PPA for non-default versions
            self._run("apt-get update -qq")
            rc, _, _ = self._run(f"apt-get install -y python{version} python{version}-venv python{version}-dev 2>/dev/null")
            if rc != 0:
                self.log.info("Adding deadsnakes PPA for Python...")
                self._run("apt-get install -y software-properties-common")
                self._run("add-apt-repository -y ppa:deadsnakes/ppa")
                self._run("apt-get update -qq")
                if not self._apt_install([
                    f"python{version}", f"python{version}-venv",
                    f"python{version}-dev", "python3-pip",
                ]):
                    return False
        else:
            # RHEL/CentOS
            if not self._yum_install([f"python{version.replace('.', '')}", f"python{version.replace('.', '')}-devel"]):
                return False

        # Verify
        for py_cmd in [f"python{version}", "python3"]:
            if self._cmd_exists(py_cmd):
                config["_python_bin"] = py_cmd
                self.log.success(f"Python {version} installed")
                return True

        self.log.error(f"Python {version} installation failed")
        return False

    def install_dependencies(self, config: Dict) -> bool:
        """Install Python dependencies using virtualenv."""
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        python_bin = config.get("_python_bin", "python3")
        venv_path = os.path.join(deploy_path, "venv")

        self.log.step("Installing Python dependencies")

        # Create virtualenv if not exists
        if not os.path.isdir(venv_path):
            self.log.info("Creating virtualenv...")
            rc, _, err = self._run(
                f"{python_bin} -m venv {venv_path}"
            )
            if rc != 0:
                self.log.error(f"Failed to create virtualenv: {err}")
                return False
            # Fix ownership
            self._run(f"chown -R {user}:{config.get('group', 'www-data')} {venv_path}")

        pip_bin = os.path.join(venv_path, "bin", "pip")

        # Upgrade pip
        self._run(f"{pip_bin} install --upgrade pip", cwd=deploy_path)

        # Detect and install dependencies
        if config.get("install_command"):
            # User-specified install command
            cmd = config["install_command"]
            if "pip install" in cmd and not cmd.startswith(pip_bin):
                cmd = cmd.replace("pip install", f"{pip_bin} install")
            rc, _, err = self._run(cmd, cwd=deploy_path, user=user, timeout=600)
        elif os.path.isfile(os.path.join(deploy_path, "requirements.txt")):
            rc, _, err = self._run(
                f"{pip_bin} install -r requirements.txt",
                cwd=deploy_path, timeout=600,
            )
        elif os.path.isfile(os.path.join(deploy_path, "pyproject.toml")):
            # Check if poetry is needed
            try:
                with open(os.path.join(deploy_path, "pyproject.toml")) as f:
                    if "tool.poetry" in f.read():
                        # Install poetry if not available
                        if not self._cmd_exists("poetry"):
                            self._run(f"{pip_bin} install poetry", timeout=120)
                        poetry_bin = os.path.join(venv_path, "bin", "poetry")
                        if not os.path.isfile(poetry_bin):
                            poetry_bin = "poetry"
                        rc, _, err = self._run(
                            f"{poetry_bin} install --no-interaction --no-dev",
                            cwd=deploy_path, timeout=600,
                        )
                    else:
                        rc, _, err = self._run(
                            f"{pip_bin} install .",
                            cwd=deploy_path, timeout=600,
                        )
            except Exception:
                rc, _, err = self._run(
                    f"{pip_bin} install .", cwd=deploy_path, timeout=600,
                )
        elif os.path.isfile(os.path.join(deploy_path, "Pipfile")):
            if not self._cmd_exists("pipenv"):
                self._run(f"{pip_bin} install pipenv", timeout=120)
            rc, _, err = self._run(
                "pipenv install --deploy --system",
                cwd=deploy_path, timeout=600,
            )
        elif os.path.isfile(os.path.join(deploy_path, "setup.py")):
            rc, _, err = self._run(
                f"{pip_bin} install .", cwd=deploy_path, timeout=600,
            )
        else:
            self.log.info("No dependency file found — skipping")
            return True

        if rc == 0:
            self.log.success("Python dependencies installed")
            return True
        else:
            self.log.warn(f"Dependency installation had issues: {err[:200]}")
            return True  # Continue — partial installs may still work

    def build(self, config: Dict) -> bool:
        """Run build command if specified."""
        build_cmd = config.get("build_command")
        if not build_cmd:
            return True  # No build needed for most Python apps

        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        venv_path = os.path.join(deploy_path, "venv")

        self.log.step("Running build command")
        # Prepend venv activation
        full_cmd = f"source {venv_path}/bin/activate && {build_cmd}"
        rc, out, err = self._run(f"bash -c '{full_cmd}'", cwd=deploy_path, user=user)
        if rc != 0:
            self.log.warn(f"Build had issues: {err[:200]}")
        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        """Return the command to start the Python application."""
        if config.get("start_command"):
            return config["start_command"]

        deploy_path = config["deploy_path"]
        venv_path = os.path.join(deploy_path, "venv")
        venv_bin = os.path.join(venv_path, "bin")
        port = config.get("app_port", 8000)

        framework = config.get("_framework_info", {}).get("name", "")

        # Django
        if framework == "django" or os.path.isfile(os.path.join(deploy_path, "manage.py")):
            wsgi_module = self._find_django_wsgi(deploy_path)
            if wsgi_module:
                if os.path.isfile(os.path.join(venv_bin, "gunicorn")):
                    return f"{venv_bin}/gunicorn {wsgi_module} --bind 0.0.0.0:{port} --workers 3"
                return f"{venv_bin}/python manage.py runserver 0.0.0.0:{port}"

        # FastAPI / Starlette
        if framework in ("fastapi", "starlette"):
            app_module = self._find_asgi_app(deploy_path)
            if app_module and os.path.isfile(os.path.join(venv_bin, "uvicorn")):
                return f"{venv_bin}/uvicorn {app_module} --host 0.0.0.0 --port {port} --workers 3"

        # Flask
        if framework == "flask":
            app_module = self._find_flask_app(deploy_path)
            if os.path.isfile(os.path.join(venv_bin, "gunicorn")):
                return f"{venv_bin}/gunicorn {app_module} --bind 0.0.0.0:{port} --workers 3"

        # Generic: try gunicorn with common entry points
        if os.path.isfile(os.path.join(venv_bin, "gunicorn")):
            for wsgi in ["wsgi:application", "app:app", "main:app"]:
                return f"{venv_bin}/gunicorn {wsgi} --bind 0.0.0.0:{port} --workers 3"

        # Last resort: use python directly
        for entry in ["app.py", "main.py", "server.py", "run.py", "wsgi.py"]:
            if os.path.isfile(os.path.join(deploy_path, entry)):
                return f"{venv_bin}/python {entry}"

        return None

    def detect_framework(self, deploy_path: str) -> Dict:
        """Detect Python framework from repository files."""
        deps_content = self._read_dependencies(deploy_path)

        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            # Check indicator files
            for filename in indicators.get("files", []):
                if os.path.isfile(os.path.join(deploy_path, filename)):
                    return self._get_framework_info(framework, deploy_path)

            # Check package names in dependencies
            for pkg in indicators.get("packages", []):
                if pkg.lower() in deps_content.lower():
                    return self._get_framework_info(framework, deploy_path)

        return self._get_framework_info("generic-python", deploy_path)

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        """Return environment variables for Python apps."""
        deploy_path = config["deploy_path"]
        venv_path = os.path.join(deploy_path, "venv")
        framework = config.get("_framework_info", {}).get("name", "")

        env_vars = {
            "VIRTUAL_ENV": venv_path,
            "PATH": f"{venv_path}/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": deploy_path,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        }

        if framework == "django":
            wsgi_module = self._find_django_settings(deploy_path)
            if wsgi_module:
                env_vars["DJANGO_SETTINGS_MODULE"] = wsgi_module

        # Merge user-specified env vars (they take priority)
        env_vars.update(config.get("environment_vars", {}))

        return env_vars

    def needs_reverse_proxy(self) -> bool:
        return True

    # ── Internal Helpers ────────────────────────────────────────

    def _read_dependencies(self, deploy_path: str) -> str:
        """Read dependency files to detect packages."""
        content = ""
        for fname in ["requirements.txt", "Pipfile", "pyproject.toml", "setup.py", "setup.cfg"]:
            fpath = os.path.join(deploy_path, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content += f.read() + "\n"
                except Exception:
                    continue
        return content

    def _find_django_wsgi(self, deploy_path: str) -> Optional[str]:
        """Find Django WSGI module path."""
        # Look for wsgi.py in subdirectories
        for root, dirs, files in os.walk(deploy_path):
            if "wsgi.py" in files:
                rel = os.path.relpath(root, deploy_path)
                if rel == ".":
                    return "wsgi:application"
                module = rel.replace(os.sep, ".")
                return f"{module}.wsgi:application"
            depth = root.replace(deploy_path, "").count(os.sep)
            if depth >= 3:
                dirs.clear()
        return "wsgi:application"

    def _find_django_settings(self, deploy_path: str) -> Optional[str]:
        """Find Django settings module."""
        for root, dirs, files in os.walk(deploy_path):
            if "settings.py" in files:
                rel = os.path.relpath(root, deploy_path)
                if rel != ".":
                    return rel.replace(os.sep, ".") + ".settings"
            depth = root.replace(deploy_path, "").count(os.sep)
            if depth >= 3:
                dirs.clear()
        return None

    def _find_asgi_app(self, deploy_path: str) -> Optional[str]:
        """Find ASGI app module for FastAPI/Starlette."""
        for entry in ["main", "app", "server", "api"]:
            fpath = os.path.join(deploy_path, f"{entry}.py")
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content = f.read()
                    if "FastAPI" in content or "Starlette" in content:
                        # Find the app variable name
                        match = re.search(r'(\w+)\s*=\s*(?:FastAPI|Starlette)\s*\(', content)
                        var_name = match.group(1) if match else "app"
                        return f"{entry}:{var_name}"
                except Exception:
                    pass
        return "main:app"

    def _find_flask_app(self, deploy_path: str) -> Optional[str]:
        """Find Flask app module."""
        for entry in ["app", "main", "wsgi", "server", "run"]:
            fpath = os.path.join(deploy_path, f"{entry}.py")
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content = f.read()
                    if "Flask" in content:
                        match = re.search(r'(\w+)\s*=\s*Flask\s*\(', content)
                        var_name = match.group(1) if match else "app"
                        return f"{entry}:{var_name}"
                except Exception:
                    pass
        return "app:app"

    def _get_framework_info(self, framework: str, deploy_path: str) -> Dict:
        """Return framework-specific deployment info."""
        base = {
            "name": framework,
            "version": "unknown",
            "document_root_suffix": "",
            "writable_dirs": [],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": None,
            "extra_extensions": [],
            "sql_files": [],
        }

        if framework == "django":
            base["document_root_suffix"] = "/static"
            base["writable_dirs"] = ["media", "static"]
            base["post_deploy_commands"] = [
                "python manage.py collectstatic --noinput",
                "python manage.py migrate --noinput",
            ]
            base["entry_point"] = "manage.py"
            # Detect DB from settings
            db = self._detect_django_db(deploy_path)
            if db:
                base["database_driver"] = db

        elif framework == "flask":
            base["writable_dirs"] = ["instance", "uploads"]
            base["entry_point"] = "app.py"

        elif framework == "fastapi":
            base["entry_point"] = "main.py"

        # Generic DB detection for all Python frameworks (if not already set by Django)
        if not base["database_driver"]:
            db = self._detect_python_db(deploy_path)
            if db:
                base["database_driver"] = db

        # Extract database credentials from .env.example
        if base["database_driver"]:
            creds = self._extract_env_credentials(deploy_path)
            if creds:
                base["database_credentials"] = creds

        return base

    def _detect_django_db(self, deploy_path: str) -> Optional[str]:
        """Detect Django database backend."""
        for root, dirs, files in os.walk(deploy_path):
            if "settings.py" in files:
                try:
                    with open(os.path.join(root, "settings.py"), "r", errors="ignore") as f:
                        content = f.read()
                    if "postgresql" in content or "psycopg" in content:
                        return "pgsql"
                    if "mysql" in content:
                        return "mysql"
                    if "sqlite3" in content:
                        return "sqlite"
                except Exception:
                    pass
            depth = root.replace(deploy_path, "").count(os.sep)
            if depth >= 3:
                dirs.clear()
        return None

    def _detect_python_db(self, deploy_path: str) -> Optional[str]:
        """
        Detect database from Python dependency files (requirements.txt, Pipfile, etc.)
        and .env.example / config files.
        """
        deps_content = self._read_dependencies(deploy_path).lower()

        # Check for PostgreSQL drivers
        pg_indicators = ["psycopg2", "psycopg", "asyncpg", "aiopg"]
        for pkg in pg_indicators:
            if pkg in deps_content:
                self.log.info(f"Detected PostgreSQL requirement from dependency: {pkg}")
                return "pgsql"

        # Check for MySQL drivers
        mysql_indicators = ["pymysql", "mysqlclient", "mysql-connector", "aiomysql"]
        for pkg in mysql_indicators:
            if pkg in deps_content:
                self.log.info(f"Detected MySQL requirement from dependency: {pkg}")
                return "mysql"

        # Check .env.example for database indicators
        env_example = os.path.join(deploy_path, ".env.example")
        if os.path.isfile(env_example):
            try:
                with open(env_example, "r", errors="ignore") as f:
                    env_content = f.read().lower()
                if "postgresql" in env_content or "db_engine=postgresql" in env_content:
                    self.log.info("Detected PostgreSQL requirement from .env.example")
                    return "pgsql"
                if "mysql" in env_content or "db_engine=mysql" in env_content:
                    self.log.info("Detected MySQL requirement from .env.example")
                    return "mysql"
            except Exception:
                pass

        # Check config files for SQLAlchemy connection strings
        for cfg_file in ["config.py", "settings.py", "database.py"]:
            cfg_path = os.path.join(deploy_path, cfg_file)
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", errors="ignore") as f:
                        content = f.read().lower()
                    if "postgresql" in content or "psycopg" in content:
                        self.log.info(f"Detected PostgreSQL from {cfg_file}")
                        return "pgsql"
                    if "mysql" in content:
                        self.log.info(f"Detected MySQL from {cfg_file}")
                        return "mysql"
                except Exception:
                    pass

        # If SQLAlchemy is present but no specific driver found, check deeper
        if "sqlalchemy" in deps_content:
            self.log.info("SQLAlchemy found but no specific DB driver detected")

        return None

    def _extract_env_credentials(self, deploy_path: str) -> Dict:
        """
        Extract database credentials from .env.example or .env.sample files.
        Parses standard DB_* environment variables.
        """
        creds = {}
        env_files = [".env.example", ".env.sample", ".env.template", ".env.dist"]

        env_path = None
        for ef in env_files:
            p = os.path.join(deploy_path, ef)
            if os.path.isfile(p):
                env_path = p
                break

        if not env_path:
            return creds

        try:
            with open(env_path, "r", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            return creds

        env_vars = {}
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env_vars[key.strip().upper()] = value.strip().strip("'\"")

        # Map common env var names to credential keys
        mappings = {
            "dbname": ["DB_NAME", "DB_DATABASE", "DATABASE_NAME", "POSTGRES_DB", "MYSQL_DATABASE"],
            "user": ["DB_USER", "DB_USERNAME", "DATABASE_USER", "POSTGRES_USER", "MYSQL_USER"],
            "password": ["DB_PASSWORD", "DB_PASS", "DATABASE_PASSWORD", "POSTGRES_PASSWORD", "MYSQL_PASSWORD"],
            "host": ["DB_HOST", "DATABASE_HOST"],
            "port": ["DB_PORT", "DATABASE_PORT"],
        }

        for cred_key, env_keys in mappings.items():
            for env_key in env_keys:
                if env_key in env_vars:
                    val = env_vars[env_key]
                    # Skip placeholder values
                    if val and val not in ("your_password_here", "changeme", "secret", "password", ""):
                        creds[cred_key] = val
                    elif cred_key in ("dbname", "user", "host", "port") and val:
                        # Keep non-password defaults even if they look like placeholders
                        creds[cred_key] = val
                    break

        if creds:
            self.log.info(f"Extracted DB credentials from {os.path.basename(env_path)}: "
                          f"db={creds.get('dbname', '?')}, user={creds.get('user', '?')}")

        return creds
