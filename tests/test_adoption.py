from __future__ import annotations

import json
import os
import unittest
from hashlib import sha256
from pathlib import Path
import shutil
import signal
import subprocess
import sys
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
        import control_plane.adoption as adoption
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_status,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        original = adoption._durable_replace_bytes
        writes = 0

        def fail_third_staged_write(
            destination: Path,
            payload: bytes,
            *,
            suffix: str,
            expected_digest: str | None,
            mode: int,
        ) -> None:
            nonlocal writes
            if suffix == ".codex-new":
                writes += 1
                if writes == 3:
                    raise OSError("injected write failure")
            original(
                destination,
                payload,
                suffix=suffix,
                expected_digest=expected_digest,
                mode=mode,
            )

        with (
            patch(
                "control_plane.adoption._durable_replace_bytes",
                side_effect=fail_third_staged_write,
            ),
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

    def test_installed_and_restored_files_are_durable_before_pointer_release(
        self,
    ) -> None:
        import stat

        import control_plane.adoption as adoption

        plan = adoption.adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        original_fsync = os.fsync
        original_replace = os.replace
        original_unlink = adoption._unlink_and_fsync
        synced_files: set[tuple[int, int]] = set()
        pending_directories: set[tuple[int, int]] = set()

        def tracked_fsync(descriptor: int) -> None:
            observed = os.fstat(descriptor)
            identity = (observed.st_dev, observed.st_ino)
            if stat.S_ISREG(observed.st_mode):
                synced_files.add(identity)
            elif stat.S_ISDIR(observed.st_mode):
                pending_directories.discard(identity)
            original_fsync(descriptor)

        def tracked_replace(source, destination) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            if source_path.name.endswith(
                (".codex-new", ".codex-upgrade", ".codex-restore")
            ):
                observed = source_path.stat()
                self.assertIn(
                    (observed.st_dev, observed.st_ino),
                    synced_files,
                    f"{source_path.name} was replaced before fsync",
                )
                parent = destination_path.parent.stat()
                pending_directories.add((parent.st_dev, parent.st_ino))
            original_replace(source, destination)

        def tracked_pointer_unlink(path: Path) -> None:
            if path == adoption._owner_pointer_path(self.scenario.repo):
                self.assertFalse(
                    pending_directories,
                    "owner pointer was released before destination dir fsync",
                )
            original_unlink(path)

        with (
            patch("control_plane.adoption.os.fsync", tracked_fsync),
            patch("control_plane.adoption.os.replace", tracked_replace),
            patch(
                "control_plane.adoption._unlink_and_fsync",
                tracked_pointer_unlink,
            ),
        ):
            adoption.adoption_apply(plan)

    def test_adoption_mutex_is_released_after_process_kill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            lock_path = Path(temporary) / "adoption.lock"
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import time;"
                        "from pathlib import Path;"
                        "from control_plane.adoption import _ProcessLock;"
                        f"p=Path({str(lock_path)!r});"
                        "guard=_ProcessLock(p);guard.__enter__();"
                        "print('LOCKED', flush=True);time.sleep(60)"
                    ),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(child.stdout.readline().strip(), "LOCKED")
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()

            from control_plane.adoption import _ProcessLock

            with _ProcessLock(lock_path):
                self.assertTrue(lock_path.exists())

    def _recovery_scenario(
        self, suffix: str
    ) -> tuple[GitScenario, Path]:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature(f"codex/recovery-owner-{suffix}")
        recovery_worktree = scenario.root / f"recovery-{suffix}"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                f"codex/recovery-peer-{suffix}",
                str(recovery_worktree),
                "HEAD",
            ],
            cwd=scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return scenario, recovery_worktree

    def _recovery_record(
        self, scenario: GitScenario, suffix: str
    ) -> tuple[dict[str, object], bytes]:
        from control_plane.repository import worktree_git_dir

        target = scenario.repo / "baseline.txt"
        original = target.read_bytes()
        backup_relative = (
            f"codex-control-plane/backups/recovery-{suffix}/baseline.txt"
        )
        backup = worktree_git_dir(scenario.repo) / backup_relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(original)
        digest = f"sha256:{sha256(original).hexdigest()}"
        return (
            {
                "path": "baseline.txt",
                "before_digest": digest,
                "installed_digest": (
                    "sha256:" + sha256(b"installed\n").hexdigest()
                ),
                "backup": backup_relative,
            },
            original,
        )

    def test_other_worktree_recovers_owner_transaction_after_each_crash_point(
        self,
    ) -> None:
        from control_plane.adoption import (
            _advance_transaction,
            _atomic_json,
            _begin_transaction,
            _owner_pointer_path,
            _recover_owner_transaction,
        )
        from control_plane.contracts import contract_digest

        for phase in ("pointer", "wal_and_config", "committed"):
            with self.subTest(phase=phase):
                scenario, peer = self._recovery_scenario(phase)
                record, original = self._recovery_record(scenario, phase)
                transaction = _begin_transaction(
                    scenario.repo,
                    operation="adopt",
                    records=[record],
                )
                installed = b"installed\n"
                (scenario.repo / "baseline.txt").write_bytes(installed)
                state = {"schema_version": 2, "status": "preparing"}
                if phase in {"wal_and_config", "committed"}:
                    _atomic_json(
                        transaction["manifest_path"].parents[2]
                        / "adoption.json",
                        state,
                    )
                    _advance_transaction(
                        transaction,
                        status="preparing",
                        state=state,
                    )
                if phase == "committed":
                    state = {"schema_version": 2, "status": "applied"}
                    final = _advance_transaction(
                        transaction,
                        status="committed",
                        state=state,
                    )
                    _atomic_json(
                        transaction["manifest_path"].parent / "COMMITTED",
                        {
                            "schema_version": 1,
                            "transaction_id": transaction["pointer"][
                                "transaction_id"
                            ],
                            "final_generation": final["generation"],
                            "final_generation_digest": final[
                                "generation_digest"
                            ],
                            "state_digest": contract_digest(state),
                        },
                    )

                _recover_owner_transaction(peer)

                observed = (scenario.repo / "baseline.txt").read_bytes()
                self.assertEqual(
                    observed,
                    installed if phase == "committed" else original,
                )
                self.assertFalse(_owner_pointer_path(peer).exists())

    def test_owner_pointer_never_hashes_mutable_journal(self) -> None:
        from control_plane.adoption import (
            _advance_transaction,
            _begin_transaction,
            _digest,
            _owner_pointer_path,
            _recover_owner_transaction,
        )

        scenario, peer = self._recovery_scenario("stable-manifest")
        transaction = _begin_transaction(
            scenario.repo,
            operation="upgrade",
            records=[],
            previous_state={"schema_version": 2, "status": "applied"},
        )
        pointer_path = _owner_pointer_path(peer)
        pointer_before = json.loads(pointer_path.read_text(encoding="utf-8"))
        manifest_digest = pointer_before["manifest_digest"]

        for status in ("upgrading", "files_replaced", "state_written"):
            _advance_transaction(
                transaction,
                status=status,
                state={"schema_version": 2, "status": status},
            )

        pointer_after = json.loads(pointer_path.read_text(encoding="utf-8"))
        self.assertEqual(pointer_after, pointer_before)
        self.assertNotIn("journal", pointer_after)
        self.assertNotIn("wal_digest", pointer_after)
        self.assertEqual(
            _digest(transaction["manifest_path"]), manifest_digest
        )
        _recover_owner_transaction(peer)

    def test_broken_wal_chain_or_ambiguous_generation_fails_closed(
        self,
    ) -> None:
        from control_plane.adoption import (
            _atomic_json,
            _begin_transaction,
            _owner_pointer_path,
            _recover_owner_transaction,
        )

        for case in (
            "broken_previous_digest",
            "duplicate_generation",
            "manifest_escape",
            "unbound_committed",
        ):
            with self.subTest(case=case):
                scenario, peer = self._recovery_scenario(case)
                transaction = _begin_transaction(
                    scenario.repo,
                    operation="adopt",
                    records=[],
                )
                pointer_path = _owner_pointer_path(peer)
                if case == "broken_previous_digest":
                    generation_path = (
                        transaction["wal_root"] / "00000001.json"
                    )
                    generation = json.loads(
                        generation_path.read_text(encoding="utf-8")
                    )
                    generation["previous_generation_digest"] = "sha256:bad"
                    _atomic_json(generation_path, generation)
                elif case == "duplicate_generation":
                    generation = json.loads(
                        (
                            transaction["wal_root"] / "00000001.json"
                        ).read_text(encoding="utf-8")
                    )
                    _atomic_json(
                        transaction["wal_root"] / "00000002.json",
                        generation,
                    )
                elif case == "manifest_escape":
                    pointer = json.loads(
                        pointer_path.read_text(encoding="utf-8")
                    )
                    pointer["manifest_path"] = "../outside.json"
                    _atomic_json(pointer_path, pointer)
                else:
                    _atomic_json(
                        transaction["manifest_path"].parent / "COMMITTED",
                        {
                            "schema_version": 1,
                            "transaction_id": transaction["pointer"][
                                "transaction_id"
                            ],
                            "final_generation": 1,
                            "final_generation_digest": "sha256:unbound",
                            "state_digest": "sha256:unbound",
                        },
                    )
                pointer_before = pointer_path.read_bytes()

                with self.assertRaisesRegex(
                    ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
                ):
                    _recover_owner_transaction(peer)

                self.assertEqual(pointer_path.read_bytes(), pointer_before)

    def test_isolated_launcher_ignores_top_level_runtime_shadow(self) -> None:
        from control_plane.adoption import (
            RUNTIME_MODULES,
            adoption_apply,
            adoption_plan,
        )

        plan = adoption_plan(
            ROOT,
            self.scenario.repo,
            base_branch="main",
            allow_dirty_source=True,
        )
        adoption_apply(plan)
        shadow = self.scenario.repo / "control_plane"
        shadow.mkdir()
        (shadow / "__init__.py").write_text(
            "raise RuntimeError('TOP_LEVEL_SHADOW_IMPORTED')\n",
            encoding="utf-8",
        )
        (shadow / "cli.py").write_text(
            "raise RuntimeError('TOP_LEVEL_SHADOW_IMPORTED')\n",
            encoding="utf-8",
        )

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
        self.assertNotIn("TOP_LEVEL_SHADOW_IMPORTED", completed.stderr)
        self.assertNotIn("TOP_LEVEL_SHADOW_IMPORTED", hook.stderr)
        for module in ("host_bridge.py", "scopes.py"):
            self.assertIn(module, RUNTIME_MODULES)
            self.assertTrue(
                (
                    self.scenario.repo
                    / ".codex"
                    / "runtime"
                    / "codex_control_plane_runtime_v2"
                    / module
                ).is_file()
            )

    def test_pr_a_adopted_runtime_imports_host_bridge_and_scopes_without_source(
        self,
    ) -> None:
        from control_plane.adoption import (
            RUNTIME_PACKAGE,
            adoption_apply,
            adoption_plan,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        runtime_root = self.scenario.repo / ".codex" / "runtime"
        package_root = runtime_root / RUNTIME_PACKAGE
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(runtime_root),
            "PYTHONSAFEPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        imported = subprocess.run(
            [
                sys.executable,
                "-P",
                "-B",
                "-c",
                (
                    f"from {RUNTIME_PACKAGE} import host_bridge, scopes;"
                    "from pathlib import Path;"
                    f"root=Path({str(package_root)!r}).resolve();"
                    "assert Path(host_bridge.__file__).resolve().is_relative_to(root);"
                    "assert Path(scopes.__file__).resolve().is_relative_to(root);"
                    "print('ISOLATED_IMPORT_OK')"
                ),
            ],
            cwd=self.scenario.root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        launcher = self.scenario.repo / "scripts" / "control-plane"
        doctor = subprocess.run(
            [
                str(launcher),
                "doctor",
                "--repo",
                str(self.scenario.repo),
                "--json",
            ],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        preflight = subprocess.run(
            [
                str(launcher),
                "preflight",
                "--mode",
                "read",
                "--repo",
                str(self.scenario.repo),
                "--json",
            ],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(imported.stdout.strip(), "ISOLATED_IMPORT_OK")
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn('"ok": true', doctor.stdout.lower())
        self.assertEqual(
            preflight.returncode,
            0,
            preflight.stdout + preflight.stderr,
        )
        self.assertIn('"ok": true', preflight.stdout.lower())

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
