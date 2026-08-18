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
tests.test_core_survey
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
control_plane/survey.py
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
tests/test_core_survey.py
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
  "$CORE_GATE_FILES" <<'PY'
from __future__ import annotations

import ast
import atexit
import fcntl
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
        or name == "tests"
        or name.startswith("tests.")
        for name in sys.modules
    )
):
    fail("E_TEST_BOOTSTRAP", "Python isolation contract failed")
if len(sys.argv) != 9:
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
test_names = words(sys.argv[4], label="CORE_TESTS")
test_files = words(sys.argv[5], label="CORE_TEST_FILES")
test_helpers = words(sys.argv[6], label="CORE_TEST_HELPERS")
test_package = words(sys.argv[7], label="CORE_TEST_PACKAGE")
shell_paths = words(sys.argv[8], label="CORE_GATE_FILES")
if source_script != repository / "tests" / "run.sh":
    fail("E_TEST_MANIFEST", "runner path is not canonical")
if set(test_files) != {
    module.replace(".", "/") + ".py" for module in test_names
}:
    fail("E_TEST_MANIFEST", "test module and source manifests differ")
if test_package != ("tests/__init__.py",):
    fail("E_TEST_MANIFEST", "test package manifest is not exact")
if set(shell_paths) != {
    "scripts/control-plane",
    "scripts/build-release-candidate",
    "tests/run.sh",
}:
    fail("E_TEST_MANIFEST", "shell gate manifest is not exact")


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
    )


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


def open_mutex(common: Path) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    before = common.lstat()
    validate_directory(before, exact_private=False)
    descriptor = os.open(common, flags)
    if identity(os.fstat(descriptor)) != identity(before):
        os.close(descriptor)
        fail("E_TEST_MUTEX", "Git common directory changed before open")
    try:
        for name in ("codex-control-plane-core", "locks"):
            try:
                child = os.open(name, flags, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(name, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(name, flags, dir_fd=descriptor)
            try:
                validate_directory(os.fstat(child), exact_private=True)
            except Exception:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        lock = os.open(
            "verification.lock",
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=descriptor,
        )
        metadata = os.fstat(lock)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            os.close(lock)
            fail("E_TEST_MUTEX", "verification lock is unsafe")
        return lock
    finally:
        os.close(descriptor)


lock_descriptor: int | None = open_mutex(common_git_directory(repository))
lock_held = False


def release_lock() -> None:
    global lock_descriptor, lock_held
    if lock_descriptor is None:
        return
    if lock_held:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
    try:
        os.close(lock_descriptor)
    except OSError:
        pass
    lock_descriptor = None
    lock_held = False


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
    os.close(lock_descriptor)
    lock_descriptor = None
    raise SystemExit(2)
except Exception:
    os.close(lock_descriptor)
    lock_descriptor = None
    raise
lock_held = True
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


python_paths = (*core_modules, *test_files, *test_helpers, *test_package)
all_paths = (*python_paths, *shell_paths)
if len(all_paths) != len(set(all_paths)):
    fail("E_TEST_MANIFEST", "gate source manifest contains duplicates")
captured = {path: safe_read(path) for path in all_paths}
if sum(len(payload) for payload in captured.values()) > TOTAL_LIMIT:
    fail("E_TEST_SOURCE", "gate source manifest exceeds its total byte limit")

module_paths: dict[str, str] = {}
for path in python_paths:
    if path == "control_plane/__init__.py":
        module = "control_plane"
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
    "tests",
    *test_names,
    *(path.removesuffix(".py").replace("/", ".") for path in test_helpers),
    *(
        "control_plane." + Path(path).stem
        for path in core_modules
        if path != "control_plane/__init__.py"
    ),
}:
    fail("E_TEST_MANIFEST", "module origin manifest is not exact")


def local_import(current: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package = current if current in {"control_plane", "tests"} else current.rpartition(".")[0]
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
    package: static_exports(package) for package in ("control_plane", "tests")
}


def scan_source(module: str, payload: bytes, filename: str) -> None:
    tree = ast.parse(payload, filename=filename)
    module_aliases: dict[str, str] = {}
    callables: dict[str, str] = {"__import__": "import", "eval": "eval", "exec": "exec"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                if target.split(".", 1)[0] in {"control_plane", "tests"} and target not in module_paths:
                    fail("E_TEST_IMPORT", f"undeclared local import {target} in {module}")
                if target in {"builtins", "importlib", "sys"}:
                    module_aliases[alias.asname or target] = target
        elif isinstance(node, ast.ImportFrom):
            target = local_import(module, node)
            if target.split(".", 1)[0] in {"control_plane", "tests"} and target not in module_paths:
                fail("E_TEST_IMPORT", f"undeclared local import {target} in {module}")
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
            if target.split(".", 1)[0] in {"control_plane", "tests"} and target not in module_paths:
                fail("E_TEST_IMPORT", f"undeclared dynamic import {target} in {module}")
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


def revalidate_sources() -> None:
    for path, payload in captured.items():
        if safe_read(path) != payload:
            fail("E_TEST_SOURCE", f"captured source drifted: {path}")


class VerifiedSourceLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname: str, path=None, target=None):
        del path, target
        relative = module_paths.get(fullname)
        if relative is None:
            if fullname == "control_plane" or fullname.startswith("control_plane."):
                raise ImportError("E_TEST_IMPORT: unapproved Core module")
            if fullname == "tests" or fullname.startswith("tests."):
                raise ImportError("E_TEST_IMPORT: unapproved test module")
            return None
        return importlib.util.spec_from_loader(
            fullname,
            self,
            origin=str(repository / relative),
            is_package=fullname in {"control_plane", "tests"},
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
        shell_paths=shell_paths,
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
