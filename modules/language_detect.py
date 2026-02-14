"""
Language Detection Module
==========================
Auto-detects the programming language of a repository
by examining file patterns, config files, and package manifests.

Priority order (first match wins):
1. Explicit `language:` in services.yml
2. Primary indicator files (composer.json → PHP, go.mod → Go, etc.)
3. File extension analysis (most common lang wins)
"""

import os
from typing import Dict, List, Optional, Tuple


# Primary indicators: file → language (checked first, high confidence)
PRIMARY_INDICATORS = {
    # PHP
    "composer.json": "php",
    "artisan": "php",  # Laravel
    "wp-config.php": "php",
    "wp-login.php": "php",

    # Python
    "requirements.txt": "python",
    "Pipfile": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "manage.py": "python",  # Django
    "app.py": None,  # Could be Flask or something else — weak signal

    # Node.js / Next.js (checked via package.json deps below)
    # package.json by itself is ambiguous — could be React, Next, Vue, etc.

    # Ruby
    "Gemfile": "ruby",
    "Rakefile": "ruby",
    "config.ru": "ruby",

    # Go
    "go.mod": "go",
    "go.sum": "go",

    # Java
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",

    # Rust
    "Cargo.toml": "rust",
    "Cargo.lock": "rust",

    # .NET
    "global.json": "dotnet",

    # Static site generators
    "hugo.toml": "static",
    "hugo.yaml": "static",
    "_config.yml": None,  # Could be Jekyll — need more context
}

# Secondary: file extension → language weight
EXTENSION_WEIGHTS = {
    ".php": ("php", 3),
    ".py": ("python", 3),
    ".js": ("node", 1),    # Weak — could be frontend
    ".ts": ("node", 2),
    ".jsx": ("node", 1),
    ".tsx": ("node", 2),
    ".rb": ("ruby", 3),
    ".go": ("go", 3),
    ".java": ("java", 3),
    ".kt": ("java", 2),    # Kotlin
    ".rs": ("rust", 3),
    ".cs": ("dotnet", 3),
    ".fs": ("dotnet", 2),  # F#
    ".vb": ("dotnet", 2),  # VB.NET
    ".html": ("static", 1),
    ".css": ("static", 0),  # Too common everywhere
}


def detect_language(deploy_path: str, log=None) -> str:
    """
    Detect the primary programming language of a repository.

    Args:
        deploy_path: Path to the cloned repository.
        log: Optional logger.

    Returns:
        Language string (e.g., 'php', 'python', 'node', 'nextjs', etc.)
    """
    if not os.path.isdir(deploy_path):
        return "php"  # Default fallback

    # Phase 1: Check primary indicator files
    for indicator_file, language in PRIMARY_INDICATORS.items():
        if language and os.path.isfile(os.path.join(deploy_path, indicator_file)):
            if log:
                log.info(f"Language detected from {indicator_file}: {language}")
            return language

    # Phase 2: Check .csproj files deeper (they could be in subdirs)
    for f in os.listdir(deploy_path):
        if f.endswith((".csproj", ".fsproj", ".vbproj", ".sln")):
            if log:
                log.info(f"Language detected from {f}: dotnet")
            return "dotnet"

    # Phase 3: Check package.json for framework-specific deps
    pkg_path = os.path.join(deploy_path, "package.json")
    if os.path.isfile(pkg_path):
        lang = _detect_from_package_json(pkg_path, deploy_path, log)
        if lang:
            return lang

    # Phase 4: File extension analysis
    lang = _detect_from_extensions(deploy_path, log)
    if lang:
        return lang

    # Phase 5: Default
    if log:
        log.warn("Could not auto-detect language — defaulting to 'php'")
    return "php"


def _detect_from_package_json(pkg_path: str, deploy_path: str, log=None) -> Optional[str]:
    """Detect language from package.json dependencies."""
    try:
        import json
        with open(pkg_path) as f:
            pkg = json.load(f)
    except Exception:
        return "node"  # Has package.json but can't read → assume Node

    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    # Next.js
    if "next" in deps:
        # Check if it's an SSR app or static export
        for cfg in ["next.config.js", "next.config.mjs", "next.config.ts"]:
            if os.path.isfile(os.path.join(deploy_path, cfg)):
                try:
                    with open(os.path.join(deploy_path, cfg), "r", errors="ignore") as f:
                        content = f.read()
                    if "output" in content and "'export'" in content:
                        if log:
                            log.info("Detected Next.js static export → static")
                        return "static"
                except Exception:
                    pass
        if log:
            log.info("Detected Next.js from package.json")
        return "nextjs"

    # Static site frameworks (build to static files, no server)
    static_indicators = [
        "react-scripts",     # Create React App
        "@vue/cli-service",  # Vue CLI
        "@angular/core",     # Angular
        "gatsby",            # Gatsby
        "svelte",            # Svelte/SvelteKit
        "@11ty/eleventy",    # Eleventy
        "astro",             # Astro
        "vite",              # Vite (might be SPA)
    ]

    # But if these also have a server framework, it's Node.js
    server_indicators = [
        "express", "koa", "@hapi/hapi", "fastify",
        "@nestjs/core", "@adonisjs/core", "strapi", "@strapi/strapi",
    ]

    has_server = any(pkg in deps for pkg in server_indicators)
    has_static = any(pkg in deps for pkg in static_indicators)

    if has_server:
        if log:
            log.info("Detected Node.js server framework from package.json")
        return "node"

    if has_static and not has_server:
        # Check for SSR indicators
        scripts = pkg.get("scripts", {})
        start_script = scripts.get("start", "")
        if "serve" in start_script or "node" in start_script or "express" in start_script:
            if log:
                log.info("Detected Node.js app (has start script with server)")
            return "node"

        if log:
            log.info("Detected static site from package.json")
        return "static"

    # Has package.json with deps but no clear framework → generic Node
    if deps:
        if log:
            log.info("Detected Node.js from package.json (generic)")
        return "node"

    return "node"


def _detect_from_extensions(deploy_path: str, log=None, max_files: int = 500) -> Optional[str]:
    """Detect language by analyzing file extensions in the repo."""
    scores: Dict[str, int] = {}
    count = 0

    for root, dirs, files in os.walk(deploy_path):
        # Skip common non-source directories
        dirs[:] = [d for d in dirs if d not in (
            ".git", "node_modules", "vendor", "venv", ".venv",
            "__pycache__", ".next", "dist", "build", "target",
            ".idea", ".vscode",
        )]

        for fname in files:
            _, ext = os.path.splitext(fname)
            if ext in EXTENSION_WEIGHTS:
                lang, weight = EXTENSION_WEIGHTS[ext]
                scores[lang] = scores.get(lang, 0) + weight
            count += 1
            if count >= max_files:
                break
        if count >= max_files:
            break

    if not scores:
        return None

    # Pick the language with highest score
    best = max(scores, key=scores.get)
    if log:
        top3 = sorted(scores.items(), key=lambda x: -x[1])[:3]
        log.info(f"Extension analysis: {dict(top3)} → {best}")
    return best


def detect_all_languages(deploy_path: str, log=None) -> List[Tuple[str, float]]:
    """
    Return a ranked list of (language, confidence) tuples.
    Useful for multi-language repos.
    """
    if not os.path.isdir(deploy_path):
        return [("php", 0.5)]

    results = {}

    # Check primary indicators
    for indicator_file, language in PRIMARY_INDICATORS.items():
        if language and os.path.isfile(os.path.join(deploy_path, indicator_file)):
            results[language] = results.get(language, 0) + 0.4

    # Check package.json
    pkg_path = os.path.join(deploy_path, "package.json")
    if os.path.isfile(pkg_path):
        lang = _detect_from_package_json(pkg_path, deploy_path)
        if lang:
            results[lang] = results.get(lang, 0) + 0.3

    # Extension analysis
    scores: Dict[str, int] = {}
    count = 0
    for root, dirs, files in os.walk(deploy_path):
        dirs[:] = [d for d in dirs if d not in (
            ".git", "node_modules", "vendor", "venv", "__pycache__",
        )]
        for fname in files:
            _, ext = os.path.splitext(fname)
            if ext in EXTENSION_WEIGHTS:
                lang, weight = EXTENSION_WEIGHTS[ext]
                scores[lang] = scores.get(lang, 0) + weight
            count += 1
            if count >= 500:
                break
        if count >= 500:
            break

    if scores:
        total = sum(scores.values())
        for lang, score in scores.items():
            results[lang] = results.get(lang, 0) + (score / total) * 0.3

    # Sort by confidence descending
    ranked = sorted(results.items(), key=lambda x: -x[1])
    return ranked
