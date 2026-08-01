from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest

from tests.git_test_support import GitScenario


ROOT = Path(__file__).parents[1]


class InstalledRiskIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()
        self.addCleanup(self.scenario.close)
        self.scenario.checkout_feature("codex/risk-installed")

    def test_installed_runtime_governs_local_risk_without_claiming_remote(
        self,
    ) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        subprocess.run(
            ["git", "add", "."],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "test: installed control plane"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        launcher = (
            Path(plan["installed_snapshot"]["path"])
            / "scripts"
            / "control-plane"
        )

        completed = subprocess.run(
            [
                str(launcher),
                "risk-status",
                "--repo",
                str(self.scenario.repo),
                "--json",
            ],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )

        payload = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(payload["status"], "UNKNOWN")
        self.assertEqual(
            payload["facts"]["governing_policy_source"],
            "installed_manifest",
        )
        checks = {
            item["code"]: item
            for item in payload["dimensions"]["local"]["checks"]
        }
        self.assertEqual(checks["RS_LOCAL_POLICY"]["status"], "PASS")
        self.assertEqual(checks["RS_LOCAL_HOOK_PATH"]["status"], "PASS")
        self.assertEqual(checks["RS_LOCAL_HOOK_DIGEST"]["status"], "PASS")
        self.assertEqual(
            payload["dimensions"]["remote"]["status"], "UNKNOWN"
        )


if __name__ == "__main__":
    unittest.main()
