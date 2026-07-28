"""Repository discovery and worktree-local state paths."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


_REPOSITORY_REDIRECT_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)


class RepositoryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def git_environment() -> dict[str, str]:
    """Preserve user authentication while removing repository redirection."""

    return {
        key: value
        for key, value in os.environ.items()
        if key not in _REPOSITORY_REDIRECT_ENV
    }


def _git(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=path,
            check=False,
            capture_output=True,
            text=True,
            env=git_environment(),
        )
    except OSError:
        return subprocess.CompletedProcess(
            args=["git", *arguments],
            returncode=128,
            stdout="",
            stderr="",
        )


def discover_repository(path: Path) -> Path:
    """Find the Git worktree root before resolving project-owned files."""

    result = _git(path, "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not result.stdout.strip():
        raise RepositoryError(
            "E_GIT_NOT_REPOSITORY", "The target is not inside a Git worktree."
        )
    return Path(result.stdout.strip()).resolve()


def worktree_git_dir(path: Path) -> Path:
    """Return the Git dir unique to this worktree, not the shared common dir."""

    root = discover_repository(path)
    result = _git(root, "rev-parse", "--path-format=absolute", "--git-dir")
    if result.returncode != 0 or not result.stdout.strip():
        raise RepositoryError(
            "E_GIT_DIR_UNKNOWN", "The worktree-specific Git dir is unavailable."
        )
    return Path(result.stdout.strip()).resolve()
