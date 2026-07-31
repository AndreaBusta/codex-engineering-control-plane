from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).parents[1]


class LockfileTests(unittest.TestCase):
    def test_task8_runtime_and_git_guard_sources_are_locked(self) -> None:
        from hashlib import sha256

        from control_plane.adoption import RUNTIME_MODULES

        self.assertIn("git_guards.py", RUNTIME_MODULES)
        lock = tomllib.loads(
            (ROOT / ".codex" / "control-plane.lock").read_text(
                encoding="utf-8"
            )
        )
        for key, relative in (
            ("git_pre_commit", ".codex/git-hooks/pre-commit"),
            ("git_pre_push", ".codex/git-hooks/pre-push"),
        ):
            payload = (ROOT / relative).read_bytes()
            self.assertEqual(
                lock["digests"][key],
                f"sha256:{sha256(payload).hexdigest()}",
            )

    def test_source_and_distributed_runtime_expose_task7_hook_contracts(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        import control_plane.hooks as hooks
        from control_plane.adoption import RUNTIME_MODULES

        self.assertIn("hooks.py", RUNTIME_MODULES)
        self.assertIn("host_bridge.py", RUNTIME_MODULES)
        self.assertTrue(callable(hooks.execute_safe_read))
        self.assertTrue(callable(hooks.evaluate_pretool_use))
        self.assertTrue(callable(hooks.secret_pattern_set_digest))
        self.assertTrue(callable(hooks.gc_current_warning_view))
        self.assertTrue(callable(bridge.run_macos_hook_smoke))
        self.assertTrue(callable(bridge.publish_macos_hook_smoke_receipt))

    def test_source_and_distributed_runtime_include_risk_sentinel(
        self,
    ) -> None:
        import control_plane.risk_sentinel as sentinel
        from control_plane.adoption import RUNTIME_MODULES

        self.assertIn("risk_sentinel.py", RUNTIME_MODULES)
        self.assertTrue(
            (ROOT / "control_plane" / "risk_sentinel.py").is_file()
        )
        self.assertTrue(callable(sentinel.aggregate_status))
        self.assertTrue(callable(sentinel.evaluate_risk_status))

    def test_source_runtime_exposes_resumable_clarification_lifecycle(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.adoption import RUNTIME_MODULES
        from control_plane.lifecycle import TaskStore

        self.assertIn("host_bridge.py", RUNTIME_MODULES)
        self.assertIn("lifecycle.py", RUNTIME_MODULES)
        self.assertTrue(callable(bridge.frame_trusted_interaction))
        self.assertTrue(callable(TaskStore.require_clarification))
        self.assertTrue(
            callable(TaskStore.resolve_and_resume_clarification)
        )
        self.assertTrue(callable(TaskStore.clarification_status))
        self.assertTrue(
            callable(TaskStore.gc_clarification_prompt_views)
        )

    def test_source_and_distributed_runtime_include_intake_module(
        self,
    ) -> None:
        from control_plane.adoption import RUNTIME_MODULES

        self.assertIn("intake.py", RUNTIME_MODULES)
        self.assertTrue((ROOT / "control_plane" / "intake.py").is_file())

    def test_source_and_distributed_runtime_include_clarification_module(
        self,
    ) -> None:
        from control_plane.adoption import RUNTIME_MODULES

        self.assertIn("clarification.py", RUNTIME_MODULES)
        self.assertTrue(
            (ROOT / "control_plane" / "clarification.py").is_file()
        )

    def _source_runtime_fixture(self, root: Path) -> None:
        shutil.copytree(ROOT / ".codex", root / ".codex")
        shutil.copytree(ROOT / "control_plane", root / "control_plane")
        shutil.copytree(ROOT / "scripts", root / "scripts")

    def _refresh_source_runtime_digest(self, root: Path) -> None:
        from control_plane.lockfile import runtime_digest

        lock = root / ".codex" / "control-plane.lock"
        lines = lock.read_text(encoding="utf-8").splitlines()
        digest = runtime_digest(
            root, "control_plane", runtime_layout="source"
        )
        lock.write_text(
            "\n".join(
                (
                    f'runtime = "{digest}"'
                    if line.startswith("runtime = ")
                    else line
                )
                for line in lines
            )
            + "\n",
            encoding="utf-8",
        )

    def test_repository_lock_matches_authority_files(self) -> None:
        from control_plane.lockfile import validate_lock

        self.assertEqual(validate_lock(ROOT), [])

    def test_drift_is_detected(self) -> None:
        from control_plane.lockfile import validate_lock

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / ".codex", root / ".codex")
            (root / ".codex" / "project-policy.toml").write_text(
                "\n", encoding="utf-8"
            )

            codes = {issue.code for issue in validate_lock(root)}

        self.assertIn("L_DIGEST", codes)

    def test_runtime_layout_is_closed_and_required(self) -> None:
        from control_plane.lockfile import validate_lock

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT, root, dirs_exist_ok=True)
            lock = root / ".codex" / "control-plane.lock"
            original = lock.read_text(encoding="utf-8")
            lock.write_text(
                original.replace('runtime_layout = "source"\n', ""),
                encoding="utf-8",
            )
            self.assertIn(
                "L_RUNTIME_LAYOUT",
                {issue.code for issue in validate_lock(root)},
            )
            lock.write_text(
                original.replace(
                    'runtime_layout = "source"',
                    'runtime_layout = "isolated"',
                ),
                encoding="utf-8",
            )
            codes = {issue.code for issue in validate_lock(root)}
            self.assertIn("L_RUNTIME_LAYOUT", codes)
            self.assertIn("L_RUNTIME_PACKAGE", codes)

    def test_runtime_digest_never_falls_back_to_the_other_layout(self) -> None:
        from control_plane.lockfile import runtime_digest

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            isolated = (
                root
                / ".codex"
                / "runtime"
                / "codex_control_plane_runtime_v2"
            )
            isolated.mkdir(parents=True)
            (isolated / "__init__.py").write_text("isolated\n", encoding="utf-8")
            source = root / "control_plane"
            source.mkdir()
            (source / "__init__.py").write_text("source\n", encoding="utf-8")

            source_digest = runtime_digest(
                root, "control_plane", runtime_layout="source"
            )
            isolated_digest = runtime_digest(
                root,
                "codex_control_plane_runtime_v2",
                runtime_layout="isolated",
            )

            self.assertNotEqual(source_digest, isolated_digest)
            with self.assertRaisesRegex(ValueError, "L_RUNTIME_LAYOUT"):
                runtime_digest(
                    root,
                    "codex_control_plane_runtime_v2",
                    runtime_layout="source",
                )

    def test_source_launcher_ignores_isolated_runtime_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._source_runtime_fixture(root)
            shadow = (
                root
                / ".codex"
                / "runtime"
                / "codex_control_plane_runtime_v2"
            )
            shadow.mkdir(parents=True)
            (shadow / "__init__.py").write_text(
                "raise RuntimeError('ISOLATED_SHADOW_IMPORTED')\n",
                encoding="utf-8",
            )
            (shadow / "cli.py").write_text(
                "raise RuntimeError('ISOLATED_SHADOW_IMPORTED')\n",
                encoding="utf-8",
            )
            self._refresh_source_runtime_digest(root)

            completed = subprocess.run(
                [
                    str(root / "scripts" / "control-plane"),
                    "policy-check",
                    "--policy",
                    str(root / ".codex" / "project-policy.toml"),
                    "--json",
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
            )
            hook = subprocess.run(
                [
                    "python3",
                    "-I",
                    "-B",
                    str(
                        root
                        / ".codex"
                        / "hooks"
                        / "control_plane_hook.py"
                    ),
                ],
                cwd=root,
                input='{"hook_event_name":"UserPromptSubmit"}',
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(hook.returncode, 0, hook.stderr)
            self.assertNotIn("ISOLATED_SHADOW_IMPORTED", completed.stderr)
            self.assertNotIn("ISOLATED_SHADOW_IMPORTED", hook.stderr)

    def test_source_launcher_and_hook_ignore_top_level_stdlib_shadow(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._source_runtime_fixture(root)
            launcher_marker = root / "launcher-stdlib-shadow-executed"
            hook_marker = root / "hook-stdlib-shadow-executed"
            (root / "argparse.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(launcher_marker)!r}).write_text('executed')\n"
                "raise RuntimeError('ARGPARSE_SHADOW_EXECUTED')\n",
                encoding="utf-8",
            )
            (root / "json.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(hook_marker)!r}).write_text('executed')\n"
                "raise RuntimeError('JSON_SHADOW_EXECUTED')\n",
                encoding="utf-8",
            )
            self._refresh_source_runtime_digest(root)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)

            completed = subprocess.run(
                [
                    str(root / "scripts" / "control-plane"),
                    "policy-check",
                    "--policy",
                    str(root / ".codex" / "project-policy.toml"),
                    "--json",
                ],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            hook = subprocess.run(
                [
                    "python3",
                    "-I",
                    "-B",
                    str(
                        root
                        / ".codex"
                        / "hooks"
                        / "control_plane_hook.py"
                    ),
                ],
                cwd=root,
                env=environment,
                input='{"hook_event_name":"UserPromptSubmit"}',
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(hook.returncode, 0, hook.stderr)
            self.assertFalse(launcher_marker.exists())
            self.assertFalse(hook_marker.exists())
            self.assertNotIn("ARGPARSE_SHADOW_EXECUTED", completed.stderr)
            self.assertNotIn("JSON_SHADOW_EXECUTED", hook.stderr)

    def test_runtime_layout_mismatch_missing_or_empty_fails_before_import(
        self,
    ) -> None:
        for case in ("mismatch", "missing", "empty"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self._source_runtime_fixture(root)
                marker = root / "runtime-imported"
                init = root / "control_plane" / "__init__.py"
                init.write_text(
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('imported')\n",
                    encoding="utf-8",
                )
                self._refresh_source_runtime_digest(root)
                if case == "mismatch":
                    lock = root / ".codex" / "control-plane.lock"
                    lock.write_text(
                        lock.read_text(encoding="utf-8").replace(
                            'runtime_package = "control_plane"',
                            (
                                "runtime_package = "
                                '"codex_control_plane_runtime_v2"'
                            ),
                        ),
                        encoding="utf-8",
                    )
                elif case == "missing":
                    (root / "control_plane").rename(
                        root / "control_plane-hidden"
                    )
                else:
                    for module in (root / "control_plane").glob("*.py"):
                        module.unlink()

                completed = subprocess.run(
                    [
                        str(root / "scripts" / "control-plane"),
                        "policy-check",
                        "--policy",
                        str(root / ".codex" / "project-policy.toml"),
                    ],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("E_RUNTIME_LAYOUT", completed.stderr)
                self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
