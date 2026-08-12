from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from control_plane.toolchain import (
    _MAX_VERSION_BYTES,
    _probe,
    authoritative_git_environment,
    build_closed_execution_context,
)
import control_plane.toolchain as toolchain
from tests.test_core_task_state import make_repo


class CoreToolchainTests(unittest.TestCase):
    def test_closed_context_rejects_preexisting_gitconfig_without_rewriting_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root / "repo")
            temp_root = root / "closed"
            temp_root.mkdir(mode=0o700)
            config = temp_root / "gitconfig"
            payload = b"[alias]\nstatus = !touch should-not-run\n"
            config.write_bytes(payload)
            config.chmod(0o600)

            with self.assertRaisesRegex(ValueError, "E_TOOLCHAIN_TEMP"):
                build_closed_execution_context(repo, temp_root=temp_root)

            self.assertEqual(config.read_bytes(), payload)

    def test_closed_context_revalidation_rejects_gitconfig_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root / "repo")
            context = build_closed_execution_context(
                repo, temp_root=root / "closed"
            )
            config = context.temp_root / "gitconfig"
            config.write_text("[alias]\nstatus = !false\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "E_TOOLCHAIN_DRIFT"):
                context.validate_executables()

    def test_closed_context_ignores_ambient_path_and_probes_nested_node(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            temp_root = Path(directory) / "closed"
            hostile = {
                "PATH": str(Path(directory) / "fake-bin"),
                "GIT_CONFIG_GLOBAL": str(Path(directory) / "fake-gitconfig"),
                "NODE_OPTIONS": "--require=/does/not/exist",
                "PYTHONPATH": str(Path(directory) / "shadow"),
            }
            with mock.patch.dict(os.environ, hostile, clear=False):
                context = build_closed_execution_context(repo, temp_root=temp_root)
            self.assertTrue(context.python.path.is_absolute())
            self.assertTrue(context.git.path.is_absolute())
            self.assertIsNotNone(context.node)
            assert context.node is not None
            self.assertIn(context.node.path.parent.as_posix(), context.environment["PATH"])
            self.assertNotIn("fake-bin", context.environment["PATH"])
            self.assertNotIn("NODE_OPTIONS", context.environment)
            self.assertTrue(context.probes["node_nested"])
            self.assertTrue(context.probes["python_no_site"])
            self.assertTrue(context.probes["python_no_preloaded_core"])
            self.assertNotIn("GIT_NO_REPLACE_OBJECTS", context.environment)
            self.assertEqual(
                authoritative_git_environment(context)["GIT_NO_REPLACE_OBJECTS"],
                "1",
            )

    def test_probe_stops_at_max_plus_one_before_child_exit(self) -> None:
        script = (
            "import os,time;"
            f"os.write(1,b'x'*({_MAX_VERSION_BYTES}+1));"
            "time.sleep(2)"
        )
        started = time.monotonic()
        with mock.patch.object(toolchain, "_PROBE_TIMEOUT_SECONDS", 3.0):
            with self.assertRaisesRegex(ValueError, "E_TOOLCHAIN_FIXTURE"):
                _probe(
                    Path(sys.executable),
                    ("-c", script),
                    environment={"PATH": "/usr/bin:/bin"},
                    name="fixture",
                )
        self.assertLess(time.monotonic() - started, 1.0)

    def test_probe_timeout_bounds_infinite_output(self) -> None:
        script = "import os\nwhile True: os.write(1,b'x'*65536)"
        started = time.monotonic()
        with mock.patch.object(toolchain, "_PROBE_TIMEOUT_SECONDS", 0.1):
            with self.assertRaisesRegex(ValueError, "E_TOOLCHAIN_FIXTURE"):
                _probe(
                    Path(sys.executable),
                    ("-c", script),
                    environment={"PATH": "/usr/bin:/bin"},
                    name="fixture",
                )
        self.assertLess(time.monotonic() - started, 1.0)

    def test_probe_deadline_applies_after_stdout_closes(self) -> None:
        script = "import os,time;os.close(1);os.close(2);time.sleep(.5)"
        started = time.monotonic()
        with mock.patch.object(toolchain, "_PROBE_TIMEOUT_SECONDS", 0.05):
            with self.assertRaisesRegex(ValueError, "E_TOOLCHAIN_FIXTURE"):
                _probe(
                    Path(sys.executable),
                    ("-c", script),
                    environment={"PATH": "/usr/bin:/bin"},
                    name="fixture",
                )
        self.assertLess(time.monotonic() - started, 0.4)

    def test_probe_reaps_descendant_after_leader_exits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "descendant-survived"
            child = (
                "import pathlib,time;time.sleep(.4);"
                f"pathlib.Path({str(marker)!r}).write_text('survived')"
            )
            leader = (
                "import subprocess,sys;"
                f"subprocess.Popen([sys.executable,'-c',{child!r}]);"
                "print('v-fixture')"
            )

            self.assertEqual(
                _probe(
                    Path(sys.executable),
                    ("-c", leader),
                    environment={"PATH": "/usr/bin:/bin"},
                    name="fixture",
                ),
                "v-fixture",
            )
            time.sleep(0.6)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
