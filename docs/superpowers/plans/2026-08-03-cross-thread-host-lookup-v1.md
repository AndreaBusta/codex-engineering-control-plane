# Cross-thread host lookup v1

## Goal

Replace PR #8's unused 697-line shadow seam with the smallest real consumer:
when an exact `codex://threads/<UUID>` appears, the agent uses the Codex host's
native read-only thread lookup and returns a bounded, non-authorizing capsule.

## Architecture

This is a host behavior contract in `AGENTS.md`, not a Control Plane Python API.
The existing adoption renderer already copies the source rules into the managed
target block; source and adoption tests make that propagation durable. No
runtime module, registry route, lock update, hook, dependency or workflow is
added.

The capsule is at most 4 KiB and contains only:

- exact thread ID;
- visible state and observation time;
- project/worktree when visible;
- latest bounded checkpoint and Continuation Pointer, or `no observado`;
- `FOUND`, `STALE` or `UNKNOWN`;
- `authorizes=false`.

Raw transcript, prompts, reasoning, tool output and secrets are excluded. The
lookup never wakes, writes, steers, archives or modifies the target and never
satisfies review, authorization or lifecycle gates.

## TDD sequence

1. Add RED contracts for source and adopted `AGENTS.md`.
2. Require exact native lookup, the closed result set, 4 KiB budget, filtered
   fields, inert authority and read-only behavior.
3. Assert that neither `control_plane/cross_thread_audit.py` nor a cross-thread
   registry route exists.
4. Add the minimum source rule; rely on the existing adoption renderer.
5. Run focused, repository and full suites plus an independent review.

## Native host observations

These are observational capability checks, not persisted transcripts and not
runtime tests:

| Case | Exact ID | Visible host state | Result | Authority |
|---|---|---|---|---|
| active | `019fc3b8-e6f6-7823-a7d8-c39b8d5ac691` | `active`, current worktree visible | `FOUND` | false |
| completed | `019fbda1-b44d-7a13-b38e-28e27c2efbc5` | `notLoaded`, latest turn completed | `STALE` | false |
| absent | `01900000-0000-7000-8000-000000000000` | native not-found result | `UNKNOWN` | false |

The active task had no completed current-turn pointer to report. The completed
task predates Continuation Pointer v1, so its pointer is `no observado`. Neither
condition is upgraded to evidence or authority.

## Threat model

| Threat | Control | Residual limit |
|---|---|---|
| Target content injects instructions | treat all returned content as untrusted; copy no raw transcript | model must still extract the bounded fields |
| Lookup leaks prompts or secrets | omit previews, prompts, reasoning, outputs and raw messages | host may expose more internally than the capsule returns |
| Serialized data fabricates freshness | use native read only; closed `FOUND/STALE/UNKNOWN` mapping | host status semantics can evolve |
| Lookup grants authority | literal `authorizes=false`; cannot satisfy any gate | separate native authorization remains necessary |
| Lookup changes another task | one read-only call; forbid wake/write/steer/archive | compromised host remains out of scope |

## Rollback

Before integration, remove the source rule and its two contract tests. After
integration, revert through a normal PR. There is no stored lookup state,
installed adapter, registry entry, hook or migration to clean up.

## External boundaries

PR #8 remains untouched until this replacement is merged. Closing it as
superseded is a later GitHub transition. Release, deploy, credentials,
dependencies and CI/CD mutation remain out of scope.
