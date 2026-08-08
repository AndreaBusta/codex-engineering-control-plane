from __future__ import annotations

import copy
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.git_test_support import FIXTURE_POLICY, GitScenario, git
from tests.router_test_support import task_envelope


class RunContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.task = task_envelope(
            task_id="TASK-RUN-001",
            risk={
                "uncertainty": 0,
                "blast_radius": 1,
                "irreversibility": 0,
                "verification_complexity": 1,
            },
        )
        self.decision = {
            "decision_digest": "sha256:" + "1" * 64,
            "decision_ready": True,
            "summary": {
                "tier": "T1",
                "project_profile": {"profiles": ["generic"]},
            },
            "required_gates": ["gate.relevant-tests"],
            "interaction": {
                "clarification_gate": {
                    "level": "low",
                    "status": "autonomous",
                    "decision_ready": True,
                }
            },
        }

    def _plan(self) -> dict[str, object]:
        from control_plane.run_workflow import build_run_plan

        return build_run_plan(
            task=self.task,
            decision=self.decision,
            repository=Path("/tmp/example-repository"),
            branch="codex/example",
            head="a" * 40,
            session_id="session-run-001",
            prepared_at="2026-08-08T10:00:00Z",
        )

    def test_run_plan_is_closed_digest_bound_and_non_authorizing(self) -> None:
        from control_plane.run_workflow import validate_run_plan

        plan = self._plan()

        self.assertEqual(validate_run_plan(plan), [])
        self.assertEqual(plan["kind"], "RunPlanV1")
        self.assertEqual(plan["max_executions"], 3)
        self.assertNotIn("objective", plan)
        self.assertNotIn("prompt", plan)
        self.assertNotIn("authorization", plan)

        tampered = copy.deepcopy(plan)
        tampered["scope_paths"] = ["other/"]
        self.assertEqual(validate_run_plan(tampered)[0].code, "RUN_DIGEST")

        extra = copy.deepcopy(plan)
        extra["authority"] = True
        self.assertEqual(validate_run_plan(extra)[0].code, "RUN_SCHEMA")

    def test_run_plan_requires_autonomous_low_clarification(self) -> None:
        from control_plane.run_workflow import build_run_plan

        blocked = copy.deepcopy(self.decision)
        blocked["interaction"]["clarification_gate"]["status"] = "pending_host_capability"

        with self.assertRaisesRegex(ValueError, "E_RUN_CLARIFICATION"):
            build_run_plan(
                task=self.task,
                decision=blocked,
                repository=Path("/tmp/example-repository"),
                branch="codex/example",
                head="a" * 40,
                session_id="session-run-001",
                prepared_at="2026-08-08T10:00:00Z",
            )

    def test_gate_receipt_accepts_only_three_attempts_and_tristate(self) -> None:
        from control_plane.run_workflow import (
            build_gate_receipt,
            validate_gate_receipt,
        )

        plan = self._plan()
        receipt = build_gate_receipt(
            run_plan=plan,
            attempt=3,
            gate_id="gate.relevant-tests",
            status="PASS",
            command_digest="sha256:" + "2" * 64,
            output_digest="sha256:" + "3" * 64,
            before_snapshot_digest="sha256:" + "4" * 64,
            after_snapshot_digest="sha256:" + "4" * 64,
            error_code=None,
            observed_at="2026-08-08T10:05:00Z",
        )

        self.assertEqual(validate_gate_receipt(receipt), [])
        self.assertNotIn("output", receipt)

        for attempt in (0, 4):
            with self.subTest(attempt=attempt), self.assertRaisesRegex(
                ValueError, "E_RUN_ATTEMPT"
            ):
                build_gate_receipt(
                    run_plan=plan,
                    attempt=attempt,
                    gate_id="gate.relevant-tests",
                    status="PASS",
                    command_digest="sha256:" + "2" * 64,
                    output_digest="sha256:" + "3" * 64,
                    before_snapshot_digest="sha256:" + "4" * 64,
                    after_snapshot_digest="sha256:" + "4" * 64,
                    error_code=None,
                    observed_at="2026-08-08T10:05:00Z",
                )

    def test_review_result_blocks_critical_and_important_findings(self) -> None:
        from control_plane.run_workflow import (
            build_review_result,
            validate_review_result,
        )

        plan = self._plan()
        result = build_review_result(
            run_plan=plan,
            reviewed_head="a" * 40,
            reviewer_kind="independent",
            reviewer_context_digest="sha256:" + "5" * 64,
            critical=0,
            important=1,
            minor=2,
            observed_at="2026-08-08T10:06:00Z",
        )

        self.assertEqual(validate_review_result(result), [])
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["authorizes"])

    def test_run_summary_uses_fail_unknown_pass_precedence(self) -> None:
        from control_plane.run_workflow import build_run_summary, validate_run_summary

        plan = self._plan()
        unknown = build_run_summary(
            run_plan=plan,
            head="a" * 40,
            lifecycle_state="verifying",
            attempt_count=1,
            gate_statuses=("PASS", "UNKNOWN"),
            gate_receipt_digests=("sha256:" + "6" * 64,),
            review_result_digest=None,
            blocked_reason_code=None,
            observed_at="2026-08-08T10:07:00Z",
        )
        failed = build_run_summary(
            run_plan=plan,
            head="a" * 40,
            lifecycle_state="blocked",
            attempt_count=2,
            gate_statuses=("PASS", "UNKNOWN", "FAIL"),
            gate_receipt_digests=("sha256:" + "6" * 64,),
            review_result_digest=None,
            blocked_reason_code="E_TEST_FAILURE",
            observed_at="2026-08-08T10:08:00Z",
        )

        self.assertEqual(validate_run_summary(unknown), [])
        self.assertEqual(unknown["gate_status"], "UNKNOWN")
        self.assertEqual(unknown["visible_status"], "VERIFICANDO")
        self.assertEqual(validate_run_summary(failed), [])
        self.assertEqual(failed["gate_status"], "FAIL")
        self.assertEqual(failed["visible_status"], "BLOCKED")


class RunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.contracts import contract_digest

        self.task = task_envelope(
            task_id="TASK-RUN-STORE-001",
            requested_outcome="local_change",
            scope_paths=["."],
            risk={
                "uncertainty": 0,
                "blast_radius": 1,
                "irreversibility": 0,
                "verification_complexity": 1,
            },
        )
        core = {
            "schema_version": 1,
            "task_id": self.task["task_id"],
            "mode": "audit",
            "ok": True,
            "decision_ready": True,
            "summary": {
                "tier": "T1",
                "project_profile": {"profiles": ["generic"]},
            },
            "documentation": {},
            "interaction": {
                "clarification_gate": {
                    "level": "low",
                    "status": "autonomous",
                    "decision_ready": True,
                }
            },
            "approval_boundaries": [],
            "authorization": {"local_read": True, "local_write": True},
            "required_gates": ["gate.relevant-tests"],
            "selected_resource_digests": {},
            "matched_routes": ["quality-profile-generic"],
            "facts": {"task_digest": contract_digest(self.task)},
            "errors": [],
        }
        self.decision = {**core, "decision_digest": contract_digest(core)}

    def test_plan_write_is_idempotent_and_rejects_replay_drift(self) -> None:
        from control_plane.run_workflow import RunStore, build_run_plan

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RunStore(root)
            plan = build_run_plan(
                task=self.task,
                decision=self.decision,
                repository=Path("/tmp/example-repository"),
                branch="codex/example",
                head="a" * 40,
                session_id="session-run-store-001",
                prepared_at="2026-08-08T10:00:00Z",
            )
            changed = build_run_plan(
                task=self.task,
                decision=self.decision,
                repository=Path("/tmp/example-repository"),
                branch="codex/example",
                head="a" * 40,
                session_id="session-run-store-001",
                prepared_at="2026-08-08T10:00:01Z",
            )

            self.assertEqual(store.write_plan(plan), plan)
            self.assertEqual(store.write_plan(plan), plan)
            with self.assertRaisesRegex(ValueError, "E_RUN_REPLAY"):
                store.write_plan(changed)

    def test_prepare_run_reuses_lifecycle_and_writer_lease(self) -> None:
        from control_plane.lifecycle import TaskStore
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import prepare_run

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/run-prepare")

        result = prepare_run(
            task=self.task,
            decision=self.decision,
            repository=scenario.repo,
            policy=load_policy(FIXTURE_POLICY),
            session_id="session-run-prepare-001",
            prepared_at="2026-08-08T10:00:00Z",
        )

        state_dir = worktree_git_dir(scenario.repo)
        state = TaskStore(state_dir).status(self.task["task_id"])
        lease_path = (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{self.task['task_id']}.json"
        )
        self.assertEqual(state["state"], "implementing")
        self.assertEqual(result["task"], state)
        self.assertTrue(lease_path.is_file())
        self.assertEqual(result["run_plan"]["max_executions"], 3)
        repeated = prepare_run(
            task=self.task,
            decision=self.decision,
            repository=scenario.repo,
            policy=load_policy(FIXTURE_POLICY),
            session_id="session-run-prepare-001",
            prepared_at="2026-08-08T10:00:01Z",
        )
        self.assertEqual(repeated["run_plan"], result["run_plan"])

    def test_prepare_run_rejects_serialized_decision_without_local_write(self) -> None:
        from control_plane.policy import load_policy
        from control_plane.run_workflow import prepare_run

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/run-denied")
        denied = copy.deepcopy(self.decision)
        denied["authorization"]["local_write"] = False
        from control_plane.contracts import contract_digest

        semantic = {
            key: value
            for key, value in denied.items()
            if key != "decision_digest"
        }
        denied["decision_digest"] = contract_digest(semantic)

        with self.assertRaisesRegex(ValueError, "E_RUN_AUTHORITY"):
            prepare_run(
                task=self.task,
                decision=denied,
                repository=scenario.repo,
                policy=load_policy(FIXTURE_POLICY),
                session_id="session-run-denied-001",
                prepared_at="2026-08-08T10:00:00Z",
            )

    def _stored_plan(self, store: object) -> dict[str, object]:
        from control_plane.run_workflow import build_run_plan

        plan = build_run_plan(
            task=self.task,
            decision=self.decision,
            repository=Path("/tmp/example-repository"),
            branch="codex/example",
            head="a" * 40,
            session_id="session-run-store-001",
            prepared_at="2026-08-08T10:00:00Z",
        )
        return store.write_plan(plan)

    def _receipt(
        self, plan: dict[str, object], attempt: int, status: str
    ) -> dict[str, object]:
        from control_plane.run_workflow import build_gate_receipt

        return build_gate_receipt(
            run_plan=plan,
            attempt=attempt,
            gate_id="gate.relevant-tests",
            status=status,
            command_digest="sha256:" + "2" * 64,
            output_digest="sha256:" + "3" * 64,
            before_snapshot_digest="sha256:" + "4" * 64,
            after_snapshot_digest="sha256:" + "4" * 64,
            error_code=None if status == "PASS" else "E_TEST_FAILURE",
            observed_at=f"2026-08-08T10:0{attempt}:00Z",
        )

    def test_attempt_policy_blocks_unknown_and_repeated_failure(self) -> None:
        from control_plane.run_workflow import RunStore

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            first = store.record_attempt(
                run_plan=plan,
                attempt=1,
                head="a" * 40,
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 1, "FAIL"),),
                failure_reason_code="E_TEST_FAILURE",
                observed_at="2026-08-08T10:01:00Z",
            )
            second = store.record_attempt(
                run_plan=plan,
                attempt=2,
                head="a" * 40,
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 2, "FAIL"),),
                failure_reason_code="E_TEST_FAILURE",
                observed_at="2026-08-08T10:02:00Z",
            )

        self.assertTrue(first["retry_allowed"])
        self.assertFalse(first["blocked"])
        self.assertFalse(second["retry_allowed"])
        self.assertTrue(second["blocked"])
        self.assertEqual(second["stop_reason_code"], "E_RUN_REPEATED_FAILURE")

    def test_attempt_policy_blocks_unknown_immediately(self) -> None:
        from control_plane.run_workflow import RunStore

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            attempt = store.record_attempt(
                run_plan=plan,
                attempt=1,
                head="a" * 40,
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 1, "UNKNOWN"),),
                failure_reason_code="E_TEST_UNKNOWN",
                observed_at="2026-08-08T10:01:00Z",
            )

        self.assertTrue(attempt["blocked"])
        self.assertEqual(attempt["stop_reason_code"], "E_RUN_UNKNOWN")

    def test_attempt_policy_blocks_scope_growth(self) -> None:
        from control_plane.run_workflow import RunStore

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            store.record_attempt(
                run_plan=plan,
                attempt=1,
                head="a" * 40,
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 1, "FAIL"),),
                failure_reason_code="E_TEST_FAILURE",
                observed_at="2026-08-08T10:01:00Z",
            )
            grown = store.record_attempt(
                run_plan=plan,
                attempt=2,
                head="a" * 40,
                changed_paths=(
                    "control_plane/run_workflow.py",
                    "tests/test_run_workflow.py",
                ),
                receipts=(self._receipt(plan, 2, "FAIL"),),
                failure_reason_code="E_DIFFERENT_FAILURE",
                observed_at="2026-08-08T10:02:00Z",
            )

        self.assertTrue(grown["blocked"])
        self.assertEqual(grown["stop_reason_code"], "E_RUN_SCOPE_GROWTH")

    def test_attempt_replay_drift_is_rejected(self) -> None:
        from control_plane.run_workflow import RunStore

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            arguments = {
                "run_plan": plan,
                "attempt": 1,
                "head": "a" * 40,
                "changed_paths": ("control_plane/run_workflow.py",),
                "receipts": (self._receipt(plan, 1, "PASS"),),
                "failure_reason_code": None,
                "observed_at": "2026-08-08T10:01:00Z",
            }
            first = store.record_attempt(**arguments)
            self.assertEqual(store.record_attempt(**arguments), first)
            arguments["changed_paths"] = ("tests/test_run_workflow.py",)
            with self.assertRaisesRegex(ValueError, "E_RUN_REPLAY"):
                store.record_attempt(**arguments)


class RunVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        RunStoreTests.setUp(self)

    def _prepared_scenario(
        self,
        *,
        gate_exit: int = 0,
        create_change: bool = True,
        include_python_test: bool = True,
        empty_python_test: bool = False,
    ) -> GitScenario:
        from control_plane.policy import load_policy
        from control_plane.run_workflow import prepare_run

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/run-verify")
        (scenario.repo / ".codex").mkdir()
        shutil.copy2(
            FIXTURE_POLICY, scenario.repo / ".codex" / "project-policy.toml"
        )
        (scenario.repo / "scripts").mkdir()
        launcher = scenario.repo / "scripts" / "control-plane"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)
        (scenario.repo / "tests").mkdir()
        (scenario.repo / "tests" / "__init__.py").write_text(
            "", encoding="utf-8"
        )
        if include_python_test:
            source = "VALUE = 1\n" if empty_python_test else (
                "import unittest\n\n"
                "class FixtureTests(unittest.TestCase):\n"
                "    def test_fixture(self):\n"
                "        self.assertTrue(True)\n"
            )
            (scenario.repo / "tests" / "test_fixture.py").write_text(
                source, encoding="utf-8"
            )
        git(scenario.repo, "add", ".codex", "scripts", "tests")
        git(scenario.repo, "commit", "-m", "test: closed verification fixture")
        prepare_run(
            task=self.task,
            decision=self.decision,
            repository=scenario.repo,
            policy=load_policy(FIXTURE_POLICY),
            session_id="session-run-verify-001",
            prepared_at="2026-08-08T10:00:00Z",
        )
        if gate_exit:
            launcher.write_text(
                f"#!/bin/sh\nexit {gate_exit}\n", encoding="utf-8"
            )
            launcher.chmod(0o755)
        if create_change:
            (scenario.repo / "change.txt").write_text(
                "change\n", encoding="utf-8"
            )
        return scenario

    def test_verify_run_executes_closed_profile_and_reaches_review_ready(self) -> None:
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import verify_run

        scenario = self._prepared_scenario()

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        state = TaskStore(worktree_git_dir(scenario.repo)).status(
            self.task["task_id"]
        )
        self.assertEqual(state["state"], "review_ready")
        self.assertEqual(result["summary"]["gate_status"], "PASS")
        self.assertEqual(result["summary"]["attempt_count"], 1)
        self.assertEqual(len(result["receipts"]), 5)

    def test_failed_gate_allows_one_repair_then_success(self) -> None:
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import verify_run

        scenario = self._prepared_scenario(gate_exit=1)
        first = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertTrue(first["attempt"]["retry_allowed"])
        self.assertEqual(first["task"]["state"], "verifying")
        self.assertEqual(
            first["receipts"][-1]["error_code"],
            "E_RUN_GATE_POLICY_CHECK_FAILED",
        )

        launcher = scenario.repo / "scripts" / "control-plane"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)
        second = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:02:00Z",
        )

        state = TaskStore(worktree_git_dir(scenario.repo)).status(
            self.task["task_id"]
        )
        self.assertEqual(second["summary"]["gate_status"], "PASS")
        self.assertEqual(second["summary"]["attempt_count"], 2)
        self.assertEqual(state["state"], "review_ready")

    def test_relevant_tests_selects_bounded_node_unit_runner(self) -> None:
        from control_plane.run_workflow import _local_gate_commands

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "scripts").mkdir()
            (repository / "tests" / "unit").mkdir(parents=True)
            (repository / "package.json").write_text(
                '{"scripts":{"test:unit":"node ./scripts/run-unit-tests.mjs"}}\n',
                encoding="utf-8",
            )
            runner = repository / "scripts" / "run-unit-tests.mjs"
            runner.write_text("process.exit(0);\n", encoding="utf-8")
            (repository / "tests" / "unit" / "sample.spec.js").write_text(
                "export {};\n", encoding="utf-8"
            )

            commands = _local_gate_commands(
                repository,
                profiles=("web_pwa",),
                changed_paths=("docs/strategy.md",),
            )

        self.assertEqual(len(commands[0]), 3)
        gate_id, argv, command_plan = commands[0]
        self.assertEqual(gate_id, "gate.relevant-tests")
        self.assertEqual(Path(argv[0]).name, "node")
        self.assertEqual(argv[1], str(runner.resolve()))
        self.assertEqual(command_plan, ((argv, (0,)),))

    def test_relevant_tests_selects_python_for_python_diff_in_web_repo(self) -> None:
        from control_plane.run_workflow import _local_gate_commands

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self._write_hybrid_test_surfaces(repository)

            commands = _local_gate_commands(
                repository,
                profiles=("web_pwa",),
                changed_paths=("control_plane/runtime.py",),
            )

        _, argv, command_plan = commands[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(command_plan, ((argv, (0,)),))

    def test_relevant_tests_selects_node_for_node_diff_in_generic_repo(self) -> None:
        from control_plane.run_workflow import _local_gate_commands

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            runner = self._write_hybrid_test_surfaces(repository)

            commands = _local_gate_commands(
                repository,
                profiles=("generic",),
                changed_paths=("src/application.js",),
            )

        _, argv, command_plan = commands[0]
        self.assertEqual(Path(argv[0]).name, "node")
        self.assertEqual(argv[1], str(runner.resolve()))
        self.assertEqual(command_plan, ((argv, (0,)),))

    def test_relevant_tests_runs_both_runners_for_mixed_diff(self) -> None:
        from control_plane.run_workflow import _local_gate_commands

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            runner = self._write_hybrid_test_surfaces(repository)

            commands = _local_gate_commands(
                repository,
                profiles=("web_pwa",),
                changed_paths=("control_plane/runtime.py", "src/application.js"),
            )

        _, argv, command_plan = commands[0]
        self.assertEqual(argv[:2], (sys.executable, "-c"))
        self.assertEqual(len(command_plan), 2)
        self.assertEqual(command_plan[0][0], argv)
        self.assertEqual(Path(command_plan[1][0][0]).name, "node")
        self.assertEqual(command_plan[1][0][1], str(runner.resolve()))
        self.assertEqual(command_plan[0][1], (0,))
        self.assertEqual(command_plan[1][1], (0,))

    def _write_hybrid_test_surfaces(self, repository: Path) -> Path:
        (repository / "scripts").mkdir()
        (repository / "tests" / "unit").mkdir(parents=True)
        (repository / "package.json").write_text(
            '{"scripts":{"test:unit":"node ./scripts/run-unit-tests.mjs"}}\n',
            encoding="utf-8",
        )
        runner = repository / "scripts" / "run-unit-tests.mjs"
        runner.write_text("process.exit(0);\n", encoding="utf-8")
        (repository / "tests" / "unit" / "sample.spec.js").write_text(
            "export {};\n", encoding="utf-8"
        )
        (repository / "tests" / "test_runtime.py").write_text(
            "import unittest\n\n"
            "class RuntimeTests(unittest.TestCase):\n"
            "    def test_runtime(self):\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        return runner

    def test_relevant_tests_fails_closed_when_discovery_finds_zero_tests(self) -> None:
        from control_plane.run_workflow import verify_run

        scenario = self._prepared_scenario(include_python_test=False)

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(result["task"]["state"], "verifying")
        self.assertEqual(result["summary"]["gate_status"], "FAIL")
        self.assertEqual(
            result["receipts"][0]["error_code"],
            "E_RUN_RELEVANT_TESTS_UNAVAILABLE",
        )

    def test_relevant_tests_fails_when_python_runner_executes_zero_tests(self) -> None:
        from control_plane.run_workflow import verify_run

        scenario = self._prepared_scenario(empty_python_test=True)

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(result["task"]["state"], "verifying")
        self.assertEqual(result["summary"]["gate_status"], "FAIL")
        self.assertEqual(
            result["receipts"][0]["error_code"],
            "E_RUN_GATE_RELEVANT_TESTS_FAILED",
        )

    def test_closed_node_gate_can_relaunch_the_selected_node_binary(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import _execute_closed_gate, RunStore

        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        scenario = self._prepared_scenario()
        runner = scenario.repo / "node-relaunch.mjs"
        runner.write_text(
            "import { spawnSync } from 'node:child_process';\n"
            "const child = spawnSync('node', ['-e', 'process.exit(0)']);\n"
            "process.exit(child.error ? 90 : child.status);\n",
            encoding="utf-8",
        )
        state_dir = worktree_git_dir(scenario.repo)
        plan = RunStore(state_dir).load_plan(self.task["task_id"])

        receipt = _execute_closed_gate(
            repository=scenario.repo,
            state_dir=state_dir,
            run_plan=plan,
            attempt=1,
            gate_id="gate.relevant-tests",
            argv=(str(Path(node).resolve()), str(runner)),
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(receipt["status"], "PASS")

    def test_closed_gate_path_excludes_repository_executable_directories(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import _execute_closed_gate, RunStore

        scenario = self._prepared_scenario()
        launcher = scenario.repo / "scripts" / "control-plane"
        launcher.write_text(
            "#!/bin/sh\n"
            "case \"${PATH}:\" in\n"
            "  *\"$(pwd)/scripts:\"*) exit 91 ;;\n"
            "  *) exit 0 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
        state_dir = worktree_git_dir(scenario.repo)
        plan = RunStore(state_dir).load_plan(self.task["task_id"])

        receipt = _execute_closed_gate(
            repository=scenario.repo,
            state_dir=state_dir,
            run_plan=plan,
            attempt=1,
            gate_id="gate.policy-check",
            argv=(str(launcher.resolve()),),
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(receipt["status"], "PASS")

    def test_diff_review_rejects_trailing_whitespace_in_untracked_file(self) -> None:
        from control_plane.run_workflow import verify_run

        scenario = self._prepared_scenario()
        (scenario.repo / "change.txt").write_text(
            "trailing whitespace  \n", encoding="utf-8"
        )

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(result["task"]["state"], "verifying")
        self.assertEqual(result["receipts"][-1]["gate_id"], "gate.diff-review")
        self.assertEqual(result["receipts"][-1]["status"], "FAIL")

    def test_diff_review_rejects_trailing_whitespace_already_staged(self) -> None:
        from control_plane.run_workflow import verify_run

        scenario = self._prepared_scenario()
        (scenario.repo / "change.txt").write_text(
            "trailing whitespace  \n", encoding="utf-8"
        )
        git(scenario.repo, "add", "change.txt")

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(result["task"]["state"], "verifying")
        self.assertEqual(result["receipts"][-1]["gate_id"], "gate.diff-review")
        self.assertEqual(result["receipts"][-1]["status"], "FAIL")

    def test_required_gate_without_bound_receipt_blocks_promotion(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import verify_run

        self.decision["required_gates"] = [
            "gate.independent-review",
            "gate.relevant-tests",
        ]
        semantic = {
            key: value
            for key, value in self.decision.items()
            if key != "decision_digest"
        }
        self.decision["decision_digest"] = contract_digest(semantic)
        scenario = self._prepared_scenario()

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(result["task"]["state"], "blocked")
        self.assertEqual(result["summary"]["gate_status"], "UNKNOWN")
        self.assertEqual(
            result["receipts"][-1]["gate_id"], "gate.independent-review"
        )
        self.assertEqual(
            result["receipts"][-1]["error_code"],
            "E_RUN_REQUIRED_GATE_UNPROVEN",
        )

    def test_verify_run_blocks_a_local_change_without_a_diff(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import verify_run

        scenario = self._prepared_scenario(create_change=False)

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(result["task"]["state"], "blocked")
        self.assertEqual(result["attempt"]["stop_reason_code"], "E_RUN_NO_CHANGE")
        self.assertEqual(result["summary"]["gate_status"], "FAIL")
        lease = (
            worktree_git_dir(scenario.repo)
            / "codex-control-plane"
            / "leases"
            / f"{self.task['task_id']}.json"
        )
        self.assertFalse(lease.exists())

    def test_gate_deadline_bounds_a_descendant_holding_stdout(self) -> None:
        from unittest.mock import patch

        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import _execute_closed_gate, RunStore

        scenario = self._prepared_scenario()
        state_dir = worktree_git_dir(scenario.repo)
        plan = RunStore(state_dir).load_plan(self.task["task_id"])
        command = (
            sys.executable,
            "-c",
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(2)'], "
            "stdout=sys.stdout, start_new_session=True)",
        )
        started = time.monotonic()
        with patch("control_plane.run_workflow._GATE_TIMEOUT_SECONDS", 0.2):
            receipt = _execute_closed_gate(
                repository=scenario.repo,
                state_dir=state_dir,
                run_plan=plan,
                attempt=1,
                gate_id="gate.relevant-tests",
                argv=command,
                observed_at="2026-08-08T10:01:00Z",
            )

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(receipt["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
