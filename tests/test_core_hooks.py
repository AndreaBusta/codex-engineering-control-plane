from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest import mock

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
            for index in range(64):
                (repo / f"untracked-{index:03d}.txt").write_text(
                    "x\n", encoding="utf-8"
                )
            inventory = observe_current_worktree(repo)
            result = execute_safe_read(
                ("git", "status", "--short"),
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

    def test_hook_rejects_excessive_nesting_before_json_decode(self) -> None:
        from control_plane.hooks import run_hook

        raw = b'{"hook_event_name":"Unknown","tool_input":' + b"[" * 256
        raw += b"0" + b"]" * 256 + b"}"
        decoded = {
            "hook_event_name": "Unknown",
            "tool_input": [],
        }
        with mock.patch(
            "control_plane.hooks.json.loads", return_value=decoded
        ) as decoder:
            with self.assertRaisesRegex(ValueError, "E_HOOK_INPUT"):
                run_hook(raw)

        decoder.assert_not_called()

    def test_hook_nesting_scan_ignores_brackets_inside_strings(self) -> None:
        from control_plane.hooks import run_hook

        rendered = run_hook(
            json.dumps(
                {
                    "hook_event_name": "Unknown",
                    "tool_input": "[" * 256 + "]" * 256,
                },
                separators=(",", ":"),
            ).encode()
        )

        self.assertEqual(rendered, "")

    def _assert_branch_deletion_commands_are_denied_by_default(
        self, commands: tuple[str, ...]
    ) -> None:
        from control_plane.hooks import run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)
                for command in commands:
                    with self.subTest(command=command):
                        rendered = run_hook(
                            json.dumps(
                                {
                                    "hook_event_name": "PreToolUse",
                                    "cwd": str(repo),
                                    "tool_name": "Bash",
                                    "tool_input": {"command": command},
                                },
                                separators=(",", ":"),
                            ).encode(),
                            expected_root=repo,
                        )
                        output = json.loads(rendered)["hookSpecificOutput"]

                        self.assertEqual(
                            output.get("permissionDecision"), "deny"
                        )
                        self.assertEqual(
                            output.get("permissionDecisionReason"),
                            "CONTROL_PLANE_SOFT_ENFORCE: "
                            "destructive_command_requires_explicit_authority",
                        )

    def test_branch_deletion_commands_are_denied_by_default(self) -> None:
        self._assert_branch_deletion_commands_are_denied_by_default(
            (
                "git branch -d feature/old",
                "git branch -D feature/old",
                "git push --delete origin feature/old",
                "git push origin :refs/heads/feature/old",
            )
        )

    def test_quoted_branch_deletion_commands_are_denied_by_default(self) -> None:
        self._assert_branch_deletion_commands_are_denied_by_default(
            (
                "git branch -d 'feature/old'",
                'git branch -D "feature/old"',
                "git push --delete origin 'feature/old'",
                "git push origin ':refs/heads/feature/old'",
            )
        )

    def test_explicit_audit_keeps_branch_deletion_advisory(self) -> None:
        from control_plane.hooks import run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            with mock.patch.dict(
                os.environ,
                {"CODEX_CONTROL_PLANE_HOOK_MODE": "audit"},
                clear=False,
            ):
                rendered = run_hook(
                    json.dumps(
                        {
                            "hook_event_name": "PreToolUse",
                            "cwd": str(repo),
                            "tool_name": "Bash",
                            "tool_input": {
                                "command": "git branch -D feature/old"
                            },
                        },
                        separators=(",", ":"),
                    ).encode(),
                    expected_root=repo,
                )

        output = json.loads(rendered)["hookSpecificOutput"]
        self.assertIn("CONTROL PLANE RISK", output["additionalContext"])
        self.assertNotIn("permissionDecision", output)

    def test_invalid_hook_modes_fail_closed_to_soft_enforce(self) -> None:
        from control_plane.hooks import run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            payload = json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "cwd": str(repo),
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": "git branch -D feature/old"
                    },
                },
                separators=(",", ":"),
            ).encode()
            for mode in ("", "typo", "SOFT-ENFORCE"):
                with self.subTest(mode=mode), mock.patch.dict(
                    os.environ,
                    {"CODEX_CONTROL_PLANE_HOOK_MODE": mode},
                    clear=False,
                ):
                    output = json.loads(
                        run_hook(payload, expected_root=repo)
                    )["hookSpecificOutput"]

                    self.assertEqual(
                        output.get("permissionDecision"), "deny"
                    )
                    self.assertEqual(
                        output.get("permissionDecisionReason"),
                        "CONTROL_PLANE_SOFT_ENFORCE: "
                        "destructive_command_requires_explicit_authority",
                    )

    def test_closed_safe_read_rg_pattern_is_not_destructive(self) -> None:
        from control_plane.hooks import run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            pattern = "needle git branch -D feature/old marker"
            command = (
                "scripts/control-plane safe-read --repo "
                f"{shlex.quote(str(repo))} -- rg --no-config --quiet -e "
                f"{shlex.quote(pattern)} -- README.md"
            )
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)
                rendered = run_hook(
                    json.dumps(
                        {
                            "hook_event_name": "PreToolUse",
                            "cwd": str(repo),
                            "tool_name": "Bash",
                            "tool_input": {"command": command},
                        },
                        separators=(",", ":"),
                    ).encode(),
                    expected_root=repo,
                )

        self.assertEqual(rendered, "")

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
