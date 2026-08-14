from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from control_plane.contracts import contract_digest, load_active_adoption_journal
from control_plane.leases import LeaseStore
from control_plane.task_state import (
    CoreTaskStore,
    assert_no_active_legacy_state,
    inventory_legacy_state,
)


RUNTIME_DIGEST = "sha256:" + "1" * 64
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64


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


def _lock_record(path: Path, relative: str) -> dict[str, object]:
    value = path.lstat()
    return {
        "path": relative,
        "device": int(value.st_dev),
        "inode": int(value.st_ino),
        "mode": stat.S_IMODE(value.st_mode),
        "links": int(value.st_nlink),
        "uid": int(value.st_uid),
        "gid": int(value.st_gid),
        "size": int(value.st_size),
        "mtime_ns": int(value.st_mtime_ns),
        "ctime_ns": int(value.st_ctime_ns),
        "flags": int(getattr(value, "st_flags", 0)),
    }


def install_active_adoption_journal(repository: Path) -> tuple[Path, Path]:
    """Install a Core-local exact active journal fixture without Adoption imports."""

    common = Path(git(repository, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = repository / common
    state = common.resolve(strict=True) / "codex-control-plane-core"
    adoption = state / "adoption"
    locks = state / "locks"
    state.mkdir(mode=0o700)
    adoption.mkdir(mode=0o700)
    locks.mkdir(mode=0o700)
    lifecycle = state / "adoption.lock"
    verification = locks / "verification.lock"
    for path in (lifecycle, verification):
        path.write_bytes(b"")
        path.chmod(0o600)

    activation = repository / ".codex" / "control-plane.lock"
    if activation.exists():
        activation_payload = activation.read_text(encoding="utf-8").replace(
            "schema_version = 2\n",
            'schema_version = 2\nadoption_lifecycle = "journal-bound-v1"\n',
            1,
        )
    else:
        activation_payload = (
            'schema_version = 2\nadoption_lifecycle = "journal-bound-v1"\n'
        )
    activation.write_text(activation_payload, encoding="utf-8")
    codex = (repository / ".codex").lstat()
    locks_metadata = locks.lstat()
    try:
        head = git(repository, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        head = "a" * 40
    try:
        branch = git(repository, "branch", "--show-current") or "codex/core-test"
    except subprocess.CalledProcessError:
        branch = "codex/core-test"
    value: dict[str, object] = {
        "schema_version": 1,
        "kind": "CoreAdoptionJournalV1",
        "plan_digest": SHA_A,
        "install_digest": SHA_B,
        "state": "active",
        "source_manifest_digest": SHA_C,
        "target_binding": {
            "repository_id": [int(repository.lstat().st_dev), int(repository.lstat().st_ino)],
            "common_dir_id": [int(state.parent.lstat().st_dev), int(state.parent.lstat().st_ino)],
            "worktree_id": [int(common.lstat().st_dev), int(common.lstat().st_ino)],
            "branch": branch,
            "head": head,
            "policy_digest": SHA_A,
            "registry_digest": SHA_B,
            "adoption_lifecycle": "journal-bound-v1",
        },
        "before_snapshot_digest": SHA_A,
        "managed_parent_directories": [
            {
                "path": ".codex",
                "state": "present",
                "identity": [int(codex.st_dev), int(codex.st_ino)],
                "mode": stat.S_IMODE(codex.st_mode),
            },
            {"path": "control_plane", "state": "absent"},
            {"path": "scripts", "state": "absent"},
            {"path": ".codex/git-hooks", "state": "absent"},
            {"path": ".codex/hooks", "state": "absent"},
        ],
        "managed_repository_scan": {
            "contract": "managed-repositories-v1",
            "nested_repositories_absent": True,
            "gitlinks_absent": True,
        },
        "lifecycle_lock": _lock_record(
            lifecycle,
            "codex-control-plane-core/adoption.lock",
        ),
        "verification_lock": {
            "directory": {
                "path": "codex-control-plane-core/locks",
                "device": int(locks_metadata.st_dev),
                "inode": int(locks_metadata.st_ino),
                "mode": stat.S_IMODE(locks_metadata.st_mode),
                "uid": int(locks_metadata.st_uid),
                "gid": int(locks_metadata.st_gid),
                "flags": int(getattr(locks_metadata, "st_flags", 0)),
            },
            "file": _lock_record(
                verification,
                "codex-control-plane-core/locks/verification.lock",
            ),
        },
        "created_directories": [],
        "published_records": [],
        "target_lock_record": {
            "path": ".codex/control-plane.lock",
            "role": "activation_pointer",
            "sha256": SHA_B,
            "git_mode": "100644",
            "size_bytes": 256,
        },
        "prior_git_config": {"core.hooksPath": None},
        "rollback_records": [
            {
                "path": ".codex/control-plane.lock",
                "role": "activation_pointer",
                "sha256": SHA_B,
                "git_mode": "100644",
                "size_bytes": 256,
                "before": "absent",
            }
        ],
        "authorizes": False,
    }
    value["state_digest"] = contract_digest(value)
    journal = adoption / "journal.json"
    journal.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    journal.chmod(0o600)
    return verification, journal


def mutate_active_adoption_journal(path: Path, case: str) -> None:
    raw = path.read_text(encoding="utf-8")
    if case == "duplicate":
        path.write_text(
            raw.replace(
                '"schema_version":1',
                '"schema_version":1,"schema_version":1',
                1,
            ),
            encoding="utf-8",
        )
        return
    value = json.loads(raw)
    if case == "prior_git_config":
        value["prior_git_config"] = {"core.hooksPath": "hooks"}
    elif case == "nan":
        value["prior_git_config"] = {"core.hooksPath": float("nan")}
    elif case == "verification_lock":
        value["verification_lock"]["file"]["links"] = 2
    else:
        raise AssertionError(f"unknown journal mutation: {case}")
    unsigned = {key: item for key, item in value.items() if key != "state_digest"}
    value["state_digest"] = contract_digest(unsigned)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _private_state_snapshot(repository: Path) -> tuple[tuple[str, int, bytes], ...]:
    state = repository / ".git" / "codex-control-plane-core"
    records: list[tuple[str, int, bytes]] = []
    for path in sorted(state.rglob("*")):
        metadata = path.lstat()
        if path.is_file():
            records.append(
                (
                    path.relative_to(state).as_posix(),
                    stat.S_IMODE(metadata.st_mode),
                    path.read_bytes(),
                )
            )
    return tuple(records)


def install_provisioning_prefix(repository: Path, prefix: str) -> Path:
    state = repository / ".git" / "codex-control-plane-core"
    state.mkdir(mode=0o700)
    lifecycle = state / "adoption.lock"
    lifecycle.write_bytes(b"")
    lifecycle.chmod(0o600)
    if prefix in {"P2", "P3", "P3Q", "P4", "P4T"}:
        (state / "adoption").mkdir(mode=0o700)
    if prefix == "P2Q":
        (state / ".provisioning-adoption").mkdir(mode=0o700)
    if prefix == "P3Q":
        (state / ".provisioning-locks").mkdir(mode=0o700)
    if prefix in {"P3", "P4", "P4T"}:
        (state / "locks").mkdir(mode=0o700)
    if prefix in {"P4", "P4T"}:
        verification = state / "locks" / "verification.lock"
        verification.write_bytes(b"")
        verification.chmod(0o600)
    if prefix == "P4T":
        temporary = state / "adoption" / (".journal.json." + "a" * 32 + ".tmp")
        temporary.write_bytes(b"partial")
        temporary.chmod(0o600)
    return state


def private_state_identity_snapshot(
    repository: Path,
) -> tuple[tuple[str, str, int, int, int, int, int, bytes], ...]:
    state = repository / ".git" / "codex-control-plane-core"
    if not state.exists():
        return ()
    records: list[tuple[str, str, int, int, int, int, int, bytes]] = []
    for path in (state, *sorted(state.rglob("*"))):
        metadata = path.lstat()
        relative = "." if path == state else path.relative_to(state).as_posix()
        if stat.S_ISDIR(metadata.st_mode):
            kind = "directory"
            payload = b""
        elif stat.S_ISREG(metadata.st_mode):
            kind = "file"
            payload = path.read_bytes()
        else:
            kind = "other"
            payload = b""
        records.append(
            (
                relative,
                kind,
                int(metadata.st_dev),
                int(metadata.st_ino),
                stat.S_IMODE(metadata.st_mode),
                int(metadata.st_nlink),
                int(metadata.st_size),
                payload,
            )
        )
    return tuple(records)


def _json_node_count(value: object) -> int:
    if isinstance(value, dict):
        return 1 + sum(_json_node_count(item) for item in value.values())
    if isinstance(value, list):
        return 1 + sum(_json_node_count(item) for item in value)
    return 1


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
    def test_every_task_and_lease_writer_holds_lifecycle_before_task_lock(self) -> None:
        import control_plane.leases as core_leases
        import control_plane.task_state as core_task_state

        cases = (
            "start",
            "rollback_start",
            "transition",
            "resume",
            "next_revision",
            "bind_lease_generation",
            "restore_after_failed_binding",
            "acquire",
            "validate_continuation",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                store = CoreTaskStore(repo)
                leases = LeaseStore(repo)
                task_id = f"TASK-LOCK-ORDER-{case.upper().replace('_', '-')}"
                arguments = {
                    "outcome": "local_change",
                    "branch": "codex/core-test",
                    "head": git(repo, "rev-parse", "HEAD"),
                    "task_digest": contract_digest({"task": task_id}),
                    "decision_digest": contract_digest({"decision": task_id}),
                    "scope_paths": ["control_plane/task_state.py"],
                }
                original: dict[str, object] | None = None
                lease: dict[str, object] | None = None
                if case != "start":
                    original = store.start(task_id, **arguments)
                if case == "resume":
                    store.transition(task_id, "planned", current_branch="codex/core-test")
                    store.transition(
                        task_id,
                        "blocked",
                        reason="reviewed lock-order fixture",
                        current_branch="codex/core-test",
                    )
                elif case == "next_revision":
                    for target in (
                        "planned",
                        "ready",
                        "implementing",
                        "verifying",
                        "review_ready",
                        "closed",
                    ):
                        store.transition(task_id, target, current_branch="codex/core-test")
                elif case in {
                    "bind_lease_generation",
                    "restore_after_failed_binding",
                    "validate_continuation",
                }:
                    assert original is not None
                    lease = leases.acquire(
                        original,
                        session_id=f"SESSION-{case.upper().replace('_', '-')}",
                        policy_digest=contract_digest({"policy": case}),
                    )
                    if case in {"restore_after_failed_binding", "validate_continuation"}:
                        store.bind_lease_generation(
                            task_id,
                            revision_id=str(lease["revision_id"]),
                            generation=int(lease["lease_generation"]),
                            expected_state_digest=str(original["state_digest"]),
                            session_id=str(lease["session_id"]),
                        )

                if case == "start":
                    operation = lambda: store.start(task_id, **arguments)
                elif case == "rollback_start":
                    assert original is not None
                    operation = lambda: store.rollback_start(original)
                elif case == "transition":
                    operation = lambda: store.transition(
                        task_id,
                        "planned",
                        current_branch="codex/core-test",
                    )
                elif case == "resume":
                    operation = lambda: store.resume(
                        task_id,
                        current_branch="codex/core-test",
                    )
                elif case == "next_revision":
                    operation = lambda: store.next_revision(
                        task_id,
                        current_branch="codex/core-test",
                        head=git(repo, "rev-parse", "HEAD"),
                        task_digest=contract_digest({"task": "next"}),
                        decision_digest=contract_digest({"decision": "next"}),
                        scope_paths=["control_plane/task_state.py"],
                    )
                elif case == "bind_lease_generation":
                    assert original is not None and lease is not None
                    operation = lambda: store.bind_lease_generation(
                        task_id,
                        revision_id=str(lease["revision_id"]),
                        generation=int(lease["lease_generation"]),
                        expected_state_digest=str(original["state_digest"]),
                        session_id=str(lease["session_id"]),
                    )
                elif case == "restore_after_failed_binding":
                    assert original is not None and lease is not None
                    operation = lambda: store.restore_after_failed_binding(
                        original,
                        expected_revision_id=str(lease["revision_id"]),
                        expected_generation=int(lease["lease_generation"]),
                        session_id=str(lease["session_id"]),
                    )
                elif case == "acquire":
                    assert original is not None
                    operation = lambda: leases.acquire(
                        original,
                        session_id="SESSION-ACQUIRE-ORDER",
                        policy_digest=contract_digest({"policy": "acquire-order"}),
                    )
                else:
                    assert lease is not None
                    operation = lambda: core_task_state.validate_writer_continuation(
                        repo,
                        task_id=task_id,
                        worktree=str(store.repository),
                        branch="codex/core-test",
                        session_id=str(lease["session_id"]),
                        policy_digest=str(lease["policy_digest"]),
                        changed_paths=["control_plane/task_state.py"],
                    )

                lifecycle_depth = 0
                task_depth = 0
                real_lifecycle = core_leases._adoption_lifecycle_lock
                real_task_lock = core_task_state._task_lock

                @contextmanager
                def tracked_lifecycle(*args: object, **kwargs: object):
                    nonlocal lifecycle_depth
                    self.assertEqual(
                        task_depth,
                        0,
                        "lifecycle lock was acquired after the task lock",
                    )
                    with real_lifecycle(*args, **kwargs):
                        lifecycle_depth += 1
                        try:
                            yield
                        finally:
                            lifecycle_depth -= 1

                @contextmanager
                def tracked_task_lock(*args: object, **kwargs: object):
                    nonlocal task_depth
                    self.assertGreater(
                        lifecycle_depth,
                        0,
                        "task lock was acquired before the lifecycle lock",
                    )
                    with real_task_lock(*args, **kwargs):
                        task_depth += 1
                        try:
                            yield
                        finally:
                            task_depth -= 1

                with patch.object(
                    core_leases,
                    "_adoption_lifecycle_lock",
                    side_effect=tracked_lifecycle,
                ), patch.object(
                    core_task_state,
                    "_task_lock",
                    side_effect=tracked_task_lock,
                ):
                    operation()

    def test_transitional_provisioning_blocks_lease_claim_without_mutation(self) -> None:
        for prefix in ("P2", "P2Q", "P3", "P3Q", "P4", "P4T"):
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                install_provisioning_prefix(repo, prefix)
                before = private_state_identity_snapshot(repo)

                with self.assertRaisesRegex(ValueError, "^E_CORE_LEASE_PATH"):
                    with LeaseStore(repo).claim_no_active("TASK-PROVISIONING-PREFIX"):
                        self.fail("Core accepted a transitional provisioning prefix")

                self.assertEqual(private_state_identity_snapshot(repo), before)

    def test_active_adoption_journal_counts_the_root_toward_the_item_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            _, journal = install_active_adoption_journal(repo)
            value = json.loads(journal.read_text(encoding="utf-8"))
            value["managed_parent_directories"] = [
                {
                    "path": path,
                    "state": "present",
                    "identity": [1, 1],
                    "mode": 0o755,
                }
                for path in (
                    ".codex",
                    "control_plane",
                    "scripts",
                    ".codex/git-hooks",
                    ".codex/hooks",
                )
            ]
            value["created_directories"] = [
                {
                    "path": f"generated-{index:03d}",
                    "mode": 0o755,
                    "identity": [1, 1] if index == 0 else None,
                }
                for index in range(166)
            ]

            def record(path: str, *, rollback: bool = False) -> dict[str, object]:
                result: dict[str, object] = {
                    "path": path,
                    "role": "runtime",
                    "sha256": "sha256:" + "d" * 64,
                    "git_mode": "100644",
                    "size_bytes": 1,
                }
                if rollback:
                    result["before"] = "absent"
                return result

            value["published_records"] = [
                record(f"scripts/generated-{index:03d}") for index in range(255)
            ]
            value["rollback_records"] = [
                record(".codex/control-plane.lock", rollback=True),
                *[
                    record(f"scripts/generated-{index:03d}", rollback=True)
                    for index in range(255)
                ],
            ]
            unsigned = {
                key: item for key, item in value.items() if key != "state_digest"
            }
            value["state_digest"] = contract_digest(unsigned)
            self.assertEqual(_json_node_count(value), 4097)
            payload = (
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("utf-8")

            with self.assertRaisesRegex(ValueError, "item bounds"):
                load_active_adoption_journal(payload)

    def test_invalid_active_journal_blocks_new_task_before_creating_its_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            _, journal = install_active_adoption_journal(repo)
            mutate_active_adoption_journal(journal, "prior_git_config")
            before = _private_state_snapshot(repo)

            with self.assertRaisesRegex(ValueError, "^E_CORE_LEASE_PATH"):
                CoreTaskStore(repo).start(
                    "TASK-NEW-INVALID-JOURNAL",
                    outcome="local_change",
                    branch="codex/core-test",
                    head=git(repo, "rev-parse", "HEAD"),
                    task_digest=contract_digest({"task": "new-invalid"}),
                    decision_digest=contract_digest({"decision": "new-invalid"}),
                    scope_paths=["new/invalid"],
                )

            self.assertEqual(_private_state_snapshot(repo), before)

    def test_invalid_active_adoption_journal_blocks_task_and_lease_mutation(self) -> None:
        for case in ("prior_git_config", "duplicate", "nan", "verification_lock"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                _, journal = install_active_adoption_journal(repo)
                store = CoreTaskStore(repo)
                task_id = f"TASK-JOURNAL-{case.upper().replace('_', '-')}"
                state = store.start(
                    task_id,
                    outcome="local_change",
                    branch="codex/core-test",
                    head=git(repo, "rev-parse", "HEAD"),
                    task_digest=contract_digest({"task": case}),
                    decision_digest=contract_digest({"decision": case}),
                    scope_paths=[f"{case}/existing"],
                )
                task_path = store.tasks / f"{task_id}.json"
                mutate_active_adoption_journal(journal, case)
                before_task = task_path.read_bytes()
                before_state = _private_state_snapshot(repo)

                with self.assertRaisesRegex(ValueError, "^E_CORE_LEASE_PATH"):
                    store.transition(
                        task_id,
                        "planned",
                        current_branch="codex/core-test",
                    )

                self.assertEqual(task_path.read_bytes(), before_task)
                self.assertEqual(_private_state_snapshot(repo), before_state)

    def test_invalid_active_adoption_journal_blocks_new_lease_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            _, journal = install_active_adoption_journal(repo)
            state = CoreTaskStore(repo).start(
                "TASK-JOURNAL-NEW-CLAIM",
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "new-claim"}),
                decision_digest=contract_digest({"decision": "new-claim"}),
                scope_paths=["new/claim"],
            )
            mutate_active_adoption_journal(journal, "prior_git_config")
            before = _private_state_snapshot(repo)

            with self.assertRaisesRegex(ValueError, "^E_CORE_LEASE_PATH"):
                LeaseStore(repo).acquire(
                    state,
                    session_id="SESSION-JOURNAL-NEW-CLAIM",
                    policy_digest=contract_digest({"policy": "new-claim"}),
                )

            self.assertEqual(_private_state_snapshot(repo), before)

    def test_next_revision_revalidates_runtime_inside_the_lease_mutex(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = CoreTaskStore(repo)
            task_id = "TASK-REVISION-RUNTIME-RACE"
            state = store.start(
                task_id,
                outcome="local_change",
                branch="codex/core-test",
                head=git(repo, "rev-parse", "HEAD"),
                task_digest=contract_digest({"task": "first"}),
                decision_digest=contract_digest({"decision": "first"}),
                scope_paths=["control_plane/task_state.py"],
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
                    current_branch="codex/core-test",
                )
            before = store.status(task_id)

            with patch(
                "control_plane.task_state._locked_runtime_digest",
                side_effect=(RUNTIME_DIGEST, "sha256:" + "2" * 64),
            ), self.assertRaisesRegex(ValueError, "^E_CORE_RUNTIME"):
                store.next_revision(
                    task_id,
                    current_branch="codex/core-test",
                    head=git(repo, "rev-parse", "HEAD"),
                    task_digest=contract_digest({"task": "second"}),
                    decision_digest=contract_digest({"decision": "second"}),
                    scope_paths=["control_plane/task_state.py"],
                )

            self.assertEqual(store.status(task_id), before)

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
            lease = LeaseStore(repo).acquire(
                original,
                session_id="SESSION-ROLLBACK-DRIFT",
                policy_digest=contract_digest({"policy": "rollback-drift"}),
            )
            current = store.bind_lease_generation(
                original["task_id"],
                revision_id=original["revision_id"],
                generation=lease["lease_generation"],
                expected_state_digest=original["state_digest"],
                session_id="SESSION-ROLLBACK-DRIFT",
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
                    session_id="SESSION-ROLLBACK-DRIFT",
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

    def test_real_legacy_terminal_schema_variants_are_historical(self) -> None:
        variants = ("task", "task-with-run-binding")
        for variant in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
                root = git_dir / "codex-control-plane"
                tasks = root / "tasks"
                tasks.mkdir(parents=True)
                task_id = f"TASK-REAL-LEGACY-{variant.upper()}"
                owner = legacy_task(
                    task_id,
                    state="blocked",
                    resume_state=None,
                    resume_forbidden=True,
                )
                owner.update(
                    {
                        "block_reason": "E_REFRAME_REQUIRED",
                        "evidence": {"ready": {"preflight_ok": True}},
                        "generation": 5,
                        "revision": 0,
                        "updated_at": "2026-08-12T07:03:18.085940Z",
                        "verification_aborted": False,
                    }
                )
                if variant == "task-with-run-binding":
                    owner.update(
                        {
                            "active_run_revision_digest": contract_digest(
                                {"legacy-run-revision": task_id}
                            ),
                            "run_plan_digest": contract_digest(
                                {"legacy-run-plan": task_id}
                            ),
                        }
                    )
                (tasks / f"{task_id}.json").write_text(
                    json.dumps(owner) + "\n",
                    encoding="utf-8",
                )
                attempts = root / "runs" / task_id
                attempts.mkdir(parents=True)
                (attempts / "attempt-1.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": "RunAttemptV1",
                            "task_id": task_id,
                            "status": "UNKNOWN",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                result = inventory_legacy_state(repo)

                self.assertFalse(result["active"])
                self.assertTrue(result["records"])
                self.assertTrue(all(not record["active"] for record in result["records"]))
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

    def test_terminal_local_unknown_run_outcomes_are_historical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            root = git_dir / "codex-control-plane"
            task_id = "TASK-TERMINAL-LOCAL-UNKNOWN"
            tasks = root / "tasks"
            receipts = root / "runs" / task_id / "receipts"
            tasks.mkdir(parents=True)
            receipts.mkdir(parents=True)
            (tasks / f"{task_id}.json").write_text(
                json.dumps(
                    legacy_task(
                        task_id,
                        state="blocked",
                        resume_forbidden=True,
                        resume_state=None,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            (receipts.parent / "attempt-1.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "RunAttemptV1",
                        "task_id": task_id,
                        "status": "UNKNOWN",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (receipts / "gate.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "GateReceiptV1",
                        "task_id": task_id,
                        "status": "UNKNOWN",
                        "error_code": "E_RUN_GATE_RELEVANT_TESTS_UNKNOWN",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = inventory_legacy_state(repo)
            run_records = [
                record for record in result["records"] if record["kind"] == "run"
            ]

            self.assertFalse(result["active"])
            self.assertEqual(len(run_records), 2)
            self.assertTrue(all(not record["active"] for record in run_records))
            self.assertTrue(
                all(record["contract_status"] == "valid" for record in run_records)
            )
            self.assertEqual(assert_no_active_legacy_state(repo), result)

    def test_local_unknown_exemption_does_not_hide_remote_or_open_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
            root = git_dir / "codex-control-plane"
            task_id = "TASK-STRICT-UNKNOWN"
            tasks = root / "tasks"
            receipts = root / "runs" / task_id / "receipts"
            tasks.mkdir(parents=True)
            receipts.mkdir(parents=True)
            (tasks / f"{task_id}.json").write_text(
                json.dumps(
                    legacy_task(
                        task_id,
                        state="blocked",
                        resume_forbidden=True,
                        resume_state=None,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            artifacts = {
                receipts / "remote.json": {
                    "schema_version": 1,
                    "kind": "GateReceiptV1",
                    "task_id": task_id,
                    "status": "UNKNOWN",
                    "remote_status": "UNKNOWN",
                },
                receipts.parent / "attempt-1.json": {
                    "schema_version": 1,
                    "kind": "RunAttemptV1",
                    "task_id": task_id,
                    "status": "UNKNOWN",
                    "pending_remote_effect": {"status": "UNKNOWN"},
                },
                receipts.parent / "plan.json": {
                    "schema_version": 1,
                    "kind": "RunPlanV1",
                    "task_id": task_id,
                    "status": "UNKNOWN",
                },
            }
            for path, payload in artifacts.items():
                path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

            result = inventory_legacy_state(repo)
            run_records = [
                record for record in result["records"] if record["kind"] == "run"
            ]

            self.assertTrue(result["active"])
            self.assertEqual(len(run_records), 3)
            self.assertTrue(all(record["active"] for record in run_records))

    def test_local_unknown_exemption_does_not_hide_any_pending_marker(self) -> None:
        pending_markers = (
            {"pending_remote_effect": "UNKNOWN"},
            {"pending_remote_effect": {"phase": "prepared"}},
        )
        for index, marker in enumerate(pending_markers, start=1):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
                root = git_dir / "codex-control-plane"
                task_id = f"TASK-PENDING-MARKER-{index}"
                tasks = root / "tasks"
                run = root / "runs" / task_id
                tasks.mkdir(parents=True)
                run.mkdir(parents=True)
                (tasks / f"{task_id}.json").write_text(
                    json.dumps(
                        legacy_task(
                            task_id,
                            state="blocked",
                            resume_forbidden=True,
                            resume_state=None,
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                )
                (run / "attempt-1.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": "RunAttemptV1",
                            "task_id": task_id,
                            "status": "UNKNOWN",
                            **marker,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                result = inventory_legacy_state(repo)
                run_record = next(
                    record for record in result["records"] if record["kind"] == "run"
                )

                self.assertTrue(result["active"])
                self.assertTrue(run_record["active"])

    def test_local_unknown_run_requires_exact_terminal_owner_without_lease(self) -> None:
        cases = ("missing", "invalid", "active", "leased")
        for owner_case in cases:
            with self.subTest(owner_case=owner_case), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
                root = git_dir / "codex-control-plane"
                task_id = f"TASK-OWNER-{owner_case.upper()}"
                tasks = root / "tasks"
                run = root / "runs" / task_id
                run.mkdir(parents=True)
                if owner_case != "missing":
                    tasks.mkdir()
                    task = legacy_task(
                        task_id,
                        state="implementing" if owner_case == "active" else "closed",
                    )
                    if owner_case == "invalid":
                        task.pop("task_digest")
                    (tasks / f"{task_id}.json").write_text(
                        json.dumps(task) + "\n", encoding="utf-8"
                    )
                if owner_case == "leased":
                    leases = root / "leases"
                    leases.mkdir()
                    (leases / "lease.json").write_text(
                        json.dumps({"schema_version": 1, "task_id": task_id}) + "\n",
                        encoding="utf-8",
                    )
                (run / "attempt-1.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": "RunAttemptV1",
                            "task_id": task_id,
                            "status": "UNKNOWN",
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

    def test_local_unknown_run_rejects_a_contradictory_terminal_owner(self) -> None:
        owner_mutations = {
            "resumable_closed": {"resume_state": "implementing"},
            "non_boolean_resume": {"resume_forbidden": "false"},
            "unknown_field": {"unexpected": False},
            "unsupported_outcome": {"outcome": "external_effect"},
            "empty_branch": {"branch": ""},
        }
        for case, mutation in owner_mutations.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repo = make_repo(Path(directory) / "repo")
                git_dir = Path(git(repo, "rev-parse", "--path-format=absolute", "--git-dir"))
                root = git_dir / "codex-control-plane"
                task_id = f"TASK-CONTRADICTORY-{case.upper().replace('_', '-')}"
                tasks = root / "tasks"
                run = root / "runs" / task_id
                tasks.mkdir(parents=True)
                run.mkdir(parents=True)
                owner = legacy_task(task_id, state="closed", **mutation)
                (tasks / f"{task_id}.json").write_text(
                    json.dumps(owner) + "\n",
                    encoding="utf-8",
                )
                (run / "attempt-1.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": "RunAttemptV1",
                            "task_id": task_id,
                            "status": "UNKNOWN",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                result = inventory_legacy_state(repo)
                records = [
                    record
                    for record in result["records"]
                    if record.get("task_id") == task_id
                ]

                self.assertTrue(result["active"])
                self.assertTrue(all(record["active"] for record in records))
                with self.assertRaisesRegex(ValueError, "E_ACTIVE_LEGACY_STATE"):
                    assert_no_active_legacy_state(repo)

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
