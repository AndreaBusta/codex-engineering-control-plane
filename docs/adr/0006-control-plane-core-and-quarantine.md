# ADR 0006: Control Plane Core and structural quarantine

Status: accepted for the local `3.1.0-core.1` prerelease candidate.

## Context

The Advanced line joined local task state, delivery, remote outcome handoffs,
candidate publication, recovery, and self-verification into one maintenance
surface. That surface became expensive to reason about and could make a local
artifact look more authoritative than it was. The last official release remains
`2.1.1`; later v2.3/v2.4 work and plugin `3.0.0` were candidates, not a new
official product release.

The repository still needs a small local control plane for policy, routing, Git
safety, task ownership, bounded verification, and recoverable checkpoints.

## Decision

Build `3.1.0-core.1` as an exact runtime allowlist with no import edge to the
quarantined implementation. Core accepts only `answer` and `local_change`:
facts-only answers do not create durable task state. Local changes store
`CoreTaskStateV1` below the worktree Git dir, while revision-scoped generational
leases and release receipts live below the Git common dir to coordinate Core
writers across worktrees. Every durable artifact remains `authorizes=false`.

Verification is serialized once per Git common dir. Maintenance has one lineage
and at most one structural reframe. Legacy state is bounded, read-only, and
non-resumable. Existing installed generations retain exact status, verification,
and rollback recovery, but Core cannot create a new adoption or upgrade.

The source plugin may be prepared for review, but
`external_consumer_adoption=PROHIBITED` until a later, separately authorized
stable-adoption decision.

## Alternatives

- Repair the whole Advanced runtime in place: rejected because it preserves the
  coupled authority and maintenance surface.
- Add a daemon, scheduler, authority store, or telemetry: rejected until manual
  dogfood demonstrates a concrete need.
- Delete every legacy record and installed generation: rejected because it
  destroys evidence and rollback capability.
- Treat local green tests as stable adoption: rejected because a candidate
  cannot certify itself.

## Consequences

Core is intentionally local-first and smaller. Remote writes, commit, Pull
Request, merge, deploy, release, installation, and upgrade remain outside its
active runtime. The compatibility parsers fail closed without importing or
mutating the quarantined implementation. Historical documents stay available as
audit history but are `HISTORICAL_NON_GOVERNING`.

The candidate may report `GREEN_LOCAL / PENDING_STABLE_ADOPTION` only after its
local gates pass. That state is non-authorizing and records
`self_certified=false`.

## Compatibility and rollback

The fail-closed parser window is the 3.1 line only, beginning at
`3.1.0-core.1`; the parsers are removed at the first 3.2 prerelease unless a new
accepted ADR supersedes this decision. They never regain Advanced behavior
inside that window.

Until stable adoption is separately authorized, rollback of this candidate is
to stop using its isolated worktree. The stable source remains
`origin/main@929d3f8a0656fed190bb65ceb3a29deef8de07d6`. Existing installations may
be inspected and verified from their exact recovery journal. Core rollback
stops with `E_ADOPT_QUIESCENCE_UNKNOWN` because v2.1 has no shared global writer
barrier; no caller assertion substitutes for that proof, and no rollback
rewrites legacy task state or undoes an external effect.
