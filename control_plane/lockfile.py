"""Integrity validation for the project control-plane lock."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class LockIssue:
    code: str
    path: str
    message: str


def _digest(path: Path) -> str:
    return f"sha256:{sha256(path.read_bytes()).hexdigest()}"


def runtime_digest(root: Path, package: str | None = None) -> str:
    selected = package or "control_plane"
    if selected == "control_plane" and (root / "control_plane").is_dir():
        runtime = root / "control_plane"
    else:
        runtime = root / ".codex" / "runtime" / selected
    hasher = sha256()
    for path in sorted(runtime.glob("*.py")):
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
    if lock.get("product_version") != "2.0.0":
        issues.append(LockIssue("L_VERSION", "product_version", "Lock does not select control-plane v2.0.0."))
    package = lock.get("runtime_package")
    if package not in {"control_plane", "codex_control_plane_runtime_v2"}:
        issues.append(
            LockIssue(
                "L_RUNTIME_PACKAGE",
                "runtime_package",
                "Lock must select an approved isolated runtime package.",
            )
        )
    expected = {
        "project_policy": root / ".codex" / "project-policy.toml",
        "resource_registry": root / ".codex" / "resource-registry.toml",
        "hooks": root / ".codex" / "hooks.json",
        "hook_entrypoint": root / ".codex" / "hooks" / "control_plane_hook.py",
        "entrypoint": root / "scripts" / "control-plane",
    }
    digests = lock.get("digests", {})
    for name, path in expected.items():
        if not path.is_file() or digests.get(name) != _digest(path):
            issues.append(LockIssue("L_DIGEST", name, f"Locked digest does not match {path.name}."))
    if digests.get("runtime") != runtime_digest(root, str(package)):
        issues.append(LockIssue("L_DIGEST", "runtime", "Locked runtime digest does not match."))
    if lock.get("hook_mode") != "audit":
        issues.append(LockIssue("L_HOOK_MODE", "hook_mode", "Initial v2 hook mode must remain audit."))
    if lock.get("hook_trust") != "pending_hook_trust":
        issues.append(LockIssue("L_HOOK_TRUST", "hook_trust", "Hook trust must remain pending until reviewed in /hooks."))
    return issues
