from __future__ import annotations

import copy
from hashlib import sha256
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


FIXTURE = Path(__file__).parent / "fixtures" / "valid-policy.toml"


def _complete_policy_recovery(
    root_value: str,
    proposed_value: str,
    governing_policy_value: str,
    policy_digest: str,
    head: str,
    session_id: str,
    invocation_id: str,
    task_id: str,
) -> None:
    import json
    import control_plane.host_bridge as bridge
    from control_plane.policy import (
        apply_project_remote_policy_update,
        frame_project_remote_policy_decision,
        load_policy,
        project_remote_policy_update_plan,
    )
    from control_plane.repository import worktree_git_dir
    from tests.host_adapter_test_support import (
        governing_policy,
        governing_runtime_observation,
        native_session_event,
        native_user_interaction_event,
    )

    root = Path(root_value)
    proposed = Path(proposed_value)
    state_dir = worktree_git_dir(root)
    state = json.loads(
        (
            state_dir
            / "codex-control-plane"
            / "tasks"
            / f"{task_id}.json"
        ).read_text(encoding="utf-8")
    )
    lease = json.loads(
        (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{task_id}.json"
        ).read_text(encoding="utf-8")
    )
    runtime = governing_runtime_observation(
        runtime_digest=policy_digest,
        lock_digest=policy_digest,
        policy_digest=policy_digest,
        attestor_worktree=str(root.resolve()),
        target_worktree=str(root.resolve()),
        governing_base_commit=head,
        runtime_layout="source",
        session_id=session_id,
        invocation_id=invocation_id,
        freshness_deadline=130.0,
    )
    policy = governing_policy(
        policy=load_policy(Path(governing_policy_value)),
        policy_digest=policy_digest,
        runtime_digest=policy_digest,
        lock_digest=policy_digest,
        governing_base_commit=head,
        session_id=session_id,
        invocation_id=invocation_id,
        freshness_deadline=130.0,
    )
    draft = project_remote_policy_update_plan(
        governing_runtime=runtime,
        governing_policy=policy,
        candidate_policy_path=proposed,
        task_context={**state, "lease_digest": lease["lease_digest"]},
        lease=lease,
        repository_identity=str(root.resolve()),
        required_checks=(),
    )
    decision_capability = bridge.attest_host_adapter_capability(
        native_session_event(
            event_id="recovery-session",
            session_id=session_id,
            invocation_id=invocation_id,
            observed_at_monotonic=100.0,
        ),
        expected_session_id=session_id,
        expected_invocation_id=invocation_id,
        clock=lambda: 100.0,
        ttl_seconds=30,
    )
    decision = frame_project_remote_policy_decision(
        native_user_interaction_event(
            event_id="recovery-decision",
            session_id=session_id,
            invocation_id=invocation_id,
            task_digest=policy_digest,
            subject_digest=draft.draft_digest,
            observed_at_monotonic=100.0,
        ),
        governing_runtime=runtime,
        host_capability=decision_capability,
        operation_kind="policy_update",
        draft_plan_digest=draft.draft_digest,
        source_repository_identity=str(root.resolve()),
        target_repository_identity=str(root.resolve()),
        target_worktree_identity=str(root.resolve()),
        repository_identity=str(root.resolve()),
        required_checks=(),
        session_id=session_id,
        invocation_id=invocation_id,
        clock=lambda: 100.0,
        ttl_seconds=30,
    )
    write_capability = bridge.attest_host_adapter_capability(
        native_session_event(
            event_id="recovery-write-session",
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
            event_id="recovery-write",
            session_id=session_id,
            invocation_id=invocation_id,
            task_digest=policy_digest,
            subject_digest=draft.draft_digest,
            observed_at_monotonic=100.0,
        ),
        host_capability=write_capability,
        task_digest=policy_digest,
        session_id=session_id,
        repository_identity=root,
        worktree_identity=root,
        branch="main",
        expected_head=head,
        subject_digest=draft.draft_digest,
        scope_paths=(".codex/project-policy.toml",),
        effect="local_write",
        operation_nonce="tool-policy-recovery",
        invocation_id=invocation_id,
        clock=lambda: 100.0,
        ttl_seconds=30,
    )
    receipt = apply_project_remote_policy_update(
        draft,
        governing_runtime=runtime,
        remote_policy_decision=decision,
        authorization=authorization,
        expected_generation=draft.generation,
        clock=lambda: 100.0,
    )
    print(receipt.generation)


class PolicyContractTests(unittest.TestCase):
    def test_governing_policy_has_a_task1_loader_bound_to_clean_base_runtime(
        self,
    ) -> None:
        from control_plane.policy import load_governing_policy_from_runtime
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
        )

        with self.assertRaisesRegex(ValueError, "E_GOVERNING_POLICY"):
            load_governing_policy_from_runtime(
                {"attestor_worktree": str(FIXTURE.parent)},
                session_id="session-policy",
                invocation_id="policy-load",
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            policy_path = root / ".codex" / "project-policy.toml"
            policy_path.parent.mkdir()
            policy_path.write_bytes(FIXTURE.read_bytes())
            digest = (
                "sha256:" + sha256(policy_path.read_bytes()).hexdigest()
            )
            runtime = governing_runtime_observation(
                runtime_digest=digest,
                lock_digest=digest,
                policy_digest=digest,
                attestor_worktree=str(root),
                target_worktree=str(root),
                governing_base_commit="a" * 40,
                runtime_layout="source",
                session_id="session-policy",
                invocation_id="policy-load",
                freshness_deadline=130.0,
            )
            forged_runtime = object.__new__(type(runtime))
            for name in type(runtime).__slots__:
                setattr(forged_runtime, name, getattr(runtime, name))
            with self.assertRaisesRegex(ValueError, "E_GOVERNING_POLICY"):
                load_governing_policy_from_runtime(
                    forged_runtime,
                    session_id="session-policy",
                    invocation_id="policy-load",
                    clock=lambda: 100.0,
                    ttl_seconds=30,
                )
            policy = load_governing_policy_from_runtime(
                runtime,
                session_id="session-policy",
                invocation_id="policy-load",
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            self.assertEqual(policy.policy_digest, digest)
            self.assertTrue(runtime._consumed)

    def test_remote_policy_decision_and_policy_only_update_are_governing_base_owned(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.policy import (
            apply_project_remote_policy_update,
            frame_project_remote_policy_decision,
            load_policy,
            project_remote_policy_update_plan,
        )
        from control_plane.repository import worktree_git_dir
        from tests.host_adapter_test_support import (
            governing_policy,
            governing_runtime_observation,
            native_session_event,
            native_user_interaction_event,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repository"
            root.mkdir()
            subprocess.run(
                ["git", "init", "-b", "main", str(root)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["git", "-C", str(root), "config", "user.name", "Policy Tests"],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "config",
                    "user.email",
                    "tests@example.invalid",
                ],
                check=True,
            )
            target = root / ".codex" / "project-policy.toml"
            target.parent.mkdir()
            target.write_bytes(FIXTURE.read_bytes())
            subprocess.run(
                ["git", "-C", str(root), "add", ".codex/project-policy.toml"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(root), "commit", "-m", "policy baseline"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            head = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            proposed = Path(temp_dir) / "proposed-policy.toml"
            proposed.write_text(
                FIXTURE.read_text(encoding="utf-8").replace(
                    'remote = "origin"', 'remote = "upstream"'
                ),
                encoding="utf-8",
            )
            policy_digest = (
                "sha256:" + sha256(target.read_bytes()).hexdigest()
            )
            session_id = "session-policy-update"
            invocation_id = "invocation-policy-update"
            task_id = "TASK-POLICY-UPDATE"
            runtime = governing_runtime_observation(
                runtime_digest=policy_digest,
                lock_digest=policy_digest,
                policy_digest=policy_digest,
                attestor_worktree=str(root.resolve()),
                target_worktree=str(root.resolve()),
                governing_base_commit=head,
                runtime_layout="source",
                session_id=session_id,
                invocation_id=invocation_id,
                freshness_deadline=130.0,
            )
            policy = governing_policy(
                policy=load_policy(target),
                policy_digest=policy_digest,
                runtime_digest=policy_digest,
                lock_digest=policy_digest,
                governing_base_commit=head,
                session_id=session_id,
                invocation_id=invocation_id,
                freshness_deadline=130.0,
            )
            state_dir = worktree_git_dir(root)
            store = TaskStore(state_dir)
            state = store.start(
                task_id,
                outcome="local_change",
                branch="main",
                task_digest=policy_digest,
                decision_digest=policy_digest,
            )
            lease = TaskLease.acquire(
                state_dir,
                task_id=task_id,
                worktree=str(root),
                branch="main",
                session_id=session_id,
                paths=[".codex/project-policy.toml"],
                policy_digest=policy_digest,
            )
            task_context = {
                **state,
                "lease_digest": lease["lease_digest"],
            }
            draft = project_remote_policy_update_plan(
                governing_runtime=runtime,
                governing_policy=policy,
                candidate_policy_path=proposed,
                task_context=task_context,
                lease=lease,
                repository_identity=str(root.resolve()),
                required_checks=(),
            )
            forged_event = object.__new__(
                bridge.NativeUserInteractionEvent
            )
            forged_event._consumed = False
            forged_event.event_id = "forged-policy-event"
            forged_event.session_id = session_id
            forged_event.invocation_id = invocation_id
            forged_event.task_digest = policy_digest
            forged_event.subject_digest = draft.draft_digest
            forged_event.observed_at_monotonic = 100.0
            forged_capability = object.__new__(bridge.HostAdapterCapability)
            forged_capability._consumed = False
            forged_capability._clock = lambda: 100.0
            forged_capability.event_id = "forged-policy-capability"
            forged_capability.session_id = session_id
            forged_capability.invocation_id = invocation_id
            forged_capability.capability_nonce = "forged"
            forged_capability.freshness_deadline = 130.0
            with self.assertRaisesRegex(
                ValueError, "E_REMOTE_POLICY_DECISION"
            ):
                frame_project_remote_policy_decision(
                    forged_event,
                    governing_runtime=runtime,
                    host_capability=forged_capability,
                    operation_kind="policy_update",
                    draft_plan_digest=draft.draft_digest,
                    source_repository_identity=str(root.resolve()),
                    target_repository_identity=str(root.resolve()),
                    target_worktree_identity=str(root.resolve()),
                    repository_identity=str(root.resolve()),
                    required_checks=(),
                    session_id=session_id,
                    invocation_id=invocation_id,
                    clock=lambda: 100.0,
                    ttl_seconds=30,
                )
            decision_capability = bridge.attest_host_adapter_capability(
                native_session_event(
                    event_id="session-policy-decision",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    observed_at_monotonic=100.0,
                ),
                expected_session_id=session_id,
                expected_invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            decision = frame_project_remote_policy_decision(
                native_user_interaction_event(
                    event_id="confirm-policy-decision",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    task_digest=policy_digest,
                    subject_digest=draft.draft_digest,
                    observed_at_monotonic=100.0,
                ),
                governing_runtime=runtime,
                host_capability=decision_capability,
                operation_kind="policy_update",
                draft_plan_digest=draft.draft_digest,
                source_repository_identity=str(root.resolve()),
                target_repository_identity=str(root.resolve()),
                target_worktree_identity=str(root.resolve()),
                repository_identity=str(root.resolve()),
                required_checks=(),
                session_id=session_id,
                invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            def write_authorization(suffix: str):
                write_capability = bridge.attest_host_adapter_capability(
                    native_session_event(
                        event_id=f"session-policy-write-{suffix}",
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
                        event_id=f"authorize-policy-write-{suffix}",
                        session_id=session_id,
                        invocation_id=invocation_id,
                        task_digest=policy_digest,
                        subject_digest=draft.draft_digest,
                        observed_at_monotonic=100.0,
                    ),
                    host_capability=write_capability,
                    task_digest=policy_digest,
                    session_id=session_id,
                    repository_identity=root,
                    worktree_identity=root,
                    branch="main",
                    expected_head=head,
                    subject_digest=draft.draft_digest,
                    scope_paths=(".codex/project-policy.toml",),
                    effect="local_write",
                    operation_nonce="tool-policy-update",
                    invocation_id=invocation_id,
                    clock=lambda: 100.0,
                    ttl_seconds=30,
                )

            forged_decision = object.__new__(
                type(decision)
            )
            for name in type(decision).__slots__:
                setattr(forged_decision, name, getattr(decision, name))
            with self.assertRaisesRegex(
                ValueError, "E_REMOTE_POLICY_APPLY"
            ):
                apply_project_remote_policy_update(
                    draft,
                    governing_runtime=runtime,
                    remote_policy_decision=forged_decision,
                    authorization=write_authorization("forged-decision"),
                    expected_generation=0,
                    clock=lambda: 100.0,
                )

            state_path = (
                state_dir
                / "codex-control-plane"
                / "tasks"
                / f"{task_id}.json"
            )
            import control_plane.policy as policy_module

            with (
                patch(
                    "control_plane.policy._recover_policy_update_locked",
                    side_effect=RuntimeError(
                        "injected allocating phase crash"
                    ),
                ),
                self.assertRaisesRegex(
                    RuntimeError, "injected allocating phase crash"
                ),
            ):
                apply_project_remote_policy_update(
                    draft,
                    governing_runtime=runtime,
                    remote_policy_decision=decision,
                    authorization=write_authorization("initial"),
                    expected_generation=0,
                    clock=lambda: 100.0,
                )

            self.assertEqual(
                load_policy(target)["git"]["remote"], "origin"
            )
            self.assertEqual(store.status(task_id)["generation"], 0)
            recovery_program = """
import json
import subprocess
import sys
from pathlib import Path
import control_plane.host_bridge as bridge
from control_plane.policy import (
    apply_project_remote_policy_update,
    frame_project_remote_policy_decision,
    load_policy,
    project_remote_policy_update_plan,
)
from control_plane.repository import worktree_git_dir
from tests.host_adapter_test_support import (
    governing_policy,
    governing_runtime_observation,
    native_session_event,
    native_user_interaction_event,
)

root = Path(sys.argv[1])
proposed = Path(sys.argv[2])
policy_digest = sys.argv[3]
head = sys.argv[4]
session_id = sys.argv[5]
invocation_id = sys.argv[6]
task_id = sys.argv[7]
state_dir = worktree_git_dir(root)
state = json.loads(
    (state_dir / "codex-control-plane" / "tasks" / f"{task_id}.json")
    .read_text(encoding="utf-8")
)
lease = json.loads(
    (state_dir / "codex-control-plane" / "leases" / f"{task_id}.json")
    .read_text(encoding="utf-8")
)
transactions = state_dir / "codex-control-plane" / "policy-updates"
transaction = next(item for item in transactions.iterdir() if item.is_dir())
backup = transaction / "project-policy.before.toml"
governing_policy_path = (
    backup
    if backup.is_file()
    else root / ".codex" / "project-policy.toml"
)
runtime = governing_runtime_observation(
    runtime_digest=policy_digest,
    lock_digest=policy_digest,
    policy_digest=policy_digest,
    attestor_worktree=str(root.resolve()),
    target_worktree=str(root.resolve()),
    governing_base_commit=head,
    runtime_layout="source",
    session_id=session_id,
    invocation_id=invocation_id,
    freshness_deadline=130.0,
)
policy = governing_policy(
    policy=load_policy(governing_policy_path),
    policy_digest=policy_digest,
    runtime_digest=policy_digest,
    lock_digest=policy_digest,
    governing_base_commit=head,
    session_id=session_id,
    invocation_id=invocation_id,
    freshness_deadline=130.0,
)
draft = project_remote_policy_update_plan(
    governing_runtime=runtime,
    governing_policy=policy,
    candidate_policy_path=proposed,
    task_context={**state, "lease_digest": lease["lease_digest"]},
    lease=lease,
    repository_identity=str(root.resolve()),
    required_checks=(),
)
decision_capability = bridge.attest_host_adapter_capability(
    native_session_event(
        event_id="recovery-session",
        session_id=session_id,
        invocation_id=invocation_id,
        observed_at_monotonic=100.0,
    ),
    expected_session_id=session_id,
    expected_invocation_id=invocation_id,
    clock=lambda: 100.0,
    ttl_seconds=30,
)
decision = frame_project_remote_policy_decision(
    native_user_interaction_event(
        event_id="recovery-decision",
        session_id=session_id,
        invocation_id=invocation_id,
        task_digest=policy_digest,
        subject_digest=draft.draft_digest,
        observed_at_monotonic=100.0,
    ),
    governing_runtime=runtime,
    host_capability=decision_capability,
    operation_kind="policy_update",
    draft_plan_digest=draft.draft_digest,
    source_repository_identity=str(root.resolve()),
    target_repository_identity=str(root.resolve()),
    target_worktree_identity=str(root.resolve()),
    repository_identity=str(root.resolve()),
    required_checks=(),
    session_id=session_id,
    invocation_id=invocation_id,
    clock=lambda: 100.0,
    ttl_seconds=30,
)
write_capability = bridge.attest_host_adapter_capability(
    native_session_event(
        event_id="recovery-write-session",
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
        event_id="recovery-write",
        session_id=session_id,
        invocation_id=invocation_id,
        task_digest=policy_digest,
        subject_digest=draft.draft_digest,
        observed_at_monotonic=100.0,
    ),
    host_capability=write_capability,
    task_digest=policy_digest,
    session_id=session_id,
    repository_identity=root,
    worktree_identity=root,
    branch="main",
    expected_head=head,
    subject_digest=draft.draft_digest,
    scope_paths=(".codex/project-policy.toml",),
    effect="local_write",
    operation_nonce="tool-policy-recovery",
    invocation_id=invocation_id,
    clock=lambda: 100.0,
    ttl_seconds=30,
)
receipt = apply_project_remote_policy_update(
    draft,
    governing_runtime=runtime,
    remote_policy_decision=decision,
    authorization=authorization,
    expected_generation=0,
    clock=lambda: 100.0,
)
print(receipt.generation)
"""
            recovered = subprocess.run(
                [
                    "python3",
                    "-c",
                    recovery_program,
                    str(root),
                    str(proposed),
                    policy_digest,
                    head,
                    session_id,
                    invocation_id,
                    task_id,
                ],
                cwd=Path(__file__).parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(recovered.stdout.strip(), "1")

            self.assertEqual(
                load_policy(target)["git"]["remote"], "upstream"
            )
            self.assertEqual(store.status(task_id)["generation"], 1)
            transaction_root = (
                state_dir
                / "codex-control-plane"
                / "policy-updates"
                / draft.draft_digest.removeprefix("sha256:")
            )
            self.assertTrue(
                (transaction_root / "project-policy.before.toml").is_file()
            )
            self.assertTrue((transaction_root / "journal.json").is_file())
            with self.assertRaisesRegex(TypeError, "host-bound"):
                type(draft)()

    def test_policy_recovery_completes_every_durable_phase_in_a_new_process(
        self,
    ) -> None:
        import json
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.policy import (
            load_policy,
            project_remote_policy_update_plan,
        )
        from control_plane.repository import worktree_git_dir
        from tests.host_adapter_test_support import (
            governing_policy,
            governing_runtime_observation,
        )

        recovery_command = (
            "from tests.test_policy import _complete_policy_recovery;"
            "import sys;"
            "_complete_policy_recovery(*sys.argv[1:])"
        )
        for phase in (
            "allocating",
            "prepared",
            "policy_replaced",
            "committed",
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir) / "repository"
                root.mkdir()
                subprocess.run(
                    ["git", "init", "-b", "main", str(root)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "config",
                        "user.name",
                        "Policy Recovery Tests",
                    ],
                    check=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "config",
                        "user.email",
                        "tests@example.invalid",
                    ],
                    check=True,
                )
                target = root / ".codex" / "project-policy.toml"
                target.parent.mkdir()
                target.write_bytes(FIXTURE.read_bytes())
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "add",
                        ".codex/project-policy.toml",
                    ],
                    check=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "commit",
                        "-m",
                        "policy baseline",
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                head = subprocess.run(
                    ["git", "-C", str(root), "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                proposed = Path(temp_dir) / "proposed-policy.toml"
                proposed.write_text(
                    FIXTURE.read_text(encoding="utf-8").replace(
                        'remote = "origin"', 'remote = "upstream"'
                    ),
                    encoding="utf-8",
                )
                policy_digest = (
                    "sha256:" + sha256(target.read_bytes()).hexdigest()
                )
                session_id = f"session-policy-recovery-{phase}"
                invocation_id = f"invocation-policy-recovery-{phase}"
                task_id = "TASK-POLICY-RECOVERY"
                runtime = governing_runtime_observation(
                    runtime_digest=policy_digest,
                    lock_digest=policy_digest,
                    policy_digest=policy_digest,
                    attestor_worktree=str(root.resolve()),
                    target_worktree=str(root.resolve()),
                    governing_base_commit=head,
                    runtime_layout="source",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    freshness_deadline=130.0,
                )
                policy = governing_policy(
                    policy=load_policy(target),
                    policy_digest=policy_digest,
                    runtime_digest=policy_digest,
                    lock_digest=policy_digest,
                    governing_base_commit=head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    freshness_deadline=130.0,
                )
                state_dir = worktree_git_dir(root)
                store = TaskStore(state_dir)
                state = store.start(
                    task_id,
                    outcome="local_change",
                    branch="main",
                    task_digest=policy_digest,
                    decision_digest=policy_digest,
                )
                lease = TaskLease.acquire(
                    state_dir,
                    task_id=task_id,
                    worktree=str(root),
                    branch="main",
                    session_id=session_id,
                    paths=[".codex/project-policy.toml"],
                    policy_digest=policy_digest,
                )
                draft = project_remote_policy_update_plan(
                    governing_runtime=runtime,
                    governing_policy=policy,
                    candidate_policy_path=proposed,
                    task_context={
                        **state,
                        "lease_digest": lease["lease_digest"],
                    },
                    lease=lease,
                    repository_identity=str(root.resolve()),
                    required_checks=(),
                )
                transaction = (
                    state_dir
                    / "codex-control-plane"
                    / "policy-updates"
                    / draft.draft_digest.removeprefix("sha256:")
                )
                transaction.mkdir(parents=True)
                journal = {
                    "schema_version": 1,
                    "draft_digest": draft.draft_digest,
                    "task_id": draft.task_id,
                    "task_digest": draft.task_digest,
                    "lease_digest": draft.lease_digest,
                    "runtime_digest": draft.runtime_digest,
                    "generation": draft.generation,
                    "before_digest": draft.before_digest,
                    "after_digest": draft.after_digest,
                    "phase": phase,
                }
                (transaction / "journal.json").write_text(
                    json.dumps(journal, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                if phase != "allocating":
                    (
                        transaction / "project-policy.before.toml"
                    ).write_bytes(FIXTURE.read_bytes())
                    (
                        transaction / "project-policy.after.toml"
                    ).write_bytes(proposed.read_bytes())
                if phase in {"policy_replaced", "committed"}:
                    target.write_bytes(proposed.read_bytes())
                if phase == "committed":
                    state_path = (
                        state_dir
                        / "codex-control-plane"
                        / "tasks"
                        / f"{task_id}.json"
                    )
                    committed_state = json.loads(
                        state_path.read_text(encoding="utf-8")
                    )
                    committed_state["generation"] = draft.generation + 1
                    committed_state[
                        "remote_policy_update_digest"
                    ] = draft.draft_digest
                    state_path.write_text(
                        json.dumps(committed_state, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                recovered = subprocess.run(
                    [
                        "python3",
                        "-c",
                        recovery_command,
                        str(root),
                        str(proposed),
                        str(FIXTURE),
                        policy_digest,
                        head,
                        session_id,
                        invocation_id,
                        task_id,
                    ],
                    cwd=Path(__file__).parents[1],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    recovered.returncode, 0, recovered.stderr
                )
                self.assertEqual(recovered.stdout.strip(), "1")
                self.assertEqual(
                    load_policy(target)["git"]["remote"], "upstream"
                )
                self.assertEqual(store.status(task_id)["generation"], 1)
                committed_journal = json.loads(
                    (transaction / "journal.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(committed_journal["phase"], "committed")

    def test_required_check_selector_uses_closed_grammar(self) -> None:
        from control_plane.policy import parse_required_check_selector

        parsed = parse_required_check_selector(
            "Control Plane / tests:github-actions:SUCCESS,NEUTRAL"
        )
        self.assertEqual(parsed.app, "github-actions")
        self.assertEqual(parsed.conclusions, ("NEUTRAL", "SUCCESS"))
        with self.assertRaisesRegex(ValueError, "E_REQUIRED_CHECK"):
            parse_required_check_selector("name:app:SUCCESS;FAILURE")

    def test_loads_valid_policy(self) -> None:
        from control_plane.policy import load_policy

        policy = load_policy(FIXTURE)

        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(policy["git"]["base_branch"], "main")
        self.assertEqual(policy["reasoning"]["normal_max_workers"], 2)

    def test_missing_policy_has_stable_error_code(self) -> None:
        from control_plane.policy import PolicyError, load_policy

        with self.assertRaises(PolicyError) as caught:
            load_policy(Path("/definitely/missing/project-policy.toml"))

        self.assertEqual(caught.exception.code, "E_POLICY_NOT_FOUND")

    def test_malformed_policy_has_stable_error_code(self) -> None:
        from control_plane.policy import PolicyError, load_policy

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.toml"
            path.write_text("[git\nbase_branch = 'main'\n", encoding="utf-8")

            with self.assertRaises(PolicyError) as caught:
                load_policy(path)

        self.assertEqual(caught.exception.code, "E_POLICY_PARSE")

    def test_valid_policy_has_no_issues(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        self.assertEqual(validate_policy(load_policy(FIXTURE)), [])

    def test_missing_required_key_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        del policy["git"]["base_branch"]

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_MISSING", codes)

    def test_unknown_schema_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["schema_version"] = 99

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_SCHEMA", codes)

    def test_schema_version_requires_an_exact_integer(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        for invalid_value in (True, 1.0):
            with self.subTest(invalid_value=invalid_value):
                policy = copy.deepcopy(load_policy(FIXTURE))
                policy["schema_version"] = invalid_value

                codes = {issue.code for issue in validate_policy(policy)}

                self.assertIn("P_SCHEMA", codes)

    def test_project_identity_requires_nonempty_strings(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        invalid_values = ("", "   ", {"nested": "table"}, ["list"])
        for path, expected_code in (
            ("project_name", "P_PROJECT_NAME"),
            ("project_kind", "P_PROJECT_KIND"),
        ):
            for invalid_value in invalid_values:
                with self.subTest(path=path, invalid_value=invalid_value):
                    policy = copy.deepcopy(load_policy(FIXTURE))
                    policy[path] = invalid_value

                    codes = {issue.code for issue in validate_policy(policy)}

                    self.assertIn(expected_code, codes)

    def test_invalid_reasoning_level_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["reasoning"]["default"] = "automatic-magic"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_REASONING", codes)

    def test_more_than_two_normal_workers_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["reasoning"]["normal_max_workers"] = 3

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_WORKERS", codes)

    def test_pull_request_cannot_be_disabled(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["require_pull_request"] = False

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_PR_REQUIRED", codes)

    def test_direct_base_push_cannot_be_enabled(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["allow_direct_base_push"] = True

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_PUSH", codes)

    def test_official_release_must_use_remote_base(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["release"]["official_source"] = "current_worktree"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_RELEASE_SOURCE", codes)

    def test_remote_cannot_be_parsed_as_a_git_option(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["remote"] = "--upload-pack=unexpected"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_REMOTE", codes)

    def test_invalid_base_ref_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["base_branch"] = "main..unexpected"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_BRANCH", codes)

    def test_base_ref_component_cannot_start_with_dot(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["base_branch"] = ".main"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_BRANCH", codes)

    def test_base_ref_cannot_use_reserved_head_pseudoref(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["base_branch"] = "HEAD"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_BRANCH", codes)

    def test_unsafe_integration_strategy_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["integration_strategy"] = "direct-push"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_INTEGRATION", codes)

    def test_security_booleans_require_actual_safe_values(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["require_pull_request"] = "yes"
        policy["git"]["allow_direct_base_push"] = "no"
        policy["reasoning"]["sequential_default"] = "yes"
        policy["documentation"]["require_impact_assessment"] = "yes"
        policy["release"]["require_manifest"] = "yes"
        policy["release"]["allow_local_official_release"] = "no"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertTrue(
            {
                "P_PR_REQUIRED",
                "P_BASE_PUSH",
                "P_SEQUENTIAL",
                "P_DOC_IMPACT",
                "P_RELEASE_MANIFEST",
                "P_LOCAL_RELEASE",
            }.issubset(codes)
        )

    def test_wrong_reasoning_type_is_rejected_without_crashing(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["reasoning"]["default"] = ["high"]

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_REASONING", codes)

    def test_unknown_policy_key_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["mystery_override"] = True

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_UNKNOWN", codes)


if __name__ == "__main__":
    unittest.main()
