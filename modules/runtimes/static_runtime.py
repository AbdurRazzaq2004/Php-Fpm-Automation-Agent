"""
Static Site Runtime - React, Vue, Angular, Hugo, etc.
======================================================
Handles static site deployment:
- Framework detection (React/CRA, Vue/Vite, Angular, Svelte, Hugo, Jekyll, etc.)
- Build via npm/yarn or native tool
- Serves built files directly through web server (no app process needed)
"""

import json
import os
import re
from typing import Dict, Optional

from modules.runtimes.base import BaseRuntime


class StaticRuntime(BaseRuntime):

    FRAMEWORK_INDICATORS = {
        "create-react-app": {"packages": ["react-scripts"]},
        "vite": {"packages": ["vite"], "files": ["vite.config.js", "vite.config.ts"]},
        "vue-cli": {"packages": ["@vue/cli-service"]},
        "angular": {"packages": ["@angular/core"], "files": ["angular.json"]},
        "svelte": {"packages": ["svelte"], "files": ["svelte.config.js"]},
        "gatsby": {"packages": ["gatsby"]},
        "hugo": {"files": ["hugo.toml", "hugo.yaml", "config.toml"]},
        "jekyll": {"files": ["_config.yml", "Gemfile"], "gems": ["jekyll"]},
        "eleventy": {"packages": ["@11ty/eleventy"], "files": [".eleventy.js"]},
        "astro": {"packages": ["astro"], "files": ["astro.config.mjs"]},
    }

    # Default output directories per framework
    OUTPUT_DIRS = {
        "create-react-app": "build",
        "vite": "dist",
        "vue-cli": "dist",
        "angular": "dist",
        "svelte": "build",
        "gatsby": "public",
        "hugo": "public",
        "jekyll": "_site",
        "eleventy": "_site",
        "astro": "dist",
        "generic-static": "dist",
    }

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        """For static sites that need Node.js for building."""
        if configured_version:
            return configured_version

        # Check if Node.js is needed (npm-based static site)
        if os.path.isfile(os.path.join(deploy_path, "package.json")):
            # Check .nvmrc or package.json engines
            nvmrc = os.path.join(deploy_path, ".nvmrc")
            if os.path.isfile(nvmrc):
                try:
                    with open(nvmrc) as f:
                        ver = f.read().strip().lstrip("v")
                    match = re.match(r"(\d+)", ver)
                    if match:
                        return match.group(1)
                except Exception:
                    pass
            return "20"

        # Hugo version
        for cfg in ["hugo.toml", "hugo.yaml", "config.toml"]:
            if os.path.isfile(os.path.join(deploy_path, cfg)):
                return "latest"

        return "20"

    def install(self, version: str, config: Dict) -> bool:
        """Install build tools (Node.js or Hugo etc.)."""
        deploy_path = config["deploy_path"]
        framework = config.get("_framework_info", {}).get("name", "")

        if framework in ("hugo",):
            return self._install_hugo(config)
        elif framework in ("jekyll",):
            return self._install_jekyll(config)
        else:
            # npm-based site: install Node.js
            return self._install_node(version, config)

    def _install_node(self, version: str, config: Dict) -> bool:
        """Install Node.js for npm-based static sites."""
        self.log.step(f"Installing Node.js {version} for static site build")

        rc, out, _ = self._run("node --version 2>/dev/null")
        if rc == 0:
            match = re.search(r"v(\d+)", out)
            if match and match.group(1) == str(version):
                self.log.info(f"✓ Node.js v{version} already installed")
                return True

        if self.os_info["family"] == "debian":
            self._run(f"curl -fsSL https://deb.nodesource.com/setup_{version}.x | bash -", timeout=120)
            return self._apt_install(["nodejs"])
        else:
            self._run(f"curl -fsSL https://rpm.nodesource.com/setup_{version}.x | bash -", timeout=120)
            return self._yum_install(["nodejs"])

    def _install_hugo(self, config: Dict) -> bool:
        """Install Hugo static site generator."""
        self.log.step("Installing Hugo")
        if self._cmd_exists("hugo"):
            self.log.info("✓ Hugo already installed")
            return True

        if self.os_info["family"] == "debian":
            # Try snap first, then apt
            rc, _, _ = self._run("snap install hugo 2>/dev/null", timeout=120)
            if rc != 0:
                self._apt_install(["hugo"])
        else:
            self._yum_install(["hugo"])

        return self._cmd_exists("hugo")

    def _install_jekyll(self, config: Dict) -> bool:
        """Install Jekyll (Ruby-based)."""
        self.log.step("Installing Jekyll")
        if self.os_info["family"] == "debian":
            self._apt_install(["ruby-full", "build-essential", "zlib1g-dev"])
        else:
            self._yum_install(["ruby", "ruby-devel", "gcc", "make"])

        self._run("gem install bundler jekyll --no-document", timeout=120)
        return True

    def install_dependencies(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        framework = config.get("_framework_info", {}).get("name", "")

        self.log.step("Installing static site dependencies")

        if framework == "jekyll":
            if os.path.isfile(os.path.join(deploy_path, "Gemfile")):
                rc, _, err = self._run("bundle install", cwd=deploy_path, timeout=300)
                if rc == 0:
                    self.log.success("Jekyll dependencies installed")
                return True

        if not os.path.isfile(os.path.join(deploy_path, "package.json")):
            self.log.info("No package.json — skipping")
            return True

        # Detect package manager
        pm = self._detect_npm_pm(deploy_path)
        if pm == "yarn" and not self._cmd_exists("yarn"):
            self._run("npm install -g yarn", timeout=120)
        elif pm == "pnpm" and not self._cmd_exists("pnpm"):
            self._run("npm install -g pnpm", timeout=120)

        install_cmds = {
            "npm": "npm ci 2>/dev/null || npm install",
            "yarn": "yarn install --frozen-lockfile 2>/dev/null || yarn install",
            "pnpm": "pnpm install --frozen-lockfile 2>/dev/null || pnpm install",
        }
        cmd = config.get("install_command") or install_cmds.get(pm, "npm install")
        rc, _, err = self._run(cmd, cwd=deploy_path, timeout=600)

        if rc == 0:
            self.log.success("Dependencies installed")
        else:
            self.log.warn(f"Install issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        framework = config.get("_framework_info", {}).get("name", "")

        build_cmd = config.get("build_command")
        if not build_cmd:
            if framework == "hugo":
                build_cmd = "hugo --minify"
            elif framework == "jekyll":
                build_cmd = "bundle exec jekyll build"
            elif os.path.isfile(os.path.join(deploy_path, "package.json")):
                try:
                    with open(os.path.join(deploy_path, "package.json")) as f:
                        pkg = json.load(f)
                    if "build" in pkg.get("scripts", {}):
                        pm = self._detect_npm_pm(deploy_path)
                        build_cmd = f"{pm} run build"
                except Exception:
                    pass

        if not build_cmd:
            self.log.info("No build command found — assuming pre-built")
            return True

        self.log.step(f"Building static site: {build_cmd}")
        rc, out, err = self._run(
            f"NODE_ENV=production {build_cmd}",
            cwd=deploy_path, user=user, timeout=600,
        )
        if rc != 0:
            self.log.error(f"Static site build failed: {err[:300]}")
            return False

        # Detect output directory
        config["_output_dir"] = self._find_output_dir(deploy_path, framework)
        self.log.success(f"Static site built → {config['_output_dir']}")
        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        """Static sites don't need a running process — served by web server."""
        return None

    def detect_framework(self, deploy_path: str) -> Dict:
        # Check npm-based frameworks
        deps = {}
        pkg_path = os.path.join(deploy_path, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path) as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            except Exception:
                pass

        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            for fname in indicators.get("files", []):
                if os.path.isfile(os.path.join(deploy_path, fname)):
                    return self._get_framework_info(framework, deploy_path)
            for pkg_name in indicators.get("packages", []):
                if pkg_name in deps:
                    return self._get_framework_info(framework, deploy_path)

            # Check gems for Jekyll
            if "gems" in indicators:
                gemfile = os.path.join(deploy_path, "Gemfile")
                if os.path.isfile(gemfile):
                    try:
                        with open(gemfile, "r", errors="ignore") as f:
                            content = f.read()
                        for gem in indicators["gems"]:
                            if gem in content:
                                return self._get_framework_info(framework, deploy_path)
                    except Exception:
                        pass

        return self._get_framework_info("generic-static", deploy_path)

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        """Static sites typically don't need runtime env vars."""
        env = {"NODE_ENV": "production"}
        env.update(config.get("environment_vars", {}))
        return env

    def needs_reverse_proxy(self) -> bool:
        """Static sites are served directly by the web server — no reverse proxy."""
        return False

    # ── Helpers ──────────────────────────────────────────────────

    def _detect_npm_pm(self, deploy_path: str) -> str:
        if os.path.isfile(os.path.join(deploy_path, "pnpm-lock.yaml")):
            return "pnpm"
        if os.path.isfile(os.path.join(deploy_path, "yarn.lock")):
            return "yarn"
        return "npm"

    def _find_output_dir(self, deploy_path: str, framework: str) -> str:
        """Find the build output directory."""
        expected = self.OUTPUT_DIRS.get(framework, "dist")
        expected_path = os.path.join(deploy_path, expected)
        if os.path.isdir(expected_path):
            return expected_path

        # Check common output dirs
        for d in ["dist", "build", "public", "out", "_site"]:
            p = os.path.join(deploy_path, d)
            if os.path.isdir(p) and os.listdir(p):
                return p

        return os.path.join(deploy_path, "dist")

    def _get_framework_info(self, framework: str, deploy_path: str) -> Dict:
        output_dir = self.OUTPUT_DIRS.get(framework, "dist")
        return {
            "name": framework,
            "version": "unknown",
            "document_root_suffix": f"/{output_dir}",
            "writable_dirs": [],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": None,
            "extra_extensions": [],
            "sql_files": [],
            "output_dir": output_dir,
            "is_static": True,
        }
