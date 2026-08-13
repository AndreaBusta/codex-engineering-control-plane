from __future__ import annotations

import copy
import gc
from pathlib import Path
import unittest
import weakref
from unittest.mock import patch

import control_plane.core_types as core_types
from control_plane.contracts import contract_digest
from control_plane.core_types import (
    WorktreeRecord,
    _consume_worktree_inventory,
    _seal_trusted_route_decision,
    observe_current_worktree,
    seal_validated_inventory,
)
from control_plane.policy import (
    _governing_policy_is_issued,
    load_policy,
    seal_governing_policy,
    validate_policy,
)
from control_plane.repository import git_common_dir, worktree_git_dir
from control_plane.risk_sentinel import FAIL, _interaction_view, _policy_observation
from control_plane.routing import compact_route_manifest, verify_route
from tests.core_router_test_support import VALID_POLICY, inventory_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _digest(label: str) -> str:
    return contract_digest({"label": label})


def _governing_policy():
    return seal_governing_policy(
        load_policy(VALID_POLICY),
        runtime_digest=_digest("runtime"),
        lock_digest=_digest("lock"),
        governing_base_commit="a" * 40,
        remote_repository="example/control-plane",
    )


def _route_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "summary": {"required": ["instruction.project-agents"]},
        "interaction": {
            "recommended_mode": "default",
            "reason_codes": ["MODE_BOUNDED"],
        },
        "documentation": {},
        "approval_boundaries": [],
        "required_gates": [],
        "facts": {"task_digest": _digest("task")},
    }
    payload["decision_digest"] = contract_digest(payload)
    return payload


class CoreRuntimeSealTests(unittest.TestCase):
    def test_validated_inventory_rejects_mutation_even_after_digest_rebinding(
        self,
    ) -> None:
        task_digest = _digest("task")
        registry_digest = _digest("registry")
        source = inventory_snapshot()
        inventory = seal_validated_inventory(
            source,
            task_digest=task_digest,
            registry_digest=registry_digest,
        )

        source["project_profile"]["profiles"] = ["source-alias-mutation"]
        observed = inventory._snapshot_for_router(
            expected_task_digest=task_digest,
            expected_registry_digest=registry_digest,
        )
        self.assertEqual(observed["project_profile"]["profiles"], ["generic"])

        inventory._snapshot["project_profile"]["profiles"] = ["post-seal"]
        inventory._snapshot.pop("snapshot_digest")
        rebound_digest = contract_digest(inventory._snapshot)
        inventory._snapshot["snapshot_digest"] = rebound_digest
        inventory.snapshot_digest = rebound_digest

        with self.assertRaisesRegex(
            ValueError,
            "E_INVENTORY_OBSERVATION: validated inventory binding mismatch",
        ):
            inventory._snapshot_for_router(
                expected_task_digest=task_digest,
                expected_registry_digest=registry_digest,
            )

    def test_governing_policy_is_deep_copied_and_slot_or_nested_drift_is_rejected(
        self,
    ) -> None:
        source = load_policy(VALID_POLICY)
        governing = seal_governing_policy(
            source,
            runtime_digest=_digest("runtime"),
            lock_digest=_digest("lock"),
            governing_base_commit="a" * 40,
            remote_repository="example/control-plane",
        )
        source["git"]["base_branch"] = "source-alias-mutation"
        self.assertEqual(governing.policy["git"]["base_branch"], "main")

        governing.policy["git"]["base_branch"] = "post-seal-valid-branch"
        self.assertFalse(_governing_policy_is_issued(governing))
        observed, check = _policy_observation(ROOT, governing)
        self.assertIsNone(observed)
        self.assertEqual(check.status, FAIL)

        second = _governing_policy()
        second.runtime_digest = _digest("replacement-runtime")
        self.assertFalse(_governing_policy_is_issued(second))

    def test_worktree_inventory_retarget_is_rejected_before_repository_subprocess(
        self,
    ) -> None:
        observation = observe_current_worktree(ROOT)
        observation.records = (
            WorktreeRecord(
                worktree="/post-seal/worktree",
                git_dir="/post-seal/git-dir",
            ),
        )
        common_dir = Path(git_common_dir(ROOT))
        git_dir = Path(worktree_git_dir(ROOT))

        from control_plane.hooks import execute_safe_read

        with patch(
            "control_plane.hooks._safe_read_repository_identity",
            return_value=(ROOT, git_dir, common_dir),
        ) as repository_identity:
            with self.assertRaisesRegex(ValueError, "E_SAFE_READ_INVENTORY"):
                execute_safe_read(
                    ("git", "status", "--short"),
                    root=ROOT,
                    worktree_inventory=observation,
                    timeout_seconds=1.0,
                    output_limit_bytes=1024,
                )
        repository_identity.assert_not_called()

    def test_worktree_inventory_returns_one_immutable_consumed_snapshot(self) -> None:
        observation = observe_current_worktree(ROOT)
        snapshot = _consume_worktree_inventory(observation)

        self.assertEqual(snapshot["common_git_dir"], str(git_common_dir(ROOT)))
        self.assertEqual(snapshot["records"][0].worktree, str(ROOT))
        self.assertTrue(observation._consumed)
        self.assertIsNone(_consume_worktree_inventory(observation))

    def test_route_decision_rejects_payload_rebinding_from_every_reader(self) -> None:
        source = _route_payload()
        decision = _seal_trusted_route_decision(source)
        source["summary"]["required"] = ["source-alias-mutation"]
        self.assertEqual(
            decision["summary"]["required"],
            ["instruction.project-agents"],
        )

        decision._payload["summary"]["required"] = ["post-seal-resource"]
        unsigned = {
            key: value
            for key, value in decision._payload.items()
            if key not in {"decision_digest", "command"}
        }
        rebound_digest = contract_digest(unsigned)
        decision._payload["decision_digest"] = rebound_digest
        decision.decision_digest = rebound_digest

        readers = (
            lambda: decision["summary"],
            lambda: tuple(decision),
            lambda: len(decision),
            lambda: decision.payload,
            lambda: compact_route_manifest(decision),
            lambda: verify_route(decision, {}, mode="audit"),
            lambda: _interaction_view(decision),
        )
        for reader in readers:
            with self.subTest(reader=reader):
                with self.assertRaisesRegex(
                    ValueError,
                    "R_ROUTE_DECISION: trusted route binding mismatch",
                ):
                    reader()

    def test_runtime_registry_uses_weak_references_and_cleans_dead_seals(self) -> None:
        source = _route_payload()
        decision = _seal_trusted_route_decision(source)
        object_id = id(decision)
        reference = weakref.ref(decision)
        self.assertIn(object_id, core_types._LIVE_OBJECTS)

        del decision
        gc.collect()

        self.assertIsNone(reference())
        self.assertNotIn(object_id, core_types._LIVE_OBJECTS)


class CoreFocalRegressionTests(unittest.TestCase):
    def test_policy_validation_reports_nonhashable_reasoning_and_strategy_values(
        self,
    ) -> None:
        cases = (
            ("reasoning", "default", "P_REASONING"),
            ("git", "integration_strategy", "P_INTEGRATION"),
        )
        for section, key, expected_code in cases:
            with self.subTest(path=f"{section}.{key}"):
                policy = copy.deepcopy(load_policy(VALID_POLICY))
                policy[section][key] = []
                issues = validate_policy(policy)
                self.assertIn(expected_code, {issue.code for issue in issues})

    def test_missing_route_hint_renders_the_closed_normal_interaction(self) -> None:
        view = _interaction_view(None)

        self.assertEqual(view["mode"], "normal")
        self.assertEqual(view["reason_codes"], ["MODE_BOUNDED"])
        self.assertFalse(view["automatic_change"])


if __name__ == "__main__":
    unittest.main()
