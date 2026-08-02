"""Fail-closed shadow contracts for exact Codex thread audit references."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from typing import Any, Mapping

from control_plane.contracts import (
    SHA256_DIGEST,
    canonical_json,
    contract_digest,
    validate_task_id,
    validate_task_envelope,
)


CROSS_THREAD_AUDIT_RESOURCE_ID = "agent.cross-thread-audit-read-shadow"
_THREAD_ID = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
)
_THREAD_REFERENCE = re.compile(
    rf"\Acodex://threads/(?P<thread_id>{_THREAD_ID})\Z",
    re.ASCII,
)
_UNKNOWN_REASON = "HOST_CONSUMER_UNAVAILABLE"
_UNKNOWN_LIMITATION = "Native Codex thread-read consumer unavailable."


@dataclass(frozen=True)
class CrossThreadAuditLookupRequest:
    task_id: str
    task_digest: str
    source_reference: str
    source_thread_id: str
    auditor_reference: str | None
    auditor_thread_id: str | None
    request_digest: str


def _thread_id(reference: object) -> str | None:
    if not isinstance(reference, str):
        return None
    match = _THREAD_REFERENCE.fullmatch(reference)
    return match.group("thread_id") if match is not None else None


def _valid_thread_id(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and _thread_id(f"codex://threads/{value}") == value
    )


def select_cross_thread_audit_resource(reference: object) -> str | None:
    """Select the shadow resource only for one exact closed reference."""

    return CROSS_THREAD_AUDIT_RESOURCE_ID if _thread_id(reference) else None


def _validate_request(request: object) -> CrossThreadAuditLookupRequest:
    if type(request) is not CrossThreadAuditLookupRequest:
        raise ValueError(
            "E_CROSS_THREAD_AUDIT_REQUEST: typed lookup request is required"
        )
    semantic = {
        "task_id": request.task_id,
        "task_digest": request.task_digest,
        "source_reference": request.source_reference,
        "source_thread_id": request.source_thread_id,
        "auditor_reference": request.auditor_reference,
        "auditor_thread_id": request.auditor_thread_id,
    }
    if (
        not validate_task_id(request.task_id)
        or SHA256_DIGEST.fullmatch(request.task_digest) is None
        or _thread_id(request.source_reference) != request.source_thread_id
        or (
            request.auditor_reference is None
            and request.auditor_thread_id is not None
        )
        or (
            request.auditor_reference is not None
            and _thread_id(request.auditor_reference)
            != request.auditor_thread_id
        )
        or request.source_thread_id == request.auditor_thread_id
        or request.request_digest != contract_digest(semantic)
    ):
        raise ValueError(
            "E_CROSS_THREAD_AUDIT_REQUEST: lookup binding is invalid"
        )
    return request


def prepare_cross_thread_audit_lookup(
    task: Mapping[str, Any],
    *,
    source_reference: str,
    auditor_reference: str | None = None,
) -> tuple[dict[str, Any], CrossThreadAuditLookupRequest]:
    """Bind exact references to the router's explicit-resource seam."""

    issues = validate_task_envelope(task)
    if issues:
        issue = issues[0]
        raise ValueError(f"{issue.code}: {issue.path}: {issue.message}")
    source_thread_id = _thread_id(source_reference)
    auditor_thread_id = (
        _thread_id(auditor_reference)
        if auditor_reference is not None
        else None
    )
    if (
        source_thread_id is None
        or (auditor_reference is not None and auditor_thread_id is None)
        or source_thread_id == auditor_thread_id
    ):
        raise ValueError(
            "E_CROSS_THREAD_AUDIT_REFERENCE: exact Codex thread references "
            "are required"
        )

    prepared = copy.deepcopy(dict(task))
    explicit = {
        str(item) for item in prepared.get("explicit_resources", [])
    }
    explicit.add(CROSS_THREAD_AUDIT_RESOURCE_ID)
    prepared["explicit_resources"] = sorted(explicit)
    prepared_issues = validate_task_envelope(prepared)
    if prepared_issues:
        issue = prepared_issues[0]
        raise ValueError(f"{issue.code}: {issue.path}: {issue.message}")

    semantic = {
        "task_id": str(prepared["task_id"]),
        "task_digest": contract_digest(prepared),
        "source_reference": source_reference,
        "source_thread_id": source_thread_id,
        "auditor_reference": auditor_reference,
        "auditor_thread_id": auditor_thread_id,
    }
    request = CrossThreadAuditLookupRequest(
        **semantic,
        request_digest=contract_digest(semantic),
    )
    return prepared, request


def _unknown_evidence_digest(
    *,
    source_thread_id: str,
    auditor_thread_id: str | None,
    task_id: str,
    task_digest: str,
) -> str:
    return contract_digest(
        {
            "source_thread_id": source_thread_id,
            "auditor_thread_id": auditor_thread_id,
            "task_id": task_id,
            "task_digest": task_digest,
            "reason_codes": [_UNKNOWN_REASON],
        }
    )


def evaluate_cross_thread_audit_lookup(
    request: CrossThreadAuditLookupRequest,
) -> dict[str, Any]:
    """Return UNKNOWN until a native, attested thread-read consumer exists."""

    bound = _validate_request(request)
    capsule: dict[str, Any] = {
        "schema_version": 1,
        "kind": "cross_thread_audit_capsule",
        "source": {
            "thread_id": bound.source_thread_id,
            "auditor_thread_id": bound.auditor_thread_id,
            "observed_thread_id": None,
            "observed_auditor_thread_id": None,
            "auditor_parent_thread_id": None,
            "project": None,
            "repository": None,
            "state": "unavailable",
            "auditor_state": None,
            "head": None,
            "base": None,
            "diff_digest": None,
            "subject_digest": None,
        },
        "consumer": {
            "task_id": bound.task_id,
            "task_digest": bound.task_digest,
            "project": None,
            "repository": None,
            "head": None,
            "base": None,
            "diff_digest": None,
            "subject_digest": None,
        },
        "verdict": "UNKNOWN",
        "findings": [],
        "tests": [],
        "limitations": [_UNKNOWN_LIMITATION],
        "evidence_digest": _unknown_evidence_digest(
            source_thread_id=bound.source_thread_id,
            auditor_thread_id=bound.auditor_thread_id,
            task_id=bound.task_id,
            task_digest=bound.task_digest,
        ),
        "freshness": "UNKNOWN",
        "reason_codes": [_UNKNOWN_REASON],
        "authorizes": False,
    }
    capsule["capsule_digest"] = contract_digest(capsule)
    return capsule


def render_cross_thread_audit_capsule(capsule: Mapping[str, Any]) -> str:
    """Serialize only the closed UNKNOWN capsule supported in shadow v1."""

    top_level = {
        "schema_version",
        "kind",
        "source",
        "consumer",
        "verdict",
        "findings",
        "tests",
        "limitations",
        "evidence_digest",
        "freshness",
        "reason_codes",
        "authorizes",
        "capsule_digest",
    }
    source_keys = {
        "thread_id",
        "auditor_thread_id",
        "observed_thread_id",
        "observed_auditor_thread_id",
        "auditor_parent_thread_id",
        "project",
        "repository",
        "state",
        "auditor_state",
        "head",
        "base",
        "diff_digest",
        "subject_digest",
    }
    consumer_keys = {
        "task_id",
        "task_digest",
        "project",
        "repository",
        "head",
        "base",
        "diff_digest",
        "subject_digest",
    }
    source = capsule.get("source")
    consumer = capsule.get("consumer")
    supplied_digest = capsule.get("capsule_digest")
    unsigned = {
        key: value for key, value in capsule.items() if key != "capsule_digest"
    }
    source_null_fields = {
        "observed_thread_id",
        "observed_auditor_thread_id",
        "auditor_parent_thread_id",
        "project",
        "repository",
        "auditor_state",
        "head",
        "base",
        "diff_digest",
        "subject_digest",
    }
    consumer_null_fields = {
        "project",
        "repository",
        "head",
        "base",
        "diff_digest",
        "subject_digest",
    }
    expected_evidence_digest = None
    if isinstance(source, Mapping) and isinstance(consumer, Mapping):
        source_thread_id = source.get("thread_id")
        auditor_thread_id = source.get("auditor_thread_id")
        task_id = consumer.get("task_id")
        task_digest = consumer.get("task_digest")
        if (
            _valid_thread_id(source_thread_id)
            and (
                auditor_thread_id is None
                or _valid_thread_id(auditor_thread_id)
            )
            and source_thread_id != auditor_thread_id
            and validate_task_id(task_id)
            and isinstance(task_digest, str)
            and SHA256_DIGEST.fullmatch(task_digest) is not None
        ):
            expected_evidence_digest = _unknown_evidence_digest(
                source_thread_id=source_thread_id,
                auditor_thread_id=auditor_thread_id,
                task_id=task_id,
                task_digest=task_digest,
            )

    if (
        set(capsule) != top_level
        or capsule.get("schema_version") != 1
        or capsule.get("kind") != "cross_thread_audit_capsule"
        or not isinstance(source, Mapping)
        or set(source) != source_keys
        or not isinstance(consumer, Mapping)
        or set(consumer) != consumer_keys
        or expected_evidence_digest is None
        or any(source.get(field) is not None for field in source_null_fields)
        or source.get("state") != "unavailable"
        or any(consumer.get(field) is not None for field in consumer_null_fields)
        or capsule.get("verdict") != "UNKNOWN"
        or capsule.get("findings") != []
        or capsule.get("tests") != []
        or capsule.get("limitations") != [_UNKNOWN_LIMITATION]
        or capsule.get("evidence_digest") != expected_evidence_digest
        or capsule.get("freshness") != "UNKNOWN"
        or capsule.get("reason_codes") != [_UNKNOWN_REASON]
        or capsule.get("authorizes") is not False
        or not isinstance(supplied_digest, str)
        or SHA256_DIGEST.fullmatch(supplied_digest) is None
        or supplied_digest != contract_digest(unsigned)
    ):
        raise ValueError(
            "E_CROSS_THREAD_AUDIT_CAPSULE: closed capsule validation failed"
        )
    rendered = canonical_json(capsule)
    if len(rendered.encode("utf-8")) > 4096:
        raise ValueError(
            "E_CROSS_THREAD_AUDIT_BUDGET: capsule exceeds 4096 bytes"
        )
    return rendered
