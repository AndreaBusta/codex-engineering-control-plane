from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.git_test_support import FIXTURE_POLICY, GitScenario


ROOT = Path(__file__).parents[1]


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "control_plane.cli", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class CliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()

    def tearDown(self) -> None:
        self.scenario.close()

    def test_policy_check_json_succeeds_for_valid_policy(self) -> None:
        result = run_cli(
            "policy-check", "--policy", str(FIXTURE_POLICY), "--json"
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "policy-check")
        self.assertEqual(payload["issues"], [])

    def test_policy_check_json_fails_for_missing_policy(self) -> None:
        result = run_cli(
            "policy-check",
            "--policy",
            str(self.scenario.root / "missing.toml"),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "E_POLICY_NOT_FOUND")

    def test_preflight_json_uses_nonzero_exit_for_blocked_write(self) -> None:
        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--offline",
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("E_GIT_BASE_BRANCH", codes)
        self.assertTrue(payload["offline"])

    def test_preflight_json_succeeds_for_clean_feature(self) -> None:
        self.scenario.checkout_feature()

        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--offline",
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["facts"]["branch"], "feature/test")

    def test_doctor_json_reports_local_prerequisites(self) -> None:
        result = run_cli(
            "doctor",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["facts"]["git_available"])
        self.assertTrue(payload["facts"]["python_compatible"])
        self.assertTrue(payload["facts"]["policy_valid"])
        self.assertTrue(payload["facts"]["git_repository"])

    def test_human_output_has_unambiguous_status(self) -> None:
        result = run_cli("policy-check", "--policy", str(FIXTURE_POLICY))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS policy-check", result.stdout)

    def test_online_preflight_handles_nonexistent_repository_as_json(self) -> None:
        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.root / "does-not-exist"),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertIn("E_GIT_NOT_REPOSITORY", codes)

    def test_online_fetch_failure_has_stable_error(self) -> None:
        self.scenario.checkout_feature()
        from tests.git_test_support import git

        git(
            self.scenario.repo,
            "remote",
            "set-url",
            "origin",
            str(self.scenario.root / "missing-remote.git"),
        )

        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--refresh",
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertIn("E_FETCH_FAILED", codes)

    def test_default_preflight_does_not_contact_remote(self) -> None:
        self.scenario.checkout_feature()
        from tests.git_test_support import git

        git(
            self.scenario.repo,
            "remote",
            "set-url",
            "origin",
            str(self.scenario.root / "missing-remote.git"),
        )

        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offline"])

    def test_invalid_policy_keeps_preflight_json_shape(self) -> None:
        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(self.scenario.root / "missing-policy.toml"),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["command"], "preflight")
        self.assertEqual(payload["facts"], {})
        self.assertEqual(payload["checks"], [])

    def test_read_human_output_is_diagnostic_when_checks_fail(self) -> None:
        (self.scenario.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        result = run_cli(
            "preflight",
            "--mode",
            "read",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DIAGNOSTIC preflight", result.stdout)

    def test_doctor_handles_nonexistent_repository_as_json(self) -> None:
        result = run_cli(
            "doctor",
            "--repo",
            str(self.scenario.root / "does-not-exist"),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertIn("E_GIT_NOT_REPOSITORY", codes)


if __name__ == "__main__":
    unittest.main()
