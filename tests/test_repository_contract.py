from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from control_plane.policy import load_policy, validate_policy


ROOT = Path(__file__).parents[1]


class RepositoryContractTests(unittest.TestCase):
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
        for relative_path in ("scripts/control-plane", "tests/run.sh"):
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
