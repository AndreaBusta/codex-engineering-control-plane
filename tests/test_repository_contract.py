from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from pathlib import Path

from control_plane.policy import load_policy, validate_policy


ROOT = Path(__file__).parents[1]
AUTHORIZATION_RECEIPT = re.compile(r"authorization_?receipt", re.IGNORECASE)


def _read_python_tree(root: Path, pattern: str = "*.py") -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(root.rglob(pattern))
    )


class RepositoryContractTests(unittest.TestCase):
    def test_productive_git_diff_uses_the_shared_closed_normalizer(self) -> None:
        from control_plane.repository import trusted_git_argv
        from tests.contract_support import (
            git_diff_contract,
            git_subprocess_contract,
        )

        inventory, issues = git_diff_contract(ROOT / "control_plane")
        subprocess_inventory, subprocess_issues = git_subprocess_contract(
            ROOT / "control_plane"
        )
        argv = trusted_git_argv(ROOT, ("diff", "--check"))
        separator = argv.index("diff")
        path_argv = trusted_git_argv(
            ROOT,
            ("diff", "--no-textconv", "--", "--no-ext-diff"),
        )
        path_diff = path_argv[path_argv.index("diff") :]
        path_separator = path_diff.index("--")

        self.assertGreaterEqual(len(inventory), 7)
        self.assertEqual(
            argv[separator : separator + 3],
            ["diff", "--no-ext-diff", "--no-textconv"],
        )
        self.assertEqual(
            path_diff[:path_separator].count("--no-ext-diff"), 1
        )
        self.assertEqual(
            path_diff[:path_separator].count("--no-textconv"), 1
        )
        self.assertEqual(path_diff[path_separator:], ["--", "--no-ext-diff"])
        self.assertEqual(issues, ())
        self.assertEqual(subprocess_issues, ())
        for expected in (
            ("run_workflow.py", "_capture_git"),
            ("lifecycle.py", "_verification_git"),
        ):
            use = next(
                item
                for item in subprocess_inventory
                if (item.relative_path, item.function) == expected
            )
            self.assertEqual(use.command_origin, "call:trusted_git_argv")

    def test_cached_name_only_diff_exception_does_not_read_worktree_content(
        self,
    ) -> None:
        from control_plane.host_bridge import (
            _closed_git_argv,
            _sanitized_git_environment,
        )
        from tests.git_test_support import (
            GitScenario,
            git,
            install_external_diff_driver,
        )

        scenario = GitScenario()
        self.addCleanup(scenario.close)
        marker = install_external_diff_driver(
            scenario.repo,
            scenario.root,
            tracked_path="baseline.txt",
            driver_name="cached-name-only-driver",
        )
        git(scenario.repo, "add", "baseline.txt")

        completed = subprocess.run(
            _closed_git_argv(
                scenario.repo,
                ["diff", "--cached", "--name-only", "-z"],
            ),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_sanitized_git_environment(),
            timeout=10,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, b"baseline.txt\0")
        self.assertFalse(marker.exists())

    def test_productive_git_diff_contract_rejects_direct_absolute_bypass(
        self,
    ) -> None:
        from tests.contract_support import git_diff_contract

        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "repository.py").write_text(
                """\
def _normalize_trusted_git_arguments(arguments):
    return arguments

def trusted_git_argv(repository, arguments):
    normalized = _normalize_trusted_git_arguments(arguments)
    return [\"/usr/bin/git\", \"-C\", str(repository), *normalized]
""",
                encoding="utf-8",
            )
            (runtime / "unsafe.py").write_text(
                """\
import subprocess

def observe(repository):
    return subprocess.run(
        [\"/usr/bin/git\", \"-C\", str(repository), \"diff\", \"HEAD\"],
        env=trusted_git_environment(),
    )

def observe_resolved(repository):
    executable = trusted_git_executable()
    return subprocess.run(
        [executable, \"-C\", str(repository), \"diff\", \"HEAD\"],
        env=trusted_git_environment(),
    )
""",
                encoding="utf-8",
            )

            _, issues = git_diff_contract(runtime)

        self.assertTrue(
            any(
                issue.startswith(
                    "GIT_DIFF_BYPASSES_NORMALIZER:unsafe.py:observe:"
                )
                for issue in issues
            ),
            issues,
        )
        self.assertTrue(
            any(
                issue.startswith(
                    "GIT_DIFF_BYPASSES_NORMALIZER:unsafe.py:observe_resolved:"
                )
                for issue in issues
            ),
            issues,
        )

    def test_productive_git_subprocesses_are_closed_and_inventoried(self) -> None:
        from tests.contract_support import git_subprocess_contract

        inventory, issues = git_subprocess_contract(ROOT / "control_plane")
        observed = {
            (item.relative_path, item.function) for item in inventory
        }

        self.assertTrue(
            {
                ("adoption.py", "_git"),
                ("cli.py", "_refresh_remote_base"),
                ("cli.py", "_run_local_git"),
                ("host_bridge.py", "commit_staged_change"),
                ("host_bridge.py", "stage_allowlisted_paths"),
                ("policy.py", "apply_project_remote_policy_update"),
            }.issubset(observed)
        )
        self.assertEqual(issues, ())

    def test_productive_git_subprocess_contract_rejects_ambient_git(self) -> None:
        from tests.contract_support import git_subprocess_contract

        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "unsafe.py").write_text(
                """\
import subprocess
import shutil

def observe(repo):
    environment = git_environment()
    return subprocess.run(["git", "-C", str(repo), "status"], env=environment)

def observe_via_path(repo):
    executable = shutil.which("git")
    return subprocess.run(
        [executable, "-C", str(repo), "status"],
        env=trusted_git_environment(),
    )

def observe_via_env(repo):
    return subprocess.run(
        ["/usr/bin/env", "git", "-C", str(repo), "status"],
        env=trusted_git_environment(),
    )

def observe_with_unknown_env(repo):
    environment = inherited_environment()
    return subprocess.run(
        trusted_git_argv(repo, ("status",)),
        env=environment,
    )
""",
                encoding="utf-8",
            )

            inventory, issues = git_subprocess_contract(runtime)

        self.assertEqual(len(inventory), 4)
        self.assertEqual(
            {issue.split(":", 1)[0] for issue in issues},
            {
                "GIT_AMBIENT_EXECUTABLE",
                "GIT_AMBIENT_ENVIRONMENT",
                "GIT_UNCLOSED_ENVIRONMENT",
            },
        )

    def test_authenticated_refresh_exception_rejects_wholesale_environment(
        self,
    ) -> None:
        from tests.contract_support import git_subprocess_contract

        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary)
            (runtime / "cli.py").write_text(
                """\
import subprocess

def _refresh_remote_base(repo):
    environment = git_environment()
    return subprocess.run(
        trusted_git_argv(repo, ("fetch", "origin")),
        env=environment,
    )
""",
                encoding="utf-8",
            )

            _, issues = git_subprocess_contract(runtime)

        self.assertTrue(
            any(
                issue.startswith("GIT_AMBIENT_ENVIRONMENT:cli.py:")
                for issue in issues
            ),
            issues,
        )

    def test_authenticated_refresh_exception_rejects_ambient_helper_builder(
        self,
    ) -> None:
        from tests.contract_support import git_subprocess_contract

        mutations = (
            "environment.update(os.environ)",
            'environment["GH_TOKEN"] = os.environ["GH_TOKEN"]',
        )
        for mutation in mutations:
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as temporary,
            ):
                runtime = Path(temporary)
                (runtime / "cli.py").write_text(
                    f"""\
import os
import subprocess

def _authenticated_git_environment(remote_url):
    environment = trusted_git_environment()
    {mutation}
    return environment

def _refresh_remote_base(repo):
    environment = _authenticated_git_environment("https://example.invalid/repo")
    return subprocess.run(
        trusted_git_argv(repo, ("fetch", "https://example.invalid/repo")),
        env=environment,
    )
""",
                    encoding="utf-8",
                )

                _, issues = git_subprocess_contract(runtime)

            self.assertTrue(
                any(
                    issue.startswith("GIT_UNCLOSED_ENVIRONMENT:cli.py:")
                    for issue in issues
                ),
                issues,
            )

    def test_shadow_receipt_detector_covers_factories_and_aliases(self) -> None:
        for source in (
            "def issue_authorization_receipt(): ...",
            "AuthorizationReceipt = dict[str, str]",
        ):
            with self.subTest(source=source):
                self.assertIsNotNone(AUTHORIZATION_RECEIPT.search(source))

    def test_shadow_runtime_scan_is_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime_root = Path(temporary) / "control_plane"
            nested = runtime_root / "orchestrator" / "tasks.py"
            nested.parent.mkdir(parents=True)
            nested.write_text("PENDING_NATIVE_REISSUE = True\n", encoding="utf-8")

            self.assertIn("PENDING_NATIVE_REISSUE", _read_python_tree(runtime_root))

    def test_shadow_support_scan_is_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            support_root = Path(temporary) / "tests"
            nested = support_root / "orchestrator" / "authorization_support.py"
            nested.parent.mkdir(parents=True)
            nested.write_text("authorization_receipt = True\n", encoding="utf-8")

            self.assertIn(
                "authorization_receipt",
                _read_python_tree(support_root, "*support.py"),
            )

    def test_repository_discovery_ignores_git_environment_redirection(self) -> None:
        from control_plane.repository import discover_repository, git_environment

        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary)
            actual = outer / "actual"
            redirect = outer / "redirect"
            for repository in (actual, redirect):
                subprocess.run(
                    ["git", "init", "-q", "-b", "main", str(repository)],
                    check=True,
                    env=git_environment(),
                )
            with patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(redirect / ".git"),
                    "GIT_WORK_TREE": str(redirect),
                },
            ):
                self.assertEqual(discover_repository(actual), actual.resolve())

    def test_required_artifacts_exist(self) -> None:
        required = (
            ".codex/project-policy.toml",
            ".codex/resource-registry.toml",
            ".codex/control-plane.lock",
            ".codex/hooks.json",
            ".codex/hooks/control_plane_hook.py",
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/workflows/control-plane.yml",
            ".gitignore",
            "AGENTS.md",
            "README.md",
            "SECURITY.md",
            "docs/adr/README.md",
            "docs/adr/TEMPLATE.md",
            "docs/adr/0001-router-hibrido-y-resolver-puro.md",
            "docs/adr/0002-distribucion-hooks-leases-y-enforcement.md",
            "docs/engineering/01-operating-model.md",
            "docs/engineering/02-git-pr-merge.md",
            "docs/engineering/03-reasoning-context-agents.md",
            "docs/engineering/04-documentation-policy.md",
            "docs/engineering/05-release-and-observation.md",
            "docs/engineering/06-recovery.md",
            "docs/engineering/07-adoption.md",
            "docs/engineering/08-global-codex-configuration.md",
            "docs/engineering/09-audit-dafo-and-risk-register.md",
            "docs/engineering/10-resource-routing.md",
            "docs/engineering/11-lifecycle-hooks-adoption.md",
            "docs/engineering/12-multidominio-y-modos.md",
            "docs/profiles/generic.md",
            "docs/profiles/ios.md",
            "docs/profiles/android.md",
            "docs/profiles/web-pwa.md",
            "docs/profiles/saas-backend.md",
            "docs/profiles/ai-text-pipeline.md",
            "scripts/control-plane",
            "scripts/build-release-candidate",
            "skills/install-control-plane/SKILL.md",
            "templates/HANDOFF.md",
            "templates/RELEASE_RECEIPT.json",
            "templates/TASK.md",
            "templates/TASK_ENVELOPE.json",
            "templates/RESOURCE_USE_RECEIPT.json",
            "tests/run.sh",
        )

        missing = [path for path in required if not (ROOT / path).is_file()]

        self.assertEqual(missing, [])

    def test_ci_workflow_is_pinned_least_privilege_and_cost_aware(self) -> None:
        from tests.contract_support import ci_contract_issues

        workflow = (
            ROOT / ".github" / "workflows" / "control-plane.yml"
        ).read_text(encoding="utf-8")

        self.assertEqual(ci_contract_issues(workflow), [])

    def test_manual_workflow_runs_real_macos_smoke_and_release_candidate(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "control-plane.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "tests.macos_hook_smoke.MacOSHookSmokeTests."
            "test_real_darwin_child_emits_closed_audit_only_contract",
            workflow,
        )
        self.assertIn("release-candidate:", workflow)
        self.assertIn("  actions: read", workflow)
        self.assertIn("needs: [verify, macos-smoke]", workflow)
        self.assertIn("scripts/build-release-candidate", workflow)
        self.assertIn("id: adoption-matrix", workflow)
        self.assertIn("id: release-preflight", workflow)
        self.assertIn("--workflow-evidence", workflow)
        self.assertIn("${{ github.run_attempt }}", workflow)
        self.assertIn('run_attempt=int(os.environ["GITHUB_RUN_ATTEMPT"])', workflow)
        self.assertIn("${{ needs.verify.result }}", workflow)
        self.assertIn("${{ needs.macos-smoke.result }}", workflow)
        self.assertIn("${{ steps.release-preflight.outcome }}", workflow)
        self.assertIn("${{ steps.adoption-matrix.outcome }}", workflow)
        self.assertIn("authorizes=False", workflow)
        refresh_step = workflow.split(
            "      - name: Refresh release gate against private main", 1
        )[1].split("      - name: Build reproducible non-authorizing candidate", 1)[0]
        build_step = workflow.split(
            "      - name: Build reproducible non-authorizing candidate", 1
        )[1]

        self.assertIn(
            "CONTROL_PLANE_GITHUB_TOKEN: ${{ github.token }}",
            refresh_step,
        )
        self.assertIn(
            "GIT_CONFIG_KEY_0=http.https://github.com/.extraheader",
            refresh_step,
        )
        self.assertIn(
            "CONTROL_PLANE_GITHUB_TOKEN: ${{ github.token }}",
            build_step,
        )
        self.assertIn(
            'GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $authorization"',
            refresh_step,
        )
        self.assertIn('echo "::add-mask::$authorization"', refresh_step)
        self.assertIn("scripts/control-plane preflight --mode release --refresh", refresh_step)
        self.assertNotIn("scripts/build-release-candidate", refresh_step)
        self.assertIn("scripts/build-release-candidate", build_step)
        self.assertEqual(workflow.count("${{ github.token }}"), 2)
        self.assertEqual(workflow.count("persist-credentials: false"), 3)
        self.assertGreaterEqual(
            workflow.count('test "$GITHUB_REF" = "refs/heads/main"'),
            3,
        )
        self.assertGreaterEqual(
            workflow.count('test "$GITHUB_SHA" = "$(git rev-parse HEAD)"'),
            4,
        )

    def test_manual_workflow_retains_exact_verified_v2_1_1_assets(self) -> None:
        workflow = (
            ROOT / ".github" / "workflows" / "control-plane.yml"
        ).read_text(encoding="utf-8")
        release_doc = (
            ROOT / "docs" / "engineering" / "05-release-and-observation.md"
        ).read_text(encoding="utf-8")
        action = (
            "actions/upload-artifact@"
            "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
        )
        upload_marker = "      - name: Upload verified v2.1.1 assets"

        self.assertEqual(workflow.count(action), 1)
        self.assertIn(upload_marker, workflow)
        self.assertLess(
            workflow.index(
                "      - name: Build reproducible non-authorizing candidate"
            ),
            workflow.index(upload_marker),
        )
        upload_step = workflow.split(upload_marker, 1)[1]
        self.assertIn(
            "name: control-plane-v2.1.1-${{ github.sha }}-"
            "attempt-${{ github.run_attempt }}",
            upload_step,
        )
        expected_paths = [
            "${{ runner.temp }}/control-plane-release-"
            "${{ github.run_id }}/candidate/" + filename
            for filename in (
                "codex-engineering-control-plane-2.1.1.tar.gz",
                "SHA256SUMS",
                "codex-engineering-control-plane-2.1.1.manifest.json",
                "codex-engineering-control-plane-2.1.1.receipt.json",
            )
        ]
        path_block = upload_step.split("          path: |\n", 1)[1].split(
            "          if-no-files-found:", 1
        )[0]
        self.assertEqual(
            [line.strip() for line in path_block.splitlines() if line.strip()],
            expected_paths,
        )
        self.assertIn("if-no-files-found: error", upload_step)
        self.assertIn("retention-days: 1", upload_step)
        self.assertIn("compression-level: 0", upload_step)
        self.assertIn("artefacto efímero de GitHub Actions", release_doc)
        self.assertIn("exactamente los cuatro assets", release_doc)
        self.assertNotIn("no publica ni sube assets", release_doc)

    def test_release_builder_uses_immutable_git_objects_and_anchored_output(self) -> None:
        builder = (ROOT / "scripts" / "build-release-candidate").read_text(
            encoding="utf-8"
        )

        self.assertIn('"cat-file", object_type, object_id', builder)
        self.assertIn("git_tree_oid(entries)", builder)
        self.assertIn("commit_object_identity(commit_object)", builder)
        self.assertIn("RELEASE_SOURCE_MAX_TOTAL_BYTES", builder)
        self.assertIn("format=tarfile.PAX_FORMAT", builder)
        self.assertIn("archive.addfile(member, BytesIO(payload))", builder)
        self.assertNotIn('"export-ignore"', builder)
        self.assertIn('"log", "--format=%s", commit', builder)
        self.assertIn("dir_fd=parent_descriptor", builder)
        self.assertIn("src_dir_fd=output_descriptor", builder)
        self.assertNotIn("tempfile.mkdtemp", builder)
        self.assertNotIn("shutil.rmtree", builder)

    def test_runtime_identity_matches_locked_product_version(self) -> None:
        from control_plane import __version__

        lock = tomllib.loads(
            (ROOT / ".codex" / "control-plane.lock").read_text(encoding="utf-8")
        )

        self.assertEqual(__version__, lock["product_version"])
        self.assertEqual(__version__, "2.1.1")

    def test_release_documents_distinguish_published_v2_1_0_from_v2_1_1(self) -> None:
        previous = (ROOT / "docs" / "releases" / "v2.1.0.md").read_text(
            encoding="utf-8"
        )
        current = (ROOT / "docs" / "releases" / "v2.1.1.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("published", previous.lower())
        self.assertNotIn("not tagged or published", previous.lower())
        self.assertIn("v2.1.1", current)
        self.assertIn("release_authorized=false", current)

    def test_documented_task_transition_uses_the_supported_cli(self) -> None:
        lifecycle = (
            ROOT / "docs" / "engineering" / "11-lifecycle-hooks-adoption.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn("--evidence preflight-evidence.json", lifecycle)

    def test_ci_contract_rejects_unpinned_actions_and_permission_escalation(
        self,
    ) -> None:
        from tests.contract_support import ci_contract_issues

        unsafe_workflow = """
name: Unsafe
on:
  pull_request:
permissions:
  contents: read
jobs:
  unsafe:
    permissions:
      contents: write
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: third-party/example@v1
      - run: bash tests/run.sh
"""

        issues = set(ci_contract_issues(unsafe_workflow))

        self.assertIn("CI_JOB_PERMISSIONS", issues)
        self.assertIn("CI_WRITE_PERMISSION", issues)
        self.assertIn("CI_UNPINNED_ACTION", issues)

    def test_ci_contract_ignores_safety_claims_inside_comments(self) -> None:
        from tests.contract_support import ci_contract_issues

        comments_only = """
# permissions:
#   contents: read
# pull_request:
# uses: actions/example@0000000000000000000000000000000000000000
jobs: {}
"""

        issues = set(ci_contract_issues(comments_only))

        self.assertIn("CI_TOP_LEVEL_PERMISSIONS", issues)
        self.assertIn("CI_PULL_REQUEST_TRIGGER", issues)
        self.assertIn("CI_NO_ACTIONS", issues)

    def test_ci_contract_rejects_yaml_shape_bypasses(self) -> None:
        from tests.contract_support import ci_contract_issues

        inline_permissions = """
on:
  pull_request:
permissions:
  contents: read
jobs:
  unsafe:
    permissions: {contents: write}
    runs-on: ubuntu-24.04
"""
        quoted_uses = """
on:
  pull_request:
permissions:
  contents: read
jobs:
  unsafe:
    runs-on: ubuntu-24.04
    steps:
      - 'uses': third-party/example@main
"""
        fake_trigger = """
permissions:
  contents: read
jobs:
  pull_request:
    runs-on: ubuntu-24.04
"""
        hidden_checkout_credentials = """
on:
  pull_request:
  push:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  unsafe:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
        with:
          persist-credentials: true
      - run: |
          echo "persist-credentials: false"
          bash tests/run.sh
"""
        spaced_uses_key = """
on:
  pull_request:
permissions:
  contents: read
jobs:
  unsafe:
    runs-on: ubuntu-24.04
    steps:
      - uses : third-party/example@main
"""
        credentials_hidden_in_env = """
on:
  pull_request:
  push:
  workflow_dispatch:
permissions:
  contents: read
jobs:
  unsafe:
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@0000000000000000000000000000000000000000
        env:
          persist-credentials: false
      - run: bash tests/run.sh
"""

        self.assertTrue(
            {"CI_JOB_PERMISSIONS", "CI_WRITE_PERMISSION"}.issubset(
                ci_contract_issues(inline_permissions)
            )
        )
        self.assertIn(
            "CI_UNSUPPORTED_YAML_STYLE",
            ci_contract_issues(quoted_uses),
        )
        self.assertIn(
            "CI_PULL_REQUEST_TRIGGER",
            ci_contract_issues(fake_trigger),
        )
        self.assertIn(
            "CI_CHECKOUT_CREDENTIALS",
            ci_contract_issues(hidden_checkout_credentials),
        )
        self.assertIn(
            "CI_UNPINNED_ACTION",
            ci_contract_issues(spaced_uses_key),
        )
        self.assertIn(
            "CI_CHECKOUT_CREDENTIALS",
            ci_contract_issues(credentials_hidden_in_env),
        )

    def test_project_policy_is_valid(self) -> None:
        policy = load_policy(ROOT / ".codex" / "project-policy.toml")

        self.assertEqual(validate_policy(policy), [])

    def test_shell_entrypoints_are_executable(self) -> None:
        for relative_path in (
            "scripts/control-plane",
            "scripts/build-release-candidate",
            "tests/run.sh",
        ):
            path = ROOT / relative_path
            self.assertTrue(
                path.is_file() and os.access(path, os.X_OK),
                f"{relative_path} must exist and be executable",
            )

    def test_release_receipt_is_machine_readable_and_complete(self) -> None:
        receipt = json.loads(
            (ROOT / "templates" / "RELEASE_RECEIPT.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(
            set(receipt),
            {
                "schema_version",
                "status",
                "source",
                "artifact",
                "verification",
                "approvals",
            },
        )
        self.assertIn("commit", receipt["source"])
        self.assertIn("build", receipt["artifact"])
        self.assertIn("external_state", receipt["verification"])

    def test_core_safety_terms_are_documented(self) -> None:
        documentation = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "docs" / "engineering").glob("*.md")
        )

        for term in (
            "PROMPT_MULTIFRONT",
            "pending_external_evidence",
            "origin/<base>",
            "ADR",
            "TestFlight",
            "secuencial por defecto",
            "dos workers",
        ):
            self.assertIn(term, documentation)

    def test_local_agents_file_stays_concise(self) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertLessEqual(len(agents.splitlines()), 140)
        self.assertIn("preflight --mode write", agents)
        self.assertIn("No hagas commit", agents)

    def test_logical_close_requires_a_verified_continuation_pointer(
        self,
    ) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        handoff = (ROOT / "templates" / "HANDOFF.md").read_text(
            encoding="utf-8"
        )
        reasoning = (
            ROOT / "docs" / "engineering" / "03-reasoning-context-agents.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Continuation Pointer", agents)
        self.assertIn("cada cierre lógico o checkpoint", agents)
        self.assertIn("`## Continuación`", agents)

        for field in (
            "## Continuación",
            "- Escribe en:",
            "- Rol:",
            "- Para continuar:",
            "- Mensaje exacto:",
            "- Estado de partida:",
            "- No hacer todavía:",
        ):
            with self.subTest(field=field):
                self.assertIn(field, handoff)

        for rule in (
            "tarea padre u orquestadora",
            "tarea hija o ejecutora",
            "identidad visible",
            "este hilo",
            "rama o worktree",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, reasoning)

    def test_cross_thread_lookup_is_host_native_bounded_and_non_authorizing(
        self,
    ) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        section = agents.split("## Lookup nativo entre tareas", 1)[1].split(
            "## Autoridad visual y tareas shadow", 1
        )[0]
        normalized = " ".join(section.split())

        for contract in (
            "`codex://threads/<UUID>`",
            "`read_thread`",
            "adapter Python",
            "una sola tarea",
            "`FOUND`",
            "`STALE`",
            "`UNKNOWN`",
            "`authorizes=false`",
            "4 KiB",
            "proyecto/worktree",
            "Continuation Pointer",
            "transcript, prompts, razonamiento, tool output o secretos",
            "despiertes, escribas, dirijas, archives ni modifiques",
            "gates de revisión ni autorización",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, normalized)

        self.assertFalse(
            (ROOT / "control_plane" / "cross_thread_audit.py").exists()
        )
        registry = (
            ROOT / ".codex" / "resource-registry.toml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("cross-thread", registry)

    def test_visual_authority_and_orchestrator_autonomy_stay_shadow_only(
        self,
    ) -> None:
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        section = agents.split("## Autoridad visual y tareas shadow", 1)[1].split(
            "## Seguridad", 1
        )[0]
        normalized = " ".join(section.split())

        for contract in (
            "`PREPARADO — NO EJECUTADO`",
            "merge, deploy o publicación",
            "acción, proyecto/repositorio, rama, commit/target exactos",
            "efecto, evidencias/gates, rollback, límites y la frase exacta",
            "`sí`, `ok`, texto ambiguo o la propia tarjeta nunca autorizan",
            "tampoco la frase exacta por sí sola",
            "preparación, autorización verificada, ejecución y observación",
            "no solo color",
            "`PENDING_NATIVE_REISSUE` o `UNKNOWN`",
            "`authorizes=false`",
            "`TrustedAuthorization`",
            "No reutilices ni serialices autoridad entre tareas o sesiones",
            "autorización fuente nativa exacta",
            "tarea origen, tarea/sesión destino",
            "`scope_paths` y `subject_digest`",
            "ausente, fabricado, expirado, reutilizado",
            "repo, acción, target o SHA",
            "abrir, supervisar, relevar y cerrar",
            "máximo dos workers y ningún writer solapado",
            "checkpoint completo, estado terminal verificable",
            "cero trabajo o efectos pendientes",
            "commit, push, PR, merge, deploy, release, secretos ni pagos",
            "el runtime no crea, despierta, escribe ni archiva tareas",
            "planes shadow",
            "Ponytail",
            "deferido",
            "DietrichGebert/ponytail@16f29800fd2681bdf24f3eb4ccffe38be3baec6b",
            "sha256:40df33b58fc6ef889b93585733feb9566b76e9586efa7f376785c1e995197ac0",
            "no se instala ni registra",
            "delete/stdlib/native/yagni/shrink",
            "net LOC",
            "read-only, opcional y no autorizante",
            "`TaskEnvelope` frente a changed paths",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, normalized)

        runtime = _read_python_tree(ROOT / "control_plane")
        support = _read_python_tree(ROOT / "tests", "*support.py")
        registry = (ROOT / ".codex" / "resource-registry.toml").read_text(
            encoding="utf-8"
        )
        project_skills = tuple((ROOT / "skills").glob("**/SKILL.md"))

        self.assertIsNone(AUTHORIZATION_RECEIPT.search(runtime))
        self.assertNotIn("PENDING_NATIVE_REISSUE", runtime)
        self.assertIsNone(AUTHORIZATION_RECEIPT.search(support))
        self.assertNotIn("ponytail", registry.lower())
        self.assertFalse(
            any(
                "ponytail" in path.as_posix().lower()
                or "ponytail" in path.read_text(encoding="utf-8").lower()
                for path in project_skills
            )
        )

    def test_v23_outcome_bridge_decision_security_and_rollback_are_linked(self) -> None:
        adr_path = (
            ROOT / "docs" / "adr"
            / "0005-host-bound-outcome-authorization.md"
        )
        threat_path = (
            ROOT / "docs" / "security"
            / "2026-08-08-v2-3-outcome-bridge-threat-model.md"
        )
        rollback_path = (
            ROOT / "docs" / "engineering"
            / "16-outcome-bridge-rollback.md"
        )
        for path in (adr_path, threat_path, rollback_path):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())

        adr = " ".join(adr_path.read_text(encoding="utf-8").lower().split())
        for contract in (
            "host-bound",
            "per-effect",
            "serializable grants",
            "second agent",
            "mutable cli",
            "evidence is not authority",
        ):
            with self.subTest(adr=contract):
                self.assertIn(contract, adr)

        threat = threat_path.read_text(encoding="utf-8")
        for contract in (
            "Assets",
            "Trust boundaries",
            "replay",
            "drift",
            "stale review",
            "uncertain write",
            "commit-tree",
            "update-ref",
            "READY/PASS integration observations",
            "Local guards are not GitHub branch protection",
            "Residual risks",
        ):
            with self.subTest(threat=contract):
                self.assertIn(contract, threat)

        rollback = " ".join(
            rollback_path.read_text(encoding="utf-8").lower().split()
        )
        for contract in (
            "before commit",
            "after local commit",
            "after push or pr",
            "uncertain remote write",
            "post-merge verification",
            "observe before retry",
            "zero second write",
            "`reset --hard`",
            "force-push",
            "automatic pr closure",
            "automatic remote rollback",
        ):
            with self.subTest(rollback=contract):
                self.assertIn(contract, rollback)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        git_guide = (
            ROOT / "docs" / "engineering" / "02-git-pr-merge.md"
        ).read_text(encoding="utf-8")
        recovery = (
            ROOT / "docs" / "engineering" / "06-recovery.md"
        ).read_text(encoding="utf-8")
        lifecycle = (
            ROOT / "docs" / "engineering"
            / "11-lifecycle-hooks-adoption.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "docs/adr/0005-host-bound-outcome-authorization.md", readme
        )
        self.assertIn(
            "docs/security/2026-08-08-v2-3-outcome-bridge-threat-model.md",
            readme,
        )
        self.assertIn(
            "docs/engineering/16-outcome-bridge-rollback.md", readme
        )
        self.assertIn(
            "2026-08-08-v2-3-outcome-bridge-threat-model.md", security
        )
        self.assertIn("16-outcome-bridge-rollback.md", recovery)
        self.assertIn("evidence != authority", git_guide)
        self.assertIn("PR LISTA", lifecycle)
        self.assertIn("native host adapter", lifecycle)

    def test_v23_docs_state_the_real_execution_and_egress_boundaries(self) -> None:
        documents = {
            "README": ROOT / "README.md",
            "lifecycle": (
                ROOT / "docs" / "engineering"
                / "11-lifecycle-hooks-adoption.md"
            ),
            "skill": ROOT / "skills" / "control-plane-run" / "SKILL.md",
        }
        required = (
            "git local allowlisted",
            "`git ls-remote` read-only",
            "prepare/arm/revalidate",
            "push/pr/squash merge",
            "host-native",
            "python no recibe autoridad",
            "`blocked`",
        )
        forbidden = (
            "python productivo no ejecuta esos efectos",
            "kernel python no ejecuta git remoto",
            "sin egress",
        )
        for name, path in documents.items():
            text = " ".join(path.read_text(encoding="utf-8").lower().split())
            for claim in required:
                with self.subTest(document=name, required=claim):
                    self.assertIn(claim, text)
            for claim in forbidden:
                with self.subTest(document=name, forbidden=claim):
                    self.assertNotIn(claim, text)

    def test_v23_promotion_truth_requires_pr_ready_before_separate_squash(self) -> None:
        spec = (
            ROOT
            / "docs"
            / "superpowers"
            / "specs"
            / "2026-08-08-control-plane-v2-3-outcome-bridge-design.md"
        ).read_text(encoding="utf-8")
        plan = (
            ROOT
            / "docs"
            / "superpowers"
            / "plans"
            / "2026-08-08-control-plane-v2-3-outcome-bridge.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn("implementación no iniciada", spec)
        for text in (spec, plan):
            with self.subTest(text=text[:20]):
                self.assertIn("implementación local verificada", text)
                self.assertIn("PR LISTA", text)
                self.assertIn("sandbox privado", text)
                self.assertIn("hasta squash merge", text)
                self.assertIn("2.1.1", text)
                self.assertIn("digest", text)

        self.assertIn("gate operativo predeterminado", spec)
        self.assertIn("promoción de release separada", spec)
        self.assertIn("no se exige ni ejecuta", plan)

        boundary = (
            "`frame_effect_authorization` recibe `NativeUserInteractionEvent` "
            "y `HostAdapterCapability`",
            "Python bridge recibe y consume la autorización nativa solo para Git "
            "local allowlisted (`git add`; commit con `git commit-tree` y "
            "`git update-ref` CAS)",
            "kernel puede observar con `git ls-remote` read-only",
            "push/PR/squash merge son host-native",
        )
        for text in (spec, plan):
            normalized = " ".join(text.split())
            for claim in boundary:
                with self.subTest(text=text[:20], claim=claim):
                    self.assertIn(claim, normalized)
            self.assertNotIn("«hasta merge»", text)
            self.assertNotIn("703/703", text)

        task_7 = plan.split("### Task 7:", 1)[1].split("### Task 8:", 1)[0]
        self.assertIn("«hasta squash merge»", task_7)
        self.assertNotIn("«hasta merge»", task_7)
        self.assertIn("- [ ] **Step 4b:", plan)
        self.assertNotIn("test_outcome_authorization", plan)
        self.assertIn("`test_git_outcome_bridge`", plan)
        self.assertIn("`test_independent_review`", plan)
        self.assertIn("dirty worktree", plan)
        self.assertIn("untracked", plan)
        self.assertIn(
            "codex-control-plane/candidates/v2-3-local-candidate.json", plan
        )
        self.assertIn("LocalCandidateReceiptV1", plan)
        for dynamic_claim in (
            "sha256:816a2e94c99f0b56f7c654682ab50d74cf31ee6487dd4c0651f646037fbf122c",
            "705/705",
            "18 tracked",
            "14 untracked",
            "review_status=",
            "RECORDED_IN_CANDIDATE_RECEIPT",
            "PENDING_FRESH_FULL_SUITE",
            "PENDING_RUNTIME_DIGEST",
            "PENDING_REVIEW_SUBJECT_DIGEST",
            "PENDING_SECURITY_SNAPSHOT",
        ):
            with self.subTest(dynamic_claim=dynamic_claim):
                self.assertNotIn(dynamic_claim, plan)

        task_8 = plan.split("### Task 8:", 1)[1].split("## Verification matrix", 1)[0]
        self.assertIn("- [ ] **Step 2:", task_8)
        self.assertIn("LocalCandidateReceiptV1", task_8)
        matrix = plan.split("## Verification matrix", 1)[1].split(
            "## Continuación", 1
        )[0]
        authorization_row = next(
            line for line in matrix.splitlines() if "Outcome authority" in line
        )
        self.assertIn("test_lifecycle", authorization_row)
        self.assertIn("replay", authorization_row)
        continuation = plan.split("## Continuación", 1)[1]
        self.assertIn("LocalCandidateReceiptV1", continuation)
        self.assertIn("codex-control-plane/candidates/v2-3-local-candidate.json", continuation)
        self.assertNotIn("705/705", continuation)
        self.assertNotIn("review_status", continuation)

    def test_v23_native_sandbox_packets_are_valid_and_fail_closed(self) -> None:
        from control_plane.contracts import validate_task_envelope

        sandbox_root = ROOT / "docs" / "engineering" / "sandbox"
        cases = (
            (
                sandbox_root / "v2-3-pr-ready-task-envelope.json",
                "TASK-V23-SANDBOX-PR-READY",
                "pull_request",
                {"local_read", "local_write", "commit", "remote_write", "pull_request"},
            ),
            (
                sandbox_root / "v2-3-squash-merge-task-envelope.json",
                "TASK-V23-SANDBOX-SQUASH-MERGE",
                "integration",
                {
                    "local_read",
                    "local_write",
                    "commit",
                    "remote_write",
                    "pull_request",
                    "integration",
                },
            ),
        )
        for path, task_id, requested_outcome, effects in cases:
            with self.subTest(path=path.name):
                envelope = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(validate_task_envelope(envelope), [])
                self.assertEqual(envelope["task_id"], task_id)
                self.assertEqual(envelope["requested_outcome"], requested_outcome)
                self.assertEqual(
                    {effect["name"] for effect in envelope["effects"]}, effects
                )
                self.assertTrue(
                    all(
                        effect["source"] == "model_inference"
                        for effect in envelope["effects"]
                    )
                )
                serialized = json.dumps(envelope, sort_keys=True).lower()
                for forbidden in (
                    "trustedauthorization",
                    "nativeuserinteractionevent",
                    "hostadaptercapability",
                    "outcomeauthorizationcontext",
                    "nonce",
                    "credential",
                    "authorizes",
                ):
                    self.assertNotIn(forbidden, serialized)

        bindings = json.loads(
            (sandbox_root / "v2-3-native-sandbox-bindings.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(bindings), {"schema_version", "status", "authorizes", "packets"}
        )
        self.assertEqual(bindings["schema_version"], 1)
        self.assertEqual(bindings["status"], "PENDING_SANDBOX_TARGET")
        self.assertFalse(bindings["authorizes"])
        self.assertEqual(len(bindings["packets"]), 2)
        for packet in bindings["packets"]:
            self.assertIsNone(packet["repository"])
            self.assertIsNone(packet["base"])
            self.assertIsNone(packet["review_head"])
            self.assertIsNone(packet["required_checks"])
            self.assertEqual(
                packet["scope_paths"],
                ["sandbox/change.txt", "sandbox/test_change.py"],
            )
            self.assertTrue(packet["recovery"])
            self.assertTrue(packet["rollback"])

        runbook = (
            ROOT / "docs" / "engineering"
            / "17-v2-3-native-sandbox-promotion.md"
        ).read_text(encoding="utf-8")
        for contract in (
            "PENDING_SANDBOX_TARGET",
            "authorizes=false",
            "review_head → committed_head",
            "merge_sha ∈ origin/<base>",
            "observe before retry",
            "zero second write",
            "real Codex task and shell tools",
            "never the Python test adapter",
            "PR LISTA",
            "hasta squash merge",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, runbook)

    def test_v23_candidate_store_documents_durable_pair_and_recovery(self) -> None:
        spec = (
            ROOT
            / "docs"
            / "superpowers"
            / "specs"
            / "2026-08-08-control-plane-v2-3-outcome-bridge-design.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(spec.split())

        for contract in (
            "parent owner-safe `0755`",
            "sin `chmod`",
            "`candidates` permanece `0700`",
            "canonical y exactamente un pending reservado",
            "`nlink=2`",
            "64 hex completos de su `receipt_digest`",
            "nombre y contenido coincidan exactamente",
            "`nlink=1` sigue siendo válido únicamente como formato legacy",
            "orphan pending",
            "inventario acotado",
            "descriptor-relative",
            "nunca ejecuta cleanup ni `unlink`",
            "partial pre-link",
            "ruta pública única",
            "preserva y falla cerrado",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, normalized)
        self.assertNotIn("Crea ancestros privados `0700`", spec)

    def test_v24_native_governor_plugin_has_a_reversible_operating_contract(self) -> None:
        design = (
            ROOT
            / "docs"
            / "superpowers"
            / "specs"
            / "2026-08-10-control-plane-v2-4-native-governor-design.md"
        )
        plan = (
            ROOT
            / "docs"
            / "superpowers"
            / "plans"
            / "2026-08-10-control-plane-v2-4-native-governor.md"
        )
        runbook = ROOT / "docs" / "engineering" / "18-native-governor-plugin.md"
        manifest = ROOT / "plugins" / "control-plane" / ".codex-plugin" / "plugin.json"

        missing = [path for path in (design, plan, runbook, manifest) if not path.is_file()]
        self.assertEqual(missing, [])

        normalized = " ".join(runbook.read_text(encoding="utf-8").lower().split())
        for required in (
            "skill-only",
            "advisory",
            "no scheduler",
            "máximo dos workers",
            "un solo writer",
            "cursor",
            "checkpoint terminal",
            "facts_only",
            "10/3",
            "global skill",
            "fail-closed",
            "instalación transaccional",
            "rollback",
            "plugin candidate",
            "no es una release",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)

        for required in (
            "scaffold oficial solo en la instalación inicial",
            "conservar la entrada y source existentes",
            "cachebuster",
            "codex plugin add",
            "duplicado activo",
            "$control-plane:control-plane-run",
            "afecta solo esa operación",
            "continúa todo trabajo local seguro",
            "result, evidence, remaining_work, pending_effects, authorizes=false",
            "tareas dogfood completadas",
            "todo lo demás es false",
            "counts unknown no disparan v2.5",
        ):
            with self.subTest(update_contract=required):
                self.assertIn(required, normalized)
        self.assertNotIn("se crea o actualiza la entrada mediante el scaffold", normalized)

        design_text = " ".join(design.read_text(encoding="utf-8").lower().split())
        plan_text = " ".join(plan.read_text(encoding="utf-8").lower().split())
        for text in (design_text, plan_text):
            self.assertIn("mensaje nativo actual", text)
            self.assertIn("petición terminal sola", text)
            self.assertIn("continúa sin crear", text)
        self.assertNotIn("petición terminal explícita", plan_text)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("docs/engineering/18-native-governor-plugin.md", readme)

        plugin = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(plugin["name"], "control-plane")
        self.assertEqual(plugin["version"], "3.0.0")
        for forbidden in ("hooks", "mcpServers", "apps"):
            self.assertNotIn(forbidden, plugin)

    def test_no_unresolved_placeholders(self) -> None:
        forbidden = re.compile(
            r"\bT[B]D\b|\bT[O]DO\b|"
            + "implement "
            + "later|"
            + "fill "
            + "in|<SCHEME_"
            + "REAL>|<COM"
            + "ANDO_"
        )
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if (
                not path.is_file()
                or ".git" in path.parts
                or "__pycache__" in path.parts
            ):
                continue
            if path.suffix not in {".md", ".py", ".toml", ".json", ""}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if forbidden.search(text):
                offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual(offenders, [])

    def test_no_literal_secret_assignment(self) -> None:
        from tests.contract_support import literal_secret_findings

        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if (
                not path.is_file()
                or ".git" in path.parts
                or "__pycache__" in path.parts
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if literal_secret_findings(text):
                offenders.append(str(path.relative_to(ROOT)))

        self.assertEqual(offenders, [])

    def test_secret_scanner_covers_common_assignment_and_token_shapes(self) -> None:
        from tests.contract_support import literal_secret_findings

        synthetic_cases = (
            "pass" + 'word: "replace-me-not-a-real-credential"',
            '"client_' + 'secret": "replace-me-not-a-real-credential"',
            '"client_' + 'secret": "replace-me-not-a-real-credential",',
            "PASS" + "WORD=replace-me-not-a-real-credential",
            "access_" + "token: replace-me-not-a-real-credential",
            "-----BEGIN " + "PRIVATE KEY-----",
            "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----",
            "gh" + "p_" + ("A" * 36),
            "AK" + "IA" + ("A" * 16),
            "AS" + "IA" + ("A" * 16),
            "xo" + "xb-" + ("1" * 24),
        )

        for sample in synthetic_cases:
            with self.subTest(sample_shape=sample[:4]):
                self.assertTrue(literal_secret_findings(sample))


if __name__ == "__main__":
    unittest.main()
