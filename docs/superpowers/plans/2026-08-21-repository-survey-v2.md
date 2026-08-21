# RepositorySurveyV2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the default `RepositorySurveyV1` output with a bounded `RepositorySurveyV2` that distinguishes unpublished unique branches from local residue and cannot let optional `added_paths` erase the normative preservation result.

**Architecture:** `control_plane/survey.py` fixes the base commit/tree once, inventories local and homonymous remote refs in aggregate, derives per-branch tree equality and reachability, then freezes the normative status before any optional enrichment. `control_plane/cli.py` projects the closed V2 contract and maps four states to four distinct process exits. Survey and pre-push remain runtime-independent and prove parity through shared scenarios in tests.

**Tech Stack:** Python 3.11 standard library, `dataclasses`, bounded Git plumbing through the existing trusted repository helpers, `unittest`, TOML Core lock, Markdown governance, POSIX shell full gate; no new dependency, module, network path or CI change.

---

**Status:** `EXECUTION_AUTHORIZED / EVIDENCE_PENDING / SHALLOW_SCOPE_REFRAME_ACCEPTED`

**Authority:** `authorizes=false`

**Date:** 2026-08-21

**Workflow:** `verified-workflow / structured`, tier `T2`

**Approved specification:** `docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md`

**Accepted base-contract decision:** `docs/adr/0008-repository-survey-v2-contract.md`

**Implementation-scope amendment state:** the plan `Status` header and the ADR
metadata are authoritative. `SHALLOW_SCOPE_REFRAME_PROPOSED` requires Task 0
Step 2; `SHALLOW_SCOPE_REFRAME_ACCEPTED` records that Step 2 observed the exact
native acceptance. Narrative text never promotes the state.

## Execution boundary

This plan records HOW and is always `authorizes=false`; changing its prose does
not create authority. While its header is `PREPARED_NOT_EXECUTED`, execution
stops after the specification, ADR and plan, and Task 0 Step 2 requires the
native instruction. Only Task 0 may change the header to
`EXECUTION_AUTHORIZED` after observing that instruction; that state records the
external authority and permits the planned local implementation. Commit, push,
Pull Request and merge then follow the standing policy integrated in
`AGENTS.md`: applicable gates over current bytes plus fresh provider evidence,
and exact-head CI before merge. Adoption, installation, deploy, release, branch
deletion, worktree cleanup and other destructive or reserved effects retain
their own authority gates.

The implementation front is bound to:

- repository: `/Users/bustaseo/Developer/codex-engineering-control-plane`;
- worktree: `/Users/bustaseo/Developer/control-plane-worktrees/survey-orphan-semantics-v1`;
- branch: `codex/survey-orphan-semantics-v1`;
- base observed while planning:
  `origin/main@250af1222570af5d8da70273e51326133176ae74`;
- one writer, no overlapping runtime, documentation or reseal writer;
- every tracked edit uses `apply_patch`; formatters may only perform bounded
  mechanical rewrites inside the listed paths;
- `max_gate_runs=6`; the last consumed full gate must be green on final frozen
  bytes.

If `origin/main` advances before implementation, reobserve and stop for a
reframe. Do not silently rebase, merge, transplant the untracked documents or
reinterpret the approved base. Before the first code edit, the accepted
contract documents must be preserved in a local commit after their applicable
gates pass, the worktree must be clean and
`scripts/control-plane preflight --mode write` must PASS. The current dirty
preflight is expected because the three planning
documents are deliberately untracked; it is not permission to bypass the gate.

No step below changes the product version. `control_plane.__version__`,
`.codex/control-plane.lock.product_version` and
`plugins/control-plane/.codex-plugin/plugin.json` remain
`3.1.0-core.2`. A later version, release or adoption decision is a separate
front.

## Closed implementation decisions

1. V2 is the only default output. There is no V1 flag, compatibility payload
   or silent field reinterpretation.
2. `only_in_branch` is removed. `added_paths: int | None` preserves the add-only
   count only as optional forensic information.
3. The normative predicate is exactly:

   ```text
   tree differs from fixed base
   AND branch has a commit not reachable from fixed base
   AND no valid local refs/remotes/<remote>/<branch> exists
   ```

4. A valid same-name local remote-tracking ref exempts even when stale or
   behind. Server freshness remains outside Survey.
5. Status precedence is `UNKNOWN > FAIL > WARN > PASS` when evidence integrity
   is considered first. Exit mapping is `PASS=0`, `FAIL=1`, `UNKNOWN=2`,
   `WARN=3`; `ok=true` only for `PASS`.
6. Missing mandatory evidence never becomes zero or `false`. UNKNOWN CLI facts
   use `UNKNOWN`, not synthetic counts.
7. Base name resolution happens once. Every later comparison uses frozen commit
   and tree OIDs, including `added_paths`.
8. Mandatory reachability is one aggregate per-ref query, not one process per
   branch. Optional `added_paths` may use one diff per branch but all branches
   share one ten-second enrichment deadline.
9. Survey and pre-push do not import each other. Shallow reachability is tested
   before any Survey runtime edit. Once native implementation authority exists,
   only the exact RED `GG_UNPUBLISHED_UNIQUE_BRANCH` activates the minimum
   candidate-only shallow check in `git_guards.py`, within its existing
   aggregate deadline; GREEN, another RED or any broader guard change stops the
   front for reframe.
10. The 27-module allowlist, 21,530 active-Python-LOC ceiling and 450-line
    `survey.py` budget remain fixed. If the implementation does not fit, stop
    and simplify or reframe; do not raise a limit.

## Requirements and traceability

| ID | Spec criteria | Approved requirement | Tasks | Required evidence |
|---|---:|---|---|---|
| `RSV2-01` | 1, 2 | Default closed V2 discriminators; no `only_in_branch` or V1 mode | 1, 4 | payload and parser RED/GREEN tests |
| `RSV2-02` | 4, 7-9 | Per-branch three-signal predicate over one fixed base | 1, 2 | modified, remote-ref, behind, shared-head and squash fixtures |
| `RSV2-03` | 10 | Absence is proven; ambiguous mandatory evidence is UNKNOWN | 2 | invalid remote/ref/object/UTF-8, timeout and limit tests |
| `RSV2-04` | 5, 6, 15 | Distinct severity and exit mapping | 1, 4 | PASS/FAIL/UNKNOWN/WARN JSON, human and process tests |
| `RSV2-05` | 3, 11 | `added_paths` is nullable and never normative | 3 | per-branch failure and shared-deadline tests |
| `RSV2-06` | 12 | Survey/guard parity without runtime coupling | 0, 5 | exact shallow RED/minimum/GREEN, cross-fixture truth table and import-boundary assertion |
| `RSV2-07` | 13 | Read-only, bounded, no network or hooks | 1-3, 5 | zero-mutation and trusted-Git assertions |
| `RSV2-08` | 15, 16 | Internal consumers and governing docs migrate atomically | 4, 6 | CLI, skill and documentation contract tests |
| `RSV2-09` | 14, 16 | 27 modules, fixed budgets, lock and threat snapshot | 7 | Core contract, lock and snapshot tests |
| `RSV2-10` | 16 | Final evidence is fresh on frozen bytes | 7 | focal suite, full gate, post-gates and independent review |

No requirement closes merely because a later test is green. Each behavior must
first produce the expected RED against V1, then GREEN after the minimum V2
change, and zero-mutation checks must compare the repository before and after.

## Exact file responsibility map

### Already created by the planning front

- `docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md`: approved
  base WHAT/WHY contract; its status line records whether Task 0 has observed
  acceptance of the shallow implementation-scope amendment.
- `docs/adr/0008-repository-survey-v2-contract.md`: accepted base structural
  decision; its amendment metadata records `proposed` or `accepted`, and either
  value remains non-authorizing by itself.
- `docs/superpowers/plans/2026-08-21-repository-survey-v2.md`: this HOW and gate
  sequence.

### Runtime and lock to modify only after fresh implementation authority

- `control_plane/survey.py`: V2 dataclasses, frozen base, bounded mandatory ref
  observations, severity derivation, nullable enrichment and V2 payload.
- `control_plane/cli.py`: `--remote`, V2 failure projection, UNKNOWN facts,
  human `WARN` rendering and four exit codes.
- `control_plane/git_guards.py`: conditional only; after the exact Task 0 RED,
  add the minimum candidate-only shallow observation inside the existing
  unpublished-branch budget. No other guard behavior is in scope.
- `.codex/control-plane.lock`: recompute only the Core `runtime` digest after
  `survey.py` and `cli.py` freeze.

### Tests to modify

- `tests/test_core_survey.py`: V2 contract, predicate counterexamples, frozen
  base, UNKNOWN discipline, optional deadline and zero mutation.
- `tests/test_core_cli.py`: V2 discriminators, `--remote`, facts, renderer and
  exits 0/1/2/3, including unexpected-exception output.
- `tests/test_core_git_guards.py`: prove the conditional shallow RED/GREEN,
  retain the bounded process budget and assert the same predicate plus updated
  Survey status without any other guard-runtime change.
- `tests/test_core_git_skill.py`: operational wording no longer calls add-only
  output a content comparison.
- `tests/test_core_documentation.py`: accepted ADR/spec/plan, governing
  supersession, four states, residual risks and final snapshot footer.
- `tests/test_core_contract.py`: executable `survey.py <= 450` budget while
  preserving the existing 27-module and 21,530-line checks.

### Governing documentation to modify

- `README.md`: V2 output, four statuses/exits and local-only boundary.
- `AGENTS.md`: replace the add-only branch-equivalence recipe with tree/full
  content equivalence; reserve `--diff-filter=A` for `added_paths` only.
- `skills/control-plane-git/SKILL.md`: V2-first preservation workflow and the
  same corrected equivalence rule.
- `docs/engineering/00-canonical-index.md`: list ADR 0008, the V2 specification
  and this plan with their exact candidate state; mark the V1 block of design
  3.3 as superseded only after Tasks 1-5 behavior is green, without claiming a
  final gate, integration or release.
- `docs/engineering/21-repository-alignment-and-branch-decisions.md`: update
  cleanup interpretation, status severity and branch-content proof.
- `docs/engineering/22-orientation-and-known-traps.md`: update cold-start Survey
  guidance while retaining the separate `survey-hardening-wip` front.
- `docs/superpowers/specs/2026-08-18-control-plane-3-3-operator-orientation-design.md`:
  add a precise supersession note linking V2/ADR 0008; do not rewrite its
  historical V1 example as if it had always been V2.
- `docs/security/2026-08-12-control-plane-core-threat-model.md`: close only the
  orphan semantic and misleading-name residuals, add V2 threats/mitigations,
  retain every other Survey-hardening item and reseal the final footer last.
- `docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md`: move to an
  implemented-local-candidate status only after Tasks 1-5 behavior is green;
  final gate evidence remains external and is never backfilled after freeze.
- `docs/adr/0008-repository-survey-v2-contract.md`: add PR identity later if a
  PR exists under the standing integrated Git authority; do not change its
  accepted decision to fit implementation drift.
- `docs/superpowers/plans/2026-08-21-repository-survey-v2.md`: record only
  concise checkbox/evidence updates during an authorized execution and only
  before the final freeze. Terminal gate evidence belongs in the external
  checkpoint and handoff, never in a post-gate tracked edit.

### Explicitly unchanged

- `control_plane/git_guards.py`: unchanged unless the exact Task 0 RED activates
  the single candidate-only shallow exception; every other edit is prohibited.
- `tests/run.sh`: existing modules already govern all changed runtime/tests.
- `control_plane/lockfile.py`, `scripts/control-plane` and
  `.codex/hooks/control_plane_hook.py`: the allowlist remains exactly 27.
- `.codex/project-policy.toml`, `.codex/resource-registry.toml` and
  `.codex/hooks.json`: no policy, route or hook contract changes.
- `docs/superpowers/plans/2026-08-18-control-plane-3-3-operator-orientation.md`:
  historical execution transcript; never rewrite it.
- `.github/`, dependencies, Adoption Enablement, plugin version, Autopilot and
  all general Survey hardening are outside scope.

## Dependency graph and execution discipline

```text
Task 0: preserve approved documents and activate a clean authorized front
  -> Task 1: V2 contract, status and normative branch predicate
      -> Task 2: frozen base and fail-closed mandatory evidence
          -> Task 3: nullable added_paths enrichment
              -> Task 4: CLI and four exit codes
                  -> Task 5: cross-fixture guard parity
                      -> Task 6: governing documentation
                          -> Task 7: lock, threat reseal, gates and review
                              -> Task 8: provider integration and main CI
```

Tasks execute sequentially. A read-only reviewer may inspect a frozen diff, but
no second writer may touch the worktree. Incidental defects outside the exact
file map are recorded with reproduction and left for their named front. In
particular, do not import the preserved hardening commit or edit
`git_guards.py` merely because a fresh review finds a valid pre-existing issue;
only the exact Task 0 shallow RED can activate its closed exception.

### Task 0: Preserve the accepted contract and activate implementation

**Files:**

- Modify: `docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md`
- Create: `docs/adr/0008-repository-survey-v2-contract.md`
- Create: `docs/superpowers/plans/2026-08-21-repository-survey-v2.md`
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md` only to
  make the documentation checkpoint snapshot-valid
- Modify after clean preflight: `tests/test_core_git_guards.py` only for the
  shallow parity precheck in Step 5
- Modify after the exact Step 5 RED: `control_plane/git_guards.py` only for the
  minimum candidate-only shallow observation in Step 6

- [ ] **Step 1: Reobserve the exact local subject without remote mutation**

  Run:

  ```bash
  pwd
  git rev-parse --show-toplevel
  git branch --show-current
  git rev-parse HEAD
  git rev-parse origin/main
  git status --short --branch
  ```

  Expected before any authorized implementation: the named worktree and branch,
  both refs at `250af1222570af5d8da70273e51326133176ae74`, and only the three
  contract documents untracked/modified. Any other path or ref is a Stable
  Pause, not an invitation to repair or rebase.

- [ ] **Step 2: Obtain the missing implementation authority**

  Require one fresh user instruction that accepts the exact ADR/plan bytes and
  authorizes local implementation, including the conditional shallow exception.
  The document text, this checkbox and `authorizes=false` cannot satisfy that
  implementation gate. Git transitions remain governed separately by the
  standing integrated `AGENTS.md` policy.

- [ ] **Step 3: Record acceptance and preserve the documents only if authorized**

  After exact reframe acceptance and implementation authority exist, use
  `apply_patch` to perform all state transitions before any runtime edit:

  - change this plan header to
    `EXECUTION_AUTHORIZED / EVIDENCE_PENDING / SHALLOW_SCOPE_REFRAME_ACCEPTED`;
  - change the specification state from `SCOPE_REFRAME_PROPOSED` to
    `SCOPE_REFRAME_ACCEPTED` and from `IMPLEMENTATION_NOT_AUTHORIZED` to
    `IMPLEMENTATION_AUTHORITY_OBSERVED` while retaining `authorizes=false`;
  - change the ADR metadata to `Enmienda de alcance shallow: accepted
    2026-08-21; excepción condicional al RED exacto`.
  - replace both the specification and plan `## Continuación` blocks with the
    same post-acceptance state:
    - `Escribe en: este hilo`;
    - `Rol: orquestadora y ejecutora principal`;
    - `Para continuar: ejecutar Task 0 Step 4 y después el RED shallow exacto
      de Step 5, antes de cualquier otro runtime`;
    - `Mensaje exacto: Continúa con Task 0 Step 4 y el RED shallow exacto de
      Step 5.`;
    - `Estado de partida: codex/survey-orphan-semantics-v1 sobre
      origin/main@250af122; reframe shallow aceptado, autoridad de
      implementación observada y documentos contractuales preservados por este
      checkpoint; todavía sin edición ni test runtime`;
    - omit `No hacer todavía` because, after the exact native instruction, no
      concrete boundary blocks the next planned local step.

  These edits record the native instruction; they do not create it and do not
  claim any test result. Then validate the documents:

  ```bash
  ! rg -n 'TB[D]|TO[D]O|FIXM[E]|PLACEHOLDE[R]' \
    docs/adr/0008-repository-survey-v2-contract.md \
    docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md \
    docs/superpowers/plans/2026-08-21-repository-survey-v2.md
  ! rg -n '[[:blank:]]+$' \
    docs/adr/0008-repository-survey-v2-contract.md \
    docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md \
    docs/superpowers/plans/2026-08-21-repository-survey-v2.md
  git diff --check
  ```

  Expected: `rg` has no matches and `git diff --check` is silent. Stage only
  after computing the normalized snapshot over these final documentary bytes,
  replacing only the threat-model `Version:` footer with `apply_patch` and
  passing the exact snapshot focal. Then prove the index before committing:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys; sys.path.insert(0, "."); from tests.test_core_documentation import normalized_snapshot_version; print(normalized_snapshot_version())'
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); name = "tests.test_core_documentation.CoreDocumentationTests.test_threat_model_is_repository_scoped_and_snapshot_bound"; result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName(name)); raise SystemExit(not result.wasSuccessful())'
  git add -- \
    docs/adr/0008-repository-survey-v2-contract.md \
    docs/security/2026-08-12-control-plane-core-threat-model.md \
    docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md \
    docs/superpowers/plans/2026-08-21-repository-survey-v2.md
  git diff --cached --name-only
  git diff --cached --check
  git diff --name-only
  git commit -m "docs: define RepositorySurveyV2 contract"
  ```

  Expected cached paths: exactly the three contract documents and the footer
  path above; the unstaged diff is empty. A remote transition is not required
  for this checkpoint; if later used, it follows the standing
  provider-observation and gate rules. Runtime edits invalidate this checkpoint
  footer later and Task 7 reseals it again on final bytes.

- [ ] **Step 4: Re-run routing and clean preflight**

  Normalize this exact plan as a T2 `TaskEnvelope`, store it outside tracked
  paths or below the worktree Git dir, run `scripts/control-plane route`, read
  every required resource, then run:

  ```bash
  scripts/control-plane preflight --mode write
  ```

  Expected: route remains structured/T2 with written-plan, relevant-test and
  independent-review gates; preflight PASS on a clean feature worktree. Delete
  no temporary state unless its creation and removal were both inside the fresh
  authority.

- [ ] **Step 5: Resolve the known shallow parity risk before Survey edits**

  Static inspection shows the current unpublished-branch path does not perform
  its own shallow-state query. Add this RED contract test to the existing
  `CoreGitGuardUnpublishedBranchTests` fixture before editing `survey.py`:

  ```python
  def test_pre_push_reports_unknown_for_shallow_unpublished_reachability(
      self,
  ) -> None:
      self._create_unpublished_branch()
      base = git(self.repo, "rev-parse", "refs/remotes/origin/main")
      common = Path(git(self.repo, "rev-parse", "--git-common-dir"))
      if not common.is_absolute():
          common = self.repo / common
      (common / "shallow").write_text(f"{base}\n", encoding="ascii")

      payload = self._guard([self._unrelated_update()])

      self.assertEqual(
          [error["code"] for error in payload["errors"]],
          ["GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN"],
      )
  ```

  Run only that method:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_git_guards.CoreGitGuardUnpublishedBranchTests.test_pre_push_reports_unknown_for_shallow_unpublished_reachability"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  There are exactly three allowed outcomes:

  - GREEN: keep the fixture, do not edit guard production and continue;
  - RED only because the observed code is
    `GG_UNPUBLISHED_UNIQUE_BRANCH`: preserve that output and continue to Step 6;
  - any other RED, fixture error or environmental failure: Stable Pause before
    editing `survey.py` or guard production.

- [ ] **Step 6: Apply the minimum shallow guard correction only after the exact RED**

  First let `_is_shallow_repository()` accept a bounded timeout while preserving
  its current default and non-fast-forward caller:

  ```python
  def _is_shallow_repository(repo: Path, *, timeout: float = 5.0) -> bool:
      value = _git_text(
          repo,
          ["rev-parse", "--is-shallow-repository"],
          timeout=timeout,
      )
      if value == "true":
          return True
      if value == "false":
          return False
      raise ValueError("GG_GIT_STATE_UNOBSERVABLE: shallow state is invalid")
  ```

  In `_unpublished_branch_errors()`, only after `candidate_refs` is non-empty
  and immediately before `rev-list`, consume the existing remaining budget:

  ```python
  try:
      shallow = _is_shallow_repository(
          repo,
          timeout=_remaining_unpublished_branch_budget(deadline),
      )
  except ValueError:
      return [unknown]
  if shallow:
      return [unknown]
  ```

  Do not move this query before candidate filtering, add a deadline, change an
  error code, predicate, publication exemption, limit or other guard behavior.
  Rename the process-budget test to
  `test_pre_push_batches_unique_commit_observation_in_four_processes`; assert
  exactly two `for-each-ref`, one `rev-parse --is-shallow-repository`, one
  `rev-list`, at most four normative processes and the same aggregate deadline.

  Re-run the exact shallow method and then all of
  `tests.test_core_git_guards`. Expected: GREEN. Any wider repair or remaining
  contradiction is a Stable Pause; it does not authorize further guard edits.

### Task 1: Close the V2 contract and normative branch predicate

**Files:**

- Modify: `tests/test_core_survey.py:1-187`
- Modify: `tests/test_core_contract.py:33-40`
- Modify: `control_plane/survey.py:1-234`

- [ ] **Step 1: Add test helpers that create no network traffic**

  Extend the test fixture with an output helper and a configured but never
  contacted remote:

  ```python
  def _git_output(repository: Path, *arguments: str) -> str:
      completed = subprocess.run(
          ["/usr/bin/git", "-C", str(repository), *arguments],
          env={
              "LC_ALL": "C",
              "PATH": "/usr/bin:/bin",
              "GIT_AUTHOR_NAME": "t",
              "GIT_AUTHOR_EMAIL": "t@e",
              "GIT_COMMITTER_NAME": "t",
              "GIT_COMMITTER_EMAIL": "t@e",
          },
          stdin=subprocess.DEVNULL,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
          text=True,
          check=True,
          timeout=10,
      )
      return completed.stdout.strip()
  ```

  At the end of `_repository()` add:

  ```python
  _git(repository, "remote", "add", "origin", "https://example.invalid/repository.git")
  ```

  This creates local config only; no fetch, ls-remote, push or other network
  command is permitted.

- [ ] **Step 2: Write RED contract, severity and predicate-counterexample tests**

  Add these exact assertions, using real temporary Git repositories:

  ```python
  def test_default_payload_is_repository_survey_v2(self) -> None:
      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          payload = survey_payload(survey_repository(repository, base="main"))
          self.assertEqual(payload["schema_version"], 2)
          self.assertEqual(payload["kind"], "RepositorySurveyV2")
          self.assertEqual(payload["comparison"]["base_ref"], "main")
          self.assertEqual(payload["comparison"]["remote_name"], "origin")
          self.assertNotIn("only_in_branch", str(payload))

  def test_modified_unique_branch_without_remote_ref_is_fail(self) -> None:
      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          _git(repository, "switch", "-c", "feature")
          (repository / "a.txt").write_text("unique\n", encoding="utf-8")
          _git(repository, "commit", "--quiet", "-am", "unique")
          feature_head = _git_output(repository, "rev-parse", "HEAD")
          _git(repository, "switch", "main")

          observed = survey_repository(repository, base="main")
          branch = next(item for item in observed.branches if item.name == "feature")

          self.assertEqual(branch.head, feature_head)
          self.assertFalse(branch.content_equivalent_to_base)
          self.assertTrue(branch.has_unique_commits)
          self.assertFalse(branch.remote_tracking_ref_present)
          self.assertTrue(branch.unpublished_unique)
          self.assertEqual(observed.unpublished_unique_branches, 1)
          self.assertEqual(observed.status, "FAIL")

  def test_untracked_without_unpublished_branch_is_warn(self) -> None:
      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          (repository / "orphan.md").write_text("local\n", encoding="utf-8")
          observed = survey_repository(repository, base="main")
          self.assertEqual(observed.unpublished_unique_branches, 0)
          self.assertEqual(observed.untracked_total, 1)
          self.assertEqual(observed.status, "WARN")

  def test_unpublished_branch_precedes_local_residue(self) -> None:
      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          _git(repository, "switch", "-c", "feature")
          (repository / "a.txt").write_text("unique\n", encoding="utf-8")
          _git(repository, "commit", "--quiet", "-am", "unique")
          _git(repository, "switch", "main")
          (repository / "orphan.md").write_text("local\n", encoding="utf-8")
          self.assertEqual(survey_repository(repository, base="main").status, "FAIL")

  def test_homonymous_remote_ref_exempts_even_when_behind(self) -> None:
      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          base_head = _git_output(repository, "rev-parse", "main")
          _git(repository, "switch", "-c", "feature")
          (repository / "a.txt").write_text("unique\n", encoding="utf-8")
          _git(repository, "commit", "--quiet", "-am", "unique")
          feature_head = _git_output(repository, "rev-parse", "HEAD")
          _git(repository, "update-ref", "refs/remotes/origin/feature", base_head)
          _git(repository, "switch", "main")
          branch = next(
              item for item in survey_repository(repository, base="main").branches
              if item.name == "feature"
          )
          self.assertNotEqual(feature_head, base_head)
          self.assertTrue(branch.has_unique_commits)
          self.assertTrue(branch.remote_tracking_ref_present)
          self.assertFalse(branch.unpublished_unique)

  def test_tree_equivalent_after_squash_is_not_unpublished(self) -> None:
      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          _git(repository, "switch", "-c", "feature")
          (repository / "a.txt").write_text("changed\n", encoding="utf-8")
          _git(repository, "commit", "--quiet", "-am", "feature")
          _git(repository, "switch", "main")
          _git(repository, "merge", "--quiet", "--squash", "feature")
          _git(repository, "commit", "--quiet", "-m", "squashed")
          branch = next(
              item for item in survey_repository(repository, base="main").branches
              if item.name == "feature"
          )
          self.assertTrue(branch.content_equivalent_to_base)
          self.assertTrue(branch.has_unique_commits)
          self.assertFalse(branch.unpublished_unique)

  def test_behind_branch_with_different_tree_has_no_unique_commits(self) -> None:
      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          _git(repository, "branch", "behind")
          (repository / "a.txt").write_text("main advanced\n", encoding="utf-8")
          _git(repository, "commit", "--quiet", "-am", "advance main")
          branch = next(
              item for item in survey_repository(repository, base="main").branches
              if item.name == "behind"
          )
          self.assertFalse(branch.content_equivalent_to_base)
          self.assertFalse(branch.has_unique_commits)
          self.assertFalse(branch.unpublished_unique)

  def test_branch_ref_drift_around_merged_observation_is_unknown(self) -> None:
      from unittest.mock import patch

      from control_plane import survey as survey_module

      for phase in ("before_reachability", "before_postvalidation"):
          with self.subTest(phase=phase), tempfile.TemporaryDirectory() as raw:
              repository = _repository(Path(raw))
              _git(repository, "switch", "-c", "feature")
              (repository / "a.txt").write_text("unique\n", encoding="utf-8")
              _git(repository, "commit", "--quiet", "-am", "unique")
              _git(repository, "switch", "main")
              main_head = _git_output(repository, "rev-parse", "main")
              real_text = survey_module._text
              moved = False

              def drifting_text(repo, arguments):
                  nonlocal moved
                  is_merged = (
                      arguments
                      and arguments[0] == "for-each-ref"
                      and any(
                          argument.startswith("--merged=")
                          for argument in arguments
                      )
                  )
                  if not is_merged or moved:
                      return real_text(repo, arguments)
                  if phase == "before_reachability":
                      _git(
                          repository,
                          "update-ref",
                          "refs/heads/feature",
                          main_head,
                      )
                  result = real_text(repo, arguments)
                  if phase == "before_postvalidation":
                      _git(
                          repository,
                          "update-ref",
                          "refs/heads/feature",
                          main_head,
                      )
                  moved = True
                  return result

              with patch.object(survey_module, "_text", drifting_text):
                  observed = survey_repository(repository, base="main")

              self.assertEqual(observed.status, "UNKNOWN")
              self.assertEqual(observed.error_code, "E_SURVEY_INVENTORY")
  ```

  Split the existing stash/untracked test so stash-only and untracked-only each
  assert `WARN`; do not weaken their counts. Preserve the existing content
  counterexamples, but migrate their contract assertions atomically:
  `RepositorySurveyV1` becomes V2 and every add-only assertion reads
  `added_paths`, never `only_in_branch`. Add to
  `tests/test_core_contract.py`:

  ```python
  survey_lines = (ROOT / "control_plane" / "survey.py").read_text(
      encoding="utf-8"
  ).count("\n") + 1
  self.assertLessEqual(survey_lines, 450)
  ```

- [ ] **Step 3: Run RED and inspect the reason**

  Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_survey", "tests.test_core_contract"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected: RED on missing V2 discriminators/fields, V1 `FAIL` for residue, the
  absent `unpublished_unique` predicate and all three counterexamples before
  production derives those signals. A syntax, fixture or environment failure
  is not the intended RED and must be repaired before production code.

- [ ] **Step 4: Introduce the closed V2 model and pure severity rule**

  Replace `BranchObservation` and extend `RepositorySurvey` with these exact
  types:

  ```python
  @dataclass(frozen=True)
  class BranchObservation:
      name: str
      head: str
      added_paths: int | None
      content_equivalent_to_base: bool
      has_unique_commits: bool
      remote_tracking_ref_present: bool
      unpublished_unique: bool


  @dataclass(frozen=True)
  class RepositorySurvey:
      root: str
      common_git_dir: str | None
      branch: str | None
      head: str | None
      base_ref: str
      base_head: str | None
      remote_name: str
      worktrees: tuple[WorktreeObservation, ...] | None
      branches: tuple[BranchObservation, ...] | None
      stashes: int | None
      untracked_total: int | None
      unpublished_unique_branches: int | None
      status: str
      error_code: str | None


  def _survey_status(*, stashes: int, untracked: int, unpublished: int) -> str:
      if unpublished:
          return "FAIL"
      if stashes or untracked:
          return "WARN"
      return "PASS"
  ```

  `_unknown()` must receive `base_ref`, `remote_name` and optional verified
  identity fields. It returns `None` for incomplete branch/worktree/count
  evidence, `status="UNKNOWN"` and the exact error code. The payload serializes
  those values as JSON `null`, never `[]`, `0` or `false`.

- [ ] **Step 5: Implement mandatory aggregate branch observation**

  Resolve `base_ref` once to `base_head` and `base_tree`. Inventory local refs
  with one bounded command whose format is:

  ```text
  %(refname)%00%(objectname)%00%(objecttype)%00%(tree)
  ```

  Use `--sort=refname`, `--count max_branches+1` and `refs/heads/`. Reject the
  entire mandatory observation unless every row is unique, under
  `refs/heads/`, type `commit`, and contains non-zero 40-hex head/tree OIDs.
  Invalid, duplicate or undecodable local rows use `E_SURVEY_INVENTORY`; an
  actual `max_branches` overflow remains `E_SURVEY_LIMIT`.

  Freeze the validated local inventory as an exact map from refname to
  `(objectname, objecttype, tree)`. Derive the candidate merged set with this
  subcommand tuple passed to the existing trusted `_text()` helper (Python
  pseudocode, not a shell command or direct `subprocess` argv):

  ```python
  (
      "for-each-ref",
      f"--merged={base_head}",
      "--sort=refname",
      f"--count={max_branches + 1}",
      "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(tree)",
      "refs/heads/",
  )
  ```

  Parse the merged rows with the same closed row validator. Each row must be
  unique, belong to the initial inventory and match its exact head, type and
  tree. Immediately after that query, repeat the initial bounded local
  inventory with the same sort, count, four-field format and `refs/heads/`
  prefix; require exact equality of the complete ref/head/type/tree map. A
  duplicate, unexpected row, identity mismatch or post-query map drift returns
  `UNKNOWN` with `E_SURVEY_INVENTORY`. Only then may a local branch absent from
  the validated merged set derive `has_unique_commits=True`. This attributes
  reachability per branch and handles shared heads without copying the guard's
  existential `rev-list --max-count=1` result or adding a process per branch.

  Validate `remote_name` against the bounded `git remote` inventory and
  `git check-ref-format refs/remotes/<remote>/sentinel`. Query only the expected
  homonymous remote refs using the exact `[r]...` pattern strategy already
  proven in `git_guards.py`, with `--count max_branches+1` and format:

  ```text
  %(refname)%00%(objectname)%00%(objecttype)
  ```

  Reject unexpected/duplicate refs, non-commit objects, invalid OIDs, undecodable
  output or a count overflow as `E_SURVEY_REMOTE_UNKNOWN`. Only a complete
  inventory may set `remote_tracking_ref_present=False`.

  Construct each normative branch with `added_paths=None` and:

  ```python
  unpublished = (
      tree != base_tree
      and ref not in merged_local_refs
      and expected_remote_ref not in observed_remote_refs
  )
  ```

  Count `unpublished_unique_branches`, compute `_survey_status()` and only then
  enter optional enrichment in Task 3.

- [ ] **Step 6: Emit only the V2 payload**

  `survey_payload()` must return the approved mapping: `comparison`, V2 branch
  fields, all three orphan counts, `other_clones="UNKNOWN"`, status,
  `error_code` and `authorizes=False`. It must never serialize
  `only_in_branch` or a V1 kind.

- [ ] **Step 7: Run GREEN for the first slice**

  Re-run the Task 1 focal command. Expected: all `test_core_survey` and
  `test_core_contract` cases PASS; `survey.py` is at most 450 lines and the
  active runtime remains at most 21,530 lines. The Core lock is expected to be
  stale until Task 7 and is not hidden or relaxed.

### Task 2: Freeze the base and fail closed on mandatory uncertainty

**Files:**

- Modify: `tests/test_core_survey.py`
- Modify: `control_plane/survey.py`

- [ ] **Step 1: Write RED mandatory-uncertainty tests**

  The three predicate counterexamples already exist and are RED before the Task
  1 implementation. This task now adds only the uncertainty and integrity
  fixtures that depend on the closed V2 model.

  Add separate uncertainty tests partitioned by failure domain:

  - missing or invalid remote, remote-inventory timeout or decode/structure
    failure, duplicate/unexpected remote row, remote count overflow, and a
    remote ref that does not point to commit assert `status="UNKNOWN"` with
    `E_SURVEY_REMOTE_UNKNOWN`;
  - local-inventory, reachability or postinventory timeout/decode failure,
    invalid or duplicate local rows, identity mismatch or ref drift, and local
    shallow or ambiguous shallow-state observation assert `status="UNKNOWN"`
    with `E_SURVEY_INVENTORY`;
  - branch or worktree limit overflow asserts `status="UNKNOWN"` with
    `E_SURVEY_LIMIT`.

  The shallow fixtures must use only local temporary repositories and must not
  contact a network. At least one UNKNOWN payload test must also assert
  `worktrees is None`, `branches is None` and every `orphan_work` value is
  `None`; never accept a fabricated boolean, zero or empty collection.

  Add two separate complete-inventory edge tests; neither is an UNKNOWN case:

  - two local refs sharing one unique head, with a homonymous remote ref for
    only one, attribute publication per local ref: the published ref is exempt,
    the other has `unpublished_unique=True`, the aggregate count is 1 and the
    normative status is `FAIL`;
  - a repository with no local branches and no stash or untracked residue emits
    `branches=[]`, all three orphan counters as 0 and `status="PASS"`.

- [ ] **Step 2: Prove base-name mutation cannot redirect later comparisons**

  Wrap the mandatory Git helper in a test that records every argv. Immediately
  after the first successful `rev-parse --verify <base>^{commit}`, move the base
  ref to a different commit. Assert the observation retains the first
  `base_head` and every later tree, merged and diff argument contains that OID,
  not the mutable base name.

  The test must fail against any implementation that passes `base` again after
  fixation. Do not solve this by disabling the mutation or weakening the argv
  assertion.

- [ ] **Step 3: Run RED**

  Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_survey"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected: new ambiguity/frozen-base cases fail for their intended missing
  validation while Task 1 cases remain green.

- [ ] **Step 4: Make mandatory reads strict and snapshot-bound**

  Extend `_git()`/`_text()` with an explicit float timeout. Decode normative
  text with `errors="strict"`; return the domain UNKNOWN on decode failure.
  Every later command receives `base_head` or `base_tree`, never `base_ref`.
  Validate sets both ways: merged refs are a subset of local refs; observed
  remote refs are a subset of expected homonymous refs. A partial inventory is
  not accepted merely because a risky branch was already found. Before using
  reachability, require exact `git rev-parse --is-shallow-repository` output
  `false`; `true`, malformed output or observation failure returns
  `E_SURVEY_INVENTORY`/`UNKNOWN` rather than treating truncated history as
  complete.

- [ ] **Step 5: Run GREEN and zero-mutation regression**

  Re-run `tests.test_core_survey`. Expected: PASS, including the existing
  repository snapshot test. Add an assertion that local refs, index, worktree,
  stash and config bytes match before/after each adversarial UNKNOWN case.

### Task 3: Make `added_paths` nullable under one shared deadline

**Files:**

- Modify: `tests/test_core_survey.py`
- Modify: `control_plane/survey.py`

- [ ] **Step 1: Write RED optionality tests**

  Test the internal enrichment seam, not `time.sleep()`:

  ```python
  def test_added_paths_failure_does_not_degrade_normative_fail(self) -> None:
      from unittest.mock import patch
      from control_plane import survey as survey_module

      with tempfile.TemporaryDirectory() as raw:
          repository = _repository(Path(raw))
          _git(repository, "switch", "-c", "feature")
          (repository / "a.txt").write_text("unique\n", encoding="utf-8")
          _git(repository, "commit", "--quiet", "-am", "unique")
          _git(repository, "switch", "main")
          real_text = survey_module._text

          def fail_only_diff(repo, arguments, **kwargs):
              if arguments[:2] == ("diff", "--diff-filter=A"):
                  return None
              return real_text(repo, arguments, **kwargs)

          with patch.object(survey_module, "_text", side_effect=fail_only_diff):
              observed = survey_repository(repository, base="main")

          feature = next(item for item in observed.branches if item.name == "feature")
          self.assertIsNone(feature.added_paths)
          self.assertTrue(feature.unpublished_unique)
          self.assertEqual(observed.status, "FAIL")
          self.assertIsNone(observed.error_code)
  ```

  Add a deterministic clock test with at least three branches. The first
  enrichment succeeds, the second consumes the shared deadline, the second and
  third return `None`, no third diff process is invoked, and the normative
  fields/status remain unchanged. Assert every diff uses
  `<base_head>..<branch_head>` OIDs.

- [ ] **Step 2: Run RED**

  Run `tests.test_core_survey`. Expected: failure because V1/Task 1 has no
  isolated enrichment budget or because optional failure still degrades the
  whole observation.

- [ ] **Step 3: Implement the exact enrichment seam**

  Use an internal helper with one clock injection point:

  ```python
  def _optional_added_paths(
      root: Path,
      base_head: str,
      branches: tuple[BranchObservation, ...],
      *,
      clock=time.monotonic,
  ) -> tuple[BranchObservation, ...]:
      deadline = clock() + _ADDED_PATHS_BUDGET_SECONDS
      enriched: list[BranchObservation] = []
      exhausted = False
      for branch in branches:
          if exhausted:
              enriched.append(branch)
              continue
          remaining = deadline - clock()
          if remaining <= 0:
              exhausted = True
              enriched.append(branch)
              continue
          added = _text(
              root,
              (
                  "diff",
                  "--diff-filter=A",
                  "--name-only",
                  f"{base_head}..{branch.head}",
              ),
              timeout=min(_TIMEOUT_SECONDS, remaining),
          )
          if added is None:
              exhausted = True
              enriched.append(branch)
              continue
          enriched.append(
              replace(
                  branch,
                  added_paths=len([item for item in added.splitlines() if item]),
              )
          )
      return tuple(enriched)
  ```

  Define `_ADDED_PATHS_BUDGET_SECONDS = 10.0` and import `replace`/`time`.
  Branches not enriched retain the `added_paths=None` value assigned during the
  normative phase. Do not catch mandatory failures in this helper or reuse its
  nullable semantics for mandatory evidence.

- [ ] **Step 4: Run GREEN and budget checks**

  Re-run `tests.test_core_survey` and `tests.test_core_contract`. Expected PASS,
  no real sleep, no call after deadline exhaustion, `survey.py <= 450` and total
  active LOC `<= 21_530`.

### Task 4: Migrate the CLI to V2 and four distinct exits

**Files:**

- Modify: `tests/test_core_cli.py:664-689`
- Modify: `control_plane/cli.py:128-183`
- Modify: `control_plane/cli.py:389-415`
- Modify: `control_plane/cli.py:1101-1105`

- [ ] **Step 1: Write RED CLI tests for all terminal states**

  Replace the V1 survey CLI test with a table that proves:

  ```python
  self.assertEqual(pass_code, 0)
  self.assertEqual(pass_payload["kind"], "RepositorySurveyV2")
  self.assertTrue(pass_payload["ok"])

  self.assertEqual(warn_code, 3)
  self.assertEqual(warn_payload["status"], "WARN")
  self.assertFalse(warn_payload["ok"])

  self.assertEqual(fail_code, 1)
  self.assertEqual(fail_payload["status"], "FAIL")
  self.assertEqual(fail_payload["facts"]["orphan_unpublished_unique_branches"], 1)

  self.assertEqual(unknown_code, 2)
  self.assertEqual(unknown_payload["status"], "UNKNOWN")
  self.assertEqual(unknown_payload["kind"], "RepositorySurveyV2")
  self.assertEqual(unknown_payload["facts"]["branches"], "UNKNOWN")
  ```

  Add human-output assertions for leading lines `PASS survey`, `WARN survey`,
  `FAIL survey` and `UNKNOWN survey`. Add `--remote upstream` coverage using a
  locally configured remote and no network. Patch `survey_repository` first to
  raise `ValueError("E_SURVEY_INVENTORY: induced")` and then
  `RuntimeError("attacker-controlled text without a code")`. Both exceptional
  routes must emit the complete V2 UNKNOWN shape: `comparison` and `clone`
  context, `worktrees=None`, `branches=None`, every `orphan_work` count `None`,
  `other_clones="UNKNOWN"`, UNKNOWN facts and exit 2. The second case must map
  to `E_SURVEY_INVENTORY`, and neither error payload may contain the arbitrary
  exception suffix or attacker-controlled text.

  Add three more RED seams so the guarantee covers the whole terminal path:

  - patch `Path.resolve` as used inside `survey_repository` to raise `OSError`
    and prove the handler does not call it again while building the fallback;
  - patch `control_plane.survey.survey_payload` to raise after a successful
    observation and require the same closed UNKNOWN payload;
  - return an observation double whose `branches` access raises during `facts`
    projection and require the same result.

  Every case asserts exit 2 and that no exception text reaches JSON or human
  output. An exception from `_emit()` itself is outside this handler boundary;
  all data passed to it by Survey must already be a closed primitive mapping.

- [ ] **Step 2: Run RED**

  Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_cli"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Run the additional new methods by exact name. Expected: WARN currently
  collapses to exit 1, parser rejects `--remote`, and exceptional output uses
  generic schema 1.

- [ ] **Step 3: Implement explicit Survey rendering and exit mapping**

  Extend `_render_human()` to admit `WARN` only for Survey. In `_emit()`, map
  Survey status before the generic `ok` fallback:

  ```python
  if payload.get("command") == "survey":
      survey_exit = {"PASS": 0, "FAIL": 1, "UNKNOWN": 2, "WARN": 3}
      status = payload.get("status")
      if status in survey_exit:
          return survey_exit[status]
  ```

  In `command_survey()`, pass `remote_name=arguments.remote`, set `ok` only for
  PASS and add `orphan_unpublished_unique_branches`. For UNKNOWN, every fact
  whose count was not observed is the string `UNKNOWN`, never `0` or an empty
  list-derived count.

  Wrap `survey_repository`, `survey_payload`, payload augmentation and `facts`
  projection in one `try`. The `except` must call a Survey-specific fallback
  builder that performs no filesystem, Git, network or object-resolution call.
  It derives context only from already parsed primitive arguments: a real
  `Path` becomes lexical `str(arguments.repo)` without `.resolve()`; unexpected
  types become the literal `UNKNOWN`. `base` and `remote` are retained only
  when they are bounded strings, otherwise they also become `UNKNOWN`.

  The fallback has the same domain shape as any other V2 UNKNOWN:

  ```python
  {
      "schema_version": 2,
      "kind": "RepositorySurveyV2",
      "comparison": {
          "base_ref": arguments.base,
          "base_head": None,
          "remote_name": arguments.remote,
      },
      "clone": {
          "root": lexical_requested_root,
          "common_git_dir": None,
          "branch": None,
          "head": None,
      },
      "worktrees": None,
      "branches": None,
      "orphan_work": {
          "stashes": None,
          "untracked_total": None,
          "unpublished_unique_branches": None,
      },
      "other_clones": "UNKNOWN",
      "command": "survey",
      "ok": False,
      "status": "UNKNOWN",
      "error_code": observed_code,
      "facts": unknown_facts,
      "errors": [{"code": observed_code, "message": stable_message}],
      "authorizes": False,
  }
  ```

  Do not expose arbitrary exception text and do not call `str(error)` in the
  fallback. Read a candidate only when `error.args[0]` is already a bounded
  string, take its prefix before `:`, and pass it through an exact allowlist
  containing only `E_SURVEY_BASE_UNKNOWN`,
  `E_SURVEY_INVENTORY`, `E_SURVEY_LIMIT` and `E_SURVEY_REMOTE_UNKNOWN`; map
  every other value to `E_SURVEY_INVENTORY`. Use a constant bounded message
  such as `Survey observation could not be completed.` rather than `str(error)`.
  Add parser option:

  ```python
  survey.add_argument("--remote", default="origin")
  ```

- [ ] **Step 4: Run GREEN and combined focal**

  Run all of `tests.test_core_cli`, `tests.test_core_survey` and
  `tests.test_core_contract`. Expected PASS with exact exit 3 for warning-only
  residue and no V1 discriminator on any Survey terminal path.

### Task 5: Prove parity with the pre-push guard without coupling runtime

**Files:**

- Modify: `tests/test_core_git_guards.py:207-228`
- Modify: `tests/test_core_git_guards.py:411-425`
- Modify: `tests/test_core_survey.py`
- Verify only: the conditional `control_plane/git_guards.py` change from Task 0;
  no additional guard edit is permitted in this task

- [ ] **Step 1: Capture the consumer RED before editing guard tests**

  With Tasks 1-4 runtime and Survey tests green, run the existing
  `tests.test_core_git_guards` unchanged:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_git_guards"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected RED: the old consumer still expects Survey `PASS`/`FAIL` V1 status
  or `only_in_branch`. A guard-runtime failure or a different regression is not
  the intended RED and must be diagnosed before changing assertions.

- [ ] **Step 2: Update the motivating fixture to assert V2 truth**

  In the existing modified-only branch fixture replace the old blind-spot
  assertions:

  ```python
  self.assertEqual(survey.status, "FAIL")
  self.assertEqual(branch.added_paths, 0)
  self.assertFalse(branch.content_equivalent_to_base)
  self.assertTrue(branch.has_unique_commits)
  self.assertFalse(branch.remote_tracking_ref_present)
  self.assertTrue(branch.unpublished_unique)
  ```

  Keep the guard assertion exactly
  `GG_UNPUBLISHED_UNIQUE_BRANCH`. Rename the test so it no longer claims Survey
  is passing.

- [ ] **Step 3: Add cross-fixture parity cases**

  For each existing guard scenario, assert the corresponding Survey branch:

  | Fixture | Survey | Guard |
  |---|---|---|
  | tree differs, unique commit, no homonymous ref | `unpublished_unique=True`, `FAIL` | blocks unique branch |
  | valid homonymous ref, even behind | `False` | permits on that signal |
  | tree equal after squash | `False` | permits |
  | branch fully merged/behind | `False` | permits |
  | mandatory inventory ambiguous | `UNKNOWN` | state unknown |
  | shallow/incomplete reachability | `UNKNOWN` | state unknown |

  Add an AST/import assertion that `control_plane.survey` does not import
  `git_guards` and `control_plane.git_guards` does not import `survey`.

- [ ] **Step 4: Run GREEN parity check**

  After Steps 2-3 update only tests, run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_git_guards", "tests.test_core_survey"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected GREEN with no new change to `control_plane/git_guards.py` after Task
  0. If a fixture proves another guard contradiction, stop and report the exact
  reproduction. Do not repair it inside this front.

- [ ] **Step 5: Create a stable runtime checkpoint after the gates pass**

  Recompute the Core runtime digest first as described in Task 7, run the lock
  focal, stage the closed runtime slice exactly and prove the index:

  ```bash
  git add -- \
    .codex/control-plane.lock \
    control_plane/cli.py \
    control_plane/git_guards.py \
    control_plane/survey.py \
    tests/test_core_cli.py \
    tests/test_core_contract.py \
    tests/test_core_git_guards.py \
    tests/test_core_survey.py
  git diff --cached --name-only
  git diff --cached --check
  git diff --name-only
  git commit -m "feat: add RepositorySurveyV2 orphan semantics"
  ```

  Expected: the cached paths are exactly the modified subset of the list above,
  no unstaged path remains and no unrelated index entry exists. Do not commit a
  runtime whose lock is knowingly stale. No remote transition is required at
  this checkpoint.

### Task 6: Align governing documentation without rewriting history

**Files:**

- Modify: `tests/test_core_git_skill.py`
- Modify: `tests/test_core_documentation.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `skills/control-plane-git/SKILL.md`
- Modify: `docs/engineering/00-canonical-index.md`
- Modify: `docs/engineering/21-repository-alignment-and-branch-decisions.md`
- Modify: `docs/engineering/22-orientation-and-known-traps.md`
- Modify: `docs/superpowers/specs/2026-08-18-control-plane-3-3-operator-orientation-design.md`
- Modify: `docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md`
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`

- [ ] **Step 1: Write RED documentation contracts**

  Add constants for ADR 0008, the V2 spec and this plan. Assert:

  - exactly one canonical-index row for each after implementation closure;
  - design 3.3 links V2 and says only its V1 Survey block is superseded;
  - README and Git skill name `RepositorySurveyV2`, four states/exits,
    `unpublished_unique`, `added_paths=null`, local remote-ref staleness and
    `other_clones=UNKNOWN`;
  - `AGENTS.md`, the Git skill, alignment and orientation never describe
    `git diff --diff-filter=A --name-only` as content equivalence;
  - add-only output is mentioned only as `added_paths` information;
  - the historical 3.3 plan remains byte-untouched;
  - the threat model removes only the two now-closed semantic residuals and
    retains filters, Gitlinks, alternates, detached substitution, TOCTOU,
    newline paths, APFS case folding, symlink/config and rollback findings;
  - the threat model names shallow reachability as untrusted and the conditional
    candidate-only guard check as consuming the existing deadline;
  - no document claims release, adoption, installation or remote proof.

- [ ] **Step 2: Run RED**

  Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_git_skill", "tests.test_core_documentation"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected: failures name V1 wording, three-state docs, add-only equivalence and
  missing governing links. Snapshot mismatch is expected until the final footer
  step; unrelated documentation failures are not.

- [ ] **Step 3: Apply the minimum governing updates**

  Use this operational equivalence rule everywhere:

  ```bash
  git diff --quiet <fixed-base-oid>..<fixed-branch-oid>
  ```

  or compare the two fixed tree OIDs directly. Reserve:

  ```bash
  git diff --diff-filter=A --name-only <fixed-base-oid>..<fixed-branch-oid>
  ```

  solely for the nullable `added_paths` enrichment.

  In design 3.3 add a dated supersession notice linking the approved V2 spec
  and ADR 0008; leave its V1 JSON as historical provenance. In the canonical
  index use `IMPLEMENTED_LOCAL_CANDIDATE / FINAL_GATE_PENDING` only after Tasks
  1-5 behavior is green. That state may make V2 the governing local candidate,
  but it must not claim final gate, integration, release or adoption evidence.

  In the threat model close the semantic blind spot, add attacker stories for
  stale local remote refs, WARN collapse, mandatory/optional evidence confusion
  and base-ref substitution, and state the exact mitigations. Keep the existing
  footer values temporarily; final reseal is Task 7.

- [ ] **Step 4: Promote status only after behavior evidence exists**

  Once Tasks 1-5 focal tests are green, change the V2 spec status to
  `IMPLEMENTED_LOCAL_CANDIDATE / FINAL_GATE_PENDING`. Do not claim closure,
  release, adoption or CI. Keep this plan at the execution state set in Task 0;
  the eventual terminal evidence is recorded outside the tracked tree after
  freeze.

- [ ] **Step 5: Run documentation GREEN except the expected footer**

  Re-run `tests.test_core_git_skill` and the new targeted documentation method.
  If the only failure is the repository-scoped Version footer, proceed to Task
  7. Any other failure is repaired within the listed documentation paths or
  reported as a scope contradiction.

### Task 7: Reseal, freeze, verify and prepare the integration boundary

**Files:**

- Modify: `.codex/control-plane.lock`
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Modify: `docs/superpowers/plans/2026-08-21-repository-survey-v2.md` only for
  concise observed evidence/checkmarks
- Verify only: all paths in the file map

- [ ] **Step 1: Prove the implementation stayed inside the boundary**

  Run:

  ```bash
  git diff --name-only origin/main
  git diff --numstat origin/main
  git diff --check origin/main
  git diff --check
  wc -l control_plane/survey.py
  ```

  Allowed paths are exactly those in this plan. No `.github/`, dependency,
  Adoption, new module, `tests/run.sh`, registry or policy path is allowed.
  `git_guards.py` may differ only by the exact Task 0 shallow correction and its
  existing caller-compatible timeout parameter. `survey.py` must be at most 450
  lines. Run the existing Core contract test to prove 27 modules and total
  active LOC `<=21_530`. Use
  `git diff --check origin/main` as well as the worktree-only check so committed
  checkpoints cannot hide whitespace errors.

- [ ] **Step 2: Reseal only the Core runtime digest**

  Compute the candidate digest without writing:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys; from pathlib import Path; sys.path.insert(0, "."); from control_plane.lockfile import runtime_digest; print(runtime_digest(Path(".")))'
  ```

  Use `apply_patch` to replace only `digests.runtime` in
  `.codex/control-plane.lock`. Do not change product version, module list, hook
  fields or another digest. Then run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_lockfile", "tests.test_core_contract"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  scripts/control-plane doctor
  ```

  Expected: lock/contract PASS and doctor PASS. Any other lock drift stops the
  front; do not reseal unrelated inputs opportunistically.

- [ ] **Step 3: Run all focused behavior and documentation tests**

  Run one combined focal invocation:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_survey", "tests.test_core_cli", "tests.test_core_git_guards", "tests.test_core_git_skill", "tests.test_core_contract", "tests.test_core_lockfile", "tests.test_core_documentation"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected: every functional and documentation assertion passes except the
  known stale threat footer before Step 4. Record the exact count and duration;
  do not predict them in advance.

- [ ] **Step 4: Reseal the repository-scoped threat footer last**

  Compute the normalized value over the final tracked/untracked overlay:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys; sys.path.insert(0, "."); from tests.test_core_documentation import normalized_snapshot_version; print(normalized_snapshot_version())'
  ```

  Replace only the final `Version:` line with `apply_patch`; keep the Repository
  identity unchanged. Then run the exact threat snapshot/documentation focal and
  repeat `git diff --check origin/main` and `git diff --check`.

- [ ] **Step 5: Freeze bytes and obtain one bounded independent review**

  After this point, do not edit before the full gate unless the reviewer finds
  an in-scope Critical or Important defect. Review only the decision delta
  against the approved spec/ADR, with explicit checks for:

  - per-branch attribution and fixed OIDs;
  - remote absence proof and UNKNOWN paths;
  - WARN/FAIL/UNKNOWN distinction in every CLI route;
  - optional deadline isolation;
  - Survey/guard import independence;
  - candidate-only shallow observation, strict decoding and consumption of the
    existing guard deadline without changing its predicate or error vocabulary;
  - exact scope and retained hardening residuals.

  Required result: 0 Critical and 0 Important. A Minor that does not affect the
  contract is recorded, not allowed to grow this front. Any repair invalidates
  the footer, freeze and prior review and returns to focused tests before
  consuming a full gate. From this freeze onward, do not check boxes or append
  results inside this tracked plan; record terminal evidence in the external
  checkpoint so the reviewed and gated tree remains byte-identical.

- [ ] **Step 6: Run the full gate within `max_gate_runs=6`**

  Delegate the long wait to a fresh disposable executor with a small context.
  The writer/orchestrator does not poll from the long-lived session. Run exactly:

  ```bash
  bash tests/run.sh
  ```

  The executor returns one terminal result: exit, test count, duration and first
  failure. If red, reproduce the failing behavior focally, repair only in-scope
  bytes, reseal, re-review and consume the next attempt. At six consumed runs,
  emit Stable Pause. Never delete tests, raise limits, split CI or weaken the
  manifest to fit the budget.

- [ ] **Step 7: Run fresh post-gates on the same bytes**

  Run:

  ```bash
  scripts/control-plane policy-check --policy .codex/project-policy.toml
  scripts/control-plane registry-check \
    --registry .codex/resource-registry.toml \
    --policy .codex/project-policy.toml
  scripts/control-plane doctor
  git diff --check origin/main
  git diff --check
  git status --short --branch
  ```

  Expected: all executable gates PASS; status shows only the intended front.
  Because the tree is intentionally changed, write-preflight is not rerun as a
  fake clean gate after implementation. The final full gate and post-gates bind
  the changed bytes.

- [ ] **Step 8: Final review, exact staging and local commit**

  A fresh read-only reviewer checks the frozen decision delta and returns 0
  Critical / 0 Important. Stage only the complete allowed path set; unchanged
  checkpointed files are harmless no-ops, while any staged path outside this
  list is a Stable Pause:

  ```bash
  git add -- \
    .codex/control-plane.lock \
    AGENTS.md \
    README.md \
    control_plane/cli.py \
    control_plane/git_guards.py \
    control_plane/survey.py \
    docs/adr/0008-repository-survey-v2-contract.md \
    docs/engineering/00-canonical-index.md \
    docs/engineering/21-repository-alignment-and-branch-decisions.md \
    docs/engineering/22-orientation-and-known-traps.md \
    docs/security/2026-08-12-control-plane-core-threat-model.md \
    docs/superpowers/plans/2026-08-21-repository-survey-v2.md \
    docs/superpowers/specs/2026-08-18-control-plane-3-3-operator-orientation-design.md \
    docs/superpowers/specs/2026-08-21-repository-survey-v2-design.md \
    skills/control-plane-git/SKILL.md \
    tests/test_core_cli.py \
    tests/test_core_contract.py \
    tests/test_core_documentation.py \
    tests/test_core_git_guards.py \
    tests/test_core_git_skill.py \
    tests/test_core_survey.py
  git diff --cached --name-only
  git diff --cached --check
  git diff --name-only
  survey_reviewed_tree=$(git write-tree)
  git commit -m "feat: align Survey orphan semantics"
  test "$(git rev-parse 'HEAD^{tree}')" = "$survey_reviewed_tree"
  ```

  Expected: the cached diff is a subset of exactly those paths, the unstaged
  diff is empty and the committed tree equals the reviewed tree. Re-run cheap
  post-gates whose inputs include commit state. If this final review finds an
  in-scope Critical or Important defect, do not commit: repair, rerun focused
  tests, reseal, obtain a fresh pre-gate review and consume a new full-gate
  attempt before reviewing again.

### Task 8: Integrate under standing Git authority

**Files:** verify only; no tracked edits after the frozen commit

- [ ] **Step 1: Reobserve provider identity, refs and protection before each transition**

  Run host-native reads, not the quarantined Core refresh path. Bind the local
  remote to the expected provider repository, inspect both effective rulesets
  (which support a not-yet-created branch name) and the complete classic branch
  protection pattern inventory, then inspect refs and PRs:

  ```bash
  survey_repo=AndreaBusta/codex-engineering-control-plane
  survey_branch=codex/survey-orphan-semantics-v1
  survey_target=main
  survey_local_head="$(git rev-parse HEAD)"
  survey_current_branch="$(git symbolic-ref --short HEAD)" || exit $?
  test "$survey_current_branch" = "$survey_branch"
  survey_branch_head="$(git rev-parse --verify \
    "refs/heads/$survey_branch^{commit}")" || exit $?
  test "$survey_branch_head" = "$survey_local_head"
  survey_fetch_urls="$(git remote get-url --all origin)" || exit $?
  survey_push_urls="$(git remote get-url --push --all origin)" || exit $?
  case "$survey_fetch_urls" in
    https://github.com/AndreaBusta/codex-engineering-control-plane|\
    https://github.com/AndreaBusta/codex-engineering-control-plane.git|\
    git@github.com:AndreaBusta/codex-engineering-control-plane|\
    git@github.com:AndreaBusta/codex-engineering-control-plane.git|\
    ssh://git@github.com/AndreaBusta/codex-engineering-control-plane|\
    ssh://git@github.com/AndreaBusta/codex-engineering-control-plane.git) ;;
    *) printf '%s\n' E_PROVIDER_FETCH_IDENTITY >&2; exit 1 ;;
  esac
  case "$survey_push_urls" in
    https://github.com/AndreaBusta/codex-engineering-control-plane|\
    https://github.com/AndreaBusta/codex-engineering-control-plane.git|\
    git@github.com:AndreaBusta/codex-engineering-control-plane|\
    git@github.com:AndreaBusta/codex-engineering-control-plane.git|\
    ssh://git@github.com/AndreaBusta/codex-engineering-control-plane|\
    ssh://git@github.com/AndreaBusta/codex-engineering-control-plane.git) ;;
    *) printf '%s\n' E_PROVIDER_PUSH_IDENTITY >&2; exit 1 ;;
  esac
  printf 'provider_remote=%s\n' "$survey_repo"
  gh repo view \
    --json nameWithOwner,url,sshUrl,defaultBranchRef,deleteBranchOnMerge
  gh api "repos/$survey_repo/branches/$survey_target" \
    --jq '{name,protected,sha:.commit.sha}'
  gh ruleset check --default --repo "$survey_repo"
  gh ruleset check "$survey_branch" --repo "$survey_repo"
  gh api graphql \
    -F owner=AndreaBusta \
    -F name=codex-engineering-control-plane \
    -f query='query($owner:String!,$name:String!){repository(owner:$owner,name:$name){nameWithOwner branchProtectionRules(first:100){nodes{pattern} pageInfo{hasNextPage}}}}'
  git ls-remote --heads origin \
    refs/heads/main \
    refs/heads/codex/survey-orphan-semantics-v1
  survey_remote_rows="$(git ls-remote --heads origin \
    "refs/heads/$survey_branch")" || exit $?
  survey_remote_head="$(printf '%s\n' "$survey_remote_rows" | \
    awk 'NR == 1 { oid=$1 } END { if (NR > 1) exit 2; print oid }')" || exit $?
  gh pr list \
    --repo "$survey_repo" \
    --state all \
    --head "$survey_branch" \
    --json number,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,url
  ```

  The symbolic branch and its full local ref must bind exactly to
  `survey_branch` and `survey_local_head`; detached HEAD, a different branch or
  a moved branch ref is a Stable Pause. The raw fetch and push URL values stay
  captured and are never echoed. The closed `case` values accept exactly one
  standard credential-free HTTPS or SSH destination for this provider
  repository; multiple URLs, userinfo/PAT, `pushurl` or `pushInsteadOf`
  redirection to any other destination fail before mutation. Expected:
  `gh repo view` resolves the same repository to exactly
  `AndreaBusta/codex-engineering-control-plane`; its reported HTTPS or SSH URL
  matches the normalized identity, its default branch is `main`, the target API
  reports protected `main` at the task base and the GraphQL protection inventory is complete
  (`hasNextPage=false`). Together, the effective ruleset reads and classic
  patterns must classify `main` as protected and the exact work-branch name as
  non-protected. If the work ref already exists, also read
  `repos/$survey_repo/branches/codex%2Fsurvey-orphan-semantics-v1` and require
  `protected=false` plus its exact SHA.

  The remote work ref may be: absent before its first creation; equal to the
  local HEAD; or, only during a repair cycle, equal to the exact
  `survey_last_published_head` recorded after the preceding push and an ancestor
  of the newly gated local HEAD. A missing ref proves only first creation, and a
  repair ancestor permits only an ordinary fast-forward. Any incomplete
  provider page, identity mismatch, target drift, protection ambiguity,
  unexpected remote SHA, duplicate PR or other `UNKNOWN` is a Stable Pause.

- [ ] **Step 2: Create or fast-forward only the verified work ref**

  If the ref is absent, create it exactly once. If it already equals local
  HEAD, perform no push. In a review/CI repair cycle, first require the observed
  remote SHA to equal the previously recorded `survey_last_published_head`, and
  prove that exact SHA is an ancestor of current local HEAD:

  ```bash
  if test "$survey_remote_head" != "$survey_local_head"; then
    if test -n "$survey_remote_head"; then
      test "$survey_remote_head" = "${survey_last_published_head:?}"
      git merge-base --is-ancestor \
        "$survey_last_published_head" "$survey_local_head"
    fi
    survey_current_branch="$(git symbolic-ref --short HEAD)" || exit $?
    test "$survey_current_branch" = "$survey_branch"
    survey_branch_head="$(git rev-parse --verify \
      "refs/heads/$survey_branch^{commit}")" || exit $?
    test "$survey_branch_head" = "$survey_local_head"
    git push --set-upstream origin \
      "refs/heads/$survey_branch:refs/heads/$survey_branch"
  fi
  git ls-remote --heads origin \
    refs/heads/main \
    refs/heads/codex/survey-orphan-semantics-v1
  survey_verified_remote_rows="$(git ls-remote --heads origin \
    "refs/heads/$survey_branch")" || exit $?
  survey_verified_remote_head="$(printf '%s\n' \
    "$survey_verified_remote_rows" | \
    awk 'NR == 1 { oid=$1 } END { if (NR != 1) exit 2; print oid }')" || exit $?
  test "$survey_verified_remote_head" = "$survey_local_head"
  ```

  The ancestor command applies only to the repair case; it is skipped for first
  creation and for an exact no-op. Use an ordinary push, never force or
  force-with-lease. Re-run the identity, protection and ref reads from Step 1
  immediately afterward. Expected: the remote work ref equals local HEAD and
  `main` is unchanged. Record that verified SHA as the new
  `survey_last_published_head` in the compact checkpoint. Do not push a
  protected ref, delete a ref or create a preservation alias.

- [ ] **Step 3: Open one ready PR against the observed base**

  If Step 1 found no PR, create exactly one ready PR:

  ```bash
  gh pr create \
    --repo AndreaBusta/codex-engineering-control-plane \
    --base main \
    --head codex/survey-orphan-semantics-v1 \
    --title "feat: align Survey orphan semantics" \
    --body "Implements the accepted RepositorySurveyV2 contract with fail-closed mandatory evidence, nullable added_paths, four CLI exits, bounded Survey/guard parity, updated governance, and no release or adoption effect."
  ```

  Re-read the PR with `gh pr view --json
  number,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,mergeable,mergeStateStatus,statusCheckRollup,url`
  and require exact provider repository, base/head and `isDraft=false`.
  Re-run Step 1 before any later PR update. A check may still be pending after
  creation; never interpret an absent, skipped or stale check as green.

- [ ] **Step 4: Select exact-head CI and address review through bounded fast-forwards**

  Discover the run by workflow, event, branch and exact commit before waiting:

  ```bash
  survey_pr_number="$(gh pr view "$survey_branch" \
    --repo "$survey_repo" --json number --jq .number)"
  survey_head_sha="$(git rev-parse HEAD)"
  survey_run_id="$(gh run list \
    --repo "$survey_repo" \
    --workflow control-plane.yml \
    --event pull_request \
    --branch "$survey_branch" \
    --commit "$survey_head_sha" \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId')"
  test -n "$survey_run_id"
  gh run view "$survey_run_id" \
    --repo "$survey_repo" \
    --json databaseId,attempt,event,headBranch,headSha,status,conclusion,workflowName,url
  ```

  If the run has not appeared yet, give this exact discovery to the disposable
  executor with a bounded two-minute appearance deadline; do not poll it from
  the orchestrator. Absence after that deadline is `UNKNOWN`. Require the
  selected record to have the exact `survey_head_sha`, branch,
  `pull_request` event and `Control Plane Core` workflow. Delegate one long
  `gh run watch "$survey_run_id" --repo "$survey_repo" --exit-status` session
  to a fresh disposable executor. Its terminal result must include a fresh
  `gh run view` of that same ID showing exact head, `status=completed` and
  `conclusion=success`. Then re-read the PR head and run `gh pr checks
  "$survey_pr_number" --repo "$survey_repo" --required --json
  name,bucket,state,workflow,link`; require `core-verify` in the `pass` bucket
  and no required check absent, pending, skipped, cancelled or failed.

  Inspect all review threads on that exact head. A Critical or Important
  finding returns to the relevant focal RED and invalidates prior freeze,
  review, full-gate and CI evidence. After the fix, reseal, rerun focused tests,
  obtain fresh review, consume a new full-gate attempt and commit. Then rerun
  Step 1: only if the provider still reports the exact preceding
  `survey_last_published_head` and it is an ancestor of the new local HEAD may
  Step 2 publish an ordinary fast-forward. Select and wait for a new exact-head
  run ID afterward. Resolve a thread only after its fix is present on that
  provider-observed head. Any other remote movement is drift, not a repair
  path.

- [ ] **Step 5: Squash only after final provider reobservation**

  Before merge, rerun all Step 1 identity, ref and protection observations and
  the exact run/required-check reads from Step 4. Prove: PR OPEN and ready, base
  unchanged and protected, source non-protected, local/remote/PR head identical,
  zero unresolved blocking threads, required CI SUCCESS on that exact SHA, and
  no merge-triggered deploy, release, publication, dependency install, CI
  mutation or secret effect. Automatic source-branch deletion is also a merge
  effect, so require the freshly observed repository setting to be false; if it
  is true, stop before merge rather than accepting or changing it implicitly.
  Then:

  ```bash
  survey_delete_branch_on_merge=$(gh repo view "$survey_repo" \
    --json deleteBranchOnMerge --jq .deleteBranchOnMerge)
  test "$survey_delete_branch_on_merge" = false
  survey_pr_number=$(gh pr view \
    --repo AndreaBusta/codex-engineering-control-plane \
    codex/survey-orphan-semantics-v1 \
    --json number \
    --jq .number)
  survey_head_sha=$(git rev-parse HEAD)
  gh pr merge "$survey_pr_number" \
    --repo AndreaBusta/codex-engineering-control-plane \
    --squash \
    --match-head-commit "$survey_head_sha" \
    --subject "feat: align Survey orphan semantics"
  ```

  If any required fact is absent, stale, skipped or `UNKNOWN`, do not enable
  auto-merge or use admin bypass.

- [ ] **Step 6: Prove content containment and green main**

  `gh pr merge` may return before a queued merge becomes terminal. Delegate a
  bounded provider wait (maximum 25 minutes) to a disposable executor that
  rereads this exact PR number and returns one terminal record. Do not poll from
  the orchestrator. Only after the record is `MERGED` continue:

  ```bash
  survey_merge_record="$(gh pr view "$survey_pr_number" \
    --repo "$survey_repo" \
    --json state,mergedAt,mergeCommit,headRefName,headRefOid,baseRefName,url)" \
    || exit $?
  survey_merge_sha="$(printf '%s\n' "$survey_merge_record" | \
    /usr/bin/jq -er --arg head "$survey_head_sha" \
    'select(.state == "MERGED" and .mergedAt != null and .mergeCommit.oid != null and .headRefName == "codex/survey-orphan-semantics-v1" and .headRefOid == $head and .baseRefName == "main") | .mergeCommit.oid')" \
    || exit $?
  git fetch origin refs/heads/main:refs/remotes/origin/main
  survey_main_sha="$(git rev-parse origin/main)"
  test "$survey_merge_sha" = "$survey_main_sha"
  git diff --stat origin/main..codex/survey-orphan-semantics-v1
  git diff --quiet origin/main..codex/survey-orphan-semantics-v1
  git ls-remote --heads origin \
    refs/heads/main \
    refs/heads/codex/survey-orphan-semantics-v1
  survey_preserved_remote_rows="$(git ls-remote --heads origin \
    "refs/heads/$survey_branch")" || exit $?
  survey_preserved_remote_head="$(printf '%s\n' \
    "$survey_preserved_remote_rows" | \
    awk 'NR == 1 { oid=$1 } END { if (NR != 1) exit 2; print oid }')" || exit $?
  test "$survey_preserved_remote_head" = "$survey_head_sha"
  ```

  Expected: the terminal PR record identifies the exact pre-merge head and base,
  its squash commit is the newly observed `origin/main`, content diff is empty,
  and the remote work ref still exists at `survey_head_sha`. Select the push run
  exactly as in Step 4, but with `--event push --branch main --commit
  "$survey_main_sha"`, verify its `headSha` before waiting, delegate the one
  exact run ID, and require terminal `Control Plane Core` SUCCESS on the new
  `main` SHA. Do not delete a branch, worktree or tag, and do not prune or clean;
  provider branch deletion never substitutes for content containment.

## Rollback and recovery

Before integration, recovery is preservation-forward: commit gated stable
checkpoints or leave the worktree intact. Do not use `reset --hard`,
`merge --abort`, `git clean`, branch deletion, worktree prune, GC or force push.

After integration, rollback is one reviewed revert that restores V1 atomically:
runtime, CLI, tests, governing docs, Core lock, threat footer and the conditional
guard shallow check if it was activated. Never restore only the old payload
while retaining WARN or V2 docs, and never keep a hidden V1 flag. There is no
data migration or persistent state to transform.

If implementation reveals that per-branch reachability cannot be attributed
within the 450-line/21,530-line budgets, or that guard parity requires any edit
beyond the exact Task 0 shallow exception, stop with the reproduction and
reframe. Those outcomes invalidate the current plan rather than authorizing a
larger diff.

## Completion criteria

This front is locally ready for integration when all `RSV2-*` rows have fresh
evidence, the last full gate is green on frozen bytes, post-gates pass, review
has 0 Critical/0 Important, the lock and threat footer match, no excluded path
changed and a gated local commit preserves the exact tree. It is terminally
complete only after Task 8 proves squash content containment and green CI on the
new `origin/main`. Neither state is release, installation or stable adoption.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora y ejecutora principal.
- **Para continuar:** ejecutar Task 0 Step 4 y después el RED shallow exacto de
  Step 5, antes de cualquier otro runtime.
- **Mensaje exacto:** `Continúa con Task 0 Step 4 y el RED shallow exacto de Step 5.`
- **Estado de partida:** `codex/survey-orphan-semantics-v1` sobre
  `origin/main@250af122`; reframe shallow aceptado, autoridad de implementación
  observada y documentos contractuales preservados por este checkpoint;
  todavía sin edición ni test runtime.
