from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tomllib
import unittest

import control_plane.host_bridge as bridge
from control_plane.contracts import contract_digest
from control_plane.git_guards import (
    guard_pre_commit,
    guard_pre_push,
    load_protected_git_policy,
    observe_installed_policy_source,
    validate_installed_policy_source,
)
from tests.git_test_support import FIXTURE_POLICY, GitScenario, git


ZERO_OID = "0" * 40
REMOTE_URL = "https://github.com/example/control-plane.git"
ROOT = Path(__file__).parents[1]


def _digest(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


class InstalledGuardScenario:
    def __init__(self) -> None:
        self.git = GitScenario()
        self.repo = self.git.repo
        observed_common_dir = Path(
            git(self.repo, "rev-parse", "--git-common-dir")
        )
        if not observed_common_dir.is_absolute():
            observed_common_dir = self.repo / observed_common_dir
        self.common_dir = observed_common_dir.resolve()
        git(self.repo, "remote", "set-url", "origin", REMOTE_URL)
        (self.repo / ".codex").mkdir()
        shutil.copyfile(
            FIXTURE_POLICY, self.repo / ".codex" / "project-policy.toml"
        )
        self.install_invocation_id = "install-task8"
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
                self.repo, "rev-parse", "refs/remotes/origin/main"
            ),
            "install_invocation_id": self.install_invocation_id,
            "git": {
                "base_branch": "main",
                "remote_name": "origin",
                "remote_url_digest": _digest(REMOTE_URL.encode("utf-8")),
                "remote_repository": "example/control-plane",
            },
            "artifacts": sorted(
                artifacts, key=lambda item: str(item["path"])
            ),
        }
        manifest_bytes = (
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
        digest = _digest(manifest_bytes)
        install = (
            self.common_dir
            / "codex-control-plane"
            / "installs"
            / digest
        )
        install.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(install)
        manifest_path = install / "manifest.json"
        manifest_path.write_bytes(manifest_bytes)
        manifest_path.chmod(0o600)
        return digest

    def load(self, invocation_id: str = "guard-invocation"):
        return load_protected_git_policy(
            canonical_repo=self.repo,
            common_git_dir=self.common_dir,
            installed_manifest_digest=self.manifest_digest,
            invocation_id=invocation_id,
            clock=lambda: 100.0,
        )


class GitGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = InstalledGuardScenario()

    def tearDown(self) -> None:
        self.scenario.close()

    def test_pre_commit_blocks_base_and_detached_but_allows_feature(self) -> None:
        base = guard_pre_commit(self.scenario.repo, self.scenario.load())
        self.assertFalse(base["ok"])
        self.assertEqual(base["errors"][0]["code"], "GG_BASE_COMMIT")

        self.scenario.git.checkout_feature()
        feature = guard_pre_commit(self.scenario.repo, self.scenario.load())
        self.assertTrue(feature["ok"])

        git(self.scenario.repo, "switch", "--detach")
        detached = guard_pre_commit(self.scenario.repo, self.scenario.load())
        self.assertFalse(detached["ok"])
        self.assertEqual(detached["errors"][0]["code"], "GG_DETACHED_HEAD")

    def test_versioned_launchers_are_closed_executable_templates(self) -> None:
        for name, action in (
            ("pre-commit", "pre-commit"),
            ("pre-push", "pre-push"),
        ):
            with self.subTest(name=name):
                path = ROOT / ".codex" / "git-hooks" / name
                payload = path.read_text(encoding="utf-8")
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
                self.assertTrue(payload.startswith("#!/bin/sh\nset -eu\n"))
                self.assertIn("__CONTROL_PLANE_ENTRYPOINT__", payload)
                self.assertIn(f"git-guard {action}", payload)
                self.assertIn("/usr/bin/git rev-parse --show-toplevel", payload)

    def test_governing_git_effect_uses_attested_semantic_policy_digest(
        self,
    ) -> None:
        from tests.host_adapter_test_support import (
            governing_runtime_observation,
        )

        policy_path = self.scenario.repo / ".codex" / "project-policy.toml"
        git(self.scenario.repo, "add", ".codex/project-policy.toml")
        git(self.scenario.repo, "commit", "-m", "test: governing policy")
        head = git(self.scenario.repo, "rev-parse", "HEAD")
        policy_bytes = policy_path.read_bytes()
        semantic_digest = contract_digest(
            tomllib.loads(policy_bytes.decode("utf-8"))
        )
        runtime = governing_runtime_observation(
            runtime_digest=_digest(b"runtime"),
            lock_digest=_digest(b"lock"),
            policy_digest=_digest(policy_bytes),
            attestor_worktree=str(self.scenario.repo),
            target_worktree=str(self.scenario.repo),
            governing_base_commit=head,
            runtime_layout="source",
            session_id="task8-policy-session",
            invocation_id="task8-policy-invocation",
            freshness_deadline=130.0,
        )

        derive_digest = getattr(
            bridge,
            "_governing_policy_contract_digest",
            lambda _: None,
        )
        self.assertNotEqual(runtime.policy_digest, semantic_digest)
        self.assertEqual(derive_digest(runtime), semantic_digest)

    def test_pre_push_blocks_every_base_update_and_deletion(self) -> None:
        head = git(self.scenario.repo, "rev-parse", "HEAD")
        for local_ref, local_oid in (
            ("refs/heads/main", head),
            ("(delete)", ZERO_OID),
        ):
            with self.subTest(local_ref=local_ref):
                payload = guard_pre_push(
                    self.scenario.repo,
                    self.scenario.load(),
                    remote_name="origin",
                    remote_url=REMOTE_URL,
                    updates=[
                        (
                            local_ref,
                            local_oid,
                            "refs/heads/main",
                            head,
                        )
                    ],
                )
                self.assertFalse(payload["ok"])
                self.assertIn(
                    "GG_BASE_PUSH",
                    {error["code"] for error in payload["errors"]},
                )

    def test_pre_push_allows_feature_fast_forward_create_and_delete(self) -> None:
        base = git(self.scenario.repo, "rev-parse", "HEAD")
        self.scenario.git.checkout_feature()
        (self.scenario.repo / "feature.txt").write_text(
            "feature\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "feature.txt")
        git(self.scenario.repo, "commit", "-m", "test: feature")
        feature = git(self.scenario.repo, "rev-parse", "HEAD")

        cases = (
            (
                "fast-forward",
                ("refs/heads/feature/test", feature, "refs/heads/feature/test", base),
            ),
            (
                "create",
                ("refs/heads/feature/test", feature, "refs/heads/feature/test", ZERO_OID),
            ),
            (
                "delete",
                ("(delete)", ZERO_OID, "refs/heads/feature/test", feature),
            ),
        )
        for name, update in cases:
            with self.subTest(case=name):
                payload = guard_pre_push(
                    self.scenario.repo,
                    self.scenario.load(),
                    remote_name="origin",
                    remote_url=REMOTE_URL,
                    updates=[update],
                )
                self.assertTrue(payload["ok"], payload)

    def test_pre_push_blocks_proven_non_fast_forward(self) -> None:
        self.scenario.git.checkout_feature()
        (self.scenario.repo / "remote.txt").write_text(
            "remote\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "remote.txt")
        git(self.scenario.repo, "commit", "-m", "test: remote feature")
        remote_oid = git(self.scenario.repo, "rev-parse", "HEAD")
        git(self.scenario.repo, "reset", "--hard", "main")
        (self.scenario.repo / "local.txt").write_text(
            "local\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "local.txt")
        git(self.scenario.repo, "commit", "-m", "test: local feature")
        local_oid = git(self.scenario.repo, "rev-parse", "HEAD")

        payload = guard_pre_push(
            self.scenario.repo,
            self.scenario.load(),
            remote_name="origin",
            remote_url=REMOTE_URL,
            updates=[
                (
                    "refs/heads/feature/test",
                    local_oid,
                    "refs/heads/feature/test",
                    remote_oid,
                )
            ],
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "GG_NON_FAST_FORWARD")

    def test_replace_refs_cannot_disguise_non_fast_forward(self) -> None:
        self.scenario.git.checkout_feature()
        (self.scenario.repo / "remote.txt").write_text(
            "remote\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "remote.txt")
        git(self.scenario.repo, "commit", "-m", "test: remote feature")
        remote_oid = git(self.scenario.repo, "rev-parse", "HEAD")
        git(self.scenario.repo, "reset", "--hard", "main")
        (self.scenario.repo / "local.txt").write_text(
            "local\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "local.txt")
        git(self.scenario.repo, "commit", "-m", "test: local feature")
        local_oid = git(self.scenario.repo, "rev-parse", "HEAD")
        local_tree = git(self.scenario.repo, "rev-parse", f"{local_oid}^{{tree}}")
        synthetic_oid = git(
            self.scenario.repo,
            "commit-tree",
            local_tree,
            "-p",
            remote_oid,
            "-m",
            "synthetic ancestry",
        )
        git(self.scenario.repo, "replace", local_oid, synthetic_oid)

        payload = guard_pre_push(
            self.scenario.repo,
            self.scenario.load(),
            remote_name="origin",
            remote_url=REMOTE_URL,
            updates=[
                (
                    "refs/heads/feature/test",
                    local_oid,
                    "refs/heads/feature/test",
                    remote_oid,
                )
            ],
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "GG_NON_FAST_FORWARD")

    def test_grafts_cannot_disguise_non_fast_forward(self) -> None:
        self.scenario.git.checkout_feature()
        (self.scenario.repo / "remote.txt").write_text(
            "remote\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "remote.txt")
        git(self.scenario.repo, "commit", "-m", "test: remote feature")
        remote_oid = git(self.scenario.repo, "rev-parse", "HEAD")
        git(self.scenario.repo, "reset", "--hard", "main")
        (self.scenario.repo / "local.txt").write_text(
            "local\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", "local.txt")
        git(self.scenario.repo, "commit", "-m", "test: local feature")
        local_oid = git(self.scenario.repo, "rev-parse", "HEAD")
        grafts = self.scenario.common_dir / "info" / "grafts"
        grafts.parent.mkdir(exist_ok=True)
        grafts.write_text(f"{local_oid} {remote_oid}\n", encoding="ascii")

        payload = guard_pre_push(
            self.scenario.repo,
            self.scenario.load(),
            remote_name="origin",
            remote_url=REMOTE_URL,
            updates=[
                (
                    "refs/heads/feature/test",
                    local_oid,
                    "refs/heads/feature/test",
                    remote_oid,
                )
            ],
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "GG_NON_FAST_FORWARD")

    def test_guard_consumes_every_update_and_rejects_bad_shapes(self) -> None:
        head = git(self.scenario.repo, "rev-parse", "HEAD")
        payload = guard_pre_push(
            self.scenario.repo,
            self.scenario.load(),
            remote_name="origin",
            remote_url=REMOTE_URL,
            updates=[
                (
                    "refs/heads/feature/new",
                    head,
                    "refs/heads/feature/new",
                    ZERO_OID,
                ),
                ("broken", "fields", "only"),
                (
                    "refs/heads/main",
                    head,
                    "refs/heads/main",
                    head,
                ),
            ],
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(
            {error["code"] for error in payload["errors"]},
            {"GG_BASE_PUSH", "GG_INPUT_INVALID"},
        )

        unobservable = guard_pre_push(
            self.scenario.repo,
            self.scenario.load(),
            remote_name="origin",
            remote_url=REMOTE_URL,
            updates=[
                (
                    "refs/heads/feature/missing",
                    "e" * 40,
                    "refs/heads/feature/missing",
                    "f" * 40,
                ),
                (
                    "refs/heads/main",
                    head,
                    "refs/heads/main",
                    head,
                ),
            ],
        )
        self.assertEqual(
            {error["code"] for error in unobservable["errors"]},
            {"GG_BASE_PUSH", "GG_GIT_STATE_UNOBSERVABLE"},
        )

    def test_remote_or_candidate_drift_cannot_weaken_installed_policy(self) -> None:
        candidate = self.scenario.repo / ".codex" / "project-policy.toml"
        candidate.write_text(
            candidate.read_text(encoding="utf-8").replace(
                'base_branch = "main"', 'base_branch = "trunk"'
            ),
            encoding="utf-8",
        )
        source_hook = self.scenario.repo / ".codex" / "git-hooks" / "pre-push"
        source_hook.parent.mkdir(parents=True)
        source_hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        head = git(self.scenario.repo, "rev-parse", "HEAD")

        payload = guard_pre_push(
            self.scenario.repo,
            self.scenario.load(),
            remote_name="fork",
            remote_url="https://example.invalid/fork.git",
            updates=[
                (
                    "refs/heads/main",
                    head,
                    "refs/heads/main",
                    head,
                )
            ],
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(
            {error["code"] for error in payload["errors"]},
            {
                "GG_BASE_PUSH",
                "GG_REMOTE_UNVERIFIED",
            },
        )
        self.assertEqual(
            {warning["code"] for warning in payload["warnings"]},
            {"GG_CANDIDATE_POLICY_DRIFT"},
        )

        self.scenario.git.checkout_feature()
        allowed = guard_pre_commit(
            self.scenario.repo, self.scenario.load()
        )
        self.assertTrue(allowed["ok"])
        self.assertEqual(
            {warning["code"] for warning in allowed["warnings"]},
            {"GG_CANDIDATE_POLICY_DRIFT"},
        )

    def test_invalid_manifest_or_artifact_fails_closed(self) -> None:
        install = (
            self.scenario.common_dir
            / "codex-control-plane"
            / "installs"
            / self.scenario.manifest_digest
        )
        (install / "git-hooks" / "pre-push").write_text(
            "#!/bin/sh\nexit 1\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "GG_INSTALLED_POLICY_INVALID"):
            self.scenario.load()

    def test_unmanifested_snapshot_entry_fails_closed(self) -> None:
        install = (
            self.scenario.common_dir
            / "codex-control-plane"
            / "installs"
            / self.scenario.manifest_digest
        )
        cache = install / "control_plane" / "__pycache__"
        cache.mkdir()
        (cache / "cli.cpython-311.pyc").write_bytes(b"untrusted-bytecode")

        with self.assertRaisesRegex(ValueError, "GG_INSTALLED_POLICY_INVALID"):
            self.scenario.load()

    def test_unobservable_git_state_and_policy_replay_fail_closed(self) -> None:
        protected = self.scenario.load()
        first = guard_pre_commit(self.scenario.repo, protected)
        replay = guard_pre_commit(self.scenario.repo, protected)
        other = guard_pre_commit(
            self.scenario.repo.parent, self.scenario.load()
        )

        self.assertFalse(first["ok"])
        self.assertEqual(first["errors"][0]["code"], "GG_BASE_COMMIT")
        self.assertFalse(replay["ok"])
        self.assertEqual(
            replay["errors"][0]["code"], "GG_INSTALLED_POLICY_INVALID"
        )
        self.assertFalse(other["ok"])
        self.assertEqual(
            other["errors"][0]["code"], "GG_GIT_STATE_UNOBSERVABLE"
        )

    def test_installed_policy_observation_is_manifest_bound_one_shot(self) -> None:
        invocation = "risk-invocation"
        observation = observe_installed_policy_source(
            protected_policy=self.scenario.load(invocation),
            canonical_repo=self.scenario.repo,
            expected_manifest_digest=self.scenario.manifest_digest,
            session_id="session-task8",
            invocation_id=invocation,
            clock=lambda: 100.0,
            ttl_seconds=30.0,
        )
        with self.assertRaisesRegex(
            ValueError, "GG_INSTALLED_POLICY_OBSERVATION"
        ):
            validate_installed_policy_source(
                observation,
                expected_repository_identity=self.scenario.repo,
                expected_manifest_digest=self.scenario.manifest_digest,
                expected_session_id="session-other",
                expected_invocation_id=invocation,
                clock=lambda: 100.0,
            )
        validated = validate_installed_policy_source(
            observation,
            expected_repository_identity=self.scenario.repo,
            expected_manifest_digest=self.scenario.manifest_digest,
            expected_session_id="session-task8",
            expected_invocation_id=invocation,
            clock=lambda: 100.0,
        )
        self.assertEqual(
            validated.manifest_digest, self.scenario.manifest_digest
        )
        with self.assertRaisesRegex(
            ValueError, "GG_INSTALLED_POLICY_OBSERVATION"
        ):
            validate_installed_policy_source(
                observation,
                expected_repository_identity=self.scenario.repo,
                expected_manifest_digest=self.scenario.manifest_digest,
                expected_session_id="session-task8",
                expected_invocation_id=invocation,
                clock=lambda: 100.0,
            )

    def test_installed_observation_loads_governing_policy_once(self) -> None:
        import control_plane.host_bridge as bridge

        invocation = "risk-governing-invocation"
        observation = observe_installed_policy_source(
            protected_policy=self.scenario.load(invocation),
            canonical_repo=self.scenario.repo,
            expected_manifest_digest=self.scenario.manifest_digest,
            session_id="session-task8",
            invocation_id=invocation,
            clock=lambda: 100.0,
            ttl_seconds=30.0,
        )
        validated = validate_installed_policy_source(
            observation,
            expected_repository_identity=self.scenario.repo,
            expected_manifest_digest=self.scenario.manifest_digest,
            expected_session_id="session-task8",
            expected_invocation_id=invocation,
            clock=lambda: 100.0,
        )
        governing = bridge.load_governing_local_policy(
            canonical_repo=self.scenario.repo,
            governing_base_observation=validated,
            expected_invocation_id=invocation,
            clock=lambda: 100.0,
        )
        self.assertEqual(governing.policy["git"]["base_branch"], "main")
        self.assertEqual(governing.remote_repository, "example/control-plane")
        self.assertEqual(governing.governing_base_commit, git(
            self.scenario.repo, "rev-parse", "HEAD"
        ))
        with self.assertRaisesRegex(ValueError, "RS_LOCAL_BASE_UNKNOWN"):
            bridge.load_governing_local_policy(
                canonical_repo=self.scenario.repo,
                governing_base_observation=validated,
                expected_invocation_id=invocation,
                clock=lambda: 100.0,
            )

    def test_installed_policy_observation_rejects_mapping_cross_repo_and_ttl(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "GG_INSTALLED_POLICY_OBSERVATION"
        ):
            validate_installed_policy_source(
                {"manifest_digest": self.scenario.manifest_digest},
                expected_repository_identity=self.scenario.repo,
                expected_manifest_digest=self.scenario.manifest_digest,
                expected_session_id="session-task8",
                expected_invocation_id="risk-invocation",
                clock=lambda: 100.0,
            )

        observation = observe_installed_policy_source(
            protected_policy=self.scenario.load("risk-invocation"),
            canonical_repo=self.scenario.repo,
            expected_manifest_digest=self.scenario.manifest_digest,
            session_id="session-task8",
            invocation_id="risk-invocation",
            clock=lambda: 100.0,
            ttl_seconds=1.0,
        )
        for repository, now in (
            (self.scenario.repo.parent, 100.0),
            (self.scenario.repo, 102.0),
        ):
            with self.subTest(repository=repository, now=now):
                with self.assertRaisesRegex(
                    ValueError, "GG_INSTALLED_POLICY_OBSERVATION"
                ):
                    validate_installed_policy_source(
                        observation,
                        expected_repository_identity=repository,
                        expected_manifest_digest=self.scenario.manifest_digest,
                        expected_session_id="session-task8",
                        expected_invocation_id="risk-invocation",
                        clock=lambda: now,
                    )


if __name__ == "__main__":
    unittest.main()
