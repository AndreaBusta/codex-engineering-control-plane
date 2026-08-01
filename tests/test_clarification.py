from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from control_plane.contracts import contract_digest


class ClarificationTests(unittest.TestCase):
    digest = "sha256:" + "a" * 64

    def task(self, uncertainty: int) -> dict[str, object]:
        return {
            "schema_version": 1,
            "task_id": "TASK-LOCAL-AUDIT",
            "objective": "Apply one bounded local change.",
            "intent": "implement",
            "phase": "implement",
            "requested_outcome": "local_change",
            "goals": [],
            "domains": ["control-plane"],
            "signals": [],
            "scope_paths": ["control_plane/"],
            "risk": {
                "uncertainty": uncertainty,
                "blast_radius": 1,
                "irreversibility": 0,
                "verification_complexity": 1,
            },
            "effects": [
                {"name": "local_read", "source": "model_inference"},
                {"name": "local_write", "source": "user_explicit"},
            ],
            "explicit_resources": [],
            "excluded_resources": [],
        }

    def diagnostic_request(self, task: dict[str, object]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "request_id": "clarify-local-audit",
            "task_digest": contract_digest(task),
            "session_id": "session-local-audit",
            "issue_kind": "clarification",
            "severity": "high",
            "question_digest": self.digest,
            "presentation_digest": "sha256:" + "b" * 64,
            "repository_check": {
                "status": "resolved",
                "evidence_digest": "sha256:" + "c" * 64,
            },
            "option_ids": ["keep-local", "defer-remote"],
            "recommended_option_id": "keep-local",
        }

    def test_low_uncertainty_is_autonomous_without_authority(self) -> None:
        from control_plane.clarification import evaluate_clarification_gate

        gate = evaluate_clarification_gate(self.task(0))

        self.assertEqual(gate["status"], "autonomous")
        self.assertTrue(gate["decision_ready"])
        self.assertEqual(gate["blocked_effects"], [])
        self.assertNotIn("authorization", gate)

    def test_material_ambiguity_is_pending_without_host_authority(self) -> None:
        from control_plane.clarification import evaluate_clarification_gate

        for uncertainty in (1, 2):
            with self.subTest(uncertainty=uncertainty):
                gate = evaluate_clarification_gate(self.task(uncertainty))
                self.assertEqual(gate["status"], "pending_host_capability")
                self.assertFalse(gate["decision_ready"])
                self.assertEqual(gate["blocked_effects"], ["local_write"])
                self.assertNotIn("authorization", gate)

    def test_critical_ambiguity_requires_reframe(self) -> None:
        from control_plane.clarification import evaluate_clarification_gate

        gate = evaluate_clarification_gate(self.task(3))

        self.assertEqual(gate["status"], "blocked")
        self.assertEqual(gate["next_action"], "reframe_task")
        self.assertEqual(gate["reason_codes"], ["C_REFRAME_REQUIRED"])

    def test_diagnostic_request_is_closed_but_never_resolves_authority(self) -> None:
        from control_plane.clarification import (
            evaluate_clarification_gate,
            validate_clarification_request,
        )

        task = self.task(2)
        request = self.diagnostic_request(task)
        self.assertEqual(validate_clarification_request(request), [])

        gate = evaluate_clarification_gate(task, request=request)
        self.assertEqual(gate["status"], "pending_host_capability")
        self.assertFalse(gate["decision_ready"])
        self.assertEqual(
            gate["reason_codes"], ["CLARIFY_REQUEST_DIAGNOSTIC_ONLY"]
        )

        tampered = copy.deepcopy(request)
        tampered["task_digest"] = "sha256:" + "d" * 64
        tampered_gate = evaluate_clarification_gate(task, request=tampered)
        self.assertEqual(
            tampered_gate["reason_codes"], ["CLARIFY_HOST_CAPABILITY_PENDING"]
        )

    def test_runtime_metrics_are_local_deduplicated_and_replay_safe(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary), runtime_digest=self.digest)
            first = store.record_context_metrics(
                "TASK-METRICS-LOCAL",
                task_digest=self.digest,
                session_id="session-metrics-local",
                invocation_id="invocation-metrics-local",
                subject_digest=self.digest,
                runtime_metrics={
                    "router_manifest_bytes": 240,
                    "context_units_selected": 7,
                    "tool_use_id": None,
                },
            )
            second = store.record_context_metrics(
                "TASK-METRICS-LOCAL",
                task_digest=self.digest,
                session_id="session-metrics-local",
                invocation_id="invocation-metrics-local",
                subject_digest=self.digest,
                runtime_metrics={
                    "router_manifest_bytes": 240,
                    "context_units_selected": 7,
                    "tool_use_id": None,
                },
            )

            self.assertEqual(first, second)
            self.assertEqual(first["metrics_status"], "local")
            self.assertEqual(first["router_manifest_bytes_total"], 240)
            self.assertEqual(first["context_units_selected_total"], 7)
            self.assertNotIn("required_resource_bytes_total", first)
            self.assertNotIn("worker_time_ms_total", first)

            with self.assertRaisesRegex(ValueError, "M_METRIC_REPLAY_CONFLICT"):
                store.record_context_metrics(
                    "TASK-METRICS-LOCAL",
                    task_digest=self.digest,
                    session_id="session-metrics-local",
                    invocation_id="invocation-metrics-local",
                    subject_digest=self.digest,
                    runtime_metrics={
                        "router_manifest_bytes": 241,
                        "tool_use_id": None,
                    },
                )


if __name__ == "__main__":
    unittest.main()
