# Outcome bridge rollback

This runbook preserves evidence and fails closed. It never grants a new effect.
Start by recording repository, worktree, branch, HEAD, task state, pending
marker and the last exact receipt. If the remote result is uncertain, observe
before retry: zero second write until the first effect is known.

## Invariants

Never use `reset --hard`, force-push, automatic PR closure or automatic remote
rollback. Never delete a branch, worktree, marker or registry to make recovery
look clean. A changed subject starts a new reviewed revision and invalidates its
dependent gates.

## Before commit

If staging did not occur, retain the reviewed worktree and rerun only the failed
local gate. If staging occurred but commit did not, compare the index to the
durable staged-subject receipt. Restore only the exact identified index entries
after explicit approval; do not discard working-tree edits or unrelated work.

Verify that state remains `review_ready` or `blocked` with its recovery record.
No remote observation or repair is needed because no remote effect was armed.

## Local candidate publication

The only public locator is
`codex-control-plane/candidates/v2-3-local-candidate.json` under the worktree
Git dir. A new publication retains one reserved internal pending hardlink to
the same owner-safe `0600` inode, so the exact pair has `nlink=2`. Its suffix is
the receipt's complete 64 lowercase hex digest without `sha256:`; recovery
requires the name, canonical bytes and pending bytes to bind the same digest.
A canonical-only `nlink=1` receipt remains readable as legacy state.

Recovery never deletes a candidate pathname. It may link one exact orphan
pending to the absent canonical and retain the resulting pair. A partial
pre-link write, replacement, mismatched or malformed leaf, unexpected hardlink
count, symlink, foreign name or multiple pending entries is preserved and
remains `BLOCKED`; do not remove it to make recovery look complete.

## After local commit

Preserve the commit and branch. If the committed subject is wrong, create a new
reviewed correction and, when separately authorized, a new commit. Do not move
the branch backward or rewrite the commit. Revalidate tree, parent, policy,
scope and lifecycle lineage before any later push.

## After push or PR

Read the exact feature ref or Pull Request before acting. If the observed push
or PR matches its plan, publish that observation and continue the stable chain.
If it differs, block for drift and request one product-level decision. Do not
force-push, delete the remote branch, close the PR automatically or edit hosted
state as a rollback substitute.

## Uncertain remote write

A timeout, crash, lost response or `UNKNOWN` is not failure proof. Put the
durable marker in observe-only before the write, then query the exact target.
There is zero second write and zero repair while the first outcome is unknown.
Exact observed success may be published once; exact observed absence remains
`BLOCKED` until the host supplies a fresh observation or product decision.

## Post-merge verification

After an observed squash merge, refresh only the exact bound base ref through
the native host boundary and prove that it contains the observed merge SHA.
Only then advance `merged → base_verified → closed` for an integration outcome.
Missing ref, mismatch, non-containment or `UNKNOWN` stays `BLOCKED` with the
immutable refresh registry and marker retained.

Do not revert the remote merge automatically. A revert is a new remote effect
with a new reviewed subject, plan, checks, authorization and PR. Local guards
are not evidence that GitHub accepted or protected the result.

## Closure evidence

Record the retained state, exact receipts, observations made, destructive
actions avoided, remaining uncertainty and next product-level decision. Link
the [threat model](../security/2026-08-08-v2-3-outcome-bridge-threat-model.md)
and [ADR 0005](../adr/0005-host-bound-outcome-authorization.md).
