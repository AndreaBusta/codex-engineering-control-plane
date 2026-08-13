from __future__ import annotations

import copy
import contextlib
import io
import json
import os
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
PIN = "dd237283dbfe466e11bd4be55acf14ecb8f6636e"
LOCATOR = f"plugin://local-superpowers/{PIN}"


def _local_superpowers_resource(revision: str) -> dict[str, object]:
    from control_plane.resource_registry import load_registry

    registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
    resource = copy.deepcopy(
        next(
            item
            for item in registry["resources"]
            if item["id"] == "plugin.superpowers-local"
        )
    )
    resource["locator"] = f"plugin://local-superpowers/{revision}"
    return resource


def _temporary_git_repository(root: Path) -> str:
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(root),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    for arguments in (
        ("init", "-q", str(root)),
        ("-C", str(root), "config", "user.name", "Core Test"),
        ("-C", str(root), "config", "user.email", "core-test@example.invalid"),
    ):
        subprocess.run(
            ["/usr/bin/git", *arguments],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=5.0,
        )
    (root / "tracked.txt").write_text("clean\n", encoding="utf-8")
    for arguments in (
        ("-C", str(root), "add", "tracked.txt"),
        ("-C", str(root), "commit", "-q", "-m", "fixture"),
    ):
        subprocess.run(
            ["/usr/bin/git", *arguments],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=5.0,
        )
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=5.0,
    )
    return completed.stdout.decode("ascii").strip()


class CorePluginTests(unittest.TestCase):
    def test_registry_has_one_exact_canonical_superpowers_pin(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
        resources = [
            item
            for item in registry["resources"]
            if item.get("capabilities") == ["workflow.superpowers"]
        ]

        self.assertEqual(validate_registry(registry), [])
        self.assertEqual(len(resources), 1)
        self.assertEqual(
            resources[0],
            {
                "id": "plugin.superpowers-local",
                "kind": "plugin",
                "provider": "local",
                "locator": LOCATOR,
                "capabilities": ["workflow.superpowers"],
                "scope": "plugin",
                "authority": "global",
                "trust": "trusted_global",
                "selection": "available",
                "effects": ["local_read"],
                "egress": "none",
                "data_classes": ["project_metadata"],
                "approval": "task",
                "load_strategy": "progressive",
                "context_class": "small",
                "canonical": True,
                "priority": 100,
                "requires": [],
                "conflicts": [],
                "supersedes": [],
                "aliases": [],
                "output_contract": "plugin-capabilities",
            },
        )

    def test_local_superpowers_locator_requires_one_lowercase_commit(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        for locator in (
            "plugin://local-superpowers",
            f"plugin://local-superpowers/{PIN.upper()}",
            f"plugin://local-superpowers/{PIN}/extra",
            "plugin://local-superpowers/not-a-commit",
        ):
            with self.subTest(locator=locator):
                registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
                resource = next(
                    item
                    for item in registry["resources"]
                    if item["id"] == "plugin.superpowers-local"
                )
                resource["locator"] = locator
                self.assertIn(
                    "R_LOCATOR", {issue.code for issue in validate_registry(registry)}
                )

        registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
        resource = next(
            item
            for item in registry["resources"]
            if item["id"] == "plugin.superpowers-local"
        )
        resource["locator"] = "plugin://openai-superpowers"
        self.assertNotIn(
            "R_LOCATOR", {issue.code for issue in validate_registry(registry)}
        )

    def test_matching_local_superpowers_revision_is_ready_without_auth(self) -> None:
        from control_plane.resource_registry import build_inventory, load_registry

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            revision = _temporary_git_repository(local_root)
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(revision)]

            inventory = build_inventory(
                registry, ROOT, local_superpowers_root=local_root
            )

        entry = inventory["resources"][0]
        self.assertEqual(entry["availability"], "available")
        self.assertEqual(entry["authenticated"], "not_applicable")
        self.assertTrue(entry["ready"])
        self.assertNotIn(str(local_root), json.dumps(inventory, sort_keys=True))

    def test_revision_drift_raises_closed_path_free_error(self) -> None:
        from control_plane.resource_registry import (
            RegistryError,
            build_inventory,
            load_registry,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            actual_revision = _temporary_git_repository(local_root)
            wrong_revision = "0" * 40
            self.assertNotEqual(actual_revision, wrong_revision)
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(wrong_revision)]

            with self.assertRaises(RegistryError) as raised:
                build_inventory(
                    registry, ROOT, local_superpowers_root=local_root
                )

        self.assertEqual(raised.exception.code, "E_RESOURCE_REVISION_DRIFT")
        self.assertNotIn(str(local_root), raised.exception.message)
        self.assertNotIn(actual_revision, raised.exception.message)

    def test_tracked_dirty_local_superpowers_is_revision_drift(self) -> None:
        from control_plane.resource_registry import (
            RegistryError,
            build_inventory,
            load_registry,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            revision = _temporary_git_repository(local_root)
            (local_root / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(revision)]
            with self.assertRaises(RegistryError) as raised:
                build_inventory(registry, ROOT, local_superpowers_root=local_root)

        self.assertEqual(raised.exception.code, "E_RESOURCE_REVISION_DRIFT")
        self.assertNotIn(str(local_root), raised.exception.message)
        self.assertNotIn("tracked.txt", raised.exception.message)

    def test_untracked_local_superpowers_is_revision_drift(self) -> None:
        from control_plane.resource_registry import (
            RegistryError,
            build_inventory,
            load_registry,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            revision = _temporary_git_repository(local_root)
            (local_root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(revision)]
            with self.assertRaises(RegistryError) as raised:
                build_inventory(registry, ROOT, local_superpowers_root=local_root)

        self.assertEqual(raised.exception.code, "E_RESOURCE_REVISION_DRIFT")
        self.assertNotIn(str(local_root), raised.exception.message)
        self.assertNotIn("untracked.txt", raised.exception.message)

    def test_ignored_untracked_skill_is_revision_drift(self) -> None:
        from control_plane.resource_registry import (
            RegistryError,
            build_inventory,
            load_registry,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            local_root = Path(temp_dir)
            revision = _temporary_git_repository(local_root)
            (local_root / ".git" / "info" / "exclude").write_text(
                "ignored-skill/\n", encoding="utf-8"
            )
            ignored = local_root / "ignored-skill"
            ignored.mkdir()
            (ignored / "SKILL.md").write_text("untrusted instructions\n", encoding="utf-8")
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(revision)]

            with self.assertRaises(RegistryError) as raised:
                build_inventory(registry, ROOT, local_superpowers_root=local_root)

        self.assertEqual(raised.exception.code, "E_RESOURCE_REVISION_DRIFT")
        self.assertNotIn(str(local_root), raised.exception.message)
        self.assertNotIn("SKILL.md", raised.exception.message)

    def test_default_superpowers_root_uses_effective_account_not_home(self) -> None:
        from control_plane.resource_registry import build_inventory, load_registry

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            effective_home = temp_root / "effective-home"
            local_root = effective_home / ".codex" / "superpowers"
            local_root.mkdir(parents=True)
            revision = _temporary_git_repository(local_root)
            hostile_home = temp_root / "hostile-home"
            hostile_home.mkdir()
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(revision)]
            with (
                mock.patch.dict(os.environ, {"HOME": str(hostile_home)}),
                mock.patch(
                    "pwd.getpwuid",
                    return_value=SimpleNamespace(pw_dir=str(effective_home)),
                ),
            ):
                inventory = build_inventory(registry, ROOT)

        entry = inventory["resources"][0]
        self.assertTrue(entry["ready"])
        serialized = json.dumps(inventory, sort_keys=True)
        self.assertNotIn(str(effective_home), serialized)
        self.assertNotIn(str(hostile_home), serialized)

    def test_inventory_cli_propagates_exact_revision_drift_code(self) -> None:
        from control_plane.cli import build_parser

        source = (ROOT / ".codex" / "resource-registry.toml").read_text(
            encoding="utf-8"
        )
        drifted = source.replace(PIN, "0" * 40, 1)
        with tempfile.TemporaryDirectory() as temp_dir:
            temporary = Path(temp_dir)
            effective_home = temporary / "effective-home"
            local_root = effective_home / ".codex" / "superpowers"
            local_root.mkdir(parents=True)
            actual_revision = _temporary_git_repository(local_root)
            self.assertNotEqual(actual_revision, "0" * 40)
            registry_path = temporary / "registry.toml"
            registry_path.write_text(drifted, encoding="utf-8")
            arguments = build_parser().parse_args(
                [
                    "inventory",
                    "--repo",
                    str(ROOT),
                    "--registry",
                    str(registry_path),
                    "--json",
                ]
            )
            output = io.StringIO()
            with (
                mock.patch(
                    "control_plane.resource_registry._effective_user_home",
                    return_value=effective_home,
                ),
                contextlib.redirect_stdout(output),
            ):
                return_code = arguments.handler(arguments)

        payload = json.loads(output.getvalue())
        self.assertEqual(return_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "E_RESOURCE_REVISION_DRIFT")
        self.assertFalse(payload["authorizes"])

    def test_missing_local_superpowers_is_unavailable_not_exception(self) -> None:
        from control_plane.resource_registry import build_inventory, load_registry

        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(PIN)]

            inventory = build_inventory(
                registry, ROOT, local_superpowers_root=missing
            )

        entry = inventory["resources"][0]
        self.assertEqual(entry["availability"], "unavailable")
        self.assertFalse(entry["ready"])
        self.assertIn("R_NOT_FOUND", entry["reason_codes"])

    def test_symlinked_local_superpowers_root_is_invalid_without_git(self) -> None:
        from control_plane.resource_registry import build_inventory, load_registry

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            checkout = temp_root / "checkout"
            checkout.mkdir()
            link = temp_root / "link"
            os.symlink(checkout, link)
            registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
            registry["resources"] = [_local_superpowers_resource(PIN)]
            with mock.patch("control_plane.resource_registry.subprocess.run") as run:
                inventory = build_inventory(
                    registry, ROOT, local_superpowers_root=link
                )

        entry = inventory["resources"][0]
        self.assertEqual(entry["availability"], "invalid")
        self.assertFalse(entry["ready"])
        self.assertIn("R_SYMLINK_ESCAPE", entry["reason_codes"])
        run.assert_not_called()

    def test_local_revision_probe_uses_only_closed_bounded_git_commands(self) -> None:
        from control_plane.resource_registry import build_inventory, load_registry

        registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
        registry["resources"] = [_local_superpowers_resource(PIN)]
        completed = [
            subprocess.CompletedProcess([], 0, stdout=(str(ROOT) + "\n").encode()),
            subprocess.CompletedProcess([], 0, stdout=(PIN + "\n").encode()),
            subprocess.CompletedProcess([], 0, stdout=None),
        ]
        with (
            mock.patch(
                "control_plane.resource_registry.subprocess.run", side_effect=completed
            ) as run,
            mock.patch(
                "control_plane.resource_registry._untracked_checkout_clean",
                return_value=True,
            ),
        ):
            inventory = build_inventory(
                registry, ROOT, local_superpowers_root=ROOT
            )

        self.assertTrue(inventory["resources"][0]["ready"])
        self.assertEqual(run.call_count, 3)
        expected_tails = (
            ("rev-parse", "--show-toplevel"),
            ("rev-parse", "--verify", "HEAD^{commit}"),
            ("diff-index", "--quiet", "HEAD", "--"),
        )
        for call, expected_tail in zip(run.call_args_list, expected_tails):
            arguments, keywords = call
            self.assertEqual(arguments[0][0], "/usr/bin/git")
            self.assertEqual(tuple(arguments[0][-len(expected_tail) :]), expected_tail)
            self.assertEqual(keywords["stdin"], subprocess.DEVNULL)
            self.assertIn(
                keywords["stdout"], {subprocess.PIPE, subprocess.DEVNULL}
            )
            self.assertEqual(keywords["stderr"], subprocess.DEVNULL)
            self.assertEqual(keywords["timeout"], 5.0)
            self.assertFalse(keywords["shell"])
            self.assertFalse(keywords["check"])
            self.assertEqual(keywords["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_untracked_probe_reads_bounded_closed_git_stream(self) -> None:
        from control_plane.resource_registry import _untracked_checkout_clean

        read_descriptor, write_descriptor = os.pipe()
        os.close(write_descriptor)
        stream = os.fdopen(read_descriptor, "rb", buffering=0)
        process = mock.Mock()
        process.stdout = stream
        process.wait.return_value = 0
        process.poll.return_value = 0
        with mock.patch(
            "control_plane.resource_registry.subprocess.Popen", return_value=process
        ) as popen:
            self.assertTrue(_untracked_checkout_clean(ROOT))

        arguments, keywords = popen.call_args
        self.assertEqual(arguments[0][0], "/usr/bin/git")
        self.assertEqual(
            tuple(arguments[0][-4:]),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        )
        self.assertEqual(keywords["stdin"], subprocess.DEVNULL)
        self.assertEqual(keywords["stdout"], subprocess.PIPE)
        self.assertEqual(keywords["stderr"], subprocess.DEVNULL)
        self.assertFalse(keywords["shell"])
        self.assertEqual(keywords["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_untracked_probe_stubborn_cleanup_preserves_unknown(self) -> None:
        from control_plane.resource_registry import _untracked_checkout_clean

        stream = mock.Mock()
        process = mock.Mock()
        process.stdout = stream
        process.poll.return_value = None
        process.wait.side_effect = (
            subprocess.TimeoutExpired("git", 0.25),
            subprocess.TimeoutExpired("git", 0.25),
            subprocess.TimeoutExpired("git", 0),
        )
        selector = mock.Mock()
        selector.select.return_value = []
        with (
            mock.patch(
                "control_plane.resource_registry.subprocess.Popen",
                return_value=process,
            ),
            mock.patch(
                "control_plane.resource_registry.selectors.DefaultSelector",
                return_value=selector,
            ),
        ):
            result = _untracked_checkout_clean(ROOT)

        self.assertIsNone(result)
        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(
            process.wait.call_args_list,
            [mock.call(timeout=0.25), mock.call(timeout=0.25), mock.call(timeout=0)],
        )
        stream.close.assert_called_once_with()
        selector.close.assert_called_once_with()

    def test_skill_and_reference_are_core_only_and_byte_identical(self) -> None:
        canonical = ROOT / "skills" / "control-plane-run"
        packaged = ROOT / "plugins" / "control-plane" / "skills" / "control-plane-run"
        skill = (canonical / "SKILL.md").read_bytes()
        reference = (canonical / "references" / "taskplaybook-v0.md").read_bytes()

        self.assertEqual(skill, (packaged / "SKILL.md").read_bytes())
        self.assertEqual(
            reference,
            (packaged / "references" / "taskplaybook-v0.md").read_bytes(),
        )
        text = skill.decode("utf-8")
        for required in (
            "dd237283dbfe466e11bd4be55acf14ecb8f6636e",
            "Control Plane owns scope, authority, and evidence",
            "Superpowers owns TDD, debugging, worktrees, and review",
            "Autopilot is OFF",
            "No daemon, scheduler, authority store, or telemetry",
            "authorizes=false",
            "local_write",
            "commit",
            "push",
            "pull_request",
            "Quarantined capabilities are unavailable",
            "Never import or execute quarantined runtime",
        ):
            self.assertIn(required, text)
        for forbidden in (
            "run prepare",
            "run verify",
            "run status",
            "run block",
            "lifecycle",
            "host bridge",
            "host_bridge",
        ):
            self.assertNotIn(forbidden, text.lower())

    def test_taskplaybook_is_small_ephemeral_and_non_authorizing(self) -> None:
        path = (
            ROOT
            / "skills"
            / "control-plane-run"
            / "references"
            / "taskplaybook-v0.md"
        )
        content = path.read_bytes()
        text = content.decode("utf-8")

        self.assertLessEqual(len(content), 1024)
        self.assertIn("authorizes: false", text)
        self.assertIn("skill-only", text.lower())
        self.assertIn("ephemeral", text.lower())
        for forbidden in (
            "runtime",
            "store",
            "files",
            "goal",
            "worker",
            "effect",
            "recovery",
        ):
            self.assertNotIn(forbidden, text.lower())

    def test_plugin_manifest_is_uninstalled_source_candidate_only(self) -> None:
        plugin_root = ROOT / "plugins" / "control-plane"
        manifest = json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        files = sorted(
            path.relative_to(plugin_root).as_posix()
            for path in plugin_root.rglob("*")
            if path.is_file()
        )

        self.assertEqual(manifest["version"], "3.1.0-core.1")
        rendered_metadata = " ".join(
            (
                str(manifest.get("description", "")),
                str(manifest.get("interface", {}).get("shortDescription", "")),
                str(manifest.get("interface", {}).get("longDescription", "")),
            )
        ).lower()
        for required in ("core", "local-first", "non-authorizing"):
            self.assertIn(required, rendered_metadata)
        for forbidden in (
            "native-governed",
            "governs native tasks",
            "host-only authority",
        ):
            self.assertNotIn(forbidden, rendered_metadata)
        self.assertEqual(
            files,
            [
                ".codex-plugin/plugin.json",
                "skills/control-plane-run/SKILL.md",
                "skills/control-plane-run/references/taskplaybook-v0.md",
            ],
        )
        self.assertNotIn("advanced", " ".join(files).lower())

    def test_core_runner_manifest_includes_plugin_test(self) -> None:
        runner = (ROOT / "tests" / "run.sh").read_text(encoding="utf-8")
        self.assertIn("tests.test_core_plugin", runner)
        self.assertIn("tests/test_core_plugin.py", runner)
        self.assertIn("control_plane/resource_registry.py", runner)


if __name__ == "__main__":
    unittest.main()
