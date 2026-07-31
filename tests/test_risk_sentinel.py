from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.git_test_support import GitScenario, git


ROOT = Path(__file__).parents[1]


def risk_check(dimension: object, code: str):
    return next(check for check in dimension.checks if check.code == code)


def register_native_task_event(
    bridge: object,
    *,
    task_state: dict[str, object],
    lease_digest: str | None,
    repository: Path,
    branch: str,
    head: str,
    session_id: str,
    invocation_id: str,
):
    from control_plane.contracts import contract_digest
    from tests.host_adapter_test_support import _register_native_object

    event = object.__new__(bridge.NativeTaskEvent)
    event._consumed = False
    event.event_id = "native-task-risk"
    event.task_id = task_state["task_id"]
    event.task_digest = task_state["task_digest"]
    event.task_state_digest = contract_digest(task_state)
    event.lease_digest = lease_digest
    event.repository_identity = str(repository.resolve())
    event.worktree_identity = str(repository.resolve())
    event.branch = branch
    event.head = head
    event.session_id = session_id
    event.invocation_id = invocation_id
    event.observed_at_monotonic = 100.0
    _register_native_object(event, "task")
    return event


def registered_route_context(
    bridge: object,
    *,
    task_digest: str,
    decision_digest: str,
    repository: Path,
    branch: str,
    head: str,
    session_id: str,
    invocation_id: str,
    clarification_status: str = "resolved",
    authorized_effects: tuple[str, ...] = (),
    blocked_effects: tuple[str, ...] = (),
):
    from control_plane.contracts import contract_digest

    context = object.__new__(bridge.TrustedRouteContext)
    context._consumed = False
    context._clock = lambda: 100.0
    context.task_digest = task_digest
    context.route_digest = decision_digest
    context.route_material_digest = contract_digest(
        {"task": task_digest, "decision": decision_digest}
    )
    context.inventory_digest = contract_digest({"inventory": "risk"})
    context.inventory_observation_id = "inventory-risk"
    context.registry_digest = contract_digest({"registry": "risk"})
    context.repository_identity = str(repository.resolve())
    context.worktree_identity = str(repository.resolve())
    context.branch = branch
    context.head = head
    context.session_id = session_id
    context.invocation_id = invocation_id
    context.required_resources = ()
    context.recommended_resources = ()
    context.forbidden_resources = ()
    context.resource_bindings = ()
    context.authorized_effects = authorized_effects
    context.blocked_effects = blocked_effects
    context.clarification_status = clarification_status
    context.context_nonce = "route-context-risk"
    context.issued_at_monotonic = 100.0
    context.freshness_deadline = 130.0
    bridge._register_runtime_host_object(context, "trusted_route_context")
    return context


def validated_host_context(
    bridge: object,
    *,
    task_state: dict[str, object],
    lease_digest: str | None,
    repository: Path,
    branch: str,
    head: str,
    session_id: str,
    invocation_id: str,
    decision_digest: str,
):
    from tests.host_adapter_test_support import native_session_event

    route_context = registered_route_context(
        bridge,
        task_digest=str(task_state["task_digest"]),
        decision_digest=decision_digest,
        repository=repository,
        branch=branch,
        head=head,
        session_id=session_id,
        invocation_id=invocation_id,
    )
    capability = bridge.attest_host_adapter_capability(
        native_session_event(
            event_id=f"session-context-{invocation_id}",
            session_id=session_id,
            invocation_id=invocation_id,
            observed_at_monotonic=100.0,
        ),
        expected_session_id=session_id,
        expected_invocation_id=invocation_id,
        clock=lambda: 100.0,
        ttl_seconds=30,
    )
    observation = bridge.observe_host_risk_context(
        native_task_event=register_native_task_event(
            bridge,
            task_state=task_state,
            lease_digest=lease_digest,
            repository=repository,
            branch=branch,
            head=head,
            session_id=session_id,
            invocation_id=invocation_id,
        ),
        trusted_route_context=route_context,
        clarification_resolution=None,
        authorization=None,
        repository_identity=repository,
        worktree_identity=repository,
        branch=branch,
        head=head,
        session_id=session_id,
        invocation_id=invocation_id,
        host_capability=capability,
        clock=lambda: 100.0,
        ttl_seconds=30,
    )
    return bridge.validate_host_risk_context(
        observation,
        expected_task_digest=str(task_state["task_digest"]),
        expected_decision_digest=decision_digest,
        expected_repository_identity=repository,
        expected_worktree_identity=repository,
        expected_branch=branch,
        expected_head=head,
        expected_session_id=session_id,
        expected_invocation_id=invocation_id,
        expected_effect=None,
        expected_subject_digest=None,
        clock=lambda: 100.0,
    )


class RiskSentinelContractTests(unittest.TestCase):
    def test_status_precedence_is_fail_unknown_pass(self) -> None:
        from control_plane.risk_sentinel import aggregate_status

        self.assertEqual(aggregate_status(["PASS"]), "PASS")
        self.assertEqual(aggregate_status(["PASS", "UNKNOWN"]), "UNKNOWN")
        self.assertEqual(
            aggregate_status(["PASS", "UNKNOWN", "FAIL"]), "FAIL"
        )
        self.assertEqual(aggregate_status([]), "PASS")

    def test_local_safe_remote_unobserved_is_unknown_not_pass(self) -> None:
        from control_plane.risk_sentinel import (
            RiskCheck,
            RiskDimension,
            RiskStatus,
        )

        local = RiskDimension(
            status="PASS",
            checks=(
                RiskCheck(
                    code="RS_LOCAL_POLICY",
                    status="PASS",
                    message="Governing policy is valid.",
                    facts={},
                ),
            ),
            errors=(),
        )
        remote = RiskDimension(
            status="UNKNOWN",
            checks=(),
            errors=(
                {
                    "code": "RS_REMOTE_NOT_OBSERVED",
                    "message": (
                        "Remote protection or provenance has not been observed."
                    ),
                },
            ),
        )
        result = RiskStatus(
            command="risk-status",
            dimensions={"local": local, "remote": remote},
            facts={},
            errors=(),
        )

        self.assertEqual(result.status, "UNKNOWN")
        self.assertFalse(result.to_dict()["ok"])
        self.assertEqual(
            result.dimensions["remote"].errors[0]["code"],
            "RS_REMOTE_NOT_OBSERVED",
        )
        self.assertEqual(
            set(result.to_dict()),
            {
                "schema_version",
                "command",
                "ok",
                "status",
                "dimensions",
                "facts",
                "errors",
            },
        )

    def test_invalid_status_is_rejected(self) -> None:
        from control_plane.risk_sentinel import aggregate_status

        with self.assertRaisesRegex(ValueError, "RS_STATUS"):
            aggregate_status(["GREEN"])

    def test_candidate_policy_cannot_make_local_risk_pass(self) -> None:
        from control_plane.risk_sentinel import evaluate_local_risk
        from control_plane.policy import load_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        candidate = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )

        result = evaluate_local_risk(scenario.repo, candidate)

        self.assertEqual(
            risk_check(result, "RS_LOCAL_POLICY").status, "FAIL"
        )
        self.assertNotEqual(result.status, "PASS")

    def test_installed_guard_snapshot_binds_hooks_base_and_remote(self) -> None:
        from types import SimpleNamespace

        from control_plane.risk_sentinel import (
            FAIL,
            PASS,
            _installed_guard_snapshot,
        )
        from tests.test_git_guards import InstalledGuardScenario

        scenario = InstalledGuardScenario()
        self.addCleanup(scenario.close)
        policy = scenario.load()
        hooks_path = str(
            scenario.common_dir
            / "codex-control-plane"
            / "installs"
            / scenario.manifest_digest
            / "git-hooks"
        )

        self.assertEqual(
            _installed_guard_snapshot(
                scenario.repo, (hooks_path,), policy
            ),
            (hooks_path, PASS),
        )
        bindings = {
            "policy_digest": policy.policy_digest,
            "lock_digest": policy.lock_digest,
            "runtime_digest": policy.runtime_digest,
            "governing_base_commit": policy.governing_base_commit,
            "remote_repository": policy.remote_repository,
        }
        for field, value in (
            ("governing_base_commit", "0" * 40),
            ("remote_repository", "other/repository"),
        ):
            with self.subTest(field=field):
                mismatched = SimpleNamespace(
                    **{**bindings, field: value}
                )
                self.assertEqual(
                    _installed_guard_snapshot(
                        scenario.repo, (hooks_path,), mismatched
                    ),
                    (None, FAIL),
                )

        (Path(hooks_path) / "pre-push").write_text(
            "#!/bin/sh\nexit 1\n", encoding="utf-8"
        )
        self.assertEqual(
            _installed_guard_snapshot(
                scenario.repo, (hooks_path,), policy
            ),
            (None, FAIL),
        )

    def test_serialized_decision_cannot_make_authority_or_clarification_pass(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "1" * 64,
            lock_digest="sha256:" + "2" * 64,
            governing_base_commit="a" * 40,
            session_id="session-risk-tests",
            invocation_id="invocation-risk-tests",
            freshness_deadline=130.0,
        )
        task = {
            "task_id": "task-risk-tests",
            "task_digest": "sha256:" + "3" * 64,
            "state": "framed",
            "generation": 0,
        }
        hint = {
            "authorization": {"local_write": True},
            "interaction": {
                "recommended_mode": "plan",
                "reason_codes": ["MODE_COMPLEX_OR_UNCERTAIN"],
                "clarification_gate": {"status": "resolved"},
            },
        }

        result = evaluate_local_risk(
            scenario.repo,
            policy,
            task_state=task,
            route_decision_hint=hint,
        )

        self.assertEqual(
            risk_check(result, "RS_CLARIFICATION_REQUIRED").status,
            "UNKNOWN",
        )
        self.assertEqual(
            risk_check(result, "RS_AUTHORITY_REQUIRED").status, "UNKNOWN"
        )

    def test_authority_is_not_applicable_only_without_task(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "4" * 64,
            lock_digest="sha256:" + "5" * 64,
            governing_base_commit="b" * 40,
            session_id="session-risk-tests",
            invocation_id="invocation-risk-tests",
            freshness_deadline=130.0,
        )

        result = evaluate_local_risk(scenario.repo, policy)
        authority = risk_check(result, "RS_AUTHORITY_REQUIRED")

        self.assertEqual(authority.status, "PASS")
        self.assertEqual(authority.facts["reason"], "NOT_APPLICABLE")

    def test_local_dimension_uses_exact_normative_check_vocabulary(
        self,
    ) -> None:
        from control_plane.risk_sentinel import evaluate_local_risk

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()

        result = evaluate_local_risk(scenario.repo, None)

        self.assertEqual(
            {check.code for check in result.checks},
            {
                "RS_LOCAL_POLICY",
                "RS_LOCAL_LOCK",
                "RS_LOCAL_REPOSITORY",
                "RS_LOCAL_BASE_BRANCH",
                "RS_LOCAL_DETACHED",
                "RS_LOCAL_BASE_DIVERGENCE",
                "RS_LOCAL_DIRTY",
                "RS_LOCAL_HOOK_PATH",
                "RS_LOCAL_HOOK_DIGEST",
                "RS_HOOK_TRUST",
                "RS_HOOK_MODE",
                "RS_CLARIFICATION_REQUIRED",
                "RS_AUTHORITY_REQUIRED",
                "RS_PROFILE",
                "RS_TASK_STATE",
            },
        )
        self.assertEqual(len(result.checks), 15)

    def test_git_observation_failure_becomes_unknown(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "6" * 64,
            lock_digest="sha256:" + "7" * 64,
            governing_base_commit="c" * 40,
            session_id="session-risk-tests",
            invocation_id="invocation-risk-tests",
            freshness_deadline=130.0,
        )

        with patch(
            "control_plane.risk_sentinel.evaluate_preflight",
            side_effect=OSError("git unavailable"),
        ):
            result = evaluate_local_risk(scenario.repo, policy)

        self.assertEqual(
            risk_check(result, "RS_LOCAL_REPOSITORY").status, "UNKNOWN"
        )
        self.assertTrue(
            any(check.status == "UNKNOWN" for check in result.checks)
        )

    def test_unanchored_cli_path_observes_only_anchor_independent_facts(
        self,
    ) -> None:
        from control_plane.risk_sentinel import evaluate_local_risk

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()

        result = evaluate_local_risk(scenario.repo, None)

        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(
            risk_check(result, "RS_LOCAL_POLICY").status, "UNKNOWN"
        )
        self.assertEqual(
            risk_check(result, "RS_LOCAL_REPOSITORY").status, "PASS"
        )
        self.assertEqual(
            risk_check(result, "RS_LOCAL_DETACHED").status, "PASS"
        )
        self.assertEqual(risk_check(result, "RS_LOCAL_DIRTY").status, "PASS")
        self.assertEqual(risk_check(result, "RS_PROFILE").status, "PASS")
        authority = risk_check(result, "RS_AUTHORITY_REQUIRED")
        self.assertEqual(authority.status, "PASS")
        self.assertEqual(authority.facts["reason"], "NOT_APPLICABLE")

    def test_host_risk_context_can_prove_current_authority(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import (
            governing_policy,
            native_session_event,
            native_user_interaction_event,
        )

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-host")
        branch = "codex/risk-host"
        head = git(scenario.repo, "rev-parse", "HEAD")
        session_id = "session-risk-host"
        invocation_id = "invocation-risk-host"
        task_digest = contract_digest({"task": "risk-host"})
        decision_digest = contract_digest({"decision": "risk-host"})
        subject_digest = contract_digest({"effect": "local_write"})
        from control_plane.lifecycle import TaskStore
        from control_plane.repository import worktree_git_dir

        task_state = TaskStore(worktree_git_dir(scenario.repo)).start(
            "task-risk-host",
            outcome="local_change",
            branch=branch,
            task_digest=task_digest,
            decision_digest=decision_digest,
        )
        route_context = registered_route_context(
            bridge,
            task_digest=task_digest,
            decision_digest=decision_digest,
            repository=scenario.repo,
            branch=branch,
            head=head,
            session_id=session_id,
            invocation_id=invocation_id,
            authorized_effects=("local_write",),
        )
        authorization_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-auth-risk",
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
                event_id="user-auth-risk",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=task_digest,
                subject_digest=subject_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=authorization_capability,
            task_digest=task_digest,
            session_id=session_id,
            repository_identity=scenario.repo,
            worktree_identity=scenario.repo,
            branch=branch,
            expected_head=head,
            subject_digest=subject_digest,
            scope_paths=("baseline.txt",),
            effect="local_write",
            operation_nonce="operation-risk-host",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        mismatched_subject_digest = contract_digest({"effect": "commit"})
        mismatched_authorization_capability = (
            bridge.attest_host_adapter_capability(
                native_session_event(
                    event_id="session-auth-risk-mismatch",
                    session_id=session_id,
                    invocation_id=invocation_id,
                    observed_at_monotonic=100.0,
                ),
                expected_session_id=session_id,
                expected_invocation_id=invocation_id,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        )
        mismatched_authorization = bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id="user-auth-risk-mismatch",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=task_digest,
                subject_digest=mismatched_subject_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=mismatched_authorization_capability,
            task_digest=task_digest,
            session_id=session_id,
            repository_identity=scenario.repo,
            worktree_identity=scenario.repo,
            branch=branch,
            expected_head=head,
            subject_digest=mismatched_subject_digest,
            scope_paths=("baseline.txt",),
            effect="commit",
            operation_nonce="operation-risk-host-mismatch",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        mismatched_risk_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-context-risk-mismatch",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        with self.assertRaisesRegex(ValueError, "RS_HOST_CONTEXT"):
            bridge.observe_host_risk_context(
                native_task_event=register_native_task_event(
                    bridge,
                    task_state=task_state,
                    lease_digest=None,
                    repository=scenario.repo,
                    branch=branch,
                    head=head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                ),
                trusted_route_context=route_context,
                clarification_resolution=None,
                authorization=mismatched_authorization,
                repository_identity=scenario.repo,
                worktree_identity=scenario.repo,
                branch=branch,
                head=head,
                session_id=session_id,
                invocation_id=invocation_id,
                host_capability=mismatched_risk_capability,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        risk_capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-context-risk",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        observation = bridge.observe_host_risk_context(
            native_task_event=register_native_task_event(
                bridge,
                task_state=task_state,
                lease_digest=None,
                repository=scenario.repo,
                branch=branch,
                head=head,
                session_id=session_id,
                invocation_id=invocation_id,
            ),
            trusted_route_context=route_context,
            clarification_resolution=None,
            authorization=authorization,
            repository_identity=scenario.repo,
            worktree_identity=scenario.repo,
            branch=branch,
            head=head,
            session_id=session_id,
            invocation_id=invocation_id,
            host_capability=risk_capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        context = bridge.validate_host_risk_context(
            observation,
            expected_task_digest=task_digest,
            expected_decision_digest=decision_digest,
            expected_repository_identity=scenario.repo,
            expected_worktree_identity=scenario.repo,
            expected_branch=branch,
            expected_head=head,
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            expected_effect="local_write",
            expected_subject_digest=subject_digest,
            clock=lambda: 100.0,
        )
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "8" * 64,
            lock_digest="sha256:" + "9" * 64,
            governing_base_commit="d" * 40,
            session_id=session_id,
            invocation_id=invocation_id,
            freshness_deadline=130.0,
        )

        result = evaluate_local_risk(
            scenario.repo,
            policy,
            task_state=task_state,
            host_context=context,
        )

        self.assertEqual(
            risk_check(result, "RS_CLARIFICATION_REQUIRED").status, "PASS"
        )
        self.assertEqual(
            risk_check(result, "RS_AUTHORITY_REQUIRED").status, "PASS"
        )
        with self.assertRaisesRegex(ValueError, "RS_HOST_CONTEXT"):
            bridge.consume_validated_host_risk_context(context)

    def test_host_context_rejects_cross_task_and_changed_head(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-current")
        branch = "codex/risk-current"
        head = git(scenario.repo, "rev-parse", "HEAD")
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "a" * 64,
            lock_digest="sha256:" + "b" * 64,
            governing_base_commit="c" * 40,
            session_id="session-risk-current",
            invocation_id="invocation-risk-current",
            freshness_deadline=130.0,
        )
        store = TaskStore(worktree_git_dir(scenario.repo))
        state_a = store.start(
            "task-risk-current-a",
            outcome="local_change",
            branch=branch,
            task_digest=contract_digest({"task": "current-a"}),
            decision_digest=contract_digest({"decision": "current-a"}),
        )
        state_b = store.start(
            "task-risk-current-b",
            outcome="local_change",
            branch=branch,
            task_digest=contract_digest({"task": "current-b"}),
            decision_digest=contract_digest({"decision": "current-b"}),
        )
        cross_context = validated_host_context(
            bridge,
            task_state=state_a,
            lease_digest=None,
            repository=scenario.repo,
            branch=branch,
            head=head,
            session_id="session-risk-current",
            invocation_id="invocation-risk-current",
            decision_digest=str(state_a["decision_digest"]),
        )

        cross = evaluate_local_risk(
            scenario.repo,
            policy,
            task_state=state_b,
            host_context=cross_context,
        )

        self.assertNotEqual(
            risk_check(cross, "RS_TASK_STATE").status, "PASS"
        )
        self.assertEqual(
            risk_check(cross, "RS_CLARIFICATION_REQUIRED").status,
            "UNKNOWN",
        )
        self.assertEqual(
            risk_check(cross, "RS_AUTHORITY_REQUIRED").status, "UNKNOWN"
        )

        head_context = validated_host_context(
            bridge,
            task_state=state_a,
            lease_digest=None,
            repository=scenario.repo,
            branch=branch,
            head=head,
            session_id="session-risk-current",
            invocation_id="invocation-risk-current",
            decision_digest=str(state_a["decision_digest"]),
        )
        (scenario.repo / "head-drift.txt").write_text(
            "drift\n", encoding="utf-8"
        )
        git(scenario.repo, "add", "head-drift.txt")
        git(scenario.repo, "commit", "-m", "test: move head")

        drifted = evaluate_local_risk(
            scenario.repo,
            policy,
            task_state=state_a,
            host_context=head_context,
        )

        self.assertEqual(
            risk_check(drifted, "RS_CLARIFICATION_REQUIRED").status,
            "UNKNOWN",
        )
        self.assertEqual(
            risk_check(drifted, "RS_AUTHORITY_REQUIRED").status, "UNKNOWN"
        )

    def test_task_and_lease_require_opaque_host_anchors(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.policy import load_policy
        from control_plane.repository import worktree_git_dir
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-opaque")
        branch = "codex/risk-opaque"
        head = git(scenario.repo, "rev-parse", "HEAD")
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy_digest = contract_digest(mapping)
        policy = governing_policy(
            policy=mapping,
            policy_digest=policy_digest,
            runtime_digest="sha256:" + "d" * 64,
            lock_digest="sha256:" + "e" * 64,
            governing_base_commit="f" * 40,
            session_id="session-risk-opaque",
            invocation_id="invocation-risk-opaque",
            freshness_deadline=130.0,
        )
        state_dir = worktree_git_dir(scenario.repo)
        store = TaskStore(state_dir)
        state = store.start(
            "task-risk-opaque",
            outcome="local_change",
            branch=branch,
            task_digest=contract_digest({"task": "opaque"}),
            decision_digest=contract_digest({"decision": "opaque"}),
        )
        lease = TaskLease.acquire(
            state_dir,
            task_id=str(state["task_id"]),
            worktree=str(scenario.repo),
            branch=branch,
            session_id="session-risk-opaque",
            paths=["dirty.txt"],
            policy_digest=policy_digest,
        )
        context = validated_host_context(
            bridge,
            task_state=state,
            lease_digest=str(lease["lease_digest"]),
            repository=scenario.repo,
            branch=branch,
            head=head,
            session_id="session-risk-opaque",
            invocation_id="invocation-risk-opaque",
            decision_digest=str(state["decision_digest"]),
        )
        (scenario.repo / "dirty.txt").write_text(
            "leased\n", encoding="utf-8"
        )

        valid = evaluate_local_risk(
            scenario.repo,
            policy,
            task_state=state,
            host_context=context,
        )

        self.assertEqual(risk_check(valid, "RS_TASK_STATE").status, "PASS")
        self.assertEqual(risk_check(valid, "RS_LOCAL_DIRTY").status, "PASS")

        state = store.status(str(state["task_id"]))
        lease = TaskLease.validate(
            state_dir,
            task_id=str(state["task_id"]),
            worktree=str(scenario.repo),
            branch=branch,
            session_id="session-risk-opaque",
            policy_digest=policy_digest,
            changed_paths=["dirty.txt"],
        )
        tampered_context = validated_host_context(
            bridge,
            task_state=state,
            lease_digest=str(lease["lease_digest"]),
            repository=scenario.repo,
            branch=branch,
            head=head,
            session_id="session-risk-opaque",
            invocation_id="invocation-risk-opaque",
            decision_digest=str(state["decision_digest"]),
        )
        task_path = (
            state_dir
            / "codex-control-plane"
            / "tasks"
            / f"{state['task_id']}.json"
        )
        tampered_state = dict(state)
        tampered_state["generation"] = int(state["generation"]) + 1
        task_path.write_text(
            json.dumps(tampered_state, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        lease_path = (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{state['task_id']}.json"
        )
        tampered_lease = dict(lease)
        tampered_lease["session_id"] = "session-risk-attacker"
        tampered_lease["lease_digest"] = contract_digest(
            {
                key: value
                for key, value in tampered_lease.items()
                if key != "lease_digest"
            }
        )
        lease_path.write_text(
            json.dumps(tampered_lease, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        tampered = evaluate_local_risk(
            scenario.repo,
            policy,
            task_state=tampered_state,
            host_context=tampered_context,
        )

        self.assertEqual(
            risk_check(tampered, "RS_TASK_STATE").status, "FAIL"
        )
        self.assertEqual(
            risk_check(tampered, "RS_LOCAL_DIRTY").status, "FAIL"
        )

    def test_lease_anchor_uses_the_record_validated_under_flock(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.risk_sentinel import _lease_covers_dirty_tree

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-lease-race")
        branch = "codex/risk-lease-race"
        mapping = scenario.policy()
        policy_digest = contract_digest(mapping)
        state_dir = worktree_git_dir(scenario.repo)
        state = TaskStore(state_dir).start(
            "task-risk-lease-race",
            outcome="local_change",
            branch=branch,
            task_digest=contract_digest({"task": "lease-race"}),
            decision_digest=contract_digest({"decision": "lease-race"}),
        )
        lease = TaskLease.acquire(
            state_dir,
            task_id=str(state["task_id"]),
            worktree=str(scenario.repo),
            branch=branch,
            session_id="session-risk-lease-race",
            paths=["dirty.txt"],
            policy_digest=policy_digest,
        )
        (scenario.repo / "dirty.txt").write_text(
            "leased\n", encoding="utf-8"
        )
        host_evidence = {
            "task_id": state["task_id"],
            "task_digest": state["task_digest"],
            "task_state_digest": contract_digest(state),
            "decision_digest": state["decision_digest"],
            "branch": branch,
            "lease_digest": lease["lease_digest"],
            "session_id": "session-risk-lease-race",
        }
        replacement = {
            **lease,
            "lease_digest": "sha256:" + "f" * 64,
        }

        with patch.object(
            TaskLease,
            "validate",
            return_value=replacement,
        ):
            covered = _lease_covers_dirty_tree(
                scenario.repo,
                task_state=state,
                policy=mapping,
                branch=branch,
                host_evidence=host_evidence,
            )

        self.assertFalse(covered)

    def test_local_base_policy_observation_is_complete_and_one_shot(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.lockfile import runtime_digest
        from tests.host_adapter_test_support import (
            _register_native_object,
            native_session_event,
        )

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name) / "repo"
        subprocess.run(
            ["git", "init", "-b", "main", str(repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        git(repo, "config", "user.name", "Risk Sentinel Tests")
        git(repo, "config", "user.email", "risk@example.invalid")
        git(
            repo,
            "remote",
            "add",
            "origin",
            "https://github.com/example/control-plane.git",
        )
        (repo / "control_plane").mkdir()
        (repo / "control_plane" / "__init__.py").write_text(
            '"""test runtime"""\n', encoding="utf-8"
        )
        (repo / ".codex").mkdir()
        policy_blob = (
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        ).read_bytes()
        (repo / ".codex" / "project-policy.toml").write_bytes(policy_blob)
        digest = runtime_digest(repo, "control_plane", runtime_layout="source")
        (repo / ".codex" / "control-plane.lock").write_text(
            "runtime_layout = \"source\"\n"
            "runtime_package = \"control_plane\"\n"
            "[digests]\n"
            f"runtime = \"{digest}\"\n",
            encoding="utf-8",
        )
        git(repo, "add", ".")
        git(repo, "commit", "-m", "test: governing base")
        base_commit = git(repo, "rev-parse", "HEAD")
        git(
            repo,
            "update-ref",
            "refs/remotes/origin/main",
            base_commit,
        )
        session_id = "session-risk-base"
        invocation_id = "invocation-risk-base"
        base_ref = "refs/remotes/origin/main"
        blob_digest = f"sha256:{sha256(policy_blob).hexdigest()}"
        event = object.__new__(bridge.NativeGitBaseEvent)
        event._consumed = False
        event.event_id = "native-git-base"
        event.repository_identity = "example/control-plane"
        event.remote = "origin"
        event.base_branch = "main"
        event.base_ref = base_ref
        event.base_commit = base_commit
        event.policy_blob = policy_blob
        event.policy_blob_digest = blob_digest
        event.policy_eof = True
        event.partial_clone = False
        event.session_id = session_id
        event.invocation_id = invocation_id
        event.observed_at_monotonic = 100.0
        _register_native_object(event, "git_base")
        registered = object.__new__(
            bridge.RegisteredGoverningBaseContext
        )
        registered._consumed = False
        registered.context_id = "registered-governing-base"
        registered.canonical_repository = str(repo.resolve())
        registered.attestor_worktree = str(repo.resolve())
        registered.target_worktree = str(repo.resolve())
        registered.repository_identity = "example/control-plane"
        registered.remote = "origin"
        registered.base_branch = "main"
        registered.base_ref = base_ref
        registered.base_commit = base_commit
        registered.policy_blob_digest = blob_digest
        registered.runtime_layout = "source"
        registered.session_id = session_id
        registered.invocation_id = invocation_id
        registered.freshness_deadline = 130.0
        _register_native_object(registered, "governing_base_context")
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id="session-base-risk",
                session_id=session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

        for attribute, invalid in (
            ("policy_eof", False),
            ("partial_clone", True),
            ("repository_identity", "other/repository"),
            ("base_ref", "refs/remotes/origin/other"),
            ("observed_at_monotonic", 1.0),
        ):
            with self.subTest(invalid_binding=attribute):
                original = getattr(event, attribute)
                setattr(event, attribute, invalid)
                with self.assertRaisesRegex(
                    ValueError, "RS_LOCAL_BASE_UNKNOWN"
                ):
                    bridge.frame_local_base_policy_source(
                        event,
                        host_capability=capability,
                        registered_base=registered,
                        session_id=session_id,
                        invocation_id=invocation_id,
                        clock=lambda: 100.0,
                        ttl_seconds=30,
                    )
                setattr(event, attribute, original)

        observation = bridge.frame_local_base_policy_source(
            event,
            host_capability=capability,
            registered_base=registered,
            session_id=session_id,
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        validated = bridge.validate_local_base_policy_source(
            observation,
            expected_registered_base=registered,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )
        governing = bridge.load_governing_local_policy(
            canonical_repo=repo,
            governing_base_observation=validated,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )

        self.assertEqual(governing.governing_base_commit, base_commit)
        self.assertEqual(governing.policy["git"]["base_branch"], "main")
        with self.assertRaisesRegex(ValueError, "RS_LOCAL_BASE_UNKNOWN"):
            bridge.load_governing_local_policy(
                canonical_repo=repo,
                governing_base_observation=validated,
                expected_invocation_id=invocation_id,
                clock=lambda: 100.0,
            )

    def test_task6_has_no_installed_policy_or_mapping_shortcut(self) -> None:
        import control_plane.host_bridge as bridge

        for candidate in (
            "ValidatedInstalledPolicyObservation",
            {"policy": ".codex/project-policy.toml"},
            object(),
        ):
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaisesRegex(
                    ValueError, "RS_LOCAL_BASE_UNKNOWN"
                ):
                    bridge.load_governing_local_policy(
                        canonical_repo=ROOT,
                        governing_base_observation=candidate,
                        expected_invocation_id="invocation-risk-base",
                        clock=lambda: 100.0,
                    )


if __name__ == "__main__":
    unittest.main()
