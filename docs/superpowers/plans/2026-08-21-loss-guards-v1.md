# Loss Guards v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent routine Git transitions from deleting branches or leaving unique work reachable only from a local branch without its own remote ref.

**Architecture:** Extend the existing hook classifier for destructive branch commands and make its closed-launcher fallback `soft-enforce`. Extend `guard_pre_push` with three constant-size Git observations under one five-second deadline: bounded local head/tree inventory, exact local remote-tracking refs, and one aggregate reachability query. `survey_repository()` remains a diagnostic characterization of the motivating blind spot, not the blocking oracle. Preserve the 27-module Core boundary and update only coupled locks, governing documentation, tests, and the repository-scoped threat-model snapshot.

**Tech Stack:** Python 3.11 standard library, `unittest`, Git plumbing through the repository's closed helpers, TOML/JSON lock contracts, POSIX shell full gate.

---

## Contract

- Base: `origin/main@649508856626e03c998555324da780bf7ec4d49c`.
- Worktree: `/Users/bustaseo/Developer/control-plane-worktrees/loss-guards-v1`.
- Branch: `codex/loss-guards-v1`.
- TDD is mandatory: each behavior test must fail for the missing behavior before production bytes change.
- One writer at a time. No dependency, new module, `.github/` change, PR, merge, adoption, installation, deploy, release, branch deletion, prune, GC, reset or force push.
- One final local commit only after focused tests, documentation, Core lock and threat-model snapshot agree.
- The full gate budget is six; the last consumed run must be green on final bytes.
- Push of this branch is authorized. Opening a PR is not authorized.

## Acceptance

1. Default `PreToolUse` behavior is `soft-enforce`. The direct API accepts an
   exact `audit` override, but the distributed closed launcher deliberately
   strips the ambient variable so an untrusted host environment cannot
   downgrade enforcement.
2. Default mode denies `git branch -d`, `git branch -D`, `git push --delete`, and `git push <remote> :refs/...` with `destructive_command_requires_explicit_authority`.
3. `guard_pre_push` returns `GG_UNPUBLISHED_UNIQUE_BRANCH` when another local branch:
   - has no matching `refs/remotes/<remote>/<branch>`;
   - has at least one commit outside `<remote>/<base>`; and
   - has a tree not equivalent to `<remote>/<base>`.
4. The guard permits the same push that publishes that local branch to its matching remote ref.
5. Missing or invalid local-branch, base, remote-ref or commit evidence returns `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN`.
6. The module inventory remains exactly 27, the Core lock validates, governing docs describe the real behavior, the threat-model footer matches final tracked bytes, and the final `bash tests/run.sh` passes.

### Task 1: Destructive branch commands and default soft-enforce

**Files:**

- Modify: `tests/test_core_hooks.py`
- Modify: `tests/test_hooks.py`
- Modify: `tests/test_core_lockfile.py`
- Modify: `tests/test_core_quarantine.py`
- Modify: `control_plane/hooks.py`
- Modify: `control_plane/lockfile.py`
- Modify: `.codex/hooks.json`
- Modify: `.codex/control-plane.lock`

- [x] **Step 1: Write RED tests**

  Add a table-driven Core hook test that removes `CODEX_CONTROL_PLANE_HOOK_MODE` and proves the four destructive branch commands receive `permissionDecision=deny`. Add an explicit-audit regression proving the override remains advisory. Update the lock/quarantine assertions to require `soft-enforce` and non-`audit-only` metadata.

- [x] **Step 2: Verify RED**

  Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_hooks", "tests.test_core_lockfile", "tests.test_core_quarantine", "tests.test_hooks.HookTests.test_destructive_pretool_is_denied_by_default", "tests.test_hooks.HookTests.test_stop_without_receipt_uses_mode_neutral_message", "tests.test_hooks.HookTests.test_raw_read_is_denied_by_default_and_advisory_in_explicit_audit", "tests.test_hooks.HookTests.test_hook_config_is_soft_enforce_by_default_and_uses_git_root"]; result = unittest.TextTestRunner().run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected: the 44 Core tests plus the four changed legacy methods fail only
  on the missing branch patterns, current `audit` fallback, lock contract and
  `audit-only` metadata. The rest of `tests.test_hooks` is not a Core focal: it
  imports quarantined legacy modules absent from the exact 27-module runtime.

- [x] **Step 3: Implement the minimum behavior**

  Add anchored patterns to `DESTRUCTIVE_PATTERNS`; change only the two hook-mode fallbacks to `soft-enforce`; make the manifest/message mode-neutral; change the lock validator to require `soft-enforce`; update hook metadata and the lock's declared mode. Recompute only the affected `hooks` and `runtime` digests in `.codex/control-plane.lock`.

- [x] **Step 4: Verify GREEN**

  Re-run the exact 44-Core-plus-four-method matrix above. Expected: 48/48 PASS
  with no warnings or unexpected output.

### Task 2: Unpublished unique branch pre-push guard

**Files:**

- Modify: `tests/test_core_git_guards.py`
- Modify: `control_plane/git_guards.py`
- Modify: `.codex/control-plane.lock`

- [x] **Step 1: Write RED tests**

  Add real temporary-Git scenarios for: unrelated push blocked by an
  unpublished unique branch; matching branch publication allowed; matching
  existing remote ref allowed; content-equivalent branch allowed; missing
  base survey evidence fails closed. The blocking reproduction must modify an
  existing tracked file so `only_in_branch == 0` while
  `content_equivalent_to_base == false`, matching the real preservation
  incident. It must also prove that top-level `survey.status == PASS` does not
  mean that no branch is at risk.

- [x] **Step 2: Verify RED**

  Run:

  ```bash
  /usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c \
    'import sys, unittest; sys.path.insert(0, "."); result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName("tests.test_core_git_guards")); raise SystemExit(not result.wasSuccessful())'
  ```

  Expected: failures show `GG_UNPUBLISHED_UNIQUE_BRANCH` and `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN` are absent.

- [x] **Step 3: Implement bounded observation**

  Inventory at most 64 local refs plus the exact base, binding head and tree.
  Query only exact matching local remote-tracking refs with a forced full-match
  glob and require each match to be a commit. Skip tree-equivalent branches,
  exact remote matches and the branch published to the same ref and HEAD by the
  current verified push. Feed all remaining refs to one
  `rev-list --max-count=1 ... --not <remote-base>` invocation. Share one
  five-second monotonic deadline across the maximum of three Git subprocesses;
  invalid or ambiguous evidence returns
  `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN`.

- [x] **Step 4: Verify GREEN and regression behavior**

  Re-run `tests.test_core_git_guards`, then run the Task 1 focal modules. Recompute the Core runtime digest in `.codex/control-plane.lock` and repeat the lock focal. Final evidence: Fase 2 20/20, lock 22/22 and Fase 1 48/48; two independent reviews closed at 0 Critical / 0 Important / 0 Minor.

### Task 3: Documentation, closure and authorized push

**Files:**

- Modify: `SECURITY.md`
- Modify: `docs/engineering/09-audit-dafo-and-risk-register.md`
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Modify: `docs/superpowers/plans/2026-08-21-loss-guards-v1.md`

- [x] **Step 1: Declare the operational change**

  Document that trusted hooks default to `soft-enforce` for mechanically recognized high-risk actions, remain bypassable/non-authorizing, and do not replace branch protection. Record the unpublished-unique-branch guard, its UNKNOWN behavior and the matching-publication exemption.

- [x] **Step 2: Record deferred fronts without implementing them**

  Preserve these as separate decisions: Phase 3 (`maintenance.py` CLI wiring
  and `consumes_reframe=True`), Phase 4 (last-green store keyed by tracked-tree
  digest), Phase 5 (remove `remote` from the aggregate `risk_sentinel`
  result), stale-lease TTL/detection, separate remote observation, and Survey
  semantics/hardening. The Survey follow-up must decide whether to redefine
  `orphan_work` and whether to rename or replace `only_in_branch`, which today
  counts added paths rather than commits. None of those changes enter this PR.

- [x] **Step 3: Check the authorized boundary**

  Run `git diff --name-only origin/main` and require every path to be one of the explicitly authorized code, tests, lock/config, documentation or threat-model paths. Require `git diff --check` to pass.

- [x] **Step 4: Realign the repository snapshot**

  Recompute the canonical repository snapshot and update only the threat-model footer after every other tracked byte is final. Run its exact documentation focal.

- [ ] **Step 5: Independent review and full gate**

  Obtain spec-compliance and code-quality reviews with no open Critical or Important issue. Delegate exactly one final `bash tests/run.sh` to a fresh disposable executor; consume another run only after a repaired, re-reviewed final snapshot.

- [ ] **Step 6: Commit and push**

  After final bytes are green, create one local commit, run remote-aware preflight, confirm `origin/main` is still the exact base, push `codex/loss-guards-v1`, and prove local HEAD equals `refs/heads/codex/loss-guards-v1` on origin. Do not open a PR.

## Rollback

Before push, reverse only this plan's explicit patch with a reviewed inverse commit or stop using the isolated worktree; do not reset, abort, clean, prune or delete. After push, preserve the branch and use a new revert commit if the user authorizes rollback. No data migration or external deployment exists.

## Continuación

- Escribe en: este hilo.
- Rol: ejecutora.
- Para continuar: ejecutar un único `bash tests/run.sh` sobre estos bytes finales mediante una ejecutora desechable y, solo si queda verde, completar los post-gates, commit y push autorizados.
- Mensaje exacto: `Continúa loss-guards-v1 desde el siguiente checkbox no completado, con un solo writer y sin abrir PR.`
- Estado de partida: `AndreaBusta/codex-engineering-control-plane`, worktree `loss-guards-v1`, rama `codex/loss-guards-v1`, base `649508856626e03c998555324da780bf7ec4d49c`; Fase 1 GREEN 48/48, Fase 2 GREEN 20/20, lock 22/22, revisiones 0/0/0 y focal documental verde; sin commit ni remoto propio.
- No hacer todavía: commit o push antes del gate final; PR, merge, adopción, instalación, deploy, release o limpieza siguen fuera.
