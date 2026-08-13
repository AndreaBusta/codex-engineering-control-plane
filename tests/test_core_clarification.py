from __future__ import annotations

import unittest

from control_plane.contracts import contract_digest
from tests.core_router_test_support import task_envelope


def clarification_request(task: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": "clarification-001",
        "task_digest": contract_digest(task),
        "session_id": "session-001",
        "issue_kind": "clarification",
        "severity": "medium",
        "question_digest": contract_digest({"question": "scope"}),
        "presentation_digest": contract_digest({"presentation": "options"}),
        "repository_check": {
            "status": "resolved",
            "evidence_digest": contract_digest({"repository": "checked"}),
        },
        "option_ids": ["bounded", "broader"],
        "recommended_option_id": "bounded",
    }


class CoreClarificationTests(unittest.TestCase):
    def test_request_contract_is_closed_and_task_bound(self) -> None:
        from control_plane.clarification import validate_clarification_request

        task = task_envelope()
        request = clarification_request(task)
        self.assertEqual(validate_clarification_request(request), [])

        request["authority"] = True
        self.assertEqual(
            [issue.code for issue in validate_clarification_request(request)],
            ["C_SCHEMA"],
        )

    def test_gate_is_pure_diagnostic_across_closed_uncertainty_levels(self) -> None:
        from control_plane.clarification import evaluate_clarification_gate

        expected = {
            0: ("autonomous", True, "continue"),
            1: ("pending_host_capability", False, "wait_for_host_capability"),
            2: ("pending_host_capability", False, "wait_for_host_capability"),
            3: ("blocked", False, "reframe_task"),
        }
        for uncertainty, (status, ready, next_action) in expected.items():
            with self.subTest(uncertainty=uncertainty):
                task = task_envelope(
                    risk={
                        "uncertainty": uncertainty,
                        "blast_radius": 0,
                        "irreversibility": 0,
                        "verification_complexity": 0,
                    }
                )
                result = evaluate_clarification_gate(
                    task,
                    request=(
                        clarification_request(task)
                        if uncertainty in {1, 2}
                        else None
                    ),
                )
                self.assertEqual(result["status"], status)
                self.assertIs(result["decision_ready"], ready)
                self.assertEqual(result["next_action"], next_action)
                self.assertNotIn("authorizes", result)


if __name__ == "__main__":
    unittest.main()
