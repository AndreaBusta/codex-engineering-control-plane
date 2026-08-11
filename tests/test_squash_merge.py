from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
import subprocess
from unittest.mock import patch

from tests.router_test_support import task_envelope
from tests.host_adapter_test_support import lifecycle_observation


class SquashMergeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.run_workflow import (
            advance_outcome_binding,
            build_outcome_binding,
            build_run_plan,
        )

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repository = Path(self.temp.name) / "repository"
        self.repository.mkdir()
        subprocess.run(
            ["git", "init", "-b", "codex/squash-contract", str(self.repository)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repository), "remote", "add", "origin",
                "https://github.com/Example/Control-Plane.git",
            ],
            check=True,
        )
        self.scope_paths = ("control_plane/host_bridge.py",)
        task = task_envelope(
            task_id="TASK-SQUASH-MERGE",
            requested_outcome="integration",
            scope_paths=list(self.scope_paths),
            effects=[
                {"name": "local_write", "source": "user_explicit"},
                {"name": "commit", "source": "user_explicit"},
                {"name": "remote_write", "source": "user_explicit"},
                {"name": "pull_request", "source": "user_explicit"},
                {"name": "integration", "source": "user_explicit"},
            ],
        )
        decision_core = {
            "schema_version": 1,
            "task_id": task["task_id"],
            "mode": "audit",
            "ok": True,
            "decision_ready": False,
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
            "approval_boundaries": [
                "commit", "remote_write", "pull_request", "integration",
            ],
            "authorization": {"local_write": True},
            "required_gates": ["gate.relevant-tests"],
            "selected_resource_digests": {},
            "matched_routes": ["quality-profile-generic"],
            "facts": {"task_digest": contract_digest(task)},
            "errors": [],
        }
        decision = {
            **decision_core,
            "decision_digest": contract_digest(decision_core),
        }
        self.run_plan = build_run_plan(
            task=task,
            decision=decision,
            repository=self.repository,
            branch="codex/squash-contract",
            head="a" * 40,
            session_id="session-squash-merge",
            prepared_at="2026-08-09T10:00:00Z",
        )
        binding = build_outcome_binding(
            run_plan=self.run_plan,
            review_head="a" * 40,
            reviewed_tree_digest="sha256:" + "1" * 64,
            reviewed_diff_digest="sha256:" + "2" * 64,
        )
        binding = advance_outcome_binding(
            binding,
            effect_id="local_write",
            observation={
                "head": "a" * 40,
                "tree_digest": "sha256:" + "1" * 64,
                "diff_digest": "sha256:" + "2" * 64,
            },
        )
        binding = advance_outcome_binding(
            binding,
            effect_id="commit",
            observation={
                "parent_head": "a" * 40,
                "tree_digest": "sha256:" + "1" * 64,
                "committed_head": "b" * 40,
            },
        )
        binding = advance_outcome_binding(
            binding,
            effect_id="remote_write",
            observation={"pushed_head": "b" * 40},
        )
        self.pull_request_number = 17
        self.pull_request_url = (
            "https://github.com/example/control-plane/pull/17"
        )
        self.pull_request_digest = contract_digest(
            {
                "number": self.pull_request_number,
                "url": self.pull_request_url,
                "head": "b" * 40,
                "draft": True,
            }
        )
        self.checks_digest = contract_digest(
            ["sha256:" + "3" * 64, "sha256:" + "4" * 64]
        )
        self.binding = advance_outcome_binding(
            binding,
            effect_id="pull_request",
            observation={
                "pull_request_digest": self.pull_request_digest,
                "checks_digest": self.checks_digest,
                "head": "b" * 40,
            },
        )
        self.policy = load_policy(
            Path(__file__).parent / "fixtures" / "valid-policy.toml"
        )

    def _effect_plan(self):
        from control_plane.host_bridge import build_squash_merge_effect_plan

        return build_squash_merge_effect_plan(
            outcome_binding=self.binding,
            task_digest=self.run_plan["task_digest"],
            policy=self.policy,
            scope_paths=self.scope_paths,
            pull_request_number=self.pull_request_number,
            pull_request_url=self.pull_request_url,
            prepared_at="2026-08-09T10:05:00Z",
            expires_at="2026-08-09T10:10:00Z",
            now="2026-08-09T10:06:00Z",
        )

    def _seed_pr_ready_store(self):
        from control_plane.lifecycle import TaskStore, _atomic_json
        from control_plane.run_workflow import RunStore

        policy_dir = self.repository / ".codex"
        policy_dir.mkdir(exist_ok=True)
        shutil.copy2(
            Path(__file__).parent / "fixtures" / "valid-policy.toml",
            policy_dir / "project-policy.toml",
        )
        state_dir = self.repository / ".git"
        RunStore(state_dir).write_plan(self.run_plan)
        store = TaskStore(state_dir)
        state = store.start(
            "TASK-SQUASH-MERGE",
            outcome="integration",
            branch="codex/squash-contract",
            task_digest=self.run_plan["task_digest"],
            decision_digest=self.run_plan["decision_digest"],
        )
        state.update(
            {
                "state": "pr_ready",
                "run_plan_digest": self.run_plan["plan_digest"],
                "outcome_binding": copy.deepcopy(self.binding),
                "resume_state": None,
                "resume_forbidden": False,
                "block_reason": None,
                "generation": 9,
            }
        )
        state["evidence"] = {
            "committed": {"commit": "b" * 40},
            "pushed": {"remote_head": "b" * 40},
            "pr_draft": {
                "pull_request": {
                    "number": self.pull_request_number,
                    "url": self.pull_request_url,
                    "head_commit": "b" * 40,
                }
            },
            "pr_ready": {
                "checks_ok": {"ok": True, "head_commit": "b" * 40},
                "pull_request_digest": self.pull_request_digest,
                "checks_digest": self.checks_digest,
                "authorizes": False,
            },
        }
        _atomic_json(store._path("TASK-SQUASH-MERGE"), state)
        return store

    def _ready_receipt(
        self, plan, *, observed_at: str = "2026-08-09T10:06:30Z"
    ):
        from control_plane.host_bridge import build_integration_receipt

        return build_integration_receipt(
            effect_plan=plan,
            status="READY",
            observed_at=observed_at,
            observed_repository="example/control-plane",
            observed_base="main",
            observed_branch="codex/squash-contract",
            observed_head_sha="b" * 40,
            observed_pr_number=17,
            observed_pr_url=self.pull_request_url,
            observed_pr_state="OPEN",
            observed_pr_draft=False,
            observed_strategy=None,
            observed_checks_digest=self.checks_digest,
            observed_merge_sha=None,
        )

    def _pass_receipt(self, plan, *, merge_sha: str = "c" * 40):
        from control_plane.host_bridge import build_integration_receipt

        return build_integration_receipt(
            effect_plan=plan,
            status="PASS",
            observed_at="2026-08-09T10:07:00Z",
            observed_repository="example/control-plane",
            observed_base="main",
            observed_branch="codex/squash-contract",
            observed_head_sha="b" * 40,
            observed_pr_number=17,
            observed_pr_url=self.pull_request_url,
            observed_pr_state="MERGED",
            observed_pr_draft=False,
            observed_strategy="squash",
            observed_checks_digest=self.checks_digest,
            observed_merge_sha=merge_sha,
        )

    def _merge_observation(self, plan, receipt):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        evidence = {
            "effect_plan_digest": plan.plan_digest,
            "integration_receipt_digest": receipt.receipt_digest,
            "pull_request_digest": plan.pull_request_digest,
            "checks_digest": plan.checks_digest,
            "merge_commit": receipt.observed_merge_sha,
            "strategy": "squash",
        }
        invocation_id = f"integration-{receipt.receipt_digest[-16:]}"
        raw = lifecycle_observation(
            bridge.GitHubObservation,
            observation_id=invocation_id,
            invocation_id=invocation_id,
            task_digest=self.run_plan["task_digest"],
            repository_identity=str(self.repository.resolve()),
            worktree_identity=str(self.repository.resolve()),
            branch="codex/squash-contract",
            prior_head=plan.head_sha,
            target_state="merged",
            session_id="session-integration-observation",
            provider="github",
            subject_digest=contract_digest(evidence),
            evidence=evidence,
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        return bridge.validate_github_observation(
            raw,
            expected_task_digest=self.run_plan["task_digest"],
            expected_repo=self.repository,
            expected_worktree=self.repository,
            expected_branch="codex/squash-contract",
            expected_prior_head=plan.head_sha,
            expected_target_state="merged",
            expected_session_id="session-integration-observation",
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )

    def _ready_observation(self, plan, receipt):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        evidence = {
            "effect_plan_digest": plan.plan_digest,
            "integration_receipt_digest": receipt.receipt_digest,
            "pull_request_digest": plan.pull_request_digest,
            "checks_digest": plan.checks_digest,
            "pull_request_state": "OPEN",
            "draft": False,
        }
        invocation_id = f"integration-ready-{receipt.receipt_digest[-16:]}"
        raw = lifecycle_observation(
            bridge.GitHubObservation,
            observation_id=invocation_id,
            invocation_id=invocation_id,
            task_digest=self.run_plan["task_digest"],
            repository_identity=str(self.repository.resolve()),
            worktree_identity=str(self.repository.resolve()),
            branch="codex/squash-contract",
            prior_head=plan.head_sha,
            target_state="integration_ready",
            session_id="session-integration-ready",
            provider="github",
            subject_digest=contract_digest(evidence),
            evidence=evidence,
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        return bridge.validate_github_observation(
            raw,
            expected_task_digest=self.run_plan["task_digest"],
            expected_repo=self.repository,
            expected_worktree=self.repository,
            expected_branch="codex/squash-contract",
            expected_prior_head=plan.head_sha,
            expected_target_state="integration_ready",
            expected_session_id="session-integration-ready",
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )

    def _git_commit_pair(self) -> tuple[str, str]:
        for key, value in (
            ("user.name", "Control Plane Tests"),
            ("user.email", "tests@example.invalid"),
        ):
            subprocess.run(
                ["git", "-C", str(self.repository), "config", key, value],
                check=True,
            )
        tracked = self.repository / "tracked.txt"
        tracked.write_text("base\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repository), "add", "tracked.txt"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository), "commit", "-m", "base"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        tracked.write_text("base\nmerge\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(self.repository), "commit", "-am", "squash result"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        merge = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        return base, merge

    def _merged_store(self, *, merge_sha: str):
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            now="2026-08-09T10:06:00Z",
        )
        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            now="2026-08-09T10:06:30Z",
        )
        ticket = store.revalidate_integration_before_execution(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:45Z",
        )
        bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)
        receipt = self._pass_receipt(plan, merge_sha=merge_sha)
        store.publish_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=receipt,
            current_branch="codex/squash-contract",
            observation=self._merge_observation(plan, receipt),
        )
        return store, plan, receipt

    def test_effect_plan_is_closed_non_authorizing_and_exactly_squash(self) -> None:
        import control_plane.host_bridge as bridge

        builder = getattr(bridge, "build_squash_merge_effect_plan", None)
        self.assertTrue(callable(builder), "squash merge plan builder is missing")
        plan = self._effect_plan()

        self.assertFalse(plan.authorizes)
        self.assertEqual(plan.requested_outcome, "integration")
        self.assertEqual(plan.effect, "integration")
        self.assertEqual(plan.integration_strategy, "squash")
        self.assertEqual(plan.pull_request_digest, self.binding["pull_request_digest"])
        self.assertEqual(plan.checks_digest, self.binding["checks_digest"])
        self.assertEqual(
            plan.argv,
            (
                "gh", "pr", "merge", "17", "--repo",
                "example/control-plane", "--match-head-commit", "b" * 40,
                "--squash",
            ),
        )
        forbidden = {
            "--auto", "--delete-branch", "--force", "--rebase", "--merge",
            "deploy", "release",
        }
        self.assertFalse(forbidden.intersection(plan.argv))
        self.assertEqual(set(plan.to_dict()), bridge._INTEGRATION_EFFECT_PLAN_KEYS)

    def test_effect_plan_rejects_wrong_outcome_expiry_and_unsupported_policy(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import build_squash_merge_effect_plan

        wrong_outcome = copy.deepcopy(self.binding)
        wrong_outcome["requested_outcome"] = "pull_request"
        wrong_outcome["binding_digest"] = contract_digest(
            {
                key: value
                for key, value in wrong_outcome.items()
                if key != "binding_digest"
            }
        )
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_EFFECT_PLAN"):
            build_squash_merge_effect_plan(
                outcome_binding=wrong_outcome,
                task_digest=self.run_plan["task_digest"],
                policy=self.policy,
                scope_paths=self.scope_paths,
                pull_request_number=self.pull_request_number,
                pull_request_url=self.pull_request_url,
                prepared_at="2026-08-09T10:05:00Z",
                expires_at="2026-08-09T10:10:00Z",
                now="2026-08-09T10:06:00Z",
            )

    def test_effect_plan_rejects_excessive_ttl_even_when_current(self) -> None:
        from control_plane.host_bridge import build_squash_merge_effect_plan

        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_EFFECT_PLAN"):
            build_squash_merge_effect_plan(
                outcome_binding=self.binding,
                task_digest=self.run_plan["task_digest"],
                policy=self.policy,
                scope_paths=self.scope_paths,
                pull_request_number=self.pull_request_number,
                pull_request_url=self.pull_request_url,
                prepared_at="2020-01-01T00:00:00Z",
                expires_at="2030-01-01T00:00:00Z",
                now="2026-08-09T10:06:00Z",
            )
        with self.assertRaisesRegex(ValueError, "unexpired PR binding"):
            build_squash_merge_effect_plan(
                outcome_binding=self.binding,
                task_digest=self.run_plan["task_digest"],
                policy=self.policy,
                scope_paths=self.scope_paths,
                pull_request_number=self.pull_request_number,
                pull_request_url=self.pull_request_url,
                prepared_at="2026-08-09T10:05:00Z",
                expires_at="2026-08-09T10:10:00Z",
                now="2026-08-09T10:10:00Z",
            )
        unsupported = copy.deepcopy(self.policy)
        unsupported["git"]["integration_strategy"] = "merge-commit"
        with self.assertRaisesRegex(
            ValueError, "BLOCKED_UNSUPPORTED_INTEGRATION_STRATEGY"
        ):
            build_squash_merge_effect_plan(
                outcome_binding=self.binding,
                task_digest=self.run_plan["task_digest"],
                policy=unsupported,
                scope_paths=self.scope_paths,
                pull_request_number=self.pull_request_number,
                pull_request_url=self.pull_request_url,
                prepared_at="2026-08-09T10:05:00Z",
                expires_at="2026-08-09T10:10:00Z",
                now="2026-08-09T10:06:00Z",
            )

    def test_effect_plan_schema_rejects_other_methods_and_extra_effects(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import IntegrationEffectPlanV1

        canonical = self._effect_plan().to_dict()
        for replacement in ("--merge", "--rebase", "--auto", "--delete-branch", "--force"):
            mutated = copy.deepcopy(canonical)
            mutated["argv"][-1] = replacement
            mutated["argv_digest"] = contract_digest(mutated["argv"])
            mutated["plan_digest"] = contract_digest(
                {key: value for key, value in mutated.items() if key != "plan_digest"}
            )
            with self.subTest(replacement=replacement):
                with self.assertRaisesRegex(ValueError, "E_INTEGRATION_EFFECT_PLAN"):
                    IntegrationEffectPlanV1.from_dict(mutated)
        for extra in ("deploy", "release", "force_push", "base_direct_write"):
            mutated = copy.deepcopy(canonical)
            mutated[extra] = True
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ValueError, "schema is not closed"):
                    IntegrationEffectPlanV1.from_dict(mutated)

    def test_pass_receipt_advances_exact_pull_request_predecessor_once(self) -> None:
        import control_plane.host_bridge as bridge

        builder = getattr(bridge, "build_integration_receipt", None)
        applier = getattr(bridge, "apply_integration_receipt", None)
        self.assertTrue(callable(builder), "integration receipt builder is missing")
        self.assertTrue(callable(applier), "integration receipt applier is missing")
        plan = self._effect_plan()
        receipt = builder(
            effect_plan=plan,
            status="PASS",
            observed_at="2026-08-09T10:07:00Z",
            observed_repository="example/control-plane",
            observed_base="main",
            observed_branch="codex/squash-contract",
            observed_head_sha="b" * 40,
            observed_pr_number=17,
            observed_pr_url=self.pull_request_url,
            observed_pr_state="MERGED",
            observed_pr_draft=False,
            observed_strategy="squash",
            observed_checks_digest=self.checks_digest,
            observed_merge_sha="c" * 40,
        )
        successor = applier(
            outcome_binding=self.binding,
            effect_plan=plan,
            receipt=receipt,
        )

        self.assertFalse(receipt.authorizes)
        self.assertEqual(successor["merge_sha"], "c" * 40)
        self.assertEqual(successor["consumed_effect_ids"][-1], "integration")
        with self.assertRaisesRegex(ValueError, "REPLAY"):
            applier(
                outcome_binding=successor,
                effect_plan=plan,
                receipt=receipt,
            )

    def test_receipt_mutation_drift_and_unknown_fail_closed(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        builder = getattr(bridge, "build_integration_receipt", None)
        applier = getattr(bridge, "apply_integration_receipt", None)
        self.assertTrue(callable(builder), "integration receipt builder is missing")
        self.assertTrue(callable(applier), "integration receipt applier is missing")
        plan = self._effect_plan()
        receipt = builder(
            effect_plan=plan,
            status="PASS",
            observed_at="2026-08-09T10:07:00Z",
            observed_repository="example/control-plane",
            observed_base="main",
            observed_branch="codex/squash-contract",
            observed_head_sha="b" * 40,
            observed_pr_number=17,
            observed_pr_url=self.pull_request_url,
            observed_pr_state="MERGED",
            observed_pr_draft=False,
            observed_strategy="squash",
            observed_checks_digest=self.checks_digest,
            observed_merge_sha="c" * 40,
        )
        mutated = receipt.to_dict()
        mutated["observed_strategy"] = "rebase"
        mutated["receipt_digest"] = contract_digest(
            {key: value for key, value in mutated.items() if key != "receipt_digest"}
        )
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_RECEIPT"):
            bridge.IntegrationReceiptV1.from_dict(mutated)
        foreign = receipt.to_dict()
        foreign["pull_request_url"] = (
            "https://github.com/other/control-plane/pull/17"
        )
        foreign["observed_pr_url"] = foreign["pull_request_url"]
        foreign["receipt_digest"] = contract_digest(
            {key: value for key, value in foreign.items() if key != "receipt_digest"}
        )
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_RECEIPT"):
            bridge.IntegrationReceiptV1.from_dict(foreign)
        drifted_binding = copy.deepcopy(self.binding)
        drifted_binding["checks_digest"] = "sha256:" + "9" * 64
        drifted_binding["binding_digest"] = contract_digest(
            {
                key: value
                for key, value in drifted_binding.items()
                if key != "binding_digest"
            }
        )
        with self.assertRaisesRegex(ValueError, "BINDING"):
            applier(
                outcome_binding=drifted_binding,
                effect_plan=plan,
                receipt=receipt,
            )
        unknown = builder(
            effect_plan=plan,
            status="UNKNOWN",
            observed_at="2026-08-09T10:07:00Z",
        )
        with self.assertRaisesRegex(ValueError, "UNKNOWN.*BLOCKED"):
            applier(
                outcome_binding=self.binding,
                effect_plan=plan,
                receipt=unknown,
            )

    def test_python_runtime_has_no_serializable_authorization_context(self) -> None:
        import control_plane.host_bridge as bridge

        self.assertFalse(hasattr(bridge, "OutcomeAuthorizationContext"))
        self.assertFalse(hasattr(bridge, "execute_squash_merge"))

    def test_ready_receipt_is_exact_read_before_write_evidence(self) -> None:
        try:
            receipt = self._ready_receipt(self._effect_plan())
        except ValueError as error:
            self.fail(f"READY observation receipt is missing: {error}")
        self.assertEqual(receipt.status, "READY")
        self.assertEqual(receipt.observed_pr_state, "OPEN")
        self.assertFalse(receipt.observed_pr_draft)
        self.assertIsNone(receipt.observed_merge_sha)
        self.assertFalse(receipt.authorizes)

    def test_lifecycle_requires_prepare_ready_observation_and_one_shot_edge(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()
        prepare = getattr(store, "prepare_integration", None)
        arm = getattr(store, "arm_integration_observe_only", None)
        revalidate = getattr(store, "revalidate_integration_before_execution", None)
        consume = getattr(bridge, "consume_integration_execution_ticket", None)
        publish = getattr(store, "publish_integration", None)
        for name, method in (
            ("prepare", prepare),
            ("arm", arm),
            ("revalidate", revalidate),
            ("consume", consume),
            ("publish", publish),
        ):
            self.assertTrue(callable(method), f"integration lifecycle {name} is missing")
        plan = self._effect_plan()
        prepared = prepare(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            now="2026-08-09T10:06:00Z",
        )
        self.assertEqual(prepared["pending_integration_effect"]["phase"], "prepared")
        self.assertTrue(prepared["resume_forbidden"])
        with self.assertRaisesRegex(ValueError, "arm is unavailable"):
            revalidate(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                current_branch="codex/squash-contract",
                now="2026-08-09T10:06:30Z",
            )
        ready = self._ready_receipt(plan)
        armed = arm(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            now="2026-08-09T10:06:30Z",
        )
        self.assertEqual(armed["pending_integration_effect"]["phase"], "observe_only")
        ticket = revalidate(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:45Z",
        )
        self.assertFalse(hasattr(ticket, "to_dict"))
        self.assertEqual(consume(ticket, effect_plan=plan).plan_digest, plan.plan_digest)
        with self.assertRaisesRegex(ValueError, "one-shot"):
            consume(ticket, effect_plan=plan)
        with self.assertRaisesRegex(ValueError, "arm is unavailable"):
            revalidate(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                current_branch="codex/squash-contract",
                now="2026-08-09T10:06:50Z",
            )
        pass_receipt = self._pass_receipt(plan)
        merged = publish(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=pass_receipt,
            current_branch="codex/squash-contract",
            observation=self._merge_observation(plan, pass_receipt),
        )
        self.assertEqual(merged["state"], "merged")
        self.assertEqual(merged["outcome_binding"]["merge_sha"], "c" * 40)

    def test_publish_integration_rejects_scalar_pass_without_native_provider_observation(
        self,
    ) -> None:
        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            now="2026-08-09T10:06:00Z",
        )
        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            now="2026-08-09T10:06:30Z",
        )

        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_OBSERVATION"):
            store.publish_integration(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                receipt=self._pass_receipt(plan),
                current_branch="codex/squash-contract",
            )

        self.assertEqual(store.status("TASK-SQUASH-MERGE")["state"], "pr_ready")

    def test_arm_integration_rejects_scalar_ready_without_native_provider_observation(
        self,
    ) -> None:
        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            now="2026-08-09T10:06:00Z",
        )

        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_OBSERVATION"):
            store.arm_integration_observe_only(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                receipt=self._ready_receipt(plan),
                current_branch="codex/squash-contract",
                now="2026-08-09T10:06:30Z",
            )

        marker = store.status("TASK-SQUASH-MERGE")["pending_integration_effect"]
        self.assertEqual(marker["phase"], "prepared")

    def test_execution_ticket_expires_and_failed_consume_burns_claim(self) -> None:
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:00Z",
        )
        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            clock=lambda: "2026-08-09T10:06:30Z",
        )
        current = ["2026-08-09T10:06:45Z"]
        ticket = store.revalidate_integration_before_execution(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: current[0],
        )

        current[0] = "2026-08-09T10:07:00.000001Z"
        with self.assertRaisesRegex(ValueError, "expired"):
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)
        current[0] = "2026-08-09T10:06:45Z"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)

    def test_execution_ticket_rejects_clock_rollback_and_burns_claim(self) -> None:
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:00Z",
        )
        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            clock=lambda: "2026-08-09T10:06:30Z",
        )
        current = ["2026-08-09T10:06:45Z"]
        ticket = store.revalidate_integration_before_execution(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: current[0],
        )

        current[0] = "2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "rolled back"):
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)
        current[0] = "2026-08-09T10:06:45Z"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)

    def test_execution_ticket_rejects_plan_expiry_equality_and_burns_claim(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        ready = self._ready_receipt(
            plan, observed_at="2026-08-09T10:09:45Z"
        )
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:09:40Z",
        )
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            clock=lambda: "2026-08-09T10:09:45Z",
        )
        current = ["2026-08-09T10:09:50Z"]
        ticket = store.revalidate_integration_before_execution(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: current[0],
        )

        current[0] = "2026-08-09T10:10:00Z"
        with self.assertRaisesRegex(ValueError, "expired"):
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)
        current[0] = "2026-08-09T10:09:59.999999Z"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)

    def test_execution_ticket_accepts_ready_deadline_equality(self) -> None:
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:00Z",
        )
        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            clock=lambda: "2026-08-09T10:06:30Z",
        )
        current = ["2026-08-09T10:06:45Z"]
        ticket = store.revalidate_integration_before_execution(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: current[0],
        )

        current[0] = "2026-08-09T10:07:00Z"
        self.assertEqual(
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan),
            plan,
        )

    def test_execution_ticket_accepts_just_before_plan_expiry(self) -> None:
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        ready = self._ready_receipt(
            plan, observed_at="2026-08-09T10:09:45Z"
        )
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:09:40Z",
        )
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            clock=lambda: "2026-08-09T10:09:45Z",
        )
        current = ["2026-08-09T10:09:50Z"]
        ticket = store.revalidate_integration_before_execution(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: current[0],
        )

        current[0] = "2026-08-09T10:09:59.999999Z"
        self.assertEqual(
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan),
            plan,
        )

    def test_stale_or_future_ready_never_arms_or_issues_a_ticket(self) -> None:
        from control_plane.host_bridge import build_integration_receipt

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:00Z",
        )
        stale = build_integration_receipt(
            effect_plan=plan,
            status="READY",
            observed_at="2026-08-09T10:05:30Z",
            observed_repository="example/control-plane",
            observed_base="main",
            observed_branch="codex/squash-contract",
            observed_head_sha="b" * 40,
            observed_pr_number=17,
            observed_pr_url=self.pull_request_url,
            observed_pr_state="OPEN",
            observed_pr_draft=False,
            observed_strategy=None,
            observed_checks_digest=self.checks_digest,
            observed_merge_sha=None,
        )
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_PREPARE"):
            store.arm_integration_observe_only(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                receipt=stale,
                current_branch="codex/squash-contract",
                observation=self._ready_observation(plan, stale),
                clock=lambda: "2026-08-09T10:06:30Z",
            )
        self.assertEqual(
            store._read("TASK-SQUASH-MERGE")["pending_integration_effect"]["phase"],
            "prepared",
        )
        self.assertNotIn("TASK-SQUASH-MERGE", store._armed_integration_plans)

        future = self._ready_receipt(plan).to_dict()
        future["observed_at"] = "2026-08-09T10:07:00Z"
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import IntegrationReceiptV1

        future["receipt_digest"] = contract_digest(
            {key: value for key, value in future.items() if key != "receipt_digest"}
        )
        future_receipt = IntegrationReceiptV1.from_dict(future)
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_PREPARE"):
            store.arm_integration_observe_only(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                receipt=future_receipt,
                current_branch="codex/squash-contract",
                observation=self._ready_observation(plan, future_receipt),
                clock=lambda: "2026-08-09T10:06:30Z",
            )

        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            clock=lambda: "2026-08-09T10:06:30Z",
        )
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_EXECUTION"):
            store.revalidate_integration_before_execution(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                current_branch="codex/squash-contract",
                clock=lambda: "2026-08-09T10:07:01Z",
            )
        self.assertNotIn("TASK-SQUASH-MERGE", store._armed_integration_plans)

    def test_integration_clock_failure_is_unknown_and_fails_closed(self) -> None:
        store = self._seed_pr_ready_store()
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_PREPARE"):
            store.prepare_integration(
                "TASK-SQUASH-MERGE",
                effect_plan=self._effect_plan(),
                current_branch="codex/squash-contract",
                clock=lambda: "UNKNOWN",
            )
        self.assertNotIn(
            "pending_integration_effect",
            store._read("TASK-SQUASH-MERGE"),
        )

    def test_arm_samples_time_after_waiting_for_task_lock(self) -> None:
        from control_plane.lifecycle import _task_guard

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:00Z",
        )
        current = ["2026-08-09T10:06:30Z"]
        clock_called = threading.Event()
        started = threading.Event()
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def clock() -> str:
            observed = current[0]
            clock_called.set()
            return observed

        def arm() -> None:
            started.set()
            try:
                ready = self._ready_receipt(plan)
                results.append(
                    store.arm_integration_observe_only(
                        "TASK-SQUASH-MERGE",
                        effect_plan=plan,
                        receipt=ready,
                        current_branch="codex/squash-contract",
                        observation=self._ready_observation(plan, ready),
                        clock=clock,
                    )
                )
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=arm, daemon=True)
        with _task_guard(store.state_dir, "TASK-SQUASH-MERGE"):
            worker.start()
            self.assertTrue(started.wait(timeout=1))
            clock_called.wait(timeout=0.5)
            current[0] = "2026-08-09T10:07:01Z"
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertTrue(clock_called.is_set())
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), "E_INTEGRATION_PREPARE")
        self.assertEqual(
            store._read("TASK-SQUASH-MERGE")["pending_integration_effect"]["phase"],
            "prepared",
        )
        self.assertNotIn("TASK-SQUASH-MERGE", store._armed_integration_plans)

    def test_executor_samples_time_after_waiting_for_task_lock(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import _task_guard

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            clock=lambda: "2026-08-09T10:06:00Z",
        )
        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            clock=lambda: "2026-08-09T10:06:30Z",
        )
        current = ["2026-08-09T10:06:45Z"]
        clock_called = threading.Event()
        started = threading.Event()
        tickets: list[object] = []
        errors: list[BaseException] = []

        def clock() -> str:
            observed = current[0]
            clock_called.set()
            return observed

        def revalidate() -> None:
            started.set()
            try:
                tickets.append(
                    store.revalidate_integration_before_execution(
                        "TASK-SQUASH-MERGE",
                        effect_plan=plan,
                        current_branch="codex/squash-contract",
                        clock=clock,
                    )
                )
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=revalidate, daemon=True)
        with _task_guard(store.state_dir, "TASK-SQUASH-MERGE"):
            worker.start()
            self.assertTrue(started.wait(timeout=1))
            clock_called.wait(timeout=0.5)
            current[0] = "2026-08-09T10:10:00Z"
        worker.join(timeout=2)
        for ticket in tickets:
            bridge.consume_integration_execution_ticket(ticket, effect_plan=plan)

        self.assertFalse(worker.is_alive())
        self.assertTrue(clock_called.is_set())
        self.assertEqual(tickets, [])
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), "E_INTEGRATION_EXECUTION")

    def test_crash_or_unknown_never_permits_a_second_merge_write(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = self._seed_pr_ready_store()
        for name in (
            "prepare_integration",
            "arm_integration_observe_only",
            "revalidate_integration_before_execution",
            "publish_integration",
        ):
            self.assertTrue(
                callable(getattr(store, name, None)),
                f"integration lifecycle {name} is missing",
            )
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            now="2026-08-09T10:06:00Z",
        )
        ready = self._ready_receipt(plan)
        store.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            now="2026-08-09T10:06:30Z",
        )
        recovered = TaskStore(self.repository / ".git")
        with self.assertRaisesRegex(ValueError, "arm is unavailable"):
            recovered.revalidate_integration_before_execution(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                current_branch="codex/squash-contract",
                now="2026-08-09T10:06:45Z",
            )
        unknown = __import__(
            "control_plane.host_bridge", fromlist=["build_integration_receipt"]
        ).build_integration_receipt(
            effect_plan=plan,
            status="UNKNOWN",
            observed_at="2026-08-09T10:07:00Z",
        )
        blocked = recovered.publish_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=unknown,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["resume_state"], "pr_ready")
        self.assertTrue(blocked["resume_forbidden"])
        self.assertEqual(
            blocked["pending_integration_effect"]["retry_policy"],
            "observe_only",
        )
        with self.assertRaisesRegex(ValueError, "observe exact PR"):
            recovered.arm_integration_observe_only(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                receipt=self._ready_receipt(plan),
                current_branch="codex/squash-contract",
                now="2026-08-09T10:08:00Z",
            )
        pass_receipt = self._pass_receipt(plan)
        observed = recovered.publish_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=pass_receipt,
            current_branch="codex/squash-contract",
            observation=self._merge_observation(plan, pass_receipt),
        )
        self.assertEqual(observed["state"], "merged")

    def test_crash_in_prepared_phase_can_only_continue_through_ready_read(self) -> None:
        from control_plane.lifecycle import TaskStore

        store = self._seed_pr_ready_store()
        plan = self._effect_plan()
        store.prepare_integration(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            current_branch="codex/squash-contract",
            now="2026-08-09T10:06:00Z",
        )
        recovered = TaskStore(self.repository / ".git")
        with self.assertRaisesRegex(ValueError, "arm is unavailable"):
            recovered.revalidate_integration_before_execution(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                current_branch="codex/squash-contract",
                now="2026-08-09T10:06:15Z",
            )
        ready = self._ready_receipt(plan)
        armed = recovered.arm_integration_observe_only(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            receipt=ready,
            current_branch="codex/squash-contract",
            observation=self._ready_observation(plan, ready),
            now="2026-08-09T10:06:30Z",
        )
        self.assertEqual(
            armed["pending_integration_effect"]["phase"], "observe_only"
        )

    def test_refreshed_base_containment_is_required_before_close(self) -> None:
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        base, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        refresh_builder = getattr(bridge, "build_base_refresh_receipt", None)
        verifier = getattr(git_state, "verify_refreshed_base_containment", None)
        publisher = getattr(store, "publish_base_verification", None)
        for name, method in (
            ("refresh receipt", refresh_builder),
            ("containment verifier", verifier),
            ("base publisher", publisher),
        ):
            self.assertTrue(callable(method), f"{name} is missing")
        refresh = refresh_builder(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:08:00Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=merge,
        )
        self.assertEqual(
            refresh.refresh_argv,
            (
                "git", "-C", str(self.repository.resolve()), "fetch", "--no-tags",
                "--no-prune", "https://github.com/Example/Control-Plane.git",
                "+refs/heads/main:refs/remotes/origin/main",
            ),
        )
        proof = verifier(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        self.assertEqual(proof.status, "PASS")
        self.assertTrue(proof.contained)
        verified = publisher(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(verified["state"], "base_verified")
        closed = store.close(
            "TASK-SQUASH-MERGE",
            current_branch="codex/squash-contract",
        )
        self.assertEqual(closed["state"], "closed")
        self.assertNotEqual(base, merge)

    def test_replace_ref_cannot_forge_refreshed_base_containment(self) -> None:
        """Base verification ignores local replace refs for ancestry proof."""
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        base, _ = self._git_commit_pair()
        tree = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", f"{base}^{{tree}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        unrelated = subprocess.run(
            ["git", "-C", str(self.repository), "commit-tree", tree, "-m", "unrelated"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        replacement = subprocess.run(
            ["git", "-C", str(self.repository), "commit-tree", tree, "-p", unrelated, "-m", "forged parent"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(self.repository), "replace", base, replacement],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repository), "update-ref", "refs/remotes/origin/main", base],
            check=True,
        )
        forged = subprocess.run(
            ["git", "-C", str(self.repository), "merge-base", "--is-ancestor", unrelated, "refs/remotes/origin/main"],
            check=False,
        )
        self.assertEqual(forged.returncode, 0)
        store, plan, integration_receipt = self._merged_store(merge_sha=unrelated)
        refresh = bridge.build_base_refresh_receipt(
            effect_plan=plan, integration_receipt=integration_receipt,
            status="PASS", observed_at="2026-08-09T10:08:00Z",
            observed_ref="refs/remotes/origin/main", observed_sha=base,
        )

        proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan, integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        self.assertEqual(proof.status, "BLOCKED")
        self.assertEqual(proof.reason_code, "BASE_MERGE_NOT_CONTAINED")
        forged_pass = bridge.build_base_verification_receipt(
            effect_plan=plan, integration_receipt=integration_receipt,
            refresh_receipt=refresh, status="PASS", reason_code="BASE_CONTAINED",
            observed_base_sha=base, contained=True,
        )
        self.assertFalse(
            git_state.revalidate_base_verification_receipt(
                forged_pass, refresh_receipt=refresh,
            )
        )
        blocked = store.publish_base_verification(
            "TASK-SQUASH-MERGE", effect_plan=plan,
            integration_receipt=integration_receipt, refresh_receipt=refresh,
            receipt=proof, current_branch="codex/squash-contract",
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertNotEqual(blocked["state"], "base_verified")

    def test_shallow_file_environment_cannot_change_base_ancestry(self) -> None:
        """Verification ignores an ambient alternate shallow boundary."""
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        base, merge = self._git_commit_pair()
        subprocess.run(
            ["git", "-C", str(self.repository), "update-ref", "refs/remotes/origin/main", merge],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=base)
        refresh = bridge.build_base_refresh_receipt(
            effect_plan=plan, integration_receipt=integration_receipt,
            status="PASS", observed_at="2026-08-09T10:08:00Z",
            observed_ref="refs/remotes/origin/main", observed_sha=merge,
        )
        shallow = self.repository / "attacker-shallow"
        shallow.write_text(f"{merge}\n", encoding="utf-8")

        with patch.dict(os.environ, {"GIT_SHALLOW_FILE": str(shallow)}):
            polluted = subprocess.run(
                ["git", "-C", str(self.repository), "merge-base", "--is-ancestor", base, "refs/remotes/origin/main"],
                check=False,
            )
            self.assertEqual(polluted.returncode, 1)
            proof = git_state.verify_refreshed_base_containment(
                effect_plan=plan, integration_receipt=integration_receipt,
                refresh_receipt=refresh,
            )
            self.assertEqual(proof.status, "PASS")
            self.assertTrue(
                git_state.revalidate_base_verification_receipt(
                    proof, refresh_receipt=refresh,
                )
            )

        verified = store.publish_base_verification(
            "TASK-SQUASH-MERGE", effect_plan=plan,
            integration_receipt=integration_receipt, refresh_receipt=refresh,
            receipt=proof, current_branch="codex/squash-contract",
        )
        self.assertEqual(verified["state"], "base_verified")

    def test_real_shallow_repository_makes_negative_containment_unknown(self) -> None:
        """A shallow rc=1 is insufficient evidence that the merge is absent."""
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        base, _ = self._git_commit_pair()
        (self.repository / "head.txt").write_text("head\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repository), "add", "head.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repository), "commit", "-m", "head"], check=True, capture_output=True)
        head = subprocess.run(["git", "-C", str(self.repository), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as temporary:
            shallow = Path(temporary) / "shallow"
            subprocess.run(["git", "clone", "--depth=1", f"file://{self.repository}", str(shallow)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(shallow), "fetch", "--depth=1", "origin", f"{base}:refs/heads/base"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(shallow), "update-ref", "refs/remotes/origin/main", head], check=True)
            self.assertEqual(subprocess.run(["git", "-C", str(shallow), "rev-parse", "--is-shallow-repository"], check=True, capture_output=True, text=True).stdout.strip(), "true")
            self.assertEqual(subprocess.run(["git", "-C", str(shallow), "cat-file", "-e", f"{base}^{{commit}}"], check=False).returncode, 0)
            self.assertEqual(subprocess.run(["git", "-C", str(shallow), "cat-file", "-e", f"{head}^{{commit}}"], check=False).returncode, 0)
            self.assertNotEqual(subprocess.run(["git", "-C", str(shallow), "cat-file", "-e", "HEAD~1^{commit}"], check=False, capture_output=True).returncode, 0)
            plan_data = self._effect_plan().to_dict()
            plan_data["repository"] = str(shallow.resolve())
            plan_data["plan_digest"] = contract_digest({key: value for key, value in plan_data.items() if key != "plan_digest"})
            plan = bridge.IntegrationEffectPlanV1.from_dict(plan_data)
            integration = self._pass_receipt(plan, merge_sha=base)
            refresh = bridge.build_base_refresh_receipt(effect_plan=plan, integration_receipt=integration, status="PASS", observed_at="2026-08-09T10:08:00Z", observed_ref="refs/remotes/origin/main", observed_sha=head)
            proof = git_state.verify_refreshed_base_containment(effect_plan=plan, integration_receipt=integration, refresh_receipt=refresh)
            forged_pass = bridge.build_base_verification_receipt(effect_plan=plan, integration_receipt=integration, refresh_receipt=refresh, status="PASS", reason_code="BASE_CONTAINED", observed_base_sha=head, contained=True)
            self.assertFalse(git_state.revalidate_base_verification_receipt(forged_pass, refresh_receipt=refresh))

        self.assertEqual(proof.status, "BLOCKED")
        self.assertEqual(proof.reason_code, "BASE_CONTAINMENT_UNKNOWN")

    def test_missing_mismatched_or_not_contained_base_blocks_with_recovery(
        self,
    ) -> None:
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        base, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", base,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        refresh_builder = getattr(bridge, "build_base_refresh_receipt", None)
        verifier = getattr(git_state, "verify_refreshed_base_containment", None)
        publisher = getattr(store, "publish_base_verification", None)
        for name, method in (
            ("refresh receipt", refresh_builder),
            ("containment verifier", verifier),
            ("base publisher", publisher),
        ):
            self.assertTrue(callable(method), f"{name} is missing")
        refresh = refresh_builder(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:08:00Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=base,
        )
        proof = verifier(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        self.assertEqual(proof.status, "BLOCKED")
        self.assertEqual(proof.reason_code, "BASE_MERGE_NOT_CONTAINED")
        blocked = publisher(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["resume_state"], "merged")
        self.assertFalse(blocked["evidence"]["base_recovery"]["authorizes"])
        with self.assertRaisesRegex(ValueError, "expected base_verified"):
            store.close(
                "TASK-SQUASH-MERGE",
                current_branch="codex/squash-contract",
            )
        forged_pass = bridge.build_base_verification_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            status="PASS",
            reason_code="BASE_CONTAINED",
            observed_base_sha=base,
            contained=True,
        )
        with self.assertRaisesRegex(ValueError, "E_BASE_VERIFICATION"):
            publisher(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=forged_pass,
                current_branch="codex/squash-contract",
            )
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        recovered_refresh = refresh_builder(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:09:00Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=merge,
        )
        recovered_proof = verifier(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=recovered_refresh,
        )
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            publisher(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=recovered_refresh,
                receipt=recovered_proof,
                current_branch="codex/squash-contract",
            )
        self.assertEqual(
            store.status("TASK-SQUASH-MERGE")["state"], "blocked"
        )

    def test_unknown_refresh_or_ref_mismatch_never_verifies_base(self) -> None:
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        _, merge = self._git_commit_pair()
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        refresh_builder = getattr(bridge, "build_base_refresh_receipt", None)
        verifier = getattr(git_state, "verify_refreshed_base_containment", None)
        self.assertTrue(callable(refresh_builder), "refresh receipt is missing")
        self.assertTrue(callable(verifier), "containment verifier is missing")
        unknown = refresh_builder(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="UNKNOWN",
            observed_at="2026-08-09T10:08:00Z",
        )
        proof = verifier(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
        )
        self.assertEqual(proof.status, "BLOCKED")
        self.assertEqual(proof.reason_code, "BASE_REFRESH_UNKNOWN")
        self.assertEqual(store.status("TASK-SQUASH-MERGE")["state"], "merged")
        missing = refresh_builder(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:08:10Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=merge,
        )
        missing_proof = verifier(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=missing,
        )
        self.assertEqual(missing_proof.reason_code, "BASE_REF_MISSING")
        base = subprocess.run(
            ["git", "-C", str(self.repository), "rev-parse", "HEAD^"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", base,
            ],
            check=True,
        )
        mismatch = verifier(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=missing,
        )
        self.assertEqual(mismatch.reason_code, "BASE_REF_MISMATCH")

    def test_unknown_refresh_cannot_be_upgraded_to_base_pass_with_cached_merge(
        self,
    ) -> None:
        import inspect

        from control_plane.contracts import contract_digest
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        _, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        unknown = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="UNKNOWN",
            observed_at="2026-08-09T10:08:00Z",
        )

        with self.assertRaisesRegex(
            ValueError, "E_BASE_VERIFICATION_RECEIPT"
        ):
            bridge.build_base_verification_receipt(
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=unknown,
                status="PASS",
                reason_code="BASE_CONTAINED",
                observed_base_sha=merge,
                contained=True,
            )

        publisher = store.publish_base_verification
        self.assertIn("refresh_receipt", inspect.signature(publisher).parameters)
        blocked_proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
        )
        blocked = publisher(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
            receipt=blocked_proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["resume_state"], "merged")
        self.assertEqual(
            blocked["evidence"]["base_recovery"]["refresh_receipt"],
            unknown.to_dict(),
        )

        refresh = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:08:10Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=merge,
        )
        proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        forged_core = {
            **{
                key: value
                for key, value in proof.to_dict().items()
                if key != "receipt_digest"
            },
            "refresh_receipt_digest": unknown.receipt_digest,
        }
        forged = bridge.BaseVerificationReceiptV1.from_dict(
            {
                **forged_core,
                "receipt_digest": contract_digest(forged_core),
            }
        )
        with self.assertRaisesRegex(ValueError, "E_BASE_VERIFICATION"):
            publisher(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=unknown,
                receipt=forged,
                current_branch="codex/squash-contract",
            )
        state = store.status("TASK-SQUASH-MERGE")
        self.assertEqual(state["state"], "blocked")
        self.assertNotEqual(state["state"], "base_verified")

    def test_first_unknown_refresh_rejects_redigested_pass_in_recovery(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskStore

        _, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        unknown = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="UNKNOWN",
            observed_at="2026-08-09T10:08:00Z",
        )
        blocked_proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
        )
        blocked = store.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
            receipt=blocked_proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(blocked["state"], "blocked")
        recovered_store = TaskStore(self.repository / ".git")

        redigested_core = {
            **{
                key: value
                for key, value in unknown.to_dict().items()
                if key != "receipt_digest"
            },
            "status": "PASS",
            "observed_ref": "refs/remotes/origin/main",
            "observed_sha": merge,
        }
        redigested = bridge.BaseRefreshReceiptV1.from_dict(
            {
                **redigested_core,
                "receipt_digest": contract_digest(redigested_core),
            }
        )
        pass_proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=redigested,
        )
        self.assertEqual(pass_proof.status, "PASS")
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            recovered_store.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=redigested,
                receipt=pass_proof,
                current_branch="codex/squash-contract",
            )
        state = recovered_store.status("TASK-SQUASH-MERGE")
        self.assertEqual(state["state"], "blocked")
        self.assertNotEqual(state["state"], "base_verified")

    def test_first_pass_refresh_is_durable_non_authorizing_and_closes(
        self,
    ) -> None:
        import json

        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        _, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        refresh = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:08:00Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=merge,
        )
        proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        verified = store.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        self.assertIn("base_refresh_observation", verified)
        marker = verified["base_refresh_observation"]
        self.assertEqual(marker["refresh_receipt"], refresh.to_dict())
        self.assertEqual(marker["merge_sha"], merge)
        self.assertEqual(marker["base"], "main")
        self.assertEqual(marker["base_ref"], "refs/remotes/origin/main")
        self.assertEqual(marker["task_id"], "TASK-SQUASH-MERGE")
        self.assertEqual(marker["task_digest"], plan.task_digest)
        self.assertEqual(marker["effect_plan_digest"], plan.plan_digest)
        self.assertEqual(
            marker["integration_receipt_digest"],
            integration_receipt.receipt_digest,
        )
        self.assertEqual(marker["generation"] + 1, verified["generation"])
        self.assertFalse(marker["authorizes"])
        registry_path = (
            self.repository / ".git" / "codex-control-plane"
            / "base-refresh-observations" / "TASK-SQUASH-MERGE.json"
        )
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(registry),
            {
                "schema_version", "kind", "task_id", "task_digest",
                "run_plan_digest", "generation", "repository", "remote",
                "remote_url", "remote_url_digest", "remote_identity",
                "remote_identity_digest",
                "base", "base_ref", "policy_digest", "effect_plan_digest",
                "integration_receipt_digest", "merge_sha", "refresh_receipt",
                "refresh_receipt_digest", "authorizes", "registry_digest",
            },
        )
        self.assertEqual(registry["refresh_receipt"], refresh.to_dict())
        self.assertFalse(registry["authorizes"])
        replay = store.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(replay, verified)
        closed = store.close(
            "TASK-SQUASH-MERGE",
            current_branch="codex/squash-contract",
        )
        self.assertEqual(closed["state"], "closed")

    def test_redigested_marker_and_recovery_cannot_override_first_registry(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskStore, _atomic_json

        _, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        unknown = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="UNKNOWN",
            observed_at="2026-08-09T10:08:00Z",
        )
        blocked_proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
        )
        store.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
            receipt=blocked_proof,
            current_branch="codex/squash-contract",
        )
        registry_path = (
            self.repository / ".git" / "codex-control-plane"
            / "base-refresh-observations" / "TASK-SQUASH-MERGE.json"
        )
        registry_before = registry_path.read_bytes()
        redigested_core = {
            **{
                key: value
                for key, value in unknown.to_dict().items()
                if key != "receipt_digest"
            },
            "status": "PASS",
            "observed_ref": "refs/remotes/origin/main",
            "observed_sha": merge,
        }
        redigested = bridge.BaseRefreshReceiptV1.from_dict(
            {
                **redigested_core,
                "receipt_digest": contract_digest(redigested_core),
            }
        )
        pass_proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=redigested,
        )

        tampered = store.status("TASK-SQUASH-MERGE")
        marker = tampered["base_refresh_observation"]
        original_marker = copy.deepcopy(marker)
        marker["refresh_receipt"] = redigested.to_dict()
        marker["refresh_receipt_digest"] = redigested.receipt_digest
        marker_core = {
            key: value for key, value in marker.items() if key != "marker_digest"
        }
        marker["marker_digest"] = contract_digest(marker_core)
        _atomic_json(store._path("TASK-SQUASH-MERGE"), tampered)
        recovered = TaskStore(self.repository / ".git")
        before = recovered.status("TASK-SQUASH-MERGE")
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            recovered.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=redigested,
                receipt=pass_proof,
                current_branch="codex/squash-contract",
            )
        self.assertEqual(recovered.status("TASK-SQUASH-MERGE"), before)
        self.assertEqual(registry_path.read_bytes(), registry_before)

        tampered = recovered.status("TASK-SQUASH-MERGE")
        tampered["base_refresh_observation"] = original_marker
        recovery = tampered["evidence"]["base_recovery"]
        recovery["refresh_receipt"] = redigested.to_dict()
        recovery["refresh_receipt_digest"] = redigested.receipt_digest
        _atomic_json(recovered._path("TASK-SQUASH-MERGE"), tampered)
        before = recovered.status("TASK-SQUASH-MERGE")
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            recovered.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=unknown,
                receipt=blocked_proof,
                current_branch="codex/squash-contract",
            )
        self.assertEqual(recovered.status("TASK-SQUASH-MERGE"), before)
        self.assertEqual(registry_path.read_bytes(), registry_before)

    def test_refresh_registry_fault_boundaries_preserve_first_receipt(
        self,
    ) -> None:
        from unittest.mock import patch

        from control_plane.contracts import contract_digest
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        _, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        unknown = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="UNKNOWN",
            observed_at="2026-08-09T10:08:00Z",
        )
        blocked_proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
        )
        registrar = getattr(store, "_register_base_refresh_observation", None)
        self.assertTrue(callable(registrar), "write-once registry is missing")
        registry_path = (
            self.repository / ".git" / "codex-control-plane"
            / "base-refresh-observations" / "TASK-SQUASH-MERGE.json"
        )
        with (
            patch.object(
                store,
                "_register_base_refresh_observation",
                side_effect=RuntimeError("fault-before-registry"),
            ),
            self.assertRaisesRegex(RuntimeError, "fault-before-registry"),
        ):
            store.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=unknown,
                receipt=blocked_proof,
                current_branch="codex/squash-contract",
            )
        self.assertFalse(registry_path.exists())
        self.assertEqual(store.status("TASK-SQUASH-MERGE")["state"], "merged")

        with (
            patch.object(
                lifecycle,
                "_atomic_json",
                side_effect=RuntimeError("fault-after-registry"),
            ),
            self.assertRaisesRegex(RuntimeError, "fault-after-registry"),
        ):
            store.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=unknown,
                receipt=blocked_proof,
                current_branch="codex/squash-contract",
            )
        self.assertTrue(registry_path.is_file())
        self.assertEqual(store.status("TASK-SQUASH-MERGE")["state"], "merged")

        redigested_core = {
            **{
                key: value
                for key, value in unknown.to_dict().items()
                if key != "receipt_digest"
            },
            "status": "PASS",
            "observed_ref": "refs/remotes/origin/main",
            "observed_sha": merge,
        }
        redigested = bridge.BaseRefreshReceiptV1.from_dict(
            {
                **redigested_core,
                "receipt_digest": contract_digest(redigested_core),
            }
        )
        pass_proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=redigested,
        )
        recovered = TaskStore(self.repository / ".git")
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            recovered.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=redigested,
                receipt=pass_proof,
                current_branch="codex/squash-contract",
            )
        blocked = recovered.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=unknown,
            receipt=blocked_proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(blocked["state"], "blocked")

    def test_refresh_registry_survives_reopen_under_umask_0777(self) -> None:
        import os
        import stat

        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskStore

        _, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        refresh = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:08:00Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=merge,
        )
        proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        registry_dir = (
            self.repository / ".git" / "codex-control-plane"
            / "base-refresh-observations"
        )

        def restore_directory_mode() -> None:
            if registry_dir.exists() and not registry_dir.is_symlink():
                registry_dir.chmod(0o700)

        self.addCleanup(restore_directory_mode)
        previous_umask = os.umask(0o777)
        try:
            registry_dir.mkdir(parents=True, exist_ok=True)
            try:
                verified = store.publish_base_verification(
                    "TASK-SQUASH-MERGE",
                    effect_plan=plan,
                    integration_receipt=integration_receipt,
                    refresh_receipt=refresh,
                    receipt=proof,
                    current_branch="codex/squash-contract",
                )
            except (OSError, ValueError) as error:
                self.fail(f"secure registry creation failed under umask: {error}")
        finally:
            os.umask(previous_umask)
        registry_path = registry_dir / "TASK-SQUASH-MERGE.json"
        self.assertEqual(stat.S_IMODE(registry_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(registry_path.stat().st_mode), 0o600)
        self.assertEqual(registry_dir.stat().st_uid, os.getuid())
        self.assertEqual(registry_path.stat().st_uid, os.getuid())
        self.assertEqual(registry_path.stat().st_nlink, 1)
        reopened = TaskStore(self.repository / ".git")
        replay = reopened.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(replay, verified)

    def test_refresh_registry_rejects_suspicious_directory_and_leaf(self) -> None:
        import os
        from unittest.mock import patch

        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        _, merge = self._git_commit_pair()
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref",
                "refs/remotes/origin/main", merge,
            ],
            check=True,
        )
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        refresh = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="PASS",
            observed_at="2026-08-09T10:08:00Z",
            observed_ref="refs/remotes/origin/main",
            observed_sha=merge,
        )
        proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        store.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        registry_path = (
            self.repository / ".git" / "codex-control-plane"
            / "base-refresh-observations" / "TASK-SQUASH-MERGE.json"
        )
        registry_dir = registry_path.parent
        reopened = TaskStore(self.repository / ".git")

        registry_path.chmod(0o660)
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            reopened.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=proof,
                current_branch="codex/squash-contract",
            )
        self.assertTrue(registry_path.is_file())
        registry_path.chmod(0o600)

        registry_dir.chmod(0o770)
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            reopened.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=proof,
                current_branch="codex/squash-contract",
            )
        self.assertTrue(registry_path.is_file())
        registry_dir.chmod(0o700)

        hardlink = registry_dir / "registry-hardlink.json"
        os.link(registry_path, hardlink)
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            reopened.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=proof,
                current_branch="codex/squash-contract",
            )
        self.assertTrue(hardlink.is_file())
        hardlink.unlink()

        leaf_backup = registry_dir / "registry-leaf-backup.json"
        registry_path.rename(leaf_backup)
        registry_path.symlink_to(leaf_backup)
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            reopened.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=proof,
                current_branch="codex/squash-contract",
            )
        self.assertTrue(registry_path.is_symlink())
        registry_path.unlink()
        leaf_backup.rename(registry_path)

        real_dir = registry_dir.with_name("base-refresh-observations-real")
        registry_dir.rename(real_dir)
        registry_dir.symlink_to(real_dir, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            reopened.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=proof,
                current_branch="codex/squash-contract",
            )
        self.assertTrue(registry_dir.is_symlink())
        registry_dir.unlink()
        real_dir.rename(registry_dir)

        actual_uid = os.getuid()
        with (
            patch.object(lifecycle.os, "getuid", return_value=actual_uid + 1),
            self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"),
        ):
            reopened.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=proof,
                current_branch="codex/squash-contract",
            )
        self.assertTrue(registry_path.is_file())

    def test_base_refresh_uses_exact_plan_url_not_mutable_remote_alias(
        self,
    ) -> None:
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge

        _, merge = self._git_commit_pair()
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        bare_b = Path(self.temp.name) / "split-remote-b.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare_b)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repository), "push", str(bare_b),
                f"{merge}:refs/heads/main",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repository), "remote", "set-url",
                "origin", str(bare_b),
            ],
            check=True,
        )
        expected_argv = (
            "git", "-C", str(self.repository.resolve()), "fetch", "--no-tags",
            "--no-prune", "https://github.com/Example/Control-Plane.git",
            "+refs/heads/main:refs/remotes/origin/main",
        )
        host_calls: list[tuple[str, ...]] = []

        def observe_exact_a() -> object:
            host_calls.append(expected_argv)
            subprocess.run(
                [
                    "git", "-C", str(self.repository), "update-ref",
                    "refs/remotes/origin/main", merge,
                ],
                check=True,
            )
            return bridge.build_base_refresh_receipt(
                effect_plan=plan,
                integration_receipt=integration_receipt,
                status="PASS",
                observed_at="2026-08-09T10:08:00Z",
                observed_ref="refs/remotes/origin/main",
                observed_sha=merge,
            )

        refresh = observe_exact_a()
        self.assertIn("remote_url", refresh.to_dict())
        self.assertEqual(refresh.remote_url, plan.remote_url)
        self.assertEqual(refresh.remote_url_digest, plan.remote_url_digest)
        self.assertEqual(refresh.remote_identity, plan.remote_identity)
        self.assertEqual(refresh.refresh_argv, expected_argv)
        self.assertEqual(host_calls, [expected_argv])
        self.assertNotIn(str(bare_b), refresh.refresh_argv)
        proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        verified = store.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(verified["state"], "base_verified")
        marker = verified["base_refresh_observation"]
        self.assertEqual(marker["remote_url"], plan.remote_url)
        self.assertEqual(
            marker["remote_url_digest"], plan.remote_url_digest
        )
        self.assertEqual(marker["remote_identity"], plan.remote_identity)
        self.assertEqual(
            marker["remote_identity_digest"],
            plan.remote_identity_digest,
        )
        registry_path = (
            self.repository / ".git" / "codex-control-plane"
            / "base-refresh-observations" / "TASK-SQUASH-MERGE.json"
        )
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        self.assertEqual(registry["remote_url"], plan.remote_url)
        self.assertEqual(
            registry["remote_url_digest"], plan.remote_url_digest
        )
        self.assertEqual(registry["remote_identity"], plan.remote_identity)
        self.assertEqual(
            registry["remote_identity_digest"],
            plan.remote_identity_digest,
        )

    def test_blocked_exact_remote_cannot_fall_back_to_alias_or_redigest_identity(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        import control_plane.git_state as git_state
        import control_plane.host_bridge as bridge
        from control_plane.lifecycle import TaskStore, _atomic_json

        _, merge = self._git_commit_pair()
        store, plan, integration_receipt = self._merged_store(merge_sha=merge)
        bare_b = Path(self.temp.name) / "blocked-split-remote-b.git"
        subprocess.run(
            ["git", "init", "--bare", str(bare_b)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repository), "push", str(bare_b),
                f"{merge}:refs/heads/main",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repository), "remote", "set-url",
                "origin", str(bare_b),
            ],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(self.repository), "update-ref", "-d",
                "refs/remotes/origin/main",
            ],
            check=True,
        )
        refresh = bridge.build_base_refresh_receipt(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            status="UNKNOWN",
            observed_at="2026-08-09T10:08:00Z",
        )
        self.assertEqual(refresh.refresh_argv[6], plan.remote_url)
        self.assertNotIn(str(bare_b), refresh.refresh_argv)
        proof = git_state.verify_refreshed_base_containment(
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
        )
        self.assertEqual(proof.reason_code, "BASE_REFRESH_UNKNOWN")
        blocked = store.publish_base_verification(
            "TASK-SQUASH-MERGE",
            effect_plan=plan,
            integration_receipt=integration_receipt,
            refresh_receipt=refresh,
            receipt=proof,
            current_branch="codex/squash-contract",
        )
        self.assertEqual(blocked["state"], "blocked")

        drift_core = {
            **{
                key: value
                for key, value in refresh.to_dict().items()
                if key != "receipt_digest"
            },
            "remote_url": "https://github.com/example/other.git",
            "remote_url_digest": contract_digest(
                "https://github.com/example/other.git"
            ),
            "remote_identity": "example/other",
            "remote_identity_digest": contract_digest("example/other"),
        }
        drift_argv = list(drift_core["refresh_argv"])
        drift_argv[6] = "https://github.com/example/other.git"
        drift_core["refresh_argv"] = drift_argv
        drift_core["refresh_argv_digest"] = contract_digest(drift_argv)
        drifted = bridge.BaseRefreshReceiptV1.from_dict(
            {
                **drift_core,
                "receipt_digest": contract_digest(drift_core),
            }
        )
        with self.assertRaisesRegex(ValueError, "E_BASE_VERIFICATION"):
            git_state.verify_refreshed_base_containment(
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=drifted,
            )

        tampered = store.status("TASK-SQUASH-MERGE")
        marker = tampered["base_refresh_observation"]
        marker["remote_url"] = "https://github.com/example/other.git"
        marker["remote_url_digest"] = contract_digest(marker["remote_url"])
        marker["remote_identity"] = "example/other"
        marker["remote_identity_digest"] = contract_digest(
            marker["remote_identity"]
        )
        marker_core = {
            key: value for key, value in marker.items() if key != "marker_digest"
        }
        marker["marker_digest"] = contract_digest(marker_core)
        _atomic_json(store._path("TASK-SQUASH-MERGE"), tampered)
        reopened = TaskStore(self.repository / ".git")
        with self.assertRaisesRegex(ValueError, "E_BASE_REFRESH_OBSERVATION"):
            reopened.publish_base_verification(
                "TASK-SQUASH-MERGE",
                effect_plan=plan,
                integration_receipt=integration_receipt,
                refresh_receipt=refresh,
                receipt=proof,
                current_branch="codex/squash-contract",
            )
        self.assertEqual(reopened.status("TASK-SQUASH-MERGE")["state"], "blocked")

    def test_generic_host_observation_cannot_bypass_merge_or_base_protocol(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge

        store = self._seed_pr_ready_store()

        def validated(target: str, evidence: dict[str, object]):
            raw = lifecycle_observation(
                bridge.GitHubObservation,
                observation_id=f"legacy-{target}",
                invocation_id=f"legacy-{target}",
                task_digest=self.run_plan["task_digest"],
                repository_identity=str(self.repository.resolve()),
                worktree_identity=str(self.repository.resolve()),
                branch="codex/squash-contract",
                prior_head="b" * 40,
                target_state=target,
                session_id="session-legacy-integration",
                provider="github",
                subject_digest="sha256:" + "8" * 64,
                evidence=evidence,
                observed_at_monotonic=100.0,
                freshness_deadline=130.0,
            )
            return bridge.validate_github_observation(
                raw,
                expected_task_digest=self.run_plan["task_digest"],
                expected_repo=self.repository,
                expected_worktree=self.repository,
                expected_branch="codex/squash-contract",
                expected_prior_head="b" * 40,
                expected_target_state=target,
                expected_session_id="session-legacy-integration",
                expected_invocation_id=f"legacy-{target}",
                clock=lambda: 100.0,
            )

        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_PROOF"):
            store.transition(
                "TASK-SQUASH-MERGE",
                "merged",
                evidence=validated("merged", {"merge_commit": "c" * 40}),
                current_branch="codex/squash-contract",
            )

        merged_store, _, _ = self._merged_store(merge_sha="c" * 40)
        with self.assertRaisesRegex(ValueError, "E_INTEGRATION_PROOF"):
            merged_store.transition(
                "TASK-SQUASH-MERGE",
                "base_verified",
                evidence=validated(
                    "base_verified", {"remote_base": "c" * 40}
                ),
                current_branch="codex/squash-contract",
            )


if __name__ == "__main__":
    unittest.main()
