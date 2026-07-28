from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "valid-policy.toml"


class PolicyContractTests(unittest.TestCase):
    def test_loads_valid_policy(self) -> None:
        from control_plane.policy import load_policy

        policy = load_policy(FIXTURE)

        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(policy["git"]["base_branch"], "main")
        self.assertEqual(policy["reasoning"]["normal_max_workers"], 2)

    def test_missing_policy_has_stable_error_code(self) -> None:
        from control_plane.policy import PolicyError, load_policy

        with self.assertRaises(PolicyError) as caught:
            load_policy(Path("/definitely/missing/project-policy.toml"))

        self.assertEqual(caught.exception.code, "E_POLICY_NOT_FOUND")

    def test_malformed_policy_has_stable_error_code(self) -> None:
        from control_plane.policy import PolicyError, load_policy

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "policy.toml"
            path.write_text("[git\nbase_branch = 'main'\n", encoding="utf-8")

            with self.assertRaises(PolicyError) as caught:
                load_policy(path)

        self.assertEqual(caught.exception.code, "E_POLICY_PARSE")

    def test_valid_policy_has_no_issues(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        self.assertEqual(validate_policy(load_policy(FIXTURE)), [])

    def test_missing_required_key_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        del policy["git"]["base_branch"]

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_MISSING", codes)

    def test_unknown_schema_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["schema_version"] = 99

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_SCHEMA", codes)

    def test_schema_version_requires_an_exact_integer(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        for invalid_value in (True, 1.0):
            with self.subTest(invalid_value=invalid_value):
                policy = copy.deepcopy(load_policy(FIXTURE))
                policy["schema_version"] = invalid_value

                codes = {issue.code for issue in validate_policy(policy)}

                self.assertIn("P_SCHEMA", codes)

    def test_project_identity_requires_nonempty_strings(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        invalid_values = ("", "   ", {"nested": "table"}, ["list"])
        for path, expected_code in (
            ("project_name", "P_PROJECT_NAME"),
            ("project_kind", "P_PROJECT_KIND"),
        ):
            for invalid_value in invalid_values:
                with self.subTest(path=path, invalid_value=invalid_value):
                    policy = copy.deepcopy(load_policy(FIXTURE))
                    policy[path] = invalid_value

                    codes = {issue.code for issue in validate_policy(policy)}

                    self.assertIn(expected_code, codes)

    def test_invalid_reasoning_level_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["reasoning"]["default"] = "automatic-magic"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_REASONING", codes)

    def test_more_than_two_normal_workers_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["reasoning"]["normal_max_workers"] = 3

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_WORKERS", codes)

    def test_pull_request_cannot_be_disabled(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["require_pull_request"] = False

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_PR_REQUIRED", codes)

    def test_direct_base_push_cannot_be_enabled(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["allow_direct_base_push"] = True

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_PUSH", codes)

    def test_official_release_must_use_remote_base(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["release"]["official_source"] = "current_worktree"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_RELEASE_SOURCE", codes)

    def test_remote_cannot_be_parsed_as_a_git_option(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["remote"] = "--upload-pack=unexpected"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_REMOTE", codes)

    def test_invalid_base_ref_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["base_branch"] = "main..unexpected"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_BRANCH", codes)

    def test_base_ref_component_cannot_start_with_dot(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["base_branch"] = ".main"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_BRANCH", codes)

    def test_base_ref_cannot_use_reserved_head_pseudoref(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["base_branch"] = "HEAD"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_BASE_BRANCH", codes)

    def test_unsafe_integration_strategy_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["integration_strategy"] = "direct-push"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_INTEGRATION", codes)

    def test_security_booleans_require_actual_safe_values(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["require_pull_request"] = "yes"
        policy["git"]["allow_direct_base_push"] = "no"
        policy["reasoning"]["sequential_default"] = "yes"
        policy["documentation"]["require_impact_assessment"] = "yes"
        policy["release"]["require_manifest"] = "yes"
        policy["release"]["allow_local_official_release"] = "no"

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertTrue(
            {
                "P_PR_REQUIRED",
                "P_BASE_PUSH",
                "P_SEQUENTIAL",
                "P_DOC_IMPACT",
                "P_RELEASE_MANIFEST",
                "P_LOCAL_RELEASE",
            }.issubset(codes)
        )

    def test_wrong_reasoning_type_is_rejected_without_crashing(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["reasoning"]["default"] = ["high"]

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_REASONING", codes)

    def test_unknown_policy_key_is_rejected(self) -> None:
        from control_plane.policy import load_policy, validate_policy

        policy = copy.deepcopy(load_policy(FIXTURE))
        policy["git"]["mystery_override"] = True

        codes = {issue.code for issue in validate_policy(policy)}

        self.assertIn("P_UNKNOWN", codes)


if __name__ == "__main__":
    unittest.main()
