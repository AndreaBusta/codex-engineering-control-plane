"""Bounded APFS materialization checks that never read tracked file contents."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from control_plane.repository import trusted_git_argv, trusted_git_environment


DATALESS_FLAG = 0x40000000
_MAX_INVENTORY_BYTES = 1_048_576


@dataclass(frozen=True)
class MaterializationResult:
    ok: bool
    status: str
    tracked_files: int
    dataless_paths: tuple[str, ...]
    error_code: str | None


def _file_flags(path: Path) -> int:
    return int(getattr(path.lstat(), "st_flags", 0))


def inspect_tracked_materialization(
    repository: Path,
    *,
    max_files: int = 20_000,
) -> MaterializationResult:
    """Inspect tracked inode flags without opening tracked file contents."""

    if (
        not isinstance(max_files, int)
        or isinstance(max_files, bool)
        or not 1 <= max_files <= 100_000
    ):
        raise ValueError("E_MATERIALIZATION_LIMIT: invalid tracked file limit")
    root = repository.resolve()
    try:
        completed = subprocess.run(
            trusted_git_argv(root, ("ls-files", "-z")),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return MaterializationResult(
            False, "UNKNOWN", 0, (), "E_MATERIALIZATION_INVENTORY"
        )
    if completed.returncode != 0 or len(completed.stdout) > _MAX_INVENTORY_BYTES:
        return MaterializationResult(
            False, "UNKNOWN", 0, (), "E_MATERIALIZATION_INVENTORY"
        )
    raw_paths = [item for item in completed.stdout.split(b"\0") if item]
    if len(raw_paths) > max_files:
        return MaterializationResult(
            False,
            "UNKNOWN",
            len(raw_paths),
            (),
            "E_MATERIALIZATION_LIMIT",
        )
    relative_paths = tuple(
        item.decode("utf-8", errors="surrogateescape") for item in raw_paths
    )
    dataless_items: list[str] = []
    missing: list[str] = []
    try:
        for relative in relative_paths:
            try:
                flags = _file_flags(root / relative)
            except FileNotFoundError:
                missing.append(relative)
                continue
            if flags & DATALESS_FLAG:
                dataless_items.append(relative)
        if missing:
            deleted = subprocess.run(
                trusted_git_argv(
                    root, ("ls-files", "--deleted", "-z")
                ),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=trusted_git_environment(),
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
            deleted_paths = {
                item.decode("utf-8", errors="surrogateescape")
                for item in deleted.stdout.split(b"\0")
                if item
            }
            if deleted.returncode != 0 or not set(missing).issubset(deleted_paths):
                raise OSError("tracked path disappeared during inspection")
    except (OSError, subprocess.SubprocessError):
        return MaterializationResult(
            False,
            "UNKNOWN",
            len(raw_paths),
            (),
            "E_MATERIALIZATION_STAT",
        )
    dataless = tuple(dataless_items)
    return MaterializationResult(
        not dataless,
        "PASS" if not dataless else "FAIL",
        len(relative_paths),
        dataless,
        None if not dataless else "E_MATERIALIZATION_DATALESS",
    )


_GIT_STATE_AREAS = {
    "objects": "objects",
    "codex-control-plane-core": "core_state",
    "codex-control-plane": "core_state",
    "worktrees": "worktrees",
    "refs": "refs",
}


@dataclass(frozen=True)
class GitStateMaterialization:
    ok: bool
    status: str
    scanned_files: int
    dataless_files: int
    areas: tuple[str, ...]
    truncated: bool
    error_code: str | None


def _git_state_roots(repository: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    for arguments in (
        ("rev-parse", "--absolute-git-dir"),
        ("rev-parse", "--path-format=absolute", "--git-common-dir"),
    ):
        try:
            completed = subprocess.run(
                trusted_git_argv(repository, arguments),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=trusted_git_environment(),
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return ()
        if completed.returncode != 0:
            return ()
        candidate = Path(completed.stdout.decode("utf-8", errors="replace").strip())
        if candidate.is_absolute() and candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def _area_for(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return "git_dir"
    head = relative.parts[0] if relative.parts else ""
    return _GIT_STATE_AREAS.get(head, "git_dir")


def inspect_git_state_materialization(
    repository: Path,
    *,
    max_files: int = 50_000,
) -> GitStateMaterialization:
    """Inspect Git state inode flags without following links or reading content."""

    if (
        not isinstance(max_files, int)
        or isinstance(max_files, bool)
        or not 1 <= max_files <= 100_000
    ):
        raise ValueError("E_MATERIALIZATION_LIMIT: invalid git state file limit")
    roots = _git_state_roots(repository.resolve())
    if not roots:
        return GitStateMaterialization(
            False, "UNKNOWN", 0, 0, (), False, "E_MATERIALIZATION_INVENTORY"
        )
    scanned = 0
    areas: set[str] = set()
    dataless = 0
    try:
        for root in roots:
            for current, directories, files in os.walk(root, followlinks=False):
                directories[:] = [
                    name
                    for name in directories
                    if not (Path(current) / name).is_symlink()
                ]
                for name in files:
                    path = Path(current) / name
                    if path.is_symlink():
                        continue
                    scanned += 1
                    if scanned > max_files:
                        return GitStateMaterialization(
                            False,
                            "UNKNOWN",
                            scanned,
                            0,
                            (),
                            True,
                            "E_MATERIALIZATION_LIMIT",
                        )
                    if _file_flags(path) & DATALESS_FLAG:
                        dataless += 1
                        areas.add(_area_for(root, path))
    except OSError:
        return GitStateMaterialization(
            False, "UNKNOWN", scanned, 0, (), False, "E_MATERIALIZATION_STAT"
        )
    return GitStateMaterialization(
        not dataless,
        "PASS" if not dataless else "FAIL",
        scanned,
        dataless,
        tuple(sorted(areas)),
        False,
        None if not dataless else "E_MATERIALIZATION_DATALESS",
    )
