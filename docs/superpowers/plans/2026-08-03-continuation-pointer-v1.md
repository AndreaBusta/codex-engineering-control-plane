# Continuation Pointer v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to execute this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every logical close tell the user exactly where and how to
continue without inventing a Codex task identity.

**Architecture:** Keep the feature native and declarative: `AGENTS.md` owns the
mandatory close rule, `templates/HANDOFF.md` owns the compact output shape, and
the reasoning guide owns deterministic parent/executor routing. Repository and
adoption contract tests prevent any of those parts from disappearing. No runtime
host adapter, dependency, hook, registry entry, CI change, or remote effect is
added.

**Tech Stack:** Markdown contracts and Python `unittest` repository tests.

---

### Task 1: Lock the continuation contract with a failing test

**Files:**

- Modify: `tests/test_adoption.py`
- Modify: `tests/test_repository_contract.py`

- [x] Add `test_logical_close_requires_a_verified_continuation_pointer`.
- [x] Require `AGENTS.md` to name `Continuation Pointer`, require the handoff
  template to expose `Escribe en`, `Rol`, `Para continuar`, `Mensaje exacto`,
  `Estado de partida` and `No hacer todavía`, and require the reasoning guide
  to distinguish the parent/orchestrator from child executors.
- [x] Run
  `python3 -m unittest tests.test_repository_contract.RepositoryContractTests.test_logical_close_requires_a_verified_continuation_pointer`
  and confirm it fails because the contract is absent.
- [x] Add an adoption-rendering regression after independent review exposed
  that adopted targets do not receive `templates/HANDOFF.md`, then confirm it
  fails because the managed `AGENTS.md` block is not self-contained.

### Task 2: Add the minimum native closure rule

**Files:**

- Modify: `AGENTS.md`
- Modify: `templates/HANDOFF.md`
- Modify: `docs/engineering/03-reasoning-context-agents.md`

- [x] Require the compact continuation block at every logical close or
  checkpoint.
- [x] Default the user's writing destination to the current parent/orchestrator
  task; treat a child task as an executor, not the default inbox.
- [x] Point to a different task only when its exact visible identity and full
  checkpoint delivery were verified independently from Git.
- [x] Use `este hilo` when the host does not expose a verified task ID; never
  invent an ID or confuse a branch/worktree with a Codex task.
- [x] Keep the user-facing pointer copy-pasteable and state blocked transitions.
- [x] Re-run the focused test and confirm it passes.
- [x] Make the adopted managed block self-contained and re-run its focal test.

### Task 3: Verify scope and repository gates

**Files:**

- Review all six changed paths from this plan.

- [x] Run `python3 -m unittest tests.test_repository_contract`.
- [x] Run `bash tests/run.sh`.
- [x] Run
  `scripts/control-plane policy-check --policy .codex/project-policy.toml`.
- [x] Run
  `scripts/control-plane registry-check --registry .codex/resource-registry.toml --policy .codex/project-policy.toml`.
- [x] Run `scripts/control-plane doctor`.
- [x] Run `git diff --check` and inspect `git diff --stat` plus `git diff`.
- [x] Obtain an independent read-only review against this plan and correct any
  in-scope finding.
- [x] Stop with the worktree uncommitted. Do not push, open a pull request,
  merge, deploy, release, or modify PR #8.

## Documentation and rollback assessment

This plan is the T2 written-plan artifact. No ADR, Issue, architecture document,
runbook, threat model, rollback plan, release note, or receipt is triggered:
the change applies an approved local documentation contract and remains
reversible with a normal unstaged edit reversal before any authorized commit.
