# ADR 0004: Skill-led local run loop

Status: accepted for implementation

## Context

The v2.1.1 product is a deterministic local-audit kernel. It already owns task
lifecycle, leases, routing, verification primitives, and host-bound Git effect
contracts, but it does not connect a normal Codex request to a review-ready
local result. A second agent runtime or scheduler would duplicate Codex and
weaken the existing authority boundary.

## Decision

Add a `control-plane-run` skill as the human-facing orchestrator and a small
stdlib-only runtime coordinator for deterministic state and evidence. The
coordinator reuses `TaskStore` and `TaskLease`, executes only closed verification
profiles, stores state below the worktree Git dir, and permits three executions
total: the initial attempt and two repairs.

Remote Git functions remain host-bound and fail closed. A serialized run plan,
receipt, review, summary, or outcome request never grants commit, push, PR,
merge, release, or credential access.

## Alternatives

- A Python agent loop was rejected because Codex already performs planning and
  implementation.
- A generic command manifest was deferred because it would become a second
  policy before dogfood proves the need for `ProjectFactsV1`.
- Immediate plugin packaging was deferred because packaging must not introduce
  behavior before the skill-only workflow is stable.

## Consequences

The first vertical slice is intentionally local and uses a closed verification
profile for this repository. Other repositories remain fail-closed until their
verification commands can be derived or explicitly governed. GitHub writes and
plugin distribution remain separate promotion gates.
