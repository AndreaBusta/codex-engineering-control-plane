"""In-process Core seals; inert by construction and never remote authority."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterator, Mapping
import weakref

from control_plane.contracts import SHA256_DIGEST, contract_digest
from control_plane.repository import discover_repository, git_common_dir, worktree_git_dir


_REGISTRY_LOCK = Lock()
_RUNTIME_HOST_KINDS = frozenset(
    {
        "governing_policy",
        "trusted_route_decision",
        "validated_inventory",
        "validated_worktree_inventory",
    }
)


@dataclass(frozen=True)
class _RuntimeHostSeal:
    reference: weakref.ReferenceType[object]
    kind: str
    binding_digest: str


_LIVE_OBJECTS: dict[int, _RuntimeHostSeal] = {}


def _runtime_projection(value: object, kind: str) -> dict[str, object]:
    """Copy one closed kind's complete semantic binding for validation/use."""

    if kind == "validated_inventory" and type(value) is ValidatedInventory:
        return {
            "task_digest": value.task_digest,
            "registry_digest": value.registry_digest,
            "snapshot_digest": value.snapshot_digest,
            "snapshot": copy.deepcopy(value._snapshot),
        }
    if kind == "trusted_route_decision" and type(value) is TrustedRouteDecision:
        return {
            "decision_digest": value.decision_digest,
            "payload": copy.deepcopy(value._payload),
        }
    if kind == "validated_worktree_inventory" and (
        type(value) is ValidatedWorktreeInventoryObservation
    ):
        return {
            "consumed": value._consumed,
            "common_git_dir": value.common_git_dir,
            "records": tuple(copy.deepcopy(value.records)),
        }
    if kind == "governing_policy":
        from control_plane.policy import GoverningPolicy

        if type(value) is GoverningPolicy:
            return {
                "policy": copy.deepcopy(value.policy),
                "policy_digest": value.policy_digest,
                "runtime_digest": value.runtime_digest,
                "lock_digest": value.lock_digest,
                "governing_base_commit": value.governing_base_commit,
                "remote_repository": value.remote_repository,
            }
    raise ValueError("E_RUNTIME_BINDING: runtime object kind is invalid")


def _projection_binding(kind: str, projection: Mapping[str, object]) -> str:
    """Normalize a typed projection into one canonical, domain-bound digest."""

    semantic = dict(projection)
    if kind == "validated_worktree_inventory":
        records = semantic.get("records")
        if not isinstance(records, tuple) or not all(
            type(record) is WorktreeRecord for record in records
        ):
            raise ValueError("E_RUNTIME_BINDING: worktree records are invalid")
        semantic["records"] = [
            {"worktree": record.worktree, "git_dir": record.git_dir}
            for record in records
        ]
    return contract_digest({"kind": kind, "binding": semantic})


def _remove_dead_runtime_host_object(
    reference: weakref.ReferenceType[object],
    *,
    object_id: int,
) -> None:
    with _REGISTRY_LOCK:
        observed = _LIVE_OBJECTS.get(object_id)
        if observed is not None and observed.reference is reference:
            _LIVE_OBJECTS.pop(object_id, None)


def _register_runtime_host_object(value: object, kind: str) -> None:
    """Register an opaque object created by a Core observation boundary."""

    if kind not in _RUNTIME_HOST_KINDS:
        raise ValueError("E_RUNTIME_BINDING: runtime object kind is invalid")
    try:
        projection = _runtime_projection(value, kind)
        binding_digest = _projection_binding(kind, projection)
        object_id = id(value)
        reference = weakref.ref(
            value,
            lambda dead, object_id=object_id: _remove_dead_runtime_host_object(
                dead,
                object_id=object_id,
            ),
        )
    except (AttributeError, RecursionError, TypeError, ValueError) as error:
        raise ValueError(
            "E_RUNTIME_BINDING: runtime object cannot be sealed"
        ) from error
    with _REGISTRY_LOCK:
        observed = _LIVE_OBJECTS.get(object_id)
        if observed is not None and observed.reference() is not None:
            raise ValueError(
                "E_RUNTIME_BINDING: runtime object is already registered"
            )
        _LIVE_OBJECTS[object_id] = _RuntimeHostSeal(
            reference=reference,
            kind=kind,
            binding_digest=binding_digest,
        )


def _copy_live_runtime_host_object(
    value: object,
    kind: str,
) -> dict[str, object] | None:
    """Validate and return the exact copied projection under the registry lock."""

    with _REGISTRY_LOCK:
        observed = _LIVE_OBJECTS.get(id(value))
        if (
            observed is None
            or observed.reference() is not value
            or observed.kind != kind
        ):
            return None
        try:
            projection = _runtime_projection(value, kind)
            if observed.binding_digest != _projection_binding(kind, projection):
                return None
        except (AttributeError, RecursionError, TypeError, ValueError):
            return None
        return projection


def _runtime_host_object_is_live(value: object, kind: str) -> bool:
    return _copy_live_runtime_host_object(value, kind) is not None


def _consume_runtime_host_object_snapshot(
    value: object,
    kind: str,
) -> dict[str, object] | None:
    """Atomically validate, project, and consume one registered object."""

    with _REGISTRY_LOCK:
        observed = _LIVE_OBJECTS.get(id(value))
        if (
            observed is None
            or observed.reference() is not value
            or observed.kind != kind
        ):
            return None
        try:
            projection = _runtime_projection(value, kind)
            if observed.binding_digest != _projection_binding(kind, projection):
                return None
        except (AttributeError, RecursionError, TypeError, ValueError):
            return None
        _LIVE_OBJECTS.pop(id(value), None)
        if kind == "validated_worktree_inventory":
            value._consumed = True
        return projection


def _consume_runtime_host_object(value: object, kind: str) -> bool:
    return _consume_runtime_host_object_snapshot(value, kind) is not None


def _native_host_object_is_valid(value: object, kind: str) -> bool:
    return _runtime_host_object_is_live(value, kind)


class HostAdapterUnavailable:
    __slots__ = ()

    def __new__(cls, *_: object, **__: object) -> "HostAdapterUnavailable":
        raise TypeError("HostAdapterUnavailable is a closed singleton")


HOST_ADAPTER_UNAVAILABLE = object.__new__(HostAdapterUnavailable)


class HostAdapterCapability:
    """Reserved host capability. Core never mints one."""

    __slots__ = ("_consumed",)

    def __new__(cls, *_: object, **__: object) -> "HostAdapterCapability":
        raise TypeError("HostAdapterCapability is host-bound")


class NativeUserInteractionEvent:
    """Reserved native event. Serialized input cannot construct one."""

    __slots__ = ("_consumed",)

    def __new__(cls, *_: object, **__: object) -> "NativeUserInteractionEvent":
        raise TypeError("NativeUserInteractionEvent is host-bound")


class TrustedAuthorization:
    """Reserved authorization. Core has no authority-minting adapter."""

    __slots__ = ("_consumed", "effect", "task_digest", "scope_paths")

    def __new__(cls, *_: object, **__: object) -> "TrustedAuthorization":
        raise TypeError("TrustedAuthorization is host-bound")


class ValidatedInventory:
    __slots__ = (
        "_snapshot",
        "task_digest",
        "registry_digest",
        "snapshot_digest",
        "__weakref__",
    )

    def __new__(cls, *_: object, **__: object) -> "ValidatedInventory":
        raise TypeError("ValidatedInventory is emitted only by Core inventory")

    def _snapshot_for_router(
        self, *, expected_task_digest: str, expected_registry_digest: str
    ) -> dict[str, object]:
        projection = _copy_live_runtime_host_object(
            self,
            "validated_inventory",
        )
        if projection is None or (
            projection.get("task_digest") != expected_task_digest
            or projection.get("registry_digest") != expected_registry_digest
        ):
            raise ValueError(
                "E_INVENTORY_OBSERVATION: validated inventory binding mismatch"
            )
        snapshot = projection.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError(
                "E_INVENTORY_OBSERVATION: validated inventory binding mismatch"
            )
        return snapshot


def seal_validated_inventory(
    snapshot: Mapping[str, object],
    *,
    task_digest: str,
    registry_digest: str,
) -> ValidatedInventory:
    """Bind one locally built metadata inventory to one task and registry."""

    supplied = snapshot.get("snapshot_digest")
    unsigned = {key: value for key, value in snapshot.items() if key != "snapshot_digest"}
    if (
        not isinstance(task_digest, str)
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or not isinstance(registry_digest, str)
        or SHA256_DIGEST.fullmatch(registry_digest) is None
        or not isinstance(supplied, str)
        or SHA256_DIGEST.fullmatch(supplied) is None
        or supplied != contract_digest(unsigned)
    ):
        raise ValueError("E_INVENTORY_OBSERVATION: inventory digest is invalid")
    result = object.__new__(ValidatedInventory)
    result._snapshot = copy.deepcopy(dict(snapshot))
    result.task_digest = task_digest
    result.registry_digest = registry_digest
    result.snapshot_digest = supplied
    _register_runtime_host_object(result, "validated_inventory")
    return result


class TrustedRouteDecision(Mapping[str, object]):
    """Opaque immutable route output; it carries no external authority."""

    __slots__ = ("_payload", "decision_digest", "__weakref__")

    def __new__(cls, *_: object, **__: object) -> "TrustedRouteDecision":
        raise TypeError("TrustedRouteDecision is emitted only by the router")

    def _payload_for_diagnostic(self) -> dict[str, object]:
        projection = _copy_live_runtime_host_object(
            self,
            "trusted_route_decision",
        )
        payload = projection.get("payload") if projection is not None else None
        if not isinstance(payload, dict):
            raise ValueError(
                "R_ROUTE_DECISION: trusted route binding mismatch"
            )
        return payload

    def __getitem__(self, key: str) -> object:
        return self._payload_for_diagnostic()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload_for_diagnostic())

    def __len__(self) -> int:
        return len(self._payload_for_diagnostic())

    @property
    def payload(self) -> dict[str, object]:
        return self._payload_for_diagnostic()

    def _payload_for_authority(self) -> dict[str, object]:
        raise ValueError("R_CORE_NO_REMOTE_AUTHORITY: route is diagnostic")


def _seal_trusted_route_decision(
    payload: Mapping[str, object],
) -> TrustedRouteDecision:
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
        raise ValueError("R_ROUTE_DECISION: router attempted to seal an invalid decision")
    decision = object.__new__(TrustedRouteDecision)
    decision._payload = copied
    decision.decision_digest = supplied
    _register_runtime_host_object(decision, "trusted_route_decision")
    return decision


def _host_adapter_capability_is_live(value: object) -> bool:
    # This prerelease deliberately has no native host adapter.
    return False


def authorization_effects_for_route(
    authorization: TrustedAuthorization,
    *,
    expected_task_digest: str,
    expected_scope_paths: tuple[str, ...],
) -> set[str]:
    del authorization, expected_task_digest, expected_scope_paths
    raise ValueError("R_CORE_NO_REMOTE_AUTHORITY: external effects are quarantined")


@dataclass(frozen=True)
class WorktreeRecord:
    worktree: str
    git_dir: str


class ValidatedWorktreeInventoryObservation:
    __slots__ = ("_consumed", "common_git_dir", "records", "__weakref__")

    def __new__(
        cls, *_: object, **__: object
    ) -> "ValidatedWorktreeInventoryObservation":
        raise TypeError("Worktree inventory is emitted only by Core observation")


def observe_current_worktree(root: Path | str) -> ValidatedWorktreeInventoryObservation:
    repository = discover_repository(Path(root))
    result = object.__new__(ValidatedWorktreeInventoryObservation)
    result._consumed = False
    result.common_git_dir = str(git_common_dir(repository))
    result.records = (
        WorktreeRecord(
            worktree=str(repository),
            git_dir=str(worktree_git_dir(repository)),
        ),
    )
    _register_runtime_host_object(result, "validated_worktree_inventory")
    return result


def _consume_worktree_inventory(value: object) -> dict[str, object] | None:
    if type(value) is not ValidatedWorktreeInventoryObservation:
        return None
    return _consume_runtime_host_object_snapshot(
        value,
        "validated_worktree_inventory",
    )
