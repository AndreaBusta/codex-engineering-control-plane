from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tests.test_core_task_state import make_repo


class CoreRiskSentinelTests(unittest.TestCase):
    def test_tristate_aggregate_and_dimension_contract_fail_closed(self) -> None:
        from control_plane.risk_sentinel import (
            FAIL,
            PASS,
            UNKNOWN,
            RiskCheck,
            RiskDimension,
            aggregate_status,
        )

        self.assertEqual(aggregate_status([PASS, UNKNOWN, PASS]), UNKNOWN)
        self.assertEqual(aggregate_status([UNKNOWN, FAIL]), FAIL)
        with self.assertRaisesRegex(ValueError, "E_RISK_STATUS"):
            aggregate_status(["MAYBE"])
        with self.assertRaisesRegex(ValueError, "E_RISK_STATUS"):
            RiskDimension(
                status=PASS,
                checks=(RiskCheck("test", UNKNOWN, "unknown", {}),),
                errors=(),
            )

    def test_risk_status_keeps_remote_unknown_and_non_authorizing(self) -> None:
        from control_plane.risk_sentinel import UNKNOWN, evaluate_risk_status

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo")
            status = evaluate_risk_status(repo).to_dict()

        self.assertEqual(status["dimensions"]["remote"]["status"], UNKNOWN)
        self.assertFalse(status["authorizes"])
        self.assertFalse(status["facts"]["automatic_change"])
        self.assertEqual(status["facts"]["remote_capability"], "quarantined")


if __name__ == "__main__":
    unittest.main()
