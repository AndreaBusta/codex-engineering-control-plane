"""Command-line interface for the local engineering control plane."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4
from typing import Any, Sequence

from control_plane.adoption import (
    adoption_apply,
    adoption_plan,
    adoption_rollback,
    adoption_status,
    adoption_verify,
    upgrade_apply,
    upgrade_plan,
)
from control_plane.contracts import (
    contract_digest,
    validate_task_envelope,
    validate_task_id,
)
from control_plane.git_state import GateError, evaluate_preflight
from control_plane.lifecycle import (
    TaskLease,
    TaskStore,
    create_verification_execution_context,
    run_verification_profile,
    task_allows_writer_lease,
)
from control_plane.lockfile import validate_lock
from control_plane.materialization import inspect_tracked_materialization
from control_plane.policy import PolicyError, load_policy, validate_policy
from control_plane.repository import (
    RepositoryError,
    assert_no_external_git_filters,
    discover_repository,
    git_common_dir,
    trusted_git_argv,
    trusted_git_environment,
    trusted_git_executable,
    worktree_git_dir,
)
from control_plane.resource_registry import (
    RegistryError,
    build_inventory,
    load_registry,
    registry_contract_digest,
    validate_policy_references,
    validate_registry,
)
from control_plane.risk_sentinel import evaluate_risk_status
from control_plane.hooks import (
    _safe_read_repository_identity,
    execute_safe_read,
)
from control_plane.host_bridge import (
    HOST_ADAPTER_UNAVAILABLE,
    MACOS_HOOK_SMOKE_ARTIFACTS,
    _smoke_git_head,
    frame_verification_task_context,
    observe_inventory,
    observe_worktree_inventory,
    publish_macos_hook_smoke_receipt,
    run_macos_hook_smoke,
    validate_inventory_observation,
    validate_worktree_inventory_observation,
)
from control_plane.routing import (
    compact_route_manifest,
    resolve_route,
    verify_route,
)
from control_plane.run_workflow import (
    RunStore,
    block_run,
    build_run_summary,
    prepare_run,
    verify_run,
)


def _policy_path(repo: Path, explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        return explicit_path
    return discover_repository(repo) / ".codex" / "project-policy.toml"


def _registry_path(repo: Path, explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        return explicit_path
    return discover_repository(repo) / ".codex" / "resource-registry.toml"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
        raise ValueError(f"E_JSON_INPUT: could not read {path}") from error
    if not isinstance(value, dict):
        raise ValueError("E_JSON_INPUT: top-level JSON value must be an object")
    return value


def _load_validated_policy(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
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
        }

    issues = validate_policy(policy)
    payload = {
        "schema_version": 1,
        "command": "policy-check",
        "ok": not issues,
        "policy": str(path),
        "issues": [asdict(issue) for issue in issues],
        "errors": [],
    }
    return policy, payload


def _render_human(payload: dict[str, Any]) -> str:
    command = str(payload.get("command", "control-plane"))
    if command == "risk-status" and payload.get("status") in {
        "PASS",
        "FAIL",
        "UNKNOWN",
    }:
        status = str(payload["status"])
        dimensions = payload.get("dimensions", {})
        local = (
            dimensions.get("local", {})
            if isinstance(dimensions, dict)
            else {}
        )
        remote = (
            dimensions.get("remote", {})
            if isinstance(dimensions, dict)
            else {}
        )
        facts = payload.get("facts", {})
        interaction = (
            facts.get("interaction", {}) if isinstance(facts, dict) else {}
        )
        profile = (
            facts.get("project_profile", {})
            if isinstance(facts, dict)
            else {}
        )
        commands = (
            interaction.get("commands", [])
            if isinstance(interaction, dict)
            else []
        )
        profiles = (
            profile.get("profiles", [])
            if isinstance(profile, dict)
            else []
        )
        lines = [
            f"{status} risk-status",
            f"local={local.get('status', 'UNKNOWN')}",
            f"remote={remote.get('status', 'UNKNOWN')}",
            "interaction_recommended="
            f"{interaction.get('mode', 'normal')}",
            "interaction_commands="
            + (
                ",".join(str(item) for item in commands)
                if isinstance(commands, list)
                else ""
            ),
            "interaction_message="
            f"{interaction.get('human_message', '')}",
            "automatic_change="
            f"{str(bool(interaction.get('automatic_change'))).lower()}",
            "project_profiles="
            + (
                ",".join(str(item) for item in profiles)
                if isinstance(profiles, list)
                else "unknown"
            ),
        ]
        if isinstance(dimensions, dict):
            for name in ("local", "remote"):
                dimension = dimensions.get(name, {})
                if not isinstance(dimension, dict):
                    continue
                for check in dimension.get("checks", []):
                    lines.append(
                        f"{check.get('code', 'UNKNOWN')} "
                        f"{check.get('message', '')}"
                    )
                for error in dimension.get("errors", []):
                    lines.append(
                        f"{error.get('code', 'UNKNOWN')} "
                        f"{error.get('message', '')}"
                    )
        for error in payload.get("errors", []):
            lines.append(
                f"{error.get('code', 'UNKNOWN')} "
                f"{error.get('message', '')}"
            )
        return "\n".join(lines)
    diagnostic = (
        (
            command == "preflight"
            and payload.get("mode") == "read"
            and any(
                not check.get("ok", False)
                for check in payload.get("checks", [])
            )
        )
        or (
            command == "route-verify"
            and payload.get("authoritative") is False
        )
    )
    status = (
        "DIAGNOSTIC"
        if diagnostic and payload.get("ok")
        else ("PASS" if payload.get("ok") else "FAIL")
    )
    lines = [f"{status} {command}"]

    if command == "route":
        summary = payload.get("summary", {})
        if isinstance(summary, dict):
            lines.append(f"tier={summary.get('tier', 'unknown')}")
            lines.append(
                f"workflow_mode={summary.get('workflow_mode', 'unknown')}"
            )
            project_profile = summary.get("project_profile", {})
            if isinstance(project_profile, dict):
                profiles = project_profile.get("profiles", [])
                rendered_profiles = (
                    ",".join(str(item) for item in profiles)
                    if isinstance(profiles, list)
                    else "unknown"
                )
                lines.append(f"project_profiles={rendered_profiles}")
            profile_mismatch = summary.get("profile_mismatch", [])
            if isinstance(profile_mismatch, list) and profile_mismatch:
                lines.append(
                    "profile_mismatch="
                    + ",".join(str(item) for item in profile_mismatch)
                )
        interaction = payload.get("interaction", {})
        if isinstance(interaction, dict):
            lines.append(
                "interaction_recommended="
                f"{interaction.get('recommended_mode', 'default')}"
            )
            reason_codes = interaction.get("reason_codes", [])
            rendered_reasons = (
                ",".join(str(item) for item in reason_codes)
                if isinstance(reason_codes, list)
                else "unknown"
            )
            lines.append(f"interaction_reasons={rendered_reasons}")
            lines.append(
                f"interaction_action={interaction.get('user_action', '')}"
            )
            lines.append(
                "interaction_automatic_change="
                f"{str(bool(interaction.get('automatic_change'))).lower()}"
            )
            clarification = interaction.get("clarification_gate", {})
            if isinstance(clarification, dict):
                lines.append(
                    "clarification_level="
                    f"{clarification.get('level', 'unknown')}"
                )
                lines.append(
                    "clarification_status="
                    f"{clarification.get('status', 'unknown')}"
                )
                lines.append(
                    "clarification_next_action="
                    f"{clarification.get('next_action', '')}"
                )
                lines.append(
                    "clarification_ready="
                    f"{str(bool(clarification.get('decision_ready'))).lower()}"
                )

    facts = payload.get("facts")
    if isinstance(facts, dict):
        for key in sorted(facts):
            lines.append(f"{key}={facts[key]}")

    for issue in payload.get("issues", []):
        lines.append(
            f"ISSUE {issue.get('code', 'UNKNOWN')} {issue.get('path', '')}: "
            f"{issue.get('message', '')}"
        )
    for error in payload.get("errors", []):
        lines.append(
            f"ERROR {error.get('code', 'UNKNOWN')}: {error.get('message', '')}"
        )
    for warning in payload.get("warnings", []):
        lines.append(
            f"WARNING {warning.get('code', 'UNKNOWN')}: "
            f"{warning.get('message', '')}"
        )

    return "\n".join(lines)


def _emit(payload: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_render_human(payload))
    if (
        payload.get("command") == "risk-status"
        and payload.get("status") == "UNKNOWN"
    ):
        return 2
    return 0 if payload.get("ok") else 1


_MAX_REMOTE_URL_BYTES = 4_096
_MAX_AUTHORIZATION_HEADER_BYTES = 4_096


def _validated_remote_fetch_url(value: str) -> tuple[str, str | None]:
    if (
        not value
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_REMOTE_URL_BYTES
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("remote URL is invalid")
    if Path(value).is_absolute():
        return value, None

    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("remote URL is invalid") from error
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname != hostname.casefold()
        or re.fullmatch(
            r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname, re.ASCII
        )
        is None
        or ".." in hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or port == 0
    ):
        raise ValueError("remote URL is invalid")
    authority = hostname if port is None else f"{hostname}:{port}"
    canonical = f"https://{authority}{parsed.path}"
    if value != canonical:
        raise ValueError("remote URL is not canonical")
    return canonical, f"https://{authority}/"


def _authenticated_git_environment(remote_url: str) -> dict[str, str]:
    """Bind the sole inherited auth channel to one exact HTTPS remote."""

    environment = trusted_git_environment()
    canonical_url, https_origin = _validated_remote_fetch_url(remote_url)
    count = os.environ.get("GIT_CONFIG_COUNT")
    auth_config_names = {
        name
        for name in os.environ
        if name in {"GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS"}
        or name.startswith("GIT_CONFIG_KEY_")
        or name.startswith("GIT_CONFIG_VALUE_")
    }
    if https_origin is None:
        if auth_config_names:
            raise ValueError("remote authentication is unsupported")
        return environment

    exact_key = f"http.{canonical_url}.extraheader"
    if count is None:
        if auth_config_names:
            raise ValueError("remote authentication config is incomplete")
        environment.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": exact_key,
                "GIT_CONFIG_VALUE_0": "",
            }
        )
        return environment

    expected_names = {
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    }
    if count != "1" or auth_config_names != expected_names:
        raise ValueError("remote authentication config is invalid")
    source_key = os.environ["GIT_CONFIG_KEY_0"]
    source_value = os.environ["GIT_CONFIG_VALUE_0"]
    if source_key not in {
        exact_key,
        f"http.{https_origin}.extraheader",
    }:
        raise ValueError("remote authentication config is not bound")
    try:
        encoded_header = source_value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("remote authentication header is invalid") from error
    if (
        not encoded_header
        or len(encoded_header) > _MAX_AUTHORIZATION_HEADER_BYTES
        or "\0" in source_value
        or "\r" in source_value
        or "\n" in source_value
        or any(
            ord(character) < 32 and character != "\t"
            or ord(character) == 127
            for character in source_value
        )
        or re.fullmatch(
            r"(?i:authorization):[ \t]*[^ \t].*", source_value, re.ASCII
        )
        is None
    ):
        raise ValueError("remote authentication header is invalid")
    environment.update(
        {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": exact_key,
            "GIT_CONFIG_VALUE_0": "",
            "GIT_CONFIG_KEY_1": exact_key,
            "GIT_CONFIG_VALUE_1": source_value,
        }
    )
    return environment


def _remote_fetch_url(repo: Path, remote: str) -> str:
    completed = _run_local_git(repo, ("remote", "get-url", remote), text=True)
    remote_url = completed.stdout.rstrip("\n")
    if (
        completed.returncode != 0
        or not remote_url
        or "\n" in remote_url
        or completed.stdout not in {remote_url, f"{remote_url}\n"}
    ):
        raise ValueError("remote URL is unavailable")
    return _validated_remote_fetch_url(remote_url)[0]


def _refresh_remote_base(
    repo: Path, remote: str, base_branch: str
) -> GateError | None:
    try:
        remote_url = _remote_fetch_url(repo, remote)
        environment = _authenticated_git_environment(remote_url)
        fetch_config: tuple[str, ...] = (
            "-c",
            "credential.helper=",
            "-c",
            "core.askPass=",
            "-c",
            "http.proxy=",
            "-c",
            "http.cookieFile=",
            "-c",
            "http.saveCookies=false",
            "-c",
            "http.followRedirects=false",
            "-c",
            "http.sslVerify=true",
            "-c",
            "protocol.ext.allow=never",
        )
        if remote_url.startswith("https://"):
            fetch_config += (
                "-c",
                f"http.{remote_url}.proxy=",
                "-c",
                f"http.{remote_url}.cookieFile=",
            )
        completed = subprocess.run(
            trusted_git_argv(
                repo,
                (
                    *fetch_config,
                    "fetch",
                    "--no-tags",
                    remote_url,
                    f"+refs/heads/{base_branch}:refs/remotes/{remote}/{base_branch}",
                ),
            ),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/git", "fetch"],
            returncode=128,
            stdout="",
            stderr="",
        )
    if completed.returncode == 0:
        return None
    return GateError(
        "E_FETCH_FAILED",
        f"Could not refresh {remote}/{base_branch}; rerun without --refresh for diagnosis.",
    )


def _run_local_git(
    repo: Path, arguments: tuple[str, ...], *, text: bool
) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(
        trusted_git_argv(repo, arguments),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        env=trusted_git_environment(),
        stdin=subprocess.DEVNULL,
        timeout=10,
    )


def _git_current_branch(repo: Path) -> str:
    try:
        completed = _run_local_git(
            repo,
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            text=True,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "E_STATE_BRANCH: task lifecycle requires a named branch"
        ) from error
    branch = completed.stdout.strip()
    if completed.returncode != 0 or not branch:
        raise ValueError("E_STATE_BRANCH: task lifecycle requires a named branch")
    return branch


def _git_changed_paths(repo: Path) -> list[str]:
    try:
        assert_no_external_git_filters(repo)
        changed = _run_local_git(
            repo,
            (
                "diff",
                "--no-renames",
                "--name-only",
                "-z",
                "HEAD",
            ),
            text=False,
        )
        if changed.returncode != 0:
            raise ValueError("changed-path diff failed")
        untracked = _run_local_git(
            repo,
            ("ls-files", "--others", "--exclude-standard", "-z"),
            text=False,
        )
        if untracked.returncode != 0:
            raise ValueError("untracked-path inventory failed")
        values = set()
        for payload in (changed.stdout, untracked.stdout):
            values.update(
                item.decode("utf-8", errors="strict")
                for item in payload.split(b"\0")
                if item
            )
    except (
        OSError,
        UnicodeDecodeError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        raise ValueError(
            "E_LEASE_SCOPE: could not enumerate changed paths"
        ) from error
    return sorted(values)


def command_policy_check(arguments: argparse.Namespace) -> int:
    _, payload = _load_validated_policy(arguments.policy)
    return _emit(payload, arguments.json)


def command_preflight(arguments: argparse.Namespace) -> int:
    policy_path = _policy_path(arguments.repo, arguments.policy)
    policy, policy_payload = _load_validated_policy(policy_path)
    if policy is None or not policy_payload["ok"]:
        payload = {
            "schema_version": 1,
            "command": "preflight",
            "ok": False,
            "mode": arguments.mode,
            "offline": not arguments.refresh,
            "facts": {},
            "checks": [],
            "issues": policy_payload.get("issues", []),
            "errors": policy_payload.get("errors", []),
        }
        return _emit(payload, arguments.json)

    fetch_error = None
    if arguments.refresh:
        fetch_error = _refresh_remote_base(
            arguments.repo,
            str(policy["git"]["remote"]),
            str(policy["git"]["base_branch"]),
        )

    result = evaluate_preflight(arguments.repo, policy, arguments.mode)
    payload = result.to_dict()
    payload["offline"] = not arguments.refresh
    if (
        arguments.mode == "write"
        and payload["facts"].get("dirty") is True
        and arguments.task_id
        and arguments.session_id
    ):
        try:
            root = discover_repository(arguments.repo)
            state_dir = worktree_git_dir(root)
            store = TaskStore(state_dir)
            before_task = store.status(arguments.task_id)
            branch = payload["facts"].get("branch")
            if (
                before_task.get("branch") != branch
                or not task_allows_writer_lease(before_task)
            ):
                raise ValueError(
                    "E_STATE_CONTINUATION: task is not an active writer "
                    "on the current branch"
                )
            TaskLease.validate(
                state_dir,
                task_id=arguments.task_id,
                worktree=str(root),
                branch=str(branch),
                session_id=arguments.session_id,
                policy_digest=contract_digest(policy),
                changed_paths=_git_changed_paths(root),
            )
            after_task = store.status(arguments.task_id)
            if (
                contract_digest(before_task) != contract_digest(after_task)
                or after_task.get("branch") != branch
                or not task_allows_writer_lease(after_task)
            ):
                raise ValueError(
                    "E_STATE_CONTINUATION: task changed during lease validation"
                )
            payload["errors"] = [
                error
                for error in payload["errors"]
                if error.get("code") != "E_GIT_DIRTY"
            ]
            payload["facts"]["lease_continuation"] = True
            payload["ok"] = not payload["errors"]
        except (RepositoryError, OSError, ValueError) as error:
            code = (
                "E_STATE_CONTINUATION"
                if isinstance(error, OSError)
                else str(error).split(":", 1)[0]
            )
            payload["errors"].append(
                {
                    "code": code,
                    "message": str(error),
                }
            )
            payload["facts"]["lease_continuation"] = False
            payload["ok"] = False
    if fetch_error is not None:
        payload["errors"].insert(0, asdict(fetch_error))
        payload["ok"] = False

    return _emit(payload, arguments.json)


def command_doctor(arguments: argparse.Namespace) -> int:
    policy_path = _policy_path(arguments.repo, arguments.policy)
    _, policy_payload = _load_validated_policy(policy_path)
    try:
        trusted_git_executable()
    except OSError:
        git_available = False
    else:
        git_available = True
    python_compatible = sys.version_info >= (3, 11)

    git_repository = False
    if git_available:
        try:
            completed = _run_local_git(
                arguments.repo,
                ("rev-parse", "--is-inside-work-tree"),
                text=True,
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            completed = subprocess.CompletedProcess(
                args=["/usr/bin/git", "rev-parse"],
                returncode=128,
                stdout="",
                stderr="",
            )
        git_repository = (
            completed.returncode == 0 and completed.stdout.strip() == "true"
        )

    facts = {
        "git_available": git_available,
        "git_repository": git_repository,
        "policy_valid": bool(policy_payload["ok"]),
        "python_compatible": python_compatible,
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "registry_valid": None,
        "lock_valid": None,
        "tracked_files_materialized": None,
        "dataless_tracked_files": None,
        "materialization_status": "UNKNOWN",
    }
    errors: list[dict[str, str]] = []
    if not git_available:
        errors.append({"code": "E_DOCTOR_GIT", "message": "Git is not available."})
    if not python_compatible:
        errors.append(
            {
                "code": "E_DOCTOR_PYTHON",
                "message": "Python 3.11 or newer is required.",
            }
        )
    if not git_repository:
        errors.append(
            {
                "code": "E_GIT_NOT_REPOSITORY",
                "message": "The target is not inside a Git worktree.",
            }
        )
    if not policy_payload["ok"]:
        errors.extend(policy_payload.get("errors", []))
        for issue in policy_payload.get("issues", []):
            errors.append(
                {
                    "code": issue["code"],
                    "message": f"{issue['path']}: {issue['message']}",
                }
            )
    if arguments.policy is None and git_repository:
        try:
            root = discover_repository(arguments.repo)
            materialization = inspect_tracked_materialization(root)
            facts["tracked_files_materialized"] = materialization.ok
            facts["dataless_tracked_files"] = len(
                materialization.dataless_paths
            )
            facts["materialization_status"] = materialization.status
            if not materialization.ok:
                errors.append(
                    {
                        "code": materialization.error_code
                        or "E_MATERIALIZATION_UNKNOWN",
                        "message": (
                            "Tracked file materialization is incomplete or "
                            "could not be proved."
                        ),
                    }
                )
            registry = load_registry(_registry_path(root, None))
            registry_issues = validate_registry(registry)
            policy, _ = _load_validated_policy(_policy_path(root, None))
            if policy is not None:
                registry_issues.extend(
                    validate_policy_references(policy, registry)
                )
            lock_issues = validate_lock(root)
            facts["registry_valid"] = not registry_issues
            facts["lock_valid"] = not lock_issues
            errors.extend(
                {
                    "code": issue.code,
                    "message": f"{issue.path}: {issue.message}",
                }
                for issue in [*registry_issues, *lock_issues]
            )
        except (RepositoryError, RegistryError) as error:
            errors.append({"code": error.code, "message": error.message})

    payload = {
        "schema_version": 1,
        "command": "doctor",
        "ok": not errors,
        "facts": facts,
        "errors": errors,
    }
    return _emit(payload, arguments.json)


def command_registry_check(arguments: argparse.Namespace) -> int:
    try:
        registry = load_registry(arguments.registry)
        issues = validate_registry(registry)
    except RegistryError as error:
        return _emit(
            {
                "schema_version": 1,
                "command": "registry-check",
                "ok": False,
                "issues": [],
                "errors": [{"code": error.code, "message": error.message}],
            },
            arguments.json,
        )
    if arguments.policy:
        policy, policy_payload = _load_validated_policy(arguments.policy)
        if policy is None or not policy_payload["ok"]:
            return _emit({**policy_payload, "command": "registry-check"}, arguments.json)
        issues.extend(validate_policy_references(policy, registry))
    payload = {
        "schema_version": 1,
        "command": "registry-check",
        "ok": not issues,
        "issues": [asdict(issue) for issue in issues],
        "errors": [],
    }
    return _emit(payload, arguments.json)


def command_inventory(arguments: argparse.Namespace) -> int:
    try:
        root = discover_repository(arguments.repo)
        registry = load_registry(_registry_path(root, arguments.registry))
        issues = validate_registry(registry)
        if issues:
            return _emit(
                {
                    "schema_version": 1,
                    "command": "inventory",
                    "ok": False,
                    "issues": [asdict(issue) for issue in issues],
                    "errors": [],
                },
                arguments.json,
            )
        payload = build_inventory(registry, root)
        payload.update({"command": "inventory", "ok": True})
        return _emit(payload, arguments.json)
    except (RepositoryError, RegistryError) as error:
        return _emit(
            {
                "schema_version": 1,
                "command": "inventory",
                "ok": False,
                "issues": [],
                "errors": [{"code": error.code, "message": error.message}],
            },
            arguments.json,
        )


def command_route(arguments: argparse.Namespace) -> int:
    try:
        root = discover_repository(arguments.repo)
        task = _read_json(arguments.task)
        task_issues = validate_task_envelope(task)
        policy = load_policy(_policy_path(root, arguments.policy))
        registry = load_registry(_registry_path(root, arguments.registry))
        issues = validate_policy(policy) + validate_registry(registry) + validate_policy_references(policy, registry)
        if task_issues or issues:
            payload = {
                "schema_version": 1,
                "command": "route",
                "ok": False,
                "issues": [asdict(item) for item in [*task_issues, *issues]],
                "errors": [],
            }
            return _emit(payload, arguments.json)
        invocation_id = f"route-{uuid4().hex}"
        observation = observe_inventory(
            registry,
            root,
            root,
            contract_digest(task),
            invocation_id,
            clock=time.monotonic,
            ttl_seconds=30,
        )
        inventory = validate_inventory_observation(
            observation,
            expected_repo=root,
            expected_worktree=root,
            expected_registry_digest=registry_contract_digest(registry),
            expected_task_digest=contract_digest(task),
            expected_invocation_id=invocation_id,
            clock=time.monotonic,
        )
        trusted_decision = resolve_route(
            task,
            policy,
            registry,
            inventory,
            mode=arguments.mode,
            host_capability=HOST_ADAPTER_UNAVAILABLE,
        )
        manifest = compact_route_manifest(trusted_decision)
        payload = dict(trusted_decision)
        state_dir = worktree_git_dir(root)
        task_id = str(task["task_id"])
        task_state_path = (
            state_dir
            / "codex-control-plane"
            / "tasks"
            / f"{task_id}.json"
        )
        lease_path = (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{task_id}.json"
        )
        if task_state_path.is_file() and lease_path.is_file():
            store = TaskStore(state_dir)
            try:
                state = store.status(task_id)
            except ValueError as error:
                if not str(error).startswith("E_FOREIGN_RUNTIME_STATE:"):
                    raise
            else:
                if lease_path.is_symlink():
                    raise ValueError(
                        "M_METRIC_BINDING: active lease path is unsafe"
                    )
                lease = _read_json(lease_path)
                lease_semantic = {
                    key: value
                    for key, value in lease.items()
                    if key != "lease_digest"
                }
                if (
                    state.get("task_digest")
                    != payload["facts"]["task_digest"]
                    or lease.get("task_id") != task_id
                    or lease.get("worktree") != str(root)
                    or lease.get("session_id") is None
                    or lease.get("lease_digest")
                    != contract_digest(lease_semantic)
                ):
                    raise ValueError(
                        "M_METRIC_BINDING: active task does not match route"
                    )
                store.record_context_metrics(
                    task_id,
                    task_digest=payload["facts"]["task_digest"],
                    session_id=str(lease["session_id"]),
                    invocation_id=invocation_id,
                    subject_digest=payload["decision_digest"],
                    runtime_metrics={
                        "router_manifest_bytes": len(
                            manifest.encode("utf-8")
                        ),
                        "context_units_selected": int(
                            payload["summary"][
                                "selected_context_units"
                            ]
                        ),
                        "tool_use_id": None,
                    },
                )
        payload["command"] = "route"
        return _emit(payload, arguments.json)
    except (RepositoryError, PolicyError, RegistryError, ValueError) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        message = getattr(error, "message", str(error))
        return _emit(
            {
                "schema_version": 1,
                "command": "route",
                "ok": False,
                "issues": [],
                "errors": [{"code": code, "message": message}],
            },
            arguments.json,
        )


def command_route_verify(arguments: argparse.Namespace) -> int:
    try:
        payload = verify_route(
            _read_json(arguments.decision),
            _read_json(arguments.receipt),
            mode=arguments.mode,
        )
        return _emit(payload, arguments.json)
    except ValueError as error:
        return _emit(
            {
                "schema_version": 1,
                "command": "route-verify",
                "ok": False,
                "errors": [{"code": "E_JSON_INPUT", "message": str(error)}],
            },
            arguments.json,
        )


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _current_branch(repository: Path) -> str:
    try:
        completed = _run_local_git(
            repository, ("branch", "--show-current"), text=True
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "E_RUN_GIT: current branch is unavailable"
        ) from error
    branch = completed.stdout.strip()
    if completed.returncode != 0 or not branch:
        raise ValueError("E_RUN_GIT: current branch is unavailable")
    return branch


def _verification_head(repository: Path) -> str:
    try:
        completed = _run_local_git(
            repository, ("rev-parse", "HEAD"), text=True
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "E_VERIFICATION_UNKNOWN: repository HEAD is unavailable"
        ) from error
    head = completed.stdout.strip()
    if completed.returncode != 0 or not head:
        raise ValueError(
            "E_VERIFICATION_UNKNOWN: repository HEAD is unavailable"
        )
    return head


def _resolve_current_task_route(
    *,
    root: Path,
    task: dict[str, Any],
    policy_path: Path,
    registry_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    task_issues = validate_task_envelope(task)
    policy = load_policy(policy_path)
    registry = load_registry(registry_path)
    issues = [
        *task_issues,
        *validate_policy(policy),
        *validate_registry(registry),
        *validate_policy_references(policy, registry),
    ]
    if issues:
        raise ValueError(f"E_RUN_INPUT: {issues[0].code}")
    invocation_id = f"run-prepare-{uuid4().hex}"
    observation = observe_inventory(
        registry,
        root,
        root,
        contract_digest(task),
        invocation_id,
        clock=time.monotonic,
        ttl_seconds=30,
    )
    inventory = validate_inventory_observation(
        observation,
        expected_repo=root,
        expected_worktree=root,
        expected_registry_digest=registry_contract_digest(registry),
        expected_task_digest=contract_digest(task),
        expected_invocation_id=invocation_id,
        clock=time.monotonic,
    )
    decision = resolve_route(
        task,
        policy,
        registry,
        inventory,
        mode="enforce",
        host_capability=HOST_ADAPTER_UNAVAILABLE,
    )
    return policy, decision


def command_run(arguments: argparse.Namespace) -> int:
    try:
        root = discover_repository(arguments.repo)
        state_dir = worktree_git_dir(root)
        if arguments.run_action == "prepare":
            task = _read_json(arguments.task)
            policy, decision = _resolve_current_task_route(
                root=root,
                task=task,
                policy_path=_policy_path(root, arguments.policy),
                registry_path=_registry_path(root, arguments.registry),
            )
            result = prepare_run(
                task=task,
                decision=decision,
                repository=root,
                policy=policy,
                session_id=arguments.session_id,
                prepared_at=_utc_timestamp(),
            )
            payload = {
                "schema_version": 1,
                "command": "run-prepare",
                "ok": True,
                **result,
            }
        elif arguments.run_action == "verify":
            result = verify_run(
                repository=root,
                task_id=arguments.task_id,
                observed_at=_utc_timestamp(),
            )
            payload = {
                "schema_version": 1,
                "command": "run-verify",
                "ok": result["summary"]["gate_status"] == "PASS",
                **result,
            }
        else:
            run_store = RunStore(state_dir)
            plan = run_store.load_plan(arguments.task_id)
            task_store = TaskStore(state_dir)
            state = task_store.status(arguments.task_id)
            if arguments.run_action == "block" and state["state"] != "blocked":
                state = block_run(
                    repository=root,
                    task_id=arguments.task_id,
                    reason_code=arguments.reason,
                )
            attempts = run_store.attempts(arguments.task_id)
            latest = attempts[-1] if attempts else None
            summary = build_run_summary(
                run_plan=plan,
                head=str(plan["head"]),
                lifecycle_state=str(state["state"]),
                attempt_count=len(attempts),
                gate_statuses=(
                    (str(latest["status"]),) if latest is not None else ()
                ),
                gate_receipt_digests=(
                    tuple(str(item) for item in latest["gate_receipt_digests"])
                    if latest is not None
                    else ()
                ),
                review_result_digest=None,
                blocked_reason_code=(
                    str(state.get("block_reason"))
                    if state["state"] == "blocked"
                    and validate_task_id(state.get("block_reason"))
                    else None
                ),
                observed_at=_utc_timestamp(),
            )
            payload = {
                "schema_version": 1,
                "command": f"run-{arguments.run_action}",
                "ok": True,
                "run_plan": plan,
                "task": state,
                "attempts": attempts,
                "summary": summary,
            }
        return _emit(payload, arguments.json)
    except (
        RepositoryError,
        PolicyError,
        RegistryError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        return _emit(
            {
                "schema_version": 1,
                "command": f"run-{arguments.run_action}",
                "ok": False,
                "errors": [{"code": code, "message": str(error)}],
            },
            arguments.json,
        )


def command_report(arguments: argparse.Namespace) -> int:
    try:
        root = discover_repository(arguments.repo)
        state_dir = worktree_git_dir(root)
        match = re.fullmatch(r"([1-9][0-9]*)d", arguments.since)
        if match is None:
            raise ValueError("E_REPORT_SINCE: expected a positive day window such as 30d")
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(match.group(1)))
        runs_root = state_dir / "codex-control-plane" / "runs"
        rows: list[tuple[str, str, int, str]] = []
        if runs_root.exists():
            if runs_root.is_symlink():
                raise ValueError("E_RUN_STATE: run directory is unsafe")
            for path in sorted(runs_root.iterdir()):
                if path.is_symlink() or not path.is_dir():
                    raise ValueError("E_RUN_STATE: run entry is unsafe")
                store = RunStore(state_dir)
                plan = store.load_plan(path.name)
                prepared = datetime.fromisoformat(
                    str(plan["prepared_at"]).replace("Z", "+00:00")
                )
                if prepared < cutoff:
                    continue
                attempts = store.attempts(path.name)
                task = TaskStore(state_dir).status(path.name)
                gate = str(attempts[-1]["status"]) if attempts else "UNKNOWN"
                rows.append((path.name, str(task["state"]), len(attempts), gate))
        lines = [
            f"# Control Plane report ({arguments.since})",
            "",
            f"Runs: {len(rows)}",
            "",
            "| Task | Lifecycle | Attempts | Gates |",
            "|---|---:|---:|---:|",
        ]
        lines.extend(f"| {task} | {state} | {attempts} | {gate} |" for task, state, attempts, gate in rows)
        print("\n".join(lines))
        return 0
    except (RepositoryError, ValueError, OSError) as error:
        print(f"FAIL report\nERROR {str(error).split(':', 1)[0]}: {error}")
        return 1


def command_risk_status(arguments: argparse.Namespace) -> int:
    """Render local risk from a validated installed anchor when available."""

    try:
        root = discover_repository(arguments.repo)
        if (
            arguments.lease_session_id is not None
            and arguments.task_id is None
        ):
            raise ValueError(
                "RS_LOCAL_LEASE_TASK: --lease-session-id requires --task-id"
            )
        decision_hint = (
            _read_json(arguments.decision)
            if arguments.decision is not None
            else None
        )
        task_state: dict[str, Any] | None = None
        if arguments.task_id is not None:
            try:
                task_state = TaskStore(worktree_git_dir(root)).status(
                    arguments.task_id
                )
            except ValueError:
                task_state = {
                    "task_id": arguments.task_id,
                    "_unobserved": True,
                }
        candidate_status = "not_provided"
        candidate_digest = None
        if arguments.policy is not None:
            candidate, candidate_payload = _load_validated_policy(
                arguments.policy
            )
            candidate_status = (
                "valid_hint"
                if candidate is not None and candidate_payload["ok"]
                else "invalid_hint"
            )
            if candidate is not None:
                candidate_digest = contract_digest(candidate)
        governing_policy = None
        governing_source = "unavailable_pending_installed_manifest"
        try:
            manifest_digest = _installed_runtime_manifest_digest()
        except ValueError:
            try:
                manifest_digest = _configured_installed_manifest_digest(root)
            except ValueError:
                manifest_digest = None
        if manifest_digest is not None:
            invocation_id = f"risk-installed-{uuid4().hex}"
            session_id = f"risk-session-{uuid4().hex}"
            try:
                from control_plane.git_guards import (
                    load_protected_git_policy,
                    observe_installed_policy_source,
                    validate_installed_policy_source,
                )
                from control_plane.host_bridge import (
                    load_governing_local_policy,
                )

                protected = load_protected_git_policy(
                    canonical_repo=root,
                    common_git_dir=git_common_dir(root),
                    installed_manifest_digest=manifest_digest,
                    invocation_id=invocation_id,
                    clock=time.monotonic,
                )
                observation = observe_installed_policy_source(
                    protected_policy=protected,
                    canonical_repo=root,
                    expected_manifest_digest=manifest_digest,
                    session_id=session_id,
                    invocation_id=invocation_id,
                    clock=time.monotonic,
                    ttl_seconds=30.0,
                )
                validated = validate_installed_policy_source(
                    observation,
                    expected_repository_identity=root,
                    expected_manifest_digest=manifest_digest,
                    expected_session_id=session_id,
                    expected_invocation_id=invocation_id,
                    clock=time.monotonic,
                )
                governing_policy = load_governing_local_policy(
                    canonical_repo=root,
                    governing_base_observation=validated,
                    expected_invocation_id=invocation_id,
                    clock=time.monotonic,
                )
                governing_source = "installed_manifest"
            except (OSError, TypeError, ValueError):
                governing_source = "installed_manifest_invalid"
        status = evaluate_risk_status(
            root,
            governing_policy,
            task_state=task_state,
            route_decision_hint=decision_hint,
            local_lease_session_id=arguments.lease_session_id,
        )
        payload = status.to_dict()
        payload["facts"]["governing_policy_source"] = (
            governing_source
        )
        payload["facts"]["candidate_policy_status"] = candidate_status
        payload["facts"]["candidate_policy_digest"] = candidate_digest
        payload["facts"]["serialized_decision_authoritative"] = False
        payload["facts"]["automatic_change"] = False
        return _emit(payload, arguments.json)
    except (RepositoryError, ValueError, OSError) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        payload = {
            "schema_version": 1,
            "command": "risk-status",
            "ok": False,
            "status": "FAIL",
            "dimensions": {
                "local": {"status": "FAIL", "checks": [], "errors": []},
                "remote": {"status": "UNKNOWN", "checks": [], "errors": []},
            },
            "facts": {},
            "errors": [{"code": code, "message": str(error)}],
        }
        return _emit(payload, arguments.json)


def command_task(arguments: argparse.Namespace) -> int:
    try:
        root = discover_repository(arguments.repo)
        state_dir = worktree_git_dir(root)
        store = TaskStore(state_dir)
        current_branch = (
            _git_current_branch(root)
            if arguments.task_action != "status"
            else None
        )
        if arguments.task_action == "lease-release":
            result = TaskLease.release(
                git_common_dir(root),
                state_dir,
                task_id=arguments.task_id,
                worktree=arguments.worktree,
                branch=arguments.branch,
                session_id=arguments.session_id,
                policy_digest=arguments.policy_digest,
                lease_digest=arguments.lease_digest,
            )
        elif arguments.task_action == "start":
            if arguments.branch != current_branch:
                raise ValueError(
                    "E_STATE_BRANCH: declared branch differs from current branch"
                )
            result = store.start(
                arguments.task_id,
                outcome=arguments.outcome,
                branch=arguments.branch,
                task_digest=arguments.task_digest,
                decision_digest=arguments.decision_digest,
            )
            if arguments.session_id:
                policy = load_policy(_policy_path(root, arguments.policy))
                TaskLease.acquire(
                    state_dir,
                    task_id=arguments.task_id,
                    worktree=str(root),
                    branch=arguments.branch,
                    session_id=arguments.session_id,
                    paths=arguments.scope_path or ["."],
                    policy_digest=contract_digest(policy),
                )
        elif arguments.task_action == "status":
            result = store.status(arguments.task_id)
        elif arguments.task_action == "resume":
            result = store.resume(
                arguments.task_id, current_branch=str(current_branch)
            )
        elif arguments.task_action == "transition":
            result = store.transition(
                arguments.task_id,
                arguments.state,
                reason=arguments.reason,
                evidence=None,
                current_branch=str(current_branch),
            )
        else:
            result = store.close(
                arguments.task_id, current_branch=str(current_branch)
            )
        return _emit(
            {
                "schema_version": 1,
                "command": f"task-{arguments.task_action}",
                "ok": True,
                "task": result,
            },
            arguments.json,
        )
    except (RepositoryError, ValueError) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        return _emit(
            {
                "schema_version": 1,
                "command": f"task-{arguments.task_action}",
                "ok": False,
                "errors": [{"code": code, "message": str(error)}],
            },
            arguments.json,
        )


def command_safe_read(arguments: argparse.Namespace) -> int:
    """Execute a closed local read against one explicit registered worktree."""

    try:
        root, _, common_dir = _safe_read_repository_identity(
            arguments.repo
        )
        invocation_id = f"safe-read-{uuid4().hex}"
        observation = observe_worktree_inventory(
            canonical_common_git_dir=common_dir,
            invocation_id=invocation_id,
            clock=time.monotonic,
            ttl_seconds=30,
            max_output_bytes=1_048_576,
        )
        inventory = validate_worktree_inventory_observation(
            observation,
            expected_common_git_dir=common_dir,
            expected_invocation_id=invocation_id,
            clock=time.monotonic,
        )
        argv = tuple(arguments.argv)
        if argv and argv[0] == "--":
            argv = argv[1:]
        result = execute_safe_read(
            argv,
            root=root,
            worktree_inventory=inventory,
            timeout_seconds=arguments.timeout,
            output_limit_bytes=arguments.output_limit,
        )
    except (RepositoryError, OSError, ValueError) as error:
        code = str(error).split(":", 1)[0]
        print(f"{code}: safe-read precondition failed", file=sys.stderr)
        return 126
    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.flush()
    if result.stderr:
        sys.stderr.buffer.write(result.stderr)
        sys.stderr.buffer.flush()
    if result.status == "completed":
        return int(result.exit_code)
    if result.status == "timeout":
        print("E_SAFE_READ_TIMEOUT: bounded read timed out", file=sys.stderr)
        return 124
    if result.status == "truncated":
        print("E_SAFE_READ_TRUNCATED: bounded read exceeded output cap", file=sys.stderr)
        return 125
    print("E_SAFE_READ_ARGV: rejected closed argv", file=sys.stderr)
    return 126


def command_verification_run(arguments: argparse.Namespace) -> int:
    try:
        root = discover_repository(arguments.repo)
        state_dir = worktree_git_dir(root)
        store = TaskStore(state_dir)
        task = store.status(arguments.task_id)
        lease_path = (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{arguments.task_id}.json"
        )
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
        head = _verification_head(root)
        context = create_verification_execution_context(
            task_context=task,
            lease=lease,
            canonical_repo=root,
            expected_head=head,
            session_id=str(task.get("session_id", "")),
            dedicated_temp_root=(
                state_dir
                / "codex-control-plane"
                / "verification-temp"
                / arguments.task_id
            ),
            clock=time.monotonic,
        )
        receipt = run_verification_profile(
            context=context,
            task_store=store,
            expected_generation=int(task.get("generation", -1)),
            clock=time.monotonic,
        )
        return _emit(
            {
                "schema_version": 1,
                "command": "verification-run",
                "ok": True,
                "receipt": asdict(receipt),
            },
            arguments.json,
        )
    except (
        RepositoryError,
        ValueError,
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        return _emit(
            {
                "schema_version": 1,
                "command": "verification-run",
                "ok": False,
                "errors": [{"code": code, "message": str(error)}],
            },
            arguments.json,
        )


def command_hook_smoke(arguments: argparse.Namespace) -> int:
    """Run and publish the closed macOS hook smoke in this process."""

    try:
        root, state_dir, _ = _safe_read_repository_identity(
            arguments.repo
        )
        store = TaskStore(state_dir)
        task = store.status(arguments.task_id)
        lease_path = (
            state_dir
            / "codex-control-plane"
            / "leases"
            / f"{arguments.task_id}.json"
        )
        lease = json.loads(lease_path.read_text(encoding="utf-8"))
        head = _smoke_git_head(root)
        if not head:
            raise ValueError(
                "E_MACOS_SMOKE_BINDING: repository HEAD is unavailable"
            )
        prior_path = os.environ.get("PATH")
        os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        try:
            execution_context = create_verification_execution_context(
                task_context=task,
                lease=lease,
                canonical_repo=root,
                expected_head=head,
                session_id=str(task.get("session_id", "")),
                dedicated_temp_root=(
                    Path(tempfile.gettempdir()).resolve()
                    / "control-plane-hook-smoke"
                    / arguments.task_id
                ),
                clock=time.monotonic,
            )
        finally:
            if prior_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = prior_path
        generation = int(task.get("generation", -1))
        task_context = frame_verification_task_context(
            task_store=store,
            execution_context=execution_context,
            expected_generation=generation,
        )
        artifact_digests = {
            name: f"sha256:{sha256((root / relative).read_bytes()).hexdigest()}"
            for name, relative in MACOS_HOOK_SMOKE_ARTIFACTS.items()
        }
        completed = run_macos_hook_smoke(
            canonical_repo=root,
            expected_head=head,
            expected_artifact_digests=artifact_digests,
            session_id=execution_context.session_id,
            invocation_id=f"hook-smoke-{uuid4().hex}",
            dedicated_temp_root=execution_context.dedicated_temp_root,
            clock=time.monotonic,
            timeout_seconds=120.0,
        )
        publication = publish_macos_hook_smoke_receipt(
            completed,
            task_store=store,
            task_context=task_context,
            expected_generation=generation,
        )
        return _emit(
            {
                "schema_version": 1,
                "command": "hook-smoke",
                "ok": True,
                "receipt": asdict(publication.receipt),
                "task_generation": publication.task_context.generation,
            },
            arguments.json,
        )
    except (
        RepositoryError,
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        return _emit(
            {
                "schema_version": 1,
                "command": "hook-smoke",
                "ok": False,
                "errors": [{"code": code, "message": str(error)}],
            },
            arguments.json,
        )


def command_adopt(arguments: argparse.Namespace) -> int:
    try:
        functions = {
            "plan": lambda: adoption_plan(
                arguments.source,
                arguments.target,
                base_branch=arguments.base_branch,
                remote=arguments.remote,
            ),
            "apply": lambda: adoption_apply(_read_json(arguments.plan)),
            "verify": lambda: adoption_verify(arguments.target),
            "status": lambda: adoption_status(arguments.target),
            "rollback": lambda: adoption_rollback(arguments.target),
        }
        return _emit(functions[arguments.adopt_action](), arguments.json)
    except (RepositoryError, ValueError, OSError) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        return _emit(
            {
                "schema_version": 1,
                "command": f"adopt-{arguments.adopt_action}",
                "ok": False,
                "errors": [{"code": code, "message": str(error)}],
            },
            arguments.json,
        )


def command_upgrade(arguments: argparse.Namespace) -> int:
    try:
        payload = (
            upgrade_plan(
                arguments.source,
                arguments.target,
                base_branch=arguments.base_branch,
                remote=arguments.remote,
            )
            if arguments.upgrade_action == "plan"
            else upgrade_apply(_read_json(arguments.plan))
        )
        return _emit(payload, arguments.json)
    except (RepositoryError, ValueError, OSError) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        return _emit(
            {
                "schema_version": 2,
                "command": f"upgrade-{arguments.upgrade_action}",
                "ok": False,
                "errors": [{"code": code, "message": str(error)}],
            },
            arguments.json,
        )


def _read_pre_push_updates(
    stream: Any, *, limit: int = 1_048_576
) -> list[tuple[str, str, str, str]]:
    """Read one complete, bounded pre-push input stream."""

    try:
        payload = stream.read(limit + 1)
    except (AttributeError, OSError, ValueError) as error:
        raise ValueError(
            "GG_INPUT_INVALID: pre-push input could not be read"
        ) from error
    if not isinstance(payload, bytes) or len(payload) > limit:
        raise ValueError(
            "GG_INPUT_INVALID: pre-push input exceeds the 1 MiB limit"
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError(
            "GG_INPUT_INVALID: pre-push input must be UTF-8"
        ) from error
    updates: list[tuple[str, str, str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError(
                "GG_INPUT_INVALID: each pre-push line requires four fields"
            )
        updates.append((fields[0], fields[1], fields[2], fields[3]))
    return updates


def _installed_runtime_manifest_digest() -> str:
    install_root = Path(__file__).resolve(strict=True).parent.parent
    digest = install_root.name
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: CLI is not running from an "
            "installed digest snapshot"
        )
    return digest


def _configured_installed_manifest_digest(repo: Path) -> str:
    try:
        completed = _run_local_git(
            repo,
            ("config", "--local", "--get-all", "core.hooksPath"),
            text=True,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: hook config is not observable"
        ) from error
    values = [
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    ]
    if completed.returncode != 0 or len(values) != 1:
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: one managed hook path is required"
        )
    hooks_path = Path(values[0])
    expected_parent = (
        git_common_dir(repo) / "codex-control-plane" / "installs"
    )
    if (
        not hooks_path.is_absolute()
        or hooks_path.name != "git-hooks"
        or hooks_path.parent.parent != expected_parent
    ):
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: managed hook path is invalid"
        )
    digest = hooks_path.parent.name
    if (
        not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise ValueError(
            "GG_INSTALLED_POLICY_INVALID: managed manifest digest is invalid"
        )
    return digest


def command_git_guard(arguments: argparse.Namespace) -> int:
    from control_plane.git_guards import (
        guard_pre_commit,
        guard_pre_push,
        load_protected_git_policy,
    )

    event = arguments.guard_action
    try:
        repo = discover_repository(arguments.repo)
        protected = load_protected_git_policy(
            canonical_repo=repo,
            common_git_dir=git_common_dir(repo),
            installed_manifest_digest=_installed_runtime_manifest_digest(),
            invocation_id=f"git-guard-{uuid4().hex}",
            clock=time.monotonic,
        )
        if event == "pre-commit":
            payload = guard_pre_commit(repo, protected)
        else:
            updates = _read_pre_push_updates(sys.stdin.buffer)
            payload = guard_pre_push(
                repo,
                protected,
                remote_name=arguments.remote_name,
                remote_url=arguments.remote_url,
                updates=updates,
            )
    except (RepositoryError, ValueError, OSError) as error:
        code = getattr(error, "code", str(error).split(":", 1)[0])
        payload = {
            "schema_version": 1,
            "command": "git-guard",
            "ok": False,
            "event": event,
            "errors": [{"code": code, "message": str(error)}],
            "warnings": [],
        }
    return _emit(payload, arguments.json)


def _add_output_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json", action="store_true", help="Emit the stable JSON contract."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control-plane",
        description="Deterministic local gates for Codex engineering workflows.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    policy_parser = subparsers.add_parser(
        "policy-check", help="Validate the project policy."
    )
    policy_parser.add_argument("--policy", type=Path, required=True)
    _add_output_option(policy_parser)
    policy_parser.set_defaults(handler=command_policy_check)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check local prerequisites without changing state."
    )
    doctor_parser.add_argument("--repo", type=Path, default=Path.cwd())
    doctor_parser.add_argument("--policy", type=Path)
    _add_output_option(doctor_parser)
    doctor_parser.set_defaults(handler=command_doctor)

    preflight_parser = subparsers.add_parser(
        "preflight", help="Evaluate read, write, or release Git gates."
    )
    preflight_parser.add_argument(
        "--mode", choices=("read", "write", "release"), required=True
    )
    preflight_parser.add_argument("--repo", type=Path, default=Path.cwd())
    preflight_parser.add_argument("--policy", type=Path)
    preflight_parser.add_argument("--task-id")
    preflight_parser.add_argument("--session-id")
    refresh_group = preflight_parser.add_mutually_exclusive_group()
    refresh_group.add_argument(
        "--refresh",
        action="store_true",
        help="Explicitly refresh the configured remote base before evaluation.",
    )
    refresh_group.add_argument(
        "--offline",
        dest="refresh",
        action="store_false",
        help="Use locally cached remote references (the default).",
    )
    preflight_parser.set_defaults(refresh=False)
    _add_output_option(preflight_parser)
    preflight_parser.set_defaults(handler=command_preflight)

    registry_parser = subparsers.add_parser(
        "registry-check", help="Validate the resource registry."
    )
    registry_parser.add_argument("--registry", type=Path, required=True)
    registry_parser.add_argument("--policy", type=Path)
    _add_output_option(registry_parser)
    registry_parser.set_defaults(handler=command_registry_check)

    inventory_parser = subparsers.add_parser(
        "inventory", help="Build a metadata-only resource inventory."
    )
    inventory_parser.add_argument("--repo", type=Path, default=Path.cwd())
    inventory_parser.add_argument("--registry", type=Path)
    _add_output_option(inventory_parser)
    inventory_parser.set_defaults(handler=command_inventory)

    route_parser = subparsers.add_parser(
        "route", help="Resolve a pre-framed TaskEnvelope."
    )
    route_parser.add_argument("--repo", type=Path, default=Path.cwd())
    route_parser.add_argument("--task", type=Path, required=True)
    route_parser.add_argument("--policy", type=Path)
    route_parser.add_argument("--registry", type=Path)
    route_parser.add_argument("--mode", choices=("audit", "enforce"), default="audit")
    _add_output_option(route_parser)
    route_parser.set_defaults(handler=command_route)

    verify_parser = subparsers.add_parser(
        "route-verify", help="Verify a resource-use receipt."
    )
    verify_parser.add_argument("--decision", type=Path, required=True)
    verify_parser.add_argument("--receipt", type=Path, required=True)
    verify_parser.add_argument("--mode", choices=("audit", "enforce"), default="audit")
    _add_output_option(verify_parser)
    verify_parser.set_defaults(handler=command_route_verify)

    run_parser = subparsers.add_parser(
        "run", help="Prepare, verify, inspect, or block one bounded local run."
    )
    run_subparsers = run_parser.add_subparsers(dest="run_action", required=True)
    run_prepare = run_subparsers.add_parser("prepare")
    run_prepare.add_argument("--repo", type=Path, default=Path.cwd())
    run_prepare.add_argument("--task", type=Path, required=True)
    run_prepare.add_argument("--session-id", required=True)
    run_prepare.add_argument("--policy", type=Path)
    run_prepare.add_argument("--registry", type=Path)
    _add_output_option(run_prepare)
    run_prepare.set_defaults(handler=command_run)
    for action in ("verify", "status", "block"):
        action_parser = run_subparsers.add_parser(action)
        action_parser.add_argument("--repo", type=Path, default=Path.cwd())
        action_parser.add_argument("--task-id", required=True)
        if action == "block":
            action_parser.add_argument("--reason", required=True)
        _add_output_option(action_parser)
        action_parser.set_defaults(handler=command_run)

    report_parser = subparsers.add_parser(
        "report", help="Summarize bounded local run receipts."
    )
    report_parser.add_argument("--repo", type=Path, default=Path.cwd())
    report_parser.add_argument("--since", default="30d")
    report_parser.add_argument("--format", choices=("markdown",), default="markdown")
    report_parser.set_defaults(handler=command_report)

    risk_parser = subparsers.add_parser(
        "risk-status",
        help="Evaluate tri-state local and remote engineering risk.",
    )
    risk_parser.add_argument("--repo", type=Path, default=Path.cwd())
    risk_parser.add_argument(
        "--policy",
        type=Path,
        help="Candidate policy hint only; never a governing policy source.",
    )
    risk_parser.add_argument("--task-id")
    risk_parser.add_argument(
        "--lease-session-id",
        help=(
            "Explicit local lease-binding hint; validates continuity but "
            "never grants authority or PASS."
        ),
    )
    risk_parser.add_argument(
        "--decision",
        type=Path,
        help="Serialized route hint only; never native authority.",
    )
    _add_output_option(risk_parser)
    risk_parser.set_defaults(handler=command_risk_status)

    safe_read_parser = subparsers.add_parser(
        "safe-read",
        help="Execute one bounded, closed local read in an explicit worktree.",
    )
    safe_read_parser.add_argument("--repo", type=Path, required=True)
    safe_read_parser.add_argument("--timeout", type=float, default=3.0)
    safe_read_parser.add_argument(
        "--output-limit", type=int, default=65_536
    )
    safe_read_parser.add_argument("argv", nargs=argparse.REMAINDER)
    safe_read_parser.set_defaults(handler=command_safe_read)

    verification_parser = subparsers.add_parser(
        "verification-run",
        help="Run the complete profile already bound to a verifier task.",
    )
    verification_parser.add_argument(
        "--repo", type=Path, default=Path.cwd()
    )
    verification_parser.add_argument("--task-id", required=True)
    _add_output_option(verification_parser)
    verification_parser.set_defaults(handler=command_verification_run)

    hook_smoke_parser = subparsers.add_parser(
        "hook-smoke",
        help="Run and publish the closed macOS hook smoke for a verifier task.",
    )
    hook_smoke_parser.add_argument("--repo", type=Path, required=True)
    hook_smoke_parser.add_argument("--task-id", required=True)
    _add_output_option(hook_smoke_parser)
    hook_smoke_parser.set_defaults(handler=command_hook_smoke)

    guard_parser = subparsers.add_parser(
        "git-guard", help="Enforce the installed protected Git policy."
    )
    guard_subparsers = guard_parser.add_subparsers(
        dest="guard_action", required=True
    )
    pre_commit_parser = guard_subparsers.add_parser("pre-commit")
    pre_commit_parser.add_argument("--repo", type=Path, required=True)
    _add_output_option(pre_commit_parser)
    pre_commit_parser.set_defaults(handler=command_git_guard)
    pre_push_parser = guard_subparsers.add_parser("pre-push")
    pre_push_parser.add_argument("--repo", type=Path, required=True)
    pre_push_parser.add_argument("--remote-name", required=True)
    pre_push_parser.add_argument("--remote-url", required=True)
    _add_output_option(pre_push_parser)
    pre_push_parser.set_defaults(handler=command_git_guard)

    task_parser = subparsers.add_parser("task", help="Manage task lifecycle state.")
    task_subparsers = task_parser.add_subparsers(dest="task_action", required=True)
    for action in (
        "start",
        "resume",
        "status",
        "transition",
        "close",
        "lease-release",
    ):
        action_parser = task_subparsers.add_parser(action)
        action_parser.add_argument("--repo", type=Path, default=Path.cwd())
        action_parser.add_argument("--task-id", required=True)
        if action == "start":
            action_parser.add_argument("--outcome", choices=tuple(sorted({"answer", "local_change", "commit", "pull_request", "integration", "release"})), required=True)
            action_parser.add_argument("--branch", required=True)
            action_parser.add_argument("--task-digest", required=True)
            action_parser.add_argument("--decision-digest", required=True)
            action_parser.add_argument("--session-id")
            action_parser.add_argument("--scope-path", action="append")
            action_parser.add_argument("--policy", type=Path)
        if action == "transition":
            action_parser.add_argument("--state", required=True)
            action_parser.add_argument("--reason")
        if action == "lease-release":
            action_parser.add_argument("--worktree", required=True)
            action_parser.add_argument("--branch", required=True)
            action_parser.add_argument("--session-id", required=True)
            action_parser.add_argument("--policy-digest", required=True)
            action_parser.add_argument("--lease-digest", required=True)
        _add_output_option(action_parser)
        action_parser.set_defaults(handler=command_task)

    adopt_parser = subparsers.add_parser(
        "adopt", help="Plan or perform project-local adoption."
    )
    adopt_subparsers = adopt_parser.add_subparsers(dest="adopt_action", required=True)
    for action in ("plan", "apply", "verify", "status", "rollback"):
        action_parser = adopt_subparsers.add_parser(action)
        if action == "plan":
            action_parser.add_argument("--target", type=Path, required=True)
            action_parser.add_argument("--source", type=Path, default=Path.cwd())
            action_parser.add_argument("--base-branch")
            action_parser.add_argument("--remote")
        elif action == "apply":
            action_parser.add_argument("--plan", type=Path, required=True)
        else:
            action_parser.add_argument("--target", type=Path, required=True)
        _add_output_option(action_parser)
        action_parser.set_defaults(handler=command_adopt)

    upgrade_parser = subparsers.add_parser(
        "upgrade", help="Plan or apply a versioned control-plane upgrade."
    )
    upgrade_subparsers = upgrade_parser.add_subparsers(
        dest="upgrade_action", required=True
    )
    for action in ("plan", "apply"):
        action_parser = upgrade_subparsers.add_parser(action)
        if action == "plan":
            action_parser.add_argument("--target", type=Path, required=True)
            action_parser.add_argument("--source", type=Path, default=Path.cwd())
            action_parser.add_argument("--base-branch")
            action_parser.add_argument("--remote")
        else:
            action_parser.add_argument("--plan", type=Path, required=True)
        _add_output_option(action_parser)
        action_parser.set_defaults(handler=command_upgrade)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
