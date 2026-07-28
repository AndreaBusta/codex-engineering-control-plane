from __future__ import annotations

import json
import unittest
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

from tests.git_test_support import GitScenario


ROOT = Path(__file__).parents[1]


class AdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()
        self.addCleanup(self.scenario.close)
        self.scenario.checkout_feature("codex/adopt-v2")

    def test_plan_is_read_only_apply_is_idempotent_and_rollback_recovers(self) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_rollback,
            adoption_status,
            adoption_verify,
        )

        before = sorted(
            str(path.relative_to(self.scenario.repo))
            for path in self.scenario.repo.rglob("*")
            if ".git" not in path.parts
        )
        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        after_plan = sorted(
            str(path.relative_to(self.scenario.repo))
            for path in self.scenario.repo.rglob("*")
            if ".git" not in path.parts
        )
        self.assertEqual(before, after_plan)
        self.assertTrue(plan["ok"])
        self.assertIn(
            "AGENTS.md", {item["path"] for item in plan["changes"]}
        )

        first = adoption_apply(plan)
        second = adoption_apply(plan)

        self.assertTrue(first["ok"])
        self.assertTrue(second["idempotent"])
        self.assertTrue(adoption_verify(self.scenario.repo)["ok"])
        self.assertEqual(adoption_status(self.scenario.repo)["status"], "applied")
        installed = (
            self.scenario.repo
            / ".codex"
            / "runtime"
            / "codex_control_plane_runtime_v2"
            / "cli.py"
        )
        self.assertTrue(installed.is_file())
        from control_plane.lockfile import validate_lock
        from control_plane.resource_registry import load_registry

        self.assertEqual(validate_lock(self.scenario.repo), [])
        installed_registry = load_registry(
            self.scenario.repo / ".codex" / "resource-registry.toml"
        )
        for resource in installed_registry["resources"]:
            locator = str(resource["locator"])
            if resource["kind"] == "document" and locator.startswith("repo://"):
                self.assertTrue(
                    (
                        self.scenario.repo
                        / locator.removeprefix("repo://")
                    ).is_file(),
                    locator,
                )

        rolled_back = adoption_rollback(self.scenario.repo)

        self.assertTrue(rolled_back["ok"])
        for change in plan["changes"]:
            self.assertFalse((self.scenario.repo / change["path"]).exists())

    def test_rollback_refuses_to_destroy_post_install_edit(self) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_rollback,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        policy = self.scenario.repo / ".codex" / "project-policy.toml"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "E_ADOPT_DRIFT"):
            adoption_rollback(self.scenario.repo)

    def test_apply_refuses_managed_target_symlink(self) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan

        outside = self.scenario.root / "outside-policy.toml"
        outside.write_text("outside\n", encoding="utf-8")
        target = self.scenario.repo / ".codex" / "project-policy.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside)

        with self.assertRaisesRegex(ValueError, "E_ADOPT_PATH"):
            plan = adoption_plan(
                ROOT, self.scenario.repo, allow_dirty_source=True
            )
            adoption_apply(plan)

        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_rollback_drift_preflight_makes_zero_mutations(self) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_rollback,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        policy = self.scenario.repo / ".codex" / "project-policy.toml"
        policy.write_text(
            policy.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        before = {
            item["path"]: (
                self.scenario.repo / item["path"]
            ).read_bytes()
            for item in plan["changes"]
            if (self.scenario.repo / item["path"]).is_file()
        }

        with self.assertRaisesRegex(ValueError, "E_ADOPT_DRIFT"):
            adoption_rollback(self.scenario.repo)

        after = {
            path: (self.scenario.repo / path).read_bytes()
            for path in before
        }
        self.assertEqual(before, after)

    def test_apply_fault_injection_restores_every_target_file(self) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_status,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        original = Path.write_bytes
        writes = 0

        def fail_third_staged_write(path: Path, value: bytes) -> int:
            nonlocal writes
            if path.name.endswith(".codex-new"):
                writes += 1
                if writes == 3:
                    raise OSError("injected write failure")
            return original(path, value)

        with (
            patch.object(Path, "write_bytes", fail_third_staged_write),
            self.assertRaisesRegex(OSError, "injected write failure"),
        ):
            adoption_apply(plan)

        for change in plan["changes"]:
            path = self.scenario.repo / change["path"]
            if change["before_digest"] is None:
                self.assertFalse(path.exists(), change["path"])
        self.assertEqual(
            adoption_status(self.scenario.repo)["status"],
            "failed_rolled_back",
        )

    def test_plan_adapts_base_and_runtime_is_not_shadowed(self) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan

        plan = adoption_plan(
            ROOT,
            self.scenario.repo,
            base_branch="main",
            allow_dirty_source=True,
        )
        adoption_apply(plan)
        shadow = self.scenario.repo / "control_plane"
        shadow.mkdir()
        (shadow / "__init__.py").write_text("", encoding="utf-8")

        completed = subprocess.run(
            [
                str(self.scenario.repo / "scripts" / "control-plane"),
                "policy-check",
                "--policy",
                str(
                    self.scenario.repo
                    / ".codex"
                    / "project-policy.toml"
                ),
                "--json",
            ],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"ok": true', completed.stdout.lower())
        hook = subprocess.run(
            [
                "python3",
                str(
                    self.scenario.repo
                    / ".codex"
                    / "hooks"
                    / "control_plane_hook.py"
                ),
            ],
            cwd=self.scenario.repo,
            input=json.dumps(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(self.scenario.repo),
                }
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertIn("CONTROL_PLANE_AUDIT_V2", hook.stdout)

    def test_upgrade_plan_applies_new_source_and_remains_reversible(self) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_rollback,
            adoption_verify,
            upgrade_apply,
            upgrade_plan,
        )

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            subprocess.run(
                ["git", "init", "-b", "main", str(source)],
                check=True,
                capture_output=True,
            )
            for key, value in (
                ("user.name", "Control Plane Tests"),
                ("user.email", "control-plane@example.invalid"),
            ):
                subprocess.run(
                    ["git", "config", key, value],
                    cwd=source,
                    check=True,
                    capture_output=True,
                )
            subprocess.run(
                ["git", "add", "."],
                cwd=source,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "test: source v2"],
                cwd=source,
                check=True,
                capture_output=True,
            )
            plan = adoption_plan(source, self.scenario.repo)
            adoption_apply(plan)
            subprocess.run(
                ["git", "add", "."],
                cwd=self.scenario.repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "test: adopt v2"],
                cwd=self.scenario.repo,
                check=True,
                capture_output=True,
            )
            profile = source / "docs" / "profiles" / "generic.md"
            profile.write_text(
                profile.read_text(encoding="utf-8")
                + "\nUPGRADE-EVIDENCE\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "."],
                cwd=source,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "test: source v2.1"],
                cwd=source,
                check=True,
                capture_output=True,
            )
            upgrade = upgrade_plan(source, self.scenario.repo)
            result = upgrade_apply(upgrade)

            self.assertTrue(result["ok"])
            installed = (
                self.scenario.repo
                / "docs"
                / "codex-control-plane"
                / "profiles"
                / "generic.md"
            )
            self.assertIn(
                "UPGRADE-EVIDENCE", installed.read_text(encoding="utf-8")
            )
            self.assertTrue(adoption_verify(self.scenario.repo)["ok"])
            self.assertTrue(adoption_rollback(self.scenario.repo)["ok"])

    def test_target_policy_uses_detected_develop_base_before_apply(self) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan
        from control_plane.policy import load_policy

        scenario = GitScenario(base_branch="develop")
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/adopt-develop")

        plan = adoption_plan(ROOT, scenario.repo, allow_dirty_source=True)
        adoption_apply(plan)
        policy = load_policy(
            scenario.repo / ".codex" / "project-policy.toml"
        )

        self.assertEqual(plan["target_git"]["base_branch"], "develop")
        self.assertEqual(policy["git"]["base_branch"], "develop")


if __name__ == "__main__":
    unittest.main()
