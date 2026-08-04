from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
SKILL = ROOT / "skills" / "install-control-plane" / "SKILL.md"


class InstallControlPlaneSkillTests(unittest.TestCase):
    def test_skill_has_exact_install_intent_triggers_and_informational_exclusions(
        self,
    ) -> None:
        text = SKILL.read_text(encoding="utf-8")
        frontmatter = re.fullmatch(
            r"---\nname: (?P<name>[^\n]+)\ndescription: (?P<description>[^\n]+)\n---\n(?P<body>.*)",
            text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(frontmatter)
        assert frontmatter is not None
        self.assertEqual(frontmatter.group("name"), "install-control-plane")
        description = frontmatter.group("description")
        for trigger in (
            "instala Control Plane",
            "instalar Control Plane",
            "install Control Plane",
        ):
            self.assertIn(trigger, description)
        self.assertIn("Do not use", description)
        self.assertIn("questions", description)
        self.assertIn("informational mentions", description)

    def test_skill_pins_release_and_keeps_authority_project_local(self) -> None:
        text = SKILL.read_text(encoding="utf-8")

        self.assertIn("v2.1.1", text)
        self.assertIn("AndreaBusta/codex-engineering-control-plane", text)
        self.assertNotRegex(text, r"(?i)use (?:the )?latest version")
        for allowed in (
            "branch",
            "download",
            "adopt plan",
            "adopt apply",
            "adopt verify",
            "adopt rollback",
            "upgrade plan",
            "upgrade apply",
            "/hooks",
        ):
            self.assertIn(allowed, text)
        for forbidden in (
            "commit",
            "push",
            "Pull Request",
            "merge",
            "deploy",
            "release",
            "dependencies",
            "plugins",
            "secrets",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertRegex(
                    text,
                    rf"(?is)(?:does not authorize|never authorizes)[^.]*\b{re.escape(forbidden)}\b",
                )

    def test_skill_fails_closed_on_ambiguous_or_unsafe_inputs(self) -> None:
        text = SKILL.read_text(encoding="utf-8").lower()

        for stop_condition in (
            "dirty target",
            "ambiguous target",
            "unknown version",
            "unverified source",
            "missing initial commit",
            "missing remote",
        ):
            self.assertIn(stop_condition, text)


if __name__ == "__main__":
    unittest.main()
