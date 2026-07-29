"""Host-bound observations that cannot be reconstructed from serialized input."""

from __future__ import annotations

from dataclasses import dataclass
import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
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
    snapshotted_bindings = {
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
    }
    issued: dict[
        int, tuple[object, str, tuple[object, ...] | None]
    ] = {}

    def snapshot(value: object, kind: str) -> tuple[object, ...] | None:
        names = snapshotted_bindings.get(kind)
        if names is None:
            return None
        try:
            return tuple(getattr(value, name) for name in names)
        except AttributeError:
            return ()

    def register(value: object, kind: str) -> None:
        issued[id(value)] = (value, kind, snapshot(value, kind))

    def is_live(value: object, kind: str) -> bool:
        entry = issued.get(id(value))
        return (
            entry is not None
            and entry[0] is value
            and entry[1] == kind
            and entry[2] == snapshot(value, kind)
        )

    def consume(value: object, kind: str) -> bool:
        if not is_live(value, kind):
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
    framed.freshness_deadline = now + float(ttl_seconds)
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
        or authorization.scope_paths != tuple(str(item) for item in normalized)
        or authorization.effect != expected_effect
        or authorization.operation_nonce != expected_operation_nonce
        or authorization.invocation_id != expected_invocation_id
    ):
        raise ValueError(
            "E_AUTH_UNTRUSTED_CHANNEL: authorization binding is invalid or stale"
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
            WorktreeInventoryRecord(
                worktree=item.worktree,
                git_dir=str(
                    _resolve_worktree_git_dir(Path(item.worktree), common_dir)
                ),
                head=item.head,
                branch=item.branch,
                detached=item.detached,
            )
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
    except OSError:
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
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
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
    hasher = sha256()
    modules = sorted(runtime.glob("*.py"))
    for path in modules:
        if path.is_symlink() or not path.is_file():
            raise ValueError("E_GOVERNING_RUNTIME: runtime module is invalid")
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    runtime_digest = f"sha256:{hasher.hexdigest()}"
    if not modules or lock.get("digests", {}).get("runtime") != runtime_digest:
        raise ValueError("E_GOVERNING_RUNTIME: runtime digest drifted")
    now = float(clock())
    observation = object.__new__(GoverningRuntimeObservation)
    observation._consumed = False
    values = {
        "runtime_digest": runtime_digest,
        "lock_digest": f"sha256:{sha256(lock_path.read_bytes()).hexdigest()}",
        "policy_digest": f"sha256:{sha256(policy_path.read_bytes()).hexdigest()}",
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
    host_capability: object,
) -> RemoteEffectContext:
    if (
        not isinstance(task, Mapping)
        or validate_task_envelope(task)
        or contract_digest(task) != expected_task_digest
        or type(local_git) is not LocalGitObservation
        or local_git.provider != "git"
        or type(host_capability) is not HostAdapterCapability
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
        or any(item is None or item.endswith("/**") for item in normalized)
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
    _assert_no_external_git_filters(
        worktree, tuple(str(item) for item in normalized)
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
        or context.session_id != session_id
        or context.invocation_id != invocation_id
    ):
        raise ValueError("E_REMOTE_EFFECT: push policy binding is invalid")
    _assert_remote_effect_context_live(
        context, code="E_REMOTE_EFFECT"
    )
    push_url = _git_text(
        Path(context.worktree_identity),
        ["remote", "get-url", "--push", remote],
    )
    if (
        "\n" in push_url
        or re.fullmatch(
            r"https://github\.com/"
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
            push_url,
        )
        is None
        or "@" in push_url
        or "?" in push_url
        or "#" in push_url
    ):
        raise ValueError(
            "E_REMOTE_EFFECT: push URL requires credential-free github.com HTTPS"
        )
    _assert_no_unsafe_transport_config(Path(context.worktree_identity))
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
    push_returncode, _ = _execute_native_remote(
        "git_feature_push",
        tuple(
            _closed_git_argv(
                context.worktree_identity,
                [
                    "push",
                    remote,
                    (
                        f"refs/heads/{context.branch}:"
                        f"refs/heads/{context.branch}"
                    ),
                ],
            )
        ),
        max_output_bytes=0,
    )
    remote_returncode, remote_output = _execute_native_remote(
        "git_feature_observe",
        tuple(
            _closed_git_argv(
                context.worktree_identity,
                [
                    "ls-remote",
                    "--heads",
                    remote,
                    f"refs/heads/{context.branch}",
                ],
            )
        ),
        max_output_bytes=4096,
    )
    expected_remote_line = (
        f"{context.head}\trefs/heads/{context.branch}\n".encode("utf-8")
    )
    if (
        push_returncode != 0
        or remote_returncode != 0
        or remote_output != expected_remote_line
        or _git_text(
            Path(context.worktree_identity), ["rev-parse", "HEAD"]
        )
        != context.head
    ):
        raise ValueError("E_REMOTE_EFFECT: feature push was not exact")
    if not _consume_governing_policy(
        governing_policy
    ) or not _consume_governing_runtime_observation(governing_runtime):
        raise ValueError(
            "E_REMOTE_EFFECT: governing runtime or policy is not host-issued"
        )
    governing_policy._consumed = True
    governing_runtime._consumed = True
    if not _consume_runtime_host_object(
        context, "validated_remote_effect_context"
    ):
        raise ValueError(
            "E_REMOTE_EFFECT: remote effect context is not host-issued"
        )
    context._consumed = True
    now = float(clock())
    observation = object.__new__(LocalGitObservation)
    observation.observation_id = f"push-{uuid4().hex}"
    observation.invocation_id = invocation_id
    observation.task_digest = context.task_digest
    observation.repository_identity = context.repository_identity
    observation.worktree_identity = context.worktree_identity
    observation.branch = context.branch
    observation.prior_head = context.head
    observation.target_state = "pushed"
    observation.session_id = session_id
    observation.provider = "git"
    observation.subject_digest = context.context_digest
    observation.evidence = {"remote_head": context.head}
    observation.observed_at_monotonic = now
    observation.freshness_deadline = now + 30
    _register_runtime_host_object(observation, "local_git_observation")
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
        or native_provider_event.repository != expected_repository
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
            expected_repository,
            "--json",
            "nameWithOwner",
        ),
        max_output_bytes=4096,
    )
    try:
        repository_payload = json.loads(raw_repository)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        repository_payload = {}
    if (
        repository_returncode != 0
        or repository_payload.get("nameWithOwner") != expected_repository
    ):
        raise ValueError(
            "E_GITHUB_PR_PROVIDER: exact repository access is not ready"
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
    provider.repository = native_provider_event.repository
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
    request = object.__new__(ValidatedPullRequestMutationRequest)
    request._consumed = False
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


def execute_pull_request_mutation(
    request: object, *, clock: Callable[[], float]
) -> PullRequestMutationObservation:
    if (
        type(request) is not ValidatedPullRequestMutationRequest
        or not _runtime_host_object_is_live(
            request, "pr_mutation_request"
        )
        or request._consumed
    ):
        raise ValueError("E_PR_MUTATION: typed request is required")
    context = request.context
    repository = request.provider.repository
    base = request.provider.base_branch
    _assert_remote_effect_context_live(context, code="E_PR_MUTATION")
    if (
        not isinstance(base, str)
        or not base
        or not isinstance(context.expected_base_sha, str)
        or _GIT_OBJECT_ID.fullmatch(context.expected_base_sha) is None
    ):
        raise ValueError("E_PR_MUTATION: base binding is required")
    if request.expected_pr_number is None:
        arguments = (
            "gh",
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            base,
            "--head",
            context.branch,
            "--title",
            request.title.value,
            "--body",
            request.body.value,
        )
        if request.draft:
            arguments = (*arguments, "--draft")
    else:
        arguments = (
            "gh",
            "pr",
            "edit",
            str(request.expected_pr_number),
            "--repo",
            repository,
            "--title",
            request.title.value,
            "--body",
            request.body.value,
        )
    mutation_returncode, _ = _execute_native_remote(
        "github_pull_request_mutation",
        arguments,
        max_output_bytes=0,
    )
    if mutation_returncode != 0:
        raise ValueError("E_PR_MUTATION: provider mutation failed")
    selector = (
        str(request.expected_pr_number)
        if request.expected_pr_number is not None
        else context.branch
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
        raise ValueError("E_PR_MUTATION: provider observation failed") from error
    if observed_returncode != 0:
        raise ValueError("E_PR_MUTATION: provider observation failed")
    if not _consume_runtime_host_object(
        request, "pr_mutation_request"
    ) or not _consume_runtime_host_object(
        context, "pr_request_context"
    ):
        raise ValueError("E_PR_MUTATION: request binding is not host-issued")
    request._consumed = True
    now = float(clock())
    observation = object.__new__(PullRequestMutationObservation)
    observation._consumed = False
    observation.repository = repository
    observation.base = str(payload["baseRefName"])
    observation.head_branch = str(payload["headRefName"])
    observation.head_sha = str(payload["headRefOid"])
    observation.number = int(payload["number"])
    observation.url = str(payload["url"])
    observation.draft = bool(payload["isDraft"])
    observation.session_id = request.session_id
    observation.invocation_id = request.invocation_id
    observation.freshness_deadline = now + 30
    _register_runtime_host_object(
        observation, "pull_request_mutation_observation"
    )
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
    if (
        type(observation) is not PullRequestMutationObservation
        or not _runtime_host_object_is_live(
            observation, "pull_request_mutation_observation"
        )
        or observation._consumed
        or observation.repository != expected_repository
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
