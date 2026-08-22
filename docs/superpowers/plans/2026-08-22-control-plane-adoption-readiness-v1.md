# Control Plane New-Project Audit Bootstrap v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and integrate a validated, project-owned starter pack that lets the next project use an exact selected clean Control Plane source in audit mode without installing runtime bytes or weakening the consumer-adoption prohibition.

**Architecture:** Keep the Core and Adoption Enablement runtimes byte-identical. Add a four-file source-side starter pack whose README remains an operator guide while only three authority files are copied to the consumer, validate their schema with existing Core loaders, and prove harness-only preview compatibility with zero target mutation. Documentation records the audit dispositions and separates source-driven audit readiness from installation or stable adoption.

**Tech Stack:** Python 3.11 `unittest`, TOML schema 1, POSIX shell entrypoints, Git worktrees, GitHub pull requests and the existing `tests/run.sh` manifest.

**Execution status:** `TASKS_0_5_COMPLETE / FINAL_REVIEW_IN_PROGRESS`

---

## Fixed execution context

- Repository: `/Users/bustaseo/Developer/codex-engineering-control-plane`
- Worktree: `/Users/bustaseo/Developer/control-plane-worktrees/adoption-readiness-v1`
- Branch: `codex/adoption-readiness-v1`
- Base: `origin/main@f1fdecbb26fed9272d07823c31f06ef15ac89f78`
- Task: `TASK-NEW-PROJECT-AUDIT-BOOTSTRAP-V1`
- Scope: templates, governing documentation, the two existing test modules and
  the threat-model footer only.
- Forbidden in this front: Core/Adoption runtime, locks, hooks, `.github/`,
  dependencies, secrets, consumer mutation, install, deploy and release.

## Task 0: Freeze the contract and audit disposition

**Files:**

- Create: `docs/superpowers/specs/2026-08-22-control-plane-adoption-readiness-v1-design.md`
- Create: `docs/superpowers/plans/2026-08-22-control-plane-adoption-readiness-v1.md`

- [x] Record the exact base SHA, audit matrix, supported source-driven audit
      flow, non-goals, failure semantics, rollback and deferred fronts.
- [x] Confirm that no ADR is required because external adoption remains
      prohibited and runtime architecture is unchanged.
- [x] Run the documentary scope check, including untracked files:

  ```bash
  git status --short
  ```

  Expected: only the two untracked design/plan paths at this stage.

- [x] Ask a fresh read-only reviewer to compare the design and plan against
      `AGENTS.md`, current adoption boundaries and the requested outcome.
- [x] Fix only findings that change behavior, security, verifiability or scope.

## Task 1: Write the RED contracts for the starter pack

**Files:**

- Modify: `tests/test_core_documentation.py`
- Modify: `tests/test_adoption_enablement_preview.py`
- Modify: `tests/adoption_enablement_test_support.py`

- [x] In `tests/test_core_documentation.py`, add constants for the bootstrap
      directory, runbook, design and plan.
- [x] Add a test that requires exactly the four starter files, loads policy and
      registry with existing Core loaders, asserts zero validator/reference
      issues, and asserts `audit`, external-effect deny, PR-required,
      direct-base-push false and the visible `new-project` customization marker.
- [x] Add `test_new_project_bootstrap_preserves_existing_authority`: precreate
      each authority destination, including a dangling symlink case, require
      exact `E_BOOTSTRAP_AUTHORITY_EXISTS` and prove zero snapshot mutation.
- [x] Add a test requiring the runbook/design/index/README to preserve
      `external_consumer_adoption=PROHIBITED`, source-driven audit,
      `authorizes=false`, and the ban on consumer Adoption Enablement.
- [x] Add a test that requires RepositorySurveyV2 documents to be represented
      as integrated governing behavior in their own status plus the index and
      rejects a stale governing `FINAL_GATE_PENDING` declaration.
- [x] Add a test that creates a committed target with a pre-existing consumer
      README plus a TaskEnvelope, then invokes
      the exact source launcher for `policy-check`, `registry-check`,
      `inventory`, `doctor`, read-only `preflight` and audit `route`. Assert
      command-specific exits and JSON, `authorizes=false` and identical target
      metadata plus byte-identical consumer README before/after. `doctor` must
      be blocked only by the deliberately absent target lock; `route` may be
      `pending_host_capability`.
- [x] In `tests/test_adoption_enablement_preview.py`, add a harness helper that
      initializes a target from the three authority files and a test that
      asserts a valid, non-authorizing, zero-mutation preview with target
      authority files excluded from managed records.
- [x] Run the RED tests with the exact compatible interpreter:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
    -c 'import sys,unittest;sys.path.insert(0,".");suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]);result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)' \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_bootstrap_is_valid_and_audit_only \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_bootstrap_preserves_existing_authority \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_bootstrap_is_governing_without_authorizing_adoption \
    tests.test_core_documentation.CoreDocumentationTests.test_repository_survey_v2_index_status_is_integrated \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_source_driven_audit_is_real_and_zero_mutation \
    tests.test_adoption_enablement_preview.AdoptionPreviewTests.test_new_project_starter_is_preview_compatible_without_mutation
  ```

  Expected: failures caused only by missing starter/runbook bytes and stale
  documentation status. Any runtime failure is a scope stop.

## Task 2: Add the project-owned starter pack

**Files:**

- Create: `templates/new-project/AGENTS.md`
- Create: `templates/new-project/README.md`
- Create: `templates/new-project/.codex/project-policy.toml`
- Create: `templates/new-project/.codex/resource-registry.toml`

- [x] Add the generic schema-1 policy with `project_name = "new-project"`,
      `project_kind = "generic"`, origin/main, squash PR integration, no direct
      base push, bounded workers and the current proportional gates.
- [x] Add the valid schema-1 registry with `registry_id = "new-project"`, audit
      default, external-effect deny, proportional budgets, project
      instructions, verified-workflow, optional provider resources and every
      policy gate alias.
- [x] Add project instructions that require customization before the bootstrap commit,
      evidence before claims, TDD and a work branch. Commit uses local gates;
      push/PR/update/merge require provider reobservation and merge requires
      exact-head CI. State that authority exists only after the instructions
      are integrated in the protected base, so bootstrap branch/commit need
      pre-existing or exact operator authority. Retain separate authority for
      deploy/release, dependencies, CI, secrets and merge-triggered effects.
- [x] Add the source-side customization checklist. It must name every generic
      value, state that only the three authority files are copied, preserve
      the consumer README, state that the pack is not an installation, and
      prohibit `scripts/control-plane-adoption` against the consumer.
- [x] Require an all-path read-only `os.path.lexists` precheck before writing.
      Validate the target root and `.codex` parent as real directories and all
      three sources as regular bounded files. If any authority destination
      exists, including a dangling symlink, return
      `E_BOOTSTRAP_AUTHORITY_EXISTS`, preserve all bytes and open a reviewed
      semantic reconciliation. Publish only through one writer and one
      `apply_patch` operation containing exactly three `*** Add File:` entries;
      never invoke a copier, overwrite or auto-merge.
- [x] Run the first documentary focal:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
    -c 'import sys,unittest;sys.path.insert(0,".");suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]);result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)' \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_bootstrap_is_valid_and_audit_only \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_bootstrap_preserves_existing_authority
  ```

  Expected: `Ran 2 tests` and `OK`.

## Task 3: Prove harness-only preview compatibility

**Files:**

- Modify: `tests/test_adoption_enablement_preview.py`
- Modify: `tests/adoption_enablement_test_support.py`
- Verify: `templates/new-project/AGENTS.md`
- Verify: `templates/new-project/.codex/project-policy.toml`
- Verify: `templates/new-project/.codex/resource-registry.toml`

- [x] Copy the three target authority files into a harness-owned temporary Git
      repository using the existing safe fixture helper.
- [x] Capture source and target metadata before preview.
- [x] Use the shared bounded, no-follow, SHA-256 content snapshot so same-size
      rewrites and symlink-target changes cannot pass as zero mutation.
- [x] Call `preview(source, target)` only in that temporary harness.
- [x] Assert `validate_plan(plan) == ()`, `authorizes is False`, `mutation is
      False`, identical before/after metadata, target authority paths absent
      from managed records and `.codex/control-plane.lock` present.
- [x] Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
    -c 'import sys,unittest;sys.path.insert(0,".");suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]);result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)' \
    tests.test_adoption_enablement_preview.AdoptionPreviewTests.test_new_project_starter_is_preview_compatible_without_mutation
  ```

  Expected: `Ran 1 test` and `OK`.

## Task 3.5: Prove the real source-driven audit sequence

**Files:**

- Modify: `tests/test_core_documentation.py`
- Verify: `templates/new-project/.codex/project-policy.toml`
- Verify: `templates/new-project/.codex/resource-registry.toml`

- [x] Use `initialize_full_source` to create a temporary clean source fixture,
      bind its exact fixture `HEAD`, and prove its tracked, staged and untracked
      state is clean before invoking its launcher.
- [x] Initialize `main` with a consumer-only README baseline commit, switch to
      `codex/bootstrap-audit`, then add the three authority files and a minimal
      valid TaskEnvelope in a second commit. Prove `main` remains byte-identical
      and contains no bootstrap authority.
- [x] Execute the launcher from that temporary clean source for
      `policy-check`, `registry-check`, `inventory`, `doctor`, `preflight
      --mode read --offline` and `route --mode audit`.
- [x] Run the exact source-binding probe immediately before the launcher and
      after the audit; require `source_binding=PASS` both times.
- [x] Execute the documented export setup and the binding probe in one clean
      shell, then reach the complete supported audit sequence without a
      missing-environment error.
- [x] Require policy, registry, inventory and read preflight to exit zero with
      their exact command and `authorizes=false`.
- [x] Require `doctor` to exit one with valid policy/registry facts and only the
      absent target lock as the installation boundary.
- [x] Require route to parse and select resources without mutation; its native
      host capability may remain pending and never authorizes.
- [x] Compare the full target metadata snapshot and the consumer README bytes
      before and after all commands.
- [x] Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
    -c 'import sys,unittest;sys.path.insert(0,".");suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]);result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)' \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_source_driven_audit_is_real_and_zero_mutation
  ```

  Expected: `Ran 1 test` and `OK`.

## Task 4: Publish the governing audit runbook and current status

**Files:**

- Create: `docs/engineering/23-new-project-audit-bootstrap.md`
- Modify: `README.md`
- Modify: `docs/engineering/00-canonical-index.md`
- Modify: `docs/adr/0008-repository-survey-v2-contract.md`
- Modify: `docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md`
- Modify: `docs/superpowers/plans/2026-08-21-repository-survey-v2.md`
- Modify: `tests/test_core_documentation.py`

- [x] Write the exact baseline/clean-worktree/work-branch/read-guide/copy-three/
      customize/commit/source-driven-audit sequence using a separately selected
      `CONTROL_PLANE_SOURCE` and exact `CONTROL_PLANE_SOURCE_SHA`, plus explicit
      target arguments.
- [x] Extract the three governing bytes only with `cat-file blob` from the fixed
      source commit into a private stage; never use live template bytes during
      target mutation.
- [x] Require exact operator-selected author and committer variables, closed
      Git identity configuration, hook/filter bypass, raw byte-to-index
      comparison, compare-and-swap ref advance and verified index restoration
      on a post-staging failure.
- [x] Replace filter-facing diff/status checks with bounded no-follow raw
      worktree hashing plus exact index/HEAD entry comparison; reject every
      non-normal index tag and unsupported symlink/Gitlink mode.
- [x] Stage through a private index and prove a failed `update-ref` restores the
      original real-index bytes and mode atomically and exactly.
- [x] Include a result matrix distinguishing `AUDIT_READY`, `BLOCKED`,
      `UNKNOWN` and the never-valid claim `ADOPTED`.
- [x] State that `survey` requires an existing local base ref and that no local
      observation proves remote state.
- [x] Link the runbook and starter pack from `README.md` without weakening the
      adoption prohibition.
- [x] Change the three RepositorySurveyV2 documents and index from
      `FINAL_GATE_PENDING` to an integrated governing status. Update only the
      status metadata and stale Continuation Pointer; keep historical task
      evidence historical.
- [x] Add the bootstrap design, plan and runbook to a distinct governing
      audit-bootstrap section in the canonical index.
- [x] Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
    -c 'import sys,unittest;sys.path.insert(0,".");suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]);result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)' \
    tests.test_core_documentation.CoreDocumentationTests.test_new_project_bootstrap_is_governing_without_authorizing_adoption \
    tests.test_core_documentation.CoreDocumentationTests.test_repository_survey_v2_index_status_is_integrated
  ```

  Expected: `Ran 2 tests` and `OK`.

## Task 5: Review security impact and reseal final bytes

**Files:**

- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Modify: `tests/test_core_documentation.py`

- [x] Add the copied-but-uncustomized starter attacker story and mitigation:
      visible generic marker, project-owned review, audit default, external
      effect deny, no runtime copy and no consumer Adoption Enablement.
- [x] Add the residual that schema-valid generic values cannot prove
      project-specific correctness.
- [x] Run all focal tests before changing the footer:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
    -c 'import sys,unittest;sys.path.insert(0,".");suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]);result=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(0 if result.wasSuccessful() else 1)' \
    tests.test_core_documentation \
    tests.test_adoption_enablement_preview
  ```

  Expected: all collected tests pass except the snapshot-binding test, whose
  only failure is the stale final `Version:` footer.

- [x] Compute the normalized snapshot over final bytes:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
    -c 'import sys;sys.path.insert(0,".");from tests.test_core_documentation import normalized_snapshot_version;print(normalized_snapshot_version())'
  ```

- [x] Replace only the final `Version:` value with that exact result using
      `apply_patch`.
- [x] Re-run both focal modules. Expected: all tests `OK`.
- [x] Run `git diff --check` and verify the scope, including untracked files:

  ```bash
  git diff --check
  git status --short
  ```

  Expected: only the paths declared by Tasks 0–5; no runtime, lock, hook,
  `.github/` or dependency path.

## Task 6: Independent reviews and exact repository gate

**Files:** all changed paths, read-only during review.

- [ ] Freeze the candidate bytes and record `git diff --stat`,
      `git status --short --branch` and the candidate tree digest.
- [ ] Ask a fresh spec reviewer to check requirement coverage and report only
      Critical/Important findings.
- [ ] If clean, ask a different fresh quality/security reviewer to inspect the
      exact frozen diff and report only Critical/Important findings.
- [ ] Repair only in-scope findings, rerun focal tests, reseal if any tracked
      byte changes, and repeat both reviews on the new snapshot.
- [ ] Run the exact full gate once on final bytes in a disposable executor:

  ```bash
  bash tests/run.sh
  ```

  Expected: the manifest's complete Core and Adoption suites pass.

- [ ] Run post-gates:

  ```bash
  scripts/control-plane policy-check --policy .codex/project-policy.toml
  scripts/control-plane registry-check \
    --registry .codex/resource-registry.toml \
    --policy .codex/project-policy.toml
  scripts/control-plane doctor
  git diff --check
  git status --short --branch
  ```

  Expected: all command exits are zero; status shows only the intentional
  candidate before commit.

## Task 7: Integrate with provider evidence

**Files:** no new product changes.

- [ ] Commit the reviewed candidate on `codex/adoption-readiness-v1` with a
      concise audit-bootstrap subject.
- [ ] Re-run `scripts/control-plane preflight --mode write` for local truth.
      Separately re-observe `origin/main`, the PR and CI through Git/the
      provider because Core deliberately cannot refresh remotes.
- [ ] Push the exact commit, open a PR against the exact base, and wait for the
      provider check in a disposable executor.
- [ ] Integrate with squash only when head, base, mergeability and exact CI are
      terminal green under the standing Git authority in `AGENTS.md`.
- [ ] Fetch and prove content containment:

  ```bash
  git diff --stat origin/main..codex/adoption-readiness-v1
  ```

  Expected: empty output.

- [ ] Verify the post-merge CI result on the exact new `origin/main` SHA.
- [ ] Do not delete the branch, worktree or any preservation ref.

## Task 8: Close the goal with an adoption-ready handoff

- [ ] Report the integrated main SHA, PR, focal/full-gate evidence, exact CI
      duration and empty content-containment result.
- [ ] After merge, observe the exact integrated `origin/main` SHA and prepare a
      selected source in a clean detached worktree at that exact SHA. Fail
      closed unless the required starter paths
      `templates/new-project/AGENTS.md`, `templates/new-project/README.md`,
      `templates/new-project/.codex/project-policy.toml` and
      `templates/new-project/.codex/resource-registry.toml`, plus executable
      `scripts/control-plane`, are present and the selected worktree has no
      tracked, staged or untracked drift.
- [ ] Export that selected path as `CONTROL_PLANE_SOURCE`, bind the same SHA as
      `CONTROL_PLANE_SOURCE_SHA`, and require the runbook probe to emit exactly
      `source_binding=PASS`. Do not perform this preparation before Task 8.
- [ ] Give the user the exact first-use path:
      `templates/new-project/README.md` followed by
      `docs/engineering/23-new-project-audit-bootstrap.md`.
- [ ] Only then state that no consumer was mutated and ask for the one remaining
      concrete input: the path and identity of the next project when the user
      is ready to perform its project-owned bootstrap.
- [ ] Mark the native Goal complete only after all integration and containment
      evidence is observed.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora y ejecutora principal.
- **Para continuar:** congelar los bytes corregidos, repetir las dos revisiones
  independientes de Task 6 y, si quedan limpias, ejecutar el full gate exacto.
- **Mensaje exacto:** Continúa Task 6 de adoption-readiness-v1 desde la revisión final sobre los bytes corregidos, sin ampliar el alcance.
- **Estado de partida:** `/Users/bustaseo/Developer/control-plane-worktrees/adoption-readiness-v1`, `codex/adoption-readiness-v1@f1fdecbb26fed9272d07823c31f06ef15ac89f78`, base `origin/main@f1fdecbb26fed9272d07823c31f06ef15ac89f78`, candidato local sin commit ni PR; `TASKS_0_5_COMPLETE / FINAL_REVIEW_IN_PROGRESS`.
