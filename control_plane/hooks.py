"""Bounded audit hooks for Codex lifecycle events."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
from typing import Any, Mapping, Sequence
from uuid import uuid4

from control_plane.contracts import (
    SHA256_DIGEST,
    canonical_json,
    contract_digest,
    validate_task_id,
)
from control_plane.intake import (
    InteractionRecommendationView,
    render_interaction_recommendation,
)
from control_plane.policy import GoverningPolicy, _governing_policy_is_issued
from control_plane.risk_sentinel import RiskStatus, evaluate_risk_status


MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 4_096
SAFE_PATH = "feature→commit→push-feature→PR→checks→authorized-merge"
WARNING_TRIGGERS = frozenset(
    {
        "user_prompt",
        "fingerprint_changed",
        "pre_red_action",
        "post_compact",
    }
)
FRAMING_STATES = frozenset({"pending_framing", "framed"})
WARNING_ACTIONS = frozenset(
    {
        "SAFE_PATH_CONFIRMED",
        "CONTINUE_WITH_CAUTION",
        "PAUSE_AND_VERIFY",
        "STOP",
    }
)
WARNING_REASON_CODES = frozenset(
    {
        "RS_ALL_GATES_PASS",
        "RS_LOCAL_STATUS_UNKNOWN",
        "RS_LOCAL_STATUS_FAIL",
        "RS_REMOTE_PROTECTION_UNVERIFIED",
        "RS_REMOTE_STATUS_FAIL",
        "RS_WARNING_STATE_UNKNOWN",
    }
)
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
SECRET_PATTERN_SET_VERSION = "repository-literal-secrets-v1"
SECRET_PATTERNS = (
    r"-----BEGIN (?:ENCRYPTED |RSA |EC |OPENSSH )?PRIVATE KEY-----",
    (
        r"(?im)^\s*(?:export\s+)?[\"']?"
        r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|"
        r"client[_-]?secret|private[_-]?key|service[_-]?account)"
        r"[\"']?\s*[:=]\s*"
        r"(?:[\"'][^\"'\r\n]{8,}[\"']|[^\s#\"'$<{][^\s#]{7,})"
        r"\s*,?\s*(?:#.*)?$"
    ),
    r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bAIza[0-9A-Za-z_-]{20,}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
    r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{12,}\b",
)


def secret_pattern_set_digest() -> str:
    """Return the runtime-locked digest of the governing secret patterns."""

    return contract_digest(
        {
            "version": SECRET_PATTERN_SET_VERSION,
            "patterns": SECRET_PATTERNS,
        }
    )


@dataclass(frozen=True)
class HookWarningPayload:
    """Closed, prompt-free warning view shared by every hook trigger."""

    title: str
    local: str
    remote: str
    action: str
    reason_code: str
    safe_path: str
    interaction: str | Mapping[str, object]
    automatic_change: bool
    trigger: str
    framing_status: str

    def __post_init__(self) -> None:
        from control_plane.risk_sentinel import FAIL, PASS, UNKNOWN

        if (
            self.title != "CONTROL PLANE RISK"
            or self.local not in {PASS, UNKNOWN, FAIL}
            or self.remote not in {PASS, UNKNOWN, FAIL}
            or self.action not in WARNING_ACTIONS
            or self.reason_code not in WARNING_REASON_CODES
            or self.safe_path != SAFE_PATH
            or self.automatic_change is not False
            or self.trigger not in WARNING_TRIGGERS
            or self.framing_status not in FRAMING_STATES
        ):
            raise ValueError("E_HOOK_WARNING: warning payload is invalid")
        if self.interaction == "pending_framing":
            normalized: str | dict[str, object] = "pending_framing"
        elif isinstance(self.interaction, Mapping):
            normalized = dict(self.interaction)
            if (
                set(normalized)
                != {
                    "mode",
                    "commands",
                    "message_code",
                    "reason_codes",
                    "automatic_change",
                    "human_message",
                }
                or normalized.get("automatic_change") is not False
            ):
                raise ValueError(
                    "E_HOOK_WARNING: interaction view is not closed"
                )
        else:
            raise ValueError("E_HOOK_WARNING: interaction view is invalid")
        object.__setattr__(self, "interaction", normalized)
        if len(canonical_json(self.as_dict()).encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("E_HOOK_OUTPUT_LIMIT: warning exceeds 4 KiB")

    def as_dict(self) -> dict[str, object]:
        interaction: object = (
            dict(self.interaction)
            if isinstance(self.interaction, Mapping)
            else self.interaction
        )
        return {
            "title": self.title,
            "local": self.local,
            "remote": self.remote,
            "action": self.action,
            "reason_code": self.reason_code,
            "safe_path": self.safe_path,
            "interaction": interaction,
            "automatic_change": self.automatic_change,
            "trigger": self.trigger,
            "framing_status": self.framing_status,
        }


@dataclass(frozen=True)
class CurrentWarningView:
    """Restart-safe warning view bound to one task, route and fingerprint."""

    schema_version: int
    generation: int
    payload: HookWarningPayload
    task_digest: str
    route_digest: str
    fingerprint: str
    payload_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "payload": self.payload.as_dict(),
            "task_digest": self.task_digest,
            "route_digest": self.route_digest,
            "fingerprint": self.fingerprint,
            "payload_digest": self.payload_digest,
        }


@dataclass(frozen=True)
class BashEffect:
    """Closed classification; it never constitutes effect authorization."""

    category: str
    reason_code: str
    argv_digest: str
    operation_id: str | None = None
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        categories = {
            "read_only_known",
            "read_only_unsanitized",
            "git_effect",
            "write_paths_known",
            "may_write_unknown_paths",
            "ambiguous_shell_command",
            "destructive",
        }
        if (
            self.category not in categories
            or SHA256_DIGEST.fullmatch(self.argv_digest) is None
            or (
                self.category == "git_effect"
                and self.operation_id not in {"push_validated_feature"}
            )
            or (
                self.category != "git_effect"
                and self.operation_id is not None
            )
        ):
            raise ValueError("E_BASH_EFFECT: classification is invalid")


@dataclass(frozen=True)
class CompletedSafeRead:
    """Bounded result that cannot be promoted into an authorization."""

    argv_digest: str
    repository_binding_digest: str
    status: str
    exit_code: int | None
    timed_out: bool
    truncated: bool
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stderr_bytes: int
    duration_ms: int
    pattern_set_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            SHA256_DIGEST.fullmatch(self.argv_digest) is None
            or SHA256_DIGEST.fullmatch(self.repository_binding_digest) is None
            or self.status
            not in {"completed", "timeout", "truncated", "rejected"}
            or self.stdout_bytes != len(self.stdout)
            or self.stderr_bytes != len(self.stderr)
            or self.duration_ms < 0
            or (self.status == "completed" and self.exit_code is None)
            or (
                self.status != "completed"
                and self.exit_code is not None
            )
            or self.timed_out != (self.status == "timeout")
            or self.truncated != (self.status == "truncated")
            or (
                self.pattern_set_digest is not None
                and SHA256_DIGEST.fullmatch(self.pattern_set_digest) is None
            )
        ):
            raise ValueError("E_SAFE_READ_RESULT: result contract is invalid")


@dataclass(frozen=True)
class PreToolDecision:
    """One bounded hook decision; only opaque host context can permit writes."""

    decision: str
    reason_code: str
    effect_category: str
    targets: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.decision not in {"allow", "deny", "ask", "advisory"}
            or self.effect_category
            not in {
                "read_only_known",
                "read_only_unsanitized",
                "git_effect",
                "write_paths_known",
                "may_write_unknown_paths",
                "ambiguous_shell_command",
                "destructive",
                "external_effect",
            }
        ):
            raise ValueError("E_PRETOOL_DECISION: decision is invalid")


_SHELL_META = re.compile(r"(?:&&|\|\||[;|&<>`]|[$][(]|\r|\n)")
_SAFE_REF_RANGE = re.compile(
    r"^origin/[A-Za-z0-9][A-Za-z0-9._/-]{0,126}[.]{3}HEAD$",
    re.ASCII,
)


def _bash_effect(
    category: str,
    reason_code: str,
    argv: Sequence[str],
    *,
    operation_id: str | None = None,
    targets: Sequence[str] = (),
) -> BashEffect:
    return BashEffect(
        category=category,
        reason_code=reason_code,
        argv_digest=contract_digest({"argv": tuple(argv)}),
        operation_id=operation_id,
        targets=tuple(targets),
    )


def _safe_repo_path(root: Path, value: str) -> str | None:
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or any(character in value for character in "*?[]")
    ):
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    target = root / relative
    parent = target.parent
    while parent != root:
        if parent.exists() and parent.is_symlink():
            return None
        if root not in parent.resolve(strict=False).parents and parent != root:
            return None
        parent = parent.parent
    if target.is_symlink():
        return None
    resolved = target.resolve(strict=False)
    if root not in resolved.parents:
        return None
    return relative.as_posix()


def _safe_read_target(root: Path, value: str) -> str | None:
    """Accept one relative or exact absolute target confined to the repo."""

    candidate = Path(value)
    if candidate.is_absolute():
        if candidate.is_symlink() or candidate.resolve(strict=False) != candidate:
            return None
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            return None
        value = relative.as_posix()
    return _safe_repo_path(root, value)


def _validate_safe_read_argv(
    argv: Sequence[str], root: Path
) -> tuple[str, tuple[str, ...]] | None:
    if (
        isinstance(argv, (str, bytes))
        or not isinstance(argv, Sequence)
        or not 2 <= len(argv) <= 64
        or any(
            not isinstance(item, str)
            or not item
            or "\x00" in item
            or len(item.encode("utf-8", "strict")) > 4_096
            for item in argv
        )
    ):
        return None
    closed = tuple(argv)
    if closed[0] == "git":
        if closed == ("git", "status", "--short"):
            return "git", closed
        if closed in {
            ("git", "diff", "--check"),
            ("git", "diff", "--cached", "--check"),
            ("git", "diff", "--name-only"),
            ("git", "diff", "--cached", "--name-only"),
        }:
            return "git", closed
        if len(closed) >= 6 and closed[:3] == (
            "git",
            "diff",
            "--exit-code",
        ):
            if _SAFE_REF_RANGE.fullmatch(closed[3]) is None or closed[4] != "--":
                return None
            paths = tuple(_safe_repo_path(root, item) for item in closed[5:])
            if not paths or any(item is None for item in paths):
                return None
            return "git", closed
        if (
            len(closed) >= 5
            and closed[1] == "diff"
            and closed[2] in {"--name-only", "--cached"}
        ):
            offset = 3
            if closed[2] == "--cached":
                if len(closed) < 6 or closed[3] != "--name-only":
                    return None
                offset = 4
            selector = closed[offset]
            if _SAFE_REF_RANGE.fullmatch(selector) is None:
                return None
            if len(closed) == offset + 1:
                return "git", closed
            if closed[offset + 1] != "--":
                return None
            paths = tuple(
                _safe_repo_path(root, item)
                for item in closed[offset + 2 :]
            )
            if not paths or any(item is None for item in paths):
                return None
            return "git", closed
        if closed in {
            ("git", "rev-parse", "HEAD"),
            ("git", "rev-parse", "--show-toplevel"),
            ("git", "rev-parse", "--git-dir"),
            ("git", "rev-parse", "--git-common-dir"),
            ("git", "log", "--oneline", "-n", "1"),
            ("git", "show", "--stat", "--oneline", "HEAD"),
        }:
            return "git", closed
        return None
    if closed[0] == "rg":
        if (
            len(closed) != 7
            or closed[1:4] != ("--no-config", "--quiet", "-e")
            or closed[5] != "--"
            or "\n" in closed[4]
            or "\r" in closed[4]
            or _safe_read_target(root, closed[6]) is None
        ):
            return None
        return "rg", closed
    if (
        len(closed) == 3
        and closed[:2] == ("secret-scan-governing", "--")
        and _safe_read_target(root, closed[2]) is not None
    ):
        return "secret-scan-governing", closed
    return None


def _git_command_tokens(
    argv: tuple[str, ...], root: Path
) -> tuple[tuple[str, ...], Path] | None:
    if not argv or argv[0] != "git":
        return None
    index = 1
    cwd = root
    while index < len(argv) and argv[index] == "-C":
        if index + 1 >= len(argv):
            return None
        candidate = Path(argv[index + 1])
        candidate = (
            candidate if candidate.is_absolute() else (cwd / candidate)
        )
        if candidate.is_symlink():
            return None
        candidate = candidate.resolve(strict=False)
        try:
            discovered = _trusted_discover_repository(candidate)
        except Exception:
            return None
        cwd = discovered
        index += 2
    if index >= len(argv) or argv[index].startswith("-"):
        return None
    return argv[index:], cwd


def _classify_push(
    tokens: tuple[str, ...],
    *,
    root: Path,
    remote: str,
    base_branch: str,
) -> BashEffect:
    argv = ("git", *tokens)
    arguments = list(tokens[1:])
    if any(item in {"--force", "--force-with-lease", "-f"} for item in arguments):
        return _bash_effect(
            "destructive", "force_push_forbidden", argv
        )
    if any(item in {"--all", "--mirror"} for item in arguments):
        return _bash_effect(
            "destructive", "ambiguous_dangerous_push", argv
        )
    if "--delete" in arguments or "-d" in arguments:
        delete_index = (
            arguments.index("--delete")
            if "--delete" in arguments
            else arguments.index("-d")
        )
        remaining = arguments[delete_index + 1 :]
        if len(remaining) >= 2 and remaining[0] == remote:
            if remaining[1].removeprefix("refs/heads/") == base_branch:
                return _bash_effect(
                    "destructive", "base_deletion_forbidden", argv
                )
        return _bash_effect(
            "git_effect",
            "feature_push_requires_host_attestation",
            argv,
            operation_id="push_validated_feature",
        )
    positionals = [
        item
        for item in arguments
        if not item.startswith("-")
    ]
    if not positionals:
        branch = _git_observation(
            root, ["symbolic-ref", "--quiet", "--short", "HEAD"]
        )
        upstream = _git_observation(
            root,
            ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        )
        if branch == base_branch or upstream == f"{remote}/{base_branch}":
            return _bash_effect(
                "destructive", "direct_base_push_forbidden", argv
            )
        if branch == "unknown" or upstream == "unknown":
            return _bash_effect(
                "ambiguous_shell_command",
                "implicit_push_target_unknown",
                argv,
            )
        return _bash_effect(
            "git_effect",
            "feature_push_requires_host_attestation",
            argv,
            operation_id="push_validated_feature",
        )
    supplied_remote = positionals[0]
    refspecs = positionals[1:]
    if supplied_remote != remote:
        return _bash_effect(
            "ambiguous_shell_command", "push_remote_unknown", argv
        )
    branch = _git_observation(
        root, ["symbolic-ref", "--quiet", "--short", "HEAD"]
    )
    for refspec in refspecs:
        destination = (
            refspec.rsplit(":", 1)[1]
            if ":" in refspec
            else refspec
        )
        normalized = destination.removeprefix("refs/heads/")
        if normalized == base_branch or (
            normalized == "HEAD" and branch == base_branch
        ):
            return _bash_effect(
                "destructive", "direct_base_push_forbidden", argv
            )
    if not refspecs and branch == base_branch:
        return _bash_effect(
            "destructive", "direct_base_push_forbidden", argv
        )
    return _bash_effect(
        "git_effect",
        "feature_push_requires_host_attestation",
        argv,
        operation_id="push_validated_feature",
    )


def classify_bash_command(
    command: str,
    *,
    root: Path,
    governing_policy: GoverningPolicy,
) -> BashEffect:
    """Classify simple Bash input without treating classification as authority."""

    if (
        type(governing_policy) is not GoverningPolicy
        or not _governing_policy_is_issued(governing_policy)
    ):
        raise ValueError("E_HOOK_POLICY: governing policy is required")
    repository = _trusted_discover_repository(root)
    if not isinstance(command, str) or not command.strip():
        return _bash_effect(
            "ambiguous_shell_command",
            "ambiguous_shell_command",
            (),
        )
    if _SHELL_META.search(command):
        return _bash_effect(
            "ambiguous_shell_command",
            "ambiguous_shell_command",
            (command,),
        )
    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError:
        return _bash_effect(
            "ambiguous_shell_command",
            "ambiguous_shell_command",
            (command,),
        )
    git_policy = governing_policy.policy.get("git", {})
    remote = str(git_policy.get("remote", ""))
    base_branch = str(git_policy.get("base_branch", ""))
    if (
        len(argv) >= 5
        and argv[0] in {
            "scripts/control-plane",
            str(repository / "scripts" / "control-plane"),
        }
        and argv[1:3] == ("safe-read", "--repo")
        and Path(argv[3]).resolve(strict=False) == repository
        and argv[4] == "--"
        and _validate_safe_read_argv(argv[5:], repository) is not None
    ):
        return _bash_effect(
            "read_only_known", "safe_read_closed_argv", argv
        )
    parsed_git = _git_command_tokens(argv, repository)
    if parsed_git is not None:
        git_tokens, git_root = parsed_git
        if git_root != repository:
            return _bash_effect(
                "ambiguous_shell_command",
                "git_worktree_target_unknown",
                argv,
            )
        command_name = git_tokens[0]
        if command_name == "push":
            return _classify_push(
                git_tokens,
                root=repository,
                remote=remote,
                base_branch=base_branch,
            )
        if command_name in {"reset", "clean"}:
            destructive = (
                "--hard" in git_tokens
                if command_name == "reset"
                else any(
                    item == "--force"
                    or (
                        item.startswith("-")
                        and not item.startswith("--")
                        and "f" in item[1:]
                    )
                    for item in git_tokens[1:]
                )
            )
            if destructive:
                return _bash_effect(
                    "destructive",
                    "destructive_command_requires_explicit_authority",
                    argv,
                )
        if command_name in {"status", "diff", "log", "show", "rev-parse"}:
            return _bash_effect(
                "read_only_unsanitized",
                "raw_read_requires_safe_read",
                argv,
            )
        return _bash_effect(
            "ambiguous_shell_command", "git_operation_unknown", argv
        )
    if argv and argv[0] == "rg":
        return _bash_effect(
            "read_only_unsanitized", "raw_read_requires_safe_read", argv
        )
    if argv and argv[0] == "rm" and any(
        item.startswith("-") and "r" in item and "f" in item
        for item in argv[1:]
    ):
        return _bash_effect(
            "destructive",
            "destructive_command_requires_explicit_authority",
            argv,
        )
    if (
        argv[:3] == ("python3", "-m", "unittest")
        or (argv and argv[0] in {"pytest", "xcodebuild"})
        or (len(argv) >= 2 and argv[0] in {"npm", "pnpm"} and argv[1] == "test")
    ):
        return _bash_effect(
            "may_write_unknown_paths",
            "test_may_write_unknown_paths",
            argv,
        )
    return _bash_effect(
        "ambiguous_shell_command", "ambiguous_shell_command", argv
    )


def _safe_read_rejected(
    argv_digest: str,
    repository_binding_digest: str,
    *,
    pattern_set_digest: str | None = None,
) -> CompletedSafeRead:
    return CompletedSafeRead(
        argv_digest=argv_digest,
        repository_binding_digest=repository_binding_digest,
        status="rejected",
        exit_code=None,
        timed_out=False,
        truncated=False,
        stdout=b"",
        stderr=b"",
        stdout_bytes=0,
        stderr_bytes=0,
        duration_ms=0,
        pattern_set_digest=pattern_set_digest,
    )


def _bounded_process(
    command: Sequence[str],
    *,
    root: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    output_limit_bytes: int,
    argv_digest: str,
    repository_binding_digest: str,
) -> CompletedSafeRead:
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=root,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        raise ValueError("E_SAFE_READ_PROCESS: process pipes are unavailable")
    selector = selectors.DefaultSelector()
    os.set_blocking(process.stdout.fileno(), False)
    os.set_blocking(process.stderr.fileno(), False)
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    status = "completed"
    deadline = started + timeout_seconds
    try:
        while selector.get_map():
            now = time.monotonic()
            if now >= deadline and status == "completed":
                status = "timeout"
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            events = selector.select(max(0.0, min(0.05, deadline - now)))
            if not events and process.poll() is not None:
                events = [
                    (key, selectors.EVENT_READ)
                    for key in tuple(selector.get_map().values())
                ]
            for key, _ in events:
                stream = key.fileobj
                name = str(key.data)
                remaining = output_limit_bytes - len(buffers[name])
                try:
                    chunk = os.read(stream.fileno(), max(1, min(65_536, remaining + 1)))
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                if len(chunk) > remaining:
                    if remaining > 0:
                        buffers[name].extend(chunk[:remaining])
                    if status == "completed":
                        status = "truncated"
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    buffers[name].extend(chunk)
            if status in {"timeout", "truncated"}:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=1)
        return_code = process.wait(timeout=1)
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    exit_code = return_code if status == "completed" else None
    return CompletedSafeRead(
        argv_digest=argv_digest,
        repository_binding_digest=repository_binding_digest,
        status=status,
        exit_code=exit_code,
        timed_out=status == "timeout",
        truncated=status == "truncated",
        stdout=bytes(buffers["stdout"]),
        stderr=bytes(buffers["stderr"]),
        stdout_bytes=len(buffers["stdout"]),
        stderr_bytes=len(buffers["stderr"]),
        duration_ms=duration_ms,
    )


def _trusted_git_executable() -> str | None:
    for candidate in (Path("/usr/bin/git"), Path("/bin/git")):
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and os.access(candidate, os.X_OK)
        ):
            return str(candidate)
    return None


def _safe_read_git_executable() -> str | None:
    """Internal test seam; production delegates only to the trusted path."""

    return _trusted_git_executable()


def _safe_read_rg_executable() -> str | None:
    """Resolve ripgrep only from fixed host locations, never ambient PATH."""

    for candidate in (
        Path("/opt/homebrew/bin/rg"),
        Path("/usr/local/bin/rg"),
        Path("/usr/bin/rg"),
        Path("/bin/rg"),
    ):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if (
            resolved.is_file()
            and not resolved.is_symlink()
            and os.access(resolved, os.X_OK)
        ):
            return str(resolved)
    return None


def _trusted_git_environment() -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }


def _trusted_git_text(root: Path, arguments: Sequence[str]) -> str:
    executable = _trusted_git_executable()
    if executable is None:
        return "unknown"
    try:
        completed = subprocess.run(
            [executable, *arguments],
            cwd=root,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=_trusted_git_environment(),
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8", "replace")) > 131_072
    ):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _safe_read_repository_identity(root: Path) -> tuple[Path, Path, Path]:
    supplied = Path(root)
    if (
        not supplied.is_absolute()
        or supplied.is_symlink()
        or not supplied.is_dir()
        or supplied.resolve() != supplied
    ):
        raise ValueError(
            "E_SAFE_READ_REPOSITORY: root must be a canonical directory"
        )
    top_level = _trusted_git_text(supplied, ("rev-parse", "--show-toplevel"))
    if top_level == "unknown" or Path(top_level).resolve() != supplied:
        raise ValueError(
            "E_SAFE_READ_REPOSITORY: target must be the exact Git root"
        )
    raw_git_dir = _trusted_git_text(
        supplied,
        ("rev-parse", "--path-format=absolute", "--git-dir"),
    )
    raw_common_dir = _trusted_git_text(
        supplied,
        ("rev-parse", "--path-format=absolute", "--git-common-dir"),
    )
    if "unknown" in {raw_git_dir, raw_common_dir}:
        raise ValueError(
            "E_SAFE_READ_REPOSITORY: Git identity is unavailable"
        )
    return supplied, Path(raw_git_dir).resolve(), Path(raw_common_dir).resolve()


def _trusted_discover_repository(path: Path) -> Path:
    supplied = Path(path)
    if not supplied.is_absolute():
        supplied = Path.cwd() / supplied
    if supplied.is_symlink() or not supplied.is_dir():
        raise ValueError(
            "E_HOOK_REPOSITORY: cwd must be a canonical directory"
        )
    supplied = supplied.resolve()
    top_level = _trusted_git_text(
        supplied,
        ("rev-parse", "--show-toplevel"),
    )
    if top_level == "unknown":
        raise ValueError("E_HOOK_REPOSITORY: Git root is unavailable")
    return _safe_read_repository_identity(Path(top_level))[0]


def _trusted_worktree_git_dir(root: Path) -> Path:
    return _safe_read_repository_identity(
        _trusted_discover_repository(root)
    )[1]


_TRUSTED_GIT_ENVIRONMENT_LOCK = threading.RLock()
_DANGEROUS_GIT_ENVIRONMENT = frozenset(
    {
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "SSH_AUTH_SOCK",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_PROXY_COMMAND",
        "GIT_PROTOCOL_FROM_USER",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "RIPGREP_CONFIG_PATH",
    }
)


@contextmanager
def _trusted_git_subprocess_environment():
    """Confine inherited Git calls in legacy read-only risk observation."""

    with _TRUSTED_GIT_ENVIRONMENT_LOCK:
        with tempfile.TemporaryDirectory(
            prefix="control-plane-hook-git-"
        ) as temporary:
            config = Path(temporary) / "gitconfig"
            config.touch(mode=0o600)
            updates = {
                **_trusted_git_environment(),
                "GIT_CONFIG_GLOBAL": str(config),
                "TMPDIR": temporary,
            }
            affected = set(updates).union(_DANGEROUS_GIT_ENVIRONMENT)
            prior = {name: os.environ.get(name) for name in affected}
            try:
                for name in _DANGEROUS_GIT_ENVIRONMENT:
                    os.environ.pop(name, None)
                os.environ.update(updates)
                yield
            finally:
                for name, value in prior.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value


def execute_safe_read(
    argv: Sequence[str],
    *,
    root: Path,
    worktree_inventory: object,
    timeout_seconds: float,
    output_limit_bytes: int,
) -> CompletedSafeRead:
    """Execute one closed read with exact worktree and bounded process effects."""

    from control_plane.host_bridge import (
        ValidatedWorktreeInventoryObservation,
        _consume_worktree_inventory,
    )

    repository, git_dir, common_dir = _safe_read_repository_identity(
        Path(root)
    )
    if (
        type(worktree_inventory)
        is not ValidatedWorktreeInventoryObservation
        or worktree_inventory._consumed
    ):
        raise ValueError(
            "E_SAFE_READ_INVENTORY: validated one-shot inventory is required"
        )
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not 0 < float(timeout_seconds) <= 30
        or not isinstance(output_limit_bytes, int)
        or isinstance(output_limit_bytes, bool)
        or not 1 <= output_limit_bytes <= 1_048_576
    ):
        raise ValueError("E_SAFE_READ_LIMIT: limits are invalid")
    owner = next(
        (
            record
            for record in worktree_inventory.records
            if record.worktree == str(repository)
            and Path(record.git_dir).resolve() == git_dir
        ),
        None,
    )
    if (
        owner is None
        or Path(worktree_inventory.common_git_dir).resolve() != common_dir
    ):
        raise ValueError(
            "E_SAFE_READ_INVENTORY: target worktree is not registered"
        )
    argv_tuple = (
        tuple(argv)
        if not isinstance(argv, (str, bytes)) and isinstance(argv, Sequence)
        else ()
    )
    argv_digest = contract_digest({"argv": argv_tuple})
    binding_digest = contract_digest(
        {
            "root": str(repository),
            "git_dir": str(git_dir),
            "common_dir": str(common_dir),
        }
    )
    validated = _validate_safe_read_argv(argv_tuple, repository)
    if validated is None:
        return _safe_read_rejected(argv_digest, binding_digest)
    executable_kind, closed_argv = validated
    governing_pattern_digest = (
        secret_pattern_set_digest()
        if executable_kind == "secret-scan-governing"
        else None
    )
    if governing_pattern_digest is not None:
        binding_digest = contract_digest(
            {
                "root": str(repository),
                "git_dir": str(git_dir),
                "common_dir": str(common_dir),
                "pattern_set_digest": governing_pattern_digest,
            }
        )
    try:
        records = _consume_worktree_inventory(
            worktree_inventory,
            expected_common_git_dir=common_dir,
        )
    except ValueError as error:
        raise ValueError(
            "E_SAFE_READ_INVENTORY: inventory expired or drifted"
        ) from error
    if not any(
        record.worktree == str(repository)
        and Path(record.git_dir).resolve() == git_dir
        for record in records
    ):
        raise ValueError(
            "E_SAFE_READ_INVENTORY: consumed inventory lost the target"
        )
    with tempfile.TemporaryDirectory(prefix="control-plane-safe-read-") as temporary:
        temporary_root = Path(temporary)
        global_config = temporary_root / "gitconfig"
        global_config.touch(mode=0o600)
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LC_ALL": "C",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(global_config),
            "TMPDIR": str(temporary_root),
        }
        if executable_kind in {"rg", "secret-scan-governing"}:
            executable = _safe_read_rg_executable()
            if executable is None:
                return _safe_read_rejected(
                    argv_digest,
                    binding_digest,
                    pattern_set_digest=governing_pattern_digest,
                )
            if executable_kind == "rg":
                command = [executable, *closed_argv[1:]]
            else:
                target = _safe_read_target(repository, closed_argv[2])
                if target is None:
                    return _safe_read_rejected(
                        argv_digest,
                        binding_digest,
                        pattern_set_digest=governing_pattern_digest,
                    )
                command = [
                    executable,
                    "--no-config",
                    "--quiet",
                    "-e",
                    "|".join(f"(?:{pattern})" for pattern in SECRET_PATTERNS),
                    "--",
                    str(repository / target),
                ]
        else:
            executable = _safe_read_git_executable()
            if executable is None:
                return _safe_read_rejected(argv_digest, binding_digest)
            subcommand = closed_argv[1]
            tail = list(closed_argv[2:])
            if subcommand in {"diff", "show"}:
                tail = ["--no-ext-diff", "--no-textconv", *tail]
            command = [
                executable,
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.pager=cat",
                "-c",
                f"pager.{subcommand}=cat",
                subcommand,
                *tail,
            ]
        completed = _bounded_process(
            command,
            root=repository,
            environment=environment,
            timeout_seconds=float(timeout_seconds),
            output_limit_bytes=output_limit_bytes,
            argv_digest=argv_digest,
            repository_binding_digest=binding_digest,
        )
        if governing_pattern_digest is None:
            return completed
        return CompletedSafeRead(
            argv_digest=completed.argv_digest,
            repository_binding_digest=completed.repository_binding_digest,
            status=completed.status,
            exit_code=completed.exit_code,
            timed_out=completed.timed_out,
            truncated=completed.truncated,
            stdout=completed.stdout,
            stderr=completed.stderr,
            stdout_bytes=completed.stdout_bytes,
            stderr_bytes=completed.stderr_bytes,
            duration_ms=completed.duration_ms,
            pattern_set_digest=governing_pattern_digest,
        )


def _pretool_targets(
    tool_name: str,
    tool_input: object,
    root: Path,
) -> tuple[str, ...] | None:
    if not isinstance(tool_input, Mapping):
        return None
    raw_targets: list[str] = []
    if tool_name in {"Edit", "Write"}:
        value = tool_input.get("file_path")
        if not isinstance(value, str) or not value:
            return None
        raw_targets.append(value)
    elif tool_name == "apply_patch":
        patch_text = tool_input.get("patch")
        if not isinstance(patch_text, str) or len(patch_text) > MAX_INPUT_BYTES:
            return None
        for line in patch_text.splitlines():
            match = re.fullmatch(
                r"[ ]*[*]{3} (?:Add File|Update File|Delete File|Move to): (.+)",
                line,
            )
            if match is not None:
                raw_targets.append(match.group(1))
        if not raw_targets:
            return None
    else:
        return None
    normalized: list[str] = []
    for raw in raw_targets:
        candidate = Path(raw)
        if candidate.is_absolute():
            if candidate.is_symlink():
                return None
            try:
                relative = candidate.resolve(strict=False).relative_to(root)
            except ValueError:
                return None
            value = relative.as_posix()
        else:
            value = raw
        safe = _safe_repo_path(root, value)
        if safe is None:
            return None
        normalized.append(safe)
    return tuple(sorted(set(normalized)))


def evaluate_pretool_use(
    payload: Mapping[str, Any],
    *,
    root: Path,
    governing_policy: GoverningPolicy,
    host_context: object | None,
    mode: str,
) -> PreToolDecision:
    """Evaluate real hook payload fields without trusting serialized authority."""

    from control_plane.host_bridge import (
        ValidatedHostRiskContext,
        consume_validated_host_risk_context,
    )
    from control_plane.lifecycle import TaskLease, TaskStore

    if (
        type(governing_policy) is not GoverningPolicy
        or not _governing_policy_is_issued(governing_policy)
    ):
        raise ValueError("E_HOOK_POLICY: governing policy is required")
    if mode not in {"audit", "soft-enforce", "enforce"}:
        raise ValueError("E_HOOK_MODE: unsupported hook mode")
    repository = _trusted_discover_repository(root)
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input", {})
    if tool_name in {"Read", "Glob", "Grep"}:
        return PreToolDecision(
            decision="allow",
            reason_code="read_for_clarification",
            effect_category="read_only_known",
        )
    if tool_name == "Bash":
        command = (
            str(tool_input.get("command", ""))
            if isinstance(tool_input, Mapping)
            else ""
        )
        effect = classify_bash_command(
            command,
            root=repository,
            governing_policy=governing_policy,
        )
        if effect.category == "read_only_known":
            decision = "allow"
        elif effect.category == "read_only_unsanitized":
            decision = "advisory" if mode == "audit" else "deny"
        elif effect.category == "git_effect":
            decision = "advisory" if mode == "audit" else "deny"
        elif effect.category == "destructive":
            decision = "advisory" if mode == "audit" else "deny"
        else:
            decision = "advisory" if mode == "audit" else "deny"
        return PreToolDecision(
            decision=decision,
            reason_code=effect.reason_code,
            effect_category=effect.category,
            targets=effect.targets,
        )
    if tool_name.startswith("mcp__"):
        return PreToolDecision(
            decision="advisory" if mode == "audit" else "ask",
            reason_code="pending_host_authorization_bridge",
            effect_category="external_effect",
        )
    if tool_name not in {"Edit", "Write", "apply_patch"}:
        return PreToolDecision(
            decision="advisory" if mode == "audit" else "deny",
            reason_code="unrecognized_tool_effect",
            effect_category="ambiguous_shell_command",
        )
    targets = _pretool_targets(tool_name, tool_input, repository)
    if targets is None:
        return PreToolDecision(
            decision="advisory" if mode == "audit" else "deny",
            reason_code="unresolvable_write_scope",
            effect_category="write_paths_known",
        )
    if type(host_context) is not ValidatedHostRiskContext:
        return PreToolDecision(
            decision="advisory" if mode == "audit" else "deny",
            reason_code="pending_host_authorization_bridge",
            effect_category="write_paths_known",
            targets=targets,
        )
    try:
        if (
            governing_policy.session_id != host_context.session_id
            or governing_policy.invocation_id
            != host_context.invocation_id
        ):
            raise ValueError(
                "RS_HOST_CONTEXT: governing policy callback differs"
            )
        bindings = consume_validated_host_risk_context(
            host_context,
            expected_repository_identity=repository,
            expected_worktree_identity=repository,
            expected_branch=host_context.branch,
            expected_head=host_context.head,
            expected_session_id=host_context.session_id,
            expected_invocation_id=host_context.invocation_id,
            expected_task_id=host_context.task_id,
            expected_task_digest=host_context.task_digest,
            expected_task_state_digest=host_context.task_state_digest,
        )
        if (
            bindings["clarification_status"] != "resolved"
            or bindings["effect"] != "local_write"
            or bindings["authorization_status"] != "granted"
        ):
            raise ValueError(
                "RS_HOST_CONTEXT: current write authorization is absent"
            )
        state_dir = _trusted_worktree_git_dir(repository)
        live_state = TaskStore(state_dir).status(
            str(bindings["task_id"])
        )
        if (
            contract_digest(live_state)
            != bindings["task_state_digest"]
        ):
            raise ValueError(
                "RS_HOST_CONTEXT: task state changed before write"
            )
        lease = TaskLease.validate(
            state_dir,
            task_id=str(bindings["task_id"]),
            worktree=str(repository),
            branch=str(bindings["branch"]),
            session_id=str(bindings["session_id"]),
            policy_digest=governing_policy.policy_digest,
            changed_paths=list(targets),
        )
        if lease.get("lease_digest") != bindings["lease_digest"]:
            raise ValueError(
                "RS_HOST_CONTEXT: validated lease digest changed"
            )
    except ValueError as error:
        reason = (
            "write_outside_task_lease"
            if str(error).startswith("E_LEASE_SCOPE:")
            else "worktree_or_task_identity_mismatch"
        )
        return PreToolDecision(
            decision="advisory" if mode == "audit" else "deny",
            reason_code=reason,
            effect_category="write_paths_known",
            targets=targets,
        )
    return PreToolDecision(
        decision="allow",
        reason_code="write_within_task_lease",
        effect_category="write_paths_known",
        targets=targets,
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


def _session_hash(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("E_HOOK_SESSION: a non-empty session ID is required")
    try:
        encoded = session_id.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("E_HOOK_SESSION: session ID is not UTF-8") from error
    if len(encoded) > 4_096:
        raise ValueError("E_HOOK_SESSION: session ID is oversized")
    return sha256(encoded).hexdigest()


def warning_state_path(root: Path, session_id: str) -> Path:
    """Return the hashed worktree-local warning dedupe path."""

    repository = _trusted_discover_repository(root)
    return (
        _trusted_worktree_git_dir(repository)
        / "codex-control-plane"
        / "warnings"
        / f"{_session_hash(session_id)}.json"
    )


def current_warning_view_path(root: Path, session_id: str) -> Path:
    """Return the separate restart-safe warning view path."""

    repository = _trusted_discover_repository(root)
    return (
        _trusted_worktree_git_dir(repository)
        / "codex-control-plane"
        / "warning-views"
        / f"{_session_hash(session_id)}.json"
    )


def _warning_state_anchor(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.name == "codex-control-plane":
            anchor = candidate.parent
            if anchor.is_symlink() or not anchor.is_dir():
                break
            return anchor
    raise ValueError("E_WARNING_STATE: state directory escaped Git dir")


def _open_private_directory(path: Path) -> int:
    anchor = _warning_state_anchor(path)
    try:
        relative = path.relative_to(anchor)
    except ValueError as error:
        raise ValueError(
            "E_WARNING_STATE: state directory escaped Git dir"
        ) from error
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(anchor, flags)
    try:
        for component in relative.parts:
            if component in {"", ".", ".."}:
                raise ValueError(
                    "E_WARNING_STATE: state directory is unsafe"
                )
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(component, flags, dir_fd=descriptor)
            if not stat.S_ISDIR(os.fstat(child).st_mode):
                os.close(child)
                raise ValueError(
                    "E_WARNING_STATE: state directory is unsafe"
                )
            os.fchmod(child, 0o700)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_private_directory(path: Path) -> None:
    descriptor = _open_private_directory(path)
    os.close(descriptor)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    payload = (canonical_json(value) + "\n").encode("utf-8")
    directory = _open_private_directory(path.parent)
    temporary = f".{path.name}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            path.name,
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        os.chmod(
            path.name,
            0o600,
            dir_fd=directory,
            follow_symlinks=False,
        )
        os.fsync(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


@contextmanager
def _warning_lock(root: Path, session_id: str):
    repository = _trusted_discover_repository(root)
    lock_dir = (
        _trusted_worktree_git_dir(repository)
        / "codex-control-plane"
        / "warning-locks"
    )
    directory = _open_private_directory(lock_dir)
    lock_name = f"{_session_hash(session_id)}.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_name, flags, 0o600, dir_fd=directory)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        os.close(directory)


def _read_closed_json(path: Path) -> Mapping[str, object] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
        raise ValueError("E_WARNING_STATE: state file is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("E_WARNING_STATE: state file is invalid") from error
    if not isinstance(value, Mapping):
        raise ValueError("E_WARNING_STATE: state file is invalid")
    return value


def should_emit_warning(
    root: Path, session_id: str, fingerprint: str
) -> bool:
    """Persist a closed dedupe record and return whether it changed."""

    if not isinstance(fingerprint, str) or SHA256_DIGEST.fullmatch(
        fingerprint
    ) is None:
        raise ValueError("E_WARNING_STATE: fingerprint is invalid")
    path = warning_state_path(root, session_id)
    with _warning_lock(root, session_id):
        current = _read_closed_json(path)
        if current is not None:
            if (
                set(current)
                != {"schema_version", "fingerprint", "emitted_at"}
                or current.get("schema_version") != 1
                or not isinstance(current.get("emitted_at"), int)
                or not isinstance(current.get("fingerprint"), str)
                or SHA256_DIGEST.fullmatch(str(current["fingerprint"])) is None
            ):
                raise ValueError("E_WARNING_STATE: dedupe record is invalid")
            if current["fingerprint"] == fingerprint:
                return False
        _atomic_json(
            path,
            {
                "schema_version": 1,
                "fingerprint": fingerprint,
                "emitted_at": time.time_ns(),
            },
        )
        return True


def _git_observation(root: Path, arguments: list[str]) -> str:
    return _trusted_git_text(root, arguments)


def _current_route_marker(root: Path) -> str:
    raw_task_id = os.environ.get("CODEX_CONTROL_PLANE_TASK_ID", "")
    if not validate_task_id(raw_task_id):
        return "pending_framing"
    path = (
        _trusted_worktree_git_dir(root)
        / "codex-control-plane"
        / "tasks"
        / f"{raw_task_id}.json"
    )
    try:
        state = _read_closed_json(path)
    except ValueError:
        return "pending_framing"
    if state is None:
        return "pending_framing"
    decision = state.get("decision_digest")
    return (
        str(decision)
        if isinstance(decision, str) and SHA256_DIGEST.fullmatch(decision)
        else "pending_framing"
    )


def _fingerprint_base_observation(
    repository: Path,
    governing_policy: GoverningPolicy | None,
) -> tuple[str, str]:
    if governing_policy is not None:
        if (
            type(governing_policy) is not GoverningPolicy
            or not _governing_policy_is_issued(governing_policy)
        ):
            raise ValueError("E_HOOK_POLICY: governing policy is required")
        policy: object = governing_policy.policy
    else:
        path = repository / ".codex" / "project-policy.toml"
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 131_072
            ):
                raise ValueError
            policy = tomllib.loads(path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            ValueError,
            tomllib.TOMLDecodeError,
        ):
            return "unknown", "unknown"
    git_policy = (
        policy.get("git")
        if isinstance(policy, Mapping)
        else None
    )
    remote = (
        git_policy.get("remote")
        if isinstance(git_policy, Mapping)
        else None
    )
    base_branch = (
        git_policy.get("base_branch")
        if isinstance(git_policy, Mapping)
        else None
    )
    git_name_pattern = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,126}$",
        re.ASCII,
    )
    if (
        not isinstance(remote, str)
        or git_name_pattern.fullmatch(remote) is None
        or not isinstance(base_branch, str)
        or git_name_pattern.fullmatch(base_branch) is None
    ):
        return "unknown", "unknown"
    ref = f"{remote}/{base_branch}"
    return ref, _git_observation(
        repository,
        ["rev-parse", "--verify", ref],
    )


def risk_fingerprint(
    root: Path,
    *,
    governing_policy: GoverningPolicy | None = None,
) -> str:
    """Hash only technical risk inputs; never include prompt or command text."""

    repository = _trusted_discover_repository(root)
    with _trusted_git_subprocess_environment():
        status = evaluate_risk_status(
            repository,
            governing_policy,
            task_state=None,
            route_decision_hint=None,
            host_context=None,
            remote=None,
        )
    base_ref, base_commit = _fingerprint_base_observation(
        repository,
        governing_policy,
    )
    payload = {
        "branch": _git_observation(
            repository, ["symbolic-ref", "--quiet", "--short", "HEAD"]
        ),
        "base_ref": base_ref,
        "base_commit": base_commit,
        "policy_digest": _digest(
            repository / ".codex" / "project-policy.toml"
        ),
        "registry_digest": _digest(
            repository / ".codex" / "resource-registry.toml"
        ),
        "lock_digest": _digest(
            repository / ".codex" / "control-plane.lock"
        ),
        "route": _current_route_marker(repository),
        "risk": {
            "local": status.dimensions["local"].status,
            "remote": status.dimensions["remote"].status,
        },
    }
    return contract_digest(payload)


def render_risk_warning(
    risk_status: RiskStatus,
    interaction: InteractionRecommendationView | None,
    *,
    trigger: str,
    framing_status: str,
    governing_policy: GoverningPolicy,
) -> HookWarningPayload:
    """Render one closed warning from opaque governing inputs."""

    if (
        type(governing_policy) is not GoverningPolicy
        or not _governing_policy_is_issued(governing_policy)
    ):
        raise ValueError("E_HOOK_POLICY: governing policy is required")
    if type(risk_status) is not RiskStatus:
        raise ValueError("E_HOOK_RISK: RiskStatus is required")
    if interaction is not None and type(interaction) is not InteractionRecommendationView:
        raise ValueError("E_HOOK_INTERACTION: interaction view is invalid")
    local = risk_status.dimensions["local"].status
    remote = risk_status.dimensions["remote"].status
    if local == "FAIL":
        action = "STOP"
        reason = "RS_LOCAL_STATUS_FAIL"
    elif remote == "FAIL":
        action = "STOP"
        reason = "RS_REMOTE_STATUS_FAIL"
    elif local == "UNKNOWN":
        action = "PAUSE_AND_VERIFY"
        reason = "RS_LOCAL_STATUS_UNKNOWN"
    elif remote == "UNKNOWN":
        action = "CONTINUE_WITH_CAUTION"
        reason = "RS_REMOTE_PROTECTION_UNVERIFIED"
    else:
        action = "SAFE_PATH_CONFIRMED"
        reason = "RS_ALL_GATES_PASS"
    interaction_value: str | Mapping[str, object] = (
        "pending_framing" if interaction is None else interaction.as_dict()
    )
    return HookWarningPayload(
        title="CONTROL PLANE RISK",
        local=local,
        remote=remote,
        action=action,
        reason_code=reason,
        safe_path=SAFE_PATH,
        interaction=interaction_value,
        automatic_change=False,
        trigger=trigger,
        framing_status=framing_status,
    )


def _minimal_unknown_warning(
    *, trigger: str, framing_status: str
) -> HookWarningPayload:
    return HookWarningPayload(
        title="CONTROL PLANE RISK",
        local="UNKNOWN",
        remote="UNKNOWN",
        action="PAUSE_AND_VERIFY",
        reason_code="RS_WARNING_STATE_UNKNOWN",
        safe_path=SAFE_PATH,
        interaction="pending_framing",
        automatic_change=False,
        trigger=trigger,
        framing_status=framing_status,
    )


def publish_current_warning_view(
    root: Path,
    session_id: str,
    payload: HookWarningPayload,
    *,
    task_digest: str,
    route_digest: str,
    fingerprint: str,
    generation: int,
) -> CurrentWarningView:
    """Atomically publish the current restart-safe warning generation."""

    if (
        type(payload) is not HookWarningPayload
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or any(
            not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
            for value in (task_digest, route_digest, fingerprint)
        )
    ):
        raise ValueError("E_WARNING_VIEW: warning view bindings are invalid")
    payload_digest = contract_digest(payload.as_dict())
    view = CurrentWarningView(
        schema_version=1,
        generation=generation,
        payload=payload,
        task_digest=task_digest,
        route_digest=route_digest,
        fingerprint=fingerprint,
        payload_digest=payload_digest,
    )
    path = current_warning_view_path(root, session_id)
    with _warning_lock(root, session_id):
        current = _read_closed_json(path)
        if current is not None:
            current_generation = current.get("generation")
            if (
                not isinstance(current_generation, int)
                or isinstance(current_generation, bool)
                or generation <= current_generation
            ):
                raise ValueError(
                    "E_WARNING_VIEW: generation must move forward"
                )
        _atomic_json(path, view.as_dict())
    return view


def publish_framed_current_warning_view(
    root: Path,
    session_id: str,
    *,
    risk_status: RiskStatus,
    interaction: InteractionRecommendationView,
    governing_policy: GoverningPolicy,
    task_digest: str,
    route_digest: str,
    generation: int,
) -> CurrentWarningView:
    """Publish the framed view only from opaque governing inputs."""

    if (
        type(governing_policy) is not GoverningPolicy
        or not _governing_policy_is_issued(governing_policy)
    ):
        raise ValueError("E_HOOK_POLICY: governing policy is required")
    if type(risk_status) is not RiskStatus:
        raise ValueError("E_HOOK_RISK: RiskStatus is required")
    if type(interaction) is not InteractionRecommendationView:
        raise ValueError("E_HOOK_INTERACTION: interaction view is invalid")
    try:
        closed_interaction = render_interaction_recommendation(
            interaction.mode,
            interaction.reason_codes,
        )
    except ValueError as error:
        raise ValueError(
            "E_HOOK_INTERACTION: interaction view is invalid"
        ) from error
    if closed_interaction != interaction:
        raise ValueError(
            "E_HOOK_INTERACTION: interaction view is invalid"
        )
    fingerprint = risk_fingerprint(Path(root))
    payload = render_risk_warning(
        risk_status,
        interaction,
        trigger="post_compact",
        framing_status="framed",
        governing_policy=governing_policy,
    )
    return publish_current_warning_view(
        Path(root),
        session_id,
        payload,
        task_digest=task_digest,
        route_digest=route_digest,
        fingerprint=fingerprint,
        generation=generation,
    )


def _warning_payload_from_mapping(
    value: object,
) -> HookWarningPayload:
    if not isinstance(value, Mapping):
        raise ValueError("E_WARNING_VIEW: payload is invalid")
    expected = {
        "title",
        "local",
        "remote",
        "action",
        "reason_code",
        "safe_path",
        "interaction",
        "automatic_change",
        "trigger",
        "framing_status",
    }
    if set(value) != expected or value["automatic_change"] is not False:
        raise ValueError("E_WARNING_VIEW: payload schema is invalid")
    return HookWarningPayload(
        title=str(value["title"]),
        local=str(value["local"]),
        remote=str(value["remote"]),
        action=str(value["action"]),
        reason_code=str(value["reason_code"]),
        safe_path=str(value["safe_path"]),
        interaction=value["interaction"],
        automatic_change=False,
        trigger=str(value["trigger"]),
        framing_status=str(value["framing_status"]),
    )


def load_current_warning_view(
    root: Path,
    session_id: str,
    *,
    expected_task_digest: str,
    expected_route_digest: str,
    expected_fingerprint: str,
) -> CurrentWarningView | None:
    """Load only an exact same-session current view; drift is inert."""

    if any(
        not isinstance(value, str) or SHA256_DIGEST.fullmatch(value) is None
        for value in (
            expected_task_digest,
            expected_route_digest,
            expected_fingerprint,
        )
    ):
        return None
    path = current_warning_view_path(root, session_id)
    try:
        with _warning_lock(root, session_id):
            raw = _read_closed_json(path)
            if raw is None or set(raw) != {
                "schema_version",
                "generation",
                "payload",
                "task_digest",
                "route_digest",
                "fingerprint",
                "payload_digest",
            }:
                return None
            payload = _warning_payload_from_mapping(raw["payload"])
            if (
                raw.get("schema_version") != 1
                or not isinstance(raw.get("generation"), int)
                or isinstance(raw.get("generation"), bool)
                or raw.get("task_digest") != expected_task_digest
                or raw.get("route_digest") != expected_route_digest
                or raw.get("fingerprint") != expected_fingerprint
                or raw.get("payload_digest")
                != contract_digest(payload.as_dict())
            ):
                return None
            return CurrentWarningView(
                schema_version=1,
                generation=int(raw["generation"]),
                payload=payload,
                task_digest=expected_task_digest,
                route_digest=expected_route_digest,
                fingerprint=expected_fingerprint,
                payload_digest=str(raw["payload_digest"]),
            )
    except (OSError, ValueError):
        return None


def gc_current_warning_view(
    root: Path,
    session_id: str,
    *,
    active_generation: int,
) -> bool:
    """Delete only a view proven older than the active task generation."""

    if (
        not isinstance(active_generation, int)
        or isinstance(active_generation, bool)
        or active_generation < 0
    ):
        raise ValueError("E_WARNING_VIEW_GC: active generation is invalid")
    path = current_warning_view_path(root, session_id)
    with _warning_lock(root, session_id):
        raw = _read_closed_json(path)
        if raw is None:
            return False
        generation = raw.get("generation")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
        ):
            raise ValueError("E_WARNING_VIEW_GC: current view is invalid")
        if generation >= active_generation:
            return False
        directory = _open_private_directory(path.parent)
        try:
            metadata = os.stat(
                path.name,
                dir_fd=directory,
                follow_symlinks=False,
            )
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(
                    "E_WARNING_VIEW_GC: current view is unsafe"
                )
            os.unlink(path.name, dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(directory)
    return True


def _warning_context(payload: HookWarningPayload) -> str:
    return canonical_json(payload.as_dict())


def _hook_warning_result(
    event: str, payload: HookWarningPayload
) -> dict[str, object]:
    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": _warning_context(payload),
        },
    }


def _untrusted_pretool_reason(
    tool_name: str, tool_input: object, root: Path
) -> tuple[str | None, bool]:
    """Classify only mechanics that cannot be weakened by hook JSON."""

    if tool_name == "Bash":
        command = (
            str(tool_input.get("command", ""))
            if isinstance(tool_input, Mapping)
            else ""
        )
        if any(pattern.search(command) for pattern in DESTRUCTIVE_PATTERNS):
            return "destructive_command_requires_explicit_authority", True
        if _SHELL_META.search(command):
            return "ambiguous_shell_command", True
        try:
            argv = tuple(shlex.split(command, posix=True))
        except ValueError:
            return "ambiguous_shell_command", True
        if (
            len(argv) >= 5
            and argv[0]
            in {
                "scripts/control-plane",
                str(root / "scripts" / "control-plane"),
            }
            and argv[1:3] == ("safe-read", "--repo")
            and Path(argv[3]).resolve(strict=False) == root
            and argv[4] == "--"
            and _validate_safe_read_argv(argv[5:], root) is not None
        ):
            return None, False
        parsed_git = _git_command_tokens(argv, root)
        if parsed_git is not None and parsed_git[0][0] in {
            "status",
            "diff",
            "log",
            "show",
            "rev-parse",
        }:
            return "raw_read_requires_safe_read", True
        if argv and argv[0] == "rg":
            return "raw_read_requires_safe_read", True
        if parsed_git is not None and parsed_git[0][0] == "push":
            return "git_effect_not_host_attested", True
        return "unresolved_bash_effect", True
    if tool_name in {"Edit", "Write", "apply_patch"}:
        return "pending_host_authorization_bridge", True
    if tool_name.startswith("mcp__"):
        return "mcp_use_requires_task_authorization_and_egress_check", False
    return "unrecognized_tool_effect", True


def _task_warning_bindings(
    root: Path,
) -> tuple[str, str] | None:
    task_id = os.environ.get("CODEX_CONTROL_PLANE_TASK_ID", "")
    if not validate_task_id(task_id):
        return None
    path = (
        _trusted_worktree_git_dir(root)
        / "codex-control-plane"
        / "tasks"
        / f"{task_id}.json"
    )
    try:
        state = _read_closed_json(path)
    except ValueError:
        return None
    if state is None:
        return None
    task_digest = state.get("task_digest")
    route_digest = state.get("decision_digest")
    if (
        not isinstance(task_digest, str)
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or not isinstance(route_digest, str)
        or SHA256_DIGEST.fullmatch(route_digest) is None
    ):
        return None
    return task_digest, route_digest


def _manifest(root: Path) -> str:
    raw_task_id = os.environ.get("CODEX_CONTROL_PLANE_TASK_ID", "")
    task_id = raw_task_id if validate_task_id(raw_task_id) else ""
    state = "unbound"
    rendered_task_id = task_id or ("invalid" if raw_task_id else "unset")
    if task_id:
        state_path = (
            _trusted_worktree_git_dir(root)
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
        root = _trusted_discover_repository(cwd)
    except Exception:
        return None
    if event == "UserPromptSubmit":
        session_id = payload.get("session_id")
        warning = _minimal_unknown_warning(
            trigger="user_prompt",
            framing_status="pending_framing",
        )
        if not isinstance(session_id, str) or not session_id:
            return _hook_warning_result(event, warning)
        try:
            fingerprint = risk_fingerprint(root)
            if not should_emit_warning(root, session_id, fingerprint):
                return None
        except (OSError, ValueError):
            return _hook_warning_result(event, warning)
        return _hook_warning_result(event, warning)
    if event == "SessionStart":
        if payload.get("source") != "compact":
            return None
        session_id = payload.get("session_id")
        fallback = _minimal_unknown_warning(
            trigger="post_compact",
            framing_status="pending_framing",
        )
        if not isinstance(session_id, str) or not session_id:
            return _hook_warning_result(event, fallback)
        try:
            fingerprint = risk_fingerprint(root)
            bindings = _task_warning_bindings(root)
            if bindings is not None:
                current = load_current_warning_view(
                    root,
                    session_id,
                    expected_task_digest=bindings[0],
                    expected_route_digest=bindings[1],
                    expected_fingerprint=fingerprint,
                )
                if current is not None:
                    return _hook_warning_result(event, current.payload)
        except (OSError, ValueError):
            pass
        return _hook_warning_result(event, fallback)
    if event == "PreToolUse":
        tool_name = str(payload.get("tool_name", ""))
        tool_input = payload.get("tool_input", {})
        reason, block_without_host = _untrusted_pretool_reason(
            tool_name, tool_input, root
        )
        if reason is not None:
            mode = os.environ.get(
                "CODEX_CONTROL_PLANE_HOOK_MODE", "audit"
            )
            deny = mode == "enforce" or (
                mode == "soft-enforce" and block_without_host
            )
            if deny:
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "CONTROL_PLANE_SOFT_ENFORCE: " + reason
                        ),
                    }
                }
            return _hook_warning_result(
                "PreToolUse",
                _minimal_unknown_warning(
                    trigger="pre_red_action",
                    framing_status=(
                        "framed"
                        if _task_warning_bindings(root) is not None
                        else "pending_framing"
                    ),
                ),
            )
        return None
    if event == "Stop":
        if payload.get("stop_hook_active") is True:
            return {"continue": True}
        task_id = os.environ.get("CODEX_CONTROL_PLANE_TASK_ID")
        if not task_id:
            return {"continue": True}
        receipt = (
            _trusted_worktree_git_dir(root)
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


def _record_hook_output_metric(
    payload: Mapping[str, Any],
    rendered: str,
) -> None:
    """Record only the byte count, bound to the active task and lease."""

    if not rendered:
        return
    task_id = os.environ.get("CODEX_CONTROL_PLANE_TASK_ID", "")
    session_id = payload.get("session_id")
    event = payload.get("hook_event_name")
    if (
        not validate_task_id(task_id)
        or not validate_task_id(session_id)
        or not isinstance(event, str)
        or not event
    ):
        return
    try:
        root = _trusted_discover_repository(
            Path(str(payload.get("cwd", ".")))
        )
        state_dir = _trusted_worktree_git_dir(root)
        from control_plane.lifecycle import TaskStore

        store = TaskStore(state_dir)
        state = store.status(task_id)
        lease = store._read_owner_lease(task_id)
    except (OSError, ValueError):
        return
    task_digest = state.get("task_digest")
    route_digest = state.get("decision_digest")
    if (
        not isinstance(lease, Mapping)
        or lease.get("task_id") != task_id
        or lease.get("worktree") != str(root)
        or lease.get("session_id") != session_id
        or not isinstance(task_digest, str)
        or SHA256_DIGEST.fullmatch(task_digest) is None
        or not isinstance(route_digest, str)
        or SHA256_DIGEST.fullmatch(route_digest) is None
    ):
        return
    raw_event_identity = payload.get("tool_use_id", payload.get("turn_id", ""))
    event_identity_digest = contract_digest(
        {
            "event": event,
            "identity": (
                raw_event_identity
                if isinstance(raw_event_identity, str)
                else ""
            ),
        }
    )
    invocation_id = (
        "hook-"
        + contract_digest(
            {
                "task_id": task_id,
                "session_id": session_id,
                "event_identity_digest": event_identity_digest,
                "output_digest": contract_digest({"rendered": rendered}),
            }
        ).removeprefix("sha256:")
    )
    tool_use_id = payload.get("tool_use_id")
    store.record_context_metrics(
        task_id,
        task_digest=task_digest,
        session_id=session_id,
        invocation_id=invocation_id,
        subject_digest=route_digest,
        runtime_metrics={
            "hook_output_bytes": len(rendered.encode("utf-8")),
            "tool_use_id": (
                tool_use_id if validate_task_id(tool_use_id) else None
            ),
        },
        host_metrics=None,
    )


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
    rendered = "" if result is None else _compact_output(result)
    _record_hook_output_metric(payload, rendered)
    return rendered
