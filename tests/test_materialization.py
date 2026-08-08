from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
