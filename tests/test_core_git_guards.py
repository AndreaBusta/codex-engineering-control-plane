from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from control_plane.git_guards import (
    _consume_validated_installed_policy,
    _protected_is_live,
    _validated_installed_policy_is_live,
    load_protected_git_policy,
    observe_installed_policy_source,
    validate_installed_policy_source,
)
from tests.git_test_support import FIXTURE_POLICY, GitScenario, git


REMOTE_URL = "https://github.com/example/control-plane.git"


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
