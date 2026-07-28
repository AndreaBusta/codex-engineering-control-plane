from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class LockfileTests(unittest.TestCase):
    def test_repository_lock_matches_authority_files(self) -> None:
        from control_plane.lockfile import validate_lock

        self.assertEqual(validate_lock(ROOT), [])

    def test_drift_is_detected(self) -> None:
        from control_plane.lockfile import validate_lock

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / ".codex", root / ".codex")
            (root / ".codex" / "project-policy.toml").write_text(
                "\n", encoding="utf-8"
            )

            codes = {issue.code for issue in validate_lock(root)}

        self.assertIn("L_DIGEST", codes)


if __name__ == "__main__":
    unittest.main()
