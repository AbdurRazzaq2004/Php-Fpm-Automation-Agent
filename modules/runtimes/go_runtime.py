"""
Go Runtime - Go web applications & APIs
=========================================
Handles Go application deployment:
- Go version detection from go.mod
- Go installation via official tarball
- go build compilation
- Binary execution via systemd
"""

import os
import re
from typing import Dict, Optional

from modules.runtimes.base import BaseRuntime


class GoRuntime(BaseRuntime):

    FRAMEWORK_INDICATORS = {
        "gin": {"packages": ["github.com/gin-gonic/gin"]},
        "echo": {"packages": ["github.com/labstack/echo"]},
        "fiber": {"packages": ["github.com/gofiber/fiber"]},
        "chi": {"packages": ["github.com/go-chi/chi"]},
        "gorilla-mux": {"packages": ["github.com/gorilla/mux"]},
        "beego": {"packages": ["github.com/beego/beego"]},
    }

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        if configured_version:
            return configured_version

        go_mod = os.path.join(deploy_path, "go.mod")
        if os.path.isfile(go_mod):
            try:
                with open(go_mod) as f:
                    content = f.read()
                match = re.search(r"^go\s+(\d+\.\d+)", content, re.M)
                if match:
                    return match.group(1)
            except Exception:
                pass

        return "1.22"

    def install(self, version: str, config: Dict) -> bool:
        self.log.step(f"Installing Go {version}")

        # Check if installed
        rc, out, _ = self._run("go version 2>/dev/null")
        if rc == 0 and f"go{version}" in out:
            self.log.info(f"✓ Go {version} already installed")
            return True

        # Install via official tarball
        arch = "amd64"
        rc, uname, _ = self._run("uname -m")
        if rc == 0 and "aarch64" in uname:
            arch = "arm64"

        tarball = f"go{version}.linux-{arch}.tar.gz"
        url = f"https://go.dev/dl/{tarball}"

        self.log.info(f"Downloading Go {version}...")
        self._run("rm -rf /usr/local/go")
        rc, _, err = self._run(
            f"curl -fsSL {url} | tar -C /usr/local -xzf -",
            timeout=300,
        )
        if rc != 0:
            # Try with full minor version
            tarball = f"go{version}.0.linux-{arch}.tar.gz"
            url = f"https://go.dev/dl/{tarball}"
            rc, _, err = self._run(
                f"curl -fsSL {url} | tar -C /usr/local -xzf -",
                timeout=300,
            )
            if rc != 0:
                self.log.error(f"Go download failed: {err[:200]}")
                return False

        # Add to PATH
        self._run('echo "export PATH=$PATH:/usr/local/go/bin" >> /etc/profile.d/go.sh')
        self._run("chmod +x /etc/profile.d/go.sh")

        rc, out, _ = self._run("/usr/local/go/bin/go version")
        if rc == 0:
            self.log.success(f"Go installed: {out.strip()}")
            return True

        self.log.error("Go installation failed")
        return False

    def install_dependencies(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]

        self.log.step("Downloading Go modules")

        if not os.path.isfile(os.path.join(deploy_path, "go.mod")):
            self.log.info("No go.mod found — skipping")
            return True

        if config.get("install_command"):
            rc, _, err = self._run(config["install_command"], cwd=deploy_path, timeout=300)
        else:
            rc, _, err = self._run(
                "PATH=$PATH:/usr/local/go/bin go mod download",
                cwd=deploy_path, timeout=300,
            )

        if rc == 0:
            self.log.success("Go modules downloaded")
        else:
            self.log.warn(f"go mod download issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        service_name = config.get("domain", "app").replace(".", "-")

        build_cmd = config.get("build_command")
        if not build_cmd:
            # Determine binary name
            binary_name = config.get("entry_point", service_name)
            build_cmd = f"CGO_ENABLED=0 go build -ldflags='-s -w' -o {binary_name} ."

        self.log.step(f"Building Go application")
        rc, out, err = self._run(
            f"PATH=$PATH:/usr/local/go/bin {build_cmd}",
            cwd=deploy_path, timeout=600,
        )
        if rc != 0:
            self.log.error(f"Go build failed: {err[:300]}")
            return False

        # Make binary executable
        binary = config.get("entry_point", service_name)
        binary_path = os.path.join(deploy_path, binary)
        if os.path.isfile(binary_path):
            self._run(f"chmod +x {binary_path}")
            config["_binary_path"] = binary_path

        self.log.success("Go application built")
        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        if config.get("start_command"):
            return config["start_command"]

        deploy_path = config["deploy_path"]
        service_name = config.get("domain", "app").replace(".", "-")
        binary = config.get("entry_point", service_name)
        binary_path = os.path.join(deploy_path, binary)

        if os.path.isfile(binary_path):
            return binary_path

        # Look for any executable in deploy dir
        for f in os.listdir(deploy_path):
            fpath = os.path.join(deploy_path, f)
            if os.path.isfile(fpath) and os.access(fpath, os.X_OK):
                # Skip common non-app executables
                if f in ("Makefile", ".gitignore"):
                    continue
                return fpath

        return f"./{binary}"

    def detect_framework(self, deploy_path: str) -> Dict:
        go_mod_content = ""
        go_mod = os.path.join(deploy_path, "go.mod")
        if os.path.isfile(go_mod):
            try:
                with open(go_mod, "r", errors="ignore") as f:
                    go_mod_content = f.read()
            except Exception:
                pass

        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            for pkg in indicators.get("packages", []):
                if pkg in go_mod_content:
                    return self._get_framework_info(framework, deploy_path)

        return self._get_framework_info("generic-go", deploy_path)

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        env = {
            "GIN_MODE": "release",
            "PORT": str(config.get("app_port", 8080)),
            "GOPATH": "/root/go",
            "PATH": "/usr/local/go/bin:/root/go/bin:/usr/local/bin:/usr/bin:/bin",
        }
        env.update(config.get("environment_vars", {}))
        return env

    def needs_reverse_proxy(self) -> bool:
        return True

    def _get_framework_info(self, framework: str, deploy_path: str) -> Dict:
        return {
            "name": framework,
            "version": "unknown",
            "document_root_suffix": "",
            "writable_dirs": ["logs", "data", "tmp"],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": None,
            "extra_extensions": [],
            "sql_files": [],
        }
