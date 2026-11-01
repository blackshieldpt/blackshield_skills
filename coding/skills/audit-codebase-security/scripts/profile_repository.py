#!/usr/bin/env python3
"""Build a deterministic security-audit applicability profile for a repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

SCHEMA_VERSION = 1
DEFAULT_MAX_FILES = 200_000
DEFAULT_CONTENT_BUDGET = 16 * 1024 * 1024
MAX_FILE_PREFIX = 256 * 1024
MAX_EVIDENCE_PATHS = 12
MAX_ENTRY_POINTS = 50
OBJECTIVE_C_MARKERS = re.compile(
    r"^\s*(?:#(?:import|include)\b|@interface\b|@implementation\b)", re.M
)
RUST_UNSAFE_MARKERS = re.compile(
    r"\bunsafe\s*(?:\{|fn\b|impl\b|extern\b)|\bextern\s+\"C\""
)
MOBILE_MANIFEST_NAMES = frozenset({"androidmanifest.xml", "info.plist"})
BUILD_OUTPUT_DIRECTORY_NAMES = frozenset({"build", "dist", "target"})

IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "bower_components",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "target",
        "venv",
    }
)

LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".h": "C/C++ Header",
    ".hh": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".cs": "C#",
    ".cshtml": "C#",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".go": "Go",
    ".hs": "Haskell",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".cjs": "JavaScript",
    ".mjs": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".php": "PHP",
    ".py": "Python",
    ".pyi": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".scala": "Scala",
    ".sh": "Shell",
    ".bash": "Shell",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".cts": "TypeScript",
    ".mts": "TypeScript",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

REFERENCE_PACK_BY_LANGUAGE: dict[str, str] = {
    "C": "references/c-cpp.md",
    "C++": "references/c-cpp.md",
    "C/C++ Header": "references/c-cpp.md",
    "Go": "references/go.md",
    "JavaScript": "references/javascript-typescript.md",
    "Objective-C": "references/c-cpp.md",
    "Objective-C++": "references/c-cpp.md",
    "Python": "references/python.md",
    "Svelte": "references/javascript-typescript.md",
    "TypeScript": "references/javascript-typescript.md",
    "Vue": "references/javascript-typescript.md",
}

MANIFEST_NAMES = frozenset(
    {
        "Cargo.lock",
        "Cargo.toml",
        "Gemfile",
        "Gemfile.lock",
        "Package.resolved",
        "Package.swift",
        "Pipfile",
        "Pipfile.lock",
        "Podfile",
        "Podfile.lock",
        "bun.lock",
        "bun.lockb",
        "composer.json",
        "composer.lock",
        "deno.json",
        "deno.jsonc",
        "go.mod",
        "go.sum",
        "mix.exs",
        "mix.lock",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pom.xml",
        "pubspec.lock",
        "pubspec.yaml",
        "pyproject.toml",
        "rebar.config",
        "requirements.txt",
        "uv.lock",
        "yarn.lock",
    }
)

FRAMEWORK_TERMS: dict[str, tuple[str, ...]] = {
    "Angular": ('"@angular/', "'@angular/", "@angular/core"),
    "Django": ("django",),
    "Electron": ('"electron"', "'electron'", " electron "),
    "Express": ('"express"', "'express'", " express@"),
    "FastAPI": ("fastapi",),
    "Fastify": ('"fastify"', "'fastify'", " fastify@"),
    "Flask": ("flask",),
    "GraphQL": ('"graphql"', "'graphql'", "graphql-core", "gqlgen"),
    "Koa": ('"koa"', "'koa'", " koa@"),
    "NestJS": ("@nestjs/",),
    "Next.js": ('"next"', "'next'", " next@"),
    "Nuxt": ('"nuxt"', "'nuxt'", " nuxt@"),
    "React": ('"react"', "'react'", " react@"),
    "Ruby on Rails": ("rails",),
    "Spring": ("spring-boot", "org.springframework"),
    "Starlette": ("starlette",),
    "Vue": ('"vue"', "'vue'", " vue@"),
    "aiohttp": ("aiohttp",),
    "axum": ("axum",),
    "gin": ("github.com/gin-gonic/gin",),
}

SURFACE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "authentication_or_session": (
        re.compile(
            r"\b(?:authentica(?:te|tion)|authoriz(?:e|ation)|session|oauth|oidc|jwt)\b",
            re.I,
        ),
        re.compile(r"\b(?:passport|login|logout|password[_-]?reset)\b", re.I),
    ),
    "database_or_query": (
        re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE)\s+", re.I),
        re.compile(
            r"\b(?:sqlalchemy|prisma|sequelize|knex|mongoose|database/sql|jdbc)\b", re.I
        ),
    ),
    "dynamic_execution_or_process": (
        re.compile(
            r"\b(?:child_process|subprocess|os\.system|Runtime\.getRuntime|ProcessBuilder)\b"
        ),
        re.compile(r"\b(?:eval|exec)\s*\(|\b(?:new\s+)?Function\s*\("),
        re.compile(r"\bexec\.Command(?:Context)?\s*\("),
    ),
    "filesystem_or_upload": (
        re.compile(
            r"\b(?:readFile(?:Sync)?|writeFile(?:Sync)?|createReadStream|createWriteStream|sendFile)\s*\("
        ),
        re.compile(r"(?<![\w.])open\s*\(|\bfopen\s*\("),
        re.compile(r"\b(?:os|io|codecs|aiofiles|fs|fsPromises|File)\.open\s*\("),
        re.compile(r"\bPath\s*\("),
        re.compile(
            r"\b(?:os\.Open|os\.OpenFile|os\.Create|os\.ReadFile|os\.WriteFile|std::fs)\b"
        ),
        re.compile(r"\b(?:multipart|multer|upload|archive|zipfile|tarfile)\b", re.I),
    ),
    "outbound_network": (
        re.compile(r"\bfetch\s*\(|\b(?:axios|undici|requests\.(?:get|post|request)|httpx)\b"),
        re.compile(
            r"\b(?:http\.NewRequest|reqwest|urlopen|webhook|proxy_pass)\b", re.I
        ),
    ),
    "parsing_or_deserialization": (
        re.compile(
            r"\b(?:pickle\.loads?|yaml\.(?:load|unsafe_load)|ObjectInputStream)\b"
        ),
        re.compile(
            r"\b(?:xml2js|libxml|XMLParser|DocumentBuilderFactory|deserialize)\b", re.I
        ),
    ),
    "cryptography_or_secrets": (
        re.compile(
            r"\b(?:crypto|cryptography|hashlib|bcrypt|argon2|Cipher|SecretKey)\b"
        ),
        re.compile(r"\b(?:private[_-]?key|api[_-]?key|client[_-]?secret)\b", re.I),
    ),
    "browser_rendering": (
        re.compile(
            r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|dangerouslySetInnerHTML)\b"
        ),
        re.compile(r"\b(?:document\.write|v-html|postMessage)\b"),
    ),
    "concurrency_or_queues": (
        re.compile(
            r"\b(?:Promise\.all|asyncio|ThreadPool|Executor|goroutine|channel|mutex)\b",
            re.I,
        ),
        re.compile(r"\b(?:queue|worker|consumer|producer|job)\b", re.I),
    ),
}

SURFACE_FAMILIES: dict[str, tuple[str, ...]] = {
    "authentication_or_session": ("AUTHN", "AUTHZ", "SESS", "LOGIC"),
    "browser_rendering": ("INJ", "CLIENT", "WEB", "SESS"),
    "concurrency_or_queues": ("CONC", "LOGIC", "DOS"),
    "cryptography_or_secrets": ("CRYPTO", "DATA", "AUTHN", "SESS"),
    "database_or_query": ("INJ", "AUTHZ", "DATA", "LOGIC"),
    "dynamic_execution_or_process": ("INJ", "AUTHZ", "FILE"),
    "filesystem_or_upload": ("FILE", "AUTHZ", "DATA", "PARSE", "DOS"),
    "infrastructure_or_deployment": ("OPS", "AUTHZ", "DATA", "CRYPTO"),
    "mobile_or_desktop": ("CLIENT", "AUTHN", "AUTHZ", "DATA", "OPS"),
    "native_or_unsafe_code": ("MEM", "PARSE", "CONC", "DOS"),
    "outbound_network": ("WEB", "DATA", "DOS"),
    "parsing_or_deserialization": ("PARSE", "INJ", "DOS"),
    "web_or_api": ("AUTHN", "AUTHZ", "WEB", "INJ", "DATA", "DOS"),
}

FAMILY_ORDER = (
    "AUTHN",
    "AUTHZ",
    "INJ",
    "WEB",
    "FILE",
    "PARSE",
    "SESS",
    "DATA",
    "CRYPTO",
    "LOGIC",
    "CONC",
    "MEM",
    "DOS",
    "CLIENT",
    "VAL",
    "OPS",
)


@dataclass
class ScanState:
    """Mutable state collected while profiling a repository."""

    files_scanned: int = 0
    symlinks_skipped: int = 0
    unreadable_files: int = 0
    content_bytes_read: int = 0
    truncated: bool = False
    ignored_directories: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class RepositoryProfile:
    """Serializable repository security profile."""

    schema_version: int
    root: str
    scan: dict[str, object]
    languages: list[dict[str, object]]
    manifests: list[str]
    frameworks: list[dict[str, object]]
    infrastructure: list[str]
    entry_point_candidates: list[str]
    security_surfaces: list[dict[str, object]]
    recommended_reference_packs: list[str]
    recommended_taxonomy_families: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, object]:
        """Return the profile as a JSON-serializable mapping."""

        return asdict(self)


def display_path(text: str) -> str:
    """Return a path that encodes as UTF-8 and stays on one output line.

    Undecodable filename bytes become ``\\xNN`` escapes and non-printable
    characters such as newlines become backslash escapes.
    """

    text = text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    return "".join(
        character if character.isprintable() else ascii(character)[1:-1]
        for character in text
    )


def normalize_excludes(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize repository-relative exclusion prefixes.

    Args:
        values: User-provided relative paths.

    Returns:
        Normalized POSIX-style prefixes.

    Raises:
        ValueError: If an exclusion is absolute or escapes the root.
    """

    normalized: list[str] = []
    for value in values:
        candidate = PurePosixPath(value.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"Exclusion must stay below the audit root: {value!r}")
        text = candidate.as_posix()
        if text != ".":
            normalized.append(text)
    return tuple(sorted(set(normalized)))


def is_excluded(relative_path: str, prefixes: Sequence[str]) -> bool:
    """Return whether a path matches an explicit exclusion prefix."""

    return any(
        relative_path == prefix or relative_path.startswith(f"{prefix}/")
        for prefix in prefixes
    )


def walk_files(
    root: Path,
    excludes: Sequence[str],
    max_files: int,
    state: ScanState,
) -> list[Path]:
    """Walk regular files deterministically without following symbolic links."""

    files: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError:
            state.unreadable_files += 1
            continue

        child_directories: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                if entry.is_symlink():
                    state.symlinks_skipped += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in IGNORED_DIRECTORY_NAMES:
                        state.ignored_directories[entry.name] += 1
                        continue
                    if is_excluded(relative, excludes):
                        state.ignored_directories[display_path(relative)] += 1
                        continue
                    child_directories.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                state.unreadable_files += 1
                continue

            if is_excluded(relative, excludes):
                continue
            files.append(path)
            if len(files) >= max_files:
                state.truncated = True
                state.files_scanned = len(files)
                return files

        pending.extend(reversed(child_directories))

    state.files_scanned = len(files)
    return files


def read_text_prefix(path: Path, limit: int = MAX_FILE_PREFIX) -> tuple[str, int]:
    """Read a bounded text prefix, rejecting obviously binary content."""

    with path.open("rb") as handle:
        data = handle.read(limit)
    if b"\x00" in data:
        return "", len(data)
    return data.decode("utf-8", errors="replace"), len(data)


def is_manifest(path: Path) -> bool:
    """Return whether a path is a recognized dependency/build manifest."""

    name = path.name
    return (
        name in MANIFEST_NAMES
        or name.endswith((".csproj", ".fsproj", ".vbproj", ".sln"))
        or name.startswith("requirements-")
        and name.endswith(".txt")
        or name
        in {
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        }
    )


def is_infrastructure(path: Path, relative: str) -> bool:
    """Return whether a path contains deployment or infrastructure configuration."""

    name = path.name.lower()
    parts = PurePosixPath(relative).parts
    return (
        path.suffix.lower() == ".tf"
        or name.startswith("dockerfile")
        or name.startswith("docker-compose")
        or name
        in {
            "compose.yaml",
            "compose.yml",
            "chart.yaml",
            "jenkinsfile",
            ".gitlab-ci.yml",
        }
        or ".github" in parts
        and "workflows" in parts
        or "k8s" in parts
        or "kubernetes" in parts
        or "helm" in parts
    )


def is_entry_point(path: Path, relative: str) -> bool:
    """Return whether a file name/path is a plausible application entry point."""

    name = path.name.lower()
    parts = {part.lower() for part in PurePosixPath(relative).parts}
    return (
        name
        in {
            "app.js",
            "app.py",
            "app.ts",
            "asgi.py",
            "cli.py",
            "main.go",
            "main.py",
            "main.rs",
            "manage.py",
            "server.js",
            "server.py",
            "server.ts",
            "urls.py",
            "wsgi.py",
        }
        or name.startswith("main.")
        or bool(parts & {"controllers", "handlers", "routes", "routers", "resolvers"})
    )


def append_evidence(
    evidence: dict[str, list[str]],
    label: str,
    relative_path: str,
) -> None:
    """Append bounded, de-duplicated evidence for a detected label."""

    values = evidence[label]
    if relative_path not in values and len(values) < MAX_EVIDENCE_PATHS:
        values.append(relative_path)


def sorted_evidence_items(
    evidence: dict[str, list[str]],
) -> list[dict[str, object]]:
    """Convert an evidence mapping to stable serialized objects."""

    return [
        {"name": name, "evidence": sorted(paths)}
        for name, paths in sorted(evidence.items())
        if paths
    ]


def build_profile(
    root: Path,
    *,
    excludes: Sequence[str] = (),
    max_files: int = DEFAULT_MAX_FILES,
    content_budget: int = DEFAULT_CONTENT_BUDGET,
) -> RepositoryProfile:
    """Profile a repository for security-audit applicability.

    Args:
        root: Repository or source-tree root.
        excludes: Repository-relative paths to exclude.
        max_files: Hard limit on files enumerated.
        content_budget: Maximum aggregate bytes read for heuristic content matching.

    Returns:
        A deterministic repository profile.

    Raises:
        ValueError: If arguments are invalid.
        FileNotFoundError: If the root does not exist.
        NotADirectoryError: If the root is not a directory.
    """

    if max_files < 1:
        raise ValueError("max_files must be positive")
    if content_budget < 0:
        raise ValueError("content_budget cannot be negative")

    resolved_root = root.resolve()
    if not resolved_root.exists():
        raise FileNotFoundError(resolved_root)
    if not resolved_root.is_dir():
        raise NotADirectoryError(resolved_root)

    normalized_excludes = normalize_excludes(excludes)
    state = ScanState()
    files = walk_files(resolved_root, normalized_excludes, max_files, state)

    unclassified_m_files = 0
    entry_points_omitted = 0
    language_counts: Counter[str] = Counter()
    manifests: list[str] = []
    infrastructure: list[str] = []
    entry_points: list[str] = []
    framework_evidence: dict[str, list[str]] = defaultdict(list)
    surface_evidence: dict[str, list[str]] = defaultdict(list)
    source_files: list[tuple[Path, str]] = []

    for path in files:
        relative = display_path(path.relative_to(resolved_root).as_posix())
        suffix = path.suffix.lower()
        language = LANGUAGE_EXTENSIONS.get(suffix)
        if suffix == ".m":
            try:
                prefix, _ = read_text_prefix(path, 4096)
            except OSError:
                prefix = ""
            if not OBJECTIVE_C_MARKERS.search(prefix):
                language = None
                unclassified_m_files += 1
        if language:
            language_counts[language] += 1
            source_files.append((path, relative))
        if is_manifest(path):
            manifests.append(relative)
        if is_infrastructure(path, relative):
            infrastructure.append(relative)
            append_evidence(surface_evidence, "infrastructure_or_deployment", relative)
        if path.name.lower() in MOBILE_MANIFEST_NAMES:
            append_evidence(surface_evidence, "mobile_or_desktop", relative)
        if is_entry_point(path, relative):
            if len(entry_points) < MAX_ENTRY_POINTS:
                entry_points.append(relative)
            else:
                entry_points_omitted += 1

    native_languages = {
        "C",
        "C++",
        "C/C++ Header",
        "Objective-C",
        "Objective-C++",
    }
    if native_languages.intersection(language_counts):
        for path, relative in source_files:
            if LANGUAGE_EXTENSIONS.get(path.suffix.lower()) in native_languages:
                append_evidence(surface_evidence, "native_or_unsafe_code", relative)

    web_languages = {"JavaScript", "TypeScript", "Vue", "Svelte"}
    if web_languages.intersection(language_counts):
        for path, relative in source_files:
            if LANGUAGE_EXTENSIONS.get(path.suffix.lower()) in web_languages:
                append_evidence(surface_evidence, "web_or_api", relative)

    content_candidates: list[tuple[Path, str, bool]] = []
    manifest_set = set(manifests)
    for path in files:
        relative = display_path(path.relative_to(resolved_root).as_posix())
        if relative in manifest_set:
            content_candidates.append((path, relative, True))
    content_candidates.extend(
        (path, relative, False)
        for path, relative in source_files
        if relative not in manifest_set
    )

    for path, relative, manifest in content_candidates:
        if state.content_bytes_read >= content_budget:
            break
        try:
            text, bytes_read = read_text_prefix(path)
        except OSError:
            state.unreadable_files += 1
            continue
        state.content_bytes_read += bytes_read
        if not text:
            continue

        lowered = text.lower()
        if manifest:
            for framework, terms in FRAMEWORK_TERMS.items():
                if any(term.lower() in lowered for term in terms):
                    append_evidence(framework_evidence, framework, relative)

        if path.suffix.lower() == ".rs" and RUST_UNSAFE_MARKERS.search(text):
            append_evidence(surface_evidence, "native_or_unsafe_code", relative)

        for surface, patterns in SURFACE_PATTERNS.items():
            if any(pattern.search(text) for pattern in patterns):
                append_evidence(surface_evidence, surface, relative)

        if any(
            term in lowered for term in ("react-native", "androidmanifest", "electron")
        ):
            append_evidence(surface_evidence, "mobile_or_desktop", relative)

    if framework_evidence:
        framework_names = set(framework_evidence)
        if framework_names.intersection(
            {
                "Django",
                "Express",
                "FastAPI",
                "Fastify",
                "Flask",
                "GraphQL",
                "Koa",
                "NestJS",
                "Next.js",
                "Nuxt",
                "Ruby on Rails",
                "Spring",
                "Starlette",
                "aiohttp",
                "axum",
                "gin",
            }
        ):
            for paths in framework_evidence.values():
                for relative in paths:
                    append_evidence(surface_evidence, "web_or_api", relative)
        if framework_names.intersection({"Angular", "Next.js", "Nuxt", "React", "Vue"}):
            for paths in framework_evidence.values():
                for relative in paths:
                    append_evidence(surface_evidence, "browser_rendering", relative)
        if "Electron" in framework_names:
            for relative in framework_evidence["Electron"]:
                append_evidence(surface_evidence, "mobile_or_desktop", relative)

    selected_families: set[str] = {"VAL"}
    for surface in surface_evidence:
        selected_families.update(SURFACE_FAMILIES.get(surface, ()))
    if manifests:
        selected_families.add("OPS")

    warnings: list[str] = [
        "Heuristic profile only; confirm every detected and missing surface manually."
    ]
    if state.truncated:
        warnings.append(
            f"File enumeration stopped at max_files={max_files}; profile is incomplete."
        )
    if state.content_bytes_read >= content_budget and content_candidates:
        warnings.append(
            f"Content inspection reached the {content_budget}-byte budget; later files were not sampled."
        )
    if state.unreadable_files:
        warnings.append(
            f"{state.unreadable_files} files or directories could not be read."
        )
    if state.symlinks_skipped:
        warnings.append(
            f"{state.symlinks_skipped} symbolic links were skipped and not followed."
        )
    if entry_points_omitted:
        warnings.append(
            f"{entry_points_omitted} more entry-point candidates were omitted after "
            f"the first {MAX_ENTRY_POINTS}."
        )
    skipped_outputs = sorted(
        BUILD_OUTPUT_DIRECTORY_NAMES.intersection(state.ignored_directories)
    )
    if skipped_outputs:
        warnings.append(
            "Skipped build-output directories named "
            + ", ".join(skipped_outputs)
            + "; review them manually if they hold source or deployment files."
        )
    if unclassified_m_files:
        warnings.append(
            f"{unclassified_m_files} .m files lack Objective-C markers and were not "
            "classified (possibly MATLAB)."
        )

    scan = {
        "files_scanned": state.files_scanned,
        "max_files": max_files,
        "content_bytes_read": state.content_bytes_read,
        "content_budget": content_budget,
        "truncated": state.truncated,
        "symlinks_skipped": state.symlinks_skipped,
        "unreadable_files": state.unreadable_files,
        "explicit_excludes": [display_path(value) for value in normalized_excludes],
        "ignored_directories": dict(sorted(state.ignored_directories.items())),
    }
    languages = [
        {"name": name, "files": count}
        for name, count in sorted(
            language_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    family_rank = {name: index for index, name in enumerate(FAMILY_ORDER)}
    recommended_families = sorted(
        selected_families, key=lambda name: (family_rank.get(name, 999), name)
    )
    recommended_reference_packs = sorted(
        {
            REFERENCE_PACK_BY_LANGUAGE[language]
            for language in language_counts
            if language in REFERENCE_PACK_BY_LANGUAGE
        }
    )

    return RepositoryProfile(
        schema_version=SCHEMA_VERSION,
        root=display_path(str(resolved_root)),
        scan=scan,
        languages=languages,
        manifests=sorted(set(manifests)),
        frameworks=sorted_evidence_items(framework_evidence),
        infrastructure=sorted(set(infrastructure)),
        entry_point_candidates=sorted(set(entry_points)),
        security_surfaces=sorted_evidence_items(surface_evidence),
        recommended_reference_packs=recommended_reference_packs,
        recommended_taxonomy_families=recommended_families,
        warnings=warnings,
    )


def render_markdown(profile: RepositoryProfile) -> str:
    """Render a compact human-readable profile."""

    data = profile.to_dict()
    scan = data["scan"]
    assert isinstance(scan, dict)
    lines = [
        "# Repository Security Profile",
        "",
        f"- Root: `{data['root']}`",
        f"- Files scanned: {scan['files_scanned']}",
        f"- Truncated: {str(scan['truncated']).lower()}",
        f"- Content bytes sampled: {scan['content_bytes_read']}",
        "",
        "## Languages",
        "",
    ]
    languages = data["languages"]
    assert isinstance(languages, list)
    if languages:
        for item in languages:
            assert isinstance(item, dict)
            lines.append(f"- {item['name']}: {item['files']} files")
    else:
        lines.append("- No recognized source language")

    for heading, key in (
        ("Dependency and build manifests", "manifests"),
        ("Infrastructure and deployment files", "infrastructure"),
        ("Entry-point candidates", "entry_point_candidates"),
    ):
        lines.extend(("", f"## {heading}", ""))
        paths = data[key]
        assert isinstance(paths, list)
        if paths:
            lines.extend(f"- `{path}`" for path in paths)
        else:
            lines.append("- None detected heuristically")

    for heading, key in (
        ("Framework evidence", "frameworks"),
        ("Security surfaces", "security_surfaces"),
    ):
        lines.extend(("", f"## {heading}", ""))
        items = data[key]
        assert isinstance(items, list)
        if not items:
            lines.append("- None detected heuristically")
            continue
        for item in items:
            assert isinstance(item, dict)
            evidence = item["evidence"]
            assert isinstance(evidence, list)
            joined = ", ".join(f"`{path}`" for path in evidence)
            lines.append(f"- {item['name']}: {joined}")

    reference_packs = data["recommended_reference_packs"]
    assert isinstance(reference_packs, list)
    lines.extend(("", "## Recommended reference packs", ""))
    if reference_packs:
        lines.extend(f"- `{path}`" for path in reference_packs)
    else:
        lines.append("- No language-specific pack available")

    families = data["recommended_taxonomy_families"]
    assert isinstance(families, list)
    lines.extend(
        (
            "",
            "## Recommended taxonomy families",
            "",
            f"- {', '.join(str(value) for value in families)}",
            "",
            "## Warnings",
            "",
        )
    )
    warnings = data["warnings"]
    assert isinstance(warnings, list)
    lines.extend(f"- {warning}" for warning in warnings)
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Profile a repository to select applicable security audit checks."
    )
    parser.add_argument("root", type=Path, nargs="?", default=Path("."))
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="markdown",
        dest="output_format",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="RELATIVE_PATH",
        help="Exclude a repository-relative path prefix; repeat as needed.",
    )
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument(
        "--content-budget",
        type=int,
        default=DEFAULT_CONTENT_BUDGET,
        help="Maximum aggregate bytes to inspect for heuristic content signals.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the repository profiler CLI."""

    args = parse_args(argv)
    try:
        profile = build_profile(
            args.root,
            excludes=args.exclude,
            max_files=args.max_files,
            content_budget=args.content_budget,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.output_format == "json":
        json.dump(profile.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_markdown(profile))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
