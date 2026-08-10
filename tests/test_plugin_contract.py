from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "control-plane"
MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
PACKAGED_SKILL = PLUGIN / "skills" / "control-plane-run" / "SKILL.md"
CANONICAL_SKILL = ROOT / "skills" / "control-plane-run" / "SKILL.md"


EXPECTED_MANIFEST = {
    "name": "control-plane",
    "version": "3.0.0",
    "description": "Native-governed Control Plane workflows for Codex.",
    "author": {"name": "Codex Engineering Control Plane"},
    "skills": "./skills/",
    "interface": {
        "displayName": "Control Plane",
        "shortDescription": "Run bounded engineering without internal prompts.",
        "longDescription": (
            "Routes verified work, governs native tasks, and preserves "
            "host-only authority."
        ),
        "developerName": "Codex Engineering Control Plane",
        "category": "Productivity",
        "capabilities": [],
        "defaultPrompt": (
            "Use $control-plane:control-plane-run to finish this engineering "
            "task safely."
        ),
    },
}


class ControlPlanePluginContractTests(unittest.TestCase):
    def test_manifest_is_the_closed_thin_v3_candidate(self) -> None:
        self.assertTrue(MANIFEST.is_file(), "control-plane plugin manifest is missing")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest, EXPECTED_MANIFEST)
        self.assertIn(
            "$control-plane:control-plane-run",
            manifest["interface"]["defaultPrompt"],
        )
        lowered = json.dumps(manifest, sort_keys=True).lower()
        for forbidden in ("hooks", "mcpservers", "apps", "credential", "token"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_packaged_skill_is_byte_identical_to_the_canonical_skill(self) -> None:
        self.assertTrue(PACKAGED_SKILL.is_file(), "packaged skill is missing")
        self.assertEqual(PACKAGED_SKILL.read_bytes(), CANONICAL_SKILL.read_bytes())

    def test_plugin_contains_no_unproved_component(self) -> None:
        self.assertTrue(PLUGIN.is_dir(), "control-plane plugin is missing")
        files = {
            path.relative_to(PLUGIN).as_posix()
            for path in PLUGIN.rglob("*")
            if path.is_file()
        }
        self.assertEqual(
            files,
            {
                ".codex-plugin/plugin.json",
                "skills/control-plane-run/SKILL.md",
            },
        )
        for forbidden in ("hooks", "scripts", "assets"):
            self.assertFalse((PLUGIN / forbidden).exists())
        self.assertFalse((PLUGIN / ".mcp.json").exists())
        self.assertFalse((PLUGIN / ".app.json").exists())


if __name__ == "__main__":
    unittest.main()
