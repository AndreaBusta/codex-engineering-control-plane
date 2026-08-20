"""Transactional local publication for Control Plane Core adoption."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import fcntl
from hashlib import sha256
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib
import uuid
from typing import Callable, Iterator, Mapping

from .contracts import (
    ADOPTION_LIFECYCLE,
    MANAGED_PARENT_PATHS,
    MANAGED_REPOSITORY_SCAN,
    canonical_json,
    contract_digest,
    load_closed_json,
    validate_closed_core_task,
    validate_journal,
    validate_plan,
    validate_receipt,
)
from .manifest import (
    CORE_RUNTIME_MODULES,
    TargetProjection,
    _canonical_lock_contract,
    build_target_projection,
    preview,
)
from .repository import (
    MANAGED_PATHS,
    PROVISIONING_PREFIXES,
    _assert_no_nested_repositories,
    _assert_single_worktree,
    _managed_parent_directories,
    _canonical_git_directory,
    _clean,
    _local_git_config_identity,
    _reject_content_filters,
    _run_git,
    _provisioning_state,
    _text,
    target_surface_digest,
)
from .safe_io import canonical_root, confined_lstat, metadata_identity, read_confined_file


STATE_ROOT = "codex-control-plane-core"
LOCK_NAME = "adoption.lock"
ADOPTION_DIRECTORY = "adoption"
JOURNAL_NAME = "journal.json"
EVIDENCE_DIRECTORY = "evidence"
FILE_MAX = 1024 * 1024
JOURNAL_MAX = 1024 * 1024
HOOKS_PATH = ".codex/git-hooks"
PRODUCT_VERSION = "3.1.0-core.2"
TOOL_VERSION = "0.1.0"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_PROVISIONING_TEMP = re.compile(r"^\.journal\.json\.[0-9a-f]{32}\.tmp$", re.ASCII)
_PROVISIONING_ADOPTION_QUARANTINE = ".provisioning-adoption"
_PROVISIONING_LOCKS_QUARANTINE = ".provisioning-locks"
FaultHook = Callable[[str], None]
DirectoryCreatedHook = Callable[[list[dict[str, object]]], None]


def _private_directory(metadata: os.stat_result, *, exact_mode: int | None = None) -> bool:
    mode = stat.S_IMODE(metadata.st_mode)
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and mode & 0o022 == 0
        and (exact_mode is None or mode == exact_mode)
        and not bool(int(getattr(metadata, "st_flags", 0)) & 0x40000000)
    )


def _private_lock(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_size == 0
        and not bool(int(getattr(metadata, "st_flags", 0)) & 0x40000000)
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_uid),
        int(metadata.st_gid),
        int(getattr(metadata, "st_flags", 0)),
    )


def _lifecycle_lock_record(metadata: os.stat_result) -> dict[str, object]:
    return {
        "path": f"{STATE_ROOT}/{LOCK_NAME}",
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "mode": stat.S_IMODE(metadata.st_mode),
        "links": int(metadata.st_nlink),
        "uid": int(metadata.st_uid),
        "gid": int(metadata.st_gid),
        "size": int(metadata.st_size),
        "mtime_ns": int(metadata.st_mtime_ns),
        "ctime_ns": int(metadata.st_ctime_ns),
        "flags": int(getattr(metadata, "st_flags", 0)),
    }


def _verification_directory_record(metadata: os.stat_result) -> dict[str, object]:
    return {
        "path": f"{STATE_ROOT}/locks",
        "device": int(metadata.st_dev),
        "inode": int(metadata.st_ino),
        "mode": stat.S_IMODE(metadata.st_mode),
        "uid": int(metadata.st_uid),
        "gid": int(metadata.st_gid),
        "flags": int(getattr(metadata, "st_flags", 0)),
    }


def _verification_lock_record(metadata: os.stat_result) -> dict[str, object]:
    record = _lifecycle_lock_record(metadata)
    record["path"] = f"{STATE_ROOT}/locks/verification.lock"
    return record


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_private_directory(path: Path, *, exact_mode: int | None = None) -> int:
    try:
        descriptor = os.open(path, _directory_flags())
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise ValueError("E_ADOPTION_PATH: private directory is unavailable") from error
    if not _private_directory(metadata, exact_mode=exact_mode):
        os.close(descriptor)
        raise ValueError("E_ADOPTION_PATH: private directory is unsafe")
    return descriptor


def _rename_noreplace(
    source_name: str,
    destination_name: str,
    *,
    source_directory: int,
    destination_directory: int,
) -> None:
    for value in (source_name, destination_name):
        if not value or PurePosixPath(value).name != value or "\x00" in value:
            raise ValueError("E_ADOPTION_PATH: atomic rename name is unsafe")
    library = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    destination = os.fsencode(destination_name)
    ctypes.set_errno(0)
    if hasattr(library, "renameatx_np"):
        operation = library.renameatx_np
        operation.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        operation.restype = ctypes.c_int
        result = operation(
            source_directory,
            source,
            destination_directory,
            destination,
            0x00000004,
        )
    elif hasattr(library, "renameat2"):
        operation = library.renameat2
        operation.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        operation.restype = ctypes.c_int
        result = operation(
            source_directory,
            source,
            destination_directory,
            destination,
            0x00000001,
        )
    else:
        raise ValueError("E_ADOPTION_FILESYSTEM: atomic no-replace rename is unavailable")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: managed destination appeared")
    raise ValueError(
        f"E_ADOPTION_PUBLISH: atomic no-replace rename failed ({error_number})"
    )


def _rename_exchange(first_name: str, second_name: str, *, directory: int) -> None:
    """Atomically exchange two leaves so the displaced inode can be verified."""

    for value in (first_name, second_name):
        if not value or PurePosixPath(value).name != value or "\x00" in value:
            raise ValueError("E_ADOPTION_GIT_CONFIG: config exchange name is unsafe")
    library = ctypes.CDLL(None, use_errno=True)
    first = os.fsencode(first_name)
    second = os.fsencode(second_name)
    ctypes.set_errno(0)
    if hasattr(library, "renameatx_np"):
        operation = library.renameatx_np
        operation.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        operation.restype = ctypes.c_int
        result = operation(directory, first, directory, second, 0x00000002)
    elif hasattr(library, "renameat2"):
        operation = library.renameat2
        operation.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        operation.restype = ctypes.c_int
        result = operation(directory, first, directory, second, 0x00000002)
    else:
        raise ValueError(
            "E_ADOPTION_FILESYSTEM: atomic config exchange is unavailable"
        )
    if result != 0:
        raise ValueError(
            "E_ADOPTION_GIT_CONFIG: atomic config exchange failed "
            f"({ctypes.get_errno()})"
        )


@dataclass
class _AdoptionLock:
    common: Path
    common_fd: int
    state_fd: int
    lock_fd: int
    created_root: bool
    created_lock: bool
    lifecycle_lock: dict[str, object]
    preserve: bool = False
    created_adoption: bool = False

    @property
    def state_root(self) -> Path:
        return self.common / STATE_ROOT

    @property
    def adoption_directory(self) -> Path:
        return self.state_root / ADOPTION_DIRECTORY

    def preserve_state(self) -> None:
        self.preserve = True

    def assert_current(
        self,
        expected: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        try:
            opened = os.fstat(self.lock_fd)
            named = os.stat(LOCK_NAME, dir_fd=self.state_fd, follow_symlinks=False)
        except OSError as error:
            raise ValueError("E_ADOPTION_LOCK: lifecycle lock is unavailable") from error
        if (
            not _private_lock(opened)
            or not _private_lock(named)
            or _directory_identity(opened) != _directory_identity(named)
        ):
            raise ValueError("E_ADOPTION_LOCK: lifecycle lock identity changed")
        current = _lifecycle_lock_record(opened)
        if expected is not None and current != dict(expected):
            raise ValueError("E_ADOPTION_LOCK: lifecycle lock binding changed")
        return current

    def ensure_adoption_directory(self) -> Path:
        try:
            metadata = os.stat(
                ADOPTION_DIRECTORY,
                dir_fd=self.state_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            try:
                os.mkdir(ADOPTION_DIRECTORY, 0o700, dir_fd=self.state_fd)
                os.fsync(self.state_fd)
                self.created_adoption = True
                metadata = os.stat(
                    ADOPTION_DIRECTORY,
                    dir_fd=self.state_fd,
                    follow_symlinks=False,
                )
            except OSError as error:
                raise ValueError("E_ADOPTION_STATE: adoption directory cannot be created") from error
        except OSError as error:
            raise ValueError("E_ADOPTION_STATE: adoption directory is unsafe") from error
        if not _private_directory(metadata, exact_mode=0o700):
            raise ValueError("E_ADOPTION_STATE: adoption directory is unsafe")
        return self.adoption_directory


@contextmanager
def _adoption_lock(
    common_directory: Path, *, create: bool = True
) -> Iterator[_AdoptionLock]:
    common = canonical_root(common_directory)
    common_fd = _open_private_directory(common)
    state_fd: int | None = None
    lock_fd: int | None = None
    created_root = False
    created_lock = False
    handle: _AdoptionLock | None = None
    try:
        if create:
            try:
                os.mkdir(STATE_ROOT, 0o700, dir_fd=common_fd)
                os.fsync(common_fd)
                created_root = True
            except FileExistsError:
                pass
            except OSError as error:
                raise ValueError("E_ADOPTION_LOCK: lock root cannot be created") from error
        try:
            state_fd = os.open(STATE_ROOT, _directory_flags(), dir_fd=common_fd)
        except OSError as error:
            raise ValueError("E_ADOPTION_LOCK: lock root cannot be opened safely") from error
        if not _private_directory(os.fstat(state_fd), exact_mode=0o700):
            raise ValueError("E_ADOPTION_LOCK: lock root is unsafe")
        flags = (
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            if not create:
                raise FileExistsError
            lock_fd = os.open(LOCK_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=state_fd)
            created_lock = True
            os.fsync(state_fd)
        except FileExistsError:
            try:
                before = os.stat(LOCK_NAME, dir_fd=state_fd, follow_symlinks=False)
                lock_fd = os.open(LOCK_NAME, flags, dir_fd=state_fd)
                opened = os.fstat(lock_fd)
            except FileNotFoundError as error:
                raise ValueError("E_ADOPTION_LOCK: adoption lock is absent") from error
            except OSError as error:
                raise ValueError("E_ADOPTION_LOCK: lock file cannot be opened safely") from error
            if metadata_identity(before) != metadata_identity(opened):
                raise ValueError("E_ADOPTION_LOCK: lock file identity changed")
        except OSError as error:
            raise ValueError("E_ADOPTION_LOCK: lock file cannot be created") from error
        if lock_fd is None or not _private_lock(os.fstat(lock_fd)):
            raise ValueError("E_ADOPTION_LOCK: lock file is unsafe")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("E_ADOPTION_BUSY: another adoption transaction holds the lock") from error
        except OSError as error:
            raise ValueError("E_ADOPTION_LOCK: adoption lock cannot be acquired") from error
        try:
            opened = os.fstat(lock_fd)
            named = os.stat(LOCK_NAME, dir_fd=state_fd, follow_symlinks=False)
        except OSError as error:
            raise ValueError("E_ADOPTION_LOCK: adoption lock path changed") from error
        if (
            not _private_lock(opened)
            or not _private_lock(named)
            or _directory_identity(opened) != _directory_identity(named)
        ):
            raise ValueError("E_ADOPTION_LOCK: adoption lock identity changed")
        lifecycle_lock = _lifecycle_lock_record(opened)
        handle = _AdoptionLock(
            common=common,
            common_fd=common_fd,
            state_fd=state_fd,
            lock_fd=lock_fd,
            created_root=created_root,
            created_lock=created_lock,
            lifecycle_lock=lifecycle_lock,
        )
        yield handle
    finally:
        if handle is not None and not handle.preserve:
            if handle.created_adoption:
                try:
                    opened_directory = os.stat(
                        ADOPTION_DIRECTORY,
                        dir_fd=handle.state_fd,
                        follow_symlinks=False,
                    )
                    if _private_directory(opened_directory, exact_mode=0o700):
                        os.rmdir(ADOPTION_DIRECTORY, dir_fd=handle.state_fd)
                        os.fsync(handle.state_fd)
                except OSError:
                    pass
            if handle.created_lock and lock_fd is not None:
                try:
                    opened_lock = os.fstat(lock_fd)
                    named_lock = os.stat(
                        LOCK_NAME,
                        dir_fd=handle.state_fd,
                        follow_symlinks=False,
                    )
                    if (
                        _private_lock(opened_lock)
                        and _private_lock(named_lock)
                        and metadata_identity(opened_lock)
                        == metadata_identity(named_lock)
                    ):
                        os.unlink(LOCK_NAME, dir_fd=handle.state_fd)
                        os.fsync(handle.state_fd)
                except OSError:
                    pass
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        if state_fd is not None:
            os.close(state_fd)
        if handle is not None and not handle.preserve and handle.created_root:
            try:
                os.rmdir(STATE_ROOT, dir_fd=common_fd)
                os.fsync(common_fd)
            except OSError:
                pass
        os.close(common_fd)


def _atomic_write(directory: Path, name: str, payload: bytes, *, mode: int = 0o600) -> None:
    directory_fd = _open_private_directory(directory, exact_mode=0o700)
    temporary = f".{name}.{uuid.uuid4().hex}.tmp"
    file_fd: int | None = None
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_fd = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        offset = 0
        while offset < len(payload):
            written = os.write(file_fd, payload[offset:])
            if written <= 0:
                raise ValueError("E_ADOPTION_IO: durable write made no progress")
            offset += written
        os.fchmod(file_fd, mode)
        os.fsync(file_fd)
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_size != len(payload)
        ):
            raise ValueError("E_ADOPTION_IO: durable file validation failed")
        os.close(file_fd)
        file_fd = None
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("E_ADOPTION_IO: atomic write failed") from error
    finally:
        if file_fd is not None:
            os.close(file_fd)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        os.close(directory_fd)


def _sealed_journal(unsigned: Mapping[str, object]) -> dict[str, object]:
    journal = dict(unsigned)
    journal["state_digest"] = contract_digest(unsigned)
    issues = validate_journal(journal)
    if issues:
        raise ValueError(f"E_ADOPTION_JOURNAL: generated journal is invalid ({issues[0].code})")
    return journal


def _write_journal(adoption_directory: Path, journal: Mapping[str, object]) -> None:
    payload = (canonical_json(journal) + "\n").encode("utf-8")
    _atomic_write(adoption_directory, JOURNAL_NAME, payload)


def _read_journal(common: Path) -> dict[str, object] | None:
    relative = f"{STATE_ROOT}/{ADOPTION_DIRECTORY}/{JOURNAL_NAME}"
    if confined_lstat(common, relative) is None:
        return None
    try:
        payload = read_confined_file(common, relative, maximum=JOURNAL_MAX)
        journal = load_closed_json(payload, limit=JOURNAL_MAX)
    except (OSError, ValueError, RecursionError) as error:
        raise ValueError("E_ADOPTION_JOURNAL: journal cannot be observed safely") from error
    issues = validate_journal(journal)
    if issues:
        raise ValueError(f"E_ADOPTION_JOURNAL: journal is invalid ({issues[0].code})")
    return journal


def _assert_lifecycle_binding(
    adoption_lock: _AdoptionLock,
    artifact: Mapping[str, object],
) -> dict[str, object]:
    expected = artifact.get("lifecycle_lock")
    if not isinstance(expected, Mapping):
        raise ValueError("E_ADOPTION_LOCK: lifecycle lock binding is absent")
    return adoption_lock.assert_current(expected)


def _receipt_name(install_digest: str) -> str:
    return f"{install_digest.removeprefix('sha256:')}.json"


def _read_receipt(common: Path, install_digest: str) -> dict[str, object] | None:
    relative = (
        f"{STATE_ROOT}/{ADOPTION_DIRECTORY}/{EVIDENCE_DIRECTORY}/"
        f"{_receipt_name(install_digest)}"
    )
    if confined_lstat(common, relative) is None:
        return None
    payload = read_confined_file(common, relative, maximum=JOURNAL_MAX)
    receipt = load_closed_json(payload, limit=JOURNAL_MAX)
    issues = validate_receipt(receipt)
    if issues:
        raise ValueError(f"E_ADOPTION_RECEIPT: receipt is invalid ({issues[0].code})")
    return receipt


def _target_binding(plan: Mapping[str, object]) -> dict[str, object]:
    target = plan["target"]
    if not isinstance(target, Mapping):
        raise ValueError("E_ADOPTION_PLAN: target binding is invalid")
    return {
        "repository_id": list(target["repository_id"]),
        "common_dir_id": list(target["common_dir_id"]),
        "worktree_id": list(target["worktree_id"]),
        "branch": target["branch"],
        "head": target["head"],
        "policy_digest": target["policy_digest"],
        "registry_digest": target["registry_digest"],
        "adoption_lifecycle": target["adoption_lifecycle"],
    }


def _install_digest(plan: Mapping[str, object]) -> str:
    source = plan["source"]
    if not isinstance(source, Mapping):
        raise ValueError("E_ADOPTION_PLAN: source binding is invalid")
    return contract_digest(
        {
            "schema_version": 1,
            "kind": "CoreAdoptionInstallBindingV1",
            "plan_digest": plan["plan_digest"],
            "source_manifest_digest": source["manifest_digest"],
            "target_binding": _target_binding(plan),
            "managed_records": plan["managed_records"],
            "authorizes": False,
        }
    )


def _validate_plan_binding(
    source: Path,
    target: Path,
    plan: Mapping[str, object],
    *,
    adoption_lock_held: bool,
    provisioning_recovery: bool = False,
) -> TargetProjection:
    observed = preview(
        source,
        target,
        adoption_lock_held=adoption_lock_held,
        provisioning_recovery=provisioning_recovery,
    )
    if canonical_json(observed) != canonical_json(plan):
        raise ValueError("E_ADOPTION_TARGET_DRIFT: reviewed plan no longer matches source and target")
    target_observation = observed["target"]
    if not isinstance(target_observation, Mapping):
        raise ValueError("E_ADOPTION_PLAN: target observation is invalid")
    # The projection is rebuilt once more to hand publication immutable bytes
    # that are independently bound by the reviewed records.
    from .repository import observe_target

    observation = observe_target(
        target,
        authority_source=source,
        adoption_lock_held=adoption_lock_held,
        provisioning_recovery=provisioning_recovery,
    )
    projection = build_target_projection(source, observation)
    manifest = projection.source_manifest
    projected_source = {
        "head": manifest.get("head"),
        "tree": manifest.get("tree"),
        "product_version": manifest.get("product_version"),
        "runtime_digest": manifest.get("runtime_digest"),
        "lock_digest": manifest.get("source_lock_digest"),
        "manifest_digest": manifest.get("manifest_digest"),
    }
    source_binding = plan.get("source")
    if not isinstance(source_binding, Mapping) or projected_source != dict(source_binding):
        raise ValueError("E_ADOPTION_SOURCE_DRIFT: source manifest changed")
    if list(projection.records) != plan["managed_records"]:
        raise ValueError("E_ADOPTION_SOURCE_DRIFT: projected bytes changed")
    return projection


def _safe_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if (
        not relative
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in relative
        or "\x00" in relative
    ):
        raise ValueError("E_ADOPTION_PATH: managed path is unsafe")
    return tuple(path.parts)


def _planned_directories(
    target: Path,
    records: object,
    *,
    target_binding: Mapping[str, object],
    before_snapshot_digest: object,
    managed_parent_directories: object,
    managed_repository_scan: object,
) -> list[dict[str, object]]:
    if not isinstance(records, list):
        raise ValueError("E_ADOPTION_PLAN: managed records are invalid")
    directories: set[str] = set()
    for item in records:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
            raise ValueError("E_ADOPTION_PLAN: managed record is invalid")
        parts = _safe_parts(item["path"])
        for depth in range(1, len(parts)):
            relative = PurePosixPath(*parts[:depth]).as_posix()
            if relative != ".codex":
                directories.add(relative)
    ordered = sorted(
        directories,
        key=lambda value: (value.count("/"), value),
    )
    expected = [relative for relative in MANAGED_PARENT_PATHS if relative != ".codex"]
    if ordered != expected:
        raise ValueError("E_ADOPTION_PLAN: managed parent projection is not exact")
    if not isinstance(managed_parent_directories, list):
        raise ValueError("E_ADOPTION_PLAN: managed parent binding is invalid")
    if not isinstance(managed_repository_scan, Mapping):
        raise ValueError("E_ADOPTION_PLAN: managed repository scan is invalid")
    parents = _managed_parent_directories(target)
    if list(parents) != managed_parent_directories:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: managed parent binding changed")
    repository_scan = _assert_no_nested_repositories(target)
    if repository_scan != dict(managed_repository_scan):
        raise ValueError("E_ADOPTION_TARGET_DRIFT: managed repository scan changed")
    observed_snapshot = target_surface_digest(
        target_binding,
        managed_parent_directories=parents,
        managed_repository_scan=repository_scan,
    )
    if observed_snapshot != before_snapshot_digest:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: managed parent binding changed")
    by_path = {str(item["path"]): item for item in parents}
    return [
        {"path": relative, "mode": 0o755, "identity": None}
        for relative in ordered
        if by_path[relative]["state"] == "absent"
    ]


def _parent_bindings(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) != len(MANAGED_PARENT_PATHS):
        raise ValueError("E_ADOPTION_JOURNAL: managed parent bindings are invalid")
    records = [dict(item) for item in value if isinstance(item, Mapping)]
    if len(records) != len(MANAGED_PARENT_PATHS) or [
        item.get("path") for item in records
    ] != list(MANAGED_PARENT_PATHS):
        raise ValueError("E_ADOPTION_JOURNAL: managed parent bindings are invalid")
    return records


def _assert_preexisting_managed_parents(
    target: Path,
    bindings: object,
    *,
    code: str,
) -> None:
    expected = _parent_bindings(bindings)
    observed = {
        str(item["path"]): item for item in _managed_parent_directories(target)
    }
    for record in expected:
        if record.get("state") == "present" and observed.get(str(record["path"])) != record:
            raise ValueError(f"{code}: pre-existing managed parent changed")


def _managed_directory_identities(
    bindings: object,
    created: object,
) -> dict[str, tuple[list[int], int]]:
    result: dict[str, tuple[list[int], int]] = {}
    for record in _parent_bindings(bindings):
        identity = record.get("identity")
        mode = record.get("mode")
        if record.get("state") == "present" and isinstance(identity, list) and type(mode) is int:
            result[str(record["path"])] = (list(identity), mode)
    if not isinstance(created, list):
        raise ValueError("E_ADOPTION_JOURNAL: created directory bindings are invalid")
    for record in created:
        if not isinstance(record, Mapping):
            raise ValueError("E_ADOPTION_JOURNAL: created directory binding is invalid")
        identity = record.get("identity")
        mode = record.get("mode")
        if identity is not None:
            if not isinstance(identity, list) or len(identity) != 2 or type(mode) is not int:
                raise ValueError("E_ADOPTION_JOURNAL: created directory identity is invalid")
            result[str(record["path"])] = (list(identity), mode)
    return result


def _open_bound_target_root(target: Path, target_binding: Mapping[str, object]) -> int:
    descriptor = _open_private_directory(target)
    try:
        opened = os.fstat(descriptor)
        named = target.lstat()
        expected = target_binding.get("repository_id")
        identity = [int(opened.st_dev), int(opened.st_ino)]
        if (
            identity != expected
            or identity != [int(named.st_dev), int(named.st_ino)]
        ):
            raise ValueError("E_ADOPTION_TARGET_DRIFT: target root identity changed")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_target_parent(
    root_fd: int,
    parts: tuple[str, ...],
    *,
    expected_directories: Mapping[str, tuple[list[int], int]] | None = None,
) -> list[int]:
    descriptors = [os.dup(root_fd)]
    try:
        traversed: list[str] = []
        for component in parts:
            traversed.append(component)
            descriptor = os.open(component, _directory_flags(), dir_fd=descriptors[-1])
            metadata = os.fstat(descriptor)
            if not _private_directory(metadata):
                os.close(descriptor)
                raise ValueError("E_ADOPTION_PATH: target directory is unsafe")
            relative = PurePosixPath(*traversed).as_posix()
            expected = (expected_directories or {}).get(relative)
            if expected is not None and (
                [int(metadata.st_dev), int(metadata.st_ino)] != expected[0]
                or stat.S_IMODE(metadata.st_mode) != expected[1]
            ):
                os.close(descriptor)
                raise ValueError("E_ADOPTION_TARGET_DRIFT: managed parent identity changed")
            descriptors.append(descriptor)
        return descriptors
    except OSError as error:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise ValueError("E_ADOPTION_PATH: target directory cannot be opened safely") from error
    except ValueError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _create_target_directories(
    target: Path,
    adoption_directory: Path,
    install_digest: str,
    records: list[dict[str, object]],
    *,
    target_binding: Mapping[str, object],
    managed_parent_directories: object,
    on_created: DirectoryCreatedHook | None = None,
) -> None:
    _assert_preexisting_managed_parents(
        target,
        managed_parent_directories,
        code="E_ADOPTION_TARGET_DRIFT",
    )
    _assert_no_nested_repositories(target)
    root_fd = _open_bound_target_root(target, target_binding)
    adoption_fd = _open_private_directory(adoption_directory, exact_mode=0o700)
    staging_directory_name = _directory_staging_name(install_digest)
    staging_fd: int | None = None
    try:
        try:
            os.mkdir(staging_directory_name, 0o700, dir_fd=adoption_fd)
            os.fsync(adoption_fd)
            staging_fd = os.open(staging_directory_name, _directory_flags(), dir_fd=adoption_fd)
        except OSError as error:
            raise ValueError("E_ADOPTION_STAGE: directory staging cannot be created") from error
        if not _private_directory(os.fstat(staging_fd), exact_mode=0o700):
            raise ValueError("E_ADOPTION_STAGE: directory staging is unsafe")
        for record in records:
            relative = str(record["path"])
            parts = _safe_parts(relative)
            staged_name = _directory_record_name(relative)
            try:
                os.mkdir(staged_name, int(record["mode"]), dir_fd=staging_fd)
                os.fsync(staging_fd)
                metadata = os.stat(staged_name, dir_fd=staging_fd, follow_symlinks=False)
            except OSError as error:
                raise ValueError("E_ADOPTION_STAGE: managed directory cannot be staged") from error
            if not _private_directory(metadata, exact_mode=int(record["mode"])):
                raise ValueError("E_ADOPTION_PATH: staged directory is unsafe")
            record["identity"] = [int(metadata.st_dev), int(metadata.st_ino)]
            if on_created is not None:
                on_created(records)
            descriptors = _open_target_parent(
                root_fd,
                parts[:-1],
                expected_directories=_managed_directory_identities(
                    managed_parent_directories,
                    records,
                ),
            )
            try:
                _rename_noreplace(
                    staged_name,
                    parts[-1],
                    source_directory=staging_fd,
                    destination_directory=descriptors[-1],
                )
                os.fsync(descriptors[-1])
                os.fsync(staging_fd)
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
        os.close(staging_fd)
        staging_fd = None
        os.rmdir(staging_directory_name, dir_fd=adoption_fd)
        os.fsync(adoption_fd)
        _assert_preexisting_managed_parents(
            target,
            managed_parent_directories,
            code="E_ADOPTION_TARGET_DRIFT",
        )
        _assert_no_nested_repositories(target)
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
        os.close(adoption_fd)
        os.close(root_fd)


def _directory_staging_name(install_digest: str) -> str:
    return f".directory-staging-{install_digest.removeprefix('sha256:')}"


def _directory_record_name(relative: str) -> str:
    return sha256(relative.encode("utf-8", errors="strict")).hexdigest()


def _stage_projection(
    adoption_directory: Path,
    install_digest: str,
    projection: TargetProjection,
) -> tuple[Path, dict[str, str]]:
    name = f".staging-{install_digest.removeprefix('sha256:')}"
    adoption_fd = _open_private_directory(adoption_directory, exact_mode=0o700)
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=adoption_fd)
            os.fsync(adoption_fd)
        except OSError as error:
            raise ValueError("E_ADOPTION_STAGE: staging directory cannot be created") from error
    finally:
        os.close(adoption_fd)
    staging = adoption_directory / name
    staged: dict[str, str] = {}
    for index, record in enumerate(projection.records):
        relative = str(record["path"])
        filename = f"{index:04d}"
        mode = 0o755 if record["git_mode"] == "100755" else 0o644
        _atomic_write(staging, filename, projection.payloads[relative], mode=mode)
        staged[relative] = filename
    return staging, staged


def _verify_installed_record(target: Path, record: Mapping[str, object]) -> None:
    relative = str(record["path"])
    payload = read_confined_file(target, relative, maximum=FILE_MAX)
    metadata = confined_lstat(target, relative)
    expected_mode = 0o755 if record["git_mode"] == "100755" else 0o644
    if (
        metadata is None
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or len(payload) != record["size_bytes"]
        or f"sha256:{sha256(payload).hexdigest()}" != record["sha256"]
    ):
        raise ValueError("E_ADOPTION_PUBLISH: installed record does not match its binding")


def _publish_staged_record(
    target: Path,
    staging: Path,
    staged_name: str,
    record: Mapping[str, object],
    *,
    target_binding: Mapping[str, object],
    managed_parent_directories: object,
    created_directories: object,
) -> None:
    parts = _safe_parts(str(record["path"]))
    root_fd = _open_bound_target_root(target, target_binding)
    staging_fd = _open_private_directory(staging, exact_mode=0o700)
    descriptors: list[int] = []
    try:
        descriptors = _open_target_parent(
            root_fd,
            parts[:-1],
            expected_directories=_managed_directory_identities(
                managed_parent_directories,
                created_directories,
            ),
        )
        _rename_noreplace(
            staged_name,
            parts[-1],
            source_directory=staging_fd,
            destination_directory=descriptors[-1],
        )
        os.fsync(descriptors[-1])
        os.fsync(staging_fd)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("E_ADOPTION_PUBLISH: atomic publication failed") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        os.close(staging_fd)
        os.close(root_fd)
    _verify_installed_record(target, record)


def _remove_empty_staging(staging: Path) -> None:
    parent = canonical_root(staging.parent)
    parent_fd = _open_private_directory(parent, exact_mode=0o700)
    try:
        os.rmdir(staging.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as error:
        raise ValueError("E_ADOPTION_STAGE: staging directory is not empty") from error
    finally:
        os.close(parent_fd)


def _journal_unsigned(
    plan: Mapping[str, object],
    install_digest: str,
    created_directories: list[dict[str, object]],
    lifecycle_lock: Mapping[str, object],
    verification_lock: Mapping[str, object],
) -> dict[str, object]:
    source = plan["source"]
    if not isinstance(source, Mapping):
        raise ValueError("E_ADOPTION_PLAN: source binding is invalid")
    lock_record = next(
        record
        for record in plan["managed_records"]
        if record["path"] == ".codex/control-plane.lock"
    )
    return {
        "schema_version": 1,
        "kind": "CoreAdoptionJournalV1",
        "plan_digest": plan["plan_digest"],
        "install_digest": install_digest,
        "state": "prepared",
        "source_manifest_digest": source["manifest_digest"],
        "target_binding": _target_binding(plan),
        "before_snapshot_digest": plan["before_snapshot_digest"],
        "managed_parent_directories": [
            dict(item) for item in plan["target"]["managed_parent_directories"]
        ],
        "managed_repository_scan": dict(plan["target"]["managed_repository_scan"]),
        "lifecycle_lock": dict(lifecycle_lock),
        "verification_lock": {
            "directory": dict(verification_lock["directory"]),
            "file": dict(verification_lock["file"]),
        },
        "created_directories": created_directories,
        "published_records": [],
        "target_lock_record": dict(lock_record),
        "prior_git_config": {"core.hooksPath": None},
        "rollback_records": [
            {**dict(record), "before": "absent"}
            for record in plan["managed_records"]
        ],
        "authorizes": False,
    }


def _transition_journal(
    journal: Mapping[str, object],
    *,
    state: str | None = None,
    created_directories: object | None = None,
    published_records: object | None = None,
) -> dict[str, object]:
    unsigned = {
        key: value
        for key, value in journal.items()
        if key != "state_digest"
    }
    if state is not None:
        unsigned["state"] = state
    if created_directories is not None:
        unsigned["created_directories"] = created_directories
    if published_records is not None:
        unsigned["published_records"] = published_records
    return _sealed_journal(unsigned)


def _private_git_config(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == os.geteuid()
        and metadata.st_nlink == 1
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
        and 0 <= metadata.st_size <= FILE_MAX
        and int(getattr(metadata, "st_flags", 0)) == 0
    )


def _git_config_binding_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(metadata.st_uid),
        int(metadata.st_gid),
        int(metadata.st_size),
        int(getattr(metadata, "st_flags", 0)),
    )


def _read_config_descriptor(descriptor: int) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        before = os.fstat(descriptor)
        if not _private_git_config(before):
            raise OSError("unsafe config descriptor")
        payload = bytearray()
        while len(payload) <= FILE_MAX:
            chunk = os.read(
                descriptor,
                min(65_536, FILE_MAX + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config is unreadable") from error
    if (
        metadata_identity(before) != metadata_identity(after)
        or len(payload) > FILE_MAX
        or len(payload) != after.st_size
    ):
        raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config changed during read")
    return bytes(payload)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    try:
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("short config write")
            offset += written
        os.fsync(descriptor)
    except OSError as error:
        raise ValueError("E_ADOPTION_GIT_CONFIG: config staging failed") from error


def _config_file_value(target: Path, config: Path) -> bytes:
    return _run_git(
        target,
        "config",
        "--file",
        str(config),
        "--get-all",
        "core.hooksPath",
        allowed_returncodes=(0, 1),
    )


def _remove_config_temp(directory: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not _private_git_config(metadata):
            raise OSError("unsafe config temporary")
        os.unlink(name, dir_fd=directory)
        os.fsync(directory)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError("E_ADOPTION_GIT_CONFIG: config staging cleanup failed") from error


def _replace_local_git_config(
    target: Path,
    *,
    expected_before: bytes,
    expected_after: bytes,
    mutation: tuple[str, ...],
) -> None:
    """Prepare with Git off-path, then atomically exchange one bound config leaf."""

    _local_git_config_identity(target)
    git_directory = _canonical_git_directory(
        target,
        "rev-parse",
        "--absolute-git-dir",
    )
    directory = _open_private_directory(git_directory)
    original = -1
    temporary = f".codex-control-plane-config.{uuid.uuid4().hex}.tmp"
    temporary_owned = False
    exchanged = False
    try:
        directory_identity = _directory_identity(os.fstat(directory))
        before = os.stat("config", dir_fd=directory, follow_symlinks=False)
        if not _private_git_config(before):
            raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config is unsafe")
        original = os.open(
            "config",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory,
        )
        opened = os.fstat(original)
        named = os.stat("config", dir_fd=directory, follow_symlinks=False)
        if (
            metadata_identity(before) != metadata_identity(opened)
            or metadata_identity(before) != metadata_identity(named)
        ):
            raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config changed")
        original_payload = _read_config_descriptor(original)
        mode = stat.S_IMODE(before.st_mode)
        staged = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=directory,
        )
        temporary_owned = True
        try:
            os.fchmod(staged, mode)
            _write_all(staged, original_payload)
        finally:
            os.close(staged)
        os.fsync(directory)
        temporary_path = git_directory / temporary
        if _config_file_value(target, temporary_path) != expected_before:
            raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config precondition drifted")
        _run_git(
            target,
            "config",
            "--file",
            str(temporary_path),
            *mutation,
        )
        prepared = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        if (
            not _private_git_config(prepared)
            or _config_file_value(target, temporary_path) != expected_after
            or _directory_identity(os.fstat(directory)) != directory_identity
        ):
            raise ValueError("E_ADOPTION_GIT_CONFIG: prepared config is unsafe")
        opened_after = os.fstat(original)
        named_before_exchange = os.stat(
            "config",
            dir_fd=directory,
            follow_symlinks=False,
        )
        if (
            metadata_identity(opened) != metadata_identity(opened_after)
            or metadata_identity(before) != metadata_identity(named_before_exchange)
            or _read_config_descriptor(original) != original_payload
        ):
            raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config changed")
        prepared_identity = _git_config_binding_identity(prepared)
        _rename_exchange("config", temporary, directory=directory)
        exchanged = True
        temporary_owned = False
        try:
            displaced = os.stat(
                temporary,
                dir_fd=directory,
                follow_symlinks=False,
            )
            active = os.stat("config", dir_fd=directory, follow_symlinks=False)
            if (
                _git_config_binding_identity(displaced)
                != _git_config_binding_identity(before)
                or _git_config_binding_identity(active) != prepared_identity
                or _read_config_descriptor(original) != original_payload
                or _hooks_path(target) != expected_after
            ):
                raise ValueError("E_ADOPTION_GIT_CONFIG: config exchange drifted")
            os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        except (OSError, ValueError):
            _rename_exchange("config", temporary, directory=directory)
            exchanged = False
            temporary_owned = True
            os.fsync(directory)
            raise
        exchanged = False
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("E_ADOPTION_GIT_CONFIG: local Git config update failed") from error
    finally:
        if exchanged:
            try:
                _rename_exchange("config", temporary, directory=directory)
                temporary_owned = True
                os.fsync(directory)
            except ValueError:
                pass
        if temporary_owned:
            _remove_config_temp(directory, temporary)
        if original >= 0:
            os.close(original)
        os.close(directory)


def _set_hooks_path(target: Path) -> None:
    _local_git_config_identity(target)
    before = _run_git(
        target,
        "config",
        "--local",
        "--get-all",
        "core.hooksPath",
        allowed_returncodes=(0, 1),
    )
    if before:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: core.hooksPath appeared")
    _replace_local_git_config(
        target,
        expected_before=b"",
        expected_after=f"{HOOKS_PATH}\n".encode("utf-8"),
        mutation=("--add", "core.hooksPath", HOOKS_PATH),
    )
    after = _run_git(
        target,
        "config",
        "--local",
        "--get-all",
        "core.hooksPath",
    )
    if after != f"{HOOKS_PATH}\n".encode("utf-8"):
        raise ValueError("E_ADOPTION_GIT_CONFIG: core.hooksPath binding failed")


def _after_snapshot_digest(
    plan: Mapping[str, object],
    install_digest: str,
) -> str:
    return contract_digest(
        {
            "schema_version": 1,
            "kind": "CoreAdoptionActiveSnapshotV1",
            "install_digest": install_digest,
            "target_binding": _target_binding(plan),
            "managed_records": plan["managed_records"],
            "core_hooks_path": HOOKS_PATH,
            "authorizes": False,
        }
    )


def _receipt(
    plan: Mapping[str, object],
    install_digest: str,
    lifecycle_lock: Mapping[str, object],
) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreAdoptionReceiptV1",
        "operation": "apply",
        "plan_digest": plan["plan_digest"],
        "install_digest": install_digest,
        "before_snapshot_digest": plan["before_snapshot_digest"],
        "after_snapshot_digest": _after_snapshot_digest(plan, install_digest),
        "result": "PASS",
        "error_codes": [],
        "lifecycle_lock": dict(lifecycle_lock),
        "authorizes": False,
    }
    receipt = dict(unsigned)
    receipt["receipt_digest"] = contract_digest(unsigned)
    issues = validate_receipt(receipt)
    if issues:
        raise ValueError(f"E_ADOPTION_RECEIPT: generated receipt is invalid ({issues[0].code})")
    return receipt


def _ensure_evidence_directory(lock: _AdoptionLock) -> Path:
    adoption = lock.ensure_adoption_directory()
    adoption_fd = _open_private_directory(adoption, exact_mode=0o700)
    try:
        try:
            os.mkdir(EVIDENCE_DIRECTORY, 0o700, dir_fd=adoption_fd)
            os.fsync(adoption_fd)
        except FileExistsError:
            pass
        except OSError as error:
            raise ValueError("E_ADOPTION_RECEIPT: evidence directory cannot be created") from error
        metadata = os.stat(
            EVIDENCE_DIRECTORY,
            dir_fd=adoption_fd,
            follow_symlinks=False,
        )
        if not _private_directory(metadata, exact_mode=0o700):
            raise ValueError("E_ADOPTION_RECEIPT: evidence directory is unsafe")
    finally:
        os.close(adoption_fd)
    return adoption / EVIDENCE_DIRECTORY


def _replay(
    common: Path,
    plan: Mapping[str, object],
    journal: Mapping[str, object],
) -> dict[str, object]:
    install_digest = _install_digest(plan)
    if (
        journal.get("plan_digest") != plan.get("plan_digest")
        or journal.get("install_digest") != install_digest
    ):
        raise ValueError("E_ADOPTION_REPLAY: existing adoption binding differs")
    if journal.get("state") != "active":
        raise ValueError("E_ADOPTION_RECOVERY_REQUIRED: adoption transaction is incomplete")
    receipt = _read_receipt(common, install_digest)
    if (
        receipt is None
        or receipt.get("operation") != "apply"
        or receipt.get("plan_digest") != plan.get("plan_digest")
        or receipt.get("install_digest") != install_digest
    ):
        raise ValueError("E_ADOPTION_RECOVERY_REQUIRED: active receipt is unavailable")
    return receipt


def _remove_empty_provisioning_quarantine(
    adoption_lock: _AdoptionLock,
    name: str,
) -> None:
    state_fd = adoption_lock.state_fd
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=state_fd)
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
        if (
            not _private_directory(opened, exact_mode=0o700)
            or not _private_directory(named, exact_mode=0o700)
            or _directory_identity(opened) != _directory_identity(named)
        ):
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning quarantine changed"
            )
        with os.scandir(descriptor) as entries:
            if any(True for _ in entries):
                raise ValueError(
                    "E_ADOPTION_RECOVERY_REQUIRED: provisioning quarantine is not empty"
                )
        named = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
        if _directory_identity(opened) != _directory_identity(named):
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning quarantine changed"
            )
        os.rmdir(name, dir_fd=state_fd)
        os.fsync(state_fd)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(
            "E_ADOPTION_RECOVERY_REQUIRED: provisioning quarantine cannot be removed"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _quarantine_empty_provisioning_directory(
    adoption_lock: _AdoptionLock,
    name: str,
    quarantine: str,
    *,
    fault: FaultHook | None,
    quarantined_boundary: str,
) -> None:
    state_fd = adoption_lock.state_fd
    descriptor: int | None = None
    moved = False
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=state_fd)
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=state_fd, follow_symlinks=False)
        if (
            not _private_directory(opened, exact_mode=0o700)
            or not _private_directory(named, exact_mode=0o700)
            or _directory_identity(opened) != _directory_identity(named)
        ):
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning directory changed"
            )
        with os.scandir(descriptor) as entries:
            if any(True for _ in entries):
                raise ValueError(
                    "E_ADOPTION_RECOVERY_REQUIRED: provisioning directory is not empty"
                )
        _rename_noreplace(
            name,
            quarantine,
            source_directory=state_fd,
            destination_directory=state_fd,
        )
        moved = True
        os.fsync(state_fd)
        quarantined = os.stat(
            quarantine,
            dir_fd=state_fd,
            follow_symlinks=False,
        )
        if (
            not _private_directory(quarantined, exact_mode=0o700)
            or _directory_identity(opened) != _directory_identity(quarantined)
        ):
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning directory changed during quarantine"
            )
        try:
            os.stat(name, dir_fd=state_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning directory reappeared"
            )
    except (OSError, ValueError) as error:
        if moved:
            try:
                _rename_noreplace(
                    quarantine,
                    name,
                    source_directory=state_fd,
                    destination_directory=state_fd,
                )
                os.fsync(state_fd)
            except (OSError, ValueError):
                pass
        if isinstance(error, ValueError) and str(error).startswith(
            "E_ADOPTION_RECOVERY_REQUIRED"
        ):
            raise
        raise ValueError(
            "E_ADOPTION_RECOVERY_REQUIRED: provisioning directory cannot be quarantined"
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if fault is not None:
        fault(quarantined_boundary)
    _remove_empty_provisioning_quarantine(adoption_lock, quarantine)


def _reset_exact_provisioning_state(
    adoption_lock: _AdoptionLock,
    *,
    fault: FaultHook | None = None,
) -> None:
    """Normalize any exact journal-less provisioning prefix to P1."""

    state_fd = adoption_lock.state_fd
    while True:
        current = _provisioning_state(adoption_lock.common)
        if current == "P1":
            adoption_lock.assert_current()
            return
        if current not in PROVISIONING_PREFIXES:
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning state is not exact"
            )
        directory_fd: int | None = None
        descriptor: int | None = None
        try:
            if current == "P4T":
                directory_fd = os.open(
                    ADOPTION_DIRECTORY,
                    _directory_flags(),
                    dir_fd=state_fd,
                )
                opened_directory = os.fstat(directory_fd)
                named_directory = os.stat(
                    ADOPTION_DIRECTORY,
                    dir_fd=state_fd,
                    follow_symlinks=False,
                )
                if (
                    not _private_directory(opened_directory, exact_mode=0o700)
                    or metadata_identity(opened_directory)
                    != metadata_identity(named_directory)
                ):
                    raise ValueError(
                        "E_ADOPTION_RECOVERY_REQUIRED: journal directory changed"
                    )
                with os.scandir(directory_fd) as entries:
                    names = [entry.name for entry in entries]
                if len(names) != 1:
                    raise ValueError(
                        "E_ADOPTION_RECOVERY_REQUIRED: journal temporary is not exact"
                    )
                name = names[0]
                if _PROVISIONING_TEMP.fullmatch(name) is None:
                    raise ValueError(
                        "E_ADOPTION_RECOVERY_REQUIRED: journal temporary name changed"
                    )
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                )
                descriptor = os.open(name, flags, dir_fd=directory_fd)
                opened = os.fstat(descriptor)
                named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or opened.st_uid != os.geteuid()
                    or stat.S_IMODE(opened.st_mode) != 0o600
                    or not 0 <= opened.st_size <= JOURNAL_MAX
                    or bool(int(getattr(opened, "st_flags", 0)) & 0x40000000)
                    or not stat.S_ISREG(named.st_mode)
                    or metadata_identity(opened) != metadata_identity(named)
                ):
                    raise ValueError(
                        "E_ADOPTION_RECOVERY_REQUIRED: journal temporary changed"
                    )
                os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
                if fault is not None:
                    fault("provisioning_temp_removed")
                continue
            if current == "P4":
                directory_fd = os.open("locks", _directory_flags(), dir_fd=state_fd)
                opened_directory = os.fstat(directory_fd)
                named_directory = os.stat(
                    "locks",
                    dir_fd=state_fd,
                    follow_symlinks=False,
                )
                if (
                    not _private_directory(opened_directory, exact_mode=0o700)
                    or metadata_identity(opened_directory)
                    != metadata_identity(named_directory)
                ):
                    raise ValueError(
                        "E_ADOPTION_VERIFICATION: orphan verifier directory changed"
                    )
                flags = (
                    os.O_RDWR
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                descriptor = os.open(
                    "verification.lock",
                    flags,
                    dir_fd=directory_fd,
                )
                if not _private_lock(os.fstat(descriptor)):
                    raise ValueError(
                        "E_ADOPTION_VERIFICATION: orphan verifier lock is unsafe"
                    )
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ValueError(
                        "E_ADOPTION_VERIFICATION_BUSY: orphan verifier mutex is held"
                    ) from error
                opened = os.fstat(descriptor)
                named = os.stat(
                    "verification.lock",
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                if (
                    not _private_lock(named)
                    or metadata_identity(opened) != metadata_identity(named)
                ):
                    raise ValueError(
                        "E_ADOPTION_VERIFICATION: orphan verifier lock changed"
                    )
                os.unlink("verification.lock", dir_fd=directory_fd)
                os.fsync(directory_fd)
                if fault is not None:
                    fault("provisioning_verification_removed")
                continue
            if current == "P3":
                _quarantine_empty_provisioning_directory(
                    adoption_lock,
                    "locks",
                    _PROVISIONING_LOCKS_QUARANTINE,
                    fault=fault,
                    quarantined_boundary="provisioning_locks_quarantined",
                )
                if fault is not None:
                    fault("provisioning_locks_removed")
                continue
            if current == "P3Q":
                _remove_empty_provisioning_quarantine(
                    adoption_lock,
                    _PROVISIONING_LOCKS_QUARANTINE,
                )
                if fault is not None:
                    fault("provisioning_locks_removed")
                continue
            if current == "P2":
                _quarantine_empty_provisioning_directory(
                    adoption_lock,
                    ADOPTION_DIRECTORY,
                    _PROVISIONING_ADOPTION_QUARANTINE,
                    fault=fault,
                    quarantined_boundary="provisioning_adoption_quarantined",
                )
                if fault is not None:
                    fault("provisioning_adoption_removed")
                continue
            if current == "P2Q":
                _remove_empty_provisioning_quarantine(
                    adoption_lock,
                    _PROVISIONING_ADOPTION_QUARANTINE,
                )
                if fault is not None:
                    fault("provisioning_adoption_removed")
                continue
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning state cannot be reset"
            )
        except ValueError:
            raise
        except OSError as error:
            raise ValueError(
                "E_ADOPTION_RECOVERY_REQUIRED: provisioning state cannot be reset"
            ) from error
        finally:
            if descriptor is not None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)
            if directory_fd is not None:
                os.close(directory_fd)


def apply_plan(
    source: Path,
    target: Path,
    plan: Mapping[str, object],
    *,
    expected_plan_digest: object,
    fault: FaultHook | None = None,
) -> dict[str, object]:
    issues = validate_plan(plan, expected_digest=expected_plan_digest)
    if issues:
        raise ValueError(f"E_ADOPTION_PLAN: reviewed plan is invalid ({issues[0].code})")
    source_root = canonical_root(source)
    target_root = canonical_root(target)
    _local_git_config_identity(target_root)
    common = _canonical_git_directory(target_root, "rev-parse", "--git-common-dir")
    initial_provisioning_state = _provisioning_state(common)
    state_exists = initial_provisioning_state != "ABSENT"
    provisioning_recovery = initial_provisioning_state in (
        PROVISIONING_PREFIXES | {"ROOT_EMPTY"}
    )
    if initial_provisioning_state == "ABSENT":
        _validate_plan_binding(
            source_root,
            target_root,
            plan,
            adoption_lock_held=False,
        )

    create_lock = initial_provisioning_state in {"ABSENT", "ROOT_EMPTY"}
    with _adoption_lock(common, create=create_lock) as adoption_lock:
        existing = _read_journal(common)
        if existing is not None:
            adoption_lock.preserve_state()
            _assert_lifecycle_binding(adoption_lock, existing)
            with _verification_guard(
                common,
                create=False,
                expected=existing["verification_lock"],
                expected_state_identity=_directory_identity(
                    os.fstat(adoption_lock.state_fd)
                ),
            ):
                receipt = _replay(common, plan, existing)
            _assert_lifecycle_binding(adoption_lock, receipt)
            return receipt
        receipts = _receipt_inventory(common)
        if receipts:
            adoption_lock.preserve_state()
            for receipt in receipts:
                _assert_lifecycle_binding(adoption_lock, receipt)
            raise ValueError("E_ADOPTION_REPLAY: target has terminal adoption evidence")
        current_provisioning_state = _provisioning_state(common)
        if state_exists and (
            not provisioning_recovery
            or current_provisioning_state not in PROVISIONING_PREFIXES
        ):
            if not adoption_lock.created_lock:
                adoption_lock.preserve_state()
            raise ValueError("E_ADOPTION_RECOVERY_REQUIRED: orphan adoption lock is unbound")
        if provisioning_recovery and initial_provisioning_state != "ABSENT":
            _validate_plan_binding(
                source_root,
                target_root,
                plan,
                adoption_lock_held=True,
                provisioning_recovery=True,
            )
            _reset_exact_provisioning_state(adoption_lock, fault=fault)

        projection = _validate_plan_binding(
            source_root,
            target_root,
            plan,
            adoption_lock_held=True,
        )
        install_digest = _install_digest(plan)
        if target_root.lstat().st_dev != common.lstat().st_dev:
            raise ValueError("E_ADOPTION_FILESYSTEM: source and destination are not on one filesystem")
        created_directories = _planned_directories(
            target_root,
            plan["managed_records"],
            target_binding=_target_binding(plan),
            before_snapshot_digest=plan["before_snapshot_digest"],
            managed_parent_directories=plan["target"]["managed_parent_directories"],
            managed_repository_scan=plan["target"]["managed_repository_scan"],
        )
        adoption_directory = adoption_lock.ensure_adoption_directory()
        with _verification_guard(
            common,
            create=True,
            expected_state_identity=_directory_identity(
                os.fstat(adoption_lock.state_fd)
            ),
        ) as verification_lock:
            adoption_lock.preserve_state()
            unsigned = _journal_unsigned(
                plan,
                install_digest,
                created_directories,
                adoption_lock.lifecycle_lock,
                verification_lock,
            )
            journal = _sealed_journal(unsigned)
            _write_journal(adoption_directory, journal)
        if fault is not None:
            fault("prepared")
        adoption_lock.assert_current(journal["lifecycle_lock"])
        _assert_preexisting_managed_parents(
            target_root,
            journal["managed_parent_directories"],
            code="E_ADOPTION_TARGET_DRIFT",
        )
        if _assert_no_nested_repositories(target_root) != journal["managed_repository_scan"]:
            raise ValueError("E_ADOPTION_TARGET_DRIFT: managed repository scan changed")

        def persist_created_directories(
            observed: list[dict[str, object]],
        ) -> None:
            nonlocal journal
            journal = _transition_journal(
                journal,
                created_directories=observed,
            )
            _write_journal(adoption_directory, journal)

        _create_target_directories(
            target_root,
            adoption_directory,
            install_digest,
            created_directories,
            target_binding=journal["target_binding"],
            managed_parent_directories=journal["managed_parent_directories"],
            on_created=persist_created_directories,
        )
        staging, staged = _stage_projection(adoption_directory, install_digest, projection)
        journal = _transition_journal(
            journal,
            state="staged",
        )
        _write_journal(adoption_directory, journal)
        if fault is not None:
            fault("staged")

        published: list[Mapping[str, object]] = []
        for record in projection.records:
            if record["path"] == ".codex/control-plane.lock":
                continue
            _publish_staged_record(
                target_root,
                staging,
                staged[str(record["path"])],
                record,
                target_binding=journal["target_binding"],
                managed_parent_directories=journal["managed_parent_directories"],
                created_directories=journal["created_directories"],
            )
            published.append(dict(record))
            journal = _transition_journal(
                journal,
                published_records=published,
            )
            _write_journal(adoption_directory, journal)
        journal = _transition_journal(journal, state="published_inactive")
        _write_journal(adoption_directory, journal)
        if fault is not None:
            fault("published_inactive")

        _set_hooks_path(target_root)
        if fault is not None:
            fault("hooks_configured")

        lock_record = next(
            record
            for record in projection.records
            if record["path"] == ".codex/control-plane.lock"
        )
        _publish_staged_record(
            target_root,
            staging,
            staged[".codex/control-plane.lock"],
            lock_record,
            target_binding=journal["target_binding"],
            managed_parent_directories=journal["managed_parent_directories"],
            created_directories=journal["created_directories"],
        )
        _remove_empty_staging(staging)
        if fault is not None:
            fault("activation_published")

        journal = _transition_journal(journal, state="active")
        _write_journal(adoption_directory, journal)
        if fault is not None:
            fault("active")

        try:
            with _verification_guard(
                common,
                create=False,
                expected=journal["verification_lock"],
                expected_state_identity=_directory_identity(
                    os.fstat(adoption_lock.state_fd)
                ),
            ):
                _verify_active(target_root, journal)
        except (OSError, ValueError, RecursionError) as error:
            raise ValueError(
                "E_ADOPTION_VERIFY_DRIFT: active generation verification failed"
            ) from error

        adoption_lock.assert_current(journal["lifecycle_lock"])
        receipt = _receipt(plan, install_digest, journal["lifecycle_lock"])
        evidence = _ensure_evidence_directory(adoption_lock)
        _atomic_write(
            evidence,
            _receipt_name(install_digest),
            (canonical_json(receipt) + "\n").encode("utf-8"),
        )
        adoption_lock.assert_current(receipt["lifecycle_lock"])
        return receipt


def _status_projection(
    *,
    state: str,
    install_digest: str | None,
    verification: str,
    result: str,
    error_codes: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "CoreAdoptionStatusV1",
        "state": state,
        "product_version": PRODUCT_VERSION,
        "tool_version": TOOL_VERSION,
        "install_digest": install_digest,
        "verification": verification,
        "result": result,
        "error_codes": error_codes,
        "authorizes": False,
    }


def _error_code(error: BaseException, fallback: str) -> str:
    value = str(error).split(":", 1)[0]
    if re.fullmatch(r"E_[A-Z0-9_]{1,62}", value):
        return value
    return fallback


def _receipt_inventory(common: Path) -> list[dict[str, object]]:
    relative = f"{STATE_ROOT}/{ADOPTION_DIRECTORY}/{EVIDENCE_DIRECTORY}"
    if confined_lstat(common, relative) is None:
        return []
    directory = canonical_root(common / relative)
    names: list[str] = []
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > 16 or not entry.is_file(follow_symlinks=False):
                    raise ValueError("E_ADOPTION_RECEIPT: evidence inventory is unsafe")
                if not re.fullmatch(r"[0-9a-f]{64}\.json", entry.name):
                    raise ValueError("E_ADOPTION_RECEIPT: evidence inventory is invalid")
                names.append(entry.name)
    except OSError as error:
        raise ValueError("E_ADOPTION_RECEIPT: evidence inventory is unavailable") from error
    receipts: list[dict[str, object]] = []
    for name in sorted(names):
        digest = f"sha256:{name[:-5]}"
        receipt = _read_receipt(common, digest)
        if receipt is None:
            raise ValueError("E_ADOPTION_RECEIPT: evidence disappeared")
        receipts.append(receipt)
    return receipts


def status(target: Path) -> dict[str, object]:
    try:
        target_root = canonical_root(target)
        common = _canonical_git_directory(target_root, "rev-parse", "--git-common-dir")
        if confined_lstat(common, STATE_ROOT) is None:
            return _status_projection(
                state="UNKNOWN",
                install_digest=None,
                verification="UNKNOWN",
                result="UNKNOWN",
                error_codes=["E_ADOPTION_NOT_FOUND"],
            )
        with _adoption_lock(common, create=False) as adoption_lock:
            adoption_lock.preserve_state()
            journal = _read_journal(common)
            if journal is None:
                receipts = _receipt_inventory(common)
                if len(receipts) == 1 and receipts[0].get("operation") == "rollback":
                    _assert_lifecycle_binding(adoption_lock, receipts[0])
                    return _status_projection(
                        state="ROLLED_BACK",
                        install_digest=str(receipts[0]["install_digest"]),
                        verification="UNKNOWN",
                        result="UNKNOWN",
                        error_codes=["E_ADOPTION_VERIFICATION_REQUIRED"],
                    )
                return _status_projection(
                    state="UNKNOWN",
                    install_digest=None,
                    verification="UNKNOWN",
                    result="UNKNOWN",
                    error_codes=["E_ADOPTION_NOT_FOUND"],
                )
            _assert_lifecycle_binding(adoption_lock, journal)
            state_value = str(journal["state"])
            install_digest = str(journal["install_digest"])
            if state_value == "active":
                receipt = _read_receipt(common, install_digest)
                if receipt is not None and receipt.get("operation") == "apply":
                    _assert_lifecycle_binding(adoption_lock, receipt)
                    return _status_projection(
                        state="ACTIVE",
                        install_digest=install_digest,
                        verification="UNKNOWN",
                        result="PASS",
                        error_codes=[],
                    )
            return _status_projection(
                state="UNKNOWN",
                install_digest=install_digest,
                verification="UNKNOWN",
                result="UNKNOWN",
                error_codes=["E_ADOPTION_RECOVERY_REQUIRED"],
            )
    except (OSError, ValueError, RecursionError) as error:
        return _status_projection(
            state="UNKNOWN",
            install_digest=None,
            verification="UNKNOWN",
            result="UNKNOWN",
            error_codes=[_error_code(error, "E_ADOPTION_STATUS")],
        )


def _bound_records(journal: Mapping[str, object]) -> list[dict[str, object]]:
    raw = journal.get("rollback_records")
    if not isinstance(raw, list) or not raw:
        raise ValueError("E_ADOPTION_JOURNAL: rollback bindings are absent")
    expected = {"path", "role", "sha256", "git_mode", "size_bytes", "before"}
    records: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != expected or item.get("before") != "absent":
            raise ValueError("E_ADOPTION_JOURNAL: rollback binding is invalid")
        record = {key: item[key] for key in expected if key != "before"}
        _safe_parts(str(record["path"]))
        if (
            record["git_mode"] not in {"100644", "100755"}
            or not isinstance(record["size_bytes"], int)
            or isinstance(record["size_bytes"], bool)
            or not 0 <= record["size_bytes"] <= FILE_MAX
            or not isinstance(record["sha256"], str)
            or _DIGEST.fullmatch(record["sha256"]) is None
        ):
            raise ValueError("E_ADOPTION_JOURNAL: rollback record is invalid")
        records.append(record)
    if [str(item["path"]) for item in records] != sorted(str(item["path"]) for item in records):
        raise ValueError("E_ADOPTION_JOURNAL: rollback records are not sorted")
    return records


def _target_identity(target: Path, journal: Mapping[str, object]) -> tuple[Path, Path]:
    binding = journal.get("target_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("E_ADOPTION_TARGET_DRIFT: target binding is absent")
    root = canonical_root(target)
    _assert_single_worktree(root)
    git_directory = _canonical_git_directory(root, "rev-parse", "--absolute-git-dir")
    common = _canonical_git_directory(root, "rev-parse", "--git-common-dir")
    branch = _text(
        _run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD"),
        code="E_ADOPTION_TARGET_DRIFT",
    )
    head = _text(
        _run_git(root, "rev-parse", "--verify", "HEAD^{commit}"),
        code="E_ADOPTION_TARGET_DRIFT",
    )
    observed = {
        "repository_id": [int(root.lstat().st_dev), int(root.lstat().st_ino)],
        "common_dir_id": [int(common.lstat().st_dev), int(common.lstat().st_ino)],
        "worktree_id": [int(git_directory.lstat().st_dev), int(git_directory.lstat().st_ino)],
        "branch": branch,
        "head": head,
        "policy_digest": f"sha256:{sha256(read_confined_file(root, '.codex/project-policy.toml', maximum=FILE_MAX)).hexdigest()}",
        "registry_digest": f"sha256:{sha256(read_confined_file(root, '.codex/resource-registry.toml', maximum=FILE_MAX)).hexdigest()}",
        "adoption_lifecycle": ADOPTION_LIFECYCLE,
    }
    if observed != dict(binding):
        raise ValueError("E_ADOPTION_TARGET_DRIFT: target identity changed")
    return git_directory, common


def _record_map(records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    result = {str(record["path"]): record for record in records}
    if len(result) != len(records):
        raise ValueError("E_ADOPTION_JOURNAL: duplicate managed record")
    return result


def _verify_managed_inventory(
    target: Path,
    records: list[dict[str, object]],
    created_directories: object,
    *,
    allow_missing: bool,
) -> None:
    if not isinstance(created_directories, list):
        raise ValueError("E_ADOPTION_JOURNAL: created directories are invalid")
    record_paths = {str(record["path"]) for record in records}
    directory_paths = {
        str(item.get("path"))
        for item in created_directories
        if isinstance(item, Mapping)
    }
    for item in created_directories:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"path", "mode", "identity"}
            or not isinstance(item.get("path"), str)
            or item.get("mode") != 0o755
        ):
            raise ValueError("E_ADOPTION_JOURNAL: created directory binding is invalid")
        relative = str(item["path"])
        metadata = confined_lstat(target, relative)
        if metadata is None:
            if allow_missing:
                continue
            raise ValueError("E_ADOPTION_VERIFY_DRIFT: managed directory is missing")
        if not _private_directory(metadata, exact_mode=0o755):
            raise ValueError("E_ADOPTION_VERIFY_DRIFT: managed directory mode drifted")
        identity = item.get("identity")
        if (
            identity is not None
            and identity != [int(metadata.st_dev), int(metadata.st_ino)]
        ):
            raise ValueError("E_ADOPTION_VERIFY_DRIFT: managed directory identity drifted")
        path = canonical_root(target / relative)
        try:
            with os.scandir(path) as entries:
                observed = sorted(entry.name for entry in entries)
        except OSError as error:
            raise ValueError("E_ADOPTION_VERIFY_DRIFT: managed inventory is unavailable") from error
        expected: set[str] = set()
        prefix = f"{relative}/"
        for candidate in (*record_paths, *directory_paths):
            if candidate.startswith(prefix):
                remainder = candidate[len(prefix):]
                if "/" not in remainder:
                    expected.add(remainder)
        if allow_missing:
            if any(name not in expected for name in observed):
                raise ValueError("E_ADOPTION_VERIFY_DRIFT: unexpected managed entry")
        elif observed != sorted(expected):
            raise ValueError("E_ADOPTION_VERIFY_DRIFT: managed inventory drifted")


def _target_lock_contract(
    target: Path,
    records: list[dict[str, object]],
) -> str:
    payload = read_confined_file(target, ".codex/control-plane.lock", maximum=FILE_MAX)
    try:
        lock = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("E_ADOPTION_VERIFY_DRIFT: target lock is invalid") from error
    if not _canonical_lock_contract(lock, adopted=True):
        raise ValueError("E_ADOPTION_VERIFY_DRIFT: target lock contract drifted")
    digests = lock["digests"]
    by_path = _record_map(records)
    bindings = {
        "hooks": ".codex/hooks.json",
        "hook_entrypoint": ".codex/hooks/control_plane_hook.py",
        "git_pre_commit": ".codex/git-hooks/pre-commit",
        "git_pre_push": ".codex/git-hooks/pre-push",
        "entrypoint": "scripts/control-plane",
    }
    for key, relative in bindings.items():
        if digests.get(key) != by_path[relative]["sha256"]:
            raise ValueError("E_ADOPTION_VERIFY_DRIFT: target lock file binding drifted")
    policy = read_confined_file(target, ".codex/project-policy.toml", maximum=FILE_MAX)
    registry = read_confined_file(target, ".codex/resource-registry.toml", maximum=FILE_MAX)
    if (
        digests.get("project_policy") != f"sha256:{sha256(policy).hexdigest()}"
        or digests.get("resource_registry") != f"sha256:{sha256(registry).hexdigest()}"
    ):
        raise ValueError("E_ADOPTION_VERIFY_DRIFT: target authority binding drifted")
    hasher = sha256()
    for name in CORE_RUNTIME_MODULES:
        module = read_confined_file(target, f"control_plane/{name}", maximum=FILE_MAX)
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(module)
        hasher.update(b"\0")
    runtime_digest = f"sha256:{hasher.hexdigest()}"
    if digests.get("runtime") != runtime_digest:
        raise ValueError("E_ADOPTION_VERIFY_DRIFT: target runtime binding drifted")
    return runtime_digest


def _hooks_path(target: Path) -> bytes:
    return _run_git(
        target,
        "config",
        "--local",
        "--get-all",
        "core.hooksPath",
        allowed_returncodes=(0, 1),
    )


def _verify_active(target: Path, journal: Mapping[str, object]) -> str:
    if journal.get("state") != "active":
        raise ValueError("E_ADOPTION_VERIFY_STATE: adoption is not active")
    _target_identity(target, journal)
    _assert_preexisting_managed_parents(
        target,
        journal.get("managed_parent_directories"),
        code="E_ADOPTION_VERIFY_DRIFT",
    )
    if _assert_no_nested_repositories(target) != journal.get("managed_repository_scan"):
        raise ValueError("E_ADOPTION_VERIFY_DRIFT: managed repository scan changed")
    records = _bound_records(journal)
    by_path = _record_map(records)
    published = journal.get("published_records")
    expected_published = [
        record for record in records if record["path"] != ".codex/control-plane.lock"
    ]
    if published != expected_published or journal.get("target_lock_record") != by_path[".codex/control-plane.lock"]:
        raise ValueError("E_ADOPTION_VERIFY_DRIFT: journal publication binding drifted")
    for record in records:
        _verify_installed_record(target, record)
    _verify_managed_inventory(
        target,
        records,
        journal.get("created_directories"),
        allow_missing=False,
    )
    runtime_digest = _target_lock_contract(target, records)
    if _hooks_path(target) != f"{HOOKS_PATH}\n".encode("utf-8"):
        raise ValueError("E_ADOPTION_VERIFY_DRIFT: core.hooksPath drifted")
    return runtime_digest


def verify(target: Path) -> dict[str, object]:
    install_digest: str | None = None
    try:
        target_root = canonical_root(target)
        common = _canonical_git_directory(target_root, "rev-parse", "--git-common-dir")
        if confined_lstat(common, STATE_ROOT) is None:
            raise ValueError("E_ADOPTION_NOT_FOUND: adoption state is absent")
        with _adoption_lock(common, create=False) as adoption_lock:
            adoption_lock.preserve_state()
            journal = _read_journal(common)
            if journal is None:
                raise ValueError("E_ADOPTION_NOT_FOUND: adoption journal is absent")
            _assert_lifecycle_binding(adoption_lock, journal)
            install_digest = str(journal["install_digest"])
            with _verification_guard(
                common,
                create=False,
                expected=journal["verification_lock"],
                expected_state_identity=_directory_identity(
                    os.fstat(adoption_lock.state_fd)
                ),
            ):
                _verify_active(target_root, journal)
            adoption_lock.assert_current(journal["lifecycle_lock"])
        return _status_projection(
            state="ACTIVE",
            install_digest=install_digest,
            verification="PASS",
            result="PASS",
            error_codes=[],
        )
    except (OSError, ValueError, RecursionError) as error:
        code = _error_code(error, "E_ADOPTION_VERIFY")
        if code not in {"E_ADOPTION_NOT_FOUND", "E_ADOPTION_VERIFY_STATE"}:
            code = "E_ADOPTION_VERIFY_DRIFT"
        return _status_projection(
            state="UNKNOWN",
            install_digest=install_digest,
            verification="FAIL",
            result="FAIL",
            error_codes=[code],
        )


def _closed_json_files(
    root: Path,
    relative: str,
    *,
    maximum_entries: int = 2048,
) -> list[tuple[str, dict[str, object]]]:
    if confined_lstat(root, relative) is None:
        return []
    directory = canonical_root(root / relative)
    names: list[str] = []
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > maximum_entries:
                    raise ValueError("E_ADOPTION_STATE_ACTIVE: state inventory exceeds its bound")
                if (
                    not entry.name.endswith(".json")
                    or not entry.is_file(follow_symlinks=False)
                ):
                    raise ValueError("E_ADOPTION_STATE_ACTIVE: state inventory is unsafe")
                names.append(entry.name)
    except OSError as error:
        raise ValueError("E_ADOPTION_STATE_ACTIVE: state inventory is unavailable") from error
    values: list[tuple[str, dict[str, object]]] = []
    for name in sorted(names):
        payload = read_confined_file(root, f"{relative}/{name}", maximum=65_536)
        values.append((name, load_closed_json(payload, limit=65_536)))
    return values


def _assert_quiescent(target: Path, git_directory: Path, common: Path) -> None:
    if confined_lstat(git_directory, "codex-control-plane") is not None:
        raise ValueError("E_ADOPTION_LEGACY_STATE: legacy state blocks rollback")
    if common != git_directory and confined_lstat(common, "codex-control-plane") is not None:
        raise ValueError("E_ADOPTION_LEGACY_STATE: legacy state blocks rollback")
    tasks = _closed_json_files(git_directory, f"{STATE_ROOT}/tasks")
    try:
        for name, task in tasks:
            validate_closed_core_task(
                task,
                expected_task_id=name.removesuffix(".json"),
                expected_repository=str(target),
            )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "E_ADOPTION_TASK_ACTIVE: an active or unobservable task blocks rollback"
        ) from error
    leases = _closed_json_files(common, f"{STATE_ROOT}/leases")
    if leases:
        raise ValueError("E_ADOPTION_LEASE_ACTIVE: an active lease blocks rollback")


@contextmanager
def _verification_guard(
    common: Path,
    *,
    create: bool,
    expected: Mapping[str, object] | None = None,
    expected_state_identity: tuple[int, ...] | None = None,
) -> Iterator[dict[str, object]]:
    if create == (expected is not None):
        raise ValueError("E_ADOPTION_VERIFICATION: verifier provisioning mode is invalid")
    root = canonical_root(common)
    parent = canonical_root(root.parent)
    parent_fd = _open_private_directory(parent)
    common_fd: int | None = None
    state_fd: int | None = None
    locks_fd: int | None = None
    descriptor: int | None = None
    created_directory = False
    created_file = False
    durable = False
    try:
        try:
            common_fd = os.open(root.name, _directory_flags(), dir_fd=parent_fd)
            opened_common = os.fstat(common_fd)
        except OSError as error:
            raise ValueError(
                "E_ADOPTION_VERIFICATION: Git common directory is unavailable"
            ) from error
        if (
            not _private_directory(opened_common)
            or _directory_identity(opened_common) != _directory_identity(root.lstat())
        ):
            raise ValueError(
                "E_ADOPTION_VERIFICATION: Git common directory is unsafe"
            )
        try:
            state_fd = os.open(STATE_ROOT, _directory_flags(), dir_fd=common_fd)
            opened_state = os.fstat(state_fd)
        except OSError as error:
            raise ValueError(
                "E_ADOPTION_VERIFICATION: verifier state directory is unavailable"
            ) from error
        if (
            not _private_directory(opened_state, exact_mode=0o700)
            or (
                expected_state_identity is not None
                and _directory_identity(opened_state) != expected_state_identity
            )
        ):
            raise ValueError(
                "E_ADOPTION_VERIFICATION: verifier state directory is unsafe"
            )
        if create:
            try:
                os.mkdir("locks", 0o700, dir_fd=state_fd)
                os.fsync(state_fd)
                created_directory = True
            except FileExistsError:
                raise ValueError(
                    "E_ADOPTION_VERIFICATION: verifier lock directory already exists"
                ) from None
            except OSError as error:
                raise ValueError(
                    "E_ADOPTION_VERIFICATION: verifier lock directory is unsafe"
                ) from error
        try:
            locks_fd = os.open("locks", _directory_flags(), dir_fd=state_fd)
        except OSError as error:
            raise ValueError(
                "E_ADOPTION_VERIFICATION: verifier lock directory is unavailable"
            ) from error
        if not _private_directory(os.fstat(locks_fd), exact_mode=0o700):
            raise ValueError("E_ADOPTION_VERIFICATION: verifier lock directory is unsafe")
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            if create:
                descriptor = os.open(
                    "verification.lock",
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=locks_fd,
                )
                created_file = True
                os.fsync(locks_fd)
            else:
                before = os.stat(
                    "verification.lock",
                    dir_fd=locks_fd,
                    follow_symlinks=False,
                )
                descriptor = os.open("verification.lock", flags, dir_fd=locks_fd)
                if metadata_identity(before) != metadata_identity(os.fstat(descriptor)):
                    raise ValueError(
                        "E_ADOPTION_VERIFICATION: verifier lock identity changed"
                    )
        except ValueError:
            raise
        except OSError as error:
            raise ValueError("E_ADOPTION_VERIFICATION: verifier lock is unavailable") from error
        if descriptor is None or not _private_lock(os.fstat(descriptor)):
            raise ValueError("E_ADOPTION_VERIFICATION: verifier lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("E_ADOPTION_VERIFICATION_BUSY: verifier mutex is held") from error
        try:
            opened_common = os.fstat(common_fd)
            named_common = os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
            path_common = root.lstat()
            opened_state = os.fstat(state_fd)
            named_state = os.stat(STATE_ROOT, dir_fd=common_fd, follow_symlinks=False)
            opened_directory = os.fstat(locks_fd)
            named_directory = os.stat("locks", dir_fd=state_fd, follow_symlinks=False)
            opened_lock = os.fstat(descriptor)
            named_lock = os.stat(
                "verification.lock",
                dir_fd=locks_fd,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError("E_ADOPTION_VERIFICATION: verifier lock identity changed") from error
        if (
            not _private_directory(opened_common)
            or not _private_directory(named_common)
            or _directory_identity(opened_common) != _directory_identity(named_common)
            or _directory_identity(opened_common) != _directory_identity(path_common)
            or not _private_directory(opened_state, exact_mode=0o700)
            or not _private_directory(named_state, exact_mode=0o700)
            or _directory_identity(opened_state) != _directory_identity(named_state)
            or (
                expected_state_identity is not None
                and _directory_identity(opened_state) != expected_state_identity
            )
            or not _private_directory(opened_directory, exact_mode=0o700)
            or not _private_directory(named_directory, exact_mode=0o700)
            or metadata_identity(opened_directory) != metadata_identity(named_directory)
            or not _private_lock(opened_lock)
            or not _private_lock(named_lock)
            or metadata_identity(opened_lock) != metadata_identity(named_lock)
        ):
            raise ValueError("E_ADOPTION_VERIFICATION: verifier lock identity changed")
        current = {
            "directory": _verification_directory_record(opened_directory),
            "file": _verification_lock_record(opened_lock),
        }
        if expected is not None and current != dict(expected):
            raise ValueError("E_ADOPTION_VERIFICATION: verifier lock binding changed")
        durable = True
        yield current
    finally:
        if create and not durable and locks_fd is not None:
            if created_file and descriptor is not None:
                try:
                    opened = os.fstat(descriptor)
                    named = os.stat(
                        "verification.lock",
                        dir_fd=locks_fd,
                        follow_symlinks=False,
                    )
                    if metadata_identity(opened) == metadata_identity(named):
                        os.unlink("verification.lock", dir_fd=locks_fd)
                        os.fsync(locks_fd)
                except OSError:
                    pass
            if created_directory and state_fd is not None:
                try:
                    opened = os.fstat(locks_fd)
                    named = os.stat("locks", dir_fd=state_fd, follow_symlinks=False)
                    if _directory_identity(opened) == _directory_identity(named):
                        os.rmdir("locks", dir_fd=state_fd)
                        os.fsync(state_fd)
                except OSError:
                    pass
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
        if locks_fd is not None:
            os.close(locks_fd)
        if state_fd is not None:
            os.close(state_fd)
        if common_fd is not None:
            os.close(common_fd)
        os.close(parent_fd)


def _staging_path(adoption_directory: Path, install_digest: str) -> Path:
    return adoption_directory / f".staging-{install_digest.removeprefix('sha256:')}"


def _directory_staging_path(adoption_directory: Path, install_digest: str) -> Path:
    return adoption_directory / _directory_staging_name(install_digest)


def _ensure_directory_staging_path(
    adoption_directory: Path,
    install_digest: str,
) -> Path:
    staging = _directory_staging_path(adoption_directory, install_digest)
    if confined_lstat(adoption_directory, staging.name) is not None:
        canonical_root(staging)
        return staging
    adoption_fd = _open_private_directory(adoption_directory, exact_mode=0o700)
    try:
        os.mkdir(staging.name, 0o700, dir_fd=adoption_fd)
        os.fsync(adoption_fd)
    except FileExistsError:
        pass
    except OSError as error:
        raise ValueError(
            "E_ADOPTION_ROLLBACK: directory quarantine cannot be created"
        ) from error
    finally:
        os.close(adoption_fd)
    canonical_root(staging)
    return staging


def _recovery_path(adoption_directory: Path, install_digest: str) -> Path:
    return adoption_directory / f".recovery-{install_digest.removeprefix('sha256:')}"


def _verify_staging(
    adoption_directory: Path,
    install_digest: str,
    records: list[dict[str, object]],
    *,
    target: Path | None = None,
) -> None:
    staging = _staging_path(adoption_directory, install_digest)
    if confined_lstat(adoption_directory, staging.name) is None:
        return
    canonical = canonical_root(staging)
    names: list[str] = []
    try:
        with os.scandir(canonical) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staging inventory is unsafe")
                names.append(entry.name)
    except OSError as error:
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staging inventory is unavailable") from error
    expected = {f"{index:04d}": record for index, record in enumerate(records)}
    if any(name not in expected for name in names):
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staging contains an unexpected entry")
    for name in names:
        record = expected[name]
        payload = read_confined_file(
            adoption_directory,
            f"{staging.name}/{name}",
            maximum=FILE_MAX,
        )
        metadata = confined_lstat(adoption_directory, f"{staging.name}/{name}")
        mode = 0o755 if record["git_mode"] == "100755" else 0o644
        if (
            metadata is None
            or stat.S_IMODE(metadata.st_mode) != mode
            or len(payload) != record["size_bytes"]
            or f"sha256:{sha256(payload).hexdigest()}" != record["sha256"]
        ):
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staging bytes drifted")
        if target is not None and confined_lstat(target, str(record["path"])) is not None:
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staged record is duplicated")


def _ensure_staging_path(
    adoption_directory: Path,
    install_digest: str,
) -> Path:
    staging = _staging_path(adoption_directory, install_digest)
    if confined_lstat(adoption_directory, staging.name) is not None:
        canonical_root(staging)
        return staging
    adoption_fd = _open_private_directory(adoption_directory, exact_mode=0o700)
    try:
        os.mkdir(staging.name, 0o700, dir_fd=adoption_fd)
        os.fsync(adoption_fd)
    except FileExistsError:
        pass
    except OSError as error:
        raise ValueError(
            "E_ADOPTION_ROLLBACK: removal staging cannot be created"
        ) from error
    finally:
        os.close(adoption_fd)
    canonical_root(staging)
    return staging


def _verify_staged_record(
    adoption_directory: Path,
    staging: Path,
    staged_name: str,
    record: Mapping[str, object],
) -> None:
    relative = f"{staging.name}/{staged_name}"
    payload = read_confined_file(adoption_directory, relative, maximum=FILE_MAX)
    metadata = confined_lstat(adoption_directory, relative)
    mode = 0o755 if record["git_mode"] == "100755" else 0o644
    if (
        metadata is None
        or stat.S_IMODE(metadata.st_mode) != mode
        or len(payload) != record["size_bytes"]
        or f"sha256:{sha256(payload).hexdigest()}" != record["sha256"]
    ):
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: quarantined record drifted")


def _verify_directory_staging(
    target: Path,
    adoption_directory: Path,
    install_digest: str,
    created: object,
) -> None:
    if not isinstance(created, list):
        raise ValueError("E_ADOPTION_JOURNAL: created directory bindings are invalid")
    staging = _directory_staging_path(adoption_directory, install_digest)
    if confined_lstat(adoption_directory, staging.name) is None:
        return
    staging_fd = _open_private_directory(staging, exact_mode=0o700)
    expected = {
        _directory_record_name(str(item["path"])): item
        for item in created
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    try:
        with os.scandir(staging) as entries:
            observed = sorted(entry.name for entry in entries)
        if any(name not in expected for name in observed):
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: directory staging has an unexpected entry")
        for name in observed:
            item = expected[name]
            metadata = os.stat(name, dir_fd=staging_fd, follow_symlinks=False)
            if not _private_directory(metadata, exact_mode=int(item["mode"])):
                raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staged directory is unsafe")
            identity = item.get("identity")
            if identity is not None and identity != [int(metadata.st_dev), int(metadata.st_ino)]:
                raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staged directory identity drifted")
            if confined_lstat(target, str(item["path"])) is not None:
                raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staged directory is duplicated")
            with os.scandir(staging / name) as children:
                if any(True for _ in children):
                    raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: staged directory is not empty")
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: directory staging is unavailable") from error
    finally:
        os.close(staging_fd)


def _verify_recovery_lock(
    adoption_directory: Path,
    install_digest: str,
    lock_record: Mapping[str, object],
    *,
    allow_empty: bool,
) -> bool:
    recovery = _recovery_path(adoption_directory, install_digest)
    if confined_lstat(adoption_directory, recovery.name) is None:
        return False
    canonical_root(recovery)
    relative = f"{recovery.name}/control-plane.lock"
    if confined_lstat(adoption_directory, relative) is None:
        try:
            with os.scandir(recovery) as entries:
                if any(True for _ in entries):
                    raise ValueError(
                        "E_ADOPTION_ROLLBACK_DRIFT: recovery inventory drifted"
                    )
        except ValueError:
            raise
        except OSError as error:
            raise ValueError(
                "E_ADOPTION_ROLLBACK_DRIFT: recovery inventory is unavailable"
            ) from error
        if allow_empty:
            return False
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: recovery lock is missing")
    payload = read_confined_file(adoption_directory, relative, maximum=FILE_MAX)
    metadata = confined_lstat(adoption_directory, relative)
    if (
        metadata is None
        or stat.S_IMODE(metadata.st_mode) != 0o644
        or len(payload) != lock_record["size_bytes"]
        or f"sha256:{sha256(payload).hexdigest()}" != lock_record["sha256"]
    ):
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: recovery lock drifted")
    return True


def _verify_rollback_state(
    target: Path,
    journal: Mapping[str, object],
    adoption_directory: Path,
) -> tuple[list[dict[str, object]], Path, Path]:
    git_directory, common = _target_identity(target, journal)
    _assert_preexisting_managed_parents(
        target,
        journal.get("managed_parent_directories"),
        code="E_ADOPTION_ROLLBACK_DRIFT",
    )
    if _assert_no_nested_repositories(target) != journal.get("managed_repository_scan"):
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: managed repository scan changed")
    records = _bound_records(journal)
    published = journal.get("published_records")
    if not isinstance(published, list) or any(item not in records for item in published):
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: journal publication set drifted")
    active_state = journal.get("state") == "active"
    for record in records:
        present = confined_lstat(target, str(record["path"])) is not None
        if active_state and not present:
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: active managed record is missing")
        if present:
            try:
                _verify_installed_record(target, record)
            except ValueError as error:
                raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: installed bytes drifted") from error
    try:
        _verify_managed_inventory(
            target,
            records,
            journal.get("created_directories"),
            allow_missing=not active_state,
        )
    except ValueError as error:
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: managed inventory drifted") from error
    hooks = _hooks_path(target)
    if hooks not in {b"", f"{HOOKS_PATH}\n".encode("utf-8")}:
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: core.hooksPath drifted")
    by_path = _record_map(records)
    lock_present = confined_lstat(target, ".codex/control-plane.lock") is not None
    recovery_present = _verify_recovery_lock(
        adoption_directory,
        str(journal["install_digest"]),
        by_path[".codex/control-plane.lock"],
        allow_empty=journal.get("state") == "rolling_back",
    )
    if lock_present and recovery_present:
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: activation lock is duplicated")
    _verify_staging(
        adoption_directory,
        str(journal["install_digest"]),
        records,
        target=target,
    )
    _verify_directory_staging(
        target,
        adoption_directory,
        str(journal["install_digest"]),
        journal.get("created_directories"),
    )
    _assert_quiescent(target, git_directory, common)
    return records, git_directory, common


def _move_activation_to_recovery(
    target: Path,
    adoption_directory: Path,
    install_digest: str,
    *,
    lock_record: Mapping[str, object],
    target_binding: Mapping[str, object],
    managed_parent_directories: object,
    created_directories: object,
) -> Path:
    recovery = _recovery_path(adoption_directory, install_digest)
    recovery_metadata = confined_lstat(adoption_directory, recovery.name)
    if recovery_metadata is None:
        adoption_fd = _open_private_directory(adoption_directory, exact_mode=0o700)
        try:
            os.mkdir(recovery.name, 0o700, dir_fd=adoption_fd)
            os.fsync(adoption_fd)
        except OSError as error:
            raise ValueError("E_ADOPTION_ROLLBACK: recovery directory cannot be created") from error
        finally:
            os.close(adoption_fd)
    else:
        canonical_root(recovery)
    lock = confined_lstat(target, ".codex/control-plane.lock")
    if lock is None:
        return recovery
    root_fd = _open_bound_target_root(target, target_binding)
    target_descriptors = _open_target_parent(
        root_fd,
        (".codex",),
        expected_directories=_managed_directory_identities(
            managed_parent_directories,
            created_directories,
        ),
    )
    target_fd = target_descriptors[-1]
    recovery_fd = _open_private_directory(recovery, exact_mode=0o700)
    moved = False
    try:
        _rename_noreplace(
            "control-plane.lock",
            "control-plane.lock",
            source_directory=target_fd,
            destination_directory=recovery_fd,
        )
        moved = True
        os.fsync(target_fd)
        os.fsync(recovery_fd)
        if not _verify_recovery_lock(
            adoption_directory,
            install_digest,
            lock_record,
            allow_empty=False,
        ):
            raise ValueError(
                "E_ADOPTION_ROLLBACK_DRIFT: deactivated lock is unavailable"
            )
    except (OSError, ValueError) as error:
        if moved:
            try:
                _rename_noreplace(
                    "control-plane.lock",
                    "control-plane.lock",
                    source_directory=recovery_fd,
                    destination_directory=target_fd,
                )
                os.fsync(recovery_fd)
                os.fsync(target_fd)
            except (OSError, ValueError):
                pass
        if isinstance(error, ValueError) and str(error).startswith(
            "E_ADOPTION_ROLLBACK_DRIFT"
        ):
            raise
        raise ValueError(
            "E_ADOPTION_ROLLBACK_DRIFT: activation changed during deactivation"
        ) from error
    finally:
        os.close(recovery_fd)
        for descriptor in reversed(target_descriptors):
            os.close(descriptor)
        os.close(root_fd)
    return recovery


def _unset_hooks_path(target: Path) -> None:
    _local_git_config_identity(target)
    current = _hooks_path(target)
    if not current:
        return
    if current != f"{HOOKS_PATH}\n".encode("utf-8"):
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: core.hooksPath drifted")
    try:
        _replace_local_git_config(
            target,
            expected_before=f"{HOOKS_PATH}\n".encode("utf-8"),
            expected_after=b"",
            mutation=(
                "--unset-all",
                "core.hooksPath",
                r"^\.codex/git-hooks$",
            ),
        )
    except ValueError as error:
        if _hooks_path(target) != f"{HOOKS_PATH}\n".encode("utf-8"):
            raise ValueError(
                "E_ADOPTION_ROLLBACK_DRIFT: core.hooksPath changed during restoration"
            ) from error
        raise ValueError("E_ADOPTION_ROLLBACK: core.hooksPath restoration failed") from error
    if _hooks_path(target):
        raise ValueError(
            "E_ADOPTION_ROLLBACK_DRIFT: core.hooksPath changed during restoration"
        )


def _unlink_target_file(
    target: Path,
    adoption_directory: Path,
    install_digest: str,
    staged_name: str,
    record: Mapping[str, object],
    *,
    target_binding: Mapping[str, object],
    managed_parent_directories: object,
    created_directories: object,
) -> None:
    relative = str(record["path"])
    staging = _staging_path(adoption_directory, install_digest)
    target_present = confined_lstat(target, relative) is not None
    staged_present = (
        confined_lstat(adoption_directory, f"{staging.name}/{staged_name}")
        is not None
    )
    if target_present and staged_present:
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: managed record is duplicated")
    if not target_present:
        if staged_present:
            _verify_staged_record(
                adoption_directory,
                staging,
                staged_name,
                record,
            )
        return
    try:
        _verify_installed_record(target, record)
    except ValueError as error:
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: installed record changed") from error
    parts = _safe_parts(relative)
    staging = _ensure_staging_path(adoption_directory, install_digest)
    root_fd = _open_bound_target_root(target, target_binding)
    descriptors: list[int] = []
    staging_fd: int | None = None
    moved = False
    try:
        descriptors = _open_target_parent(
            root_fd,
            parts[:-1],
            expected_directories=_managed_directory_identities(
                managed_parent_directories,
                created_directories,
            ),
        )
        staging_fd = _open_private_directory(staging, exact_mode=0o700)
        _rename_noreplace(
            parts[-1],
            staged_name,
            source_directory=descriptors[-1],
            destination_directory=staging_fd,
        )
        moved = True
        os.fsync(descriptors[-1])
        os.fsync(staging_fd)
        _verify_staged_record(
            adoption_directory,
            staging,
            staged_name,
            record,
        )
        if confined_lstat(target, relative) is not None:
            raise ValueError(
                "E_ADOPTION_ROLLBACK_DRIFT: managed destination reappeared"
            )
    except (OSError, ValueError) as error:
        if moved and staging_fd is not None:
            try:
                _rename_noreplace(
                    staged_name,
                    parts[-1],
                    source_directory=staging_fd,
                    destination_directory=descriptors[-1],
                )
                os.fsync(staging_fd)
                os.fsync(descriptors[-1])
            except (OSError, ValueError):
                pass
        if isinstance(error, ValueError) and str(error).startswith(
            "E_ADOPTION_ROLLBACK_DRIFT"
        ):
            raise
        raise ValueError(
            "E_ADOPTION_ROLLBACK_DRIFT: managed record changed during quarantine"
        ) from error
    finally:
        if staging_fd is not None:
            os.close(staging_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        os.close(root_fd)


def _cleanup_staging(
    target: Path,
    adoption_directory: Path,
    install_digest: str,
    records: list[dict[str, object]],
) -> None:
    staging = _staging_path(adoption_directory, install_digest)
    if confined_lstat(adoption_directory, staging.name) is None:
        return
    _verify_staging(
        adoption_directory,
        install_digest,
        records,
        target=target,
    )


def _cleanup_directory_staging(
    target: Path,
    adoption_directory: Path,
    install_digest: str,
    created: object,
) -> None:
    staging = _directory_staging_path(adoption_directory, install_digest)
    if confined_lstat(adoption_directory, staging.name) is None:
        return
    _verify_directory_staging(
        target,
        adoption_directory,
        install_digest,
        created,
    )
    staging_fd = _open_private_directory(staging, exact_mode=0o700)
    try:
        with os.scandir(staging) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            os.rmdir(name, dir_fd=staging_fd)
        os.fsync(staging_fd)
    except OSError as error:
        raise ValueError("E_ADOPTION_ROLLBACK: directory staging cannot be cleaned") from error
    finally:
        os.close(staging_fd)
    parent_fd = _open_private_directory(adoption_directory, exact_mode=0o700)
    try:
        os.rmdir(staging.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as error:
        raise ValueError("E_ADOPTION_ROLLBACK: directory staging cannot be removed") from error
    finally:
        os.close(parent_fd)


def _remove_created_directories(
    target: Path,
    adoption_directory: Path,
    install_digest: str,
    created: object,
    *,
    target_binding: Mapping[str, object],
    managed_parent_directories: object,
) -> None:
    if not isinstance(created, list):
        raise ValueError("E_ADOPTION_JOURNAL: created directory bindings are invalid")
    staging = _ensure_directory_staging_path(adoption_directory, install_digest)
    staging_fd = _open_private_directory(staging, exact_mode=0o700)
    root_fd = _open_bound_target_root(target, target_binding)
    try:
        ordered = sorted(
            created,
            key=lambda item: (str(item["path"]).count("/"), str(item["path"])),
            reverse=True,
        )
        for item in ordered:
            relative = str(item["path"])
            metadata = confined_lstat(target, relative)
            staged_name = _directory_record_name(relative)
            staged_metadata = confined_lstat(
                adoption_directory,
                f"{staging.name}/{staged_name}",
            )
            if metadata is not None and staged_metadata is not None:
                raise ValueError(
                    "E_ADOPTION_ROLLBACK_DRIFT: created directory is duplicated"
                )
            if metadata is None:
                if staged_metadata is not None:
                    identity = item.get("identity") if isinstance(item, Mapping) else None
                    if (
                        (
                            identity is not None
                            and identity
                            != [int(staged_metadata.st_dev), int(staged_metadata.st_ino)]
                        )
                        or not _private_directory(
                            staged_metadata,
                            exact_mode=int(item["mode"]),
                        )
                    ):
                        raise ValueError(
                            "E_ADOPTION_ROLLBACK_DRIFT: quarantined directory drifted"
                        )
                continue
            identity = item.get("identity") if isinstance(item, Mapping) else None
            if identity != [int(metadata.st_dev), int(metadata.st_ino)]:
                raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: created directory identity drifted")
            parts = _safe_parts(relative)
            descriptors = _open_target_parent(
                root_fd,
                parts[:-1],
                expected_directories=_managed_directory_identities(
                    managed_parent_directories,
                    created,
                ),
            )
            moved = False
            try:
                _rename_noreplace(
                    parts[-1],
                    staged_name,
                    source_directory=descriptors[-1],
                    destination_directory=staging_fd,
                )
                moved = True
                os.fsync(descriptors[-1])
                os.fsync(staging_fd)
                quarantined = os.stat(
                    staged_name,
                    dir_fd=staging_fd,
                    follow_symlinks=False,
                )
                if (
                    identity != [int(quarantined.st_dev), int(quarantined.st_ino)]
                    or not _private_directory(
                        quarantined,
                        exact_mode=int(item["mode"]),
                    )
                ):
                    raise ValueError(
                        "E_ADOPTION_ROLLBACK_DRIFT: quarantined directory drifted"
                    )
                with os.scandir(staging / staged_name) as children:
                    if any(True for _ in children):
                        raise ValueError(
                            "E_ADOPTION_ROLLBACK_DRIFT: quarantined directory is not empty"
                        )
                if confined_lstat(target, relative) is not None:
                    raise ValueError(
                        "E_ADOPTION_ROLLBACK_DRIFT: created directory reappeared"
                    )
            except (OSError, ValueError) as error:
                if moved:
                    try:
                        _rename_noreplace(
                            staged_name,
                            parts[-1],
                            source_directory=staging_fd,
                            destination_directory=descriptors[-1],
                        )
                        os.fsync(staging_fd)
                        os.fsync(descriptors[-1])
                    except (OSError, ValueError):
                        pass
                if isinstance(error, ValueError) and str(error).startswith(
                    "E_ADOPTION_ROLLBACK_DRIFT"
                ):
                    raise
                raise ValueError(
                    "E_ADOPTION_ROLLBACK_DRIFT: created directory changed during quarantine"
                ) from error
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
    finally:
        os.close(root_fd)
        os.close(staging_fd)


def _prove_rollback_snapshot(
    target: Path,
    journal: Mapping[str, object],
) -> str:
    try:
        _reject_content_filters(target)
        _target_identity(target, journal)
        _clean(target, code="E_ADOPTION_ROLLBACK_DRIFT")
        if _hooks_path(target):
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: core.hooksPath was not restored")
        if any(confined_lstat(target, relative) is not None for relative in MANAGED_PATHS):
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: managed surface remains")
        created = journal.get("created_directories")
        if not isinstance(created, list) or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("path"), str)
            or confined_lstat(target, str(item["path"])) is not None
            for item in created
        ):
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: managed directory remains")
        binding = journal.get("target_binding")
        if not isinstance(binding, Mapping):
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: target binding is absent")
        observed = target_surface_digest(
            binding,
            managed_parent_directories=_managed_parent_directories(target),
            managed_repository_scan=_assert_no_nested_repositories(target),
        )
        if observed != journal.get("before_snapshot_digest"):
            raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: restored snapshot differs")
        return observed
    except ValueError as error:
        if str(error).startswith("E_ADOPTION_ROLLBACK_DRIFT"):
            raise
        raise ValueError("E_ADOPTION_ROLLBACK_DRIFT: restored snapshot is unprovable") from error


def _rollback_receipt(
    journal: Mapping[str, object],
    *,
    after_snapshot_digest: str,
) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreAdoptionReceiptV1",
        "operation": "rollback",
        "plan_digest": journal["plan_digest"],
        "install_digest": journal["install_digest"],
        "before_snapshot_digest": journal["before_snapshot_digest"],
        "after_snapshot_digest": after_snapshot_digest,
        "result": "PASS",
        "error_codes": [],
        "lifecycle_lock": dict(journal["lifecycle_lock"]),
        "authorizes": False,
    }
    receipt = dict(unsigned)
    receipt["receipt_digest"] = contract_digest(unsigned)
    issues = validate_receipt(receipt)
    if issues:
        raise ValueError("E_ADOPTION_RECEIPT: rollback receipt is invalid")
    return receipt


def _unlink_journal(adoption_directory: Path) -> None:
    descriptor = _open_private_directory(adoption_directory, exact_mode=0o700)
    try:
        os.unlink(JOURNAL_NAME, dir_fd=descriptor)
        os.fsync(descriptor)
    except OSError as error:
        raise ValueError("E_ADOPTION_ROLLBACK: journal cannot be finalized") from error
    finally:
        os.close(descriptor)


def rollback(
    target: Path,
    *,
    install_digest: str,
    fault: FaultHook | None = None,
) -> dict[str, object]:
    if not isinstance(install_digest, str) or _DIGEST.fullmatch(install_digest) is None:
        raise ValueError("E_ADOPTION_REPLAY: install digest is invalid")
    target_root = canonical_root(target)
    _local_git_config_identity(target_root)
    common = _canonical_git_directory(target_root, "rev-parse", "--git-common-dir")
    if confined_lstat(common, STATE_ROOT) is None:
        raise ValueError("E_ADOPTION_NOT_FOUND: adoption state is absent")
    with _adoption_lock(common, create=False) as adoption_lock:
        adoption_lock.preserve_state()
        journal = _read_journal(common)
        if journal is None:
            receipt = _read_receipt(common, install_digest)
            if receipt is not None and receipt.get("operation") == "rollback":
                _assert_lifecycle_binding(adoption_lock, receipt)
                return receipt
            if _receipt_inventory(common):
                raise ValueError("E_ADOPTION_REPLAY: rollback binding differs")
            raise ValueError("E_ADOPTION_NOT_FOUND: adoption journal is absent")
        if journal.get("install_digest") != install_digest:
            raise ValueError("E_ADOPTION_REPLAY: rollback binding differs")
        _assert_lifecycle_binding(adoption_lock, journal)
        adoption_directory = adoption_lock.ensure_adoption_directory()
        with _verification_guard(
            common,
            create=False,
            expected=journal["verification_lock"],
            expected_state_identity=_directory_identity(
                os.fstat(adoption_lock.state_fd)
            ),
        ):
            records, git_directory, observed_common = _verify_rollback_state(
                target_root,
                journal,
                adoption_directory,
            )
            if observed_common != common:
                raise ValueError("E_ADOPTION_TARGET_DRIFT: common Git dir changed")
            journal = _transition_journal(journal, state="rolling_back")
            _write_journal(adoption_directory, journal)
            if fault is not None:
                fault("rolling_back")

            recovery = _move_activation_to_recovery(
                target_root,
                adoption_directory,
                install_digest,
                lock_record=next(
                    record
                    for record in records
                    if record["path"] == ".codex/control-plane.lock"
                ),
                target_binding=journal["target_binding"],
                managed_parent_directories=journal["managed_parent_directories"],
                created_directories=journal["created_directories"],
            )
            activation_record = next(
                record
                for record in records
                if record["path"] == ".codex/control-plane.lock"
            )
            activation_quarantined = _verify_recovery_lock(
                adoption_directory,
                install_digest,
                activation_record,
                allow_empty=True,
            )
            if fault is not None:
                fault("deactivated")
            _unset_hooks_path(target_root)
            if fault is not None:
                fault("config_restored")
            for index in range(len(records) - 1, -1, -1):
                record = records[index]
                if record["path"] != ".codex/control-plane.lock":
                    _unlink_target_file(
                        target_root,
                        adoption_directory,
                        install_digest,
                        f"{index:04d}",
                        record,
                        target_binding=journal["target_binding"],
                        managed_parent_directories=journal["managed_parent_directories"],
                        created_directories=journal["created_directories"],
                    )
            _remove_created_directories(
                target_root,
                adoption_directory,
                install_digest,
                journal["created_directories"],
                target_binding=journal["target_binding"],
                managed_parent_directories=journal["managed_parent_directories"],
            )
            _cleanup_staging(
                target_root,
                adoption_directory,
                install_digest,
                records,
            )
            _cleanup_directory_staging(
                target_root,
                adoption_directory,
                install_digest,
                journal["created_directories"],
            )
            if fault is not None:
                fault("records_removed")
            after_snapshot_digest = _prove_rollback_snapshot(target_root, journal)
            _verify_staging(
                adoption_directory,
                install_digest,
                records,
                target=target_root,
            )
            if not _verify_recovery_lock(
                adoption_directory,
                install_digest,
                activation_record,
                allow_empty=not activation_quarantined,
            ) and activation_quarantined:
                raise ValueError(
                    "E_ADOPTION_ROLLBACK_DRIFT: retained activation quarantine is unavailable"
                )

            journal = _transition_journal(journal, state="rolled_back")
            _write_journal(adoption_directory, journal)
            if fault is not None:
                fault("rolled_back")
            receipt = _rollback_receipt(
                journal,
                after_snapshot_digest=after_snapshot_digest,
            )
            evidence = _ensure_evidence_directory(adoption_lock)
            _atomic_write(
                evidence,
                _receipt_name(install_digest),
                (canonical_json(receipt) + "\n").encode("utf-8"),
            )
            adoption_lock.assert_current(journal["lifecycle_lock"])
            _unlink_journal(adoption_directory)
            adoption_lock.assert_current(receipt["lifecycle_lock"])
            return receipt
