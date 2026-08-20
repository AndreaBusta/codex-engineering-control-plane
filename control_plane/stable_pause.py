"""Verify-only Stable Pause observation for Core-owned local state."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import time
import tomllib
from typing import Any, Mapping, Sequence

from control_plane.contracts import (
    SHA256_DIGEST,
    canonical_json,
    derive_stable_pause_status,
    load_active_adoption_journal,
    stable_pause_checkpoint_digest,
    validate_stable_pause_observation,
    validate_task_id,
)
from control_plane.repository import (
    RepositoryError,
    assert_no_external_git_filters,
    discover_repository,
    git_common_dir,
    trusted_git_argv,
    trusted_git_environment,
    worktree_git_dir,
)


_GIT_TIMEOUT_SECONDS = 5.0
_GIT_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_STATUS_RECORDS = 4096
_MAX_INDEX_RECORDS = 20_000
_MAX_PATH_BYTES = 4096
_MAX_PATH_DEPTH = 32
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_AGGREGATE_BYTES = 64 * 1024 * 1024
_FILE_PROVIDER_DATALESS = 0x40000000
_STREAM_CHUNK = 65_536
_PROCESS_REAP_SECONDS = 0.25
_MAX_STATE_FILE_BYTES = 1024 * 1024
_MAX_STATE_ENTRIES = 4096
_MAX_STATE_TOTAL_BYTES = 8 * 1024 * 1024
_ADOPTION_LIFECYCLE = "journal-bound-v1"
_TASK_LOCK_NAME = re.compile(r"^[0-9a-f]{64}\.lock$", re.ASCII)
_JSON_RECORD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}\.json$", re.ASCII)

_STATUS_ARGUMENTS = (
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
    "--no-renames",
)
_IGNORED_ARGUMENTS = (
    "status",
    "--porcelain=v1",
    "-z",
    "--untracked-files=all",
    "--no-renames",
    "--ignored=matching",
)
_INDEX_ARGUMENTS = ("ls-files", "--stage", "-z")
_INDEX_FLAGS_ARGUMENTS = ("ls-files", "-v", "-z")
_BRANCH_ARGUMENTS = ("symbolic-ref", "--quiet", "--short", "HEAD")
_HEAD_ARGUMENTS = ("rev-parse", "--verify", "HEAD^{commit}")
_DIFF_ARGUMENTS = ("diff", "--check")
_CACHED_DIFF_ARGUMENTS = ("diff", "--cached", "--check")
_BATCH_ARGUMENTS = ("cat-file", "--batch")
_ALLOWED_GIT_ARGUMENTS = frozenset(
    {
        _STATUS_ARGUMENTS,
        _IGNORED_ARGUMENTS,
        _INDEX_ARGUMENTS,
        _INDEX_FLAGS_ARGUMENTS,
        _BRANCH_ARGUMENTS,
        _HEAD_ARGUMENTS,
        _DIFF_ARGUMENTS,
        _CACHED_DIFF_ARGUMENTS,
        _BATCH_ARGUMENTS,
    }
)
_INDEX_RECORD = re.compile(
    rb"(100644|100755|120000|160000) ([0-9a-f]{40}) ([0-3])\t(.+)",
    re.DOTALL,
)
_HEAD = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)


class StablePauseError(ValueError):
    """A closed observer failure that never serializes attacker text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"{code}: stable pause observation failed")


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass
class _DirectoryHandle:
    path: Path
    descriptor: int
    identity: tuple[int, ...]
    private: bool


@dataclass
class _MutexHandle:
    label: str
    path: Path
    parent: _DirectoryHandle
    descriptor: int
    identity: tuple[int, ...]


def _exact_repository_root(repository: Path | str) -> Path:
    try:
        requested = Path(repository).resolve(strict=True)
        discovered = discover_repository(requested)
    except (OSError, RepositoryError, RuntimeError, ValueError) as error:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY") from error
    if requested != discovered:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY")
    return discovered


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_uid),
        int(value.st_gid),
        int(value.st_size),
        int(value.st_mtime_ns),
        int(value.st_ctime_ns),
        int(getattr(value, "st_flags", 0)),
    )


def _directory_identity(path: Path) -> tuple[int, ...]:
    try:
        before = path.lstat()
        if (
            not stat.S_ISDIR(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or int(getattr(before, "st_flags", 0)) & _FILE_PROVIDER_DATALESS
        ):
            raise OSError
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY") from error
    try:
        opened = os.fstat(descriptor)
        after = path.lstat()
        if _identity(opened) != _identity(before) or _identity(after) != _identity(opened):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        return _identity(opened)
    finally:
        os.close(descriptor)


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_PROCESS_REAP_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=_PROCESS_REAP_SECONDS)


def _run_git(
    repository: Path,
    arguments: tuple[str, ...],
    *,
    input_data: bytes | None = None,
) -> _GitResult:
    if arguments not in _ALLOWED_GIT_ARGUMENTS:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY")
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    stdout = bytearray()
    stderr = bytearray()
    returncode: int | None = None
    try:
        process = subprocess.Popen(
            trusted_git_argv(repository, arguments),
            cwd=repository,
            env=trusted_git_environment(),
            stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            start_new_session=True,
        )
        if process.stdout is None or process.stderr is None:
            raise OSError
        selector = selectors.DefaultSelector()
        for stream, target in ((process.stdout, stdout), (process.stderr, stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, ("read", target))
        payload = memoryview(input_data or b"")
        offset = 0
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, ("write", None))
        deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(list(arguments), _GIT_TIMEOUT_SECONDS)
            for key, _ in selector.select(min(0.05, remaining)):
                operation, target = key.data
                if operation == "write":
                    try:
                        written = os.write(
                            key.fd,
                            payload[offset : offset + _STREAM_CHUNK],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        written = len(payload) - offset
                    offset += written
                    if offset >= len(payload):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                allowance = _GIT_OUTPUT_BYTES + 1 - len(stdout) - len(stderr)
                if allowance <= 0:
                    raise OverflowError
                try:
                    chunk = os.read(key.fd, min(_STREAM_CHUNK, allowance))
                except BlockingIOError:
                    continue
                if chunk:
                    target.extend(chunk)
                    if len(stdout) + len(stderr) > _GIT_OUTPUT_BYTES:
                        raise OverflowError
                else:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(list(arguments), _GIT_TIMEOUT_SECONDS)
        returncode = process.wait(timeout=remaining)
    except (
        OSError,
        OverflowError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
    finally:
        if selector is not None:
            selector.close()
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
            try:
                _kill_and_reap(process)
            except (OSError, subprocess.SubprocessError):
                pass
    return _GitResult(int(returncode), bytes(stdout), bytes(stderr))


def _decode_path(raw: bytes) -> str:
    try:
        value = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
    path = PurePosixPath(value)
    if (
        not value
        or len(encoded) > _MAX_PATH_BYTES
        or path.is_absolute()
        or ".." in path.parts
        or len(path.parts) > _MAX_PATH_DEPTH
        or path.as_posix() != value
    ):
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    return value


def _parse_status(payload: bytes) -> tuple[list[dict[str, str]], set[str], dict[str, int]]:
    if payload and not payload.endswith(b"\0"):
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    raw_records = [item for item in payload.split(b"\0") if item]
    if len(raw_records) > _MAX_STATUS_RECORDS:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    records: list[dict[str, str]] = []
    paths: set[str] = set()
    counts = {"staged": 0, "unstaged": 0, "untracked": 0}
    for raw in raw_records:
        if len(raw) < 4 or raw[2:3] != b" ":
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        try:
            code = raw[:2].decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
        if any(character not in " MADRCUT?!" for character in code):
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        path = _decode_path(raw[3:])
        records.append({"code": code, "path": path})
        paths.add(path)
        if code == "??":
            counts["untracked"] += 1
        else:
            if code[0] != " ":
                counts["staged"] += 1
            if code[1] != " ":
                counts["unstaged"] += 1
    records.sort(key=lambda item: (item["path"], item["code"]))
    return records, paths, counts


def _parse_ignored_paths(payload: bytes) -> set[str]:
    if payload and not payload.endswith(b"\0"):
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    raw_records = [item for item in payload.split(b"\0") if item]
    if len(raw_records) > _MAX_STATUS_RECORDS * 2:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    ignored: set[str] = set()
    for raw in raw_records:
        if len(raw) < 4 or raw[2:3] != b" ":
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        try:
            code = raw[:2].decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
        path_bytes = raw[3:]
        if code == "!!":
            if path_bytes.endswith(b"/"):
                path_bytes = path_bytes[:-1]
            ignored.add(_decode_path(path_bytes))
            continue
        if any(character not in " MADRCUT?" for character in code):
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        _decode_path(path_bytes)
    return ignored


def _parse_index(payload: bytes) -> list[dict[str, Any]]:
    if payload and not payload.endswith(b"\0"):
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    raw_records = [item for item in payload.split(b"\0") if item]
    if len(raw_records) > _MAX_INDEX_RECORDS:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    records: list[dict[str, Any]] = []
    for raw in raw_records:
        match = _INDEX_RECORD.fullmatch(raw)
        if match is None:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        records.append(
            {
                "mode": match.group(1).decode("ascii"),
                "oid": match.group(2).decode("ascii"),
                "stage": int(match.group(3)),
                "path": _decode_path(match.group(4)),
            }
        )
    records.sort(key=lambda item: (item["path"], item["stage"], item["oid"]))
    return records


def _parse_index_flags(payload: bytes) -> list[dict[str, str]]:
    if payload and not payload.endswith(b"\0"):
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    raw_records = [item for item in payload.split(b"\0") if item]
    if len(raw_records) > _MAX_INDEX_RECORDS:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    records: list[dict[str, str]] = []
    for raw in raw_records:
        if len(raw) < 3 or raw[1:2] != b" ":
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        try:
            tag = raw[:1].decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
        if not (tag == "?" or "A" <= tag <= "Z") or tag == "S":
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        records.append({"tag": tag, "path": _decode_path(raw[2:])})
    records.sort(key=lambda item: (item["path"], item["tag"]))
    return records


def _open_parent(root: Path, relative: str) -> tuple[int, str]:
    parts = PurePosixPath(relative).parts
    if not parts:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY") from error
    try:
        for component in parts[:-1]:
            child = os.open(component, flags, dir_fd=descriptor)
            metadata = os.fstat(child)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) & 0o022
                or int(getattr(metadata, "st_flags", 0)) & _FILE_PROVIDER_DATALESS
            ):
                os.close(child)
                raise StablePauseError("E_STABLE_PAUSE_REPOSITORY")
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except Exception:
        os.close(descriptor)
        raise


def _assert_bounded_worktree_types(root: Path, ignored_paths: set[str]) -> None:
    ignored = {PurePosixPath(value) for value in ignored_paths}
    observed = 0

    def skipped(relative: PurePosixPath) -> bool:
        return any(relative == prefix or prefix in relative.parents for prefix in ignored)

    def safe_directory(value: os.stat_result) -> bool:
        return (
            stat.S_ISDIR(value.st_mode)
            and value.st_uid == os.geteuid()
            and stat.S_IMODE(value.st_mode) & 0o022 == 0
            and not bool(
                int(getattr(value, "st_flags", 0)) & _FILE_PROVIDER_DATALESS
            )
        )

    def visit(
        descriptor: int,
        prefix: PurePosixPath,
        depth: int,
        *,
        repository_root: bool,
    ) -> None:
        nonlocal observed
        if depth > _MAX_PATH_DEPTH:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        before = os.fstat(descriptor)
        if not safe_directory(before):
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        try:
            with os.scandir(descriptor) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as error:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
        visible = []
        for entry in entries:
            if repository_root and entry.name == ".git":
                continue
            relative = prefix / entry.name
            if skipped(relative):
                continue
            visible.append((entry, relative))
        names = {entry.name for entry, _ in visible}
        if not repository_root and (
            ".git" in names
            or (
                {"HEAD", "config", "objects"}.issubset(names)
                and ("refs" in names or "packed-refs" in names)
            )
        ):
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        for entry, relative in visible:
            observed += 1
            if observed > _MAX_INDEX_RECORDS:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            try:
                encoded = relative.as_posix().encode("utf-8", errors="strict")
                metadata = os.stat(
                    entry.name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except (OSError, UnicodeEncodeError) as error:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
            if (
                not encoded
                or len(encoded) > _MAX_PATH_BYTES
                or len(relative.parts) > _MAX_PATH_DEPTH
                or metadata.st_uid != os.geteuid()
                or int(getattr(metadata, "st_flags", 0))
                & _FILE_PROVIDER_DATALESS
            ):
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                try:
                    child = os.open(
                        entry.name,
                        _directory_flags(),
                        dir_fd=descriptor,
                    )
                except OSError as error:
                    raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
                try:
                    opened = os.fstat(child)
                    if (
                        not safe_directory(opened)
                        or _identity(opened) != _identity(metadata)
                    ):
                        raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                    visit(child, relative, depth + 1, repository_root=False)
                    named = os.stat(
                        entry.name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if _identity(named) != _identity(opened):
                        raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                finally:
                    os.close(child)
            elif stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            else:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        if _identity(os.fstat(descriptor)) != _identity(before):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")

    try:
        root_descriptor = os.open(root, _directory_flags())
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY") from error
    try:
        visit(root_descriptor, PurePosixPath(), 0, repository_root=True)
        if _identity(root.lstat()) != _identity(os.fstat(root_descriptor)):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
    finally:
        os.close(root_descriptor)


def _directory_worktree_record(
    parent: int,
    name: str,
    relative: str,
    before: os.stat_result,
) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    counters = {"entries": 0, "bytes": 0}

    def safe_directory(value: os.stat_result) -> bool:
        return (
            stat.S_ISDIR(value.st_mode)
            and value.st_uid == os.geteuid()
            and stat.S_IMODE(value.st_mode) & 0o022 == 0
            and not bool(
                int(getattr(value, "st_flags", 0)) & _FILE_PROVIDER_DATALESS
            )
        )

    def visit(descriptor: int, prefix: PurePosixPath, depth: int) -> None:
        if depth > _MAX_PATH_DEPTH:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        opened_directory = os.fstat(descriptor)
        if not safe_directory(opened_directory):
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        try:
            with os.scandir(descriptor) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as error:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
        names = {entry.name for entry in entries}
        if ".git" in names or (
            {"HEAD", "config", "objects"}.issubset(names)
            and ("refs" in names or "packed-refs" in names)
        ):
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        for entry in entries:
            counters["entries"] += 1
            if counters["entries"] > _MAX_INDEX_RECORDS:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            child_relative = prefix / entry.name
            try:
                encoded = child_relative.as_posix().encode("utf-8", errors="strict")
                metadata = os.stat(
                    entry.name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except (OSError, UnicodeEncodeError) as error:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
            if (
                not encoded
                or len(encoded) > _MAX_PATH_BYTES
                or len(child_relative.parts) > _MAX_PATH_DEPTH
                or metadata.st_uid != os.geteuid()
                or int(getattr(metadata, "st_flags", 0))
                & _FILE_PROVIDER_DATALESS
            ):
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            common = {
                "path": child_relative.as_posix(),
                "mode": stat.S_IMODE(metadata.st_mode),
                "identity": list(_identity(metadata)),
            }
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                try:
                    child = os.open(
                        entry.name,
                        _directory_flags(),
                        dir_fd=descriptor,
                    )
                except OSError as error:
                    raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
                try:
                    opened = os.fstat(child)
                    if (
                        not safe_directory(opened)
                        or _identity(opened) != _identity(metadata)
                    ):
                        raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                    records.append({**common, "kind": "directory"})
                    visit(child, child_relative, depth + 1)
                    named = os.stat(
                        entry.name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if _identity(named) != _identity(opened):
                        raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                finally:
                    os.close(child)
                continue
            if stat.S_ISLNK(metadata.st_mode):
                try:
                    target = os.readlink(entry.name, dir_fd=descriptor)
                    target_bytes = target.encode("utf-8", errors="strict")
                    named = os.stat(
                        entry.name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except (OSError, UnicodeEncodeError) as error:
                    raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
                if (
                    metadata.st_nlink != 1
                    or len(target_bytes) > _MAX_PATH_BYTES
                    or _identity(named) != _identity(metadata)
                ):
                    raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                counters["bytes"] += len(target_bytes)
                records.append({**common, "kind": "symlink", "target": target})
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            if not 0 <= metadata.st_size <= _MAX_FILE_BYTES:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            try:
                file_descriptor = os.open(
                    entry.name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=descriptor,
                )
            except OSError as error:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
            try:
                opened = os.fstat(file_descriptor)
                if _identity(opened) != _identity(metadata):
                    raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                payload = bytearray()
                while len(payload) <= _MAX_FILE_BYTES:
                    chunk = os.read(
                        file_descriptor,
                        min(_STREAM_CHUNK, _MAX_FILE_BYTES + 1 - len(payload)),
                    )
                    if not chunk:
                        break
                    payload.extend(chunk)
                final = os.fstat(file_descriptor)
                named = os.stat(
                    entry.name,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
                if (
                    len(payload) != opened.st_size
                    or len(payload) > _MAX_FILE_BYTES
                    or _identity(final) != _identity(opened)
                    or _identity(named) != _identity(opened)
                ):
                    raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
            finally:
                os.close(file_descriptor)
            counters["bytes"] += len(payload)
            records.append(
                {
                    **common,
                    "kind": "regular",
                    "content_sha256": sha256(payload).hexdigest(),
                    "size": len(payload),
                }
            )
        if counters["bytes"] > _MAX_AGGREGATE_BYTES:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        if _identity(os.fstat(descriptor)) != _identity(opened_directory):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")

    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
    try:
        opened = os.fstat(descriptor)
        if not safe_directory(opened) or _identity(opened) != _identity(before):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        visit(descriptor, PurePosixPath(relative), 0)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _identity(named) != _identity(opened):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        return records, counters["bytes"]
    finally:
        os.close(descriptor)


def _worktree_record(root: Path, relative: str) -> tuple[dict[str, Any], int]:
    parent, name = _open_parent(root, relative)
    try:
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return {"path": relative, "kind": "absent"}, 0
        if before.st_uid != os.geteuid() or int(getattr(before, "st_flags", 0)) & _FILE_PROVIDER_DATALESS:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        common = {
            "path": relative,
            "mode": stat.S_IMODE(before.st_mode),
            "identity": list(_identity(before)),
        }
        if stat.S_ISLNK(before.st_mode):
            try:
                target = os.readlink(name, dir_fd=parent)
                encoded = target.encode("utf-8", errors="strict")
            except (OSError, UnicodeEncodeError) as error:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
            after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                before.st_nlink != 1
                or len(encoded) > _MAX_PATH_BYTES
                or _identity(after) != _identity(before)
            ):
                raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
            return {**common, "kind": "symlink", "target": target}, len(encoded)
        if stat.S_ISDIR(before.st_mode):
            entries, size = _directory_worktree_record(
                parent,
                name,
                relative,
                before,
            )
            return {**common, "kind": "directory", "entries": entries}, size
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        if before.st_size > _MAX_FILE_BYTES:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent,
            )
        except OSError as error:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
        try:
            opened = os.fstat(descriptor)
            if _identity(opened) != _identity(before):
                raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
            payload = bytearray()
            while len(payload) <= _MAX_FILE_BYTES:
                chunk = os.read(
                    descriptor,
                    min(_STREAM_CHUNK, _MAX_FILE_BYTES + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                len(payload) != opened.st_size
                or len(payload) > _MAX_FILE_BYTES
                or _identity(after) != _identity(opened)
                or _identity(named) != _identity(opened)
            ):
                raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        finally:
            os.close(descriptor)
        return {
            **common,
            "kind": "regular",
            "content_sha256": sha256(payload).hexdigest(),
            "size": len(payload),
        }, len(payload)
    finally:
        os.close(parent)


def _blob_records(
    repository: Path,
    oids: Sequence[str],
) -> tuple[list[dict[str, Any]], int]:
    if not oids:
        return [], 0
    result = _run_git(
        repository,
        _BATCH_ARGUMENTS,
        input_data=("\n".join(oids) + "\n").encode("ascii"),
    )
    if result.returncode != 0:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    cursor = 0
    total = 0
    records: list[dict[str, Any]] = []
    for oid in oids:
        line_end = result.stdout.find(b"\n", cursor)
        if line_end < 0:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        header = result.stdout[cursor:line_end]
        match = re.fullmatch(rb"([0-9a-f]{40}) blob ([0-9]+)", header)
        if match is None or match.group(1).decode("ascii") != oid:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        size = int(match.group(2))
        payload_start = line_end + 1
        payload_end = payload_start + size
        if (
            size > _MAX_FILE_BYTES
            or payload_end >= len(result.stdout)
            or result.stdout[payload_end : payload_end + 1] != b"\n"
        ):
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        payload = result.stdout[payload_start:payload_end]
        total += size
        if total > _MAX_AGGREGATE_BYTES:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        records.append(
            {
                "oid": oid,
                "size": size,
                "content_sha256": sha256(payload).hexdigest(),
            }
        )
        cursor = payload_end + 1
    if cursor != len(result.stdout):
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    return records, total


def _domain_digest(domain: str, value: Any) -> str:
    payload = domain.encode("ascii") + b"\0" + canonical_json(value).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def observe_repository_snapshot(repository: Path | str) -> dict[str, Any]:
    """Capture one deterministic, byte-bound, read-only repository snapshot."""

    try:
        root = _exact_repository_root(repository)
        worktree_dir = worktree_git_dir(root)
        common_dir = git_common_dir(root)
    except (OSError, RepositoryError, ValueError) as error:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY") from error
    root_identity = _directory_identity(root)
    worktree_dir_identity = _directory_identity(worktree_dir)
    common_dir_identity = _directory_identity(common_dir)
    try:
        assert_no_external_git_filters(root)
    except ValueError as error:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY") from error

    branch_result = _run_git(root, _BRANCH_ARGUMENTS)
    head_result = _run_git(root, _HEAD_ARGUMENTS)
    status_result = _run_git(root, _STATUS_ARGUMENTS)
    ignored_result = _run_git(root, _IGNORED_ARGUMENTS)
    index_result = _run_git(root, _INDEX_ARGUMENTS)
    index_flags_result = _run_git(root, _INDEX_FLAGS_ARGUMENTS)
    if any(
        result.returncode != 0
        for result in (
            branch_result,
            head_result,
            status_result,
            ignored_result,
            index_result,
            index_flags_result,
        )
    ):
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY")
    try:
        branch = branch_result.stdout.decode("utf-8", errors="strict").strip()
        head = head_result.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY") from error
    if (
        _BRANCH.fullmatch(branch) is None
        or ".." in branch
        or branch.endswith("/")
        or _HEAD.fullmatch(head) is None
    ):
        raise StablePauseError("E_STABLE_PAUSE_REPOSITORY")

    status_records, status_paths, counts = _parse_status(status_result.stdout)
    ignored_paths = _parse_ignored_paths(ignored_result.stdout)
    index_records = _parse_index(index_result.stdout)
    index_flags = _parse_index_flags(index_flags_result.stdout)
    if any(record["mode"] == "160000" for record in index_records):
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
    observed_paths = set(status_paths)
    observed_paths.update(str(record["path"]) for record in index_records)
    _assert_bounded_worktree_types(root, ignored_paths)
    path_records: list[dict[str, Any]] = []
    blob_records: list[dict[str, Any]] = []
    total_bytes = 0
    for relative in sorted(observed_paths):
        record, size = _worktree_record(root, relative)
        total_bytes += size
        if total_bytes > _MAX_AGGREGATE_BYTES:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        path_records.append(record)
    relevant_oids = sorted(
        {
            str(record["oid"])
            for record in index_records
            if record["path"] in status_paths
        }
    )
    blob_records, blob_bytes = _blob_records(root, relevant_oids)
    total_bytes += blob_bytes
    if total_bytes > _MAX_AGGREGATE_BYTES:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS")

    diff = _run_git(root, _DIFF_ARGUMENTS)
    cached_diff = _run_git(root, _CACHED_DIFF_ARGUMENTS)
    if diff.returncode == 0 and cached_diff.returncode == 0:
        diff_check = "PASS"
    elif all(
        result.returncode in {0, 1, 2} for result in (diff, cached_diff)
    ) and any(result.stdout or result.stderr for result in (diff, cached_diff)):
        diff_check = "FAIL"
    else:
        diff_check = "UNKNOWN"

    status_input = {"records": status_records}
    status_digest = _domain_digest(
        "control-plane-stable-pause-status-v1",
        status_input,
    )
    worktree_input = {
        "root_identity": list(root_identity),
        "worktree_git_dir_identity": list(worktree_dir_identity),
        "common_git_dir_identity": list(common_dir_identity),
        "branch": branch,
        "head": head,
        "status": status_input,
        "ignored": sorted(ignored_paths),
        "index": index_records,
        "index_flags": index_flags,
        "paths": path_records,
        "blobs": blob_records,
    }
    worktree_digest = _domain_digest(
        "control-plane-stable-pause-worktree-v1",
        worktree_input,
    )

    if (
        _directory_identity(root) != root_identity
        or _directory_identity(worktree_dir) != worktree_dir_identity
        or _directory_identity(common_dir) != common_dir_identity
    ):
        raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
    return {
        "root": str(root),
        "common_git_dir": str(common_dir),
        "branch": branch,
        "head": head,
        "status_digest": status_digest,
        "worktree_digest": worktree_digest,
        "staged_count": counts["staged"],
        "unstaged_count": counts["unstaged"],
        "untracked_count": counts["untracked"],
        "diff_check": diff_check,
    }


def _safe_directory(value: os.stat_result, *, private: bool) -> bool:
    mode = stat.S_IMODE(value.st_mode)
    return (
        stat.S_ISDIR(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink >= 1
        and (mode == 0o700 if private else mode & 0o022 == 0)
        and not bool(int(getattr(value, "st_flags", 0)) & _FILE_PROVIDER_DATALESS)
    )


def _safe_mutex(value: os.stat_result) -> bool:
    return (
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink == 1
        and stat.S_IMODE(value.st_mode) == 0o600
        and value.st_size == 0
        and not bool(int(getattr(value, "st_flags", 0)) & _FILE_PROVIDER_DATALESS)
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_directory(path: Path, *, private: bool) -> _DirectoryHandle:
    try:
        before = path.lstat()
        descriptor = os.open(path, _directory_flags())
        opened = os.fstat(descriptor)
        after = path.lstat()
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE") from error
    if (
        not _safe_directory(before, private=private)
        or not _safe_directory(opened, private=private)
        or _identity(before) != _identity(opened)
        or _identity(after) != _identity(opened)
    ):
        os.close(descriptor)
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE")
    return _DirectoryHandle(path, descriptor, _identity(opened), private)


def _open_child_directory(
    parent: _DirectoryHandle,
    name: str,
    *,
    private: bool,
) -> _DirectoryHandle:
    path = parent.path / name
    try:
        before = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        descriptor = os.open(name, _directory_flags(), dir_fd=parent.descriptor)
        opened = os.fstat(descriptor)
        after = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE") from error
    if (
        not _safe_directory(before, private=private)
        or not _safe_directory(opened, private=private)
        or _identity(before) != _identity(opened)
        or _identity(after) != _identity(opened)
    ):
        os.close(descriptor)
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE")
    return _DirectoryHandle(path, descriptor, _identity(opened), private)


def _revalidate_directory(handle: _DirectoryHandle) -> bool:
    try:
        opened = os.fstat(handle.descriptor)
        named = handle.path.lstat()
    except OSError:
        return False
    return (
        _safe_directory(opened, private=handle.private)
        and _safe_directory(named, private=handle.private)
        and _identity(opened) == handle.identity
        and _identity(named) == handle.identity
    )


def _acquire_existing_mutex(
    parent: _DirectoryHandle,
    name: str,
    label: str,
) -> tuple[str, _MutexHandle | None]:
    path = parent.path / name
    try:
        before = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return "absent", None
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE") from error
    try:
        descriptor = os.open(
            name,
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent.descriptor,
        )
        opened = os.fstat(descriptor)
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE") from error
    if not _safe_mutex(before) or not _safe_mutex(opened) or _identity(before) != _identity(opened):
        os.close(descriptor)
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return "held", None
    except OSError as error:
        os.close(descriptor)
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE") from error
    try:
        named = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
    except OSError as error:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE") from error
    if (
        not _revalidate_directory(parent)
        or not _safe_mutex(named)
        or _identity(named) != _identity(opened)
    ):
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        raise StablePauseError("E_STABLE_PAUSE_OPERATION_ACTIVE")
    return "free", _MutexHandle(label, path, parent, descriptor, _identity(opened))


def _close_lock_graph(
    mutexes: list[_MutexHandle],
    directories: list[_DirectoryHandle],
) -> bool:
    stable = True
    for mutex in reversed(mutexes):
        try:
            opened = os.fstat(mutex.descriptor)
            named = os.stat(
                mutex.path.name,
                dir_fd=mutex.parent.descriptor,
                follow_symlinks=False,
            )
            if (
                not _revalidate_directory(mutex.parent)
                or not _safe_mutex(opened)
                or not _safe_mutex(named)
                or _identity(opened) != mutex.identity
                or _identity(named) != mutex.identity
            ):
                stable = False
        except OSError:
            stable = False
        try:
            fcntl.flock(mutex.descriptor, fcntl.LOCK_UN)
        except OSError:
            stable = False
        os.close(mutex.descriptor)
    for directory in reversed(directories):
        if not _revalidate_directory(directory):
            stable = False
        os.close(directory.descriptor)
    return stable


def _strict_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _bounded_json(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    items = 0
    while stack:
        item, depth = stack.pop()
        items += 1
        if items > _MAX_STATE_ENTRIES or depth > 32:
            raise ValueError("bounded JSON exceeded")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str) or len(key.encode("utf-8")) > 8192:
                    raise ValueError("invalid JSON key")
                if key == "authorizes" and child is not False:
                    raise ValueError("state cannot authorize")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item.encode("utf-8")) > 8192:
                raise ValueError("oversized JSON string")
        elif item is None or isinstance(item, bool) or type(item) is int:
            continue
        else:
            raise ValueError("unsupported JSON value")


def _decode_state_json(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_pairs,
            parse_constant=_reject_json_constant,
        )
        _bounded_json(value)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
    if not isinstance(value, dict):
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE")
    return value


def _read_safe_regular_at(
    parent: _DirectoryHandle,
    name: str,
    *,
    maximum: int,
) -> tuple[bytes, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent.descriptor,
        )
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) & 0o022
            or not 0 <= opened.st_size <= maximum
            or int(getattr(opened, "st_flags", 0)) & _FILE_PROVIDER_DATALESS
            or _identity(opened) != _identity(before)
        ):
            raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                descriptor,
                min(_STREAM_CHUNK, maximum + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        final = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        if (
            len(payload) != opened.st_size
            or len(payload) > maximum
            or _identity(final) != _identity(opened)
            or _identity(named) != _identity(opened)
            or not _revalidate_directory(parent)
        ):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        return bytes(payload), opened
    finally:
        os.close(descriptor)


def _read_relative_regular(
    anchor: Path,
    parents: Sequence[tuple[str, bool]],
    filename: str,
    *,
    maximum: int,
    missing_ok: bool = False,
) -> bytes | None:
    directories: list[_DirectoryHandle] = []
    try:
        try:
            current = _open_directory(anchor, private=False)
        except StablePauseError as error:
            raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
        directories.append(current)
        for name, private in parents:
            try:
                os.stat(name, dir_fd=current.descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if missing_ok:
                    return None
                raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from None
            except OSError as error:
                raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
            try:
                current = _open_child_directory(current, name, private=private)
            except StablePauseError as error:
                raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
            directories.append(current)
        try:
            os.stat(filename, dir_fd=current.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from None
        except OSError as error:
            raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
        payload, _ = _read_safe_regular_at(
            current,
            filename,
            maximum=maximum,
        )
        return payload
    finally:
        if directories and not _close_lock_graph([], directories):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")


def _state_tree_surface(path: Path, label: str) -> list[tuple[Any, ...]]:
    try:
        path.lstat()
    except FileNotFoundError:
        return [(label, ".", "absent")]
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
    try:
        root_handle = _open_directory(path, private=True)
    except StablePauseError as error:
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
    root_metadata = os.fstat(root_handle.descriptor)
    records: list[tuple[Any, ...]] = []
    counters = {"entries": 0, "bytes": 0}

    def visit(
        directory: _DirectoryHandle,
        relative: PurePosixPath,
        depth: int,
    ) -> None:
        if depth > 16:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        before = os.fstat(directory.descriptor)
        try:
            with os.scandir(directory.descriptor) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
        for entry in entries:
            counters["entries"] += 1
            if counters["entries"] > _MAX_STATE_ENTRIES:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            try:
                encoded = entry.name.encode("utf-8", errors="strict")
                metadata = os.stat(
                    entry.name,
                    dir_fd=directory.descriptor,
                    follow_symlinks=False,
                )
            except (OSError, UnicodeEncodeError) as error:
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
            child_relative = relative / entry.name
            if (
                not encoded
                or len(encoded) > _MAX_PATH_BYTES
                or len(child_relative.parts) > 16
                or metadata.st_uid != os.geteuid()
                or int(getattr(metadata, "st_flags", 0)) & _FILE_PROVIDER_DATALESS
            ):
                raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
            common = (label, child_relative.as_posix(), list(_identity(metadata)))
            if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                try:
                    child = _open_child_directory(
                        directory,
                        entry.name,
                        private=True,
                    )
                except StablePauseError as error:
                    raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
                try:
                    opened = os.fstat(child.descriptor)
                    records.append(
                        (
                            label,
                            child_relative.as_posix(),
                            list(_identity(opened)),
                            "directory",
                        )
                    )
                    visit(child, child_relative, depth + 1)
                    if not _revalidate_directory(child):
                        raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                finally:
                    os.close(child.descriptor)
            elif stat.S_ISREG(metadata.st_mode):
                payload, opened = _read_safe_regular_at(
                    directory,
                    entry.name,
                    maximum=_MAX_STATE_FILE_BYTES,
                )
                counters["bytes"] += len(payload)
                if counters["bytes"] > _MAX_STATE_TOTAL_BYTES:
                    raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
                records.append(
                    (
                        label,
                        child_relative.as_posix(),
                        list(_identity(opened)),
                        "regular",
                        sha256(payload).hexdigest(),
                    )
                )
            elif stat.S_ISLNK(metadata.st_mode):
                try:
                    target = os.readlink(entry.name, dir_fd=directory.descriptor)
                    target_bytes = target.encode("utf-8", errors="strict")
                    named = os.stat(
                        entry.name,
                        dir_fd=directory.descriptor,
                        follow_symlinks=False,
                    )
                except (OSError, UnicodeEncodeError) as error:
                    raise StablePauseError("E_STABLE_PAUSE_BOUNDS") from error
                if (
                    metadata.st_nlink != 1
                    or len(target_bytes) > _MAX_PATH_BYTES
                    or _identity(named) != _identity(metadata)
                    or not _revalidate_directory(directory)
                ):
                    raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
                records.append((*common, "symlink", target))
            else:
                records.append((*common, "other"))
        if (
            _identity(os.fstat(directory.descriptor)) != _identity(before)
            or not _revalidate_directory(directory)
        ):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")

    try:
        records.append((label, ".", list(_identity(root_metadata)), "directory"))
        visit(root_handle, PurePosixPath(), 0)
        if not _revalidate_directory(root_handle):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        return records
    finally:
        os.close(root_handle.descriptor)


def _control_surface_snapshot(
    root: Path,
    worktree_dir: Path,
    common_dir: Path,
) -> tuple[tuple[Any, ...], ...]:
    activation = _read_relative_regular(
        root,
        ((".codex", False),),
        "control-plane.lock",
        maximum=65_536,
    )
    assert activation is not None
    records: list[tuple[Any, ...]] = [
        ("activation", sha256(activation).hexdigest())
    ]
    state_paths = [(worktree_dir / "codex-control-plane-core", "worktree")]
    common_state = common_dir / "codex-control-plane-core"
    if common_state != state_paths[0][0]:
        state_paths.append((common_state, "common"))
    for state_path, label in state_paths:
        records.extend(_state_tree_surface(state_path, label))
    return tuple(sorted(records, key=repr))


def _activation_binding(root: Path) -> tuple[str | None, str]:
    payload = _read_relative_regular(
        root,
        ((".codex", False),),
        "control-plane.lock",
        maximum=65_536,
    )
    assert payload is not None
    try:
        value = tomllib.loads(payload.decode("utf-8", errors="strict"))
        digests = value.get("digests")
        runtime = digests.get("runtime") if isinstance(digests, Mapping) else None
        marker = value.get("adoption_lifecycle")
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
    if (
        not isinstance(runtime, str)
        or SHA256_DIGEST.fullmatch(runtime) is None
        or marker not in {None, _ADOPTION_LIFECYCLE}
    ):
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE")
    return marker, runtime


def _journal_payload(common_dir: Path) -> bytes | None:
    return _read_relative_regular(
        common_dir,
        (
            ("codex-control-plane-core", True),
            ("adoption", True),
        ),
        "journal.json",
        maximum=_MAX_STATE_FILE_BYTES,
        missing_ok=True,
    )


def _journal_bound_required(root: Path, common_dir: Path) -> bool:
    marker, _ = _activation_binding(root)
    return marker is not None or _journal_payload(common_dir) is not None


def _acquire_lock_graph(
    root: Path,
    worktree_dir: Path,
    common_dir: Path,
    task_id: str,
    *,
    verification_required: bool,
) -> tuple[
    dict[str, str],
    list[_MutexHandle],
    list[_DirectoryHandle],
    dict[str, _DirectoryHandle],
    bool,
]:
    directories: list[_DirectoryHandle] = []
    mutexes: list[_MutexHandle] = []
    statuses = {
        "adoption_mutex": "unknown",
        "verification_mutex": "unknown",
        "task_mutex": "unknown",
        "lease_mutex": "unknown",
    }
    named: dict[str, _DirectoryHandle] = {}
    try:
        named["root"] = _open_directory(root, private=False)
        directories.append(named["root"])
        named["common"] = _open_directory(common_dir, private=False)
        directories.append(named["common"])
        named["common_state"] = _open_child_directory(
            named["common"], "codex-control-plane-core", private=True
        )
        directories.append(named["common_state"])
        named["common_locks"] = _open_child_directory(
            named["common_state"], "locks", private=True
        )
        directories.append(named["common_locks"])
        named["worktree"] = _open_directory(worktree_dir, private=False)
        directories.append(named["worktree"])
        named["worktree_state"] = _open_child_directory(
            named["worktree"], "codex-control-plane-core", private=True
        )
        directories.append(named["worktree_state"])
        named["worktree_locks"] = _open_child_directory(
            named["worktree_state"], "locks", private=True
        )
        directories.append(named["worktree_locks"])
        named["task_locks"] = _open_child_directory(
            named["worktree_locks"], "tasks", private=True
        )
        directories.append(named["task_locks"])

        lock_specs = (
            ("adoption_mutex", named["common_state"], "adoption.lock", True),
            (
                "verification_mutex",
                named["common_locks"],
                "verification.lock",
                verification_required,
            ),
            (
                "task_mutex",
                named["task_locks"],
                f"{sha256(task_id.encode('utf-8')).hexdigest()}.lock",
                True,
            ),
            ("lease_mutex", named["common_locks"], "leases.lock", True),
        )
        for label, parent, filename, required in lock_specs:
            status, handle = _acquire_existing_mutex(parent, filename, label)
            statuses[label] = status
            if handle is not None:
                mutexes.append(handle)
            if status == "held" or (status == "absent" and required):
                return statuses, mutexes, directories, named, False
        return statuses, mutexes, directories, named, True
    except Exception:
        _close_lock_graph(mutexes, directories)
        raise


def _directory_journal_record(
    handle: _DirectoryHandle,
    relative: str,
) -> dict[str, object]:
    value = os.fstat(handle.descriptor)
    return {
        "path": relative,
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "uid": int(value.st_uid),
        "gid": int(value.st_gid),
        "flags": int(getattr(value, "st_flags", 0)),
    }


def _lock_journal_record(handle: _MutexHandle, relative: str) -> dict[str, object]:
    value = os.fstat(handle.descriptor)
    return {
        "path": relative,
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "links": int(value.st_nlink),
        "uid": int(value.st_uid),
        "gid": int(value.st_gid),
        "size": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "ctime_ns": int(value.st_ctime_ns),
        "flags": int(getattr(value, "st_flags", 0)),
    }


def _json_records(directory: Path) -> list[tuple[Path, dict[str, Any]]]:
    try:
        directory.lstat()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
    try:
        handle = _open_directory(directory, private=True)
    except StablePauseError as error:
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
    try:
        try:
            with os.scandir(handle.descriptor) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE") from error
        if len(entries) > _MAX_STATE_ENTRIES:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        records: list[tuple[Path, dict[str, Any]]] = []
        for entry in entries:
            if not _JSON_RECORD_NAME.fullmatch(entry.name):
                continue
            path = directory / entry.name
            payload, _ = _read_safe_regular_at(
                handle,
                entry.name,
                maximum=_MAX_STATE_FILE_BYTES,
            )
            records.append((path, _decode_state_json(payload)))
        if not _revalidate_directory(handle):
            raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        return records
    finally:
        os.close(handle.descriptor)


def _evaluate_lifecycle(
    root: Path,
    worktree_dir: Path,
    common_dir: Path,
    task_id: str,
    mutexes: list[_MutexHandle],
    directories: Mapping[str, _DirectoryHandle],
    repository_binding: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str, bool]:
    from control_plane.leases import LeaseStore
    from control_plane.task_state import CoreTaskStore

    unknown = {
        "task_id": task_id,
        "task_state": "unknown",
        "task_state_digest": None,
        "lease_state": "unknown",
        "lease_digest": None,
        "owner_runtime_digest": None,
    }
    try:
        task_payload = _read_relative_regular(
            worktree_dir,
            (
                ("codex-control-plane-core", True),
                ("tasks", True),
            ),
            f"{task_id}.json",
            maximum=_MAX_STATE_FILE_BYTES,
        )
        assert task_payload is not None
        task = _decode_state_json(task_payload)
        CoreTaskStore._validate(task, expected_task_id=task_id)
        lease_records = _json_records(
            common_dir / "codex-control-plane-core" / "leases"
        )
        receipt_records = _json_records(
            common_dir
            / "codex-control-plane-core"
            / "lease-release-receipts"
        )
        leases = [value for _, value in lease_records]
        receipts = [value for _, value in receipt_records]
        for path, lease in lease_records:
            LeaseStore._validate_lease(lease)
            if path.stem != lease.get("lease_id"):
                raise ValueError("lease filename binding differs")
        for path, receipt in receipt_records:
            LeaseStore._validate_receipt(receipt)
            if path.stem != receipt.get("lease_id"):
                raise ValueError("lease receipt filename binding differs")
        marker, runtime = _activation_binding(root)
        journal_payload = _journal_payload(common_dir)
        journal = (
            load_active_adoption_journal(journal_payload)
            if journal_payload is not None
            else None
        )
    except (StablePauseError, ValueError, RecursionError):
        return unknown, "contradiction", False

    lifecycle: dict[str, Any] = {
        "task_id": task_id,
        "task_state": task["state"],
        "task_state_digest": task["state_digest"],
        "lease_state": "absent",
        "lease_digest": None,
        "owner_runtime_digest": task["owner_runtime_digest"],
    }
    coherent = (
        task.get("repository") == str(root)
        and task.get("worktree") == str(root)
        and task.get("owner_runtime_digest") == runtime
        and (
            repository_binding is None
            or (
                task.get("branch") == repository_binding.get("branch")
                and task.get("head") == repository_binding.get("head")
            )
        )
    )
    matching = [lease for lease in leases if lease.get("task_id") == task_id]
    other = [lease for lease in leases if lease.get("task_id") != task_id]
    terminal = task.get("state") == "closed" and task.get("resume_state") is None
    if terminal:
        coherent = coherent and not matching and not other
        generation = task.get("lease_generation")
        if type(generation) is int and generation > 0:
            exact_receipts = [
                receipt
                for receipt in receipts
                if receipt.get("task_id") == task_id
                and receipt.get("revision_id") == task.get("revision_id")
                and receipt.get("lease_generation") == generation
            ]
            coherent = coherent and len(exact_receipts) == 1
        lifecycle_class = "terminal" if coherent else "contradiction"
    else:
        if len(matching) == 1:
            lease = matching[0]
            lifecycle["lease_state"] = "active"
            lifecycle["lease_digest"] = lease["lease_digest"]
            expected = {
                "task_id": task_id,
                "revision_id": task.get("revision_id"),
                "lease_generation": task.get("lease_generation"),
                "worktree": task.get("worktree"),
                "branch": task.get("branch"),
                "owner_runtime_digest": task.get("owner_runtime_digest"),
            }
            coherent = coherent and all(
                lease.get(key) == value for key, value in expected.items()
            )
        else:
            lifecycle["lease_state"] = "unknown" if len(matching) > 1 else "absent"
            coherent = False
        coherent = coherent and not other
        lifecycle_class = "active" if coherent else "contradiction"

    receipt_ids = {receipt.get("lease_id") for receipt in receipts}
    if any(lease.get("lease_id") in receipt_ids for lease in leases):
        coherent = False
        lifecycle_class = "contradiction"

    common_state = common_dir / "codex-control-plane-core"
    try:
        adoption_metadata = (common_state / "adoption").lstat()
    except FileNotFoundError:
        adoption_exists = False
    except OSError:
        adoption_exists = True
        coherent = False
        lifecycle_class = "contradiction"
    else:
        adoption_exists = True
        if not _safe_directory(adoption_metadata, private=True):
            coherent = False
            lifecycle_class = "contradiction"
    if marker is None and journal is None and not adoption_exists:
        pass
    elif marker != _ADOPTION_LIFECYCLE or journal is None:
        coherent = False
        lifecycle_class = "contradiction"
    else:
        by_label = {handle.label: handle for handle in mutexes}
        adoption = by_label.get("adoption_mutex")
        verification = by_label.get("verification_mutex")
        common_locks = directories.get("common_locks")
        if adoption is None or verification is None or common_locks is None:
            coherent = False
            lifecycle_class = "contradiction"
        else:
            expected_lifecycle = _lock_journal_record(
                adoption,
                "codex-control-plane-core/adoption.lock",
            )
            expected_verification = {
                "directory": _directory_journal_record(
                    common_locks,
                    "codex-control-plane-core/locks",
                ),
                "file": _lock_journal_record(
                    verification,
                    "codex-control-plane-core/locks/verification.lock",
                ),
            }
            if (
                journal.get("state") != "active"
                or journal.get("lifecycle_lock") != expected_lifecycle
                or journal.get("verification_lock") != expected_verification
            ):
                coherent = False
                lifecycle_class = "contradiction"
    return lifecycle, lifecycle_class, coherent


def _state_entries(
    root: Path,
) -> list[tuple[str, str, os.stat_result, bytes | None]]:
    try:
        root.lstat()
    except FileNotFoundError:
        return []
    records: list[tuple[str, str, os.stat_result, bytes | None]] = []

    def visit(directory: Path, prefix: PurePosixPath, depth: int) -> None:
        if depth > 16 or len(records) > _MAX_STATE_ENTRIES:
            raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
        try:
            handle = _open_directory(directory, private=True)
        except StablePauseError as error:
            raise StablePauseError("E_STABLE_PAUSE_RESIDUE") from error
        try:
            try:
                with os.scandir(handle.descriptor) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except OSError as error:
                raise StablePauseError("E_STABLE_PAUSE_RESIDUE") from error
            for entry in entries:
                relative = prefix / entry.name
                try:
                    encoded = entry.name.encode("utf-8", errors="strict")
                    metadata = os.stat(
                        entry.name,
                        dir_fd=handle.descriptor,
                        follow_symlinks=False,
                    )
                except (OSError, UnicodeEncodeError) as error:
                    raise StablePauseError("E_STABLE_PAUSE_RESIDUE") from error
                if (
                    not encoded
                    or len(encoded) > _MAX_PATH_BYTES
                    or len(relative.parts) > 16
                    or metadata.st_uid != os.geteuid()
                    or int(getattr(metadata, "st_flags", 0))
                    & _FILE_PROVIDER_DATALESS
                ):
                    raise StablePauseError("E_STABLE_PAUSE_RESIDUE")
                if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                    kind = (
                        "directory"
                        if _safe_directory(metadata, private=True)
                        else "unsafe-directory"
                    )
                elif stat.S_ISREG(metadata.st_mode):
                    try:
                        payload, opened = _read_safe_regular_at(
                            handle,
                            entry.name,
                            maximum=_MAX_STATE_FILE_BYTES,
                        )
                    except StablePauseError:
                        kind = "invalid-regular"
                        payload = None
                    else:
                        kind = "regular"
                        metadata = opened
                elif stat.S_ISLNK(metadata.st_mode):
                    kind = "symlink"
                    payload = None
                else:
                    kind = "other"
                    payload = None
                if kind in {"directory", "unsafe-directory"}:
                    payload = None
                records.append((relative.as_posix(), kind, metadata, payload))
                if len(records) > _MAX_STATE_ENTRIES:
                    raise StablePauseError("E_STABLE_PAUSE_BOUNDS")
                if kind == "directory":
                    visit(directory / entry.name, relative, depth + 1)
            if not _revalidate_directory(handle):
                raise StablePauseError("E_STABLE_PAUSE_SNAPSHOT_DRIFT")
        finally:
            os.close(handle.descriptor)

    visit(root, PurePosixPath(), 0)
    return records


def _owned_residue(
    worktree_dir: Path,
    common_dir: Path,
) -> tuple[list[str], str]:
    from control_plane.leases import LeaseStore
    from control_plane.task_state import CoreTaskStore

    worktree_state = worktree_dir / "codex-control-plane-core"
    common_state = common_dir / "codex-control-plane-core"
    roots = [(worktree_state, "shared" if worktree_state == common_state else "worktree")]
    if common_state != worktree_state:
        roots.append((common_state, "common"))
    allowed_directories = {
        "worktree": {"tasks", "locks", "locks/tasks"},
        "common": {"locks", "leases", "lease-release-receipts", "adoption"},
        "shared": {
            "tasks",
            "locks",
            "locks/tasks",
            "leases",
            "lease-release-receipts",
            "adoption",
        },
    }
    classes: list[str] = []
    for state_root, root_role in roots:
        for relative, kind, metadata, payload in _state_entries(state_root):
            relative_path = PurePosixPath(relative)
            lowered = relative.lower()
            if any(
                marker in lowered
                for marker in (
                    ".provisioning-",
                    ".tmp",
                    ".pending",
                    "staging",
                    "recovery",
                    "quarantine",
                    "rolling_back",
                    "rollback-",
                )
            ):
                classes.append("transient-control-plane-state")
                continue
            if kind == "directory" and relative in allowed_directories[root_role]:
                continue
            if kind == "invalid-regular":
                classes.append("invalid-durable-record")
                continue
            try:
                if (
                    root_role in {"common", "shared"}
                    and relative == "adoption.lock"
                    and kind == "regular"
                ):
                    if not _safe_mutex(metadata):
                        raise ValueError
                    continue
                if (
                    root_role in {"common", "shared"}
                    and relative in {"locks/verification.lock", "locks/leases.lock"}
                    and kind == "regular"
                ):
                    if not _safe_mutex(metadata):
                        raise ValueError
                    continue
                if (
                    root_role in {"worktree", "shared"}
                    and relative.startswith("locks/tasks/")
                    and kind == "regular"
                ):
                    if (
                        _TASK_LOCK_NAME.fullmatch(relative_path.name) is None
                        or not _safe_mutex(metadata)
                    ):
                        raise ValueError
                    continue
                if (
                    root_role in {"worktree", "shared"}
                    and relative.startswith("tasks/")
                    and kind == "regular"
                    and relative_path.suffix == ".json"
                ):
                    value = _decode_state_json(payload or b"")
                    CoreTaskStore._validate(
                        value,
                        expected_task_id=relative_path.stem,
                    )
                    continue
                if (
                    root_role in {"common", "shared"}
                    and relative.startswith("leases/")
                    and kind == "regular"
                    and relative_path.suffix == ".json"
                ):
                    value = _decode_state_json(payload or b"")
                    LeaseStore._validate_lease(value)
                    if relative_path.stem != value.get("lease_id"):
                        raise ValueError
                    continue
                if (
                    root_role in {"common", "shared"}
                    and relative.startswith("lease-release-receipts/")
                    and kind == "regular"
                    and relative_path.suffix == ".json"
                ):
                    value = _decode_state_json(payload or b"")
                    LeaseStore._validate_receipt(value)
                    if relative_path.stem != value.get("lease_id"):
                        raise ValueError
                    continue
                if (
                    root_role in {"common", "shared"}
                    and relative == "adoption/journal.json"
                    and kind == "regular"
                ):
                    load_active_adoption_journal(payload or b"")
                    continue
            except (OSError, StablePauseError, ValueError, RecursionError):
                classes.append("invalid-durable-record")
                continue
            classes.append("unknown-protected-entry")
    classes.sort()
    return classes, _domain_digest(
        "control-plane-stable-pause-residue-v1",
        {"classifications": classes},
    )


def _state_result(
    *,
    task_id: str,
    lifecycle: dict[str, Any] | None,
    lifecycle_class: str,
    statuses: Mapping[str, str],
    lifecycle_check: str,
    mutex_check: str,
    residue_check: str,
    residue_count: int,
    residue_digest: str | None,
    snapshot_check: str,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if lifecycle_check == "FAIL":
        issues.append({"code": "E_STABLE_PAUSE_LIFECYCLE", "dimension": "lifecycle"})
    if mutex_check == "FAIL":
        issues.append(
            {"code": "E_STABLE_PAUSE_OPERATION_ACTIVE", "dimension": "operation"}
        )
    if residue_check == "FAIL":
        issues.append({"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "residue"})
    if snapshot_check == "FAIL":
        issues.append(
            {"code": "E_STABLE_PAUSE_SNAPSHOT_DRIFT", "dimension": "snapshot"}
        )
    if any(value == "UNKNOWN" for value in (lifecycle_check, mutex_check, residue_check, snapshot_check)):
        issues.append({"code": "E_STABLE_PAUSE_BOUNDS", "dimension": "bounds"})
    normalized = sorted(
        {(str(item["code"]), str(item["dimension"])) for item in issues}
    )
    closed_issues = [
        {"code": code, "dimension": dimension} for code, dimension in normalized
    ]
    return {
        "lifecycle": lifecycle
        or {
            "task_id": task_id,
            "task_state": "unknown",
            "task_state_digest": None,
            "lease_state": "unknown",
            "lease_digest": None,
            "owner_runtime_digest": None,
        },
        "control_plane_state": {
            **dict(statuses),
            "residue_count": residue_count,
            "residue_digest": residue_digest,
        },
        "checks": {
            "lifecycle_binding": lifecycle_check,
            "mutex_quiescence": mutex_check,
            "owned_residue": residue_check,
            "snapshot_stability": snapshot_check,
        },
        "issues": closed_issues,
        "lifecycle_class": lifecycle_class,
    }


def observe_control_plane_snapshot(
    repository: Path | str,
    task_id: str,
    *,
    _repository_before: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe exact Core lifecycle state under the existing lock graph only."""

    if not validate_task_id(task_id):
        raise StablePauseError("E_STABLE_PAUSE_LIFECYCLE")
    try:
        root = _exact_repository_root(repository)
        worktree_dir = worktree_git_dir(root)
        common_dir = git_common_dir(root)
        before = _control_surface_snapshot(root, worktree_dir, common_dir)
        verification_required = _journal_bound_required(root, common_dir)
    except StablePauseError as error:
        lifecycle_check = (
            "FAIL" if error.code == "E_STABLE_PAUSE_LIFECYCLE" else "UNKNOWN"
        )
        mutex_check = (
            "FAIL" if error.code == "E_STABLE_PAUSE_OPERATION_ACTIVE" else "UNKNOWN"
        )
        residue_check = (
            "FAIL" if error.code == "E_STABLE_PAUSE_RESIDUE" else "UNKNOWN"
        )
        snapshot_check = (
            "FAIL" if error.code == "E_STABLE_PAUSE_SNAPSHOT_DRIFT" else "UNKNOWN"
        )
        definite = "FAIL" in {
            lifecycle_check,
            mutex_check,
            residue_check,
            snapshot_check,
        }
        return _state_result(
            task_id=task_id,
            lifecycle=None,
            lifecycle_class="contradiction" if definite else "unknown",
            statuses={
                "adoption_mutex": "unknown",
                "verification_mutex": "unknown",
                "task_mutex": "unknown",
                "lease_mutex": "unknown",
            },
            lifecycle_check=lifecycle_check,
            mutex_check=mutex_check,
            residue_check=residue_check,
            residue_count=0,
            residue_digest=None,
            snapshot_check=snapshot_check,
        )
    except (OSError, RepositoryError, ValueError):
        return _state_result(
            task_id=task_id,
            lifecycle=None,
            lifecycle_class="unknown",
            statuses={
                "adoption_mutex": "unknown",
                "verification_mutex": "unknown",
                "task_mutex": "unknown",
                "lease_mutex": "unknown",
            },
            lifecycle_check="UNKNOWN",
            mutex_check="UNKNOWN",
            residue_check="UNKNOWN",
            residue_count=0,
            residue_digest=None,
            snapshot_check="UNKNOWN",
        )

    mutexes: list[_MutexHandle] = []
    directories: list[_DirectoryHandle] = []
    statuses = {
        "adoption_mutex": "unknown",
        "verification_mutex": "unknown",
        "task_mutex": "unknown",
        "lease_mutex": "unknown",
    }
    lifecycle: dict[str, Any] | None = None
    lifecycle_class = "unknown"
    lifecycle_check = "UNKNOWN"
    mutex_check = "UNKNOWN"
    residue_check = "UNKNOWN"
    residue_count = 0
    residue_digest: str | None = None
    snapshot_check = "UNKNOWN"
    repository_after: dict[str, Any] | None = None
    graph_stable = True
    try:
        statuses, mutexes, directories, named, complete = _acquire_lock_graph(
            root,
            worktree_dir,
            common_dir,
            task_id,
            verification_required=verification_required,
        )
        if not complete:
            mutex_check = "FAIL"
            snapshot_check = "PASS"
            return _state_result(
                task_id=task_id,
                lifecycle=None,
                lifecycle_class="unknown",
                statuses=statuses,
                lifecycle_check="UNKNOWN",
                mutex_check=mutex_check,
                residue_check="UNKNOWN",
                residue_count=0,
                residue_digest=None,
                snapshot_check=snapshot_check,
            )
        mutex_check = "PASS"
        after = _control_surface_snapshot(root, worktree_dir, common_dir)
        snapshot_check = "PASS" if after == before else "FAIL"
        if _repository_before is not None:
            repository_after = observe_repository_snapshot(root)
            if dict(_repository_before) != repository_after:
                snapshot_check = "FAIL"
        lifecycle, lifecycle_class, coherent = _evaluate_lifecycle(
            root,
            worktree_dir,
            common_dir,
            task_id,
            mutexes,
            named,
            repository_after or _repository_before,
        )
        lifecycle_check = "PASS" if coherent else "FAIL"
        classes, residue_digest = _owned_residue(worktree_dir, common_dir)
        residue_count = len(classes)
        residue_check = "PASS" if not classes else "FAIL"
    except StablePauseError as error:
        if error.code == "E_STABLE_PAUSE_OPERATION_ACTIVE":
            mutex_check = "FAIL"
        elif error.code == "E_STABLE_PAUSE_SNAPSHOT_DRIFT":
            snapshot_check = "FAIL"
        elif error.code == "E_STABLE_PAUSE_LIFECYCLE":
            lifecycle_check = "FAIL"
            lifecycle_class = "contradiction"
        elif error.code == "E_STABLE_PAUSE_RESIDUE":
            residue_check = "FAIL"
        else:
            if lifecycle_check == "UNKNOWN":
                lifecycle_check = "UNKNOWN"
            if residue_check == "UNKNOWN":
                residue_digest = None
    finally:
        if mutexes or directories:
            graph_stable = _close_lock_graph(mutexes, directories)
    if not graph_stable:
        snapshot_check = "FAIL"
    result = _state_result(
        task_id=task_id,
        lifecycle=lifecycle,
        lifecycle_class=lifecycle_class,
        statuses=statuses,
        lifecycle_check=lifecycle_check,
        mutex_check=mutex_check,
        residue_check=residue_check,
        residue_count=residue_count,
        residue_digest=residue_digest,
        snapshot_check=snapshot_check,
    )
    if repository_after is not None:
        result["_repository_snapshot"] = repository_after
    return result


def unknown_stable_pause_observation(
    repository: Path | str,
    task_id: str,
    *,
    issue_code: str = "E_STABLE_PAUSE_BOUNDS",
) -> dict[str, Any]:
    """Build one privacy-safe UNKNOWN object without echoing exception text."""

    issue_dimensions = {
        "E_STABLE_PAUSE_REPOSITORY": "repository",
        "E_STABLE_PAUSE_SNAPSHOT_DRIFT": "snapshot",
        "E_STABLE_PAUSE_LIFECYCLE": "lifecycle",
        "E_STABLE_PAUSE_OPERATION_ACTIVE": "operation",
        "E_STABLE_PAUSE_RESIDUE": "residue",
        "E_STABLE_PAUSE_BOUNDS": "bounds",
    }
    if issue_code not in issue_dimensions or not validate_task_id(task_id):
        issue_code = "E_STABLE_PAUSE_BOUNDS"
        task_id = task_id if validate_task_id(task_id) else "TASK-UNKNOWN"
    try:
        root = Path(repository).resolve(strict=False)
    except (OSError, RuntimeError):
        root = Path.cwd().resolve(strict=False)
    root_text = str(root)
    try:
        root_bytes = root_text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        root_bytes = b""
    if (
        not root_text.startswith("/")
        or not 1 < len(root_bytes) <= 1024
        or "\0" in root_text
        or "\n" in root_text
        or "\r" in root_text
        or ".." in PurePosixPath(root_text).parts
    ):
        root_text = "/unknown"
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "StablePauseObservationV1",
        "scope": "core-owned-local-state",
        "status": "UNKNOWN",
        "repository": {
            "root": root_text,
            "common_git_dir": root_text,
            "branch": "unknown",
            "head": "0" * 40,
            "status_digest": None,
            "worktree_digest": None,
            "staged_count": 0,
            "unstaged_count": 0,
            "untracked_count": 0,
            "diff_check": "UNKNOWN",
        },
        "lifecycle": {
            "task_id": task_id,
            "task_state": "unknown",
            "task_state_digest": None,
            "lease_state": "unknown",
            "lease_digest": None,
            "owner_runtime_digest": None,
        },
        "control_plane_state": {
            "adoption_mutex": "unknown",
            "verification_mutex": "unknown",
            "task_mutex": "unknown",
            "lease_mutex": "unknown",
            "residue_count": 0,
            "residue_digest": None,
        },
        "checks": {
            "repository_identity": "UNKNOWN",
            "snapshot_stability": "UNKNOWN",
            "lifecycle_binding": "UNKNOWN",
            "mutex_quiescence": "UNKNOWN",
            "owned_residue": "UNKNOWN",
        },
        "issues": [
            {"code": issue_code, "dimension": issue_dimensions[issue_code]}
        ],
        "checkpoint_digest": "sha256:" + "0" * 64,
        "authorizes": False,
    }
    value["checkpoint_digest"] = stable_pause_checkpoint_digest(value)
    return validate_stable_pause_observation(value)


def observe_stable_pause(
    repository: Path | str,
    task_id: str,
) -> dict[str, Any]:
    """Assemble the complete bounded verify-only Stable Pause observation."""

    if not validate_task_id(task_id):
        return unknown_stable_pause_observation(
            repository,
            task_id,
            issue_code="E_STABLE_PAUSE_LIFECYCLE",
        )
    try:
        from control_plane.materialization import inspect_git_state_materialization

        materialization = inspect_git_state_materialization(Path(repository))
    except (OSError, RuntimeError, ValueError):
        materialization = None
    if materialization is None or not materialization.ok:
        return unknown_stable_pause_observation(
            repository,
            task_id,
            issue_code="E_STABLE_PAUSE_REPOSITORY",
        )
    try:
        repository_before = observe_repository_snapshot(repository)
        state = observe_control_plane_snapshot(
            repository,
            task_id,
            _repository_before=repository_before,
        )
    except StablePauseError as error:
        return unknown_stable_pause_observation(
            repository,
            task_id,
            issue_code=(
                error.code
                if error.code
                in {
                    "E_STABLE_PAUSE_REPOSITORY",
                    "E_STABLE_PAUSE_SNAPSHOT_DRIFT",
                    "E_STABLE_PAUSE_LIFECYCLE",
                    "E_STABLE_PAUSE_OPERATION_ACTIVE",
                    "E_STABLE_PAUSE_RESIDUE",
                    "E_STABLE_PAUSE_BOUNDS",
                }
                else "E_STABLE_PAUSE_BOUNDS"
            ),
        )
    repository_final = state.pop("_repository_snapshot", repository_before)
    checks = {
        "repository_identity": (
            "UNKNOWN" if repository_final.get("diff_check") == "UNKNOWN" else "PASS"
        ),
        "snapshot_stability": state["checks"]["snapshot_stability"],
        "lifecycle_binding": state["checks"]["lifecycle_binding"],
        "mutex_quiescence": state["checks"]["mutex_quiescence"],
        "owned_residue": state["checks"]["owned_residue"],
    }
    issues = list(state["issues"])
    if checks["repository_identity"] == "UNKNOWN":
        issues.append(
            {"code": "E_STABLE_PAUSE_REPOSITORY", "dimension": "repository"}
        )
    issue_pairs = sorted(
        {(str(item["code"]), str(item["dimension"])) for item in issues}
    )
    issues = [{"code": code, "dimension": dimension} for code, dimension in issue_pairs]
    status = derive_stable_pause_status(checks, state["lifecycle_class"])
    value: dict[str, Any] = {
        "schema_version": 1,
        "kind": "StablePauseObservationV1",
        "scope": "core-owned-local-state",
        "status": status,
        "repository": repository_final,
        "lifecycle": state["lifecycle"],
        "control_plane_state": state["control_plane_state"],
        "checks": checks,
        "issues": issues,
        "checkpoint_digest": "sha256:" + "0" * 64,
        "authorizes": False,
    }
    value["checkpoint_digest"] = stable_pause_checkpoint_digest(value)
    return validate_stable_pause_observation(value)
