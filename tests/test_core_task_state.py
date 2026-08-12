from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from control_plane.contracts import contract_digest
from control_plane.task_state import (
    CoreTaskStore,
    assert_no_active_legacy_state,
    inventory_legacy_state,
)


RUNTIME_DIGEST = "sha256:" + "1" * 64


def git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *arguments],
        text=True,
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def make_repo(root: Path) -> Path:
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Core Test")
    git(root, "config", "user.email", "core@example.invalid")
    (root / "README.md").write_text("core\n", encoding="utf-8")
    (root / ".codex").mkdir()
    (root / ".codex" / "project-policy.toml").write_bytes(
        (Path(__file__).parent / "fixtures" / "valid-policy.toml").read_bytes()
    )
    (root / ".codex" / "control-plane.lock").write_text(
        f'schema_version = 2\n[digests]\nruntime = "{RUNTIME_DIGEST}"\n',
        encoding="utf-8",
    )
    git(
        root,
        "add",
        "README.md",
        ".codex/control-plane.lock",
        ".codex/project-policy.toml",
    )
    git(root, "commit", "-qm", "initial")
    git(root, "switch", "-qc", "codex/core-test")
    return root


def legacy_task(task_id: str, *, state: str, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "task_id": task_id,
        "state": state,
        "resume_state": None,
        "resume_forbidden": False,
        "outcome": "local_change",
        "branch": "codex/core-test",
        "task_digest": contract_digest({"legacy-task": task_id}),
        "decision_digest": contract_digest({"legacy-decision": task_id}),
        "owner_runtime_digest": RUNTIME_DIGEST,
    }
    value.update(overrides)
    return value


class CoreTaskStateTests(unittest.TestCase):
    def test_configured_protected_base_is_bound_and_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            governing = repo / ".codex" / "project-policy.toml"
            governing.write_text(
                governing.read_text(encoding="utf-8").replace(
                    'base_branch = "main"', 'base_branch = "trunk"'
                ),
                encoding="utf-8",
            )
            store = CoreTaskStore(repo)
            common = {
                "outcome": "local_change",
                "head": git(repo, "rev-parse", "HEAD"),
                "task_digest": contract_digest({"task": "configured-base"}),
                "decision_digest": contract_digest({"decision": "configured-base"}),
                "scope_paths": ["control_plane/task_state.py"],
                "protected_base": "trunk",
            }

            with self.assertRaisesRegex(ValueError, "E_CORE_STATE_BRANCH"):
                store.start("TASK-PROTECTED-BASE", branch="trunk", **common)
            self.assertFalse(store.tasks.exists())

            with self.assertRaisesRegex(ValueError, "E_CORE_STATE_BRANCH"):
                store.start(
                    "TASK-FORGED-PROTECTED-BASE",
                    branch="codex/core-test",
                    **{**common, "protected_base": "main"},
                )
            self.assertFalse(store.tasks.exists())

            state = store.start("TASK-NON-PROTECTED-MAIN", branch="main", **common)
            self.assertEqual(state["protected_base"], "trunk")
            self.assertEqual(store.status(state["task_id"]), state)

    def test_active_legacy_state_blocks_core_start_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            legacy_tasks = git_dir / "codex-control-plane" / "tasks"
            legacy_tasks.mkdir(parents=True)
            (legacy_tasks / "TASK-ACTIVE-LEGACY.json").write_text(
                json.dumps(legacy_task("TASK-ACTIVE-LEGACY", state="implementing")) + "\n",
                encoding="utf-8",
            )
            store = CoreTaskStore(repo)

            with self.assertRaisesRegex(ValueError, "E_ACTIVE_LEGACY_STATE"):
                store.start(
                    "TASK-CORE-BLOCKED-BY-LEGACY",
                    outcome="local_change",
                    branch="codex/core-test",
                    protected_base="main",
                    head=git(repo, "rev-parse", "HEAD"),
                    task_digest=contract_digest({"task": "legacy-barrier"}),
                    decision_digest=contract_digest({"decision": "legacy-barrier"}),
                    scope_paths=["control_plane/task_state.py"],
                )

            self.assertFalse(store.tasks.exists())

    def test_active_legacy_state_blocks_existing_core_start_replay(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            arguments = {
                "outcome": "local_change",
                "branch": "codex/core-test",
                "protected_base": "main",
                "head": git(repo, "rev-parse", "HEAD"),
                "task_digest": contract_digest({"task": "legacy-replay"}),
                "decision_digest": contract_digest({"decision": "legacy-replay"}),
                "scope_paths": ["control_plane/task_state.py"],
            }
            state = store.start("TASK-CORE-LEGACY-REPLAY", **arguments)
            path = store.tasks / "TASK-CORE-LEGACY-REPLAY.json"
            before = path.read_bytes()
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            legacy_tasks = git_dir / "codex-control-plane" / "tasks"
            legacy_tasks.mkdir(parents=True)
            (legacy_tasks / "TASK-ACTIVE-REPLAY.json").write_text(
                json.dumps(legacy_task("TASK-ACTIVE-REPLAY", state="implementing")) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "E_ACTIVE_LEGACY_STATE"):
                store.start("TASK-CORE-LEGACY-REPLAY", **arguments)

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.status(state["task_id"]), state)

    def test_resigned_state_rejects_extra_fields_and_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            state = store.start(
                "TASK-CLOSED-SCHEMA",
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "closed-schema"}),
                decision_digest=contract_digest({"decision": "closed-schema"}),
                scope_paths=["control_plane/"],
            )
            path = store.tasks / "TASK-CLOSED-SCHEMA.json"
            for mutation in (
                {"unexpected_field": "resigned"},
                {"authorizes": True},
            ):
                tampered = {**state, **mutation}
                tampered.pop("state_digest", None)
                tampered["state_digest"] = contract_digest(tampered)
                path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
                path.chmod(0o600)
                with self.assertRaisesRegex(ValueError, "E_CORE_STATE_INVALID"):
                    store.status("TASK-CLOSED-SCHEMA")

    def test_facts_only_never_creates_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            result = store.start(
                "TASK-FACTS",
                outcome="answer",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "facts"}),
                decision_digest=contract_digest({"decision": "facts"}),
                scope_paths=["."],
            )
            self.assertFalse(result["persisted"])
            self.assertFalse((store.tasks / "TASK-FACTS.json").exists())

    def test_local_state_uses_exact_closed_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            state = store.start(
                "TASK-LOCAL",
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "local"}),
                decision_digest=contract_digest({"decision": "local"}),
                scope_paths=["control_plane"],
            )
            self.assertEqual(state["state"], "framed")
            for target in ("planned", "ready", "implementing", "verifying", "review_ready", "closed"):
                state = store.transition(
                    "TASK-LOCAL", target, current_branch="codex/core-test"
                )
            self.assertEqual(state["state"], "closed")
            with self.assertRaisesRegex(ValueError, "E_CORE_STATE_TRANSITION"):
                store.transition(
                    "TASK-LOCAL", "implementing", current_branch="codex/core-test"
                )

    def test_failed_binding_restore_rejects_signed_stable_field_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            original = store.start(
                "TASK-ROLLBACK-DRIFT",
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "rollback-drift"}),
                decision_digest=contract_digest({"decision": "original"}),
                scope_paths=["control_plane/task_state.py"],
            )
            current = store.bind_lease_generation(
                original["task_id"],
                revision_id=original["revision_id"],
                generation=1,
                expected_state_digest=original["state_digest"],
            )
            mutated = dict(current)
            mutated["decision_digest"] = contract_digest({"decision": "drifted"})
            mutated.pop("state_digest")
            mutated["state_digest"] = contract_digest(mutated)
            path = store.tasks / "TASK-ROLLBACK-DRIFT.json"
            path.write_bytes(
                (json.dumps(mutated, sort_keys=True, separators=(",", ":")) + "\n").encode()
            )
            before = path.read_bytes()

            with self.assertRaisesRegex(ValueError, "E_CORE_STATE_ROLLBACK"):
                store.restore_after_failed_binding(
                    original,
                    expected_revision_id=original["revision_id"],
                    expected_generation=1,
                )

            self.assertEqual(path.read_bytes(), before)

    def test_legacy_inventory_is_read_only_and_non_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            task_dir = git_dir / "codex-control-plane" / "tasks"
            task_dir.mkdir(parents=True)
            legacy = task_dir / "TASK-OLD.json"
            legacy.write_text('{"state":"implementing"}\n', encoding="utf-8")
            before = legacy.read_bytes()
            result = inventory_legacy_state(repo)
            self.assertTrue(result["active"])
            self.assertFalse(result["resumable"])
            self.assertEqual(result["records"][0]["origin"], "legacy")
            self.assertEqual(legacy.read_bytes(), before)

    def test_legacy_blocked_task_is_active_only_when_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            task_dir = git_dir / "codex-control-plane" / "tasks"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK-RESUMABLE.json").write_text(
                json.dumps(
                    legacy_task(
                        "TASK-RESUMABLE",
                        state="blocked",
                        resume_state="implementing",
                        resume_forbidden=False,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            (task_dir / "TASK-FINAL-BLOCK.json").write_text(
                json.dumps(
                    legacy_task(
                        "TASK-FINAL-BLOCK",
                        state="blocked",
                        resume_state=None,
                        resume_forbidden=True,
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            result = inventory_legacy_state(repo)
            by_task = {
                record["task_id"]: record
                for record in result["records"]
                if record["kind"] == "task"
            }
            self.assertTrue(by_task["TASK-RESUMABLE"]["active"])
            self.assertFalse(by_task["TASK-FINAL-BLOCK"]["active"])
            with self.assertRaisesRegex(ValueError, "E_ACTIVE_LEGACY_STATE"):
                assert_no_active_legacy_state(repo)

    def test_remote_unknown_is_active_on_every_legacy_record_kind(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            root = git_dir / "codex-control-plane"
            tasks = root / "tasks"
            tasks.mkdir(parents=True)
            task_id = "TASK-REMOTE-UNKNOWN"
            task = legacy_task(task_id, state="closed", remote_status="UNKNOWN")
            (tasks / f"{task_id}.json").write_text(
                json.dumps(task) + "\n", encoding="utf-8"
            )
            run = root / "runs" / task_id
            run.mkdir(parents=True)
            (run / "plan.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "RunPlanV1",
                        "task_id": task_id,
                        "remote_status": "UNKNOWN",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = inventory_legacy_state(repo)
            self.assertTrue(result["active"])
            self.assertTrue(all(record["active"] for record in result["records"]))

    def test_nested_pending_and_base_refresh_unknown_are_active_without_payload_echo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            root = git_dir / "codex-control-plane"
            tasks = root / "tasks"
            refresh = root / "base-refresh-observations"
            tasks.mkdir(parents=True)
            refresh.mkdir()
            task_id = "TASK-REMOTE-MARKERS"
            (tasks / f"{task_id}.json").write_text(
                json.dumps(
                    legacy_task(
                        task_id,
                        state="blocked",
                        resume_forbidden=True,
                        pending_remote_effect={
                            "status": "UNKNOWN",
                            "phase": "observe_only",
                            "opaque": "must-not-be-emitted",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            (refresh / "refresh.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "refresh_receipt": {
                            "status": "UNKNOWN",
                            "opaque": "must-not-be-emitted",
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = inventory_legacy_state(repo)

            self.assertTrue(result["active"])
            self.assertEqual(
                {record["kind"] for record in result["records"]},
                {"task", "remote_unknown"},
            )
            self.assertNotIn("must-not-be-emitted", json.dumps(result))

    def test_closed_legacy_task_with_bound_run_history_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            root = git_dir / "codex-control-plane"
            task_id = "TASK-CLOSED-HISTORY"
            tasks = root / "tasks"
            tasks.mkdir(parents=True)
            (tasks / f"{task_id}.json").write_text(
                json.dumps(legacy_task(task_id, state="closed")) + "\n",
                encoding="utf-8",
            )
            run = root / "runs" / task_id
            (run / "receipts").mkdir(parents=True)
            (run / "plan.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "RunPlanV1",
                        "task_id": task_id,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run / "receipts" / "historical.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "GateReceiptV1",
                        "task_id": task_id,
                        "status": "PASS",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = inventory_legacy_state(repo)
            self.assertFalse(result["active"])
            self.assertTrue(result["records"])
            self.assertTrue(all(not record["active"] for record in result["records"]))

    def test_run_history_stays_active_while_same_task_has_legacy_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            root = git_dir / "codex-control-plane"
            task_id = "TASK-CLOSED-WITH-LEASE"
            tasks = root / "tasks"
            leases = root / "leases"
            run = root / "runs" / task_id
            tasks.mkdir(parents=True)
            leases.mkdir()
            run.mkdir(parents=True)
            (tasks / f"{task_id}.json").write_text(
                json.dumps(legacy_task(task_id, state="closed")) + "\n",
                encoding="utf-8",
            )
            (leases / "lease.json").write_text(
                json.dumps({"schema_version": 1, "task_id": task_id}) + "\n",
                encoding="utf-8",
            )
            (run / "plan.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "RunPlanV1",
                        "task_id": task_id,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = inventory_legacy_state(repo)
            run_record = next(
                record for record in result["records"] if record["kind"] == "run"
            )
            self.assertTrue(run_record["active"])

    def test_unknown_or_incomplete_legacy_contract_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            task_dir = git_dir / "codex-control-plane" / "tasks"
            task_dir.mkdir(parents=True)
            (task_dir / "TASK-INCOMPLETE.json").write_text(
                '{"schema_version":1,"task_id":"TASK-INCOMPLETE","state":"closed"}\n',
                encoding="utf-8",
            )
            (task_dir / "TASK-MALFORMED.json").write_text("{\n", encoding="utf-8")

            result = inventory_legacy_state(repo)
            self.assertTrue(result["active"])
            self.assertEqual(len(result["records"]), 2)
            self.assertTrue(all(record["active"] for record in result["records"]))
            self.assertTrue(all(record["state"] == "UNKNOWN" for record in result["records"]))

    def test_legacy_inventory_rejects_symlinked_intermediate_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = make_repo(base / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            root = git_dir / "codex-control-plane"
            root.mkdir()
            outside = base / "outside-tasks"
            outside.mkdir()
            external = outside / "TASK-OUTSIDE.json"
            external.write_text(
                json.dumps(legacy_task("TASK-OUTSIDE", state="implementing")) + "\n",
                encoding="utf-8",
            )
            before = external.read_bytes()
            (root / "tasks").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "E_LEGACY_STATE_UNKNOWN"):
                inventory_legacy_state(repo)

            self.assertEqual(external.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
