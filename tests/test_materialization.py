from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_plane.materialization import (
    DATALESS_FLAG,
    GitStateMaterialization,
    inspect_git_state_materialization,
)


class MaterializationTests(unittest.TestCase):
    def _repository(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "materialized.txt").write_text("ready\n", encoding="utf-8")
        (repo / "placeholder.txt").write_text("placeholder\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "--", "materialized.txt", "placeholder.txt"],
            check=True,
        )
        return repo.resolve()

    def test_dataless_tracked_file_is_reported_without_reading_contents(self) -> None:
        from control_plane.materialization import (
            DATALESS_FLAG,
            inspect_tracked_materialization,
        )

        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(Path(temporary))

            def flags(path: Path) -> int:
                if path.name == "placeholder.txt":
                    return DATALESS_FLAG
                return 0

            with patch(
                "control_plane.materialization._file_flags", side_effect=flags
            ), patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("materialization inspection read contents"),
            ):
                result = inspect_tracked_materialization(repo)

        self.assertFalse(result.ok)
        self.assertEqual(result.tracked_files, 2)
        self.assertEqual(result.dataless_paths, ("placeholder.txt",))
        self.assertEqual(result.status, "FAIL")

    def test_materialized_repository_passes_with_bounded_inventory(self) -> None:
        from control_plane.materialization import inspect_tracked_materialization

        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(Path(temporary))
            with patch("control_plane.materialization._file_flags", return_value=0):
                result = inspect_tracked_materialization(repo, max_files=2)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.dataless_paths, ())

    def test_inventory_overflow_is_unknown_not_pass(self) -> None:
        from control_plane.materialization import inspect_tracked_materialization

        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(Path(temporary))
            result = inspect_tracked_materialization(repo, max_files=1)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(result.error_code, "E_MATERIALIZATION_LIMIT")

    def test_tracked_deletion_is_not_a_dataless_placeholder(self) -> None:
        from control_plane.materialization import inspect_tracked_materialization

        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(Path(temporary))
            (repo / "placeholder.txt").unlink()
            result = inspect_tracked_materialization(repo)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "PASS")

    def test_inventory_ignores_ambient_path_and_index_redirect(self) -> None:
        from control_plane.materialization import inspect_tracked_materialization

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = self._repository(root)
            empty_index = root / "empty.index"
            index_environment = dict(os.environ)
            index_environment["GIT_INDEX_FILE"] = str(empty_index)
            subprocess.run(
                ["git", "-C", str(repo), "read-tree", "--empty"],
                check=True,
                env=index_environment,
            )
            marker = root / "ambient-git-executed"
            fake_bin = root / "fake-bin"
            fake_bin.mkdir()
            fake_git = fake_bin / "git"
            fake_git.write_text(
                "#!/usr/bin/python3\n"
                "import os, sys\n"
                f"open({str(marker)!r}, 'wb').close()\n"
                "os.execv('/usr/bin/git', ['/usr/bin/git', *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o700)
            with patch.dict(
                os.environ,
                {
                    "PATH": str(fake_bin),
                    "GIT_INDEX_FILE": str(empty_index),
                },
                clear=False,
            ), patch(
                "control_plane.materialization._file_flags", return_value=0
            ):
                result = inspect_tracked_materialization(repo)

            ambient_git_executed = marker.exists()

        self.assertFalse(ambient_git_executed)
        self.assertTrue(result.ok)
        self.assertEqual(result.tracked_files, 2)

    def test_tracked_inventory_never_uses_unbounded_subprocess_run(self) -> None:
        from control_plane.materialization import inspect_tracked_materialization

        with tempfile.TemporaryDirectory() as temporary:
            repo = self._repository(Path(temporary))
            with patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("unbounded subprocess.run was used"),
            ), patch(
                "control_plane.materialization._file_flags", return_value=0
            ):
                result = inspect_tracked_materialization(repo)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.tracked_files, 2)


def _repository(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    subprocess.run(
        ["/usr/bin/git", "init", "--quiet", str(repository)],
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=10,
    )
    return repository.resolve()


class GitStateMaterializationTests(unittest.TestCase):
    def test_clean_git_state_is_proven_without_spawning_git(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            with patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("git was spawned before materialization"),
            ):
                observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "PASS")
        self.assertTrue(observed.ok)

    def test_symlinked_repository_root_is_unknown_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            alias = Path(raw) / "repo-alias"
            alias.symlink_to(repository, target_is_directory=True)
            observed = inspect_git_state_materialization(alias)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_INVENTORY")

    def test_git_state_flags_come_from_descriptor_bound_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            with patch(
                "control_plane.materialization._file_flags",
                side_effect=AssertionError("absolute path flags were consulted"),
            ):
                observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "PASS")
        self.assertTrue(observed.ok)

    def test_clean_git_state_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            observed = inspect_git_state_materialization(repository)
            self.assertIsInstance(observed, GitStateMaterialization)
            self.assertTrue(observed.ok)
            self.assertEqual(observed.status, "PASS")
            self.assertEqual(observed.dataless_files, 0)
            self.assertGreater(observed.scanned_files, 0)
            self.assertEqual(observed.areas, ())
            self.assertIsNone(observed.error_code)

    def test_dataless_git_state_fails_and_names_area_without_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            core = repository / ".git" / "codex-control-plane-core"
            core.mkdir(parents=True)
            (core / "adoption.lock").write_bytes(b"")
            real_flags = None

            def fake_flags(metadata, path: Path) -> int:
                if path.name == "adoption.lock":
                    return DATALESS_FLAG
                return real_flags(metadata, path)

            from control_plane import materialization

            real_flags = materialization._metadata_flags
            with patch.object(materialization, "_metadata_flags", fake_flags):
                observed = inspect_git_state_materialization(repository)
            self.assertFalse(observed.ok)
            self.assertEqual(observed.status, "FAIL")
            self.assertEqual(observed.dataless_files, 1)
            self.assertIn("core_state", observed.areas)
            self.assertEqual(observed.error_code, "E_MATERIALIZATION_DATALESS")
            for area in observed.areas:
                self.assertNotIn("/", area)
                self.assertNotIn("adoption.lock", area)

    def test_limit_returns_unknown_without_partial_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            observed = inspect_git_state_materialization(repository, max_files=1)
            self.assertFalse(observed.ok)
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertTrue(observed.truncated)
            self.assertEqual(observed.dataless_files, 0)
            self.assertEqual(observed.error_code, "E_MATERIALIZATION_LIMIT")

    def test_invalid_limit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            for invalid in (0, -1, True, "5", 100_001):
                with self.assertRaises(ValueError) as observed:
                    inspect_git_state_materialization(repository, max_files=invalid)
                self.assertIn("E_MATERIALIZATION_LIMIT", str(observed.exception))

    def test_git_state_limit_above_governing_cap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            with self.assertRaisesRegex(ValueError, "^E_MATERIALIZATION_LIMIT"):
                inspect_git_state_materialization(repository, max_files=50_001)

    def test_symlinked_git_entry_is_unknown_not_silently_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            outside = Path(raw) / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("x", encoding="utf-8")
            os.symlink(outside, repository / ".git" / "linked")
            observed = inspect_git_state_materialization(repository)
            self.assertEqual(observed.status, "UNKNOWN")
            self.assertFalse(observed.ok)
            self.assertEqual(observed.error_code, "E_MATERIALIZATION_STAT")

    def test_fifo_git_entry_is_unknown_not_silently_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            os.mkfifo(repository / ".git" / "hostile.fifo")
            observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_STAT")

    def test_inventory_limit_counts_directories_as_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            for name in ("empty-a", "empty-b", "empty-c"):
                (repository / ".git" / name).mkdir()
            file_count = sum(
                1 for item in (repository / ".git").rglob("*") if item.is_file()
            )
            observed = inspect_git_state_materialization(
                repository, max_files=file_count
            )

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertTrue(observed.truncated)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_LIMIT")

    def test_directory_depth_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            current = repository / ".git" / "deep"
            for index in range(70):
                current = current / f"d{index}"
                current.mkdir(parents=True)
            observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertTrue(observed.truncated)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_LIMIT")

    def test_git_state_scan_deadline_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            with patch("time.monotonic", side_effect=(0.0, 11.0)):
                observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertTrue(observed.truncated)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_LIMIT")

    def test_git_state_deadline_is_passed_into_topology_discovery(self) -> None:
        from control_plane import materialization

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            real_discovery = materialization._git_state_roots
            with patch.object(
                materialization,
                "_git_state_roots",
                wraps=real_discovery,
            ) as discovery:
                observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "PASS")
        self.assertIn("deadline", discovery.call_args.kwargs)

    def test_object_alternates_are_unknown_before_any_git_observation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            alternate = Path(raw) / "alternate-objects"
            alternate.mkdir()
            (repository / ".git" / "objects" / "info" / "alternates").write_text(
                f"{alternate}\n", encoding="utf-8"
            )
            observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_STAT")

    def test_case_variant_object_alternates_is_also_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            (repository / ".git" / "objects" / "info" / "Alternates").write_text(
                "/unobserved/object-store\n", encoding="utf-8"
            )
            observed = inspect_git_state_materialization(repository)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_STAT")

    def test_git_state_root_identity_is_bound_from_discovery_to_walk(self) -> None:
        from control_plane import materialization

        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            original_git = repository / ".git"
            displaced = repository / ".git.displaced"
            real_discovery = materialization._git_state_roots

            def substitute(*args, **kwargs):
                roots = real_discovery(*args, **kwargs)
                original_git.rename(displaced)
                original_git.mkdir()
                return roots

            try:
                with patch.object(
                    materialization,
                    "_git_state_roots",
                    side_effect=substitute,
                ):
                    observed = inspect_git_state_materialization(repository)
            finally:
                if original_git.is_dir():
                    original_git.rmdir()
                if displaced.exists():
                    displaced.rename(original_git)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_STAT")

    def test_dataless_linked_worktree_control_file_stops_before_git_spawn(self) -> None:
        from control_plane import materialization

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            main = _repository(root)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(main),
                    "commit",
                    "--quiet",
                    "--allow-empty",
                    "-m",
                    "first",
                ],
                env={
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                    "GIT_AUTHOR_NAME": "t",
                    "GIT_AUTHOR_EMAIL": "t@e",
                    "GIT_COMMITTER_NAME": "t",
                    "GIT_COMMITTER_EMAIL": "t@e",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10,
            )
            linked = root / "linked"
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(main),
                    "worktree",
                    "add",
                    "--quiet",
                    str(linked),
                    "-b",
                    "side",
                ],
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=10,
            )
            real_flags = materialization._metadata_flags

            def flags(metadata, path: Path) -> int:
                if path == (linked / ".git").resolve(strict=False):
                    return DATALESS_FLAG
                return real_flags(metadata, path)

            with patch.object(materialization, "_metadata_flags", side_effect=flags), patch.object(
                subprocess,
                "run",
                side_effect=AssertionError("git spawned before control-file proof"),
            ):
                observed = inspect_git_state_materialization(linked)

        self.assertEqual(observed.status, "UNKNOWN")
        self.assertFalse(observed.ok)
        self.assertEqual(observed.error_code, "E_MATERIALIZATION_INVENTORY")

    def test_unreadable_git_subtree_is_unknown_not_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            blocked = repository / ".git" / "codex-control-plane-core"
            blocked.mkdir(parents=True)
            (blocked / "adoption.lock").write_bytes(b"")
            os.chmod(blocked, 0)
            try:
                observed = inspect_git_state_materialization(repository)
            finally:
                os.chmod(blocked, 0o755)
            self.assertEqual(
                observed.status,
                "UNKNOWN",
                "an unreadable subtree must not be reported as a clean scan",
            )
            self.assertEqual(observed.error_code, "E_MATERIALIZATION_STAT")

    def test_linked_worktree_is_not_scanned_twice(self) -> None:
        main = None
        with tempfile.TemporaryDirectory() as raw:
            main = _repository(Path(raw))
            subprocess.run(
                ["/usr/bin/git", "-C", str(main), "commit", "--quiet",
                 "--allow-empty", "-m", "first"],
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin",
                     "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
                     "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=True, timeout=10,
            )
            linked = Path(raw) / "linked"
            subprocess.run(
                ["/usr/bin/git", "-C", str(main), "worktree", "add", "--quiet",
                 str(linked), "-b", "side"],
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=True, timeout=10,
            )
            linked = linked.resolve()
            unique = sum(
                len(files)
                for _, _, files in os.walk(main / ".git", followlinks=False)
            )
            observed = inspect_git_state_materialization(linked)
            self.assertEqual(
                observed.scanned_files,
                unique,
                "a nested worktree git dir must not be walked twice",
            )

    def test_inspection_does_not_mutate_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))

            def snapshot() -> list[tuple[str, int, int]]:
                return sorted(
                    (
                        str(item.relative_to(repository)),
                        item.lstat().st_size,
                        item.lstat().st_mtime_ns,
                    )
                    for item in (repository / ".git").rglob("*")
                    if item.is_file()
                )

            before = snapshot()
            inspect_git_state_materialization(repository)
            self.assertEqual(before, snapshot())



if __name__ == "__main__":
    unittest.main()
