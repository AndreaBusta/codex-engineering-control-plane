from __future__ import annotations

from hashlib import sha1, sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest

from tests.git_test_support import GitScenario, git


ROOT = Path(__file__).parents[1]
RUNBOOK = ROOT / "docs" / "engineering" / "11-lifecycle-hooks-adoption.md"


def _run(
    command: list[str],
    *,
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        input=input_text,
        check=False,
        capture_output=True,
        text=True,
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | str, int]]:
    snapshot: dict[str, tuple[str, bytes | str, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        mode = path.lstat().st_mode & 0o777
        if path.is_symlink():
            snapshot[relative.as_posix()] = (
                "symlink",
                os.readlink(path),
                mode,
            )
        elif path.is_file():
            snapshot[relative.as_posix()] = (
                "file",
                path.read_bytes(),
                mode,
            )
        elif path.is_dir():
            snapshot[relative.as_posix()] = ("directory", b"", mode)
    return snapshot


def _hook_config(root: Path) -> tuple[int, str]:
    result = _run(
        ["git", "config", "--local", "--get-all", "core.hooksPath"],
        cwd=root,
    )
    return result.returncode, result.stdout


class SupportedAdoptionRunbookContractTests(unittest.TestCase):
    def test_runbook_defines_one_supported_reversible_sequence(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        required_in_order = (
            "## Recorrido soportado v2.1 para un proyecto nuevo",
            "checkout limpia del Control Plane",
            "release-source.json",
            "`adopt plan`",
            "revisar el JSON",
            "`adopt apply`",
            "`adopt verify`",
            "gates reales del proyecto",
            "`/hooks`",
            "`adopt rollback`",
            "restauración exacta",
            "volver a ejecutar `adopt plan`",
            "Pull Request del proyecto",
            "`upgrade plan`",
            "`upgrade apply`",
        )
        cursor = -1
        for item in required_in_order:
            with self.subTest(item=item):
                cursor = text.index(item, cursor + 1)

        for boundary in (
            "no instala dependencias",
            "no modifica el remote",
            "no instala workflows de CI",
            "no publica ni despliega",
            "no revierte commits Git",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, text)


class SupportedAdoptionAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._source_temporary = tempfile.TemporaryDirectory()
        cls.source_seed = Path(cls._source_temporary.name) / "source-seed"
        shutil.copytree(
            ROOT,
            cls.source_seed,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", ".DS_Store"
            ),
        )
        subprocess.run(
            ["git", "init", "-b", "main", str(cls.source_seed)],
            check=True,
            capture_output=True,
            text=True,
        )
        git(cls.source_seed, "config", "user.name", "Control Plane Tests")
        git(
            cls.source_seed,
            "config",
            "user.email",
            "control-plane@example.invalid",
        )
        git(cls.source_seed, "add", ".")
        git(cls.source_seed, "commit", "-m", "test: source v2.1")
        cls.source_remote = Path(cls._source_temporary.name) / "source-remote.git"
        source_remote = _run(
            [
                "git",
                "init",
                "--bare",
                "--initial-branch=main",
                str(cls.source_remote),
            ],
            cwd=Path(cls._source_temporary.name),
        )
        if source_remote.returncode != 0:
            raise AssertionError(source_remote.stderr)
        git(cls.source_seed, "remote", "add", "origin", str(cls.source_remote))
        git(cls.source_seed, "push", "-u", "origin", "main")
        git(
            cls.source_seed,
            "remote",
            "set-url",
            "origin",
            "https://github.com/AndreaBusta/codex-engineering-control-plane.git",
        )
        cls.release_output = Path(cls._source_temporary.name) / "release-output"
        built = _run(
            [
                sys.executable,
                str(cls.source_seed / "scripts" / "build-release-candidate"),
                "--repo",
                str(cls.source_seed),
                "--output-dir",
                str(cls.release_output),
                "--workflow-url",
                "https://github.com/AndreaBusta/"
                "codex-engineering-control-plane/actions/runs/123456/attempts/1",
            ],
            cwd=cls.source_seed,
        )
        if built.returncode != 0:
            raise AssertionError(built.stdout + built.stderr)
        archive = cls.release_output / "codex-engineering-control-plane-2.1.1.tar.gz"
        cls.release_extract = Path(cls._source_temporary.name) / "release-extract"
        cls.release_extract.mkdir()
        with tarfile.open(archive, mode="r:gz") as packaged:
            packaged.extractall(cls.release_extract, filter="data")
        cls.release_source = (
            cls.release_extract / "codex-engineering-control-plane-2.1.1"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._source_temporary.cleanup()

    def _clone_source(self, destination: Path) -> Path:
        source = destination / "control-plane-source"
        result = _run(
            [
                "git",
                "clone",
                "--quiet",
                "--no-hardlinks",
                str(self.source_seed),
                str(source),
            ],
            cwd=destination,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        git(source, "config", "user.name", "Control Plane Tests")
        git(source, "config", "user.email", "control-plane@example.invalid")
        self.assertEqual(git(source, "status", "--porcelain"), "")
        return source

    def _source_cli(
        self,
        source: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return _run(
            [str(source / "scripts" / "control-plane"), *arguments],
            cwd=source,
        )

    def _json_cli(
        self,
        source: Path,
        *arguments: str,
    ) -> dict[str, object]:
        result = self._source_cli(source, *arguments)
        self.assertEqual(
            result.returncode,
            0,
            result.stdout + result.stderr,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload.get("ok"), payload)
        return payload

    def _write_markers(self, target: Path, markers: dict[str, str]) -> None:
        for relative, content in markers.items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def _task_payload(self, label: str, profiles: tuple[str, ...]) -> dict:
        return {
            "schema_version": 1,
            "task_id": f"SUPPORTED-ADOPTION-{label.upper()}",
            "objective": f"Audit the adopted {label} project profiles.",
            "intent": "audit",
            "phase": "research",
            "requested_outcome": "answer",
            "goals": [
                {
                    "id": f"audit-{label}",
                    "summary": "Apply every detected quality profile.",
                    "domains": list(profiles),
                    "depends_on": [],
                }
            ],
            "domains": list(profiles),
            "signals": ["cross_system", "regression_risk"],
            "scope_paths": ["baseline.txt"],
            "risk": {
                "uncertainty": 0,
                "blast_radius": 2,
                "irreversibility": 0,
                "verification_complexity": 2,
            },
            "risk_provenance": "model_inference",
            "effects": [
                {"name": "local_read", "source": "user_explicit"}
            ],
            "explicit_resources": [],
            "excluded_resources": [],
        }

    def _run_isolated_target(
        self,
        *,
        source: Path,
        target: Path,
        task_path: Path,
    ) -> dict[str, object]:
        hidden_source = source.with_name(source.name + ".hidden")
        source.rename(hidden_source)
        try:
            launcher = target / "scripts" / "control-plane"
            doctor = _run(
                [str(launcher), "doctor", "--repo", str(target), "--json"],
                cwd=target,
            )
            self.assertEqual(
                doctor.returncode,
                0,
                doctor.stdout + doctor.stderr,
            )
            self.assertTrue(json.loads(doctor.stdout)["ok"])
            routed = _run(
                [
                    str(launcher),
                    "route",
                    "--repo",
                    str(target),
                    "--task",
                    str(task_path),
                    "--mode",
                    "audit",
                    "--json",
                ],
                cwd=target,
            )
            self.assertEqual(
                routed.returncode,
                0,
                routed.stdout + routed.stderr,
            )
            return json.loads(routed.stdout)
        finally:
            hidden_source.rename(source)

    def _assert_supported_flow(
        self,
        *,
        target: Path,
        source: Path,
        label: str,
        expected_kind: str,
        expected_profiles: tuple[str, ...],
        expected_resources: tuple[str, ...],
    ) -> None:
        before_tree = _tree_snapshot(target)
        before_status = git(target, "status", "--porcelain")
        before_hook_config = _hook_config(target)
        before_head = git(target, "rev-parse", "HEAD")
        before_refs = git(target, "show-ref")
        before_remote = git(
            target, "remote", "get-url", "--push", "origin"
        )
        control_state = Path(
            git(
                target,
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "codex-control-plane",
            )
        )
        self.assertFalse(control_state.exists())
        state_root = target.parent / f"{label}-acceptance"
        state_root.mkdir(exist_ok=True)

        plan_result = self._source_cli(
            source,
            "adopt",
            "plan",
            "--source",
            str(source),
            "--target",
            str(target),
            "--json",
        )
        self.assertEqual(
            plan_result.returncode,
            0,
            plan_result.stdout + plan_result.stderr,
        )
        plan = json.loads(plan_result.stdout)
        self.assertTrue(plan["ok"], plan)
        self.assertFalse(plan["source_dirty"])
        self.assertTrue(str(plan["plan_id"]).startswith("sha256:"))
        self.assertEqual(plan["target_git"]["remote"], "origin")
        self.assertEqual(plan["target_git"]["base_branch"], "main")
        self.assertEqual(_tree_snapshot(target), before_tree)
        self.assertEqual(git(target, "status", "--porcelain"), before_status)
        self.assertEqual(_hook_config(target), before_hook_config)
        self.assertEqual(git(target, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git(target, "show-ref"), before_refs)
        self.assertEqual(
            git(target, "remote", "get-url", "--push", "origin"),
            before_remote,
        )
        self.assertFalse(control_state.exists())

        plan_path = state_root / "adoption-plan.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._json_cli(
            source,
            "adopt",
            "apply",
            "--plan",
            str(plan_path),
            "--json",
        )
        status = self._json_cli(
            source,
            "adopt",
            "status",
            "--target",
            str(target),
            "--json",
        )
        self.assertEqual(status["status"], "applied")
        self.assertEqual(status["plan_id"], plan["plan_id"])
        self.assertFalse((target / ".github" / "workflows").exists())
        self._json_cli(
            source,
            "adopt",
            "verify",
            "--target",
            str(target),
            "--json",
        )

        with (target / ".codex" / "project-policy.toml").open("rb") as handle:
            policy = tomllib.load(handle)
        self.assertEqual(policy["project_kind"], expected_kind)

        task_path = state_root / "task.json"
        task_path.write_text(
            json.dumps(
                self._task_payload(label, expected_profiles),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        decision = self._run_isolated_target(
            source=source,
            target=target,
            task_path=task_path,
        )

        self.assertTrue(decision["ok"], decision)
        self.assertEqual(
            decision["summary"]["project_profile"]["profiles"],
            list(expected_profiles),
        )
        required = set(decision["summary"]["required"])
        self.assertTrue(set(expected_resources).issubset(required), decision)

        hooks_path = git(
            target, "config", "--local", "--get", "core.hooksPath"
        )
        self.assertTrue(Path(hooks_path).is_absolute())
        pre_commit = _run([str(Path(hooks_path) / "pre-commit")], cwd=target)
        self.assertEqual(
            pre_commit.returncode,
            0,
            pre_commit.stdout + pre_commit.stderr,
        )
        base_head = git(target, "rev-parse", "refs/remotes/origin/main")
        remote_url = git(target, "remote", "get-url", "--push", "origin")
        pre_push = _run(
            [str(Path(hooks_path) / "pre-push"), "origin", remote_url],
            cwd=target,
            input_text=(
                f"refs/heads/main {base_head} refs/heads/main {base_head}\n"
            ),
        )
        self.assertEqual(pre_push.returncode, 1)
        self.assertIn("GG_BASE_PUSH", pre_push.stdout)

        self._json_cli(
            source,
            "adopt",
            "rollback",
            "--target",
            str(target),
            "--json",
        )
        self.assertEqual(_tree_snapshot(target), before_tree)
        self.assertEqual(git(target, "status", "--porcelain"), before_status)
        self.assertEqual(_hook_config(target), before_hook_config)
        self.assertEqual(git(target, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git(target, "show-ref"), before_refs)
        self.assertEqual(
            git(target, "remote", "get-url", "--push", "origin"),
            before_remote,
        )

        second_plan = self._json_cli(
            source,
            "adopt",
            "plan",
            "--source",
            str(source),
            "--target",
            str(target),
            "--json",
        )
        second_plan_path = state_root / "second-adoption-plan.json"
        second_plan_path.write_text(
            json.dumps(second_plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(second_plan["plan_id"], plan["plan_id"])
        self._json_cli(
            source,
            "adopt",
            "apply",
            "--plan",
            str(second_plan_path),
            "--json",
        )
        git(target, "add", ".")
        adoption_commit = git(
            target, "commit", "-m", "test: adopt Control Plane v2.1"
        )
        self.assertIn("adopt Control Plane v2.1", adoption_commit)
        adopted_head = git(target, "rev-parse", "HEAD")

        upgrade_marker = f"SUPPORTED-UPGRADE-{label.upper()}"
        profile = source / "docs" / "profiles" / "generic.md"
        profile.write_text(
            profile.read_text(encoding="utf-8")
            + f"\n{upgrade_marker}\n",
            encoding="utf-8",
        )
        git(source, "add", str(profile.relative_to(source)))
        git(source, "commit", "-m", f"test: upgrade {label} source")
        upgrade_plan = self._json_cli(
            source,
            "upgrade",
            "plan",
            "--source",
            str(source),
            "--target",
            str(target),
            "--json",
        )
        self.assertEqual(
            upgrade_plan["from_plan_id"], second_plan["plan_id"]
        )
        upgrade_path = state_root / "upgrade-plan.json"
        upgrade_path.write_text(
            json.dumps(upgrade_plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._json_cli(
            source,
            "upgrade",
            "apply",
            "--plan",
            str(upgrade_path),
            "--json",
        )
        self._json_cli(
            source,
            "adopt",
            "verify",
            "--target",
            str(target),
            "--json",
        )
        installed_profile = (
            target
            / "docs"
            / "codex-control-plane"
            / "profiles"
            / "generic.md"
        )
        self.assertIn(
            upgrade_marker,
            installed_profile.read_text(encoding="utf-8"),
        )
        upgraded_decision = self._run_isolated_target(
            source=source,
            target=target,
            task_path=task_path,
        )
        self.assertEqual(
            upgraded_decision["summary"]["project_profile"]["profiles"],
            list(expected_profiles),
        )
        self._json_cli(
            source,
            "adopt",
            "rollback",
            "--target",
            str(target),
            "--json",
        )
        self.assertEqual(_tree_snapshot(target), before_tree)
        self.assertEqual(_hook_config(target), before_hook_config)
        self.assertEqual(git(target, "rev-parse", "HEAD"), adopted_head)
        self.assertNotEqual(git(target, "status", "--porcelain"), "")

    def test_new_generic_repository_supported_flow(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/adopt-generic")
        source = self._clone_source(scenario.root)
        self._assert_supported_flow(
            target=scenario.repo,
            source=source,
            label="generic",
            expected_kind="generic",
            expected_profiles=("generic",),
            expected_resources=("document.profile-generic",),
        )

    def test_ios_marker_repository_supported_flow(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        self._write_markers(
            scenario.repo,
            {
                "SampleApp.xcodeproj/project.pbxproj": "// fixture\n",
                "SampleApp/Info.plist": "fixture\n",
            },
        )
        git(scenario.repo, "add", ".")
        git(scenario.repo, "commit", "-m", "test: add iOS markers")
        git(scenario.repo, "push", "origin", "main")
        scenario.checkout_feature("codex/adopt-ios")
        source = self._clone_source(scenario.root)
        self._assert_supported_flow(
            target=scenario.repo,
            source=source,
            label="ios",
            expected_kind="ios",
            expected_profiles=("ios",),
            expected_resources=("document.profile-ios",),
        )

    def test_bustafit_hybrid_isolated_clone_supported_flow(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        self._write_markers(
            scenario.repo,
            {
                "ios/App.xcodeproj/project.pbxproj": "// fixture\n",
                "android/settings.gradle": "rootProject.name='BUSTAFIT'\n",
                "android/app/src/main/AndroidManifest.xml": "<manifest />\n",
                "vite.config.js": "export default {}\n",
                "src/sw.js": "// service worker\n",
                "firebase.json": "{}\n",
                "functions/package.json": "{}\n",
            },
        )
        git(scenario.repo, "add", ".")
        git(scenario.repo, "commit", "-m", "test: add BUSTAFIT markers")
        git(scenario.repo, "push", "origin", "main")

        target = scenario.root / "bustafit-isolated-clone"
        cloned = _run(
            ["git", "clone", "--quiet", str(scenario.remote), str(target)],
            cwd=scenario.root,
        )
        self.assertEqual(cloned.returncode, 0, cloned.stderr)
        git(target, "config", "user.name", "Control Plane Tests")
        git(target, "config", "user.email", "control-plane@example.invalid")
        git(target, "switch", "-c", "codex/adopt-bustafit")
        source = self._clone_source(scenario.root)
        self._assert_supported_flow(
            target=target,
            source=source,
            label="bustafit",
            expected_kind="hybrid",
            expected_profiles=(
                "android",
                "ios",
                "saas_backend",
                "web_pwa",
            ),
            expected_resources=(
                "document.profile-android",
                "document.profile-ios",
                "document.profile-saas-backend",
                "document.profile-web-pwa",
            ),
        )

    def test_extracted_release_tree_is_a_reversible_adoption_source(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/adopt-release-tree")
        source = self.release_source
        target = scenario.repo
        self.assertTrue((source / ".codex" / "release-source.json").is_file())
        self.assertFalse((source / ".git").exists())
        before_tree = _tree_snapshot(target)
        before_status = git(target, "status", "--porcelain")
        before_hook_config = _hook_config(target)
        before_head = git(target, "rev-parse", "HEAD")
        marker = json.loads(
            (source / ".codex" / "release-source.json").read_text(
                encoding="utf-8"
            )
        )

        plan_result = self._source_cli(
            source,
            "adopt",
            "plan",
            "--source",
            str(source),
            "--target",
            str(target),
            "--json",
        )
        self.assertEqual(
            plan_result.returncode,
            0,
            plan_result.stdout + plan_result.stderr,
        )
        plan = json.loads(plan_result.stdout)
        self.assertTrue(plan["ok"], plan)
        self.assertFalse(plan["source_dirty"])
        self.assertEqual(plan["source_commit"], marker["source_commit"])
        self.assertEqual(_tree_snapshot(target), before_tree)

        state_root = scenario.root / "release-tree-acceptance"
        state_root.mkdir()
        plan_path = state_root / "plan.json"
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._json_cli(
            source,
            "adopt",
            "apply",
            "--plan",
            str(plan_path),
            "--json",
        )
        launcher = target / "scripts" / "control-plane"
        verified = _run(
            [str(launcher), "adopt", "verify", "--target", str(target), "--json"],
            cwd=target,
        )
        self.assertEqual(
            verified.returncode,
            0,
            verified.stdout + verified.stderr,
        )
        self.assertTrue(json.loads(verified.stdout)["ok"])
        rolled_back = _run(
            [
                str(launcher),
                "adopt",
                "rollback",
                "--target",
                str(target),
                "--json",
            ],
            cwd=target,
        )
        self.assertEqual(
            rolled_back.returncode,
            0,
            rolled_back.stdout + rolled_back.stderr,
        )
        self.assertTrue(json.loads(rolled_back.stdout)["ok"])
        self.assertEqual(_tree_snapshot(target), before_tree)
        self.assertEqual(git(target, "status", "--porcelain"), before_status)
        self.assertEqual(_hook_config(target), before_hook_config)
        self.assertEqual(git(target, "rev-parse", "HEAD"), before_head)

    def test_extracted_release_tree_tampering_fails_closed(self) -> None:
        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/adopt-tampered-release-tree")
        target = scenario.repo
        before_tree = _tree_snapshot(target)
        before_hook_config = _hook_config(target)

        def changed_bytes(source: Path) -> None:
            profile = source / "docs" / "profiles" / "generic.md"
            profile.write_bytes(profile.read_bytes() + b"tampered\n")

        def recomputed_file_record(source: Path) -> None:
            profile = source / "docs" / "profiles" / "generic.md"
            profile.write_bytes(profile.read_bytes() + b"coherent-tamper\n")
            payload = profile.read_bytes()
            marker = source / ".codex" / "release-source.json"
            document = json.loads(marker.read_text(encoding="utf-8"))
            record = next(
                item
                for item in document["entries"]
                if item["path"] == "docs/profiles/generic.md"
            )
            blob = sha1(usedforsecurity=False)
            blob.update(f"blob {len(payload)}\0".encode("ascii"))
            blob.update(payload)
            record["git_oid"] = blob.hexdigest()
            record["sha256"] = f"sha256:{sha256(payload).hexdigest()}"
            record["size_bytes"] = len(payload)
            marker.write_text(
                json.dumps(document, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        def extra_file(source: Path) -> None:
            (source / "unmanifested.txt").write_text("extra\n", encoding="utf-8")

        def extra_empty_directory(source: Path) -> None:
            (source / "unmanifested-empty-directory").mkdir()

        def changed_mode(source: Path) -> None:
            profile = source / "docs" / "profiles" / "generic.md"
            profile.chmod(profile.stat().st_mode | 0o111)

        def unknown_marker_key(source: Path) -> None:
            marker = source / ".codex" / "release-source.json"
            payload = json.loads(marker.read_text(encoding="utf-8"))
            payload["trusted"] = True
            marker.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        def forged_commit(source: Path) -> None:
            marker = source / ".codex" / "release-source.json"
            payload = json.loads(marker.read_text(encoding="utf-8"))
            payload["source_commit"] = "0" * 40
            marker.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        def forged_tree(source: Path) -> None:
            marker = source / ".codex" / "release-source.json"
            payload = json.loads(marker.read_text(encoding="utf-8"))
            payload["source_tree"] = "0" * 40
            marker.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        def linked_marker(source: Path) -> None:
            marker = source / ".codex" / "release-source.json"
            marker.unlink()
            marker.symlink_to(
                self.release_source / ".codex" / "release-source.json"
            )

        for label, mutate in (
            ("bytes", changed_bytes),
            ("recomputed-file-record", recomputed_file_record),
            ("extra", extra_file),
            ("extra-directory", extra_empty_directory),
            ("mode", changed_mode),
            ("schema", unknown_marker_key),
            ("forged-commit", forged_commit),
            ("forged-tree", forged_tree),
            ("symlink", linked_marker),
        ):
            with self.subTest(label=label):
                source = scenario.root / f"tampered-{label}"
                shutil.copytree(self.release_source, source)
                mutate(source)
                result = self._source_cli(
                    source,
                    "adopt",
                    "plan",
                    "--source",
                    str(source),
                    "--target",
                    str(target),
                    "--json",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "E_ADOPT_RELEASE_SOURCE",
                    result.stdout + result.stderr,
                )
                self.assertEqual(_tree_snapshot(target), before_tree)
                self.assertEqual(_hook_config(target), before_hook_config)


if __name__ == "__main__":
    unittest.main()
