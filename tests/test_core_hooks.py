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

    def _assert_commands_are_denied_by_default(
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

    def _assert_commands_are_advisory_by_default(
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

                        self.assertNotIn("permissionDecision", output)
                        self.assertIn(
                            "CONTROL PLANE RISK", output["additionalContext"]
                        )

    def test_branch_deletion_commands_are_denied_by_default(self) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "git branch -d feature/old",
                "git branch -D feature/old",
                "git push --delete origin feature/old",
                "git push origin :refs/heads/feature/old",
            )
        )

    def test_quoted_branch_deletion_commands_are_denied_by_default(self) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "git branch -d 'feature/old'",
                'git branch -D "feature/old"',
                "git push --delete origin 'feature/old'",
                "git push origin ':refs/heads/feature/old'",
            )
        )

    def test_destructive_commands_in_top_level_compounds_are_denied(self) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "make build && git reset --hard HEAD",
                "git status --short || git clean -fd",
                "echo ready; git branch -D feature/old",
                "printf payload | rm -rf build",
            )
        )

    def test_dynamic_segment_cannot_mask_static_destructive_segment(
        self,
    ) -> None:
        commands: list[str] = []
        for operator in ("&&", "||", ";", "|"):
            commands.extend(
                (
                    "printf '%s\\n' \"$(id)\" "
                    f"{operator} git reset --hard HEAD",
                    "git reset --hard HEAD "
                    f"{operator} printf '%s\\n' \"$(id)\"",
                )
            )
        commands.extend(
            (
                "$'printf' '%s\\n' safe && git reset --hard HEAD",
                "git reset --hard HEAD && $'printf' '%s\\n' safe",
                "$'opaque\\'value' && git reset --hard HEAD",
                "git reset --hard HEAD && $'opaque\\'value'",
            )
        )
        self._assert_commands_are_denied_by_default(tuple(commands))

    def test_static_quote_concatenation_in_destructive_tokens_is_denied(
        self,
    ) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "g'it' reset --hard HEAD",
                "git res'et' --hard HEAD",
                "git reset --ha'rd' HEAD",
                "'git' reset --hard HEAD",
            )
        )

    def test_adjacent_posix_quote_fragments_are_classified_as_one_token(
        self,
    ) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "'g''it' reset --hard HEAD",
                '"g"\'it\' reset --hard HEAD',
                "git 'res''et' --hard HEAD",
                "git reset '--ha''rd' HEAD",
            )
        )

    def test_ansi_c_quotation_is_advisory_unless_a_static_segment_denies(
        self,
    ) -> None:
        from control_plane.hooks import run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            payload = json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "cwd": str(repo),
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": "$'git' reset --hard HEAD"
                    },
                },
                separators=(",", ":"),
            ).encode()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)
                advisory = json.loads(
                    run_hook(payload, expected_root=repo)
                )["hookSpecificOutput"]
            with mock.patch.dict(
                os.environ,
                {"CODEX_CONTROL_PLANE_HOOK_MODE": "enforce"},
                clear=False,
            ):
                enforced = json.loads(
                    run_hook(payload, expected_root=repo)
                )["hookSpecificOutput"]

        self.assertNotIn("permissionDecision", advisory)
        self.assertEqual(enforced.get("permissionDecision"), "deny")
        self.assertEqual(
            enforced.get("permissionDecisionReason"),
            "CONTROL_PLANE_SOFT_ENFORCE: ambiguous_shell_command",
        )

    def test_fully_quoted_destructive_text_is_advisory(self) -> None:
        self._assert_commands_are_advisory_by_default(
            (
                "'git reset --hard HEAD'",
                '"git reset --hard HEAD"',
                "printf '%s\\n' \"g'it' reset --hard HEAD\"",
                "printf '%s\\n' \"&\"'&' git reset --hard HEAD",
                "printf '%s\\n' \\&\\& git reset --hard HEAD",
            )
        )

    def test_destructive_push_spellings_are_denied(self) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "git push --force-with-lease=refs/heads/main:abc123 "
                "origin main",
                "git push -d origin feature/old",
                "git push origin -d feature/old",
                "git push --delete origin feature/old",
                "git push origin --delete feature/old",
                "git push origin +feature/work:refs/heads/feature/work",
                "git push origin :refs/heads/feature/old",
                "git push --no-force --force origin feature/work",
                "git push --no-force-with-lease "
                "--force-with-lease=refs/heads/main:abc123 origin main",
            )
        )

    def test_option_values_terminators_and_force_cancellation_are_advisory(
        self,
    ) -> None:
        self._assert_commands_are_advisory_by_default(
            (
                "git branch --format '-d' --list "
                "'__hook_review_no_match__'",
                "git branch --contains '-d' --list "
                "'__hook_review_no_match__'",
                "git branch --merged '--delete' --list "
                "'__hook_review_no_match__'",
                "git branch --set-upstream-to '-D' "
                "'__hook_review_no_match__'",
                "git branch -- -d",
                "git push -o '+source:destination' --dry-run "
                "origin feature/work",
                "git push origin --recurse-submodules "
                "'+source:destination' --dry-run feature/work",
                "git push --push-option=+source:destination "
                "origin feature/work",
                "git push --force-with-lease --no-force-with-lease "
                "origin feature/work",
                "git push --force-with-lease=refs/heads/main:abc123 "
                "--no-force-with-lease origin feature/work",
                "git push --force --no-force origin feature/work",
                "git branch -d --no-delete --list "
                "'__hook_review_no_match__'",
                "git push --delete --no-delete origin feature/work",
                "git push -- origin --force",
            )
        )

    def test_clean_branch_and_push_short_options_respect_roles(self) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "git clean -f",
                "git clean -fd",
                "git clean -df",
                "git clean -feKEEP",
                "git branch -vd feature/old",
                "git branch -rd origin/feature/old",
                "git push -fd origin feature/old",
                "git push origin -df feature/old",
                "git push --no-force --no-delete -fd "
                "origin feature/old",
            )
        )
        self._assert_commands_are_advisory_by_default(
            (
                "git clean -e '-f' --dry-run",
                "git clean --exclude '-f' --dry-run",
                "git clean -e-f --dry-run",
                "git clean -ne-f",
                "git clean -- -rf",
                "git branch -u '-d' --list "
                "'__hook_review_no_match__'",
                "git branch -- -vd",
                "git push -of --dry-run origin feature/work",
                "git push -o '-fd' --dry-run origin feature/work",
                "git push -fd --no-force --no-delete "
                "origin feature/work",
                "git push -- origin -fd",
            )
        )

    def test_leading_static_assignments_are_classified_but_wrappers_are_not(
        self,
    ) -> None:
        self._assert_commands_are_denied_by_default(
            (
                "HOOK_SCOPE=review git reset --hard HEAD",
                "HOOK_SCOPE=review OTHER='two words' git clean -fd",
                "printf ready && HOOK_SCOPE=review "
                "git branch -D feature/old",
                "HOOK_SCOPE=review git push origin "
                "+feature/work:refs/heads/feature/work",
            )
        )
        self._assert_commands_are_advisory_by_default(
            (
                "1INVALID=value git reset --hard HEAD",
                r"HOOK_SCOPE\=review git reset --hard HEAD",
                "'HOOK_SCOPE'=review git reset --hard HEAD",
                "env HOOK_SCOPE=review git reset --hard HEAD",
                "command git reset --hard HEAD",
                "eval 'git reset --hard HEAD'",
                "sh -c 'git reset --hard HEAD'",
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

    def test_closed_safe_read_quoted_search_text_is_silent(self) -> None:
        from control_plane.hooks import run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)
                for pattern in (
                    "needle && marker",
                    "needle git branch -D feature/old marker",
                    "needle --force-with-lease=refs/heads/main:abc123 marker",
                    "needle && git branch -D feature/old "
                    "--force-with-lease=refs/heads/main:abc123 marker",
                    "$(git reset --hard HEAD)",
                    "`git reset --hard HEAD`",
                    "<(id)",
                ):
                    with self.subTest(pattern=pattern):
                        command = (
                            "scripts/control-plane safe-read --repo "
                            f"{shlex.quote(str(repo))} -- "
                            "rg --no-config --quiet -e "
                            f"{shlex.quote(pattern)} -- README.md"
                        )
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

                command = (
                    "scripts/control-plane safe-read --repo "
                    f"{shlex.quote(str(repo))} -- "
                    "rg --no-config --quiet -e \"<(\" -- README.md"
                )
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

    def test_dynamic_safe_read_pattern_is_advisory_or_denied_by_mode(
        self,
    ) -> None:
        from control_plane.hooks import run_hook

        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            for pattern, shell_pattern in (
                (
                    "$(git reset --hard HEAD)",
                    '"$(git reset --hard HEAD)"',
                ),
                (
                    "`git reset --hard HEAD`",
                    '"`git reset --hard HEAD`"',
                ),
                ("<(id)", "<(id)"),
                (">(true)", ">(true)"),
            ):
                command = (
                    "scripts/control-plane safe-read --repo "
                    f"{shlex.quote(str(repo))} -- "
                    "rg --no-config --quiet -e "
                    f"{shell_pattern} -- README.md"
                )
                payload = json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "cwd": str(repo),
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    },
                    separators=(",", ":"),
                ).encode()
                with self.subTest(pattern=pattern, mode="soft-enforce"):
                    with mock.patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)
                        rendered = run_hook(payload, expected_root=repo)

                    self.assertNotEqual(rendered, "")
                    output = json.loads(rendered)["hookSpecificOutput"]
                    self.assertIn(
                        "CONTROL PLANE RISK", output["additionalContext"]
                    )
                    self.assertNotIn("permissionDecision", output)

                with self.subTest(pattern=pattern, mode="enforce"):
                    with mock.patch.dict(
                        os.environ,
                        {"CODEX_CONTROL_PLANE_HOOK_MODE": "enforce"},
                        clear=False,
                    ):
                        rendered = run_hook(payload, expected_root=repo)

                    self.assertNotEqual(rendered, "")
                    output = json.loads(rendered)["hookSpecificOutput"]
                    self.assertEqual(output.get("permissionDecision"), "deny")
                    self.assertEqual(
                        output.get("permissionDecisionReason"),
                        "CONTROL_PLANE_SOFT_ENFORCE: "
                        "ambiguous_shell_command",
                    )

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

    def test_soft_enforce_denies_only_destructive_commands(self) -> None:
        from control_plane.hooks import run_hook

        advisory_cases = (
            ("Bash", {"command": "git status --short"}),
            ("Bash", {"command": "git diff --stat"}),
            ("Bash", {"command": "rg pattern"}),
            ("Bash", {"command": "git push origin feature/work"}),
            (
                "Bash",
                {"command": "git push --no-force origin feature/work"},
            ),
            (
                "Bash",
                {
                    "command": (
                        "git push --no-force-with-lease "
                        "origin feature/work"
                    )
                },
            ),
            (
                "Bash",
                {
                    "command": (
                        "git push --force-if-includes origin feature/work"
                    )
                },
            ),
            ("Bash", {"command": "make build && make test"}),
            (
                "Bash",
                {"command": "echo 'git branch -D feature/old'"},
            ),
            (
                "Bash",
                {
                    "command": (
                        "printf '%s\\n' 'git branch -D feature/old && "
                        "git push --force-with-lease="
                        "refs/heads/main:abc123'"
                    )
                },
            ),
            (
                "Bash",
                {
                    "command": (
                        "echo 'git push --delete origin feature/old'"
                    )
                },
            ),
            (
                "Bash",
                {"command": "eval 'git reset --hard HEAD'"},
            ),
            (
                "Bash",
                {"command": "sh -c 'git reset --hard HEAD'"},
            ),
            ("Bash", {"command": "destructive_alias feature/old"}),
            ("Edit", {"file_path": "README.md"}),
            ("Write", {"file_path": "README.md"}),
            ("apply_patch", {"input": "*** Begin Patch"}),
            (
                "mcp__github__get_pull_request",
                {"owner": "example", "repo": "example"},
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)

                for tool_name, tool_input in advisory_cases:
                    with self.subTest(tool=tool_name, input=tool_input):
                        output = json.loads(
                            run_hook(
                                json.dumps(
                                    {
                                        "hook_event_name": "PreToolUse",
                                        "cwd": str(repo),
                                        "tool_name": tool_name,
                                        "tool_input": tool_input,
                                    },
                                    separators=(",", ":"),
                                ).encode(),
                                expected_root=repo,
                            )
                        )["hookSpecificOutput"]

                        self.assertNotIn("permissionDecision", output)

                destructive = json.loads(
                    run_hook(
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
                )["hookSpecificOutput"]

                self.assertEqual(destructive.get("permissionDecision"), "deny")
                self.assertEqual(
                    destructive.get("permissionDecisionReason"),
                    "CONTROL_PLANE_SOFT_ENFORCE: "
                    "destructive_command_requires_explicit_authority",
                )

    def test_explicit_enforce_denies_unattested_effects(self) -> None:
        from control_plane.hooks import run_hook

        cases = (
            (
                "Bash",
                {"command": "git status --short"},
                "raw_read_requires_safe_read",
            ),
            (
                "Bash",
                {"command": "eval 'git reset --hard HEAD'"},
                "unresolved_bash_effect",
            ),
            (
                "Bash",
                {"command": "sh -c 'git reset --hard HEAD'"},
                "unresolved_bash_effect",
            ),
            (
                "Edit",
                {"file_path": "README.md"},
                "pending_host_authorization_bridge",
            ),
            (
                "mcp__github__get_pull_request",
                {"owner": "example", "repo": "example"},
                "mcp_use_requires_task_authorization_and_egress_check",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            with mock.patch.dict(
                os.environ,
                {"CODEX_CONTROL_PLANE_HOOK_MODE": "enforce"},
                clear=False,
            ):
                for tool_name, tool_input, reason in cases:
                    with self.subTest(tool=tool_name):
                        output = json.loads(
                            run_hook(
                                json.dumps(
                                    {
                                        "hook_event_name": "PreToolUse",
                                        "cwd": str(repo),
                                        "tool_name": tool_name,
                                        "tool_input": tool_input,
                                    },
                                    separators=(",", ":"),
                                ).encode(),
                                expected_root=repo,
                            )
                        )["hookSpecificOutput"]

                        self.assertEqual(
                            output.get("permissionDecision"), "deny"
                        )
                        self.assertEqual(
                            output.get("permissionDecisionReason"),
                            "CONTROL_PLANE_SOFT_ENFORCE: " + reason,
                        )


if __name__ == "__main__":
    unittest.main()
