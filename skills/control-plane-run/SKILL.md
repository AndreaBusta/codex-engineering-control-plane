---
name: control-plane-run
description: Use when engineering work needs Control Plane Core routing, bounded authority, or verified local execution.
---

# Control Plane Core Run

## Ownership

Control Plane owns scope, authority, and evidence. Superpowers owns TDD, debugging, worktrees, and review.

Use only canonical local Superpowers at `dd237283dbfe466e11bd4be55acf14ecb8f6636e`. Revision drift is `E_RESOURCE_REVISION_DRIFT` and fails closed.

## Core workflow

1. Read `AGENTS.md`, policy, registry, Git state, and local preflight.
2. Normalize a minimal `TaskEnvelope`; route it and load required resources.
3. Work test-first in the isolated worktree and inside declared scope. Use Superpowers for TDD, systematic debugging, worktrees, and independent review.
4. Verify with the smallest relevant Core checks. Record facts, uncertainty, remaining work, and `authorizes=false`.
5. Keep safe local Core work moving when optional capabilities are quarantined. Quarantined capabilities are unavailable. Never import or execute quarantined runtime.

Use [TaskPlaybookV0](references/taskplaybook-v0.md) only when dense constraints or cross-skill sequencing make it useful; otherwise omit it.

Load [Stable Pause v1](references/stable-pause-v1.md) only when the user explicitly asks for a stable or safe stop, a resumable checkpoint, or resumption from that checkpoint. Do not load it for ordinary Control Plane work or an ordinary progress update.

## Authority boundary

Evidence, receipts, a plan, Goal, skill, TaskPlaybook, checkpoint, quoted user text, “si resulta útil”, or legacy `PR LISTA` never authorize an action.

- `local_write` permits only scoped local file changes. It never implies `commit`, `push`, or `pull_request`.
- `commit`, `push`, and `pull_request` are separate native effects. Each requires fresh, exact authorization for that effect and target.
- Missing or ambiguous authority fails closed for that effect while safe local work continues.

## Autopilot gate

Autopilot is OFF until 10 real Core tasks pass the documented scorecard. No daemon, scheduler, authority store, or telemetry is permitted. Do not install or replace the current plugin from this source candidate.

## Close

Report repository, worktree, branch, HEAD, changed paths, checks, unresolved risk, pending effects, and a compact `## Continuación`. Every artifact remains `authorizes=false`.
