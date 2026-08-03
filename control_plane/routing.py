"""Pure resource routing, authorization boundaries, and receipt verification."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from control_plane.contracts import (
    RESOURCE_ID,
    SHA256_DIGEST,
    TASK_EFFECTS,
    contract_digest,
    validate_task_id,
    validate_task_envelope,
)
from control_plane.clarification import evaluate_clarification_gate
from control_plane.host_bridge import (
    HOST_ADAPTER_UNAVAILABLE,
    HostAdapterCapability,
    HostAdapterUnavailable,
    TrustedAuthorization,
    TrustedRouteDecision,
    ValidatedInventory,
    _host_adapter_capability_is_live,
    _seal_trusted_route_decision,
    authorization_effects_for_route,
)
from control_plane.resource_registry import (
    registry_contract_digest,
    validate_inventory,
)


CRITICAL_SIGNALS = frozenset(
    {
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
        "security",
        "privacy",
        "data_loss",
        "irreversible",
    }
)
EXTERNAL_EFFECTS = frozenset(
    {
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
OUTCOME_RANK = {
    "answer": 0,
    "local_change": 1,
    "commit": 2,
    "pull_request": 3,
    "integration": 4,
    "release": 5,
}
CANONICAL_OUTCOME_GATES = {
    "pull_request": "gate.pull-request",
    "release": "gate.release-proof",
}
EFFECT_MINIMUM_OUTCOME = {
    "network_read": 0,
    "local_write": 1,
    "commit": 2,
    "remote_write": 3,
    "pull_request": 3,
    "integration": 4,
    "release": 5,
    "deploy": 5,
    "publish": 5,
}
DOCUMENTS = (
    "plan",
    "adr",
    "issue",
    "architecture",
    "runbook",
    "threat_model",
    "rollback",
    "release_notes",
    "release_receipt",
)
TECHNICAL_PROFILES = frozenset(
    {
        "generic",
        "ios",
        "android",
        "web_pwa",
        "saas_backend",
        "ai_text_pipeline",
    }
)
CONTEXT_UNITS = {
    "none": 0,
    "tiny": 1,
    "small": 2,
    "medium": 4,
    "large": 8,
}


def _tier(task: Mapping[str, Any]) -> str:
    signals = {str(item) for item in task.get("signals", [])}
    domains = {str(item) for item in task.get("domains", [])}
    effect_names = {
        str(item.get("name"))
        for item in task.get("effects", [])
        if isinstance(item, Mapping)
    }
    if (
        task.get("intent") == "release"
        or task.get("requested_outcome") == "release"
        or signals & CRITICAL_SIGNALS
        or domains & CRITICAL_SIGNALS
        or effect_names
        & {
        "release",
        "destructive",
        "credential_access",
        }
    ):
        return "T3"
    risk = task.get("risk", {})
    score = sum(
        max(0, min(3, int(risk.get(axis, 0))))
        for axis in (
            "uncertainty",
            "blast_radius",
            "irreversibility",
            "verification_complexity",
        )
    )
    if score == 0 and not signals:
        return "T0"
    if score <= 3 and not signals.intersection(
        {"multi_file", "regression_risk", "architecture_change"}
    ):
        return "T1"
    if score <= 8:
        return "T2"
    return "T3"


def _matches(
    route: Mapping[str, Any],
    task: Mapping[str, Any],
    tier: str,
    detected_domains: set[str],
) -> bool:
    signals = set(task.get("signals", []))
    declared_domains = set(task.get("domains", []))
    domains = (
        detected_domains
        if str(route.get("id", "")).startswith("quality-profile-")
        else declared_domains.union(detected_domains)
    )
    effects = {
        item.get("name")
        for item in task.get("effects", [])
        if isinstance(item, Mapping)
    }
    if tier not in route.get("tiers", []):
        return False
    if task.get("phase") not in route.get("phases", []):
        return False
    if task.get("intent") not in route.get("intents", []):
        return False
    filters = (
        ("domains_any", domains),
        ("signals_any", signals),
        ("effects_any", effects),
    )
    for name, actual in filters:
        expected = set(route.get(name, []))
        if expected and not expected.intersection(actual):
            return False
    if not set(route.get("signals_all", [])).issubset(signals):
        return False
    return True


def _select_capability(
    capability: str,
    resources: list[Mapping[str, Any]],
    errors: list[dict[str, str]],
) -> str | None:
    providers = [
        item for item in resources if capability in item.get("capabilities", [])
    ]
    if not providers:
        errors.append(
            {
                "code": "E_RESOURCE_UNRESOLVED",
                "message": f"No resource provides capability {capability}.",
            }
        )
        return None
    canonical = [item for item in providers if item.get("canonical") is True]
    project_canonical = [
        item
        for item in canonical
        if item.get("scope") == "project" and item.get("authority") == "project"
    ]
    if len(project_canonical) == 1:
        return str(project_canonical[0]["id"])
    candidates = canonical or providers
    if len(candidates) != 1:
        errors.append(
            {
                "code": "E_RESOURCE_AMBIGUOUS",
                "message": f"Capability {capability} has no unique canonical resource.",
            }
        )
        return None
    return str(candidates[0]["id"])


def _documentation(
    task: Mapping[str, Any], tier: str
) -> dict[str, dict[str, Any]]:
    signals = set(task.get("signals", []))
    intent = str(task.get("intent", ""))
    outcome = str(task.get("requested_outcome", ""))
    critical = bool(signals & CRITICAL_SIGNALS)
    decisions = {
        name: {"required": False, "reason_code": "DOC_NOT_TRIGGERED"}
        for name in DOCUMENTS
    }

    def require(name: str, reason: str) -> None:
        decisions[name] = {"required": True, "reason_code": reason}

    if tier in {"T2", "T3"} or intent == "plan":
        require("plan", "DOC_COMPLEXITY")
    if "architecture_change" in signals:
        require("adr", "DOC_DURABLE_DECISION")
        require("architecture", "DOC_ARCHITECTURE_CHANGE")
    if critical:
        require("threat_model", "DOC_CRITICAL_RISK")
        require("rollback", "DOC_CRITICAL_ROLLBACK")
    if "migration" in signals:
        require("adr", "DOC_CRITICAL_DECISION")
        require("architecture", "DOC_CRITICAL_ARCHITECTURE")
        require("runbook", "DOC_CRITICAL_OPERATION")
    if signals.intersection({"production", "release", "testflight"}) or outcome == "release":
        require("runbook", "DOC_OPERATIONAL_CHANGE")
        require("release_notes", "DOC_RELEASE")
        require("release_receipt", "DOC_RELEASE_EVIDENCE")
    if "follow_up" in signals:
        require("issue", "DOC_FOLLOW_UP")
    return decisions


def _interaction_mode(
    task: Mapping[str, Any], tier: str, prompt_multifront: bool
) -> dict[str, Any]:
    """Recommend a Codex interaction mode without changing the UI state."""

    signals = set(task.get("signals", []))
    risk = task.get("risk", {})
    unclear = (
        "unclear_outcome" in signals
        or int(risk.get("uncertainty", 0)) >= 3
    )
    long_running = bool(
        signals.intersection(
            {"long_running", "multiple_milestones"}
        )
    ) or len(task.get("goals", [])) >= 3
    needs_plan = (
        tier in {"T2", "T3"}
        and (
            int(risk.get("uncertainty", 0)) >= 2
            or prompt_multifront
            or bool(
                signals.intersection(
                    {
                        "architecture_change",
                        "migration",
                        "auth",
                        "payments",
                        "private_data",
                        "production",
                        "release",
                        "testflight",
                        "cross_system",
                    }
                )
            )
        )
    )
    if long_running and (unclear or needs_plan):
        mode = "plan_then_goal"
        action = "Use /plan to refine measurable completion criteria, then /goal."
        reasons = ["MODE_LONG_RUNNING", "MODE_REQUIRES_PLAN"]
    elif long_running:
        mode = "goal"
        action = "Use /goal with outcome, constraints, and verification."
        reasons = ["MODE_LONG_RUNNING"]
    elif needs_plan or unclear:
        mode = "plan"
        action = "Use /plan before implementation."
        reasons = ["MODE_COMPLEX_OR_UNCERTAIN"]
    else:
        mode = "default"
        action = "Continue in the current task mode."
        reasons = ["MODE_BOUNDED"]
    return {
        "recommended_mode": mode,
        "reason_codes": reasons,
        "user_action": action,
        "automatic_change": False,
        "confidence": "high" if signals or tier in {"T0", "T3"} else "medium",
    }


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def resolve_route(
    task: Mapping[str, Any],
    policy: Mapping[str, Any],
    registry: Mapping[str, Any],
    inventory: ValidatedInventory,
    *,
    mode: str,
    host_capability: HostAdapterCapability | HostAdapterUnavailable = (
        HOST_ADAPTER_UNAVAILABLE
    ),
    clarification_request: Mapping[str, Any] | None = None,
    authorization: TrustedAuthorization | None = None,
) -> TrustedRouteDecision:
    """Resolve a pre-framed task without I/O, execution, or interpretation."""

    if mode not in {"audit", "enforce"}:
        raise ValueError("mode must be audit or enforce")
    if not isinstance(task, Mapping):
        raise ValueError(
            "T_TASK_ENVELOPE: resolver requires a TaskEnvelope mapping"
        )
    task_issues = validate_task_envelope(task)
    if task_issues:
        issue = task_issues[0]
        raise ValueError(
            f"{issue.code}: {issue.path}: {issue.message}"
        )
    task_digest = contract_digest(task)
    if not isinstance(inventory, ValidatedInventory):
        raise ValueError(
            "E_INVENTORY_OBSERVATION: resolver requires ValidatedInventory"
        )
    host_capability_ready = _host_adapter_capability_is_live(
        host_capability
    )
    if not host_capability_ready and (
        host_capability is not HOST_ADAPTER_UNAVAILABLE
    ):
        raise ValueError(
            "C_UNTRUSTED_HOST_CAPABILITY: typed host state is required"
        )
    inventory_snapshot = inventory._snapshot_for_router(
        expected_task_digest=task_digest,
        expected_registry_digest=registry_contract_digest(registry),
    )
    inventory = inventory_snapshot
    tier = _tier(task)
    workflow_mode = {"T0": "direct", "T1": "direct", "T2": "structured", "T3": "controlled"}[tier]
    resources = sorted(
        (
            item
            for item in registry.get("resources", [])
            if isinstance(item, Mapping)
        ),
        key=lambda item: str(item.get("id", "")),
    )
    resource_by_id = {str(item.get("id")): item for item in resources}
    inventory_by_id = {
        str(item.get("id")): item
        for item in inventory.get("resources", [])
        if isinstance(item, Mapping)
    }
    inventory_issues = validate_inventory(registry, inventory)
    errors: list[dict[str, str]] = [
        {
            "code": issue.code,
            "message": f"{issue.path}: {issue.message}".lstrip(": "),
        }
        for issue in inventory_issues
    ]
    granted_effects: set[str] = set()
    if authorization is not None:
        granted_effects = authorization_effects_for_route(
            authorization,
            expected_task_digest=task_digest,
            expected_scope_paths=tuple(
                str(item)
                for item in task.get("scope_paths", [])
                if isinstance(item, str)
            ),
        )
    matched_routes = sorted(
        (
            route
            for route in registry.get("routes", [])
            if isinstance(route, Mapping)
            and _matches(
                route,
                task,
                tier,
                {
                    str(item)
                    for item in inventory.get("project_profile", {}).get(
                        "profiles", []
                    )
                },
            )
        ),
        key=lambda item: (-int(item.get("priority", 0)), str(item.get("id", ""))),
    )
    required: list[str] = [
        str(item["id"])
        for item in resources
        if item.get("selection") == "required"
    ]
    recommended: list[str] = []
    forbidden: list[str] = list(task.get("excluded_resources", []))
    for route in matched_routes:
        for capability in sorted(route.get("required_capabilities", [])):
            selected = _select_capability(capability, resources, errors)
            if selected:
                required.append(selected)
        recommended.extend(str(item) for item in route.get("recommended_resources", []))
        forbidden.extend(str(item) for item in route.get("forbidden_resources", []))
    required.extend(str(item) for item in task.get("explicit_resources", []))

    outcome_rank = OUTCOME_RANK.get(str(task.get("requested_outcome")), -1)
    gate_aliases: dict[str, str] = {}
    for resource in resources:
        if resource.get("kind") == "gate":
            for alias in resource.get("aliases", []):
                gate_aliases[str(alias)] = str(resource["id"])
    outcome_scoped_gate_aliases = {
        "pull_request": OUTCOME_RANK["pull_request"],
        "release_proof": OUTCOME_RANK["release"],
    }
    required_gates = _unique_sorted(
        gate_aliases.get(str(gate), f"unresolved:{gate}")
        for gate in policy.get("gates", {}).get(tier, {}).get("required", [])
        if outcome_rank >= outcome_scoped_gate_aliases.get(str(gate), 0)
    )
    if outcome_rank >= OUTCOME_RANK["pull_request"] and policy.get(
        "git", {}
    ).get("require_pull_request"):
        required_gates.append(CANONICAL_OUTCOME_GATES["pull_request"])
    if outcome_rank >= OUTCOME_RANK["release"]:
        required_gates.append(CANONICAL_OUTCOME_GATES["release"])
    required_gates = _unique_sorted(required_gates)
    required.extend(
        gate for gate in required_gates if not gate.startswith("unresolved:")
    )

    required = _unique_sorted(required)
    pending = list(required)
    while pending:
        identifier = pending.pop()
        resource = resource_by_id.get(identifier)
        if resource is None:
            continue
        for dependency in resource.get("requires", []):
            dependency = str(dependency)
            if dependency not in required:
                required.append(dependency)
                pending.append(dependency)
    required = _unique_sorted(required)
    forbidden = _unique_sorted(forbidden)
    denied_required = sorted(set(required).intersection(forbidden))
    if denied_required:
        errors.append(
            {
                "code": "E_RESOURCE_FORBIDDEN",
                "message": (
                    "Registry/task denial dominates selection: "
                    + ", ".join(denied_required)
                ),
            }
        )
    for identifier in required:
        resource = resource_by_id.get(identifier)
        if resource is None:
            continue
        conflicts = set(resource.get("conflicts", []))
        selected_conflicts = conflicts.intersection(required)
        if selected_conflicts:
            errors.append(
                {
                    "code": "E_RESOURCE_CONFLICT",
                    "message": (
                        f"Resource {identifier} conflicts with "
                        + ", ".join(sorted(selected_conflicts))
                    ),
                }
            )
    recommended = [
        item
        for item in _unique_sorted(recommended)
        if item not in required and item not in forbidden
    ]
    budget = registry.get("budgets", {}).get(tier, {})
    max_recommended = int(budget.get("max_recommended", 0))
    max_context_units = int(budget.get("max_context_units", 0))
    required_context_units = sum(
        CONTEXT_UNITS.get(
            str(resource_by_id.get(identifier, {}).get("context_class", "large")),
            8,
        )
        for identifier in required
    )
    if required_context_units > max_context_units:
        errors.append(
            {
                "code": "E_CONTEXT_BUDGET_REQUIRED",
                "message": (
                    "Required resources exceed the tier context budget; "
                    "segment the task or raise policy explicitly."
                ),
            }
        )
    selected_recommended: list[str] = []
    deferred: list[str] = []
    context_units = required_context_units
    for identifier in recommended:
        units = CONTEXT_UNITS.get(
            str(resource_by_id.get(identifier, {}).get("context_class", "large")),
            8,
        )
        if (
            len(selected_recommended) < max_recommended
            and context_units + units <= max_context_units
        ):
            selected_recommended.append(identifier)
            context_units += units
        else:
            deferred.append(identifier)
    unresolved: list[str] = []
    inventory_entries: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in inventory.get("resources", []):
        if isinstance(item, Mapping):
            inventory_entries[str(item.get("id"))].append(item)
    for identifier, entries in sorted(inventory_entries.items()):
        if len(entries) > 1 and len(
            {str(item.get("locator_digest")) for item in entries}
        ) > 1:
            errors.append(
                {
                    "code": "E_RESOURCE_AMBIGUOUS",
                    "message": f"Inventory has conflicting digests for {identifier}.",
                }
            )
    for identifier in required:
        entry = inventory_by_id.get(identifier)
        if identifier not in resource_by_id or entry is None:
            unresolved.append(identifier)
        elif entry.get("ready") is not True:
            unresolved.append(identifier)

    effects = [
        item
        for item in task.get("effects", [])
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    ]
    authorization_effects: dict[str, bool] = {}
    approval_boundaries: list[str] = []
    for effect in sorted({str(item["name"]) for item in effects}):
        outcome_authorized = outcome_rank >= EFFECT_MINIMUM_OUTCOME.get(effect, 0)
        if effect in {"destructive", "credential_access"}:
            outcome_authorized = task.get("intent") == "operate"
        if effect == "local_write":
            authorized = outcome_authorized
        else:
            authorized = effect not in EXTERNAL_EFFECTS or (
                effect in granted_effects and outcome_authorized
            )
        authorization_effects[effect] = authorized
        if effect in EXTERNAL_EFFECTS.union({"local_write"}) and not authorized:
            approval_boundaries.append(effect)

    for identifier in required:
        resource = resource_by_id.get(identifier, {})
        resource_effects = {
            str(effect) for effect in resource.get("effects", [])
        }
        external_resource_effects = resource_effects.intersection(
            EXTERNAL_EFFECTS
        )
        missing_authority = sorted(
            effect
            for effect in external_resource_effects
            if not authorization_effects.get(effect, False)
        )
        if missing_authority:
            unresolved.append(identifier)
            approval_boundaries.append(f"resource:{identifier}")
            errors.append(
                {
                    "code": "E_RESOURCE_APPROVAL",
                    "message": (
                        f"External resource {identifier} lacks task-bound "
                        "authority for: " + ", ".join(missing_authority)
                    ),
                }
            )

    unresolved.extend(
        gate.removeprefix("unresolved:")
        for gate in required_gates
        if gate.startswith("unresolved:")
    )
    unresolved = _unique_sorted(unresolved)
    if unresolved:
        errors.append(
            {
                "code": "E_RESOURCE_NOT_READY",
                "message": "Required resources are not ready: " + ", ".join(unresolved),
            }
        )

    goals = [
        item for item in task.get("goals", []) if isinstance(item, Mapping)
    ]
    independent_goals = (
        len(goals) > 1
        and all(not item.get("depends_on") for item in goals)
        and len(
            {
                domain
                for item in goals
                for domain in item.get("domains", [])
                if isinstance(domain, str)
            }
        )
        > 1
    )
    prompt_multifront = independent_goals or "independent_work" in task.get(
        "signals", []
    )
    interaction = _interaction_mode(task, tier, prompt_multifront)
    clarification_gate = evaluate_clarification_gate(
        task,
        request=clarification_request,
    )
    interaction["clarification_gate"] = clarification_gate
    graph_candidate = (
        prompt_multifront and int(budget.get("max_agents", 0)) >= 2
    )
    detected_profiles = {
        str(item)
        for item in inventory.get("project_profile", {}).get("profiles", [])
    }
    declared_profiles = {
        str(item)
        for item in task.get("domains", [])
        if str(item) in TECHNICAL_PROFILES
    }
    profile_mismatch = sorted(declared_profiles - detected_profiles)
    facts = {
        "task_digest": task_digest,
        "policy_digest": contract_digest(policy),
        "registry_digest": registry_contract_digest(registry),
        "inventory_digest": (
            str(inventory.get("snapshot_digest"))
            if not inventory_issues
            else contract_digest(
                {
                    key: value
                    for key, value in inventory.items()
                    if key != "snapshot_digest"
                }
            )
        ),
    }
    summary = {
        "tier": tier,
        "workflow_mode": workflow_mode,
        "required": required,
        "recommended": selected_recommended,
        "deferred": deferred,
        "forbidden": forbidden,
        "shadowed": [],
        "unresolved": unresolved,
        "prompt_multifront": prompt_multifront,
        "execution_strategy": "sequential",
        "graph_candidate": graph_candidate,
        "graph_validation_required": graph_candidate,
        "max_agents": int(budget.get("max_agents", 0)),
        "max_context_units": int(budget.get("max_context_units", 0)),
        "selected_context_units": context_units,
        "project_profile": inventory.get(
            "project_profile",
            {
                "schema_version": 1,
                "kind": "unknown",
                "profiles": [],
                "evidence": [],
                "confidence": "not_observed",
                "truncated": False,
            },
        ),
        "profile_mismatch": profile_mismatch,
    }
    clarification_blocks_write = bool(
        clarification_gate.get("decision_ready") is not True
        and clarification_gate.get("blocked_effects")
    )
    if clarification_blocks_write:
        errors.append(
            {
                "code": "R_CLARIFICATION_PENDING",
                "message": (
                    "Material clarification is unresolved for requested "
                    "write effects."
                ),
            }
        )
    blocking = (
        bool(errors)
        or bool(approval_boundaries)
        or clarification_blocks_write
    )
    decision: dict[str, Any] = {
        "schema_version": 1,
        "task_id": task.get("task_id"),
        "mode": mode,
        "ok": not blocking if mode == "enforce" else True,
        "decision_ready": not blocking,
        "summary": summary,
        "documentation": _documentation(task, tier),
        "interaction": interaction,
        "approval_boundaries": sorted(approval_boundaries),
        "authorization": authorization_effects,
        "required_gates": required_gates,
        "selected_resource_digests": {
            identifier: str(inventory_by_id[identifier]["locator_digest"])
            for identifier in sorted(
                set(required).union(selected_recommended)
            )
            if identifier in inventory_by_id
            and isinstance(inventory_by_id[identifier].get("locator_digest"), str)
        },
        "matched_routes": [str(item.get("id")) for item in matched_routes],
        "facts": facts,
        "errors": errors,
    }
    decision["decision_digest"] = contract_digest(decision)
    return _seal_trusted_route_decision(decision)


def verify_route(
    decision: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    mode: str,
) -> dict[str, Any]:
    """Verify a serialized receipt diagnostically without host authority."""

    if mode not in {"audit", "enforce"}:
        raise ValueError("mode must be audit or enforce")
    errors: list[dict[str, str]] = []
    decision_required = {
        "schema_version",
        "task_id",
        "mode",
        "ok",
        "decision_ready",
        "summary",
        "documentation",
        "interaction",
        "approval_boundaries",
        "authorization",
        "required_gates",
        "selected_resource_digests",
        "matched_routes",
        "facts",
        "errors",
        "decision_digest",
    }
    decision_allowed = decision_required.union({"command"})
    if set(decision).difference(decision_allowed) or not decision_required.issubset(
        decision
    ):
        errors.append(
            {
                "code": "E_DECISION_SCHEMA",
                "message": "RouteDecision does not use the closed schema-1 fields.",
            }
        )
    if (
        decision.get("schema_version") != 1
        or not validate_task_id(decision.get("task_id"))
        or decision.get("mode") not in {"audit", "enforce"}
        or not isinstance(decision.get("ok"), bool)
        or not isinstance(decision.get("decision_ready"), bool)
        or not isinstance(decision.get("summary"), Mapping)
        or not isinstance(decision.get("facts"), Mapping)
        or not isinstance(decision.get("selected_resource_digests"), Mapping)
    ):
        errors.append(
            {
                "code": "E_DECISION_SCHEMA",
                "message": "RouteDecision has invalid required field types.",
            }
        )
    if (
        decision.get("decision_ready") is not True
        or decision.get("ok") is not True
    ):
        errors.append(
            {
                "code": "E_DECISION_NOT_READY",
                "message": (
                    "A receipt cannot certify a RouteDecision with pending "
                    "errors or approval boundaries."
                ),
            }
        )
    supplied_decision_digest = decision.get("decision_digest")
    decision_without_digest = {
        key: value
        for key, value in decision.items()
        if key not in {"decision_digest", "command"}
    }
    if (
        not isinstance(supplied_decision_digest, str)
        or SHA256_DIGEST.fullmatch(supplied_decision_digest) is None
        or supplied_decision_digest != contract_digest(decision_without_digest)
    ):
        errors.append(
            {
                "code": "E_DECISION_DIGEST",
                "message": "RouteDecision digest is absent or does not match content.",
            }
        )
    facts = decision.get("facts", {})
    if (
        not isinstance(facts, Mapping)
        or set(facts)
        != {
            "task_digest",
            "policy_digest",
            "registry_digest",
            "inventory_digest",
        }
        or any(
            not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
            for value in facts.values()
        )
    ):
        errors.append(
            {
                "code": "E_DECISION_FACTS",
                "message": "RouteDecision facts must contain four SHA-256 digests.",
            }
        )

    receipt_required = {
        "schema_version",
        "task_id",
        "decision_digest",
        "task_digest",
        "policy_digest",
        "registry_digest",
        "inventory_digest",
        "used",
        "omitted",
        "gate_results",
        "observed_effects",
        "receipt_digest",
    }
    if set(receipt) != receipt_required:
        errors.append(
            {
                "code": "E_RECEIPT_SCHEMA",
                "message": "ResourceUseReceipt does not use the closed schema-1 fields.",
            }
        )
    if receipt.get("schema_version") != 1 or not validate_task_id(
        receipt.get("task_id")
    ):
        errors.append(
            {
                "code": "E_RECEIPT_SCHEMA",
                "message": "Receipt schema version or task ID is invalid.",
            }
        )
    if receipt.get("task_id") != decision.get("task_id"):
        errors.append(
            {
                "code": "E_RECEIPT_TASK",
                "message": "Receipt task ID differs from RouteDecision.",
            }
        )
    supplied_receipt_digest = receipt.get("receipt_digest")
    receipt_without_digest = {
        key: value for key, value in receipt.items() if key != "receipt_digest"
    }
    if (
        not isinstance(supplied_receipt_digest, str)
        or SHA256_DIGEST.fullmatch(supplied_receipt_digest) is None
        or supplied_receipt_digest != contract_digest(receipt_without_digest)
    ):
        errors.append(
            {
                "code": "E_RECEIPT_DIGEST",
                "message": "Receipt digest is absent or does not match content.",
            }
        )
    for field in (
        "decision_digest",
        "task_digest",
        "policy_digest",
        "registry_digest",
        "inventory_digest",
    ):
        expected = (
            decision.get(field)
            if field == "decision_digest"
            else decision.get("facts", {}).get(field)
        )
        if receipt.get(field) != expected:
            errors.append(
                {"code": "E_RECEIPT_DIGEST", "message": f"Digest mismatch: {field}"}
            )
    raw_used = receipt.get("used")
    used_valid = isinstance(raw_used, list) and all(
        isinstance(item, Mapping)
        and set(item)
        == {"resource_id", "locator_digest", "evidence_digest"}
        and isinstance(item.get("resource_id"), str)
        and RESOURCE_ID.fullmatch(str(item.get("resource_id"))) is not None
        and isinstance(item.get("locator_digest"), str)
        and SHA256_DIGEST.fullmatch(str(item.get("locator_digest"))) is not None
        and isinstance(item.get("evidence_digest"), str)
        and SHA256_DIGEST.fullmatch(str(item.get("evidence_digest"))) is not None
        for item in (raw_used if isinstance(raw_used, list) else [])
    )
    if not used_valid:
        errors.append(
            {
                "code": "E_RECEIPT_SCHEMA",
                "message": (
                    "Used resources must bind resource_id, locator digest, "
                    "and observation digest."
                ),
            }
        )
    selected_resource_digests = decision.get("selected_resource_digests", {})
    if isinstance(raw_used, list):
        for item in raw_used:
            if not isinstance(item, Mapping):
                continue
            resource_id = str(item.get("resource_id"))
            locator_digest = item.get("locator_digest")
            expected_evidence = contract_digest(
                {
                    "decision_digest": decision.get("decision_digest"),
                    "resource_id": resource_id,
                    "locator_digest": locator_digest,
                }
            )
            if (
                not isinstance(selected_resource_digests, Mapping)
                or selected_resource_digests.get(resource_id) != locator_digest
                or item.get("evidence_digest") != expected_evidence
            ):
                errors.append(
                    {
                        "code": "E_RECEIPT_RESOURCE_EVIDENCE",
                        "message": (
                            f"Resource evidence is not bound to the selected "
                            f"inventory locator: {resource_id}"
                        ),
                    }
                )
    used = {
        str(item.get("resource_id"))
        for item in (raw_used if isinstance(raw_used, list) else [])
        if isinstance(item, Mapping)
    }
    omitted = receipt.get("omitted")
    if not isinstance(omitted, list) or not all(
        isinstance(item, str) and RESOURCE_ID.fullmatch(item) is not None
        for item in omitted
    ):
        errors.append(
            {
                "code": "E_RECEIPT_SCHEMA",
                "message": "Omitted resources must be stable resource IDs.",
            }
        )
    required = set(decision.get("summary", {}).get("required", []))
    forbidden = set(decision.get("summary", {}).get("forbidden", []))
    if not required.issubset(used):
        errors.append(
            {
                "code": "E_RECEIPT_REQUIRED",
                "message": "Required resources were not recorded as used.",
            }
        )
    if used.intersection(forbidden) or any(
        item.startswith("forbidden.") for item in used
    ):
        errors.append(
            {
                "code": "E_RECEIPT_FORBIDDEN",
                "message": "A forbidden resource was recorded as used.",
            }
        )
    raw_gates = receipt.get("gate_results")
    gates_valid = isinstance(raw_gates, list) and all(
        isinstance(item, Mapping)
        and set(item)
        == {
            "gate_id",
            "ok",
            "report_digest",
            "subject_digest",
            "evidence_digest",
        }
        and isinstance(item.get("gate_id"), str)
        and RESOURCE_ID.fullmatch(str(item.get("gate_id"))) is not None
        and isinstance(item.get("ok"), bool)
        and isinstance(item.get("report_digest"), str)
        and SHA256_DIGEST.fullmatch(str(item.get("report_digest"))) is not None
        and item.get("subject_digest") == decision.get("decision_digest")
        and isinstance(item.get("evidence_digest"), str)
        and item.get("evidence_digest")
        == contract_digest(
            {
                "gate_id": str(item.get("gate_id")),
                "ok": item.get("ok"),
                "report_digest": str(item.get("report_digest")),
                "subject_digest": item.get("subject_digest"),
            }
        )
        for item in (raw_gates if isinstance(raw_gates, list) else [])
    )
    if not gates_valid:
        errors.append(
            {
                "code": "E_RECEIPT_SCHEMA",
                "message": (
                    "Gate results must bind a report digest to this exact "
                    "RouteDecision."
                ),
            }
        )
    gate_results = {
        str(item.get("gate_id")): item.get("ok") is True
        for item in (raw_gates if isinstance(raw_gates, list) else [])
        if isinstance(item, Mapping)
    }
    if any(
        not gate_results.get(gate_id, False)
        for gate_id in decision.get("required_gates", [])
    ):
        errors.append(
            {
                "code": "E_RECEIPT_GATE",
                "message": "One or more required gates lack passing evidence.",
            }
        )
    observed_effects = receipt.get("observed_effects")
    if not isinstance(observed_effects, list) or not all(
        effect in TASK_EFFECTS for effect in observed_effects
    ):
        errors.append(
            {
                "code": "E_RECEIPT_SCHEMA",
                "message": "Observed effects must use the closed effect vocabulary.",
            }
        )
        observed_effects = []
    authorization = decision.get("authorization", {})
    for effect in observed_effects:
        if effect in EXTERNAL_EFFECTS.union({"local_write"}) and not authorization.get(
            effect, False
        ):
            errors.append(
                {
                    "code": "E_RECEIPT_EFFECT",
                    "message": f"Observed effect was not authorized: {effect}",
                }
            )
    return {
        "schema_version": 1,
        "command": "route-verify",
        "mode": mode,
        "ok": not errors if mode == "enforce" else True,
        "compliant": not errors,
        "authoritative": False,
        "status": "diagnostic",
        "errors": errors,
    }


def compact_route_manifest(decision: Mapping[str, Any]) -> str:
    """Render only the route facts Codex needs to rehydrate after compaction."""

    summary = decision.get("summary", {})
    project_profile = summary.get("project_profile", {})
    if isinstance(project_profile, Mapping):
        compact_profile = {
            key: project_profile.get(key)
            for key in (
                "schema_version",
                "kind",
                "profiles",
                "evidence",
                "confidence",
                "truncated",
            )
        }
        compact_profile["evidence"] = []
    else:
        compact_profile = None
    gate = decision.get("interaction", {}).get(
        "clarification_gate", {}
    )
    compact = {
        "schema_version": 1,
        "task_digest": decision.get("facts", {}).get("task_digest"),
        "decision_digest": decision.get("decision_digest"),
        "tier": summary.get("tier"),
        "workflow_mode": summary.get("workflow_mode"),
        "required": summary.get("required", []),
        "recommended": summary.get("recommended", []),
        "unresolved": summary.get("unresolved", []),
        "max_agents": summary.get("max_agents"),
        "execution_strategy": summary.get("execution_strategy"),
        "required_documents": sorted(
            name
            for name, result in decision.get("documentation", {}).items()
            if isinstance(result, Mapping) and result.get("required") is True
        ),
        "approval_boundaries": decision.get("approval_boundaries", []),
        "required_gates": decision.get("required_gates", []),
        "project_profile": compact_profile,
        "interaction": {
            key: value
            for key, value in decision.get("interaction", {}).items()
            if key != "clarification_gate"
        },
        "clarification": {
            "level": gate.get("level"),
            "status": gate.get("status"),
            "decision_ready": gate.get("decision_ready"),
            "reason_codes": gate.get("reason_codes", []),
        },
    }
    from control_plane.contracts import canonical_json, contract_digest

    compact["manifest_digest"] = contract_digest(compact)
    rendered = canonical_json(compact)
    if len(rendered.encode("utf-8")) > 4096:
        raise ValueError("E_CONTEXT_BUDGET: compact route manifest exceeds 4 KiB")
    return rendered
