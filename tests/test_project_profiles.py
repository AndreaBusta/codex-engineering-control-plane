from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.router_test_support import task_envelope, validated_inventory


ROOT = Path(__file__).parents[1]


class ProjectProfileTests(unittest.TestCase):
    def detect(self, paths: tuple[str, ...]) -> dict:
        from control_plane.project_profiles import detect_project_profile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in paths:
                path = root / relative
                if relative.endswith("/"):
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("{}\n", encoding="utf-8")
            return detect_project_profile(root)

    def test_ios_is_detected_from_apple_project_markers(self) -> None:
        profile = self.detect(("App.xcodeproj/", "App/Info.plist"))

        self.assertEqual(profile["kind"], "ios")
        self.assertEqual(profile["profiles"], ["ios"])

    def test_android_is_not_misclassified_as_ios(self) -> None:
        profile = self.detect(
            ("settings.gradle.kts", "app/src/main/AndroidManifest.xml")
        )

        self.assertEqual(profile["kind"], "android")
        self.assertNotIn("ios", profile["profiles"])

    def test_pwa_profile_uses_web_runtime_markers(self) -> None:
        profile = self.detect(
            ("package.json", "public/manifest.webmanifest", "src/service-worker.js")
        )

        self.assertEqual(profile["kind"], "web_pwa")

    def test_web_application_without_service_worker_uses_web_profile(
        self,
    ) -> None:
        profile = self.detect(("package.json", "vite.config.ts", "src/main.ts"))

        self.assertEqual(profile["kind"], "web_pwa")

    def test_saas_backend_profile_uses_api_and_migration_markers(self) -> None:
        profile = self.detect(
            ("pyproject.toml", "api/", "migrations/", "Dockerfile")
        )

        self.assertEqual(profile["kind"], "saas_backend")

    def test_ai_text_pipeline_uses_prompts_evals_and_pipeline_markers(self) -> None:
        profile = self.detect(
            ("pyproject.toml", "prompts/", "evals/", "pipelines/")
        )

        self.assertEqual(profile["kind"], "ai_text_pipeline")

    def test_multiple_independent_stacks_become_hybrid(self) -> None:
        profile = self.detect(
            (
                "ios/App.xcodeproj/",
                "android/settings.gradle",
                "android/app/src/main/AndroidManifest.xml",
            )
        )

        self.assertEqual(profile["kind"], "hybrid")
        self.assertEqual(profile["profiles"], ["android", "ios"])

    def test_unknown_repository_falls_back_to_generic_without_ios(self) -> None:
        profile = self.detect(("Makefile", "src/main.c"))

        self.assertEqual(profile["kind"], "generic")
        self.assertEqual(profile["profiles"], ["generic"])
        self.assertNotIn("ios", profile["profiles"])

    def test_markers_inside_docs_fixtures_and_examples_do_not_create_profiles(
        self,
    ) -> None:
        profile = self.detect(
            (
                "docs/prompts/",
                "docs/evals/",
                "fixtures/api/",
                "examples/vite.config.ts",
                "tests/AndroidManifest.xml",
                "tests/build.gradle",
            )
        )

        self.assertEqual(profile["profiles"], ["generic"])

    def test_detection_is_deterministic_bounded_and_does_not_follow_symlinks(
        self,
    ) -> None:
        from control_plane.project_profiles import detect_project_profile

        with (
            tempfile.TemporaryDirectory() as temporary,
            tempfile.TemporaryDirectory() as outside_temporary,
        ):
            root = Path(temporary)
            outside = Path(outside_temporary)
            (outside / "App.xcodeproj").mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)

            first = detect_project_profile(root)
            second = detect_project_profile(root)

        self.assertEqual(first, second)
        self.assertEqual(first["kind"], "generic")
        self.assertLessEqual(len(first["evidence"]), 32)

    def test_incomplete_scan_is_reported_instead_of_claiming_full_confidence(
        self,
    ) -> None:
        from control_plane.project_profiles import detect_project_profile

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for index in range(4):
                (root / f"{index:02d}.txt").write_text("x\n", encoding="utf-8")
            with patch("control_plane.project_profiles.MAX_ENTRIES", 2):
                profile = detect_project_profile(root)

        self.assertTrue(profile["truncated"])
        self.assertEqual(profile["confidence"], "bounded_scan_incomplete")

    def test_router_loads_android_quality_profile_without_ios_profile(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import HOST_ADAPTER_UNAVAILABLE
        from control_plane.policy import load_policy
        from control_plane.resource_registry import load_registry
        from control_plane.routing import resolve_route

        registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
        policy = load_policy(ROOT / ".codex" / "project-policy.toml")
        inventory: dict = {
            "schema_version": 1,
            "source": "profile-test",
            "project_profile": {
                "schema_version": 1,
                "kind": "android",
                "profiles": ["android"],
                "evidence": ["app/src/main/AndroidManifest.xml"],
                "confidence": "marker_evidence",
                "truncated": False,
            },
            "resources": [
                {
                    "id": resource["id"],
                    "availability": "available",
                    "discovered": True,
                    "enabled": True,
                    "trusted": True,
                    "authenticated": "not_applicable",
                    "healthy": "available",
                    "authorized_for_task": False,
                    "ready": True,
                    "locator_digest": f"sha256:{index:064x}",
                    "size_bytes": 256,
                    "reason_codes": [],
                }
                for index, resource in enumerate(registry["resources"])
            ],
        }
        inventory["snapshot_digest"] = contract_digest(inventory)

        task = task_envelope(domains=["mobile", "ios"])
        decision = resolve_route(
            task,
            policy,
            registry,
            validated_inventory(
                inventory,
                registry=registry,
                task=task,
                invocation_id="project-profile-route",
            ),
            mode="audit",
            host_capability=HOST_ADAPTER_UNAVAILABLE,
        )

        self.assertIn("document.profile-android", decision["summary"]["required"])
        self.assertNotIn("document.profile-ios", decision["summary"]["required"])
        self.assertEqual(decision["summary"]["profile_mismatch"], ["ios"])

    def test_hybrid_t2_answer_requires_all_profiles_across_lifecycle_phases(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.host_bridge import HOST_ADAPTER_UNAVAILABLE
        from control_plane.policy import load_policy
        from control_plane.resource_registry import load_registry
        from control_plane.routing import resolve_route

        registry = load_registry(ROOT / ".codex" / "resource-registry.toml")
        policy = load_policy(ROOT / ".codex" / "project-policy.toml")
        inventory: dict = {
            "schema_version": 1,
            "source": "hybrid-profile-test",
            "project_profile": {
                "schema_version": 1,
                "kind": "hybrid",
                "profiles": ["android", "ios", "web_pwa"],
                "evidence": [
                    "android/app/src/main/AndroidManifest.xml",
                    "ios/App/App.xcodeproj",
                    "sw.js",
                ],
                "confidence": "marker_evidence",
                "truncated": False,
            },
            "resources": [
                {
                    "id": resource["id"],
                    "availability": "available",
                    "discovered": True,
                    "enabled": True,
                    "trusted": True,
                    "authenticated": "not_applicable",
                    "healthy": "available",
                    "authorized_for_task": False,
                    "ready": True,
                    "locator_digest": f"sha256:{index:064x}",
                    "size_bytes": 256,
                    "reason_codes": [],
                }
                for index, resource in enumerate(registry["resources"])
            ],
        }
        inventory["snapshot_digest"] = contract_digest(inventory)
        cases = (
            {
                "name": "plan",
                "objective": "Plan a bounded shared-core UI change.",
                "intent": "plan",
                "phase": "plan",
                "domains": ["product_ui"],
                "signals": ["multi_file", "regression_risk"],
            },
            {
                "name": "research",
                "objective": "Audit the hybrid architecture and its quality gates.",
                "intent": "audit",
                "phase": "research",
                "domains": ["architecture"],
                "signals": ["cross_system", "regression_risk"],
            },
            {
                "name": "observe",
                "objective": "Observe a shared hybrid runtime after integration.",
                "intent": "audit",
                "phase": "observe",
                "domains": ["architecture"],
                "signals": ["cross_system", "regression_risk"],
            },
        )
        for case in cases:
            with self.subTest(case=case["name"]):
                task = task_envelope(
                    objective=case["objective"],
                    intent=case["intent"],
                    phase=case["phase"],
                    requested_outcome="answer",
                    domains=case["domains"],
                    goals=[
                        {
                            "id": f"{case['name']}-shared-ui",
                            "summary": "Cover every detected quality profile.",
                            "domains": case["domains"],
                            "depends_on": [],
                        }
                    ],
                    signals=case["signals"],
                    risk={
                        "uncertainty": 0,
                        "blast_radius": 1,
                        "irreversibility": 0,
                        "verification_complexity": 1,
                    },
                    effects=[
                        {"name": "local_read", "source": "model_inference"}
                    ],
                )

                decision = resolve_route(
                    task,
                    policy,
                    registry,
                    validated_inventory(
                        inventory,
                        registry=registry,
                        task=task,
                        invocation_id=f"hybrid-profile-{case['name']}-route",
                    ),
                    mode="audit",
                    host_capability=HOST_ADAPTER_UNAVAILABLE,
                )

                self.assertEqual(decision["summary"]["tier"], "T2")
                self.assertTrue(decision["decision_ready"])
                self.assertNotIn(
                    "E_CONTEXT_BUDGET_REQUIRED",
                    {error["code"] for error in decision["errors"]},
                )
                self.assertEqual(
                    decision["summary"]["selected_context_units"], 8
                )
                self.assertTrue(
                    {
                        "document.profile-android",
                        "document.profile-ios",
                        "document.profile-web-pwa",
                    }.issubset(decision["summary"]["required"])
                )
        resources = {resource["id"]: resource for resource in registry["resources"]}
        for resource_id in (
            "document.profile-android",
            "document.profile-ios",
            "document.profile-web-pwa",
        ):
            resource = resources[resource_id]
            self.assertEqual(resource["context_class"], "tiny")
            path = ROOT / str(resource["locator"]).removeprefix("repo://")
            self.assertLessEqual(path.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
