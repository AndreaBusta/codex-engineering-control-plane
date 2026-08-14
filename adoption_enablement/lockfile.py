"""Exact lock contract for the isolated adoption-enablement runtime."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
import tomllib

from .safe_io import canonical_root, read_confined_file


ADOPTION_MODULES = (
    "__init__.py",
    "cli.py",
    "contracts.py",
    "lockfile.py",
    "manifest.py",
    "repository.py",
    "safe_io.py",
    "transaction.py",
)
TOOL_VERSION = "0.1.0"
LOCK_MAX = 64 * 1024
MODULE_MAX = 1024 * 1024
RUNTIME_TOTAL_MAX = 8 * 1024 * 1024
DATALESS_FLAG = 0x40000000


def _dataless(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_flags", 0) or 0) & DATALESS_FLAG)


def _private_regular(metadata: os.stat_result, maximum: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
        and 0 <= metadata.st_size <= maximum
        and not _dataless(metadata)
    )


def _inventory(root: Path) -> tuple[str, ...]:
    package = canonical_root(root / "adoption_enablement")
    names: list[str] = []
    try:
        with os.scandir(package) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > 64 or not entry.is_file(follow_symlinks=False):
                    raise ValueError("E_ADOPTION_MODULE_SET: runtime inventory is unsafe")
                names.append(entry.name)
    except OSError as error:
        raise ValueError("E_ADOPTION_MODULE_SET: runtime inventory is unavailable") from error
    observed = tuple(sorted(names))
    if observed != tuple(sorted(ADOPTION_MODULES)):
        raise ValueError("E_ADOPTION_MODULE_SET: runtime module set is not exact")
    return observed


def runtime_digest(root: Path) -> str:
    canonical = canonical_root(root)
    _inventory(canonical)
    hasher = sha256(b"control-plane-adoption-enablement-v1\0")
    total = 0
    for name in ADOPTION_MODULES:
        payload = read_confined_file(
            canonical,
            f"adoption_enablement/{name}",
            maximum=MODULE_MAX,
        )
        total += len(payload)
        if total > RUNTIME_TOTAL_MAX:
            raise ValueError("E_ADOPTION_MODULE_SET: runtime exceeds its total byte limit")
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payload)
        hasher.update(b"\0")
    _inventory(canonical)
    return f"sha256:{hasher.hexdigest()}"


def validate_lock(root: Path) -> tuple[str, ...]:
    try:
        canonical = canonical_root(root)
        payload = read_confined_file(
            canonical,
            ".codex/adoption-enablement.lock",
            maximum=LOCK_MAX,
        )
        lock = tomllib.loads(payload.decode("utf-8", errors="strict"))
        if set(lock) != {
            "schema_version",
            "tool_version",
            "runtime_package",
            "runtime_layout",
            "runtime_modules",
            "digests",
        }:
            raise ValueError("E_ADOPTION_TOOL_LOCK: lock fields are not exact")
        if (
            lock.get("schema_version") != 1
            or lock.get("tool_version") != TOOL_VERSION
            or lock.get("runtime_package") != "adoption_enablement"
            or lock.get("runtime_layout") != "source"
            or lock.get("runtime_modules") != list(ADOPTION_MODULES)
            or not isinstance(lock.get("digests"), dict)
            or set(lock["digests"]) != {"entrypoint", "runtime"}
        ):
            raise ValueError("E_ADOPTION_TOOL_LOCK: lock contract is unsupported")
        entrypoint = read_confined_file(
            canonical,
            "scripts/control-plane-adoption",
            maximum=MODULE_MAX,
        )
        if lock["digests"].get("entrypoint") != f"sha256:{sha256(entrypoint).hexdigest()}":
            raise ValueError("E_ADOPTION_TOOL_LOCK: entrypoint digest drifted")
        if lock["digests"].get("runtime") != runtime_digest(canonical):
            raise ValueError("E_ADOPTION_TOOL_LOCK: runtime digest drifted")
        return ()
    except (OSError, UnicodeDecodeError, ValueError, RecursionError, tomllib.TOMLDecodeError) as error:
        value = str(error).split(":", 1)[0]
        return (value if value.startswith("E_ADOPTION_") else "E_ADOPTION_TOOL_LOCK",)
