from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.test_core_task_state import git, make_repo


class CoreRepositoryTests(unittest.TestCase):
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
