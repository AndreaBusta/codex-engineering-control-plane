from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
from tests.host_adapter_test_support import (
    independent_review_receipt,
    lifecycle_observation,
)
from tests.git_test_support import install_external_diff_driver


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.contracts import contract_digest

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_dir = Path(self.temp.name)
        self.digest = contract_digest({"test": "lifecycle"})

    def _two_worktree_repository(
        self, suffix: str = ""
    ) -> tuple[Path, Path, Path, Path]:
        normalized_suffix = f"-{suffix}" if suffix else ""
        repository = self.state_dir / f"repository{normalized_suffix}"
        other = self.state_dir / f"other-worktree{normalized_suffix}"
        repository.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(repository)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.name", "Control Plane Tests"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "config", "user.email", "tests@example.invalid"],
            check=True,
        )
        (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "baseline"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "worktree",
                "add",
                "-b",
                "codex/other",
                str(other),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        common_dir = repository / ".git"
        other_git_dir = common_dir / "worktrees" / other.name
        return repository, other, common_dir, other_git_dir

    def test_verification_git_never_executes_external_diff_driver(self) -> None:
        from control_plane.lifecycle import _verification_git

        repository, _, _, _ = self._two_worktree_repository("diff-driver")
        marker = install_external_diff_driver(
            repository,
            self.state_dir,
            tracked_path="tracked.txt",
            driver_name="verification-subject-driver",
        )

        completed = _verification_git(
            repository, ("diff", "HEAD", "--", "tracked.txt")
        )

        self.assertEqual(completed.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertIn(b"changed through builtin diff", completed.stdout)


class HostBridgeLifecycleCompositionTests(LifecycleTests):
    """Exercise the local delivery handoff without a remote mutation."""

    def setUp(self) -> None:
        from control_plane.contracts import contract_digest

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_dir = Path(self.temp.name)
        self.digest = contract_digest({"test": "delivery-composition"})

    def _repository(self) -> tuple[Path, Path]:
        repository = self.state_dir / "delivery-repository"
        repository.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main", str(repository)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for key, value in (
            ("user.name", "Control Plane Tests"),
            ("user.email", "tests@example.invalid"),
        ):
            subprocess.run(
                ["git", "-C", str(repository), "config", key, value],
                check=True,
            )
        (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "tracked.txt"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "baseline"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return repository, repository / ".git"

    def _expected_index_tree(self, repository: Path, paths: tuple[str, ...] = ("tracked.txt",)) -> str:
        temporary_index = self.state_dir / "expected.index"
        environment = dict(__import__("os").environ)
        environment["GIT_INDEX_FILE"] = str(temporary_index)
        for arguments in (
            ("read-tree", "HEAD"),
            ("add", "--", *paths),
        ):
            subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return subprocess.run(
            ["git", "-C", str(repository), "write-tree"],
            check=True,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

    def _delivery_policy_digest(self, repository: Path) -> str:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy

        return contract_digest(load_policy(repository / ".codex" / "project-policy.toml"))

    def _published_delivery_review(self, task_id: str, *, outcome: str = "commit", include_untracked: bool = False):
        """Create delivery proof through the public run/review/promotion path."""

        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore, build_independent_review_receipt, prepare_review_packet,
            prepare_run, publish_review_ready, verify_run,
        )
        from tests.git_test_support import FIXTURE_POLICY, GitScenario, git
        from tests.router_test_support import task_envelope

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature(f"codex/{task_id.lower()}")
        (scenario.repo / ".codex").mkdir()
        shutil.copy2(FIXTURE_POLICY, scenario.repo / ".codex" / "project-policy.toml")
        (scenario.repo / "scripts").mkdir()
        launcher = scenario.repo / "scripts" / "control-plane"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)
        (scenario.repo / "tests").mkdir()
        (scenario.repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (scenario.repo / "tests" / "test_fixture.py").write_text(
            "import unittest\n\nclass FixtureTests(unittest.TestCase):\n    def test_fixture(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (scenario.repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
        git(scenario.repo, "add", ".codex", "scripts", "tests", "tracked.txt")
        git(scenario.repo, "commit", "-m", "test: delivery review fixture")
        scope_paths = ["tracked.txt", "untracked.txt"] if include_untracked else ["tracked.txt"]
        task = task_envelope(
            task_id=task_id, requested_outcome=outcome, scope_paths=scope_paths,
            effects=[{"name": "local_write", "source": "user_explicit"},
                     {"name": "commit", "source": "user_explicit"}],
        )
        boundaries = [] if outcome == "commit" else ["commit", "remote_write", "pull_request"]
        effects = task["effects"]
        if outcome != "commit":
            effects.extend([
                {"name": "remote_write", "source": "user_explicit"},
                {"name": "pull_request", "source": "user_explicit"},
            ])
        decision_core = {
            "schema_version": 1, "task_id": task_id, "mode": "audit", "ok": True,
            "decision_ready": False,
            "summary": {"tier": "T2", "project_profile": {"profiles": ["generic"]}},
            "documentation": {},
            "interaction": {"clarification_gate": {"level": "low", "status": "autonomous", "decision_ready": True}},
            "approval_boundaries": boundaries,
            "authorization": {"local_write": True},
            "required_gates": ["gate.relevant-tests", "gate.independent-review"],
            "selected_resource_digests": {}, "matched_routes": ["quality-profile-generic"],
            "facts": {"task_digest": contract_digest(task)}, "errors": [],
        }
        decision = {**decision_core, "decision_digest": contract_digest(decision_core)}
        prepare_run(task=task, decision=decision, repository=scenario.repo,
                    policy=load_policy(FIXTURE_POLICY), session_id=f"session-{task_id.lower()}",
                    prepared_at="2026-08-09T10:00:00Z")
        parent = git(scenario.repo, "rev-parse", "HEAD")
        (scenario.repo / "tracked.txt").write_text("delivery reviewed\n", encoding="utf-8")
        if include_untracked:
            (scenario.repo / "untracked.txt").write_text("delivery untracked\n", encoding="utf-8")
        verified = verify_run(repository=scenario.repo, task_id=task_id,
                              observed_at="2026-08-09T10:01:00Z")
        state_dir = worktree_git_dir(scenario.repo)
        runs = RunStore(state_dir)
        packet = prepare_review_packet(scenario.repo, task_id, 1, "independent", "sha256:" + "4" * 64)
        receipt, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet,
            findings_digest="sha256:" + "5" * 64,
            critical=0, important=0, status="PASS", observed_at="2026-08-09T10:02:00Z",
        )
        runs.persist_review_receipt(
            task_id, packet["packet_digest"], receipt,
            observation=review_observation,
        )
        state = TaskStore(state_dir).status(task_id)
        ready = publish_review_ready(repository=scenario.repo, task_id=task_id,
                                     expected_generation=state["generation"],
                                     receipt_digests=(receipt["receipt_digest"],))
        self.assertEqual(verified["attempt"]["status"], "PASS")
        self.assertEqual(ready["state"], "review_ready")
        return scenario.repo, state_dir, TaskStore(state_dir), parent

    def _delivery_fixture(self, task_id: str):
        repository, state_dir, store, parent = self._published_delivery_review(task_id)
        expected_tree = self._expected_index_tree(repository)
        lease = store.acquire_delivery_lease(
            task_id, worktree=str(repository), branch="codex/" + task_id.lower(), session_id=f"session-{task_id.lower()}",
            paths=["tracked.txt"], policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
            diff_digest=store.status(task_id)["delivery_review_binding"]["diff_digest"], expected_generation=store.status(task_id)["generation"],
        )
        store.prepare_delivery_commit(
            task_id, lease=lease, snapshot_digest=store.status(task_id)["delivery_review_binding"]["binding_digest"], allowlist=("tracked.txt",),
            expected_index_tree=expected_tree, parent_head=parent, expected_tree=expected_tree,
            message="test: delivery fault",
        )
        return repository, state_dir, store, parent, expected_tree, lease

    def _commit_delivery(self, repository, store, task_id, parent, lease):
        from control_plane.host_bridge import observe_local_git_state, validate_local_git_observation

        subprocess.run(["git", "-C", str(repository), "add", "--", "tracked.txt"], check=True)
        store.observe_delivery_index(task_id, lease=lease, expected_index_tree=self._expected_index_tree(repository))
        subprocess.run(["git", "-C", str(repository), "commit", "-m", "test: delivery fault"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raw = observe_local_git_state(
            task_state=store.status(task_id), expected_repo=repository, expected_worktree=repository,
            expected_branch=store.status(task_id)["branch"], expected_prior_head=parent, target_state="committed",
            session_id=lease["session_id"], invocation_id=f"commit-{task_id}", clock=lambda: 100.0, ttl_seconds=30,
        )
        return validate_local_git_observation(
            raw, expected_task_digest=store.status(task_id)["task_digest"], expected_repo=repository, expected_worktree=repository,
            expected_branch=store.status(task_id)["branch"], expected_prior_head=parent, expected_target_state="committed",
            expected_session_id=lease["session_id"], expected_invocation_id=f"commit-{task_id}", clock=lambda: 100.0,
        )

    def test_delivery_staged_diff_matches_real_review_artifact_for_tracked_and_untracked(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import _delivery_review_diff, _delivery_review_diff_matches

        task_id = "TASK-DELIVERY-DIFF-PARITY"
        repository, _state_dir, store, parent = self._published_delivery_review(
            task_id, include_untracked=True,
        )
        binding = store.status(task_id)["delivery_review_binding"]
        paths = ("tracked.txt", "untracked.txt")
        lease = store.acquire_delivery_lease(
            task_id, worktree=str(repository), branch=store.status(task_id)["branch"],
            session_id=f"session-{task_id.lower()}", paths=list(paths),
            policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
            diff_digest=binding["diff_digest"],
            expected_generation=store.status(task_id)["generation"],
        )
        expected_tree = self._expected_index_tree(repository, paths)
        store.prepare_delivery_commit(
            task_id, lease=lease, snapshot_digest=store.status(task_id)["delivery_review_binding"]["binding_digest"], allowlist=paths,
            expected_index_tree=expected_tree, parent_head=parent,
            expected_tree=expected_tree, message="test: diff parity",
        )
        subprocess.run(["git", "-C", str(repository), "add", "--", *paths], check=True)
        rendered = _delivery_review_diff(repository, parent, paths)
        self.assertEqual(contract_digest({"diff": rendered.hex()}), binding["diff_digest"])
        self.assertTrue(_delivery_review_diff_matches(repository, binding, paths))
        store.observe_delivery_index(task_id, lease=lease, expected_index_tree=expected_tree)

    def test_delivery_review_revalidation_ignores_replace_ref_hiding_staged_diff(self) -> None:
        from control_plane.lifecycle import _delivery_review_diff_matches

        task_id = "TASK-DELIVERY-REPLACE-REF"
        repository, _state_dir, store, parent = self._published_delivery_review(
            task_id
        )
        binding = store.status(task_id)["delivery_review_binding"]
        subprocess.run(
            ["git", "-C", str(repository), "add", "--", "tracked.txt"],
            check=True,
        )
        tree = subprocess.run(
            ["git", "-C", str(repository), "write-tree"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        replacement = subprocess.run(
            ["git", "-C", str(repository), "commit-tree", tree],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(repository), "replace", parent, replacement],
            check=True,
        )

        self.assertTrue(
            _delivery_review_diff_matches(
                repository, binding, ("tracked.txt",)
            )
        )

    def test_delivery_index_uses_only_an_explicit_index_parameter(self) -> None:
        from control_plane.lifecycle import _delivery_index_tree

        repository, _git_dir = self._repository()
        tracked = repository / "tracked.txt"
        tracked.write_text("actual index\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "tracked.txt"],
            check=True,
        )
        actual_tree = _delivery_index_tree(repository)

        explicit_index = self.state_dir / "explicit-delivery.index"
        environment = dict(__import__("os").environ)
        environment["GIT_INDEX_FILE"] = str(explicit_index)
        tracked.write_text("explicit index\n", encoding="utf-8")
        for arguments in (("read-tree", "HEAD"), ("add", "tracked.txt")):
            subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        explicit_tree = subprocess.run(
            ["git", "-C", str(repository), "write-tree"],
            check=True,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertNotEqual(actual_tree, explicit_tree)

        with patch.dict(
            "os.environ",
            {"GIT_INDEX_FILE": str(explicit_index)},
            clear=False,
        ):
            self.assertEqual(_delivery_index_tree(repository), actual_tree)
            self.assertEqual(
                _delivery_index_tree(
                    repository, index_file=explicit_index
                ),
                explicit_tree,
            )

    def test_delivery_marker_requires_the_durable_review_binding_digest(self) -> None:
        task_id = "TASK-DELIVERY-SNAPSHOT-BINDING"
        repository, _state_dir, store, parent = self._published_delivery_review(task_id)
        binding = store.status(task_id)["delivery_review_binding"]
        expected_tree = self._expected_index_tree(repository)
        lease = store.acquire_delivery_lease(
            task_id, worktree=str(repository), branch=store.status(task_id)["branch"],
            session_id=f"session-{task_id.lower()}", paths=["tracked.txt"],
            policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
            diff_digest=binding["diff_digest"], expected_generation=store.status(task_id)["generation"],
        )
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_MARKER"):
            store.prepare_delivery_commit(
                task_id, lease=lease, snapshot_digest=self.digest,
                allowlist=("tracked.txt",), expected_index_tree=expected_tree,
                parent_head=parent, expected_tree=expected_tree, message="test: forged snapshot",
            )

    def test_delivery_review_diff_caps_a_large_staged_untracked_blob(self) -> None:
        from control_plane.lifecycle import _delivery_review_diff

        repository, _state_dir, store, parent = self._published_delivery_review(
            "TASK-DELIVERY-DIFF-CAP", include_untracked=True,
        )
        (repository / "untracked.txt").write_bytes(b"x" * (1_048_577))
        subprocess.run(["git", "-C", str(repository), "add", "--", "tracked.txt", "untracked.txt"], check=True)
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_INDEX"):
            _delivery_review_diff(repository, parent, ("tracked.txt", "untracked.txt"))

    def test_delivery_acquire_rejects_untracked_mode_drift_after_review(self) -> None:
        task_id = "TASK-DELIVERY-UNTRACKED-MODE-DRIFT"
        repository, _state_dir, store, parent = self._published_delivery_review(task_id, include_untracked=True)
        (repository / "untracked.txt").chmod(0o755)
        binding = store.status(task_id)["delivery_review_binding"]
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_REVIEW"):
            store.acquire_delivery_lease(
                task_id, worktree=str(repository), branch=store.status(task_id)["branch"],
                session_id=f"session-{task_id.lower()}", paths=["tracked.txt", "untracked.txt"],
                policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
                diff_digest=binding["diff_digest"], expected_generation=store.status(task_id)["generation"],
            )

    def test_delivery_acquire_rejects_review_drift_unrelated_untracked_and_pre_staged_index(self) -> None:
        def acquire_after(task_id: str, mutate) -> None:
            repository, _state_dir, store, parent = self._published_delivery_review(task_id)
            mutate(repository)
            binding = store.status(task_id)["delivery_review_binding"]
            with self.assertRaisesRegex(ValueError, "E_DELIVERY_REVIEW"):
                store.acquire_delivery_lease(
                    task_id, worktree=str(repository), branch=store.status(task_id)["branch"],
                    session_id=f"session-{task_id.lower()}", paths=["tracked.txt"],
                    policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
                    diff_digest=binding["diff_digest"], expected_generation=store.status(task_id)["generation"],
                )

        acquire_after("TASK-DELIVERY-WORKING-DRIFT", lambda repo: (repo / "tracked.txt").write_text("changed after review\n", encoding="utf-8"))
        acquire_after("TASK-DELIVERY-UNTRACKED-OUTSIDE", lambda repo: (repo / "outside.txt").write_text("outside\n", encoding="utf-8"))
        acquire_after("TASK-DELIVERY-PRE-STAGED", lambda repo: subprocess.run(["git", "-C", str(repo), "add", "--", "tracked.txt"], check=True))

    def test_delivery_acquire_rejects_review_ready_without_durable_proof(self) -> None:
        from control_plane.lifecycle import TaskStore

        repository, state_dir = self._repository()
        task_id = "TASK-DELIVERY-MISSING-PROOF"
        store = TaskStore(state_dir)
        store.start(task_id, outcome="commit", branch="main", task_digest=self.digest, decision_digest=self.digest)
        for target, evidence in (("planned", None), ("ready", {"preflight_ok": True}), ("implementing", None), ("verifying", {"implementation_complete": True}), ("review_ready", {"gates_ok": True, "documentation_decision": self.digest})):
            store.transition(task_id, target, evidence=evidence, current_branch="main")
        head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_REVIEW"):
            store.acquire_delivery_lease(task_id, worktree=str(repository), branch="main", session_id="session-missing-proof", paths=["tracked.txt"], policy_digest=self.digest, expected_head=head, diff_digest=self.digest, expected_generation=store.status(task_id)["generation"])

    def test_delivery_acquire_rejects_missing_required_gate_receipt(self) -> None:
        from control_plane.run_workflow import RunStore

        task_id = "TASK-DELIVERY-MISSING-GATE"
        repository, state_dir, store, parent = self._published_delivery_review(task_id)
        runs = RunStore(state_dir)
        latest = runs.attempts(task_id)[-1]
        gate_digest = latest["gate_receipt_digests"][0].removeprefix("sha256:")
        (runs._directory(task_id) / "receipts" / f"{gate_digest}.json").unlink()
        binding = store.status(task_id)["delivery_review_binding"]
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_REVIEW"):
            store.acquire_delivery_lease(
                task_id, worktree=str(repository), branch=store.status(task_id)["branch"],
                session_id="session-missing-gate", paths=["tracked.txt"],
                policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
                diff_digest=binding["diff_digest"], expected_generation=store.status(task_id)["generation"],
            )

    def test_delivery_does_not_promote_remote_state_without_a_remote_observation(self) -> None:
        task_id = "TASK-DELIVERY-REMOTE-UNKNOWN"
        _repository, _state_dir, store, _parent = self._published_delivery_review(task_id, outcome="pull_request")
        self.assertFalse(hasattr(store, "publish_delivery_push"))
        with self.assertRaisesRegex(ValueError, "E_TRANSITION"):
            store.transition(
                task_id, "pushed", current_branch=store.status(task_id)["branch"],
            )

    def test_delivery_acquire_rejects_live_branch_and_cached_base_drift(self) -> None:
        def assert_rejected(task_id: str, mutate) -> None:
            repository, _state_dir, store, parent = self._published_delivery_review(task_id)
            mutate(repository, parent)
            binding = store.status(task_id)["delivery_review_binding"]
            with self.assertRaisesRegex(ValueError, "E_DELIVERY_REVIEW"):
                store.acquire_delivery_lease(
                    task_id, worktree=str(repository), branch=store.status(task_id)["branch"],
                    session_id=f"session-{task_id.lower()}", paths=["tracked.txt"],
                    policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
                    diff_digest=binding["diff_digest"], expected_generation=store.status(task_id)["generation"],
                )

        assert_rejected("TASK-DELIVERY-BRANCH-DRIFT", lambda repo, _head: subprocess.run(["git", "-C", str(repo), "switch", "-c", "codex/delivery-drift"], check=True, stdout=subprocess.DEVNULL))
        assert_rejected("TASK-DELIVERY-BASE-DRIFT", lambda repo, head: subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", head], check=True))

    def test_delivery_prepare_rejects_cached_base_drift_after_acquire(self) -> None:
        task_id = "TASK-DELIVERY-BASE-DRIFT-AFTER-LEASE"
        repository, _state_dir, store, parent = self._published_delivery_review(task_id)
        binding = store.status(task_id)["delivery_review_binding"]
        lease = store.acquire_delivery_lease(
            task_id, worktree=str(repository), branch=store.status(task_id)["branch"],
            session_id=f"session-{task_id.lower()}", paths=["tracked.txt"],
            policy_digest=self._delivery_policy_digest(repository), expected_head=parent,
            diff_digest=binding["diff_digest"], expected_generation=store.status(task_id)["generation"],
        )
        subprocess.run(["git", "-C", str(repository), "update-ref", "refs/remotes/origin/main", parent], check=True)
        expected_tree = self._expected_index_tree(repository)
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_MARKER"):
            store.prepare_delivery_commit(
                task_id, lease=lease, snapshot_digest=binding["binding_digest"],
                allowlist=("tracked.txt",), expected_index_tree=expected_tree,
                parent_head=parent, expected_tree=expected_tree, message="test: base drift",
            )

    def test_review_ready_can_reach_remote_context_after_local_commit(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import (
            observe_local_git_state,
            validate_local_git_observation,
        )
        from control_plane.run_workflow import validate_outcome_binding

        task_id = "TASK-DELIVERY-COMPOSITION"
        session_id = "session-delivery-composition"
        repository, state_dir, store, parent = self._published_delivery_review(
            task_id, outcome="pull_request",
        )
        branch = store.status(task_id)["branch"]
        expected_tree = self._expected_index_tree(repository)
        lease = store.acquire_delivery_lease(
            task_id,
            worktree=str(repository),
            branch=branch,
            session_id=session_id,
            paths=["tracked.txt"],
            policy_digest=self._delivery_policy_digest(repository),
            expected_head=parent,
            diff_digest=store.status(task_id)["delivery_review_binding"]["diff_digest"],
            expected_generation=store.status(task_id)["generation"],
        )
        self.assertEqual(store.status(task_id)["state"], "review_ready")
        store.prepare_delivery_commit(
            task_id,
            lease=lease,
            snapshot_digest=store.status(task_id)["delivery_review_binding"]["binding_digest"],
            allowlist=("tracked.txt",),
            expected_index_tree=expected_tree,
            parent_head=parent,
            expected_tree=expected_tree,
            message="test: delivery",
        )
        subprocess.run(
            ["git", "-C", str(repository), "add", "--", "tracked.txt"],
            check=True,
        )
        store.observe_delivery_index(
            task_id, lease=lease, expected_index_tree=expected_tree
        )
        self.assertEqual(store.status(task_id)["state"], "review_ready")
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "test: delivery"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        observed = observe_local_git_state(
            task_state=store.status(task_id),
            expected_repo=repository,
            expected_worktree=repository,
            expected_branch=branch,
            expected_prior_head=parent,
            target_state="committed",
            session_id=session_id,
            invocation_id="delivery-commit-observation",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        validated = validate_local_git_observation(
            observed,
            expected_task_digest=store.status(task_id)["task_digest"],
            expected_repo=repository,
            expected_worktree=repository,
            expected_branch=branch,
            expected_prior_head=parent,
            expected_target_state="committed",
            expected_session_id=session_id,
            expected_invocation_id="delivery-commit-observation",
            clock=lambda: 100.0,
        )
        committed = store.publish_delivery_commit(
            task_id, lease=lease, observation=validated, current_branch=branch
        )
        binding = committed["outcome_binding"]

        self.assertEqual(committed["state"], "committed")
        self.assertEqual(validate_outcome_binding(binding), [])
        self.assertEqual(binding["consumed_effect_ids"], ["local_write", "commit"])
        self.assertEqual(binding["review_head"], parent)
        self.assertEqual(
            binding["reviewed_tree_digest"],
            contract_digest({"git_tree_oid": expected_tree}),
        )
        self.assertEqual(
            binding["reviewed_diff_digest"],
            committed["delivery_review_binding"]["diff_digest"],
        )
        self.assertEqual(binding["committed_head"], committed["evidence"]["committed"]["commit"])
        self.assertFalse((state_dir / "codex-control-plane" / "delivery-leases" / f"{task_id}.json").exists())

    def test_review_ready_rejects_implementation_lease(self) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        repository, state_dir = self._repository()
        task_id = "TASK-DELIVERY-LEASE-ONLY"
        store = TaskStore(state_dir)
        store.start(
            task_id,
            outcome="commit",
            branch="main",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
            ("review_ready", {"gates_ok": True, "documentation_decision": self.digest}),
        ):
            store.transition(task_id, target, evidence=evidence, current_branch="main")

        with self.assertRaisesRegex(ValueError, "E_LEASE_DELIVERY_REQUIRED"):
            TaskLease.acquire(
                state_dir,
                task_id=task_id,
                worktree=str(repository),
                branch="main",
                session_id="session-writer-rejected",
                paths=["tracked.txt"],
                policy_digest=self.digest,
            )

    def test_delivery_recovery_after_marker_before_stage_blocks_without_write(self) -> None:
        task_id = "TASK-DELIVERY-RECOVERY-PREPARED"
        repository, state_dir, store, _parent, _expected_tree, _lease = self._delivery_fixture(task_id)

        recovered = store.recover_writer_finalization(task_id)

        self.assertEqual(recovered["state"], "blocked")
        self.assertTrue((state_dir / "codex-control-plane" / "delivery-leases" / f"{task_id}.json").exists())
        cached = subprocess.run(
            ["git", "-C", str(repository), "diff", "--cached", "--name-only"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertEqual(cached, "")

    def test_delivery_recovery_after_stage_blocks_without_repeating_stage(self) -> None:
        repository, state_dir, store, _parent, expected_tree, lease = self._delivery_fixture("TASK-FAULT-POST-STAGE")
        subprocess.run(["git", "-C", str(repository), "add", "--", "tracked.txt"], check=True)
        store.observe_delivery_index("TASK-FAULT-POST-STAGE", lease=lease, expected_index_tree=expected_tree)

        recovered = store.recover_writer_finalization("TASK-FAULT-POST-STAGE")

        self.assertEqual(recovered["state"], "blocked")
        self.assertEqual(recovered["finalizing_delivery_commit"]["phase"], "index_observed")
        self.assertEqual(
            subprocess.run(["git", "-C", str(repository), "diff", "--cached", "--name-only"], check=True, text=True, stdout=subprocess.PIPE).stdout,
            "tracked.txt\n",
        )
        self.assertTrue((state_dir / "codex-control-plane" / "delivery-leases" / "TASK-FAULT-POST-STAGE.json").exists())

    def test_delivery_recovery_after_native_commit_publishes_once_and_releases(self) -> None:
        repository, state_dir, store, parent, _expected_tree, lease = self._delivery_fixture("TASK-FAULT-POST-COMMIT")
        self._commit_delivery(repository, store, "TASK-FAULT-POST-COMMIT", parent, lease)

        recovered = store.recover_writer_finalization("TASK-FAULT-POST-COMMIT")
        repeated = store.recover_writer_finalization("TASK-FAULT-POST-COMMIT")

        self.assertEqual(recovered["state"], "committed")
        self.assertEqual(repeated["state"], "committed")
        self.assertEqual(recovered["finalizing_delivery_commit"]["phase"], "lease_released")
        self.assertFalse((state_dir / "codex-control-plane" / "delivery-leases" / "TASK-FAULT-POST-COMMIT.json").exists())

    def test_delivery_recovery_blocks_unstaged_drift_after_native_commit(self) -> None:
        repository, state_dir, store, parent, _expected_tree, lease = self._delivery_fixture("TASK-FAULT-RECOVERY-UNSTAGED")
        self._commit_delivery(repository, store, "TASK-FAULT-RECOVERY-UNSTAGED", parent, lease)
        (repository / "foreign-after-commit.txt").write_text("foreign\n", encoding="utf-8")
        recovered = store.recover_writer_finalization("TASK-FAULT-RECOVERY-UNSTAGED")
        self.assertEqual(recovered["state"], "blocked")
        self.assertTrue((state_dir / "codex-control-plane" / "delivery-leases" / "TASK-FAULT-RECOVERY-UNSTAGED.json").exists())

    def test_delivery_recovery_after_state_publish_releases_idempotently(self) -> None:
        from control_plane.lifecycle import DeliveryLease

        repository, state_dir, store, parent, _expected_tree, lease = self._delivery_fixture("TASK-FAULT-POST-STATE")
        observation = self._commit_delivery(repository, store, "TASK-FAULT-POST-STATE", parent, lease)
        with patch.object(DeliveryLease, "release", side_effect=RuntimeError("fault-before-release")):
            with self.assertRaisesRegex(RuntimeError, "fault-before-release"):
                store.publish_delivery_commit("TASK-FAULT-POST-STATE", lease=lease, observation=observation, current_branch=store.status("TASK-FAULT-POST-STATE")["branch"])

        marker = store.status("TASK-FAULT-POST-STATE")
        self.assertEqual(marker["state"], "committed")
        self.assertEqual(marker["finalizing_delivery_commit"]["phase"], "state_committed")
        recovered = store.recover_writer_finalization("TASK-FAULT-POST-STATE")

        self.assertEqual(recovered["finalizing_delivery_commit"]["phase"], "lease_released")
        self.assertFalse((state_dir / "codex-control-plane" / "delivery-leases" / "TASK-FAULT-POST-STATE.json").exists())

    def test_delivery_recovery_after_release_tombstone_is_idempotent(self) -> None:
        import control_plane.lifecycle as lifecycle

        repository, _state_dir, store, parent, _expected_tree, lease = self._delivery_fixture("TASK-FAULT-DURING-RELEASE")
        observation = self._commit_delivery(repository, store, "TASK-FAULT-DURING-RELEASE", parent, lease)
        with patch.object(lifecycle, "_fsync_directory", side_effect=RuntimeError("fault-during-release")):
            with self.assertRaisesRegex(RuntimeError, "fault-during-release"):
                store.publish_delivery_commit("TASK-FAULT-DURING-RELEASE", lease=lease, observation=observation, current_branch=store.status("TASK-FAULT-DURING-RELEASE")["branch"])

        recovered = store.recover_writer_finalization("TASK-FAULT-DURING-RELEASE")

        self.assertEqual(recovered["state"], "committed")
        self.assertEqual(recovered["finalizing_delivery_commit"]["phase"], "lease_released")

    def test_delivery_lease_release_is_owner_bound_and_idempotent(self) -> None:
        from control_plane.lifecycle import DeliveryLease

        _repository, _state_dir, _store, _parent, _tree, lease = self._delivery_fixture("TASK-DELIVERY-DOUBLE-RELEASE")
        first = DeliveryLease.release(_state_dir, task_id="TASK-DELIVERY-DOUBLE-RELEASE", lease=lease)
        second = DeliveryLease.release(_state_dir, task_id="TASK-DELIVERY-DOUBLE-RELEASE", lease=lease)
        other = {**lease, "session_id": "other-owner"}

        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_LEASE_MISMATCH"):
            DeliveryLease.release(_state_dir, task_id="TASK-DELIVERY-DOUBLE-RELEASE", lease=other)

    def test_delivery_publish_reobserves_head_before_committing_state(self) -> None:
        repository, _state_dir, store, parent, _tree, lease = self._delivery_fixture("TASK-DELIVERY-PUBLISH-HEAD-DRIFT")
        observation = self._commit_delivery(repository, store, "TASK-DELIVERY-PUBLISH-HEAD-DRIFT", parent, lease)
        branch = store.status("TASK-DELIVERY-PUBLISH-HEAD-DRIFT")["branch"]
        subprocess.run(["git", "-C", str(repository), "update-ref", f"refs/heads/{branch}", parent], check=True)
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_COMMIT"):
            store.publish_delivery_commit(
                "TASK-DELIVERY-PUBLISH-HEAD-DRIFT", lease=lease,
                observation=observation, current_branch=branch,
            )
        self.assertEqual(store.status("TASK-DELIVERY-PUBLISH-HEAD-DRIFT")["state"], "review_ready")

    def test_active_run_revision_binding_is_closed_and_compare_and_swap(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir, runtime_digest=self.digest)
        task_id = "TASK-RUN-REVISION-BIND"
        branch = "codex/revision-bind"
        store.start(
            task_id, outcome="local_change", branch=branch,
            task_digest=self.digest, decision_digest=self.digest,
        )
        revision = "sha256:" + "a" * 64
        bound = store.bind_active_run_revision(
            task_id, run_plan_digest=self.digest,
            revision_digest=revision, current_branch=branch,
        )
        self.assertEqual(bound["active_run_revision_digest"], revision)
        self.assertEqual(
            store.bind_active_run_revision(
                task_id, run_plan_digest=self.digest,
                revision_digest=revision, current_branch=branch,
            )["active_run_revision_digest"],
            revision,
        )
        with self.assertRaisesRegex(ValueError, "E_STATE_CAS"):
            store.bind_active_run_revision(
                task_id, run_plan_digest=self.digest,
                revision_digest="sha256:" + "b" * 64,
                current_branch=branch,
            )

    def test_review_handoff_recovery_rejects_a_marker_without_live_subject_proof(self) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskLease, TaskStore

        task_id, branch, session = "TASK-REVIEW-HANDOFF", "codex/review-handoff", "session-review-handoff"
        store = TaskStore(self.state_dir, runtime_digest=self.digest)
        store.start(task_id, outcome="local_change", branch=branch, task_digest=self.digest, decision_digest=self.digest)
        for target, evidence in (("planned", None), ("ready", {"preflight_ok": True}), ("implementing", None), ("verifying", {"implementation_complete": True})):
            state = store.transition(task_id, target, evidence=evidence, current_branch=branch)
        lease = TaskLease.acquire(self.state_dir, task_id=task_id, worktree="/repo/review-handoff", branch=branch, session_id=session, paths=["."], policy_digest=self.digest)
        state.update({"state": "finalizing_review_handoff", "resume_forbidden": True,
            "review_handoff_finalization": {"prior_generation": state["generation"], "evidence": {"revision_digest": "sha256:" + "a" * 64, "attempt_digest": "sha256:" + "b" * 64, "artifact_digest": "sha256:" + "c" * 64}, "task_id": task_id, "worktree": "/repo/review-handoff", "branch": branch, "session_id": session, "policy_digest": self.digest, "lease_digest": lease["lease_digest"]}})
        lifecycle._atomic_json(store._path(task_id), state)
        with self.assertRaisesRegex(ValueError, "E_REVIEW_HANDOFF_RECOVERY_UNKNOWN"):
            store.recover_writer_finalization(task_id)
        self.assertTrue((self.state_dir / "codex-control-plane" / "leases" / f"{task_id}.json").exists())
        self.assertEqual(store.status(task_id)["state"], "finalizing_review_handoff")

    def test_review_handoff_recovery_rejects_a_third_lease_without_mutation(self) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore

        task_id, branch, session = "TASK-REVIEW-HANDOFF-THIRD", "codex/review-handoff-third", "session-review-handoff"
        store = TaskStore(self.state_dir, runtime_digest=self.digest)
        store.start(task_id, outcome="local_change", branch=branch, task_digest=self.digest, decision_digest=self.digest)
        for target, evidence in (("planned", None), ("ready", {"preflight_ok": True}), ("implementing", None), ("verifying", {"implementation_complete": True})):
            state = store.transition(task_id, target, evidence=evidence, current_branch=branch)
        lease = TaskLease.acquire(self.state_dir, task_id=task_id, worktree="/repo/review-handoff-third", branch=branch, session_id=session, paths=["."], policy_digest=self.digest)
        state.update({"state": "finalizing_review_handoff", "resume_forbidden": True,
            "review_handoff_finalization": {"prior_generation": state["generation"], "evidence": {"revision_digest": "sha256:" + "a" * 64, "attempt_digest": "sha256:" + "b" * 64, "artifact_digest": "sha256:" + "c" * 64}, "task_id": task_id, "worktree": "/repo/review-handoff-third", "branch": branch, "session_id": session, "policy_digest": self.digest, "lease_digest": lease["lease_digest"]}})
        lifecycle._atomic_json(store._path(task_id), state)
        third = dict(lease)
        third["session_id"] = "session-third-owner"
        third["lease_digest"] = contract_digest({key: value for key, value in third.items() if key != "lease_digest"})
        lifecycle._atomic_json(self.state_dir / "codex-control-plane" / "leases" / f"{task_id}.json", third)

        with self.assertRaisesRegex(ValueError, "E_REVIEW_HANDOFF_RECOVERY_UNKNOWN"):
            store.recover_writer_finalization(task_id)

        self.assertEqual(store.status(task_id)["state"], "finalizing_review_handoff")
        self.assertEqual(store._read_owner_lease(task_id), third)

    def _verification_context_for_repo(
        self,
        repository: Path,
        temp_root: Path,
        *,
        task_id: str = "TASK-VERIFY-RUNNER",
        task_digest: str | None = None,
        profile: str = "governing_base_verification",
        session_id: str = "session-verify-runner",
        lease_digest: str | None = None,
    ):
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import (
            VERIFICATION_COMMAND_IDS,
            VerificationExecutionContext,
        )

        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        executables = {
            "python": str(Path(sys.executable).resolve()),
            "git": str(Path(shutil.which("git") or "/usr/bin/git").resolve()),
            "control_plane": str(
                (Path(__file__).parents[1] / "scripts" / "control-plane").resolve()
            ),
        }
        context = object.__new__(VerificationExecutionContext)
        context._consumed = False
        values = {
            "task_id": task_id,
            "task_digest": task_digest or self.digest,
            "profile": profile,
            "profile_digest": contract_digest(
                {
                    "profile": profile,
                    "commands": VERIFICATION_COMMAND_IDS[profile],
                }
            ),
            "runtime_digest": self.digest,
            "target_digest": self.digest,
            "content_trust": (
                "project_owned"
                if profile == "control_plane_assurance"
                else "governing_base"
            ),
            "repository": str(repository.resolve()),
            "worktree": str(repository.resolve()),
            "expected_head": head,
            "session_id": session_id,
            "lease_digest": lease_digest or self.digest,
            "dedicated_temp_root": str(temp_root.resolve()),
            "executables": executables,
            "executables_digest": contract_digest(executables),
        }
        for name, value in values.items():
            setattr(context, name, value)
        context.context_digest = contract_digest(
            {
                name: getattr(context, name)
                for name in (
                    "task_id",
                    "task_digest",
                    "profile",
                    "profile_digest",
                    "runtime_digest",
                    "target_digest",
                    "content_trust",
                    "repository",
                    "worktree",
                    "expected_head",
                    "session_id",
                    "lease_digest",
                    "executables_digest",
                )
            }
        )
        return context

    def _active_verification_fixture(
        self, *, task_id: str, profile: str
    ):
        from control_plane.lifecycle import (
            TaskLease,
            TaskStore,
            _atomic_json,
        )

        repository, _, common_dir, _ = self._two_worktree_repository(
            task_id.lower()
        )
        session_id = f"session-{task_id.lower()}"
        store = TaskStore(common_dir, runtime_digest=self.digest)
        store.start(
            task_id,
            outcome="commit",
            branch="main",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
        ):
            state = store.transition(
                task_id,
                target,
                evidence=evidence,
                current_branch="main",
            )
        lease = TaskLease.acquire(
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            session_id=session_id,
            paths=["."],
            policy_digest=self.digest,
        )
        context = self._verification_context_for_repo(
            repository,
            self.state_dir / f"{task_id.lower()}-temp",
            task_id=task_id,
            profile=profile,
            session_id=session_id,
            lease_digest=lease["lease_digest"],
        )
        state.update(
            {
                "verification_profile": context.profile,
                "verification_profile_digest": context.profile_digest,
                "verification_runtime_digest": context.runtime_digest,
                "verification_target_digest": context.target_digest,
                "verification_content_trust": context.content_trust,
                "session_id": session_id,
            }
        )
        _atomic_json(store._path(task_id), state)
        return repository, common_dir, store, state, context, lease

    def test_every_task_state_is_owned_by_exact_runtime_digest(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore

        owner_digest = contract_digest({"runtime": "owner"})
        foreign_digest = contract_digest({"runtime": "foreign"})
        task_id = "TASK-RUNTIME-OWNED"
        owner_store = TaskStore(
            self.state_dir, runtime_digest=owner_digest
        )

        state = owner_store.start(
            task_id,
            outcome="local_change",
            branch="codex/runtime-owned",
            task_digest=self.digest,
            decision_digest=self.digest,
        )

        self.assertEqual(state["owner_runtime_digest"], owner_digest)
        with self.assertRaisesRegex(
            ValueError, "E_FOREIGN_RUNTIME_STATE"
        ):
            TaskStore(
                self.state_dir, runtime_digest=foreign_digest
            ).transition(
                task_id,
                "planned",
                current_branch="codex/runtime-owned",
            )

    def test_closed_task_identity_cannot_reacquire_writer_lease(self) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        task_id = "TASK-CLOSED-LEASE"
        branch = "codex/closed-lease"
        store = TaskStore(self.state_dir, runtime_digest=self.digest)
        store.start(
            task_id,
            outcome="answer",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        store.transition(task_id, "planned", current_branch=branch)
        store.close(task_id, current_branch=branch)

        with self.assertRaisesRegex(
            ValueError, "E_LEASE_RECOVERY_UNAUTHORIZED"
        ):
            TaskLease.acquire(
                self.state_dir,
                task_id=task_id,
                worktree="/repo/closed-lease",
                branch=branch,
                session_id="session-closed-lease",
                paths=["."],
                policy_digest=self.digest,
            )

    def test_abandoned_recovery_rejects_foreign_runtime_state(self) -> None:
        import json

        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore

        task_id = "TASK-FOREIGN-RECOVERY"
        branch = "codex/foreign-recovery"
        session_id = "session-foreign-recovery"
        store = TaskStore(self.state_dir, runtime_digest=self.digest)
        store.start(
            task_id,
            outcome="local_change",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        lease = TaskLease.acquire(
            self.state_dir,
            task_id=task_id,
            worktree="/repo/foreign-recovery",
            branch=branch,
            session_id=session_id,
            paths=["."],
            policy_digest=self.digest,
        )
        task_path = (
            self.state_dir
            / "codex-control-plane"
            / "tasks"
            / f"{task_id}.json"
        )
        state = json.loads(task_path.read_text(encoding="utf-8"))
        state["owner_runtime_digest"] = contract_digest(
            {"runtime": "foreign"}
        )
        task_path.write_text(
            json.dumps(state, sort_keys=True) + "\n", encoding="utf-8"
        )
        fake_authorization = type(
            "FakeAuthorization", (), {"authorization_id": "forged"}
        )()

        with self.assertRaisesRegex(
            ValueError, "E_FOREIGN_RUNTIME_STATE"
        ):
            TaskLease.recover_abandoned(
                self.state_dir,
                self.state_dir,
                task_id=task_id,
                worktree="/repo/foreign-recovery",
                branch=branch,
                owner_session_id=session_id,
                policy_digest=self.digest,
                lease_digest=lease["lease_digest"],
                recovery_authorization=fake_authorization,
                worktree_inventory={},
            )

    def _abandoned_recovery_fixture(self, *, task_id: str):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from tests.host_adapter_test_support import (
            native_session_event,
            native_user_interaction_event,
        )

        repository, _, common_dir, _ = self._two_worktree_repository(
            task_id.lower()
        )
        owner_session = f"session-owner-{task_id.lower()}"
        recovering_session = f"session-recovery-{task_id.lower()}"
        store = TaskStore(common_dir)
        store.start(
            task_id,
            outcome="commit",
            branch="main",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        lease = TaskLease.acquire(
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            session_id=owner_session,
            paths=["."],
            policy_digest=self.digest,
        )
        invocation_id = f"recover-{task_id.lower()}"
        raw_inventory = bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            raw_inventory,
            expected_common_git_dir=common_dir,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id=f"session-{task_id.lower()}",
                session_id=recovering_session,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=recovering_session,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization = bridge.frame_lease_recovery_authorization(
            native_confirmation_event=native_user_interaction_event(
                event_id=f"confirm-{task_id.lower()}",
                session_id=recovering_session,
                invocation_id=invocation_id,
                task_digest=self.digest,
                subject_digest=contract_digest(
                    {
                        "task_id": task_id,
                        "lease_digest": lease["lease_digest"],
                    }
                ),
                observed_at_monotonic=100.0,
            ),
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            owner_session_id=owner_session,
            recovering_session_id=recovering_session,
            policy_digest=self.digest,
            lease_digest=lease["lease_digest"],
            inventory=inventory,
            invocation_id=invocation_id,
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        return (
            repository,
            common_dir,
            store,
            lease,
            owner_session,
            inventory,
            authorization,
        )

    def _remote_effect_fixture(
        self,
        *,
        task_id: str,
        effect: str,
        outcome: str,
        expected_pr_number: int | None = None,
        expected_base_sha: str | None = None,
        expected_checks_digest: str | None = None,
        branch: str = "main",
    ):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore, _atomic_json
        from tests.host_adapter_test_support import (
            governing_policy,
            native_session_event,
        )
        from tests.router_test_support import task_envelope

        repository, _, common_dir, _ = self._two_worktree_repository(
            task_id.lower()
        )
        if branch != "main":
            subprocess.run(
                ["git", "-C", str(repository), "switch", "-c", branch],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "remote",
                "add",
                "origin",
                "https://github.com/example/control-plane.git",
            ],
            check=True,
        )
        session_id = f"session-{task_id.lower()}"
        invocation_id = f"invocation-{task_id.lower()}"
        task = task_envelope(
            task_id=task_id,
            requested_outcome=outcome,
            effects=[
                {"name": "local_read", "source": "user_explicit"},
                {"name": effect, "source": "user_explicit"},
            ],
            scope_paths=["."],
        )
        task_digest = contract_digest(task)
        store = TaskStore(common_dir)
        state = store.start(
            task_id,
            outcome=outcome,
            branch=branch,
            task_digest=task_digest,
            decision_digest=self.digest,
        )
        state["state"] = "closed"
        _atomic_json(store._path(task_id), state)
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        local = lifecycle_observation(
            bridge.LocalGitObservation,
            observation_id=f"local-{task_id.lower()}",
            invocation_id=invocation_id,
            task_digest=task_digest,
            repository_identity=str(repository.resolve()),
            worktree_identity=str(repository.resolve()),
            branch=branch,
            prior_head=head,
            target_state="committed",
            session_id=session_id,
            provider="git",
            subject_digest=self.digest,
            evidence={"commit": head},
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id=f"session-{task_id.lower()}",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        policy = governing_policy(
            policy={
                "git": {
                    "remote": "origin",
                    "base_branch": "main",
                }
            },
            policy_digest=self.digest,
            runtime_digest=self.digest,
            lock_digest=self.digest,
            governing_base_commit=head,
            session_id=session_id,
            invocation_id=invocation_id,
            freshness_deadline=130.0,
        )
        context = bridge.create_remote_effect_context(
            task=task,
            expected_task_digest=task_digest,
            local_git=local,
            session_id=session_id,
            invocation_id=invocation_id,
            effect=effect,
            expected_pr_number=expected_pr_number,
            expected_base_sha=expected_base_sha,
            expected_checks_digest=expected_checks_digest,
            governing_policy=policy,
            host_capability=capability,
        )
        return {
            "bridge": bridge,
            "repository": repository,
            "common_dir": common_dir,
            "store": store,
            "task": task,
            "task_digest": task_digest,
            "head": head,
            "branch": branch,
            "session_id": session_id,
            "invocation_id": invocation_id,
            "context": context,
            "governing_policy": policy,
        }

    def _pr_mutation_fixture(
        self,
        *,
        task_id: str,
        expected_pr_number: int | None,
        expected_base_sha: str,
        expected_checks_digest: str | None,
        draft: bool = True,
    ):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from tests.host_adapter_test_support import (
            governing_policy,
            governing_runtime_observation,
            native_github_provider_event,
            native_session_event,
            native_user_interaction_event,
        )

        branch = f"codex/{task_id.lower()}"
        fixture = self._remote_effect_fixture(
            task_id=task_id,
            effect="pull_request",
            outcome="pull_request",
            branch=branch,
            expected_pr_number=expected_pr_number,
            expected_base_sha=expected_base_sha,
            expected_checks_digest=expected_checks_digest,
        )
        context = bridge.validate_remote_effect_context(
            fixture["context"],
            expected_task_digest=fixture["task_digest"],
            expected_repo=fixture["repository"],
            expected_worktree=fixture["repository"],
            expected_branch=branch,
            expected_head=fixture["head"],
            expected_session=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            expected_effect="pull_request",
            expected_pr_number=expected_pr_number,
            expected_base_sha=expected_base_sha,
            expected_checks_digest=expected_checks_digest,
        )
        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(fixture["repository"].resolve()),
            target_worktree=str(fixture["repository"].resolve()),
            governing_base_commit=fixture["head"],
            runtime_layout="source",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        policy = governing_policy(
            policy={
                "git": {
                    "remote": "origin",
                    "base_branch": "main",
                }
            },
            policy_digest=self.digest,
            runtime_digest=self.digest,
            lock_digest=self.digest,
            governing_base_commit=fixture["head"],
            remote_repository="example/control-plane",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        native_provider = native_github_provider_event(
            event_id=f"provider-{task_id.lower()}",
            repository="example/control-plane",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
        )

        def provider_preflight(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            if operation == "github_auth_status":
                return 0, b""
            if operation == "github_repository_access":
                return 0, b'{"nameWithOwner":"example/control-plane"}'
            raise AssertionError(f"unexpected provider operation: {operation}")

        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=provider_preflight,
        ):
            provider = bridge.approve_github_pr_write_provider(
                native_provider,
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository="example/control-plane",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        title = bridge.validate_pull_request_title(
            f"Stabilize {task_id}"
        )
        body = bridge.validate_pull_request_body(
            "Bounded pull request mutation test."
        )
        subject_digest = contract_digest(
            {
                "context": context.context_digest,
                "title": title.digest,
                "body": body.digest,
                "draft": draft,
                "expected_pr_number": expected_pr_number,
            }
        )
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id=f"session-{task_id.lower()}",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                observed_at_monotonic=100.0,
            ),
            expected_session_id=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id=f"authorize-{task_id.lower()}",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                task_digest=fixture["task_digest"],
                subject_digest=subject_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=capability,
            task_digest=fixture["task_digest"],
            session_id=fixture["session_id"],
            repository_identity=fixture["repository"],
            worktree_identity=fixture["repository"],
            branch=branch,
            expected_head=fixture["head"],
            subject_digest=subject_digest,
            scope_paths=(".",),
            effect="pull_request",
            operation_nonce=f"tool-{task_id.lower()}",
            invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        request = bridge.build_pull_request_mutation_request(
            context=context,
            provider=provider,
            authorization=authorization,
            title=title,
            body=body,
            draft=draft,
            expected_pr_number=expected_pr_number,
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            tool_use_id=f"tool-{task_id.lower()}",
            clock=lambda: 100.0,
        )
        fixture.update(
            {
                "context": context,
                "provider": provider,
                "authorization": authorization,
                "request": request,
            }
        )
        return fixture

    def test_pr_ready_uses_fresh_one_shot_grant_and_native_ready_effect(
        self,
    ) -> None:
        import json
        import control_plane.host_bridge as bridge

        fixture = self._pr_mutation_fixture(
            task_id="TASK-PR-MARK-READY",
            expected_pr_number=7,
            expected_base_sha="a" * 40,
            expected_checks_digest=None,
            draft=False,
        )
        mutation_argv: list[tuple[str, ...]] = []

        def provider(operation, arguments, max_output_bytes):
            del max_output_bytes
            if operation == "github_pull_request_precondition_pr":
                return 0, json.dumps({
                    "number": 7,
                    "baseRefName": "main",
                    "headRefName": fixture["branch"],
                    "headRefOid": fixture["head"],
                }).encode("utf-8")
            if operation == "github_pull_request_precondition_base":
                return 0, json.dumps({
                    "object": {"sha": "a" * 40}
                }).encode("utf-8")
            if operation == "github_pull_request_precondition_checks":
                return 0, b'{"total_count":0,"check_runs":[]}'
            if operation == "github_pull_request_mutation":
                mutation_argv.append(tuple(arguments))
                return 0, b""
            if operation == "github_pull_request_observe":
                return 0, json.dumps({
                    "number": 7,
                    "url": "https://github.com/example/control-plane/pull/7",
                    "isDraft": False,
                    "baseRefName": "main",
                    "headRefName": fixture["branch"],
                    "headRefOid": fixture["head"],
                }).encode("utf-8")
            raise AssertionError(f"unexpected provider operation: {operation}")

        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=provider,
        ):
            observed = bridge.execute_pull_request_mutation(
                fixture["request"], clock=lambda: 100.0
            )
        self.assertEqual(
            mutation_argv,
            [("gh", "pr", "ready", "7", "--repo", "example/control-plane")],
        )
        validated = bridge.validate_pull_request_mutation(
            observed,
            expected_repository="example/control-plane",
            expected_base="main",
            expected_head_branch=fixture["branch"],
            expected_head_sha=fixture["head"],
            expected_pr_number=7,
            expected_draft=False,
            expected_session_id=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
        )
        self.assertFalse(validated.draft)
        with self.assertRaisesRegex(ValueError, "E_PR_MUTATION"):
            bridge.execute_pull_request_mutation(
                fixture["request"], clock=lambda: 100.0
            )

    def _fresh_feature_push_bindings(
        self,
        fixture,
        *,
        context,
        suffix: str,
        tool_use_id: str,
    ):
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import (
            governing_policy,
            governing_runtime_observation,
            native_session_event,
            native_user_interaction_event,
        )

        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(fixture["repository"].resolve()),
            target_worktree=str(fixture["repository"].resolve()),
            governing_base_commit=fixture["head"],
            runtime_layout="source",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        policy = governing_policy(
            policy={
                "git": {
                    "remote": "origin",
                    "base_branch": "main",
                }
            },
            policy_digest=self.digest,
            runtime_digest=self.digest,
            lock_digest=self.digest,
            governing_base_commit=fixture["head"],
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        inventory_id = f"inventory-{suffix}"
        raw_inventory = bridge.observe_worktree_inventory(
            canonical_common_git_dir=fixture["common_dir"],
            invocation_id=inventory_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            raw_inventory,
            expected_common_git_dir=fixture["common_dir"],
            expected_invocation_id=inventory_id,
            clock=lambda: 100.0,
        )
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id=f"session-{suffix}",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                observed_at_monotonic=100.0,
            ),
            expected_session_id=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id=f"authorize-{suffix}",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                task_digest=fixture["task_digest"],
                subject_digest=context.context_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=capability,
            task_digest=fixture["task_digest"],
            session_id=fixture["session_id"],
            repository_identity=fixture["repository"],
            worktree_identity=fixture["repository"],
            branch=fixture["branch"],
            expected_head=fixture["head"],
            subject_digest=context.context_digest,
            scope_paths=(".",),
            effect="remote_write",
            operation_nonce=tool_use_id,
            invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        return {
            "runtime": runtime,
            "policy": policy,
            "inventory": inventory,
            "authorization": authorization,
            "tool_use_id": tool_use_id,
        }

    def _feature_push_fixture(self, *, task_id: str):
        import control_plane.host_bridge as bridge

        fixture = self._remote_effect_fixture(
            task_id=task_id,
            effect="remote_write",
            outcome="pull_request",
            branch=f"codex/{task_id.lower()}",
        )
        context = bridge.validate_remote_effect_context(
            fixture["context"],
            expected_task_digest=fixture["task_digest"],
            expected_repo=fixture["repository"],
            expected_worktree=fixture["repository"],
            expected_branch=fixture["branch"],
            expected_head=fixture["head"],
            expected_session=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            expected_effect="remote_write",
            expected_pr_number=None,
            expected_base_sha=None,
            expected_checks_digest=None,
        )
        fixture["context"] = context
        fixture["bindings"] = self._fresh_feature_push_bindings(
            fixture,
            context=context,
            suffix=f"{task_id.lower()}-primary",
            tool_use_id=f"tool-{task_id.lower()}-primary",
        )
        return fixture

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

    def test_state_only_mutations_use_per_task_flock_and_cas(self) -> None:
        import control_plane.lifecycle as lifecycle

        store = lifecycle.TaskStore(self.state_dir)
        store.start(
            "TASK-STATE-CAS",
            outcome="commit",
            branch="codex/state-cas",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        original_atomic = lifecycle._atomic_json
        first_at_write = threading.Event()
        release_first = threading.Event()
        results: list[str] = []

        def delayed_atomic(path: Path, value: object) -> None:
            if threading.current_thread().name == "state-cas-first":
                first_at_write.set()
                release_first.wait(timeout=1)
            original_atomic(path, value)

        def advance() -> None:
            try:
                store.transition(
                    "TASK-STATE-CAS",
                    "planned",
                    current_branch="codex/state-cas",
                )
                results.append("ok")
            except ValueError as error:
                results.append(str(error))

        with patch.object(lifecycle, "_atomic_json", delayed_atomic):
            first = threading.Thread(
                target=advance, name="state-cas-first"
            )
            second = threading.Thread(
                target=advance, name="state-cas-second"
            )
            first.start()
            self.assertTrue(first_at_write.wait(timeout=1))
            second.start()
            second.join(timeout=0.2)
            release_first.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(results.count("ok"), 1)
        self.assertEqual(
            sum("E_STATE_TRANSITION" in result for result in results),
            1,
        )
        self.assertEqual(store.status("TASK-STATE-CAS")["generation"], 1)

    def test_verification_task_envelope_factory_emits_complete_schema1_only(
        self,
    ) -> None:
        from control_plane.contracts import validate_task_envelope
        from control_plane.lifecycle import build_verification_task_envelope

        candidate = build_verification_task_envelope(
            task_id="TASK-VERIFY-CANDIDATE",
            profile="control_plane_assurance",
        )
        governing = build_verification_task_envelope(
            task_id="TASK-VERIFY-BASE",
            profile="governing_base_verification",
        )

        self.assertEqual(validate_task_envelope(candidate), [])
        self.assertEqual(validate_task_envelope(governing), [])
        self.assertEqual(set(candidate), set(governing))
        self.assertEqual(candidate["goals"][0]["id"], "verify-candidate")
        self.assertEqual(governing["goals"][0]["id"], "verify-governing-base")
        with self.assertRaisesRegex(ValueError, "E_VERIFICATION_PROFILE"):
            build_verification_task_envelope(
                task_id="TASK-VERIFY-ARBITRARY",
                profile="caller-selected",
            )

    def test_verification_execution_context_denies_product_writes_and_unlisted_commands(
        self,
    ) -> None:
        from control_plane.lifecycle import (
            VerificationExecutionContext,
            _run_verification_command,
        )

        with self.assertRaisesRegex(TypeError, "host-bound"):
            VerificationExecutionContext(
                profile="governing_base_verification"
            )

        with self.assertRaisesRegex(ValueError, "E_VERIFICATION_CONTEXT"):
            _run_verification_command(
                context={"profile": "control_plane_assurance"},
                command_id="git_commit",
                clock=lambda: 100.0,
            )

        repository, _, _, _ = self._two_worktree_repository()
        context = self._verification_context_for_repo(
            repository, self.state_dir / "unlisted-command-temp"
        )
        with self.assertRaisesRegex(ValueError, "E_VERIFICATION_COMMAND"):
            _run_verification_command(
                context=context,
                command_id="git_commit",
                clock=lambda: 100.0,
            )

    def test_verification_runner_is_mechanical_closed_and_needs_no_host_adapter(
        self,
    ) -> None:
        from control_plane.lifecycle import (
            CompletedVerificationCommand,
            TaskLease,
            TaskStore,
            VERIFICATION_COMMAND_IDS,
            _atomic_json,
            run_verification_profile,
        )

        repository, _, common_dir, _ = self._two_worktree_repository()
        task_id = "TASK-VERIFY-MECHANICAL"
        session_id = "session-verify-mechanical"
        store = TaskStore(common_dir, runtime_digest=self.digest)
        store.start(
            task_id,
            outcome="local_change",
            branch="main",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
        ):
            state = store.transition(
                task_id,
                target,
                evidence=evidence,
                current_branch="main",
            )
        lease = TaskLease.acquire(
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            session_id=session_id,
            paths=["."],
            policy_digest=self.digest,
        )
        context = self._verification_context_for_repo(
            repository,
            self.state_dir / "mechanical-verification-temp",
            task_id=task_id,
            session_id=session_id,
            lease_digest=lease["lease_digest"],
        )
        state.update(
            {
                "verification_profile": context.profile,
                "verification_profile_digest": context.profile_digest,
                "verification_runtime_digest": context.runtime_digest,
                "verification_target_digest": context.target_digest,
                "verification_content_trust": context.content_trust,
                "session_id": session_id,
            }
        )
        _atomic_json(store._path(task_id), state)

        def completed(
            *,
            context,
            command_id: str,
            clock,
        ) -> CompletedVerificationCommand:
            del clock
            return CompletedVerificationCommand(
                command_id=command_id,
                returncode=0,
                status="PASS",
                output_digest=self.digest,
                output_truncated=False,
                before_snapshot_digest=self.digest,
                after_snapshot_digest=self.digest,
                context_digest=context.context_digest,
            )

        with patch(
            "control_plane.lifecycle._run_verification_command",
            side_effect=completed,
        ) as runner:
            receipt = run_verification_profile(
                context=context,
                task_store=store,
                expected_generation=state["generation"],
                clock=lambda: 100.0,
            )

        self.assertEqual(
            [call.kwargs["command_id"] for call in runner.call_args_list],
            list(VERIFICATION_COMMAND_IDS["governing_base_verification"]),
        )
        self.assertEqual(
            receipt.host_isolation,
            "pending_verification_host_isolation",
        )
        self.assertEqual(store.status(task_id)["state"], "review_ready")
        with self.assertRaisesRegex(ValueError, "E_VERIFICATION_REPLAY"):
            run_verification_profile(
                context=context,
                task_store=store,
                expected_generation=state["generation"],
                clock=lambda: 100.0,
            )

    def test_candidate_json_cannot_self_certify_supplemental_assurance(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import (
            CompletedVerificationCommand,
            VERIFICATION_SUPPLEMENTAL_RECEIPTS,
            _atomic_json,
            run_verification_profile,
        )

        (
            _repository,
            common_dir,
            store,
            state,
            context,
            _lease,
        ) = self._active_verification_fixture(
            task_id="TASK-VERIFY-FORGED-SUPPLEMENTAL",
            profile="control_plane_assurance",
        )
        receipts = (
            common_dir
            / "codex-control-plane"
            / "verification-receipts"
            / context.task_id
        )
        for kind in VERIFICATION_SUPPLEMENTAL_RECEIPTS[context.profile]:
            semantic = {
                "schema_version": 1,
                "kind": kind,
                "task_id": context.task_id,
                "task_digest": context.task_digest,
                "head": context.expected_head,
                "profile": context.profile,
                "profile_digest": context.profile_digest,
                "generation": state["generation"],
                "session_id": context.session_id,
                "lease_digest": context.lease_digest,
                "status": "PASS",
                "subject_digest": self.digest,
            }
            _atomic_json(
                receipts / f"{kind}.json",
                {
                    **semantic,
                    "receipt_digest": contract_digest(semantic),
                },
            )

        def completed(
            *,
            context,
            command_id: str,
            clock,
        ) -> CompletedVerificationCommand:
            del clock
            return CompletedVerificationCommand(
                command_id=command_id,
                returncode=0,
                status="PASS",
                output_digest=self.digest,
                output_truncated=False,
                before_snapshot_digest=self.digest,
                after_snapshot_digest=self.digest,
                context_digest=context.context_digest,
            )

        with (
            patch(
                "control_plane.lifecycle._run_verification_command",
                side_effect=completed,
            ),
            self.assertRaisesRegex(
                ValueError, "E_VERIFICATION_EVIDENCE"
            ),
        ):
            run_verification_profile(
                context=context,
                task_store=store,
                expected_generation=state["generation"],
                clock=lambda: 100.0,
            )

    def test_task1_rejects_generic_supplemental_evidence_and_keeps_pending(
        self,
    ) -> None:
        import json
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import (
            VERIFICATION_SUPPLEMENTAL_RECEIPTS,
            frame_verification_supplemental_evidence_set,
            publish_verification_supplemental_evidence,
        )
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
        )

        (
            repository,
            _common_dir,
            store,
            state,
            context,
            _lease,
        ) = self._active_verification_fixture(
            task_id="TASK-VERIFY-HOST-SUPPLEMENTAL",
            profile="control_plane_assurance",
        )
        specifications = {
            kind: {
                "status": (
                    "AUDIT"
                    if kind == "SkillPressureEvaluationReceipt"
                    else "PASS"
                ),
                "subject_digest": self.digest,
            }
            for kind in VERIFICATION_SUPPLEMENTAL_RECEIPTS[
                context.profile
            ]
        }
        serialized_specifications = json.loads(
            json.dumps(specifications)
        )
        for index, candidate in enumerate(
            (specifications, serialized_specifications)
        ):
            runtime = governing_runtime_observation(
                runtime_digest=context.runtime_digest,
                lock_digest=self.digest,
                policy_digest=self.digest,
                attestor_worktree=str(repository.resolve()),
                target_worktree=str(repository.resolve()),
                governing_base_commit=context.expected_head,
                runtime_layout="source",
                session_id=context.session_id,
                invocation_id=f"host-supplemental-{index}",
                freshness_deadline=130.0,
            )
            with self.assertRaisesRegex(
                ValueError, "E_VERIFICATION_EVIDENCE"
            ):
                frame_verification_supplemental_evidence_set(
                    governing_runtime=runtime,
                    context=context,
                    expected_generation=state["generation"] + 1,
                    specifications=candidate,
                    clock=lambda: 100.0,
                )
            self.assertFalse(runtime._consumed)

        generic_evidence = []
        for kind in sorted(
            VERIFICATION_SUPPLEMENTAL_RECEIPTS[context.profile]
        ):
            item = object.__new__(
                lifecycle.HostBoundVerificationEvidence
            )
            values = {
                "_consumed": False,
                "observation_id": f"generic-{kind}",
                "kind": kind,
                "receipt_digest": self.digest,
                "status": "PASS",
                "subject_digest": self.digest,
                "task_id": context.task_id,
                "task_digest": context.task_digest,
                "head": context.expected_head,
                "profile": context.profile,
                "profile_digest": context.profile_digest,
                "generation": state["generation"] + 1,
                "session_id": context.session_id,
                "lease_digest": context.lease_digest,
                "context_digest": context.context_digest,
                "freshness_deadline": 130.0,
            }
            for name, value in values.items():
                setattr(item, name, value)
            lifecycle._register_runtime_host_object(
                item, "verification_supplemental_evidence"
            )
            generic_evidence.append(item)
        with self.assertRaisesRegex(
            ValueError, "E_VERIFICATION_EVIDENCE"
        ):
            publish_verification_supplemental_evidence(
                task_store=store,
                context=context,
                evidence=tuple(generic_evidence),
                expected_generation=state["generation"],
                clock=lambda: 100.0,
            )

        with self.assertRaisesRegex(
            ValueError, "E_VERIFICATION_EVIDENCE"
        ):
            publish_verification_supplemental_evidence(
                task_store=store,
                context=context,
                evidence=(),
                expected_generation=state["generation"],
                clock=lambda: 100.0,
            )

        self.assertFalse(
            hasattr(lifecycle, "CompletedMacOSHookSmoke")
        )
        self.assertFalse(
            hasattr(lifecycle, "CompletedSkillPressureEvaluation")
        )
        self.assertFalse(
            hasattr(lifecycle, "ValidatedIndependentReviewObservation")
        )
        current = store.status(context.task_id)
        self.assertEqual(current["state"], "verifying")
        self.assertEqual(current["generation"], state["generation"])
        self.assertNotIn(
            "verification_supplemental_evidence", current
        )
        receipt_root = (
            store.state_dir
            / "codex-control-plane"
            / "verification-receipts"
            / context.task_id
        )
        self.assertFalse(receipt_root.exists())

    def test_bootstrap_state_is_owned_and_closed_by_immutable_base_runtime(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import (
            CompletedVerificationCommand,
            TaskLease,
            TaskStore,
            VERIFICATION_COMMAND_IDS,
            VerificationTaskBootstrap,
            build_verification_task_envelope,
            run_verification_profile,
        )

        repository, _, common_dir, _ = self._two_worktree_repository()
        task_id = "TASK-VERIFY-BASE-OWNED"
        session_id = "session-verify-base-owned"
        task = build_verification_task_envelope(
            task_id=task_id,
            profile="governing_base_verification",
        )
        task_digest = contract_digest(task)
        profile_digest = contract_digest(
            {
                "profile": "governing_base_verification",
                "commands": VERIFICATION_COMMAND_IDS[
                    "governing_base_verification"
                ],
            }
        )
        bootstrap = object.__new__(VerificationTaskBootstrap)
        bootstrap._consumed = False
        bootstrap.task = task
        bootstrap.task_digest = task_digest
        bootstrap.profile = "governing_base_verification"
        bootstrap.profile_digest = profile_digest
        bootstrap.runtime_digest = self.digest
        bootstrap.target_digest = self.digest
        bootstrap.content_trust = "governing_base"
        bootstrap.authority_digest = self.digest
        bootstrap.bootstrap_digest = self.digest
        bootstrap.session_id = session_id
        bridge._register_runtime_host_object(
            bootstrap, "verification_task_bootstrap"
        )

        base_store = TaskStore(common_dir, runtime_digest=self.digest)
        base_store.start(
            task_id,
            outcome="local_change",
            branch="main",
            task_digest=task_digest,
            decision_digest=self.digest,
            verification_bootstrap=bootstrap,
        )
        foreign_store = TaskStore(
            common_dir,
            runtime_digest=contract_digest({"runtime": "candidate"}),
        )
        with self.assertRaisesRegex(
            ValueError, "E_FOREIGN_RUNTIME_STATE"
        ):
            foreign_store.transition(
                task_id, "planned", current_branch="main"
            )

        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
        ):
            state = base_store.transition(
                task_id,
                target,
                evidence=evidence,
                current_branch="main",
            )
        lease = TaskLease.acquire(
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            session_id=session_id,
            paths=["."],
            policy_digest=self.digest,
        )
        context = self._verification_context_for_repo(
            repository,
            self.state_dir / "base-owned-verification-temp",
            task_id=task_id,
            task_digest=task_digest,
            session_id=session_id,
            lease_digest=lease["lease_digest"],
        )

        def completed(
            *,
            context,
            command_id: str,
            clock,
        ) -> CompletedVerificationCommand:
            del clock
            return CompletedVerificationCommand(
                command_id=command_id,
                returncode=0,
                status="PASS",
                output_digest=self.digest,
                output_truncated=False,
                before_snapshot_digest=self.digest,
                after_snapshot_digest=self.digest,
                context_digest=context.context_digest,
            )

        with patch(
            "control_plane.lifecycle._run_verification_command",
            side_effect=completed,
        ):
            run_verification_profile(
                context=context,
                task_store=base_store,
                expected_generation=state["generation"],
                clock=lambda: 100.0,
            )
        closed = base_store.close(task_id, current_branch="main")
        self.assertEqual(closed["state"], "closed")
        self.assertFalse(
            (
                common_dir
                / "codex-control-plane"
                / "leases"
                / f"{task_id}.json"
            ).exists()
        )

    def test_verification_command_observation_detects_tracked_index_and_untracked_mutation(
        self,
    ) -> None:
        from control_plane.lifecycle import _run_verification_command

        repository, _, _, _ = self._two_worktree_repository()
        for index, script in enumerate(
            (
                "from pathlib import Path; Path('tracked.txt').write_text('mutated\\n')",
                "from pathlib import Path; Path('new-untracked.txt').write_text('new\\n')",
            )
        ):
            with self.subTest(script=script):
                temp_root = self.state_dir / f"verify-temp-{index}"
                context = self._verification_context_for_repo(
                    repository, temp_root
                )
                with patch(
                    "control_plane.lifecycle._verification_argv",
                    return_value=(sys.executable, "-c", script),
                ):
                    completed = _run_verification_command(
                        context=context,
                        command_id="doctor",
                        clock=lambda: 100.0,
                    )
                self.assertEqual(completed.status, "FAIL")
                (repository / "tracked.txt").write_text(
                    "baseline\n", encoding="utf-8"
                )
                (repository / "new-untracked.txt").unlink(missing_ok=True)

    def test_verification_snapshot_ignores_large_clean_tracked_content(self) -> None:
        from control_plane.lifecycle import _verification_snapshot

        repository, _, _, _ = self._two_worktree_repository("large-clean-tree")
        large = repository / "large-clean.bin"
        with large.open("wb") as handle:
            handle.truncate(67_108_865)
        subprocess.run(
            ["git", "-C", str(repository), "add", "large-clean.bin"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "commit",
                "-m",
                "test: large clean tracked file",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (repository / "small-untracked.txt").write_text(
            "candidate\n", encoding="utf-8"
        )

        snapshot = _verification_snapshot(repository)

        self.assertRegex(snapshot, r"^sha256:[0-9a-f]{64}$")

    def test_verification_snapshot_ignores_replace_path_config_and_ambient_index(self) -> None:
        from control_plane.lifecycle import _verification_snapshot

        repository, _, _, _ = self._two_worktree_repository(
            "snapshot-git-environment"
        )
        baseline = _verification_snapshot(repository)
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        tracked = repository / "tracked.txt"
        tracked.write_text("replacement tree\n", encoding="utf-8")
        replacement_index = self.state_dir / "snapshot-replacement.index"
        index_environment = dict(__import__("os").environ)
        index_environment["GIT_INDEX_FILE"] = str(replacement_index)
        for arguments in (("read-tree", "HEAD"), ("add", "tracked.txt")):
            subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                env=index_environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        replacement_tree = subprocess.run(
            ["git", "-C", str(repository), "write-tree"],
            check=True,
            env=index_environment,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        tracked.write_text("baseline\n", encoding="utf-8")
        replacement = subprocess.run(
            ["git", "-C", str(repository), "commit-tree", replacement_tree],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(repository), "replace", head, replacement],
            check=True,
        )

        fake_bin = self.state_dir / "snapshot-fake-bin"
        fake_bin.mkdir()
        marker = self.state_dir / "snapshot-ambient-git-executed"
        fake_git = fake_bin / "git"
        fake_git.write_text(
            f"#!/bin/sh\n: > {str(marker)!r}\nexit 99\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o700)
        external_config = self.state_dir / "snapshot-external.gitconfig"
        external_config.write_text(
            "[core]\n\tfsmonitor = /usr/bin/false\n", encoding="utf-8"
        )
        with patch.dict(
            "os.environ",
            {
                "PATH": str(fake_bin),
                "GIT_CONFIG_GLOBAL": str(external_config),
                "GIT_INDEX_FILE": str(replacement_index),
            },
            clear=False,
        ):
            observed = _verification_snapshot(repository)

        self.assertFalse(marker.exists())
        self.assertEqual(observed, baseline)

    def test_verification_snapshot_blocks_clean_filter_before_execution(self) -> None:
        from control_plane.lifecycle import _verification_snapshot

        repository, _, _, _ = self._two_worktree_repository(
            "snapshot-clean-filter"
        )
        attributes = repository / ".gitattributes"
        attributes.write_text(
            "tracked.txt filter=snapshot-clean\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "-C", str(repository), "add", ".gitattributes"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "commit",
                "-m",
                "test: snapshot filter baseline",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        marker = repository / "snapshot-clean-filter-executed"
        filter_script = repository / "snapshot-clean-filter.sh"
        filter_script.write_text(
            f"#!/bin/sh\n: > {str(marker)!r}\ncat\n",
            encoding="utf-8",
        )
        filter_script.chmod(0o700)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "filter.snapshot-clean.clean",
                str(filter_script),
            ],
            check=True,
        )
        (repository / "tracked.txt").write_text(
            "after filter\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(ValueError, "E_GIT_FILTER"):
            _verification_snapshot(repository)

        self.assertFalse(marker.exists())

    def test_verification_runner_uses_sanitized_environment_and_reports_host_isolation(
        self,
    ) -> None:
        from control_plane.lifecycle import (
            VerificationExecutionContext,
            _run_verification_command,
            _sanitized_verification_environment,
        )

        repository, _, _, _ = self._two_worktree_repository()
        temp_root = self.state_dir / "verification-environment"
        context = self._verification_context_for_repo(repository, temp_root)
        with patch.dict(
            "os.environ",
            {
                "GH_TOKEN": "canary",
                "HTTPS_PROXY": "canary",
                "GIT_ASKPASS": "canary",
                "SSH_AUTH_SOCK": "canary",
            },
        ):
            environment = _sanitized_verification_environment(context)

        self.assertNotIn("GH_TOKEN", environment)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertNotIn("canary", environment.values())
        self.assertEqual(
            environment["HOME"], str(temp_root.resolve() / "home")
        )
        with (
            patch.object(
                VerificationExecutionContext,
                "content_trust",
                "external_untrusted",
                create=True,
            ),
            patch("control_plane.lifecycle.subprocess.Popen") as child,
        ):
            with self.assertRaisesRegex(
                ValueError, "E_VERIFICATION_HOST_ISOLATION"
            ):
                _run_verification_command(
                    context=context,
                    command_id="governing_tree_clean",
                    clock=lambda: 100.0,
                )
        child.assert_not_called()

    def test_remote_effect_context_rejects_serialized_capabilities(self) -> None:
        from control_plane.host_bridge import create_remote_effect_context

        with self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT_CONTEXT"):
            create_remote_effect_context(
                task={"schema_version": 1},
                expected_task_digest=self.digest,
                local_git={"evidence": {"commit": "a" * 40}},
                session_id="session-remote-context",
                invocation_id="remote-context",
                effect="remote_write",
                expected_pr_number=None,
                expected_base_sha=None,
                expected_checks_digest=None,
                governing_policy={"policy": "forged"},
                host_capability={"capability": "forged"},
            )

    def test_clean_remote_preflight_requires_host_bound_remote_effect_context(
        self,
    ) -> None:
        fixture = self._remote_effect_fixture(
            task_id="TASK-REMOTE-CLEAN",
            effect="remote_write",
            outcome="pull_request",
        )
        bridge = fixture["bridge"]
        with self.assertRaisesRegex(
            ValueError, "E_REMOTE_EFFECT_CONTEXT"
        ):
            bridge.validate_remote_effect_context(
                {"context_digest": fixture["context"].context_digest},
                expected_task_digest=fixture["task_digest"],
                expected_repo=fixture["repository"],
                expected_worktree=fixture["repository"],
                expected_branch="main",
                expected_head=fixture["head"],
                expected_session=fixture["session_id"],
                expected_invocation_id=fixture["invocation_id"],
                expected_effect="remote_write",
                expected_pr_number=None,
                expected_base_sha=None,
                expected_checks_digest=None,
            )
        forged = object.__new__(bridge.RemoteEffectContext)
        for name in bridge.RemoteEffectContext.__slots__:
            setattr(forged, name, getattr(fixture["context"], name))
        with self.assertRaisesRegex(
            ValueError, "E_REMOTE_EFFECT_CONTEXT"
        ):
            bridge.validate_remote_effect_context(
                forged,
                expected_task_digest=fixture["task_digest"],
                expected_repo=fixture["repository"],
                expected_worktree=fixture["repository"],
                expected_branch="main",
                expected_head=fixture["head"],
                expected_session=fixture["session_id"],
                expected_invocation_id=fixture["invocation_id"],
                expected_effect="remote_write",
                expected_pr_number=None,
                expected_base_sha=None,
                expected_checks_digest=None,
            )
        validated = bridge.validate_remote_effect_context(
            fixture["context"],
            expected_task_digest=fixture["task_digest"],
            expected_repo=fixture["repository"],
            expected_worktree=fixture["repository"],
            expected_branch="main",
            expected_head=fixture["head"],
            expected_session=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            expected_effect="remote_write",
            expected_pr_number=None,
            expected_base_sha=None,
            expected_checks_digest=None,
        )
        self.assertEqual(validated.effect, "remote_write")

    def test_remote_effect_context_revalidates_task_schema_digest_and_outcome(
        self,
    ) -> None:
        fixture = self._remote_effect_fixture(
            task_id="TASK-REMOTE-DIGEST",
            effect="remote_write",
            outcome="pull_request",
        )
        with self.assertRaisesRegex(
            ValueError, "E_REMOTE_EFFECT_CONTEXT"
        ):
            fixture["bridge"].validate_remote_effect_context(
                fixture["context"],
                expected_task_digest="sha256:" + "f" * 64,
                expected_repo=fixture["repository"],
                expected_worktree=fixture["repository"],
                expected_branch="main",
                expected_head=fixture["head"],
                expected_session=fixture["session_id"],
                expected_invocation_id=fixture["invocation_id"],
                expected_effect="remote_write",
                expected_pr_number=None,
                expected_base_sha=None,
                expected_checks_digest=None,
            )
        with self.assertRaisesRegex(
            ValueError, "task outcome does not authorize effect"
        ):
            self._remote_effect_fixture(
                task_id="TASK-REMOTE-OUTCOME",
                effect="integration",
                outcome="pull_request",
            )

    def test_remote_effect_context_revalidates_pr_base_checks_and_invocation_at_use(
        self,
    ) -> None:
        fixture = self._remote_effect_fixture(
            task_id="TASK-REMOTE-DRIFT",
            effect="pull_request",
            outcome="pull_request",
            expected_pr_number=7,
            expected_base_sha="main",
            expected_checks_digest=self.digest,
        )
        bridge = fixture["bridge"]
        for overrides in (
            {"expected_invocation_id": "other-invocation"},
            {"expected_pr_number": 8},
            {"expected_base_sha": "develop"},
            {"expected_checks_digest": "sha256:" + "e" * 64},
        ):
            arguments = {
                "expected_task_digest": fixture["task_digest"],
                "expected_repo": fixture["repository"],
                "expected_worktree": fixture["repository"],
                "expected_branch": "main",
                "expected_head": fixture["head"],
                "expected_session": fixture["session_id"],
                "expected_invocation_id": fixture["invocation_id"],
                "expected_effect": "pull_request",
                "expected_pr_number": 7,
                "expected_base_sha": "main",
                "expected_checks_digest": self.digest,
            }
            arguments.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    ValueError, "E_REMOTE_EFFECT_CONTEXT"
                ):
                    bridge.validate_remote_effect_context(
                        fixture["context"], **arguments
                    )

    def test_pull_request_outcome_cannot_reuse_context_for_integration(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            ValueError, "task outcome does not authorize effect"
        ):
            self._remote_effect_fixture(
                task_id="TASK-REMOTE-INTEGRATION",
                effect="integration",
                outcome="pull_request",
            )

    def test_remote_effect_context_never_authorizes_local_write_or_commit(
        self,
    ) -> None:
        fixture = self._remote_effect_fixture(
            task_id="TASK-REMOTE-NO-LOCAL",
            effect="remote_write",
            outcome="pull_request",
        )
        validated = fixture["bridge"].validate_remote_effect_context(
            fixture["context"],
            expected_task_digest=fixture["task_digest"],
            expected_repo=fixture["repository"],
            expected_worktree=fixture["repository"],
            expected_branch="main",
            expected_head=fixture["head"],
            expected_session=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            expected_effect="remote_write",
            expected_pr_number=None,
            expected_base_sha=None,
            expected_checks_digest=None,
        )
        with self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
            fixture["bridge"].stage_allowlisted_paths(
                governing_runtime={},
                task_context=fixture["task"],
                inventory={},
                lease={},
                authorization=validated,
                paths=("tracked.txt",),
                expected_head=fixture["head"],
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                tool_use_id="tool-local-denied",
                clock=lambda: 100.0,
            )

    def test_governing_git_effects_reject_serialized_authority(self) -> None:
        from control_plane.host_bridge import (
            _sanitized_git_environment,
            stage_allowlisted_paths,
        )

        with patch.dict(
            "os.environ",
            {
                "HOME": "/user-home-canary",
                "GH_TOKEN": "token-canary",
                "HTTPS_PROXY": "proxy-canary",
                "SSH_AUTH_SOCK": "agent-canary",
                "GIT_CONFIG_GLOBAL": "/config-canary",
                "GIT_EXEC_PATH": "/exec-canary",
            },
            clear=False,
        ):
            environment = _sanitized_git_environment()
        self.assertNotIn("token-canary", environment.values())
        self.assertNotIn("proxy-canary", environment.values())
        self.assertNotIn("agent-canary", environment.values())
        self.assertNotIn("/user-home-canary", environment.values())
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(environment["GIT_CONFIG_SYSTEM"], "/dev/null")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")

        with self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
            stage_allowlisted_paths(
                governing_runtime={},
                task_context={},
                inventory={},
                lease={},
                authorization={},
                paths=("control_plane/lifecycle.py",),
                expected_head="a" * 40,
                session_id="session-stage",
                invocation_id="stage",
                tool_use_id="tool-stage",
                clock=lambda: 100.0,
            )

    def test_governing_stage_and_commit_effects_are_closed_and_one_shot(
        self,
    ) -> None:
        import json
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import (
            TaskLease,
            TaskStore,
            _atomic_json,
            _common_lease_lock,
            _task_guard,
        )
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
            native_session_event,
            native_user_interaction_event,
        )

        task_id = "TASK-GOVERNING-GIT"
        session_id = "session-governing-git"
        invocation_id = "invocation-governing-git"
        repository, common_dir, store, head = self._published_delivery_review(task_id)
        branch = store.status(task_id)["branch"]
        delivery_task_digest = store.status(task_id)["task_digest"]
        delivery_policy_digest = self._delivery_policy_digest(repository)
        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=delivery_policy_digest,
            attestor_worktree=str(repository.resolve()),
            target_worktree=str(repository.resolve()),
            governing_base_commit=head,
            runtime_layout="source",
            session_id=session_id,
            invocation_id=invocation_id,
            freshness_deadline=130.0,
        )
        pre_commit = repository / ".git" / "hooks" / "pre-commit"
        pre_commit.write_text(
            "#!/bin/sh\nexit 97\n", encoding="utf-8"
        )
        pre_commit.chmod(0o700)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "commit.gpgSign",
                "true",
            ],
            check=True,
        )
        expected_tree = self._expected_index_tree(repository)
        lease = store.acquire_delivery_lease(
            task_id,
            worktree=str(repository),
            branch=branch,
            session_id=session_id,
            paths=["tracked.txt"],
            policy_digest=self._delivery_policy_digest(repository),
            expected_head=head,
            diff_digest=store.status(task_id)["delivery_review_binding"]["diff_digest"],
            expected_generation=store.status(task_id)["generation"],
        )
        store.prepare_delivery_commit(
            task_id,
            lease=lease,
            snapshot_digest=store.status(task_id)["delivery_review_binding"]["binding_digest"],
            allowlist=("tracked.txt",),
            expected_index_tree=expected_tree,
            parent_head=head,
            expected_tree=expected_tree,
            message="Commit governed change",
        )
        task_context = {
            "task_id": task_id,
            "task_digest": store.status(task_id)["task_digest"],
            "lease_digest": lease["lease_digest"],
        }

        def inventory(invocation: str, *, ttl_seconds: float = 30.0):
            raw = bridge.observe_worktree_inventory(
                canonical_common_git_dir=common_dir,
                invocation_id=invocation,
                clock=lambda: 100.0,
                ttl_seconds=ttl_seconds,
                max_output_bytes=1_000_000,
            )
            return bridge.validate_worktree_inventory_observation(
                raw,
                expected_common_git_dir=common_dir,
                expected_invocation_id=invocation,
                clock=lambda: 100.0,
            )

        stage_subject = contract_digest({"paths": ("tracked.txt",)})
        stage_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-stage-governing",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        stage_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-stage-governing",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=delivery_task_digest,
                subject_digest=stage_subject,
                observed_at_monotonic=100.0,
            ),
            host_capability=stage_capability,
            task_digest=delivery_task_digest,
            session_id=session_id,
            repository_identity=repository,
            worktree_identity=repository,
            branch=branch,
            expected_head=head,
            subject_digest=stage_subject,
            scope_paths=("tracked.txt",),
            effect="local_write",
            operation_nonce="tool-stage-governing",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        attributes = repository / ".gitattributes"
        attributes.write_text(
            "tracked.txt filter=untrusted-clean\n", encoding="utf-8"
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "filter.untrusted-clean.clean",
                "cat",
            ],
            check=True,
        )
        forged_runtime = object.__new__(type(runtime))
        for name in type(runtime).__slots__:
            setattr(forged_runtime, name, getattr(runtime, name))
        with self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
            bridge.stage_allowlisted_paths(
                governing_runtime=forged_runtime,
                task_context=task_context,
                inventory=inventory("inventory-forged-runtime"),
                lease=lease,
                authorization=stage_authorization,
                paths=("tracked.txt",),
                expected_head=head,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_use_id="tool-stage-governing",
                clock=lambda: 100.0,
            )
        self.assertFalse(stage_authorization._consumed)
        with self.assertRaisesRegex(ValueError, "E_GIT_FILTER"):
            bridge.stage_allowlisted_paths(
                governing_runtime=runtime,
                task_context=task_context,
                inventory=inventory("inventory-filter-governing"),
                lease=lease,
                authorization=stage_authorization,
                paths=("tracked.txt",),
                expected_head=head,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_use_id="tool-stage-governing",
                clock=lambda: 100.0,
            )
        self.assertFalse(stage_authorization._consumed)
        attributes.unlink()
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "--unset-all",
                "filter.untrusted-clean.clean",
            ],
            check=True,
        )

        def fresh_stage_authorization(
            suffix: str, *, ttl_seconds: float = 30.0
        ):
            capability = bridge.attest_host_adapter_capability(
                native_session_event(
                    event_id=f"session-stage-post-consume-{suffix}",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    observed_at_monotonic=100.0,
                ),
                expected_session_id=session_id,
                expected_invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            return bridge.frame_effect_authorization(
                native_user_interaction_event(
                    event_id=f"authorize-stage-post-consume-{suffix}",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    task_digest=delivery_task_digest,
                    subject_digest=stage_subject,
                    observed_at_monotonic=100.0,
                ),
                host_capability=capability,
                task_digest=delivery_task_digest,
                session_id=session_id,
                repository_identity=repository,
                worktree_identity=repository,
                branch=branch,
                expected_head=head,
                subject_digest=stage_subject,
                scope_paths=("tracked.txt",),
                effect="local_write",
                operation_nonce=f"tool-stage-post-consume-{suffix}",
                invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=ttl_seconds,
            )

        original_task_state = store.status(task_id)
        lease_path = (
            common_dir
            / "codex-control-plane"
            / "delivery-leases"
            / f"{task_id}.json"
        )
        original_lease_state = json.loads(
            lease_path.read_text(encoding="utf-8")
        )
        real_run = subprocess.run
        original_inventory_consume = bridge._consume_worktree_inventory
        expected_pristine_index = real_run(
            ["git", "-C", str(repository), "write-tree"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

        def advance_clock_after_grants_are_consumed(
            consumed: threading.Event,
            locked: threading.Event,
            now: list[float],
            errors: list[Exception],
        ) -> None:
            try:
                with _common_lease_lock(common_dir):
                    with _task_guard(common_dir, task_id):
                        locked.set()
                        if not consumed.wait(timeout=1.0):
                            raise AssertionError(
                                "effect grants were not consumed before timeout"
                            )
                        now[0] = 106.0
            except Exception as error:
                errors.append(error)

        for expired_grant in ("authorization", "inventory"):
            with self.subTest(stage_post_consume_expiry=expired_grant):
                expiry_now = [100.0]
                expiry_clock_calls: list[float] = []
                expiry_authorization = fresh_stage_authorization(
                    f"expired-{expired_grant}",
                    ttl_seconds=(
                        5.0 if expired_grant == "authorization" else 30.0
                    ),
                )
                expiry_inventory = inventory(
                    f"inventory-stage-expired-{expired_grant}",
                    ttl_seconds=(
                        5.0 if expired_grant == "inventory" else 30.0
                    ),
                )
                grants_consumed = threading.Event()
                expiry_lock_held = threading.Event()
                expiry_errors: list[Exception] = []
                expiry_add_argv: list[tuple[str, ...]] = []
                expiry_thread = threading.Thread(
                    target=advance_clock_after_grants_are_consumed,
                    args=(
                        grants_consumed,
                        expiry_lock_held,
                        expiry_now,
                        expiry_errors,
                    ),
                    name=f"stage-{expired_grant}-expiry",
                    daemon=True,
                )
                expiry_thread.start()
                self.assertTrue(expiry_lock_held.wait(timeout=1.0))

                def consume_then_signal(*args, **kwargs):
                    result = original_inventory_consume(*args, **kwargs)
                    grants_consumed.set()
                    return result

                def record_expired_stage_add(argv, *args, **kwargs):
                    if "add" in argv:
                        expiry_add_argv.append(
                            tuple(str(item) for item in argv)
                        )
                        return subprocess.CompletedProcess(
                            argv, 1, b"", b""
                        )
                    return real_run(argv, *args, **kwargs)

                def expiry_clock() -> float:
                    expiry_clock_calls.append(expiry_now[0])
                    return expiry_now[0]

                try:
                    with (
                        patch.object(
                            bridge,
                            "_consume_worktree_inventory",
                            side_effect=consume_then_signal,
                        ),
                        patch.object(
                            bridge.subprocess,
                            "run",
                            side_effect=record_expired_stage_add,
                        ),
                        self.assertRaisesRegex(
                            ValueError, "E_GIT_EFFECT"
                        ),
                    ):
                        bridge.stage_allowlisted_paths(
                            governing_runtime=runtime,
                            task_context=task_context,
                            inventory=expiry_inventory,
                            lease=lease,
                            authorization=expiry_authorization,
                            paths=("tracked.txt",),
                            expected_head=head,
                            session_id=session_id,
                            invocation_id=invocation_id,
                            tool_use_id=(
                                f"tool-stage-post-consume-expired-"
                                f"{expired_grant}"
                            ),
                            clock=expiry_clock,
                        )
                finally:
                    grants_consumed.set()
                    expiry_thread.join(timeout=2.0)

                self.assertFalse(
                    expiry_thread.is_alive(), "stage expiry deadlocked"
                )
                self.assertEqual(expiry_errors, [])
                self.assertEqual(expiry_now[0], 106.0)
                self.assertLess(
                    expiry_now[0], runtime.freshness_deadline
                )
                self.assertEqual(
                    expiry_clock_calls, [100.0, 100.0, 106.0]
                )
                self.assertEqual(expiry_add_argv, [])
                self.assertTrue(expiry_authorization._consumed)
                self.assertTrue(expiry_inventory._consumed)
                self.assertEqual(
                    real_run(
                        ["git", "-C", str(repository), "write-tree"],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip(),
                    expected_pristine_index,
                )
                self.assertEqual(
                    real_run(
                        ["git", "-C", str(repository), "rev-parse", "HEAD"],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip(),
                    head,
                )

        for drift_kind in ("state", "marker", "lease"):
            with self.subTest(stage_post_consume_drift=drift_kind):
                drift_authorization = fresh_stage_authorization(drift_kind)
                drift_inventory = inventory(
                    f"inventory-stage-post-consume-{drift_kind}"
                )
                stage_add_argv: list[tuple[str, ...]] = []

                def consume_then_drift(*args, **kwargs):
                    result = original_inventory_consume(*args, **kwargs)
                    if drift_kind == "lease":
                        drifted_lease = dict(original_lease_state)
                        drifted_lease["generation"] = (
                            int(drifted_lease["generation"]) + 1
                        )
                        drifted_lease["lease_digest"] = contract_digest(
                            {
                                key: value
                                for key, value in drifted_lease.items()
                                if key != "lease_digest"
                            }
                        )
                        _atomic_json(lease_path, drifted_lease)
                    else:
                        drifted_task = store.status(task_id)
                        if drift_kind == "state":
                            drifted_task["state"] = "committed"
                        else:
                            drifted_marker = dict(
                                drifted_task["finalizing_delivery_commit"]
                            )
                            drifted_marker["phase"] = "index_observed"
                            drifted_marker["marker_digest"] = contract_digest(
                                {
                                    key: value
                                    for key, value in drifted_marker.items()
                                    if key != "marker_digest"
                                }
                            )
                            drifted_task["finalizing_delivery_commit"] = (
                                drifted_marker
                            )
                        _atomic_json(store._path(task_id), drifted_task)
                    return result

                def record_stage_add(argv, *args, **kwargs):
                    if "add" in argv:
                        stage_add_argv.append(
                            tuple(str(item) for item in argv)
                        )
                        return subprocess.CompletedProcess(
                            argv, 1, b"", b""
                        )
                    return real_run(argv, *args, **kwargs)

                try:
                    with (
                        patch.object(
                            bridge,
                            "_consume_worktree_inventory",
                            side_effect=consume_then_drift,
                        ),
                        patch.object(
                            bridge.subprocess,
                            "run",
                            side_effect=record_stage_add,
                        ),
                        self.assertRaisesRegex(
                            ValueError, "E_GIT_EFFECT"
                        ),
                    ):
                        bridge.stage_allowlisted_paths(
                            governing_runtime=runtime,
                            task_context=task_context,
                            inventory=drift_inventory,
                            lease=lease,
                            authorization=drift_authorization,
                            paths=("tracked.txt",),
                            expected_head=head,
                            session_id=session_id,
                            invocation_id=invocation_id,
                            tool_use_id=(
                                f"tool-stage-post-consume-{drift_kind}"
                            ),
                            clock=lambda: 100.0,
                        )
                finally:
                    _atomic_json(store._path(task_id), original_task_state)
                    _atomic_json(lease_path, original_lease_state)

                self.assertEqual(stage_add_argv, [])
                self.assertTrue(drift_authorization._consumed)
                self.assertTrue(drift_inventory._consumed)
                self.assertEqual(
                    real_run(
                        ["git", "-C", str(repository), "write-tree"],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip(),
                    expected_pristine_index,
                )
                self.assertEqual(
                    real_run(
                        ["git", "-C", str(repository), "rev-parse", "HEAD"],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip(),
                    head,
                )

        race_authorization = fresh_stage_authorization("locked-race")
        race_inventory = inventory("inventory-stage-locked-race")
        race_locked = threading.Event()
        second_filter_reached = threading.Event()
        durable_drift_written = threading.Event()
        race_errors: list[BaseException] = []
        race_add_argv: list[tuple[str, ...]] = []

        def mutate_state_under_canonical_locks() -> None:
            try:
                with _common_lease_lock(common_dir):
                    with _task_guard(common_dir, task_id):
                        race_locked.set()
                        second_filter_reached.wait(timeout=0.25)
                        drifted_task = json.loads(
                            store._path(task_id).read_text(encoding="utf-8")
                        )
                        drifted_task["state"] = "committed"
                        _atomic_json(store._path(task_id), drifted_task)
                        durable_drift_written.set()
            except BaseException as error:
                race_errors.append(error)

        race_thread = threading.Thread(
            target=mutate_state_under_canonical_locks,
            name="stage-governing-state-race",
            daemon=True,
        )
        race_thread.start()
        self.assertTrue(race_locked.wait(timeout=1.0))
        real_filter_guard = bridge._assert_no_external_git_filters
        filter_guard_calls = 0

        def signal_from_second_filter(*args, **kwargs):
            nonlocal filter_guard_calls
            filter_guard_calls += 1
            if filter_guard_calls == 2:
                second_filter_reached.set()
                if not durable_drift_written.wait(timeout=1.0):
                    raise AssertionError(
                        "durable drift did not complete after the second guard"
                    )
            return real_filter_guard(*args, **kwargs)

        def record_racing_stage_add(argv, *args, **kwargs):
            if "add" in argv:
                race_add_argv.append(tuple(str(item) for item in argv))
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return real_run(argv, *args, **kwargs)

        try:
            with (
                patch.object(
                    bridge,
                    "_assert_no_external_git_filters",
                    side_effect=signal_from_second_filter,
                ),
                patch.object(
                    bridge.subprocess,
                    "run",
                    side_effect=record_racing_stage_add,
                ),
                self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"),
            ):
                bridge.stage_allowlisted_paths(
                    governing_runtime=runtime,
                    task_context=task_context,
                    inventory=race_inventory,
                    lease=lease,
                    authorization=race_authorization,
                    paths=("tracked.txt",),
                    expected_head=head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    tool_use_id="tool-stage-post-consume-locked-race",
                    clock=lambda: 100.0,
                )
        finally:
            second_filter_reached.set()
            race_thread.join(timeout=2.0)
            _atomic_json(store._path(task_id), original_task_state)

        self.assertFalse(race_thread.is_alive(), "stage race deadlocked")
        self.assertEqual(race_errors, [])
        self.assertTrue(durable_drift_written.is_set())
        self.assertEqual(race_add_argv, [])
        self.assertTrue(race_authorization._consumed)
        self.assertTrue(race_inventory._consumed)
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "write-tree"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            expected_pristine_index,
        )
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            head,
        )

        shared_inventory = inventory("inventory-stage-shared-claim")
        shared_authorizations = {
            suffix: fresh_stage_authorization(f"inventory-claim-{suffix}")
            for suffix in ("a", "b")
        }
        self.assertNotEqual(
            shared_authorizations["a"].authorization_id,
            shared_authorizations["b"].authorization_id,
        )
        start_stage_claim = threading.Barrier(2)
        concurrent_current_check = threading.Barrier(2)
        inventory_check_counts = threading.local()
        race_results_lock = threading.Lock()
        shared_claim_results: list[object] = []
        shared_claim_errors: list[Exception] = []
        shared_claim_add_argv: list[tuple[str, ...]] = []
        real_inventory_is_current = bridge._inventory_is_current

        def synchronize_inventory_claim(candidate) -> bool:
            count = getattr(inventory_check_counts, "count", 0) + 1
            inventory_check_counts.count = count
            if candidate is shared_inventory and count == 2:
                try:
                    concurrent_current_check.wait(timeout=0.25)
                except threading.BrokenBarrierError:
                    pass
            return real_inventory_is_current(candidate)

        def record_shared_claim_add(argv, *args, **kwargs):
            if "add" in argv:
                with race_results_lock:
                    shared_claim_add_argv.append(
                        tuple(str(item) for item in argv)
                    )
            return real_run(argv, *args, **kwargs)

        def stage_with_shared_inventory(suffix: str) -> None:
            try:
                start_stage_claim.wait(timeout=1.0)
                result = bridge.stage_allowlisted_paths(
                    governing_runtime=runtime,
                    task_context=task_context,
                    inventory=shared_inventory,
                    lease=lease,
                    authorization=shared_authorizations[suffix],
                    paths=("tracked.txt",),
                    expected_head=head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    tool_use_id=(
                        f"tool-stage-post-consume-inventory-claim-{suffix}"
                    ),
                    clock=lambda: 100.0,
                )
                with race_results_lock:
                    shared_claim_results.append(result)
            except Exception as error:
                with race_results_lock:
                    shared_claim_errors.append(error)

        shared_claim_threads = tuple(
            threading.Thread(
                target=stage_with_shared_inventory,
                args=(suffix,),
                name=f"stage-shared-inventory-{suffix}",
                daemon=True,
            )
            for suffix in ("a", "b")
        )
        with (
            patch.object(
                bridge,
                "_inventory_is_current",
                side_effect=synchronize_inventory_claim,
            ),
            patch.object(
                bridge.subprocess,
                "run",
                side_effect=record_shared_claim_add,
            ),
        ):
            for thread in shared_claim_threads:
                thread.start()
            for thread in shared_claim_threads:
                thread.join(timeout=2.0)

        self.assertTrue(
            all(not thread.is_alive() for thread in shared_claim_threads),
            "shared inventory claim deadlocked",
        )
        self.assertEqual(len(shared_claim_results), 1)
        self.assertEqual(len(shared_claim_errors), 1)
        self.assertIsInstance(shared_claim_errors[0], ValueError)
        self.assertRegex(
            str(shared_claim_errors[0]), "E_LEASE_OBSERVATION_STALE"
        )
        self.assertEqual(len(shared_claim_add_argv), 1)
        self.assertTrue(shared_inventory._consumed)
        self.assertTrue(
            all(
                authorization._consumed
                for authorization in shared_authorizations.values()
            )
        )
        self.assertEqual(
            shared_claim_results[0].index_tree, expected_tree
        )
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "write-tree"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            expected_tree,
        )
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            head,
        )

        stage_boundary_clock_calls: list[float] = []

        def stage_boundary_clock() -> float:
            stage_boundary_clock_calls.append(130.0)
            return 130.0

        index = bridge.stage_allowlisted_paths(
            governing_runtime=runtime,
            task_context=task_context,
            inventory=inventory("inventory-stage-governing"),
            lease=lease,
            authorization=stage_authorization,
            paths=("tracked.txt",),
            expected_head=head,
            session_id=session_id,
            invocation_id=invocation_id,
            tool_use_id="tool-stage-governing",
            clock=stage_boundary_clock,
        )
        self.assertEqual(stage_boundary_clock_calls, [130.0] * 4)
        with self.assertRaisesRegex(ValueError, "E_AUTH_REPLAY"):
            bridge.consume_authorization(
                stage_authorization,
                expected_task_digest=store.status(task_id)["task_digest"],
                expected_session_id=session_id,
                expected_repository_identity=repository,
                expected_worktree_identity=repository,
                expected_branch=branch,
                expected_head=head,
                expected_subject_digest=stage_subject,
                expected_scope_paths=("tracked.txt",),
                expected_effect="local_write",
                expected_operation_nonce="tool-stage-governing",
                expected_invocation_id=invocation_id,
                clock=lambda: 131.0,
            )

        message = "Commit governed change"
        commit_subject = contract_digest(
            {"index": index.observation_digest, "message": message}
        )
        commit_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-commit-governing",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        commit_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-commit-governing",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=delivery_task_digest,
                subject_digest=commit_subject,
                observed_at_monotonic=100.0,
            ),
            host_capability=commit_capability,
            task_digest=delivery_task_digest,
            session_id=session_id,
            repository_identity=repository,
            worktree_identity=repository,
            branch=branch,
            expected_head=head,
            subject_digest=commit_subject,
            scope_paths=("tracked.txt",),
            effect="commit",
            operation_nonce="tool-commit-governing",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

        def fresh_commit_authorization(
            suffix: str, *, ttl_seconds: float = 30.0
        ):
            capability = bridge.attest_host_adapter_capability(
                native_session_event(
                    event_id=f"session-commit-{suffix}",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    observed_at_monotonic=100.0,
                ),
                expected_session_id=session_id,
                expected_invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            return bridge.frame_effect_authorization(
                native_user_interaction_event(
                    event_id=f"authorize-commit-{suffix}",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    task_digest=delivery_task_digest,
                    subject_digest=commit_subject,
                    observed_at_monotonic=100.0,
                ),
                host_capability=capability,
                task_digest=delivery_task_digest,
                session_id=session_id,
                repository_identity=repository,
                worktree_identity=repository,
                branch=branch,
                expected_head=head,
                subject_digest=commit_subject,
                scope_paths=("tracked.txt",),
                effect="commit",
                operation_nonce=f"tool-commit-{suffix}",
                invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=ttl_seconds,
            )
        with self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
            bridge.commit_staged_change(
                governing_runtime=runtime,
                task_context=task_context,
                inventory=inventory("inventory-commit-before-index-observed"),
                lease=lease,
                index_observation=index,
                authorization=commit_authorization,
                message=message,
                expected_prior_head=head,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_use_id="tool-commit-governing",
                clock=lambda: 100.0,
            )
        self.assertFalse(commit_authorization._consumed)
        self.assertEqual(
            subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip(),
            head,
        )
        store.observe_delivery_index(
            task_id, lease=lease, expected_index_tree=index.index_tree
        )
        drifted_message = "Commit a different governed change"
        drifted_subject = contract_digest(
            {"index": index.observation_digest, "message": drifted_message}
        )
        drifted_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-commit-message-drift",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        drifted_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-commit-message-drift",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=delivery_task_digest,
                subject_digest=drifted_subject,
                observed_at_monotonic=100.0,
            ),
            host_capability=drifted_capability,
            task_digest=delivery_task_digest,
            session_id=session_id,
            repository_identity=repository,
            worktree_identity=repository,
            branch=branch,
            expected_head=head,
            subject_digest=drifted_subject,
            scope_paths=("tracked.txt",),
            effect="commit",
            operation_nonce="tool-commit-message-drift",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        real_run = subprocess.run
        commit_argv: list[tuple[str, ...]] = []

        def record_commit(argv, *args, **kwargs):
            if "commit" in argv:
                commit_argv.append(tuple(str(item) for item in argv))
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return real_run(argv, *args, **kwargs)

        with patch.object(
            bridge.subprocess, "run", side_effect=record_commit
        ), self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
            bridge.commit_staged_change(
                governing_runtime=runtime,
                task_context=task_context,
                inventory=inventory("inventory-commit-message-drift"),
                lease=lease,
                index_observation=index,
                authorization=drifted_authorization,
                message=drifted_message,
                expected_prior_head=head,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_use_id="tool-commit-message-drift",
                clock=lambda: 100.0,
            )
        self.assertEqual(commit_argv, [])
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            head,
        )
        self.assertFalse(index._consumed)
        original_state = store.status(task_id)
        original_marker = dict(
            original_state["finalizing_delivery_commit"]
        )
        for field, value in (
            ("parent_head", "e" * 40),
            ("expected_tree", "f" * 40),
        ):
            with self.subTest(marker_drift=field):
                drifted_state = store.status(task_id)
                drifted_marker = dict(
                    drifted_state["finalizing_delivery_commit"]
                )
                drifted_marker[field] = value
                drifted_marker["marker_digest"] = contract_digest(
                    {
                        key: item
                        for key, item in drifted_marker.items()
                        if key != "marker_digest"
                    }
                )
                drifted_state["finalizing_delivery_commit"] = drifted_marker
                _atomic_json(store._path(task_id), drifted_state)
                marker_commit_argv: list[tuple[str, ...]] = []

                def record_marker_commit(argv, *args, **kwargs):
                    if "commit" in argv:
                        marker_commit_argv.append(
                            tuple(str(item) for item in argv)
                        )
                        return subprocess.CompletedProcess(
                            argv, 1, b"", b""
                        )
                    return real_run(argv, *args, **kwargs)

                with patch.object(
                    bridge.subprocess,
                    "run",
                    side_effect=record_marker_commit,
                ), self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
                    bridge.commit_staged_change(
                        governing_runtime=runtime,
                        task_context=task_context,
                        inventory=inventory(
                            f"inventory-commit-marker-{field}"
                        ),
                        lease=lease,
                        index_observation=index,
                        authorization=commit_authorization,
                        message=message,
                        expected_prior_head=head,
                        session_id=session_id,
                        invocation_id=invocation_id,
                        tool_use_id="tool-commit-governing",
                        clock=lambda: 100.0,
                    )
                self.assertEqual(marker_commit_argv, [])
                self.assertFalse(commit_authorization._consumed)
                self.assertFalse(index._consumed)
                restored = store.status(task_id)
                restored["finalizing_delivery_commit"] = dict(
                    original_marker
                )
                _atomic_json(store._path(task_id), restored)
        post_consume_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-commit-post-consume-drift",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        post_consume_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-commit-post-consume-drift",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=delivery_task_digest,
                subject_digest=commit_subject,
                observed_at_monotonic=100.0,
            ),
            host_capability=post_consume_capability,
            task_digest=delivery_task_digest,
            session_id=session_id,
            repository_identity=repository,
            worktree_identity=repository,
            branch=branch,
            expected_head=head,
            subject_digest=commit_subject,
            scope_paths=("tracked.txt",),
            effect="commit",
            operation_nonce="tool-commit-post-consume-drift",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        expected_index_bytes = real_run(
            ["git", "-C", str(repository), "show", ":tracked.txt"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        original_consume = bridge.consume_authorization
        post_consume_commit_argv: list[tuple[str, ...]] = []

        def consume_then_mutate_index(*args, **kwargs):
            result = original_consume(*args, **kwargs)
            (repository / "tracked.txt").write_text(
                "post-consume index drift\n", encoding="utf-8"
            )
            real_run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "add",
                    "--",
                    "tracked.txt",
                ],
                check=True,
            )
            return result

        def record_post_consume_commit(argv, *args, **kwargs):
            if "commit" in argv:
                post_consume_commit_argv.append(
                    tuple(str(item) for item in argv)
                )
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return real_run(argv, *args, **kwargs)

        with (
            patch.object(
                bridge,
                "consume_authorization",
                side_effect=consume_then_mutate_index,
            ),
            patch.object(
                bridge.subprocess,
                "run",
                side_effect=record_post_consume_commit,
            ),
            self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"),
        ):
            bridge.commit_staged_change(
                governing_runtime=runtime,
                task_context=task_context,
                inventory=inventory("inventory-commit-post-consume-drift"),
                lease=lease,
                index_observation=index,
                authorization=post_consume_authorization,
                message=message,
                expected_prior_head=head,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_use_id="tool-commit-post-consume-drift",
                clock=lambda: 100.0,
            )
        self.assertEqual(post_consume_commit_argv, [])
        self.assertTrue(post_consume_authorization._consumed)
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            head,
        )
        (repository / "tracked.txt").write_bytes(expected_index_bytes)
        real_run(
            [
                "git",
                "-C",
                str(repository),
                "add",
                "--",
                "tracked.txt",
            ],
            check=True,
        )
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "write-tree"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            index.index_tree,
        )
        for expired_grant in ("authorization", "inventory"):
            with self.subTest(commit_post_consume_expiry=expired_grant):
                expiry_now = [100.0]
                expiry_clock_calls: list[float] = []
                expiry_authorization = fresh_commit_authorization(
                    f"expired-{expired_grant}",
                    ttl_seconds=(
                        5.0 if expired_grant == "authorization" else 30.0
                    ),
                )
                expiry_inventory = inventory(
                    f"inventory-commit-expired-{expired_grant}",
                    ttl_seconds=(
                        5.0 if expired_grant == "inventory" else 30.0
                    ),
                )
                grants_consumed = threading.Event()
                expiry_lock_held = threading.Event()
                expiry_errors: list[Exception] = []
                expiry_commit_argv: list[tuple[str, ...]] = []
                expiry_thread = threading.Thread(
                    target=advance_clock_after_grants_are_consumed,
                    args=(
                        grants_consumed,
                        expiry_lock_held,
                        expiry_now,
                        expiry_errors,
                    ),
                    name=f"commit-{expired_grant}-expiry",
                    daemon=True,
                )
                expiry_thread.start()
                self.assertTrue(expiry_lock_held.wait(timeout=1.0))

                def consume_then_signal(*args, **kwargs):
                    result = original_inventory_consume(*args, **kwargs)
                    grants_consumed.set()
                    return result

                def record_expired_commit(argv, *args, **kwargs):
                    if "commit" in argv:
                        expiry_commit_argv.append(
                            tuple(str(item) for item in argv)
                        )
                        return subprocess.CompletedProcess(
                            argv, 1, b"", b""
                        )
                    return real_run(argv, *args, **kwargs)

                def expiry_clock() -> float:
                    expiry_clock_calls.append(expiry_now[0])
                    return expiry_now[0]

                try:
                    with (
                        patch.object(
                            bridge,
                            "_consume_worktree_inventory",
                            side_effect=consume_then_signal,
                        ),
                        patch.object(
                            bridge.subprocess,
                            "run",
                            side_effect=record_expired_commit,
                        ),
                        self.assertRaisesRegex(
                            ValueError, "E_GIT_EFFECT"
                        ),
                    ):
                        bridge.commit_staged_change(
                            governing_runtime=runtime,
                            task_context=task_context,
                            inventory=expiry_inventory,
                            lease=lease,
                            index_observation=index,
                            authorization=expiry_authorization,
                            message=message,
                            expected_prior_head=head,
                            session_id=session_id,
                            invocation_id=invocation_id,
                            tool_use_id=(
                                f"tool-commit-expired-{expired_grant}"
                            ),
                            clock=expiry_clock,
                        )
                finally:
                    grants_consumed.set()
                    expiry_thread.join(timeout=2.0)

                self.assertFalse(
                    expiry_thread.is_alive(), "commit expiry deadlocked"
                )
                self.assertEqual(expiry_errors, [])
                self.assertEqual(expiry_now[0], 106.0)
                self.assertLess(
                    expiry_now[0], runtime.freshness_deadline
                )
                self.assertEqual(
                    expiry_clock_calls, [100.0, 100.0, 106.0]
                )
                self.assertEqual(expiry_commit_argv, [])
                self.assertTrue(expiry_authorization._consumed)
                self.assertTrue(expiry_inventory._consumed)
                self.assertFalse(index._consumed)
                self.assertEqual(
                    real_run(
                        ["git", "-C", str(repository), "write-tree"],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip(),
                    index.index_tree,
                )
                self.assertEqual(
                    real_run(
                        ["git", "-C", str(repository), "rev-parse", "HEAD"],
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip(),
                    head,
                )
        commit_race_authorization = fresh_commit_authorization("locked-race")
        commit_race_inventory = inventory("inventory-commit-locked-race")
        commit_race_locked = threading.Event()
        second_binding_reached = threading.Event()
        commit_drift_written = threading.Event()
        commit_race_errors: list[BaseException] = []
        racing_commit_argv: list[tuple[str, ...]] = []
        commit_original_task = store.status(task_id)

        def mutate_commit_state_under_canonical_locks() -> None:
            try:
                with _common_lease_lock(common_dir):
                    with _task_guard(common_dir, task_id):
                        commit_race_locked.set()
                        second_binding_reached.wait(timeout=0.25)
                        drifted_task = json.loads(
                            store._path(task_id).read_text(encoding="utf-8")
                        )
                        drifted_task["state"] = "committed"
                        _atomic_json(store._path(task_id), drifted_task)
                        commit_drift_written.set()
            except BaseException as error:
                commit_race_errors.append(error)

        commit_race_thread = threading.Thread(
            target=mutate_commit_state_under_canonical_locks,
            name="commit-governing-state-race",
            daemon=True,
        )
        commit_race_thread.start()
        self.assertTrue(commit_race_locked.wait(timeout=1.0))
        real_commit_binding = bridge._validate_staged_commit_binding
        commit_binding_calls = 0

        def signal_after_second_commit_binding(*args, **kwargs):
            nonlocal commit_binding_calls
            result = real_commit_binding(*args, **kwargs)
            commit_binding_calls += 1
            if commit_binding_calls == 2:
                second_binding_reached.set()
                if not commit_drift_written.wait(timeout=1.0):
                    raise AssertionError(
                        "durable drift did not complete after commit binding"
                    )
            return result

        def record_racing_commit(argv, *args, **kwargs):
            if "commit" in argv:
                racing_commit_argv.append(tuple(str(item) for item in argv))
                return subprocess.CompletedProcess(argv, 1, b"", b"")
            return real_run(argv, *args, **kwargs)

        try:
            with (
                patch.object(
                    bridge,
                    "_validate_staged_commit_binding",
                    side_effect=signal_after_second_commit_binding,
                ),
                patch.object(
                    bridge.subprocess,
                    "run",
                    side_effect=record_racing_commit,
                ),
                self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"),
            ):
                bridge.commit_staged_change(
                    governing_runtime=runtime,
                    task_context=task_context,
                    inventory=commit_race_inventory,
                    lease=lease,
                    index_observation=index,
                    authorization=commit_race_authorization,
                    message=message,
                    expected_prior_head=head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    tool_use_id="tool-commit-locked-race",
                    clock=lambda: 100.0,
                )
        finally:
            second_binding_reached.set()
            commit_race_thread.join(timeout=2.0)
            _atomic_json(store._path(task_id), commit_original_task)

        self.assertFalse(
            commit_race_thread.is_alive(), "commit race deadlocked"
        )
        self.assertEqual(commit_race_errors, [])
        self.assertTrue(commit_drift_written.is_set())
        self.assertEqual(racing_commit_argv, [])
        self.assertTrue(commit_race_authorization._consumed)
        self.assertTrue(commit_race_inventory._consumed)
        self.assertFalse(index._consumed)
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "write-tree"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            index.index_tree,
        )
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            head,
        )
        race_index = object.__new__(bridge.LocalGitIndexObservation)
        for field in (
            "task_digest",
            "worktree_identity",
            "branch",
            "head",
            "index_tree",
            "paths",
            "session_id",
            "invocation_id",
            "observation_digest",
        ):
            setattr(race_index, field, getattr(index, field))
        race_index._consumed = False
        ref_race_authorization = fresh_commit_authorization("ref-race")
        ref_race_inventory = inventory("inventory-commit-ref-race")
        raced = False

        def race_ref_at_commit_boundary(argv, *args, **kwargs):
            nonlocal raced
            if not raced and ("commit" in argv or "update-ref" in argv):
                raced = True
                base_tree = real_run(
                    [
                        "git", "-C", str(repository), "show", "-s",
                        "--format=%T", head,
                    ],
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.strip()
                competitor = real_run(
                    [
                        "git", "-C", str(repository), "commit-tree",
                        base_tree, "-p", head,
                    ],
                    check=True,
                    input=b"competing commit\n",
                    stdout=subprocess.PIPE,
                ).stdout.decode("ascii").strip()
                real_run(
                    [
                        "git", "-C", str(repository), "update-ref",
                        f"refs/heads/{branch}", competitor, head,
                    ],
                    check=True,
                )
            return real_run(argv, *args, **kwargs)

        try:
            with (
                patch.object(
                    bridge.subprocess,
                    "run",
                    side_effect=race_ref_at_commit_boundary,
                ),
                self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"),
            ):
                bridge.commit_staged_change(
                    governing_runtime=runtime,
                    task_context=task_context,
                    inventory=ref_race_inventory,
                    lease=lease,
                    index_observation=race_index,
                    authorization=ref_race_authorization,
                    message=message,
                    expected_prior_head=head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    tool_use_id="tool-commit-ref-race",
                    clock=lambda: 100.0,
                )
        finally:
            live_head = real_run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
            if live_head != head:
                real_run(
                    [
                        "git", "-C", str(repository), "update-ref",
                        f"refs/heads/{branch}", head, live_head,
                    ],
                    check=True,
                )
        self.assertTrue(raced)
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            head,
        )
        self.assertEqual(
            real_run(
                ["git", "-C", str(repository), "write-tree"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            index.index_tree,
        )
        self.assertFalse(index._consumed)
        commit_boundary_clock_calls: list[float] = []

        def commit_boundary_clock() -> float:
            commit_boundary_clock_calls.append(130.0)
            return 130.0

        committed = bridge.commit_staged_change(
            governing_runtime=runtime,
            task_context=task_context,
            inventory=inventory("inventory-commit-governing"),
            lease=lease,
            index_observation=index,
            authorization=commit_authorization,
            message=message,
            expected_prior_head=head,
            session_id=session_id,
            invocation_id=invocation_id,
            tool_use_id="tool-commit-governing",
            clock=commit_boundary_clock,
        )
        self.assertEqual(commit_boundary_clock_calls, [130.0] * 5)
        self.assertNotEqual(committed.evidence["commit"], head)
        self.assertTrue(index._consumed)
        with self.assertRaisesRegex(ValueError, "E_AUTH_REPLAY"):
            bridge.consume_authorization(
                commit_authorization,
                expected_task_digest=delivery_task_digest,
                expected_session_id=session_id,
                expected_repository_identity=repository,
                expected_worktree_identity=repository,
                expected_branch=branch,
                expected_head=head,
                expected_subject_digest=commit_subject,
                expected_scope_paths=("tracked.txt",),
                expected_effect="commit",
                expected_operation_nonce="tool-commit-governing",
                expected_invocation_id=invocation_id,
                clock=lambda: 131.0,
            )

        new_head = committed.evidence["commit"]
        self.assertEqual(
            real_run(
                [
                    "git", "-C", str(repository), "rev-list", "--parents",
                    "-n", "1", new_head,
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.split(),
            [new_head, head],
        )
        self.assertEqual(
            real_run(
                [
                    "git", "-C", str(repository), "show", "-s",
                    "--format=%T", new_head,
                ],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip(),
            index.index_tree,
        )
        validated_commit = bridge.validate_local_git_observation(
            committed,
            expected_task_digest=store.status(task_id)["task_digest"],
            expected_repo=repository,
            expected_worktree=repository,
            expected_branch=branch,
            expected_prior_head=head,
            expected_target_state="committed",
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 130.0,
        )
        store.publish_delivery_commit(
            task_id=task_id,
            lease=lease,
            observation=validated_commit,
            current_branch=branch,
        )
        (repository / "tracked.txt").write_text(
            "stale lease must not stage\n", encoding="utf-8"
        )
        stale_subject = contract_digest({"paths": ("tracked.txt",)})
        stale_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-stale-lease",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        stale_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-stale-lease",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=delivery_task_digest,
                subject_digest=stale_subject,
                observed_at_monotonic=100.0,
            ),
            host_capability=stale_capability,
            task_digest=delivery_task_digest,
            session_id=session_id,
            repository_identity=repository,
            worktree_identity=repository,
            branch=branch,
            expected_head=new_head,
            subject_digest=stale_subject,
            scope_paths=("tracked.txt",),
            effect="local_write",
            operation_nonce="tool-stale-lease",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        with self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
            bridge.stage_allowlisted_paths(
                governing_runtime=runtime,
                task_context=task_context,
                inventory=inventory("inventory-stale-lease"),
                lease=lease,
                authorization=stale_authorization,
                paths=("tracked.txt",),
                expected_head=new_head,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_use_id="tool-stale-lease",
                clock=lambda: 100.0,
            )

    def test_stage_rejects_directory_glob_before_descendant_clean_filter(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
            native_session_event,
            native_user_interaction_event,
        )

        task_id = "TASK-STAGE-DESCENDANT-FILTER"
        session_id = "session-stage-descendant-filter"
        invocation_id = "stage-descendant-filter"
        repository, common_dir, store, head = self._published_delivery_review(task_id)
        branch = store.status(task_id)["branch"]
        delivery_task_digest = store.status(task_id)["task_digest"]
        lease = store.acquire_delivery_lease(
            task_id,
            worktree=str(repository),
            branch=branch,
            session_id=session_id,
            paths=["tracked.txt"],
            policy_digest=self._delivery_policy_digest(repository),
            expected_head=head,
            diff_digest=store.status(task_id)["delivery_review_binding"]["diff_digest"],
            expected_generation=store.status(task_id)["generation"],
        )
        task_context = {
            "task_id": task_id,
            "task_digest": delivery_task_digest,
            "lease_digest": lease["lease_digest"],
        }
        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(repository.resolve()),
            target_worktree=str(repository.resolve()),
            governing_base_commit=head,
            runtime_layout="source",
            session_id=session_id,
            invocation_id=invocation_id,
            freshness_deadline=130.0,
        )
        directory = repository / "dir"
        directory.mkdir()
        (directory / "file.txt").write_text(
            "descendant\n", encoding="utf-8"
        )
        (repository / ".gitattributes").write_text(
            "dir/file.txt filter=evil\n", encoding="utf-8"
        )
        marker = repository / "clean-filter-executed"
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "config",
                "filter.evil.clean",
                f"sh -c 'touch {marker}; cat'",
            ],
            check=True,
        )
        raw_inventory = bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id="inventory-stage-descendant-filter",
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            raw_inventory,
            expected_common_git_dir=common_dir,
            expected_invocation_id="inventory-stage-descendant-filter",
            clock=lambda: 100.0,
        )
        subject_digest = contract_digest({"paths": ("dir",)})
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-stage-descendant-filter",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-stage-descendant-filter",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=delivery_task_digest,
                subject_digest=subject_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=capability,
            task_digest=delivery_task_digest,
            session_id=session_id,
            repository_identity=repository,
            worktree_identity=repository,
            branch=branch,
            expected_head=head,
            subject_digest=subject_digest,
            scope_paths=("dir",),
            effect="local_write",
            operation_nonce="tool-stage-descendant-filter",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

        with self.assertRaisesRegex(ValueError, "E_GIT_EFFECT"):
            bridge.stage_allowlisted_paths(
                governing_runtime=runtime,
                task_context=task_context,
                inventory=inventory,
                lease=lease,
                authorization=authorization,
                paths=("dir/**",),
                expected_head=head,
                session_id=session_id,
                invocation_id=invocation_id,
                tool_use_id="tool-stage-descendant-filter",
                clock=lambda: 100.0,
            )
        self.assertFalse(marker.exists())
        self.assertFalse(authorization._consumed)

    def test_candidate_cannot_self_host_stage_commit_push_or_pr(self) -> None:
        import control_plane.host_bridge as bridge

        with self.assertRaisesRegex(TypeError, "host-bound"):
            bridge.GoverningRuntimeObservation()
        self.test_governing_git_effects_reject_serialized_authority()

    def test_feature_push_context_claim_is_atomic_before_first_egress(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge

        fixture = self._feature_push_fixture(
            task_id="TASK-FEATURE-PUSH-ATOMIC"
        )
        second = self._fresh_feature_push_bindings(
            fixture,
            context=fixture["context"],
            suffix="feature-push-atomic-second",
            tool_use_id="tool-feature-push-atomic-second",
        )
        bundles = [fixture["bindings"], second]
        push_started = threading.Event()
        release_first_push = threading.Event()
        push_calls: list[tuple[str, ...]] = []
        results: list[object] = []
        errors: list[Exception] = []

        def remote_executor(operation, arguments, max_output_bytes):
            del max_output_bytes
            if operation == "git_feature_push":
                push_calls.append(arguments)
                if len(push_calls) == 1:
                    push_started.set()
                    if not release_first_push.wait(timeout=5):
                        raise AssertionError("first push was not released")
                return 0, b""
            if operation == "git_feature_observe":
                return (
                    0,
                    (
                        f"{fixture['head']}\t"
                        f"refs/heads/{fixture['branch']}\n"
                    ).encode("utf-8"),
                )
            raise AssertionError(f"unexpected operation: {operation}")

        def execute(bundle) -> None:
            try:
                results.append(
                    bridge.push_validated_feature(
                        context=fixture["context"],
                        governing_runtime=bundle["runtime"],
                        governing_policy=bundle["policy"],
                        authorization=bundle["authorization"],
                        inventory=bundle["inventory"],
                        session_id=fixture["session_id"],
                        invocation_id=fixture["invocation_id"],
                        tool_use_id=bundle["tool_use_id"],
                        clock=lambda: 100.0,
                    )
                )
            except Exception as error:
                errors.append(error)

        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=remote_executor,
        ):
            first = threading.Thread(target=execute, args=(bundles[0],))
            second_thread = threading.Thread(
                target=execute, args=(bundles[1],)
            )
            first.start()
            self.assertTrue(push_started.wait(timeout=5))
            second_thread.start()
            second_thread.join(timeout=5)
            self.assertFalse(second_thread.is_alive())
            release_first_push.set()
            first.join(timeout=5)
            self.assertFalse(first.is_alive())

        self.assertEqual(len(push_calls), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIs(type(results[0]), bridge.LocalGitObservation)
        self.assertRegex(str(errors[0]), "E_REMOTE_EFFECT")
        for bundle in bundles:
            with self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT"):
                bridge.push_validated_feature(
                    context=fixture["context"],
                    governing_runtime=bundle["runtime"],
                    governing_policy=bundle["policy"],
                    authorization=bundle["authorization"],
                    inventory=bundle["inventory"],
                    session_id=fixture["session_id"],
                    invocation_id=fixture["invocation_id"],
                    tool_use_id=bundle["tool_use_id"],
                    clock=lambda: 100.0,
                )
        self.assertEqual(len(push_calls), 1)

    def test_feature_push_failures_are_terminal_or_observation_only(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge

        before = self._feature_push_fixture(
            task_id="TASK-FEATURE-PUSH-BEFORE-EFFECT"
        )
        original_inventory_consume = bridge._consume_worktree_inventory

        def drift_after_claim(*args, **kwargs):
            result = original_inventory_consume(*args, **kwargs)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(before["repository"]),
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/attacker/exfiltration.git",
                ],
                check=True,
            )
            return result

        with (
            patch.object(
                bridge,
                "_consume_worktree_inventory",
                side_effect=drift_after_claim,
            ),
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=AssertionError(
                    "pre-effect drift reached remote egress"
                ),
            ) as before_remote,
            self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT"),
        ):
            bridge.push_validated_feature(
                context=before["context"],
                governing_runtime=before["bindings"]["runtime"],
                governing_policy=before["bindings"]["policy"],
                authorization=before["bindings"]["authorization"],
                inventory=before["bindings"]["inventory"],
                session_id=before["session_id"],
                invocation_id=before["invocation_id"],
                tool_use_id=before["bindings"]["tool_use_id"],
                clock=lambda: 100.0,
            )
        before_remote.assert_not_called()
        self.assertTrue(before["context"]._consumed)
        with self.assertRaisesRegex(
            ValueError, "E_REMOTE_EFFECT_RECOVERY"
        ):
            bridge.recover_feature_push_outcome(
                before["context"], clock=lambda: 100.0
            )

        during = self._feature_push_fixture(
            task_id="TASK-FEATURE-PUSH-DURING-EFFECT"
        )
        during_calls: list[str] = []

        def fail_during_push(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            during_calls.append(operation)
            if operation == "git_feature_push":
                raise RuntimeError("transport outcome is unknown")
            if operation == "git_feature_observe":
                return (
                    0,
                    (
                        f"{during['head']}\t"
                        f"refs/heads/{during['branch']}\n"
                    ).encode("utf-8"),
                )
            raise AssertionError(f"unexpected operation: {operation}")

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=fail_during_push,
            ),
            self.assertRaisesRegex(
                ValueError, "E_REMOTE_EFFECT_OUTCOME_UNKNOWN"
            ),
        ):
            bridge.push_validated_feature(
                context=during["context"],
                governing_runtime=during["bindings"]["runtime"],
                governing_policy=during["bindings"]["policy"],
                authorization=during["bindings"]["authorization"],
                inventory=during["bindings"]["inventory"],
                session_id=during["session_id"],
                invocation_id=during["invocation_id"],
                tool_use_id=during["bindings"]["tool_use_id"],
                clock=lambda: 100.0,
            )
        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=fail_during_push,
        ):
            recovered = bridge.recover_feature_push_outcome(
                during["context"], clock=lambda: 100.0
            )
        self.assertEqual(
            during_calls,
            ["git_feature_push", "git_feature_observe"],
        )
        self.assertEqual(recovered.evidence["remote_head"], during["head"])
        with self.assertRaisesRegex(
            ValueError, "E_REMOTE_EFFECT_RECOVERY"
        ):
            bridge.recover_feature_push_outcome(
                during["context"], clock=lambda: 100.0
            )

        after = self._feature_push_fixture(
            task_id="TASK-FEATURE-PUSH-AFTER-EFFECT"
        )
        after_calls: list[str] = []

        def fail_after_push(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            after_calls.append(operation)
            if operation == "git_feature_push":
                return 0, b""
            if operation == "git_feature_observe":
                if after_calls.count("git_feature_observe") == 1:
                    raise RuntimeError("observation unavailable")
                return (
                    0,
                    (
                        f"{after['head']}\t"
                        f"refs/heads/{after['branch']}\n"
                    ).encode("utf-8"),
                )
            raise AssertionError(f"unexpected operation: {operation}")

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=fail_after_push,
            ),
            self.assertRaisesRegex(
                ValueError, "E_REMOTE_EFFECT_OUTCOME_UNKNOWN"
            ),
        ):
            bridge.push_validated_feature(
                context=after["context"],
                governing_runtime=after["bindings"]["runtime"],
                governing_policy=after["bindings"]["policy"],
                authorization=after["bindings"]["authorization"],
                inventory=after["bindings"]["inventory"],
                session_id=after["session_id"],
                invocation_id=after["invocation_id"],
                tool_use_id=after["bindings"]["tool_use_id"],
                clock=lambda: 100.0,
            )
        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=fail_after_push,
        ):
            recovered = bridge.recover_feature_push_outcome(
                after["context"], clock=lambda: 100.0
            )
        self.assertEqual(after_calls.count("git_feature_push"), 1)
        self.assertEqual(after_calls.count("git_feature_observe"), 2)
        self.assertEqual(recovered.evidence["remote_head"], after["head"])

    def test_feature_push_rejects_remote_repository_swap_after_framing(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
            native_session_event,
            native_user_interaction_event,
        )

        fixture = self._remote_effect_fixture(
            task_id="TASK-FEATURE-PUSH-REMOTE-SWAP",
            effect="remote_write",
            outcome="pull_request",
            branch="codex/feature-push-remote-swap",
        )
        context = bridge.validate_remote_effect_context(
            fixture["context"],
            expected_task_digest=fixture["task_digest"],
            expected_repo=fixture["repository"],
            expected_worktree=fixture["repository"],
            expected_branch=fixture["branch"],
            expected_head=fixture["head"],
            expected_session=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            expected_effect="remote_write",
            expected_pr_number=None,
            expected_base_sha=None,
            expected_checks_digest=None,
        )
        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(fixture["repository"].resolve()),
            target_worktree=str(fixture["repository"].resolve()),
            governing_base_commit=fixture["head"],
            runtime_layout="source",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        policy = fixture["governing_policy"]
        raw_inventory = bridge.observe_worktree_inventory(
            canonical_common_git_dir=fixture["common_dir"],
            invocation_id="inventory-feature-push-remote-swap",
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            raw_inventory,
            expected_common_git_dir=fixture["common_dir"],
            expected_invocation_id="inventory-feature-push-remote-swap",
            clock=lambda: 100.0,
        )
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-feature-push-remote-swap",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                observed_at_monotonic=100.0,
            ),
            expected_session_id=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-feature-push-remote-swap",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                task_digest=fixture["task_digest"],
                subject_digest=context.context_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=capability,
            task_digest=fixture["task_digest"],
            session_id=fixture["session_id"],
            repository_identity=fixture["repository"],
            worktree_identity=fixture["repository"],
            branch=fixture["branch"],
            expected_head=fixture["head"],
            subject_digest=context.context_digest,
            scope_paths=(".",),
            effect="remote_write",
            operation_nonce="tool-feature-push-remote-swap",
            invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(fixture["repository"]),
                "remote",
                "set-url",
                "origin",
                "https://github.com/attacker/exfiltration.git",
            ],
            check=True,
        )

        with patch.object(
            bridge, "_execute_native_remote", return_value=(1, b"")
        ) as remote_executor:
            with self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT"):
                bridge.push_validated_feature(
                    context=context,
                    governing_runtime=runtime,
                    governing_policy=policy,
                    authorization=authorization,
                    inventory=inventory,
                    session_id=fixture["session_id"],
                    invocation_id=fixture["invocation_id"],
                    tool_use_id="tool-feature-push-remote-swap",
                    clock=lambda: 100.0,
                )
        remote_executor.assert_not_called()
        self.assertFalse(authorization._consumed)

    def test_feature_push_and_pr_mutation_consume_distinct_closed_contexts(
        self,
    ) -> None:
        import json
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from tests.host_adapter_test_support import (
            governing_policy,
            governing_runtime_observation,
            native_github_provider_event,
            native_session_event,
            native_user_interaction_event,
        )

        push_fixture = self._remote_effect_fixture(
            task_id="TASK-FEATURE-PUSH",
            effect="remote_write",
            outcome="pull_request",
            branch="codex/feature-push",
        )
        push_context = bridge.validate_remote_effect_context(
            push_fixture["context"],
            expected_task_digest=push_fixture["task_digest"],
            expected_repo=push_fixture["repository"],
            expected_worktree=push_fixture["repository"],
            expected_branch=push_fixture["branch"],
            expected_head=push_fixture["head"],
            expected_session=push_fixture["session_id"],
            expected_invocation_id=push_fixture["invocation_id"],
            expected_effect="remote_write",
            expected_pr_number=None,
            expected_base_sha=None,
            expected_checks_digest=None,
        )
        push_runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(push_fixture["repository"].resolve()),
            target_worktree=str(push_fixture["repository"].resolve()),
            governing_base_commit=push_fixture["head"],
            runtime_layout="source",
            session_id=push_fixture["session_id"],
            invocation_id=push_fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        push_policy = push_fixture["governing_policy"]
        raw_push_inventory = bridge.observe_worktree_inventory(
            canonical_common_git_dir=push_fixture["common_dir"],
            invocation_id="inventory-feature-push",
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        push_inventory = bridge.validate_worktree_inventory_observation(
            raw_push_inventory,
            expected_common_git_dir=push_fixture["common_dir"],
            expected_invocation_id="inventory-feature-push",
            clock=lambda: 100.0,
        )
        push_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-feature-push",
                session_id=push_fixture["session_id"],
                invocation_id=push_fixture["invocation_id"],
                observed_at_monotonic=100.0,
            ),
            expected_session_id=push_fixture["session_id"],
            expected_invocation_id=push_fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        push_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-feature-push",
                session_id=push_fixture["session_id"],
                invocation_id=push_fixture["invocation_id"],
                task_digest=push_fixture["task_digest"],
                subject_digest=push_context.context_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=push_capability,
            task_digest=push_fixture["task_digest"],
            session_id=push_fixture["session_id"],
            repository_identity=push_fixture["repository"],
            worktree_identity=push_fixture["repository"],
            branch=push_fixture["branch"],
            expected_head=push_fixture["head"],
            subject_digest=push_context.context_digest,
            scope_paths=(".",),
            effect="remote_write",
            operation_nonce="tool-feature-push",
            invocation_id=push_fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

        forged_push_runtime = object.__new__(type(push_runtime))
        for name in type(push_runtime).__slots__:
            setattr(
                forged_push_runtime, name, getattr(push_runtime, name)
            )
        with patch.object(
            bridge,
            "_execute_native_remote",
            side_effect=AssertionError(
                "unissued runtime reached the remote executor"
            ),
        ):
            with self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT"):
                bridge.push_validated_feature(
                    context=push_context,
                    governing_runtime=forged_push_runtime,
                    governing_policy=push_policy,
                    authorization=push_authorization,
                    inventory=push_inventory,
                    session_id=push_fixture["session_id"],
                    invocation_id=push_fixture["invocation_id"],
                    tool_use_id="tool-feature-push",
                    clock=lambda: 100.0,
                )
        forged_push_policy = object.__new__(type(push_policy))
        for name in type(push_policy).__slots__:
            setattr(forged_push_policy, name, getattr(push_policy, name))
        with patch.object(
            bridge,
            "_execute_native_remote",
            side_effect=AssertionError(
                "unissued policy reached the remote executor"
            ),
        ):
            with self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT"):
                bridge.push_validated_feature(
                    context=push_context,
                    governing_runtime=push_runtime,
                    governing_policy=forged_push_policy,
                    authorization=push_authorization,
                    inventory=push_inventory,
                    session_id=push_fixture["session_id"],
                    invocation_id=push_fixture["invocation_id"],
                    tool_use_id="tool-feature-push",
                    clock=lambda: 100.0,
                )
        self.assertFalse(push_authorization._consumed)
        subprocess.run(
            [
                "git",
                "-C",
                str(push_fixture["repository"]),
                "config",
                "--local",
                "http.sslVerify",
                "false",
            ],
            check=True,
        )
        with self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT"):
            bridge.push_validated_feature(
                context=push_context,
                governing_runtime=push_runtime,
                governing_policy=push_policy,
                authorization=push_authorization,
                inventory=push_inventory,
                session_id=push_fixture["session_id"],
                invocation_id=push_fixture["invocation_id"],
                tool_use_id="tool-feature-push",
                clock=lambda: 100.0,
            )
        self.assertFalse(push_authorization._consumed)
        subprocess.run(
            [
                "git",
                "-C",
                str(push_fixture["repository"]),
                "config",
                "--local",
                "--unset",
                "http.sslVerify",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(push_fixture["repository"]),
                "config",
                "extensions.worktreeConfig",
                "true",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(push_fixture["repository"]),
                "config",
                "--worktree",
                "http.sslVerify",
                "false",
            ],
            check=True,
        )
        with self.assertRaisesRegex(ValueError, "E_REMOTE_EFFECT"):
            bridge.push_validated_feature(
                context=push_context,
                governing_runtime=push_runtime,
                governing_policy=push_policy,
                authorization=push_authorization,
                inventory=push_inventory,
                session_id=push_fixture["session_id"],
                invocation_id=push_fixture["invocation_id"],
                tool_use_id="tool-feature-push",
                clock=lambda: 100.0,
            )
        self.assertFalse(push_authorization._consumed)
        subprocess.run(
            [
                "git",
                "-C",
                str(push_fixture["repository"]),
                "config",
                "--worktree",
                "--unset",
                "http.sslVerify",
            ],
            check=True,
        )

        git_effect_argv: list[list[str]] = []

        def git_push_side_effect(arguments, **kwargs):
            git_effect_argv.append(list(arguments))
            if (
                "config" in arguments
                and "--name-only" in arguments
            ):
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=b"remote.origin.url\0",
                    stderr=b"",
                )
            if "get-url" in arguments:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=(
                        "https://github.com/example/control-plane.git\n"
                    ),
                    stderr="",
                )
            if "ls-remote" in arguments:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=(
                        f"{push_fixture['head']}\t"
                        f"refs/heads/{push_fixture['branch']}\n"
                    ),
                    stderr="",
                )
            if arguments[-3:] == [
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
            ]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=str(push_fixture["repository"] / ".git") + "\n",
                    stderr="",
                )
            if arguments[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=push_fixture["head"] + "\n",
                    stderr="",
                )
            if arguments[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=push_fixture["branch"] + "\n",
                    stderr="",
                )
            if arguments[-2:] == ["ls-files", "-z"]:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=b"", stderr=b""
                )
            if "status" in arguments:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout="", stderr=""
                )
            if "push" in arguments:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=b"", stderr=b""
                )
            raise AssertionError(f"unexpected Git argv: {arguments}")

        with patch(
            "control_plane.host_bridge.subprocess.run",
            side_effect=git_push_side_effect,
        ):
            pushed = bridge.push_validated_feature(
                context=push_context,
                governing_runtime=push_runtime,
                governing_policy=push_policy,
                authorization=push_authorization,
                inventory=push_inventory,
                session_id=push_fixture["session_id"],
                invocation_id=push_fixture["invocation_id"],
                tool_use_id="tool-feature-push",
                clock=lambda: 100.0,
            )
        self.assertEqual(pushed.evidence["remote_head"], push_fixture["head"])
        self.assertTrue(
            any("get-url" in arguments for arguments in git_effect_argv)
        )
        self.assertTrue(
            all(
                "core.hooksPath=/dev/null" in arguments
                for arguments in git_effect_argv
                if "push" in arguments or "ls-remote" in arguments
            )
        )

        pr_fixture = self._remote_effect_fixture(
            task_id="TASK-FEATURE-PR",
            effect="pull_request",
            outcome="pull_request",
            branch="codex/feature-pr",
            expected_base_sha="a" * 40,
        )
        pr_context = bridge.validate_remote_effect_context(
            pr_fixture["context"],
            expected_task_digest=pr_fixture["task_digest"],
            expected_repo=pr_fixture["repository"],
            expected_worktree=pr_fixture["repository"],
            expected_branch=pr_fixture["branch"],
            expected_head=pr_fixture["head"],
            expected_session=pr_fixture["session_id"],
            expected_invocation_id=pr_fixture["invocation_id"],
            expected_effect="pull_request",
            expected_pr_number=None,
            expected_base_sha="a" * 40,
            expected_checks_digest=None,
        )
        pr_runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(pr_fixture["repository"].resolve()),
            target_worktree=str(pr_fixture["repository"].resolve()),
            governing_base_commit=pr_fixture["head"],
            runtime_layout="source",
            session_id=pr_fixture["session_id"],
            invocation_id=pr_fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        pr_policy = governing_policy(
            policy={
                "git": {
                    "remote": "origin",
                    "base_branch": "main",
                }
            },
            policy_digest=self.digest,
            runtime_digest=self.digest,
            lock_digest=self.digest,
            governing_base_commit=pr_fixture["head"],
            session_id=pr_fixture["session_id"],
            invocation_id=pr_fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        native_provider = native_github_provider_event(
            event_id="provider-feature-pr",
            repository="example/control-plane",
            session_id=pr_fixture["session_id"],
            invocation_id=pr_fixture["invocation_id"],
        )
        def feature_pr_provider(arguments, **kwargs):
            if arguments[-4:] == [
                "remote",
                "get-url",
                "--push",
                "origin",
            ]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=(
                        "https://github.com/example/control-plane.git\n"
                    ),
                )
            if arguments[1:3] == ["auth", "status"]:
                return subprocess.CompletedProcess(arguments, 0)
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=b'{"nameWithOwner":"example/control-plane"}',
            )

        with patch(
            "control_plane.host_bridge.subprocess.run",
            side_effect=feature_pr_provider,
        ):
            provider = bridge.approve_github_pr_write_provider(
                native_provider,
                governing_runtime=pr_runtime,
                governing_policy=pr_policy,
                expected_repository="example/control-plane",
                session_id=pr_fixture["session_id"],
                invocation_id=pr_fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        title = bridge.validate_pull_request_title("Control plane stabilization")
        body = bridge.validate_pull_request_body(
            "Implements the bounded stabilization milestone."
        )
        pr_subject = contract_digest(
            {
                "context": pr_context.context_digest,
                "title": title.digest,
                "body": body.digest,
                "draft": True,
                "expected_pr_number": None,
            }
        )
        pr_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-feature-pr",
                session_id=pr_fixture["session_id"],
                invocation_id=pr_fixture["invocation_id"],
                observed_at_monotonic=100.0,
            ),
            expected_session_id=pr_fixture["session_id"],
            expected_invocation_id=pr_fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        pr_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-feature-pr",
                session_id=pr_fixture["session_id"],
                invocation_id=pr_fixture["invocation_id"],
                task_digest=pr_fixture["task_digest"],
                subject_digest=pr_subject,
                observed_at_monotonic=100.0,
            ),
            host_capability=pr_capability,
            task_digest=pr_fixture["task_digest"],
            session_id=pr_fixture["session_id"],
            repository_identity=pr_fixture["repository"],
            worktree_identity=pr_fixture["repository"],
            branch=pr_fixture["branch"],
            expected_head=pr_fixture["head"],
            subject_digest=pr_subject,
            scope_paths=(".",),
            effect="pull_request",
            operation_nonce="tool-feature-pr",
            invocation_id=pr_fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        def build_pr_request():
            return bridge.build_pull_request_mutation_request(
                context=pr_context,
                provider=provider,
                authorization=pr_authorization,
                title=title,
                body=body,
                draft=True,
                expected_pr_number=None,
                session_id=pr_fixture["session_id"],
                invocation_id=pr_fixture["invocation_id"],
                tool_use_id="tool-feature-pr",
                clock=lambda: 100.0,
            )

        drift = pr_fixture["repository"] / "late-drift.txt"
        drift.write_text("drift\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "E_PR_MUTATION"):
            build_pr_request()
        drift.unlink()
        request = build_pr_request()

        pr_payload = {
            "number": 9,
            "url": "https://github.com/example/control-plane/pull/9",
            "isDraft": True,
            "baseRefName": "main",
            "headRefName": pr_fixture["branch"],
            "headRefOid": pr_fixture["head"],
        }

        def gh_side_effect(arguments, **kwargs):
            if arguments[-3:] == [
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
            ]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=str(pr_fixture["repository"] / ".git") + "\n",
                )
            if arguments[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=pr_fixture["head"] + "\n"
                )
            if arguments[-2:] == ["branch", "--show-current"]:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=pr_fixture["branch"] + "\n"
                )
            if arguments[-2:] == ["ls-files", "-z"]:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=b"", stderr=b""
                )
            if "status" in arguments:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout="", stderr=""
                )
            if arguments[-4:] == [
                "remote",
                "get-url",
                "--push",
                "origin",
            ]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=(
                        "https://github.com/example/control-plane.git\n"
                    ),
                )
            if arguments[1:3] == ["pr", "list"]:
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=b"[]"
                )
            if (
                arguments[1] == "api"
                and "/git/ref/heads/" in arguments[2]
            ):
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=json.dumps(
                        {"object": {"sha": "a" * 40}}
                    ).encode("utf-8"),
                )
            if (
                arguments[1] == "api"
                and "/check-runs?per_page=100" in arguments[2]
            ):
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=b'{"total_count":0,"check_runs":[]}',
                )
            if arguments[1:3] == ["pr", "create"]:
                return subprocess.CompletedProcess(arguments, 0)
            if arguments[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=json.dumps(pr_payload).encode("utf-8"),
                )
            raise AssertionError(f"unexpected gh argv: {arguments}")

        with patch(
            "control_plane.host_bridge.subprocess.run",
            side_effect=gh_side_effect,
        ):
            observed = bridge.execute_pull_request_mutation(
                request, clock=lambda: 100.0
            )
        observed.number = 7
        with self.assertRaisesRegex(ValueError, "E_PR_MUTATION"):
            bridge.validate_pull_request_mutation(
                observed,
                expected_repository="example/control-plane",
                expected_base="main",
                expected_head_branch=pr_fixture["branch"],
                expected_head_sha=pr_fixture["head"],
                expected_pr_number=7,
                expected_draft=True,
                expected_session_id=pr_fixture["session_id"],
                expected_invocation_id=pr_fixture["invocation_id"],
                clock=lambda: 100.0,
            )
        observed.number = 9
        forged_observation = object.__new__(
            bridge.PullRequestMutationObservation
        )
        for name in bridge.PullRequestMutationObservation.__slots__:
            setattr(forged_observation, name, getattr(observed, name))
        with self.assertRaisesRegex(ValueError, "E_PR_MUTATION"):
            bridge.validate_pull_request_mutation(
                forged_observation,
                expected_repository="example/control-plane",
                expected_base="main",
                expected_head_branch=pr_fixture["branch"],
                expected_head_sha=pr_fixture["head"],
                expected_pr_number=9,
                expected_draft=True,
                expected_session_id=pr_fixture["session_id"],
                expected_invocation_id=pr_fixture["invocation_id"],
                clock=lambda: 100.0,
            )
        validated_pr = bridge.validate_pull_request_mutation(
            observed,
            expected_repository="example/control-plane",
            expected_base="main",
            expected_head_branch=pr_fixture["branch"],
            expected_head_sha=pr_fixture["head"],
            expected_pr_number=9,
            expected_draft=True,
            expected_session_id=pr_fixture["session_id"],
            expected_invocation_id=pr_fixture["invocation_id"],
            clock=lambda: 100.0,
        )
        self.assertEqual(validated_pr.number, 9)
        validated_pr.number = 7
        self.assertFalse(
            bridge._runtime_host_object_is_live(
                validated_pr,
                "validated_pull_request_mutation_observation",
            )
        )
        self.assertTrue(push_context._consumed)
        self.assertTrue(pr_context._consumed)
        self.assertNotEqual(
            push_fixture["task_digest"], pr_fixture["task_digest"]
        )

    def test_pr_write_provider_is_pre_authenticated_host_bound_and_secret_free(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import (
            governing_policy,
            governing_runtime_observation,
            native_github_provider_event,
        )

        fixture = self._remote_effect_fixture(
            task_id="TASK-PR-PROVIDER",
            effect="pull_request",
            outcome="pull_request",
            branch="codex/pr-provider",
            expected_base_sha="a" * 40,
        )
        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(fixture["repository"].resolve()),
            target_worktree=str(fixture["repository"].resolve()),
            governing_base_commit=fixture["head"],
            runtime_layout="source",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        policy = governing_policy(
            policy={
                "git": {
                    "remote": "origin",
                    "base_branch": "main",
                }
            },
            policy_digest=self.digest,
            runtime_digest=self.digest,
            lock_digest=self.digest,
            governing_base_commit=fixture["head"],
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        with self.assertRaisesRegex(
            ValueError, "E_GITHUB_PR_PROVIDER"
        ):
            bridge.approve_github_pr_write_provider(
                {"repository": "owner/repository"},
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository="owner/repository",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        event = native_github_provider_event(
            event_id="provider-secret-free",
            repository="owner/repository",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
        )
        with (
            patch(
                "control_plane.host_bridge.subprocess.run"
            ) as mismatched_repository_executor,
            self.assertRaisesRegex(ValueError, "E_GITHUB_PR_PROVIDER"),
        ):
            bridge.approve_github_pr_write_provider(
                event,
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository="owner/repository",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        mismatched_repository_executor.assert_not_called()
        self.assertFalse(event._consumed)
        self.assertFalse(runtime._consumed)
        self.assertFalse(policy._consumed)

        event = native_github_provider_event(
            event_id="provider-secret-free-canonical",
            repository="example/control-plane",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(fixture["repository"]),
                "remote",
                "set-url",
                "origin",
                "https://github.com/attacker/exfiltration.git",
            ],
            check=True,
        )
        early_provider_calls: list[str] = []

        def early_drift_provider(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            early_provider_calls.append(operation)
            if operation == "github_auth_status":
                return 0, b""
            return 0, b'{"nameWithOwner":"example/control-plane"}'

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=early_drift_provider,
            ),
            self.assertRaisesRegex(ValueError, "E_GITHUB_PR_PROVIDER"),
        ):
            bridge.approve_github_pr_write_provider(
                event,
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository="example/control-plane",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        self.assertEqual(early_provider_calls, [])
        self.assertFalse(event._consumed)
        self.assertFalse(runtime._consumed)
        self.assertFalse(policy._consumed)
        subprocess.run(
            [
                "git",
                "-C",
                str(fixture["repository"]),
                "remote",
                "set-url",
                "origin",
                "https://github.com/example/control-plane.git",
            ],
            check=True,
        )

        late_provider_calls: list[str] = []

        def late_drift_provider(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            late_provider_calls.append(operation)
            if operation == "github_auth_status":
                return 0, b""
            if operation == "github_repository_access":
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(fixture["repository"]),
                        "remote",
                        "set-url",
                        "origin",
                        "https://github.com/attacker/exfiltration.git",
                    ],
                    check=True,
                )
                return (
                    0,
                    b'{"nameWithOwner":"example/control-plane"}',
                )
            raise AssertionError(f"unexpected operation: {operation}")

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=late_drift_provider,
            ),
            self.assertRaisesRegex(ValueError, "E_GITHUB_PR_PROVIDER"),
        ):
            bridge.approve_github_pr_write_provider(
                event,
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository="example/control-plane",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        self.assertEqual(
            late_provider_calls,
            ["github_auth_status", "github_repository_access"],
        )
        self.assertFalse(event._consumed)
        self.assertFalse(runtime._consumed)
        self.assertFalse(policy._consumed)
        subprocess.run(
            [
                "git",
                "-C",
                str(fixture["repository"]),
                "remote",
                "set-url",
                "origin",
                "https://github.com/example/control-plane.git",
            ],
            check=True,
        )

        def ready_provider(arguments, **kwargs):
            if arguments[-4:] == [
                "remote",
                "get-url",
                "--push",
                "origin",
            ]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=(
                        "https://github.com/example/control-plane.git\n"
                    ),
                )
            if arguments[1:3] == ["auth", "status"]:
                return subprocess.CompletedProcess(arguments, 0)
            if arguments[1:3] == ["repo", "view"]:
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    stdout=b'{"nameWithOwner":"example/control-plane"}',
                )
            raise AssertionError(f"unexpected provider argv: {arguments}")

        with (
            patch.dict(
                "os.environ",
                {
                    "GH_TOKEN": "canary-secret",
                    "CLOUD_SECRET": "canary-secret",
                },
                clear=False,
            ),
            patch(
                "control_plane.host_bridge.subprocess.run",
                side_effect=ready_provider,
            ) as provider_doctor,
        ):
            provider = bridge.approve_github_pr_write_provider(
                event,
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository="example/control-plane",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        github_calls = [
            call
            for call in provider_doctor.call_args_list
            if call.args[0][0] == "gh"
        ]
        self.assertEqual(len(github_calls), 2)
        for call in provider_doctor.call_args_list:
            environment = call.kwargs["env"]
            self.assertNotIn("GH_TOKEN", environment)
            self.assertNotIn("CLOUD_SECRET", environment)
            self.assertNotIn("canary-secret", environment.values())
        self.assertEqual(provider.repository, "example/control-plane")

        wrong_repository_event = native_github_provider_event(
            event_id="provider-wrong-repository",
            repository="example/control-plane",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
        )

        def wrong_repository_provider(arguments, **kwargs):
            if arguments[1:3] == ["auth", "status"]:
                return subprocess.CompletedProcess(arguments, 0)
            return subprocess.CompletedProcess(
                arguments,
                0,
                stdout=b'{"nameWithOwner":"other/repository"}',
            )

        with (
            patch(
                "control_plane.host_bridge.subprocess.run",
                side_effect=wrong_repository_provider,
            ),
            self.assertRaisesRegex(ValueError, "E_GITHUB_PR_PROVIDER"),
        ):
            bridge.approve_github_pr_write_provider(
                wrong_repository_event,
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository="example/control-plane",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        with self.assertRaisesRegex(ValueError, "secret-like"):
            bridge.validate_pull_request_body("token ghp_" + "x" * 40)

    def test_pr_provider_type_gate_precedes_canonical_attribute_access(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
            native_github_provider_event,
        )

        fixture = self._remote_effect_fixture(
            task_id="TASK-PR-PROVIDER-TYPE-GATE",
            effect="pull_request",
            outcome="pull_request",
            branch="codex/pr-provider-type-gate",
            expected_base_sha="a" * 40,
        )
        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(fixture["repository"].resolve()),
            target_worktree=str(fixture["repository"].resolve()),
            governing_base_commit=fixture["head"],
            runtime_layout="source",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )

        class ForeignObject:
            def __getattribute__(self, name):
                raise AssertionError(
                    f"foreign attribute accessed before type gate: {name}"
                )

        with self.assertRaisesRegex(
            ValueError, "E_GITHUB_PR_PROVIDER"
        ):
            bridge.approve_github_pr_write_provider(
                ForeignObject(),
                governing_runtime=runtime,
                governing_policy=fixture["governing_policy"],
                expected_repository="example/control-plane",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

        event = native_github_provider_event(
            event_id="provider-type-gate",
            repository="example/control-plane",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
        )
        with self.assertRaisesRegex(
            ValueError, "E_GITHUB_PR_PROVIDER"
        ):
            bridge.approve_github_pr_write_provider(
                event,
                governing_runtime=runtime,
                governing_policy=ForeignObject(),
                expected_repository="example/control-plane",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

    def test_github_repository_identity_accepts_real_casing_and_stays_exact(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import (
            governing_policy,
            governing_runtime_observation,
            native_github_provider_event,
        )

        fixture = self._remote_effect_fixture(
            task_id="TASK-PR-REPOSITORY-CASING",
            effect="pull_request",
            outcome="pull_request",
            branch="codex/pr-repository-casing",
            expected_base_sha="a" * 40,
        )
        canonical_repository = (
            "andreabusta/codex-engineering-control-plane"
        )
        raw_repository = "AndreaBusta/codex-engineering-control-plane"
        subprocess.run(
            [
                "git",
                "-C",
                str(fixture["repository"]),
                "remote",
                "set-url",
                "origin",
                (
                    "https://github.com/AndreaBusta/"
                    "codex-engineering-control-plane.git"
                ),
            ],
            check=True,
        )
        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=self.digest,
            attestor_worktree=str(fixture["repository"].resolve()),
            target_worktree=str(fixture["repository"].resolve()),
            governing_base_commit=fixture["head"],
            runtime_layout="source",
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        policy = governing_policy(
            policy={
                "git": {
                    "remote": "origin",
                    "base_branch": "main",
                }
            },
            policy_digest=self.digest,
            runtime_digest=self.digest,
            lock_digest=self.digest,
            governing_base_commit=fixture["head"],
            remote_repository=canonical_repository,
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            freshness_deadline=130.0,
        )
        event = native_github_provider_event(
            event_id="provider-real-casing",
            repository=raw_repository,
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
        )

        def provider_preflight(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            if operation == "github_auth_status":
                return 0, b""
            if operation == "github_repository_access":
                return (
                    0,
                    b'{"nameWithOwner":"AndreaBusta/'
                    b'codex-engineering-control-plane"}',
                )
            raise AssertionError(
                f"unexpected provider operation: {operation}"
            )

        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=provider_preflight,
        ):
            provider = bridge.approve_github_pr_write_provider(
                event,
                governing_runtime=runtime,
                governing_policy=policy,
                expected_repository=canonical_repository,
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        self.assertEqual(provider.repository, canonical_repository)

        bindings = bridge._PullRequestMutationEffectBindings(
            repository=canonical_repository,
            base_branch="main",
            branch=fixture["branch"],
            head=fixture["head"],
            remote_repository=canonical_repository,
            remote_name="origin",
            expected_base_sha="a" * 40,
            expected_checks_digest=None,
            expected_pr_number=4,
            title="Bounded casing regression",
            body="Provider URL identity remains exact.",
            draft=True,
            session_id=fixture["session_id"],
            invocation_id=fixture["invocation_id"],
            provider_freshness_deadline=130.0,
        )
        raw_url = (
            "https://github.com/AndreaBusta/"
            "codex-engineering-control-plane/pull/4"
        )

        def observed_pr(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            if operation == "github_pull_request_observe":
                return (
                    0,
                    (
                        '{"number":4,"url":"'
                        + raw_url
                        + '","isDraft":true,"baseRefName":"main",'
                        '"headRefName":"'
                        + fixture["branch"]
                        + '","headRefOid":"'
                        + fixture["head"]
                        + '"}'
                    ).encode("utf-8"),
                )
            raise AssertionError(
                f"unexpected provider operation: {operation}"
            )

        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=observed_pr,
        ):
            observation = bridge._observe_pull_request_mutation(
                bindings, clock=lambda: 100.0
            )
        self.assertEqual(observation.repository, canonical_repository)
        self.assertEqual(observation.url, raw_url)

        def wrong_repository_pr(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            if operation == "github_pull_request_observe":
                return (
                    0,
                    (
                        '{"number":4,"url":"https://github.com/'
                        'AndreaBusta/other-repository/pull/4",'
                        '"isDraft":true,"baseRefName":"main",'
                        '"headRefName":"'
                        + fixture["branch"]
                        + '","headRefOid":"'
                        + fixture["head"]
                        + '"}'
                    ).encode("utf-8"),
                )
            raise AssertionError(
                f"unexpected provider operation: {operation}"
            )

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=wrong_repository_pr,
            ),
            self.assertRaisesRegex(ValueError, "E_PR_MUTATION"),
        ):
            bridge._observe_pull_request_mutation(
                bindings, clock=lambda: 100.0
            )

    def test_pr_mutation_revalidates_remote_before_egress_and_effect(
        self,
    ) -> None:
        import json
        import control_plane.host_bridge as bridge

        framed = self._pr_mutation_fixture(
            task_id="TASK-PR-REMOTE-BEFORE-EGRESS",
            expected_pr_number=None,
            expected_base_sha="a" * 40,
            expected_checks_digest=None,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(framed["repository"]),
                "remote",
                "set-url",
                "origin",
                "https://github.com/attacker/exfiltration.git",
            ],
            check=True,
        )
        provider_calls: list[str] = []

        def forbidden_provider(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            provider_calls.append(operation)
            return 0, b"[]"

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=forbidden_provider,
            ),
            self.assertRaisesRegex(ValueError, "E_PR_MUTATION"),
        ):
            bridge.execute_pull_request_mutation(
                framed["request"], clock=lambda: 100.0
            )
        self.assertEqual(provider_calls, [])

        late = self._pr_mutation_fixture(
            task_id="TASK-PR-REMOTE-BEFORE-EFFECT",
            expected_pr_number=None,
            expected_base_sha="a" * 40,
            expected_checks_digest=None,
        )
        late_calls: list[str] = []

        def late_drift_provider(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            late_calls.append(operation)
            if operation == "github_pull_request_precondition_pr":
                return 0, b"[]"
            if operation == "github_pull_request_precondition_base":
                return (
                    0,
                    json.dumps(
                        {"object": {"sha": "a" * 40}}
                    ).encode("utf-8"),
                )
            if operation == "github_pull_request_precondition_checks":
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(late["repository"]),
                        "remote",
                        "set-url",
                        "origin",
                        "https://github.com/attacker/exfiltration.git",
                    ],
                    check=True,
                )
                return 0, b'{"total_count":0,"check_runs":[]}'
            if operation == "github_pull_request_mutation":
                return 0, b""
            if operation == "github_pull_request_observe":
                return (
                    0,
                    json.dumps(
                        {
                            "number": 7,
                            "url": (
                                "https://github.com/"
                                "example/control-plane/pull/7"
                            ),
                            "isDraft": True,
                            "baseRefName": "main",
                            "headRefName": late["branch"],
                            "headRefOid": late["head"],
                        }
                    ).encode("utf-8"),
                )
            raise AssertionError(f"unexpected operation: {operation}")

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=late_drift_provider,
            ),
            self.assertRaisesRegex(ValueError, "E_PR_MUTATION"),
        ):
            bridge.execute_pull_request_mutation(
                late["request"], clock=lambda: 100.0
            )
        self.assertNotIn("github_pull_request_mutation", late_calls)

    def test_pr_mutation_rejects_provider_tampering_before_egress(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge

        fixture = self._pr_mutation_fixture(
            task_id="TASK-PR-PROVIDER-TAMPER",
            expected_pr_number=None,
            expected_base_sha="a" * 40,
            expected_checks_digest=None,
        )
        fixture["provider"].repository = "attacker/exfiltration"
        provider_calls: list[str] = []

        def forbidden_provider(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            provider_calls.append(operation)
            return 0, b"[]"

        with (
            patch.object(
                bridge,
                "_native_host_remote_executor",
                side_effect=forbidden_provider,
            ),
            self.assertRaisesRegex(ValueError, "E_PR_MUTATION"),
        ):
            bridge.execute_pull_request_mutation(
                fixture["request"], clock=lambda: 100.0
            )
        self.assertEqual(provider_calls, [])

    def test_host_authorization_consumption_is_atomic(self) -> None:
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import (
            native_session_event,
            native_user_interaction_event,
        )

        fixture = self._remote_effect_fixture(
            task_id="TASK-AUTHORIZATION-CAS",
            effect="remote_write",
            outcome="pull_request",
            branch="codex/authorization-cas",
        )
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-authorization-cas",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                observed_at_monotonic=100.0,
            ),
            expected_session_id=fixture["session_id"],
            expected_invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="authorize-authorization-cas",
                session_id=fixture["session_id"],
                invocation_id=fixture["invocation_id"],
                task_digest=fixture["task_digest"],
                subject_digest=fixture["context"].context_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=capability,
            task_digest=fixture["task_digest"],
            session_id=fixture["session_id"],
            repository_identity=fixture["repository"],
            worktree_identity=fixture["repository"],
            branch=fixture["branch"],
            expected_head=fixture["head"],
            subject_digest=fixture["context"].context_digest,
            scope_paths=(".",),
            effect="remote_write",
            operation_nonce="tool-authorization-cas",
            invocation_id=fixture["invocation_id"],
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        consume_cells = dict(
            zip(
                bridge._consume_runtime_host_object.__code__.co_freevars,
                bridge._consume_runtime_host_object.__closure__,
                strict=True,
            )
        )
        registry_cell = consume_cells["issued"]
        original_registry = registry_cell.cell_contents
        pop_barrier = threading.Barrier(2)

        class BarrierPopDict(dict):
            def pop(self, key, default=None):
                try:
                    pop_barrier.wait(timeout=0.25)
                except threading.BrokenBarrierError:
                    pass
                return super().pop(key, default)

        gated_registry = BarrierPopDict(original_registry)
        registry_cell.cell_contents = gated_registry
        successes: list[str] = []
        errors: list[str] = []

        def consume_once() -> None:
            try:
                result = bridge.consume_authorization(
                    authorization,
                    expected_task_digest=fixture["task_digest"],
                    expected_session_id=fixture["session_id"],
                    expected_repository_identity=fixture["repository"],
                    expected_worktree_identity=fixture["repository"],
                    expected_branch=fixture["branch"],
                    expected_head=fixture["head"],
                    expected_subject_digest=fixture["context"].context_digest,
                    expected_scope_paths=(".",),
                    expected_effect="remote_write",
                    expected_operation_nonce="tool-authorization-cas",
                    expected_invocation_id=fixture["invocation_id"],
                    clock=lambda: 100.0,
                )
                successes.append(result.authorization_id)
            except ValueError as error:
                errors.append(str(error))

        workers = [
            threading.Thread(target=consume_once, daemon=True)
            for _ in range(2)
        ]
        try:
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=2)
        finally:
            original_registry.clear()
            original_registry.update(gated_registry)
            registry_cell.cell_contents = original_registry
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 1)

    def test_pr_mutation_reobserves_live_pr_base_and_checks_before_effect(
        self,
    ) -> None:
        import json
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        expected_base_sha = "a" * 40
        for drift in ("pr", "base", "checks"):
            with self.subTest(drift=drift):
                check = {
                    "id": 17,
                    "name": "verify",
                    "status": "completed",
                    "conclusion": "success",
                    "app_slug": "github-actions",
                }
                checks_digest = contract_digest((check,))
                fixture = self._pr_mutation_fixture(
                    task_id=f"TASK-PR-LIVE-{drift.upper()}",
                    expected_pr_number=7,
                    expected_base_sha=expected_base_sha,
                    expected_checks_digest=checks_digest,
                )
                live_pr = {
                    "number": 7,
                    "baseRefName": "main",
                    "headRefName": fixture["branch"],
                    "headRefOid": fixture["head"],
                }
                live_base = {"object": {"sha": expected_base_sha}}
                live_check = {
                    **check,
                    "head_sha": fixture["head"],
                }
                if drift == "pr":
                    live_pr["headRefOid"] = "f" * 40
                elif drift == "base":
                    live_base["object"]["sha"] = "b" * 40
                else:
                    live_check["conclusion"] = "failure"
                calls: list[str] = []

                def provider(operation, arguments, max_output_bytes):
                    del arguments, max_output_bytes
                    calls.append(operation)
                    if operation == "github_pull_request_precondition_pr":
                        return 0, json.dumps(live_pr).encode("utf-8")
                    if operation == "github_pull_request_precondition_base":
                        return 0, json.dumps(live_base).encode("utf-8")
                    if operation == "github_pull_request_precondition_checks":
                        payload = {
                            "total_count": 1,
                            "check_runs": [
                                {
                                    key: value
                                    for key, value in live_check.items()
                                    if key != "app_slug"
                                }
                                | {
                                    "app": {
                                        "slug": live_check["app_slug"]
                                    }
                                }
                            ],
                        }
                        return 0, json.dumps(payload).encode("utf-8")
                    if operation == "github_pull_request_mutation":
                        return 0, b""
                    if operation == "github_pull_request_observe":
                        payload = {
                            **live_pr,
                            "url": (
                                "https://github.com/example/control-plane/pull/7"
                            ),
                            "isDraft": True,
                        }
                        return 0, json.dumps(payload).encode("utf-8")
                    raise AssertionError(
                        f"unexpected provider operation: {operation}"
                    )

                with (
                    patch.object(
                        bridge,
                        "_native_host_remote_executor",
                        side_effect=provider,
                    ),
                    self.assertRaisesRegex(ValueError, "E_PR_MUTATION"),
                ):
                    bridge.execute_pull_request_mutation(
                        fixture["request"], clock=lambda: 100.0
                    )
                self.assertNotIn(
                    "github_pull_request_mutation", calls
                )

    def test_pr_mutation_claim_is_atomic_before_remote_effect(self) -> None:
        import json
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        expected_base_sha = "a" * 40
        check = {
            "id": 17,
            "name": "verify",
            "status": "completed",
            "conclusion": "success",
            "app_slug": "github-actions",
        }
        fixture = self._pr_mutation_fixture(
            task_id="TASK-PR-ATOMIC-CLAIM",
            expected_pr_number=7,
            expected_base_sha=expected_base_sha,
            expected_checks_digest=contract_digest((check,)),
        )
        first_effect_entered = threading.Event()
        release_first_effect = threading.Event()
        mutation_calls: list[int] = []
        mutation_lock = threading.Lock()
        observations: list[object] = []
        errors: list[Exception] = []

        def provider(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            if operation == "github_pull_request_precondition_pr":
                payload = {
                    "number": 7,
                    "baseRefName": "main",
                    "headRefName": fixture["branch"],
                    "headRefOid": fixture["head"],
                }
                return 0, json.dumps(payload).encode("utf-8")
            if operation == "github_pull_request_precondition_base":
                payload = {"object": {"sha": expected_base_sha}}
                return 0, json.dumps(payload).encode("utf-8")
            if operation == "github_pull_request_precondition_checks":
                payload = {
                    "total_count": 1,
                    "check_runs": [
                        {
                            **{
                                key: value
                                for key, value in check.items()
                                if key != "app_slug"
                            },
                            "head_sha": fixture["head"],
                            "app": {"slug": check["app_slug"]},
                        }
                    ],
                }
                return 0, json.dumps(payload).encode("utf-8")
            if operation == "github_pull_request_mutation":
                with mutation_lock:
                    mutation_calls.append(len(mutation_calls) + 1)
                    call_number = len(mutation_calls)
                if call_number == 1:
                    first_effect_entered.set()
                    if not release_first_effect.wait(timeout=5):
                        raise AssertionError(
                            "test did not release the first effect"
                        )
                return 0, b""
            if operation == "github_pull_request_observe":
                payload = {
                    "number": 7,
                    "url": (
                        "https://github.com/example/control-plane/pull/7"
                    ),
                    "isDraft": True,
                    "baseRefName": "main",
                    "headRefName": fixture["branch"],
                    "headRefOid": fixture["head"],
                }
                return 0, json.dumps(payload).encode("utf-8")
            raise AssertionError(
                f"unexpected provider operation: {operation}"
            )

        def execute() -> None:
            try:
                observations.append(
                    bridge.execute_pull_request_mutation(
                        fixture["request"], clock=lambda: 100.0
                    )
                )
            except Exception as error:
                errors.append(error)

        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=provider,
        ):
            first = threading.Thread(target=execute)
            first.start()
            self.assertTrue(first_effect_entered.wait(timeout=5))
            second = threading.Thread(target=execute)
            second.start()
            second.join(timeout=5)
            self.assertFalse(second.is_alive())
            release_first_effect.set()
            first.join(timeout=5)
            self.assertFalse(first.is_alive())

        self.assertEqual(len(mutation_calls), 1)
        self.assertEqual(len(observations), 1)
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), "E_PR_MUTATION")

    def test_pr_mutation_unknown_outcome_recovers_by_observation_only(
        self,
    ) -> None:
        import json
        import control_plane.host_bridge as bridge

        expected_base_sha = "a" * 40
        fixture = self._pr_mutation_fixture(
            task_id="TASK-PR-UNKNOWN-OUTCOME",
            expected_pr_number=7,
            expected_base_sha=expected_base_sha,
            expected_checks_digest=None,
        )
        calls: list[str] = []

        def provider(operation, arguments, max_output_bytes):
            del arguments, max_output_bytes
            calls.append(operation)
            if operation == "github_pull_request_precondition_pr":
                payload = {
                    "number": 7,
                    "baseRefName": "main",
                    "headRefName": fixture["branch"],
                    "headRefOid": fixture["head"],
                }
                return 0, json.dumps(payload).encode("utf-8")
            if operation == "github_pull_request_precondition_base":
                payload = {"object": {"sha": expected_base_sha}}
                return 0, json.dumps(payload).encode("utf-8")
            if operation == "github_pull_request_precondition_checks":
                return 0, b'{"total_count":0,"check_runs":[]}'
            if operation == "github_pull_request_mutation":
                return 1, b""
            if operation == "github_pull_request_observe":
                payload = {
                    "number": 7,
                    "url": (
                        "https://github.com/example/control-plane/pull/7"
                    ),
                    "isDraft": True,
                    "baseRefName": "main",
                    "headRefName": fixture["branch"],
                    "headRefOid": fixture["head"],
                }
                return 0, json.dumps(payload).encode("utf-8")
            raise AssertionError(
                f"unexpected provider operation: {operation}"
            )

        with patch.object(
            bridge,
            "_native_host_remote_executor",
            side_effect=provider,
        ):
            with self.assertRaisesRegex(
                ValueError, "E_PR_MUTATION_OUTCOME_UNKNOWN"
            ):
                bridge.execute_pull_request_mutation(
                    fixture["request"], clock=lambda: 100.0
                )
            mutation_count = calls.count(
                "github_pull_request_mutation"
            )
            with self.assertRaisesRegex(ValueError, "E_PR_MUTATION"):
                bridge.execute_pull_request_mutation(
                    fixture["request"], clock=lambda: 100.0
                )
            recovered = (
                bridge.recover_pull_request_mutation_outcome(
                    fixture["request"], clock=lambda: 100.0
                )
            )

        self.assertEqual(
            calls.count("github_pull_request_mutation"),
            mutation_count,
        )
        self.assertEqual(recovered.number, 7)

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

    def test_repository_root_lease_conflicts_with_every_child_scope(self) -> None:
        from control_plane.lifecycle import TaskLease

        variants = (".", "./", "./**")
        for index, root_scope in enumerate(variants):
            with self.subTest(root_scope=root_scope, order="root-first"):
                state_dir = self.state_dir / f"root-first-{index}"
                TaskLease.acquire(
                    state_dir,
                    task_id="TASK-ROOT",
                    worktree="/repo/a",
                    branch="codex/root",
                    session_id="session-root",
                    paths=[root_scope],
                    policy_digest=self.digest,
                )
                with self.assertRaisesRegex(ValueError, "E_LEASE_CONFLICT"):
                    TaskLease.acquire(
                        state_dir,
                        task_id="TASK-CHILD",
                        worktree="/repo/a",
                        branch="codex/child",
                        session_id="session-child",
                        paths=["src/**"],
                        policy_digest=self.digest,
                    )

            with self.subTest(root_scope=root_scope, order="child-first"):
                state_dir = self.state_dir / f"child-first-{index}"
                TaskLease.acquire(
                    state_dir,
                    task_id="TASK-CHILD",
                    worktree="/repo/a",
                    branch="codex/child",
                    session_id="session-child",
                    paths=["src/**"],
                    policy_digest=self.digest,
                )
                with self.assertRaisesRegex(ValueError, "E_LEASE_CONFLICT"):
                    TaskLease.acquire(
                        state_dir,
                        task_id="TASK-ROOT",
                        worktree="/repo/a",
                        branch="codex/root",
                        session_id="session-root",
                        paths=[root_scope],
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

    def test_overlapping_leases_conflict_across_two_worktree_git_dirs(self) -> None:
        from control_plane.lifecycle import TaskLease

        repository, other, common_dir, other_git_dir = (
            self._two_worktree_repository()
        )
        TaskLease.acquire(
            common_dir,
            task_id="TASK-WORKTREE-A",
            worktree=str(repository),
            branch="main",
            session_id="session-a",
            paths=["."],
            policy_digest=self.digest,
        )

        with self.assertRaisesRegex(ValueError, "E_LEASE_CONFLICT"):
            TaskLease.acquire(
                other_git_dir,
                task_id="TASK-WORKTREE-B",
                worktree=str(other),
                branch="codex/other",
                session_id="session-b",
                paths=["src/**"],
                policy_digest=self.digest,
            )

    def test_delivery_and_implementation_leases_conflict_across_worktrees(self) -> None:
        from control_plane.lifecycle import DeliveryLease, TaskLease

        repository, other, common_dir, other_git_dir = self._two_worktree_repository("delivery-conflict")
        head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        TaskLease.acquire(
            common_dir, task_id="TASK-IMPLEMENTATION-OWNER", worktree=str(repository),
            branch="main", session_id="session-implementation-owner", paths=["src/**"],
            policy_digest=self.digest,
        )
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_LEASE_CONFLICT"):
            DeliveryLease.acquire(
                other_git_dir, task_id="TASK-DELIVERY-CANDIDATE", worktree=str(other),
                branch="codex/other", session_id="session-delivery-candidate", paths=["src/file.py"],
                policy_digest=self.digest, generation=1, review_head=head, base_head=head, diff_digest=self.digest,
            )

    def test_delivery_leases_conflict_across_worktrees(self) -> None:
        from control_plane.lifecycle import DeliveryLease

        repository, other, common_dir, other_git_dir = self._two_worktree_repository("delivery-delivery-conflict")
        head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        DeliveryLease.acquire(
            common_dir, task_id="TASK-DELIVERY-OWNER", worktree=str(repository),
            branch="main", session_id="session-delivery-owner", paths=["src/**"],
            policy_digest=self.digest, generation=1, review_head=head, base_head=head, diff_digest=self.digest,
        )
        with self.assertRaisesRegex(ValueError, "E_DELIVERY_LEASE_CONFLICT"):
            DeliveryLease.acquire(
                other_git_dir, task_id="TASK-DELIVERY-CANDIDATE", worktree=str(other),
                branch="codex/other", session_id="session-delivery-candidate", paths=["src/file.py"],
                policy_digest=self.digest, generation=1, review_head=head, base_head=head, diff_digest=self.digest,
            )

    def test_implementation_rejects_overlapping_delivery_lease_across_worktrees(self) -> None:
        from control_plane.lifecycle import DeliveryLease, TaskLease

        repository, other, common_dir, other_git_dir = self._two_worktree_repository("implementation-delivery-conflict")
        head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        DeliveryLease.acquire(
            common_dir, task_id="TASK-DELIVERY-OWNER", worktree=str(repository),
            branch="main", session_id="session-delivery-owner", paths=["src/**"],
            policy_digest=self.digest, generation=1, review_head=head, base_head=head, diff_digest=self.digest,
        )
        with self.assertRaisesRegex(ValueError, "E_LEASE_CONFLICT"):
            TaskLease.acquire(
                other_git_dir, task_id="TASK-IMPLEMENTATION-CANDIDATE", worktree=str(other),
                branch="codex/other", session_id="session-implementation-candidate", paths=["src/file.py"],
                policy_digest=self.digest,
            )

    def test_delivery_lease_acquisition_is_atomic_for_overlapping_worktrees(self) -> None:
        from control_plane.lifecycle import DeliveryLease

        repository, other, common_dir, other_git_dir = self._two_worktree_repository("delivery-atomic")
        head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        barrier = threading.Barrier(2)
        results: list[str] = []

        def acquire(state_dir, task_id, worktree, branch) -> None:
            barrier.wait()
            try:
                DeliveryLease.acquire(
                    state_dir, task_id=task_id, worktree=str(worktree), branch=branch,
                    session_id=f"session-{task_id.lower()}", paths=["src/**"],
                    policy_digest=self.digest, generation=1, review_head=head, base_head=head, diff_digest=self.digest,
                )
                results.append("acquired")
            except ValueError as error:
                results.append(str(error).split(":", 1)[0])

        first = threading.Thread(target=acquire, args=(common_dir, "TASK-DELIVERY-A", repository, "main"))
        second = threading.Thread(target=acquire, args=(other_git_dir, "TASK-DELIVERY-B", other, "codex/other"))
        first.start(); second.start(); first.join(); second.join()
        self.assertCountEqual(results, ["acquired", "E_DELIVERY_LEASE_CONFLICT"])

    def test_delivery_lease_scan_rejects_symlink_and_oversized_candidates(self) -> None:
        from control_plane.lifecycle import DeliveryLease

        for label, install in (
            ("symlink", lambda path: path.symlink_to(path.parent / "outside.json")),
            ("oversized", lambda path: path.write_bytes(b"x" * 65_537)),
        ):
            with self.subTest(label=label):
                repository, _other, common_dir, other_git_dir = self._two_worktree_repository(f"delivery-{label}")
                head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
                candidate = other_git_dir / "codex-control-plane" / "delivery-leases" / "TASK-UNSAFE.json"
                candidate.parent.mkdir(parents=True)
                install(candidate)
                with self.assertRaisesRegex(ValueError, "E_LEASE_OBSERVATION_UNKNOWN"):
                    DeliveryLease.acquire(
                        common_dir, task_id="TASK-DELIVERY-SAFE", worktree=str(repository),
                        branch="main", session_id="session-delivery-safe", paths=["src/**"],
                        policy_digest=self.digest, generation=1, review_head=head, base_head=head, diff_digest=self.digest,
                    )

    def test_writer_lease_never_expires_or_transfers_implicitly(self) -> None:
        from control_plane.lifecycle import TaskLease

        repository, other, common_dir, other_git_dir = (
            self._two_worktree_repository()
        )
        TaskLease.acquire(
            common_dir,
            task_id="TASK-NONEXPIRING-A",
            worktree=str(repository),
            branch="main",
            session_id="session-nonexpiring-owner",
            paths=["."],
            policy_digest=self.digest,
        )

        with (
            patch("control_plane.lifecycle.time.monotonic", return_value=10**12),
            patch("control_plane.lifecycle.os.kill", side_effect=ProcessLookupError),
            self.assertRaisesRegex(ValueError, "E_LEASE_CONFLICT"),
        ):
            TaskLease.acquire(
                other_git_dir,
                task_id="TASK-NONEXPIRING-B",
                worktree=str(other),
                branch="codex/other",
                session_id="session-nonexpiring-replacement",
                paths=["."],
                policy_digest=self.digest,
            )

    def test_worktree_inventory_overflow_or_truncation_is_unknown(self) -> None:
        from control_plane.host_bridge import parse_worktree_porcelain

        def render(count: int) -> bytes:
            return b"".join(
                (
                    f"worktree /repo/w{index}\n"
                    f"HEAD {'a' * 40}\n"
                    f"branch refs/heads/codex/w{index}\n\n"
                ).encode("ascii")
                for index in range(count)
            )

        parsed = parse_worktree_porcelain(
            render(256), max_worktrees=256, max_output_bytes=1_000_000
        )
        self.assertEqual(len(parsed), 256)

        for payload in (
            render(257),
            render(1).rstrip(b"\n"),
            render(1) + render(1),
            b"worktree /repo/w0\nHEAD invalid\n\n",
        ):
            with self.subTest(payload_size=len(payload)):
                with self.assertRaisesRegex(
                    ValueError, "E_LEASE_OBSERVATION_UNKNOWN"
                ):
                    parse_worktree_porcelain(
                        payload,
                        max_worktrees=256,
                        max_output_bytes=1_000_000,
                    )

        with self.assertRaisesRegex(
            ValueError, "E_LEASE_OBSERVATION_UNKNOWN"
        ):
            parse_worktree_porcelain(
                render(1),
                max_worktrees=256,
                max_output_bytes=len(render(1)) - 1,
            )

    def test_worktree_inventory_observation_is_host_bound_one_shot_and_complete(
        self,
    ) -> None:
        from control_plane.host_bridge import (
            observe_worktree_inventory,
            validate_worktree_inventory_observation,
        )

        _, _, common_dir, _ = self._two_worktree_repository()
        now = [100.0]
        observation = observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id="inventory-invocation",
            clock=lambda: now[0],
            ttl_seconds=10,
            max_output_bytes=1_000_000,
        )

        with self.assertRaisesRegex(
            ValueError, "E_LEASE_OBSERVATION_UNKNOWN"
        ):
            validate_worktree_inventory_observation(
                {"observation_id": "forged"},
                expected_common_git_dir=common_dir,
                expected_invocation_id="inventory-invocation",
                clock=lambda: now[0],
            )

        validated = validate_worktree_inventory_observation(
            observation,
            expected_common_git_dir=common_dir,
            expected_invocation_id="inventory-invocation",
            clock=lambda: now[0],
        )
        self.assertEqual(len(validated.records), 2)

        now[0] = 111.0
        with self.assertRaisesRegex(
            ValueError, "E_LEASE_OBSERVATION_UNKNOWN"
        ):
            validate_worktree_inventory_observation(
                observation,
                expected_common_git_dir=common_dir,
                expected_invocation_id="inventory-invocation",
                clock=lambda: now[0],
            )

    def test_worktree_registry_race_is_detected_after_observation_before_lease_scan(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskLease, _common_lease_lock

        repository, _, common_dir, _ = self._two_worktree_repository()
        invocation_id = "inventory-registry-race"
        observation = bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            observation,
            expected_common_git_dir=common_dir,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )

        injected = common_dir / "worktrees" / "externally-added"
        injected.mkdir()
        with (
            _common_lease_lock(common_dir) as token,
            patch(
                "control_plane.lifecycle.subprocess.run",
                side_effect=AssertionError("Git must not run under the flock"),
            ),
            self.assertRaisesRegex(ValueError, "E_LEASE_OBSERVATION_STALE"),
        ):
            TaskLease._acquire_locked(
                token,
                task_id="TASK-REGISTRY-RACE",
                worktree=str(repository),
                branch="main",
                session_id="session-registry-race",
                policy_digest=self.digest,
                scopes=["."],
                inventory=inventory,
            )

        self.assertFalse(
            (
                common_dir
                / "codex-control-plane"
                / "leases"
                / "TASK-REGISTRY-RACE.json"
            ).exists()
        )

    def test_worktree_inventory_reobserves_branch_and_head_before_lease(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskLease, _common_lease_lock

        repository, _, common_dir, _ = self._two_worktree_repository(
            "stale-branch"
        )
        observation = bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id="inventory-stale-branch",
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            observation,
            expected_common_git_dir=common_dir,
            expected_invocation_id="inventory-stale-branch",
            clock=lambda: 100.0,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "switch",
                "-c",
                "codex/switched-after-observation",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with _common_lease_lock(common_dir) as token:
            with self.assertRaisesRegex(
                ValueError, "E_LEASE_OBSERVATION_STALE"
            ):
                TaskLease._acquire_locked(
                    token,
                    task_id="TASK-STALE-BRANCH-INVENTORY",
                    worktree=str(repository),
                    branch="main",
                    session_id="session-stale-branch-inventory",
                    policy_digest=self.digest,
                    scopes=["."],
                    inventory=inventory,
                )
        self.assertFalse(
            (
                common_dir
                / "codex-control-plane"
                / "leases"
                / "TASK-STALE-BRANCH-INVENTORY.json"
            ).exists()
        )

    def test_verification_target_attestors_produce_both_closed_target_types(
        self,
    ) -> None:
        import hashlib
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import (
            TaskLease,
            TaskStore,
            bind_candidate_assurance_bootstrap_authority,
            create_verification_execution_context,
            create_verification_task_bootstrap,
            _run_verification_command,
        )
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
        )
        from tests.router_test_support import VALID_POLICY

        repository, _, common_dir, _ = self._two_worktree_repository()
        policy_path = repository / ".codex" / "project-policy.toml"
        policy_path.parent.mkdir()
        policy_path.write_bytes(VALID_POLICY.read_bytes())
        subprocess.run(
            ["git", "-C", str(repository), "add", ".codex/project-policy.toml"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "policy"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        verifier = self.state_dir / "verification-target-base"
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "worktree",
                "add",
                "--detach",
                str(verifier),
                head,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def inventory(invocation_id: str):
            raw = bridge.observe_worktree_inventory(
                canonical_common_git_dir=common_dir,
                invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=30,
                max_output_bytes=1_000_000,
            )
            return bridge.validate_worktree_inventory_observation(
                raw,
                expected_common_git_dir=common_dir,
                expected_invocation_id=invocation_id,
                clock=lambda: 100.0,
            )

        policy_digest = (
            "sha256:" + hashlib.sha256(policy_path.read_bytes()).hexdigest()
        )
        candidate = bridge.attest_candidate_verification_target(
            inventory=inventory("target-candidate"),
            canonical_repository=repository,
            candidate_worktree=repository,
            expected_branch="main",
            expected_head=head,
            expected_candidate_policy_digest=policy_digest,
            content_trust="external_untrusted",
            session_id="session-target-attestation",
            invocation_id="target-candidate",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        governing = bridge.attest_governing_base_verification_target(
            inventory=inventory("target-governing"),
            canonical_repository=verifier,
            verifier_worktree=verifier,
            expected_governing_base_commit=head,
            session_id="session-target-attestation",
            invocation_id="target-governing",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

        self.assertIsInstance(
            candidate, bridge.ValidatedCandidateWorktreeObservation
        )
        self.assertIsInstance(
            governing,
            bridge.ValidatedGoverningBaseWorktreeObservation,
        )
        self.assertNotEqual(type(candidate), type(governing))
        self.assertEqual(candidate.content_trust, "external_untrusted")
        with self.assertRaisesRegex(TypeError, "host-bound"):
            bridge.ValidatedCandidateWorktreeObservation()

        forged_candidate = object.__new__(
            bridge.ValidatedCandidateWorktreeObservation
        )
        for name in bridge._ValidatedVerificationTarget.__slots__:
            setattr(forged_candidate, name, getattr(candidate, name))
        forged_candidate.content_trust = "project_owned"
        forged_runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=policy_digest,
            attestor_worktree=str(repository.resolve()),
            target_worktree=str(repository.resolve()),
            governing_base_commit=head,
            runtime_layout="source",
            session_id="session-target-attestation",
            invocation_id="target-candidate",
            freshness_deadline=130.0,
        )
        with self.assertRaisesRegex(
            ValueError, "E_VERIFICATION_BOOTSTRAP"
        ):
            bind_candidate_assurance_bootstrap_authority(
                governing_runtime=forged_runtime,
                candidate_target=forged_candidate,
                expected_head=head,
                session_id="session-target-attestation",
                invocation_id="target-candidate",
                clock=lambda: 100.0,
            )

        runtime = governing_runtime_observation(
            runtime_digest=self.digest,
            lock_digest=self.digest,
            policy_digest=policy_digest,
            attestor_worktree=str(repository.resolve()),
            target_worktree=str(repository.resolve()),
            governing_base_commit=head,
            runtime_layout="source",
            session_id="session-target-attestation",
            invocation_id="target-candidate",
            freshness_deadline=130.0,
        )
        authority = bind_candidate_assurance_bootstrap_authority(
            governing_runtime=runtime,
            candidate_target=candidate,
            expected_head=head,
            session_id="session-target-attestation",
            invocation_id="target-candidate",
            clock=lambda: 100.0,
        )
        bootstrap = create_verification_task_bootstrap(
            task_id="TASK-EXTERNAL-VERIFY", authority=authority
        )
        store = TaskStore(common_dir, runtime_digest=self.digest)
        store.start(
            "TASK-EXTERNAL-VERIFY",
            outcome="local_change",
            branch="main",
            task_digest=bootstrap.task_digest,
            decision_digest=self.digest,
            verification_bootstrap=bootstrap,
        )
        for state_name, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
        ):
            state = store.transition(
                "TASK-EXTERNAL-VERIFY",
                state_name,
                evidence=evidence,
                current_branch="main",
            )
        lease = TaskLease.acquire(
            common_dir,
            task_id="TASK-EXTERNAL-VERIFY",
            worktree=str(repository),
            branch="main",
            session_id="session-target-attestation",
            paths=["."],
            policy_digest=self.digest,
        )
        context = create_verification_execution_context(
            task_context=state,
            lease=lease,
            canonical_repo=repository,
            expected_head=head,
            session_id="session-target-attestation",
            dedicated_temp_root=self.state_dir / "external-verification-temp",
            clock=lambda: 100.0,
        )
        with patch("control_plane.lifecycle.subprocess.Popen") as child:
            with self.assertRaisesRegex(
                ValueError, "E_VERIFICATION_HOST_ISOLATION"
            ):
                _run_verification_command(
                    context=context,
                    command_id="normal_budget",
                    clock=lambda: 100.0,
                )
        child.assert_not_called()

    def test_task_lease_release_is_owner_bound_idempotent_and_unblocks_next_worktree(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskLease

        repository, other, common_dir, other_git_dir = (
            self._two_worktree_repository()
        )
        lease = TaskLease.acquire(
            common_dir,
            task_id="TASK-RELEASE-A",
            worktree=str(repository),
            branch="main",
            session_id="session-release-a",
            paths=["."],
            policy_digest=self.digest,
        )

        with self.assertRaisesRegex(ValueError, "E_LEASE_MISMATCH"):
            TaskLease.release(
                common_dir,
                common_dir,
                task_id="TASK-RELEASE-A",
                worktree=str(repository),
                branch="main",
                session_id="session-other",
                policy_digest=self.digest,
                lease_digest=lease["lease_digest"],
            )

        released = TaskLease.release(
            common_dir,
            common_dir,
            task_id="TASK-RELEASE-A",
            worktree=str(repository),
            branch="main",
            session_id="session-release-a",
            policy_digest=self.digest,
            lease_digest=lease["lease_digest"],
        )
        repeated = TaskLease.release(
            common_dir,
            common_dir,
            task_id="TASK-RELEASE-A",
            worktree=str(repository),
            branch="main",
            session_id="session-release-a",
            policy_digest=self.digest,
            lease_digest=lease["lease_digest"],
        )

        self.assertTrue(released["released"])
        self.assertTrue(repeated["idempotent"])
        next_lease = TaskLease.acquire(
            other_git_dir,
            task_id="TASK-RELEASE-B",
            worktree=str(other),
            branch="codex/other",
            session_id="session-release-b",
            paths=["."],
            policy_digest=self.digest,
        )
        self.assertEqual(next_lease["task_id"], "TASK-RELEASE-B")

    def test_release_locked_never_reacquires_common_dir_flock(self) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import (
            LeaseLockToken,
            TaskLease,
            _common_lease_lock,
            _valid_lease_lock_token,
        )

        lease = TaskLease.acquire(
            self.state_dir,
            task_id="TASK-RELEASE-LOCKED",
            worktree="/repo/release-locked",
            branch="codex/release-locked",
            session_id="session-release-locked",
            paths=["."],
            policy_digest=self.digest,
        )
        with _common_lease_lock(self.state_dir) as token:
            with patch(
                "control_plane.lifecycle._common_lease_lock",
                side_effect=AssertionError("release attempted to relock"),
            ):
                released = TaskLease._release_locked(
                    token,
                    state_dir=self.state_dir,
                    task_id="TASK-RELEASE-LOCKED",
                    worktree="/repo/release-locked",
                    branch="codex/release-locked",
                    session_id="session-release-locked",
                    policy_digest=self.digest,
                    lease_digest=lease["lease_digest"],
                )
        self.assertTrue(released["released"])

        with self.assertRaisesRegex(TypeError, "internal"):
            LeaseLockToken()
        forged = object.__new__(LeaseLockToken)
        forged._active = True
        forged.common_dir = self.state_dir.resolve()
        active_tokens = getattr(lifecycle, "_ACTIVE_LEASE_LOCK_TOKENS", {})
        active_tokens[id(forged)] = forged.common_dir
        self.assertFalse(
            _valid_lease_lock_token(forged, self.state_dir),
            "a mutable Python registry must not substitute for a held flock",
        )
        with self.assertRaisesRegex(ValueError, "E_LEASE_LOCK"):
            TaskLease._release_locked(
                forged,
                state_dir=self.state_dir,
                task_id="TASK-RELEASE-LOCKED",
                worktree="/repo/release-locked",
                branch="codex/release-locked",
                session_id="session-release-locked",
                policy_digest=self.digest,
                lease_digest=lease["lease_digest"],
            )

    def test_abandoned_lease_recovery_requires_exact_owner_and_host_authorization(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore

        repository, other, common_dir, other_git_dir = (
            self._two_worktree_repository()
        )
        task_id = "TASK-ABANDONED-A"
        owner_session = "session-abandoned-owner"
        recovering_session = "session-abandoned-recovery"
        store = TaskStore(common_dir)
        store.start(
            task_id,
            outcome="local_change",
            branch="main",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        lease = TaskLease.acquire(
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            session_id=owner_session,
            paths=["."],
            policy_digest=self.digest,
        )
        invocation_id = "abandoned-recovery"
        inventory_observation = bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            inventory_observation,
            expected_common_git_dir=common_dir,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )
        subject_digest = contract_digest(
            {"task_id": task_id, "lease_digest": lease["lease_digest"]}
        )
        from tests.host_adapter_test_support import (
            native_session_event,
            native_user_interaction_event,
        )

        session_event = native_session_event(
            event_id="session-abandoned",
            session_id=recovering_session,
            invocation_id=invocation_id,
            observed_at_monotonic=100.0,
        )
        capability = bridge.attest_host_adapter_capability(
            session_event,
            expected_session_id=recovering_session,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        confirmation_event = native_user_interaction_event(
            event_id="confirm-abandon",
            session_id=recovering_session,
            invocation_id=invocation_id,
            task_digest=self.digest,
            subject_digest=subject_digest,
            observed_at_monotonic=100.0,
        )
        authorization = bridge.frame_lease_recovery_authorization(
            native_confirmation_event=confirmation_event,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            owner_session_id=owner_session,
            recovering_session_id=recovering_session,
            policy_digest=self.digest,
            lease_digest=lease["lease_digest"],
            inventory=inventory,
            invocation_id=invocation_id,
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

        with self.assertRaisesRegex(
            ValueError, "E_LEASE_RECOVERY_UNAUTHORIZED"
        ):
            TaskLease.recover_abandoned(
                common_dir,
                common_dir,
                task_id=task_id,
                worktree=str(repository),
                branch="main",
                owner_session_id=owner_session,
                policy_digest=self.digest,
                lease_digest=lease["lease_digest"],
                recovery_authorization={"authorization_id": "forged"},
                worktree_inventory=inventory,
            )

        recovered = TaskLease.recover_abandoned(
            common_dir,
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            owner_session_id=owner_session,
            policy_digest=self.digest,
            lease_digest=lease["lease_digest"],
            recovery_authorization=authorization,
            worktree_inventory=inventory,
        )

        self.assertTrue(recovered["released"])
        blocked = store.status(task_id)
        self.assertEqual(blocked["state"], "blocked")
        self.assertTrue(blocked["resume_forbidden"])
        with self.assertRaisesRegex(ValueError, "E_STATE_RESUME"):
            store.resume(task_id, current_branch="main")

        next_lease = TaskLease.acquire(
            other_git_dir,
            task_id="TASK-ABANDONED-B",
            worktree=str(other),
            branch="codex/other",
            session_id=recovering_session,
            paths=["."],
            policy_digest=self.digest,
        )
        self.assertEqual(next_lease["task_id"], "TASK-ABANDONED-B")

    def test_abandoned_lease_recovery_requires_a_new_task(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from tests.host_adapter_test_support import (
            native_session_event,
            native_user_interaction_event,
        )

        repository, other, common_dir, other_git_dir = (
            self._two_worktree_repository()
        )
        task_id = "TASK-ABANDONED-OLD"
        owner_session = "session-abandoned-old"
        recovering_session = "session-abandoned-new"
        TaskStore(common_dir).start(
            task_id,
            outcome="local_change",
            branch="main",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        lease = TaskLease.acquire(
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            session_id=owner_session,
            paths=["."],
            policy_digest=self.digest,
        )
        invocation_id = "abandoned-new-task"
        raw_inventory = bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            raw_inventory,
            expected_common_git_dir=common_dir,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-abandoned-new-task",
                session_id=recovering_session,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=recovering_session,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization = bridge.frame_lease_recovery_authorization(
            native_confirmation_event=native_user_interaction_event(
                event_id="confirm-abandoned-new-task",
                session_id=recovering_session,
                invocation_id=invocation_id,
                task_digest=self.digest,
                subject_digest=contract_digest(
                    {
                        "task_id": task_id,
                        "lease_digest": lease["lease_digest"],
                    }
                ),
                observed_at_monotonic=100.0,
            ),
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            owner_session_id=owner_session,
            recovering_session_id=recovering_session,
            policy_digest=self.digest,
            lease_digest=lease["lease_digest"],
            inventory=inventory,
            invocation_id=invocation_id,
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        TaskLease.recover_abandoned(
            common_dir,
            common_dir,
            task_id=task_id,
            worktree=str(repository),
            branch="main",
            owner_session_id=owner_session,
            policy_digest=self.digest,
            lease_digest=lease["lease_digest"],
            recovery_authorization=authorization,
            worktree_inventory=inventory,
        )

        with self.assertRaisesRegex(
            ValueError, "E_LEASE_RECOVERY_UNAUTHORIZED"
        ):
            TaskLease.acquire(
                other_git_dir,
                task_id=task_id,
                worktree=str(other),
                branch="codex/other",
                session_id=recovering_session,
                paths=["."],
                policy_digest=self.digest,
            )
        replacement = TaskLease.acquire(
            other_git_dir,
            task_id="TASK-ABANDONED-REPLACEMENT",
            worktree=str(other),
            branch="codex/other",
            session_id=recovering_session,
            paths=["."],
            policy_digest=self.digest,
        )
        self.assertEqual(
            replacement["task_id"], "TASK-ABANDONED-REPLACEMENT"
        )

    def test_abandoned_recovery_never_leaves_released_lease_with_resumable_task(
        self,
    ) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskLease

        task_id = "TASK-ABANDON-CRASH"
        (
            repository,
            common_dir,
            store,
            lease,
            owner_session,
            inventory,
            authorization,
        ) = self._abandoned_recovery_fixture(task_id=task_id)
        original_atomic_json = lifecycle._atomic_json
        calls = [0]

        def fail_after_release(path, value):
            calls[0] += 1
            if calls[0] == 3:
                raise RuntimeError("injected-after-abandon-release")
            return original_atomic_json(path, value)

        with (
            patch(
                "control_plane.lifecycle._atomic_json",
                side_effect=fail_after_release,
            ),
            self.assertRaisesRegex(
                RuntimeError, "injected-after-abandon-release"
            ),
        ):
            TaskLease.recover_abandoned(
                common_dir,
                common_dir,
                task_id=task_id,
                worktree=str(repository),
                branch="main",
                owner_session_id=owner_session,
                policy_digest=self.digest,
                lease_digest=lease["lease_digest"],
                recovery_authorization=authorization,
                worktree_inventory=inventory,
            )

        marker = store.status(task_id)
        self.assertEqual(marker["state"], "finalizing_abandon")
        self.assertTrue(marker["resume_forbidden"])
        self.assertFalse(
            (
                common_dir
                / "codex-control-plane"
                / "leases"
                / f"{task_id}.json"
            ).exists()
        )
        recovered = store.recover_writer_finalization(task_id)
        self.assertEqual(recovered["state"], "blocked")
        self.assertTrue(recovered["resume_forbidden"])
        with self.assertRaisesRegex(ValueError, "E_STATE_RESUME"):
            store.resume(task_id, current_branch="main")

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

    def test_serialized_transition_evidence_cannot_advance_authoritative_state(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-SERIALIZED-EVIDENCE",
            outcome="commit",
            branch="codex/evidence",
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for state, evidence in (
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
        ):
            store.transition(
                "TASK-SERIALIZED-EVIDENCE",
                state,
                evidence=evidence,
                current_branch="codex/evidence",
            )

        with self.assertRaisesRegex(
            ValueError, "E_LIFECYCLE_OBSERVATION_REQUIRED"
        ):
            store.transition(
                "TASK-SERIALIZED-EVIDENCE",
                "committed",
                evidence={"commit": "a" * 40},
                current_branch="codex/evidence",
            )

    def test_lifecycle_observation_rejects_binding_expiry_and_replay(self) -> None:
        import control_plane.host_bridge as bridge

        now = [100.0]
        observation = lifecycle_observation(
            bridge.LocalGitObservation,
            observation_id="local-git-observation",
            invocation_id="local-git-invocation",
            task_digest=self.digest,
            repository_identity=str(self.state_dir.resolve()),
            worktree_identity=str(self.state_dir.resolve()),
            branch="codex/evidence",
            prior_head="a" * 40,
            target_state="committed",
            session_id="session-evidence",
            provider="git",
            subject_digest=self.digest,
            evidence={"commit": "b" * 40},
            observed_at_monotonic=100.0,
            freshness_deadline=105.0,
        )

        now[0] = 106.0
        with self.assertRaisesRegex(ValueError, "E_LIFECYCLE_OBSERVATION"):
            bridge.validate_local_git_observation(
                observation,
                expected_task_digest=self.digest,
                expected_repo=self.state_dir,
                expected_worktree=self.state_dir,
                expected_branch="codex/evidence",
                expected_prior_head="a" * 40,
                expected_target_state="committed",
                expected_session_id="session-evidence",
                expected_invocation_id="local-git-invocation",
                clock=lambda: now[0],
            )

        now[0] = 100.0
        validated = bridge.validate_local_git_observation(
            observation,
            expected_task_digest=self.digest,
            expected_repo=self.state_dir,
            expected_worktree=self.state_dir,
            expected_branch="codex/evidence",
            expected_prior_head="a" * 40,
            expected_target_state="committed",
            expected_session_id="session-evidence",
            expected_invocation_id="local-git-invocation",
            clock=lambda: now[0],
        )
        bridge.consume_lifecycle_observation(validated)
        with self.assertRaisesRegex(ValueError, "E_LIFECYCLE_REPLAY"):
            bridge.consume_lifecycle_observation(validated)

        forged = object.__new__(bridge.ValidatedGitHubObservation)
        forged._consumed = False
        forged.observation_id = "forged-github-observation"
        forged.task_digest = self.digest
        forged.branch = "codex/evidence"
        forged.prior_head = "a" * 40
        forged.target_state = "merged"
        forged.evidence = {"merge_commit": "b" * 40}
        with self.assertRaisesRegex(
            ValueError, "E_LIFECYCLE_OBSERVATION_REQUIRED"
        ):
            bridge.consume_lifecycle_observation(forged)

    def test_local_git_observer_binds_the_new_clean_commit(self) -> None:
        from control_plane.host_bridge import (
            observe_local_git_state,
            validate_local_git_observation,
        )

        repository, _, _, _ = self._two_worktree_repository()
        prior_head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repository), "add", "tracked.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repository), "commit", "-m", "change"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        new_head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

        observation = observe_local_git_state(
            task_state={"task_digest": self.digest},
            expected_repo=repository,
            expected_worktree=repository,
            expected_branch="main",
            expected_prior_head=prior_head,
            target_state="committed",
            session_id="session-local-git",
            invocation_id="invocation-local-git",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        validated = validate_local_git_observation(
            observation,
            expected_task_digest=self.digest,
            expected_repo=repository,
            expected_worktree=repository,
            expected_branch="main",
            expected_prior_head=prior_head,
            expected_target_state="committed",
            expected_session_id="session-local-git",
            expected_invocation_id="invocation-local-git",
            clock=lambda: 100.0,
        )

        self.assertEqual(validated.evidence, {"commit": new_head})

    def test_remote_lifecycle_requires_host_bound_github_observation(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskStore

        branch = "codex/remote-evidence"
        head = "b" * 40
        store = TaskStore(self.state_dir)
        store.start(
            "TASK-REMOTE-EVIDENCE",
            outcome="pull_request",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for state, evidence in (
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
        ):
            store.transition(
                "TASK-REMOTE-EVIDENCE",
                state,
                evidence=evidence,
                current_branch=branch,
            )
        local = lifecycle_observation(
            bridge.LocalGitObservation,
            observation_id="local-for-remote",
            invocation_id="local-for-remote",
            task_digest=self.digest,
            repository_identity=str(self.state_dir.resolve()),
            worktree_identity=str(self.state_dir.resolve()),
            branch=branch,
            prior_head="a" * 40,
            target_state="committed",
            session_id="session-remote",
            provider="git",
            subject_digest=self.digest,
            evidence={"commit": head},
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        validated_local = bridge.validate_local_git_observation(
            local,
            expected_task_digest=self.digest,
            expected_repo=self.state_dir,
            expected_worktree=self.state_dir,
            expected_branch=branch,
            expected_prior_head="a" * 40,
            expected_target_state="committed",
            expected_session_id="session-remote",
            expected_invocation_id="local-for-remote",
            clock=lambda: 100.0,
        )
        store.transition(
            "TASK-REMOTE-EVIDENCE",
            "committed",
            evidence=validated_local,
            current_branch=branch,
        )

        with self.assertRaisesRegex(
            ValueError, "E_LIFECYCLE_OBSERVATION_REQUIRED"
        ):
            store.transition(
                "TASK-REMOTE-EVIDENCE",
                "pushed",
                evidence={"remote_head": head},
                current_branch=branch,
            )

        remote = lifecycle_observation(
            bridge.GitHubObservation,
            observation_id="github-push",
            invocation_id="github-push",
            task_digest=self.digest,
            repository_identity=str(self.state_dir.resolve()),
            worktree_identity=str(self.state_dir.resolve()),
            branch=branch,
            prior_head=head,
            target_state="pushed",
            session_id="session-remote",
            provider="github",
            subject_digest=self.digest,
            evidence={"remote_head": head},
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        validated_remote = bridge.validate_github_observation(
            remote,
            expected_task_digest=self.digest,
            expected_repo=self.state_dir,
            expected_worktree=self.state_dir,
            expected_branch=branch,
            expected_prior_head=head,
            expected_target_state="pushed",
            expected_session_id="session-remote",
            expected_invocation_id="github-push",
            clock=lambda: 100.0,
        )
        pushed = store.transition(
            "TASK-REMOTE-EVIDENCE",
            "pushed",
            evidence=validated_remote,
            current_branch=branch,
        )

        self.assertEqual(pushed["evidence"]["pushed"]["remote_head"], head)

    def test_pr_review_cycle_without_task5a_marker_is_rejected(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskStore

        repository, _, common_dir, _ = self._two_worktree_repository()
        branch = "main"
        task_id = "TASK-PR-REVISION"
        prior_head = "b" * 40
        store = TaskStore(common_dir)
        store.start(
            task_id,
            outcome="pull_request",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for state, evidence in (
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
        ):
            store.transition(
                task_id,
                state,
                evidence=evidence,
                current_branch=branch,
            )

        local_raw = lifecycle_observation(
            bridge.LocalGitObservation,
            observation_id="revision-local",
            invocation_id="revision-local",
            task_digest=self.digest,
            repository_identity=str(repository.resolve()),
            worktree_identity=str(repository.resolve()),
            branch=branch,
            prior_head="a" * 40,
            target_state="committed",
            session_id="session-revision",
            provider="git",
            subject_digest=self.digest,
            evidence={"commit": prior_head},
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        local = bridge.validate_local_git_observation(
            local_raw,
            expected_task_digest=self.digest,
            expected_repo=repository,
            expected_worktree=repository,
            expected_branch=branch,
            expected_prior_head="a" * 40,
            expected_target_state="committed",
            expected_session_id="session-revision",
            expected_invocation_id="revision-local",
            clock=lambda: 100.0,
        )
        store.transition(
            task_id, "committed", evidence=local, current_branch=branch
        )

        def github(
            target: str, evidence: dict, invocation: str
        ):
            raw = lifecycle_observation(
                bridge.GitHubObservation,
                observation_id=invocation,
                invocation_id=invocation,
                task_digest=self.digest,
                repository_identity=str(repository.resolve()),
                worktree_identity=str(repository.resolve()),
                branch=branch,
                prior_head=prior_head,
                target_state=target,
                session_id="session-revision",
                provider="github",
                subject_digest=self.digest,
                evidence=evidence,
                observed_at_monotonic=100.0,
                freshness_deadline=130.0,
            )
            return bridge.validate_github_observation(
                raw,
                expected_task_digest=self.digest,
                expected_repo=repository,
                expected_worktree=repository,
                expected_branch=branch,
                expected_prior_head=prior_head,
                expected_target_state=target,
                expected_session_id="session-revision",
                expected_invocation_id=invocation,
                clock=lambda: 100.0,
            )

        store.transition(
            task_id,
            "pushed",
            evidence=github("pushed", {"remote_head": prior_head}, "revision-push"),
            current_branch=branch,
        )
        store.transition(
            task_id,
            "pr_draft",
            evidence=github(
                "pr_draft",
                {
                    "pull_request": {
                        "number": 7,
                        "url": "https://example.invalid/pr/7",
                        "head_commit": prior_head,
                    }
                },
                "revision-pr",
            ),
            current_branch=branch,
        )
        state = store.status(task_id)
        inventory_raw = bridge.observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id="revision-start",
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_000_000,
        )
        inventory = bridge.validate_worktree_inventory_observation(
            inventory_raw,
            expected_common_git_dir=common_dir,
            expected_invocation_id="revision-start",
            clock=lambda: 100.0,
        )
        revision_raw = lifecycle_observation(
            bridge.GitHubObservation,
            observation_id="revision-feedback",
            invocation_id="revision-feedback",
            task_digest=self.digest,
            repository_identity=str(repository.resolve()),
            worktree_identity=str(repository.resolve()),
            branch=branch,
            prior_head=prior_head,
            target_state="implementing",
            session_id="session-revision",
            provider="github",
            subject_digest=self.digest,
            evidence={
                "pull_request_number": 7,
                "prior_head": prior_head,
                "reason": "review_feedback",
                "observation_digest": self.digest,
            },
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        revision_observation = bridge.validate_github_observation(
            revision_raw,
            expected_task_digest=self.digest,
            expected_repo=repository,
            expected_worktree=repository,
            expected_branch=branch,
            expected_prior_head=prior_head,
            expected_target_state="implementing",
            expected_session_id="session-revision",
            expected_invocation_id="revision-feedback",
            clock=lambda: 100.0,
        )

        with self.assertRaisesRegex(ValueError, "E_REVISION_MARKER"):
            store.start_revision(
                task_id,
                expected_generation=state["generation"],
                reason="review_feedback",
                observation=revision_observation,
                worktree_inventory=inventory,
                worktree=str(repository),
                session_id="session-revision",
                policy_digest=self.digest,
                scope_paths=["."],
                current_branch=branch,
            )

    def test_start_revision_without_marker_cannot_acquire_writer_lease(
        self,
    ) -> None:
        self.test_pr_review_cycle_without_task5a_marker_is_rejected()

    def test_start_revision_uses_lock_token_aware_acquire_without_relocking_or_subprocess(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskLease

        with patch.object(
            TaskLease,
            "acquire",
            side_effect=AssertionError(
                "start_revision called the public relocking API"
            ),
        ):
            self.test_pr_review_cycle_without_task5a_marker_is_rejected()

    def test_bootstrap_review_round_uses_a_fresh_child_and_lease(self) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        branch = "codex/bootstrap-review"
        store = TaskStore(self.state_dir)
        store.start(
            "LOCAL-R0",
            outcome="answer",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        lease_r0 = TaskLease.acquire(
            self.state_dir,
            task_id="LOCAL-R0",
            worktree="/repo/bootstrap-review",
            branch=branch,
            session_id="session-local-r0",
            paths=["."],
            policy_digest=self.digest,
        )
        store.transition("LOCAL-R0", "planned", current_branch=branch)
        store.close("LOCAL-R0", current_branch=branch)
        with self.assertRaisesRegex(ValueError, "E_STATE_FINALIZING|illegal"):
            store.transition(
                "LOCAL-R0", "planned", current_branch=branch
            )

        store.start(
            "LOCAL-R1",
            outcome="answer",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        lease_r1 = TaskLease.acquire(
            self.state_dir,
            task_id="LOCAL-R1",
            worktree="/repo/bootstrap-review",
            branch=branch,
            session_id="session-local-r1",
            paths=["."],
            policy_digest=self.digest,
        )
        self.assertNotEqual(
            lease_r0["lease_digest"], lease_r1["lease_digest"]
        )
        self.assertEqual(store.status("LOCAL-R0")["state"], "closed")
        self.assertEqual(store.status("LOCAL-R1")["state"], "framed")

    def test_base_advance_cannot_use_start_revision(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        with self.assertRaisesRegex(ValueError, "E_REFRAME_REQUIRED"):
            store.start_revision(
                "TASK-BASE-ADVANCED",
                expected_generation=0,
                reason="base_advanced",
                observation={},
                worktree_inventory={},
                worktree="/repo/a",
                session_id="session-base-advanced",
                policy_digest=self.digest,
                scope_paths=["."],
                current_branch="codex/base-advanced",
            )

    def test_revision_start_marker_without_required_binding_is_rejected(
        self,
    ) -> None:
        import json
        from control_plane.lifecycle import TaskStore

        task_id = "TASK-REVISION-RECOVERY"
        branch = "codex/revision-recovery"
        store = TaskStore(self.state_dir)
        store.start(
            task_id,
            outcome="pull_request",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        path = (
            self.state_dir
            / "codex-control-plane"
            / "tasks"
            / f"{task_id}.json"
        )
        marker = store.status(task_id)
        marker.update(
            {
                "state": "finalizing_revision",
                "generation": 8,
                "resume_forbidden": True,
                "revision_finalization": {
                    "prior_state": "pr_draft",
                    "prior_generation": 8,
                    "lease": {
                        "task_id": task_id,
                        "worktree": "/repo/revision-recovery",
                        "branch": branch,
                        "session_id": "session-revision-recovery",
                        "policy_digest": self.digest,
                    },
                    "next_state": {},
                },
            }
        )
        path.write_text(json.dumps(marker), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "E_REVISION_RECOVERY"):
            store.recover_revision_start(task_id)

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

    def test_legacy_release_lifecycle_cannot_bypass_typed_pr_readiness(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
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
            if target == "pr_ready":
                with self.assertRaisesRegex(
                    ValueError, "E_PR_READINESS_PROOF"
                ):
                    store.transition(
                        "TASK-RELEASE",
                        target,
                        evidence=evidence,
                        current_branch=branch,
                    )
                self.assertEqual(
                    store.status("TASK-RELEASE")["state"], "pr_draft"
                )
                break
            if target in {
                "committed",
                "pushed",
                "pr_draft",
                "pr_ready",
                "merged",
                "base_verified",
                "release_pending",
                "released",
                "observed",
            }:
                invocation = f"release-{target}"
                observation_type = (
                    bridge.LocalGitObservation
                    if target == "committed"
                    else (
                        bridge.GitHubObservation
                        if target
                        in {
                            "pushed",
                            "pr_draft",
                            "pr_ready",
                            "merged",
                            "base_verified",
                        }
                        else bridge.ReleaseProviderObservation
                    )
                )
                provider = (
                    "git"
                    if target == "committed"
                    else (
                        "github"
                        if observation_type is bridge.GitHubObservation
                        else "testflight"
                    )
                )
                raw = lifecycle_observation(
                    observation_type,
                    observation_id=invocation,
                    invocation_id=invocation,
                    task_digest=self.digest,
                    repository_identity=str(self.state_dir.resolve()),
                    worktree_identity=str(self.state_dir.resolve()),
                    branch=branch,
                    prior_head=("0" * 40 if target == "committed" else head),
                    target_state=target,
                    session_id="session-release",
                    provider=provider,
                    subject_digest=self.digest,
                    evidence=evidence,
                    observed_at_monotonic=100.0,
                    freshness_deadline=130.0,
                )
                validator = (
                    bridge.validate_local_git_observation
                    if target == "committed"
                    else (
                        bridge.validate_github_observation
                        if observation_type is bridge.GitHubObservation
                        else bridge.validate_release_provider_observation
                    )
                )
                validator_arguments = {
                    "expected_task_digest": self.digest,
                    "expected_repo": self.state_dir,
                    "expected_worktree": self.state_dir,
                    "expected_branch": branch,
                    "expected_prior_head": (
                        "0" * 40 if target == "committed" else head
                    ),
                    "expected_target_state": target,
                    "expected_session_id": "session-release",
                    "expected_invocation_id": invocation,
                    "clock": lambda: 100.0,
                }
                if validator is bridge.validate_release_provider_observation:
                    validator_arguments["expected_provider"] = "testflight"
                evidence = validator(raw, **validator_arguments)
            store.transition(
                "TASK-RELEASE",
                target,
                evidence=evidence,
                current_branch=branch,
            )

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

    def test_close_marker_recovers_after_release_boundary_crash(self) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        branch = "codex/recover-close"
        task_id = "TASK-RECOVER-CLOSE"
        store = TaskStore(self.state_dir)
        state = store.start(
            task_id,
            outcome="answer",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        lease = TaskLease.acquire(
            self.state_dir,
            task_id=task_id,
            worktree="/repo/recover-close",
            branch=branch,
            session_id="session-recover-close",
            paths=["."],
            policy_digest=self.digest,
        )
        store.transition(task_id, "planned", current_branch=branch)

        with (
            patch.object(
                TaskLease,
                "_release_locked",
                side_effect=RuntimeError("injected-after-marker"),
            ),
            self.assertRaisesRegex(RuntimeError, "injected-after-marker"),
        ):
            store.close(task_id, current_branch=branch)

        self.assertEqual(
            store.status(task_id)["state"], "finalizing_close"
        )
        recovered = store.recover_writer_finalization(task_id)
        self.assertEqual(recovered["state"], "closed")
        self.assertEqual(recovered["generation"], 2)
        self.assertFalse(
            (
                self.state_dir
                / "codex-control-plane"
                / "leases"
                / f"{task_id}.json"
            ).exists()
        )

    def test_close_and_suspend_writer_are_two_phase_and_crash_recoverable(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        branch = "codex/recover-suspend"
        task_id = "TASK-RECOVER-SUSPEND"
        store = TaskStore(self.state_dir)
        store.start(
            task_id,
            outcome="local_change",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
        ):
            state = store.transition(
                task_id,
                target,
                evidence=evidence,
                current_branch=branch,
            )
        TaskLease.acquire(
            self.state_dir,
            task_id=task_id,
            worktree="/repo/recover-suspend",
            branch=branch,
            session_id="session-recover-suspend",
            paths=["."],
            policy_digest=self.digest,
        )

        with (
            patch.object(
                TaskLease,
                "_release_locked",
                side_effect=RuntimeError("injected-suspend-marker"),
            ),
            self.assertRaisesRegex(RuntimeError, "injected-suspend-marker"),
        ):
            store.suspend_for_reframe(
                task_id,
                expected_generation=state["generation"],
                current_branch=branch,
            )

        marker = store.status(task_id)
        self.assertEqual(marker["state"], "finalizing_suspend")
        self.assertTrue(marker["resume_forbidden"])
        with self.assertRaisesRegex(ValueError, "E_STATE_FINALIZING"):
            store.transition(task_id, "verifying", current_branch=branch)
        recovered = store.recover_writer_finalization(task_id)
        self.assertEqual(recovered["state"], "blocked")
        self.assertEqual(recovered["block_reason"], "E_REFRAME_REQUIRED")
        self.assertTrue(recovered["resume_forbidden"])
        self.assertFalse(
            (
                self.state_dir
                / "codex-control-plane"
                / "leases"
                / f"{task_id}.json"
            ).exists()
        )

    def test_abort_verification_is_owner_bound_two_phase_and_not_resumable(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        branch = "codex/verifier"
        task_id = "TASK-VERIFY-ABORT"
        worktree = "/repo/verifier"
        session = "session-verifier"
        store = TaskStore(self.state_dir)
        store.start(
            task_id,
            outcome="local_change",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        store.transition(task_id, "planned", current_branch=branch)
        store.transition(
            task_id,
            "ready",
            evidence={"preflight_ok": True},
            current_branch=branch,
        )
        state = store.transition(
            task_id, "implementing", current_branch=branch
        )
        lease = TaskLease.acquire(
            self.state_dir,
            task_id=task_id,
            worktree=worktree,
            branch=branch,
            session_id=session,
            paths=["."],
            policy_digest=self.digest,
        )

        blocked = store.abort_verification(
            task_id=task_id,
            expected_generation=state["generation"],
            task_digest=self.digest,
            repo=worktree,
            worktree=worktree,
            branch=branch,
            session_id=session,
            lease_digest=lease["lease_digest"],
            reason_code="E_VERIFICATION_FAIL",
        )

        self.assertEqual(blocked["state"], "blocked")
        self.assertTrue(blocked["resume_forbidden"])
        self.assertTrue(blocked["verification_aborted"])
        with self.assertRaisesRegex(ValueError, "E_STATE_RESUME"):
            store.resume(task_id, current_branch=branch)

    def test_recover_verification_abort_uses_durable_marker(self) -> None:
        from control_plane.lifecycle import TaskLease, TaskStore

        branch = "codex/verifier-recovery"
        task_id = "TASK-VERIFY-RECOVERY"
        worktree = "/repo/verifier-recovery"
        session = "session-verifier-recovery"
        store = TaskStore(self.state_dir)
        store.start(
            task_id,
            outcome="local_change",
            branch=branch,
            task_digest=self.digest,
            decision_digest=self.digest,
        )
        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
        ):
            state = store.transition(
                task_id,
                target,
                evidence=evidence,
                current_branch=branch,
            )
        lease = TaskLease.acquire(
            self.state_dir,
            task_id=task_id,
            worktree=worktree,
            branch=branch,
            session_id=session,
            paths=["."],
            policy_digest=self.digest,
        )
        with (
            patch.object(
                TaskLease,
                "_release_locked",
                side_effect=RuntimeError("injected-verifier-abort"),
            ),
            self.assertRaisesRegex(RuntimeError, "injected-verifier-abort"),
        ):
            store.abort_verification(
                task_id=task_id,
                expected_generation=state["generation"],
                task_digest=self.digest,
                repo=worktree,
                worktree=worktree,
                branch=branch,
                session_id=session,
                lease_digest=lease["lease_digest"],
                reason_code="E_VERIFICATION_UNKNOWN",
            )

        recovered = TaskStore.recover_verification_abort(
            task_id=task_id,
            state_dir=self.state_dir,
            common_dir=self.state_dir,
        )
        self.assertEqual(recovered["state"], "blocked")
        self.assertTrue(recovered["verification_aborted"])


    def test_clear_task_can_transition_from_framed_to_planned(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = TaskStore(self.state_dir)
        store.start(
            "TASK-CLEAR-ORDINAL",
            outcome="answer",
            branch="codex/clear-ordinal",
            task_digest=self.digest,
            decision_digest=self.digest,
        )

        state = store.transition(
            "TASK-CLEAR-ORDINAL",
            "planned",
            current_branch="codex/clear-ordinal",
        )

        self.assertEqual(state["state"], "planned")
        self.assertEqual(state["generation"], 1)


    def test_outcome_limits_use_ordered_states_and_blocked_is_lateral(self) -> None:
        from control_plane.lifecycle import (
            ORDERED_STATES,
            OUTCOME_LIMITS,
        )

        self.assertNotIn("blocked", ORDERED_STATES)
        for terminal in OUTCOME_LIMITS.values():
            self.assertIn(terminal, ORDERED_STATES)

if __name__ == "__main__":
    unittest.main()
