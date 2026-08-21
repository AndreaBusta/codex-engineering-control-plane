from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import control_plane.lockfile as lockfile
from control_plane.lockfile import ACTIVE_RUNTIME_MODULES, runtime_digest, validate_lock


ROOT = Path(__file__).resolve().parents[1]
LOCK_MAX_BYTES = 64 * 1024
READ_CHUNK_BYTES = 64 * 1024
RUNTIME_MODULE_MAX_BYTES = 1024 * 1024
RUNTIME_TOTAL_MAX_BYTES = 8 * 1024 * 1024
EXPECTED_CORE_MODULES = (
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
    "stable_pause.py",
    "survey.py",
    "task_state.py",
    "toolchain.py",
    "verification.py",
)


def independent_runtime_digest(root: Path) -> str:
    hasher = sha256()
    for name in EXPECTED_CORE_MODULES:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((root / "control_plane" / name).read_bytes())
        hasher.update(b"\0")
    return f"sha256:{hasher.hexdigest()}"


def literal_module_inventory(source: str) -> tuple[str, ...]:
    match = re.search(
        r"ACTIVE_RUNTIME_MODULES\s*=\s*\((?P<body>.*?)\n\)",
        source,
        re.DOTALL,
    )
    if match is None:
        return ()
    return tuple(
        re.findall(r'^\s+"([a-z_]+\.py)",\s*$', match.group("body"), re.MULTILINE)
    )


def local_tree_snapshot(root: Path) -> tuple[tuple[str, str, int, bytes], ...]:
    snapshot: list[tuple[str, str, int, bytes]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        kind = "file" if stat.S_ISREG(metadata.st_mode) else "directory"
        payload = path.read_bytes() if kind == "file" else b""
        snapshot.append(
            (
                path.relative_to(root).as_posix(),
                kind,
                stat.S_IMODE(metadata.st_mode),
                payload,
            )
        )
    return tuple(snapshot)


class CoreLockfileTests(unittest.TestCase):
    def _runtime_fixture(self, root: Path, *, marker: Path | None = None) -> None:
        package = root / "control_plane"
        package.mkdir(parents=True)
        for name in EXPECTED_CORE_MODULES:
            payload = f"# {name}\n"
            if name == "__init__.py" and marker is not None:
                payload = (
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')\n"
                )
            (package / name).write_text(payload, encoding="utf-8")

    def _write_bootstrap_lock(
        self,
        root: Path,
        *,
        schema: int = 2,
        product: str = "3.1.0-core.2",
        layout: str = "source",
        package: str = "control_plane",
        modules: tuple[str, ...] = EXPECTED_CORE_MODULES,
    ) -> None:
        lock = root / ".codex" / "control-plane.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        module_lines = "\n".join(f'  "{name}",' for name in modules)
        digest_lines = [
            f'runtime = "{independent_runtime_digest(root)}"',
        ]
        for digest_name, relative in (
            ("entrypoint", Path("scripts/control-plane")),
            ("hook_entrypoint", Path(".codex/hooks/control_plane_hook.py")),
        ):
            candidate = root / relative
            if candidate.is_file():
                digest_lines.append(
                    f'{digest_name} = "sha256:{sha256(candidate.read_bytes()).hexdigest()}"'
                )
        lock.write_text(
            f"schema_version = {schema}\n"
            f'product_version = "{product}"\n'
            f'runtime_layout = "{layout}"\n'
            f'runtime_package = "{package}"\n'
            "runtime_modules = [\n"
            f"{module_lines}\n"
            "]\n\n"
            "[digests]\n"
            + "\n".join(digest_lines)
            + "\n",
            encoding="utf-8",
        )

    def _bootstrap_fixture(self, root: Path, marker: Path) -> None:
        self._runtime_fixture(root, marker=marker)
        (root / "scripts").mkdir()
        (root / ".codex" / "hooks").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "control-plane", root / "scripts" / "control-plane")
        shutil.copy2(
            ROOT / ".codex" / "hooks" / "control_plane_hook.py",
            root / ".codex" / "hooks" / "control_plane_hook.py",
        )
        self._write_bootstrap_lock(root)

    def _run_bootstrap(
        self,
        root: Path,
        kind: str,
        *,
        input_text: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if kind == "launcher":
            arguments = [str(root / "scripts" / "control-plane"), "doctor", "--json"]
            selected_input = None
        else:
            arguments = [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=/dev/null",
                str(root / ".codex" / "hooks" / "control_plane_hook.py"),
            ]
            selected_input = input_text or '{"hook_event_name":"UserPromptSubmit"}'
        return subprocess.run(
            arguments,
            cwd=root,
            env=environment,
            input=selected_input,
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )

    @staticmethod
    def _entry_module_source(kind: str, marker: Path, value: str) -> str:
        if kind == "launcher":
            return (
                "from pathlib import Path\n\n"
                "def main(argv):\n"
                f"    Path({str(marker)!r}).write_text({value!r}, encoding='utf-8')\n"
                "    return 0\n"
            )
        return (
            "from pathlib import Path\n\n"
            "def run_hook(raw_input, *, expected_root=None):\n"
            f"    Path({str(marker)!r}).write_text({value!r}, encoding='utf-8')\n"
            "    return ''\n"
        )

    def _executable_bootstrap_fixture(
        self,
        root: Path,
        marker: Path,
        kind: str,
        *,
        package_source: str = "# verified package\n",
    ) -> Path:
        self._runtime_fixture(root)
        target_name = "cli.py" if kind == "launcher" else "hooks.py"
        target = root / "control_plane" / target_name
        target.write_text(
            self._entry_module_source(kind, marker, "src"),
            encoding="utf-8",
        )
        (root / "control_plane" / "__init__.py").write_text(
            package_source,
            encoding="utf-8",
        )
        (root / "scripts").mkdir()
        (root / ".codex" / "hooks").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "control-plane", root / "scripts" / "control-plane")
        shutil.copy2(
            ROOT / ".codex" / "hooks" / "control_plane_hook.py",
            root / ".codex" / "hooks" / "control_plane_hook.py",
        )
        self._write_bootstrap_lock(root)
        return target

    def _install_timestamp_valid_malicious_cache(
        self,
        target: Path,
        *,
        kind: str,
        marker: Path,
    ) -> None:
        source = self._entry_module_source(kind, marker, "src")
        malicious = self._entry_module_source(kind, marker, "pyc")
        self.assertEqual(len(source.encode("utf-8")), len(malicious.encode("utf-8")))
        target.write_text(malicious, encoding="utf-8")
        fixed_time = 1_700_000_000
        os.utime(target, (fixed_time, fixed_time))
        cache = target.parent / "__pycache__" / (
            f"{target.stem}.{sys.implementation.cache_tag}.pyc"
        )
        cache.parent.mkdir(mode=0o755)
        py_compile.compile(
            str(target),
            cfile=str(cache),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
        )
        target.write_text(source, encoding="utf-8")
        os.utime(target, (fixed_time, fixed_time))

    def test_schema_two_declares_independent_exact_core_inventory(self) -> None:
        lock = tomllib.loads(
            (ROOT / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
        )

        self.assertEqual(ACTIVE_RUNTIME_MODULES, EXPECTED_CORE_MODULES)
        self.assertEqual(len(EXPECTED_CORE_MODULES), 27)
        self.assertEqual(getattr(lockfile, "LOCK_MAX_BYTES", None), LOCK_MAX_BYTES)
        self.assertEqual(getattr(lockfile, "READ_CHUNK_BYTES", None), READ_CHUNK_BYTES)
        self.assertEqual(
            getattr(lockfile, "RUNTIME_MODULE_MAX_BYTES", None),
            RUNTIME_MODULE_MAX_BYTES,
        )
        self.assertEqual(
            getattr(lockfile, "RUNTIME_TOTAL_MAX_BYTES", None),
            RUNTIME_TOTAL_MAX_BYTES,
        )
        self.assertEqual(lock["schema_version"], 2)
        self.assertEqual(lock["product_version"], "3.1.0-core.2")
        self.assertEqual(lock["runtime_layout"], "source")
        self.assertEqual(lock["runtime_package"], "control_plane")
        self.assertEqual(tuple(lock["runtime_modules"]), EXPECTED_CORE_MODULES)
        self.assertEqual(lock["hook_mode"], "soft-enforce")
        self.assertEqual(lock["hook_trust"], "pending_hook_trust")

    def test_repository_lock_matches_independent_digest_oracles(self) -> None:
        lock = tomllib.loads(
            (ROOT / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
        )
        expected_paths = {
            "project_policy": ROOT / ".codex" / "project-policy.toml",
            "resource_registry": ROOT / ".codex" / "resource-registry.toml",
            "hooks": ROOT / ".codex" / "hooks.json",
            "hook_entrypoint": ROOT / ".codex" / "hooks" / "control_plane_hook.py",
            "git_pre_commit": ROOT / ".codex" / "git-hooks" / "pre-commit",
            "git_pre_push": ROOT / ".codex" / "git-hooks" / "pre-push",
            "entrypoint": ROOT / "scripts" / "control-plane",
        }
        observed = {
            name: f"sha256:{sha256(path.read_bytes()).hexdigest()}"
            for name, path in expected_paths.items()
        }
        observed["runtime"] = independent_runtime_digest(ROOT)

        self.assertEqual(lock["digests"], observed)
        self.assertEqual(runtime_digest(ROOT), observed["runtime"])
        self.assertEqual(validate_lock(ROOT), [])

    def test_bootstraps_hardcode_exact_allowlist_before_every_import(self) -> None:
        for relative in (
            "scripts/control-plane",
            ".codex/hooks/control_plane_hook.py",
        ):
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(literal_module_inventory(source), EXPECTED_CORE_MODULES)
                allowlist = source.index("ACTIVE_RUNTIME_MODULES = (")
                schema = source.index('lock.get("schema_version")')
                inventory = source.index("if inventory(runtime) != expected_modules")
                digest = source.index('declared_digests.get("runtime")')
                loader = source.index("class VerifiedCoreLoader")
                first_import = source.index('import_module("control_plane')
                self.assertLess(allowlist, schema)
                self.assertLess(schema, first_import)
                self.assertLess(inventory, first_import)
                self.assertLess(digest, first_import)
                self.assertLess(loader, first_import)
                self.assertIn("verified_sources[name]", source)
                self.assertIn("sys.dont_write_bytecode = True", source)
                self.assertIn("sys.pycache_prefix = os.devnull", source)
                self.assertNotIn('glob("*.py")', source)
                self.assertNotIn("spec_from_file_location", source)

        launcher = (ROOT / "scripts" / "control-plane").read_text(encoding="utf-8")
        self.assertNotIn("command -v", launcher)
        self.assertIn("for candidate in", launcher)
        self.assertNotIn("PYTHON_CANDIDATES=(", launcher)
        self.assertTrue(launcher.startswith("#!/bin/sh\n"))
        self.assertIn("/usr/bin/env -i", launcher)
        self.assertIn("-X pycache_prefix=/dev/null", launcher)
        self.assertIn("import sys, tomllib", launcher)
        self.assertIn("sys.version_info >= (3, 11)", launcher)

    def test_stage0_launcher_opens_every_runtime_leaf_nonblocking(self) -> None:
        source = (ROOT / "scripts" / "control-plane").read_text(encoding="utf-8")
        read_private = source.split("def read_private(path, limit, code):", 1)[1]
        read_private = read_private.split("\ndef inventory(runtime):", 1)[0]

        self.assertIn('getattr(os, "O_NONBLOCK", 0)', read_private)
        self.assertLess(read_private.index("flags = ("), read_private.index("os.open(path, flags)"))
        self.assertLess(read_private.index("os.open(path, flags)"), read_private.index("os.fstat(descriptor)"))

    def test_bootstraps_ignore_timestamp_valid_repository_bytecode(self) -> None:
        for kind in ("launcher", "hook"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "selected-source"
                target = self._executable_bootstrap_fixture(root, marker, kind)
                self._install_timestamp_valid_malicious_cache(
                    target,
                    kind=kind,
                    marker=marker,
                )

                completed = self._run_bootstrap(root, kind)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(marker.read_text(encoding="utf-8"), "src")
                self.assertNotIn("Traceback", completed.stderr)

    def test_bootstraps_ignore_site_pth_and_sitecustomize_markers(self) -> None:
        for kind in ("launcher", "hook"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                selected = root / "selected-source"
                injected = root / "site-imported"
                self._executable_bootstrap_fixture(root, selected, kind)
                attacker = root / "attacker"
                user_site = (
                    attacker
                    / "lib"
                    / f"python{sys.version_info.major}.{sys.version_info.minor}"
                    / "site-packages"
                )
                user_site.mkdir(parents=True)
                payload = (
                    "from pathlib import Path; "
                    f"Path({str(injected)!r}).write_text('site', encoding='utf-8')\n"
                )
                (attacker / "sitecustomize.py").write_text(payload, encoding="utf-8")
                (user_site / "attack.pth").write_text(
                    "import pathlib; "
                    f"pathlib.Path({str(injected)!r}).write_text('pth', encoding='utf-8')\n",
                    encoding="utf-8",
                )
                environment = os.environ.copy()
                environment.update(
                    {
                        "PYTHONPATH": str(attacker),
                        "PYTHONUSERBASE": str(attacker),
                    }
                )

                completed = self._run_bootstrap(
                    root,
                    kind,
                    environment=environment,
                )

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(selected.read_text(encoding="utf-8"), "src")
                self.assertFalse(injected.exists())
                self.assertNotIn("Traceback", completed.stderr)

    def test_bootstraps_reject_every_shadow_entry_except_private_pycache(self) -> None:
        for kind in ("launcher", "hook"):
            for shadow in ("module-package", "root-pyc", "unrelated-file"):
                with (
                    self.subTest(kind=kind, shadow=shadow),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    marker = root / "shadow-imported"
                    self._executable_bootstrap_fixture(root, marker, kind)
                    runtime = root / "control_plane"
                    if shadow == "module-package":
                        name = "cli" if kind == "launcher" else "hooks"
                        package = runtime / name
                        package.mkdir()
                        package.joinpath("__init__.py").write_text(
                            self._entry_module_source(kind, marker, "bad"),
                            encoding="utf-8",
                        )
                    elif shadow == "root-pyc":
                        (runtime / "unapproved.pyc").write_bytes(b"unapproved")
                    else:
                        (runtime / "README.txt").write_text("unapproved\n", encoding="utf-8")

                    completed = self._run_bootstrap(root, kind)

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("E_RUNTIME_MODULE_SET", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertFalse(marker.exists())

    def test_bootstraps_execute_the_verified_byte_map_after_source_changes(self) -> None:
        for kind in ("launcher", "hook"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / "selected-source"
                target_name = "cli.py" if kind == "launcher" else "hooks.py"
                target = root / "control_plane" / target_name
                changed_source = self._entry_module_source(kind, marker, "new")
                package_source = (
                    "from pathlib import Path\n"
                    f"Path({str(target)!r}).write_text({changed_source!r}, encoding='utf-8')\n"
                )
                self._executable_bootstrap_fixture(
                    root,
                    marker,
                    kind,
                    package_source=package_source,
                )

                completed = self._run_bootstrap(root, kind)

                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(marker.read_text(encoding="utf-8"), "src")
                self.assertNotIn("Traceback", completed.stderr)

    def test_launcher_cleans_shell_loader_and_python_environment_before_runtime(self) -> None:
        source = (ROOT / "scripts" / "control-plane").read_text(encoding="utf-8")
        self.assertIn("exec /usr/bin/env -i CONTROL_PLANE_CLEAN_SHELL=1", source)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "environment-result"
            startup_marker = root / "shell-startup"
            self._executable_bootstrap_fixture(root, marker, "launcher")
            target = root / "control_plane" / "cli.py"
            target.write_text(
                "from pathlib import Path\n"
                "import os\n\n"
                "def main(argv):\n"
                "    hostile = {'BASH_ENV', 'ENV', 'LD_PRELOAD', "
                "'DYLD_INSERT_LIBRARIES', 'PYTHONPATH', 'PYTHONHOME'}\n"
                f"    Path({str(marker)!r}).write_text("
                "'dirty' if hostile & set(os.environ) else 'clean', encoding='utf-8')\n"
                "    return 0\n",
                encoding="utf-8",
            )
            self._write_bootstrap_lock(root)
            startup = root / "hostile-startup.sh"
            startup.write_text(
                f"printf injected > {str(startup_marker)!r}\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "BASH_ENV": str(startup),
                    "ENV": str(startup),
                    "LD_PRELOAD": "hostile-loader-value",
                    "DYLD_INSERT_LIBRARIES": "hostile-loader-value",
                    "PYTHONPATH": str(root / "hostile-python-path"),
                    "PYTHONHOME": str(root / "hostile-python-home"),
                    "PATH": str(root / "hostile-bin"),
                    "BASH_FUNC_cd%%": (
                        "() { printf injected > "
                        + str(startup_marker)
                        + '; builtin cd "$@"; }'
                    ),
                }
            )

            completed = self._run_bootstrap(
                root,
                "launcher",
                environment=environment,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "clean")
            self.assertFalse(startup_marker.exists())
            self.assertNotIn("Traceback", completed.stderr)

    def test_direct_hook_requires_explicit_closed_python_and_is_not_productive_path(self) -> None:
        source = (ROOT / ".codex" / "hooks" / "control_plane_hook.py").read_text(
            encoding="utf-8"
        )
        hooks = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        commands = {
            hook["command"]
            for entries in hooks["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        }

        self.assertTrue(source.startswith("#!/usr/bin/python3\n"))
        self.assertIn("direct hook requires explicit compatible Python", source)
        self.assertIn("sys.flags.no_site", source)
        self.assertIn("-I -S -B -X pycache_prefix=/dev/null", source)
        self.assertEqual(len(commands), 1)
        command = commands.pop()
        self.assertIn("scripts/control-plane", command)
        self.assertNotIn("control_plane_hook.py", command)
        completed = subprocess.run(
            [str(ROOT / ".codex" / "hooks" / "control_plane_hook.py")],
            cwd=ROOT,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("E_RUNTIME_BOOTSTRAP", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_bootstraps_reject_foreign_hook_cwd_without_mutation(self) -> None:
        payload_template = (
            '{{"hook_event_name":"PreToolUse","cwd":{cwd},'
            '"tool_name":"Bash","tool_input":{{"command":"pwd"}}}}'
        )
        for kind in ("launcher", "hook"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as foreign_directory,
            ):
                foreign = Path(foreign_directory)
                initialized = subprocess.run(
                    ["/usr/bin/git", "init", "--quiet", str(foreign)],
                    env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                before = local_tree_snapshot(foreign)
                payload = payload_template.format(cwd=json.dumps(str(foreign)))
                if kind == "launcher":
                    arguments = [str(ROOT / "scripts" / "control-plane"), "__hook__"]
                else:
                    arguments = [
                        sys.executable,
                        "-I",
                        "-S",
                        "-B",
                        "-X",
                        "pycache_prefix=/dev/null",
                        str(ROOT / ".codex" / "hooks" / "control_plane_hook.py"),
                    ]

                completed = subprocess.run(
                    arguments,
                    cwd=ROOT,
                    env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                    input=payload,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )

                self.assertEqual(completed.returncode, 1)
                self.assertIn("E_HOOK_REPOSITORY", completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)
                self.assertEqual(local_tree_snapshot(foreign), before)

    def test_bootstraps_collapse_deep_hook_json_to_stable_error(self) -> None:
        nested = "[" * 1500 + "0" + "]" * 1500
        payload = (
            '{"hook_event_name":"PreToolUse","cwd":'
            + json.dumps(str(ROOT))
            + ',"tool_name":"Bash","tool_input":'
            + nested
            + "}"
        )
        for kind in ("launcher", "hook"):
            with self.subTest(kind=kind):
                if kind == "launcher":
                    arguments = [str(ROOT / "scripts" / "control-plane"), "__hook__"]
                else:
                    arguments = [
                        sys.executable,
                        "-I",
                        "-S",
                        "-B",
                        "-X",
                        "pycache_prefix=/dev/null",
                        str(ROOT / ".codex" / "hooks" / "control_plane_hook.py"),
                    ]
                completed = subprocess.run(
                    arguments,
                    cwd=ROOT,
                    env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                    input=payload,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )

                self.assertEqual(completed.returncode, 1)
                self.assertIn("E_HOOK_INPUT", completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)

    def test_bootstraps_reject_contract_drift_before_import(self) -> None:
        cases = (
            ("schema", {"schema": 1}, "E_RUNTIME_BOOTSTRAP", None),
            ("product", {"product": "3.1.0-core.0"}, "E_RUNTIME_BOOTSTRAP", None),
            ("layout", {"layout": "isolated"}, "E_RUNTIME_LAYOUT", None),
            ("package", {"package": "wrong"}, "E_RUNTIME_LAYOUT", None),
            (
                "declared-order",
                {"modules": tuple(reversed(EXPECTED_CORE_MODULES))},
                "E_RUNTIME_MODULE_SET",
                None,
            ),
            ("extra", {}, "E_RUNTIME_MODULE_SET", "unexpected.py"),
        )
        for kind in ("launcher", "hook"):
            for label, overrides, error_code, extra in cases:
                with self.subTest(kind=kind, case=label), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    marker = root / "runtime-imported"
                    self._bootstrap_fixture(root, marker)
                    if extra is not None:
                        (root / "control_plane" / extra).write_text("# extra\n", encoding="utf-8")
                    else:
                        self._write_bootstrap_lock(root, **overrides)

                    completed = self._run_bootstrap(root, kind)

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(error_code, completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertFalse(marker.exists())

    def test_bootstraps_reject_malformed_module_and_digest_types_without_traceback(self) -> None:
        for kind in ("launcher", "hook"):
            for field in ("runtime_modules", "digests"):
                with self.subTest(kind=kind, field=field), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    marker = root / "runtime-imported"
                    self._bootstrap_fixture(root, marker)
                    lock = root / ".codex" / "control-plane.lock"
                    source = lock.read_text(encoding="utf-8")
                    if field == "runtime_modules":
                        source = re.sub(
                            r"runtime_modules = \[.*?\]\n\n",
                            "runtime_modules = 1\n\n",
                            source,
                            flags=re.DOTALL,
                        )
                    else:
                        source = source.replace("[digests]\n", "digests = 1\n")
                    lock.write_text(source, encoding="utf-8")

                    completed = self._run_bootstrap(root, kind)

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("E_RUNTIME_BOOTSTRAP", completed.stderr)
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertFalse(marker.exists())

    def test_validate_lock_rejects_symlink_fifo_hardlink_and_dataless_lock(self) -> None:
        for case in ("symlink", "fifo", "hardlink"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                lock = root / ".codex" / "control-plane.lock"
                lock.parent.mkdir(parents=True)
                other = root / "other.lock"
                other.write_text("schema_version = 2\n", encoding="utf-8")
                if case == "symlink":
                    lock.symlink_to(other)
                elif case == "fifo":
                    os.mkfifo(lock)
                else:
                    os.link(other, lock)
                self.assertEqual([issue.code for issue in validate_lock(root)], ["L_PARSE"])

    def test_private_ownership_and_modes_are_enforced_for_lock_runtime_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            runtime = root / "control_plane"
            runtime.joinpath("__pycache__").mkdir(mode=0o755)
            self.assertTrue(runtime_digest(root).startswith("sha256:"))

            runtime.joinpath(EXPECTED_CORE_MODULES[-1]).chmod(0o666)
            with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                runtime_digest(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            runtime = root / "control_plane"
            runtime.chmod(0o777)
            with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                runtime_digest(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            cache = root / "control_plane" / "__pycache__"
            cache.mkdir(mode=0o777)
            cache.chmod(0o777)
            with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                runtime_digest(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            with patch.object(lockfile.os, "geteuid", return_value=os.geteuid() + 1):
                with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                    runtime_digest(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / ".codex" / "control-plane.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text("schema_version = 2\n", encoding="utf-8")
            lock.chmod(0o666)
            self.assertEqual([issue.code for issue in validate_lock(root)], ["L_PARSE"])

    def test_bootstraps_reject_writable_authority_and_self_files_before_import(self) -> None:
        for kind in ("launcher", "hook"):
            for target_kind in ("lock", "runtime", "bootstrap"):
                with (
                    self.subTest(kind=kind, target=target_kind),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    marker = root / "runtime-imported"
                    target = self._executable_bootstrap_fixture(root, marker, kind)
                    if target_kind == "lock":
                        selected = root / ".codex" / "control-plane.lock"
                    elif target_kind == "runtime":
                        selected = target
                    elif kind == "launcher":
                        selected = root / "scripts" / "control-plane"
                    else:
                        selected = root / ".codex" / "hooks" / "control_plane_hook.py"
                    selected.chmod(selected.stat().st_mode | 0o022)

                    completed = self._run_bootstrap(root, kind)

                    self.assertNotEqual(completed.returncode, 0)
                    self.assertRegex(completed.stderr, r"E_RUNTIME_(?:BOOTSTRAP|MODULE_SET)")
                    self.assertNotIn("Traceback", completed.stderr)
                    self.assertFalse(marker.exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / ".codex" / "control-plane.lock"
            lock.parent.mkdir(parents=True)
            lock.write_text("schema_version = 2\n", encoding="utf-8")
            with patch.object(lockfile, "_is_dataless", return_value=True):
                self.assertEqual([issue.code for issue in validate_lock(root)], ["L_PARSE"])

    def test_runtime_digest_rejects_extra_and_missing_modules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            (root / "control_plane" / "unexpected.py").write_text("# drift\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                runtime_digest(root)
            (root / "control_plane" / "unexpected.py").unlink()
            (root / "control_plane" / EXPECTED_CORE_MODULES[-1]).unlink()
            with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                runtime_digest(root)

    def test_runtime_digest_rejects_symlink_fifo_hardlink_and_oversize(self) -> None:
        for case in ("symlink", "fifo", "hardlink", "oversize"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self._runtime_fixture(root)
                target = root / "control_plane" / EXPECTED_CORE_MODULES[-1]
                target.unlink()
                if case == "symlink":
                    target.symlink_to(root / "control_plane" / EXPECTED_CORE_MODULES[0])
                elif case == "fifo":
                    os.mkfifo(target)
                elif case == "hardlink":
                    os.link(root / "control_plane" / EXPECTED_CORE_MODULES[0], target)
                else:
                    with target.open("wb") as handle:
                        handle.truncate(RUNTIME_MODULE_MAX_BYTES + 1)

                with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                    runtime_digest(root)

    def test_runtime_digest_rejects_dataless_and_total_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            is_dataless = getattr(lockfile, "_is_dataless", None)
            if is_dataless is None:
                self.fail("lockfile must expose the dataless predicate used by bounded reads")
            with patch.object(lockfile, "_is_dataless", return_value=True):
                with self.assertRaisesRegex(ValueError, "E_RUNTIME_DATALESS"):
                    runtime_digest(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            each = RUNTIME_TOTAL_MAX_BYTES // len(EXPECTED_CORE_MODULES) + 1
            self.assertLess(each, RUNTIME_MODULE_MAX_BYTES)
            for name in EXPECTED_CORE_MODULES:
                with (root / "control_plane" / name).open("wb") as handle:
                    handle.truncate(each)
            with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                runtime_digest(root)

    def test_runtime_and_lock_reads_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            real_read = os.read
            requested: list[int] = []

            def recording_read(descriptor: int, size: int) -> bytes:
                requested.append(size)
                return real_read(descriptor, size)

            lock_os = getattr(lockfile, "os", None)
            if lock_os is None:
                self.fail("bounded lock/runtime reads must use os.read")
            with patch.object(lock_os, "read", side_effect=recording_read):
                runtime_digest(root)
            self.assertTrue(requested)
            self.assertLessEqual(max(requested), READ_CHUNK_BYTES)

            lock = root / ".codex" / "control-plane.lock"
            lock.parent.mkdir(parents=True)
            with lock.open("wb") as handle:
                handle.truncate(LOCK_MAX_BYTES + 1)
            issues = validate_lock(root)
            self.assertEqual([issue.code for issue in issues], ["L_PARSE"])

    def test_runtime_digest_never_falls_back_between_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._runtime_fixture(root)
            isolated = root / ".codex" / "runtime" / "codex_control_plane_runtime_core_v3"
            isolated.mkdir(parents=True)
            for name in EXPECTED_CORE_MODULES:
                (isolated / name).write_text(f"# isolated {name}\n", encoding="utf-8")

            source_digest = runtime_digest(root)
            isolated_digest = runtime_digest(
                root,
                "codex_control_plane_runtime_core_v3",
                runtime_layout="isolated",
            )

            self.assertNotEqual(source_digest, isolated_digest)
            with self.assertRaisesRegex(ValueError, "E_RUNTIME_MODULE_SET"):
                runtime_digest(
                    root,
                    "codex_control_plane_runtime_core_v3",
                    runtime_layout="source",
                )


if __name__ == "__main__":
    unittest.main()
