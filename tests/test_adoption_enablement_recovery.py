from __future__ import annotations

import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import adoption_enablement.transaction as transaction
from adoption_enablement.manifest import preview
from adoption_enablement.transaction import (
    apply_plan,
    rollback,
    status,
    verify,
)
from control_plane.verification import VerificationMutex
from tests.adoption_enablement_test_support import (
    git,
    initialize_fresh_target,
    initialize_full_source,
    write_file,
)


ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = (
    "prepared",
    "staged",
    "published_inactive",
    "hooks_configured",
    "activation_published",
    "active",
)
STATUS_KEYS = {
    "schema_version",
    "kind",
    "state",
    "product_version",
    "tool_version",
    "install_digest",
    "verification",
    "result",
    "error_codes",
    "authorizes",
}


class InjectedFault(RuntimeError):
    pass


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    records: list[tuple[str, str, int, str]] = []
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        if current_path == root:
            directories[:] = [name for name in directories if name != ".git"]
        directories.sort()
        files.sort()
        for name in directories:
            path = current_path / name
            metadata = path.lstat()
            records.append(
                (
                    path.relative_to(root).as_posix(),
                    "directory",
                    stat.S_IMODE(metadata.st_mode),
                    "",
                )
            )
        for name in files:
            path = current_path / name
            metadata = path.lstat()
            records.append(
                (
                    path.relative_to(root).as_posix(),
                    "file",
                    stat.S_IMODE(metadata.st_mode),
                    "sha256:" + sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(records)


def _full_state_snapshot(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    records: list[tuple[str, str, int, str]] = []
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in directories:
            path = current_path / name
            metadata = path.lstat()
            records.append((path.relative_to(root).as_posix(), "directory", stat.S_IMODE(metadata.st_mode), ""))
        for name in files:
            path = current_path / name
            metadata = path.lstat()
            records.append(
                (
                    path.relative_to(root).as_posix(),
                    "file",
                    stat.S_IMODE(metadata.st_mode),
                    "sha256:" + sha256(path.read_bytes()).hexdigest(),
                )
            )
    return tuple(records)


def _apply(source: Path, target: Path) -> tuple[dict[str, object], dict[str, object]]:
    plan = preview(source, target)
    receipt = apply_plan(
        source,
        target,
        plan,
        expected_plan_digest=plan["plan_digest"],
    )
    return plan, receipt


class AdoptionRecoveryTests(unittest.TestCase):
    def test_each_partial_journalless_provisioning_prefix_is_recoverable(self) -> None:
        prefixes = (
            "root-only",
            "lifecycle-only",
            "adoption-directory",
            "adoption-quarantine",
            "empty-locks-directory",
            "locks-quarantine",
            "verification-lock",
            "journal-temporary",
        )
        for prefix in prefixes:
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                plan = preview(source, target)
                state = target / ".git" / transaction.STATE_ROOT
                state.mkdir(mode=0o700)
                if prefix != "root-only":
                    lifecycle = state / transaction.LOCK_NAME
                    lifecycle.write_bytes(b"")
                    lifecycle.chmod(0o600)
                if prefix in {
                    "adoption-directory",
                    "empty-locks-directory",
                    "locks-quarantine",
                    "verification-lock",
                    "journal-temporary",
                }:
                    (state / transaction.ADOPTION_DIRECTORY).mkdir(mode=0o700)
                if prefix in {
                    "empty-locks-directory",
                    "verification-lock",
                    "journal-temporary",
                }:
                    locks = state / "locks"
                    locks.mkdir(mode=0o700)
                if prefix == "adoption-quarantine":
                    (state / transaction._PROVISIONING_ADOPTION_QUARANTINE).mkdir(
                        mode=0o700
                    )
                if prefix == "locks-quarantine":
                    (state / transaction._PROVISIONING_LOCKS_QUARANTINE).mkdir(
                        mode=0o700
                    )
                if prefix in {"verification-lock", "journal-temporary"}:
                    verification = state / "locks" / "verification.lock"
                    verification.write_bytes(b"")
                    verification.chmod(0o600)
                if prefix == "journal-temporary":
                    temporary = (
                        state
                        / transaction.ADOPTION_DIRECTORY
                        / (".journal.json." + "a" * 32 + ".tmp")
                    )
                    temporary.write_bytes(b"partial")
                    temporary.chmod(0o600)

                receipt = apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

                self.assertEqual(receipt["result"], "PASS")
                self.assertEqual(verify(target)["result"], "PASS")

    def test_each_provisioning_cleanup_boundary_remains_retryable(self) -> None:
        boundaries = (
            "provisioning_temp_removed",
            "provisioning_verification_removed",
            "provisioning_locks_quarantined",
            "provisioning_locks_removed",
            "provisioning_adoption_quarantined",
            "provisioning_adoption_removed",
        )
        for boundary in boundaries:
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                plan = preview(source, target)
                state = target / ".git" / transaction.STATE_ROOT
                adoption = state / transaction.ADOPTION_DIRECTORY
                locks = state / "locks"
                state.mkdir(mode=0o700)
                adoption.mkdir(mode=0o700)
                locks.mkdir(mode=0o700)
                for path in (
                    state / transaction.LOCK_NAME,
                    locks / "verification.lock",
                ):
                    path.write_bytes(b"")
                    path.chmod(0o600)
                temporary = adoption / (".journal.json." + "b" * 32 + ".tmp")
                temporary.write_bytes(b"partial")
                temporary.chmod(0o600)

                def interrupt(observed: str) -> None:
                    if observed == boundary:
                        raise InjectedFault(boundary)

                with self.assertRaisesRegex(InjectedFault, f"^{boundary}$"):
                    apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                        fault=interrupt,
                    )

                receipt = apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )
                self.assertEqual(receipt["result"], "PASS")

    def test_post_cleanup_validation_failure_leaves_a_retryable_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            state = target / ".git" / transaction.STATE_ROOT
            adoption = state / transaction.ADOPTION_DIRECTORY
            locks = state / "locks"
            state.mkdir(mode=0o700)
            adoption.mkdir(mode=0o700)
            locks.mkdir(mode=0o700)
            for path in (
                state / transaction.LOCK_NAME,
                locks / "verification.lock",
            ):
                path.write_bytes(b"")
                path.chmod(0o600)
            original = transaction._validate_plan_binding
            calls = 0

            def fail_second(*args: object, **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ValueError("E_ADOPTION_TARGET_DRIFT: injected")
                return original(*args, **kwargs)

            with patch.object(
                transaction,
                "_validate_plan_binding",
                side_effect=fail_second,
            ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_TARGET_DRIFT"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            self.assertEqual(
                sorted(path.name for path in state.iterdir()),
                [transaction.LOCK_NAME],
            )
            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            self.assertEqual(receipt["result"], "PASS")

    def test_core_only_verification_prefixes_are_preserved(self) -> None:
        for case in ("empty-locks", "verification-lock"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                plan = preview(source, target)
                state = target / ".git" / transaction.STATE_ROOT
                locks = state / "locks"
                locks.mkdir(parents=True, mode=0o700)
                if case == "verification-lock":
                    path = locks / "verification.lock"
                    path.write_bytes(b"")
                    path.chmod(0o600)
                before = _full_state_snapshot(state)

                with self.assertRaisesRegex(ValueError, "^E_ADOPTION_LOCK"):
                    apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                    )

                self.assertEqual(_full_state_snapshot(state), before)

    def test_root_empty_core_prefix_race_removes_only_the_created_lifecycle_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            state = target / ".git" / transaction.STATE_ROOT
            state.mkdir(mode=0o700)
            original = transaction._provisioning_state
            observations = 0

            def create_core_prefix(common: Path) -> str:
                nonlocal observations
                observations += 1
                if observations == 2:
                    (state / "locks").mkdir(mode=0o700)
                return original(common)

            with patch.object(
                transaction,
                "_provisioning_state",
                side_effect=create_core_prefix,
            ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_RECOVERY_REQUIRED"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            self.assertGreaterEqual(observations, 2)
            self.assertTrue((state / "locks").is_dir())
            self.assertFalse((state / transaction.LOCK_NAME).exists())

    def test_p2_p3_cleanup_never_removes_a_substituted_directory(self) -> None:
        for prefix, relative in (("P2", "adoption"), ("P3", "locks")):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                plan = preview(source, target)
                state = target / ".git" / transaction.STATE_ROOT
                state.mkdir(mode=0o700)
                lifecycle = state / transaction.LOCK_NAME
                lifecycle.write_bytes(b"")
                lifecycle.chmod(0o600)
                (state / transaction.ADOPTION_DIRECTORY).mkdir(mode=0o700)
                if prefix == "P3":
                    (state / "locks").mkdir(mode=0o700)
                subject = state / relative
                subject_identity = (subject.lstat().st_dev, subject.lstat().st_ino)
                preserved = container / f"preserved-{relative}"
                replacement_identity: tuple[int, int] | None = None
                swapped = False
                real_scandir = transaction.os.scandir

                class SwapAfterScan:
                    def __init__(self, iterator: object) -> None:
                        self.iterator = iterator

                    def __enter__(self) -> object:
                        return self.iterator.__enter__()  # type: ignore[attr-defined]

                    def __exit__(self, *arguments: object) -> object:
                        nonlocal replacement_identity, swapped
                        result = self.iterator.__exit__(*arguments)  # type: ignore[attr-defined]
                        subject.rename(preserved)
                        subject.mkdir(mode=0o700)
                        metadata = subject.lstat()
                        replacement_identity = (metadata.st_dev, metadata.st_ino)
                        swapped = True
                        return result

                def swap_after_subject_scan(path: object) -> object:
                    iterator = real_scandir(path)
                    if (
                        not swapped
                        and isinstance(path, int)
                        and (os.fstat(path).st_dev, os.fstat(path).st_ino)
                        == subject_identity
                    ):
                        return SwapAfterScan(iterator)
                    return iterator

                with patch.object(
                    transaction.os,
                    "scandir",
                    side_effect=swap_after_subject_scan,
                ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_RECOVERY_REQUIRED"):
                    apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                    )

                self.assertTrue(swapped)
                self.assertTrue(subject.is_dir())
                self.assertEqual(
                    (subject.lstat().st_dev, subject.lstat().st_ino),
                    replacement_identity,
                )
                self.assertTrue(preserved.is_dir())

    def test_p4t_cleanup_opens_and_revalidates_the_observed_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(strict=True)
            common = root / "common"
            state = common / transaction.STATE_ROOT
            adoption = state / transaction.ADOPTION_DIRECTORY
            locks = state / "locks"
            common.mkdir(mode=0o700)
            state.mkdir(mode=0o700)
            adoption.mkdir(mode=0o700)
            locks.mkdir(mode=0o700)
            for path in (
                state / transaction.LOCK_NAME,
                locks / "verification.lock",
            ):
                path.write_bytes(b"")
                path.chmod(0o600)
            temporary = adoption / (".journal.json." + "c" * 32 + ".tmp")
            temporary.write_bytes(b"partial")
            temporary.chmod(0o600)
            original_state = transaction._provisioning_state
            real_open = transaction.os.open
            swapped = False
            observed_flags: list[int] = []

            def replace_after_classification(path: Path) -> str:
                nonlocal swapped
                result = original_state(path)
                if result == "P4T" and not swapped:
                    temporary.unlink()
                    os.mkfifo(temporary, 0o600)
                    swapped = True
                return result

            def reject_blocking_open(
                path: object,
                flags: int,
                *arguments: object,
                **keywords: object,
            ) -> int:
                if path == temporary.name and keywords.get("dir_fd") is not None:
                    observed_flags.append(flags)
                    if not flags & getattr(os, "O_NONBLOCK", 0):
                        raise RuntimeError("blocking FIFO open attempted")
                return real_open(path, flags, *arguments, **keywords)

            with transaction._adoption_lock(common, create=False) as lifecycle:
                lifecycle.preserve_state()
                with patch.object(
                    transaction,
                    "_provisioning_state",
                    side_effect=replace_after_classification,
                ), patch.object(
                    transaction.os,
                    "open",
                    side_effect=reject_blocking_open,
                ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_RECOVERY_REQUIRED"):
                    transaction._reset_exact_provisioning_state(lifecycle)

            self.assertTrue(swapped)
            self.assertTrue(observed_flags)
            self.assertTrue(observed_flags[-1] & getattr(os, "O_NONBLOCK", 0))
            self.assertTrue(stat.S_ISFIFO(temporary.lstat().st_mode))

    def test_forged_closed_task_blocks_rollback_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            write_file(
                target,
                ".git/codex-control-plane-core/tasks/FORGED-CLOSED.json",
                '{"state":"closed"}\n',
                mode=0o600,
            )
            before = _full_state_snapshot(target)

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_TASK_ACTIVE"):
                rollback(target, install_digest=receipt["install_digest"])

            self.assertEqual(_full_state_snapshot(target), before)

    def test_rollback_preserves_a_record_substituted_after_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            managed = target / "scripts" / "control-plane"
            preserved_original = container / "managed-original"
            sentinel = b"consumer sentinel\n"
            original_verify = transaction._verify_installed_record
            observations = 0

            def substitute_after_second_verification(
                observed_target: Path,
                record: dict[str, object],
            ) -> None:
                nonlocal observations
                original_verify(observed_target, record)
                if record.get("path") != "scripts/control-plane":
                    return
                observations += 1
                if observations == 2:
                    managed.replace(preserved_original)
                    managed.write_bytes(sentinel)
                    managed.chmod(0o755)

            with patch.object(
                transaction,
                "_verify_installed_record",
                side_effect=substitute_after_second_verification,
            ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_ROLLBACK_DRIFT"):
                rollback(target, install_digest=receipt["install_digest"])

            self.assertEqual(observations, 2)
            self.assertEqual(managed.read_bytes(), sentinel)

    def test_rollback_conditionally_removes_only_its_exact_hooks_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            original_run_git = transaction._run_git
            replaced = False

            def replace_before_unset(
                repository: Path,
                *arguments: str,
                **keywords: object,
            ) -> bytes:
                nonlocal replaced
                if (
                    not replaced
                    and arguments[:4]
                    == ("config", "--local", "--unset-all", "core.hooksPath")
                ):
                    git(
                        repository,
                        "config",
                        "--local",
                        "--replace-all",
                        "core.hooksPath",
                        "consumer/hooks",
                    )
                    replaced = True
                return original_run_git(repository, *arguments, **keywords)

            with patch.object(
                transaction,
                "_run_git",
                side_effect=replace_before_unset,
            ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_ROLLBACK_DRIFT"):
                rollback(target, install_digest=receipt["install_digest"])

            self.assertTrue(replaced)
            self.assertEqual(
                git(
                    target,
                    "config",
                    "--local",
                    "--get-all",
                    "core.hooksPath",
                    check=False,
                ).stdout,
                b"consumer/hooks\n",
            )

    def test_rollback_retains_open_managed_and_activation_inodes_in_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            managed_descriptor = os.open(target / "scripts" / "control-plane", os.O_RDWR)
            activation_descriptor = os.open(
                target / ".codex" / "control-plane.lock",
                os.O_RDWR,
            )
            try:
                rollback_receipt = rollback(
                    target,
                    install_digest=receipt["install_digest"],
                )
                self.assertEqual(rollback_receipt["result"], "PASS")
                self.assertEqual(os.fstat(managed_descriptor).st_nlink, 1)
                self.assertEqual(os.fstat(activation_descriptor).st_nlink, 1)
            finally:
                os.close(managed_descriptor)
                os.close(activation_descriptor)

    def test_rollback_rechecks_managed_quarantine_after_an_open_descriptor_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            descriptor = os.open(target / "scripts" / "control-plane", os.O_RDWR)
            original_verify = transaction._verify_staging
            observations = 0

            def mutate_after_retention_check(*arguments: object, **keywords: object) -> None:
                nonlocal observations
                observations += 1
                original_verify(*arguments, **keywords)
                if observations == 2:
                    os.pwrite(descriptor, b"X", 0)
                    os.fsync(descriptor)

            try:
                with patch.object(
                    transaction,
                    "_verify_staging",
                    side_effect=mutate_after_retention_check,
                ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_ROLLBACK_DRIFT"):
                    rollback(target, install_digest=receipt["install_digest"])
                self.assertEqual(observations, 3)
                self.assertEqual(os.fstat(descriptor).st_nlink, 1)
            finally:
                os.close(descriptor)

    def test_rollback_rechecks_activation_quarantine_after_an_open_descriptor_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            descriptor = os.open(
                target / ".codex" / "control-plane.lock",
                os.O_RDWR,
            )
            original_verify = transaction._verify_recovery_lock
            observations = 0

            def mutate_after_deactivation_check(
                *arguments: object,
                **keywords: object,
            ) -> bool:
                nonlocal observations
                observations += 1
                result = original_verify(*arguments, **keywords)
                if observations == 2 and result:
                    os.pwrite(descriptor, b"X", 0)
                    os.fsync(descriptor)
                return result

            try:
                with patch.object(
                    transaction,
                    "_verify_recovery_lock",
                    side_effect=mutate_after_deactivation_check,
                ), self.assertRaisesRegex(ValueError, "^E_ADOPTION_ROLLBACK_DRIFT"):
                    rollback(target, install_digest=receipt["install_digest"])
                self.assertEqual(observations, 3)
                self.assertEqual(os.fstat(descriptor).st_nlink, 1)
            finally:
                os.close(descriptor)

    def test_verification_guard_keeps_one_persistent_mutex_domain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            common = container / "common"
            common.mkdir(mode=0o700)
            state = common / transaction.STATE_ROOT
            state.mkdir(mode=0o700)
            lock_path = state / "locks" / "verification.lock"
            real_flock = fcntl.flock
            stale_descriptor: int | None = None
            captured_old_inode = False

            def capture_old_inode_on_unlock(descriptor: int, operation: int) -> None:
                nonlocal captured_old_inode, stale_descriptor
                real_flock(descriptor, operation)
                if operation == fcntl.LOCK_UN and not captured_old_inode:
                    stale_descriptor = os.open(
                        lock_path,
                        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    )
                    real_flock(stale_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    captured_old_inode = True

            try:
                with patch.object(
                    transaction.fcntl,
                    "flock",
                    side_effect=capture_old_inode_on_unlock,
                ):
                    with transaction._verification_guard(
                        common,
                        create=True,
                    ) as binding:
                        pass

                self.assertTrue(lock_path.is_file())
                self.assertIsNotNone(stale_descriptor)
                with self.assertRaisesRegex(
                    ValueError,
                    "^E_ADOPTION_VERIFICATION_BUSY: verifier mutex is held$",
                ):
                    with transaction._verification_guard(
                        common,
                        create=False,
                        expected=binding,
                    ):
                        self.fail("a second verifier entered through a new mutex inode")
            finally:
                if stale_descriptor is not None:
                    real_flock(stale_descriptor, fcntl.LOCK_UN)
                    os.close(stale_descriptor)

    def test_verification_guard_revalidates_named_identity_after_flock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            common = container / "common"
            common.mkdir(mode=0o700)
            state = common / transaction.STATE_ROOT
            state.mkdir(mode=0o700)
            lock_path = state / "locks" / "verification.lock"
            displaced_path = state / "locks" / "verification.displaced"
            with transaction._verification_guard(common, create=True) as binding:
                pass
            real_flock = fcntl.flock
            replaced = False
            entered = False

            def replace_name_after_flock(descriptor: int, operation: int) -> None:
                nonlocal replaced
                real_flock(descriptor, operation)
                if operation == (fcntl.LOCK_EX | fcntl.LOCK_NB) and not replaced:
                    lock_path.rename(displaced_path)
                    replacement = os.open(
                        lock_path,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                    )
                    os.close(replacement)
                    replaced = True

            with patch.object(
                transaction.fcntl,
                "flock",
                side_effect=replace_name_after_flock,
            ), self.assertRaisesRegex(
                ValueError,
                "^E_ADOPTION_VERIFICATION: verifier lock identity changed$",
            ):
                with transaction._verification_guard(
                    common,
                    create=False,
                    expected=binding,
                ):
                    entered = True

            self.assertFalse(entered)

    def test_verification_guard_retains_the_locked_directory_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            common = container / "common"
            common.mkdir(mode=0o700)
            state = common / transaction.STATE_ROOT
            state.mkdir(mode=0o700)
            with transaction._verification_guard(common, create=True) as binding:
                pass
            locks = state / "locks"
            lock_path = locks / "verification.lock"
            displaced = state / "locks.displaced"
            real_flock = fcntl.flock
            replaced = False

            def replace_directory_after_flock(descriptor: int, operation: int) -> None:
                nonlocal replaced
                real_flock(descriptor, operation)
                if operation == (fcntl.LOCK_EX | fcntl.LOCK_NB) and not replaced:
                    locks.rename(displaced)
                    locks.mkdir(mode=0o700)
                    (displaced / "verification.lock").rename(lock_path)
                    replaced = True

            with patch.object(
                transaction.fcntl,
                "flock",
                side_effect=replace_directory_after_flock,
            ), self.assertRaisesRegex(
                ValueError,
                "^E_ADOPTION_VERIFICATION: verifier lock identity changed$",
            ):
                with transaction._verification_guard(
                    common,
                    create=False,
                    expected=binding,
                ):
                    self.fail("Adoption accepted a substituted lock directory")

    def test_verification_guard_revalidates_common_and_state_after_flock(self) -> None:
        for case in ("common", "state"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(strict=True)
                common = root / "common"
                common.mkdir(mode=0o700)
                state = common / transaction.STATE_ROOT
                state.mkdir(mode=0o700)
                with transaction._verification_guard(common, create=True) as binding:
                    pass
                real_flock = fcntl.flock
                replaced = False

                def replace_ancestor_after_flock(
                    descriptor: int,
                    operation: int,
                ) -> None:
                    nonlocal replaced
                    real_flock(descriptor, operation)
                    if operation != (fcntl.LOCK_EX | fcntl.LOCK_NB) or replaced:
                        return
                    if case == "common":
                        common.rename(root / "common.displaced")
                        common.mkdir(mode=0o700)
                    else:
                        state.rename(common / f"{transaction.STATE_ROOT}.displaced")
                        state.mkdir(mode=0o700)
                    replaced = True

                with patch.object(
                    transaction.fcntl,
                    "flock",
                    side_effect=replace_ancestor_after_flock,
                ), self.assertRaisesRegex(
                    ValueError,
                    "^E_ADOPTION_VERIFICATION: verifier lock identity changed$",
                ):
                    with transaction._verification_guard(
                        common,
                        create=False,
                        expected=binding,
                    ):
                        self.fail("Adoption accepted a substituted lock ancestor")

    def test_fresh_verification_provisioning_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            common = container / "common"
            common.mkdir(mode=0o700)
            state = common / transaction.STATE_ROOT
            state.mkdir(mode=0o700)
            real_mkdir = os.mkdir
            competitor: Path | None = None

            def create_competing_lock(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> None:
                nonlocal competitor
                real_mkdir(path, mode, dir_fd=dir_fd)
                if path == "locks" and dir_fd is not None:
                    locks_fd = os.open("locks", os.O_RDONLY, dir_fd=dir_fd)
                    try:
                        descriptor = os.open(
                            "verification.lock",
                            os.O_RDWR | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=locks_fd,
                        )
                        os.close(descriptor)
                    finally:
                        os.close(locks_fd)
                    competitor = state / "locks" / "verification.lock"

            entered = False
            with patch.object(
                transaction.os,
                "mkdir",
                side_effect=create_competing_lock,
            ), self.assertRaisesRegex(
                ValueError,
                "^E_ADOPTION_VERIFICATION: verifier lock is unavailable$",
            ):
                with transaction._verification_guard(common, create=True):
                    entered = True
            self.assertFalse(entered)
            self.assertIsNotNone(competitor)
            assert competitor is not None
            self.assertTrue(competitor.is_file())

    def test_unjournaled_mutex_provisioning_is_exactly_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            original_write = transaction._write_journal
            interrupted = False

            def interrupt_first_journal(
                adoption_directory: Path,
                journal: dict[str, object],
            ) -> None:
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    raise InjectedFault("verification-provisioned")
                original_write(adoption_directory, journal)

            with patch.object(
                transaction,
                "_write_journal",
                side_effect=interrupt_first_journal,
            ), self.assertRaisesRegex(InjectedFault, "^verification-provisioned$"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            state = target / ".git" / transaction.STATE_ROOT
            self.assertEqual(
                sorted(path.name for path in state.iterdir()),
                ["adoption", "adoption.lock", "locks"],
            )
            self.assertEqual(list((state / "adoption").iterdir()), [])
            self.assertEqual(
                [path.name for path in (state / "locks").iterdir()],
                ["verification.lock"],
            )
            self.assertTrue((state / "locks" / "verification.lock").is_file())
            self.assertFalse((state / "adoption" / "journal.json").exists())
            receipt = apply_plan(
                source,
                target,
                plan,
                expected_plan_digest=plan["plan_digest"],
            )
            self.assertEqual(receipt["result"], "PASS")
            self.assertEqual(verify(target)["result"], "PASS")

    def test_core_owned_verification_mutex_is_not_adoption_provisioning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)
            with VerificationMutex(target) as acquired:
                self.assertTrue(acquired)
            state = target / ".git" / transaction.STATE_ROOT
            lock_path = state / "locks" / "verification.lock"
            before = _full_state_snapshot(state)
            before_identity = transaction.metadata_identity(lock_path.lstat())

            with self.assertRaisesRegex(
                ValueError,
                "^E_ADOPTION_RECOVERY_REQUIRED",
            ):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            self.assertEqual(_full_state_snapshot(state), before)
            self.assertEqual(
                transaction.metadata_identity(lock_path.lstat()),
                before_identity,
            )

    def test_provisioning_recovery_validates_plan_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            plan = preview(source, target)

            with patch.object(
                transaction,
                "_write_journal",
                side_effect=InjectedFault("verification-provisioned"),
            ), self.assertRaisesRegex(InjectedFault, "^verification-provisioned$"):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            state = target / ".git" / transaction.STATE_ROOT
            before = _full_state_snapshot(state)
            lock_identity = transaction.metadata_identity(
                (state / "locks" / "verification.lock").lstat()
            )
            git(source, "commit", "--allow-empty", "-m", "source drift")

            with self.assertRaisesRegex(
                ValueError,
                "^E_ADOPTION_(SOURCE|TARGET)_DRIFT",
            ):
                apply_plan(
                    source,
                    target,
                    plan,
                    expected_plan_digest=plan["plan_digest"],
                )

            self.assertEqual(_full_state_snapshot(state), before)
            self.assertEqual(
                transaction.metadata_identity(
                    (state / "locks" / "verification.lock").lstat()
                ),
                lock_identity,
            )

    def test_rollback_rejects_a_missing_or_replaced_bound_verification_mutex(self) -> None:
        for case in ("missing_file", "replaced_file", "replaced_directory"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                plan, receipt = _apply(source, target)
                state = target / ".git" / transaction.STATE_ROOT
                locks = state / "locks"
                lock_path = locks / "verification.lock"
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                )
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if case == "missing_file":
                    lock_path.unlink()
                elif case == "replaced_file":
                    lock_path.rename(locks / "verification.displaced")
                    replacement = os.open(
                        lock_path,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    os.close(replacement)
                else:
                    locks.rename(state / "locks.displaced")
                    locks.mkdir(mode=0o700)
                    replacement = os.open(
                        lock_path,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                    os.close(replacement)
                before = _full_state_snapshot(target)
                try:
                    with self.assertRaisesRegex(
                        ValueError,
                        "^E_ADOPTION_VERIFICATION:",
                    ):
                        apply_plan(
                            source,
                            target,
                            plan,
                            expected_plan_digest=plan["plan_digest"],
                        )
                    self.assertEqual(before, _full_state_snapshot(target))
                    self.assertEqual(verify(target)["result"], "FAIL")
                    self.assertEqual(before, _full_state_snapshot(target))
                    with self.assertRaisesRegex(
                        ValueError,
                        "^E_ADOPTION_VERIFICATION:",
                    ):
                        rollback(
                            target,
                            install_digest=str(receipt["install_digest"]),
                        )
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                    os.close(descriptor)
                self.assertEqual(before, _full_state_snapshot(target))
                self.assertTrue((target / ".codex" / "control-plane.lock").is_file())

    def test_status_and_verify_do_not_create_a_missing_adoption_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = initialize_fresh_target(Path(directory) / "target")
            state = target / ".git" / "codex-control-plane-core"
            state.mkdir(mode=0o700)
            before = _full_state_snapshot(target)

            observed = status(target)
            verification = verify(target)

            self.assertEqual(observed["result"], "UNKNOWN")
            self.assertEqual(verification["result"], "FAIL")
            self.assertEqual(before, _full_state_snapshot(target))
            self.assertFalse((state / "adoption.lock").exists())

    def test_status_and_verify_are_closed_read_only_projections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            before = _full_state_snapshot(target)

            observed = status(target)
            verification = verify(target)

            self.assertEqual(set(observed), STATUS_KEYS)
            self.assertEqual(set(verification), STATUS_KEYS)
            self.assertEqual(observed["state"], "ACTIVE")
            self.assertEqual(observed["install_digest"], receipt["install_digest"])
            self.assertEqual(verification["verification"], "PASS")
            self.assertEqual(verification["result"], "PASS")
            self.assertIs(observed["authorizes"], False)
            self.assertEqual(before, _full_state_snapshot(target))
            serialized = json.dumps({"status": observed, "verify": verification})
            self.assertNotIn(str(source), serialized)
            self.assertNotIn(str(target), serialized)
            self.assertNotIn("journal.json", serialized)

    def test_invalid_journal_status_is_unknown_and_never_echoes_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _apply(source, target)
            journal = target / ".git" / "codex-control-plane-core" / "adoption" / "journal.json"
            journal.write_bytes(b'{"marker":"DO-NOT-ECHO-ME", broken')
            before = _full_state_snapshot(target)

            observed = status(target)

            self.assertEqual(observed["state"], "UNKNOWN")
            self.assertEqual(observed["result"], "UNKNOWN")
            self.assertIn("E_ADOPTION_JOURNAL", observed["error_codes"])
            self.assertNotIn("DO-NOT-ECHO-ME", json.dumps(observed))
            self.assertEqual(before, _full_state_snapshot(target))

    def test_verify_rejects_byte_mode_and_inventory_drift_without_repair(self) -> None:
        cases = ("bytes", "mode", "extra")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                _apply(source, target)
                if case == "bytes":
                    path = target / "control_plane" / "contracts.py"
                    path.write_bytes(path.read_bytes() + b"\n# drift\n")
                elif case == "mode":
                    (target / "scripts" / "control-plane").chmod(0o644)
                else:
                    write_file(target, "control_plane/extra.py", "raise SystemExit(7)\n")
                before = _full_state_snapshot(target)

                observed = verify(target)

                self.assertEqual(observed["result"], "FAIL")
                self.assertIn("E_ADOPTION_VERIFY_DRIFT", observed["error_codes"])
                self.assertEqual(before, _full_state_snapshot(target))

    def test_rollback_rejects_active_task_lease_and_verifier_without_mutation(self) -> None:
        for case in ("task", "lease", "verifier"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                _, receipt = _apply(source, target)
                state = target / ".git" / "codex-control-plane-core"
                descriptor: int | None = None
                if case == "task":
                    write_file(
                        target,
                        ".git/codex-control-plane-core/tasks/CORE-ACTIVE.json",
                        '{"state":"implementing"}\n',
                        mode=0o600,
                    )
                    expected = "E_ADOPTION_TASK_ACTIVE"
                elif case == "lease":
                    write_file(
                        target,
                        ".git/codex-control-plane-core/leases/lease-active.json",
                        '{"kind":"CoreLeaseV1"}\n',
                        mode=0o600,
                    )
                    expected = "E_ADOPTION_LEASE_ACTIVE"
                else:
                    locks = state / "locks"
                    locks.mkdir(mode=0o700, exist_ok=True)
                    path = write_file(
                        target,
                        ".git/codex-control-plane-core/locks/verification.lock",
                        b"",
                        mode=0o600,
                    )
                    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    expected = "E_ADOPTION_VERIFICATION_BUSY"
                before = _full_state_snapshot(target)
                try:
                    with self.assertRaisesRegex(ValueError, f"^{expected}"):
                        rollback(target, install_digest=receipt["install_digest"])
                finally:
                    if descriptor is not None:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                        os.close(descriptor)
                self.assertEqual(before, _full_state_snapshot(target))

    def test_rollback_rejects_installed_drift_before_deactivation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)
            path = target / "control_plane" / "contracts.py"
            path.write_bytes(path.read_bytes() + b"\n# preserve me\n")
            before = _full_state_snapshot(target)

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_ROLLBACK_DRIFT"):
                rollback(target, install_digest=receipt["install_digest"])

            self.assertTrue((target / ".codex" / "control-plane.lock").exists())
            self.assertEqual(
                git(target, "config", "--local", "--get-all", "core.hooksPath").stdout,
                b".codex/git-hooks\n",
            )
            self.assertEqual(before, _full_state_snapshot(target))

    def test_rollback_reobserves_the_restored_surface_before_emitting_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, receipt = _apply(source, target)

            def introduce_drift(boundary: str) -> None:
                if boundary == "records_removed":
                    write_file(target, ".codex/hooks.json", b"competitor\n")

            with self.assertRaisesRegex(ValueError, "^E_ADOPTION_ROLLBACK_DRIFT"):
                rollback(
                    target,
                    install_digest=receipt["install_digest"],
                    fault=introduce_drift,
                )

            self.assertEqual((target / ".codex" / "hooks.json").read_bytes(), b"competitor\n")

    def test_rolled_back_status_does_not_claim_current_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory).resolve(strict=True)
            source = initialize_full_source(container / "source", ROOT)
            target = initialize_fresh_target(container / "target")
            _, apply_receipt = _apply(source, target)

            rollback_receipt = rollback(
                target,
                install_digest=apply_receipt["install_digest"],
            )
            observed = status(target)

            self.assertEqual(
                rollback_receipt["after_snapshot_digest"],
                rollback_receipt["before_snapshot_digest"],
            )
            self.assertEqual(observed["state"], "ROLLED_BACK")
            self.assertEqual(observed["verification"], "UNKNOWN")
            self.assertEqual(observed["result"], "UNKNOWN")

    def test_rollback_rejects_every_bound_drift_class_before_deactivation(self) -> None:
        for case in ("mode", "missing", "extra", "unsafe", "identity", "journal"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                _, receipt = _apply(source, target)
                external = container / "external.txt"
                external.write_text("preserve\n", encoding="utf-8")
                if case == "mode":
                    (target / "scripts" / "control-plane").chmod(0o644)
                    expected = "E_ADOPTION_ROLLBACK_DRIFT"
                elif case == "missing":
                    (target / "control_plane" / "contracts.py").unlink()
                    expected = "E_ADOPTION_ROLLBACK_DRIFT"
                elif case == "extra":
                    write_file(target, "control_plane/extra.py", "raise SystemExit(7)\n")
                    expected = "E_ADOPTION_ROLLBACK_DRIFT"
                elif case == "unsafe":
                    path = target / "control_plane" / "contracts.py"
                    path.unlink()
                    path.symlink_to(external)
                    expected = "E_ADOPTION_ROLLBACK_DRIFT"
                elif case == "identity":
                    git(target, "branch", "-m", "codex/renamed-adoption-target")
                    expected = "E_ADOPTION_TARGET_DRIFT"
                else:
                    journal = (
                        target
                        / ".git"
                        / "codex-control-plane-core"
                        / "adoption"
                        / "journal.json"
                    )
                    value = json.loads(journal.read_text(encoding="utf-8"))
                    value["unexpected"] = "marker"
                    journal.write_text(json.dumps(value), encoding="utf-8")
                    expected = "E_ADOPTION_JOURNAL"
                before = _full_state_snapshot(target)
                external_before = external.read_bytes()

                with self.assertRaisesRegex(ValueError, f"^{expected}"):
                    rollback(target, install_digest=receipt["install_digest"])

                self.assertEqual(before, _full_state_snapshot(target))
                self.assertEqual(external.read_bytes(), external_before)
                self.assertTrue((target / ".codex" / "control-plane.lock").exists())

    def test_interrupted_rollback_replays_exact_compensations(self) -> None:
        for boundary in (
            "rolling_back",
            "deactivated",
            "config_restored",
            "records_removed",
            "rolled_back",
        ):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                before = _tree_snapshot(target)
                _, receipt = _apply(source, target)

                def checkpoint(observed: str) -> None:
                    if observed == boundary:
                        raise InjectedFault(boundary)

                with self.assertRaisesRegex(InjectedFault, f"^{boundary}$"):
                    rollback(
                        target,
                        install_digest=receipt["install_digest"],
                        fault=checkpoint,
                    )

                replay = rollback(target, install_digest=receipt["install_digest"])

                self.assertEqual(replay["operation"], "rollback")
                self.assertEqual(before, _tree_snapshot(target))

    def test_rollback_replays_empty_recovery_directory_crash_windows(self) -> None:
        for window in ("before-lock-move",):
            with self.subTest(window=window), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                before = _tree_snapshot(target)
                _, receipt = _apply(source, target)

                if window == "before-lock-move":
                    def interrupt_move(
                        target_root: Path,
                        adoption_directory: Path,
                        install_digest: str,
                        *,
                        lock_record: object,
                        target_binding: object,
                        managed_parent_directories: object,
                        created_directories: object,
                    ) -> Path:
                        del (
                            target_root,
                            lock_record,
                            target_binding,
                            managed_parent_directories,
                            created_directories,
                        )
                        recovery = transaction._recovery_path(
                            adoption_directory,
                            install_digest,
                        )
                        recovery.mkdir(mode=0o700)
                        raise InjectedFault(window)

                    patched = patch.object(
                        transaction,
                        "_move_activation_to_recovery",
                        side_effect=interrupt_move,
                    )
                with patched, self.assertRaisesRegex(InjectedFault, f"^{window}$"):
                    rollback(target, install_digest=receipt["install_digest"])

                recovery = (
                    target
                    / ".git"
                    / "codex-control-plane-core"
                    / "adoption"
                    / (".recovery-" + str(receipt["install_digest"]).removeprefix("sha256:"))
                )
                self.assertTrue(recovery.is_dir())
                self.assertEqual(tuple(recovery.iterdir()), ())

                replay = rollback(target, install_digest=receipt["install_digest"])

                self.assertEqual(replay["operation"], "rollback")
                self.assertEqual(replay["result"], "PASS")
                self.assertEqual(before, _tree_snapshot(target))

    def test_rollback_and_recovery_restore_exact_consumer_tree(self) -> None:
        for boundary in (*BOUNDARIES, "completed"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                container = Path(directory).resolve(strict=True)
                source = initialize_full_source(container / "source", ROOT)
                target = initialize_fresh_target(container / "target")
                before = _tree_snapshot(target)
                plan = preview(source, target)
                receipt: dict[str, object] | None = None
                if boundary == "completed":
                    receipt = apply_plan(
                        source,
                        target,
                        plan,
                        expected_plan_digest=plan["plan_digest"],
                    )
                else:
                    def checkpoint(observed: str) -> None:
                        if observed == boundary:
                            raise InjectedFault(boundary)

                    with self.assertRaises(InjectedFault):
                        apply_plan(
                            source,
                            target,
                            plan,
                            expected_plan_digest=plan["plan_digest"],
                            fault=checkpoint,
                        )
                    journal = json.loads(
                        (
                            target
                            / ".git"
                            / "codex-control-plane-core"
                            / "adoption"
                            / "journal.json"
                        ).read_text(encoding="utf-8")
                    )
                    install_digest = journal["install_digest"]
                if receipt is not None:
                    install_digest = receipt["install_digest"]

                rollback_receipt = rollback(target, install_digest=install_digest)

                self.assertEqual(rollback_receipt["operation"], "rollback")
                self.assertEqual(rollback_receipt["result"], "PASS")
                self.assertEqual(before, _tree_snapshot(target))
                self.assertEqual(
                    git(
                        target,
                        "config",
                        "--local",
                        "--get-all",
                        "core.hooksPath",
                        check=False,
                    ).stdout,
                    b"",
                )
                self.assertFalse((target / ".codex" / "control-plane.lock").exists())
                state = target / ".git" / "codex-control-plane-core"
                evidence = tuple(
                    path.relative_to(state).as_posix()
                    for path in state.rglob("*")
                    if path.is_file()
                )
                base_evidence = {
                    "adoption.lock",
                    "locks/verification.lock",
                    f"adoption/evidence/{install_digest.removeprefix('sha256:')}.json",
                }
                self.assertTrue(base_evidence.issubset(set(evidence)))
                quarantine = set(evidence) - base_evidence
                expected_quarantine = 0 if boundary == "prepared" else len(
                    plan["managed_records"]
                )
                self.assertEqual(len(quarantine), expected_quarantine)
                self.assertTrue(
                    all(
                        path.startswith("adoption/.staging-")
                        or path.startswith("adoption/.recovery-")
                        for path in quarantine
                    )
                )
                replay_before = _full_state_snapshot(target)
                replay = rollback(target, install_digest=install_digest)
                self.assertEqual(replay, rollback_receipt)
                self.assertEqual(replay_before, _full_state_snapshot(target))


if __name__ == "__main__":
    unittest.main()
