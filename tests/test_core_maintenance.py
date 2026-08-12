from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from control_plane.contracts import contract_digest
from control_plane.maintenance import MaintenanceStore, local_candidate_status
from tests.test_core_task_state import make_repo


class CoreMaintenanceTests(unittest.TestCase):
    def test_resigned_lineage_rejects_extra_fields_and_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = MaintenanceStore(repo)
            lineage = store.open(
                lineage_id="MAINT-CLOSED-SCHEMA",
                stable_runtime_digest="sha256:" + "1" * 64,
                candidate_runtime_digest="sha256:" + "2" * 64,
            )
            path = store.lineages / "MAINT-CLOSED-SCHEMA.json"
            for mutation in ({"unexpected_field": "resigned"}, {"authorizes": True}):
                tampered = {**lineage, **mutation}
                tampered.pop("lineage_digest", None)
                tampered["lineage_digest"] = contract_digest(tampered)
                path.write_text(json.dumps(tampered, sort_keys=True) + "\n", encoding="utf-8")
                path.chmod(0o600)
                with self.assertRaisesRegex(ValueError, "E_MAINTENANCE_STATE"):
                    store.structural_failure(
                        lineage_id="MAINT-CLOSED-SCHEMA", reason="E_EXPECTED_REJECTION"
                    )

    def test_only_one_structural_reframe_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = make_repo(Path(directory) / "repo")
            store = MaintenanceStore(repo)
            lineage = store.open(
                lineage_id="MAINT-CORE",
                stable_runtime_digest="sha256:" + "1" * 64,
                candidate_runtime_digest="sha256:" + "2" * 64,
            )
            first = store.structural_failure(
                lineage_id=lineage["lineage_id"], reason="E_FIRST_STRUCTURE"
            )
            self.assertTrue(first["reframe_allowed"])
            second = store.structural_failure(
                lineage_id=lineage["lineage_id"], reason="E_SECOND_STRUCTURE"
            )
            self.assertFalse(second["reframe_allowed"])
            self.assertEqual(second["error_code"], "E_BOOTSTRAP_REFRAME_LIMIT")
            self.assertEqual(second["stable_runtime_digest"], "sha256:" + "1" * 64)
            self.assertEqual(second["created_child"], False)

    def test_candidate_never_self_certifies(self) -> None:
        digest = "sha256:" + "3" * 64
        status = local_candidate_status(
            candidate_runtime_digest=digest,
            verifier_runtime_digest=digest,
        )
        self.assertEqual(status["status"], "GREEN_LOCAL")
        self.assertEqual(status["adoption"], "PENDING_STABLE_ADOPTION")
        self.assertFalse(status["self_certified"])
        self.assertFalse(status["authorizes"])


if __name__ == "__main__":
    unittest.main()
