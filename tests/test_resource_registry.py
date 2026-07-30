from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path

from tests.router_test_support import VALID_POLICY, VALID_REGISTRY


ROOT = Path(__file__).parents[1]


class ResourceRegistryTests(unittest.TestCase):
    def test_task_framer_registry_declares_its_real_markdown_output(
        self,
    ) -> None:
        from control_plane.resource_registry import load_registry

        registry = load_registry(
            ROOT / ".codex" / "resource-registry.toml"
        )
        resource = next(
            item
            for item in registry["resources"]
            if item["id"] == "skill.task-framer"
        )

        self.assertEqual(resource["locator"], "user-skill://task-framer")
        self.assertEqual(resource["capabilities"], ["task.framing"])
        self.assertTrue(resource["canonical"])
        self.assertEqual(resource["output_contract"], "markdown")

    def test_valid_registry_loads_and_validates(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        registry = load_registry(VALID_REGISTRY)

        self.assertEqual(registry["schema_version"], 1)
        self.assertEqual(validate_registry(registry), [])

    def test_unknown_registry_key_is_rejected(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        registry = load_registry(VALID_REGISTRY)
        registry["resources"][0]["command"] = "dangerous"

        codes = {issue.code for issue in validate_registry(registry)}

        self.assertIn("R_UNKNOWN", codes)

    def test_scalar_enums_budgets_and_route_shapes_are_strict(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        registry = load_registry(VALID_REGISTRY)
        registry["router"]["default_mode"] = "permissive"
        registry["budgets"]["T2"]["max_agents"] = "two"
        registry["resources"][0]["canonical"] = "yes"
        registry["resources"][0]["selection"] = "sometimes"
        registry["resources"][0]["priority"] = "high"
        registry["routes"][0]["tiers"] = ["T9"]
        registry["routes"][0]["phases"] = "implement"

        codes = {issue.code for issue in validate_registry(registry)}

        self.assertIn("R_ROUTER", codes)
        self.assertIn("R_BUDGET", codes)
        self.assertIn("R_TYPE", codes)
        self.assertIn("R_ENUM", codes)
        self.assertIn("R_ROUTE", codes)

    def test_duplicate_and_non_ascii_ids_are_rejected(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        registry = load_registry(VALID_REGISTRY)
        duplicate = copy.deepcopy(registry["resources"][0])
        registry["resources"].append(duplicate)
        registry["resources"][1]["id"] = "skill.verifiеd-workflow"

        codes = {issue.code for issue in validate_registry(registry)}

        self.assertIn("R_DUPLICATE_ID", codes)
        self.assertIn("R_RESOURCE_ID", codes)

    def test_http_locator_and_path_traversal_are_rejected(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        registry = load_registry(VALID_REGISTRY)
        registry["resources"][0]["locator"] = "https://example.test/run"
        registry["resources"][1]["locator"] = "repo://../outside.md"

        codes = {issue.code for issue in validate_registry(registry)}

        self.assertIn("R_LOCATOR", codes)

    def test_user_skill_locator_rejects_traversal_and_path_components(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        for locator in (
            "user-skill://../outside",
            "user-skill:///tmp/outside",
            "user-skill://nested/skill",
        ):
            with self.subTest(locator=locator):
                registry = load_registry(VALID_REGISTRY)
                registry["resources"][1]["locator"] = locator
                codes = {issue.code for issue in validate_registry(registry)}
                self.assertIn("R_LOCATOR", codes)

    def test_missing_dependency_and_cycle_are_rejected(self) -> None:
        from control_plane.resource_registry import load_registry, validate_registry

        registry = load_registry(VALID_REGISTRY)
        registry["resources"][0]["requires"] = ["missing.resource"]
        registry["resources"][1]["requires"] = ["document.operating-model"]
        registry["resources"][2]["requires"] = ["skill.verified-workflow"]

        codes = {issue.code for issue in validate_registry(registry)}

        self.assertIn("R_DEPENDENCY_MISSING", codes)
        self.assertIn("R_DEPENDENCY_CYCLE", codes)

    def test_policy_gate_references_require_registered_gate_aliases(self) -> None:
        from control_plane.policy import load_policy
        from control_plane.resource_registry import (
            load_registry,
            validate_policy_references,
        )

        policy = load_policy(VALID_POLICY)
        registry = load_registry(VALID_REGISTRY)
        policy["gates"]["T0"]["required"].append("unknown_gate")

        codes = {
            issue.code for issue in validate_policy_references(policy, registry)
        }

        self.assertIn("R_GATE_UNRESOLVED", codes)

    def test_inventory_rejects_repo_symlink_escape(self) -> None:
        from control_plane.resource_registry import build_inventory, load_registry

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "docs").mkdir()
            outside = root.parent / f"{root.name}-outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            self.addCleanup(lambda: outside.unlink(missing_ok=True))
            os.symlink(outside, root / "docs" / "escape.md")
            registry = load_registry(VALID_REGISTRY)
            registry["resources"][0]["locator"] = "repo://docs/escape.md"

            inventory = build_inventory(registry, root)

        entry = next(
            item
            for item in inventory["resources"]
            if item["id"] == "instruction.project-agents"
        )
        self.assertEqual(entry["availability"], "invalid")
        self.assertIn("R_SYMLINK_ESCAPE", entry["reason_codes"])

    def test_inventory_is_metadata_only_and_deterministic(self) -> None:
        from control_plane.resource_registry import (
            build_inventory,
            load_registry,
            validate_inventory,
        )

        registry = load_registry(VALID_REGISTRY)
        first = build_inventory(registry, Path(__file__).parents[1])
        second = build_inventory(registry, Path(__file__).parents[1])

        self.assertEqual(first["snapshot_digest"], second["snapshot_digest"])
        self.assertEqual(validate_inventory(registry, first), [])
        self.assertNotIn("content", str(first))
        self.assertNotIn("secret", str(first).lower())

    def test_inventory_rejects_forged_digest_duplicates_and_fake_readiness(
        self,
    ) -> None:
        import copy
        from control_plane.resource_registry import (
            load_registry,
            validate_inventory,
        )
        from tests.router_test_support import inventory_snapshot

        registry = load_registry(VALID_REGISTRY)
        inventory = inventory_snapshot()
        entry = next(
            item
            for item in inventory["resources"]
            if item["id"] == "mcp.github-pr-read"
        )
        entry["ready"] = True
        inventory["resources"].append(copy.deepcopy(entry))
        inventory["snapshot_digest"] = "sha256:" + ("0" * 64)

        codes = {
            issue.code for issue in validate_inventory(registry, inventory)
        }
        self.assertTrue(
            {"I_DUPLICATE", "I_READY_MISMATCH", "I_SNAPSHOT_DIGEST"}.issubset(
                codes
            )
        )


if __name__ == "__main__":
    unittest.main()
