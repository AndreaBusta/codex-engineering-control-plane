from __future__ import annotations

from contextlib import contextmanager
import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import adoption_enablement.transaction as transaction
import control_plane.task_state as core_task_state
from adoption_enablement.contracts import (
    contract_digest,
    load_closed_json,
    validate_journal,
    validate_receipt,
)
from adoption_enablement.manifest import preview
from adoption_enablement.transaction import apply_plan, rollback, verify
from control_plane.task_state import CoreTaskStore
from tests.adoption_enablement_test_support import (
    git,
    initialize_fresh_target,
    initialize_full_source,
    metadata_snapshot,
    write_file,
)


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_STATES = {
    "prepared": "prepared",
    "staged": "staged",
    "published_inactive": "published_inactive",
    "hooks_configured": "published_inactive",
    "activation_published": "published_inactive",
    "active": "active",
}


def _journal_path(target: Path) -> Path:
    return target / ".git" / "codex-control-plane-core" / "adoption" / "journal.json"


def _journal(target: Path) -> dict[str, object]:
    return load_closed_json(_journal_path(target).read_bytes(), limit=1024 * 1024)


def _hooks_path(target: Path) -> bytes:
    return git(
        target,
        "config",
        "--local",
        "--get-all",
        "core.hooksPath",
        check=False,
    ).stdout


def _assert_record(test: unittest.TestCase, target: Path, record: dict[str, object]) -> None:
    path = target / str(record["path"])
    payload = path.read_bytes()
    metadata = path.lstat()
    test.assertTrue(stat.S_ISREG(metadata.st_mode))
    test.assertEqual("sha256:" + sha256(payload).hexdigest(), record["sha256"])
    test.assertEqual(len(payload), record["size_bytes"])
    expected = 0o755 if record["git_mode"] == "100755" else 0o644
    test.assertEqual(stat.S_IMODE(metadata.st_mode), expected)


class InjectedFault(RuntimeError):
    pass


class AdoptionTransactionTests(unittest.TestCase):
    def test_new_task_holds_a_lifecycle_domain_even_when_adoption_was_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            repository = initialize_full_source(container / "repository", ROOT)
            for relative in (
                ".codex/project-policy.toml",
                ".codex/resource-registry.toml",
            ):
                write_file(repository, relative, (ROOT / relative).read_bytes())
            entered = threading.Event()
            release = threading.Event()
            failures: list[BaseException] = []
            states: list[dict[str, object]] = []
            real_task_lock = core_task_state._task_lock

            @contextmanager
            def pause_before_task_lock(*arguments: object, **keywords: object):
                entered.set()
                if not release.wait(timeout=10):
                    raise AssertionError("task-lock pause timed out")
                with real_task_lock(*arguments, **keywords):
                    yield

            def start_task() -> None:
                try:
                    states.append(
                        CoreTaskStore(repository).start(
                            "TASK-LIFECYCLE-ABSENT-RACE",
                            outcome="local_change",
                            branch=git(repository, "branch", "--show-current").stdout.strip().decode(),
                            head=git(repository, "rev-parse", "HEAD").stdout.strip().decode(),
                            task_digest=contract_digest({"task": "lifecycle-absent"}),
                            decision_digest=contract_digest({"decision": "lifecycle-absent"}),
                            scope_paths=["local/lifecycle"],
                        )
                    )
                except BaseException as error:
                    failures.append(error)

            with patch.object(
                core_task_state,
                "_task_lock",
                side_effect=pause_before_task_lock,
            ):
                worker = threading.Thread(target=start_task, daemon=True)
                worker.start()
                self.assertTrue(entered.wait(timeout=10))
                lifecycle = (
                    repository
                    / ".git"
                    / transaction.STATE_ROOT
                    / transaction.LOCK_NAME
                )
                try:
                    self.assertTrue(lifecycle.is_file())
                    common_raw = git(
                        repository,
                        "rev-parse",
                        "--git-common-dir",
                    ).stdout.strip().decode()
                    common = Path(common_raw)
                    if not common.is_absolute():
                        common = repository / common
                    with self.assertRaisesRegex(ValueError, "^E_ADOPTION_BUSY"):
                        with transaction._adoption_lock(
                            common.resolve(strict=True),
                            create=True,
                        ):
                            pass
                finally:
                    release.set()
                    worker.join(timeout=10)

            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(len(states), 1)

    def test_apply_and_rollback_preserve_a_preexisting_managed_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            write_file(target, "scripts/keep.txt", "keep\n")
            write_file(target, ".codex/hooks/keep.txt", "keep hook parent\n")
            git(target, "add", "--all")
            git(target, "commit", "-m", "preexisting managed parent")
            parents = (target / "scripts", target / ".codex/hooks")
            parent_identities = {
                path: (path.lstat().st_dev, path.lstat().st_ino, path.lstat().st_mode)
                for path in parents
            }
            parent_snapshots = {
                path: metadata_snapshot(path)
                for path in parents
            }
            plan = preview(source, target)

            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            created = {
                str(record["path"])
                for record in _journal(target)["created_directories"]
            }
            rollback_receipt = rollback(
                target,
                install_digest=str(receipt["install_digest"]),
            )

            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(rollback_receipt["result"], "PASS")
            self.assertNotIn("scripts", created)
            self.assertNotIn(".codex/hooks", created)
            for path in parents:
                after = path.lstat()
                self.assertEqual(
                    (after.st_dev, after.st_ino, after.st_mode),
                    parent_identities[path],
                )
                self.assertEqual(metadata_snapshot(path), parent_snapshots[path])
            self.assertEqual((target / "scripts" / "keep.txt").read_text(), "keep\n")
            self.assertEqual(
                (target / ".codex/hooks/keep.txt").read_text(),
                "keep hook parent\n",
            )
            self.assertFalse((target / "scripts" / "control-plane").exists())
            self.assertFalse((target / "control_plane").exists())

    def test_parent_identity_drift_fails_before_adoption_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            write_file(target, "scripts/keep.txt", "keep\n")
            git(target, "add", "--all")
            git(target, "commit", "-m", "preexisting managed parent")
            plan = preview(source, target)

            original = target / "scripts"
            displaced = target / "scripts-displaced"
            original.rename(displaced)
            original.mkdir(mode=0o755)
            (displaced / "keep.txt").rename(original / "keep.txt")
            displaced.rmdir()
            self.assertEqual(git(target, "status", "--porcelain").stdout, b"")

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_DRIFT"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            self.assertFalse((target / ".git/codex-control-plane-core").exists())

    def test_parent_drift_after_prepared_fails_before_target_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            write_file(target, "scripts/keep.txt", "keep\n")
            git(target, "add", "--all")
            git(target, "commit", "-m", "preexisting scripts parent")
            plan = preview(source, target)

            def replace_parent(boundary: str) -> None:
                if boundary != "prepared":
                    return
                original = target / "scripts"
                displaced = target / "scripts-displaced"
                original.rename(displaced)
                original.mkdir(mode=0o755)
                (displaced / "keep.txt").rename(original / "keep.txt")
                displaced.rmdir()

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_DRIFT"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                    fault=replace_parent,
                )

            self.assertFalse((target / "control_plane").exists())
            self.assertFalse((target / "scripts/control-plane").exists())
            self.assertEqual(_journal(target)["state"], "prepared")

    def test_parent_drift_after_apply_blocks_verify_and_rollback_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            write_file(target, "scripts/keep.txt", "keep\n")
            git(target, "add", "--all")
            git(target, "commit", "-m", "preexisting scripts parent")
            plan = preview(source, target)
            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            original = target / "scripts"
            displaced = target / "scripts-displaced"
            original.rename(displaced)
            original.mkdir(mode=0o755)
            for child in tuple(displaced.iterdir()):
                child.rename(original / child.name)
            displaced.rmdir()
            activation = target / ".codex/control-plane.lock"
            activation_before = activation.read_bytes()
            hooks_before = _hooks_path(target)

            verification = verify(target)
            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_ROLLBACK_DRIFT"):
                rollback(target, install_digest=str(receipt["install_digest"]))

            self.assertEqual(verification["result"], "FAIL")
            self.assertEqual(activation.read_bytes(), activation_before)
            self.assertEqual(_hooks_path(target), hooks_before)
            self.assertTrue((target / "scripts/control-plane").exists())
            self.assertEqual(_journal(target)["state"], "active")

    def test_nested_repository_drift_after_apply_blocks_verify_and_rollback_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            marker = target / "scripts" / "nested" / ".git"
            marker.mkdir(parents=True)
            activation = target / ".codex/control-plane.lock"
            activation_before = activation.read_bytes()
            hooks_before = _hooks_path(target)

            verification = verify(target)
            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_NESTED_REPOSITORY"):
                rollback(target, install_digest=str(receipt["install_digest"]))

            self.assertEqual(verification["result"], "FAIL")
            self.assertEqual(activation.read_bytes(), activation_before)
            self.assertEqual(_hooks_path(target), hooks_before)
            self.assertTrue((target / "scripts/control-plane").exists())
            self.assertEqual(_journal(target)["state"], "active")

    def test_missing_or_replaced_lifecycle_lock_blocks_core_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            task_id = "TASK-MISSING-ADOPTION-MUTEX"
            branch = git(target, "branch", "--show-current").stdout.decode().strip()
            head = git(target, "rev-parse", "HEAD").stdout.decode().strip()
            store = CoreTaskStore(target)
            state = store.start(
                task_id,
                outcome="local_change",
                branch=branch,
                head=head,
                task_digest="sha256:" + "5" * 64,
                decision_digest="sha256:" + "6" * 64,
                scope_paths=["AGENTS.md"],
            )
            for next_state in (
                "planned",
                "ready",
                "implementing",
                "verifying",
                "review_ready",
                "closed",
            ):
                state = store.transition(task_id, next_state, current_branch=branch)
            task_before = store.status(task_id)
            lock = target / ".git/codex-control-plane-core/adoption.lock"
            old_descriptor = os.open(lock, os.O_RDWR)
            try:
                import fcntl

                fcntl.flock(old_descriptor, fcntl.LOCK_SH)
                lock.unlink()
                lock.write_bytes(b"")
                lock.chmod(0o600)

                with self.assertRaisesRegex(ValueError, "^E_CORE_LEASE_PATH"):
                    store.next_revision(
                        task_id,
                        current_branch=branch,
                        head=head,
                        task_digest="sha256:" + "7" * 64,
                        decision_digest="sha256:" + "8" * 64,
                        scope_paths=["AGENTS.md"],
                    )
                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_LOCK"):
                    rollback(target, install_digest=str(receipt["install_digest"]))
            finally:
                import fcntl

                fcntl.flock(old_descriptor, fcntl.LOCK_UN)
                os.close(old_descriptor)

            self.assertEqual(store.status(task_id), task_before)
            self.assertTrue((target / ".codex/control-plane.lock").exists())
            self.assertEqual(_journal(target)["state"], "active")

    def test_rollback_excludes_a_waiting_closed_task_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            task_id = "TASK-ROLLBACK-LIFECYCLE-RACE"
            branch = git(target, "branch", "--show-current").stdout.decode().strip()
            head = git(target, "rev-parse", "HEAD").stdout.decode().strip()
            store = CoreTaskStore(target)
            state = store.start(
                task_id,
                outcome="local_change",
                branch=branch,
                head=head,
                task_digest="sha256:" + "1" * 64,
                decision_digest="sha256:" + "2" * 64,
                scope_paths=["AGENTS.md"],
            )
            for next_state in (
                "planned",
                "ready",
                "implementing",
                "verifying",
                "review_ready",
                "closed",
            ):
                state = store.transition(
                    task_id,
                    next_state,
                    current_branch=branch,
                )
            self.assertEqual(state["state"], "closed")

            marker = container / "revision-waiting"
            child: subprocess.Popen[bytes] | None = None
            code = r'''
from contextlib import contextmanager
from pathlib import Path
import sys
import control_plane.leases as leases_module
from control_plane.task_state import CoreTaskStore

target = Path(sys.argv[1])
marker = Path(sys.argv[2])
original = leases_module._adoption_lifecycle_lock

@contextmanager
def marked(repository, common_git_dir):
    marker.write_text("waiting\n", encoding="utf-8")
    with original(repository, common_git_dir):
        yield

leases_module._adoption_lifecycle_lock = marked
CoreTaskStore(target).next_revision(
    "TASK-ROLLBACK-LIFECYCLE-RACE",
    current_branch="codex/adoption-target",
    head="''' + head + r'''",
    task_digest="sha256:" + "3" * 64,
    decision_digest="sha256:" + "4" * 64,
    scope_paths=["AGENTS.md"],
)
'''

            def start_waiting_revision(boundary: str) -> None:
                nonlocal child
                if boundary != "rolling_back":
                    return
                environment = {
                    "HOME": "/var/empty",
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(target),
                }
                child = subprocess.Popen(
                    [sys.executable, "-B", "-c", code, str(target), str(marker)],
                    cwd=container,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    if child.poll() is not None:
                        break
                    time.sleep(0.005)
                self.assertTrue(
                    marker.exists(),
                    "revision process did not reach the lifecycle mutex",
                )

            try:
                rollback_receipt = rollback(
                    target,
                    install_digest=str(receipt["install_digest"]),
                    fault=start_waiting_revision,
                )
                self.assertIsNotNone(child)
                stdout, stderr = child.communicate(timeout=10)
            finally:
                if child is not None and child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)

            self.assertEqual(rollback_receipt["result"], "PASS")
            self.assertNotEqual(child.returncode, 0, (stdout, stderr))
            task_path = target / ".git/codex-control-plane-core/tasks" / f"{task_id}.json"
            persisted = json.loads(task_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["state"], "closed")

    def test_atomic_publication_never_replaces_a_concurrent_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            rename_noreplace = transaction._rename_noreplace
            competed = False

            def publish_with_competitor(
                source_name: str,
                destination_name: str,
                *,
                source_directory: int,
                destination_directory: int,
            ) -> None:
                nonlocal competed
                if destination_name == "hooks.json" and not competed:
                    competed = True
                    descriptor = os.open(
                        destination_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o644,
                        dir_fd=destination_directory,
                    )
                    try:
                        os.write(descriptor, b"competitor\n")
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                    os.fsync(destination_directory)
                rename_noreplace(
                    source_name,
                    destination_name,
                    source_directory=source_directory,
                    destination_directory=destination_directory,
                )

            with patch.object(
                transaction,
                "_rename_noreplace",
                side_effect=publish_with_competitor,
            ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_DRIFT"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            self.assertTrue(competed)
            self.assertEqual((target / ".codex" / "hooks.json").read_bytes(), b"competitor\n")

    def test_crash_before_created_directory_identity_never_leaves_ambiguous_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            real_stat = transaction.os.stat
            interrupted = False
            first_staged_name = transaction._directory_record_name("control_plane")

            def interrupt_first_directory_identity(
                path: object,
                *arguments: object,
                **keywords: object,
            ) -> os.stat_result:
                nonlocal interrupted
                if path == first_staged_name and keywords.get("dir_fd") is not None and not interrupted:
                    interrupted = True
                    raise InjectedFault("directory-identity")
                return real_stat(path, *arguments, **keywords)

            with patch.object(
                transaction.os,
                "stat",
                side_effect=interrupt_first_directory_identity,
            ), self.assertRaisesRegex(InjectedFault, "^directory-identity$"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            journal = _journal(target)
            self.assertTrue(interrupted)
            self.assertIsNone(journal["created_directories"][0]["identity"])
            receipt = rollback(target, install_digest=journal["install_digest"])
            self.assertEqual(receipt["result"], "PASS")
            self.assertFalse((target / "control_plane").exists())

    def test_apply_publishes_inactive_bytes_then_activation_last(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            observed: list[str] = []

            def checkpoint(boundary: str) -> None:
                observed.append(boundary)
                journal = _journal(target)
                self.assertEqual(journal["state"], BOUNDARY_STATES[boundary])
                self.assertEqual(validate_journal(journal), ())
                lock_exists = (target / ".codex" / "control-plane.lock").exists()
                if boundary in {"prepared", "staged", "published_inactive", "hooks_configured"}:
                    self.assertFalse(lock_exists)
                if boundary == "published_inactive":
                    for record in plan["managed_records"]:
                        if record["path"] != ".codex/control-plane.lock":
                            _assert_record(self, target, record)
                    self.assertEqual(_hooks_path(target), b"")
                    self._assert_inactive_entrypoints_fail_closed(target)
                if boundary == "hooks_configured":
                    self.assertEqual(_hooks_path(target), b".codex/git-hooks\n")
                if boundary in {"activation_published", "active"}:
                    for record in plan["managed_records"]:
                        _assert_record(self, target, record)
                    self.assertEqual(_hooks_path(target), b".codex/git-hooks\n")

            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
                fault=checkpoint,
            )

            self.assertEqual(
                observed,
                [
                    "prepared",
                    "staged",
                    "published_inactive",
                    "hooks_configured",
                    "activation_published",
                    "active",
                ],
            )
            self.assertEqual(validate_receipt(receipt), ())
            self.assertEqual(receipt["operation"], "apply")
            self.assertEqual(receipt["result"], "PASS")
            self.assertIs(receipt["authorizes"], False)

    def test_apply_verifies_the_active_generation_before_emitting_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)

            def drift_after_activation(boundary: str) -> None:
                if boundary == "active":
                    (target / ".codex" / "hooks.json").write_text(
                        '{"drift":true}\n',
                        encoding="utf-8",
                    )

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_VERIFY_DRIFT"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                    fault=drift_after_activation,
                )

            self.assertEqual(_journal(target)["state"], "active")
            evidence = (
                target
                / ".git"
                / "codex-control-plane-core"
                / "adoption"
                / "evidence"
            )
            self.assertFalse(evidence.exists())

    def test_created_directory_identities_are_durable_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)

            with patch.object(
                transaction,
                "_stage_projection",
                side_effect=InjectedFault("after-directory-creation"),
            ):
                with self.assertRaisesRegex(InjectedFault, "^after-directory-creation$"):
                    apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                    )

            journal = _journal(target)
            self.assertEqual(journal["state"], "prepared")
            self.assertTrue(journal["created_directories"])
            self.assertTrue(
                all(
                    isinstance(record["identity"], list)
                    and len(record["identity"]) == 2
                    for record in journal["created_directories"]
                )
            )

            receipt = rollback(target, install_digest=journal["install_digest"])

            self.assertEqual(receipt["operation"], "rollback")
            self.assertEqual(receipt["result"], "PASS")
            for record in journal["created_directories"]:
                self.assertFalse((target / str(record["path"])).exists())

    def test_each_created_directory_identity_is_durable_before_the_next_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            write_journal = transaction._write_journal

            def interrupt_after_first_identity(
                adoption_directory: Path,
                journal: dict[str, object],
            ) -> None:
                write_journal(adoption_directory, journal)
                created = journal["created_directories"]
                if (
                    journal["state"] == "prepared"
                    and isinstance(created, list)
                    and sum(record["identity"] is not None for record in created) == 1
                ):
                    raise InjectedFault("first-directory-durable")

            with patch.object(
                transaction,
                "_write_journal",
                side_effect=interrupt_after_first_identity,
            ):
                with self.assertRaisesRegex(InjectedFault, "^first-directory-durable$"):
                    apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                    )

            journal = _journal(target)
            created = journal["created_directories"]
            self.assertEqual(
                sum(record["identity"] is not None for record in created),
                1,
            )
            receipt = rollback(target, install_digest=journal["install_digest"])
            self.assertEqual(receipt["result"], "PASS")
            for record in created:
                self.assertFalse((target / str(record["path"])).exists())

    def test_every_durable_boundary_is_recoverable_and_never_partially_active(self) -> None:
        for stop in BOUNDARY_STATES:
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                plan = preview(source, target)

                def checkpoint(boundary: str) -> None:
                    if boundary == stop:
                        raise InjectedFault(stop)

                with self.assertRaisesRegex(InjectedFault, f"^{stop}$"):
                    apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                        fault=checkpoint,
                    )

                journal = _journal(target)
                self.assertEqual(journal["state"], BOUNDARY_STATES[stop])
                self.assertEqual(validate_journal(journal), ())
                activation = target / ".codex" / "control-plane.lock"
                if stop in {"prepared", "staged", "published_inactive", "hooks_configured"}:
                    self.assertFalse(activation.exists())
                else:
                    self.assertTrue(activation.exists())
                    for record in plan["managed_records"]:
                        _assert_record(self, target, record)
                    self.assertEqual(_hooks_path(target), b".codex/git-hooks\n")

    def test_exact_replay_returns_identical_receipt_and_wrong_replay_mutates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            before_replay = metadata_snapshot(target)

            replay = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )

            self.assertEqual(replay, receipt)
            self.assertEqual(before_replay, metadata_snapshot(target))

            wrong = copy.deepcopy(plan)
            wrong["source"]["head"] = "f" * 40
            unsigned = {key: value for key, value in wrong.items() if key != "plan_digest"}
            wrong["plan_digest"] = contract_digest(unsigned)
            before_wrong = metadata_snapshot(target)
            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_REPLAY"):
                apply_plan(
                    source,
                    target,
                    wrong,
                    expected_plan_digest=wrong["plan_digest"],
                )
            self.assertEqual(before_wrong, metadata_snapshot(target))

    def test_plan_digest_or_target_drift_fails_before_adoption_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            before = metadata_snapshot(target)

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_PLAN"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest="sha256:" + "0" * 64,
                )
            self.assertEqual(before, metadata_snapshot(target))
            self.assertFalse((target / ".git" / "codex-control-plane-core").exists())

            (target / "AGENTS.md").write_text("# drift\n", encoding="utf-8")
            drifted_before = metadata_snapshot(target)
            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_DIRTY"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )
            self.assertEqual(drifted_before, metadata_snapshot(target))
            self.assertFalse((target / ".git" / "codex-control-plane-core").exists())

    def test_source_head_drift_after_locked_preview_fails_before_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            original_preview = transaction.preview
            calls = 0

            def drift_after_locked_preview(*args: object, **kwargs: object) -> dict[str, object]:
                nonlocal calls
                observed = original_preview(*args, **kwargs)  # type: ignore[arg-type]
                calls += 1
                if calls == 2:
                    git(source, "commit", "--allow-empty", "-m", "source HEAD drift")
                return observed

            with patch.object(transaction, "preview", side_effect=drift_after_locked_preview):
                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_SOURCE_DRIFT"):
                    apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                    )

            self.assertEqual(calls, 2)
            self.assertFalse((target / ".git/codex-control-plane-core").exists())

    def _assert_inactive_entrypoints_fail_closed(self, target: Path) -> None:
        environment = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
        launcher = subprocess.run(
            ["/bin/sh", str(target / "scripts" / "control-plane"), "doctor", "--json"],
            cwd=target,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertNotEqual(launcher.returncode, 0)
        hook = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=/dev/null",
                str(target / ".codex" / "hooks" / "control_plane_hook.py"),
            ],
            cwd=target,
            env=environment,
            input=b"{}",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertNotEqual(hook.returncode, 0)


if __name__ == "__main__":
    unittest.main()
