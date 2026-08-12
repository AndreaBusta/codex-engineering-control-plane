"""Small local lifecycle and bounded read-only legacy inventory."""

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

from control_plane.contracts import SHA256_DIGEST, contract_digest, validate_task_id
from control_plane.repository import (
    discover_repository,
    ensure_private_state_directory,
    git_common_dir,
    open_private_state_lock,
    worktree_git_dir,
)
from control_plane.scopes import normalize_scope


CORE_STATES = (
    "framed",
    "planned",
    "ready",
    "implementing",
    "verifying",
    "review_ready",
    "blocked",
    "closed",
)
_TRANSITIONS = {
    "framed": {"planned", "blocked"},
    "planned": {"ready", "blocked"},
    "ready": {"implementing", "blocked"},
    "implementing": {"verifying", "blocked"},
    "verifying": {"implementing", "review_ready", "blocked"},
    "review_ready": {"implementing", "closed", "blocked"},
    "blocked": set(),
    "closed": set(),
}
_REVISION_ID = re.compile(r"^rev-[0-9a-f]{16}$", re.ASCII)
_TASK_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "revision_id",
        "requested_outcome",
        "state",
        "resume_state",
        "block_reason",
        "repository",
        "worktree",
        "branch",
        "protected_base",
        "head",
        "scope_paths",
        "task_digest",
        "decision_digest",
        "owner_runtime_digest",
        "lease_generation",
        "revision",
        "created_at",
        "updated_at",
        "authorizes",
        "state_digest",
    }
)
_MAX_STATE_BYTES = 131_072
_MAX_LEGACY_FILES = 2_048
_MAX_LEGACY_BYTES = 1_048_576
_LEGACY_RUN_KINDS = frozenset(
    {
        "DeliveryAuditV1",
        "GateReceiptV1",
        "IndependentReviewReceiptV1",
        "OutcomeBindingV1",
        "ReviewCheckSummaryV1",
        "ReviewPacketV1",
        "ReviewResultV1",
        "RollbackPlanV1",
        "RunAttemptV1",
        "RunPlanV1",
        "RunRevisionV1",
        "RunSummaryV1",
        "StableReviewDiffArtifactV1",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_task_id(value: object) -> str:
    if not validate_task_id(value):
        raise ValueError("E_CORE_TASK_ID: task_id is invalid")
    return str(value)


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _locked_runtime_digest(repository: Path) -> str:
    codex = repository / ".codex"
    try:
        directory_metadata = codex.lstat()
        if (
            stat.S_ISLNK(directory_metadata.st_mode)
            or not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(directory_metadata.st_mode) & 0o022
        ):
            raise ValueError
        directory = os.open(
            codex,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            before = os.stat(
                "control-plane.lock",
                dir_fd=directory,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o022
                or before.st_size > 65_536
                or int(getattr(before, "st_flags", 0)) & 0x40000000
            ):
                raise ValueError
            descriptor = os.open(
                "control-plane.lock",
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory,
            )
        finally:
            os.close(directory)
        try:
            opened = os.fstat(descriptor)
            if _file_identity(opened) != _file_identity(before):
                raise ValueError
            chunks: list[bytes] = []
            observed = 0
            while True:
                chunk = os.read(descriptor, min(65_536, 65_537 - observed))
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
                if observed > 65_536:
                    raise ValueError
            after = os.fstat(descriptor)
            if (
                _file_identity(after) != _file_identity(opened)
                or observed != opened.st_size
            ):
                raise ValueError
        finally:
            os.close(descriptor)
        lock = tomllib.loads(b"".join(chunks).decode("utf-8"))
        value = lock["digests"]["runtime"]
    except (
        KeyError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise ValueError("E_CORE_RUNTIME: locked runtime identity is unavailable") from error
    if not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None:
        raise ValueError("E_CORE_RUNTIME: locked runtime identity is invalid")
    return value


def _ensure_private_directory(path: Path) -> Path:
    if not path.exists():
        path.mkdir(parents=True, mode=0o700)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("E_CORE_STATE_PATH: state directory is unsafe")
    path.chmod(0o700)
    return path


def _read_json(path: Path, *, code: str = "E_CORE_STATE") -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > _MAX_STATE_BYTES
        ):
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"{code}_NOT_FOUND: state is unavailable") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{code}_INVALID: state is invalid") from error
    if not isinstance(value, dict):
        raise ValueError(f"{code}_INVALID: state must be an object")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    parent = _ensure_private_directory(path.parent)
    if path.is_symlink():
        raise ValueError("E_CORE_STATE_PATH: state leaf is unsafe")
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > _MAX_STATE_BYTES:
        raise ValueError("E_CORE_STATE_SIZE: state exceeds the bounded size")
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


@contextmanager
def _task_lock(git_dir: Path, task_id: str) -> Iterator[None]:
    descriptor = open_private_state_lock(
        git_dir,
        ("codex-control-plane-core", "locks", "tasks"),
        f"{sha256(task_id.encode()).hexdigest()}.lock",
        code="E_CORE_STATE_PATH",
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def task_allows_writer_lease(state: Mapping[str, Any]) -> bool:
    return (
        state.get("kind") == "CoreTaskStateV1"
        and state.get("requested_outcome") == "local_change"
        and state.get("state") in {"framed", "planned", "ready", "implementing", "verifying"}
    )


class CoreTaskStore:
    def __init__(self, repository: Path | str) -> None:
        self.repository = discover_repository(Path(repository))
        self.git_dir = worktree_git_dir(self.repository)
        self.root = self.git_dir / "codex-control-plane-core"
        self.tasks = self.root / "tasks"

    def _path(self, task_id: object) -> Path:
        return self.tasks / f"{_safe_task_id(task_id)}.json"

    def status(self, task_id: object) -> dict[str, Any]:
        safe_tasks = ensure_private_state_directory(
            self.git_dir,
            ("codex-control-plane-core", "tasks"),
            create=False,
            missing_ok=True,
            code="E_CORE_STATE_PATH",
        )
        value = _read_json(
            (safe_tasks or self.tasks) / f"{_safe_task_id(task_id)}.json"
        )
        self._validate(value, expected_task_id=str(task_id))
        return value

    def start(
        self,
        task_id: object,
        *,
        outcome: str,
        branch: str,
        protected_base: str | None = None,
        head: str,
        task_digest: str,
        decision_digest: str,
        scope_paths: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        value, _ = self.start_with_origin(
            task_id,
            outcome=outcome,
            branch=branch,
            protected_base=protected_base,
            head=head,
            task_digest=task_digest,
            decision_digest=decision_digest,
            scope_paths=scope_paths,
        )
        return value

    def start_with_origin(
        self,
        task_id: object,
        *,
        outcome: str,
        branch: str,
        protected_base: str | None = None,
        head: str,
        task_digest: str,
        decision_digest: str,
        scope_paths: list[str] | tuple[str, ...],
    ) -> tuple[dict[str, Any], bool]:
        task = _safe_task_id(task_id)
        if outcome == "answer":
            return (
                {
                    "schema_version": 1,
                    "kind": "CoreFactsOnlyV1",
                    "task_id": task,
                    "requested_outcome": "answer",
                    "state": "closed",
                    "persisted": False,
                    "authorizes": False,
                },
                False,
            )
        if outcome != "local_change":
            raise ValueError(
                "E_CAPABILITY_QUARANTINED: Core lifecycle accepts only local_change"
            )
        from control_plane.policy import load_policy, validate_policy

        governing = load_policy(
            self.repository / ".codex" / "project-policy.toml"
        )
        issues = validate_policy(governing)
        if issues:
            raise ValueError(f"P_POLICY: {issues[0].message}")
        governing_base = governing["git"]["base_branch"]
        if protected_base is not None and protected_base != governing_base:
            raise ValueError(
                "E_CORE_STATE_BRANCH: protected base differs from governing policy"
            )
        protected_base = governing_base
        if (
            not isinstance(protected_base, str)
            or not protected_base
            or not isinstance(branch, str)
            or not branch
            or branch == protected_base
        ):
            raise ValueError("E_CORE_STATE_BRANCH: a non-protected named branch is required")
        if not isinstance(head, str) or re.fullmatch(r"[0-9a-f]{40}", head) is None:
            raise ValueError("E_CORE_STATE_HEAD: HEAD must be an exact commit")
        for label, digest in (("task", task_digest), ("decision", decision_digest)):
            if not isinstance(digest, str) or SHA256_DIGEST.fullmatch(digest) is None:
                raise ValueError(f"E_CORE_STATE_DIGEST: {label} digest is invalid")
        scopes = sorted({normalize_scope(item) for item in scope_paths})
        if not scopes:
            scopes = ["."]
        assert_no_active_legacy_state(self.repository)
        revision_id = "rev-" + sha256(f"{task_digest}\0{head}".encode()).hexdigest()[:16]
        path = self._path(task)
        with _task_lock(self.git_dir, task):
            if path.exists():
                existing = self.status(task)
                immutable = {
                    "task_id": task,
                    "revision_id": revision_id,
                    "requested_outcome": outcome,
                    "branch": branch,
                    "protected_base": protected_base,
                    "head": head,
                    "task_digest": task_digest,
                    "decision_digest": decision_digest,
                    "scope_paths": scopes,
                }
                if any(existing.get(key) != value for key, value in immutable.items()):
                    raise ValueError("E_CORE_STATE_REPLAY: existing task binding differs")
                return existing, False
            value: dict[str, Any] = {
                "schema_version": 1,
                "kind": "CoreTaskStateV1",
                "task_id": task,
                "revision_id": revision_id,
                "requested_outcome": outcome,
                "state": "framed",
                "resume_state": None,
                "block_reason": None,
                "repository": str(self.repository),
                "worktree": str(self.repository),
                "branch": branch,
                "protected_base": protected_base,
                "head": head,
                "scope_paths": scopes,
                "task_digest": task_digest,
                "decision_digest": decision_digest,
                "owner_runtime_digest": _locked_runtime_digest(self.repository),
                "lease_generation": 0,
                "revision": 0,
                "created_at": _now(),
                "updated_at": _now(),
                "authorizes": False,
            }
            value["state_digest"] = contract_digest(value)
            _atomic_json(path, value)
            return value, True

    def rollback_start(self, state: Mapping[str, Any]) -> None:
        task = _safe_task_id(state.get("task_id"))
        self._validate(state, expected_task_id=task)
        if state.get("revision") != 0 or state.get("lease_generation") != 0:
            raise ValueError("E_CORE_STATE_ROLLBACK: task is no longer pristine")
        path = self._path(task)
        with _task_lock(self.git_dir, task):
            if not path.exists():
                return
            current = _read_json(path)
            self._validate(current, expected_task_id=task)
            if current != dict(state):
                raise ValueError("E_CORE_STATE_ROLLBACK: task changed after start")
            _unlink_durable(path)

    def restore_after_failed_binding(
        self,
        original: Mapping[str, Any],
        *,
        expected_revision_id: str,
        expected_generation: int,
    ) -> None:
        task = _safe_task_id(original.get("task_id"))
        self._validate(original, expected_task_id=task)
        with _task_lock(self.git_dir, task):
            current = self.status(task)
            mutable = {"lease_generation", "revision", "updated_at", "state_digest"}
            original_stable = {
                key: value for key, value in original.items() if key not in mutable
            }
            current_stable = {
                key: value for key, value in current.items() if key not in mutable
            }
            if (
                current_stable != original_stable
                or
                current.get("revision_id") != expected_revision_id
                or current.get("lease_generation") != expected_generation
                or current.get("revision") != int(original.get("revision", -1)) + 1
                or current.get("state") != original.get("state")
            ):
                raise ValueError(
                    "E_CORE_STATE_ROLLBACK: task changed after lease binding"
                )
            _atomic_json(self._path(task), original)

    def transition(
        self,
        task_id: object,
        state: str,
        *,
        reason: str | None = None,
        current_branch: str | None = None,
    ) -> dict[str, Any]:
        task = _safe_task_id(task_id)
        if state not in CORE_STATES:
            raise ValueError("E_CORE_STATE_TRANSITION: target state is unsupported")
        with _task_lock(self.git_dir, task):
            current = self.status(task)
            if current_branch is not None and current.get("branch") != current_branch:
                raise ValueError("E_CORE_STATE_BRANCH: branch binding drifted")
            previous = str(current["state"])
            if state == previous:
                return current
            if state not in _TRANSITIONS[previous]:
                raise ValueError(
                    f"E_CORE_STATE_TRANSITION: illegal transition {previous}->{state}"
                )
            if state == "blocked":
                if not isinstance(reason, str) or not reason or len(reason) > 256:
                    raise ValueError("E_CORE_STATE_BLOCK: bounded reason is required")
                current["resume_state"] = previous
                current["block_reason"] = reason
            else:
                current["resume_state"] = None
                current["block_reason"] = None
            current["state"] = state
            current["revision"] = int(current["revision"]) + 1
            current["updated_at"] = _now()
            current.pop("state_digest", None)
            current["state_digest"] = contract_digest(current)
            _atomic_json(self._path(task), current)
            return current

    def resume(self, task_id: object, *, current_branch: str) -> dict[str, Any]:
        task = _safe_task_id(task_id)
        with _task_lock(self.git_dir, task):
            current = self.status(task)
            if current.get("branch") != current_branch:
                raise ValueError("E_CORE_STATE_BRANCH: branch binding drifted")
            target = current.get("resume_state")
            if current.get("state") != "blocked" or target not in CORE_STATES:
                raise ValueError("E_CORE_STATE_RESUME: task is not resumable")
            current["state"] = target
            current["resume_state"] = None
            current["block_reason"] = None
            current["revision"] = int(current["revision"]) + 1
            current["updated_at"] = _now()
            current.pop("state_digest", None)
            current["state_digest"] = contract_digest(current)
            _atomic_json(self._path(task), current)
            return current

    def close(self, task_id: object, *, current_branch: str) -> dict[str, Any]:
        return self.transition(task_id, "closed", current_branch=current_branch)

    def bind_lease_generation(
        self,
        task_id: object,
        *,
        revision_id: str,
        generation: int,
        expected_state_digest: str,
    ) -> dict[str, Any]:
        task = _safe_task_id(task_id)
        with _task_lock(self.git_dir, task):
            current = self.status(task)
            if current.get("revision_id") != revision_id:
                raise ValueError("E_CORE_LEASE_BINDING: revision drifted")
            if not task_allows_writer_lease(current):
                raise ValueError("E_CORE_LEASE_BINDING: task is not an active writer")
            existing = int(current.get("lease_generation", 0))
            if existing not in {0, generation}:
                raise ValueError("E_CORE_LEASE_BINDING: generation drifted")
            if existing == generation:
                return current
            if current.get("state_digest") != expected_state_digest:
                raise ValueError("E_CORE_LEASE_BINDING: task state changed before binding")
            current["lease_generation"] = generation
            current["revision"] = int(current["revision"]) + 1
            current["updated_at"] = _now()
            current.pop("state_digest", None)
            current["state_digest"] = contract_digest(current)
            _atomic_json(self._path(task), current)
            return current

    @staticmethod
    def _validate(value: Mapping[str, Any], *, expected_task_id: str) -> None:
        unsigned = {key: item for key, item in value.items() if key != "state_digest"}
        state = value.get("state")
        resume_state = value.get("resume_state")
        block_reason = value.get("block_reason")
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
        branch = value.get("branch")
        protected_base = value.get("protected_base")
        repository = value.get("repository")
        worktree = value.get("worktree")
        head = value.get("head")
        task_digest = value.get("task_digest")
        revision_id = value.get("revision_id")
        blocked_binding = (
            isinstance(resume_state, str)
            and resume_state in _TRANSITIONS
            and resume_state not in {"blocked", "closed"}
            and isinstance(block_reason, str)
            and 0 < len(block_reason) <= 256
        )
        unblocked_binding = resume_state is None and block_reason is None
        valid = (
            set(value) == _TASK_STATE_FIELDS
            and type(value.get("schema_version")) is int
            and value.get("schema_version") == 1
            and value.get("kind") == "CoreTaskStateV1"
            and value.get("task_id") == expected_task_id
            and validate_task_id(value.get("task_id"))
            and value.get("requested_outcome") == "local_change"
            and state in CORE_STATES
            and ((state == "blocked" and blocked_binding) or (state != "blocked" and unblocked_binding))
            and isinstance(revision_id, str)
            and _REVISION_ID.fullmatch(revision_id) is not None
            and isinstance(repository, str)
            and Path(repository).is_absolute()
            and isinstance(worktree, str)
            and Path(worktree).is_absolute()
            and repository == worktree
            and isinstance(branch, str)
            and bool(branch)
            and isinstance(protected_base, str)
            and bool(protected_base)
            and branch != protected_base
            and isinstance(head, str)
            and re.fullmatch(r"[0-9a-f]{40}", head) is not None
            and isinstance(raw_scopes, list)
            and bool(raw_scopes)
            and raw_scopes == normalized_scopes
            and all(
                isinstance(value.get(name), str)
                and SHA256_DIGEST.fullmatch(str(value.get(name))) is not None
                for name in ("task_digest", "decision_digest", "owner_runtime_digest")
            )
            and revision_id
            == "rev-"
            + sha256(f"{task_digest}\0{head}".encode()).hexdigest()[:16]
            and type(value.get("lease_generation")) is int
            and int(value.get("lease_generation", -1)) >= 0
            and type(value.get("revision")) is int
            and int(value.get("revision", -1)) >= 0
            and all(
                isinstance(value.get(name), str)
                and 0 < len(str(value.get(name))) <= 64
                and str(value.get(name)).endswith("Z")
                for name in ("created_at", "updated_at")
            )
            and value.get("authorizes") is False
            and isinstance(value.get("state_digest"), str)
            and SHA256_DIGEST.fullmatch(str(value.get("state_digest"))) is not None
            and value.get("state_digest") == contract_digest(unsigned)
        )
        if not valid:
            raise ValueError("E_CORE_STATE_INVALID: state binding is invalid")


def validate_writer_continuation(
    repository: Path | str,
    *,
    task_id: str,
    worktree: str,
    branch: str,
    session_id: str,
    policy_digest: str,
    changed_paths: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Observe one task and its lease under their owning locks."""

    from control_plane.leases import LeaseStore

    store = CoreTaskStore(repository)
    task = _safe_task_id(task_id)
    with _task_lock(store.git_dir, task):
        state = store.status(task)
        try:
            generation = int(state["lease_generation"])
            revision_id = str(state["revision_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                "E_CORE_STATE_CONTINUATION: task lease binding is invalid"
            ) from error
        current_runtime_digest = _locked_runtime_digest(store.repository)
        if state.get("owner_runtime_digest") != current_runtime_digest:
            raise ValueError(
                "E_CORE_RUNTIME: task owner runtime differs from the current lock"
            )
        if (
            not task_allows_writer_lease(state)
            or state.get("worktree") != worktree
            or state.get("branch") != branch
            or generation < 1
        ):
            raise ValueError(
                "E_CORE_STATE_CONTINUATION: task is not an active bound writer"
            )
        lease = LeaseStore(store.repository).validate_continuation(
            task_id=task,
            worktree=worktree,
            branch=branch,
            session_id=session_id,
            policy_digest=policy_digest,
            expected_revision_id=revision_id,
            expected_lease_generation=generation,
            expected_owner_runtime_digest=str(state["owner_runtime_digest"]),
            changed_paths=changed_paths,
        )
        after = store.status(task)
        if after.get("state_digest") != state.get("state_digest"):
            raise ValueError(
                "E_CORE_STATE_CONTINUATION: task changed during validation"
            )
        return {"task": state, "lease": lease, "authorizes": False}


def _legacy_state_roots(repository: Path) -> tuple[Path, ...]:
    current = worktree_git_dir(repository)
    common = git_common_dir(repository)
    roots = {current / "codex-control-plane", common / "codex-control-plane"}
    worktrees = common / "worktrees"
    try:
        if worktrees.exists():
            metadata = worktrees.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("E_LEGACY_STATE_UNKNOWN: linked worktree root is unsafe")
            children = tuple(worktrees.iterdir())
        else:
            children = ()
    except OSError as error:
        raise ValueError("E_LEGACY_STATE_UNKNOWN: linked worktrees are unavailable") from error
    if len(children) > 256:
        raise ValueError("E_LEGACY_STATE_BOUNDS: too many linked worktrees")
    for child in children:
        if child.is_dir() and not child.is_symlink():
            roots.add(child / "codex-control-plane")
    return tuple(sorted(roots))


def _legacy_json_paths(
    root: Path,
    directory_name: str,
    *,
    recursive: bool,
    counters: dict[str, int],
) -> list[Path]:
    directory = root / directory_name
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy directory is unavailable") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy directory is unsafe")
    paths: list[Path] = []

    def visit(current: Path, depth: int) -> None:
        if depth > 16:
            raise ValueError("E_LEGACY_STATE_BOUNDS: legacy directory is too deep")
        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name)
        except OSError as error:
            raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy directory is unavailable") from error
        try:
            for entry in entries:
                counters["entries"] += 1
                if counters["entries"] > _MAX_LEGACY_FILES:
                    raise ValueError("E_LEGACY_STATE_BOUNDS: legacy inventory is too large")
                try:
                    leaf = entry.stat(follow_symlinks=False)
                except OSError as error:
                    raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy entry is unavailable") from error
                path = Path(entry.path)
                if stat.S_ISLNK(leaf.st_mode):
                    raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy entry is a symlink")
                if stat.S_ISDIR(leaf.st_mode):
                    if recursive:
                        visit(path, depth + 1)
                    continue
                if not stat.S_ISREG(leaf.st_mode):
                    raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy entry is unsafe")
                if path.suffix == ".json":
                    paths.append(path)
        finally:
            for entry in entries:
                entry.close() if hasattr(entry, "close") else None

    visit(directory, 0)
    return paths


def _read_legacy_payload(
    path: Path, *, counters: dict[str, int]
) -> Mapping[str, Any] | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        before = path.lstat()
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy leaf is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_nlink,
            value.st_uid,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > _MAX_STATE_BYTES
            or identity(opened) != identity(before)
        ):
            raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy leaf is unsafe")
        counters["bytes"] += opened.st_size
        if counters["bytes"] > _MAX_LEGACY_BYTES:
            raise ValueError("E_LEGACY_STATE_BOUNDS: legacy inventory is too large")
        payload = bytearray()
        while len(payload) <= _MAX_STATE_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, _MAX_STATE_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) != opened.st_size
            or len(payload) > _MAX_STATE_BYTES
            or identity(after) != identity(opened)
        ):
            raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy leaf changed during read")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(bytes(payload).decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None
    return value if isinstance(value, Mapping) else None


def _contains_remote_unknown(value: object) -> bool:
    pending: list[tuple[object, int, str | None]] = [(value, 0, None)]
    observed = 0
    while pending:
        current, depth, owning_key = pending.pop()
        observed += 1
        if observed > 4_096 or depth > 32:
            return True
        if isinstance(current, Mapping):
            if current.get("status") == "UNKNOWN" or current.get("remote_status") == "UNKNOWN":
                return True
            if owning_key is not None and owning_key.startswith("pending_") and (
                current.get("phase") == "observe_only"
                or current.get("retry") == "observe_only"
            ):
                return True
            pending.extend(
                (child, depth + 1, str(key))
                for key, child in current.items()
            )
        elif isinstance(current, list):
            pending.extend((child, depth + 1, owning_key) for child in current)
    return False


def _legacy_task_contract(
    path: Path, payload: Mapping[str, Any] | None
) -> tuple[str | None, str, bool, bool]:
    task_id = path.stem if validate_task_id(path.stem) else None
    if (
        payload is None
        or payload.get("schema_version") != 1
        or payload.get("task_id") != task_id
        or not isinstance(payload.get("state"), str)
        or not isinstance(payload.get("outcome"), str)
        or not isinstance(payload.get("branch"), str)
        or not isinstance(payload.get("task_digest"), str)
        or SHA256_DIGEST.fullmatch(str(payload.get("task_digest"))) is None
        or not isinstance(payload.get("decision_digest"), str)
        or SHA256_DIGEST.fullmatch(str(payload.get("decision_digest"))) is None
        or not isinstance(payload.get("owner_runtime_digest"), str)
        or SHA256_DIGEST.fullmatch(str(payload.get("owner_runtime_digest"))) is None
    ):
        return task_id, "UNKNOWN", False, True
    state = str(payload["state"])
    remote_unknown = _contains_remote_unknown(payload)
    terminal = state == "closed" or (
        state == "blocked"
        and payload.get("resume_forbidden") is True
        and payload.get("resume_state") is None
    )
    return task_id, state, True, remote_unknown or not terminal


def _legacy_run_contract(
    root: Path,
    path: Path,
    payload: Mapping[str, Any] | None,
    tasks: Mapping[str, Mapping[str, Any]],
    leased_tasks: frozenset[str],
) -> tuple[str | None, str, bool]:
    relative = path.relative_to(root)
    path_task_id = relative.parts[1] if len(relative.parts) > 2 else None
    task_id = path_task_id if validate_task_id(path_task_id) else None
    valid = (
        payload is not None
        and payload.get("schema_version") == 1
        and payload.get("kind") in _LEGACY_RUN_KINDS
        and payload.get("task_id") == task_id
    )
    if not valid:
        return task_id, "UNKNOWN", True
    state = payload.get("state")
    if not isinstance(state, str):
        status = payload.get("status")
        state = status if isinstance(status, str) else "historical"
    owner = tasks.get(str(task_id))
    active = (
        _contains_remote_unknown(payload)
        or owner is None
        or owner.get("contract_status") != "valid"
        or bool(owner.get("active"))
        or task_id in leased_tasks
    )
    return task_id, state, active


def inventory_legacy_state(repository: Path | str) -> dict[str, Any]:
    """Observe old runtime state without rewriting, deleting, or resuming it."""

    repo = discover_repository(Path(repository))
    records: list[dict[str, Any]] = []
    counters = {"entries": 0, "bytes": 0}
    for root in _legacy_state_roots(repo):
        if not root.exists():
            continue
        try:
            metadata = root.lstat()
        except OSError as error:
            raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy root is unavailable") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("E_LEGACY_STATE_UNKNOWN: legacy root is unsafe")
        paths_by_kind = {
            "task": _legacy_json_paths(root, "tasks", recursive=False, counters=counters),
            "lease": _legacy_json_paths(root, "leases", recursive=False, counters=counters),
            "delivery_lease": _legacy_json_paths(
                root, "delivery-leases", recursive=False, counters=counters
            ),
            "run": _legacy_json_paths(root, "runs", recursive=True, counters=counters),
            "remote_unknown": _legacy_json_paths(
                root,
                "base-refresh-observations",
                recursive=True,
                counters=counters,
            ),
        }
        tasks: dict[str, dict[str, Any]] = {}
        leased_tasks: set[str] = set()
        for path in paths_by_kind["task"]:
            payload = _read_legacy_payload(path, counters=counters)
            task_id, state, valid, active = _legacy_task_contract(path, payload)
            record = {
                "origin": "legacy",
                "resumable": False,
                "kind": "task",
                "task_id": task_id,
                "path": str(path),
                "state": state,
                "remote_status": (
                    payload.get("remote_status") if payload is not None else "UNKNOWN"
                ),
                "contract_status": "valid" if valid else "unknown",
                "active": bool(active),
            }
            records.append(record)
            if task_id is not None:
                tasks[task_id] = record
        for kind in ("lease", "delivery_lease"):
            for path in paths_by_kind[kind]:
                payload = _read_legacy_payload(path, counters=counters)
                task_id = payload.get("task_id") if payload is not None else None
                valid = validate_task_id(task_id)
                if valid:
                    leased_tasks.add(str(task_id))
                records.append(
                    {
                        "origin": "legacy",
                        "resumable": False,
                        "kind": kind,
                        "task_id": task_id if valid else None,
                        "path": str(path),
                        "state": (
                            str(payload.get("state"))
                            if payload is not None and isinstance(payload.get("state"), str)
                            else "UNKNOWN"
                        ),
                        "remote_status": (
                            payload.get("remote_status") if payload is not None else "UNKNOWN"
                        ),
                        "contract_status": "valid" if valid else "unknown",
                        "active": True,
                    }
                )
        for path in paths_by_kind["run"]:
            payload = _read_legacy_payload(path, counters=counters)
            task_id, state, active = _legacy_run_contract(
                root,
                path,
                payload,
                tasks,
                frozenset(leased_tasks),
            )
            records.append(
                {
                    "origin": "legacy",
                    "resumable": False,
                    "kind": "run",
                    "task_id": task_id,
                    "path": str(path),
                    "state": state,
                    "remote_status": (
                        payload.get("remote_status") if payload is not None else "UNKNOWN"
                    ),
                    "contract_status": "valid" if state != "UNKNOWN" else "unknown",
                    "active": bool(active),
                }
            )
        for path in paths_by_kind["remote_unknown"]:
            payload = _read_legacy_payload(path, counters=counters)
            if payload is not None and not _contains_remote_unknown(payload):
                continue
            records.append(
                {
                    "origin": "legacy",
                    "resumable": False,
                    "kind": "remote_unknown",
                    "task_id": None,
                    "path": str(path),
                    "state": "UNKNOWN",
                    "remote_status": "UNKNOWN",
                    "contract_status": "valid" if payload is not None else "unknown",
                    "active": True,
                }
            )
    return {
        "schema_version": 1,
        "kind": "LegacyStateInventoryV1",
        "origin": "legacy",
        "resumable": False,
        "active": any(record["active"] for record in records),
        "records": records,
        "authorizes": False,
    }


def assert_no_active_legacy_state(repository: Path | str) -> dict[str, Any]:
    inventory = inventory_legacy_state(repository)
    if inventory["active"]:
        raise ValueError(
            "E_ACTIVE_LEGACY_STATE: close or release legacy state with its owning runtime"
        )
    return inventory
