from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
import tempfile
import unittest

from control_plane.adoption_recovery import adoption_status
from control_plane.contracts import contract_digest
from control_plane.leases import LeaseStore
from control_plane.maintenance import MaintenanceStore
from control_plane.task_state import CoreTaskStore
from control_plane.verification import VerificationMutex
from tests.test_core_task_state import git, make_repo


def external_file_snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mode & 0o777


def enter_context(manager: object) -> object:
    with manager as value:  # type: ignore[attr-defined]
        return value


def fake_state(repo: Path, task_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "CoreTaskStateV1",
        "task_id": task_id,
        "revision_id": "rev-0123456789abcdef",
        "requested_outcome": "local_change",
        "state": "framed",
        "worktree": str(repo),
        "branch": "codex/core-test",
        "scope_paths": ["control_plane"],
        "owner_runtime_digest": "sha256:" + "1" * 64,
        "state_digest": contract_digest({"fixture": task_id}),
    }


class CoreStatePathTests(unittest.TestCase):
    def test_core_state_ancestor_symlink_never_crosses_git_dir(self) -> None:
        operations = (
            (
                "task",
                lambda repo: CoreTaskStore(repo).start(
                    "TASK-PATH",
                    outcome="local_change",
                    branch="codex/core-test",
                    head=git(repo, "rev-parse", "HEAD"),
                    task_digest=contract_digest({"task": "path"}),
                    decision_digest=contract_digest({"decision": "path"}),
                    scope_paths=["control_plane"],
                ),
            ),
            (
                "lease",
                lambda repo: LeaseStore(repo).acquire(
                    fake_state(repo, "TASK-LEASE-PATH"),
                    session_id="SESSION-PATH",
                    policy_digest=contract_digest({"policy": "path"}),
                ),
            ),
            (
                "verification",
                lambda repo: enter_context(VerificationMutex(repo)),
            ),
            (
                "maintenance",
                lambda repo: MaintenanceStore(repo).open(
                    lineage_id="MAINT-PATH",
                    stable_runtime_digest="sha256:" + "1" * 64,
                    candidate_runtime_digest="sha256:" + "2" * 64,
                ),
            ),
        )
        for name, operation in operations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                repo = make_repo(base / "repo")
                git_dir = Path(
                    git(repo, "rev-parse", "--path-format=absolute", "--git-dir")
                )
                outside = base / "outside"
                outside.mkdir(mode=0o700)
                (git_dir / "codex-control-plane-core").symlink_to(
                    outside, target_is_directory=True
                )

                with self.assertRaisesRegex(ValueError, "PATH|LOCK"):
                    operation(repo)

                self.assertEqual(tuple(outside.iterdir()), ())

    def test_lock_leaf_symlink_never_mutates_external_file(self) -> None:
        cases: list[tuple[str, str, str, object]] = []
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = make_repo(base / "repo")
            git_dir = Path(
                git(repo, "rev-parse", "--path-format=absolute", "--git-dir")
            )
            common = Path(
                git(
                    repo,
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                )
            )
            external = base / "external.lock"
            external.write_text("external\n", encoding="utf-8")
            external.chmod(0o644)
            before = external_file_snapshot(external)
            lease_state = CoreTaskStore(repo).start(
                "TASK-LEASE-LOCK-PATH",
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "lease-lock-path"}),
                decision_digest=contract_digest({"decision": "lease-lock-path"}),
                scope_paths=["control_plane"],
            )

            task_id = "TASK-LOCK-PATH"
            task_lock = (
                git_dir
                / "codex-control-plane-core"
                / "locks"
                / "tasks"
                / f"{sha256(task_id.encode()).hexdigest()}.lock"
            )
            cases.append(
                (
                    "task",
                    "E_CORE_STATE_PATH",
                    str(task_lock),
                    lambda: CoreTaskStore(repo).start(
                        task_id,
                        outcome="local_change",
                        branch="codex/core-test",
                        head=git(repo, "rev-parse", "HEAD"),
                        task_digest=contract_digest({"task": "lock-path"}),
                        decision_digest=contract_digest({"decision": "lock-path"}),
                        scope_paths=["control_plane"],
                    ),
                )
            )
            cases.append(
                (
                    "lease",
                    "E_CORE_LEASE_PATH",
                    str(
                        common
                        / "codex-control-plane-core"
                        / "locks"
                        / "leases.lock"
                    ),
                    lambda: LeaseStore(repo).acquire(
                        lease_state,
                        session_id="SESSION-LOCK-PATH",
                        policy_digest=contract_digest({"policy": "lock-path"}),
                    ),
                )
            )
            cases.append(
                (
                    "verification",
                    "E_VERIFICATION_LOCK",
                    str(
                        common
                        / "codex-control-plane-core"
                        / "locks"
                        / "verification.lock"
                    ),
                    lambda: enter_context(VerificationMutex(repo)),
                )
            )
            cases.append(
                (
                    "maintenance",
                    "E_MAINTENANCE_PATH",
                    str(
                        common
                        / "codex-control-plane-core"
                        / "locks"
                        / "maintenance.lock"
                    ),
                    lambda: MaintenanceStore(repo).open(
                        lineage_id="MAINT-LOCK-PATH",
                        stable_runtime_digest="sha256:" + "1" * 64,
                        candidate_runtime_digest="sha256:" + "2" * 64,
                    ),
                )
            )
            for name, code, raw_lock, operation in cases:
                with self.subTest(name=name):
                    lock = Path(raw_lock)
                    lock.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                    os.chmod(lock.parent, 0o700)
                    try:
                        existing = lock.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        self.assertTrue(stat.S_ISREG(existing.st_mode))
                        self.assertEqual(existing.st_nlink, 1)
                        lock.unlink()
                    lock.symlink_to(external)
                    with self.assertRaisesRegex(ValueError, code):
                        operation()
                    self.assertEqual(external_file_snapshot(external), before)
                    lock.unlink()

    def test_adoption_journal_ancestor_symlink_is_rejected_without_reading_outside(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = make_repo(base / "repo")
            git_dir = Path(
                git(repo, "rev-parse", "--path-format=absolute", "--git-dir")
            )
            outside = base / "outside-adoption"
            outside.mkdir(mode=0o700)
            journal = outside / "adoption.json"
            journal.write_text('{"schema_version":2,"status":"applied"}\n')
            before = external_file_snapshot(journal)
            (git_dir / "codex-control-plane").symlink_to(
                outside, target_is_directory=True
            )

            result = adoption_status(repo)
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(
                result["errors"][0]["code"], "E_ADOPT_RECOVERY_UNKNOWN"
            )

            self.assertEqual(external_file_snapshot(journal), before)


if __name__ == "__main__":
    unittest.main()
