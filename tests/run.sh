#!/bin/sh
case ${CONTROL_PLANE_CLEAN_SHELL-} in
  1) ;;
  *)
    exec /usr/bin/env -i CONTROL_PLANE_CLEAN_SHELL=1 LC_ALL=C PATH=/usr/bin:/bin \
      /bin/sh "$0" "$@"
    ;;
esac
set -eu
set -f

unset BASH_ENV ENV CDPATH LD_PRELOAD DYLD_INSERT_LIBRARIES PYTHONHOME PYTHONPATH || :

if [ "$#" -ne 0 ]; then
  echo "E_TEST_USAGE: tests/run.sh accepts no arguments" >&2
  exit 1
fi

SOURCE_SCRIPT=$0
TEST_DIR=$(/usr/bin/dirname "$SOURCE_SCRIPT")
TEST_DIR=$(CDPATH= cd -P "$TEST_DIR" && /bin/pwd -P)
PROJECT_ROOT=$(CDPATH= cd -P "$TEST_DIR/.." && /bin/pwd -P)
cd "$PROJECT_ROOT"

PYTHON_BIN=
for candidate in \
  /usr/local/bin/python3 \
  /opt/homebrew/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
  /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
  /usr/bin/python3
do
  if [ -x "$candidate" ] && \
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
      "$candidate" -I -S -B -X pycache_prefix=/dev/null \
      -c 'import sys, tomllib; raise SystemExit(0 if sys.version_info >= (3, 11) and sys.flags.no_site and not any(name == "control_plane" or name.startswith("control_plane.") for name in sys.modules) else 1)' \
      </dev/null >/dev/null 2>&1
  then
    PYTHON_BIN=$candidate
    break
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  echo "E_TEST_BOOTSTRAP: compatible isolated Python 3.11+ is unavailable" >&2
  exit 1
fi

CORE_TESTS='tests.test_core_adoption_recovery
tests.test_contracts_v2
tests.test_graph
tests.test_materialization
tests.test_resource_registry
tests.test_core_clarification
tests.test_core_cli
tests.test_core_contract
tests.test_core_documentation
tests.test_core_git_guards
tests.test_core_git_state
tests.test_core_governing_manifest
tests.test_core_hooks
tests.test_core_intake
tests.test_core_leases
tests.test_core_lockfile
tests.test_core_maintenance
tests.test_core_plugin
tests.test_core_policy
tests.test_core_project_profiles
tests.test_core_quarantine
tests.test_core_repository
tests.test_core_risk_sentinel
tests.test_core_routing
tests.test_core_state_paths
tests.test_core_task_state
tests.test_core_toolchain
tests.test_core_types
tests.test_core_verification'

CORE_MODULES='control_plane/__init__.py
control_plane/adoption_recovery.py
control_plane/clarification.py
control_plane/cli.py
control_plane/contracts.py
control_plane/core_types.py
control_plane/git_guards.py
control_plane/git_state.py
control_plane/graph.py
control_plane/hooks.py
control_plane/intake.py
control_plane/leases.py
control_plane/lockfile.py
control_plane/maintenance.py
control_plane/materialization.py
control_plane/policy.py
control_plane/project_profiles.py
control_plane/repository.py
control_plane/resource_registry.py
control_plane/risk_sentinel.py
control_plane/routing.py
control_plane/scopes.py
control_plane/task_state.py
control_plane/toolchain.py
control_plane/verification.py'

CORE_TEST_FILES='tests/test_core_adoption_recovery.py
tests/test_contracts_v2.py
tests/test_graph.py
tests/test_materialization.py
tests/test_resource_registry.py
tests/test_core_clarification.py
tests/test_core_cli.py
tests/test_core_contract.py
tests/test_core_documentation.py
tests/test_core_git_guards.py
tests/test_core_git_state.py
tests/test_core_governing_manifest.py
tests/test_core_hooks.py
tests/test_core_intake.py
tests/test_core_leases.py
tests/test_core_lockfile.py
tests/test_core_maintenance.py
tests/test_core_plugin.py
tests/test_core_policy.py
tests/test_core_project_profiles.py
tests/test_core_quarantine.py
tests/test_core_repository.py
tests/test_core_risk_sentinel.py
tests/test_core_routing.py
tests/test_core_state_paths.py
tests/test_core_task_state.py
tests/test_core_toolchain.py
tests/test_core_types.py
tests/test_core_verification.py'

CORE_TEST_HELPERS='tests/core_gate.py
tests/core_router_test_support.py
tests/git_test_support.py'

CORE_TEST_PACKAGE='tests/__init__.py'

CORE_GATE_FILES='scripts/control-plane
scripts/build-release-candidate
tests/run.sh'

ADOPTION_MODULES='adoption_enablement/__init__.py
adoption_enablement/cli.py
adoption_enablement/contracts.py
adoption_enablement/lockfile.py
adoption_enablement/manifest.py
adoption_enablement/repository.py
adoption_enablement/safe_io.py
adoption_enablement/transaction.py'

ADOPTION_TESTS='tests.test_adoption_enablement_contracts
tests.test_adoption_enablement_repository
tests.test_adoption_enablement_preview
tests.test_adoption_enablement_transaction
tests.test_adoption_enablement_recovery
tests.test_adoption_enablement_bootstrap
tests.test_adoption_enablement_e2e'

ADOPTION_TEST_FILES='tests/test_adoption_enablement_contracts.py
tests/test_adoption_enablement_repository.py
tests/test_adoption_enablement_preview.py
tests/test_adoption_enablement_transaction.py
tests/test_adoption_enablement_recovery.py
tests/test_adoption_enablement_bootstrap.py
tests/test_adoption_enablement_e2e.py'

ADOPTION_TEST_HELPERS='tests/adoption_enablement_test_support.py'

ADOPTION_GATE_FILES='scripts/control-plane-adoption
.codex/adoption-enablement.lock'

# FULL_GATE_COMMANDS_BEGIN
/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
  "$PYTHON_BIN" -I -S -B -X pycache_prefix=/dev/null - \
  "$PROJECT_ROOT" \
  "$SOURCE_SCRIPT" \
  "$CORE_MODULES" \
  "$CORE_TESTS" \
  "$CORE_TEST_FILES" \
  "$CORE_TEST_HELPERS" \
  "$CORE_TEST_PACKAGE" \
  "$CORE_GATE_FILES" \
  "$ADOPTION_MODULES" \
  "$ADOPTION_TESTS" \
  "$ADOPTION_TEST_FILES" \
  "$ADOPTION_TEST_HELPERS" \
  "$ADOPTION_GATE_FILES" <<'PY'
from __future__ import annotations

import ast
import atexit
import fcntl
import hashlib
import importlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import tomllib
from types import ModuleType
from typing import Iterable


LIMIT = 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
DATALESS_FLAG = 0x40000000
gate_executed = False


def error_code(error: BaseException) -> str:
    candidate = str(error).partition(":")[0]
    if (
        candidate.startswith("E_")
        and len(candidate) <= 64
        and all(character.isupper() or character.isdigit() or character == "_" for character in candidate)
    ):
        return candidate
    return "E_TEST_GATE"


def emit_gate_error(error: BaseException) -> None:
    print(
        json.dumps(
            {
                "authorizes": False,
                "error_code": error_code(error),
                "executed": gate_executed,
                "status": "FAIL",
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )


def stable_excepthook(kind, error, traceback) -> None:
    del kind, traceback
    emit_gate_error(error)


sys.excepthook = stable_excepthook


def fail(code: str, message: str) -> None:
    raise RuntimeError(f"{code}: {message}")


if (
    sys.version_info < (3, 11)
    or not sys.flags.isolated
    or not sys.flags.safe_path
    or not sys.flags.no_site
    or not sys.flags.dont_write_bytecode
    or sys.pycache_prefix != os.devnull
    or any(
        name == "control_plane"
        or name.startswith("control_plane.")
        or name == "adoption_enablement"
        or name.startswith("adoption_enablement.")
        or name == "tests"
        or name.startswith("tests.")
        for name in sys.modules
    )
):
    fail("E_TEST_BOOTSTRAP", "Python isolation contract failed")
if len(sys.argv) != 14:
    fail("E_TEST_BOOTSTRAP", "fixed gate manifest arguments are missing")


def words(value: str, *, label: str) -> tuple[str, ...]:
    result = tuple(value.splitlines())
    if not result or any(not item or item.strip() != item for item in result):
        fail("E_TEST_MANIFEST", f"{label} is empty or malformed")
    if len(result) != len(set(result)):
        fail("E_TEST_MANIFEST", f"{label} contains duplicates")
    return result


repository = Path(sys.argv[1]).resolve(strict=True)
source_script = Path(sys.argv[2]).resolve(strict=True)
core_modules = words(sys.argv[3], label="CORE_MODULES")
core_test_names = words(sys.argv[4], label="CORE_TESTS")
core_test_files = words(sys.argv[5], label="CORE_TEST_FILES")
core_test_helpers = words(sys.argv[6], label="CORE_TEST_HELPERS")
test_package = words(sys.argv[7], label="CORE_TEST_PACKAGE")
core_gate_paths = words(sys.argv[8], label="CORE_GATE_FILES")
adoption_modules = words(sys.argv[9], label="ADOPTION_MODULES")
adoption_test_names = words(sys.argv[10], label="ADOPTION_TESTS")
adoption_test_files = words(sys.argv[11], label="ADOPTION_TEST_FILES")
adoption_test_helpers = words(sys.argv[12], label="ADOPTION_TEST_HELPERS")
adoption_gate_paths = words(sys.argv[13], label="ADOPTION_GATE_FILES")
test_names = (*core_test_names, *adoption_test_names)
test_files = (*core_test_files, *adoption_test_files)
test_helpers = (*core_test_helpers, *adoption_test_helpers)
if source_script != repository / "tests" / "run.sh":
    fail("E_TEST_MANIFEST", "runner path is not canonical")
if set(core_test_files) != {
    module.replace(".", "/") + ".py" for module in core_test_names
}:
    fail("E_TEST_MANIFEST", "Core test module and source manifests differ")
if set(adoption_test_files) != {
    module.replace(".", "/") + ".py" for module in adoption_test_names
}:
    fail("E_TEST_MANIFEST", "adoption test module and source manifests differ")
if test_package != ("tests/__init__.py",):
    fail("E_TEST_MANIFEST", "test package manifest is not exact")
if set(core_gate_paths) != {
    "scripts/control-plane",
    "scripts/build-release-candidate",
    "tests/run.sh",
}:
    fail("E_TEST_MANIFEST", "shell gate manifest is not exact")
if set(adoption_modules) != {
    "adoption_enablement/__init__.py",
    "adoption_enablement/cli.py",
    "adoption_enablement/contracts.py",
    "adoption_enablement/lockfile.py",
    "adoption_enablement/manifest.py",
    "adoption_enablement/repository.py",
    "adoption_enablement/safe_io.py",
    "adoption_enablement/transaction.py",
}:
    fail("E_TEST_MANIFEST", "adoption module manifest is not exact")
if set(adoption_gate_paths) != {
    "scripts/control-plane-adoption",
    ".codex/adoption-enablement.lock",
}:
    fail("E_TEST_MANIFEST", "adoption gate manifest is not exact")


def executable_metadata(path: Path, *, name: str) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        fail("E_TEST_BOOTSTRAP", f"{name} executable is unsafe")


executable_metadata(Path("/usr/bin/git"), name="Git")


def identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        int(getattr(value, "st_flags", 0)),
    )


VERIFICATION_DIRECTORY_KEYS = {
    "path", "device", "inode", "mode", "uid", "gid", "flags"
}
VERIFICATION_FILE_KEYS = {
    "path", "device", "inode", "mode", "links", "uid", "gid", "size",
    "mtime_ns", "ctime_ns", "flags",
}
ADOPTION_JOURNAL_KEYS = {
    "schema_version", "kind", "plan_digest", "install_digest", "state",
    "source_manifest_digest", "target_binding", "before_snapshot_digest",
    "managed_parent_directories", "managed_repository_scan", "lifecycle_lock",
    "verification_lock", "created_directories", "published_records",
    "target_lock_record", "prior_git_config", "rollback_records",
    "state_digest", "authorizes",
}


def verification_directory_record(value: os.stat_result) -> dict[str, object]:
    return {
        "path": "codex-control-plane-core/locks",
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "uid": int(value.st_uid),
        "gid": int(value.st_gid),
        "flags": int(getattr(value, "st_flags", 0)),
    }


def verification_file_record(value: os.stat_result) -> dict[str, object]:
    return {
        "path": "codex-control-plane-core/locks/verification.lock",
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "links": int(value.st_nlink),
        "uid": int(value.st_uid),
        "gid": int(value.st_gid),
        "size": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "ctime_ns": int(value.st_ctime_ns),
        "flags": int(getattr(value, "st_flags", 0)),
    }


def lifecycle_file_record(value: os.stat_result) -> dict[str, object]:
    record = verification_file_record(value)
    record["path"] = "codex-control-plane-core/adoption.lock"
    return record


def bounded_binding_bytes(
    anchor: Path,
    parents: tuple[tuple[str, bool], ...],
    filename: str,
    *,
    maximum: int,
    exact_mode: int | None,
) -> bytes | None:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        descriptor = os.open(anchor, directory_flags)
        descriptors.append(descriptor)
        validate_directory(os.fstat(descriptor), exact_private=False)
        for name, exact_private in parents:
            try:
                child = os.open(name, directory_flags, dir_fd=descriptor)
            except FileNotFoundError:
                return None
            validate_directory(os.fstat(child), exact_private=exact_private)
            descriptors.append(child)
            descriptor = child
        try:
            before = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
            file_descriptor = os.open(
                filename,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=descriptor,
            )
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or (
                stat.S_IMODE(before.st_mode) != exact_mode
                if exact_mode is not None
                else bool(stat.S_IMODE(before.st_mode) & 0o022)
            )
            or not 0 <= before.st_size <= maximum
            or int(getattr(before, "st_flags", 0)) & DATALESS_FLAG
        ):
            fail("E_TEST_MUTEX", "adoption binding is unsafe")
        opened = os.fstat(file_descriptor)
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(
                file_descriptor,
                min(65_536, maximum + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(file_descriptor)
        named = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
        if (
            len(payload) > maximum
            or len(payload) != opened.st_size
            or identity(before) != identity(opened)
            or identity(opened) != identity(after)
            or identity(opened) != identity(named)
        ):
            fail("E_TEST_MUTEX", "adoption binding changed during read")
        return bytes(payload)
    except OSError:
        fail("E_TEST_MUTEX", "adoption binding is unavailable")
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for opened_directory in reversed(descriptors):
            os.close(opened_directory)


def _closed_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            fail("E_TEST_MUTEX", "adoption journal has duplicate fields")
        value[key] = item
    return value


def validated_verification_binding(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"directory", "file"}:
        fail("E_TEST_MUTEX", "verification binding is invalid")
    directory = value.get("directory")
    lock = value.get("file")
    if (
        not isinstance(directory, dict)
        or set(directory) != VERIFICATION_DIRECTORY_KEYS
        or directory.get("path") != "codex-control-plane-core/locks"
        or any(type(directory.get(key)) is not int or directory[key] < 0 for key in VERIFICATION_DIRECTORY_KEYS - {"path"})
        or directory.get("mode") != 0o700
        or int(directory.get("flags", 0)) & DATALESS_FLAG
    ):
        fail("E_TEST_MUTEX", "verification directory binding is invalid")
    if (
        not isinstance(lock, dict)
        or set(lock) != VERIFICATION_FILE_KEYS
        or lock.get("path") != "codex-control-plane-core/locks/verification.lock"
        or any(type(lock.get(key)) is not int or lock[key] < 0 for key in VERIFICATION_FILE_KEYS - {"path"})
        or lock.get("mode") != 0o600
        or lock.get("links") != 1
        or lock.get("size") != 0
        or int(lock.get("flags", 0)) & DATALESS_FLAG
    ):
        fail("E_TEST_MUTEX", "verification file binding is invalid")
    return {"directory": dict(directory), "file": dict(lock)}


def validate_lifecycle_binding(value: object) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != VERIFICATION_FILE_KEYS
        or value.get("path") != "codex-control-plane-core/adoption.lock"
        or any(
            type(value.get(key)) is not int or value[key] < 0
            for key in VERIFICATION_FILE_KEYS - {"path"}
        )
        or value.get("mode") != 0o600
        or value.get("links") != 1
        or value.get("size") != 0
        or int(value.get("flags", 0)) & DATALESS_FLAG
    ):
        fail("E_TEST_MUTEX", "lifecycle lock binding is invalid")


def adoption_verification_context(root: Path, common: Path) -> dict[str, object] | None:
    marker_payload = bounded_binding_bytes(
        root,
        ((".codex", False),),
        "control-plane.lock",
        maximum=65_536,
        exact_mode=None,
    )
    try:
        marker_document = (
            {} if marker_payload is None else tomllib.loads(marker_payload.decode("utf-8"))
        )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        fail("E_TEST_MUTEX", "activation lifecycle binding is invalid")
    marker = marker_document.get("adoption_lifecycle")
    if marker not in {None, "journal-bound-v1"}:
        fail("E_TEST_MUTEX", "activation lifecycle binding is unsupported")

    journal_payload = bounded_binding_bytes(
        common,
        (("codex-control-plane-core", True), ("adoption", True)),
        "journal.json",
        maximum=LIMIT,
        exact_mode=0o600,
    )
    journal: dict[str, object] | None = None
    if journal_payload is not None:
        try:
            candidate = json.loads(
                journal_payload.decode("utf-8"),
                object_pairs_hook=_closed_pairs,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            fail("E_TEST_MUTEX", "adoption journal is invalid")
        if not isinstance(candidate, dict):
            fail("E_TEST_MUTEX", "adoption journal is invalid")
        unsigned = {key: item for key, item in candidate.items() if key != "state_digest"}
        try:
            canonical = json.dumps(
                unsigned,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            fail("E_TEST_MUTEX", "adoption journal is invalid")
        supplied = candidate.get("state_digest")
        observed = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
        if (
            set(candidate) != ADOPTION_JOURNAL_KEYS
            or candidate.get("schema_version") != 1
            or candidate.get("kind") != "CoreAdoptionJournalV1"
            or candidate.get("authorizes") is not False
            or candidate.get("state") != "active"
            or not isinstance(supplied, str)
            or supplied != observed
        ):
            fail("E_TEST_MUTEX", "adoption journal binding is invalid")
        journal = candidate

    if marker is None and journal is None:
        return None
    if (
        marker != "journal-bound-v1"
        or journal is None
        or journal.get("state") != "active"
    ):
        fail("E_TEST_MUTEX", "adoption lifecycle is not active")
    validate_lifecycle_binding(journal.get("lifecycle_lock"))
    return {
        "marker": marker,
        "state_digest": journal["state_digest"],
        "lifecycle_lock": dict(journal["lifecycle_lock"]),
        "verification_lock": validated_verification_binding(
            journal.get("verification_lock")
        ),
    }


def bounded_git_control_file(path: Path, *, label: str) -> str:
    try:
        before = path.lstat()
    except OSError as error:
        fail("E_TEST_MUTEX", f"{label} is not observable")
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size > 4_096
    ):
        fail("E_TEST_MUTEX", f"{label} is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0),
    )
    try:
        opened = os.fstat(descriptor)
        payload = os.read(descriptor, 4_097)
        trailing = os.read(descriptor, 1)
        after = os.fstat(descriptor)
    except OSError as error:
        fail("E_TEST_MUTEX", f"{label} cannot be read")
    finally:
        os.close(descriptor)
    try:
        observed = path.lstat()
    except OSError as error:
        fail("E_TEST_MUTEX", f"{label} changed after read")
    if (
        identity(before) != identity(opened)
        or identity(before) != identity(after)
        or identity(before) != identity(observed)
        or trailing
        or len(payload) != before.st_size
        or b"\0" in payload
    ):
        fail("E_TEST_MUTEX", f"{label} changed during read")
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError:
        fail("E_TEST_MUTEX", f"{label} is not UTF-8")
    if value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value:
        fail("E_TEST_MUTEX", f"{label} is malformed")
    return value


def canonical_git_directory(path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError:
        fail("E_TEST_MUTEX", f"{label} is not observable")
    if (
        resolved != path
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink < 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        fail("E_TEST_MUTEX", f"{label} is unsafe")
    return path


def common_git_directory(root: Path) -> Path:
    entry = root / ".git"
    try:
        metadata = entry.lstat()
    except OSError:
        fail("E_TEST_MUTEX", "Git directory is not observable")
    if stat.S_ISDIR(metadata.st_mode):
        git_directory = canonical_git_directory(entry, label="Git directory")
    elif stat.S_ISREG(metadata.st_mode):
        pointer = bounded_git_control_file(entry, label="Git directory pointer")
        if not pointer.startswith("gitdir: "):
            fail("E_TEST_MUTEX", "Git directory pointer is malformed")
        candidate = Path(pointer.removeprefix("gitdir: "))
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate = candidate.resolve(strict=True)
        except OSError:
            fail("E_TEST_MUTEX", "Git directory pointer is unresolved")
        git_directory = canonical_git_directory(candidate, label="Git directory")
    else:
        fail("E_TEST_MUTEX", "Git directory entry is unsafe")

    commondir = git_directory / "commondir"
    try:
        commondir.lstat()
    except FileNotFoundError:
        return git_directory
    except OSError:
        fail("E_TEST_MUTEX", "Git common directory pointer is not observable")
    value = bounded_git_control_file(
        commondir, label="Git common directory pointer"
    )
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = git_directory / candidate
    try:
        candidate = candidate.resolve(strict=True)
    except OSError:
        fail("E_TEST_MUTEX", "Git common directory pointer is unresolved")
    return canonical_git_directory(candidate, label="Git common directory")


def validate_directory(value: os.stat_result, *, exact_private: bool) -> None:
    mode = stat.S_IMODE(value.st_mode)
    if (
        not stat.S_ISDIR(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_nlink < 1
        or (mode != 0o700 if exact_private else bool(mode & 0o022))
    ):
        fail("E_TEST_MUTEX", "verification state directory is unsafe")


def validate_lifecycle_file(value: os.stat_result) -> None:
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != os.geteuid()
        or value.st_nlink != 1
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_size != 0
        or int(getattr(value, "st_flags", 0)) & DATALESS_FLAG
    ):
        fail("E_TEST_MUTEX", "lifecycle mutex is unsafe")


def validate_lifecycle_after_flock(
    root: Path,
    common: Path,
    descriptors: tuple[int, int, int],
    context: dict[str, object] | None,
) -> None:
    common_descriptor, state_descriptor, descriptor = descriptors
    try:
        opened_common = os.fstat(common_descriptor)
        named_common = common.lstat()
        opened_state = os.fstat(state_descriptor)
        named_state = os.stat(
            "codex-control-plane-core",
            dir_fd=common_descriptor,
            follow_symlinks=False,
        )
        opened = os.fstat(descriptor)
        named = os.stat(
            "adoption.lock",
            dir_fd=state_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        fail("E_TEST_MUTEX", "lifecycle mutex identity changed")
    validate_directory(opened_common, exact_private=False)
    validate_directory(named_common, exact_private=False)
    validate_directory(opened_state, exact_private=True)
    validate_directory(named_state, exact_private=True)
    validate_lifecycle_file(opened)
    validate_lifecycle_file(named)
    if (
        identity(opened_common) != identity(named_common)
        or identity(opened_state) != identity(named_state)
        or identity(opened) != identity(named)
    ):
        fail("E_TEST_MUTEX", "lifecycle mutex identity changed")
    for reserved in (".provisioning-adoption", ".provisioning-locks"):
        try:
            os.stat(
                reserved,
                dir_fd=state_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        except OSError:
            fail("E_TEST_MUTEX", "provisioning state is unavailable")
        fail("E_TEST_MUTEX", "adoption lifecycle is provisioning")
    try:
        adoption = os.stat(
            "adoption",
            dir_fd=state_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        adoption_present = False
    except OSError:
        fail("E_TEST_MUTEX", "adoption state is unavailable")
    else:
        validate_directory(adoption, exact_private=True)
        adoption_present = True
    if (context is None and adoption_present) or (
        context is not None and not adoption_present
    ):
        fail("E_TEST_MUTEX", "adoption lifecycle is not active")
    if context is not None and context.get("lifecycle_lock") != lifecycle_file_record(opened):
        fail("E_TEST_MUTEX", "lifecycle mutex binding changed")
    if adoption_verification_context(root, common) != context:
        fail("E_TEST_MUTEX", "adoption lifecycle binding changed")


def open_lifecycle_mutex(
    root: Path,
    common: Path,
) -> tuple[tuple[int, int, int], dict[str, object] | None]:
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    held = False
    try:
        common_descriptor = os.open(common, directory_flags)
        descriptors.append(common_descriptor)
        validate_directory(os.fstat(common_descriptor), exact_private=False)
        try:
            state_descriptor = os.open(
                "codex-control-plane-core",
                directory_flags,
                dir_fd=common_descriptor,
            )
        except FileNotFoundError:
            try:
                os.mkdir("codex-control-plane-core", 0o700, dir_fd=common_descriptor)
                os.fsync(common_descriptor)
            except FileExistsError:
                pass
            state_descriptor = os.open(
                "codex-control-plane-core",
                directory_flags,
                dir_fd=common_descriptor,
            )
        descriptors.append(state_descriptor)
        validate_directory(os.fstat(state_descriptor), exact_private=True)
        lock_flags = (
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            before = os.stat(
                "adoption.lock",
                dir_fd=state_descriptor,
                follow_symlinks=False,
            )
            descriptor = os.open(
                "adoption.lock",
                lock_flags,
                dir_fd=state_descriptor,
            )
            if identity(before) != identity(os.fstat(descriptor)):
                fail("E_TEST_MUTEX", "lifecycle mutex identity changed")
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    "adoption.lock",
                    lock_flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=state_descriptor,
                )
                os.fsync(state_descriptor)
            except FileExistsError:
                fail("E_TEST_MUTEX", "lifecycle mutex raced during creation")
        descriptors.append(descriptor)
        validate_lifecycle_file(os.fstat(descriptor))
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        held = True
        context = adoption_verification_context(root, common)
        result = (descriptors[0], descriptors[1], descriptors[2])
        validate_lifecycle_after_flock(root, common, result, context)
        return result, context
    except Exception:
        if held and descriptors:
            try:
                fcntl.flock(descriptors[-1], fcntl.LOCK_UN)
            except OSError:
                pass
        for opened in reversed(descriptors):
            try:
                os.close(opened)
            except OSError:
                pass
        raise


def open_mutex(
    common: Path,
    context: dict[str, object] | None,
) -> tuple[int, int, int, int]:
    create = context is None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    before = common.lstat()
    validate_directory(before, exact_private=False)
    descriptors: list[int] = []
    try:
        common_descriptor = os.open(common, flags)
        descriptors.append(common_descriptor)
        if identity(os.fstat(common_descriptor)) != identity(before):
            fail("E_TEST_MUTEX", "Git common directory changed before open")
        descriptor = common_descriptor
        for name in ("codex-control-plane-core", "locks"):
            try:
                child = os.open(name, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    fail("E_TEST_MUTEX", "bound verification directory is absent")
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                    os.fsync(descriptor)
                except FileExistsError:
                    pass
                child = os.open(name, flags, dir_fd=descriptor)
            try:
                validate_directory(os.fstat(child), exact_private=True)
            except Exception:
                os.close(child)
                raise
            descriptors.append(child)
            descriptor = child
        lock_flags = (
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if create:
            lock_flags |= os.O_CREAT
        try:
            lock = os.open(
                "verification.lock",
                lock_flags,
                0o600,
                dir_fd=descriptor,
            )
        except OSError:
            fail("E_TEST_MUTEX", "bound verification lock is unavailable")
        metadata = os.fstat(lock)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            os.close(lock)
            fail("E_TEST_MUTEX", "verification lock is unsafe")
        descriptors.append(lock)
        return (
            descriptors[0],
            descriptors[1],
            descriptors[2],
            descriptors[3],
        )
    except Exception:
        for opened in reversed(descriptors):
            os.close(opened)
        raise


def validate_mutex_after_flock(
    root: Path,
    common: Path,
    descriptors: tuple[int, int, int, int],
    context: dict[str, object] | None,
) -> None:
    common_descriptor, state_descriptor, locks_descriptor, descriptor = descriptors
    try:
        opened_state = os.fstat(state_descriptor)
        named_state = os.stat(
            "codex-control-plane-core",
            dir_fd=common_descriptor,
            follow_symlinks=False,
        )
        directory = os.fstat(locks_descriptor)
        named_directory = os.stat(
            "locks",
            dir_fd=state_descriptor,
            follow_symlinks=False,
        )
        opened = os.fstat(descriptor)
        named = os.stat(
            "verification.lock",
            dir_fd=locks_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        fail("E_TEST_MUTEX", "verification mutex identity changed")
    validate_directory(opened_state, exact_private=True)
    validate_directory(named_state, exact_private=True)
    validate_directory(directory, exact_private=True)
    validate_directory(named_directory, exact_private=True)
    if (
        verification_directory_record(opened_state)
        != verification_directory_record(named_state)
        or verification_directory_record(directory)
        != verification_directory_record(named_directory)
        or not stat.S_ISREG(named.st_mode)
        or named.st_uid != os.geteuid()
        or named.st_nlink != 1
        or stat.S_IMODE(named.st_mode) != 0o600
        or named.st_size != 0
        or (
            context is not None
            and int(getattr(named, "st_flags", 0)) & DATALESS_FLAG
        )
        or verification_file_record(named) != verification_file_record(opened)
    ):
        fail("E_TEST_MUTEX", "verification mutex identity changed")
    current = {
        "directory": verification_directory_record(directory),
        "file": verification_file_record(opened),
    }
    if context is not None and current != context["verification_lock"]:
        fail("E_TEST_MUTEX", "verification mutex binding changed")
    if adoption_verification_context(root, common) != context:
        fail("E_TEST_MUTEX", "adoption verification binding changed")


common_directory = common_git_directory(repository)
lifecycle_descriptors, verification_context = open_lifecycle_mutex(
    repository,
    common_directory,
)
mutex_descriptors: tuple[int, int, int, int] | None = open_mutex(
    common_directory,
    verification_context,
)
lock_descriptor: int | None = mutex_descriptors[-1]
lock_held = False
lifecycle_held = True


def release_lock() -> None:
    global lock_descriptor, lock_held, mutex_descriptors, lifecycle_descriptors, lifecycle_held
    if mutex_descriptors is not None:
        if lock_held:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        for descriptor in reversed(mutex_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        mutex_descriptors = None
        lock_descriptor = None
        lock_held = False
    if lifecycle_descriptors is not None:
        if lifecycle_held:
            try:
                fcntl.flock(lifecycle_descriptors[-1], fcntl.LOCK_UN)
            except OSError:
                pass
        for descriptor in reversed(lifecycle_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        lifecycle_descriptors = None
        lifecycle_held = False


try:
    fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print(
        json.dumps(
            {
                "authorizes": False,
                "consumes_reframe": False,
                "error_code": "E_VERIFICATION_BUSY",
                "executed": False,
                "status": "UNKNOWN",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    release_lock()
    raise SystemExit(2)
except Exception:
    release_lock()
    raise
lock_held = True
validate_lifecycle_after_flock(
    repository,
    common_directory,
    lifecycle_descriptors,
    verification_context,
)
validate_mutex_after_flock(
    repository,
    common_directory,
    mutex_descriptors,
    verification_context,
)
atexit.register(release_lock)


def safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        fail("E_TEST_MANIFEST", "manifest path is unsafe")
    path = repository.joinpath(*pure.parts)
    if path.resolve(strict=True) != path:
        fail("E_TEST_SOURCE", f"source path is redirected: {value}")
    current = path.parent
    while True:
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            fail("E_TEST_SOURCE", f"source parent is unsafe: {value}")
        if current == repository:
            break
        if repository not in current.parents:
            fail("E_TEST_SOURCE", f"source escaped repository: {value}")
        current = current.parent
    return path


def safe_read(value: str) -> bytes:
    path = safe_relative(value)
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o022
        or before.st_size > LIMIT
        or int(getattr(before, "st_flags", 0)) & DATALESS_FLAG
    ):
        fail("E_TEST_SOURCE", f"source is not private bounded content: {value}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if identity(opened) != identity(before):
            fail("E_TEST_SOURCE", f"source changed before read: {value}")
        payload = bytearray()
        while len(payload) <= LIMIT:
            chunk = os.read(descriptor, min(65_536, LIMIT + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    observed = path.lstat()
    if (
        len(payload) > LIMIT
        or len(payload) != before.st_size
        or identity(before) != identity(after)
        or identity(before) != identity(observed)
    ):
        fail("E_TEST_SOURCE", f"source changed during read: {value}")
    return bytes(payload)


def exact_directory_inventory(
    relative: str,
    *,
    selected_prefixes: tuple[str, ...],
    expected: set[str],
) -> None:
    directory = repository / relative
    before = directory.lstat()
    if (
        directory.resolve(strict=True) != directory
        or not stat.S_ISDIR(before.st_mode)
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o022
    ):
        fail("E_TEST_SOURCE", f"inventory directory is unsafe: {relative}")
    observed: set[str] = set()
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > 4_096:
                    fail("E_TEST_MANIFEST", f"inventory exceeds its bound: {relative}")
                if not any(entry.name.startswith(prefix) for prefix in selected_prefixes):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    fail("E_TEST_SOURCE", f"manifest entry is not a regular file: {relative}")
                observed.add(f"{relative}/{entry.name}")
    except OSError:
        fail("E_TEST_SOURCE", f"inventory is unavailable: {relative}")
    after = directory.lstat()
    if identity(before) != identity(after) or observed != expected:
        fail("E_TEST_MANIFEST", f"declared inventory is not exact: {relative}")


exact_directory_inventory(
    "adoption_enablement",
    selected_prefixes=("",),
    expected=set(adoption_modules),
)
exact_directory_inventory(
    "tests",
    selected_prefixes=("test_adoption_enablement_", "adoption_enablement_"),
    expected={*adoption_test_files, *adoption_test_helpers},
)


python_paths = (*core_modules, *adoption_modules, *test_files, *test_helpers, *test_package)
all_paths = (*python_paths, *core_gate_paths, *adoption_gate_paths)
if len(all_paths) != len(set(all_paths)):
    fail("E_TEST_MANIFEST", "gate source manifest contains duplicates")
captured = {path: safe_read(path) for path in all_paths}
if sum(len(payload) for payload in captured.values()) > TOTAL_LIMIT:
    fail("E_TEST_SOURCE", "gate source manifest exceeds its total byte limit")

module_paths: dict[str, str] = {}
for path in python_paths:
    if path == "control_plane/__init__.py":
        module = "control_plane"
    elif path == "adoption_enablement/__init__.py":
        module = "adoption_enablement"
    elif path == "tests/__init__.py":
        module = "tests"
    else:
        module = path.removesuffix(".py").replace("/", ".")
    if module in module_paths:
        fail("E_TEST_MANIFEST", "multiple sources declare one module")
    module_paths[module] = path
    shadow = safe_relative(path).with_suffix("")
    if shadow.is_dir():
        fail("E_TEST_SOURCE", f"module has a directory shadow: {path}")
if set(module_paths) != {
    "control_plane",
    "adoption_enablement",
    "tests",
    *test_names,
    *(path.removesuffix(".py").replace("/", ".") for path in test_helpers),
    *(
        "control_plane." + Path(path).stem
        for path in core_modules
        if path != "control_plane/__init__.py"
    ),
    *(
        "adoption_enablement." + Path(path).stem
        for path in adoption_modules
        if path != "adoption_enablement/__init__.py"
    ),
}:
    fail("E_TEST_MANIFEST", "module origin manifest is not exact")


def local_import(current: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = current if current in {"control_plane", "adoption_enablement", "tests"} else current.rpartition(".")[0]
    parts = package.split(".") if package else []
    if node.level > len(parts):
        fail("E_TEST_IMPORT", f"relative import escapes package in {current}")
    base = parts[: len(parts) - node.level + 1]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def static_exports(module: str) -> set[str]:
    relative = module_paths[module]
    tree = ast.parse(captured[relative], filename=str(repository / relative))
    exports: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            exports.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets: Iterable[ast.AST] = node.targets if isinstance(node, ast.Assign) else (node.target,)
            exports.update(target.id for target in targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.Import):
            exports.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            exports.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
    return exports


package_exports = {
    package: static_exports(package)
    for package in ("control_plane", "adoption_enablement", "tests")
}


def scan_source(module: str, payload: bytes, filename: str) -> None:
    tree = ast.parse(payload, filename=filename)
    module_aliases: dict[str, str] = {}
    callables: dict[str, str] = {"__import__": "import", "eval": "eval", "exec": "exec"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                if target.split(".", 1)[0] in {"control_plane", "adoption_enablement", "tests"} and target not in module_paths:
                    fail("E_TEST_IMPORT", f"undeclared local import {target} in {module}")
                if (
                    (module == "control_plane" or module.startswith("control_plane."))
                    and (target == "adoption_enablement" or target.startswith("adoption_enablement."))
                ) or (
                    (module == "adoption_enablement" or module.startswith("adoption_enablement."))
                    and (target == "control_plane" or target.startswith("control_plane."))
                ):
                    fail("E_TEST_IMPORT", f"Core/adoption boundary import in {module}")
                if target in {"builtins", "importlib", "sys"}:
                    module_aliases[alias.asname or target] = target
        elif isinstance(node, ast.ImportFrom):
            target = local_import(module, node)
            if target.split(".", 1)[0] in {"control_plane", "adoption_enablement", "tests"} and target not in module_paths:
                fail("E_TEST_IMPORT", f"undeclared local import {target} in {module}")
            if (
                (module == "control_plane" or module.startswith("control_plane."))
                and (target == "adoption_enablement" or target.startswith("adoption_enablement."))
            ) or (
                (module == "adoption_enablement" or module.startswith("adoption_enablement."))
                and (target == "control_plane" or target.startswith("control_plane."))
            ):
                fail("E_TEST_IMPORT", f"Core/adoption boundary import in {module}")
            for alias in node.names:
                if target in package_exports:
                    candidate = f"{target}.{alias.name}"
                    if (
                        alias.name == "*"
                        or (
                            candidate not in module_paths
                            and alias.name not in package_exports[target]
                        )
                    ):
                        fail("E_TEST_IMPORT", f"undeclared root from-import {candidate} in {module}")
                if target == "importlib" and alias.name == "import_module":
                    callables[alias.asname or alias.name] = "import"
                if target == "builtins" and alias.name in {"__import__", "eval", "exec"}:
                    callables[alias.asname or alias.name] = "import" if alias.name == "__import__" else alias.name

    def callable_kind(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return callables.get(value.id)
        if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
            owner = module_aliases.get(value.value.id)
            if owner == "importlib" and value.attr == "import_module":
                return "import"
            if owner == "builtins" and value.attr in {"__import__", "eval", "exec"}:
                return "import" if value.attr == "__import__" else value.attr
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "getattr"
            and len(value.args) >= 2
            and isinstance(value.args[0], ast.Name)
            and isinstance(value.args[1], ast.Constant)
            and isinstance(value.args[1].value, str)
        ):
            owner = module_aliases.get(value.args[0].id)
            attribute = value.args[1].value
            if owner == "importlib" and attribute == "import_module":
                return "import"
            if owner == "builtins" and attribute in {"__import__", "eval", "exec"}:
                return "import" if attribute == "__import__" else attribute
        return None

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            kind = callable_kind(node.value)
            targets: Iterable[ast.AST] = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in module_aliases
                    and module_aliases.get(target.id) != module_aliases[node.value.id]
                ):
                    module_aliases[target.id] = module_aliases[node.value.id]
                    changed = True
                if isinstance(target, ast.Name) and kind and callables.get(target.id) != kind:
                    callables[target.id] = kind
                    changed = True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = callable_kind(node.func)
        if kind in {"eval", "exec"}:
            fail("E_TEST_IMPORT", f"{kind} is forbidden in {module}")
        if kind == "import":
            target = (
                node.args[0].value
                if node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                else None
            )
            if target is None:
                fail("E_TEST_IMPORT", f"nonliteral dynamic import in {module}")
            if target.split(".", 1)[0] in {"control_plane", "adoption_enablement", "tests"} and target not in module_paths:
                fail("E_TEST_IMPORT", f"undeclared dynamic import {target} in {module}")
            if (
                (module == "control_plane" or module.startswith("control_plane."))
                and (target == "adoption_enablement" or target.startswith("adoption_enablement."))
            ) or (
                (module == "adoption_enablement" or module.startswith("adoption_enablement."))
                and (target == "control_plane" or target.startswith("control_plane."))
            ):
                fail("E_TEST_IMPORT", f"dynamic Core/adoption boundary import in {module}")
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and module_aliases.get(node.func.value.value.id) == "sys"
            and node.func.value.attr == "path"
            and node.func.attr in {"append", "extend", "insert"}
        ):
            fail("E_TEST_IMPORT", f"sys.path mutation is forbidden in {module}")


for module, path in module_paths.items():
    payload = captured[path]
    compile(payload, str(repository / path), "exec", dont_inherit=True)
    scan_source(module, payload, str(repository / path))


full_adoption_journal: dict[str, object] | None = None


def revalidate_sources() -> None:
    for path, payload in captured.items():
        if safe_read(path) != payload:
            fail("E_TEST_SOURCE", f"captured source drifted: {path}")
    if full_adoption_journal is not None:
        payload = bounded_binding_bytes(
            common_directory,
            (("codex-control-plane-core", True), ("adoption", True)),
            "journal.json",
            maximum=LIMIT,
            exact_mode=0o600,
        )
        try:
            current = adoption_contracts.load_active_adoption_journal(payload)
        except (TypeError, ValueError, RecursionError):
            fail("E_TEST_MUTEX", "active adoption journal is invalid")
        if current != full_adoption_journal:
            fail("E_TEST_MUTEX", "active adoption journal changed")


class VerifiedSourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname: str, path=None, target=None):
        del path, target
        relative = module_paths.get(fullname)
        if relative is None:
            if fullname == "control_plane" or fullname.startswith("control_plane."):
                raise ImportError("E_TEST_IMPORT: unapproved Core module")
            if fullname == "adoption_enablement" or fullname.startswith("adoption_enablement."):
                raise ImportError("E_TEST_IMPORT: unapproved adoption module")
            if fullname == "tests" or fullname.startswith("tests."):
                raise ImportError("E_TEST_IMPORT: unapproved test module")
            return None
        return importlib.util.spec_from_loader(
            fullname,
            self,
            origin=str(repository / relative),
            is_package=fullname in {"control_plane", "adoption_enablement", "tests"},
        )

    def create_module(self, spec):
        del spec
        return None

    def exec_module(self, module: ModuleType) -> None:
        relative = module_paths.get(module.__name__)
        if relative is None:
            raise ImportError("E_TEST_IMPORT: unapproved verified module")
        filename = str(repository / relative)
        module.__file__ = filename
        code = compile(captured[relative], filename, "exec", dont_inherit=True)
        exec(code, module.__dict__)


context_root: Path | None = None
try:
    sys.meta_path.insert(0, VerifiedSourceLoader())
    revalidate_sources()
    adoption_contracts = importlib.import_module("control_plane.contracts")
    if verification_context is not None:
        journal_payload = bounded_binding_bytes(
            common_directory,
            (("codex-control-plane-core", True), ("adoption", True)),
            "journal.json",
            maximum=LIMIT,
            exact_mode=0o600,
        )
        try:
            full_adoption_journal = adoption_contracts.load_active_adoption_journal(
                journal_payload
            )
        except (TypeError, ValueError, RecursionError):
            fail("E_TEST_MUTEX", "active adoption journal is invalid")
        if full_adoption_journal.get("state_digest") != verification_context.get(
            "state_digest"
        ):
            fail("E_TEST_MUTEX", "active adoption journal binding changed")
    revalidate_sources()
    toolchain = importlib.import_module("control_plane.toolchain")
    context_root = Path(tempfile.mkdtemp(prefix="codex-control-plane-gate-"))
    context = toolchain.build_closed_execution_context(
        repository,
        temp_root=context_root,
    )
    context.validate_executables()
    revalidate_sources()
    gate = importlib.import_module("tests.core_gate")
    gate_executed = True
    returncode = gate.run_gate(
        repository=repository,
        context=context,
        test_names=test_names,
        shell_paths=(*core_gate_paths, "scripts/control-plane-adoption"),
        revalidate_sources=revalidate_sources,
        authoritative_git_environment=toolchain.authoritative_git_environment,
    )
except SystemExit:
    raise
except Exception as error:
    emit_gate_error(error)
    returncode = 1
finally:
    if context_root is not None:
        config = context_root / "gitconfig"
        try:
            metadata = config.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == os.geteuid()
            and metadata.st_nlink == 1
            and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_size == 0
        ):
            config.unlink()
        try:
            context_root.rmdir()
        except OSError:
            pass
    release_lock()
raise SystemExit(returncode)
PY
