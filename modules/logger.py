"""
Logging Module - PHP-FPM Automation Agent
==========================================
Provides structured, timestamped logging with file and console output.
Supports per-service log isolation and log rotation awareness.
"""

import logging
import os
import sys
from datetime import datetime
from typing import Optional


class DeployLogger:
    """
    Production-grade logger with:
    - Console output (colored by level)
    - File output (structured, timestamped)
    - Per-service log context
    - Step tracking for audit trail
    """

    LOG_DIR = "/var/log/php-deployer"
    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[41m",  # Red background
        "RESET": "\033[0m",
        "BOLD": "\033[1m",
        "DIM": "\033[2m",
    }

    def __init__(self, service_name: Optional[str] = None, verbose: bool = False):
        self.service_name = service_name or "global"
        self.verbose = verbose
        self.step_count = 0
        self.warnings = []
        self.errors = []
        self._setup_log_dir()
        self._setup_logger()

    def _setup_log_dir(self):
        """Create log directory if it doesn't exist."""
        os.makedirs(self.LOG_DIR, exist_ok=True)
        # Also create per-session log directory
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = os.path.join(self.LOG_DIR, "sessions")
        os.makedirs(self.session_dir, exist_ok=True)

    def _setup_logger(self):
        """Configure logging handlers."""
        self.logger = logging.getLogger(f"php-deployer.{self.service_name}")
        self.logger.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        self.logger.handlers.clear()

        # File handler - always DEBUG level for full audit trail
        log_file = os.path.join(
            self.session_dir,
            f"{self.session_id}_{self.service_name}.log"
        )
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_fmt)
        self.logger.addHandler(file_handler)

        # Console handler - respects verbose flag
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG if self.verbose else logging.INFO)
        console_handler.setFormatter(self._ColorFormatter())
        self.logger.addHandler(console_handler)

        # Also log to combined session log
        combined_log = os.path.join(self.session_dir, f"{self.session_id}_combined.log")
        combined_handler = logging.FileHandler(combined_log)
        combined_handler.setLevel(logging.DEBUG)
        combined_handler.setFormatter(file_fmt)
        self.logger.addHandler(combined_handler)

    class _ColorFormatter(logging.Formatter):
        """Custom formatter with ANSI colors for console output."""
        COLORS = {
            logging.DEBUG: "\033[36m",
            logging.INFO: "\033[32m",
            logging.WARNING: "\033[33m",
            logging.ERROR: "\033[31m",
            logging.CRITICAL: "\033[41m",
        }
        RESET = "\033[0m"
        BOLD = "\033[1m"

        def format(self, record):
            color = self.COLORS.get(record.levelno, self.RESET)
            timestamp = datetime.now().strftime("%H:%M:%S")
            level = record.levelname.ljust(8)
            return f"{color}{self.BOLD}[{timestamp}] {level}{self.RESET} {record.getMessage()}"

    # ── Public API ──────────────────────────────────────────────

    def step(self, message: str):
        """Log a numbered deployment step."""
        self.step_count += 1
        self.logger.info(f"STEP {self.step_count:02d} ▸ {message}")

    def info(self, message: str):
        self.logger.info(message)

    def debug(self, message: str):
        self.logger.debug(message)

    def warn(self, message: str):
        self.warnings.append(message)
        self.logger.warning(message)

    def error(self, message: str):
        self.errors.append(message)
        self.logger.error(message)

    def critical(self, message: str):
        self.errors.append(message)
        self.logger.critical(message)

    def success(self, message: str):
        self.logger.info(f"✓ {message}")

    def skip(self, message: str):
        self.logger.info(f"⊘ SKIP: {message}")

    def banner(self, title: str):
        """Print a section banner."""
        line = "═" * 60
        self.logger.info(line)
        self.logger.info(f"  {title}")
        self.logger.info(line)

    def divider(self):
        self.logger.info("─" * 60)

    def summary(self):
        """Print deployment summary."""
        self.divider()
        self.logger.info(f"DEPLOYMENT SUMMARY for [{self.service_name}]")
        self.logger.info(f"  Steps executed:  {self.step_count}")
        self.logger.info(f"  Warnings:        {len(self.warnings)}")
        self.logger.info(f"  Errors:          {len(self.errors)}")
        if self.errors:
            self.logger.error("  ✗ DEPLOYMENT FAILED")
            for err in self.errors:
                self.logger.error(f"    → {err}")
        elif self.warnings:
            self.logger.warning("  ⚠ DEPLOYMENT COMPLETED WITH WARNINGS")
        else:
            self.logger.info("  ✓ DEPLOYMENT SUCCESSFUL")
        self.divider()

    def get_log_path(self) -> str:
        """Return path to the current log file."""
        return os.path.join(
            self.session_dir,
            f"{self.session_id}_{self.service_name}.log"
        )

    def has_errors(self) -> bool:
        return len(self.errors) > 0
