from __future__ import annotations

import copy
import json
import unittest

from control_plane.contracts import contract_digest
from tests.core_router_test_support import (
    VALID_POLICY,
    VALID_REGISTRY,
    inventory_snapshot,
    task_envelope,
    validated_inventory,
)


class CoreRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from control_plane.policy import load_policy
        from control_plane.resource_registry import load_registry

        cls.policy = load_policy(VALID_POLICY)
        cls.registry = load_registry(VALID_REGISTRY)

    def route(self, task: dict[str, object]):
        from control_plane.routing import resolve_route

        return resolve_route(
            task,
            self.policy,
            self.registry,
            validated_inventory(
                inventory_snapshot(),
                registry=self.registry,
                task=task,
            ),
            mode="audit",
        )

    def test_local_answer_route_is_sealed_bounded_and_non_authorizing(self) -> None:
        from control_plane.core_types import TrustedRouteDecision
        from control_plane.routing import compact_route_manifest

        task = task_envelope(
            task_id="route-answer",
            intent="explain",
            phase="frame",
            requested_outcome="answer",
            signals=[],
            risk={
                "uncertainty": 0,
                "blast_radius": 0,
                "irreversibility": 0,
                "verification_complexity": 0,
            },
            effects=[{"name": "local_read", "source": "model_inference"}],
        )
        decision = self.route(task)
        self.assertIs(type(decision), TrustedRouteDecision)
        self.assertTrue(decision["decision_ready"])
        self.assertFalse(decision["authorizes"])
        self.assertEqual(decision["summary"]["tier"], "T0")
        manifest = json.loads(compact_route_manifest(decision))
        self.assertEqual(manifest["task_digest"], contract_digest(task))
        self.assertLessEqual(len(compact_route_manifest(decision).encode()), 4096)

    def test_remote_effect_is_deferred_and_never_minted_by_serialized_input(self) -> None:
        task = task_envelope(
            task_id="route-pr",
            phase="integrate",
            intent="integrate",
            requested_outcome="pull_request",
            effects=[
                {"name": "local_read", "source": "model_inference"},
                {"name": "remote_write", "source": "user_explicit"},
            ],
        )
        decision = self.route(task)
        self.assertFalse(decision["authorizes"])
        self.assertFalse(decision["authorization"]["remote_write"])
        self.assertIn("remote_write", decision["approval_boundaries"])
        self.assertFalse(decision["decision_ready"])

    def test_route_verify_detects_serialized_drift_diagnostically(self) -> None:
        from control_plane.routing import verify_route

        task = task_envelope(
            task_id="route-verify-core",
            intent="explain",
            phase="frame",
            requested_outcome="answer",
            signals=[],
            risk={
                "uncertainty": 0,
                "blast_radius": 0,
                "irreversibility": 0,
                "verification_complexity": 0,
            },
            effects=[{"name": "local_read", "source": "model_inference"}],
        )
        decision = self.route(task).payload
        receipt = {
            "schema_version": 1,
            "task_id": task["task_id"],
            "decision_digest": decision["decision_digest"],
            **decision["facts"],
            "used": [],
            "omitted": [],
            "gate_results": [],
            "observed_effects": [],
        }
        receipt["receipt_digest"] = contract_digest(receipt)
        result = verify_route(decision, receipt, mode="enforce")
        self.assertFalse(result["compliant"])
        self.assertFalse(result["authoritative"])
        self.assertTrue(result["errors"])

        drifted = copy.deepcopy(decision)
        drifted["facts"]["task_digest"] = contract_digest({"other": True})
        result = verify_route(drifted, receipt, mode="audit")
        self.assertTrue(result["ok"])
        self.assertFalse(result["compliant"])


if __name__ == "__main__":
    unittest.main()
