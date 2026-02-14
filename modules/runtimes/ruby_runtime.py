"""
Ruby Runtime - Rails, Sinatra, Hanami, etc.
=============================================
Handles Ruby application deployment:
- Ruby version detection from .ruby-version, Gemfile
- rbenv / system ruby installation
- Bundler dependency management
- Puma / Unicorn process management
- Rails/Sinatra framework detection
"""

import os
import re
from typing import Dict, Optional

from modules.runtimes.base import BaseRuntime


class RubyRuntime(BaseRuntime):

    FRAMEWORK_INDICATORS = {
        "rails": {
            "files": ["bin/rails", "config/application.rb"],
            "gems": ["rails"],
        },
        "sinatra": {"gems": ["sinatra"]},
        "hanami": {"gems": ["hanami"]},
        "grape": {"gems": ["grape"]},
        "padrino": {"gems": ["padrino"]},
    }

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        if configured_version:
            return configured_version

        # .ruby-version
        rv_path = os.path.join(deploy_path, ".ruby-version")
        if os.path.isfile(rv_path):
            try:
                with open(rv_path) as f:
                    ver = f.read().strip()
                    match = re.match(r"(\d+\.\d+(?:\.\d+)?)", ver)
                    if match:
                        return match.group(1)
            except Exception:
                pass

        # Gemfile
        gemfile = os.path.join(deploy_path, "Gemfile")
        if os.path.isfile(gemfile):
            try:
                with open(gemfile) as f:
                    content = f.read()
                match = re.search(r"ruby\s+['\"](\d+\.\d+(?:\.\d+)?)", content)
                if match:
                    return match.group(1)
            except Exception:
                pass

        return "3.3"

    def install(self, version: str, config: Dict) -> bool:
        self.log.step(f"Installing Ruby {version}")

        # Check if already installed
        rc, out, _ = self._run("ruby --version 2>/dev/null")
        if rc == 0 and version in out:
            self.log.info(f"✓ Ruby {version} already installed")
            return True

        if self.os_info["family"] == "debian":
            # Try system packages first
            self._run("apt-get update -qq")
            rc, _, _ = self._run(f"apt-get install -y ruby{version} ruby{version}-dev 2>/dev/null")
            if rc != 0:
                # Install via rbenv or brightbox PPA
                self._run("apt-get install -y software-properties-common")
                self._run("apt-add-repository -y ppa:brightbox/ruby-ng 2>/dev/null")
                self._run("apt-get update -qq")
                rc, _, _ = self._run(f"apt-get install -y ruby{version} ruby{version}-dev 2>/dev/null")
                if rc != 0:
                    # Fallback: install default ruby + build deps
                    self._apt_install([
                        "ruby-full", "ruby-dev", "build-essential",
                        "zlib1g-dev", "libssl-dev", "libreadline-dev",
                        "libyaml-dev", "libxml2-dev", "libxslt1-dev",
                    ])
        else:
            self._yum_install(["ruby", "ruby-devel", "gcc", "make"])

        # Install bundler
        self._run("gem install bundler --no-document", timeout=120)

        rc, out, _ = self._run("ruby --version")
        if rc == 0:
            self.log.success(f"Ruby installed: {out.strip()}")
            return True

        self.log.error("Ruby installation failed")
        return False

    def install_dependencies(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")

        self.log.step("Installing Ruby dependencies (Bundler)")

        if not os.path.isfile(os.path.join(deploy_path, "Gemfile")):
            self.log.info("No Gemfile found — skipping")
            return True

        # Configure bundler for deployment
        self._run("bundle config set --local deployment true", cwd=deploy_path)
        self._run("bundle config set --local without 'development test'", cwd=deploy_path)

        if config.get("install_command"):
            rc, _, err = self._run(config["install_command"], cwd=deploy_path, user=user, timeout=600)
        else:
            rc, _, err = self._run("bundle install --jobs 4", cwd=deploy_path, timeout=600)

        if rc == 0:
            self.log.success("Ruby dependencies installed")
        else:
            self.log.warn(f"Bundle install issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        framework = config.get("_framework_info", {}).get("name", "")

        if config.get("build_command"):
            self.log.step(f"Running build: {config['build_command']}")
            rc, _, err = self._run(config["build_command"], cwd=deploy_path, user=user, timeout=600)
            if rc != 0:
                self.log.warn(f"Build issues: {err[:200]}")
            return True

        if framework == "rails":
            self.log.step("Running Rails asset precompilation")
            # Precompile assets
            self._run(
                "bundle exec rails assets:precompile RAILS_ENV=production",
                cwd=deploy_path, user=user, timeout=300,
            )
            # Run migrations
            self._run(
                "bundle exec rails db:migrate RAILS_ENV=production",
                cwd=deploy_path, user=user, timeout=120,
            )

        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        if config.get("start_command"):
            return config["start_command"]

        deploy_path = config["deploy_path"]
        port = config.get("app_port", 3000)
        framework = config.get("_framework_info", {}).get("name", "")

        if framework == "rails":
            # Prefer puma
            if os.path.isfile(os.path.join(deploy_path, "config", "puma.rb")):
                return f"bundle exec puma -C config/puma.rb -b tcp://0.0.0.0:{port} -e production"
            return f"bundle exec rails server -b 0.0.0.0 -p {port} -e production"

        if framework == "sinatra":
            for entry in ["app.rb", "config.ru"]:
                if os.path.isfile(os.path.join(deploy_path, entry)):
                    return f"bundle exec rackup -p {port} -o 0.0.0.0"

        # Generic — try config.ru (Rack)
        if os.path.isfile(os.path.join(deploy_path, "config.ru")):
            return f"bundle exec rackup -p {port} -o 0.0.0.0"

        return None

    def detect_framework(self, deploy_path: str) -> Dict:
        gemfile_content = ""
        gemfile_path = os.path.join(deploy_path, "Gemfile")
        if os.path.isfile(gemfile_path):
            try:
                with open(gemfile_path, "r", errors="ignore") as f:
                    gemfile_content = f.read()
            except Exception:
                pass

        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            for fname in indicators.get("files", []):
                if os.path.isfile(os.path.join(deploy_path, fname)):
                    return self._get_framework_info(framework, deploy_path)
            for gem in indicators.get("gems", []):
                if re.search(rf"""gem\s+['"]{gem}['"]""", gemfile_content):
                    return self._get_framework_info(framework, deploy_path)

        return self._get_framework_info("generic-ruby", deploy_path)

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        env = {
            "RAILS_ENV": "production",
            "RACK_ENV": "production",
            "BUNDLE_WITHOUT": "development:test",
            "RAILS_LOG_TO_STDOUT": "true",
            "RAILS_SERVE_STATIC_FILES": "true",
        }
        if config.get("_framework_info", {}).get("name") == "rails":
            # Generate a secret key base if not set
            env["SECRET_KEY_BASE"] = "$(bundle exec rails secret 2>/dev/null || openssl rand -hex 64)"
        env.update(config.get("environment_vars", {}))
        return env

    def needs_reverse_proxy(self) -> bool:
        return True

    def _get_framework_info(self, framework: str, deploy_path: str) -> Dict:
        base = {
            "name": framework,
            "version": "unknown",
            "document_root_suffix": "/public",
            "writable_dirs": ["log", "tmp", "public/uploads"],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": None,
            "extra_extensions": [],
            "sql_files": [],
        }
        if framework == "rails":
            base["post_deploy_commands"] = [
                "bundle exec rails assets:precompile",
                "bundle exec rails db:migrate",
            ]
            db = self._detect_rails_db(deploy_path)
            if db:
                base["database_driver"] = db
        return base

    def _detect_rails_db(self, deploy_path: str) -> Optional[str]:
        db_yml = os.path.join(deploy_path, "config", "database.yml")
        if os.path.isfile(db_yml):
            try:
                with open(db_yml, "r", errors="ignore") as f:
                    content = f.read()
                if "postgresql" in content:
                    return "pgsql"
                if "mysql" in content:
                    return "mysql"
                if "sqlite" in content:
                    return "sqlite"
            except Exception:
                pass
        return None
