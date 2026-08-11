"""Bounded APFS materialization checks that never read tracked file contents."""

from __future__ import annotations

from dataclasses import dataclass
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
