from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from control_plane.core_types import observe_current_worktree
from tests.test_core_task_state import make_repo


class CoreHookTests(unittest.TestCase):
    def test_safe_read_python_fallback_is_fully_isolated(self) -> None:
        from control_plane.hooks import _safe_read_regex_fallback_command

        command = _safe_read_regex_fallback_command(("token",), "README.md")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(
            command[1:6],
            ["-I", "-S", "-B", "-X", "pycache_prefix=/dev/null"],
        )

    def test_safe_read_accepts_closed_git_and_rejects_shell_or_path_escape(self) -> None:
        from control_plane.hooks import execute_safe_read

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            result = execute_safe_read(
                ("git", "status", "--short"),
                root=repo,
                worktree_inventory=observe_current_worktree(repo),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            rejected = execute_safe_read(
                ("git", "status", "--short;touch", "outside"),
                root=repo,
                worktree_inventory=observe_current_worktree(repo),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(rejected.status, "rejected")
        self.assertIsNone(rejected.exit_code)

    def test_safe_read_output_is_bounded_and_inventory_is_one_shot(self) -> None:
        from control_plane.hooks import execute_safe_read

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            (repo / "large.txt").write_text("x" * 16_384, encoding="utf-8")
            inventory = observe_current_worktree(repo)
            result = execute_safe_read(
                ("rg", "--no-config", "--quiet", "-e", "x", "--", "large.txt"),
                root=repo,
                worktree_inventory=inventory,
                timeout_seconds=2,
                output_limit_bytes=128,
            )
            with self.assertRaisesRegex(ValueError, "E_SAFE_READ_INVENTORY"):
                execute_safe_read(
                    ("git", "status", "--short"),
                    root=repo,
                    worktree_inventory=inventory,
                    timeout_seconds=2,
                    output_limit_bytes=128,
                )

        self.assertIn(result.status, {"completed", "truncated"})
        self.assertLessEqual(result.stdout_bytes, 128)
        self.assertLessEqual(result.stderr_bytes, 128)

    def test_hook_input_is_bounded_and_never_authorizes(self) -> None:
        from control_plane.hooks import MAX_INPUT_BYTES, run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            rendered = run_hook(
                json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "source": "compact",
                        "session_id": "session-core",
                        "cwd": str(repo),
                    },
                    separators=(",", ":"),
                ).encode(),
                expected_root=repo,
            )
            output = json.loads(rendered)

        self.assertNotIn("authorizes", output)
        self.assertLessEqual(len(rendered.encode()), 4096)
        with self.assertRaisesRegex(ValueError, "E_HOOK_INPUT_LIMIT"):
            run_hook(b"x" * (MAX_INPUT_BYTES + 1))


if __name__ == "__main__":
    unittest.main()
