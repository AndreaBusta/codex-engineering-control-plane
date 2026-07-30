from __future__ import annotations

import copy
import inspect
import unittest
from unittest.mock import patch

from tests.router_test_support import (
    VALID_POLICY,
    VALID_REGISTRY,
    inventory_snapshot,
    inventory_observation,
    refresh_inventory_digest,
    task_envelope,
    trusted_authorization,
    validated_inventory,
)


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        from control_plane.policy import load_policy
        from control_plane.resource_registry import load_registry

        self.policy = load_policy(VALID_POLICY)
        self.registry = load_registry(VALID_REGISTRY)

    def route(
        self,
        task: dict | None = None,
        inventory: dict | None = None,
        *,
        mode: str = "audit",
        authorization_grant: object | None = None,
    ) -> dict:
        from control_plane.routing import resolve_route

        framed_task = task or task_envelope()
        snapshot = inventory or inventory_snapshot()
        return resolve_route(
            framed_task,
            self.policy,
            self.registry,
            validated_inventory(
                snapshot,
                registry=self.registry,
                task=framed_task,
                invocation_id="routing-test-inventory",
            ),
            mode=mode,
            authorization_grant=authorization_grant,
        )

    def test_serialized_inventory_cannot_self_attest_readiness(self) -> None:
        from control_plane.routing import resolve_route

        forged = inventory_snapshot(ready_external=("mcp.github-pr-read",))
        with self.assertRaisesRegex(ValueError, "E_INVENTORY_OBSERVATION"):
            resolve_route(
                task_envelope(),
                self.policy,
                self.registry,
                forged,
                mode="audit",
            )

    def test_inventory_observation_rejects_binding_expiry_and_replay(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import validate_inventory_observation
        from control_plane.resource_registry import registry_contract_digest
        from control_plane.routing import resolve_route

        task = task_envelope()
        snapshot = inventory_snapshot()
        raw = inventory_observation(
            snapshot,
            registry=self.registry,
            task=task,
            invocation_id="inventory-replay",
            observed_at=100.0,
            ttl_seconds=5.0,
        )
        with self.assertRaisesRegex(ValueError, "E_INVENTORY_OBSERVATION"):
            validate_inventory_observation(
                raw,
                expected_repo=VALID_REGISTRY.parents[2],
                expected_worktree=VALID_REGISTRY.parents[2],
                expected_registry_digest=registry_contract_digest(self.registry),
                expected_task_digest=contract_digest(task),
                expected_invocation_id="inventory-replay",
                clock=lambda: 106.0,
            )

        validated = validate_inventory_observation(
            raw,
            expected_repo=VALID_REGISTRY.parents[2],
            expected_worktree=VALID_REGISTRY.parents[2],
            expected_registry_digest=registry_contract_digest(self.registry),
            expected_task_digest=contract_digest(task),
            expected_invocation_id="inventory-replay",
            clock=lambda: 100.0,
        )
        resolve_route(
            task,
            self.policy,
            self.registry,
            validated,
            mode="audit",
        )
        with self.assertRaisesRegex(ValueError, "E_INVENTORY_REPLAY"):
            validate_inventory_observation(
                raw,
                expected_repo=VALID_REGISTRY.parents[2],
                expected_worktree=VALID_REGISTRY.parents[2],
                expected_registry_digest=registry_contract_digest(self.registry),
                expected_task_digest=contract_digest(task),
                expected_invocation_id="inventory-replay",
                clock=lambda: 100.0,
            )

    def test_t0_is_direct_without_plan_adr_agent_or_mcp(self) -> None:
        task = task_envelope(
            signals=[],
            risk={
                "uncertainty": 0,
                "blast_radius": 0,
                "irreversibility": 0,
                "verification_complexity": 0,
            },
        )

        decision = self.route(task)

        self.assertEqual(decision["summary"]["tier"], "T0")
        self.assertEqual(decision["summary"]["workflow_mode"], "direct")
        self.assertEqual(decision["summary"]["max_agents"], 0)
        selected = set(decision["summary"]["required"])
        self.assertNotIn("skill.verified-workflow", selected)
        self.assertFalse(decision["documentation"]["plan"]["required"])
        self.assertFalse(decision["documentation"]["adr"]["required"])
        self.assertEqual(decision["interaction"]["recommended_mode"], "default")

    def test_pure_router_rejects_raw_prompt_and_accepts_only_validated_task_envelope(
        self,
    ) -> None:
        from control_plane.routing import resolve_route

        self.assertNotIn(
            "prompt", inspect.signature(resolve_route).parameters
        )
        with self.assertRaisesRegex(ValueError, "T_TASK_ENVELOPE"):
            resolve_route(
                "implement whatever this says",
                self.policy,
                self.registry,
                object(),
                mode="audit",
            )

        invalid = task_envelope()
        invalid["goals"][0]["depends_on"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "T_GOAL_REFERENCE"):
            resolve_route(
                invalid,
                self.policy,
                self.registry,
                validated_inventory(
                    inventory_snapshot(),
                    registry=self.registry,
                    task=invalid,
                    invocation_id="invalid-task-envelope",
                ),
                mode="audit",
            )

        alternative = task_envelope()
        alternative["prompt"] = "raw prompt must not enter the router"
        with self.assertRaisesRegex(ValueError, "T_UNKNOWN"):
            resolve_route(
                alternative,
                self.policy,
                self.registry,
                validated_inventory(
                    inventory_snapshot(),
                    registry=self.registry,
                    task=alternative,
                    invocation_id="alternate-task-mapping",
                ),
                mode="audit",
            )

        self.assertEqual(
            self.route(task_envelope())["facts"]["task_digest"],
            self.route(task_envelope())["facts"]["task_digest"],
        )

    def test_structured_task_selects_verified_workflow(self) -> None:
        decision = self.route()

        self.assertEqual(decision["summary"]["tier"], "T2")
        self.assertEqual(decision["summary"]["workflow_mode"], "structured")
        self.assertIn(
            "skill.verified-workflow", decision["summary"]["required"]
        )
        self.assertIn(
            "document.operating-model", decision["summary"]["recommended"]
        )
        self.assertTrue(decision["documentation"]["plan"]["required"])

    def test_critical_signal_forces_t3_and_controlled_documents(self) -> None:
        task = task_envelope(
            signals=["auth", "private_data"],
            risk={
                "uncertainty": 0,
                "blast_radius": 0,
                "irreversibility": 0,
                "verification_complexity": 0,
            },
        )

        decision = self.route(task)

        self.assertEqual(decision["summary"]["tier"], "T3")
        self.assertEqual(decision["summary"]["workflow_mode"], "controlled")
        self.assertTrue(decision["documentation"]["threat_model"]["required"])
        self.assertTrue(decision["documentation"]["rollback"]["required"])
        self.assertFalse(decision["documentation"]["adr"]["required"])
        self.assertFalse(decision["documentation"]["architecture"]["required"])
        self.assertFalse(decision["documentation"]["runbook"]["required"])
        self.assertEqual(decision["summary"]["max_agents"], 2)
        self.assertNotIn(
            "gate.release-proof", decision["required_gates"]
        )

    def test_security_privacy_data_loss_and_irreversibility_force_t3(self) -> None:
        for signal in ("security", "privacy", "data_loss", "irreversible"):
            with self.subTest(signal=signal):
                decision = self.route(
                    task_envelope(
                        signals=[signal],
                        risk={
                            "uncertainty": 0,
                            "blast_radius": 0,
                            "irreversibility": 0,
                            "verification_complexity": 0,
                        },
                    )
                )
                self.assertEqual(decision["summary"]["tier"], "T3")
                self.assertTrue(
                    decision["documentation"]["threat_model"]["required"]
                )
                self.assertTrue(
                    decision["documentation"]["rollback"]["required"]
                )

    def test_migration_requires_workflow_and_durable_documentation(
        self,
    ) -> None:
        decision = self.route(task_envelope(signals=["migration"]))

        self.assertIn(
            "skill.verified-workflow", decision["summary"]["required"]
        )
        self.assertTrue(decision["documentation"]["adr"]["required"])
        self.assertTrue(decision["documentation"]["architecture"]["required"])
        self.assertTrue(decision["documentation"]["runbook"]["required"])

    def test_multiple_independent_goals_mark_prompt_multifront(self) -> None:
        task = task_envelope(
            goals=[
                {
                    "id": "goal-auth",
                    "summary": "Change authentication.",
                    "domains": ["auth"],
                    "depends_on": [],
                },
                {
                    "id": "goal-stats",
                    "summary": "Redesign statistics.",
                    "domains": ["ui"],
                    "depends_on": [],
                },
            ],
            signals=["independent_work", "multi_file"],
        )

        decision = self.route(task)

        self.assertTrue(decision["summary"]["prompt_multifront"])
        self.assertLessEqual(decision["summary"]["max_agents"], 2)
        self.assertEqual(decision["summary"]["execution_strategy"], "sequential")
        self.assertFalse(decision["summary"]["graph_candidate"])

    def test_multifront_enters_router_as_existing_goals_and_dependencies(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest

        task = task_envelope(
            goals=[
                {
                    "id": "contracts",
                    "summary": "Define contracts.",
                    "domains": ["generic"],
                    "depends_on": [],
                },
                {
                    "id": "runtime",
                    "summary": "Integrate the runtime.",
                    "domains": ["generic"],
                    "depends_on": ["contracts"],
                },
                {
                    "id": "tests",
                    "summary": "Verify behavior.",
                    "domains": ["generic"],
                    "depends_on": ["runtime"],
                },
                {
                    "id": "docs",
                    "summary": "Document the boundary.",
                    "domains": ["generic"],
                    "depends_on": ["tests"],
                },
            ],
            signals=["multi_file", "regression_risk"],
        )

        decision = self.route(task)

        self.assertEqual(
            decision["facts"]["task_digest"], contract_digest(task)
        )
        self.assertFalse(decision["summary"]["prompt_multifront"])
        self.assertFalse(decision["summary"]["graph_candidate"])

    def test_t3_multifront_is_only_a_graph_candidate_until_validated(
        self,
    ) -> None:
        task = task_envelope(
            goals=[
                {
                    "id": "goal-auth",
                    "summary": "Change authentication.",
                    "domains": ["auth"],
                    "depends_on": [],
                },
                {
                    "id": "goal-stats",
                    "summary": "Redesign statistics.",
                    "domains": ["ui"],
                    "depends_on": [],
                },
            ],
            signals=["auth", "independent_work"],
        )

        decision = self.route(task)

        self.assertTrue(decision["summary"]["graph_candidate"])
        self.assertTrue(decision["summary"]["graph_validation_required"])
        self.assertEqual(decision["summary"]["execution_strategy"], "sequential")

    def test_required_unknown_resource_is_diagnostic_in_audit_and_blocks_enforce(
        self,
    ) -> None:
        task = task_envelope(
            intent="integrate",
            phase="integrate",
            requested_outcome="integration",
            effects=[
                {"name": "remote_write", "source": "user_explicit"},
            ],
        )
        inventory = inventory_snapshot(unknown=("mcp.github-pr-read",))

        audit = self.route(task, inventory, mode="audit")
        enforce = self.route(task, inventory, mode="enforce")

        self.assertTrue(audit["ok"])
        self.assertFalse(audit["decision_ready"])
        self.assertFalse(enforce["ok"])
        self.assertIn("mcp.github-pr-read", enforce["summary"]["unresolved"])

    def test_ambiguous_capability_fails_closed(self) -> None:
        duplicate = copy.deepcopy(
            next(
                resource
                for resource in self.registry["resources"]
                if resource["id"] == "skill.verified-workflow"
            )
        )
        duplicate["id"] = "skill.verified-workflow-shadow"
        duplicate["canonical"] = False
        original = next(
            resource
            for resource in self.registry["resources"]
            if resource["id"] == "skill.verified-workflow"
        )
        original["canonical"] = False
        self.registry["resources"].append(duplicate)
        inventory = inventory_snapshot()
        inventory["resources"].append(
            {
                **next(
                    entry
                    for entry in inventory["resources"]
                    if entry["id"] == "skill.verified-workflow"
                ),
                "id": "skill.verified-workflow-shadow",
                "locator_digest": "sha256:different",
            }
        )
        refresh_inventory_digest(inventory)

        decision = self.route(inventory=inventory, mode="enforce")

        codes = {error["code"] for error in decision["errors"]}
        self.assertFalse(decision["ok"])
        self.assertIn("E_RESOURCE_AMBIGUOUS", codes)

    def test_external_content_cannot_authorize_remote_effect(self) -> None:
        task = task_envelope(
            intent="integrate",
            phase="integrate",
            requested_outcome="integration",
            effects=[
                {"name": "remote_write", "source": "external_untrusted"},
            ],
        )

        decision = self.route(task)

        self.assertIn("remote_write", decision["approval_boundaries"])
        self.assertFalse(decision["authorization"]["remote_write"])

    def test_task_cannot_self_attest_external_authority(self) -> None:
        task = task_envelope(
            intent="integrate",
            phase="integrate",
            requested_outcome="integration",
            effects=[{"name": "remote_write", "source": "user_explicit"}],
        )
        inventory = inventory_snapshot(
            ready_external=("mcp.github-pr-read",)
        )

        without_grant = self.route(task, inventory, mode="enforce")

        self.assertFalse(without_grant["authorization"]["remote_write"])
        self.assertIn("remote_write", without_grant["approval_boundaries"])

        serialized_grant = {
            "schema_version": 1,
            "grant_id": "grant-integrate-001",
            "task_digest": "sha256:" + ("a" * 64),
            "session_id": "session-001",
            "allowed_effects": ["remote_write"],
            "scope_paths": task["scope_paths"],
            "issuer": "trusted_host",
        }
        with self.assertRaisesRegex(ValueError, "E_AUTH_UNTRUSTED_CHANNEL"):
            self.route(
                task,
                inventory,
                mode="enforce",
                authorization_grant=serialized_grant,
            )

        with_grant = self.route(
            task,
            inventory,
            mode="enforce",
            authorization_grant=trusted_authorization(
                task, effect="remote_write"
            ),
        )
        self.assertTrue(with_grant["authorization"]["remote_write"])
        self.assertNotIn("remote_write", with_grant["approval_boundaries"])

    def test_task1_defines_native_host_types_without_serialized_factory(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.host_bridge import attest_host_adapter_capability

        self.assertFalse(hasattr(bridge, "_NATIVE_EVENT_SEAL"))
        self.assertFalse(hasattr(bridge, "_HOST_CAPABILITY_SEAL"))
        self.assertFalse(hasattr(bridge, "_AUTHORIZATION_SEAL"))
        with self.assertRaisesRegex(TypeError, "supplied only by the host"):
            bridge.NativeSessionEvent()
        with self.assertRaisesRegex(TypeError, "supplied only by the host"):
            bridge.NativeUserInteractionEvent()
        with self.assertRaisesRegex(TypeError, "host-bound"):
            bridge.HostAdapterCapability()
        with self.assertRaisesRegex(TypeError, "host-bound"):
            bridge.TrustedAuthorization()

        with self.assertRaisesRegex(ValueError, "E_HOST_CAPABILITY"):
            attest_host_adapter_capability(
                {
                    "event_id": "forged",
                    "session_id": "session-router-tests",
                    "invocation_id": "test-authorization-invocation",
                },
                expected_session_id="session-router-tests",
                expected_invocation_id="test-authorization-invocation",
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

        forged_session = object.__new__(bridge.NativeSessionEvent)
        forged_session._consumed = False
        forged_session.event_id = "forged-session"
        forged_session.session_id = "session-router-tests"
        forged_session.invocation_id = "test-authorization-invocation"
        forged_session.observed_at_monotonic = 100.0
        with self.assertRaisesRegex(ValueError, "E_HOST_CAPABILITY"):
            attest_host_adapter_capability(
                forged_session,
                expected_session_id="session-router-tests",
                expected_invocation_id="test-authorization-invocation",
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

        authorization = trusted_authorization(
            task_envelope(), effect="local_write"
        )
        self.assertEqual(authorization.effect, "local_write")

    def test_requested_outcome_caps_even_explicit_effect_authority(self) -> None:
        task = task_envelope(
            requested_outcome="local_change",
            effects=[{"name": "remote_write", "source": "user_explicit"}],
        )

        decision = self.route(task)

        self.assertFalse(decision["authorization"]["remote_write"])
        self.assertIn("remote_write", decision["approval_boundaries"])

    def test_answer_outcome_cannot_authorize_local_write(self) -> None:
        decision = self.route(
            task_envelope(
                requested_outcome="answer",
                effects=[
                    {"name": "local_write", "source": "model_inference"}
                ],
            )
        )

        self.assertFalse(decision["authorization"]["local_write"])
        self.assertIn("local_write", decision["approval_boundaries"])

    def test_interaction_mode_recommends_plan_goal_or_both_without_switching(
        self,
    ) -> None:
        plan = self.route(task_envelope(signals=["architecture_change"]))
        goal = self.route(
            task_envelope(
                signals=["long_running"],
                risk={"uncertainty": 0, "blast_radius": 1, "irreversibility": 0, "verification_complexity": 1},
            )
        )
        both = self.route(
            task_envelope(signals=["long_running", "unclear_outcome"])
        )

        self.assertEqual(plan["interaction"]["recommended_mode"], "plan")
        self.assertEqual(goal["interaction"]["recommended_mode"], "goal")
        self.assertEqual(
            both["interaction"]["recommended_mode"], "plan_then_goal"
        )
        self.assertFalse(both["interaction"]["automatic_change"])

    def test_release_and_cross_system_recommend_plan_without_inventing_goal(
        self,
    ) -> None:
        release = self.route(
            task_envelope(
                intent="release",
                phase="release",
                requested_outcome="release",
                signals=["release"],
                effects=[{"name": "release", "source": "user_explicit"}],
            )
        )
        cross_system = self.route(
            task_envelope(signals=["cross_system"])
        )

        self.assertEqual(
            release["interaction"]["recommended_mode"], "plan"
        )
        self.assertEqual(
            cross_system["interaction"]["recommended_mode"], "plan"
        )

    def test_release_requires_workflow_provider_evidence_and_release_gate(
        self,
    ) -> None:
        task = task_envelope(
            intent="release",
            phase="release",
            requested_outcome="release",
            signals=[],
            effects=[{"name": "release", "source": "user_explicit"}],
        )
        inventory = inventory_snapshot(
            unknown=("mcp.release-provider-evidence",)
        )

        decision = self.route(task, inventory, mode="enforce")

        self.assertEqual(decision["summary"]["tier"], "T3")
        self.assertIn(
            "skill.verified-workflow", decision["summary"]["required"]
        )
        self.assertIn(
            "mcp.release-provider-evidence", decision["summary"]["required"]
        )
        self.assertIn("gate.release-proof", decision["required_gates"])
        self.assertFalse(decision["ok"])

    def test_required_gate_must_be_operationally_ready(self) -> None:
        decision = self.route(
            inventory=inventory_snapshot(
                unavailable=("gate.relevant-tests",)
            ),
            mode="enforce",
        )

        self.assertIn("gate.relevant-tests", decision["summary"]["required"])
        self.assertIn(
            "gate.relevant-tests", decision["summary"]["unresolved"]
        )
        self.assertFalse(decision["ok"])

    def test_ready_external_resource_still_requires_task_bound_authority(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest

        task = task_envelope(
            intent="integrate",
            phase="integrate",
            requested_outcome="integration",
            effects=[
                {"name": "network_read", "source": "user_explicit"},
                {"name": "remote_write", "source": "user_explicit"},
            ],
        )
        inventory = inventory_snapshot(ready_external=("mcp.github-pr-read",))
        without_grant = self.route(task, inventory, mode="enforce")
        self.assertIn(
            "E_RESOURCE_APPROVAL",
            {error["code"] for error in without_grant["errors"]},
        )

        with_grant = self.route(
            task,
            inventory,
            mode="audit",
            authorization_grant=trusted_authorization(
                task, effect="network_read"
            ),
        )
        self.assertNotIn(
            "E_RESOURCE_APPROVAL",
            {error["code"] for error in with_grant["errors"]},
        )

    def test_recommended_resources_are_limited_by_tier(self) -> None:
        for index in range(5):
            resource = copy.deepcopy(self.registry["resources"][2])
            resource["id"] = f"document.extra-{index}"
            resource["locator"] = f"repo://docs/extra-{index}.md"
            self.registry["resources"].append(resource)
            self.registry["routes"][0]["recommended_resources"].append(
                resource["id"]
            )
        inventory = inventory_snapshot()
        for index in range(5):
            inventory["resources"].append(
                {
                    **inventory["resources"][1],
                    "id": f"document.extra-{index}",
                    "locator_digest": f"sha256:extra-{index}",
                }
            )
        refresh_inventory_digest(inventory)

        decision = self.route(inventory=inventory)

        self.assertLessEqual(len(decision["summary"]["recommended"]), 2)
        self.assertGreaterEqual(len(decision["summary"]["deferred"]), 4)

    def test_route_is_deterministic_when_registry_order_changes(self) -> None:
        first = self.route()
        self.registry["resources"].reverse()
        self.registry["routes"].reverse()
        second = self.route()

        self.assertEqual(first, second)

    def test_resolver_does_not_use_network_or_subprocess(self) -> None:
        with (
            patch("subprocess.run", side_effect=AssertionError("subprocess")),
            patch("socket.socket", side_effect=AssertionError("network")),
        ):
            decision = self.route()

        self.assertTrue(decision["ok"])

    def test_route_verify_checks_required_forbidden_effects_and_digests(
        self,
    ) -> None:
        from control_plane.lifecycle import create_resource_receipt
        from control_plane.routing import verify_route

        decision = self.route()
        receipt = create_resource_receipt(
            task_id="task-router-001",
            decision_digest=decision["decision_digest"],
            digests={
                "task": decision["facts"]["task_digest"],
                "policy": decision["facts"]["policy_digest"],
                "registry": decision["facts"]["registry_digest"],
                "inventory": decision["facts"]["inventory_digest"],
            },
            used=decision["summary"]["required"],
            resource_digests=decision["selected_resource_digests"],
            omitted=decision["summary"]["recommended"],
            gates=[
                {
                    "gate_id": gate_id,
                    "ok": True,
                    "report_digest": decision["facts"]["task_digest"],
                }
                for gate_id in decision["required_gates"]
            ],
            effects=["local_read", "local_write"],
        )

        result = verify_route(decision, receipt, mode="enforce")

        self.assertTrue(result["ok"], result)
        receipt["used"].append(
            {
                "resource_id": "forbidden.fake",
                "locator_digest": decision["facts"]["task_digest"],
                "evidence_digest": decision["facts"]["task_digest"],
            }
        )
        receipt["observed_effects"].append("release")
        result = verify_route(decision, receipt, mode="enforce")
        self.assertFalse(result["ok"])

    def test_route_verify_fails_closed_for_empty_or_tampered_contracts(
        self,
    ) -> None:
        from control_plane.routing import verify_route

        empty = verify_route({}, {}, mode="enforce")
        self.assertFalse(empty["ok"])
        self.assertFalse(empty["compliant"])
        self.assertIn(
            "E_DECISION_SCHEMA", {error["code"] for error in empty["errors"]}
        )

        decision = self.route()
        decision["summary"]["tier"] = "T0"
        tampered = verify_route(decision, {}, mode="enforce")
        self.assertFalse(tampered["ok"])
        self.assertIn(
            "E_DECISION_DIGEST",
            {error["code"] for error in tampered["errors"]},
        )

    def test_route_verify_cannot_certify_a_decision_that_is_not_ready(
        self,
    ) -> None:
        from control_plane.lifecycle import create_resource_receipt
        from control_plane.routing import verify_route

        task = task_envelope(
            intent="integrate",
            phase="integrate",
            requested_outcome="integration",
            effects=[
                {"name": "remote_write", "source": "user_explicit"},
            ],
        )
        decision = self.route(task, mode="audit")
        receipt = create_resource_receipt(
            task_id="task-router-001",
            decision_digest=decision["decision_digest"],
            digests={
                "task": decision["facts"]["task_digest"],
                "policy": decision["facts"]["policy_digest"],
                "registry": decision["facts"]["registry_digest"],
                "inventory": decision["facts"]["inventory_digest"],
            },
            used=decision["summary"]["required"],
            resource_digests=decision["selected_resource_digests"],
            omitted=decision["summary"]["recommended"],
            gates=[
                {
                    "gate_id": gate_id,
                    "ok": True,
                    "report_digest": decision["facts"]["task_digest"],
                }
                for gate_id in decision["required_gates"]
            ],
            effects=[],
        )

        result = verify_route(decision, receipt, mode="enforce")

        self.assertFalse(decision["decision_ready"])
        self.assertFalse(result["ok"])
        self.assertIn(
            "E_DECISION_NOT_READY",
            {error["code"] for error in result["errors"]},
        )

    def test_route_verify_rejects_unrequested_local_write(self) -> None:
        from control_plane.lifecycle import create_resource_receipt
        from control_plane.routing import verify_route

        decision = self.route(
            task_envelope(
                intent="audit",
                requested_outcome="answer",
                effects=[{"name": "local_write", "source": "model_inference"}],
            ),
            mode="audit",
        )
        receipt = create_resource_receipt(
            task_id="task-router-001",
            decision_digest=decision["decision_digest"],
            digests={
                "task": decision["facts"]["task_digest"],
                "policy": decision["facts"]["policy_digest"],
                "registry": decision["facts"]["registry_digest"],
                "inventory": decision["facts"]["inventory_digest"],
            },
            used=decision["summary"]["required"],
            resource_digests=decision["selected_resource_digests"],
            omitted=decision["summary"]["recommended"],
            gates=[
                {
                    "gate_id": gate_id,
                    "ok": True,
                    "report_digest": decision["facts"]["task_digest"],
                }
                for gate_id in decision["required_gates"]
            ],
            effects=["local_write"],
        )

        result = verify_route(decision, receipt, mode="enforce")

        self.assertFalse(result["ok"])
        self.assertIn(
            "E_RECEIPT_EFFECT",
            {error["code"] for error in result["errors"]},
        )

    def test_inventory_digest_and_ready_state_are_enforced(self) -> None:
        inventory = inventory_snapshot()
        required = next(
            item
            for item in inventory["resources"]
            if item["id"] == "skill.verified-workflow"
        )
        required["ready"] = False
        refresh_inventory_digest(inventory)

        not_ready = self.route(inventory=inventory, mode="enforce")
        self.assertFalse(not_ready["ok"])
        self.assertIn(
            "I_READY_MISMATCH",
            {error["code"] for error in not_ready["errors"]},
        )

        forged = inventory_snapshot()
        forged["snapshot_digest"] = "sha256:" + ("0" * 64)
        forged_result = self.route(inventory=forged, mode="enforce")
        self.assertFalse(forged_result["ok"])
        self.assertIn(
            "I_SNAPSHOT_DIGEST",
            {error["code"] for error in forged_result["errors"]},
        )

    def test_forbidden_resource_dominates_explicit_selection(self) -> None:
        task = task_envelope(
            explicit_resources=["skill.verified-workflow"],
            excluded_resources=["skill.verified-workflow"],
        )

        decision = self.route(task, mode="enforce")

        self.assertFalse(decision["ok"])
        self.assertIn(
            "E_RESOURCE_FORBIDDEN",
            {error["code"] for error in decision["errors"]},
        )

    def test_required_resources_block_instead_of_silently_exceeding_context(
        self,
    ) -> None:
        workflow = next(
            item
            for item in self.registry["resources"]
            if item["id"] == "skill.verified-workflow"
        )
        workflow["context_class"] = "large"

        decision = self.route(mode="enforce")

        self.assertFalse(decision["ok"])
        self.assertIn(
            "E_CONTEXT_BUDGET_REQUIRED",
            {error["code"] for error in decision["errors"]},
        )


if __name__ == "__main__":
    unittest.main()
