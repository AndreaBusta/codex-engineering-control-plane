from __future__ import annotations

import json
import fcntl
import multiprocessing
import os
from pathlib import Path
import py_compile
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from control_plane.verification import VerificationMutex, run_serialized_verification
from tests.test_core_task_state import git, make_repo


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
    shutil.copytree(ROOT / "tests", repository / "tests", ignore=ignored)
    (repository / "scripts").mkdir()
    for name in ("control-plane", "build-release-candidate"):
        shutil.copy2(ROOT / "scripts" / name, repository / "scripts" / name)
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


class CoreVerificationTests(unittest.TestCase):
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
