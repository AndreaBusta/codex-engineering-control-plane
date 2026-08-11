"""Closed contracts for a skill-led, deterministic local engineering run."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

from control_plane.contracts import (
    SHA256_DIGEST,
    ContractIssue,
    contract_digest,
    safe_scope_path,
    validate_task_envelope,
    validate_task_id,
)
from control_plane.git_state import evaluate_preflight
from control_plane.lifecycle import ORDERED_STATES, TaskLease, TaskStore
from control_plane.materialization import inspect_tracked_materialization
from control_plane.repository import (
    assert_no_external_git_filters,
    discover_repository,
    trusted_git_argv,
    trusted_git_environment,
    worktree_git_dir,
)
from control_plane.scopes import scope_owns
from control_plane.routing import deferred_effects_for_outcome


RUN_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN"})
VISIBLE_STATUSES = frozenset(
    {"PLANIFICANDO", "TRABAJANDO", "VERIFICANDO", "PR LISTA", "BLOCKED"}
)
MAX_EXECUTIONS = 3
_GATE_TIMEOUT_SECONDS = 300.0
_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40,64}$", re.ASCII)
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)
_LOCAL_GATE_IDS = (
    "gate.relevant-tests",
    "gate.policy-check",
    "gate.registry-check",
    "gate.doctor",
    "gate.diff-review",
)
_PLAN_BOUND_GATE_IDS = frozenset({"gate.written-plan", "gate.rollback-plan"})
_DEFERRED_REVIEW_GATE_IDS = frozenset({
    "gate.independent-review", "gate.security-review",
})
_DEFERRED_OUTCOME_GATE_IDS = frozenset({
    "gate.pull-request", "gate.release-proof",
})
_KNOWN_GATE_IDS = frozenset(_LOCAL_GATE_IDS).union(
    _PLAN_BOUND_GATE_IDS, _DEFERRED_REVIEW_GATE_IDS, _DEFERRED_OUTCOME_GATE_IDS,
)
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$",
    re.ASCII,
)
_UNITTEST_DISCOVERY_PROGRAM = (
    "import sys, unittest\n"
    "suite = unittest.defaultTestLoader.discover('tests')\n"
    "result = unittest.TextTestRunner(verbosity=1).run(suite)\n"
    "sys.exit(0 if result.wasSuccessful() and result.testsRun > 0 else 1)\n"
)
_RUN_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "decision_digest",
        "repository",
        "branch",
        "head",
        "session_id",
        "requested_outcome",
        "tier",
        "profiles",
        "scope_paths",
        "required_gates",
        "deferred_effects",
        "authorizes",
        "max_executions",
        "prepared_at",
        "plan_digest",
    }
)
_OUTCOME_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "branch",
        "scope_paths_digest",
        "review_head",
        "reviewed_tree_digest",
        "reviewed_diff_digest",
        "committed_head",
        "pushed_head",
        "pull_request_digest",
        "checks_digest",
        "merge_sha",
        "consumed_effect_ids",
        "authorizes",
        "binding_digest",
    }
)
_OUTCOME_EFFECTS = {
    "local_change": ("local_write",),
    "commit": ("local_write", "commit"),
    "pull_request": ("local_write", "commit", "remote_write", "pull_request"),
    "integration": (
        "local_write", "commit", "remote_write", "pull_request", "integration",
    ),
}
_GATE_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "attempt",
        "gate_id",
        "status",
        "command_digest",
        "output_digest",
        "before_snapshot_digest",
        "after_snapshot_digest",
        "error_code",
        "observed_at",
        "receipt_digest",
    }
)
_ROLLBACK_PLAN_KEYS = frozenset({
    "schema_version", "kind", "task_id", "task_digest", "run_plan_digest",
    "run_revision_digest", "attempt", "repository", "branch", "head",
    "scope_paths_digest", "trigger_conditions", "rollback_steps",
    "post_rollback_checks", "irreversible_boundaries", "status",
    "observation_digest", "observed_at", "authorizes", "plan_digest",
})
_REVIEW_RESULT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "reviewed_head",
        "reviewer_kind",
        "reviewer_context_digest",
        "critical",
        "important",
        "minor",
        "status",
        "authorizes",
        "observed_at",
        "result_digest",
    }
)
_REVIEW_PACKET_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "run_revision_digest",
        "attempt",
        "attempt_digest",
        "repository",
        "base_head",
        "branch",
        "reviewed_head",
        "review_kind",
        "criteria_digest",
        "scope_paths",
        "artifact_digest",
        "diff_digest",
        "diff_size",
        "evidence_summaries",
        "authorizes",
        "packet_digest",
    }
)
_REVIEW_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "attempt",
        "repository",
        "base_head",
        "reviewed_head",
        "scope_paths",
        "untracked_modes",
        "diff_digest",
        "diff_size",
        "authorizes",
        "artifact_digest",
    }
)
_INDEPENDENT_REVIEW_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "run_revision_digest",
        "attempt",
        "attempt_digest",
        "repository",
        "base_head",
        "branch",
        "reviewed_head",
        "review_packet_digest",
        "artifact_digest",
        "diff_digest",
        "scope_paths_digest",
        "review_kind",
        "criteria_digest",
        "findings_digest",
        "reviewer_identity_digest",
        "observation_digest",
        "critical",
        "important",
        "status",
        "authorizes",
        "observed_at",
        "receipt_digest",
    }
)
MAX_REVIEW_PACKET_BYTES = 4096
MAX_REVIEW_PACKET_STORAGE_BYTES = 8192
MAX_ROLLBACK_PLAN_BYTES = 16_384
MAX_REVIEW_SCOPE_PATH_LENGTH = 240
MAX_REVIEW_DIFF_BYTES = 1024 * 1024
_RUN_SUMMARY_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "head",
        "lifecycle_state",
        "visible_status",
        "attempt_count",
        "gate_status",
        "gate_receipt_digests",
        "review_result_digest",
        "blocked_reason_code",
        "observed_at",
        "summary_digest",
    }
)
_DELIVERY_AUDIT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "visible_status",
        "lifecycle_state",
        "observed",
        "receipt_digests",
        "attempts",
        "latest_attempt",
        "pending_repair",
        "block_reason_code",
        "missing_evidence",
        "next_safe_action",
        "authorizes",
        "audit_digest",
    }
)
_DELIVERY_AUDIT_NEXT_ACTIONS = frozenset(
    {
        "CONTINUE_PLANNING",
        "CONTINUE_IMPLEMENTATION",
        "COMPLETE_VERIFICATION",
        "REPAIR_IMPLEMENTATION",
        "PREPARE_COMMIT",
        "PREPARE_PUSH",
        "PREPARE_PULL_REQUEST",
        "OBSERVE_PR_READINESS",
        "PREPARE_INTEGRATION",
        "REQUEST_HUMAN_INTERVENTION",
        "NO_ACTION",
    }
)
_DELIVERY_AUDIT_MAX_BYTES = 4096
_RUN_REVISION_KEYS = frozenset({
    "schema_version", "kind", "task_id", "task_digest", "run_plan_digest",
    "revision", "parent_revision_digest", "first_attempt", "repository",
    "branch", "head", "scope_paths", "reason", "source_attempt_digest",
    "source_review_receipt_digest", "source_diff_digest", "authorizes", "revision_digest",
})


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code, path, message)


def _digest(value: object) -> bool:
    return isinstance(value, str) and SHA256_DIGEST.fullmatch(value) is not None


def _timestamp(value: object) -> bool:
    return isinstance(value, str) and _TIMESTAMP.fullmatch(value) is not None


def _tier_review_gates_complete(tier: object, gates: object) -> bool:
    """Require the review set implied by every review-bearing tier."""

    if not isinstance(gates, (list, tuple)):
        return False
    selected = set(gates)
    if tier == "T2":
        return "gate.independent-review" in selected
    if tier == "T3":
        return {
            "gate.independent-review", "gate.security-review",
        }.issubset(selected)
    return True


def _closed_schema(
    value: Mapping[str, Any],
    *,
    keys: frozenset[str],
    kind: str,
) -> list[ContractIssue]:
    if set(value) != keys or value.get("schema_version") != 1 or value.get("kind") != kind:
        return [_issue("RUN_SCHEMA", "", f"{kind} must use its closed schema.")]
    return []


def _digest_issue(
    value: Mapping[str, Any], digest_key: str, code: str
) -> list[ContractIssue]:
    semantic = {key: item for key, item in value.items() if key != digest_key}
    if value.get(digest_key) != contract_digest(semantic):
        return [_issue(code, digest_key, "Contract digest does not match its fields.")]
    return []


def build_run_plan(
    *,
    task: Mapping[str, Any],
    decision: Mapping[str, Any],
    repository: Path,
    branch: str,
    head: str,
    session_id: str,
    prepared_at: str,
) -> dict[str, Any]:
    if validate_task_envelope(task):
        raise ValueError("E_RUN_TASK: valid TaskEnvelope v1 required")
    clarification = decision.get("interaction", {}).get(
        "clarification_gate", {}
    )
    try:
        deferred_effects = deferred_effects_for_outcome(
            task.get("requested_outcome")
        )
    except ValueError as error:
        raise ValueError("E_RUN_OUTCOME: supported outcome required") from error
    approval_boundaries = decision.get("approval_boundaries")
    if (
        not isinstance(clarification, Mapping)
        or clarification.get("level") != "low"
        or clarification.get("status") != "autonomous"
        or clarification.get("decision_ready") is not True
        or not isinstance(approval_boundaries, (list, tuple))
        or not all(isinstance(item, str) for item in approval_boundaries)
        or "local_write" in approval_boundaries
        or any(item not in deferred_effects for item in approval_boundaries)
        or decision.get("authorization", {}).get("local_write") is not True
        or decision.get("errors", []) not in ([], ())
    ):
        raise ValueError("E_RUN_CLARIFICATION: autonomous low-risk decision required")
    summary = decision.get("summary", {})
    profile = summary.get("project_profile", {}) if isinstance(summary, Mapping) else {}
    profiles = profile.get("profiles", []) if isinstance(profile, Mapping) else []
    required_gates = decision.get("required_gates", [])
    root = repository.resolve()
    if (
        not root.is_absolute()
        or not isinstance(branch, str)
        or _BRANCH.fullmatch(branch) is None
        or ".." in branch
        or not isinstance(head, str)
        or _GIT_OBJECT_ID.fullmatch(head) is None
        or not validate_task_id(session_id)
        or not _timestamp(prepared_at)
        or not _digest(decision.get("decision_digest"))
        or not isinstance(summary, Mapping)
        or summary.get("tier") not in {"T0", "T1", "T2", "T3"}
        or not isinstance(profiles, list)
        or not profiles
        or not all(isinstance(item, str) and item for item in profiles)
        or not isinstance(required_gates, list)
        or not all(isinstance(item, str) and item in _KNOWN_GATE_IDS for item in required_gates)
        or not _tier_review_gates_complete(summary.get("tier"), required_gates)
    ):
        raise ValueError("E_RUN_BINDING: run plan binding is invalid")
    scope_paths = task.get("scope_paths")
    if (
        not isinstance(scope_paths, list)
        or not scope_paths
        or not all(safe_scope_path(item) for item in scope_paths)
    ):
        raise ValueError("E_RUN_SCOPE: run scope is invalid")
    core = {
        "schema_version": 1,
        "kind": "RunPlanV1",
        "task_id": task["task_id"],
        "task_digest": contract_digest(task),
        "decision_digest": decision["decision_digest"],
        "repository": str(root),
        "branch": branch,
        "head": head,
        "session_id": session_id,
        "requested_outcome": task["requested_outcome"],
        "tier": summary["tier"],
        "profiles": sorted(set(profiles)),
        "scope_paths": list(scope_paths),
        "required_gates": sorted(set(required_gates)),
        "deferred_effects": deferred_effects,
        "authorizes": False,
        "max_executions": MAX_EXECUTIONS,
        "prepared_at": prepared_at,
    }
    return {**core, "plan_digest": contract_digest(core)}


def validate_run_plan(value: Mapping[str, Any]) -> list[ContractIssue]:
    issues = _closed_schema(value, keys=_RUN_PLAN_KEYS, kind="RunPlanV1")
    if issues:
        return issues
    requested_outcome = value.get("requested_outcome")
    outcome_valid = (
        isinstance(requested_outcome, str)
        and requested_outcome in _OUTCOME_EFFECTS
    )
    if (
        not validate_task_id(value.get("task_id"))
        or not _digest(value.get("task_digest"))
        or not _digest(value.get("decision_digest"))
        or not isinstance(value.get("repository"), str)
        or not Path(str(value["repository"])).is_absolute()
        or not isinstance(value.get("branch"), str)
        or _BRANCH.fullmatch(str(value["branch"])) is None
        or not isinstance(value.get("head"), str)
        or _GIT_OBJECT_ID.fullmatch(str(value["head"])) is None
        or not validate_task_id(value.get("session_id"))
        or not outcome_valid
        or value.get("tier") not in {"T0", "T1", "T2", "T3"}
        or not isinstance(value.get("profiles"), list)
        or not value.get("profiles")
        or len(value["profiles"]) != len(set(value["profiles"]))
        or not all(isinstance(item, str) and item for item in value["profiles"])
        or not isinstance(value.get("scope_paths"), list)
        or not value.get("scope_paths")
        or not all(safe_scope_path(item) for item in value["scope_paths"])
        or not isinstance(value.get("required_gates"), list)
        or len(value["required_gates"]) != len(set(value["required_gates"]))
        or not all(
            isinstance(item, str) and item in _KNOWN_GATE_IDS
            for item in value["required_gates"]
        )
        or not _tier_review_gates_complete(
            value.get("tier"), value.get("required_gates")
        )
        or (
            outcome_valid
            and value.get("deferred_effects")
            != deferred_effects_for_outcome(requested_outcome)
        )
        or value.get("authorizes") is not False
        or value.get("max_executions") != MAX_EXECUTIONS
        or not _timestamp(value.get("prepared_at"))
    ):
        return [_issue("RUN_BINDING", "", "RunPlanV1 binding is invalid.")]
    return _digest_issue(value, "plan_digest", "RUN_DIGEST")


def _scope_paths_digest(paths: object) -> str:
    if not isinstance(paths, list) or not paths or not all(safe_scope_path(item) for item in paths):
        raise ValueError("E_OUTCOME_BINDING: scope paths are invalid")
    return contract_digest(list(paths))


def build_outcome_binding(
    *,
    run_plan: Mapping[str, Any],
    review_head: str,
    reviewed_tree_digest: str,
    reviewed_diff_digest: str,
) -> dict[str, Any]:
    """Create a durable, non-authorizing lineage record for host observations."""

    if (
        validate_run_plan(run_plan)
        or not isinstance(review_head, str)
        or _GIT_OBJECT_ID.fullmatch(review_head) is None
        or not _digest(reviewed_tree_digest)
        or not _digest(reviewed_diff_digest)
    ):
        raise ValueError("E_OUTCOME_BINDING: initial binding is invalid")
    core = {
        "schema_version": 1,
        "kind": "OutcomeBindingV1",
        "task_id": run_plan["task_id"],
        "run_plan_digest": run_plan["plan_digest"],
        "requested_outcome": run_plan["requested_outcome"],
        "repository": run_plan["repository"],
        "branch": run_plan["branch"],
        "scope_paths_digest": _scope_paths_digest(run_plan["scope_paths"]),
        "review_head": review_head,
        "reviewed_tree_digest": reviewed_tree_digest,
        "reviewed_diff_digest": reviewed_diff_digest,
        "committed_head": None,
        "pushed_head": None,
        "pull_request_digest": None,
        "checks_digest": None,
        "merge_sha": None,
        "consumed_effect_ids": [],
        "authorizes": False,
    }
    return {**core, "binding_digest": contract_digest(core)}


def validate_outcome_binding(value: Mapping[str, Any]) -> list[ContractIssue]:
    """Validate a closed lineage receipt without asserting host authority."""

    issues = _closed_schema(value, keys=_OUTCOME_BINDING_KEYS, kind="OutcomeBindingV1")
    if issues:
        return issues
    outcome = value.get("requested_outcome")
    expected = _OUTCOME_EFFECTS.get(outcome) if isinstance(outcome, str) else None
    consumed = value.get("consumed_effect_ids")
    if (
        not validate_task_id(value.get("task_id"))
        or not _digest(value.get("run_plan_digest"))
        or not isinstance(value.get("repository"), str)
        or not Path(value["repository"]).is_absolute()
        or not isinstance(value.get("branch"), str)
        or _BRANCH.fullmatch(value["branch"]) is None
        or not _digest(value.get("scope_paths_digest"))
        or not isinstance(value.get("review_head"), str)
        or _GIT_OBJECT_ID.fullmatch(value["review_head"]) is None
        or not _digest(value.get("reviewed_tree_digest"))
        or not _digest(value.get("reviewed_diff_digest"))
        or expected is None
        or not isinstance(consumed, list)
        or consumed != list(expected[:len(consumed)])
        or len(consumed) > len(expected)
        or value.get("authorizes") is not False
    ):
        return [_issue("OUTCOME_BINDING", "", "OutcomeBindingV1 binding is invalid.")]
    staged = "local_write" in consumed
    committed = "commit" in consumed
    pushed = "remote_write" in consumed
    drafted = "pull_request" in consumed
    merged = "integration" in consumed
    if (
        (committed != isinstance(value.get("committed_head"), str))
        or (pushed != isinstance(value.get("pushed_head"), str))
        or (drafted != (isinstance(value.get("pull_request_digest"), str) and isinstance(value.get("checks_digest"), str)))
        or (merged != isinstance(value.get("merge_sha"), str))
        or (not committed and value.get("committed_head") is not None)
        or (not pushed and value.get("pushed_head") is not None)
        or (not drafted and (value.get("pull_request_digest") is not None or value.get("checks_digest") is not None))
        or (not merged and value.get("merge_sha") is not None)
        or (staged is False and any(item is not None for item in (
            value.get("committed_head"), value.get("pushed_head"),
            value.get("pull_request_digest"), value.get("checks_digest"), value.get("merge_sha"),
        )))
        or (committed and _GIT_OBJECT_ID.fullmatch(str(value.get("committed_head"))) is None)
        or (pushed and value.get("pushed_head") != value.get("committed_head"))
        or (drafted and not all(_digest(value.get(key)) for key in ("pull_request_digest", "checks_digest")))
        or (merged and _GIT_OBJECT_ID.fullmatch(str(value.get("merge_sha"))) is None)
    ):
        return [_issue("OUTCOME_BINDING", "", "OutcomeBindingV1 lineage is invalid.")]
    return _digest_issue(value, "binding_digest", "OUTCOME_DIGEST")


def _outcome_observation(
    effect_id: str, observation: Mapping[str, Any]
) -> tuple[str, ...]:
    expected = {
        "local_write": ("head", "tree_digest", "diff_digest"),
        "commit": ("parent_head", "tree_digest", "committed_head"),
        "remote_write": ("pushed_head",),
        "pull_request": ("pull_request_digest", "checks_digest", "head"),
        "integration": ("merge_sha", "checks_digest"),
    }.get(effect_id)
    if expected is None:
        raise ValueError("E_OUTCOME_EFFECT: effect is not canonical")
    if set(observation) != set(expected):
        raise ValueError("E_OUTCOME_OBSERVATION: observation schema is invalid")
    return expected


def advance_outcome_binding(
    binding: Mapping[str, Any], *, effect_id: str, observation: Mapping[str, Any]
) -> dict[str, Any]:
    """Advance one exact canonical effect after an external host observation."""

    if validate_outcome_binding(binding):
        raise ValueError("E_OUTCOME_BINDING: binding is invalid")
    if not isinstance(observation, Mapping):
        raise ValueError("E_OUTCOME_OBSERVATION: observation is invalid")
    _outcome_observation(effect_id, observation)
    outcome = str(binding["requested_outcome"])
    expected_effects = _OUTCOME_EFFECTS[outcome]
    consumed = list(binding["consumed_effect_ids"])
    if effect_id not in expected_effects:
        raise ValueError("E_OUTCOME_EFFECT: effect is outside requested outcome")
    if effect_id in consumed:
        raise ValueError("E_OUTCOME_REPLAY: effect has already been consumed")
    if effect_id != expected_effects[len(consumed)]:
        raise ValueError("E_OUTCOME_ORDER: effect is not the expected successor")
    if effect_id == "local_write" and (
        observation["head"] != binding["review_head"]
        or observation["tree_digest"] != binding["reviewed_tree_digest"]
        or observation["diff_digest"] != binding["reviewed_diff_digest"]
    ):
        raise ValueError("E_OUTCOME_CAS: reviewed subject drifted before staging")
    if effect_id == "commit" and (
        observation["parent_head"] != binding["review_head"]
        or observation["tree_digest"] != binding["reviewed_tree_digest"]
        or not isinstance(observation["committed_head"], str)
        or _GIT_OBJECT_ID.fullmatch(observation["committed_head"]) is None
        or observation["committed_head"] == binding["review_head"]
    ):
        raise ValueError("E_OUTCOME_CAS: commit does not match reviewed state")
    if effect_id == "remote_write" and observation["pushed_head"] != binding["committed_head"]:
        raise ValueError("E_OUTCOME_CAS: pushed head does not match commit")
    if effect_id == "pull_request" and (
        observation["head"] != binding["committed_head"]
        or not _digest(observation["pull_request_digest"])
        or not _digest(observation["checks_digest"])
    ):
        raise ValueError("E_OUTCOME_CAS: pull request observation drifted")
    if effect_id == "integration" and (
        observation["checks_digest"] != binding["checks_digest"]
        or not isinstance(observation["merge_sha"], str)
        or _GIT_OBJECT_ID.fullmatch(observation["merge_sha"]) is None
    ):
        raise ValueError("E_OUTCOME_CAS: merge observation drifted")
    core = {key: value for key, value in binding.items() if key != "binding_digest"}
    core["consumed_effect_ids"] = [*consumed, effect_id]
    if effect_id == "commit":
        core["committed_head"] = observation["committed_head"]
    elif effect_id == "remote_write":
        core["pushed_head"] = observation["pushed_head"]
    elif effect_id == "pull_request":
        core["pull_request_digest"] = observation["pull_request_digest"]
        core["checks_digest"] = observation["checks_digest"]
    elif effect_id == "integration":
        core["merge_sha"] = observation["merge_sha"]
    result = {**core, "binding_digest": contract_digest(core)}
    if validate_outcome_binding(result):
        raise ValueError("E_OUTCOME_BINDING: successor binding is invalid")
    return result


def build_run_revision(
    *, run_plan: Mapping[str, Any], revision: int, first_attempt: int,
    head: str, reason: str, parent_revision_digest: str | None,
    source_attempt_digest: str | None, source_review_receipt_digest: str | None,
    source_diff_digest: str | None,
) -> dict[str, Any]:
    if validate_run_plan(run_plan) or not isinstance(revision, int) or isinstance(revision, bool) or revision < 0 or not 1 <= first_attempt <= MAX_EXECUTIONS or _GIT_OBJECT_ID.fullmatch(head) is None:
        raise ValueError("E_RUN_REVISION: revision binding is invalid")
    initial = revision == 0
    if (
        (initial and (reason != "initial" or any(item is not None for item in (parent_revision_digest, source_attempt_digest, source_review_receipt_digest, source_diff_digest))))
        or (not initial and (reason not in {"review_findings", "pull_request_feedback"} or not all(_digest(item) for item in (parent_revision_digest, source_attempt_digest, source_review_receipt_digest, source_diff_digest))))
    ):
        raise ValueError("E_RUN_REVISION: revision lineage is invalid")
    core = {"schema_version": 1, "kind": "RunRevisionV1", "task_id": run_plan["task_id"], "task_digest": run_plan["task_digest"], "run_plan_digest": run_plan["plan_digest"], "revision": revision, "parent_revision_digest": parent_revision_digest, "first_attempt": first_attempt, "repository": run_plan["repository"], "branch": run_plan["branch"], "head": head, "scope_paths": list(run_plan["scope_paths"]), "reason": reason, "source_attempt_digest": source_attempt_digest, "source_review_receipt_digest": source_review_receipt_digest, "source_diff_digest": source_diff_digest, "authorizes": False}
    return {**core, "revision_digest": contract_digest(core)}


def validate_run_revision(value: Mapping[str, Any]) -> list[ContractIssue]:
    issues = _closed_schema(value, keys=_RUN_REVISION_KEYS, kind="RunRevisionV1")
    if issues:
        return issues
    initial = value.get("revision") == 0
    sources = (
        value.get("parent_revision_digest"), value.get("source_attempt_digest"),
        value.get("source_review_receipt_digest"), value.get("source_diff_digest"),
    )
    if (
        not validate_task_id(value.get("task_id"))
        or not _digest(value.get("task_digest"))
        or not _digest(value.get("run_plan_digest"))
        or not isinstance(value.get("revision"), int)
        or isinstance(value.get("revision"), bool)
        or not 0 <= value["revision"] <= 2
        or not isinstance(value.get("first_attempt"), int)
        or not 1 <= value["first_attempt"] <= MAX_EXECUTIONS
        or not isinstance(value.get("repository"), str)
        or not Path(value["repository"]).is_absolute()
        or not isinstance(value.get("branch"), str)
        or _BRANCH.fullmatch(value["branch"]) is None
        or not isinstance(value.get("head"), str)
        or _GIT_OBJECT_ID.fullmatch(value["head"]) is None
        or not _review_scope_paths(value.get("scope_paths"))
        or value.get("authorizes") is not False
        or (
            initial
            and (
                value.get("reason") != "initial"
                or any(source is not None for source in sources)
            )
        )
        or (
            not initial
            and (
                value.get("reason") not in {"review_findings", "pull_request_feedback"}
                or not all(_digest(source) for source in sources)
            )
        )
    ):
        return [_issue("RUN_REVISION", "", "RunRevisionV1 binding is invalid.")]
    return _digest_issue(value, "revision_digest", "RUN_DIGEST")


def build_gate_receipt(
    *,
    run_plan: Mapping[str, Any],
    attempt: int,
    gate_id: str,
    status: str,
    command_digest: str,
    output_digest: str,
    before_snapshot_digest: str,
    after_snapshot_digest: str,
    error_code: str | None,
    observed_at: str,
) -> dict[str, Any]:
    if validate_run_plan(run_plan):
        raise ValueError("E_RUN_PLAN: valid RunPlanV1 required")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or not 1 <= attempt <= MAX_EXECUTIONS:
        raise ValueError("E_RUN_ATTEMPT: attempt must be from one to three")
    if status not in RUN_STATUSES:
        raise ValueError("E_RUN_STATUS: gate status is invalid")
    if (
        not isinstance(gate_id, str)
        or not gate_id
        or not all(
            _digest(item)
            for item in (
                command_digest,
                output_digest,
                before_snapshot_digest,
                after_snapshot_digest,
            )
        )
        or (error_code is not None and not validate_task_id(error_code))
        or not _timestamp(observed_at)
    ):
        raise ValueError("E_RUN_RECEIPT: gate receipt binding is invalid")
    core = {
        "schema_version": 1,
        "kind": "GateReceiptV1",
        "task_id": run_plan["task_id"],
        "task_digest": run_plan["task_digest"],
        "run_plan_digest": run_plan["plan_digest"],
        "attempt": attempt,
        "gate_id": gate_id,
        "status": status,
        "command_digest": command_digest,
        "output_digest": output_digest,
        "before_snapshot_digest": before_snapshot_digest,
        "after_snapshot_digest": after_snapshot_digest,
        "error_code": error_code,
        "observed_at": observed_at,
    }
    return {**core, "receipt_digest": contract_digest(core)}


def validate_gate_receipt(value: Mapping[str, Any]) -> list[ContractIssue]:
    issues = _closed_schema(value, keys=_GATE_RECEIPT_KEYS, kind="GateReceiptV1")
    if issues:
        return issues
    if (
        not validate_task_id(value.get("task_id"))
        or not all(
            _digest(value.get(key))
            for key in (
                "task_digest",
                "run_plan_digest",
                "command_digest",
                "output_digest",
                "before_snapshot_digest",
                "after_snapshot_digest",
            )
        )
        or not isinstance(value.get("attempt"), int)
        or isinstance(value.get("attempt"), bool)
        or not 1 <= int(value["attempt"]) <= MAX_EXECUTIONS
        or not isinstance(value.get("gate_id"), str)
        or not value.get("gate_id")
        or value.get("status") not in RUN_STATUSES
        or (
            value.get("error_code") is not None
            and not validate_task_id(value.get("error_code"))
        )
        or not _timestamp(value.get("observed_at"))
    ):
        return [_issue("RUN_RECEIPT", "", "GateReceiptV1 binding is invalid.")]
    return _digest_issue(value, "receipt_digest", "RUN_DIGEST")


def _rollback_plan_rows_are_valid(value: Mapping[str, Any]) -> bool:
    def bounded(item: object) -> bool:
        return bool(
            isinstance(item, str)
            and 1 <= len(item.encode("utf-8")) <= 512
            and "\x00" not in item
        )

    triggers = value.get("trigger_conditions")
    steps = value.get("rollback_steps")
    checks = value.get("post_rollback_checks")
    boundaries = value.get("irreversible_boundaries")
    if not all(isinstance(rows, list) for rows in (
        triggers, steps, checks, boundaries,
    )):
        return False
    if value.get("status") == "UNKNOWN":
        return not triggers and not steps and not checks and not boundaries
    return bool(
        value.get("status") == "PASS"
        and 1 <= len(triggers) <= 16
        and 1 <= len(steps) <= 32
        and 1 <= len(checks) <= 16
        and 1 <= len(boundaries) <= 16
        and all(
            isinstance(row, Mapping)
            and set(row) == {"condition", "signal"}
            and bounded(row.get("condition"))
            and bounded(row.get("signal"))
            for row in triggers
        )
        and all(
            isinstance(row, Mapping)
            and set(row) == {"order", "action", "target", "success_condition"}
            and isinstance(row.get("order"), int)
            and not isinstance(row.get("order"), bool)
            and bounded(row.get("action"))
            and bounded(row.get("target"))
            and bounded(row.get("success_condition"))
            for row in steps
        )
        and tuple(row["order"] for row in steps) == tuple(range(1, len(steps) + 1))
        and all(
            isinstance(row, Mapping)
            and set(row) == {"check_id", "expected"}
            and bounded(row.get("check_id"))
            and bounded(row.get("expected"))
            for row in checks
        )
        and len({row["check_id"] for row in checks}) == len(checks)
        and all(
            isinstance(row, Mapping)
            and set(row) == {"boundary", "mitigation"}
            and bounded(row.get("boundary"))
            and bounded(row.get("mitigation"))
            for row in boundaries
        )
    )


def validate_rollback_plan(value: Mapping[str, Any]) -> list[ContractIssue]:
    """Validate a closed durable plan; required_gates text is never evidence."""

    issues = _closed_schema(
        value, keys=_ROLLBACK_PLAN_KEYS, kind="RollbackPlanV1"
    )
    if issues:
        return issues
    if (
        not validate_task_id(value.get("task_id"))
        or not all(_digest(value.get(key)) for key in (
            "task_digest", "run_plan_digest", "run_revision_digest",
            "scope_paths_digest", "observation_digest",
        ))
        or not isinstance(value.get("attempt"), int)
        or isinstance(value.get("attempt"), bool)
        or not 1 <= int(value["attempt"]) <= MAX_EXECUTIONS
        or not isinstance(value.get("repository"), str)
        or not value.get("repository")
        or not isinstance(value.get("branch"), str)
        or _BRANCH.fullmatch(str(value.get("branch"))) is None
        or not isinstance(value.get("head"), str)
        or _GIT_OBJECT_ID.fullmatch(str(value.get("head"))) is None
        or value.get("authorizes") is not False
        or not _timestamp(value.get("observed_at"))
        or not _rollback_plan_rows_are_valid(value)
        or len(_canonical_json_bytes(value)) > MAX_ROLLBACK_PLAN_BYTES
    ):
        return [_issue("ROLLBACK_PLAN", "", "RollbackPlanV1 binding is invalid.")]
    return _digest_issue(value, "plan_digest", "RUN_DIGEST")


def build_rollback_plan(
    *,
    run_plan: Mapping[str, Any],
    run_revision: Mapping[str, Any],
    attempt: int,
    observation: object,
) -> dict[str, Any]:
    """Build a non-authorizing durable plan from one exact host observation."""

    from control_plane.host_bridge import inspect_rollback_plan_observation

    if (
        validate_run_plan(run_plan)
        or validate_run_revision(run_revision)
        or run_revision.get("run_plan_digest") != run_plan.get("plan_digest")
        or run_plan.get("tier") != "T3"
        or "gate.rollback-plan" not in run_plan.get("required_gates", ())
    ):
        raise ValueError("E_ROLLBACK_PLAN: exact T3 plan binding is required")
    inspected = inspect_rollback_plan_observation(
        observation,
        run_plan=run_plan,
        run_revision=run_revision,
        attempt=attempt,
    )
    core = {
        "schema_version": 1,
        "kind": "RollbackPlanV1",
        "task_id": run_plan["task_id"],
        "task_digest": run_plan["task_digest"],
        "run_plan_digest": run_plan["plan_digest"],
        "run_revision_digest": run_revision["revision_digest"],
        "attempt": attempt,
        "repository": run_plan["repository"],
        "branch": run_plan["branch"],
        "head": run_revision["head"],
        "scope_paths_digest": contract_digest({
            "scope_paths": run_plan["scope_paths"]
        }),
        "trigger_conditions": [
            {"condition": condition, "signal": signal}
            for condition, signal in inspected["trigger_conditions"]
        ],
        "rollback_steps": [
            {
                "order": order, "action": action, "target": target,
                "success_condition": success_condition,
            }
            for order, action, target, success_condition
            in inspected["rollback_steps"]
        ],
        "post_rollback_checks": [
            {"check_id": check_id, "expected": expected}
            for check_id, expected in inspected["post_rollback_checks"]
        ],
        "irreversible_boundaries": [
            {"boundary": boundary, "mitigation": mitigation}
            for boundary, mitigation in inspected["irreversible_boundaries"]
        ],
        "status": inspected["status"],
        "observation_digest": inspected["observation_digest"],
        "observed_at": inspected["observed_at"],
        "authorizes": False,
    }
    result = {**core, "plan_digest": contract_digest(core)}
    if validate_rollback_plan(result):
        raise ValueError("E_ROLLBACK_PLAN: structured plan is invalid")
    return result


def build_review_result(
    *,
    run_plan: Mapping[str, Any],
    reviewed_head: str,
    reviewer_kind: str,
    reviewer_context_digest: str,
    critical: int,
    important: int,
    minor: int,
    observed_at: str,
) -> dict[str, Any]:
    if validate_run_plan(run_plan):
        raise ValueError("E_RUN_PLAN: valid RunPlanV1 required")
    counts = (critical, important, minor)
    if (
        not isinstance(reviewed_head, str)
        or _GIT_OBJECT_ID.fullmatch(reviewed_head) is None
        or reviewer_kind not in {"independent", "security"}
        or not _digest(reviewer_context_digest)
        or not all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in counts)
        or not _timestamp(observed_at)
    ):
        raise ValueError("E_RUN_REVIEW: review result binding is invalid")
    status = "FAIL" if critical or important else "PASS"
    core = {
        "schema_version": 1,
        "kind": "ReviewResultV1",
        "task_id": run_plan["task_id"],
        "task_digest": run_plan["task_digest"],
        "run_plan_digest": run_plan["plan_digest"],
        "reviewed_head": reviewed_head,
        "reviewer_kind": reviewer_kind,
        "reviewer_context_digest": reviewer_context_digest,
        "critical": critical,
        "important": important,
        "minor": minor,
        "status": status,
        "authorizes": False,
        "observed_at": observed_at,
    }
    return {**core, "result_digest": contract_digest(core)}


def validate_review_result(value: Mapping[str, Any]) -> list[ContractIssue]:
    issues = _closed_schema(value, keys=_REVIEW_RESULT_KEYS, kind="ReviewResultV1")
    if issues:
        return issues
    counts = tuple(value.get(key) for key in ("critical", "important", "minor"))
    expected_status = "FAIL" if any(int(item or 0) for item in counts[:2]) else "PASS"
    if (
        not validate_task_id(value.get("task_id"))
        or not all(
            _digest(value.get(key))
            for key in ("task_digest", "run_plan_digest", "reviewer_context_digest")
        )
        or not isinstance(value.get("reviewed_head"), str)
        or _GIT_OBJECT_ID.fullmatch(str(value["reviewed_head"])) is None
        or value.get("reviewer_kind") not in {"independent", "security"}
        or not all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in counts)
        or value.get("status") != expected_status
        or value.get("authorizes") is not False
        or not _timestamp(value.get("observed_at"))
    ):
        return [_issue("RUN_REVIEW", "", "ReviewResultV1 binding is invalid.")]
    return _digest_issue(value, "result_digest", "RUN_DIGEST")


def _review_scope_paths(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and bool(value)
        and len(value) == len(set(value))
        and tuple(sorted(value)) == tuple(value)
        and all(
            isinstance(item, str)
            and len(item) <= MAX_REVIEW_SCOPE_PATH_LENGTH
            and safe_scope_path(item)
            for item in value
        )
    )


def _review_packet_size(value: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    )


_REVIEW_KINDS = frozenset({"independent", "security"})


def _review_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Project one persisted PASS gate receipt into bounded review evidence."""

    gate_id = receipt.get("gate_id")
    if (
        validate_gate_receipt(receipt)
        or receipt.get("status") != "PASS"
        or gate_id not in frozenset(_LOCAL_GATE_IDS).union(_PLAN_BOUND_GATE_IDS)
    ):
        raise ValueError("E_REVIEW_PACKET: local gate receipt is invalid")
    return {
        "kind": "ReviewCheckSummaryV1",
        "check_kind": "test" if gate_id == "gate.relevant-tests" else "gate",
        "check_id": gate_id,
        "status": "PASS",
        "argv_digest": receipt["command_digest"],
        "output_digest": receipt["output_digest"],
        "receipt_digest": receipt["receipt_digest"],
    }


def _valid_review_summaries(value: object) -> bool:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        return False
    expected = {
        "kind", "check_kind", "check_id", "status", "argv_digest",
        "output_digest", "receipt_digest",
    }
    identifiers: list[tuple[str, str]] = []
    digests: set[str] = set()
    for item in value:
        if (
            not isinstance(item, Mapping)
            or set(item) != expected
            or item.get("kind") != "ReviewCheckSummaryV1"
            or item.get("check_kind") not in {"test", "gate"}
            or not isinstance(item.get("check_id"), str)
            or not item["check_id"]
            or item.get("status") != "PASS"
            or not all(_digest(item.get(key)) for key in (
                "argv_digest", "output_digest", "receipt_digest"
            ))
        ):
            return False
        identifiers.append((str(item["check_kind"]), str(item["check_id"])))
        digest = str(item["receipt_digest"])
        if digest in digests:
            return False
        digests.add(digest)
    return identifiers == sorted(identifiers) and len(set(identifiers)) == len(identifiers)


def _build_review_packet(
    *,
    run_plan: Mapping[str, Any],
    run_revision: Mapping[str, Any],
    attempt_record: Mapping[str, Any],
    artifact: Mapping[str, Any],
    review_kind: str,
    criteria_digest: str,
    evidence_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Internal-only construction from records that have already been verified."""

    if (
        validate_run_plan(run_plan)
        or validate_run_revision(run_revision)
        or validate_stable_review_diff_artifact(artifact)
        or review_kind not in _REVIEW_KINDS
        or not _digest(criteria_digest)
        or not _valid_review_summaries(evidence_summaries)
    ):
        raise ValueError("E_REVIEW_PACKET: packet inputs are invalid")
    core = {
        "schema_version": 1,
        "kind": "ReviewPacketV1",
        "task_id": run_plan["task_id"],
        "task_digest": run_plan["task_digest"],
        "run_plan_digest": run_plan["plan_digest"],
        "run_revision_digest": run_revision["revision_digest"],
        "attempt": attempt_record["attempt"],
        "attempt_digest": attempt_record["attempt_digest"],
        "repository": run_plan["repository"],
        "base_head": artifact["base_head"],
        "branch": run_plan["branch"],
        "reviewed_head": run_revision["head"],
        "review_kind": review_kind,
        "criteria_digest": criteria_digest,
        "scope_paths": list(artifact["scope_paths"]),
        "artifact_digest": artifact["artifact_digest"],
        "diff_digest": artifact["diff_digest"],
        "diff_size": artifact["diff_size"],
        "evidence_summaries": evidence_summaries,
        "authorizes": False,
    }
    packet = {**core, "packet_digest": contract_digest(core)}
    if _review_packet_size(packet) > MAX_REVIEW_PACKET_BYTES:
        raise ValueError("E_REVIEW_PACKET: review packet exceeds byte cap")
    return packet


def validate_review_packet(value: Mapping[str, Any]) -> list[ContractIssue]:
    issues = _closed_schema(value, keys=_REVIEW_PACKET_KEYS, kind="ReviewPacketV1")
    if issues:
        return issues
    if (
        _review_packet_size(value) > MAX_REVIEW_PACKET_BYTES
        or not validate_task_id(value.get("task_id"))
        or not all(_digest(value.get(key)) for key in (
            "task_digest", "run_plan_digest", "run_revision_digest", "attempt_digest",
            "criteria_digest", "artifact_digest", "diff_digest",
        ))
        or not isinstance(value.get("attempt"), int)
        or isinstance(value.get("attempt"), bool)
        or not 1 <= int(value["attempt"]) <= MAX_EXECUTIONS
        or not isinstance(value.get("repository"), str)
        or not Path(str(value["repository"])).is_absolute()
        or not isinstance(value.get("branch"), str)
        or _BRANCH.fullmatch(str(value["branch"])) is None
        or not all(isinstance(value.get(key), str) and _GIT_OBJECT_ID.fullmatch(str(value[key])) is not None for key in ("base_head", "reviewed_head"))
        or value.get("review_kind") not in _REVIEW_KINDS
        or not _review_scope_paths(value.get("scope_paths"))
        or not _valid_review_summaries(value.get("evidence_summaries"))
        or not isinstance(value.get("diff_size"), int)
        or isinstance(value.get("diff_size"), bool)
        or not 0 <= int(value["diff_size"]) <= MAX_REVIEW_DIFF_BYTES
        or value.get("authorizes") is not False
    ):
        return [_issue("RUN_PACKET", "", "ReviewPacketV1 binding is invalid.")]
    return _digest_issue(value, "packet_digest", "RUN_DIGEST")


def build_independent_review_receipt(
    *,
    review_packet: Mapping[str, Any],
    findings_digest: str,
    critical: int,
    important: int,
    status: str,
    observed_at: str,
    review_kind: str | None = None,
    criteria_digest: str | None = None,
    observation: object | None = None,
) -> dict[str, Any]:
    """Close a host-observed reviewer conclusion without serializing authority."""

    if validate_review_packet(review_packet):
        raise ValueError("E_REVIEW_PACKET: valid ReviewPacketV1 required")
    if observation is None:
        reviewer_identity_digest = contract_digest(
            {"reviewer_identity": None}
        )
        observation_digest = contract_digest(
            {
                "unobserved_review_packet_digest": review_packet[
                    "packet_digest"
                ],
                "findings_digest": findings_digest,
                "critical": critical,
                "important": important,
                "status": status,
                "observed_at": observed_at,
            }
        )
    else:
        from control_plane.host_bridge import (
            inspect_independent_review_observation,
        )

        reviewer_identity_digest, observation_digest = (
            inspect_independent_review_observation(
                observation, review_packet=review_packet
            )
        )
    if (
        (review_kind is not None and review_kind != review_packet.get("review_kind"))
        or (criteria_digest is not None and criteria_digest != review_packet.get("criteria_digest"))
        or not _digest(findings_digest)
        or status not in RUN_STATUSES
        or not all(isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in (critical, important))
        or (status == "PASS" and (critical != 0 or important != 0))
        or (status == "FAIL" and critical + important == 0)
        or (status == "UNKNOWN" and (critical != 0 or important != 0))
        or not _timestamp(observed_at)
    ):
        raise ValueError("E_INDEPENDENT_REVIEW: receipt binding is invalid")
    core = {
        "schema_version": 1,
        "kind": "IndependentReviewReceiptV1",
        "task_id": review_packet["task_id"],
        "task_digest": review_packet["task_digest"],
        "run_plan_digest": review_packet["run_plan_digest"],
        "run_revision_digest": review_packet["run_revision_digest"],
        "attempt": review_packet["attempt"],
        "attempt_digest": review_packet["attempt_digest"],
        "repository": review_packet["repository"],
        "base_head": review_packet["base_head"],
        "branch": review_packet["branch"],
        "reviewed_head": review_packet["reviewed_head"],
        "review_packet_digest": review_packet["packet_digest"],
        "artifact_digest": review_packet["artifact_digest"],
        "diff_digest": review_packet["diff_digest"],
        "scope_paths_digest": contract_digest({"scope_paths": review_packet["scope_paths"]}),
        "review_kind": review_packet["review_kind"],
        "criteria_digest": review_packet["criteria_digest"],
        "findings_digest": findings_digest,
        "reviewer_identity_digest": reviewer_identity_digest,
        "observation_digest": observation_digest,
        "critical": critical,
        "important": important,
        "status": status,
        "authorizes": False,
        "observed_at": observed_at,
    }
    return {**core, "receipt_digest": contract_digest(core)}


def validate_independent_review_receipt(
    value: Mapping[str, Any],
) -> list[ContractIssue]:
    issues = _closed_schema(
        value, keys=_INDEPENDENT_REVIEW_RECEIPT_KEYS, kind="IndependentReviewReceiptV1"
    )
    if issues:
        return issues
    if (
        not validate_task_id(value.get("task_id"))
        or not all(_digest(value.get(key)) for key in (
            "task_digest", "run_plan_digest", "run_revision_digest", "attempt_digest",
            "review_packet_digest", "artifact_digest", "diff_digest",
            "scope_paths_digest", "criteria_digest", "findings_digest",
            "reviewer_identity_digest", "observation_digest",
        ))
        or not isinstance(value.get("attempt"), int)
        or isinstance(value.get("attempt"), bool)
        or not 1 <= int(value["attempt"]) <= MAX_EXECUTIONS
        or not isinstance(value.get("repository"), str)
        or not Path(str(value["repository"])).is_absolute()
        or not isinstance(value.get("branch"), str)
        or _BRANCH.fullmatch(str(value["branch"])) is None
        or not all(isinstance(value.get(key), str) and _GIT_OBJECT_ID.fullmatch(str(value[key])) is not None for key in ("base_head", "reviewed_head"))
        or value.get("review_kind") not in _REVIEW_KINDS
        or value.get("status") not in RUN_STATUSES
        or not all(isinstance(value.get(key), int) and not isinstance(value.get(key), bool) and value[key] >= 0 for key in ("critical", "important"))
        or (value.get("status") == "PASS" and (value["critical"] != 0 or value["important"] != 0))
        or (value.get("status") == "FAIL" and value["critical"] + value["important"] == 0)
        or (value.get("status") == "UNKNOWN" and (value["critical"] != 0 or value["important"] != 0))
        or value.get("authorizes") is not False
        or not _timestamp(value.get("observed_at"))
    ):
        return [_issue("RUN_INDEPENDENT_REVIEW", "", "Independent review receipt binding is invalid.")]
    return _digest_issue(value, "receipt_digest", "RUN_DIGEST")


def _aggregate_status(statuses: tuple[str, ...]) -> str:
    if not statuses or "UNKNOWN" in statuses:
        result = "UNKNOWN"
    else:
        result = "PASS"
    if "FAIL" in statuses:
        result = "FAIL"
    return result


def _visible_status(lifecycle_state: str) -> str:
    if lifecycle_state == "blocked":
        return "BLOCKED"
    if lifecycle_state in {"framed", "planned"}:
        return "PLANIFICANDO"
    if lifecycle_state in {"ready", "implementing"}:
        return "TRABAJANDO"
    if lifecycle_state in {"pushed", "pr_draft", "pr_ready"}:
        return "PR LISTA" if lifecycle_state == "pr_ready" else "TRABAJANDO"
    return "VERIFICANDO"


def build_run_summary(
    *,
    run_plan: Mapping[str, Any],
    head: str,
    lifecycle_state: str,
    attempt_count: int,
    gate_statuses: tuple[str, ...],
    gate_receipt_digests: tuple[str, ...],
    review_result_digest: str | None,
    blocked_reason_code: str | None,
    observed_at: str,
) -> dict[str, Any]:
    if validate_run_plan(run_plan):
        raise ValueError("E_RUN_PLAN: valid RunPlanV1 required")
    gate_status = _aggregate_status(gate_statuses)
    if (
        not isinstance(head, str)
        or _GIT_OBJECT_ID.fullmatch(head) is None
        or not isinstance(lifecycle_state, str)
        or not lifecycle_state
        or not isinstance(attempt_count, int)
        or isinstance(attempt_count, bool)
        or not 0 <= attempt_count <= MAX_EXECUTIONS
        or any(item not in RUN_STATUSES for item in gate_statuses)
        or not all(_digest(item) for item in gate_receipt_digests)
        or (review_result_digest is not None and not _digest(review_result_digest))
        or (blocked_reason_code is not None and not validate_task_id(blocked_reason_code))
        or not _timestamp(observed_at)
    ):
        raise ValueError("E_RUN_SUMMARY: run summary binding is invalid")
    core = {
        "schema_version": 1,
        "kind": "RunSummaryV1",
        "task_id": run_plan["task_id"],
        "task_digest": run_plan["task_digest"],
        "run_plan_digest": run_plan["plan_digest"],
        "head": head,
        "lifecycle_state": lifecycle_state,
        "visible_status": _visible_status(lifecycle_state),
        "attempt_count": attempt_count,
        "gate_status": gate_status,
        "gate_receipt_digests": list(gate_receipt_digests),
        "review_result_digest": review_result_digest,
        "blocked_reason_code": blocked_reason_code,
        "observed_at": observed_at,
    }
    return {**core, "summary_digest": contract_digest(core)}


def validate_run_summary(value: Mapping[str, Any]) -> list[ContractIssue]:
    issues = _closed_schema(value, keys=_RUN_SUMMARY_KEYS, kind="RunSummaryV1")
    if issues:
        return issues
    if (
        not validate_task_id(value.get("task_id"))
        or not all(
            _digest(value.get(key))
            for key in ("task_digest", "run_plan_digest")
        )
        or not isinstance(value.get("head"), str)
        or _GIT_OBJECT_ID.fullmatch(str(value["head"])) is None
        or not isinstance(value.get("lifecycle_state"), str)
        or not value.get("lifecycle_state")
        or value.get("visible_status") not in VISIBLE_STATUSES
        or not isinstance(value.get("attempt_count"), int)
        or isinstance(value.get("attempt_count"), bool)
        or not 0 <= int(value["attempt_count"]) <= MAX_EXECUTIONS
        or value.get("gate_status") not in RUN_STATUSES
        or not isinstance(value.get("gate_receipt_digests"), list)
        or not all(_digest(item) for item in value["gate_receipt_digests"])
        or (
            value.get("review_result_digest") is not None
            and not _digest(value.get("review_result_digest"))
        )
        or (
            value.get("blocked_reason_code") is not None
            and not validate_task_id(value.get("blocked_reason_code"))
        )
        or not _timestamp(value.get("observed_at"))
    ):
        return [_issue("RUN_SUMMARY", "", "RunSummaryV1 binding is invalid.")]
    return _digest_issue(value, "summary_digest", "RUN_DIGEST")


def _delivery_audit_state_dir(repository: Path) -> Path:
    """Resolve only the worktree's already-present Git dir without Git."""

    root = Path(repository)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("E_DELIVERY_AUDIT_REPOSITORY: repository is invalid")
    entry = root / ".git"
    if entry.is_dir() and not entry.is_symlink():
        return entry.resolve()
    if entry.is_symlink() or not entry.is_file() or entry.stat().st_size > 4096:
        raise ValueError("E_DELIVERY_AUDIT_REPOSITORY: Git dir is unavailable")
    try:
        pointer = entry.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError("E_DELIVERY_AUDIT_REPOSITORY: Git dir is unreadable") from error
    prefix = "gitdir: "
    if not pointer.startswith(prefix) or pointer.count("\n") > 1:
        raise ValueError("E_DELIVERY_AUDIT_REPOSITORY: Git dir is invalid")
    target = pointer[len(prefix):].strip()
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("E_DELIVERY_AUDIT_REPOSITORY: Git dir is invalid")
    return candidate


def _delivery_audit_digest_list(value: object, *, code: str) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or not all(_digest(item) for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"E_DELIVERY_AUDIT_{code}: receipt digests are invalid")
    return sorted(str(item) for item in value)


def _delivery_audit_review_receipts(
    store: "RunStore", *, task_id: str, run_plan: Mapping[str, Any],
    attempts: list[Mapping[str, Any]],
) -> list[str]:
    """Read every fixed review-receipt path and rebind it to its attempt."""

    attempt_by_number = {int(item["attempt"]): item for item in attempts}
    digests: list[str] = []
    for attempt in range(1, MAX_EXECUTIONS + 1):
        for review_kind in sorted(_REVIEW_KINDS):
            path = store._review_receipt_path(task_id, attempt, review_kind)
            if not path.exists():
                continue
            receipt = store._load_closed_json(
                path, maximum=MAX_REVIEW_PACKET_BYTES,
                code="E_DELIVERY_AUDIT_REVIEW",
            )
            record = attempt_by_number.get(attempt)
            if (
                record is None
                or validate_independent_review_receipt(receipt)
                or receipt.get("task_id") != task_id
                or receipt.get("task_digest") != run_plan.get("task_digest")
                or receipt.get("run_plan_digest") != run_plan.get("plan_digest")
                or receipt.get("run_revision_digest") != record.get("run_revision_digest")
                or receipt.get("attempt") != attempt
                or receipt.get("attempt_digest") != record.get("attempt_digest")
                or receipt.get("reviewed_head") != record.get("head")
                or receipt.get("review_kind") != review_kind
            ):
                raise ValueError("E_DELIVERY_AUDIT_REVIEW: receipt binding drifted")
            digests.append(str(receipt["receipt_digest"]))
    if len(digests) != len(set(digests)):
        raise ValueError("E_DELIVERY_AUDIT_REVIEW: receipt digest was reused")
    return sorted(digests)


def _delivery_audit_action(
    lifecycle_state: str, *, requested_outcome: str,
    latest_attempt: Mapping[str, Any], pending_repair: bool,
) -> str:
    if lifecycle_state in {"framed", "planned"}:
        return "CONTINUE_PLANNING"
    if lifecycle_state in {"ready", "implementing"}:
        return "CONTINUE_IMPLEMENTATION"
    if lifecycle_state == "verifying":
        if latest_attempt is not None and latest_attempt.get("retry_allowed") is True:
            return "REPAIR_IMPLEMENTATION"
        return "COMPLETE_VERIFICATION"
    if lifecycle_state == "review_ready":
        return "NO_ACTION" if requested_outcome == "local_change" else "PREPARE_COMMIT"
    if lifecycle_state == "committed":
        return "NO_ACTION" if requested_outcome == "commit" else "PREPARE_PUSH"
    if lifecycle_state == "pushed":
        return "PREPARE_PULL_REQUEST"
    if lifecycle_state == "pr_draft":
        return "REPAIR_IMPLEMENTATION" if pending_repair else "OBSERVE_PR_READINESS"
    if lifecycle_state == "pr_ready":
        return "NO_ACTION" if requested_outcome == "pull_request" else "PREPARE_INTEGRATION"
    if lifecycle_state in {"merged", "base_verified", "release_pending", "released", "observed", "closed"}:
        return "NO_ACTION"
    return "REQUEST_HUMAN_INTERVENTION"


def _delivery_audit_attempt_is_coherent(
    value: Mapping[str, Any], *, total: int,
) -> bool:
    """Check the closed outcome tuple stored for one executed attempt."""

    status = value.get("status")
    retry, blocked = value.get("retry_allowed"), value.get("blocked")
    failure, stop = value.get("failure_reason_code"), value.get("stop_reason_code")
    if status not in RUN_STATUSES or not isinstance(retry, bool) or not isinstance(blocked, bool):
        return False
    if failure is not None and not validate_task_id(failure):
        return False
    if stop is not None and not validate_task_id(stop):
        return False
    if status == "PASS":
        return failure is None and stop is None and retry is False and blocked is False
    if not validate_task_id(failure):
        return False
    if status == "UNKNOWN":
        return stop == "E_RUN_UNKNOWN" and retry is False and blocked is True
    if total == MAX_EXECUTIONS:
        return (
            stop == "E_RUN_EXHAUSTED"
            and blocked is True
            and retry is False
        )
    if stop is not None and stop not in {
        "E_RUN_NO_CHANGE", "E_RUN_SCOPE_GROWTH", "E_RUN_REPEATED_FAILURE",
        "E_RUN_EXHAUSTED",
    }:
        return False
    if stop == "E_RUN_EXHAUSTED" and total != MAX_EXECUTIONS:
        return False
    return blocked == (stop is not None) and retry == (stop is None)


def _delivery_audit_expected_observed(
    lifecycle_state: str, observed: Mapping[str, Any], *, requested_outcome: str,
) -> bool:
    """Require the observation shape implied by the durable lifecycle phase."""

    if set(observed) != {"head", "committed_head", "pushed_head", "pull_request_number"}:
        return False
    head = observed.get("head")
    committed, pushed, number = (
        observed.get("committed_head"), observed.get("pushed_head"),
        observed.get("pull_request_number"),
    )
    if not isinstance(head, str) or _GIT_OBJECT_ID.fullmatch(head) is None:
        return False
    if any(item is not None and (not isinstance(item, str) or _GIT_OBJECT_ID.fullmatch(item) is None) for item in (committed, pushed)):
        return False
    if number is not None and (not isinstance(number, int) or isinstance(number, bool) or number <= 0):
        return False
    committed_states = {"committed", "pushed", "pr_draft", "pr_ready", "merged", "base_verified"}
    pushed_states = {"pushed", "pr_draft", "pr_ready", "merged", "base_verified"}
    pr_states = {"pr_draft", "pr_ready", "merged", "base_verified"}
    if lifecycle_state in committed_states and committed is None:
        return False
    if lifecycle_state not in committed_states and committed is not None:
        return False
    if lifecycle_state in pushed_states and (pushed is None or pushed != committed):
        return False
    if lifecycle_state not in pushed_states and pushed is not None:
        return False
    if lifecycle_state in pr_states and number is None:
        return False
    if lifecycle_state not in pr_states and number is not None:
        return False
    return not (
        lifecycle_state in {"pr_draft", "pr_ready"}
        and requested_outcome not in {"pull_request", "integration"}
    )


def _delivery_audit_bytes(value: Mapping[str, Any]) -> int:
    return len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def validate_delivery_audit(value: Mapping[str, Any]) -> list[ContractIssue]:
    """Validate the compact, non-authorizing DeliveryAuditV1 boundary."""

    issues = _closed_schema(value, keys=_DELIVERY_AUDIT_KEYS, kind="DeliveryAuditV1")
    if issues:
        return issues
    observed = value.get("observed")
    receipt_digests = value.get("receipt_digests")
    attempts = value.get("attempts")
    outcome = value.get("requested_outcome")
    if (
        not validate_task_id(value.get("task_id"))
        or not all(_digest(value.get(key)) for key in ("task_digest", "run_plan_digest"))
        or outcome not in _OUTCOME_EFFECTS
        or value.get("visible_status") not in VISIBLE_STATUSES
        or value.get("lifecycle_state") not in set(ORDERED_STATES).union({"blocked"})
        or not isinstance(observed, Mapping)
        or not _delivery_audit_expected_observed(
            str(value.get("lifecycle_state")), observed,
            requested_outcome=str(outcome),
        )
        or not isinstance(receipt_digests, Mapping)
        or set(receipt_digests) != {"gate", "review", "remote_outcome", "pr_readiness"}
        or any(
            not isinstance(receipt_digests.get(key), list)
            or receipt_digests.get(key) != sorted(set(receipt_digests.get(key, [])))
            or not all(_digest(item) for item in receipt_digests.get(key, []))
            for key in ("gate", "review", "remote_outcome", "pr_readiness")
        )
        or not isinstance(attempts, Mapping)
        or set(attempts) != {"total", "maximum", "repairs_used"}
        or not all(
            isinstance(attempts.get(key), int) and not isinstance(attempts.get(key), bool)
            for key in ("total", "maximum", "repairs_used")
        )
        or attempts.get("maximum") != MAX_EXECUTIONS
        or not 0 <= attempts.get("total", -1) <= MAX_EXECUTIONS
        or attempts.get("repairs_used") != max(0, attempts.get("total", 0) - 1)
        or not isinstance(value.get("latest_attempt"), Mapping)
        or set(value.get("latest_attempt", {})) != {
            "status", "retry_allowed", "blocked", "stop_reason_code", "failure_reason_code",
        }
        or not isinstance(value.get("pending_repair"), bool)
        or (
            value.get("block_reason_code") is not None
            and not validate_task_id(value.get("block_reason_code"))
        )
        or not isinstance(value.get("missing_evidence"), list)
        or value.get("missing_evidence") != sorted(set(value.get("missing_evidence", [])))
        or not all(isinstance(item, str) and item in {"remote_observation", "durable_receipt"} for item in value.get("missing_evidence", []))
        or value.get("next_safe_action") not in _DELIVERY_AUDIT_NEXT_ACTIONS
        or value.get("authorizes") is not False
        or _delivery_audit_bytes(value) > _DELIVERY_AUDIT_MAX_BYTES
    ):
        return [_issue("DELIVERY_AUDIT", "", "DeliveryAuditV1 binding is invalid.")]
    if value.get("visible_status") != _visible_status(str(value["lifecycle_state"])):
        return [_issue("DELIVERY_AUDIT", "visible_status", "Visible status drifted.")]
    lifecycle_state = str(value["lifecycle_state"])
    latest_attempt = value["latest_attempt"]
    if attempts["total"] == 0 and latest_attempt != {
        "status": None, "retry_allowed": False, "blocked": False,
        "stop_reason_code": None, "failure_reason_code": None,
    }:
        return [_issue("DELIVERY_AUDIT", "latest_attempt", "No attempt may have a result.")]
    if attempts["total"] > 0 and not _delivery_audit_attempt_is_coherent(
        latest_attempt, total=int(attempts["total"]),
    ):
        return [_issue("DELIVERY_AUDIT", "latest_attempt", "Latest attempt is incoherent.")]
    if attempts["total"] > 0 and latest_attempt["blocked"] is True and lifecycle_state != "blocked":
        return [_issue("DELIVERY_AUDIT", "lifecycle_state", "Blocked attempt requires blocked lifecycle.")]
    if (
        lifecycle_state == "blocked"
        and attempts["total"] > 0
        and latest_attempt["blocked"] is True
        and value.get("block_reason_code") != latest_attempt["stop_reason_code"]
    ):
        return [_issue("DELIVERY_AUDIT", "block_reason_code", "Run block reason drifted.")]
    pending_repair = bool(value["pending_repair"])
    expected_action = _delivery_audit_action(
        lifecycle_state, requested_outcome=str(outcome),
        latest_attempt=latest_attempt, pending_repair=pending_repair,
    )
    if value.get("next_safe_action") != expected_action:
        return [_issue("DELIVERY_AUDIT", "next_safe_action", "Safe action drifted.")]
    if lifecycle_state == "blocked" and (
        value.get("block_reason_code") is None
        or value.get("next_safe_action") != "REQUEST_HUMAN_INTERVENTION"
    ):
        return [_issue("DELIVERY_AUDIT", "block_reason_code", "Blocked audit is incomplete.")]
    if lifecycle_state != "blocked" and not pending_repair and value.get("block_reason_code") is not None:
        return [_issue("DELIVERY_AUDIT", "block_reason_code", "Unexpected block reason.")]
    if pending_repair != (lifecycle_state == "pr_draft" and value.get("block_reason_code") == "E_PR_READINESS_REVISION_REQUIRED"):
        return [_issue("DELIVERY_AUDIT", "pending_repair", "Repair marker drifted.")]
    expected_missing = ["remote_observation"] if (
        lifecycle_state == "blocked"
        and str(value.get("block_reason_code")).endswith("_UNKNOWN")
    ) else []
    if value.get("missing_evidence") != expected_missing:
        return [_issue("DELIVERY_AUDIT", "missing_evidence", "Missing evidence drifted.")]
    if lifecycle_state == "pr_ready" and (
        not value["receipt_digests"]["pr_readiness"]
        or value["observed"]["pull_request_number"] is None
    ):
        return [_issue("DELIVERY_AUDIT", "receipt_digests", "PR ready proof is incomplete.")]
    return _digest_issue(value, "audit_digest", "DELIVERY_AUDIT_DIGEST")


def build_delivery_audit(repository: Path, task_id: str) -> dict[str, Any]:
    """Build a deterministic user view from durable local state without Git."""

    if not validate_task_id(task_id):
        raise ValueError("E_DELIVERY_AUDIT_TASK: task ID is invalid")
    state_dir = _delivery_audit_state_dir(repository)
    store = RunStore(state_dir)
    plan = store.load_plan(task_id)
    revision = store.load_active(task_id)
    state = TaskStore(state_dir).status(task_id)
    attempts = store.attempts(task_id)
    root = Path(repository).resolve()
    if (
        str(root) != plan.get("repository")
        or revision.get("repository") != plan.get("repository")
        or revision.get("repository") != str(root)
    ):
        raise ValueError("E_DELIVERY_AUDIT_REPOSITORY: repository binding drifted")
    if (
        state.get("task_digest") != plan.get("task_digest")
        or state.get("run_plan_digest") != plan.get("plan_digest")
        or state.get("active_run_revision_digest") != revision.get("revision_digest")
        or revision.get("task_digest") != plan.get("task_digest")
        or revision.get("run_plan_digest") != plan.get("plan_digest")
        or state.get("state") not in set(ORDERED_STATES).union({"blocked"})
    ):
        raise ValueError("E_DELIVERY_AUDIT_STATE: durable state binding drifted")
    revisions: list[dict[str, Any]] = []
    for number in range(MAX_EXECUTIONS):
        path = store._revision_path(task_id, number)
        if not path.exists():
            continue
        if number != len(revisions):
            raise ValueError("E_DELIVERY_AUDIT_ATTEMPT: run revision gap")
        revisions.append(store._read_revision(task_id, number))
    gate_digests: list[str] = []
    for attempt_number, record in enumerate(attempts, start=1):
        expected_revision = max(
            (item for item in revisions if int(item["first_attempt"]) <= attempt_number),
            key=lambda item: int(item["revision"]), default=None,
        )
        if (
            record.get("task_id") != task_id
            or record.get("task_digest") != plan.get("task_digest")
            or record.get("run_plan_digest") != plan.get("plan_digest")
            or record.get("attempt") != attempt_number
            or expected_revision is None
            or record.get("run_revision_digest") != expected_revision.get("revision_digest")
            or record.get("head") != expected_revision.get("head")
            or attempt_number < expected_revision.get("first_attempt", MAX_EXECUTIONS + 1)
            or not _delivery_audit_attempt_is_coherent(
                record, total=attempt_number,
            )
            or not isinstance(record.get("gate_receipt_digests"), list)
            or not record.get("gate_receipt_digests")
        ):
            raise ValueError("E_DELIVERY_AUDIT_ATTEMPT: durable attempt drifted")
        for digest in _delivery_audit_digest_list(record["gate_receipt_digests"], code="GATE"):
            receipt = store.load_gate_receipt(task_id, digest)
            if (
                receipt.get("task_digest") != plan.get("task_digest")
                or receipt.get("run_plan_digest") != plan.get("plan_digest")
                or receipt.get("attempt") != record.get("attempt")
            ):
                raise ValueError("E_DELIVERY_AUDIT_GATE: receipt binding drifted")
            gate_digests.append(digest)
    if len(gate_digests) != len(set(gate_digests)):
        raise ValueError("E_DELIVERY_AUDIT_GATE: receipt digest was reused")
    lifecycle_state = str(state["state"])
    evidence = state.get("evidence", {})
    if not isinstance(evidence, Mapping):
        raise ValueError("E_DELIVERY_AUDIT_EVIDENCE: lifecycle evidence is invalid")
    remote_registry = _delivery_audit_digest_list(
        state.get("remote_outcome_receipt_digests"), code="REMOTE",
    ) + _delivery_audit_digest_list(
        state.get("pull_request_outcome_receipt_digests"), code="REMOTE",
    )
    if len(remote_registry) != len(set(remote_registry)):
        raise ValueError("E_DELIVERY_AUDIT_REMOTE: receipt digest was reused")
    remote_registry.sort()
    pr_readiness_registry = _delivery_audit_digest_list(
        state.get("pr_readiness_receipt_digests"), code="PR",
    )
    if remote_registry and lifecycle_state not in {
        "pushed", "pr_draft", "pr_ready", "merged", "base_verified",
    }:
        raise ValueError("E_DELIVERY_AUDIT_REMOTE: receipt registry lacks proof")
    if pr_readiness_registry and lifecycle_state not in {"pr_draft", "pr_ready"}:
        raise ValueError("E_DELIVERY_AUDIT_PR: receipt registry lacks proof")
    binding = state.get("outcome_binding")
    needs_binding = lifecycle_state in {
        "committed", "pushed", "pr_draft", "pr_ready", "merged", "base_verified",
    }
    if binding is None:
        if needs_binding:
            raise ValueError("E_DELIVERY_AUDIT_EVIDENCE: outcome binding is missing")
        observed = {
            "head": revision["head"], "committed_head": None,
            "pushed_head": None, "pull_request_number": None,
        }
    elif (
        not isinstance(binding, Mapping)
        or validate_outcome_binding(binding)
        or binding.get("task_id") != task_id
        or binding.get("run_plan_digest") != plan.get("plan_digest")
        or binding.get("requested_outcome") != plan.get("requested_outcome")
        or binding.get("repository") != plan.get("repository")
        or binding.get("branch") != plan.get("branch")
    ):
        raise ValueError("E_DELIVERY_AUDIT_EVIDENCE: outcome binding drifted")
    else:
        pull_request = evidence.get("pr_draft", {}).get("pull_request") if isinstance(evidence.get("pr_draft"), Mapping) else None
        number = pull_request.get("number") if isinstance(pull_request, Mapping) else None
        if number is not None and (not isinstance(number, int) or isinstance(number, bool) or number <= 0):
            raise ValueError("E_DELIVERY_AUDIT_EVIDENCE: pull request observation is invalid")
        observed = {
            "head": revision["head"],
            "committed_head": binding.get("committed_head"),
            "pushed_head": binding.get("pushed_head"),
            "pull_request_number": number,
        }
    pushed_evidence = evidence.get("pushed")
    remote_digests: list[str] = []
    if lifecycle_state in {"pushed", "pr_draft", "pr_ready", "merged", "base_verified"} and (
        not isinstance(pushed_evidence, Mapping)
        or pushed_evidence.get("remote_head") != observed["pushed_head"]
        or not _digest(pushed_evidence.get("receipt_digest"))
        or pushed_evidence.get("receipt_digest") not in remote_registry
    ):
        raise ValueError("E_DELIVERY_AUDIT_REMOTE: pushed proof is unavailable")
    if isinstance(pushed_evidence, Mapping) and _digest(pushed_evidence.get("receipt_digest")):
        remote_digests.append(str(pushed_evidence["receipt_digest"]))
    pr_draft_evidence = evidence.get("pr_draft")
    if lifecycle_state in {"pr_draft", "pr_ready", "merged", "base_verified"} and (
        not isinstance(pr_draft_evidence, Mapping)
        or not isinstance(pr_draft_evidence.get("pull_request"), Mapping)
        or pr_draft_evidence["pull_request"].get("number") != observed["pull_request_number"]
        or pr_draft_evidence["pull_request"].get("head_commit") != observed["pushed_head"]
        or not _digest(pr_draft_evidence.get("receipt_digest"))
        or pr_draft_evidence.get("receipt_digest") not in remote_registry
    ):
        raise ValueError("E_DELIVERY_AUDIT_EVIDENCE: pull request proof is unavailable")
    if isinstance(pr_draft_evidence, Mapping) and _digest(pr_draft_evidence.get("receipt_digest")):
        remote_digests.append(str(pr_draft_evidence["receipt_digest"]))
    remote_digests = sorted(set(remote_digests))
    pending_repair = lifecycle_state == "pr_draft" and state.get("revision_required") is not None
    if pending_repair:
        try:
            TaskStore._validated_pull_request_revision_required(state, task_id=task_id)
        except ValueError as error:
            raise ValueError("E_DELIVERY_AUDIT_EVIDENCE: pending repair is invalid") from error
    pr_ready_evidence = evidence.get("pr_ready")
    pr_readiness_digests: list[str] = []
    if lifecycle_state == "pr_ready" and (
        not isinstance(pr_ready_evidence, Mapping)
        or pr_ready_evidence.get("authorizes") is not False
        or pr_ready_evidence.get("pull_request_digest") != binding.get("pull_request_digest")
        or pr_ready_evidence.get("checks_digest") != binding.get("checks_digest")
        or _delivery_audit_digest_list(
            pr_ready_evidence.get("receipt_digests"), code="PR",
        ) != pr_readiness_registry
        or not isinstance(pr_ready_evidence.get("checks_ok"), Mapping)
        or pr_ready_evidence["checks_ok"].get("ok") is not True
        or pr_ready_evidence["checks_ok"].get("head_commit") != observed["pushed_head"]
        or "pull_request" not in binding.get("consumed_effect_ids", [])
        or not pr_readiness_registry
    ):
        raise ValueError("E_DELIVERY_AUDIT_EVIDENCE: PR ready proof is unavailable")
    if lifecycle_state == "pr_ready":
        pr_readiness_digests = _delivery_audit_digest_list(
            pr_ready_evidence.get("receipt_digests"), code="PR",
        )
    elif pending_repair:
        pr_readiness_digests = _delivery_audit_digest_list(
            state["revision_required"].get("receipt_digests"), code="PR",
        )
    latest = attempts[-1] if attempts else None
    latest_view = (
        {
            "status": latest["status"], "retry_allowed": latest["retry_allowed"],
            "blocked": latest["blocked"], "stop_reason_code": latest["stop_reason_code"],
            "failure_reason_code": latest["failure_reason_code"],
        }
        if latest is not None else {
            "status": None, "retry_allowed": False, "blocked": False,
            "stop_reason_code": None, "failure_reason_code": None,
        }
    )
    blocked = lifecycle_state == "blocked"
    block_reason = state.get("block_reason") if (blocked or pending_repair) else None
    if (blocked or pending_repair) and not validate_task_id(block_reason):
        raise ValueError("E_DELIVERY_AUDIT_BLOCKED: block reason is unavailable")
    missing_evidence = (
        ["remote_observation"]
        if blocked and (block_reason == "E_RUN_UNKNOWN" or str(block_reason).endswith("_UNKNOWN"))
        else []
    )
    core = {
        "schema_version": 1,
        "kind": "DeliveryAuditV1",
        "task_id": task_id,
        "task_digest": plan["task_digest"],
        "run_plan_digest": plan["plan_digest"],
        "requested_outcome": plan["requested_outcome"],
        "visible_status": _visible_status(lifecycle_state),
        "lifecycle_state": lifecycle_state,
        "observed": observed,
        "receipt_digests": {
            "gate": sorted(gate_digests),
            "review": _delivery_audit_review_receipts(
                store, task_id=task_id, run_plan=plan, attempts=attempts,
            ),
            "remote_outcome": remote_digests,
            "pr_readiness": pr_readiness_digests,
        },
        "attempts": {
            "total": len(attempts), "maximum": MAX_EXECUTIONS,
            "repairs_used": max(0, len(attempts) - 1),
        },
        "latest_attempt": latest_view,
        "pending_repair": pending_repair,
        "block_reason_code": block_reason,
        "missing_evidence": missing_evidence,
        "next_safe_action": _delivery_audit_action(
            lifecycle_state, requested_outcome=str(plan["requested_outcome"]),
            latest_attempt=latest_view, pending_repair=pending_repair,
        ),
        "authorizes": False,
    }
    audit = {**core, "audit_digest": contract_digest(core)}
    if validate_delivery_audit(audit):
        raise ValueError("E_DELIVERY_AUDIT: generated audit is invalid")
    return audit


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("E_RUN_STATE: run state path is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
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


@contextmanager
def _run_guard(state_dir: Path, task_id: str):
    """Serialize immutable run-lineage mutations for one task durably."""

    if not validate_task_id(task_id):
        raise ValueError("E_TASK_ID: unsafe task ID")
    lock_dir = state_dir / "codex-control-plane" / "locks" / "runs"
    lock_dir.mkdir(parents=True, exist_ok=True)
    if lock_dir.is_symlink() or not lock_dir.is_dir():
        raise ValueError("E_RUN_STATE: run lock path is unsafe")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_dir / f"{task_id}.lock", flags, 0o600)
    except OSError as error:
        raise ValueError("E_RUN_STATE: run lock is unavailable") from error
    with os.fdopen(descriptor, "r+", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("E_RUN_STATE: run lock is unsafe")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def validate_stable_review_diff_artifact(
    value: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate the self-contained stable review-diff manifest."""

    issues = _closed_schema(
        value, keys=_REVIEW_ARTIFACT_KEYS, kind="StableReviewDiffArtifactV1"
    )
    if issues:
        return issues
    if (
        not validate_task_id(value.get("task_id"))
        or not isinstance(value.get("attempt"), int)
        or isinstance(value.get("attempt"), bool)
        or not 1 <= int(value["attempt"]) <= MAX_EXECUTIONS
        or not isinstance(value.get("repository"), str)
        or not Path(str(value["repository"])).is_absolute()
        or not all(
            isinstance(value.get(key), str)
            and _GIT_OBJECT_ID.fullmatch(str(value[key])) is not None
            for key in ("base_head", "reviewed_head")
        )
        or not _review_scope_paths(value.get("scope_paths"))
        or not isinstance(value.get("untracked_modes"), list)
        or any(
            not isinstance(item, list) or len(item) != 2
            or not safe_scope_path(item[0]) or not isinstance(item[1], int)
            or item[1] not in {0o644, 0o755}
            for item in value["untracked_modes"]
        )
        or not _digest(value.get("diff_digest"))
        or not isinstance(value.get("diff_size"), int)
        or isinstance(value.get("diff_size"), bool)
        or not 0 <= int(value["diff_size"]) <= MAX_REVIEW_DIFF_BYTES
        or value.get("authorizes") is not False
    ):
        return [_issue("RUN_ARTIFACT", "", "Stable review artifact is invalid.")]
    return _digest_issue(value, "artifact_digest", "RUN_DIGEST")


class ReviewArtifactStore:
    """Safely retain one self-contained, bounded diff per passed run attempt."""

    def __init__(self, repository: Path) -> None:
        self.repository = discover_repository(repository)
        self.git_dir = worktree_git_dir(self.repository)
        self.root = self.git_dir / "codex-control-plane" / "review-artifacts"

    @staticmethod
    def _diff_digest(diff: bytes) -> str:
        return contract_digest({"diff": diff.hex()})

    @staticmethod
    def _artifact_manifest(
        *, task_id: str, attempt: int, repository: Path, base_head: str,
        reviewed_head: str, scope_paths: tuple[str, ...], untracked_modes: list[list[object]], diff: bytes,
    ) -> dict[str, Any]:
        core = {
            "schema_version": 1, "kind": "StableReviewDiffArtifactV1",
            "task_id": task_id, "attempt": attempt,
            "repository": str(repository), "base_head": base_head,
            "reviewed_head": reviewed_head, "scope_paths": list(scope_paths),
            "untracked_modes": untracked_modes,
            "diff_digest": ReviewArtifactStore._diff_digest(diff),
            "diff_size": len(diff), "authorizes": False,
        }
        return {**core, "artifact_digest": contract_digest(core)}

    @staticmethod
    def _require_manifest(manifest: Mapping[str, Any]) -> None:
        if validate_stable_review_diff_artifact(manifest):
            raise ValueError("E_REVIEW_ARTIFACT: manifest is invalid")

    def _components(self, manifest: Mapping[str, Any]) -> tuple[str, ...]:
        self._require_manifest(manifest)
        if Path(str(manifest["repository"])).resolve() != self.repository:
            raise ValueError("E_REVIEW_ARTIFACT: repository binding drifted")
        return (
            "codex-control-plane", "review-artifacts", str(manifest["task_id"]),
            f"attempt-{manifest['attempt']}",
        )

    def manifest_path(self, manifest: Mapping[str, Any]) -> Path:
        components = self._components(manifest)
        return self.git_dir.joinpath(*components, "manifest.json")

    def load_manifest(self, task_id: str, attempt: int) -> dict[str, Any]:
        """Load the immutable manifest for one exact durable artifact."""

        if not validate_task_id(task_id) or not isinstance(attempt, int):
            raise ValueError("E_REVIEW_ARTIFACT: artifact request is invalid")
        if not 1 <= attempt <= MAX_EXECUTIONS:
            raise ValueError("E_REVIEW_ARTIFACT: artifact request is invalid")
        components = (
            "codex-control-plane", "review-artifacts", task_id, f"attempt-{attempt}",
        )
        try:
            descriptors = self._open_components(components, create=False)
        except FileNotFoundError as error:
            raise ValueError("E_REVIEW_ARTIFACT: artifact manifest is unavailable") from error
        try:
            manifest = json.loads(self._read_leaf(
                descriptors[-1], "manifest.json", MAX_REVIEW_PACKET_BYTES,
            ).decode("utf-8"))
        except (UnicodeDecodeError, OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("E_REVIEW_ARTIFACT: artifact manifest is unreadable") from error
        finally:
            self._close_dirs(descriptors)
        if not isinstance(manifest, dict) or validate_stable_review_diff_artifact(manifest):
            raise ValueError("E_REVIEW_ARTIFACT: artifact manifest is invalid")
        if (
            manifest.get("task_id") != task_id or manifest.get("attempt") != attempt
            or Path(str(manifest.get("repository", ""))).resolve() != self.repository
        ):
            raise ValueError("E_REVIEW_ARTIFACT: artifact manifest binding drifted")
        self.read_bounded(manifest)
        return manifest

    def _open_components(self, components: tuple[str, ...], *, create: bool) -> list[int]:
        """Open every artifact ancestor once, descriptor-relative and nofollow."""

        descriptors: list[int] = []
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptors.append(os.open(self.git_dir, flags))
            root_stat = os.fstat(descriptors[0])
            if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != os.getuid():
                raise ValueError("E_REVIEW_ARTIFACT: Git directory is unsafe")
            for index, component in enumerate(components):
                parent = descriptors[-1]
                try:
                    descriptor = os.open(component, flags, dir_fd=parent)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(component, 0o700, dir_fd=parent)
                    descriptor = os.open(component, flags, dir_fd=parent)
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or (index >= 1 and stat.S_IMODE(info.st_mode) != 0o700)
                ):
                    os.close(descriptor)
                    raise ValueError("E_REVIEW_ARTIFACT: artifact directory is unsafe")
                descriptors.append(descriptor)
            return descriptors
        except FileNotFoundError:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            raise
        except (OSError, ValueError) as error:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
            if isinstance(error, ValueError):
                raise
            raise ValueError("E_REVIEW_ARTIFACT: artifact path is unsafe") from error

    def _open_dirs(
        self, manifest: Mapping[str, Any], *, create: bool
    ) -> list[int]:
        try:
            return self._open_components(self._components(manifest), create=create)
        except FileNotFoundError as error:
            raise ValueError("E_REVIEW_ARTIFACT: artifact is unavailable") from error

    @staticmethod
    def _close_dirs(descriptors: list[int]) -> None:
        for descriptor in reversed(descriptors):
            os.close(descriptor)

    @staticmethod
    def _read_leaf(directory: int, name: str, maximum: int) -> bytes:
        descriptor = -1
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise ValueError("E_REVIEW_ARTIFACT: artifact leaf is unsafe")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(descriptor, min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > maximum:
                    raise ValueError("E_REVIEW_ARTIFACT: artifact exceeds byte cap")
            return b"".join(chunks)
        except OSError as error:
            raise ValueError("E_REVIEW_ARTIFACT: artifact leaf is unavailable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _write_all(descriptor: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ValueError("E_REVIEW_ARTIFACT: artifact write failed")
            offset += written

    @staticmethod
    def _write_leaf(directory: int, name: str, payload: bytes) -> None:
        temporary = f".{name}.pending"
        descriptor = -1
        failure: Exception | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=directory,
            )
            os.fchmod(descriptor, 0o600)
            ReviewArtifactStore._write_all(descriptor, payload)
            os.fsync(descriptor)
            os.link(
                temporary,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            os.fsync(directory)
        except Exception as error:
            failure = error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                raise ValueError(
                    "E_REVIEW_ARTIFACT: temporary leaf cleanup is uncertain"
                ) from cleanup_error
        if failure is not None:
            if isinstance(failure, ValueError) and str(failure).startswith(
                "E_REVIEW_ARTIFACT:"
            ):
                raise failure
            raise ValueError(
                "E_REVIEW_ARTIFACT: artifact write failed"
            ) from failure

    @staticmethod
    def _capture_git(
        repository: Path,
        arguments: tuple[str, ...],
        *,
        maximum: int = MAX_REVIEW_DIFF_BYTES,
        index_file: Path | str | None = None,
    ) -> bytes:
        closed_arguments = arguments
        if arguments[:1] == ("diff",) and "--no-textconv" not in arguments:
            closed_arguments = ("diff", "--no-textconv", *arguments[1:])
        if (
            arguments[:1] == ("diff",)
            and "--cached" not in arguments
            and "--staged" not in arguments
            and "--no-index" not in arguments
        ):
            assert_no_external_git_filters(
                repository, index_file=index_file
            )
        try:
            process = subprocess.Popen(
                trusted_git_argv(repository, closed_arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=trusted_git_environment(index_file=index_file),
            )
            assert process.stdout is not None
            payload = process.stdout.read(maximum + 1)
            process.stdout.close()
            returncode = process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("E_REVIEW_ARTIFACT_UNKNOWN: diff capture unavailable") from error
        if len(payload) > maximum:
            raise ValueError("E_REVIEW_ARTIFACT: diff exceeds byte cap")
        if returncode not in (0, 1):
            raise ValueError("E_REVIEW_ARTIFACT_UNKNOWN: diff capture failed")
        return payload

    def _read_untracked_once(self, path: str, maximum: int) -> bytes:
        """Copy one untracked regular file through nofollow descriptors only."""

        if not safe_scope_path(path):
            raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe")
        descriptors: list[int] = []
        leaf = -1
        try:
            descriptors.append(os.open(
                self.repository, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            ))
            root_info = os.fstat(descriptors[0])
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or root_info.st_uid != os.getuid()
                or stat.S_IMODE(root_info.st_mode) & 0o022
            ):
                raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe")
            for component in Path(path).parts[:-1]:
                descriptor = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptors[-1],
                )
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o022
                ):
                    os.close(descriptor)
                    raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe")
                descriptors.append(descriptor)
            leaf = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptors[-1])
            info = os.fstat(leaf)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = os.read(leaf, min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > maximum:
                    raise ValueError("E_REVIEW_ARTIFACT: diff exceeds byte cap")
            return b"".join(chunks)
        except OSError as error:
            raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe") from error
        finally:
            if leaf >= 0:
                os.close(leaf)
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _capture_untracked_diff(self, path: str, maximum: int) -> bytes:
        payload = self._read_untracked_once(path, maximum)
        with tempfile.TemporaryDirectory(prefix="codex-review-") as temporary:
            copy_path = Path(temporary) / "content"
            descriptor = os.open(copy_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                self._write_all(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            rendered = self._capture_git(self.repository, (
                "diff", "--no-index", "--binary", "--full-index", "--no-ext-diff",
                "--", "/dev/null", str(copy_path),
            ), maximum=maximum)
            return rendered.replace(str(copy_path).encode("utf-8"), path.encode("utf-8"))

    def _untracked_modes(self, paths: tuple[str, ...]) -> list[list[object]]:
        modes: list[list[object]] = []
        for path in paths:
            if path not in set(_git_untracked_paths(self.repository)):
                continue
            candidate = self.repository / path
            try:
                info = os.stat(candidate, follow_symlinks=False)
            except OSError as error:
                raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe") from error
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe")
            modes.append([path, stat.S_IMODE(info.st_mode)])
        return modes

    def _capture_diff(self, reviewed_head: str, paths: tuple[str, ...]) -> bytes:
        payload = bytearray(self._capture_git(
            self.repository,
            ("diff", "--binary", "--full-index", "--no-ext-diff", "--no-renames", reviewed_head, "--"),
            maximum=MAX_REVIEW_DIFF_BYTES,
        ))
        untracked = set(_git_untracked_paths(self.repository))
        for path in paths:
            if path not in untracked:
                continue
            piece = self._capture_untracked_diff(
                path, MAX_REVIEW_DIFF_BYTES - len(payload),
            )
            payload.extend(piece)
            if len(payload) > MAX_REVIEW_DIFF_BYTES:
                raise ValueError("E_REVIEW_ARTIFACT: diff exceeds byte cap")
        return bytes(payload)

    def create_from_repository(
        self,
        repository: Path,
        task_id: str,
        attempt: int,
        *,
        pending_run_plan: Mapping[str, Any] | None = None,
        pending_revision: Mapping[str, Any] | None = None,
        pending_changed_paths: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        root = discover_repository(repository)
        if root != self.repository or not validate_task_id(task_id) or not isinstance(attempt, int):
            raise ValueError("E_REVIEW_ARTIFACT: invalid artifact request")
        store = RunStore(worktree_git_dir(root))
        plan = store.load_plan(task_id)
        revision = store.load_active(task_id)
        pending = any(
            value is not None
            for value in (
                pending_run_plan,
                pending_revision,
                pending_changed_paths,
            )
        )
        if pending:
            if (
                pending_run_plan is None
                or pending_revision is None
                or pending_changed_paths is None
                or validate_run_plan(pending_run_plan)
                or validate_run_revision(pending_revision)
                or dict(pending_run_plan) != plan
                or dict(pending_revision) != revision
                or store.next_attempt(task_id) != attempt
            ):
                raise ValueError(
                    "E_REVIEW_ARTIFACT: pending attempt binding is invalid"
                )
            paths = tuple(str(path) for path in pending_changed_paths)
            record: Mapping[str, Any] = {
                "task_id": task_id,
                "task_digest": plan["task_digest"],
                "run_plan_digest": plan["plan_digest"],
                "status": "PASS",
                "run_revision_digest": revision["revision_digest"],
                "head": revision["head"],
            }
        else:
            attempts = store.attempts(task_id)
            if not attempts or attempts[-1].get("attempt") != attempt:
                raise ValueError(
                    "E_REVIEW_ARTIFACT: passed attempt is unavailable"
                )
            record = attempts[-1]
            paths = tuple(
                str(path) for path in record.get("changed_paths", ())
            )
        if (
            Path(str(plan.get("repository"))).resolve() != root
            or record.get("task_id") != task_id
            or record.get("task_digest") != plan.get("task_digest")
            or record.get("run_plan_digest") != plan.get("plan_digest")
            or record.get("status") != "PASS"
            or record.get("run_revision_digest") != revision.get("revision_digest")
            or record.get("head") != revision.get("head")
            or not paths
            or tuple(sorted(paths)) != paths
            or not all(any(scope_owns(str(scope), path) for scope in plan["scope_paths"]) for path in paths)
        ):
            raise ValueError("E_REVIEW_ARTIFACT: attempt binding is invalid")
        from control_plane.policy import load_policy
        policy = load_policy(root / ".codex" / "project-policy.toml")
        remote = str(policy["git"]["remote"])
        base_branch = str(policy["git"]["base_branch"])
        base_head = _git_text(root, "rev-parse", f"refs/remotes/{remote}/{base_branch}")
        before = _changed_paths(root)
        if before != paths:
            raise ValueError("E_REVIEW_ARTIFACT: working tree does not match attempt")
        untracked_modes = self._untracked_modes(paths)
        diff = self._capture_diff(str(revision["head"]), paths)
        after = _changed_paths(root)
        if (
            after != before
            or _git_text(root, "rev-parse", "HEAD") != revision["head"]
            or _git_text(root, "rev-parse", f"refs/remotes/{remote}/{base_branch}")
            != base_head
        ):
            raise ValueError("E_REVIEW_ARTIFACT_UNKNOWN: repository changed during capture")
        manifest = self._artifact_manifest(
            task_id=task_id, attempt=attempt, repository=root, base_head=base_head,
            reviewed_head=str(revision["head"]), scope_paths=paths,
            untracked_modes=untracked_modes, diff=diff,
        )
        try:
            directories = self._open_dirs(manifest, create=True)
            try:
                directory = directories[-1]
                state = self.artifact_state(manifest)
                if state == "present":
                    existing = self.read_bounded(manifest)
                elif state == "absent" or (
                    state == "partial" and not os.listdir(directory)
                ):
                    existing = None
                else:
                    raise ValueError(
                        "E_REVIEW_ARTIFACT: existing artifact is incomplete"
                    )
                if existing is not None:
                    if existing != diff:
                        raise ValueError(
                            "E_REVIEW_ARTIFACT: replay content drifted"
                        )
                    return manifest
                self._write_leaf(directory, "review.diff", diff)
                encoded = json.dumps(
                    manifest, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                if len(encoded) > MAX_REVIEW_PACKET_BYTES:
                    raise ValueError(
                        "E_REVIEW_ARTIFACT: manifest exceeds byte cap"
                    )
                self._write_leaf(directory, "manifest.json", encoded)
                os.fsync(directory)
                return manifest
            finally:
                self._close_dirs(directories)
        except Exception:
            deletion = self.artifact_state(manifest)
            if deletion in {"present", "partial"}:
                self.delete_exact(manifest)
            elif deletion == "drift":
                raise ValueError(
                    "E_REVIEW_ARTIFACT: partial artifact cleanup is uncertain"
                )
            raise

    def read_bounded(self, manifest: Mapping[str, Any]) -> bytes:
        directories = self._open_dirs(manifest, create=False)
        try:
            raw_manifest = self._read_leaf(directories[-1], "manifest.json", MAX_REVIEW_PACKET_BYTES)
            try:
                persisted = json.loads(raw_manifest.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("E_REVIEW_ARTIFACT: manifest is unreadable") from error
            if not isinstance(persisted, dict) or persisted != dict(manifest):
                raise ValueError("E_REVIEW_ARTIFACT: manifest binding drifted")
            self._require_manifest(persisted)
            diff = self._read_leaf(directories[-1], "review.diff", MAX_REVIEW_DIFF_BYTES)
            if len(diff) != persisted["diff_size"] or self._diff_digest(diff) != persisted["diff_digest"]:
                raise ValueError("E_REVIEW_ARTIFACT: diff binding drifted")
            return diff
        finally:
            self._close_dirs(directories)

    def artifact_state(self, manifest: Mapping[str, Any]) -> str:
        """Classify durable deletion state without interpreting error text."""

        try:
            directories = self._open_components(self._components(manifest), create=False)
        except FileNotFoundError:
            return "absent"
        except ValueError:
            return "drift"
        try:
            payloads: dict[str, bytes | None] = {}
            for leaf in ("manifest.json", "review.diff"):
                descriptor = -1
                try:
                    descriptor = os.open(
                        leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directories[-1],
                    )
                    info = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600
                    ):
                        return "drift"
                    maximum = (
                        MAX_REVIEW_PACKET_BYTES
                        if leaf == "manifest.json"
                        else MAX_REVIEW_DIFF_BYTES
                    )
                    chunks: list[bytes] = []
                    size = 0
                    while True:
                        chunk = os.read(
                            descriptor, min(65536, maximum + 1 - size)
                        )
                        if not chunk:
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > maximum:
                            return "drift"
                    payloads[leaf] = b"".join(chunks)
                except FileNotFoundError:
                    payloads[leaf] = None
                except OSError:
                    return "drift"
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
            raw_manifest = payloads["manifest.json"]
            if raw_manifest is not None:
                try:
                    persisted = json.loads(raw_manifest.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return "drift"
                if (
                    not isinstance(persisted, dict)
                    or persisted != dict(manifest)
                    or validate_stable_review_diff_artifact(persisted)
                ):
                    return "drift"
            diff = payloads["review.diff"]
            if diff is not None and (
                len(diff) != manifest["diff_size"]
                or self._diff_digest(diff) != manifest["diff_digest"]
            ):
                return "drift"
            return (
                "present"
                if raw_manifest is not None and diff is not None
                else "partial"
            )
        finally:
            self._close_dirs(directories)

    def delete_exact(self, manifest: Mapping[str, Any]) -> str:
        state = self.artifact_state(manifest)
        if state == "absent":
            return state
        if state == "drift":
            raise ValueError("E_REVIEW_ARTIFACT: exact delete drifted")
        directories = self._open_dirs(manifest, create=False)
        try:
            attempt_dir, task_dir, artifacts_dir, control_dir = (
                directories[-1], directories[-2], directories[-3], directories[-4]
            )
            for leaf in (
                "review.diff",
                "manifest.json",
                ".review.diff.pending",
                ".manifest.json.pending",
            ):
                try:
                    os.unlink(leaf, dir_fd=attempt_dir)
                except FileNotFoundError:
                    pass
            os.fsync(attempt_dir)
            os.fsync(task_dir)
            try:
                os.rmdir(f"attempt-{manifest['attempt']}", dir_fd=task_dir)
            except FileNotFoundError:
                pass
            os.fsync(task_dir)
            try:
                os.rmdir(str(manifest["task_id"]), dir_fd=artifacts_dir)
                os.fsync(artifacts_dir)
                os.rmdir("review-artifacts", dir_fd=control_dir)
                os.fsync(control_dir)
            except OSError:
                pass
            return "absent"
        except OSError as error:
            raise ValueError("E_REVIEW_ARTIFACT: exact delete failed") from error
        finally:
            self._close_dirs(directories)


class RunStore:
    """Persist compact run plans and receipts beneath one worktree Git dir."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.root = state_dir / "codex-control-plane" / "runs"

    def _directory(self, task_id: str) -> Path:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        return self.root / task_id

    def _plan_path(self, task_id: str) -> Path:
        return self._directory(task_id) / "plan.json"

    def _revision_directory(self, task_id: str) -> Path:
        return self._directory(task_id) / "revisions"

    def _revision_path(self, task_id: str, revision: int) -> Path:
        if not isinstance(revision, int) or isinstance(revision, bool) or not 0 <= revision <= 2:
            raise ValueError("E_RUN_REVISION: revision must be from zero to two")
        return self._revision_directory(task_id) / f"revision-{revision:02d}.json"

    def _safe_revision_directory(self, task_id: str, *, create: bool) -> Path:
        task_directory = self._directory(task_id)
        directory = self._revision_directory(task_id)
        for path in (task_directory, directory):
            if path.exists() and (path.is_symlink() or not path.is_dir()):
                raise ValueError("E_RUN_STATE: run revision path is unsafe")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        elif not directory.is_dir() or directory.is_symlink():
            raise ValueError("E_RUN_STATE: run revision path is unsafe")
        return directory

    def write_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        if validate_run_plan(plan):
            raise ValueError("E_RUN_PLAN: invalid RunPlanV1")
        task_id = str(plan["task_id"])
        with _run_guard(self.state_dir, task_id):
            return self._write_plan_locked(plan)

    def _write_plan_locked(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(plan["task_id"])
        path = self._plan_path(task_id)
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("E_RUN_STATE: run plan path is unsafe")
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("E_RUN_STATE: run plan is unreadable") from error
            if existing != dict(plan):
                raise ValueError("E_RUN_REPLAY: task ID already has another run plan")
            return existing
        _atomic_json(path, plan)
        return dict(plan)

    def load_plan(self, task_id: str) -> dict[str, Any]:
        path = self._plan_path(task_id)
        if path.is_symlink() or not path.is_file():
            raise ValueError("E_RUN_NOT_FOUND: run plan is unavailable")
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("E_RUN_STATE: run plan is unreadable") from error
        if not isinstance(plan, dict) or validate_run_plan(plan):
            raise ValueError("E_RUN_STATE: persisted run plan is invalid")
        return plan

    def _read_revision(self, task_id: str, revision: int) -> dict[str, Any]:
        self._safe_revision_directory(task_id, create=False)
        path = self._revision_path(task_id, revision)
        if path.is_symlink() or not path.is_file():
            raise ValueError("E_RUN_STATE: run revision is unavailable")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("E_RUN_STATE: run revision is unreadable") from error
        if (
            not isinstance(value, dict)
            or validate_run_revision(value)
            or value.get("task_id") != task_id
            or value.get("revision") != revision
        ):
            raise ValueError("E_RUN_STATE: run revision is invalid")
        plan = self.load_plan(task_id)
        if (
            value.get("task_digest") != plan.get("task_digest")
            or value.get("run_plan_digest") != plan.get("plan_digest")
            or value.get("repository") != plan.get("repository")
            or value.get("branch") != plan.get("branch")
        ):
            raise ValueError("E_RUN_STATE: run revision binding drifted")
        return value

    def write_initial_revision(self, run_plan: Mapping[str, Any]) -> dict[str, Any]:
        if validate_run_plan(run_plan):
            raise ValueError("E_RUN_PLAN: invalid RunPlanV1")
        plan = self.write_plan(run_plan)
        task_id = str(plan["task_id"])
        self._safe_revision_directory(task_id, create=True)
        revision = build_run_revision(
            run_plan=plan, revision=0, first_attempt=1, head=str(plan["head"]),
            reason="initial", parent_revision_digest=None,
            source_attempt_digest=None, source_review_receipt_digest=None,
            source_diff_digest=None,
        )
        path = self._revision_path(task_id, 0)
        if path.exists() or path.is_symlink():
            existing = self._read_revision(task_id, 0)
            if existing != revision:
                raise ValueError("E_RUN_REPLAY: initial revision changed")
            return existing
        _atomic_json(path, revision)
        return revision

    def load_revision(self, task_id: str, revision_digest: str) -> dict[str, Any]:
        if not _digest(revision_digest):
            raise ValueError("E_RUN_REVISION: revision digest is invalid")
        for revision in range(3):
            path = self._revision_path(task_id, revision)
            if not path.exists() and not path.is_symlink():
                continue
            value = self._read_revision(task_id, revision)
            if value["revision_digest"] == revision_digest:
                return value
        raise ValueError("E_RUN_NOT_FOUND: run revision is unavailable")

    def load_active(self, task_id: str) -> dict[str, Any]:
        initial = self._read_revision(task_id, 0)
        if (
            initial.get("first_attempt") != 1
            or initial.get("head") != self.load_plan(task_id).get("head")
            or initial.get("reason") != "initial"
        ):
            raise ValueError("E_RUN_STATE: initial revision binding drifted")
        active = initial
        for revision in (1, 2):
            path = self._revision_path(task_id, revision)
            if not path.exists() and not path.is_symlink():
                continue
            candidate = self._read_revision(task_id, revision)
            if (
                candidate.get("parent_revision_digest") != active.get("revision_digest")
                or candidate.get("first_attempt") <= active.get("first_attempt")
                or candidate.get("reason") not in {"review_findings", "pull_request_feedback"}
                or (
                    candidate.get("reason") == "review_findings"
                    and candidate.get("head") != active.get("head")
                )
            ):
                raise ValueError("E_RUN_STATE: revision lineage drifted")
            active = candidate
        return active

    def write_review_revision(self, revision: Mapping[str, Any]) -> dict[str, Any]:
        """Durably write one already-validated local-review revision exactly."""

        if validate_run_revision(revision):
            raise ValueError("E_RUN_REVISION: revision is invalid")
        task_id = str(revision["task_id"])
        plan = self.load_plan(task_id)
        if (
            revision.get("run_plan_digest") != plan.get("plan_digest")
            or revision.get("task_digest") != plan.get("task_digest")
            or revision.get("repository") != plan.get("repository")
            or revision.get("branch") != plan.get("branch")
            or int(revision.get("revision", -1)) == 0
        ):
            raise ValueError("E_RUN_REVISION: revision binding is invalid")
        number = int(revision["revision"])
        self._safe_revision_directory(task_id, create=True)
        path = self._revision_path(task_id, number)
        if path.exists() or path.is_symlink():
            existing = self._read_revision(task_id, number)
            if existing != dict(revision):
                raise ValueError("E_RUN_REPLAY: review revision changed")
            return existing
        _atomic_json(path, revision)
        return dict(revision)

    def delete_review_revision_exact(self, revision: Mapping[str, Any]) -> None:
        """Remove only an unactivated, byte-exact orphan revision record."""

        if validate_run_revision(revision):
            raise ValueError("E_RUN_REVISION: revision is invalid")
        task_id, number = str(revision["task_id"]), int(revision["revision"])
        path = self._revision_path(task_id, number)
        if not path.exists() or path.is_symlink() or self._read_revision(task_id, number) != dict(revision):
            raise ValueError("E_RUN_REVISION: orphan revision is inconsistent")
        path.unlink()

    def _attempt_path(self, task_id: str, attempt: int) -> Path:
        if not 1 <= attempt <= MAX_EXECUTIONS:
            raise ValueError("E_RUN_ATTEMPT: attempt must be from one to three")
        return self._directory(task_id) / f"attempt-{attempt}.json"

    def _rollback_plan_path(self, task_id: str, attempt: int) -> Path:
        if not 1 <= attempt <= MAX_EXECUTIONS:
            raise ValueError("E_ROLLBACK_PLAN: attempt must be from one to three")
        return (
            self._directory(task_id)
            / "rollback-plans"
            / f"attempt-{attempt}.json"
        )

    def _review_packet_path(self, task_id: str, attempt: int, review_kind: str) -> Path:
        if not 1 <= attempt <= MAX_EXECUTIONS or review_kind not in _REVIEW_KINDS:
            raise ValueError("E_REVIEW_PACKET: packet path is invalid")
        return self._directory(task_id) / "review-packets" / f"attempt-{attempt}" / f"{review_kind}.json"

    def _review_receipt_path(self, task_id: str, attempt: int, review_kind: str) -> Path:
        if not 1 <= attempt <= MAX_EXECUTIONS or review_kind not in _REVIEW_KINDS:
            raise ValueError("E_INDEPENDENT_REVIEW: receipt path is invalid")
        return self._directory(task_id) / "review-receipts" / f"attempt-{attempt}" / f"{review_kind}.json"

    @staticmethod
    def _load_closed_json(path: Path, *, maximum: int, code: str) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
            raise ValueError(f"{code}: persisted path is unsafe")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"{code}: persisted JSON is unreadable") from error
        if not isinstance(value, dict):
            raise ValueError(f"{code}: persisted JSON is invalid")
        return value

    def load_gate_receipt(self, task_id: str, digest: str) -> dict[str, Any]:
        if not _digest(digest):
            raise ValueError("E_RUN_RECEIPT: receipt digest is invalid")
        path = self._directory(task_id) / "receipts" / f"{digest.removeprefix('sha256:')}.json"
        receipt = self._load_closed_json(path, maximum=MAX_REVIEW_PACKET_BYTES, code="E_RUN_RECEIPT")
        if validate_gate_receipt(receipt) or receipt.get("task_id") != task_id or receipt.get("receipt_digest") != digest:
            raise ValueError("E_RUN_RECEIPT: persisted receipt binding drifted")
        return receipt

    def persist_rollback_plan(
        self,
        rollback_plan: Mapping[str, Any],
        *,
        observation: object | None = None,
    ) -> dict[str, Any]:
        """Consume one host proof immediately before durable plan publication."""

        if observation is None or not isinstance(rollback_plan, Mapping):
            raise ValueError("E_ROLLBACK_PLAN: structured host proof is required")
        if validate_rollback_plan(rollback_plan):
            raise ValueError("E_ROLLBACK_PLAN: plan contract is invalid")
        task_id = str(rollback_plan["task_id"])
        plan, revision = self.load_plan(task_id), self.load_active(task_id)
        attempt = int(rollback_plan["attempt"])
        if (
            plan.get("tier") != "T3"
            or "gate.rollback-plan" not in plan.get("required_gates", ())
            or rollback_plan.get("task_digest") != plan.get("task_digest")
            or rollback_plan.get("run_plan_digest") != plan.get("plan_digest")
            or rollback_plan.get("run_revision_digest")
            != revision.get("revision_digest")
            or rollback_plan.get("repository") != plan.get("repository")
            or rollback_plan.get("branch") != plan.get("branch")
            or rollback_plan.get("head") != revision.get("head")
            or rollback_plan.get("scope_paths_digest") != contract_digest({
                "scope_paths": plan.get("scope_paths")
            })
            or attempt != self.next_attempt(task_id)
        ):
            raise ValueError("E_ROLLBACK_PLAN: durable binding drifted")
        path = self._rollback_plan_path(task_id, attempt)
        if path.exists() or path.is_symlink():
            raise ValueError("E_ROLLBACK_PLAN_OBSERVATION: plan proof cannot replay")
        from control_plane.host_bridge import consume_rollback_plan_observation

        consume_rollback_plan_observation(
            observation,
            run_plan=plan,
            run_revision=revision,
            rollback_plan=rollback_plan,
        )
        _atomic_json(path, rollback_plan)
        return dict(rollback_plan)

    def load_rollback_plan(
        self, task_id: str, attempt: int
    ) -> dict[str, Any]:
        rollback_plan = self._load_closed_json(
            self._rollback_plan_path(task_id, attempt),
            maximum=MAX_ROLLBACK_PLAN_BYTES,
            code="E_ROLLBACK_PLAN",
        )
        plan, revision = self.load_plan(task_id), self.load_active(task_id)
        if (
            validate_rollback_plan(rollback_plan)
            or rollback_plan.get("task_id") != task_id
            or rollback_plan.get("attempt") != attempt
            or rollback_plan.get("task_digest") != plan.get("task_digest")
            or rollback_plan.get("run_plan_digest") != plan.get("plan_digest")
            or rollback_plan.get("run_revision_digest")
            != revision.get("revision_digest")
            or rollback_plan.get("head") != revision.get("head")
            or rollback_plan.get("scope_paths_digest") != contract_digest({
                "scope_paths": plan.get("scope_paths")
            })
        ):
            raise ValueError("E_ROLLBACK_PLAN: persisted plan binding drifted")
        return rollback_plan

    def persist_review_packet(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        if validate_review_packet(packet):
            raise ValueError("E_REVIEW_PACKET: packet is invalid")
        task_id, attempt, review_kind = str(packet["task_id"]), int(packet["attempt"]), str(packet["review_kind"])
        path = self._review_packet_path(task_id, attempt, review_kind)
        if path.exists() or path.is_symlink():
            existing = self._load_closed_json(path, maximum=MAX_REVIEW_PACKET_STORAGE_BYTES, code="E_REVIEW_PACKET")
            if existing != dict(packet) or path.read_bytes() != _canonical_json_bytes(packet):
                raise ValueError("E_REVIEW_PACKET: packet replay drifted")
            return existing
        _atomic_json(path, packet)
        return dict(packet)

    def load_review_packet(self, task_id: str, attempt: int, review_kind: str) -> dict[str, Any]:
        packet = self._load_closed_json(
            self._review_packet_path(task_id, attempt, review_kind),
            maximum=MAX_REVIEW_PACKET_STORAGE_BYTES, code="E_REVIEW_PACKET",
        )
        if (
            validate_review_packet(packet)
            or packet.get("task_id") != task_id
            or packet.get("attempt") != attempt
            or packet.get("review_kind") != review_kind
        ):
            raise ValueError("E_REVIEW_PACKET: persisted packet binding drifted")
        return packet

    def load_active_review_packet(
        self, task_id: str, attempt: int, review_kind: str,
    ) -> dict[str, Any]:
        """Load a packet only after its repository-bound handoff completed."""

        packet = self.load_review_packet(task_id, attempt, review_kind)
        task_store = TaskStore(self.state_dir)
        state = task_store.status(task_id)
        expected = {
            "revision_digest": packet["run_revision_digest"],
            "attempt_digest": packet["attempt_digest"],
            "artifact_digest": packet["artifact_digest"],
        }
        if (
            state.get("state") != "verifying"
            or state.get("active_run_revision_digest") != packet["run_revision_digest"]
            or state.get("evidence", {}).get("review_handoff") != expected
            or state.get("resume_forbidden")
        ):
            raise ValueError("E_REVIEW_PACKET: packet handoff is not active")
        root = discover_repository(Path(str(packet["repository"])))
        if worktree_git_dir(root).resolve() != self.state_dir.resolve():
            raise ValueError("E_REVIEW_PACKET: packet repository drifted")
        from control_plane.policy import load_policy
        plan = self.load_plan(task_id)
        task_store.handoff_to_local_review(
            task_id,
            expected_generation=int(state["generation"]),
            active_revision_digest=str(packet["run_revision_digest"]),
            attempt_digest=str(packet["attempt_digest"]),
            artifact_digest=str(packet["artifact_digest"]),
            worktree=str(root),
            branch=str(packet["branch"]),
            session=str(
                state.get("implementation_session_id", plan["session_id"])
            ),
            policy_digest=contract_digest(
                load_policy(root / ".codex" / "project-policy.toml")
            ),
        )
        return packet

    def persist_review_receipt(
        self,
        task_id: str,
        packet_digest: str,
        receipt: Mapping[str, Any],
        *,
        observation: object | None = None,
    ) -> dict[str, Any]:
        if observation is None:
            raise ValueError(
                "E_INDEPENDENT_REVIEW_OBSERVATION: host proof required"
            )
        if not _digest(packet_digest) or validate_independent_review_receipt(receipt):
            raise ValueError("E_INDEPENDENT_REVIEW: receipt is invalid")
        attempt, review_kind = receipt.get("attempt"), receipt.get("review_kind")
        if not isinstance(attempt, int) or review_kind not in _REVIEW_KINDS:
            raise ValueError("E_INDEPENDENT_REVIEW: receipt path binding is invalid")
        packet = self.load_active_review_packet(task_id, attempt, str(review_kind))
        with _run_guard(self.state_dir, task_id):
            return self._persist_review_receipt_locked(
                task_id=task_id,
                packet_digest=packet_digest,
                receipt=receipt,
                observation=observation,
                packet=packet,
            )

    def _persist_review_receipt_locked(
        self,
        *,
        task_id: str,
        packet_digest: str,
        receipt: Mapping[str, Any],
        observation: object,
        packet: Mapping[str, Any],
    ) -> dict[str, Any]:
        attempt, review_kind = receipt["attempt"], str(receipt["review_kind"])
        plan, revision = self.load_plan(task_id), self.load_active(task_id)
        attempts = self.attempts(task_id)
        if not attempts or attempts[-1].get("attempt") != attempt:
            raise ValueError("E_INDEPENDENT_REVIEW: historical attempt cannot receive a receipt")
        latest = attempts[-1]
        if packet.get("packet_digest") != packet_digest:
            raise ValueError("E_INDEPENDENT_REVIEW: packet digest does not resolve")
        bindings = (
            "task_id", "task_digest", "run_plan_digest", "run_revision_digest",
            "attempt", "attempt_digest", "repository", "base_head", "branch",
            "reviewed_head", "review_kind", "criteria_digest", "artifact_digest",
            "diff_digest",
        )
        if (
            receipt.get("review_packet_digest") != packet_digest
            or any(receipt.get(key) != packet.get(key) for key in bindings)
            or receipt.get("scope_paths_digest") != contract_digest({"scope_paths": packet["scope_paths"]})
            or packet.get("run_plan_digest") != plan.get("plan_digest")
            or packet.get("run_revision_digest") != revision.get("revision_digest")
            or packet.get("attempt_digest") != latest.get("attempt_digest")
            or packet.get("reviewed_head") != latest.get("head")
        ):
            raise ValueError("E_INDEPENDENT_REVIEW: receipt binding drifted")
        path = self._review_receipt_path(task_id, attempt, review_kind)
        if path.exists() or path.is_symlink():
            raise ValueError(
                "E_INDEPENDENT_REVIEW_OBSERVATION: review proof cannot replay"
            )
        from control_plane.host_bridge import consume_independent_review_observation

        consume_independent_review_observation(
            observation, review_packet=packet, receipt=receipt
        )
        _atomic_json(path, receipt)
        return dict(receipt)

    def active_review_receipts(self, task_id: str) -> list[dict[str, Any]]:
        plan, revision = self.load_plan(task_id), self.load_active(task_id)
        attempts = self.attempts(task_id)
        if not attempts:
            raise ValueError("E_INDEPENDENT_REVIEW: no active attempt")
        latest = attempts[-1]
        directory = self._directory(task_id) / "review-receipts" / f"attempt-{latest['attempt']}"
        if not directory.exists():
            return []
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("E_INDEPENDENT_REVIEW: receipt directory is unsafe")
        receipts: list[dict[str, Any]] = []
        for kind in sorted(_REVIEW_KINDS):
            path = directory / f"{kind}.json"
            if not path.exists() and not path.is_symlink():
                continue
            receipt = self._load_closed_json(path, maximum=MAX_REVIEW_PACKET_STORAGE_BYTES, code="E_INDEPENDENT_REVIEW")
            try:
                packet = self.load_active_review_packet(
                    task_id, int(latest["attempt"]), kind,
                )
            except ValueError as error:
                raise ValueError(
                    "E_INDEPENDENT_REVIEW: packet handoff is not active"
                ) from error
            if (
                validate_independent_review_receipt(receipt)
                or receipt.get("receipt_digest") is None
                or receipt.get("run_plan_digest") != plan["plan_digest"]
                or receipt.get("run_revision_digest") != revision["revision_digest"]
                or receipt.get("attempt_digest") != latest["attempt_digest"]
                or receipt.get("reviewed_head") != latest["head"]
                or receipt.get("review_packet_digest") != packet["packet_digest"]
            ):
                raise ValueError("E_INDEPENDENT_REVIEW: active receipt is stale")
            receipts.append(receipt)
        observation_digests = [
            str(receipt["observation_digest"]) for receipt in receipts
        ]
        if len(observation_digests) != len(set(observation_digests)):
            raise ValueError(
                "E_INDEPENDENT_REVIEW_OBSERVATION: distinct reviews required"
            )
        return receipts

    def attempts(self, task_id: str) -> list[dict[str, Any]]:
        directory = self._directory(task_id)
        records: list[dict[str, Any]] = []
        for attempt in range(1, MAX_EXECUTIONS + 1):
            path = self._attempt_path(task_id, attempt)
            if not path.exists():
                break
            if path.is_symlink() or not path.is_file():
                raise ValueError("E_RUN_STATE: attempt path is unsafe")
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("E_RUN_STATE: attempt record is unreadable") from error
            semantic = {
                key: value
                for key, value in record.items()
                if key != "attempt_digest"
            }
            if (
                not isinstance(record, dict)
                or record.get("schema_version") != 1
                or record.get("kind") != "RunAttemptV1"
                or record.get("attempt") != attempt
                or not _digest(record.get("run_revision_digest"))
                or record.get("attempt_digest") != contract_digest(semantic)
            ):
                raise ValueError("E_RUN_STATE: attempt record is invalid")
            records.append(record)
        return records

    def next_attempt(self, task_id: str) -> int:
        records = self.attempts(task_id)
        active = self.load_active(task_id)
        if records and records[-1].get("blocked") is True:
            raise ValueError("E_RUN_TERMINAL: run cannot execute another attempt")
        if (
            records
            and records[-1].get("status") == "PASS"
            and active.get("first_attempt") != len(records) + 1
        ):
            raise ValueError("E_RUN_TERMINAL: run cannot execute another attempt")
        attempt = len(records) + 1
        if attempt > MAX_EXECUTIONS:
            raise ValueError("E_RUN_EXHAUSTED: run exhausted three executions")
        return attempt

    def _write_receipt(self, task_id: str, receipt: Mapping[str, Any]) -> None:
        if validate_gate_receipt(receipt):
            raise ValueError("E_RUN_RECEIPT: invalid GateReceiptV1")
        digest = str(receipt["receipt_digest"])
        path = self._directory(task_id) / "receipts" / f"{digest.removeprefix('sha256:')}.json"
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("E_RUN_STATE: receipt path is unsafe")
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("E_RUN_STATE: receipt is unreadable") from error
            if existing != dict(receipt):
                raise ValueError("E_RUN_REPLAY: receipt digest was reused")
            return
        _atomic_json(path, receipt)

    def record_attempt(
        self,
        *,
        run_plan: Mapping[str, Any],
        run_revision: Mapping[str, Any],
        attempt: int,
        head: str,
        changed_paths: tuple[str, ...],
        receipts: tuple[Mapping[str, Any], ...],
        failure_reason_code: str | None,
        observed_at: str,
    ) -> dict[str, Any]:
        if validate_run_plan(run_plan):
            raise ValueError("E_RUN_PLAN: invalid RunPlanV1")
        task_id = str(run_plan["task_id"])
        with _run_guard(self.state_dir, task_id):
            return self._record_attempt_locked(
                run_plan=run_plan,
                run_revision=run_revision,
                attempt=attempt,
                head=head,
                changed_paths=changed_paths,
                receipts=receipts,
                failure_reason_code=failure_reason_code,
                observed_at=observed_at,
            )

    def _record_attempt_locked(
        self,
        *,
        run_plan: Mapping[str, Any],
        run_revision: Mapping[str, Any],
        attempt: int,
        head: str,
        changed_paths: tuple[str, ...],
        receipts: tuple[Mapping[str, Any], ...],
        failure_reason_code: str | None,
        observed_at: str,
    ) -> dict[str, Any]:
        if validate_run_plan(run_plan):
            raise ValueError("E_RUN_PLAN: invalid RunPlanV1")
        task_id = str(run_plan["task_id"])
        if self.load_plan(task_id) != dict(run_plan):
            raise ValueError("E_RUN_REPLAY: run plan binding changed")
        if (
            validate_run_revision(run_revision)
            or self.load_active(task_id) != dict(run_revision)
            or run_revision.get("run_plan_digest") != run_plan.get("plan_digest")
        ):
            raise ValueError("E_RUN_REVISION: active run revision is invalid")
        if (
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or not 1 <= attempt <= MAX_EXECUTIONS
            or attempt < run_revision.get("first_attempt", 0)
            or head != run_revision.get("head")
            or not receipts
            or len(set(changed_paths)) != len(changed_paths)
            or tuple(sorted(changed_paths)) != changed_paths
            or not all(safe_scope_path(item) for item in changed_paths)
            or not all(
                any(scope_owns(str(scope), item) for scope in run_plan["scope_paths"])
                for item in changed_paths
            )
            or not _timestamp(observed_at)
        ):
            raise ValueError("E_RUN_ATTEMPT: attempt binding is invalid")
        for receipt in receipts:
            if (
                validate_gate_receipt(receipt)
                or receipt.get("task_id") != task_id
                or receipt.get("task_digest") != run_plan.get("task_digest")
                or receipt.get("run_plan_digest") != run_plan.get("plan_digest")
                or receipt.get("attempt") != attempt
            ):
                raise ValueError("E_RUN_RECEIPT: receipt does not bind this attempt")
        statuses = tuple(str(item["status"]) for item in receipts)
        status = _aggregate_status(statuses)
        if (status == "PASS" and failure_reason_code is not None) or (
            status != "PASS" and not validate_task_id(failure_reason_code)
        ):
            raise ValueError("E_RUN_ATTEMPT: failure reason does not match status")
        previous = self.attempts(task_id)
        path = self._attempt_path(task_id, attempt)
        if not path.exists() and attempt != len(previous) + 1:
            raise ValueError("E_RUN_ATTEMPT: attempts must be recorded in order")
        prior_paths = set(previous[0]["changed_paths"]) if previous else set()
        scope_grew = bool(previous and set(changed_paths).difference(prior_paths))
        repeated = bool(
            previous
            and failure_reason_code is not None
            and previous[-1].get("failure_reason_code") == failure_reason_code
        )
        stop_reason = None
        if failure_reason_code == "E_RUN_NO_CHANGE":
            stop_reason = failure_reason_code
        elif status == "UNKNOWN":
            stop_reason = "E_RUN_UNKNOWN"
        elif scope_grew:
            stop_reason = "E_RUN_SCOPE_GROWTH"
        elif repeated:
            stop_reason = "E_RUN_REPEATED_FAILURE"
        elif status == "FAIL" and attempt == MAX_EXECUTIONS:
            stop_reason = "E_RUN_EXHAUSTED"
        core = {
            "schema_version": 1,
            "kind": "RunAttemptV1",
            "task_id": task_id,
            "task_digest": run_plan["task_digest"],
            "run_plan_digest": run_plan["plan_digest"],
            "run_revision_digest": run_revision["revision_digest"],
            "attempt": attempt,
            "head": head,
            "changed_paths": list(changed_paths),
            "status": status,
            "gate_receipt_digests": [
                str(item["receipt_digest"]) for item in receipts
            ],
            "failure_reason_code": failure_reason_code,
            "stop_reason_code": stop_reason,
            "retry_allowed": status == "FAIL" and stop_reason is None,
            "blocked": stop_reason is not None,
            "observed_at": observed_at,
        }
        record = {**core, "attempt_digest": contract_digest(core)}
        if path.exists():
            existing = next(
                (item for item in previous if item.get("attempt") == attempt),
                None,
            )
            if existing != record:
                raise ValueError("E_RUN_REPLAY: attempt identity changed")
            return existing
        for receipt in receipts:
            self._write_receipt(task_id, receipt)
        _atomic_json(path, record)
        return record


def _required_review_kinds(run_plan: Mapping[str, Any]) -> tuple[str, ...]:
    if run_plan.get("tier") not in {"T2", "T3"}:
        return ()
    kinds = tuple(
        kind for gate_id, kind in (
            ("gate.independent-review", "independent"),
            ("gate.security-review", "security"),
        )
        if gate_id in run_plan.get("required_gates", ())
    )
    if not kinds:
        raise ValueError("E_INDEPENDENT_REVIEW: plan does not require review")
    return kinds


def _executable_gate_ids(run_plan: Mapping[str, Any]) -> frozenset[str]:
    """Return the closed local evidence set for this exact persisted plan."""

    if validate_run_plan(run_plan):
        raise ValueError("E_RUN_PLAN: valid RunPlanV1 required")
    required = frozenset(str(item) for item in run_plan["required_gates"])
    return frozenset(_LOCAL_GATE_IDS).union(required.intersection(_PLAN_BOUND_GATE_IDS))


def _written_plan_receipt(
    *, repository: Path, run_plan: Mapping[str, Any], run_revision: Mapping[str, Any],
    attempt: int, observed_at: str,
) -> dict[str, Any]:
    """Prove the persisted plan binding without accepting caller evidence."""

    if (
        validate_run_plan(run_plan)
        or validate_run_revision(run_revision)
        or run_revision.get("run_plan_digest") != run_plan.get("plan_digest")
        or "gate.written-plan" not in run_plan.get("required_gates", ())
    ):
        raise ValueError("E_RUN_PLAN: written-plan binding is invalid")
    snapshot, _ = _snapshot(repository)
    return build_gate_receipt(
        run_plan=run_plan,
        attempt=attempt,
        gate_id="gate.written-plan",
        status="PASS",
        command_digest=contract_digest({
            "check": "bound-persisted-run-plan",
            "run_plan_digest": run_plan["plan_digest"],
            "run_revision_digest": run_revision["revision_digest"],
            "attempt": attempt,
        }),
        output_digest=contract_digest({
            "run_plan_digest": run_plan["plan_digest"],
            "decision_digest": run_plan["decision_digest"],
        }),
        before_snapshot_digest=snapshot,
        after_snapshot_digest=snapshot,
        error_code=None,
        observed_at=observed_at,
    )


def _written_plan_receipt_is_bound(
    receipt: Mapping[str, Any], *, run_plan: Mapping[str, Any],
    run_revision: Mapping[str, Any], attempt: int,
) -> bool:
    """Validate the deterministic fields of the persisted plan receipt."""

    return (
        not validate_gate_receipt(receipt)
        and receipt.get("gate_id") == "gate.written-plan"
        and receipt.get("status") == "PASS"
        and receipt.get("attempt") == attempt
        and receipt.get("task_digest") == run_plan.get("task_digest")
        and receipt.get("run_plan_digest") == run_plan.get("plan_digest")
        and receipt.get("command_digest") == contract_digest({
            "check": "bound-persisted-run-plan",
            "run_plan_digest": run_plan["plan_digest"],
            "run_revision_digest": run_revision["revision_digest"],
            "attempt": attempt,
        })
        and receipt.get("output_digest") == contract_digest({
            "run_plan_digest": run_plan["plan_digest"],
            "decision_digest": run_plan["decision_digest"],
        })
    )


def _rollback_plan_receipt(
    *,
    repository: Path,
    run_store: RunStore,
    run_plan: Mapping[str, Any],
    run_revision: Mapping[str, Any],
    attempt: int,
    observed_at: str,
) -> dict[str, Any]:
    """Derive the gate only from the exact persisted structured plan."""

    if (
        validate_run_plan(run_plan)
        or validate_run_revision(run_revision)
        or run_revision.get("run_plan_digest") != run_plan.get("plan_digest")
        or "gate.rollback-plan" not in run_plan.get("required_gates", ())
    ):
        raise ValueError("E_ROLLBACK_PLAN: gate binding is invalid")
    snapshot, _ = _snapshot(repository)
    try:
        rollback_plan = run_store.load_rollback_plan(
            str(run_plan["task_id"]), attempt
        )
    except ValueError:
        rollback_plan = None
    status = (
        "PASS"
        if rollback_plan is not None and rollback_plan.get("status") == "PASS"
        else "UNKNOWN"
    )
    command_digest = contract_digest({
        "check": "bound-persisted-rollback-plan",
        "run_plan_digest": run_plan["plan_digest"],
        "run_revision_digest": run_revision["revision_digest"],
        "attempt": attempt,
        "head": run_revision["head"],
    })
    output_digest = contract_digest(
        {
            "rollback_plan_digest": rollback_plan["plan_digest"],
            "observation_digest": rollback_plan["observation_digest"],
            "status": rollback_plan["status"],
        }
        if rollback_plan is not None
        else {
            "rollback_plan_digest": None,
            "observation_digest": None,
            "status": "UNKNOWN",
        }
    )
    return build_gate_receipt(
        run_plan=run_plan,
        attempt=attempt,
        gate_id="gate.rollback-plan",
        status=status,
        command_digest=command_digest,
        output_digest=output_digest,
        before_snapshot_digest=snapshot,
        after_snapshot_digest=snapshot,
        error_code=None if status == "PASS" else "E_ROLLBACK_PLAN_UNKNOWN",
        observed_at=observed_at,
    )


def _rollback_plan_receipt_is_bound(
    receipt: Mapping[str, Any],
    *,
    rollback_plan: Mapping[str, Any],
    run_plan: Mapping[str, Any],
    run_revision: Mapping[str, Any],
    attempt: int,
) -> bool:
    """Validate exact durable plan/attempt/HEAD binding of a PASS receipt."""

    return bool(
        not validate_gate_receipt(receipt)
        and not validate_rollback_plan(rollback_plan)
        and receipt.get("gate_id") == "gate.rollback-plan"
        and receipt.get("status") == "PASS"
        and receipt.get("attempt") == attempt
        and rollback_plan.get("status") == "PASS"
        and rollback_plan.get("attempt") == attempt
        and rollback_plan.get("task_digest") == run_plan.get("task_digest")
        and rollback_plan.get("run_plan_digest") == run_plan.get("plan_digest")
        and rollback_plan.get("run_revision_digest")
        == run_revision.get("revision_digest")
        and rollback_plan.get("head") == run_revision.get("head")
        and receipt.get("command_digest") == contract_digest({
            "check": "bound-persisted-rollback-plan",
            "run_plan_digest": run_plan["plan_digest"],
            "run_revision_digest": run_revision["revision_digest"],
            "attempt": attempt,
            "head": run_revision["head"],
        })
        and receipt.get("output_digest") == contract_digest({
            "rollback_plan_digest": rollback_plan["plan_digest"],
            "observation_digest": rollback_plan["observation_digest"],
            "status": "PASS",
        })
    )


def prepare_review_packet(
    repository: Path,
    task_id: str,
    attempt: int,
    review_kind: str,
    criteria_digest: str,
) -> dict[str, Any]:
    """Prepare the only public review-packet input from durable local proof."""

    from control_plane.policy import load_policy

    root = discover_repository(repository)
    if (
        not validate_task_id(task_id)
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or review_kind not in _REVIEW_KINDS
        or not _digest(criteria_digest)
    ):
        raise ValueError("E_REVIEW_PACKET: request is invalid")
    store = RunStore(worktree_git_dir(root))
    plan, revision = store.load_plan(task_id), store.load_active(task_id)
    if (
        Path(str(plan["repository"])).resolve() != root
        or review_kind not in _required_review_kinds(plan)
    ):
        raise ValueError("E_REVIEW_PACKET: plan binding is invalid")
    attempts = store.attempts(task_id)
    if not attempts or attempts[-1].get("attempt") != attempt:
        raise ValueError("E_REVIEW_PACKET: latest attempt is unavailable")
    record = attempts[-1]
    if (
        record.get("status") != "PASS"
        or record.get("task_digest") != plan["task_digest"]
        or record.get("run_plan_digest") != plan["plan_digest"]
        or record.get("run_revision_digest") != revision["revision_digest"]
        or record.get("head") != revision["head"]
        or not _digest(record.get("attempt_digest"))
    ):
        raise ValueError("E_REVIEW_PACKET: attempt binding is invalid")
    expected_gates = _executable_gate_ids(plan)
    gate_digests = record.get("gate_receipt_digests")
    if not isinstance(gate_digests, list) or len(gate_digests) != len(expected_gates):
        raise ValueError("E_REVIEW_PACKET: executable gate set is incomplete")
    gate_receipts = [store.load_gate_receipt(task_id, str(digest)) for digest in gate_digests]
    gate_ids = [receipt.get("gate_id") for receipt in gate_receipts]
    if (
        set(gate_ids) != set(expected_gates)
        or len(set(gate_ids)) != len(gate_ids)
        or any(
            receipt.get("status") != "PASS"
            or receipt.get("task_digest") != plan["task_digest"]
            or receipt.get("run_plan_digest") != plan["plan_digest"]
            or receipt.get("attempt") != attempt
            for receipt in gate_receipts
        )
    ):
        raise ValueError("E_REVIEW_PACKET: executable gate evidence drifted")
    written = next(
        (item for item in gate_receipts if item.get("gate_id") == "gate.written-plan"),
        None,
    )
    if "gate.written-plan" in expected_gates and (
        written is None
        or not _written_plan_receipt_is_bound(
            written, run_plan=plan, run_revision=revision, attempt=attempt,
        )
    ):
        raise ValueError("E_REVIEW_PACKET: written plan evidence drifted")
    rollback = next(
        (item for item in gate_receipts if item.get("gate_id") == "gate.rollback-plan"),
        None,
    )
    if "gate.rollback-plan" in expected_gates:
        try:
            rollback_plan = store.load_rollback_plan(task_id, attempt)
        except ValueError as error:
            raise ValueError(
                "E_REVIEW_PACKET: rollback plan evidence is unavailable"
            ) from error
        if rollback is None or not _rollback_plan_receipt_is_bound(
            rollback,
            rollback_plan=rollback_plan,
            run_plan=plan,
            run_revision=revision,
            attempt=attempt,
        ):
            raise ValueError("E_REVIEW_PACKET: rollback plan evidence drifted")
    artifact = ReviewArtifactStore(root).load_manifest(task_id, attempt)
    paths = tuple(record.get("changed_paths", ()))
    if (
        artifact.get("repository") != str(root)
        or artifact.get("reviewed_head") != revision["head"]
        or tuple(artifact.get("scope_paths", ())) != paths
        or not paths
    ):
        raise ValueError("E_REVIEW_PACKET: artifact binding drifted")
    # `load_manifest` has already bounded and digest-verified the diff bytes.
    summaries = sorted((_review_summary(receipt) for receipt in gate_receipts), key=lambda item: (item["check_kind"], item["check_id"]))
    packet = _build_review_packet(
        run_plan=plan, run_revision=revision, attempt_record=record,
        artifact=artifact, review_kind=review_kind, criteria_digest=criteria_digest,
        evidence_summaries=summaries,
    )
    persisted = store.persist_review_packet(packet)
    # Packet durability is the release boundary: never make the reviewer wait
    # behind an implementation lease, but never release before the packet exists.
    policy = load_policy(root / ".codex" / "project-policy.toml")
    state = TaskStore(worktree_git_dir(root)).status(task_id)
    TaskStore(worktree_git_dir(root)).handoff_to_local_review(
        task_id,
        expected_generation=int(state["generation"]),
        active_revision_digest=str(revision["revision_digest"]),
        attempt_digest=str(record["attempt_digest"]),
        artifact_digest=str(artifact["artifact_digest"]),
        worktree=str(root),
        branch=str(plan["branch"]),
        session=str(state.get("implementation_session_id", plan["session_id"])),
        policy_digest=contract_digest(policy),
    )
    return persisted


def promote_review_ready(
    *,
    state_dir: Path,
    run_plan: Mapping[str, Any],
    receipt_digests: tuple[str, ...],
) -> dict[str, Any]:
    """Validate the exact non-authorizing review receipt set for promotion."""

    if validate_run_plan(run_plan):
        raise ValueError("E_INDEPENDENT_REVIEW: valid run plan required")
    required = _required_review_kinds(run_plan)
    store = RunStore(state_dir)
    durable = store.active_review_receipts(str(run_plan["task_id"]))
    if len(receipt_digests) != len(required) or len(durable) != len(required):
        raise ValueError("E_INDEPENDENT_REVIEW: required receipt set is incomplete")
    durable_digests = [item.get("receipt_digest") for item in durable]
    if (
        not all(_digest(item) for item in receipt_digests)
        or len(set(receipt_digests)) != len(receipt_digests)
        or set(receipt_digests) != set(durable_digests)
        or any(validate_independent_review_receipt(item) for item in durable)
    ):
        raise ValueError("E_INDEPENDENT_REVIEW: receipt replay or durability drift")
    by_kind = {str(item.get("review_kind")): item for item in durable}
    if set(by_kind) != set(required) or any(item.get("status") != "PASS" for item in by_kind.values()):
        raise ValueError("E_INDEPENDENT_REVIEW: required receipt did not pass")
    attempts = store.attempts(str(run_plan["task_id"]))
    if not attempts or attempts[-1].get("status") != "PASS":
        raise ValueError("E_INDEPENDENT_REVIEW: receipt is stale for run attempt")
    latest = attempts[-1]
    expected_gates = _executable_gate_ids(run_plan)
    gate_digests = latest.get("gate_receipt_digests")
    if not isinstance(gate_digests, list) or len(gate_digests) != len(expected_gates):
        raise ValueError("E_INDEPENDENT_REVIEW: executable gate set is incomplete")
    gates = [store.load_gate_receipt(str(run_plan["task_id"]), str(item)) for item in gate_digests]
    if (
        {item.get("gate_id") for item in gates} != set(expected_gates)
        or len({item.get("gate_id") for item in gates}) != len(gates)
        or any(item.get("status") != "PASS" or item.get("attempt") != latest["attempt"] for item in gates)
    ):
        raise ValueError("E_INDEPENDENT_REVIEW: executable gate receipt drifted")
    written = next(
        (item for item in gates if item.get("gate_id") == "gate.written-plan"),
        None,
    )
    active_revision = store.load_active(str(run_plan["task_id"]))
    if "gate.written-plan" in expected_gates and (
        written is None
        or not _written_plan_receipt_is_bound(
            written, run_plan=run_plan, run_revision=active_revision,
            attempt=int(latest["attempt"]),
        )
    ):
        raise ValueError("E_INDEPENDENT_REVIEW: written plan evidence drifted")
    rollback = next(
        (item for item in gates if item.get("gate_id") == "gate.rollback-plan"),
        None,
    )
    if "gate.rollback-plan" in expected_gates:
        try:
            rollback_plan = store.load_rollback_plan(
                str(run_plan["task_id"]), int(latest["attempt"])
            )
        except ValueError as error:
            raise ValueError(
                "E_INDEPENDENT_REVIEW: rollback plan evidence is unavailable"
            ) from error
        if rollback is None or not _rollback_plan_receipt_is_bound(
            rollback,
            rollback_plan=rollback_plan,
            run_plan=run_plan,
            run_revision=active_revision,
            attempt=int(latest["attempt"]),
        ):
            raise ValueError(
                "E_INDEPENDENT_REVIEW: rollback plan evidence drifted"
            )
    for receipt in durable:
        packet = store.load_active_review_packet(
            str(run_plan["task_id"]), int(latest["attempt"]),
            str(receipt["review_kind"]),
        )
        if (
            packet.get("packet_digest") != receipt.get("review_packet_digest")
            or packet.get("evidence_summaries")
            != sorted((_review_summary(item) for item in gates), key=lambda item: (item["check_kind"], item["check_id"]))
        ):
            raise ValueError("E_INDEPENDENT_REVIEW: packet evidence drifted")
    core = {
        "run_plan_digest": run_plan["plan_digest"],
        "review_receipt_digests": sorted(receipt_digests),
        "review_kinds": list(required),
        "authorizes": False,
    }
    return {**core, "promotion_digest": contract_digest(core)}


def publish_review_ready(
    *,
    repository: Path,
    task_id: str,
    expected_generation: int,
    receipt_digests: tuple[str, ...],
) -> dict[str, Any]:
    """Perform the one local transition after an exact durable receipt set."""

    root = discover_repository(repository)
    state_dir = worktree_git_dir(root)
    run_plan = RunStore(state_dir).load_plan(task_id)
    proof = promote_review_ready(
        state_dir=state_dir, run_plan=run_plan, receipt_digests=receipt_digests
    )
    task_store = TaskStore(state_dir)
    state = task_store.status(task_id)
    if state.get("state") != "verifying" or state.get("generation") != expected_generation:
        raise ValueError("E_INDEPENDENT_REVIEW: task is not awaiting review")
    records = RunStore(state_dir).attempts(task_id)
    active = RunStore(state_dir).load_active(task_id)
    if not records or records[-1].get("status") != "PASS":
        raise ValueError("E_INDEPENDENT_REVIEW: no passed active attempt")
    artifact = ReviewArtifactStore(root).load_manifest(task_id, int(records[-1]["attempt"]))
    return task_store.finalize_review_ready(
        task_id, expected_generation=expected_generation,
        run_plan_digest=str(run_plan["plan_digest"]),
        run_revision_digest=str(active["revision_digest"]),
        attempt_digest=str(records[-1]["attempt_digest"]),
        promotion_digest=str(proof["promotion_digest"]),
        receipt_digests=receipt_digests,
        artifact=artifact,
        current_branch=str(run_plan["branch"]),
    )


def start_local_review_revision(
    *,
    repository: Path,
    task_id: str,
    expected_generation: int,
    review_receipt_digest: str,
    new_session_id: str,
) -> dict[str, Any]:
    """Resume one local run after an exact blocking independent review.

    This is the root-kernel API: callers provide neither repository facts nor
    mutable run details.  They are loaded and cross-checked from durable state.
    """

    if not _digest(review_receipt_digest) or not validate_task_id(new_session_id):
        raise ValueError("E_LOCAL_REVIEW: request binding is invalid")
    root = discover_repository(repository)
    state_dir = worktree_git_dir(root)
    store = RunStore(state_dir)
    plan = store.load_plan(task_id)
    active = store.load_active(task_id)
    attempts = store.attempts(task_id)
    if (
        Path(str(plan.get("repository", ""))).resolve() != root
        or not attempts
        or attempts[-1].get("status") != "PASS"
        or attempts[-1].get("run_revision_digest") != active.get("revision_digest")
        or _git_text(root, "branch", "--show-current") != plan.get("branch")
        or _git_text(root, "rev-parse", "HEAD") != active.get("head")
    ):
        raise ValueError("E_LOCAL_REVIEW: active run binding is invalid")
    artifact = ReviewArtifactStore(root).load_manifest(task_id, int(attempts[-1]["attempt"]))
    receipts = store.active_review_receipts(task_id)
    receipt = next(
        (item for item in receipts if item.get("receipt_digest") == review_receipt_digest),
        None,
    )
    if receipt is None:
        raise ValueError("E_LOCAL_REVIEW: review receipt is unavailable")
    packet = store.load_active_review_packet(
        task_id, int(attempts[-1]["attempt"]), str(receipt["review_kind"]),
    )
    if (
        receipt.get("status") != "FAIL"
        or not (int(receipt.get("critical", 0)) or int(receipt.get("important", 0)))
        or receipt.get("review_packet_digest") != packet.get("packet_digest")
        or receipt.get("run_revision_digest") != active.get("revision_digest")
        or receipt.get("attempt_digest") != attempts[-1].get("attempt_digest")
        or receipt.get("artifact_digest") != artifact.get("artifact_digest")
        or receipt.get("diff_digest") != artifact.get("diff_digest")
        or packet.get("artifact_digest") != artifact.get("artifact_digest")
        or packet.get("diff_digest") != artifact.get("diff_digest")
    ):
        raise ValueError("E_LOCAL_REVIEW: review proof is invalid")
    state = TaskStore(state_dir).status(task_id)
    handoff = state.get("evidence", {}).get("review_handoff")
    if (
        state.get("state") != "verifying"
        or state.get("generation") != expected_generation
        or state.get("active_run_revision_digest") != active.get("revision_digest")
        or not isinstance(handoff, Mapping)
        or handoff != {
            "revision_digest": active["revision_digest"],
            "attempt_digest": attempts[-1]["attempt_digest"],
            "artifact_digest": artifact["artifact_digest"],
        }
    ):
        raise ValueError("E_LOCAL_REVIEW: handoff binding is invalid")
    if int(attempts[-1]["attempt"]) >= MAX_EXECUTIONS:
        return TaskStore(state_dir).finalize_exhausted_review(
            task_id,
            expected_generation=expected_generation,
            run_plan_digest=str(plan["plan_digest"]),
            run_revision_digest=str(active["revision_digest"]),
            attempt_digest=str(attempts[-1]["attempt_digest"]),
            review_kind=str(receipt["review_kind"]),
            review_receipt_digest=str(receipt["receipt_digest"]),
            artifact=artifact,
            current_branch=str(plan["branch"]),
        )
    revision = build_run_revision(
        run_plan=plan, revision=int(active["revision"]) + 1,
        first_attempt=int(attempts[-1]["attempt"]) + 1,
        head=str(active["head"]), reason="review_findings",
        parent_revision_digest=str(active["revision_digest"]),
        source_attempt_digest=str(attempts[-1]["attempt_digest"]),
        source_review_receipt_digest=review_receipt_digest,
        source_diff_digest=str(artifact["diff_digest"]),
    )
    from control_plane.policy import load_policy
    return TaskStore(state_dir).start_local_review_revision(
        task_id, expected_generation=expected_generation, run_plan=plan,
        parent_revision=active, latest_attempt=attempts[-1],
        review_receipt=receipt, artifact=artifact, revision=revision,
        worktree=str(root), policy_digest=contract_digest(
            load_policy(root / ".codex" / "project-policy.toml")
        ), new_session_id=new_session_id,
    )


def _git_text(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            trusted_git_argv(repository, arguments),
            check=False,
            capture_output=True,
            text=True,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "E_RUN_GIT: required Git fact is unavailable"
        ) from error
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise ValueError("E_RUN_GIT: required Git fact is unavailable")
    return value


def _validate_route_for_run(
    task: Mapping[str, Any], decision: Mapping[str, Any]
) -> list[str]:
    semantic = {
        key: value
        for key, value in decision.items()
        if key not in {"command", "decision_digest"}
    }
    facts = decision.get("facts", {})
    try:
        deferred_effects = deferred_effects_for_outcome(
            task.get("requested_outcome")
        )
    except ValueError as error:
        raise ValueError("E_RUN_OUTCOME: supported outcome required") from error
    approval_boundaries = decision.get("approval_boundaries")
    if (
        decision.get("decision_digest") != contract_digest(semantic)
        or decision.get("task_id") != task.get("task_id")
        or not isinstance(facts, Mapping)
        or facts.get("task_digest") != contract_digest(task)
        or decision.get("authorization", {}).get("local_write") is not True
        or not isinstance(approval_boundaries, (list, tuple))
        or not all(isinstance(item, str) for item in approval_boundaries)
        or "local_write" in approval_boundaries
        or any(item not in deferred_effects for item in approval_boundaries)
        or decision.get("errors") not in ([], ())
    ):
        raise ValueError(
            "E_RUN_AUTHORITY: current route does not permit the requested local run"
        )
    return deferred_effects


def prepare_run(
    *,
    task: Mapping[str, Any],
    decision: Mapping[str, Any],
    repository: Path,
    policy: Mapping[str, Any],
    session_id: str,
    prepared_at: str,
) -> dict[str, Any]:
    """Prepare lifecycle and lease state; never execute implementation work."""

    if validate_task_envelope(task):
        raise ValueError("E_RUN_TASK: supported TaskEnvelope v1 required")
    _validate_route_for_run(task, decision)
    root = discover_repository(repository)
    materialization = inspect_tracked_materialization(root)
    if materialization.status != "PASS":
        raise ValueError(
            f"{materialization.error_code or 'E_MATERIALIZATION_UNKNOWN'}: "
            "tracked source is not fully materialized"
        )
    preflight = evaluate_preflight(root, policy, "write")
    if not preflight.ok:
        code = preflight.errors[0].code if preflight.errors else "E_RUN_PREFLIGHT"
        raise ValueError(f"{code}: write preflight did not pass")
    branch = _git_text(root, "branch", "--show-current")
    head = _git_text(root, "rev-parse", "HEAD")
    if branch != preflight.facts.get("branch") or head != preflight.facts.get("head"):
        raise ValueError("E_RUN_GIT: Git facts changed after preflight")
    plan = build_run_plan(
        task=task,
        decision=decision,
        repository=root,
        branch=branch,
        head=head,
        session_id=session_id,
        prepared_at=prepared_at,
    )
    state_dir = worktree_git_dir(root)
    run_store = RunStore(state_dir)
    try:
        existing_plan = run_store.load_plan(str(task["task_id"]))
    except ValueError as error:
        if not str(error).startswith("E_RUN_NOT_FOUND:"):
            raise
        run_store.write_plan(plan)
    else:
        stable_keys = set(_RUN_PLAN_KEYS) - {"prepared_at", "plan_digest"}
        if any(existing_plan[key] != plan[key] for key in stable_keys):
            raise ValueError("E_RUN_REPLAY: persisted run binding changed")
        plan = existing_plan
    task_store = TaskStore(state_dir)
    state = task_store.start(
        str(task["task_id"]),
        outcome=str(task["requested_outcome"]),
        branch=branch,
        task_digest=str(plan["task_digest"]),
        decision_digest=str(plan["decision_digest"]),
    )
    revision = run_store.write_initial_revision(plan)
    state = task_store.bind_active_run_revision(
        str(task["task_id"]),
        run_plan_digest=str(plan["plan_digest"]),
        revision_digest=str(revision["revision_digest"]),
        current_branch=branch,
    )
    lease = TaskLease.acquire(
        state_dir,
        task_id=str(task["task_id"]),
        worktree=str(root),
        branch=branch,
        session_id=session_id,
        paths=[str(item) for item in task["scope_paths"]],
        policy_digest=contract_digest(policy),
    )
    if state["state"] == "framed":
        state = task_store.transition(
            str(task["task_id"]), "planned", current_branch=branch
        )
    if state["state"] == "planned":
        state = task_store.transition(
            str(task["task_id"]),
            "ready",
            evidence={"preflight_ok": True},
            current_branch=branch,
        )
    if state["state"] == "ready":
        state = task_store.transition(
            str(task["task_id"]), "implementing", current_branch=branch
        )
    if state["state"] != "implementing":
        raise ValueError("E_RUN_STATE: prepared run is not implementing")
    return {"run_plan": plan, "run_revision": revision, "task": state, "lease": lease}


def block_run(*, repository: Path, task_id: str, reason_code: str) -> dict[str, Any]:
    """Permanently stop one writer run and release its exact lease."""

    if not validate_task_id(reason_code):
        raise ValueError("E_RUN_BLOCK_REASON: reason must be a stable code")
    root = discover_repository(repository)
    state_dir = worktree_git_dir(root)
    store = TaskStore(state_dir)
    state = store.status(task_id)
    if state["state"] == "blocked":
        return state
    return store._finalize_writer(
        task_id,
        expected_generation=int(state["generation"]),
        marker_state="finalizing_suspend",
        destination="blocked",
        reason_code=reason_code,
    )


def _changed_paths(repository: Path) -> tuple[str, ...]:
    assert_no_external_git_filters(repository)
    values: set[str] = set()
    commands = (
        ("diff", "--name-only", "-z", "HEAD", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    for arguments in commands:
        try:
            completed = subprocess.run(
                trusted_git_argv(repository, arguments),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=trusted_git_environment(),
                stdin=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(
                "E_RUN_GIT: changed paths are unavailable"
            ) from error
        if completed.returncode != 0:
            raise ValueError("E_RUN_GIT: changed paths are unavailable")
        try:
            paths = completed.stdout.decode("utf-8").split("\0")
        except UnicodeDecodeError as error:
            raise ValueError("E_RUN_GIT: changed paths are not UTF-8") from error
        values.update(path for path in paths if path)
    ordered = tuple(sorted(values))
    if not all(safe_scope_path(path) for path in ordered):
        raise ValueError("E_RUN_SCOPE: changed path is unsafe")
    return ordered


def _git_untracked_paths(repository: Path) -> tuple[str, ...]:
    """Return only ignored-filtered untracked paths in deterministic order."""

    try:
        process = subprocess.Popen(
            trusted_git_argv(
                repository,
                ("ls-files", "--others", "--exclude-standard", "-z"),
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=trusted_git_environment(),
        )
        assert process.stdout is not None
        output = process.stdout.read(MAX_REVIEW_PACKET_BYTES + 1)
        process.stdout.close()
        returncode = process.wait(timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("E_REVIEW_ARTIFACT_UNKNOWN: untracked paths are unavailable") from error
    if returncode != 0:
        raise ValueError("E_REVIEW_ARTIFACT_UNKNOWN: untracked paths are unavailable")
    if len(output) > MAX_REVIEW_PACKET_BYTES:
        raise ValueError("E_REVIEW_ARTIFACT: untracked inventory exceeds byte cap")
    try:
        paths = tuple(sorted(path for path in output.decode("utf-8").split("\0") if path))
    except UnicodeDecodeError as error:
        raise ValueError("E_REVIEW_ARTIFACT_UNKNOWN: untracked paths are not UTF-8") from error
    if not all(safe_scope_path(path) for path in paths):
        raise ValueError("E_REVIEW_ARTIFACT: untracked path is unsafe")
    return paths


def _relevant_tests_command_plan(
    repository: Path,
    *,
    profiles: tuple[str, ...],
    changed_paths: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[tuple[tuple[str, ...], tuple[int, ...]], ...]]:
    python_tests = (
        tuple(
            sorted(
                path
                for path in (repository / "tests").rglob("test*.py")
                if path.is_file() and not path.is_symlink()
            )
        )
        if (repository / "tests").is_dir()
        else ()
    )
    manifest = repository / "package.json"
    node_runner = repository / "scripts" / "run-unit-tests.mjs"
    node_test_root = repository / "tests" / "unit"
    node_tests = (
        tuple(
            sorted(
                path
                for path in node_test_root.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and path.name.endswith((".spec.js", ".node-test.mjs"))
            )
        )
        if node_test_root.is_dir()
        else ()
    )
    prefers_node = bool(
        set(profiles).intersection(
            {"android", "ios", "web_pwa", "saas_backend"}
        )
    )
    node_argv: tuple[str, ...] | None = None
    if manifest.is_file() and node_runner.is_file() and node_tests:
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "E_RUN_RELEVANT_TESTS_UNAVAILABLE: package manifest is unreadable"
            ) from error
        scripts = (
            package.get("scripts", {}) if isinstance(package, Mapping) else {}
        )
        if not isinstance(scripts, Mapping) or scripts.get("test:unit") != (
            "node ./scripts/run-unit-tests.mjs"
        ):
            raise ValueError(
                "E_RUN_RELEVANT_TESTS_UNAVAILABLE: unit runner is not bound"
            )
        node = shutil.which("node")
        if node is None:
            raise ValueError(
                "E_RUN_RELEVANT_TESTS_UNAVAILABLE: node is unavailable"
            )
        node_path = Path(node).resolve()
        try:
            node_path.relative_to(repository.resolve())
        except ValueError:
            pass
        else:
            raise ValueError(
                "E_RUN_RELEVANT_TESTS_UNAVAILABLE: node is repository-controlled"
            )
        node_argv = (str(node_path), str(node_runner.resolve()))
    python_argv = (
        (sys.executable, "-c", _UNITTEST_DISCOVERY_PROGRAM)
        if python_tests
        else None
    )
    python_changed = any(
        path.endswith(".py")
        or Path(path).name in {"pyproject.toml", "setup.cfg", "setup.py", "tox.ini"}
        or Path(path).name.startswith("requirements")
        for path in changed_paths
    )
    node_changed = any(
        path.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"))
        or Path(path).name
        in {
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        }
        for path in changed_paths
    )
    selected: list[tuple[str, ...]] = []
    if python_changed:
        if python_argv is None:
            raise ValueError(
                "E_RUN_RELEVANT_TESTS_UNAVAILABLE: Python changes lack a bounded test runner"
            )
        selected.append(python_argv)
    if node_changed:
        if node_argv is None:
            raise ValueError(
                "E_RUN_RELEVANT_TESTS_UNAVAILABLE: Node changes lack a bounded test runner"
            )
        selected.append(node_argv)
    if not selected:
        if prefers_node and node_argv is not None:
            selected.append(node_argv)
        elif python_argv is not None and node_argv is not None:
            selected.extend((python_argv, node_argv))
        elif python_argv is not None:
            selected.append(python_argv)
        elif node_argv is not None:
            selected.append(node_argv)
        else:
            raise ValueError(
                "E_RUN_RELEVANT_TESTS_UNAVAILABLE: no bounded project test runner was found"
            )
    primary = selected[0]
    command_plan = tuple((argv, (0,)) for argv in selected)
    return primary, command_plan


def _local_gate_commands(
    repository: Path,
    *,
    profiles: tuple[str, ...],
    changed_paths: tuple[str, ...],
) -> tuple[
    tuple[
        str,
        tuple[str, ...],
        tuple[tuple[tuple[str, ...], tuple[int, ...]], ...] | None,
    ],
    ...,
]:
    launcher = str((repository / "scripts" / "control-plane").resolve())
    policy = str((repository / ".codex" / "project-policy.toml").resolve())
    registry = str((repository / ".codex" / "resource-registry.toml").resolve())
    relevant_argv, relevant_plan = _relevant_tests_command_plan(
        repository,
        profiles=profiles,
        changed_paths=changed_paths,
    )
    return (
        (
            "gate.relevant-tests",
            relevant_argv,
            relevant_plan,
        ),
        (
            "gate.policy-check",
            (launcher, "policy-check", "--policy", policy, "--json"),
            None,
        ),
        (
            "gate.registry-check",
            (
                launcher,
                "registry-check",
                "--registry",
                registry,
                "--policy",
                policy,
                "--json",
            ),
            None,
        ),
        (
            "gate.doctor",
            (launcher, "doctor", "--repo", str(repository), "--json"),
            None,
        ),
        (
            "gate.diff-review",
            tuple(trusted_git_argv(repository, ("diff", "--check"))),
            None,
        ),
    )


def _untracked_paths(repository: Path) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            trusted_git_argv(
                repository,
                ("ls-files", "--others", "--exclude-standard", "-z"),
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=trusted_git_environment(),
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "E_RUN_GIT: untracked paths are unavailable"
        ) from error
    if completed.returncode != 0:
        raise ValueError("E_RUN_GIT: untracked paths are unavailable")
    try:
        paths = tuple(
            sorted(
                path
                for path in completed.stdout.decode("utf-8").split("\0")
                if path
            )
        )
    except UnicodeDecodeError as error:
        raise ValueError("E_RUN_GIT: untracked path is not UTF-8") from error
    if not all(safe_scope_path(path) for path in paths):
        raise ValueError("E_RUN_SCOPE: untracked path is unsafe")
    return paths


def _snapshot(repository: Path) -> tuple[str, bool]:
    try:
        from control_plane.lifecycle import _verification_snapshot

        return _verification_snapshot(repository), True
    except (OSError, ValueError):
        return contract_digest({"snapshot": "UNKNOWN"}), False


def _closed_gate_environment(temp_root: Path) -> dict[str, str]:
    git_observation = trusted_git_environment()
    executable_dirs = {
        str(Path(sys.executable).resolve().parent),
        "/usr/bin",
        "/bin",
    }
    return {
        "PATH": os.pathsep.join(sorted(executable_dirs)),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(temp_root / "home"),
        "TMPDIR": str(temp_root / "tmp"),
        "XDG_CACHE_HOME": str(temp_root / "cache"),
        "PYTHONPYCACHEPREFIX": str(temp_root / "pycache"),
        "GIT_CONFIG_NOSYSTEM": git_observation["GIT_CONFIG_NOSYSTEM"],
        "GIT_CONFIG_SYSTEM": git_observation["GIT_CONFIG_SYSTEM"],
        "GIT_CONFIG_GLOBAL": git_observation["GIT_CONFIG_GLOBAL"],
        "GIT_GRAFT_FILE": git_observation["GIT_GRAFT_FILE"],
        "GIT_NO_LAZY_FETCH": git_observation["GIT_NO_LAZY_FETCH"],
        "GIT_NO_REPLACE_OBJECTS": git_observation[
            "GIT_NO_REPLACE_OBJECTS"
        ],
        "GIT_OPTIONAL_LOCKS": git_observation["GIT_OPTIONAL_LOCKS"],
        "GIT_LITERAL_PATHSPECS": git_observation[
            "GIT_LITERAL_PATHSPECS"
        ],
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "SSH_AUTH_SOCK": "",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }


def _execute_closed_gate(
    *,
    repository: Path,
    state_dir: Path,
    run_plan: Mapping[str, Any],
    run_revision: Mapping[str, Any],
    attempt: int,
    gate_id: str,
    argv: tuple[str, ...],
    observed_at: str,
    command_plan: tuple[tuple[tuple[str, ...], tuple[int, ...]], ...] | None = None,
) -> dict[str, Any]:
    if gate_id not in _LOCAL_GATE_IDS:
        raise ValueError("E_RUN_GATE: gate is outside the closed local profile")
    if (
        validate_run_plan(run_plan)
        or validate_run_revision(run_revision)
        or run_revision.get("run_plan_digest") != run_plan.get("plan_digest")
        or run_revision.get("repository") != run_plan.get("repository")
        or run_revision.get("branch") != run_plan.get("branch")
    ):
        raise ValueError("E_RUN_REVISION: gate revision does not bind run plan")
    before, before_ok = _snapshot(repository)
    temp_root = state_dir / "codex-control-plane" / "run-temp" / str(run_plan["task_id"])
    for name in ("home", "tmp", "cache", "pycache"):
        (temp_root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    output = bytearray()
    output_truncated = False
    timed_out = False
    execution_ok = True
    failed = False
    outcomes: list[dict[str, object]] = []
    commands = command_plan or ((argv, (0,)),)
    environment = _closed_gate_environment(temp_root)
    repository_root = repository.resolve()
    command_directories: set[str] = set()
    for command_argv, _ in commands:
        executable = Path(command_argv[0])
        if not executable.is_absolute():
            continue
        resolved = executable.resolve()
        try:
            resolved.relative_to(repository_root)
        except ValueError:
            command_directories.add(str(resolved.parent))
    environment["PATH"] = os.pathsep.join(
        sorted(
            {
                *environment["PATH"].split(os.pathsep),
                *command_directories,
            }
        )
    )
    deadline = time.monotonic() + _GATE_TIMEOUT_SECONDS
    for command_argv, accepted_returncodes in commands:
        returncode = -1
        try:
            process = subprocess.Popen(
                command_argv,
                cwd=repository,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment,
                shell=False,
                start_new_session=True,
            )
            if process.stdout is None:
                raise OSError("child output unavailable")
            os.set_blocking(process.stdout.fileno(), False)
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            stream_open = True
            try:
                while stream_open or process.poll() is None:
                    if time.monotonic() >= deadline:
                        timed_out = True
                        if process.poll() is None:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        break
                    for key, _ in selector.select(
                        0.05 if process.poll() is None else 0
                    ):
                        try:
                            chunk = os.read(key.fd, 65_536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(process.stdout)
                            stream_open = False
                            continue
                        available = max(0, 1_048_576 - len(output))
                        output.extend(chunk[:available])
                        output_truncated = (
                            output_truncated or len(chunk) > available
                        )
                returncode = process.wait(timeout=1)
            finally:
                selector.close()
                process.stdout.close()
        except (OSError, subprocess.SubprocessError):
            execution_ok = False
        outcomes.append(
            {
                "argv": list(command_argv),
                "accepted_returncodes": list(accepted_returncodes),
                "returncode": returncode,
            }
        )
        if not execution_ok or timed_out or output_truncated:
            break
        if returncode not in accepted_returncodes:
            failed = True
            break
    after, after_ok = _snapshot(repository)
    immutable = before_ok and after_ok and before == after
    current_head = ""
    try:
        current_head = _git_text(repository, "rev-parse", "HEAD")
    except ValueError:
        execution_ok = False
    gate_code = gate_id.removeprefix("gate.").replace("-", "_").replace(".", "_").upper()
    if not execution_ok or timed_out or output_truncated or not immutable or current_head != run_revision["head"]:
        status = "UNKNOWN"
        error_code = f"E_RUN_GATE_{gate_code}_UNKNOWN"
    elif failed:
        status = "FAIL"
        error_code = f"E_RUN_GATE_{gate_code}_FAILED"
    else:
        status = "PASS"
        error_code = None
    return build_gate_receipt(
        run_plan=run_plan,
        attempt=attempt,
        gate_id=gate_id,
        status=status,
        command_digest=contract_digest(
            {
                "commands": [
                    {
                        "argv": list(command_argv),
                        "accepted_returncodes": list(accepted_returncodes),
                    }
                    for command_argv, accepted_returncodes in commands
                ]
            }
        ),
        output_digest=contract_digest(
            {
                "bytes_hex": bytes(output).hex(),
                "truncated": output_truncated,
                "timed_out": timed_out,
                "outcomes": outcomes,
            }
        ),
        before_snapshot_digest=before,
        after_snapshot_digest=after,
        error_code=error_code,
        observed_at=observed_at,
    )


def _execute_diff_review_gate(
    *,
    repository: Path,
    state_dir: Path,
    run_plan: Mapping[str, Any],
    run_revision: Mapping[str, Any],
    attempt: int,
    observed_at: str,
) -> dict[str, Any]:
    assert_no_external_git_filters(repository)
    tracked = tuple(
        trusted_git_argv(repository, ("diff", "HEAD", "--check"))
    )
    command_plan: list[tuple[tuple[str, ...], tuple[int, ...]]] = [
        (tracked, (0,))
    ]
    for relative in _untracked_paths(repository):
        candidate = repository / relative
        if candidate.is_symlink():
            continue
        if not candidate.is_file():
            raise ValueError("E_RUN_GIT: untracked path is not a regular file")
        command_plan.append(
            (
                (
                    *trusted_git_argv(
                        repository,
                        (
                            "diff",
                            "--no-index",
                            "--check",
                            "--",
                            os.devnull,
                            str(candidate),
                        ),
                    ),
                ),
                (0, 1),
            )
        )
    return _execute_closed_gate(
        repository=repository,
        state_dir=state_dir,
        run_plan=run_plan,
        run_revision=run_revision,
        attempt=attempt,
        gate_id="gate.diff-review",
        argv=tracked,
        observed_at=observed_at,
        command_plan=tuple(command_plan),
    )


def verify_run(
    *, repository: Path, task_id: str, observed_at: str
) -> dict[str, Any]:
    """Execute the closed local profile and publish one bounded attempt."""

    from control_plane.policy import load_policy

    root = discover_repository(repository)
    state_dir = worktree_git_dir(root)
    run_store = RunStore(state_dir)
    plan = run_store.load_plan(task_id)
    run_revision = run_store.load_active(task_id)
    if Path(str(plan["repository"])).resolve() != root:
        raise ValueError("E_RUN_BINDING: repository changed")
    branch = _git_text(root, "branch", "--show-current")
    head = _git_text(root, "rev-parse", "HEAD")
    if branch != plan["branch"] or head != run_revision["head"]:
        raise ValueError("E_RUN_DRIFT: branch or HEAD changed")
    materialization = inspect_tracked_materialization(root)
    if materialization.status != "PASS":
        raise ValueError("E_RUN_UNKNOWN: materialization is not proven")
    policy = load_policy(root / ".codex" / "project-policy.toml")
    changed_paths = _changed_paths(root)
    task_store = TaskStore(state_dir)
    state = task_store.status(task_id)
    TaskLease.validate(
        state_dir,
        task_id=task_id,
        worktree=str(root),
        branch=branch,
        session_id=str(state.get("implementation_session_id", plan["session_id"])),
        policy_digest=contract_digest(policy),
        changed_paths=list(changed_paths),
    )
    if (
        state.get("run_plan_digest") != plan.get("plan_digest")
        or state.get("active_run_revision_digest")
        != run_revision.get("revision_digest")
    ):
        raise ValueError("E_RUN_REVISION: task state does not bind active revision")
    if state["state"] == "implementing":
        state = task_store.transition(
            task_id,
            "verifying",
            evidence={"implementation_complete": True},
            current_branch=branch,
        )
    if state["state"] != "verifying":
        raise ValueError("E_RUN_STATE: run is not ready to verify")
    attempt_number = run_store.next_attempt(task_id)
    receipts: list[dict[str, Any]] = []
    unchanged_review_diff = False
    if run_revision.get("reason") == "review_findings":
        source_diff_digest = run_revision.get("source_diff_digest")
        if not _digest(source_diff_digest):
            raise ValueError("E_RUN_REVISION: review correction source diff is invalid")
        current_diff = ReviewArtifactStore(root)._capture_diff(head, changed_paths)
        unchanged_review_diff = (
            ReviewArtifactStore._diff_digest(current_diff) == source_diff_digest
        )
    if unchanged_review_diff:
        snapshot, _ = _snapshot(root)
        receipts.append(
            build_gate_receipt(
                run_plan=plan,
                attempt=attempt_number,
                gate_id="gate.diff-review",
                status="FAIL",
                command_digest=contract_digest({"check": "review-correction-diff"}),
                output_digest=contract_digest({"source_diff_digest": run_revision["source_diff_digest"]}),
                before_snapshot_digest=snapshot,
                after_snapshot_digest=snapshot,
                error_code="E_RUN_REVIEW_UNCHANGED",
                observed_at=observed_at,
            )
        )
    elif not changed_paths:
        snapshot, _ = _snapshot(root)
        receipts.append(
            build_gate_receipt(
                run_plan=plan,
                attempt=attempt_number,
                gate_id="gate.diff-review",
                status="FAIL",
                command_digest=contract_digest({"check": "non-empty-diff"}),
                output_digest=contract_digest({"changed_paths": []}),
                before_snapshot_digest=snapshot,
                after_snapshot_digest=snapshot,
                error_code="E_RUN_NO_CHANGE",
                observed_at=observed_at,
            )
        )
    else:
        try:
            local_commands = _local_gate_commands(
                root,
                profiles=tuple(str(item) for item in plan["profiles"]),
                changed_paths=changed_paths,
            )
        except ValueError as error:
            if not str(error).startswith("E_RUN_RELEVANT_TESTS_UNAVAILABLE:"):
                raise
            snapshot, snapshot_ok = _snapshot(root)
            receipts.append(
                build_gate_receipt(
                    run_plan=plan,
                    attempt=attempt_number,
                    gate_id="gate.relevant-tests",
                    status="FAIL" if snapshot_ok else "UNKNOWN",
                    command_digest=contract_digest(
                        {"selection": "bounded-project-test-runner"}
                    ),
                    output_digest=contract_digest(
                        {"error_code": "E_RUN_RELEVANT_TESTS_UNAVAILABLE"}
                    ),
                    before_snapshot_digest=snapshot,
                    after_snapshot_digest=snapshot,
                    error_code="E_RUN_RELEVANT_TESTS_UNAVAILABLE",
                    observed_at=observed_at,
                )
            )
        else:
            for gate_id, argv, command_plan in local_commands:
                if gate_id == "gate.diff-review":
                    receipt = _execute_diff_review_gate(
                        repository=root,
                        state_dir=state_dir,
                        run_plan=plan,
                        run_revision=run_revision,
                        attempt=attempt_number,
                        observed_at=observed_at,
                    )
                else:
                    receipt = _execute_closed_gate(
                        repository=root,
                        state_dir=state_dir,
                        run_plan=plan,
                        run_revision=run_revision,
                        attempt=attempt_number,
                        gate_id=gate_id,
                        argv=argv,
                        observed_at=observed_at,
                        command_plan=command_plan,
                    )
                receipts.append(receipt)
                if receipt["status"] != "PASS":
                    break
    if receipts and all(item["status"] == "PASS" for item in receipts):
        # Review and outcome gates are deliberately deferred: a local runner
        # must neither forge them as UNKNOWN nor treat them as executable.
        # Plan-bound gates derive only from durable exact contracts. Declaring
        # a gate in required_gates is never evidence that it passed.
        if "gate.written-plan" in plan["required_gates"]:
            receipts.append(_written_plan_receipt(
                repository=root, run_plan=plan, run_revision=run_revision,
                attempt=attempt_number, observed_at=observed_at,
            ))
        if "gate.rollback-plan" in plan["required_gates"]:
            receipts.append(_rollback_plan_receipt(
                repository=root,
                run_store=run_store,
                run_plan=plan,
                run_revision=run_revision,
                attempt=attempt_number,
                observed_at=observed_at,
            ))
    failure_reason = next(
        (str(item["error_code"]) for item in receipts if item["status"] != "PASS"),
        None,
    )
    review_manifest: dict[str, Any] | None = None
    local_gates_passed = bool(receipts) and all(
        item["status"] == "PASS" for item in receipts
    )
    if local_gates_passed:
        try:
            review_manifest = ReviewArtifactStore(root).create_from_repository(
                root,
                task_id,
                attempt_number,
                pending_run_plan=plan,
                pending_revision=run_revision,
                pending_changed_paths=changed_paths,
            )
        except ValueError:
            block_run(
                repository=root,
                task_id=task_id,
                reason_code="E_REVIEW_ARTIFACT",
            )
            raise
    try:
        attempt_record = run_store.record_attempt(
            run_plan=plan,
            run_revision=run_revision,
            attempt=attempt_number,
            head=head,
            changed_paths=changed_paths,
            receipts=tuple(receipts),
            failure_reason_code=failure_reason,
            observed_at=observed_at,
        )
    except Exception:
        if review_manifest is not None:
            ReviewArtifactStore(root).delete_exact(review_manifest)
        raise
    review_artifact: dict[str, Any] | None = None
    if attempt_record["status"] == "PASS" and plan["tier"] in {"T2", "T3"}:
        if review_manifest is None:
            raise ValueError(
                "E_REVIEW_ARTIFACT: passed review run has no stable artifact"
            )
        review_artifact = {
            "manifest": review_manifest,
            "artifact_digest": review_manifest["artifact_digest"],
        }
    elif attempt_record["status"] != "PASS" and review_manifest is not None:
        ReviewArtifactStore(root).delete_exact(review_manifest)
    if attempt_record["status"] == "PASS" and plan["tier"] in {"T0", "T1"}:
        if review_manifest is None:
            raise ValueError(
                "E_REVIEW_ARTIFACT: passed direct-tier run has no stable artifact"
            )
        state = task_store.handoff_to_local_review(
            task_id,
            expected_generation=int(state["generation"]),
            active_revision_digest=str(run_revision["revision_digest"]),
            attempt_digest=str(attempt_record["attempt_digest"]),
            artifact_digest=str(review_manifest["artifact_digest"]),
            worktree=str(root),
            branch=branch,
            session=str(
                state.get("implementation_session_id", plan["session_id"])
            ),
            policy_digest=contract_digest(policy),
        )
        state = publish_review_ready(
            repository=root,
            task_id=task_id,
            expected_generation=int(state["generation"]),
            receipt_digests=(),
        )
    elif attempt_record["blocked"]:
        state = block_run(
            repository=root,
            task_id=task_id,
            reason_code=str(attempt_record["stop_reason_code"]),
        )
    summary = build_run_summary(
        run_plan=plan,
        head=head,
        lifecycle_state=str(state["state"]),
        attempt_count=attempt_number,
        gate_statuses=tuple(str(item["status"]) for item in receipts),
        gate_receipt_digests=tuple(str(item["receipt_digest"]) for item in receipts),
        review_result_digest=None,
        blocked_reason_code=(
            str(attempt_record["stop_reason_code"])
            if attempt_record["blocked"]
            else None
        ),
        observed_at=observed_at,
    )
    result = {
        "run_plan": plan,
        "run_revision": run_revision,
        "task": state,
        "attempt": attempt_record,
        "receipts": receipts,
        "summary": summary,
    }
    if review_artifact is not None:
        result["review_artifact"] = review_artifact
    return result
