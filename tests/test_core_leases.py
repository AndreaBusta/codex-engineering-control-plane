from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from control_plane.contracts import contract_digest
from control_plane.leases import LeaseStore
from tests.test_core_task_state import git, make_repo
from control_plane.task_state import CoreTaskStore


class CoreLeaseTests(unittest.TestCase):
    def test_resigned_lease_and_receipt_reject_extra_fields_and_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            state = self._state(repo, "TASK-CLOSED-LEASE", "control_plane")
            store = LeaseStore(repo)
            lease = store.acquire(
                state,
                session_id="SESSION-CLOSED-LEASE",
                policy_digest=contract_digest({"policy": "closed-lease"}),
            )
            for mutation in ({"unexpected_field": "resigned"}, {"authorizes": True}):
                tampered = {**lease, **mutation}
                tampered.pop("lease_digest", None)
                tampered["lease_digest"] = contract_digest(tampered)
                with self.assertRaisesRegex(ValueError, "E_CORE_LEASE_INVALID"):
                    LeaseStore._validate_lease(tampered)

            receipt = store.release(
                task_id=str(lease["task_id"]),
                revision_id=str(lease["revision_id"]),
                lease_generation=int(lease["lease_generation"]),
                worktree=str(lease["worktree"]),
                branch=str(lease["branch"]),
                session_id=str(lease["session_id"]),
                policy_digest=str(lease["policy_digest"]),
                lease_digest=str(lease["lease_digest"]),
            )
            for mutation in ({"unexpected_field": "resigned"}, {"authorizes": True}):
                tampered = {**receipt, **mutation}
                tampered.pop("receipt_digest", None)
                tampered["receipt_digest"] = contract_digest(tampered)
                with self.assertRaisesRegex(ValueError, "E_CORE_LEASE_INVALID"):
                    LeaseStore._validate_receipt(tampered)

    def _state(self, repo: Path, task_id: str, scope: str) -> dict:
        return CoreTaskStore(repo).start(
            task_id,
            outcome="local_change",
            branch="codex/core-test",
            head=git(repo, "rev-parse", "HEAD"),
            task_digest=contract_digest({"task": task_id}),
            decision_digest=contract_digest({"decision": task_id}),
            scope_paths=[scope],
        )

    def test_overlap_conflicts_and_release_receipt_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            leases = LeaseStore(repo)
            first = self._state(repo, "TASK-ONE", "control_plane")
            second = self._state(repo, "TASK-TWO", "control_plane/cli.py")
            policy_digest = contract_digest({"policy": 1})
            lease = leases.acquire(
                first, session_id="SESSION-ONE", policy_digest=policy_digest
            )
            with self.assertRaisesRegex(ValueError, "E_CORE_LEASE_CONFLICT"):
                leases.acquire(
                    second, session_id="SESSION-TWO", policy_digest=policy_digest
                )
            receipt = leases.release(
                task_id=lease["task_id"],
                revision_id=lease["revision_id"],
                lease_generation=lease["lease_generation"],
                worktree=lease["worktree"],
                branch=lease["branch"],
                session_id=lease["session_id"],
                policy_digest=policy_digest,
                lease_digest=lease["lease_digest"],
            )
            path = leases.receipts / f'{lease["lease_id"]}.json'
            before = path.read_bytes()
            replay = leases.release(
                task_id=lease["task_id"],
                revision_id=lease["revision_id"],
                lease_generation=lease["lease_generation"],
                worktree=lease["worktree"],
                branch=lease["branch"],
                session_id=lease["session_id"],
                policy_digest=policy_digest,
                lease_digest=lease["lease_digest"],
            )
            self.assertEqual(replay, receipt)
            self.assertEqual(path.read_bytes(), before)
            next_lease = leases.acquire(
                second, session_id="SESSION-TWO", policy_digest=policy_digest
            )
            self.assertEqual(next_lease["lease_generation"], 1)

    def test_same_task_next_revision_gets_next_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            leases = LeaseStore(repo)
            policy_digest = contract_digest({"policy": 1})
            first = self._state(repo, "TASK-REV", "control_plane")
            lease1 = leases.acquire(
                first, session_id="SESSION-ONE", policy_digest=policy_digest
            )
            leases.release(
                task_id=lease1["task_id"],
                revision_id=lease1["revision_id"],
                lease_generation=lease1["lease_generation"],
                worktree=lease1["worktree"],
                branch=lease1["branch"],
                session_id=lease1["session_id"],
                policy_digest=policy_digest,
                lease_digest=lease1["lease_digest"],
            )
            second = dict(first)
            second["task_digest"] = contract_digest({"task": "TASK-REV-SECOND"})
            second["revision_id"] = "rev-" + sha256(
                f'{second["task_digest"]}\0{second["head"]}'.encode("utf-8")
            ).hexdigest()[:16]
            second.pop("state_digest")
            second["state_digest"] = contract_digest(second)
            task_path = CoreTaskStore(repo).tasks / "TASK-REV.json"
            task_path.write_text(
                json.dumps(second, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            task_path.chmod(0o600)
            lease2 = leases.acquire(
                second, session_id="SESSION-TWO", policy_digest=policy_digest
            )
            self.assertEqual(lease2["lease_generation"], 2)
            self.assertNotEqual(lease2["lease_id"], lease1["lease_id"])

    def test_malformed_historical_receipt_blocks_generation_without_new_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            leases = LeaseStore(repo)
            policy_digest = contract_digest({"policy": "history"})
            first = self._state(repo, "TASK-RECEIPT-HISTORY", "control_plane")
            lease = leases.acquire(
                first,
                session_id="SESSION-RECEIPT-HISTORY-ONE",
                policy_digest=policy_digest,
            )
            receipt = leases.release(
                task_id=lease["task_id"],
                revision_id=lease["revision_id"],
                lease_generation=lease["lease_generation"],
                worktree=lease["worktree"],
                branch=lease["branch"],
                session_id=lease["session_id"],
                policy_digest=policy_digest,
                lease_digest=lease["lease_digest"],
            )
            malformed = {**receipt, "unexpected_field": "resigned"}
            malformed.pop("receipt_digest")
            malformed["receipt_digest"] = contract_digest(malformed)
            receipt_path = leases.receipts / f'{lease["lease_id"]}.json'
            receipt_path.write_text(
                json.dumps(malformed, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            second = dict(first)
            second["task_digest"] = contract_digest({"task": "receipt-history-two"})
            second["revision_id"] = "rev-" + sha256(
                f'{second["task_digest"]}\0{second["head"]}'.encode("utf-8")
            ).hexdigest()[:16]
            second.pop("state_digest")
            second["state_digest"] = contract_digest(second)
            task_path = CoreTaskStore(repo).tasks / "TASK-RECEIPT-HISTORY.json"
            task_path.write_text(
                json.dumps(second, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            task_path.chmod(0o600)

            with self.assertRaisesRegex(ValueError, "E_CORE_LEASE_INVALID"):
                leases.acquire(
                    second,
                    session_id="SESSION-RECEIPT-HISTORY-TWO",
                    policy_digest=policy_digest,
                )

            self.assertEqual(leases.active(), [])

    def test_acquire_rejects_stale_and_inactive_persisted_state_without_lease(self) -> None:
        for scenario in ("stale", "inactive"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                store = CoreTaskStore(repo)
                leases = LeaseStore(repo)
                original = self._state(repo, f"TASK-ACQUIRE-{scenario.upper()}", "control_plane")
                current = store.transition(
                    original["task_id"],
                    "planned" if scenario == "stale" else "blocked",
                    reason=None if scenario == "stale" else "fixture",
                    current_branch="codex/core-test",
                )
                supplied = original if scenario == "stale" else current
                before = (store.tasks / f'{original["task_id"]}.json').read_bytes()

                with self.assertRaisesRegex(ValueError, "E_CORE_LEASE_BINDING"):
                    leases.acquire(
                        supplied,
                        session_id=f"SESSION-ACQUIRE-{scenario.upper()}",
                        policy_digest=contract_digest({"policy": scenario}),
                    )

                self.assertEqual(leases.active(), [])
                self.assertEqual(
                    (store.tasks / f'{original["task_id"]}.json').read_bytes(), before
                )

    def test_exact_acquire_replay_rejects_immutable_binding_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            leases = LeaseStore(repo)
            state = self._state(repo, "TASK-REPLAY-BINDING", "control_plane")
            policy_digest = contract_digest({"policy": "exact"})
            lease = leases.acquire(
                state,
                session_id="SESSION-REPLAY-BINDING",
                policy_digest=policy_digest,
            )
            path = leases.leases / f'{lease["lease_id"]}.json'
            before = path.read_bytes()
            cases = (
                (
                    "policy_digest",
                    state,
                    contract_digest({"policy": "drifted"}),
                ),
                ("branch", {**state, "branch": "codex/drifted"}, policy_digest),
                (
                    "owner_runtime_digest",
                    {**state, "owner_runtime_digest": "sha256:" + "2" * 64},
                    policy_digest,
                ),
            )

            for field, replay_state, replay_policy_digest in cases:
                with self.subTest(field=field):
                    expected = (
                        "E_CORE_LEASE_REPLAY"
                        if field == "policy_digest"
                        else "E_CORE_LEASE_BINDING"
                    )
                    with self.assertRaisesRegex(ValueError, expected):
                        leases.acquire_with_origin(
                            replay_state,
                            session_id="SESSION-REPLAY-BINDING",
                            policy_digest=replay_policy_digest,
                        )
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(leases.active(), [lease])

    def test_release_requires_exact_policy_digest_and_preserves_wrong_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            leases = LeaseStore(repo)
            policy_digest = contract_digest({"policy": "exact"})
            state = self._state(repo, "TASK-POLICY-RELEASE", "control_plane")
            lease = leases.acquire(
                state,
                session_id="SESSION-POLICY-RELEASE",
                policy_digest=policy_digest,
            )
            path = leases.leases / f'{lease["lease_id"]}.json'
            before = path.read_bytes()

            with self.assertRaisesRegex(ValueError, "E_CORE_LEASE_RELEASE"):
                leases.release(
                    task_id=lease["task_id"],
                    revision_id=lease["revision_id"],
                    lease_generation=lease["lease_generation"],
                    worktree=lease["worktree"],
                    branch=lease["branch"],
                    session_id=lease["session_id"],
                    policy_digest=contract_digest({"policy": "wrong"}),
                    lease_digest=lease["lease_digest"],
                )
            self.assertEqual(path.read_bytes(), before)

            receipt = leases.release(
                task_id=lease["task_id"],
                revision_id=lease["revision_id"],
                lease_generation=lease["lease_generation"],
                worktree=lease["worktree"],
                branch=lease["branch"],
                session_id=lease["session_id"],
                policy_digest=policy_digest,
                lease_digest=lease["lease_digest"],
            )
            self.assertEqual(receipt["released_lease_digest"], lease["lease_digest"])
            self.assertFalse(path.exists())

    def test_shared_continuation_rejects_prior_transition(self) -> None:
        from control_plane.task_state import validate_writer_continuation

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            leases = LeaseStore(repo)
            policy_digest = contract_digest({"policy": "continuation"})
            state = self._state(repo, "TASK-CONTINUATION", "control_plane")
            lease = leases.acquire(
                state,
                session_id="SESSION-CONTINUATION",
                policy_digest=policy_digest,
            )
            bound = store.bind_lease_generation(
                state["task_id"],
                revision_id=lease["revision_id"],
                generation=lease["lease_generation"],
                expected_state_digest=state["state_digest"],
            )
            observed = validate_writer_continuation(
                repo,
                task_id=bound["task_id"],
                worktree=str(store.repository),
                branch="codex/core-test",
                session_id="SESSION-CONTINUATION",
                policy_digest=policy_digest,
                changed_paths=("control_plane/cli.py",),
            )
            self.assertEqual(observed["task"]["state_digest"], bound["state_digest"])
            store.transition(
                bound["task_id"],
                "blocked",
                reason="test",
                current_branch="codex/core-test",
            )
            with self.assertRaisesRegex(ValueError, "E_CORE_STATE_CONTINUATION"):
                validate_writer_continuation(
                    repo,
                    task_id=bound["task_id"],
                    worktree=str(store.repository),
                    branch="codex/core-test",
                    session_id="SESSION-CONTINUATION",
                    policy_digest=policy_digest,
                    changed_paths=("control_plane/cli.py",),
                )

    def test_shared_continuation_serializes_transition_as_later_drift(self) -> None:
        from control_plane.task_state import validate_writer_continuation

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            leases = LeaseStore(repo)
            policy_digest = contract_digest({"policy": "interleaving"})
            state = self._state(repo, "TASK-INTERLEAVING", "control_plane")
            lease = leases.acquire(
                state,
                session_id="SESSION-INTERLEAVING",
                policy_digest=policy_digest,
            )
            bound = store.bind_lease_generation(
                state["task_id"],
                revision_id=lease["revision_id"],
                generation=lease["lease_generation"],
                expected_state_digest=state["state_digest"],
            )
            validation_entered = threading.Event()
            allow_validation = threading.Event()
            transition_started = threading.Event()
            transition_finished = threading.Event()
            outcomes: dict[str, object] = {}
            original = LeaseStore.validate_continuation

            def paused_validation(lease_store: LeaseStore, **keywords: object) -> dict:
                validation_entered.set()
                if not allow_validation.wait(2):
                    raise AssertionError("validation interleave was not released")
                return original(lease_store, **keywords)

            def validate() -> None:
                outcomes["validation"] = validate_writer_continuation(
                    repo,
                    task_id=bound["task_id"],
                    worktree=str(store.repository),
                    branch="codex/core-test",
                    session_id="SESSION-INTERLEAVING",
                    policy_digest=policy_digest,
                    changed_paths=("control_plane/cli.py",),
                )

            def transition() -> None:
                transition_started.set()
                outcomes["transition"] = store.transition(
                    bound["task_id"],
                    "blocked",
                    reason="later drift",
                    current_branch="codex/core-test",
                )
                transition_finished.set()

            with patch.object(LeaseStore, "validate_continuation", paused_validation):
                validation_thread = threading.Thread(target=validate)
                validation_thread.start()
                self.assertTrue(validation_entered.wait(2))
                transition_thread = threading.Thread(target=transition)
                transition_thread.start()
                self.assertTrue(transition_started.wait(2))
                self.assertFalse(transition_finished.wait(0.1))
                allow_validation.set()
                validation_thread.join(2)
                transition_thread.join(2)

            self.assertFalse(validation_thread.is_alive())
            self.assertFalse(transition_thread.is_alive())
            self.assertEqual(outcomes["validation"]["task"]["state"], "framed")
            self.assertEqual(outcomes["transition"]["state"], "blocked")

    def test_bind_rejects_intervening_transition_and_inactive_task(self) -> None:
        for target in ("planned", "blocked"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                store = CoreTaskStore(repo)
                state = self._state(repo, f"TASK-BIND-{target.upper()}", "control_plane")
                lease = LeaseStore(repo).acquire(
                    state,
                    session_id=f"SESSION-BIND-{target.upper()}",
                    policy_digest=contract_digest({"policy": target}),
                )
                transitioned = store.transition(
                    state["task_id"],
                    target,
                    reason="fixture" if target == "blocked" else None,
                    current_branch="codex/core-test",
                )

                with self.assertRaisesRegex(ValueError, "E_CORE_LEASE_BINDING"):
                    store.bind_lease_generation(
                        state["task_id"],
                        revision_id=lease["revision_id"],
                        generation=lease["lease_generation"],
                        expected_state_digest=state["state_digest"],
                    )

                self.assertEqual(store.status(state["task_id"]), transitioned)

    def test_continuation_rejects_owner_runtime_drift_from_current_lock(self) -> None:
        from control_plane.task_state import validate_writer_continuation

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            state = self._state(repo, "TASK-RUNTIME-DRIFT", "control_plane")
            policy_digest = contract_digest({"policy": "runtime-drift"})
            lease = LeaseStore(repo).acquire(
                state,
                session_id="SESSION-RUNTIME-DRIFT",
                policy_digest=policy_digest,
            )
            store.bind_lease_generation(
                state["task_id"],
                revision_id=lease["revision_id"],
                generation=lease["lease_generation"],
                expected_state_digest=state["state_digest"],
            )
            (repo / ".codex" / "control-plane.lock").write_text(
                'schema_version = 2\n[digests]\nruntime = "sha256:' + "2" * 64 + '"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "E_CORE_RUNTIME"):
                validate_writer_continuation(
                    repo,
                    task_id=state["task_id"],
                    worktree=str(store.repository),
                    branch="codex/core-test",
                    session_id="SESSION-RUNTIME-DRIFT",
                    policy_digest=policy_digest,
                    changed_paths=("control_plane/cli.py",),
                )


if __name__ == "__main__":
    unittest.main()
