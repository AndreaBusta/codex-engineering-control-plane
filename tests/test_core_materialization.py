from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from control_plane.materialization import (
    DATALESS_FLAG,
    GitStateMaterialization,
    inspect_git_state_materialization,
)


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
