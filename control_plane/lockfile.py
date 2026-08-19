"""Exact, bounded integrity lock for the Control Plane Core runtime."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import stat
import tomllib

from . import __version__


ACTIVE_RUNTIME_MODULES = (
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
    "survey.py",
    "task_state.py",
    "toolchain.py",
    "verification.py",
)
RUNTIME_PACKAGES = {
    "source": "control_plane",
    "isolated": "codex_control_plane_runtime_core_v3",
}
LOCK_MAX_BYTES = 64 * 1024
AUTHORITY_FILE_MAX_BYTES = 1024 * 1024
RUNTIME_MODULE_MAX_BYTES = 1024 * 1024
RUNTIME_TOTAL_MAX_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
RUNTIME_DIRECTORY_ENTRY_MAX = 256
DATALESS_FLAG = 0x40000000


@dataclass(frozen=True)
class LockIssue:
    code: str
    path: str
    message: str


def _is_dataless(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_flags", 0)) & DATALESS_FLAG)


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _is_private_regular(metadata: os.stat_result, *, limit: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
        and metadata.st_size <= limit
        and not _is_dataless(metadata)
    )


def _private_directory_metadata(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ValueError(
            "E_RUNTIME_MODULE_SET: runtime directory is unavailable"
        ) from error
    if _is_dataless(metadata):
        raise ValueError("E_RUNTIME_DATALESS: runtime directory is an APFS placeholder")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ValueError("E_RUNTIME_MODULE_SET: runtime directory is not private")
    return metadata


def _bounded_private_bytes(path: Path, *, limit: int, code: str) -> bytes:
    """Read one regular, private, materialized file without following links."""

    try:
        before = path.lstat()
    except OSError as error:
        raise ValueError(f"{code}: file is unavailable") from error
    if _is_dataless(before):
        raise ValueError("E_RUNTIME_DATALESS: file is an APFS placeholder")
    if not _is_private_regular(before, limit=limit):
        raise ValueError(f"{code}: file is not bounded regular private content")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"{code}: file cannot be opened safely") from error
    try:
        opened = os.fstat(descriptor)
        if _is_dataless(opened):
            raise ValueError("E_RUNTIME_DATALESS: file is an APFS placeholder")
        if not _is_private_regular(opened, limit=limit) or _identity(opened) != _identity(before):
            raise ValueError(f"{code}: file identity changed before read")

        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(
                descriptor,
                min(READ_CHUNK_BYTES, limit + 1 - observed),
            )
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > limit:
                raise ValueError(f"{code}: file exceeds its byte limit")
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    try:
        after_path = path.lstat()
    except OSError as error:
        raise ValueError(f"{code}: file disappeared during read") from error
    if (
        not _is_private_regular(after_open, limit=limit)
        or not _is_private_regular(after_path, limit=limit)
        or _identity(before) != _identity(after_open)
        or _identity(before) != _identity(after_path)
    ):
        raise ValueError(f"{code}: file changed during read")
    return b"".join(chunks)


def _digest(path: Path) -> str:
    payload = _bounded_private_bytes(
        path,
        limit=AUTHORITY_FILE_MAX_BYTES,
        code="L_DIGEST",
    )
    return f"sha256:{sha256(payload).hexdigest()}"


def _runtime_directory(root: Path, package: str, layout: str) -> Path:
    if layout not in RUNTIME_PACKAGES or package != RUNTIME_PACKAGES[layout]:
        raise ValueError(
            "E_RUNTIME_MODULE_SET: runtime layout and package are inconsistent"
        )
    if layout == "source":
        return root / "control_plane"
    return root / ".codex" / "runtime" / package


def _runtime_inventory(runtime: Path) -> tuple[str, ...]:
    before = _private_directory_metadata(runtime)
    observed: list[str] = []
    try:
        with os.scandir(runtime) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > RUNTIME_DIRECTORY_ENTRY_MAX:
                    raise ValueError(
                        "E_RUNTIME_MODULE_SET: runtime directory exceeds its entry limit"
                    )
                if entry.name in ACTIVE_RUNTIME_MODULES:
                    observed.append(entry.name)
                elif entry.name == "__pycache__":
                    _private_directory_metadata(runtime / entry.name)
                else:
                    raise ValueError(
                        "E_RUNTIME_MODULE_SET: runtime contains an unapproved entry"
                    )
    except OSError as error:
        raise ValueError("E_RUNTIME_MODULE_SET: runtime inventory is unavailable") from error
    after = _private_directory_metadata(runtime)
    if _identity(before) != _identity(after):
        raise ValueError("E_RUNTIME_MODULE_SET: runtime inventory changed during observation")
    return tuple(sorted(observed))


def runtime_digest(
    root: Path,
    package: str | None = None,
    *,
    runtime_layout: str | None = None,
) -> str:
    """Hash only the ordered Core allowlist and reject every inventory drift."""

    layout = runtime_layout or "source"
    selected = package or RUNTIME_PACKAGES.get(layout)
    if selected is None:
        raise ValueError("E_RUNTIME_MODULE_SET: runtime layout is unsupported")
    runtime = _runtime_directory(root, selected, layout)
    expected_sorted = tuple(sorted(ACTIVE_RUNTIME_MODULES))
    if _runtime_inventory(runtime) != expected_sorted:
        raise ValueError("E_RUNTIME_MODULE_SET: exact module inventory drifted")

    hasher = sha256()
    total = 0
    for name in ACTIVE_RUNTIME_MODULES:
        try:
            payload = _bounded_private_bytes(
                runtime / name,
                limit=RUNTIME_MODULE_MAX_BYTES,
                code="E_RUNTIME_MODULE_SET",
            )
        except ValueError as error:
            if str(error).startswith("E_RUNTIME_DATALESS"):
                raise
            raise ValueError("E_RUNTIME_MODULE_SET: runtime module is unsafe") from error
        total += len(payload)
        if total > RUNTIME_TOTAL_MAX_BYTES:
            raise ValueError("E_RUNTIME_MODULE_SET: runtime exceeds its total byte limit")
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payload)
        hasher.update(b"\0")

    if _runtime_inventory(runtime) != expected_sorted:
        raise ValueError("E_RUNTIME_MODULE_SET: runtime inventory changed during hashing")
    return f"sha256:{hasher.hexdigest()}"


def _issue(code: str, path: str, message: str) -> LockIssue:
    return LockIssue(code=code, path=path, message=message)


def validate_lock(root: Path) -> list[LockIssue]:
    lock_path = root / ".codex" / "control-plane.lock"
    try:
        lock_bytes = _bounded_private_bytes(
            lock_path,
            limit=LOCK_MAX_BYTES,
            code="L_PARSE",
        )
        lock = tomllib.loads(lock_bytes.decode("utf-8"))
    except (UnicodeError, ValueError, tomllib.TOMLDecodeError):
        return [
            _issue(
                "L_PARSE",
                str(lock_path),
                "Control-plane lock is unavailable, unsafe, oversized, or invalid.",
            )
        ]

    issues: list[LockIssue] = []
    if lock.get("schema_version") != 2:
        issues.append(_issue("L_SCHEMA", "schema_version", "Only Core lock schema 2 is supported."))
    if lock.get("product_version") != __version__:
        issues.append(
            _issue("L_VERSION", "product_version", f"Lock does not select control-plane v{__version__}.")
        )
    for name in (
        "policy_schema",
        "registry_schema",
        "task_schema",
        "route_schema",
        "receipt_schema",
        "clarification_schema",
        "risk_schema",
    ):
        if lock.get(name) != 1:
            issues.append(_issue("L_SCHEMA", name, f"{name} must select schema 1."))

    declared_modules = lock.get("runtime_modules")
    if not isinstance(declared_modules, list) or tuple(declared_modules) != ACTIVE_RUNTIME_MODULES:
        issues.append(
            _issue(
                "E_RUNTIME_MODULE_SET",
                "runtime_modules",
                "Lock must declare the exact ordered Core runtime inventory.",
            )
        )

    layout = lock.get("runtime_layout")
    package = lock.get("runtime_package")
    if layout not in RUNTIME_PACKAGES or package != RUNTIME_PACKAGES.get(layout):
        issues.append(
            _issue(
                "L_RUNTIME_LAYOUT",
                "runtime_layout",
                "Runtime layout and package form an invalid Core pair.",
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
    digests = lock.get("digests")
    if not isinstance(digests, dict):
        digests = {}
    for name, path in expected.items():
        try:
            observed = _digest(path)
        except ValueError:
            observed = None
        if digests.get(name) != observed:
            issues.append(_issue("L_DIGEST", name, f"Locked digest does not match {path.name}."))

    try:
        observed_runtime = runtime_digest(
            root,
            str(package),
            runtime_layout=str(layout),
        )
    except ValueError:
        observed_runtime = None
        issues.append(
            _issue(
                "E_RUNTIME_MODULE_SET",
                "runtime_modules",
                "Selected runtime module inventory is missing, extra, or invalid.",
            )
        )
    if digests.get("runtime") != observed_runtime:
        issues.append(_issue("L_DIGEST", "runtime", "Locked runtime digest does not match."))
    if lock.get("hook_mode") != "audit":
        issues.append(_issue("L_HOOK_MODE", "hook_mode", "Core hooks must remain in audit mode."))
    if lock.get("hook_trust") != "pending_hook_trust":
        issues.append(
            _issue(
                "L_HOOK_TRUST",
                "hook_trust",
                "Hook trust remains pending until separately authorized adoption.",
            )
        )
    return issues
