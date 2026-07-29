from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from control_plane.contracts import contract_digest

ROOT = Path(__file__).parents[1]
VALID_REGISTRY = ROOT / "tests" / "fixtures" / "valid-registry.toml"
VALID_POLICY = ROOT / "tests" / "fixtures" / "valid-policy.toml"


def task_envelope(**overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "task-router-001",
        "objective": "Implement a verified local change.",
        "intent": "implement",
        "phase": "implement",
        "requested_outcome": "local_change",
        "goals": [
            {
                "id": "goal-main",
                "summary": "Implement the requested behavior.",
                "domains": ["control-plane"],
                "depends_on": [],
            }
        ],
        "domains": ["control-plane"],
        "signals": ["multi_file", "regression_risk"],
        "scope_paths": ["control_plane/", "tests/"],
        "risk": {
            "uncertainty": 2,
            "blast_radius": 2,
            "irreversibility": 1,
            "verification_complexity": 2,
        },
        "effects": [
            {"name": "local_read", "source": "model_inference"},
            {"name": "local_write", "source": "user_explicit"},
        ],
        "explicit_resources": [],
        "excluded_resources": [],
    }
    task.update(overrides)
    return task


def inventory_snapshot(
    *,
    unavailable: tuple[str, ...] = (),
    unknown: tuple[str, ...] = (),
    ready_external: tuple[str, ...] = (),
    digest_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    digest_overrides = digest_overrides or {}
    resource_ids = (
        "instruction.project-agents",
        "document.operating-model",
        "skill.verified-workflow",
        "gate.targeted-validation",
        "gate.diff-review",
        "gate.relevant-tests",
        "gate.written-plan",
        "gate.independent-review",
        "gate.pull-request",
        "gate.security-review",
        "gate.rollback-plan",
        "gate.release-proof",
        "mcp.github-pr-read",
        "mcp.release-provider-evidence",
    )
    resources: list[dict[str, Any]] = []
    for resource_id in resource_ids:
        availability = "available"
        if resource_id in unavailable:
            availability = "unavailable"
        elif resource_id in unknown:
            availability = "unknown"
        is_external = resource_id.startswith("mcp.")
        external_ready = resource_id in ready_external
        discovered = availability == "available"
        trusted = not is_external or external_ready
        authenticated = (
            "authenticated"
            if is_external and external_ready
            else ("unknown" if is_external else "not_applicable")
        )
        healthy = (
            "healthy"
            if is_external and external_ready
            else availability
        )
        ready = bool(
            availability == "available"
            and discovered
            and trusted
            and authenticated in {"authenticated", "not_applicable"}
            and healthy in {"healthy", "available"}
        )
        resources.append(
            {
                "id": resource_id,
                "availability": availability,
                "discovered": discovered,
                "enabled": discovered,
                "trusted": trusted,
                "authenticated": authenticated,
                "healthy": healthy,
                "authorized_for_task": False,
                "ready": ready,
                "locator_digest": digest_overrides.get(
                    resource_id,
                    contract_digest({"resource_id": resource_id}),
                ),
                "size_bytes": 256,
                "reason_codes": (
                    [] if availability == "available" else ["r_not_ready"]
                ),
            }
        )
    snapshot = {
        "schema_version": 1,
        "source": "test-runtime",
        "observed_at": "2026-07-28T00:00:00Z",
        "project_profile": {
            "schema_version": 1,
            "kind": "generic",
            "profiles": ["generic"],
            "evidence": [],
            "confidence": "high",
            "truncated": False,
        },
        "resources": resources,
    }
    snapshot["snapshot_digest"] = contract_digest(snapshot)
    return snapshot


def refresh_inventory_digest(inventory: dict[str, Any]) -> None:
    inventory.pop("snapshot_digest", None)
    inventory["snapshot_digest"] = contract_digest(inventory)


def inventory_observation(
    snapshot: dict[str, Any],
    *,
    registry: dict[str, Any],
    task: dict[str, Any],
    invocation_id: str = "test-inventory-invocation",
    observed_at: float = 100.0,
    ttl_seconds: float = 30.0,
):
    import control_plane.host_bridge as bridge
    from control_plane.resource_registry import registry_contract_digest

    observation = object.__new__(bridge.InventoryObservation)
    observation._consumed = False
    observation.observation_id = f"test-{invocation_id}"
    observation.invocation_id = invocation_id
    observation.task_digest = contract_digest(task)
    observation.repository_identity = str(ROOT.resolve())
    observation.worktree_identity = str(ROOT.resolve())
    observation.registry_digest = registry_contract_digest(registry)
    observation.snapshot_digest = str(snapshot.get("snapshot_digest"))
    observation.snapshot = copy.deepcopy(snapshot)
    observation.observed_at_monotonic = observed_at
    observation.freshness_deadline = observed_at + ttl_seconds
    return observation


def validated_inventory(
    snapshot: dict[str, Any],
    *,
    registry: dict[str, Any],
    task: dict[str, Any],
    invocation_id: str = "test-inventory-invocation",
):
    from control_plane.host_bridge import validate_inventory_observation
    from control_plane.resource_registry import registry_contract_digest

    observation = inventory_observation(
        snapshot,
        registry=registry,
        task=task,
        invocation_id=invocation_id,
    )
    return validate_inventory_observation(
        observation,
        expected_repo=ROOT,
        expected_worktree=ROOT,
        expected_registry_digest=registry_contract_digest(registry),
        expected_task_digest=contract_digest(task),
        expected_invocation_id=invocation_id,
        clock=lambda: 100.0,
    )


def trusted_authorization(
    task: dict[str, Any],
    *,
    effect: str,
    invocation_id: str = "test-authorization-invocation",
):
    import control_plane.host_bridge as bridge
    from tests.host_adapter_test_support import (
        native_session_event,
        native_user_interaction_event,
    )

    session_id = "session-router-tests"
    subject_digest = contract_digest({"effect": effect, "task": task["task_id"]})
    session_event = native_session_event(
        event_id=f"session-event-{effect}",
        session_id=session_id,
        invocation_id=invocation_id,
        observed_at_monotonic=100.0,
    )
    capability = bridge.attest_host_adapter_capability(
        session_event,
        expected_session_id=session_id,
        expected_invocation_id=invocation_id,
        clock=lambda: 100.0,
        ttl_seconds=30,
    )
    user_event = native_user_interaction_event(
        event_id=f"user-event-{effect}",
        session_id=session_id,
        invocation_id=invocation_id,
        task_digest=contract_digest(task),
        subject_digest=subject_digest,
        observed_at_monotonic=100.0,
    )
    return bridge.frame_effect_authorization(
        user_event,
        host_capability=capability,
        task_digest=contract_digest(task),
        session_id=session_id,
        repository_identity=str(ROOT.resolve()),
        worktree_identity=str(ROOT.resolve()),
        branch="codex/test",
        expected_head="a" * 40,
        subject_digest=subject_digest,
        scope_paths=tuple(task["scope_paths"]),
        effect=effect,
        operation_nonce=f"operation-{effect}",
        invocation_id=invocation_id,
        clock=lambda: 100.0,
        ttl_seconds=30,
    )


def deep_copy(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)
