"""Integrity validation for the project control-plane lock."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import tomllib

from . import __version__


@dataclass(frozen=True)
class LockIssue:
    code: str
    path: str
    message: str


def _digest(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def runtime_digest(
    root: Path,
    package: str | None = None,
    *,
    runtime_layout: str | None = None,
) -> str:
    selected = package or "control_plane"
    layout = runtime_layout or (
        "source" if selected == "control_plane" else "isolated"
    )
    expected_package = {
        "source": "control_plane",
        "isolated": "codex_control_plane_runtime_v2",
    }
    if layout not in expected_package or selected != expected_package[layout]:
        raise ValueError(
            "L_RUNTIME_LAYOUT: runtime layout and package are inconsistent"
        )
    runtime = (
        root / "control_plane"
        if layout == "source"
        else root / ".codex" / "runtime" / selected
    )
    if runtime.is_symlink() or not runtime.is_dir():
        raise ValueError("L_RUNTIME_LAYOUT: selected runtime is unavailable")
    modules = sorted(runtime.glob("*.py"))
    if not modules:
        raise ValueError("L_RUNTIME_LAYOUT: selected runtime is empty")
    hasher = sha256()
    for path in modules:
        if path.is_symlink() or not path.is_file():
            raise ValueError("L_RUNTIME_LAYOUT: runtime module is invalid")
        hasher.update(path.name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return f"sha256:{hasher.hexdigest()}"


def validate_lock(root: Path) -> list[LockIssue]:
    lock_path = root / ".codex" / "control-plane.lock"
    try:
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return [LockIssue("L_PARSE", str(lock_path), "Control-plane lock is unavailable or invalid.")]
    issues: list[LockIssue] = []
    if lock.get("schema_version") != 1:
        issues.append(LockIssue("L_SCHEMA", "schema_version", "Only lock schema 1 is supported."))
    if lock.get("product_version") != __version__:
        issues.append(
            LockIssue(
                "L_VERSION",
                "product_version",
                f"Lock does not select control-plane v{__version__}.",
            )
        )
    for schema_name in (
        "policy_schema",
        "registry_schema",
        "task_schema",
        "route_schema",
        "receipt_schema",
        "clarification_schema",
        "risk_schema",
    ):
        if lock.get(schema_name) != 1:
            issues.append(
                LockIssue(
                    "L_SCHEMA",
                    schema_name,
                    f"{schema_name} must select schema 1.",
                )
            )
    layout = lock.get("runtime_layout")
    package = lock.get("runtime_package")
    expected_package = {
        "source": "control_plane",
        "isolated": "codex_control_plane_runtime_v2",
    }
    if layout not in expected_package:
        issues.append(
            LockIssue(
                "L_RUNTIME_LAYOUT",
                "runtime_layout",
                "Lock must select source or isolated runtime layout.",
            )
        )
    if package != expected_package.get(layout):
        issues.append(
            LockIssue(
                "L_RUNTIME_LAYOUT",
                "runtime_layout",
                "Runtime layout and package form an invalid closed pair.",
            )
        )
        issues.append(
            LockIssue(
                "L_RUNTIME_PACKAGE",
                "runtime_package",
                "Runtime package must match the selected closed layout.",
            )
        )
    expected = {
        "project_policy": root / ".codex" / "project-policy.toml",
        "resource_registry": root / ".codex" / "resource-registry.toml",
        "hooks": root / ".codex" / "hooks.json",
        "hook_entrypoint": root / ".codex" / "hooks" / "control_plane_hook.py",
        "git_pre_commit": root / ".codex" / "git-hooks" / "pre-commit",
        "git_pre_push": root / ".codex" / "git-hooks" / "pre-push",
        "entrypoint": root / "scripts" / "control-plane",
    }
    digests = lock.get("digests", {})
    for name, path in expected.items():
        if not path.is_file() or digests.get(name) != _digest(path):
            issues.append(LockIssue("L_DIGEST", name, f"Locked digest does not match {path.name}."))
    try:
        observed_runtime = runtime_digest(
            root,
            str(package),
            runtime_layout=str(layout),
        )
    except ValueError:
        observed_runtime = None
        if layout in expected_package and package == expected_package.get(layout):
            issues.append(
                LockIssue(
                    "L_RUNTIME_LAYOUT",
                    "runtime_layout",
                    "Selected runtime is missing, empty, symlinked, or invalid.",
                )
            )
    if digests.get("runtime") != observed_runtime:
        issues.append(LockIssue("L_DIGEST", "runtime", "Locked runtime digest does not match."))
    if lock.get("hook_mode") != "audit":
        issues.append(LockIssue("L_HOOK_MODE", "hook_mode", "Initial v2 hook mode must remain audit."))
    if lock.get("hook_trust") != "pending_hook_trust":
        issues.append(LockIssue("L_HOOK_TRUST", "hook_trust", "Hook trust must remain pending until reviewed in /hooks."))
    return issues
