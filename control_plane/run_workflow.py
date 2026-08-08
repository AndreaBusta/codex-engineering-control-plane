"""Closed contracts for a skill-led, deterministic local engineering run."""

from __future__ import annotations

from pathlib import Path
import json
import os
import re
import selectors
import shutil
import signal
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
from control_plane.lifecycle import TaskLease, TaskStore
from control_plane.materialization import inspect_tracked_materialization
from control_plane.repository import (
    discover_repository,
    git_environment,
    worktree_git_dir,
)
from control_plane.scopes import scope_owns


RUN_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN"})
VISIBLE_STATUSES = frozenset(
    {"PLANIFICANDO", "TRABAJANDO", "VERIFICANDO", "PR LISTA", "BLOCKED"}
)
MAX_EXECUTIONS = 3
_GATE_TIMEOUT_SECONDS = 300.0
_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40,64}$", re.ASCII)
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII)
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$",
    re.ASCII,
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
        "max_executions",
        "prepared_at",
        "plan_digest",
    }
)
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


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code, path, message)


def _digest(value: object) -> bool:
    return isinstance(value, str) and SHA256_DIGEST.fullmatch(value) is not None


def _timestamp(value: object) -> bool:
    return isinstance(value, str) and _TIMESTAMP.fullmatch(value) is not None


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
    if (
        not isinstance(clarification, Mapping)
        or clarification.get("level") != "low"
        or clarification.get("status") != "autonomous"
        or clarification.get("decision_ready") is not True
        or decision.get("decision_ready") is not True
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
        or not all(isinstance(item, str) and item for item in required_gates)
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
        "max_executions": MAX_EXECUTIONS,
        "prepared_at": prepared_at,
    }
    return {**core, "plan_digest": contract_digest(core)}


def validate_run_plan(value: Mapping[str, Any]) -> list[ContractIssue]:
    issues = _closed_schema(value, keys=_RUN_PLAN_KEYS, kind="RunPlanV1")
    if issues:
        return issues
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
        or value.get("requested_outcome")
        not in {"answer", "local_change", "commit", "pull_request", "integration", "release"}
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
        or not all(isinstance(item, str) and item for item in value["required_gates"])
        or value.get("max_executions") != MAX_EXECUTIONS
        or not _timestamp(value.get("prepared_at"))
    ):
        return [_issue("RUN_BINDING", "", "RunPlanV1 binding is invalid.")]
    return _digest_issue(value, "plan_digest", "RUN_DIGEST")


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


class RunStore:
    """Persist compact run plans and receipts beneath one worktree Git dir."""

    def __init__(self, state_dir: Path) -> None:
        self.root = state_dir / "codex-control-plane" / "runs"

    def _directory(self, task_id: str) -> Path:
        if not validate_task_id(task_id):
            raise ValueError("E_TASK_ID: unsafe task ID")
        return self.root / task_id

    def _plan_path(self, task_id: str) -> Path:
        return self._directory(task_id) / "plan.json"

    def write_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        if validate_run_plan(plan):
            raise ValueError("E_RUN_PLAN: invalid RunPlanV1")
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

    def _attempt_path(self, task_id: str, attempt: int) -> Path:
        if not 1 <= attempt <= MAX_EXECUTIONS:
            raise ValueError("E_RUN_ATTEMPT: attempt must be from one to three")
        return self._directory(task_id) / f"attempt-{attempt}.json"

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
                or record.get("attempt_digest") != contract_digest(semantic)
            ):
                raise ValueError("E_RUN_STATE: attempt record is invalid")
            records.append(record)
        return records

    def next_attempt(self, task_id: str) -> int:
        records = self.attempts(task_id)
        if records and (
            records[-1].get("blocked") is True
            or records[-1].get("status") == "PASS"
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
            not isinstance(attempt, int)
            or isinstance(attempt, bool)
            or not 1 <= attempt <= MAX_EXECUTIONS
            or head != run_plan.get("head")
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


def _git_text(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=git_environment(),
        timeout=10,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise ValueError("E_RUN_GIT: required Git fact is unavailable")
    return value


def _validate_route_for_run(
    task: Mapping[str, Any], decision: Mapping[str, Any]
) -> None:
    semantic = {
        key: value
        for key, value in decision.items()
        if key not in {"command", "decision_digest"}
    }
    facts = decision.get("facts", {})
    if (
        decision.get("decision_digest") != contract_digest(semantic)
        or decision.get("task_id") != task.get("task_id")
        or not isinstance(facts, Mapping)
        or facts.get("task_digest") != contract_digest(task)
        or decision.get("decision_ready") is not True
        or decision.get("authorization", {}).get("local_write") is not True
        or decision.get("approval_boundaries") not in ([], ())
        or decision.get("errors") not in ([], ())
    ):
        raise ValueError(
            "E_RUN_AUTHORITY: current route does not permit the requested local run"
        )


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

    if validate_task_envelope(task) or task.get("requested_outcome") != "local_change":
        raise ValueError("E_RUN_TASK: local_change TaskEnvelope v1 required")
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
        outcome="local_change",
        branch=branch,
        task_digest=str(plan["task_digest"]),
        decision_digest=str(plan["decision_digest"]),
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
    return {"run_plan": plan, "task": state, "lease": lease}


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


_LOCAL_GATE_IDS = (
    "gate.relevant-tests",
    "gate.policy-check",
    "gate.registry-check",
    "gate.doctor",
    "gate.diff-review",
)


def _changed_paths(repository: Path) -> tuple[str, ...]:
    values: set[str] = set()
    commands = (
        ("diff", "--name-only", "-z", "HEAD", "--"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    for arguments in commands:
        completed = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_environment(),
            timeout=10,
        )
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


def _local_gate_commands(repository: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    launcher = str((repository / "scripts" / "control-plane").resolve())
    policy = str((repository / ".codex" / "project-policy.toml").resolve())
    registry = str((repository / ".codex" / "resource-registry.toml").resolve())
    return (
        (
            "gate.relevant-tests",
            (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"),
        ),
        (
            "gate.policy-check",
            (launcher, "policy-check", "--policy", policy, "--json"),
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
        ),
        ("gate.doctor", (launcher, "doctor", "--repo", str(repository), "--json")),
        ("gate.diff-review", ("git", "-C", str(repository), "diff", "--check")),
    )


def _snapshot(repository: Path) -> tuple[str, bool]:
    try:
        from control_plane.lifecycle import _verification_snapshot

        return _verification_snapshot(repository), True
    except (OSError, ValueError):
        return contract_digest({"snapshot": "UNKNOWN"}), False


def _closed_gate_environment(temp_root: Path) -> dict[str, str]:
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
    attempt: int,
    gate_id: str,
    argv: tuple[str, ...],
    observed_at: str,
) -> dict[str, Any]:
    if gate_id not in _LOCAL_GATE_IDS:
        raise ValueError("E_RUN_GATE: gate is outside the closed local profile")
    before, before_ok = _snapshot(repository)
    temp_root = state_dir / "codex-control-plane" / "run-temp" / str(run_plan["task_id"])
    for name in ("home", "tmp", "cache", "pycache"):
        (temp_root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    output = bytearray()
    output_truncated = False
    timed_out = False
    returncode = -1
    execution_ok = True
    try:
        process = subprocess.Popen(
            argv,
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_closed_gate_environment(temp_root),
            shell=False,
            start_new_session=True,
        )
        if process.stdout is None:
            raise OSError("child output unavailable")
        os.set_blocking(process.stdout.fileno(), False)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + _GATE_TIMEOUT_SECONDS
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
                for key, _ in selector.select(0.05 if process.poll() is None else 0):
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
                    output_truncated = output_truncated or len(chunk) > available
                if timed_out and process.poll() is not None and not stream_open:
                    break
            returncode = process.wait(timeout=1)
        finally:
            selector.close()
            process.stdout.close()
    except (OSError, subprocess.SubprocessError):
        execution_ok = False
    after, after_ok = _snapshot(repository)
    immutable = before_ok and after_ok and before == after
    current_head = ""
    try:
        current_head = _git_text(repository, "rev-parse", "HEAD")
    except ValueError:
        execution_ok = False
    gate_code = gate_id.removeprefix("gate.").replace("-", "_").replace(".", "_").upper()
    if not execution_ok or timed_out or output_truncated or not immutable or current_head != run_plan["head"]:
        status = "UNKNOWN"
        error_code = f"E_RUN_GATE_{gate_code}_UNKNOWN"
    elif returncode != 0:
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
        command_digest=contract_digest({"argv": list(argv)}),
        output_digest=contract_digest(
            {
                "bytes_hex": bytes(output).hex(),
                "truncated": output_truncated,
                "timed_out": timed_out,
                "returncode": returncode,
            }
        ),
        before_snapshot_digest=before,
        after_snapshot_digest=after,
        error_code=error_code,
        observed_at=observed_at,
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
    if Path(str(plan["repository"])).resolve() != root:
        raise ValueError("E_RUN_BINDING: repository changed")
    branch = _git_text(root, "branch", "--show-current")
    head = _git_text(root, "rev-parse", "HEAD")
    if branch != plan["branch"] or head != plan["head"]:
        raise ValueError("E_RUN_DRIFT: branch or HEAD changed")
    materialization = inspect_tracked_materialization(root)
    if materialization.status != "PASS":
        raise ValueError("E_RUN_UNKNOWN: materialization is not proven")
    policy = load_policy(root / ".codex" / "project-policy.toml")
    changed_paths = _changed_paths(root)
    TaskLease.validate(
        state_dir,
        task_id=task_id,
        worktree=str(root),
        branch=branch,
        session_id=str(plan["session_id"]),
        policy_digest=contract_digest(policy),
        changed_paths=list(changed_paths),
    )
    task_store = TaskStore(state_dir)
    state = task_store.status(task_id)
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
    if not changed_paths:
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
        for gate_id, argv in _local_gate_commands(root):
            receipt = _execute_closed_gate(
                repository=root,
                state_dir=state_dir,
                run_plan=plan,
                attempt=attempt_number,
                gate_id=gate_id,
                argv=argv,
                observed_at=observed_at,
            )
            receipts.append(receipt)
            if receipt["status"] != "PASS":
                break
    failure_reason = next(
        (str(item["error_code"]) for item in receipts if item["status"] != "PASS"),
        None,
    )
    attempt_record = run_store.record_attempt(
        run_plan=plan,
        attempt=attempt_number,
        head=head,
        changed_paths=changed_paths,
        receipts=tuple(receipts),
        failure_reason_code=failure_reason,
        observed_at=observed_at,
    )
    if attempt_record["status"] == "PASS":
        state = task_store.transition(
            task_id,
            "review_ready",
            evidence={
                "gates_ok": True,
                "documentation_decision": contract_digest(
                    {"run_plan": plan["plan_digest"], "gates": list(_LOCAL_GATE_IDS)}
                ),
            },
            current_branch=branch,
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
    return {
        "run_plan": plan,
        "task": state,
        "attempt": attempt_record,
        "receipts": receipts,
        "summary": summary,
    }
