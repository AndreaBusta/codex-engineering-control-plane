from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.git_test_support import GitScenario
from tests.git_test_support import FIXTURE_POLICY
from tests.router_test_support import task_envelope


ROOT = Path(__file__).parents[1]


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "control_plane.cli", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


class CliV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.contracts import contract_digest

        self.digest = contract_digest({"test": "cli-v2"})

    def test_default_authority_files_resolve_from_subdirectory(self) -> None:
        subdirectory = ROOT / "docs" / "engineering"

        inventory = run_cli(
            "inventory", "--repo", str(subdirectory), "--json"
        )

        payload = json.loads(inventory.stdout)
        self.assertEqual(inventory.returncode, 0, inventory.stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["resources"]), 28)

    def test_registry_check_validates_policy_gate_references(self) -> None:
        result = run_cli(
            "registry-check",
            "--registry",
            str(ROOT / ".codex" / "resource-registry.toml"),
            "--policy",
            str(ROOT / ".codex" / "project-policy.toml"),
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_route_command_emits_a_decision_under_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_path = Path(temporary) / "task.json"
            task_path.write_text(
                json.dumps(task_envelope()), encoding="utf-8"
            )

            result = run_cli(
                "route",
                "--repo",
                str(ROOT / "control_plane"),
                "--task",
                str(task_path),
                "--mode",
                "audit",
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["summary"]["tier"], "T2")
        self.assertIn("skill.verified-workflow", payload["summary"]["required"])

    def test_route_rejects_serialized_inventory_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_path = Path(temporary) / "task.json"
            inventory_path = Path(temporary) / "inventory.json"
            task_path.write_text(json.dumps(task_envelope()), encoding="utf-8")
            inventory_path.write_text("{}\n", encoding="utf-8")

            result = run_cli(
                "route",
                "--repo",
                str(ROOT),
                "--task",
                str(task_path),
                "--inventory",
                str(inventory_path),
                "--json",
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments: --inventory", result.stderr)

    def test_verification_run_rejects_caller_selected_profile_or_command(
        self,
    ) -> None:
        result = run_cli(
            "verification-run",
            "--repo",
            str(ROOT),
            "--task-id",
            "TASK-VERIFY-CLI",
            "--profile",
            "control_plane_assurance",
            "--command-id",
            "doctor",
            "--json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_human_route_output_surfaces_profile_and_mode_recommendation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_path = Path(temporary) / "task.json"
            task_path.write_text(
                json.dumps(
                    task_envelope(
                        signals=[
                            "multi_file",
                            "regression_risk",
                            "long_running",
                            "unclear_outcome",
                        ]
                    )
                ),
                encoding="utf-8",
            )

            result = run_cli(
                "route",
                "--repo",
                str(ROOT),
                "--task",
                str(task_path),
                "--mode",
                "audit",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project_profiles=generic", result.stdout)
        self.assertIn("interaction_recommended=plan_then_goal", result.stdout)
        self.assertIn("interaction_automatic_change=false", result.stdout)

    def test_actual_registry_auto_selects_security_and_multidomain_guides(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_path = Path(temporary) / "task.json"
            task_path.write_text(
                json.dumps(
                    task_envelope(
                        signals=["auth", "cross_system"],
                        domains=["control-plane", "saas_backend"],
                    )
                ),
                encoding="utf-8",
            )
            result = run_cli(
                "route",
                "--repo",
                str(ROOT),
                "--task",
                str(task_path),
                "--mode",
                "audit",
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "document.security-guide", payload["summary"]["required"]
        )
        selected_or_deferred = set(payload["summary"]["recommended"]).union(
            payload["summary"]["deferred"]
        )
        self.assertIn("document.multidomain-guide", selected_or_deferred)

    def test_task_commands_store_state_under_git_dir(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)

        started = run_cli(
            "task",
            "start",
            "--repo",
            str(scenario.repo),
            "--task-id",
            "TASK-CLI",
            "--outcome",
            "answer",
            "--branch",
            "main",
            "--task-digest",
            self.digest,
            "--decision-digest",
            self.digest,
            "--json",
        )
        status = run_cli(
            "task",
            "status",
            "--repo",
            str(scenario.repo),
            "--task-id",
            "TASK-CLI",
            "--json",
        )

        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(json.loads(status.stdout)["task"]["state"], "framed")
        self.assertFalse((scenario.repo / "TASK-CLI.json").exists())

    def test_task_transition_rejects_serialized_evidence_argument(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / "evidence.json"
            evidence.write_text(
                json.dumps({"remote_head": "a" * 40}), encoding="utf-8"
            )
            result = run_cli(
                "task",
                "transition",
                "--repo",
                str(ROOT),
                "--task-id",
                "TASK-FORGED-EVIDENCE",
                "--state",
                "pushed",
                "--evidence",
                str(evidence),
                "--json",
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments: --evidence", result.stderr)

    def test_dirty_preflight_requires_exact_active_task_and_session(
        self,
    ) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        started = run_cli(
            "task",
            "start",
            "--repo",
            str(scenario.repo),
            "--task-id",
            "TASK-LEASE",
            "--outcome",
            "local_change",
            "--branch",
            "feature/test",
            "--task-digest",
            self.digest,
            "--decision-digest",
            self.digest,
            "--session-id",
            "session-a",
            "--scope-path",
            "src/**",
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )
        (scenario.repo / "src").mkdir(exist_ok=True)
        (scenario.repo / "src" / "dirty.txt").write_text(
            "dirty\n", encoding="utf-8"
        )

        valid = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--task-id",
            "TASK-LEASE",
            "--session-id",
            "session-a",
            "--json",
        )
        wrong = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--task-id",
            "TASK-LEASE",
            "--session-id",
            "session-b",
            "--json",
        )
        missing_task = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--session-id",
            "session-a",
            "--json",
        )
        missing_session = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--task-id",
            "TASK-LEASE",
            "--json",
        )

        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertTrue(json.loads(valid.stdout)["facts"]["lease_continuation"])
        self.assertNotEqual(wrong.returncode, 0)
        self.assertNotEqual(missing_task.returncode, 0)
        self.assertNotEqual(missing_session.returncode, 0)
        self.assertIn(
            "E_LEASE_MISMATCH",
            {item["code"] for item in json.loads(wrong.stdout)["errors"]},
        )
        for result in (missing_task, missing_session):
            self.assertIn(
                "E_GIT_DIRTY",
                {
                    item["code"]
                    for item in json.loads(result.stdout)["errors"]
                },
            )

    def test_task_lease_release_requires_exact_owner_bindings(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/lease-release")
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir

        policy_digest = contract_digest(load_policy(FIXTURE_POLICY))
        state_dir = worktree_git_dir(scenario.repo)
        lease = TaskLease.acquire(
            state_dir,
            task_id="TASK-CLI-RELEASE",
            worktree=str(scenario.repo),
            branch="codex/lease-release",
            session_id="session-cli-release",
            paths=["."],
            policy_digest=policy_digest,
        )

        result = run_cli(
            "task",
            "lease-release",
            "--repo",
            str(scenario.repo),
            "--task-id",
            "TASK-CLI-RELEASE",
            "--worktree",
            str(scenario.repo),
            "--branch",
            "codex/lease-release",
            "--session-id",
            "session-cli-release",
            "--policy-digest",
            policy_digest,
            "--lease-digest",
            lease["lease_digest"],
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
