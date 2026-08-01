from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.git_test_support import GitScenario, git


ROOT = Path(__file__).parents[1]


def risk_check(dimension: object, code: str):
    return next(check for check in dimension.checks if check.code == code)




class RiskSentinelContractTests(unittest.TestCase):
    def test_status_precedence_is_fail_unknown_pass(self) -> None:
        from control_plane.risk_sentinel import aggregate_status

        self.assertEqual(aggregate_status(["PASS"]), "PASS")
        self.assertEqual(aggregate_status(["PASS", "UNKNOWN"]), "UNKNOWN")
        self.assertEqual(
            aggregate_status(["PASS", "UNKNOWN", "FAIL"]), "FAIL"
        )
        self.assertEqual(aggregate_status([]), "PASS")

    def test_local_safe_remote_unobserved_is_unknown_not_pass(self) -> None:
        from control_plane.risk_sentinel import (
            RiskCheck,
            RiskDimension,
            RiskStatus,
        )

        local = RiskDimension(
            status="PASS",
            checks=(
                RiskCheck(
                    code="RS_LOCAL_POLICY",
                    status="PASS",
                    message="Governing policy is valid.",
                    facts={},
                ),
            ),
            errors=(),
        )
        remote = RiskDimension(
            status="UNKNOWN",
            checks=(),
            errors=(
                {
                    "code": "RS_REMOTE_NOT_OBSERVED",
                    "message": (
                        "Remote protection or provenance has not been observed."
                    ),
                },
            ),
        )
        result = RiskStatus(
            command="risk-status",
            dimensions={"local": local, "remote": remote},
            facts={},
            errors=(),
        )

        self.assertEqual(result.status, "UNKNOWN")
        self.assertFalse(result.to_dict()["ok"])
        self.assertEqual(
            result.dimensions["remote"].errors[0]["code"],
            "RS_REMOTE_NOT_OBSERVED",
        )
        self.assertEqual(
            set(result.to_dict()),
            {
                "schema_version",
                "command",
                "ok",
                "status",
                "dimensions",
                "facts",
                "errors",
            },
        )

    def test_invalid_status_is_rejected(self) -> None:
        from control_plane.risk_sentinel import aggregate_status

        with self.assertRaisesRegex(ValueError, "RS_STATUS"):
            aggregate_status(["GREEN"])

    def test_candidate_policy_cannot_make_local_risk_pass(self) -> None:
        from control_plane.risk_sentinel import evaluate_local_risk
        from control_plane.policy import load_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        candidate = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )

        result = evaluate_local_risk(scenario.repo, candidate)

        self.assertEqual(
            risk_check(result, "RS_LOCAL_POLICY").status, "FAIL"
        )
        self.assertNotEqual(result.status, "PASS")

    def test_installed_guard_snapshot_binds_hooks_base_and_remote(self) -> None:
        from types import SimpleNamespace

        from control_plane.risk_sentinel import (
            FAIL,
            PASS,
            _installed_guard_snapshot,
        )
        from tests.test_git_guards import InstalledGuardScenario

        scenario = InstalledGuardScenario()
        self.addCleanup(scenario.close)
        policy = scenario.load()
        hooks_path = str(
            scenario.common_dir
            / "codex-control-plane"
            / "installs"
            / scenario.manifest_digest
            / "git-hooks"
        )

        self.assertEqual(
            _installed_guard_snapshot(
                scenario.repo, (hooks_path,), policy
            ),
            (hooks_path, PASS),
        )
        bindings = {
            "policy_digest": policy.policy_digest,
            "lock_digest": policy.lock_digest,
            "runtime_digest": policy.runtime_digest,
            "governing_base_commit": policy.governing_base_commit,
            "remote_repository": policy.remote_repository,
        }
        for field, value in (
            ("governing_base_commit", "0" * 40),
            ("remote_repository", "other/repository"),
        ):
            with self.subTest(field=field):
                mismatched = SimpleNamespace(
                    **{**bindings, field: value}
                )
                self.assertEqual(
                    _installed_guard_snapshot(
                        scenario.repo, (hooks_path,), mismatched
                    ),
                    (None, FAIL),
                )

        (Path(hooks_path) / "pre-push").write_text(
            "#!/bin/sh\nexit 1\n", encoding="utf-8"
        )
        self.assertEqual(
            _installed_guard_snapshot(
                scenario.repo, (hooks_path,), policy
            ),
            (None, FAIL),
        )

    def test_serialized_decision_cannot_make_authority_or_clarification_pass(
        self,
    ) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "1" * 64,
            lock_digest="sha256:" + "2" * 64,
            governing_base_commit="a" * 40,
            session_id="session-risk-tests",
            invocation_id="invocation-risk-tests",
            freshness_deadline=130.0,
        )
        task = {
            "task_id": "task-risk-tests",
            "task_digest": "sha256:" + "3" * 64,
            "state": "framed",
            "generation": 0,
        }
        hint = {
            "authorization": {"local_write": True},
            "interaction": {
                "recommended_mode": "plan",
                "reason_codes": ["MODE_COMPLEX_OR_UNCERTAIN"],
                "clarification_gate": {"status": "resolved"},
            },
        }

        result = evaluate_local_risk(
            scenario.repo,
            policy,
            task_state=task,
            route_decision_hint=hint,
        )

        self.assertEqual(
            risk_check(result, "RS_CLARIFICATION_REQUIRED").status,
            "UNKNOWN",
        )
        self.assertEqual(
            risk_check(result, "RS_AUTHORITY_REQUIRED").status, "UNKNOWN"
        )

    def test_authority_is_not_applicable_only_without_task(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "4" * 64,
            lock_digest="sha256:" + "5" * 64,
            governing_base_commit="b" * 40,
            session_id="session-risk-tests",
            invocation_id="invocation-risk-tests",
            freshness_deadline=130.0,
        )

        result = evaluate_local_risk(scenario.repo, policy)
        authority = risk_check(result, "RS_AUTHORITY_REQUIRED")

        self.assertEqual(authority.status, "PASS")
        self.assertEqual(authority.facts["reason"], "NOT_APPLICABLE")

    def test_local_dimension_uses_exact_normative_check_vocabulary(
        self,
    ) -> None:
        from control_plane.risk_sentinel import evaluate_local_risk

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()

        result = evaluate_local_risk(scenario.repo, None)

        self.assertEqual(
            {check.code for check in result.checks},
            {
                "RS_LOCAL_POLICY",
                "RS_LOCAL_LOCK",
                "RS_LOCAL_REPOSITORY",
                "RS_LOCAL_BASE_BRANCH",
                "RS_LOCAL_DETACHED",
                "RS_LOCAL_BASE_DIVERGENCE",
                "RS_LOCAL_DIRTY",
                "RS_LOCAL_HOOK_PATH",
                "RS_LOCAL_HOOK_DIGEST",
                "RS_HOOK_TRUST",
                "RS_HOOK_MODE",
                "RS_CLARIFICATION_REQUIRED",
                "RS_AUTHORITY_REQUIRED",
                "RS_PROFILE",
                "RS_TASK_STATE",
            },
        )
        self.assertEqual(len(result.checks), 15)

    def test_git_observation_failure_becomes_unknown(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.policy import load_policy
        from control_plane.risk_sentinel import evaluate_local_risk
        from tests.host_adapter_test_support import governing_policy

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()
        mapping = load_policy(
            ROOT / "tests" / "fixtures" / "valid-policy.toml"
        )
        policy = governing_policy(
            policy=mapping,
            policy_digest=contract_digest(mapping),
            runtime_digest="sha256:" + "6" * 64,
            lock_digest="sha256:" + "7" * 64,
            governing_base_commit="c" * 40,
            session_id="session-risk-tests",
            invocation_id="invocation-risk-tests",
            freshness_deadline=130.0,
        )

        with patch(
            "control_plane.risk_sentinel.evaluate_preflight",
            side_effect=OSError("git unavailable"),
        ):
            result = evaluate_local_risk(scenario.repo, policy)

        self.assertEqual(
            risk_check(result, "RS_LOCAL_REPOSITORY").status, "UNKNOWN"
        )
        self.assertTrue(
            any(check.status == "UNKNOWN" for check in result.checks)
        )

    def test_unanchored_cli_path_observes_only_anchor_independent_facts(
        self,
    ) -> None:
        from control_plane.risk_sentinel import evaluate_local_risk

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature()

        result = evaluate_local_risk(scenario.repo, None)

        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(
            risk_check(result, "RS_LOCAL_POLICY").status, "UNKNOWN"
        )
        self.assertEqual(
            risk_check(result, "RS_LOCAL_REPOSITORY").status, "PASS"
        )
        self.assertEqual(
            risk_check(result, "RS_LOCAL_DETACHED").status, "PASS"
        )
        self.assertEqual(risk_check(result, "RS_LOCAL_DIRTY").status, "PASS")
        self.assertEqual(risk_check(result, "RS_PROFILE").status, "PASS")
        authority = risk_check(result, "RS_AUTHORITY_REQUIRED")
        self.assertEqual(authority.status, "PASS")
        self.assertEqual(authority.facts["reason"], "NOT_APPLICABLE")


    def test_lease_anchor_uses_the_record_validated_under_flock(self) -> None:
        from control_plane.contracts import contract_digest
        from control_plane.lifecycle import TaskLease, TaskStore
        from control_plane.repository import worktree_git_dir
        from control_plane.risk_sentinel import _lease_coverage

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        scenario.checkout_feature("codex/risk-lease-race")
        branch = "codex/risk-lease-race"
        mapping = scenario.policy()
        policy_digest = contract_digest(mapping)
        state_dir = worktree_git_dir(scenario.repo)
        state = TaskStore(state_dir).start(
            "task-risk-lease-race",
            outcome="local_change",
            branch=branch,
            task_digest=contract_digest({"task": "lease-race"}),
            decision_digest=contract_digest({"decision": "lease-race"}),
        )
        lease = TaskLease.acquire(
            state_dir,
            task_id=str(state["task_id"]),
            worktree=str(scenario.repo),
            branch=branch,
            session_id="session-risk-lease-race",
            paths=["dirty.txt"],
            policy_digest=policy_digest,
        )
        (scenario.repo / "dirty.txt").write_text(
            "leased\n", encoding="utf-8"
        )
        host_evidence = {
            "task_id": state["task_id"],
            "task_digest": state["task_digest"],
            "task_state_digest": contract_digest(state),
            "decision_digest": state["decision_digest"],
            "branch": branch,
            "lease_digest": lease["lease_digest"],
            "session_id": "session-risk-lease-race",
        }
        replacement = {
            **lease,
            "lease_digest": "sha256:" + "f" * 64,
        }

        exact = _lease_coverage(
            scenario.repo,
            task_state=state,
            policy=mapping,
            branch=branch,
            host_evidence=host_evidence,
        )
        contradictory = _lease_coverage(
            scenario.repo,
            task_state=state,
            policy=mapping,
            branch=branch,
            host_evidence={
                **host_evidence,
                "session_id": "session-risk-lease-other",
            },
            local_session_id="session-risk-lease-race",
        )

        with patch.object(
            TaskLease,
            "validate",
            return_value=replacement,
        ):
            coverage = _lease_coverage(
                scenario.repo,
                task_state=state,
                policy=mapping,
                branch=branch,
                host_evidence=host_evidence,
            )

        self.assertEqual(exact, "host_attested")
        self.assertEqual(contradictory, "invalid")
        self.assertEqual(coverage, "invalid")

        with patch(
            "control_plane.risk_sentinel._git_changed_paths",
            return_value=None,
        ):
            unobservable = _lease_coverage(
                scenario.repo,
                task_state=state,
                policy=mapping,
                branch=branch,
                host_evidence=None,
                local_session_id="session-risk-lease-race",
            )
        self.assertEqual(unobservable, "unobservable")


    def test_task6_has_no_installed_policy_or_mapping_shortcut(self) -> None:
        import control_plane.host_bridge as bridge

        for candidate in (
            "ValidatedInstalledPolicyObservation",
            {"policy": ".codex/project-policy.toml"},
            object(),
        ):
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaisesRegex(
                    ValueError, "RS_LOCAL_BASE_UNKNOWN"
                ):
                    bridge.load_governing_local_policy(
                        canonical_repo=ROOT,
                        governing_base_observation=candidate,
                        expected_invocation_id="invocation-risk-base",
                        clock=lambda: 100.0,
                    )


if __name__ == "__main__":
    unittest.main()
