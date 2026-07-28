"""Bounded audit hooks for Codex lifecycle events."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from control_plane.contracts import validate_task_id
from control_plane.repository import discover_repository, worktree_git_dir


MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 4_096
DESTRUCTIVE_PATTERNS = (
    re.compile(r"(?:^|\s)git(?:\s+-C\s+\S+)*\s+reset\s+--hard(?:\s|$)"),
    re.compile(
        r"(?:^|\s)git(?:\s+-C\s+\S+)*\s+clean\b(?=[^\n]*"
        r"(?:--force|-[a-z]*f))"
    ),
    re.compile(
        r"(?:^|\s)git(?:\s+-C\s+\S+)*\s+push\b[^\n]*"
        r"(?:--force(?:-with-lease)?|-f)(?:\s|$)"
    ),
    re.compile(
        r"(?:^|\s)rm\b(?=[^\n]*(?:-[a-z]*r))"
        r"(?=[^\n]*(?:-[a-z]*f))[^\n]*"
    ),
)


def _digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def _compact_output(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("E_HOOK_OUTPUT_LIMIT: hook output exceeds 4 KiB")
    return encoded


def _manifest(root: Path) -> str:
    raw_task_id = os.environ.get("CODEX_CONTROL_PLANE_TASK_ID", "")
    task_id = raw_task_id if validate_task_id(raw_task_id) else ""
    state = "unbound"
    rendered_task_id = task_id or ("invalid" if raw_task_id else "unset")
    if task_id:
        state_path = (
            worktree_git_dir(root)
            / "codex-control-plane"
            / "tasks"
            / f"{task_id}.json"
        )
        if state_path.is_file():
            try:
                state = str(json.loads(state_path.read_text(encoding="utf-8"))["state"])
            except (KeyError, OSError, json.JSONDecodeError):
                state = "invalid"
    return (
        "CONTROL_PLANE_AUDIT_V2 "
        f"task={rendered_task_id} state={state} "
        f"policy={_digest(root / '.codex/project-policy.toml')} "
        f"registry={_digest(root / '.codex/resource-registry.toml')} "
        "authority=selection-is-not-authorization "
        "routing=frame-route-load-required-resources "
        "automatic_resource_selection=true "
        f"hook_mode={os.environ.get('CODEX_CONTROL_PLANE_HOOK_MODE', 'audit')} "
        "hook_trust=pending-or-user-reviewed"
    )


def evaluate_hook(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return bounded JSON output, or None for a silent passing hook."""

    event = str(payload.get("hook_event_name", ""))
    cwd = Path(str(payload.get("cwd", ".")))
    try:
        root = discover_repository(cwd)
    except Exception:
        return None
    if event in {"UserPromptSubmit", "SessionStart"}:
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": _manifest(root),
            },
        }
    if event == "PreToolUse":
        tool_name = str(payload.get("tool_name", ""))
        tool_input = payload.get("tool_input", {})
        command = (
            str(tool_input.get("command", ""))
            if isinstance(tool_input, Mapping)
            else ""
        )
        reasons: list[str] = []
        if tool_name == "Bash" and any(
            pattern.search(command) for pattern in DESTRUCTIVE_PATTERNS
        ):
            reasons.append("destructive_command_requires_explicit_authority")
        if tool_name.startswith("mcp__"):
            reasons.append("mcp_use_requires_task_authorization_and_egress_check")
        if reasons:
            mode = os.environ.get("CODEX_CONTROL_PLANE_HOOK_MODE", "audit")
            deny = mode == "enforce" or (
                mode == "soft-enforce"
                and "destructive_command_requires_explicit_authority" in reasons
            )
            if deny:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "CONTROL_PLANE_SOFT_ENFORCE: "
                            + ",".join(sorted(reasons))
                        ),
                    }
                }
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": (
                        "CONTROL_PLANE_AUDIT: " + ",".join(sorted(reasons))
                    ),
                }
            }
        return None
    if event == "Stop":
        if payload.get("stop_hook_active") is True:
            return {"continue": True}
        task_id = os.environ.get("CODEX_CONTROL_PLANE_TASK_ID")
        if not task_id:
            return {"continue": True}
        receipt = (
            worktree_git_dir(root)
            / "codex-control-plane"
            / "receipts"
            / f"{task_id}.json"
        )
        if not receipt.is_file():
            return {
                "continue": True,
                "systemMessage": (
                    "CONTROL_PLANE_AUDIT: active task has no compact receipt; "
                    "audit mode does not continue the turn automatically."
                ),
            }
        return {"continue": True}
    return None


def run_hook(raw_input: bytes) -> str:
    if len(raw_input) > MAX_INPUT_BYTES:
        raise ValueError("E_HOOK_INPUT_LIMIT: hook input exceeds 1 MiB")
    try:
        payload = json.loads(raw_input.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("E_HOOK_INPUT: invalid hook JSON") from error
    if not isinstance(payload, Mapping):
        raise ValueError("E_HOOK_INPUT: hook input must be an object")
    result = evaluate_hook(payload)
    return "" if result is None else _compact_output(result)
