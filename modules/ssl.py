"""
SSL/TLS Module - PHP-FPM Automation Agent
============================================
Manages SSL certificate provisioning via Let's Encrypt
and custom certificate installation.
"""

import os
import subprocess
from typing import Dict, Optional, Tuple

from modules.logger import DeployLogger
from modules.system import SystemDetector


class SSLManager:
    """
    Manages SSL/TLS certificates:
    - Let's Encrypt via Certbot (auto-provisioning)
    - Custom certificate installation
    - Certificate validation
    - Auto-renewal setup
    """

    def __init__(self, system: SystemDetector, log: DeployLogger):
        self.system = system
        self.log = log

    def _run(self, cmd: str) -> Tuple[int, str, str]:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    def setup_ssl(self, config: Dict) -> bool:
        """
        Set up SSL for a service. Uses custom certs if provided,
        otherwise attempts Let's Encrypt.
        """
        domain = config["domain"]
        self.log.step(f"Setting up SSL for {domain}")

        # Check if custom certs are provided
        cert_path = config.get("ssl_cert_path")
        key_path = config.get("ssl_key_path")

        if cert_path and key_path:
            return self._setup_custom_ssl(cert_path, key_path, domain)
        else:
            return self._setup_letsencrypt(config)

    def _setup_custom_ssl(self, cert_path: str, key_path: str, domain: str) -> bool:
        """Validate and use custom SSL certificates."""
        if not os.path.exists(cert_path):
            self.log.error(f"SSL certificate not found: {cert_path}")
            return False
        if not os.path.exists(key_path):
            self.log.error(f"SSL key not found: {key_path}")
            return False

        # Validate cert matches key
        rc, cert_mod, _ = self._run(
            f"openssl x509 -noout -modulus -in '{cert_path}' | openssl md5"
        )
        rc2, key_mod, _ = self._run(
            f"openssl rsa -noout -modulus -in '{key_path}' | openssl md5"
        )

        if rc == 0 and rc2 == 0 and cert_mod == key_mod:
            self.log.success("SSL certificate and key validated (match)")
        else:
            self.log.warn("Could not verify cert/key match — proceeding anyway")

        # Check expiry
        rc, expiry, _ = self._run(
            f"openssl x509 -enddate -noout -in '{cert_path}'"
        )
        if rc == 0:
            self.log.info(f"SSL certificate expiry: {expiry}")

        return True

    def _setup_letsencrypt(self, config: Dict) -> bool:
        """Provision SSL via Let's Encrypt / Certbot."""
        domain = config["domain"]
        web_server = config.get("web_server", "nginx")

        # Check if cert already exists
        cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        if os.path.exists(cert_path):
            self.log.skip(f"Let's Encrypt cert already exists for {domain}")
            config["ssl_cert_path"] = cert_path
            config["ssl_key_path"] = f"/etc/letsencrypt/live/{domain}/privkey.pem"
            return True

        # Check if certbot is available
        if not self.system._cmd_exists("certbot"):
            self.log.warn(
                "Certbot not installed. Install it and run: "
                f"certbot --{web_server} -d {domain}"
            )
            self.log.warn("Skipping SSL provisioning — deploy will work without SSL")
            return False

        # Run certbot
        self.log.info(f"Requesting Let's Encrypt certificate for {domain}")
        plugin = "nginx" if web_server == "nginx" else "apache"
        rc, out, err = self._run(
            f"certbot --{plugin} -d {domain} -d www.{domain} "
            f"--non-interactive --agree-tos --redirect "
            f"--email webmaster@{domain}"
        )

        if rc != 0:
            self.log.error(f"Certbot failed: {err}")
            self.log.warn("SSL provisioning failed — service will run on HTTP")
            return False

        # Set paths in config
        config["ssl_cert_path"] = cert_path
        config["ssl_key_path"] = f"/etc/letsencrypt/live/{domain}/privkey.pem"

        # Ensure auto-renewal timer is active
        self._run("systemctl enable certbot.timer 2>/dev/null")
        self._run("systemctl start certbot.timer 2>/dev/null")

        self.log.success(f"SSL certificate provisioned for {domain}")
        return True

    def check_certificate_expiry(self, domain: str) -> Optional[str]:
        """Check when a certificate expires."""
        cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
        if not os.path.exists(cert_path):
            return None
        rc, out, _ = self._run(
            f"openssl x509 -enddate -noout -in '{cert_path}'"
        )
        return out if rc == 0 else None
