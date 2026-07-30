from __future__ import annotations

import copy
import unittest

from tests.router_test_support import task_envelope


class ContractTestsV2(unittest.TestCase):
    def test_valid_task_envelope_has_no_issues(self) -> None:
        from control_plane.contracts import validate_task_envelope

        self.assertEqual(validate_task_envelope(task_envelope()), [])

    def test_unknown_dependency_raises_uncertainty_for_clarification(
        self,
    ) -> None:
        from control_plane.contracts import validate_task_envelope

        cases = {
            "T_GOAL_REFERENCE": [
                {
                    "id": "build",
                    "summary": "Build the feature.",
                    "domains": ["generic"],
                    "depends_on": ["missing"],
                }
            ],
            "T_GOAL_SELF_DEPENDENCY": [
                {
                    "id": "build",
                    "summary": "Build the feature.",
                    "domains": ["generic"],
                    "depends_on": ["build"],
                }
            ],
            "T_GOAL_CYCLE": [
                {
                    "id": "build",
                    "summary": "Build the feature.",
                    "domains": ["generic"],
                    "depends_on": ["verify"],
                },
                {
                    "id": "verify",
                    "summary": "Verify the feature.",
                    "domains": ["generic"],
                    "depends_on": ["build"],
                },
            ],
        }

        for expected_code, goals in cases.items():
            with self.subTest(expected_code=expected_code):
                codes = {
                    issue.code
                    for issue in validate_task_envelope(
                        task_envelope(goals=goals)
                    )
                }
                self.assertIn(expected_code, codes)

    def test_unknown_key_intent_and_provenance_fail_closed(self) -> None:
        from control_plane.contracts import validate_task_envelope

        task = task_envelope(intent="invent")
        task["surprise"] = True
        task["effects"][0]["source"] = "external_magic"
        codes = {issue.code for issue in validate_task_envelope(task)}

        self.assertIn("T_UNKNOWN", codes)
        self.assertIn("T_INTENT", codes)
        self.assertIn("T_PROVENANCE", codes)

    def test_risk_cannot_be_lowered_by_external_content(self) -> None:
        from control_plane.contracts import validate_task_envelope

        task = task_envelope()
        task["risk"]["uncertainty"] = -1
        task["risk_provenance"] = "external_untrusted"

        codes = {issue.code for issue in validate_task_envelope(task)}

        self.assertIn("T_RISK", codes)

    def test_effect_phase_paths_goals_and_task_id_are_closed_and_safe(
        self,
    ) -> None:
        from control_plane.contracts import validate_task_envelope

        invalid = task_envelope(
            task_id="../escape",
            phase="magic",
            scope_paths=["../outside"],
            goals=[{"id": "bad/id", "summary": "", "domains": "ios"}],
            effects=[
                {"name": "unknown_effect", "source": "model_inference"}
            ],
        )

        codes = {issue.code for issue in validate_task_envelope(invalid)}

        self.assertIn("T_TASK_ID", codes)
        self.assertIn("T_PHASE", codes)
        self.assertIn("T_SCOPE", codes)
        self.assertIn("T_GOAL", codes)
        self.assertIn("T_EFFECT", codes)

    def test_contract_digest_ignores_mapping_order_not_list_order(self) -> None:
        from control_plane.contracts import contract_digest

        first = {"a": 1, "b": [1, 2]}
        second = {"b": [1, 2], "a": 1}
        changed = copy.deepcopy(first)
        changed["b"].reverse()

        self.assertEqual(contract_digest(first), contract_digest(second))
        self.assertNotEqual(contract_digest(first), contract_digest(changed))

    def test_authorization_request_is_inert_task_bound_and_scope_bound(
        self,
    ) -> None:
        from control_plane.contracts import (
            contract_digest,
            safe_scope_path,
            validate_authorization_request,
        )

        task = task_envelope()
        digest = contract_digest(task)
        request = {
            "schema_version": 1,
            "grant_id": "grant-001",
            "task_digest": digest,
            "session_id": "session-001",
            "allowed_effects": ["commit"],
            "scope_paths": task["scope_paths"],
        }

        self.assertTrue(safe_scope_path("control_plane/"))
        self.assertFalse(safe_scope_path("../outside"))
        self.assertEqual(
            validate_authorization_request(
                request,
                task_digest=digest,
                scope_paths=task["scope_paths"],
            ),
            [],
        )
        request["issuer"] = "trusted_host"
        self.assertIn(
            "Z_SCHEMA",
            {
                issue.code
                for issue in validate_authorization_request(
                    request,
                    task_digest=digest,
                    scope_paths=task["scope_paths"],
                )
            },
        )
        request.pop("issuer")
        request["task_digest"] = "sha256:" + ("0" * 64)
        codes = {
            issue.code
            for issue in validate_authorization_request(
                request,
                task_digest=digest,
                scope_paths=task["scope_paths"],
            )
        }
        self.assertIn("Z_TASK_DIGEST", codes)


if __name__ == "__main__":
    unittest.main()
