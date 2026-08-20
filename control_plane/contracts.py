"""Canonical, dependency-free helpers for versioned control-plane contracts."""

from __future__ import annotations

from hashlib import sha256
import heapq
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any
from typing import Mapping


def canonical_json(value: Any) -> str:
    """Return a stable JSON representation suitable for hashing and receipts."""

    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def contract_digest(value: Any) -> str:
    """Hash a contract without depending on dict insertion order."""

    return f"sha256:{sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


TASK_INTENTS = frozenset(
    {
        "explain",
        "audit",
        "plan",
        "diagnose",
        "implement",
        "review",
        "integrate",
        "release",
        "operate",
    }
)
TASK_OUTCOMES = frozenset(
    {"answer", "local_change", "commit", "pull_request", "integration", "release"}
)
TASK_PHASES = frozenset(
    {
        "frame",
        "research",
        "plan",
        "implement",
        "verify",
        "review",
        "integrate",
        "release",
        "observe",
        "operate",
    }
)
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
STABLE_PAUSE_STATUSES = (
    "SAFE_PAUSE_ACTIVE",
    "SAFE_PAUSE_TERMINAL",
    "UNSAFE_PAUSE",
    "UNKNOWN",
)
STABLE_PAUSE_CHECK_VALUES = ("PASS", "FAIL", "UNKNOWN")
STABLE_PAUSE_MUTEX_VALUES = ("free", "held", "absent", "unknown")
STABLE_PAUSE_LEASE_VALUES = ("active", "absent", "unknown")
STABLE_PAUSE_ISSUE_CODES = (
    "E_STABLE_PAUSE_REPOSITORY",
    "E_STABLE_PAUSE_SNAPSHOT_DRIFT",
    "E_STABLE_PAUSE_LIFECYCLE",
    "E_STABLE_PAUSE_OPERATION_ACTIVE",
    "E_STABLE_PAUSE_RESIDUE",
    "E_STABLE_PAUSE_BOUNDS",
)
STABLE_PAUSE_ISSUE_DIMENSIONS = (
    "repository",
    "snapshot",
    "lifecycle",
    "operation",
    "residue",
    "bounds",
)
TASK_EFFECTS = frozenset(
    {
        "local_read",
        "local_write",
        "network_read",
        "commit",
        "remote_write",
        "pull_request",
        "integration",
        "release",
        "deploy",
        "publish",
        "destructive",
        "credential_access",
    }
)
PROVENANCE = frozenset(
    {"user_explicit", "model_inference", "project_policy", "external_untrusted"}
)
TASK_SIGNALS = frozenset(
    {
        "multi_file",
        "regression_risk",
        "architecture_change",
        "auth",
        "authorization",
        "payments",
        "private_data",
        "migration",
        "secrets",
        "destructive",
        "production",
        "release",
        "testflight",
        "independent_work",
        "follow_up",
        "long_running",
        "multiple_milestones",
        "unclear_outcome",
        "cross_system",
        "security",
        "privacy",
        "data_loss",
        "irreversible",
        "external_effect",
    }
)
TASK_KEYS = frozenset(
    {
        "schema_version",
        "task_id",
        "objective",
        "intent",
        "phase",
        "requested_outcome",
        "goals",
        "domains",
        "signals",
        "scope_paths",
        "risk",
        "risk_provenance",
        "effects",
        "explicit_resources",
        "excluded_resources",
    }
)
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$", re.ASCII)
DOMAIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}$", re.ASCII)
RESOURCE_ID = DOMAIN_ID
SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
GOAL_KEYS = frozenset({"id", "summary", "domains", "depends_on"})
RISK_AXES = frozenset(
    {
        "uncertainty",
        "blast_radius",
        "irreversibility",
        "verification_complexity",
    }
)
AUTHORIZATION_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "grant_id",
        "task_digest",
        "session_id",
        "allowed_effects",
        "scope_paths",
    }
)

_STABLE_PAUSE_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "scope",
        "status",
        "repository",
        "lifecycle",
        "control_plane_state",
        "checks",
        "issues",
        "checkpoint_digest",
        "authorizes",
    }
)
_STABLE_PAUSE_REPOSITORY_KEYS = frozenset(
    {
        "root",
        "common_git_dir",
        "branch",
        "head",
        "status_digest",
        "worktree_digest",
        "staged_count",
        "unstaged_count",
        "untracked_count",
        "diff_check",
    }
)
_STABLE_PAUSE_LIFECYCLE_KEYS = frozenset(
    {
        "task_id",
        "task_state",
        "task_state_digest",
        "lease_state",
        "lease_digest",
        "owner_runtime_digest",
    }
)
_STABLE_PAUSE_CONTROL_KEYS = frozenset(
    {
        "adoption_mutex",
        "verification_mutex",
        "task_mutex",
        "lease_mutex",
        "residue_count",
        "residue_digest",
    }
)
_STABLE_PAUSE_CHECK_KEYS = frozenset(
    {
        "repository_identity",
        "snapshot_stability",
        "lifecycle_binding",
        "mutex_quiescence",
        "owned_residue",
    }
)
_STABLE_PAUSE_ISSUE_KEYS = frozenset({"code", "dimension"})
_STABLE_PAUSE_ISSUE_DIMENSION_BY_CODE = {
    code: dimension
    for code, dimension in zip(
        STABLE_PAUSE_ISSUE_CODES,
        STABLE_PAUSE_ISSUE_DIMENSIONS,
        strict=True,
    )
}
_STABLE_PAUSE_MAX_BYTES = 4096
_STABLE_PAUSE_MAX_DEPTH = 32
_STABLE_PAUSE_MAX_ITEMS = 4096
_STABLE_PAUSE_MAX_STRING = 8192
_STABLE_PAUSE_MAX_ISSUES = 8
_STABLE_PAUSE_HEAD = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_STABLE_PAUSE_BRANCH = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$",
    re.ASCII,
)
_STABLE_PAUSE_CHECKPOINT_DOMAIN = b"control-plane-stable-pause-v1\0"

_ACTIVE_ADOPTION_JOURNAL_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "plan_digest",
        "install_digest",
        "state",
        "source_manifest_digest",
        "target_binding",
        "before_snapshot_digest",
        "managed_parent_directories",
        "managed_repository_scan",
        "lifecycle_lock",
        "verification_lock",
        "created_directories",
        "published_records",
        "target_lock_record",
        "prior_git_config",
        "rollback_records",
        "state_digest",
        "authorizes",
    }
)
_ACTIVE_ADOPTION_TARGET_KEYS = frozenset(
    {
        "repository_id",
        "common_dir_id",
        "worktree_id",
        "branch",
        "head",
        "policy_digest",
        "registry_digest",
        "adoption_lifecycle",
    }
)
_ACTIVE_ADOPTION_PARENT_PATHS = (
    ".codex",
    "control_plane",
    "scripts",
    ".codex/git-hooks",
    ".codex/hooks",
)
_ACTIVE_ADOPTION_SCAN = {
    "contract": "managed-repositories-v1",
    "nested_repositories_absent": True,
    "gitlinks_absent": True,
}
_ACTIVE_ADOPTION_RECORD_KEYS = frozenset(
    {"path", "role", "sha256", "git_mode", "size_bytes"}
)
_ACTIVE_ADOPTION_ROLLBACK_KEYS = frozenset(
    {*_ACTIVE_ADOPTION_RECORD_KEYS, "before"}
)
_ACTIVE_ADOPTION_LOCK_KEYS = frozenset(
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
_ACTIVE_ADOPTION_DIRECTORY_KEYS = frozenset(
    {"path", "device", "inode", "mode", "uid", "gid", "flags"}
)
_ACTIVE_ADOPTION_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ACTIVE_ADOPTION_BRANCH = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$",
    re.ASCII,
)
_ACTIVE_ADOPTION_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$", re.ASCII)
_ACTIVE_ADOPTION_MAX_BYTES = 1024 * 1024
_ACTIVE_ADOPTION_MAX_DEPTH = 32
_ACTIVE_ADOPTION_MAX_ITEMS = 4096
_ACTIVE_ADOPTION_MAX_STRING = 8192
_ACTIVE_ADOPTION_MAX_RECORDS = 256


def _active_adoption_closed_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("active adoption journal has duplicate fields")
        value[key] = item
    return value


def _active_adoption_reject_constant(_: str) -> None:
    raise ValueError("active adoption journal contains a non-finite number")


def _active_adoption_bounded(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    items = 0
    while stack:
        item, depth = stack.pop()
        items += 1
        if items > _ACTIVE_ADOPTION_MAX_ITEMS:
            raise ValueError("active adoption journal exceeds item bounds")
        if depth > _ACTIVE_ADOPTION_MAX_DEPTH:
            raise ValueError("active adoption journal exceeds depth bounds")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or len(key.encode("utf-8")) > _ACTIVE_ADOPTION_MAX_STRING
                ):
                    raise ValueError("active adoption journal has an invalid key")
                if key == "authorizes" and child is not False:
                    raise ValueError("active adoption journal cannot authorize")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item.encode("utf-8")) > _ACTIVE_ADOPTION_MAX_STRING:
                raise ValueError("active adoption journal has an oversized string")
        elif item is None or isinstance(item, bool) or type(item) is int:
            pass
        else:
            raise ValueError("active adoption journal has an unsupported value")


def _active_adoption_exact(value: Any, keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("active adoption journal fields are not exact")
    return value


def _active_adoption_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and SHA256_DIGEST.fullmatch(value) is not None
    )


def _active_adoption_identity(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(item) is int and item >= 0 for item in value)
    )


def _active_adoption_safe_path(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1024
        or "\\" in value
        or "\x00" in value
    ):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _active_adoption_lock(value: Any, path: str) -> dict[str, Any]:
    record = _active_adoption_exact(value, _ACTIVE_ADOPTION_LOCK_KEYS)
    integer_fields = _ACTIVE_ADOPTION_LOCK_KEYS - {"path"}
    if (
        record.get("path") != path
        or any(type(record.get(key)) is not int or record[key] < 0 for key in integer_fields)
        or record.get("mode") != 0o600
        or record.get("links") != 1
        or record.get("size") != 0
        or int(record.get("flags", 0)) & 0x40000000
    ):
        raise ValueError("active adoption journal lock binding is invalid")
    return dict(record)


def _active_adoption_verification(value: Any) -> None:
    binding = _active_adoption_exact(value, frozenset({"directory", "file"}))
    directory = _active_adoption_exact(
        binding.get("directory"),
        _ACTIVE_ADOPTION_DIRECTORY_KEYS,
    )
    if (
        directory.get("path") != "codex-control-plane-core/locks"
        or any(
            type(directory.get(key)) is not int or directory[key] < 0
            for key in _ACTIVE_ADOPTION_DIRECTORY_KEYS - {"path"}
        )
        or directory.get("mode") != 0o700
        or int(directory.get("flags", 0)) & 0x40000000
    ):
        raise ValueError("active adoption journal verification directory is invalid")
    _active_adoption_lock(
        binding.get("file"),
        "codex-control-plane-core/locks/verification.lock",
    )


def _active_adoption_record(value: Any, *, rollback: bool) -> str:
    keys = _ACTIVE_ADOPTION_ROLLBACK_KEYS if rollback else _ACTIVE_ADOPTION_RECORD_KEYS
    record = _active_adoption_exact(value, keys)
    path = record.get("path")
    size = record.get("size_bytes")
    if (
        not _active_adoption_safe_path(path)
        or not isinstance(record.get("role"), str)
        or _ACTIVE_ADOPTION_ROLE.fullmatch(record["role"]) is None
        or not _active_adoption_digest(record.get("sha256"))
        or record.get("git_mode") not in {"100644", "100755"}
        or type(size) is not int
        or not 0 <= size <= _ACTIVE_ADOPTION_MAX_BYTES
        or (rollback and record.get("before") != "absent")
    ):
        raise ValueError("active adoption journal managed record is invalid")
    return str(path)


def validate_active_adoption_journal(value: Any) -> dict[str, Any]:
    journal = _active_adoption_exact(value, _ACTIVE_ADOPTION_JOURNAL_KEYS)
    if (
        type(journal.get("schema_version")) is not int
        or journal.get("schema_version") != 1
        or journal.get("kind") != "CoreAdoptionJournalV1"
        or journal.get("state") != "active"
        or journal.get("authorizes") is not False
    ):
        raise ValueError("active adoption journal envelope is invalid")
    for key in (
        "plan_digest",
        "install_digest",
        "source_manifest_digest",
        "before_snapshot_digest",
    ):
        if not _active_adoption_digest(journal.get(key)):
            raise ValueError("active adoption journal digest binding is invalid")

    target = _active_adoption_exact(
        journal.get("target_binding"),
        _ACTIVE_ADOPTION_TARGET_KEYS,
    )
    if any(
        not _active_adoption_identity(target.get(key))
        for key in ("repository_id", "common_dir_id", "worktree_id")
    ):
        raise ValueError("active adoption journal target identity is invalid")
    branch = target.get("branch")
    if (
        not isinstance(branch, str)
        or _ACTIVE_ADOPTION_BRANCH.fullmatch(branch) is None
        or ".." in branch
        or branch.endswith("/")
        or not isinstance(target.get("head"), str)
        or _ACTIVE_ADOPTION_COMMIT.fullmatch(target["head"]) is None
        or not _active_adoption_digest(target.get("policy_digest"))
        or not _active_adoption_digest(target.get("registry_digest"))
        or target.get("adoption_lifecycle") != "journal-bound-v1"
    ):
        raise ValueError("active adoption journal target binding is invalid")

    parents = journal.get("managed_parent_directories")
    if not isinstance(parents, list) or len(parents) != len(_ACTIVE_ADOPTION_PARENT_PATHS):
        raise ValueError("active adoption journal parent bindings are invalid")
    observed_parent_paths: list[str] = []
    for item in parents:
        if not isinstance(item, Mapping):
            raise ValueError("active adoption journal parent binding is invalid")
        state = item.get("state")
        keys = (
            frozenset({"path", "state"})
            if state == "absent"
            else frozenset({"path", "state", "identity", "mode"})
        )
        parent = _active_adoption_exact(item, keys)
        path = parent.get("path")
        if not isinstance(path, str):
            raise ValueError("active adoption journal parent path is invalid")
        observed_parent_paths.append(path)
        if state == "present":
            mode = parent.get("mode")
            if (
                not _active_adoption_identity(parent.get("identity"))
                or type(mode) is not int
                or not 0 <= mode <= 0o7777
                or mode & 0o022
            ):
                raise ValueError("active adoption journal parent identity is invalid")
        elif state != "absent":
            raise ValueError("active adoption journal parent state is invalid")
    if observed_parent_paths != list(_ACTIVE_ADOPTION_PARENT_PATHS):
        raise ValueError("active adoption journal parent paths are not canonical")
    if journal.get("managed_repository_scan") != _ACTIVE_ADOPTION_SCAN:
        raise ValueError("active adoption journal repository scan is invalid")

    _active_adoption_lock(
        journal.get("lifecycle_lock"),
        "codex-control-plane-core/adoption.lock",
    )
    _active_adoption_verification(journal.get("verification_lock"))

    created = journal.get("created_directories")
    if not isinstance(created, list) or len(created) > _ACTIVE_ADOPTION_MAX_RECORDS:
        raise ValueError("active adoption journal created directories are invalid")
    created_paths: list[str] = []
    for item in created:
        record = _active_adoption_exact(
            item,
            frozenset({"path", "mode", "identity"}),
        )
        path = record.get("path")
        identity = record.get("identity")
        if (
            not _active_adoption_safe_path(path)
            or record.get("mode") != 0o755
            or (identity is not None and not _active_adoption_identity(identity))
        ):
            raise ValueError("active adoption journal created directory is invalid")
        created_paths.append(str(path))
    if (
        created_paths != sorted(created_paths, key=lambda item: (item.count("/"), item))
        or len(created_paths) != len(set(created_paths))
    ):
        raise ValueError("active adoption journal created directories are not exact")

    published = journal.get("published_records")
    if not isinstance(published, list) or len(published) > _ACTIVE_ADOPTION_MAX_RECORDS:
        raise ValueError("active adoption journal published records are invalid")
    published_paths = [
        _active_adoption_record(item, rollback=False) for item in published
    ]
    if published_paths != sorted(published_paths) or len(published_paths) != len(set(published_paths)):
        raise ValueError("active adoption journal published records are not exact")

    target_lock_path = _active_adoption_record(
        journal.get("target_lock_record"),
        rollback=False,
    )
    if target_lock_path != ".codex/control-plane.lock":
        raise ValueError("active adoption journal activation record is invalid")
    if journal.get("prior_git_config") != {"core.hooksPath": None}:
        raise ValueError("active adoption journal prior Git config is invalid")

    rollback = journal.get("rollback_records")
    if (
        not isinstance(rollback, list)
        or not rollback
        or len(rollback) > _ACTIVE_ADOPTION_MAX_RECORDS
    ):
        raise ValueError("active adoption journal rollback records are invalid")
    rollback_paths = [
        _active_adoption_record(item, rollback=True) for item in rollback
    ]
    if (
        rollback_paths != sorted(rollback_paths)
        or len(rollback_paths) != len(set(rollback_paths))
        or target_lock_path not in rollback_paths
    ):
        raise ValueError("active adoption journal rollback records are not exact")

    supplied = journal.get("state_digest")
    unsigned = {key: item for key, item in journal.items() if key != "state_digest"}
    if not _active_adoption_digest(supplied) or supplied != contract_digest(unsigned):
        raise ValueError("active adoption journal digest is invalid")
    return dict(journal)


def load_active_adoption_journal(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 <= len(payload) <= _ACTIVE_ADOPTION_MAX_BYTES:
        raise ValueError("active adoption journal payload is invalid")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_active_adoption_closed_pairs,
            parse_constant=_active_adoption_reject_constant,
        )
        _active_adoption_bounded(value)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("active adoption journal JSON is invalid") from error
    return validate_active_adoption_journal(value)


def _stable_pause_closed_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("stable pause observation has duplicate fields")
        value[key] = item
    return value


def _stable_pause_reject_constant(_: str) -> None:
    raise ValueError("stable pause observation contains a non-finite number")


def _stable_pause_bounded(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    observed = 0
    while stack:
        item, depth = stack.pop()
        observed += 1
        if observed > _STABLE_PAUSE_MAX_ITEMS:
            raise ValueError("stable pause observation exceeds item bounds")
        if depth > _STABLE_PAUSE_MAX_DEPTH:
            raise ValueError("stable pause observation exceeds depth bounds")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or len(key.encode("utf-8")) > _STABLE_PAUSE_MAX_STRING
                ):
                    raise ValueError("stable pause observation has an invalid key")
                if key == "authorizes" and child is not False:
                    raise ValueError("stable pause observation cannot authorize")
                stack.append((child, depth + 1))
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item.encode("utf-8")) > _STABLE_PAUSE_MAX_STRING:
                raise ValueError("stable pause observation has an oversized string")
        elif item is None or isinstance(item, bool) or type(item) is int:
            pass
        else:
            raise ValueError("stable pause observation has an unsupported value")


def _stable_pause_exact(value: Any, keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("stable pause observation fields are not exact")
    return value


def _stable_pause_digest(value: Any, *, nullable: bool = False) -> bool:
    return (nullable and value is None) or (
        isinstance(value, str) and SHA256_DIGEST.fullmatch(value) is not None
    )


def _stable_pause_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("/")
        and 1 < len(value.encode("utf-8")) <= 4096
        and "\0" not in value
        and "\n" not in value
        and "\r" not in value
    )


def stable_pause_checkpoint_digest(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping) or value.get("authorizes") is not False:
        raise ValueError("stable pause observation cannot authorize")
    unsigned = dict(value)
    unsigned.pop("checkpoint_digest", None)
    _stable_pause_bounded(unsigned)
    payload = _STABLE_PAUSE_CHECKPOINT_DOMAIN + canonical_json(unsigned).encode(
        "utf-8"
    )
    return f"sha256:{sha256(payload).hexdigest()}"


def derive_stable_pause_status(
    checks: Mapping[str, Any], lifecycle_class: str
) -> str:
    closed = _stable_pause_exact(checks, _STABLE_PAUSE_CHECK_KEYS)
    if any(value not in STABLE_PAUSE_CHECK_VALUES for value in closed.values()):
        raise ValueError("stable pause checks are invalid")
    if lifecycle_class not in {"active", "terminal", "contradiction", "unknown"}:
        raise ValueError("stable pause lifecycle classification is invalid")
    if "FAIL" in closed.values() or lifecycle_class == "contradiction":
        return "UNSAFE_PAUSE"
    if "UNKNOWN" in closed.values() or lifecycle_class == "unknown":
        return "UNKNOWN"
    if lifecycle_class == "active":
        return "SAFE_PAUSE_ACTIVE"
    return "SAFE_PAUSE_TERMINAL"


def validate_stable_pause_observation(value: Any) -> dict[str, Any]:
    _stable_pause_bounded(value)
    observation = _stable_pause_exact(value, _STABLE_PAUSE_ROOT_KEYS)
    if (
        type(observation.get("schema_version")) is not int
        or observation.get("schema_version") != 1
        or observation.get("kind") != "StablePauseObservationV1"
        or observation.get("scope") != "core-owned-local-state"
        or observation.get("status") not in STABLE_PAUSE_STATUSES
        or observation.get("authorizes") is not False
    ):
        raise ValueError("stable pause observation envelope is invalid")

    repository = _stable_pause_exact(
        observation.get("repository"),
        _STABLE_PAUSE_REPOSITORY_KEYS,
    )
    if (
        not _stable_pause_path(repository.get("root"))
        or not _stable_pause_path(repository.get("common_git_dir"))
        or not isinstance(repository.get("branch"), str)
        or _STABLE_PAUSE_BRANCH.fullmatch(str(repository["branch"])) is None
        or ".." in str(repository["branch"])
        or str(repository["branch"]).endswith("/")
        or not isinstance(repository.get("head"), str)
        or _STABLE_PAUSE_HEAD.fullmatch(str(repository["head"])) is None
        or not _stable_pause_digest(repository.get("status_digest"), nullable=True)
        or not _stable_pause_digest(repository.get("worktree_digest"), nullable=True)
        or (repository.get("status_digest") is None)
        != (repository.get("worktree_digest") is None)
        or any(
            type(repository.get(key)) is not int
            or not 0 <= int(repository[key]) <= 20_000
            for key in ("staged_count", "unstaged_count", "untracked_count")
        )
        or repository.get("diff_check") not in STABLE_PAUSE_CHECK_VALUES
    ):
        raise ValueError("stable pause repository binding is invalid")

    lifecycle = _stable_pause_exact(
        observation.get("lifecycle"),
        _STABLE_PAUSE_LIFECYCLE_KEYS,
    )
    task_state = lifecycle.get("task_state")
    lease_state = lifecycle.get("lease_state")
    if (
        not validate_task_id(lifecycle.get("task_id"))
        or task_state not in (*CORE_STATES, "unknown")
        or not _stable_pause_digest(
            lifecycle.get("task_state_digest"), nullable=True
        )
        or lease_state not in STABLE_PAUSE_LEASE_VALUES
        or not _stable_pause_digest(lifecycle.get("lease_digest"), nullable=True)
        or not _stable_pause_digest(
            lifecycle.get("owner_runtime_digest"), nullable=True
        )
        or (lease_state == "active")
        != (lifecycle.get("lease_digest") is not None)
        or (
            task_state == "unknown"
            and (
                lifecycle.get("task_state_digest") is not None
                or lifecycle.get("owner_runtime_digest") is not None
            )
        )
        or (
            task_state != "unknown"
            and (
                lifecycle.get("task_state_digest") is None
                or lifecycle.get("owner_runtime_digest") is None
            )
        )
    ):
        raise ValueError("stable pause lifecycle binding is invalid")

    control = _stable_pause_exact(
        observation.get("control_plane_state"),
        _STABLE_PAUSE_CONTROL_KEYS,
    )
    if (
        any(
            control.get(key) not in STABLE_PAUSE_MUTEX_VALUES
            for key in (
                "adoption_mutex",
                "verification_mutex",
                "task_mutex",
                "lease_mutex",
            )
        )
        or type(control.get("residue_count")) is not int
        or not 0 <= int(control["residue_count"]) <= 4096
        or not _stable_pause_digest(control.get("residue_digest"), nullable=True)
    ):
        raise ValueError("stable pause control-plane state is invalid")

    checks = _stable_pause_exact(observation.get("checks"), _STABLE_PAUSE_CHECK_KEYS)
    if any(value not in STABLE_PAUSE_CHECK_VALUES for value in checks.values()):
        raise ValueError("stable pause checks are invalid")
    if (control.get("residue_digest") is None) != (
        checks.get("owned_residue") == "UNKNOWN"
    ):
        raise ValueError("stable pause residue binding is invalid")
    if (repository.get("status_digest") is None) and not any(
        checks.get(key) == "UNKNOWN"
        for key in ("repository_identity", "snapshot_stability")
    ):
        raise ValueError("stable pause snapshot binding is invalid")

    issues = observation.get("issues")
    if not isinstance(issues, list) or len(issues) > _STABLE_PAUSE_MAX_ISSUES:
        raise ValueError("stable pause issues are invalid")
    normalized_issues: list[dict[str, str]] = []
    for item in issues:
        issue = _stable_pause_exact(item, _STABLE_PAUSE_ISSUE_KEYS)
        code = issue.get("code")
        dimension = issue.get("dimension")
        if (
            code not in STABLE_PAUSE_ISSUE_CODES
            or dimension not in STABLE_PAUSE_ISSUE_DIMENSIONS
            or _STABLE_PAUSE_ISSUE_DIMENSION_BY_CODE.get(str(code)) != dimension
        ):
            raise ValueError("stable pause issue is invalid")
        normalized_issues.append({"code": str(code), "dimension": str(dimension)})
    if normalized_issues != sorted(
        normalized_issues,
        key=lambda item: (item["code"], item["dimension"]),
    ) or len({(item["code"], item["dimension"]) for item in normalized_issues}) != len(
        normalized_issues
    ):
        raise ValueError("stable pause issues are not exact")

    status = observation["status"]
    if status.startswith("SAFE_PAUSE_") and (
        any(value != "PASS" for value in checks.values()) or normalized_issues
    ):
        raise ValueError("stable pause safe status is inconsistent")
    if status.startswith("SAFE_PAUSE_") and (
        control.get("adoption_mutex") != "free"
        or control.get("verification_mutex") not in {"free", "absent"}
        or control.get("task_mutex") != "free"
        or control.get("lease_mutex") != "free"
        or control.get("residue_count") != 0
    ):
        raise ValueError("stable pause safe control state is inconsistent")
    if status == "SAFE_PAUSE_ACTIVE" and (
        task_state not in CORE_STATES[:-1] or lease_state != "active"
    ):
        raise ValueError("stable pause active lifecycle is inconsistent")
    if status == "SAFE_PAUSE_TERMINAL" and (
        task_state != "closed" or lease_state != "absent"
    ):
        raise ValueError("stable pause terminal lifecycle is inconsistent")
    if status == "UNSAFE_PAUSE" and (
        "FAIL" not in checks.values() or not normalized_issues
    ):
        raise ValueError("stable pause unsafe status is inconsistent")
    if status == "UNKNOWN" and (
        "FAIL" in checks.values()
        or "UNKNOWN" not in checks.values()
        or not normalized_issues
    ):
        raise ValueError("stable pause unknown status is inconsistent")

    supplied = observation.get("checkpoint_digest")
    if not _stable_pause_digest(supplied) or supplied != stable_pause_checkpoint_digest(
        observation
    ):
        raise ValueError("stable pause checkpoint digest is invalid")
    encoded = canonical_json(observation).encode("utf-8")
    if len(encoded) > _STABLE_PAUSE_MAX_BYTES:
        raise ValueError("stable pause observation exceeds output bounds")
    return dict(observation)


def load_stable_pause_observation(payload: bytes) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not 0 <= len(payload) <= _STABLE_PAUSE_MAX_BYTES:
        raise ValueError("stable pause observation exceeds file bounds")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_stable_pause_closed_pairs,
            parse_constant=_stable_pause_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("stable pause observation JSON is invalid") from error
    _stable_pause_bounded(value)
    return validate_stable_pause_observation(value)


@dataclass(frozen=True)
class ContractIssue:
    code: str
    path: str
    message: str


def validate_task_id(task_id: Any) -> bool:
    """Return whether a task ID is safe for contracts and local state paths."""

    return (
        isinstance(task_id, str)
        and TASK_ID.fullmatch(task_id) is not None
        and ".." not in task_id
    )


def safe_scope_path(value: Any) -> bool:
    """Return whether a repository-relative scope is traversal-free."""

    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


_safe_scope_path = safe_scope_path


def validate_authorization_request(
    request: Mapping[str, Any],
    *,
    task_digest: str,
    scope_paths: list[str],
) -> list[ContractIssue]:
    """Validate an inert request that cannot grant authority by serialization."""

    issues: list[ContractIssue] = []
    if set(request) != AUTHORIZATION_REQUEST_KEYS:
        issues.append(
            ContractIssue(
                "Z_SCHEMA",
                "",
                "AuthorizationRequest must use the closed schema-1 fields.",
            )
        )
    if request.get("schema_version") != 1:
        issues.append(
            ContractIssue(
                "Z_SCHEMA",
                "schema_version",
                "Only AuthorizationRequest schema 1 is supported.",
            )
        )
    if not validate_task_id(request.get("grant_id")):
        issues.append(
            ContractIssue(
                "Z_SCHEMA",
                "grant_id",
                "Request ID must be bounded path-safe ASCII.",
            )
        )
    if not validate_task_id(request.get("session_id")):
        issues.append(
            ContractIssue(
                "Z_SESSION",
                "session_id",
                "Session ID must be bounded path-safe ASCII.",
            )
        )
    supplied_digest = request.get("task_digest")
    if (
        not isinstance(supplied_digest, str)
        or SHA256_DIGEST.fullmatch(supplied_digest) is None
        or supplied_digest != task_digest
    ):
        issues.append(
            ContractIssue(
                "Z_TASK_DIGEST",
                "task_digest",
                "Request must bind to the exact TaskEnvelope digest.",
            )
        )
    allowed_effects = request.get("allowed_effects")
    if (
        not isinstance(allowed_effects, list)
        or not allowed_effects
        or len(allowed_effects) != len(set(allowed_effects))
        or not all(effect in TASK_EFFECTS for effect in allowed_effects)
    ):
        issues.append(
            ContractIssue(
                "Z_EFFECT",
                "allowed_effects",
                "Request effects must be unique closed-vocabulary values.",
            )
        )
    supplied_scope = request.get("scope_paths")
    if (
        not isinstance(supplied_scope, list)
        or not supplied_scope
        or not all(_safe_scope_path(item) for item in supplied_scope)
        or sorted(set(supplied_scope)) != sorted(set(scope_paths))
    ):
        issues.append(
            ContractIssue(
                "Z_SCOPE",
                "scope_paths",
                "Request scope must exactly match the framed task scope.",
            )
        )
    return sorted(issues, key=lambda item: (item.code, item.path))


def validate_authorization_grant(
    grant: Mapping[str, Any],
    *,
    task_digest: str,
    scope_paths: list[str],
) -> list[ContractIssue]:
    """Compatibility name for inert AuthorizationRequest validation."""

    return validate_authorization_request(
        grant,
        task_digest=task_digest,
        scope_paths=scope_paths,
    )


def validate_task_envelope(task: Mapping[str, Any]) -> list[ContractIssue]:
    """Validate the closed, versioned TaskEnvelope supplied to the pure router."""

    issues: list[ContractIssue] = []
    for key in sorted(task):
        if key not in TASK_KEYS:
            issues.append(
                ContractIssue(
                    "T_UNKNOWN", str(key), "Unknown TaskEnvelope schema key."
                )
            )
    if task.get("schema_version") != 1:
        issues.append(
            ContractIssue("T_SCHEMA", "schema_version", "Only schema 1 is supported.")
        )
    if not validate_task_id(task.get("task_id")):
        issues.append(
            ContractIssue(
                "T_TASK_ID",
                "task_id",
                "Task ID must be bounded path-safe ASCII.",
            )
        )
    objective = task.get("objective")
    objective_size: int | None = None
    if isinstance(objective, str):
        try:
            objective_size = len(objective.encode("utf-8"))
        except UnicodeEncodeError:
            objective_size = None
    if (
        not isinstance(objective, str)
        or not objective.strip()
        or objective_size is None
        or objective_size > 8192
    ):
        issues.append(
            ContractIssue(
                "T_OBJECTIVE",
                "objective",
                "Objective must be non-empty and at most 8 KiB.",
            )
        )
    if task.get("intent") not in TASK_INTENTS:
        issues.append(
            ContractIssue("T_INTENT", "intent", "Unsupported task intent.")
        )
    if task.get("phase") not in TASK_PHASES:
        issues.append(
            ContractIssue("T_PHASE", "phase", "Unsupported task phase.")
        )
    if task.get("requested_outcome") not in TASK_OUTCOMES:
        issues.append(
            ContractIssue(
                "T_OUTCOME", "requested_outcome", "Unsupported requested outcome."
            )
        )
    goals = task.get("goals")
    if not isinstance(goals, list) or not goals:
        issues.append(
            ContractIssue("T_GOAL", "goals", "At least one goal is required.")
        )
    else:
        dependency_graph: dict[str, tuple[str, ...]] = {}
        dependency_paths: dict[tuple[str, str], str] = {}
        for index, goal in enumerate(goals):
            if not isinstance(goal, Mapping) or set(goal) != GOAL_KEYS:
                issues.append(
                    ContractIssue(
                        "T_GOAL",
                        f"goals.{index}",
                        "Goal must use the closed schema.",
                    )
                )
                continue
            domains = goal.get("domains")
            dependencies = goal.get("depends_on")
            if (
                not validate_task_id(goal.get("id"))
                or not isinstance(goal.get("summary"), str)
                or not str(goal.get("summary")).strip()
                or not isinstance(domains, list)
                or not domains
                or not all(
                    isinstance(item, str) and DOMAIN_ID.fullmatch(item)
                    for item in domains
                )
                or not isinstance(dependencies, list)
                or not all(validate_task_id(item) for item in dependencies)
            ):
                issues.append(
                    ContractIssue(
                        "T_GOAL",
                        f"goals.{index}",
                        "Goal identifiers, summary, domains, or dependencies are invalid.",
                    )
                )
                continue
            goal_id = str(goal["id"])
            if goal_id in dependency_graph:
                issues.append(
                    ContractIssue(
                        "T_GOAL",
                        f"goals.{index}.id",
                        "Goal identifiers must be unique.",
                    )
                )
                continue
            dependency_graph[goal_id] = tuple(str(item) for item in dependencies)
            for dependency_index, dependency in enumerate(dependencies):
                dependency_paths[(goal_id, str(dependency))] = (
                    f"goals.{index}.depends_on.{dependency_index}"
                )

        goal_ids = set(dependency_graph)
        graph: dict[str, set[str]] = {
            goal_id: set() for goal_id in dependency_graph
        }
        for goal_id, dependencies in dependency_graph.items():
            for dependency in dependencies:
                path = dependency_paths[(goal_id, dependency)]
                if dependency == goal_id:
                    issues.append(
                        ContractIssue(
                            "T_GOAL_SELF_DEPENDENCY",
                            path,
                            "A goal cannot depend on itself.",
                        )
                    )
                elif dependency not in goal_ids:
                    issues.append(
                        ContractIssue(
                            "T_GOAL_REFERENCE",
                            path,
                            "Goal dependency must reference an existing goal.",
                        )
                    )
                else:
                    graph[goal_id].add(dependency)

        dependents: dict[str, set[str]] = {
            goal_id: set() for goal_id in graph
        }
        remaining_dependencies = {
            goal_id: len(dependencies)
            for goal_id, dependencies in graph.items()
        }
        for goal_id, dependencies in graph.items():
            for dependency in dependencies:
                dependents[dependency].add(goal_id)
        ready = [
            goal_id
            for goal_id, count in remaining_dependencies.items()
            if count == 0
        ]
        heapq.heapify(ready)
        visited = 0
        while ready:
            goal_id = heapq.heappop(ready)
            visited += 1
            for dependent in sorted(dependents[goal_id]):
                remaining_dependencies[dependent] -= 1
                if remaining_dependencies[dependent] == 0:
                    heapq.heappush(ready, dependent)
        if visited != len(graph):
            issues.append(
                ContractIssue(
                    "T_GOAL_CYCLE",
                    "goals",
                    "Goal dependency graph must be acyclic.",
                )
            )

    domains = task.get("domains")
    if not isinstance(domains, list) or not domains or not all(
        isinstance(item, str) and DOMAIN_ID.fullmatch(item)
        for item in domains
    ):
        issues.append(
            ContractIssue(
                "T_DOMAIN", "domains", "Domains must be stable lower-case IDs."
            )
        )

    scope_paths = task.get("scope_paths")
    if not isinstance(scope_paths, list) or not scope_paths or not all(
        _safe_scope_path(item) for item in scope_paths
    ):
        issues.append(
            ContractIssue(
                "T_SCOPE",
                "scope_paths",
                "Scope paths must be repository-relative and traversal-free.",
            )
        )

    for field in ("explicit_resources", "excluded_resources"):
        values = task.get(field)
        if not isinstance(values, list) or not all(
            isinstance(item, str) and RESOURCE_ID.fullmatch(item)
            for item in values
        ):
            issues.append(
                ContractIssue(
                    "T_RESOURCE",
                    field,
                    "Resource references must be stable lower-case IDs.",
                )
            )

    risk = task.get("risk")
    if not isinstance(risk, Mapping):
        issues.append(ContractIssue("T_RISK", "risk", "Risk axes are required."))
    else:
        if set(risk) != RISK_AXES:
            issues.append(
                ContractIssue(
                    "T_RISK", "risk", "Risk must use exactly the schema-1 axes."
                )
            )
        for axis in sorted(RISK_AXES):
            value = risk.get(axis)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 3:
                issues.append(
                    ContractIssue(
                        "T_RISK", f"risk.{axis}", "Risk must be an integer from 0 to 3."
                    )
                )
    if (
        "risk_provenance" in task
        and task.get("risk_provenance") not in PROVENANCE
    ):
        issues.append(
            ContractIssue(
                "T_PROVENANCE",
                "risk_provenance",
                "Unknown risk provenance.",
            )
        )
    effects = task.get("effects", [])
    if not isinstance(effects, list):
        issues.append(ContractIssue("T_EFFECTS", "effects", "Effects must be a list."))
    else:
        for index, effect in enumerate(effects):
            if (
                not isinstance(effect, Mapping)
                or set(effect) != {"name", "source"}
                or effect.get("name") not in TASK_EFFECTS
            ):
                issues.append(
                    ContractIssue(
                        "T_EFFECT",
                        f"effects.{index}",
                        "Effect must use the closed schema-1 vocabulary.",
                    )
                )
            if not isinstance(effect, Mapping) or effect.get("source") not in PROVENANCE:
                issues.append(
                    ContractIssue(
                        "T_PROVENANCE",
                        f"effects.{index}.source",
                        "Unknown effect provenance.",
                    )
                )
    signals = task.get("signals")
    if not isinstance(signals, list) or not all(
        isinstance(signal, str) and signal in TASK_SIGNALS
        for signal in signals
    ):
        issues.append(
            ContractIssue(
                "T_SIGNAL",
                "signals",
                "Signals must use the closed schema-1 vocabulary.",
            )
        )
    return sorted(issues, key=lambda item: (item.code, item.path))
