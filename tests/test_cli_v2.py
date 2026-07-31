from __future__ import annotations

from contextlib import redirect_stdout
import io
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
        self.assertEqual(
            payload["interaction"]["clarification_gate"]["status"],
            "pending_host_capability",
        )

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

    def test_route_has_no_serialized_clarification_or_authority_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            task_path = Path(temporary) / "task.json"
            task_path.write_text(json.dumps(task_envelope()), encoding="utf-8")
            for flag in (
                "--clarification-request",
                "--clarification-resolution",
                "--assumption",
                "--irreversible-confirmation",
                "--authorization",
            ):
                with self.subTest(flag=flag):
                    result = run_cli(
                        "route",
                        "--repo",
                        str(ROOT),
                        "--task",
                        str(task_path),
                        flag,
                        str(task_path),
                        "--json",
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("unrecognized arguments", result.stderr)

    def test_serialized_resource_receipt_is_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            decision_path = Path(temporary) / "decision.json"
            receipt_path = Path(temporary) / "receipt.json"
            decision_path.write_text("{}\n", encoding="utf-8")
            receipt_path.write_text("{}\n", encoding="utf-8")

            result = run_cli(
                "route-verify",
                "--decision",
                str(decision_path),
                "--receipt",
                str(receipt_path),
                "--mode",
                "audit",
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["status"], "diagnostic")

    def test_risk_status_without_host_anchor_is_unknown_exit_two(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-cli")
        with tempfile.TemporaryDirectory() as temporary:
            decision_path = Path(temporary) / "decision.json"
            decision_path.write_text(
                json.dumps(
                    {
                        "interaction": {
                            "recommended_mode": "plan",
                            "reason_codes": ["MODE_COMPLEX_OR_UNCERTAIN"],
                        },
                        "authorization": {"local_write": True},
                    }
                ),
                encoding="utf-8",
            )
            result = run_cli(
                "risk-status",
                "--repo",
                str(scenario.repo),
                "--policy",
                str(ROOT / ".codex" / "project-policy.toml"),
                "--task-id",
                "TASK-RISK-MISSING",
                "--decision",
                str(decision_path),
                "--json",
            )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(payload["status"], "UNKNOWN")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["dimensions"]["local"]["status"], "UNKNOWN")
        self.assertEqual(payload["dimensions"]["remote"]["status"], "UNKNOWN")
        self.assertEqual(
            payload["facts"]["governing_policy_source"],
            "unavailable_pending_installed_manifest",
        )
        self.assertEqual(
            payload["facts"]["candidate_policy_status"], "valid_hint"
        )
        self.assertFalse(
            payload["facts"]["serialized_decision_authoritative"]
        )
        checks = payload["dimensions"]["local"]["checks"]
        self.assertEqual(
            next(
                item["status"]
                for item in checks
                if item["code"] == "RS_AUTHORITY_REQUIRED"
            ),
            "UNKNOWN",
        )

    def test_risk_status_human_uses_closed_interaction_view(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-human")
        with tempfile.TemporaryDirectory() as temporary:
            decision_path = Path(temporary) / "decision.json"
            decision_path.write_text(
                json.dumps(
                    {
                        "interaction": {
                            "recommended_mode": "plan_then_goal",
                            "reason_codes": [
                                "MODE_LONG_RUNNING",
                                "MODE_REQUIRES_PLAN",
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            result = run_cli(
                "risk-status",
                "--repo",
                str(scenario.repo),
                "--decision",
                str(decision_path),
            )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stdout.startswith("UNKNOWN risk-status\n"))
        self.assertIn("local=UNKNOWN", result.stdout)
        self.assertIn("remote=UNKNOWN", result.stdout)
        self.assertIn(
            "interaction_recommended=plan_then_goal", result.stdout
        )
        self.assertIn("interaction_commands=/plan,/goal", result.stdout)
        self.assertIn("automatic_change=false", result.stdout)
        self.assertIn("RS_REMOTE_NOT_OBSERVED ", result.stdout)

    def test_risk_emit_contract_uses_exit_zero_one_two_in_both_formats(
        self,
    ) -> None:
        from control_plane.cli import _emit

        for status, expected in (("PASS", 0), ("FAIL", 1), ("UNKNOWN", 2)):
            payload = {
                "schema_version": 1,
                "command": "risk-status",
                "ok": status == "PASS",
                "status": status,
                "dimensions": {
                    "local": {"status": status, "checks": [], "errors": []},
                    "remote": {"status": "PASS", "checks": [], "errors": []},
                },
                "facts": {
                    "interaction": {
                        "mode": "normal",
                        "commands": [],
                        "human_message": "continue",
                        "automatic_change": False,
                    },
                    "project_profile": {"profiles": ["generic"]},
                },
                "errors": [],
            }
            for as_json in (False, True):
                with self.subTest(status=status, as_json=as_json):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        code = _emit(payload, as_json)
                    self.assertEqual(code, expected)
                    self.assertTrue(output.getvalue())

    def test_risk_json_and_human_share_all_closed_interaction_modes(
        self,
    ) -> None:
        from control_plane.cli import _render_human
        from control_plane.intake import render_interaction_recommendation
        from control_plane.risk_sentinel import evaluate_risk_status

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-interaction")
        cases = (
            ("default", "normal", ["MODE_BOUNDED"]),
            ("plan", "plan", ["MODE_COMPLEX_OR_UNCERTAIN"]),
            ("goal", "goal", ["MODE_LONG_RUNNING"]),
            (
                "plan_then_goal",
                "plan_then_goal",
                ["MODE_LONG_RUNNING", "MODE_REQUIRES_PLAN"],
            ),
        )
        for route_mode, view_mode, reasons in cases:
            with self.subTest(mode=route_mode):
                status = evaluate_risk_status(
                    scenario.repo,
                    None,
                    route_decision_hint={
                        "interaction": {
                            "recommended_mode": route_mode,
                            "reason_codes": reasons,
                        }
                    },
                )
                payload = status.to_dict()
                expected = render_interaction_recommendation(
                    view_mode, reasons
                ).as_dict()
                self.assertEqual(payload["facts"]["interaction"], expected)
                human = _render_human(payload)
                self.assertIn(
                    f"interaction_recommended={view_mode}", human
                )
                self.assertIn(
                    "interaction_commands=" + ",".join(expected["commands"]),
                    human,
                )
                self.assertIn(
                    f"interaction_message={expected['human_message']}", human
                )
                self.assertIn("automatic_change=false", human)

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

    def test_hook_smoke_cli_accepts_only_repo_task_and_output_mode(
        self,
    ) -> None:
        closed = run_cli(
            "hook-smoke",
            "--repo",
            str(ROOT.resolve()),
            "--task-id",
            "TASK-HOOK-SMOKE-CLOSED-CLI",
            "--json",
        )
        injected = run_cli(
            "hook-smoke",
            "--repo",
            str(ROOT.resolve()),
            "--task-id",
            "TASK-HOOK-SMOKE-CLOSED-CLI",
            "--result",
            str(ROOT / "forged-result.json"),
            "--observation",
            str(ROOT / "forged-observation.json"),
            "--json",
        )

        self.assertNotIn("invalid choice", closed.stderr)
        self.assertNotEqual(injected.returncode, 0)
        self.assertIn("unrecognized arguments", injected.stderr)

    def test_git_guard_pre_push_parser_is_bounded_and_closed(self) -> None:
        from control_plane.cli import _read_pre_push_updates

        valid = (
            b"refs/heads/feature/a "
            + b"a" * 40
            + b" refs/heads/feature/a "
            + b"0" * 40
            + b"\n"
            + b"(delete) "
            + b"0" * 40
            + b" refs/heads/feature/b "
            + b"b" * 40
            + b"\n"
        )
        updates = _read_pre_push_updates(io.BytesIO(valid))
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[1][0], "(delete)")

        for payload in (
            b"only three fields\n",
            b"refs/heads/x \xff refs/heads/x " + b"0" * 40 + b"\n",
            b"x" * (1_048_576 + 1),
        ):
            with self.subTest(size=len(payload)):
                with self.assertRaisesRegex(ValueError, "GG_INPUT_INVALID"):
                    _read_pre_push_updates(io.BytesIO(payload))

    def test_git_guard_cli_is_closed_and_fails_without_install(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        result = run_cli(
            "git-guard",
            "pre-commit",
            "--repo",
            str(scenario.repo),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["command"], "git-guard")
        self.assertEqual(payload["event"], "pre-commit")
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["errors"][0]["code"], "GG_INSTALLED_POLICY_INVALID"
        )

    def test_git_guard_human_output_surfaces_non_blocking_drift(self) -> None:
        from control_plane.cli import _render_human

        rendered = _render_human(
            {
                "schema_version": 1,
                "command": "git-guard",
                "ok": True,
                "event": "pre-commit",
                "errors": [],
                "warnings": [
                    {
                        "code": "GG_CANDIDATE_POLICY_DRIFT",
                        "message": "Candidate policy differs.",
                    }
                ],
            }
        )

        self.assertTrue(rendered.startswith("PASS git-guard\n"))
        self.assertIn(
            "WARNING GG_CANDIDATE_POLICY_DRIFT: Candidate policy differs.",
            rendered,
        )

    def test_safe_read_cli_binds_explicit_repo_and_closed_argv(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)

        completed = run_cli(
            "safe-read",
            "--repo",
            str(scenario.repo.resolve()),
            "--",
            "git",
            "status",
            "--short",
        )
        rejected = run_cli(
            "safe-read",
            "--repo",
            str(scenario.repo.resolve()),
            "--",
            "git",
            "-c",
            "alias.status=!echo unsafe",
            "status",
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(rejected.returncode, 126)
        self.assertIn("E_SAFE_READ_ARGV", rejected.stderr)

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
        self.assertIn("clarification_level=high", result.stdout)
        self.assertIn(
            "clarification_status=pending_host_capability", result.stdout
        )
        self.assertIn(
            "clarification_next_action=wait_for_host_capability",
            result.stdout,
        )
        self.assertIn("clarification_ready=false", result.stdout)

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

    def test_task_clarification_status_reemits_only_durable_request_and_view(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore, _atomic_json
        from control_plane.repository import worktree_git_dir

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        state_dir = worktree_git_dir(scenario.repo)
        task_id = "TASK-CLI-CLARIFICATION"
        store = TaskStore(state_dir)
        state = store.start(
            task_id,
            outcome="local_change",
            branch="main",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        prompt_view = {
            "schema_version": 1,
            "request_id": "clarify-cli-request",
            "question_text": "Continue with the safe option?",
            "options": [
                {"id": "safe", "label": "Use safe option"},
                {"id": "stop", "label": "Stop"},
            ],
            "recommended_option_id": "safe",
            "consequence_text": "The task remains blocked until answered.",
        }
        presentation_digest = contract_digest(prompt_view)
        request = {
            "schema_version": 1,
            "request_id": "clarify-cli-request",
            "task_digest": self.digest,
            "session_id": "session-cli-clarification",
            "issue_kind": "decision_approval",
            "severity": "high",
            "question_digest": contract_digest({"question": "cli"}),
            "presentation_digest": presentation_digest,
            "repository_check": {
                "status": "not_checked",
                "evidence_digest": None,
            },
            "option_ids": ["safe", "stop"],
            "recommended_option_id": "safe",
        }
        relative_sidecar = (
            "codex-control-plane/clarification-prompt-views/"
            f"{task_id}/generation-00000001.json"
        )
        sidecar = state_dir / relative_sidecar
        sidecar.parent.mkdir(parents=True)
        sidecar.write_bytes(
            json.dumps(
                prompt_view,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        sidecar.chmod(0o600)
        state.update(
            {
                "state": "clarification_required",
                "clarification_resume_state": "framed",
                "clarification_request": request,
                "clarification_request_digest": contract_digest(request),
                "clarification_prompt_view_path": relative_sidecar,
                "clarification_presentation_digest": presentation_digest,
                "generation": 1,
            }
        )
        _atomic_json(store._path(task_id), state)

        result = run_cli(
            "task",
            "clarification-status",
            "--repo",
            str(scenario.repo),
            "--task-id",
            task_id,
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["task"]["state"], "clarification_required")
        self.assertEqual(payload["task"]["request"], request)
        self.assertEqual(payload["task"]["prompt_view"], prompt_view)
        self.assertNotIn("resolution", payload["task"])

    def test_task_cli_has_no_serialized_clarification_resolution_path(
        self,
    ) -> None:
        result = run_cli(
            "task",
            "clarify",
            "--repo",
            str(ROOT),
            "--task-id",
            "TASK-NO-SERIALIZED-RESOLUTION",
            "--resolution",
            "resolution.json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)

    def test_task_transition_rejects_lateral_clarification_state(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        started = run_cli(
            "task",
            "start",
            "--repo",
            str(scenario.repo),
            "--task-id",
            "TASK-CLI-NO-LATERAL",
            "--outcome",
            "local_change",
            "--branch",
            "main",
            "--task-digest",
            self.digest,
            "--decision-digest",
            self.digest,
            "--json",
        )
        transitioned = run_cli(
            "task",
            "transition",
            "--repo",
            str(scenario.repo),
            "--task-id",
            "TASK-CLI-NO-LATERAL",
            "--state",
            "clarification_required",
            "--json",
        )

        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertNotEqual(transitioned.returncode, 0)
        self.assertIn(
            "E_STATE_LATERAL",
            {
                item["code"]
                for item in json.loads(transitioned.stdout)["errors"]
            },
        )


if __name__ == "__main__":
    unittest.main()
