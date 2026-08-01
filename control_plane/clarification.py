"""Pure, diagnostic clarification contracts for the local-audit kernel.

Clarification data is inert.  This module can identify material ambiguity and
validate a bounded diagnostic request, but it never constructs host authority,
persists a sidecar, or treats serialized input as a user decision.
"""

from __future__ import annotations

from typing import Any, Mapping

from control_plane.contracts import (
    SHA256_DIGEST,
    ContractIssue,
    contract_digest,
    validate_task_id,
)


CLARIFICATION_LEVELS = frozenset({"low", "medium", "high", "critical"})
CLARIFICATION_KINDS = frozenset({"clarification", "decision_approval"})
REPOSITORY_CHECK_STATES = frozenset(
    {"not_checked", "resolved", "unresolved", "conflicting"}
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


def _issue(code: str, path: str, message: str) -> ContractIssue:
    return ContractIssue(code, path, message)


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and SHA256_DIGEST.fullmatch(value) is not None


def _valid_option_ids(value: object) -> bool:
    return bool(
        isinstance(value, list)
        and 2 <= len(value) <= 3
        and len(set(value)) == len(value)
        and all(validate_task_id(item) for item in value)
    )


def validate_clarification_request(
    request: Mapping[str, Any],
) -> list[ContractIssue]:
    """Validate one bounded diagnostic request without granting authority."""

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
        issues.append(
            _issue("C_SCHEMA", "schema_version", "Only schema 1 is supported.")
        )
    if not validate_task_id(request.get("request_id")):
        issues.append(_issue("C_SCHEMA", "request_id", "Unsafe request ID."))
    if not _valid_digest(request.get("task_digest")):
        issues.append(
            _issue("C_TASK_DIGEST", "task_digest", "Invalid task digest.")
        )
    if not validate_task_id(request.get("session_id")):
        issues.append(_issue("C_SESSION", "session_id", "Invalid session ID."))
    if request.get("issue_kind") not in CLARIFICATION_KINDS:
        issues.append(
            _issue("C_ISSUE_KIND", "issue_kind", "Unsupported issue kind.")
        )
    if request.get("severity") not in CLARIFICATION_LEVELS:
        issues.append(_issue("C_SEVERITY", "severity", "Unsupported severity."))
    if not _valid_digest(request.get("question_digest")):
        issues.append(
            _issue(
                "C_QUESTION_DIGEST",
                "question_digest",
                "Invalid question digest.",
            )
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


def clarification_level(task: Mapping[str, Any]) -> str:
    """Map validated uncertainty 0..3 to the closed clarification level."""

    uncertainty = int(task["risk"]["uncertainty"])
    return ("low", "medium", "high", "critical")[uncertainty]


def evaluate_clarification_gate(
    task: Mapping[str, Any],
    *,
    request: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a pure diagnostic gate; never authorize or resolve a write."""

    level = clarification_level(task)
    task_digest = contract_digest(task)
    blocked_effects = sorted(
        {
            str(effect.get("name"))
            for effect in task.get("effects", [])
            if isinstance(effect, Mapping)
            and effect.get("name") not in {None, "local_read"}
        }
    )
    request_digest: str | None = None
    request_valid = False
    if request is not None:
        request_valid = not validate_clarification_request(request)
        request_valid = bool(
            request_valid and request.get("task_digest") == task_digest
        )
        if request_valid:
            request_digest = contract_digest(request)

    if level == "low":
        status = "autonomous"
        decision_ready = True
        next_action = "continue"
        reasons = ["CLARIFY_LOW_AUTONOMOUS"]
        blocked_effects = []
    elif level == "critical":
        status = "blocked"
        decision_ready = False
        next_action = "reframe_task"
        reasons = ["C_REFRAME_REQUIRED"]
    else:
        status = "pending_host_capability"
        decision_ready = False
        next_action = "wait_for_host_capability"
        reasons = [
            (
                "CLARIFY_REQUEST_DIAGNOSTIC_ONLY"
                if request_valid
                else "CLARIFY_HOST_CAPABILITY_PENDING"
            )
        ]

    result = {
        "level": level,
        "status": status,
        "decision_ready": decision_ready,
        "next_action": next_action,
        "blocked_effects": blocked_effects,
        "reason_codes": reasons,
    }
    result["context_digest"] = contract_digest(
        {
            "level": level,
            "status": status,
            "task_digest": task_digest,
            "request_digest": request_digest,
            "reason_codes": reasons,
        }
    )
    return result
