from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from control_plane.contracts import contract_digest
from control_plane.leases import LeaseStore
from control_plane.task_state import CoreTaskStore
from tests.git_test_support import FIXTURE_POLICY
from tests.core_router_test_support import (
    VALID_REGISTRY,
    inventory_snapshot,
    task_envelope,
)
from tests.test_core_task_state import git, make_repo


ROOT = Path(__file__).resolve().parents[1]
ADVANCED_MODULES = (
    "control_plane.adoption",
    "control_plane.candidate_receipt",
    "control_plane.host_bridge",
    "control_plane.lifecycle",
    "control_plane.release_source",
    "control_plane.run_workflow",
)
CORE_COMMAND_CASES = (
    (
        "policy-check",
        ("policy-check", "--policy", "policy.toml", "--json"),
        {"policy": Path("policy.toml"), "json": True},
    ),
    (
        "doctor",
        ("doctor", "--repo", "/repo", "--policy", "policy.toml", "--json"),
        {"repo": Path("/repo"), "policy": Path("policy.toml"), "json": True},
    ),
    (
        "preflight",
        (
            "preflight", "--mode", "write", "--repo", "/repo", "--policy",
            "policy.toml", "--task-id", "TASK-CORE", "--session-id", "SESSION-CORE",
            "--offline", "--json",
        ),
        {
            "mode": "write", "repo": Path("/repo"), "policy": Path("policy.toml"),
            "task_id": "TASK-CORE", "session_id": "SESSION-CORE", "refresh": False,
            "json": True,
        },
    ),
    (
        "registry-check",
        (
            "registry-check", "--registry", "registry.toml", "--policy",
            "policy.toml", "--json",
        ),
        {
            "registry": Path("registry.toml"), "policy": Path("policy.toml"),
            "json": True,
        },
    ),
    (
        "inventory",
        ("inventory", "--repo", "/repo", "--registry", "registry.toml", "--json"),
        {"repo": Path("/repo"), "registry": Path("registry.toml"), "json": True},
    ),
    (
        "route",
        (
            "route", "--repo", "/repo", "--task", "task.json", "--policy",
            "policy.toml", "--registry", "registry.toml", "--mode", "audit", "--json",
        ),
        {
            "repo": Path("/repo"), "task": Path("task.json"),
            "policy": Path("policy.toml"), "registry": Path("registry.toml"),
            "mode": "audit", "json": True,
        },
    ),
    (
        "route-verify",
        (
            "route-verify", "--decision", "decision.json", "--receipt",
            "receipt.json", "--mode", "audit", "--json",
        ),
        {
            "decision": Path("decision.json"), "receipt": Path("receipt.json"),
            "mode": "audit", "json": True,
        },
    ),
    (
        "risk-status",
        (
            "risk-status", "--repo", "/repo", "--policy", "policy.toml",
            "--task-id", "TASK-CORE", "--lease-session-id", "SESSION-CORE",
            "--decision", "decision.json", "--json",
        ),
        {
            "repo": Path("/repo"), "policy": Path("policy.toml"),
            "task_id": "TASK-CORE", "lease_session_id": "SESSION-CORE",
            "decision": Path("decision.json"), "json": True,
        },
    ),
    (
        "safe-read",
        (
            "safe-read", "--repo", "/repo", "--timeout", "2.5", "--output-limit",
            "4096", "--", "git", "status", "--short",
        ),
        {
            "repo": Path("/repo"), "timeout": 2.5, "output_limit": 4096,
            "argv": ["--", "git", "status", "--short"],
        },
    ),
    (
        "hook-smoke",
        ("hook-smoke", "--repo", "/repo", "--task-id", "TASK-CORE", "--json"),
        {"repo": Path("/repo"), "task_id": "TASK-CORE", "json": True},
    ),
    (
        "git-guard",
        ("git-guard", "pre-commit", "--repo", "/repo", "--json"),
        {"guard_action": "pre-commit", "repo": Path("/repo"), "json": True},
    ),
    (
        "task",
        ("task", "status", "--repo", "/repo", "--task-id", "TASK-CORE", "--json"),
        {"task_action": "status", "repo": Path("/repo"), "task_id": "TASK-CORE", "json": True},
    ),
)


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "control_plane.cli", *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def run_cli_in_process(*arguments: str) -> tuple[int, dict]:
    from control_plane.cli import main

    output = io.StringIO()
    with redirect_stdout(output):
        return_code = main(arguments)
    return return_code, json.loads(output.getvalue())


def tree_snapshot(root: Path) -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (str(path.relative_to(root)), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def core_record_snapshot(repo: Path) -> tuple[tuple[str, str, bytes], ...]:
    task_store = CoreTaskStore(repo)
    lease_store = LeaseStore(repo)
    records: list[tuple[str, str, bytes]] = []
    for namespace, directory in (
        ("tasks", task_store.tasks),
        ("leases", lease_store.leases),
        ("lease-release-receipts", lease_store.receipts),
    ):
        if directory.is_dir():
            records.extend(
                (namespace, str(path.relative_to(directory)), path.read_bytes())
                for path in sorted(directory.rglob("*"))
                if path.is_file()
            )
    return tuple(records)


def task_start_arguments(repo: Path, task_id: str, policy: Path) -> tuple[str, ...]:
    return (
        "task", "start", "--repo", str(repo), "--task-id", task_id,
        "--outcome", "local_change", "--branch", "codex/core-test",
        "--task-digest", contract_digest({"task": task_id}),
        "--decision-digest", contract_digest({"decision": task_id}),
        "--session-id", f"SESSION-{task_id}", "--scope-path",
        "control_plane/cli.py", "--policy", str(policy), "--json",
    )


class CoreCliTests(unittest.TestCase):
    def test_explicit_policy_cannot_substitute_for_governing_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            alternate = repo / "alternate-policy.toml"
            alternate.write_text(
                FIXTURE_POLICY.read_text(encoding="utf-8").replace(
                    'project_name = "fixture-project"',
                    'project_name = "alternate-project"',
                ),
                encoding="utf-8",
            )
            before = core_record_snapshot(repo)

            started = run_cli(
                *task_start_arguments(repo, "TASK-POLICY-SUBSTITUTION", alternate)
            )
            self.assertNotEqual(started.returncode, 0, started.stdout)
            started_payload = json.loads(started.stdout)
            self.assertEqual(
                started_payload["errors"][0]["code"], "E_POLICY_NOT_GOVERNING"
            )
            self.assertFalse(started_payload["authorizes"])
            self.assertEqual(core_record_snapshot(repo), before)

            for mode in ("read", "write"):
                with self.subTest(mode=mode):
                    completed = run_cli(
                        "preflight",
                        "--mode",
                        mode,
                        "--repo",
                        str(repo),
                        "--policy",
                        str(alternate),
                        "--json",
                    )
                    self.assertNotEqual(completed.returncode, 0, completed.stdout)
                    payload = json.loads(completed.stdout)
                    self.assertEqual(
                        payload["errors"][0]["code"], "E_POLICY_NOT_GOVERNING"
                    )
                    self.assertFalse(payload["authorizes"])
                    self.assertEqual(core_record_snapshot(repo), before)

    def test_building_parser_imports_no_advanced_module(self) -> None:
        program = (
            "import json,sys\n"
            "from control_plane.cli import build_parser\n"
            "build_parser()\n"
            f"names={ADVANCED_MODULES!r}\n"
            "print(json.dumps(sorted(name for name in names if name in sys.modules)))\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [])

    def test_core_command_matrix_preserves_closed_basic_flags(self) -> None:
        from control_plane.cli import build_parser

        self.assertEqual(len(CORE_COMMAND_CASES), 12)
        self.assertEqual(
            {name for name, _, _ in CORE_COMMAND_CASES},
            {
                "policy-check", "doctor", "preflight", "registry-check",
                "inventory", "route", "route-verify", "risk-status", "safe-read",
                "hook-smoke", "git-guard", "task",
            },
        )
        parser = build_parser()
        for name, argv, expected in CORE_COMMAND_CASES:
            with self.subTest(command=name):
                try:
                    parsed = parser.parse_args(argv)
                except SystemExit as error:
                    self.fail(f"{name} parser is unavailable: exit {error.code}")
                self.assertEqual(parsed.command, name)
                self.assertTrue(callable(parsed.handler))
                for attribute, value in expected.items():
                    self.assertEqual(getattr(parsed, attribute), value)

    def test_task_lease_release_requires_policy_digest(self) -> None:
        from control_plane.cli import build_parser

        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                (
                    "task", "lease-release", "--task-id", "TASK-CORE",
                    "--worktree", "/repo", "--branch", "codex/core-test",
                    "--session-id", "SESSION-CORE", "--lease-digest",
                    "sha256:" + "1" * 64,
                )
            )

    def test_hook_smoke_is_non_mutating_core_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            marker = target / "keep.txt"
            marker.write_text("unchanged\n", encoding="utf-8")
            before = tree_snapshot(target)
            program = (
                "from contextlib import redirect_stdout\n"
                "import io,json,sys\n"
                "from control_plane.cli import main\n"
                f"advanced={ADVANCED_MODULES!r}\n"
                "output=io.StringIO()\n"
                "with redirect_stdout(output):\n"
                f" rc=main(['hook-smoke','--repo',{str(target)!r},'--task-id',"
                "'TASK-HOOK-SMOKE-CORE','--json'])\n"
                "print(json.dumps({'rc':rc,'payload':json.loads(output.getvalue()),"
                "'advanced':sorted(name for name in advanced if name in sys.modules)}))\n"
            )
            completed = subprocess.run(
                [sys.executable, "-c", program],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            observed = json.loads(completed.stdout)
            self.assertEqual(observed["rc"], 2)
            self.assertEqual(observed["advanced"], [])
            self.assertEqual(
                observed["payload"],
                {
                    "schema_version": 1,
                    "command": "hook-smoke",
                    "ok": False,
                    "status": "UNKNOWN",
                    "error_code": "E_CAPABILITY_QUARANTINED",
                    "errors": [
                        {
                            "code": "E_CAPABILITY_QUARANTINED",
                            "message": "Advanced hook-smoke assurance is unavailable in Core.",
                        }
                    ],
                    "authorizes": False,
                },
            )
            self.assertEqual(tree_snapshot(target), before)

    def test_doctor_and_preflight_non_repository_emit_closed_json(self) -> None:
        for command in ("doctor", "preflight"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as directory:
                arguments = [command, "--repo", directory]
                if command == "preflight":
                    arguments.extend(("--mode", "write"))
                arguments.append("--json")

                completed = run_cli(*arguments)

                self.assertNotEqual(completed.returncode, 0)
                self.assertNotIn("Traceback", completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["command"], command)
                self.assertFalse(payload["ok"])
                self.assertFalse(payload["authorizes"])
                self.assertEqual(payload["errors"][0]["code"], "E_GIT_NOT_REPOSITORY")

    def test_release_preflight_quarantines_before_repository_observation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "keep.txt").write_text("unchanged\n", encoding="utf-8")
            before = tree_snapshot(target)

            with patch(
                "control_plane.cli._repository",
                side_effect=AssertionError("release preflight observed repository"),
            ) as repository_probe, patch(
                "control_plane.cli.subprocess.run",
                side_effect=AssertionError("release preflight spawned a process"),
            ) as process_probe:
                return_code, payload = run_cli_in_process(
                    "preflight", "--mode", "release", "--repo", str(target), "--json"
                )

            self.assertEqual(return_code, 2)
            self.assertEqual(
                payload,
                {
                    "schema_version": 1,
                    "command": "preflight",
                    "ok": False,
                    "error_code": "E_CAPABILITY_QUARANTINED",
                    "authorizes": False,
                },
            )
            repository_probe.assert_not_called()
            process_probe.assert_not_called()
            self.assertEqual(tree_snapshot(target), before)

    def test_route_json_round_trips_through_route_verify(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            task = task_envelope(
                intent="explain",
                phase="research",
                requested_outcome="answer",
                signals=[],
                risk={
                    "uncertainty": 0,
                    "blast_radius": 0,
                    "irreversibility": 0,
                    "verification_complexity": 0,
                },
                effects=[{"name": "local_read", "source": "model_inference"}],
            )
            task_path = target / "task.json"
            decision_path = target / "decision.json"
            receipt_path = target / "receipt.json"
            task_path.write_text(json.dumps(task), encoding="utf-8")

            with patch(
                "control_plane.resource_registry.build_inventory",
                return_value=inventory_snapshot(),
            ):
                route_code, decision = run_cli_in_process(
                    "route",
                    "--repo",
                    str(ROOT),
                    "--task",
                    str(task_path),
                    "--policy",
                    str(FIXTURE_POLICY),
                    "--registry",
                    str(VALID_REGISTRY),
                    "--mode",
                    "audit",
                    "--json",
                )

            self.assertEqual(route_code, 0, decision)
            self.assertTrue(decision["decision_ready"], decision)
            self.assertFalse(decision["authorizes"])
            used = []
            for resource_id in decision["summary"]["required"]:
                locator_digest = decision["selected_resource_digests"][resource_id]
                used.append(
                    {
                        "resource_id": resource_id,
                        "locator_digest": locator_digest,
                        "evidence_digest": contract_digest(
                            {
                                "decision_digest": decision["decision_digest"],
                                "resource_id": resource_id,
                                "locator_digest": locator_digest,
                            }
                        ),
                    }
                )
            gate_results = []
            for gate_id in decision["required_gates"]:
                report_digest = contract_digest({"gate_id": gate_id})
                subject_digest = decision["decision_digest"]
                gate_results.append(
                    {
                        "gate_id": gate_id,
                        "ok": True,
                        "report_digest": report_digest,
                        "subject_digest": subject_digest,
                        "evidence_digest": contract_digest(
                            {
                                "gate_id": gate_id,
                                "ok": True,
                                "report_digest": report_digest,
                                "subject_digest": subject_digest,
                            }
                        ),
                    }
                )
            receipt = {
                "schema_version": 1,
                "task_id": decision["task_id"],
                "decision_digest": decision["decision_digest"],
                "task_digest": decision["facts"]["task_digest"],
                "policy_digest": decision["facts"]["policy_digest"],
                "registry_digest": decision["facts"]["registry_digest"],
                "inventory_digest": decision["facts"]["inventory_digest"],
                "used": used,
                "omitted": [],
                "gate_results": gate_results,
                "observed_effects": [],
            }
            receipt["receipt_digest"] = contract_digest(receipt)
            decision_path.write_text(json.dumps(decision), encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

            verify_code, verification = run_cli_in_process(
                "route-verify",
                "--decision",
                str(decision_path),
                "--receipt",
                str(receipt_path),
                "--mode",
                "enforce",
                "--json",
            )

            self.assertEqual(verify_code, 0, verification)
            self.assertTrue(verification["compliant"], verification)
            self.assertEqual(verification["errors"], [])
            self.assertFalse(verification["authorizes"])

            forged = dict(decision)
            forged["authorizes"] = True
            forged["decision_digest"] = contract_digest(
                {
                    key: value
                    for key, value in forged.items()
                    if key not in {"command", "decision_digest"}
                }
            )
            decision_path.write_text(json.dumps(forged), encoding="utf-8")
            _, rejected = run_cli_in_process(
                "route-verify",
                "--decision",
                str(decision_path),
                "--receipt",
                str(receipt_path),
                "--mode",
                "audit",
                "--json",
            )

            self.assertIn(
                "E_DECISION_SCHEMA",
                {error["code"] for error in rejected["errors"]},
            )

    def test_json_input_rejects_symlink_fifo_and_oversize_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            symlink = root / "symlink.json"
            symlink.symlink_to(target)
            fifo = root / "input.fifo"
            os.mkfifo(fifo)
            oversize = root / "oversize.json"
            oversize.write_text(
                json.dumps({"payload": "x" * 1_048_576}), encoding="utf-8"
            )
            program = (
                "from pathlib import Path\n"
                "from control_plane.cli import _read_json\n"
                "import sys\n"
                "try:\n"
                " _read_json(Path(sys.argv[1]))\n"
                "except ValueError as error:\n"
                " print(str(error).split(':', 1)[0])\n"
                " raise SystemExit(0)\n"
                "raise SystemExit(3)\n"
            )
            for hostile in (symlink, oversize, fifo):
                with self.subTest(kind=hostile.name):
                    try:
                        completed = subprocess.run(
                            [sys.executable, "-c", program, str(hostile)],
                            cwd=ROOT,
                            text=True,
                            capture_output=True,
                            check=False,
                            timeout=2,
                        )
                    except subprocess.TimeoutExpired:
                        self.fail(f"JSON reader blocked on {hostile.name}")
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout.strip(), "E_JSON_INPUT")

    def test_doctor_fails_closed_on_unproven_materialization(self) -> None:
        from control_plane.materialization import MaterializationResult

        for observed_code, expected_code in (
            ("E_MATERIALIZATION_STAT", "E_MATERIALIZATION_STAT"),
            (None, "E_MATERIALIZATION_UNKNOWN"),
        ):
            with self.subTest(error_code=observed_code), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                (repo / ".codex" / "project-policy.toml").write_bytes(
                    FIXTURE_POLICY.read_bytes()
                )
                observation = MaterializationResult(
                    False,
                    "UNKNOWN",
                    1,
                    ("dataless.txt",),
                    observed_code,
                )

                with patch(
                    "control_plane.materialization.inspect_tracked_materialization",
                    return_value=observation,
                ), patch.object(
                    Path,
                    "read_bytes",
                    side_effect=AssertionError("doctor read dataless contents"),
                ):
                    return_code, payload = run_cli_in_process(
                        "doctor", "--repo", str(repo), "--json"
                    )

                self.assertNotEqual(return_code, 0)
                self.assertFalse(payload["ok"])
                self.assertIn(
                    expected_code,
                    {error["code"] for error in payload["errors"]},
                )

    def test_quarantined_run_is_exact_and_does_not_mutate_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            task = target / "task.json"
            task.write_text("{}\n", encoding="utf-8")
            before = tree_snapshot(target)

            completed = run_cli(
                "run",
                "prepare",
                "--repo",
                str(target),
                "--task",
                str(task),
                "--session-id",
                "SESSION-CORE",
                "--json",
            )

            self.assertEqual(completed.returncode, 2, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(
                payload,
                {
                    "schema_version": 1,
                    "command": "run-prepare",
                    "ok": False,
                    "error_code": "E_CAPABILITY_QUARANTINED",
                    "authorizes": False,
                },
            )
            self.assertEqual(tree_snapshot(target), before)

    def test_task_start_and_status_use_core_state_and_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            task_digest = contract_digest({"task": "cli"})
            decision_digest = contract_digest({"decision": "cli"})
            started = run_cli(
                "task",
                "start",
                "--repo",
                str(repo),
                "--task-id",
                "TASK-CORE-CLI",
                "--outcome",
                "local_change",
                "--branch",
                "codex/core-test",
                "--task-digest",
                task_digest,
                "--decision-digest",
                decision_digest,
                "--session-id",
                "SESSION-CORE-CLI",
                "--scope-path",
                "control_plane/cli.py",
                "--policy",
                str(FIXTURE_POLICY),
                "--json",
            )
            status = run_cli(
                "task",
                "status",
                "--repo",
                str(repo),
                "--task-id",
                "TASK-CORE-CLI",
                "--json",
            )

            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(status.returncode, 0, status.stderr)
            started_payload = json.loads(started.stdout)
            status_payload = json.loads(status.stdout)
            self.assertEqual(started_payload["task"]["kind"], "CoreTaskStateV1")
            self.assertEqual(started_payload["task"]["lease_generation"], 1)
            self.assertEqual(status_payload["task"], started_payload["task"])
            lease = LeaseStore(repo).find("TASK-CORE-CLI")
            self.assertIsNotNone(lease)
            self.assertEqual(lease["lease_generation"], 1)
            self.assertFalse(started_payload["authorizes"])

    def test_task_start_with_unusable_policy_leaves_no_core_records(self) -> None:
        for scenario, expected_code in (
            ("missing", "E_POLICY_NOT_FOUND"),
            ("invalid", "P_POLICY"),
        ):
            with self.subTest(policy=scenario), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                policy = repo / f"{scenario}-policy.toml"
                if scenario == "invalid":
                    policy.write_text("schema_version = 1\n", encoding="utf-8")
                before = core_record_snapshot(repo)

                completed = run_cli(
                    *task_start_arguments(repo, f"TASK-POLICY-{scenario.upper()}", policy)
                )

                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                payload = json.loads(completed.stdout)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["errors"][0]["code"], expected_code)
                self.assertEqual(core_record_snapshot(repo), before)

    def test_task_start_faults_preserve_exact_new_and_existing_core_records(self) -> None:
        for fault, target in (
            ("acquire", LeaseStore),
            ("binding", CoreTaskStore),
        ):
            method = (
                "acquire_with_origin"
                if fault == "acquire"
                else "bind_lease_generation"
            )
            for preexisting in (False, True):
                with (
                    self.subTest(fault=fault, preexisting=preexisting),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    repo = make_repo(Path(directory) / "repo")
                    task_id = f"TASK-{fault.upper()}-{'OLD' if preexisting else 'NEW'}"
                    if preexisting:
                        CoreTaskStore(repo).start(
                            task_id,
                            outcome="local_change",
                            branch="codex/core-test",
                            head=git(repo, "rev-parse", "HEAD"),
                            task_digest=contract_digest({"task": task_id}),
                            decision_digest=contract_digest({"decision": task_id}),
                            scope_paths=["control_plane/cli.py"],
                        )
                    before = core_record_snapshot(repo)

                    with patch.object(
                        target,
                        method,
                        side_effect=ValueError(f"E_TEST_{fault.upper()}: injected failure"),
                    ):
                        return_code, payload = run_cli_in_process(
                            *task_start_arguments(repo, task_id, FIXTURE_POLICY)
                        )

                    self.assertNotEqual(return_code, 0)
                    self.assertFalse(payload["ok"])
                    self.assertEqual(core_record_snapshot(repo), before)

    def test_task_start_binding_post_write_failure_restores_exact_records(self) -> None:
        original_bind = CoreTaskStore.bind_lease_generation

        def bind_then_fail(store: CoreTaskStore, *arguments: object, **keywords: object) -> dict:
            original_bind(store, *arguments, **keywords)
            raise ValueError("E_TEST_BINDING_POST_WRITE: injected failure")

        for preexisting in (False, True):
            with (
                self.subTest(preexisting=preexisting),
                tempfile.TemporaryDirectory() as directory,
            ):
                repo = make_repo(Path(directory) / "repo")
                task_id = f"TASK-BINDING-POST-{'OLD' if preexisting else 'NEW'}"
                if preexisting:
                    CoreTaskStore(repo).start(
                        task_id,
                        outcome="local_change",
                        branch="codex/core-test",
                        head=git(repo, "rev-parse", "HEAD"),
                        task_digest=contract_digest({"task": task_id}),
                        decision_digest=contract_digest({"decision": task_id}),
                        scope_paths=["control_plane/cli.py"],
                    )
                before = core_record_snapshot(repo)

                with patch.object(
                    CoreTaskStore,
                    "bind_lease_generation",
                    autospec=True,
                    side_effect=bind_then_fail,
                ):
                    return_code, payload = run_cli_in_process(
                        *task_start_arguments(repo, task_id, FIXTURE_POLICY)
                    )

                self.assertNotEqual(return_code, 0)
                self.assertFalse(payload["ok"])
                self.assertEqual(core_record_snapshot(repo), before)

    def test_task_start_interleaving_preserves_transition_and_compensates_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            task_id = "TASK-BINDING-INTERLEAVING"
            store = CoreTaskStore(repo)
            store.start(
                task_id,
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": task_id}),
                decision_digest=contract_digest({"decision": task_id}),
                scope_paths=["control_plane/cli.py"],
            )
            original_acquire = LeaseStore.acquire_with_origin

            def acquire_then_transition(
                lease_store: LeaseStore,
                *arguments: object,
                **keywords: object,
            ) -> tuple[dict, bool]:
                lease, created = original_acquire(
                    lease_store, *arguments, **keywords
                )
                CoreTaskStore(repo).transition(
                    task_id,
                    "planned",
                    current_branch="codex/core-test",
                )
                return lease, created

            with patch.object(
                LeaseStore,
                "acquire_with_origin",
                autospec=True,
                side_effect=acquire_then_transition,
            ):
                return_code, payload = run_cli_in_process(
                    *task_start_arguments(repo, task_id, FIXTURE_POLICY)
                )

            self.assertNotEqual(return_code, 0)
            self.assertFalse(payload["ok"])
            self.assertEqual(
                payload["errors"][0]["code"], "E_CORE_LEASE_BINDING"
            )
            self.assertEqual(store.status(task_id)["state"], "planned")
            self.assertEqual(LeaseStore(repo).active(), [])

    def test_preflight_and_risk_share_locked_continuation_validation(self) -> None:
        from control_plane.git_state import GateError, GateResult
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            (repo / ".codex" / "project-policy.toml").write_bytes(
                FIXTURE_POLICY.read_bytes()
            )
            policy = load_policy(FIXTURE_POLICY)
            policy_digest = contract_digest(policy)
            store = CoreTaskStore(repo)
            state = store.start(
                "TASK-SHARED-CONTINUATION",
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "shared-continuation"}),
                decision_digest=contract_digest({"decision": "shared-continuation"}),
                scope_paths=["."],
            )
            lease = LeaseStore(repo).acquire(
                state,
                session_id="SESSION-SHARED-CONTINUATION",
                policy_digest=policy_digest,
            )
            state = store.bind_lease_generation(
                state["task_id"],
                revision_id=lease["revision_id"],
                generation=lease["lease_generation"],
                expected_state_digest=state["state_digest"],
            )
            (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            observation = {"task": state, "lease": lease, "authorizes": False}

            with patch(
                "control_plane.task_state.validate_writer_continuation",
                return_value=observation,
            ) as shared:
                run_cli_in_process(
                    "preflight", "--mode", "write", "--repo", str(repo),
                    "--policy", str(FIXTURE_POLICY), "--task-id", state["task_id"],
                    "--session-id", "SESSION-SHARED-CONTINUATION", "--json",
                )
                with patch(
                    "control_plane.risk_sentinel.evaluate_preflight",
                    return_value=GateResult(
                        False,
                        "write",
                        {
                            "branch": "codex/core-test",
                            "dirty": True,
                            "changed_paths": ["dirty.txt"],
                        },
                        [],
                        [GateError("E_GIT_DIRTY", "dirty")],
                    ),
                ):
                    evaluate_local_risk(
                        repo,
                        None,
                        task_state=state,
                        local_lease_session_id="SESSION-SHARED-CONTINUATION",
                    )

            self.assertEqual(shared.call_count, 2)

    def test_core_entrypoint_uses_only_the_exact_test_manifest(self) -> None:
        from tests.test_core_governing_manifest import GOVERNING_TESTS

        source = (ROOT / "tests" / "run.sh").read_text(encoding="utf-8")
        match = re.search(r"CORE_TESTS='(?P<body>.*?)'", source, re.DOTALL)

        self.assertIsNotNone(match)
        observed = set(
            line for line in match.group("body").splitlines() if line
        )
        self.assertEqual(observed, GOVERNING_TESTS)
        self.assertNotIn("unittest discover", source)
        for module in ADVANCED_MODULES:
            self.assertNotIn(module.replace(".", "/") + ".py", source)


if __name__ == "__main__":
    unittest.main()
