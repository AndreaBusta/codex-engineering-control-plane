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
        host_capability: object | None = None,
        clarification_request: object | None = None,
        authorization: object | None = None,
        assumption: object | None = None,
        clarification_resolution: object | None = None,
        irreversible_confirmation: object | None = None,
    ) -> dict:
        import control_plane.host_bridge as bridge
        from control_plane.routing import resolve_route

        framed_task = task or task_envelope()
        snapshot = inventory or inventory_snapshot()
        if host_capability is None:
            host_capability = bridge.HOST_ADAPTER_UNAVAILABLE
        suppress_default_request = clarification_request is False
        if suppress_default_request:
            clarification_request = None
        if (
            clarification_request is None
            and not suppress_default_request
            and int(framed_task["risk"]["uncertainty"]) == 2
        ):
            clarification_request = self.validated_request(
                framed_task, repository_status="resolved"
            )
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
            host_capability=host_capability,
            clarification_request=clarification_request,
            authorization=authorization,
            assumption=assumption,
            clarification_resolution=clarification_resolution,
            irreversible_confirmation=irreversible_confirmation,
        )

    def host_capability(
        self,
        *,
        invocation_id: str = "routing-host-capability",
        clock=lambda: 100.0,
        ttl_seconds: float = 30,
    ):
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import native_session_event

        event = native_session_event(
            event_id=f"event-{invocation_id}",
            session_id="session-routing-tests",
            invocation_id=invocation_id,
            observed_at_monotonic=100.0,
        )
        return bridge.attest_host_adapter_capability(
            event,
            expected_session_id="session-routing-tests",
            expected_invocation_id=invocation_id,
            clock=clock,
            ttl_seconds=ttl_seconds,
        )

    def validated_request(
        self,
        task: dict,
        *,
        repository_status: str,
        issue_kind: str = "clarification",
        invocation_id: str = "routing-request",
    ):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        evidence_digest = (
            None
            if repository_status == "not_checked"
            else contract_digest(
                {
                    "repository_status": repository_status,
                    "task_id": task["task_id"],
                }
            )
        )
        payload = {
            "schema_version": 1,
            "request_id": f"request-{invocation_id}",
            "task_digest": contract_digest(task),
            "session_id": "session-routing-tests",
            "issue_kind": issue_kind,
            "severity": (
                "low",
                "medium",
                "high",
                "critical",
            )[int(task["risk"]["uncertainty"])],
            "question_digest": contract_digest(
                {"question": task["task_id"], "kind": issue_kind}
            ),
            "presentation_digest": contract_digest(
                {"presentation": task["task_id"], "kind": issue_kind}
            ),
            "repository_check": {
                "status": repository_status,
                "evidence_digest": evidence_digest,
            },
            "option_ids": ["preserve-current", "change-contract"],
            "recommended_option_id": "preserve-current",
        }
        request = object.__new__(bridge.ValidatedClarificationRequest)
        request._consumed = False
        request.payload = copy.deepcopy(payload)
        request.request_digest = contract_digest(payload)
        request.task_digest = contract_digest(task)
        request.session_id = "session-routing-tests"
        request.invocation_id = invocation_id
        request.provenance = "trusted_host"
        bridge._register_runtime_host_object(
            request, "validated_clarification_request"
        )
        return request

    def validated_assumption(self, task: dict, request: object):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        assumption = object.__new__(bridge.ValidatedAssumption)
        assumption.payload = {
            "schema_version": 1,
            "request_digest": request.request_digest,
            "task_digest": contract_digest(task),
            "selected_option_id": "preserve-current",
            "statement_digest": contract_digest(
                {"assumption": "preserve-current"}
            ),
        }
        assumption.provenance = "model_inference"
        bridge._register_runtime_host_object(
            assumption, "validated_assumption"
        )
        return assumption

    def trusted_resolution(self, task: dict, request: object):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest

        resolution = object.__new__(bridge.TrustedInteraction)
        resolution._consumed = False
        resolution.payload = {
            "schema_version": 1,
            "resolution_id": "resolution-routing-tests",
            "request_digest": request.request_digest,
            "task_digest": contract_digest(task),
            "session_id": request.session_id,
            "selected_option_id": "preserve-current",
            "response_digest": contract_digest({"response": "preserve"}),
        }
        resolution.request_digest = request.request_digest
        resolution.task_digest = contract_digest(task)
        resolution.session_id = request.session_id
        resolution.invocation_id = request.invocation_id
        resolution.freshness_deadline = 130.0
        bridge._register_runtime_host_object(
            resolution, "trusted_interaction"
        )
        return resolution

    def bound_authorization(
        self,
        task: dict,
        *,
        effect: str,
        subject_digest: str,
        invocation_id: str,
    ):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from tests.host_adapter_test_support import (
            native_session_event,
            native_user_interaction_event,
        )

        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id=f"session-{invocation_id}",
                session_id="session-routing-tests",
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id="session-routing-tests",
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        return bridge.frame_effect_authorization(
            native_user_interaction_event(
                event_id=f"user-{invocation_id}",
                session_id="session-routing-tests",
                invocation_id=invocation_id,
                task_digest=contract_digest(task),
                subject_digest=subject_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=capability,
            task_digest=contract_digest(task),
            session_id="session-routing-tests",
            repository_identity=VALID_REGISTRY.parents[2],
            worktree_identity=VALID_REGISTRY.parents[2],
            branch="codex/test",
            expected_head="a" * 40,
            subject_digest=subject_digest,
            scope_paths=tuple(task["scope_paths"]),
            effect=effect,
            operation_nonce=f"operation-{invocation_id}",
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )

    def bound_confirmation(
        self,
        task: dict,
        *,
        authorization: object,
        subject_digest: str,
    ):
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from tests.host_adapter_test_support import (
            native_session_event,
            native_user_interaction_event,
        )

        invocation_id = authorization.invocation_id
        capability = bridge.attest_host_adapter_capability(
            native_session_event(
                event_id=f"confirmation-session-{invocation_id}",
                session_id=authorization.session_id,
                invocation_id=invocation_id,
                observed_at_monotonic=100.0,
            ),
            expected_session_id=authorization.session_id,
            expected_invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        request = {
            "schema_version": 1,
            "confirmation_id": "confirmation-routing-tests",
            "request_digest": subject_digest,
            "task_digest": contract_digest(task),
            "session_id": authorization.session_id,
            "scope_paths": list(task["scope_paths"]),
            "effect": authorization.effect,
            "consequence_digest": contract_digest(
                {"consequence": "irreversible"}
            ),
        }
        return bridge.frame_irreversible_confirmation(
            request,
            native_user_event=native_user_interaction_event(
                event_id=f"confirmation-user-{invocation_id}",
                session_id=authorization.session_id,
                invocation_id=invocation_id,
                task_digest=contract_digest(task),
                subject_digest=subject_digest,
                observed_at_monotonic=100.0,
            ),
            host_capability=capability,
            repository_identity=VALID_REGISTRY.parents[2],
            worktree_identity=VALID_REGISTRY.parents[2],
            branch=authorization.branch,
            expected_head=authorization.expected_head,
            subject_digest=subject_digest,
            authorization_id=authorization.authorization_id,
            operation_nonce=authorization.operation_nonce,
            invocation_id=invocation_id,
            clock=lambda: 100.0,
            ttl_seconds=30,
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
                host_capability=object(),
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
            host_capability=__import__(
                "control_plane.host_bridge",
                fromlist=["HOST_ADAPTER_UNAVAILABLE"],
            ).HOST_ADAPTER_UNAVAILABLE,
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
                host_capability=object(),
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
                host_capability=object(),
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
                host_capability=object(),
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
                authorization=serialized_grant,
            )

        with_grant = self.route(
            task,
            inventory,
            mode="enforce",
            authorization=trusted_authorization(
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

    def test_low_uncertainty_is_autonomous_without_new_authority(self) -> None:
        task = task_envelope(
            risk={
                "uncertainty": 0,
                "blast_radius": 2,
                "irreversibility": 1,
                "verification_complexity": 2,
            },
            effects=[
                {"name": "local_read", "source": "model_inference"},
                {"name": "remote_write", "source": "user_explicit"},
            ],
        )

        decision = self.route(task, clarification_request=False)
        gate = decision["interaction"]["clarification_gate"]

        self.assertEqual(gate["status"], "autonomous")
        self.assertTrue(gate["decision_ready"])
        self.assertFalse(decision["authorization"]["remote_write"])
        self.assertIn("remote_write", decision["approval_boundaries"])

    def test_medium_requires_a_visible_task_bound_assumption(self) -> None:
        task = task_envelope(
            risk={
                "uncertainty": 1,
                "blast_radius": 2,
                "irreversibility": 1,
                "verification_complexity": 2,
            }
        )
        request = self.validated_request(
            task, repository_status="not_checked"
        )

        pending = self.route(
            task,
            host_capability=self.host_capability(
                invocation_id="medium-pending"
            ),
            clarification_request=request,
        )
        resolved = self.route(
            task,
            host_capability=self.host_capability(
                invocation_id="medium-resolved"
            ),
            clarification_request=request,
            assumption=self.validated_assumption(task, request),
        )

        self.assertEqual(
            pending["interaction"]["clarification_gate"]["status"],
            "assumption_required",
        )
        self.assertEqual(
            resolved["interaction"]["clarification_gate"]["status"],
            "resolved",
        )

    def test_high_inspects_before_asking_and_blocks_write_while_unresolved(
        self,
    ) -> None:
        task = task_envelope()
        request = self.validated_request(
            task, repository_status="unresolved"
        )

        missing = self.route(
            task,
            host_capability=self.host_capability(
                invocation_id="high-request-required"
            ),
            clarification_request=False,
        )
        asking = self.route(
            task,
            host_capability=self.host_capability(
                invocation_id="high-asking"
            ),
            clarification_request=request,
        )

        self.assertEqual(
            missing["interaction"]["clarification_gate"]["status"],
            "clarification_request_required",
        )
        self.assertEqual(
            asking["interaction"]["clarification_gate"]["status"],
            "ask_user",
        )
        self.assertFalse(asking["decision_ready"])
        self.assertIn(
            "local_write",
            asking["interaction"]["clarification_gate"]["blocked_effects"],
        )

    def test_repository_evidence_resolves_fact_not_decision_approval(
        self,
    ) -> None:
        task = task_envelope()
        factual = self.validated_request(
            task,
            repository_status="resolved",
            invocation_id="factual-resolved",
        )
        decision_request = self.validated_request(
            task,
            repository_status="resolved",
            issue_kind="decision_approval",
            invocation_id="decision-pending",
        )

        factual_route = self.route(
            task, clarification_request=factual
        )
        decision_route = self.route(
            task, clarification_request=decision_request
        )

        self.assertEqual(
            factual_route["interaction"]["clarification_gate"]["status"],
            "resolved",
        )
        self.assertEqual(
            decision_route["interaction"]["clarification_gate"]["status"],
            "ask_user",
        )

    def test_critical_requires_reframed_task_and_blocks_writes(self) -> None:
        task = task_envelope(
            risk={
                "uncertainty": 3,
                "blast_radius": 2,
                "irreversibility": 1,
                "verification_complexity": 2,
            }
        )

        decision = self.route(task, clarification_request=False)
        gate = decision["interaction"]["clarification_gate"]

        self.assertEqual(gate["status"], "blocked")
        self.assertIn("C_REFRAME_REQUIRED", gate["reason_codes"])
        self.assertFalse(decision["decision_ready"])
        self.assertIn("local_write", gate["blocked_effects"])

    def test_clarification_authorization_and_confirmation_are_not_interchangeable(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest

        task = task_envelope(
            risk={
                "uncertainty": 0,
                "blast_radius": 2,
                "irreversibility": 3,
                "verification_complexity": 2,
            }
        )
        subject_digest = contract_digest(task)
        authorization = self.bound_authorization(
            task,
            effect="local_write",
            subject_digest=subject_digest,
            invocation_id="irreversible-routing",
        )
        without_authorization = self.route(
            task, clarification_request=False
        )
        without_confirmation = self.route(
            task,
            clarification_request=False,
            authorization=authorization,
        )
        confirmation = self.bound_confirmation(
            task,
            authorization=authorization,
            subject_digest=subject_digest,
        )
        complete = self.route(
            task,
            clarification_request=False,
            authorization=authorization,
            irreversible_confirmation=confirmation,
        )

        self.assertEqual(
            without_authorization["interaction"]["clarification_gate"][
                "status"
            ],
            "authorization_required",
        )
        self.assertEqual(
            without_confirmation["interaction"]["clarification_gate"][
                "status"
            ],
            "confirmation_required",
        )
        self.assertEqual(
            complete["interaction"]["clarification_gate"]["status"],
            "autonomous",
        )

    def test_serialized_authorization_never_changes_route_authority(self) -> None:
        task = task_envelope(
            risk={
                "uncertainty": 0,
                "blast_radius": 2,
                "irreversibility": 1,
                "verification_complexity": 2,
            },
            intent="integrate",
            phase="integrate",
            requested_outcome="integration",
            effects=[
                {"name": "remote_write", "source": "user_explicit"},
            ],
        )
        forged = {
            "schema_version": 1,
            "task_digest": "sha256:" + ("1" * 64),
            "effect": "remote_write",
        }

        with self.assertRaisesRegex(ValueError, "E_AUTH_UNTRUSTED_CHANNEL"):
            self.route(
                task,
                clarification_request=False,
                authorization=forged,
            )

    def test_raw_request_mapping_cannot_enter_router_even_if_byte_identical(
        self,
    ) -> None:
        task = task_envelope()
        request = self.validated_request(
            task, repository_status="resolved"
        )

        with self.assertRaisesRegex(ValueError, "C_UNTRUSTED_REQUEST"):
            self.route(
                task,
                clarification_request=copy.deepcopy(request.payload),
            )

    def test_missing_host_capability_never_invents_clarification_request(
        self,
    ) -> None:
        decision = self.route(
            task_envelope(),
            host_capability=__import__(
                "control_plane.host_bridge",
                fromlist=["HOST_ADAPTER_UNAVAILABLE"],
            ).HOST_ADAPTER_UNAVAILABLE,
            clarification_request=False,
        )
        gate = decision["interaction"]["clarification_gate"]

        self.assertEqual(gate["status"], "pending_host_capability")
        self.assertNotIn("question", gate)
        self.assertNotIn("options", gate)

    def test_router_requires_typed_host_capability_state(self) -> None:
        import control_plane.host_bridge as bridge

        task = task_envelope()
        ready = self.route(
            task,
            host_capability=self.host_capability(
                invocation_id="typed-host-ready"
            ),
            clarification_request=False,
        )
        unavailable = self.route(
            task,
            host_capability=bridge.HOST_ADAPTER_UNAVAILABLE,
            clarification_request=False,
        )

        self.assertEqual(
            ready["interaction"]["clarification_gate"]["status"],
            "clarification_request_required",
        )
        self.assertEqual(
            unavailable["interaction"]["clarification_gate"]["status"],
            "pending_host_capability",
        )
        for forged in (
            {},
            "ready",
            True,
            object.__new__(bridge.HostAdapterUnavailable),
        ):
            with self.subTest(forged=forged):
                with self.assertRaisesRegex(
                    ValueError, "C_UNTRUSTED_HOST_CAPABILITY"
                ):
                    self.route(
                        task,
                        host_capability=forged,
                        clarification_request=False,
                    )

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
            authorization=trusted_authorization(
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

    def resource_observation(
        self,
        task: dict,
        decision: dict,
        inventory: object,
        *,
        effects: tuple[str, ...],
        invocation_id: str,
        clock=lambda: 100.0,
        capability_ttl_seconds: float = 30,
        validation_clock=None,
    ):
        import control_plane.host_bridge as bridge
        from tests.host_adapter_test_support import _register_native_object

        capability = self.host_capability(
            invocation_id=invocation_id,
            clock=clock,
            ttl_seconds=capability_ttl_seconds,
        )
        context = bridge.build_trusted_route_context(
            task=task,
            decision=decision,
            inventory=inventory,
            expected_repository=VALID_REGISTRY.parents[2],
            expected_worktree=VALID_REGISTRY.parents[2],
            expected_branch="codex/test",
            expected_head="a" * 40,
            session_id="session-routing-tests",
            invocation_id=invocation_id,
            host_capability=capability,
            clock=clock,
            ttl_seconds=30,
        )
        events = []
        for ordinal, resource_id in enumerate(
            decision["summary"]["required"]
        ):
            event = object.__new__(bridge.NativeResourceUseEvent)
            event._consumed = False
            event.event_id = f"resource-{invocation_id}-{ordinal}"
            event.resource_id = resource_id
            event.locator_digest = decision[
                "selected_resource_digests"
            ][resource_id]
            event.operation = "read"
            event.ordinal = ordinal
            event.observed_effects = effects if ordinal == 0 else ()
            event.tool_use_id = f"tool-{invocation_id}"
            event.task_digest = context.task_digest
            event.route_digest = context.route_digest
            event.repository_identity = context.repository_identity
            event.worktree_identity = context.worktree_identity
            event.branch = context.branch
            event.head = context.head
            event.session_id = context.session_id
            event.invocation_id = context.invocation_id
            event.context_nonce = context.context_nonce
            event.observed_at_monotonic = float(clock())
            _register_native_object(event, "resource_use")
            events.append(event)
        observation = bridge.observe_resource_use(
            native_resource_events=tuple(events),
            task_context=context,
            route_decision=decision,
            expected_repository=VALID_REGISTRY.parents[2],
            expected_worktree=VALID_REGISTRY.parents[2],
            expected_branch="codex/test",
            expected_head="a" * 40,
            session_id="session-routing-tests",
            invocation_id=invocation_id,
            clock=clock,
            ttl_seconds=30,
        )
        return bridge.validate_resource_use_observation(
            observation,
            expected_task_digest=decision["facts"]["task_digest"],
            expected_route_digest=decision["decision_digest"],
            expected_resource_bindings=decision[
                "selected_resource_digests"
            ],
            expected_repository=VALID_REGISTRY.parents[2],
            expected_worktree=VALID_REGISTRY.parents[2],
            expected_branch="codex/test",
            expected_head="a" * 40,
            expected_session_id="session-routing-tests",
            expected_invocation_id=invocation_id,
            clock=validation_clock or clock,
        )

    def test_route_verify_accepts_only_host_bound_resource_observation(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.routing import resolve_route, verify_route

        task = task_envelope()
        inventory = validated_inventory(
            inventory_snapshot(),
            registry=self.registry,
            task=task,
            invocation_id="resource-route",
        )
        decision = resolve_route(
            task,
            self.policy,
            self.registry,
            inventory,
            mode="audit",
            host_capability=bridge.HOST_ADAPTER_UNAVAILABLE,
            clarification_request=self.validated_request(
                task, repository_status="resolved"
            ),
        )
        observation = self.resource_observation(
            task,
            decision,
            inventory,
            effects=("local_read", "local_write"),
            invocation_id="resource-route",
        )

        with self.assertRaisesRegex(
            ValueError, "R_UNTRUSTED_ROUTE_DECISION"
        ):
            verify_route(dict(decision), observation, mode="enforce")
        receipt = verify_route(decision, observation, mode="enforce")

        self.assertTrue(receipt["ok"], receipt)
        self.assertTrue(receipt["authoritative"])
        with self.assertRaisesRegex(
            ValueError, "R_UNTRUSTED_RESOURCE_OBSERVATION"
        ):
            verify_route(decision, copy.deepcopy(receipt), mode="enforce")
        with self.assertRaisesRegex(
            ValueError, "R_RESOURCE_OBSERVATION_REPLAY"
        ):
            verify_route(decision, observation, mode="enforce")

    def test_route_verify_rejects_write_with_pending_clarification(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.routing import resolve_route, verify_route

        task = task_envelope()
        inventory = validated_inventory(
            inventory_snapshot(),
            registry=self.registry,
            task=task,
            invocation_id="pending-route",
        )
        decision = resolve_route(
            task,
            self.policy,
            self.registry,
            inventory,
            mode="audit",
            host_capability=bridge.HOST_ADAPTER_UNAVAILABLE,
        )
        observation = self.resource_observation(
            task,
            decision,
            inventory,
            effects=("local_write",),
            invocation_id="pending-route",
        )

        receipt = verify_route(decision, observation, mode="enforce")

        self.assertFalse(receipt["ok"])
        self.assertIn(
            "R_CLARIFICATION_PENDING",
            {error["code"] for error in receipt["errors"]},
        )

    def test_resource_use_observation_is_exact_bound_fresh_and_one_shot(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.routing import resolve_route
        from tests.host_adapter_test_support import _register_native_object

        task = task_envelope()
        inventory = validated_inventory(
            inventory_snapshot(),
            registry=self.registry,
            task=task,
            invocation_id="binding-route",
        )
        decision = resolve_route(
            task,
            self.policy,
            self.registry,
            inventory,
            mode="audit",
            host_capability=bridge.HOST_ADAPTER_UNAVAILABLE,
            clarification_request=self.validated_request(
                task, repository_status="resolved"
            ),
        )
        capability = self.host_capability(invocation_id="binding-route")
        context = bridge.build_trusted_route_context(
            task=task,
            decision=decision,
            inventory=inventory,
            expected_repository=VALID_REGISTRY.parents[2],
            expected_worktree=VALID_REGISTRY.parents[2],
            expected_branch="codex/test",
            expected_head="a" * 40,
            session_id="session-routing-tests",
            invocation_id="binding-route",
            host_capability=capability,
            clock=lambda: 100.0,
            ttl_seconds=30,
        )
        events = []
        for ordinal, resource_id in enumerate(
            decision["summary"]["required"]
        ):
            event = object.__new__(bridge.NativeResourceUseEvent)
            event._consumed = False
            event.event_id = f"binding-event-{ordinal}"
            event.resource_id = resource_id
            event.locator_digest = decision[
                "selected_resource_digests"
            ][resource_id]
            event.operation = "read"
            event.ordinal = ordinal
            event.observed_effects = ("local_read",)
            event.tool_use_id = "tool-binding-route"
            event.task_digest = context.task_digest
            event.route_digest = context.route_digest
            event.repository_identity = context.repository_identity
            event.worktree_identity = context.worktree_identity
            event.branch = context.branch
            event.head = context.head
            event.session_id = context.session_id
            event.invocation_id = context.invocation_id
            event.context_nonce = context.context_nonce
            event.observed_at_monotonic = 100.0
            _register_native_object(event, "resource_use")
            events.append(event)
        events[0].context_nonce = "route-context-from-another-execution"
        with self.assertRaisesRegex(
            ValueError, "R_RESOURCE_OBSERVATION_BINDING"
        ):
            bridge.observe_resource_use(
                native_resource_events=events,
                task_context=context,
                route_decision=decision,
                expected_repository=VALID_REGISTRY.parents[2],
                expected_worktree=VALID_REGISTRY.parents[2],
                expected_branch="codex/test",
                expected_head="a" * 40,
                session_id="session-routing-tests",
                invocation_id="binding-route",
                clock=lambda: 100.0,
                ttl_seconds=5,
            )
        events[0].context_nonce = context.context_nonce
        observation = bridge.observe_resource_use(
            native_resource_events=events,
            task_context=context,
            route_decision=decision,
            expected_repository=VALID_REGISTRY.parents[2],
            expected_worktree=VALID_REGISTRY.parents[2],
            expected_branch="codex/test",
            expected_head="a" * 40,
            session_id="session-routing-tests",
            invocation_id="binding-route",
            clock=lambda: 100.0,
            ttl_seconds=5,
        )
        common = {
            "expected_task_digest": decision["facts"]["task_digest"],
            "expected_route_digest": decision["decision_digest"],
            "expected_resource_bindings": decision[
                "selected_resource_digests"
            ],
            "expected_repository": VALID_REGISTRY.parents[2],
            "expected_worktree": VALID_REGISTRY.parents[2],
            "expected_branch": "codex/test",
            "expected_head": "a" * 40,
            "expected_session_id": "session-routing-tests",
            "expected_invocation_id": "binding-route",
        }
        altered_bindings = dict(
            decision["selected_resource_digests"]
        )
        first_resource = next(iter(altered_bindings))
        altered_bindings[first_resource] = "sha256:" + ("f" * 64)
        binding_mutations = (
            {"expected_task_digest": "sha256:" + ("1" * 64)},
            {"expected_route_digest": "sha256:" + ("2" * 64)},
            {"expected_resource_bindings": altered_bindings},
            {"expected_repository": VALID_REGISTRY.parent},
            {"expected_worktree": VALID_REGISTRY.parent},
            {"expected_branch": "codex/other"},
            {"expected_head": "b" * 40},
            {"expected_session_id": "session-other"},
            {"expected_invocation_id": "invocation-other"},
        )
        for mutation in binding_mutations:
            with self.subTest(binding=next(iter(mutation))):
                with self.assertRaisesRegex(
                    ValueError, "R_RESOURCE_OBSERVATION_BINDING"
                ):
                    bridge.validate_resource_use_observation(
                        observation,
                        **{**common, **mutation},
                        clock=lambda: 100.0,
                    )
        with self.assertRaisesRegex(
            ValueError, "R_RESOURCE_OBSERVATION_BINDING"
        ):
            bridge.validate_resource_use_observation(
                observation,
                **common,
                clock=lambda: float("nan"),
            )
        with self.assertRaisesRegex(
            ValueError, "R_RESOURCE_OBSERVATION_STALE"
        ):
            bridge.validate_resource_use_observation(
                observation,
                **common,
                clock=lambda: 106.0,
            )
        validated = bridge.validate_resource_use_observation(
            observation,
            **common,
            clock=lambda: 100.0,
        )
        self.assertEqual(
            validated.route_digest, decision["decision_digest"]
        )
        with self.assertRaisesRegex(
            ValueError, "R_RESOURCE_OBSERVATION_REPLAY"
        ):
            bridge.validate_resource_use_observation(
                observation,
                **common,
                clock=lambda: 100.0,
            )

    def test_serialized_route_decision_cannot_enter_authoritative_context(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.contracts import contract_digest
        from control_plane.routing import resolve_route

        invocation_id = "serialized-route-decision"
        task = task_envelope()
        inventory = validated_inventory(
            inventory_snapshot(),
            registry=self.registry,
            task=task,
            invocation_id=invocation_id,
        )
        decision = resolve_route(
            task,
            self.policy,
            self.registry,
            inventory,
            mode="audit",
            host_capability=bridge.HOST_ADAPTER_UNAVAILABLE,
            clarification_request=self.validated_request(
                task, repository_status="resolved"
            ),
        )
        serialized = copy.deepcopy(dict(decision))
        serialized["authorization"]["destructive"] = True
        serialized["interaction"]["clarification_gate"]["status"] = "resolved"
        serialized["interaction"]["clarification_gate"][
            "decision_ready"
        ] = True
        serialized["approval_boundaries"] = []
        serialized["errors"] = []
        serialized["decision_ready"] = True
        serialized["ok"] = True
        serialized["decision_digest"] = contract_digest(
            {
                key: value
                for key, value in serialized.items()
                if key != "decision_digest"
            }
        )

        with self.assertRaisesRegex(
            ValueError, "R_UNTRUSTED_ROUTE_DECISION"
        ):
            bridge.build_trusted_route_context(
                task=task,
                decision=serialized,
                inventory=inventory,
                expected_repository=VALID_REGISTRY.parents[2],
                expected_worktree=VALID_REGISTRY.parents[2],
                expected_branch="codex/test",
                expected_head="a" * 40,
                session_id="session-routing-tests",
                invocation_id=invocation_id,
                host_capability=self.host_capability(
                    invocation_id=invocation_id
                ),
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

    def test_route_context_binds_inventory_invocation_branch_and_head(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.routing import resolve_route

        task = task_envelope()
        inventory = validated_inventory(
            inventory_snapshot(),
            registry=self.registry,
            task=task,
            invocation_id="inventory-other-invocation",
        )
        decision = resolve_route(
            task,
            self.policy,
            self.registry,
            inventory,
            mode="audit",
            host_capability=bridge.HOST_ADAPTER_UNAVAILABLE,
            clarification_request=self.validated_request(
                task, repository_status="resolved"
            ),
        )

        with self.assertRaisesRegex(ValueError, "R_ROUTE_CONTEXT"):
            bridge.build_trusted_route_context(
                task=task,
                decision=decision,
                inventory=inventory,
                expected_repository=VALID_REGISTRY.parents[2],
                expected_worktree=VALID_REGISTRY.parents[2],
                expected_branch="codex/test",
                expected_head="a" * 40,
                session_id="session-routing-tests",
                invocation_id="context-invocation",
                host_capability=self.host_capability(
                    invocation_id="context-invocation"
                ),
                clock=lambda: 100.0,
                ttl_seconds=30,
            )

    def test_validated_observation_expires_before_authoritative_verify(
        self,
    ) -> None:
        import control_plane.host_bridge as bridge
        from control_plane.routing import resolve_route, verify_route

        invocation_id = "verify-after-expiry"
        now = [100.0]
        clock = lambda: now[0]
        task = task_envelope()
        inventory = validated_inventory(
            inventory_snapshot(),
            registry=self.registry,
            task=task,
            invocation_id=invocation_id,
        )
        decision = resolve_route(
            task,
            self.policy,
            self.registry,
            inventory,
            mode="audit",
            host_capability=bridge.HOST_ADAPTER_UNAVAILABLE,
            clarification_request=self.validated_request(
                task, repository_status="resolved"
            ),
        )
        observation = self.resource_observation(
            task,
            decision,
            inventory,
            effects=("local_read",),
            invocation_id=invocation_id,
            clock=clock,
            capability_ttl_seconds=5,
            validation_clock=lambda: 100.0,
        )
        self.assertEqual(observation.freshness_deadline, 105.0)

        now[0] = 106.0
        with self.assertRaisesRegex(
            ValueError, "R_RESOURCE_OBSERVATION_STALE"
        ):
            verify_route(decision, observation, mode="enforce")

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
