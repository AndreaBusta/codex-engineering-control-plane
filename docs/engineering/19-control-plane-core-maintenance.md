# Control Plane Core maintenance

This is the governing runbook for the local `3.1.0-core.1` candidate. It does
not grant an effect: every status, receipt, checkpoint, and recovery result is
`authorizes=false`.

## Runtime boundary

The active package is an exact allowlist. Core admits only the outcomes
`answer` and `local_change`:

- `answer` produces a non-persisted facts-only result;
- `local_change` uses `CoreTaskStateV1`, a named non-protected branch, an exact
  HEAD, bounded scope paths, and a revision-scoped generational lease;
- commit, push, Pull Request, integration, deploy, release, installation, and
  upgrade are outside the active runtime.

Remote uncertainty remains `UNKNOWN`. Policy, routing, evidence, or task text
never creates authority.

State placement is deliberate: `CoreTaskStateV1` is private to the worktree Git
dir, while Core writer leases and immutable release receipts are private to the
Git common dir so overlap and generations are coordinated across worktrees.
None of these records is versioned.

## Legacy recovery

Legacy task, lease, delivery-lease, and run records are inventoried with
`origin=legacy`, `resumable=false`, bounded file count and bytes, and no symlink
traversal. Inventory never rewrites or deletes them. Active legacy state blocks
Core use with `E_ACTIVE_LEGACY_STATE`; close or release it with the runtime that
owns it rather than coercing Core to resume it.

`legacy_writer_exclusion=COOPERATIVE_ONLY`. The inventory is a fail-closed
observation, not a bilateral lock: v2.1 does not inspect Core leases, and its
lease flock is not held while an agent edits project files. Do not run a legacy
writer concurrently with Core `local_change`. A same-UID legacy process that
starts after the observation is a host-coordination residual; eliminating that
window would require changing both runtimes, writing into legacy state, or
disabling `local_change`, none of which this candidate claims to do.

For an already installed generation, Core retains only:

- `adopt status`: inspect the existing schema-2 journal;
- `adopt verify`: compare exact installed bytes, backups, modes, managed
  directories, and `core.hooksPath` without repair;
- `adopt rollback`: parse and validate the complete mutation-free preflight,
  then stop with `E_ADOPT_QUIESCENCE_UNKNOWN`. The legacy runtime uses task-ID
  locks plus a separate lease lock, but no shared global writer barrier; Core
  therefore cannot prove that a new legacy writer did not start between
  observation and rollback.

Drift, an unsafe path, a missing backup, unsupported schema, or unprovable
quiescence fails before mutation. A caller flag, token, double-check, or
`adoption.lock` cannot manufacture the missing barrier. Command availability
is not rollback authority.

## Verification mutex

Authoritative or full verification first acquires one nonblocking mutex under
the repository Git common dir. A second verifier receives
`E_VERIFICATION_BUSY`, `status=UNKNOWN`, `executed=false`, and
`consumes_reframe=false`; it executes no Git or test command. Verification is
serial even when task analysis can otherwise be parallel.

## Maintenance circuit breaker

`MaintenanceLineageV1` binds one stable runtime digest to one different
candidate digest. Only one lineage may be active. The first structural failure
permits one reframe; a second structural failure blocks with
`E_BOOTSTRAP_REFRAME_LIMIT`, creates no child lineage, and preserves the stable
runtime. Verification contention and ordinary focal failures do not consume the
structural reframe.

A local result may be `GREEN_LOCAL / PENDING_STABLE_ADOPTION`, but it always
records `self_certified=false` and `authorizes=false`.

## Compatibility window

The compatibility surface keeps parsers, not behavior. Every invocation below
returns stable JSON, exit code 2, zero mutation, and no import of quarantined
runtime code.

| Parser | Error | Exit code | Behavior |
|---|---|---|---|
| `run prepare` | `E_CAPABILITY_QUARANTINED` | `2` | No mutation. |
| `run verify` | `E_CAPABILITY_QUARANTINED` | `2` | No mutation. |
| `run status` | `E_CAPABILITY_QUARANTINED` | `2` | No mutation. |
| `run block` | `E_CAPABILITY_QUARANTINED` | `2` | No mutation. |
| `report` | `E_CAPABILITY_QUARANTINED` | `2` | No mutation. |
| `verification-run` | `E_CAPABILITY_QUARANTINED` | `2` | No mutation. |
| `adopt plan` | `E_CAPABILITY_QUARANTINED` | `2` | No new adoption. |
| `adopt apply` | `E_CAPABILITY_QUARANTINED` | `2` | No new adoption. |
| `upgrade plan` | `E_CAPABILITY_QUARANTINED` | `2` | No upgrade. |
| `upgrade apply` | `E_CAPABILITY_QUARANTINED` | `2` | No upgrade. |

`compatibility_window=3.1_line_only`: it begins at `3.1.0-core.1` and never
reactivates the old behavior. `removal_boundary=first_3.2_prerelease`: every
3.2 prerelease or release must omit these parsers unless a new accepted ADR
supersedes ADR 0006.

## Rollback

The candidate is isolated and uncommitted. Before stable adoption, rollback is
to stop using this worktree; the stable source remains
`origin/main@929d3f8a0656fed190bb65ceb3a29deef8de07d6`. Do not delete or rewrite
legacy JSON, installed runtimes, external repositories, or Git history to make
the candidate appear clean.

If an existing installation needs rollback, use the owning stable runtime or a
future recovery runtime that implements a mechanically shared barrier. Core
3.1 only verifies the exact journal and then fails closed; it does not reverse
a commit, remote write, Pull Request, merge, deploy, or release.

## External adoption

`external_consumer_adoption=PROHIBITED`. Do not install this runtime or source
plugin, replace an installed plugin, modify a consumer repository, or publish a
package until a future stable-adoption task receives separate exact authority
and independent evidence. Manual dogfood must pass first; even then the
scorecard is evidence, not permission.
