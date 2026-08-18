from __future__ import annotations

import unittest

from control_plane.git_state import GateResult
from tests.git_test_support import GitScenario


class CoreGitStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()
        self.addCleanup(self.scenario.close)

    def test_write_gate_blocks_an_unmaterialized_repository(self) -> None:
        from unittest.mock import patch

        from control_plane.git_state import evaluate_preflight
        from control_plane.materialization import GitStateMaterialization

        policy = self.scenario.policy()
        self.scenario.checkout_feature()
        unproven = GitStateMaterialization(
            False,
            "FAIL",
            1148,
            468,
            ("core_state", "objects"),
            False,
            "E_MATERIALIZATION_DATALESS",
        )
        with patch(
            "control_plane.materialization.inspect_git_state_materialization",
            return_value=unproven,
        ):
            write = evaluate_preflight(self.scenario.repo, policy, "write")
            read = evaluate_preflight(self.scenario.repo, policy, "read")

        self.assertFalse(write.ok)
        self.assertIn(
            "E_MATERIALIZATION_DATALESS", {error.code for error in write.errors}
        )
        self.assertIn(
            "GIT_STATE_MATERIALIZED", {check.code for check in write.checks}
        )
        self.assertFalse(
            next(
                check
                for check in write.checks
                if check.code == "GIT_STATE_MATERIALIZED"
            ).ok
        )
        self.assertTrue(read.ok, "read mode must still allow investigation")
        self.assertFalse(read.facts["git_state_materialized"])
        self.assertEqual(read.facts["dataless_git_state_files"], 468)

    def test_write_gate_passes_on_a_materialized_repository(self) -> None:
        from control_plane.git_state import evaluate_preflight

        policy = self.scenario.policy()
        self.scenario.checkout_feature()
        write = evaluate_preflight(self.scenario.repo, policy, "write")
        self.assertNotIn(
            "E_MATERIALIZATION_DATALESS", {error.code for error in write.errors}
        )
        self.assertTrue(
            next(
                check
                for check in write.checks
                if check.code == "GIT_STATE_MATERIALIZED"
            ).ok
        )

    def test_read_and_write_preflight_are_local_and_fail_closed(self) -> None:
        from control_plane.git_state import evaluate_preflight

        policy = self.scenario.policy()
        read = evaluate_preflight(self.scenario.repo, policy, "read")
        self.assertTrue(read.ok)
        self.assertEqual(read.facts["branch"], "main")

        protected = evaluate_preflight(self.scenario.repo, policy, "write")
        self.assertFalse(protected.ok)
        self.assertIn("E_GIT_BASE_BRANCH", {error.code for error in protected.errors})

        self.scenario.checkout_feature()
        feature = evaluate_preflight(self.scenario.repo, policy, "write")
        self.assertTrue(feature.ok)
        self.assertEqual(feature.to_dict()["command"], "preflight")

        (self.scenario.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        dirty = evaluate_preflight(self.scenario.repo, policy, "write")
        self.assertFalse(dirty.ok)
        self.assertIn("E_GIT_DIRTY", {error.code for error in dirty.errors})

    def test_release_and_remote_receipt_helpers_are_quarantined(self) -> None:
        from control_plane.git_state import (
            revalidate_base_verification_receipt,
            verify_refreshed_base_containment,
        )

        with self.assertRaisesRegex(ValueError, "E_CAPABILITY_QUARANTINED"):
            verify_refreshed_base_containment(
                effect_plan={}, integration_receipt={}, refresh_receipt={}
            )
        self.assertFalse(
            revalidate_base_verification_receipt({}, refresh_receipt={})
        )

    def test_unobservable_git_is_a_stable_failed_result(self) -> None:
        from pathlib import Path
        from control_plane.git_state import evaluate_preflight

        result = evaluate_preflight(Path("/definitely/not/a/repository"), self.scenario.policy(), "read")
        self.assertIsInstance(result, GateResult)
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0].code, "E_GIT_NOT_REPOSITORY")


if __name__ == "__main__":
    unittest.main()
