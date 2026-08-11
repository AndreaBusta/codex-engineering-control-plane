from __future__ import annotations

import copy
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.git_test_support import FIXTURE_POLICY, GitScenario, git
from tests.router_test_support import task_envelope


class IndependentReviewContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()
        self.addCleanup(self.scenario.close)
        self.scenario.checkout_feature("codex/review-packet")
        policy = self.scenario.repo / ".codex" / "project-policy.toml"
        policy.parent.mkdir()
        policy.write_text(FIXTURE_POLICY.read_text(encoding="utf-8"), encoding="utf-8")
        (self.scenario.repo / "change.txt").write_text("change\n", encoding="utf-8")
        git(self.scenario.repo, "add", ".codex")
        git(self.scenario.repo, "commit", "-m", "test: review policy")
        from control_plane.repository import worktree_git_dir
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.policy import load_policy
        from control_plane.run_workflow import RunStore, ReviewArtifactStore, build_gate_receipt, build_run_plan
        from control_plane.contracts import contract_digest

        task = task_envelope(task_id="TASK-REVIEW-001", scope_paths=["change.txt"])
        decision = {
            "decision_digest": "sha256:" + "1" * 64, "decision_ready": True,
            "summary": {"tier": "T2", "project_profile": {"profiles": ["generic"]}},
            "required_gates": ["gate.relevant-tests", "gate.independent-review"],
            "approval_boundaries": [],
            "authorization": {"local_write": True},
            "errors": [],
            "interaction": {"clarification_gate": {"level": "low", "status": "autonomous", "decision_ready": True}},
        }
        self.plan = build_run_plan(task=task, decision=decision, repository=self.scenario.repo,
            branch="codex/review-packet", head=git(self.scenario.repo, "rev-parse", "HEAD"),
            session_id="session-review-001", prepared_at="2026-08-08T10:00:00Z")
        self.store = RunStore(worktree_git_dir(self.scenario.repo))
        self.store.write_plan(self.plan)
        self.revision = self.store.write_initial_revision(self.plan)
        state = TaskStore(worktree_git_dir(self.scenario.repo))
        state.start(task["task_id"], outcome="local_change", branch="codex/review-packet",
            task_digest=self.plan["task_digest"], decision_digest=self.plan["decision_digest"])
        for target, evidence in (("planned", None), ("ready", {"preflight_ok": True}),
                                 ("implementing", None), ("verifying", {"implementation_complete": True})):
            state.transition(task["task_id"], target, evidence=evidence, current_branch="codex/review-packet")
        state.bind_active_run_revision(task["task_id"], run_plan_digest=self.plan["plan_digest"],
            revision_digest=self.revision["revision_digest"], current_branch="codex/review-packet")
        TaskLease.acquire(worktree_git_dir(self.scenario.repo), task_id=task["task_id"],
            worktree=str(self.scenario.repo), branch="codex/review-packet", session_id="session-review-001",
            paths=["change.txt"], policy_digest=contract_digest(load_policy(policy)))
        receipts = tuple(build_gate_receipt(run_plan=self.plan, attempt=1, gate_id=gate_id,
            status="PASS", command_digest=contract_digest({"argv": gate_id}),
            output_digest=contract_digest({"output": gate_id}),
            before_snapshot_digest=contract_digest({"before": gate_id}),
            after_snapshot_digest=contract_digest({"after": gate_id}), error_code=None,
            observed_at="2026-08-08T10:01:00Z") for gate_id in (
                "gate.relevant-tests", "gate.policy-check", "gate.registry-check", "gate.doctor", "gate.diff-review"))
        self.attempt = self.store.record_attempt(run_plan=self.plan, run_revision=self.revision,
            attempt=1, head=self.revision["head"], changed_paths=("change.txt",), receipts=receipts,
            failure_reason_code=None, observed_at="2026-08-08T10:01:00Z")
        ReviewArtifactStore(self.scenario.repo).create_from_repository(self.scenario.repo, task["task_id"], 1)

    def _packet(self, kind: str = "independent") -> dict[str, object]:
        from control_plane.run_workflow import prepare_review_packet
        return prepare_review_packet(self.scenario.repo, "TASK-REVIEW-001", 1, kind, "sha256:" + "2" * 64)

    def test_local_review_revision_exposes_only_the_same_head_entrypoint(self) -> None:
        from control_plane.lifecycle import TaskStore
        from control_plane.run_workflow import RunStore

        legacy_entrypoint = "return_to_implementation" + "_after_review"
        self.assertFalse(hasattr(TaskStore, legacy_entrypoint))
        self.assertFalse(hasattr(RunStore, "append_review_revision"))
        self.assertTrue(hasattr(TaskStore, "start_local_review_revision"))

    def test_active_revision_cas_cannot_activate_a_review_correction(self) -> None:
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir

        tasks = TaskStore(worktree_git_dir(self.scenario.repo))
        with self.assertRaisesRegex(ValueError, "E_STATE_CAS"):
            tasks.bind_active_run_revision(
                "TASK-REVIEW-001",
                run_plan_digest=self.plan["plan_digest"],
                revision_digest="sha256:" + "f" * 64,
                expected_active_revision_digest=self.revision[
                    "revision_digest"
                ],
                current_branch="codex/review-packet",
            )

    def test_task_store_exposes_only_proven_review_ready_finalization(self) -> None:
        from control_plane.lifecycle import TaskStore

        self.assertFalse(hasattr(TaskStore, "publish_review_ready"))
        self.assertTrue(hasattr(TaskStore, "finalize_review_ready"))

    def test_prepare_review_packet_is_closed_persisted_and_non_authorizing(self) -> None:
        from control_plane.run_workflow import validate_review_packet
        packet = self._packet()
        self.assertEqual(validate_review_packet(packet), [])
        self.assertEqual(packet["kind"], "ReviewPacketV1")
        self.assertFalse(packet["authorizes"])
        self.assertEqual(len(packet["evidence_summaries"]), 5)
        self.assertEqual(self._packet(), packet)

    def test_packet_replay_requires_identical_persisted_bytes(self) -> None:
        packet = self._packet()
        path = self.store._review_packet_path("TASK-REVIEW-001", 1, "independent")
        path.write_text(__import__("json").dumps(packet, sort_keys=True), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "E_REVIEW_PACKET"):
            self._packet()

    def test_prepare_rejects_missing_gate_and_artifact_drift(self) -> None:
        from control_plane.run_workflow import prepare_review_packet, ReviewArtifactStore
        path = self.store._directory("TASK-REVIEW-001") / "attempt-1.json"
        record = self.store.attempts("TASK-REVIEW-001")[0]
        record["gate_receipt_digests"] = record["gate_receipt_digests"][:-1]
        from control_plane.contracts import contract_digest
        record["attempt_digest"] = contract_digest({key: value for key, value in record.items() if key != "attempt_digest"})
        path.write_text(__import__("json").dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "E_REVIEW_PACKET"):
            prepare_review_packet(self.scenario.repo, "TASK-REVIEW-001", 1, "independent", "sha256:" + "2" * 64)
        # Restore a valid attempt then prove a missing bounded artifact also blocks.
        path.write_text(__import__("json").dumps(self.attempt), encoding="utf-8")
        manifest = ReviewArtifactStore(self.scenario.repo).load_manifest("TASK-REVIEW-001", 1)
        ReviewArtifactStore(self.scenario.repo).delete_exact(manifest)
        with self.assertRaisesRegex(ValueError, "E_REVIEW_ARTIFACT"):
            prepare_review_packet(self.scenario.repo, "TASK-REVIEW-001", 1, "independent", "sha256:" + "2" * 64)

    def test_artifact_manifest_rejects_a_leaf_symlink_and_oversized_inventory(self) -> None:
        from unittest.mock import patch
        from control_plane.run_workflow import ReviewArtifactStore, _git_untracked_paths

        artifact = ReviewArtifactStore(self.scenario.repo)
        manifest = artifact.load_manifest("TASK-REVIEW-001", 1)
        path = artifact.manifest_path(manifest)
        path.unlink()
        path.symlink_to(self.scenario.repo / "change.txt")
        with self.assertRaisesRegex(ValueError, "E_REVIEW_ARTIFACT"):
            artifact.load_manifest("TASK-REVIEW-001", 1)
        path.unlink()
        path.write_text(__import__("json").dumps(manifest), encoding="utf-8")
        path.chmod(0o600)
        (self.scenario.repo / "many-untracked-paths.txt").write_text("x\n", encoding="utf-8")
        with patch("control_plane.run_workflow.MAX_REVIEW_PACKET_BYTES", 8):
            with self.assertRaisesRegex(ValueError, "untracked inventory exceeds byte cap"):
                _git_untracked_paths(self.scenario.repo)
        task_dir = path.parent.parent
        moved = task_dir.with_name(task_dir.name + "-moved")
        task_dir.rename(moved)
        task_dir.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "E_REVIEW_ARTIFACT"):
            artifact.load_manifest("TASK-REVIEW-001", 1)

    def test_artifact_creation_rejects_unsafe_untracked_permissions(self) -> None:
        import stat
        from control_plane.run_workflow import ReviewArtifactStore

        artifact = ReviewArtifactStore(self.scenario.repo)
        manifest = artifact.load_manifest("TASK-REVIEW-001", 1)
        artifact.delete_exact(manifest)
        source = self.scenario.repo / "change.txt"
        source_mode = stat.S_IMODE(source.stat().st_mode)
        repository_mode = stat.S_IMODE(self.scenario.repo.stat().st_mode)
        self.addCleanup(source.chmod, source_mode)
        self.addCleanup(self.scenario.repo.chmod, repository_mode)

        source.chmod(0o666)
        with self.assertRaisesRegex(ValueError, "E_REVIEW_ARTIFACT"):
            artifact.create_from_repository(
                self.scenario.repo, "TASK-REVIEW-001", 1
            )

        source.chmod(source_mode)
        self.scenario.repo.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "E_REVIEW_ARTIFACT"):
            artifact.create_from_repository(
                self.scenario.repo, "TASK-REVIEW-001", 1
            )

    def test_artifact_delete_classifies_partial_absent_and_drift(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        artifact = ReviewArtifactStore(self.scenario.repo)
        manifest = artifact.load_manifest("TASK-REVIEW-001", 1)
        directory = artifact.manifest_path(manifest).parent
        (directory / "review.diff").unlink()
        self.assertEqual(artifact.artifact_state(manifest), "partial")
        self.assertEqual(artifact.delete_exact(manifest), "absent")
        self.assertEqual(artifact.artifact_state(manifest), "absent")
        self.assertEqual(artifact.delete_exact(manifest), "absent")

    def test_artifact_cap_is_global_across_multiple_untracked_diffs(self) -> None:
        from unittest.mock import patch
        from control_plane.run_workflow import ReviewArtifactStore

        artifact = ReviewArtifactStore(self.scenario.repo)
        (self.scenario.repo / "second.txt").write_text("second\n", encoding="utf-8")
        first = artifact._capture_untracked_diff("change.txt", 1_048_576)
        second = artifact._capture_untracked_diff("second.txt", 1_048_576)
        with patch("control_plane.run_workflow.MAX_REVIEW_DIFF_BYTES", len(first) + len(second) - 1):
            with self.assertRaisesRegex(ValueError, "diff exceeds byte cap"):
                artifact._capture_diff(
                    self.revision["head"], ("change.txt", "second.txt"),
                )

    def test_artifact_capture_disables_configured_textconv(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        tracked = self.scenario.repo / "tracked.txt"
        attributes = self.scenario.repo / ".gitattributes"
        marker = self.scenario.repo / "textconv-executed.marker"
        converter = self.scenario.repo / "textconv-converter.py"
        tracked.write_text("before\n", encoding="utf-8")
        attributes.write_text("tracked.txt diff=control-plane-test\n", encoding="utf-8")
        converter.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"Path({str(marker)!r}).write_text('executed\\n', encoding='utf-8')\n"
            "sys.stdout.write(Path(sys.argv[1]).read_text(encoding='utf-8'))\n",
            encoding="utf-8",
        )
        converter.chmod(0o700)
        git(self.scenario.repo, "add", "tracked.txt", ".gitattributes")
        git(self.scenario.repo, "commit", "-m", "test: textconv baseline")
        reviewed_head = git(self.scenario.repo, "rev-parse", "HEAD")
        git(
            self.scenario.repo,
            "config",
            "diff.control-plane-test.textconv",
            str(converter),
        )
        tracked.write_text("after\n", encoding="utf-8")

        captured = ReviewArtifactStore(self.scenario.repo)._capture_diff(
            reviewed_head, ("tracked.txt",),
        )

        self.assertFalse(marker.exists())
        self.assertIn(b"+after", captured)

    def test_artifact_capture_ignores_replace_ref_that_hides_tracked_change(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        tracked = self.scenario.repo / "tracked.txt"
        tracked.write_text("before\n", encoding="utf-8")
        git(self.scenario.repo, "add", "tracked.txt")
        git(self.scenario.repo, "commit", "-m", "test: replace-ref baseline")
        reviewed_head = git(self.scenario.repo, "rev-parse", "HEAD")
        tracked.write_text("after\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment["GIT_INDEX_FILE"] = str(Path(temporary) / "replacement.index")
            for arguments in (("read-tree", "HEAD"), ("add", "--", "tracked.txt")):
                subprocess.run(
                    ["git", "-C", str(self.scenario.repo), *arguments],
                    check=True,
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            tree = subprocess.run(
                ["git", "-C", str(self.scenario.repo), "write-tree"],
                check=True,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout.strip()
        replacement = git(self.scenario.repo, "commit-tree", tree)
        git(self.scenario.repo, "replace", reviewed_head, replacement)

        captured = ReviewArtifactStore(self.scenario.repo)._capture_diff(
            reviewed_head, ("tracked.txt",),
        )

        self.assertIn(b"-before", captured)
        self.assertIn(b"+after", captured)

    def test_artifact_capture_ignores_path_and_external_git_config(self) -> None:
        from unittest.mock import patch
        from control_plane.run_workflow import ReviewArtifactStore

        tracked = self.scenario.repo / "trusted-git.txt"
        tracked.write_text("before\n", encoding="utf-8")
        git(self.scenario.repo, "add", "trusted-git.txt")
        git(self.scenario.repo, "commit", "-m", "test: trusted git baseline")
        reviewed_head = git(self.scenario.repo, "rev-parse", "HEAD")
        tracked.write_text("after\n", encoding="utf-8")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "ambient-git-executed"
            fake_git = root / "git"
            fake_git.write_text(
                f"#!/bin/sh\n: > {str(marker)!r}\nexit 0\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o700)
            external_config = root / "gitconfig"
            external_config.write_text(
                "[diff]\n\tnoprefix = true\n", encoding="utf-8"
            )
            with patch.dict(
                os.environ,
                {
                    "PATH": str(root),
                    "GIT_CONFIG_GLOBAL": str(external_config),
                },
                clear=False,
            ):
                captured = ReviewArtifactStore(
                    self.scenario.repo
                )._capture_diff(reviewed_head, ("trusted-git.txt",))
            ambient_git_executed = marker.exists()

        self.assertFalse(ambient_git_executed)
        self.assertIn(
            b"diff --git a/trusted-git.txt b/trusted-git.txt", captured
        )
        self.assertIn(b"+after", captured)

    def test_artifact_capture_blocks_clean_and_process_filters_before_execution(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        attributes = self.scenario.repo / ".gitattributes"
        clean_path = self.scenario.repo / "filter-clean.txt"
        process_path = self.scenario.repo / "filter-process.txt"
        clean_path.write_text("before clean\n", encoding="utf-8")
        process_path.write_text("before process\n", encoding="utf-8")
        attributes.write_text(
            "filter-clean.txt filter=review-clean\n"
            "filter-process.txt filter=review-process\n",
            encoding="utf-8",
        )
        git(
            self.scenario.repo,
            "add",
            ".gitattributes",
            "filter-clean.txt",
            "filter-process.txt",
        )
        git(self.scenario.repo, "commit", "-m", "test: filter baseline")
        reviewed_head = git(self.scenario.repo, "rev-parse", "HEAD")

        clean_marker = self.scenario.repo / "clean-filter-executed"
        process_marker = self.scenario.repo / "process-filter-executed"
        clean_filter = self.scenario.repo / "clean-filter.sh"
        process_filter = self.scenario.repo / "process-filter.sh"
        clean_filter.write_text(
            f"#!/bin/sh\n: > {str(clean_marker)!r}\ncat\n",
            encoding="utf-8",
        )
        process_filter.write_text(
            f"#!/bin/sh\n: > {str(process_marker)!r}\nexit 1\n",
            encoding="utf-8",
        )
        clean_filter.chmod(0o700)
        process_filter.chmod(0o700)
        git(
            self.scenario.repo,
            "config",
            "filter.review-clean.clean",
            str(clean_filter),
        )
        git(
            self.scenario.repo,
            "config",
            "filter.review-process.process",
            str(process_filter),
        )
        git(
            self.scenario.repo,
            "config",
            "filter.review-process.required",
            "true",
        )
        clean_path.write_text("after clean\n", encoding="utf-8")
        process_path.write_text("after process\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "E_GIT_FILTER"):
            ReviewArtifactStore(self.scenario.repo)._capture_diff(
                reviewed_head,
                ("filter-clean.txt", "filter-process.txt"),
            )

        self.assertFalse(clean_marker.exists())
        self.assertFalse(process_marker.exists())

    def test_artifact_git_subprocess_does_not_receive_sensitive_environment(self) -> None:
        from unittest.mock import patch
        from control_plane.run_workflow import ReviewArtifactStore

        tracked = self.scenario.repo / "environment.txt"
        tracked.write_text("before\n", encoding="utf-8")
        git(self.scenario.repo, "add", "environment.txt")
        git(self.scenario.repo, "commit", "-m", "test: environment baseline")
        reviewed_head = git(self.scenario.repo, "rev-parse", "HEAD")
        tracked.write_text("after\n", encoding="utf-8")
        observed_environments: list[dict[str, str]] = []
        real_popen = subprocess.Popen

        def observe(*args, **kwargs):
            observed_environments.append(dict(kwargs.get("env", {})))
            return real_popen(*args, **kwargs)

        sensitive = {
            "AWS_SECRET_ACCESS_KEY": "canary-not-a-secret",
            "GH_TOKEN": "canary-not-a-secret",
            "HTTPS_PROXY": "http://canary.invalid",
            "SSH_AUTH_SOCK": "/tmp/canary-agent.sock",
        }
        with patch.dict(os.environ, sensitive, clear=False), patch(
            "control_plane.run_workflow.subprocess.Popen",
            side_effect=observe,
        ):
            captured = ReviewArtifactStore(
                self.scenario.repo
            )._capture_diff(reviewed_head, ("environment.txt",))

        self.assertIn(b"+after", captured)
        self.assertTrue(observed_environments)
        for environment in observed_environments:
            self.assertFalse(set(sensitive).intersection(environment))

    def test_untracked_capture_completes_short_writes_and_rejects_zero(self) -> None:
        import control_plane.run_workflow as run_workflow
        from unittest.mock import patch
        from control_plane.run_workflow import ReviewArtifactStore

        artifact = ReviewArtifactStore(self.scenario.repo)
        original_write = run_workflow.os.write
        first_write = True

        def short_once(descriptor: int, payload: bytes) -> int:
            nonlocal first_write
            if first_write:
                first_write = False
                return original_write(descriptor, payload[:1])
            return original_write(descriptor, payload)

        with patch.object(run_workflow.os, "write", side_effect=short_once):
            captured = artifact._capture_untracked_diff(
                "change.txt", 1_048_576,
            )
        self.assertIn(b"+change", captured)

        with patch.object(run_workflow.os, "write", return_value=0):
            with self.assertRaisesRegex(ValueError, "artifact write failed"):
                artifact._capture_untracked_diff("change.txt", 1_048_576)

    def test_artifact_delete_recovers_from_each_material_syscall_fault(self) -> None:
        """Fault injection exercises real unlink/rmdir/fsync boundaries."""
        import errno
        import os
        from unittest.mock import patch
        from control_plane.run_workflow import ReviewArtifactStore

        artifact = ReviewArtifactStore(self.scenario.repo)
        manifest = artifact.load_manifest("TASK-REVIEW-001", 1)
        originals = {"unlink": os.unlink, "rmdir": os.rmdir, "fsync": os.fsync}
        cases = (
            ("unlink", "review.diff", True), ("unlink", "manifest.json", True),
            ("rmdir", "attempt-1", True), ("rmdir", "TASK-REVIEW-001", False),
            ("fsync", None, True),
        )
        for syscall, target, raises in cases:
            with self.subTest(syscall=syscall, target=target):
                if artifact.artifact_state(manifest) == "absent":
                    artifact.create_from_repository(self.scenario.repo, "TASK-REVIEW-001", 1)
                fired = False
                def fault(*args, **kwargs):
                    nonlocal fired
                    if not fired and (target is None or args[0] == target):
                        fired = True
                        raise OSError(errno.EIO, "injected delete fault")
                    return originals[syscall](*args, **kwargs)
                with patch(f"control_plane.run_workflow.os.{syscall}", side_effect=fault):
                    if raises:
                        with self.assertRaises(ValueError):
                            artifact.delete_exact(manifest)
                    else:
                        self.assertEqual(artifact.delete_exact(manifest), "absent")
                self.assertIn(artifact.artifact_state(manifest), {"present", "partial", "absent"})
                self.assertEqual(artifact.delete_exact(manifest), "absent")
                self.assertEqual(artifact.artifact_state(manifest), "absent")

    def test_partial_leaf_cleanup_never_deletes_a_replacement(self) -> None:
        from control_plane.run_workflow import ReviewArtifactStore

        with tempfile.TemporaryDirectory() as temporary:
            replacement_path = Path(temporary) / "leaf"
            replacement_path.write_bytes(b"replacement")
            replacement_path.chmod(0o600)
            directory = os.open(
                temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                with self.assertRaisesRegex(
                    ValueError, "artifact write failed"
                ):
                    ReviewArtifactStore._write_leaf(
                        directory, "leaf", b"owned"
                    )
                self.assertEqual(
                    replacement_path.read_bytes(), b"replacement"
                )
                self.assertEqual(
                    [path.name for path in Path(temporary).iterdir()],
                    ["leaf"],
                )
            finally:
                os.close(directory)

    def test_receipt_binds_persisted_packet_and_counts(self) -> None:
        from control_plane.run_workflow import build_independent_review_receipt, validate_independent_review_receipt
        from tests.host_adapter_test_support import independent_review_receipt

        packet = self._packet()
        receipt, observation = independent_review_receipt(
            run_store=self.store,
            review_packet=packet,
            findings_digest="sha256:" + "3" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        self.assertEqual(validate_independent_review_receipt(receipt), [])
        self.assertEqual(
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                receipt,
                observation=observation,
            ),
            receipt,
        )
        self.assertFalse(
            {"session_id", "invocation_id", "nonce", "ttl_seconds"}
            & set(receipt)
        )
        self.assertEqual(
            {"reviewer_identity_digest", "observation_digest"}
            & set(receipt),
            {"reviewer_identity_digest", "observation_digest"},
        )
        with self.assertRaisesRegex(ValueError, "E_INDEPENDENT_REVIEW"):
            build_independent_review_receipt(
                review_packet=packet,
                findings_digest="sha256:" + "3" * 64,
                critical=1,
                important=0,
                status="PASS",
                observed_at="2026-08-08T10:02:00Z",
                observation=observation,
            )
        changed = copy.deepcopy(receipt)
        changed["branch"] = "codex/other"
        with self.assertRaisesRegex(ValueError, "E_INDEPENDENT_REVIEW"):
            self.store.persist_review_receipt("TASK-REVIEW-001", packet["packet_digest"], changed)

    def test_native_review_session_is_independent_of_local_run_correlator(self) -> None:
        from tests.host_adapter_test_support import independent_review_receipt

        packet = self._packet()
        self.assertEqual(self.plan["session_id"], "session-review-001")
        receipt, observation = independent_review_receipt(
            run_store=self.store,
            review_packet=packet,
            findings_digest="sha256:" + "7" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:00Z",
            native_session_id="native-review-session-001",
            invocation_id="native-review-invocation-001",
        )
        self.assertEqual(observation.session_id, "native-review-session-001")
        self.assertNotEqual(observation.session_id, self.plan["session_id"])

        self.assertEqual(
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                receipt,
                observation=observation,
            ),
            receipt,
        )

    def test_scalar_review_receipt_cannot_be_persisted_without_host_observation(self) -> None:
        from control_plane.run_workflow import build_independent_review_receipt
        from tests.host_adapter_test_support import independent_review_receipt

        packet = self._packet()
        receipt = build_independent_review_receipt(
            review_packet=packet,
            findings_digest="sha256:" + "8" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        with self.assertRaisesRegex(
            ValueError, "E_INDEPENDENT_REVIEW_OBSERVATION"
        ):
            self.store.persist_review_receipt(
                "TASK-REVIEW-001", packet["packet_digest"], receipt
            )
        observed_receipt, observation = independent_review_receipt(
            run_store=self.store,
            review_packet=packet,
            findings_digest="sha256:" + "8" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        with self.assertRaisesRegex(
            ValueError, "E_INDEPENDENT_REVIEW_OBSERVATION"
        ):
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                observed_receipt,
            )
        self.store.persist_review_receipt(
            "TASK-REVIEW-001",
            packet["packet_digest"],
            observed_receipt,
            observation=observation,
        )

    def test_host_review_observation_is_opaque_exact_fresh_and_one_shot(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from tests.host_adapter_test_support import independent_review_receipt

        with self.assertRaisesRegex(TypeError, "host-bound"):
            bridge.IndependentReviewObservation()
        with self.assertRaisesRegex(TypeError, "host-bound"):
            bridge.ValidatedIndependentReviewObservation()

        packet = self._packet()
        receipt, observation = independent_review_receipt(
            run_store=self.store,
            review_packet=packet,
            findings_digest="sha256:" + "9" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        drifted = dict(receipt)
        drifted["findings_digest"] = "sha256:" + "a" * 64
        drifted["receipt_digest"] = contract_digest(
            {
                key: value
                for key, value in drifted.items()
                if key != "receipt_digest"
            }
        )
        with self.assertRaisesRegex(
            ValueError, "E_INDEPENDENT_REVIEW_OBSERVATION"
        ):
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                drifted,
                observation=observation,
            )
        self.store.persist_review_receipt(
            "TASK-REVIEW-001",
            packet["packet_digest"],
            receipt,
            observation=observation,
        )
        with self.assertRaisesRegex(
            ValueError, "E_INDEPENDENT_REVIEW_OBSERVATION"
        ):
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                receipt,
                observation=observation,
            )

    def test_stale_review_observation_and_post_consume_fault_fail_closed(self) -> None:
        from unittest.mock import patch
        from tests.host_adapter_test_support import independent_review_receipt

        packet = self._packet()
        receipt, observation = independent_review_receipt(
            run_store=self.store,
            review_packet=packet,
            findings_digest="sha256:" + "b" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:00Z",
        )
        observation._clock = lambda: 1_000.0
        with self.assertRaisesRegex(
            ValueError, "E_INDEPENDENT_REVIEW_OBSERVATION"
        ):
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                receipt,
                observation=observation,
            )

        receipt, observation = independent_review_receipt(
            run_store=self.store,
            review_packet=packet,
            findings_digest="sha256:" + "c" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-08T10:02:30Z",
        )
        with patch(
            "control_plane.run_workflow._atomic_json",
            side_effect=OSError("fault after proof consumption"),
        ), self.assertRaisesRegex(OSError, "fault after proof consumption"):
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                receipt,
                observation=observation,
            )
        self.assertFalse(
            self.store._review_receipt_path(
                "TASK-REVIEW-001", 1, "independent"
            ).exists()
        )
        with self.assertRaisesRegex(
            ValueError, "E_INDEPENDENT_REVIEW_OBSERVATION"
        ):
            self.store.persist_review_receipt(
                "TASK-REVIEW-001",
                packet["packet_digest"],
                receipt,
                observation=observation,
            )


if __name__ == "__main__":
    unittest.main()
