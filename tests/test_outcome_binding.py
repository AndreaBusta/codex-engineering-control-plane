from __future__ import annotations

import copy
import unittest
from pathlib import Path

from tests.router_test_support import task_envelope


def _decision(task: dict[str, object]) -> dict[str, object]:
    from control_plane.contracts import contract_digest

    core = {
        "schema_version": 1,
        "task_id": task["task_id"],
        "mode": "audit",
        "ok": True,
        "decision_ready": False,
        "summary": {
            "tier": "T1",
            "project_profile": {"profiles": ["generic"]},
        },
        "documentation": {},
        "interaction": {
            "clarification_gate": {
                "level": "low", "status": "autonomous", "decision_ready": True,
            }
        },
        "approval_boundaries": ["commit", "remote_write", "pull_request", "integration"],
        "authorization": {"local_write": True},
        "required_gates": ["gate.relevant-tests"],
        "selected_resource_digests": {},
        "matched_routes": ["quality-profile-generic"],
        "facts": {"task_digest": contract_digest(task)},
        "errors": [],
    }
    return {**core, "decision_digest": contract_digest(core)}


class OutcomeBindingTests(unittest.TestCase):
    def _plan(self) -> dict[str, object]:
        from control_plane.run_workflow import build_run_plan

        task = task_envelope(
            requested_outcome="integration",
            effects=[
                {"name": "local_write", "source": "user_explicit"},
                {"name": "commit", "source": "user_explicit"},
                {"name": "remote_write", "source": "user_explicit"},
                {"name": "pull_request", "source": "user_explicit"},
                {"name": "integration", "source": "user_explicit"},
            ],
        )
        return build_run_plan(
            task=task,
            decision=_decision(task),
            repository=Path("/tmp/outcome-binding-repository"),
            branch="codex/outcome-binding",
            head="a" * 40,
            session_id="session-outcome-binding-001",
            prepared_at="2026-08-08T10:00:00Z",
        )

    def test_binding_is_closed_non_authorizing_and_advances_only_in_order(self) -> None:
        from control_plane.run_workflow import (
            advance_outcome_binding,
            build_outcome_binding,
            validate_outcome_binding,
        )

        binding = build_outcome_binding(
            run_plan=self._plan(),
            review_head="a" * 40,
            reviewed_tree_digest="sha256:" + "1" * 64,
            reviewed_diff_digest="sha256:" + "2" * 64,
        )
        self.assertEqual(validate_outcome_binding(binding), [])
        self.assertFalse(binding["authorizes"])
        self.assertEqual(binding["committed_head"], None)

        staged = advance_outcome_binding(
            binding, effect_id="local_write", observation={
                "head": "a" * 40,
                "tree_digest": "sha256:" + "1" * 64,
                "diff_digest": "sha256:" + "2" * 64,
            },
        )
        committed = advance_outcome_binding(
            staged, effect_id="commit", observation={
                "parent_head": "a" * 40,
                "tree_digest": "sha256:" + "1" * 64,
                "committed_head": "b" * 40,
            },
        )
        pushed = advance_outcome_binding(
            committed, effect_id="remote_write", observation={
                "pushed_head": "b" * 40,
            },
        )
        drafted = advance_outcome_binding(
            pushed, effect_id="pull_request", observation={
                "pull_request_digest": "sha256:" + "3" * 64,
                "checks_digest": "sha256:" + "4" * 64,
                "head": "b" * 40,
            },
        )
        merged = advance_outcome_binding(
            drafted, effect_id="integration", observation={
                "merge_sha": "c" * 40,
                "checks_digest": "sha256:" + "4" * 64,
            },
        )

        self.assertEqual(validate_outcome_binding(merged), [])
        self.assertEqual(merged["merge_sha"], "c" * 40)
        self.assertEqual(
            merged["consumed_effect_ids"],
            ["local_write", "commit", "remote_write", "pull_request", "integration"],
        )

    def test_binding_rejects_reorder_replay_drift_and_unknown_effect(self) -> None:
        from control_plane.run_workflow import advance_outcome_binding, build_outcome_binding

        binding = build_outcome_binding(
            run_plan=self._plan(), review_head="a" * 40,
            reviewed_tree_digest="sha256:" + "1" * 64,
            reviewed_diff_digest="sha256:" + "2" * 64,
        )
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_ORDER"):
            advance_outcome_binding(
                binding, effect_id="remote_write", observation={"pushed_head": "b" * 40},
            )
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_EFFECT"):
            advance_outcome_binding(binding, effect_id="release", observation={})
        staged = advance_outcome_binding(
            binding, effect_id="local_write", observation={
                "head": "a" * 40,
                "tree_digest": "sha256:" + "1" * 64,
                "diff_digest": "sha256:" + "2" * 64,
            },
        )
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_CAS"):
            advance_outcome_binding(
                staged, effect_id="commit", observation={
                    "parent_head": "d" * 40,
                    "tree_digest": "sha256:" + "1" * 64,
                    "committed_head": "b" * 40,
                },
            )
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_REPLAY"):
            advance_outcome_binding(
                staged, effect_id="local_write", observation={
                    "head": "a" * 40,
                    "tree_digest": "sha256:" + "1" * 64,
                    "diff_digest": "sha256:" + "2" * 64,
                },
            )
        tampered = copy.deepcopy(staged)
        tampered["scope_paths_digest"] = "sha256:" + "9" * 64
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_BINDING"):
            advance_outcome_binding(
                tampered, effect_id="commit", observation={
                    "parent_head": "a" * 40,
                    "tree_digest": "sha256:" + "1" * 64,
                    "committed_head": "b" * 40,
                },
            )

    def test_unhashable_or_missing_outcome_returns_contract_issue_not_type_error(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import (
            advance_outcome_binding,
            build_outcome_binding,
            validate_outcome_binding,
            validate_run_plan,
        )

        for invalid_outcome in ([], {}, None):
            with self.subTest(invalid_outcome=invalid_outcome):
                plan = self._plan()
                plan["requested_outcome"] = invalid_outcome
                plan["plan_digest"] = contract_digest(
                    {key: value for key, value in plan.items() if key != "plan_digest"}
                )
                plan_issues = validate_run_plan(plan)
                self.assertEqual(plan_issues[0].code, "RUN_BINDING")

                binding = build_outcome_binding(
                    run_plan=self._plan(),
                    review_head="a" * 40,
                    reviewed_tree_digest="sha256:" + "1" * 64,
                    reviewed_diff_digest="sha256:" + "2" * 64,
                )
                binding["requested_outcome"] = invalid_outcome
                binding["binding_digest"] = contract_digest(
                    {key: value for key, value in binding.items() if key != "binding_digest"}
                )
                binding_issues = validate_outcome_binding(binding)
                self.assertEqual(binding_issues[0].code, "OUTCOME_BINDING")
                with self.assertRaisesRegex(ValueError, "E_OUTCOME_BINDING"):
                    advance_outcome_binding(
                        binding,
                        effect_id="local_write",
                        observation={
                            "head": "a" * 40,
                            "tree_digest": "sha256:" + "1" * 64,
                            "diff_digest": "sha256:" + "2" * 64,
                        },
                    )


if __name__ == "__main__":
    unittest.main()
