"""Pure clarification contracts and deterministic gate evaluation.

Serialized payloads in this module are inert data.  Trust is carried only by
opaque, in-memory wrappers issued by :mod:`control_plane.host_bridge`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from control_plane.contracts import (
    PROVENANCE,
    SHA256_DIGEST,
    TASK_EFFECTS,
    ContractIssue,
    canonical_json,
    contract_digest,
    safe_scope_path,
    validate_task_envelope,
    validate_task_id,
)
from control_plane.scopes import normalize_scope


CLARIFICATION_LEVELS = frozenset({"low", "medium", "high", "critical"})
CLARIFICATION_KINDS = frozenset({"clarification", "decision_approval"})
REPOSITORY_CHECK_STATES = frozenset(
    {"not_checked", "resolved", "unresolved", "conflicting"}
)

_ISSUE_KEYS = frozenset(
    {
        "schema_version",
        "issue_id",
        "issue_kind",
        "severity",
        "question_digest",
        "option_ids",
        "recommended_option_id",
    }
)
_PROMPT_DRAFT_KEYS = frozenset(
    {
        "schema_version",
        "question_text",
        "options",
        "recommended_option_id",
        "consequence_text",
    }
)
_PROMPT_VIEW_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "question_text",
        "options",
        "recommended_option_id",
        "consequence_text",
    }
)
_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "request_id",
        "task_digest",
        "session_id",
        "issue_kind",
        "severity",
        "question_digest",
        "presentation_digest",
        "repository_check",
        "option_ids",
        "recommended_option_id",
    }
)
_RESOLUTION_KEYS = frozenset(
    {
        "schema_version",
        "resolution_id",
        "request_digest",
        "task_digest",
        "session_id",
        "selected_option_id",
        "response_digest",
    }
)
_ASSUMPTION_KEYS = frozenset(
    {
        "schema_version",
        "request_digest",
        "task_digest",
        "selected_option_id",
        "statement_digest",
    }
)
_CONFIRMATION_KEYS = frozenset(
    {
        "schema_version",
        "confirmation_id",
        "request_digest",
        "task_digest",
        "session_id",
        "scope_paths",
        "effect",
        "consequence_digest",
    }
)


@dataclass(frozen=True)
class RepositoryEvidenceFacts:
    """Bounded internal facts returned by a host-selected inspector."""

    status: str
    evidence_items: tuple[str, ...]


class ClarificationRepositoryInspector(Protocol):
    def inspect(
        self,
        *,
        canonical_root: Path,
        question_digest: str,
        max_files: int,
        max_bytes: int,
    ) -> RepositoryEvidenceFacts: ...


class RepositoryEvidenceNotChecked:
    """Typed absence of repository evidence; never a resolved observation."""

    __slots__ = ()

    def __new__(cls, token: object | None = None):
        if token is not _NOT_CHECKED_TOKEN:
            raise TypeError("RepositoryEvidenceNotChecked is a closed sentinel")
        return super().__new__(cls)


_NOT_CHECKED_TOKEN = object()
REPOSITORY_EVIDENCE_NOT_CHECKED = RepositoryEvidenceNotChecked(
    _NOT_CHECKED_TOKEN
)


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code, path, message)


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and SHA256_DIGEST.fullmatch(value) is not None


def _utf8_size(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def _valid_bounded_text(value: object, maximum: int) -> bool:
    size = _utf8_size(value)
    return bool(
        isinstance(value, str)
        and value.strip()
        and size is not None
        and size <= maximum
        and not any(ord(character) < 32 and character not in "\t\n" for character in value)
    )


def _valid_option_ids(value: object) -> bool:
    return bool(
        isinstance(value, list)
        and 2 <= len(value) <= 3
        and len(set(value)) == len(value)
        and all(validate_task_id(item) for item in value)
    )


def validate_clarification_issue_draft(
    issue: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate the exact serializable issue supplied by the framer."""

    issues: list[ContractIssue] = []
    if not isinstance(issue, Mapping) or set(issue) != _ISSUE_KEYS:
        return [
            _issue(
                "C_ISSUE_SCHEMA",
                "issue",
                "ClarificationIssueDraft must use the closed schema.",
            )
        ]
    if issue.get("schema_version") != 1:
        issues.append(
            _issue("C_ISSUE_SCHEMA", "schema_version", "Only schema 1 is supported.")
        )
    if not validate_task_id(issue.get("issue_id")):
        issues.append(_issue("C_ISSUE_SCHEMA", "issue_id", "Unsafe issue ID."))
    if issue.get("issue_kind") not in CLARIFICATION_KINDS:
        issues.append(
            _issue("C_ISSUE_KIND", "issue_kind", "Unsupported issue kind.")
        )
    if issue.get("severity") not in CLARIFICATION_LEVELS:
        issues.append(
            _issue("C_SEVERITY", "severity", "Unsupported clarification severity.")
        )
    if not _valid_digest(issue.get("question_digest")):
        issues.append(
            _issue(
                "C_QUESTION_DIGEST",
                "question_digest",
                "Question digest must be sha256.",
            )
        )
    option_ids = issue.get("option_ids")
    if not _valid_option_ids(option_ids):
        issues.append(
            _issue("C_OPTION", "option_ids", "Two or three unique options are required.")
        )
    if (
        not isinstance(option_ids, list)
        or issue.get("recommended_option_id") not in option_ids
    ):
        issues.append(
            _issue(
                "C_OPTION",
                "recommended_option_id",
                "Recommendation must name one declared option.",
            )
        )
    return issues


def validate_clarification_prompt_view_draft(
    draft: Mapping[str, Any],
    *,
    issue: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate bounded display text without treating it as authority."""

    issues: list[ContractIssue] = []
    if not isinstance(draft, Mapping) or set(draft) != _PROMPT_DRAFT_KEYS:
        return [
            _issue(
                "C_PRESENTATION_UNAVAILABLE",
                "prompt_view",
                "Prompt view must use the closed schema.",
            )
        ]
    if draft.get("schema_version") != 1:
        issues.append(
            _issue(
                "C_PRESENTATION_UNAVAILABLE",
                "prompt_view.schema_version",
                "Only schema 1 is supported.",
            )
        )
    if not _valid_bounded_text(draft.get("question_text"), 512):
        issues.append(
            _issue(
                "C_PRESENTATION_UNAVAILABLE",
                "prompt_view.question_text",
                "Question text is invalid or oversized.",
            )
        )
    if not _valid_bounded_text(draft.get("consequence_text"), 256):
        issues.append(
            _issue(
                "C_PRESENTATION_UNAVAILABLE",
                "prompt_view.consequence_text",
                "Consequence text is invalid or oversized.",
            )
        )
    options = draft.get("options")
    option_ids: list[str] = []
    if (
        not isinstance(options, list)
        or not 2 <= len(options) <= 3
        or any(
            not isinstance(option, Mapping)
            or set(option) != {"id", "label"}
            or not validate_task_id(option.get("id"))
            or not _valid_bounded_text(option.get("label"), 128)
            for option in options
        )
    ):
        issues.append(
            _issue(
                "C_PRESENTATION_UNAVAILABLE",
                "prompt_view.options",
                "Prompt options are invalid.",
            )
        )
    else:
        option_ids = [str(option["id"]) for option in options]
        if len(set(option_ids)) != len(option_ids):
            issues.append(
                _issue(
                    "C_PRESENTATION_UNAVAILABLE",
                    "prompt_view.options",
                    "Prompt option IDs must be unique.",
                )
            )
    if (
        option_ids != list(issue.get("option_ids", []))
        or draft.get("recommended_option_id")
        != issue.get("recommended_option_id")
    ):
        issues.append(
            _issue(
                "C_PRESENTATION_UNAVAILABLE",
                "prompt_view.options",
                "Prompt view does not match the framed issue.",
            )
        )
    return issues


def validate_clarification_request(
    request: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate the exact inert ClarificationRequest payload."""

    issues: list[ContractIssue] = []
    if not isinstance(request, Mapping) or set(request) != _REQUEST_KEYS:
        return [
            _issue(
                "C_SCHEMA",
                "request",
                "ClarificationRequest must use the closed schema.",
            )
        ]
    if request.get("schema_version") != 1:
        issues.append(_issue("C_SCHEMA", "schema_version", "Only schema 1 is supported."))
    if not validate_task_id(request.get("request_id")):
        issues.append(_issue("C_SCHEMA", "request_id", "Unsafe request ID."))
    if not _valid_digest(request.get("task_digest")):
        issues.append(_issue("C_TASK_DIGEST", "task_digest", "Invalid task digest."))
    if not validate_task_id(request.get("session_id")):
        issues.append(_issue("C_SESSION", "session_id", "Invalid session ID."))
    if request.get("issue_kind") not in CLARIFICATION_KINDS:
        issues.append(_issue("C_ISSUE_KIND", "issue_kind", "Unsupported issue kind."))
    if request.get("severity") not in CLARIFICATION_LEVELS:
        issues.append(_issue("C_SEVERITY", "severity", "Unsupported severity."))
    if not _valid_digest(request.get("question_digest")):
        issues.append(
            _issue("C_QUESTION_DIGEST", "question_digest", "Invalid question digest.")
        )
    if not _valid_digest(request.get("presentation_digest")):
        issues.append(
            _issue(
                "C_PRESENTATION_UNAVAILABLE",
                "presentation_digest",
                "Invalid presentation digest.",
            )
        )
    option_ids = request.get("option_ids")
    if not _valid_option_ids(option_ids):
        issues.append(_issue("C_OPTION", "option_ids", "Invalid option IDs."))
    if (
        not isinstance(option_ids, list)
        or request.get("recommended_option_id") not in option_ids
    ):
        issues.append(
            _issue("C_OPTION", "recommended_option_id", "Invalid recommendation.")
        )
    repository = request.get("repository_check")
    if not isinstance(repository, Mapping) or set(repository) != {
        "status",
        "evidence_digest",
    }:
        issues.append(
            _issue(
                "C_REPOSITORY_EVIDENCE",
                "repository_check",
                "Repository check must use the closed schema.",
            )
        )
    else:
        status = repository.get("status")
        evidence_digest = repository.get("evidence_digest")
        if status not in REPOSITORY_CHECK_STATES:
            issues.append(
                _issue(
                    "C_REPOSITORY_EVIDENCE",
                    "repository_check.status",
                    "Invalid repository status.",
                )
            )
        if (status == "not_checked" and evidence_digest is not None) or (
            status != "not_checked" and not _valid_digest(evidence_digest)
        ):
            issues.append(
                _issue(
                    "C_REPOSITORY_EVIDENCE",
                    "repository_check.evidence_digest",
                    "Repository evidence digest does not match status.",
                )
            )
    return issues


def _request_id(
    *,
    task_digest: str,
    session_id: str,
    issue_id: str,
    question_digest: str,
) -> str:
    digest = contract_digest(
        {
            "task_digest": task_digest,
            "session_id": session_id,
            "issue_id": issue_id,
            "question_digest": question_digest,
        }
    )
    return f"clarify-{digest.removeprefix('sha256:')[:24]}"


def require_validated_clarification_request(request: object):
    """Reject serialized lookalikes and return a live opaque request."""

    import control_plane.host_bridge as bridge

    if (
        type(request) is not bridge.ValidatedClarificationRequest
        or not bridge._runtime_host_object_is_live(
            request, "validated_clarification_request"
        )
        or request._consumed
        or contract_digest(request.payload) != request.request_digest
    ):
        raise ValueError("C_UNTRUSTED_REQUEST: validated request is required")
    return request


def build_validated_clarification_request(
    task: Mapping[str, Any],
    *,
    issue: object,
    prompt_view: object,
    session_id: str,
    repository_observation: object,
    host_capability: object,
):
    """Bind host-framed issue, presentation and repository evidence once."""

    import control_plane.host_bridge as bridge

    if type(issue) is not bridge.FramedClarificationIssue:
        raise ValueError("C_UNTRUSTED_ISSUE: framed issue is required")
    if (
        type(prompt_view) is not bridge.FramedClarificationPromptView
        or not bridge._runtime_host_object_is_live(
            prompt_view, "framed_clarification_prompt_view"
        )
        or prompt_view._consumed
        or contract_digest(prompt_view.payload) != prompt_view.payload_digest
    ):
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: framed prompt view is required"
        )
    if (
        not bridge._runtime_host_object_is_live(
            issue, "framed_clarification_issue"
        )
        or issue._consumed
        or contract_digest(issue.payload) != issue.payload_digest
    ):
        raise ValueError("C_UNTRUSTED_ISSUE: framed issue is required")
    if (
        type(host_capability) is not bridge.HostAdapterCapability
        or not bridge._runtime_host_object_is_live(
            host_capability, "host_capability"
        )
        or host_capability._consumed
        or float(host_capability._clock()) > host_capability.freshness_deadline
    ):
        raise ValueError("C_UNTRUSTED_REQUEST: live host capability is required")
    if float(host_capability._clock()) > prompt_view.freshness_deadline:
        raise ValueError("C_PRESENTATION_UNAVAILABLE: prompt view expired")
    task_issues = validate_task_envelope(task)
    task_digest = contract_digest(task)
    if task_issues or task_digest != issue.task_digest:
        raise ValueError("C_TASK_DIGEST: task binding is invalid")
    if (
        not validate_task_id(session_id)
        or issue.payload["severity"] != clarification_level(task)
        or issue.session_id != session_id
        or prompt_view.session_id != session_id
        or issue.invocation_id != prompt_view.invocation_id
        or host_capability.session_id != session_id
        or host_capability.invocation_id != issue.invocation_id
        or prompt_view.issue_id != issue.payload["issue_id"]
        or prompt_view.question_digest != issue.payload["question_digest"]
        or prompt_view.task_digest != task_digest
    ):
        if issue.payload["severity"] != clarification_level(task):
            raise ValueError("C_SEVERITY: issue severity does not match task")
        raise ValueError("C_UNTRUSTED_REQUEST: request binding is invalid")

    if repository_observation is REPOSITORY_EVIDENCE_NOT_CHECKED:
        repository_check = {"status": "not_checked", "evidence_digest": None}
        if (
            issue.payload["issue_kind"] == "clarification"
            and issue.payload["severity"] in {"high", "critical"}
        ):
            raise ValueError(
                "C_REPOSITORY_CHECK_REQUIRED: factual high ambiguity requires evidence"
            )
    else:
        if (
            type(repository_observation)
            is not bridge.ValidatedClarificationRepositoryObservation
            or not bridge._runtime_host_object_is_live(
                repository_observation,
                "validated_clarification_repository_observation",
            )
            or repository_observation._consumed
        ):
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_UNTRUSTED: validated observation required"
            )
        if (
            float(host_capability._clock())
            > repository_observation.freshness_deadline
        ):
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_STALE: observation expired"
            )
        if (
            repository_observation.task_digest != task_digest
            or repository_observation.session_id != session_id
            or repository_observation.question_digest
            != issue.payload["question_digest"]
            or repository_observation.invocation_id != issue.invocation_id
        ):
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_BINDING: observation does not match request"
            )
        repository_check = {
            "status": repository_observation.status,
            "evidence_digest": repository_observation.evidence_digest,
        }

    request_id = _request_id(
        task_digest=task_digest,
        session_id=session_id,
        issue_id=issue.payload["issue_id"],
        question_digest=issue.payload["question_digest"],
    )
    if prompt_view.payload.get("request_id") != request_id:
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt request binding is invalid"
        )
    payload = {
        "schema_version": 1,
        "request_id": request_id,
        "task_digest": task_digest,
        "session_id": session_id,
        "issue_kind": issue.payload["issue_kind"],
        "severity": issue.payload["severity"],
        "question_digest": issue.payload["question_digest"],
        "presentation_digest": prompt_view.presentation_digest,
        "repository_check": repository_check,
        "option_ids": copy.deepcopy(issue.payload["option_ids"]),
        "recommended_option_id": issue.payload["recommended_option_id"],
    }
    contract_issues = validate_clarification_request(payload)
    if contract_issues:
        raise ValueError(f"{contract_issues[0].code}: invalid request payload")

    if not bridge._consume_runtime_host_object(
        issue, "framed_clarification_issue"
    ):
        raise ValueError("C_UNTRUSTED_ISSUE: issue is not host-issued")
    if not bridge._consume_runtime_host_object(
        prompt_view, "framed_clarification_prompt_view"
    ):
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt view is not host-issued"
        )
    if repository_observation is not REPOSITORY_EVIDENCE_NOT_CHECKED:
        if not bridge._consume_runtime_host_object(
            repository_observation,
            "validated_clarification_repository_observation",
        ):
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_REPLAY: observation was consumed"
            )
        repository_observation._consumed = True
    if not bridge._consume_runtime_host_object(
        host_capability, "host_capability"
    ):
        raise ValueError("C_UNTRUSTED_REQUEST: host capability is not issued")

    issue._consumed = True
    prompt_view._consumed = True
    host_capability._consumed = True
    request = object.__new__(bridge.ValidatedClarificationRequest)
    request._consumed = False
    request.payload = copy.deepcopy(payload)
    request.request_digest = contract_digest(payload)
    request.task_digest = task_digest
    request.session_id = session_id
    request.invocation_id = issue.invocation_id
    request.provenance = issue.provenance
    bridge._register_runtime_host_object(
        request, "validated_clarification_request"
    )
    return request


def validate_assumption_record(
    assumption: object,
    *,
    request: Mapping[str, Any],
    task_digest: str,
) -> list[ContractIssue]:
    import control_plane.host_bridge as bridge

    if (
        type(assumption) is not bridge.ValidatedAssumption
        or not bridge._runtime_host_object_is_live(
            assumption, "validated_assumption"
        )
    ):
        return [
            _issue(
                "A_UNTRUSTED_CHANNEL",
                "assumption",
                "ValidatedAssumption must be host-framed in memory.",
            )
        ]
    payload = assumption.payload
    issues: list[ContractIssue] = []
    if not isinstance(payload, Mapping) or set(payload) != _ASSUMPTION_KEYS:
        return [_issue("A_SCHEMA", "assumption", "Invalid assumption schema.")]
    if (
        request.get("severity") != "medium"
        or request.get("issue_kind") != "clarification"
    ):
        return [
            _issue(
                "A_SCHEMA",
                "assumption",
                "Assumptions apply only to medium factual clarification.",
            )
        ]
    if payload.get("schema_version") != 1:
        issues.append(_issue("A_SCHEMA", "schema_version", "Only schema 1 is supported."))
    expected_request_digest = contract_digest(request)
    if payload.get("request_digest") != expected_request_digest:
        issues.append(
            _issue("A_REQUEST_DIGEST", "request_digest", "Request digest mismatch.")
        )
    if payload.get("task_digest") != task_digest:
        issues.append(_issue("C_TASK_DIGEST", "task_digest", "Task digest mismatch."))
    if payload.get("selected_option_id") not in request.get("option_ids", []):
        issues.append(_issue("A_OPTION", "selected_option_id", "Unknown option."))
    if not _valid_digest(payload.get("statement_digest")):
        issues.append(
            _issue("A_SCHEMA", "statement_digest", "Invalid statement digest.")
        )
    if assumption.provenance not in {"model_inference", "project_policy"}:
        issues.append(
            _issue("A_UNTRUSTED_CHANNEL", "provenance", "Invalid assumption provenance.")
        )
    return issues


def validate_clarification_resolution(
    payload: Mapping[str, Any],
    *,
    request: Mapping[str, Any],
    task_digest: str,
    session_id: str,
    trusted_interaction: object,
) -> list[ContractIssue]:
    import control_plane.host_bridge as bridge

    issues: list[ContractIssue] = []
    if not isinstance(payload, Mapping) or set(payload) != _RESOLUTION_KEYS:
        issues.append(_issue("C_SCHEMA", "resolution", "Invalid resolution schema."))
    else:
        if payload.get("schema_version") != 1 or not validate_task_id(
            payload.get("resolution_id")
        ):
            issues.append(_issue("C_SCHEMA", "resolution", "Invalid resolution identity."))
        if payload.get("request_digest") != contract_digest(request):
            issues.append(
                _issue("C_REQUEST_DIGEST", "request_digest", "Request digest mismatch.")
            )
        if payload.get("task_digest") != task_digest:
            issues.append(_issue("C_TASK_DIGEST", "task_digest", "Task digest mismatch."))
        if payload.get("session_id") != session_id:
            issues.append(_issue("C_SESSION", "session_id", "Session mismatch."))
        if payload.get("selected_option_id") not in request.get("option_ids", []):
            issues.append(_issue("C_OPTION", "selected_option_id", "Unknown option."))
        if not _valid_digest(payload.get("response_digest")):
            issues.append(_issue("C_RESPONSE", "response_digest", "Invalid response digest."))
    if (
        type(trusted_interaction) is not bridge.TrustedInteraction
        or not bridge._runtime_host_object_is_live(
            trusted_interaction, "trusted_interaction"
        )
        or trusted_interaction._consumed
        or trusted_interaction.payload != dict(payload)
    ):
        issues.append(
            _issue(
                "C_UNTRUSTED_CHANNEL",
                "trusted_interaction",
                "Resolution must be wrapped by the current host interaction.",
            )
        )
    return issues


def _canonical_identity(value: Path | str) -> str | None:
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError):
        return None
    return str(path) if path.is_dir() else None


def validate_authorization(
    authorization: object,
    *,
    task_digest: str,
    session_id: str,
    repository_identity: Path | str,
    worktree_identity: Path | str,
    branch: str,
    expected_head: str,
    subject_digest: str,
    scope_paths: Sequence[str],
    effect: str,
    operation_nonce: str,
    invocation_id: str,
    now_monotonic: float,
) -> list[ContractIssue]:
    import control_plane.host_bridge as bridge

    if type(authorization) is not bridge.TrustedAuthorization:
        return [
            _issue(
                "Z_UNTRUSTED_CHANNEL",
                "authorization",
                "TrustedAuthorization must be host-issued in memory.",
            )
        ]
    if authorization._consumed:
        return [_issue("Z_REPLAY", "authorization", "Authorization was consumed.")]
    if not bridge._runtime_host_object_is_live(
        authorization, "trusted_authorization"
    ):
        return [
            _issue(
                "Z_UNTRUSTED_CHANNEL",
                "authorization",
                "TrustedAuthorization must be host-issued in memory.",
            )
        ]
    if (
        authorization.freshness_deadline
        != authorization.expires_at_monotonic
        or authorization.issued_at_monotonic
        > authorization.expires_at_monotonic
        or float(now_monotonic) > authorization.expires_at_monotonic
    ):
        return [_issue("Z_EXPIRED", "authorization", "Authorization expired.")]
    normalized = tuple(normalize_scope(item) for item in scope_paths)
    repository = _canonical_identity(repository_identity)
    worktree = _canonical_identity(worktree_identity)
    if authorization.task_digest != task_digest:
        return [_issue("Z_TASK_DIGEST", "task_digest", "Task digest mismatch.")]
    if authorization.session_id != session_id:
        return [_issue("Z_SESSION", "session_id", "Session mismatch.")]
    if effect not in TASK_EFFECTS or authorization.effect != effect:
        return [_issue("Z_EFFECT", "effect", "Effect mismatch.")]
    if (
        any(item is None for item in normalized)
        or authorization.scope_paths != tuple(str(item) for item in normalized)
    ):
        return [_issue("Z_SCOPE", "scope_paths", "Scope mismatch.")]
    if (
        repository is None
        or worktree is None
        or authorization.repository_identity != repository
        or authorization.worktree_identity != worktree
        or authorization.branch != branch
        or authorization.expected_head != expected_head
        or authorization.subject_digest != subject_digest
        or authorization.operation_nonce != operation_nonce
        or authorization.invocation_id != invocation_id
    ):
        return [_issue("Z_BINDING", "authorization", "Authorization binding mismatch.")]
    return []


def validate_irreversible_confirmation(
    confirmation: object,
    *,
    request_digest: str,
    task_digest: str,
    session_id: str,
    repository_identity: Path | str,
    worktree_identity: Path | str,
    branch: str,
    expected_head: str,
    subject_digest: str,
    scope_paths: Sequence[str],
    effect: str,
    expected_consequence_digest: str,
    authorization_id: str,
    operation_nonce: str,
    invocation_id: str,
    now_monotonic: float,
) -> list[ContractIssue]:
    import control_plane.host_bridge as bridge

    if type(confirmation) is not bridge.TrustedIrreversibleConfirmation:
        return [
            _issue(
                "I_UNTRUSTED_CHANNEL",
                "confirmation",
                "Confirmation must be host-issued in memory.",
            )
        ]
    if confirmation._consumed:
        return [_issue("I_REPLAY", "confirmation", "Confirmation was consumed.")]
    if not bridge._runtime_host_object_is_live(
        confirmation, "trusted_irreversible_confirmation"
    ):
        return [
            _issue(
                "I_UNTRUSTED_CHANNEL",
                "confirmation",
                "Confirmation must be host-issued in memory.",
            )
        ]
    if (
        confirmation.freshness_deadline
        != confirmation.expires_at_monotonic
        or confirmation.issued_at_monotonic
        > confirmation.expires_at_monotonic
        or float(now_monotonic) > confirmation.expires_at_monotonic
    ):
        return [_issue("I_EXPIRED", "confirmation", "Confirmation expired.")]
    payload = confirmation.payload
    if not isinstance(payload, Mapping) or set(payload) != _CONFIRMATION_KEYS:
        return [_issue("I_SCHEMA", "confirmation", "Invalid confirmation schema.")]
    if contract_digest(payload) != confirmation.payload_digest:
        return [
            _issue(
                "I_UNTRUSTED_CHANNEL",
                "confirmation",
                "Confirmation payload changed after host framing.",
            )
        ]
    if payload.get("request_digest") != request_digest:
        return [_issue("I_REQUEST_DIGEST", "request_digest", "Request mismatch.")]
    if payload.get("task_digest") != task_digest:
        return [_issue("I_TASK_DIGEST", "task_digest", "Task mismatch.")]
    if payload.get("session_id") != session_id:
        return [_issue("I_SESSION", "session_id", "Session mismatch.")]
    if payload.get("effect") != effect:
        return [_issue("I_EFFECT", "effect", "Effect mismatch.")]
    normalized = tuple(normalize_scope(item) for item in scope_paths)
    if (
        any(item is None for item in normalized)
        or tuple(payload.get("scope_paths", []))
        != tuple(str(item) for item in normalized)
    ):
        return [_issue("I_SCOPE", "scope_paths", "Scope mismatch.")]
    if payload.get("consequence_digest") != expected_consequence_digest:
        return [
            _issue(
                "I_CONSEQUENCE_DIGEST",
                "consequence_digest",
                "Consequence digest mismatch.",
            )
        ]
    if (
        confirmation.repository_identity
        != _canonical_identity(repository_identity)
        or confirmation.worktree_identity != _canonical_identity(worktree_identity)
        or confirmation.branch != branch
        or confirmation.expected_head != expected_head
        or confirmation.subject_digest != subject_digest
        or confirmation.authorization_id != authorization_id
        or confirmation.operation_nonce != operation_nonce
        or confirmation.invocation_id != invocation_id
    ):
        return [_issue("I_BINDING", "confirmation", "Confirmation binding mismatch.")]
    return []


def clarification_level(task: Mapping[str, Any]) -> str:
    uncertainty = int(task["risk"]["uncertainty"])
    return ("low", "medium", "high", "critical")[uncertainty]


def evaluate_clarification_gate(
    task: Mapping[str, Any],
    *,
    request: object | None,
    assumption: object | None,
    resolution: object | None,
    irreversible_confirmation: object | None,
    authorization: object | None,
) -> dict[str, Any]:
    """Return the deterministic Task 3 gate without mutating route or lifecycle."""

    level = clarification_level(task)
    task_digest = contract_digest(task)
    status = "autonomous"
    reasons = ["CLARIFY_LOW_AUTONOMOUS"]
    bound_request = None
    if request is not None:
        try:
            bound_request = require_validated_clarification_request(request)
        except ValueError:
            bound_request = None
    if bound_request is not None and bound_request.task_digest != task_digest:
        bound_request = None

    if level == "critical":
        status = "blocked"
        reasons = ["C_REFRAME_REQUIRED"]
    elif level == "medium":
        if (
            bound_request is not None
            and bound_request.payload["issue_kind"] == "decision_approval"
        ):
            if resolution is None or validate_clarification_resolution(
                resolution.payload if hasattr(resolution, "payload") else {},
                request=bound_request.payload,
                task_digest=task_digest,
                session_id=bound_request.payload["session_id"],
                trusted_interaction=resolution,
            ):
                status = "ask_user"
                reasons = ["CLARIFY_DECISION_APPROVAL_REQUIRED"]
            else:
                status = "resolved"
                reasons = ["CLARIFY_DECISION_APPROVED"]
        elif bound_request is None or assumption is None:
            status = "assumption_required"
            reasons = ["CLARIFY_ASSUMPTION_REQUIRED"]
        elif validate_assumption_record(
            assumption,
            request=bound_request.payload,
            task_digest=task_digest,
        ):
            status = "assumption_required"
            reasons = ["CLARIFY_ASSUMPTION_INVALID"]
        else:
            status = "resolved"
            reasons = ["CLARIFY_ASSUMPTION_ACCEPTED"]
    elif level == "high":
        if bound_request is None:
            status = "inspect_repository"
            reasons = ["CLARIFY_REPOSITORY_REQUIRED"]
        else:
            repository_status = bound_request.payload["repository_check"]["status"]
            if (
                bound_request.payload["issue_kind"] == "clarification"
                and repository_status == "resolved"
            ):
                status = "resolved"
                reasons = ["CLARIFY_REPOSITORY_RESOLVED"]
            elif resolution is None:
                status = "ask_user"
                reasons = ["CLARIFY_REPOSITORY_UNRESOLVED"]
            elif validate_clarification_resolution(
                resolution.payload if hasattr(resolution, "payload") else {},
                request=bound_request.payload,
                task_digest=task_digest,
                session_id=bound_request.payload["session_id"],
                trusted_interaction=resolution,
            ):
                status = "ask_user"
                reasons = ["CLARIFY_RESOLUTION_INVALID"]
            else:
                status = "resolved"
                reasons = ["CLARIFY_RESOLVED"]

    requires_irreversible_confirmation = (
        int(task["risk"]["irreversibility"]) == 3
        or any(
            effect.get("name") == "destructive"
            for effect in task.get("effects", [])
            if isinstance(effect, Mapping)
        )
    )
    if status in {"autonomous", "resolved"} and requires_irreversible_confirmation:
        import control_plane.host_bridge as bridge

        expected_subject_digest = (
            bound_request.request_digest
            if bound_request is not None
            else task_digest
        )
        expected_scope_paths = tuple(
            str(normalize_scope(path)) for path in task.get("scope_paths", [])
        )
        required_effects = {
            str(effect.get("name"))
            for effect in task.get("effects", [])
            if isinstance(effect, Mapping)
            and effect.get("name") not in {None, "local_read"}
        }
        authorization_is_bound = bool(
            type(authorization) is bridge.TrustedAuthorization
            and bridge._runtime_host_object_is_live(
                authorization, "trusted_authorization"
            )
            and not authorization._consumed
            and authorization.task_digest == task_digest
            and authorization.subject_digest == expected_subject_digest
            and authorization.scope_paths == expected_scope_paths
            and authorization.effect in required_effects
        )
        if (
            not authorization_is_bound
        ):
            status = "authorization_required"
            reasons = ["CLARIFY_AUTHORIZATION_REQUIRED"]
        else:
            confirmation_is_bound = bool(
                type(irreversible_confirmation)
                is bridge.TrustedIrreversibleConfirmation
                and bridge._runtime_host_object_is_live(
                    irreversible_confirmation,
                    "trusted_irreversible_confirmation",
                )
                and not irreversible_confirmation._consumed
                and contract_digest(irreversible_confirmation.payload)
                == irreversible_confirmation.payload_digest
                and irreversible_confirmation.payload.get("request_digest")
                == expected_subject_digest
                and irreversible_confirmation.payload.get("task_digest")
                == task_digest
                and irreversible_confirmation.payload.get("session_id")
                == authorization.session_id
                and tuple(
                    irreversible_confirmation.payload.get("scope_paths", [])
                )
                == authorization.scope_paths
                and irreversible_confirmation.payload.get("effect")
                == authorization.effect
                and irreversible_confirmation.authorization_id
                == authorization.authorization_id
                and irreversible_confirmation.operation_nonce
                == authorization.operation_nonce
                and irreversible_confirmation.repository_identity
                == authorization.repository_identity
                and irreversible_confirmation.worktree_identity
                == authorization.worktree_identity
                and irreversible_confirmation.branch == authorization.branch
                and irreversible_confirmation.expected_head
                == authorization.expected_head
                and irreversible_confirmation.subject_digest
                == authorization.subject_digest
                and irreversible_confirmation.invocation_id
                == authorization.invocation_id
            )
            if not confirmation_is_bound:
                status = "confirmation_required"
                reasons = ["CLARIFY_CONFIRMATION_REQUIRED"]

    blocking = status in {
        "assumption_required",
        "inspect_repository",
        "ask_user",
        "authorization_required",
        "confirmation_required",
        "blocked",
    }
    blocked_effects = sorted(
        {
            str(effect.get("name"))
            for effect in task.get("effects", [])
            if isinstance(effect, Mapping)
            and effect.get("name") not in {None, "local_read"}
        }
    )
    context = {
        "level": level,
        "status": status,
        "task_digest": task_digest,
        "request_digest": (
            bound_request.request_digest if bound_request is not None else None
        ),
        "reason_codes": reasons,
    }
    return {
        "level": level,
        "status": status,
        "decision_ready": not blocking,
        "next_action": {
            "autonomous": "continue",
            "assumption_required": "record_explicit_assumption",
            "inspect_repository": "inspect_repository",
            "ask_user": "ask_one_material_question",
            "authorization_required": "request_effect_authorization",
            "confirmation_required": "request_irreversible_confirmation",
            "blocked": "reframe_task",
            "resolved": "continue",
        }[status],
        "blocked_effects": blocked_effects if blocking else [],
        "context_digest": contract_digest(context),
        "reason_codes": reasons,
    }
