from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.host_adapter_test_support import (
    native_session_event,
    native_user_interaction_event,
)
from tests.router_test_support import task_envelope


ROOT = Path(__file__).parents[1].resolve()
HEAD = "a" * 40
SESSION_ID = "session-clarification-tests"
INVOCATION_ID = "invocation-clarification-tests"


class FixedRepositoryInspector:
    def __init__(
        self,
        *,
        status: str = "unresolved",
        evidence: tuple[str, ...] = ("control_plane/contracts.py",),
    ) -> None:
        self.status = status
        self.evidence = evidence
        self.calls: list[tuple[Path, str, int, int]] = []

    def inspect(
        self,
        *,
        canonical_root: Path,
        question_digest: str,
        max_files: int,
        max_bytes: int,
    ):
        from control_plane.clarification import RepositoryEvidenceFacts

        self.calls.append(
            (canonical_root, question_digest, max_files, max_bytes)
        )
        return RepositoryEvidenceFacts(
            status=self.status,
            evidence_items=self.evidence,
        )


class ClarificationTests(unittest.TestCase):
    def setUp(self) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        self.bridge = bridge
        self.task = task_envelope(
            task_id="task-clarification-tests",
            risk={
                "uncertainty": 2,
                "blast_radius": 2,
                "irreversibility": 1,
                "verification_complexity": 2,
            },
            scope_paths=["control_plane/", "tests/"],
        )
        self.task_digest = contract_digest(self.task)
        self.question_digest = contract_digest(
            {"question": "Preserve the current contract?"}
        )
        self.issue_draft = {
            "schema_version": 1,
            "issue_id": "issue-contract-choice",
            "issue_kind": "clarification",
            "severity": "high",
            "question_digest": self.question_digest,
            "option_ids": ["preserve-current", "change-contract"],
            "recommended_option_id": "preserve-current",
        }
        self.prompt_draft = {
            "schema_version": 1,
            "question_text": "Should the current contract be preserved?",
            "options": [
                {"id": "preserve-current", "label": "Preserve current"},
                {"id": "change-contract", "label": "Change contract"},
            ],
            "recommended_option_id": "preserve-current",
            "consequence_text": "Changing it may break existing callers.",
        }
        bridge._clarification_repository_inspector_validator = (
            lambda value: isinstance(value, FixedRepositoryInspector)
        )

    def capability(
        self,
        *,
        invocation_id: str = INVOCATION_ID,
        session_id: str = SESSION_ID,
        now: float = 100.0,
    ):
        event = native_session_event(
            event_id=f"event-{invocation_id}",
            session_id=session_id,
            invocation_id=invocation_id,
            observed_at_monotonic=now,
        )
        return self.bridge.attest_host_adapter_capability(
            event,
            expected_session_id=session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: now,
            ttl_seconds=30,
        )

    def framed_issue(
        self,
        *,
        issue_draft: dict | None = None,
        capability=None,
        invocation_id: str = INVOCATION_ID,
    ):
        return self.bridge.frame_clarification_issue(
            issue_draft or self.issue_draft,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            invocation_id=invocation_id,
            host_capability=capability or self.capability(
                invocation_id=invocation_id
            ),
        )

    def validated_request(
        self,
        *,
        task: dict | None = None,
        repository_status: str = "unresolved",
        issue_draft: dict | None = None,
        use_not_checked: bool = False,
        invocation_id: str = INVOCATION_ID,
    ):
        from control_plane.clarification import (
            REPOSITORY_EVIDENCE_NOT_CHECKED,
            build_validated_clarification_request,
        )

        framed_task = task or self.task
        framed_task_digest = self.bridge.contract_digest(framed_task)
        framed_issue_draft = issue_draft or self.issue_draft
        capability = self.capability(invocation_id=invocation_id)
        issue = self.bridge.frame_clarification_issue(
            framed_issue_draft,
            task_digest=framed_task_digest,
            session_id=SESSION_ID,
            invocation_id=invocation_id,
            host_capability=capability,
        )
        prompt = self.bridge.frame_clarification_prompt_view(
            self.prompt_draft,
            issue=issue,
            task_digest=framed_task_digest,
            session_id=SESSION_ID,
            invocation_id=invocation_id,
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        if use_not_checked:
            repository = REPOSITORY_EVIDENCE_NOT_CHECKED
        else:
            observation = self.bridge.observe_clarification_repository(
                task_digest=framed_task_digest,
                session_id=SESSION_ID,
                repository_identity=str(ROOT),
                worktree_identity=str(ROOT),
                branch="codex/test",
                head=HEAD,
                question_digest=self.question_digest,
                invocation_id=invocation_id,
                inspector=FixedRepositoryInspector(status=repository_status),
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            repository = (
                self.bridge.validate_clarification_repository_observation(
                    observation,
                    expected_task_digest=framed_task_digest,
                    expected_session_id=SESSION_ID,
                    expected_repository_identity=str(ROOT),
                    expected_worktree_identity=str(ROOT),
                    expected_branch="codex/test",
                    expected_head=HEAD,
                    expected_question_digest=self.question_digest,
                    expected_invocation_id=invocation_id,
                    clock=lambda: 100.0,
                )
            )
        return build_validated_clarification_request(
            framed_task,
            issue=issue,
            prompt_view=prompt,
            session_id=SESSION_ID,
            repository_observation=repository,
            host_capability=capability,
        )

    def trusted_route_context(
        self,
        *,
        task_digest: str | None = None,
        decision_digest: str | None = None,
        repository: Path = ROOT,
        worktree: Path = ROOT,
        branch: str = "codex/test",
        head: str = HEAD,
        session_id: str = SESSION_ID,
        invocation_id: str = INVOCATION_ID,
        now: float = 100.0,
        ttl_seconds: float = 30.0,
        clarification_status: str = "ask_user",
        route_material_digest: str | None = None,
    ):
        from control_plane.contracts import contract_digest

        context = object.__new__(self.bridge.TrustedRouteContext)
        context._consumed = False
        context._clock = lambda: now
        context.task_digest = task_digest or self.task_digest
        context.route_digest = decision_digest or contract_digest(
            {"decision": "clarification-lifecycle"}
        )
        context.route_material_digest = (
            route_material_digest
            or contract_digest(
                {
                    "task_digest": context.task_digest,
                    "route_family": "clarification-lifecycle",
                }
            )
        )
        context.inventory_digest = contract_digest(
            {"inventory": "clarification-lifecycle"}
        )
        context.inventory_observation_id = (
            f"inventory-{invocation_id}"
        )
        context.registry_digest = contract_digest(
            {"registry": "clarification-lifecycle"}
        )
        context.repository_identity = str(repository.resolve())
        context.worktree_identity = str(worktree.resolve())
        context.branch = branch
        context.head = head
        context.session_id = session_id
        context.invocation_id = invocation_id
        context.required_resources = ()
        context.recommended_resources = ()
        context.forbidden_resources = ()
        context.resource_bindings = ()
        context.authorized_effects = ()
        context.blocked_effects = ("local_write",)
        context.clarification_status = clarification_status
        context.context_nonce = f"context-{invocation_id}"
        context.issued_at_monotonic = now
        context.freshness_deadline = now + ttl_seconds
        self.bridge._register_runtime_host_object(
            context, "trusted_route_context"
        )
        return context

    def repository_context(
        self,
        *,
        task_digest: str | None = None,
        question_digest: str | None = None,
        repository: Path = ROOT,
        worktree: Path = ROOT,
        branch: str = "codex/test",
        head: str = HEAD,
        session_id: str = SESSION_ID,
        invocation_id: str = "invocation-resolution-repository",
        status: str = "unresolved",
        evidence: tuple[str, ...] = ("control_plane/contracts.py",),
    ):
        observation = self.bridge.observe_clarification_repository(
            task_digest=task_digest or self.task_digest,
            session_id=session_id,
            repository_identity=repository,
            worktree_identity=worktree,
            branch=branch,
            head=head,
            question_digest=question_digest or self.question_digest,
            invocation_id=invocation_id,
            inspector=FixedRepositoryInspector(
                status=status, evidence=evidence
            ),
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        return self.bridge.validate_clarification_repository_observation(
            observation,
            expected_task_digest=task_digest or self.task_digest,
            expected_session_id=session_id,
            expected_repository_identity=repository,
            expected_worktree_identity=worktree,
            expected_branch=branch,
            expected_head=head,
            expected_question_digest=(
                question_digest or self.question_digest
            ),
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
        )

    def trusted_interaction(
        self,
        request,
        *,
        session_id: str = SESSION_ID,
        invocation_id: str = "invocation-resolution",
        selected_option_id: str = "preserve-current",
    ):
        response_digest = self.bridge.contract_digest(
            {"response": selected_option_id}
        )
        subject_digest = (
            self.bridge.clarification_interaction_subject_digest(
                request_digest=request.request_digest,
                task_digest=request.task_digest,
                session_id=session_id,
                invocation_id=invocation_id,
                selected_option_id=selected_option_id,
                response_digest=response_digest,
            )
        )
        return self.bridge.frame_trusted_interaction(
            native_event=native_user_interaction_event(
                event_id=f"event-{invocation_id}",
                session_id=session_id,
                invocation_id=invocation_id,
                task_digest=request.task_digest,
                subject_digest=subject_digest,
                observed_at_monotonic=100.0,
            ),
            request=request,
            selected_option_id=selected_option_id,
            response_digest=response_digest,
            session_id=session_id,
            invocation_id=invocation_id,
            host_capability=self.capability(
                invocation_id=invocation_id,
                session_id=session_id,
            ),
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

    def start_task_at(
        self,
        store,
        task_id: str,
        source: str,
        *,
        task_digest: str | None = None,
        decision_digest: str | None = None,
        branch: str = "codex/test",
    ) -> dict:
        from control_plane.contracts import contract_digest

        task_digest = task_digest or self.task_digest
        decision_digest = decision_digest or contract_digest(
            {"decision": "clarification-lifecycle"}
        )
        state = store.start(
            task_id,
            outcome="local_change",
            branch=branch,
            task_digest=task_digest,
            decision_digest=decision_digest,
        )
        transitions = (
            ("planned", None),
            ("ready", {"preflight_ok": True}),
            ("implementing", None),
            ("verifying", {"implementation_complete": True}),
            (
                "review_ready",
                {
                    "gates_ok": True,
                    "documentation_decision": decision_digest,
                },
            ),
        )
        for target, evidence in transitions:
            if source == "framed":
                break
            state = store.transition(
                task_id,
                target,
                evidence=evidence,
                current_branch=branch,
            )
            if target == source:
                break
        self.assertEqual(state["state"], source)
        return state

    def require_flow(
        self,
        store,
        *,
        task_id: str,
        source: str = "planned",
        task_digest: str | None = None,
        decision_digest: str | None = None,
        repository: Path = ROOT,
        worktree: Path = ROOT,
        branch: str = "codex/test",
        head: str = HEAD,
        session_id: str = SESSION_ID,
        invocation_id: str = "invocation-require",
        request=None,
    ):
        from control_plane.contracts import contract_digest

        task_digest = task_digest or self.task_digest
        decision_digest = decision_digest or contract_digest(
            {"decision": "clarification-lifecycle"}
        )
        prior_state = self.start_task_at(
            store,
            task_id,
            source,
            task_digest=task_digest,
            decision_digest=decision_digest,
            branch=branch,
        )
        if source == "implementing":
            from control_plane.lifecycle import TaskLease

            TaskLease.acquire(
                store.state_dir,
                task_id=task_id,
                worktree=str(worktree),
                branch=branch,
                session_id=session_id,
                paths=["."],
                policy_digest=contract_digest(
                    {"policy": "clarification-lifecycle"}
                ),
            )
        request = request or self.validated_request(
            invocation_id=invocation_id
        )
        context = self.trusted_route_context(
            task_digest=task_digest,
            decision_digest=decision_digest,
            repository=repository,
            worktree=worktree,
            branch=branch,
            head=head,
            session_id=session_id,
            invocation_id=invocation_id,
        )
        state = store.require_clarification(
            task_id,
            request=request,
            route_context=context,
            expected_generation=prior_state["generation"],
            current_branch=branch,
            task_digest=task_digest,
            decision_digest=decision_digest,
        )
        return request, state

    def resolve_flow(
        self,
        store,
        *,
        task_id: str,
        request,
        required_state: dict,
        task_digest: str | None = None,
        decision_digest: str | None = None,
        repository: Path = ROOT,
        worktree: Path = ROOT,
        branch: str = "codex/test",
        head: str = HEAD,
        session_id: str = SESSION_ID,
        invocation_id: str = "invocation-resolution",
        repository_status: str = "unresolved",
        repository_context=None,
        question_digest: str | None = None,
        context_digest: str | None = None,
    ):
        from control_plane.contracts import contract_digest

        task_digest = task_digest or self.task_digest
        decision_digest = decision_digest or contract_digest(
            {"decision": "clarification-lifecycle"}
        )
        route_context = self.trusted_route_context(
            task_digest=task_digest,
            decision_digest=decision_digest,
            repository=repository,
            worktree=worktree,
            branch=branch,
            head=head,
            session_id=session_id,
            invocation_id=invocation_id,
            clarification_status="resolved",
        )
        interaction = self.trusted_interaction(
            request,
            session_id=session_id,
            invocation_id=invocation_id,
        )
        if repository_context is None:
            repository_context = self.repository_context(
                task_digest=task_digest,
                question_digest=question_digest,
                repository=repository,
                worktree=worktree,
                branch=branch,
                head=head,
                session_id=session_id,
                invocation_id=f"{invocation_id}-repository",
                status=repository_status,
            )
        return store.resolve_and_resume_clarification(
            task_id,
            interaction=interaction,
            route_context=route_context,
            repository_context=repository_context,
            expected_generation=required_state["generation"],
            current_branch=branch,
            expected_head=head,
            task_digest=task_digest,
            decision_digest=decision_digest,
            context_digest=(
                context_digest
                or self.bridge.clarification_route_context_digest(
                    route_context
                )
            ),
            question_digest=question_digest or self.question_digest,
        )

    def test_clarification_request_is_closed_deterministic_and_bound(
        self,
    ) -> None:
        from control_plane.clarification import (
            validate_clarification_request,
        )

        first = self.validated_request(invocation_id="invocation-first")
        second = self.validated_request(invocation_id="invocation-second")

        self.assertEqual(first.payload, second.payload)
        self.assertEqual(first.request_digest, second.request_digest)
        self.assertEqual(validate_clarification_request(first.payload), [])
        changed = copy.deepcopy(first.payload)
        changed["session_id"] = "session-other"
        self.assertNotEqual(first.request_digest, self.bridge.contract_digest(changed))
        changed["unexpected"] = True
        self.assertIn(
            "C_SCHEMA",
            {issue.code for issue in validate_clarification_request(changed)},
        )
        mismatched_task = copy.deepcopy(self.task)
        mismatched_task["risk"]["uncertainty"] = 0
        with self.assertRaisesRegex(ValueError, "C_SEVERITY"):
            self.validated_request(
                task=mismatched_task,
                invocation_id="invocation-severity-mismatch",
            )

    def test_host_validates_and_wraps_request_before_routing(self) -> None:
        request = self.validated_request()

        self.assertEqual(request.payload["task_digest"], self.task_digest)
        self.assertEqual(request.payload["session_id"], SESSION_ID)
        self.assertEqual(
            request.payload["question_digest"], self.question_digest
        )
        self.assertTrue(
            self.bridge._runtime_host_object_is_live(
                request, "validated_clarification_request"
            )
        )

    def test_prompt_view_is_framed_by_host_and_consumed_once(self) -> None:
        from control_plane.clarification import (
            build_validated_clarification_request,
        )

        decision_issue = copy.deepcopy(self.issue_draft)
        decision_issue["issue_kind"] = "decision_approval"
        capability = self.capability()
        issue = self.framed_issue(
            issue_draft=decision_issue, capability=capability
        )
        prompt = self.bridge.frame_clarification_prompt_view(
            self.prompt_draft,
            issue=issue,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            invocation_id=INVOCATION_ID,
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        not_checked = __import__(
            "control_plane.clarification",
            fromlist=["REPOSITORY_EVIDENCE_NOT_CHECKED"],
        ).REPOSITORY_EVIDENCE_NOT_CHECKED
        build_validated_clarification_request(
            self.task,
            issue=issue,
            prompt_view=prompt,
            session_id=SESSION_ID,
            repository_observation=not_checked,
            host_capability=capability,
        )
        with self.assertRaisesRegex(ValueError, "C_PRESENTATION_UNAVAILABLE"):
            build_validated_clarification_request(
                self.task,
                issue=issue,
                prompt_view=prompt,
                session_id=SESSION_ID,
                repository_observation=not_checked,
                host_capability=capability,
            )

    def test_prompt_view_mapping_replay_or_cross_context_is_rejected(
        self,
    ) -> None:
        from control_plane.clarification import (
            REPOSITORY_EVIDENCE_NOT_CHECKED,
            build_validated_clarification_request,
        )

        capability = self.capability()
        issue = self.framed_issue(capability=capability)
        prompt = self.bridge.frame_clarification_prompt_view(
            self.prompt_draft,
            issue=issue,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            invocation_id=INVOCATION_ID,
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        with self.assertRaisesRegex(ValueError, "C_PRESENTATION_UNAVAILABLE"):
            build_validated_clarification_request(
                self.task,
                issue=issue,
                prompt_view=copy.deepcopy(prompt.payload),
                session_id=SESSION_ID,
                repository_observation=REPOSITORY_EVIDENCE_NOT_CHECKED,
                host_capability=capability,
            )

        decision_issue_draft = copy.deepcopy(self.issue_draft)
        decision_issue_draft["issue_kind"] = "decision_approval"
        decision_capability = self.capability(
            invocation_id="invocation-prompt-tamper"
        )
        decision_issue = self.framed_issue(
            issue_draft=decision_issue_draft,
            capability=decision_capability,
            invocation_id="invocation-prompt-tamper",
        )
        tampered_prompt = self.bridge.frame_clarification_prompt_view(
            self.prompt_draft,
            issue=decision_issue,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            invocation_id="invocation-prompt-tamper",
            host_capability=decision_capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        tampered_prompt.payload["question_text"] = "Tampered after framing"
        with self.assertRaisesRegex(ValueError, "C_PRESENTATION_UNAVAILABLE"):
            build_validated_clarification_request(
                self.task,
                issue=decision_issue,
                prompt_view=tampered_prompt,
                session_id=SESSION_ID,
                repository_observation=REPOSITORY_EVIDENCE_NOT_CHECKED,
                host_capability=decision_capability,
            )

    def test_raw_request_mapping_cannot_be_promoted_even_if_byte_identical(
        self,
    ) -> None:
        from control_plane.clarification import (
            require_validated_clarification_request,
        )

        request = self.validated_request()
        with self.assertRaisesRegex(ValueError, "C_UNTRUSTED_REQUEST"):
            require_validated_clarification_request(
                copy.deepcopy(request.payload)
            )
        request.payload["session_id"] = "session-tampered"
        with self.assertRaisesRegex(ValueError, "C_UNTRUSTED_REQUEST"):
            require_validated_clarification_request(request)

    def test_missing_host_capability_cannot_create_validated_request(
        self,
    ) -> None:
        from control_plane.clarification import (
            REPOSITORY_EVIDENCE_NOT_CHECKED,
            build_validated_clarification_request,
        )

        with self.assertRaisesRegex(ValueError, "C_UNTRUSTED_ISSUE"):
            build_validated_clarification_request(
                self.task,
                issue=copy.deepcopy(self.issue_draft),
                prompt_view=copy.deepcopy(self.prompt_draft),
                session_id=SESSION_ID,
                repository_observation=REPOSITORY_EVIDENCE_NOT_CHECKED,
                host_capability=None,
            )

    def test_raw_repository_evidence_cannot_claim_resolved(self) -> None:
        from control_plane.clarification import (
            build_validated_clarification_request,
        )

        capability = self.capability()
        issue = self.framed_issue(capability=capability)
        prompt = self.bridge.frame_clarification_prompt_view(
            self.prompt_draft,
            issue=issue,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            invocation_id=INVOCATION_ID,
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        with self.assertRaisesRegex(
            ValueError, "C_REPOSITORY_OBSERVATION_UNTRUSTED"
        ):
            build_validated_clarification_request(
                self.task,
                issue=issue,
                prompt_view=prompt,
                session_id=SESSION_ID,
                repository_observation={
                    "status": "resolved",
                    "evidence_digest": "sha256:" + ("1" * 64),
                },
                host_capability=capability,
            )

    def test_not_checked_repository_path_is_typed_and_cannot_resolve_factual_ambiguity(
        self,
    ) -> None:
        decision = copy.deepcopy(self.issue_draft)
        decision["issue_kind"] = "decision_approval"
        self.assertEqual(
            self.validated_request(
                issue_draft=decision,
                use_not_checked=True,
                invocation_id="invocation-not-checked-decision",
            ).payload["repository_check"]["status"],
            "not_checked",
        )
        with self.assertRaisesRegex(
            ValueError, "C_REPOSITORY_CHECK_REQUIRED"
        ):
            self.validated_request(
                use_not_checked=True,
                invocation_id="invocation-not-checked-factual",
            )

    def test_repository_inspector_is_closed_bounded_and_host_selected(
        self,
    ) -> None:
        inspector = FixedRepositoryInspector()
        self.bridge._clarification_repository_inspector_validator = (
            lambda _: False
        )
        with self.assertRaisesRegex(
            ValueError, "C_REPOSITORY_OBSERVATION_UNTRUSTED"
        ):
            self.bridge.observe_clarification_repository(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                repository_identity=str(ROOT),
                worktree_identity=str(ROOT),
                branch="codex/test",
                head=HEAD,
                question_digest=self.question_digest,
                invocation_id=INVOCATION_ID,
                inspector=inspector,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
        self.bridge._clarification_repository_inspector_validator = (
            lambda value: isinstance(value, FixedRepositoryInspector)
        )
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary).resolve()
            evidence = repository / "evidence.txt"
            evidence.write_text("first", encoding="utf-8")
            first = self.bridge.observe_clarification_repository(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                repository_identity=repository,
                worktree_identity=repository,
                branch="codex/test",
                head=HEAD,
                question_digest=self.question_digest,
                invocation_id="invocation-evidence-first",
                inspector=FixedRepositoryInspector(
                    evidence=("evidence.txt",)
                ),
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            evidence.write_text("second", encoding="utf-8")
            second = self.bridge.observe_clarification_repository(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                repository_identity=repository,
                worktree_identity=repository,
                branch="codex/test",
                head=HEAD,
                question_digest=self.question_digest,
                invocation_id="invocation-evidence-second",
                inspector=FixedRepositoryInspector(
                    evidence=("evidence.txt",)
                ),
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            self.assertNotEqual(first.evidence_digest, second.evidence_digest)
            first.status = "resolved"
            with self.assertRaisesRegex(
                ValueError, "C_REPOSITORY_OBSERVATION_UNTRUSTED"
            ):
                self.bridge.validate_clarification_repository_observation(
                    first,
                    expected_task_digest=self.task_digest,
                    expected_session_id=SESSION_ID,
                    expected_repository_identity=repository,
                    expected_worktree_identity=repository,
                    expected_branch="codex/test",
                    expected_head=HEAD,
                    expected_question_digest=self.question_digest,
                    expected_invocation_id="invocation-evidence-first",
                    clock=lambda: 100.0,
                )
            evidence.write_bytes(b"x" * 65537)
            with self.assertRaisesRegex(
                ValueError, "C_REPOSITORY_OBSERVATION_UNTRUSTED"
            ):
                self.bridge.observe_clarification_repository(
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    repository_identity=repository,
                    worktree_identity=repository,
                    branch="codex/test",
                    head=HEAD,
                    question_digest=self.question_digest,
                    invocation_id="invocation-evidence-overflow",
                    inspector=FixedRepositoryInspector(
                        evidence=("evidence.txt",)
                    ),
                    clock=lambda: 100.0,
                    ttl_seconds=30,
                )

    def test_repository_observation_rejects_stale_replay_and_cross_context(
        self,
    ) -> None:
        observation = self.bridge.observe_clarification_repository(
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            head=HEAD,
            question_digest=self.question_digest,
            invocation_id=INVOCATION_ID,
            inspector=FixedRepositoryInspector(),
            clock=lambda: 100.0,
            ttl_seconds=5,
        )
        with self.assertRaisesRegex(
            ValueError, "C_REPOSITORY_OBSERVATION_STALE"
        ):
            self.bridge.validate_clarification_repository_observation(
                observation,
                expected_task_digest=self.task_digest,
                expected_session_id=SESSION_ID,
                expected_repository_identity=str(ROOT),
                expected_worktree_identity=str(ROOT),
                expected_branch="codex/test",
                expected_head=HEAD,
                expected_question_digest=self.question_digest,
                expected_invocation_id=INVOCATION_ID,
                clock=lambda: 106.0,
            )
        fresh = self.bridge.observe_clarification_repository(
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            head=HEAD,
            question_digest=self.question_digest,
            invocation_id="invocation-repository-binding",
            inspector=FixedRepositoryInspector(),
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        arguments = dict(
            expected_task_digest=self.task_digest,
            expected_session_id=SESSION_ID,
            expected_repository_identity=str(ROOT),
            expected_worktree_identity=str(ROOT),
            expected_branch="codex/test",
            expected_head=HEAD,
            expected_question_digest=self.question_digest,
            expected_invocation_id="invocation-repository-binding",
            clock=lambda: 100.0,
        )
        with self.assertRaisesRegex(
            ValueError, "C_REPOSITORY_OBSERVATION_BINDING"
        ):
            self.bridge.validate_clarification_repository_observation(
                fresh,
                **{**arguments, "expected_branch": "codex/other"},
            )
        self.bridge.validate_clarification_repository_observation(
            fresh, **arguments
        )
        with self.assertRaisesRegex(
            ValueError, "C_REPOSITORY_OBSERVATION_REPLAY"
        ):
            self.bridge.validate_clarification_repository_observation(
                fresh, **arguments
            )
        stale_after_validation = (
            self.bridge.observe_clarification_repository(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                repository_identity=str(ROOT),
                worktree_identity=str(ROOT),
                branch="codex/test",
                head=HEAD,
                question_digest=self.question_digest,
                invocation_id="invocation-repository-expiry",
                inspector=FixedRepositoryInspector(),
                clock=lambda: 100.0,
                ttl_seconds=5,
            )
        )
        validated_then_expired = (
            self.bridge.validate_clarification_repository_observation(
                stale_after_validation,
                expected_task_digest=self.task_digest,
                expected_session_id=SESSION_ID,
                expected_repository_identity=str(ROOT),
                expected_worktree_identity=str(ROOT),
                expected_branch="codex/test",
                expected_head=HEAD,
                expected_question_digest=self.question_digest,
                expected_invocation_id="invocation-repository-expiry",
                clock=lambda: 100.0,
            )
        )
        capability = self.capability(
            invocation_id="invocation-repository-expiry", now=106.0
        )
        issue = self.framed_issue(
            capability=capability,
            invocation_id="invocation-repository-expiry",
        )
        prompt = self.bridge.frame_clarification_prompt_view(
            self.prompt_draft,
            issue=issue,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            invocation_id="invocation-repository-expiry",
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        from control_plane.clarification import (
            build_validated_clarification_request,
        )

        with self.assertRaisesRegex(
            ValueError, "C_REPOSITORY_OBSERVATION_STALE"
        ):
            build_validated_clarification_request(
                self.task,
                issue=issue,
                prompt_view=prompt,
                session_id=SESSION_ID,
                repository_observation=validated_then_expired,
                host_capability=capability,
            )

    def test_serialized_resolution_cannot_self_attest_trusted_host(
        self,
    ) -> None:
        from control_plane.clarification import (
            validate_clarification_resolution,
        )

        request = self.validated_request()
        payload = {
            "schema_version": 1,
            "resolution_id": "resolution-one",
            "request_digest": request.request_digest,
            "task_digest": self.task_digest,
            "session_id": SESSION_ID,
            "selected_option_id": "preserve-current",
            "response_digest": "sha256:" + ("2" * 64),
        }
        self.assertIn(
            "C_UNTRUSTED_CHANNEL",
            {
                issue.code
                for issue in validate_clarification_resolution(
                    payload,
                    request=request.payload,
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    trusted_interaction=copy.deepcopy(payload),
                )
            },
        )

    def test_serialized_authorization_cannot_self_attest_trusted_host(
        self,
    ) -> None:
        from control_plane.clarification import validate_authorization

        codes = {
            issue.code
            for issue in validate_authorization(
                {"issuer": "trusted_host"},
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                repository_identity=str(ROOT),
                worktree_identity=str(ROOT),
                branch="codex/test",
                expected_head=HEAD,
                subject_digest=self.question_digest,
                scope_paths=self.task["scope_paths"],
                effect="commit",
                operation_nonce="operation-commit",
                invocation_id=INVOCATION_ID,
                now_monotonic=100.0,
            )
        }
        self.assertEqual(codes, {"Z_UNTRUSTED_CHANNEL"})

    def test_host_wrapped_authorization_validates_exact_bindings(
        self,
    ) -> None:
        from control_plane.clarification import validate_authorization

        capability = self.capability()
        event = native_user_interaction_event(
            event_id="authorization-event",
            session_id=SESSION_ID,
            invocation_id=INVOCATION_ID,
            task_digest=self.task_digest,
            subject_digest=self.question_digest,
            observed_at_monotonic=100.0,
        )
        authorization = self.bridge.frame_effect_authorization(
            event,
            host_capability=capability,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            expected_head=HEAD,
            subject_digest=self.question_digest,
            scope_paths=tuple(self.task["scope_paths"]),
            effect="commit",
            operation_nonce="operation-commit",
            invocation_id=INVOCATION_ID,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        arguments = dict(
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            expected_head=HEAD,
            subject_digest=self.question_digest,
            scope_paths=self.task["scope_paths"],
            effect="commit",
            operation_nonce="operation-commit",
            invocation_id=INVOCATION_ID,
            now_monotonic=100.0,
        )
        self.assertEqual(validate_authorization(authorization, **arguments), [])
        arguments["branch"] = "codex/other"
        self.assertIn(
            "Z_BINDING",
            {issue.code for issue in validate_authorization(authorization, **arguments)},
        )
        arguments["branch"] = "codex/test"
        authorization.effect = "push"
        self.assertIn(
            "Z_UNTRUSTED_CHANNEL",
            {
                issue.code
                for issue in validate_authorization(
                    authorization, **arguments
                )
            },
        )

    def test_command_hook_json_cannot_mint_host_capability(self) -> None:
        with self.assertRaisesRegex(TypeError, "host-bound"):
            self.bridge.HostAdapterCapability()

    def test_native_host_adapter_contract_is_same_callback_and_one_shot(
        self,
    ) -> None:
        capability = self.capability()
        self.assertEqual(capability.session_id, SESSION_ID)
        self.assertEqual(capability.invocation_id, INVOCATION_ID)
        with self.assertRaisesRegex(ValueError, "E_HOST_CAPABILITY"):
            self.bridge.attest_host_adapter_capability(
                native_session_event(
                    event_id="wrong-event",
                    session_id=SESSION_ID,
                    invocation_id="wrong-invocation",
                    observed_at_monotonic=100.0,
                ),
                expected_session_id=SESSION_ID,
                expected_invocation_id=INVOCATION_ID,
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

    def test_trusted_authorization_replay_or_cross_context_fails(self) -> None:
        from control_plane.clarification import validate_authorization

        capability = self.capability()
        event = native_user_interaction_event(
            event_id="authorization-replay",
            session_id=SESSION_ID,
            invocation_id=INVOCATION_ID,
            task_digest=self.task_digest,
            subject_digest=self.question_digest,
            observed_at_monotonic=100.0,
        )
        authorization = self.bridge.frame_effect_authorization(
            event,
            host_capability=capability,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            expected_head=HEAD,
            subject_digest=self.question_digest,
            scope_paths=tuple(self.task["scope_paths"]),
            effect="commit",
            operation_nonce="operation-replay",
            invocation_id=INVOCATION_ID,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        authorization._consumed = True
        self.assertIn(
            "Z_REPLAY",
            {
                issue.code
                for issue in validate_authorization(
                    authorization,
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    repository_identity=str(ROOT),
                    worktree_identity=str(ROOT),
                    branch="codex/test",
                    expected_head=HEAD,
                    subject_digest=self.question_digest,
                    scope_paths=self.task["scope_paths"],
                    effect="commit",
                    operation_nonce="operation-replay",
                    invocation_id=INVOCATION_ID,
                    now_monotonic=100.0,
                )
            },
        )

    def test_clarification_issue_supplies_question_and_options_deterministically(
        self,
    ) -> None:
        issue = self.framed_issue()
        request = self.validated_request(invocation_id="invocation-options")

        self.assertEqual(
            request.payload["option_ids"], issue.payload["option_ids"]
        )
        self.assertEqual(
            request.payload["recommended_option_id"],
            issue.payload["recommended_option_id"],
        )

    def test_serialized_issue_cannot_self_attest_user_or_policy_provenance(
        self,
    ) -> None:
        from control_plane.clarification import (
            validate_clarification_issue_draft,
        )

        issue = copy.deepcopy(self.issue_draft)
        issue["provenance"] = "user_explicit"
        self.assertIn(
            "C_ISSUE_SCHEMA",
            {
                item.code
                for item in validate_clarification_issue_draft(issue)
            },
        )

    def test_host_wrapped_resolution_validates_exact_bindings(self) -> None:
        from control_plane.clarification import (
            validate_clarification_resolution,
        )

        request = self.validated_request()
        payload = {
            "schema_version": 1,
            "resolution_id": "resolution-host",
            "request_digest": request.request_digest,
            "task_digest": self.task_digest,
            "session_id": SESSION_ID,
            "selected_option_id": "preserve-current",
            "response_digest": "sha256:" + ("3" * 64),
        }
        interaction = object.__new__(self.bridge.TrustedInteraction)
        interaction._consumed = False
        interaction.payload = copy.deepcopy(payload)
        interaction.request_digest = request.request_digest
        interaction.task_digest = self.task_digest
        interaction.session_id = SESSION_ID
        interaction.invocation_id = INVOCATION_ID
        interaction.freshness_deadline = 130.0
        self.bridge._register_runtime_host_object(
            interaction, "trusted_interaction"
        )
        self.assertEqual(
            validate_clarification_resolution(
                payload,
                request=request.payload,
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                trusted_interaction=interaction,
            ),
            [],
        )

    def test_medium_assumption_is_serializable_but_cannot_resolve_high_or_authorize(
        self,
    ) -> None:
        from control_plane.clarification import (
            evaluate_clarification_gate,
            validate_assumption_record,
        )

        medium = copy.deepcopy(self.task)
        medium["risk"]["uncertainty"] = 1
        medium_digest = self.bridge.contract_digest(medium)
        medium_issue = copy.deepcopy(self.issue_draft)
        medium_issue["severity"] = "medium"
        request = self.validated_request(
            task=medium,
            issue_draft=medium_issue,
            repository_status="unresolved",
            invocation_id="invocation-medium-assumption",
        )
        payload = {
            "schema_version": 1,
            "request_digest": request.request_digest,
            "task_digest": medium_digest,
            "selected_option_id": "preserve-current",
            "statement_digest": "sha256:" + ("4" * 64),
        }
        assumption = object.__new__(self.bridge.ValidatedAssumption)
        assumption.payload = copy.deepcopy(payload)
        assumption.provenance = "model_inference"
        self.bridge._register_runtime_host_object(
            assumption, "validated_assumption"
        )
        self.assertEqual(
            validate_assumption_record(
                assumption,
                request=request.payload,
                task_digest=medium_digest,
            ),
            [],
        )
        self.assertEqual(
            evaluate_clarification_gate(
                medium,
                request=request,
                assumption=assumption,
                resolution=None,
                irreversible_confirmation=None,
                authorization=None,
            )["status"],
            "resolved",
        )
        self.assertNotEqual(
            evaluate_clarification_gate(
                self.task,
                request=request,
                assumption=assumption,
                resolution=None,
                irreversible_confirmation=None,
                authorization=None,
            )["status"],
            "resolved",
        )
        decision_issue = copy.deepcopy(medium_issue)
        decision_issue["issue_kind"] = "decision_approval"
        decision_request = self.validated_request(
            task=medium,
            issue_draft=decision_issue,
            use_not_checked=True,
            invocation_id="invocation-medium-decision",
        )
        decision_assumption = object.__new__(
            self.bridge.ValidatedAssumption
        )
        decision_assumption.payload = {
            **payload,
            "request_digest": decision_request.request_digest,
        }
        decision_assumption.provenance = "model_inference"
        self.bridge._register_runtime_host_object(
            decision_assumption, "validated_assumption"
        )
        self.assertEqual(
            evaluate_clarification_gate(
                medium,
                request=decision_request,
                assumption=decision_assumption,
                resolution=None,
                irreversible_confirmation=None,
                authorization=None,
            )["status"],
            "ask_user",
        )

    def test_serialized_assumption_cannot_self_attest_model_or_policy(
        self,
    ) -> None:
        from control_plane.clarification import validate_assumption_record

        request = self.validated_request()
        self.assertIn(
            "A_UNTRUSTED_CHANNEL",
            {
                issue.code
                for issue in validate_assumption_record(
                    {
                        "schema_version": 1,
                        "request_digest": request.request_digest,
                        "task_digest": self.task_digest,
                        "selected_option_id": "preserve-current",
                        "statement_digest": "sha256:" + ("4" * 64),
                        "provenance": "model_inference",
                    },
                    request=request.payload,
                    task_digest=self.task_digest,
                )
            },
        )

    def confirmation_fixture(self):
        capability = self.capability(
            invocation_id="invocation-confirmation"
        )
        consequence_digest = "sha256:" + ("5" * 64)
        request = self.validated_request(
            invocation_id="invocation-confirmation-request"
        )
        subject_digest = request.request_digest
        event = native_user_interaction_event(
            event_id="confirmation-event",
            session_id=SESSION_ID,
            invocation_id="invocation-confirmation",
            task_digest=self.task_digest,
            subject_digest=subject_digest,
            observed_at_monotonic=100.0,
        )
        confirmation_request = {
            "schema_version": 1,
            "confirmation_id": "confirmation-one",
            "request_digest": request.request_digest,
            "task_digest": self.task_digest,
            "session_id": SESSION_ID,
            "scope_paths": self.task["scope_paths"],
            "effect": "destructive",
            "consequence_digest": consequence_digest,
        }
        confirmation = self.bridge.frame_irreversible_confirmation(
            confirmation_request,
            native_user_event=event,
            host_capability=capability,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            expected_head=HEAD,
            subject_digest=subject_digest,
            authorization_id="authorization-confirmation",
            operation_nonce="operation-confirmation",
            invocation_id="invocation-confirmation",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        return request, confirmation_request, confirmation

    def test_irreversible_confirmation_does_not_authorize(self) -> None:
        from control_plane.clarification import evaluate_clarification_gate

        task = copy.deepcopy(self.task)
        task["risk"]["uncertainty"] = 0
        task["risk"]["irreversibility"] = 3
        task["effects"] = [
            {"name": "destructive", "source": "user_explicit"}
        ]
        request, _, confirmation = self.confirmation_fixture()
        gate = evaluate_clarification_gate(
            task,
            request=request,
            assumption=None,
            resolution=None,
            irreversible_confirmation=confirmation,
            authorization=None,
        )
        self.assertEqual(gate["status"], "authorization_required")

    def test_irreversible_confirmation_binds_request_and_consequence(
        self,
    ) -> None:
        from control_plane.clarification import (
            validate_irreversible_confirmation,
        )

        request, confirmation_request, confirmation = self.confirmation_fixture()
        codes = {
            issue.code
            for issue in validate_irreversible_confirmation(
                confirmation,
                request_digest=request.request_digest,
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                repository_identity=str(ROOT),
                worktree_identity=str(ROOT),
                branch="codex/test",
                expected_head=HEAD,
                subject_digest=request.request_digest,
                scope_paths=self.task["scope_paths"],
                effect="destructive",
                expected_consequence_digest="sha256:" + ("9" * 64),
                authorization_id="authorization-confirmation",
                operation_nonce="operation-confirmation",
                invocation_id="invocation-confirmation",
                now_monotonic=100.0,
            )
        }
        self.assertIn("I_CONSEQUENCE_DIGEST", codes)
        self.assertNotEqual(
            confirmation_request["consequence_digest"],
            "sha256:" + ("9" * 64),
        )
        mutated_consequence = "sha256:" + ("6" * 64)
        confirmation.payload["consequence_digest"] = mutated_consequence
        confirmation.payload_digest = self.bridge.contract_digest(
            confirmation.payload
        )
        self.assertIn(
            "I_UNTRUSTED_CHANNEL",
            {
                issue.code
                for issue in validate_irreversible_confirmation(
                    confirmation,
                    request_digest=request.request_digest,
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    repository_identity=str(ROOT),
                    worktree_identity=str(ROOT),
                    branch="codex/test",
                    expected_head=HEAD,
                    subject_digest=request.request_digest,
                    scope_paths=self.task["scope_paths"],
                    effect="destructive",
                    expected_consequence_digest=mutated_consequence,
                    authorization_id="authorization-confirmation",
                    operation_nonce="operation-confirmation",
                    invocation_id="invocation-confirmation",
                    now_monotonic=100.0,
                )
            },
        )

    def test_irreversible_confirmation_is_one_shot_and_operation_bound(
        self,
    ) -> None:
        from control_plane.clarification import (
            validate_irreversible_confirmation,
        )

        request, _, confirmation = self.confirmation_fixture()
        confirmation._consumed = True
        codes = {
            issue.code
            for issue in validate_irreversible_confirmation(
                confirmation,
                request_digest=request.request_digest,
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                repository_identity=str(ROOT),
                worktree_identity=str(ROOT),
                branch="codex/test",
                expected_head=HEAD,
                subject_digest=request.request_digest,
                scope_paths=self.task["scope_paths"],
                effect="destructive",
                expected_consequence_digest="sha256:" + ("5" * 64),
                authorization_id="authorization-confirmation",
                operation_nonce="operation-confirmation",
                invocation_id="invocation-confirmation",
                now_monotonic=100.0,
            )
        }
        self.assertIn("I_REPLAY", codes)

    def test_trusted_authorization_does_not_confirm_irreversibility(
        self,
    ) -> None:
        from control_plane.clarification import evaluate_clarification_gate

        task = copy.deepcopy(self.task)
        task["risk"]["uncertainty"] = 0
        task["risk"]["irreversibility"] = 3
        task["effects"] = [
            {"name": "destructive", "source": "user_explicit"}
        ]
        task_digest = self.bridge.contract_digest(task)
        capability = self.capability(
            invocation_id="invocation-destructive-authorization"
        )
        event = native_user_interaction_event(
            event_id="destructive-authorization-event",
            session_id=SESSION_ID,
            invocation_id="invocation-destructive-authorization",
            task_digest=task_digest,
            subject_digest=task_digest,
            observed_at_monotonic=100.0,
        )
        authorization = self.bridge.frame_effect_authorization(
            event,
            host_capability=capability,
            task_digest=task_digest,
            session_id=SESSION_ID,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            expected_head=HEAD,
            subject_digest=task_digest,
            scope_paths=tuple(task["scope_paths"]),
            effect="destructive",
            operation_nonce="operation-destructive",
            invocation_id="invocation-destructive-authorization",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        gate = evaluate_clarification_gate(
            task,
            request=None,
            assumption=None,
            resolution=None,
            irreversible_confirmation=None,
            authorization=authorization,
        )
        self.assertEqual(gate["status"], "confirmation_required")

        foreign_capability = self.capability(
            invocation_id="invocation-foreign-authorization"
        )
        foreign_event = native_user_interaction_event(
            event_id="foreign-authorization-event",
            session_id=SESSION_ID,
            invocation_id="invocation-foreign-authorization",
            task_digest=self.task_digest,
            subject_digest=self.task_digest,
            observed_at_monotonic=100.0,
        )
        foreign_authorization = self.bridge.frame_effect_authorization(
            foreign_event,
            host_capability=foreign_capability,
            task_digest=self.task_digest,
            session_id=SESSION_ID,
            repository_identity=str(ROOT),
            worktree_identity=str(ROOT),
            branch="codex/test",
            expected_head=HEAD,
            subject_digest=self.task_digest,
            scope_paths=tuple(self.task["scope_paths"]),
            effect="destructive",
            operation_nonce="operation-foreign",
            invocation_id="invocation-foreign-authorization",
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        self.assertEqual(
            evaluate_clarification_gate(
                task,
                request=None,
                assumption=None,
                resolution=None,
                irreversible_confirmation=None,
                authorization=foreign_authorization,
            )["status"],
            "authorization_required",
        )

    def test_serialized_confirmation_cannot_self_attest_trusted_host(
        self,
    ) -> None:
        from control_plane.clarification import (
            validate_irreversible_confirmation,
        )

        request, payload, _ = self.confirmation_fixture()
        self.assertIn(
            "I_UNTRUSTED_CHANNEL",
            {
                issue.code
                for issue in validate_irreversible_confirmation(
                    payload,
                    request_digest=request.request_digest,
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    repository_identity=str(ROOT),
                    worktree_identity=str(ROOT),
                    branch="codex/test",
                    expected_head=HEAD,
                    subject_digest=request.request_digest,
                    scope_paths=self.task["scope_paths"],
                    effect="destructive",
                    expected_consequence_digest="sha256:" + ("5" * 64),
                    authorization_id="authorization-confirmation",
                    operation_nonce="operation-confirmation",
                    invocation_id="invocation-confirmation",
                    now_monotonic=100.0,
                )
            },
        )

    def test_unknown_fields_options_scopes_and_digests_fail_closed(
        self,
    ) -> None:
        from control_plane.clarification import (
            validate_clarification_issue_draft,
            validate_clarification_request,
        )

        issue = copy.deepcopy(self.issue_draft)
        issue["option_ids"] = ["duplicate", "duplicate"]
        issue["question_digest"] = "not-a-digest"
        request = self.validated_request().payload
        request["scope_paths"] = ["../outside"]
        self.assertTrue(validate_clarification_issue_draft(issue))
        self.assertIn(
            "C_SCHEMA",
            {item.code for item in validate_clarification_request(request)},
        )

    def test_complete_context_metrics_require_every_declared_source(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            host_metrics = self.bridge.frame_host_context_metrics(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id=INVOCATION_ID,
                subject_digest=self.question_digest,
                required_resource_bytes=120,
                recommended_resource_bytes=80,
                worker_id="worker-main",
                retry_count=1,
                started_at_monotonic=100.0,
                ended_at_monotonic=104.2,
                tool_use_id="tool-use-one",
                host_capability=self.capability(),
            )
            first = store.record_context_metrics(
                "task-clarification-tests",
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id=INVOCATION_ID,
                subject_digest=self.question_digest,
                runtime_metrics={
                    "router_manifest_bytes": 200,
                    "novice_brief_bytes": 100,
                    "hook_output_bytes": 20,
                    "context_units_selected": 4,
                    "tool_use_id": "tool-use-one",
                },
                host_metrics=host_metrics,
            )
            second = store.record_context_metrics(
                "task-clarification-tests",
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id=INVOCATION_ID,
                subject_digest=self.question_digest,
                runtime_metrics={
                    "router_manifest_bytes": 200,
                    "novice_brief_bytes": 100,
                    "hook_output_bytes": 20,
                    "context_units_selected": 4,
                    "tool_use_id": "tool-use-one",
                },
                host_metrics=host_metrics,
            )
            summary = store.context_metrics("task-clarification-tests")
        self.assertEqual(first, second)
        self.assertEqual(summary["metrics_status"], "complete")
        self.assertEqual(summary["required_resource_bytes_total"], 120)
        self.assertEqual(summary["worker_time_ms_total"], 4200)

    def test_missing_host_context_metrics_are_partial_and_null(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            store.record_context_metrics(
                "task-clarification-tests",
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id=INVOCATION_ID,
                subject_digest=self.question_digest,
                runtime_metrics={"router_manifest_bytes": 200},
                host_metrics=None,
            )
            summary = store.context_metrics("task-clarification-tests")
        self.assertEqual(summary["metrics_status"], "partial")
        self.assertIsNone(summary["required_resource_bytes_total"])
        self.assertIsNone(summary["workers_unique"])
        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            complete_metrics = self.bridge.frame_host_context_metrics(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id="invocation-covered",
                subject_digest=self.question_digest,
                required_resource_bytes=120,
                recommended_resource_bytes=80,
                worker_id="worker-covered",
                retry_count=0,
                started_at_monotonic=100.0,
                ended_at_monotonic=101.0,
                tool_use_id=None,
                host_capability=self.capability(
                    invocation_id="invocation-covered"
                ),
            )
            store.record_context_metrics(
                "task-clarification-tests",
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id="invocation-covered",
                subject_digest=self.question_digest,
                runtime_metrics={"router_manifest_bytes": 200},
                host_metrics=complete_metrics,
            )
            mixed = store.record_context_metrics(
                "task-clarification-tests",
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id="invocation-uncovered",
                subject_digest=self.question_digest,
                runtime_metrics={"router_manifest_bytes": 100},
                host_metrics=None,
            )
        self.assertEqual(mixed["metrics_status"], "partial")
        self.assertIsNone(mixed["required_resource_bytes_total"])

    def test_context_metrics_replay_is_idempotent_and_conflict_fails_closed(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            arguments = dict(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id=INVOCATION_ID,
                subject_digest=self.question_digest,
                runtime_metrics={"router_manifest_bytes": 200},
                host_metrics=None,
            )
            first = store.record_context_metrics(
                "task-clarification-tests", **arguments
            )
            second = store.record_context_metrics(
                "task-clarification-tests", **arguments
            )
            self.assertEqual(first, second)
            additional = {
                **arguments,
                "runtime_metrics": {"novice_brief_bytes": 50},
            }
            separated = store.record_context_metrics(
                "task-clarification-tests", **additional
            )
            self.assertEqual(
                separated["router_manifest_bytes_total"], 200
            )
            self.assertEqual(
                separated["novice_brief_bytes_total"], 50
            )
            arguments["runtime_metrics"] = {"router_manifest_bytes": 201}
            with self.assertRaisesRegex(
                ValueError, "M_METRIC_REPLAY_CONFLICT"
            ):
                store.record_context_metrics(
                    "task-clarification-tests", **arguments
                )

    def test_host_context_metrics_require_exact_task_session_invocation_identity(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            with self.assertRaisesRegex(
                ValueError, "M_METRIC_UNTRUSTED_CHANNEL"
            ):
                store.record_context_metrics(
                    "task-clarification-tests",
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id=INVOCATION_ID,
                    subject_digest=self.question_digest,
                    runtime_metrics={},
                    host_metrics={
                        "required_resource_bytes": 120,
                        "worker_id": "forged",
                    },
                )
            with self.assertRaisesRegex(
                ValueError, "M_METRIC_BINDING"
            ):
                store.record_context_metrics(
                    "task-clarification-tests",
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id=INVOCATION_ID,
                    subject_digest=self.question_digest,
                    runtime_metrics={"worker_id": "forged"},
                    host_metrics=None,
                )
            with self.assertRaisesRegex(
                ValueError, "M_METRIC_BINDING"
            ):
                self.bridge.frame_host_context_metrics(
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id="invocation-non-finite",
                    subject_digest=self.question_digest,
                    required_resource_bytes=120,
                    recommended_resource_bytes=80,
                    worker_id="worker-main",
                    retry_count=0,
                    started_at_monotonic=float("nan"),
                    ended_at_monotonic=101.0,
                    tool_use_id=None,
                    host_capability=self.capability(
                        invocation_id="invocation-non-finite"
                    ),
                )
            tampered = self.bridge.frame_host_context_metrics(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id="invocation-metrics-tamper",
                subject_digest=self.question_digest,
                required_resource_bytes=120,
                recommended_resource_bytes=80,
                worker_id="worker-main",
                retry_count=0,
                started_at_monotonic=100.0,
                ended_at_monotonic=101.0,
                tool_use_id=None,
                host_capability=self.capability(
                    invocation_id="invocation-metrics-tamper"
                ),
            )
            tampered.payload["required_resource_bytes"] = 121
            tampered.payload_digest = self.bridge.contract_digest(
                tampered.payload
            )
            with self.assertRaisesRegex(
                ValueError, "M_METRIC_UNTRUSTED_CHANNEL"
            ):
                store.record_context_metrics(
                    "task-clarification-tests",
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id="invocation-metrics-tamper",
                    subject_digest=self.question_digest,
                    runtime_metrics={},
                    host_metrics=tampered,
                )

    def test_context_metrics_concurrent_hooks_are_lossless_and_order_independent(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))

            def record(index: int) -> dict:
                invocation_id = f"invocation-hook-{index}"
                metrics = self.bridge.frame_host_context_metrics(
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id=invocation_id,
                    subject_digest=self.question_digest,
                    required_resource_bytes=10 + index,
                    recommended_resource_bytes=20 + index,
                    worker_id=f"worker-{index}",
                    retry_count=index,
                    started_at_monotonic=100.0 + index,
                    ended_at_monotonic=102.0 + index,
                    tool_use_id=f"tool-use-{index}",
                    host_capability=self.capability(
                        invocation_id=invocation_id
                    ),
                )
                return store.record_context_metrics(
                    "task-clarification-tests",
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id=invocation_id,
                    subject_digest=self.question_digest,
                    runtime_metrics={
                        "hook_output_bytes": 100 + index,
                        "tool_use_id": f"tool-use-{index}",
                    },
                    host_metrics=metrics,
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(record, (0, 1)))
            summary = store.context_metrics("task-clarification-tests")
        self.assertEqual(summary["invocation_count_unique"], 2)
        self.assertEqual(summary["hook_invocation_count_unique"], 2)
        self.assertEqual(summary["hook_output_bytes_total"], 201)
        self.assertEqual(summary["required_resource_bytes_total"], 21)
        self.assertEqual(summary["workers_unique"], 2)
        self.assertEqual(summary["retry_count_total"], 1)
        self.assertEqual(summary["worker_time_ms_total"], 4000)
        self.assertEqual(summary["task_elapsed_ms"], 3000)

    def test_context_metrics_aggregate_once_per_invocation_across_tools(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            for tool_use_id in ("tool-use-a", "tool-use-b"):
                host_metrics = self.bridge.frame_host_context_metrics(
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id="invocation-shared",
                    subject_digest=self.question_digest,
                    required_resource_bytes=10,
                    recommended_resource_bytes=20,
                    worker_id="worker-shared",
                    retry_count=1,
                    started_at_monotonic=100.0,
                    ended_at_monotonic=101.0,
                    tool_use_id=tool_use_id,
                    host_capability=self.capability(
                        invocation_id="invocation-shared"
                    ),
                )
                summary = store.record_context_metrics(
                    "task-clarification-tests",
                    task_digest=self.task_digest,
                    session_id=SESSION_ID,
                    invocation_id="invocation-shared",
                    subject_digest=self.question_digest,
                    runtime_metrics={
                        "hook_output_bytes": 10,
                        "context_units_selected": 4,
                        "tool_use_id": tool_use_id,
                    },
                    host_metrics=host_metrics,
                )
        self.assertEqual(summary["metrics_status"], "complete")
        self.assertEqual(summary["invocation_count_unique"], 1)
        self.assertEqual(summary["hook_invocation_count_unique"], 2)
        self.assertEqual(summary["context_units_selected_total"], 4)
        self.assertEqual(summary["required_resource_bytes_total"], 20)
        self.assertEqual(summary["recommended_resource_bytes_total"], 40)
        self.assertEqual(summary["retry_count_total"], 1)
        self.assertEqual(summary["worker_time_ms_total"], 1000)

    def test_host_context_metrics_recover_after_partial_runtime_publication(
        self,
    ) -> None:
        import control_plane.lifecycle as lifecycle

        with tempfile.TemporaryDirectory() as temporary:
            store = lifecycle.TaskStore(Path(temporary))
            host_metrics = self.bridge.frame_host_context_metrics(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id="invocation-metric-recovery",
                subject_digest=self.question_digest,
                required_resource_bytes=10,
                recommended_resource_bytes=20,
                worker_id="worker-recovery",
                retry_count=0,
                started_at_monotonic=100.0,
                ended_at_monotonic=101.0,
                tool_use_id=None,
                host_capability=self.capability(
                    invocation_id="invocation-metric-recovery"
                ),
            )
            arguments = dict(
                task_digest=self.task_digest,
                session_id=SESSION_ID,
                invocation_id="invocation-metric-recovery",
                subject_digest=self.question_digest,
                runtime_metrics={"router_manifest_bytes": 100},
                host_metrics=host_metrics,
            )
            original_atomic_json = lifecycle._atomic_json
            calls = 0

            def fail_second_write(path: Path, value: dict) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected metric publication failure")
                original_atomic_json(path, value)

            with patch(
                "control_plane.lifecycle._atomic_json",
                side_effect=fail_second_write,
            ):
                with self.assertRaisesRegex(
                    OSError, "injected metric publication failure"
                ):
                    store.record_context_metrics(
                        "task-clarification-tests", **arguments
                    )
            self.assertFalse(host_metrics._consumed)
            recovered = store.record_context_metrics(
                "task-clarification-tests", **arguments
            )
            host_records = list(
                (
                    Path(temporary)
                    / "codex-control-plane"
                    / "metrics"
                    / "task-clarification-tests"
                ).glob("host-*.json")
            )
            self.assertEqual(len(host_records), 1)
            host_batch = json.loads(
                host_records[0].read_text(encoding="utf-8")
            )
            self.assertEqual(host_batch["metric"], "host_metric_batch")
            self.assertEqual(
                {row["metric"] for row in host_batch["value"]["rows"]},
                {
                    "required_resource_bytes",
                    "recommended_resource_bytes",
                    "worker_id",
                    "retry_count",
                    "worker_interval",
                },
            )
            self.assertTrue(
                all("row_digest" in row for row in host_batch["value"]["rows"])
            )
        self.assertEqual(recovered["metrics_status"], "complete")
        self.assertEqual(recovered["required_resource_bytes_total"], 10)

    def test_material_ambiguity_enters_clarification_required_from_active_states(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        for source in (
            "framed",
            "planned",
            "ready",
            "implementing",
            "verifying",
            "review_ready",
        ):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as temporary:
                store = TaskStore(Path(temporary))
                request, state = self.require_flow(
                    store,
                    task_id=f"TASK-CLARIFY-{source.upper()}",
                    source=source,
                    invocation_id=f"invocation-require-{source}",
                )
                self.assertEqual(state["state"], "clarification_required")
                self.assertEqual(
                    state["clarification_resume_state"], source
                )
                self.assertEqual(
                    state["clarification_request_digest"],
                    request.request_digest,
                )
                for key in (
                    "clarification_context_digest",
                    "clarification_question_digest",
                    "clarification_repository_evidence_digest",
                    "clarification_task_digest",
                    "clarification_decision_digest",
                    "clarification_prompt_view_path",
                ):
                    self.assertIn(key, state)

    def test_lateral_state_preserves_route_blocked_effects(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            task_id = "TASK-BLOCKED-EFFECTS"
            prior = self.start_task_at(store, task_id, "planned")
            invocation_id = "invocation-blocked-effects"
            request = self.validated_request(invocation_id=invocation_id)
            context = self.trusted_route_context(
                decision_digest=prior["decision_digest"],
                invocation_id=invocation_id,
            )

            self.assertEqual(context.blocked_effects, ("local_write",))
            state = store.require_clarification(
                task_id,
                request=request,
                route_context=context,
                expected_generation=prior["generation"],
                current_branch="codex/test",
                task_digest=self.task_digest,
                decision_digest=prior["decision_digest"],
            )
            self.assertEqual(
                state["clarification_blocked_effects"],
                ["local_write"],
            )

    def test_host_bridge_is_the_only_entry_to_clarification_required(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            task_id = "TASK-HOST-ONLY-CLARIFICATION"
            decision_digest = contract_digest({"decision": "host-only"})
            prior_state = self.start_task_at(
                store,
                task_id,
                "planned",
                decision_digest=decision_digest,
            )
            request = self.validated_request(
                invocation_id="invocation-host-only"
            )
            context = self.trusted_route_context(
                decision_digest=decision_digest,
                invocation_id="invocation-host-only",
            )

            for untrusted_request, untrusted_context in (
                (request.payload, context),
                (request, {"route_digest": decision_digest}),
            ):
                with self.subTest(
                    request_type=type(untrusted_request).__name__,
                    context_type=type(untrusted_context).__name__,
                ), self.assertRaisesRegex(
                    (TypeError, ValueError),
                    "C_UNTRUSTED_REQUEST|C_ROUTE_CONTEXT_UNTRUSTED",
                ):
                    store.require_clarification(
                        task_id,
                        request=untrusted_request,
                        route_context=untrusted_context,
                        expected_generation=prior_state["generation"],
                        current_branch="codex/test",
                        task_digest=self.task_digest,
                        decision_digest=decision_digest,
                    )

            state = store.require_clarification(
                task_id,
                request=request,
                route_context=context,
                expected_generation=prior_state["generation"],
                current_branch="codex/test",
                task_digest=self.task_digest,
                decision_digest=decision_digest,
            )
            self.assertEqual(state["state"], "clarification_required")

    def test_host_bridge_records_resolution_and_resumes_in_same_process(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-RESOLVE-SAME-PROCESS",
                source="ready",
                invocation_id="invocation-require-same-process",
            )
            invocation_id = "invocation-resolve-same-process"
            route_context = self.trusted_route_context(
                decision_digest=required[
                    "clarification_decision_digest"
                ],
                invocation_id=invocation_id,
                clarification_status="resolved",
            )
            repository_context = self.repository_context(
                invocation_id=f"{invocation_id}-repository"
            )
            resolution_arguments = {
                "route_context": route_context,
                "repository_context": repository_context,
                "expected_generation": required["generation"],
                "current_branch": "codex/test",
                "expected_head": HEAD,
                "task_digest": self.task_digest,
                "decision_digest": required[
                    "clarification_decision_digest"
                ],
                "context_digest": (
                    self.bridge.clarification_route_context_digest(
                        route_context
                    )
                ),
                "question_digest": self.question_digest,
            }
            with self.assertRaisesRegex(
                (TypeError, ValueError), "C_UNTRUSTED_CHANNEL"
            ):
                store.resolve_and_resume_clarification(
                    "TASK-RESOLVE-SAME-PROCESS",
                    interaction={
                        "selected_option_id": "preserve-current"
                    },
                    **resolution_arguments,
                )
            interaction = self.trusted_interaction(
                request,
                invocation_id=invocation_id,
            )
            resolved = store.resolve_and_resume_clarification(
                "TASK-RESOLVE-SAME-PROCESS",
                interaction=interaction,
                **resolution_arguments,
            )

            self.assertEqual(resolved["state"], "ready")
            self.assertNotIn("clarification_request", resolved)
            self.assertIn("clarification_resolution_digest", resolved)

    def _assert_router_resolved_context_can_publish_clarification_resolution(
        self,
        *,
        mode: str,
    ) -> None:
        from control_plane.lifecycle import TaskStore
        from control_plane.policy import load_policy
        from control_plane.resource_registry import load_registry
        from control_plane.routing import resolve_route
        from tests.router_test_support import (
            VALID_POLICY,
            VALID_REGISTRY,
            inventory_snapshot,
            validated_inventory,
        )

        policy = load_policy(VALID_POLICY)
        registry = load_registry(VALID_REGISTRY)
        snapshot = inventory_snapshot()
        entry_invocation = "invocation-router-lifecycle-entry"
        resolution_invocation = "invocation-router-lifecycle-resolution"
        request = self.validated_request(invocation_id=entry_invocation)
        asking_decision = resolve_route(
            self.task,
            policy,
            registry,
            validated_inventory(
                snapshot,
                registry=registry,
                task=self.task,
                invocation_id=entry_invocation,
            ),
            mode=mode,
            host_capability=self.bridge.HOST_ADAPTER_UNAVAILABLE,
            clarification_request=request,
        )
        self.assertEqual(
            asking_decision["interaction"]["clarification_gate"]["status"],
            "ask_user",
        )
        self.assertTrue(asking_decision["authorization"]["local_write"])
        self.assertIn(
            "local_write",
            asking_decision["interaction"]["clarification_gate"][
                "blocked_effects"
            ],
        )
        asking_context = self.bridge.build_trusted_route_context(
            task=self.task,
            decision=asking_decision,
            inventory=validated_inventory(
                snapshot,
                registry=registry,
                task=self.task,
                invocation_id=entry_invocation,
            ),
            expected_repository=ROOT,
            expected_worktree=ROOT,
            expected_branch="codex/test",
            expected_head=HEAD,
            session_id=SESSION_ID,
            invocation_id=entry_invocation,
            host_capability=self.capability(
                invocation_id=entry_invocation
            ),
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            task_id = f"TASK-ROUTER-LIFECYCLE-{mode.upper()}"
            prior = self.start_task_at(
                store,
                task_id,
                "ready",
                decision_digest=asking_decision.decision_digest,
            )
            required = store.require_clarification(
                task_id,
                request=request,
                route_context=asking_context,
                expected_generation=prior["generation"],
                current_branch="codex/test",
                task_digest=self.task_digest,
                decision_digest=asking_decision.decision_digest,
            )
            interaction = self.trusted_interaction(
                request,
                invocation_id=resolution_invocation,
            )
            resolved_decision = resolve_route(
                self.task,
                policy,
                registry,
                validated_inventory(
                    snapshot,
                    registry=registry,
                    task=self.task,
                    invocation_id=resolution_invocation,
                ),
                mode=mode,
                host_capability=self.bridge.HOST_ADAPTER_UNAVAILABLE,
                clarification_request=request,
                clarification_resolution=interaction,
            )
            self.assertEqual(
                resolved_decision["interaction"]["clarification_gate"][
                    "status"
                ],
                "resolved",
            )
            self.assertNotEqual(
                asking_decision.decision_digest,
                resolved_decision.decision_digest,
            )
            resolved_context = self.bridge.build_trusted_route_context(
                task=self.task,
                decision=resolved_decision,
                inventory=validated_inventory(
                    snapshot,
                    registry=registry,
                    task=self.task,
                    invocation_id=resolution_invocation,
                ),
                expected_repository=ROOT,
                expected_worktree=ROOT,
                expected_branch="codex/test",
                expected_head=HEAD,
                session_id=SESSION_ID,
                invocation_id=resolution_invocation,
                host_capability=self.capability(
                    invocation_id=resolution_invocation
                ),
                clock=lambda: 100.0,
                ttl_seconds=30,
            )
            self.assertEqual(
                asking_context.route_material_digest,
                resolved_context.route_material_digest,
            )
            repository = self.repository_context(
                invocation_id=f"{resolution_invocation}-repository"
            )

            resolved = store.resolve_and_resume_clarification(
                task_id,
                interaction=interaction,
                route_context=resolved_context,
                repository_context=repository,
                expected_generation=required["generation"],
                current_branch="codex/test",
                expected_head=HEAD,
                task_digest=self.task_digest,
                decision_digest=resolved_decision.decision_digest,
                context_digest=(
                    self.bridge.clarification_route_context_digest(
                        resolved_context
                    )
                ),
                question_digest=self.question_digest,
            )

            self.assertEqual(resolved["state"], "ready")
            self.assertEqual(
                resolved["decision_digest"],
                resolved_decision.decision_digest,
            )

    def test_router_resolved_context_can_publish_clarification_resolution(
        self,
    ) -> None:
        self._assert_router_resolved_context_can_publish_clarification_resolution(
            mode="audit"
        )

    def test_enforce_router_resolved_context_can_publish_resolution(
        self,
    ) -> None:
        self._assert_router_resolved_context_can_publish_clarification_resolution(
            mode="enforce"
        )

    def test_material_route_drift_still_reframes_after_resolution(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-MATERIAL-ROUTE-DRIFT",
                source="ready",
                invocation_id="invocation-material-route-entry",
            )
            invocation_id = "invocation-material-route-resolution"
            new_decision_digest = contract_digest(
                {"decision": "material-route-change"}
            )
            route_context = self.trusted_route_context(
                decision_digest=new_decision_digest,
                invocation_id=invocation_id,
                clarification_status="resolved",
                route_material_digest=contract_digest(
                    {"material_route": "changed"}
                ),
            )
            interaction = self.trusted_interaction(
                request,
                invocation_id=invocation_id,
            )
            repository = self.repository_context(
                invocation_id=f"{invocation_id}-repository"
            )

            reframed = store.resolve_and_resume_clarification(
                "TASK-MATERIAL-ROUTE-DRIFT",
                interaction=interaction,
                route_context=route_context,
                repository_context=repository,
                expected_generation=required["generation"],
                current_branch="codex/test",
                expected_head=HEAD,
                task_digest=self.task_digest,
                decision_digest=new_decision_digest,
                context_digest=(
                    self.bridge.clarification_route_context_digest(
                        route_context
                    )
                ),
                question_digest=self.question_digest,
            )

            self.assertEqual(reframed["state"], "planned")
            self.assertEqual(
                reframed["decision_digest"], new_decision_digest
            )

    def test_trusted_route_context_is_fresh_one_shot_and_route_bound(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            task_id = "TASK-ROUTE-CONTEXT-ONCE"
            decision_digest = contract_digest({"decision": "route-once"})
            prior_state = self.start_task_at(
                store,
                task_id,
                "planned",
                decision_digest=decision_digest,
            )
            request = self.validated_request(
                invocation_id="invocation-route-once"
            )
            context = self.trusted_route_context(
                decision_digest=decision_digest,
                invocation_id="invocation-route-once",
            )
            store.require_clarification(
                task_id,
                request=request,
                route_context=context,
                expected_generation=prior_state["generation"],
                current_branch="codex/test",
                task_digest=self.task_digest,
                decision_digest=decision_digest,
            )
            with self.assertRaisesRegex(
                ValueError, "C_ROUTE_CONTEXT_UNTRUSTED"
            ):
                self.bridge.clarification_route_context_digest(context)

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            task_id = "TASK-ROUTE-CONTEXT-STALE"
            prior_state = self.start_task_at(
                store,
                task_id,
                "planned",
                decision_digest=decision_digest,
            )
            stale_request = self.validated_request(
                invocation_id="invocation-route-stale"
            )
            stale = self.trusted_route_context(
                decision_digest=decision_digest,
                invocation_id="invocation-route-stale",
                now=100.0,
                ttl_seconds=-1.0,
            )
            with self.assertRaisesRegex(
                ValueError, "C_ROUTE_CONTEXT_UNTRUSTED"
            ):
                store.require_clarification(
                    task_id,
                    request=stale_request,
                    route_context=stale,
                    expected_generation=prior_state["generation"],
                    current_branch="codex/test",
                    task_digest=self.task_digest,
                    decision_digest=decision_digest,
                )

    def test_trusted_interaction_is_native_event_bound_and_one_shot(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-INTERACTION-ONCE",
                invocation_id="invocation-require-interaction",
            )
            with self.assertRaisesRegex(
                (TypeError, ValueError), "C_UNTRUSTED_CHANNEL"
            ):
                self.bridge.frame_trusted_interaction(
                    native_event={
                        "event_id": "serialized-event",
                        "session_id": SESSION_ID,
                    },
                    request=request,
                    selected_option_id="preserve-current",
                    response_digest=self.bridge.contract_digest(
                        {"response": "preserve-current"}
                    ),
                    session_id=SESSION_ID,
                    invocation_id="invocation-serialized-interaction",
                    host_capability=self.capability(
                        invocation_id="invocation-serialized-interaction"
                    ),
                    clock=lambda: 100.0,
                    ttl_seconds=30,
                )

            route_context = self.trusted_route_context(
                decision_digest=required[
                    "clarification_decision_digest"
                ],
                invocation_id="invocation-native-interaction",
                clarification_status="resolved",
            )
            interaction = self.trusted_interaction(
                request,
                invocation_id="invocation-native-interaction",
            )
            repository = self.repository_context(
                invocation_id="invocation-native-interaction-repository"
            )
            arguments = {
                "interaction": interaction,
                "route_context": route_context,
                "repository_context": repository,
                "expected_generation": required["generation"],
                "current_branch": "codex/test",
                "expected_head": HEAD,
                "task_digest": self.task_digest,
                "decision_digest": required[
                    "clarification_decision_digest"
                ],
                "context_digest": (
                    self.bridge.clarification_route_context_digest(
                        route_context
                    )
                ),
                "question_digest": self.question_digest,
            }
            store.resolve_and_resume_clarification(
                "TASK-INTERACTION-ONCE", **arguments
            )
            self.assertTrue(interaction._consumed)
            self.assertFalse(
                self.bridge._runtime_host_object_is_live(
                    interaction, "trusted_interaction"
                )
            )
            with self.assertRaisesRegex(
                ValueError,
                "E_STATE_CAS|C_UNTRUSTED_CHANNEL|C_INTERACTION_REPLAY",
            ):
                store.resolve_and_resume_clarification(
                    "TASK-INTERACTION-ONCE", **arguments
                )

    def test_native_interaction_rejects_request_only_subject_attestation(
        self,
    ) -> None:
        request = self.validated_request(
            invocation_id="invocation-request-only-subject"
        )
        response_digest = self.bridge.contract_digest(
            {"response": "preserve-current"}
        )

        with self.assertRaisesRegex(ValueError, "C_UNTRUSTED_CHANNEL"):
            self.bridge.frame_trusted_interaction(
                native_event=native_user_interaction_event(
                    event_id="event-request-only-subject",
                    session_id=SESSION_ID,
                    invocation_id="invocation-request-only-subject",
                    task_digest=request.task_digest,
                    subject_digest=request.request_digest,
                    observed_at_monotonic=100.0,
                ),
                request=request,
                selected_option_id="preserve-current",
                response_digest=response_digest,
                session_id=SESSION_ID,
                invocation_id="invocation-request-only-subject",
                host_capability=self.capability(
                    invocation_id="invocation-request-only-subject"
                ),
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

    def test_native_interaction_subject_binds_option_response_and_time(
        self,
    ) -> None:
        cases = (
            "option",
            "response",
            "future",
            "expired",
            "event_id",
        )
        for case in cases:
            with self.subTest(case=case):
                invocation_id = f"invocation-interaction-{case}"
                request = self.validated_request(
                    invocation_id=invocation_id
                )
                selected_option_id = "preserve-current"
                response_digest = self.bridge.contract_digest(
                    {"response": selected_option_id}
                )
                subject_digest = (
                    self.bridge.clarification_interaction_subject_digest(
                        request_digest=request.request_digest,
                        task_digest=request.task_digest,
                        session_id=SESSION_ID,
                        invocation_id=invocation_id,
                        selected_option_id=selected_option_id,
                        response_digest=response_digest,
                    )
                )
                supplied_option = (
                    "change-contract"
                    if case == "option"
                    else selected_option_id
                )
                supplied_response = (
                    self.bridge.contract_digest(
                        {"response": "changed-response"}
                    )
                    if case == "response"
                    else response_digest
                )
                observed_at = 101.0 if case == "future" else 100.0
                clock_now = 131.0 if case == "expired" else 100.0
                with self.assertRaisesRegex(
                    ValueError, "C_UNTRUSTED_CHANNEL"
                ):
                    self.bridge.frame_trusted_interaction(
                        native_event=native_user_interaction_event(
                            event_id=(
                                ""
                                if case == "event_id"
                                else f"event-interaction-{case}"
                            ),
                            session_id=SESSION_ID,
                            invocation_id=invocation_id,
                            task_digest=request.task_digest,
                            subject_digest=subject_digest,
                            observed_at_monotonic=observed_at,
                        ),
                        request=request,
                        selected_option_id=supplied_option,
                        response_digest=supplied_response,
                        session_id=SESSION_ID,
                        invocation_id=invocation_id,
                        host_capability=self.capability(
                            invocation_id=invocation_id
                        ),
                        clock=lambda: clock_now,
                        ttl_seconds=30,
                    )

    def test_resolution_requires_task_question_context_and_evidence_digests(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        for field in ("task_digest", "context_digest", "question_digest"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                store = TaskStore(Path(temporary))
                request, required = self.require_flow(
                    store,
                    task_id=f"TASK-RESOLUTION-DIGEST-{field.upper()}",
                    invocation_id=f"invocation-require-digest-{field}",
                )
                wrong = self.bridge.contract_digest(
                    {"wrong": field}
                )
                route_context = self.trusted_route_context(
                    decision_digest=required[
                        "clarification_decision_digest"
                    ],
                    invocation_id=f"invocation-resolve-digest-{field}",
                )
                interaction = self.trusted_interaction(
                    request,
                    invocation_id=f"invocation-resolve-digest-{field}",
                )
                repository = self.repository_context(
                    invocation_id=(
                        f"invocation-resolve-digest-{field}-repository"
                    )
                )
                arguments = {
                    "interaction": interaction,
                    "route_context": route_context,
                    "repository_context": repository,
                    "expected_generation": required["generation"],
                    "current_branch": "codex/test",
                    "expected_head": HEAD,
                    "task_digest": self.task_digest,
                    "decision_digest": required[
                        "clarification_decision_digest"
                    ],
                    "context_digest": (
                        self.bridge.clarification_route_context_digest(
                            route_context
                        )
                    ),
                    "question_digest": self.question_digest,
                }
                arguments[field] = wrong
                with self.assertRaisesRegex(
                    ValueError,
                    "C_(TASK|CONTEXT|QUESTION|ROUTE_CONTEXT|REPOSITORY)",
                ):
                    store.resolve_and_resume_clarification(
                        f"TASK-RESOLUTION-DIGEST-{field.upper()}",
                        **arguments,
                    )
                self.assertEqual(
                    store.status(
                        f"TASK-RESOLUTION-DIGEST-{field.upper()}"
                    )["state"],
                    "clarification_required",
                )

    def test_same_task_digest_resumes_preserved_state(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-SAME-DIGEST",
                source="verifying",
                invocation_id="invocation-require-same-digest",
            )
            resumed = self.resolve_flow(
                store,
                task_id="TASK-SAME-DIGEST",
                request=request,
                required_state=required,
                invocation_id="invocation-resolve-same-digest",
            )
            self.assertEqual(resumed["state"], "verifying")
            self.assertEqual(
                resumed["generation"], required["generation"] + 1
            )

    def test_changed_task_digest_returns_to_planned_and_invalidates_descendants(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-CHANGED-DIGEST",
                source="review_ready",
                invocation_id="invocation-require-changed-digest",
            )
            new_task_digest = contract_digest({"task": "reframed"})
            new_decision_digest = contract_digest(
                {"decision": "reframed"}
            )
            reframed = self.resolve_flow(
                store,
                task_id="TASK-CHANGED-DIGEST",
                request=request,
                required_state=required,
                task_digest=new_task_digest,
                decision_digest=new_decision_digest,
                invocation_id="invocation-resolve-changed-digest",
            )

            self.assertEqual(reframed["state"], "planned")
            self.assertEqual(reframed["task_digest"], new_task_digest)
            self.assertEqual(
                reframed["decision_digest"], new_decision_digest
            )
            self.assertEqual(
                reframed["clarification_invalidation"]["invalidated_from"],
                "review_ready",
            )
            self.assertTrue(
                {"ready", "verifying", "review_ready"}.isdisjoint(
                    reframed["evidence"]
                )
            )

    def test_changed_question_or_repository_evidence_invalidates_resolution(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-CHANGED-REPOSITORY",
                source="ready",
                invocation_id="invocation-require-repository-change",
            )
            result = self.resolve_flow(
                store,
                task_id="TASK-CHANGED-REPOSITORY",
                request=request,
                required_state=required,
                repository_status="conflicting",
                invocation_id="invocation-resolve-repository-change",
            )
            self.assertEqual(result["state"], "clarification_required")
            self.assertEqual(
                result["clarification_resolution_invalidated"],
                "repository_evidence_changed",
            )

    def test_invalidated_resolution_requires_fresh_host_reframe(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            task_id = "TASK-REFRAME-INVALIDATED-CLARIFICATION"
            request, required = self.require_flow(
                store,
                task_id=task_id,
                source="ready",
                invocation_id="invocation-reframe-initial",
            )
            invalidated = self.resolve_flow(
                store,
                task_id=task_id,
                request=request,
                required_state=required,
                repository_status="conflicting",
                invocation_id="invocation-reframe-invalidated",
            )
            old_sidecar = root / invalidated[
                "clarification_prompt_view_path"
            ]
            self.assertTrue(old_sidecar.exists())
            state_path = store._path(task_id)
            state_before_stale_refresh = state_path.read_bytes()
            sidecar_before_stale_refresh = old_sidecar.read_bytes()

            reframe_invocation = "invocation-reframe-fresh-request"
            fresh_request = self.validated_request(
                invocation_id=reframe_invocation
            )
            fresh_context = self.trusted_route_context(
                decision_digest=invalidated[
                    "clarification_decision_digest"
                ],
                invocation_id=reframe_invocation,
            )
            with self.assertRaisesRegex(ValueError, "E_STATE_CAS"):
                store.require_clarification(
                    task_id,
                    request=fresh_request,
                    route_context=fresh_context,
                    expected_generation=invalidated["generation"] - 1,
                    current_branch="codex/test",
                    task_digest=self.task_digest,
                    decision_digest=invalidated[
                        "clarification_decision_digest"
                    ],
                )
            self.assertEqual(
                state_path.read_bytes(), state_before_stale_refresh
            )
            self.assertEqual(
                old_sidecar.read_bytes(), sidecar_before_stale_refresh
            )
            reframed = store.require_clarification(
                task_id,
                request=fresh_request,
                route_context=fresh_context,
                expected_generation=invalidated["generation"],
                current_branch="codex/test",
                task_digest=self.task_digest,
                decision_digest=invalidated[
                    "clarification_decision_digest"
                ],
            )

            self.assertEqual(reframed["state"], "clarification_required")
            self.assertEqual(
                reframed["clarification_resume_state"], "ready"
            )
            self.assertGreater(
                reframed["generation"], invalidated["generation"]
            )
            self.assertNotIn(
                "clarification_resolution_invalidated", reframed
            )
            self.assertFalse(old_sidecar.exists())
            resolved = self.resolve_flow(
                store,
                task_id=task_id,
                request=fresh_request,
                required_state=reframed,
                invocation_id="invocation-reframe-final-resolution",
            )
            self.assertEqual(resolved["state"], "ready")

    def test_invalidated_marker_is_closed_before_host_refresh(self) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            task_id = "TASK-CLOSED-INVALIDATION-MARKER"
            request, required = self.require_flow(
                store,
                task_id=task_id,
                source="ready",
                invocation_id="invocation-closed-marker-initial",
            )
            invalidated = self.resolve_flow(
                store,
                task_id=task_id,
                request=request,
                required_state=required,
                repository_status="conflicting",
                invocation_id="invocation-closed-marker-invalidated",
            )
            invalidated["clarification_resolution_invalidated"] = (
                "serialized_override"
            )
            lifecycle._atomic_json(store._path(task_id), invalidated)
            sidecar = root / invalidated[
                "clarification_prompt_view_path"
            ]
            state_before = store._path(task_id).read_bytes()
            sidecar_before = sidecar.read_bytes()
            invocation_id = "invocation-closed-marker-refresh"
            fresh_request = self.validated_request(
                invocation_id=invocation_id
            )
            fresh_context = self.trusted_route_context(
                decision_digest=invalidated[
                    "clarification_decision_digest"
                ],
                invocation_id=invocation_id,
            )

            with self.assertRaisesRegex(ValueError, "E_STATE_LATERAL"):
                store.require_clarification(
                    task_id,
                    request=fresh_request,
                    route_context=fresh_context,
                    expected_generation=invalidated["generation"],
                    current_branch="codex/test",
                    task_digest=self.task_digest,
                    decision_digest=invalidated[
                        "clarification_decision_digest"
                    ],
                )
            self.assertEqual(store._path(task_id).read_bytes(), state_before)
            self.assertEqual(sidecar.read_bytes(), sidecar_before)

    def test_not_checked_resolution_requires_closed_policy_exception(
        self,
    ) -> None:
        from control_plane.clarification import (
            REPOSITORY_EVIDENCE_NOT_CHECKED,
        )
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskStore

        low_task = task_envelope(
            task_id="task-low-not-checked",
            risk={
                "uncertainty": 0,
                "blast_radius": 0,
                "irreversibility": 0,
                "verification_complexity": 0,
            },
            scope_paths=["control_plane/"],
        )
        low_digest = contract_digest(low_task)
        low_issue = {**self.issue_draft, "severity": "low"}
        request = self.validated_request(
            task=low_task,
            issue_draft=low_issue,
            use_not_checked=True,
            invocation_id="invocation-low-not-checked",
        )
        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-LOW-NOT-CHECKED",
                task_digest=low_digest,
                request=request,
                invocation_id="invocation-low-not-checked",
            )
            with self.assertRaisesRegex(
                ValueError, "C_REPOSITORY_CHECK_REQUIRED"
            ):
                self.resolve_flow(
                    store,
                    task_id="TASK-LOW-NOT-CHECKED",
                    request=request,
                    required_state=required,
                    task_digest=low_digest,
                    repository_context=REPOSITORY_EVIDENCE_NOT_CHECKED,
                    invocation_id="invocation-low-not-checked-resolution",
                )

    def test_prompt_sidecar_creation_fsyncs_each_new_directory_parent(
        self,
    ) -> None:
        import control_plane.lifecycle as lifecycle

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = (
                root
                / "codex-control-plane"
                / "clarification-prompt-views"
                / "TASK-PROMPT-DURABILITY"
                / "generation-00000000.json"
            )
            original_mkdir = lifecycle.os.mkdir
            original_fsync = lifecycle.os.fsync
            events: list[tuple[str, int | None, str | None]] = []

            def tracked_mkdir(path, mode=0o777, *, dir_fd=None):
                events.append(("mkdir", dir_fd, str(path)))
                return original_mkdir(path, mode=mode, dir_fd=dir_fd)

            def tracked_fsync(descriptor):
                events.append(("fsync", descriptor, None))
                return original_fsync(descriptor)

            with patch.object(
                lifecycle.os, "mkdir", side_effect=tracked_mkdir
            ), patch.object(
                lifecycle.os, "fsync", side_effect=tracked_fsync
            ):
                lifecycle._atomic_bytes(root, target, b"{}")

            self.assertEqual(target.read_bytes(), b"{}")
            mkdir_events = [
                (index, event)
                for index, event in enumerate(events)
                if event[0] == "mkdir"
            ]
            self.assertEqual(len(mkdir_events), 3)
            for index, (_, parent_descriptor, _) in mkdir_events:
                self.assertEqual(
                    events[index + 1],
                    ("fsync", parent_descriptor, None),
                )

    def test_resolution_publish_and_cleanup_faults_remain_recoverable(
        self,
    ) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-RESOLUTION-STATE-FAULT",
                source="ready",
                invocation_id="invocation-state-fault-require",
            )
            with patch.object(
                lifecycle,
                "_atomic_json",
                side_effect=OSError("injected-resolution-state-fault"),
            ), self.assertRaisesRegex(
                OSError, "injected-resolution-state-fault"
            ):
                self.resolve_flow(
                    store,
                    task_id="TASK-RESOLUTION-STATE-FAULT",
                    request=request,
                    required_state=required,
                    invocation_id="invocation-state-fault-first",
                )
            preserved = store.clarification_status(
                "TASK-RESOLUTION-STATE-FAULT"
            )
            self.assertEqual(preserved["state"], "clarification_required")
            recovered = self.resolve_flow(
                store,
                task_id="TASK-RESOLUTION-STATE-FAULT",
                request=request,
                required_state=required,
                invocation_id="invocation-state-fault-retry",
            )
            self.assertEqual(recovered["state"], "ready")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            request, required = self.require_flow(
                store,
                task_id="TASK-RESOLUTION-UNLINK-FAULT",
                source="ready",
                invocation_id="invocation-unlink-fault-require",
            )
            sidecar = root / required["clarification_prompt_view_path"]
            with patch.object(
                lifecycle,
                "_unlink_prompt_file",
                side_effect=OSError("injected-resolution-unlink-fault"),
            ), self.assertRaisesRegex(
                OSError, "injected-resolution-unlink-fault"
            ):
                self.resolve_flow(
                    store,
                    task_id="TASK-RESOLUTION-UNLINK-FAULT",
                    request=request,
                    required_state=required,
                    invocation_id="invocation-unlink-fault-resolution",
                )
            self.assertEqual(
                store.status("TASK-RESOLUTION-UNLINK-FAULT")["state"],
                "ready",
            )
            self.assertTrue(sidecar.exists())
            gc_result = store.gc_clarification_prompt_views(
                "TASK-RESOLUTION-UNLINK-FAULT"
            )
            self.assertFalse(sidecar.exists())
            self.assertEqual(gc_result["removed"], [str(sidecar)])

    def test_clarification_resolution_is_crash_and_race_safe(self) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            prior_state = self.start_task_at(
                store, "TASK-CLARIFICATION-CRASH", "planned"
            )
            request = self.validated_request(
                invocation_id="invocation-crash-require"
            )
            context = self.trusted_route_context(
                invocation_id="invocation-crash-require"
            )
            with patch.object(
                lifecycle,
                "_atomic_json",
                side_effect=OSError("injected-state-publish"),
            ), self.assertRaisesRegex(OSError, "injected-state-publish"):
                store.require_clarification(
                    "TASK-CLARIFICATION-CRASH",
                    request=request,
                    route_context=context,
                    expected_generation=prior_state["generation"],
                    current_branch="codex/test",
                    task_digest=self.task_digest,
                    decision_digest=store.status(
                        "TASK-CLARIFICATION-CRASH"
                    )["decision_digest"],
                )
            self.assertEqual(
                store.status("TASK-CLARIFICATION-CRASH")["state"],
                "planned",
            )
            removed = store.gc_clarification_prompt_views(
                "TASK-CLARIFICATION-CRASH"
            )
            self.assertTrue(removed["removed"])

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            request, required = self.require_flow(
                store,
                task_id="TASK-CLARIFICATION-RACE",
                source="ready",
                invocation_id="invocation-race-require",
            )
            route_context = self.trusted_route_context(
                decision_digest=required[
                    "clarification_decision_digest"
                ],
                invocation_id="invocation-race-resolve",
                clarification_status="resolved",
            )
            interaction = self.trusted_interaction(
                request, invocation_id="invocation-race-resolve"
            )
            repository = self.repository_context(
                invocation_id="invocation-race-repository"
            )
            arguments = {
                "interaction": interaction,
                "route_context": route_context,
                "repository_context": repository,
                "expected_generation": required["generation"],
                "current_branch": "codex/test",
                "expected_head": HEAD,
                "task_digest": self.task_digest,
                "decision_digest": required[
                    "clarification_decision_digest"
                ],
                "context_digest": (
                    self.bridge.clarification_route_context_digest(
                        route_context
                    )
                ),
                "question_digest": self.question_digest,
            }

            def resolve_once():
                return store.resolve_and_resume_clarification(
                    "TASK-CLARIFICATION-RACE", **arguments
                )

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [
                    future.exception() or future.result()
                    for future in (
                        pool.submit(resolve_once),
                        pool.submit(resolve_once),
                    )
                ]
            states = [
                item["state"] for item in results if isinstance(item, dict)
            ]
            self.assertEqual(states, ["ready"])
            self.assertEqual(
                store.status("TASK-CLARIFICATION-RACE")["state"], "ready"
            )

    def test_prompt_view_gc_uses_same_task_flock_and_generation(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            store = TaskStore(Path(temporary))
            _, required = self.require_flow(
                store,
                task_id="TASK-PROMPT-GC",
                invocation_id="invocation-prompt-gc",
            )
            current = (
                Path(temporary)
                / required["clarification_prompt_view_path"]
            )
            orphan = current.parent / "generation-00000000.json"
            orphan.write_text("{}\n", encoding="utf-8")

            result = store.gc_clarification_prompt_views(
                "TASK-PROMPT-GC",
                expected_generation=required["generation"],
            )

            self.assertTrue(current.exists())
            self.assertFalse(orphan.exists())
            self.assertEqual(result["generation"], required["generation"])
            self.assertEqual(result["removed"], [str(orphan)])

    def test_prompt_view_gc_rejects_ancestor_symlink_escape(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary, (
            tempfile.TemporaryDirectory()
        ) as outside_temporary:
            root = Path(temporary)
            outside = Path(outside_temporary)
            store = TaskStore(root)
            task_id = "TASK-PROMPT-GC-ANCESTOR-SYMLINK"
            self.start_task_at(store, task_id, "planned")
            outside_task = outside / task_id
            outside_task.mkdir()
            external = outside_task / "generation-00000000.json"
            external.write_text("{}\n", encoding="utf-8")
            prompt_root = (
                root / "codex-control-plane" / "clarification-prompt-views"
            )
            prompt_root.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(
                ValueError,
                "C_PRESENTATION_UNAVAILABLE",
            ):
                store.gc_clarification_prompt_views(task_id)

            self.assertTrue(external.exists())

    def test_prompt_publisher_rejects_ancestor_symlink_escape(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary, (
            tempfile.TemporaryDirectory()
        ) as outside_temporary:
            root = Path(temporary)
            outside = Path(outside_temporary)
            store = TaskStore(root)
            task_id = "TASK-PROMPT-PUBLISH-ANCESTOR-SYMLINK"
            prior = self.start_task_at(store, task_id, "planned")
            invocation_id = "invocation-publish-ancestor-symlink"
            request = self.validated_request(invocation_id=invocation_id)
            context = self.trusted_route_context(
                decision_digest=prior["decision_digest"],
                invocation_id=invocation_id,
            )
            prompt_root = (
                root / "codex-control-plane" / "clarification-prompt-views"
            )
            prompt_root.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(
                ValueError,
                "C_PRESENTATION_UNAVAILABLE",
            ):
                store.require_clarification(
                    task_id,
                    request=request,
                    route_context=context,
                    expected_generation=prior["generation"],
                    current_branch="codex/test",
                    task_digest=self.task_digest,
                    decision_digest=prior["decision_digest"],
                )

            self.assertEqual(list(outside.rglob("generation-*.json")), [])

    def test_prompt_publisher_cannot_race_ancestor_symlink_swap(self) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary, (
            tempfile.TemporaryDirectory()
        ) as outside_temporary:
            root = Path(temporary)
            outside = Path(outside_temporary)
            store = TaskStore(root)
            task_id = "TASK-PROMPT-PUBLISH-SYMLINK-RACE"
            prior = self.start_task_at(store, task_id, "planned")
            invocation_id = "invocation-publish-symlink-race"
            request = self.validated_request(invocation_id=invocation_id)
            context = self.trusted_route_context(
                decision_digest=prior["decision_digest"],
                invocation_id=invocation_id,
            )
            prompt_root = (
                root / "codex-control-plane" / "clarification-prompt-views"
            )
            outside_task = outside / task_id
            outside_task.mkdir()
            original_atomic_bytes = lifecycle._atomic_bytes

            def swap_then_publish(state_dir, path, payload):
                prompt_root.symlink_to(outside, target_is_directory=True)
                return original_atomic_bytes(state_dir, path, payload)

            with patch.object(
                lifecycle,
                "_atomic_bytes",
                side_effect=swap_then_publish,
            ), self.assertRaisesRegex(
                ValueError,
                "C_PRESENTATION_UNAVAILABLE",
            ):
                store.require_clarification(
                    task_id,
                    request=request,
                    route_context=context,
                    expected_generation=prior["generation"],
                    current_branch="codex/test",
                    task_digest=self.task_digest,
                    decision_digest=prior["decision_digest"],
                )

            self.assertEqual(
                list(outside_task.glob("generation-*.json")),
                [],
            )

    def test_prompt_gc_cannot_race_ancestor_symlink_swap(self) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary, (
            tempfile.TemporaryDirectory()
        ) as outside_temporary:
            root = Path(temporary)
            outside = Path(outside_temporary)
            store = TaskStore(root)
            task_id = "TASK-PROMPT-GC-SYMLINK-RACE"
            self.start_task_at(store, task_id, "planned")
            prompt_root = (
                root / "codex-control-plane" / "clarification-prompt-views"
            )
            prompt_task = prompt_root / task_id
            prompt_task.mkdir(parents=True)
            local = prompt_task / "generation-00000000.json"
            local.write_text("{}\n", encoding="utf-8")
            outside_task = outside / task_id
            outside_task.mkdir()
            external = outside_task / local.name
            external.write_text("{}\n", encoding="utf-8")
            displaced = root / "displaced-prompt-views"
            original_unlink = lifecycle.os.unlink
            swapped = False

            def swap_then_unlink(path, *args, **kwargs):
                nonlocal swapped
                if (
                    not swapped
                    and path == local.name
                    and kwargs.get("dir_fd") is not None
                ):
                    prompt_root.rename(displaced)
                    prompt_root.symlink_to(outside, target_is_directory=True)
                    swapped = True
                return original_unlink(path, *args, **kwargs)

            with patch.object(
                lifecycle.os,
                "unlink",
                side_effect=swap_then_unlink,
            ):
                store.gc_clarification_prompt_views(task_id)

            self.assertTrue(swapped)
            self.assertTrue(external.exists())
            self.assertFalse((displaced / task_id / local.name).exists())

    def test_prompt_publisher_and_gc_serialize_on_task_flock(self) -> None:
        import threading
        import control_plane.lifecycle as lifecycle
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            task_id = "TASK-PROMPT-PUBLISH-GC-RACE"
            prior = self.start_task_at(store, task_id, "planned")
            invocation_id = "invocation-publish-gc-race"
            request = self.validated_request(invocation_id=invocation_id)
            context = self.trusted_route_context(
                decision_digest=prior["decision_digest"],
                invocation_id=invocation_id,
            )
            orphan = (
                root
                / "codex-control-plane"
                / "clarification-prompt-views"
                / task_id
                / "generation-00000000.json"
            )
            lifecycle._atomic_bytes(root, orphan, b"{}")
            sidecar_written = threading.Event()
            allow_state_publish = threading.Event()
            gc_started = threading.Event()
            original_atomic_bytes = lifecycle._atomic_bytes

            def blocking_atomic_bytes(state_dir, path, payload):
                original_atomic_bytes(state_dir, path, payload)
                if Path(path) != orphan:
                    sidecar_written.set()
                    if not allow_state_publish.wait(timeout=5):
                        raise AssertionError("publisher barrier timed out")

            def publish():
                return store.require_clarification(
                    task_id,
                    request=request,
                    route_context=context,
                    expected_generation=prior["generation"],
                    current_branch="codex/test",
                    task_digest=self.task_digest,
                    decision_digest=prior["decision_digest"],
                )

            def collect():
                gc_started.set()
                return store.gc_clarification_prompt_views(task_id)

            with patch.object(
                lifecycle, "_atomic_bytes", side_effect=blocking_atomic_bytes
            ), ThreadPoolExecutor(max_workers=2) as pool:
                publisher = pool.submit(publish)
                self.assertTrue(sidecar_written.wait(timeout=5))
                collector = pool.submit(collect)
                self.assertTrue(gc_started.wait(timeout=5))
                self.assertFalse(collector.done())
                allow_state_publish.set()
                published = publisher.result(timeout=5)
                collected = collector.result(timeout=5)

            current = root / published["clarification_prompt_view_path"]
            self.assertTrue(current.exists())
            self.assertFalse(orphan.exists())
            self.assertEqual(collected["removed"], [str(orphan)])

    def test_cold_restart_reemits_exact_prompt_view(self) -> None:
        from control_plane.lifecycle import TaskStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TaskStore(root)
            _, required = self.require_flow(
                store,
                task_id="TASK-COLD-RESTART",
                invocation_id="invocation-cold-restart",
            )
            sidecar = root / required["clarification_prompt_view_path"]
            before = sidecar.read_bytes()

            restarted = TaskStore(root)
            status = restarted.clarification_status(
                "TASK-COLD-RESTART"
            )
            self.assertEqual(
                json.dumps(
                    status["prompt_view"],
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8"),
                before,
            )
            sidecar.unlink()
            with self.assertRaisesRegex(
                ValueError, "C_PRESENTATION_UNAVAILABLE"
            ):
                restarted.clarification_status("TASK-COLD-RESTART")
            self.assertEqual(
                restarted.status("TASK-COLD-RESTART")["state"],
                "clarification_required",
            )

    def _dirty_clarification_fixture(
        self,
        temporary: str,
        *,
        lease_paths: list[str] | None = None,
    ):
        from control_plane.clarification import (
            REPOSITORY_EVIDENCE_NOT_CHECKED,
        )
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore

        repo = Path(temporary) / "repo"
        subprocess.run(
            ["git", "init", "-b", "main", str(repo)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Tests"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "config",
                "user.email",
                "tests@example.invalid",
            ],
            check=True,
        )
        tracked = repo / "tracked.txt"
        tracked.write_text("baseline\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "tracked.txt"], check=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "baseline"],
            check=True,
            capture_output=True,
        )
        branch = "codex/dirty-clarification"
        subprocess.run(
            ["git", "-C", str(repo), "switch", "-c", branch],
            check=True,
            capture_output=True,
        )
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        state_dir = repo / ".git"
        store = TaskStore(state_dir)
        task_id = "TASK-DIRTY-CLARIFICATION"
        decision_digest = contract_digest(
            {"decision": "dirty-clarification"}
        )
        prior_state = self.start_task_at(
            store,
            task_id,
            "implementing",
            decision_digest=decision_digest,
            branch=branch,
        )
        lease = TaskLease.acquire(
            state_dir,
            task_id=task_id,
            worktree=str(repo),
            branch=branch,
            session_id=SESSION_ID,
            paths=lease_paths or ["tracked.txt"],
            policy_digest=contract_digest({"policy": "dirty"}),
        )
        tracked.write_text("dirty\n", encoding="utf-8")
        decision_issue = {
            **self.issue_draft,
            "issue_kind": "decision_approval",
        }
        request = self.validated_request(
            issue_draft=decision_issue,
            use_not_checked=True,
            invocation_id="invocation-dirty-require",
        )
        context = self.trusted_route_context(
            decision_digest=decision_digest,
            repository=repo,
            worktree=repo,
            branch=branch,
            head=head,
            invocation_id="invocation-dirty-require",
        )
        required = store.require_clarification(
            task_id,
            request=request,
            route_context=context,
            expected_generation=prior_state["generation"],
            current_branch=branch,
            task_digest=self.task_digest,
            decision_digest=decision_digest,
        )
        return {
            "repo": repo,
            "tracked": tracked,
            "head": head,
            "state_dir": state_dir,
            "store": store,
            "task_id": task_id,
            "decision_digest": decision_digest,
            "lease": lease,
            "request": request,
            "required": required,
            "repository_context": REPOSITORY_EVIDENCE_NOT_CHECKED,
            "branch": branch,
        }

    def test_dirty_refresh_cannot_launder_new_changed_paths(self) -> None:
        from control_plane.contracts import contract_digest

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._dirty_clarification_fixture(
                temporary, lease_paths=["."]
            )
            invalidated = self.resolve_flow(
                fixture["store"],
                task_id=fixture["task_id"],
                request=fixture["request"],
                required_state=fixture["required"],
                decision_digest=fixture["decision_digest"],
                repository=fixture["repo"],
                worktree=fixture["repo"],
                branch=fixture["branch"],
                head=fixture["head"],
                invocation_id="invocation-dirty-invalidated",
                repository_context=self.repository_context(
                    repository=fixture["repo"],
                    worktree=fixture["repo"],
                    branch=fixture["branch"],
                    head=fixture["head"],
                    invocation_id=(
                        "invocation-dirty-invalidated-repository"
                    ),
                    status="conflicting",
                    evidence=("tracked.txt",),
                ),
            )
            fixture["repo"].joinpath("new-during-gate.txt").write_text(
                "new write\n", encoding="utf-8"
            )
            refresh_invocation = "invocation-dirty-refresh"
            decision_issue = {
                **self.issue_draft,
                "issue_kind": "decision_approval",
            }
            fresh_request = self.validated_request(
                issue_draft=decision_issue,
                use_not_checked=True,
                invocation_id=refresh_invocation,
            )
            fresh_context = self.trusted_route_context(
                decision_digest=fixture["decision_digest"],
                repository=fixture["repo"],
                worktree=fixture["repo"],
                branch=fixture["branch"],
                head=fixture["head"],
                invocation_id=refresh_invocation,
            )

            blocked = fixture["store"].require_clarification(
                fixture["task_id"],
                request=fresh_request,
                route_context=fresh_context,
                expected_generation=invalidated["generation"],
                current_branch=fixture["branch"],
                task_digest=self.task_digest,
                decision_digest=fixture["decision_digest"],
            )

            self.assertEqual(blocked["state"], "blocked")
            self.assertEqual(
                blocked["block_reason"], "E_CLARIFICATION_LEASE_DRIFT"
            )
            self.assertTrue(blocked["resume_forbidden"])
            self.assertEqual(
                blocked["clarification_block_digest"],
                contract_digest(
                    {
                        "reason": "E_CLARIFICATION_LEASE_DRIFT",
                        "task_id": fixture["task_id"],
                        "generation": invalidated["generation"],
                    }
                ),
            )

    def test_dirty_inventory_observation_runs_outside_lifecycle_flocks(
        self,
    ) -> None:
        from contextlib import contextmanager
        import control_plane.lifecycle as lifecycle

        depth = {"common": 0, "task": 0}
        observations: list[tuple[int, int]] = []
        original_common = lifecycle._common_lease_lock
        original_task = lifecycle._task_guard
        original_changed_paths = lifecycle._changed_paths

        @contextmanager
        def guarded_common(*args, **kwargs):
            with original_common(*args, **kwargs) as token:
                depth["common"] += 1
                try:
                    yield token
                finally:
                    depth["common"] -= 1

        @contextmanager
        def guarded_task(*args, **kwargs):
            with original_task(*args, **kwargs):
                depth["task"] += 1
                try:
                    yield
                finally:
                    depth["task"] -= 1

        def observed_changed_paths(worktree):
            observations.append((depth["common"], depth["task"]))
            return original_changed_paths(worktree)

        with tempfile.TemporaryDirectory() as temporary, patch.object(
            lifecycle, "_common_lease_lock", guarded_common
        ), patch.object(
            lifecycle, "_task_guard", guarded_task
        ), patch.object(
            lifecycle,
            "_changed_paths",
            side_effect=observed_changed_paths,
        ):
            fixture = self._dirty_clarification_fixture(temporary)
            resumed = self.resolve_flow(
                fixture["store"],
                task_id=fixture["task_id"],
                request=fixture["request"],
                required_state=fixture["required"],
                decision_digest=fixture["decision_digest"],
                repository=fixture["repo"],
                worktree=fixture["repo"],
                branch=fixture["branch"],
                head=fixture["head"],
                invocation_id="invocation-lock-free-inventory",
                repository_context=fixture["repository_context"],
            )

        self.assertEqual(resumed["state"], "implementing")
        self.assertEqual(observations, [(0, 0), (0, 0)])

    def test_same_session_dirty_clarification_revalidates_existing_writer_lease(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._dirty_clarification_fixture(temporary)
            resumed = self.resolve_flow(
                fixture["store"],
                task_id=fixture["task_id"],
                request=fixture["request"],
                required_state=fixture["required"],
                decision_digest=fixture["decision_digest"],
                repository=fixture["repo"],
                worktree=fixture["repo"],
                branch=fixture["branch"],
                head=fixture["head"],
                invocation_id="invocation-dirty-resume",
                repository_context=fixture["repository_context"],
            )

            self.assertEqual(resumed["state"], "implementing")
            self.assertEqual(
                resumed["lease_digest"],
                fixture["lease"]["lease_digest"],
            )
            self.assertTrue(
                fixture["state_dir"]
                .joinpath(
                    "codex-control-plane",
                    "leases",
                    f"{fixture['task_id']}.json",
                )
                .exists()
            )

    def test_cross_session_clarification_never_adopts_dirty_writer_lease(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._dirty_clarification_fixture(temporary)
            blocked = self.resolve_flow(
                fixture["store"],
                task_id=fixture["task_id"],
                request=fixture["request"],
                required_state=fixture["required"],
                decision_digest=fixture["decision_digest"],
                repository=fixture["repo"],
                worktree=fixture["repo"],
                branch=fixture["branch"],
                head=fixture["head"],
                session_id="session-other",
                invocation_id="invocation-dirty-other-session",
                repository_context=fixture["repository_context"],
            )

            self.assertEqual(blocked["state"], "blocked")
            self.assertEqual(
                blocked["block_reason"], "E_CLARIFICATION_OWNER_CHANGED"
            )
            self.assertTrue(blocked["resume_forbidden"])
            self.assertTrue(
                fixture["state_dir"]
                .joinpath(
                    "codex-control-plane",
                    "leases",
                    f"{fixture['task_id']}.json",
                )
                .exists()
            )

    def test_dirty_clarification_reframe_releases_old_lease_before_new_task(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._dirty_clarification_fixture(temporary)
            new_task_digest = contract_digest({"task": "dirty-reframe"})
            new_decision_digest = contract_digest(
                {"decision": "dirty-reframe"}
            )
            blocked = self.resolve_flow(
                fixture["store"],
                task_id=fixture["task_id"],
                request=fixture["request"],
                required_state=fixture["required"],
                task_digest=new_task_digest,
                decision_digest=new_decision_digest,
                repository=fixture["repo"],
                worktree=fixture["repo"],
                branch=fixture["branch"],
                head=fixture["head"],
                invocation_id="invocation-dirty-reframe",
                repository_context=fixture["repository_context"],
            )

            self.assertEqual(blocked["state"], "blocked")
            self.assertEqual(blocked["block_reason"], "E_REFRAME_REQUIRED")
            self.assertTrue(blocked["resume_forbidden"])
            self.assertFalse(
                fixture["state_dir"]
                .joinpath(
                    "codex-control-plane",
                    "leases",
                    f"{fixture['task_id']}.json",
                )
                .exists()
            )
            self.assertEqual(
                fixture["tracked"].read_text(encoding="utf-8"), "dirty\n"
            )

    def test_clarification_reframe_recovery_revalidates_exact_marker(
        self,
    ) -> None:
        import control_plane.lifecycle as lifecycle
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._dirty_clarification_fixture(temporary)
            with patch.object(
                TaskLease,
                "_release_locked",
                side_effect=OSError("injected-release-boundary"),
            ), self.assertRaisesRegex(OSError, "injected-release-boundary"):
                self.resolve_flow(
                    fixture["store"],
                    task_id=fixture["task_id"],
                    request=fixture["request"],
                    required_state=fixture["required"],
                    task_digest=contract_digest({"task": "recovery-new"}),
                    decision_digest=contract_digest(
                        {"decision": "recovery-new"}
                    ),
                    repository=fixture["repo"],
                    worktree=fixture["repo"],
                    branch=fixture["branch"],
                    head=fixture["head"],
                    invocation_id="invocation-recovery-marker",
                    repository_context=fixture["repository_context"],
                )
            marker = fixture["store"].status(fixture["task_id"])
            self.assertEqual(marker["state"], "finalizing_suspend")
            original_release = TaskLease._release_locked

            def release_then_tamper(*args, **kwargs):
                result = original_release(*args, **kwargs)
                current = fixture["store"].status(fixture["task_id"])
                current["generation"] += 1
                current["finalization"] = {
                    **current["finalization"],
                    "resolution_digest": contract_digest(
                        {"tampered": "marker"}
                    ),
                }
                lifecycle._atomic_json(
                    fixture["store"]._path(fixture["task_id"]), current
                )
                return result

            with patch.object(
                TaskLease,
                "_release_locked",
                side_effect=release_then_tamper,
            ), self.assertRaisesRegex(ValueError, "E_STATE_CAS"):
                fixture["store"].recover_writer_finalization(
                    fixture["task_id"]
                )

    def test_crashed_clarification_owner_recovery_is_explicit_and_non_destructive(
        self,
    ) -> None:
        from control_plane.lifecycle import TaskLease

        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._dirty_clarification_fixture(temporary)
            with self.assertRaisesRegex(
                (TypeError, ValueError),
                "E_LEASE_RECOVERY_UNAUTHORIZED|host-bound",
            ):
                TaskLease.recover_abandoned(
                    fixture["state_dir"],
                    fixture["state_dir"],
                    task_id=fixture["task_id"],
                    worktree=str(fixture["repo"]),
                    branch=fixture["branch"],
                    owner_session_id=SESSION_ID,
                    policy_digest=fixture["lease"]["policy_digest"],
                    lease_digest=fixture["lease"]["lease_digest"],
                    recovery_authorization={
                        "task_id": fixture["task_id"]
                    },
                    worktree_inventory={},
                )
            self.assertTrue(
                fixture["state_dir"]
                .joinpath(
                    "codex-control-plane",
                    "leases",
                    f"{fixture['task_id']}.json",
                )
                .exists()
            )
            self.assertEqual(
                fixture["tracked"].read_text(encoding="utf-8"), "dirty\n"
            )


if __name__ == "__main__":
    unittest.main()
