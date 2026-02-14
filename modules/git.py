"""
Git Clone Module - PHP-FPM Automation Agent
=============================================
Secure repository cloning with PAT support,
smart branch handling, and update detection.

Smart Branch Features:
- Auto-detects default branch from remote (main vs master vs develop)
- Falls back gracefully when specified branch doesn't exist
- Lists available branches on failure for user guidance
"""

import os
import subprocess
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, quote

from modules.logger import DeployLogger


class GitManager:
    """
    Manages Git operations:
    - Clone private repos using PAT tokens
    - **Smart branch detection and fallback**
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

    def detect_default_branch(self, repo_url: str, pat_token: Optional[str] = None) -> str:
        """
        Detect the default branch of a remote repository.
        Uses `git ls-remote --symref` to find HEAD target.
        Falls back to checking main/master if that fails.
        """
        auth_url = self._build_auth_url(repo_url, pat_token)

        # Try ls-remote --symref to get the symbolic HEAD
        rc, out, _ = self._run(
            f"git ls-remote --symref '{auth_url}' HEAD",
            env={"GIT_TERMINAL_PROMPT": "0"}
        )
        if rc == 0 and out:
            # Parse: ref: refs/heads/main	HEAD
            match = re.search(r"ref: refs/heads/(\S+)\s+HEAD", out)
            if match:
                branch = match.group(1)
                self.log.info(f"Detected default branch from remote: {branch}")
                return branch

        # Fallback: check if main or master exists
        for candidate in ["main", "master"]:
            rc, _, _ = self._run(
                f"git ls-remote --exit-code '{auth_url}' refs/heads/{candidate}",
                env={"GIT_TERMINAL_PROMPT": "0"}
            )
            if rc == 0:
                self.log.info(f"Detected branch '{candidate}' exists on remote")
                return candidate

        self.log.warn("Could not detect default branch, using 'main'")
        return "main"

    def list_remote_branches(self, repo_url: str, pat_token: Optional[str] = None) -> List[str]:
        """List all branches available on the remote repository."""
        auth_url = self._build_auth_url(repo_url, pat_token)
        rc, out, _ = self._run(
            f"git ls-remote --heads '{auth_url}'",
            env={"GIT_TERMINAL_PROMPT": "0"}
        )
        if rc != 0 or not out:
            return []

        branches = []
        for line in out.strip().split("\n"):
            match = re.search(r"refs/heads/(.+)$", line)
            if match:
                branches.append(match.group(1))
        return branches

    def verify_branch_exists(self, repo_url: str, branch: str,
                             pat_token: Optional[str] = None) -> bool:
        """Check if a specific branch exists on the remote."""
        auth_url = self._build_auth_url(repo_url, pat_token)
        rc, _, _ = self._run(
            f"git ls-remote --exit-code '{auth_url}' refs/heads/{branch}",
            env={"GIT_TERMINAL_PROMPT": "0"}
        )
        return rc == 0

    def clone(self, config: Dict) -> bool:
        """
        Clone a repository to the deploy path.
        Handles:
        - Fresh clone (deploy_path doesn't exist)
        - Update existing repo (pull latest)
        - Branch switching
        - **Smart branch fallback when specified branch doesn't exist**
        """
        repo_url = config["repo_url"]
        deploy_path = config["deploy_path"]
        branch = config.get("branch", "main")
        pat_token = config.get("pat_token")
        service_name = config["service_name"]

        self.log.step(f"Git operations for [{service_name}]")

        # Build authenticated URL
        auth_url = self._build_auth_url(repo_url, pat_token)

        # ── Smart branch handling ───────────────────────────────
        # If the user specified the default "main" but it doesn't exist,
        # auto-detect the real default branch
        if not self.verify_branch_exists(repo_url, branch, pat_token):
            self.log.warn(f"Branch '{branch}' does not exist on remote!")
            
            # Auto-detect the default branch
            detected = self.detect_default_branch(repo_url, pat_token)
            if detected != branch:
                self.log.info(f"Switching to detected default branch: '{detected}'")
                config["branch"] = detected
                branch = detected
            else:
                # List available branches for debugging
                available = self.list_remote_branches(repo_url, pat_token)
                if available:
                    self.log.info(f"Available branches: {', '.join(available[:20])}")
                self.log.error(
                    f"Branch '{branch}' not found. Please specify a valid branch "
                    f"in your services.yml configuration."
                )
                return False

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

        # Mark directory as safe to avoid 'dubious ownership' errors
        # Use --replace-all to prevent duplicates on repeated deploys
        self._run(f"git config --global --replace-all safe.directory {path}")

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
