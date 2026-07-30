from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
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


if __name__ == "__main__":
    unittest.main()
