"""
.NET Runtime - ASP.NET Core applications
==========================================
Handles .NET application deployment:
- .NET version detection from *.csproj, global.json
- .NET SDK installation (Microsoft packages)
- dotnet publish build
- Kestrel execution via systemd
"""

import os
import re
from typing import Dict, Optional

from modules.runtimes.base import BaseRuntime


class DotNetRuntime(BaseRuntime):

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        if configured_version:
            return configured_version

        # Check global.json
        global_json = os.path.join(deploy_path, "global.json")
        if os.path.isfile(global_json):
            try:
                import json
                with open(global_json) as f:
                    data = json.load(f)
                ver = data.get("sdk", {}).get("version", "")
                match = re.match(r"(\d+\.\d+)", ver)
                if match:
                    return match.group(1)
            except Exception:
                pass

        # Check .csproj files for TargetFramework
        for root, dirs, files in os.walk(deploy_path):
            for fname in files:
                if fname.endswith(".csproj"):
                    try:
                        with open(os.path.join(root, fname), "r", errors="ignore") as f:
                            content = f.read()
                        match = re.search(r"<TargetFramework>net(\d+\.\d+)", content)
                        if match:
                            return match.group(1)
                    except Exception:
                        pass
            depth = root.replace(deploy_path, "").count(os.sep)
            if depth >= 2:
                dirs.clear()

        return "8.0"

    def install(self, version: str, config: Dict) -> bool:
        self.log.step(f"Installing .NET SDK {version}")

        # Check if already installed
        rc, out, _ = self._run("dotnet --version 2>/dev/null")
        if rc == 0 and out.strip().startswith(version):
            self.log.info(f"✓ .NET SDK {version} already installed")
            return True

        if self.os_info["family"] == "debian":
            # Install Microsoft package repository
            self.log.info("Setting up Microsoft .NET repository...")
            codename = self.os_info.get("codename", "jammy")
            distro = self.os_info.get("distro_id", "ubuntu")

            self._run(
                f"curl -fsSL https://packages.microsoft.com/config/{distro}/{self.os_info.get('version', '22.04')}/packages-microsoft-prod.deb "
                f"-o /tmp/packages-microsoft-prod.deb && dpkg -i /tmp/packages-microsoft-prod.deb",
                timeout=120,
            )
            self._run("apt-get update -qq")
            if not self._apt_install([f"dotnet-sdk-{version}"]):
                # Try with install script
                self.log.info("Trying .NET install script...")
                self._run(
                    f"curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel {version}",
                    timeout=300,
                )
                # Add to PATH
                self._run('echo "export PATH=$HOME/.dotnet:$PATH" >> /etc/profile.d/dotnet.sh')
        else:
            self._yum_install([f"dotnet-sdk-{version}"])

        rc, out, _ = self._run("dotnet --version 2>/dev/null || $HOME/.dotnet/dotnet --version")
        if rc == 0:
            self.log.success(f".NET SDK installed: {out.strip()}")
            return True

        self.log.error(".NET SDK installation failed")
        return False

    def install_dependencies(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]

        self.log.step("Restoring .NET dependencies")

        # Find .csproj or .sln
        has_project = any(
            f.endswith((".csproj", ".sln", ".fsproj"))
            for f in os.listdir(deploy_path) if os.path.isfile(os.path.join(deploy_path, f))
        )
        if not has_project:
            # Check subdirectories
            for d in os.listdir(deploy_path):
                subdir = os.path.join(deploy_path, d)
                if os.path.isdir(subdir):
                    has_project = any(
                        f.endswith((".csproj", ".fsproj"))
                        for f in os.listdir(subdir) if os.path.isfile(os.path.join(subdir, f))
                    )
                    if has_project:
                        break

        if not has_project:
            self.log.info("No .NET project file found — skipping")
            return True

        if config.get("install_command"):
            rc, _, err = self._run(config["install_command"], cwd=deploy_path, timeout=600)
        else:
            rc, _, err = self._run("dotnet restore", cwd=deploy_path, timeout=300)

        if rc == 0:
            self.log.success(".NET dependencies restored")
        else:
            self.log.warn(f"dotnet restore issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        service_name = config.get("domain", "app").replace(".", "-")

        build_cmd = config.get("build_command")
        if not build_cmd:
            publish_dir = os.path.join(deploy_path, "publish")
            build_cmd = f"dotnet publish -c Release -o {publish_dir} --no-restore"

        self.log.step("Building .NET application")
        rc, out, err = self._run(build_cmd, cwd=deploy_path, user=user, timeout=600)
        if rc != 0:
            self.log.error(f".NET build failed: {err[:300]}")
            return False

        # Find the DLL
        dll = self._find_entry_dll(deploy_path)
        if dll:
            config["_dll_path"] = dll
            self.log.success(f"Published: {os.path.basename(dll)}")

        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        if config.get("start_command"):
            return config["start_command"]

        deploy_path = config["deploy_path"]
        port = config.get("app_port", 5000)

        dll = config.get("_dll_path") or self._find_entry_dll(deploy_path)
        if dll:
            return f"dotnet {dll} --urls http://0.0.0.0:{port}"

        return None

    def detect_framework(self, deploy_path: str) -> Dict:
        return {
            "name": "aspnet-core",
            "version": "unknown",
            "document_root_suffix": "/wwwroot",
            "writable_dirs": ["logs", "data", "wwwroot/uploads"],
            "post_deploy_commands": [],
            "database_driver": self._detect_db(deploy_path),
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": "dotnet publish -c Release",
            "extra_extensions": [],
            "sql_files": [],
        }

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        env = {
            "ASPNETCORE_ENVIRONMENT": "Production",
            "ASPNETCORE_URLS": f"http://0.0.0.0:{config.get('app_port', 5000)}",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        }
        env.update(config.get("environment_vars", {}))
        return env

    def needs_reverse_proxy(self) -> bool:
        return True

    # ── Helpers ──────────────────────────────────────────────────

    def _find_entry_dll(self, deploy_path: str) -> Optional[str]:
        """Find the main DLL in publish directory."""
        publish_dir = os.path.join(deploy_path, "publish")
        if not os.path.isdir(publish_dir):
            publish_dir = os.path.join(deploy_path, "bin", "Release")

        if not os.path.isdir(publish_dir):
            return None

        # Walk to find DLLs
        for root, dirs, files in os.walk(publish_dir):
            for f in files:
                if f.endswith(".dll") and not f.startswith("Microsoft.") and not f.startswith("System."):
                    # Check for corresponding runtimeconfig.json (entry point indicator)
                    base = f[:-4]
                    if f"{base}.runtimeconfig.json" in files:
                        return os.path.join(root, f)
            break  # Only check top level

        # Fallback: look for the project-named DLL
        for f in os.listdir(deploy_path):
            if f.endswith(".csproj"):
                proj_name = f[:-7]
                dll = os.path.join(publish_dir, f"{proj_name}.dll")
                if os.path.isfile(dll):
                    return dll
        return None

    def _detect_db(self, deploy_path: str) -> Optional[str]:
        # Check appsettings.json
        for fname in ["appsettings.json", "appsettings.Production.json"]:
            fpath = os.path.join(deploy_path, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content = f.read()
                    if "Npgsql" in content or "PostgreSQL" in content:
                        return "pgsql"
                    if "MySql" in content:
                        return "mysql"
                    if "SqlServer" in content:
                        return "mssql"
                except Exception:
                    pass
        return None
