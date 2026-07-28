"""Command-line interface for the local engineering control plane."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence

from control_plane.git_state import GateError, evaluate_preflight
from control_plane.policy import PolicyError, load_policy, validate_policy


def _policy_path(repo: Path, explicit_path: Path | None) -> Path:
    return explicit_path if explicit_path is not None else repo / ".codex" / "project-policy.toml"


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
    diagnostic = (
        command == "preflight"
        and payload.get("mode") == "read"
        and any(not check.get("ok", False) for check in payload.get("checks", []))
    )
    status = (
        "DIAGNOSTIC"
        if diagnostic and payload.get("ok")
        else ("PASS" if payload.get("ok") else "FAIL")
    )
    lines = [f"{status} {command}"]

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

    return "\n".join(lines)


def _emit(payload: dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_render_human(payload))
    return 0 if payload.get("ok") else 1


def _refresh_remote_base(
    repo: Path, remote: str, base_branch: str
) -> GateError | None:
    try:
        completed = subprocess.run(
            [
                "git",
                "fetch",
                "--no-tags",
                remote,
                f"+refs/heads/{base_branch}:refs/remotes/{remote}/{base_branch}",
            ],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        completed = subprocess.CompletedProcess(
            args=["git", "fetch"],
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
    if fetch_error is not None:
        payload["errors"].insert(0, asdict(fetch_error))
        payload["ok"] = False

    return _emit(payload, arguments.json)


def command_doctor(arguments: argparse.Namespace) -> int:
    policy_path = _policy_path(arguments.repo, arguments.policy)
    _, policy_payload = _load_validated_policy(policy_path)
    git_available = shutil.which("git") is not None
    python_compatible = sys.version_info >= (3, 11)

    git_repository = False
    if git_available:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=arguments.repo,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            completed = subprocess.CompletedProcess(
                args=["git", "rev-parse"],
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

    payload = {
        "schema_version": 1,
        "command": "doctor",
        "ok": not errors,
        "facts": facts,
        "errors": errors,
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
