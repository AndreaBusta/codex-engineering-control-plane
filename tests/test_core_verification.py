from __future__ import annotations

import json
import fcntl
import multiprocessing
import os
from pathlib import Path
import py_compile
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from control_plane.contracts import contract_digest
from control_plane.verification import VerificationMutex, run_serialized_verification
from tests.test_core_task_state import (
    git,
    install_active_adoption_journal,
    install_provisioning_prefix,
    make_repo,
    private_state_identity_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


def runner_fixture(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(
        ROOT / "control_plane",
        repository / "control_plane",
        ignore=ignored,
    )
    shutil.copytree(
        ROOT / "adoption_enablement",
        repository / "adoption_enablement",
        ignore=ignored,
    )
    shutil.copytree(
        ROOT / "skills" / "control-plane-git",
        repository / "skills" / "control-plane-git",
        ignore=ignored,
    )
    shutil.copytree(
        ROOT / "templates" / "spec-pack",
        repository / "templates" / "spec-pack",
        ignore=ignored,
    )
    shutil.copytree(ROOT / "tests", repository / "tests", ignore=ignored)
    (repository / "scripts").mkdir()
    for name in ("control-plane", "build-release-candidate", "control-plane-adoption"):
        shutil.copy2(ROOT / "scripts" / name, repository / "scripts" / name)
    (repository / ".codex").mkdir()
    shutil.copy2(
        ROOT / ".codex" / "adoption-enablement.lock",
        repository / ".codex" / "adoption-enablement.lock",
    )
    initialized = subprocess.run(
        ["/usr/bin/git", "init", "--quiet", str(repository)],
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=5,
    )
    if initialized.returncode != 0:
        raise AssertionError(initialized.stderr.decode("utf-8", errors="replace"))
    return repository


def padded_source(source: str, *, size: int = 4_096) -> bytes:
    payload = source.encode("utf-8")
    if len(payload) + 2 > size:
        raise AssertionError("runner sentinel source exceeds fixture size")
    return payload + b"\n#" + (b" " * (size - len(payload) - 2))


def install_gate_sentinel(
    repository: Path,
    *,
    malicious_marker: Path | None = None,
) -> None:
    target = repository / "tests" / "core_gate.py"
    sentinel = padded_source("raise RuntimeError('E_TEST_SENTINEL')")
    fixed_time = 1_700_000_000
    if malicious_marker is not None:
        malicious = padded_source(
            "from pathlib import Path\n"
            f"Path({str(malicious_marker)!r}).write_text('pyc', encoding='utf-8')"
        )
        target.write_bytes(malicious)
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
    target.write_bytes(sentinel)
    os.utime(target, (fixed_time, fixed_time))


def run_fixture_gate(
    repository: Path,
    *,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", str(repository / "tests" / "run.sh")],
        cwd=repository,
        env=environment or {"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )


def hold_mutex(repo: str, ready: multiprocessing.Queue) -> None:
    with VerificationMutex(Path(repo)) as acquired:
        ready.put(acquired)
        time.sleep(1.0)


def install_active_adoption_verification_binding(repository: Path) -> Path:
    lock_path, _ = install_active_adoption_journal(repository)
    return lock_path


def replace_bound_verification_mutex(lock_path: Path, case: str) -> int:
    descriptor = os.open(
        lock_path,
        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
    )
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if case == "missing_file":
        lock_path.unlink()
    elif case == "replaced_file":
        lock_path.rename(lock_path.with_name("verification.displaced"))
    else:
        locks = lock_path.parent
        locks.rename(locks.with_name("locks.displaced"))
        locks.mkdir(mode=0o700)
    if case != "missing_file":
        replacement = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        os.close(replacement)
    return descriptor


def mutate_adoption_journal(repository: Path, case: str) -> None:
    path = (
        repository
        / ".git"
        / "codex-control-plane-core"
        / "adoption"
        / "journal.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    if case == "duplicate":
        payload = path.read_text(encoding="utf-8").replace(
            '"schema_version":1',
            '"schema_version":1,"schema_version":1',
            1,
        )
        path.write_text(payload, encoding="utf-8")
        return
    if case == "extra":
        value["unexpected"] = False
    elif case == "schema":
        value["schema_version"] = 2
    elif case == "target_binding_extra":
        value["target_binding"]["unexpected"] = False
    elif case == "parent_mode":
        value["managed_parent_directories"][0]["mode"] = 0o777
    elif case == "parent_order":
        value["managed_parent_directories"][1:3] = reversed(
            value["managed_parent_directories"][1:3]
        )
    elif case == "repository_scan":
        value["managed_repository_scan"]["gitlinks_absent"] = False
    elif case == "created_path":
        value["created_directories"].append(
            {"path": "../escape", "mode": 0o755, "identity": None}
        )
    elif case == "published_record":
        value["published_records"].append({"path": "scripts/control-plane"})
    elif case == "target_lock_path":
        value["target_lock_record"]["path"] = ".codex/other.lock"
    elif case == "prior_git_config":
        value["prior_git_config"] = {"core.hooksPath": "hooks"}
    elif case == "rollback_before":
        value["rollback_records"][0]["before"] = "present"
    elif case == "nested_authorizes":
        value["target_binding"]["authorizes"] = True
    elif case == "digest_syntax":
        value["plan_digest"] = "invalid"
    else:
        raise AssertionError(f"unknown journal mutation: {case}")
    unsigned = {key: item for key, item in value.items() if key != "state_digest"}
    value["state_digest"] = contract_digest(unsigned)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class CoreVerificationTests(unittest.TestCase):
    def test_runner_fixture_carries_every_reconciled_governing_surface(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = runner_fixture(Path(directory))

            for relative in (
                "adoption_enablement/manifest.py",
                "control_plane/stable_pause.py",
                "control_plane/survey.py",
                "skills/control-plane-git/SKILL.md",
                "templates/spec-pack/SPEC_PACK_MANIFEST.json",
            ):
                with self.subTest(relative=relative):
                    self.assertTrue((repository / relative).is_file(), relative)

    def test_transitional_provisioning_blocks_core_verifier_without_mutation(self) -> None:
        for prefix in ("P2", "P2Q", "P3", "P3Q", "P4", "P4T"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                repository = runner_fixture(Path(directory))
                install_provisioning_prefix(repository, prefix)
                before = private_state_identity_snapshot(repository)

                with self.assertRaisesRegex(ValueError, "^E_VERIFICATION_LOCK:"):
                    with VerificationMutex(repository):
                        self.fail("Core verifier accepted transitional provisioning")
                self.assertEqual(private_state_identity_snapshot(repository), before)

    def test_transitional_provisioning_blocks_runner_without_mutation(self) -> None:
        for prefix in ("P2", "P2Q", "P3", "P3Q", "P4", "P4T"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                repository = runner_fixture(Path(directory))
                install_gate_sentinel(repository)
                install_provisioning_prefix(repository, prefix)
                before = private_state_identity_snapshot(repository)

                completed = run_fixture_gate(repository)

                self.assert_stable_gate_error(completed, "E_TEST_MUTEX")
                self.assertEqual(private_state_identity_snapshot(repository), before)

    def test_core_and_runner_require_a_closed_active_adoption_journal(self) -> None:
        for case in (
            "duplicate",
            "extra",
            "schema",
            "target_binding_extra",
            "parent_mode",
            "parent_order",
            "repository_scan",
            "created_path",
            "published_record",
            "target_lock_path",
            "prior_git_config",
            "rollback_before",
            "nested_authorizes",
            "digest_syntax",
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repository = runner_fixture(Path(directory))
                install_gate_sentinel(repository)
                install_active_adoption_verification_binding(repository)
                mutate_adoption_journal(repository, case)

                with self.assertRaisesRegex(ValueError, "^E_VERIFICATION_LOCK:"):
                    with VerificationMutex(repository):
                        self.fail("Core accepted a non-closed adoption journal")
                completed = run_fixture_gate(repository)
                self.assert_stable_gate_error(completed, "E_TEST_MUTEX")

    def test_core_verifier_retains_the_locked_directory_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = make_repo(Path(directory) / "repo")
            with VerificationMutex(repository) as acquired:
                self.assertTrue(acquired)
            state = repository / ".git" / "codex-control-plane-core"
            locks = state / "locks"
            lock_path = locks / "verification.lock"
            displaced = state / "locks.displaced"
            real_flock = fcntl.flock
            replaced = False

            def replace_directory_after_flock(descriptor: int, operation: int) -> None:
                nonlocal replaced
                real_flock(descriptor, operation)
                if operation == (fcntl.LOCK_EX | fcntl.LOCK_NB) and not replaced:
                    locks.rename(displaced)
                    locks.mkdir(mode=0o700)
                    (displaced / "verification.lock").rename(lock_path)
                    replaced = True

            with patch(
                "control_plane.verification.fcntl.flock",
                side_effect=replace_directory_after_flock,
            ), self.assertRaisesRegex(ValueError, "^E_VERIFICATION_LOCK:"):
                with VerificationMutex(repository):
                    self.fail("Core accepted a substituted lock directory")

    def test_runner_retains_the_locked_directory_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = runner_fixture(root)
            install_gate_sentinel(repository)
            ready = root / "runner-ready"
            resume = root / "runner-resume"
            runner = repository / "tests" / "run.sh"
            source = runner.read_text(encoding="utf-8")
            marker = "lock_held = True\nvalidate_lifecycle_after_flock("
            replacement = (
                "lock_held = True\n"
                "import time as _mutex_test_time\n"
                f"Path({str(ready)!r}).write_text('ready', encoding='utf-8')\n"
                "for _mutex_test_index in range(500):\n"
                f"    if Path({str(resume)!r}).exists():\n"
                "        break\n"
                "    _mutex_test_time.sleep(0.01)\n"
                "else:\n"
                "    fail('E_TEST_MUTEX', 'test rendezvous timed out')\n"
                "validate_lifecycle_after_flock("
            )
            self.assertIn(marker, source)
            runner.write_text(source.replace(marker, replacement, 1), encoding="utf-8")
            process = subprocess.Popen(
                ["/bin/sh", str(runner)],
                cwd=repository,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(200):
                if ready.exists():
                    break
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            state = repository / ".git" / "codex-control-plane-core"
            locks = state / "locks"
            displaced = state / "locks.displaced"
            locks.rename(displaced)
            locks.mkdir(mode=0o700)
            (displaced / "verification.lock").rename(
                locks / "verification.lock"
            )
            resume.write_text("resume", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=10)
            completed = subprocess.CompletedProcess(
                process.args,
                process.returncode,
                stdout,
                stderr,
            )
            self.assert_stable_gate_error(completed, "E_TEST_MUTEX")

    def test_runner_rejects_a_symlinked_adoption_binding_ancestor(self) -> None:
        for case in ("marker", "journal"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repository = runner_fixture(Path(directory))
                install_gate_sentinel(repository)
                install_active_adoption_verification_binding(repository)
                if case == "marker":
                    original = repository / ".codex"
                    displaced = repository / ".codex.displaced"
                else:
                    state = repository / ".git" / "codex-control-plane-core"
                    original = state / "adoption"
                    displaced = state / "adoption.displaced"
                original.rename(displaced)
                original.symlink_to(displaced.name, target_is_directory=True)

                completed = run_fixture_gate(repository)

                self.assert_stable_gate_error(completed, "E_TEST_MUTEX")

    def test_bound_core_verifier_rejects_a_second_mutex_domain(self) -> None:
        for case in ("missing_file", "replaced_file", "replaced_directory"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repository = make_repo(Path(directory) / "repo")
                lock_path = install_active_adoption_verification_binding(repository)
                descriptor = replace_bound_verification_mutex(lock_path, case)
                try:
                    with self.assertRaisesRegex(ValueError, "^E_VERIFICATION_LOCK:"):
                        with VerificationMutex(repository) as acquired:
                            self.assertFalse(acquired)
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    os.close(descriptor)

    def test_bound_runner_rejects_a_second_mutex_domain_before_execution(self) -> None:
        for case in ("missing_file", "replaced_file", "replaced_directory"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repository = runner_fixture(Path(directory))
                install_gate_sentinel(repository)
                lock_path = install_active_adoption_verification_binding(repository)
                descriptor = replace_bound_verification_mutex(lock_path, case)
                try:
                    completed = run_fixture_gate(repository)
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    os.close(descriptor)
                self.assert_stable_gate_error(completed, "E_TEST_MUTEX")

    def test_core_gate_rejects_local_clean_filter_before_git_sink(self) -> None:
        from tests import core_gate

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root / "repo")
            marker = root / "filter-executed"
            helper = root / "filter-helper.sh"
            helper.write_text(
                f"#!/bin/sh\n: > {shlex.quote(str(marker))}\ncat\n",
                encoding="utf-8",
            )
            helper.chmod(0o700)
            (repo / ".gitattributes").write_text(
                "README.md filter=core-gate-test\n", encoding="utf-8"
            )
            git(repo, "add", ".gitattributes")
            git(repo, "commit", "-qm", "filter fixture")
            git(repo, "config", "filter.core-gate-test.clean", str(helper))
            context = SimpleNamespace(
                python=SimpleNamespace(
                    path=Path(sys.executable),
                    realpath=Path(sys.executable).resolve(),
                ),
                node=SimpleNamespace(
                    path=Path("/usr/bin/true"),
                    realpath=Path("/usr/bin/true").resolve(),
                ),
                git=SimpleNamespace(path=Path("/usr/bin/git")),
                environment={},
            )
            commands: list[tuple[str, ...]] = []

            def successful_run(argv: object, **_: object) -> subprocess.CompletedProcess[bytes]:
                command = tuple(str(item) for item in argv)  # type: ignore[arg-type]
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with (
                patch.object(core_gate, "_activate_context"),
                patch.object(core_gate, "_run", side_effect=successful_run),
                patch.object(core_gate, "_run_cli", return_value={}),
            ):
                with self.assertRaisesRegex(ValueError, "E_GIT_FILTER"):
                    core_gate.run_gate(
                        repository=repo,
                        context=context,
                        test_names=(),
                        shell_paths=(),
                        revalidate_sources=lambda: None,
                        authoritative_git_environment=lambda _: {},
                    )

            self.assertFalse(marker.exists())
            self.assertFalse(
                any("diff" in command or "status" in command for command in commands)
            )

    def test_core_gate_rejects_local_textconv_before_git_sink(self) -> None:
        from tests import core_gate

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root / "repo")
            marker = root / "textconv-executed"
            helper = root / "textconv-helper.sh"
            helper.write_text(
                f"#!/bin/sh\n: > {shlex.quote(str(marker))}\ncat\n",
                encoding="utf-8",
            )
            helper.chmod(0o700)
            (repo / ".gitattributes").write_text(
                "README.md diff=core-gate-textconv\n", encoding="utf-8"
            )
            git(repo, "add", ".gitattributes")
            git(repo, "commit", "-qm", "textconv fixture")
            git(
                repo,
                "config",
                "diff.core-gate-textconv.textconv",
                str(helper),
            )
            context = SimpleNamespace(
                python=SimpleNamespace(
                    path=Path(sys.executable),
                    realpath=Path(sys.executable).resolve(),
                ),
                node=SimpleNamespace(
                    path=Path("/usr/bin/true"),
                    realpath=Path("/usr/bin/true").resolve(),
                ),
                git=SimpleNamespace(path=Path("/usr/bin/git")),
                environment={},
            )
            commands: list[tuple[str, ...]] = []

            def successful_run(argv: object, **_: object) -> subprocess.CompletedProcess[bytes]:
                command = tuple(str(item) for item in argv)  # type: ignore[arg-type]
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with (
                patch.object(core_gate, "_activate_context"),
                patch.object(core_gate, "_run", side_effect=successful_run),
                patch.object(core_gate, "_run_cli", return_value={}),
            ):
                with self.assertRaisesRegex(ValueError, "E_GIT_FILTER"):
                    core_gate.run_gate(
                        repository=repo,
                        context=context,
                        test_names=(),
                        shell_paths=(),
                        revalidate_sources=lambda: None,
                        authoritative_git_environment=lambda _: {},
                    )

            self.assertFalse(marker.exists())
            self.assertFalse(
                any("diff" in command or "status" in command for command in commands)
            )

    def test_core_gate_command_output_is_bounded_before_storage(self) -> None:
        from tests.core_gate import _run

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "E_TEST_OUTPUT"):
                _run(
                    [
                        sys.executable,
                        "-c",
                        "import sys;sys.stdout.buffer.write(b'x'*300000)",
                    ],
                    repository=repo,
                    environment={"PATH": "/usr/bin:/bin"},
                    timeout=2.0,
                )

    def test_core_gate_guards_each_git_sink_and_preserves_clean_checkout(self) -> None:
        from tests import core_gate

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            context = SimpleNamespace(
                python=SimpleNamespace(
                    path=Path(sys.executable),
                    realpath=Path(sys.executable).resolve(),
                ),
                node=SimpleNamespace(
                    path=Path("/usr/bin/true"),
                    realpath=Path("/usr/bin/true").resolve(),
                ),
                git=SimpleNamespace(path=Path("/usr/bin/git")),
                environment={},
            )
            completed = subprocess.CompletedProcess([], 0, b"", b"")
            with (
                patch.object(core_gate, "_activate_context"),
                patch.object(core_gate, "_run", return_value=completed) as run,
                patch.object(core_gate, "_run_cli", return_value={}),
                patch(
                    "control_plane.repository.assert_no_external_git_filters"
                ) as guard,
            ):
                result = core_gate.run_gate(
                    repository=repo,
                    context=context,
                    test_names=(),
                    shell_paths=(),
                    revalidate_sources=lambda: None,
                    authoritative_git_environment=lambda _: {},
                )

            self.assertEqual(result, 0)
            self.assertEqual(guard.call_count, 2)
            captured = [
                call.args[0]
                for call in run.mock_calls
                if call.args
                and "diff" in tuple(str(item) for item in call.args[0])
            ]
            self.assertEqual(len(captured), 1)
            self.assertIn("--no-ext-diff", captured[0])
            self.assertIn("--no-textconv", captured[0])

    def test_core_gate_timeout_kills_descendant_process_group(self) -> None:
        from tests.core_gate import _run

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            marker = repo / "descendant-survived"
            child = (
                "import pathlib,time;time.sleep(.4);"
                f"pathlib.Path({str(marker)!r}).write_text('bad')"
            )
            parent = (
                "import subprocess,sys,time;"
                f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
                "time.sleep(10)"
            )
            with self.assertRaisesRegex(RuntimeError, "E_TEST_TIMEOUT"):
                _run(
                    [sys.executable, "-c", parent],
                    repository=repo,
                    environment={"PATH": "/usr/bin:/bin"},
                    timeout=0.1,
                )
            time.sleep(0.6)
            self.assertFalse(marker.exists())

    def assert_stable_gate_error(
        self,
        completed: subprocess.CompletedProcess[str],
        error_code: str,
    ) -> None:
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            json.loads(completed.stderr),
            {
                "authorizes": False,
                "error_code": error_code,
                "executed": False,
                "status": "FAIL",
            },
        )

    def test_runner_ignores_site_pth_and_sitecustomize_before_verified_loader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = runner_fixture(root)
            install_gate_sentinel(repository)
            injected = root / "site-imported"
            attacker = root / "attacker"
            user_site = (
                attacker
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            )
            user_site.mkdir(parents=True)
            (attacker / "sitecustomize.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(injected)!r}).write_text('site', encoding='utf-8')\n",
                encoding="utf-8",
            )
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

            completed = run_fixture_gate(repository, environment=environment)

            self.assert_stable_gate_error(completed, "E_TEST_SENTINEL")
            self.assertFalse(injected.exists())

    def test_runner_ignores_timestamp_valid_repository_pyc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = runner_fixture(root)
            injected = root / "pyc-imported"
            install_gate_sentinel(repository, malicious_marker=injected)

            completed = run_fixture_gate(repository)

            self.assert_stable_gate_error(completed, "E_TEST_SENTINEL")
            self.assertFalse(injected.exists())

    def test_runner_shadow_failure_is_stable_and_executes_no_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = runner_fixture(Path(directory))
            (repository / "tests" / "core_gate").mkdir()

            completed = run_fixture_gate(repository)

            self.assert_stable_gate_error(completed, "E_TEST_SOURCE")

    def test_runner_rejects_undeclared_root_from_import_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = runner_fixture(root)
            marker = root / "gate-executed"
            bootstrap_marker = root / "toolchain-imported"
            toolchain = repository / "control_plane" / "toolchain.py"
            toolchain_source = toolchain.read_text(encoding="utf-8")
            toolchain.write_text(
                toolchain_source.replace(
                    "from __future__ import annotations\n",
                    "from __future__ import annotations\n"
                    "from pathlib import Path as _SentinelPath\n"
                    f"_SentinelPath({str(bootstrap_marker)!r}).write_text("
                    "'imported', encoding='utf-8')\n",
                    1,
                ),
                encoding="utf-8",
            )
            source = (
                "from pathlib import Path\n"
                "from control_plane import undeclared_origin\n"
                f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
            )
            (repository / "tests" / "core_gate.py").write_text(
                source,
                encoding="utf-8",
            )

            completed = run_fixture_gate(repository)

            self.assert_stable_gate_error(completed, "E_TEST_IMPORT")
            self.assertFalse(marker.exists())
            self.assertFalse(bootstrap_marker.exists())

    def test_second_verifier_is_busy_and_executes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            context = multiprocessing.get_context("fork")
            ready: multiprocessing.Queue = context.Queue()
            process = context.Process(target=hold_mutex, args=(str(repo), ready))
            process.start()
            self.assertTrue(ready.get(timeout=5))
            calls: list[str] = []
            result = run_serialized_verification(repo, lambda: calls.append("ran"))
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(result.status, "UNKNOWN")
            self.assertEqual(result.error_code, "E_VERIFICATION_BUSY")
            self.assertEqual(calls, [])
            self.assertFalse(result.consumes_reframe)
            self.assertFalse(result.authorizes)

    def test_full_gate_runner_busy_executes_no_first_command(self) -> None:
        with VerificationMutex(ROOT) as acquired:
            busy = subprocess.run(
                ["/bin/sh", str(ROOT / "tests" / "run.sh")],
                cwd=ROOT,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )

        self.assertEqual(busy.returncode, 2, busy.stderr)
        self.assertEqual(busy.stderr, "")
        self.assertEqual(
            json.loads(busy.stdout),
            {
                "authorizes": False,
                "consumes_reframe": False,
                "error_code": "E_VERIFICATION_BUSY",
                "executed": False,
                "status": "UNKNOWN",
            },
        )

    def test_busy_runner_locates_mutex_without_invoking_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "repo"
            tests = repository / "tests"
            tests.mkdir(parents=True)
            shutil.copy2(ROOT / "tests" / "run.sh", tests / "run.sh")
            locks = repository / ".git" / "codex-control-plane-core" / "locks"
            locks.mkdir(parents=True, mode=0o700)
            locks.parent.chmod(0o700)
            lock_path = locks / "verification.lock"
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            os.fchmod(descriptor, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                busy = subprocess.run(
                    ["/bin/sh", str(tests / "run.sh")],
                    cwd=repository,
                    env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                    stdin=subprocess.DEVNULL,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

        self.assertEqual(busy.returncode, 2, busy.stderr)
        self.assertEqual(busy.stderr, "")
        self.assertEqual(
            json.loads(busy.stdout),
            {
                "authorizes": False,
                "consumes_reframe": False,
                "error_code": "E_VERIFICATION_BUSY",
                "executed": False,
                "status": "UNKNOWN",
            },
        )

    def test_removed_internal_mode_cannot_be_forged(self) -> None:
        environment = os.environ.copy()
        environment["CONTROL_PLANE_VERIFICATION_MUTEX_HELD"] = "1"
        completed = subprocess.run(
            [
                "/bin/sh",
                str(ROOT / "tests" / "run.sh"),
                "--inside-verification-mutex",
            ],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("E_TEST_USAGE", completed.stderr)


if __name__ == "__main__":
    unittest.main()
