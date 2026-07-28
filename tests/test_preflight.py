from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
import subprocess
from unittest.mock import patch

from tests.git_test_support import GitScenario, create_unborn_repository, git


def error_codes(result: object) -> set[str]:
    return {error.code for error in result.errors}


class GitPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()

    def tearDown(self) -> None:
        self.scenario.close()

    def test_write_blocks_protected_base_branch(self) -> None:
        from control_plane.git_state import evaluate_preflight

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="write"
        )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_BASE_BRANCH", error_codes(result))

    def test_write_accepts_clean_feature_based_on_remote_base(self) -> None:
        from control_plane.git_state import evaluate_preflight

        self.scenario.checkout_feature()

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="write"
        )

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.facts["branch"], "feature/test")
        self.assertEqual(result.facts["behind"], 0)

    def test_write_blocks_dirty_tree(self) -> None:
        from control_plane.git_state import evaluate_preflight

        self.scenario.checkout_feature()
        (self.scenario.repo / "untracked file.txt").write_text(
            "dirty\n", encoding="utf-8"
        )

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="write"
        )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_DIRTY", error_codes(result))

    def test_write_blocks_detached_head(self) -> None:
        from control_plane.git_state import evaluate_preflight

        git(self.scenario.repo, "switch", "--detach")

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="write"
        )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_DETACHED", error_codes(result))

    def test_write_blocks_branch_behind_remote_base(self) -> None:
        from control_plane.git_state import evaluate_preflight

        self.scenario.checkout_feature()
        self.scenario.advance_remote_base()

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="write"
        )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_BEHIND_BASE", error_codes(result))
        self.assertEqual(result.facts["behind"], 1)

    def test_read_mode_diagnoses_unsafe_state_without_blocking(self) -> None:
        from control_plane.git_state import evaluate_preflight

        (self.scenario.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        git(self.scenario.repo, "switch", "--detach")

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="read"
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.facts["dirty"])
        self.assertTrue(result.facts["detached"])

    def test_alternative_base_branch_is_driven_by_policy(self) -> None:
        from control_plane.git_state import evaluate_preflight

        alternative = GitScenario(base_branch="develop")
        self.addCleanup(alternative.close)
        alternative.checkout_feature("feature/from-develop")

        result = evaluate_preflight(
            alternative.repo, alternative.policy(), mode="write"
        )

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.facts["base_branch"], "develop")

    def test_missing_remote_is_blocked_for_write(self) -> None:
        from control_plane.git_state import evaluate_preflight

        self.scenario.checkout_feature()
        git(self.scenario.repo, "remote", "remove", "origin")

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="write"
        )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_NO_REMOTE", error_codes(result))

    def test_unborn_repository_is_blocked_for_write(self) -> None:
        from control_plane.git_state import evaluate_preflight

        temp, repo = create_unborn_repository()
        self.addCleanup(temp.cleanup)

        result = evaluate_preflight(repo, self.scenario.policy(), mode="write")

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_UNBORN", error_codes(result))

    def test_release_accepts_clean_synced_base(self) -> None:
        from control_plane.git_state import evaluate_preflight

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="release"
        )

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.facts["ahead"], 0)
        self.assertEqual(result.facts["behind"], 0)

    def test_release_blocks_feature_branch(self) -> None:
        from control_plane.git_state import evaluate_preflight

        self.scenario.checkout_feature()

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="release"
        )

        self.assertFalse(result.ok)
        self.assertIn("E_RELEASE_WRONG_BRANCH", error_codes(result))

    def test_release_blocks_local_base_ahead_of_remote(self) -> None:
        from control_plane.git_state import evaluate_preflight

        (self.scenario.repo / "local-only.txt").write_text(
            "not pushed\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "local-only.txt")
        git(self.scenario.repo, "commit", "-m", "test: local only")

        result = evaluate_preflight(
            self.scenario.repo, self.scenario.policy(), mode="release"
        )

        self.assertFalse(result.ok)
        self.assertIn("E_RELEASE_NOT_SYNCED", error_codes(result))
        self.assertEqual(result.facts["ahead"], 1)

    def test_non_repository_has_stable_error(self) -> None:
        from control_plane.git_state import evaluate_preflight

        with tempfile.TemporaryDirectory() as temp_dir:
            result = evaluate_preflight(
                Path(temp_dir), self.scenario.policy(), mode="read"
            )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_NOT_REPOSITORY", error_codes(result))

    def test_nonexistent_repository_path_has_stable_error(self) -> None:
        from control_plane.git_state import evaluate_preflight

        result = evaluate_preflight(
            self.scenario.root / "does-not-exist",
            self.scenario.policy(),
            mode="read",
        )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_NOT_REPOSITORY", error_codes(result))

    def test_invalid_mode_is_rejected_by_api(self) -> None:
        from control_plane.git_state import evaluate_preflight

        with self.assertRaises(ValueError):
            evaluate_preflight(
                self.scenario.repo, self.scenario.policy(), mode="unknown"
            )

    def test_status_failure_cannot_be_misread_as_clean(self) -> None:
        import control_plane.git_state as git_state

        self.scenario.checkout_feature()
        real_git = git_state._git

        def fail_status(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
            if arguments and arguments[0] == "status":
                return subprocess.CompletedProcess(
                    ["git", *arguments], 128, "", ""
                )
            return real_git(repo, *arguments)

        with patch("control_plane.git_state._git", side_effect=fail_status):
            result = git_state.evaluate_preflight(
                self.scenario.repo, self.scenario.policy(), mode="write"
            )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_STATUS_FAILED", error_codes(result))

    def test_divergence_failure_cannot_pass_write_gate(self) -> None:
        import control_plane.git_state as git_state

        self.scenario.checkout_feature()
        real_git = git_state._git

        def fail_divergence(
            repo: Path, *arguments: str
        ) -> subprocess.CompletedProcess[str]:
            if arguments and arguments[0] == "rev-list":
                return subprocess.CompletedProcess(
                    ["git", *arguments], 128, "", ""
                )
            return real_git(repo, *arguments)

        with patch("control_plane.git_state._git", side_effect=fail_divergence):
            result = git_state.evaluate_preflight(
                self.scenario.repo, self.scenario.policy(), mode="write"
            )

        self.assertFalse(result.ok)
        self.assertIn("E_GIT_DIVERGENCE_UNKNOWN", error_codes(result))


if __name__ == "__main__":
    unittest.main()
