from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


class CoreProjectProfileTests(unittest.TestCase):
    def test_hybrid_markers_are_detected_without_reading_contents_or_symlinks(self) -> None:
        from control_plane.project_profiles import detect_project_profile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "App.xcodeproj").mkdir()
            (root / "AndroidManifest.xml").write_text("", encoding="utf-8")
            (root / "build.gradle.kts").write_text("", encoding="utf-8")
            (root / "vite.config.ts").write_text("", encoding="utf-8")
            outside = root.parent / f"{root.name}-outside.xcworkspace"
            outside.mkdir()
            self.addCleanup(outside.rmdir)
            (root / "linked.xcworkspace").symlink_to(outside, target_is_directory=True)

            profile = detect_project_profile(root)

        self.assertEqual(profile["kind"], "hybrid")
        self.assertEqual(profile["profiles"], ["android", "ios", "web_pwa"])
        self.assertNotIn("linked.xcworkspace", profile["evidence"])

    def test_bounded_scan_reports_incomplete_instead_of_overclaiming(self) -> None:
        from control_plane.project_profiles import detect_project_profile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"file-{index}.txt").write_text("", encoding="utf-8")
            with patch("control_plane.project_profiles.MAX_ENTRIES", 2):
                profile = detect_project_profile(root)

        self.assertTrue(profile["truncated"])
        self.assertEqual(profile["confidence"], "bounded_scan_incomplete")


if __name__ == "__main__":
    unittest.main()
