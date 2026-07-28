from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


class HookTests(unittest.TestCase):
    def payload(self, event: str, **overrides: object) -> dict:
        value = {
            "session_id": "session-test",
            "turn_id": "turn-test",
            "cwd": os.getcwd(),
            "hook_event_name": event,
            "permission_mode": "default",
        }
        value.update(overrides)
        return value

    def test_user_prompt_is_not_echoed_or_persisted(self) -> None:
        from control_plane.hooks import run_hook

        marker = "PROMPT-MUST-NOT-BE-RETAINED"
        output = run_hook(
            json.dumps(
                self.payload("UserPromptSubmit", prompt=marker)
            ).encode()
        )

        self.assertNotIn(marker, output)
        self.assertLessEqual(len(output.encode()), 4096)
        self.assertIn("selection-is-not-authorization", output)

    def test_session_start_rehydrates_only_compact_state(self) -> None:
        from control_plane.hooks import run_hook

        output = json.loads(
            run_hook(
                json.dumps(
                    self.payload("SessionStart", source="compact")
                ).encode()
            )
        )

        specific = output["hookSpecificOutput"]
        self.assertEqual(specific["hookEventName"], "SessionStart")
        self.assertIn(
            "frame-route-load-required-resources",
            specific["additionalContext"],
        )

    def test_invalid_environment_task_id_is_not_echoed_or_used_as_path(
        self,
    ) -> None:
        from control_plane.hooks import run_hook

        with patch.dict(
            os.environ,
            {"CODEX_CONTROL_PLANE_TASK_ID": "../../INJECTED\nSYSTEM"},
            clear=False,
        ):
            output = run_hook(
                json.dumps(
                    self.payload("UserPromptSubmit", prompt="safe")
                ).encode()
            )

        self.assertNotIn("INJECTED", output)
        self.assertIn("task=invalid", output)

    def test_passing_pretool_hook_is_silent(self) -> None:
        from control_plane.hooks import run_hook

        output = run_hook(
            json.dumps(
                self.payload(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_input={"command": "git status --short"},
                )
            ).encode()
        )

        self.assertEqual(output, "")

    def test_destructive_pretool_is_audit_warning_not_fake_authority(self) -> None:
        from control_plane.hooks import run_hook

        output = json.loads(
            run_hook(
                json.dumps(
                    self.payload(
                        "PreToolUse",
                        tool_name="Bash",
                        tool_input={"command": "git reset --hard HEAD"},
                    )
                ).encode()
            )
        )

        specific = output["hookSpecificOutput"]
        self.assertIn("requires_explicit_authority", specific["additionalContext"])
        self.assertNotIn("permissionDecision", specific)

    def test_stop_reentry_never_creates_continuation_loop(self) -> None:
        from control_plane.hooks import run_hook

        output = json.loads(
            run_hook(
                json.dumps(
                    self.payload("Stop", stop_hook_active=True)
                ).encode()
            )
        )

        self.assertTrue(output["continue"])
        self.assertNotIn("decision", output)

    def test_soft_enforce_can_block_curated_destructive_command(self) -> None:
        from control_plane.hooks import run_hook

        with patch.dict(
            os.environ,
            {"CODEX_CONTROL_PLANE_HOOK_MODE": "soft-enforce"},
            clear=False,
        ):
            output = json.loads(
                run_hook(
                    json.dumps(
                        self.payload(
                            "PreToolUse",
                            tool_name="Bash",
                            tool_input={"command": "git push --force origin main"},
                        )
                    ).encode()
                )
            )

        self.assertEqual(
            output["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_soft_enforce_warns_but_does_not_claim_mcp_authority(self) -> None:
        from control_plane.hooks import run_hook

        with patch.dict(
            os.environ,
            {"CODEX_CONTROL_PLANE_HOOK_MODE": "soft-enforce"},
            clear=False,
        ):
            output = json.loads(
                run_hook(
                    json.dumps(
                        self.payload(
                            "PreToolUse",
                            tool_name="mcp__github__get_pull_request",
                            tool_input={"owner": "example", "repo": "example"},
                        )
                    ).encode()
                )
            )

        specific = output["hookSpecificOutput"]
        self.assertIn("egress_check", specific["additionalContext"])
        self.assertNotIn("permissionDecision", specific)

    def test_oversized_input_fails_closed(self) -> None:
        from control_plane.hooks import MAX_INPUT_BYTES, run_hook

        with self.assertRaisesRegex(ValueError, "E_HOOK_INPUT_LIMIT"):
            run_hook(b"x" * (MAX_INPUT_BYTES + 1))

    def test_hook_config_is_audit_bounded_and_uses_git_root(self) -> None:
        config = json.loads(
            Path(".codex/hooks.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(config["hooks"]),
            {"UserPromptSubmit", "PreToolUse", "Stop", "SessionStart"},
        )
        self.assertEqual(config["hooks"]["SessionStart"][0]["matcher"], "compact")
        serialized = json.dumps(config)
        self.assertIn("git rev-parse --show-toplevel", serialized)
        self.assertNotIn("dangerously-bypass-hook-trust", serialized)
        for groups in config["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertLessEqual(hook["timeout"], 3)


if __name__ == "__main__":
    unittest.main()
