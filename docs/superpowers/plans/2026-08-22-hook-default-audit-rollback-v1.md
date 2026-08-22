# Hook Default Audit Rollback v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the distributed Control Plane hook launcher to an audit-only default after the terminal PR #30 NO-GO, without extending or integrating the rejected shell-command grammar.

**Architecture:** Change only mode selection and the contracts that bind it. Missing or invalid mode becomes `audit`; explicit `soft-enforce` and `enforce` retain their current behavior, but neither is promoted or distributed as the default. Keep every destructive pattern and `_untrusted_pretool_reason` branch byte-identical to the base.

**Tech Stack:** Python 3.11 standard library, `unittest`, JSON/TOML contracts, repository lock digests, POSIX shell full gate.

---

## Contract

- Base: `origin/main@34082998c731b71ba0a8f3a8ba39144aa2e539b4`.
- Worktree: `/Users/bustaseo/Developer/control-plane-worktrees/hook-default-audit-rollback-v1`.
- Branch: `codex/hook-default-audit-rollback-v1`.
- Product decision: distributed hooks are audit-only by default. Semantic enforcement is not supported until a later product decision supplies a structured host command contract.
- No shell tokenization, destructive-pattern, `_untrusted_pretool_reason`, Git guard, CLI, dependency, schema, CI, installation, consumer, deploy, release, branch deletion, prune, GC, reset or force-push change.
- TDD is mandatory. The default/invalid-mode assertions must fail before production or lock bytes change.
- One writer. One final commit after focused tests, reviews and the full gate pass on the final bytes.
- The full gate budget is six; the last consumed run must be green.

## Acceptance

1. With `CODEX_CONTROL_PLANE_HOOK_MODE` absent, destructive commands and ordinary untrusted operations return advisory context without `permissionDecision`.
2. Invalid mode values fall back to the same audit behavior.
3. Exact explicit `soft-enforce` still denies the already-recognized destructive branch commands; exact explicit `enforce` keeps its existing fail-closed contract.
4. Exact safe reads remain silent. MCP remains advisory.
5. `.codex/hooks.json`, `.codex/control-plane.lock`, the lock validator and governing documentation all declare `audit`/`audit-only` and `pending_hook_trust`.
6. The Core inventory remains exactly 27 modules, digests and threat-model footer match final bytes, and `bash tests/run.sh` passes.
7. PR #30 remains closed and neither its remote head nor the preserved parser WIP is merged.

### Task 1: RED for audit-only default

**Files:**

- Modify: `tests/test_core_hooks.py`
- Modify: `tests/test_core_lockfile.py`
- Modify: `tests/test_core_quarantine.py`
- Modify: `tests/test_hooks.py`

- [x] **Step 1: Replace the default-denial assertions with the approved contract**

Use real `run_hook` calls. For absent and invalid mode, assert:

```python
specific = json.loads(run_hook(payload, expected_root=repo))["hookSpecificOutput"]
self.assertIn("CONTROL PLANE RISK", specific["additionalContext"])
self.assertNotIn("permissionDecision", specific)
```

Add an explicit `soft-enforce` case for the four already-supported branch-deletion forms and keep the existing explicit `enforce`, exact safe-read and MCP controls unchanged.

Update lock/config assertions to require:

```python
self.assertEqual(lock["hook_mode"], "audit")
self.assertIn("audit-only", description)
self.assertNotIn("soft-enforce-by-default", description)
```

- [x] **Step 2: Verify the intended RED**

Run:

```bash
/usr/local/bin/python3 -I -S -B -X pycache_prefix=/dev/null -c 'import sys, unittest; sys.path.insert(0, "."); names = ["tests.test_core_hooks", "tests.test_core_lockfile", "tests.test_core_quarantine"]; result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)); raise SystemExit(not result.wasSuccessful())'
```

Expected: failures only because runtime, lock validator, lock metadata and hook description still select `soft-enforce`.

### Task 2: Minimal mode rollback

**Files:**

- Modify: `control_plane/hooks.py`
- Modify: `control_plane/lockfile.py`
- Modify: `.codex/hooks.json`
- Modify: `.codex/control-plane.lock`

- [x] **Step 1: Change only mode defaults**

Implement this exact selection:

```python
def _hook_mode() -> str:
    mode = os.environ.get("CODEX_CONTROL_PLANE_HOOK_MODE", "audit")
    return mode if mode in {"audit", "soft-enforce", "enforce"} else "audit"
```

Require `hook_mode == "audit"` in `validate_lock()`. Change the hook description to `Control Plane Core 3.1 audit-only hooks; authorizes=false and trust remains pending separate adoption.` Set the lock field to `audit`.

- [x] **Step 2: Verify GREEN before resealing**

Re-run the Task 1 command. Expected: behavioral and metadata assertions pass; digest-oracle failures are allowed only until the next step.

- [x] **Step 3: Recompute only coupled lock digests**

Update `digests.hooks` from `.codex/hooks.json` and `digests.runtime` from `control_plane.lockfile.runtime_digest(Path("."))`. Do not modify the 27-entry module list or other digest fields.

- [x] **Step 4: Verify focused GREEN**

Run the Task 1 matrix again and the individually loadable modified legacy hook methods. Expected: all selected tests pass; pre-existing `control_plane.host_bridge` import debt in the undiscovered legacy module does not expand this task.

### Task 3: Governing documentation and security boundary

**Files:**

- Modify: `SECURITY.md`
- Modify: `docs/engineering/09-audit-dafo-and-risk-register.md`
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Modify: `docs/superpowers/plans/2026-08-22-hook-default-audit-rollback-v1.md`

- [x] **Step 1: Record the product decision**

State that the distributed launcher is audit-only, advisory, cooperative and `pending_hook_trust`. Explicit `soft-enforce` is an unpromoted direct-API mode, not a supported distributed default. A future promotion requires structured host command evidence and a new product decision.

- [x] **Step 2: Record the residual**

Audit prevents false blocking but cannot stop destructive commands. Branch protection, the pre-push unpublished-work guard, remote observation and exact human authority remain the active controls. Do not claim shell coverage.

- [x] **Step 3: Rebind the threat-model footer last**

After every other tracked byte is final, run:

```bash
python3 -c "import sys;sys.path.insert(0,'.');from tests.test_core_documentation import normalized_snapshot_version as v;print('Version:',v())"
```

Write that exact version into the footer, then run its snapshot-bound test.

### Task 4: Final verification, review and integration

- [x] **Step 1: Prove scope and no grammar change**

Compare `control_plane/hooks.py` with the base and require all hunks to be confined to `_hook_mode`. Run `git diff --check` and confirm every changed path is listed by this plan.

- [ ] **Step 2: Independent reviews**

Obtain a spec-compliance review and a security/code-quality review on the same frozen bytes. Both must report 0 Critical and 0 Important.

- [ ] **Step 3: Full gate and post-gates**

Delegate one terminal `bash tests/run.sh` execution to a disposable worker. If green, run policy-check, registry-check, doctor, `git diff --check` and final status.

- [ ] **Step 4: Commit, push, PR and squash merge**

Commit the exact reviewed paths, push normally, open a PR against the exact current `origin/main`, require `core-verify=SUCCESS` on its exact head, and squash merge with `--match-head-commit`. Never delete a branch.

- [ ] **Step 5: Verify integration and freeze**

Prove content containment, require `core-verify=SUCCESS` on the new `main`, preserve branches/worktrees, update the durable checkpoint and stop without opening another front.

## Rollback

Before merge, stop using this isolated worktree or add a reviewed inverse commit; never reset or discard. After merge, rollback is a new squash-revert PR restoring the previous two `_hook_mode` fallbacks plus the coupled lock/config/docs, only after a new product decision. There is no data migration, installation or deployment to reverse.

## Continuación

- Escribe en: este hilo.
- Rol: ejecutora.
- Para continuar: revisar de forma independiente los bytes locales congelados antes de decidir el full gate o cualquier transición Git.
- Mensaje exacto: `Revisa el candidato audit-only congelado; no ejecutes todavía commit, push, PR, merge ni el full gate.`
- Estado de partida: `codex/hook-default-audit-rollback-v1@34082998c731b71ba0a8f3a8ba39144aa2e539b4`, sin commit nuevo; RED observado y GREEN focal en 47 Core + 6 legacy seleccionados; PR #30 sigue fuera de esta implementación.
- No hacer todavía: full gate, commit, push, PR, merge, instalación, deploy o release.
