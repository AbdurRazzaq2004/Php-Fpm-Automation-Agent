"""
Rust Runtime - Actix-web, Axum, Rocket, etc.
==============================================
Handles Rust application deployment:
- Rust version detection from rust-toolchain.toml
- Rustup / system installation
- Cargo build (release)
- Binary execution via systemd
"""

import os
import re
from typing import Dict, Optional

from modules.runtimes.base import BaseRuntime


class RustRuntime(BaseRuntime):

    FRAMEWORK_INDICATORS = {
        "actix-web": {"packages": ["actix-web"]},
        "axum": {"packages": ["axum"]},
        "rocket": {"packages": ["rocket"]},
        "warp": {"packages": ["warp"]},
        "tide": {"packages": ["tide"]},
    }

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        if configured_version:
            return configured_version

        # Check rust-toolchain.toml or rust-toolchain
        for fname in ["rust-toolchain.toml", "rust-toolchain"]:
            fpath = os.path.join(deploy_path, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath) as f:
                        content = f.read()
                    match = re.search(r'channel\s*=\s*"([^"]+)"', content)
                    if match:
                        return match.group(1)
                    # Simple format: just the version
                    ver = content.strip()
                    if re.match(r"\d+\.\d+", ver):
                        return ver
                except Exception:
                    pass

        return "stable"

    def install(self, version: str, config: Dict) -> bool:
        self.log.step(f"Installing Rust ({version})")

        # Check if already installed
        rc, out, _ = self._run("rustc --version 2>/dev/null")
        if rc == 0:
            self.log.info(f"✓ Rust already installed: {out.strip()}")
            return True

        # Install build deps
        if self.os_info["family"] == "debian":
            self._apt_install(["curl", "build-essential", "pkg-config", "libssl-dev"])
        else:
            self._yum_install(["curl", "gcc", "openssl-devel"])

        # Install via rustup
        self.log.info("Installing Rust via rustup...")
        rc, _, err = self._run(
            f"curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | "
            f"sh -s -- -y --default-toolchain {version}",
            timeout=300,
        )
        if rc != 0:
            self.log.error(f"Rustup installation failed: {err[:200]}")
            return False

        # Source cargo env
        self._run('echo "source /root/.cargo/env" >> /etc/profile.d/rust.sh')

        rc, out, _ = self._run("source /root/.cargo/env && rustc --version")
        if rc == 0:
            self.log.success(f"Rust installed: {out.strip()}")
            return True

        self.log.error("Rust installation failed")
        return False

    def install_dependencies(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]

        if not os.path.isfile(os.path.join(deploy_path, "Cargo.toml")):
            self.log.info("No Cargo.toml found — skipping")
            return True

        self.log.step("Fetching Rust dependencies")
        rc, _, err = self._run(
            "source /root/.cargo/env && cargo fetch",
            cwd=deploy_path, timeout=300,
        )
        if rc == 0:
            self.log.success("Rust dependencies fetched")
        else:
            self.log.warn(f"cargo fetch issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")

        build_cmd = config.get("build_command", "cargo build --release")

        self.log.step("Building Rust application (release)")
        rc, out, err = self._run(
            f"source /root/.cargo/env && {build_cmd}",
            cwd=deploy_path, user=user, timeout=1200,  # Rust builds can be slow
        )
        if rc != 0:
            self.log.error(f"Rust build failed: {err[:300]}")
            return False

        # Find the binary
        binary = self._find_binary(deploy_path)
        if binary:
            config["_binary_path"] = binary
            self.log.success(f"Built binary: {os.path.basename(binary)}")
        else:
            self.log.warn("No binary found in target/release/")

        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        if config.get("start_command"):
            return config["start_command"]

        binary = config.get("_binary_path") or self._find_binary(config["deploy_path"])
        if binary:
            return binary
        return None

    def detect_framework(self, deploy_path: str) -> Dict:
        cargo_content = ""
        cargo_path = os.path.join(deploy_path, "Cargo.toml")
        if os.path.isfile(cargo_path):
            try:
                with open(cargo_path, "r", errors="ignore") as f:
                    cargo_content = f.read()
            except Exception:
                pass

        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            for pkg in indicators.get("packages", []):
                if pkg in cargo_content:
                    return self._get_framework_info(framework)

        return self._get_framework_info("generic-rust")

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        env = {
            "RUST_LOG": "info",
            "PORT": str(config.get("app_port", 8080)),
            "HOST": "0.0.0.0",
        }
        env.update(config.get("environment_vars", {}))
        return env

    def needs_reverse_proxy(self) -> bool:
        return True

    def _find_binary(self, deploy_path: str) -> Optional[str]:
        release_dir = os.path.join(deploy_path, "target", "release")
        if not os.path.isdir(release_dir):
            return None

        # Get the package name from Cargo.toml
        cargo_path = os.path.join(deploy_path, "Cargo.toml")
        pkg_name = None
        if os.path.isfile(cargo_path):
            try:
                with open(cargo_path) as f:
                    content = f.read()
                match = re.search(r'name\s*=\s*"([^"]+)"', content)
                if match:
                    pkg_name = match.group(1).replace("-", "_")
            except Exception:
                pass

        # Look for executables in target/release
        for f in sorted(os.listdir(release_dir)):
            fpath = os.path.join(release_dir, f)
            if os.path.isfile(fpath) and os.access(fpath, os.X_OK):
                if not f.endswith((".d", ".so", ".rlib")):
                    if pkg_name and f == pkg_name:
                        return fpath
                    if not pkg_name:
                        return fpath

        # Return by package name even if not yet built
        if pkg_name:
            return os.path.join(release_dir, pkg_name)
        return None

    def _get_framework_info(self, framework: str) -> Dict:
        return {
            "name": framework,
            "version": "unknown",
            "document_root_suffix": "",
            "writable_dirs": ["logs", "data"],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": "cargo build --release",
            "extra_extensions": [],
            "sql_files": [],
        }
