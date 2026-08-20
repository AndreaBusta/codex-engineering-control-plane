"""Exact source projection and zero-mutation adoption preview."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import tomllib
from typing import Any, Mapping

from .contracts import ADOPTION_LIFECYCLE, REQUIREMENT_IDS, contract_digest, validate_plan
from .repository import SourceObservation, TargetObservation, observe_source, observe_target
from .safe_io import confined_lstat, metadata_identity, read_confined_file


CORE_RUNTIME_MODULES = (
    "__init__.py",
    "adoption_recovery.py",
    "clarification.py",
    "cli.py",
    "contracts.py",
    "core_types.py",
    "git_guards.py",
    "git_state.py",
    "graph.py",
    "hooks.py",
    "intake.py",
    "leases.py",
    "lockfile.py",
    "maintenance.py",
    "materialization.py",
    "policy.py",
    "project_profiles.py",
    "repository.py",
    "resource_registry.py",
    "risk_sentinel.py",
    "routing.py",
    "scopes.py",
    "stable_pause.py",
    "survey.py",
    "task_state.py",
    "toolchain.py",
    "verification.py",
)
MANAGED_SOURCE_PATHS = (
    ".codex/hooks.json",
    ".codex/hooks/control_plane_hook.py",
    ".codex/git-hooks/pre-commit",
    ".codex/git-hooks/pre-push",
    "scripts/control-plane",
    *(f"control_plane/{name}" for name in CORE_RUNTIME_MODULES),
)
FILE_MAX = 1024 * 1024
RUNTIME_TOTAL_MAX = 8 * 1024 * 1024
_DIGEST_BINDINGS = {
    ".codex/hooks.json": "hooks",
    ".codex/hooks/control_plane_hook.py": "hook_entrypoint",
    ".codex/git-hooks/pre-commit": "git_pre_commit",
    ".codex/git-hooks/pre-push": "git_pre_push",
    "scripts/control-plane": "entrypoint",
}
_ROLES = {
    ".codex/hooks.json": "hook_manifest",
    ".codex/hooks/control_plane_hook.py": "hook_entrypoint",
    ".codex/git-hooks/pre-commit": "git_pre_commit",
    ".codex/git-hooks/pre-push": "git_pre_push",
    "scripts/control-plane": "entrypoint",
}


@dataclass(frozen=True)
class TargetProjection:
    source_manifest: Mapping[str, object]
    records: tuple[Mapping[str, object], ...]
    payloads: Mapping[str, bytes]
    target_lock: bytes


def _source_lock(source: Path) -> tuple[bytes, dict[str, Any]]:
    payload = read_confined_file(
        source,
        ".codex/control-plane.lock",
        maximum=FILE_MAX,
    )
    try:
        value = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("E_ADOPTION_SOURCE_LOCK: source lock is invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or value.get("product_version") != "3.1.0-core.2"
        or value.get("runtime_package") != "control_plane"
        or value.get("runtime_layout") != "source"
        or value.get("runtime_modules") != list(CORE_RUNTIME_MODULES)
        or not isinstance(value.get("digests"), dict)
        or "adoption_lifecycle" in value
    ):
        raise ValueError("E_ADOPTION_SOURCE_LOCK: source lock contract is unsupported")
    return payload, value


def _role(relative: str) -> str:
    return _ROLES.get(relative, "runtime_module")


def _record(source: Path, relative: str) -> dict[str, object]:
    payload = read_confined_file(source, relative, maximum=FILE_MAX)
    metadata = confined_lstat(source, relative)
    if metadata is None or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("E_ADOPTION_SOURCE_MANIFEST: source record is unavailable")
    return {
        "path": relative,
        "role": _role(relative),
        "sha256": f"sha256:{sha256(payload).hexdigest()}",
        "git_mode": "100755" if metadata.st_mode & stat.S_IXUSR else "100644",
        "size_bytes": len(payload),
    }


def _runtime_digest(source: Path) -> str:
    hasher = sha256()
    total = 0
    for name in CORE_RUNTIME_MODULES:
        payload = read_confined_file(
            source,
            f"control_plane/{name}",
            maximum=FILE_MAX,
        )
        total += len(payload)
        if total > RUNTIME_TOTAL_MAX:
            raise ValueError("E_ADOPTION_SOURCE_RUNTIME: Core runtime exceeds its bound")
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payload)
        hasher.update(b"\0")
    return f"sha256:{hasher.hexdigest()}"


def _validate_runtime_inventory(source: Path) -> None:
    runtime = source / "control_plane"
    before = runtime.lstat()
    if not stat.S_ISDIR(before.st_mode) or stat.S_IMODE(before.st_mode) & 0o022:
        raise ValueError("E_ADOPTION_SOURCE_MODULES: runtime directory is unsafe")
    observed: list[str] = []
    try:
        with os.scandir(runtime) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > 256:
                    raise ValueError("E_ADOPTION_SOURCE_MODULES: runtime inventory exceeds its bound")
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError("E_ADOPTION_SOURCE_MODULES: runtime contains a non-file entry")
                observed.append(entry.name)
    except OSError as error:
        raise ValueError("E_ADOPTION_SOURCE_MODULES: runtime inventory is unavailable") from error
    after = runtime.lstat()
    if (
        tuple(sorted(observed)) != tuple(sorted(CORE_RUNTIME_MODULES))
        or metadata_identity(before) != metadata_identity(after)
    ):
        raise ValueError("E_ADOPTION_SOURCE_MODULES: runtime module set is not exact")


def build_source_manifest(source: Path) -> dict[str, object]:
    observation = observe_source(source)
    lock_payload, lock = _source_lock(source)
    _validate_runtime_inventory(source)
    observed_runtime = _runtime_digest(source)
    digests = lock["digests"]
    if observed_runtime != observation.runtime_digest or observed_runtime != digests.get("runtime"):
        raise ValueError("E_ADOPTION_SOURCE_RUNTIME: runtime digest drifted")
    records = [_record(source, relative) for relative in sorted(MANAGED_SOURCE_PATHS)]
    by_path = {record["path"]: record for record in records}
    for relative, digest_key in _DIGEST_BINDINGS.items():
        if by_path[relative]["sha256"] != digests.get(digest_key):
            raise ValueError("E_ADOPTION_SOURCE_MANIFEST: source entrypoint digest drifted")
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreSourceManifestV1",
        "head": observation.head,
        "tree": observation.tree,
        "product_version": observation.product_version,
        "runtime_digest": observed_runtime,
        "source_lock_digest": f"sha256:{sha256(lock_payload).hexdigest()}",
        "records": records,
        "authorizes": False,
    }
    manifest = dict(unsigned)
    manifest["manifest_digest"] = contract_digest(unsigned)
    after = observe_source(source)
    if after != observation:
        raise ValueError("E_ADOPTION_SOURCE_DRIFT: source changed during manifest construction")
    return manifest


def render_target_lock(
    source: Path,
    target: TargetObservation,
    *,
    projected_digests: Mapping[str, str] | None = None,
) -> bytes:
    payload, _ = _source_lock(source)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("E_ADOPTION_SOURCE_LOCK: source lock is not UTF-8") from error
    substitutions = {
        "project_policy": target.policy_digest,
        "resource_registry": target.registry_digest,
    }
    text, lifecycle_count = re.subn(
        r'(?m)^(product_version = "3\.1\.0-core\.2")$',
        rf'\1\nadoption_lifecycle = "{ADOPTION_LIFECYCLE}"',
        text,
    )
    if lifecycle_count != 1:
        raise ValueError("E_ADOPTION_SOURCE_LOCK: lifecycle insertion point is absent")
    if projected_digests is not None:
        for key in ("git_pre_commit", "git_pre_push"):
            digest = projected_digests.get(key)
            if not isinstance(digest, str):
                raise ValueError("E_ADOPTION_SOURCE_LOCK: projected hook binding is absent")
            substitutions[key] = digest
    for key, digest in substitutions.items():
        text, count = re.subn(
            rf'(?m)^{re.escape(key)} = "sha256:[0-9a-f]{{64}}"$',
            f'{key} = "{digest}"',
            text,
        )
        if count != 1:
            raise ValueError("E_ADOPTION_SOURCE_LOCK: target authority binding is absent")
    rendered = text.encode("utf-8")
    try:
        value = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError("E_ADOPTION_SOURCE_LOCK: rendered target lock is invalid") from error
    if (
        value.get("digests", {}).get("project_policy") != target.policy_digest
        or value.get("digests", {}).get("resource_registry") != target.registry_digest
        or value.get("adoption_lifecycle") != ADOPTION_LIFECYCLE
    ):
        raise ValueError("E_ADOPTION_SOURCE_LOCK: rendered target lock binding drifted")
    return rendered


def _projected_payload(source: Path, relative: str) -> bytes:
    payload = read_confined_file(source, relative, maximum=FILE_MAX)
    if relative in {
        ".codex/git-hooks/pre-commit",
        ".codex/git-hooks/pre-push",
    }:
        marker = b"__CONTROL_PLANE_ENTRYPOINT__"
        if payload.count(marker) != 1:
            raise ValueError("E_ADOPTION_SOURCE_MANIFEST: Git hook template is invalid")
        payload = payload.replace(marker, b"$repo/scripts/control-plane")
    return payload


def _projected_record(source: Path, relative: str, payload: bytes) -> dict[str, object]:
    metadata = confined_lstat(source, relative)
    if metadata is None or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("E_ADOPTION_SOURCE_MANIFEST: source record is unavailable")
    return {
        "path": relative,
        "role": _role(relative),
        "sha256": f"sha256:{sha256(payload).hexdigest()}",
        "git_mode": "100755" if metadata.st_mode & stat.S_IXUSR else "100644",
        "size_bytes": len(payload),
    }


def build_target_projection(
    source: Path,
    target: TargetObservation,
) -> TargetProjection:
    manifest = build_source_manifest(source)
    payloads = {
        relative: _projected_payload(source, relative)
        for relative in sorted(MANAGED_SOURCE_PATHS)
    }
    projected_digests = {
        digest_key: f"sha256:{sha256(payloads[relative]).hexdigest()}"
        for relative, digest_key in _DIGEST_BINDINGS.items()
        if digest_key in {"git_pre_commit", "git_pre_push"}
    }
    target_lock = render_target_lock(
        source,
        target,
        projected_digests=projected_digests,
    )
    payloads[".codex/control-plane.lock"] = target_lock
    records = [
        _projected_record(source, relative, payload)
        for relative, payload in payloads.items()
        if relative != ".codex/control-plane.lock"
    ]
    records.append(
        {
            "path": ".codex/control-plane.lock",
            "role": "activation_pointer",
            "sha256": f"sha256:{sha256(target_lock).hexdigest()}",
            "git_mode": "100644",
            "size_bytes": len(target_lock),
        }
    )
    records.sort(key=lambda item: item["path"])
    return TargetProjection(
        source_manifest=manifest,
        records=tuple(records),
        payloads=payloads,
        target_lock=target_lock,
    )


def _target_contract(target: TargetObservation) -> dict[str, object]:
    return {
        "repository_id": list(target.repository_id),
        "common_dir_id": list(target.common_dir_id),
        "worktree_id": list(target.worktree_id),
        "branch": target.branch,
        "head": target.head,
        "policy_digest": target.policy_digest,
        "registry_digest": target.registry_digest,
        "before_snapshot_digest": target.before_snapshot_digest,
        "core_hooks_path_before": None,
        "adoption_lifecycle": ADOPTION_LIFECYCLE,
        "managed_parent_directories": [
            dict(item) for item in target.managed_parent_directories
        ],
        "managed_repository_scan": dict(target.managed_repository_scan),
    }


def preview(
    source: Path,
    target: Path,
    *,
    adoption_lock_held: bool = False,
    provisioning_recovery: bool = False,
) -> dict[str, object]:
    source_before = build_source_manifest(source)
    target_before = observe_target(
        target,
        authority_source=source,
        adoption_lock_held=adoption_lock_held,
        provisioning_recovery=provisioning_recovery,
    )
    projection = build_target_projection(source, target_before)
    manifest = projection.source_manifest
    if manifest != source_before:
        raise ValueError("E_ADOPTION_SOURCE_DRIFT: selected source changed during preview")
    records = list(projection.records)
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreAdoptionPlanV1",
        "source": {
            "head": manifest["head"],
            "tree": manifest["tree"],
            "product_version": manifest["product_version"],
            "runtime_digest": manifest["runtime_digest"],
            "lock_digest": manifest["source_lock_digest"],
            "manifest_digest": manifest["manifest_digest"],
        },
        "target": _target_contract(target_before),
        "managed_records": records,
        "before_snapshot_digest": target_before.before_snapshot_digest,
        "requirement_ids": list(REQUIREMENT_IDS),
        "result": "PASS",
        "applicable": True,
        "mutation": False,
        "error_codes": [],
        "authorizes": False,
    }
    plan = dict(unsigned)
    plan["plan_digest"] = contract_digest(unsigned)
    target_after = observe_target(
        target,
        authority_source=source,
        adoption_lock_held=adoption_lock_held,
        provisioning_recovery=provisioning_recovery,
    )
    source_after = build_source_manifest(source)
    if source_after != source_before:
        raise ValueError("E_ADOPTION_SOURCE_DRIFT: selected source changed during preview")
    if target_after != target_before:
        raise ValueError("E_ADOPTION_TARGET_DRIFT: target changed during preview")
    issues = validate_plan(plan)
    if issues:
        raise ValueError(f"E_ADOPTION_PLAN: generated plan is invalid ({issues[0].code})")
    return plan
