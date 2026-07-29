"""Load and validate the versioned project policy."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping
import re
import subprocess
import tomllib
import tempfile

from control_plane.contracts import contract_digest
from control_plane.scopes import normalize_scope, scope_owns
from control_plane.repository import git_environment, worktree_git_dir
from control_plane.host_bridge import (
    GoverningRuntimeObservation,
    HostAdapterCapability,
    NativeUserInteractionEvent,
    TrustedAuthorization,
    _consume_governing_runtime_observation,
    _consume_runtime_host_object,
    _governing_runtime_observation_is_live,
    _native_host_object_is_valid,
    _register_runtime_host_object,
    _runtime_host_object_is_live,
    consume_authorization,
)

SUPPORTED_SCHEMA_VERSION = 1
ALLOWED_REASONING_LEVELS = frozenset(
    {"low", "medium", "high", "xhigh", "max", "ultra"}
)
ALLOWED_INTEGRATION_STRATEGIES = frozenset(
    {"squash", "merge-commit", "rebase-merge"}
)
ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "": frozenset(
        {
            "schema_version",
            "project_name",
            "project_kind",
            "git",
            "reasoning",
            "documentation",
            "release",
            "gates",
        }
    ),
    "git": frozenset(
        {
            "remote",
            "base_branch",
            "require_pull_request",
            "allow_direct_base_push",
            "integration_strategy",
        }
    ),
    "reasoning": frozenset(
        {
            "model",
            "default",
            "plan",
            "subagent",
            "normal_max_workers",
            "sequential_default",
        }
    ),
    "documentation": frozenset({"require_impact_assessment"}),
    "release": frozenset(
        {
            "official_source",
            "require_manifest",
            "allow_local_official_release",
        }
    ),
    "gates": frozenset({"T0", "T1", "T2", "T3"}),
    "gates.T0": frozenset({"required"}),
    "gates.T1": frozenset({"required"}),
    "gates.T2": frozenset({"required"}),
    "gates.T3": frozenset({"required"}),
}


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _atomic_policy_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_bytes(
        path,
        (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        ),
    )
class GoverningPolicy:
    __slots__ = (
        "_consumed",
        "policy",
        "policy_digest",
        "runtime_digest",
        "lock_digest",
        "governing_base_commit",
        "session_id",
        "invocation_id",
        "freshness_deadline",
        "binding_digest",
    )

    def __new__(cls, *_: object, **__: object) -> "GoverningPolicy":
        raise TypeError("GoverningPolicy is runtime-bound")


_GOVERNING_POLICY_BINDING_FIELDS = (
    "policy_digest",
    "runtime_digest",
    "lock_digest",
    "governing_base_commit",
    "session_id",
    "invocation_id",
)
_ISSUED_GOVERNING_POLICIES: dict[
    int, tuple[GoverningPolicy, tuple[object, ...], str]
] = {}


def _governing_policy_binding(
    policy: GoverningPolicy,
) -> tuple[object, ...]:
    return tuple(
        getattr(policy, name)
        for name in _GOVERNING_POLICY_BINDING_FIELDS
    ) + (policy.freshness_deadline, policy.binding_digest)


def _register_governing_policy(policy: GoverningPolicy) -> None:
    _register_runtime_host_object(policy, "governing_policy")
    _ISSUED_GOVERNING_POLICIES[id(policy)] = (
        policy,
        _governing_policy_binding(policy),
        contract_digest(policy.policy),
    )


def _governing_policy_is_issued_for_runtime(
    policy: object,
    governing_runtime: object,
) -> bool:
    if (
        type(policy) is not GoverningPolicy
        or type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
    ):
        return False
    issued = _ISSUED_GOVERNING_POLICIES.get(id(policy))
    expected_binding_digest = contract_digest(
        {
            name: getattr(policy, name)
            for name in _GOVERNING_POLICY_BINDING_FIELDS
        }
    )
    return (
        issued is not None
        and issued[0] is policy
        and issued[1] == _governing_policy_binding(policy)
        and issued[2] == contract_digest(policy.policy)
        and _runtime_host_object_is_live(policy, "governing_policy")
        and not policy._consumed
        and policy.binding_digest == expected_binding_digest
        and policy.runtime_digest == governing_runtime.runtime_digest
        and policy.lock_digest == governing_runtime.lock_digest
        and policy.policy_digest == governing_runtime.policy_digest
        and policy.governing_base_commit
        == governing_runtime.governing_base_commit
        and policy.session_id == governing_runtime.session_id
        and policy.invocation_id == governing_runtime.invocation_id
    )


def _governing_policy_is_live_for_runtime(
    policy: object,
    governing_runtime: object,
    *,
    clock: object,
) -> bool:
    return (
        _governing_policy_is_issued_for_runtime(
            policy, governing_runtime
        )
        and float(clock()) <= policy.freshness_deadline
    )


def _consume_governing_policy(policy: object) -> bool:
    if type(policy) is not GoverningPolicy:
        return False
    issued = _ISSUED_GOVERNING_POLICIES.get(id(policy))
    if (
        issued is None
        or issued[0] is not policy
        or issued[1] != _governing_policy_binding(policy)
        or issued[2] != contract_digest(policy.policy)
    ):
        return False
    _ISSUED_GOVERNING_POLICIES.pop(id(policy), None)
    return _consume_runtime_host_object(policy, "governing_policy")


@dataclass(frozen=True)
class RequiredCheckCandidate:
    name: str
    app: str
    conclusions: tuple[str, ...]
    selector_digest: str


def parse_required_check_selector(value: str) -> RequiredCheckCandidate:
    """Parse NAME:APP:CONCLUSION[,CONCLUSION] without lenient fallbacks."""

    if (
        not isinstance(value, str)
        or len(value) > 512
        or value.count(":") != 2
    ):
        raise ValueError("E_REQUIRED_CHECK: selector grammar is invalid")
    name, app, raw_conclusions = value.split(":")
    conclusions = tuple(sorted(set(raw_conclusions.split(","))))
    allowed = {"SUCCESS", "NEUTRAL", "SKIPPED"}
    if (
        not name.strip()
        or not app.strip()
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", app) is None
        or not conclusions
        or any(item not in allowed for item in conclusions)
    ):
        raise ValueError("E_REQUIRED_CHECK: selector grammar is invalid")
    core = {
        "name": name.strip(),
        "app": app,
        "conclusions": conclusions,
    }
    return RequiredCheckCandidate(
        **core, selector_digest=contract_digest(core)
    )


def load_governing_policy_from_runtime(
    governing_runtime: object,
    *,
    expected_policy_relative_path: str = ".codex/project-policy.toml",
    expected_schema_version: int = 1,
    session_id: str,
    invocation_id: str,
    clock: object,
    ttl_seconds: float,
) -> GoverningPolicy:
    if (
        type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or expected_policy_relative_path != ".codex/project-policy.toml"
        or expected_schema_version != 1
        or governing_runtime.session_id != session_id
        or governing_runtime.invocation_id != invocation_id
        or float(clock()) > governing_runtime.freshness_deadline
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_GOVERNING_POLICY: governing runtime binding is invalid"
        )
    path = (
        Path(governing_runtime.attestor_worktree)
        / expected_policy_relative_path
    )
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 131_072
    ):
        raise ValueError("E_GOVERNING_POLICY: policy file is unavailable")
    raw = path.read_bytes()
    file_digest = f"sha256:{sha256(raw).hexdigest()}"
    if file_digest != governing_runtime.policy_digest:
        raise ValueError("E_GOVERNING_POLICY: policy bytes drifted")
    try:
        policy = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("E_GOVERNING_POLICY: policy is invalid") from error
    if policy.get("schema_version") != 1 or validate_policy(policy):
        raise ValueError("E_GOVERNING_POLICY: policy contract is invalid")
    result = object.__new__(GoverningPolicy)
    result._consumed = False
    result.policy = dict(policy)
    result.policy_digest = file_digest
    result.runtime_digest = governing_runtime.runtime_digest
    result.lock_digest = governing_runtime.lock_digest
    result.governing_base_commit = governing_runtime.governing_base_commit
    result.session_id = governing_runtime.session_id
    result.invocation_id = governing_runtime.invocation_id
    result.freshness_deadline = float(clock()) + float(ttl_seconds)
    result.binding_digest = contract_digest(
        {
            "policy_digest": result.policy_digest,
            "runtime_digest": result.runtime_digest,
            "lock_digest": result.lock_digest,
            "governing_base_commit": result.governing_base_commit,
            "session_id": result.session_id,
            "invocation_id": result.invocation_id,
        }
    )
    if not _consume_governing_runtime_observation(governing_runtime):
        raise ValueError(
            "E_GOVERNING_POLICY: governing runtime is not host-issued"
        )
    governing_runtime._consumed = True
    _register_governing_policy(result)
    return result


class ProjectRemotePolicyDecision:
    __slots__ = (
        "_consumed",
        "operation_kind",
        "draft_plan_digest",
        "source_repository_identity",
        "target_repository_identity",
        "target_worktree_identity",
        "repository_identity",
        "required_checks",
        "session_id",
        "invocation_id",
        "runtime_digest",
        "decision_digest",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ProjectRemotePolicyDecision":
        raise TypeError("project remote policy decision is host-bound")


def frame_project_remote_policy_decision(
    native_user_event: object,
    *,
    governing_runtime: object,
    host_capability: object,
    operation_kind: str,
    draft_plan_digest: str,
    source_repository_identity: str,
    target_repository_identity: str,
    target_worktree_identity: str,
    repository_identity: str,
    required_checks: tuple[RequiredCheckCandidate, ...],
    session_id: str,
    invocation_id: str,
    clock: object,
    ttl_seconds: float,
) -> ProjectRemotePolicyDecision:
    if (
        type(native_user_event) is not NativeUserInteractionEvent
        or not _native_host_object_is_valid(
            native_user_event, "user_interaction"
        )
        or native_user_event._consumed
        or type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or float(clock()) > governing_runtime.freshness_deadline
        or type(host_capability) is not HostAdapterCapability
        or not _runtime_host_object_is_live(
            host_capability, "host_capability"
        )
        or host_capability._consumed
        or float(host_capability._clock())
        > host_capability.freshness_deadline
        or operation_kind not in {"adoption", "policy_update"}
        or native_user_event.session_id != session_id
        or native_user_event.invocation_id != invocation_id
        or host_capability.session_id != session_id
        or host_capability.invocation_id != invocation_id
        or governing_runtime.session_id != session_id
        or governing_runtime.invocation_id != invocation_id
        or native_user_event.subject_digest != draft_plan_digest
        or target_worktree_identity != governing_runtime.target_worktree
        or any(not isinstance(item, RequiredCheckCandidate) for item in required_checks)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_REMOTE_POLICY_DECISION: native governing-base decision required"
        )
    if not _consume_runtime_host_object(
        host_capability, "host_capability"
    ):
        raise ValueError(
            "E_REMOTE_POLICY_DECISION: host capability is not issued"
        )
    native_user_event._consumed = True
    host_capability._consumed = True
    decision = object.__new__(ProjectRemotePolicyDecision)
    decision._consumed = False
    values = {
        "operation_kind": operation_kind,
        "draft_plan_digest": draft_plan_digest,
        "source_repository_identity": source_repository_identity,
        "target_repository_identity": target_repository_identity,
        "target_worktree_identity": target_worktree_identity,
        "repository_identity": repository_identity,
        "required_checks": tuple(
            item.selector_digest for item in required_checks
        ),
        "session_id": session_id,
        "invocation_id": invocation_id,
        "runtime_digest": governing_runtime.runtime_digest,
        "freshness_deadline": float(clock()) + float(ttl_seconds),
    }
    for name, value in values.items():
        setattr(decision, name, value)
    decision.decision_digest = contract_digest(
        {
            name: getattr(decision, name)
            for name in (
                "operation_kind",
                "draft_plan_digest",
                "source_repository_identity",
                "target_repository_identity",
                "target_worktree_identity",
                "repository_identity",
                "required_checks",
                "session_id",
                "invocation_id",
                "runtime_digest",
            )
        }
    )
    _register_runtime_host_object(
        decision, "project_remote_policy_decision"
    )
    return decision


class ProjectRemotePolicyUpdateDraft:
    __slots__ = (
        "_consumed",
        "task_id",
        "worktree",
        "repository_identity",
        "generation",
        "path",
        "before_digest",
        "after_bytes",
        "after_digest",
        "task_digest",
        "lease_digest",
        "runtime_digest",
        "required_checks",
        "draft_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ProjectRemotePolicyUpdateDraft":
        raise TypeError("project remote policy draft is host-bound")


def _mint_project_remote_policy_update_draft(
    *,
    task_id: str,
    worktree: str,
    repository_identity: str,
    generation: int,
    path: str,
    before_digest: str,
    after_bytes: bytes,
    task_digest: str,
    lease_digest: str,
    runtime_digest: str,
    required_checks: tuple[str, ...],
) -> ProjectRemotePolicyUpdateDraft:
    draft = object.__new__(ProjectRemotePolicyUpdateDraft)
    draft._consumed = False
    values = {
        "task_id": task_id,
        "worktree": worktree,
        "repository_identity": repository_identity,
        "generation": generation,
        "path": path,
        "before_digest": before_digest,
        "after_bytes": after_bytes,
        "after_digest": f"sha256:{sha256(after_bytes).hexdigest()}",
        "task_digest": task_digest,
        "lease_digest": lease_digest,
        "runtime_digest": runtime_digest,
        "required_checks": required_checks,
    }
    for name, value in values.items():
        setattr(draft, name, value)
    draft.draft_digest = contract_digest(
        {
            "task_id": draft.task_id,
            "worktree": draft.worktree,
            "repository_identity": draft.repository_identity,
            "generation": draft.generation,
            "path": draft.path,
            "before_digest": draft.before_digest,
            "after_digest": draft.after_digest,
            "task_digest": draft.task_digest,
            "lease_digest": draft.lease_digest,
            "runtime_digest": draft.runtime_digest,
            "required_checks": draft.required_checks,
        }
    )
    _register_runtime_host_object(
        draft, "project_remote_policy_update_draft"
    )
    return draft


def _recoverable_project_remote_policy_update_draft(
    *,
    governing_runtime: GoverningRuntimeObservation,
    governing_policy: GoverningPolicy,
    candidate_raw: bytes,
    target_path: Path,
    task_context: Mapping[str, Any],
    lease: Mapping[str, Any],
    repository_identity: str,
    required_checks: tuple[str, ...],
) -> ProjectRemotePolicyUpdateDraft | None:
    state_dir = worktree_git_dir(Path(governing_runtime.target_worktree))
    transactions = state_dir / "codex-control-plane" / "policy-updates"
    if not transactions.exists():
        return None
    if transactions.is_symlink() or not transactions.is_dir():
        raise ValueError(
            "E_REMOTE_POLICY_RECOVERY: transaction inventory is invalid"
        )
    entries = sorted(transactions.iterdir(), key=lambda item: item.name)
    if len(entries) > 64:
        raise ValueError(
            "E_REMOTE_POLICY_RECOVERY: transaction inventory exceeds cap"
        )
    matches: list[ProjectRemotePolicyUpdateDraft] = []
    candidate_digest = f"sha256:{sha256(candidate_raw).hexdigest()}"
    for transaction in entries:
        journal_path = transaction / "journal.json"
        if not journal_path.exists():
            continue
        if (
            transaction.is_symlink()
            or not transaction.is_dir()
            or journal_path.is_symlink()
            or not journal_path.is_file()
            or journal_path.stat().st_size > 131_072
        ):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: transaction record is invalid"
            )
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: transaction journal is invalid"
            ) from error
        if not isinstance(journal, Mapping):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: transaction journal is invalid"
            )
        if (
            set(journal)
            != {
                "schema_version",
                "draft_digest",
                "task_id",
                "task_digest",
                "lease_digest",
                "runtime_digest",
                "generation",
                "before_digest",
                "after_digest",
                "phase",
            }
            or journal.get("schema_version") != 1
            or journal.get("phase")
            not in {"allocating", "prepared", "policy_replaced", "committed"}
            or not isinstance(journal.get("generation"), int)
            or isinstance(journal.get("generation"), bool)
        ):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: transaction journal is invalid"
            )
        phase = str(journal["phase"])
        journal_generation = int(journal["generation"])
        task_generation = task_context.get("generation")
        binding = (
            journal.get("task_id") == task_context.get("task_id")
            and journal.get("task_digest") == task_context.get("task_digest")
            and journal.get("lease_digest") == lease.get("lease_digest")
            and journal.get("runtime_digest")
            == governing_runtime.runtime_digest
            and (
                task_generation == journal_generation
                or (
                    phase in {"policy_replaced", "committed"}
                    and task_generation == journal_generation + 1
                )
            )
            and journal.get("after_digest") == candidate_digest
        )
        if not binding:
            continue
        backup_path = transaction / "project-policy.before.toml"
        after_path = transaction / "project-policy.after.toml"
        target_raw = target_path.read_bytes()
        target_digest = f"sha256:{sha256(target_raw).hexdigest()}"
        if phase == "allocating":
            if target_digest != journal.get("before_digest"):
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: allocating policy drifted"
                )
            before_raw = target_raw
            after_raw = candidate_raw
            if backup_path.exists() and (
                backup_path.is_symlink()
                or not backup_path.is_file()
                or backup_path.stat().st_size > 131_072
                or backup_path.read_bytes() != before_raw
            ):
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: backup binding is invalid"
                )
            if after_path.exists() and (
                after_path.is_symlink()
                or not after_path.is_file()
                or after_path.stat().st_size > 131_072
                or after_path.read_bytes() != after_raw
            ):
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: candidate binding is invalid"
                )
        else:
            if (
                not backup_path.is_file()
                or backup_path.is_symlink()
                or not after_path.is_file()
                or after_path.is_symlink()
                or backup_path.stat().st_size > 131_072
                or after_path.stat().st_size > 131_072
            ):
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: durable policy artifacts are invalid"
                )
            before_raw = backup_path.read_bytes()
            after_raw = after_path.read_bytes()
        before_digest = f"sha256:{sha256(before_raw).hexdigest()}"
        try:
            before_policy = tomllib.loads(before_raw.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: governing policy backup is invalid"
            ) from error
        if (
            before_digest != journal.get("before_digest")
            or after_raw != candidate_raw
            or before_policy != governing_policy.policy
            or before_digest != governing_policy.policy_digest
        ):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: durable policy binding drifted"
            )
        draft = _mint_project_remote_policy_update_draft(
            task_id=str(task_context["task_id"]),
            worktree=governing_runtime.target_worktree,
            repository_identity=repository_identity,
            generation=journal_generation,
            path=str(target_path.resolve()),
            before_digest=before_digest,
            after_bytes=candidate_raw,
            task_digest=str(task_context["task_digest"]),
            lease_digest=str(lease["lease_digest"]),
            runtime_digest=governing_runtime.runtime_digest,
            required_checks=required_checks,
        )
        if (
            journal.get("draft_digest") != draft.draft_digest
            or transaction.name
            != draft.draft_digest.removeprefix("sha256:")
            or target_digest
            not in {draft.before_digest, draft.after_digest}
        ):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: reconstructed draft is invalid"
            )
        matches.append(draft)
    if len(matches) > 1:
        raise ValueError(
            "E_REMOTE_POLICY_RECOVERY: transaction is ambiguous"
        )
    return matches[0] if matches else None


def project_remote_policy_update_plan(
    *,
    governing_runtime: object,
    governing_policy: object,
    candidate_policy_path: Path | str,
    task_context: Mapping[str, Any],
    lease: Mapping[str, Any],
    repository_identity: str,
    required_checks: tuple[RequiredCheckCandidate, ...],
) -> ProjectRemotePolicyUpdateDraft:
    if (
        type(governing_runtime) is not GoverningRuntimeObservation
        or type(governing_policy) is not GoverningPolicy
        or not _governing_runtime_observation_is_live(governing_runtime)
        or not _governing_policy_is_issued_for_runtime(
            governing_policy, governing_runtime
        )
        or governing_runtime._consumed
        or governing_policy._consumed
        or governing_policy.runtime_digest != governing_runtime.runtime_digest
        or governing_policy.lock_digest != governing_runtime.lock_digest
        or governing_policy.policy_digest != governing_runtime.policy_digest
        or lease.get("task_id") != task_context.get("task_id")
        or lease.get("lease_digest") != task_context.get("lease_digest")
        or lease.get("worktree") != governing_runtime.target_worktree
        or governing_runtime.target_worktree != repository_identity
        or any(not isinstance(item, RequiredCheckCandidate) for item in required_checks)
    ):
        raise ValueError("E_REMOTE_POLICY_DRAFT: governing bindings are invalid")
    candidate_path = Path(candidate_policy_path)
    target_path = (
        Path(governing_runtime.target_worktree)
        / ".codex"
        / "project-policy.toml"
    )
    if (
        candidate_path.is_symlink()
        or not candidate_path.is_file()
        or candidate_path.stat().st_size > 131_072
        or target_path.is_symlink()
        or not target_path.is_file()
        or target_path.stat().st_size > 131_072
    ):
        raise ValueError("E_REMOTE_POLICY_DRAFT: policy path is not allowlisted")
    candidate_raw = candidate_path.read_bytes()
    before_raw = target_path.read_bytes()
    candidate = load_policy(candidate_path)
    before_policy = load_policy(target_path)
    if validate_policy(candidate):
        raise ValueError("E_REMOTE_POLICY_DRAFT: candidate policy is invalid")
    immutable_sections = {
        key: value
        for key, value in governing_policy.policy.items()
        if key != "git"
    }
    candidate_immutable = {
        key: value for key, value in candidate.items() if key != "git"
    }
    if candidate_immutable != immutable_sections:
        raise ValueError(
            "E_REMOTE_POLICY_DRAFT: policy-only update changed unrelated keys"
        )
    allowed_git = {"remote", "base_branch"}
    for key, value in candidate["git"].items():
        if key not in allowed_git and value != governing_policy.policy["git"].get(key):
            raise ValueError(
                "E_REMOTE_POLICY_DRAFT: policy-only update changed unrelated Git keys"
            )
    normalized_paths = tuple(
        normalize_scope(item) for item in lease.get("paths", ())
    )
    if (
        not normalized_paths
        or any(item is None for item in normalized_paths)
        or not any(
            scope_owns(str(item), ".codex/project-policy.toml")
            for item in normalized_paths
        )
        or not isinstance(task_context.get("generation"), int)
        or isinstance(task_context.get("generation"), bool)
    ):
        raise ValueError(
            "E_REMOTE_POLICY_DRAFT: exact policy writer lease is required"
        )
    required_check_digests = tuple(
        item.selector_digest for item in required_checks
    )
    recovery = _recoverable_project_remote_policy_update_draft(
        governing_runtime=governing_runtime,
        governing_policy=governing_policy,
        candidate_raw=candidate_raw,
        target_path=target_path,
        task_context=task_context,
        lease=lease,
        repository_identity=repository_identity,
        required_checks=required_check_digests,
    )
    if recovery is not None:
        return recovery
    if (
        before_policy != governing_policy.policy
        or f"sha256:{sha256(before_raw).hexdigest()}"
        != governing_policy.policy_digest
    ):
        raise ValueError(
            "E_REMOTE_POLICY_DRAFT: target policy is not the governing policy"
        )
    return _mint_project_remote_policy_update_draft(
        task_id=str(task_context["task_id"]),
        worktree=governing_runtime.target_worktree,
        repository_identity=repository_identity,
        generation=int(task_context["generation"]),
        path=str(target_path.resolve()),
        before_digest=f"sha256:{sha256(before_raw).hexdigest()}",
        after_bytes=candidate_raw,
        task_digest=str(task_context["task_digest"]),
        lease_digest=str(lease["lease_digest"]),
        runtime_digest=governing_runtime.runtime_digest,
        required_checks=required_check_digests,
    )


@dataclass(frozen=True)
class ProjectRemotePolicyUpdateReceipt:
    draft_digest: str
    before_digest: str
    after_digest: str
    generation: int
    runtime_digest: str


def _policy_update_receipt(
    draft: ProjectRemotePolicyUpdateDraft,
) -> ProjectRemotePolicyUpdateReceipt:
    return ProjectRemotePolicyUpdateReceipt(
        draft_digest=draft.draft_digest,
        before_digest=draft.before_digest,
        after_digest=draft.after_digest,
        generation=draft.generation + 1,
        runtime_digest=draft.runtime_digest,
    )


def _recover_policy_update_locked(
    *,
    draft: ProjectRemotePolicyUpdateDraft,
    state: dict[str, Any],
    state_path: Path,
    policy_path: Path,
    journal_path: Path,
    backup_path: Path,
    after_path: Path,
) -> ProjectRemotePolicyUpdateReceipt:
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "E_REMOTE_POLICY_RECOVERY: journal is unavailable"
        ) from error
    if (
        journal_path.is_symlink()
        or not isinstance(journal, Mapping)
        or set(journal)
        != {
            "schema_version",
            "draft_digest",
            "task_id",
            "task_digest",
            "lease_digest",
            "runtime_digest",
            "generation",
            "before_digest",
            "after_digest",
            "phase",
        }
        or journal.get("schema_version") != 1
        or journal.get("draft_digest") != draft.draft_digest
        or journal.get("task_id") != draft.task_id
        or journal.get("task_digest") != draft.task_digest
        or journal.get("lease_digest") != draft.lease_digest
        or journal.get("runtime_digest") != draft.runtime_digest
        or journal.get("generation") != draft.generation
        or journal.get("before_digest") != draft.before_digest
        or journal.get("after_digest") != draft.after_digest
        or journal.get("phase")
        not in {"allocating", "prepared", "policy_replaced", "committed"}
    ):
        raise ValueError(
            "E_REMOTE_POLICY_RECOVERY: journal binding is invalid"
        )
    phase = str(journal["phase"])
    current_bytes = policy_path.read_bytes()
    current_digest = f"sha256:{sha256(current_bytes).hexdigest()}"
    if phase == "allocating":
        if current_digest != draft.before_digest:
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: allocating policy drifted"
            )
        if backup_path.exists():
            if (
                backup_path.is_symlink()
                or f"sha256:{sha256(backup_path.read_bytes()).hexdigest()}"
                != draft.before_digest
            ):
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: backup binding is invalid"
                )
        else:
            _atomic_bytes(backup_path, current_bytes)
        if after_path.exists():
            if (
                after_path.is_symlink()
                or f"sha256:{sha256(after_path.read_bytes()).hexdigest()}"
                != draft.after_digest
            ):
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: candidate binding is invalid"
                )
        else:
            _atomic_bytes(after_path, draft.after_bytes)
        journal["phase"] = "prepared"
        _atomic_policy_json(journal_path, journal)
        phase = "prepared"
    if (
        not backup_path.is_file()
        or backup_path.is_symlink()
        or f"sha256:{sha256(backup_path.read_bytes()).hexdigest()}"
        != draft.before_digest
        or not after_path.is_file()
        or after_path.is_symlink()
        or f"sha256:{sha256(after_path.read_bytes()).hexdigest()}"
        != draft.after_digest
        or after_path.read_bytes() != draft.after_bytes
    ):
        raise ValueError(
            "E_REMOTE_POLICY_RECOVERY: durable policy artifacts are invalid"
        )
    if phase == "prepared":
        if current_digest == draft.before_digest:
            _atomic_bytes(policy_path, after_path.read_bytes())
        elif current_digest != draft.after_digest:
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: prepared policy drifted"
            )
        if (
            f"sha256:{sha256(policy_path.read_bytes()).hexdigest()}"
            != draft.after_digest
            or validate_policy(load_policy(policy_path))
        ):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: resulting policy is invalid"
            )
        journal["phase"] = "policy_replaced"
        _atomic_policy_json(journal_path, journal)
        phase = "policy_replaced"
    if phase == "policy_replaced":
        if (
            f"sha256:{sha256(policy_path.read_bytes()).hexdigest()}"
            != draft.after_digest
        ):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: replaced policy drifted"
            )
        if (
            state.get("generation") == draft.generation
            and state.get("remote_policy_update_digest") is None
        ):
            state["generation"] = draft.generation + 1
            state["remote_policy_update_digest"] = draft.draft_digest
            _atomic_policy_json(state_path, state)
        elif not (
            state.get("generation") == draft.generation + 1
            and state.get("remote_policy_update_digest")
            == draft.draft_digest
        ):
            raise ValueError(
                "E_REMOTE_POLICY_RECOVERY: task state is ambiguous"
            )
        journal["phase"] = "committed"
        _atomic_policy_json(journal_path, journal)
        phase = "committed"
    if not (
        phase == "committed"
        and f"sha256:{sha256(policy_path.read_bytes()).hexdigest()}"
        == draft.after_digest
        and state.get("generation") == draft.generation + 1
        and state.get("remote_policy_update_digest")
        == draft.draft_digest
    ):
        raise ValueError(
            "E_REMOTE_POLICY_RECOVERY: committed state is incomplete"
        )
    return _policy_update_receipt(draft)


def apply_project_remote_policy_update(
    draft: object,
    *,
    governing_runtime: object,
    remote_policy_decision: object,
    authorization: object,
    expected_generation: int,
    clock: object,
) -> ProjectRemotePolicyUpdateReceipt:
    if (
        type(draft) is not ProjectRemotePolicyUpdateDraft
        or not _runtime_host_object_is_live(
            draft, "project_remote_policy_update_draft"
        )
        or draft._consumed
        or type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or float(clock()) > governing_runtime.freshness_deadline
        or draft.runtime_digest != governing_runtime.runtime_digest
        or draft.worktree != governing_runtime.target_worktree
        or type(remote_policy_decision) is not ProjectRemotePolicyDecision
        or not _runtime_host_object_is_live(
            remote_policy_decision, "project_remote_policy_decision"
        )
        or remote_policy_decision._consumed
        or remote_policy_decision.runtime_digest
        != governing_runtime.runtime_digest
        or remote_policy_decision.draft_plan_digest != draft.draft_digest
        or remote_policy_decision.operation_kind != "policy_update"
        or remote_policy_decision.target_worktree_identity != draft.worktree
        or remote_policy_decision.target_repository_identity
        != draft.repository_identity
        or remote_policy_decision.repository_identity
        != draft.repository_identity
        or remote_policy_decision.required_checks != draft.required_checks
        or remote_policy_decision.session_id
        != governing_runtime.session_id
        or remote_policy_decision.invocation_id
        != governing_runtime.invocation_id
        or float(clock()) > remote_policy_decision.freshness_deadline
        or type(authorization) is not TrustedAuthorization
        or not isinstance(expected_generation, int)
        or isinstance(expected_generation, bool)
        or expected_generation < 0
        or expected_generation != draft.generation
    ):
        raise ValueError("E_REMOTE_POLICY_APPLY: bound draft and decision required")
    path = Path(draft.path)
    worktree = Path(draft.worktree)
    state_dir = worktree_git_dir(worktree)
    state_path = (
        state_dir
        / "codex-control-plane"
        / "tasks"
        / f"{draft.task_id}.json"
    )
    lease_path = (
        state_dir
        / "codex-control-plane"
        / "leases"
        / f"{draft.task_id}.json"
    )
    head_process = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        env=git_environment(),
    )
    if head_process.returncode != 0:
        raise ValueError(
            "E_REMOTE_POLICY_APPLY: policy task or lease binding drifted"
        )
    expected_head = head_process.stdout.strip()
    from control_plane.lifecycle import (
        _common_git_dir,
        _common_lease_lock,
        _task_guard,
    )

    common_dir = _common_git_dir(state_dir)
    with _common_lease_lock(common_dir):
        with _task_guard(state_dir, draft.task_id):
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                live_lease = json.loads(
                    lease_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    "E_REMOTE_POLICY_APPLY: task or lease state is unavailable"
                ) from error
            if (
                path.is_symlink()
                or not path.is_file()
                or state.get("task_id") != draft.task_id
                or state.get("task_digest") != draft.task_digest
                or live_lease.get("task_id") != draft.task_id
                or live_lease.get("worktree") != draft.worktree
                or live_lease.get("lease_digest") != draft.lease_digest
                or live_lease.get("lease_digest")
                != contract_digest(
                    {
                        key: value
                        for key, value in live_lease.items()
                        if key != "lease_digest"
                    }
                )
            ):
                raise ValueError(
                    "E_REMOTE_POLICY_APPLY: policy task or lease binding drifted"
                )
            consume_authorization(
                authorization,
                expected_task_digest=draft.task_digest,
                expected_session_id=remote_policy_decision.session_id,
                expected_repository_identity=draft.worktree,
                expected_worktree_identity=draft.worktree,
                expected_branch=str(state.get("branch")),
                expected_head=expected_head,
                expected_subject_digest=draft.draft_digest,
                expected_scope_paths=(".codex/project-policy.toml",),
                expected_effect="local_write",
                expected_operation_nonce=authorization.operation_nonce,
                expected_invocation_id=remote_policy_decision.invocation_id,
                clock=clock,
            )
            transaction_root = (
                state_dir
                / "codex-control-plane"
                / "policy-updates"
                / draft.draft_digest.removeprefix("sha256:")
            )
            journal_path = transaction_root / "journal.json"
            backup_path = transaction_root / "project-policy.before.toml"
            after_path = transaction_root / "project-policy.after.toml"
            if transaction_root.is_symlink():
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: transaction root is invalid"
                )
            if journal_path.exists():
                receipt = _recover_policy_update_locked(
                    draft=draft,
                    state=state,
                    state_path=state_path,
                    policy_path=path,
                    journal_path=journal_path,
                    backup_path=backup_path,
                    after_path=after_path,
                )
                if not _consume_runtime_host_object(
                    draft, "project_remote_policy_update_draft"
                ) or not _consume_runtime_host_object(
                    remote_policy_decision,
                    "project_remote_policy_decision",
                ) or not _consume_governing_runtime_observation(
                    governing_runtime
                ):
                    raise ValueError(
                        "E_REMOTE_POLICY_APPLY: governing bindings are not issued"
                    )
                draft._consumed = True
                remote_policy_decision._consumed = True
                governing_runtime._consumed = True
                return receipt
            if backup_path.exists() or after_path.exists():
                raise ValueError(
                    "E_REMOTE_POLICY_RECOVERY: orphaned policy artifacts"
                )
            if state.get("generation") != expected_generation:
                raise ValueError(
                    "E_REMOTE_POLICY_APPLY: policy task generation drifted"
                )
            before_bytes = path.read_bytes()
            if (
                f"sha256:{sha256(before_bytes).hexdigest()}"
                != draft.before_digest
            ):
                raise ValueError("E_REMOTE_POLICY_APPLY: policy bytes drifted")
            transaction_root.mkdir(parents=True, exist_ok=True)
            journal = {
                "schema_version": 1,
                "draft_digest": draft.draft_digest,
                "task_id": draft.task_id,
                "task_digest": draft.task_digest,
                "lease_digest": draft.lease_digest,
                "runtime_digest": draft.runtime_digest,
                "generation": expected_generation,
                "before_digest": draft.before_digest,
                "after_digest": draft.after_digest,
                "phase": "allocating",
            }
            _atomic_policy_json(journal_path, journal)
            receipt = _recover_policy_update_locked(
                draft=draft,
                state=state,
                state_path=state_path,
                policy_path=path,
                journal_path=journal_path,
                backup_path=backup_path,
                after_path=after_path,
            )
            if not _consume_runtime_host_object(
                draft, "project_remote_policy_update_draft"
            ) or not _consume_runtime_host_object(
                remote_policy_decision,
                "project_remote_policy_decision",
            ) or not _consume_governing_runtime_observation(
                governing_runtime
            ):
                raise ValueError(
                    "E_REMOTE_POLICY_APPLY: governing bindings are not issued"
                )
            draft._consumed = True
            remote_policy_decision._consumed = True
            governing_runtime._consumed = True
            return receipt


class PolicyError(Exception):
    """Raised when a policy file cannot be read or parsed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PolicyIssue:
    """One deterministic policy validation failure."""

    code: str
    path: str
    message: str


def load_policy(path: Path) -> dict[str, Any]:
    """Load TOML without silently substituting policy defaults."""

    try:
        with path.open("rb") as policy_file:
            data = tomllib.load(policy_file)
    except FileNotFoundError as error:
        raise PolicyError(
            "E_POLICY_NOT_FOUND", f"Project policy not found: {path}"
        ) from error
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PolicyError(
            "E_POLICY_PARSE", f"Project policy could not be parsed: {path}"
        ) from error

    return data


def _value_at(policy: Mapping[str, Any], dotted_path: str) -> Any:
    value: Any = policy
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _missing_issues(policy: Mapping[str, Any]) -> list[PolicyIssue]:
    required_paths = (
        "schema_version",
        "project_name",
        "project_kind",
        "git.remote",
        "git.base_branch",
        "git.require_pull_request",
        "git.allow_direct_base_push",
        "git.integration_strategy",
        "reasoning.model",
        "reasoning.default",
        "reasoning.plan",
        "reasoning.subagent",
        "reasoning.normal_max_workers",
        "reasoning.sequential_default",
        "documentation.require_impact_assessment",
        "release.official_source",
        "release.require_manifest",
        "release.allow_local_official_release",
        "gates.T0.required",
        "gates.T1.required",
        "gates.T2.required",
        "gates.T3.required",
    )
    return [
        PolicyIssue("P_MISSING", path, f"Required policy key is missing: {path}")
        for path in required_paths
        if _value_at(policy, path) is None
    ]


def _unknown_issues(policy: Mapping[str, Any]) -> list[PolicyIssue]:
    issues: list[PolicyIssue] = []
    for path, allowed in ALLOWED_KEYS.items():
        section = policy if path == "" else _value_at(policy, path)
        if not isinstance(section, Mapping):
            continue
        for key in section:
            if key not in allowed:
                dotted = f"{path}.{key}" if path else str(key)
                issues.append(
                    PolicyIssue(
                        "P_UNKNOWN",
                        dotted,
                        f"Unknown policy key for schema 1: {dotted}",
                    )
                )
    return issues


def _is_safe_git_name(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("-"):
        return False
    if re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value):
        return False
    if (
        value.startswith("/")
        or value.endswith(("/", "."))
        or "//" in value
        or ".." in value
        or "@{" in value
    ):
        return False
    return all(
        component not in {"", ".", ".."}
        and not component.startswith(".")
        and not component.endswith(".lock")
        for component in value.split("/")
    )


def _is_nonempty_text(value: Any, *, max_length: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= max_length
        and not re.search(r"[\x00-\x1f\x7f]", value)
    )


def validate_policy(policy: Mapping[str, Any]) -> list[PolicyIssue]:
    """Return every policy issue so configuration can be fixed in one pass."""

    issues = _missing_issues(policy)
    issues.extend(_unknown_issues(policy))

    schema_version = policy.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SUPPORTED_SCHEMA_VERSION
    ):
        issues.append(
            PolicyIssue(
                "P_SCHEMA",
                "schema_version",
                f"Only schema version {SUPPORTED_SCHEMA_VERSION} is supported.",
            )
        )

    project_name = policy.get("project_name")
    if project_name is not None and not _is_nonempty_text(
        project_name, max_length=120
    ):
        issues.append(
            PolicyIssue(
                "P_PROJECT_NAME",
                "project_name",
                "Project name must be nonempty text without control characters.",
            )
        )

    project_kind = policy.get("project_kind")
    if project_kind is not None and (
        not _is_nonempty_text(project_kind, max_length=64)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project_kind) is None
    ):
        issues.append(
            PolicyIssue(
                "P_PROJECT_KIND",
                "project_kind",
                "Project kind must be a nonempty identifier using letters, digits, dots, underscores, or hyphens.",
            )
        )

    for path in ("reasoning.default", "reasoning.plan", "reasoning.subagent"):
        level = _value_at(policy, path)
        if level is not None and (
            not isinstance(level, str) or level not in ALLOWED_REASONING_LEVELS
        ):
            issues.append(
                PolicyIssue(
                    "P_REASONING",
                    path,
                    f"Unsupported reasoning level at {path}: {level!r}",
                )
            )

    if _value_at(policy, "reasoning.model") not in (None, "gpt-5.6-sol"):
        issues.append(
            PolicyIssue(
                "P_MODEL",
                "reasoning.model",
                "The project policy requires gpt-5.6-sol.",
            )
        )

    workers = _value_at(policy, "reasoning.normal_max_workers")
    if workers is not None and (
        isinstance(workers, bool)
        or not isinstance(workers, int)
        or not 1 <= workers <= 2
    ):
        issues.append(
            PolicyIssue(
                "P_WORKERS",
                "reasoning.normal_max_workers",
                "Normal concurrency must be an integer between 1 and 2.",
            )
        )

    sequential_default = _value_at(policy, "reasoning.sequential_default")
    if sequential_default is not None and sequential_default is not True:
        issues.append(
            PolicyIssue(
                "P_SEQUENTIAL",
                "reasoning.sequential_default",
                "Sequential execution must remain the default.",
            )
        )

    require_pull_request = _value_at(policy, "git.require_pull_request")
    if require_pull_request is not None and require_pull_request is not True:
        issues.append(
            PolicyIssue(
                "P_PR_REQUIRED",
                "git.require_pull_request",
                "Protected-base integration must require a Pull Request.",
            )
        )

    remote = _value_at(policy, "git.remote")
    if remote is not None and not _is_safe_git_name(remote):
        issues.append(
            PolicyIssue(
                "P_REMOTE",
                "git.remote",
                "Remote must be a safe Git name and cannot be an option.",
            )
        )

    base_branch = _value_at(policy, "git.base_branch")
    if base_branch is not None and (
        base_branch == "HEAD" or not _is_safe_git_name(base_branch)
    ):
        issues.append(
            PolicyIssue(
                "P_BASE_BRANCH",
                "git.base_branch",
                "Base branch must be a valid, unambiguous Git branch name.",
            )
        )

    integration_strategy = _value_at(policy, "git.integration_strategy")
    if (
        integration_strategy is not None
        and (
            not isinstance(integration_strategy, str)
            or integration_strategy not in ALLOWED_INTEGRATION_STRATEGIES
        )
    ):
        issues.append(
            PolicyIssue(
                "P_INTEGRATION",
                "git.integration_strategy",
                "Integration strategy must be squash, merge-commit, or rebase-merge.",
            )
        )

    allow_direct_base_push = _value_at(policy, "git.allow_direct_base_push")
    if allow_direct_base_push is not None and allow_direct_base_push is not False:
        issues.append(
            PolicyIssue(
                "P_BASE_PUSH",
                "git.allow_direct_base_push",
                "Direct pushes to the protected base branch are forbidden.",
            )
        )

    if _value_at(policy, "release.official_source") not in (None, "remote_base"):
        issues.append(
            PolicyIssue(
                "P_RELEASE_SOURCE",
                "release.official_source",
                "Official releases must use the protected remote base.",
            )
        )

    require_impact_assessment = _value_at(
        policy, "documentation.require_impact_assessment"
    )
    if (
        require_impact_assessment is not None
        and require_impact_assessment is not True
    ):
        issues.append(
            PolicyIssue(
                "P_DOC_IMPACT",
                "documentation.require_impact_assessment",
                "Documentation impact assessment must remain enabled.",
            )
        )

    require_manifest = _value_at(policy, "release.require_manifest")
    if require_manifest is not None and require_manifest is not True:
        issues.append(
            PolicyIssue(
                "P_RELEASE_MANIFEST",
                "release.require_manifest",
                "Official releases require a release manifest.",
            )
        )

    allow_local_release = _value_at(
        policy, "release.allow_local_official_release"
    )
    if allow_local_release is not None and allow_local_release is not False:
        issues.append(
            PolicyIssue(
                "P_LOCAL_RELEASE",
                "release.allow_local_official_release",
                "A local worktree cannot be the source of an official release.",
            )
        )

    for tier in ("T0", "T1", "T2", "T3"):
        path = f"gates.{tier}.required"
        gates = _value_at(policy, path)
        if gates is not None and (
            not isinstance(gates, list)
            or not gates
            or not all(isinstance(item, str) and item for item in gates)
        ):
            issues.append(
                PolicyIssue(
                    "P_GATES",
                    path,
                    f"{tier} must contain at least one named gate.",
                )
            )

    return issues
