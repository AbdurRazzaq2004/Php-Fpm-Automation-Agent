"""
Process Manager Module - Systemd & PM2 service management
============================================================
Creates and manages long-running application processes:
- systemd unit files for Python, Ruby, Go, Java, Rust, .NET
- PM2 ecosystem configs for Node.js / Next.js
- Service lifecycle: create, start, stop, restart, enable, status
"""

import json
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple


class ProcessManager:
    """Unified process manager for all non-PHP languages."""

    def __init__(self, log, os_info: dict):
        self.log = log
        self.os_info = os_info

    def _run(self, cmd: str, cwd: str = None, timeout: int = 120) -> Tuple[int, str, str]:
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=timeout,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return 1, "", "Command timed out"
        except Exception as e:
            return 1, "", str(e)

    # ── Systemd ──────────────────────────────────────────────────

    def create_systemd_service(self, config: Dict, runtime=None) -> bool:
        """Create a systemd unit file for the application."""
        service_name = config.get("systemd_service") or self._make_service_name(config)
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")
        group = config.get("group", "www-data")
        language = config.get("language", "")

        # Get start command
        start_cmd = config.get("start_command")
        if not start_cmd and runtime:
            start_cmd = runtime.get_start_command(config)

        if not start_cmd:
            self.log.error(f"No start command found for {service_name}")
            return False

        # Get environment variables
        env_vars = config.get("environment_vars", {})
        if runtime:
            env_vars = {**runtime.get_environment_vars(config), **env_vars}

        # Build unit file
        unit_content = self._build_systemd_unit(
            service_name=service_name,
            description=f"{config.get('domain', service_name)} ({language})",
            exec_start=start_cmd,
            working_directory=deploy_path,
            user=user,
            group=group,
            env_vars=env_vars,
            language=language,
            config=config,
        )

        unit_path = f"/etc/systemd/system/{service_name}.service"
        self.log.step(f"Creating systemd service: {service_name}")

        try:
            with open(unit_path, "w") as f:
                f.write(unit_content)
            self.log.info(f"Unit file written: {unit_path}")
        except Exception as e:
            self.log.error(f"Failed to write unit file: {e}")
            return False

        # Reload systemd and enable service
        self._run("systemctl daemon-reload")
        rc, _, err = self._run(f"systemctl enable {service_name}")
        if rc != 0:
            self.log.warn(f"Failed to enable {service_name}: {err}")

        # Start the service
        rc, _, err = self._run(f"systemctl start {service_name}")
        if rc != 0:
            self.log.error(f"Failed to start {service_name}: {err}")
            # Show journal logs for debugging
            _, journal, _ = self._run(f"journalctl -u {service_name} -n 20 --no-pager")
            if journal:
                self.log.info(f"Journal output:\n{journal}")
            return False

        self.log.success(f"Service {service_name} started and enabled")
        config["systemd_service"] = service_name
        return True

    def _build_systemd_unit(self, service_name: str, description: str,
                            exec_start: str, working_directory: str,
                            user: str, group: str, env_vars: Dict,
                            language: str, config: Dict) -> str:
        """Generate a systemd unit file."""
        # Environment directives
        env_lines = ""
        for key, value in env_vars.items():
            env_lines += f"Environment=\"{key}={value}\"\n"

        # Language-specific tuning
        restart_sec = "5"
        after = "network.target"
        wants = ""

        if language in ("python", "ruby", "java", "dotnet"):
            after = "network.target"
        elif language == "go" or language == "rust":
            restart_sec = "2"

        # Determine the Type
        service_type = "simple"

        # Memory limits
        memory_limit = ""
        if config.get("node_max_memory"):
            memory_limit = f"MemoryMax={config['node_max_memory']}M"

        unit = f"""[Unit]
Description={description}
After={after}
{f"Wants={wants}" if wants else ""}

[Service]
Type={service_type}
User={user}
Group={group}
WorkingDirectory={working_directory}
ExecStart={exec_start}
Restart=always
RestartSec={restart_sec}
StandardOutput=append:/var/log/{service_name}.log
StandardError=append:/var/log/{service_name}.error.log

# Environment
{env_lines}
# Security
NoNewPrivileges=true
PrivateTmp=true

# Limits
LimitNOFILE=65535
{memory_limit}

[Install]
WantedBy=multi-user.target
"""
        # Clean up empty lines from conditionals
        unit = re.sub(r'\n{3,}', '\n\n', unit)
        return unit

    # ── PM2 (Node.js / Next.js) ─────────────────────────────────

    def create_pm2_service(self, config: Dict, runtime=None) -> bool:
        """Create and start a PM2-managed Node.js process."""
        deploy_path = config["deploy_path"]
        service_name = config.get("systemd_service") or self._make_service_name(config)
        user = config.get("user", "root")
        language = config.get("language", "node")
        port = config.get("app_port", 3000)
        instances = config.get("node_instances", 1)
        max_memory = config.get("node_max_memory", 512)

        # Ensure PM2 is installed
        if not self._ensure_pm2():
            # Fallback to systemd
            self.log.warn("PM2 not available, falling back to systemd")
            return self.create_systemd_service(config, runtime)

        # Get start command
        start_cmd = config.get("start_command")
        if not start_cmd and runtime:
            start_cmd = runtime.get_start_command(config)

        if not start_cmd:
            self.log.error("No start command for PM2")
            return False

        # Get environment
        env_vars = config.get("environment_vars", {})
        if runtime:
            env_vars = {**runtime.get_environment_vars(config), **env_vars}

        # Build PM2 ecosystem config
        ecosystem = self._build_pm2_ecosystem(
            name=service_name,
            script=start_cmd,
            cwd=deploy_path,
            instances=instances,
            max_memory=max_memory,
            env_vars=env_vars,
            port=port,
        )

        eco_path = os.path.join(deploy_path, "ecosystem.config.js")
        self.log.step(f"Creating PM2 process: {service_name}")

        try:
            with open(eco_path, "w") as f:
                f.write(ecosystem)
        except Exception as e:
            self.log.error(f"Failed to write ecosystem file: {e}")
            return False

        # Stop existing processes
        self._run(f"pm2 delete {service_name} 2>/dev/null")

        # Start with PM2
        rc, out, err = self._run(f"pm2 start {eco_path}", cwd=deploy_path, timeout=60)
        if rc != 0:
            self.log.warn(f"PM2 start failed: {err or out}")
            self.log.info("Falling back to systemd...")
            return self.create_systemd_service(config, runtime)

        # Save PM2 process list
        self._run("pm2 save")

        # Setup PM2 startup script
        self._run(f"pm2 startup systemd -u {user} --hp /home/{user} 2>/dev/null")
        self._run("pm2 startup systemd 2>/dev/null")

        self.log.success(f"PM2 process {service_name} started")
        config["systemd_service"] = service_name
        config["_process_manager_type"] = "pm2"
        return True

    def _ensure_pm2(self) -> bool:
        """Ensure PM2 is installed globally."""
        rc, _, _ = self._run("pm2 --version 2>/dev/null")
        if rc == 0:
            return True

        self.log.info("Installing PM2 globally...")
        rc, _, err = self._run("npm install -g pm2", timeout=120)
        if rc != 0:
            self.log.warn(f"Failed to install PM2: {err}")
            return False
        return True

    def _build_pm2_ecosystem(self, name: str, script: str, cwd: str,
                             instances: int, max_memory: int,
                             env_vars: Dict, port: int) -> str:
        """Generate PM2 ecosystem.config.js."""
        # Parse script into command and args
        # PM2 expects the .js file as 'script', not 'node' as the command
        parts = script.split()
        interpreters = {"node", "nodejs", "bun", "deno"}
        if parts[0] in interpreters and len(parts) > 1:
            # e.g. "node server/index.js --port 5000" → script=server/index.js, args=--port 5000
            script_path = parts[1]
            args = " ".join(parts[2:]) if len(parts) > 2 else ""
        elif len(parts) > 1:
            script_path = parts[0]
            args = " ".join(parts[1:])
        else:
            script_path = script
            args = ""

        # Determine exec_mode
        exec_mode = "cluster" if instances > 1 else "fork"
        # Only cluster mode works with Node.js scripts, not custom binaries
        if not script_path.endswith(".js") and exec_mode == "cluster":
            exec_mode = "fork"

        env_json = json.dumps(env_vars, indent=8)

        return f"""module.exports = {{
  apps: [{{
    name: '{name}',
    script: '{script_path}',
    args: '{args}',
    cwd: '{cwd}',
    instances: {instances},
    exec_mode: '{exec_mode}',
    max_memory_restart: '{max_memory}M',
    env: {env_json},
    merge_logs: true,
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    error_file: '/var/log/{name}-error.log',
    out_file: '/var/log/{name}-out.log',
    time: true,
    autorestart: true,
    watch: false,
    max_restarts: 10,
    restart_delay: 5000,
  }}]
}};
"""

    # ── Common Service Operations ────────────────────────────────

    def start(self, config: Dict) -> bool:
        """Start the application process."""
        pm_type = config.get("_process_manager_type", "systemd")
        name = config.get("systemd_service", "")

        if pm_type == "pm2":
            rc, _, err = self._run(f"pm2 start {name}")
        else:
            rc, _, err = self._run(f"systemctl start {name}")

        if rc != 0:
            self.log.error(f"Failed to start {name}: {err}")
            return False
        return True

    def stop(self, config: Dict) -> bool:
        """Stop the application process."""
        pm_type = config.get("_process_manager_type", "systemd")
        name = config.get("systemd_service", "")

        if pm_type == "pm2":
            rc, _, err = self._run(f"pm2 stop {name}")
        else:
            rc, _, err = self._run(f"systemctl stop {name}")

        if rc != 0:
            self.log.warn(f"Failed to stop {name}: {err}")
            return False
        return True

    def restart(self, config: Dict) -> bool:
        """Restart the application process."""
        pm_type = config.get("_process_manager_type", "systemd")
        name = config.get("systemd_service", "")

        if pm_type == "pm2":
            rc, _, err = self._run(f"pm2 restart {name}")
        else:
            rc, _, err = self._run(f"systemctl restart {name}")

        if rc != 0:
            self.log.error(f"Failed to restart {name}: {err}")
            return False
        return True

    def status(self, config: Dict) -> Dict:
        """Get process status."""
        pm_type = config.get("_process_manager_type", "systemd")
        name = config.get("systemd_service", "")

        if pm_type == "pm2":
            rc, out, _ = self._run(f"pm2 jlist")
            if rc == 0:
                try:
                    procs = json.loads(out)
                    for p in procs:
                        if p.get("name") == name:
                            return {
                                "running": p.get("pm2_env", {}).get("status") == "online",
                                "pid": p.get("pid"),
                                "memory": p.get("monit", {}).get("memory", 0),
                                "cpu": p.get("monit", {}).get("cpu", 0),
                                "uptime": p.get("pm2_env", {}).get("pm_uptime", 0),
                            }
                except Exception:
                    pass
        else:
            rc, out, _ = self._run(f"systemctl is-active {name}")
            pid_rc, pid_out, _ = self._run(f"systemctl show -p MainPID {name}")
            return {
                "running": rc == 0 and "active" in out,
                "pid": pid_out.split("=")[-1] if pid_rc == 0 else None,
                "status": out.strip(),
            }

        return {"running": False}

    def setup_service(self, config: Dict, runtime=None) -> bool:
        """Create and start the appropriate process manager based on language."""
        language = config.get("language", "")
        process_manager = config.get("process_manager", "systemd")

        # Static sites don't need a process manager
        if language == "static":
            self.log.info("Static site — no process manager needed")
            return True

        # PHP uses PHP-FPM (handled separately)
        if language == "php":
            self.log.info("PHP uses FPM — skipping process manager")
            return True

        # Node.js / Next.js prefer PM2
        if process_manager == "pm2" or (language in ("node", "nextjs") and process_manager != "systemd"):
            return self.create_pm2_service(config, runtime)

        # Everything else uses systemd
        return self.create_systemd_service(config, runtime)

    # ── Helpers ──────────────────────────────────────────────────

    def _make_service_name(self, config: Dict) -> str:
        """Generate a service name from domain."""
        domain = config.get("domain", "app")
        # Clean domain into a valid systemd service name
        name = re.sub(r'[^a-zA-Z0-9-]', '-', domain)
        name = re.sub(r'-+', '-', name).strip('-')
        return name
