"""One maintenance lineage and one structural reframe, never an R-chain."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterator, Mapping

from control_plane.contracts import SHA256_DIGEST, contract_digest, validate_task_id
from control_plane.repository import (
    discover_repository,
    git_common_dir,
    open_private_state_lock,
)


_ACTIVE = frozenset({"open", "reframed"})
_STATUSES = frozenset({"open", "reframed", "blocked"})
_LINEAGE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "lineage_id",
        "stable_runtime_digest",
        "candidate_runtime_digest",
        "status",
        "structural_reframes",
        "last_failure",
        "error_code",
        "created_child",
        "created_at",
        "updated_at",
        "authorizes",
        "lineage_digest",
    }
)
_MAX_LINEAGES = 64
_MAX_BYTES = 65_536


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("E_MAINTENANCE_PATH: maintenance directory is unsafe")
    path.chmod(0o700)
    return path


@contextmanager
def _lock(common_git_dir: Path) -> Iterator[None]:
    descriptor = open_private_state_lock(
        common_git_dir,
        ("codex-control-plane-core", "locks"),
        "maintenance.lock",
        code="E_MAINTENANCE_PATH",
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
            or metadata.st_size > _MAX_BYTES
        ):
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("E_MAINTENANCE_STATE: lineage is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("E_MAINTENANCE_STATE: lineage must be an object")
    unsigned = {key: item for key, item in value.items() if key != "lineage_digest"}
    last_failure = value.get("last_failure")
    status = value.get("status")
    structural_reframes = value.get("structural_reframes")
    valid = (
        set(value) == _LINEAGE_FIELDS
        and type(value.get("schema_version")) is int
        and value.get("schema_version") == 1
        and value.get("kind") == "MaintenanceLineageV1"
        and validate_task_id(value.get("lineage_id"))
        and all(
            isinstance(value.get(name), str)
            and SHA256_DIGEST.fullmatch(str(value.get(name))) is not None
            for name in (
                "stable_runtime_digest",
                "candidate_runtime_digest",
                "lineage_digest",
            )
        )
        and value.get("stable_runtime_digest") != value.get("candidate_runtime_digest")
        and status in _STATUSES
        and type(structural_reframes) is int
        and structural_reframes in {0, 1}
        and (
            last_failure is None
            or (isinstance(last_failure, str) and 0 < len(last_failure) <= 256)
        )
        and (
            (status == "open" and structural_reframes == 0 and last_failure is None)
            or (status in {"reframed", "blocked"} and structural_reframes == 1 and last_failure is not None)
        )
        and (
            value.get("error_code") is None
            if status != "blocked"
            else value.get("error_code") == "E_BOOTSTRAP_REFRAME_LIMIT"
        )
        and value.get("created_child") is False
        and all(
            isinstance(value.get(name), str)
            and 0 < len(str(value.get(name))) <= 64
            and str(value.get(name)).endswith("Z")
            for name in ("created_at", "updated_at")
        )
        and value.get("authorizes") is False
        and value.get("lineage_digest") == contract_digest(unsigned)
    )
    if not valid:
        raise ValueError("E_MAINTENANCE_STATE: lineage binding is invalid")
    return value


def _atomic(path: Path, value: Mapping[str, Any]) -> None:
    parent = _directory(path.parent)
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > _MAX_BYTES:
        raise ValueError("E_MAINTENANCE_STATE: lineage is too large")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        path.chmod(0o600)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


class MaintenanceStore:
    def __init__(self, repository: Path | str) -> None:
        repo = discover_repository(Path(repository))
        self.common_git_dir = git_common_dir(repo)
        self.root = self.common_git_dir / "codex-control-plane-core"
        self.lineages = self.root / "maintenance-lineages"

    def _paths(self) -> list[Path]:
        if not self.lineages.exists():
            return []
        paths = sorted(self.lineages.glob("*.json"))
        if len(paths) > _MAX_LINEAGES:
            raise ValueError("E_MAINTENANCE_BOUNDS: too many lineages")
        return paths

    def _path(self, lineage_id: str) -> Path:
        if not validate_task_id(lineage_id):
            raise ValueError("E_MAINTENANCE_ID: lineage_id is invalid")
        return self.lineages / f"{lineage_id}.json"

    def open(
        self,
        *,
        lineage_id: str,
        stable_runtime_digest: str,
        candidate_runtime_digest: str,
    ) -> dict[str, Any]:
        if any(
            not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
            for value in (stable_runtime_digest, candidate_runtime_digest)
        ):
            raise ValueError("E_MAINTENANCE_DIGEST: runtime digest is invalid")
        if stable_runtime_digest == candidate_runtime_digest:
            raise ValueError("E_MAINTENANCE_SELF: stable and candidate must differ")
        path = self._path(lineage_id)
        with _lock(self.common_git_dir):
            active = [value for value in (_read(item) for item in self._paths()) if value["status"] in _ACTIVE]
            if path.exists():
                existing = _read(path)
                if (
                    existing.get("stable_runtime_digest") != stable_runtime_digest
                    or existing.get("candidate_runtime_digest") != candidate_runtime_digest
                ):
                    raise ValueError("E_MAINTENANCE_REPLAY: lineage binding drifted")
                return existing
            if active:
                raise ValueError("E_MAINTENANCE_LINEAGE_ACTIVE: another lineage is open")
            value: dict[str, Any] = {
                "schema_version": 1,
                "kind": "MaintenanceLineageV1",
                "lineage_id": lineage_id,
                "stable_runtime_digest": stable_runtime_digest,
                "candidate_runtime_digest": candidate_runtime_digest,
                "status": "open",
                "structural_reframes": 0,
                "last_failure": None,
                "error_code": None,
                "created_child": False,
                "created_at": _now(),
                "updated_at": _now(),
                "authorizes": False,
            }
            value["lineage_digest"] = contract_digest(value)
            _atomic(path, value)
            return value

    def structural_failure(self, *, lineage_id: str, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason or len(reason) > 256:
            raise ValueError("E_MAINTENANCE_REASON: bounded reason is required")
        path = self._path(lineage_id)
        with _lock(self.common_git_dir):
            value = _read(path)
            if value["status"] not in _ACTIVE:
                raise ValueError("E_MAINTENANCE_TERMINAL: lineage is not active")
            count = int(value["structural_reframes"])
            if count == 0:
                value["structural_reframes"] = 1
                value["status"] = "reframed"
                value["error_code"] = None
                reframe_allowed = True
            else:
                value["status"] = "blocked"
                value["error_code"] = "E_BOOTSTRAP_REFRAME_LIMIT"
                reframe_allowed = False
            value["last_failure"] = reason
            value["created_child"] = False
            value["updated_at"] = _now()
            value.pop("lineage_digest", None)
            value["lineage_digest"] = contract_digest(value)
            _atomic(path, value)
            return {**value, "reframe_allowed": reframe_allowed}


def local_candidate_status(
    *,
    candidate_runtime_digest: str,
    verifier_runtime_digest: str,
) -> dict[str, Any]:
    if any(
        not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
        for value in (candidate_runtime_digest, verifier_runtime_digest)
    ):
        raise ValueError("E_MAINTENANCE_DIGEST: runtime digest is invalid")
    return {
        "schema_version": 1,
        "kind": "CoreCandidateStatusV1",
        "status": "GREEN_LOCAL",
        "adoption": "PENDING_STABLE_ADOPTION",
        "candidate_runtime_digest": candidate_runtime_digest,
        "verifier_runtime_digest": verifier_runtime_digest,
        "self_certified": False,
        "authorizes": False,
    }
