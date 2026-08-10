"""Repository discovery and worktree-local state paths."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
from typing import Sequence


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
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)

_TRUSTED_GIT = Path("/usr/bin/git")
_CLOSED_GIT_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.external=",
    "-c",
    "color.ui=false",
)
_MAX_GIT_FILTER_PATH_BYTES = 1_048_576
_MAX_GIT_FILTER_OUTPUT_BYTES = 4_194_304
_MAX_GIT_FILTER_PATHS = 20_000


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


def trusted_git_executable() -> str:
    """Return the immutable system Git used for subject observations."""

    if (
        not _TRUSTED_GIT.is_absolute()
        or _TRUSTED_GIT.is_symlink()
        or not _TRUSTED_GIT.is_file()
        or not os.access(_TRUSTED_GIT, os.X_OK)
    ):
        raise OSError("trusted Git executable is unavailable")
    return str(_TRUSTED_GIT)


def _normalize_trusted_git_arguments(
    arguments: Sequence[str],
) -> tuple[str, ...]:
    normalized = tuple(arguments)
    if normalized[:1] != ("diff",):
        return normalized
    tail = normalized[1:]
    try:
        separator = tail.index("--")
    except ValueError:
        separator = len(tail)
    options = tuple(
        argument
        for argument in tail[:separator]
        if argument not in {"--no-ext-diff", "--no-textconv"}
    )
    suffix = tail[separator:]
    return (
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        *options,
        *suffix,
    )


def trusted_git_argv(
    repository: Path | str, arguments: Sequence[str]
) -> list[str]:
    """Build a closed read-only Git observation command."""

    normalized_arguments = _normalize_trusted_git_arguments(arguments)
    return [
        trusted_git_executable(),
        *_CLOSED_GIT_CONFIG,
        "-C",
        str(repository),
        *normalized_arguments,
    ]


def trusted_git_environment(
    *, index_file: Path | str | None = None
) -> dict[str, str]:
    """Close Git redirects/config while allowing one explicit index binding."""

    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_GRAFT_FILE": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "GCM_INTERACTIVE": "never",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }
    if index_file is not None:
        candidate = Path(index_file)
        try:
            parent = candidate.parent.resolve(strict=True)
            try:
                metadata = candidate.lstat()
            except FileNotFoundError:
                metadata = None
        except OSError as error:
            raise ValueError(
                "E_GIT_INDEX_FILE: explicit index path is unavailable"
            ) from error
        if (
            not candidate.is_absolute()
            or not parent.is_dir()
            or (
                metadata is not None
                and (
                    stat.S_ISLNK(metadata.st_mode)
                    or not stat.S_ISREG(metadata.st_mode)
                )
            )
        ):
            raise ValueError(
                "E_GIT_INDEX_FILE: explicit index path is unsafe"
            )
        environment["GIT_INDEX_FILE"] = str(candidate)
    return environment


def assert_no_external_git_filters(
    repository: Path | str,
    paths: Sequence[str] | None = None,
    *,
    index_file: Path | str | None = None,
) -> None:
    """Fail before a worktree observation can invoke an attribute filter."""

    environment = trusted_git_environment(index_file=index_file)
    if paths is None:
        try:
            inventory = subprocess.run(
                trusted_git_argv(repository, ("ls-files", "-z")),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(
                "E_GIT_FILTER: clean-filter inventory is incomplete"
            ) from error
        raw_paths = tuple(
            item for item in inventory.stdout.split(b"\0") if item
        )
        if (
            inventory.returncode != 0
            or len(inventory.stdout) > _MAX_GIT_FILTER_PATH_BYTES
            or len(raw_paths) > _MAX_GIT_FILTER_PATHS
        ):
            raise ValueError(
                "E_GIT_FILTER: clean-filter inventory is incomplete"
            )
    else:
        if len(paths) > _MAX_GIT_FILTER_PATHS:
            raise ValueError(
                "E_GIT_FILTER: clean-filter inventory is incomplete"
            )
        encoded: list[bytes] = []
        for value in paths:
            if not isinstance(value, str):
                raise ValueError(
                    "E_GIT_FILTER: clean-filter inventory is incomplete"
                )
            pure = PurePosixPath(value)
            if (
                not value
                or pure.is_absolute()
                or ".." in pure.parts
                or pure.as_posix() != value
                or "\0" in value
            ):
                raise ValueError(
                    "E_GIT_FILTER: clean-filter inventory is incomplete"
                )
            try:
                encoded.append(value.encode("utf-8", errors="strict"))
            except UnicodeEncodeError as error:
                raise ValueError(
                    "E_GIT_FILTER: clean-filter inventory is incomplete"
                ) from error
        raw_paths = tuple(encoded)
    if not raw_paths:
        return
    payload = b"\0".join(raw_paths) + b"\0"
    if len(payload) > _MAX_GIT_FILTER_PATH_BYTES:
        raise ValueError(
            "E_GIT_FILTER: clean-filter inventory is incomplete"
        )
    try:
        completed = subprocess.run(
            trusted_git_argv(
                repository, ("check-attr", "--stdin", "-z", "filter")
            ),
            check=False,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "E_GIT_FILTER: clean-filter inventory is incomplete"
        ) from error
    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if (
        completed.returncode != 0
        or len(completed.stdout) > _MAX_GIT_FILTER_OUTPUT_BYTES
        or len(fields) != len(raw_paths) * 3
        or tuple(fields[0::3]) != raw_paths
        or any(field != b"filter" for field in fields[1::3])
    ):
        raise ValueError(
            "E_GIT_FILTER: clean-filter inventory is incomplete"
        )
    if any(
        field not in {b"unspecified", b"unset"}
        for field in fields[2::3]
    ):
        raise ValueError(
            "E_GIT_FILTER: external clean filters are not permitted"
        )


def _git(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [trusted_git_executable(), *_CLOSED_GIT_CONFIG, *arguments],
            cwd=path,
            check=False,
            capture_output=True,
            text=True,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(
            args=[str(_TRUSTED_GIT), *arguments],
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


def git_common_dir(path: Path) -> Path:
    """Return the canonical common Git dir shared by all registered worktrees."""

    root = discover_repository(path)
    result = _git(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RepositoryError(
            "E_GIT_COMMON_DIR_UNKNOWN",
            "The common Git dir is unavailable.",
        )
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = root / common
    common = common.resolve()
    if common.is_symlink() or not common.is_dir():
        raise RepositoryError(
            "E_GIT_COMMON_DIR_UNKNOWN",
            "The common Git dir is invalid.",
        )
    return common
