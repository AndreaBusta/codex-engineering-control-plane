"""Lazy, Core-only command line interface for Control Plane 3.1."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4


_CORE_STATES = (
    "framed",
    "planned",
    "ready",
    "implementing",
    "verifying",
    "review_ready",
    "blocked",
    "closed",
)
_QUARANTINED = "E_CAPABILITY_QUARANTINED"
_JSON_INPUT_MAX_BYTES = 1_048_576


def _error_code(error: BaseException) -> str:
    return str(getattr(error, "code", str(error).split(":", 1)[0]))


def _read_json(path: Path) -> dict[str, Any]:
    descriptor = -1
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size > _JSON_INPUT_MAX_BYTES
        ):
            raise OSError("unsafe JSON input")
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > _JSON_INPUT_MAX_BYTES
            or opened_identity != before_identity
        ):
            raise OSError("JSON input changed before open")
        payload = bytearray()
        while len(payload) <= _JSON_INPUT_MAX_BYTES:
            chunk = os.read(
                descriptor,
                min(65_536, _JSON_INPUT_MAX_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            after_identity != opened_identity
            or len(payload) > _JSON_INPUT_MAX_BYTES
            or len(payload) != after.st_size
        ):
            raise OSError("JSON input changed during read")
    except (FileNotFoundError, OSError, ValueError) as error:
        raise ValueError(f"E_JSON_INPUT: could not read {path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"E_JSON_INPUT: could not read {path}") from error
    if not isinstance(value, dict):
        raise ValueError("E_JSON_INPUT: top-level JSON value must be an object")
    return value


def _render_human(payload: Mapping[str, Any]) -> str:
    command = str(payload.get("command", "control-plane"))
    if command == "risk-status" and payload.get("status") in {"PASS", "FAIL", "UNKNOWN"}:
        lines = [f"{payload['status']} risk-status"]
    else:
        diagnostic = (
            command == "preflight"
            and payload.get("mode") == "read"
            and any(
                isinstance(check, Mapping) and not check.get("ok", False)
                for check in payload.get("checks", [])
            )
        ) or (
            command == "route-verify"
            and payload.get("authoritative") is False
        )
        status = (
            "DIAGNOSTIC"
            if diagnostic and payload.get("ok")
            else ("PASS" if payload.get("ok") else "FAIL")
        )
        lines = [f"{status} {command}"]
    facts = payload.get("facts")
    if isinstance(facts, Mapping):
        lines.extend(f"{key}={facts[key]}" for key in sorted(facts))
    for issue in payload.get("issues", []):
        if isinstance(issue, Mapping):
            lines.append(
                f"ISSUE {issue.get('code', 'UNKNOWN')} {issue.get('path', '')}: "
                f"{issue.get('message', '')}"
            )
    for error in payload.get("errors", []):
        if isinstance(error, Mapping):
            lines.append(
                f"ERROR {error.get('code', 'UNKNOWN')}: {error.get('message', '')}"
            )
    return "\n".join(lines)


def _emit(payload: dict[str, Any], as_json: bool) -> int:
    print(
        json.dumps(payload, indent=2, sort_keys=True)
        if as_json
        else _render_human(payload)
    )
    if payload.get("error_code") == _QUARANTINED:
        return 2
    if payload.get("command") == "risk-status" and payload.get("status") == "UNKNOWN":
        return 2
    return 0 if payload.get("ok") else 1


def _failure(command: str, error: BaseException, *, schema_version: int = 1) -> dict[str, Any]:
    return {
        "schema_version": schema_version,
        "command": command,
        "ok": False,
        "errors": [{"code": _error_code(error), "message": str(error)}],
        "authorizes": False,
    }


def _quarantined(command: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "command": command,
        "ok": False,
        "error_code": _QUARANTINED,
        "authorizes": False,
    }


def _repository(repo: Path) -> Path:
    from control_plane.repository import discover_repository

    return discover_repository(repo)


def _policy_path(repo: Path, explicit: Path | None) -> Path:
    return explicit if explicit is not None else _repository(repo) / ".codex/project-policy.toml"


def _registry_path(repo: Path, explicit: Path | None) -> Path:
    return explicit if explicit is not None else _repository(repo) / ".codex/resource-registry.toml"


def _load_policy(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    from control_plane.policy import PolicyError, load_policy, validate_policy

    try:
        policy = load_policy(path)
    except PolicyError as error:
        return None, {
            "schema_version": 1,
            "command": "policy-check",
            "ok": False,
            "policy": str(path),
            "issues": [],
            "errors": [{"code": error.code, "message": error.message}],
            "authorizes": False,
        }
    issues = validate_policy(policy)
    return policy, {
        "schema_version": 1,
        "command": "policy-check",
        "ok": not issues,
        "policy": str(path),
        "issues": [asdict(issue) for issue in issues],
        "errors": [],
        "authorizes": False,
    }


def _governing_policy(repo: Path, explicit: Path | None) -> dict[str, Any]:
    """Load the canonical policy and accept an explicit path only as an exact hint."""

    from control_plane.contracts import contract_digest
    from control_plane.policy import load_policy, validate_policy

    canonical = repo / ".codex" / "project-policy.toml"
    policy = load_policy(canonical)
    issues = validate_policy(policy)
    if issues:
        raise ValueError(f"P_POLICY: {issues[0].message}")
    if explicit is not None:
        candidate = load_policy(explicit)
        candidate_issues = validate_policy(candidate)
        if candidate_issues:
            raise ValueError(f"P_POLICY: {candidate_issues[0].message}")
        if contract_digest(candidate) != contract_digest(policy):
            raise ValueError(
                "E_POLICY_NOT_GOVERNING: explicit policy differs from the canonical policy"
            )
    return policy


def _git(repo: Path, *arguments: str, text: bool = True) -> subprocess.CompletedProcess[Any]:
    from control_plane.repository import trusted_git_argv, trusted_git_environment

    return subprocess.run(
        trusted_git_argv(repo, arguments),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=text,
        env=trusted_git_environment(),
        timeout=10,
    )


def _branch(repo: Path) -> str:
    completed = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = completed.stdout.strip()
    if completed.returncode != 0 or not branch:
        raise ValueError("E_CORE_STATE_BRANCH: task lifecycle requires a named branch")
    return branch


def _head(repo: Path) -> str:
    completed = _git(repo, "rev-parse", "--verify", "HEAD")
    head = completed.stdout.strip()
    if completed.returncode != 0 or len(head) != 40:
        raise ValueError("E_CORE_STATE_HEAD: repository HEAD is unavailable")
    return head


def _changed_paths(repo: Path) -> list[str]:
    from control_plane.repository import assert_no_external_git_filters

    try:
        assert_no_external_git_filters(repo)
        changed = _git(repo, "diff", "--no-renames", "--name-only", "-z", "HEAD", text=False)
        untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z", text=False)
        if changed.returncode != 0 or untracked.returncode != 0:
            raise ValueError
        values = {
            item.decode("utf-8", errors="strict")
            for payload in (changed.stdout, untracked.stdout)
            for item in payload.split(b"\0")
            if item
        }
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError("E_CORE_LEASE_SCOPE: could not enumerate changed paths") from error
    return sorted(values)


def command_policy_check(arguments: argparse.Namespace) -> int:
    _, payload = _load_policy(arguments.policy)
    return _emit(payload, arguments.json)


def command_registry_check(arguments: argparse.Namespace) -> int:
    from control_plane.resource_registry import (
        RegistryError,
        load_registry,
        validate_policy_references,
        validate_registry,
    )

    try:
        registry = load_registry(arguments.registry)
        issues = validate_registry(registry)
        if arguments.policy is not None:
            policy, policy_payload = _load_policy(arguments.policy)
            if policy is None or not policy_payload["ok"]:
                payload = dict(policy_payload)
                payload["command"] = "registry-check"
                return _emit(payload, arguments.json)
            issues.extend(validate_policy_references(policy, registry))
        payload = {
            "schema_version": 1,
            "command": "registry-check",
            "ok": not issues,
            "issues": [asdict(issue) for issue in issues],
            "errors": [],
            "authorizes": False,
        }
    except RegistryError as error:
        payload = _failure("registry-check", error)
        payload["issues"] = []
    return _emit(payload, arguments.json)


def command_inventory(arguments: argparse.Namespace) -> int:
    from control_plane.resource_registry import (
        RegistryError,
        build_inventory,
        load_registry,
        validate_registry,
    )

    try:
        root = _repository(arguments.repo)
        registry = load_registry(_registry_path(root, arguments.registry))
        issues = validate_registry(registry)
        if issues:
            payload = {
                "schema_version": 1,
                "command": "inventory",
                "ok": False,
                "issues": [asdict(issue) for issue in issues],
                "errors": [],
                "authorizes": False,
            }
        else:
            payload = build_inventory(registry, root)
            payload.update({"command": "inventory", "ok": True, "authorizes": False})
    except (RegistryError, ValueError, OSError) as error:
        payload = _failure("inventory", error)
        payload["issues"] = []
    return _emit(payload, arguments.json)


def command_doctor(arguments: argparse.Namespace) -> int:
    from control_plane.lockfile import validate_lock
    from control_plane.materialization import (
        inspect_git_state_materialization,
        inspect_tracked_materialization,
    )
    from control_plane.repository import trusted_git_executable
    from control_plane.resource_registry import (
        load_registry,
        validate_policy_references,
        validate_registry,
    )

    errors: list[dict[str, str]] = []
    try:
        trusted_git_executable()
        git_available = True
    except OSError:
        git_available = False
    try:
        root = _repository(arguments.repo)
        git_repository = True
    except Exception as error:
        root = None
        git_repository = False
        errors.append({"code": _error_code(error), "message": str(error)})
    policy_path = (
        arguments.policy
        if arguments.policy is not None
        else (root / ".codex" / "project-policy.toml" if root is not None else None)
    )
    if policy_path is None:
        policy = None
        policy_payload: dict[str, Any] = {
            "ok": False,
            "issues": [],
            "errors": [],
        }
    else:
        policy, policy_payload = _load_policy(policy_path)
    facts: dict[str, Any] = {
        "git_available": git_available,
        "git_repository": git_repository,
        "policy_valid": bool(policy_payload["ok"]),
        "python_compatible": sys.version_info >= (3, 11),
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "registry_valid": None,
        "lock_valid": None,
        "tracked_files_materialized": None,
        "dataless_tracked_files": None,
        "materialization_status": "UNKNOWN",
        "git_state_materialized": None,
        "dataless_git_state_files": None,
        "git_state_materialization_status": "UNKNOWN",
        "git_state_areas": [],
    }
    if not git_available:
        errors.append({"code": "E_DOCTOR_GIT", "message": "Git is not available."})
    if not facts["python_compatible"]:
        errors.append({"code": "E_DOCTOR_PYTHON", "message": "Python 3.11 or newer is required."})
    errors.extend(policy_payload.get("errors", []))
    errors.extend(
        {"code": issue["code"], "message": f"{issue['path']}: {issue['message']}"}
        for issue in policy_payload.get("issues", [])
    )
    if root is not None and arguments.policy is None:
        try:
            materialization = inspect_tracked_materialization(root)
            facts.update(
                {
                    "tracked_files_materialized": materialization.ok,
                    "dataless_tracked_files": len(materialization.dataless_paths),
                    "materialization_status": materialization.status,
                }
            )
            if not materialization.ok:
                errors.append(
                    {
                        "code": materialization.error_code
                        or "E_MATERIALIZATION_UNKNOWN",
                        "message": "Tracked file materialization is not proven.",
                    }
                )
            git_state = inspect_git_state_materialization(root)
            facts.update(
                {
                    "git_state_materialized": git_state.ok,
                    "dataless_git_state_files": git_state.dataless_files,
                    "git_state_materialization_status": git_state.status,
                    "git_state_areas": list(git_state.areas),
                }
            )
            if not git_state.ok:
                errors.append(
                    {
                        "code": git_state.error_code or "E_MATERIALIZATION_UNKNOWN",
                        "message": "Git state materialization is not proven.",
                    }
                )
            registry = load_registry(_registry_path(root, None))
            registry_issues = validate_registry(registry)
            if policy is not None:
                registry_issues.extend(validate_policy_references(policy, registry))
            lock_issues = validate_lock(root)
            facts["registry_valid"] = not registry_issues
            facts["lock_valid"] = not lock_issues
            errors.extend(
                {"code": issue.code, "message": f"{issue.path}: {issue.message}"}
                for issue in [*registry_issues, *lock_issues]
            )
        except Exception as error:
            errors.append({"code": _error_code(error), "message": str(error)})
    return _emit(
        {
            "schema_version": 1,
            "command": "doctor",
            "ok": not errors,
            "facts": facts,
            "errors": errors,
            "authorizes": False,
        },
        arguments.json,
    )


def command_preflight(arguments: argparse.Namespace) -> int:
    if arguments.mode == "release":
        return _emit(_quarantined("preflight"), arguments.json)

    from control_plane.contracts import contract_digest
    from control_plane.git_state import evaluate_preflight
    from control_plane.task_state import validate_writer_continuation

    try:
        root = _repository(arguments.repo)
        policy = _governing_policy(root, arguments.policy)
        payload = evaluate_preflight(root, policy, arguments.mode).to_dict()
        payload.update({"offline": not arguments.refresh, "authorizes": False})
        if arguments.refresh:
            payload["errors"].insert(
                0,
                {
                    "code": _QUARANTINED,
                    "message": "Remote refresh is outside the Core runtime.",
                },
            )
            payload["ok"] = False
        if (
            arguments.mode == "write"
            and payload["facts"].get("dirty") is True
            and arguments.task_id
            and arguments.session_id
        ):
            validate_writer_continuation(
                root,
                task_id=arguments.task_id,
                worktree=str(root),
                branch=str(payload["facts"].get("branch")),
                session_id=arguments.session_id,
                policy_digest=contract_digest(policy),
                changed_paths=_changed_paths(root),
            )
            payload["errors"] = [
                item for item in payload["errors"] if item.get("code") != "E_GIT_DIRTY"
            ]
            payload["facts"]["lease_continuation"] = True
            payload["ok"] = not payload["errors"]
    except Exception as error:
        if "payload" not in locals():
            payload = _failure("preflight", error)
            payload.update({"mode": arguments.mode, "offline": not arguments.refresh, "facts": {}, "checks": [], "issues": []})
        else:
            payload["errors"].append({"code": _error_code(error), "message": str(error)})
            payload["facts"]["lease_continuation"] = False
            payload["ok"] = False
    return _emit(payload, arguments.json)


def command_route(arguments: argparse.Namespace) -> int:
    from control_plane.contracts import contract_digest, validate_task_envelope
    from control_plane.core_types import HOST_ADAPTER_UNAVAILABLE, seal_validated_inventory
    from control_plane.policy import load_policy, validate_policy
    from control_plane.resource_registry import (
        build_inventory,
        load_registry,
        registry_contract_digest,
        validate_policy_references,
        validate_registry,
    )
    from control_plane.routing import compact_route_manifest, resolve_route

    try:
        root = _repository(arguments.repo)
        task = _read_json(arguments.task)
        policy = load_policy(_policy_path(root, arguments.policy))
        registry = load_registry(_registry_path(root, arguments.registry))
        issues = [
            *validate_task_envelope(task),
            *validate_policy(policy),
            *validate_registry(registry),
            *validate_policy_references(policy, registry),
        ]
        if issues:
            payload = {
                "schema_version": 1,
                "command": "route",
                "ok": False,
                "issues": [asdict(issue) for issue in issues],
                "errors": [],
                "authorizes": False,
            }
        else:
            task_digest = contract_digest(task)
            inventory = seal_validated_inventory(
                build_inventory(registry, root),
                task_digest=task_digest,
                registry_digest=registry_contract_digest(registry),
            )
            decision = resolve_route(
                task,
                policy,
                registry,
                inventory,
                mode=arguments.mode,
                host_capability=HOST_ADAPTER_UNAVAILABLE,
            )
            decision_payload = decision.payload
            compact_route_manifest(decision_payload)
            payload = decision_payload
            payload["command"] = "route"
    except Exception as error:
        payload = _failure("route", error)
        payload["issues"] = []
    return _emit(payload, arguments.json)


def command_route_verify(arguments: argparse.Namespace) -> int:
    from control_plane.routing import verify_route

    try:
        payload = verify_route(
            _read_json(arguments.decision),
            _read_json(arguments.receipt),
            mode=arguments.mode,
        )
        payload["authorizes"] = False
    except Exception as error:
        payload = _failure("route-verify", error)
    return _emit(payload, arguments.json)


def command_risk_status(arguments: argparse.Namespace) -> int:
    from control_plane.contracts import contract_digest
    from control_plane.risk_sentinel import evaluate_risk_status
    from control_plane.task_state import CoreTaskStore

    try:
        root = _repository(arguments.repo)
        if arguments.lease_session_id is not None and arguments.task_id is None:
            raise ValueError("RS_LOCAL_LEASE_TASK: --lease-session-id requires --task-id")
        task_state = None
        if arguments.task_id is not None:
            try:
                task_state = CoreTaskStore(root).status(arguments.task_id)
            except ValueError:
                task_state = {"task_id": arguments.task_id, "_unobserved": True}
        decision = _read_json(arguments.decision) if arguments.decision else None
        candidate_status = "not_provided"
        candidate_digest = None
        if arguments.policy is not None:
            candidate, candidate_payload = _load_policy(arguments.policy)
            candidate_status = "valid_hint" if candidate is not None and candidate_payload["ok"] else "invalid_hint"
            candidate_digest = contract_digest(candidate) if candidate is not None else None
        payload = evaluate_risk_status(
            root,
            None,
            task_state=task_state,
            route_decision_hint=decision,
            local_lease_session_id=arguments.lease_session_id,
        ).to_dict()
        payload["facts"].update(
            {
                "governing_policy_source": "unavailable_pending_installed_manifest",
                "candidate_policy_status": candidate_status,
                "candidate_policy_digest": candidate_digest,
                "serialized_decision_authoritative": False,
                "automatic_change": False,
            }
        )
    except Exception as error:
        payload = _failure("risk-status", error)
        payload.update(
            {
                "status": "FAIL",
                "dimensions": {
                    "local": {"status": "FAIL", "checks": [], "errors": []},
                    "remote": {"status": "UNKNOWN", "checks": [], "errors": []},
                },
                "facts": {},
            }
        )
    return _emit(payload, arguments.json)


def command_task(arguments: argparse.Namespace) -> int:
    from control_plane.contracts import contract_digest
    from control_plane.leases import LeaseStore
    from control_plane.task_state import CoreTaskStore

    command = f"task-{arguments.task_action}"

    def release_binding(lease: Mapping[str, object] | None) -> dict[str, object] | None:
        if lease is None:
            return None
        fields = (
            "task_id",
            "revision_id",
            "lease_generation",
            "worktree",
            "branch",
            "session_id",
            "policy_digest",
            "lease_digest",
        )
        return {
            "schema_version": 1,
            "kind": "CoreLeaseReleaseBindingV1",
            **{field: lease[field] for field in fields},
            "authorizes": False,
        }

    try:
        root = _repository(arguments.repo)
        store = CoreTaskStore(root)
        leases = LeaseStore(root)
        action = arguments.task_action
        lease = None
        if action == "start":
            current_branch = _branch(root)
            if arguments.branch != current_branch:
                raise ValueError("E_CORE_STATE_BRANCH: declared branch differs from current branch")
            policy_digest = None
            if arguments.outcome == "local_change":
                policy = _governing_policy(root, arguments.policy)
                policy_digest = contract_digest(policy)
            result, created_task = store.start_with_origin(
                arguments.task_id,
                outcome=arguments.outcome,
                branch=arguments.branch,
                protected_base=(
                    str(policy["git"]["base_branch"])
                    if arguments.outcome == "local_change"
                    else None
                ),
                head=_head(root),
                task_digest=arguments.task_digest,
                decision_digest=arguments.decision_digest,
                scope_paths=arguments.scope_path or ["."],
            )
            if arguments.session_id and result.get("kind") == "CoreTaskStateV1":
                created_lease = False
                unbound = result
                try:
                    lease, created_lease = leases.acquire_with_origin(
                        result,
                        session_id=arguments.session_id,
                        policy_digest=str(policy_digest),
                    )
                    result = store.bind_lease_generation(
                        arguments.task_id,
                        revision_id=str(lease["revision_id"]),
                        generation=int(lease["lease_generation"]),
                        expected_state_digest=str(lease["acquired_state_digest"]),
                        session_id=str(lease["session_id"]),
                    )
                except Exception as start_error:
                    try:
                        if lease is not None:
                            current = store.status(arguments.task_id)
                            if (
                                current.get("revision_id") == lease.get("revision_id")
                                and current.get("lease_generation")
                                == lease.get("lease_generation")
                                and current.get("state_digest")
                                != unbound.get("state_digest")
                            ):
                                store.restore_after_failed_binding(
                                    unbound,
                                    expected_revision_id=str(lease["revision_id"]),
                                    expected_generation=int(lease["lease_generation"]),
                                    session_id=str(lease["session_id"]),
                                )
                        if created_lease and lease is not None:
                            leases.rollback_acquire(lease)
                        if created_task:
                            store.rollback_start(unbound)
                    except Exception as rollback_error:
                        raise ValueError(
                            f"E_CORE_START_ROLLBACK: {rollback_error}"
                        ) from start_error
                    raise
        elif action == "status":
            result = store.status(arguments.task_id)
            lease = leases.find(arguments.task_id)
        elif action == "revise":
            result = store.next_revision(
                arguments.task_id,
                current_branch=_branch(root),
                head=_head(root),
                task_digest=arguments.task_digest,
                decision_digest=arguments.decision_digest,
                scope_paths=arguments.scope_path or ["."],
            )
        elif action == "transition":
            result = store.transition(
                arguments.task_id,
                arguments.state,
                reason=arguments.reason,
                current_branch=_branch(root),
                session_id=arguments.session_id,
            )
        elif action == "resume":
            result = store.resume(
                arguments.task_id,
                current_branch=_branch(root),
                session_id=arguments.session_id,
            )
        elif action == "close":
            result = store.close(
                arguments.task_id,
                current_branch=_branch(root),
                session_id=arguments.session_id,
            )
        else:
            lease = None
            if arguments.revision_id is None or arguments.lease_generation is None:
                lease = leases.find(arguments.task_id)
                if lease is None:
                    raise ValueError(
                        "E_CORE_LEASE_NOT_FOUND: active lease is unavailable"
                    )
            result = leases.release(
                task_id=arguments.task_id,
                revision_id=(
                    arguments.revision_id
                    if arguments.revision_id is not None
                    else str(lease["revision_id"])
                ),
                lease_generation=(
                    arguments.lease_generation
                    if arguments.lease_generation is not None
                    else int(lease["lease_generation"])
                ),
                worktree=arguments.worktree,
                branch=arguments.branch,
                session_id=arguments.session_id,
                policy_digest=arguments.policy_digest,
                lease_digest=arguments.lease_digest,
            )
        payload = {
            "schema_version": 1,
            "command": command,
            "ok": True,
            "task": result,
            "authorizes": False,
        }
        if action in {"start", "status", "revise"}:
            payload["lease"] = release_binding(lease)
        elif action == "lease-release":
            payload["lease"] = None
            payload["receipt"] = result
    except Exception as error:
        payload = _failure(command, error)
    return _emit(payload, arguments.json)


def command_safe_read(arguments: argparse.Namespace) -> int:
    from control_plane.core_types import observe_current_worktree
    from control_plane.hooks import _safe_read_repository_identity, execute_safe_read

    try:
        root, _, _ = _safe_read_repository_identity(arguments.repo)
        argv = tuple(arguments.argv)
        if argv[:1] == ("--",):
            argv = argv[1:]
        result = execute_safe_read(
            argv,
            root=root,
            worktree_inventory=observe_current_worktree(root),
            timeout_seconds=arguments.timeout,
            output_limit_bytes=arguments.output_limit,
        )
    except Exception as error:
        print(f"{_error_code(error)}: safe-read precondition failed", file=sys.stderr)
        return 126
    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)
    if result.status == "completed":
        return int(result.exit_code)
    codes = {"timeout": (124, "E_SAFE_READ_TIMEOUT"), "truncated": (125, "E_SAFE_READ_TRUNCATED")}
    exit_code, code = codes.get(result.status, (126, "E_SAFE_READ_ARGV"))
    print(f"{code}: bounded read was not completed", file=sys.stderr)
    return exit_code


def command_hook_smoke(arguments: argparse.Namespace) -> int:
    del arguments.repo, arguments.task_id
    return _emit(
        {
            "schema_version": 1,
            "command": "hook-smoke",
            "ok": False,
            "status": "UNKNOWN",
            "error_code": _QUARANTINED,
            "errors": [
                {
                    "code": _QUARANTINED,
                    "message": "Advanced hook-smoke assurance is unavailable in Core.",
                }
            ],
            "authorizes": False,
        },
        arguments.json,
    )


def _manifest_digest() -> str:
    digest = Path(__file__).resolve(strict=True).parent.parent.name
    if len(digest) != 71 or not digest.startswith("sha256:"):
        raise ValueError("GG_INSTALLED_POLICY_INVALID: CLI is not an installed digest snapshot")
    int(digest[7:], 16)
    return digest


def _pre_push_updates(limit: int = 1_048_576) -> list[tuple[str, str, str, str]]:
    payload = sys.stdin.buffer.read(limit + 1)
    if len(payload) > limit:
        raise ValueError("GG_INPUT_INVALID: pre-push input exceeds the 1 MiB limit")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("GG_INPUT_INVALID: pre-push input must be UTF-8") from error
    updates = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("GG_INPUT_INVALID: each pre-push line requires four fields")
        updates.append(tuple(fields))
    return updates


def command_git_guard(arguments: argparse.Namespace) -> int:
    from control_plane.git_guards import guard_pre_commit, guard_pre_push, load_protected_git_policy
    from control_plane.repository import git_common_dir

    try:
        root = _repository(arguments.repo)
        protected = load_protected_git_policy(
            canonical_repo=root,
            common_git_dir=git_common_dir(root),
            installed_manifest_digest=_manifest_digest(),
            invocation_id=f"git-guard-{uuid4().hex}",
            clock=time.monotonic,
        )
        payload = (
            guard_pre_commit(root, protected)
            if arguments.guard_action == "pre-commit"
            else guard_pre_push(
                root,
                protected,
                remote_name=arguments.remote_name,
                remote_url=arguments.remote_url,
                updates=_pre_push_updates(),
            )
        )
        payload["authorizes"] = False
    except Exception as error:
        payload = _failure("git-guard", error)
        payload.update({"event": arguments.guard_action, "warnings": []})
    return _emit(payload, arguments.json)


def command_adopt(arguments: argparse.Namespace) -> int:
    command = f"adopt-{arguments.adopt_action}"
    if arguments.adopt_action in {"plan", "apply"}:
        return _emit(_quarantined(command), arguments.json)
    from control_plane.adoption_recovery import adoption_rollback, adoption_status, adoption_verify

    try:
        functions = {
            "status": adoption_status,
            "verify": adoption_verify,
            "rollback": adoption_rollback,
        }
        payload = functions[arguments.adopt_action](arguments.target)
        payload["authorizes"] = False
    except Exception as error:
        payload = _failure(command, error, schema_version=2)
    return _emit(payload, arguments.json)


def command_quarantined(arguments: argparse.Namespace) -> int:
    action = getattr(arguments, "run_action", None) or getattr(arguments, "upgrade_action", None)
    command = arguments.command if action is None else f"{arguments.command}-{action}"
    return _emit(_quarantined(command), getattr(arguments, "json", False))


def _output(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit the stable JSON contract.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="control-plane")
    commands = parser.add_subparsers(dest="command", required=True)

    policy = commands.add_parser("policy-check")
    policy.add_argument("--policy", type=Path, required=True)
    _output(policy)
    policy.set_defaults(handler=command_policy_check)

    doctor = commands.add_parser("doctor")
    doctor.add_argument("--repo", type=Path, default=Path.cwd())
    doctor.add_argument("--policy", type=Path)
    _output(doctor)
    doctor.set_defaults(handler=command_doctor)

    preflight = commands.add_parser("preflight")
    preflight.add_argument("--mode", choices=("read", "write", "release"), required=True)
    preflight.add_argument("--repo", type=Path, default=Path.cwd())
    preflight.add_argument("--policy", type=Path)
    preflight.add_argument("--task-id")
    preflight.add_argument("--session-id")
    refresh = preflight.add_mutually_exclusive_group()
    refresh.add_argument("--refresh", action="store_true")
    refresh.add_argument("--offline", dest="refresh", action="store_false")
    preflight.set_defaults(refresh=False)
    _output(preflight)
    preflight.set_defaults(handler=command_preflight)

    registry = commands.add_parser("registry-check")
    registry.add_argument("--registry", type=Path, required=True)
    registry.add_argument("--policy", type=Path)
    _output(registry)
    registry.set_defaults(handler=command_registry_check)

    inventory = commands.add_parser("inventory")
    inventory.add_argument("--repo", type=Path, default=Path.cwd())
    inventory.add_argument("--registry", type=Path)
    _output(inventory)
    inventory.set_defaults(handler=command_inventory)

    route = commands.add_parser("route")
    route.add_argument("--repo", type=Path, default=Path.cwd())
    route.add_argument("--task", type=Path, required=True)
    route.add_argument("--policy", type=Path)
    route.add_argument("--registry", type=Path)
    route.add_argument("--mode", choices=("audit", "enforce"), default="audit")
    _output(route)
    route.set_defaults(handler=command_route)

    route_verify = commands.add_parser("route-verify")
    route_verify.add_argument("--decision", type=Path, required=True)
    route_verify.add_argument("--receipt", type=Path, required=True)
    route_verify.add_argument("--mode", choices=("audit", "enforce"), default="audit")
    _output(route_verify)
    route_verify.set_defaults(handler=command_route_verify)

    risk = commands.add_parser("risk-status", aliases=["risk"])
    risk.add_argument("--repo", type=Path, default=Path.cwd())
    risk.add_argument("--policy", type=Path)
    risk.add_argument("--task-id")
    risk.add_argument("--lease-session-id")
    risk.add_argument("--decision", type=Path)
    _output(risk)
    risk.set_defaults(handler=command_risk_status)

    safe_read = commands.add_parser("safe-read")
    safe_read.add_argument("--repo", type=Path, required=True)
    safe_read.add_argument("--timeout", type=float, default=3.0)
    safe_read.add_argument("--output-limit", type=int, default=65_536)
    safe_read.add_argument("argv", nargs=argparse.REMAINDER)
    safe_read.set_defaults(handler=command_safe_read)

    hook_smoke = commands.add_parser("hook-smoke")
    hook_smoke.add_argument("--repo", type=Path, required=True)
    hook_smoke.add_argument("--task-id", required=True)
    _output(hook_smoke)
    hook_smoke.set_defaults(handler=command_hook_smoke)

    guard = commands.add_parser("git-guard")
    guard_actions = guard.add_subparsers(dest="guard_action", required=True)
    pre_commit = guard_actions.add_parser("pre-commit")
    pre_commit.add_argument("--repo", type=Path, required=True)
    _output(pre_commit)
    pre_commit.set_defaults(handler=command_git_guard)
    pre_push = guard_actions.add_parser("pre-push")
    pre_push.add_argument("--repo", type=Path, required=True)
    pre_push.add_argument("--remote-name", required=True)
    pre_push.add_argument("--remote-url", required=True)
    _output(pre_push)
    pre_push.set_defaults(handler=command_git_guard)

    task = commands.add_parser("task")
    task_actions = task.add_subparsers(dest="task_action", required=True)
    for action in (
        "start", "revise", "resume", "status", "transition", "close",
        "lease-release",
    ):
        action_parser = task_actions.add_parser(action)
        action_parser.add_argument("--repo", type=Path, default=Path.cwd())
        action_parser.add_argument("--task-id", required=True)
        if action == "start":
            action_parser.add_argument(
                "--outcome",
                choices=("answer", "local_change", "commit", "pull_request", "integration", "release"),
                required=True,
            )
            action_parser.add_argument("--branch", required=True)
            action_parser.add_argument("--task-digest", required=True)
            action_parser.add_argument("--decision-digest", required=True)
            action_parser.add_argument("--session-id")
            action_parser.add_argument("--scope-path", action="append")
            action_parser.add_argument("--policy", type=Path)
        elif action == "revise":
            action_parser.add_argument("--task-digest", required=True)
            action_parser.add_argument("--decision-digest", required=True)
            action_parser.add_argument("--scope-path", action="append")
        elif action == "transition":
            action_parser.add_argument("--state", choices=_CORE_STATES, required=True)
            action_parser.add_argument("--reason")
            action_parser.add_argument("--session-id")
        elif action in {"resume", "close"}:
            action_parser.add_argument("--session-id")
        elif action == "lease-release":
            action_parser.add_argument("--revision-id")
            action_parser.add_argument("--lease-generation", type=int)
            action_parser.add_argument("--worktree", required=True)
            action_parser.add_argument("--branch", required=True)
            action_parser.add_argument("--session-id", required=True)
            action_parser.add_argument("--policy-digest", required=True)
            action_parser.add_argument("--lease-digest", required=True)
        _output(action_parser)
        action_parser.set_defaults(handler=command_task)

    run = commands.add_parser("run")
    run_actions = run.add_subparsers(dest="run_action", required=True)
    prepare = run_actions.add_parser("prepare")
    prepare.add_argument("--repo", type=Path, default=Path.cwd())
    prepare.add_argument("--task", type=Path, required=True)
    prepare.add_argument("--session-id", required=True)
    prepare.add_argument("--policy", type=Path)
    prepare.add_argument("--registry", type=Path)
    _output(prepare)
    prepare.set_defaults(handler=command_quarantined)
    for action in ("verify", "status", "block"):
        item = run_actions.add_parser(action)
        item.add_argument("--repo", type=Path, default=Path.cwd())
        item.add_argument("--task-id", required=True)
        if action == "block":
            item.add_argument("--reason", required=True)
        _output(item)
        item.set_defaults(handler=command_quarantined)

    report = commands.add_parser("report")
    report.add_argument("--repo", type=Path, default=Path.cwd())
    report.add_argument("--since", default="30d")
    report.add_argument("--format", choices=("markdown",), default="markdown")
    _output(report)
    report.set_defaults(handler=command_quarantined)

    verification = commands.add_parser("verification-run")
    verification.add_argument("--repo", type=Path, default=Path.cwd())
    verification.add_argument("--task-id", required=True)
    _output(verification)
    verification.set_defaults(handler=command_quarantined)

    adopt = commands.add_parser("adopt")
    adopt_actions = adopt.add_subparsers(dest="adopt_action", required=True)
    for action in ("plan", "apply", "verify", "status", "rollback"):
        item = adopt_actions.add_parser(action)
        if action == "plan":
            item.add_argument("--target", type=Path, required=True)
            item.add_argument("--source", type=Path, default=Path.cwd())
            item.add_argument("--base-branch")
            item.add_argument("--remote")
        elif action == "apply":
            item.add_argument("--plan", type=Path, required=True)
        else:
            item.add_argument("--target", type=Path, required=True)
        _output(item)
        item.set_defaults(handler=command_adopt)

    upgrade = commands.add_parser("upgrade")
    upgrade_actions = upgrade.add_subparsers(dest="upgrade_action", required=True)
    for action in ("plan", "apply"):
        item = upgrade_actions.add_parser(action)
        if action == "plan":
            item.add_argument("--target", type=Path, required=True)
            item.add_argument("--source", type=Path, default=Path.cwd())
            item.add_argument("--base-branch")
            item.add_argument("--remote")
        else:
            item.add_argument("--plan", type=Path, required=True)
        _output(item)
        item.set_defaults(handler=command_quarantined)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
