"""
Hooks Module - PHP-FPM Automation Agent
==========================================
Executes pre-deploy and post-deploy commands
defined in the service configuration.
"""

import os
import subprocess
from typing import Dict, List, Tuple

from modules.logger import DeployLogger


class HooksRunner:
    """
    Executes deployment hooks:
    - pre_deploy_commands: run before deployment
    - post_deploy_commands: run after code is deployed (e.g., composer install)
    """

    def __init__(self, log: DeployLogger):
        self.log = log

    def _run(self, cmd: str, cwd: str, user: str = "root") -> Tuple[int, str, str]:
        """Run a command in the deploy directory as the service user."""
        if user != "root":
            # Run as the service user
            full_cmd = f"su -s /bin/bash -c '{cmd}' {user}"
        else:
            full_cmd = cmd

        self.log.debug(f"hook exec [{user}]: {cmd}")
        try:
            result = subprocess.run(
                full_cmd, shell=True, capture_output=True, text=True,
                timeout=600, cwd=cwd
            )
            if result.stdout.strip():
                self.log.debug(f"stdout: {result.stdout.strip()[-500:]}")
            if result.returncode != 0 and result.stderr.strip():
                self.log.warn(f"stderr: {result.stderr.strip()[-500:]}")
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            self.log.error(f"Hook timed out: {cmd}")
            return 1, "", "timeout"

    def run_pre_deploy(self, config: Dict) -> bool:
        """Execute pre-deployment hooks."""
        commands = config.get("pre_deploy_commands", [])
        if not commands:
            return True

        self.log.step(f"Running pre-deploy hooks ({len(commands)} commands)")
        return self._run_hooks(commands, config)

    def run_post_deploy(self, config: Dict) -> bool:
        """Execute post-deployment hooks."""
        commands = config.get("post_deploy_commands", [])
        if not commands:
            return True

        self.log.step(f"Running post-deploy hooks ({len(commands)} commands)")
        return self._run_hooks(commands, config)

    def _run_hooks(self, commands: List[str], config: Dict) -> bool:
        """Execute a list of hook commands."""
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        success = True

        for i, cmd in enumerate(commands, 1):
            self.log.info(f"  [{i}/{len(commands)}] {cmd}")
            rc, out, err = self._run(cmd, cwd=deploy_path, user=user)
            if rc != 0:
                self.log.error(f"Hook failed (exit {rc}): {cmd}")
                success = False
                # Continue with remaining hooks unless it's a critical command
                if cmd.startswith("!"):  # Convention: prefix with ! for critical hooks
                    self.log.error("Critical hook failed — aborting")
                    return False

        return success

    def run_composer_install(self, config: Dict) -> bool:
        """Run composer install if composer.json exists."""
        deploy_path = config["deploy_path"]
        composer_json = os.path.join(deploy_path, "composer.json")

        if not os.path.isfile(composer_json):
            self.log.debug("No composer.json found — skipping composer install")
            return True

        self.log.info("Running composer install...")
        user = config.get("user", "root")
        rc, out, err = self._run(
            "composer install --no-interaction --prefer-dist --optimize-autoloader --no-dev",
            cwd=deploy_path,
            user=user
        )

        if rc != 0:
            self.log.warn(f"Composer install had issues: {err[-300:]}")
            # Don't fail deployment for composer issues
            return True

        self.log.success("Composer install completed")
        return True

    def setup_environment_file(self, config: Dict) -> bool:
        """Copy or symlink environment file to deploy path."""
        env_file = config.get("environment_file")
        deploy_path = config["deploy_path"]
        target_env = os.path.join(deploy_path, ".env")

        if not env_file:
            return True

        if not os.path.isfile(env_file):
            self.log.warn(f"Environment file not found: {env_file}")
            return True

        # Copy (not symlink, for security)
        try:
            import shutil
            shutil.copy2(env_file, target_env)
            os.chmod(target_env, 0o600)
            user = config.get("user", "root")
            group = config.get("group", "www-data")
            self._run(f"chown {user}:{group} '{target_env}'", cwd=deploy_path)
            self.log.success(f"Environment file deployed: {target_env}")
        except Exception as e:
            self.log.error(f"Failed to deploy environment file: {e}")
            return False

        return True

    def setup_cron_jobs(self, config: Dict) -> bool:
        """Set up cron jobs for the service."""
        cron_jobs = config.get("cron_jobs", [])
        if not cron_jobs:
            return True

        user = config.get("user", "root")
        service_name = config["service_name"]

        self.log.info(f"Setting up {len(cron_jobs)} cron job(s)")

        # Build crontab content with markers
        marker_start = f"# BEGIN php-deployer:{service_name}"
        marker_end = f"# END php-deployer:{service_name}"

        # Get existing crontab
        rc, existing_crontab, _ = self._run(f"crontab -l -u {user} 2>/dev/null", cwd="/tmp")

        # Remove old entries for this service
        lines = existing_crontab.splitlines() if existing_crontab else []
        new_lines = []
        inside_block = False
        for line in lines:
            if line.strip() == marker_start:
                inside_block = True
                continue
            elif line.strip() == marker_end:
                inside_block = False
                continue
            if not inside_block:
                new_lines.append(line)

        # Add new entries
        new_lines.append(marker_start)
        for job in cron_jobs:
            new_lines.append(job)
        new_lines.append(marker_end)

        # Write crontab
        crontab_content = "\n".join(new_lines) + "\n"
        rc, _, err = self._run(
            f"echo '{crontab_content}' | crontab -u {user} -",
            cwd="/tmp"
        )
        if rc != 0:
            self.log.warn(f"Failed to set crontab: {err}")
            return False

        self.log.success(f"Cron jobs configured for {user}")
        return True
