"""Host-bound observations that cannot be reconstructed from serialized input."""

from __future__ import annotations

from dataclasses import dataclass
import copy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import subprocess
import tempfile
import threading
import tomllib
from typing import Callable, Mapping
from uuid import uuid4

from control_plane.contracts import (
    SHA256_DIGEST,
    TASK_EFFECTS,
    contract_digest,
    validate_task_id,
    validate_task_envelope,
)
from control_plane.resource_registry import (
    build_inventory,
    registry_contract_digest,
    validate_inventory,
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
_clarification_repository_inspector_validator: Callable[[object], bool] = (
    lambda _: False
)


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
        "clarification_repository_observation": (
            "observation_id",
            "task_digest",
            "session_id",
            "repository_identity",
            "worktree_identity",
            "branch",
            "head",
            "question_digest",
            "invocation_id",
            "status",
            "evidence_digest",
            "freshness_deadline",
        ),
        "validated_clarification_repository_observation": (
            "observation_id",
            "task_digest",
            "session_id",
            "repository_identity",
            "worktree_identity",
            "branch",
            "head",
            "question_digest",
            "invocation_id",
            "status",
            "evidence_digest",
            "freshness_deadline",
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
        "framed_clarification_issue": (
            "payload_digest",
            "provenance",
            "task_digest",
            "session_id",
            "invocation_id",
        ),
        "framed_clarification_prompt_view": (
            "payload_digest",
            "presentation_digest",
            "issue_id",
            "task_digest",
            "session_id",
            "question_digest",
            "invocation_id",
            "freshness_deadline",
        ),
        "validated_clarification_request": (
            "request_digest",
            "task_digest",
            "session_id",
            "invocation_id",
            "provenance",
        ),
        "validated_assumption": ("provenance",),
        "trusted_interaction": (
            "request_digest",
            "task_digest",
            "session_id",
            "invocation_id",
            "freshness_deadline",
        ),
        "trusted_irreversible_confirmation": (
            "payload_digest",
            "authorization_id",
            "operation_nonce",
            "repository_identity",
            "worktree_identity",
            "branch",
            "expected_head",
            "subject_digest",
            "invocation_id",
            "issued_at_monotonic",
            "expires_at_monotonic",
            "freshness_deadline",
        ),
        "host_context_metrics": (
            "payload_digest",
            "task_digest",
            "session_id",
            "invocation_id",
            "subject_digest",
            "freshness_deadline",
        ),
    }
    issued: dict[
        int, tuple[object, str, tuple[object, ...] | None]
    ] = {}
    registry_lock = threading.RLock()

    def snapshot(value: object, kind: str) -> tuple[object, ...] | None:
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
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "HostAdapterCapability":
        raise TypeError("HostAdapterCapability is host-bound")


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


class FramedClarificationIssue:
    __slots__ = (
        "_consumed",
        "payload",
        "payload_digest",
        "provenance",
        "task_digest",
        "session_id",
        "invocation_id",
    )

    def __new__(cls, *_: object, **__: object) -> "FramedClarificationIssue":
        raise TypeError("FramedClarificationIssue is host-bound")


class FramedClarificationPromptView:
    __slots__ = (
        "_consumed",
        "payload",
        "payload_digest",
        "presentation_digest",
        "issue_id",
        "task_digest",
        "session_id",
        "question_digest",
        "invocation_id",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "FramedClarificationPromptView":
        raise TypeError("FramedClarificationPromptView is host-bound")


class ClarificationRepositoryObservation:
    __slots__ = (
        "_consumed",
        "observation_id",
        "task_digest",
        "session_id",
        "repository_identity",
        "worktree_identity",
        "branch",
        "head",
        "question_digest",
        "invocation_id",
        "status",
        "evidence_digest",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ClarificationRepositoryObservation":
        raise TypeError("ClarificationRepositoryObservation is host-bound")


class ValidatedClarificationRepositoryObservation(
    ClarificationRepositoryObservation
):
    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedClarificationRepositoryObservation":
        raise TypeError(
            "ValidatedClarificationRepositoryObservation is host-bound"
        )


class ValidatedClarificationRequest:
    __slots__ = (
        "_consumed",
        "payload",
        "request_digest",
        "task_digest",
        "session_id",
        "invocation_id",
        "provenance",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedClarificationRequest":
        raise TypeError("ValidatedClarificationRequest is host-bound")


class ValidatedAssumption:
    __slots__ = ("payload", "provenance")

    def __new__(cls, *_: object, **__: object) -> "ValidatedAssumption":
        raise TypeError("ValidatedAssumption is host-bound")


class TrustedInteraction:
    __slots__ = (
        "_consumed",
        "payload",
        "request_digest",
        "task_digest",
        "session_id",
        "invocation_id",
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "TrustedInteraction":
        raise TypeError("TrustedInteraction is host-bound")


class TrustedIrreversibleConfirmation:
    __slots__ = (
        "_consumed",
        "payload",
        "payload_digest",
        "authorization_id",
        "operation_nonce",
        "repository_identity",
        "worktree_identity",
        "branch",
        "expected_head",
        "subject_digest",
        "invocation_id",
        "issued_at_monotonic",
        "expires_at_monotonic",
        "freshness_deadline",
    )

    def __new__(
        cls, *_: object, **__: object
    ) -> "TrustedIrreversibleConfirmation":
        raise TypeError("TrustedIrreversibleConfirmation is host-bound")


class HostContextMetrics:
    __slots__ = (
        "_consumed",
        "payload",
        "payload_digest",
        "task_digest",
        "session_id",
        "invocation_id",
        "subject_digest",
        "freshness_deadline",
    )

    def __new__(cls, *_: object, **__: object) -> "HostContextMetrics":
        raise TypeError("HostContextMetrics is host-bound")


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
    completed = subprocess.run(
        _closed_git_argv(worktree, arguments),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=_sanitized_git_environment(),
    )
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
    status = subprocess.run(
        _closed_git_argv(
            worktree,
            [
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            ],
        ),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_sanitized_git_environment(),
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


@dataclass(frozen=True)
class ConsumedEffectCapabilities:
    authorization_id: str
    confirmation_id: str
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
    capability.freshness_deadline = now + float(ttl_seconds)
    _register_runtime_host_object(capability, "host_capability")
    return capability


def _assert_live_clarification_capability(
    capability: object,
    *,
    task_digest: str,
    session_id: str,
    invocation_id: str,
) -> HostAdapterCapability:
    if (
        type(capability) is not HostAdapterCapability
        or not _runtime_host_object_is_live(capability, "host_capability")
        or capability._consumed
        or float(capability._clock()) > capability.freshness_deadline
        or capability.session_id != session_id
        or capability.invocation_id != invocation_id
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or not validate_task_id(session_id)
        or not validate_task_id(invocation_id)
    ):
        raise ValueError(
            "C_UNTRUSTED_REQUEST: live host capability binding is required"
        )
    return capability


def frame_clarification_issue(
    issue_draft: Mapping[str, object],
    *,
    task_digest: str,
    session_id: str,
    invocation_id: str,
    host_capability: object,
    native_user_event: object | None = None,
) -> FramedClarificationIssue:
    """Frame one closed issue while deriving provenance outside its payload."""

    from control_plane.clarification import validate_clarification_issue_draft

    _assert_live_clarification_capability(
        host_capability,
        task_digest=task_digest,
        session_id=session_id,
        invocation_id=invocation_id,
    )
    issues = validate_clarification_issue_draft(issue_draft)
    if issues:
        raise ValueError(f"{issues[0].code}: invalid clarification issue")
    provenance = "model_inference"
    if native_user_event is not None:
        if (
            type(native_user_event) is not NativeUserInteractionEvent
            or not _native_host_object_is_valid(
                native_user_event, "user_interaction"
            )
            or native_user_event._consumed
            or native_user_event.session_id != session_id
            or native_user_event.invocation_id != invocation_id
            or native_user_event.task_digest != task_digest
            or native_user_event.subject_digest
            != issue_draft.get("question_digest")
        ):
            raise ValueError(
                "C_UNTRUSTED_ISSUE: native user issue binding is invalid"
            )
        native_user_event._consumed = True
        provenance = "user_explicit"
    framed = object.__new__(FramedClarificationIssue)
    framed._consumed = False
    framed.payload = copy.deepcopy(dict(issue_draft))
    framed.payload_digest = contract_digest(framed.payload)
    framed.provenance = provenance
    framed.task_digest = task_digest
    framed.session_id = session_id
    framed.invocation_id = invocation_id
    _register_runtime_host_object(framed, "framed_clarification_issue")
    return framed


def frame_clarification_prompt_view(
    draft: Mapping[str, object],
    *,
    issue: object,
    task_digest: str,
    session_id: str,
    invocation_id: str,
    host_capability: object,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> FramedClarificationPromptView:
    """Sanitize and bind one display-only prompt view for the current callback."""

    from control_plane.clarification import (
        _request_id,
        validate_clarification_prompt_view_draft,
    )

    _assert_live_clarification_capability(
        host_capability,
        task_digest=task_digest,
        session_id=session_id,
        invocation_id=invocation_id,
    )
    if (
        type(issue) is not FramedClarificationIssue
        or not _runtime_host_object_is_live(
            issue, "framed_clarification_issue"
        )
        or issue._consumed
        or issue.task_digest != task_digest
        or issue.session_id != session_id
        or issue.invocation_id != invocation_id
        or contract_digest(issue.payload) != issue.payload_digest
    ):
        raise ValueError("C_UNTRUSTED_ISSUE: framed issue is required")
    issues = validate_clarification_prompt_view_draft(
        draft, issue=issue.payload
    )
    if issues:
        raise ValueError(f"{issues[0].code}: invalid prompt view")
    if (
        not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError("C_PRESENTATION_UNAVAILABLE: invalid prompt TTL")
    payload = {
        "schema_version": 1,
        "request_id": _request_id(
            task_digest=task_digest,
            session_id=session_id,
            issue_id=str(issue.payload["issue_id"]),
            question_digest=str(issue.payload["question_digest"]),
        ),
        "question_text": str(draft["question_text"]),
        "options": copy.deepcopy(draft["options"]),
        "recommended_option_id": str(draft["recommended_option_id"]),
        "consequence_text": str(draft["consequence_text"]),
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > 1024:
        raise ValueError(
            "C_PRESENTATION_UNAVAILABLE: prompt view exceeds 1 KiB"
        )
    framed = object.__new__(FramedClarificationPromptView)
    framed._consumed = False
    framed.payload = payload
    framed.payload_digest = contract_digest(payload)
    framed.presentation_digest = framed.payload_digest
    framed.issue_id = str(issue.payload["issue_id"])
    framed.task_digest = task_digest
    framed.session_id = session_id
    framed.question_digest = str(issue.payload["question_digest"])
    framed.invocation_id = invocation_id
    framed.freshness_deadline = float(clock()) + float(ttl_seconds)
    _register_runtime_host_object(
        framed, "framed_clarification_prompt_view"
    )
    return framed


def observe_clarification_repository(
    *,
    task_digest: str,
    session_id: str,
    repository_identity: Path | str,
    worktree_identity: Path | str,
    branch: str,
    head: str,
    question_digest: str,
    invocation_id: str,
    inspector: object,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> ClarificationRepositoryObservation:
    """Run the host-selected, bounded, no-egress repository inspector."""

    from control_plane.clarification import RepositoryEvidenceFacts

    repository = _canonical_directory(
        repository_identity, code="C_REPOSITORY_OBSERVATION_UNTRUSTED"
    )
    worktree = _canonical_directory(
        worktree_identity, code="C_REPOSITORY_OBSERVATION_UNTRUSTED"
    )
    if (
        not _clarification_repository_inspector_validator(inspector)
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or SHA256_DIGEST.fullmatch(question_digest) is None
        or not validate_task_id(session_id)
        or not validate_task_id(invocation_id)
        or not isinstance(branch, str)
        or not branch
        or _GIT_OBJECT_ID.fullmatch(head) is None
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_UNTRUSTED: inspector binding is invalid"
        )
    try:
        facts = inspector.inspect(
            canonical_root=repository,
            question_digest=question_digest,
            max_files=32,
            max_bytes=65536,
        )
    except Exception as error:
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_UNTRUSTED: inspector failed"
        ) from error
    if (
        type(facts) is not RepositoryEvidenceFacts
        or facts.status not in {"resolved", "unresolved", "conflicting"}
        or not isinstance(facts.evidence_items, tuple)
        or not facts.evidence_items
        or len(facts.evidence_items) > 32
    ):
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_UNTRUSTED: inspector output is invalid"
        )
    total_bytes = 0
    evidence_items: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for item in facts.evidence_items:
        if not isinstance(item, str) or not item or item in seen_paths:
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_UNTRUSTED: evidence path is invalid"
            )
        try:
            candidate = (repository / item).resolve(strict=True)
            candidate.relative_to(repository)
        except (OSError, RuntimeError, ValueError) as error:
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_UNTRUSTED: evidence escaped root"
            ) from error
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(candidate, flags)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError(
                        "C_REPOSITORY_OBSERVATION_UNTRUSTED: "
                        "evidence must be a regular file"
                    )
                remaining = 65536 - total_bytes
                chunks: list[bytes] = []
                bytes_left = remaining + 1
                while bytes_left:
                    chunk = os.read(descriptor, min(bytes_left, 65536))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    bytes_left -= len(chunk)
                content = b"".join(chunks)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_UNTRUSTED: evidence file is unsafe"
            ) from error
        if len(content) > remaining:
            raise ValueError(
                "C_REPOSITORY_OBSERVATION_UNTRUSTED: evidence exceeded limit"
            )
        total_bytes += len(content)
        seen_paths.add(item)
        evidence_items.append(
            {
                "path": item,
                "content_digest": f"sha256:{sha256(content).hexdigest()}",
            }
        )
    evidence_digest = contract_digest(
        {
            "status": facts.status,
            "evidence_items": sorted(
                evidence_items, key=lambda evidence: evidence["path"]
            ),
            "question_digest": question_digest,
        }
    )
    observation = object.__new__(ClarificationRepositoryObservation)
    observation._consumed = False
    observation.observation_id = f"clarification-repository-{uuid4().hex}"
    observation.task_digest = task_digest
    observation.session_id = session_id
    observation.repository_identity = str(repository)
    observation.worktree_identity = str(worktree)
    observation.branch = branch
    observation.head = head
    observation.question_digest = question_digest
    observation.invocation_id = invocation_id
    observation.status = facts.status
    observation.evidence_digest = evidence_digest
    observation.freshness_deadline = float(clock()) + float(ttl_seconds)
    _register_runtime_host_object(
        observation, "clarification_repository_observation"
    )
    return observation


def validate_clarification_repository_observation(
    observation: object,
    *,
    expected_task_digest: str,
    expected_session_id: str,
    expected_repository_identity: Path | str,
    expected_worktree_identity: Path | str,
    expected_branch: str,
    expected_head: str,
    expected_question_digest: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ValidatedClarificationRepositoryObservation:
    """Consume a fresh repository observation and return its opaque validation."""

    if type(observation) is not ClarificationRepositoryObservation:
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_UNTRUSTED: host observation required"
        )
    if observation._consumed:
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_REPLAY: observation was consumed"
        )
    if not _runtime_host_object_is_live(
        observation, "clarification_repository_observation"
    ):
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_UNTRUSTED: host observation required"
        )
    if float(clock()) > observation.freshness_deadline:
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_STALE: observation expired"
        )
    repository = _canonical_directory(
        expected_repository_identity, code="C_REPOSITORY_OBSERVATION_BINDING"
    )
    worktree = _canonical_directory(
        expected_worktree_identity, code="C_REPOSITORY_OBSERVATION_BINDING"
    )
    if (
        observation.task_digest != expected_task_digest
        or observation.session_id != expected_session_id
        or observation.repository_identity != str(repository)
        or observation.worktree_identity != str(worktree)
        or observation.branch != expected_branch
        or observation.head != expected_head
        or observation.question_digest != expected_question_digest
        or observation.invocation_id != expected_invocation_id
    ):
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_BINDING: observation binding mismatch"
        )
    if not _consume_runtime_host_object(
        observation, "clarification_repository_observation"
    ):
        raise ValueError(
            "C_REPOSITORY_OBSERVATION_REPLAY: observation was consumed"
        )
    observation._consumed = True
    validated = object.__new__(ValidatedClarificationRepositoryObservation)
    for name in ClarificationRepositoryObservation.__slots__:
        setattr(validated, name, getattr(observation, name))
    validated._consumed = False
    _register_runtime_host_object(
        validated, "validated_clarification_repository_observation"
    )
    return validated


def frame_irreversible_confirmation(
    confirmation_request: Mapping[str, object],
    *,
    native_user_event: object,
    host_capability: object,
    repository_identity: Path | str,
    worktree_identity: Path | str,
    branch: str,
    expected_head: str,
    subject_digest: str,
    authorization_id: str,
    operation_nonce: str,
    invocation_id: str,
    clock: Callable[[], float],
    ttl_seconds: float,
) -> TrustedIrreversibleConfirmation:
    """Wrap one exact irreversible consequence without granting authority."""

    from control_plane.clarification import _CONFIRMATION_KEYS

    task_digest = confirmation_request.get("task_digest")
    session_id = confirmation_request.get("session_id")
    if not isinstance(task_digest, str) or not isinstance(session_id, str):
        raise ValueError("I_SCHEMA: invalid confirmation request")
    _assert_live_clarification_capability(
        host_capability,
        task_digest=task_digest,
        session_id=session_id,
        invocation_id=invocation_id,
    )
    repository = _canonical_directory(
        repository_identity, code="I_UNTRUSTED_CHANNEL"
    )
    worktree = _canonical_directory(
        worktree_identity, code="I_UNTRUSTED_CHANNEL"
    )
    scope_paths = confirmation_request.get("scope_paths")
    normalized = (
        tuple(normalize_scope(item) for item in scope_paths)
        if isinstance(scope_paths, list)
        else ()
    )
    if (
        set(confirmation_request) != _CONFIRMATION_KEYS
        or confirmation_request.get("schema_version") != 1
        or not validate_task_id(confirmation_request.get("confirmation_id"))
        or SHA256_DIGEST.fullmatch(
            str(confirmation_request.get("request_digest"))
        )
        is None
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or not validate_task_id(session_id)
        or any(item is None for item in normalized)
        or not normalized
        or len(set(normalized)) != len(normalized)
        or confirmation_request.get("effect") not in TASK_EFFECTS
        or SHA256_DIGEST.fullmatch(
            str(confirmation_request.get("consequence_digest"))
        )
        is None
        or type(native_user_event) is not NativeUserInteractionEvent
        or not _native_host_object_is_valid(
            native_user_event, "user_interaction"
        )
        or native_user_event._consumed
        or native_user_event.session_id != session_id
        or native_user_event.invocation_id != invocation_id
        or native_user_event.task_digest != task_digest
        or native_user_event.subject_digest != subject_digest
        or _GIT_OBJECT_ID.fullmatch(expected_head) is None
        or not validate_task_id(authorization_id)
        or not validate_task_id(operation_nonce)
        or not isinstance(ttl_seconds, (int, float))
        or isinstance(ttl_seconds, bool)
        or not 0 < float(ttl_seconds) <= 300
    ):
        raise ValueError("I_UNTRUSTED_CHANNEL: confirmation binding is invalid")
    if not _consume_runtime_host_object(
        host_capability, "host_capability"
    ):
        raise ValueError("I_UNTRUSTED_CHANNEL: host capability is not issued")
    host_capability._consumed = True
    native_user_event._consumed = True
    payload = copy.deepcopy(dict(confirmation_request))
    payload["scope_paths"] = [str(item) for item in normalized]
    confirmation = object.__new__(TrustedIrreversibleConfirmation)
    confirmation._consumed = False
    confirmation.payload = payload
    confirmation.payload_digest = contract_digest(payload)
    confirmation.authorization_id = authorization_id
    confirmation.operation_nonce = operation_nonce
    confirmation.repository_identity = str(repository)
    confirmation.worktree_identity = str(worktree)
    confirmation.branch = branch
    confirmation.expected_head = expected_head
    confirmation.subject_digest = subject_digest
    confirmation.invocation_id = invocation_id
    confirmation.issued_at_monotonic = float(clock())
    confirmation.expires_at_monotonic = (
        confirmation.issued_at_monotonic + float(ttl_seconds)
    )
    confirmation.freshness_deadline = confirmation.expires_at_monotonic
    _register_runtime_host_object(
        confirmation, "trusted_irreversible_confirmation"
    )
    return confirmation


def frame_host_context_metrics(
    *,
    task_digest: str,
    session_id: str,
    invocation_id: str,
    subject_digest: str,
    required_resource_bytes: int | None,
    recommended_resource_bytes: int | None,
    worker_id: str,
    retry_count: int,
    started_at_monotonic: float,
    ended_at_monotonic: float,
    tool_use_id: str | None,
    host_capability: object,
) -> HostContextMetrics:
    """Frame host-only resource, worker, retry and timing measurements."""

    _assert_live_clarification_capability(
        host_capability,
        task_digest=task_digest,
        session_id=session_id,
        invocation_id=invocation_id,
    )
    numeric_bytes = (required_resource_bytes, recommended_resource_bytes)
    if (
        SHA256_DIGEST.fullmatch(subject_digest) is None
        or any(
            value is not None
            and (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            )
            for value in numeric_bytes
        )
        or not validate_task_id(worker_id)
        or not isinstance(retry_count, int)
        or isinstance(retry_count, bool)
        or retry_count < 0
        or not isinstance(started_at_monotonic, (int, float))
        or isinstance(started_at_monotonic, bool)
        or not isinstance(ended_at_monotonic, (int, float))
        or isinstance(ended_at_monotonic, bool)
        or not math.isfinite(float(started_at_monotonic))
        or not math.isfinite(float(ended_at_monotonic))
        or float(started_at_monotonic) < 0
        or float(ended_at_monotonic) < float(started_at_monotonic)
        or (tool_use_id is not None and not validate_task_id(tool_use_id))
    ):
        raise ValueError("M_METRIC_BINDING: host metrics are invalid")
    if not _consume_runtime_host_object(
        host_capability, "host_capability"
    ):
        raise ValueError(
            "M_METRIC_UNTRUSTED_CHANNEL: host capability is not issued"
        )
    host_capability._consumed = True
    payload = {
        "required_resource_bytes": required_resource_bytes,
        "recommended_resource_bytes": recommended_resource_bytes,
        "worker_id": worker_id,
        "retry_count": retry_count,
        "started_at_monotonic": float(started_at_monotonic),
        "ended_at_monotonic": float(ended_at_monotonic),
        "tool_use_id": tool_use_id,
    }
    metrics = object.__new__(HostContextMetrics)
    metrics._consumed = False
    metrics.payload = payload
    metrics.payload_digest = contract_digest(payload)
    metrics.task_digest = task_digest
    metrics.session_id = session_id
    metrics.invocation_id = invocation_id
    metrics.subject_digest = subject_digest
    metrics.freshness_deadline = host_capability.freshness_deadline
    _register_runtime_host_object(metrics, "host_context_metrics")
    return metrics


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


def consume_effect_capabilities(
    authorization: object,
    confirmation: object,
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
    expected_consequence_digest: str,
    expected_operation_nonce: str,
    expected_invocation_id: str,
    clock: Callable[[], float],
) -> ConsumedEffectCapabilities:
    """Validate and consume authorization plus confirmation as one operation."""

    from control_plane.clarification import (
        validate_authorization,
        validate_irreversible_confirmation,
    )

    repository = _canonical_directory(
        expected_repository_identity, code="Z_BINDING"
    )
    worktree = _canonical_directory(
        expected_worktree_identity, code="Z_BINDING"
    )
    with _CAPABILITY_CONSUMPTION_LOCK:
        authorization_issues = validate_authorization(
            authorization,
            task_digest=expected_task_digest,
            session_id=expected_session_id,
            repository_identity=repository,
            worktree_identity=worktree,
            branch=expected_branch,
            expected_head=expected_head,
            subject_digest=expected_subject_digest,
            scope_paths=expected_scope_paths,
            effect=expected_effect,
            operation_nonce=expected_operation_nonce,
            invocation_id=expected_invocation_id,
            now_monotonic=float(clock()),
        )
        if authorization_issues:
            raise ValueError(
                f"{authorization_issues[0].code}: "
                f"{authorization_issues[0].message}"
            )
        confirmation_issues = validate_irreversible_confirmation(
            confirmation,
            request_digest=expected_subject_digest,
            task_digest=expected_task_digest,
            session_id=expected_session_id,
            repository_identity=repository,
            worktree_identity=worktree,
            branch=expected_branch,
            expected_head=expected_head,
            subject_digest=expected_subject_digest,
            scope_paths=expected_scope_paths,
            effect=expected_effect,
            expected_consequence_digest=expected_consequence_digest,
            authorization_id=authorization.authorization_id,
            operation_nonce=expected_operation_nonce,
            invocation_id=expected_invocation_id,
            now_monotonic=float(clock()),
        )
        if confirmation_issues:
            raise ValueError(
                f"{confirmation_issues[0].code}: "
                f"{confirmation_issues[0].message}"
            )
        _claim_capability_consumption(
            worktree=worktree,
            authorization_id=authorization.authorization_id,
            operation_nonce=expected_operation_nonce,
            confirmation_id=str(
                confirmation.payload["confirmation_id"]
            ),
        )
        if not _consume_runtime_host_object(
            authorization, "trusted_authorization"
        ) or not _consume_runtime_host_object(
            confirmation, "trusted_irreversible_confirmation"
        ):
            raise ValueError(
                "Z_REPLAY: effect capabilities were already consumed"
            )
        authorization._consumed = True
        confirmation._consumed = True
    return ConsumedEffectCapabilities(
        authorization_id=authorization.authorization_id,
        confirmation_id=str(confirmation.payload["confirmation_id"]),
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
        _closed_git_argv(worktree, arguments),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_sanitized_git_environment(),
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
    completed = subprocess.run(
        [
            "git",
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
    if (
        type(inventory) is not ValidatedWorktreeInventoryObservation
        or inventory._consumed
        or inventory.common_git_dir != str(expected_common_git_dir.resolve())
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
    status = subprocess.run(
        [
            "git",
            "-C",
            str(target),
            "status",
            "--porcelain=v2",
            "-z",
            "--untracked-files=all",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_sanitized_git_environment(),
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


def _assert_no_external_git_filters(
    worktree: Path, paths: tuple[str, ...]
) -> None:
    completed = subprocess.run(
        _closed_git_argv(
            worktree, ["check-attr", "-z", "filter", "--", *paths]
        ),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_sanitized_git_environment(),
    )
    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if (
        completed.returncode != 0
        or len(fields) != len(paths) * 3
        or any(fields[index + 1] != b"filter" for index in range(0, len(fields), 3))
    ):
        raise ValueError(
            "E_GIT_FILTER: clean-filter inventory is incomplete"
        )
    if any(
        fields[index + 2] not in {b"unspecified", b"unset"}
        for index in range(0, len(fields), 3)
    ):
        raise ValueError(
            "E_GIT_FILTER: external clean filters are not permitted"
        )


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
                _closed_git_argv(
                    worktree,
                    ["ls-files", "--error-unmatch", "--", relative],
                ),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_sanitized_git_environment(),
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
            _closed_git_argv(
                worktree,
                ["config", scope, "--name-only", "--null", "--list"],
            ),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_sanitized_git_environment(),
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
) -> tuple[Path, Mapping[str, object]]:
    if (
        type(governing_runtime) is not GoverningRuntimeObservation
        or not _governing_runtime_observation_is_live(governing_runtime)
        or governing_runtime._consumed
        or float(clock()) > governing_runtime.freshness_deadline
        or not isinstance(task_context, Mapping)
        or not isinstance(
            inventory, ValidatedWorktreeInventoryObservation
        )
        or inventory._consumed
        or not isinstance(lease, Mapping)
        or not isinstance(authorization, TrustedAuthorization)
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
        / "leases"
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
    if (
        task_path.is_symlink()
        or lease_path.is_symlink()
        or not isinstance(live_task, Mapping)
        or not isinstance(live_lease, Mapping)
        or live_task.get("task_id") != task_id
        or live_task.get("task_digest") != task_context.get("task_digest")
        or live_task.get("branch") != lease.get("branch")
        or live_task.get("state") != "review_ready"
        or live_task.get("resume_forbidden") is True
        or dict(live_lease) != dict(lease)
        or live_lease.get("lease_digest") != contract_digest(lease_semantic)
        or live_lease.get("lease_digest")
        != task_context.get("lease_digest")
        or live_lease.get("policy_digest")
        != governing_runtime.policy_digest
    ):
        raise ValueError(
            "E_GIT_EFFECT: live task or writer lease binding drifted"
        )
    return worktree, lease


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
    worktree, lease_mapping = _validate_governing_git_effect(
        governing_runtime=governing_runtime,
        task_context=task_context,
        inventory=inventory,
        lease=lease,
        authorization=authorization,
        expected_head=expected_head,
        session_id=session_id,
        invocation_id=invocation_id,
        clock=clock,
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
    _assert_exact_stage_paths(worktree, paths, exact_paths)
    _assert_no_external_git_filters(worktree, exact_paths)
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
        item.decode("utf-8") for item in staged.stdout.split(b"\0") if item
    )
    if completed.returncode != 0 or staged.returncode != 0 or set(
        observed_paths
    ) != set(normalized):
        raise ValueError("E_GIT_EFFECT: staged index is not the allowlist")
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
    worktree, lease_mapping = _validate_governing_git_effect(
        governing_runtime=governing_runtime,
        task_context=task_context,
        inventory=inventory,
        lease=lease,
        authorization=authorization,
        expected_head=expected_prior_head,
        session_id=session_id,
        invocation_id=invocation_id,
        clock=clock,
    )
    if (
        type(index_observation) is not LocalGitIndexObservation
        or index_observation._consumed
        or index_observation.task_digest != task_context.get("task_digest")
        or index_observation.worktree_identity != str(worktree)
        or index_observation.head != expected_prior_head
        or not isinstance(message, str)
        or not 1 <= len(message) <= 200
        or any(ord(character) < 32 for character in message)
        or _git_text(worktree, ["write-tree"])
        != index_observation.index_tree
    ):
        raise ValueError("E_GIT_EFFECT: staged commit binding is invalid")
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
        or _git_text(worktree, ["diff", "--cached", "--name-only"])
    ):
        raise ValueError("E_GIT_EFFECT: commit was not observed exactly")
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
    else:
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
