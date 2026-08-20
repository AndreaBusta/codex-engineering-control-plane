"""Revision-scoped, generational writer leases for Control Plane Core."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import tomllib
from typing import Any, Iterator, Mapping

from control_plane.contracts import (
    SHA256_DIGEST,
    contract_digest,
    load_active_adoption_journal,
    validate_task_id,
)
from control_plane.repository import (
    discover_repository,
    ensure_private_state_directory,
    git_common_dir,
    open_private_state_lock,
)
from control_plane.scopes import normalize_scope, scope_owns, scopes_overlap


_MAX_LEASE_FILES = 2_048
_MAX_LEASE_BYTES = 65_536
_MAX_ADOPTION_JOURNAL_BYTES = 1024 * 1024
_ADOPTION_LIFECYCLE = "journal-bound-v1"
_REVISION_ID = re.compile(r"^rev-[0-9a-f]{16}$", re.ASCII)
_LEASE_ID = re.compile(r"^lease-[0-9a-f]{64}$", re.ASCII)
_LEASE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "lease_id",
        "task_id",
        "revision_id",
        "lease_generation",
        "worktree",
        "branch",
        "session_id",
        "scope_paths",
        "policy_digest",
        "owner_runtime_digest",
        "acquired_state_digest",
        "acquired_at",
        "authorizes",
        "lease_digest",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "lease_id",
        "task_id",
        "revision_id",
        "lease_generation",
        "released_lease_digest",
        "released_policy_digest",
        "released_at",
        "authorizes",
        "receipt_digest",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _private_directory(path: Path) -> Path:
    if not path.exists():
        path.mkdir(parents=True, mode=0o700)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("E_CORE_LEASE_PATH: lease directory is unsafe")
    path.chmod(0o700)
    return path


def _lock_identity(value: os.stat_result) -> tuple[int, ...]:
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


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_uid),
        int(value.st_gid),
        int(getattr(value, "st_flags", 0)),
    )


def _private_state_directory(value: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(value.st_mode)
        and value.st_uid == os.geteuid()
        and stat.S_IMODE(value.st_mode) == 0o700
        and not bool(int(getattr(value, "st_flags", 0)) & 0x40000000)
    )


def _private_lifecycle_lock(value: os.stat_result) -> bool:
    return (
        stat.S_ISREG(value.st_mode)
        and value.st_nlink == 1
        and value.st_uid == os.geteuid()
        and stat.S_IMODE(value.st_mode) == 0o600
        and value.st_size == 0
        and not bool(int(getattr(value, "st_flags", 0)) & 0x40000000)
    )


def _lifecycle_record(value: os.stat_result) -> dict[str, object]:
    return {
        "path": "codex-control-plane-core/adoption.lock",
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


def _bounded_regular_bytes(path: Path, *, maximum: int) -> bytes | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError("E_CORE_LEASE_PATH: adoption binding is unavailable") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o022
        or not 0 <= before.st_size <= maximum
        or bool(int(getattr(before, "st_flags", 0)) & 0x40000000)
    ):
        raise ValueError("E_CORE_LEASE_PATH: adoption binding is unsafe")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("E_CORE_LEASE_PATH: adoption binding cannot be opened") from error
    try:
        opened = os.fstat(descriptor)
        if _lock_identity(before) != _lock_identity(opened):
            raise ValueError("E_CORE_LEASE_PATH: adoption binding identity changed")
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > maximum:
                raise ValueError("E_CORE_LEASE_PATH: adoption binding is oversized")
        after = path.lstat()
        if (
            _lock_identity(opened) != _lock_identity(os.fstat(descriptor))
            or _lock_identity(opened) != _lock_identity(after)
            or observed != opened.st_size
        ):
            raise ValueError("E_CORE_LEASE_PATH: adoption binding changed")
        return b"".join(chunks)
    except OSError as error:
        raise ValueError("E_CORE_LEASE_PATH: adoption binding changed") from error
    finally:
        os.close(descriptor)


def _adoption_marker(repository: Path) -> str | None:
    payload = _bounded_regular_bytes(
        repository / ".codex" / "control-plane.lock",
        maximum=65_536,
    )
    if payload is None:
        return None
    try:
        value = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("E_CORE_LEASE_PATH: activation lock is invalid") from error
    marker = value.get("adoption_lifecycle")
    if marker is None:
        return None
    if marker != _ADOPTION_LIFECYCLE:
        raise ValueError("E_CORE_LEASE_PATH: activation lifecycle is unsupported")
    return str(marker)


def _adoption_journal(common_git_dir: Path) -> dict[str, object] | None:
    adoption = ensure_private_state_directory(
        common_git_dir,
        ("codex-control-plane-core", "adoption"),
        create=False,
        missing_ok=True,
        code="E_CORE_LEASE_PATH",
    )
    if adoption is None:
        return None
    payload = _bounded_regular_bytes(
        adoption / "journal.json",
        maximum=_MAX_ADOPTION_JOURNAL_BYTES,
    )
    if payload is None:
        return None
    try:
        value = load_active_adoption_journal(payload)
    except (ValueError, RecursionError) as error:
        raise ValueError("E_CORE_LEASE_PATH: adoption journal is invalid") from error
    return value


@contextmanager
def _adoption_lifecycle_lock(
    repository: Path,
    common_git_dir: Path,
) -> Iterator[None]:
    """Hold one lifecycle inode across legacy or journal-bound Core mutation."""

    state = ensure_private_state_directory(
        common_git_dir,
        ("codex-control-plane-core",),
        create=True,
        code="E_CORE_LEASE_PATH",
    )
    assert state is not None
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    state_descriptor: int | None = None
    descriptor: int | None = None
    try:
        state_descriptor = os.open(state, directory_flags)
        opened_state = os.fstat(state_descriptor)
        named_state = state.lstat()
        if (
            not _private_state_directory(opened_state)
            or not _private_state_directory(named_state)
            or _directory_identity(opened_state) != _directory_identity(named_state)
        ):
            raise ValueError("E_CORE_LEASE_PATH: adoption mutex directory is unsafe")
        flags = (
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(
                "adoption.lock",
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=state_descriptor,
            )
            os.fsync(state_descriptor)
        except FileExistsError:
            before = os.stat(
                "adoption.lock",
                dir_fd=state_descriptor,
                follow_symlinks=False,
            )
            descriptor = os.open(
                "adoption.lock",
                flags,
                dir_fd=state_descriptor,
            )
            if _lock_identity(before) != _lock_identity(os.fstat(descriptor)):
                raise ValueError("E_CORE_LEASE_PATH: adoption mutex identity changed")
        except OSError as error:
            raise ValueError("E_CORE_LEASE_PATH: adoption mutex cannot be opened") from error
        opened = os.fstat(descriptor)
        if not _private_lifecycle_lock(opened):
            raise ValueError("E_CORE_LEASE_PATH: adoption mutex is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
        except OSError as error:
            raise ValueError("E_CORE_LEASE_PATH: adoption mutex cannot be acquired") from error
        try:
            after = os.stat(
                "adoption.lock",
                dir_fd=state_descriptor,
                follow_symlinks=False,
            )
            named_state = state.lstat()
        except OSError as error:
            raise ValueError("E_CORE_LEASE_PATH: adoption mutex changed") from error
        if (
            not _private_lifecycle_lock(after)
            or _lock_identity(opened) != _lock_identity(after)
            or _directory_identity(opened_state) != _directory_identity(named_state)
        ):
            raise ValueError("E_CORE_LEASE_PATH: adoption mutex identity changed")
        marker_after = _adoption_marker(repository)
        journal_after = _adoption_journal(common_git_dir)
        for reserved in (".provisioning-adoption", ".provisioning-locks"):
            try:
                os.stat(
                    reserved,
                    dir_fd=state_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            except OSError as error:
                raise ValueError(
                    "E_CORE_LEASE_PATH: adoption provisioning state is unavailable"
                ) from error
            raise ValueError(
                "E_CORE_LEASE_PATH: adoption lifecycle is provisioning"
            )
        try:
            adoption_metadata = os.stat(
                "adoption",
                dir_fd=state_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            adoption_present = False
        except OSError as error:
            raise ValueError("E_CORE_LEASE_PATH: adoption state is unavailable") from error
        else:
            adoption_present = True
            if not _private_state_directory(adoption_metadata):
                raise ValueError("E_CORE_LEASE_PATH: adoption state is unsafe")
        legacy = marker_after is None and journal_after is None and not adoption_present
        active = (
            marker_after == _ADOPTION_LIFECYCLE
            and journal_after is not None
            and journal_after.get("state") == "active"
            and journal_after.get("lifecycle_lock") == _lifecycle_record(opened)
        )
        if not legacy and not active:
            raise ValueError("E_CORE_LEASE_PATH: adoption lifecycle is not active")
        final = os.stat(
            "adoption.lock",
            dir_fd=state_descriptor,
            follow_symlinks=False,
        )
        if _lock_identity(opened) != _lock_identity(final):
            raise ValueError("E_CORE_LEASE_PATH: adoption mutex identity changed")
        yield
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)
        if state_descriptor is not None:
            os.close(state_descriptor)


@contextmanager
def _lease_file_lock(common_git_dir: Path) -> Iterator[None]:
    descriptor = open_private_state_lock(
        common_git_dir,
        ("codex-control-plane-core", "locks"),
        "leases.lock",
        code="E_CORE_LEASE_PATH",
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _lease_lock(repository: Path, common_git_dir: Path) -> Iterator[None]:
    with _adoption_lifecycle_lock(repository, common_git_dir):
        with _lease_file_lock(common_git_dir):
            yield


def _read(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_LEASE_BYTES
        ):
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("E_CORE_LEASE_INVALID: lease record is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("E_CORE_LEASE_INVALID: lease record must be an object")
    return value


def _write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    parent = _private_directory(path.parent)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > _MAX_LEASE_BYTES:
        raise ValueError("E_CORE_LEASE_SIZE: lease record is too large")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _unlink_durable(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class _HeldAdoptionClaims:
    def __init__(self, store: "LeaseStore") -> None:
        self._store = store
        self._active = True

    def close(self) -> None:
        self._active = False

    @contextmanager
    def claim_no_active(self, task_id: str) -> Iterator[None]:
        if not self._active:
            raise ValueError("E_CORE_LEASE_PATH: adoption barrier is not held")
        with self._store._claim_no_active_under_adoption(task_id):
            yield


class LeaseStore:
    def __init__(self, repository: Path | str) -> None:
        self.repository = discover_repository(Path(repository))
        self.common_git_dir = git_common_dir(self.repository)
        self.root = self.common_git_dir / "codex-control-plane-core"
        self.leases = self.root / "leases"
        self.receipts = self.root / "lease-release-receipts"

    def _records(self, directory: Path) -> list[dict[str, Any]]:
        safe_directory = ensure_private_state_directory(
            self.common_git_dir,
            ("codex-control-plane-core", directory.name),
            create=False,
            missing_ok=True,
            code="E_CORE_LEASE_PATH",
        )
        if safe_directory is None:
            return []
        paths = sorted(safe_directory.glob("*.json"))
        if len(paths) > _MAX_LEASE_FILES:
            raise ValueError("E_CORE_LEASE_BOUNDS: too many lease records")
        return [_read(path) for path in paths]

    def active(self) -> list[dict[str, Any]]:
        records = self._records(self.leases)
        for record in records:
            self._validate_lease(record)
        return records

    def find(self, task_id: str) -> dict[str, Any] | None:
        matches = [record for record in self.active() if record["task_id"] == task_id]
        if len(matches) > 1:
            raise ValueError("E_CORE_LEASE_INVALID: task has multiple active leases")
        return matches[0] if matches else None

    @contextmanager
    def _adoption_scope(
        self,
        claims: _HeldAdoptionClaims | None,
    ) -> Iterator[None]:
        if claims is None:
            with _adoption_lifecycle_lock(self.repository, self.common_git_dir):
                yield
            return
        if (
            type(claims) is not _HeldAdoptionClaims
            or claims._store is not self
            or not claims._active
        ):
            raise ValueError("E_CORE_LEASE_PATH: adoption barrier is not held")
        yield

    @contextmanager
    def _claim_lock(
        self,
        claims: _HeldAdoptionClaims | None,
    ) -> Iterator[None]:
        with self._adoption_scope(claims):
            with _lease_file_lock(self.common_git_dir):
                yield

    @contextmanager
    def claim_mutation(
        self,
        state: Mapping[str, Any],
        *,
        session_id: str | None,
        _claims: _HeldAdoptionClaims | None = None,
    ) -> Iterator[dict[str, Any] | None]:
        """Hold the lease mutex while a bound owner mutates one task."""

        with self._claim_lock(_claims):
            lease = self.find(str(state.get("task_id", "")))
            if (
                lease is None
                and session_id is None
                and state.get("lease_generation") == 0
            ):
                yield None
                return
            if not validate_task_id(session_id):
                raise ValueError("E_CORE_LEASE_OWNER: session identity is invalid")
            expected = {
                "task_id": state.get("task_id"),
                "revision_id": state.get("revision_id"),
                "worktree": state.get("worktree"),
                "branch": state.get("branch"),
                "session_id": session_id,
                "owner_runtime_digest": state.get("owner_runtime_digest"),
            }
            if lease is None or any(
                lease.get(key) != value for key, value in expected.items()
            ):
                raise ValueError(
                    "E_CORE_LEASE_OWNER: task mutation requires the exact lease owner"
                )
            generation = state.get("lease_generation")
            if (
                generation == 0
                and lease.get("acquired_state_digest") != state.get("state_digest")
            ) or (
                generation != 0
                and lease.get("lease_generation") != generation
            ):
                raise ValueError(
                    "E_CORE_LEASE_OWNER: task mutation lease generation drifted"
                )
            yield lease

    @contextmanager
    def claim_next_revision(
        self,
        state: Mapping[str, Any],
        *,
        _claims: _HeldAdoptionClaims | None = None,
    ) -> Iterator[int]:
        """Prove a prior writer was released before replacing its revision."""

        task_id = state.get("task_id")
        revision_id = state.get("revision_id")
        generation = state.get("lease_generation")
        if (
            not validate_task_id(task_id)
            or not isinstance(revision_id, str)
            or _REVISION_ID.fullmatch(revision_id) is None
            or type(generation) is not int
            or generation < 0
        ):
            raise ValueError("E_CORE_REVISION: task revision binding is invalid")
        with self._claim_lock(_claims):
            if self.find(str(task_id)) is not None:
                raise ValueError(
                    "E_CORE_REVISION_LEASE_ACTIVE: active lease blocks revision"
                )
            receipts = self._records(self.receipts)
            for receipt in receipts:
                self._validate_receipt(receipt)
            task_receipts = [
                receipt for receipt in receipts if receipt.get("task_id") == task_id
            ]
            if generation > 0:
                matching = [
                    receipt
                    for receipt in task_receipts
                    if receipt.get("revision_id") == revision_id
                    and receipt.get("lease_generation") == generation
                ]
                if len(matching) != 1:
                    raise ValueError(
                        "E_CORE_REVISION_RECEIPT: exact lease release receipt is required"
                    )
            yield max(
                [
                    int(state.get("lease_generation_floor", 0)),
                    *(int(receipt["lease_generation"]) for receipt in task_receipts),
                ]
            )

    @contextmanager
    def claim_no_active(
        self,
        task_id: str,
        *,
        _claims: _HeldAdoptionClaims | None = None,
    ) -> Iterator[None]:
        """Hold the lease mutex while proving a task has no active lease."""

        with self._adoption_scope(_claims):
            with self._claim_no_active_under_adoption(task_id):
                yield

    @contextmanager
    def adoption_claims(self) -> Iterator[_HeldAdoptionClaims]:
        """Validate and retain the adoption barrier before another lock is created."""

        with _adoption_lifecycle_lock(self.repository, self.common_git_dir):
            claims = _HeldAdoptionClaims(self)
            try:
                yield claims
            finally:
                claims.close()

    @contextmanager
    def _claim_no_active_under_adoption(self, task_id: str) -> Iterator[None]:
        if not validate_task_id(task_id):
            raise ValueError("E_CORE_LEASE_BINDING: task identity is invalid")
        with _lease_file_lock(self.common_git_dir):
            if self.find(task_id) is not None:
                raise ValueError(
                    "E_CORE_LEASE_ACTIVE: active lease blocks task mutation"
                )
            yield

    @contextmanager
    def claim_binding(
        self,
        state: Mapping[str, Any],
        *,
        revision_id: str,
        generation: int,
        acquired_state_digest: str,
        session_id: str | None,
        _claims: _HeldAdoptionClaims | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Hold the lease mutex while an exact acquired binding is consumed."""

        with self._claim_lock(_claims):
            if not validate_task_id(session_id):
                raise ValueError(
                    "E_CORE_LEASE_OWNER: exact lease session is required"
                )
            lease = self.find(str(state.get("task_id", "")))
            if lease is None:
                raise ValueError("E_CORE_LEASE_NOT_FOUND: active lease is unavailable")
            if lease.get("session_id") != session_id:
                raise ValueError(
                    "E_CORE_LEASE_OWNER: exact lease session does not match"
                )
            expected = {
                "task_id": state.get("task_id"),
                "revision_id": revision_id,
                "lease_generation": generation,
                "worktree": state.get("worktree"),
                "branch": state.get("branch"),
                "owner_runtime_digest": state.get("owner_runtime_digest"),
                "acquired_state_digest": acquired_state_digest,
            }
            if any(lease.get(key) != value for key, value in expected.items()):
                raise ValueError("E_CORE_LEASE_BINDING: active lease binding drifted")
            yield lease

    def acquire(
        self,
        state: Mapping[str, Any],
        *,
        session_id: str,
        policy_digest: str,
    ) -> dict[str, Any]:
        value, _ = self.acquire_with_origin(
            state,
            session_id=session_id,
            policy_digest=policy_digest,
        )
        return value

    def acquire_with_origin(
        self,
        state: Mapping[str, Any],
        *,
        session_id: str,
        policy_digest: str,
    ) -> tuple[dict[str, Any], bool]:
        task_id = state.get("task_id")
        revision_id = state.get("revision_id")
        if (
            not validate_task_id(task_id)
            or not isinstance(revision_id, str)
            or not validate_task_id(session_id)
            or not isinstance(policy_digest, str)
            or SHA256_DIGEST.fullmatch(policy_digest) is None
            or state.get("kind") != "CoreTaskStateV1"
            or state.get("requested_outcome") != "local_change"
            or not isinstance(state.get("state_digest"), str)
            or SHA256_DIGEST.fullmatch(str(state.get("state_digest"))) is None
        ):
            raise ValueError("E_CORE_LEASE_BINDING: task or session binding is invalid")
        from control_plane.task_state import (
            CoreTaskStore,
            _locked_runtime_digest,
            _task_lock,
            task_allows_writer_lease,
        )

        task_store = CoreTaskStore(self.repository)
        with self.adoption_claims() as claims:
            with _task_lock(task_store.git_dir, str(task_id)):
                current = task_store.status(str(task_id))
                if (
                    current != dict(state)
                    or not task_allows_writer_lease(current)
                    or current.get("owner_runtime_digest")
                    != _locked_runtime_digest(self.repository)
                ):
                    raise ValueError(
                        "E_CORE_LEASE_BINDING: task state is stale or not an active writer"
                    )
                return self._acquire_current(
                    current,
                    session_id=session_id,
                    policy_digest=policy_digest,
                    _claims=claims,
                )

    def _acquire_current(
        self,
        state: Mapping[str, Any],
        *,
        session_id: str,
        policy_digest: str,
        _claims: _HeldAdoptionClaims | None = None,
    ) -> tuple[dict[str, Any], bool]:
        task_id = str(state["task_id"])
        revision_id = str(state["revision_id"])
        scopes = sorted({normalize_scope(str(item)) for item in state.get("scope_paths", [])})
        if not scopes:
            scopes = ["."]
        with self._claim_lock(_claims):
            active = self.active()
            exact = [
                item
                for item in active
                if item["task_id"] == task_id
                and item["revision_id"] == revision_id
                and item["session_id"] == session_id
                and item["worktree"] == state.get("worktree")
            ]
            if exact:
                expected = {
                    "schema_version": 1,
                    "kind": "CoreWriterLeaseV1",
                    "task_id": task_id,
                    "revision_id": revision_id,
                    "worktree": state.get("worktree"),
                    "branch": state.get("branch"),
                    "session_id": session_id,
                    "scope_paths": scopes,
                    "policy_digest": policy_digest,
                    "owner_runtime_digest": state.get("owner_runtime_digest"),
                }
                if len(exact) != 1 or any(
                    exact[0].get(key) != value for key, value in expected.items()
                ):
                    raise ValueError("E_CORE_LEASE_REPLAY: exact lease drifted")
                return exact[0], False
            for item in active:
                if any(
                    scopes_overlap(owned, requested)
                    for owned in item["scope_paths"]
                    for requested in scopes
                ):
                    raise ValueError(
                        "E_CORE_LEASE_CONFLICT: another writer owns overlapping scope"
                    )
            receipts = self._records(self.receipts)
            for receipt in receipts:
                self._validate_receipt(receipt)
            historical = [*active, *receipts]
            generation = 1 + max(
                [
                    int(state.get("lease_generation_floor", 0)),
                    *(
                    int(item.get("lease_generation", 0))
                    for item in historical
                    if item.get("task_id") == task_id
                    ),
                ]
            )
            lease_id = "lease-" + sha256(
                f"{task_id}\0{revision_id}\0{generation}".encode()
            ).hexdigest()
            value: dict[str, Any] = {
                "schema_version": 1,
                "kind": "CoreWriterLeaseV1",
                "lease_id": lease_id,
                "task_id": task_id,
                "revision_id": revision_id,
                "lease_generation": generation,
                "worktree": state.get("worktree"),
                "branch": state.get("branch"),
                "session_id": session_id,
                "scope_paths": scopes,
                "policy_digest": policy_digest,
                "owner_runtime_digest": state.get("owner_runtime_digest"),
                "acquired_state_digest": state.get("state_digest"),
                "acquired_at": _now(),
                "authorizes": False,
            }
            value["lease_digest"] = contract_digest(value)
            path = self.leases / f"{lease_id}.json"
            try:
                _write_exclusive(path, value)
            except Exception:
                if path.exists():
                    current = _read(path)
                    if current != value:
                        raise ValueError(
                            "E_CORE_LEASE_ROLLBACK: lease changed during acquire"
                        ) from None
                    _unlink_durable(path)
                raise
            return value, True

    def rollback_acquire(self, lease: Mapping[str, Any]) -> None:
        self._validate_lease(lease)
        task_id = str(lease["task_id"])
        revision_id = str(lease["revision_id"])
        generation = int(lease["lease_generation"])
        expected_id = "lease-" + sha256(
            f"{task_id}\0{revision_id}\0{generation}".encode()
        ).hexdigest()
        if lease.get("lease_id") != expected_id:
            raise ValueError("E_CORE_LEASE_ROLLBACK: lease identity is invalid")
        path = self.leases / f"{expected_id}.json"
        receipt = self.receipts / f"{expected_id}.json"
        with _lease_lock(self.repository, self.common_git_dir):
            if receipt.exists():
                raise ValueError("E_CORE_LEASE_ROLLBACK: release receipt already exists")
            if not path.exists():
                return
            current = _read(path)
            self._validate_lease(current)
            if current != dict(lease):
                raise ValueError("E_CORE_LEASE_ROLLBACK: lease changed after acquire")
            _unlink_durable(path)

    def validate_continuation(
        self,
        *,
        task_id: str,
        worktree: str,
        branch: str,
        session_id: str,
        policy_digest: str,
        expected_revision_id: str,
        expected_lease_generation: int,
        expected_owner_runtime_digest: str,
        changed_paths: list[str] | tuple[str, ...],
        _claims: _HeldAdoptionClaims | None = None,
    ) -> dict[str, Any]:
        with self._claim_lock(_claims):
            lease = self.find(task_id)
            if lease is None:
                raise ValueError("E_CORE_LEASE_NOT_FOUND: active lease is unavailable")
            if (
                lease["worktree"] != worktree
                or lease["branch"] != branch
                or lease["session_id"] != session_id
                or lease["policy_digest"] != policy_digest
                or lease["revision_id"] != expected_revision_id
                or lease["lease_generation"] != expected_lease_generation
                or lease.get("owner_runtime_digest") != expected_owner_runtime_digest
            ):
                raise ValueError("E_CORE_LEASE_BINDING: continuation binding drifted")
            for path in changed_paths:
                if not any(scope_owns(scope, path) for scope in lease["scope_paths"]):
                    raise ValueError(f"E_CORE_LEASE_SCOPE: changed path is unowned: {path}")
            return lease

    def release(
        self,
        *,
        task_id: str,
        revision_id: str,
        lease_generation: int,
        worktree: str,
        branch: str,
        session_id: str,
        policy_digest: str,
        lease_digest: str,
    ) -> dict[str, Any]:
        with _lease_lock(self.repository, self.common_git_dir):
            lease_id = "lease-" + sha256(
                f"{task_id}\0{revision_id}\0{lease_generation}".encode()
            ).hexdigest()
            lease_path = self.leases / f"{lease_id}.json"
            receipt_path = self.receipts / f"{lease_id}.json"
            if receipt_path.exists():
                receipt = _read(receipt_path)
                self._validate_receipt(receipt)
                if (
                    receipt.get("released_lease_digest") != lease_digest
                    or receipt.get("released_policy_digest") != policy_digest
                ):
                    raise ValueError("E_CORE_LEASE_RELEASE: receipt replay drifted")
                if lease_path.exists():
                    _unlink_durable(lease_path)
                return receipt
            if not lease_path.exists():
                raise ValueError("E_CORE_LEASE_NOT_FOUND: active lease is unavailable")
            lease = _read(lease_path)
            self._validate_lease(lease)
            expected = {
                "task_id": task_id,
                "revision_id": revision_id,
                "lease_generation": lease_generation,
                "worktree": worktree,
                "branch": branch,
                "session_id": session_id,
                "policy_digest": policy_digest,
                "lease_digest": lease_digest,
            }
            if any(lease.get(key) != value for key, value in expected.items()):
                raise ValueError("E_CORE_LEASE_RELEASE: owner binding drifted")
            receipt: dict[str, Any] = {
                "schema_version": 1,
                "kind": "CoreLeaseReleaseReceiptV1",
                "lease_id": lease_id,
                "task_id": task_id,
                "revision_id": revision_id,
                "lease_generation": lease_generation,
                "released_lease_digest": lease_digest,
                "released_policy_digest": policy_digest,
                "released_at": _now(),
                "authorizes": False,
            }
            receipt["receipt_digest"] = contract_digest(receipt)
            _write_exclusive(receipt_path, receipt)
            _unlink_durable(lease_path)
            return receipt

    @staticmethod
    def _validate_lease(value: Mapping[str, Any]) -> None:
        unsigned = {key: item for key, item in value.items() if key != "lease_digest"}
        raw_scopes = value.get("scope_paths")
        try:
            normalized_scopes = (
                sorted({normalize_scope(item) for item in raw_scopes})
                if isinstance(raw_scopes, list)
                and all(isinstance(item, str) for item in raw_scopes)
                else None
            )
        except (TypeError, ValueError):
            normalized_scopes = None
        task_id = value.get("task_id")
        revision_id = value.get("revision_id")
        generation = value.get("lease_generation")
        lease_id = value.get("lease_id")
        valid = (
            set(value) == _LEASE_FIELDS
            and type(value.get("schema_version")) is int
            and value.get("schema_version") == 1
            and value.get("kind") == "CoreWriterLeaseV1"
            and validate_task_id(task_id)
            and isinstance(revision_id, str)
            and _REVISION_ID.fullmatch(revision_id) is not None
            and type(generation) is int
            and int(generation) >= 1
            and isinstance(lease_id, str)
            and _LEASE_ID.fullmatch(lease_id) is not None
            and lease_id
            == "lease-"
            + sha256(f"{task_id}\0{revision_id}\0{generation}".encode()).hexdigest()
            and all(
                isinstance(value.get(name), str) and bool(value.get(name))
                for name in ("worktree", "branch", "session_id")
            )
            and Path(str(value.get("worktree"))).is_absolute()
            and validate_task_id(value.get("session_id"))
            and isinstance(raw_scopes, list)
            and bool(raw_scopes)
            and raw_scopes == normalized_scopes
            and all(
                isinstance(value.get(name), str)
                and SHA256_DIGEST.fullmatch(str(value.get(name))) is not None
                for name in (
                    "policy_digest",
                    "owner_runtime_digest",
                    "acquired_state_digest",
                    "lease_digest",
                )
            )
            and isinstance(value.get("acquired_at"), str)
            and 0 < len(str(value.get("acquired_at"))) <= 64
            and str(value.get("acquired_at")).endswith("Z")
            and value.get("authorizes") is False
            and value.get("lease_digest") == contract_digest(unsigned)
        )
        if not valid:
            raise ValueError("E_CORE_LEASE_INVALID: lease binding is invalid")

    @staticmethod
    def _validate_receipt(value: Mapping[str, Any]) -> None:
        unsigned = {key: item for key, item in value.items() if key != "receipt_digest"}
        task_id = value.get("task_id")
        revision_id = value.get("revision_id")
        generation = value.get("lease_generation")
        lease_id = value.get("lease_id")
        valid = (
            set(value) == _RECEIPT_FIELDS
            and type(value.get("schema_version")) is int
            and value.get("schema_version") == 1
            and value.get("kind") == "CoreLeaseReleaseReceiptV1"
            and validate_task_id(task_id)
            and isinstance(revision_id, str)
            and _REVISION_ID.fullmatch(revision_id) is not None
            and type(generation) is int
            and int(generation) >= 1
            and isinstance(lease_id, str)
            and _LEASE_ID.fullmatch(lease_id) is not None
            and lease_id
            == "lease-"
            + sha256(f"{task_id}\0{revision_id}\0{generation}".encode()).hexdigest()
            and all(
                isinstance(value.get(name), str)
                and SHA256_DIGEST.fullmatch(str(value.get(name))) is not None
                for name in (
                    "released_lease_digest",
                    "released_policy_digest",
                    "receipt_digest",
                )
            )
            and isinstance(value.get("released_at"), str)
            and 0 < len(str(value.get("released_at"))) <= 64
            and str(value.get("released_at")).endswith("Z")
            and value.get("authorizes") is False
            and value.get("receipt_digest") == contract_digest(unsigned)
        )
        if not valid:
            raise ValueError("E_CORE_LEASE_INVALID: release receipt is invalid")
