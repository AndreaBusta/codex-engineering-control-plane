from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.contracts import contract_digest

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_dir = Path(self.temp.name)
        self.digest = contract_digest({"test": "lifecycle"})

    def test_all_declared_legal_transitions_and_illegal_pairs(self) -> None:
        from control_plane.lifecycle import (
            LEGAL_TRANSITIONS,
            ORDERED_STATES,
            transition_allowed,
        )

        for source in ORDERED_STATES:
            for target in ORDERED_STATES:
                with self.subTest(source=source, target=target):
                    self.assertEqual(
                        transition_allowed(source, target),
                        target in LEGAL_TRANSITIONS[source],
                    )

    def test_blocked_preserves_resume_state(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-1",
            outcome="local_change",
            branch="codex/test",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        store.transition("TASK-1", "planned", current_branch="codex/test")
        store.transition(
            "TASK-1",
            "blocked",
            reason="Need user decision.",
            current_branch="codex/test",
        )
        blocked = store.status("TASK-1")
        self.assertEqual(blocked["resume_state"], "planned")

        resumed = store.resume("TASK-1", current_branch="codex/test")

        self.assertEqual(resumed["state"], "planned")
        self.assertIsNone(resumed["resume_state"])

    def test_requested_outcome_limits_terminal_state(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-2",
            outcome="answer",
            branch="codex/test",
            task_digest=self.digest,
            decision_digest=self.digest,
        )

        with self.assertRaisesRegex(ValueError, "E_STATE_OUTCOME"):
            store.transition(
                "TASK-2", "committed", current_branch="codex/test"
            )

    def test_close_requires_outcome_terminal_state(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-3",
            outcome="local_change",
            branch="codex/test",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        store.transition("TASK-3", "planned", current_branch="codex/test")
        store.transition(
            "TASK-3",
            "ready",
            evidence={"preflight_ok": True},
            current_branch="codex/test",
        )
        store.transition(
            "TASK-3", "implementing", current_branch="codex/test"
        )

        with self.assertRaisesRegex(ValueError, "E_STATE_CLOSE"):
            store.close("TASK-3", current_branch="codex/test")

    def test_task_lease_allows_same_identity_and_blocks_drift(self) -> None:
        from control_plane.lifecycle import TaskLease

        lease = TaskLease.acquire(
            self.state_dir,
            task_id="TASK-4",
            worktree="/repo/a",
            branch="codex/a",
            session_id="session-a",
            paths=["control_plane/"],
            policy_digest=self.digest,
        )
        same = TaskLease.acquire(
            self.state_dir,
            task_id="TASK-4",
            worktree="/repo/a",
            branch="codex/a",
            session_id="session-a",
            paths=["control_plane/"],
            policy_digest=self.digest,
        )
        self.assertEqual(lease["lease_digest"], same["lease_digest"])

        with self.assertRaisesRegex(ValueError, "E_LEASE_MISMATCH"):
            TaskLease.acquire(
                self.state_dir,
                task_id="TASK-4",
                worktree="/repo/b",
                branch="codex/a",
                session_id="session-a",
                paths=["control_plane/"],
                policy_digest=self.digest,
            )

    def test_task_lease_blocks_overlapping_writer_from_another_task(self) -> None:
        from control_plane.lifecycle import TaskLease

        TaskLease.acquire(
            self.state_dir,
            task_id="TASK-A",
            worktree="/repo/a",
            branch="codex/a",
            session_id="session-a",
            paths=["src/auth/**"],
            policy_digest=self.digest,
        )

        with self.assertRaisesRegex(ValueError, "E_LEASE_CONFLICT"):
            TaskLease.acquire(
                self.state_dir,
                task_id="TASK-B",
                worktree="/repo/a",
                branch="codex/b",
                session_id="session-b",
                paths=["src/**"],
                policy_digest=self.digest,
            )

    def test_task_lease_acquisition_is_atomic_for_overlapping_writers(self) -> None:
        from control_plane.lifecycle import TaskLease

        barrier = threading.Barrier(2)
        results: list[str] = []

        def acquire(task_id: str) -> None:
            barrier.wait()
            try:
                TaskLease.acquire(
                    self.state_dir,
                    task_id=task_id,
                    worktree="/repo/a",
                    branch=f"codex/{task_id.lower()}",
                    session_id=f"session-{task_id.lower()}",
                    paths=["src/**"],
                    policy_digest=self.digest,
                )
                results.append("acquired")
            except ValueError as error:
                results.append(str(error).split(":", 1)[0])

        first = threading.Thread(target=acquire, args=("TASK-A",))
        second = threading.Thread(target=acquire, args=("TASK-B",))
        first.start()
        second.start()
        first.join()
        second.join()

        self.assertCountEqual(results, ["acquired", "E_LEASE_CONFLICT"])

    def test_task_lease_validation_requires_changed_path_inventory(self) -> None:
        from control_plane.lifecycle import TaskLease

        TaskLease.acquire(
            self.state_dir,
            task_id="TASK-SCOPE-REQUIRED",
            worktree="/repo/a",
            branch="codex/a",
            session_id="session-a",
            paths=["src/**"],
            policy_digest=self.digest,
        )

        with self.assertRaisesRegex(ValueError, "E_LEASE_SCOPE"):
            TaskLease.validate(
                self.state_dir,
                task_id="TASK-SCOPE-REQUIRED",
                worktree="/repo/a",
                branch="codex/a",
                session_id="session-a",
                policy_digest=self.digest,
                changed_paths=None,  # type: ignore[arg-type]
            )

    def test_task_lease_rejects_changed_files_outside_owned_scope(self) -> None:
        from control_plane.lifecycle import TaskLease

        TaskLease.acquire(
            self.state_dir,
            task_id="TASK-SCOPE",
            worktree="/repo/a",
            branch="codex/a",
            session_id="session-a",
            paths=["src/auth/**"],
            policy_digest=self.digest,
        )

        with self.assertRaisesRegex(ValueError, "E_LEASE_SCOPE"):
            TaskLease.validate(
                self.state_dir,
                task_id="TASK-SCOPE",
                worktree="/repo/a",
                branch="codex/a",
                session_id="session-a",
                policy_digest=self.digest,
                changed_paths=["src/payments/file.py"],
            )

    def test_task_store_lease_and_receipt_reject_path_like_task_ids(
        self,
    ) -> None:
        from control_plane.lifecycle import (
            TaskLease,
            TaskStore,
            create_resource_receipt,
        )

        store = TaskStore(self.state_dir)
        with self.assertRaisesRegex(ValueError, "E_TASK_ID"):
            store.start(
                "../escape",
                outcome="answer",
                branch="feature/x",
                task_digest=self.digest,
                decision_digest=self.digest,
            )
        with self.assertRaisesRegex(ValueError, "E_TASK_ID"):
            TaskLease.acquire(
                self.state_dir,
                task_id="../escape",
                worktree="/repo/a",
                branch="feature/x",
                session_id="session-a",
                paths=["src/**"],
                policy_digest=self.digest,
            )
        with self.assertRaisesRegex(ValueError, "E_TASK_ID"):
            create_resource_receipt(
                task_id="../escape",
                decision_digest="sha256:decision",
                digests={},
                used=[],
                resource_digests={},
                omitted=[],
                gates=[],
                effects=[],
            )

    def test_receipt_contains_no_prompt_or_external_output(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import create_resource_receipt

        digest = contract_digest({"fixture": True})
        receipt = create_resource_receipt(
            task_id="TASK-5",
            decision_digest=digest,
            digests={
                "task": digest,
                "policy": digest,
                "registry": digest,
                "inventory": digest,
            },
            used=["skill.verified-workflow"],
            resource_digests={"skill.verified-workflow": digest},
            omitted=["document.operating-model"],
            gates=[
                {
                    "gate_id": "gate.diff-review",
                    "ok": True,
                    "report_digest": digest,
                }
            ],
            effects=["local_read"],
        )
        serialized = str(receipt).lower()

        self.assertNotIn("prompt", serialized)
        self.assertNotIn("external_output", serialized)
        self.assertEqual(
            receipt["used"],
            [
                {
                    "resource_id": "skill.verified-workflow",
                    "locator_digest": digest,
                    "evidence_digest": contract_digest(
                        {
                            "decision_digest": digest,
                            "resource_id": "skill.verified-workflow",
                            "locator_digest": digest,
                        }
                    ),
                }
            ],
        )

    def test_evidence_bearing_states_cannot_be_asserted_narratively(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-EVIDENCE",
            outcome="local_change",
            branch="codex/test",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        store.transition(
            "TASK-EVIDENCE", "planned", current_branch="codex/test"
        )

        with self.assertRaisesRegex(ValueError, "E_STATE_EVIDENCE"):
            store.transition(
                "TASK-EVIDENCE", "ready", current_branch="codex/test"
            )

        ready = store.transition(
            "TASK-EVIDENCE",
            "ready",
            evidence={"preflight_ok": True},
            current_branch="codex/test",
        )
        self.assertTrue(ready["evidence"]["ready"]["preflight_ok"])

    def test_false_or_untyped_evidence_cannot_advance_lifecycle(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-FALSE",
            outcome="release",
            branch="codex/release",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        store.transition(
            "TASK-FALSE", "planned", current_branch="codex/release"
        )

        with self.assertRaisesRegex(ValueError, "E_STATE_EVIDENCE"):
            store.transition(
                "TASK-FALSE",
                "ready",
                evidence={"preflight_ok": False},
                current_branch="codex/release",
            )

    def test_branch_drift_and_tampered_lease_are_rejected(self) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-BRANCH",
            outcome="answer",
            branch="codex/expected",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        with self.assertRaisesRegex(ValueError, "E_STATE_BRANCH"):
            store.transition(
                "TASK-BRANCH", "planned", current_branch="codex/other"
            )

        TaskLease.acquire(
            self.state_dir,
            task_id="TASK-LEASE-DIGEST",
            worktree="/repo/a",
            branch="codex/expected",
            session_id="session-a",
            paths=["src/**"],
            policy_digest=self.digest,
        )
        lease_path = (
            self.state_dir
            / "codex-control-plane"
            / "leases"
            / "TASK-LEASE-DIGEST.json"
        )
        import json

        payload = json.loads(lease_path.read_text(encoding="utf-8"))
        payload["paths"] = ["other"]
        lease_path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "E_LEASE_DIGEST"):
            TaskLease.validate(
                self.state_dir,
                task_id="TASK-LEASE-DIGEST",
                worktree="/repo/a",
                branch="codex/expected",
                session_id="session-a",
                policy_digest=self.digest,
                changed_paths=["src/file.py"],
            )

    def test_release_lifecycle_links_commit_pr_checks_manifest_and_build(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        branch = "codex/release"
        head = "a" * 40
        merge = "b" * 40
        store.start(
            "TASK-RELEASE",
            outcome="release",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        transitions = [
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
            (
                "review_ready",
                {
                    "gates_ok": True,
                    "documentation_decision": self.digest,
                },
            ),
            ("committed", {"commit": head}),
            ("pushed", {"remote_head": head}),
            (
                "pr_draft",
                {
                    "pull_request": {
                        "number": 7,
                        "url": "https://example.invalid/pr/7",
                        "head_commit": head,
                    }
                },
            ),
            (
                "pr_ready",
                {"checks_ok": {"ok": True, "head_commit": head}},
            ),
            ("merged", {"merge_commit": merge}),
            ("base_verified", {"remote_base": merge}),
            (
                "release_pending",
                {
                    "release_manifest": {
                        "digest": self.digest,
                        "commit": merge,
                    }
                },
            ),
            (
                "released",
                {
                    "provider_build": {
                        "provider": "testflight",
                        "build_id": "42",
                        "commit": merge,
                    }
                },
            ),
            (
                "observed",
                {
                    "observation": {
                        "status": "healthy",
                        "reference": "provider-observation-42",
                    }
                },
            ),
        ]
        for target, evidence in transitions:
            store.transition(
                "TASK-RELEASE",
                target,
                evidence=evidence,
                current_branch=branch,
            )

        closed = store.close("TASK-RELEASE", current_branch=branch)
        self.assertEqual(closed["state"], "closed")

    def test_close_releases_task_lease(self) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-CLOSE",
            outcome="answer",
            branch="codex/answer",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        TaskLease.acquire(
            self.state_dir,
            task_id="TASK-CLOSE",
            worktree="/repo/a",
            branch="codex/answer",
            session_id="session-a",
            paths=["."],
            policy_digest=self.digest,
        )
        store.transition(
            "TASK-CLOSE", "planned", current_branch="codex/answer"
        )
        store.close("TASK-CLOSE", current_branch="codex/answer")

        lease = (
            self.state_dir
            / "codex-control-plane"
            / "leases"
            / "TASK-CLOSE.json"
        )
        self.assertFalse(lease.exists())


if __name__ == "__main__":
    unittest.main()
