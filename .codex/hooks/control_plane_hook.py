#!/usr/bin/python3
"""Direct bounded Core hook; production hook metadata uses the closed launcher."""

from __future__ import annotations

import os
import sys

if (
    not sys.flags.isolated
    or not sys.flags.safe_path
    or not sys.flags.no_site
    or not sys.flags.dont_write_bytecode
    or sys.pycache_prefix != os.devnull
    or sys.version_info < (3, 11)
):
    raise SystemExit(
        "E_RUNTIME_BOOTSTRAP: direct hook requires explicit compatible Python 3.11+ "
        "-I -S -B -X pycache_prefix=/dev/null"
    )

if any(
    name == "control_plane" or name.startswith("control_plane.")
    for name in sys.modules
):
    raise SystemExit("E_RUNTIME_BOOTSTRAP: preloaded Core module is not allowed")

sys.dont_write_bytecode = True
sys.pycache_prefix = os.devnull
os.environ.clear()
os.environ.update({"LC_ALL": "C", "PATH": "/usr/bin:/bin"})

import importlib
import importlib.abc
import importlib.util
from hashlib import sha256
from pathlib import Path
import stat
import tomllib


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
    "task_state.py",
    "toolchain.py",
    "verification.py",
)
PRODUCT_VERSION = "3.1.0-core.2"
LOCK_MAX_BYTES = 64 * 1024
BOOTSTRAP_MAX_BYTES = 1024 * 1024
RUNTIME_MODULE_MAX_BYTES = 1024 * 1024
RUNTIME_TOTAL_MAX_BYTES = 8 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
DIRECTORY_ENTRY_MAX = 256
DATALESS_FLAG = 0x40000000
BOOTSTRAP_PATH = Path(__file__).resolve(strict=True)
ROOT = BOOTSTRAP_PATH.parents[2]


def fail(code: str, message: str) -> None:
    raise SystemExit(f"{code}: {message}")


def dataless(metadata: os.stat_result) -> bool:
    return bool(int(getattr(metadata, "st_flags", 0)) & DATALESS_FLAG)


def identity(metadata: os.stat_result) -> tuple[int, ...]:
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


def private_regular(metadata: os.stat_result, limit: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_uid == os.geteuid()
        and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
        and metadata.st_size <= limit
        and not dataless(metadata)
    )


def private_directory(path: Path, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        fail(code, "runtime directory is unavailable")
    if dataless(metadata):
        fail("E_RUNTIME_DATALESS", "runtime contains an APFS placeholder")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        fail(code, "runtime directory is not private")
    return metadata


def read_private(path: Path, limit: int, code: str) -> bytes:
    try:
        before = path.lstat()
    except OSError:
        fail(code, "runtime path is unavailable")
    if dataless(before):
        fail("E_RUNTIME_DATALESS", "runtime contains an APFS placeholder")
    if not private_regular(before, limit):
        fail(code, "runtime path is not bounded regular private content")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        fail(code, "runtime path cannot be opened safely")
    try:
        opened = os.fstat(descriptor)
        if dataless(opened):
            fail("E_RUNTIME_DATALESS", "runtime contains an APFS placeholder")
        if not private_regular(opened, limit) or identity(opened) != identity(before):
            fail(code, "runtime path identity changed before read")
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, limit + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > limit:
                fail(code, "runtime path exceeds its byte limit")
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except OSError:
        fail(code, "runtime path disappeared during read")
    if (
        not private_regular(after_open, limit)
        or not private_regular(after_path, limit)
        or identity(before) != identity(after_open)
        or identity(before) != identity(after_path)
    ):
        fail(code, "runtime path changed during read")
    return b"".join(chunks)


def inventory(runtime: Path) -> tuple[str, ...]:
    before = private_directory(runtime, "E_RUNTIME_MODULE_SET")
    names: list[str] = []
    try:
        with os.scandir(runtime) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > DIRECTORY_ENTRY_MAX:
                    fail("E_RUNTIME_MODULE_SET", "runtime inventory exceeds its entry limit")
                if entry.name in ACTIVE_RUNTIME_MODULES:
                    names.append(entry.name)
                elif entry.name == "__pycache__":
                    private_directory(runtime / entry.name, "E_RUNTIME_MODULE_SET")
                else:
                    fail("E_RUNTIME_MODULE_SET", "runtime contains an unapproved entry")
    except OSError:
        fail("E_RUNTIME_MODULE_SET", "runtime inventory is unavailable")
    after = private_directory(runtime, "E_RUNTIME_MODULE_SET")
    if identity(before) != identity(after):
        fail("E_RUNTIME_MODULE_SET", "runtime inventory changed during observation")
    return tuple(sorted(names))


lock_path = ROOT / ".codex" / "control-plane.lock"
try:
    lock_bytes = read_private(lock_path, LOCK_MAX_BYTES, "E_RUNTIME_BOOTSTRAP")
    lock = tomllib.loads(lock_bytes.decode("utf-8"))
except (UnicodeError, tomllib.TOMLDecodeError):
    fail("E_RUNTIME_BOOTSTRAP", "lock is invalid")
if lock.get("schema_version") != 2 or lock.get("product_version") != PRODUCT_VERSION:
    fail("E_RUNTIME_BOOTSTRAP", "lock schema or product is unsupported")
if lock.get("runtime_layout") != "source" or lock.get("runtime_package") != "control_plane":
    fail("E_RUNTIME_LAYOUT", "source hook requires the Core source runtime")
declared_modules = lock.get("runtime_modules")
declared_digests = lock.get("digests")
if not isinstance(declared_modules, list) or not isinstance(declared_digests, dict):
    fail("E_RUNTIME_BOOTSTRAP", "lock module or digest contract is malformed")
if tuple(declared_modules) != ACTIVE_RUNTIME_MODULES:
    fail("E_RUNTIME_MODULE_SET", "lock does not declare the exact Core runtime")
bootstrap_bytes = read_private(BOOTSTRAP_PATH, BOOTSTRAP_MAX_BYTES, "E_RUNTIME_BOOTSTRAP")
if declared_digests.get("hook_entrypoint") != f"sha256:{sha256(bootstrap_bytes).hexdigest()}":
    fail("E_RUNTIME_BOOTSTRAP", "hook entrypoint does not match lock")

runtime = ROOT / "control_plane"
expected_modules = tuple(sorted(ACTIVE_RUNTIME_MODULES))
if inventory(runtime) != expected_modules:
    fail("E_RUNTIME_MODULE_SET", "source runtime inventory drifted")
hasher = sha256()
total = 0
verified_sources: dict[str, bytes] = {}
for name in ACTIVE_RUNTIME_MODULES:
    payload = read_private(runtime / name, RUNTIME_MODULE_MAX_BYTES, "E_RUNTIME_MODULE_SET")
    verified_sources[name] = payload
    total += len(payload)
    if total > RUNTIME_TOTAL_MAX_BYTES:
        fail("E_RUNTIME_MODULE_SET", "source runtime exceeds its total byte limit")
    hasher.update(name.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(payload)
    hasher.update(b"\0")
if inventory(runtime) != expected_modules:
    fail("E_RUNTIME_MODULE_SET", "source runtime inventory changed during hashing")
if declared_digests.get("runtime") != f"sha256:{hasher.hexdigest()}":
    fail("E_RUNTIME_DIGEST", "source runtime does not match lock")


class VerifiedCoreLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def _selection(self, fullname: str) -> tuple[str, bool] | None:
        if fullname == "control_plane":
            return "__init__.py", True
        if fullname.startswith("control_plane."):
            suffix = fullname.removeprefix("control_plane.")
            if "." not in suffix:
                name = f"{suffix}.py"
                if name in verified_sources:
                    return name, False
            raise ImportError("E_RUNTIME_MODULE_SET: unapproved Core import")
        return None

    def find_spec(self, fullname, path=None, target=None):
        del path, target
        selected = self._selection(fullname)
        if selected is None:
            return None
        name, is_package = selected
        return importlib.util.spec_from_loader(
            fullname,
            self,
            origin=str(runtime / name),
            is_package=is_package,
        )

    def create_module(self, spec):
        del spec
        return None

    def exec_module(self, module) -> None:
        selected = self._selection(module.__name__)
        if selected is None:
            raise ImportError("E_RUNTIME_MODULE_SET: unapproved Core import")
        name, _ = selected
        filename = str(runtime / name)
        module.__file__ = filename
        code = compile(verified_sources[name], filename, "exec", dont_inherit=True)
        exec(code, module.__dict__)


sys.meta_path.insert(0, VerifiedCoreLoader())
run_hook = importlib.import_module("control_plane.hooks").run_hook


def main() -> int:
    payload = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        print("E_HOOK_INPUT_SIZE: hook input exceeds its byte limit", file=sys.stderr)
        return 1
    try:
        output = run_hook(payload, expected_root=ROOT)
    except (ValueError, RecursionError) as error:
        message = str(error) if isinstance(error, ValueError) else "E_HOOK_INPUT: invalid hook JSON"
        print(message, file=sys.stderr)
        return 1
    if output:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
