from __future__ import annotations

import copy
import inspect
import unittest

from tests.router_test_support import ROOT, task_envelope, validated_inventory


SOURCE_REFERENCE = "codex://threads/019fc3b8-f25b-7281-9401-7773e71af0f5"
AUDITOR_REFERENCE = "codex://threads/019fc3df-1709-7740-a9b6-5375b951f4b0"


class CrossThreadAuditLookupTests(unittest.TestCase):
    def _request(self, *, auditor_reference: str | None = None):
        from control_plane.cross_thread_audit import prepare_cross_thread_audit_lookup

        return prepare_cross_thread_audit_lookup(
            task_envelope(
                task_id="cross-thread-audit-consumer",
                explicit_resources=[],
            ),
            source_reference=SOURCE_REFERENCE,
            auditor_reference=auditor_reference,
        )[1]

    @staticmethod
    def _resign(capsule: dict[str, object]) -> dict[str, object]:
        from control_plane.contracts import contract_digest

        unsigned = {
            key: value
            for key, value in capsule.items()
            if key != "capsule_digest"
        }
        capsule["capsule_digest"] = contract_digest(unsigned)
        return capsule

    def test_exact_reference_selects_shadow_but_runtime_remains_unresolved(self) -> None:
        from control_plane.adoption import RUNTIME_MODULES
        from control_plane.cross_thread_audit import (
            CROSS_THREAD_AUDIT_RESOURCE_ID,
            prepare_cross_thread_audit_lookup,
            select_cross_thread_audit_resource,
        )
        from control_plane.policy import load_policy
        from control_plane.resource_registry import build_inventory, load_registry
        from control_plane.routing import resolve_route

        task = task_envelope(
            task_id="cross-thread-audit-consumer",
            signals=["cross_system"],
            risk={
                "uncertainty": 0,
                "blast_radius": 1,
                "irreversibility": 0,
                "verification_complexity": 1,
            },
            explicit_resources=[],
        )
        prepared, request = prepare_cross_thread_audit_lookup(
            task,
            source_reference=SOURCE_REFERENCE,
        )

        self.assertEqual(
            select_cross_thread_audit_resource(SOURCE_REFERENCE),
            CROSS_THREAD_AUDIT_RESOURCE_ID,
        )
        for fuzzy in (
            "019fc3b8-f25b-7281-9401-7773e71af0f5",
            f"inspect {SOURCE_REFERENCE}",
            f"{SOURCE_REFERENCE}/",
            "codex://thread/019fc3b8-f25b-7281-9401-7773e71af0f5",
        ):
            with self.subTest(fuzzy=fuzzy):
                self.assertIsNone(select_cross_thread_audit_resource(fuzzy))
        self.assertEqual(prepared["explicit_resources"], [CROSS_THREAD_AUDIT_RESOURCE_ID])
        self.assertEqual(request.source_thread_id, SOURCE_REFERENCE.rsplit("/", 1)[1])
        self.assertIn("cross_thread_audit.py", RUNTIME_MODULES)

        policy = load_policy(ROOT / ".codex" / "project-policy.toml")
        registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
        resource = next(
            item
            for item in registry["resources"]
            if item["id"] == CROSS_THREAD_AUDIT_RESOURCE_ID
        )
        self.assertEqual(resource["capabilities"], ["audit.cross-thread.lookup.shadow"])
        self.assertEqual(resource["effects"], ["local_read"])
        self.assertEqual(resource["egress"], "none")
        self.assertEqual(resource["selection"], "recommended")
        self.assertEqual(resource["trust"], "unverified_external")
        self.assertTrue(resource["locator"].startswith("agent://"))
        self.assertNotIn("steer", " ".join(resource["capabilities"]))

        snapshot = build_inventory(registry, ROOT)
        inventory_resource = next(
            item
            for item in snapshot["resources"]
            if item["id"] == CROSS_THREAD_AUDIT_RESOURCE_ID
        )
        self.assertEqual(inventory_resource["availability"], "unknown")
        self.assertFalse(inventory_resource["ready"])
        self.assertIn("R_RUNTIME_EVIDENCE_REQUIRED", inventory_resource["reason_codes"])

        decision = resolve_route(
            prepared,
            policy,
            registry,
            validated_inventory(
                snapshot,
                registry=registry,
                task=prepared,
                invocation_id="cross-thread-route",
            ),
            mode="audit",
        )
        self.assertIn(CROSS_THREAD_AUDIT_RESOURCE_ID, decision["summary"]["required"])
        self.assertIn(CROSS_THREAD_AUDIT_RESOURCE_ID, decision["summary"]["unresolved"])
        self.assertIn(
            "E_RESOURCE_NOT_READY",
            {item["code"] for item in decision["errors"]},
        )

    def test_absent_native_consumer_can_only_produce_bounded_unknown(self) -> None:
        from control_plane.contracts import canonical_json, contract_digest
        from control_plane.cross_thread_audit import (
            evaluate_cross_thread_audit_lookup,
            render_cross_thread_audit_capsule,
        )

        request = self._request(auditor_reference=AUDITOR_REFERENCE)
        capsule = evaluate_cross_thread_audit_lookup(request)
        rendered = render_cross_thread_audit_capsule(capsule)

        self.assertEqual(capsule["freshness"], "UNKNOWN")
        self.assertEqual(capsule["verdict"], "UNKNOWN")
        self.assertEqual(capsule["reason_codes"], ["HOST_CONSUMER_UNAVAILABLE"])
        self.assertFalse(capsule["authorizes"])
        self.assertEqual(capsule["source"]["thread_id"], request.source_thread_id)
        self.assertEqual(
            capsule["source"]["auditor_thread_id"], request.auditor_thread_id
        )
        self.assertEqual(capsule["source"]["state"], "unavailable")
        self.assertEqual(capsule["findings"], [])
        self.assertEqual(capsule["tests"], [])
        unsigned = {
            key: value
            for key, value in capsule.items()
            if key != "capsule_digest"
        }
        self.assertEqual(capsule["capsule_digest"], contract_digest(unsigned))
        self.assertEqual(rendered, canonical_json(capsule))
        self.assertLessEqual(len(rendered.encode("utf-8")), 4096)
        for forbidden in (
            "transcript",
            "prompt",
            "raw_output",
            "tool_output",
            "hidden_reasoning",
        ):
            self.assertNotIn(forbidden, rendered.lower())

    def test_no_public_api_can_fabricate_valid_cross_thread_evidence(self) -> None:
        import control_plane.cross_thread_audit as audit_lookup

        request = self._request()
        capsule = audit_lookup.evaluate_cross_thread_audit_lookup(request)

        for removed_name in (
            "CrossThreadAuditContext",
            "CrossThreadAuditObservation",
            "frame_cross_thread_audit_context",
            "frame_cross_thread_audit_observation",
        ):
            self.assertFalse(hasattr(audit_lookup, removed_name))
        self.assertEqual(
            tuple(inspect.signature(audit_lookup.evaluate_cross_thread_audit_lookup).parameters),
            ("request",),
        )
        with self.assertRaisesRegex(ValueError, "typed lookup request"):
            audit_lookup.evaluate_cross_thread_audit_lookup(request.__dict__)  # type: ignore[arg-type]

        forged = copy.deepcopy(capsule)
        forged["freshness"] = "VALID"
        forged["verdict"] = "PASS"
        forged["findings"] = ["Caller-invented approval."]
        forged["tests"] = ["Caller-invented test result."]
        self._resign(forged)
        with self.assertRaisesRegex(ValueError, "closed capsule validation"):
            audit_lookup.render_cross_thread_audit_capsule(forged)

    def test_renderer_rejects_impossible_ids_and_unknown_semantics(self) -> None:
        from control_plane.cross_thread_audit import (
            evaluate_cross_thread_audit_lookup,
            render_cross_thread_audit_capsule,
        )

        request = self._request()
        capsule = evaluate_cross_thread_audit_lookup(request)
        mutations = (
            ("bad-observed-id", ("source", "observed_thread_id"), "not-a-thread-id"),
            ("completed-without-observation", ("source", "state"), "completed"),
            ("unknown-pass", ("verdict",), "PASS"),
            ("unknown-findings", ("findings",), ["Injected finding."]),
            ("unknown-tests", ("tests",), ["Injected test."]),
            ("unknown-auditor-state", ("source", "auditor_state"), "completed"),
        )
        for name, path, value in mutations:
            with self.subTest(name=name):
                forged = copy.deepcopy(capsule)
                target = forged
                for key in path[:-1]:
                    target = target[key]  # type: ignore[index,assignment]
                target[path[-1]] = value  # type: ignore[index]
                self._resign(forged)
                with self.assertRaisesRegex(ValueError, "closed capsule validation"):
                    render_cross_thread_audit_capsule(forged)

    def test_resource_receipt_records_lookup_but_cannot_make_a_gate_pass(self) -> None:
        from control_plane.cross_thread_audit import (
            CROSS_THREAD_AUDIT_RESOURCE_ID,
            prepare_cross_thread_audit_lookup,
        )
        from control_plane.lifecycle import create_resource_receipt
        from control_plane.policy import load_policy
        from control_plane.resource_registry import build_inventory, load_registry
        from control_plane.routing import resolve_route, verify_route

        prepared, _request = prepare_cross_thread_audit_lookup(
            task_envelope(
                task_id="cross-thread-receipt-consumer",
                signals=["cross_system"],
                risk={
                    "uncertainty": 0,
                    "blast_radius": 1,
                    "irreversibility": 0,
                    "verification_complexity": 1,
                },
                explicit_resources=[],
            ),
            source_reference=SOURCE_REFERENCE,
        )
        policy = load_policy(ROOT / ".codex" / "project-policy.toml")
        registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
        decision = resolve_route(
            prepared,
            policy,
            registry,
            validated_inventory(
                build_inventory(registry, ROOT),
                registry=registry,
                task=prepared,
                invocation_id="cross-thread-receipt-route",
            ),
            mode="audit",
        )
        receipt = create_resource_receipt(
            task_id=prepared["task_id"],
            decision_digest=decision["decision_digest"],
            digests={
                "task": decision["facts"]["task_digest"],
                "policy": decision["facts"]["policy_digest"],
                "registry": decision["facts"]["registry_digest"],
                "inventory": decision["facts"]["inventory_digest"],
            },
            used=[CROSS_THREAD_AUDIT_RESOURCE_ID],
            resource_digests=decision["selected_resource_digests"],
            omitted=[],
            gates=[],
            effects=["local_read"],
        )

        diagnostic = verify_route(decision, receipt, mode="audit")

        self.assertFalse(diagnostic["authoritative"])
        self.assertFalse(diagnostic["compliant"])
        self.assertIn("E_RECEIPT_GATE", {item["code"] for item in diagnostic["errors"]})
        import control_plane.cross_thread_audit as audit_lookup

        self.assertFalse(hasattr(audit_lookup, "IndependentReviewReceipt"))


if __name__ == "__main__":
    unittest.main()
