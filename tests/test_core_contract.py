from __future__ import annotations

import ast
from pathlib import Path
import unittest

from control_plane import __version__
from control_plane.lockfile import ACTIVE_RUNTIME_MODULES


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_MODULES = {
    "adoption",
    "candidate_receipt",
    "host_bridge",
    "lifecycle",
    "release_source",
    "run_workflow",
}


class CoreContractTests(unittest.TestCase):
    def test_stable_pause_contract_is_closed(self) -> None:
        from control_plane.contracts import validate_stable_pause_observation
        from tests.core_stable_pause_test_support import stable_pause_observation

        value = stable_pause_observation()
        self.assertEqual(validate_stable_pause_observation(value), value)
        value["unexpected"] = False
        with self.assertRaisesRegex(ValueError, "stable pause"):
            validate_stable_pause_observation(value)

    def test_core_candidate_version_and_active_loc_budget_are_exact(self) -> None:
        self.assertEqual(__version__, "3.1.0-core.2")
        active_lines = sum(
            (ROOT / "control_plane" / module).read_text(encoding="utf-8").count("\n")
            + 1
            for module in ACTIVE_RUNTIME_MODULES
        )
        self.assertLessEqual(active_lines, 21_530)
        survey_lines = (ROOT / "control_plane" / "survey.py").read_text(
            encoding="utf-8"
        ).count("\n") + 1
        self.assertLessEqual(survey_lines, 450)

    def test_active_runtime_has_no_advanced_import_edge(self) -> None:
        active_stems = {Path(name).stem for name in ACTIVE_RUNTIME_MODULES}
        self.assertTrue(FORBIDDEN_MODULES.isdisjoint(active_stems))
        for module in ACTIVE_RUNTIME_MODULES:
            path = ROOT / "control_plane" / module
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.rsplit(".", 1)[-1])
            self.assertTrue(
                FORBIDDEN_MODULES.isdisjoint(imported),
                f"{module} imports quarantined modules {sorted(FORBIDDEN_MODULES & imported)}",
            )

    def test_advanced_source_is_not_present_in_active_package(self) -> None:
        for stem in FORBIDDEN_MODULES:
            self.assertFalse((ROOT / "control_plane" / f"{stem}.py").exists(), stem)


if __name__ == "__main__":
    unittest.main()
