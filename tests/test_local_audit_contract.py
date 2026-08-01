from __future__ import annotations

import argparse
import importlib
import inspect
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("parser has no subcommands")


class LocalAuditSurfaceContractTests(unittest.TestCase):
    def test_cli_exposes_only_the_supported_local_audit_commands(self) -> None:
        from control_plane.cli import build_parser

        commands = _subcommands(build_parser())
        self.assertEqual(
            set(commands),
            {
                "adopt",
                "doctor",
                "git-guard",
                "hook-smoke",
                "inventory",
                "policy-check",
                "preflight",
                "registry-check",
                "risk-status",
                "route",
                "route-verify",
                "safe-read",
                "task",
                "upgrade",
                "verification-run",
            },
        )
        self.assertEqual(
            set(_subcommands(commands["task"])),
            {"start", "resume", "status", "transition", "close", "lease-release"},
        )
        self.assertEqual(
            set(_subcommands(commands["git-guard"])),
            {"pre-commit", "pre-push"},
        )

    def test_candidate_only_surfaces_without_a_product_consumer_are_absent(self) -> None:
        forbidden = {
            "control_plane.clarification": {
                "RepositoryEvidenceFacts",
                "ClarificationRepositoryInspector",
                "RepositoryEvidenceNotChecked",
                "build_validated_clarification_request",
                "validate_assumption_record",
                "validate_clarification_resolution",
                "validate_authorization",
                "validate_irreversible_confirmation",
            },
            "control_plane.hooks": {
                "CurrentWarningView",
                "BashEffect",
                "PreToolDecision",
                "classify_bash_command",
                "evaluate_pretool_use",
                "current_warning_view_path",
                "publish_current_warning_view",
                "publish_framed_current_warning_view",
                "load_current_warning_view",
                "gc_current_warning_view",
                "_ensure_private_directory",
                "_warning_payload_from_mapping",
            },
            "control_plane.host_bridge": {
                "NativeTaskEvent",
                "NativeGitBaseEvent",
                "RegisteredGoverningBaseContext",
                "LocalBasePolicyObservation",
                "ValidatedLocalBaseObservation",
                "HostRiskContextObservation",
                "ValidatedHostRiskContext",
                "NativeResourceUseEvent",
                "TrustedRouteContext",
                "ResourceUseObservation",
                "ValidatedResourceUseObservation",
                "FramedClarificationIssue",
                "FramedClarificationPromptView",
                "ClarificationRepositoryObservation",
                "ValidatedClarificationRepositoryObservation",
                "ValidatedAssumption",
                "TrustedInteraction",
                "TrustedIrreversibleConfirmation",
                "HostContextMetrics",
                "ConsumedEffectCapabilities",
                "frame_local_base_policy_source",
                "validate_local_base_policy_source",
                "observe_host_risk_context",
                "validate_host_risk_context",
                "consume_validated_host_risk_context",
                "frame_clarification_issue",
                "frame_clarification_prompt_view",
                "clarification_route_context_digest",
                "consume_clarification_entry_bindings",
                "clarification_interaction_subject_digest",
                "frame_trusted_interaction",
                "consume_clarification_resolution_bindings",
                "observe_clarification_repository",
                "validate_clarification_repository_observation",
                "frame_irreversible_confirmation",
                "frame_host_context_metrics",
                "consume_effect_capabilities",
                "build_trusted_route_context",
                "observe_resource_use",
                "validate_resource_use_observation",
                "NativeHooksReviewEvent",
                "ValidatedHookReviewObservation",
                "HookReviewReceipt",
                "HookReviewPublicationResult",
                "frame_hook_review_observation",
                "publish_hook_review_receipt",
                "NativeMacOSProcessEvent",
                "ValidatedNativeMacOSHookSmokeObservation",
                "observe_native_macos_hook_smoke",
                "_validated_route_payload",
                "_clarification_route_material_digest",
                "_receipt_core",
            },
        }
        leaked = {
            module_name: sorted(
                name
                for name in names
                if hasattr(importlib.import_module(module_name), name)
            )
            for module_name, names in forbidden.items()
        }
        self.assertEqual(leaked, {module_name: [] for module_name in forbidden})

        from control_plane.lifecycle import TaskStore

        self.assertFalse(hasattr(TaskStore, "require_clarification"))
        self.assertFalse(hasattr(TaskStore, "resolve_and_resume_clarification"))
        self.assertFalse(hasattr(TaskStore, "clarification_status"))
        self.assertNotIn(
            "host_metrics",
            inspect.signature(TaskStore.record_context_metrics).parameters,
        )

    def test_every_origin_main_public_api_remains_importable(self) -> None:
        baseline = {
            "control_plane.adoption": {
                "adoption_apply", "adoption_plan", "adoption_rollback", "adoption_status",
                "adoption_verify", "upgrade_apply", "upgrade_plan",
            },
            "control_plane.cli": {
                "build_parser", "command_adopt", "command_doctor", "command_inventory",
                "command_policy_check", "command_preflight", "command_registry_check",
                "command_route", "command_route_verify", "command_task", "command_upgrade",
                "command_verification_run", "main",
            },
            "control_plane.contracts": {
                "ContractIssue", "canonical_json", "contract_digest",
                "validate_authorization_grant", "validate_task_envelope", "validate_task_id",
            },
            "control_plane.hooks": {"evaluate_hook", "run_hook"},
            "control_plane.host_bridge": {
                "ConsumedAuthorization", "GitHubObservation", "GoverningRuntimeObservation",
                "HostAdapterCapability", "InventoryObservation", "LocalGitIndexObservation",
                "LocalGitObservation", "NativeGitHubProviderEvent", "NativeSessionEvent",
                "NativeUserInteractionEvent", "PullRequestMutationObservation",
                "ReleaseProviderObservation", "RemoteEffectContext", "TrustedAuthorization",
                "TrustedLeaseRecoveryAuthorization", "ValidatedCandidateWorktreeObservation",
                "ValidatedGitHubObservation", "ValidatedGitHubPullRequestWriteProvider",
                "ValidatedGoverningBaseWorktreeObservation", "ValidatedInventory",
                "ValidatedLocalGitObservation", "ValidatedPullRequestBody",
                "ValidatedPullRequestMutationObservation", "ValidatedPullRequestMutationRequest",
                "ValidatedPullRequestTitle", "ValidatedReleaseProviderObservation",
                "ValidatedRemoteEffectContext", "ValidatedWorktreeInventoryObservation",
                "WorktreeInventoryObservation", "WorktreeInventoryRecord", "WorktreePorcelainEntry",
                "approve_github_pr_write_provider", "attest_candidate_verification_target",
                "attest_governing_base_verification_target", "attest_host_adapter_capability",
                "attest_verification_governing_runtime", "authorization_effects_for_route",
                "build_pull_request_mutation_request", "commit_staged_change",
                "consume_authorization", "consume_lease_recovery_authorization",
                "consume_lifecycle_observation", "create_remote_effect_context",
                "execute_pull_request_mutation", "frame_effect_authorization",
                "frame_lease_recovery_authorization", "observe_inventory",
                "observe_local_git_state", "observe_worktree_inventory", "parse_worktree_porcelain",
                "push_validated_feature", "recover_feature_push_outcome",
                "recover_pull_request_mutation_outcome", "stage_allowlisted_paths",
                "validate_github_observation", "validate_inventory_observation",
                "validate_local_git_observation", "validate_pull_request_body",
                "validate_pull_request_mutation", "validate_pull_request_title",
                "validate_release_provider_observation", "validate_remote_effect_context",
                "validate_worktree_inventory_observation",
            },
            "control_plane.lifecycle": {
                "CandidateAssuranceBootstrapAuthority", "CompletedVerificationCommand",
                "GoverningBaseBootstrapAuthority", "HostBoundVerificationEvidence",
                "LeaseLockToken", "TaskLease", "TaskStore", "VerificationExecutionContext",
                "VerificationExecutionReceipt", "VerificationTaskBootstrap",
                "bind_candidate_assurance_bootstrap_authority",
                "bind_governing_base_bootstrap_authority", "build_verification_task_envelope",
                "create_resource_receipt", "create_verification_execution_context",
                "create_verification_task_bootstrap", "frame_verification_supplemental_evidence_set",
                "publish_verification_supplemental_evidence", "run_verification_profile",
                "transition_allowed",
            },
            "control_plane.lockfile": {"LockIssue", "runtime_digest", "validate_lock"},
            "control_plane.policy": {
                "GoverningPolicy", "PolicyError", "PolicyIssue", "ProjectRemotePolicyDecision",
                "ProjectRemotePolicyUpdateDraft", "ProjectRemotePolicyUpdateReceipt",
                "RequiredCheckCandidate", "apply_project_remote_policy_update",
                "frame_project_remote_policy_decision", "load_governing_policy_from_runtime",
                "load_policy", "parse_required_check_selector", "project_remote_policy_update_plan",
                "validate_policy",
            },
            "control_plane.resource_registry": {
                "RegistryError", "RegistryIssue", "build_inventory", "load_registry",
                "registry_contract_digest", "validate_inventory", "validate_policy_references",
                "validate_registry",
            },
            "control_plane.routing": {"compact_route_manifest", "resolve_route", "verify_route"},
        }
        missing = {
            module_name: sorted(name for name in names if not hasattr(importlib.import_module(module_name), name))
            for module_name, names in baseline.items()
        }
        self.assertEqual(missing, {module_name: [] for module_name in baseline})

    def test_runtime_hooks_cover_the_closed_real_event_set(self) -> None:
        from control_plane.host_bridge import MACOS_HOOK_SMOKE_SCENARIOS

        config = json.loads((ROOT / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(config["hooks"]),
            {"UserPromptSubmit", "SessionStart", "PreToolUse", "Stop"},
        )
        self.assertEqual(config["hooks"]["SessionStart"][0]["matcher"], "compact")
        self.assertIn("sessionstart_compact_fallback", MACOS_HOOK_SMOKE_SCENARIOS)
        self.assertNotIn(
            "sessionstart_compact_to_post_compact",
            MACOS_HOOK_SMOKE_SCENARIOS,
        )

    def test_risk_status_exit_contract_is_exact(self) -> None:
        from control_plane.cli import _emit

        for status, expected in (("PASS", 0), ("FAIL", 1), ("UNKNOWN", 2)):
            payload = {
                "schema_version": 1,
                "command": "risk-status",
                "ok": status == "PASS",
                "status": status,
                "dimensions": {
                    "local": {"status": status, "checks": [], "errors": []},
                    "remote": {"status": "PASS", "checks": [], "errors": []},
                },
                "facts": {},
                "errors": [],
            }
            with self.subTest(status=status), redirect_stdout(StringIO()):
                self.assertEqual(_emit(payload, True), expected)

    def test_local_distribution_contains_no_deferred_remote_surface(self) -> None:
        forbidden_paths = (
            ".codex/templates/risk-sentinel.yml.tmpl",
            ".github/workflows/risk-sentinel.yml",
            "control_plane/github_provenance.py",
        )
        self.assertEqual([path for path in forbidden_paths if (ROOT / path).exists()], [])
        lock = (ROOT / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
        self.assertNotIn("risk_workflow", lock)
        self.assertNotIn("risk_workflow_template", lock)


if __name__ == "__main__":
    unittest.main()
