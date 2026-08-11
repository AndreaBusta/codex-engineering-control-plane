from __future__ import annotations

import json
import multiprocessing
import os
import stat
import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from control_plane.contracts import canonical_json, contract_digest


def _fifo_candidate_worker(
    git_dir: str,
    operation: str,
    receipt: dict[str, object],
    connection: object,
) -> None:
    from control_plane.candidate_receipt import LocalCandidateReceiptStore

    try:
        store = LocalCandidateReceiptStore(Path(git_dir))
        if operation == "load":
            store.load()
        else:
            store.store(receipt)
    except Exception as error:  # pragma: no branch - child reports exact outcome
        connection.send((type(error).__name__, str(error)))
    else:
        connection.send(("NO_ERROR", ""))
    finally:
        connection.close()


def digest(label: str) -> str:
    return contract_digest({"label": label})


def candidate_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "candidate_id": "v2-3-local-candidate",
        "repository": "/tmp/control-plane-candidate",
        "branch": "codex/control-plane-v2-3",
        "head_sha": "7bd9d2a96f5c3cdd22807a5d7f810d3a6fc1d9d4",
        "product_version": "2.1.1",
        "runtime_digest": digest("runtime"),
        "worktree_subject": {
            "algorithm": "ControlPlaneReviewSubjectV1",
            "digest": digest("subject"),
        },
        "security_snapshot": {
            "algorithm": "codex-security-snapshot/v1",
            "digest": digest("security"),
        },
        "index_digest": digest("index"),
        "index_empty": True,
        "tracked_modified_count": 18,
        "untracked_count": 14,
        "suite": {
            "command": ["bash", "tests/run.sh"],
            "count": 705,
            "status": "PASS",
        },
        "gates": [
            {
                "name": "policy-check",
                "command": [
                    "scripts/control-plane",
                    "policy-check",
                    "--policy",
                    ".codex/project-policy.toml",
                ],
                "status": "PASS",
                "result_digest": digest("policy"),
            },
            {
                "name": "preflight-write",
                "command": [
                    "scripts/control-plane",
                    "preflight",
                    "--mode",
                    "write",
                ],
                "status": "FAIL",
                "result_digest": digest("dirty-wip"),
            },
        ],
        "independent_review": {
            "result_digest": digest("independent-review"),
            "status": "PASS",
        },
        "security_review": {
            "result_digest": digest("security-review"),
            "status": "PASS",
        },
        "sandbox_status": "PENDING_SANDBOX_TARGET",
        "observed_at": "2026-08-10T12:00:00Z",
    }
    values.update(overrides)
    return values


class LocalCandidateReceiptContractTests(unittest.TestCase):
    def build(self, **overrides: object) -> dict[str, object]:
        from control_plane.candidate_receipt import build_local_candidate_receipt

        return build_local_candidate_receipt(**candidate_values(**overrides))

    def test_builder_emits_closed_bounded_non_authorizing_contract(self) -> None:
        from control_plane.candidate_receipt import (
            MAX_CANDIDATE_RECEIPT_BYTES,
            validate_local_candidate_receipt,
        )

        receipt = self.build()

        self.assertEqual(validate_local_candidate_receipt(receipt), [])
        self.assertEqual(
            set(receipt),
            {
                "schema_version",
                "kind",
                "candidate_id",
                "repository",
                "branch",
                "head_sha",
                "product_version",
                "runtime_digest",
                "worktree_subject",
                "security_snapshot",
                "index_digest",
                "index_empty",
                "tracked_modified_count",
                "untracked_count",
                "suite",
                "gates",
                "independent_review",
                "security_review",
                "sandbox_status",
                "observed_at",
                "authorizes",
                "receipt_digest",
            },
        )
        self.assertEqual(receipt["kind"], "LocalCandidateReceiptV1")
        self.assertFalse(receipt["authorizes"])
        self.assertEqual(
            receipt["receipt_digest"],
            contract_digest(
                {key: value for key, value in receipt.items() if key != "receipt_digest"}
            ),
        )
        encoded = json.dumps(
            receipt, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_CANDIDATE_RECEIPT_BYTES)
        lowered = encoded.lower()
        for forbidden in (
            b"authority",
            b"session",
            b"nonce",
            b"grant",
            b"credential",
            b"prompt",
            b"log_body",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_validator_rejects_unknown_fields_digest_drift_and_bad_nested_shape(self) -> None:
        from control_plane.candidate_receipt import validate_local_candidate_receipt

        receipt = self.build()
        cases = (
            {**receipt, "session_id": "forbidden"},
            {**receipt, "receipt_digest": digest("forged")},
            {**receipt, "authorizes": True},
            {**receipt, "sandbox_status": "PASS"},
            {**receipt, "worktree_subject": {"algorithm": "sha256", "digest": digest("x")}},
            {**receipt, "independent_review": {"status": "PASS"}},
            {**receipt, "gates": [receipt["gates"][0], receipt["gates"][0]]},
            {
                **receipt,
                "security_review": {
                    "result_digest": digest("security-review"),
                    "status": {},
                },
            },
            {
                **receipt,
                "suite": {
                    "command": ["bash", "tests/run.sh"],
                    "count": 705,
                    "status": [],
                },
            },
        )
        for value in cases:
            with self.subTest(keys=set(value)):
                self.assertTrue(validate_local_candidate_receipt(value))

    def test_builder_rejects_oversize_and_unbounded_values(self) -> None:
        from control_plane.candidate_receipt import build_local_candidate_receipt

        with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
            build_local_candidate_receipt(
                **candidate_values(repository="/" + ("a" * 9000))
            )
        with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
            build_local_candidate_receipt(
                **candidate_values(
                    suite={
                        "command": ["bash", "tests/run.sh", "x" * 600],
                        "count": 705,
                        "status": "PASS",
                    }
                )
            )

    def test_candidate_module_is_part_of_the_distributed_runtime(self) -> None:
        from control_plane.adoption import RUNTIME_MODULES

        self.assertIn("candidate_receipt.py", RUNTIME_MODULES)


class LocalCandidateReceiptStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.candidate_receipt import (
            LocalCandidateReceiptStore,
            build_local_candidate_receipt,
        )

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.git_dir = Path(self.temporary.name) / "worktree-git-dir"
        self.git_dir.mkdir(mode=0o700)
        self.store = LocalCandidateReceiptStore(self.git_dir)
        self.receipt = build_local_candidate_receipt(**candidate_values())

    def _payload(self, receipt: dict[str, object] | None = None) -> bytes:
        return (canonical_json(receipt or self.receipt) + "\n").encode("utf-8")

    def _pending_name(self, receipt: dict[str, object] | None = None) -> str:
        value = receipt or self.receipt
        return (
            ".v2-3-local-candidate.json.pending-"
            + str(value["receipt_digest"]).removeprefix("sha256:")
        )

    def _noncanonical_payloads(self) -> tuple[tuple[str, bytes], ...]:
        canonical = canonical_json(self.receipt)
        return (
            (
                "duplicate-key",
                ('{"schema_version":1,' + canonical[1:] + "\n").encode("utf-8"),
            ),
            (
                "whitespace",
                (json.dumps(self.receipt, ensure_ascii=True, sort_keys=True, indent=1) + "\n").encode("utf-8"),
            ),
            (
                "insertion-order",
                (
                    json.dumps(
                        self.receipt,
                        ensure_ascii=True,
                        separators=(",", ":"),
                        sort_keys=False,
                    )
                    + "\n"
                ).encode("utf-8"),
            ),
        )

    def _candidate_directory(self, *, control_mode: int = 0o700) -> Path:
        control = self.git_dir / "codex-control-plane"
        control.mkdir(mode=control_mode, exist_ok=True)
        os.chmod(control, control_mode)
        candidates = control / "candidates"
        candidates.mkdir(mode=0o700, exist_ok=True)
        os.chmod(candidates, 0o700)
        return candidates

    def _assert_fifo_operation_is_bounded_and_fails_closed(
        self, *, operation: str
    ) -> None:
        context = multiprocessing.get_context("fork")
        receiving, sending = context.Pipe(duplex=False)
        process = context.Process(
            target=_fifo_candidate_worker,
            args=(str(self.git_dir), operation, self.receipt, sending),
        )
        process.start()
        sending.close()
        process.join(0.75)
        try:
            self.assertFalse(
                process.is_alive(),
                f"{operation} blocked while opening a FIFO candidate",
            )
            self.assertTrue(receiving.poll(0.25), "child did not report its result")
            error_type, message = receiving.recv()
            self.assertEqual(error_type, "ValueError")
            self.assertRegex(message, "^E_CANDIDATE_RECEIPT")
        finally:
            if process.is_alive():
                process.terminate()
                process.join(1)
            receiving.close()

    def test_canonical_fifo_returns_bounded_preserves_fifo_and_fails_closed(self) -> None:
        candidates = self._candidate_directory()
        os.mkfifo(self.store.path, 0o600)
        before = self.store.path.lstat()

        self._assert_fifo_operation_is_bounded_and_fails_closed(operation="load")

        after = self.store.path.lstat()
        self.assertTrue(stat.S_ISFIFO(after.st_mode))
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(tuple(candidates.iterdir()), (self.store.path,))

    def test_pending_fifo_returns_bounded_preserves_fifo_and_fails_closed(self) -> None:
        candidates = self._candidate_directory()
        pending = candidates / self._pending_name()
        os.mkfifo(pending, 0o600)
        before = pending.lstat()

        self._assert_fifo_operation_is_bounded_and_fails_closed(operation="store")

        after = pending.lstat()
        self.assertTrue(stat.S_ISFIFO(after.st_mode))
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(tuple(candidates.iterdir()), (pending,))

    def test_canonical_noncanonical_json_is_preserved_and_rejected(self) -> None:
        for case, payload in self._noncanonical_payloads():
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    from control_plane.candidate_receipt import LocalCandidateReceiptStore

                    git_dir = Path(directory) / "git-dir"
                    git_dir.mkdir(mode=0o700)
                    store = LocalCandidateReceiptStore(git_dir)
                    candidates = git_dir / "codex-control-plane" / "candidates"
                    candidates.mkdir(parents=True, mode=0o700)
                    os.chmod(candidates.parent, 0o700)
                    os.chmod(candidates, 0o700)
                    store.path.write_bytes(payload)
                    os.chmod(store.path, 0o600)
                    before = store.path.lstat()

                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.load()
                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.store(self.receipt)

                    after = store.path.lstat()
                    self.assertEqual(store.path.read_bytes(), payload)
                    self.assertEqual(
                        (before.st_dev, before.st_ino),
                        (after.st_dev, after.st_ino),
                    )

    def test_pending_noncanonical_json_is_preserved_and_rejected(self) -> None:
        for index, (case, payload) in enumerate(self._noncanonical_payloads()):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    from control_plane.candidate_receipt import LocalCandidateReceiptStore

                    git_dir = Path(directory) / "git-dir"
                    git_dir.mkdir(mode=0o700)
                    store = LocalCandidateReceiptStore(git_dir)
                    candidates = git_dir / "codex-control-plane" / "candidates"
                    candidates.mkdir(parents=True, mode=0o700)
                    os.chmod(candidates.parent, 0o700)
                    os.chmod(candidates, 0o700)
                    pending = candidates / self._pending_name()
                    pending.write_bytes(payload)
                    os.chmod(pending, 0o600)
                    before = pending.lstat()

                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.store(self.receipt)

                    after = pending.lstat()
                    self.assertEqual(pending.read_bytes(), payload)
                    self.assertEqual(
                        (before.st_dev, before.st_ino),
                        (after.st_dev, after.st_ino),
                    )
                    self.assertFalse(store.path.exists())

    def test_incremental_inventory_stops_at_caps_and_preserves_foreign_state(self) -> None:
        class InstrumentedScandir:
            def __init__(self, names: object) -> None:
                self._names = iter(names)
                self.consumed = 0

            def __enter__(self) -> "InstrumentedScandir":
                return self

            def __exit__(self, *unused: object) -> None:
                return None

            def __iter__(self) -> "InstrumentedScandir":
                return self

            def __next__(self) -> SimpleNamespace:
                name = next(self._names)
                self.consumed += 1
                return SimpleNamespace(name=name)

        cases = (
            ("entry-cap", (f"foreign-{index}" for index in range(100_000)), 65),
            ("name-byte-cap", ("x" * 250 for _ in range(100_000)), 17),
        )
        for case, names, maximum_consumed in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    from control_plane.candidate_receipt import LocalCandidateReceiptStore

                    git_dir = Path(directory) / "git-dir"
                    git_dir.mkdir(mode=0o700)
                    store = LocalCandidateReceiptStore(git_dir)
                    candidates = git_dir / "codex-control-plane" / "candidates"
                    candidates.mkdir(parents=True, mode=0o700)
                    os.chmod(candidates.parent, 0o700)
                    os.chmod(candidates, 0o700)
                    foreign = candidates / "foreign-state"
                    foreign.write_bytes(b"preserve-foreign-state")
                    os.chmod(foreign, 0o600)
                    before = foreign.lstat()
                    scanner = InstrumentedScandir(names)

                    with patch(
                        "control_plane.candidate_receipt.os.scandir",
                        return_value=scanner,
                    ) as scandir:
                        with self.assertRaisesRegex(
                            ValueError, "^E_CANDIDATE_RECEIPT"
                        ):
                            store.store(self.receipt)

                    self.assertLessEqual(scanner.consumed, maximum_consumed)
                    scanned = scandir.call_args.args[0]
                    self.assertIsInstance(scanned, int)
                    after = foreign.lstat()
                    self.assertEqual(foreign.read_bytes(), b"preserve-foreign-state")
                    self.assertEqual(
                        (before.st_dev, before.st_ino),
                        (after.st_dev, after.st_ino),
                    )
                    self.assertFalse(store.path.exists())

    def test_store_is_durable_private_exact_and_identical_replay_is_idempotent(self) -> None:
        first = self.store.store(self.receipt)
        path = self.store.path
        before = path.stat()
        pending = tuple(path.parent.glob(".v2-3-local-candidate.json.pending-*"))

        second = self.store.store(self.receipt)
        after = path.stat()

        self.assertEqual(first, self.receipt)
        self.assertEqual(second, self.receipt)
        loaded: dict[str, object] | None = None
        load_error: ValueError | None = None
        try:
            loaded = self.store.load()
        except ValueError as caught:
            load_error = caught
        self.assertIsNone(load_error, f"exact pair was rejected: {load_error}")
        self.assertEqual(loaded, self.receipt)
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            (path.stat().st_dev, path.stat().st_ino),
            (pending[0].stat().st_dev, pending[0].stat().st_ino),
        )
        self.assertEqual(path.stat().st_nlink, 2)
        self.assertEqual(stat.S_IMODE(pending[0].stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.parent.parent.stat().st_mode), 0o700)
        self.assertEqual(
            path,
            self.git_dir
            / "codex-control-plane"
            / "candidates"
            / "v2-3-local-candidate.json",
        )

    def test_canonical_only_legacy_receipt_remains_valid(self) -> None:
        candidates = self._candidate_directory()
        self.store.path.write_bytes(self._payload())
        os.chmod(self.store.path, 0o600)

        self.assertEqual(self.store.load(), self.receipt)
        self.assertEqual(self.store.store(self.receipt), self.receipt)
        self.assertEqual(self.store.path.stat().st_nlink, 1)
        self.assertEqual(tuple(candidates.iterdir()), (self.store.path,))

    def test_load_accepts_only_an_exact_canonical_pending_hardlink_pair(self) -> None:
        candidates = self._candidate_directory()
        self.store.path.write_bytes(self._payload())
        os.chmod(self.store.path, 0o600)
        pending = candidates / (
            self._pending_name()
        )
        os.link(self.store.path, pending)

        loaded: dict[str, object] | None = None
        load_error: ValueError | None = None
        try:
            loaded = self.store.load()
        except ValueError as caught:
            load_error = caught
        self.assertIsNone(load_error, f"exact pair was rejected: {load_error}")
        self.assertEqual(loaded, self.receipt)
        self.assertEqual(self.store.path.stat().st_nlink, 2)
        self.assertEqual(
            (self.store.path.stat().st_dev, self.store.path.stat().st_ino),
            (pending.stat().st_dev, pending.stat().st_ino),
        )

    def test_drift_is_rejected_without_replacing_exact_receipt(self) -> None:
        self.store.store(self.receipt)
        original = self.store.path.read_bytes()
        drifted = dict(self.receipt)
        drifted["observed_at"] = "2026-08-10T12:01:00Z"
        drifted["receipt_digest"] = contract_digest(
            {key: value for key, value in drifted.items() if key != "receipt_digest"}
        )

        with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT_DRIFT"):
            self.store.store(drifted)

        self.assertEqual(self.store.path.read_bytes(), original)

    def test_malformed_oversize_mode_symlink_and_hardlink_are_preserved(self) -> None:
        cases = ("malformed", "oversize", "mode", "symlink", "hardlink")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    from control_plane.candidate_receipt import LocalCandidateReceiptStore

                    git_dir = Path(directory) / "git-dir"
                    git_dir.mkdir(mode=0o700)
                    store = LocalCandidateReceiptStore(git_dir)
                    candidate_dir = git_dir / "codex-control-plane" / "candidates"
                    candidate_dir.mkdir(parents=True, mode=0o700)
                    os.chmod(candidate_dir.parent, 0o700)
                    path = store.path
                    target: Path | None = None
                    if case == "malformed":
                        path.write_text("{not-json", encoding="utf-8")
                        os.chmod(path, 0o600)
                    elif case == "oversize":
                        path.write_bytes(b"x" * 8193)
                        os.chmod(path, 0o600)
                    elif case == "mode":
                        path.write_bytes(b"{}")
                        os.chmod(path, 0o644)
                    elif case == "symlink":
                        target = Path(directory) / "target"
                        target.write_bytes(b"preserve-target")
                        path.symlink_to(target)
                    else:
                        target = Path(directory) / "target"
                        target.write_bytes(b"preserve-hardlink")
                        os.chmod(target, 0o600)
                        os.link(target, path)
                    before = path.lstat()
                    target_before = target.read_bytes() if target is not None else None

                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.load()
                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.store(self.receipt)

                    after = path.lstat()
                    self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
                    if target is not None:
                        self.assertEqual(target.read_bytes(), target_before)

    def test_unsafe_directory_and_owner_mismatch_fail_closed(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir(mode=0o700)
        (self.git_dir / "codex-control-plane").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
            self.store.store(self.receipt)
        self.assertEqual(list(outside.iterdir()), [])

        with tempfile.TemporaryDirectory() as directory:
            from control_plane.candidate_receipt import LocalCandidateReceiptStore

            git_dir = Path(directory) / "git-dir"
            git_dir.mkdir(mode=0o700)
            owner_store = LocalCandidateReceiptStore(git_dir)
            with patch("control_plane.candidate_receipt.os.getuid", return_value=-1):
                with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                    owner_store.store(self.receipt)

    def test_preexisting_deterministic_pending_collision_is_preserved(self) -> None:
        candidate_dir = self.git_dir / "codex-control-plane" / "candidates"
        candidate_dir.mkdir(parents=True, mode=0o700)
        os.chmod(candidate_dir.parent, 0o700)
        os.chmod(candidate_dir, 0o700)
        collision = candidate_dir / self._pending_name()
        collision.write_bytes(b"preserve-suspicious-temp")
        os.chmod(collision, 0o600)

        with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
            self.store.store(self.receipt)

        self.assertEqual(collision.read_bytes(), b"preserve-suspicious-temp")

    def test_failed_publication_preserves_replacement_of_owned_pending_inode(self) -> None:
        candidates = self._candidate_directory()
        pending = candidates / self._pending_name()
        replacement_payload = b"preserve-foreign-replacement"
        replacement_identity: tuple[int, int] | None = None

        def replace_pending_then_fail(*args: object, **kwargs: object) -> None:
            nonlocal replacement_identity
            self.assertEqual(args[0], pending.name)
            pending.unlink()
            pending.write_bytes(replacement_payload)
            os.chmod(pending, 0o600)
            replacement = pending.lstat()
            replacement_identity = (replacement.st_dev, replacement.st_ino)
            raise OSError("fault-after-pending-fsync")

        error: ValueError | None = None
        with patch(
            "control_plane.candidate_receipt.os.link",
            side_effect=replace_pending_then_fail,
        ):
            try:
                self.store.store(self.receipt)
            except ValueError as caught:
                error = caught

        self.assertIsInstance(error, ValueError)
        self.assertTrue(pending.exists(), "publication deleted the replacement inode")
        self.assertEqual(pending.read_bytes(), replacement_payload)
        after = pending.lstat()
        self.assertEqual((after.st_dev, after.st_ino), replacement_identity)
        self.assertFalse(self.store.path.exists())

    def test_pending_name_binds_receipt_digest_across_load_and_replay(self) -> None:
        candidates = self._candidate_directory()
        receipt_b = dict(self.receipt)
        receipt_b["observed_at"] = "2026-08-10T12:03:00Z"
        receipt_b["receipt_digest"] = contract_digest(
            {
                key: value
                for key, value in receipt_b.items()
                if key != "receipt_digest"
            }
        )
        payload_b = self._payload(receipt_b)
        expected_name = (
            ".v2-3-local-candidate.json.pending-"
            + str(self.receipt["receipt_digest"]).removeprefix("sha256:")
        )
        real_link = os.link
        linked_name: list[str] = []

        def replace_with_b_and_link(
            source: str,
            target: str,
            **kwargs: object,
        ) -> None:
            linked_name.append(source)
            pending = candidates / source
            pending.unlink()
            pending.write_bytes(payload_b)
            os.chmod(pending, 0o600)
            real_link(source, target, **kwargs)

        store_a_error: ValueError | None = None
        with patch(
            "control_plane.candidate_receipt.os.link",
            side_effect=replace_with_b_and_link,
        ):
            try:
                self.store.store(self.receipt)
            except ValueError as caught:
                store_a_error = caught

        self.assertIsInstance(store_a_error, ValueError)
        self.assertEqual(len(linked_name), 1)
        pending = candidates / linked_name[0]
        before_canonical = self.store.path.lstat()
        before_pending = pending.lstat()

        failures: list[ValueError | None] = []
        for operation in (
            self.store.load,
            lambda: self.store.store(self.receipt),
            lambda: self.store.store(receipt_b),
        ):
            try:
                operation()
            except ValueError as caught:
                failures.append(caught)
            else:
                failures.append(None)

        self.assertTrue(
            all(isinstance(error, ValueError) for error in failures),
            f"inconsistent pair was accepted: {failures}",
        )
        self.assertEqual(linked_name, [expected_name])
        self.assertEqual(self.store.path.read_bytes(), payload_b)
        self.assertEqual(pending.read_bytes(), payload_b)
        after_canonical = self.store.path.lstat()
        after_pending = pending.lstat()
        self.assertEqual(
            (before_canonical.st_dev, before_canonical.st_ino),
            (after_canonical.st_dev, after_canonical.st_ino),
        )
        self.assertEqual(
            (before_pending.st_dev, before_pending.st_ino),
            (after_pending.st_dev, after_pending.st_ino),
        )
        self.assertEqual(
            (after_canonical.st_dev, after_canonical.st_ino),
            (after_pending.st_dev, after_pending.st_ino),
        )
        self.assertEqual(after_canonical.st_nlink, 2)

    def test_legacy_owner_safe_parent_0755_is_accepted_without_chmod(self) -> None:
        control = self.git_dir / "codex-control-plane"
        control.mkdir(mode=0o755)
        os.chmod(control, 0o755)

        self.store.store(self.receipt)

        self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.store.path.parent.stat().st_mode), 0o700)
        self.assertEqual(self.store.load(), self.receipt)

    def test_writable_or_foreign_legacy_parent_is_rejected_and_preserved(self) -> None:
        for mode in (0o775, 0o777):
            with self.subTest(mode=oct(mode)):
                with tempfile.TemporaryDirectory() as directory:
                    from control_plane.candidate_receipt import LocalCandidateReceiptStore

                    git_dir = Path(directory) / "git-dir"
                    git_dir.mkdir(mode=0o700)
                    control = git_dir / "codex-control-plane"
                    control.mkdir(mode=mode)
                    os.chmod(control, mode)
                    store = LocalCandidateReceiptStore(git_dir)

                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.store(self.receipt)

                    self.assertEqual(stat.S_IMODE(control.stat().st_mode), mode)
                    self.assertEqual(list(control.iterdir()), [])

        control = self.git_dir / "codex-control-plane"
        control.mkdir(mode=0o755)
        os.chmod(control, 0o755)
        uid = os.getuid()
        with patch(
            "control_plane.candidate_receipt.os.getuid",
            side_effect=(uid, -1),
        ):
            with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                self.store.store(self.receipt)
        self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o755)
        self.assertEqual(list(control.iterdir()), [])

    def test_published_pair_is_retained_and_replay_never_unlinks(self) -> None:
        with patch(
            "control_plane.candidate_receipt.os.unlink",
            side_effect=AssertionError("candidate publication must not unlink"),
        ) as unlink:
            self.assertEqual(self.store.store(self.receipt), self.receipt)
            self.assertEqual(
                type(self.store)(self.git_dir).store(self.receipt), self.receipt
            )
            unlink.assert_not_called()

        pending = tuple(self.store.path.parent.glob(
            ".v2-3-local-candidate.json.pending-*"
        ))
        self.assertTrue(self.store.path.is_file())
        self.assertEqual(len(pending), 1)
        self.assertEqual(
            (self.store.path.stat().st_dev, self.store.path.stat().st_ino),
            (pending[0].stat().st_dev, pending[0].stat().st_ino),
        )
        self.assertEqual(self.store.path.stat().st_nlink, 2)

        self.assertEqual(self.store.path.stat().st_nlink, 2)
        self.assertTrue(pending[0].exists())

    def test_multiple_mismatched_or_symlink_pending_is_preserved_and_blocks(self) -> None:
        cases = ("multiple", "mismatch", "separate-identical", "symlink")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    from control_plane.candidate_receipt import LocalCandidateReceiptStore

                    git_dir = Path(directory) / "git-dir"
                    git_dir.mkdir(mode=0o700)
                    store = LocalCandidateReceiptStore(git_dir)
                    candidates = git_dir / "codex-control-plane" / "candidates"
                    candidates.mkdir(parents=True, mode=0o700)
                    os.chmod(candidates.parent, 0o700)
                    os.chmod(candidates, 0o700)
                    canonical = store.path
                    canonical.write_bytes(self._payload())
                    os.chmod(canonical, 0o600)
                    pending_one = candidates / self._pending_name()
                    if case == "multiple":
                        os.link(canonical, pending_one)
                        pending_two = candidates / (
                            ".v2-3-local-candidate.json.pending-" + ("b" * 64)
                        )
                        os.link(canonical, pending_two)
                    elif case == "mismatch":
                        drifted = dict(self.receipt)
                        drifted["observed_at"] = "2026-08-10T12:02:00Z"
                        drifted["receipt_digest"] = contract_digest(
                            {
                                key: value
                                for key, value in drifted.items()
                                if key != "receipt_digest"
                            }
                        )
                        pending_one.write_bytes(self._payload(drifted))
                        os.chmod(pending_one, 0o600)
                    elif case == "separate-identical":
                        pending_one.write_bytes(self._payload())
                        os.chmod(pending_one, 0o600)
                    else:
                        target = Path(directory) / "target"
                        target.write_bytes(self._payload())
                        pending_one.symlink_to(target)
                    before = {
                        path.name: path.lstat()
                        for path in candidates.iterdir()
                    }

                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.store(self.receipt)

                    after = {
                        path.name: path.lstat()
                        for path in candidates.iterdir()
                    }
                    self.assertEqual(set(after), set(before))
                    for name in before:
                        self.assertEqual(
                            (before[name].st_dev, before[name].st_ino),
                            (after[name].st_dev, after[name].st_ino),
                        )

    def test_exact_orphan_pending_is_recovered_but_foreign_or_drifted_is_preserved(self) -> None:
        candidates = self._candidate_directory()
        exact = candidates / self._pending_name()
        exact.write_bytes(self._payload())
        os.chmod(exact, 0o600)

        self.assertEqual(self.store.store(self.receipt), self.receipt)
        self.assertTrue(self.store.path.is_file())
        self.assertTrue(exact.exists())
        self.assertEqual(self.store.path.stat().st_nlink, 2)
        self.assertEqual(
            (self.store.path.stat().st_dev, self.store.path.stat().st_ino),
            (exact.stat().st_dev, exact.stat().st_ino),
        )

        for case in ("foreign", "drifted"):
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    from control_plane.candidate_receipt import LocalCandidateReceiptStore

                    git_dir = Path(directory) / "git-dir"
                    git_dir.mkdir(mode=0o700)
                    store = LocalCandidateReceiptStore(git_dir)
                    candidates = git_dir / "codex-control-plane" / "candidates"
                    candidates.mkdir(parents=True, mode=0o700)
                    os.chmod(candidates.parent, 0o700)
                    os.chmod(candidates, 0o700)
                    suffix = (
                        "foreign"
                        if case == "foreign"
                        else self._pending_name().removeprefix(
                            ".v2-3-local-candidate.json.pending-"
                        )
                    )
                    pending = candidates / (
                        ".v2-3-local-candidate.json.pending-" + suffix
                    )
                    payload = self._payload()
                    if case == "drifted":
                        payload = b"{\"not\":\"the exact receipt\"}\n"
                    pending.write_bytes(payload)
                    os.chmod(pending, 0o600)
                    before = pending.read_bytes()

                    with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
                        store.store(self.receipt)

                    self.assertEqual(pending.read_bytes(), before)
                    self.assertFalse(store.path.exists())

    def test_partial_prelink_pending_is_preserved_and_blocks_replay(self) -> None:
        pending = self._candidate_directory() / self._pending_name()

        def write_partial(descriptor: int, payload: bytes) -> None:
            os.write(descriptor, payload[:17])
            raise ValueError("fault-during-candidate-write")

        with patch.object(self.store, "_write_all", side_effect=write_partial):
            with self.assertRaisesRegex(ValueError, "fault-during-candidate-write"):
                self.store.store(self.receipt)

        self.assertTrue(pending.exists(), "partial pending was auto-unlinked")
        before = pending.lstat()
        self.assertEqual(pending.read_bytes(), self._payload()[:17])
        self.assertFalse(self.store.path.exists())
        with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
            self.store.store(self.receipt)
        after = pending.lstat()
        self.assertEqual(
            (before.st_dev, before.st_ino),
            (after.st_dev, after.st_ino),
        )

    def test_pending_inventory_is_bounded_and_preserved(self) -> None:
        candidates = self._candidate_directory()
        for index in range(65):
            path = candidates / f"foreign-{index:02d}"
            path.write_bytes(b"x")
            os.chmod(path, 0o600)

        with self.assertRaisesRegex(ValueError, "^E_CANDIDATE_RECEIPT"):
            self.store.store(self.receipt)

        self.assertEqual(len(tuple(candidates.iterdir())), 65)
        self.assertFalse(self.store.path.exists())


if __name__ == "__main__":
    unittest.main()
