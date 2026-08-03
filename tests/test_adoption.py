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

from tests.git_test_support import GitScenario, git


ROOT = Path(__file__).parents[1]


class AdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()
        self.addCleanup(self.scenario.close)
        self.scenario.checkout_feature("codex/adopt-v2")

    def _run_isolated_intake(
        self, repository: Path
    ) -> subprocess.CompletedProcess[str]:
        from control_plane.adoption import RUNTIME_PACKAGE

        runtime_root = repository / ".codex" / "runtime"
        package_root = runtime_root / RUNTIME_PACKAGE
        task = {
            "schema_version": 1,
            "task_id": "adopted-intake-render",
            "objective": "Explain a bounded adopted-runtime task.",
            "intent": "explain",
            "phase": "frame",
            "requested_outcome": "answer",
            "goals": [
                {
                    "id": "explain",
                    "summary": "Explain the task.",
                    "domains": ["generic"],
                    "depends_on": [],
                }
            ],
            "domains": ["generic"],
            "signals": [],
            "scope_paths": ["baseline.txt"],
            "risk": {
                "uncertainty": 0,
                "blast_radius": 0,
                "irreversibility": 0,
                "verification_complexity": 0,
            },
            "effects": [
                {"name": "local_read", "source": "user_explicit"}
            ],
            "explicit_resources": [],
            "excluded_resources": [],
        }
        code = (
            f"from {RUNTIME_PACKAGE} import "
            "clarification,contracts,host_bridge,intake,policy,"
            "resource_registry,routing,scopes;"
            "from pathlib import Path;"
            "import json;"
            f"root=Path({str(repository)!r}).resolve();"
            f"package=Path({str(package_root)!r}).resolve();"
            f"task=json.loads({json.dumps(task)!r});"
            "assert Path(host_bridge.__file__).resolve().is_relative_to(package);"
            "assert Path(clarification.__file__).resolve().is_relative_to(package);"
            "assert Path(intake.__file__).resolve().is_relative_to(package);"
            "assert Path(scopes.__file__).resolve().is_relative_to(package);"
            "pol=policy.load_policy(root/'.codex'/'project-policy.toml');"
            "reg=resource_registry.load_registry("
            "root/'.codex'/'resource-registry.toml');"
            "digest=contracts.contract_digest(task);"
            "raw=host_bridge.observe_inventory("
            "reg,root,root,digest,'adopted-intake',"
            "clock=lambda:100.0,ttl_seconds=30.0);"
            "inventory=host_bridge.validate_inventory_observation("
            "raw,expected_repo=root,expected_worktree=root,"
            "expected_registry_digest="
            "resource_registry.registry_contract_digest(reg),"
            "expected_task_digest=digest,"
            "expected_invocation_id='adopted-intake',"
            "clock=lambda:100.0);"
            "decision=routing.resolve_route("
            "task,pol,reg,inventory,mode='audit');"
            "manifest=routing.compact_route_manifest(decision);"
            "brief=intake.render_novice_brief(task,manifest);"
            "assert 'Modo normal:' in brief;"
            "assert 'automatic_change=false' in brief;"
            "assert len(brief.encode('utf-8'))<=1024;"
            "assert clarification.clarification_level(task)=='low';"
            "print('ISOLATED_INTAKE_OK')"
        )
        return subprocess.run(
            [sys.executable, "-P", "-B", "-c", code],
            cwd=self.scenario.root,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": str(runtime_root),
                "PYTHONSAFEPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=False,
            capture_output=True,
            text=True,
        )

    def test_adoption_installs_external_git_guards_and_rolls_back_config(
        self,
    ) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_rollback,
            adoption_verify,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        self.assertTrue(plan["ok"], plan)
        config_change = plan["git_config_changes"][0]
        self.assertEqual(config_change["key"], "core.hooksPath")
        self.assertEqual(config_change["observed_records"], [])
        self.assertEqual(config_change["previous_local_values"], [])
        self.assertTrue(Path(config_change["planned_value"]).is_absolute())
        snapshot = plan["installed_snapshot"]
        self.assertTrue(
            Path(snapshot["path"]).is_relative_to(
                Path(snapshot["common_git_dir"])
                / "codex-control-plane"
                / "installs"
            )
        )

        adoption_apply(plan)

        configured = subprocess.run(
            ["git", "config", "--local", "--get-all", "core.hooksPath"],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertEqual(
            configured.stdout.strip(), snapshot["hooks_path"]
        )
        install = Path(snapshot["path"])
        self.assertTrue((install / "manifest.json").is_file())
        manifest = json.loads(
            (install / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["source_commit"], plan["source_commit"])
        self.assertNotIn("remote_url", manifest["git"])
        self.assertEqual(
            manifest["git"]["remote_url_digest"],
            "sha256:"
            + sha256(
                git(
                    self.scenario.repo,
                    "remote",
                    "get-url",
                    "--push",
                    "origin",
                ).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            manifest["governing_base_commit"],
            subprocess.run(
                ["git", "rev-parse", "refs/remotes/origin/main"],
                cwd=self.scenario.repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
        )
        self.assertEqual(
            (install / "manifest.json").stat().st_mode & 0o777, 0o600
        )
        for hook in ("pre-commit", "pre-push"):
            self.assertEqual(
                (install / "git-hooks" / hook).stat().st_mode & 0o777,
                0o700,
            )
            self.assertNotIn(
                "__CONTROL_PLANE_ENTRYPOINT__",
                (install / "git-hooks" / hook).read_text(encoding="utf-8"),
            )
        self.assertTrue(adoption_verify(self.scenario.repo)["ok"])

        feature_commit = subprocess.run(
            [str(install / "git-hooks" / "pre-commit")],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            feature_commit.returncode,
            0,
            feature_commit.stdout + feature_commit.stderr,
        )
        subprocess.run(
            ["git", "switch", "main"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        base_commit = subprocess.run(
            [str(install / "git-hooks" / "pre-commit")],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(base_commit.returncode, 1)
        self.assertIn("GG_BASE_COMMIT", base_commit.stdout)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        feature = subprocess.run(
            ["git", "rev-parse", "codex/adopt-v2"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        remote_url = subprocess.run(
            ["git", "remote", "get-url", "--push", "origin"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        base_push = subprocess.run(
            [
                str(install / "git-hooks" / "pre-push"),
                "origin",
                remote_url,
            ],
            cwd=self.scenario.repo,
            input=f"refs/heads/main {head} refs/heads/main {head}\n",
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(base_push.returncode, 1)
        self.assertIn("GG_BASE_PUSH", base_push.stdout)
        feature_push = subprocess.run(
            [
                str(install / "git-hooks" / "pre-push"),
                "origin",
                remote_url,
            ],
            cwd=self.scenario.repo,
            input=(
                "refs/heads/codex/adopt-v2 "
                f"{feature} refs/heads/codex/adopt-v2 {head}\n"
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            feature_push.returncode,
            0,
            feature_push.stdout + feature_push.stderr,
        )
        for bad_input in (
            "only three fields\n",
            "x" * (1_048_576 + 1),
        ):
            with self.subTest(bad_input_size=len(bad_input)):
                rejected = subprocess.run(
                    [
                        str(install / "git-hooks" / "pre-push"),
                        "origin",
                        remote_url,
                    ],
                    cwd=self.scenario.repo,
                    input=bad_input,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(rejected.returncode, 1)
                self.assertIn("GG_INPUT_INVALID", rejected.stdout)

        self.assertTrue(adoption_rollback(self.scenario.repo)["ok"])
        absent = subprocess.run(
            ["git", "config", "--local", "--get-all", "core.hooksPath"],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(absent.returncode, 1)
        self.assertFalse(install.exists())

    def test_installed_guard_ignores_coordinated_candidate_authority_drift(
        self,
    ) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        install = Path(plan["installed_snapshot"]["path"])
        installed_hook = install / "git-hooks" / "pre-push"
        installed_hook_before = installed_hook.read_bytes()

        candidate_policy = (
            self.scenario.repo / ".codex" / "project-policy.toml"
        )
        policy_text = (
            candidate_policy.read_text(encoding="utf-8")
            .replace('remote = "origin"', 'remote = "fork"')
            .replace('base_branch = "main"', 'base_branch = "trunk"')
        )
        self.assertIn('remote = "fork"', policy_text)
        self.assertIn('base_branch = "trunk"', policy_text)
        policy_bytes = policy_text.encode("utf-8")
        candidate_policy.write_bytes(policy_bytes)

        candidate_hook = (
            self.scenario.repo / ".codex" / "git-hooks" / "pre-push"
        )
        hook_bytes = b"#!/bin/sh\nexit 0\n"
        candidate_hook.write_bytes(hook_bytes)
        candidate_hook.chmod(0o755)

        candidate_lock = (
            self.scenario.repo / ".codex" / "control-plane.lock"
        )
        lock_lines = candidate_lock.read_text(encoding="utf-8").splitlines()
        coordinated_digests = {
            "project_policy": (
                f"sha256:{sha256(policy_bytes).hexdigest()}"
            ),
            "git_pre_push": f"sha256:{sha256(hook_bytes).hexdigest()}",
        }
        rewritten: list[str] = []
        replaced: set[str] = set()
        for line in lock_lines:
            key = line.partition(" = ")[0]
            if key in coordinated_digests:
                rewritten.append(f'{key} = "{coordinated_digests[key]}"')
                replaced.add(key)
            else:
                rewritten.append(line)
        self.assertEqual(replaced, set(coordinated_digests))
        candidate_lock.write_text(
            "\n".join(rewritten) + "\n", encoding="utf-8"
        )

        head = git(self.scenario.repo, "rev-parse", "HEAD")
        remote_url = git(
            self.scenario.repo, "remote", "get-url", "--push", "origin"
        )
        result = subprocess.run(
            [str(installed_hook), "origin", remote_url],
            cwd=self.scenario.repo,
            input=(
                f"refs/heads/main {head} refs/heads/main {head}\n"
            ),
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("GG_BASE_PUSH", result.stdout)
        self.assertIn("GG_CANDIDATE_POLICY_DRIFT", result.stdout)
        self.assertNotIn("GG_REMOTE_UNVERIFIED", result.stdout)
        self.assertEqual(installed_hook.read_bytes(), installed_hook_before)

    def test_adoption_refuses_unmanaged_hook_config_or_default_hook(
        self,
    ) -> None:
        from control_plane.adoption import adoption_plan

        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", "custom-hooks"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        conflict = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        self.assertFalse(conflict["ok"])
        self.assertIn(
            "E_ADOPT_HOOK_PATH_CONFLICT", conflict["preflight_errors"]
        )
        subprocess.run(
            ["git", "config", "--local", "--unset-all", "core.hooksPath"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        default_hook = (
            Path(
                subprocess.run(
                    ["git", "rev-parse", "--git-common-dir"],
                    cwd=self.scenario.repo,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            )
            / "hooks"
            / "pre-commit"
        )
        if not default_hook.is_absolute():
            default_hook = self.scenario.repo / default_hook
        default_hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        default_hook.chmod(0o755)

        existing = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )

        self.assertFalse(existing["ok"])
        self.assertIn(
            "E_ADOPT_EXISTING_HOOKS", existing["preflight_errors"]
        )

    def test_installed_launcher_rejects_unmanifested_runtime_entry(
        self,
    ) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        install = Path(plan["installed_snapshot"]["path"])
        cache = (
            install
            / "codex_control_plane_runtime_v2"
            / "__pycache__"
        )
        cache.mkdir()
        (cache / "cli.cpython-311.pyc").write_bytes(
            b"untrusted-bytecode"
        )

        completed = subprocess.run(
            [
                str(install / "scripts" / "control-plane"),
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

        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "GG_INSTALLED_POLICY_INVALID: unmanifested artifact",
            completed.stderr,
        )

    def test_installed_launcher_and_hook_ignore_caller_path(self) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        install = Path(plan["installed_snapshot"]["path"])
        with tempfile.TemporaryDirectory() as temporary:
            shim_root = Path(temporary)
            marker = shim_root / "dirname-invoked"
            dirname = shim_root / "dirname"
            dirname.write_text(
                "#!/bin/sh\n"
                '/usr/bin/touch "$CONTROL_PLANE_PATH_MARKER"\n'
                'exec /usr/bin/dirname "$@"\n',
                encoding="utf-8",
            )
            dirname.chmod(0o755)
            environment = dict(os.environ)
            environment["PATH"] = str(shim_root)
            environment["CONTROL_PLANE_PATH_MARKER"] = str(marker)
            commands = (
                [
                    str(install / "scripts" / "control-plane"),
                    "git-guard",
                    "pre-commit",
                    "--repo",
                    str(self.scenario.repo),
                ],
                [str(install / "git-hooks" / "pre-commit")],
            )
            for command in commands:
                with self.subTest(command=command[0]):
                    marker.unlink(missing_ok=True)
                    completed = subprocess.run(
                        command,
                        cwd=self.scenario.repo,
                        env=environment,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertIn(completed.returncode, {0, 1, 2})
                    self.assertFalse(marker.exists())

    def test_adoption_reports_shared_common_hook_scope(self) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_rollback,
            adoption_status,
        )

        peer = self.scenario.root / "peer"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                "codex/adopt-peer",
                str(peer),
                "HEAD",
            ],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )

        self.assertIn(
            "W_ADOPT_SHARED_COMMON_HOOK_PATH", plan["warnings"]
        )
        adoption_apply(plan)
        self.assertIn(
            "W_ADOPT_SHARED_COMMON_HOOK_PATH",
            adoption_status(self.scenario.repo)["warnings"],
        )
        peer_hook = (
            Path(plan["installed_snapshot"]["hooks_path"]) / "pre-commit"
        )
        peer_guard = subprocess.run(
            [str(peer_hook)],
            cwd=peer,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(peer_guard.returncode, 0, peer_guard.stderr)
        adoption_rollback(self.scenario.repo)

    def test_global_include_and_worktree_hook_paths_block_without_mutation(
        self,
    ) -> None:
        from control_plane.adoption import adoption_plan

        with tempfile.TemporaryDirectory() as temporary:
            global_config = Path(temporary) / "global.gitconfig"
            included = Path(temporary) / "included.gitconfig"
            included.write_text(
                "[core]\n\thooksPath = inherited-hooks\n",
                encoding="utf-8",
            )
            global_config.write_text(
                f"[include]\n\tpath = {included}\n", encoding="utf-8"
            )
            with patch.dict(
                os.environ,
                {"GIT_CONFIG_GLOBAL": str(global_config)},
                clear=False,
            ):
                inherited = adoption_plan(
                    ROOT, self.scenario.repo, allow_dirty_source=True
                )
            self.assertFalse(inherited["ok"])
            self.assertIn(
                "E_ADOPT_HOOK_PATH_CONFLICT",
                inherited["preflight_errors"],
            )
            configured = subprocess.run(
                ["git", "config", "--local", "--get-all", "core.hooksPath"],
                cwd=self.scenario.repo,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(configured.returncode, 1)

        subprocess.run(
            ["git", "config", "--local", "extensions.worktreeConfig", "true"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "--worktree", "core.hooksPath", "worktree-hooks"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        worktree = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        self.assertFalse(worktree["ok"])
        self.assertIn(
            "E_ADOPT_HOOK_PATH_CONFLICT", worktree["preflight_errors"]
        )
        local = subprocess.run(
            ["git", "config", "--local", "--get-all", "core.hooksPath"],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(local.returncode, 1)

    def test_adoption_rejects_remote_url_with_embedded_credentials(
        self,
    ) -> None:
        from control_plane.adoption import adoption_plan

        for remote_url in (
            "https://placeholder-user:placeholder-token@example.invalid/repo.git",
            "https://example.invalid/repo.git?temporary-marker",
            "https://example.invalid/repo.git#temporary-marker",
            "ssh://placeholder-user:placeholder-token@example.invalid/repo.git",
            "ssh://git@example.invalid/repo.git?temporary-marker",
            "ssh://git@example.invalid/repo.git#temporary-marker",
        ):
            with self.subTest(shape=remote_url.split("example", 1)[0]):
                git(
                    self.scenario.repo,
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    remote_url,
                )
                with self.assertRaisesRegex(
                    ValueError, "E_ADOPT_REMOTE_CREDENTIALS"
                ):
                    adoption_plan(
                        ROOT,
                        self.scenario.repo,
                        allow_dirty_source=True,
                    )

    def test_config_fault_restores_files_snapshot_and_absent_local_value(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        plan = adoption.adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        original = adoption._set_local_hooks_path

        def set_then_fail(root: Path, value: str) -> None:
            original(root, value)
            raise OSError("injected config failure")

        with (
            patch(
                "control_plane.adoption._set_local_hooks_path",
                side_effect=set_then_fail,
            ),
            self.assertRaisesRegex(OSError, "injected config failure"),
        ):
            adoption.adoption_apply(plan)

        configured = subprocess.run(
            ["git", "config", "--local", "--get-all", "core.hooksPath"],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(configured.returncode, 1)
        self.assertFalse(Path(plan["installed_snapshot"]["path"]).exists())
        for change in plan["changes"]:
            if change["before_digest"] is None:
                self.assertFalse(
                    (self.scenario.repo / change["path"]).exists()
                )

    def test_snapshot_cleanup_fault_does_not_skip_other_compensations(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        plan = adoption.adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        original = adoption._set_local_hooks_path

        def set_then_fail(root: Path, value: str) -> None:
            original(root, value)
            raise OSError("injected config failure")

        with (
            patch(
                "control_plane.adoption._set_local_hooks_path",
                side_effect=set_then_fail,
            ),
            patch(
                "control_plane.adoption._remove_snapshot_tree",
                side_effect=OSError("injected snapshot removal failure"),
            ),
            self.assertRaises(Exception) as raised,
        ):
            adoption.adoption_apply(plan)

        self.assertIn("E_ADOPT_RECOVERY_FAILED", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, ExceptionGroup)
        self.assertIn(
            "injected config failure",
            repr(raised.exception.__cause__.exceptions[0]),
        )
        configured = subprocess.run(
            ["git", "config", "--local", "--get-all", "core.hooksPath"],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(configured.returncode, 1)
        for change in plan["changes"]:
            if change["before_digest"] is None:
                self.assertFalse(
                    (self.scenario.repo / change["path"]).exists(),
                    change["path"],
                )
        self.assertTrue(adoption._owner_pointer_path(self.scenario.repo).exists())

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
        installed_agents = (self.scenario.repo / "AGENTS.md").read_text(
            encoding="utf-8"
        )
        from control_plane.adoption import AGENTS_END, AGENTS_START

        managed_agents = installed_agents.split(AGENTS_START, 1)[1].split(
            AGENTS_END, 1
        )[0]
        self.assertNotIn("bash tests/run.sh", managed_agents)
        self.assertIn(
            "gates canónicos documentados por el repositorio objetivo",
            managed_agents,
        )
        self.assertIn(
            "scripts/control-plane policy-check", managed_agents
        )
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

    def test_render_agents_preserves_target_gate_outside_managed_block(
        self,
    ) -> None:
        from control_plane.adoption import (
            AGENTS_END,
            AGENTS_START,
            _render_agents,
        )

        target_text = (
            "# Target rules\n\n"
            "## Verification\n\n"
            "```bash\n"
            "bash tests/run.sh\n"
            "```\n"
        )
        (self.scenario.repo / "AGENTS.md").write_text(
            target_text, encoding="utf-8"
        )

        rendered = _render_agents(ROOT, self.scenario.repo).decode("utf-8")
        managed = rendered.split(AGENTS_START, 1)[1].split(
            AGENTS_END, 1
        )[0]

        self.assertTrue(rendered.startswith(target_text))
        self.assertIn("bash tests/run.sh", rendered.split(AGENTS_START, 1)[0])
        self.assertNotIn("bash tests/run.sh", managed)
        self.assertIn(
            "gates canónicos documentados por el repositorio objetivo",
            managed,
        )

    def test_render_agents_includes_self_contained_continuation_pointer(
        self,
    ) -> None:
        from control_plane.adoption import (
            AGENTS_END,
            AGENTS_START,
            _render_agents,
        )

        rendered = _render_agents(ROOT, self.scenario.repo).decode("utf-8")
        managed = rendered.split(AGENTS_START, 1)[1].split(
            AGENTS_END, 1
        )[0]
        normalized_managed = " ".join(managed.split())

        for field in (
            "- Escribe en:",
            "- Rol:",
            "- Para continuar:",
            "- Mensaje exacto:",
            "- Estado de partida:",
            "- No hacer todavía:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, managed)

        for rule in (
            "tarea padre u orquestadora como destino normal del usuario",
            "`este hilo` si el host no expone una identidad verificable",
            "identidad visible",
            "estado activo",
            "checkpoint completo",
            "Git no demuestra",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, normalized_managed)

        self.assertNotIn("templates/HANDOFF.md", managed)

    def test_adopted_cli_reports_local_validated_lease_as_unknown(self) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        launcher = self.scenario.repo / "scripts" / "control-plane"
        digest = "sha256:" + "a" * 64
        started = subprocess.run(
            [
                str(launcher),
                "task",
                "start",
                "--repo",
                str(self.scenario.repo),
                "--task-id",
                "TASK-ADOPTED-RISK",
                "--outcome",
                "local_change",
                "--branch",
                "codex/adopt-v2",
                "--task-digest",
                digest,
                "--decision-digest",
                digest,
                "--session-id",
                "session-adopted-risk",
                "--scope-path",
                ".",
                "--json",
            ],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        risk = subprocess.run(
            [
                str(launcher),
                "risk-status",
                "--repo",
                str(self.scenario.repo),
                "--task-id",
                "TASK-ADOPTED-RISK",
                "--lease-session-id",
                "session-adopted-risk",
                "--json",
            ],
            cwd=self.scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertNotIn("unrecognized arguments", risk.stderr)
        self.assertEqual(risk.returncode, 2, risk.stderr)
        payload = json.loads(risk.stdout)
        self.assertEqual(payload["status"], "UNKNOWN")
        dirty = next(
            item
            for item in payload["dimensions"]["local"]["checks"]
            if item["code"] == "RS_LOCAL_DIRTY"
        )
        self.assertEqual(dirty["status"], "UNKNOWN")
        self.assertEqual(dirty["facts"]["lease_coverage"], "local_validated")
        self.assertIsNone(dirty["facts"]["lease_valid"])

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

    def test_snapshot_parent_symlink_is_rejected_before_external_write(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        plan = adoption.adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        common = Path(plan["installed_snapshot"]["common_git_dir"])
        control_root = common / "codex-control-plane"
        control_root.mkdir()
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary).resolve()
            (control_root / "installs").symlink_to(
                external, target_is_directory=True
            )
            original = adoption._durable_replace_bytes
            escaped: list[Path] = []

            def reject_external_write(
                destination: Path,
                payload: bytes,
                *,
                suffix: str,
                expected_digest: str | None,
                mode: int,
            ) -> None:
                if destination.resolve().is_relative_to(external):
                    escaped.append(destination)
                    raise AssertionError("external snapshot write attempted")
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
                    side_effect=reject_external_write,
                ),
                self.assertRaisesRegex(
                    ValueError, "E_ADOPT_SNAPSHOT_DRIFT"
                ),
            ):
                adoption.adoption_apply(plan)

            self.assertEqual(escaped, [])

    def test_control_state_root_symlink_is_rejected_before_lock_write(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        plan = adoption.adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        common = Path(plan["installed_snapshot"]["common_git_dir"])
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary).resolve()
            (common / "codex-control-plane").symlink_to(
                external, target_is_directory=True
            )

            with self.assertRaisesRegex(
                ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
            ):
                adoption.adoption_apply(plan)

            self.assertEqual(list(external.iterdir()), [])

    def test_linked_worktree_state_symlink_is_rejected_before_wal_write(
        self,
    ) -> None:
        import control_plane.adoption as adoption
        from control_plane.repository import worktree_git_dir

        peer = self.scenario.root / "state-peer"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                "codex/state-peer",
                str(peer),
                "HEAD",
            ],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        owner_git_dir = worktree_git_dir(peer)
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary).resolve()
            (owner_git_dir / "codex-control-plane").symlink_to(
                external, target_is_directory=True
            )

            with self.assertRaisesRegex(
                ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
            ):
                adoption._begin_transaction(
                    peer, operation="adopt", records=[]
                )

            self.assertEqual(list(external.iterdir()), [])

    def test_linked_worktree_journal_symlink_is_rejected_before_read_or_write(
        self,
    ) -> None:
        import control_plane.adoption as adoption
        from control_plane.repository import worktree_git_dir

        peer = self.scenario.root / "journal-peer"
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                "codex/journal-peer",
                str(peer),
                "HEAD",
            ],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        plan = adoption.adoption_plan(
            ROOT, peer, allow_dirty_source=True
        )
        owner_git_dir = worktree_git_dir(peer)
        with tempfile.TemporaryDirectory() as temporary:
            external = Path(temporary).resolve()
            external_journal = external / "adoption.json"
            external_journal.write_text(
                '{"status":"preparing","records":[]}\n',
                encoding="utf-8",
            )
            before = external_journal.read_bytes()
            (owner_git_dir / "codex-control-plane").symlink_to(
                external, target_is_directory=True
            )

            with self.assertRaisesRegex(
                ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
            ):
                adoption.adoption_apply(plan)

            self.assertEqual(external_journal.read_bytes(), before)

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

    def test_rollback_config_drift_preflight_makes_zero_mutations(self) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
            adoption_rollback,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        subprocess.run(
            ["git", "config", "--local", "core.hooksPath", "tampered-hooks"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
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

        self.assertEqual(
            before,
            {
                path: (self.scenario.repo / path).read_bytes()
                for path in before
            },
        )
        self.assertTrue(Path(plan["installed_snapshot"]["path"]).is_dir())
        configured = subprocess.run(
            ["git", "config", "--local", "--get", "core.hooksPath"],
            cwd=self.scenario.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(configured.stdout.strip(), "tampered-hooks")

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
            lock_path = Path(temporary).resolve() / "adoption.lock"
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
                staged = scenario.repo / "baseline.txt.codex-new"
                if phase != "committed":
                    staged.write_bytes(b"partial\n")
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
                if phase != "committed":
                    self.assertFalse(staged.exists())
                self.assertFalse(_owner_pointer_path(peer).exists())

    def test_other_worktree_recovers_snapshot_and_git_config_after_crash(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        scenario, peer = self._recovery_scenario("external-state")
        plan = adoption.adoption_plan(
            ROOT, scenario.repo, allow_dirty_source=True
        )
        source = adoption.discover_repository(ROOT)
        target = adoption.discover_repository(scenario.repo)
        rendered = adoption._render_distribution(
            source, target, git_facts=plan["target_git"]
        )
        snapshot, files = adoption._installed_snapshot(
            source,
            target,
            source_commit=plan["source_commit"],
            git_facts=plan["target_git"],
            rendered=rendered,
        )
        change = plan["git_config_changes"][0]
        transaction = adoption._begin_transaction(
            target,
            operation="adopt",
            records=[],
            external_state={
                "git_config_change": change,
                "snapshot": {**snapshot, "created": True},
            },
        )
        adoption._publish_install_snapshot(target, snapshot, files)
        staging = Path(snapshot["staging_path"])
        staging.mkdir(mode=0o700)
        (staging / "partial").write_bytes(b"partial\n")
        adoption._set_local_hooks_path(target, change["planned_value"])
        adoption._advance_transaction(
            transaction,
            status="config_applied",
            state={"schema_version": 2, "status": "preparing"},
        )

        adoption._recover_owner_transaction(peer)

        configured = subprocess.run(
            ["git", "config", "--local", "--get-all", "core.hooksPath"],
            cwd=scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(configured.returncode, 1)
        self.assertFalse(Path(snapshot["path"]).exists())
        self.assertFalse(staging.exists())
        self.assertFalse(adoption._owner_pointer_path(peer).exists())

    def test_owner_recovery_attempts_external_compensation_after_file_failure(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        scenario, peer = self._recovery_scenario(
            "external-after-file-failure"
        )
        plan = adoption.adoption_plan(
            ROOT, scenario.repo, allow_dirty_source=True
        )
        source = adoption.discover_repository(ROOT)
        target = adoption.discover_repository(scenario.repo)
        rendered = adoption._render_distribution(
            source, target, git_facts=plan["target_git"]
        )
        snapshot, files = adoption._installed_snapshot(
            source,
            target,
            source_commit=plan["source_commit"],
            git_facts=plan["target_git"],
            rendered=rendered,
        )
        change = plan["git_config_changes"][0]
        transaction = adoption._begin_transaction(
            target,
            operation="adopt",
            records=[],
            external_state={
                "git_config_change": change,
                "snapshot": {**snapshot, "created": True},
            },
        )
        adoption._publish_install_snapshot(target, snapshot, files)
        adoption._set_local_hooks_path(target, change["planned_value"])
        adoption._advance_transaction(
            transaction,
            status="config_applied",
            state={"schema_version": 2, "status": "preparing"},
        )

        with (
            patch(
                "control_plane.adoption._restore_records",
                side_effect=OSError("injected record recovery failure"),
            ),
            self.assertRaisesRegex(
                ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
            ),
        ):
            adoption._recover_owner_transaction(peer)

        configured = subprocess.run(
            ["git", "config", "--local", "--get-all", "core.hooksPath"],
            cwd=scenario.repo,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(configured.returncode, 1)
        self.assertFalse(Path(snapshot["path"]).exists())
        self.assertTrue(adoption._owner_pointer_path(peer).exists())

    def test_crash_before_commit_marker_restores_absent_adopt_journal(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        plan = adoption.adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        with (
            patch(
                "control_plane.adoption._commit_transaction",
                side_effect=RuntimeError("injected pre-commit-marker crash"),
            ),
            self.assertRaisesRegex(
                RuntimeError, "injected pre-commit-marker crash"
            ),
        ):
            adoption.adoption_apply(plan)

        self.assertEqual(
            adoption.adoption_status(self.scenario.repo)["status"],
            "applied",
        )
        retried = adoption.adoption_apply(plan)

        self.assertTrue(retried["ok"])
        self.assertTrue(adoption.adoption_verify(self.scenario.repo)["ok"])

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

    def test_owner_pointer_symlink_is_rejected_before_read(self) -> None:
        from control_plane.adoption import (
            _begin_transaction,
            _owner_pointer_path,
            _recover_owner_transaction,
        )

        scenario, peer = self._recovery_scenario("owner-pointer-symlink")
        _begin_transaction(
            scenario.repo,
            operation="adopt",
            records=[],
        )
        pointer_path = _owner_pointer_path(peer)
        external_pointer = peer / "outside-owner-pointer.json"
        external_pointer.write_bytes(pointer_path.read_bytes())
        pointer_path.unlink()
        pointer_path.symlink_to(external_pointer)

        with self.assertRaisesRegex(
            ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
        ):
            _recover_owner_transaction(peer)

        self.assertTrue(pointer_path.is_symlink())

    def test_owner_pointer_is_opened_nonblocking(self) -> None:
        import control_plane.adoption as adoption

        pointer_path = adoption._owner_pointer_path(self.scenario.repo)
        pointer_path.write_text("{}\n", encoding="utf-8")
        original_open = adoption.os.open
        observed_flags: list[int] = []

        def inspect_open(path: object, flags: int, *args: object, **kwargs: object):
            if Path(path) == pointer_path:
                observed_flags.append(flags)
            return original_open(path, flags, *args, **kwargs)

        with (
            patch("control_plane.adoption.os.open", side_effect=inspect_open),
            self.assertRaisesRegex(
                ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
            ),
        ):
            adoption._recover_owner_transaction(self.scenario.repo)

        self.assertEqual(len(observed_flags), 1)
        self.assertTrue(observed_flags[0] & os.O_NONBLOCK)

    def test_owner_pointer_rejects_fifo_directory_and_oversize(self) -> None:
        import control_plane.adoption as adoption

        for case in ("fifo", "directory", "oversize"):
            with self.subTest(case=case):
                scenario = GitScenario()
                self.addCleanup(scenario.close)
                scenario.checkout_feature(f"codex/owner-pointer-{case}")
                pointer_path = adoption._owner_pointer_path(scenario.repo)
                if case == "fifo":
                    os.mkfifo(pointer_path)
                elif case == "directory":
                    pointer_path.mkdir()
                else:
                    pointer_path.write_bytes(b"x" * 65_537)

                with self.assertRaisesRegex(
                    ValueError, "E_ADOPT_RECOVERY_UNKNOWN"
                ):
                    adoption._recover_owner_transaction(scenario.repo)

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
        launcher_marker = (
            self.scenario.repo / "isolated-launcher-stdlib-shadow-executed"
        )
        hook_marker = (
            self.scenario.repo / "isolated-hook-stdlib-shadow-executed"
        )
        (self.scenario.repo / "argparse.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(launcher_marker)!r}).write_text('executed')\n"
            "raise RuntimeError('ARGPARSE_SHADOW_EXECUTED')\n",
            encoding="utf-8",
        )
        (self.scenario.repo / "json.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(hook_marker)!r}).write_text('executed')\n"
            "raise RuntimeError('JSON_SHADOW_EXECUTED')\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.scenario.repo)

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
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"ok": true', completed.stdout.lower())
        hook = subprocess.run(
            [
                "python3",
                "-I",
                "-B",
                str(
                    self.scenario.repo
                    / ".codex"
                    / "hooks"
                    / "control_plane_hook.py"
                ),
            ],
            cwd=self.scenario.repo,
            env=environment,
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
        self.assertIn("CONTROL PLANE RISK", hook.stdout)
        self.assertNotIn("TOP_LEVEL_SHADOW_IMPORTED", completed.stderr)
        self.assertNotIn("TOP_LEVEL_SHADOW_IMPORTED", hook.stderr)
        self.assertFalse(launcher_marker.exists())
        self.assertFalse(hook_marker.exists())
        self.assertNotIn("ARGPARSE_SHADOW_EXECUTED", completed.stderr)
        self.assertNotIn("JSON_SHADOW_EXECUTED", hook.stderr)
        for module in (
            "host_bridge.py",
            "intake.py",
            "risk_sentinel.py",
            "scopes.py",
        ):
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

        risk = subprocess.run(
            [
                str(self.scenario.repo / "scripts" / "control-plane"),
                "risk-status",
                "--repo",
                str(self.scenario.repo),
                "--json",
            ],
            cwd=self.scenario.repo,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(risk.returncode, 1, risk.stderr)
        risk_payload = json.loads(risk.stdout)
        self.assertEqual(risk_payload["status"], "FAIL")
        self.assertEqual(
            next(
                item["status"]
                for item in risk_payload["dimensions"]["local"]["checks"]
                if item["code"] == "RS_LOCAL_DIRTY"
            ),
            "FAIL",
        )
        self.assertEqual(
            risk_payload["facts"]["governing_policy_source"],
            "installed_manifest",
        )

    def test_isolated_safe_read_matches_source_decisions(self) -> None:
        from control_plane.adoption import adoption_apply, adoption_plan
        from control_plane.hooks import _safe_read_rg_executable

        plan = adoption_plan(
            ROOT,
            self.scenario.repo,
            base_branch="main",
            allow_dirty_source=True,
        )
        adoption_apply(plan)
        installed = self.scenario.repo / "scripts" / "control-plane"
        pilot = (self.scenario.repo / "pilot.md").resolve()
        pilot.write_text("Closed pilot charter.\n", encoding="utf-8")
        rg_expected = 1 if _safe_read_rg_executable() is not None else 126

        for argv, expected in (
            (("git", "diff", "--check"), 0),
            (("git", "status", "--porcelain"), 126),
            (
                (
                    "rg",
                    "--no-config",
                    "--quiet",
                    "-e",
                    "not-present",
                    "--",
                    str(pilot),
                ),
                rg_expected,
            ),
            (("secret-scan-governing", "--", str(pilot)), 1),
        ):
            with self.subTest(argv=argv):
                source = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "control_plane.cli",
                        "safe-read",
                        "--repo",
                        str(self.scenario.repo.resolve()),
                        "--",
                        *argv,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                isolated = subprocess.run(
                    [
                        str(installed),
                        "safe-read",
                        "--repo",
                        str(self.scenario.repo.resolve()),
                        "--",
                        *argv,
                    ],
                    cwd=self.scenario.repo,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(source.returncode, expected, source.stderr)
                self.assertEqual(
                    isolated.returncode, expected, isolated.stderr
                )
                self.assertEqual(source.stdout, isolated.stdout)
                self.assertEqual(source.stderr, isolated.stderr)

    def test_pr_b_adopted_runtime_imports_and_renders_intake_without_source(
        self,
    ) -> None:
        from control_plane.adoption import (
            adoption_apply,
            adoption_plan,
        )

        plan = adoption_plan(
            ROOT, self.scenario.repo, allow_dirty_source=True
        )
        adoption_apply(plan)
        imported = self._run_isolated_intake(self.scenario.repo)
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
        self.assertEqual(imported.stdout.strip(), "ISOLATED_INTAKE_OK")
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
            installed_intake = (
                self.scenario.repo
                / ".codex"
                / "runtime"
                / "codex_control_plane_runtime_v2"
                / "intake.py"
            )
            self.assertTrue(installed_intake.is_file())
            isolated = self._run_isolated_intake(self.scenario.repo)
            self.assertEqual(isolated.returncode, 0, isolated.stderr)
            self.assertEqual(
                isolated.stdout.strip(), "ISOLATED_INTAKE_OK"
            )
            self.assertTrue(adoption_verify(self.scenario.repo)["ok"])
            self.assertTrue(adoption_rollback(self.scenario.repo)["ok"])
            self.assertFalse(
                Path(plan["installed_snapshot"]["path"]).exists()
            )
            self.assertFalse(
                Path(upgrade["installed_snapshot"]["path"]).exists()
            )
            configured = subprocess.run(
                ["git", "config", "--local", "--get-all", "core.hooksPath"],
                cwd=self.scenario.repo,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(configured.returncode, 1)

    def test_upgrade_config_fault_restores_prior_snapshot_and_config(
        self,
    ) -> None:
        import control_plane.adoption as adoption

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
            subprocess.run(
                ["git", "config", "user.name", "Control Plane Tests"],
                cwd=source,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "user.email",
                    "control-plane@example.invalid",
                ],
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
                ["git", "commit", "-m", "test: source initial"],
                cwd=source,
                check=True,
                capture_output=True,
            )
            first = adoption.adoption_plan(source, self.scenario.repo)
            adoption.adoption_apply(first)
            subprocess.run(
                ["git", "add", "."],
                cwd=self.scenario.repo,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "test: adoption initial"],
                cwd=self.scenario.repo,
                check=True,
                capture_output=True,
            )
            profile = source / "docs" / "profiles" / "generic.md"
            profile.write_text(
                profile.read_text(encoding="utf-8")
                + "\nFAULT-INJECTION-UPGRADE\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "add", "."],
                cwd=source,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "test: source upgrade"],
                cwd=source,
                check=True,
                capture_output=True,
            )
            upgrade = adoption.upgrade_plan(source, self.scenario.repo)
            old_snapshot = Path(first["installed_snapshot"]["path"])
            new_snapshot = Path(upgrade["installed_snapshot"]["path"])
            old_hooks = first["installed_snapshot"]["hooks_path"]
            original = adoption._set_local_hooks_path

            def set_then_fail(root: Path, value: str) -> None:
                original(root, value)
                raise OSError("injected upgrade config failure")

            with (
                patch(
                    "control_plane.adoption._set_local_hooks_path",
                    side_effect=set_then_fail,
                ),
                self.assertRaisesRegex(
                    OSError, "injected upgrade config failure"
                ),
            ):
                adoption.upgrade_apply(upgrade)

            configured = subprocess.run(
                ["git", "config", "--local", "--get", "core.hooksPath"],
                cwd=self.scenario.repo,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(configured.stdout.strip(), old_hooks)
            self.assertTrue(old_snapshot.is_dir())
            self.assertFalse(new_snapshot.exists())
            self.assertTrue(adoption.adoption_verify(self.scenario.repo)["ok"])

    def test_upgrade_faults_restore_exact_files_snapshot_and_config(
        self,
    ) -> None:
        import control_plane.adoption as adoption

        def snapshot_tree(path: Path) -> dict[str, tuple[bytes, int]]:
            return {
                item.relative_to(path).as_posix(): (
                    item.read_bytes(),
                    item.stat().st_mode & 0o777,
                )
                for item in sorted(path.rglob("*"))
                if item.is_file()
            }

        for fault in (
            "managed-file-replace",
            "snapshot-publish",
            "snapshot-verify",
        ):
            with (
                self.subTest(fault=fault),
                tempfile.TemporaryDirectory() as temporary,
            ):
                source = Path(temporary) / "source"
                shutil.copytree(
                    ROOT,
                    source,
                    ignore=shutil.ignore_patterns(
                        ".git", "__pycache__", "*.pyc"
                    ),
                )
                subprocess.run(
                    ["git", "init", "-b", "main", str(source)],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "config", "user.name", "Control Plane Tests"],
                    cwd=source,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "config",
                        "user.email",
                        "control-plane@example.invalid",
                    ],
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
                    ["git", "commit", "-m", "test: source initial"],
                    cwd=source,
                    check=True,
                    capture_output=True,
                )

                target = GitScenario()
                self.addCleanup(target.close)
                target.checkout_feature(f"codex/upgrade-{fault}")
                first = adoption.adoption_plan(source, target.repo)
                adoption.adoption_apply(first)
                git(target.repo, "add", ".")
                git(target.repo, "commit", "-m", "test: adoption initial")

                profile = source / "docs" / "profiles" / "generic.md"
                profile.write_text(
                    profile.read_text(encoding="utf-8")
                    + f"\nUPGRADE-{fault}\n",
                    encoding="utf-8",
                )
                subprocess.run(
                    ["git", "add", "."],
                    cwd=source,
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "commit", "-m", f"test: {fault} upgrade"],
                    cwd=source,
                    check=True,
                    capture_output=True,
                )

                upgrade = adoption.upgrade_plan(source, target.repo)
                old_snapshot = Path(first["installed_snapshot"]["path"])
                new_snapshot = Path(upgrade["installed_snapshot"]["path"])
                self.assertTrue(old_snapshot.is_dir())
                self.assertFalse(new_snapshot.exists())
                old_snapshot_before = snapshot_tree(old_snapshot)
                config_before = git(
                    target.repo,
                    "config",
                    "--local",
                    "--get-all",
                    "core.hooksPath",
                ).splitlines()
                targets_before: dict[
                    str, tuple[bytes, int] | None
                ] = {}
                for change in upgrade["changes"]:
                    relative = str(change["path"])
                    path = target.repo / relative
                    targets_before[relative] = (
                        (path.read_bytes(), path.stat().st_mode & 0o777)
                        if path.is_file()
                        else None
                    )

                if fault == "managed-file-replace":
                    original_replace = adoption._durable_replace_bytes
                    injected = False

                    def replace_then_fail(
                        destination: Path,
                        payload: bytes,
                        *,
                        suffix: str,
                        expected_digest: str | None,
                        mode: int,
                    ) -> None:
                        nonlocal injected
                        original_replace(
                            destination,
                            payload,
                            suffix=suffix,
                            expected_digest=expected_digest,
                            mode=mode,
                        )
                        if suffix == ".codex-upgrade" and not injected:
                            injected = True
                            raise OSError(
                                "injected upgrade file replacement failure"
                            )

                    fault_patch = patch(
                        "control_plane.adoption._durable_replace_bytes",
                        side_effect=replace_then_fail,
                    )
                    expected_error = (
                        OSError,
                        "injected upgrade file replacement failure",
                    )
                elif fault == "snapshot-publish":
                    original_publish = adoption._publish_install_snapshot
                    injected = False

                    def publish_then_fail(
                        target_root: Path,
                        snapshot,
                        files,
                    ) -> bool:
                        nonlocal injected
                        original_publish(target_root, snapshot, files)
                        injected = True
                        raise OSError(
                            "injected upgrade snapshot publication failure"
                        )

                    fault_patch = patch(
                        "control_plane.adoption._publish_install_snapshot",
                        side_effect=publish_then_fail,
                    )
                    expected_error = (
                        OSError,
                        "injected upgrade snapshot publication failure",
                    )
                else:
                    original_snapshot_valid = adoption._snapshot_is_valid
                    validation_calls = 0
                    injected = False

                    def fail_final_snapshot_verification(
                        target_root: Path,
                        snapshot,
                    ) -> bool:
                        nonlocal injected, validation_calls
                        valid = original_snapshot_valid(
                            target_root, snapshot
                        )
                        if (
                            str(snapshot.get("path")) == str(new_snapshot)
                            and new_snapshot.exists()
                        ):
                            validation_calls += 1
                            if validation_calls == 2:
                                injected = True
                                return False
                        return valid

                    fault_patch = patch(
                        "control_plane.adoption._snapshot_is_valid",
                        side_effect=fail_final_snapshot_verification,
                    )
                    expected_error = (ValueError, "E_UPGRADE_VERIFY")

                with (
                    fault_patch,
                    self.assertRaisesRegex(*expected_error),
                ):
                    adoption.upgrade_apply(upgrade)

                self.assertTrue(injected, f"{fault} was not reached")
                self.assertEqual(
                    git(
                        target.repo,
                        "config",
                        "--local",
                        "--get-all",
                        "core.hooksPath",
                    ).splitlines(),
                    config_before,
                )
                self.assertEqual(
                    snapshot_tree(old_snapshot), old_snapshot_before
                )
                self.assertFalse(new_snapshot.exists())
                for relative, expected in targets_before.items():
                    path = target.repo / relative
                    observed = (
                        (path.read_bytes(), path.stat().st_mode & 0o777)
                        if path.is_file()
                        else None
                    )
                    self.assertEqual(
                        observed,
                        expected,
                        f"{fault} did not restore {relative}",
                    )
                status = adoption.adoption_status(target.repo)
                self.assertEqual(status["status"], "applied")
                self.assertEqual(status["plan_id"], first["plan_id"])
                self.assertTrue(adoption.adoption_verify(target.repo)["ok"])

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
