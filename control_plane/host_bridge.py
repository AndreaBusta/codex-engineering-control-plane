"""Host-bound observations that cannot be reconstructed from serialized input."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import copy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from typing import Callable, Iterator, Mapping
from uuid import uuid4

from control_plane.contracts import (
    RESOURCE_ID,
    SHA256_DIGEST,
    TASK_EFFECTS,
    contract_digest,
    safe_scope_path,
    validate_task_id,
    validate_task_envelope,
)
from control_plane.resource_registry import (
    build_inventory,
    registry_contract_digest,
    validate_inventory,
)
from control_plane.repository import (
    assert_no_external_git_filters as _assert_no_external_git_filters,
    trusted_git_argv,
    trusted_git_environment,
)
from control_plane.scopes import normalize_scope


_GIT_OBJECT_ID = re.compile(r"^[0-9a-f]{40,64}$", re.ASCII)
_GITHUB_HTTPS_REMOTE = re.compile(
    r"https://github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repository>[A-Za-z0-9_.-]+?)(?:\.git)?",
    re.ASCII,
)
_GITHUB_REPOSITORY_IDENTITY = re.compile(
    r"(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repository>[A-Za-z0-9_.-]+)",
    re.ASCII,
)
_GITHUB_PULL_REQUEST_HTTPS_URL = re.compile(
    r"https://github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/"
    r"(?P<repository>[A-Za-z0-9_.-]+)/"
    r"pull/(?P<number>[1-9][0-9]*)",
    re.ASCII,
)
_FEATURE_PUSH_CLAIM_LOCK = threading.Lock()
_FEATURE_PUSH_OPERATIONS: dict[int, object] = {}
_PR_MUTATION_CLAIM_LOCK = threading.Lock()
_CAPABILITY_CONSUMPTION_LOCK = threading.Lock()
_INTEGRATION_TICKET_LOCK = threading.Lock()
_INTEGRATION_TICKETS: dict[
    str, tuple[str, object, datetime, datetime, datetime]
] = {}
_THREAD_LOCK_TYPE = type(threading.Lock())


_OUTCOME_REMOTE_NAME = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", re.ASCII
)
_OUTCOME_BRANCH = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$", re.ASCII
)
_OUTCOME_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?Z$",
    re.ASCII,
)
_INTEGRATION_EFFECT_PLAN_MAX_TTL_SECONDS = 300.0
_INTEGRATION_READY_MAX_AGE_SECONDS = 30.0
_OUTCOME_EFFECT_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "remote",
        "remote_url",
        "remote_url_digest",
        "remote_identity_digest",
        "base",
        "branch",
        "head_sha",
        "scope_paths",
        "subject_digest",
        "policy_digest",
        "effect",
        "title",
        "title_digest",
        "body",
        "body_digest",
        "draft",
        "operation",
        "operation_digest",
        "required_checks",
        "argv",
        "argv_digest",
        "observation_argv",
        "observation_argv_digest",
        "observe_before_retry",
        "authorizes",
        "plan_digest",
    }
)
_INTEGRATION_EFFECT_PLAN_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "remote",
        "remote_url",
        "remote_url_digest",
        "remote_identity",
        "remote_identity_digest",
        "base",
        "branch",
        "head_sha",
        "scope_paths",
        "subject_digest",
        "policy_digest",
        "effect",
        "integration_strategy",
        "pull_request_number",
        "pull_request_url",
        "pull_request_digest",
        "checks_digest",
        "prepared_at",
        "expires_at",
        "argv",
        "argv_digest",
        "observation_argv",
        "observation_argv_digest",
        "observe_before_retry",
        "authorizes",
        "plan_digest",
    }
)
_INTEGRATION_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "remote",
        "remote_url_digest",
        "remote_identity",
        "base",
        "branch",
        "head_sha",
        "subject_digest",
        "policy_digest",
        "effect",
        "integration_strategy",
        "pull_request_number",
        "pull_request_url",
        "pull_request_digest",
        "checks_digest",
        "effect_plan_digest",
        "status",
        "observed_repository",
        "observed_base",
        "observed_branch",
        "observed_head_sha",
        "observed_pr_number",
        "observed_pr_url",
        "observed_pr_state",
        "observed_pr_draft",
        "observed_strategy",
        "observed_checks_digest",
        "observed_merge_sha",
        "observed_at",
        "authorizes",
        "receipt_digest",
    }
)
_BASE_REFRESH_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "repository",
        "remote",
        "remote_url",
        "remote_url_digest",
        "remote_identity",
        "remote_identity_digest",
        "base",
        "base_ref",
        "policy_digest",
        "effect_plan_digest",
        "integration_receipt_digest",
        "merge_sha",
        "refresh_argv",
        "refresh_argv_digest",
        "status",
        "observed_ref",
        "observed_sha",
        "observed_at",
        "authorizes",
        "receipt_digest",
    }
)
_BASE_VERIFICATION_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "repository",
        "remote",
        "base",
        "base_ref",
        "policy_digest",
        "effect_plan_digest",
        "integration_receipt_digest",
        "refresh_receipt_digest",
        "merge_sha",
        "status",
        "reason_code",
        "observed_base_sha",
        "contained",
        "observed_at",
        "authorizes",
        "receipt_digest",
    }
)
_REMOTE_OUTCOME_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "remote",
        "remote_url",
        "remote_url_digest",
        "remote_identity_digest",
        "base",
        "branch",
        "head_sha",
        "scope_paths",
        "subject_digest",
        "policy_digest",
        "effect",
        "title_digest",
        "body_digest",
        "draft",
        "effect_plan_digest",
        "status",
        "observed_repository",
        "observed_remote",
        "observed_base",
        "observed_branch",
        "observed_head_sha",
        "observed_pr_number",
        "observed_pr_url",
        "observed_pr_draft",
        "disposition",
        "observation_kind",
        "required_check_digests",
        "check_results",
        "feedback",
        "observed_at",
        "authorizes",
        "receipt_digest",
    }
)
_OUTCOME_CONTRACT_MAX_BYTES = 16_384
_OUTCOME_SCOPE_MAX_ITEMS = 64


def _outcome_digest(value: object) -> bool:
    return isinstance(value, str) and SHA256_DIGEST.fullmatch(value) is not None


def _outcome_scope(value: object) -> bool:
    return (
        isinstance(value, tuple)
        and 1 <= len(value) <= _OUTCOME_SCOPE_MAX_ITEMS
        and tuple(sorted(set(value))) == value
        and all(
            isinstance(path, str)
            and len(path.encode("utf-8")) <= 512
            and safe_scope_path(path)
            for path in value
        )
    )


def _outcome_contract_size(value: Mapping[str, object]) -> int:
    try:
        return len(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        return _OUTCOME_CONTRACT_MAX_BYTES + 1


def _outcome_time(value: object) -> datetime | None:
    if not isinstance(value, str) or _OUTCOME_TIMESTAMP.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    return parsed if parsed.tzinfo == timezone.utc else None


_PR_SECRET_LIKE = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"(?:bearer|token|password|secret)\s*[:=]\s*\S{8,})",
    re.ASCII,
)


def _outcome_pr_content(title: object, body: object) -> bool:
    if (
        not isinstance(title, str)
        or title != title.strip()
        or not 1 <= len(title) <= 180
        or len(title.encode("utf-8")) > 512
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in title)
        or not isinstance(body, str)
        or not 1 <= len(body.encode("utf-8")) <= 32_768
        or any(
            (ord(character) < 32 and character not in {"\n", "\t"})
            or 127 <= ord(character) <= 159
            for character in body
        )
        or _PR_SECRET_LIKE.search(title) is not None
        or _PR_SECRET_LIKE.search(body) is not None
    ):
        return False
    return True


def _outcome_required_checks(value: object, *, required: bool) -> bool:
    if not isinstance(value, tuple) or len(value) > 64 or (required and not value):
        return False
    if not all(
        isinstance(item, tuple)
        and len(item) == 4
        and isinstance(item[0], str)
        and item[0] == item[0].strip()
        and bool(item[0])
        and isinstance(item[1], str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", item[1]) is not None
        and isinstance(item[2], tuple)
        and item[2] == tuple(sorted(set(item[2])))
        and bool(item[2])
        and all(conclusion in {"SUCCESS", "NEUTRAL", "SKIPPED"} for conclusion in item[2])
        and _outcome_digest(item[3])
        and item[3] == contract_digest(
            {"name": item[0], "app": item[1], "conclusions": item[2]}
        )
        for item in value
    ):
        return False
    return tuple(item[3] for item in value) == tuple(sorted(item[3] for item in value)) and len({item[3] for item in value}) == len(value)


def _outcome_identity_for_url(remote_url: str) -> str:
    if not isinstance(remote_url, str) or not remote_url:
        raise ValueError("E_OUTCOME_EFFECT_PLAN: remote URL is invalid")
    local_remote = Path(remote_url)
    if local_remote.is_absolute():
        return f"local:{local_remote.resolve()}"
    match = _GITHUB_HTTPS_REMOTE.fullmatch(remote_url)
    if match is None or "@" in remote_url:
        raise ValueError(
            "E_OUTCOME_EFFECT_PLAN: remote URL is not credential-free"
        )
    return (
        f"github:{match.group('owner').lower()}/"
        f"{match.group('repository').lower()}"
    )


@dataclass(frozen=True)
class IntegrationEffectPlanV1:
    """Closed, non-authorizing proposal for one explicit squash merge."""

    schema_version: int
    kind: str
    task_id: str
    task_digest: str
    run_plan_digest: str
    requested_outcome: str
    repository: str
    remote: str
    remote_url: str
    remote_url_digest: str
    remote_identity: str
    remote_identity_digest: str
    base: str
    branch: str
    head_sha: str
    scope_paths: tuple[str, ...]
    subject_digest: str
    policy_digest: str
    effect: str
    integration_strategy: str
    pull_request_number: int
    pull_request_url: str
    pull_request_digest: str
    checks_digest: str
    prepared_at: str
    expires_at: str
    argv: tuple[str, ...]
    argv_digest: str
    observation_argv: tuple[str, ...]
    observation_argv_digest: str
    observe_before_retry: bool
    authorizes: bool
    plan_digest: str

    def __post_init__(self) -> None:
        expected_argv = (
            "gh",
            "pr",
            "merge",
            str(self.pull_request_number),
            "--repo",
            self.remote_identity,
            "--match-head-commit",
            self.head_sha,
            "--squash",
        )
        expected_observation = (
            "gh",
            "pr",
            "view",
            str(self.pull_request_number),
            "--repo",
            self.remote_identity,
            "--json",
            "number,url,state,isDraft,baseRefName,headRefName,headRefOid,mergeCommit,mergedAt",
        )
        prepared = _outcome_time(self.prepared_at)
        expires = _outcome_time(self.expires_at)
        core = {
            key: value
            for key, value in self.to_dict().items()
            if key != "plan_digest"
        }
        try:
            pr_repository, pr_number = _github_pull_request_url_identity(
                self.pull_request_url,
                code="E_INTEGRATION_EFFECT_PLAN",
            )
        except ValueError:
            pr_repository, pr_number = None, None
        if (
            self.schema_version != 1
            or self.kind != "IntegrationEffectPlanV1"
            or not validate_task_id(self.task_id)
            or not all(
                _outcome_digest(value)
                for value in (
                    self.task_digest,
                    self.run_plan_digest,
                    self.remote_url_digest,
                    self.remote_identity_digest,
                    self.subject_digest,
                    self.policy_digest,
                    self.pull_request_digest,
                    self.checks_digest,
                    self.argv_digest,
                    self.observation_argv_digest,
                    self.plan_digest,
                )
            )
            or self.requested_outcome != "integration"
            or not isinstance(self.repository, str)
            or not Path(self.repository).is_absolute()
            or str(Path(self.repository).resolve()) != self.repository
            or _OUTCOME_REMOTE_NAME.fullmatch(self.remote) is None
            or self.remote_url_digest != contract_digest(self.remote_url)
            or self.remote_identity
            != _canonical_github_repository_from_url(
                self.remote_url, code="E_INTEGRATION_EFFECT_PLAN"
            )
            or self.remote_identity_digest != contract_digest(self.remote_identity)
            or pr_repository != self.remote_identity
            or pr_number != self.pull_request_number
            or _OUTCOME_BRANCH.fullmatch(self.base) is None
            or _OUTCOME_BRANCH.fullmatch(self.branch) is None
            or self.base == self.branch
            or _GIT_OBJECT_ID.fullmatch(self.head_sha) is None
            or not _outcome_scope(self.scope_paths)
            or self.effect != "integration"
            or self.integration_strategy != "squash"
            or not isinstance(self.pull_request_number, int)
            or isinstance(self.pull_request_number, bool)
            or self.pull_request_number <= 0
            or prepared is None
            or expires is None
            or prepared >= expires
            or (expires - prepared).total_seconds()
            > _INTEGRATION_EFFECT_PLAN_MAX_TTL_SECONDS
            or self.argv != expected_argv
            or self.observation_argv != expected_observation
            or self.argv_digest != contract_digest(list(self.argv))
            or self.observation_argv_digest
            != contract_digest(list(self.observation_argv))
            or self.observe_before_retry is not True
            or self.authorizes is not False
            or self.plan_digest != contract_digest(core)
            or _outcome_contract_size(self.to_dict()) > _OUTCOME_CONTRACT_MAX_BYTES
        ):
            raise ValueError(
                "E_INTEGRATION_EFFECT_PLAN: closed squash plan is invalid"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "task_id": self.task_id,
            "task_digest": self.task_digest,
            "run_plan_digest": self.run_plan_digest,
            "requested_outcome": self.requested_outcome,
            "repository": self.repository,
            "remote": self.remote,
            "remote_url": self.remote_url,
            "remote_url_digest": self.remote_url_digest,
            "remote_identity": self.remote_identity,
            "remote_identity_digest": self.remote_identity_digest,
            "base": self.base,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "scope_paths": list(self.scope_paths),
            "subject_digest": self.subject_digest,
            "policy_digest": self.policy_digest,
            "effect": self.effect,
            "integration_strategy": self.integration_strategy,
            "pull_request_number": self.pull_request_number,
            "pull_request_url": self.pull_request_url,
            "pull_request_digest": self.pull_request_digest,
            "checks_digest": self.checks_digest,
            "prepared_at": self.prepared_at,
            "expires_at": self.expires_at,
            "argv": list(self.argv),
            "argv_digest": self.argv_digest,
            "observation_argv": list(self.observation_argv),
            "observation_argv_digest": self.observation_argv_digest,
            "observe_before_retry": self.observe_before_retry,
            "authorizes": self.authorizes,
            "plan_digest": self.plan_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IntegrationEffectPlanV1":
        if not isinstance(value, Mapping) or set(value) != _INTEGRATION_EFFECT_PLAN_KEYS:
            raise ValueError("E_INTEGRATION_EFFECT_PLAN: schema is not closed")
        try:
            payload = dict(value)
            payload["scope_paths"] = tuple(payload["scope_paths"])
            payload["argv"] = tuple(payload["argv"])
            payload["observation_argv"] = tuple(payload["observation_argv"])
            return cls(**payload)
        except (TypeError, ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error).startswith(
                "E_INTEGRATION_EFFECT_PLAN"
            ):
                raise
            raise ValueError(
                "E_INTEGRATION_EFFECT_PLAN: payload is invalid"
            ) from error


class IntegrationExecutionTicket:
    """Process-local executor-edge proof; never durable or authorizing."""

    __slots__ = (
        "_ticket_id",
        "_plan_digest",
        "_clock",
        "_issued_at",
        "_ready_deadline",
        "_plan_expires",
    )

    def __new__(cls, *_: object, **__: object) -> "IntegrationExecutionTicket":
        raise TypeError("IntegrationExecutionTicket is internal")


def _issue_integration_execution_ticket(
    effect_plan: IntegrationEffectPlanV1,
    *,
    clock: object,
    issued_at: datetime,
    ready_deadline: datetime,
    plan_expires: datetime,
) -> IntegrationExecutionTicket:
    if (
        type(effect_plan) is not IntegrationEffectPlanV1
        or not callable(clock)
        or not isinstance(issued_at, datetime)
        or issued_at.tzinfo != timezone.utc
        or not isinstance(ready_deadline, datetime)
        or ready_deadline.tzinfo != timezone.utc
        or not isinstance(plan_expires, datetime)
        or plan_expires.tzinfo != timezone.utc
        or issued_at > ready_deadline
        or issued_at >= plan_expires
    ):
        raise ValueError("E_INTEGRATION_EXECUTION: exact live plan is required")
    ticket_id = uuid4().hex
    ticket = object.__new__(IntegrationExecutionTicket)
    ticket._ticket_id = ticket_id
    ticket._plan_digest = effect_plan.plan_digest
    ticket._clock = clock
    ticket._issued_at = issued_at
    ticket._ready_deadline = ready_deadline
    ticket._plan_expires = plan_expires
    with _INTEGRATION_TICKET_LOCK:
        _INTEGRATION_TICKETS[ticket_id] = (
            effect_plan.plan_digest,
            clock,
            issued_at,
            ready_deadline,
            plan_expires,
        )
    return ticket


def consume_integration_execution_ticket(
    ticket: IntegrationExecutionTicket,
    *,
    effect_plan: IntegrationEffectPlanV1,
) -> IntegrationEffectPlanV1:
    """Consume one executor-edge ticket without invoking a provider."""

    if (
        type(ticket) is not IntegrationExecutionTicket
        or type(effect_plan) is not IntegrationEffectPlanV1
    ):
        raise ValueError("E_INTEGRATION_EXECUTION: one-shot ticket is invalid")
    with _INTEGRATION_TICKET_LOCK:
        registered = _INTEGRATION_TICKETS.pop(ticket._ticket_id, None)
    if (
        registered is None
        or registered[0] != effect_plan.plan_digest
        or registered[1] is not ticket._clock
        or registered[2] != ticket._issued_at
        or registered[3] != ticket._ready_deadline
        or registered[4] != ticket._plan_expires
        or ticket._plan_digest != effect_plan.plan_digest
    ):
        raise ValueError("E_INTEGRATION_EXECUTION: one-shot ticket is unavailable")
    try:
        observed = ticket._clock()
    except Exception as error:
        raise ValueError(
            "E_INTEGRATION_EXECUTION: ticket time is UNKNOWN"
        ) from error
    current = (
        observed
        if isinstance(observed, datetime) and observed.tzinfo == timezone.utc
        else _outcome_time(observed)
    )
    if current is None:
        raise ValueError("E_INTEGRATION_EXECUTION: ticket time is UNKNOWN")
    if current < ticket._issued_at:
        raise ValueError("E_INTEGRATION_EXECUTION: ticket clock rolled back")
    if (
        current > ticket._ready_deadline
        or current >= ticket._plan_expires
    ):
        raise ValueError("E_INTEGRATION_EXECUTION: one-shot ticket expired")
    return IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())


@dataclass(frozen=True)
class IntegrationReceiptV1:
    """Exact merge observation bound to one non-authorizing squash plan."""

    schema_version: int
    kind: str
    task_id: str
    task_digest: str
    run_plan_digest: str
    requested_outcome: str
    repository: str
    remote: str
    remote_url_digest: str
    remote_identity: str
    base: str
    branch: str
    head_sha: str
    subject_digest: str
    policy_digest: str
    effect: str
    integration_strategy: str
    pull_request_number: int
    pull_request_url: str
    pull_request_digest: str
    checks_digest: str
    effect_plan_digest: str
    status: str
    observed_repository: str | None
    observed_base: str | None
    observed_branch: str | None
    observed_head_sha: str | None
    observed_pr_number: int | None
    observed_pr_url: str | None
    observed_pr_state: str | None
    observed_pr_draft: bool | None
    observed_strategy: str | None
    observed_checks_digest: str | None
    observed_merge_sha: str | None
    observed_at: str
    authorizes: bool
    receipt_digest: str

    def __post_init__(self) -> None:
        core = {
            key: value
            for key, value in self.to_dict().items()
            if key != "receipt_digest"
        }
        try:
            canonical_identity = _canonical_github_repository_identity(
                self.remote_identity, code="E_INTEGRATION_RECEIPT"
            )
            pull_request_identity, pull_request_number = (
                _github_pull_request_url_identity(
                    self.pull_request_url, code="E_INTEGRATION_RECEIPT"
                )
            )
        except ValueError:
            canonical_identity = None
            pull_request_identity, pull_request_number = None, None
        observed = (
            self.observed_repository,
            self.observed_base,
            self.observed_branch,
            self.observed_head_sha,
            self.observed_pr_number,
            self.observed_pr_url,
            self.observed_pr_state,
            self.observed_pr_draft,
            self.observed_strategy,
            self.observed_checks_digest,
            self.observed_merge_sha,
        )
        pass_exact = self.status != "PASS" or observed == (
            self.remote_identity,
            self.base,
            self.branch,
            self.head_sha,
            self.pull_request_number,
            self.pull_request_url,
            "MERGED",
            False,
            "squash",
            self.checks_digest,
            self.observed_merge_sha,
        )
        ready_exact = self.status != "READY" or observed == (
            self.remote_identity,
            self.base,
            self.branch,
            self.head_sha,
            self.pull_request_number,
            self.pull_request_url,
            "OPEN",
            False,
            None,
            self.checks_digest,
            None,
        )
        inconclusive_empty = self.status in {"PASS", "READY"} or all(
            value is None for value in observed
        )
        if (
            self.schema_version != 1
            or self.kind != "IntegrationReceiptV1"
            or not validate_task_id(self.task_id)
            or not all(
                _outcome_digest(value)
                for value in (
                    self.task_digest,
                    self.run_plan_digest,
                    self.remote_url_digest,
                    self.subject_digest,
                    self.policy_digest,
                    self.pull_request_digest,
                    self.checks_digest,
                    self.effect_plan_digest,
                    self.receipt_digest,
                )
            )
            or self.requested_outcome != "integration"
            or not isinstance(self.repository, str)
            or not Path(self.repository).is_absolute()
            or str(Path(self.repository).resolve()) != self.repository
            or _OUTCOME_REMOTE_NAME.fullmatch(self.remote) is None
            or _GITHUB_REPOSITORY_IDENTITY.fullmatch(self.remote_identity) is None
            or canonical_identity != self.remote_identity
            or pull_request_identity != self.remote_identity
            or pull_request_number != self.pull_request_number
            or _OUTCOME_BRANCH.fullmatch(self.base) is None
            or _OUTCOME_BRANCH.fullmatch(self.branch) is None
            or self.base == self.branch
            or _GIT_OBJECT_ID.fullmatch(self.head_sha) is None
            or self.effect != "integration"
            or self.integration_strategy != "squash"
            or not isinstance(self.pull_request_number, int)
            or isinstance(self.pull_request_number, bool)
            or self.pull_request_number <= 0
            or self.status not in {"READY", "PASS", "FAIL", "UNKNOWN"}
            or not pass_exact
            or not ready_exact
            or not inconclusive_empty
            or (
                self.status == "PASS"
                and _GIT_OBJECT_ID.fullmatch(str(self.observed_merge_sha)) is None
            )
            or _outcome_time(self.observed_at) is None
            or self.authorizes is not False
            or self.receipt_digest != contract_digest(core)
            or _outcome_contract_size(self.to_dict()) > _OUTCOME_CONTRACT_MAX_BYTES
        ):
            raise ValueError(
                "E_INTEGRATION_RECEIPT: closed squash receipt is invalid"
            )

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IntegrationReceiptV1":
        if not isinstance(value, Mapping) or set(value) != _INTEGRATION_RECEIPT_KEYS:
            raise ValueError("E_INTEGRATION_RECEIPT: schema is not closed")
        try:
            return cls(**dict(value))
        except (TypeError, ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error).startswith(
                "E_INTEGRATION_RECEIPT"
            ):
                raise
            raise ValueError("E_INTEGRATION_RECEIPT: payload is invalid") from error


@dataclass(frozen=True)
class BaseRefreshReceiptV1:
    """Non-authorizing host receipt for one exact closed base fetch."""

    schema_version: int
    kind: str
    task_id: str
    task_digest: str
    run_plan_digest: str
    repository: str
    remote: str
    remote_url: str
    remote_url_digest: str
    remote_identity: str
    remote_identity_digest: str
    base: str
    base_ref: str
    policy_digest: str
    effect_plan_digest: str
    integration_receipt_digest: str
    merge_sha: str
    refresh_argv: tuple[str, ...]
    refresh_argv_digest: str
    status: str
    observed_ref: str | None
    observed_sha: str | None
    observed_at: str
    authorizes: bool
    receipt_digest: str

    def __post_init__(self) -> None:
        expected_ref = f"refs/remotes/{self.remote}/{self.base}"
        expected_argv = (
            "git",
            "-C",
            self.repository,
            "fetch",
            "--no-tags",
            "--no-prune",
            self.remote_url,
            f"+refs/heads/{self.base}:{expected_ref}",
        )
        core = {
            key: value
            for key, value in self.to_dict().items()
            if key != "receipt_digest"
        }
        pass_exact = self.status != "PASS" or (
            self.observed_ref == expected_ref
            and _GIT_OBJECT_ID.fullmatch(str(self.observed_sha)) is not None
        )
        inconclusive_empty = self.status == "PASS" or (
            self.observed_ref is None and self.observed_sha is None
        )
        if (
            self.schema_version != 1
            or self.kind != "BaseRefreshReceiptV1"
            or not validate_task_id(self.task_id)
            or not all(
                _outcome_digest(value)
                for value in (
                    self.task_digest,
                    self.run_plan_digest,
                    self.remote_url_digest,
                    self.remote_identity_digest,
                    self.policy_digest,
                    self.effect_plan_digest,
                    self.integration_receipt_digest,
                    self.refresh_argv_digest,
                    self.receipt_digest,
                )
            )
            or not isinstance(self.repository, str)
            or not Path(self.repository).is_absolute()
            or str(Path(self.repository).resolve()) != self.repository
            or _OUTCOME_REMOTE_NAME.fullmatch(self.remote) is None
            or self.remote_url_digest != contract_digest(self.remote_url)
            or self.remote_identity
            != _canonical_github_repository_from_url(
                self.remote_url, code="E_BASE_REFRESH_RECEIPT"
            )
            or self.remote_identity_digest != contract_digest(self.remote_identity)
            or _OUTCOME_BRANCH.fullmatch(self.base) is None
            or self.base_ref != expected_ref
            or _GIT_OBJECT_ID.fullmatch(self.merge_sha) is None
            or self.refresh_argv != expected_argv
            or self.refresh_argv_digest != contract_digest(list(self.refresh_argv))
            or self.status not in {"PASS", "FAIL", "UNKNOWN"}
            or not pass_exact
            or not inconclusive_empty
            or _outcome_time(self.observed_at) is None
            or self.authorizes is not False
            or self.receipt_digest != contract_digest(core)
        ):
            raise ValueError("E_BASE_REFRESH_RECEIPT: receipt is invalid")

    def to_dict(self) -> dict[str, object]:
        return {**self.__dict__, "refresh_argv": list(self.refresh_argv)}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BaseRefreshReceiptV1":
        if not isinstance(value, Mapping) or set(value) != _BASE_REFRESH_RECEIPT_KEYS:
            raise ValueError("E_BASE_REFRESH_RECEIPT: schema is not closed")
        try:
            payload = dict(value)
            payload["refresh_argv"] = tuple(payload["refresh_argv"])
            return cls(**payload)
        except (TypeError, ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error).startswith(
                "E_BASE_REFRESH_RECEIPT"
            ):
                raise
            raise ValueError("E_BASE_REFRESH_RECEIPT: payload is invalid") from error


@dataclass(frozen=True)
class BaseVerificationReceiptV1:
    """Read-only containment result for the exact refreshed remote base."""

    schema_version: int
    kind: str
    task_id: str
    task_digest: str
    run_plan_digest: str
    repository: str
    remote: str
    base: str
    base_ref: str
    policy_digest: str
    effect_plan_digest: str
    integration_receipt_digest: str
    refresh_receipt_digest: str
    merge_sha: str
    status: str
    reason_code: str
    observed_base_sha: str | None
    contained: bool | None
    observed_at: str
    authorizes: bool
    receipt_digest: str

    def __post_init__(self) -> None:
        expected_ref = f"refs/remotes/{self.remote}/{self.base}"
        core = {
            key: value
            for key, value in self.to_dict().items()
            if key != "receipt_digest"
        }
        pass_exact = (
            self.status != "PASS"
            or (
                self.reason_code == "BASE_CONTAINED"
                and self.contained is True
                and _GIT_OBJECT_ID.fullmatch(str(self.observed_base_sha))
                is not None
            )
        )
        blocked_exact = (
            self.status != "BLOCKED"
            or (
                self.reason_code
                in {
                    "BASE_REFRESH_UNKNOWN",
                    "BASE_REF_MISSING",
                    "BASE_REF_MISMATCH",
                    "BASE_MERGE_NOT_CONTAINED",
                    "BASE_CONTAINMENT_UNKNOWN",
                }
                and self.contained in {False, None}
                and (
                    self.observed_base_sha is None
                    or _GIT_OBJECT_ID.fullmatch(self.observed_base_sha) is not None
                )
            )
        )
        if (
            self.schema_version != 1
            or self.kind != "BaseVerificationReceiptV1"
            or not validate_task_id(self.task_id)
            or not all(
                _outcome_digest(value)
                for value in (
                    self.task_digest,
                    self.run_plan_digest,
                    self.policy_digest,
                    self.effect_plan_digest,
                    self.integration_receipt_digest,
                    self.refresh_receipt_digest,
                    self.receipt_digest,
                )
            )
            or not isinstance(self.repository, str)
            or not Path(self.repository).is_absolute()
            or str(Path(self.repository).resolve()) != self.repository
            or _OUTCOME_REMOTE_NAME.fullmatch(self.remote) is None
            or _OUTCOME_BRANCH.fullmatch(self.base) is None
            or self.base_ref != expected_ref
            or _GIT_OBJECT_ID.fullmatch(self.merge_sha) is None
            or self.status not in {"PASS", "BLOCKED"}
            or not pass_exact
            or not blocked_exact
            or _outcome_time(self.observed_at) is None
            or self.authorizes is not False
            or self.receipt_digest != contract_digest(core)
        ):
            raise ValueError("E_BASE_VERIFICATION_RECEIPT: receipt is invalid")

    def to_dict(self) -> dict[str, object]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "BaseVerificationReceiptV1":
        if (
            not isinstance(value, Mapping)
            or set(value) != _BASE_VERIFICATION_RECEIPT_KEYS
        ):
            raise ValueError("E_BASE_VERIFICATION_RECEIPT: schema is not closed")
        try:
            return cls(**dict(value))
        except (TypeError, ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error).startswith(
                "E_BASE_VERIFICATION_RECEIPT"
            ):
                raise
            raise ValueError(
                "E_BASE_VERIFICATION_RECEIPT: payload is invalid"
            ) from error


@dataclass(frozen=True)
class OutcomeEffectPlanV1:
    """Closed kernel proposal for one bounded remote outcome effect.

    This value describes an effect.  It carries no host authority and cannot
    execute itself.
    """

    schema_version: int
    kind: str
    task_id: str
    task_digest: str
    run_plan_digest: str
    requested_outcome: str
    repository: str
    remote: str
    remote_url: str
    remote_url_digest: str
    remote_identity_digest: str
    base: str
    branch: str
    head_sha: str
    scope_paths: tuple[str, ...]
    subject_digest: str
    policy_digest: str
    effect: str
    title: str | None
    title_digest: str | None
    body: str | None
    body_digest: str | None
    draft: bool | None
    operation: str | None
    operation_digest: str | None
    required_checks: tuple[tuple[str, str, tuple[str, ...], str], ...]
    argv: tuple[str, ...]
    argv_digest: str
    observation_argv: tuple[str, ...]
    observation_argv_digest: str
    observe_before_retry: bool
    authorizes: bool
    plan_digest: str

    def __post_init__(self) -> None:
        expected_push = (
            "git",
            "push",
            self.remote_url,
            f"{self.head_sha}:refs/heads/{self.branch}",
        )
        expected_observation = (
            "git",
            "ls-remote",
            "--heads",
            self.remote_url,
            f"refs/heads/{self.branch}",
        )
        expected_pr_argv = (
            "pull_request.create_draft",
            self.remote_identity_digest,
            self.base,
            self.branch,
            self.head_sha,
            str(self.title_digest),
            str(self.body_digest),
        )
        expected_pr_observation = (
            "pull_request.observe",
            self.remote_identity_digest,
            self.base,
            self.branch,
            self.head_sha,
        )
        ready_number = self.argv[2] if len(self.argv) == 8 else None
        ready_url_digest = self.argv[3] if len(self.argv) == 8 else None
        ready_checks_digest = self.argv[7] if len(self.argv) == 8 else None
        expected_ready_argv = (
            "pull_request.mark_ready",
            self.remote_identity_digest,
            ready_number,
            ready_url_digest,
            self.base,
            self.branch,
            self.head_sha,
            ready_checks_digest,
        )
        expected_ready_observation = (
            "pull_request.observe",
            self.remote_identity_digest,
            ready_number,
            ready_url_digest,
            self.base,
            self.branch,
            self.head_sha,
        )
        remote_write_variant = (
            self.effect == "remote_write"
            and all(
                value is None
                for value in (
                    self.title,
                    self.title_digest,
                    self.body,
                    self.body_digest,
                    self.draft,
                    self.operation,
                    self.operation_digest,
                )
            )
            and not self.required_checks
            and self.argv == expected_push
            and self.observation_argv == expected_observation
        )
        pull_request_variant = (
            self.effect == "pull_request"
            and _outcome_pr_content(self.title, self.body)
            and self.title_digest == contract_digest(self.title)
            and self.body_digest == contract_digest(self.body)
            and self.draft is True
            and self.operation == "create_draft_pull_request"
            and self.operation_digest
            == contract_digest(
                {
                    "operation": self.operation,
                    "argv": list(expected_pr_argv),
                    "observation_argv": list(expected_pr_observation),
                }
            )
            and self.argv == expected_pr_argv
            and self.observation_argv == expected_pr_observation
            and _outcome_required_checks(self.required_checks, required=True)
        )
        pull_request_ready_variant = (
            self.effect == "pull_request"
            and all(
                value is None
                for value in (
                    self.title,
                    self.title_digest,
                    self.body,
                    self.body_digest,
                )
            )
            and self.draft is False
            and self.operation == "mark_pull_request_ready"
            and isinstance(ready_number, str)
            and re.fullmatch(r"[1-9][0-9]*", ready_number) is not None
            and _outcome_digest(ready_url_digest)
            and _outcome_digest(ready_checks_digest)
            and self.operation_digest
            == contract_digest(
                {
                    "operation": self.operation,
                    "argv": list(expected_ready_argv),
                    "observation_argv": list(expected_ready_observation),
                }
            )
            and self.argv == expected_ready_argv
            and self.observation_argv == expected_ready_observation
            and _outcome_required_checks(self.required_checks, required=True)
        )
        core = {
            key: value
            for key, value in self.to_dict().items()
            if key != "plan_digest"
        }
        if (
            self.schema_version != 1
            or self.kind != "OutcomeEffectPlanV1"
            or not validate_task_id(self.task_id)
            or not all(
                _outcome_digest(value)
                for value in (
                    self.task_digest,
                    self.run_plan_digest,
                    self.subject_digest,
                    self.policy_digest,
                    self.remote_url_digest,
                    self.remote_identity_digest,
                    self.argv_digest,
                    self.observation_argv_digest,
                    self.plan_digest,
                )
            )
            or self.requested_outcome not in {"pull_request", "integration"}
            or not isinstance(self.repository, str)
            or not Path(self.repository).is_absolute()
            or str(Path(self.repository).resolve()) != self.repository
            or _OUTCOME_REMOTE_NAME.fullmatch(self.remote) is None
            or self.remote_url_digest != contract_digest(self.remote_url)
            or self.remote_identity_digest
            != contract_digest(_outcome_identity_for_url(self.remote_url))
            or _OUTCOME_BRANCH.fullmatch(self.base) is None
            or _OUTCOME_BRANCH.fullmatch(self.branch) is None
            or self.base == self.branch
            or _GIT_OBJECT_ID.fullmatch(self.head_sha) is None
            or not _outcome_scope(self.scope_paths)
            or not (
                remote_write_variant
                or pull_request_variant
                or pull_request_ready_variant
            )
            or self.argv_digest != contract_digest(list(self.argv))
            or self.observation_argv_digest
            != contract_digest(list(self.observation_argv))
            or self.observe_before_retry is not True
            or self.authorizes is not False
            or self.plan_digest != contract_digest(core)
            or _outcome_contract_size(self.to_dict())
            > _OUTCOME_CONTRACT_MAX_BYTES
        ):
            raise ValueError(
                "E_OUTCOME_EFFECT_PLAN: closed outcome effect plan is invalid"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "task_id": self.task_id,
            "task_digest": self.task_digest,
            "run_plan_digest": self.run_plan_digest,
            "requested_outcome": self.requested_outcome,
            "repository": self.repository,
            "remote": self.remote,
            "remote_url": self.remote_url,
            "remote_url_digest": self.remote_url_digest,
            "remote_identity_digest": self.remote_identity_digest,
            "base": self.base,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "scope_paths": list(self.scope_paths),
            "subject_digest": self.subject_digest,
            "policy_digest": self.policy_digest,
            "effect": self.effect,
            "title": self.title,
            "title_digest": self.title_digest,
            "body": self.body,
            "body_digest": self.body_digest,
            "draft": self.draft,
            "operation": self.operation,
            "operation_digest": self.operation_digest,
            "required_checks": [
                {"name": item[0], "app": item[1], "conclusions": list(item[2]), "selector_digest": item[3]}
                for item in self.required_checks
            ],
            "argv": list(self.argv),
            "argv_digest": self.argv_digest,
            "observation_argv": list(self.observation_argv),
            "observation_argv_digest": self.observation_argv_digest,
            "observe_before_retry": self.observe_before_retry,
            "authorizes": self.authorizes,
            "plan_digest": self.plan_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "OutcomeEffectPlanV1":
        if not isinstance(value, Mapping) or set(value) != _OUTCOME_EFFECT_PLAN_KEYS:
            raise ValueError("E_OUTCOME_EFFECT_PLAN: schema is not closed")
        try:
            payload = dict(value)
            raw_checks = payload["required_checks"]
            if (
                not isinstance(raw_checks, list)
                or any(
                    not isinstance(item, Mapping)
                    or set(item) != {"name", "app", "conclusions", "selector_digest"}
                    or not isinstance(item["conclusions"], list)
                    for item in raw_checks
                )
            ):
                raise ValueError("E_OUTCOME_EFFECT_PLAN: nested schema is not closed")
            payload["scope_paths"] = tuple(payload["scope_paths"])
            payload["argv"] = tuple(payload["argv"])
            payload["observation_argv"] = tuple(payload["observation_argv"])
            payload["required_checks"] = tuple(
                (
                    item["name"], item["app"], tuple(item["conclusions"]),
                    item["selector_digest"],
                )
                for item in payload["required_checks"]
            )
            return cls(**payload)
        except (TypeError, ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error).startswith(
                "E_OUTCOME_EFFECT_PLAN"
            ):
                raise
            raise ValueError("E_OUTCOME_EFFECT_PLAN: payload is invalid") from error


@dataclass(frozen=True)
class RemoteOutcomeReceiptV1:
    """Durable result of a host observation; never proof of authority."""

    schema_version: int
    kind: str
    task_id: str
    task_digest: str
    run_plan_digest: str
    requested_outcome: str
    repository: str
    remote: str
    remote_url: str
    remote_url_digest: str
    remote_identity_digest: str
    base: str
    branch: str
    head_sha: str
    scope_paths: tuple[str, ...]
    subject_digest: str
    policy_digest: str
    effect: str
    title_digest: str | None
    body_digest: str | None
    draft: bool | None
    effect_plan_digest: str
    status: str
    observed_repository: str | None
    observed_remote: str | None
    observed_base: str | None
    observed_branch: str | None
    observed_head_sha: str | None
    observed_pr_number: int | None
    observed_pr_url: str | None
    observed_pr_draft: bool | None
    disposition: str | None
    observation_kind: str | None
    required_check_digests: tuple[str, ...]
    check_results: tuple[tuple[str, str], ...]
    feedback: tuple[tuple[int, str, str, str], ...]
    observed_at: str
    authorizes: bool
    receipt_digest: str

    def __post_init__(self) -> None:
        core = {
            key: value
            for key, value in self.to_dict().items()
            if key != "receipt_digest"
        }
        observed = (
            self.observed_repository,
            self.observed_remote,
            self.observed_base,
            self.observed_branch,
            self.observed_head_sha,
        )
        observed_repository_valid = self.observed_repository is None or (
            isinstance(self.observed_repository, str)
            and Path(self.observed_repository).is_absolute()
            and not any(
                ord(character) < 32 for character in self.observed_repository
            )
            and (
                str(Path(self.observed_repository).resolve())
                == self.observed_repository
                or (self.effect == "pull_request" and self.status == "FAIL")
            )
        )
        observed_fields_valid = observed_repository_valid and (
            self.observed_remote is None
            or _OUTCOME_REMOTE_NAME.fullmatch(self.observed_remote) is not None
        ) and (
            self.observed_base is None
            or _OUTCOME_BRANCH.fullmatch(self.observed_base) is not None
        ) and (
            self.observed_branch is None
            or _OUTCOME_BRANCH.fullmatch(self.observed_branch) is not None
        ) and (
            self.observed_head_sha is None
            or _GIT_OBJECT_ID.fullmatch(self.observed_head_sha) is not None
        )
        pass_exact = self.status != "PASS" or observed == (
            self.repository,
            self.remote,
            self.base,
            self.branch,
            self.head_sha,
        )
        unknown_empty = self.status != "UNKNOWN" or all(
            value is None for value in observed
        )
        pr_observed = (
            self.observed_pr_number,
            self.observed_pr_url,
            self.observed_pr_draft,
            self.disposition,
        )
        valid_pr_url = self.observed_pr_url is None
        if self.observed_pr_url is not None:
            try:
                expected_repository = _canonical_github_repository_from_url(
                    self.remote_url, code="E_REMOTE_OUTCOME_RECEIPT"
                )
                observed_repository, observed_number = (
                    _github_pull_request_url_identity(
                        self.observed_pr_url,
                        code="E_REMOTE_OUTCOME_RECEIPT",
                    )
                )
            except ValueError:
                valid_pr_url = False
            else:
                valid_pr_url = (
                    observed_repository == expected_repository
                    and observed_number == self.observed_pr_number
                )
        remote_write_variant = (
            self.effect == "remote_write"
            and self.status in {"PASS", "FAIL", "UNKNOWN"}
            and self.title_digest is None
            and self.body_digest is None
            and self.draft is None
            and all(value is None for value in pr_observed)
            and pass_exact
            and unknown_empty
        )
        pr_empty = all(value is None for value in (*observed, *pr_observed))
        pr_pass = (
            self.status == "PASS"
            and observed
            == (
                self.repository,
                self.remote,
                self.base,
                self.branch,
                self.head_sha,
            )
            and isinstance(self.observed_pr_number, int)
            and not isinstance(self.observed_pr_number, bool)
            and self.observed_pr_number > 0
            and valid_pr_url
            and self.observed_pr_draft is True
            and self.disposition in {"observed_existing", "created"}
        )
        pr_fail = (
            self.status == "FAIL"
            and not pr_empty
            and isinstance(self.observed_pr_number, int)
            and not isinstance(self.observed_pr_number, bool)
            and self.observed_pr_number > 0
            and valid_pr_url
            and isinstance(self.observed_pr_draft, bool)
            and self.disposition == "observed_existing"
        )
        pull_request_variant = (
            self.effect == "pull_request"
            and _outcome_digest(self.title_digest)
            and _outcome_digest(self.body_digest)
            and self.draft is True
            and self.status in {"PASS", "FAIL", "UNKNOWN", "ABSENT"}
            and (pr_pass or pr_fail or (self.status in {"UNKNOWN", "ABSENT"} and pr_empty))
        )
        readiness_pr = (
            self.effect == "pull_request"
            and self.status in {"PASS", "UNKNOWN"}
            and self.observed_repository == self.repository
            and self.observed_remote == self.remote
            and self.observed_base == self.base
            and self.observed_branch == self.branch
            and self.observed_head_sha == self.head_sha
            and isinstance(self.observed_pr_number, int)
            and not isinstance(self.observed_pr_number, bool)
            and self.observed_pr_number > 0
            and valid_pr_url
            and self.observed_pr_draft is True
            and self.disposition == "observed_existing"
        )
        check_digests_valid = (
            isinstance(self.required_check_digests, tuple)
            and len(self.required_check_digests) <= 64
            and tuple(sorted(set(self.required_check_digests)))[::] == self.required_check_digests
            and all(_outcome_digest(value) for value in self.required_check_digests)
        )
        check_results_valid = (
            isinstance(self.check_results, tuple)
            and len(self.check_results) <= 64
            and all(
                isinstance(item, tuple)
                and len(item) == 2
                and _outcome_digest(item[0])
                and item[1] in {"PASS", "FAIL", "UNKNOWN"}
                for item in self.check_results
            )
            and tuple(item[0] for item in self.check_results)
            == self.required_check_digests
        )
        feedback_valid = (
            isinstance(self.feedback, tuple)
            and len(self.feedback) <= 64
            and len({item[0] for item in self.feedback if isinstance(item, tuple) and len(item) == 4})
            == len(self.feedback)
            and all(
                isinstance(item, tuple)
                and len(item) == 4
                and isinstance(item[0], int)
                and not isinstance(item[0], bool)
                and item[0] > 0
                and _outcome_digest(item[1])
                and item[2] in {"Critical", "Important", "Minor"}
                and item[3] in {"resolved", "unresolved"}
                for item in self.feedback
            )
        )
        readiness_variant = (
            readiness_pr
            and self.observation_kind in {"checks", "review_threads", "comments"}
            and (
                (
                    self.observation_kind == "checks"
                    and self.status == "PASS"
                    and check_digests_valid
                    and check_results_valid
                    and not self.feedback
                )
                or (
                    self.observation_kind in {"review_threads", "comments"}
                    and not self.required_check_digests
                    and not self.check_results
                    and feedback_valid
                    and (self.status == "PASS" or not self.feedback)
                )
            )
        )
        ready_pass = (
            self.status == "PASS"
            and observed
            == (
                self.repository,
                self.remote,
                self.base,
                self.branch,
                self.head_sha,
            )
            and isinstance(self.observed_pr_number, int)
            and not isinstance(self.observed_pr_number, bool)
            and self.observed_pr_number > 0
            and valid_pr_url
            and self.observed_pr_draft is False
            and self.disposition in {"marked_ready", "observed_existing"}
        )
        ready_unknown = self.status == "UNKNOWN" and (
            pr_empty
            or (
                observed
                == (
                    self.repository,
                    self.remote,
                    self.base,
                    self.branch,
                    self.head_sha,
                )
                and isinstance(self.observed_pr_number, int)
                and not isinstance(self.observed_pr_number, bool)
                and self.observed_pr_number > 0
                and valid_pr_url
                and self.observed_pr_draft is True
                and self.disposition == "observed_existing"
            )
        )
        pull_request_ready_variant = (
            self.effect == "pull_request"
            and self.title_digest is None
            and self.body_digest is None
            and self.draft is False
            and self.observation_kind == "ready_state"
            and not self.required_check_digests
            and not self.check_results
            and not self.feedback
            and (ready_pass or ready_unknown)
        )
        legacy_variant = (
            self.observation_kind is None
            and not self.required_check_digests
            and not self.check_results
            and not self.feedback
            and (remote_write_variant or pull_request_variant)
        )
        if (
            self.schema_version != 1
            or self.kind != "RemoteOutcomeReceiptV1"
            or not validate_task_id(self.task_id)
            or not all(
                _outcome_digest(value)
                for value in (
                    self.task_digest,
                    self.run_plan_digest,
                    self.subject_digest,
                    self.policy_digest,
                    self.remote_url_digest,
                    self.remote_identity_digest,
                    self.effect_plan_digest,
                    self.receipt_digest,
                )
            )
            or self.requested_outcome not in {"pull_request", "integration"}
            or not isinstance(self.repository, str)
            or not Path(self.repository).is_absolute()
            or str(Path(self.repository).resolve()) != self.repository
            or _OUTCOME_REMOTE_NAME.fullmatch(self.remote) is None
            or self.remote_url_digest != contract_digest(self.remote_url)
            or self.remote_identity_digest
            != contract_digest(_outcome_identity_for_url(self.remote_url))
            or _OUTCOME_BRANCH.fullmatch(self.base) is None
            or _OUTCOME_BRANCH.fullmatch(self.branch) is None
            or self.base == self.branch
            or _GIT_OBJECT_ID.fullmatch(self.head_sha) is None
            or not _outcome_scope(self.scope_paths)
            or not (
                legacy_variant
                or readiness_variant
                or pull_request_ready_variant
            )
            or not observed_fields_valid
            or _OUTCOME_TIMESTAMP.fullmatch(self.observed_at) is None
            or self.authorizes is not False
            or self.receipt_digest != contract_digest(core)
            or _outcome_contract_size(self.to_dict())
            > _OUTCOME_CONTRACT_MAX_BYTES
        ):
            raise ValueError(
                "E_REMOTE_OUTCOME_RECEIPT: closed observation receipt is invalid"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "task_id": self.task_id,
            "task_digest": self.task_digest,
            "run_plan_digest": self.run_plan_digest,
            "requested_outcome": self.requested_outcome,
            "repository": self.repository,
            "remote": self.remote,
            "remote_url": self.remote_url,
            "remote_url_digest": self.remote_url_digest,
            "remote_identity_digest": self.remote_identity_digest,
            "base": self.base,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "scope_paths": list(self.scope_paths),
            "subject_digest": self.subject_digest,
            "policy_digest": self.policy_digest,
            "effect": self.effect,
            "title_digest": self.title_digest,
            "body_digest": self.body_digest,
            "draft": self.draft,
            "effect_plan_digest": self.effect_plan_digest,
            "status": self.status,
            "observed_repository": self.observed_repository,
            "observed_remote": self.observed_remote,
            "observed_base": self.observed_base,
            "observed_branch": self.observed_branch,
            "observed_head_sha": self.observed_head_sha,
            "observed_pr_number": self.observed_pr_number,
            "observed_pr_url": self.observed_pr_url,
            "observed_pr_draft": self.observed_pr_draft,
            "disposition": self.disposition,
            "observation_kind": self.observation_kind,
            "required_check_digests": list(self.required_check_digests),
            "check_results": [list(item) for item in self.check_results],
            "feedback": [
                {"id": item[0], "digest": item[1], "severity": item[2], "status": item[3]}
                for item in self.feedback
            ],
            "observed_at": self.observed_at,
            "authorizes": self.authorizes,
            "receipt_digest": self.receipt_digest,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RemoteOutcomeReceiptV1":
        if not isinstance(value, Mapping) or set(value) != _REMOTE_OUTCOME_RECEIPT_KEYS:
            raise ValueError("E_REMOTE_OUTCOME_RECEIPT: schema is not closed")
        try:
            payload = dict(value)
            raw_required = payload["required_check_digests"]
            raw_results = payload["check_results"]
            raw_feedback = payload["feedback"]
            if (
                not isinstance(raw_required, list)
                or not isinstance(raw_results, list)
                or not isinstance(raw_feedback, list)
                or any(
                    not isinstance(item, list) or len(item) != 2
                    for item in raw_results
                )
                or any(
                    not isinstance(item, Mapping)
                    or set(item) != {"id", "digest", "severity", "status"}
                    for item in raw_feedback
                )
            ):
                raise ValueError("E_REMOTE_OUTCOME_RECEIPT: nested schema is not closed")
            payload["scope_paths"] = tuple(payload["scope_paths"])
            payload["required_check_digests"] = tuple(payload["required_check_digests"])
            payload["check_results"] = tuple(
                tuple(item) for item in payload["check_results"]
            )
            payload["feedback"] = tuple(
                (item["id"], item["digest"], item["severity"], item["status"])
                for item in payload["feedback"]
            )
            return cls(**payload)
        except (TypeError, ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error).startswith(
                "E_REMOTE_OUTCOME_RECEIPT"
            ):
                raise
            raise ValueError("E_REMOTE_OUTCOME_RECEIPT: payload is invalid") from error


def _outcome_remote_url_and_identity(
    repository: Path, remote: str
) -> tuple[str, str, str]:
    completed = subprocess.run(
        trusted_git_argv(
            repository, ("remote", "get-url", "--push", remote)
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
        env=trusted_git_environment(),
    )
    if completed.returncode != 0:
        raise ValueError("E_OUTCOME_EFFECT_PLAN: remote identity is unavailable")
    remote_url = completed.stdout.rstrip("\n")
    identity = _outcome_identity_for_url(remote_url)
    return remote_url, contract_digest(remote_url), contract_digest(identity)


def build_remote_write_effect_plan(
    *,
    outcome_binding: Mapping[str, object],
    task_digest: str,
    remote: str,
    base: str,
    scope_paths: tuple[str, ...],
    policy_digest: str,
) -> OutcomeEffectPlanV1:
    """Build the only supported push plan: feature -> same feature ref."""

    from control_plane.run_workflow import validate_outcome_binding

    if (
        validate_outcome_binding(outcome_binding)
        or not _outcome_digest(task_digest)
        or not _outcome_digest(policy_digest)
        or tuple(sorted(set(scope_paths))) != scope_paths
        or contract_digest(list(scope_paths))
        != outcome_binding.get("scope_paths_digest")
        or outcome_binding.get("requested_outcome")
        not in {"pull_request", "integration"}
        or outcome_binding.get("consumed_effect_ids")
        not in (
            ["local_write", "commit"],
            ("local_write", "commit"),
        )
        or outcome_binding.get("committed_head") is None
    ):
        raise ValueError(
            "E_OUTCOME_EFFECT_PLAN: committed OutcomeBinding is required"
        )
    branch = str(outcome_binding["branch"])
    repository = Path(str(outcome_binding["repository"])).resolve()
    remote_url, remote_url_digest, remote_identity_digest = (
        _outcome_remote_url_and_identity(repository, remote)
    )
    head_sha = str(outcome_binding["committed_head"])
    argv = (
        "git",
        "push",
        remote_url,
        f"{head_sha}:refs/heads/{branch}",
    )
    observation_argv = (
        "git",
        "ls-remote",
        "--heads",
        remote_url,
        f"refs/heads/{branch}",
    )
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "OutcomeEffectPlanV1",
        "task_id": outcome_binding["task_id"],
        "task_digest": task_digest,
        "run_plan_digest": outcome_binding["run_plan_digest"],
        "requested_outcome": outcome_binding["requested_outcome"],
        "repository": str(repository),
        "remote": remote,
        "remote_url": remote_url,
        "remote_url_digest": remote_url_digest,
        "remote_identity_digest": remote_identity_digest,
        "base": base,
        "branch": branch,
        "head_sha": head_sha,
        "scope_paths": list(scope_paths),
        "subject_digest": outcome_binding["binding_digest"],
        "policy_digest": policy_digest,
        "effect": "remote_write",
        "title": None,
        "title_digest": None,
        "body": None,
        "body_digest": None,
        "draft": None,
        "operation": None,
        "operation_digest": None,
        "required_checks": [],
        "argv": list(argv),
        "argv_digest": contract_digest(list(argv)),
        "observation_argv": list(observation_argv),
        "observation_argv_digest": contract_digest(list(observation_argv)),
        "observe_before_retry": True,
        "authorizes": False,
    }
    return OutcomeEffectPlanV1.from_dict(
        {**core, "plan_digest": contract_digest(core)}
    )


def build_squash_merge_effect_plan(
    *,
    outcome_binding: Mapping[str, object],
    task_digest: str,
    policy: Mapping[str, object],
    scope_paths: tuple[str, ...],
    pull_request_number: int,
    pull_request_url: str,
    prepared_at: str,
    expires_at: str,
    now: str,
) -> IntegrationEffectPlanV1:
    """Build the only supported integration plan from an exact PR predecessor."""

    from control_plane.policy import validate_policy
    from control_plane.run_workflow import validate_outcome_binding

    policy_git = policy.get("git", {}) if isinstance(policy, Mapping) else {}
    if policy_git.get("integration_strategy") != "squash":
        raise ValueError(
            "BLOCKED_UNSUPPORTED_INTEGRATION_STRATEGY: squash is required"
        )
    prepared = _outcome_time(prepared_at)
    expires = _outcome_time(expires_at)
    current = _outcome_time(now)
    if (
        validate_policy(policy)
        or validate_outcome_binding(outcome_binding)
        or outcome_binding.get("requested_outcome") != "integration"
        or outcome_binding.get("consumed_effect_ids")
        not in (
            ["local_write", "commit", "remote_write", "pull_request"],
            ("local_write", "commit", "remote_write", "pull_request"),
        )
        or outcome_binding.get("pushed_head")
        != outcome_binding.get("committed_head")
        or not _outcome_digest(outcome_binding.get("pull_request_digest"))
        or not _outcome_digest(outcome_binding.get("checks_digest"))
        or not _outcome_digest(task_digest)
        or tuple(sorted(set(scope_paths))) != scope_paths
        or contract_digest(list(scope_paths))
        != outcome_binding.get("scope_paths_digest")
        or policy_git.get("require_pull_request") is not True
        or policy_git.get("allow_direct_base_push") is not False
        or not isinstance(pull_request_number, int)
        or isinstance(pull_request_number, bool)
        or pull_request_number <= 0
        or prepared is None
        or expires is None
        or current is None
        or not prepared <= current < expires
        or (expires - prepared).total_seconds()
        > _INTEGRATION_EFFECT_PLAN_MAX_TTL_SECONDS
    ):
        raise ValueError(
            "E_INTEGRATION_EFFECT_PLAN: exact unexpired PR binding is required"
        )
    repository = Path(str(outcome_binding["repository"])).resolve()
    remote = str(policy_git["remote"])
    base = str(policy_git["base_branch"])
    branch = str(outcome_binding["branch"])
    if branch == base:
        raise ValueError(
            "E_INTEGRATION_EFFECT_PLAN: direct base integration is forbidden"
        )
    remote_url, remote_url_digest, _ = _outcome_remote_url_and_identity(
        repository, remote
    )
    remote_identity = _canonical_github_repository_from_url(
        remote_url, code="E_INTEGRATION_EFFECT_PLAN"
    )
    try:
        observed_repository, observed_number = _github_pull_request_url_identity(
            pull_request_url, code="E_INTEGRATION_EFFECT_PLAN"
        )
    except ValueError as error:
        raise ValueError(
            "E_INTEGRATION_EFFECT_PLAN: pull request identity is invalid"
        ) from error
    head_sha = str(outcome_binding["committed_head"])
    expected_pr_digest = contract_digest(
        {
            "number": pull_request_number,
            "url": pull_request_url,
            "head": head_sha,
            "draft": True,
        }
    )
    if (
        observed_repository != remote_identity
        or observed_number != pull_request_number
        or expected_pr_digest != outcome_binding.get("pull_request_digest")
    ):
        raise ValueError(
            "E_INTEGRATION_EFFECT_PLAN: pull request binding drifted"
        )
    argv = (
        "gh",
        "pr",
        "merge",
        str(pull_request_number),
        "--repo",
        remote_identity,
        "--match-head-commit",
        head_sha,
        "--squash",
    )
    observation_argv = (
        "gh",
        "pr",
        "view",
        str(pull_request_number),
        "--repo",
        remote_identity,
        "--json",
        "number,url,state,isDraft,baseRefName,headRefName,headRefOid,mergeCommit,mergedAt",
    )
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "IntegrationEffectPlanV1",
        "task_id": outcome_binding["task_id"],
        "task_digest": task_digest,
        "run_plan_digest": outcome_binding["run_plan_digest"],
        "requested_outcome": "integration",
        "repository": str(repository),
        "remote": remote,
        "remote_url": remote_url,
        "remote_url_digest": remote_url_digest,
        "remote_identity": remote_identity,
        "remote_identity_digest": contract_digest(remote_identity),
        "base": base,
        "branch": branch,
        "head_sha": head_sha,
        "scope_paths": list(scope_paths),
        "subject_digest": outcome_binding["binding_digest"],
        "policy_digest": contract_digest(policy),
        "effect": "integration",
        "integration_strategy": "squash",
        "pull_request_number": pull_request_number,
        "pull_request_url": pull_request_url,
        "pull_request_digest": outcome_binding["pull_request_digest"],
        "checks_digest": outcome_binding["checks_digest"],
        "prepared_at": prepared_at,
        "expires_at": expires_at,
        "argv": list(argv),
        "argv_digest": contract_digest(list(argv)),
        "observation_argv": list(observation_argv),
        "observation_argv_digest": contract_digest(list(observation_argv)),
        "observe_before_retry": True,
        "authorizes": False,
    }
    return IntegrationEffectPlanV1.from_dict(
        {**core, "plan_digest": contract_digest(core)}
    )


def build_integration_receipt(
    *,
    effect_plan: IntegrationEffectPlanV1,
    status: str,
    observed_at: str,
    observed_repository: str | None = None,
    observed_base: str | None = None,
    observed_branch: str | None = None,
    observed_head_sha: str | None = None,
    observed_pr_number: int | None = None,
    observed_pr_url: str | None = None,
    observed_pr_state: str | None = None,
    observed_pr_draft: bool | None = None,
    observed_strategy: str | None = None,
    observed_checks_digest: str | None = None,
    observed_merge_sha: str | None = None,
) -> IntegrationReceiptV1:
    """Build a durable merge observation that never carries authority."""

    if type(effect_plan) is not IntegrationEffectPlanV1:
        raise ValueError("E_INTEGRATION_RECEIPT: exact effect plan is required")
    effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "IntegrationReceiptV1",
        "task_id": effect_plan.task_id,
        "task_digest": effect_plan.task_digest,
        "run_plan_digest": effect_plan.run_plan_digest,
        "requested_outcome": effect_plan.requested_outcome,
        "repository": effect_plan.repository,
        "remote": effect_plan.remote,
        "remote_url_digest": effect_plan.remote_url_digest,
        "remote_identity": effect_plan.remote_identity,
        "base": effect_plan.base,
        "branch": effect_plan.branch,
        "head_sha": effect_plan.head_sha,
        "subject_digest": effect_plan.subject_digest,
        "policy_digest": effect_plan.policy_digest,
        "effect": effect_plan.effect,
        "integration_strategy": effect_plan.integration_strategy,
        "pull_request_number": effect_plan.pull_request_number,
        "pull_request_url": effect_plan.pull_request_url,
        "pull_request_digest": effect_plan.pull_request_digest,
        "checks_digest": effect_plan.checks_digest,
        "effect_plan_digest": effect_plan.plan_digest,
        "status": status,
        "observed_repository": observed_repository,
        "observed_base": observed_base,
        "observed_branch": observed_branch,
        "observed_head_sha": observed_head_sha,
        "observed_pr_number": observed_pr_number,
        "observed_pr_url": observed_pr_url,
        "observed_pr_state": observed_pr_state,
        "observed_pr_draft": observed_pr_draft,
        "observed_strategy": observed_strategy,
        "observed_checks_digest": observed_checks_digest,
        "observed_merge_sha": observed_merge_sha,
        "observed_at": observed_at,
        "authorizes": False,
    }
    return IntegrationReceiptV1.from_dict(
        {**core, "receipt_digest": contract_digest(core)}
    )


def apply_integration_receipt(
    *,
    outcome_binding: Mapping[str, object],
    effect_plan: IntegrationEffectPlanV1,
    receipt: IntegrationReceiptV1,
) -> dict[str, object]:
    """Advance pull_request -> merged only from one exact PASS receipt."""

    from control_plane.run_workflow import (
        advance_outcome_binding,
        validate_outcome_binding,
    )

    if validate_outcome_binding(outcome_binding):
        raise ValueError("E_INTEGRATION_BINDING: OutcomeBinding is invalid")
    if "integration" in outcome_binding.get("consumed_effect_ids", ()):
        raise ValueError("E_INTEGRATION_REPLAY: integration was already consumed")
    if type(effect_plan) is not IntegrationEffectPlanV1:
        raise ValueError("E_INTEGRATION_EFFECT_PLAN: exact plan is required")
    if type(receipt) is not IntegrationReceiptV1:
        raise ValueError("E_INTEGRATION_RECEIPT: exact receipt is required")
    effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
    receipt = IntegrationReceiptV1.from_dict(receipt.to_dict())
    binding_fields = (
        ("task_id", "task_id"),
        ("run_plan_digest", "run_plan_digest"),
        ("requested_outcome", "requested_outcome"),
        ("repository", "repository"),
        ("branch", "branch"),
        ("head_sha", "committed_head"),
        ("pull_request_digest", "pull_request_digest"),
        ("checks_digest", "checks_digest"),
    )
    receipt_fields = (
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "remote",
        "remote_url_digest",
        "remote_identity",
        "base",
        "branch",
        "head_sha",
        "subject_digest",
        "policy_digest",
        "effect",
        "integration_strategy",
        "pull_request_number",
        "pull_request_url",
        "pull_request_digest",
        "checks_digest",
    )
    if (
        outcome_binding.get("consumed_effect_ids")
        not in (
            ["local_write", "commit", "remote_write", "pull_request"],
            ("local_write", "commit", "remote_write", "pull_request"),
        )
        or effect_plan.subject_digest != outcome_binding.get("binding_digest")
        or any(
            getattr(effect_plan, plan_name) != outcome_binding.get(binding_name)
            for plan_name, binding_name in binding_fields
        )
        or receipt.effect_plan_digest != effect_plan.plan_digest
        or any(
            getattr(receipt, field) != getattr(effect_plan, field)
            for field in receipt_fields
        )
    ):
        raise ValueError("E_INTEGRATION_BINDING: plan or receipt drifted")
    if receipt.status == "UNKNOWN":
        raise ValueError(
            "E_INTEGRATION_UNKNOWN: BLOCKED; observe the same PR before retry"
        )
    if receipt.status == "FAIL":
        raise ValueError(
            "E_INTEGRATION_FAIL: BLOCKED; observe the same PR before retry"
        )
    return advance_outcome_binding(
        outcome_binding,
        effect_id="integration",
        observation={
            "merge_sha": receipt.observed_merge_sha,
            "checks_digest": receipt.observed_checks_digest,
        },
    )


def _integration_receipt_matches_plan(
    effect_plan: IntegrationEffectPlanV1,
    receipt: IntegrationReceiptV1,
) -> bool:
    fields = (
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "remote",
        "remote_url_digest",
        "remote_identity",
        "base",
        "branch",
        "head_sha",
        "subject_digest",
        "policy_digest",
        "effect",
        "integration_strategy",
        "pull_request_number",
        "pull_request_url",
        "pull_request_digest",
        "checks_digest",
    )
    return (
        receipt.effect_plan_digest == effect_plan.plan_digest
        and all(
            getattr(receipt, field) == getattr(effect_plan, field)
            for field in fields
        )
    )


def build_base_refresh_receipt(
    *,
    effect_plan: IntegrationEffectPlanV1,
    integration_receipt: IntegrationReceiptV1,
    status: str,
    observed_at: str,
    observed_ref: str | None = None,
    observed_sha: str | None = None,
) -> BaseRefreshReceiptV1:
    """Record the host's exact fetch result without performing the fetch."""

    if (
        type(effect_plan) is not IntegrationEffectPlanV1
        or type(integration_receipt) is not IntegrationReceiptV1
    ):
        raise ValueError("E_BASE_REFRESH_RECEIPT: exact contracts are required")
    effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
    integration_receipt = IntegrationReceiptV1.from_dict(
        integration_receipt.to_dict()
    )
    if (
        integration_receipt.status != "PASS"
        or not _integration_receipt_matches_plan(
            effect_plan, integration_receipt
        )
    ):
        raise ValueError(
            "E_BASE_REFRESH_RECEIPT: exact PASS integration is required"
        )
    base_ref = f"refs/remotes/{effect_plan.remote}/{effect_plan.base}"
    refresh_argv = (
        "git",
        "-C",
        effect_plan.repository,
        "fetch",
        "--no-tags",
        "--no-prune",
        effect_plan.remote_url,
        f"+refs/heads/{effect_plan.base}:{base_ref}",
    )
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "BaseRefreshReceiptV1",
        "task_id": effect_plan.task_id,
        "task_digest": effect_plan.task_digest,
        "run_plan_digest": effect_plan.run_plan_digest,
        "repository": effect_plan.repository,
        "remote": effect_plan.remote,
        "remote_url": effect_plan.remote_url,
        "remote_url_digest": effect_plan.remote_url_digest,
        "remote_identity": effect_plan.remote_identity,
        "remote_identity_digest": effect_plan.remote_identity_digest,
        "base": effect_plan.base,
        "base_ref": base_ref,
        "policy_digest": effect_plan.policy_digest,
        "effect_plan_digest": effect_plan.plan_digest,
        "integration_receipt_digest": integration_receipt.receipt_digest,
        "merge_sha": integration_receipt.observed_merge_sha,
        "refresh_argv": list(refresh_argv),
        "refresh_argv_digest": contract_digest(list(refresh_argv)),
        "status": status,
        "observed_ref": observed_ref,
        "observed_sha": observed_sha,
        "observed_at": observed_at,
        "authorizes": False,
    }
    return BaseRefreshReceiptV1.from_dict(
        {**core, "receipt_digest": contract_digest(core)}
    )


def build_base_verification_receipt(
    *,
    effect_plan: IntegrationEffectPlanV1,
    integration_receipt: IntegrationReceiptV1,
    refresh_receipt: BaseRefreshReceiptV1,
    status: str,
    reason_code: str,
    observed_base_sha: str | None,
    contained: bool | None,
) -> BaseVerificationReceiptV1:
    """Build the closed read-only result after inspecting the refreshed ref."""

    if (
        type(effect_plan) is not IntegrationEffectPlanV1
        or type(integration_receipt) is not IntegrationReceiptV1
        or type(refresh_receipt) is not BaseRefreshReceiptV1
    ):
        raise ValueError(
            "E_BASE_VERIFICATION_RECEIPT: exact contracts are required"
        )
    try:
        effect_plan = IntegrationEffectPlanV1.from_dict(effect_plan.to_dict())
        integration_receipt = IntegrationReceiptV1.from_dict(
            integration_receipt.to_dict()
        )
        refresh_receipt = BaseRefreshReceiptV1.from_dict(
            refresh_receipt.to_dict()
        )
    except ValueError as error:
        raise ValueError(
            "E_BASE_VERIFICATION_RECEIPT: exact contracts are required"
        ) from error
    expected_base_ref = f"refs/remotes/{effect_plan.remote}/{effect_plan.base}"
    if (
        integration_receipt.status != "PASS"
        or not _integration_receipt_matches_plan(
            effect_plan, integration_receipt
        )
        or refresh_receipt.task_id != effect_plan.task_id
        or refresh_receipt.task_digest != effect_plan.task_digest
        or refresh_receipt.run_plan_digest != effect_plan.run_plan_digest
        or refresh_receipt.repository != effect_plan.repository
        or refresh_receipt.remote != effect_plan.remote
        or refresh_receipt.remote_url != effect_plan.remote_url
        or refresh_receipt.remote_url_digest != effect_plan.remote_url_digest
        or refresh_receipt.remote_identity != effect_plan.remote_identity
        or refresh_receipt.remote_identity_digest
        != effect_plan.remote_identity_digest
        or refresh_receipt.base != effect_plan.base
        or refresh_receipt.base_ref != expected_base_ref
        or refresh_receipt.policy_digest != effect_plan.policy_digest
        or refresh_receipt.effect_plan_digest != effect_plan.plan_digest
        or refresh_receipt.integration_receipt_digest
        != integration_receipt.receipt_digest
        or refresh_receipt.merge_sha != integration_receipt.observed_merge_sha
    ):
        raise ValueError(
            "E_BASE_VERIFICATION_RECEIPT: receipt binding drifted"
        )
    if status == "PASS" and refresh_receipt.status != "PASS":
        raise ValueError(
            "E_BASE_VERIFICATION_RECEIPT: exact PASS refresh is required"
        )
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "BaseVerificationReceiptV1",
        "task_id": effect_plan.task_id,
        "task_digest": effect_plan.task_digest,
        "run_plan_digest": effect_plan.run_plan_digest,
        "repository": effect_plan.repository,
        "remote": effect_plan.remote,
        "base": effect_plan.base,
        "base_ref": refresh_receipt.base_ref,
        "policy_digest": effect_plan.policy_digest,
        "effect_plan_digest": effect_plan.plan_digest,
        "integration_receipt_digest": integration_receipt.receipt_digest,
        "refresh_receipt_digest": refresh_receipt.receipt_digest,
        "merge_sha": integration_receipt.observed_merge_sha,
        "status": status,
        "reason_code": reason_code,
        "observed_base_sha": observed_base_sha,
        "contained": contained,
        "observed_at": refresh_receipt.observed_at,
        "authorizes": False,
    }
    return BaseVerificationReceiptV1.from_dict(
        {**core, "receipt_digest": contract_digest(core)}
    )


def build_pull_request_effect_plan(
    *,
    outcome_binding: Mapping[str, object],
    task_digest: str,
    remote: str,
    base: str,
    scope_paths: tuple[str, ...],
    policy_digest: str,
    title: str,
    body: str,
    required_checks: tuple[object, ...],
) -> OutcomeEffectPlanV1:
    """Build a non-authorizing proposal for one draft pull request."""

    from control_plane.policy import RequiredCheckCandidate
    from control_plane.run_workflow import validate_outcome_binding

    selector_rows = tuple(sorted(
        [
            (item.name, item.app, item.conclusions, item.selector_digest)
            for item in required_checks
            if isinstance(item, RequiredCheckCandidate)
        ],
        key=lambda item: item[3],
    ))

    if (
        validate_outcome_binding(outcome_binding)
        or not _outcome_digest(task_digest)
        or not _outcome_digest(policy_digest)
        or tuple(sorted(set(scope_paths))) != scope_paths
        or contract_digest(list(scope_paths))
        != outcome_binding.get("scope_paths_digest")
        or outcome_binding.get("requested_outcome")
        not in {"pull_request", "integration"}
        or outcome_binding.get("consumed_effect_ids")
        not in (
            ["local_write", "commit", "remote_write"],
            ("local_write", "commit", "remote_write"),
        )
        or outcome_binding.get("pushed_head")
        != outcome_binding.get("committed_head")
        or not _outcome_pr_content(title, body)
        or len(selector_rows) != len(required_checks)
        or not _outcome_required_checks(selector_rows, required=True)
    ):
        raise ValueError(
            "E_OUTCOME_EFFECT_PLAN: pushed OutcomeBinding and safe PR content are required"
        )
    branch = str(outcome_binding["branch"])
    repository = Path(str(outcome_binding["repository"])).resolve()
    remote_url, remote_url_digest, remote_identity_digest = (
        _outcome_remote_url_and_identity(repository, remote)
    )
    head_sha = str(outcome_binding["pushed_head"])
    title_digest = contract_digest(title)
    body_digest = contract_digest(body)
    argv = (
        "pull_request.create_draft",
        remote_identity_digest,
        base,
        branch,
        head_sha,
        title_digest,
        body_digest,
    )
    observation_argv = (
        "pull_request.observe",
        remote_identity_digest,
        base,
        branch,
        head_sha,
    )
    operation = "create_draft_pull_request"
    operation_digest = contract_digest(
        {
            "operation": operation,
            "argv": list(argv),
            "observation_argv": list(observation_argv),
        }
    )
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "OutcomeEffectPlanV1",
        "task_id": outcome_binding["task_id"],
        "task_digest": task_digest,
        "run_plan_digest": outcome_binding["run_plan_digest"],
        "requested_outcome": outcome_binding["requested_outcome"],
        "repository": str(repository),
        "remote": remote,
        "remote_url": remote_url,
        "remote_url_digest": remote_url_digest,
        "remote_identity_digest": remote_identity_digest,
        "base": base,
        "branch": branch,
        "head_sha": head_sha,
        "scope_paths": list(scope_paths),
        "subject_digest": outcome_binding["binding_digest"],
        "policy_digest": policy_digest,
        "effect": "pull_request",
        "title": title,
        "title_digest": title_digest,
        "body": body,
        "body_digest": body_digest,
        "draft": True,
        "operation": operation,
        "operation_digest": operation_digest,
        "required_checks": [
            {"name": item[0], "app": item[1], "conclusions": list(item[2]), "selector_digest": item[3]}
            for item in selector_rows
        ],
        "argv": list(argv),
        "argv_digest": contract_digest(list(argv)),
        "observation_argv": list(observation_argv),
        "observation_argv_digest": contract_digest(list(observation_argv)),
        "observe_before_retry": True,
        "authorizes": False,
    }
    return OutcomeEffectPlanV1.from_dict(
        {**core, "plan_digest": contract_digest(core)}
    )


def build_pull_request_ready_effect_plan(
    *,
    draft_effect_plan: OutcomeEffectPlanV1,
    outcome_binding: Mapping[str, object],
    pull_request_number: int,
    pull_request_url: str,
    readiness_receipts: tuple[RemoteOutcomeReceiptV1, ...],
) -> OutcomeEffectPlanV1:
    """Build the exact non-authorizing proposal for draft -> ready."""

    from control_plane.run_workflow import validate_outcome_binding

    if type(draft_effect_plan) is not OutcomeEffectPlanV1:
        raise ValueError(
            "E_PR_READY_EFFECT_PLAN: exact draft effect plan is required"
        )
    draft_effect_plan = OutcomeEffectPlanV1.from_dict(
        draft_effect_plan.to_dict()
    )
    if (
        validate_outcome_binding(outcome_binding)
        or draft_effect_plan.effect != "pull_request"
        or draft_effect_plan.draft is not True
        or draft_effect_plan.operation != "create_draft_pull_request"
        or outcome_binding.get("binding_digest")
        != draft_effect_plan.subject_digest
        or outcome_binding.get("consumed_effect_ids")
        not in (
            ["local_write", "commit", "remote_write"],
            ("local_write", "commit", "remote_write"),
        )
        or outcome_binding.get("pushed_head") != draft_effect_plan.head_sha
        or not isinstance(pull_request_number, int)
        or isinstance(pull_request_number, bool)
        or pull_request_number <= 0
        or not isinstance(readiness_receipts, tuple)
        or len(readiness_receipts) != 3
        or any(
            type(receipt) is not RemoteOutcomeReceiptV1
            for receipt in readiness_receipts
        )
    ):
        raise ValueError(
            "E_PR_READY_EFFECT_PLAN: exact passed readiness proof is required"
        )
    parsed = tuple(
        RemoteOutcomeReceiptV1.from_dict(receipt.to_dict())
        for receipt in readiness_receipts
    )
    by_kind = {receipt.observation_kind: receipt for receipt in parsed}
    if (
        set(by_kind) != {"checks", "review_threads", "comments"}
        or len(by_kind) != 3
    ):
        raise ValueError(
            "E_PR_READY_EFFECT_PLAN: exact passed readiness proof is required"
        )
    ordered = (
        by_kind["checks"],
        by_kind["review_threads"],
        by_kind["comments"],
    )
    plan_fields = (
        "task_id",
        "task_digest",
        "run_plan_digest",
        "requested_outcome",
        "repository",
        "remote",
        "remote_url",
        "remote_url_digest",
        "remote_identity_digest",
        "base",
        "branch",
        "head_sha",
        "scope_paths",
        "subject_digest",
        "policy_digest",
        "effect",
        "title_digest",
        "body_digest",
    )
    if (
        any(receipt.status != "PASS" for receipt in ordered)
        or any(
            getattr(receipt, field) != getattr(draft_effect_plan, field)
            for receipt in ordered
            for field in plan_fields
        )
        or any(receipt.draft is not True for receipt in ordered)
        or any(
            (
                receipt.observed_pr_number,
                receipt.observed_pr_url,
                receipt.observed_pr_draft,
                receipt.observed_head_sha,
            )
            != (
                pull_request_number,
                pull_request_url,
                True,
                draft_effect_plan.head_sha,
            )
            for receipt in ordered
        )
        or any(
            row[3] == "unresolved"
            for receipt in ordered[1:]
            for row in receipt.feedback
        )
        or any(status != "PASS" for _, status in ordered[0].check_results)
    ):
        raise ValueError(
            "E_PR_READY_EFFECT_PLAN: exact passed readiness proof is required"
        )
    try:
        observed_repository, observed_number = _github_pull_request_url_identity(
            pull_request_url, code="E_PR_READY_EFFECT_PLAN"
        )
        expected_repository = _canonical_github_repository_from_url(
            draft_effect_plan.remote_url, code="E_PR_READY_EFFECT_PLAN"
        )
    except ValueError as error:
        raise ValueError(
            "E_PR_READY_EFFECT_PLAN: pull request identity is invalid"
        ) from error
    if (
        observed_repository != expected_repository
        or observed_number != pull_request_number
    ):
        raise ValueError(
            "E_PR_READY_EFFECT_PLAN: pull request identity drifted"
        )
    receipt_digests = tuple(receipt.receipt_digest for receipt in ordered)
    checks_digest = contract_digest(list(receipt_digests))
    url_digest = contract_digest(pull_request_url)
    argv = (
        "pull_request.mark_ready",
        draft_effect_plan.remote_identity_digest,
        str(pull_request_number),
        url_digest,
        draft_effect_plan.base,
        draft_effect_plan.branch,
        draft_effect_plan.head_sha,
        checks_digest,
    )
    observation_argv = (
        "pull_request.observe",
        draft_effect_plan.remote_identity_digest,
        str(pull_request_number),
        url_digest,
        draft_effect_plan.base,
        draft_effect_plan.branch,
        draft_effect_plan.head_sha,
    )
    operation = "mark_pull_request_ready"
    operation_digest = contract_digest(
        {
            "operation": operation,
            "argv": list(argv),
            "observation_argv": list(observation_argv),
        }
    )
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "OutcomeEffectPlanV1",
        "task_id": draft_effect_plan.task_id,
        "task_digest": draft_effect_plan.task_digest,
        "run_plan_digest": draft_effect_plan.run_plan_digest,
        "requested_outcome": draft_effect_plan.requested_outcome,
        "repository": draft_effect_plan.repository,
        "remote": draft_effect_plan.remote,
        "remote_url": draft_effect_plan.remote_url,
        "remote_url_digest": draft_effect_plan.remote_url_digest,
        "remote_identity_digest": draft_effect_plan.remote_identity_digest,
        "base": draft_effect_plan.base,
        "branch": draft_effect_plan.branch,
        "head_sha": draft_effect_plan.head_sha,
        "scope_paths": list(draft_effect_plan.scope_paths),
        "subject_digest": draft_effect_plan.subject_digest,
        "policy_digest": draft_effect_plan.policy_digest,
        "effect": "pull_request",
        "title": None,
        "title_digest": None,
        "body": None,
        "body_digest": None,
        "draft": False,
        "operation": operation,
        "operation_digest": operation_digest,
        "required_checks": [
            {
                "name": item[0],
                "app": item[1],
                "conclusions": list(item[2]),
                "selector_digest": item[3],
            }
            for item in draft_effect_plan.required_checks
        ],
        "argv": list(argv),
        "argv_digest": contract_digest(list(argv)),
        "observation_argv": list(observation_argv),
        "observation_argv_digest": contract_digest(list(observation_argv)),
        "observe_before_retry": True,
        "authorizes": False,
    }
    return OutcomeEffectPlanV1.from_dict(
        {**core, "plan_digest": contract_digest(core)}
    )


def build_remote_outcome_receipt(
    *,
    effect_plan: OutcomeEffectPlanV1,
    status: str,
    observed_at: str,
    observed_repository: str | None = None,
    observed_remote: str | None = None,
    observed_base: str | None = None,
    observed_branch: str | None = None,
    observed_head_sha: str | None = None,
) -> RemoteOutcomeReceiptV1:
    """Publish only a durable observation; this does not assert provenance."""

    if type(effect_plan) is not OutcomeEffectPlanV1:
        raise ValueError("E_REMOTE_OUTCOME_RECEIPT: exact effect plan is required")
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "RemoteOutcomeReceiptV1",
        "task_id": effect_plan.task_id,
        "task_digest": effect_plan.task_digest,
        "run_plan_digest": effect_plan.run_plan_digest,
        "requested_outcome": effect_plan.requested_outcome,
        "repository": effect_plan.repository,
        "remote": effect_plan.remote,
        "remote_url": effect_plan.remote_url,
        "remote_url_digest": effect_plan.remote_url_digest,
        "remote_identity_digest": effect_plan.remote_identity_digest,
        "base": effect_plan.base,
        "branch": effect_plan.branch,
        "head_sha": effect_plan.head_sha,
        "scope_paths": list(effect_plan.scope_paths),
        "subject_digest": effect_plan.subject_digest,
        "policy_digest": effect_plan.policy_digest,
        "effect": effect_plan.effect,
        "title_digest": effect_plan.title_digest,
        "body_digest": effect_plan.body_digest,
        "draft": effect_plan.draft,
        "effect_plan_digest": effect_plan.plan_digest,
        "status": status,
        "observed_repository": observed_repository,
        "observed_remote": observed_remote,
        "observed_base": observed_base,
        "observed_branch": observed_branch,
        "observed_head_sha": observed_head_sha,
        "observed_pr_number": None,
        "observed_pr_url": None,
        "observed_pr_draft": None,
        "disposition": None,
        "observation_kind": None,
        "required_check_digests": [],
        "check_results": [],
        "feedback": [],
        "observed_at": observed_at,
        "authorizes": False,
    }
    return RemoteOutcomeReceiptV1.from_dict(
        {**core, "receipt_digest": contract_digest(core)}
    )


def build_pull_request_outcome_receipt(
    *,
    effect_plan: OutcomeEffectPlanV1,
    status: str,
    observed_at: str,
    observed_repository: str | None = None,
    observed_remote: str | None = None,
    observed_base: str | None = None,
    observed_branch: str | None = None,
    observed_head_sha: str | None = None,
    observed_pr_number: int | None = None,
    observed_pr_url: str | None = None,
    observed_pr_draft: bool | None = None,
    disposition: str | None = None,
) -> RemoteOutcomeReceiptV1:
    """Record one bounded PR observation without provider authority."""

    if (
        type(effect_plan) is not OutcomeEffectPlanV1
        or effect_plan.effect != "pull_request"
    ):
        raise ValueError("E_REMOTE_OUTCOME_RECEIPT: exact PR plan is required")
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "RemoteOutcomeReceiptV1",
        "task_id": effect_plan.task_id,
        "task_digest": effect_plan.task_digest,
        "run_plan_digest": effect_plan.run_plan_digest,
        "requested_outcome": effect_plan.requested_outcome,
        "repository": effect_plan.repository,
        "remote": effect_plan.remote,
        "remote_url": effect_plan.remote_url,
        "remote_url_digest": effect_plan.remote_url_digest,
        "remote_identity_digest": effect_plan.remote_identity_digest,
        "base": effect_plan.base,
        "branch": effect_plan.branch,
        "head_sha": effect_plan.head_sha,
        "scope_paths": list(effect_plan.scope_paths),
        "subject_digest": effect_plan.subject_digest,
        "policy_digest": effect_plan.policy_digest,
        "effect": effect_plan.effect,
        "title_digest": effect_plan.title_digest,
        "body_digest": effect_plan.body_digest,
        "draft": effect_plan.draft,
        "effect_plan_digest": effect_plan.plan_digest,
        "status": status,
        "observed_repository": observed_repository,
        "observed_remote": observed_remote,
        "observed_base": observed_base,
        "observed_branch": observed_branch,
        "observed_head_sha": observed_head_sha,
        "observed_pr_number": observed_pr_number,
        "observed_pr_url": observed_pr_url,
        "observed_pr_draft": observed_pr_draft,
        "disposition": disposition,
        "observation_kind": None,
        "required_check_digests": [],
        "check_results": [],
        "feedback": [],
        "observed_at": observed_at,
        "authorizes": False,
    }
    return RemoteOutcomeReceiptV1.from_dict(
        {**core, "receipt_digest": contract_digest(core)}
    )


def build_pull_request_readiness_receipt(
    *,
    effect_plan: OutcomeEffectPlanV1,
    observation_kind: str,
    status: str,
    observed_at: str,
    observed_repository: str | None,
    observed_remote: str | None,
    observed_base: str | None,
    observed_branch: str | None,
    observed_head_sha: str | None,
    observed_pr_number: int | None,
    observed_pr_url: str | None,
    observed_pr_draft: bool | None,
    required_check_digests: tuple[str, ...] = (),
    check_results: tuple[tuple[str, str], ...] = (),
    feedback: tuple[tuple[int, str, str, str], ...] = (),
) -> RemoteOutcomeReceiptV1:
    """Record bounded checks or feedback; never serialize provider text."""

    if type(effect_plan) is not OutcomeEffectPlanV1 or effect_plan.effect != "pull_request":
        raise ValueError("E_REMOTE_OUTCOME_RECEIPT: exact PR plan is required")
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "RemoteOutcomeReceiptV1",
        "task_id": effect_plan.task_id,
        "task_digest": effect_plan.task_digest,
        "run_plan_digest": effect_plan.run_plan_digest,
        "requested_outcome": effect_plan.requested_outcome,
        "repository": effect_plan.repository,
        "remote": effect_plan.remote,
        "remote_url": effect_plan.remote_url,
        "remote_url_digest": effect_plan.remote_url_digest,
        "remote_identity_digest": effect_plan.remote_identity_digest,
        "base": effect_plan.base,
        "branch": effect_plan.branch,
        "head_sha": effect_plan.head_sha,
        "scope_paths": list(effect_plan.scope_paths),
        "subject_digest": effect_plan.subject_digest,
        "policy_digest": effect_plan.policy_digest,
        "effect": effect_plan.effect,
        "title_digest": effect_plan.title_digest,
        "body_digest": effect_plan.body_digest,
        "draft": effect_plan.draft,
        "effect_plan_digest": effect_plan.plan_digest,
        "status": status,
        "observed_repository": observed_repository,
        "observed_remote": observed_remote,
        "observed_base": observed_base,
        "observed_branch": observed_branch,
        "observed_head_sha": observed_head_sha,
        "observed_pr_number": observed_pr_number,
        "observed_pr_url": observed_pr_url,
        "observed_pr_draft": observed_pr_draft,
        "disposition": "observed_existing",
        "observation_kind": observation_kind,
        "required_check_digests": list(required_check_digests),
        "check_results": [list(item) for item in check_results],
        "feedback": [
            {"id": item[0], "digest": item[1], "severity": item[2], "status": item[3]}
            for item in feedback
        ],
        "observed_at": observed_at,
        "authorizes": False,
    }
    return RemoteOutcomeReceiptV1.from_dict(
        {**core, "receipt_digest": contract_digest(core)}
    )


def build_pull_request_ready_outcome_receipt(
    *,
    effect_plan: OutcomeEffectPlanV1,
    status: str,
    observed_at: str,
    observed_repository: str | None = None,
    observed_remote: str | None = None,
    observed_base: str | None = None,
    observed_branch: str | None = None,
    observed_head_sha: str | None = None,
    observed_pr_number: int | None = None,
    observed_pr_url: str | None = None,
    observed_pr_draft: bool | None = None,
    disposition: str | None = None,
) -> RemoteOutcomeReceiptV1:
    """Record only the observed result of one draft -> ready effect."""

    if (
        type(effect_plan) is not OutcomeEffectPlanV1
        or effect_plan.effect != "pull_request"
        or effect_plan.operation != "mark_pull_request_ready"
        or effect_plan.draft is not False
    ):
        raise ValueError(
            "E_PR_READY_OUTCOME_RECEIPT: exact ready effect plan is required"
        )
    core: dict[str, object] = {
        "schema_version": 1,
        "kind": "RemoteOutcomeReceiptV1",
        "task_id": effect_plan.task_id,
        "task_digest": effect_plan.task_digest,
        "run_plan_digest": effect_plan.run_plan_digest,
        "requested_outcome": effect_plan.requested_outcome,
        "repository": effect_plan.repository,
        "remote": effect_plan.remote,
        "remote_url": effect_plan.remote_url,
        "remote_url_digest": effect_plan.remote_url_digest,
        "remote_identity_digest": effect_plan.remote_identity_digest,
        "base": effect_plan.base,
        "branch": effect_plan.branch,
        "head_sha": effect_plan.head_sha,
        "scope_paths": list(effect_plan.scope_paths),
        "subject_digest": effect_plan.subject_digest,
        "policy_digest": effect_plan.policy_digest,
        "effect": effect_plan.effect,
        "title_digest": None,
        "body_digest": None,
        "draft": False,
        "effect_plan_digest": effect_plan.plan_digest,
        "status": status,
        "observed_repository": observed_repository,
        "observed_remote": observed_remote,
        "observed_base": observed_base,
        "observed_branch": observed_branch,
        "observed_head_sha": observed_head_sha,
        "observed_pr_number": observed_pr_number,
        "observed_pr_url": observed_pr_url,
        "observed_pr_draft": observed_pr_draft,
        "disposition": disposition,
        "observation_kind": "ready_state",
        "required_check_digests": [],
        "check_results": [],
        "feedback": [],
        "observed_at": observed_at,
        "authorizes": False,
    }
    try:
        return RemoteOutcomeReceiptV1.from_dict(
            {**core, "receipt_digest": contract_digest(core)}
        )
    except ValueError as error:
        raise ValueError(
            "E_PR_READY_OUTCOME_RECEIPT: observation is invalid"
        ) from error


def apply_remote_write_receipt(
    *,
    outcome_binding: Mapping[str, object],
    effect_plan: OutcomeEffectPlanV1,
    receipt: RemoteOutcomeReceiptV1,
) -> dict[str, object]:
    """Advance CAS only after one exact PASS observation of the remote ref."""

    from control_plane.run_workflow import (
        advance_outcome_binding,
        validate_outcome_binding,
    )

    if validate_outcome_binding(outcome_binding):
        raise ValueError("E_OUTCOME_BINDING: binding is invalid")
    if type(effect_plan) is not OutcomeEffectPlanV1:
        raise ValueError("E_OUTCOME_EFFECT_PLAN: exact plan is required")
    if type(receipt) is not RemoteOutcomeReceiptV1:
        raise ValueError("E_REMOTE_OUTCOME_RECEIPT: exact receipt is required")
    effect_plan = OutcomeEffectPlanV1.from_dict(effect_plan.to_dict())
    receipt = RemoteOutcomeReceiptV1.from_dict(receipt.to_dict())
    if "remote_write" in outcome_binding.get("consumed_effect_ids", ()):
        raise ValueError("E_OUTCOME_REPLAY: effect has already been consumed")
    common = (
        ("task_id", "task_id"),
        ("run_plan_digest", "run_plan_digest"),
        ("requested_outcome", "requested_outcome"),
        ("repository", "repository"),
        ("branch", "branch"),
    )
    if (
        effect_plan.subject_digest != outcome_binding.get("binding_digest")
        or effect_plan.head_sha != outcome_binding.get("committed_head")
        or effect_plan.effect != "remote_write"
        or any(
            getattr(effect_plan, plan_name) != outcome_binding.get(binding_name)
            for plan_name, binding_name in common
        )
        or receipt.effect_plan_digest != effect_plan.plan_digest
        or any(
            getattr(receipt, name) != getattr(effect_plan, name)
            for name in (
                "task_id",
                "task_digest",
                "run_plan_digest",
                "requested_outcome",
                "repository",
                "remote",
                "remote_url",
                "remote_url_digest",
                "remote_identity_digest",
                "base",
                "branch",
                "head_sha",
                "scope_paths",
                "subject_digest",
                "policy_digest",
                "effect",
            )
        )
    ):
        raise ValueError("E_REMOTE_OUTCOME_BINDING: plan or receipt drifted")
    if receipt.status == "UNKNOWN":
        raise ValueError(
            "E_REMOTE_OUTCOME_UNKNOWN: BLOCKED; observe the same ref, do not retry"
        )
    if receipt.status == "FAIL":
        raise ValueError(
            "E_REMOTE_OUTCOME_FAIL: BLOCKED; bounded repair is required"
        )
    return advance_outcome_binding(
        outcome_binding,
        effect_id="remote_write",
        observation={"pushed_head": receipt.observed_head_sha},
    )


def _native_host_adapter_unavailable(_: object, __: str) -> bool:
    """Fail closed until the native host installs its identity validator."""

    return False


_native_host_object_validator: Callable[[object, str], bool] = (
    _native_host_adapter_unavailable
)


def _native_remote_executor_unavailable(
    _: str, __: tuple[str, ...], ___: int
) -> tuple[int, bytes]:
    raise ValueError("native remote provider is unavailable")


_native_host_remote_executor: Callable[
    [str, tuple[str, ...], int], tuple[int, bytes]
] = _native_remote_executor_unavailable


def _execute_native_remote(
    operation: str,
    arguments: tuple[str, ...],
    *,
    max_output_bytes: int,
) -> tuple[int, bytes]:
    try:
        result = _native_host_remote_executor(
            operation, arguments, max_output_bytes
        )
    except Exception as error:
        raise ValueError(
            "E_NATIVE_REMOTE_PROVIDER: host provider is unavailable"
        ) from error
    if (
        not isinstance(result, tuple)
        or len(result) != 2
        or not isinstance(result[0], int)
        or isinstance(result[0], bool)
        or not isinstance(result[1], bytes)
        or len(result[1]) > max_output_bytes
    ):
        raise ValueError(
            "E_NATIVE_REMOTE_PROVIDER: host provider result is invalid"
        )
    return result


def _native_host_object_is_valid(value: object, kind: str) -> bool:
    try:
        return _native_host_object_validator(value, kind) is True
    except Exception:
        return False


def _runtime_host_object_registry():
    remote_context_bindings = (
        "task_digest",
        "task_id",
        "repository_identity",
        "worktree_identity",
        "remote_repository",
        "remote_name",
        "branch",
        "head",
        "session_id",
        "invocation_id",
        "effect",
        "expected_pr_number",
        "expected_base_sha",
        "expected_checks_digest",
        "context_digest",
    )
    snapshotted_bindings = {
        "host_capability": (
            "_clock",
            "event_id",
            "session_id",
            "invocation_id",
            "capability_nonce",
            "issued_at_monotonic",
            "freshness_deadline",
        ),
        "trusted_authorization": (
            "authorization_id",
            "native_event_id",
            "task_digest",
            "session_id",
            "repository_identity",
            "worktree_identity",
            "branch",
            "expected_head",
            "subject_digest",
            "scope_paths",
            "effect",
            "operation_nonce",
            "invocation_id",
            "issued_at_monotonic",
            "expires_at_monotonic",
            "freshness_deadline",
        ),
        "completed_macos_hook_smoke": (
            "_consumed",
            "platform_name",
            "repository",
            "head",
            "artifact_digests",
            "harness_digest",
            "harness_binding_digest",
            "session_id",
            "invocation_id",
            "dedicated_temp_root",
            "observed_at_monotonic",
            "cases",
            "mechanical_result",
            "native_adapter",
            "human_hooks_review",
            "authorizes",
            "completed_digest",
        ),
        "verification_task_context": (
            "_consumed",
            "task_id",
            "task_digest",
            "profile",
            "profile_digest",
            "runtime_digest",
            "target_digest",
            "repository",
            "worktree",
            "expected_head",
            "session_id",
            "lease_digest",
            "generation",
            "execution_context_digest",
            "context_digest",
        ),
        "verification_supplemental_evidence": (
            "observation_id",
            "kind",
            "receipt_digest",
            "status",
            "subject_digest",
            "task_id",
            "task_digest",
            "head",
            "profile",
            "profile_digest",
            "generation",
            "session_id",
            "lease_digest",
            "context_digest",
            "freshness_deadline",
        ),
        "pull_request_mutation_observation": (
            "repository",
            "base",
            "head_branch",
            "head_sha",
            "number",
            "url",
            "draft",
            "session_id",
            "invocation_id",
            "freshness_deadline",
        ),
        "validated_pull_request_mutation_observation": (
            "repository",
            "base",
            "head_branch",
            "head_sha",
            "number",
            "url",
            "draft",
            "session_id",
            "invocation_id",
            "freshness_deadline",
        ),
        "independent_review_observation": (
            "_consumed",
            "task_id",
            "task_digest",
            "review_packet_digest",
            "review_kind",
            "criteria_digest",
            "findings_digest",
            "critical",
            "important",
            "status",
            "reviewer_identity",
            "reviewer_identity_digest",
            "session_id",
            "invocation_id",
            "observed_at",
            "observed_at_monotonic",
            "freshness_deadline",
            "observation_digest",
        ),
        "validated_independent_review_observation": (
            "_consumed",
            "_clock",
            "task_id",
            "task_digest",
            "review_packet_digest",
            "review_kind",
            "criteria_digest",
            "findings_digest",
            "critical",
            "important",
            "status",
            "reviewer_identity_digest",
            "session_id",
            "invocation_id",
            "observed_at",
            "freshness_deadline",
            "observation_digest",
        ),
        "remote_effect_context": remote_context_bindings,
        "validated_remote_effect_context": remote_context_bindings,
        "claimed_feature_push_context": remote_context_bindings,
        "feature_push_unknown_context": remote_context_bindings,
        "pr_request_context": remote_context_bindings,
        "claimed_pr_request_context": remote_context_bindings,
        "github_pr_write_provider": (
            "provider_id",
            "repository",
            "base_branch",
            "session_id",
            "invocation_id",
            "freshness_deadline",
        ),
        "pr_request_provider": (
            "provider_id",
            "repository",
            "base_branch",
            "session_id",
            "invocation_id",
            "freshness_deadline",
        ),
    }
    payload_snapshotted_bindings = {
        "trusted_route_decision": ("decision_digest",),
    }
    issued: dict[
        int, tuple[object, str, tuple[object, ...] | None]
    ] = {}
    registry_lock = threading.RLock()

    def snapshot(value: object, kind: str) -> tuple[object, ...] | None:
        if kind == "validated_inventory":
            try:
                return (
                    contract_digest(value._snapshot),
                    value.observation_id,
                    value.invocation_id,
                    value.task_digest,
                    value.repository_identity,
                    value.worktree_identity,
                    value.registry_digest,
                    value.snapshot_digest,
                    value.observed_at_monotonic,
                    value.freshness_deadline,
                )
            except (AttributeError, TypeError, ValueError):
                return ()
        payload_names = payload_snapshotted_bindings.get(kind)
        if payload_names is not None:
            try:
                return (
                    contract_digest(value.payload),
                    *(getattr(value, name) for name in payload_names),
                )
            except (AttributeError, TypeError, ValueError):
                return ()
        if kind in {
            "pr_mutation_request",
            "pr_mutation_unknown_request",
        }:
            try:
                context = value.context
                provider = value.provider
                title = value.title
                body = value.body
                return (
                    context,
                    context.context_digest,
                    context.remote_repository,
                    context.remote_name,
                    context.branch,
                    context.head,
                    provider,
                    provider.provider_id,
                    provider.repository,
                    provider.base_branch,
                    provider.session_id,
                    provider.invocation_id,
                    provider.freshness_deadline,
                    title,
                    title.value,
                    title.digest,
                    body,
                    body.value,
                    body.digest,
                    value.draft,
                    value.expected_pr_number,
                    value.session_id,
                    value.invocation_id,
                    value.request_digest,
                    value._effect_bindings,
                    (
                        value._execution_state
                        if kind == "pr_mutation_unknown_request"
                        else "ready"
                    ),
                    (
                        value._recovery_consumed
                        if kind == "pr_mutation_unknown_request"
                        else False
                    ),
                )
            except AttributeError:
                return ()
        names = snapshotted_bindings.get(kind)
        if names is None:
            return None
        try:
            return tuple(getattr(value, name) for name in names)
        except AttributeError:
            return ()

    def register(value: object, kind: str) -> None:
        with registry_lock:
            issued[id(value)] = (value, kind, snapshot(value, kind))

    def is_live(value: object, kind: str) -> bool:
        with registry_lock:
            entry = issued.get(id(value))
            return (
                entry is not None
                and entry[0] is value
                and entry[1] == kind
                and entry[2] == snapshot(value, kind)
            )

    def consume(value: object, kind: str) -> bool:
        with registry_lock:
            entry = issued.get(id(value))
            if (
                entry is None
                or entry[0] is not value
                or entry[1] != kind
                or entry[2] != snapshot(value, kind)
            ):
                return False
            issued.pop(id(value), None)
            return True

    return register, is_live, consume


(
    _register_runtime_host_object,
    _runtime_host_object_is_live,
    _consume_runtime_host_object,
) = _runtime_host_object_registry()


@dataclass(frozen=True)
class WorktreePorcelainEntry:
    worktree: str
    head: str
    branch: str | None
    detached: bool


@dataclass(frozen=True)
class WorktreeInventoryRecord:
    worktree: str
    git_dir: str
    head: str
    branch: str | None
    detached: bool


def parse_worktree_porcelain(
    payload: bytes, *, max_worktrees: int, max_output_bytes: int
) -> tuple[WorktreePorcelainEntry, ...]:
    """Parse a complete bounded ``git worktree list --porcelain`` response."""

    if (
        not isinstance(payload, bytes)
        or not isinstance(max_worktrees, int)
        or isinstance(max_worktrees, bool)
        or not 1 <= max_worktrees <= 256
        or not isinstance(max_output_bytes, int)
        or isinstance(max_output_bytes, bool)
        or max_output_bytes <= 0
        or len(payload) > max_output_bytes
        or not payload
        or not payload.endswith(b"\n\n")
        or b"\x00" in payload
    ):
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: incomplete worktree inventory"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: worktree inventory is not UTF-8"
        ) from error
    blocks = text[:-2].split("\n\n")
    if len(blocks) > max_worktrees:
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: worktree inventory exceeds cap"
        )
    entries: list[WorktreePorcelainEntry] = []
    seen: set[str] = set()
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].startswith("worktree "):
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: malformed worktree record"
            )
        worktree = lines[0][len("worktree ") :]
        head = lines[1][len("HEAD ") :] if lines[1].startswith("HEAD ") else ""
        branch: str | None = None
        detached = False
        for line in lines[2:]:
            if line.startswith("branch refs/heads/"):
                if branch is not None or detached:
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: ambiguous worktree branch"
                    )
                branch = line[len("branch refs/heads/") :]
            elif line == "detached":
                if branch is not None or detached:
                    raise ValueError(
                        "E_LEASE_OBSERVATION_UNKNOWN: ambiguous worktree branch"
                    )
                detached = True
            elif line == "locked" or line.startswith(("locked ", "prunable ")):
                continue
            else:
                raise ValueError(
                    "E_LEASE_OBSERVATION_UNKNOWN: unknown worktree field"
                )
        path = Path(worktree)
        if (
            not path.is_absolute()
            or worktree in seen
            or _GIT_OBJECT_ID.fullmatch(head) is None
            or (branch is None) == (not detached)
        ):
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: invalid worktree identity"
            )
        seen.add(worktree)
        entries.append(
            WorktreePorcelainEntry(
                worktree=worktree,
                head=head,
                branch=branch,
                detached=detached,
            )
        )
    return tuple(entries)


def _regular_directory(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: Git directory is unavailable"
        )
    return path.resolve()


def _resolve_worktree_git_dir(worktree: Path, common_dir: Path) -> Path:
    marker = worktree / ".git"
    if marker.is_symlink():
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: worktree Git marker is a symlink"
        )
    if marker.is_dir():
        resolved = marker.resolve()
    elif marker.is_file():
        if marker.stat().st_size > 4096:
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: worktree Git marker exceeds cap"
            )
        content = marker.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir: "):
            raise ValueError(
                "E_LEASE_OBSERVATION_UNKNOWN: malformed worktree Git marker"
            )
        raw = Path(content[len("gitdir: ") :])
        resolved = (marker.parent / raw).resolve() if not raw.is_absolute() else raw.resolve()
    else:
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: worktree Git marker is unavailable"
        )
    _regular_directory(resolved)
    if resolved != common_dir and common_dir not in resolved.parents:
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: worktree Git dir escaped common dir"
        )
    return resolved


def _records_digest(records: tuple[WorktreeInventoryRecord, ...]) -> str:
    return contract_digest(
        [
            {
                "worktree": item.worktree,
                "git_dir": item.git_dir,
                "head": item.head,
                "branch": item.branch,
                "detached": item.detached,
            }
            for item in records
        ]
    )


def _bounded_git_admin_text(path: Path, *, max_bytes: int) -> str:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > max_bytes
    ):
        raise ValueError(
            "E_LEASE_OBSERVATION_STALE: Git identity file is unavailable"
        )
    try:
        return path.read_bytes().decode("utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(
            "E_LEASE_OBSERVATION_STALE: Git identity file is invalid"
        ) from error


def _resolve_live_branch_head(common_dir: Path, ref: str) -> str:
    parsed = PurePosixPath(ref)
    if (
        not ref.startswith("refs/heads/")
        or parsed.is_absolute()
        or ".." in parsed.parts
        or "\\" in ref
        or "\x00" in ref
    ):
        raise ValueError(
            "E_LEASE_OBSERVATION_STALE: worktree branch ref is invalid"
        )
    loose = common_dir.joinpath(*parsed.parts)
    parent = loose.parent
    while parent != common_dir:
        if parent.is_symlink():
            raise ValueError(
                "E_LEASE_OBSERVATION_STALE: branch ref path is unsafe"
            )
        parent = parent.parent
    if loose.exists():
        head = _bounded_git_admin_text(loose, max_bytes=256)
        if _GIT_OBJECT_ID.fullmatch(head) is None:
            raise ValueError(
                "E_LEASE_OBSERVATION_STALE: branch head is invalid"
            )
        return head
    packed = common_dir / "packed-refs"
    if not packed.exists():
        raise ValueError(
            "E_LEASE_OBSERVATION_STALE: branch head is unavailable"
        )
    for line in _bounded_git_admin_text(
        packed, max_bytes=4_194_304
    ).splitlines():
        if not line or line.startswith(("#", "^")):
            continue
        try:
            object_id, candidate = line.split(" ", 1)
        except ValueError as error:
            raise ValueError(
                "E_LEASE_OBSERVATION_STALE: packed refs are invalid"
            ) from error
        if candidate == ref:
            if _GIT_OBJECT_ID.fullmatch(object_id) is None:
                raise ValueError(
                    "E_LEASE_OBSERVATION_STALE: packed branch head is invalid"
                )
            return object_id
    raise ValueError(
        "E_LEASE_OBSERVATION_STALE: branch head is unavailable"
    )


def _live_worktree_record(
    item: WorktreeInventoryRecord, common_dir: Path
) -> WorktreeInventoryRecord:
    worktree = Path(item.worktree)
    git_dir = _resolve_worktree_git_dir(worktree, common_dir)
    head_value = _bounded_git_admin_text(
        git_dir / "HEAD", max_bytes=4096
    )
    if head_value.startswith("ref: "):
        ref = head_value[len("ref: ") :]
        head = _resolve_live_branch_head(common_dir, ref)
        branch = ref[len("refs/heads/") :]
        detached = False
    else:
        if _GIT_OBJECT_ID.fullmatch(head_value) is None:
            raise ValueError(
                "E_LEASE_OBSERVATION_STALE: detached head is invalid"
            )
        head = head_value
        branch = None
        detached = True
    return WorktreeInventoryRecord(
        worktree=str(worktree.resolve()),
        git_dir=str(git_dir),
        head=head,
        branch=branch,
        detached=detached,
    )


class WorktreeInventoryObservation:
    __slots__ = (
        "observation_id",
        "invocation_id",
        "common_git_dir",
        "records",
        "identity_digest",
        "observed_at_monotonic",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "WorktreeInventoryObservation":
        raise TypeError("WorktreeInventoryObservation is host-bound")


class ValidatedWorktreeInventoryObservation:
    __slots__ = (
        "_consumed",
        "_clock",
        "_claim_lock",
        "observation_id",
        "invocation_id",
        "common_git_dir",
        "records",
        "identity_digest",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedWorktreeInventoryObservation":
        raise TypeError("ValidatedWorktreeInventoryObservation is host-bound")


class InventoryObservation:
    __slots__ = (
        "_consumed",
        "observation_id",
        "invocation_id",
        "task_digest",
        "repository_identity",
        "worktree_identity",
        "registry_digest",
        "snapshot_digest",
        "snapshot",
        "observed_at_monotonic",
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "InventoryObservation":
        raise TypeError("InventoryObservation is host-bound")


class ValidatedInventory:
    __slots__ = (
        "_snapshot",
        "observation_id",
        "invocation_id",
        "task_digest",
        "repository_identity",
        "worktree_identity",
        "registry_digest",
        "snapshot_digest",
        "observed_at_monotonic",
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "ValidatedInventory":
        raise TypeError("ValidatedInventory is host-bound")

    def _snapshot_for_router(
        self, *, expected_task_digest: str, expected_registry_digest: str
    ) -> dict[str, object]:
        if (
            self.task_digest != expected_task_digest
            or self.registry_digest != expected_registry_digest
            or self.snapshot_digest != self._snapshot.get("snapshot_digest")
            or not _runtime_host_object_is_live(
                self, "validated_inventory"
            )
        ):
            raise ValueError(
                "E_INVENTORY_OBSERVATION: validated inventory binding mismatch"
            )
        return copy.deepcopy(self._snapshot)


class NativeSessionEvent:
    __slots__ = (
        "_consumed",
        "event_id",
        "session_id",
        "invocation_id",
        "observed_at_monotonic",
    )

    def __new__(cls, *_: object, **__: object) -> "NativeSessionEvent":
        raise TypeError("NativeSessionEvent is supplied only by the host")


class NativeUserInteractionEvent:
    __slots__ = (
        "_consumed",
        "event_id",
        "session_id",
        "invocation_id",
        "task_digest",
        "subject_digest",
        "observed_at_monotonic",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "NativeUserInteractionEvent":
        raise TypeError(
            "NativeUserInteractionEvent is supplied only by the host"
        )


class HostAdapterCapability:
    __slots__ = (
        "_consumed",
        "_clock",
        "event_id",
        "session_id",
        "invocation_id",
        "capability_nonce",
        "issued_at_monotonic",
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "HostAdapterCapability":
        raise TypeError("HostAdapterCapability is host-bound")


class HostAdapterUnavailable:
    __slots__ = ()

    def __new__(cls, *_: object, **__: object) -> "HostAdapterUnavailable":
        raise TypeError("HostAdapterUnavailable is a closed singleton")


HOST_ADAPTER_UNAVAILABLE = object.__new__(HostAdapterUnavailable)


class TrustedRouteDecision(Mapping[str, object]):
    """Opaque, immutable view of one decision emitted by the router."""

    __slots__ = ("_payload", "decision_digest")

    def __new__(cls, *_: object, **__: object) -> "TrustedRouteDecision":
        raise TypeError("TrustedRouteDecision is emitted only by the router")

    def __getitem__(self, key: str) -> object:
        return copy.deepcopy(self._payload[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    @property
    def payload(self) -> dict[str, object]:
        return copy.deepcopy(self._payload)

    def _payload_for_authority(self) -> dict[str, object]:
        supplied = self._payload.get("decision_digest")
        unsigned = {
            key: value
            for key, value in self._payload.items()
            if key not in {"decision_digest", "command"}
        }
        if (
            type(self) is not TrustedRouteDecision
            or not _runtime_host_object_is_live(
                self, "trusted_route_decision"
            )
            or supplied != self.decision_digest
            or not isinstance(supplied, str)
            or SHA256_DIGEST.fullmatch(supplied) is None
            or supplied != contract_digest(unsigned)
        ):
            raise ValueError(
                "R_UNTRUSTED_ROUTE_DECISION: router-issued decision is required"
            )
        return copy.deepcopy(self._payload)


def _seal_trusted_route_decision(
    payload: Mapping[str, object],
) -> TrustedRouteDecision:
    """Internal router seam; serialized callers cannot reconstruct the result."""

    copied = copy.deepcopy(dict(payload))
    supplied = copied.get("decision_digest")
    unsigned = {
        key: value
        for key, value in copied.items()
        if key not in {"decision_digest", "command"}
    }
    if (
        not isinstance(supplied, str)
        or SHA256_DIGEST.fullmatch(supplied) is None
        or supplied != contract_digest(unsigned)
    ):
        raise ValueError(
            "R_ROUTE_DECISION: router attempted to seal an invalid decision"
        )
    decision = object.__new__(TrustedRouteDecision)
    decision._payload = copied
    decision.decision_digest = supplied
    _register_runtime_host_object(decision, "trusted_route_decision")
    return decision


def _host_adapter_capability_is_live(value: object) -> bool:
    return bool(
        type(value) is HostAdapterCapability
        and not value._consumed
        and _runtime_host_object_is_live(value, "host_capability")
    )


class TrustedAuthorization:
    __slots__ = (
        "_consumed",
        "authorization_id",
        "native_event_id",
        "task_digest",
        "session_id",
        "repository_identity",
        "worktree_identity",
        "branch",
        "expected_head",
        "subject_digest",
        "scope_paths",
        "effect",
        "operation_nonce",
        "invocation_id",
        "issued_at_monotonic",
        "expires_at_monotonic",
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "TrustedAuthorization":
        raise TypeError("TrustedAuthorization is host-bound")


class TrustedLeaseRecoveryAuthorization:
    __slots__ = (
        "_consumed",
        "_clock",
        "authorization_id",
        "task_id",
        "task_digest",
        "common_git_dir",
        "worktree",
        "branch",
        "owner_session_id",
        "recovering_session_id",
        "policy_digest",
        "lease_digest",
        "inventory_observation_id",
        "inventory_identity_digest",
        "invocation_id",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "TrustedLeaseRecoveryAuthorization":
        raise TypeError("TrustedLeaseRecoveryAuthorization is host-bound")


def frame_lease_recovery_authorization(
    *,
    native_confirmation_event: object,
    task_id: str,
    worktree: Path | str,
    branch: str,
    owner_session_id: str,
    recovering_session_id: str,
    policy_digest: str,
    lease_digest: str,
    inventory: object,
    invocation_id: str,
    host_capability: object,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> TrustedLeaseRecoveryAuthorization:
    """Frame an explicit one-shot abandonment confirmation without takeover."""

    if (
        not isinstance(native_confirmation_event, NativeUserInteractionEvent)
        or not isinstance(host_capability, HostAdapterCapability)
        or not isinstance(inventory, ValidatedWorktreeInventoryObservation)
    ):
        raise ValueError(
            "E_LEASE_RECOVERY_UNAUTHORIZED: native confirmation, capability, "
            "and inventory are required"
        )
    canonical_worktree = _canonical_directory(
        worktree, code="E_LEASE_RECOVERY_UNAUTHORIZED"
    )
    matching = next(
        (
            item
            for item in inventory.records
            if item.worktree == str(canonical_worktree)
        ),
        None,
    )
    subject_digest = contract_digest(
        {"task_id": task_id, "lease_digest": lease_digest}
    )
    now = float(clock())
    if (
        type(native_confirmation_event) is not NativeUserInteractionEvent
        or not _native_host_object_is_valid(
            native_confirmation_event, "user_interaction"
        )
        or native_confirmation_event._consumed
        or type(host_capability) is not HostAdapterCapability
        or not _runtime_host_object_is_live(
            host_capability, "host_capability"
        )
        or host_capability._consumed
        or inventory._consumed
        or matching is None
        or matching.branch != branch
        or native_confirmation_event.session_id != recovering_session_id
        or native_confirmation_event.invocation_id != invocation_id
        or native_confirmation_event.subject_digest != subject_digest
        or host_capability.session_id != recovering_session_id
        or host_capability.invocation_id != invocation_id
        or now > host_capability.freshness_deadline
        or owner_session_id == recovering_session_id
        or not validate_task_id(task_id)
        or not validate_task_id(owner_session_id)
        or not validate_task_id(recovering_session_id)
        or SHA256_DIGEST.fullmatch(policy_digest) is None
        or SHA256_DIGEST.fullmatch(lease_digest) is None
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_LEASE_RECOVERY_UNAUTHORIZED: recovery binding is invalid"
        )
    if not _consume_runtime_host_object(
        host_capability, "host_capability"
    ):
        raise ValueError(
            "E_LEASE_RECOVERY_UNAUTHORIZED: host capability is not issued"
        )
    native_confirmation_event._consumed = True
    host_capability._consumed = True
    framed = object.__new__(TrustedLeaseRecoveryAuthorization)
    framed._consumed = False
    framed._clock = clock
    framed.authorization_id = f"lease-recovery-{uuid4().hex}"
    framed.task_id = task_id
    framed.task_digest = native_confirmation_event.task_digest
    framed.common_git_dir = inventory.common_git_dir
    framed.worktree = str(canonical_worktree)
    framed.branch = branch
    framed.owner_session_id = owner_session_id
    framed.recovering_session_id = recovering_session_id
    framed.policy_digest = policy_digest
    framed.lease_digest = lease_digest
    framed.inventory_observation_id = inventory.observation_id
    framed.inventory_identity_digest = inventory.identity_digest
    framed.invocation_id = invocation_id
    framed.freshness_deadline = now + float(ttl_seconds)
    _register_runtime_host_object(
        framed, "lease_recovery_authorization"
    )
    return framed


def consume_lease_recovery_authorization(
    authorization: object,
    *,
    task_id: str,
    worktree: Path | str,
    branch: str,
    owner_session_id: str,
    policy_digest: str,
    lease_digest: str,
    inventory: object,
    expected_common_git_dir: Path,
) -> TrustedLeaseRecoveryAuthorization:
    """Consume recovery authorization and its exact inventory under the lease lock."""

    if not isinstance(authorization, TrustedLeaseRecoveryAuthorization):
        raise ValueError(
            "E_LEASE_RECOVERY_UNAUTHORIZED: trusted recovery authorization required"
        )
    if not isinstance(inventory, ValidatedWorktreeInventoryObservation):
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: validated inventory required"
        )
    canonical_worktree = _canonical_directory(
        worktree, code="E_LEASE_RECOVERY_UNAUTHORIZED"
    )
    if authorization._consumed:
        raise ValueError(
            "E_LEASE_RECOVERY_UNAUTHORIZED: recovery authorization was consumed"
        )
    if (
        type(authorization) is not TrustedLeaseRecoveryAuthorization
        or not _runtime_host_object_is_live(
            authorization, "lease_recovery_authorization"
        )
        or float(authorization._clock()) > authorization.freshness_deadline
        or authorization.task_id != task_id
        or authorization.worktree != str(canonical_worktree)
        or authorization.branch != branch
        or authorization.owner_session_id != owner_session_id
        or authorization.policy_digest != policy_digest
        or authorization.lease_digest != lease_digest
        or authorization.common_git_dir != str(expected_common_git_dir.resolve())
        or authorization.inventory_observation_id != inventory.observation_id
        or authorization.inventory_identity_digest != inventory.identity_digest
        or inventory._consumed
    ):
        raise ValueError(
            "E_LEASE_RECOVERY_UNAUTHORIZED: recovery authorization binding mismatch"
        )
    _consume_worktree_inventory(
        inventory, expected_common_git_dir=expected_common_git_dir
    )
    if not _consume_runtime_host_object(
        authorization, "lease_recovery_authorization"
    ):
        raise ValueError(
            "E_LEASE_RECOVERY_UNAUTHORIZED: recovery authorization is not "
            "host-issued"
        )
    authorization._consumed = True
    return authorization


class RollbackPlanObservation:
    """One host-issued structured rollback plan; never serialized as authority."""

    __slots__ = (
        "_consumed", "task_id", "task_digest", "run_plan_digest",
        "run_revision_digest", "attempt", "repository", "branch", "head",
        "scope_paths_digest", "trigger_conditions", "rollback_steps",
        "post_rollback_checks", "irreversible_boundaries", "status",
        "session_id", "invocation_id", "observed_at",
        "observed_at_monotonic", "freshness_deadline", "observation_digest",
    )

    def __new__(cls, *_: object, **__: object) -> "RollbackPlanObservation":
        raise TypeError("RollbackPlanObservation is host-bound")


class ValidatedRollbackPlanObservation:
    """Process-local one-shot proof for an exact structured rollback plan."""

    __slots__ = (
        "_consumed", "_clock", "task_id", "task_digest", "run_plan_digest",
        "run_revision_digest", "attempt", "repository", "branch", "head",
        "scope_paths_digest", "trigger_conditions", "rollback_steps",
        "post_rollback_checks", "irreversible_boundaries", "status",
        "session_id", "invocation_id", "observed_at", "freshness_deadline",
        "observation_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedRollbackPlanObservation":
        raise TypeError("ValidatedRollbackPlanObservation is host-bound")


_ROLLBACK_PLAN_OBSERVATION_DIGEST_FIELDS = (
    "task_id", "task_digest", "run_plan_digest", "run_revision_digest",
    "attempt", "repository", "branch", "head", "scope_paths_digest",
    "trigger_conditions", "rollback_steps", "post_rollback_checks",
    "irreversible_boundaries", "status", "session_id", "invocation_id",
    "observed_at", "observed_at_monotonic", "freshness_deadline",
)


def _rollback_plan_content_is_valid(observation: object) -> bool:
    def bounded(value: object) -> bool:
        return bool(
            isinstance(value, str)
            and 1 <= len(value.encode("utf-8")) <= 512
            and "\x00" not in value
        )

    triggers = getattr(observation, "trigger_conditions", None)
    steps = getattr(observation, "rollback_steps", None)
    checks = getattr(observation, "post_rollback_checks", None)
    boundaries = getattr(observation, "irreversible_boundaries", None)
    status = getattr(observation, "status", None)
    if (
        type(triggers) is not tuple
        or type(steps) is not tuple
        or type(checks) is not tuple
        or type(boundaries) is not tuple
        or status not in {"PASS", "UNKNOWN"}
        or any(type(row) is not tuple for row in (*triggers, *steps, *checks, *boundaries))
        or any(len(row) != 2 or not all(bounded(item) for item in row) for row in triggers)
        or any(
            len(row) != 4
            or not isinstance(row[0], int)
            or isinstance(row[0], bool)
            or not all(bounded(item) for item in row[1:])
            for row in steps
        )
        or any(len(row) != 2 or not all(bounded(item) for item in row) for row in checks)
        or any(len(row) != 2 or not all(bounded(item) for item in row) for row in boundaries)
    ):
        return False
    if status == "UNKNOWN":
        return not triggers and not steps and not checks and not boundaries
    return bool(
        1 <= len(triggers) <= 16
        and 1 <= len(steps) <= 32
        and 1 <= len(checks) <= 16
        and 1 <= len(boundaries) <= 16
        and tuple(row[0] for row in steps) == tuple(range(1, len(steps) + 1))
        and len({row[0] for row in checks}) == len(checks)
    )


def _rollback_plan_observation_digest(
    observation: RollbackPlanObservation,
) -> str:
    return contract_digest({
        name: getattr(observation, name)
        for name in _ROLLBACK_PLAN_OBSERVATION_DIGEST_FIELDS
    })


def validate_rollback_plan_observation(
    observation: object,
    *,
    run_plan: Mapping[str, object],
    run_revision: Mapping[str, object],
    expected_attempt: int,
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedRollbackPlanObservation:
    """Validate and frame one exact fresh host rollback-plan observation."""

    now = float(clock())
    expected_scope_digest = contract_digest({
        "scope_paths": run_plan.get("scope_paths")
    })
    if (
        type(observation) is not RollbackPlanObservation
        or observation._consumed
        or not _runtime_host_object_is_live(
            observation, "rollback_plan_observation"
        )
        or not validate_task_id(observation.task_id)
        or observation.task_id != run_plan.get("task_id")
        or observation.task_digest != run_plan.get("task_digest")
        or observation.run_plan_digest != run_plan.get("plan_digest")
        or observation.run_revision_digest != run_revision.get("revision_digest")
        or observation.attempt != expected_attempt
        or not isinstance(expected_attempt, int)
        or isinstance(expected_attempt, bool)
        or not 1 <= expected_attempt <= 3
        or observation.repository != run_plan.get("repository")
        or observation.branch != run_plan.get("branch")
        or observation.head != run_revision.get("head")
        or observation.scope_paths_digest != expected_scope_digest
        or not _rollback_plan_content_is_valid(observation)
        or observation.session_id != expected_session_id
        or observation.invocation_id != expected_invocation_id
        or not validate_task_id(expected_session_id)
        or not isinstance(expected_invocation_id, str)
        or not expected_invocation_id
        or _OUTCOME_TIMESTAMP.fullmatch(observation.observed_at) is None
        or not isinstance(observation.observed_at_monotonic, (int, float))
        or isinstance(observation.observed_at_monotonic, bool)
        or not isinstance(observation.freshness_deadline, (int, float))
        or isinstance(observation.freshness_deadline, bool)
        or not observation.observed_at_monotonic <= now <= observation.freshness_deadline
        or observation.freshness_deadline - observation.observed_at_monotonic > 300
        or observation.observation_digest
        != _rollback_plan_observation_digest(observation)
    ):
        raise ValueError(
            "E_ROLLBACK_PLAN_OBSERVATION: binding is invalid or stale"
        )
    if not _consume_runtime_host_object(
        observation, "rollback_plan_observation"
    ):
        raise ValueError(
            "E_ROLLBACK_PLAN_OBSERVATION: observation is not host-issued"
        )
    observation._consumed = True
    validated = object.__new__(ValidatedRollbackPlanObservation)
    validated._consumed = False
    validated._clock = clock
    for name in (
        "task_id", "task_digest", "run_plan_digest", "run_revision_digest",
        "attempt", "repository", "branch", "head", "scope_paths_digest",
        "trigger_conditions", "rollback_steps", "post_rollback_checks",
        "irreversible_boundaries", "status", "session_id", "invocation_id",
        "observed_at", "freshness_deadline", "observation_digest",
    ):
        setattr(validated, name, getattr(observation, name))
    _register_runtime_host_object(
        validated, "validated_rollback_plan_observation"
    )
    return validated


def inspect_rollback_plan_observation(
    observation: object,
    *,
    run_plan: Mapping[str, object],
    run_revision: Mapping[str, object],
    attempt: int,
) -> dict[str, object]:
    """Expose only closed durable rollback fields from a live exact proof."""

    if (
        type(observation) is not ValidatedRollbackPlanObservation
        or observation._consumed
        or not _runtime_host_object_is_live(
            observation, "validated_rollback_plan_observation"
        )
        or float(observation._clock()) > observation.freshness_deadline
        or observation.task_id != run_plan.get("task_id")
        or observation.task_digest != run_plan.get("task_digest")
        or observation.run_plan_digest != run_plan.get("plan_digest")
        or observation.run_revision_digest != run_revision.get("revision_digest")
        or observation.attempt != attempt
        or observation.repository != run_plan.get("repository")
        or observation.branch != run_plan.get("branch")
        or observation.head != run_revision.get("head")
    ):
        raise ValueError(
            "E_ROLLBACK_PLAN_OBSERVATION: proof is invalid or stale"
        )
    return {
        "trigger_conditions": observation.trigger_conditions,
        "rollback_steps": observation.rollback_steps,
        "post_rollback_checks": observation.post_rollback_checks,
        "irreversible_boundaries": observation.irreversible_boundaries,
        "status": observation.status,
        "observed_at": observation.observed_at,
        "observation_digest": observation.observation_digest,
    }


def consume_rollback_plan_observation(
    observation: object,
    *,
    run_plan: Mapping[str, object],
    run_revision: Mapping[str, object],
    rollback_plan: Mapping[str, object],
) -> None:
    """Consume the exact host proof immediately before durable publication."""

    inspected = inspect_rollback_plan_observation(
        observation,
        run_plan=run_plan,
        run_revision=run_revision,
        attempt=int(rollback_plan.get("attempt", 0)),
    )
    expected = {
        "trigger_conditions": tuple(
            (row["condition"], row["signal"])
            for row in rollback_plan.get("trigger_conditions", ())
        ),
        "rollback_steps": tuple(
            (row["order"], row["action"], row["target"], row["success_condition"])
            for row in rollback_plan.get("rollback_steps", ())
        ),
        "post_rollback_checks": tuple(
            (row["check_id"], row["expected"])
            for row in rollback_plan.get("post_rollback_checks", ())
        ),
        "irreversible_boundaries": tuple(
            (row["boundary"], row["mitigation"])
            for row in rollback_plan.get("irreversible_boundaries", ())
        ),
        "status": rollback_plan.get("status"),
        "observed_at": rollback_plan.get("observed_at"),
        "observation_digest": rollback_plan.get("observation_digest"),
    }
    if inspected != expected:
        raise ValueError("E_ROLLBACK_PLAN_OBSERVATION: plan binding drifted")
    assert isinstance(observation, ValidatedRollbackPlanObservation)
    if not _consume_runtime_host_object(
        observation, "validated_rollback_plan_observation"
    ):
        raise ValueError(
            "E_ROLLBACK_PLAN_OBSERVATION: proof is not host-issued"
        )
    observation._consumed = True


class IndependentReviewObservation:
    """One host-issued reviewer conclusion; never serialized as authority."""

    __slots__ = (
        "_consumed",
        "task_id",
        "task_digest",
        "review_packet_digest",
        "review_kind",
        "criteria_digest",
        "findings_digest",
        "critical",
        "important",
        "status",
        "reviewer_identity",
        "reviewer_identity_digest",
        "session_id",
        "invocation_id",
        "observed_at",
        "observed_at_monotonic",
        "freshness_deadline",
        "observation_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "IndependentReviewObservation":
        raise TypeError("IndependentReviewObservation is host-bound")


class ValidatedIndependentReviewObservation:
    """Process-local proof that an exact host observation was validated."""

    __slots__ = (
        "_consumed",
        "_clock",
        "task_id",
        "task_digest",
        "review_packet_digest",
        "review_kind",
        "criteria_digest",
        "findings_digest",
        "critical",
        "important",
        "status",
        "reviewer_identity_digest",
        "session_id",
        "invocation_id",
        "observed_at",
        "freshness_deadline",
        "observation_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedIndependentReviewObservation":
        raise TypeError(
            "ValidatedIndependentReviewObservation is host-bound"
        )


_INDEPENDENT_REVIEW_OBSERVATION_DIGEST_FIELDS = (
    "task_id",
    "task_digest",
    "review_packet_digest",
    "review_kind",
    "criteria_digest",
    "findings_digest",
    "critical",
    "important",
    "status",
    "reviewer_identity_digest",
    "session_id",
    "invocation_id",
    "observed_at",
    "observed_at_monotonic",
    "freshness_deadline",
)


def _independent_review_observation_digest(
    observation: IndependentReviewObservation,
) -> str:
    return contract_digest(
        {
            name: getattr(observation, name)
            for name in _INDEPENDENT_REVIEW_OBSERVATION_DIGEST_FIELDS
        }
    )


def validate_independent_review_observation(
    observation: object,
    *,
    review_packet: Mapping[str, object],
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedIndependentReviewObservation:
    """Validate and frame one exact, fresh, independently issued review."""

    now = float(clock())
    if type(observation) is not IndependentReviewObservation:
        raise ValueError(
            "E_INDEPENDENT_REVIEW_OBSERVATION: host observation required"
        )
    expected_identity_digest = contract_digest(
        {"reviewer_identity": observation.reviewer_identity}
    )
    if (
        observation._consumed
        or not _runtime_host_object_is_live(
            observation, "independent_review_observation"
        )
        or not validate_task_id(observation.task_id)
        or observation.task_id != review_packet.get("task_id")
        or SHA256_DIGEST.fullmatch(observation.task_digest) is None
        or observation.task_digest != review_packet.get("task_digest")
        or observation.review_packet_digest != review_packet.get("packet_digest")
        or observation.review_kind != review_packet.get("review_kind")
        or observation.criteria_digest != review_packet.get("criteria_digest")
        or SHA256_DIGEST.fullmatch(observation.findings_digest) is None
        or observation.status not in {"PASS", "FAIL", "UNKNOWN"}
        or not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (observation.critical, observation.important)
        )
        or (
            observation.status == "PASS"
            and (observation.critical != 0 or observation.important != 0)
        )
        or (
            observation.status == "FAIL"
            and observation.critical + observation.important == 0
        )
        or (
            observation.status == "UNKNOWN"
            and (observation.critical != 0 or observation.important != 0)
        )
        or not isinstance(observation.reviewer_identity, str)
        or not 1 <= len(observation.reviewer_identity) <= 256
        or observation.reviewer_identity_digest != expected_identity_digest
        or observation.session_id != expected_session_id
        or observation.invocation_id != expected_invocation_id
        or not validate_task_id(expected_session_id)
        or not isinstance(expected_invocation_id, str)
        or not expected_invocation_id
        or _OUTCOME_TIMESTAMP.fullmatch(observation.observed_at) is None
        or not isinstance(observation.observed_at_monotonic, (int, float))
        or isinstance(observation.observed_at_monotonic, bool)
        or not isinstance(observation.freshness_deadline, (int, float))
        or isinstance(observation.freshness_deadline, bool)
        or not observation.observed_at_monotonic <= now <= observation.freshness_deadline
        or observation.freshness_deadline - observation.observed_at_monotonic > 300
        or observation.observation_digest
        != _independent_review_observation_digest(observation)
    ):
        raise ValueError(
            "E_INDEPENDENT_REVIEW_OBSERVATION: binding is invalid or stale"
        )
    if not _consume_runtime_host_object(
        observation, "independent_review_observation"
    ):
        raise ValueError(
            "E_INDEPENDENT_REVIEW_OBSERVATION: observation is not host-issued"
        )
    observation._consumed = True
    validated = object.__new__(ValidatedIndependentReviewObservation)
    validated._consumed = False
    validated._clock = clock
    for name in (
        "task_id",
        "task_digest",
        "review_packet_digest",
        "review_kind",
        "criteria_digest",
        "findings_digest",
        "critical",
        "important",
        "status",
        "reviewer_identity_digest",
        "session_id",
        "invocation_id",
        "observed_at",
        "freshness_deadline",
        "observation_digest",
    ):
        setattr(validated, name, getattr(observation, name))
    _register_runtime_host_object(
        validated, "validated_independent_review_observation"
    )
    return validated


def inspect_independent_review_observation(
    observation: object,
    *,
    review_packet: Mapping[str, object],
) -> tuple[str, str]:
    """Return only the durable digests of a still-live exact proof."""

    if (
        type(observation) is not ValidatedIndependentReviewObservation
        or observation._consumed
        or not _runtime_host_object_is_live(
            observation, "validated_independent_review_observation"
        )
        or float(observation._clock()) > observation.freshness_deadline
        or observation.task_id != review_packet.get("task_id")
        or observation.task_digest != review_packet.get("task_digest")
        or observation.review_packet_digest != review_packet.get("packet_digest")
        or observation.review_kind != review_packet.get("review_kind")
        or observation.criteria_digest != review_packet.get("criteria_digest")
    ):
        raise ValueError(
            "E_INDEPENDENT_REVIEW_OBSERVATION: proof is invalid or stale"
        )
    return (
        str(observation.reviewer_identity_digest),
        str(observation.observation_digest),
    )


def consume_independent_review_observation(
    observation: object,
    *,
    review_packet: Mapping[str, object],
    receipt: Mapping[str, object],
) -> None:
    """Consume the one-shot proof immediately before durable publication."""

    reviewer_digest, observation_digest = inspect_independent_review_observation(
        observation, review_packet=review_packet
    )
    assert isinstance(observation, ValidatedIndependentReviewObservation)
    if (
        receipt.get("findings_digest") != observation.findings_digest
        or receipt.get("critical") != observation.critical
        or receipt.get("important") != observation.important
        or receipt.get("status") != observation.status
        or receipt.get("observed_at") != observation.observed_at
        or receipt.get("reviewer_identity_digest") != reviewer_digest
        or receipt.get("observation_digest") != observation_digest
    ):
        raise ValueError(
            "E_INDEPENDENT_REVIEW_OBSERVATION: receipt binding drifted"
        )
    if not _consume_runtime_host_object(
        observation, "validated_independent_review_observation"
    ):
        raise ValueError(
            "E_INDEPENDENT_REVIEW_OBSERVATION: proof is not host-issued"
        )
    observation._consumed = True


class LocalGitObservation:
    __slots__ = (
        "observation_id",
        "invocation_id",
        "task_digest",
        "repository_identity",
        "worktree_identity",
        "branch",
        "prior_head",
        "target_state",
        "session_id",
        "provider",
        "subject_digest",
        "evidence",
        "observed_at_monotonic",
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "LocalGitObservation":
        raise TypeError("LocalGitObservation is host-bound")


class ValidatedLocalGitObservation:
    __slots__ = (
        "_consumed",
        "observation_id",
        "task_digest",
        "branch",
        "prior_head",
        "target_state",
        "evidence",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedLocalGitObservation":
        raise TypeError("ValidatedLocalGitObservation is host-bound")


class GitHubObservation(LocalGitObservation):
    """Host-provided remote observation; production factory arrives in Task 9."""


class ValidatedGitHubObservation:
    __slots__ = (
        "_consumed",
        "observation_id",
        "task_digest",
        "branch",
        "prior_head",
        "target_state",
        "evidence",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedGitHubObservation":
        raise TypeError("ValidatedGitHubObservation is host-bound")


class ReleaseProviderObservation(LocalGitObservation):
    """Host-provided release observation with no serialized factory."""


class ValidatedReleaseProviderObservation:
    __slots__ = (
        "_consumed",
        "observation_id",
        "task_digest",
        "branch",
        "prior_head",
        "target_state",
        "evidence",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedReleaseProviderObservation":
        raise TypeError("ValidatedReleaseProviderObservation is host-bound")


def validate_release_provider_observation(
    observation: object,
    *,
    expected_task_digest: str,
    expected_repo: Path | str,
    expected_worktree: Path | str,
    expected_branch: str,
    expected_prior_head: str,
    expected_target_state: str,
    expected_provider: str,
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedReleaseProviderObservation:
    repository = _canonical_directory(
        expected_repo, code="E_LIFECYCLE_OBSERVATION"
    )
    worktree = _canonical_directory(
        expected_worktree, code="E_LIFECYCLE_OBSERVATION"
    )
    if type(observation) is not ReleaseProviderObservation:
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: ReleaseProviderObservation is required"
        )
    if (
        not _runtime_host_object_is_live(
            observation, "release_provider_observation"
        )
        or
        observation.task_digest != expected_task_digest
        or observation.repository_identity != str(repository)
        or observation.worktree_identity != str(worktree)
        or observation.branch != expected_branch
        or observation.prior_head != expected_prior_head
        or observation.target_state != expected_target_state
        or observation.session_id != expected_session_id
        or observation.invocation_id != expected_invocation_id
        or observation.provider != expected_provider
        or not expected_provider
        or float(clock()) > observation.freshness_deadline
        or SHA256_DIGEST.fullmatch(observation.task_digest) is None
        or SHA256_DIGEST.fullmatch(observation.subject_digest) is None
    ):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: release binding is invalid or stale"
        )
    if not _consume_runtime_host_object(
        observation, "release_provider_observation"
    ):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: release observation is not host-issued"
        )
    validated = object.__new__(ValidatedReleaseProviderObservation)
    validated._consumed = False
    validated.observation_id = observation.observation_id
    validated.task_digest = observation.task_digest
    validated.branch = observation.branch
    validated.prior_head = observation.prior_head
    validated.target_state = observation.target_state
    validated.evidence = copy.deepcopy(observation.evidence)
    _register_runtime_host_object(
        validated, "validated_release_provider_observation"
    )
    return validated


def validate_github_observation(
    observation: object,
    *,
    expected_task_digest: str,
    expected_repo: Path | str,
    expected_worktree: Path | str,
    expected_branch: str,
    expected_prior_head: str,
    expected_target_state: str,
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedGitHubObservation:
    """Validate one provider observation without exposing a serialized factory."""

    repository = _canonical_directory(
        expected_repo, code="E_LIFECYCLE_OBSERVATION"
    )
    worktree = _canonical_directory(
        expected_worktree, code="E_LIFECYCLE_OBSERVATION"
    )
    if type(observation) is not GitHubObservation:
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: GitHubObservation is required"
        )
    if (
        not _runtime_host_object_is_live(observation, "github_observation")
        or
        observation.task_digest != expected_task_digest
        or observation.repository_identity != str(repository)
        or observation.worktree_identity != str(worktree)
        or observation.branch != expected_branch
        or observation.prior_head != expected_prior_head
        or observation.target_state != expected_target_state
        or observation.session_id != expected_session_id
        or observation.invocation_id != expected_invocation_id
        or observation.provider != "github"
        or float(clock()) > observation.freshness_deadline
        or SHA256_DIGEST.fullmatch(observation.task_digest) is None
        or SHA256_DIGEST.fullmatch(observation.subject_digest) is None
    ):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: GitHub binding is invalid or stale"
        )
    if not _consume_runtime_host_object(observation, "github_observation"):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: GitHub observation is not host-issued"
        )
    validated = object.__new__(ValidatedGitHubObservation)
    validated._consumed = False
    validated.observation_id = observation.observation_id
    validated.task_digest = observation.task_digest
    validated.branch = observation.branch
    validated.prior_head = observation.prior_head
    validated.target_state = observation.target_state
    validated.evidence = copy.deepcopy(observation.evidence)
    _register_runtime_host_object(validated, "validated_github_observation")
    return validated


def validate_local_git_observation(
    observation: object,
    *,
    expected_task_digest: str,
    expected_repo: Path | str,
    expected_worktree: Path | str,
    expected_branch: str,
    expected_prior_head: str,
    expected_target_state: str,
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedLocalGitObservation:
    """Validate one local Git observation without accepting serialized evidence."""

    repository = _canonical_directory(
        expected_repo, code="E_LIFECYCLE_OBSERVATION"
    )
    worktree = _canonical_directory(
        expected_worktree, code="E_LIFECYCLE_OBSERVATION"
    )
    if type(observation) is not LocalGitObservation:
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: LocalGitObservation is required"
        )
    if (
        not _runtime_host_object_is_live(observation, "local_git_observation")
        or
        observation.task_digest != expected_task_digest
        or observation.repository_identity != str(repository)
        or observation.worktree_identity != str(worktree)
        or observation.branch != expected_branch
        or observation.prior_head != expected_prior_head
        or observation.target_state != expected_target_state
        or observation.session_id != expected_session_id
        or observation.invocation_id != expected_invocation_id
        or observation.provider != "git"
        or float(clock()) > observation.freshness_deadline
        or SHA256_DIGEST.fullmatch(observation.task_digest) is None
        or SHA256_DIGEST.fullmatch(observation.subject_digest) is None
    ):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: local Git binding is invalid or stale"
        )
    if not _consume_runtime_host_object(observation, "local_git_observation"):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: local Git observation is not host-issued"
        )
    validated = object.__new__(ValidatedLocalGitObservation)
    validated._consumed = False
    validated.observation_id = observation.observation_id
    validated.task_digest = observation.task_digest
    validated.branch = observation.branch
    validated.prior_head = observation.prior_head
    validated.target_state = observation.target_state
    validated.evidence = copy.deepcopy(observation.evidence)
    _register_runtime_host_object(
        validated, "validated_local_git_observation"
    )
    return validated


def _git_text(worktree: Path, arguments: list[str]) -> str:
    if arguments[:1] == ["status"] or (
        arguments[:1] == ["diff"]
        and "--cached" not in arguments
        and "--staged" not in arguments
        and "--no-index" not in arguments
    ):
        _assert_no_external_git_filters(worktree)
    try:
        completed = subprocess.run(
            trusted_git_argv(worktree, arguments),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=trusted_git_environment(),
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: local Git observation failed"
        ) from error
    if completed.returncode != 0:
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: local Git observation failed"
        )
    return completed.stdout.strip()


def _canonical_github_repository_from_url(
    value: object, *, code: str
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{code}: GitHub repository URL is invalid")
    match = _GITHUB_HTTPS_REMOTE.fullmatch(value)
    if (
        match is None
        or "@" in value
        or "?" in value
        or "#" in value
        or match.group("owner") in {".", ".."}
        or match.group("repository") in {".", ".."}
    ):
        raise ValueError(
            f"{code}: credential-free github.com HTTPS is required"
        )
    return (
        f"{match.group('owner').casefold()}/"
        f"{match.group('repository').casefold()}"
    )


def _canonical_github_repository_identity(
    value: object, *, code: str
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{code}: GitHub repository identity is invalid")
    match = _GITHUB_REPOSITORY_IDENTITY.fullmatch(value)
    if (
        match is None
        or match.group("owner") in {".", ".."}
        or match.group("repository") in {".", ".."}
    ):
        raise ValueError(f"{code}: GitHub repository identity is invalid")
    return (
        f"{match.group('owner').casefold()}/"
        f"{match.group('repository').casefold()}"
    )


def _github_pull_request_url_identity(
    value: object, *, code: str
) -> tuple[str, int]:
    if not isinstance(value, str):
        raise ValueError(f"{code}: GitHub pull request URL is invalid")
    match = _GITHUB_PULL_REQUEST_HTTPS_URL.fullmatch(value)
    if (
        match is None
        or match.group("owner") in {".", ".."}
        or match.group("repository") in {".", ".."}
    ):
        raise ValueError(f"{code}: GitHub pull request URL is invalid")
    repository = _canonical_github_repository_identity(
        f"{match.group('owner')}/{match.group('repository')}",
        code=code,
    )
    return repository, int(match.group("number"))


def observe_local_git_state(
    *,
    task_state: Mapping[str, object],
    expected_repo: Path | str,
    expected_worktree: Path | str,
    expected_branch: str,
    expected_prior_head: str,
    target_state: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> LocalGitObservation:
    """Observe one closed local Git transition without caller-supplied evidence."""

    repository = _canonical_directory(
        expected_repo, code="E_LIFECYCLE_OBSERVATION"
    )
    worktree = _canonical_directory(
        expected_worktree, code="E_LIFECYCLE_OBSERVATION"
    )
    task_digest = task_state.get("task_digest")
    if (
        not isinstance(task_digest, str)
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or target_state != "committed"
        or _GIT_OBJECT_ID.fullmatch(expected_prior_head) is None
        or not validate_task_id(session_id)
        or not isinstance(invocation_id, str)
        or not invocation_id
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: invalid local Git observation binding"
        )
    observed_root = Path(_git_text(worktree, ["rev-parse", "--show-toplevel"])).resolve()
    branch = _git_text(worktree, ["branch", "--show-current"])
    head = _git_text(worktree, ["rev-parse", "HEAD"])
    _assert_no_external_git_filters(worktree)
    status = subprocess.run(
        trusted_git_argv(
            worktree,
            (
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
            ),
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=trusted_git_environment(),
        timeout=10,
    )
    if (
        observed_root != worktree
        or repository != worktree
        or branch != expected_branch
        or status.returncode != 0
        or status.stdout
        or _GIT_OBJECT_ID.fullmatch(head) is None
        or head == expected_prior_head
    ):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION: local Git state is not the expected clean commit"
        )
    evidence = {"commit": head}
    subject_digest = contract_digest(
        {
            "target_state": target_state,
            "prior_head": expected_prior_head,
            "evidence": evidence,
        }
    )
    now = float(clock())
    observation = object.__new__(LocalGitObservation)
    observation.observation_id = f"local-git-{uuid4().hex}"
    observation.invocation_id = invocation_id
    observation.task_digest = task_digest
    observation.repository_identity = str(repository)
    observation.worktree_identity = str(worktree)
    observation.branch = branch
    observation.prior_head = expected_prior_head
    observation.target_state = target_state
    observation.session_id = session_id
    observation.provider = "git"
    observation.subject_digest = subject_digest
    observation.evidence = copy.deepcopy(evidence)
    observation.observed_at_monotonic = now
    observation.freshness_deadline = now + float(ttl_seconds)
    _register_runtime_host_object(observation, "local_git_observation")
    return observation


def consume_lifecycle_observation(
    observation: object,
) -> dict[str, object]:
    """Consume one validated lifecycle observation exactly once."""

    if not isinstance(
        observation,
        (
            ValidatedLocalGitObservation,
            ValidatedGitHubObservation,
            ValidatedReleaseProviderObservation,
        ),
    ):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION_REQUIRED: validated observation is required"
        )
    if observation._consumed:
        raise ValueError(
            "E_LIFECYCLE_REPLAY: lifecycle observation was already consumed"
        )
    kind = {
        ValidatedLocalGitObservation: "validated_local_git_observation",
        ValidatedGitHubObservation: "validated_github_observation",
        ValidatedReleaseProviderObservation: (
            "validated_release_provider_observation"
        ),
    }[type(observation)]
    if not _consume_runtime_host_object(observation, kind):
        raise ValueError(
            "E_LIFECYCLE_OBSERVATION_REQUIRED: validated observation must be "
            "host-issued"
        )
    observation._consumed = True
    return copy.deepcopy(observation.evidence)


@dataclass(frozen=True)
class ConsumedAuthorization:
    authorization_id: str
    task_digest: str
    effect: str
    operation_nonce: str


def _claim_capability_consumption(
    *,
    worktree: Path,
    authorization_id: str,
    operation_nonce: str,
    confirmation_id: str | None,
) -> Path:
    git_dir = _git_dir_for_worktree(worktree)
    directory = (
        git_dir / "codex-control-plane" / "capability-consumption"
    )
    directory.mkdir(parents=True, exist_ok=True)
    identity = contract_digest(
        {
            "authorization_id": authorization_id,
            "operation_nonce": operation_nonce,
        }
    ).removeprefix("sha256:")
    path = directory / f"{identity}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    payload = json.dumps(
        {
            "schema_version": 1,
            "authorization_id": authorization_id,
            "confirmation_id": confirmation_id,
            "operation_nonce": operation_nonce,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as error:
        raise ValueError(
            "Z_REPLAY: capability operation was already consumed"
        ) from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        directory_descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path


def attest_host_adapter_capability(
    native_session_event: object,
    *,
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> HostAdapterCapability:
    """Consume a native session event and frame one host capability."""

    if not isinstance(native_session_event, NativeSessionEvent):
        raise ValueError("E_HOST_CAPABILITY: native session event is required")
    if native_session_event._consumed:
        raise ValueError("E_HOST_CAPABILITY: native session event was consumed")
    if (
        type(native_session_event) is not NativeSessionEvent
        or not _native_host_object_is_valid(native_session_event, "session")
        or native_session_event.session_id != expected_session_id
        or native_session_event.invocation_id != expected_invocation_id
        or not validate_task_id(expected_session_id)
        or not isinstance(expected_invocation_id, str)
        or not expected_invocation_id
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError("E_HOST_CAPABILITY: session binding is invalid")
    now = float(clock())
    native_session_event._consumed = True
    capability = object.__new__(HostAdapterCapability)
    capability._consumed = False
    capability._clock = clock
    capability.event_id = native_session_event.event_id
    capability.session_id = native_session_event.session_id
    capability.invocation_id = native_session_event.invocation_id
    capability.capability_nonce = f"host-capability-{uuid4().hex}"
    capability.issued_at_monotonic = now
    capability.freshness_deadline = now + float(ttl_seconds)
    _register_runtime_host_object(capability, "host_capability")
    return capability




def load_governing_local_policy(
    *,
    canonical_repo: Path | str,
    governing_base_observation: object,
    expected_invocation_id: str,
    clock: Callable[[], float],
):
    """Load policy only from the installed content-addressed snapshot."""

    from control_plane.git_guards import (
        ValidatedInstalledPolicyObservation,
        _consume_validated_installed_policy,
        _validated_installed_policy_is_live,
    )
    from control_plane.policy import GoverningPolicy, _register_governing_policy

    try:
        canonical = _canonical_directory(
            canonical_repo, code="RS_LOCAL_BASE_UNKNOWN"
        )
        now = float(clock())
    except (TypeError, ValueError) as error:
        raise ValueError(
            "RS_LOCAL_BASE_UNKNOWN: canonical repository is invalid"
        ) from error
    if (
        type(governing_base_observation)
        is not ValidatedInstalledPolicyObservation
        or not _validated_installed_policy_is_live(
            governing_base_observation, clock=clock
        )
        or governing_base_observation.repository_identity != str(canonical)
        or governing_base_observation.invocation_id != expected_invocation_id
        or now > governing_base_observation.freshness_deadline
    ):
        raise ValueError(
            "RS_LOCAL_BASE_UNKNOWN: validated installed observation is required"
        )
    if not _consume_validated_installed_policy(governing_base_observation):
        raise ValueError(
            "RS_LOCAL_BASE_UNKNOWN: installed observation was replayed"
        )
    result = object.__new__(GoverningPolicy)
    result._consumed = False
    result.policy = copy.deepcopy(governing_base_observation.policy)
    result.policy_digest = governing_base_observation.policy_digest
    result.runtime_digest = governing_base_observation.runtime_digest
    result.lock_digest = governing_base_observation.lock_digest
    result.governing_base_commit = governing_base_observation.governing_base_commit
    result.remote_repository = governing_base_observation.remote_repository
    result.session_id = governing_base_observation.session_id
    result.invocation_id = expected_invocation_id
    result.freshness_deadline = min(
        governing_base_observation.freshness_deadline, now + 30.0
    )
    result.binding_digest = contract_digest(
        {
            "policy_digest": result.policy_digest,
            "runtime_digest": result.runtime_digest,
            "lock_digest": result.lock_digest,
            "governing_base_commit": result.governing_base_commit,
            "remote_repository": result.remote_repository,
            "session_id": result.session_id,
            "invocation_id": result.invocation_id,
        }
    )
    _register_governing_policy(result)
    return result




def frame_effect_authorization(
    native_user_event: object,
    *,
    host_capability: object,
    task_digest: str,
    session_id: str,
    repository_identity: Path | str,
    worktree_identity: Path | str,
    branch: str,
    expected_head: str,
    subject_digest: str,
    scope_paths: tuple[str, ...],
    effect: str,
    operation_nonce: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> TrustedAuthorization:
    """Frame one effect grant from one native user interaction."""

    if not isinstance(native_user_event, NativeUserInteractionEvent):
        raise ValueError(
            "E_AUTH_UNTRUSTED_CHANNEL: native user interaction is required"
        )
    if not isinstance(host_capability, HostAdapterCapability):
        raise ValueError("E_AUTH_UNTRUSTED_CHANNEL: host capability is required")
    repository = _canonical_directory(
        repository_identity, code="E_AUTH_UNTRUSTED_CHANNEL"
    )
    worktree = _canonical_directory(
        worktree_identity, code="E_AUTH_UNTRUSTED_CHANNEL"
    )
    normalized_scope = tuple(normalize_scope(item) for item in scope_paths)
    now = float(clock())
    if (
        type(native_user_event) is not NativeUserInteractionEvent
        or not _native_host_object_is_valid(
            native_user_event, "user_interaction"
        )
        or native_user_event._consumed
        or type(host_capability) is not HostAdapterCapability
        or not _runtime_host_object_is_live(
            host_capability, "host_capability"
        )
        or host_capability._consumed
        or now > host_capability.freshness_deadline
        or native_user_event.session_id != session_id
        or native_user_event.invocation_id != invocation_id
        or native_user_event.task_digest != task_digest
        or native_user_event.subject_digest != subject_digest
        or host_capability.session_id != session_id
        or host_capability.invocation_id != invocation_id
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or SHA256_DIGEST.fullmatch(subject_digest) is None
        or _GIT_OBJECT_ID.fullmatch(expected_head) is None
        or not isinstance(branch, str)
        or not branch
        or not scope_paths
        or any(item is None for item in normalized_scope)
        or effect not in TASK_EFFECTS
        or not validate_task_id(operation_nonce)
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_AUTH_UNTRUSTED_CHANNEL: authorization binding is invalid"
        )
    if not _consume_runtime_host_object(
        host_capability, "host_capability"
    ):
        raise ValueError(
            "E_AUTH_UNTRUSTED_CHANNEL: host capability is not issued"
        )
    native_user_event._consumed = True
    host_capability._consumed = True
    framed = object.__new__(TrustedAuthorization)
    framed._consumed = False
    framed.authorization_id = f"authorization-{uuid4().hex}"
    framed.native_event_id = native_user_event.event_id
    framed.task_digest = task_digest
    framed.session_id = session_id
    framed.repository_identity = str(repository)
    framed.worktree_identity = str(worktree)
    framed.branch = branch
    framed.expected_head = expected_head
    framed.subject_digest = subject_digest
    framed.scope_paths = tuple(str(item) for item in normalized_scope)
    framed.effect = effect
    framed.operation_nonce = operation_nonce
    framed.invocation_id = invocation_id
    framed.issued_at_monotonic = now
    framed.expires_at_monotonic = now + float(ttl_seconds)
    framed.freshness_deadline = framed.expires_at_monotonic
    _register_runtime_host_object(framed, "trusted_authorization")
    return framed


def authorization_effects_for_route(
    authorization: object,
    *,
    expected_task_digest: str,
    expected_scope_paths: tuple[str, ...],
) -> set[str]:
    """Expose only the requested effect to the non-authoritative route view."""

    if not isinstance(authorization, TrustedAuthorization):
        raise ValueError(
            "E_AUTH_UNTRUSTED_CHANNEL: serialized authorization is inert"
        )
    normalized = tuple(normalize_scope(item) for item in expected_scope_paths)
    if (
        type(authorization) is not TrustedAuthorization
        or not _runtime_host_object_is_live(
            authorization, "trusted_authorization"
        )
        or authorization._consumed
        or authorization.task_digest != expected_task_digest
        or any(item is None for item in normalized)
        or authorization.scope_paths != tuple(str(item) for item in normalized)
    ):
        raise ValueError(
            "E_AUTH_UNTRUSTED_CHANNEL: authorization does not match task"
        )
    return {authorization.effect}


def consume_authorization(
    authorization: object,
    *,
    expected_task_digest: str,
    expected_session_id: str,
    expected_repository_identity: Path | str,
    expected_worktree_identity: Path | str,
    expected_branch: str,
    expected_head: str,
    expected_subject_digest: str,
    expected_scope_paths: tuple[str, ...],
    expected_effect: str,
    expected_operation_nonce: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ConsumedAuthorization:
    """Atomically consume one authorization after every binding revalidates."""

    if not isinstance(authorization, TrustedAuthorization):
        raise ValueError(
            "E_AUTH_UNTRUSTED_CHANNEL: TrustedAuthorization is required"
        )
    repository = _canonical_directory(
        expected_repository_identity, code="E_AUTH_UNTRUSTED_CHANNEL"
    )
    worktree = _canonical_directory(
        expected_worktree_identity, code="E_AUTH_UNTRUSTED_CHANNEL"
    )
    normalized = tuple(normalize_scope(item) for item in expected_scope_paths)
    with _CAPABILITY_CONSUMPTION_LOCK:
        if authorization._consumed:
            raise ValueError("E_AUTH_REPLAY: authorization was already consumed")
        if (
            type(authorization) is not TrustedAuthorization
            or not _runtime_host_object_is_live(
                authorization, "trusted_authorization"
            )
            or float(clock()) > authorization.freshness_deadline
            or authorization.task_digest != expected_task_digest
            or authorization.session_id != expected_session_id
            or authorization.repository_identity != str(repository)
            or authorization.worktree_identity != str(worktree)
            or authorization.branch != expected_branch
            or authorization.expected_head != expected_head
            or authorization.subject_digest != expected_subject_digest
            or any(item is None for item in normalized)
            or authorization.scope_paths
            != tuple(str(item) for item in normalized)
            or authorization.effect != expected_effect
            or authorization.operation_nonce != expected_operation_nonce
            or authorization.invocation_id != expected_invocation_id
        ):
            raise ValueError(
                "E_AUTH_UNTRUSTED_CHANNEL: authorization binding is invalid or stale"
            )
        _claim_capability_consumption(
            worktree=worktree,
            authorization_id=authorization.authorization_id,
            operation_nonce=authorization.operation_nonce,
            confirmation_id=None,
        )
        if not _consume_runtime_host_object(
            authorization, "trusted_authorization"
        ):
            raise ValueError(
                "E_AUTH_UNTRUSTED_CHANNEL: authorization is not host-issued"
            )
        authorization._consumed = True
    return ConsumedAuthorization(
        authorization_id=authorization.authorization_id,
        task_digest=authorization.task_digest,
        effect=authorization.effect,
        operation_nonce=authorization.operation_nonce,
    )




def _sanitized_git_environment() -> dict[str, str]:
    empty_home = (
        Path(tempfile.gettempdir())
        / f"codex-control-plane-git-home-{os.getuid()}"
    )
    if empty_home.is_symlink():
        raise ValueError("E_GIT_ENVIRONMENT: dedicated HOME is unsafe")
    empty_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    empty_home.chmod(0o700)
    return {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(empty_home),
        "TMPDIR": tempfile.gettempdir(),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "SSH_ASKPASS": "",
        "SSH_AUTH_SOCK": "",
        "GCM_INTERACTIVE": "never",
        "GIT_SSH_COMMAND": "/usr/bin/false",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }


def _trusted_git_executable() -> str | None:
    for candidate in (Path("/usr/bin/git"), Path("/bin/git")):
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and os.access(candidate, os.X_OK)
        ):
            return str(candidate)
    return None


_CLOSED_GIT_CONFIG = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "commit.gpgSign=false",
    "-c",
    "tag.gpgSign=false",
    "-c",
    "credential.helper=",
    "-c",
    "http.sslVerify=true",
    "-c",
    "http.proxy=",
    "-c",
    "http.extraHeader=",
    "-c",
    "core.pager=cat",
    "-c",
    "diff.external=",
)


def _closed_git_argv(
    worktree: Path | str, arguments: list[str] | tuple[str, ...]
) -> list[str]:
    return [
        "git",
        *_CLOSED_GIT_CONFIG,
        "-C",
        str(worktree),
        *arguments,
    ]


def _governing_git_bytes(
    worktree: Path,
    arguments: list[str],
    *,
    max_output_bytes: int,
) -> bytes:
    completed = subprocess.run(
        trusted_git_argv(worktree, arguments),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=trusted_git_environment(),
        timeout=10,
    )
    payload = completed.stdout
    if (
        completed.returncode != 0
        or not isinstance(payload, bytes)
        or len(payload) > max_output_bytes
    ):
        raise ValueError(
            "E_GOVERNING_RUNTIME: immutable Git object is unavailable"
        )
    return payload


def _governing_tree_entries(
    worktree: Path,
    treeish: str,
    *,
    path: str | None = None,
) -> tuple[tuple[str, str, str], ...]:
    arguments = ["ls-tree", "-z", treeish]
    if path is not None:
        arguments.extend(["--", path])
    payload = _governing_git_bytes(
        worktree, arguments, max_output_bytes=262_144
    )
    entries: list[tuple[str, str, str]] = []
    for raw in payload.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_name = raw.split(b"\t", 1)
            mode, object_type, _object_id = metadata.decode(
                "ascii"
            ).split(" ", 2)
            name = raw_name.decode("utf-8")
        except (UnicodeDecodeError, ValueError) as error:
            raise ValueError(
                "E_GOVERNING_RUNTIME: immutable Git tree is invalid"
            ) from error
        if "\x00" in name or not name:
            raise ValueError(
                "E_GOVERNING_RUNTIME: immutable Git tree is invalid"
            )
        entries.append((mode, object_type, name))
    return tuple(entries)


def _governing_regular_blob(
    worktree: Path,
    commit: str,
    relative_path: str,
    *,
    max_output_bytes: int,
) -> bytes:
    entries = _governing_tree_entries(
        worktree, commit, path=relative_path
    )
    if entries != (("100644", "blob", relative_path),) and entries != (
        ("100755", "blob", relative_path),
    ):
        raise ValueError(
            "E_GOVERNING_RUNTIME: governing path is not a regular blob"
        )
    return _governing_git_bytes(
        worktree,
        ["cat-file", "blob", f"{commit}:{relative_path}"],
        max_output_bytes=max_output_bytes,
    )


def _canonical_directory(value: Path | str, *, code: str) -> Path:
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{code}: directory identity is unavailable")
    return path.resolve()


def observe_inventory(
    registry: Mapping[str, object],
    repo: Path | str,
    worktree: Path | str,
    task_digest: str,
    invocation_id: str,
    *,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> InventoryObservation:
    """Build inventory in-process and bind it to exact host-observed identities."""

    repository = _canonical_directory(repo, code="E_INVENTORY_OBSERVATION")
    target_worktree = _canonical_directory(
        worktree, code="E_INVENTORY_OBSERVATION"
    )
    if (
        SHA256_DIGEST.fullmatch(task_digest) is None
        or not isinstance(invocation_id, str)
        or not invocation_id
        or not callable(clock)
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_INVENTORY_OBSERVATION: invalid observation binding"
        )
    snapshot = build_inventory(registry, repository)
    issues = validate_inventory(registry, snapshot)
    if issues:
        raise ValueError(
            "E_INVENTORY_OBSERVATION: host inventory failed validation"
        )
    now = float(clock())
    observation = object.__new__(InventoryObservation)
    observation._consumed = False
    observation.observation_id = f"inventory-{uuid4().hex}"
    observation.invocation_id = invocation_id
    observation.task_digest = task_digest
    observation.repository_identity = str(repository)
    observation.worktree_identity = str(target_worktree)
    observation.registry_digest = registry_contract_digest(registry)
    observation.snapshot_digest = str(snapshot["snapshot_digest"])
    observation.snapshot = copy.deepcopy(dict(snapshot))
    observation.observed_at_monotonic = now
    observation.freshness_deadline = now + float(ttl_seconds)
    return observation


def validate_inventory_observation(
    observation: object,
    *,
    expected_repo: Path | str,
    expected_worktree: Path | str,
    expected_registry_digest: str,
    expected_task_digest: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedInventory:
    """Consume one exact host observation and return a non-serializable wrapper."""

    repository = _canonical_directory(
        expected_repo, code="E_INVENTORY_OBSERVATION"
    )
    worktree = _canonical_directory(
        expected_worktree, code="E_INVENTORY_OBSERVATION"
    )
    if not isinstance(observation, InventoryObservation):
        raise ValueError(
            "E_INVENTORY_OBSERVATION: serialized inventory is not trusted"
        )
    if observation._consumed:
        raise ValueError("E_INVENTORY_REPLAY: inventory observation was consumed")
    if (
        type(observation) is not InventoryObservation
        or observation.repository_identity != str(repository)
        or observation.worktree_identity != str(worktree)
        or observation.registry_digest != expected_registry_digest
        or observation.task_digest != expected_task_digest
        or observation.invocation_id != expected_invocation_id
        or float(clock()) > observation.freshness_deadline
        or observation.snapshot_digest
        != observation.snapshot.get("snapshot_digest")
    ):
        raise ValueError(
            "E_INVENTORY_OBSERVATION: inventory binding is invalid or stale"
        )
    observation._consumed = True
    validated = object.__new__(ValidatedInventory)
    validated._snapshot = copy.deepcopy(observation.snapshot)
    validated.observation_id = observation.observation_id
    validated.invocation_id = observation.invocation_id
    validated.task_digest = observation.task_digest
    validated.repository_identity = observation.repository_identity
    validated.worktree_identity = observation.worktree_identity
    validated.registry_digest = observation.registry_digest
    validated.snapshot_digest = observation.snapshot_digest
    validated.observed_at_monotonic = observation.observed_at_monotonic
    validated.freshness_deadline = observation.freshness_deadline
    _register_runtime_host_object(validated, "validated_inventory")
    return validated


def observe_worktree_inventory(
    *,
    canonical_common_git_dir: Path | str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
    max_worktrees: int = 256,
    max_output_bytes: int = 1_048_576,
) -> WorktreeInventoryObservation:
    """Observe the registered worktrees directly from one canonical Git common dir."""

    common_dir = _regular_directory(Path(canonical_common_git_dir))
    if (
        not isinstance(invocation_id, str)
        or not invocation_id
        or not callable(clock)
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: invalid inventory observation binding"
        )
    git = _trusted_git_executable()
    if git is None:
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: trusted Git is unavailable"
        )
    completed = subprocess.run(
        [
            git,
            "--git-dir",
            str(common_dir),
            "worktree",
            "list",
            "--porcelain",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_sanitized_git_environment(),
    )
    if completed.returncode != 0:
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: Git worktree inventory failed"
        )
    entries = parse_worktree_porcelain(
        completed.stdout,
        max_worktrees=max_worktrees,
        max_output_bytes=max_output_bytes,
    )
    records = tuple(
        sorted(
            (
                WorktreeInventoryRecord(
                    worktree=str(Path(entry.worktree).resolve()),
                    git_dir=str(
                        _resolve_worktree_git_dir(
                            Path(entry.worktree).resolve(), common_dir
                        )
                    ),
                    head=entry.head,
                    branch=entry.branch,
                    detached=entry.detached,
                )
                for entry in entries
            ),
            key=lambda item: (item.worktree, item.git_dir),
        )
    )
    if len({item.git_dir for item in records}) != len(records):
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: duplicate worktree Git dir"
        )
    now = float(clock())
    observation = object.__new__(WorktreeInventoryObservation)
    observation.observation_id = f"worktree-inventory-{uuid4().hex}"
    observation.invocation_id = invocation_id
    observation.common_git_dir = str(common_dir)
    observation.records = records
    observation.identity_digest = _records_digest(records)
    observation.observed_at_monotonic = now
    observation.freshness_deadline = now + float(ttl_seconds)
    validate_worktree_inventory_observation(
        observation,
        expected_common_git_dir=common_dir,
        expected_invocation_id=invocation_id,
        clock=clock,
    )
    return observation


def validate_worktree_inventory_observation(
    observation: object,
    *,
    expected_common_git_dir: Path | str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedWorktreeInventoryObservation:
    """Validate exact bindings without accepting mappings or serialized lookalikes."""

    try:
        common_dir = _regular_directory(Path(expected_common_git_dir))
    except (OSError, ValueError) as error:
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: common Git dir is unavailable"
        ) from error
    if (
        type(observation) is not WorktreeInventoryObservation
        or observation.common_git_dir != str(common_dir)
        or observation.invocation_id != expected_invocation_id
        or float(clock()) > observation.freshness_deadline
        or observation.identity_digest != _records_digest(observation.records)
    ):
        raise ValueError(
            "E_LEASE_OBSERVATION_UNKNOWN: inventory binding is invalid or stale"
        )
    validated = object.__new__(ValidatedWorktreeInventoryObservation)
    validated._consumed = False
    validated._clock = clock
    validated._claim_lock = threading.Lock()
    validated.observation_id = observation.observation_id
    validated.invocation_id = observation.invocation_id
    validated.common_git_dir = observation.common_git_dir
    validated.records = observation.records
    validated.identity_digest = observation.identity_digest
    validated.freshness_deadline = observation.freshness_deadline
    return validated


def _inventory_is_current(
    inventory: ValidatedWorktreeInventoryObservation,
) -> bool:
    common_dir = Path(inventory.common_git_dir)
    try:
        refreshed = tuple(
            _live_worktree_record(item, common_dir)
            for item in inventory.records
        )
        registered = {
            str(path.resolve())
            for path in (common_dir / "worktrees").iterdir()
            if path.is_dir() and not path.is_symlink()
        } if (common_dir / "worktrees").is_dir() else set()
        observed_linked = {
            item.git_dir for item in inventory.records if item.git_dir != str(common_dir)
        }
    except (OSError, ValueError):
        return False
    return (
        registered == observed_linked
        and _records_digest(refreshed) == inventory.identity_digest
    )


def _consume_worktree_inventory(
    inventory: object, *, expected_common_git_dir: Path
) -> tuple[WorktreeInventoryRecord, ...]:
    claim_lock = getattr(inventory, "_claim_lock", None)
    if (
        type(inventory) is not ValidatedWorktreeInventoryObservation
        or type(claim_lock) is not _THREAD_LOCK_TYPE
    ):
        raise ValueError(
            "E_LEASE_OBSERVATION_STALE: worktree inventory changed before use"
        )
    with claim_lock:
        if (
            inventory._consumed
            or float(inventory._clock()) > inventory.freshness_deadline
            or inventory.common_git_dir
            != str(expected_common_git_dir.resolve())
            or not _inventory_is_current(inventory)
        ):
            raise ValueError(
                "E_LEASE_OBSERVATION_STALE: worktree inventory changed before use"
            )
        inventory._consumed = True
        return inventory.records


class _ValidatedVerificationTarget:
    __slots__ = (
        "_consumed",
        "target_digest",
        "inventory_observation_id",
        "common_git_dir",
        "repository_identity",
        "worktree_identity",
        "branch",
        "head",
        "policy_digest",
        "content_trust",
        "session_id",
        "invocation_id",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "_ValidatedVerificationTarget":
        raise TypeError("verification target is host-bound")


class ValidatedCandidateWorktreeObservation(_ValidatedVerificationTarget):
    pass


class ValidatedGoverningBaseWorktreeObservation(
    _ValidatedVerificationTarget
):
    pass


def _attest_verification_target(
    target_type: type[_ValidatedVerificationTarget],
    *,
    inventory: object,
    canonical_repository: Path | str,
    worktree: Path | str,
    expected_branch: str | None,
    expected_head: str,
    expected_policy_digest: str | None,
    content_trust: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> _ValidatedVerificationTarget:
    if (
        not isinstance(inventory, ValidatedWorktreeInventoryObservation)
        or inventory._consumed
        or not validate_task_id(session_id)
        or not invocation_id
        or _GIT_OBJECT_ID.fullmatch(expected_head) is None
        or content_trust
        not in {"project_owned", "governing_base", "external_untrusted"}
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_VERIFICATION_TARGET: fresh typed inventory is required"
        )
    repository = _canonical_directory(
        canonical_repository, code="E_VERIFICATION_TARGET"
    )
    target = _canonical_directory(worktree, code="E_VERIFICATION_TARGET")
    record = next(
        (item for item in inventory.records if item.worktree == str(target)),
        None,
    )
    if record is None:
        raise ValueError(
            "E_VERIFICATION_TARGET: target is not in observed inventory"
        )
    observed_root = Path(
        _git_text(target, ["rev-parse", "--show-toplevel"])
    ).resolve()
    observed_common = Path(
        _git_text(
            target,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        )
    ).resolve()
    branch = _git_text(target, ["branch", "--show-current"]) or None
    head = _git_text(target, ["rev-parse", "HEAD"])
    _assert_no_external_git_filters(target)
    status = subprocess.run(
        trusted_git_argv(
            target,
            (
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
            ),
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=trusted_git_environment(),
        timeout=10,
    )
    policy_digest: str | None = None
    if expected_policy_digest is not None:
        policy_path = target / ".codex" / "project-policy.toml"
        if policy_path.is_symlink() or not policy_path.is_file():
            raise ValueError(
                "E_VERIFICATION_TARGET: candidate policy is unavailable"
            )
        policy_digest = f"sha256:{sha256(policy_path.read_bytes()).hexdigest()}"
    if (
        observed_root != repository
        or observed_common != Path(inventory.common_git_dir)
        or head != expected_head
        or record.head != expected_head
        or record.branch != expected_branch
        or branch != expected_branch
        or status.returncode != 0
        or status.stdout
        or policy_digest != expected_policy_digest
    ):
        raise ValueError(
            "E_VERIFICATION_TARGET: target binding or cleanliness drifted"
        )
    now = float(clock())
    inventory._consumed = True
    result = object.__new__(target_type)
    result._consumed = False
    result.inventory_observation_id = inventory.observation_id
    result.common_git_dir = inventory.common_git_dir
    result.repository_identity = str(repository)
    result.worktree_identity = str(target)
    result.branch = branch
    result.head = head
    result.policy_digest = policy_digest
    result.content_trust = content_trust
    result.session_id = session_id
    result.invocation_id = invocation_id
    result.freshness_deadline = now + float(ttl_seconds)
    result.target_digest = contract_digest(
        {
            "kind": target_type.__name__,
            "inventory_observation_id": result.inventory_observation_id,
            "common_git_dir": result.common_git_dir,
            "repository_identity": result.repository_identity,
            "worktree_identity": result.worktree_identity,
            "branch": result.branch,
            "head": result.head,
            "policy_digest": result.policy_digest,
            "content_trust": result.content_trust,
            "session_id": result.session_id,
            "invocation_id": result.invocation_id,
        }
    )
    kind = (
        "candidate_verification_target"
        if target_type is ValidatedCandidateWorktreeObservation
        else "governing_base_verification_target"
    )
    _register_runtime_host_object(result, kind)
    return result


def attest_candidate_verification_target(
    *,
    inventory: object,
    canonical_repository: Path | str,
    candidate_worktree: Path | str,
    expected_branch: str,
    expected_head: str,
    expected_candidate_policy_digest: str,
    content_trust: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> ValidatedCandidateWorktreeObservation:
    if content_trust not in {"project_owned", "external_untrusted"}:
        raise ValueError(
            "E_VERIFICATION_TARGET: candidate content trust is invalid"
        )
    result = _attest_verification_target(
        ValidatedCandidateWorktreeObservation,
        inventory=inventory,
        canonical_repository=canonical_repository,
        worktree=candidate_worktree,
        expected_branch=expected_branch,
        expected_head=expected_head,
        expected_policy_digest=expected_candidate_policy_digest,
        content_trust=content_trust,
        session_id=session_id,
        invocation_id=invocation_id,
        clock=clock,
        ttl_seconds=ttl_seconds,
    )
    assert isinstance(result, ValidatedCandidateWorktreeObservation)
    return result


def attest_governing_base_verification_target(
    *,
    inventory: object,
    canonical_repository: Path | str,
    verifier_worktree: Path | str,
    expected_governing_base_commit: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> ValidatedGoverningBaseWorktreeObservation:
    result = _attest_verification_target(
        ValidatedGoverningBaseWorktreeObservation,
        inventory=inventory,
        canonical_repository=canonical_repository,
        worktree=verifier_worktree,
        expected_branch=None,
        expected_head=expected_governing_base_commit,
        expected_policy_digest=None,
        content_trust="governing_base",
        session_id=session_id,
        invocation_id=invocation_id,
        clock=clock,
        ttl_seconds=ttl_seconds,
    )
    assert isinstance(result, ValidatedGoverningBaseWorktreeObservation)
    return result


class GoverningRuntimeObservation:
    __slots__ = (
        "_consumed",
        "runtime_digest",
        "lock_digest",
        "policy_digest",
        "attestor_worktree",
        "target_worktree",
        "governing_base_commit",
        "runtime_layout",
        "session_id",
        "invocation_id",
        "freshness_deadline",
        "observation_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "GoverningRuntimeObservation":
        raise TypeError("governing runtime is host-bound")


_GOVERNING_RUNTIME_BINDING_FIELDS = (
    "runtime_digest",
    "lock_digest",
    "policy_digest",
    "attestor_worktree",
    "target_worktree",
    "governing_base_commit",
    "runtime_layout",
    "session_id",
    "invocation_id",
)
_ISSUED_GOVERNING_RUNTIMES: dict[
    int, tuple[GoverningRuntimeObservation, tuple[object, ...]]
] = {}


def _governing_runtime_binding(
    observation: GoverningRuntimeObservation,
) -> tuple[object, ...]:
    return tuple(
        getattr(observation, name)
        for name in _GOVERNING_RUNTIME_BINDING_FIELDS
    ) + (
        observation.freshness_deadline,
        observation.observation_digest,
    )


def _register_governing_runtime_observation(
    observation: GoverningRuntimeObservation,
) -> None:
    _register_runtime_host_object(
        observation, "governing_runtime_observation"
    )
    _ISSUED_GOVERNING_RUNTIMES[id(observation)] = (
        observation,
        _governing_runtime_binding(observation),
    )


def _governing_runtime_observation_is_live(observation: object) -> bool:
    if type(observation) is not GoverningRuntimeObservation:
        return False
    issued = _ISSUED_GOVERNING_RUNTIMES.get(id(observation))
    expected_digest = contract_digest(
        {
            name: getattr(observation, name)
            for name in _GOVERNING_RUNTIME_BINDING_FIELDS
        }
    )
    return (
        issued is not None
        and issued[0] is observation
        and issued[1] == _governing_runtime_binding(observation)
        and observation.observation_digest == expected_digest
        and _runtime_host_object_is_live(
            observation, "governing_runtime_observation"
        )
    )


def _consume_governing_runtime_observation(observation: object) -> bool:
    if not _governing_runtime_observation_is_live(observation):
        return False
    _ISSUED_GOVERNING_RUNTIMES.pop(id(observation), None)
    return _consume_runtime_host_object(
        observation, "governing_runtime_observation"
    )


def attest_verification_governing_runtime(
    *,
    attestor_worktree: Path | str,
    governing_base_commit: str,
    target_worktree: Path | str,
    expected_runtime_layout: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> GoverningRuntimeObservation:
    attestor = _canonical_directory(
        attestor_worktree, code="E_GOVERNING_RUNTIME"
    )
    target = _canonical_directory(
        target_worktree, code="E_GOVERNING_RUNTIME"
    )
    head = _git_text(attestor, ["rev-parse", "HEAD"])
    status = _git_text(
        attestor, ["status", "--porcelain=v2", "--untracked-files=all"]
    )
    lock_path = attestor / ".codex" / "control-plane.lock"
    policy_path = attestor / ".codex" / "project-policy.toml"
    if (
        _GIT_OBJECT_ID.fullmatch(governing_base_commit) is None
        or
        head != governing_base_commit
        or status
        or expected_runtime_layout not in {"source", "isolated"}
        or not validate_task_id(session_id)
        or not invocation_id
        or not 0 < float(ttl_seconds) <= 300
        or lock_path.is_symlink()
        or policy_path.is_symlink()
        or not lock_path.is_file()
        or not policy_path.is_file()
    ):
        raise ValueError(
            "E_GOVERNING_RUNTIME: attestor binding or cleanliness is invalid"
        )
    lock_bytes = _governing_regular_blob(
        attestor,
        governing_base_commit,
        ".codex/control-plane.lock",
        max_output_bytes=131_072,
    )
    policy_bytes = _governing_regular_blob(
        attestor,
        governing_base_commit,
        ".codex/project-policy.toml",
        max_output_bytes=131_072,
    )
    if (
        lock_path.read_bytes() != lock_bytes
        or policy_path.read_bytes() != policy_bytes
    ):
        raise ValueError(
            "E_GOVERNING_RUNTIME: governing filesystem bytes drifted"
        )
    try:
        lock = tomllib.loads(lock_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(
            "E_GOVERNING_RUNTIME: immutable lock is invalid"
        ) from error
    package = (
        "control_plane"
        if expected_runtime_layout == "source"
        else "codex_control_plane_runtime_v2"
    )
    runtime = (
        attestor / "control_plane"
        if expected_runtime_layout == "source"
        else attestor / ".codex" / "runtime" / package
    )
    if (
        lock.get("runtime_layout") != expected_runtime_layout
        or lock.get("runtime_package") != package
        or runtime.is_symlink()
        or not runtime.is_dir()
    ):
        raise ValueError("E_GOVERNING_RUNTIME: locked runtime layout drifted")
    runtime_relative = (
        "control_plane"
        if expected_runtime_layout == "source"
        else f".codex/runtime/{package}"
    )
    tree_entries = _governing_tree_entries(
        attestor, f"{governing_base_commit}:{runtime_relative}"
    )
    committed_modules = tuple(
        sorted(
            name
            for mode, object_type, name in tree_entries
            if object_type == "blob"
            and mode in {"100644", "100755"}
            and name.endswith(".py")
            and "/" not in name
        )
    )
    hasher = sha256()
    modules = sorted(runtime.glob("*.py"))
    if tuple(path.name for path in modules) != committed_modules:
        raise ValueError(
            "E_GOVERNING_RUNTIME: runtime module inventory drifted"
        )
    for path in modules:
        if path.is_symlink() or not path.is_file():
            raise ValueError("E_GOVERNING_RUNTIME: runtime module is invalid")
        committed_bytes = _governing_regular_blob(
            attestor,
            governing_base_commit,
            f"{runtime_relative}/{path.name}",
            max_output_bytes=1_048_576,
        )
        if path.read_bytes() != committed_bytes:
            raise ValueError(
                "E_GOVERNING_RUNTIME: governing runtime bytes drifted"
            )
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(committed_bytes)
        hasher.update(b"\0")
    runtime_digest = f"sha256:{hasher.hexdigest()}"
    if not modules or lock.get("digests", {}).get("runtime") != runtime_digest:
        raise ValueError("E_GOVERNING_RUNTIME: runtime digest drifted")
    now = float(clock())
    observation = object.__new__(GoverningRuntimeObservation)
    observation._consumed = False
    values = {
        "runtime_digest": runtime_digest,
        "lock_digest": f"sha256:{sha256(lock_bytes).hexdigest()}",
        "policy_digest": f"sha256:{sha256(policy_bytes).hexdigest()}",
        "attestor_worktree": str(attestor),
        "target_worktree": str(target),
        "governing_base_commit": governing_base_commit,
        "runtime_layout": expected_runtime_layout,
        "session_id": session_id,
        "invocation_id": invocation_id,
        "freshness_deadline": now + float(ttl_seconds),
    }
    for name, value in values.items():
        setattr(observation, name, value)
    observation.observation_digest = contract_digest(
        {
            name: getattr(observation, name)
            for name in (
                "runtime_digest",
                "lock_digest",
                "policy_digest",
                "attestor_worktree",
                "target_worktree",
                "governing_base_commit",
                "runtime_layout",
                "session_id",
                "invocation_id",
            )
        }
    )
    _register_governing_runtime_observation(observation)
    return observation


class RemoteEffectContext:
    __slots__ = (
        "_consumed",
        "task_digest",
        "task_id",
        "repository_identity",
        "worktree_identity",
        "remote_repository",
        "remote_name",
        "branch",
        "head",
        "session_id",
        "invocation_id",
        "effect",
        "expected_pr_number",
        "expected_base_sha",
        "expected_checks_digest",
        "context_digest",
    )

    def __new__(cls, *_: object, **__: object) -> "RemoteEffectContext":
        raise TypeError("RemoteEffectContext is host-bound")


class ValidatedRemoteEffectContext:
    __slots__ = (
        "_consumed",
        "task_digest",
        "task_id",
        "repository_identity",
        "worktree_identity",
        "remote_repository",
        "remote_name",
        "branch",
        "head",
        "session_id",
        "invocation_id",
        "effect",
        "expected_pr_number",
        "expected_base_sha",
        "expected_checks_digest",
        "context_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedRemoteEffectContext":
        raise TypeError("ValidatedRemoteEffectContext is host-bound")


def _git_dir_for_worktree(worktree: Path) -> Path:
    raw = _git_text(
        worktree, ["rev-parse", "--path-format=absolute", "--git-dir"]
    )
    return Path(raw).resolve()


def create_remote_effect_context(
    *,
    task: Mapping[str, object],
    expected_task_digest: str,
    local_git: object,
    session_id: str,
    invocation_id: str,
    effect: str,
    expected_pr_number: int | None,
    expected_base_sha: str | None,
    expected_checks_digest: str | None,
    governing_policy: object,
    host_capability: object,
) -> RemoteEffectContext:
    from control_plane.policy import (
        GoverningPolicy,
        _governing_policy_is_live,
    )

    if (
        not isinstance(task, Mapping)
        or validate_task_envelope(task)
        or contract_digest(task) != expected_task_digest
        or type(local_git) is not LocalGitObservation
        or local_git.provider != "git"
        or type(host_capability) is not HostAdapterCapability
        or type(governing_policy) is not GoverningPolicy
        or not _governing_policy_is_live(
            governing_policy, clock=host_capability._clock
        )
        or not _runtime_host_object_is_live(
            host_capability, "host_capability"
        )
        or host_capability._consumed
        or float(host_capability._clock())
        > host_capability.freshness_deadline
        or host_capability.session_id != session_id
        or host_capability.invocation_id != invocation_id
        or local_git.task_digest != expected_task_digest
        or local_git.session_id != session_id
        or local_git.invocation_id != invocation_id
        or local_git.target_state != "committed"
        or effect not in {"remote_write", "pull_request", "integration"}
    ):
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: trusted clean child evidence is required"
        )
    outcome_order = {
        "answer": 0,
        "local_change": 1,
        "commit": 2,
        "pull_request": 3,
        "integration": 4,
        "release": 5,
    }
    required = {"remote_write": 3, "pull_request": 3, "integration": 4}
    if outcome_order.get(str(task.get("requested_outcome")), -1) < required[effect]:
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: task outcome does not authorize effect"
        )
    worktree = _canonical_directory(
        local_git.worktree_identity, code="E_REMOTE_EFFECT_CONTEXT"
    )
    repository = _canonical_directory(
        local_git.repository_identity, code="E_REMOTE_EFFECT_CONTEXT"
    )
    head = str(local_git.evidence.get("commit", ""))
    if (
        _GIT_OBJECT_ID.fullmatch(head) is None
        or local_git.repository_identity != str(repository)
        or local_git.worktree_identity != str(worktree)
        or _git_text(worktree, ["rev-parse", "HEAD"]) != head
        or _git_text(worktree, ["branch", "--show-current"])
        != local_git.branch
        or _git_text(
            worktree,
            ["status", "--porcelain=v2", "--untracked-files=all"],
        )
    ):
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: child worktree is not clean at commit"
        )
    state_dir = _git_dir_for_worktree(worktree)
    task_id = str(task["task_id"])
    state_path = (
        state_dir / "codex-control-plane" / "tasks" / f"{task_id}.json"
    )
    lease_path = (
        state_dir / "codex-control-plane" / "leases" / f"{task_id}.json"
    )
    try:
        import json

        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: closed child state is unavailable"
        ) from error
    task_effects = {
        str(item.get("name"))
        for item in task.get("effects", ())
        if isinstance(item, Mapping)
    }
    if (
        state.get("state") != "closed"
        or state.get("task_id") != task_id
        or state.get("task_digest") != expected_task_digest
        or state.get("branch") != local_git.branch
        or state.get("outcome") != task.get("requested_outcome")
        or effect not in task_effects
        or lease_path.exists()
    ):
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: writer child is not closed and released"
        )
    if effect == "integration" and task.get("requested_outcome") != "integration":
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: integration requires a separate outcome"
        )
    policy_git = governing_policy.policy.get("git", {})
    remote_name = policy_git.get("remote")
    if not isinstance(remote_name, str) or not remote_name:
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: governing remote is unavailable"
        )
    try:
        live_remote_repository = _canonical_github_repository_from_url(
            _git_text(
                worktree,
                ["remote", "get-url", "--push", remote_name],
            ),
            code="E_REMOTE_EFFECT_CONTEXT",
        )
    except ValueError as error:
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: governing remote is unavailable"
        ) from error
    if live_remote_repository != governing_policy.remote_repository:
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: governing remote identity drifted"
        )
    if not _consume_runtime_host_object(
        host_capability, "host_capability"
    ):
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: host capability is not issued"
        )
    host_capability._consumed = True
    context = object.__new__(RemoteEffectContext)
    context._consumed = False
    values = {
        "task_digest": expected_task_digest,
        "task_id": task_id,
        "repository_identity": str(repository),
        "worktree_identity": str(worktree),
        "remote_repository": governing_policy.remote_repository,
        "remote_name": remote_name,
        "branch": local_git.branch,
        "head": head,
        "session_id": session_id,
        "invocation_id": invocation_id,
        "effect": effect,
        "expected_pr_number": expected_pr_number,
        "expected_base_sha": expected_base_sha,
        "expected_checks_digest": expected_checks_digest,
    }
    for name, value in values.items():
        setattr(context, name, value)
    context.context_digest = contract_digest(
        {
            name: getattr(context, name)
            for name in (
                "task_digest",
                "task_id",
                "repository_identity",
                "worktree_identity",
                "remote_repository",
                "remote_name",
                "branch",
                "head",
                "session_id",
                "invocation_id",
                "effect",
                "expected_pr_number",
                "expected_base_sha",
                "expected_checks_digest",
            )
        }
    )
    _register_runtime_host_object(context, "remote_effect_context")
    return context


def validate_remote_effect_context(
    context: object,
    *,
    expected_task_digest: str,
    expected_repo: Path | str,
    expected_worktree: Path | str,
    expected_branch: str,
    expected_head: str,
    expected_session: str,
    expected_invocation_id: str,
    expected_effect: str,
    expected_pr_number: int | None,
    expected_base_sha: str | None,
    expected_checks_digest: str | None,
) -> ValidatedRemoteEffectContext:
    repository = _canonical_directory(
        expected_repo, code="E_REMOTE_EFFECT_CONTEXT"
    )
    worktree = _canonical_directory(
        expected_worktree, code="E_REMOTE_EFFECT_CONTEXT"
    )
    context_core = {
        name: getattr(context, name, None)
        for name in (
            "task_digest",
            "task_id",
            "repository_identity",
            "worktree_identity",
            "remote_repository",
            "remote_name",
            "branch",
            "head",
            "session_id",
            "invocation_id",
            "effect",
            "expected_pr_number",
            "expected_base_sha",
            "expected_checks_digest",
        )
    }
    if (
        type(context) is not RemoteEffectContext
        or not _runtime_host_object_is_live(
            context, "remote_effect_context"
        )
        or context._consumed
        or context.context_digest != contract_digest(context_core)
        or context.task_digest != expected_task_digest
        or context.repository_identity != str(repository)
        or context.worktree_identity != str(worktree)
        or context.branch != expected_branch
        or context.head != expected_head
        or context.session_id != expected_session
        or context.invocation_id != expected_invocation_id
        or context.effect != expected_effect
        or context.expected_pr_number != expected_pr_number
        or context.expected_base_sha != expected_base_sha
        or context.expected_checks_digest != expected_checks_digest
        or _git_text(worktree, ["rev-parse", "HEAD"]) != expected_head
        or _git_text(worktree, ["branch", "--show-current"])
        != expected_branch
        or _git_text(
            worktree,
            ["status", "--porcelain=v2", "--untracked-files=all"],
        )
    ):
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: remote effect binding drifted"
        )
    state_dir = _git_dir_for_worktree(worktree)
    state_path = (
        state_dir
        / "codex-control-plane"
        / "tasks"
        / f"{context.task_id}.json"
    )
    lease_path = (
        state_dir
        / "codex-control-plane"
        / "leases"
        / f"{context.task_id}.json"
    )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: child state is unavailable"
        ) from error
    if (
        state.get("state") != "closed"
        or state.get("task_digest") != expected_task_digest
        or state.get("branch") != expected_branch
        or lease_path.exists()
    ):
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: child closure drifted"
        )
    if not _consume_runtime_host_object(
        context, "remote_effect_context"
    ):
        raise ValueError(
            "E_REMOTE_EFFECT_CONTEXT: context is not host-issued"
        )
    context._consumed = True
    validated = object.__new__(ValidatedRemoteEffectContext)
    validated._consumed = False
    for name in ValidatedRemoteEffectContext.__slots__:
        if name != "_consumed":
            setattr(validated, name, getattr(context, name))
    _register_runtime_host_object(
        validated, "validated_remote_effect_context"
    )
    return validated


def _assert_remote_effect_context_live(
    context: ValidatedRemoteEffectContext, *, code: str
) -> None:
    worktree = _canonical_directory(
        context.worktree_identity, code=code
    )
    if (
        not (
            _runtime_host_object_is_live(
                context, "validated_remote_effect_context"
            )
            or _runtime_host_object_is_live(
                context, "pr_request_context"
            )
            or _runtime_host_object_is_live(
                context, "claimed_pr_request_context"
            )
            or _runtime_host_object_is_live(
                context, "claimed_feature_push_context"
            )
            or _runtime_host_object_is_live(
                context, "feature_push_unknown_context"
            )
        )
        or _git_text(worktree, ["rev-parse", "HEAD"]) != context.head
        or _git_text(worktree, ["branch", "--show-current"])
        != context.branch
        or _git_text(
            worktree,
            ["status", "--porcelain=v2", "--untracked-files=all"],
        )
    ):
        raise ValueError(f"{code}: validated remote context drifted")
    try:
        live_remote_repository = _canonical_github_repository_from_url(
            _git_text(
                worktree,
                ["remote", "get-url", "--push", context.remote_name],
            ),
            code=code,
        )
    except (AttributeError, ValueError) as error:
        raise ValueError(
            f"{code}: validated remote context drifted"
        ) from error
    if live_remote_repository != context.remote_repository:
        raise ValueError(f"{code}: validated remote context drifted")
    state_dir = _git_dir_for_worktree(worktree)
    state_path = (
        state_dir
        / "codex-control-plane"
        / "tasks"
        / f"{context.task_id}.json"
    )
    lease_path = (
        state_dir
        / "codex-control-plane"
        / "leases"
        / f"{context.task_id}.json"
    )
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            f"{code}: closed child state is unavailable"
        ) from error
    if (
        state_path.is_symlink()
        or lease_path.exists()
        or state.get("state") != "closed"
        or state.get("task_id") != context.task_id
        or state.get("task_digest") != context.task_digest
        or state.get("branch") != context.branch
    ):
        raise ValueError(f"{code}: closed child binding drifted")


class LocalGitIndexObservation:
    __slots__ = (
        "_consumed",
        "task_digest",
        "worktree_identity",
        "branch",
        "head",
        "index_tree",
        "paths",
        "session_id",
        "invocation_id",
        "observation_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "LocalGitIndexObservation":
        raise TypeError("LocalGitIndexObservation is host-bound")


def _assert_exact_stage_paths(
    worktree: Path,
    requested: tuple[str, ...],
    normalized: tuple[str, ...],
) -> None:
    if len(requested) != len(normalized):
        raise ValueError("E_GIT_EFFECT: stage path inventory is invalid")
    for raw, relative in zip(requested, normalized):
        target = worktree / relative
        if (
            raw != relative
            or relative == "."
            or any(character in raw for character in "*?[]")
            or target.is_symlink()
            or target.is_dir()
        ):
            raise ValueError(
                "E_GIT_EFFECT: stage requires exact literal file paths"
            )
        resolved = target.resolve(strict=False)
        if worktree not in resolved.parents:
            raise ValueError(
                "E_GIT_EFFECT: stage path escaped the worktree"
            )
        parent = target.parent
        while parent != worktree:
            if parent.is_symlink():
                raise ValueError(
                    "E_GIT_EFFECT: stage path traverses a symlink"
                )
            parent = parent.parent
        if target.exists() and not target.is_file():
            raise ValueError(
                "E_GIT_EFFECT: stage target is not a regular file"
            )
        if not target.exists():
            tracked = subprocess.run(
                trusted_git_argv(
                    worktree,
                    ("ls-files", "--error-unmatch", "--", relative),
                ),
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=trusted_git_environment(),
                timeout=10,
            )
            if tracked.returncode != 0:
                raise ValueError(
                    "E_GIT_EFFECT: missing stage target is not tracked"
                )


def _assert_no_unsafe_transport_config(worktree: Path) -> None:
    keys: list[str] = []
    scopes = ["--local"]
    for scope in scopes:
        completed = subprocess.run(
            trusted_git_argv(
                worktree,
                ("config", scope, "--name-only", "--null", "--list"),
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=trusted_git_environment(),
            timeout=10,
        )
        payload = completed.stdout
        if (
            completed.returncode != 0
            or not isinstance(payload, bytes)
            or len(payload) > 65_536
        ):
            raise ValueError(
                "E_REMOTE_EFFECT: repository transport config is unobservable"
            )
        try:
            scope_keys = [
                key
                for key in payload.decode("utf-8").lower().split("\0")
                if key
            ]
        except UnicodeDecodeError as error:
            raise ValueError(
                "E_REMOTE_EFFECT: repository transport config is invalid"
            ) from error
        keys.extend(scope_keys)
        if (
            scope == "--local"
            and "extensions.worktreeconfig" in scope_keys
        ):
            scopes.append("--worktree")
    if any(
        key.startswith(("http.", "credential.", "url.", "protocol."))
        or key in {"core.gitproxy", "core.sshcommand"}
        or (
            key.startswith("remote.")
            and key.endswith((".proxy", ".proxyauthmethod", ".receivepack"))
        )
        for key in keys
    ):
        raise ValueError(
            "E_REMOTE_EFFECT: unsafe repository transport config is present"
        )


def _governing_policy_contract_digest(
    governing_runtime: GoverningRuntimeObservation,
) -> str:
    policy_bytes = _governing_regular_blob(
        Path(governing_runtime.attestor_worktree),
        governing_runtime.governing_base_commit,
        ".codex/project-policy.toml",
        max_output_bytes=131_072,
    )
    if (
        f"sha256:{sha256(policy_bytes).hexdigest()}"
        != governing_runtime.policy_digest
    ):
        raise ValueError("E_GIT_EFFECT: governing policy blob drifted")
    try:
        policy = tomllib.loads(policy_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError(
            "E_GIT_EFFECT: governing policy blob is invalid"
        ) from error
    return contract_digest(policy)


def _validate_governing_git_effect(
    *,
    governing_runtime: object,
    task_context: object,
    inventory: object,
    lease: object,
    authorization: object,
    expected_head: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    expected_marker_phase: str,
    expected_inventory_consumed: bool = False,
    expected_authorization_consumed: bool = False,
) -> tuple[Path, Mapping[str, object], Mapping[str, object]]:
    now = float(clock())
    if (
        type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or now > governing_runtime.freshness_deadline
        or not isinstance(task_context, Mapping)
        or not isinstance(
            inventory, ValidatedWorktreeInventoryObservation
        )
        or inventory._consumed is not expected_inventory_consumed
        or (
            expected_inventory_consumed
            and now > inventory.freshness_deadline
        )
        or not _inventory_is_current(inventory)
        or not isinstance(lease, Mapping)
        or not isinstance(authorization, TrustedAuthorization)
        or (
            authorization._consumed
            is not expected_authorization_consumed
        )
        or (
            expected_authorization_consumed
            and now > authorization.freshness_deadline
        )
        or governing_runtime.session_id != session_id
        or governing_runtime.invocation_id != invocation_id
        or lease.get("session_id") != session_id
        or lease.get("task_id") != task_context.get("task_id")
        or lease.get("lease_digest") != task_context.get("lease_digest")
    ):
        raise ValueError(
            "E_GIT_EFFECT: governing runtime, task, lease, and grants are required"
        )
    worktree = _canonical_directory(
        str(lease.get("worktree", "")), code="E_GIT_EFFECT"
    )
    owner = next(
        (
            item
            for item in inventory.records
            if item.worktree == str(worktree)
            and item.branch == lease.get("branch")
        ),
        None,
    )
    if (
        governing_runtime.target_worktree != str(worktree)
        or _git_text(worktree, ["rev-parse", "HEAD"]) != expected_head
        or _git_text(worktree, ["branch", "--show-current"])
        != lease.get("branch")
        or owner is None
    ):
        raise ValueError("E_GIT_EFFECT: worktree binding drifted")
    task_id = str(task_context.get("task_id", ""))
    task_path = (
        Path(owner.git_dir)
        / "codex-control-plane"
        / "tasks"
        / f"{task_id}.json"
    )
    lease_path = (
        Path(owner.git_dir)
        / "codex-control-plane"
        / "delivery-leases"
        / f"{task_id}.json"
    )
    try:
        live_task = json.loads(task_path.read_text(encoding="utf-8"))
        live_lease = json.loads(lease_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "E_GIT_EFFECT: live task or writer lease is unavailable"
        ) from error
    lease_semantic = {
        key: value
        for key, value in live_lease.items()
        if key != "lease_digest"
    }
    live_policy_digest = live_lease.get("policy_digest")
    governing_policy_contract_digest = (
        governing_runtime.policy_digest
        if live_policy_digest == governing_runtime.policy_digest
        else _governing_policy_contract_digest(governing_runtime)
    )
    marker = live_task.get("finalizing_delivery_commit") if isinstance(live_task, Mapping) else None
    review_binding = (
        live_task.get("delivery_review_binding")
        if isinstance(live_task, Mapping)
        else None
    )
    marker_required = {
        "schema_version",
        "task_id",
        "generation",
        "lease_digest",
        "snapshot_digest",
        "allowlist",
        "expected_index_tree",
        "parent_head",
        "base_head",
        "expected_tree",
        "message_digest",
        "phase",
        "marker_digest",
    }
    if (
        task_path.is_symlink()
        or lease_path.is_symlink()
        or not isinstance(live_task, Mapping)
        or not isinstance(live_lease, Mapping)
        or live_task.get("task_id") != task_id
        or live_task.get("task_digest") != task_context.get("task_digest")
        or live_task.get("branch") != lease.get("branch")
        or live_task.get("state") != "review_ready"
        or not isinstance(marker, Mapping)
        or set(marker) != marker_required
        or marker.get("schema_version") != 1
        or marker.get("task_id") != task_id
        or marker.get("generation") != live_task.get("generation")
        or marker.get("generation") != live_lease.get("generation")
        or marker.get("lease_digest") != lease.get("lease_digest")
        or marker.get("phase") != expected_marker_phase
        or marker.get("parent_head") != expected_head
        or marker.get("parent_head") != live_lease.get("review_head")
        or marker.get("base_head") != live_lease.get("base_head")
        or marker.get("expected_index_tree") != marker.get("expected_tree")
        or _GIT_OBJECT_ID.fullmatch(
            str(marker.get("expected_index_tree", ""))
        ) is None
        or not isinstance(marker.get("message_digest"), str)
        or SHA256_DIGEST.fullmatch(str(marker.get("message_digest"))) is None
        or not isinstance(marker.get("allowlist"), list)
        or tuple(marker.get("allowlist", ()))
        != tuple(live_lease.get("paths", ()))
        or not isinstance(review_binding, Mapping)
        or review_binding.get("authorizes") is not False
        or review_binding.get("binding_digest")
        != contract_digest(
            {
                key: value
                for key, value in review_binding.items()
                if key != "binding_digest"
            }
        )
        or marker.get("snapshot_digest")
        != review_binding.get("binding_digest")
        or marker.get("parent_head") != review_binding.get("reviewed_head")
        or marker.get("allowlist") != review_binding.get("scope_paths")
        or marker.get("marker_digest")
        != contract_digest(
            {
                key: value
                for key, value in marker.items()
                if key != "marker_digest"
            }
        )
        or dict(live_lease) != dict(lease)
        or live_lease.get("lease_digest") != contract_digest(lease_semantic)
        or live_lease.get("lease_digest")
        != task_context.get("lease_digest")
        or live_policy_digest != governing_policy_contract_digest
    ):
        raise ValueError(
            "E_GIT_EFFECT: live task or writer lease binding drifted"
        )
    return worktree, lease, marker


def _governing_git_effect_lock_binding(
    *,
    worktree: Path,
    task_context: Mapping[str, object],
    inventory: ValidatedWorktreeInventoryObservation,
    lease: Mapping[str, object],
) -> tuple[Path, Path, str]:
    task_id = str(task_context.get("task_id", ""))
    owner = next(
        (
            item
            for item in inventory.records
            if item.worktree == str(worktree)
            and item.branch == lease.get("branch")
        ),
        None,
    )
    if owner is None or not validate_task_id(task_id):
        raise ValueError("E_GIT_EFFECT: worktree lock binding is invalid")
    return Path(inventory.common_git_dir), Path(owner.git_dir), task_id


def stage_allowlisted_paths(
    *,
    governing_runtime: object,
    task_context: object,
    inventory: object,
    lease: object,
    authorization: object,
    paths: tuple[str, ...],
    expected_head: str,
    session_id: str,
    invocation_id: str,
    tool_use_id: str,
    clock: Callable[[], float],
) -> LocalGitIndexObservation:
    worktree, lease_mapping, _marker = _validate_governing_git_effect(
        governing_runtime=governing_runtime,
        task_context=task_context,
        inventory=inventory,
        lease=lease,
        authorization=authorization,
        expected_head=expected_head,
        session_id=session_id,
        invocation_id=invocation_id,
        clock=clock,
        expected_marker_phase="prepared",
    )
    normalized = tuple(normalize_scope(item) for item in paths)
    owned = tuple(str(item) for item in lease_mapping.get("paths", ()))
    if (
        not paths
        or any(item is None for item in normalized)
        or any(
            not any(
                scope == "."
                or path == scope.removesuffix("/**")
                or path.startswith(scope.removesuffix("/**") + "/")
                for scope in owned
            )
            for path in normalized
        )
    ):
        raise ValueError("E_GIT_EFFECT: stage paths exceed the writer lease")
    exact_paths = tuple(str(item) for item in normalized)
    _assert_exact_stage_paths(worktree, paths, exact_paths)
    _assert_no_external_git_filters(
        worktree, exact_paths
    )
    subject_digest = contract_digest({"paths": normalized})
    consume_authorization(
        authorization,
        expected_task_digest=str(task_context["task_digest"]),
        expected_session_id=session_id,
        expected_repository_identity=governing_runtime.target_worktree,
        expected_worktree_identity=str(worktree),
        expected_branch=str(lease_mapping["branch"]),
        expected_head=expected_head,
        expected_subject_digest=subject_digest,
        expected_scope_paths=tuple(str(item) for item in normalized),
        expected_effect="local_write",
        expected_operation_nonce=tool_use_id,
        expected_invocation_id=invocation_id,
        clock=clock,
    )
    _consume_worktree_inventory(
        inventory,
        expected_common_git_dir=Path(inventory.common_git_dir),
    )
    common_dir, task_state_dir, task_id = (
        _governing_git_effect_lock_binding(
            worktree=worktree,
            task_context=task_context,
            inventory=inventory,
            lease=lease_mapping,
        )
    )
    from control_plane.lifecycle import _common_lease_lock, _task_guard

    with _common_lease_lock(common_dir):
        with _task_guard(task_state_dir, task_id):
            worktree, lease_mapping, _marker = (
                _validate_governing_git_effect(
                    governing_runtime=governing_runtime,
                    task_context=task_context,
                    inventory=inventory,
                    lease=lease,
                    authorization=authorization,
                    expected_head=expected_head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    clock=clock,
                    expected_marker_phase="prepared",
                    expected_inventory_consumed=True,
                    expected_authorization_consumed=True,
                )
            )
            _assert_exact_stage_paths(worktree, paths, exact_paths)
            _assert_no_external_git_filters(worktree, exact_paths)
            worktree, lease_mapping, _marker = (
                _validate_governing_git_effect(
                    governing_runtime=governing_runtime,
                    task_context=task_context,
                    inventory=inventory,
                    lease=lease,
                    authorization=authorization,
                    expected_head=expected_head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    clock=clock,
                    expected_marker_phase="prepared",
                    expected_inventory_consumed=True,
                    expected_authorization_consumed=True,
                )
            )
            completed = subprocess.run(
                _closed_git_argv(
                    worktree, ["add", "--", *normalized]
                ),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_sanitized_git_environment(),
            )
            staged = subprocess.run(
                _closed_git_argv(
                    worktree,
                    [
                        "diff",
                        "--cached",
                        "--name-only",
                        "-z",
                    ],
                ),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_sanitized_git_environment(),
            )
            observed_paths = tuple(
                item.decode("utf-8")
                for item in staged.stdout.split(b"\0")
                if item
            )
            if completed.returncode != 0 or staged.returncode != 0 or set(
                observed_paths
            ) != set(normalized):
                raise ValueError(
                    "E_GIT_EFFECT: staged index is not the allowlist"
                )
            index_tree = _git_text(worktree, ["write-tree"])
    observation = object.__new__(LocalGitIndexObservation)
    observation._consumed = False
    observation.task_digest = str(task_context["task_digest"])
    observation.worktree_identity = str(worktree)
    observation.branch = str(lease_mapping["branch"])
    observation.head = expected_head
    observation.index_tree = index_tree
    observation.paths = tuple(str(item) for item in normalized)
    observation.session_id = session_id
    observation.invocation_id = invocation_id
    observation.observation_digest = contract_digest(
        {
            name: getattr(observation, name)
            for name in (
                "task_digest",
                "worktree_identity",
                "branch",
                "head",
                "index_tree",
                "paths",
                "session_id",
                "invocation_id",
            )
        }
    )
    return observation


def _validate_staged_commit_binding(
    *,
    worktree: Path,
    task_context: Mapping[str, object],
    lease: Mapping[str, object],
    marker: Mapping[str, object],
    index_observation: object,
    message: object,
    expected_prior_head: str,
    session_id: str,
    invocation_id: str,
) -> None:
    if (
        type(index_observation) is not LocalGitIndexObservation
        or index_observation._consumed
        or index_observation.task_digest
        != task_context.get("task_digest")
        or index_observation.worktree_identity != str(worktree)
        or index_observation.branch != lease.get("branch")
        or index_observation.head != expected_prior_head
        or index_observation.session_id != session_id
        or index_observation.invocation_id != invocation_id
        or tuple(index_observation.paths)
        != tuple(lease.get("paths", ()))
        or index_observation.observation_digest
        != contract_digest(
            {
                name: getattr(index_observation, name)
                for name in (
                    "task_digest",
                    "worktree_identity",
                    "branch",
                    "head",
                    "index_tree",
                    "paths",
                    "session_id",
                    "invocation_id",
                )
            }
        )
        or not isinstance(message, str)
        or not 1 <= len(message) <= 200
        or any(ord(character) < 32 for character in message)
        or marker.get("parent_head") != expected_prior_head
        or marker.get("message_digest")
        != f"sha256:{sha256(message.encode('utf-8')).hexdigest()}"
        or _git_text(worktree, ["branch", "--show-current"])
        != lease.get("branch")
        or _git_text(worktree, ["rev-parse", "HEAD"])
        != expected_prior_head
        or _git_text(worktree, ["write-tree"])
        != index_observation.index_tree
        or marker.get("expected_index_tree")
        != index_observation.index_tree
        or marker.get("expected_tree")
        != index_observation.index_tree
    ):
        raise ValueError(
            "E_GIT_EFFECT: staged commit binding is invalid"
        )


def commit_staged_change(
    *,
    governing_runtime: object,
    task_context: object,
    inventory: object,
    lease: object,
    index_observation: object,
    authorization: object,
    message: str,
    expected_prior_head: str,
    session_id: str,
    invocation_id: str,
    tool_use_id: str,
    clock: Callable[[], float],
) -> LocalGitObservation:
    worktree, lease_mapping, marker = _validate_governing_git_effect(
        governing_runtime=governing_runtime,
        task_context=task_context,
        inventory=inventory,
        lease=lease,
        authorization=authorization,
        expected_head=expected_prior_head,
        session_id=session_id,
        invocation_id=invocation_id,
        clock=clock,
        expected_marker_phase="index_observed",
    )
    _validate_staged_commit_binding(
        worktree=worktree,
        task_context=task_context,
        lease=lease_mapping,
        marker=marker,
        index_observation=index_observation,
        message=message,
        expected_prior_head=expected_prior_head,
        session_id=session_id,
        invocation_id=invocation_id,
    )
    subject_digest = contract_digest(
        {
            "index": index_observation.observation_digest,
            "message": message,
        }
    )
    consume_authorization(
        authorization,
        expected_task_digest=str(task_context["task_digest"]),
        expected_session_id=session_id,
        expected_repository_identity=governing_runtime.target_worktree,
        expected_worktree_identity=str(worktree),
        expected_branch=str(lease_mapping["branch"]),
        expected_head=expected_prior_head,
        expected_subject_digest=subject_digest,
        expected_scope_paths=tuple(index_observation.paths),
        expected_effect="commit",
        expected_operation_nonce=tool_use_id,
        expected_invocation_id=invocation_id,
        clock=clock,
    )
    _consume_worktree_inventory(
        inventory,
        expected_common_git_dir=Path(inventory.common_git_dir),
    )
    common_dir, task_state_dir, task_id = (
        _governing_git_effect_lock_binding(
            worktree=worktree,
            task_context=task_context,
            inventory=inventory,
            lease=lease_mapping,
        )
    )
    from control_plane.lifecycle import _common_lease_lock, _task_guard

    with _common_lease_lock(common_dir):
        with _task_guard(task_state_dir, task_id):
            worktree, lease_mapping, marker = (
                _validate_governing_git_effect(
                    governing_runtime=governing_runtime,
                    task_context=task_context,
                    inventory=inventory,
                    lease=lease,
                    authorization=authorization,
                    expected_head=expected_prior_head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    clock=clock,
                    expected_marker_phase="index_observed",
                    expected_inventory_consumed=True,
                    expected_authorization_consumed=True,
                )
            )
            _validate_staged_commit_binding(
                worktree=worktree,
                task_context=task_context,
                lease=lease_mapping,
                marker=marker,
                index_observation=index_observation,
                message=message,
                expected_prior_head=expected_prior_head,
                session_id=session_id,
                invocation_id=invocation_id,
            )
            worktree, lease_mapping, marker = (
                _validate_governing_git_effect(
                    governing_runtime=governing_runtime,
                    task_context=task_context,
                    inventory=inventory,
                    lease=lease,
                    authorization=authorization,
                    expected_head=expected_prior_head,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    clock=clock,
                    expected_marker_phase="index_observed",
                    expected_inventory_consumed=True,
                    expected_authorization_consumed=True,
                )
            )
            _validate_staged_commit_binding(
                worktree=worktree,
                task_context=task_context,
                lease=lease_mapping,
                marker=marker,
                index_observation=index_observation,
                message=message,
                expected_prior_head=expected_prior_head,
                session_id=session_id,
                invocation_id=invocation_id,
            )
            completed = subprocess.run(
                _closed_git_argv(
                    worktree, ["commit", "-m", message]
                ),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_sanitized_git_environment(),
            )
            head = _git_text(worktree, ["rev-parse", "HEAD"])
            if (
                completed.returncode != 0
                or head == expected_prior_head
                or _GIT_OBJECT_ID.fullmatch(head) is None
                or _git_text(
                    worktree, ["diff", "--cached", "--name-only"]
                )
            ):
                raise ValueError(
                    "E_GIT_EFFECT: commit was not observed exactly"
                )
            index_observation._consumed = True
    now = float(clock())
    observation = object.__new__(LocalGitObservation)
    observation.observation_id = f"local-git-{uuid4().hex}"
    observation.invocation_id = invocation_id
    observation.task_digest = str(task_context["task_digest"])
    observation.repository_identity = governing_runtime.target_worktree
    observation.worktree_identity = str(worktree)
    observation.branch = str(lease_mapping["branch"])
    observation.prior_head = expected_prior_head
    observation.target_state = "committed"
    observation.session_id = session_id
    observation.provider = "git"
    observation.subject_digest = subject_digest
    observation.evidence = {"commit": head}
    observation.observed_at_monotonic = now
    observation.freshness_deadline = now + 30
    _register_runtime_host_object(observation, "local_git_observation")
    return observation


@dataclass(frozen=True)
class _FeaturePushEffectBindings:
    repository_identity: str
    worktree_identity: str
    remote_repository: str
    remote_name: str
    push_url: str
    branch: str
    head: str
    task_digest: str
    session_id: str
    invocation_id: str
    context_digest: str


@dataclass
class _FeaturePushOperation:
    context: ValidatedRemoteEffectContext
    bindings: _FeaturePushEffectBindings
    state: str
    recovery_consumed: bool = False


def _claim_feature_push_context(
    context: ValidatedRemoteEffectContext,
    *,
    push_url: str,
) -> _FeaturePushEffectBindings:
    with _FEATURE_PUSH_CLAIM_LOCK:
        if (
            context._consumed
            or id(context) in _FEATURE_PUSH_OPERATIONS
            or not _runtime_host_object_is_live(
                context, "validated_remote_effect_context"
            )
        ):
            raise ValueError(
                "E_REMOTE_EFFECT: unclaimed push context is required"
            )
        bindings = _FeaturePushEffectBindings(
            repository_identity=context.repository_identity,
            worktree_identity=context.worktree_identity,
            remote_repository=context.remote_repository,
            remote_name=context.remote_name,
            push_url=push_url,
            branch=context.branch,
            head=context.head,
            task_digest=context.task_digest,
            session_id=context.session_id,
            invocation_id=context.invocation_id,
            context_digest=context.context_digest,
        )
        if not _consume_runtime_host_object(
            context, "validated_remote_effect_context"
        ):
            raise ValueError(
                "E_REMOTE_EFFECT: push context claim is unavailable"
            )
        context._consumed = True
        _FEATURE_PUSH_OPERATIONS[id(context)] = _FeaturePushOperation(
            context=context,
            bindings=bindings,
            state="claimed",
        )
        _register_runtime_host_object(
            context, "claimed_feature_push_context"
        )
        return bindings


def _set_feature_push_precondition_failed(
    context: ValidatedRemoteEffectContext,
) -> None:
    with _FEATURE_PUSH_CLAIM_LOCK:
        operation = _FEATURE_PUSH_OPERATIONS.get(id(context))
        if (
            type(operation) is _FeaturePushOperation
            and operation.context is context
            and operation.state == "claimed"
        ):
            _consume_runtime_host_object(
                context, "claimed_feature_push_context"
            )
            operation.state = "precondition_failed"


def _start_feature_push_effect(
    context: ValidatedRemoteEffectContext,
) -> _FeaturePushEffectBindings:
    with _FEATURE_PUSH_CLAIM_LOCK:
        operation = _FEATURE_PUSH_OPERATIONS.get(id(context))
        if (
            type(operation) is not _FeaturePushOperation
            or operation.context is not context
            or operation.state != "claimed"
            or not _consume_runtime_host_object(
                context, "claimed_feature_push_context"
            )
        ):
            raise ValueError(
                "E_REMOTE_EFFECT: claimed push context is unavailable"
            )
        operation.state = "effect_started"
        return operation.bindings


def _set_feature_push_outcome_unknown(
    context: ValidatedRemoteEffectContext,
) -> None:
    with _FEATURE_PUSH_CLAIM_LOCK:
        operation = _FEATURE_PUSH_OPERATIONS.get(id(context))
        if (
            type(operation) is not _FeaturePushOperation
            or operation.context is not context
            or operation.state
            not in {"effect_started", "effect_acknowledged"}
        ):
            raise ValueError(
                "E_REMOTE_EFFECT_OUTCOME_UNKNOWN: push state is invalid"
            )
        operation.state = "outcome_unknown"
        _register_runtime_host_object(
            context, "feature_push_unknown_context"
        )


def _observe_feature_push(
    bindings: _FeaturePushEffectBindings,
    *,
    clock: Callable[[], float],
) -> LocalGitObservation:
    worktree = _canonical_directory(
        bindings.worktree_identity, code="E_REMOTE_EFFECT"
    )
    live_push_url = _git_text(
        worktree,
        ["remote", "get-url", "--push", bindings.remote_name],
    )
    if (
        live_push_url != bindings.push_url
        or _canonical_github_repository_from_url(
            live_push_url, code="E_REMOTE_EFFECT"
        )
        != bindings.remote_repository
        or _git_text(worktree, ["rev-parse", "HEAD"])
        != bindings.head
        or _git_text(worktree, ["branch", "--show-current"])
        != bindings.branch
        or _git_text(
            worktree,
            ["status", "--porcelain=v2", "--untracked-files=all"],
        )
    ):
        raise ValueError(
            "E_REMOTE_EFFECT: feature push observation binding drifted"
        )
    _assert_no_unsafe_transport_config(worktree)
    remote_returncode, remote_output = _execute_native_remote(
        "git_feature_observe",
        tuple(
            _closed_git_argv(
                worktree,
                [
                    "ls-remote",
                    "--heads",
                    bindings.push_url,
                    f"refs/heads/{bindings.branch}",
                ],
            )
        ),
        max_output_bytes=4096,
    )
    expected_remote_line = (
        f"{bindings.head}\trefs/heads/{bindings.branch}\n".encode("utf-8")
    )
    if (
        remote_returncode != 0
        or remote_output != expected_remote_line
    ):
        raise ValueError(
            "E_REMOTE_EFFECT: feature push observation is inconclusive"
        )
    now = float(clock())
    observation = object.__new__(LocalGitObservation)
    observation.observation_id = f"push-{uuid4().hex}"
    observation.invocation_id = bindings.invocation_id
    observation.task_digest = bindings.task_digest
    observation.repository_identity = bindings.repository_identity
    observation.worktree_identity = bindings.worktree_identity
    observation.branch = bindings.branch
    observation.prior_head = bindings.head
    observation.target_state = "pushed"
    observation.session_id = bindings.session_id
    observation.provider = "git"
    observation.subject_digest = bindings.context_digest
    observation.evidence = {"remote_head": bindings.head}
    observation.observed_at_monotonic = now
    observation.freshness_deadline = now + 30
    _register_runtime_host_object(observation, "local_git_observation")
    return observation


def push_validated_feature(
    *,
    context: object,
    governing_runtime: object,
    governing_policy: object,
    authorization: object,
    inventory: object,
    session_id: str,
    invocation_id: str,
    tool_use_id: str,
    clock: Callable[[], float],
) -> LocalGitObservation:
    from control_plane.policy import (
        GoverningPolicy,
        _consume_governing_policy,
        _governing_policy_is_live_for_runtime,
    )

    if (
        type(context) is not ValidatedRemoteEffectContext
        or context._consumed
        or context.effect != "remote_write"
        or type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or float(clock()) > governing_runtime.freshness_deadline
        or governing_runtime.target_worktree != context.worktree_identity
        or type(governing_policy) is not GoverningPolicy
        or not _governing_policy_is_live_for_runtime(
            governing_policy, governing_runtime, clock=clock
        )
        or type(inventory) is not ValidatedWorktreeInventoryObservation
        or inventory._consumed
        or type(authorization) is not TrustedAuthorization
    ):
        raise ValueError("E_REMOTE_EFFECT: closed push bindings are required")
    policy_git = governing_policy.policy.get("git", {})
    remote = policy_git.get("remote")
    base = policy_git.get("base_branch")
    if (
        not isinstance(remote, str)
        or not remote
        or not isinstance(base, str)
        or context.branch == base
        or context.remote_name != remote
        or context.session_id != session_id
        or context.invocation_id != invocation_id
    ):
        raise ValueError("E_REMOTE_EFFECT: push policy binding is invalid")
    _assert_remote_effect_context_live(
        context, code="E_REMOTE_EFFECT"
    )
    push_url = _git_text(
        Path(context.worktree_identity), ["remote", "get-url", "--push", remote]
    )
    try:
        live_remote_repository = _canonical_github_repository_from_url(
            push_url, code="E_REMOTE_EFFECT"
        )
    except ValueError as error:
        raise ValueError(
            "E_REMOTE_EFFECT: push URL requires credential-free github.com HTTPS"
        ) from error
    if (
        context.remote_repository != governing_policy.remote_repository
        or live_remote_repository != context.remote_repository
    ):
        raise ValueError(
            "E_REMOTE_EFFECT: push repository identity drifted"
        )
    _assert_no_unsafe_transport_config(Path(context.worktree_identity))
    bindings = _claim_feature_push_context(
        context, push_url=push_url
    )
    try:
        consume_authorization(
            authorization,
            expected_task_digest=context.task_digest,
            expected_session_id=session_id,
            expected_repository_identity=context.repository_identity,
            expected_worktree_identity=context.worktree_identity,
            expected_branch=context.branch,
            expected_head=context.head,
            expected_subject_digest=context.context_digest,
            expected_scope_paths=(".",),
            expected_effect="remote_write",
            expected_operation_nonce=tool_use_id,
            expected_invocation_id=invocation_id,
            clock=clock,
        )
        _consume_worktree_inventory(
            inventory,
            expected_common_git_dir=Path(inventory.common_git_dir),
        )
        if not _consume_governing_policy(
            governing_policy
        ) or not _consume_governing_runtime_observation(
            governing_runtime
        ):
            raise ValueError(
                "E_REMOTE_EFFECT: governing runtime or policy is not host-issued"
            )
        governing_policy._consumed = True
        governing_runtime._consumed = True
        _assert_remote_effect_context_live(
            context, code="E_REMOTE_EFFECT"
        )
        revalidated_push_url = _git_text(
            Path(context.worktree_identity),
            ["remote", "get-url", "--push", remote],
        )
        if (
            revalidated_push_url != bindings.push_url
            or _canonical_github_repository_from_url(
                revalidated_push_url, code="E_REMOTE_EFFECT"
            )
            != bindings.remote_repository
        ):
            raise ValueError(
                "E_REMOTE_EFFECT: push repository identity drifted"
            )
        _assert_no_unsafe_transport_config(
            Path(context.worktree_identity)
        )
        bindings = _start_feature_push_effect(context)
    except Exception:
        _set_feature_push_precondition_failed(context)
        raise
    try:
        push_returncode, _ = _execute_native_remote(
            "git_feature_push",
            tuple(
                _closed_git_argv(
                    bindings.worktree_identity,
                    [
                        "push",
                        bindings.push_url,
                        (
                            f"refs/heads/{bindings.branch}:"
                            f"refs/heads/{bindings.branch}"
                        ),
                    ],
                )
            ),
            max_output_bytes=0,
        )
    except Exception as error:
        _set_feature_push_outcome_unknown(context)
        raise ValueError(
            "E_REMOTE_EFFECT_OUTCOME_UNKNOWN: context consumed; "
            "observe the exact remote branch and never retry the push"
        ) from error
    if push_returncode != 0:
        _set_feature_push_outcome_unknown(context)
        raise ValueError(
            "E_REMOTE_EFFECT_OUTCOME_UNKNOWN: context consumed; "
            "observe the exact remote branch and never retry the push"
        )
    with _FEATURE_PUSH_CLAIM_LOCK:
        operation = _FEATURE_PUSH_OPERATIONS[id(context)]
        operation.state = "effect_acknowledged"
    try:
        observation = _observe_feature_push(
            bindings, clock=clock
        )
    except Exception as error:
        _set_feature_push_outcome_unknown(context)
        raise ValueError(
            "E_REMOTE_EFFECT_OUTCOME_UNKNOWN: effect acknowledged but "
            "observation is inconclusive; never retry the push"
        ) from error
    with _FEATURE_PUSH_CLAIM_LOCK:
        operation = _FEATURE_PUSH_OPERATIONS[id(context)]
        operation.state = "completed"
    return observation


def recover_feature_push_outcome(
    context: object, *, clock: Callable[[], float]
) -> LocalGitObservation:
    with _FEATURE_PUSH_CLAIM_LOCK:
        operation = _FEATURE_PUSH_OPERATIONS.get(id(context))
        if (
            type(context) is not ValidatedRemoteEffectContext
            or type(operation) is not _FeaturePushOperation
            or operation.context is not context
            or operation.state != "outcome_unknown"
            or operation.recovery_consumed
            or not _runtime_host_object_is_live(
                context, "feature_push_unknown_context"
            )
        ):
            raise ValueError(
                "E_REMOTE_EFFECT_RECOVERY: unknown push outcome is required"
            )
        if not _consume_runtime_host_object(
            context, "feature_push_unknown_context"
        ):
            raise ValueError(
                "E_REMOTE_EFFECT_RECOVERY: push recovery claim failed"
            )
        operation.recovery_consumed = True
        operation.state = "recovery_started"
        bindings = operation.bindings
    try:
        observation = _observe_feature_push(
            bindings, clock=clock
        )
    except Exception as error:
        with _FEATURE_PUSH_CLAIM_LOCK:
            operation.state = "recovery_pending"
        raise ValueError(
            "E_REMOTE_EFFECT_RECOVERY_PENDING: exact remote observation "
            "is inconclusive; do not repeat the push"
        ) from error
    with _FEATURE_PUSH_CLAIM_LOCK:
        operation.state = "recovered"
    return observation


class NativeGitHubProviderEvent:
    __slots__ = (
        "_consumed",
        "event_id",
        "repository",
        "session_id",
        "invocation_id",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "NativeGitHubProviderEvent":
        raise TypeError("GitHub provider event is native-host only")


class ValidatedGitHubPullRequestWriteProvider:
    __slots__ = (
        "_consumed",
        "provider_id",
        "repository",
        "base_branch",
        "session_id",
        "invocation_id",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedGitHubPullRequestWriteProvider":
        raise TypeError("GitHub PR write provider is host-bound")


def _assert_governing_pr_remote_live(
    *,
    governing_runtime: GoverningRuntimeObservation,
    governing_policy: object,
    expected_repository: str,
) -> None:
    canonical_expected_repository = _canonical_github_repository_identity(
        expected_repository, code="E_GITHUB_PR_PROVIDER"
    )
    policy_git = governing_policy.policy.get("git", {})
    remote_name = policy_git.get("remote")
    if not isinstance(remote_name, str) or not remote_name:
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: governing remote is unavailable"
        )
    try:
        live_repository = _canonical_github_repository_from_url(
            _git_text(
                _canonical_directory(
                    governing_runtime.target_worktree,
                    code="E_GITHUB_PR_PROVIDER",
                ),
                ["remote", "get-url", "--push", remote_name],
            ),
            code="E_GITHUB_PR_PROVIDER",
        )
    except ValueError as error:
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: governing remote is unavailable"
        ) from error
    if (
        live_repository != canonical_expected_repository
        or _canonical_github_repository_identity(
            governing_policy.remote_repository,
            code="E_GITHUB_PR_PROVIDER",
        )
        != canonical_expected_repository
    ):
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: governing remote identity drifted"
        )


def approve_github_pr_write_provider(
    native_provider_event: object,
    *,
    governing_runtime: object,
    governing_policy: object,
    expected_repository: str,
    session_id: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> ValidatedGitHubPullRequestWriteProvider:
    from control_plane.policy import (
        GoverningPolicy,
        _consume_governing_policy,
        _governing_policy_is_live_for_runtime,
    )

    if (
        type(native_provider_event) is not NativeGitHubProviderEvent
        or type(governing_runtime) is not GoverningRuntimeObservation
        or type(governing_policy) is not GoverningPolicy
    ):
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: native preauthenticated provider required"
        )
    try:
        canonical_expected_repository = (
            _canonical_github_repository_identity(
                expected_repository, code="E_GITHUB_PR_PROVIDER"
            )
        )
        canonical_event_repository = _canonical_github_repository_identity(
            native_provider_event.repository,
            code="E_GITHUB_PR_PROVIDER",
        )
        canonical_policy_repository = _canonical_github_repository_identity(
            governing_policy.remote_repository,
            code="E_GITHUB_PR_PROVIDER",
        )
    except ValueError as error:
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: native preauthenticated provider required"
        ) from error
    if (
        type(native_provider_event) is not NativeGitHubProviderEvent
        or not _native_host_object_is_valid(
            native_provider_event, "github_provider"
        )
        or native_provider_event._consumed
        or type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or float(clock()) > governing_runtime.freshness_deadline
        or type(governing_policy) is not GoverningPolicy
        or not _governing_policy_is_live_for_runtime(
            governing_policy, governing_runtime, clock=clock
        )
        or canonical_policy_repository != canonical_expected_repository
        or canonical_event_repository != canonical_expected_repository
        or native_provider_event.session_id != session_id
        or native_provider_event.invocation_id != invocation_id
        or governing_runtime.session_id != session_id
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: native preauthenticated provider required"
        )
    base_branch = governing_policy.policy.get("git", {}).get("base_branch")
    if not isinstance(base_branch, str) or not base_branch:
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: governing base branch is unavailable"
        )
    _assert_governing_pr_remote_live(
        governing_runtime=governing_runtime,
        governing_policy=governing_policy,
        expected_repository=canonical_expected_repository,
    )
    auth_returncode, _ = _execute_native_remote(
        "github_auth_status",
        ("gh", "auth", "status", "--hostname", "github.com"),
        max_output_bytes=0,
    )
    if auth_returncode != 0:
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: existing host authentication is not ready"
        )
    repository_returncode, raw_repository = _execute_native_remote(
        "github_repository_access",
        (
            "gh",
            "repo",
            "view",
            canonical_expected_repository,
            "--json",
            "nameWithOwner",
        ),
        max_output_bytes=4096,
    )
    try:
        repository_payload = json.loads(raw_repository)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        repository_payload = {}
    try:
        canonical_provider_repository = (
            _canonical_github_repository_identity(
                repository_payload.get("nameWithOwner"),
                code="E_GITHUB_PR_PROVIDER",
            )
        )
    except ValueError:
        canonical_provider_repository = None
    if (
        repository_returncode != 0
        or canonical_provider_repository != canonical_expected_repository
    ):
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: exact repository access is not ready"
        )
    _assert_governing_pr_remote_live(
        governing_runtime=governing_runtime,
        governing_policy=governing_policy,
        expected_repository=canonical_expected_repository,
    )
    if not _consume_governing_policy(
        governing_policy
    ) or not _consume_governing_runtime_observation(governing_runtime):
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: governing bindings are not host-issued"
        )
    governing_policy._consumed = True
    governing_runtime._consumed = True
    native_provider_event._consumed = True
    provider = object.__new__(ValidatedGitHubPullRequestWriteProvider)
    provider._consumed = False
    provider.provider_id = f"github-pr-write-{uuid4().hex}"
    provider.repository = canonical_expected_repository
    provider.base_branch = base_branch
    provider.session_id = native_provider_event.session_id
    provider.invocation_id = native_provider_event.invocation_id
    provider.freshness_deadline = float(clock()) + float(ttl_seconds)
    _register_runtime_host_object(provider, "github_pr_write_provider")
    return provider


@dataclass(frozen=True)
class ValidatedPullRequestTitle:
    value: str
    digest: str


@dataclass(frozen=True)
class ValidatedPullRequestBody:
    value: str
    digest: str


def validate_pull_request_title(value: str) -> ValidatedPullRequestTitle:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 180
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("E_PR_CONTENT: title is invalid")
    return ValidatedPullRequestTitle(value=value, digest=contract_digest(value))


def validate_pull_request_body(value: str) -> ValidatedPullRequestBody:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 65_536
        or "\x00" in value
        or re.search(
            r"(?i)(ghp_|github_pat_|-----BEGIN [A-Z ]+PRIVATE KEY-----)",
            value,
        )
    ):
        raise ValueError("E_PR_CONTENT: body is invalid or secret-like")
    return ValidatedPullRequestBody(value=value, digest=contract_digest(value))


class ValidatedPullRequestMutationRequest:
    __slots__ = (
        "_consumed",
        "_execution_state",
        "_recovery_consumed",
        "_effect_bindings",
        "context",
        "provider",
        "title",
        "body",
        "draft",
        "expected_pr_number",
        "session_id",
        "invocation_id",
        "request_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedPullRequestMutationRequest":
        raise TypeError("validated PR mutation request is host-bound")


@dataclass(frozen=True)
class _PullRequestMutationEffectBindings:
    repository: str
    base_branch: str
    branch: str
    head: str
    remote_repository: str
    remote_name: str
    expected_base_sha: str
    expected_checks_digest: str | None
    expected_pr_number: int | None
    title: str
    body: str
    draft: bool
    session_id: str
    invocation_id: str
    provider_freshness_deadline: float


def build_pull_request_mutation_request(
    *,
    context: object,
    provider: object,
    authorization: object,
    title: object,
    body: object,
    draft: bool,
    expected_pr_number: int | None,
    session_id: str,
    invocation_id: str,
    tool_use_id: str,
    clock: Callable[[], float],
) -> ValidatedPullRequestMutationRequest:
    if (
        type(context) is not ValidatedRemoteEffectContext
        or context._consumed
        or context.effect != "pull_request"
        or type(provider) is not ValidatedGitHubPullRequestWriteProvider
        or not _runtime_host_object_is_live(
            provider, "github_pr_write_provider"
        )
        or provider._consumed
        or float(clock()) > provider.freshness_deadline
        or type(title) is not ValidatedPullRequestTitle
        or type(body) is not ValidatedPullRequestBody
        or not isinstance(draft, bool)
        or context.expected_pr_number != expected_pr_number
        or context.session_id != session_id
        or context.invocation_id != invocation_id
        or provider.repository != context.remote_repository
        or provider.session_id != session_id
        or provider.invocation_id != invocation_id
    ):
        raise ValueError("E_PR_MUTATION: closed PR bindings are required")
    _assert_remote_effect_context_live(context, code="E_PR_MUTATION")
    subject_digest = contract_digest(
        {
            "context": context.context_digest,
            "title": title.digest,
            "body": body.digest,
            "draft": draft,
            "expected_pr_number": expected_pr_number,
        }
    )
    consume_authorization(
        authorization,
        expected_task_digest=context.task_digest,
        expected_session_id=session_id,
        expected_repository_identity=context.repository_identity,
        expected_worktree_identity=context.worktree_identity,
        expected_branch=context.branch,
        expected_head=context.head,
        expected_subject_digest=subject_digest,
        expected_scope_paths=(".",),
        expected_effect="pull_request",
        expected_operation_nonce=tool_use_id,
        expected_invocation_id=invocation_id,
        clock=clock,
    )
    if not _consume_runtime_host_object(
        context, "validated_remote_effect_context"
    ) or not _consume_runtime_host_object(
        provider, "github_pr_write_provider"
    ):
        raise ValueError("E_PR_MUTATION: host-issued bindings are required")
    context._consumed = True
    provider._consumed = True
    _register_runtime_host_object(context, "pr_request_context")
    _register_runtime_host_object(provider, "pr_request_provider")
    request = object.__new__(ValidatedPullRequestMutationRequest)
    request._consumed = False
    request._execution_state = "ready"
    request._recovery_consumed = False
    request._effect_bindings = None
    request.context = context
    request.provider = provider
    request.title = title
    request.body = body
    request.draft = draft
    request.expected_pr_number = expected_pr_number
    request.session_id = session_id
    request.invocation_id = invocation_id
    request.request_digest = contract_digest(
        {
            "context": context.context_digest,
            "provider": provider.provider_id,
            "title": title.digest,
            "body": body.digest,
            "draft": draft,
            "expected_pr_number": expected_pr_number,
            "session_id": session_id,
            "invocation_id": invocation_id,
        }
    )
    _register_runtime_host_object(request, "pr_mutation_request")
    return request


class PullRequestMutationObservation:
    __slots__ = (
        "_consumed",
        "repository",
        "base",
        "head_branch",
        "head_sha",
        "number",
        "url",
        "draft",
        "session_id",
        "invocation_id",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "PullRequestMutationObservation":
        raise TypeError("PR mutation observation is host-bound")


class ValidatedPullRequestMutationObservation(PullRequestMutationObservation):
    pass


def _github_json_response(
    operation: str,
    arguments: tuple[str, ...],
    *,
    max_output_bytes: int,
) -> object:
    returncode, raw = _execute_native_remote(
        operation,
        arguments,
        max_output_bytes=max_output_bytes,
    )
    if returncode != 0:
        raise ValueError(
            "E_PR_MUTATION: live provider precondition is unavailable"
        )
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(
            "E_PR_MUTATION: live provider precondition is invalid"
        ) from error


def _live_github_checks_digest(
    payload: object, *, expected_head: str
) -> str | None:
    if not isinstance(payload, Mapping):
        raise ValueError("E_PR_MUTATION: live checks are invalid")
    total_count = payload.get("total_count")
    raw_runs = payload.get("check_runs")
    if (
        not isinstance(total_count, int)
        or isinstance(total_count, bool)
        or not 0 <= total_count <= 100
        or not isinstance(raw_runs, list)
        or len(raw_runs) != total_count
    ):
        raise ValueError("E_PR_MUTATION: live checks are incomplete")
    checks: list[dict[str, object]] = []
    identifiers: set[int] = set()
    for raw_run in raw_runs:
        if not isinstance(raw_run, Mapping):
            raise ValueError("E_PR_MUTATION: live checks are invalid")
        identifier = raw_run.get("id")
        name = raw_run.get("name")
        status = raw_run.get("status")
        conclusion = raw_run.get("conclusion")
        head_sha = raw_run.get("head_sha")
        app = raw_run.get("app")
        app_slug = app.get("slug") if isinstance(app, Mapping) else None
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier <= 0
            or identifier in identifiers
            or not isinstance(name, str)
            or not name
            or not isinstance(status, str)
            or not status
            or (
                conclusion is not None
                and not isinstance(conclusion, str)
            )
            or head_sha != expected_head
            or not isinstance(app_slug, str)
            or not app_slug
        ):
            raise ValueError("E_PR_MUTATION: live checks are invalid")
        identifiers.add(identifier)
        checks.append(
            {
                "id": identifier,
                "name": name,
                "status": status,
                "conclusion": conclusion,
                "app_slug": app_slug,
            }
        )
    if not checks:
        return None
    checks.sort(
        key=lambda item: (
            int(item["id"]),
            str(item["name"]),
            str(item["app_slug"]),
        )
    )
    return contract_digest(tuple(checks))


def _assert_live_pull_request_preconditions(
    bindings: _PullRequestMutationEffectBindings,
) -> None:
    repository = bindings.repository
    base = bindings.base_branch
    if bindings.expected_pr_number is None:
        raw_pr = _github_json_response(
            "github_pull_request_precondition_pr",
            (
                "gh",
                "pr",
                "list",
                "--repo",
                repository,
                "--state",
                "open",
                "--base",
                base,
                "--head",
                bindings.branch,
                "--limit",
                "2",
                "--json",
                "number,baseRefName,headRefName,headRefOid",
            ),
            max_output_bytes=16_384,
        )
        if raw_pr != []:
            raise ValueError(
                "E_PR_MUTATION: live PR number drifted"
            )
    else:
        raw_pr = _github_json_response(
            "github_pull_request_precondition_pr",
            (
                "gh",
                "pr",
                "view",
                str(bindings.expected_pr_number),
                "--repo",
                repository,
                "--json",
                "number,baseRefName,headRefName,headRefOid",
            ),
            max_output_bytes=16_384,
        )
        if (
            not isinstance(raw_pr, Mapping)
            or raw_pr.get("number") != bindings.expected_pr_number
            or raw_pr.get("baseRefName") != base
            or raw_pr.get("headRefName") != bindings.branch
            or raw_pr.get("headRefOid") != bindings.head
        ):
            raise ValueError("E_PR_MUTATION: live PR binding drifted")
    raw_base = _github_json_response(
        "github_pull_request_precondition_base",
        (
            "gh",
            "api",
            f"repos/{repository}/git/ref/heads/{base}",
        ),
        max_output_bytes=16_384,
    )
    base_object = (
        raw_base.get("object")
        if isinstance(raw_base, Mapping)
        else None
    )
    if (
        not isinstance(base_object, Mapping)
        or base_object.get("sha") != bindings.expected_base_sha
    ):
        raise ValueError("E_PR_MUTATION: live base SHA drifted")
    raw_checks = _github_json_response(
        "github_pull_request_precondition_checks",
        (
            "gh",
            "api",
            (
                f"repos/{repository}/commits/{bindings.head}/"
                "check-runs?per_page=100"
            ),
        ),
        max_output_bytes=262_144,
    )
    if (
        _live_github_checks_digest(
            raw_checks, expected_head=bindings.head
        )
        != bindings.expected_checks_digest
    ):
        raise ValueError("E_PR_MUTATION: live checks digest drifted")


def _claim_pull_request_mutation(
    request: object,
) -> tuple[
    ValidatedPullRequestMutationRequest,
    ValidatedRemoteEffectContext,
    _PullRequestMutationEffectBindings,
]:
    with _PR_MUTATION_CLAIM_LOCK:
        context = getattr(request, "context", None)
        provider = getattr(request, "provider", None)
        title = getattr(request, "title", None)
        body = getattr(request, "body", None)
        expected_request_digest = (
            contract_digest(
                {
                    "context": context.context_digest,
                    "provider": provider.provider_id,
                    "title": title.digest,
                    "body": body.digest,
                    "draft": request.draft,
                    "expected_pr_number": request.expected_pr_number,
                    "session_id": request.session_id,
                    "invocation_id": request.invocation_id,
                }
            )
            if (
                type(context) is ValidatedRemoteEffectContext
                and type(provider)
                is ValidatedGitHubPullRequestWriteProvider
                and type(title) is ValidatedPullRequestTitle
                and type(body) is ValidatedPullRequestBody
            )
            else None
        )
        if (
            type(request) is not ValidatedPullRequestMutationRequest
            or request._consumed
            or request._execution_state != "ready"
            or request._effect_bindings is not None
            or type(context) is not ValidatedRemoteEffectContext
            or type(provider)
            is not ValidatedGitHubPullRequestWriteProvider
            or type(title) is not ValidatedPullRequestTitle
            or type(body) is not ValidatedPullRequestBody
            or not _runtime_host_object_is_live(
                request, "pr_mutation_request"
            )
            or not _runtime_host_object_is_live(
                context, "pr_request_context"
            )
            or not _runtime_host_object_is_live(
                provider, "pr_request_provider"
            )
            or request.request_digest != expected_request_digest
            or provider.repository != context.remote_repository
            or request.session_id != context.session_id
            or request.invocation_id != context.invocation_id
            or provider.session_id != context.session_id
            or provider.invocation_id != context.invocation_id
        ):
            raise ValueError(
                "E_PR_MUTATION: typed unclaimed request is required"
            )
        bindings = _PullRequestMutationEffectBindings(
            repository=provider.repository,
            base_branch=provider.base_branch,
            branch=context.branch,
            head=context.head,
            remote_repository=context.remote_repository,
            remote_name=context.remote_name,
            expected_base_sha=str(context.expected_base_sha),
            expected_checks_digest=context.expected_checks_digest,
            expected_pr_number=request.expected_pr_number,
            title=title.value,
            body=body.value,
            draft=request.draft,
            session_id=request.session_id,
            invocation_id=request.invocation_id,
            provider_freshness_deadline=provider.freshness_deadline,
        )
        if not _consume_runtime_host_object(
            request, "pr_mutation_request"
        ) or not _consume_runtime_host_object(
            context, "pr_request_context"
        ) or not _consume_runtime_host_object(
            provider, "pr_request_provider"
        ):
            raise ValueError(
                "E_PR_MUTATION: request claim is unavailable"
            )
        request._consumed = True
        request._execution_state = "claimed"
        request._effect_bindings = bindings
        _register_runtime_host_object(
            context, "claimed_pr_request_context"
        )
        return request, context, bindings


def _observe_pull_request_mutation(
    bindings: _PullRequestMutationEffectBindings,
    *,
    clock: Callable[[], float],
) -> PullRequestMutationObservation:
    repository = bindings.repository
    selector = (
        str(bindings.expected_pr_number)
        if bindings.expected_pr_number is not None
        else bindings.branch
    )
    observed_returncode, observed_output = _execute_native_remote(
        "github_pull_request_observe",
        (
            "gh",
            "pr",
            "view",
            selector,
            "--repo",
            repository,
            "--json",
            "number,url,isDraft,baseRefName,headRefName,headRefOid",
        ),
        max_output_bytes=16_384,
    )
    try:
        payload = json.loads(observed_output)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(
            "E_PR_MUTATION: provider observation failed"
        ) from error
    number = payload.get("number") if isinstance(payload, Mapping) else None
    url = payload.get("url") if isinstance(payload, Mapping) else None
    draft = payload.get("isDraft") if isinstance(payload, Mapping) else None
    try:
        url_repository, url_number = _github_pull_request_url_identity(
            url, code="E_PR_MUTATION"
        )
    except ValueError:
        url_repository, url_number = None, None
    if (
        observed_returncode != 0
        or not isinstance(number, int)
        or isinstance(number, bool)
        or number <= 0
        or (
            bindings.expected_pr_number is not None
            and number != bindings.expected_pr_number
        )
        or not isinstance(url, str)
        or url_repository != repository
        or url_number != number
        or not isinstance(draft, bool)
        or payload.get("baseRefName") != bindings.base_branch
        or payload.get("headRefName") != bindings.branch
        or payload.get("headRefOid") != bindings.head
    ):
        raise ValueError(
            "E_PR_MUTATION: provider observation binding drifted"
        )
    now = float(clock())
    observation = object.__new__(PullRequestMutationObservation)
    observation._consumed = False
    observation.repository = repository
    observation.base = bindings.base_branch
    observation.head_branch = bindings.branch
    observation.head_sha = bindings.head
    observation.number = number
    observation.url = url
    observation.draft = draft
    observation.session_id = bindings.session_id
    observation.invocation_id = bindings.invocation_id
    observation.freshness_deadline = now + 30
    _register_runtime_host_object(
        observation, "pull_request_mutation_observation"
    )
    return observation


def execute_pull_request_mutation(
    request: object, *, clock: Callable[[], float]
) -> PullRequestMutationObservation:
    request, context, bindings = _claim_pull_request_mutation(request)
    repository = bindings.repository
    base = bindings.base_branch
    try:
        _assert_remote_effect_context_live(context, code="E_PR_MUTATION")
        if (
            not isinstance(base, str)
            or not base
            or _GIT_OBJECT_ID.fullmatch(bindings.expected_base_sha) is None
            or float(clock()) > bindings.provider_freshness_deadline
        ):
            raise ValueError("E_PR_MUTATION: base binding is required")
        _assert_live_pull_request_preconditions(bindings)
        _assert_remote_effect_context_live(
            context, code="E_PR_MUTATION"
        )
    except Exception:
        _consume_runtime_host_object(
            context, "claimed_pr_request_context"
        )
        request._execution_state = "precondition_failed"
        raise
    if not _consume_runtime_host_object(
        context, "claimed_pr_request_context"
    ):
        request._execution_state = "precondition_failed"
        raise ValueError(
            "E_PR_MUTATION: claimed context is unavailable"
        )
    if bindings.expected_pr_number is None:
        arguments = (
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            base,
            "--head",
            bindings.branch,
            "--title",
            bindings.title,
            "--body",
            bindings.body,
        )
        if bindings.draft:
            arguments = (*arguments, "--draft")
    elif bindings.draft:
        arguments = (
            "gh",
            "pr",
            "edit",
            str(bindings.expected_pr_number),
            "--repo",
            repository,
            "--title",
            bindings.title,
            "--body",
            bindings.body,
        )
    else:
        arguments = (
            "gh",
            "pr",
            "ready",
            str(bindings.expected_pr_number),
            "--repo",
            repository,
        )
    request._execution_state = "effect_started"
    try:
        mutation_returncode, _ = _execute_native_remote(
            "github_pull_request_mutation",
            arguments,
            max_output_bytes=0,
        )
    except Exception as error:
        request._execution_state = "outcome_unknown"
        _register_runtime_host_object(
            request, "pr_mutation_unknown_request"
        )
        raise ValueError(
            "E_PR_MUTATION_OUTCOME_UNKNOWN: request consumed; "
            "observe the exact selector and never retry the effect"
        ) from error
    if mutation_returncode != 0:
        request._execution_state = "outcome_unknown"
        _register_runtime_host_object(
            request, "pr_mutation_unknown_request"
        )
        raise ValueError(
            "E_PR_MUTATION_OUTCOME_UNKNOWN: request consumed; "
            "observe the exact selector and never retry the effect"
        )
    request._execution_state = "effect_acknowledged"
    try:
        observation = _observe_pull_request_mutation(
            bindings, clock=clock
        )
    except Exception as error:
        request._execution_state = "outcome_unknown"
        _register_runtime_host_object(
            request, "pr_mutation_unknown_request"
        )
        raise ValueError(
            "E_PR_MUTATION_OUTCOME_UNKNOWN: effect acknowledged but "
            "observation is inconclusive; never retry the effect"
        ) from error
    request._execution_state = "completed"
    return observation


def recover_pull_request_mutation_outcome(
    request: object, *, clock: Callable[[], float]
) -> PullRequestMutationObservation:
    with _PR_MUTATION_CLAIM_LOCK:
        bindings = getattr(request, "_effect_bindings", None)
        if (
            type(request) is not ValidatedPullRequestMutationRequest
            or request._execution_state != "outcome_unknown"
            or request._recovery_consumed
            or type(bindings) is not _PullRequestMutationEffectBindings
            or not _runtime_host_object_is_live(
                request, "pr_mutation_unknown_request"
            )
            or float(clock()) > bindings.provider_freshness_deadline
        ):
            raise ValueError(
                "E_PR_MUTATION_RECOVERY: fresh unknown outcome is required"
            )
        if not _consume_runtime_host_object(
            request, "pr_mutation_unknown_request"
        ):
            raise ValueError(
                "E_PR_MUTATION_RECOVERY: request recovery claim failed"
            )
        request._recovery_consumed = True
    try:
        observation = _observe_pull_request_mutation(
            bindings, clock=clock
        )
    except Exception as error:
        raise ValueError(
            "E_PR_MUTATION_RECOVERY_PENDING: exact provider observation "
            "is inconclusive; do not repeat the effect"
        ) from error
    request._execution_state = "recovered"
    return observation


def validate_pull_request_mutation(
    observation: object,
    *,
    expected_repository: str,
    expected_base: str,
    expected_head_branch: str,
    expected_head_sha: str,
    expected_pr_number: int | None,
    expected_draft: bool,
    expected_session_id: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedPullRequestMutationObservation:
    try:
        canonical_expected_repository = (
            _canonical_github_repository_identity(
                expected_repository, code="E_PR_MUTATION"
            )
        )
    except ValueError as error:
        raise ValueError(
            "E_PR_MUTATION: PR observation binding drifted"
        ) from error
    if (
        type(observation) is not PullRequestMutationObservation
        or not _runtime_host_object_is_live(
            observation, "pull_request_mutation_observation"
        )
        or observation._consumed
        or observation.repository != canonical_expected_repository
        or observation.base != expected_base
        or observation.head_branch != expected_head_branch
        or observation.head_sha != expected_head_sha
        or (
            expected_pr_number is not None
            and observation.number != expected_pr_number
        )
        or observation.draft != expected_draft
        or observation.session_id != expected_session_id
        or observation.invocation_id != expected_invocation_id
        or float(clock()) > observation.freshness_deadline
    ):
        raise ValueError("E_PR_MUTATION: PR observation binding drifted")
    if not _consume_runtime_host_object(
        observation, "pull_request_mutation_observation"
    ):
        raise ValueError("E_PR_MUTATION: PR observation is not host-issued")
    observation._consumed = True
    validated = object.__new__(ValidatedPullRequestMutationObservation)
    validated._consumed = False
    for name in (
        "repository",
        "base",
        "head_branch",
        "head_sha",
        "number",
        "url",
        "draft",
        "session_id",
        "invocation_id",
        "freshness_deadline",
    ):
        setattr(validated, name, getattr(observation, name))
    _register_runtime_host_object(
        validated, "validated_pull_request_mutation_observation"
    )
    return validated


MACOS_HOOK_SMOKE_SCENARIOS = (
    "warning_once",
    "sessionstart_compact_fallback",
    "safe_read_explicit_repo",
    "feature_commit_push",
    "base_detached_force_denied",
    "stop_receipt",
    "rollback_byte_exact",
    "source_isolated_parity",
)
MACOS_HOOK_SMOKE_ARTIFACTS = {
    "policy": ".codex/project-policy.toml",
    "registry": ".codex/resource-registry.toml",
    "lock": ".codex/control-plane.lock",
    "launcher": "scripts/control-plane",
    "hooks": ".codex/hooks.json",
}
_SMOKE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", re.ASCII)


@dataclass(frozen=True)
class MacOSHookSmokeCase:
    case_id: str
    status: str
    exit_code: int | None
    stdout_digest: str
    stderr_digest: str


class CompletedMacOSHookSmoke:
    __slots__ = (
        "_consumed",
        "platform_name",
        "repository",
        "head",
        "artifact_digests",
        "harness_digest",
        "harness_binding_digest",
        "session_id",
        "invocation_id",
        "dedicated_temp_root",
        "observed_at_monotonic",
        "cases",
        "mechanical_result",
        "native_adapter",
        "human_hooks_review",
        "authorizes",
        "completed_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "CompletedMacOSHookSmoke":
        raise TypeError("macOS hook smoke result is host-bound")


class VerificationTaskContext:
    __slots__ = (
        "_consumed",
        "task_id",
        "task_digest",
        "profile",
        "profile_digest",
        "runtime_digest",
        "target_digest",
        "repository",
        "worktree",
        "expected_head",
        "session_id",
        "lease_digest",
        "generation",
        "execution_context_digest",
        "context_digest",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "VerificationTaskContext":
        raise TypeError("verification task context is host-bound")


@dataclass(frozen=True)
class MacOSHookSmokeReceipt:
    schema_version: int
    kind: str
    task_id: str
    task_digest: str
    profile: str
    profile_digest: str
    runtime_digest: str
    target_digest: str
    repository: str
    head: str
    artifact_digests: tuple[tuple[str, str], ...]
    harness_digest: str
    harness_binding_digest: str
    session_id: str
    invocation_id: str
    generation: int
    completed_digest: str
    mechanical_result: str
    native_adapter: str
    human_hooks_review: str
    authorizes: bool
    receipt_digest: str


@dataclass(frozen=True)
class HookSmokePublicationResult:
    receipt: MacOSHookSmokeReceipt
    task_context: VerificationTaskContext


def _closed_artifact_digests(
    canonical_repo: Path, supplied: object
) -> tuple[tuple[str, str], ...]:
    if not isinstance(supplied, Mapping) or set(supplied) != set(
        MACOS_HOOK_SMOKE_ARTIFACTS
    ):
        raise ValueError(
            "E_MACOS_SMOKE_BINDING: artifact set is not exact"
        )
    observed: list[tuple[str, str]] = []
    for name, relative in MACOS_HOOK_SMOKE_ARTIFACTS.items():
        path = canonical_repo / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                "E_MACOS_SMOKE_BINDING: required artifact is unavailable"
            )
        digest = f"sha256:{sha256(path.read_bytes()).hexdigest()}"
        if supplied.get(name) != digest:
            raise ValueError(
                "E_MACOS_SMOKE_BINDING: artifact digest drifted"
            )
        observed.append((name, digest))
    return tuple(observed)


def _smoke_git_head(repository: Path) -> str:
    try:
        completed = subprocess.run(
            trusted_git_argv(repository, ("rev-parse", "HEAD")),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=trusted_git_environment(),
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _smoke_harness_binding(
    repository: Path,
    expected_head: str,
) -> tuple[str, str, bool]:
    harness_relative = "tests/macos_hook_smoke.py"
    empty_digest = f"sha256:{sha256(b'').hexdigest()}"
    git = _trusted_git_executable()
    expected_bytes: bytes | None = None
    if git is not None:
        try:
            completed = subprocess.run(
                [
                    git,
                    "-C",
                    str(repository),
                    "show",
                    f"{expected_head}:{harness_relative}",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=_sanitized_git_environment(),
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if (
            completed is not None
            and completed.returncode == 0
            and len(completed.stdout) <= 1_048_576
        ):
            expected_bytes = completed.stdout
    harness = repository / harness_relative
    current_bytes: bytes | None = None
    try:
        if (
            not harness.is_symlink()
            and harness.is_file()
            and harness.stat().st_size <= 1_048_576
        ):
            current_bytes = harness.read_bytes()
    except OSError:
        current_bytes = None
    expected_digest = (
        f"sha256:{sha256(expected_bytes).hexdigest()}"
        if expected_bytes is not None
        else empty_digest
    )
    current_digest = (
        f"sha256:{sha256(current_bytes).hexdigest()}"
        if current_bytes is not None
        else empty_digest
    )
    exact = expected_bytes is not None and current_bytes == expected_bytes
    binding_digest = contract_digest(
        {
            "repository": str(repository),
            "head": expected_head,
            "path": harness_relative,
            "expected_digest": expected_digest,
            "current_digest": current_digest,
            "status": "exact" if exact else "unknown",
        }
    )
    return expected_digest, binding_digest, exact


def _unknown_smoke_cases() -> tuple[dict[str, object], ...]:
    empty_digest = f"sha256:{sha256(b'').hexdigest()}"
    return tuple(
        {
            "id": case_id,
            "status": "UNKNOWN",
            "exit_code": None,
            "stdout_digest": empty_digest,
            "stderr_digest": empty_digest,
        }
        for case_id in MACOS_HOOK_SMOKE_SCENARIOS
    )


def _run_macos_smoke_process(
    repository: Path,
    dedicated_temp_root: Path,
    timeout_seconds: float,
) -> tuple[tuple[dict[str, object], ...], str]:
    harness = repository / "tests" / "macos_hook_smoke.py"
    if harness.is_symlink() or not harness.is_file():
        raise ValueError(
            "E_MACOS_SMOKE_RUNNER: smoke harness is unavailable"
        )
    environment = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LC_ALL": "C",
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(dedicated_temp_root),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            str(harness),
            "--run-macos-hook-smoke",
            "--repo",
            str(repository),
        ],
        cwd=repository,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        raise ValueError(
            "E_MACOS_SMOKE_RUNNER: smoke pipes are unavailable"
        )
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout_seconds
    group_closed = False

    def terminate_group() -> None:
        nonlocal group_closed
        if group_closed:
            return
        group_closed = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (PermissionError, ProcessLookupError):
                pass
            process.wait(timeout=5)

    try:
        while selector.get_map():
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                terminate_group()
                raise ValueError(
                    "E_MACOS_SMOKE_RUNNER: smoke process timed out"
                )
            for key, _ in selector.select(min(0.1, remaining_time)):
                stream = key.fileobj
                name = str(key.data)
                remaining = 262_144 - len(buffers[name])
                try:
                    chunk = os.read(
                        stream.fileno(), max(1, min(65_536, remaining + 1))
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                if len(chunk) > remaining:
                    terminate_group()
                    raise ValueError(
                        "E_MACOS_SMOKE_RUNNER: smoke output exceeded cap"
                    )
                buffers[name].extend(chunk)
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            terminate_group()
            raise ValueError(
                "E_MACOS_SMOKE_RUNNER: smoke process timed out"
            )
        try:
            returncode = process.wait(timeout=remaining_time)
        except subprocess.TimeoutExpired as error:
            terminate_group()
            raise ValueError(
                "E_MACOS_SMOKE_RUNNER: smoke process timed out"
            ) from error
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
        terminate_group()
    stdout = bytes(buffers["stdout"])
    stderr = bytes(buffers["stderr"])
    if returncode != 0:
        raise ValueError(
            "E_MACOS_SMOKE_RUNNER: smoke process failed"
        )
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            "E_MACOS_SMOKE_RUNNER: smoke output is invalid"
        ) from error
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"schema_version", "scenarios", "native_adapter"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("scenarios"), list)
        or payload.get("native_adapter") not in {"ready", "absent", "failed"}
    ):
        raise ValueError(
            "E_MACOS_SMOKE_RUNNER: smoke output schema is invalid"
        )
    return tuple(payload["scenarios"]), str(payload["native_adapter"])


def _closed_smoke_cases(
    supplied: object,
) -> tuple[MacOSHookSmokeCase, ...]:
    if not isinstance(supplied, tuple) or len(supplied) != len(
        MACOS_HOOK_SMOKE_SCENARIOS
    ):
        raise ValueError(
            "E_MACOS_SMOKE_RESULT: scenario set is not exact"
        )
    cases: list[MacOSHookSmokeCase] = []
    for expected_id, item in zip(MACOS_HOOK_SMOKE_SCENARIOS, supplied):
        if (
            not isinstance(item, Mapping)
            or set(item)
            != {
                "id",
                "status",
                "exit_code",
                "stdout_digest",
                "stderr_digest",
            }
            or item.get("id") != expected_id
            or item.get("status") not in {"PASS", "FAIL", "UNKNOWN"}
            or (
                item.get("exit_code") is not None
                and (
                    not isinstance(item.get("exit_code"), int)
                    or isinstance(item.get("exit_code"), bool)
                )
            )
            or any(
                not isinstance(item.get(name), str)
                or SHA256_DIGEST.fullmatch(str(item[name])) is None
                for name in ("stdout_digest", "stderr_digest")
            )
        ):
            raise ValueError(
                "E_MACOS_SMOKE_RESULT: scenario result is invalid"
            )
        cases.append(
            MacOSHookSmokeCase(
                case_id=expected_id,
                status=str(item["status"]),
                exit_code=item["exit_code"],
                stdout_digest=str(item["stdout_digest"]),
                stderr_digest=str(item["stderr_digest"]),
            )
        )
    return tuple(cases)


def run_macos_hook_smoke(
    *,
    canonical_repo: Path | str,
    expected_head: str,
    expected_artifact_digests: object,
    session_id: str,
    invocation_id: str,
    dedicated_temp_root: Path | str,
    clock: Callable[[], float],
    timeout_seconds: float,
) -> CompletedMacOSHookSmoke:
    repository = Path(canonical_repo)
    temp_root = Path(dedicated_temp_root)
    if (
        not repository.is_absolute()
        or repository.is_symlink()
        or not repository.is_dir()
        or repository.resolve() != repository
        or not temp_root.is_absolute()
        or temp_root.is_symlink()
        or temp_root.resolve(strict=False) != temp_root
        or repository == temp_root
        or repository in temp_root.parents
        or temp_root in repository.parents
        or _GIT_OBJECT_ID.fullmatch(expected_head) is None
        or _smoke_git_head(repository) != expected_head
        or _SMOKE_ID.fullmatch(session_id) is None
        or _SMOKE_ID.fullmatch(invocation_id) is None
        or not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not 0 < float(timeout_seconds) <= 300
    ):
        raise ValueError(
            "E_MACOS_SMOKE_BINDING: repository, HEAD, or invocation drifted"
        )
    try:
        observed_at = float(clock())
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            "E_MACOS_SMOKE_BINDING: clock is invalid"
        ) from error
    if not math.isfinite(observed_at):
        raise ValueError("E_MACOS_SMOKE_BINDING: clock is invalid")
    artifacts = _closed_artifact_digests(
        repository, expected_artifact_digests
    )
    harness_digest, harness_binding_digest, harness_exact = (
        _smoke_harness_binding(repository, expected_head)
    )
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if temp_root.is_symlink() or not temp_root.is_dir():
        raise ValueError(
            "E_MACOS_SMOKE_BINDING: temporary root is unsafe"
        )
    platform_name = platform.system()
    if platform_name != "Darwin" or not harness_exact:
        raw_cases = _unknown_smoke_cases()
        native_adapter = "absent"
    else:
        raw_cases, native_adapter = _run_macos_smoke_process(
            repository, temp_root, float(timeout_seconds)
        )
    cases = _closed_smoke_cases(raw_cases)
    statuses = {item.status for item in cases}
    mechanical_result = (
        "FAIL"
        if "FAIL" in statuses
        else "UNKNOWN"
        if "UNKNOWN" in statuses
        else "PASS"
    )
    completed = object.__new__(CompletedMacOSHookSmoke)
    completed._consumed = False
    completed.platform_name = platform_name
    completed.repository = str(repository)
    completed.head = expected_head
    completed.artifact_digests = artifacts
    completed.harness_digest = harness_digest
    completed.harness_binding_digest = harness_binding_digest
    completed.session_id = session_id
    completed.invocation_id = invocation_id
    completed.dedicated_temp_root = str(temp_root)
    completed.observed_at_monotonic = observed_at
    completed.cases = cases
    completed.mechanical_result = mechanical_result
    completed.native_adapter = native_adapter
    completed.human_hooks_review = "pending"
    completed.authorizes = False
    completed.completed_digest = contract_digest(
        {
            "platform_name": completed.platform_name,
            "repository": completed.repository,
            "head": completed.head,
            "artifact_digests": completed.artifact_digests,
            "harness_digest": completed.harness_digest,
            "harness_binding_digest": completed.harness_binding_digest,
            "session_id": completed.session_id,
            "invocation_id": completed.invocation_id,
            "observed_at_monotonic": completed.observed_at_monotonic,
            "cases": [
                {
                    "case_id": item.case_id,
                    "status": item.status,
                    "exit_code": item.exit_code,
                    "stdout_digest": item.stdout_digest,
                    "stderr_digest": item.stderr_digest,
                }
                for item in completed.cases
            ],
            "mechanical_result": completed.mechanical_result,
            "native_adapter": completed.native_adapter,
            "human_hooks_review": completed.human_hooks_review,
            "authorizes": completed.authorizes,
        }
    )
    _register_runtime_host_object(
        completed, "completed_macos_hook_smoke"
    )
    return completed


def _task_context_core(
    *,
    execution_context: object,
    generation: int,
) -> dict[str, object]:
    return {
        "task_id": execution_context.task_id,
        "task_digest": execution_context.task_digest,
        "profile": execution_context.profile,
        "profile_digest": execution_context.profile_digest,
        "runtime_digest": execution_context.runtime_digest,
        "target_digest": execution_context.target_digest,
        "repository": execution_context.repository,
        "worktree": execution_context.worktree,
        "expected_head": execution_context.expected_head,
        "session_id": execution_context.session_id,
        "lease_digest": execution_context.lease_digest,
        "generation": generation,
        "execution_context_digest": execution_context.context_digest,
    }


def _new_verification_task_context(
    values: Mapping[str, object],
) -> VerificationTaskContext:
    context = object.__new__(VerificationTaskContext)
    context._consumed = False
    for name, value in values.items():
        setattr(context, name, value)
    context.context_digest = contract_digest(values)
    _register_runtime_host_object(context, "verification_task_context")
    return context


def frame_verification_task_context(
    *,
    task_store: object,
    execution_context: object,
    expected_generation: int,
) -> VerificationTaskContext:
    from control_plane.lifecycle import (
        TaskStore,
        VerificationExecutionContext,
    )

    if (
        type(task_store) is not TaskStore
        or type(execution_context) is not VerificationExecutionContext
        or execution_context._consumed
        or not isinstance(expected_generation, int)
        or isinstance(expected_generation, bool)
    ):
        raise ValueError(
            "E_VERIFICATION_TASK_CONTEXT: typed verifier context is required"
        )
    state = task_store.status(execution_context.task_id)
    lease = task_store._read_owner_lease(execution_context.task_id)
    if (
        state.get("state") != "verifying"
        or state.get("generation") != expected_generation
        or state.get("task_digest") != execution_context.task_digest
        or state.get("verification_profile") != execution_context.profile
        or state.get("verification_profile_digest")
        != execution_context.profile_digest
        or state.get("verification_runtime_digest")
        != execution_context.runtime_digest
        or state.get("verification_target_digest")
        != execution_context.target_digest
        or state.get("session_id") != execution_context.session_id
        or lease is None
        or lease.get("lease_digest") != execution_context.lease_digest
        or Path(execution_context.repository).resolve()
        != Path(str(lease.get("worktree", ""))).resolve()
        or _smoke_git_head(Path(execution_context.repository))
        != execution_context.expected_head
    ):
        raise ValueError(
            "E_VERIFICATION_TASK_CONTEXT: task, lease, or HEAD drifted"
        )
    return _new_verification_task_context(
        _task_context_core(
            execution_context=execution_context,
            generation=expected_generation,
        )
    )


def _refresh_verification_task_context(
    context: VerificationTaskContext, generation: int
) -> VerificationTaskContext:
    values = {
        name: getattr(context, name)
        for name in (
            "task_id",
            "task_digest",
            "profile",
            "profile_digest",
            "runtime_digest",
            "target_digest",
            "repository",
            "worktree",
            "expected_head",
            "session_id",
            "lease_digest",
            "execution_context_digest",
        )
    }
    values["generation"] = generation
    return _new_verification_task_context(values)


def _read_durable_receipt(path: Path) -> dict[str, object] | None:
    if path.is_symlink():
        raise ValueError(
            "E_RECEIPT_RECOVERY: durable receipt is invalid"
        )
    if not path.exists():
        return None
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 65_536
        ):
            raise ValueError
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        digest = payload.get("receipt_digest")
        if (
            not isinstance(digest, str)
            or SHA256_DIGEST.fullmatch(digest) is None
            or contract_digest(
                {
                    key: value
                    for key, value in payload.items()
                    if key != "receipt_digest"
                }
            )
            != digest
        ):
            raise ValueError
    except (
        OSError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError(
            "E_RECEIPT_RECOVERY: durable receipt is invalid"
        ) from error
    return payload


def _atomic_receipt_json(
    path: Path,
    value: Mapping[str, object],
) -> None:
    """Persist receipt JSON through the dirfd/no-follow state writer."""

    from control_plane.hooks import _atomic_json as atomic_private_json

    atomic_private_json(path, value)


def publish_macos_hook_smoke_receipt(
    completed: CompletedMacOSHookSmoke,
    *,
    task_store: object,
    task_context: VerificationTaskContext,
    expected_generation: int,
) -> HookSmokePublicationResult:
    from control_plane.lifecycle import (
        TaskStore,
        _atomic_json,
        _task_guard,
    )

    if type(completed) is not CompletedMacOSHookSmoke:
        raise ValueError(
            "E_MACOS_SMOKE: typed completed smoke is required"
        )
    if completed._consumed:
        raise ValueError("E_MACOS_SMOKE_REPLAY: smoke was consumed")
    if (
        type(task_store) is not TaskStore
        or type(task_context) is not VerificationTaskContext
        or task_context._consumed
        or not _runtime_host_object_is_live(
            task_context, "verification_task_context"
        )
        or not _runtime_host_object_is_live(
            completed, "completed_macos_hook_smoke"
        )
        or expected_generation != task_context.generation
        or task_context.profile != "control_plane_assurance"
        or completed.repository != task_context.repository
        or completed.head != task_context.expected_head
        or completed.session_id != task_context.session_id
    ):
        raise ValueError(
            "E_MACOS_SMOKE: smoke and task context are not exact"
        )
    receipt_path = (
        task_store.state_dir
        / "codex-control-plane"
        / "verification-receipts"
        / task_context.task_id
        / "MacOSHookSmokeReceipt.json"
    )
    receipt_values = {
        "schema_version": 1,
        "kind": "MacOSHookSmokeReceipt",
        "task_id": task_context.task_id,
        "task_digest": task_context.task_digest,
        "profile": task_context.profile,
        "profile_digest": task_context.profile_digest,
        "runtime_digest": task_context.runtime_digest,
        "target_digest": task_context.target_digest,
        "repository": task_context.repository,
        "head": task_context.expected_head,
        "artifact_digests": completed.artifact_digests,
        "harness_digest": completed.harness_digest,
        "harness_binding_digest": completed.harness_binding_digest,
        "session_id": task_context.session_id,
        "invocation_id": completed.invocation_id,
        "generation": expected_generation,
        "completed_digest": completed.completed_digest,
        "mechanical_result": completed.mechanical_result,
        "native_adapter": completed.native_adapter,
        "human_hooks_review": "pending",
        "authorizes": False,
    }
    serialized_receipt_values = {
        **receipt_values,
        "artifact_digests": dict(completed.artifact_digests),
    }
    receipt = MacOSHookSmokeReceipt(
        **receipt_values,
        receipt_digest=contract_digest(serialized_receipt_values),
    )
    durable_receipt = {
        **serialized_receipt_values,
        "receipt_digest": receipt.receipt_digest,
    }
    registration_entry = {
        "observation_id": f"macos-smoke-{completed.invocation_id}",
        "receipt_digest": receipt.receipt_digest,
        "status": receipt.mechanical_result,
        "subject_digest": receipt.completed_digest,
    }
    with _task_guard(task_store.state_dir, task_context.task_id):
        state = task_store._read(task_context.task_id)
        lease = task_store._read_owner_lease(task_context.task_id)
        (
            current_harness_digest,
            current_harness_binding_digest,
            current_harness_exact,
        ) = _smoke_harness_binding(
            Path(task_context.repository),
            task_context.expected_head,
        )
        persisted = _read_durable_receipt(receipt_path)
        generation = state.get("generation")
        if (
            state.get("state") != "verifying"
            or generation
            not in {expected_generation, expected_generation + 1}
            or state.get("task_digest") != task_context.task_digest
            or state.get("verification_profile") != task_context.profile
            or state.get("verification_profile_digest")
            != task_context.profile_digest
            or state.get("verification_runtime_digest")
            != task_context.runtime_digest
            or state.get("verification_target_digest")
            != task_context.target_digest
            or state.get("session_id") != task_context.session_id
            or lease is None
            or lease.get("lease_digest") != task_context.lease_digest
            or _smoke_git_head(Path(task_context.repository))
            != task_context.expected_head
            or not current_harness_exact
            or current_harness_digest != completed.harness_digest
            or current_harness_binding_digest
            != completed.harness_binding_digest
            or (
                persisted is not None
                and persisted != durable_receipt
            )
        ):
            raise ValueError(
                "E_MACOS_SMOKE_CAS: task changed before smoke publish"
            )
        registration = dict(
            state.get("verification_supplemental_evidence", {})
        )
        if generation == expected_generation:
            if persisted is None:
                _atomic_receipt_json(receipt_path, durable_receipt)
            registration["MacOSHookSmokeReceipt"] = registration_entry
            next_state = copy.deepcopy(state)
            next_state["verification_supplemental_evidence"] = registration
            next_state["generation"] = expected_generation + 1
            next_state["hook_trust"] = "pending_hook_trust"
            _atomic_json(
                task_store._path(task_context.task_id), next_state
            )
        elif (
            persisted is None
            or registration.get("MacOSHookSmokeReceipt")
            != registration_entry
            or state.get("hook_trust") != "pending_hook_trust"
        ):
            raise ValueError(
                "E_MACOS_SMOKE_CAS: partial publication is inconsistent"
            )
        if not _consume_runtime_host_object(
            completed, "completed_macos_hook_smoke"
        ) or not _consume_runtime_host_object(
            task_context, "verification_task_context"
        ):
            raise ValueError(
                "E_MACOS_SMOKE_REPLAY: host-bound input was consumed"
            )
        completed._consumed = True
        task_context._consumed = True
    refreshed = _refresh_verification_task_context(
        task_context, expected_generation + 1
    )
    return HookSmokePublicationResult(
        receipt=receipt, task_context=refreshed
    )
