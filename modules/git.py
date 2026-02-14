"""
Git Clone Module - PHP-FPM Automation Agent
=============================================
Secure repository cloning with PAT support,
branch handling, and update detection.
"""

import os
import subprocess
import re
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, quote

from modules.logger import DeployLogger


class GitManager:
    """
    Manages Git operations:
    - Clone private repos using PAT tokens
    - Branch checkout and switching
    - Pull/update existing repos
    - Shallow clone for faster initial deployment
    - Secure credential handling (no tokens in logs)
    """

    def __init__(self, log: DeployLogger):
        self.log = log

    def _run(self, cmd: str, cwd: Optional[str] = None,
             env: Optional[Dict] = None) -> Tuple[int, str, str]:
        """Execute a command, masking sensitive data in logs."""
        # Mask PAT tokens in log output
        safe_cmd = re.sub(r"(https?://)[^@]+@", r"\1***@", cmd)
        self.log.debug(f"exec: {safe_cmd}")

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=300, cwd=cwd, env=run_env
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            self.log.error("Git operation timed out")
            return 1, "", "timeout"

    # ── URL Building ────────────────────────────────────────────

    def _build_auth_url(self, repo_url: str, pat_token: Optional[str] = None) -> str:
        """
        Build authenticated Git URL.
        Supports:
        - HTTPS with PAT: https://<token>@github.com/user/repo.git
        - HTTPS without auth: https://github.com/user/repo.git
        - SSH: git@github.com:user/repo.git (passed through)
        """
        if not pat_token:
            return repo_url

        # Only inject PAT for HTTPS URLs
        if repo_url.startswith("https://") or repo_url.startswith("http://"):
            parsed = urlparse(repo_url)
            safe_token = quote(pat_token, safe="")
            auth_url = f"{parsed.scheme}://{safe_token}@{parsed.hostname}"
            if parsed.port:
                auth_url += f":{parsed.port}"
            auth_url += parsed.path
            return auth_url

        # SSH URLs pass through unchanged
        return repo_url

    # ── Clone Operations ────────────────────────────────────────

    def clone(self, config: Dict) -> bool:
        """
        Clone a repository to the deploy path.
        Handles:
        - Fresh clone (deploy_path doesn't exist)
        - Update existing repo (pull latest)
        - Branch switching
        """
        repo_url = config["repo_url"]
        deploy_path = config["deploy_path"]
        branch = config.get("branch", "main")
        pat_token = config.get("pat_token")
        service_name = config["service_name"]

        self.log.step(f"Git operations for [{service_name}]")

        # Build authenticated URL
        auth_url = self._build_auth_url(repo_url, pat_token)

        # Check if deploy path already has a git repo
        git_dir = os.path.join(deploy_path, ".git")

        if os.path.isdir(git_dir):
            return self._update_existing(deploy_path, branch, auth_url)
        elif os.path.isdir(deploy_path) and os.listdir(deploy_path):
            # Directory exists and is not empty, but not a git repo
            self.log.warn(
                f"Deploy path exists and is not a git repo: {deploy_path}"
            )
            self.log.warn("Will clone to a temp location and sync files")
            return self._clone_and_sync(auth_url, deploy_path, branch)
        else:
            return self._fresh_clone(auth_url, deploy_path, branch)

    def _fresh_clone(self, url: str, path: str, branch: str) -> bool:
        """Perform a fresh git clone."""
        self.log.info(f"Cloning repository to {path} (branch: {branch})")

        # Create parent directory
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=True)

        # Clone with specific branch, single branch for efficiency
        rc, out, err = self._run(
            f"git clone --branch {branch} --single-branch --depth 50 '{url}' '{path}'",
            env={"GIT_TERMINAL_PROMPT": "0"}
        )

        if rc != 0:
            # Mask token in error message
            safe_err = re.sub(r"(https?://)[^@]+@", r"\1***@", err)
            self.log.error(f"Git clone failed: {safe_err}")
            return False

        self.log.success(f"Repository cloned successfully")
        return True

    def _update_existing(self, path: str, branch: str, url: str) -> bool:
        """Update an existing git repository."""
        self.log.info(f"Updating existing repository at {path}")

        # Check current branch
        rc, current_branch, _ = self._run("git rev-parse --abbrev-ref HEAD", cwd=path)
        if rc == 0:
            self.log.info(f"Current branch: {current_branch}")

        # Stash any local changes
        self._run("git stash --include-untracked", cwd=path)

        # Fetch latest
        rc, _, err = self._run(
            f"git fetch origin {branch}",
            cwd=path,
            env={"GIT_TERMINAL_PROMPT": "0"}
        )
        if rc != 0:
            safe_err = re.sub(r"(https?://)[^@]+@", r"\1***@", err)
            self.log.error(f"Git fetch failed: {safe_err}")
            return False

        # Checkout target branch if different
        if current_branch != branch:
            self.log.info(f"Switching to branch: {branch}")
            rc, _, err = self._run(f"git checkout {branch}", cwd=path)
            if rc != 0:
                rc, _, err = self._run(f"git checkout -b {branch} origin/{branch}", cwd=path)
                if rc != 0:
                    self.log.error(f"Failed to checkout branch {branch}: {err}")
                    return False

        # Pull latest changes
        rc, _, err = self._run(f"git pull origin {branch}", cwd=path)
        if rc != 0:
            # Try reset to origin
            self.log.warn("Pull failed, attempting hard reset to origin")
            rc, _, err = self._run(f"git reset --hard origin/{branch}", cwd=path)
            if rc != 0:
                self.log.error(f"Git reset failed: {err}")
                return False

        self.log.success("Repository updated")
        return True

    def _clone_and_sync(self, url: str, path: str, branch: str) -> bool:
        """Clone to temp dir and sync to existing path."""
        import tempfile
        import shutil

        tmp_dir = tempfile.mkdtemp(prefix="php-deployer-")
        try:
            rc, _, err = self._run(
                f"git clone --branch {branch} --single-branch --depth 50 '{url}' '{tmp_dir}/repo'",
                env={"GIT_TERMINAL_PROMPT": "0"}
            )
            if rc != 0:
                safe_err = re.sub(r"(https?://)[^@]+@", r"\1***@", err)
                self.log.error(f"Git clone failed: {safe_err}")
                return False

            # Sync files using rsync (preserves existing files not in repo)
            rc, _, err = self._run(
                f"rsync -a --delete --exclude='.env' --exclude='storage/' "
                f"--exclude='vendor/' --exclude='node_modules/' "
                f"'{tmp_dir}/repo/' '{path}/'"
            )
            if rc != 0:
                self.log.error(f"Sync failed: {err}")
                return False

            self.log.success("Repository synced to existing path")
            return True
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ── Utility Methods ─────────────────────────────────────────

    def get_current_commit(self, path: str) -> Optional[str]:
        """Get current commit hash."""
        rc, out, _ = self._run("git rev-parse --short HEAD", cwd=path)
        return out if rc == 0 else None

    def get_latest_tag(self, path: str) -> Optional[str]:
        """Get the latest git tag."""
        rc, out, _ = self._run("git describe --tags --abbrev=0 2>/dev/null", cwd=path)
        return out if rc == 0 else None

    def validate_remote(self, repo_url: str, pat_token: Optional[str] = None) -> bool:
        """Validate that a remote repository is accessible."""
        auth_url = self._build_auth_url(repo_url, pat_token)
        rc, _, _ = self._run(
            f"git ls-remote --exit-code '{auth_url}' HEAD",
            env={"GIT_TERMINAL_PROMPT": "0"}
        )
        return rc == 0
