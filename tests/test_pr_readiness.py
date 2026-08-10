from __future__ import annotations

import copy
import itertools
import unittest
from unittest.mock import patch

from tests import test_git_outcome_bridge as git_outcome_bridge


class PullRequestReadinessTests(unittest.TestCase):
    """Contract tests for a bounded, read-only PR readiness observation."""

    def setUp(self) -> None:
        self.flow, self.plan = self._draft_context()

    def _draft_context(self, *, required_checks=()):
        self.flow = git_outcome_bridge.GitOutcomeBridgePreparationTests(
            methodName="runTest"
        )
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.flow._push()
        self.plan = self.flow._pull_request_plan()
        if required_checks:
            from control_plane.host_bridge import build_pull_request_effect_plan

            self.plan = build_pull_request_effect_plan(
                outcome_binding=self.flow.store.status(self.flow.task["task_id"])["outcome_binding"],
                task_digest=self.flow.run_plan["task_digest"], remote="origin",
                base="main", scope_paths=("prepared.txt",),
                policy_digest=self.flow.policy_digest, title="Draft change",
                body="Draft body", required_checks=required_checks,
            )
        pr_host = git_outcome_bridge._FakePullRequestHost(
            existing=git_outcome_bridge._FakePullRequestHost.matching(self.plan)
        )
        receipt = pr_host.observe(self.plan)
        drafted = self.flow._publish_with_exact_pr_remote(self.plan, receipt)
        self.assertEqual(drafted["state"], "pr_draft")
        return self.flow, self.plan

    def _receipts(
        self,
        *,
        check_statuses: tuple[str, ...] | None = None,
        comments_status: str = "PASS",
        thread_feedback: tuple[tuple[int, str, str], ...] = (),
        comment_feedback: tuple[tuple[int, str, str], ...] = (),
        pr_number: int = 7,
        pr_url: str = "https://github.com/example/control-plane/pull/7",
    ):
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import (
            build_pull_request_readiness_receipt,
        )

        required = tuple(item[3] for item in self.plan.required_checks)
        check_statuses = check_statuses or tuple("PASS" for _ in required)
        common = {
            "effect_plan": self.plan,
            "observed_at": "2026-08-09T12:00:00Z",
            "observed_repository": self.plan.repository,
            "observed_remote": self.plan.remote,
            "observed_base": self.plan.base,
            "observed_branch": self.plan.branch,
            "observed_head_sha": self.plan.head_sha,
            "observed_pr_number": pr_number,
            "observed_pr_url": pr_url,
            "observed_pr_draft": True,
        }
        def feedback_rows(rows):
            return tuple(
                (item[0], contract_digest({"feedback_id": item[0]}), item[1], item[2])
                for item in rows
            )
        return (
            build_pull_request_readiness_receipt(
                **common,
                observation_kind="checks",
                status="PASS",
                required_check_digests=required,
                check_results=tuple(zip(required, check_statuses)),
            ),
            build_pull_request_readiness_receipt(
                **common,
                observation_kind="review_threads",
                status="PASS",
                feedback=feedback_rows(thread_feedback),
            ),
            build_pull_request_readiness_receipt(
                **common,
                observation_kind="comments",
                status=comments_status,
                feedback=feedback_rows(comment_feedback),
            ),
        )

    def _publish_for(self, flow, plan, receipts):
        with flow._exact_pr_remote(plan):
            return flow.store.publish_pull_request_readiness(
                flow.task["task_id"], effect_plan=plan, receipts=receipts,
                current_branch=flow.branch,
            )

    def _publish(self, receipts):
        return self._publish_for(self.flow, self.plan, receipts)

    def _publish_ready(self, receipts):
        from control_plane.host_bridge import (
            OutcomeEffectPlanV1,
            build_pull_request_ready_outcome_receipt,
        )

        prepared = self._publish(receipts)
        ready_plan = OutcomeEffectPlanV1.from_dict(
            prepared["pending_pull_request_ready_effect"]["effect_plan"]
        )
        with self.flow._exact_pr_remote(ready_plan):
            self.flow.store.arm_pull_request_ready(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                current_branch=self.flow.branch,
            )
            self.flow.store.revalidate_pull_request_ready_before_execution(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                current_branch=self.flow.branch,
            )
        receipt = build_pull_request_ready_outcome_receipt(
            effect_plan=ready_plan,
            status="PASS",
            observed_at="2026-08-09T12:01:00Z",
            observed_repository=ready_plan.repository,
            observed_remote=ready_plan.remote,
            observed_base=ready_plan.base,
            observed_branch=ready_plan.branch,
            observed_head_sha=ready_plan.head_sha,
            observed_pr_number=7,
            observed_pr_url="https://github.com/example/control-plane/pull/7",
            observed_pr_draft=False,
            disposition="marked_ready",
        )
        with self.flow._exact_pr_remote(ready_plan):
            return self.flow.store.publish_pull_request_ready(
                self.flow.task["task_id"], effect_plan=ready_plan,
                receipt=receipt, current_branch=self.flow.branch,
            )

    def _revision_inputs(self, marker, *, reason="checks_failed"):
        """Build one exact host-observed Task 5B repair request."""

        from control_plane import host_bridge
        from control_plane.repository import worktree_git_dir
        from tests.host_adapter_test_support import lifecycle_observation

        common_dir = worktree_git_dir(self.flow.scenario.repo)
        inventory_raw = host_bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id="task5b-inventory",
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = host_bridge.validate_worktree_inventory_observation(
            inventory_raw,
            expected_common_git_dir=common_dir,
            expected_invocation_id="task5b-inventory",
            clock=lambda: 100.0,
        )
        raw = lifecycle_observation(
            host_bridge.GitHubObservation,
            observation_id="task5b-revision",
            invocation_id="task5b-revision",
            task_digest=self.flow.run_plan["task_digest"],
            repository_identity=str(self.flow.scenario.repo.resolve()),
            worktree_identity=str(self.flow.scenario.repo.resolve()),
            branch=self.flow.branch,
            prior_head=self.plan.head_sha,
            target_state="implementing",
            session_id="task5b-session",
            provider="github",
            subject_digest=marker["marker_digest"],
            evidence={
                "pull_request_number": 7,
                "prior_head": self.plan.head_sha,
                "reason": reason,
                "observation_digest": marker["marker_digest"],
            },
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        observation = host_bridge.validate_github_observation(
            raw,
            expected_task_digest=self.flow.run_plan["task_digest"],
            expected_repo=self.flow.scenario.repo,
            expected_worktree=self.flow.scenario.repo,
            expected_branch=self.flow.branch,
            expected_prior_head=self.plan.head_sha,
            expected_target_state="implementing",
            expected_session_id="task5b-session",
            expected_invocation_id="task5b-revision",
            clock=lambda: 100.0,
        )
        return {
            "expected_generation": self.flow.store.status(self.flow.task["task_id"])["generation"],
            "reason": reason,
            "observation": observation,
            "worktree_inventory": inventory,
            "worktree": str(self.flow.scenario.repo),
            "session_id": "task5b-session",
            "policy_digest": self.flow.policy_digest,
            "scope_paths": ["prepared.txt"],
            "current_branch": self.flow.branch,
        }

    def test_exact_pass_promotes_and_only_then_advances_outcome_binding(self) -> None:
        from control_plane.host_bridge import (
            OutcomeEffectPlanV1,
            build_pull_request_ready_outcome_receipt,
        )

        prepared = self._publish(self._receipts())
        self.assertEqual(prepared["state"], "pr_draft")
        marker = prepared["pending_pull_request_ready_effect"]
        self.assertEqual(marker["phase"], "prepared")
        self.assertFalse(marker["authorizes"])
        ready_plan = OutcomeEffectPlanV1.from_dict(marker["effect_plan"])
        self.assertEqual(ready_plan.effect, "pull_request")
        self.assertEqual(ready_plan.operation, "mark_pull_request_ready")
        self.assertFalse(ready_plan.draft)
        self.assertFalse(ready_plan.authorizes)
        self.assertEqual(
            prepared["outcome_binding"]["consumed_effect_ids"],
            ["local_write", "commit", "remote_write"],
        )

        with self.flow._exact_pr_remote(ready_plan):
            self.flow.store.arm_pull_request_ready(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                current_branch=self.flow.branch,
            )
            revalidated = self.flow.store.revalidate_pull_request_ready_before_execution(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                current_branch=self.flow.branch,
            )
        self.assertEqual(revalidated, ready_plan)
        ready_receipt = build_pull_request_ready_outcome_receipt(
            effect_plan=ready_plan,
            status="PASS",
            observed_at="2026-08-09T12:01:00Z",
            observed_repository=ready_plan.repository,
            observed_remote=ready_plan.remote,
            observed_base=ready_plan.base,
            observed_branch=ready_plan.branch,
            observed_head_sha=ready_plan.head_sha,
            observed_pr_number=7,
            observed_pr_url="https://github.com/example/control-plane/pull/7",
            observed_pr_draft=False,
            disposition="marked_ready",
        )
        with self.flow._exact_pr_remote(ready_plan):
            ready = self.flow.store.publish_pull_request_ready(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                receipt=ready_receipt,
                current_branch=self.flow.branch,
            )

        self.assertEqual(ready["state"], "pr_ready")
        self.assertEqual(
            ready["outcome_binding"]["consumed_effect_ids"][-1], "pull_request"
        )
        self.assertIsNotNone(ready["outcome_binding"]["pull_request_digest"])
        self.assertIsNotNone(ready["outcome_binding"]["checks_digest"])
        self.assertFalse(ready["evidence"]["pr_ready"]["authorizes"])

    def test_ready_unknown_is_observe_only_and_cannot_rearm_or_repair(self) -> None:
        from control_plane.host_bridge import (
            OutcomeEffectPlanV1,
            build_pull_request_ready_outcome_receipt,
        )

        prepared = self._publish(self._receipts())
        ready_plan = OutcomeEffectPlanV1.from_dict(
            prepared["pending_pull_request_ready_effect"]["effect_plan"]
        )
        with self.flow._exact_pr_remote(ready_plan):
            self.flow.store.arm_pull_request_ready(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                current_branch=self.flow.branch,
            )
            self.flow.store.revalidate_pull_request_ready_before_execution(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                current_branch=self.flow.branch,
            )
        unknown = build_pull_request_ready_outcome_receipt(
            effect_plan=ready_plan,
            status="UNKNOWN",
            observed_at="2026-08-09T12:01:00Z",
        )
        with self.flow._exact_pr_remote(ready_plan):
            blocked = self.flow.store.publish_pull_request_ready(
                self.flow.task["task_id"],
                effect_plan=ready_plan,
                receipt=unknown,
                current_branch=self.flow.branch,
            )
        self.assertEqual(
            (blocked["state"], blocked["resume_state"], blocked["block_reason"]),
            ("blocked", "pr_draft", "E_PR_READY_OUTCOME_UNKNOWN"),
        )
        self.assertEqual(
            blocked["pending_pull_request_ready_effect"]["phase"],
            "observe_only",
        )
        self.assertNotIn("revision_required", blocked)
        self.assertNotIn(
            "pull_request", blocked["outcome_binding"]["consumed_effect_ids"]
        )
        with self.flow._exact_pr_remote(ready_plan):
            with self.assertRaisesRegex(ValueError, "E_PR_READY_OBSERVE_ONLY"):
                self.flow.store.arm_pull_request_ready(
                    self.flow.task["task_id"],
                    effect_plan=ready_plan,
                    current_branch=self.flow.branch,
                )

    def test_ready_marker_rejects_coherent_pr_identity_rebinding(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import build_pull_request_ready_effect_plan
        from control_plane.lifecycle import _atomic_json

        prepared = self._publish(self._receipts())
        state = copy.deepcopy(prepared)
        alternate_receipts = self._receipts(
            pr_number=8,
            pr_url="https://github.com/example/control-plane/pull/8",
        )
        alternate_digests = [
            receipt.receipt_digest for receipt in alternate_receipts
        ]
        alternate_plan = build_pull_request_ready_effect_plan(
            draft_effect_plan=self.plan,
            outcome_binding=state["outcome_binding"],
            pull_request_number=8,
            pull_request_url="https://github.com/example/control-plane/pull/8",
            readiness_receipts=alternate_receipts,
        )
        marker = state["pending_pull_request_ready_effect"]
        marker.update({
            "effect_plan": alternate_plan.to_dict(),
            "pr_draft": {
                "number": 8,
                "url": "https://github.com/example/control-plane/pull/8",
                "head_commit": self.plan.head_sha,
            },
            "readiness_receipts": [
                receipt.to_dict() for receipt in alternate_receipts
            ],
            "readiness_receipt_digests": alternate_digests,
            "pull_request_digest": contract_digest({
                "number": 8,
                "url": "https://github.com/example/control-plane/pull/8",
                "head": self.plan.head_sha,
                "draft": True,
            }),
            "checks_digest": contract_digest(alternate_digests),
        })
        marker["marker_digest"] = contract_digest({
            key: value for key, value in marker.items()
            if key != "marker_digest"
        })
        state["pr_readiness_receipt_digests"] = alternate_digests
        state["evidence"]["pr_readiness"] = {
            "status": "PASS",
            "receipt_digests": alternate_digests,
            "pull_request_digest": marker["pull_request_digest"],
            "checks_digest": marker["checks_digest"],
            "authorizes": False,
        }
        _atomic_json(self.flow.store._path(self.flow.task["task_id"]), state)

        with self.flow._exact_pr_remote(alternate_plan):
            with self.assertRaisesRegex(ValueError, "E_PR_READY_PREPARE"):
                self.flow.store.arm_pull_request_ready(
                    self.flow.task["task_id"],
                    effect_plan=alternate_plan,
                    current_branch=self.flow.branch,
                )

    def test_unknown_comments_blocks_without_consuming_pull_request_attempt(self) -> None:
        blocked = self._publish(self._receipts(comments_status="UNKNOWN"))

        self.assertEqual((blocked["state"], blocked["block_reason"]), (
            "blocked", "E_PR_READINESS_COMMENTS_UNKNOWN",
        ))
        self.assertNotIn("pull_request", blocked["outcome_binding"]["consumed_effect_ids"])

    def test_failed_required_check_requires_closed_revision_marker(self) -> None:
        receipts = self._receipts(check_statuses=("FAIL",))
        blocked = self._publish(receipts)

        self.assertEqual(blocked["state"], "pr_draft")
        self.assertEqual(blocked["block_reason"], "E_PR_READINESS_REVISION_REQUIRED")
        marker = blocked["revision_required"]
        self.assertEqual(marker["kind"], "PullRequestRevisionRequiredV1")
        self.assertEqual(marker["reason"], "checks_failed")
        self.assertFalse(marker["authorizes"])
        self.assertEqual(marker["generation"], blocked["generation"])
        self.assertEqual(marker["head_sha"], self.plan.head_sha)
        self.assertEqual(
            marker["receipts"], [receipt.to_dict() for receipt in receipts]
        )
        self.assertEqual(
            marker["receipt_digests"], [receipt.receipt_digest for receipt in receipts]
        )
        with self.assertRaisesRegex(ValueError, "E_PR_READINESS_REVISION_REQUIRED"):
            self._publish(self._receipts())
        self.assertNotIn("pull_request", blocked["outcome_binding"]["consumed_effect_ids"])

    def test_pr_revision_consumes_only_the_exact_task5a_marker(self) -> None:
        from control_plane import host_bridge
        from control_plane.repository import worktree_git_dir
        from tests.host_adapter_test_support import lifecycle_observation

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        marker = blocked["revision_required"]
        common_dir = worktree_git_dir(self.flow.scenario.repo)
        inventory_raw = host_bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir, invocation_id="task5b-inventory",
            clock=lambda: 100.0, ttl_seconds=30, max_output_bytes=1_000_000,
        )
        inventory = host_bridge.validate_worktree_inventory_observation(
            inventory_raw, expected_common_git_dir=common_dir,
            expected_invocation_id="task5b-inventory", clock=lambda: 100.0,
        )
        raw = lifecycle_observation(
            host_bridge.GitHubObservation, observation_id="task5b-revision",
            invocation_id="task5b-revision", task_digest=self.flow.run_plan["task_digest"],
            repository_identity=str(self.flow.scenario.repo.resolve()),
            worktree_identity=str(self.flow.scenario.repo.resolve()),
            branch=self.flow.branch, prior_head=self.plan.head_sha,
            target_state="implementing", session_id="task5b-session", provider="github",
            subject_digest=marker["marker_digest"], evidence={
                "pull_request_number": 7, "prior_head": self.plan.head_sha,
                "reason": "checks_failed", "observation_digest": marker["marker_digest"],
            }, observed_at_monotonic=100.0, freshness_deadline=130.0,
        )
        observation = host_bridge.validate_github_observation(
            raw, expected_task_digest=self.flow.run_plan["task_digest"],
            expected_repo=self.flow.scenario.repo, expected_worktree=self.flow.scenario.repo,
            expected_branch=self.flow.branch, expected_prior_head=self.plan.head_sha,
            expected_target_state="implementing", expected_session_id="task5b-session",
            expected_invocation_id="task5b-revision", clock=lambda: 100.0,
        )
        implementing = self.flow.store.start_revision(
            self.flow.task["task_id"], expected_generation=blocked["generation"],
            reason="checks_failed", observation=observation,
            worktree_inventory=inventory, worktree=str(self.flow.scenario.repo),
            session_id="task5b-session", policy_digest=self.flow.policy_digest,
            scope_paths=["prepared.txt"], current_branch=self.flow.branch,
        )
        self.assertEqual(implementing["state"], "implementing")
        from control_plane.run_workflow import RunStore
        revision = RunStore(worktree_git_dir(self.flow.scenario.repo)).load_active(
            self.flow.task["task_id"]
        )
        self.assertEqual((revision["reason"], revision["first_attempt"], revision["head"]),
                         ("pull_request_feedback", 2, self.plan.head_sha))
        self.assertEqual(revision["source_review_receipt_digest"], marker["marker_digest"])
        self.assertEqual(implementing["active_run_revision_digest"], revision["revision_digest"])
        self.assertNotIn("revision_required", implementing)
        self.assertNotIn("pr_draft", implementing["evidence"])
        self.assertNotIn("pr_readiness", implementing["evidence"])

    def test_pr_revision_invalidates_superseded_delivery_and_review_bindings(self) -> None:
        from control_plane.run_workflow import RunStore

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        before = copy.deepcopy(blocked)

        implementing = self.flow.store.start_revision(
            self.flow.task["task_id"],
            **self._revision_inputs(blocked["revision_required"]),
        )

        self.assertEqual(implementing["state"], "implementing")
        self.assertEqual(
            RunStore(self.flow.store.state_dir).load_plan(self.flow.task["task_id"])["requested_outcome"],
            before["outcome"],
        )
        self.assertEqual(len(implementing["pull_request_history"]), 1)
        history = implementing["pull_request_history"][0]
        self.assertEqual(history["revision"], before["revision"])
        self.assertEqual(history["attempt"], 1)
        self.assertEqual(history["number"], 7)
        self.assertEqual(history["head"], self.plan.head_sha)
        self.assertEqual(history["reason"], "checks_failed")
        self.assertEqual(history["marker_digest"], before["revision_required"]["marker_digest"])
        self.assertEqual(history["receipt_digests"], before["revision_required"]["receipt_digests"])
        self.assertEqual(history["source_attempt_digest"], before["review_attempt_digest"])
        for key in (
            "outcome_binding", "delivery_review_binding", "review_attempt_digest",
            "review_promotion_digest", "review_packet_digest", "review_receipt_digests",
            "pr_readiness_receipt_digests", "pull_request_effect_plan",
        ):
            self.assertNotIn(key, implementing)

    def test_third_consumed_attempt_blocks_before_observation_or_lease(self) -> None:
        from control_plane.run_workflow import RunStore
        from control_plane.lifecycle import TaskLease

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        revision_inputs = self._revision_inputs(blocked["revision_required"])
        runs = RunStore(self.flow.store.state_dir)
        attempts = runs.attempts(self.flow.task["task_id"])
        exhausted = [*attempts[:-1], {**attempts[-1], "attempt": 3}]
        before = copy.deepcopy(blocked)
        with patch.object(RunStore, "attempts", return_value=exhausted), \
             patch("control_plane.lifecycle.consume_lifecycle_observation", side_effect=AssertionError("observation consumed")), \
             patch.object(TaskLease, "_acquire_locked", side_effect=AssertionError("lease acquired")), \
             patch.object(runs, "write_review_revision", side_effect=AssertionError("revision written")):
            result = self.flow.store.start_revision(
                self.flow.task["task_id"], **revision_inputs,
            )
        self.assertEqual((result["state"], result["block_reason"]), (
            "blocked", "E_REVISION_EXHAUSTED",
        ))
        self.assertEqual(result["generation"], before["generation"])
        self.assertEqual(result["revision_required"], before["revision_required"])

    def test_revision_local_branch_or_head_drift_fails_before_marker_mutation(self) -> None:
        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        before = copy.deepcopy(blocked)
        with patch("control_plane.lifecycle._revision_worktree_is_current", return_value=False):
            with self.assertRaisesRegex(ValueError, "E_REVISION_BINDING"):
                self.flow.store.start_revision(
                    self.flow.task["task_id"],
                    **self._revision_inputs(blocked["revision_required"]),
                )
        self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)

    def test_revision_final_state_write_fault_recovers_exact_pending_revision(self) -> None:
        from control_plane import lifecycle
        from control_plane.run_workflow import RunStore

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        original_atomic_json = lifecycle._atomic_json
        state_path = self.flow.store._path(self.flow.task["task_id"])

        def fail_only_final_state(path, value):
            if path == state_path and value.get("state") == "implementing":
                raise OSError("final state write fault")
            return original_atomic_json(path, value)

        with patch("control_plane.lifecycle._atomic_json", side_effect=fail_only_final_state):
            with self.assertRaisesRegex(OSError, "final state write fault"):
                self.flow.store.start_revision(
                    self.flow.task["task_id"],
                    **self._revision_inputs(blocked["revision_required"]),
                )

        pending = self.flow.store.status(self.flow.task["task_id"])
        self.assertEqual(pending["state"], "finalizing_revision")
        self.assertIsNotNone(self.flow.store._read_owner_lease(self.flow.task["task_id"]))
        revision = RunStore(self.flow.store.state_dir).load_active(self.flow.task["task_id"])
        self.assertEqual(revision["reason"], "pull_request_feedback")

        recovered = self.flow.store.recover_revision_start(self.flow.task["task_id"])
        self.assertEqual(recovered["state"], "implementing")
        self.assertEqual(recovered["active_run_revision_digest"], revision["revision_digest"])
        for key in ("outcome_binding", "delivery_review_binding", "review_attempt_digest"):
            self.assertNotIn(key, recovered)

    def test_revision_acquisition_or_write_fault_restores_task_without_orphan(self) -> None:
        from control_plane.lifecycle import TaskLease
        from control_plane.run_workflow import RunStore

        for fault in ("acquire", "write"):
            with self.subTest(fault=fault):
                flow, plan = self._draft_context()
                previous, self.plan = self.plan, plan
                blocked = self._publish_for(flow, plan, self._receipts(check_statuses=("FAIL",)))
                self.plan = previous
                inputs = self._revision_inputs(blocked["revision_required"])
                before = copy.deepcopy(blocked)
                if fault == "acquire":
                    target = patch.object(
                        TaskLease, "_acquire_locked", side_effect=OSError("lease fault")
                    )
                else:
                    target = patch.object(
                        RunStore, "write_review_revision", side_effect=OSError("revision fault")
                    )
                with target, self.assertRaisesRegex(OSError, "fault"):
                    flow.store.start_revision(flow.task["task_id"], **inputs)
                self.assertEqual(flow.store.status(flow.task["task_id"]), before)
                self.assertIsNone(flow.store._read_owner_lease(flow.task["task_id"]))

    def test_revision_write_that_persists_then_raises_recovers_without_rollback(self) -> None:
        from control_plane.run_workflow import RunStore

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        original_write = RunStore.write_review_revision

        def persist_then_raise(store, revision):
            original_write(store, revision)
            raise OSError("write returned after durable persistence")

        with patch.object(RunStore, "write_review_revision", new=persist_then_raise):
            with self.assertRaisesRegex(OSError, "durable persistence"):
                self.flow.store.start_revision(
                    self.flow.task["task_id"],
                    **self._revision_inputs(blocked["revision_required"]),
                )
        pending = self.flow.store.status(self.flow.task["task_id"])
        self.assertEqual(pending["state"], "finalizing_revision")
        self.assertIsNotNone(self.flow.store._read_owner_lease(self.flow.task["task_id"]))
        recovered = self.flow.store.recover_revision_start(self.flow.task["task_id"])
        self.assertEqual(recovered["state"], "implementing")

    def test_revision_recovery_without_lease_deletes_only_exact_orphan_and_restores_marker(self) -> None:
        from control_plane import lifecycle
        from control_plane.lifecycle import TaskLease
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import RunStore

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        original_atomic_json = lifecycle._atomic_json
        state_path = self.flow.store._path(self.flow.task["task_id"])
        with patch(
            "control_plane.lifecycle._atomic_json",
            side_effect=lambda path, value: (
                (_ for _ in ()).throw(OSError("final state write fault"))
                if path == state_path and value.get("state") == "implementing"
                else original_atomic_json(path, value)
            ),
        ):
            with self.assertRaisesRegex(OSError, "final state write fault"):
                self.flow.store.start_revision(
                    self.flow.task["task_id"],
                    **self._revision_inputs(blocked["revision_required"]),
                )
        pending = self.flow.store.status(self.flow.task["task_id"])
        lease = self.flow.store._read_owner_lease(self.flow.task["task_id"])
        TaskLease.release(
            worktree_git_dir(self.flow.scenario.repo), self.flow.store.state_dir,
            task_id=self.flow.task["task_id"], worktree=lease["worktree"],
            branch=lease["branch"], session_id=lease["session_id"],
            policy_digest=lease["policy_digest"], lease_digest=lease["lease_digest"],
        )
        revision = pending["revision_finalization"]["run_revision"]
        revision_path = RunStore(self.flow.store.state_dir)._revision_path(
            self.flow.task["task_id"], revision["revision"]
        )
        self.assertTrue(revision_path.exists())

        restored = self.flow.store.recover_revision_start(self.flow.task["task_id"])
        self.assertEqual(restored["state"], "pr_draft")
        self.assertFalse(restored["resume_forbidden"])
        self.assertEqual(restored["revision_required"], blocked["revision_required"])
        self.assertFalse(revision_path.exists())

    def test_foreign_revision_recovery_is_unknown_and_preserves_marker(self) -> None:
        from control_plane import lifecycle
        from control_plane.contracts import contract_digest

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        original_atomic_json = lifecycle._atomic_json
        state_path = self.flow.store._path(self.flow.task["task_id"])
        with patch(
            "control_plane.lifecycle._atomic_json",
            side_effect=lambda path, value: (
                (_ for _ in ()).throw(OSError("final state write fault"))
                if path == state_path and value.get("state") == "implementing"
                else original_atomic_json(path, value)
            ),
        ):
            with self.assertRaisesRegex(OSError, "final state write fault"):
                self.flow.store.start_revision(
                    self.flow.task["task_id"],
                    **self._revision_inputs(blocked["revision_required"]),
                )
        foreign = self.flow.store.status(self.flow.task["task_id"])
        foreign["revision_finalization"]["lease"]["session_id"] = "foreign-session"
        finalization = foreign["revision_finalization"]
        finalization["finalization_digest"] = contract_digest({
            key: value for key, value in finalization.items()
            if key != "finalization_digest"
        })
        lifecycle._atomic_json(state_path, foreign)
        before = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))

        with self.assertRaisesRegex(ValueError, "E_REVISION_RECOVERY_UNKNOWN"):
            self.flow.store.recover_revision_start(self.flow.task["task_id"])
        self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)

    def test_revision_recovery_never_applies_recalculated_marker_next_state(self) -> None:
        from control_plane import lifecycle
        from control_plane.contracts import contract_digest

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        original_atomic_json = lifecycle._atomic_json
        state_path = self.flow.store._path(self.flow.task["task_id"])
        with patch(
            "control_plane.lifecycle._atomic_json",
            side_effect=lambda path, value: (
                (_ for _ in ()).throw(OSError("final state write fault"))
                if path == state_path and value.get("state") == "implementing"
                else original_atomic_json(path, value)
            ),
        ):
            with self.assertRaisesRegex(OSError, "final state write fault"):
                self.flow.store.start_revision(
                    self.flow.task["task_id"],
                    **self._revision_inputs(blocked["revision_required"]),
                )
        injected = self.flow.store.status(self.flow.task["task_id"])
        finalization = injected["revision_finalization"]
        finalization["next_state"]["state"] = "closed"
        finalization["finalization_digest"] = contract_digest({
            key: value for key, value in finalization.items()
            if key != "finalization_digest"
        })
        lifecycle._atomic_json(state_path, injected)
        before = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))

        with self.assertRaisesRegex(ValueError, "E_REVISION_RECOVERY_UNKNOWN"):
            self.flow.store.recover_revision_start(self.flow.task["task_id"])
        self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)

    def test_revision_recovery_rejects_coherent_but_rebuilt_run_revision_drift(self) -> None:
        from control_plane import lifecycle
        from control_plane.contracts import contract_digest
        from control_plane.run_workflow import RunStore

        blocked = self._publish(self._receipts(check_statuses=("FAIL",)))
        original_atomic_json = lifecycle._atomic_json
        state_path = self.flow.store._path(self.flow.task["task_id"])
        with patch.object(RunStore, "write_review_revision", return_value=None), patch(
            "control_plane.lifecycle._atomic_json",
            side_effect=lambda path, value: (
                (_ for _ in ()).throw(OSError("final state write fault"))
                if path == state_path and value.get("state") == "implementing"
                else original_atomic_json(path, value)
            ),
        ):
            with self.assertRaisesRegex(OSError, "final state write fault"):
                self.flow.store.start_revision(
                    self.flow.task["task_id"],
                    **self._revision_inputs(blocked["revision_required"]),
                )
        tampered = self.flow.store.status(self.flow.task["task_id"])
        finalization = tampered["revision_finalization"]
        revision = finalization["run_revision"]
        revision["head"] = "a" * 40
        revision["revision_digest"] = contract_digest({
            key: value for key, value in revision.items()
            if key != "revision_digest"
        })
        finalization["next_state"]["active_run_revision_digest"] = revision["revision_digest"]
        finalization["finalization_digest"] = contract_digest({
            key: value for key, value in finalization.items()
            if key != "finalization_digest"
        })
        lifecycle._atomic_json(state_path, tampered)
        before = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))

        with patch.object(RunStore, "write_review_revision", side_effect=AssertionError("must not write")):
            with self.assertRaisesRegex(ValueError, "E_REVISION_RECOVERY_UNKNOWN"):
                self.flow.store.recover_revision_start(self.flow.task["task_id"])
        self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)

    def test_revision_marker_rejects_clean_fabrication_and_any_bound_drift(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import _atomic_json

        initial = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))
        clean = copy.deepcopy(initial)
        pr = clean["evidence"]["pr_draft"]["pull_request"]
        plan = clean["pull_request_effect_plan"]
        core = {
            "schema_version": 1,
            "kind": "PullRequestRevisionRequiredV1",
            "task_id": self.flow.task["task_id"],
            "generation": clean["generation"],
            "pull_request": pr,
            "head_sha": self.plan.head_sha,
            "effect_plan_digest": plan["plan_digest"],
            "policy_digest": plan["policy_digest"],
            "outcome_binding_digest": clean["outcome_binding"]["binding_digest"],
            "receipt_digests": [
                "sha256:" + character * 64 for character in ("d", "e", "f")
            ],
            "reason": "checks_failed",
            "authorizes": False,
        }
        fabricated = {**core, "marker_digest": contract_digest(core)}
        clean["revision_required"] = fabricated
        clean["resume_forbidden"] = True
        _atomic_json(self.flow.store._path(self.flow.task["task_id"]), clean)
        before = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))
        with self.assertRaisesRegex(ValueError, "marker is invalid"):
            self._publish(self._receipts())
        self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)

        _atomic_json(self.flow.store._path(self.flow.task["task_id"]), initial)

        receipts = self._receipts(check_statuses=("FAIL",))
        blocked = self._publish(receipts)
        for field, value in (
            ("reason", "review_feedback"),
            ("receipt_digests", [
                "sha256:" + character * 64 for character in ("a", "b", "c")
            ]),
            ("receipts", [receipts[0].to_dict(), receipts[2].to_dict()]),
        ):
            with self.subTest(field=field):
                drifted = copy.deepcopy(blocked)
                drifted["revision_required"][field] = value
                marker = drifted["revision_required"]
                marker["marker_digest"] = contract_digest(
                    {key: item for key, item in marker.items() if key != "marker_digest"}
                )
                _atomic_json(self.flow.store._path(self.flow.task["task_id"]), drifted)
                before = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))
                with self.assertRaisesRegex(ValueError, "marker is invalid"):
                    self._publish(self._receipts())
                self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)
        for label, mutate in (
            ("missing_registry", lambda state: state.__setitem__(
                "pr_readiness_receipt_digests", state["pr_readiness_receipt_digests"][:2]
            )),
            ("duplicate_registry", lambda state: state.__setitem__(
                "pr_readiness_receipt_digests", [
                    state["pr_readiness_receipt_digests"][0],
                    state["pr_readiness_receipt_digests"][0],
                    state["pr_readiness_receipt_digests"][2],
                ]
            )),
            ("evidence", lambda state: state["evidence"].__setitem__(
                "pr_readiness", {"status": "PASS", "receipt_digests": [], "authorizes": False}
            )),
        ):
            with self.subTest(label=label):
                drifted = copy.deepcopy(blocked)
                mutate(drifted)
                _atomic_json(self.flow.store._path(self.flow.task["task_id"]), drifted)
                before = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))
                with self.assertRaisesRegex(ValueError, "marker is invalid"):
                    self._publish(self._receipts())
                self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)
        payload_mutation = copy.deepcopy(blocked)
        payload_mutation["revision_required"]["receipts"][0]["status"] = "UNKNOWN"
        marker = payload_mutation["revision_required"]
        marker["marker_digest"] = contract_digest(
            {key: item for key, item in marker.items() if key != "marker_digest"}
        )
        _atomic_json(self.flow.store._path(self.flow.task["task_id"]), payload_mutation)
        before = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))
        with self.assertRaisesRegex(ValueError, "marker is invalid"):
            self._publish(self._receipts())
        self.assertEqual(self.flow.store.status(self.flow.task["task_id"]), before)
        _atomic_json(self.flow.store._path(self.flow.task["task_id"]), blocked)

    def test_unknown_required_check_blocks_without_consuming_pull_request_attempt(self) -> None:
        blocked = self._publish(self._receipts(check_statuses=("UNKNOWN",)))

        self.assertEqual((blocked["state"], blocked["block_reason"]), (
            "blocked", "E_PR_READINESS_CHECKS_UNKNOWN",
        ))
        self.assertNotIn("pull_request", blocked["outcome_binding"]["consumed_effect_ids"])

    def test_unknown_dominates_failed_checks_regardless_of_selector_order(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import _atomic_json
        from control_plane.policy import parse_required_check_selector

        candidates = (
            parse_required_check_selector("alpha:control-plane:SUCCESS"),
            parse_required_check_selector("beta:control-plane:SUCCESS"),
        )
        for statuses in (("UNKNOWN", "FAIL"), ("FAIL", "UNKNOWN")):
            with self.subTest(statuses=statuses):
                flow, plan = self._draft_context(required_checks=candidates)
                previous, self.plan = self.plan, plan
                receipts = self._receipts(check_statuses=statuses)
                self.plan = previous
                blocked = self._publish_for(flow, plan, receipts)
                self.assertEqual((blocked["state"], blocked["block_reason"]), (
                    "blocked", "E_PR_READINESS_CHECKS_UNKNOWN",
                ))
                self.assertNotIn("revision_required", blocked)
                self.assertNotIn("pull_request", blocked["outcome_binding"]["consumed_effect_ids"])

                marker = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))
                marker["revision_required"] = {
                    "schema_version": 1,
                    "kind": "PullRequestRevisionRequiredV1",
                    "task_id": flow.task["task_id"],
                    "generation": marker["generation"],
                    "pull_request": marker["evidence"]["pr_draft"]["pull_request"],
                    "head_sha": plan.head_sha,
                    "effect_plan_digest": plan.plan_digest,
                    "policy_digest": plan.policy_digest,
                    "outcome_binding_digest": marker["outcome_binding"]["binding_digest"],
                    "receipts": [receipt.to_dict() for receipt in receipts],
                    "receipt_digests": [receipt.receipt_digest for receipt in receipts],
                    "reason": "checks_failed",
                    "authorizes": False,
                }
                revision = marker["revision_required"]
                revision["marker_digest"] = contract_digest(revision)
                marker["state"] = "pr_draft"
                marker["block_reason"] = "E_PR_READINESS_REVISION_REQUIRED"
                marker["resume_forbidden"] = True
                marker["pr_readiness_receipt_digests"] = revision["receipt_digests"]
                marker["evidence"]["pr_readiness"] = {
                    "status": "FAIL", "receipt_digests": revision["receipt_digests"],
                    "authorizes": False,
                }
                _atomic_json(flow.store._path(flow.task["task_id"]), marker)
                with flow._exact_pr_remote(plan):
                    with self.assertRaisesRegex(ValueError, "marker is invalid"):
                        flow.store.publish_pull_request_readiness(
                            flow.task["task_id"], effect_plan=plan,
                            receipts=self._receipts(), current_branch=flow.branch,
                        )

    def test_feedback_severity_distinguishes_revision_from_minor_waiting(self) -> None:
        for severity, expected_state, expected_reason in (
            ("Critical", "pr_draft", "E_PR_READINESS_REVISION_REQUIRED"),
            ("Important", "pr_draft", "E_PR_READINESS_REVISION_REQUIRED"),
            ("Minor", "blocked", "E_PR_READINESS_UNRESOLVED_MINOR"),
        ):
            with self.subTest(severity=severity):
                flow, plan = self._draft_context()
                prior_plan, self.plan = self.plan, plan
                receipts = self._receipts(
                    thread_feedback=((31, severity, "unresolved"),)
                )
                self.plan = prior_plan
                result = self._publish_for(flow, plan, receipts)
                self.assertEqual((result["state"], result["block_reason"]),
                                 (expected_state, expected_reason))
                if severity == "Minor":
                    self.assertNotIn("revision_required", result)
                else:
                    self.assertEqual(result["revision_required"]["reason"], "review_feedback")

    def test_plan_required_checks_are_digest_canonical_not_name_ordered(self) -> None:
        from control_plane.host_bridge import build_pull_request_effect_plan
        from control_plane.policy import parse_required_check_selector

        candidates = [
            parse_required_check_selector(f"check-{index}:control-plane:SUCCESS")
            for index in range(8)
        ]
        first, second = next(
            (left, right)
            for left, right in itertools.combinations(candidates, 2)
            if (left.name < right.name) != (left.selector_digest < right.selector_digest)
        )
        plan = build_pull_request_effect_plan(
            outcome_binding=self.flow.store.status(self.flow.task["task_id"])["outcome_binding"],
            task_digest=self.flow.run_plan["task_digest"], remote="origin", base="main",
            scope_paths=("prepared.txt",), policy_digest=self.flow.policy_digest,
            title="Digest ordering", body="Bounded body", required_checks=(first, second),
        )
        self.assertEqual(
            tuple(item[3] for item in plan.required_checks),
            tuple(sorted((first.selector_digest, second.selector_digest))),
        )

    def test_receipt_permutations_have_identical_canonical_readiness_evidence(self) -> None:
        from control_plane.lifecycle import _atomic_json

        initial = copy.deepcopy(self.flow.store.status(self.flow.task["task_id"]))
        receipts = self._receipts()
        observed = []
        for order in itertools.permutations((0, 1, 2)):
            _atomic_json(self.flow.store._path(self.flow.task["task_id"]), initial)
            prepared = self._publish(tuple(receipts[index] for index in order))
            marker = prepared["pending_pull_request_ready_effect"]
            observed.append((
                prepared["outcome_binding"], marker["readiness_receipt_digests"],
                marker["checks_digest"], marker["effect_plan"],
            ))
        self.assertTrue(all(item == observed[0] for item in observed))

    def test_nested_required_checks_and_feedback_schemas_are_closed(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import OutcomeEffectPlanV1, RemoteOutcomeReceiptV1

        plan = self.plan.to_dict()
        for mutation in (
            {**plan["required_checks"][0], "grant": "no"},
            [plan["required_checks"][0]],
        ):
            with self.subTest(required_checks=mutation):
                payload = copy.deepcopy(plan)
                payload["required_checks"] = [mutation]
                payload["plan_digest"] = contract_digest(
                    {key: value for key, value in payload.items() if key != "plan_digest"}
                )
                with self.assertRaisesRegex(ValueError, "E_OUTCOME_EFFECT_PLAN"):
                    OutcomeEffectPlanV1.from_dict(payload)
        receipt = self._receipts()[1].to_dict()
        receipt["feedback"] = [{
            "id": 1, "digest": "sha256:" + "a" * 64, "severity": "Minor",
            "status": "resolved", "provider_text": "ignored?",
        }]
        receipt["receipt_digest"] = contract_digest(
            {key: value for key, value in receipt.items() if key != "receipt_digest"}
        )
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
            RemoteOutcomeReceiptV1.from_dict(receipt)

    def test_schema_is_closed_non_authorizing_bounded_and_rejects_replay(self) -> None:
        from control_plane.host_bridge import RemoteOutcomeReceiptV1

        receipt = self._receipts()[0]
        payload = receipt.to_dict()
        self.assertFalse(receipt.authorizes)
        self.assertEqual(RemoteOutcomeReceiptV1.from_dict(payload), receipt)
        for field, value in (
            ("session_id", "session"), ("invocation_id", "call"),
            ("nonce", "nonce"), ("ttl", 10), ("grant", "grant"),
            ("credential", "secret"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
                    RemoteOutcomeReceiptV1.from_dict({**payload, field: value})
        oversized = copy.deepcopy(payload)
        oversized["feedback"] = [
            {"id": index + 1, "digest": "sha256:" + "a" * 64,
             "severity": "Minor", "status": "resolved"}
            for index in range(256)
        ]
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
            RemoteOutcomeReceiptV1.from_dict(oversized)

        self._publish(self._receipts())
        with self.assertRaisesRegex(ValueError, "E_PR_READINESS_REPLAY"):
            self._publish(self._receipts())

    def test_missing_duplicate_extra_or_mismatched_pr_proof_fails_closed(self) -> None:
        receipts = list(self._receipts())
        missing = tuple(receipts[:2])
        with self.assertRaisesRegex(ValueError, "E_PR_READINESS_RECEIPTS"):
            self._publish(missing)
        duplicate = (receipts[0], receipts[0], receipts[2])
        with self.assertRaisesRegex(ValueError, "E_PR_READINESS_RECEIPTS"):
            self._publish(duplicate)
        drifted = copy.deepcopy(receipts[0].to_dict())
        drifted["observed_head_sha"] = "a" * 40
        from control_plane.host_bridge import RemoteOutcomeReceiptV1
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
            RemoteOutcomeReceiptV1.from_dict(drifted)

    def test_local_required_gates_cannot_substitute_pr_check_selectors(self) -> None:
        receipts = list(self._receipts())
        receipts[0] = self._receipts(
            check_statuses=("PASS",),
        )[0]
        payload = receipts[0].to_dict()
        payload["required_check_digests"] = [
            "sha256:" + "f" * 64
        ]
        payload["check_results"] = [["sha256:" + "f" * 64, "PASS"]]
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import RemoteOutcomeReceiptV1
        payload["receipt_digest"] = contract_digest(
            {key: value for key, value in payload.items() if key != "receipt_digest"}
        )
        receipts[0] = RemoteOutcomeReceiptV1.from_dict(payload)
        with self.assertRaisesRegex(ValueError, "E_PR_READINESS_CHECKS"):
            self._publish(tuple(receipts))

    def test_omitted_or_added_required_selector_fails_closed(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import RemoteOutcomeReceiptV1

        original = self._receipts()[0].to_dict()
        for selectors in ([], [
            *original["required_check_digests"], "sha256:" + "e" * 64,
        ]):
            with self.subTest(selectors=selectors):
                payload = copy.deepcopy(original)
                payload["required_check_digests"] = selectors
                payload["check_results"] = [[selector, "PASS"] for selector in selectors]
                payload["receipt_digest"] = contract_digest(
                    {key: value for key, value in payload.items() if key != "receipt_digest"}
                )
                receipts = list(self._receipts())
                receipts[0] = RemoteOutcomeReceiptV1.from_dict(payload)
                with self.assertRaisesRegex(ValueError, "E_PR_READINESS_CHECKS"):
                    self._publish(tuple(receipts))

    def test_generic_transition_cannot_bypass_readiness_receipts(self) -> None:
        with self.assertRaisesRegex(ValueError, "E_PR_READINESS_PROOF"):
            self.flow.store.transition(
                self.flow.task["task_id"],
                "pr_ready",
                evidence={"checks_ok": {"ok": True, "head_commit": self.plan.head_sha}},
                current_branch=self.flow.branch,
            )


if __name__ == "__main__":
    unittest.main()
