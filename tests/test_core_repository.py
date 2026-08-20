from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.test_core_task_state import git, make_repo


class CoreRepositoryTests(unittest.TestCase):
    def test_public_bounded_git_runner_executes_with_closed_output(self) -> None:
        from control_plane.repository import run_bounded_git

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo").resolve()
            completed = run_bounded_git(
                repo,
                ("status", "--porcelain", "-uno"),
                output_limit=4_096,
                timeout=2.0,
            )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, b"")
        self.assertLessEqual(len(completed.stdout) + len(completed.stderr), 4_096)

    def test_public_bounded_git_runner_rejects_mutating_commands(self) -> None:
        from control_plane.repository import run_bounded_git

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            with self.assertRaisesRegex(ValueError, "E_GIT_OBSERVATION"):
                run_bounded_git(
                    repo,
                    ("reset", "--hard", "HEAD"),
                    output_limit=4_096,
                    timeout=2.0,
                )

    def test_public_bounded_git_runner_accepts_required_exact_read_forms(self) -> None:
        from control_plane.repository import run_bounded_git

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo").resolve()
            try:
                git_directory = run_bounded_git(
                    repo,
                    ("rev-parse", "--absolute-git-dir"),
                    output_limit=4_096,
                    timeout=2.0,
                )
                empty_diff = run_bounded_git(
                    repo,
                    ("diff", "--name-only", "HEAD..HEAD", "--"),
                    output_limit=4_096,
                    timeout=2.0,
                )
            except ValueError as error:
                self.fail(f"required read-only form was rejected: {error}")

        self.assertEqual(git_directory.returncode, 0)
        self.assertTrue(git_directory.stdout.strip().endswith(b"/.git"))
        self.assertEqual(empty_diff.returncode, 0)
        self.assertEqual(empty_diff.stdout, b"")

    def test_public_bounded_git_runner_rejects_option_bypasses(self) -> None:
        from control_plane.repository import run_bounded_git

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo").resolve()
            output = Path(directory) / "stash-output"
            for arguments in (
                ("diff", "--ext-diff"),
                ("stash", "list", f"--output={output}"),
                ("diff", "--no-index", "outside-a", "outside-b"),
            ):
                with self.subTest(arguments=arguments):
                    with self.assertRaisesRegex(ValueError, "E_GIT_OBSERVATION"):
                        run_bounded_git(
                            repo,
                            arguments,
                            output_limit=4_096,
                            timeout=2.0,
                        )
            self.assertFalse(output.exists())

    def test_canonical_directory_observer_rejects_intermediate_symlink(self) -> None:
        from control_plane.repository import observed_directory

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            real = root / "real"
            real.mkdir()
            bridge = root / "bridge"
            bridge.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "E_DIRECTORY_OBSERVATION"):
                with observed_directory(bridge):
                    self.fail("a symlinked directory was accepted")

    def test_canonical_directory_observer_allows_root_owned_sticky_temp(self) -> None:
        from control_plane.repository import observed_directory

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            with observed_directory(root) as opened:
                observed, descriptor, _ = opened
                self.assertEqual(observed, root)
                self.assertGreaterEqual(descriptor, 0)

    def test_root_owned_sticky_ancestor_ignores_unrelated_entry_churn(self) -> None:
        from control_plane import repository as repository_module
        from control_plane.repository import observed_directory

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            noise = Path("/private/tmp") / f"codex-observer-noise-{os.getpid()}"
            real_open = repository_module.os.open
            mutated = False

            def noisy_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal mutated
                if path == "tmp" and not mutated:
                    mutated = True
                    noise.write_bytes(b"unrelated\n")
                return real_open(path, flags, mode, dir_fd=dir_fd)

            try:
                with patch.object(repository_module.os, "open", side_effect=noisy_open):
                    with observed_directory(root) as opened:
                        self.assertEqual(opened[0], root)
            finally:
                noise.unlink(missing_ok=True)

    def test_canonical_directory_observer_rejects_user_owned_sticky_parent(self) -> None:
        from control_plane.repository import observed_directory

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            writable = root / "writable"
            writable.mkdir(mode=0o700)
            child = writable / "child"
            child.mkdir(mode=0o700)
            writable.chmod(0o1777)

            with self.assertRaisesRegex(ValueError, "E_DIRECTORY_OBSERVATION"):
                with observed_directory(child):
                    self.fail("a user-owned writable ancestor was accepted")

    def test_bounded_git_runner_is_bound_to_the_observed_directory(self) -> None:
        from control_plane import repository as repository_module
        from control_plane.repository import run_bounded_git

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / "active"
            active.mkdir()
            original = make_repo(active / "repo").resolve()
            replacement_parent = root / "replacement"
            replacement_parent.mkdir()
            replacement = make_repo(replacement_parent / "repo").resolve()
            (replacement / "README.md").write_text(
                "replacement\n", encoding="utf-8"
            )
            git(replacement, "commit", "-qam", "replacement")
            expected = (git(original, "rev-parse", "HEAD") + "\n").encode()
            displaced = root / "displaced"
            real_probe = repository_module._bounded_filter_probe

            def swap_ancestor(*args, **kwargs):
                active.rename(displaced)
                replacement_parent.rename(active)
                try:
                    return real_probe(*args, **kwargs)
                finally:
                    active.rename(replacement_parent)
                    displaced.rename(active)

            with patch.object(
                repository_module,
                "_bounded_filter_probe",
                side_effect=swap_ancestor,
            ):
                completed = run_bounded_git(
                    original,
                    ("rev-parse", "HEAD"),
                    output_limit=4_096,
                    timeout=2.0,
                )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, expected)

    def test_bounded_git_wrapper_is_isolated_and_disables_bytecode(self) -> None:
        from control_plane import repository as repository_module
        from control_plane.repository import run_bounded_git

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo").resolve()
            completed = subprocess.CompletedProcess([], 0, b"", b"")
            with patch.object(
                repository_module,
                "_bounded_filter_probe",
                return_value=completed,
            ) as probe:
                run_bounded_git(
                    repo,
                    ("rev-parse", "HEAD"),
                    output_limit=4_096,
                    timeout=2.0,
                )

        wrapper = probe.call_args.args[0]
        self.assertEqual(wrapper[:5], ["/usr/bin/python3", "-I", "-S", "-B", "-c"])
        self.assertEqual(probe.call_args.kwargs["cwd"], Path("/"))
        self.assertEqual(len(probe.call_args.kwargs["pass_fds"]), 1)

    def test_bounded_regular_file_reader_is_no_follow_owned_and_capped(self) -> None:
        from control_plane import repository as repository_module

        self.assertTrue(
            hasattr(repository_module, "read_bounded_regular_file"),
            "the bounded regular-file reader is missing",
        )
        reader = repository_module.read_bounded_regular_file
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            control = root / "gitdir"
            control.write_bytes(b"worktree-pointer\n")
            self.assertEqual(
                reader(control, output_limit=4_096), b"worktree-pointer\n"
            )

            linked = root / "linked"
            linked.symlink_to(control)
            hardlinked = root / "hardlinked"
            os.link(control, hardlinked)
            oversized = root / "oversized"
            oversized.write_bytes(b"12345")
            writable = root / "writable"
            writable.write_bytes(b"unsafe")
            writable.chmod(0o666)
            for unsafe, limit in (
                (linked, 4_096),
                (hardlinked, 4_096),
                (oversized, 4),
                (writable, 4_096),
            ):
                with self.subTest(path=unsafe.name):
                    with self.assertRaisesRegex(ValueError, "E_BOUNDED_FILE"):
                        reader(unsafe, output_limit=limit)

    def test_discovery_ignores_ambient_redirects_and_observes_exact_git_dirs(self) -> None:
        from control_plane.repository import (
            discover_repository,
            git_common_dir,
            worktree_git_dir,
        )

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repo = make_repo(base / "repo")
            outside = base / "outside"
            outside.mkdir()
            with patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(outside),
                    "GIT_WORK_TREE": str(outside),
                    "GIT_CONFIG_GLOBAL": str(base / "hostile-config"),
                },
                clear=False,
            ):
                self.assertEqual(discover_repository(repo), repo.resolve())
                self.assertEqual(worktree_git_dir(repo), (repo / ".git").resolve())
                self.assertEqual(git_common_dir(repo), (repo / ".git").resolve())

    def test_trusted_git_argv_closes_config_and_diff_drivers(self) -> None:
        from control_plane.repository import trusted_git_argv, trusted_git_environment

        command = trusted_git_argv(Path("/repo"), ("diff", "--check"))
        self.assertEqual(command[0], "/usr/bin/git")
        self.assertIn("core.hooksPath=/dev/null", command)
        self.assertIn("--no-ext-diff", command)
        self.assertIn("--no-textconv", command)
        environment = trusted_git_environment()
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertNotIn("GIT_DIR", environment)

    def test_external_git_guard_rejects_textconv_without_executing_it(self) -> None:
        from control_plane.repository import assert_no_external_git_filters

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
                "README.md diff=core-textconv\n", encoding="utf-8"
            )
            git(repo, "add", ".gitattributes")
            git(repo, "commit", "-qm", "textconv fixture")
            git(repo, "config", "diff.core-textconv.textconv", str(helper))

            with self.assertRaisesRegex(ValueError, "E_GIT_FILTER"):
                assert_no_external_git_filters(repo)

            self.assertFalse(marker.exists())

    def test_filter_probe_rejects_output_before_storing_beyond_cap(self) -> None:
        from control_plane.repository import _bounded_filter_probe

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "E_GIT_FILTER"):
                _bounded_filter_probe(
                    [
                        sys.executable,
                        "-c",
                        "import sys;sys.stdout.buffer.write(b'x'*5000000)",
                    ],
                    cwd=Path(directory),
                    environment={"PATH": "/usr/bin:/bin"},
                    input_data=None,
                    output_limit=4_096,
                    timeout=2.0,
                )

    def test_filter_probe_timeout_kills_descendants_without_path_leak(self) -> None:
        from control_plane.repository import _bounded_filter_probe

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "descendant-survived"
            child = (
                "import pathlib,time;time.sleep(.4);"
                f"pathlib.Path({str(marker)!r}).write_text('bad')"
            )
            parent = (
                "import subprocess,sys,time;"
                f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
                "time.sleep(10)"
            )
            with self.assertRaisesRegex(ValueError, "E_GIT_FILTER") as failure:
                _bounded_filter_probe(
                    [sys.executable, "-c", parent],
                    cwd=root,
                    environment={"PATH": "/usr/bin:/bin"},
                    input_data=None,
                    output_limit=4_096,
                    timeout=0.1,
                )
            self.assertNotIn(str(root), str(failure.exception))
            time.sleep(0.6)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
