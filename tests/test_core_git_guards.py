from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from control_plane.git_guards import (
    _consume_validated_installed_policy,
    _protected_is_live,
    _validated_installed_policy_is_live,
    guard_pre_push,
    load_protected_git_policy,
    observe_installed_policy_source,
    validate_installed_policy_source,
)
from control_plane.survey import survey_repository
from tests.git_test_support import FIXTURE_POLICY, GitScenario, git


REMOTE_URL = "https://github.com/example/control-plane.git"
ZERO_OID = "0" * 40


def _digest(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


class _MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value
        self.error: Exception | None = None

    def __call__(self) -> float:
        if self.error is not None:
            raise self.error
        return self.value


class _InstalledGuardScenario:
    def __init__(self) -> None:
        self.git = GitScenario()
        self.repo = self.git.repo
        observed_common = Path(git(self.repo, "rev-parse", "--git-common-dir"))
        if not observed_common.is_absolute():
            observed_common = self.repo / observed_common
        self.common_dir = observed_common.resolve()
        git(self.repo, "remote", "set-url", "origin", REMOTE_URL)
        (self.repo / ".codex").mkdir()
        shutil.copyfile(
            FIXTURE_POLICY,
            self.repo / ".codex" / "project-policy.toml",
        )
        self.install_invocation_id = "core-seal-install"
        self.manifest_digest = self._install_snapshot()

    def close(self) -> None:
        self.git.close()

    def _install_snapshot(self) -> str:
        staging = self.common_dir / "codex-control-plane" / "staging"
        files = {
            "policy": (
                "policy/project-policy.toml",
                FIXTURE_POLICY.read_bytes(),
                0o600,
            ),
            "lock": (
                "control-plane.lock",
                b'{"schema_version":1,"runtime_layout":"isolated"}\n',
                0o600,
            ),
            "runtime_entrypoint": (
                "scripts/control-plane",
                b"#!/bin/sh\nexit 0\n",
                0o700,
            ),
            "runtime_module": (
                "control_plane/__init__.py",
                b"",
                0o600,
            ),
            "hook_pre_commit": (
                "git-hooks/pre-commit",
                b"#!/bin/sh\nexit 0\n",
                0o700,
            ),
            "hook_pre_push": (
                "git-hooks/pre-push",
                b"#!/bin/sh\nexit 0\n",
                0o700,
            ),
        }
        artifacts: list[dict[str, object]] = []
        for role, (relative, payload, mode) in files.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(mode)
            artifacts.append(
                {
                    "role": role,
                    "path": relative,
                    "digest": _digest(payload),
                    "mode": mode,
                }
            )
        manifest = {
            "schema_version": 1,
            "repository_identity": str(self.common_dir),
            "common_git_dir": str(self.common_dir),
            "source_commit": git(self.repo, "rev-parse", "HEAD"),
            "governing_base_commit": git(
                self.repo,
                "rev-parse",
                "refs/remotes/origin/main",
            ),
            "install_invocation_id": self.install_invocation_id,
            "git": {
                "base_branch": "main",
                "remote_name": "origin",
                "remote_url_digest": _digest(REMOTE_URL.encode("utf-8")),
                "remote_repository": "example/control-plane",
            },
            "artifacts": sorted(artifacts, key=lambda item: str(item["path"])),
        }
        manifest_bytes = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        digest = _digest(manifest_bytes)
        install = self.common_dir / "codex-control-plane" / "installs" / digest
        install.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(install)
        manifest_path = install / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        manifest_path.chmod(0o600)
        return digest

    def load(self, *, invocation_id: str, clock):
        return load_protected_git_policy(
            canonical_repo=self.repo,
            common_git_dir=self.common_dir,
            installed_manifest_digest=self.manifest_digest,
            invocation_id=invocation_id,
            clock=clock,
        )


class CoreGitGuardUnpublishedBranchTests(unittest.TestCase):
    BRANCH = "feature/unpublished"

    def setUp(self) -> None:
        self.scenario = _InstalledGuardScenario()
        self.repo = self.scenario.repo
        git(self.repo, "add", ".codex/project-policy.toml")
        git(self.repo, "commit", "-m", "test: install policy fixture")
        git(
            self.repo,
            "update-ref",
            "refs/remotes/origin/main",
            git(self.repo, "rev-parse", "HEAD"),
        )

    def tearDown(self) -> None:
        self.scenario.close()

    def _create_unpublished_branch(
        self,
        name: str | None = None,
    ) -> str:
        branch = name or self.BRANCH
        git(self.repo, "switch", "-c", branch)
        (self.repo / "baseline.txt").write_text(
            f"unpublished {branch}\n", encoding="utf-8"
        )
        git(self.repo, "add", "baseline.txt")
        git(self.repo, "commit", "-m", f"test: unpublished {branch}")
        return git(self.repo, "rev-parse", "HEAD")

    def _unrelated_update(self) -> tuple[str, str, str, str]:
        base = git(self.repo, "rev-parse", "refs/remotes/origin/main")
        return (
            "refs/heads/main",
            base,
            "refs/heads/feature/other",
            ZERO_OID,
        )

    def _guard(
        self, updates: list[tuple[str, str, str, str]]
    ) -> dict[str, object]:
        return guard_pre_push(
            self.repo,
            self.scenario.load(
                invocation_id="phase2-unpublished-branch",
                clock=lambda: 100.0,
            ),
            remote_name="origin",
            remote_url=REMOTE_URL,
            updates=updates,
        )

    def _survey_branch(self):
        survey = survey_repository(self.repo, base="origin/main")
        branches = {branch.name: branch for branch in survey.branches}
        self.assertIn(self.BRANCH, branches)
        return survey, branches[self.BRANCH]

    def test_pre_push_blocks_unique_unpublished_branch_on_passing_survey(
        self,
    ) -> None:
        self._create_unpublished_branch()

        survey, branch = self._survey_branch()
        self.assertEqual(survey.status, "PASS")
        self.assertEqual(branch.only_in_branch, 0)
        self.assertFalse(branch.content_equivalent_to_base)

        payload = self._guard([self._unrelated_update()])

        self.assertFalse(payload["ok"])
        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_UNIQUE_BRANCH"],
        )

    def test_pre_push_accepts_valid_git_branch_names(self) -> None:
        for branch in (
            "feature/c++",
            "feature/release@2",
            "feature/mañana",
        ):
            with self.subTest(branch=branch):
                self._create_unpublished_branch(branch)
                try:
                    payload = self._guard([self._unrelated_update()])

                    self.assertFalse(payload["ok"])
                    self.assertEqual(
                        [error["code"] for error in payload["errors"]],
                        ["GG_UNPUBLISHED_UNIQUE_BRANCH"],
                    )
                finally:
                    git(self.repo, "switch", "main")
                    git(self.repo, "branch", "-D", branch)

    def test_pre_push_allows_exact_same_branch_publication(self) -> None:
        head = self._create_unpublished_branch()
        ref = f"refs/heads/{self.BRANCH}"

        payload = self._guard([(ref, head, ref, ZERO_OID)])

        self.assertTrue(payload["ok"], payload)

    def test_pre_push_allows_matching_remote_tracking_ref_even_when_behind(
        self,
    ) -> None:
        head = self._create_unpublished_branch()
        base = git(self.repo, "rev-parse", "refs/remotes/origin/main")
        git(
            self.repo,
            "update-ref",
            f"refs/remotes/origin/{self.BRANCH}",
            base,
        )
        self.assertNotEqual(head, base)

        payload = self._guard([self._unrelated_update()])

        self.assertTrue(payload["ok"], payload)

    def test_pre_push_fails_closed_when_exact_tracking_ref_is_not_a_commit(
        self,
    ) -> None:
        head = self._create_unpublished_branch()
        tree = git(self.repo, "rev-parse", f"{head}^{{tree}}")
        git(
            self.repo,
            "update-ref",
            f"refs/remotes/origin/{self.BRANCH}",
            tree,
        )

        payload = self._guard([self._unrelated_update()])

        self.assertFalse(payload["ok"])
        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN"],
        )

    def test_pre_push_exact_remote_query_ignores_hundreds_of_children(
        self,
    ) -> None:
        branch = "release"
        self._create_unpublished_branch(branch)
        base = git(self.repo, "rev-parse", "refs/remotes/origin/main")
        for index in range(200):
            git(
                self.repo,
                "update-ref",
                f"refs/remotes/origin/{branch}/child-{index:03d}+valid",
                base,
            )
        observations: list[tuple[list[str], bytes]] = []
        real_run = subprocess.run

        def recording_run(*args, **kwargs):
            completed = real_run(*args, **kwargs)
            observations.append((list(args[0]), completed.stdout))
            return completed

        with patch(
            "control_plane.git_guards.subprocess.run",
            side_effect=recording_run,
        ):
            payload = self._guard([self._unrelated_update()])

        self.assertFalse(payload["ok"])
        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_UNIQUE_BRANCH"],
        )
        remote_observations = [
            item
            for item in observations
            if "for-each-ref" in item[0]
            and any("%(objecttype)" in argument for argument in item[0])
        ]
        self.assertEqual(len(remote_observations), 1)
        remote_command, remote_output = remote_observations[0]
        self.assertIn("--count", remote_command)
        count_index = remote_command.index("--count")
        self.assertEqual(remote_command[count_index + 1], "65")
        self.assertIn(
            "[r]efs/remotes/origin/release",
            remote_command,
        )
        observed_remote_refs = [
            record.split(b"\0", 1)[0].decode("utf-8")
            for record in remote_output.splitlines()
        ]
        self.assertEqual(
            observed_remote_refs,
            ["refs/remotes/origin/main"],
        )

    def test_pre_push_allows_ahead_branch_with_base_equivalent_content(
        self,
    ) -> None:
        self._create_unpublished_branch()
        (self.repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
        git(self.repo, "add", "baseline.txt")
        git(self.repo, "commit", "-m", "test: restore base content")

        survey, branch = self._survey_branch()
        self.assertEqual(
            git(
                self.repo,
                "rev-list",
                "--count",
                f"refs/remotes/origin/main..refs/heads/{self.BRANCH}",
            ),
            "2",
        )
        self.assertTrue(branch.content_equivalent_to_base)

        payload = self._guard([self._unrelated_update()])

        self.assertTrue(payload["ok"], payload)

    def test_pre_push_fails_closed_when_remote_base_ref_disappears(self) -> None:
        protected = self.scenario.load(
            invocation_id="phase2-missing-remote-base",
            clock=lambda: 100.0,
        )
        update = self._unrelated_update()
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/main")

        payload = guard_pre_push(
            self.repo,
            protected,
            remote_name="origin",
            remote_url=REMOTE_URL,
            updates=[update],
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN"],
        )

    def test_pre_push_mismatched_oid_does_not_exempt_branch(self) -> None:
        self._create_unpublished_branch()
        base = git(self.repo, "rev-parse", "refs/remotes/origin/main")
        ref = f"refs/heads/{self.BRANCH}"

        payload = self._guard([(ref, base, ref, ZERO_OID)])

        self.assertFalse(payload["ok"])
        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_UNIQUE_BRANCH"],
        )

    def test_pre_push_still_evaluates_branches_when_untracked_makes_survey_fail(
        self,
    ) -> None:
        self._create_unpublished_branch()
        (self.repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

        survey, _ = self._survey_branch()
        self.assertEqual(survey.status, "FAIL")

        payload = self._guard([self._unrelated_update()])

        self.assertFalse(payload["ok"])
        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_UNIQUE_BRANCH"],
        )

    def test_pre_push_allows_valid_inventory_with_zero_local_branches(
        self,
    ) -> None:
        git(self.repo, "switch", "--detach")
        git(self.repo, "branch", "-D", "main")

        payload = self._guard([self._unrelated_update()])

        self.assertTrue(payload["ok"], payload)
        self.assertNotIn(
            "GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN",
            {error["code"] for error in payload["errors"]},
        )

    def test_pre_push_ignores_many_irrelevant_remote_tracking_refs(
        self,
    ) -> None:
        base = git(self.repo, "rev-parse", "refs/remotes/origin/main")
        for index in range(65):
            git(
                self.repo,
                "update-ref",
                f"refs/remotes/origin/noise/{index:02d}",
                base,
            )

        payload = self._guard([self._unrelated_update()])

        self.assertTrue(payload["ok"], payload)

    def test_pre_push_expired_aggregate_budget_stops_before_rev_list(
        self,
    ) -> None:
        self._create_unpublished_branch()
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(*args, **kwargs):
            commands.append(list(args[0]))
            return real_run(*args, **kwargs)

        clock_values = iter((100.0, 100.0, 100.0, 100.0, 106.0))

        def expired_clock() -> float:
            return next(clock_values, 106.0)

        with (
            patch("time.monotonic", side_effect=expired_clock),
            patch(
                "control_plane.git_guards.subprocess.run",
                side_effect=recording_run,
            ),
        ):
            payload = self._guard([self._unrelated_update()])

        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN"],
        )
        self.assertEqual(
            sum("for-each-ref" in command for command in commands),
            2,
        )
        self.assertFalse(any("rev-list" in command for command in commands))

    def test_pre_push_batches_unique_commit_observation_in_three_processes(
        self,
    ) -> None:
        for index in range(3):
            git(self.repo, "switch", "main")
            self._create_unpublished_branch(f"feature/unpublished-{index}")
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(*args, **kwargs):
            commands.append(list(args[0]))
            return real_run(*args, **kwargs)

        with patch(
            "control_plane.git_guards.subprocess.run",
            side_effect=recording_run,
        ):
            payload = self._guard([self._unrelated_update()])

        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_UNIQUE_BRANCH"],
        )
        self.assertEqual(
            sum("for-each-ref" in command for command in commands),
            2,
        )
        self.assertEqual(
            sum("rev-list" in command for command in commands),
            1,
        )
        observation_commands = [
            command
            for command in commands
            if "for-each-ref" in command or "rev-list" in command
        ]
        self.assertLessEqual(len(observation_commands), 3)
        rev_list = next(
            command for command in observation_commands if "rev-list" in command
        )
        self.assertIn("--max-count=1", rev_list)
        self.assertIn("--not", rev_list)
        self.assertIn("refs/remotes/origin/main", rev_list)
        for index in range(3):
            self.assertIn(
                f"refs/heads/feature/unpublished-{index}",
                rev_list,
            )

    def test_pre_push_does_not_use_ahead_behind_in_source_or_argv(self) -> None:
        self._create_unpublished_branch()
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(*args, **kwargs):
            commands.append(list(args[0]))
            return real_run(*args, **kwargs)

        with patch(
            "control_plane.git_guards.subprocess.run",
            side_effect=recording_run,
        ):
            self._guard([self._unrelated_update()])

        source = (
            Path(__file__).parents[1] / "control_plane" / "git_guards.py"
        ).read_text(encoding="utf-8")
        with self.subTest(location="source"):
            self.assertNotIn("ahead-behind", source)
        with self.subTest(location="argv"):
            self.assertFalse(
                any(
                    "ahead-behind" in argument
                    for command in commands
                    for argument in command
                )
            )

    def test_pre_push_allows_branch_with_no_commits_unique_to_base(
        self,
    ) -> None:
        base = git(self.repo, "rev-parse", "refs/remotes/origin/main")
        contained = git(self.repo, "rev-parse", f"{base}^")
        git(
            self.repo,
            "update-ref",
            "refs/heads/feature/contained",
            contained,
        )
        commands: list[list[str]] = []
        real_run = subprocess.run

        def recording_run(*args, **kwargs):
            commands.append(list(args[0]))
            return real_run(*args, **kwargs)

        with patch(
            "control_plane.git_guards.subprocess.run",
            side_effect=recording_run,
        ):
            payload = self._guard([self._unrelated_update()])

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(
            sum("rev-list" in command for command in commands),
            1,
        )

    def test_pre_push_fails_closed_on_ambiguous_rev_list_output(self) -> None:
        self._create_unpublished_branch()
        real_run = subprocess.run

        def ambiguous_rev_list(*args, **kwargs):
            command = list(args[0])
            if "rev-list" in command:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=b"ambiguous\noutput\n",
                    stderr=b"",
                )
            return real_run(*args, **kwargs)

        with patch(
            "control_plane.git_guards.subprocess.run",
            side_effect=ambiguous_rev_list,
        ):
            payload = self._guard([self._unrelated_update()])

        self.assertFalse(payload["ok"])
        self.assertEqual(
            [error["code"] for error in payload["errors"]],
            ["GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN"],
        )


class CoreGitGuardClockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = _InstalledGuardScenario()

    def tearDown(self) -> None:
        self.scenario.close()

    def _observation(self, *, invocation_id: str, clock):
        protected = self.scenario.load(
            invocation_id=invocation_id,
            clock=clock,
        )
        return observe_installed_policy_source(
            protected_policy=protected,
            canonical_repo=self.scenario.repo,
            expected_manifest_digest=self.scenario.manifest_digest,
            session_id="core-seal-session",
            invocation_id=invocation_id,
            clock=clock,
            ttl_seconds=30.0,
        )

    def test_protected_policy_rejects_replaced_raising_and_nonfinite_clocks(
        self,
    ) -> None:
        clock = lambda: 100.0
        replaced = self.scenario.load(
            invocation_id="clock-replaced",
            clock=clock,
        )
        replaced._clock = lambda: -1_000_000.0
        self.assertFalse(_protected_is_live(replaced))

        raising_clock = _MutableClock(100.0)
        raising = self.scenario.load(
            invocation_id="clock-raising",
            clock=raising_clock,
        )
        raising_clock.error = RuntimeError("clock unavailable")
        self.assertFalse(_protected_is_live(raising))

        nonfinite_clock = _MutableClock(100.0)
        nonfinite = self.scenario.load(
            invocation_id="clock-nonfinite",
            clock=nonfinite_clock,
        )
        nonfinite_clock.value = float("-inf")
        self.assertFalse(_protected_is_live(nonfinite))

    def test_installed_observation_rejects_substituted_or_mutated_clock(self) -> None:
        clock = lambda: 100.0
        substituted = self._observation(
            invocation_id="observation-substituted",
            clock=clock,
        )
        with self.assertRaisesRegex(
            ValueError,
            "GG_INSTALLED_POLICY_OBSERVATION",
        ):
            validate_installed_policy_source(
                substituted,
                expected_repository_identity=self.scenario.repo,
                expected_manifest_digest=self.scenario.manifest_digest,
                expected_session_id="core-seal-session",
                expected_invocation_id="observation-substituted",
                clock=lambda: 100.0,
            )

        mutated = self._observation(
            invocation_id="observation-mutated",
            clock=clock,
        )
        mutated._clock = lambda: -1_000_000.0
        with self.assertRaisesRegex(
            ValueError,
            "GG_INSTALLED_POLICY_OBSERVATION",
        ):
            validate_installed_policy_source(
                mutated,
                expected_repository_identity=self.scenario.repo,
                expected_manifest_digest=self.scenario.manifest_digest,
                expected_session_id="core-seal-session",
                expected_invocation_id="observation-mutated",
                clock=clock,
            )

    def test_validated_observation_consume_rechecks_clock_and_freshness(self) -> None:
        now = [100.0]

        def clock() -> float:
            return now[0]

        observation = self._observation(
            invocation_id="validated-clock",
            clock=clock,
        )
        validated = validate_installed_policy_source(
            observation,
            expected_repository_identity=self.scenario.repo,
            expected_manifest_digest=self.scenario.manifest_digest,
            expected_session_id="core-seal-session",
            expected_invocation_id="validated-clock",
            clock=clock,
        )
        self.assertTrue(
            _validated_installed_policy_is_live(validated, clock=clock)
        )

        now[0] = 131.0
        self.assertFalse(
            _consume_validated_installed_policy(validated, clock=clock)
        )
        self.assertFalse(
            _validated_installed_policy_is_live(validated, clock=clock)
        )


if __name__ == "__main__":
    unittest.main()
