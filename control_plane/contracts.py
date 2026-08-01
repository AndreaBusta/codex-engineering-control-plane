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
