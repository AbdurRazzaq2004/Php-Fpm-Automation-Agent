"""
Next.js Runtime - Server-Side Rendered React applications
==========================================================
Extends NodeRuntime for Next.js-specific deployment:
- next.config.js detection
- next build & next start
- Static export detection (output: 'export')
- Standalone output handling
"""

import json
import os
import re
from typing import Dict, Optional

from modules.runtimes.node_runtime import NodeRuntime


class NextJSRuntime(NodeRuntime):

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        """Detect Node.js version needed for Next.js."""
        # Next.js needs a minimum Node version; otherwise use parent detection
        node_ver = super().detect_version(deploy_path, configured_version)
        # Next.js 14+ requires Node 18+
        if int(node_ver.split(".")[0]) < 18:
            self.log.info("Next.js requires Node 18+, upgrading target version")
            return "20"
        return node_ver

    def install_dependencies(self, config: Dict) -> bool:
        """Install dependencies (all, not production-only, for Next.js build)."""
        deploy_path = config["deploy_path"]
        pm = config.get("package_manager") or self._detect_package_manager(deploy_path)

        self.log.step(f"Installing Next.js dependencies ({pm})")

        if not os.path.isfile(os.path.join(deploy_path, "package.json")):
            self.log.info("No package.json found — skipping")
            return True

        # Install package manager if needed
        if pm == "yarn" and not self._cmd_exists("yarn"):
            self._run("npm install -g yarn", timeout=120)
        elif pm == "pnpm" and not self._cmd_exists("pnpm"):
            self._run("npm install -g pnpm", timeout=120)

        # Install ALL deps (Next.js needs devDeps for build)
        install_cmds = {
            "npm": "npm ci 2>/dev/null || npm install",
            "yarn": "yarn install --frozen-lockfile 2>/dev/null || yarn install",
            "pnpm": "pnpm install --frozen-lockfile 2>/dev/null || pnpm install",
        }
        cmd = config.get("install_command") or install_cmds.get(pm, "npm install")
        rc, _, err = self._run(cmd, cwd=deploy_path, timeout=600)

        if rc == 0:
            self.log.success("Next.js dependencies installed")
        else:
            self.log.warn(f"Dependency installation issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        """Build the Next.js application."""
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        build_cmd = config.get("build_command", "npx next build")

        self.log.step("Building Next.js application")

        # Set NODE_ENV for build
        env = f"NODE_ENV=production"
        rc, out, err = self._run(f"{env} {build_cmd}", cwd=deploy_path, user=user, timeout=900)
        if rc != 0:
            self.log.error(f"Next.js build failed: {err[:300]}")
            return False

        self.log.success("Next.js build completed")

        # Detect build output type
        config["_nextjs_output_type"] = self._detect_output_type(deploy_path)
        self.log.info(f"Output type: {config['_nextjs_output_type']}")

        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        """Return start command based on Next.js output type."""
        if config.get("start_command"):
            return config["start_command"]

        deploy_path = config["deploy_path"]
        port = config.get("app_port", 3000)
        output_type = config.get("_nextjs_output_type", self._detect_output_type(deploy_path))

        if output_type == "standalone":
            # Standalone mode — run the server.js directly
            standalone_path = os.path.join(deploy_path, ".next", "standalone", "server.js")
            if os.path.isfile(standalone_path):
                return f"node .next/standalone/server.js"

        if output_type == "export":
            # Static export — served directly by web server, no process needed
            return None

        # Default: use next start
        pm = config.get("package_manager") or self._detect_package_manager(deploy_path)
        return f"npx next start -p {port}"

    def detect_framework(self, deploy_path: str) -> Dict:
        """Return Next.js-specific framework info."""
        output_type = self._detect_output_type(deploy_path)
        return {
            "name": "nextjs",
            "version": self._get_nextjs_version(deploy_path),
            "document_root_suffix": "/public" if output_type != "export" else "/out",
            "writable_dirs": [".next", "public"],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": "package.json",
            "start_command": None,
            "build_command": "npx next build",
            "extra_extensions": [],
            "sql_files": [],
            "output_type": output_type,
        }

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        """Return Next.js environment variables."""
        env = super().get_environment_vars(config)
        env["NEXT_TELEMETRY_DISABLED"] = "1"
        return env

    def needs_reverse_proxy(self) -> bool:
        """Static exports don't need a reverse proxy."""
        return True  # Caller should check _nextjs_output_type

    # ── Internal Helpers ─────────────────────────────────────────

    def _detect_output_type(self, deploy_path: str) -> str:
        """Detect Next.js output type: 'default', 'standalone', or 'export'."""
        # Check next.config.js / next.config.mjs / next.config.ts
        for config_name in ["next.config.js", "next.config.mjs", "next.config.ts"]:
            config_path = os.path.join(deploy_path, config_name)
            if os.path.isfile(config_path):
                try:
                    with open(config_path, "r", errors="ignore") as f:
                        content = f.read()
                    if re.search(r"""output\s*:\s*['"]standalone['"]""", content):
                        return "standalone"
                    if re.search(r"""output\s*:\s*['"]export['"]""", content):
                        return "export"
                except Exception:
                    pass

        # Check if .next/standalone exists (already built)
        if os.path.isdir(os.path.join(deploy_path, ".next", "standalone")):
            return "standalone"

        # Check if out/ exists (static export already built)
        if os.path.isdir(os.path.join(deploy_path, "out")):
            return "export"

        return "default"

    def _get_nextjs_version(self, deploy_path: str) -> str:
        """Get Next.js version from package.json."""
        pkg_path = os.path.join(deploy_path, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path) as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                ver = deps.get("next", "unknown")
                return ver.lstrip("^~>=<")
            except Exception:
                pass
        return "unknown"
