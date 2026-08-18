from __future__ import annotations

import copy
import fcntl
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from control_plane.contracts import (
    CORE_STATES,
    STABLE_PAUSE_CHECK_VALUES,
    STABLE_PAUSE_ISSUE_CODES,
    STABLE_PAUSE_ISSUE_DIMENSIONS,
    STABLE_PAUSE_LEASE_VALUES,
    STABLE_PAUSE_MUTEX_VALUES,
    STABLE_PAUSE_STATUSES,
    derive_stable_pause_status,
    load_stable_pause_observation,
    stable_pause_checkpoint_digest,
    validate_stable_pause_observation,
    contract_digest,
    canonical_json,
)
from tests.core_stable_pause_test_support import (
    DIGEST_A,
    DIGEST_B,
    git,
    install_lifecycle_fixture,
    make_repository,
    private_state_identity_snapshot,
    repository_surface_snapshot,
    resigned,
    stable_pause_observation,
)


class StablePauseContractTests(unittest.TestCase):
    def test_closed_vocabularies_are_exact(self) -> None:
        self.assertEqual(
            STABLE_PAUSE_STATUSES,
            (
                "SAFE_PAUSE_ACTIVE",
                "SAFE_PAUSE_TERMINAL",
                "UNSAFE_PAUSE",
                "UNKNOWN",
            ),
        )
        self.assertEqual(STABLE_PAUSE_CHECK_VALUES, ("PASS", "FAIL", "UNKNOWN"))
        self.assertEqual(
            STABLE_PAUSE_MUTEX_VALUES,
            ("free", "held", "absent", "unknown"),
        )
        self.assertEqual(STABLE_PAUSE_LEASE_VALUES, ("active", "absent", "unknown"))
        self.assertEqual(
            STABLE_PAUSE_ISSUE_CODES,
            (
                "E_STABLE_PAUSE_REPOSITORY",
                "E_STABLE_PAUSE_SNAPSHOT_DRIFT",
                "E_STABLE_PAUSE_LIFECYCLE",
                "E_STABLE_PAUSE_OPERATION_ACTIVE",
                "E_STABLE_PAUSE_RESIDUE",
                "E_STABLE_PAUSE_BOUNDS",
            ),
        )
        self.assertEqual(
            STABLE_PAUSE_ISSUE_DIMENSIONS,
            ("repository", "snapshot", "lifecycle", "operation", "residue", "bounds"),
        )
        self.assertNotIn("paused", CORE_STATES)

    def test_valid_active_and_terminal_objects_round_trip(self) -> None:
        active = stable_pause_observation()
        terminal = stable_pause_observation(
            status="SAFE_PAUSE_TERMINAL",
            task_state="closed",
            lease_state="absent",
        )
        for value in (active, terminal):
            with self.subTest(status=value["status"]):
                self.assertEqual(validate_stable_pause_observation(value), value)
                payload = json.dumps(value, sort_keys=True).encode("utf-8")
                self.assertEqual(load_stable_pause_observation(payload), value)
                self.assertLessEqual(len(json.dumps(value, sort_keys=True, separators=(",", ":"))), 4096)

    def test_exact_root_and_nested_fields_are_required(self) -> None:
        cases = (
            ((), "unexpected"),
            (("repository",), "unexpected"),
            (("lifecycle",), "unexpected"),
            (("control_plane_state",), "unexpected"),
            (("checks",), "unexpected"),
            (("issues", 0), "unexpected"),
        )
        base = stable_pause_observation()
        base["issues"] = [
            {"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "residue"}
        ]
        base = resigned(base)
        for path, key in cases:
            with self.subTest(path=path):
                candidate = copy.deepcopy(base)
                target: object = candidate
                for part in path:
                    target = target[part]  # type: ignore[index]
                target[key] = False  # type: ignore[index]
                candidate = resigned(candidate)
                with self.assertRaisesRegex(ValueError, "stable pause"):
                    validate_stable_pause_observation(candidate)

    def test_safe_status_cannot_hide_mutex_residue_or_lifecycle_contradictions(self) -> None:
        candidates: list[dict[str, object]] = []

        held = stable_pause_observation()
        held["control_plane_state"]["adoption_mutex"] = "held"
        candidates.append(held)

        residue = stable_pause_observation()
        residue["control_plane_state"]["residue_count"] = 1
        candidates.append(residue)

        candidates.append(stable_pause_observation(lease_state="absent"))
        candidates.append(stable_pause_observation(status="SAFE_PAUSE_TERMINAL"))

        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                validate_stable_pause_observation(resigned(candidate))

    def test_digest_and_scalar_contracts_fail_closed(self) -> None:
        mutations = (
            ("upper digest", lambda value: value["repository"].__setitem__("status_digest", "sha256:" + "A" * 64)),
            ("negative count", lambda value: value["repository"].__setitem__("staged_count", -1)),
            ("boolean count", lambda value: value["repository"].__setitem__("staged_count", False)),
            ("unknown status", lambda value: value.__setitem__("status", "SAFE")),
            ("unknown mutex", lambda value: value["control_plane_state"].__setitem__("task_mutex", "idle")),
            ("unknown lease", lambda value: value["lifecycle"].__setitem__("lease_state", "held")),
            ("active without digest", lambda value: value["lifecycle"].__setitem__("lease_digest", None)),
            ("absent with digest", lambda value: (value["lifecycle"].__setitem__("lease_state", "absent"), value["lifecycle"].__setitem__("lease_digest", DIGEST_A))),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                value = stable_pause_observation()
                mutate(value)
                value = resigned(value)
                with self.assertRaisesRegex(ValueError, "stable pause"):
                    validate_stable_pause_observation(value)

    def test_issues_are_closed_sorted_unique_and_bounded(self) -> None:
        value = stable_pause_observation(status="UNSAFE_PAUSE")
        value["checks"]["owned_residue"] = "FAIL"
        value["issues"] = [
            {"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "residue"}
        ]
        self.assertEqual(validate_stable_pause_observation(resigned(value))["issues"], value["issues"])

        invalid = (
            list(reversed(value["issues"] * 2)),
            [{"code": "E_STABLE_PAUSE_HOST_UNKNOWN", "dimension": "bounds"}],
            [{"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "repository"}],
            [{"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "residue", "path": "/tmp/private"}],
            [{"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "residue"}] * 9,
        )
        for issues in invalid:
            candidate = stable_pause_observation(status="UNSAFE_PAUSE")
            candidate["issues"] = issues
            candidate = resigned(candidate)
            with self.assertRaisesRegex(ValueError, "stable pause"):
                validate_stable_pause_observation(candidate)

    def test_recursive_authority_and_privacy_fields_are_rejected(self) -> None:
        for key in (
            "timestamp",
            "duration",
            "hostname",
            "pid",
            "nonce",
            "session_id",
            "prompt",
            "transcript",
            "exception",
            "path",
        ):
            with self.subTest(key=key):
                value = stable_pause_observation()
                value[key] = "attacker-controlled"
                value = resigned(value)
                with self.assertRaisesRegex(ValueError, "stable pause"):
                    validate_stable_pause_observation(value)

        nested = stable_pause_observation()
        nested["issues"] = [
            {
                "code": "E_STABLE_PAUSE_RESIDUE",
                "dimension": "residue",
                "authorizes": True,
            }
        ]
        with self.assertRaisesRegex(ValueError, "authorize"):
            validate_stable_pause_observation(nested)

    def test_strict_loader_rejects_duplicate_nonfinite_and_bounds(self) -> None:
        valid = stable_pause_observation()
        payload = json.dumps(valid, sort_keys=True, separators=(",", ":")).encode()
        duplicate = payload.replace(b'"authorizes":false', b'"authorizes":false,"authorizes":false', 1)
        cases = (
            duplicate,
            payload.replace(b'"staged_count":0', b'"staged_count":NaN', 1),
            b"\xff",
            b"{" + b'"x":' * 34 + b"0" + b"}" * 34,
            b" " * 4097,
        )
        for candidate in cases:
            with self.subTest(size=len(candidate)), self.assertRaisesRegex(ValueError, "stable pause"):
                load_stable_pause_observation(candidate)

    def test_checkpoint_digest_is_domain_separated_and_replay_stable(self) -> None:
        value = stable_pause_observation()
        first = stable_pause_checkpoint_digest(value)
        second = stable_pause_checkpoint_digest(copy.deepcopy(value))
        self.assertEqual(first, second)
        self.assertEqual(first, value["checkpoint_digest"])
        value["repository"]["unstaged_count"] = 2
        self.assertNotEqual(stable_pause_checkpoint_digest(value), first)
        value["authorizes"] = True
        with self.assertRaisesRegex(ValueError, "authorize"):
            stable_pause_checkpoint_digest(value)

    def test_status_derivation_uses_closed_precedence(self) -> None:
        passed = {
            "repository_identity": "PASS",
            "snapshot_stability": "PASS",
            "lifecycle_binding": "PASS",
            "mutex_quiescence": "PASS",
            "owned_residue": "PASS",
        }
        self.assertEqual(derive_stable_pause_status(passed, "active"), "SAFE_PAUSE_ACTIVE")
        self.assertEqual(derive_stable_pause_status(passed, "terminal"), "SAFE_PAUSE_TERMINAL")
        unknown = dict(passed, repository_identity="UNKNOWN")
        self.assertEqual(derive_stable_pause_status(unknown, "active"), "UNKNOWN")
        failed = dict(unknown, owned_residue="FAIL")
        self.assertEqual(derive_stable_pause_status(failed, "unknown"), "UNSAFE_PAUSE")
        self.assertEqual(derive_stable_pause_status(passed, "contradiction"), "UNSAFE_PAUSE")


class StablePauseRepositoryTests(unittest.TestCase):
    def test_clean_and_dirty_snapshots_are_deterministic_and_byte_bound(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            clean_a = observe_repository_snapshot(repo)
            clean_b = observe_repository_snapshot(repo)
            self.assertEqual(clean_a, clean_b)
            self.assertEqual(clean_a["staged_count"], 0)
            self.assertEqual(clean_a["unstaged_count"], 0)
            self.assertEqual(clean_a["untracked_count"], 0)

            tracked = repo / "tracked.txt"
            tracked.write_text("first dirty bytes\n", encoding="utf-8")
            dirty_a = observe_repository_snapshot(repo)
            tracked.write_text("other dirty bytes\n", encoding="utf-8")
            dirty_b = observe_repository_snapshot(repo)

            self.assertEqual(dirty_a["status_digest"], dirty_b["status_digest"])
            self.assertNotEqual(dirty_a["worktree_digest"], dirty_b["worktree_digest"])
            self.assertEqual(dirty_a["unstaged_count"], 1)

    def test_index_hints_and_filemode_config_cannot_hide_worktree_drift(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            git(repo, "update-index", "--assume-unchanged", "tracked.txt")
            (repo / "tracked.txt").write_text("hidden dirty bytes\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "E_STABLE_PAUSE_BOUNDS"):
                observe_repository_snapshot(repo)

            git(repo, "update-index", "--no-assume-unchanged", "tracked.txt")
            git(repo, "checkout", "--", "tracked.txt")
            git(repo, "update-index", "--skip-worktree", "tracked.txt")
            with self.assertRaisesRegex(ValueError, "E_STABLE_PAUSE_BOUNDS"):
                observe_repository_snapshot(repo)
            git(repo, "update-index", "--no-skip-worktree", "tracked.txt")
            git(repo, "config", "core.filemode", "false")
            baseline_mode = observe_repository_snapshot(repo)
            (repo / "tracked.txt").chmod(0o755)
            hidden_mode = observe_repository_snapshot(repo)
            self.assertEqual(hidden_mode["unstaged_count"], 1)
            self.assertNotEqual(
                hidden_mode["worktree_digest"],
                baseline_mode["worktree_digest"],
            )

    def test_staged_unstaged_untracked_rename_delete_and_modes_are_bound(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            staged = repo / "staged.sh"
            staged.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            staged.chmod(0o755)
            git(repo, "add", "staged.sh")
            (repo / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
            (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
            os.symlink("untracked.txt", repo / "untracked-link")

            observed = observe_repository_snapshot(repo)

            self.assertEqual(observed["staged_count"], 1)
            self.assertEqual(observed["unstaged_count"], 1)
            self.assertEqual(observed["untracked_count"], 2)
            first_digest = observed["worktree_digest"]

            git(repo, "mv", "tracked.txt", "renamed.txt")
            renamed = observe_repository_snapshot(repo)
            self.assertNotEqual(renamed["worktree_digest"], first_digest)
            (repo / "renamed.txt").unlink()
            deleted = observe_repository_snapshot(repo)
            self.assertNotEqual(deleted["worktree_digest"], renamed["worktree_digest"])

    def test_staged_blobs_use_one_globally_bounded_cat_file_batch(self) -> None:
        from control_plane import stable_pause

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            for index in range(3):
                (repo / f"staged-{index}.txt").write_text(
                    f"staged {index}\n",
                    encoding="utf-8",
                )
            git(repo, "add", "staged-0.txt", "staged-1.txt", "staged-2.txt")
            real_run_git = stable_pause._run_git
            batches = 0

            def counted_run_git(*args: object, **kwargs: object):
                nonlocal batches
                if len(args) >= 2 and args[1] == stable_pause._BATCH_ARGUMENTS:
                    batches += 1
                return real_run_git(*args, **kwargs)

            with patch.object(stable_pause, "_run_git", side_effect=counted_run_git):
                stable_pause.observe_repository_snapshot(repo)

            self.assertEqual(batches, 1)

    def test_diff_check_failure_is_visible_but_snapshot_remains_complete(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            (repo / "tracked.txt").write_text("trailing whitespace   \n", encoding="utf-8")

            observed = observe_repository_snapshot(repo)

            self.assertEqual(observed["diff_check"], "FAIL")
            self.assertIsInstance(observed["worktree_digest"], str)

    def test_unsafe_leaf_types_and_hardlinks_fail_without_git_mutation(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        cases = ("fifo", "hardlink")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repo = make_repository(Path(directory) / "repo")
                leaf = repo / "unsafe"
                if case == "fifo":
                    os.mkfifo(leaf)
                else:
                    source = repo / "hardlink-source"
                    source.write_text("same inode\n", encoding="utf-8")
                    os.link(source, leaf)
                before = repository_surface_snapshot(repo)

                with self.assertRaisesRegex(ValueError, "E_STABLE_PAUSE_"):
                    observe_repository_snapshot(repo)

                self.assertEqual(repository_surface_snapshot(repo), before)

    def test_nested_and_bare_repositories_are_rejected_as_unbound_content(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        for kind in ("worktree", "bare"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                repo = make_repository(Path(directory) / "repo")
                nested = repo / ("nested" if kind == "worktree" else "nested.git")
                if kind == "worktree":
                    git(repo, "init", "-b", "main", str(nested))
                else:
                    git(repo, "init", "--bare", str(nested))

                with self.assertRaisesRegex(ValueError, "E_STABLE_PAUSE_BOUNDS"):
                    observe_repository_snapshot(repo)

    def test_ignored_cache_content_is_outside_the_repository_snapshot(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            (repo / ".gitignore").write_text("cache/\n", encoding="utf-8")
            git(repo, "add", ".gitignore")
            git(repo, "commit", "-m", "ignore local cache")
            cache = repo / "cache"
            cache.mkdir()
            os.mkfifo(cache / "worker.pipe")

            observed = observe_repository_snapshot(repo)

            self.assertEqual(observed["untracked_count"], 0)

    def test_hostile_git_redirect_environment_is_ignored(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            other = make_repository(Path(directory) / "other")
            (repo / "tracked.txt").write_text("dirty\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(other / ".git"),
                    "GIT_WORK_TREE": str(other),
                    "GIT_INDEX_FILE": str(other / ".git" / "index"),
                    "GIT_CONFIG_GLOBAL": str(other / "hostile-config"),
                    "GIT_PAGER": "false",
                },
            ):
                observed = observe_repository_snapshot(repo)
            self.assertEqual(observed["root"], str(repo))
            self.assertEqual(observed["unstaged_count"], 1)

    def test_local_core_worktree_redirect_cannot_substitute_another_repository(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = make_repository(base / "repo")
            other = make_repository(base / "other")
            git(repo, "config", "core.worktree", str(other))

            with self.assertRaisesRegex(ValueError, "E_STABLE_PAUSE_REPOSITORY"):
                observe_repository_snapshot(repo)

    def test_repository_filter_is_rejected_before_git_observation_executes_it(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            sentinel = repo / "filter-executed"
            filter_script = repo / "hostile_filter.py"
            filter_script.write_text(
                "from pathlib import Path\n"
                "import sys\n"
                f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n"
                "sys.stdout.buffer.write(sys.stdin.buffer.read())\n",
                encoding="utf-8",
            )
            (repo / ".gitattributes").write_text(
                "tracked.txt filter=stable-pause-hostile\n",
                encoding="utf-8",
            )
            git(repo, "add", ".gitattributes", "hostile_filter.py")
            git(repo, "commit", "-m", "add hostile filter definition")
            git(
                repo,
                "config",
                "filter.stable-pause-hostile.clean",
                f"{shlex.quote(sys.executable)} {shlex.quote(str(filter_script))}",
            )
            (repo / "tracked.txt").write_text("dirty bytes\n", encoding="utf-8")
            before = repository_surface_snapshot(repo)

            with self.assertRaisesRegex(ValueError, "E_STABLE_PAUSE_REPOSITORY"):
                observe_repository_snapshot(repo)

            self.assertFalse(sentinel.exists())
            self.assertEqual(repository_surface_snapshot(repo), before)

    def test_worktree_inventory_uses_descriptor_relative_scandir(self) -> None:
        from control_plane.stable_pause import observe_repository_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            nested = repo / "nested"
            nested.mkdir()
            (nested / "untracked.txt").write_text("bytes\n", encoding="utf-8")
            real_scandir = os.scandir

            def descriptor_only(target: object):
                if not isinstance(target, int):
                    candidate = Path(target).resolve(strict=False)  # type: ignore[arg-type]
                    if candidate == repo or repo in candidate.parents:
                        raise AssertionError("worktree scandir must use a directory descriptor")
                return real_scandir(target)  # type: ignore[arg-type]

            with patch("control_plane.stable_pause.os.scandir", side_effect=descriptor_only):
                observed = observe_repository_snapshot(repo)

            self.assertEqual(observed["untracked_count"], 1)


class StablePauseLifecycleTests(unittest.TestCase):
    def test_coherent_active_and_terminal_lifecycles_are_observed_without_writes(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        for terminal in (False, True):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                repo = make_repository(Path(directory) / "repo")
                state, lease = install_lifecycle_fixture(repo, terminal=terminal)
                before = private_state_identity_snapshot(repo)

                observed = observe_control_plane_snapshot(repo, state["task_id"])

                self.assertEqual(observed["checks"]["lifecycle_binding"], "PASS")
                self.assertEqual(observed["checks"]["mutex_quiescence"], "PASS")
                self.assertEqual(observed["lifecycle_class"], "terminal" if terminal else "active")
                self.assertEqual(observed["lifecycle"]["task_state"], state["state"])
                self.assertEqual(
                    observed["lifecycle"]["lease_state"],
                    "absent" if terminal else "active",
                )
                self.assertEqual(
                    observed["lifecycle"]["lease_digest"],
                    None if lease is None else lease["lease_digest"],
                )
                self.assertTrue(
                    all(
                        observed["control_plane_state"][name] == "free"
                        for name in (
                            "adoption_mutex",
                            "verification_mutex",
                            "task_mutex",
                            "lease_mutex",
                        )
                    )
                )
                self.assertEqual(private_state_identity_snapshot(repo), before)

    def test_protected_state_inventory_uses_descriptor_relative_scandir(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, _ = install_lifecycle_fixture(repo)
            state_root = (repo / ".git" / "codex-control-plane-core").resolve()
            real_scandir = os.scandir

            def descriptor_only(target: object):
                if not isinstance(target, int):
                    candidate = Path(target).resolve(strict=False)  # type: ignore[arg-type]
                    if candidate == state_root or state_root in candidate.parents:
                        raise AssertionError("state scandir must use a directory descriptor")
                return real_scandir(target)  # type: ignore[arg-type]

            with patch("control_plane.stable_pause.os.scandir", side_effect=descriptor_only):
                observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["checks"]["lifecycle_binding"], "PASS")
            self.assertEqual(observed["checks"]["owned_residue"], "PASS")

    def test_legacy_verification_mutex_may_be_absent_but_required_mutexes_may_not(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, _ = install_lifecycle_fixture(repo)
            core = repo / ".git" / "codex-control-plane-core"
            verification = core / "locks" / "verification.lock"
            verification.unlink()
            before = private_state_identity_snapshot(repo)

            observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["control_plane_state"]["verification_mutex"], "absent")
            self.assertEqual(observed["checks"]["mutex_quiescence"], "PASS")
            self.assertEqual(private_state_identity_snapshot(repo), before)

            (core / "locks" / "leases.lock").unlink()
            missing_before = private_state_identity_snapshot(repo)
            missing = observe_control_plane_snapshot(repo, state["task_id"])
            self.assertEqual(missing["checks"]["mutex_quiescence"], "FAIL")
            self.assertIn(
                {"code": "E_STABLE_PAUSE_OPERATION_ACTIVE", "dimension": "operation"},
                missing["issues"],
            )
            self.assertEqual(private_state_identity_snapshot(repo), missing_before)

    def test_held_mutex_is_unsafe_and_observer_never_waits_or_mutates(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, _ = install_lifecycle_fixture(repo)
            lock_path = repo / ".git" / "codex-control-plane-core" / "adoption.lock"
            descriptor = os.open(lock_path, os.O_RDWR | os.O_NONBLOCK)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                before = private_state_identity_snapshot(repo)
                observed = observe_control_plane_snapshot(repo, state["task_id"])
                self.assertEqual(observed["control_plane_state"]["adoption_mutex"], "held")
                self.assertEqual(observed["checks"]["mutex_quiescence"], "FAIL")
                self.assertEqual(private_state_identity_snapshot(repo), before)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def test_resigned_lease_owner_mismatch_is_a_definite_lifecycle_contradiction(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, lease = install_lifecycle_fixture(repo)
            assert lease is not None
            path = (
                repo
                / ".git"
                / "codex-control-plane-core"
                / "leases"
                / f'{lease["lease_id"]}.json'
            )
            forged = json.loads(path.read_text(encoding="utf-8"))
            forged["owner_runtime_digest"] = DIGEST_B
            forged.pop("lease_digest")
            forged["lease_digest"] = contract_digest(forged)
            path.write_text(
                json.dumps(forged, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            before = private_state_identity_snapshot(repo)

            observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["checks"]["lifecycle_binding"], "FAIL")
            self.assertEqual(observed["lifecycle_class"], "contradiction")
            self.assertEqual(private_state_identity_snapshot(repo), before)

    def test_duplicate_task_json_fails_closed_without_serializing_attacker_text(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, _ = install_lifecycle_fixture(repo)
            path = (
                repo
                / ".git"
                / "codex-control-plane-core"
                / "tasks"
                / f'{state["task_id"]}.json'
            )
            payload = path.read_bytes().replace(
                b'"schema_version":1',
                b'"schema_version":1,"schema_version":1',
                1,
            )
            path.write_bytes(payload)
            path.chmod(0o600)
            before = private_state_identity_snapshot(repo)

            observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["checks"]["lifecycle_binding"], "FAIL")
            self.assertEqual(observed["lifecycle"]["task_state"], "unknown")
            self.assertNotIn("schema_version", json.dumps(observed["issues"]))
            self.assertEqual(private_state_identity_snapshot(repo), before)

    def test_misnamed_active_lease_is_a_lifecycle_contradiction(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, lease = install_lifecycle_fixture(repo)
            assert lease is not None
            leases = repo / ".git" / "codex-control-plane-core" / "leases"
            source = leases / f'{lease["lease_id"]}.json'
            source.rename(leases / "misbound.json")

            observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["checks"]["lifecycle_binding"], "FAIL")
            self.assertEqual(observed["lifecycle_class"], "contradiction")

    def test_terminal_generation_requires_the_exact_release_receipt(self) -> None:
        from control_plane.leases import LeaseStore
        from control_plane.stable_pause import observe_control_plane_snapshot
        from control_plane.task_state import CoreTaskStore

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, lease = install_lifecycle_fixture(repo)
            assert lease is not None
            tasks = CoreTaskStore(repo)
            for target in ("verifying", "review_ready", "closed"):
                state = tasks.transition(
                    state["task_id"],
                    target,
                    current_branch=str(state["branch"]),
                    session_id=str(lease["session_id"]),
                )
            leases = LeaseStore(repo)
            leases.release(
                task_id=str(lease["task_id"]),
                revision_id=str(lease["revision_id"]),
                lease_generation=int(lease["lease_generation"]),
                worktree=str(lease["worktree"]),
                branch=str(lease["branch"]),
                session_id=str(lease["session_id"]),
                policy_digest=str(lease["policy_digest"]),
                lease_digest=str(lease["lease_digest"]),
            )
            (leases.receipts / f'{lease["lease_id"]}.json').unlink()

            observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["checks"]["lifecycle_binding"], "FAIL")
            self.assertEqual(observed["lifecycle_class"], "contradiction")


class StablePauseResidueTests(unittest.TestCase):
    def test_unsafe_nested_state_directory_is_closed_residue(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, _ = install_lifecycle_fixture(repo)
            tasks = repo / ".git" / "codex-control-plane-core" / "tasks"
            tasks.chmod(0o755)

            observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["checks"]["lifecycle_binding"], "FAIL")

    def test_linked_worktree_rejects_valid_records_in_the_wrong_state_root(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            primary = make_repository(base / "primary")
            linked = (base / "linked").resolve()
            git(
                primary,
                "worktree",
                "add",
                "-b",
                "codex/stable-pause-linked",
                str(linked),
                "HEAD",
            )
            state, _ = install_lifecycle_fixture(linked)
            worktree_git = Path(
                git(linked, "rev-parse", "--path-format=absolute", "--git-dir")
            ).resolve()
            common_git = Path(
                git(linked, "rev-parse", "--path-format=absolute", "--git-common-dir")
            ).resolve()
            self.assertNotEqual(worktree_git, common_git)
            source = (
                worktree_git
                / "codex-control-plane-core"
                / "tasks"
                / f'{state["task_id"]}.json'
            )
            misplaced = common_git / "codex-control-plane-core" / "tasks"
            misplaced.mkdir(mode=0o700)
            (misplaced / source.name).write_bytes(source.read_bytes())
            (misplaced / source.name).chmod(0o600)

            observed = observe_control_plane_snapshot(linked, state["task_id"])

            self.assertEqual(observed["checks"]["owned_residue"], "FAIL")
            self.assertGreater(observed["control_plane_state"]["residue_count"], 0)

    def test_provisioning_and_unknown_protected_entries_are_closed_residue(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        for relative in (".provisioning-adoption", "unknown-protected-entry"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                repo = make_repository(Path(directory) / "repo")
                state, _ = install_lifecycle_fixture(repo)
                path = repo / ".git" / "codex-control-plane-core" / relative
                if relative.startswith("."):
                    path.mkdir(mode=0o700)
                else:
                    path.write_bytes(b"unexpected")
                    path.chmod(0o600)
                before = private_state_identity_snapshot(repo)

                observed = observe_control_plane_snapshot(repo, state["task_id"])

                self.assertEqual(observed["checks"]["owned_residue"], "FAIL")
                self.assertGreaterEqual(observed["control_plane_state"]["residue_count"], 1)
                self.assertIn(
                    {"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "residue"},
                    observed["issues"],
                )
                self.assertEqual(private_state_identity_snapshot(repo), before)

    def test_global_temporary_files_are_outside_the_owned_inventory(self) -> None:
        from control_plane.stable_pause import observe_control_plane_snapshot

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            repo = make_repository(base / "repo")
            state, _ = install_lifecycle_fixture(repo)
            unrelated = base / "browser-cache.tmp"
            unrelated.write_bytes(b"outside control plane state")
            before = unrelated.read_bytes()

            observed = observe_control_plane_snapshot(repo, state["task_id"])

            self.assertEqual(observed["checks"]["owned_residue"], "PASS")
            self.assertEqual(observed["control_plane_state"]["residue_count"], 0)
            self.assertEqual(unrelated.read_bytes(), before)


class StablePauseCliTests(unittest.TestCase):
    def test_final_observer_emits_valid_active_and_terminal_objects_without_writes(self) -> None:
        from control_plane.stable_pause import observe_stable_pause

        for terminal in (False, True):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                repo = make_repository(Path(directory) / "repo")
                state, _ = install_lifecycle_fixture(repo, terminal=terminal)
                before = private_state_identity_snapshot(repo)

                first = observe_stable_pause(repo, state["task_id"])
                second = observe_stable_pause(repo, state["task_id"])

                self.assertEqual(first, second)
                self.assertEqual(validate_stable_pause_observation(first), first)
                self.assertEqual(
                    first["status"],
                    "SAFE_PAUSE_TERMINAL" if terminal else "SAFE_PAUSE_ACTIVE",
                )
                self.assertLessEqual(len(canonical_json(first).encode("utf-8")), 4096)
                self.assertEqual(private_state_identity_snapshot(repo), before)

    def test_repository_drift_between_snapshots_is_unsafe(self) -> None:
        from control_plane import stable_pause

        with tempfile.TemporaryDirectory() as directory:
            repo = make_repository(Path(directory) / "repo")
            state, _ = install_lifecycle_fixture(repo)
            real = stable_pause.observe_repository_snapshot(repo)
            drifted = dict(real)
            drifted["worktree_digest"] = DIGEST_B
            if drifted == real:
                drifted["worktree_digest"] = DIGEST_A
            before = private_state_identity_snapshot(repo)
            with patch.object(
                stable_pause,
                "observe_repository_snapshot",
                side_effect=(real, drifted),
            ):
                observed = stable_pause.observe_stable_pause(repo, state["task_id"])

            self.assertEqual(observed["status"], "UNSAFE_PAUSE")
            self.assertEqual(observed["checks"]["snapshot_stability"], "FAIL")
            self.assertIn(
                {"code": "E_STABLE_PAUSE_SNAPSHOT_DRIFT", "dimension": "snapshot"},
                observed["issues"],
            )
            self.assertEqual(private_state_identity_snapshot(repo), before)

    def test_checkpoint_handler_calls_observer_once_and_writes_one_canonical_line(self) -> None:
        from contextlib import redirect_stdout
        import io

        from control_plane.cli import main

        value = stable_pause_observation()
        output = io.StringIO()
        with patch(
            "control_plane.stable_pause.observe_stable_pause",
            return_value=value,
        ) as observer, redirect_stdout(output):
            code = main(
                (
                    "task",
                    "checkpoint",
                    "--mode",
                    "stable-pause",
                    "--task-id",
                    "TASK-STABLE-PAUSE-V1",
                    "--json",
                )
            )

        self.assertEqual(code, 0)
        observer.assert_called_once_with(Path.cwd(), "TASK-STABLE-PAUSE-V1")
        self.assertEqual(output.getvalue(), canonical_json(value) + "\n")

    def test_checkpoint_handler_maps_closed_statuses_and_invalid_output(self) -> None:
        from contextlib import redirect_stdout
        import io

        from control_plane.cli import main

        active = stable_pause_observation()
        terminal = stable_pause_observation(
            status="SAFE_PAUSE_TERMINAL",
            task_state="closed",
            lease_state="absent",
        )
        unsafe = stable_pause_observation(status="UNSAFE_PAUSE")
        unsafe["checks"]["owned_residue"] = "FAIL"
        unsafe["issues"] = [
            {"code": "E_STABLE_PAUSE_RESIDUE", "dimension": "residue"}
        ]
        unsafe = resigned(unsafe)
        unknown = stable_pause_observation(status="UNKNOWN")
        unknown["checks"]["repository_identity"] = "UNKNOWN"
        unknown["issues"] = [
            {"code": "E_STABLE_PAUSE_REPOSITORY", "dimension": "repository"}
        ]
        unknown = resigned(unknown)
        cases = ((active, 0), (terminal, 0), (unsafe, 1), (unknown, 2))
        argv = (
            "task",
            "checkpoint",
            "--mode",
            "stable-pause",
            "--task-id",
            "TASK-STABLE-PAUSE-V1",
            "--json",
        )
        for value, expected in cases:
            with self.subTest(status=value["status"]), patch(
                "control_plane.stable_pause.observe_stable_pause",
                return_value=value,
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(main(argv), expected)

        with patch(
            "control_plane.stable_pause.observe_stable_pause",
            return_value={"attacker": "x" * 5000},
        ), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(argv), 2)
        fallback = json.loads(output.getvalue())
        self.assertEqual(fallback["status"], "UNKNOWN")
        self.assertFalse(fallback["authorizes"])
        self.assertNotIn("attacker", output.getvalue())

    def test_unknown_fallback_never_echoes_an_unsafe_or_oversized_path(self) -> None:
        from control_plane.stable_pause import unknown_stable_pause_observation

        for repository in ("unsafe\npath", "/" + "x" * 5000):
            with self.subTest(repository=repository[:32]):
                observed = unknown_stable_pause_observation(
                    repository,
                    "TASK-STABLE-PAUSE-V1",
                )
                self.assertEqual(observed["status"], "UNKNOWN")
                self.assertEqual(observed["repository"]["root"], "/unknown")
                self.assertNotIn(repository, canonical_json(observed))
