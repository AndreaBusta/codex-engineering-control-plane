"""Serialized, proportional local verification for Control Plane Core."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import stat
import tomllib
from typing import Callable, Generic, Mapping, TypeVar

from control_plane.contracts import load_active_adoption_journal
from control_plane.leases import _adoption_lifecycle_lock
from control_plane.repository import (
    discover_repository,
    git_common_dir,
)


T = TypeVar("T")
_ADOPTION_LIFECYCLE = "journal-bound-v1"
_DIRECTORY_KEYS = frozenset(
    {"path", "device", "inode", "mode", "uid", "gid", "flags"}
)
_LOCK_KEYS = frozenset(
    {
        "path",
        "device",
        "inode",
        "mode",
        "links",
        "uid",
        "gid",
        "size",
        "mtime_ns",
        "ctime_ns",
        "flags",
    }
)
_DATALESS_FLAG = 0x40000000
_JOURNAL_MAX = 1024 * 1024


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _safe_directory(value: os.stat_result, *, private: bool) -> bool:
    mode = stat.S_IMODE(value.st_mode)
    return (
        stat.S_ISDIR(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink >= 1
        and (mode == 0o700 if private else mode & 0o022 == 0)
        and not bool(int(getattr(value, "st_flags", 0)) & _DATALESS_FLAG)
    )


def _safe_regular(
    value: os.stat_result,
    *,
    maximum: int,
    exact_mode: int | None,
) -> bool:
    mode = stat.S_IMODE(value.st_mode)
    return (
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink == 1
        and (mode == exact_mode if exact_mode is not None else mode & 0o022 == 0)
        and 0 <= value.st_size <= maximum
        and not bool(int(getattr(value, "st_flags", 0)) & _DATALESS_FLAG)
    )


def _metadata_identity(value: os.stat_result) -> tuple[int, ...]:
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


def _read_relative_regular(
    anchor: Path,
    parents: tuple[tuple[str, bool], ...],
    filename: str,
    *,
    maximum: int,
    exact_mode: int | None,
) -> bytes | None:
    descriptors: list[int] = []
    try:
        descriptor = os.open(anchor, _directory_flags())
        descriptors.append(descriptor)
        if not _safe_directory(os.fstat(descriptor), private=False):
            raise ValueError("E_VERIFICATION_LOCK: adoption anchor is unsafe")
        for name, private in parents:
            try:
                child = os.open(name, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                return None
            except OSError as error:
                raise ValueError(
                    "E_VERIFICATION_LOCK: adoption binding ancestor is unsafe"
                ) from error
            if not _safe_directory(os.fstat(child), private=private):
                os.close(child)
                raise ValueError(
                    "E_VERIFICATION_LOCK: adoption binding ancestor is unsafe"
                )
            descriptors.append(child)
            descriptor = child
        try:
            before = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
            file_descriptor = os.open(
                filename,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=descriptor,
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError("E_VERIFICATION_LOCK: adoption binding is unsafe") from error
        try:
            opened = os.fstat(file_descriptor)
            if (
                not _safe_regular(
                    opened,
                    maximum=maximum,
                    exact_mode=exact_mode,
                )
                or _metadata_identity(before) != _metadata_identity(opened)
            ):
                raise ValueError("E_VERIFICATION_LOCK: adoption binding is unsafe")
            payload = bytearray()
            while len(payload) <= maximum:
                chunk = os.read(
                    file_descriptor,
                    min(65_536, maximum + 1 - len(payload)),
                )
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(file_descriptor)
            named = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
            if (
                len(payload) > maximum
                or len(payload) != opened.st_size
                or _metadata_identity(opened) != _metadata_identity(after)
                or _metadata_identity(opened) != _metadata_identity(named)
            ):
                raise ValueError("E_VERIFICATION_LOCK: adoption binding changed")
            return bytes(payload)
        finally:
            os.close(file_descriptor)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError("E_VERIFICATION_LOCK: adoption binding is unavailable") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _directory_record(value: os.stat_result) -> dict[str, object]:
    return {
        "path": "codex-control-plane-core/locks",
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "uid": int(value.st_uid),
        "gid": int(value.st_gid),
        "flags": int(getattr(value, "st_flags", 0)),
    }


def _lock_record(value: os.stat_result) -> dict[str, object]:
    return {
        "path": "codex-control-plane-core/locks/verification.lock",
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


def _valid_integer_fields(value: Mapping[str, object], keys: tuple[str, ...]) -> bool:
    return all(type(value.get(key)) is int and int(value[key]) >= 0 for key in keys)


def _validated_verification_binding(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"directory", "file"}:
        raise ValueError("E_VERIFICATION_LOCK: adoption verification binding is invalid")
    directory = value.get("directory")
    lock = value.get("file")
    if (
        not isinstance(directory, Mapping)
        or set(directory) != _DIRECTORY_KEYS
        or directory.get("path") != "codex-control-plane-core/locks"
        or not _valid_integer_fields(
            directory, ("device", "inode", "mode", "uid", "gid", "flags")
        )
        or directory.get("mode") != 0o700
        or int(directory.get("flags", 0)) & 0x40000000
    ):
        raise ValueError("E_VERIFICATION_LOCK: adoption verification directory is invalid")
    if (
        not isinstance(lock, Mapping)
        or set(lock) != _LOCK_KEYS
        or lock.get("path") != "codex-control-plane-core/locks/verification.lock"
        or not _valid_integer_fields(
            lock,
            (
                "device",
                "inode",
                "mode",
                "links",
                "uid",
                "gid",
                "size",
                "mtime_ns",
                "ctime_ns",
                "flags",
            ),
        )
        or lock.get("mode") != 0o600
        or lock.get("links") != 1
        or lock.get("size") != 0
        or int(lock.get("flags", 0)) & 0x40000000
    ):
        raise ValueError("E_VERIFICATION_LOCK: adoption verification file is invalid")
    return {"directory": dict(directory), "file": dict(lock)}


def _validated_lifecycle_lock(value: object) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != _LOCK_KEYS
        or value.get("path") != "codex-control-plane-core/adoption.lock"
        or not _valid_integer_fields(
            value,
            (
                "device",
                "inode",
                "mode",
                "links",
                "uid",
                "gid",
                "size",
                "mtime_ns",
                "ctime_ns",
                "flags",
            ),
        )
        or value.get("mode") != 0o600
        or value.get("links") != 1
        or value.get("size") != 0
        or int(value.get("flags", 0)) & _DATALESS_FLAG
    ):
        raise ValueError("E_VERIFICATION_LOCK: lifecycle lock binding is invalid")


def _adoption_marker(repository: Path) -> str | None:
    payload = _read_relative_regular(
        repository,
        ((".codex", False),),
        "control-plane.lock",
        maximum=65_536,
        exact_mode=None,
    )
    if payload is None:
        return None
    try:
        value = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("E_VERIFICATION_LOCK: activation lock is invalid") from error
    marker = value.get("adoption_lifecycle")
    if marker is None:
        return None
    if marker != _ADOPTION_LIFECYCLE:
        raise ValueError("E_VERIFICATION_LOCK: activation lifecycle is unsupported")
    return str(marker)


def _adoption_journal(common_git_dir: Path) -> dict[str, object] | None:
    payload = _read_relative_regular(
        common_git_dir,
        (("codex-control-plane-core", True), ("adoption", True)),
        "journal.json",
        maximum=_JOURNAL_MAX,
        exact_mode=0o600,
    )
    if payload is None:
        return None
    try:
        value = load_active_adoption_journal(payload)
    except (ValueError, RecursionError) as error:
        raise ValueError("E_VERIFICATION_LOCK: adoption journal is invalid") from error
    _validated_lifecycle_lock(value.get("lifecycle_lock"))
    _validated_verification_binding(value.get("verification_lock"))
    return value


def _adoption_verification_context(
    repository: Path,
    common_git_dir: Path,
) -> dict[str, object] | None:
    try:
        marker = _adoption_marker(repository)
        journal = _adoption_journal(common_git_dir)
    except (OSError, ValueError, RecursionError) as error:
        raise ValueError("E_VERIFICATION_LOCK: adoption binding is unavailable") from error
    if marker is None and journal is None:
        return None
    if (
        marker != _ADOPTION_LIFECYCLE
        or journal is None
        or not isinstance(journal.get("state_digest"), str)
    ):
        raise ValueError("E_VERIFICATION_LOCK: adoption lifecycle is not active")
    return {
        "marker": marker,
        "state_digest": journal["state_digest"],
        "verification_lock": _validated_verification_binding(
            journal.get("verification_lock")
        ),
    }


def _open_private_child(parent: int, name: str, *, create: bool) -> int:
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise ValueError("E_VERIFICATION_LOCK: bound mutex directory is absent") from None
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
            os.fsync(parent)
            descriptor = os.open(name, _directory_flags(), dir_fd=parent)
        except OSError as error:
            raise ValueError(
                "E_VERIFICATION_LOCK: mutex directory cannot be created safely"
            ) from error
    except OSError as error:
        raise ValueError("E_VERIFICATION_LOCK: mutex directory is unsafe") from error
    if not _safe_directory(os.fstat(descriptor), private=True):
        os.close(descriptor)
        raise ValueError("E_VERIFICATION_LOCK: mutex directory is unsafe")
    return descriptor


def _safe_mutex_file(value: os.stat_result, *, bound: bool) -> bool:
    return (
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.geteuid()
        and value.st_nlink == 1
        and stat.S_IMODE(value.st_mode) == 0o600
        and value.st_size == 0
        and (
            not bound
            or not bool(int(getattr(value, "st_flags", 0)) & _DATALESS_FLAG)
        )
    )


@dataclass(frozen=True)
class VerificationResult(Generic[T]):
    status: str
    error_code: str | None
    executed: bool
    value: T | None
    consumes_reframe: bool
    authorizes: bool = False


class VerificationMutex:
    """One nonblocking full verifier for one Git common directory."""

    def __init__(self, repository: Path | str) -> None:
        repo = discover_repository(Path(repository))
        self.repository = repo
        self.common_git_dir = git_common_dir(repo)
        self.root = self.common_git_dir / "codex-control-plane-core" / "locks"
        self.path = self.root / "verification.lock"
        self.common_descriptor: int | None = None
        self.state_descriptor: int | None = None
        self.locks_descriptor: int | None = None
        self.descriptor: int | None = None
        self.lifecycle_claim: AbstractContextManager[None] | None = None
        self.lifecycle_held = False
        self.acquired = False

    def _open_mutex(self, *, create: bool, bound: bool) -> None:
        try:
            self.common_descriptor = os.open(
                self.common_git_dir,
                _directory_flags(),
            )
        except OSError as error:
            raise ValueError("E_VERIFICATION_LOCK: Git common directory is unsafe") from error
        if not _safe_directory(os.fstat(self.common_descriptor), private=False):
            raise ValueError("E_VERIFICATION_LOCK: Git common directory is unsafe")
        self.state_descriptor = _open_private_child(
            self.common_descriptor,
            "codex-control-plane-core",
            create=create,
        )
        self.locks_descriptor = _open_private_child(
            self.state_descriptor,
            "locks",
            create=create,
        )
        flags = (
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if create:
            flags |= os.O_CREAT
        try:
            self.descriptor = os.open(
                "verification.lock",
                flags,
                0o600,
                dir_fd=self.locks_descriptor,
            )
        except OSError as error:
            raise ValueError("E_VERIFICATION_LOCK: verification mutex is unavailable") from error
        if not _safe_mutex_file(os.fstat(self.descriptor), bound=bound):
            raise ValueError("E_VERIFICATION_LOCK: verification mutex is unsafe")

    def _assert_named_mutex(
        self,
        context: Mapping[str, object] | None,
    ) -> None:
        if (
            self.common_descriptor is None
            or self.state_descriptor is None
            or self.locks_descriptor is None
            or self.descriptor is None
        ):
            raise ValueError("E_VERIFICATION_LOCK: verification mutex is unavailable")
        try:
            opened_state = os.fstat(self.state_descriptor)
            named_state = os.stat(
                "codex-control-plane-core",
                dir_fd=self.common_descriptor,
                follow_symlinks=False,
            )
            opened_directory = os.fstat(self.locks_descriptor)
            named_directory = os.stat(
                "locks",
                dir_fd=self.state_descriptor,
                follow_symlinks=False,
            )
            opened_lock = os.fstat(self.descriptor)
            named_lock = os.stat(
                "verification.lock",
                dir_fd=self.locks_descriptor,
                follow_symlinks=False,
            )
        except OSError as error:
            raise ValueError(
                "E_VERIFICATION_LOCK: verification mutex identity changed"
            ) from error
        bound = context is not None
        if (
            not _safe_directory(opened_state, private=True)
            or not _safe_directory(named_state, private=True)
            or _directory_record(opened_state) != _directory_record(named_state)
            or not _safe_directory(opened_directory, private=True)
            or not _safe_directory(named_directory, private=True)
            or _directory_record(opened_directory) != _directory_record(named_directory)
            or not _safe_mutex_file(opened_lock, bound=bound)
            or not _safe_mutex_file(named_lock, bound=bound)
            or _lock_record(opened_lock) != _lock_record(named_lock)
        ):
            raise ValueError("E_VERIFICATION_LOCK: verification mutex identity changed")
        current = {
            "directory": _directory_record(opened_directory),
            "file": _lock_record(opened_lock),
        }
        if context is not None and current != context["verification_lock"]:
            raise ValueError("E_VERIFICATION_LOCK: verification mutex binding changed")

    def __enter__(self) -> bool:
        try:
            self.lifecycle_claim = _adoption_lifecycle_lock(
                self.repository,
                self.common_git_dir,
            )
            try:
                self.lifecycle_claim.__enter__()
            except (OSError, ValueError) as error:
                raise ValueError(
                    "E_VERIFICATION_LOCK: adoption lifecycle is unavailable"
                ) from error
            self.lifecycle_held = True
            context_before = _adoption_verification_context(
                self.repository,
                self.common_git_dir,
            )
            self._open_mutex(
                create=context_before is None,
                bound=context_before is not None,
            )
            assert self.descriptor is not None
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.acquired = False
            return False
        except Exception:
            self._release()
            raise
        try:
            self._assert_named_mutex(context_before)
            context_after = _adoption_verification_context(
                self.repository,
                self.common_git_dir,
            )
            if context_after != context_before:
                raise ValueError("E_VERIFICATION_LOCK: adoption binding changed")
        except Exception:
            self._release()
            raise
        self.acquired = True
        return True

    def _release(self) -> None:
        if self.descriptor is None:
            pass
        else:
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self.descriptor)
            self.descriptor = None
        for attribute in (
            "locks_descriptor",
            "state_descriptor",
            "common_descriptor",
        ):
            descriptor = getattr(self, attribute)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, attribute, None)
        if self.lifecycle_held and self.lifecycle_claim is not None:
            try:
                self.lifecycle_claim.__exit__(None, None, None)
            finally:
                self.lifecycle_held = False
                self.lifecycle_claim = None
        elif self.lifecycle_claim is not None:
            self.lifecycle_claim = None
        self.acquired = False

    def __exit__(self, *_: object) -> None:
        self._release()


def run_serialized_verification(
    repository: Path | str,
    runner: Callable[[], T],
) -> VerificationResult[T]:
    """Run only after the mutex; contention executes no Git or test command."""

    with VerificationMutex(repository) as acquired:
        if not acquired:
            return VerificationResult(
                status="UNKNOWN",
                error_code="E_VERIFICATION_BUSY",
                executed=False,
                value=None,
                consumes_reframe=False,
                authorizes=False,
            )
        try:
            value = runner()
        except Exception:
            return VerificationResult(
                status="FAIL",
                error_code="E_VERIFICATION_FAILED",
                executed=True,
                value=None,
                consumes_reframe=False,
                authorizes=False,
            )
        return VerificationResult(
            status="PASS",
            error_code=None,
            executed=True,
            value=value,
            consumes_reframe=False,
            authorizes=False,
        )
