"""Closed, bounded contracts for local Core adoption enablement."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping


REQUIREMENT_IDS = tuple(f"AE-{index:02d}" for index in range(1, 10))
ADOPTION_LIFECYCLE = "journal-bound-v1"
MANAGED_PARENT_PATHS = (
    ".codex",
    "control_plane",
    "scripts",
    ".codex/git-hooks",
    ".codex/hooks",
)
MANAGED_REPOSITORY_SCAN = {
    "contract": "managed-repositories-v1",
    "nested_repositories_absent": True,
    "gitlinks_absent": True,
}
_CORE_TASK_KEYS = frozenset(
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
        "lease_generation_floor",
        "revision",
        "created_at",
        "updated_at",
        "authorizes",
        "state_digest",
    }
)
_CORE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$", re.ASCII)
_CORE_REVISION_ID = re.compile(r"^rev-[0-9a-f]{16}$", re.ASCII)
PLAN_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "source",
        "target",
        "managed_records",
        "before_snapshot_digest",
        "requirement_ids",
        "result",
        "applicable",
        "mutation",
        "error_codes",
        "plan_digest",
        "authorizes",
    }
)
JOURNAL_KEYS = frozenset(
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
RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "operation",
        "plan_digest",
        "install_digest",
        "before_snapshot_digest",
        "after_snapshot_digest",
        "result",
        "error_codes",
        "lifecycle_lock",
        "receipt_digest",
        "authorizes",
    }
)

_SOURCE_KEYS = frozenset(
    {
        "head",
        "tree",
        "product_version",
        "runtime_digest",
        "lock_digest",
        "manifest_digest",
    }
)
_TARGET_KEYS = frozenset(
    {
        "repository_id",
        "common_dir_id",
        "worktree_id",
        "branch",
        "head",
        "policy_digest",
        "registry_digest",
        "before_snapshot_digest",
        "core_hooks_path_before",
        "adoption_lifecycle",
        "managed_parent_directories",
        "managed_repository_scan",
    }
)
_TARGET_BINDING_KEYS = frozenset(
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
_RECORD_KEYS = frozenset(
    {"path", "role", "sha256", "git_mode", "size_bytes"}
)
_DIRECTORY_RECORD_KEYS = frozenset({"path", "mode", "identity"})
_PARENT_ABSENT_KEYS = frozenset({"path", "state"})
_PARENT_PRESENT_KEYS = frozenset({"path", "state", "identity", "mode"})
_LIFECYCLE_LOCK_KEYS = frozenset(
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
_VERIFICATION_LOCK_KEYS = frozenset({"directory", "file"})
_VERIFICATION_DIRECTORY_KEYS = frozenset(
    {"path", "device", "inode", "mode", "uid", "gid", "flags"}
)
_ROLLBACK_RECORD_KEYS = frozenset({*_RECORD_KEYS, "before"})
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_COMMIT = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_ERROR_CODE = re.compile(r"^E_[A-Z0-9_]{1,62}$", re.ASCII)
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)
_ROLE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$", re.ASCII)
_MAX_DEPTH = 32
_MAX_ITEMS = 4096
_MAX_STRING = 8192
_MAX_RECORDS = 256
_MAX_FILE_BYTES = 1024 * 1024
_JOURNAL_STATES = frozenset(
    {
        "prepared",
        "staged",
        "published_inactive",
        "active",
        "rolling_back",
        "rolled_back",
    }
)


@dataclass(frozen=True)
class ContractIssue:
    code: str
    path: str
    message: str


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("E_ADOPTION_JSON: value is not canonical JSON") from error


def contract_digest(value: Any) -> str:
    payload = canonical_json(value).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def _core_scope(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return None
    normalized = value.rstrip("/")
    if normalized.endswith("/**"):
        normalized = normalized[:-3].rstrip("/")
    if normalized in {"", "."}:
        return "."
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in {"", "."}:
        return "."
    if any(character in normalized for character in "*?[]"):
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    canonical = path.as_posix()
    return "." if canonical == "." else canonical


def validate_closed_core_task(
    value: Any,
    *,
    expected_task_id: str,
    expected_repository: str,
) -> dict[str, object]:
    """Validate the exact CoreTaskStateV1 subset that proves quiescence."""

    if not isinstance(value, dict) or set(value) != _CORE_TASK_KEYS:
        raise ValueError("E_ADOPTION_TASK_ACTIVE: task binding is not exact")
    unsigned = {key: item for key, item in value.items() if key != "state_digest"}
    task_id = value.get("task_id")
    revision_id = value.get("revision_id")
    head = value.get("head")
    task_digest = value.get("task_digest")
    scopes = value.get("scope_paths")
    normalized_scopes = (
        sorted({_core_scope(item) for item in scopes})
        if isinstance(scopes, list)
        and scopes
        and all(_core_scope(item) is not None for item in scopes)
        else None
    )
    branch = value.get("branch")
    protected_base = value.get("protected_base")
    valid = (
        type(value.get("schema_version")) is int
        and value.get("schema_version") == 1
        and value.get("kind") == "CoreTaskStateV1"
        and isinstance(task_id, str)
        and _CORE_TASK_ID.fullmatch(task_id) is not None
        and task_id == expected_task_id
        and value.get("requested_outcome") == "local_change"
        and value.get("state") == "closed"
        and value.get("resume_state") is None
        and value.get("block_reason") is None
        and value.get("repository") == expected_repository
        and value.get("worktree") == expected_repository
        and isinstance(branch, str)
        and bool(branch)
        and isinstance(protected_base, str)
        and bool(protected_base)
        and branch != protected_base
        and isinstance(head, str)
        and re.fullmatch(r"[0-9a-f]{40}", head) is not None
        and isinstance(revision_id, str)
        and _CORE_REVISION_ID.fullmatch(revision_id) is not None
        and isinstance(task_digest, str)
        and _SHA256.fullmatch(task_digest) is not None
        and revision_id
        == "rev-" + sha256(f"{task_digest}\0{head}".encode()).hexdigest()[:16]
        and normalized_scopes is not None
        and scopes == normalized_scopes
        and all(
            isinstance(value.get(name), str)
            and _SHA256.fullmatch(str(value.get(name))) is not None
            for name in ("decision_digest", "owner_runtime_digest")
        )
        and type(value.get("lease_generation")) is int
        and int(value.get("lease_generation", -1)) >= 0
        and type(value.get("lease_generation_floor")) is int
        and int(value.get("lease_generation_floor", -1))
        >= int(value.get("lease_generation", -1))
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
        and _SHA256.fullmatch(str(value.get("state_digest"))) is not None
        and value.get("state_digest") == contract_digest(unsigned)
    )
    if not valid:
        raise ValueError("E_ADOPTION_TASK_ACTIVE: task binding is invalid")
    return dict(value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("E_ADOPTION_JSON: duplicate object key")
        value[key] = item
    return value


def _constant(_: str) -> None:
    raise ValueError("E_ADOPTION_JSON: non-finite number")


def _check_shape(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    observed = 0
    while stack:
        item, depth = stack.pop()
        observed += 1
        if observed > _MAX_ITEMS or depth > _MAX_DEPTH:
            raise ValueError("E_ADOPTION_JSON: JSON shape exceeds its bound")
        if isinstance(item, str):
            if len(item.encode("utf-8")) > _MAX_STRING:
                raise ValueError("E_ADOPTION_JSON: JSON string exceeds its bound")
        elif isinstance(item, dict):
            stack.extend((entry, depth + 1) for entry in item.values())
        elif isinstance(item, list):
            stack.extend((entry, depth + 1) for entry in item)
        elif item is None or isinstance(item, (bool, int, float)):
            continue
        else:
            raise ValueError("E_ADOPTION_JSON: unsupported JSON value")


def load_closed_json(payload: bytes, *, limit: int) -> dict[str, object]:
    if (
        not isinstance(payload, bytes)
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or limit <= 0
    ):
        raise ValueError("E_ADOPTION_JSON_SIZE: invalid JSON byte limit")
    if not payload or len(payload) > limit:
        raise ValueError("E_ADOPTION_JSON_SIZE: JSON exceeds its byte limit")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=_constant,
        )
        _check_shape(value)
    except ValueError as error:
        if str(error).startswith("E_ADOPTION_JSON"):
            raise
        raise ValueError("E_ADOPTION_JSON: JSON is malformed") from error
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("E_ADOPTION_JSON: JSON is malformed") from error
    if not isinstance(value, dict):
        raise ValueError("E_ADOPTION_JSON: JSON root must be an object")
    return value


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code=code, path=path, message=message)


def _exact_mapping(
    value: Any,
    keys: frozenset[str],
    *,
    path: str,
    issues: list[ContractIssue],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != keys:
        issues.append(
            _issue("E_ADOPTION_SCHEMA", path, "Object fields are not exact.")
        )
        return None
    return value


def _digest(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _commit(value: Any) -> bool:
    return isinstance(value, str) and _COMMIT.fullmatch(value) is not None


def _identity(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in value
        )
    )


def _safe_path(value: Any) -> bool:
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


def _error_codes(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= 64
        and len(value) == len(set(value))
        and all(
            isinstance(item, str) and _ERROR_CODE.fullmatch(item) is not None
            for item in value
        )
    )


def _validate_managed_parents(
    value: Any,
    *,
    path: str,
    code: str,
    issues: list[ContractIssue],
) -> None:
    if not isinstance(value, list) or len(value) != len(MANAGED_PARENT_PATHS):
        issues.append(_issue(code, path, "Managed parent bindings are not exact."))
        return
    observed_paths: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, Mapping):
            issues.append(_issue(code, item_path, "Managed parent binding is invalid."))
            continue
        state = item.get("state")
        keys = _PARENT_ABSENT_KEYS if state == "absent" else _PARENT_PRESENT_KEYS
        record = _exact_mapping(item, keys, path=item_path, issues=issues)
        if record is None:
            continue
        relative = record.get("path")
        if not isinstance(relative, str):
            issues.append(_issue(code, f"{item_path}.path", "Managed parent path is invalid."))
        else:
            observed_paths.append(relative)
        if state == "present":
            mode = record.get("mode")
            if (
                not _identity(record.get("identity"))
                or type(mode) is not int
                or not 0 <= mode <= 0o7777
                or mode & 0o022
            ):
                issues.append(_issue(code, item_path, "Managed parent identity or mode is unsafe."))
        elif state != "absent":
            issues.append(_issue(code, f"{item_path}.state", "Managed parent state is invalid."))
    if observed_paths != list(MANAGED_PARENT_PATHS):
        issues.append(_issue(code, path, "Managed parent paths are not canonical."))


def _validate_managed_repository_scan(
    value: Any,
    *,
    path: str,
    code: str,
    issues: list[ContractIssue],
) -> None:
    if value != MANAGED_REPOSITORY_SCAN:
        issues.append(_issue(code, path, "Managed repository scan binding is invalid."))


def _validate_lifecycle_lock(
    value: Any,
    *,
    path: str,
    code: str,
    issues: list[ContractIssue],
) -> None:
    record = _exact_mapping(value, _LIFECYCLE_LOCK_KEYS, path=path, issues=issues)
    if record is None:
        return
    integer_fields = (
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
    )
    if (
        record.get("path") != "codex-control-plane-core/adoption.lock"
        or any(
            type(record.get(key)) is not int or int(record[key]) < 0
            for key in integer_fields
        )
        or record.get("mode") != 0o600
        or record.get("links") != 1
        or record.get("size") != 0
        or int(record.get("flags", 0)) & 0x40000000
    ):
        issues.append(_issue(code, path, "Lifecycle lock identity is invalid."))


def _validate_verification_lock(
    value: Any,
    *,
    path: str,
    code: str,
    issues: list[ContractIssue],
) -> None:
    binding = _exact_mapping(
        value,
        _VERIFICATION_LOCK_KEYS,
        path=path,
        issues=issues,
    )
    if binding is None:
        return
    directory = _exact_mapping(
        binding.get("directory"),
        _VERIFICATION_DIRECTORY_KEYS,
        path=f"{path}.directory",
        issues=issues,
    )
    if directory is not None:
        integer_fields = ("device", "inode", "mode", "uid", "gid", "flags")
        if (
            directory.get("path") != "codex-control-plane-core/locks"
            or any(
                type(directory.get(key)) is not int or int(directory[key]) < 0
                for key in integer_fields
            )
            or directory.get("mode") != 0o700
            or int(directory.get("flags", 0)) & 0x40000000
        ):
            issues.append(
                _issue(code, f"{path}.directory", "Verification directory identity is invalid.")
            )
    lock = _exact_mapping(
        binding.get("file"),
        _LIFECYCLE_LOCK_KEYS,
        path=f"{path}.file",
        issues=issues,
    )
    if lock is not None:
        integer_fields = (
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
        )
        if (
            lock.get("path")
            != "codex-control-plane-core/locks/verification.lock"
            or any(
                type(lock.get(key)) is not int or int(lock[key]) < 0
                for key in integer_fields
            )
            or lock.get("mode") != 0o600
            or lock.get("links") != 1
            or lock.get("size") != 0
            or int(lock.get("flags", 0)) & 0x40000000
        ):
            issues.append(
                _issue(code, f"{path}.file", "Verification lock identity is invalid.")
            )


def _authority_issues(value: Any) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    stack: list[tuple[Any, str]] = [(value, "")]
    while stack:
        item, path = stack.pop()
        if isinstance(item, Mapping):
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                if key == "authorizes" and child is not False:
                    issues.append(
                        _issue(
                            "E_ADOPTION_AUTHORITY",
                            child_path,
                            "Serialized adoption artifacts never authorize.",
                        )
                    )
                stack.append((child, child_path))
        elif isinstance(item, list):
            stack.extend(
                (child, f"{path}[{index}]")
                for index, child in enumerate(item)
            )
    return issues


def _validate_source(value: Any, issues: list[ContractIssue]) -> None:
    source = _exact_mapping(value, _SOURCE_KEYS, path="source", issues=issues)
    if source is None:
        return
    if not _commit(source.get("head")) or not _commit(source.get("tree")):
        issues.append(_issue("E_ADOPTION_SOURCE", "source", "Source Git binding is invalid."))
    if source.get("product_version") != "3.1.0-core.2":
        issues.append(
            _issue("E_ADOPTION_SOURCE", "source.product_version", "Core version is unsupported.")
        )
    for key in ("runtime_digest", "lock_digest", "manifest_digest"):
        if not _digest(source.get(key)):
            issues.append(_issue("E_ADOPTION_SOURCE", f"source.{key}", "Source digest is invalid."))


def _validate_target(value: Any, issues: list[ContractIssue]) -> None:
    target = _exact_mapping(value, _TARGET_KEYS, path="target", issues=issues)
    if target is None:
        return
    for key in ("repository_id", "common_dir_id", "worktree_id"):
        if not _identity(target.get(key)):
            issues.append(_issue("E_ADOPTION_TARGET", f"target.{key}", "Target identity is invalid."))
    branch = target.get("branch")
    if (
        not isinstance(branch, str)
        or _BRANCH.fullmatch(branch) is None
        or ".." in branch
        or branch.endswith("/")
    ):
        issues.append(_issue("E_ADOPTION_TARGET", "target.branch", "Target branch is invalid."))
    if not _commit(target.get("head")):
        issues.append(_issue("E_ADOPTION_TARGET", "target.head", "Target HEAD is invalid."))
    for key in ("policy_digest", "registry_digest", "before_snapshot_digest"):
        if not _digest(target.get(key)):
            issues.append(_issue("E_ADOPTION_TARGET", f"target.{key}", "Target digest is invalid."))
    if target.get("core_hooks_path_before") is not None:
        issues.append(
            _issue("E_ADOPTION_NOT_FRESH", "target.core_hooks_path_before", "Target hooks path must be absent.")
        )
    if target.get("adoption_lifecycle") != ADOPTION_LIFECYCLE:
        issues.append(
            _issue(
                "E_ADOPTION_TARGET",
                "target.adoption_lifecycle",
                "Target lifecycle policy is invalid.",
            )
        )
    _validate_managed_parents(
        target.get("managed_parent_directories"),
        path="target.managed_parent_directories",
        code="E_ADOPTION_TARGET",
        issues=issues,
    )
    _validate_managed_repository_scan(
        target.get("managed_repository_scan"),
        path="target.managed_repository_scan",
        code="E_ADOPTION_TARGET",
        issues=issues,
    )


def _validate_records(value: Any, issues: list[ContractIssue]) -> None:
    if not isinstance(value, list) or not value or len(value) > _MAX_RECORDS:
        issues.append(_issue("E_ADOPTION_MANIFEST", "managed_records", "Managed records are not bounded."))
        return
    paths: list[str] = []
    for index, item in enumerate(value):
        path = f"managed_records[{index}]"
        record = _exact_mapping(item, _RECORD_KEYS, path=path, issues=issues)
        if record is None:
            continue
        record_path = record.get("path")
        if not _safe_path(record_path):
            issues.append(_issue("E_ADOPTION_MANIFEST", f"{path}.path", "Managed path is unsafe."))
        else:
            paths.append(record_path)
        if not isinstance(record.get("role"), str) or _ROLE.fullmatch(record["role"]) is None:
            issues.append(_issue("E_ADOPTION_MANIFEST", f"{path}.role", "Managed role is invalid."))
        if not _digest(record.get("sha256")):
            issues.append(_issue("E_ADOPTION_MANIFEST", f"{path}.sha256", "Managed digest is invalid."))
        if record.get("git_mode") not in {"100644", "100755"}:
            issues.append(_issue("E_ADOPTION_MANIFEST", f"{path}.git_mode", "Managed mode is invalid."))
        size = record.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= _MAX_FILE_BYTES:
            issues.append(_issue("E_ADOPTION_MANIFEST", f"{path}.size_bytes", "Managed size is invalid."))
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        issues.append(_issue("E_ADOPTION_MANIFEST", "managed_records", "Managed paths must be sorted and unique."))


def _validate_journal_record(
    value: Any,
    *,
    path: str,
    issues: list[ContractIssue],
    rollback: bool = False,
) -> str | None:
    keys = _ROLLBACK_RECORD_KEYS if rollback else _RECORD_KEYS
    record = _exact_mapping(value, keys, path=path, issues=issues)
    if record is None:
        return None
    relative = record.get("path")
    if not _safe_path(relative):
        issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.path", "Journal path is unsafe."))
        return None
    if not isinstance(record.get("role"), str) or _ROLE.fullmatch(record["role"]) is None:
        issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.role", "Journal role is invalid."))
    if not _digest(record.get("sha256")):
        issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.sha256", "Journal digest is invalid."))
    if record.get("git_mode") not in {"100644", "100755"}:
        issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.git_mode", "Journal mode is invalid."))
    size = record.get("size_bytes")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= _MAX_FILE_BYTES:
        issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.size_bytes", "Journal size is invalid."))
    if rollback and record.get("before") != "absent":
        issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.before", "Rollback prior state is invalid."))
    return str(relative)


def _validate_seal(
    value: Mapping[str, Any],
    *,
    seal_key: str,
    error_code: str,
    issues: list[ContractIssue],
) -> None:
    supplied = value.get(seal_key)
    unsigned = {key: item for key, item in value.items() if key != seal_key}
    if not _digest(supplied) or supplied != contract_digest(unsigned):
        issues.append(_issue(error_code, seal_key, "Contract digest does not match canonical bytes."))


def validate_plan(
    value: Any,
    *,
    expected_digest: Any | None = None,
) -> tuple[ContractIssue, ...]:
    issues = _authority_issues(value)
    plan = _exact_mapping(value, PLAN_KEYS, path="", issues=issues)
    if plan is None:
        return tuple(sorted(issues, key=lambda item: (item.code, item.path)))
    if plan.get("schema_version") != 1 or plan.get("kind") != "CoreAdoptionPlanV1":
        issues.append(_issue("E_ADOPTION_SCHEMA", "", "Plan schema or kind is unsupported."))
    if plan.get("authorizes") is not False:
        issues.append(_issue("E_ADOPTION_AUTHORITY", "authorizes", "Plan cannot authorize."))
    _validate_source(plan.get("source"), issues)
    _validate_target(plan.get("target"), issues)
    _validate_records(plan.get("managed_records"), issues)
    if not _digest(plan.get("before_snapshot_digest")):
        issues.append(_issue("E_ADOPTION_TARGET", "before_snapshot_digest", "Before digest is invalid."))
    target = plan.get("target")
    if isinstance(target, Mapping) and plan.get("before_snapshot_digest") != target.get("before_snapshot_digest"):
        issues.append(_issue("E_ADOPTION_TARGET", "before_snapshot_digest", "Before digest binding drifted."))
    if plan.get("requirement_ids") != list(REQUIREMENT_IDS):
        issues.append(_issue("E_ADOPTION_SCHEMA", "requirement_ids", "Requirement IDs are not exact."))
    if plan.get("result") not in {"PASS", "FAIL", "UNKNOWN"}:
        issues.append(_issue("E_ADOPTION_RESULT", "result", "Plan result is invalid."))
    if not isinstance(plan.get("applicable"), bool):
        issues.append(_issue("E_ADOPTION_RESULT", "applicable", "Applicability must be boolean."))
    if plan.get("mutation") is not False:
        issues.append(_issue("E_ADOPTION_MUTATION", "mutation", "Preview must be non-mutating."))
    if not _error_codes(plan.get("error_codes")):
        issues.append(_issue("E_ADOPTION_RESULT", "error_codes", "Error codes are invalid."))
    if plan.get("result") == "PASS" and (
        plan.get("applicable") is not True or plan.get("error_codes") != []
    ):
        issues.append(_issue("E_ADOPTION_RESULT", "result", "PASS plan is internally inconsistent."))
    _validate_seal(
        plan,
        seal_key="plan_digest",
        error_code="E_ADOPTION_PLAN_DIGEST",
        issues=issues,
    )
    if expected_digest is not None and plan.get("plan_digest") != expected_digest:
        issues.append(
            _issue(
                "E_ADOPTION_PLAN_DIGEST",
                "plan_digest",
                "Plan does not match the separately expected digest.",
            )
        )
    return tuple(sorted(set(issues), key=lambda item: (item.code, item.path)))


def validate_journal(value: Any) -> tuple[ContractIssue, ...]:
    issues = _authority_issues(value)
    journal = _exact_mapping(value, JOURNAL_KEYS, path="", issues=issues)
    if journal is None:
        return tuple(sorted(issues, key=lambda item: (item.code, item.path)))
    if journal.get("schema_version") != 1 or journal.get("kind") != "CoreAdoptionJournalV1":
        issues.append(_issue("E_ADOPTION_SCHEMA", "", "Journal schema or kind is unsupported."))
    if journal.get("authorizes") is not False:
        issues.append(_issue("E_ADOPTION_AUTHORITY", "authorizes", "Journal cannot authorize."))
    for key in ("plan_digest", "install_digest", "source_manifest_digest", "before_snapshot_digest"):
        if not _digest(journal.get(key)):
            issues.append(_issue("E_ADOPTION_JOURNAL", key, "Journal digest is invalid."))
    if journal.get("state") not in _JOURNAL_STATES:
        issues.append(_issue("E_ADOPTION_JOURNAL", "state", "Journal state is invalid."))
    target = _exact_mapping(
        journal.get("target_binding"),
        _TARGET_BINDING_KEYS,
        path="target_binding",
        issues=issues,
    )
    if target is not None:
        for key in ("repository_id", "common_dir_id", "worktree_id"):
            if not _identity(target.get(key)):
                issues.append(_issue("E_ADOPTION_JOURNAL", f"target_binding.{key}", "Target identity is invalid."))
        branch = target.get("branch")
        if (
            not isinstance(branch, str)
            or _BRANCH.fullmatch(branch) is None
            or ".." in branch
            or branch.endswith("/")
        ):
            issues.append(_issue("E_ADOPTION_JOURNAL", "target_binding.branch", "Target branch is invalid."))
        if not _commit(target.get("head")):
            issues.append(_issue("E_ADOPTION_JOURNAL", "target_binding.head", "Target HEAD is invalid."))
        for key in ("policy_digest", "registry_digest"):
            if not _digest(target.get(key)):
                issues.append(_issue("E_ADOPTION_JOURNAL", f"target_binding.{key}", "Target authority digest is invalid."))
        if target.get("adoption_lifecycle") != ADOPTION_LIFECYCLE:
            issues.append(
                _issue(
                    "E_ADOPTION_JOURNAL",
                    "target_binding.adoption_lifecycle",
                    "Lifecycle policy binding is invalid.",
                )
            )

    _validate_managed_parents(
        journal.get("managed_parent_directories"),
        path="managed_parent_directories",
        code="E_ADOPTION_JOURNAL",
        issues=issues,
    )
    _validate_managed_repository_scan(
        journal.get("managed_repository_scan"),
        path="managed_repository_scan",
        code="E_ADOPTION_JOURNAL",
        issues=issues,
    )
    _validate_lifecycle_lock(
        journal.get("lifecycle_lock"),
        path="lifecycle_lock",
        code="E_ADOPTION_JOURNAL",
        issues=issues,
    )
    _validate_verification_lock(
        journal.get("verification_lock"),
        path="verification_lock",
        code="E_ADOPTION_JOURNAL",
        issues=issues,
    )

    created = journal.get("created_directories")
    created_paths: list[str] = []
    if not isinstance(created, list) or len(created) > _MAX_RECORDS:
        issues.append(_issue("E_ADOPTION_JOURNAL", "created_directories", "Created directories are not bounded."))
    else:
        for index, item in enumerate(created):
            path = f"created_directories[{index}]"
            record = _exact_mapping(item, _DIRECTORY_RECORD_KEYS, path=path, issues=issues)
            if record is None:
                continue
            relative = record.get("path")
            if not _safe_path(relative):
                issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.path", "Created directory path is unsafe."))
            else:
                created_paths.append(str(relative))
            if record.get("mode") != 0o755:
                issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.mode", "Created directory mode is invalid."))
            identity = record.get("identity")
            if identity is not None and not _identity(identity):
                issues.append(_issue("E_ADOPTION_JOURNAL", f"{path}.identity", "Created directory identity is invalid."))
        expected_order = sorted(created_paths, key=lambda value: (value.count("/"), value))
        if created_paths != expected_order or len(created_paths) != len(set(created_paths)):
            issues.append(_issue("E_ADOPTION_JOURNAL", "created_directories", "Created directory paths are not exact."))

    published = journal.get("published_records")
    published_paths: list[str] = []
    if not isinstance(published, list) or len(published) > _MAX_RECORDS:
        issues.append(_issue("E_ADOPTION_JOURNAL", "published_records", "Published records are not bounded."))
    else:
        for index, item in enumerate(published):
            relative = _validate_journal_record(
                item,
                path=f"published_records[{index}]",
                issues=issues,
            )
            if relative is not None:
                published_paths.append(relative)
        if published_paths != sorted(published_paths) or len(published_paths) != len(set(published_paths)):
            issues.append(_issue("E_ADOPTION_JOURNAL", "published_records", "Published paths are not exact."))

    lock_path = _validate_journal_record(
        journal.get("target_lock_record"),
        path="target_lock_record",
        issues=issues,
    )
    if lock_path != ".codex/control-plane.lock":
        issues.append(_issue("E_ADOPTION_JOURNAL", "target_lock_record.path", "Activation record is invalid."))

    rollback = journal.get("rollback_records")
    rollback_paths: list[str] = []
    if not isinstance(rollback, list) or not rollback or len(rollback) > _MAX_RECORDS:
        issues.append(_issue("E_ADOPTION_JOURNAL", "rollback_records", "Rollback records are not bounded."))
    else:
        for index, item in enumerate(rollback):
            relative = _validate_journal_record(
                item,
                path=f"rollback_records[{index}]",
                issues=issues,
                rollback=True,
            )
            if relative is not None:
                rollback_paths.append(relative)
        if rollback_paths != sorted(rollback_paths) or len(rollback_paths) != len(set(rollback_paths)):
            issues.append(_issue("E_ADOPTION_JOURNAL", "rollback_records", "Rollback paths are not exact."))
        if lock_path is not None and lock_path not in rollback_paths:
            issues.append(_issue("E_ADOPTION_JOURNAL", "rollback_records", "Activation rollback binding is absent."))
    if journal.get("prior_git_config") != {"core.hooksPath": None}:
        issues.append(_issue("E_ADOPTION_JOURNAL", "prior_git_config", "Prior Git configuration is invalid."))
    _validate_seal(
        journal,
        seal_key="state_digest",
        error_code="E_ADOPTION_JOURNAL",
        issues=issues,
    )
    return tuple(sorted(set(issues), key=lambda item: (item.code, item.path)))


def validate_receipt(value: Any) -> tuple[ContractIssue, ...]:
    issues = _authority_issues(value)
    receipt = _exact_mapping(value, RECEIPT_KEYS, path="", issues=issues)
    if receipt is None:
        return tuple(sorted(issues, key=lambda item: (item.code, item.path)))
    if receipt.get("schema_version") != 1 or receipt.get("kind") != "CoreAdoptionReceiptV1":
        issues.append(_issue("E_ADOPTION_SCHEMA", "", "Receipt schema or kind is unsupported."))
    if receipt.get("authorizes") is not False:
        issues.append(_issue("E_ADOPTION_AUTHORITY", "authorizes", "Receipt cannot authorize."))
    if receipt.get("operation") not in {"apply", "rollback"}:
        issues.append(_issue("E_ADOPTION_RECEIPT", "operation", "Receipt operation is invalid."))
    for key in (
        "plan_digest",
        "install_digest",
        "before_snapshot_digest",
        "after_snapshot_digest",
    ):
        if not _digest(receipt.get(key)):
            issues.append(_issue("E_ADOPTION_RECEIPT", key, "Receipt digest is invalid."))
    if receipt.get("result") not in {"PASS", "FAIL", "UNKNOWN"}:
        issues.append(_issue("E_ADOPTION_RECEIPT", "result", "Receipt result is invalid."))
    if not _error_codes(receipt.get("error_codes")):
        issues.append(_issue("E_ADOPTION_RECEIPT", "error_codes", "Receipt error codes are invalid."))
    _validate_lifecycle_lock(
        receipt.get("lifecycle_lock"),
        path="lifecycle_lock",
        code="E_ADOPTION_RECEIPT",
        issues=issues,
    )
    _validate_seal(
        receipt,
        seal_key="receipt_digest",
        error_code="E_ADOPTION_RECEIPT",
        issues=issues,
    )
    return tuple(sorted(set(issues), key=lambda item: (item.code, item.path)))
