"""
Runtime Modules - Universal Deployment Automation Agent
========================================================
Each runtime handles installing, configuring, and managing
a specific programming language and its ecosystem.

Architecture:
    BaseRuntime (abstract)
    ├── PHPRuntime         → PHP-FPM pools, Composer
    ├── PythonRuntime      → virtualenv, pip/poetry, Gunicorn/Uvicorn
    ├── NodeRuntime        → nvm/nodesource, npm/yarn/pnpm, PM2
    ├── NextJSRuntime      → extends NodeRuntime for SSR
    ├── RubyRuntime        → rbenv, Bundler, Puma
    ├── GoRuntime          → go binary, go mod, compiled
    ├── JavaRuntime        → JDK, Maven/Gradle, JAR
    ├── RustRuntime        → rustup/cargo, compiled binary
    ├── DotNetRuntime      → dotnet SDK, Kestrel
    └── StaticRuntime      → Node build, serve via web server
"""

from modules.runtimes.base import BaseRuntime
from modules.runtimes.python_runtime import PythonRuntime
from modules.runtimes.node_runtime import NodeRuntime
from modules.runtimes.nextjs_runtime import NextJSRuntime
from modules.runtimes.ruby_runtime import RubyRuntime
from modules.runtimes.go_runtime import GoRuntime
from modules.runtimes.java_runtime import JavaRuntime
from modules.runtimes.rust_runtime import RustRuntime
from modules.runtimes.dotnet_runtime import DotNetRuntime
from modules.runtimes.static_runtime import StaticRuntime

__all__ = [
    "BaseRuntime",
    "PythonRuntime",
    "NodeRuntime",
    "NextJSRuntime",
    "RubyRuntime",
    "GoRuntime",
    "JavaRuntime",
    "RustRuntime",
    "DotNetRuntime",
    "StaticRuntime",
]

# Registry: language string → Runtime class
RUNTIME_REGISTRY = {
    "python": PythonRuntime,
    "node": NodeRuntime,
    "nextjs": NextJSRuntime,
    "ruby": RubyRuntime,
    "go": GoRuntime,
    "java": JavaRuntime,
    "rust": RustRuntime,
    "dotnet": DotNetRuntime,
    "static": StaticRuntime,
}


def get_runtime(language: str, log, os_info: dict) -> BaseRuntime:
    """Factory: return the runtime instance for a given language.

    Raises ValueError for unsupported languages.
    PHP is handled separately by the existing phpfpm.py module.
    """
    runtime_cls = RUNTIME_REGISTRY.get(language)
    if runtime_cls is None:
        raise ValueError(
            f"Unsupported language: '{language}'. "
            f"Supported: php, {', '.join(RUNTIME_REGISTRY.keys())}"
        )
    return runtime_cls(log=log, os_info=os_info)
