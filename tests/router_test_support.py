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


def deep_copy(value: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(value)
