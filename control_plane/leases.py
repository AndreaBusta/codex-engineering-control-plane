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
from typing import Any, Iterator, Mapping

from control_plane.contracts import SHA256_DIGEST, contract_digest, validate_task_id
from control_plane.repository import (
    discover_repository,
    ensure_private_state_directory,
    git_common_dir,
    open_private_state_lock,
)
from control_plane.scopes import normalize_scope, scope_owns, scopes_overlap


_MAX_LEASE_FILES = 2_048
_MAX_LEASE_BYTES = 65_536
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


@contextmanager
def _lease_lock(common_git_dir: Path) -> Iterator[None]:
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
            )

    def _acquire_current(
        self,
        state: Mapping[str, Any],
        *,
        session_id: str,
        policy_digest: str,
    ) -> tuple[dict[str, Any], bool]:
        task_id = str(state["task_id"])
        revision_id = str(state["revision_id"])
        scopes = sorted({normalize_scope(str(item)) for item in state.get("scope_paths", [])})
        if not scopes:
            scopes = ["."]
        with _lease_lock(self.common_git_dir):
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
                (
                    int(item.get("lease_generation", 0))
                    for item in historical
                    if item.get("task_id") == task_id
                ),
                default=0,
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
        with _lease_lock(self.common_git_dir):
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
    ) -> dict[str, Any]:
        with _lease_lock(self.common_git_dir):
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
        with _lease_lock(self.common_git_dir):
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
