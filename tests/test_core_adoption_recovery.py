from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

import control_plane.adoption_recovery as recovery
from control_plane.adoption_recovery import adoption_rollback, adoption_status, adoption_verify
from control_plane.project_profiles import detect_project_profile
from tests.git_test_support import FIXTURE_POLICY
from tests.test_core_task_state import git, make_repo


def digest(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def install_snapshot(repo: Path, *, label: str = "current") -> dict[str, object]:
    common = Path(
        git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    staging = common / "codex-control-plane" / f"snapshot-fixture-{label}"
    artifacts = {
        "policy": ("policy/project-policy.toml", FIXTURE_POLICY.read_bytes(), 0o600),
        "lock": ("control-plane.lock", b"fixture-lock\n", 0o600),
        "runtime_entrypoint": ("scripts/control-plane", b"#!/bin/sh\nexit 0\n", 0o700),
        "runtime_module": ("control_plane/__init__.py", b"", 0o600),
        "hook_pre_commit": ("git-hooks/pre-commit", b"#!/bin/sh\nexit 0\n", 0o700),
        "hook_pre_push": ("git-hooks/pre-push", b"#!/bin/sh\nexit 0\n", 0o700),
    }
    inventory: list[dict[str, object]] = []
    for role, (relative, payload, mode) in artifacts.items():
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(mode)
        inventory.append(
            {"role": role, "path": relative, "digest": digest(payload), "mode": mode}
        )
    head = git(repo, "rev-parse", "HEAD")
    manifest = {
        "schema_version": 1,
        "repository_identity": str(common),
        "common_git_dir": str(common),
        "source_commit": head,
        "governing_base_commit": head,
        "install_invocation_id": f"core-adoption-recovery-test-{label}",
        "git": {
            "base_branch": "main",
            "remote_name": "origin",
            "remote_url_digest": digest(b"https://example.invalid/control-plane.git"),
            "remote_repository": "example/control-plane",
        },
        "artifacts": sorted(inventory, key=lambda item: str(item["path"])),
    }
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    manifest_digest = digest(manifest_bytes)
    installs = common / "codex-control-plane" / "installs"
    installs.mkdir(parents=True, exist_ok=True)
    for private in (common / "codex-control-plane", installs):
        private.chmod(0o700)
    install = installs / manifest_digest
    staging.rename(install)
    manifest_path = install / "manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    manifest_path.chmod(0o600)
    artifact_digests = {
        relative: digest(payload)
        for relative, payload in {
            **{item[0]: item[1] for item in artifacts.values()},
            "manifest.json": manifest_bytes,
        }.items()
    }
    return {
        "manifest_digest": manifest_digest,
        "common_git_dir": str(common),
        "path": str(install),
        "staging_path": str(installs / f".{manifest_digest}.staging"),
        "hooks_path": str(install / "git-hooks"),
        "artifact_digests": artifact_digests,
    }


def modern_journal(
    repo: Path,
    *,
    records: list[dict[str, object]],
    initial_config: list[str] | None = None,
    created_snapshot: bool = True,
    optional: dict[str, object] | None = None,
) -> tuple[Path, dict[str, object]]:
    git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
    control = git_dir / "codex-control-plane"
    control.mkdir(exist_ok=True)
    control.chmod(0o700)
    snapshot = install_snapshot(repo)
    state: dict[str, object] = {
        "schema_version": 2,
        "status": "applied",
        "plan_id": digest(b"plan"),
        "source_commit": git(repo, "rev-parse", "HEAD"),
        "source_manifest_digest": digest(b"source-manifest"),
        "records": records,
        "created_directories": [],
        "installed_snapshot": snapshot,
        "snapshot_records": [{**snapshot, "created": created_snapshot}],
        "git_config_changes": [
            {"key": "core.hooksPath", "planned_value": snapshot["hooks_path"]}
        ],
        "initial_git_config_values": [] if initial_config is None else initial_config,
        "warnings": [],
    }
    if optional:
        state.update(optional)
    journal = control / "adoption.json"
    journal.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    journal.chmod(0o600)
    git(repo, "config", "--local", "core.hooksPath", str(snapshot["hooks_path"]))
    return journal, state


class CoreAdoptionRecoveryTests(unittest.TestCase):
    def test_core_recovery_runtime_contains_no_unreachable_rollback_mutators(self) -> None:
        source = Path(recovery.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_functions = {
            "_atomic_json",
            "_durable_copy",
            "_restore_config",
            "_remove_created_directories",
            "_remove_snapshot_contents",
            "_remove_snapshot_tree",
            "_rollback_with_proven_quiescence",
            "_recovery_lock",
        }
        definitions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(forbidden_functions.isdisjoint(definitions))
        forbidden_attributes = {"mkdir", "write_bytes", "write_text", "unlink", "rmdir", "replace", "chmod", "fchmod", "fsync"}
        observed_attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        self.assertTrue(forbidden_attributes.isdisjoint(observed_attributes))
        self.assertNotIn("--replace-all", source)
        self.assertNotIn("--unset-all", source)

    def test_status_validates_closed_journal_and_never_echoes_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            managed = repo / "managed.txt"
            installed = b"installed\n"
            managed.write_bytes(installed)
            journal, state = modern_journal(
                repo,
                records=[
                    {
                        "path": "managed.txt",
                        "before_digest": None,
                        "installed_digest": digest(installed),
                        "backup": None,
                    }
                ],
            )

            valid = adoption_status(repo)
            self.assertEqual(
                valid,
                {
                    "schema_version": 2,
                    "command": "adopt-status",
                    "ok": True,
                    "status": "applied",
                    "plan_id": state["plan_id"],
                    "errors": [],
                    "authorizes": False,
                },
            )

            marker = "private-journal-value-must-not-be-echoed"
            state["unexpected"] = marker
            journal.write_text(
                json.dumps(state, sort_keys=True) + "\n", encoding="utf-8"
            )
            journal.chmod(0o600)
            rejected = adoption_status(repo)
            self.assertEqual(rejected["ok"], False)
            self.assertEqual(rejected["status"], "UNKNOWN")
            self.assertEqual(
                rejected["errors"][0]["code"], "E_ADOPT_ROLLBACK_SCHEMA"
            )
            self.assertNotIn(marker, json.dumps(rejected, sort_keys=True))
            self.assertEqual(rejected["authorizes"], False)

            for raw, expected_code in (
                (b'{"schema_version":2,"marker":"private-broken-json"',
                 "E_ADOPT_RECOVERY_UNKNOWN"),
                (b'{"schema_version":1,"marker":"private-old-schema"}\n',
                 "E_ADOPT_ROLLBACK_SCHEMA"),
            ):
                with self.subTest(expected_code=expected_code):
                    journal.write_bytes(raw)
                    journal.chmod(0o600)
                    malformed = adoption_status(repo)
                    self.assertFalse(malformed["ok"])
                    self.assertEqual(malformed["status"], "UNKNOWN")
                    self.assertEqual(malformed["errors"][0]["code"], expected_code)
                    self.assertNotIn("private-", json.dumps(malformed, sort_keys=True))
                    self.assertFalse(malformed["authorizes"])

    def test_created_directory_traversal_is_bounded_and_accepts_small_tree(self) -> None:
        for scenario in ("small", "too-many"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                managed = repo / "generated" / "managed.txt"
                managed.parent.mkdir()
                managed.write_text("installed\n", encoding="utf-8")
                managed.chmod(0o644)
                journal, state = modern_journal(
                    repo,
                    records=[
                        {
                            "path": "generated/managed.txt",
                            "before_digest": None,
                            "installed_digest": digest(b"installed\n"),
                            "backup": None,
                        }
                    ],
                )
                state["created_directories"] = ["generated"]
                count = 4 if scenario == "small" else recovery._MAX_CREATED_DESCENDANTS + 1
                for index in range(count):
                    (managed.parent / f"empty-{index:05d}").mkdir()
                journal.write_text(
                    json.dumps(state, sort_keys=True) + "\n", encoding="utf-8"
                )
                journal.chmod(0o600)
                before = (managed.read_bytes(), journal.read_bytes())

                result = adoption_verify(repo)
                if scenario == "small":
                    self.assertTrue(result["ok"], result)
                else:
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["errors"][0]["code"], "E_ADOPT_BOUNDS")
                self.assertEqual((managed.read_bytes(), journal.read_bytes()), before)

    def test_created_directory_traversal_does_not_follow_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = make_repo(root / "repo")
            managed = repo / "generated" / "managed.txt"
            managed.parent.mkdir()
            managed.write_text("installed\n", encoding="utf-8")
            managed.chmod(0o644)
            journal, state = modern_journal(
                repo,
                records=[
                    {
                        "path": "generated/managed.txt",
                        "before_digest": None,
                        "installed_digest": digest(b"installed\n"),
                        "backup": None,
                    }
                ],
            )
            state["created_directories"] = ["generated"]
            outside = root / "outside"
            outside.mkdir()
            (outside / "sentinel").write_text("outside\n", encoding="utf-8")
            (managed.parent / "linked").symlink_to(outside, target_is_directory=True)
            journal.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
            journal.chmod(0o600)

            result = adoption_verify(repo)

            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "E_ADOPT_DRIFT")
            self.assertEqual((outside / "sentinel").read_text(encoding="utf-8"), "outside\n")

    def test_rollback_fails_closed_when_legacy_quiescence_cannot_be_proven(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            (repo / "ios" / "App.xcodeproj").mkdir(parents=True)
            (repo / "ios" / "App.xcodeproj" / "project.pbxproj").write_text(
                "fixture\n", encoding="utf-8"
            )
            (repo / "functions").mkdir()
            (repo / "firebase.json").write_text("{}\n", encoding="utf-8")
            (repo / "functions" / "package.json").write_text("{}\n", encoding="utf-8")
            profile = detect_project_profile(repo)
            self.assertEqual(profile["kind"], "hybrid")
            self.assertEqual(profile["profiles"], ["ios", "saas_backend"])
            self.assertFalse(profile["truncated"])
            managed = repo / "managed.txt"
            managed.write_text("installed\n", encoding="utf-8")
            managed.chmod(0o644)
            journal, _ = modern_journal(
                repo,
                records=[
                    {
                        "path": "managed.txt",
                        "before_digest": None,
                        "installed_digest": digest(b"installed\n"),
                        "backup": None,
                    }
                ],
            )
            before = (managed.read_bytes(), journal.read_bytes())

            with self.assertRaisesRegex(ValueError, "E_ADOPT_QUIESCENCE_UNKNOWN"):
                adoption_rollback(repo)

            self.assertEqual((managed.read_bytes(), journal.read_bytes()), before)
            self.assertFalse((journal.parent / "locks" / "adoption.lock").exists())

    def test_existing_install_verifies_but_rollback_preserves_every_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            managed = repo / "scripts" / "control-plane"
            managed.parent.mkdir()
            before = b"#!/bin/sh\necho old\n"
            installed = b"#!/bin/sh\necho installed\n"
            managed.write_bytes(installed)
            managed.chmod(0o755)
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            backup = git_dir / "codex-control-plane" / "backups" / "scripts" / "control-plane"
            backup.parent.mkdir(parents=True)
            (git_dir / "codex-control-plane").chmod(0o700)
            (git_dir / "codex-control-plane" / "backups").chmod(0o700)
            (git_dir / "codex-control-plane" / "backups" / "scripts").chmod(0o700)
            backup.parent.chmod(0o700)
            backup.write_bytes(before)
            backup.chmod(0o640)
            journal, state = modern_journal(
                repo,
                records=[
                    {
                        "path": "scripts/control-plane",
                        "before_digest": digest(before),
                        "installed_digest": digest(installed),
                        "backup": "codex-control-plane/backups/scripts/control-plane",
                    }
                ],
            )
            snapshot = Path(str(state["installed_snapshot"]["path"]))  # type: ignore[index]
            self.assertEqual(adoption_status(repo)["status"], "applied")
            self.assertTrue(adoption_verify(repo)["ok"])
            observed_before = (
                managed.read_bytes(),
                managed.stat().st_mode & 0o777,
                journal.read_bytes(),
                snapshot.is_dir(),
                git(repo, "config", "--local", "--get-all", "core.hooksPath"),
            )
            with self.assertRaisesRegex(ValueError, "E_ADOPT_QUIESCENCE_UNKNOWN"):
                adoption_rollback(repo)
            self.assertEqual(
                (
                    managed.read_bytes(),
                    managed.stat().st_mode & 0o777,
                    journal.read_bytes(),
                    snapshot.is_dir(),
                    git(repo, "config", "--local", "--get-all", "core.hooksPath"),
                ),
                observed_before,
            )

    def test_drift_blocks_rollback_without_mutating_any_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            managed = repo / "managed.txt"
            managed.write_text("drift\n", encoding="utf-8")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            journal, _ = modern_journal(
                repo,
                records=[
                    {
                        "path": "managed.txt",
                        "before_digest": None,
                        "installed_digest": digest(b"installed\n"),
                        "backup": None,
                    }
                ],
            )
            before = tuple((path, path.read_bytes()) for path in (managed, journal))
            with self.assertRaisesRegex(ValueError, "E_ADOPT_DRIFT"):
                adoption_rollback(repo)
            self.assertEqual(tuple((path, path.read_bytes()) for path in (managed, journal)), before)

    def test_quiescence_block_precedes_every_git_config_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = make_repo(base / "repo")
            managed = repo / "scripts" / "control-plane"
            managed.parent.mkdir()
            before = b"#!/bin/sh\necho old\n"
            installed = b"#!/bin/sh\necho installed\n"
            managed.write_bytes(installed)
            managed.chmod(0o755)
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            control = git_dir / "codex-control-plane"
            backup = control / "backups" / "scripts" / "control-plane"
            backup.parent.mkdir(parents=True)
            for parent in (control, control / "backups", control / "backups" / "scripts", backup.parent):
                parent.chmod(0o700)
            backup.write_bytes(before)
            backup.chmod(0o640)
            prior_hooks = str(base / "prior-hooks")
            journal, state = modern_journal(
                repo,
                records=[
                    {
                        "path": "scripts/control-plane",
                        "before_digest": digest(before),
                        "installed_digest": digest(installed),
                        "backup": "codex-control-plane/backups/scripts/control-plane",
                    }
                ],
                initial_config=[prior_hooks],
            )
            installed_hooks = str(state["installed_snapshot"]["hooks_path"])  # type: ignore[index]
            real_run = recovery.subprocess.run

            mutating_calls: list[list[str]] = []

            def observe_config(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                if "--replace-all" in argv or "--unset-all" in argv:
                    mutating_calls.append(argv)
                return real_run(argv, **kwargs)

            with patch.object(recovery.subprocess, "run", side_effect=observe_config):
                with self.assertRaisesRegex(ValueError, "E_ADOPT_QUIESCENCE_UNKNOWN"):
                    adoption_rollback(repo)

            self.assertEqual(mutating_calls, [])
            self.assertEqual(json.loads(journal.read_text())["status"], "applied")
            self.assertEqual(managed.read_bytes(), installed)
            self.assertEqual(
                git(repo, "config", "--local", "--get-all", "core.hooksPath"),
                installed_hooks,
            )

    def test_legacy_writer_interleaving_cannot_open_a_rollback_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            managed = repo / "managed.txt"
            managed.write_text("installed\n", encoding="utf-8")
            managed.chmod(0o644)
            journal, _ = modern_journal(
                repo,
                records=[
                    {
                        "path": "managed.txt",
                        "before_digest": None,
                        "installed_digest": digest(b"installed\n"),
                        "backup": None,
                    }
                ],
            )
            before = (managed.read_bytes(), journal.read_bytes())
            preflight_done = threading.Event()
            continue_recovery = threading.Event()
            original_preflight = recovery._preflight
            outcome: list[BaseException] = []

            def paused_preflight(*args: object, **kwargs: object) -> None:
                original_preflight(*args, **kwargs)
                preflight_done.set()
                self.assertTrue(continue_recovery.wait(timeout=5))

            def roll_back() -> None:
                try:
                    adoption_rollback(repo)
                except BaseException as error:
                    outcome.append(error)

            with patch.object(recovery, "_preflight", side_effect=paused_preflight):
                worker = threading.Thread(target=roll_back)
                worker.start()
                self.assertTrue(preflight_done.wait(timeout=5))
                git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
                legacy_tasks = git_dir / "codex-control-plane" / "tasks"
                legacy_tasks.mkdir(parents=True, exist_ok=True)
                (legacy_tasks / "TASK-INTERLEAVED.json").write_text(
                    '{"state":"implementing"}\n', encoding="utf-8"
                )
                continue_recovery.set()
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(outcome), 1)
            self.assertIn("E_ADOPT_QUIESCENCE_UNKNOWN", str(outcome[0]))
            self.assertEqual((managed.read_bytes(), journal.read_bytes()), before)

    def test_pre_snapshot_schema_is_observable_but_verify_and_rollback_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            managed = repo / "managed.txt"
            managed.write_text("installed\n", encoding="utf-8")
            managed.chmod(0o644)
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            control = git_dir / "codex-control-plane"
            control.mkdir(mode=0o700)
            journal = control / "adoption.json"
            journal.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "status": "applied",
                        "records": [
                            {
                                "path": "managed.txt",
                                "before_digest": None,
                                "installed_digest": digest(b"installed\n"),
                                "backup": None,
                            }
                        ],
                        "git_config_changes": [],
                        "initial_git_config_values": [],
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            journal.chmod(0o600)
            before = (managed.read_bytes(), journal.read_bytes())

            status = adoption_status(repo)
            self.assertFalse(status["ok"])
            self.assertEqual(status["status"], "UNKNOWN")
            self.assertEqual(
                status["errors"][0]["code"], "E_ADOPT_ROLLBACK_SCHEMA"
            )
            verification = adoption_verify(repo)
            self.assertFalse(verification["ok"])
            self.assertEqual(verification["status"], "UNKNOWN")
            self.assertEqual(verification["errors"][0]["code"], "E_ADOPT_ROLLBACK_SCHEMA")
            with self.assertRaisesRegex(ValueError, "E_ADOPT_ROLLBACK_SCHEMA"):
                adoption_rollback(repo)

            self.assertEqual((managed.read_bytes(), journal.read_bytes()), before)
            self.assertFalse((control / "locks").exists())

    def test_v2_1_optional_fields_are_compatible(self) -> None:
        variants = (
            {"warnings": [{"code": "W_TEST", "message": "fixture"}]},
            {
                "warnings": [],
                "upgrade_history": [
                    {
                        "from_plan_id": digest(b"old-plan"),
                        "to_plan_id": digest(b"plan"),
                        "backup_stamp": "fixture-stamp",
                    }
                ],
            },
        )
        for index, optional in enumerate(variants):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                managed = repo / "managed.txt"
                managed.write_text("installed\n", encoding="utf-8")
                managed.chmod(0o644)
                modern_journal(
                    repo,
                    records=[
                        {
                            "path": "managed.txt",
                            "before_digest": None,
                            "installed_digest": digest(b"installed\n"),
                            "backup": None,
                        }
                    ],
                    optional=optional,
                )
                self.assertTrue(adoption_verify(repo)["ok"])

    def test_installed_file_mode_and_snapshot_pointer_drift_are_mutation_free(self) -> None:
        for fault in ("managed-mode", "snapshot-pointer"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                repo = make_repo(base / "repo")
                managed = repo / "scripts" / "control-plane"
                managed.parent.mkdir()
                managed.write_bytes(b"#!/bin/sh\nexit 0\n")
                managed.chmod(0o755)
                journal, state = modern_journal(
                    repo,
                    records=[
                        {
                            "path": "scripts/control-plane",
                            "before_digest": None,
                            "installed_digest": digest(managed.read_bytes()),
                            "backup": None,
                        }
                    ],
                )
                outside = base / "outside-snapshot"
                outside.mkdir()
                (outside / "sentinel").write_text("outside\n", encoding="utf-8")
                if fault == "managed-mode":
                    managed.chmod(0o644)
                else:
                    state["installed_snapshot"]["path"] = str(outside)  # type: ignore[index]
                    journal.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
                    journal.chmod(0o600)
                before = (
                    managed.read_bytes(),
                    managed.stat().st_mode & 0o777,
                    journal.read_bytes(),
                    (outside / "sentinel").read_bytes(),
                )

                self.assertFalse(adoption_verify(repo)["ok"])
                with self.assertRaisesRegex(ValueError, "E_ADOPT_(DRIFT|ROLLBACK_SCHEMA)"):
                    adoption_rollback(repo)

                self.assertEqual(
                    (
                        managed.read_bytes(),
                        managed.stat().st_mode & 0o777,
                        journal.read_bytes(),
                        (outside / "sentinel").read_bytes(),
                    ),
                    before,
                )

    def test_quiescence_block_preserves_all_snapshot_generations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            managed = repo / "managed.txt"
            managed.write_text("installed\n", encoding="utf-8")
            managed.chmod(0o644)
            previous = install_snapshot(repo, label="previous")
            journal, state = modern_journal(
                repo,
                records=[
                    {
                        "path": "managed.txt",
                        "before_digest": None,
                        "installed_digest": digest(b"installed\n"),
                        "backup": None,
                    }
                ],
            )
            current = dict(state["installed_snapshot"])  # type: ignore[arg-type]
            state["snapshot_records"] = [
                {**previous, "created": False},
                {**current, "created": True},
            ]
            journal.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
            journal.chmod(0o600)

            self.assertTrue(adoption_verify(repo)["ok"])
            journal_before = journal.read_bytes()
            with self.assertRaisesRegex(ValueError, "E_ADOPT_QUIESCENCE_UNKNOWN"):
                adoption_rollback(repo)
            self.assertEqual(journal.read_bytes(), journal_before)
            self.assertTrue(Path(str(current["path"])).is_dir())
            self.assertTrue(Path(str(previous["path"])).is_dir())


if __name__ == "__main__":
    unittest.main()
