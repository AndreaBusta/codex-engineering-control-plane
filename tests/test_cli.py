from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.git_test_support import FIXTURE_POLICY, GitScenario


ROOT = Path(__file__).parents[1]


def run_cli(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "control_plane.cli", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def install_fake_git(root: Path, marker: Path, output: str = "") -> Path:
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    executable = fake_bin / "git"
    executable.write_text(
        "#!/bin/sh\n"
        f": > {shlex.quote(str(marker))}\n"
        f"printf '%s\\n' {shlex.quote(output)}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return fake_bin


class CliContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = GitScenario()

    def tearDown(self) -> None:
        self.scenario.close()

    def test_policy_check_json_succeeds_for_valid_policy(self) -> None:
        result = run_cli(
            "policy-check", "--policy", str(FIXTURE_POLICY), "--json"
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "policy-check")
        self.assertEqual(payload["issues"], [])

    def test_policy_check_json_fails_for_missing_policy(self) -> None:
        result = run_cli(
            "policy-check",
            "--policy",
            str(self.scenario.root / "missing.toml"),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errors"][0]["code"], "E_POLICY_NOT_FOUND")

    def test_preflight_json_uses_nonzero_exit_for_blocked_write(self) -> None:
        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--offline",
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("E_GIT_BASE_BRANCH", codes)
        self.assertTrue(payload["offline"])

    def test_preflight_json_succeeds_for_clean_feature(self) -> None:
        self.scenario.checkout_feature()

        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--offline",
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["facts"]["branch"], "feature/test")

    def test_doctor_json_reports_local_prerequisites(self) -> None:
        result = run_cli(
            "doctor",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["facts"]["git_available"])
        self.assertTrue(payload["facts"]["python_compatible"])
        self.assertTrue(payload["facts"]["policy_valid"])
        self.assertTrue(payload["facts"]["git_repository"])

    def test_doctor_json_reports_tracked_materialization(self) -> None:
        result = run_cli("doctor", "--repo", str(ROOT), "--json")

        payload = json.loads(result.stdout)
        self.assertTrue(payload["facts"]["tracked_files_materialized"])
        self.assertEqual(payload["facts"]["dataless_tracked_files"], 0)
        self.assertEqual(payload["facts"]["materialization_status"], "PASS")

    def test_human_output_has_unambiguous_status(self) -> None:
        result = run_cli("policy-check", "--policy", str(FIXTURE_POLICY))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS policy-check", result.stdout)

    def test_online_preflight_handles_nonexistent_repository_as_json(self) -> None:
        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.root / "does-not-exist"),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertIn("E_GIT_NOT_REPOSITORY", codes)

    def test_online_fetch_failure_has_stable_error(self) -> None:
        self.scenario.checkout_feature()
        from tests.git_test_support import git

        git(
            self.scenario.repo,
            "remote",
            "set-url",
            "origin",
            str(self.scenario.root / "missing-remote.git"),
        )

        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--refresh",
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertIn("E_FETCH_FAILED", codes)

    def test_online_preflight_refreshes_local_remote_with_closed_git(self) -> None:
        self.scenario.checkout_feature()

        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--refresh",
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["offline"])

    def test_online_preflight_forwards_ephemeral_git_auth_config_to_fetch(self) -> None:
        from control_plane.cli import _refresh_remote_base
        from tests.git_test_support import git

        observed: dict[str, object] = {}
        actual_run = subprocess.run
        remote_url = "https://github.com/example/control-plane.git"
        git(self.scenario.repo, "remote", "set-url", "origin", remote_url)

        def observe(arguments, **kwargs):
            if "fetch" not in arguments:
                return actual_run(arguments, **kwargs)
            observed["arguments"] = tuple(str(item) for item in arguments)
            observed["environment"] = dict(kwargs["env"])
            return subprocess.CompletedProcess(
                arguments, 0, stdout="", stderr=""
            )

        environment = {
            "PATH": str(self.scenario.root / "untrusted-bin"),
            "GIT_DIR": str(self.scenario.root / "redirected.git"),
            "GIT_CONFIG_GLOBAL": str(
                self.scenario.root / "untrusted-global.config"
            ),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic masked-test-value",
        }
        with patch.dict(os.environ, environment, clear=True), patch(
            "control_plane.cli.subprocess.run", side_effect=observe
        ):
            error = _refresh_remote_base(
                self.scenario.repo, "origin", "main"
            )

        self.assertIsNone(error)
        arguments = observed["arguments"]
        self.assertEqual(arguments[0], "/usr/bin/git")
        self.assertIn(remote_url, arguments)
        self.assertNotIn("origin", arguments)
        for closed_config in (
            "credential.helper=",
            "core.askPass=",
            "http.proxy=",
            "http.cookieFile=",
            "http.followRedirects=false",
            "http.sslVerify=true",
            "protocol.ext.allow=never",
            f"http.{remote_url}.proxy=",
            f"http.{remote_url}.cookieFile=",
        ):
            self.assertIn(closed_config, arguments)
        child = observed["environment"]
        self.assertEqual(child["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(
            child["GIT_CONFIG_KEY_0"],
            f"http.{remote_url}.extraheader",
        )
        self.assertEqual(child["GIT_CONFIG_VALUE_0"], "")
        self.assertEqual(
            child["GIT_CONFIG_KEY_1"],
            f"http.{remote_url}.extraheader",
        )
        self.assertEqual(
            child["GIT_CONFIG_VALUE_1"],
            "AUTHORIZATION: basic masked-test-value",
        )
        self.assertNotIn("GIT_DIR", child)
        self.assertNotIn("GIT_CONFIG_PARAMETERS", child)
        self.assertEqual(child["PATH"], "/usr/bin:/bin")
        self.assertEqual(child["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(child["GIT_CONFIG_SYSTEM"], "/dev/null")
        self.assertEqual(child["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_online_refresh_drops_non_auth_sensitive_environment(self) -> None:
        from control_plane.cli import _refresh_remote_base
        from tests.git_test_support import git

        actual_run = subprocess.run
        remote_url = "https://github.com/example/control-plane.git"
        git(self.scenario.repo, "remote", "set-url", "origin", remote_url)
        observed: dict[str, str] = {}

        def observe(arguments, **kwargs):
            if "fetch" not in arguments:
                return actual_run(arguments, **kwargs)
            observed.update(kwargs["env"])
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        sensitive = {
            "AWS_SECRET_ACCESS_KEY": "canary-not-a-secret",
            "GH_TOKEN": "canary-not-a-secret",
            "HTTPS_PROXY": "http://canary.invalid",
            "GIT_EXEC_PATH": str(self.scenario.root / "untrusted-git-exec"),
            "GIT_SSH_COMMAND": "untrusted-ssh-command",
            "SSH_AUTH_SOCK": str(self.scenario.root / "untrusted-agent.sock"),
        }
        with patch.dict(os.environ, sensitive, clear=True), patch(
            "control_plane.cli.subprocess.run", side_effect=observe
        ):
            error = _refresh_remote_base(self.scenario.repo, "origin", "main")

        self.assertIsNone(error)
        self.assertTrue(observed)
        self.assertFalse(set(sensitive).intersection(observed))
        self.assertEqual(observed["PATH"], "/usr/bin:/bin")

    def test_online_refresh_rejects_unbound_git_config_before_fetch(self) -> None:
        from control_plane.cli import _refresh_remote_base
        from tests.git_test_support import git

        actual_run = subprocess.run
        remote_url = "https://github.com/example/control-plane.git"
        git(self.scenario.repo, "remote", "set-url", "origin", remote_url)

        valid_auth = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic masked-test-value",
        }
        cases = (
            (
                "arbitrary key",
                {
                    **valid_auth,
                    "GIT_CONFIG_KEY_0": "core.sshCommand",
                },
            ),
            (
                "different URL",
                {
                    **valid_auth,
                    "GIT_CONFIG_KEY_0": (
                        "http.https://attacker.invalid/.extraheader"
                    ),
                },
            ),
            (
                "config parameters",
                {
                    **valid_auth,
                    "GIT_CONFIG_PARAMETERS": "'protocol.ext.allow=always'",
                },
            ),
            (
                "unknown indexed variable",
                {
                    **valid_auth,
                    "GIT_CONFIG_KEY_X": "http.https://github.com/.extraheader",
                },
            ),
        )
        for name, environment in cases:
            with self.subTest(case=name):
                fetch_called = False

                def observe(arguments, **kwargs):
                    nonlocal fetch_called
                    if "fetch" not in arguments:
                        return actual_run(arguments, **kwargs)
                    fetch_called = True
                    return subprocess.CompletedProcess(
                        arguments, 0, stdout="", stderr=""
                    )

                with patch.dict(os.environ, environment, clear=True), patch(
                    "control_plane.cli.subprocess.run", side_effect=observe
                ):
                    error = _refresh_remote_base(
                        self.scenario.repo, "origin", "main"
                    )

                self.assertIsNotNone(error)
                self.assertEqual(error.code, "E_FETCH_FAILED")
                self.assertFalse(fetch_called)

    def test_online_refresh_rejects_ssh_without_explicit_auth_contract(self) -> None:
        from control_plane.cli import _refresh_remote_base
        from tests.git_test_support import git

        actual_run = subprocess.run
        git(
            self.scenario.repo,
            "remote",
            "set-url",
            "origin",
            "git@github.com:example/control-plane.git",
        )
        fetch_called = False

        def observe(arguments, **kwargs):
            nonlocal fetch_called
            if "fetch" not in arguments:
                return actual_run(arguments, **kwargs)
            fetch_called = True
            return subprocess.CompletedProcess(arguments, 0, stdout="", stderr="")

        with patch.dict(
            os.environ,
            {"SSH_AUTH_SOCK": str(self.scenario.root / "agent.sock")},
            clear=True,
        ), patch("control_plane.cli.subprocess.run", side_effect=observe):
            error = _refresh_remote_base(self.scenario.repo, "origin", "main")

        self.assertIsNotNone(error)
        self.assertEqual(error.code, "E_FETCH_FAILED")
        self.assertFalse(fetch_called)

    def test_local_branch_readers_ignore_fake_path_and_observe_real_branch(self) -> None:
        from control_plane.cli import _current_branch, _git_current_branch

        self.scenario.checkout_feature("codex/real-cli-branch")
        marker = self.scenario.root / "fake-branch-git-executed"
        fake_bin = install_fake_git(self.scenario.root, marker, "main")

        with patch.dict(os.environ, {"PATH": str(fake_bin)}, clear=False):
            task_branch = _git_current_branch(self.scenario.repo)
            run_branch = _current_branch(self.scenario.repo)

        self.assertFalse(marker.exists())
        self.assertEqual(task_branch, "codex/real-cli-branch")
        self.assertEqual(run_branch, "codex/real-cli-branch")

    def test_changed_paths_ignore_fake_path_and_observe_real_drift(self) -> None:
        from control_plane.cli import _git_changed_paths

        (self.scenario.repo / "baseline.txt").write_text(
            "changed\n", encoding="utf-8"
        )
        (self.scenario.repo / "untracked.txt").write_text(
            "untracked\n", encoding="utf-8"
        )
        marker = self.scenario.root / "fake-paths-git-executed"
        fake_bin = install_fake_git(self.scenario.root, marker)

        with patch.dict(os.environ, {"PATH": str(fake_bin)}, clear=False):
            paths = _git_changed_paths(self.scenario.repo)

        self.assertFalse(marker.exists())
        self.assertEqual(paths, ["baseline.txt", "untracked.txt"])

    def test_changed_paths_block_clean_filter_before_execution(self) -> None:
        from control_plane.cli import _git_changed_paths
        from tests.git_test_support import git

        attributes = self.scenario.repo / ".gitattributes"
        attributes.write_text(
            "baseline.txt filter=cli-clean\n", encoding="utf-8"
        )
        git(self.scenario.repo, "add", ".gitattributes")
        git(self.scenario.repo, "commit", "-m", "test: cli filter baseline")
        marker = self.scenario.root / "cli-filter-executed"
        executable = self.scenario.root / "cli-clean.sh"
        executable.write_text(
            "#!/bin/sh\n"
            f": > {shlex.quote(str(marker))}\n"
            "cat\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        git(
            self.scenario.repo,
            "config",
            "filter.cli-clean.clean",
            shlex.quote(str(executable)),
        )
        (self.scenario.repo / "baseline.txt").write_text(
            "filtered change\n", encoding="utf-8"
        )

        try:
            _git_changed_paths(self.scenario.repo)
        except ValueError as error:
            outcome = str(error)
        else:
            outcome = "paths-observed"

        self.assertFalse(marker.exists())
        self.assertIn("E_LEASE_SCOPE", outcome)

    def test_local_git_observations_do_not_receive_sensitive_environment(self) -> None:
        from control_plane.cli import (
            _current_branch,
            _git_changed_paths,
            _git_current_branch,
        )

        observed: list[dict[str, str]] = []
        actual_run = subprocess.run

        def observe(arguments, **kwargs):
            if isinstance(kwargs.get("env"), dict):
                observed.append(dict(kwargs["env"]))
            return actual_run(arguments, **kwargs)

        sensitive = {
            "AWS_SECRET_ACCESS_KEY": "canary-not-a-secret",
            "GH_TOKEN": "canary-not-a-secret",
            "HTTPS_PROXY": "http://canary.invalid",
            "SSH_AUTH_SOCK": "/tmp/canary-cli-agent.sock",
        }
        with patch.dict(os.environ, sensitive, clear=False), patch(
            "control_plane.cli.subprocess.run", side_effect=observe
        ):
            _git_current_branch(self.scenario.repo)
            _current_branch(self.scenario.repo)
            _git_changed_paths(self.scenario.repo)

        self.assertTrue(observed)
        for environment in observed:
            self.assertFalse(set(sensitive).intersection(environment))

    def test_local_git_timeouts_use_stable_unknown_errors(self) -> None:
        from control_plane.cli import (
            _current_branch,
            _git_changed_paths,
            _git_current_branch,
        )

        cases = (
            (_git_current_branch, "E_STATE_BRANCH"),
            (_current_branch, "E_RUN_GIT"),
            (_git_changed_paths, "E_LEASE_SCOPE"),
        )
        for operation, code in cases:
            with self.subTest(operation=operation.__name__), patch(
                "control_plane.cli.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["git"], 5),
            ):
                try:
                    operation(self.scenario.repo)
                except Exception as error:
                    outcome = str(error)
                else:
                    outcome = "returned"
                self.assertIn(code, outcome)

    def test_default_preflight_does_not_contact_remote(self) -> None:
        self.scenario.checkout_feature()
        from tests.git_test_support import git

        git(
            self.scenario.repo,
            "remote",
            "set-url",
            "origin",
            str(self.scenario.root / "missing-remote.git"),
        )

        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offline"])

    def test_invalid_policy_keeps_preflight_json_shape(self) -> None:
        result = run_cli(
            "preflight",
            "--mode",
            "write",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(self.scenario.root / "missing-policy.toml"),
            "--json",
        )

        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["command"], "preflight")
        self.assertEqual(payload["facts"], {})
        self.assertEqual(payload["checks"], [])

    def test_read_human_output_is_diagnostic_when_checks_fail(self) -> None:
        (self.scenario.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        result = run_cli(
            "preflight",
            "--mode",
            "read",
            "--repo",
            str(self.scenario.repo),
            "--policy",
            str(FIXTURE_POLICY),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DIAGNOSTIC preflight", result.stdout)

    def test_doctor_handles_nonexistent_repository_as_json(self) -> None:
        result = run_cli(
            "doctor",
            "--repo",
            str(self.scenario.root / "does-not-exist"),
            "--policy",
            str(FIXTURE_POLICY),
            "--json",
        )

        payload = json.loads(result.stdout)
        codes = {error["code"] for error in payload["errors"]}
        self.assertEqual(result.returncode, 1)
        self.assertIn("E_GIT_NOT_REPOSITORY", codes)


if __name__ == "__main__":
    unittest.main()
