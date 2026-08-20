from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import adoption_enablement.repository as repository

from adoption_enablement.contracts import validate_plan
from adoption_enablement.manifest import (
    CORE_RUNTIME_MODULES,
    MANAGED_SOURCE_PATHS,
    build_source_manifest,
    build_target_projection,
    preview,
    render_target_lock,
)
from adoption_enablement.repository import observe_target
from tests.adoption_enablement_test_support import (
    git,
    initialize_fresh_target,
    initialize_full_source,
    metadata_snapshot,
    write_file,
)


ROOT = Path(__file__).resolve().parents[1]


class AdoptionPreviewTests(unittest.TestCase):
    def test_source_manifest_is_the_exact_core_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = initialize_full_source(Path(directory) / "source", ROOT)

            manifest = build_source_manifest(source)

            self.assertEqual(
                tuple(record["path"] for record in manifest["records"]),
                tuple(sorted(MANAGED_SOURCE_PATHS)),
            )
            self.assertEqual(len(CORE_RUNTIME_MODULES), 27)
            self.assertEqual(CORE_RUNTIME_MODULES.count("stable_pause.py"), 1)
            self.assertEqual(CORE_RUNTIME_MODULES.count("survey.py"), 1)
            self.assertIn("control_plane/stable_pause.py", MANAGED_SOURCE_PATHS)
            self.assertIn("control_plane/survey.py", MANAGED_SOURCE_PATHS)
            self.assertNotIn(".codex/control-plane.lock", MANAGED_SOURCE_PATHS)
            self.assertIs(manifest["authorizes"], False)

    def test_source_manifest_rejects_extra_or_drifted_runtime_bytes(self) -> None:
        cases = ("extra", "drift")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                source = initialize_full_source(Path(directory) / "source", ROOT)
                if case == "extra":
                    write_file(source, "control_plane/attacker.py", "raise SystemExit(9)\n")
                else:
                    path = source / "control_plane" / "contracts.py"
                    path.write_bytes(path.read_bytes() + b"\n# drift\n")
                git(source, "add", "--all")
                git(source, "commit", "-m", case)

                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_SOURCE_(?:MODULES|RUNTIME)"):
                    build_source_manifest(source)

    def test_target_lock_is_generated_in_memory_for_target_authority_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            observation = observe_target(target, authority_source=source)

            payload = render_target_lock(source, observation)
            lock = tomllib.loads(payload.decode("utf-8"))

            self.assertEqual(lock["digests"]["project_policy"], observation.policy_digest)
            self.assertEqual(lock["digests"]["resource_registry"], observation.registry_digest)
            self.assertFalse((target / ".codex" / "control-plane.lock").exists())

    def test_target_projection_renders_operational_git_hooks_and_binds_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")

            projection = build_target_projection(
                source,
                observe_target(target, authority_source=source),
            )
            lock = tomllib.loads(projection.target_lock.decode("utf-8"))

            for relative, digest_key in (
                (".codex/git-hooks/pre-commit", "git_pre_commit"),
                (".codex/git-hooks/pre-push", "git_pre_push"),
            ):
                payload = projection.payloads[relative]
                self.assertNotIn(b"__CONTROL_PLANE_ENTRYPOINT__", payload)
                self.assertIn(b'"$repo/scripts/control-plane"', payload)
                self.assertEqual(
                    lock["digests"][digest_key],
                    "sha256:" + sha256(payload).hexdigest(),
                )

    def test_preview_is_path_safe_and_zero_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            source_before = metadata_snapshot(source)
            target_before = metadata_snapshot(target)

            plan = preview(source, target)

            self.assertEqual(validate_plan(plan), ())
            self.assertEqual(source_before, metadata_snapshot(source))
            self.assertEqual(target_before, metadata_snapshot(target))
            serialized = json.dumps(plan, sort_keys=True)
            self.assertNotIn(str(source), serialized)
            self.assertNotIn(str(target), serialized)
            self.assertNotIn("AGENTS.md", tuple(record["path"] for record in plan["managed_records"]))
            self.assertNotIn(
                ".codex/project-policy.toml",
                tuple(record["path"] for record in plan["managed_records"]),
            )
            self.assertNotIn(
                ".codex/resource-registry.toml",
                tuple(record["path"] for record in plan["managed_records"]),
            )
            self.assertIn(
                ".codex/control-plane.lock",
                tuple(record["path"] for record in plan["managed_records"]),
            )
            self.assertIs(plan["authorizes"], False)
            self.assertIs(plan["mutation"], False)

    def test_preview_rejects_source_or_target_drift_during_convergence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            original = observe_target(target, authority_source=source)
            drifted = copy.copy(original)
            object.__setattr__(drifted, "head", "f" * 40)

            with patch(
                "adoption_enablement.manifest.observe_target",
                side_effect=(original, drifted),
            ):
                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_DRIFT"):
                    preview(source, target)

    def test_target_authority_uses_the_exact_selected_source_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "selected-source", ROOT)
            target = initialize_fresh_target(container / "target")
            commands: list[tuple[str, ...]] = []
            run_closed = repository._run_closed_command

            def observe_command(arguments: object, **keywords: object) -> bytes:
                command = tuple(str(item) for item in arguments)  # type: ignore[arg-type]
                if command and command[0].endswith("/scripts/control-plane"):
                    commands.append(command)
                return run_closed(arguments, **keywords)  # type: ignore[arg-type]

            with patch.object(
                repository,
                "_run_closed_command",
                side_effect=observe_command,
            ):
                preview(source, target)

            self.assertTrue(commands)
            self.assertEqual(
                {command[0] for command in commands},
                {str(source / "scripts" / "control-plane")},
            )


if __name__ == "__main__":
    unittest.main()
