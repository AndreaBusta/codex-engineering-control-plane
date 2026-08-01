from __future__ import annotations

import json
import os
from hashlib import sha256
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.git_test_support import GitScenario, git


ROOT = Path(__file__).resolve().parents[1]


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

    def governing_policy(self, scenario: GitScenario, label: str):
        from control_plane.contracts import contract_digest
        from tests.host_adapter_test_support import governing_policy

        policy = scenario.policy()
        return governing_policy(
            policy=policy,
            policy_digest=contract_digest(policy),
            runtime_digest=contract_digest({"runtime": label}),
            lock_digest=contract_digest({"lock": label}),
            governing_base_commit="d" * 40,
            session_id=f"session-{label}",
            invocation_id=f"invocation-{label}",
            freshness_deadline=130.0,
        )

    def worktree_inventory(self, repo: Path, label: str):
        from control_plane.host_bridge import (
            observe_worktree_inventory,
            validate_worktree_inventory_observation,
        )

        common = Path(
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        ).resolve()
        observation = observe_worktree_inventory(
            canonical_common_git_dir=common,
            invocation_id=label,
            clock=lambda: 100.0,
            ttl_seconds=30,
            max_output_bytes=1_048_576,
        )
        return validate_worktree_inventory_observation(
            observation,
            expected_common_git_dir=common,
            expected_invocation_id=label,
            clock=lambda: 100.0,
        )

    def test_user_prompt_is_not_echoed_or_persisted(self) -> None:
        from control_plane.hooks import run_hook

        marker = "PROMPT-MUST-NOT-BE-RETAINED"
        scenario = GitScenario()
        try:
            output = run_hook(
                json.dumps(
                    self.payload(
                        "UserPromptSubmit",
                        cwd=str(scenario.repo),
                        session_id="session-no-prompt-retention",
                        prompt=marker,
                    )
                ).encode()
            )
        finally:
            scenario.close()

        self.assertNotIn(marker, output)
        self.assertLessEqual(len(output.encode()), 4096)
        self.assertIn("CONTROL PLANE RISK", output)

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
        self.assertIn("pending_framing", specific["additionalContext"])

    def test_invalid_environment_task_id_is_not_echoed_or_used_as_path(
        self,
    ) -> None:
        from control_plane.hooks import run_hook

        scenario = GitScenario()
        try:
            with patch.dict(
                os.environ,
                {"CODEX_CONTROL_PLANE_TASK_ID": "../../INJECTED\nSYSTEM"},
                clear=False,
            ):
                output = run_hook(
                    json.dumps(
                        self.payload(
                            "UserPromptSubmit",
                            cwd=str(scenario.repo),
                            session_id="session-invalid-task-id",
                            prompt="safe",
                        )
                    ).encode()
                )
        finally:
            scenario.close()

        self.assertNotIn("INJECTED", output)
        context = json.loads(output)["hookSpecificOutput"][
            "additionalContext"
        ]
        context_payload = json.loads(context)
        self.assertEqual(
            context_payload["framing_status"], "pending_framing"
        )

    def test_passing_pretool_hook_is_silent(self) -> None:
        from control_plane.hooks import run_hook

        output = run_hook(
            json.dumps(
                self.payload(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_input={
                        "command": (
                            "scripts/control-plane safe-read --repo "
                            f"{Path.cwd().resolve()} -- git status --short"
                        )
                    },
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
        self.assertIn("CONTROL PLANE RISK", specific["additionalContext"])
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
        self.assertIn("CONTROL PLANE RISK", specific["additionalContext"])
        self.assertNotIn("permissionDecision", specific)

    def test_raw_read_is_advisory_in_audit_and_denied_in_soft_enforce(
        self,
    ) -> None:
        from control_plane.hooks import run_hook

        encoded = json.dumps(
            self.payload(
                "PreToolUse",
                tool_name="Bash",
                tool_input={"command": "git status --short"},
            )
        ).encode()
        audit = json.loads(run_hook(encoded))
        with patch.dict(
            os.environ,
            {"CODEX_CONTROL_PLANE_HOOK_MODE": "soft-enforce"},
            clear=False,
        ):
            soft = json.loads(run_hook(encoded))

        self.assertIn(
            "CONTROL PLANE RISK",
            audit["hookSpecificOutput"]["additionalContext"],
        )
        self.assertNotIn(
            "permissionDecision", audit["hookSpecificOutput"]
        )
        self.assertEqual(
            soft["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        self.assertIn(
            "raw_read_requires_safe_read",
            soft["hookSpecificOutput"]["permissionDecisionReason"],
        )

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
        self.assertIn("/usr/bin/git rev-parse --show-toplevel", serialized)
        self.assertNotIn("dangerously-bypass-hook-trust", serialized)
        for groups in config["hooks"].values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertLessEqual(hook["timeout"], 3)

    def test_first_prompt_warns_second_identical_prompt_is_silent(self) -> None:
        from control_plane.hooks import run_hook

        scenario = GitScenario()
        try:
            encoded = json.dumps(
                self.payload(
                    "UserPromptSubmit",
                    cwd=str(scenario.repo),
                    session_id="session-warning-once",
                    prompt="first secret-shaped prompt",
                )
            ).encode()
            first = run_hook(encoded)
            second = run_hook(encoded)
        finally:
            scenario.close()

        self.assertIn("CONTROL PLANE RISK", first)
        self.assertEqual(second, "")

    def test_changed_risk_fingerprint_warns_again(self) -> None:
        from control_plane.hooks import risk_fingerprint, should_emit_warning

        scenario = GitScenario()
        try:
            first = risk_fingerprint(scenario.repo)
            self.assertTrue(
                should_emit_warning(
                    scenario.repo, "session-fingerprint", first
                )
            )
            self.assertFalse(
                should_emit_warning(
                    scenario.repo, "session-fingerprint", first
                )
            )
            scenario.checkout_feature()
            changed = risk_fingerprint(scenario.repo)
            self.assertNotEqual(first, changed)
            self.assertTrue(
                should_emit_warning(
                    scenario.repo, "session-fingerprint", changed
                )
            )
        finally:
            scenario.close()

    def test_session_state_uses_hashed_id_under_worktree_git_dir(self) -> None:
        from control_plane.hooks import warning_state_path
        from control_plane.repository import worktree_git_dir

        scenario = GitScenario()
        try:
            session_id = "session/raw/value"
            path = warning_state_path(scenario.repo, session_id)
            expected_name = sha256(session_id.encode("utf-8")).hexdigest()

            self.assertEqual(path.name, f"{expected_name}.json")
            self.assertNotIn(session_id, str(path))
            self.assertEqual(
                path.parent,
                worktree_git_dir(scenario.repo)
                / "codex-control-plane"
                / "warnings",
            )
        finally:
            scenario.close()

    def test_warning_state_never_contains_prompt_or_command(self) -> None:
        from control_plane.hooks import should_emit_warning, warning_state_path

        scenario = GitScenario()
        try:
            marker = "PROMPT-OR-COMMAND-MUST-NOT-PERSIST"
            fingerprint = f"sha256:{sha256(marker.encode()).hexdigest()}"
            self.assertTrue(
                should_emit_warning(scenario.repo, "session-closed", fingerprint)
            )
            path = warning_state_path(scenario.repo, "session-closed")
            state = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(
                set(state),
                {"schema_version", "fingerprint", "emitted_at"},
            )
            self.assertNotIn(marker, path.read_text(encoding="utf-8"))
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode),
                0o600,
            )
        finally:
            scenario.close()

    def test_unwritable_or_invalid_state_returns_unknown_warning(self) -> None:
        from control_plane.hooks import run_hook

        scenario = GitScenario()
        try:
            with patch(
                "control_plane.hooks.warning_state_path",
                side_effect=OSError("unwritable"),
            ):
                output = json.loads(
                    run_hook(
                        json.dumps(
                            self.payload(
                                "UserPromptSubmit",
                                cwd=str(scenario.repo),
                                session_id="session-invalid-state",
                            )
                        ).encode()
                    )
                )
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn('"action":"PAUSE_AND_VERIFY"', context)
            self.assertIn('"local":"UNKNOWN"', context)
            self.assertNotIn("Traceback", context)
            self.assertLessEqual(
                len(json.dumps(output).encode("utf-8")), 4096
            )
        finally:
            scenario.close()

    def test_warning_state_rejects_ancestor_symlink_escape(self) -> None:
        from control_plane.hooks import run_hook
        from control_plane.repository import worktree_git_dir

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                outside = Path(temporary) / "outside"
                outside.mkdir()
                state_root = (
                    worktree_git_dir(scenario.repo) / "codex-control-plane"
                )
                state_root.symlink_to(outside, target_is_directory=True)
                output = run_hook(
                    json.dumps(
                        self.payload(
                            "UserPromptSubmit",
                            cwd=str(scenario.repo),
                            session_id="session-ancestor-symlink",
                        )
                    ).encode("utf-8")
                )

                self.assertIn("RS_WARNING_STATE_UNKNOWN", output)
                self.assertEqual(list(outside.iterdir()), [])
        finally:
            scenario.close()


    def test_user_prompt_before_framing_never_invents_interaction(self) -> None:
        from control_plane.hooks import run_hook

        scenario = GitScenario()
        try:
            output = json.loads(
                run_hook(
                    json.dumps(
                        self.payload(
                            "UserPromptSubmit",
                            cwd=str(scenario.repo),
                            session_id="session-pending-framing",
                        )
                    ).encode()
                )
            )
        finally:
            scenario.close()
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn('"interaction":"pending_framing"', context)
        self.assertNotIn("/plan", context)
        self.assertNotIn("/goal", context)
        self.assertNotIn("route_digest", context)


    def test_hook_records_exact_serialized_output_bytes_in_runtime_metrics(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.hooks import run_hook
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.repository import worktree_git_dir

        scenario = GitScenario()
        try:
            scenario.checkout_feature("feature/hook-metrics")
            root = scenario.repo.resolve()
            state_dir = worktree_git_dir(root)
            task_id = "TASK-HOOK-METRICS"
            session_id = "session-hook-metrics"
            task_digest = contract_digest({"task": task_id})
            route_digest = contract_digest({"route": task_id})
            store = TaskStore(state_dir)
            store.start(
                task_id,
                outcome="local_change",
                branch="feature/hook-metrics",
                task_digest=task_digest,
                decision_digest=route_digest,
            )
            TaskLease.acquire(
                state_dir,
                task_id=task_id,
                worktree=str(root),
                branch="feature/hook-metrics",
                session_id=session_id,
                paths=["."],
                policy_digest=contract_digest(scenario.policy()),
            )

            with patch.dict(
                os.environ,
                {"CODEX_CONTROL_PLANE_TASK_ID": task_id},
                clear=False,
            ):
                output = run_hook(
                    json.dumps(
                        self.payload(
                            "UserPromptSubmit",
                            cwd=str(root),
                            session_id=session_id,
                            turn_id="turn-hook-metrics",
                            prompt="sensitive-marker-not-a-secret",
                        )
                    ).encode()
                )

            summary = store.context_metrics(task_id)
            persisted_metrics = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (
                    state_dir
                    / "codex-control-plane"
                    / "metrics"
                    / task_id
                ).glob("*.json")
            )
            self.assertTrue(output)
            self.assertNotIn("sensitive-marker-not-a-secret", persisted_metrics)
            self.assertEqual(
                summary["hook_output_bytes_total"],
                len(output.encode("utf-8")),
            )
            self.assertEqual(
                summary["hook_output_bytes_max"],
                len(output.encode("utf-8")),
            )
        finally:
            scenario.close()

    def test_risk_fingerprint_uses_governing_remote_and_base(self) -> None:
        from control_plane.hooks import risk_fingerprint

        scenario = GitScenario(base_branch="trunk")
        try:
            policy = self.governing_policy(
                scenario,
                "fingerprint-governing-base",
            )
            observed: list[tuple[str, ...]] = []
            from control_plane import hooks as hooks_module

            real_observation = hooks_module._git_observation

            def record(root: Path, arguments: list[str]) -> str:
                observed.append(tuple(arguments))
                return real_observation(root, arguments)

            with patch(
                "control_plane.hooks._git_observation",
                side_effect=record,
            ):
                fingerprint = risk_fingerprint(
                    scenario.repo.resolve(),
                    governing_policy=policy,
                )

            self.assertRegex(fingerprint, r"^sha256:[0-9a-f]{64}$")
            self.assertIn(
                ("rev-parse", "--verify", "origin/trunk"),
                observed,
            )
            self.assertNotIn(
                ("rev-parse", "--verify", "origin/main"),
                observed,
            )
        finally:
            scenario.close()


    def test_completed_safe_read_normal_and_nonzero_are_exact(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            scenario.checkout_feature("feature/safe-read")
            clean = execute_safe_read(
                ("git", "status", "--short"),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-read-clean"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            (scenario.repo / "baseline.txt").write_text(
                "changed\n", encoding="utf-8"
            )
            git(scenario.repo, "add", "baseline.txt")
            git(scenario.repo, "commit", "-m", "test: change")
            different = execute_safe_read(
                (
                    "git",
                    "diff",
                    "--exit-code",
                    "origin/main...HEAD",
                    "--",
                    "baseline.txt",
                ),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-read-different"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )

            self.assertEqual(clean.status, "completed")
            self.assertEqual(clean.exit_code, 0)
            self.assertEqual(different.status, "completed")
            self.assertEqual(different.exit_code, 1)
            self.assertEqual(clean.stdout_bytes, len(clean.stdout))
            self.assertEqual(clean.stderr_bytes, len(clean.stderr))
            self.assertLessEqual(len(different.stdout), 4096)
            self.assertFalse(different.timed_out)
            self.assertFalse(different.truncated)
        finally:
            scenario.close()

    def test_completed_safe_read_timeout_kills_process_group(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_git = Path(temporary) / "git"
                child_marker = Path(temporary) / "child-finished"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    f"(sleep 2; printf done > {child_marker}) &\n"
                    "sleep 2\n",
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                with patch(
                    "control_plane.hooks._safe_read_git_executable",
                    return_value=str(fake_git),
                ):
                    result = execute_safe_read(
                        ("git", "status", "--short"),
                        root=scenario.repo.resolve(),
                        worktree_inventory=self.worktree_inventory(
                            scenario.repo, "inventory-safe-read-timeout"
                        ),
                        timeout_seconds=0.05,
                        output_limit_bytes=4096,
                    )
                self.assertEqual(result.status, "timeout")
                self.assertTrue(result.timed_out)
                self.assertIsNone(result.exit_code)
                self.assertFalse(child_marker.exists())
        finally:
            scenario.close()

    def test_completed_safe_read_overflow_is_bounded(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_git = Path(temporary) / "git"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    "while :; do printf 0123456789; done\n",
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                with patch(
                    "control_plane.hooks._safe_read_git_executable",
                    return_value=str(fake_git),
                ):
                    result = execute_safe_read(
                        ("git", "status", "--short"),
                        root=scenario.repo.resolve(),
                        worktree_inventory=self.worktree_inventory(
                            scenario.repo, "inventory-safe-read-overflow"
                        ),
                        timeout_seconds=2,
                        output_limit_bytes=128,
                    )
            self.assertEqual(result.status, "truncated")
            self.assertTrue(result.truncated)
            self.assertIsNone(result.exit_code)
            self.assertEqual(len(result.stdout), 128)
            self.assertLessEqual(len(result.stderr), 128)
        finally:
            scenario.close()

    def test_safe_read_never_executes_git_from_ambient_path(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_git = Path(temporary) / "git"
                marker = Path(temporary) / "ambient-git-executed"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    f"printf used > {marker}\n"
                    'exec /usr/bin/git "$@"\n',
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                inventory = self.worktree_inventory(
                    scenario.repo,
                    "inventory-safe-read-ambient-path",
                )
                with patch.dict(
                    os.environ,
                    {"PATH": temporary},
                    clear=False,
                ):
                    result = execute_safe_read(
                        ("git", "status", "--short"),
                        root=scenario.repo.resolve(),
                        worktree_inventory=inventory,
                        timeout_seconds=2,
                        output_limit_bytes=4096,
                    )

                self.assertEqual(result.status, "completed")
                self.assertFalse(marker.exists())
        finally:
            scenario.close()

    def test_hook_never_executes_git_from_ambient_path(self) -> None:
        from control_plane.hooks import run_hook

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_git = Path(temporary) / "git"
                marker = Path(temporary) / "ambient-hook-git-executed"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    f"printf used > {marker}\n"
                    'exec /usr/bin/git "$@"\n',
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                with patch.dict(
                    os.environ,
                    {"PATH": f"{temporary}:/usr/bin:/bin"},
                    clear=False,
                ):
                    output = run_hook(
                        json.dumps(
                            self.payload(
                                "UserPromptSubmit",
                                cwd=str(scenario.repo.resolve()),
                                session_id="session-hook-trusted-git",
                                turn_id="turn-hook-trusted-git",
                            )
                        ).encode()
                    )

                self.assertTrue(output)
                self.assertFalse(marker.exists())
        finally:
            scenario.close()

    def test_safe_read_timeout_kills_descendant_after_leader_exits(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_git = Path(temporary) / "git"
                child_marker = Path(temporary) / "descendant-finished"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    f"(sleep 2; printf done > {child_marker}) &\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                with patch(
                    "control_plane.hooks._safe_read_git_executable",
                    return_value=str(fake_git),
                ):
                    result = execute_safe_read(
                        ("git", "status", "--short"),
                        root=scenario.repo.resolve(),
                        worktree_inventory=self.worktree_inventory(
                            scenario.repo,
                            "inventory-safe-read-descendant-pipe",
                        ),
                        timeout_seconds=0.05,
                        output_limit_bytes=4096,
                    )

                self.assertEqual(result.status, "timeout")
                self.assertTrue(result.timed_out)
                self.assertFalse(child_marker.exists())
        finally:
            scenario.close()

    def test_safe_read_timeout_applies_after_child_closes_output(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_git = Path(temporary) / "git"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    "exec 1>&-\n"
                    "exec 2>&-\n"
                    "sleep 2\n",
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                with patch(
                    "control_plane.hooks._safe_read_git_executable",
                    return_value=str(fake_git),
                ):
                    started = time.monotonic()
                    result = execute_safe_read(
                        ("git", "status", "--short"),
                        root=scenario.repo.resolve(),
                        worktree_inventory=self.worktree_inventory(
                            scenario.repo,
                            "inventory-safe-read-closed-output",
                        ),
                        timeout_seconds=0.05,
                        output_limit_bytes=4096,
                    )

                self.assertEqual(result.status, "timeout")
                self.assertLess(time.monotonic() - started, 0.2)
        finally:
            scenario.close()

    def test_safe_read_inventory_is_exact_fresh_and_one_shot(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            inventory = self.worktree_inventory(
                scenario.repo, "inventory-safe-read-one-shot"
            )
            first = execute_safe_read(
                ("git", "status", "--short"),
                root=scenario.repo.resolve(),
                worktree_inventory=inventory,
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            self.assertEqual(first.status, "completed")
            with self.assertRaisesRegex(ValueError, "E_SAFE_READ_INVENTORY"):
                execute_safe_read(
                    ("git", "status", "--short"),
                    root=scenario.repo.resolve(),
                    worktree_inventory=inventory,
                    timeout_seconds=2,
                    output_limit_bytes=4096,
                )
            with self.assertRaisesRegex(ValueError, "E_SAFE_READ_INVENTORY"):
                execute_safe_read(
                    ("git", "status", "--short"),
                    root=scenario.repo.resolve(),
                    worktree_inventory={"records": []},
                    timeout_seconds=2,
                    output_limit_bytes=4096,
                )
        finally:
            scenario.close()

    def test_safe_read_rejects_inventory_that_expires_before_use(self) -> None:
        from control_plane.hooks import execute_safe_read
        from control_plane.host_bridge import (
            observe_worktree_inventory,
            validate_worktree_inventory_observation,
        )

        scenario = GitScenario()
        try:
            now = [100.0]
            common = Path(
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(scenario.repo),
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-common-dir",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            ).resolve()
            observation = observe_worktree_inventory(
                canonical_common_git_dir=common,
                invocation_id="inventory-safe-read-expired",
                clock=lambda: now[0],
                ttl_seconds=1,
                max_output_bytes=1_048_576,
            )
            inventory = validate_worktree_inventory_observation(
                observation,
                expected_common_git_dir=common,
                expected_invocation_id="inventory-safe-read-expired",
                clock=lambda: now[0],
            )
            now[0] = 102.0

            with self.assertRaisesRegex(
                ValueError, "E_SAFE_READ_INVENTORY"
            ):
                execute_safe_read(
                    ("git", "status", "--short"),
                    root=scenario.repo.resolve(),
                    worktree_inventory=inventory,
                    timeout_seconds=2,
                    output_limit_bytes=4096,
                )
        finally:
            scenario.close()

    def test_safe_read_rejects_symlink_unregistered_and_unsafe_argv(
        self,
    ) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                symlink = Path(temporary) / "repo-link"
                symlink.symlink_to(scenario.repo, target_is_directory=True)
                with self.assertRaisesRegex(
                    ValueError, "E_SAFE_READ_REPOSITORY"
                ):
                    execute_safe_read(
                        ("git", "status", "--short"),
                        root=symlink,
                        worktree_inventory=self.worktree_inventory(
                            scenario.repo, "inventory-safe-read-symlink"
                        ),
                        timeout_seconds=2,
                        output_limit_bytes=4096,
                    )
            rejected = execute_safe_read(
                ("git", "-c", "alias.status=!echo unsafe", "status"),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-read-rejected"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            self.assertEqual(rejected.status, "rejected")
            self.assertEqual(rejected.exit_code, None)
        finally:
            scenario.close()

    def test_safe_read_rg_uses_quiet_single_path_grammar(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            allowed = execute_safe_read(
                (
                    "rg",
                    "--no-config",
                    "--quiet",
                    "-e",
                    "baseline",
                    "--",
                    "baseline.txt",
                ),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-read-rg"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            rejected = execute_safe_read(
                (
                    "rg",
                    "--no-config",
                    "--quiet",
                    "-e",
                    "baseline",
                    "--",
                    "baseline.txt",
                    "second.txt",
                ),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-read-rg-reject"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            self.assertEqual(allowed.status, "completed")
            self.assertEqual(allowed.exit_code, 0)
            self.assertEqual(rejected.status, "rejected")

            absolute = execute_safe_read(
                (
                    "rg",
                    "--no-config",
                    "--quiet",
                    "-e",
                    "baseline",
                    "--",
                    str((scenario.repo / "baseline.txt").resolve()),
                ),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-read-rg-absolute"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            self.assertEqual(absolute.status, "completed")
            self.assertEqual(absolute.exit_code, 0)
        finally:
            scenario.close()

    def test_safe_read_governing_secret_scan_is_closed_and_nonrevealing(
        self,
    ) -> None:
        from control_plane.hooks import (
            execute_safe_read,
            secret_pattern_set_digest,
        )

        scenario = GitScenario()
        try:
            clean_path = (scenario.repo / "clean-pilot.md").resolve()
            finding_path = (scenario.repo / "finding-pilot.md").resolve()
            clean_path.write_text("Pilot charter without credentials.\n", encoding="utf-8")
            finding_path.write_text(
                "pass" + "word: replace-me-not-a-real-credential\n",
                encoding="utf-8",
            )
            clean = execute_safe_read(
                ("secret-scan-governing", "--", str(clean_path)),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-secret-scan-clean"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            finding = execute_safe_read(
                ("secret-scan-governing", "--", str(finding_path)),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-secret-scan-finding"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )
            forged = execute_safe_read(
                (
                    "secret-scan-governing",
                    "--pattern",
                    "narrow",
                    "--",
                    str(clean_path),
                ),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-secret-scan-forged"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )

            self.assertEqual(clean.status, "completed")
            self.assertEqual(clean.exit_code, 1)
            self.assertEqual(finding.status, "completed")
            self.assertEqual(finding.exit_code, 0)
            self.assertEqual(forged.status, "rejected")
            self.assertEqual(clean.pattern_set_digest, secret_pattern_set_digest())
            self.assertEqual(finding.pattern_set_digest, secret_pattern_set_digest())
            self.assertEqual(clean.stdout, b"")
            self.assertEqual(clean.stderr, b"")
            self.assertEqual(finding.stdout, b"")
            self.assertEqual(finding.stderr, b"")
        finally:
            scenario.close()

    def test_safe_read_show_forces_no_external_diff_or_textconv(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_git = Path(temporary) / "git"
                argv_record = Path(temporary) / "argv"
                fake_git.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s\\n' \"$@\" > {argv_record}\n",
                    encoding="utf-8",
                )
                fake_git.chmod(0o700)
                with patch(
                    "control_plane.hooks._safe_read_git_executable",
                    return_value=str(fake_git),
                ):
                    result = execute_safe_read(
                        ("git", "show", "--stat", "--oneline", "HEAD"),
                        root=scenario.repo.resolve(),
                        worktree_inventory=self.worktree_inventory(
                            scenario.repo, "inventory-safe-show-no-filters"
                        ),
                        timeout_seconds=2,
                        output_limit_bytes=4096,
                    )

                arguments = argv_record.read_text(encoding="utf-8").splitlines()
                self.assertEqual(result.status, "completed")
                self.assertIn("--no-ext-diff", arguments)
                self.assertIn("--no-textconv", arguments)
                self.assertLess(arguments.index("--no-ext-diff"), arguments.index("--stat"))
                self.assertLess(arguments.index("--no-textconv"), arguments.index("--stat"))
        finally:
            scenario.close()

    def test_safe_read_ignores_replace_objects(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            original = git(scenario.repo, "rev-parse", "HEAD")
            (scenario.repo / "baseline.txt").write_text(
                "replacement\n", encoding="utf-8"
            )
            git(scenario.repo, "add", "baseline.txt")
            git(scenario.repo, "commit", "-m", "REPLACEMENT_MARKER")
            current = git(scenario.repo, "rev-parse", "HEAD")
            git(scenario.repo, "replace", current, original)

            result = execute_safe_read(
                ("git", "show", "--stat", "--oneline", "HEAD"),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-show-replace"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.exit_code, 0)
            self.assertIn(b"REPLACEMENT_MARKER", result.stdout)
        finally:
            scenario.close()

    def test_safe_read_missing_promisor_object_never_runs_transport(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            blob = git(scenario.repo, "rev-parse", "HEAD:baseline.txt")
            object_path = (
                scenario.repo
                / ".git"
                / "objects"
                / blob[:2]
                / blob[2:]
            )
            self.assertTrue(object_path.is_file())
            marker = scenario.root / "lazy-fetch-transport-used"
            upload_pack = scenario.root / "upload-pack-canary"
            upload_pack.write_text(
                "#!/bin/sh\n"
                f"printf used > {marker}\n"
                'exec /usr/bin/git-upload-pack "$@"\n',
                encoding="utf-8",
            )
            upload_pack.chmod(0o700)
            git(scenario.repo, "config", "core.repositoryformatversion", "1")
            git(scenario.repo, "config", "extensions.partialClone", "origin")
            git(scenario.repo, "config", "remote.origin.promisor", "true")
            git(
                scenario.repo,
                "config",
                "remote.origin.partialclonefilter",
                "blob:none",
            )
            git(
                scenario.repo,
                "config",
                "remote.origin.uploadpack",
                str(upload_pack),
            )
            object_path.unlink()

            result = execute_safe_read(
                ("git", "show", "--stat", "--oneline", "HEAD"),
                root=scenario.repo.resolve(),
                worktree_inventory=self.worktree_inventory(
                    scenario.repo, "inventory-safe-show-promisor"
                ),
                timeout_seconds=2,
                output_limit_bytes=4096,
            )

            self.assertEqual(result.status, "completed")
            self.assertNotEqual(result.exit_code, 0)
            self.assertFalse(marker.exists())
        finally:
            scenario.close()

    def test_safe_read_rg_uses_the_bounded_trusted_executable(self) -> None:
        from control_plane.hooks import execute_safe_read

        scenario = GitScenario()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                fake_rg = Path(temporary) / "rg"
                child_marker = Path(temporary) / "rg-child-finished"
                fake_rg.write_text(
                    "#!/bin/sh\n"
                    f"(sleep 2; printf done > {child_marker}) &\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                fake_rg.chmod(0o700)
                with patch(
                    "control_plane.hooks._safe_read_rg_executable",
                    return_value=str(fake_rg),
                ):
                    result = execute_safe_read(
                        (
                            "rg",
                            "--no-config",
                            "--quiet",
                            "-e",
                            "baseline",
                            "--",
                            "baseline.txt",
                        ),
                        root=scenario.repo.resolve(),
                        worktree_inventory=self.worktree_inventory(
                            scenario.repo,
                            "inventory-safe-read-rg-bounded",
                        ),
                        timeout_seconds=0.05,
                        output_limit_bytes=4096,
                    )

                self.assertEqual(result.status, "timeout")
                self.assertTrue(result.timed_out)
                time.sleep(0.1)
                self.assertFalse(child_marker.exists())
        finally:
            scenario.close()



if __name__ == "__main__":
    unittest.main()
