from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "control-plane-run" / "SKILL.md"
OPENAI_YAML = ROOT / "skills" / "control-plane-run" / "agents" / "openai.yaml"


class ControlPlaneRunSkillTests(unittest.TestCase):
    def test_skill_exposes_the_bounded_local_run_protocol(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("---\nname: control-plane-run\n"))
        for required in (
            "run prepare",
            "run verify",
            "run status",
            "run block",
            "--task-id <id> --reason <código>",
            "PLANIFICANDO",
            "TRABAJANDO",
            "VERIFICANDO",
            "PR LISTA",
            "BLOCKED",
            "tres ejecuciones totales",
            "UNKNOWN",
            "autorización nativa",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertNotIn("--decision", text)
        self.assertNotIn("force-push", text.lower())

    def test_skill_has_ui_metadata_without_external_dependencies(self) -> None:
        metadata = OPENAI_YAML.read_text(encoding="utf-8")

        self.assertIn('display_name: "Control Plane Run"', metadata)
        self.assertIn("$control-plane-run", metadata)
        self.assertNotIn("dependencies:", metadata)


if __name__ == "__main__":
    unittest.main()
