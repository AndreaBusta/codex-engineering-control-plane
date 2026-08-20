"""Bounded APFS materialization checks that never read tracked file contents."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import time

from control_plane.repository import observed_directory, run_bounded_git


DATALESS_FLAG = 0x40000000
_MAX_INVENTORY_BYTES = 1_048_576
_MAX_GIT_CONTROL_BYTES = 4_096
_MAX_GIT_STATE_DEPTH = 64
_GIT_STATE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class MaterializationResult:
    ok: bool
    status: str
    tracked_files: int
    dataless_paths: tuple[str, ...]
    error_code: str | None


def _file_flags(path: Path) -> int:
    return int(getattr(path.lstat(), "st_flags", 0))


def _metadata_flags(metadata: os.stat_result, path: Path) -> int:
    del path
    return int(getattr(metadata, "st_flags", 0))


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise TimeoutError("materialization deadline expired")


def _canonical_repository_root(repository: Path, *, deadline: float) -> Path:
    _check_deadline(deadline)
    candidate = Path(os.path.abspath(repository))
    try:
        with observed_directory(candidate, deadline=deadline) as opened:
            root, _, _ = opened
    except ValueError as error:
        if isinstance(error.__cause__, TimeoutError):
            raise TimeoutError("repository root observation timed out") from error
        raise OSError("repository root is unavailable") from error
    _check_deadline(deadline)
    return root


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        int(getattr(metadata, "st_flags", 0)),
    )


@dataclass(frozen=True)
class _GitStateRoot:
    path: Path
    identity: tuple[int, ...]


def _read_git_control_file(
    parent: int,
    name: str,
    path: Path,
    *,
    deadline: float,
) -> str:
    """Read one tiny Git pointer without following links or blocking on FIFOs."""

    descriptor = -1
    try:
        _check_deadline(deadline)
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        before_flags = _metadata_flags(before, path)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > _MAX_GIT_CONTROL_BYTES
            or before_flags & DATALESS_FLAG
        ):
            raise OSError("unsafe Git control file")
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        payload = bytearray()
        while len(payload) <= _MAX_GIT_CONTROL_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, _MAX_GIT_CONTROL_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        after_flags = _metadata_flags(named, path)
        _check_deadline(deadline)
        if (
            _identity(before) != _identity(opened)
            or _identity(before) != _identity(after)
            or _identity(before) != _identity(named)
            or len(payload) > _MAX_GIT_CONTROL_BYTES
            or len(payload) != opened.st_size
            or after_flags & DATALESS_FLAG
        ):
            raise OSError("Git control file changed during read")
    except TimeoutError:
        raise
    except (OSError, ValueError) as error:
        raise OSError("Git control file is not safely observable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = bytes(payload).decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise OSError("Git control file is not UTF-8") from error
    if value.endswith("\n"):
        value = value[:-1]
    if not value or "\0" in value:
        raise OSError("Git control file is malformed")
    return value


def _canonical_git_directory(path: Path, *, deadline: float) -> _GitStateRoot:
    candidate = Path(os.path.abspath(path))
    try:
        with observed_directory(candidate, deadline=deadline) as opened:
            canonical, _, metadata = opened
            flags = _metadata_flags(metadata, candidate)
    except ValueError as error:
        if isinstance(error.__cause__, TimeoutError):
            raise TimeoutError("Git directory observation timed out") from error
        raise OSError("Git directory is not safely observable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink < 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or flags & DATALESS_FLAG
    ):
        raise OSError("Git directory is unsafe")
    return _GitStateRoot(canonical, _identity(metadata))


def _git_state_roots(
    repository: Path,
    *,
    deadline: float,
) -> tuple[_GitStateRoot, ...]:
    """Discover worktree/common Git dirs from filesystem metadata only."""

    root = _canonical_repository_root(Path(repository), deadline=deadline)
    entry = root / ".git"
    try:
        with observed_directory(root, deadline=deadline) as root_observation:
            _, root_descriptor, _ = root_observation
            metadata = os.stat(
                ".git",
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            flags = _metadata_flags(metadata, entry)
            if flags & DATALESS_FLAG:
                raise OSError("Git directory entry is unsafe")
            if stat.S_ISREG(metadata.st_mode):
                pointer = _read_git_control_file(
                    root_descriptor,
                    ".git",
                    entry,
                    deadline=deadline,
                )
            else:
                pointer = None
    except TimeoutError:
        raise
    except ValueError as error:
        if isinstance(error.__cause__, TimeoutError):
            raise TimeoutError("Git directory entry observation timed out") from error
        raise OSError("Git directory entry is unavailable") from error
    except OSError as error:
        raise OSError("Git directory entry is unavailable") from error
    if stat.S_ISDIR(metadata.st_mode):
        git_root = _canonical_git_directory(entry, deadline=deadline)
    elif stat.S_ISREG(metadata.st_mode):
        assert pointer is not None
        if not pointer.startswith("gitdir: "):
            raise OSError("Git directory pointer is malformed")
        candidate = Path(pointer.removeprefix("gitdir: "))
        if not candidate.is_absolute():
            candidate = root / candidate
        git_root = _canonical_git_directory(candidate, deadline=deadline)
    else:
        raise OSError("Git directory entry is unsafe")
    git_directory = git_root.path

    try:
        with observed_directory(
            git_directory,
            deadline=deadline,
        ) as git_observation:
            _, git_descriptor, _ = git_observation
            try:
                os.stat(
                    "commondir",
                    dir_fd=git_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                value = None
            else:
                value = _read_git_control_file(
                    git_descriptor,
                    "commondir",
                    git_directory / "commondir",
                    deadline=deadline,
                )
    except ValueError as error:
        if isinstance(error.__cause__, TimeoutError):
            raise TimeoutError("Git common directory observation timed out") from error
        raise OSError("Git common directory pointer is unavailable") from error
    if value is None:
        common_root = git_root
    else:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = git_directory / candidate
        common_root = _canonical_git_directory(candidate, deadline=deadline)

    roots = (git_root, common_root)
    maximal: list[_GitStateRoot] = []
    for candidate in roots:
        if any(
            candidate.path != other.path
            and candidate.path.is_relative_to(other.path)
            for other in roots
        ):
            continue
        if not any(item.path == candidate.path for item in maximal):
            maximal.append(candidate)
    return tuple(maximal)


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
    deadline = time.monotonic() + _GIT_STATE_TIMEOUT_SECONDS
    try:
        root = _canonical_repository_root(repository, deadline=deadline)
        _git_state_roots(repository, deadline=deadline)
        completed = run_bounded_git(
            root,
            ("ls-files", "-z"),
            output_limit=_MAX_INVENTORY_BYTES,
            timeout=10.0,
        )
    except (OSError, ValueError):
        return MaterializationResult(
            False, "UNKNOWN", 0, (), "E_MATERIALIZATION_INVENTORY"
        )
    if completed.returncode != 0:
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
            deleted = run_bounded_git(
                root,
                ("ls-files", "--deleted", "-z"),
                output_limit=_MAX_INVENTORY_BYTES,
                timeout=10.0,
            )
            deleted_paths = {
                item.decode("utf-8", errors="surrogateescape")
                for item in deleted.stdout.split(b"\0")
                if item
            }
            if deleted.returncode != 0 or not set(missing).issubset(deleted_paths):
                raise OSError("tracked path disappeared during inspection")
    except (OSError, ValueError):
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


def _area_for(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return "git_dir"
    head = relative.parts[0] if relative.parts else ""
    return _GIT_STATE_AREAS.get(head, "git_dir")


class _GitStateStatError(Exception):
    """A Git-state entry could not be proven safe and stable."""


class _GitStateLimit(Exception):
    """A declared traversal bound was exhausted."""


def _validate_git_directory(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink < 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise _GitStateStatError


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _walk_git_state(
    root: Path,
    *,
    expected_root_identity: tuple[int, ...],
    max_entries: int,
    deadline: float,
    state: dict[str, object],
) -> None:
    def check_budget() -> None:
        if time.monotonic() > deadline:
            raise _GitStateLimit

    def walk(
        directory: Path,
        descriptor: int,
        expected: tuple[int, ...],
        depth: int,
    ) -> None:
        check_budget()
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    check_budget()
                    state["entries"] = int(state["entries"]) + 1
                    if int(state["entries"]) > max_entries:
                        raise _GitStateLimit
                    path = directory / entry.name
                    try:
                        before = os.stat(
                            entry.name,
                            dir_fd=descriptor,
                            follow_symlinks=False,
                        )
                    except OSError as error:
                        raise _GitStateStatError from error
                    if stat.S_ISREG(before.st_mode):
                        try:
                            flags = _metadata_flags(before, path)
                            after = os.stat(
                                entry.name,
                                dir_fd=descriptor,
                                follow_symlinks=False,
                            )
                        except OSError as error:
                            raise _GitStateStatError from error
                        if _identity(before) != _identity(after):
                            raise _GitStateStatError
                        if tuple(
                            part.casefold()
                            for part in path.relative_to(root).parts
                        ) == (
                            "objects",
                            "info",
                            "alternates",
                        ):
                            raise _GitStateStatError
                        state["scanned"] = int(state["scanned"]) + 1
                        if flags & DATALESS_FLAG:
                            state["dataless"] = int(state["dataless"]) + 1
                            cast_areas = state["areas"]
                            if not isinstance(cast_areas, set):
                                raise _GitStateStatError
                            cast_areas.add(_area_for(root, path))
                        continue
                    if not stat.S_ISDIR(before.st_mode):
                        raise _GitStateStatError
                    if depth >= _MAX_GIT_STATE_DEPTH:
                        raise _GitStateLimit
                    try:
                        if _metadata_flags(before, path) & DATALESS_FLAG:
                            raise _GitStateStatError
                        child = os.open(
                            entry.name,
                            _directory_open_flags(),
                            dir_fd=descriptor,
                        )
                    except OSError as error:
                        raise _GitStateStatError from error
                    try:
                        opened = os.fstat(child)
                        named_after_open = os.stat(
                            entry.name,
                            dir_fd=descriptor,
                            follow_symlinks=False,
                        )
                        _validate_git_directory(opened)
                        if (
                            _identity(before) != _identity(opened)
                            or _identity(before) != _identity(named_after_open)
                        ):
                            raise _GitStateStatError
                        walk(path, child, _identity(opened), depth + 1)
                        after_walk = os.fstat(child)
                        named_after_walk = os.stat(
                            entry.name,
                            dir_fd=descriptor,
                            follow_symlinks=False,
                        )
                        if (
                            _identity(after_walk) != _identity(opened)
                            or _identity(named_after_walk) != _identity(opened)
                        ):
                            raise _GitStateStatError
                    finally:
                        os.close(child)
        except _GitStateLimit:
            raise
        except _GitStateStatError:
            raise
        except OSError as error:
            raise _GitStateStatError from error
        try:
            after = os.fstat(descriptor)
        except OSError as error:
            raise _GitStateStatError from error
        if _identity(after) != expected:
            raise _GitStateStatError

    try:
        with observed_directory(root, deadline=deadline) as root_observation:
            _, descriptor, opened = root_observation
            if _metadata_flags(opened, root) & DATALESS_FLAG:
                raise _GitStateStatError
            _validate_git_directory(opened)
            if _identity(opened) != expected_root_identity:
                raise _GitStateStatError
            walk(root, descriptor, expected_root_identity, 0)
    except ValueError as error:
        if isinstance(error.__cause__, TimeoutError):
            raise _GitStateLimit from error
        raise _GitStateStatError from error


def inspect_git_state_materialization(
    repository: Path,
    *,
    max_files: int = 50_000,
) -> GitStateMaterialization:
    """Inspect Git state inode flags without following links or reading content."""

    if (
        not isinstance(max_files, int)
        or isinstance(max_files, bool)
        or not 1 <= max_files <= 50_000
    ):
        raise ValueError("E_MATERIALIZATION_LIMIT: invalid git state file limit")
    deadline = time.monotonic() + _GIT_STATE_TIMEOUT_SECONDS
    try:
        roots = _git_state_roots(repository, deadline=deadline)
    except TimeoutError:
        return GitStateMaterialization(
            False, "UNKNOWN", 0, 0, (), True, "E_MATERIALIZATION_LIMIT"
        )
    except (OSError, ValueError):
        return GitStateMaterialization(
            False, "UNKNOWN", 0, 0, (), False, "E_MATERIALIZATION_INVENTORY"
        )
    state: dict[str, object] = {
        "entries": 0,
        "scanned": 0,
        "dataless": 0,
        "areas": set(),
    }
    try:
        for root in roots:
            _walk_git_state(
                root.path,
                expected_root_identity=root.identity,
                max_entries=max_files,
                deadline=deadline,
                state=state,
            )
    except _GitStateLimit:
        return GitStateMaterialization(
            False,
            "UNKNOWN",
            int(state["scanned"]),
            0,
            (),
            True,
            "E_MATERIALIZATION_LIMIT",
        )
    except (_GitStateStatError, OSError):
        return GitStateMaterialization(
            False,
            "UNKNOWN",
            int(state["scanned"]),
            0,
            (),
            False,
            "E_MATERIALIZATION_STAT",
        )
    dataless = int(state["dataless"])
    areas = state["areas"]
    if not isinstance(areas, set):
        return GitStateMaterialization(
            False, "UNKNOWN", 0, 0, (), False, "E_MATERIALIZATION_STAT"
        )
    return GitStateMaterialization(
        not dataless,
        "PASS" if not dataless else "FAIL",
        int(state["scanned"]),
        dataless,
        tuple(sorted(areas)),
        False,
        None if not dataless else "E_MATERIALIZATION_DATALESS",
    )
