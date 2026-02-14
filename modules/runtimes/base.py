"""
Base Runtime - Abstract base class for all language runtimes.
==============================================================
Defines the interface that every runtime must implement.
"""

import os
import subprocess
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from modules.logger import DeployLogger
from modules.system import SystemDetector


class BaseRuntime(ABC):
    """
    Abstract base class for language runtimes.

    Every runtime must implement:
    - detect_version()    → detect runtime version from repo files
    - install()           → install the runtime + package manager
    - install_deps()      → install app dependencies
    - build()             → run build step (if needed)
    - get_start_command() → return the command to start the app
    - detect_framework()  → detect which framework is used
    - get_framework_info() → return framework-specific config
    """

    def __init__(self, system: SystemDetector, log: DeployLogger):
        self.system = system
        self.log = log
        self.os_info = system.detect_os()

    def _run(self, cmd: str, cwd: Optional[str] = None,
             timeout: int = 300, user: Optional[str] = None) -> Tuple[int, str, str]:
        """Execute a shell command."""
        if user:
            cmd = f"su -s /bin/bash -c '{cmd}' {user}"
        self.log.debug(f"runtime exec: {cmd}")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return 1, "", "timeout"

    def _cmd_exists(self, cmd: str) -> bool:
        """Check if a command exists on the system."""
        rc, _, _ = self._run(f"which {cmd} 2>/dev/null")
        return rc == 0

    def _apt_install(self, packages: List[str]) -> bool:
        """Install packages via apt (Debian/Ubuntu)."""
        pkg_str = " ".join(packages)
        rc, _, err = self._run(
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {pkg_str}",
            timeout=600,
        )
        if rc != 0:
            self.log.error(f"Failed to install {pkg_str}: {err}")
            return False
        return True

    def _yum_install(self, packages: List[str]) -> bool:
        """Install packages via yum/dnf (RHEL/CentOS)."""
        pkg_str = " ".join(packages)
        mgr = "dnf" if self._cmd_exists("dnf") else "yum"
        rc, _, err = self._run(f"{mgr} install -y {pkg_str}", timeout=600)
        if rc != 0:
            self.log.error(f"Failed to install {pkg_str}: {err}")
            return False
        return True

    def _install_packages(self, packages: List[str]) -> bool:
        """Install packages using the system package manager."""
        if self.os_info["family"] == "debian":
            return self._apt_install(packages)
        return self._yum_install(packages)

    # ── Abstract Methods ────────────────────────────────────────

    @abstractmethod
    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        """
        Detect the runtime version from repository files.

        Args:
            deploy_path: Path to the cloned repository
            configured_version: User-specified version (takes priority)

        Returns:
            The version string to use (e.g., "3.11", "20", "8.2")
        """
        pass

    @abstractmethod
    def install(self, version: str, config: Dict) -> bool:
        """
        Install the language runtime and its package manager.

        Args:
            version: The runtime version to install
            config: Full service configuration dict

        Returns:
            True if installation succeeded
        """
        pass

    @abstractmethod
    def install_dependencies(self, config: Dict) -> bool:
        """
        Install application dependencies (npm install, pip install, etc.)

        Args:
            config: Full service configuration dict

        Returns:
            True if dependency installation succeeded
        """
        pass

    @abstractmethod
    def build(self, config: Dict) -> bool:
        """
        Run the build step for the application.
        Returns True if build succeeded or no build is needed.

        Args:
            config: Full service configuration dict
        """
        pass

    @abstractmethod
    def get_start_command(self, config: Dict) -> Optional[str]:
        """
        Return the command to start the application.
        For PHP, this returns None (FPM manages processes).
        For others, this returns the systemd ExecStart command.

        Args:
            config: Full service configuration dict
        """
        pass

    @abstractmethod
    def detect_framework(self, deploy_path: str) -> Dict:
        """
        Detect which framework is used and return framework info.

        Returns dict with:
        - name: framework name (e.g., "django", "express", "rails")
        - version: framework version (if detectable)
        - document_root_suffix: recommended doc root
        - writable_dirs: directories needing write permissions
        - post_deploy_commands: recommended post-deploy hooks
        - database_driver: likely database driver
        - entry_point: main entry file/module
        - start_command: how to start the app
        - build_command: how to build the app
        """
        pass

    @abstractmethod
    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        """
        Return environment variables needed for the application.
        These will be injected into the process manager config.

        Args:
            config: Full service configuration dict

        Returns:
            Dict of environment variable name → value
        """
        pass

    # ── Common Helpers ──────────────────────────────────────────

    def detect_package_manager(self, deploy_path: str) -> str:
        """
        Detect the package manager from repository files.
        Override in subclasses for language-specific detection.
        """
        return "unknown"

    def get_health_check_path(self, config: Dict) -> str:
        """Return the HTTP path for health checks. Override per language."""
        return "/"

    def get_document_root(self, deploy_path: str) -> str:
        """
        Detect the document root (for static files / web server).
        Override for frameworks that use subdirectories.
        """
        for candidate in ["public", "dist", "build", "static", "web", "www", "htdocs", "html", "out"]:
            if os.path.isdir(os.path.join(deploy_path, candidate)):
                return f"/{candidate}"
        return ""

    def needs_reverse_proxy(self) -> bool:
        """
        Whether this runtime needs a reverse proxy configuration.
        PHP uses FPM sockets (FastCGI), not HTTP reverse proxy.
        All other languages use HTTP reverse proxy to app_port.
        """
        return True  # Override to False in PHPRuntime
