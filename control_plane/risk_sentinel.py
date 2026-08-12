"""Small tri-state local risk sentinel; remote truth is always deferred."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from control_plane.contracts import contract_digest
from control_plane.git_state import evaluate_preflight
from control_plane.intake import render_interaction_recommendation
from control_plane.lockfile import validate_lock
from control_plane.materialization import inspect_tracked_materialization
from control_plane.policy import GoverningPolicy, _governing_policy_snapshot, load_policy, validate_policy
from control_plane.project_profiles import detect_project_profile
from control_plane.repository import discover_repository


PASS = "PASS"
UNKNOWN = "UNKNOWN"
FAIL = "FAIL"
_RANK = {PASS: 0, UNKNOWN: 1, FAIL: 2}


def aggregate_status(values: Iterable[str]) -> str:
    result = PASS
    for value in values:
        if value not in _RANK:
            raise ValueError("E_RISK_STATUS: unsupported tri-state value")
        if _RANK[value] > _RANK[result]:
            result = value
    return result


@dataclass(frozen=True)
class RiskCheck:
    code: str
    status: str
    message: str
    facts: dict[str, Any]


@dataclass(frozen=True)
class RiskDimension:
    status: str
    checks: tuple[RiskCheck, ...]
    errors: tuple[dict[str, str], ...]

    def __post_init__(self) -> None:
        expected = aggregate_status(check.status for check in self.checks)
        if self.errors and expected == PASS:
            expected = UNKNOWN
        if self.status != expected:
            raise ValueError("E_RISK_STATUS: dimension aggregate is inconsistent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class RiskStatus:
    command: str
    dimensions: dict[str, RiskDimension]
    facts: dict[str, Any]
    errors: tuple[dict[str, str], ...]

    @property
    def status(self) -> str:
        return aggregate_status(dimension.status for dimension in self.dimensions.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "command": self.command,
            "ok": self.status == PASS,
            "status": self.status,
            "dimensions": {
                name: dimension.to_dict() for name, dimension in self.dimensions.items()
            },
            "facts": self.facts,
            "errors": list(self.errors),
            "authorizes": False,
        }


def _check(code: str, status: str, message: str, **facts: Any) -> RiskCheck:
    return RiskCheck(code=code, status=status, message=message, facts=facts)


def _interaction_view(decision: Mapping[str, Any] | None) -> dict[str, Any]:
    from control_plane.core_types import TrustedRouteDecision

    if type(decision) is TrustedRouteDecision:
        decision = decision._payload_for_diagnostic()
    try:
        interaction = decision.get("interaction", {}) if isinstance(decision, Mapping) else {}
        mode = interaction.get("recommended_mode", "normal")
        if mode == "default":
            mode = "normal"
        reasons = interaction.get("reason_codes", ["MODE_BOUNDED"])
        return render_interaction_recommendation(
            str(mode),
            list(reasons) if isinstance(reasons, list) else ["MODE_BOUNDED"],
        ).as_dict()
    except ValueError:
        return render_interaction_recommendation("normal", ["MODE_BOUNDED"]).as_dict()


def _policy_observation(repo: Path, policy: GoverningPolicy | None) -> tuple[dict[str, Any] | None, RiskCheck]:
    if policy is not None:
        snapshot = _governing_policy_snapshot(policy)
        candidate = snapshot.get("policy") if snapshot is not None else None
        if isinstance(candidate, dict) and not validate_policy(candidate):
            return candidate, _check(
                "RS_LOCAL_POLICY",
                PASS,
                "Installed governing policy is locally attested.",
                policy_digest=snapshot.get("policy_digest"),
            )
        return None, _check(
            "RS_LOCAL_POLICY",
            FAIL,
            "Supplied governing policy is untrusted or invalid.",
        )
    try:
        candidate = load_policy(repo / ".codex" / "project-policy.toml")
    except Exception:
        return None, _check(
            "RS_LOCAL_POLICY",
            UNKNOWN,
            "No locally parseable candidate policy was observed.",
        )
    if validate_policy(candidate):
        return candidate, _check(
            "RS_LOCAL_POLICY",
            FAIL,
            "Candidate policy is invalid and cannot govern.",
        )
    return candidate, _check(
        "RS_LOCAL_POLICY",
        UNKNOWN,
        "Candidate policy is valid but is not an installed governing observation.",
        policy_digest=contract_digest(candidate),
    )


def evaluate_local_risk(
    repo: Path | str,
    policy: GoverningPolicy | None,
    *,
    task_state: Mapping[str, Any] | None = None,
    route_decision_hint: Mapping[str, Any] | None = None,
    local_lease_session_id: str | None = None,
) -> RiskDimension:
    del route_decision_hint
    repository = discover_repository(Path(repo))
    policy_mapping, policy_check = _policy_observation(repository, policy)
    checks: list[RiskCheck] = [policy_check]
    if policy_mapping is None:
        checks.append(_check("RS_LOCAL_GIT", UNKNOWN, "Write preflight lacks a valid policy."))
    else:
        result = evaluate_preflight(repository, policy_mapping, "write")
        concrete_errors = [error.code for error in result.errors]
        dirty_only = concrete_errors == ["E_GIT_DIRTY"]
        lease_valid = False
        if dirty_only and task_state is not None and local_lease_session_id:
            try:
                from control_plane.task_state import validate_writer_continuation

                observation = validate_writer_continuation(
                    repository,
                    task_id=str(task_state.get("task_id")),
                    worktree=str(repository),
                    branch=str(result.facts.get("branch")),
                    session_id=local_lease_session_id,
                    policy_digest=contract_digest(policy_mapping),
                    changed_paths=tuple(result.facts.get("changed_paths", ())),
                )
                task_state = observation["task"]
                lease_valid = True
            except ValueError:
                lease_valid = False
        if result.ok:
            git_status = PASS if policy_check.status == PASS else UNKNOWN
            message = "Local Git write facts pass under the observed policy."
        elif dirty_only and lease_valid:
            git_status = UNKNOWN
            message = "A local lease explains the dirty tree but cannot grant authority."
        else:
            git_status = FAIL
            message = "Local Git write facts contain a demonstrated failure."
        checks.append(
            _check(
                "RS_LOCAL_GIT",
                git_status,
                message,
                error_codes=concrete_errors,
                branch=result.facts.get("branch"),
                dirty=result.facts.get("dirty"),
                lease_validated=lease_valid,
            )
        )
    lock_issues = validate_lock(repository)
    checks.append(
        _check(
            "RS_RUNTIME_LOCK",
            PASS if not lock_issues else FAIL,
            "Runtime lock is exact." if not lock_issues else "Runtime lock has drift.",
            issue_codes=[issue.code for issue in lock_issues],
        )
    )
    materialization = inspect_tracked_materialization(repository)
    checks.append(
        _check(
            "RS_MATERIALIZATION",
            PASS if materialization.ok else UNKNOWN,
            "Tracked files are materialized." if materialization.ok else "Tracked materialization is not proven.",
            materialization_status=materialization.status,
            dataless_paths=len(materialization.dataless_paths),
        )
    )
    profile = detect_project_profile(repository)
    checks.append(
        _check(
            "RS_PROFILE",
            PASS,
            "Project profile was observed locally.",
            **profile,
        )
    )
    if task_state is None:
        checks.append(_check("RS_TASK", PASS, "No task authority is applicable to this observation."))
    elif task_state.get("kind") == "CoreTaskStateV1" and task_state.get("worktree") == str(repository):
        checks.append(_check("RS_TASK", UNKNOWN, "Core task is locally bound but non-authorizing."))
    else:
        checks.append(_check("RS_TASK", FAIL, "Task state is foreign or invalid."))
    return RiskDimension(
        status=aggregate_status(check.status for check in checks),
        checks=tuple(checks),
        errors=(),
    )


def evaluate_risk_status(
    repo: Path | str,
    policy: GoverningPolicy | None = None,
    *,
    task_state: Mapping[str, Any] | None = None,
    route_decision_hint: Mapping[str, Any] | None = None,
    local_lease_session_id: str | None = None,
) -> RiskStatus:
    local = evaluate_local_risk(
        repo,
        policy,
        task_state=task_state,
        route_decision_hint=route_decision_hint,
        local_lease_session_id=local_lease_session_id,
    )
    remote = RiskDimension(
        status=UNKNOWN,
        checks=(),
        errors=(
            {
                "code": "RS_REMOTE_NOT_OBSERVED",
                "message": "Remote protection and provenance are outside Core observation.",
            },
        ),
    )
    return RiskStatus(
        command="risk-status",
        dimensions={"local": local, "remote": remote},
        facts={
            "interaction": _interaction_view(route_decision_hint),
            "automatic_change": False,
            "remote_capability": "quarantined",
        },
        errors=(),
    )
