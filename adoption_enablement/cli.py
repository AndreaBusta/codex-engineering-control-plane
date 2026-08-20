"""Closed CLI for local adoption enablement."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Mapping, Sequence

from .contracts import canonical_json, load_closed_json, validate_plan
from .manifest import preview
from .safe_io import canonical_root, read_confined_file
from .transaction import apply_plan, rollback, status, verify


PLAN_MAX = 1024 * 1024
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise ValueError("E_ADOPTION_USAGE: invalid command arguments")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="control-plane-adoption", add_help=True)
    commands = parser.add_subparsers(dest="command", required=True)

    preview_command = commands.add_parser("preview")
    preview_command.add_argument("--source", type=Path, required=True)
    preview_command.add_argument("--target", type=Path, required=True)
    preview_command.add_argument("--json", action="store_true")

    apply_command = commands.add_parser("apply")
    apply_command.add_argument("--source", type=Path, required=True)
    apply_command.add_argument("--target", type=Path, required=True)
    apply_command.add_argument("--plan", type=Path, required=True)
    apply_command.add_argument("--plan-digest", required=True)
    apply_command.add_argument("--json", action="store_true")

    for name in ("status", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--target", type=Path, required=True)
        command.add_argument("--json", action="store_true")

    rollback_command = commands.add_parser("rollback")
    rollback_command.add_argument("--target", type=Path, required=True)
    rollback_command.add_argument("--install-digest", required=True)
    rollback_command.add_argument("--json", action="store_true")
    return parser


def _safe_error(error: BaseException) -> tuple[str, str]:
    code = str(error).split(":", 1)[0]
    if re.fullmatch(r"E_[A-Z0-9_]{1,62}", code) is None:
        code = "E_ADOPTION_INTERNAL"
    messages = {
        "E_ADOPTION_USAGE": "Adoption command arguments are invalid.",
        "E_ADOPTION_PLAN": "The reviewed adoption plan is invalid.",
        "E_ADOPTION_REPLAY": "The adoption binding does not match existing evidence.",
        "E_ADOPTION_BUSY": "Another adoption operation is active.",
    }
    return code, messages.get(code, "Adoption enablement failed closed.")


def _failure(
    error: BaseException, *, command: str | None = None
) -> dict[str, object]:
    code, message = _safe_error(error)
    payload: dict[str, object] = {
        "ok": False,
        "result": "UNKNOWN",
        "error_code": code,
        "message": message,
        "authorizes": False,
    }
    if command == "preview":
        payload.update({"applicable": False, "mutation": False})
    return payload


def _read_plan(path: Path, expected: object) -> Mapping[str, object]:
    try:
        parent = canonical_root(path.parent)
        payload = read_confined_file(parent, path.name, maximum=PLAN_MAX)
    except (OSError, ValueError) as error:
        raise ValueError("E_ADOPTION_PLAN: plan file is unavailable") from error
    value = load_closed_json(payload, limit=PLAN_MAX)
    issues = validate_plan(value, expected_digest=expected)
    if issues:
        raise ValueError(f"E_ADOPTION_PLAN: plan is invalid ({issues[0].code})")
    return value


def _execute(arguments: argparse.Namespace) -> Mapping[str, object]:
    if arguments.command == "preview":
        return preview(arguments.source, arguments.target)
    if arguments.command == "apply":
        if _DIGEST.fullmatch(arguments.plan_digest) is None:
            raise ValueError("E_ADOPTION_PLAN: expected digest is invalid")
        plan = _read_plan(arguments.plan, arguments.plan_digest)
        return apply_plan(
            arguments.source,
            arguments.target,
            plan,
            expected_plan_digest=arguments.plan_digest,
        )
    if arguments.command == "status":
        return status(arguments.target)
    if arguments.command == "verify":
        return verify(arguments.target)
    if arguments.command == "rollback":
        return rollback(arguments.target, install_digest=arguments.install_digest)
    raise ValueError("E_ADOPTION_USAGE: unsupported command")


def main(argv: Sequence[str] | None = None) -> int:
    command: str | None = None
    try:
        arguments = _parser().parse_args(list(argv) if argv is not None else None)
        command = arguments.command
        payload = dict(_execute(arguments))
        if payload.get("authorizes") is not False:
            raise ValueError("E_ADOPTION_AUTHORITY: command result is invalid")
        print(canonical_json(payload))
        return 0 if payload.get("result") == "PASS" else 2
    except (OSError, ValueError, RecursionError) as error:
        print(canonical_json(_failure(error, command=command)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
