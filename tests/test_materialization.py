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
        return repo

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
    return repository


class GitStateMaterializationTests(unittest.TestCase):
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

            def fake_flags(path: Path) -> int:
                if path.name == "adoption.lock":
                    return DATALESS_FLAG
                return real_flags(path)

            from control_plane import materialization

            real_flags = materialization._file_flags
            with patch.object(materialization, "_file_flags", fake_flags):
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

    def test_symlinked_git_entry_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repository = _repository(Path(raw))
            outside = Path(raw) / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("x", encoding="utf-8")
            os.symlink(outside, repository / ".git" / "linked")
            observed = inspect_git_state_materialization(repository)
            self.assertEqual(observed.status, "PASS")
            self.assertTrue(observed.ok)

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
