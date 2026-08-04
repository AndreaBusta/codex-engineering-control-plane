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
            "scripts/build-release-candidate",
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
        self.assertIn("needs: [verify, macos-smoke]", workflow)
        self.assertIn("scripts/build-release-candidate", workflow)
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
            'GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $authorization"',
            refresh_step,
        )
        self.assertIn('echo "::add-mask::$authorization"', refresh_step)
        self.assertIn("scripts/control-plane preflight --mode release --refresh", refresh_step)
        self.assertNotIn("scripts/build-release-candidate", refresh_step)
        self.assertNotIn("github.token", build_step)
        self.assertNotIn("CONTROL_PLANE_GITHUB_TOKEN", build_step)
        self.assertIn("scripts/build-release-candidate", build_step)
        self.assertEqual(workflow.count("${{ github.token }}"), 1)
        self.assertEqual(workflow.count("persist-credentials: false"), 3)
        self.assertNotIn("upload-artifact", workflow)
        self.assertGreaterEqual(
            workflow.count('test "$GITHUB_REF" = "refs/heads/main"'),
            3,
        )
        self.assertGreaterEqual(
            workflow.count('test "$GITHUB_SHA" = "$(git rev-parse HEAD)"'),
            4,
        )

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

        runtime = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "control_plane").glob("*.py")
        )
        support = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "tests").glob("*support.py")
        )
        registry = (ROOT / ".codex" / "resource-registry.toml").read_text(
            encoding="utf-8"
        )
        project_skills = tuple((ROOT / "skills").glob("**/SKILL.md"))

        self.assertIsNone(
            re.search(r"class\s+\w*AuthorizationReceipt\b", runtime)
        )
        self.assertNotIn("PENDING_NATIVE_REISSUE", runtime)
        self.assertNotIn("authorization_receipt", support)
        self.assertNotIn("ponytail", registry.lower())
        self.assertFalse(
            any(
                "ponytail" in path.as_posix().lower()
                or "ponytail" in path.read_text(encoding="utf-8").lower()
                for path in project_skills
            )
        )

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
