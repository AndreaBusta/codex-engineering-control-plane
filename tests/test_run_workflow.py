from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests.git_test_support import (
    FIXTURE_POLICY,
    GitScenario,
    git,
    install_external_diff_driver,
)
from tests.host_adapter_test_support import independent_review_receipt
from tests.router_test_support import task_envelope


def _review_packet_for_test(
    plan: dict[str, object], revision: dict[str, object], attempt: int, *,
    criteria: str = "sha256:" + "9" * 64,
    attempt_digest: str = "sha256:" + "8" * 64,
    diff_digest: str = "sha256:" + "7" * 64,
) -> dict[str, object]:
    """Synthetic closed packet for revision-lineage tests, never persisted."""
    from control_plane.contracts import contract_digest
    from control_plane.run_workflow import _build_review_packet
    artifact = {
        "schema_version": 1, "kind": "StableReviewDiffArtifactV1",
        "task_id": plan["task_id"], "attempt": attempt,
        "repository": plan["repository"], "base_head": "b" * 40,
        "reviewed_head": revision["head"],
        "scope_paths": ["control_plane/run_workflow.py"],
        "untracked_modes": [],
        "diff_digest": diff_digest, "diff_size": 1,
        "authorizes": False,
    }
    artifact["artifact_digest"] = contract_digest(artifact)
    record = {"attempt": attempt, "attempt_digest": attempt_digest}
    summaries = [{
        "kind": "ReviewCheckSummaryV1", "check_kind": "test",
        "check_id": "gate.relevant-tests", "status": "PASS",
        "argv_digest": "sha256:" + "1" * 64,
        "output_digest": "sha256:" + "2" * 64,
        "receipt_digest": "sha256:" + "3" * 64,
    }]
    return _build_review_packet(run_plan=plan, run_revision=revision,
        attempt_record=record, artifact=artifact, review_kind="independent",
        criteria_digest=criteria, evidence_summaries=summaries)


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
            "approval_boundaries": [],
            "authorization": {"local_write": True},
            "errors": [],
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

    def test_run_plan_keeps_remote_outcome_as_non_authorizing_deferred_effects(
        self,
    ) -> None:
        from control_plane.run_workflow import build_run_plan, validate_run_plan

        task = copy.deepcopy(self.task)
        task["requested_outcome"] = "pull_request"
        task["effects"] = [
            {"name": "local_write", "source": "user_explicit"},
            {"name": "commit", "source": "user_explicit"},
            {"name": "remote_write", "source": "user_explicit"},
            {"name": "pull_request", "source": "user_explicit"},
        ]
        decision = copy.deepcopy(self.decision)
        decision["decision_ready"] = False
        decision["approval_boundaries"] = [
            "commit", "remote_write", "pull_request",
        ]
        decision["authorization"] = {"local_write": True}
        semantic = {
            key: value for key, value in decision.items()
            if key != "decision_digest"
        }
        from control_plane.contracts import contract_digest
        decision["decision_digest"] = contract_digest(semantic)

        plan = build_run_plan(
            task=task,
            decision=decision,
            repository=Path("/tmp/example-repository"),
            branch="codex/example",
            head="a" * 40,
            session_id="session-run-001",
            prepared_at="2026-08-08T10:00:00Z",
        )

        self.assertEqual(validate_run_plan(plan), [])
        self.assertEqual(plan["requested_outcome"], "pull_request")
        self.assertEqual(
            plan["deferred_effects"],
            ["commit", "remote_write", "pull_request"],
        )
        self.assertNotIn("authorization", plan)

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

    def test_t3_run_plan_requires_independent_and_security_review(self) -> None:
        self.decision["summary"]["tier"] = "T3"
        self.decision["required_gates"] = ["gate.independent-review"]

        with self.assertRaisesRegex(ValueError, "E_RUN_BINDING"):
            self._plan()

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
        self.assertEqual(
            state["active_run_revision_digest"],
            result["run_revision"]["revision_digest"],
        )
        self.assertEqual(result["run_revision"]["first_attempt"], 1)
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

    def test_prepare_run_accepts_pull_request_with_only_future_effects_deferred(
        self,
    ) -> None:
        from control_plane.policy import load_policy
        from control_plane.run_workflow import prepare_run
        from control_plane.contracts import contract_digest

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/run-pr-deferred")
        task = copy.deepcopy(self.task)
        task["requested_outcome"] = "pull_request"
        task["effects"] = [
            {"name": "local_write", "source": "user_explicit"},
            {"name": "commit", "source": "user_explicit"},
            {"name": "remote_write", "source": "user_explicit"},
            {"name": "pull_request", "source": "user_explicit"},
        ]
        decision = copy.deepcopy(self.decision)
        decision["facts"] = {"task_digest": contract_digest(task)}
        decision["decision_ready"] = False
        decision["approval_boundaries"] = [
            "commit", "remote_write", "pull_request",
        ]
        decision["authorization"] = {"local_write": True}
        decision["decision_digest"] = contract_digest(
            {key: value for key, value in decision.items() if key != "decision_digest"}
        )

        prepared = prepare_run(
            task=task,
            decision=decision,
            repository=scenario.repo,
            policy=load_policy(FIXTURE_POLICY),
            session_id="session-run-pr-deferred-001",
            prepared_at="2026-08-08T10:00:00Z",
        )

        self.assertEqual(prepared["task"]["outcome"], "pull_request")
        self.assertEqual(
            prepared["run_plan"]["deferred_effects"],
            ["commit", "remote_write", "pull_request"],
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
        stored = store.write_plan(plan)
        store.write_initial_revision(stored)
        return stored

    def _active_revision(self, store: object, plan: dict[str, object]) -> dict[str, object]:
        return store.load_active(str(plan["task_id"]))

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
                run_revision=self._active_revision(store, plan),
                attempt=1,
                head="a" * 40,
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 1, "FAIL"),),
                failure_reason_code="E_TEST_FAILURE",
                observed_at="2026-08-08T10:01:00Z",
            )
            second = store.record_attempt(
                run_plan=plan,
                run_revision=self._active_revision(store, plan),
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
                run_revision=self._active_revision(store, plan),
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
                run_revision=self._active_revision(store, plan),
                attempt=1,
                head="a" * 40,
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 1, "FAIL"),),
                failure_reason_code="E_TEST_FAILURE",
                observed_at="2026-08-08T10:01:00Z",
            )
            grown = store.record_attempt(
                run_plan=plan,
                run_revision=self._active_revision(store, plan),
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
                "run_revision": self._active_revision(store, plan),
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

    def test_concurrent_conflicting_attempt_has_one_lineage_and_no_orphan_receipt(
        self,
    ) -> None:
        from unittest.mock import patch

        from control_plane.run_workflow import (
            RunStore,
            build_gate_receipt,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RunStore(root)
            plan = self._stored_plan(store)
            revision = self._active_revision(store, plan)
            receipts = tuple(
                build_gate_receipt(
                    run_plan=plan,
                    attempt=1,
                    gate_id="gate.relevant-tests",
                    status="PASS",
                    command_digest="sha256:" + character * 64,
                    output_digest="sha256:" + character * 64,
                    before_snapshot_digest="sha256:" + "4" * 64,
                    after_snapshot_digest="sha256:" + "4" * 64,
                    error_code=None,
                    observed_at="2026-08-08T10:01:00Z",
                )
                for character in ("2", "3")
            )
            rendezvous = threading.Barrier(2)
            original_write_receipt = RunStore._write_receipt

            def synchronized_write_receipt(
                current: RunStore,
                task_id: str,
                receipt: dict[str, object],
            ) -> None:
                try:
                    rendezvous.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass
                original_write_receipt(current, task_id, receipt)

            results: list[dict[str, object]] = []
            errors: list[BaseException] = []
            result_lock = threading.Lock()

            def record(receipt: dict[str, object]) -> None:
                try:
                    result = RunStore(root).record_attempt(
                        run_plan=plan,
                        run_revision=revision,
                        attempt=1,
                        head="a" * 40,
                        changed_paths=("control_plane/run_workflow.py",),
                        receipts=(receipt,),
                        failure_reason_code=None,
                        observed_at="2026-08-08T10:01:00Z",
                    )
                except BaseException as error:
                    with result_lock:
                        errors.append(error)
                else:
                    with result_lock:
                        results.append(result)

            with patch.object(
                RunStore, "_write_receipt", synchronized_write_receipt
            ):
                workers = [
                    threading.Thread(target=record, args=(receipt,), daemon=True)
                    for receipt in receipts
                ]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(timeout=3)

            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            self.assertRegex(str(errors[0]), "E_RUN_REPLAY")
            attempts = store.attempts(str(plan["task_id"]))
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0], results[0])
            receipt_files = list(
                (store._directory(str(plan["task_id"])) / "receipts").glob(
                    "*.json"
                )
            )
            self.assertEqual(len(receipt_files), 1)
            self.assertEqual(
                receipt_files[0].stem,
                results[0]["gate_receipt_digests"][0].removeprefix("sha256:"),
            )

    def test_revision_store_is_closed_replay_safe_and_loads_active(self) -> None:
        from control_plane.run_workflow import RunStore

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            initial = self._active_revision(store, plan)
            self.assertEqual(initial["revision"], 0)
            self.assertEqual(initial["first_attempt"], 1)
            self.assertEqual(initial["head"], plan["head"])
            self.assertEqual(
                store.load_revision(str(plan["task_id"]), initial["revision_digest"]),
                initial,
            )
            self.assertEqual(store.write_initial_revision(plan), initial)
            with self.assertRaisesRegex(ValueError, "E_RUN_ATTEMPT"):
                store.record_attempt(
                    run_plan=plan, run_revision=initial, attempt=1,
                    head="b" * 40,
                    changed_paths=("control_plane/run_workflow.py",),
                    receipts=(self._receipt(plan, 1, "FAIL"),),
                    failure_reason_code="E_TEST_FAILURE",
                    observed_at="2026-08-08T10:01:00Z",
                )

    def test_revision_append_binds_exact_passed_attempt_and_review_receipt(self) -> None:
        from control_plane.run_workflow import (
            RunStore,
            build_independent_review_receipt,
            build_run_revision,
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            parent = self._active_revision(store, plan)
            attempt = store.record_attempt(
                run_plan=plan,
                run_revision=parent,
                attempt=1,
                head="a" * 40,
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 1, "PASS"),),
                failure_reason_code=None,
                observed_at="2026-08-08T10:01:00Z",
            )
            packet = _review_packet_for_test(plan, parent, 1, attempt_digest=attempt["attempt_digest"])
            receipt = build_independent_review_receipt(
                review_packet=packet, criteria_digest="sha256:" + "9" * 64,
                findings_digest="sha256:" + "a" * 64, critical=1, important=0, status="FAIL",
                observed_at="2026-08-08T10:02:00Z",
            )
            revised = build_run_revision(
                run_plan=plan,
                revision=1,
                first_attempt=2,
                head=parent["head"],
                reason="review_findings",
                parent_revision_digest=parent["revision_digest"],
                source_attempt_digest=attempt["attempt_digest"],
                source_review_receipt_digest=receipt["receipt_digest"],
                source_diff_digest=receipt["diff_digest"],
            )
            store.write_review_revision(revised)
            self.assertEqual(revised["revision"], 1)
            self.assertEqual(revised["first_attempt"], 2)
            self.assertEqual(revised["parent_revision_digest"], parent["revision_digest"])
            self.assertEqual(revised["source_attempt_digest"], attempt["attempt_digest"])
            self.assertEqual(revised["source_diff_digest"], receipt["diff_digest"])
            self.assertIs(revised["authorizes"], False)
            with self.assertRaisesRegex(ValueError, "E_RUN_REVISION"):
                store.record_attempt(
                    run_plan=plan, run_revision=parent, attempt=2,
                    head=parent["head"],
                    changed_paths=("control_plane/run_workflow.py",),
                    receipts=(self._receipt(plan, 2, "FAIL"),),
                    failure_reason_code="E_TEST_FAILURE",
                    observed_at="2026-08-08T10:03:00Z",
                )

    def test_revision_store_rejects_drift_unsafe_paths_and_a_third_correction(self) -> None:
        from control_plane.run_workflow import RunStore

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            revision_path = (
                Path(temporary) / "codex-control-plane" / "runs"
                / str(plan["task_id"]) / "revisions" / "revision-00.json"
            )
            revision_path.unlink()
            revision_path.symlink_to(Path(temporary) / "outside.json")
            with self.assertRaisesRegex(ValueError, "E_RUN_STATE"):
                store.load_active(str(plan["task_id"]))

    def test_revision_store_allows_only_two_review_corrections(self) -> None:
        from control_plane.run_workflow import (
            RunStore,
            build_run_revision,
            build_independent_review_receipt,
        )

        def failed_receipt(plan: dict[str, object], attempt: int, head: str) -> dict[str, object]:
            from control_plane.contracts import contract_digest

            revision = store.load_active(str(plan["task_id"]))
            latest = store.attempts(str(plan["task_id"]))[-1]
            packet = _review_packet_for_test(plan, revision, attempt, criteria="sha256:" + "c" * 64, attempt_digest=latest["attempt_digest"])
            return build_independent_review_receipt(
                review_packet=packet, criteria_digest="sha256:" + "c" * 64,
                findings_digest="sha256:" + "d" * 64, critical=1, important=0, status="FAIL",
                observed_at=f"2026-08-08T10:0{attempt}:30Z",
            )

        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            plan = self._stored_plan(store)
            parent = self._active_revision(store, plan)
            for attempt_number in (1, 2):
                attempt = store.record_attempt(
                    run_plan=plan, run_revision=parent, attempt=attempt_number,
                    head=parent["head"],
                    changed_paths=("control_plane/run_workflow.py",),
                    receipts=(self._receipt(plan, attempt_number, "PASS"),),
                    failure_reason_code=None,
                    observed_at=f"2026-08-08T10:0{attempt_number}:00Z",
                )
                receipt = failed_receipt(
                    plan, attempt_number, attempt["head"]
                )
                parent = build_run_revision(
                    run_plan=plan,
                    revision=attempt_number,
                    first_attempt=attempt_number + 1,
                    head=parent["head"],
                    reason="review_findings",
                    parent_revision_digest=parent["revision_digest"],
                    source_attempt_digest=attempt["attempt_digest"],
                    source_review_receipt_digest=receipt["receipt_digest"],
                    source_diff_digest=receipt["diff_digest"],
                )
                store.write_review_revision(parent)
            final = store.record_attempt(
                run_plan=plan, run_revision=parent, attempt=3, head=parent["head"],
                changed_paths=("control_plane/run_workflow.py",),
                receipts=(self._receipt(plan, 3, "PASS"),),
                failure_reason_code=None,
                observed_at="2026-08-08T10:03:00Z",
            )
            final_receipt = failed_receipt(plan, 3, final["head"])
            with self.assertRaisesRegex(ValueError, "E_RUN_REVISION"):
                build_run_revision(
                    run_plan=plan,
                    revision=3,
                    first_attempt=4,
                    head=parent["head"],
                    reason="review_findings",
                    parent_revision_digest=parent["revision_digest"],
                    source_attempt_digest=final["attempt_digest"],
                    source_review_receipt_digest=final_receipt[
                        "receipt_digest"
                    ],
                    source_diff_digest=final_receipt["diff_digest"],
                )


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

    def test_review_capture_never_executes_external_diff_driver(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        marker = install_external_diff_driver(
            scenario.repo,
            scenario.root,
            tracked_path="baseline.txt",
            driver_name="review-subject-driver",
        )

        captured = ReviewArtifactStore._capture_git(
            scenario.repo,
            ("diff", "HEAD", "--", "baseline.txt"),
        )

        self.assertFalse(marker.exists())
        self.assertIn(b"changed through builtin diff", captured)

    def _persist_t3_rollback_plan(self, scenario: GitScenario) -> dict[str, object]:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, build_rollback_plan
        from tests.host_adapter_test_support import rollback_plan_observation

        runs = RunStore(worktree_git_dir(scenario.repo))
        plan, revision = runs.load_plan(self.task["task_id"]), runs.load_active(
            self.task["task_id"]
        )
        observation = rollback_plan_observation(
            run_plan=plan,
            run_revision=revision,
            attempt=runs.next_attempt(self.task["task_id"]),
            trigger_conditions=(("verification_or_review_regresses", "gate status is not PASS"),),
            rollback_steps=((1, "restore prior tracked content", "owned scope", "git diff matches prior tree"),),
            post_rollback_checks=(("gate.diff-review", "PASS"),),
            irreversible_boundaries=(("remote effects", "none are authorized by this plan"),),
            status="PASS",
        )
        rollback_plan = build_rollback_plan(
            run_plan=plan,
            run_revision=revision,
            attempt=runs.next_attempt(self.task["task_id"]),
            observation=observation,
        )
        return runs.persist_rollback_plan(
            rollback_plan, observation=observation
        )

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

    def test_t1_commit_pass_publishes_delivery_ready_binding_and_releases_writer(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import verify_run

        self.task["requested_outcome"] = "commit"
        self.task["effects"] = [
            {"name": "local_read", "source": "model_inference"},
            {"name": "local_write", "source": "user_explicit"},
            {"name": "commit", "source": "user_explicit"},
        ]
        self.decision["facts"] = {"task_digest": contract_digest(self.task)}
        self.decision["approval_boundaries"] = ["commit"]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()

        verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        state_dir = worktree_git_dir(scenario.repo)
        task_store = TaskStore(state_dir)
        state = task_store.status(self.task["task_id"])
        binding = state.get("delivery_review_binding")
        self.assertEqual(state["state"], "review_ready")
        self.assertIsInstance(binding, dict)
        self.assertEqual(binding["receipt_digests"], [])
        implementation_lease = (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{self.task['task_id']}.json"
        )
        self.assertFalse(implementation_lease.exists())
        delivery_lease = task_store.acquire_delivery_lease(
            self.task["task_id"],
            worktree=str(scenario.repo),
            branch="codex/run-verify",
            session_id="session-delivery-001",
            paths=list(binding["scope_paths"]),
            policy_digest=contract_digest(
                load_policy(scenario.repo / ".codex" / "project-policy.toml")
            ),
            expected_head=str(binding["reviewed_head"]),
            diff_digest=str(binding["diff_digest"]),
            expected_generation=int(state["generation"]),
        )
        self.assertEqual(delivery_lease["task_id"], self.task["task_id"])

    def test_delivery_audit_is_compact_read_only_and_binds_durable_running_state(
        self,
    ) -> None:
        """Task 5C exposes no execution capability while a run is active."""
        from unittest.mock import patch
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import build_delivery_audit, validate_delivery_audit

        scenario = self._prepared_scenario()
        state_dir = worktree_git_dir(scenario.repo)
        paths = (
            state_dir / "codex-control-plane" / "tasks" / f"{self.task['task_id']}.json",
            state_dir / "codex-control-plane" / "runs" / self.task["task_id"] / "plan.json",
        )
        before = [(path.read_bytes(), path.stat().st_mtime_ns) for path in paths]

        with patch(
            "control_plane.run_workflow.subprocess.run",
            side_effect=AssertionError("audit must not execute Git"),
        ):
            audit = build_delivery_audit(scenario.repo, self.task["task_id"])

        self.assertEqual(validate_delivery_audit(audit), [])
        self.assertLessEqual(len(json.dumps(audit, sort_keys=True).encode("utf-8")), 4096)
        self.assertEqual(json.loads(json.dumps(audit, sort_keys=True)), audit)
        self.assertEqual(audit["kind"], "DeliveryAuditV1")
        self.assertEqual(audit["visible_status"], "TRABAJANDO")
        self.assertEqual(audit["lifecycle_state"], "implementing")
        self.assertEqual(audit["attempts"], {"total": 0, "maximum": 3, "repairs_used": 0})
        self.assertEqual(audit["next_safe_action"], "CONTINUE_IMPLEMENTATION")
        self.assertFalse(audit["authorizes"])
        self.assertNotIn("session_id", audit)
        self.assertNotIn("authorization", audit)
        self.assertEqual([(path.read_bytes(), path.stat().st_mtime_ns) for path in paths], before)

    def test_delivery_audit_reports_pr_ready_without_receipt_bodies_or_urls(self) -> None:
        from control_plane.run_workflow import build_delivery_audit, validate_delivery_audit
        from tests.test_pr_readiness import PullRequestReadinessTests

        readiness = PullRequestReadinessTests(methodName="runTest")
        readiness.setUp()
        self.addCleanup(readiness.doCleanups)
        ready = readiness._publish_ready(readiness._receipts())

        audit = build_delivery_audit(
            readiness.flow.scenario.repo, readiness.flow.task["task_id"],
        )

        self.assertEqual(ready["state"], "pr_ready")
        self.assertEqual(validate_delivery_audit(audit), [])
        self.assertEqual(audit["visible_status"], "PR LISTA")
        self.assertEqual(audit["lifecycle_state"], "pr_ready")
        self.assertEqual(audit["next_safe_action"], "NO_ACTION")
        self.assertEqual(audit["observed"]["pull_request_number"], 7)
        self.assertTrue(audit["receipt_digests"]["remote_outcome"])
        self.assertTrue(audit["receipt_digests"]["pr_readiness"])
        self.assertNotIn("url", json.dumps(audit, sort_keys=True))

    def test_delivery_audit_reports_a_pr_repair_as_implementing(self) -> None:
        from control_plane.run_workflow import build_delivery_audit
        from tests.test_pr_readiness import PullRequestReadinessTests

        readiness = PullRequestReadinessTests(methodName="runTest")
        readiness.setUp()
        self.addCleanup(readiness.doCleanups)
        blocked = readiness._publish(readiness._receipts(check_statuses=("FAIL",)))
        readiness.flow.store.start_revision(
            readiness.flow.task["task_id"],
            **readiness._revision_inputs(blocked["revision_required"]),
        )

        audit = build_delivery_audit(
            readiness.flow.scenario.repo, readiness.flow.task["task_id"],
        )

        self.assertEqual(audit["lifecycle_state"], "implementing")
        self.assertEqual(audit["visible_status"], "TRABAJANDO")
        self.assertEqual(audit["next_safe_action"], "CONTINUE_IMPLEMENTATION")

    def test_delivery_audit_blocks_unknown_and_reports_exhaustion(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore, block_run, build_delivery_audit, build_gate_receipt,
        )

        scenario = self._prepared_scenario()
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        plan = runs.load_plan(self.task["task_id"])
        revision = runs.load_active(self.task["task_id"])
        for attempt in range(1, 4):
            receipt = build_gate_receipt(
                run_plan=plan, attempt=attempt, gate_id="gate.relevant-tests",
                status="FAIL", command_digest="sha256:" + str(attempt) * 64,
                output_digest="sha256:" + str(attempt + 3) * 64,
                before_snapshot_digest="sha256:" + "a" * 64,
                after_snapshot_digest="sha256:" + "a" * 64,
                error_code=f"E_TEST_FAILURE_{attempt}",
                observed_at=f"2026-08-08T10:0{attempt}:00Z",
            )
            runs.record_attempt(
                run_plan=plan, run_revision=revision, attempt=attempt,
                head=revision["head"], changed_paths=("change.txt",),
                receipts=(receipt,), failure_reason_code=f"E_TEST_FAILURE_{attempt}",
                observed_at=f"2026-08-08T10:0{attempt}:00Z",
            )
        block_run(
            repository=scenario.repo, task_id=self.task["task_id"],
            reason_code="E_RUN_EXHAUSTED",
        )

        exhausted = build_delivery_audit(scenario.repo, self.task["task_id"])

        self.assertEqual(exhausted["visible_status"], "BLOCKED")
        self.assertEqual(exhausted["attempts"], {"total": 3, "maximum": 3, "repairs_used": 2})
        self.assertEqual(exhausted["block_reason_code"], "E_RUN_EXHAUSTED")
        self.assertEqual(exhausted["next_safe_action"], "REQUEST_HUMAN_INTERVENTION")

    def test_delivery_audit_unknown_missing_run_and_tamper_fail_closed(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import block_run, build_delivery_audit

        scenario = self._prepared_scenario()
        with self.assertRaisesRegex(ValueError, "E_RUN_NOT_FOUND"):
            build_delivery_audit(scenario.repo, "TASK-RUN-VERIFY-MISSING")
        block_run(
            repository=scenario.repo, task_id=self.task["task_id"],
            reason_code="E_RUN_UNKNOWN",
        )
        unknown = build_delivery_audit(scenario.repo, self.task["task_id"])
        self.assertEqual(unknown["missing_evidence"], ["remote_observation"])

        state_dir = worktree_git_dir(scenario.repo)
        state_path = state_dir / "codex-control-plane" / "tasks" / f"{self.task['task_id']}.json"
        tampered = json.loads(state_path.read_text(encoding="utf-8"))
        tampered["run_plan_digest"] = "sha256:" + "0" * 64
        state_path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_AUDIT_STATE"):
            build_delivery_audit(scenario.repo, self.task["task_id"])

    def test_delivery_audit_rejects_a_tampered_durable_gate_receipt(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, build_delivery_audit, verify_run

        scenario = self._prepared_scenario()
        verify_run(
            repository=scenario.repo, task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        state_dir = worktree_git_dir(scenario.repo)
        digest = RunStore(state_dir).attempts(self.task["task_id"])[0]["gate_receipt_digests"][0]
        receipt_path = (
            state_dir / "codex-control-plane" / "runs" / self.task["task_id"]
            / "receipts" / f"{digest.removeprefix('sha256:')}.json"
        )
        tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
        tampered["status"] = "FAIL"
        receipt_path.write_text(json.dumps(tampered), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "E_RUN_RECEIPT"):
            build_delivery_audit(scenario.repo, self.task["task_id"])

    def test_delivery_audit_validator_derives_status_action_observed_and_missing_evidence(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import build_delivery_audit, validate_delivery_audit

        scenario = self._prepared_scenario()
        audit = build_delivery_audit(scenario.repo, self.task["task_id"])
        mutations = (
            ("visible_status", "VERIFICANDO"),
            ("next_safe_action", "COMPLETE_VERIFICATION"),
            ("observed", {**audit["observed"], "committed_head": "b" * 40}),
            ("missing_evidence", ["remote_observation"]),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                tampered = {**audit, field: value}
                core = {key: value for key, value in tampered.items() if key != "audit_digest"}
                tampered["audit_digest"] = contract_digest(core)
                self.assertNotEqual(validate_delivery_audit(tampered), [])

    def test_delivery_audit_pending_pr_repair_preserves_reason_and_repairs(self) -> None:
        from control_plane.run_workflow import build_delivery_audit
        from tests.test_pr_readiness import PullRequestReadinessTests

        readiness = PullRequestReadinessTests(methodName="runTest")
        readiness.setUp()
        self.addCleanup(readiness.doCleanups)
        pending = readiness._publish(readiness._receipts(check_statuses=("FAIL",)))

        audit = build_delivery_audit(
            readiness.flow.scenario.repo, readiness.flow.task["task_id"],
        )

        self.assertEqual(pending["state"], "pr_draft")
        self.assertEqual(audit["block_reason_code"], "E_PR_READINESS_REVISION_REQUIRED")
        self.assertEqual(audit["next_safe_action"], "REPAIR_IMPLEMENTATION")

    def test_delivery_audit_rejects_pr_ready_without_evidence_or_pull_request(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import build_delivery_audit
        from tests.test_pr_readiness import PullRequestReadinessTests

        readiness = PullRequestReadinessTests(methodName="runTest")
        readiness.setUp()
        self.addCleanup(readiness.doCleanups)
        readiness._publish_ready(readiness._receipts())
        state_dir = worktree_git_dir(readiness.flow.scenario.repo)
        state_path = state_dir / "codex-control-plane" / "tasks" / f"{readiness.flow.task['task_id']}.json"
        tampered = json.loads(state_path.read_text(encoding="utf-8"))
        tampered["evidence"].pop("pr_ready")
        state_path.write_text(json.dumps(tampered), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "E_DELIVERY_AUDIT_EVIDENCE"):
            build_delivery_audit(readiness.flow.scenario.repo, readiness.flow.task["task_id"])

    def test_delivery_audit_rejects_repository_alias_attempt_redirection_and_registry_digest(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, build_delivery_audit, verify_run

        scenario = self._prepared_scenario()
        state_dir = worktree_git_dir(scenario.repo)
        alias = scenario.repo.parent / "audit-alias"
        alias.mkdir()
        (alias / ".git").write_text(f"gitdir: {state_dir}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_AUDIT_REPOSITORY"):
            build_delivery_audit(alias, self.task["task_id"])

        state_path = state_dir / "codex-control-plane" / "tasks" / f"{self.task['task_id']}.json"
        registry = json.loads(state_path.read_text(encoding="utf-8"))
        registry["remote_outcome_receipt_digests"] = ["sha256:" + "f" * 64]
        state_path.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_AUDIT_REMOTE"):
            build_delivery_audit(scenario.repo, self.task["task_id"])

        registry.pop("remote_outcome_receipt_digests")
        state_path.write_text(json.dumps(registry), encoding="utf-8")
        verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:01:00Z")
        runs = RunStore(state_dir)
        attempt_path = state_dir / "codex-control-plane" / "runs" / self.task["task_id"] / "attempt-1.json"
        redirected = json.loads(attempt_path.read_text(encoding="utf-8"))
        redirected["run_revision_digest"] = "sha256:" + "e" * 64
        core = {key: value for key, value in redirected.items() if key != "attempt_digest"}
        redirected["attempt_digest"] = contract_digest(core)
        attempt_path.write_text(json.dumps(redirected), encoding="utf-8")
        self.assertEqual(runs.attempts(self.task["task_id"])[0]["run_revision_digest"], "sha256:" + "e" * 64)
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_AUDIT_ATTEMPT"):
            build_delivery_audit(scenario.repo, self.task["task_id"])

    def test_delivery_audit_validator_rejects_redigested_latest_attempt_impossibilities(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import build_delivery_audit, validate_delivery_audit

        scenario = self._prepared_scenario()
        audit = build_delivery_audit(scenario.repo, self.task["task_id"])
        mutations = (
            {"attempts": {"total": 0, "maximum": 3, "repairs_used": 0},
             "latest_attempt": {**audit["latest_attempt"], "status": "FAIL", "retry_allowed": True}},
            {"attempts": {"total": 1, "maximum": 3, "repairs_used": 0},
             "latest_attempt": {**audit["latest_attempt"], "status": "PASS", "retry_allowed": True}},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                tampered = {**audit, **mutation}
                core = {key: value for key, value in tampered.items() if key != "audit_digest"}
                tampered["audit_digest"] = contract_digest(core)
                self.assertNotEqual(validate_delivery_audit(tampered), [])

    def test_delivery_audit_omits_synthetic_remote_registry_digest_at_pr_ready(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import build_delivery_audit
        from tests.test_pr_readiness import PullRequestReadinessTests

        readiness = PullRequestReadinessTests(methodName="runTest")
        readiness.setUp()
        self.addCleanup(readiness.doCleanups)
        readiness._publish(readiness._receipts())
        state_dir = worktree_git_dir(readiness.flow.scenario.repo)
        state_path = state_dir / "codex-control-plane" / "tasks" / f"{readiness.flow.task['task_id']}.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        fake = "sha256:" + "f" * 64
        state["remote_outcome_receipt_digests"].append(fake)
        state_path.write_text(json.dumps(state), encoding="utf-8")

        audit = build_delivery_audit(readiness.flow.scenario.repo, readiness.flow.task["task_id"])

        self.assertNotIn(fake, audit["receipt_digests"]["remote_outcome"])

    def test_delivery_audit_rejects_review_receipt_task_or_revision_drift(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, build_delivery_audit
        from tests.test_pr_readiness import PullRequestReadinessTests

        readiness = PullRequestReadinessTests(methodName="runTest")
        readiness.setUp()
        self.addCleanup(readiness.doCleanups)
        readiness._publish(readiness._receipts())
        state_dir = worktree_git_dir(readiness.flow.scenario.repo)
        runs = RunStore(state_dir)
        path = runs._review_receipt_path(readiness.flow.task["task_id"], 1, "independent")
        self.assertTrue(path.is_file())
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["task_digest"] = "sha256:" + "e" * 64
        receipt["run_revision_digest"] = "sha256:" + "d" * 64
        core = {key: value for key, value in receipt.items() if key != "receipt_digest"}
        receipt["receipt_digest"] = contract_digest(core)
        path.write_text(json.dumps(receipt), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "E_DELIVERY_AUDIT_REVIEW"):
            build_delivery_audit(readiness.flow.scenario.repo, readiness.flow.task["task_id"])

    def test_delivery_audit_validator_binds_latest_attempt_to_lifecycle_and_stop_policy(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import build_delivery_audit, validate_delivery_audit

        scenario = self._prepared_scenario()
        audit = build_delivery_audit(scenario.repo, self.task["task_id"])
        mutations = (
            {
                "attempts": {"total": 1, "maximum": 3, "repairs_used": 0},
                "latest_attempt": {
                    "status": "UNKNOWN", "retry_allowed": False, "blocked": True,
                    "stop_reason_code": "E_RUN_UNKNOWN", "failure_reason_code": "E_TEST_UNKNOWN",
                },
            },
            {
                "attempts": {"total": 1, "maximum": 3, "repairs_used": 0},
                "latest_attempt": {
                    "status": "FAIL", "retry_allowed": False, "blocked": True,
                    "stop_reason_code": "E_RUN_UNKNOWN", "failure_reason_code": "E_TEST_FAILURE",
                },
            },
            {
                "attempts": {"total": 3, "maximum": 3, "repairs_used": 2},
                "latest_attempt": {
                    "status": "FAIL", "retry_allowed": True, "blocked": False,
                    "stop_reason_code": None, "failure_reason_code": "E_TEST_FAILURE",
                },
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                tampered = {**audit, **mutation}
                core = {key: value for key, value in tampered.items() if key != "audit_digest"}
                tampered["audit_digest"] = contract_digest(core)
                self.assertNotEqual(validate_delivery_audit(tampered), [])

    def test_t2_stays_verifying_until_an_independent_review_receipt_exists(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        semantic = {
            key: value for key, value in self.decision.items() if key != "decision_digest"
        }
        self.decision["decision_digest"] = contract_digest(semantic)
        scenario = self._prepared_scenario()

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        self.assertEqual(result["task"]["state"], "verifying")
        self.assertEqual(result["summary"]["gate_status"], "PASS")
        self.assertEqual(
            result["review_artifact"]["manifest"]["kind"],
            "StableReviewDiffArtifactV1",
        )
        self.assertEqual(
            result["review_artifact"]["artifact_digest"],
            result["review_artifact"]["manifest"]["artifact_digest"],
        )
        self.assertNotIn("packet_digest", result["review_artifact"]["manifest"])

    def test_t2_generic_transition_cannot_bypass_review(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests",
            "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        tasks = TaskStore(worktree_git_dir(scenario.repo))
        before = tasks.status(self.task["task_id"])

        with self.assertRaisesRegex(ValueError, "E_INDEPENDENT_REVIEW"):
            tasks.transition(
                self.task["task_id"],
                "review_ready",
                evidence={
                    "gates_ok": True,
                    "documentation_decision": "sha256:" + "f" * 64,
                },
                current_branch="codex/run-verify",
            )

        self.assertEqual(tasks.status(self.task["task_id"]), before)

    def test_t2_transition_fails_closed_without_a_safe_plan(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, verify_run

        for mutation in ("missing", "symlink"):
            with self.subTest(mutation=mutation):
                self.decision["summary"]["tier"] = "T2"
                self.decision["required_gates"] = [
                    "gate.relevant-tests",
                    "gate.independent-review",
                ]
                self.decision["decision_digest"] = contract_digest(
                    {
                        key: value
                        for key, value in self.decision.items()
                        if key != "decision_digest"
                    }
                )
                scenario = self._prepared_scenario()
                verify_run(
                    repository=scenario.repo,
                    task_id=self.task["task_id"],
                    observed_at="2026-08-08T10:01:00Z",
                )
                state_dir = worktree_git_dir(scenario.repo)
                tasks = TaskStore(state_dir)
                before = tasks.status(self.task["task_id"])
                plan_path = RunStore(state_dir)._plan_path(
                    self.task["task_id"]
                )
                plan_path.unlink()
                if mutation == "symlink":
                    plan_path.symlink_to(scenario.repo / "change.txt")

                with self.assertRaisesRegex(
                    ValueError, "E_INDEPENDENT_REVIEW"
                ):
                    tasks.transition(
                        self.task["task_id"],
                        "review_ready",
                        evidence={
                            "gates_ok": True,
                            "documentation_decision": "sha256:" + "f" * 64,
                        },
                        current_branch="codex/run-verify",
                    )

                self.assertEqual(tasks.status(self.task["task_id"]), before)

    def test_t2_plan_bound_gate_reaches_artifact_without_review_unknown(self) -> None:
        """A routed T2 runs only local gates plus its persisted written plan."""
        from control_plane.contracts import contract_digest
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, prepare_review_packet, verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.written-plan", "gate.relevant-tests", "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()

        result = verify_run(repository=scenario.repo, task_id=self.task["task_id"],
                            observed_at="2026-08-08T10:01:00Z")

        self.assertEqual(result["task"]["state"], "verifying")
        self.assertEqual(result["summary"]["gate_status"], "PASS")
        self.assertIsNotNone(result["review_artifact"])
        self.assertEqual(
            {receipt["gate_id"] for receipt in result["receipts"]},
            {
                "gate.written-plan", "gate.relevant-tests", "gate.policy-check",
                "gate.registry-check", "gate.doctor", "gate.diff-review",
            },
        )
        packet = prepare_review_packet(
            scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "3" * 64,
        )
        self.assertEqual(
            {summary["check_id"] for summary in packet["evidence_summaries"]},
            {receipt["gate_id"] for receipt in result["receipts"]},
        )
        self.assertEqual(
            RunStore(worktree_git_dir(scenario.repo)).load_review_packet(
                self.task["task_id"], 1, "independent",
            ),
            packet,
        )

    def test_t3_missing_rollback_plan_is_unknown_and_blocks_review(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import verify_run

        self.decision["summary"]["tier"] = "T3"
        self.decision["required_gates"] = [
            "gate.written-plan", "gate.relevant-tests", "gate.independent-review",
            "gate.security-review", "gate.rollback-plan",
        ]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        rollback = next(
            item for item in result["receipts"]
            if item["gate_id"] == "gate.rollback-plan"
        )
        self.assertEqual(rollback["status"], "UNKNOWN")
        self.assertEqual(result["summary"]["gate_status"], "UNKNOWN")
        self.assertEqual(result["task"]["state"], "blocked")
        self.assertIsNone(result.get("review_artifact"))

        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, build_rollback_plan
        from tests.host_adapter_test_support import rollback_plan_observation

        unknown_scenario = self._prepared_scenario()
        unknown_runs = RunStore(worktree_git_dir(unknown_scenario.repo))
        plan = unknown_runs.load_plan(self.task["task_id"])
        revision = unknown_runs.load_active(self.task["task_id"])
        observation = rollback_plan_observation(
            run_plan=plan,
            run_revision=revision,
            attempt=1,
            trigger_conditions=(),
            rollback_steps=(),
            post_rollback_checks=(),
            irreversible_boundaries=(),
            status="UNKNOWN",
            invocation_id="native-rollback-unknown",
        )
        unknown_runs.persist_rollback_plan(
            build_rollback_plan(
                run_plan=plan,
                run_revision=revision,
                attempt=1,
                observation=observation,
            ),
            observation=observation,
        )
        unknown_result = verify_run(
            repository=unknown_scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        self.assertEqual(unknown_result["summary"]["gate_status"], "UNKNOWN")
        self.assertEqual(unknown_result["task"]["state"], "blocked")

    def test_t3_structured_host_bound_rollback_plan_satisfies_exact_gate(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, verify_run

        self.decision["summary"]["tier"] = "T3"
        self.decision["required_gates"] = [
            "gate.written-plan", "gate.relevant-tests", "gate.independent-review",
            "gate.security-review", "gate.rollback-plan",
        ]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        runs = RunStore(worktree_git_dir(scenario.repo))
        from tests.host_adapter_test_support import rollback_plan_observation
        from control_plane.run_workflow import build_rollback_plan
        plan, revision = runs.load_plan(self.task["task_id"]), runs.load_active(self.task["task_id"])
        observation = rollback_plan_observation(
            run_plan=plan, run_revision=revision, attempt=1,
            trigger_conditions=(("verification_or_review_regresses", "gate status is not PASS"),),
            rollback_steps=((1, "restore prior tracked content", "owned scope", "git diff matches prior tree"),),
            post_rollback_checks=(("gate.diff-review", "PASS"),),
            irreversible_boundaries=(("remote effects", "none are authorized by this plan"),),
            status="PASS",
        )
        rollback_plan = build_rollback_plan(run_plan=plan, run_revision=revision, attempt=1, observation=observation)
        with self.assertRaisesRegex(ValueError, "E_ROLLBACK_PLAN"):
            runs.persist_rollback_plan("rollback", observation=observation)
        runs.persist_rollback_plan(rollback_plan, observation=observation)

        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )

        rollback = next(
            item for item in result["receipts"]
            if item["gate_id"] == "gate.rollback-plan"
        )
        self.assertEqual(rollback["status"], "PASS")
        self.assertEqual(result["summary"]["gate_status"], "PASS")
        self.assertEqual(result["task"]["state"], "verifying")
        self.assertIsNotNone(result["review_artifact"])

    def test_t3_defers_security_until_promotion_without_unknown_receipt(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import verify_run

        self.decision["summary"]["tier"] = "T3"
        self.decision["required_gates"] = [
            "gate.written-plan", "gate.relevant-tests", "gate.independent-review",
            "gate.security-review", "gate.rollback-plan",
        ]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        self._persist_t3_rollback_plan(scenario)

        result = verify_run(repository=scenario.repo, task_id=self.task["task_id"],
                            observed_at="2026-08-08T10:01:00Z")

        self.assertEqual(result["task"]["state"], "verifying")
        self.assertEqual(result["summary"]["gate_status"], "PASS")
        self.assertNotIn("gate.independent-review", [item["gate_id"] for item in result["receipts"]])
        self.assertNotIn("gate.security-review", [item["gate_id"] for item in result["receipts"]])
        self.assertIsNotNone(result["review_artifact"])

    def test_t3_promotion_requires_two_distinct_host_review_observations(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore,
            _atomic_json,
            prepare_review_packet,
            promote_review_ready,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T3"
        self.decision["required_gates"] = [
            "gate.written-plan",
            "gate.relevant-tests",
            "gate.independent-review",
            "gate.security-review",
            "gate.rollback-plan",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        self._persist_t3_rollback_plan(scenario)
        verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        runs = RunStore(worktree_git_dir(scenario.repo))
        receipts: dict[str, dict[str, object]] = {}
        for index, review_kind in enumerate(
            ("independent", "security"), start=4
        ):
            packet = prepare_review_packet(
                scenario.repo,
                self.task["task_id"],
                1,
                review_kind,
                "sha256:" + str(index) * 64,
            )
            receipt, observation = independent_review_receipt(
                run_store=runs,
                review_packet=packet,
                findings_digest="sha256:" + str(index + 2) * 64,
                critical=0,
                important=0,
                status="PASS",
                observed_at=f"2026-08-08T10:0{index}:00Z",
                reviewer_identity=f"test-reviewer:{review_kind}",
            )
            runs.persist_review_receipt(
                self.task["task_id"],
                packet["packet_digest"],
                receipt,
                observation=observation,
            )
            receipts[review_kind] = receipt

        promotion = promote_review_ready(
            state_dir=worktree_git_dir(scenario.repo),
            run_plan=runs.load_plan(self.task["task_id"]),
            receipt_digests=tuple(
                str(receipts[kind]["receipt_digest"])
                for kind in ("independent", "security")
            ),
        )
        self.assertFalse(promotion["authorizes"])
        security = dict(receipts["security"])
        security["observation_digest"] = receipts["independent"][
            "observation_digest"
        ]
        security["receipt_digest"] = contract_digest(
            {
                key: value
                for key, value in security.items()
                if key != "receipt_digest"
            }
        )
        _atomic_json(
            runs._review_receipt_path(
                self.task["task_id"], 1, "security"
            ),
            security,
        )
        with self.assertRaisesRegex(
            ValueError, "E_INDEPENDENT_REVIEW_OBSERVATION"
        ):
            promote_review_ready(
                state_dir=worktree_git_dir(scenario.repo),
                run_plan=runs.load_plan(self.task["task_id"]),
                receipt_digests=(
                    str(receipts["independent"]["receipt_digest"]),
                    str(security["receipt_digest"]),
                ),
            )

    def test_review_artifact_failure_never_leaves_a_durable_pass(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests",
            "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        unsafe = scenario.repo / "unsafe-untracked.txt"
        unsafe.write_text("unsafe\n", encoding="utf-8")
        unsafe.chmod(0o666)
        state_dir = worktree_git_dir(scenario.repo)

        with self.assertRaisesRegex(ValueError, "E_REVIEW_ARTIFACT"):
            verify_run(
                repository=scenario.repo,
                task_id=self.task["task_id"],
                observed_at="2026-08-08T10:01:00Z",
            )

        self.assertEqual(
            RunStore(state_dir).attempts(self.task["task_id"]), []
        )
        state = TaskStore(state_dir).status(self.task["task_id"])
        self.assertEqual(state["state"], "blocked")
        self.assertEqual(state["block_reason"], "E_REVIEW_ARTIFACT")

    def test_pending_artifact_partial_leaf_faults_roll_back_exactly(self) -> None:
        from unittest.mock import patch
        import control_plane.run_workflow as run_workflow
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore,
            RunStore,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests",
            "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        original = ReviewArtifactStore._write_leaf
        original_write = run_workflow.os.write

        for target_leaf in ("review.diff", "manifest.json"):
            with self.subTest(target_leaf=target_leaf):
                scenario = self._prepared_scenario()
                state_dir = worktree_git_dir(scenario.repo)

                def fail_leaf(
                    directory: int, name: str, payload: bytes
                ) -> None:
                    if name != target_leaf:
                        original(directory, name, payload)
                        return

                    def partial_write(
                        descriptor: int, chunk: bytes
                    ) -> int:
                        original_write(descriptor, chunk[:1])
                        raise OSError("injected partial leaf write")

                    with patch.object(
                        run_workflow.os,
                        "write",
                        side_effect=partial_write,
                    ):
                        original(directory, name, payload)

                with patch.object(
                    ReviewArtifactStore,
                    "_write_leaf",
                    side_effect=fail_leaf,
                ):
                    with self.assertRaisesRegex(
                        ValueError, "E_REVIEW_ARTIFACT"
                    ):
                        verify_run(
                            repository=scenario.repo,
                            task_id=self.task["task_id"],
                            observed_at="2026-08-08T10:01:00Z",
                        )

                artifact_root = (
                    ReviewArtifactStore(scenario.repo).root
                    / self.task["task_id"]
                    / "attempt-1"
                )
                self.assertFalse(artifact_root.exists())
                self.assertEqual(
                    RunStore(state_dir).attempts(self.task["task_id"]), []
                )
                self.assertEqual(
                    TaskStore(state_dir).status(self.task["task_id"])[
                        "state"
                    ],
                    "blocked",
                )

    def test_pending_artifact_crash_survivor_is_cleaned_exactly(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore,
            RunStore,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests",
            "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        state_dir = worktree_git_dir(scenario.repo)
        artifact_store = ReviewArtifactStore(scenario.repo)
        attempt_root = (
            artifact_store.root
            / self.task["task_id"]
            / "attempt-1"
        )
        for directory in (
            artifact_store.root,
            attempt_root.parent,
            attempt_root,
        ):
            directory.mkdir(mode=0o700, exist_ok=True)
            directory.chmod(0o700)
        survivor = attempt_root / ".review.diff.pending"
        survivor.write_bytes(b"partial private diff")
        survivor.chmod(0o600)

        with self.assertRaisesRegex(ValueError, "E_REVIEW_ARTIFACT"):
            verify_run(
                repository=scenario.repo,
                task_id=self.task["task_id"],
                observed_at="2026-08-08T10:01:00Z",
            )

        self.assertFalse(attempt_root.exists())
        self.assertEqual(
            RunStore(state_dir).attempts(self.task["task_id"]), []
        )
        self.assertEqual(
            TaskStore(state_dir).status(self.task["task_id"])["state"],
            "blocked",
        )

    def test_unknown_required_gate_is_rejected_before_a_run_plan_is_persisted(self) -> None:
        from control_plane.run_workflow import build_run_plan

        self.decision["required_gates"] = ["gate.not-real"]
        with self.assertRaisesRegex(ValueError, "E_RUN_BINDING"):
            build_run_plan(
                task=self.task, decision=self.decision, repository=Path("/tmp/example-repository"),
                branch="codex/example", head="a" * 40, session_id="session-run-001",
                prepared_at="2026-08-08T10:00:00Z",
            )

    def test_t2_promotes_once_from_an_exact_durable_independent_receipt(self) -> None:
        from unittest.mock import patch
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore, RunStore,
            build_independent_review_receipt,
            prepare_review_packet,
            publish_review_ready,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        semantic = {key: value for key, value in self.decision.items() if key != "decision_digest"}
        self.decision["decision_digest"] = contract_digest(semantic)
        scenario = self._prepared_scenario()
        verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:01:00Z")
        store = RunStore(worktree_git_dir(scenario.repo))
        plan = store.load_plan(self.task["task_id"])
        packet = prepare_review_packet(scenario.repo, self.task["task_id"], 1,
            "independent", "sha256:" + "4" * 64)
        receipt, review_observation = independent_review_receipt(
            run_store=store, review_packet=packet, findings_digest="sha256:" + "5" * 64,
            critical=0, important=0, status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        store.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], receipt,
            observation=review_observation,
        )
        expected = __import__("control_plane.lifecycle", fromlist=["TaskStore"]).TaskStore(worktree_git_dir(scenario.repo)).status(self.task["task_id"])["generation"]
        with patch.object(ReviewArtifactStore, "delete_exact", side_effect=RuntimeError("fault-delete")), self.assertRaisesRegex(RuntimeError, "fault-delete"):
            publish_review_ready(repository=scenario.repo, task_id=self.task["task_id"], expected_generation=expected, receipt_digests=(receipt["receipt_digest"],))

    def test_direct_review_ready_finalization_rejects_forged_proof(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore,
            RunStore,
            prepare_review_packet,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests", "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        result = verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        prepare_review_packet(
            scenario.repo, self.task["task_id"], 1, "independent",
            "sha256:" + "4" * 64,
        )
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        plan = runs.load_plan(self.task["task_id"])
        revision = runs.load_active(self.task["task_id"])
        artifact = ReviewArtifactStore(scenario.repo).load_manifest(
            self.task["task_id"], 1
        )
        tasks = TaskStore(state_dir)
        before = tasks.status(self.task["task_id"])

        with self.assertRaisesRegex(ValueError, "E_INDEPENDENT_REVIEW"):
            tasks.finalize_review_ready(
                self.task["task_id"],
                expected_generation=before["generation"],
                run_plan_digest=plan["plan_digest"],
                run_revision_digest=revision["revision_digest"],
                attempt_digest=result["attempt"]["attempt_digest"],
                promotion_digest="sha256:" + "a" * 64,
                receipt_digests=("sha256:" + "b" * 64,),
                artifact=artifact,
                current_branch=plan["branch"],
            )

        self.assertEqual(tasks.status(self.task["task_id"]), before)
        self.assertEqual(
            ReviewArtifactStore(scenario.repo).artifact_state(artifact),
            "present",
        )

    def _review_ready_marker_fixture(self):
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore, _atomic_json
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore, RunStore, build_independent_review_receipt,
            prepare_review_packet, promote_review_ready, verify_run,
        )
        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        result = verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:01:00Z")
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        plan = runs.load_plan(self.task["task_id"])
        packet = prepare_review_packet(scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "4" * 64)
        receipt, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet,
            findings_digest="sha256:" + "5" * 64, critical=0,
            important=0, status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        runs.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], receipt,
            observation=review_observation,
        )
        proof = promote_review_ready(state_dir=state_dir, run_plan=plan, receipt_digests=(receipt["receipt_digest"],))
        tasks = TaskStore(state_dir)
        state = tasks.status(self.task["task_id"])
        artifact = ReviewArtifactStore(scenario.repo).load_manifest(self.task["task_id"], 1)
        final_core = {"prior_generation": state["generation"], "task_id": self.task["task_id"], "run_plan_digest": plan["plan_digest"], "run_revision_digest": runs.load_active(self.task["task_id"])["revision_digest"], "attempt_digest": result["attempt"]["attempt_digest"], "promotion_digest": proof["promotion_digest"], "receipt_digests": [receipt["receipt_digest"]], "artifact": artifact, "artifact_delete_started": True, "branch": plan["branch"]}
        final = {
            **final_core,
            "finalization_digest": contract_digest(final_core),
        }
        state.update({"state": "finalizing_review_ready", "resume_forbidden": True, "review_ready_finalization": final})
        _atomic_json(tasks._path(self.task["task_id"]), state)
        return scenario, tasks, artifact

    def test_review_ready_recovery_completes_a_real_partial_artifact(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore
        scenario, tasks, artifact = self._review_ready_marker_fixture()
        ReviewArtifactStore(scenario.repo).manifest_path(artifact).with_name("review.diff").unlink()
        recovered = tasks.recover_writer_finalization(self.task["task_id"])
        self.assertEqual(recovered["state"], "review_ready")
        self.assertEqual(ReviewArtifactStore(scenario.repo).artifact_state(artifact), "absent")

    def test_review_ready_recovery_keeps_a_real_drift_marker(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore
        scenario, tasks, artifact = self._review_ready_marker_fixture()
        ReviewArtifactStore(scenario.repo).manifest_path(artifact).with_name("review.diff").write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "E_REVIEW_READY_RECOVERY_UNKNOWN"):
            tasks.recover_writer_finalization(self.task["task_id"])
        state = tasks.status(self.task["task_id"])
        self.assertEqual(state["state"], "finalizing_review_ready")
        self.assertIn("review_ready_finalization", state)

    def test_review_ready_recovery_rejects_a_tampered_partial_artifact(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        scenario, tasks, artifact = self._review_ready_marker_fixture()
        manifest_path = ReviewArtifactStore(scenario.repo).manifest_path(artifact)
        manifest_path.unlink()
        manifest_path.with_name("review.diff").write_text(
            "replacement\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "E_REVIEW_READY_RECOVERY_UNKNOWN"):
            tasks.recover_writer_finalization(self.task["task_id"])

        state = tasks.status(self.task["task_id"])
        self.assertEqual(state["state"], "finalizing_review_ready")
        self.assertIn("review_ready_finalization", state)

    def test_review_ready_recovery_rejects_forged_marker_binding(self) -> None:
        from control_plane.lifecycle import _atomic_json

        _scenario, tasks, _artifact = self._review_ready_marker_fixture()
        state = tasks.status(self.task["task_id"])
        state["review_ready_finalization"]["attempt_digest"] = (
            "sha256:" + "f" * 64
        )
        _atomic_json(tasks._path(self.task["task_id"]), state)

        with self.assertRaisesRegex(ValueError, "E_REVIEW_READY_RECOVERY_UNKNOWN"):
            tasks.recover_writer_finalization(self.task["task_id"])

        retained = tasks.status(self.task["task_id"])
        self.assertEqual(retained["state"], "finalizing_review_ready")
        self.assertEqual(
            retained["review_ready_finalization"]["attempt_digest"],
            "sha256:" + "f" * 64,
        )

    def test_review_ready_recovery_rejects_live_diff_drift(self) -> None:
        scenario, tasks, _artifact = self._review_ready_marker_fixture()
        (scenario.repo / "change.txt").write_text(
            "changed after finalization marker\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(
            ValueError, "E_REVIEW_READY_RECOVERY_UNKNOWN"
        ):
            tasks.recover_writer_finalization(self.task["task_id"])

        retained = tasks.status(self.task["task_id"])
        self.assertEqual(retained["state"], "finalizing_review_ready")
        self.assertIn("review_ready_finalization", retained)

    def _local_review_marker_fixture(self, *, drift: bool):
        from unittest.mock import patch
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore, RunStore, build_independent_review_receipt,
            prepare_review_packet, start_local_review_revision, verify_run,
        )
        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:01:00Z")
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        packet = prepare_review_packet(scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "4" * 64)
        failed, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet,
            findings_digest="sha256:" + "5" * 64, critical=1,
            important=0, status="FAIL",
            observed_at="2026-08-08T10:02:00Z",
        )
        runs.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], failed,
            observation=review_observation,
        )
        tasks = TaskStore(state_dir)
        before = tasks.status(self.task["task_id"])
        artifact = ReviewArtifactStore(scenario.repo).load_manifest(self.task["task_id"], 1)
        def interrupt_delete(item):
            diff = ReviewArtifactStore(scenario.repo).manifest_path(item).with_name("review.diff")
            if drift:
                diff.write_text("drift\n", encoding="utf-8")
            else:
                diff.unlink()
            raise RuntimeError("injected-delete-boundary")
        with patch.object(ReviewArtifactStore, "delete_exact", side_effect=interrupt_delete):
            with self.assertRaisesRegex(RuntimeError, "injected-delete-boundary"):
                start_local_review_revision(
                    repository=scenario.repo, task_id=self.task["task_id"],
                    expected_generation=before["generation"], review_receipt_digest=failed["receipt_digest"],
                    new_session_id="session-local-recovery-002",
                )
        return scenario, tasks, artifact, before

    def test_local_review_recovery_completes_a_real_partial_artifact(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import ReviewArtifactStore, RunStore
        scenario, tasks, artifact, _before = self._local_review_marker_fixture(drift=False)
        recovered = tasks.recover_writer_finalization(self.task["task_id"])
        self.assertEqual(recovered["state"], "implementing")
        self.assertTrue(recovered["lease_digest"])
        self.assertEqual(RunStore(worktree_git_dir(scenario.repo)).load_active(self.task["task_id"])["revision"], 1)
        self.assertEqual(ReviewArtifactStore(scenario.repo).artifact_state(artifact), "absent")

    def test_local_review_recovery_keeps_a_real_drift_marker(self) -> None:
        scenario, tasks, _artifact, before = self._local_review_marker_fixture(drift=True)
        with self.assertRaisesRegex(ValueError, "E_LOCAL_REVIEW_RECOVERY_UNKNOWN"):
            tasks.recover_writer_finalization(self.task["task_id"])
        state = tasks.status(self.task["task_id"])
        self.assertEqual(state["state"], "finalizing_local_review_revision")
        self.assertEqual(state["generation"], before["generation"])
        self.assertIn("local_review_revision_finalization", state)

    def test_local_review_recovery_rejects_forged_marker_binding(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import _atomic_json

        _scenario, tasks, _artifact, _before = self._local_review_marker_fixture(
            drift=False
        )
        state = tasks.status(self.task["task_id"])
        final = state["local_review_revision_finalization"]
        final["review_receipt_digest"] = "sha256:" + "f" * 64
        if "finalization_digest" in final:
            core = {
                key: value
                for key, value in final.items()
                if key != "finalization_digest"
            }
            final["finalization_digest"] = contract_digest(core)
        _atomic_json(tasks._path(self.task["task_id"]), state)

        with self.assertRaisesRegex(
            ValueError, "E_LOCAL_REVIEW_RECOVERY_UNKNOWN"
        ):
            tasks.recover_writer_finalization(self.task["task_id"])

        retained = tasks.status(self.task["task_id"])
        self.assertEqual(retained["state"], "finalizing_local_review_revision")
        self.assertEqual(
            retained["local_review_revision_finalization"][
                "review_receipt_digest"
            ],
            "sha256:" + "f" * 64,
        )

    def test_local_review_recovery_rejects_live_head_drift(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        scenario, tasks, artifact, _before = self._local_review_marker_fixture(
            drift=False
        )
        git(scenario.repo, "add", "change.txt")
        git(scenario.repo, "commit", "-m", "test: drift after local marker")

        with self.assertRaisesRegex(
            ValueError, "E_LOCAL_REVIEW_RECOVERY_UNKNOWN"
        ):
            tasks.recover_writer_finalization(self.task["task_id"])

        retained = tasks.status(self.task["task_id"])
        self.assertEqual(
            retained["state"], "finalizing_local_review_revision"
        )
        self.assertIn("local_review_revision_finalization", retained)
        self.assertEqual(
            ReviewArtifactStore(scenario.repo).artifact_state(artifact),
            "partial",
        )

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

    def test_review_correction_keeps_head_and_binds_source_diff(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore,
            build_independent_review_receipt,
            prepare_review_packet,
            start_local_review_revision,
            verify_run,
        )

        from control_plane.repository import worktree_git_dir
        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        semantic = {key: value for key, value in self.decision.items() if key != "decision_digest"}
        self.decision["decision_digest"] = __import__("control_plane.contracts", fromlist=["contract_digest"]).contract_digest(semantic)
        scenario = self._prepared_scenario()
        first = verify_run(
            repository=scenario.repo, task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        state_dir = worktree_git_dir(scenario.repo)
        store = RunStore(state_dir)
        parent = store.load_active(self.task["task_id"])
        packet = prepare_review_packet(
            scenario.repo,
            self.task["task_id"],
            1,
            "independent",
            "sha256:" + "8" * 64,
        )
        receipt, review_observation = independent_review_receipt(
            run_store=store, review_packet=packet,
            findings_digest="sha256:" + "9" * 64, critical=1, important=0, status="FAIL",
            observed_at="2026-08-08T10:02:00Z",
        )
        store.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], receipt,
            observation=review_observation,
        )
        from control_plane.lifecycle import TaskStore
        before = TaskStore(state_dir).status(self.task["task_id"])
        start_local_review_revision(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            expected_generation=before["generation"],
            review_receipt_digest=receipt["receipt_digest"],
            new_session_id="session-review-unchanged-002",
        )
        corrected = store.load_active(self.task["task_id"])
        self.assertEqual(corrected["head"], parent["head"])
        self.assertEqual(corrected["source_diff_digest"], receipt["diff_digest"])

        result = verify_run(
            repository=scenario.repo, task_id=self.task["task_id"],
            observed_at="2026-08-08T10:03:00Z",
        )

        self.assertEqual(result["attempt"]["attempt"], 2)
        self.assertEqual(result["run_revision"], corrected)
        self.assertEqual(result["receipts"][0]["gate_id"], "gate.diff-review")
        self.assertEqual(result["receipts"][0]["error_code"], "E_RUN_REVIEW_UNCHANGED")

    def test_start_local_review_revision_reacquires_after_handoff(self) -> None:
        """A blocking local review starts the next immutable attempt safely."""
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore,
            build_independent_review_receipt,
            prepare_review_packet,
            start_local_review_revision,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        first = verify_run(
            repository=scenario.repo, task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        state_dir = worktree_git_dir(scenario.repo)
        run_store = RunStore(state_dir)
        parent = run_store.load_active(self.task["task_id"])
        packet = prepare_review_packet(
            scenario.repo, self.task["task_id"], 1, "independent",
            "sha256:" + "4" * 64,
        )
        failed, review_observation = independent_review_receipt(
            run_store=run_store, review_packet=packet,
            findings_digest="sha256:" + "5" * 64,
            critical=1, important=0, status="FAIL",
            observed_at="2026-08-08T10:02:00Z",
        )
        run_store.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], failed,
            observation=review_observation,
        )
        before = TaskStore(state_dir).status(self.task["task_id"])

        state = start_local_review_revision(
            repository=scenario.repo, task_id=self.task["task_id"],
            expected_generation=before["generation"],
            review_receipt_digest=failed["receipt_digest"],
            new_session_id="session-review-revision-002",
        )

        active = run_store.load_active(self.task["task_id"])
        self.assertEqual(state["state"], "implementing")
        self.assertEqual(active["revision"], 1)
        self.assertEqual(active["head"], parent["head"])
        self.assertEqual(active["first_attempt"], 2)
        self.assertEqual(active["source_diff_digest"], failed["diff_digest"])
        self.assertEqual(state["active_run_revision_digest"], active["revision_digest"])
        self.assertTrue(state["lease_digest"])

    def test_direct_local_review_revision_rejects_forged_proof(self) -> None:
        """The lifecycle boundary must rebind caller data to durable review proof."""
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore,
            RunStore,
            build_independent_review_receipt,
            build_run_revision,
            prepare_review_packet,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests",
            "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        plan = runs.load_plan(self.task["task_id"])
        parent = runs.load_active(self.task["task_id"])
        latest = runs.attempts(self.task["task_id"])[-1]
        packet = prepare_review_packet(
            scenario.repo,
            self.task["task_id"],
            1,
            "independent",
            "sha256:" + "4" * 64,
        )
        persisted, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet,
            findings_digest="sha256:" + "5" * 64,
            critical=1,
            important=0,
            status="FAIL",
            observed_at="2026-08-08T10:02:00Z",
        )
        runs.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], persisted,
            observation=review_observation,
        )
        forged = dict(persisted)
        forged["findings_digest"] = "sha256:" + "6" * 64
        forged["receipt_digest"] = contract_digest(
            {
                key: value
                for key, value in forged.items()
                if key != "receipt_digest"
            }
        )
        artifact = ReviewArtifactStore(scenario.repo).load_manifest(
            self.task["task_id"], 1
        )
        revision = build_run_revision(
            run_plan=plan,
            revision=1,
            first_attempt=2,
            head=parent["head"],
            reason="review_findings",
            parent_revision_digest=parent["revision_digest"],
            source_attempt_digest=latest["attempt_digest"],
            source_review_receipt_digest=forged["receipt_digest"],
            source_diff_digest=artifact["diff_digest"],
        )
        tasks = TaskStore(state_dir)
        before = tasks.status(self.task["task_id"])

        with self.assertRaisesRegex(ValueError, "E_LOCAL_REVIEW"):
            tasks.start_local_review_revision(
                self.task["task_id"],
                expected_generation=before["generation"],
                run_plan=plan,
                parent_revision=parent,
                latest_attempt=latest,
                review_receipt=forged,
                artifact=artifact,
                revision=revision,
                worktree=str(scenario.repo),
                policy_digest=contract_digest(
                    load_policy(
                        scenario.repo / ".codex" / "project-policy.toml"
                    )
                ),
                new_session_id="session-forged-review-002",
            )

        retained = tasks.status(self.task["task_id"])
        self.assertEqual(retained["state"], "verifying")
        self.assertNotIn("local_review_revision_finalization", retained)

    def test_t2_blocking_review_revisions_then_promotes_only_latest_evidence(self) -> None:
        """The full local-review loop consumes attempts 1 then 2, never a lease."""
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore, ReviewArtifactStore, build_independent_review_receipt,
            prepare_review_packet, publish_review_ready,
            start_local_review_revision, verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        first = verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:01:00Z")
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        packet_one = prepare_review_packet(scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "4" * 64)
        fail, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet_one,
            findings_digest="sha256:" + "5" * 64,
            critical=1, important=0, status="FAIL", observed_at="2026-08-08T10:02:00Z",
        )
        runs.persist_review_receipt(
            self.task["task_id"], packet_one["packet_digest"], fail,
            observation=review_observation,
        )
        handed_off = TaskStore(state_dir).status(self.task["task_id"])
        self.assertFalse((state_dir / "codex-control-plane" / "leases" / f"{self.task['task_id']}.json").exists())
        start_local_review_revision(
            repository=scenario.repo, task_id=self.task["task_id"],
            expected_generation=handed_off["generation"], review_receipt_digest=fail["receipt_digest"],
            new_session_id="session-review-e2e-002",
        )
        self.assertFalse(ReviewArtifactStore(scenario.repo).manifest_path(first["review_artifact"]["manifest"]).exists())
        (scenario.repo / "change.txt").write_text("corrected change\n", encoding="utf-8")
        second = verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:03:00Z")
        self.assertEqual(second["attempt"]["attempt"], 2)
        packet_two = prepare_review_packet(scenario.repo, self.task["task_id"], 2, "independent", "sha256:" + "6" * 64)
        passed, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet_two,
            findings_digest="sha256:" + "7" * 64,
            critical=0, important=0, status="PASS", observed_at="2026-08-08T10:04:00Z",
        )
        runs.persist_review_receipt(
            self.task["task_id"], packet_two["packet_digest"], passed,
            observation=review_observation,
        )
        before_publish = TaskStore(state_dir).status(self.task["task_id"])
        self.assertFalse((state_dir / "codex-control-plane" / "leases" / f"{self.task['task_id']}.json").exists())
        ready = publish_review_ready(
            repository=scenario.repo, task_id=self.task["task_id"],
            expected_generation=before_publish["generation"], receipt_digests=(passed["receipt_digest"],),
        )
        self.assertEqual(ready["state"], "review_ready")
        self.assertEqual([record["attempt"] for record in runs.attempts(self.task["task_id"])], [1, 2])
        with self.assertRaisesRegex(ValueError, "E_INDEPENDENT_REVIEW"):
            publish_review_ready(
                repository=scenario.repo, task_id=self.task["task_id"],
                expected_generation=before_publish["generation"], receipt_digests=(fail["receipt_digest"],),
            )

    def test_third_blocking_review_ends_blocked_and_cleans_artifact(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            ReviewArtifactStore,
            RunStore,
            build_independent_review_receipt,
            prepare_review_packet,
            start_local_review_revision,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests",
            "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        tasks = TaskStore(state_dir)
        final_artifact = None

        for attempt in (1, 2, 3):
            if attempt > 1:
                (scenario.repo / "change.txt").write_text(
                    f"corrected change {attempt}\n", encoding="utf-8"
                )
            result = verify_run(
                repository=scenario.repo,
                task_id=self.task["task_id"],
                observed_at=f"2026-08-08T10:0{attempt}:00Z",
            )
            packet = prepare_review_packet(
                scenario.repo,
                self.task["task_id"],
                attempt,
                "independent",
                "sha256:" + str(attempt + 3) * 64,
            )
            failed, review_observation = independent_review_receipt(
                run_store=runs, review_packet=packet,
                findings_digest="sha256:" + str(attempt + 6) * 64,
                critical=0,
                important=1,
                status="FAIL",
                observed_at=f"2026-08-08T10:0{attempt}:30Z",
            )
            runs.persist_review_receipt(
                self.task["task_id"], packet["packet_digest"], failed,
                observation=review_observation,
            )
            state = tasks.status(self.task["task_id"])
            final_artifact = ReviewArtifactStore(scenario.repo).load_manifest(
                self.task["task_id"], attempt
            )
            outcome = start_local_review_revision(
                repository=scenario.repo,
                task_id=self.task["task_id"],
                expected_generation=state["generation"],
                review_receipt_digest=failed["receipt_digest"],
                new_session_id=f"session-review-exhausted-00{attempt}",
            )
            if attempt < 3:
                self.assertEqual(outcome["state"], "implementing")

        self.assertIsNotNone(final_artifact)
        self.assertEqual(outcome["state"], "blocked")
        self.assertEqual(outcome["block_reason"], "E_RUN_EXHAUSTED")
        self.assertTrue(outcome["resume_forbidden"])
        self.assertEqual(
            ReviewArtifactStore(scenario.repo).artifact_state(final_artifact),
            "absent",
        )
        self.assertFalse(
            (
                state_dir
                / "codex-control-plane"
                / "leases"
                / f"{self.task['task_id']}.json"
            ).exists()
        )

    def test_review_correction_rejects_changed_head(self) -> None:
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore,
            build_independent_review_receipt,
            prepare_review_packet,
            start_local_review_revision,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        semantic = {key: value for key, value in self.decision.items() if key != "decision_digest"}
        self.decision["decision_digest"] = __import__("control_plane.contracts", fromlist=["contract_digest"]).contract_digest(semantic)
        scenario = self._prepared_scenario()
        first = verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:01:00Z")
        state_dir = worktree_git_dir(scenario.repo)
        store = RunStore(state_dir)
        packet = prepare_review_packet(
            scenario.repo,
            self.task["task_id"],
            1,
            "independent",
            "sha256:" + "8" * 64,
        )
        receipt, review_observation = independent_review_receipt(
            run_store=store, review_packet=packet,
            findings_digest="sha256:" + "9" * 64,
            critical=1,
            important=0,
            status="FAIL",
            observed_at="2026-08-08T10:02:00Z",
        )
        store.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], receipt,
            observation=review_observation,
        )
        git(scenario.repo, "add", "change.txt")
        git(scenario.repo, "commit", "-m", "test: wrong correction head")
        from control_plane.lifecycle import TaskStore
        state = TaskStore(state_dir).status(self.task["task_id"])
        with self.assertRaisesRegex(ValueError, "E_LOCAL_REVIEW"):
            start_local_review_revision(
                repository=scenario.repo,
                task_id=self.task["task_id"],
                expected_generation=state["generation"],
                review_receipt_digest=receipt["receipt_digest"],
                new_session_id="session-review-head-drift-002",
            )

    def test_t2_packet_handoff_releases_writer_lease_and_is_idempotent(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import prepare_review_packet, verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        semantic = {key: value for key, value in self.decision.items() if key != "decision_digest"}
        self.decision["decision_digest"] = contract_digest(semantic)
        scenario = self._prepared_scenario()
        verify_run(repository=scenario.repo, task_id=self.task["task_id"], observed_at="2026-08-08T10:01:00Z")
        first = prepare_review_packet(scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "6" * 64)
        state_dir = worktree_git_dir(scenario.repo)
        self.assertFalse((state_dir / "codex-control-plane" / "leases" / f"{self.task['task_id']}.json").exists())
        state = TaskStore(state_dir).status(self.task["task_id"])
        self.assertEqual(state["state"], "verifying")
        self.assertFalse(state["resume_forbidden"])
        self.assertEqual(state["evidence"]["review_handoff"]["attempt_digest"], first["attempt_digest"])
        self.assertEqual(prepare_review_packet(scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "6" * 64), first)

    def test_completed_handoff_replay_blocks_after_worktree_drift(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, prepare_review_packet, verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests", "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        packet = prepare_review_packet(
            scenario.repo, self.task["task_id"], 1, "independent",
            "sha256:" + "6" * 64,
        )
        (scenario.repo / "change.txt").write_text(
            "drift after handoff\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "E_REVIEW_HANDOFF_DRIFT"):
            prepare_review_packet(
                scenario.repo, self.task["task_id"], 1, "independent",
                "sha256:" + "6" * 64,
            )

        state_dir = worktree_git_dir(scenario.repo)
        task_store = TaskStore(state_dir)
        state = task_store.status(self.task["task_id"])
        self.assertEqual(state["state"], "blocked")
        self.assertTrue(state["resume_forbidden"])
        with self.assertRaisesRegex(ValueError, "E_STATE_RESUME"):
            task_store.resume(
                self.task["task_id"], current_branch="codex/run-verify"
            )
        with self.assertRaisesRegex(ValueError, "E_REVIEW_PACKET"):
            RunStore(state_dir).load_active_review_packet(
                self.task["task_id"], 1, str(packet["review_kind"]),
            )

    def test_direct_review_receipt_revalidates_live_handoff_subject(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore,
            build_independent_review_receipt,
            prepare_review_packet,
            verify_run,
        )

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = [
            "gate.relevant-tests", "gate.independent-review",
        ]
        self.decision["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in self.decision.items()
                if key != "decision_digest"
            }
        )
        scenario = self._prepared_scenario()
        verify_run(
            repository=scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-08T10:01:00Z",
        )
        packet = prepare_review_packet(
            scenario.repo, self.task["task_id"], 1, "independent",
            "sha256:" + "6" * 64,
        )
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        receipt, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet,
            findings_digest="sha256:" + "7" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        (scenario.repo / "change.txt").write_text(
            "drift before receipt\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "E_REVIEW_HANDOFF_DRIFT"):
            runs.persist_review_receipt(
                self.task["task_id"], packet["packet_digest"], receipt,
                observation=review_observation,
            )

        state = TaskStore(state_dir).status(self.task["task_id"])
        self.assertEqual(state["state"], "blocked")
        self.assertTrue(state["resume_forbidden"])
        self.assertFalse(
            runs._review_receipt_path(
                self.task["task_id"], 1, "independent"
            ).exists()
        )

    def test_pre_handoff_subject_drift_keeps_lease_and_packet_inactive(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, prepare_review_packet, verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        verify_run(repository=scenario.repo, task_id=self.task["task_id"],
                   observed_at="2026-08-08T10:01:00Z")
        # This is a live untracked mutation after the stable artifact exists.
        (scenario.repo / "after-verify.txt").write_text("drift\n", encoding="utf-8")
        state_dir = worktree_git_dir(scenario.repo)

        with self.assertRaisesRegex(ValueError, "E_REVIEW_HANDOFF_DRIFT"):
            prepare_review_packet(
                scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "6" * 64,
            )

        state = TaskStore(state_dir).status(self.task["task_id"])
        self.assertEqual(state["state"], "verifying")
        self.assertTrue((state_dir / "codex-control-plane" / "leases" / f"{self.task['task_id']}.json").exists())
        with self.assertRaisesRegex(ValueError, "E_REVIEW_PACKET"):
            RunStore(state_dir).load_active_review_packet(
                self.task["task_id"], 1, "independent",
            )

    def test_post_release_subject_drift_leaves_recoverable_nonactive_marker(self) -> None:
        from unittest.mock import patch
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore, prepare_review_packet, verify_run

        self.decision["summary"]["tier"] = "T2"
        self.decision["required_gates"] = ["gate.relevant-tests", "gate.independent-review"]
        self.decision["decision_digest"] = contract_digest(
            {key: value for key, value in self.decision.items() if key != "decision_digest"}
        )
        scenario = self._prepared_scenario()
        verify_run(repository=scenario.repo, task_id=self.task["task_id"],
                   observed_at="2026-08-08T10:01:00Z")
        state_dir = worktree_git_dir(scenario.repo)
        original_release = TaskLease._release_locked

        def release_then_mutate(*args, **kwargs):
            released = original_release(*args, **kwargs)
            (scenario.repo / "after-release.txt").write_text("drift\n", encoding="utf-8")
            return released

        with patch.object(TaskLease, "_release_locked", side_effect=release_then_mutate):
            with self.assertRaisesRegex(ValueError, "E_REVIEW_HANDOFF_DRIFT"):
                prepare_review_packet(
                    scenario.repo, self.task["task_id"], 1, "independent", "sha256:" + "6" * 64,
                )

        state = TaskStore(state_dir).status(self.task["task_id"])
        self.assertEqual(state["state"], "finalizing_review_handoff")
        self.assertTrue(state["resume_forbidden"])
        self.assertNotIn("review_handoff", state.get("evidence", {}))
        with self.assertRaisesRegex(ValueError, "E_REVIEW_PACKET"):
            RunStore(state_dir).load_active_review_packet(
                self.task["task_id"], 1, "independent",
            )
        with self.assertRaisesRegex(ValueError, "E_REVIEW_HANDOFF_DRIFT"):
            TaskStore(state_dir).recover_writer_finalization(self.task["task_id"])

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
        revision = RunStore(state_dir).load_active(self.task["task_id"])

        receipt = _execute_closed_gate(
            repository=scenario.repo,
            state_dir=state_dir,
            run_plan=plan,
            run_revision=revision,
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
        revision = RunStore(state_dir).load_active(self.task["task_id"])

        receipt = _execute_closed_gate(
            repository=scenario.repo,
            state_dir=state_dir,
            run_plan=plan,
            run_revision=revision,
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

    def test_deferred_review_gate_does_not_create_an_unknown_local_receipt(self) -> None:
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

        self.assertEqual(result["task"]["state"], "review_ready")
        self.assertEqual(result["summary"]["gate_status"], "PASS")
        self.assertNotIn(
            "gate.independent-review", [item["gate_id"] for item in result["receipts"]],
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
        revision = RunStore(state_dir).load_active(self.task["task_id"])
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
                run_revision=revision,
                attempt=1,
                gate_id="gate.relevant-tests",
                argv=command,
                observed_at="2026-08-08T10:01:00Z",
            )

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(receipt["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
