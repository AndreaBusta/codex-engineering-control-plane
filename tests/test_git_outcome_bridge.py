from __future__ import annotations

import copy
from contextlib import contextmanager
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.host_adapter_test_support import (
    independent_review_receipt,
    lifecycle_observation,
)
from tests.git_test_support import FIXTURE_POLICY, GitScenario, git
from tests.router_test_support import task_envelope


def _decision(task: dict[str, object]) -> dict[str, object]:
    from control_plane.contracts import contract_digest

    core = {
        "schema_version": 1,
        "task_id": task["task_id"],
        "mode": "audit",
        "ok": True,
        "decision_ready": False,
        "summary": {
            "tier": "T2",
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
        "approval_boundaries": ["commit", "remote_write", "pull_request"],
        "authorization": {"local_write": True},
        "required_gates": ["gate.relevant-tests", "gate.independent-review"],
        "selected_resource_digests": {},
        "matched_routes": ["quality-profile-generic"],
        "facts": {"task_digest": contract_digest(task)},
        "errors": [],
    }
    return {**core, "decision_digest": contract_digest(core)}


class _FakeLocalPushHost:
    """Test-only host: local bare repository, never a network remote."""

    def __init__(self, repository: Path, *, observation_available: bool = True):
        self.repository = repository
        self.observation_available = observation_available
        self.write_calls = 0
        self.observe_calls = 0

    def execute(self, payload: dict[str, object], *, timeout: str | None = None) -> None:
        from control_plane.host_bridge import OutcomeEffectPlanV1

        plan = OutcomeEffectPlanV1.from_dict(payload)
        self.write_calls += 1
        if timeout == "before_send":
            raise TimeoutError("provider result unknown")
        completed = subprocess.run(
            plan.argv,
            cwd=self.repository,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr.decode("utf-8", errors="replace"))
        if timeout == "after_send":
            raise TimeoutError("provider result unknown")

    def execute_prepared(
        self,
        *,
        store,
        task_id: str,
        plan,
        current_branch: str,
        timeout: str | None = None,
        before_execution=None,
    ) -> None:
        validated = type(plan).from_dict(plan.to_dict())
        store.arm_remote_write_observation(
            task_id,
            effect_plan=validated,
            current_branch=current_branch,
        )
        if before_execution is not None:
            before_execution()
        validated = store.revalidate_remote_write_before_execution(
            task_id,
            effect_plan=validated,
            current_branch=current_branch,
        )
        self.execute(validated.to_dict(), timeout=timeout)

    def observe(self, plan):
        from control_plane.host_bridge import build_remote_outcome_receipt

        self.observe_calls += 1
        if not self.observation_available:
            return build_remote_outcome_receipt(
                effect_plan=plan,
                status="UNKNOWN",
                observed_at="2026-08-09T10:00:00Z",
            )
        result = subprocess.run(
            plan.observation_argv,
            cwd=self.repository,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        observed_head = result.stdout.split()[0] if result.stdout.split() else None
        status = "PASS" if observed_head == plan.head_sha else "FAIL"
        return build_remote_outcome_receipt(
            effect_plan=plan,
            status=status,
            observed_at="2026-08-09T10:00:00Z",
            observed_repository=plan.repository if observed_head else None,
            observed_remote=plan.remote if observed_head else None,
            observed_base=plan.base if observed_head else None,
            observed_branch=plan.branch if observed_head else None,
            observed_head_sha=observed_head,
        )


class _FakePullRequestHost:
    """Test-only in-memory PR provider; never invokes gh or a network API."""

    def __init__(self, *, existing=None, observation_available: bool = True):
        self.existing = copy.deepcopy(existing)
        self.observation_available = observation_available
        self.write_calls = 0
        self.observe_calls = 0

    @staticmethod
    def matching(plan, *, number: int = 7, disposition: str = "observed_existing"):
        return {
            "repository": plan.repository,
            "remote": plan.remote,
            "base": plan.base,
            "branch": plan.branch,
            "head_sha": plan.head_sha,
            "number": number,
            "url": f"https://github.com/example/control-plane/pull/{number}",
            "draft": True,
            "disposition": disposition,
        }

    def observe(self, plan):
        from control_plane.host_bridge import build_pull_request_outcome_receipt

        self.observe_calls += 1
        if not self.observation_available:
            return build_pull_request_outcome_receipt(
                effect_plan=plan,
                status="UNKNOWN",
                observed_at="2026-08-09T11:00:00Z",
            )
        if self.existing is None:
            return build_pull_request_outcome_receipt(
                effect_plan=plan,
                status="ABSENT",
                observed_at="2026-08-09T11:00:00Z",
            )
        exact = all(
            self.existing.get(name) == getattr(plan, plan_name)
            for name, plan_name in (
                ("repository", "repository"),
                ("remote", "remote"),
                ("base", "base"),
                ("branch", "branch"),
                ("head_sha", "head_sha"),
            )
        ) and self.existing.get("draft") is True
        return build_pull_request_outcome_receipt(
            effect_plan=plan,
            status="PASS" if exact else "FAIL",
            observed_at="2026-08-09T11:00:00Z",
            observed_repository=self.existing.get("repository"),
            observed_remote=self.existing.get("remote"),
            observed_base=self.existing.get("base"),
            observed_branch=self.existing.get("branch"),
            observed_head_sha=self.existing.get("head_sha"),
            observed_pr_number=self.existing.get("number"),
            observed_pr_url=self.existing.get("url"),
            observed_pr_draft=self.existing.get("draft"),
            disposition=self.existing.get("disposition", "observed_existing"),
        )

    def create(self, plan, *, timeout: str | None = None) -> None:
        self.write_calls += 1
        if timeout == "before_send":
            raise TimeoutError("provider result unknown")
        self.existing = self.matching(plan, disposition="created")
        if timeout == "after_send":
            raise TimeoutError("provider result unknown")

    def execute_prepared(
        self,
        *,
        store,
        task_id: str,
        plan,
        current_branch: str,
        timeout: str | None = None,
    ) -> None:
        validated = type(plan).from_dict(plan.to_dict())
        store.arm_pull_request_draft_creation(
            task_id, effect_plan=validated, current_branch=current_branch
        )
        validated = store.revalidate_pull_request_draft_before_execution(
            task_id, effect_plan=validated, current_branch=current_branch
        )
        self.create(validated, timeout=timeout)


class GitOutcomeBridgeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.contracts import contract_digest

        self.scenario = GitScenario()
        self.addCleanup(self.scenario.close)
        self.branch = "codex/outcome-push"
        self.scenario.checkout_feature(self.branch)
        (self.scenario.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        git(self.scenario.repo, "add", "feature.txt")
        git(self.scenario.repo, "commit", "-m", "test: feature")
        self.head = git(self.scenario.repo, "rev-parse", "HEAD")
        self.task_digest = contract_digest({"task": "remote-write"})
        self.policy_digest = contract_digest({"policy": "remote-write"})

    def _binding(self):
        from control_plane.run_workflow import (
            advance_outcome_binding,
            build_outcome_binding,
            build_run_plan,
        )

        task = task_envelope(
            task_id="TASK-REMOTE-WRITE",
            requested_outcome="pull_request",
            scope_paths=["feature.txt"],
            effects=[
                {"name": "local_write", "source": "user_explicit"},
                {"name": "commit", "source": "user_explicit"},
                {"name": "remote_write", "source": "user_explicit"},
                {"name": "pull_request", "source": "user_explicit"},
            ],
        )
        plan = build_run_plan(
            task=task,
            decision=_decision(task),
            repository=self.scenario.repo,
            branch=self.branch,
            head="a" * 40,
            session_id="local-correlator-remote-write",
            prepared_at="2026-08-09T09:00:00Z",
        )
        binding = build_outcome_binding(
            run_plan=plan,
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
        return advance_outcome_binding(
            binding,
            effect_id="commit",
            observation={
                "parent_head": "a" * 40,
                "tree_digest": "sha256:" + "1" * 64,
                "committed_head": self.head,
            },
        )

    def _plan(self):
        from control_plane.host_bridge import build_remote_write_effect_plan

        return build_remote_write_effect_plan(
            outcome_binding=self._binding(),
            task_digest=self.task_digest,
            remote="origin",
            base="main",
            scope_paths=("feature.txt",),
            policy_digest=self.policy_digest,
        )

    def _pushed_binding(self):
        from control_plane.run_workflow import advance_outcome_binding

        binding = self._binding()
        return advance_outcome_binding(
            binding,
            effect_id="remote_write",
            observation={"pushed_head": binding["committed_head"]},
        )

    def _pr_plan(self, *, title="Draft change", body="Bounded draft body"):
        from control_plane.host_bridge import build_pull_request_effect_plan
        from control_plane.policy import parse_required_check_selector

        git(
            self.scenario.repo,
            "remote",
            "set-url",
            "--push",
            "origin",
            "https://github.com/Example/Control-Plane.git",
        )
        return build_pull_request_effect_plan(
            outcome_binding=self._pushed_binding(),
            task_digest=self.task_digest,
            remote="origin",
            base="main",
            scope_paths=("feature.txt",),
            policy_digest=self.policy_digest,
            title=title,
            body=body,
            required_checks=(
                parse_required_check_selector("contract:control-plane:SUCCESS"),
            ),
        )

    def test_remote_write_contract_api_exists(self) -> None:
        from control_plane.host_bridge import (
            OutcomeEffectPlanV1,
            RemoteOutcomeReceiptV1,
        )

        self.assertEqual(OutcomeEffectPlanV1.__name__, "OutcomeEffectPlanV1")
        self.assertEqual(RemoteOutcomeReceiptV1.__name__, "RemoteOutcomeReceiptV1")

    def test_pull_request_draft_contract_api_exists(self) -> None:
        from control_plane.host_bridge import (
            OutcomeEffectPlanV1,
            RemoteOutcomeReceiptV1,
            build_pull_request_effect_plan,
            build_pull_request_outcome_receipt,
        )

        self.assertEqual(OutcomeEffectPlanV1.__name__, "OutcomeEffectPlanV1")
        self.assertEqual(RemoteOutcomeReceiptV1.__name__, "RemoteOutcomeReceiptV1")
        self.assertTrue(callable(build_pull_request_effect_plan))
        self.assertTrue(callable(build_pull_request_outcome_receipt))

    def test_pull_request_plan_is_closed_bound_and_draft_only(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import OutcomeEffectPlanV1

        plan = self._pr_plan()
        payload = plan.to_dict()
        self.assertEqual(plan.effect, "pull_request")
        self.assertTrue(plan.draft)
        self.assertFalse(plan.authorizes)
        self.assertEqual(plan.title_digest, contract_digest(plan.title))
        self.assertEqual(plan.body_digest, contract_digest(plan.body))
        self.assertEqual(plan.operation, "create_draft_pull_request")
        self.assertEqual(plan.argv[0], "pull_request.create_draft")
        self.assertNotIn("gh", plan.argv)
        self.assertEqual(OutcomeEffectPlanV1.from_dict(payload), plan)
        self.assertTrue(
            {
                "session_id",
                "invocation_id",
                "nonce",
                "ttl",
                "grant",
                "credential",
            }.isdisjoint(payload)
        )
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_EFFECT_PLAN"):
            OutcomeEffectPlanV1.from_dict({**payload, "unexpected": True})

        push_payload = self._plan().to_dict()
        self.assertTrue(
            all(
                push_payload[field] is None
                for field in (
                    "title",
                    "title_digest",
                    "body",
                    "body_digest",
                    "draft",
                    "operation",
                    "operation_digest",
                )
            )
        )
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_EFFECT_PLAN"):
            OutcomeEffectPlanV1.from_dict(
                {**push_payload, "draft": True}
            )

    def test_pull_request_content_is_sanitized_and_bounded(self) -> None:
        credential_like_body = "token=" + "github_" + "pat_" + "ABCDEFGHIJK"
        private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
        invalid = (
            {"title": "bad\nline", "body": "body"},
            {"title": " " + "title", "body": "body"},
            {"title": "title", "body": credential_like_body},
            {"title": "title", "body": private_key_marker},
            {"title": "x" * 181, "body": "body"},
            {"title": "title", "body": "x" * 32_769},
            {"title": "title", "body": "bad\x00body"},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "E_OUTCOME_EFFECT_PLAN"):
                    self._pr_plan(**values)

    def test_pull_request_receipt_is_closed_and_exact(self) -> None:
        from control_plane.host_bridge import (
            RemoteOutcomeReceiptV1,
            build_pull_request_outcome_receipt,
        )

        plan = self._pr_plan()
        receipt = build_pull_request_outcome_receipt(
            effect_plan=plan,
            status="PASS",
            observed_at="2026-08-09T11:00:00Z",
            observed_repository=plan.repository,
            observed_remote=plan.remote,
            observed_base=plan.base,
            observed_branch=plan.branch,
            observed_head_sha=plan.head_sha,
            observed_pr_number=7,
            observed_pr_url="https://github.com/example/control-plane/pull/7",
            observed_pr_draft=True,
            disposition="observed_existing",
        )
        self.assertEqual(RemoteOutcomeReceiptV1.from_dict(receipt.to_dict()), receipt)
        self.assertFalse(receipt.authorizes)
        self.assertEqual(receipt.observed_pr_number, 7)
        self.assertTrue(receipt.observed_pr_draft)
        self.assertEqual(receipt.disposition, "observed_existing")
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
            RemoteOutcomeReceiptV1.from_dict(
                {**receipt.to_dict(), "credential": "secret"}
            )

    def test_plan_is_closed_non_authorizing_and_only_normal_feature_refspec(self) -> None:
        from control_plane.host_bridge import OutcomeEffectPlanV1

        plan = self._plan()
        self.assertEqual(
            plan.argv,
            (
                "git",
                "push",
                str(self.scenario.remote),
                f"{self.head}:refs/heads/{self.branch}",
            ),
        )
        payload = plan.to_dict()
        self.assertFalse(payload["authorizes"])
        self.assertEqual(OutcomeEffectPlanV1.from_dict(payload), plan)
        forbidden = {"session_id", "invocation_id", "nonce", "ttl", "grant", "credential"}
        self.assertTrue(forbidden.isdisjoint(payload))

        fake = _FakeLocalPushHost(self.scenario.repo)
        mutations = (
            {**payload, "argv": ["git", "push", "--force", "origin", f"{self.branch}:{self.branch}"]},
            {**payload, "argv": ["git", "push", "--force-with-lease", "origin", f"{self.branch}:{self.branch}"]},
            {**payload, "argv": ["git", "push", "origin", f"{self.branch}:main"]},
            {**payload, "argv": ["git", "push", "origin", f"{self.branch}:codex/other"]},
            {**payload, "unexpected": True},
        )
        for mutation in mutations:
            with self.subTest(argv=mutation.get("argv")):
                with self.assertRaisesRegex(ValueError, "E_OUTCOME_EFFECT_PLAN"):
                    fake.execute(mutation)
        self.assertEqual(fake.write_calls, 0)

    def test_exact_pass_receipt_advances_remote_write_cas(self) -> None:
        from control_plane.host_bridge import (
            RemoteOutcomeReceiptV1,
            apply_remote_write_receipt,
        )

        binding = self._binding()
        plan = self._plan()
        fake = _FakeLocalPushHost(self.scenario.repo)
        fake.execute(plan.to_dict())
        receipt = fake.observe(plan)
        self.assertEqual(RemoteOutcomeReceiptV1.from_dict(receipt.to_dict()), receipt)
        self.assertFalse(receipt.authorizes)

        advanced = apply_remote_write_receipt(
            outcome_binding=binding,
            effect_plan=plan,
            receipt=receipt,
        )
        self.assertEqual(advanced["pushed_head"], self.head)
        self.assertEqual(advanced["consumed_effect_ids"][-1], "remote_write")

        with self.assertRaisesRegex(ValueError, "E_OUTCOME_REPLAY"):
            apply_remote_write_receipt(
                outcome_binding=advanced,
                effect_plan=plan,
                receipt=receipt,
            )

    def test_timeout_observes_once_and_never_retries_blindly(self) -> None:
        from control_plane.host_bridge import apply_remote_write_receipt

        binding = self._binding()
        plan = self._plan()
        fake = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaises(TimeoutError):
            fake.execute(plan.to_dict(), timeout="after_send")
        receipt = fake.observe(plan)
        advanced = apply_remote_write_receipt(
            outcome_binding=binding,
            effect_plan=plan,
            receipt=receipt,
        )
        self.assertEqual(advanced["pushed_head"], self.head)
        self.assertEqual((fake.write_calls, fake.observe_calls), (1, 1))

    def test_unknown_and_fail_observations_do_not_trigger_a_second_write(self) -> None:
        from control_plane.host_bridge import apply_remote_write_receipt

        binding = self._binding()
        plan = self._plan()
        unknown_host = _FakeLocalPushHost(
            self.scenario.repo, observation_available=False
        )
        with self.assertRaises(TimeoutError):
            unknown_host.execute(plan.to_dict(), timeout="before_send")
        unknown = unknown_host.observe(plan)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_UNKNOWN"):
            apply_remote_write_receipt(
                outcome_binding=binding,
                effect_plan=plan,
                receipt=unknown,
            )
        self.assertEqual((unknown_host.write_calls, unknown_host.observe_calls), (1, 1))

        failed_host = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaises(TimeoutError):
            failed_host.execute(plan.to_dict(), timeout="before_send")
        failed = failed_host.observe(plan)
        self.assertEqual(failed.status, "FAIL")
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_FAIL"):
            apply_remote_write_receipt(
                outcome_binding=binding,
                effect_plan=plan,
                receipt=failed,
            )
        self.assertEqual((failed_host.write_calls, failed_host.observe_calls), (1, 1))

    def test_receipt_rejects_binding_drift_and_forbidden_authority_fields(self) -> None:
        from control_plane.host_bridge import (
            RemoteOutcomeReceiptV1,
            apply_remote_write_receipt,
            build_remote_outcome_receipt,
        )

        binding = self._binding()
        plan = self._plan()
        receipt = build_remote_outcome_receipt(
            effect_plan=plan,
            status="PASS",
            observed_at="2026-08-09T10:00:00Z",
            observed_repository=plan.repository,
            observed_remote=plan.remote,
            observed_base=plan.base,
            observed_branch=plan.branch,
            observed_head_sha=plan.head_sha,
        )
        for field, value in (
            ("session_id", "session"),
            ("invocation_id", "invocation"),
            ("nonce", "nonce"),
            ("credential", "secret"),
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
                    RemoteOutcomeReceiptV1.from_dict({**receipt.to_dict(), field: value})

        drifted = copy.deepcopy(binding)
        drifted["committed_head"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "E_OUTCOME_BINDING"):
            apply_remote_write_receipt(
                outcome_binding=drifted,
                effect_plan=plan,
                receipt=receipt,
            )

    def test_apply_revalidates_a_mutated_receipt_instance(self) -> None:
        from control_plane.host_bridge import (
            apply_remote_write_receipt,
            build_remote_outcome_receipt,
        )

        binding = self._binding()
        plan = self._plan()
        receipt = build_remote_outcome_receipt(
            effect_plan=plan,
            status="FAIL",
            observed_at="2026-08-09T10:00:00Z",
        )
        object.__setattr__(receipt, "status", "PASS")
        object.__setattr__(receipt, "observed_repository", plan.repository)
        object.__setattr__(receipt, "observed_remote", plan.remote)
        object.__setattr__(receipt, "observed_base", plan.base)
        object.__setattr__(receipt, "observed_branch", plan.branch)
        object.__setattr__(receipt, "observed_head_sha", plan.head_sha)

        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
            apply_remote_write_receipt(
                outcome_binding=binding,
                effect_plan=plan,
                receipt=receipt,
            )


class GitOutcomeBridgePreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import build_remote_write_effect_plan
        from control_plane.lifecycle import TaskStore
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir
        from control_plane.run_workflow import (
            RunStore,
            build_independent_review_receipt,
            prepare_review_packet,
            prepare_run,
            publish_review_ready,
            verify_run,
        )

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scenario = GitScenario()
        self.addCleanup(self.scenario.close)
        (self.scenario.repo / ".codex").mkdir()
        (self.scenario.repo / ".codex" / "project-policy.toml").write_bytes(
            FIXTURE_POLICY.read_bytes()
        )
        (self.scenario.repo / "scripts").mkdir()
        launcher = self.scenario.repo / "scripts" / "control-plane"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)
        (self.scenario.repo / "tests").mkdir()
        (self.scenario.repo / "tests" / "__init__.py").write_text(
            "", encoding="utf-8"
        )
        (self.scenario.repo / "tests" / "test_fixture.py").write_text(
            "import unittest\n\n"
            "class FixtureTests(unittest.TestCase):\n"
            "    def test_fixture(self): self.assertTrue(True)\n",
            encoding="utf-8",
        )
        (self.scenario.repo / "prepared.txt").write_text(
            "baseline\n", encoding="utf-8"
        )
        git(
            self.scenario.repo,
            "add",
            ".codex",
            "scripts",
            "tests",
            "prepared.txt",
        )
        git(self.scenario.repo, "commit", "-m", "test: canonical delivery fixture")
        git(self.scenario.repo, "push", "origin", "main")
        self.state_dir = worktree_git_dir(self.scenario.repo)
        self.branch = "codex/prepared-push"
        self.scenario.checkout_feature(self.branch)
        self.task = task_envelope(
            task_id="TASK-PREPARED-PUSH",
            requested_outcome="pull_request",
            scope_paths=["prepared.txt"],
            effects=[
                {"name": "local_write", "source": "user_explicit"},
                {"name": "commit", "source": "user_explicit"},
                {"name": "remote_write", "source": "user_explicit"},
                {"name": "pull_request", "source": "user_explicit"},
            ],
        )
        prepared = prepare_run(
            task=self.task,
            decision=_decision(self.task),
            repository=self.scenario.repo,
            policy=load_policy(
                self.scenario.repo / ".codex" / "project-policy.toml"
            ),
            session_id="local-correlator-prepared-push",
            prepared_at="2026-08-09T09:00:00Z",
        )
        self.run_plan = prepared["run_plan"]
        review_head = git(self.scenario.repo, "rev-parse", "HEAD")
        (self.scenario.repo / "prepared.txt").write_text(
            "prepared\n", encoding="utf-8"
        )
        verified = verify_run(
            repository=self.scenario.repo,
            task_id=self.task["task_id"],
            observed_at="2026-08-09T09:01:00Z",
        )
        packet = prepare_review_packet(
            self.scenario.repo,
            self.task["task_id"],
            1,
            "independent",
            "sha256:" + "4" * 64,
        )
        runs = RunStore(self.state_dir)
        receipt, review_observation = independent_review_receipt(
            run_store=runs, review_packet=packet,
            findings_digest="sha256:" + "5" * 64,
            critical=0,
            important=0,
            status="PASS",
            observed_at="2026-08-09T09:02:00Z",
        )
        runs.persist_review_receipt(
            self.task["task_id"], packet["packet_digest"], receipt,
            observation=review_observation,
        )
        self.store = TaskStore(self.state_dir)
        state = self.store.status(self.task["task_id"])
        publish_review_ready(
            repository=self.scenario.repo,
            task_id=self.task["task_id"],
            expected_generation=state["generation"],
            receipt_digests=(receipt["receipt_digest"],),
        )
        state = self.store.status(self.task["task_id"])
        review = state["delivery_review_binding"]
        lease = self.store.acquire_delivery_lease(
            self.task["task_id"],
            worktree=str(self.scenario.repo),
            branch=self.branch,
            session_id="prepared-local-session",
            paths=["prepared.txt"],
            policy_digest=contract_digest(
                load_policy(
                    self.scenario.repo / ".codex" / "project-policy.toml"
                )
            ),
            expected_head=review_head,
            diff_digest=review["diff_digest"],
            expected_generation=state["generation"],
        )
        expected_index = Path(self.temp.name) / "expected.index"
        environment = dict(__import__("os").environ)
        environment["GIT_INDEX_FILE"] = str(expected_index)
        for arguments in (("read-tree", "HEAD"), ("add", "--", "prepared.txt")):
            subprocess.run(
                ["git", "-C", str(self.scenario.repo), *arguments],
                check=True,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        expected_tree = subprocess.run(
            ["git", "-C", str(self.scenario.repo), "write-tree"],
            check=True,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.store.prepare_delivery_commit(
            self.task["task_id"],
            lease=lease,
            snapshot_digest=review["binding_digest"],
            allowlist=("prepared.txt",),
            expected_index_tree=expected_tree,
            parent_head=review_head,
            expected_tree=expected_tree,
            message="test: prepared push",
        )
        git(self.scenario.repo, "add", "prepared.txt")
        from control_plane.lifecycle import (
            _delivery_index_paths,
            _delivery_index_tree,
            _delivery_review_diff,
            _delivery_remote_base_matches,
            _delivery_review_diff_matches,
        )

        self.assertEqual(_delivery_index_tree(self.scenario.repo), expected_tree)
        self.assertEqual(_delivery_index_paths(self.scenario.repo), ("prepared.txt",))
        self.assertTrue(
            _delivery_remote_base_matches(self.scenario.repo, lease["base_head"])
        )
        rendered = _delivery_review_diff(
            self.scenario.repo, review_head, ("prepared.txt",)
        )
        self.assertEqual(
            contract_digest({"diff": rendered.hex()}), review["diff_digest"]
        )
        self.assertTrue(
            _delivery_review_diff_matches(
                self.scenario.repo, review, ("prepared.txt",)
            )
        )
        self.store.observe_delivery_index(
            self.task["task_id"], lease=lease, expected_index_tree=expected_tree
        )
        git(self.scenario.repo, "commit", "-m", "test: prepared push")
        self.head = git(self.scenario.repo, "rev-parse", "HEAD")
        from control_plane.host_bridge import (
            observe_local_git_state,
            validate_local_git_observation,
        )

        observed = observe_local_git_state(
            task_state=self.store.status(self.task["task_id"]),
            expected_repo=self.scenario.repo,
            expected_worktree=self.scenario.repo,
            expected_branch=self.branch,
            expected_prior_head=review_head,
            target_state="committed",
            session_id=lease["session_id"],
            invocation_id="prepared-local-commit",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        validated = validate_local_git_observation(
            observed,
            expected_task_digest=self.run_plan["task_digest"],
            expected_repo=self.scenario.repo,
            expected_worktree=self.scenario.repo,
            expected_branch=self.branch,
            expected_prior_head=review_head,
            expected_target_state="committed",
            expected_session_id=lease["session_id"],
            expected_invocation_id="prepared-local-commit",
            clock=lambda: 100.0,
        )
        committed = self.store.publish_delivery_commit(
            self.task["task_id"],
            lease=lease,
            observation=validated,
            current_branch=self.branch,
        )
        self.binding = committed["outcome_binding"]
        self.policy = load_policy(
            self.scenario.repo / ".codex" / "project-policy.toml"
        )
        self.policy_digest = contract_digest(self.policy)
        self.plan = build_remote_write_effect_plan(
            outcome_binding=self.binding,
            task_digest=self.run_plan["task_digest"],
            remote="origin",
            base="main",
            scope_paths=("prepared.txt",),
            policy_digest=self.policy_digest,
        )

    def _prepare(self, *, plan=None):
        return self.store.prepare_remote_write(
            self.task["task_id"],
            effect_plan=self.plan if plan is None else plan,
            current_branch=self.branch,
        )

    def _push(self) -> None:
        prepared = self._prepare()
        self.assertEqual(prepared["state"], "committed")
        fake = _FakeLocalPushHost(self.scenario.repo)
        fake.execute_prepared(
            store=self.store,
            task_id=self.task["task_id"],
            plan=self.plan,
            current_branch=self.branch,
        )
        receipt = fake.observe(self.plan)
        pushed = self.store.publish_remote_write(
            self.task["task_id"],
            effect_plan=self.plan,
            receipt=receipt,
            current_branch=self.branch,
        )
        self.assertEqual(pushed["state"], "pushed")

    def _pull_request_plan(self, *, base: str = "main", title="Draft change", body="Draft body"):
        from control_plane.host_bridge import build_pull_request_effect_plan
        from control_plane.policy import parse_required_check_selector

        git(
            self.scenario.repo,
            "remote",
            "set-url",
            "--push",
            "origin",
            "https://github.com/Example/Control-Plane.git",
        )
        state = self.store.status(self.task["task_id"])
        return build_pull_request_effect_plan(
            outcome_binding=state["outcome_binding"],
            task_digest=self.run_plan["task_digest"],
            remote="origin",
            base=base,
            scope_paths=("prepared.txt",),
            policy_digest=self.policy_digest,
            title=title,
            body=body,
            required_checks=(
                parse_required_check_selector("contract:control-plane:SUCCESS"),
            ),
        )

    @contextmanager
    def _exact_pr_remote(self, plan, *, observed_feature_head=None):
        """Fake only the exact target's read-only ls-remote boundary."""

        from unittest.mock import patch
        from control_plane import lifecycle

        original = lifecycle._delivery_git_text
        base_head = git(
            self.scenario.repo,
            "rev-parse",
            f"refs/remotes/{plan.remote}/{plan.base}",
        )

        def exact_remote_observation(worktree, arguments):
            if arguments[:3] == ("ls-remote", "--heads", plan.remote_url):
                reference = arguments[3]
                if reference == f"refs/heads/{plan.base}":
                    return f"{base_head}\t{reference}"
                if reference == f"refs/heads/{plan.branch}":
                    return f"{observed_feature_head or plan.head_sha}\t{reference}"
                raise ValueError("unexpected exact-target ref")
            return original(worktree, arguments)

        with patch(
            "control_plane.lifecycle._delivery_git_text",
            side_effect=exact_remote_observation,
        ):
            yield

    def _publish_with_exact_pr_remote(self, plan, receipt, *, store=None):
        with self._exact_pr_remote(plan):
            return (store or self.store).publish_pull_request_draft(
                self.task["task_id"],
                effect_plan=plan,
                receipt=receipt,
                current_branch=self.branch,
            )

    def test_prepare_rejects_forged_review_lineage(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import build_remote_write_effect_plan

        for field, value in (
            ("review_head", "b" * 40),
            ("reviewed_tree_digest", "sha256:" + "6" * 64),
            ("reviewed_diff_digest", "sha256:" + "7" * 64),
        ):
            with self.subTest(field=field):
                forged = {**self.binding, field: value}
                forged["binding_digest"] = contract_digest(
                    {
                        key: item
                        for key, item in forged.items()
                        if key != "binding_digest"
                    }
                )
                plan = build_remote_write_effect_plan(
                    outcome_binding=forged,
                    task_digest=self.run_plan["task_digest"],
                    remote="origin",
                    base="main",
                    scope_paths=("prepared.txt",),
                    policy_digest=self.policy_digest,
                )
                with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_PREPARE"):
                    self._prepare(plan=plan)

    def test_prepare_rejects_false_declared_base(self) -> None:
        from control_plane.host_bridge import build_remote_write_effect_plan

        false_base = build_remote_write_effect_plan(
            outcome_binding=self.binding,
            task_digest=self.run_plan["task_digest"],
            remote="origin",
            base="trunk",
            scope_paths=("prepared.txt",),
            policy_digest=self.policy_digest,
        )
        with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_PREPARE"):
            self._prepare(plan=false_base)

    def test_canonical_base_blocks_direct_main_even_when_plan_declares_trunk(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import (
            build_remote_write_effect_plan,
            validate_local_git_observation,
        )
        from control_plane.lifecycle import TaskStore
        from control_plane.run_workflow import (
            RunStore,
            advance_outcome_binding,
            build_outcome_binding,
            build_run_plan,
        )

        git(self.scenario.repo, "switch", "main")
        git(self.scenario.repo, "push", "origin", "main:refs/heads/trunk")
        git(self.scenario.repo, "fetch", "origin", "trunk")
        remote_main_before = git(
            self.scenario.repo,
            "ls-remote",
            "--heads",
            "origin",
            "refs/heads/main",
        )
        (self.scenario.repo / "direct-main.txt").write_text(
            "must not push\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "direct-main.txt")
        git(self.scenario.repo, "commit", "-m", "test: local direct main")
        direct_head = git(self.scenario.repo, "rev-parse", "HEAD")
        task = task_envelope(
            task_id="TASK-DIRECT-MAIN",
            requested_outcome="pull_request",
            scope_paths=["direct-main.txt"],
            effects=[
                {"name": "local_write", "source": "user_explicit"},
                {"name": "commit", "source": "user_explicit"},
                {"name": "remote_write", "source": "user_explicit"},
                {"name": "pull_request", "source": "user_explicit"},
            ],
        )
        run_plan = build_run_plan(
            task=task,
            decision=_decision(task),
            repository=self.scenario.repo,
            branch="main",
            head="a" * 40,
            session_id="local-correlator-direct-main",
            prepared_at="2026-08-09T09:00:00Z",
        )
        RunStore(self.state_dir).write_plan(run_plan)
        binding = build_outcome_binding(
            run_plan=run_plan,
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
                "committed_head": direct_head,
            },
        )
        spoofed_policy = copy.deepcopy(self.policy)
        spoofed_policy["git"]["base_branch"] = "trunk"
        effect_plan = build_remote_write_effect_plan(
            outcome_binding=binding,
            task_digest=run_plan["task_digest"],
            remote="origin",
            base="trunk",
            scope_paths=("direct-main.txt",),
            policy_digest=contract_digest(spoofed_policy),
        )
        store = TaskStore(self.state_dir)
        store.start(
            task["task_id"],
            outcome="pull_request",
            branch="main",
            task_digest=run_plan["task_digest"],
            decision_digest=run_plan["decision_digest"],
        )
        for target, evidence in (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
            (
                "review_ready",
                {
                    "gates_ok": True,
                    "documentation_decision": contract_digest({"docs": "direct"}),
                },
            ),
        ):
            store.transition(
                task["task_id"], target, evidence=evidence, current_branch="main"
            )
        local = lifecycle_observation(
            __import__("control_plane.host_bridge", fromlist=["LocalGitObservation"]).LocalGitObservation,
            observation_id="direct-main-commit",
            invocation_id="direct-main-commit",
            task_digest=run_plan["task_digest"],
            repository_identity=str(self.scenario.repo.resolve()),
            worktree_identity=str(self.scenario.repo.resolve()),
            branch="main",
            prior_head="a" * 40,
            target_state="committed",
            session_id="direct-main-session",
            provider="git",
            subject_digest=run_plan["task_digest"],
            evidence={"commit": direct_head},
            observed_at_monotonic=100.0,
            freshness_deadline=130.0,
        )
        validated = validate_local_git_observation(
            local,
            expected_task_digest=run_plan["task_digest"],
            expected_repo=self.scenario.repo,
            expected_worktree=self.scenario.repo,
            expected_branch="main",
            expected_prior_head="a" * 40,
            expected_target_state="committed",
            expected_session_id="direct-main-session",
            expected_invocation_id="direct-main-commit",
            clock=lambda: 100.0,
        )
        store.transition(
            task["task_id"], "committed", evidence=validated, current_branch="main"
        )
        fake = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_PREPARE"):
            store.prepare_remote_write(
                task["task_id"],
                effect_plan=effect_plan,
                current_branch="main",
            )
        self.assertEqual(fake.write_calls, 0)
        self.assertEqual(
            git(
                self.scenario.repo,
                "ls-remote",
                "--heads",
                "origin",
                "refs/heads/main",
            ),
            remote_main_before,
        )

    def test_live_head_drift_is_rejected_before_fake_write(self) -> None:
        self._prepare()
        (self.scenario.repo / "drift.txt").write_text("drift\n", encoding="utf-8")
        git(self.scenario.repo, "add", "drift.txt")
        git(self.scenario.repo, "commit", "-m", "test: drift after plan")
        fake = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_PREPARE"):
            fake.execute_prepared(
                store=self.store,
                task_id=self.task["task_id"],
                plan=self.plan,
                current_branch=self.branch,
            )
        self.assertEqual(fake.write_calls, 0)

    def test_remote_identity_drift_is_rejected_before_fake_write(self) -> None:
        self._prepare()
        replacement = self.scenario.root / "replacement.git"
        subprocess.run(
            ["git", "init", "--bare", str(replacement)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        git(self.scenario.repo, "remote", "set-url", "origin", str(replacement))
        fake = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_PREPARE"):
            fake.execute_prepared(
                store=self.store,
                task_id=self.task["task_id"],
                plan=self.plan,
                current_branch=self.branch,
            )
        self.assertEqual(fake.write_calls, 0)

    def test_policy_change_after_arm_is_rejected_at_executor_edge(self) -> None:
        self._prepare()
        remote_before = git(
            self.scenario.repo,
            "ls-remote",
            "--heads",
            "origin",
            f"refs/heads/{self.branch}",
        )

        def change_policy_base() -> None:
            policy_path = self.scenario.repo / ".codex" / "project-policy.toml"
            policy_text = policy_path.read_text(encoding="utf-8")
            self.assertIn('base_branch = "main"', policy_text)
            policy_path.write_text(
                policy_text.replace(
                    'base_branch = "main"', 'base_branch = "trunk"', 1
                ),
                encoding="utf-8",
            )

        fake = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_EXECUTION"):
            fake.execute_prepared(
                store=self.store,
                task_id=self.task["task_id"],
                plan=self.plan,
                current_branch=self.branch,
                before_execution=change_policy_base,
            )
        self.assertEqual(fake.write_calls, 0)
        self.assertEqual(
            git(
                self.scenario.repo,
                "ls-remote",
                "--heads",
                "origin",
                f"refs/heads/{self.branch}",
            ),
            remote_before,
        )
        self.assertEqual(
            self.store.status(self.task["task_id"])["pending_remote_effect"][
                "phase"
            ],
            "observe_only",
        )

    def test_pass_receipt_reobserves_local_head_before_promotion(self) -> None:
        self._prepare()
        fake = _FakeLocalPushHost(self.scenario.repo)
        fake.execute_prepared(
            store=self.store,
            task_id=self.task["task_id"],
            plan=self.plan,
            current_branch=self.branch,
        )
        receipt = fake.observe(self.plan)
        (self.scenario.repo / "after-pass.txt").write_text(
            "local drift\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "after-pass.txt")
        git(self.scenario.repo, "commit", "-m", "test: drift after remote pass")

        blocked = self.store.publish_remote_write(
            self.task["task_id"],
            effect_plan=self.plan,
            receipt=receipt,
            current_branch=self.branch,
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["block_reason"], "E_REMOTE_WRITE_PUBLISH_DRIFT")
        self.assertNotIn("pushed", blocked["evidence"])
        self.assertEqual(blocked["pending_remote_effect"]["phase"], "observe_only")
        self.assertEqual(fake.write_calls, 1)

    def test_crash_after_send_restarts_in_observe_only_without_second_push(self) -> None:
        from control_plane.lifecycle import TaskStore

        prepared = self._prepare()
        self.assertEqual(prepared["pending_remote_effect"]["phase"], "prepared")
        self.assertEqual(
            prepared["pending_remote_effect"]["outcome_binding"], self.binding
        )
        self.assertEqual(prepared["pending_remote_effect"]["run_plan"], self.run_plan)
        self.assertEqual(prepared["pending_remote_effect"]["policy"], self.policy)
        fake = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaises(TimeoutError):
            fake.execute_prepared(
                store=self.store,
                task_id=self.task["task_id"],
                plan=self.plan,
                current_branch=self.branch,
                timeout="after_send",
            )
        restarted = TaskStore(self.state_dir)
        self.assertEqual(
            restarted.status(self.task["task_id"])["pending_remote_effect"]["phase"],
            "observe_only",
        )
        with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_OBSERVE_ONLY"):
            fake.execute_prepared(
                store=restarted,
                task_id=self.task["task_id"],
                plan=self.plan,
                current_branch=self.branch,
            )
        self.assertEqual(fake.write_calls, 1)
        receipt = fake.observe(self.plan)
        pushed = restarted.publish_remote_write(
            self.task["task_id"],
            effect_plan=self.plan,
            receipt=receipt,
            current_branch=self.branch,
        )
        self.assertEqual(pushed["state"], "pushed")
        self.assertEqual(fake.write_calls, 1)

    def test_publish_revalidates_mutated_receipt_and_keeps_observe_only(self) -> None:
        from control_plane.host_bridge import build_remote_outcome_receipt

        self._prepare()
        self.store.arm_remote_write_observation(
            self.task["task_id"],
            effect_plan=self.plan,
            current_branch=self.branch,
        )
        receipt = build_remote_outcome_receipt(
            effect_plan=self.plan,
            status="FAIL",
            observed_at="2026-08-09T10:03:00Z",
        )
        object.__setattr__(receipt, "status", "PASS")
        object.__setattr__(receipt, "observed_repository", self.plan.repository)
        object.__setattr__(receipt, "observed_remote", self.plan.remote)
        object.__setattr__(receipt, "observed_base", self.plan.base)
        object.__setattr__(receipt, "observed_branch", self.plan.branch)
        object.__setattr__(receipt, "observed_head_sha", self.plan.head_sha)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
            self.store.publish_remote_write(
                self.task["task_id"],
                effect_plan=self.plan,
                receipt=receipt,
                current_branch=self.branch,
            )
        state = self.store.status(self.task["task_id"])
        self.assertEqual(state["state"], "committed")
        self.assertEqual(state["pending_remote_effect"]["phase"], "observe_only")

    def test_unknown_keeps_observe_only_marker_across_restart(self) -> None:
        from control_plane.host_bridge import build_remote_outcome_receipt
        from control_plane.lifecycle import TaskStore

        self._prepare()
        self.store.arm_remote_write_observation(
            self.task["task_id"],
            effect_plan=self.plan,
            current_branch=self.branch,
        )
        unknown = build_remote_outcome_receipt(
            effect_plan=self.plan,
            status="UNKNOWN",
            observed_at="2026-08-09T10:02:00Z",
        )
        blocked = self.store.publish_remote_write(
            self.task["task_id"],
            effect_plan=self.plan,
            receipt=unknown,
            current_branch=self.branch,
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["pending_remote_effect"]["phase"], "observe_only")
        restarted = TaskStore(self.state_dir)
        fake = _FakeLocalPushHost(self.scenario.repo)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_WRITE_OBSERVE_ONLY"):
            fake.execute_prepared(
                store=restarted,
                task_id=self.task["task_id"],
                plan=self.plan,
                current_branch=self.branch,
            )
        self.assertEqual(fake.write_calls, 0)

    def test_matching_existing_draft_is_reused_without_write(self) -> None:
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost(existing=_FakePullRequestHost.matching(plan))

        receipt = fake.observe(plan)
        drafted = self._publish_with_exact_pr_remote(plan, receipt)

        self.assertEqual(drafted["state"], "pr_draft")
        self.assertEqual(fake.write_calls, 0)
        self.assertEqual(
            drafted["outcome_binding"]["consumed_effect_ids"],
            ["local_write", "commit", "remote_write"],
        )
        evidence = drafted["evidence"]["pr_draft"]
        self.assertTrue(evidence["draft"])
        self.assertEqual(evidence["disposition"], "observed_existing")
        self.assertEqual(evidence["receipt_digest"], receipt.receipt_digest)

    def test_split_fetch_and_push_url_cannot_promote_from_fetch_refs(self) -> None:
        from unittest.mock import patch
        from control_plane import lifecycle

        self._push()
        plan = self._pull_request_plan()
        receipt = _FakePullRequestHost(
            existing=_FakePullRequestHost.matching(plan)
        ).observe(plan)
        original = lifecycle._delivery_git_text

        def reject_exact_remote(worktree, arguments):
            if arguments[:3] == ("ls-remote", "--heads", plan.remote_url):
                raise ValueError("exact push target observation unavailable")
            return original(worktree, arguments)

        with patch(
            "control_plane.lifecycle._delivery_git_text",
            side_effect=reject_exact_remote,
        ):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_PREPARE"):
                self.store.publish_pull_request_draft(
                    self.task["task_id"],
                    effect_plan=plan,
                    receipt=receipt,
                    current_branch=self.branch,
                )
        state = self.store.status(self.task["task_id"])
        self.assertEqual(state["state"], "pushed")
        self.assertNotIn("pr_draft", state.get("evidence", {}))
        self.assertNotIn("pull_request_outcome_receipt_digests", state)

    def test_foreign_or_mismatched_pull_request_url_never_promotes(self) -> None:
        from control_plane.host_bridge import build_pull_request_outcome_receipt

        self._push()
        plan = self._pull_request_plan()
        invalid = (
            (7, "https://github.com/other/control-plane/pull/7"),
            (7, "https://github.com/example/control-plane/pull/999"),
            (7, "https://user@github.com/example/control-plane/pull/7"),
            (7, "https://github.com/example/control-plane/pull/7?view=1"),
            (7, "https://github.com/example/control-plane/pull/7#discussion"),
            (7, "https://github.com/example/control-plane/pull/7/files"),
            (8, "https://github.com/example/control-plane/pull/7"),
        )
        for number, url in invalid:
            with self.subTest(number=number, url=url):
                with self.assertRaisesRegex(
                    ValueError, "E_REMOTE_OUTCOME_RECEIPT|E_PULL_REQUEST_OUTCOME"
                ):
                    receipt = build_pull_request_outcome_receipt(
                        effect_plan=plan,
                        status="PASS",
                        observed_at="2026-08-09T11:00:00Z",
                        observed_repository=plan.repository,
                        observed_remote=plan.remote,
                        observed_base=plan.base,
                        observed_branch=plan.branch,
                        observed_head_sha=plan.head_sha,
                        observed_pr_number=number,
                        observed_pr_url=url,
                        observed_pr_draft=True,
                        disposition="observed_existing",
                    )
                    self.store.publish_pull_request_draft(
                        self.task["task_id"],
                        effect_plan=plan,
                        receipt=receipt,
                        current_branch=self.branch,
                    )
                state = self.store.status(self.task["task_id"])
                self.assertEqual(state["state"], "pushed")
                self.assertNotIn("pr_draft", state.get("evidence", {}))
                self.assertNotIn("pull_request_outcome_receipt_digests", state)

    def _assert_pull_request_mismatch_blocks(self, **changes) -> None:
        self._push()
        plan = self._pull_request_plan()
        exact = _FakePullRequestHost.matching(plan)
        fake = _FakePullRequestHost(existing={**exact, **changes})
        receipt = fake.observe(plan)
        self.assertEqual(receipt.status, "FAIL")
        blocked = self._publish_with_exact_pr_remote(plan, receipt)
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["block_reason"], "E_PULL_REQUEST_OUTCOME_FAIL")
        self.assertEqual(fake.write_calls, 0)

    def test_pull_request_repository_mismatch_blocks_without_write(self) -> None:
        self._assert_pull_request_mismatch_blocks(
            repository=str(self.scenario.root / "other")
        )

    def test_pull_request_base_mismatch_blocks_without_write(self) -> None:
        self._assert_pull_request_mismatch_blocks(base="trunk")

    def test_pull_request_branch_mismatch_blocks_without_write(self) -> None:
        self._assert_pull_request_mismatch_blocks(branch="codex/other")

    def test_pull_request_sha_mismatch_blocks_without_write(self) -> None:
        self._assert_pull_request_mismatch_blocks(head_sha="b" * 40)

    def test_pull_request_ready_mismatch_blocks_without_write(self) -> None:
        self._assert_pull_request_mismatch_blocks(draft=False)

    def test_unavailable_pull_request_observation_blocks_without_write(self) -> None:
        self._push()
        plan = self._pull_request_plan()

        unavailable = _FakePullRequestHost(observation_available=False)
        blocked = self._publish_with_exact_pr_remote(
            plan, unavailable.observe(plan)
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["block_reason"], "E_PULL_REQUEST_OUTCOME_UNKNOWN")
        self.assertEqual(unavailable.write_calls, 0)

    def test_absent_pr_is_created_as_draft_after_durable_revalidation(self) -> None:
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        prepared = self._publish_with_exact_pr_remote(
            plan, fake.observe(plan)
        )
        self.assertEqual(prepared["pending_pull_request_effect"]["phase"], "prepared")

        with self._exact_pr_remote(plan):
            fake.execute_prepared(
                store=self.store,
                task_id=self.task["task_id"],
                plan=plan,
                current_branch=self.branch,
            )
        drafted = self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        self.assertEqual(drafted["state"], "pr_draft")
        self.assertEqual(fake.write_calls, 1)
        self.assertTrue(drafted["evidence"]["pr_draft"]["draft"])
        self.assertEqual(drafted["evidence"]["pr_draft"]["disposition"], "created")

    def test_timeout_after_pr_send_observes_and_never_writes_twice(self) -> None:
        from control_plane.lifecycle import TaskStore

        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(
            plan, fake.observe(plan)
        )
        with self._exact_pr_remote(plan):
            with self.assertRaises(TimeoutError):
                fake.execute_prepared(
                    store=self.store,
                    task_id=self.task["task_id"],
                    plan=plan,
                    current_branch=self.branch,
                    timeout="after_send",
                )
        restarted = TaskStore(self.state_dir)
        with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_OBSERVE_ONLY"):
            with self._exact_pr_remote(plan):
                fake.execute_prepared(
                    store=restarted,
                    task_id=self.task["task_id"],
                    plan=plan,
                    current_branch=self.branch,
                )
        drafted = self._publish_with_exact_pr_remote(
            plan, fake.observe(plan), store=restarted
        )
        self.assertEqual(drafted["state"], "pr_draft")
        self.assertEqual(fake.write_calls, 1)

    def test_pull_request_arm_is_one_shot_and_restart_observe_only(self) -> None:
        from control_plane.lifecycle import TaskStore

        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        with self._exact_pr_remote(plan):
            armed = self.store.arm_pull_request_draft_creation(
                self.task["task_id"],
                effect_plan=plan,
                current_branch=self.branch,
            )
        self.assertEqual(
            armed["pending_pull_request_effect"]["phase"], "observe_only"
        )
        restarted = TaskStore(self.state_dir)
        with self._exact_pr_remote(plan):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_OBSERVE_ONLY"):
                restarted.arm_pull_request_draft_creation(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        self.assertEqual(fake.write_calls, 0)

    def test_pull_request_executor_edge_rejects_policy_drift(self) -> None:
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        with self._exact_pr_remote(plan):
            self.store.arm_pull_request_draft_creation(
                self.task["task_id"], effect_plan=plan, current_branch=self.branch
            )
        policy_path = self.scenario.repo / ".codex" / "project-policy.toml"
        policy_path.write_text(
            policy_path.read_text(encoding="utf-8").replace(
                'base_branch = "main"', 'base_branch = "trunk"', 1
            ),
            encoding="utf-8",
        )
        with self._exact_pr_remote(plan):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_EXECUTION"):
                self.store.revalidate_pull_request_draft_before_execution(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        self.assertEqual(fake.write_calls, 0)

    def test_pull_request_executor_edge_rejects_run_plan_drift(self) -> None:
        import json
        from control_plane.run_workflow import RunStore

        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        with self._exact_pr_remote(plan):
            self.store.arm_pull_request_draft_creation(
                self.task["task_id"], effect_plan=plan, current_branch=self.branch
            )
        run_store = RunStore(self.state_dir)
        plan_path = run_store._plan_path(self.task["task_id"])
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        payload["branch"] = "codex/drifted"
        plan_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        with self._exact_pr_remote(plan):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_EXECUTION"):
                self.store.revalidate_pull_request_draft_before_execution(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        self.assertEqual(fake.write_calls, 0)

    def test_pull_request_executor_edge_rejects_head_drift(self) -> None:
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        with self._exact_pr_remote(plan):
            self.store.arm_pull_request_draft_creation(
                self.task["task_id"], effect_plan=plan, current_branch=self.branch
            )
        (self.scenario.repo / "pr-drift.txt").write_text("drift\n", encoding="utf-8")
        git(self.scenario.repo, "add", "pr-drift.txt")
        git(self.scenario.repo, "commit", "-m", "test: pr executor drift")
        with self._exact_pr_remote(plan):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_EXECUTION"):
                self.store.revalidate_pull_request_draft_before_execution(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        self.assertEqual(fake.write_calls, 0)

    def test_pull_request_executor_edge_rejects_target_ref_drift(self) -> None:
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        with self._exact_pr_remote(plan):
            self.store.arm_pull_request_draft_creation(
                self.task["task_id"], effect_plan=plan, current_branch=self.branch
            )
        with self._exact_pr_remote(plan, observed_feature_head="b" * 40):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_EXECUTION"):
                self.store.revalidate_pull_request_draft_before_execution(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        self.assertEqual(fake.write_calls, 0)

    def test_absent_after_arm_blocks_and_never_rearms(self) -> None:
        from control_plane.host_bridge import build_pull_request_outcome_receipt

        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        with self._exact_pr_remote(plan):
            self.store.arm_pull_request_draft_creation(
                self.task["task_id"], effect_plan=plan, current_branch=self.branch
            )
        absent = build_pull_request_outcome_receipt(
            effect_plan=plan,
            status="ABSENT",
            observed_at="2026-08-09T11:01:00Z",
        )
        blocked = self._publish_with_exact_pr_remote(plan, absent)
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(
            blocked["pending_pull_request_effect"]["phase"], "observe_only"
        )
        with self._exact_pr_remote(plan):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_OBSERVE_ONLY"):
                self.store.arm_pull_request_draft_creation(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        self.assertEqual(fake.write_calls, 0)

    def test_pull_request_marker_mutation_fails_closed(self) -> None:
        from control_plane import lifecycle
        from control_plane.contracts import contract_digest

        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        state = self.store.status(self.task["task_id"])
        marker = dict(state["pending_pull_request_effect"])
        marker["authorizes"] = True
        marker["marker_digest"] = contract_digest(
            {key: value for key, value in marker.items() if key != "marker_digest"}
        )
        state["pending_pull_request_effect"] = marker
        lifecycle._atomic_json(self.store._path(self.task["task_id"]), state)
        with self._exact_pr_remote(plan):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_PREPARE"):
                self.store.arm_pull_request_draft_creation(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        self.assertEqual(fake.write_calls, 0)

    def _prepared_absence_marker(self):
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        return plan, fake, self.store.status(self.task["task_id"])

    def _persist_marker_and_reject_arm(self, plan, fake, state, marker) -> None:
        from control_plane import lifecycle
        from control_plane.contracts import contract_digest

        marker["marker_digest"] = contract_digest(
            {key: value for key, value in marker.items() if key != "marker_digest"}
        )
        state["pending_pull_request_effect"] = marker
        lifecycle._atomic_json(self.store._path(self.task["task_id"]), state)
        with self._exact_pr_remote(plan):
            with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_PREPARE"):
                self.store.arm_pull_request_draft_creation(
                    self.task["task_id"],
                    effect_plan=plan,
                    current_branch=self.branch,
                )
        persisted = self.store.status(self.task["task_id"])
        self.assertEqual(persisted["pending_pull_request_effect"]["phase"], "prepared")
        self.assertNotIn(self.task["task_id"], self.store._armed_pull_request_plans)
        self.assertEqual(fake.write_calls, 0)

    def test_absence_marker_digest_mutation_rejects_arm(self) -> None:
        plan, fake, state = self._prepared_absence_marker()
        marker = copy.deepcopy(state["pending_pull_request_effect"])
        marker["absence_receipt_digest"] = "sha256:" + "9" * 64
        self._persist_marker_and_reject_arm(plan, fake, state, marker)

    def test_absence_marker_timestamp_mutation_rejects_arm(self) -> None:
        plan, fake, state = self._prepared_absence_marker()
        marker = copy.deepcopy(state["pending_pull_request_effect"])
        marker["observed_at"] = "2026-08-09T11:59:00Z"
        self._persist_marker_and_reject_arm(plan, fake, state, marker)

    def test_absence_marker_receipt_payload_mutation_rejects_arm(self) -> None:
        from control_plane.contracts import contract_digest

        plan, fake, state = self._prepared_absence_marker()
        marker = copy.deepcopy(state["pending_pull_request_effect"])
        receipt = marker["absence_receipt"]
        receipt["authorizes"] = True
        receipt["receipt_digest"] = contract_digest(
            {key: value for key, value in receipt.items() if key != "receipt_digest"}
        )
        self._persist_marker_and_reject_arm(plan, fake, state, marker)

    def test_absence_marker_missing_registered_digest_rejects_arm(self) -> None:
        plan, fake, state = self._prepared_absence_marker()
        state["pull_request_outcome_receipt_digests"] = []
        marker = copy.deepcopy(state["pending_pull_request_effect"])
        self._persist_marker_and_reject_arm(plan, fake, state, marker)

    def test_absence_marker_receipt_plan_mismatch_rejects_arm(self) -> None:
        from control_plane.contracts import contract_digest

        plan, fake, state = self._prepared_absence_marker()
        marker = copy.deepcopy(state["pending_pull_request_effect"])
        receipt = marker["absence_receipt"]
        receipt["effect_plan_digest"] = "sha256:" + "8" * 64
        receipt["receipt_digest"] = contract_digest(
            {key: value for key, value in receipt.items() if key != "receipt_digest"}
        )
        marker["absence_receipt_digest"] = receipt["receipt_digest"]
        self._persist_marker_and_reject_arm(plan, fake, state, marker)

    def test_pull_request_mutated_receipt_fails_before_persistence(self) -> None:
        from control_plane.host_bridge import build_pull_request_outcome_receipt

        self._push()
        plan = self._pull_request_plan()
        receipt = build_pull_request_outcome_receipt(
            effect_plan=plan,
            status="UNKNOWN",
            observed_at="2026-08-09T11:02:00Z",
        )
        for field, value in (
            ("status", "PASS"),
            ("observed_repository", plan.repository),
            ("observed_remote", plan.remote),
            ("observed_base", plan.base),
            ("observed_branch", plan.branch),
            ("observed_head_sha", plan.head_sha),
            ("observed_pr_number", 7),
            ("observed_pr_url", "https://github.com/example/control-plane/pull/7"),
            ("observed_pr_draft", True),
            ("disposition", "observed_existing"),
        ):
            object.__setattr__(receipt, field, value)
        with self.assertRaisesRegex(ValueError, "E_REMOTE_OUTCOME_RECEIPT"):
            self._publish_with_exact_pr_remote(plan, receipt)
        state = self.store.status(self.task["task_id"])
        self.assertEqual(state["state"], "pushed")
        self.assertNotIn("pending_pull_request_effect", state)

    def test_pull_request_absence_receipt_replay_fails_closed(self) -> None:
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost()
        receipt = fake.observe(plan)
        self._publish_with_exact_pr_remote(plan, receipt)
        with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_OUTCOME_REPLAY"):
            self._publish_with_exact_pr_remote(plan, receipt)
        self.assertEqual(
            self.store.status(self.task["task_id"])["pending_pull_request_effect"][
                "phase"
            ],
            "prepared",
        )
        self.assertEqual(fake.write_calls, 0)

    def test_created_status_without_observe_only_marker_is_rejected(self) -> None:
        self._push()
        plan = self._pull_request_plan()
        fake = _FakePullRequestHost(
            existing=_FakePullRequestHost.matching(plan, disposition="created")
        )
        with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_OUTCOME_BINDING"):
            self._publish_with_exact_pr_remote(plan, fake.observe(plan))
        state = self.store.status(self.task["task_id"])
        self.assertEqual(state["state"], "pushed")
        self.assertNotIn("pending_pull_request_effect", state)
        self.assertEqual(fake.write_calls, 0)

    def test_pr_plan_base_drift_is_rejected_before_provider(self) -> None:
        self._push()
        plan = self._pull_request_plan(base="trunk")
        fake = _FakePullRequestHost()
        with self.assertRaisesRegex(ValueError, "E_PULL_REQUEST_PREPARE"):
            self.store.publish_pull_request_draft(
                self.task["task_id"],
                effect_plan=plan,
                receipt=fake.observe(plan),
                current_branch=self.branch,
            )
        self.assertEqual(fake.write_calls, 0)


if __name__ == "__main__":
    unittest.main()
