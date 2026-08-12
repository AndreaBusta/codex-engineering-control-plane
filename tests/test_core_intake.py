from __future__ import annotations

import json
import unittest

from control_plane.contracts import contract_digest
from tests.core_router_test_support import task_envelope


def compact_manifest(task: dict[str, object]) -> str:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "task_digest": contract_digest(task),
        "decision_digest": contract_digest({"decision": task["task_id"]}),
        "tier": "T0",
        "workflow_mode": "direct",
        "required": ["instruction.project-agents"],
        "recommended": [],
        "unresolved": [],
        "max_agents": 0,
        "execution_strategy": "sequential",
        "required_documents": [],
        "approval_boundaries": [],
        "required_gates": [],
        "project_profile": {
            "schema_version": 1,
            "kind": "generic",
            "profiles": ["generic"],
            "evidence": [],
            "confidence": "fallback",
            "truncated": False,
        },
        "interaction": {
            "recommended_mode": "default",
            "reason_codes": ["MODE_BOUNDED"],
            "user_action": "Continue in the current task mode.",
            "automatic_change": False,
            "confidence": "high",
        },
        "clarification": {
            "level": "low",
            "status": "autonomous",
            "decision_ready": True,
            "reason_codes": ["CLARIFY_LOW_AUTONOMOUS"],
        },
    }
    manifest["manifest_digest"] = contract_digest(manifest)
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


class CoreIntakeTests(unittest.TestCase):
    def test_closed_interaction_view_never_changes_mode(self) -> None:
        from control_plane.intake import render_interaction_recommendation

        view = render_interaction_recommendation("normal", ["MODE_BOUNDED"])
        self.assertEqual(view.commands, ())
        self.assertFalse(view.automatic_change)
        with self.assertRaisesRegex(ValueError, "E_INTERACTION_MODE"):
            render_interaction_recommendation("automatic", ["MODE_BOUNDED"])

    def test_novice_brief_is_task_bound_bounded_and_non_authorizing(self) -> None:
        from control_plane.intake import render_novice_brief

        task = task_envelope(
            task_id="brief-core",
            signals=[],
            risk={
                "uncertainty": 0,
                "blast_radius": 0,
                "irreversibility": 0,
                "verification_complexity": 0,
            },
        )
        brief = render_novice_brief(task, compact_manifest(task))
        self.assertLessEqual(len(brief.encode()), 1024)
        self.assertIn("solo descripción; no autoriza", brief)
        self.assertIn("automatic_change=false", brief)

        other = task_envelope(task_id="other-task")
        with self.assertRaisesRegex(ValueError, "E_INTAKE_TASK_DIGEST"):
            render_novice_brief(other, compact_manifest(task))


if __name__ == "__main__":
    unittest.main()
