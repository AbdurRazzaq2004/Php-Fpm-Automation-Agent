"""
Permissions Module - PHP-FPM Automation Agent
================================================
Manages file ownership, permissions, and directory
structure for deployed services.
"""

import os
import subprocess
from typing import Dict, List, Tuple

from modules.logger import DeployLogger


class PermissionsManager:
    """
    Manages filesystem permissions for deployed services:
    - Correct ownership (service user + web server group)
    - Strict file/directory permissions
    - Writable directories (storage, cache, logs)
    - Shared directories (persist across deployments)
    """

    def __init__(self, log: DeployLogger):
        self.log = log

    def _run(self, cmd: str) -> Tuple[int, str, str]:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def setup_permissions(self, config: Dict) -> bool:
        """Set up all permissions for a deployed service."""
        deploy_path = config["deploy_path"]
        user = config["user"]
        group = config.get("group", "www-data")
        service_name = config["service_name"]

        self.log.step(f"Setting permissions for [{service_name}]")

        if not os.path.isdir(deploy_path):
            self.log.error(f"Deploy path does not exist: {deploy_path}")
            return False

        # 1. Set ownership: service_user:www-data
        self.log.info(f"Setting ownership: {user}:{group}")
        rc, _, err = self._run(f"chown -R {user}:{group} '{deploy_path}'")
        if rc != 0:
            self.log.error(f"Failed to set ownership: {err}")
            return False

        # 2. Set base permissions
        # Directories: 750 (owner rwx, group r-x, others ---)
        self.log.info("Setting directory permissions: 750")
        self._run(f"find '{deploy_path}' -type d -exec chmod 750 {{}} \\;")

        # Files: 640 (owner rw-, group r--, others ---)
        self.log.info("Setting file permissions: 640")
        self._run(f"find '{deploy_path}' -type f -exec chmod 640 {{}} \\;")

        # 3. Make scripts executable in vendor/bin, artisan, etc.
        for exec_path in [
            os.path.join(deploy_path, "artisan"),
            os.path.join(deploy_path, "vendor", "bin"),
            os.path.join(deploy_path, "bin"),
        ]:
            if os.path.exists(exec_path):
                if os.path.isdir(exec_path):
                    self._run(f"find '{exec_path}' -type f -exec chmod 750 {{}} \\;")
                else:
                    self._run(f"chmod 750 '{exec_path}'")

        # 4. Create and set up writable directories
        writable_dirs = config.get("writable_dirs", [])
        # Common framework writable dirs
        common_writable = [
            "storage", "storage/framework", "storage/framework/sessions",
            "storage/framework/views", "storage/framework/cache",
            "storage/logs", "bootstrap/cache", "cache",
            "tmp", "uploads",
        ]
        for wd in common_writable + writable_dirs:
            full_path = os.path.join(deploy_path, wd)
            if os.path.isdir(full_path):
                self.log.debug(f"Writable dir: {wd}")
                self._run(f"chmod -R 770 '{full_path}'")
                self._run(f"chown -R {user}:{group} '{full_path}'")

        # 5. Set up shared directories (preserved across deployments)
        shared_dirs = config.get("shared_dirs", [])
        shared_base = os.path.join(os.path.dirname(deploy_path), "shared", config["service_name"])
        for sd in shared_dirs:
            shared_path = os.path.join(shared_base, sd)
            target_path = os.path.join(deploy_path, sd)

            os.makedirs(shared_path, exist_ok=True)
            self._run(f"chown -R {user}:{group} '{shared_path}'")
            self._run(f"chmod -R 770 '{shared_path}'")

            # Create symlink
            if os.path.isdir(target_path) and not os.path.islink(target_path):
                # First deployment: move existing content to shared
                self._run(f"rsync -a '{target_path}/' '{shared_path}/'")
                self._run(f"rm -rf '{target_path}'")
            elif os.path.islink(target_path):
                os.unlink(target_path)

            os.symlink(shared_path, target_path)
            self.log.debug(f"Shared dir linked: {sd} → {shared_path}")

        # 6. Protect .env file
        env_file = os.path.join(deploy_path, ".env")
        if os.path.exists(env_file):
            self._run(f"chmod 600 '{env_file}'")
            self._run(f"chown {user}:{group} '{env_file}'")
            self.log.debug(".env file secured (600)")

        self.log.success("Permissions configured")
        return True

    def create_deploy_directories(self, config: Dict) -> bool:
        """Create the deployment directory structure."""
        deploy_path = config["deploy_path"]
        user = config["user"]
        group = config.get("group", "www-data")

        parent = os.path.dirname(deploy_path)
        os.makedirs(parent, exist_ok=True)
        self._run(f"chown root:root '{parent}'")
        self._run(f"chmod 755 '{parent}'")

        self.log.debug(f"Deploy directory ready: {deploy_path}")
        return True
