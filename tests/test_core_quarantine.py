from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "control-plane"


def tree_snapshot(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (str(path.relative_to(root)), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


class CoreQuarantineTests(unittest.TestCase):
    def _assert_quarantined(self, build_arguments) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            marker = target / "keep.txt"
            marker.write_text("unchanged\n", encoding="utf-8")
            task = target / "task.json"
            task.write_text("{}\n", encoding="utf-8")
            plan = target / "plan.json"
            plan.write_text("{}\n", encoding="utf-8")
            arguments = build_arguments(target, task, plan)
            before = tree_snapshot(target)
            completed = subprocess.run(
                [str(CLI), *arguments],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], "E_CAPABILITY_QUARANTINED")
            self.assertFalse(payload["authorizes"])
            self.assertEqual(tree_snapshot(target), before)

    def test_advanced_actions_are_zero_mutation_stubs(self) -> None:
        cases = (
            lambda root, task, plan: [
                "verification-run", "--repo", str(root), "--task-id", "TASK-CORE", "--json"
            ],
            lambda root, task, plan: [
                "run", "prepare", "--repo", str(root), "--task", str(task),
                "--session-id", "SESSION-CORE", "--json",
            ],
            lambda root, task, plan: ["report", "--repo", str(root), "--json"],
            lambda root, task, plan: ["adopt", "plan", "--target", str(root), "--json"],
            lambda root, task, plan: ["adopt", "apply", "--plan", str(plan), "--json"],
            lambda root, task, plan: ["upgrade", "plan", "--target", str(root), "--json"],
            lambda root, task, plan: ["upgrade", "apply", "--plan", str(plan), "--json"],
        )
        for build_arguments in cases:
            with self.subTest(case=build_arguments):
                self._assert_quarantined(build_arguments)

    def test_release_candidate_surface_is_stable_zero_mutation_stub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            output = target / "candidate"
            evidence = target / "evidence.json"
            evidence.write_text('{"authorizes":false}\n', encoding="utf-8")
            before = tree_snapshot(target)

            completed = subprocess.run(
                [
                    str(ROOT / "scripts" / "build-release-candidate"),
                    "--repo",
                    str(ROOT),
                    "--output-dir",
                    str(output),
                    "--workflow-url",
                    "https://invalid.example/run/1",
                    "--workflow-evidence",
                    str(evidence),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(
                json.loads(completed.stdout),
                {
                    "authorizes": False,
                    "error_code": "E_CAPABILITY_QUARANTINED",
                    "message": "Release candidate generation is quarantined in Control Plane Core.",
                    "ok": False,
                },
            )
            self.assertEqual(tree_snapshot(target), before)
            self.assertFalse(output.exists())

    def test_release_candidate_stub_has_no_advanced_or_network_runtime(self) -> None:
        source = (ROOT / "scripts" / "build-release-candidate").read_text(
            encoding="utf-8"
        )
        self.assertTrue(source.startswith("#!/bin/sh\n"))
        for forbidden in (
            "control_plane.",
            "urllib",
            "tarfile",
            "CONTROL_PLANE_GITHUB_TOKEN",
            "github.com",
            "git ",
        ):
            self.assertNotIn(forbidden, source)

    def test_ci_has_one_core_verify_job_and_no_advanced_surface(self) -> None:
        source = (ROOT / ".github" / "workflows" / "control-plane.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(source.count("/bin/sh tests/run.sh"), 1)
        self.assertIn("/usr/bin/env -i", source)
        self.assertNotIn("shell: /bin/bash", source)
        self.assertIn("actions: read", source)
        self.assertIn("contents: read", source)
        self.assertIn("fetch-depth: 0", source)
        self.assertNotIn("fetch-depth: 1", source)
        for forbidden in (
            "release-candidate:",
            "macos-smoke:",
            "tests.test_supported_adoption_acceptance",
            "tests.macos_hook_smoke",
            "scripts/build-release-candidate",
            "CONTROL_PLANE_GITHUB_TOKEN",
            "preflight --mode release",
            "upload-artifact",
        ):
            self.assertNotIn(forbidden, source)

    def test_hooks_metadata_is_core_audit_only_and_uses_closed_launcher(self) -> None:
        raw = (ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8")
        value = json.loads(raw)
        description = value["description"]
        self.assertIn("Control Plane Core 3.1", description)
        self.assertIn("audit-only", description)
        self.assertIn("authorizes=false", description)
        self.assertNotIn("v2", description)
        commands = {
            hook["command"]
            for entries in value["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        }
        self.assertEqual(len(commands), 1)
        command = commands.pop()
        self.assertTrue(command.startswith("/usr/bin/env -i "))
        self.assertIn("/bin/sh -c", command)
        self.assertNotIn("/bin/bash", command)
        self.assertIn("/usr/bin/git rev-parse --show-toplevel", command)
        self.assertIn("GIT_CONFIG_NOSYSTEM=1", command)
        self.assertIn("GIT_CONFIG_GLOBAL=/dev/null", command)
        self.assertIn("/scripts/control-plane", command)
        self.assertIn("__hook__", command)
        self.assertNotIn("control_plane_hook.py", command)

        environment = os.environ.copy()
        environment["GIT_DIR"] = str(ROOT / "not-a-git-dir")
        environment["GIT_WORK_TREE"] = str(ROOT / "not-a-work-tree")
        environment["GIT_CONFIG_GLOBAL"] = str(ROOT / "not-a-config")
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            startup_marker = scratch / "startup-marker"
            startup = scratch / "startup.sh"
            startup.write_text(
                f"printf injected > {str(startup_marker)!r}\n",
                encoding="utf-8",
            )
            environment["BASH_ENV"] = str(startup)
            environment["ENV"] = str(startup)
            environment["LD_PRELOAD"] = "hostile-loader-value"
            environment["DYLD_INSERT_LIBRARIES"] = "hostile-loader-value"
            completed = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=ROOT,
                env=environment,
                input='{"hook_event_name":"UserPromptSubmit"}',
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
            self.assertFalse(startup_marker.exists())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("not-a-git-dir", completed.stderr + completed.stdout)

        lock = tomllib.loads(
            (ROOT / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
        )
        self.assertEqual(lock["hook_mode"], "audit")
        self.assertEqual(lock["hook_trust"], "pending_hook_trust")

    def test_launcher_clean_reexec_preserves_empty_and_payload_hook_stdin_once(self) -> None:
        cases = (
            ("empty", "", 1, "stderr"),
            (
                "payload",
                '{"hook_event_name":"UserPromptSubmit"}',
                0,
                "stdout",
            ),
        )
        for label, hook_input, returncode, output_stream in cases:
            with self.subTest(label=label):
                completed = subprocess.run(
                    [str(ROOT / "scripts" / "control-plane"), "__hook__"],
                    cwd=ROOT,
                    env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                    input=hook_input,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=5,
                )

                self.assertEqual(completed.returncode, returncode)
                selected_output = getattr(completed, output_stream)
                self.assertEqual(len(selected_output.splitlines()), 1)
                other_stream = "stderr" if output_stream == "stdout" else "stdout"
                self.assertEqual(getattr(completed, other_stream), "")
                if label == "empty":
                    self.assertEqual(selected_output, "E_HOOK_INPUT: invalid hook JSON\n")
                else:
                    self.assertTrue(json.loads(selected_output)["continue"])

    def test_release_stub_is_posix_env_hostile_and_zero_mutation(self) -> None:
        source = (ROOT / "scripts" / "build-release-candidate").read_text(
            encoding="utf-8"
        )
        self.assertTrue(source.startswith("#!/bin/sh\n"))
        self.assertIn("exec /usr/bin/env -i CONTROL_PLANE_CLEAN_SHELL=1", source)
        for bashism in ("BASH_SOURCE", "[[", "pipefail", "=("):
            self.assertNotIn(bashism, source)

        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory)
            marker = scratch / "startup-marker"
            startup = scratch / "startup.sh"
            startup.write_text(
                f"printf injected > {str(marker)!r}\n",
                encoding="utf-8",
            )
            before = tuple(scratch.rglob("*"))
            environment = os.environ.copy()
            environment.update(
                {
                    "BASH_ENV": str(startup),
                    "ENV": str(startup),
                    "PATH": str(scratch / "hostile-bin"),
                }
            )

            completed = subprocess.run(
                [str(ROOT / "scripts" / "build-release-candidate")],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertEqual(
                json.loads(completed.stdout),
                {
                    "authorizes": False,
                    "error_code": "E_CAPABILITY_QUARANTINED",
                    "message": "Release candidate generation is quarantined in Control Plane Core.",
                    "ok": False,
                },
            )
            self.assertFalse(marker.exists())
            self.assertEqual(tuple(scratch.rglob("*")), before)

    def test_core_runner_is_posix_closed_and_compiles_bytes_without_pycache(self) -> None:
        source = (ROOT / "tests" / "run.sh").read_text(encoding="utf-8")
        engine = (ROOT / "tests" / "core_gate.py").read_text(encoding="utf-8")
        self.assertTrue(source.startswith("#!/bin/sh\n"))
        self.assertIn("exec /usr/bin/env -i CONTROL_PLANE_CLEAN_SHELL=1", source)
        self.assertIn("/usr/bin/env -i", source)
        self.assertIn('"$PYTHON_BIN" -I -S -B -X pycache_prefix=/dev/null', source)
        self.assertIn("-S -B -X pycache_prefix=/dev/null", source)
        self.assertIn("sys.flags.no_site", source)
        self.assertIn("build_closed_execution_context", source)
        self.assertNotIn("--inside-verification-mutex", source)
        self.assertNotIn("CONTROL_PLANE_VERIFICATION_MUTEX_HELD", source)
        self.assertLess(source.index("compile(payload,"), source.index("gate.run_gate"))
        self.assertIn("loadTestsFromNames", engine)
        self.assertIn("context.python.path", engine)
        self.assertIn("context.node.path", engine)
        self.assertIn("context.git.path", engine)
        self.assertIn("E_TEST_CONTEXT: attested Node is unavailable", engine)
        self.assertIn("_assert_context(context, repository)", engine)
        self.assertGreaterEqual(engine.count("_activate_context(context, repository)"), 7)
        self.assertIn("compile(payload,", source)
        self.assertNotIn("py_compile", source)
        self.assertNotIn("CORE_TESTS=(", source)
        self.assertNotIn("CORE_MODULES=(", source)
        self.assertNotIn("CORE_TEST_FILES=(", source)
        self.assertIn("for candidate in", source)
        self.assertNotIn("python3 -m", source)
        self.assertNotIn("python3 -c", source)
        self.assertNotIn("/bin/bash", source)

    def test_hook_manifest_label_is_core_generation_not_v2(self) -> None:
        source = (ROOT / "control_plane" / "hooks.py").read_text(encoding="utf-8")
        self.assertIn("CONTROL_PLANE_CORE_AUDIT", source)
        self.assertNotIn("CONTROL_PLANE_AUDIT_V2", source)


if __name__ == "__main__":
    unittest.main()
