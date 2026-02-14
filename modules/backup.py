"""
Backup & Rollback Module - PHP-FPM Automation Agent
=====================================================
Creates timestamped backups before any destructive operation
and supports full rollback on deployment failure.
"""

import os
import shutil
import json
from datetime import datetime
from typing import Dict, List, Optional

from modules.logger import DeployLogger


class BackupManager:
    """
    Manages pre-deployment backups and rollback:
    - Config file backups (nginx, apache, php-fpm pools)
    - Application directory snapshots
    - Rollback manifest tracking
    - Automatic cleanup of old backups (retention policy)
    """

    BACKUP_ROOT = "/var/backups/php-deployer"
    MAX_BACKUPS_PER_SERVICE = 5

    def __init__(self, service_name: str, log: DeployLogger):
        self.service_name = service_name
        self.log = log
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.backup_dir = os.path.join(
            self.BACKUP_ROOT, service_name, self.timestamp
        )
        self.manifest: Dict = {
            "service_name": service_name,
            "timestamp": self.timestamp,
            "files": [],
            "directories": [],
        }
        self._initialized = False

    def _ensure_dir(self):
        """Create backup directory structure."""
        if not self._initialized:
            os.makedirs(self.backup_dir, exist_ok=True)
            self._initialized = True

    # ── File Backup ─────────────────────────────────────────────

    def backup_file(self, filepath: str) -> Optional[str]:
        """
        Backup a single file. Returns backup path or None if file doesn't exist.
        """
        if not os.path.exists(filepath):
            self.log.debug(f"Backup skip (not found): {filepath}")
            return None

        self._ensure_dir()
        # Preserve directory structure in backup
        rel_path = filepath.lstrip("/")
        backup_path = os.path.join(self.backup_dir, "files", rel_path)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)

        shutil.copy2(filepath, backup_path)
        self.manifest["files"].append({
            "original": filepath,
            "backup": backup_path,
        })
        self.log.debug(f"Backed up: {filepath}")
        return backup_path

    def backup_files(self, filepaths: List[str]) -> int:
        """Backup multiple files. Returns count of files backed up."""
        count = 0
        for fp in filepaths:
            if self.backup_file(fp):
                count += 1
        self.log.info(f"Backed up {count}/{len(filepaths)} files")
        return count

    # ── Directory Backup ────────────────────────────────────────

    def backup_directory(self, dirpath: str) -> Optional[str]:
        """
        Backup an entire directory tree. Returns backup path.
        Uses copytree for atomic snapshot.
        """
        if not os.path.isdir(dirpath):
            self.log.debug(f"Backup skip (not found): {dirpath}")
            return None

        self._ensure_dir()
        rel_path = dirpath.strip("/").replace("/", "_")
        backup_path = os.path.join(self.backup_dir, "dirs", rel_path)

        try:
            shutil.copytree(dirpath, backup_path, symlinks=True)
            self.manifest["directories"].append({
                "original": dirpath,
                "backup": backup_path,
            })
            self.log.info(f"Backed up directory: {dirpath}")
            return backup_path
        except Exception as e:
            self.log.warn(f"Failed to backup directory {dirpath}: {e}")
            return None

    # ── Rollback ────────────────────────────────────────────────

    def rollback(self) -> bool:
        """
        Restore all backed up files and directories to their original locations.
        Returns True if rollback succeeded.
        """
        self.log.banner("ROLLING BACK CHANGES")
        success = True

        # Restore files
        for entry in self.manifest["files"]:
            try:
                original = entry["original"]
                backup = entry["backup"]
                if os.path.exists(backup):
                    os.makedirs(os.path.dirname(original), exist_ok=True)
                    shutil.copy2(backup, original)
                    self.log.info(f"Restored: {original}")
            except Exception as e:
                self.log.error(f"Failed to restore {entry['original']}: {e}")
                success = False

        # Restore directories
        for entry in self.manifest["directories"]:
            try:
                original = entry["original"]
                backup = entry["backup"]
                if os.path.exists(backup):
                    if os.path.exists(original):
                        shutil.rmtree(original)
                    shutil.copytree(backup, original, symlinks=True)
                    self.log.info(f"Restored directory: {original}")
            except Exception as e:
                self.log.error(f"Failed to restore dir {entry['original']}: {e}")
                success = False

        return success

    # ── Manifest ────────────────────────────────────────────────

    def save_manifest(self):
        """Save backup manifest for later rollback reference."""
        self._ensure_dir()
        manifest_path = os.path.join(self.backup_dir, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(self.manifest, f, indent=2)
        self.log.debug(f"Manifest saved: {manifest_path}")

    def load_manifest(self, backup_timestamp: str) -> Optional[Dict]:
        """Load a manifest from a previous backup."""
        manifest_path = os.path.join(
            self.BACKUP_ROOT, self.service_name, backup_timestamp, "manifest.json"
        )
        if not os.path.exists(manifest_path):
            self.log.error(f"Manifest not found: {manifest_path}")
            return None
        with open(manifest_path, "r") as f:
            return json.load(f)

    # ── Cleanup ─────────────────────────────────────────────────

    def cleanup_old_backups(self):
        """Remove old backups beyond retention limit."""
        service_dir = os.path.join(self.BACKUP_ROOT, self.service_name)
        if not os.path.isdir(service_dir):
            return

        backups = sorted([
            d for d in os.listdir(service_dir)
            if os.path.isdir(os.path.join(service_dir, d))
        ])

        while len(backups) > self.MAX_BACKUPS_PER_SERVICE:
            old = backups.pop(0)
            old_path = os.path.join(service_dir, old)
            try:
                shutil.rmtree(old_path)
                self.log.debug(f"Cleaned up old backup: {old_path}")
            except Exception as e:
                self.log.warn(f"Failed to cleanup {old_path}: {e}")

    # ── List Backups ────────────────────────────────────────────

    def list_backups(self) -> List[Dict]:
        """List all backups for this service."""
        service_dir = os.path.join(self.BACKUP_ROOT, self.service_name)
        if not os.path.isdir(service_dir):
            return []

        backups = []
        for d in sorted(os.listdir(service_dir)):
            manifest_path = os.path.join(service_dir, d, "manifest.json")
            entry = {"timestamp": d, "path": os.path.join(service_dir, d)}
            if os.path.exists(manifest_path):
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)
                    entry["files_count"] = len(manifest.get("files", []))
                    entry["dirs_count"] = len(manifest.get("directories", []))
            backups.append(entry)

        return backups
