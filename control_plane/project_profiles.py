"""Bounded, read-only project-type detection from repository markers."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from typing import Iterable


IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".worktrees",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "DerivedData",
        ".venv",
        "venv",
        "docs",
        "doc",
        "test",
        "tests",
        "fixtures",
        "examples",
        "example",
        "samples",
        "sample",
    }
)
MAX_DEPTH = 5
MAX_ENTRIES = 20_000
MAX_EVIDENCE = 32


def _scan(root: Path) -> tuple[set[str], set[str], bool]:
    files: set[str] = set()
    directories: set[str] = set()
    observed = 0
    for current, names, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        depth = len(current_path.relative_to(root).parts)
        names[:] = sorted(
            name
            for name in names
            if name not in IGNORED_DIRECTORIES
            and not (current_path / name).is_symlink()
            and depth < MAX_DEPTH
        )
        for name in names:
            relative = (current_path / name).relative_to(root).as_posix()
            directories.add(relative)
            observed += 1
            if observed >= MAX_ENTRIES:
                return files, directories, True
        for name in sorted(filenames):
            path = current_path / name
            if path.is_symlink():
                continue
            files.add(path.relative_to(root).as_posix())
            observed += 1
            if observed >= MAX_ENTRIES:
                return files, directories, True
    return files, directories, False


def _basename_matches(paths: Iterable[str], names: set[str]) -> list[str]:
    return sorted(
        path for path in paths if PurePosixPath(path).name in names
    )


def detect_project_profile(repo_root: Path) -> dict:
    """Return profile evidence without reading source or manifest contents."""

    root = repo_root.resolve()
    files, directories, scan_truncated = _scan(root)
    evidence: dict[str, list[str]] = {}

    ios = sorted(
        {
            *(
                path
                for path in directories
                if path.endswith((".xcodeproj", ".xcworkspace"))
            ),
            *_basename_matches(files, {"Info.plist", "project.pbxproj"}),
        }
    )
    if any(path.endswith((".xcodeproj", ".xcworkspace")) for path in ios):
        evidence["ios"] = ios

    android = sorted(
        {
            *_basename_matches(
                files,
                {
                    "settings.gradle",
                    "settings.gradle.kts",
                    "build.gradle",
                    "build.gradle.kts",
                    "AndroidManifest.xml",
                },
            )
        }
    )
    if any(path.endswith("AndroidManifest.xml") for path in android) and any(
        PurePosixPath(path).name.startswith(("settings.gradle", "build.gradle"))
        for path in android
    ):
        evidence["android"] = android

    web = sorted(
        {
            *_basename_matches(
                files,
                {
                    "manifest.webmanifest",
                    "service-worker.js",
                    "service-worker.ts",
                    "sw.js",
                    "vite.config.js",
                    "vite.config.ts",
                    "next.config.js",
                    "next.config.mjs",
                },
            )
        }
    )
    if web:
        evidence["web_pwa"] = web

    backend_markers = sorted(
        {
            *_basename_matches(
                files,
                {
                    "Dockerfile",
                    "docker-compose.yml",
                    "docker-compose.yaml",
                    "pyproject.toml",
                    "go.mod",
                    "Cargo.toml",
                },
            ),
            *(
                path
                for path in directories
                if PurePosixPath(path).name
                in {"api", "server", "migrations", "prisma"}
            ),
        }
    )
    backend_dirs = {
        PurePosixPath(path).name
        for path in backend_markers
        if path in directories
    }
    if backend_dirs.intersection({"api", "server", "migrations", "prisma"}):
        evidence["saas_backend"] = backend_markers

    ai_markers = sorted(
        path
        for path in directories
        if PurePosixPath(path).name
        in {"prompts", "evals", "evaluations", "pipelines", "providers"}
    )
    if len({PurePosixPath(path).name for path in ai_markers}) >= 2:
        evidence["ai_text_pipeline"] = ai_markers

    profiles = sorted(evidence)
    if not profiles:
        profiles = ["generic"]
        evidence["generic"] = _basename_matches(
            files,
            {
                "Makefile",
                "package.json",
                "pyproject.toml",
                "go.mod",
                "Cargo.toml",
                "Package.swift",
            },
        )
    kind = profiles[0] if len(profiles) == 1 else "hybrid"
    flattened = sorted(
        {path for paths in evidence.values() for path in paths}
    )[:MAX_EVIDENCE]
    return {
        "schema_version": 1,
        "kind": kind,
        "profiles": profiles,
        "evidence": flattened,
        "confidence": (
            "bounded_scan_incomplete"
            if scan_truncated
            else ("fallback" if profiles == ["generic"] else "marker_evidence")
        ),
        "truncated": scan_truncated
        or len(flattened)
        < len({path for paths in evidence.values() for path in paths}),
    }
