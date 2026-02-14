"""
Java Runtime - Spring Boot, Quarkus, Micronaut, etc.
=====================================================
Handles Java application deployment:
- JDK version detection from pom.xml, build.gradle
- JDK installation (OpenJDK)
- Maven / Gradle build
- JAR execution via systemd
"""

import os
import re
from typing import Dict, Optional

from modules.runtimes.base import BaseRuntime


class JavaRuntime(BaseRuntime):

    FRAMEWORK_INDICATORS = {
        "spring-boot": {
            "files": ["src/main/resources/application.properties", "src/main/resources/application.yml"],
            "packages": ["spring-boot-starter", "org.springframework.boot"],
        },
        "quarkus": {"packages": ["io.quarkus"]},
        "micronaut": {"packages": ["io.micronaut"]},
        "vert.x": {"packages": ["io.vertx"]},
        "dropwizard": {"packages": ["io.dropwizard"]},
    }

    def detect_version(self, deploy_path: str, configured_version: Optional[str] = None) -> str:
        if configured_version:
            return configured_version

        # Check pom.xml
        pom = os.path.join(deploy_path, "pom.xml")
        if os.path.isfile(pom):
            try:
                with open(pom, "r", errors="ignore") as f:
                    content = f.read()
                match = re.search(r"<java\.version>(\d+)</java\.version>", content)
                if match:
                    return match.group(1)
                match = re.search(r"<maven\.compiler\.source>(\d+)", content)
                if match:
                    return match.group(1)
            except Exception:
                pass

        # Check build.gradle
        for gradle_file in ["build.gradle", "build.gradle.kts"]:
            gradle = os.path.join(deploy_path, gradle_file)
            if os.path.isfile(gradle):
                try:
                    with open(gradle, "r", errors="ignore") as f:
                        content = f.read()
                    match = re.search(r"sourceCompatibility\s*=?\s*['\"]?(\d+)", content)
                    if match:
                        return match.group(1)
                    match = re.search(r"JavaVersion\.VERSION_(\d+)", content)
                    if match:
                        return match.group(1)
                except Exception:
                    pass

        # Check .java-version
        jv_path = os.path.join(deploy_path, ".java-version")
        if os.path.isfile(jv_path):
            try:
                with open(jv_path) as f:
                    return f.read().strip()
            except Exception:
                pass

        return "21"  # LTS

    def install(self, version: str, config: Dict) -> bool:
        self.log.step(f"Installing Java {version} (OpenJDK)")

        # Check if already installed
        rc, out, _ = self._run("java -version 2>&1")
        if rc == 0 and (f'"{version}.' in out or f'"1.{version}.' in out or f'openjdk {version}' in out.lower()):
            self.log.info(f"✓ Java {version} already installed")
            return True

        if self.os_info["family"] == "debian":
            self._run("apt-get update -qq")
            if not self._apt_install([f"openjdk-{version}-jdk-headless"]):
                # Try alternative package name
                if not self._apt_install([f"openjdk-{version}-jdk"]):
                    self.log.error(f"Failed to install OpenJDK {version}")
                    return False
        else:
            if not self._yum_install([f"java-{version}-openjdk-devel"]):
                return False

        # Also install Maven by default
        self._install_build_tool(config, deploy_path=None)

        rc, out, _ = self._run("java -version 2>&1")
        if rc == 0:
            self.log.success(f"Java installed: {out.strip().splitlines()[0]}")
            return True

        self.log.error("Java installation failed")
        return False

    def _install_build_tool(self, config: Dict, deploy_path: Optional[str] = None):
        """Install Maven or Gradle based on project."""
        if deploy_path and os.path.isfile(os.path.join(deploy_path, "gradlew")):
            self.log.info("Gradle wrapper found — will use gradlew")
            return

        if deploy_path and (
            os.path.isfile(os.path.join(deploy_path, "build.gradle"))
            or os.path.isfile(os.path.join(deploy_path, "build.gradle.kts"))
        ):
            if not self._cmd_exists("gradle"):
                self.log.info("Installing Gradle...")
                if self.os_info["family"] == "debian":
                    self._apt_install(["gradle"])
                else:
                    self._yum_install(["gradle"])
        else:
            if not self._cmd_exists("mvn"):
                self.log.info("Installing Maven...")
                if self.os_info["family"] == "debian":
                    self._apt_install(["maven"])
                else:
                    self._yum_install(["maven"])

    def install_dependencies(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        self.log.step("Resolving Java dependencies")

        self._install_build_tool(config, deploy_path)

        if config.get("install_command"):
            rc, _, err = self._run(config["install_command"], cwd=deploy_path, timeout=600)
        elif os.path.isfile(os.path.join(deploy_path, "gradlew")):
            self._run(f"chmod +x {os.path.join(deploy_path, 'gradlew')}")
            rc, _, err = self._run("./gradlew dependencies", cwd=deploy_path, timeout=600)
        elif os.path.isfile(os.path.join(deploy_path, "build.gradle")) or \
             os.path.isfile(os.path.join(deploy_path, "build.gradle.kts")):
            rc, _, err = self._run("gradle dependencies", cwd=deploy_path, timeout=600)
        elif os.path.isfile(os.path.join(deploy_path, "pom.xml")):
            rc, _, err = self._run(
                "mvn dependency:resolve -q", cwd=deploy_path, timeout=600,
            )
        else:
            self.log.info("No build file found — skipping")
            return True

        if rc == 0:
            self.log.success("Java dependencies resolved")
        else:
            self.log.warn(f"Dependency resolution issues: {err[:200]}")
        return True

    def build(self, config: Dict) -> bool:
        deploy_path = config["deploy_path"]
        user = config.get("user", "root")

        build_cmd = config.get("build_command")
        if not build_cmd:
            if os.path.isfile(os.path.join(deploy_path, "gradlew")):
                build_cmd = "./gradlew build -x test"
            elif os.path.isfile(os.path.join(deploy_path, "build.gradle")) or \
                 os.path.isfile(os.path.join(deploy_path, "build.gradle.kts")):
                build_cmd = "gradle build -x test"
            elif os.path.isfile(os.path.join(deploy_path, "pom.xml")):
                build_cmd = "mvn package -DskipTests -q"
            else:
                return True

        self.log.step(f"Building Java application")
        rc, out, err = self._run(build_cmd, cwd=deploy_path, user=user, timeout=900)
        if rc != 0:
            self.log.error(f"Java build failed: {err[:300]}")
            return False

        # Find the built JAR/WAR
        jar_path = self._find_artifact(deploy_path)
        if jar_path:
            config["_artifact_path"] = jar_path
            self.log.success(f"Built artifact: {os.path.basename(jar_path)}")
        else:
            self.log.warn("No JAR/WAR artifact found after build")

        return True

    def get_start_command(self, config: Dict) -> Optional[str]:
        if config.get("start_command"):
            return config["start_command"]

        deploy_path = config["deploy_path"]
        port = config.get("app_port", 8080)

        # Use previously found artifact
        jar_path = config.get("_artifact_path") or self._find_artifact(deploy_path)
        if jar_path:
            return f"java -jar {jar_path} --server.port={port}"

        # Quarkus runner
        runner = os.path.join(deploy_path, "target", "quarkus-app", "quarkus-run.jar")
        if os.path.isfile(runner):
            return f"java -jar {runner}"

        return None

    def detect_framework(self, deploy_path: str) -> Dict:
        build_content = self._read_build_files(deploy_path)

        for framework, indicators in self.FRAMEWORK_INDICATORS.items():
            for fname in indicators.get("files", []):
                if os.path.isfile(os.path.join(deploy_path, fname)):
                    return self._get_framework_info(framework, deploy_path)
            for pkg in indicators.get("packages", []):
                if pkg in build_content:
                    return self._get_framework_info(framework, deploy_path)

        return self._get_framework_info("generic-java", deploy_path)

    def get_environment_vars(self, config: Dict) -> Dict[str, str]:
        env = {
            "JAVA_HOME": self._find_java_home(),
            "SERVER_PORT": str(config.get("app_port", 8080)),
            "SPRING_PROFILES_ACTIVE": "production",
        }
        env.update(config.get("environment_vars", {}))
        return env

    def needs_reverse_proxy(self) -> bool:
        return True

    # ── Helpers ──────────────────────────────────────────────────

    def _find_artifact(self, deploy_path: str) -> Optional[str]:
        """Find the built JAR/WAR file."""
        # Check target/ (Maven) and build/libs/ (Gradle)
        search_dirs = [
            os.path.join(deploy_path, "target"),
            os.path.join(deploy_path, "build", "libs"),
        ]
        for search_dir in search_dirs:
            if not os.path.isdir(search_dir):
                continue
            jars = []
            for f in os.listdir(search_dir):
                if f.endswith((".jar", ".war")) and "-sources" not in f and "-javadoc" not in f:
                    jars.append(os.path.join(search_dir, f))
            if jars:
                # Prefer the largest JAR (fat JAR)
                jars.sort(key=lambda x: os.path.getsize(x), reverse=True)
                return jars[0]
        return None

    def _read_build_files(self, deploy_path: str) -> str:
        content = ""
        for fname in ["pom.xml", "build.gradle", "build.gradle.kts"]:
            fpath = os.path.join(deploy_path, fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content += f.read() + "\n"
                except Exception:
                    pass
        return content

    def _find_java_home(self) -> str:
        rc, out, _ = self._run("readlink -f $(which java) 2>/dev/null")
        if rc == 0 and out.strip():
            # /usr/lib/jvm/java-21-openjdk-amd64/bin/java → /usr/lib/jvm/java-21-openjdk-amd64
            java_bin = out.strip()
            return os.path.dirname(os.path.dirname(java_bin))
        return "/usr/lib/jvm/default-java"

    def _get_framework_info(self, framework: str, deploy_path: str) -> Dict:
        base = {
            "name": framework,
            "version": "unknown",
            "document_root_suffix": "",
            "writable_dirs": ["logs", "data", "tmp"],
            "post_deploy_commands": [],
            "database_driver": None,
            "database_credentials": {},
            "entry_point": None,
            "start_command": None,
            "build_command": None,
            "extra_extensions": [],
            "sql_files": [],
        }
        if framework == "spring-boot":
            base["post_deploy_commands"] = []
            db = self._detect_spring_db(deploy_path)
            if db:
                base["database_driver"] = db
        return base

    def _detect_spring_db(self, deploy_path: str) -> Optional[str]:
        for fname in ["application.properties", "application.yml"]:
            fpath = os.path.join(deploy_path, "src", "main", "resources", fname)
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        content = f.read()
                    if "postgresql" in content:
                        return "pgsql"
                    if "mysql" in content:
                        return "mysql"
                except Exception:
                    pass
        return None
