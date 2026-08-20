from __future__ import annotations

from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "control-plane-git" / "SKILL.md"
CONTENT_EQUIVALENCE_GUIDANCE = (
    ROOT / "AGENTS.md",
    SKILL,
    ROOT / "docs" / "engineering" / "21-repository-alignment-and-branch-decisions.md",
    ROOT / "docs" / "engineering" / "22-orientation-and-known-traps.md",
)


class GitSkillContractTests(unittest.TestCase):
    def test_skill_exists_with_frontmatter(self) -> None:
        content = SKILL.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\n"))
        self.assertIn("name: control-plane-git", content)
        self.assertIn("description:", content)

    def test_skill_states_the_blind_spots_and_stays_non_authorizing(self) -> None:
        content = SKILL.read_text(encoding="utf-8")
        for token in (
            "authorizes=false",
            "other clone",
            "squash",
            "dataless",
            "native",
        ):
            self.assertIn(token, content)
        for forbidden in ("adapter", "cross_thread_audit"):
            self.assertNotIn(forbidden, content)

    def test_skill_carries_the_bounded_autonomy_contract(self) -> None:
        content = SKILL.read_text(encoding="utf-8")
        for token in (
            "Mandate",
            "Recoverable stop",
            "Independent review is mandatory",
            "did not write",
        ):
            self.assertIn(token, content)
        for external in ("commit", "push", "pull request", "merge", "release"):
            self.assertIn(external, content.lower())
        for forbidden in ("Autopilot=ON", "daemon", "scheduler", "telemetry"):
            self.assertNotIn(forbidden, content)

    def test_skill_is_small_enough_to_always_load(self) -> None:
        self.assertLessEqual(len(SKILL.read_bytes()), 4_096)

    def test_content_equivalence_guidance_uses_the_whole_diff(self) -> None:
        for path in CONTENT_EQUIVALENCE_GUIDANCE:
            with self.subTest(path=str(path.relative_to(ROOT))):
                content = path.read_text(encoding="utf-8")
                if "git diff --diff-filter=A --name-only" in content:
                    self.fail(
                        "an add-only diff omits modified and deleted branch content"
                    )
                self.assertRegex(content, r"git diff --name-(?:only|status)")

    def test_registry_routes_the_git_capability(self) -> None:
        registry = tomllib.loads(
            (ROOT / ".codex" / "resource-registry.toml").read_text(encoding="utf-8")
        )
        resources = {item["id"]: item for item in registry["resources"]}
        resource = resources["skill.control-plane-git"]
        self.assertEqual(resource["kind"], "skill")
        self.assertTrue(resource["canonical"])
        self.assertEqual(resource["effects"], ["local_read"])
        self.assertEqual(resource["egress"], "none")
        self.assertIn("git.orientation", resource["capabilities"])
        route = next(
            item for item in registry["routes"] if item["id"] == "git-orientation"
        )
        self.assertIn("skill.control-plane-git", route["recommended_resources"])


if __name__ == "__main__":
    unittest.main()
