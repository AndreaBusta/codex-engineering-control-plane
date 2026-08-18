# Control Plane Core maintenance

This is the governing runbook for the local `3.1.0-core.2` candidate. It does
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

The mutex is the persistent `locks/verification.lock` inode. Fresh Adoption apply
is the only path that provisions and seals its `verification_lock` for a
journal-bound generation. Active Core, the full-gate runner, replay, verify and
rollback are `create=false` and reuse-only: they retain common/state/locks/file
descriptors through flock, revalidate every named identity, validate the same
closed active journal, and never unlink the mutex on release. Missing or
substituted state fails with `E_VERIFICATION_LOCK` or `E_TEST_MUTEX` instead of
recreating or repairing it.

A pre-existing Core-owned verification mutex is not evidence of an interrupted
Adoption apply. Fresh apply leaves it unchanged and fails eligibility. Only the
exact journal-less provisioning inventory created by that same interrupted
apply may be cleaned after the reviewed plan is revalidated. This behavior is
non-authorizing and records `authorizes=false`.

## Stable Pause v1

Stable Pause is a verify-only local checkpoint for one exact task ID:

```bash
scripts/control-plane task checkpoint \
  --mode stable-pause \
  --task-id EXACT-TASK-ID \
  --json
```

The output is one canonical `StablePauseObservationV1` line of at most
4096 bytes. `SAFE_PAUSE_ACTIVE` and `SAFE_PAUSE_TERMINAL` return exit 0;
`UNSAFE_PAUSE` returns exit 1; `UNKNOWN` returns exit 2. Every variant contains
`checkpoint_digest` and `authorizes=false`. A dirty worktree, failing RED, or
`git diff --check` failure stays visible as quality evidence but is not
automatically unsafe unless it also proves drift, lifecycle contradiction, or
an active operation.

The progressive `control-plane-run` reference performs the
native host before and after quiescence check around the single foreground observer. Native facts
may downgrade the effective result but never upgrades a Core `UNSAFE_PAUSE` or
`UNKNOWN`. Render the bounded continuation capsule only after that join. On
resume, rerun the command for the same task and worktree, compare
`checkpoint_digest`, explain any drift, and repeat normal route, preflight,
authority, and lifecycle gates before a write.

This procedure is zero mutation: no cleanup, no lifecycle transition, no Goal,
no test or gate, no Git transition, no remote effect, no consumer, and no
canary. It never creates or repairs a mutex or checkpoint artifact. A held or
missing required mutex, lifecycle/residue contradiction, bounds failure, or
unknown host visibility is a fail-closed result, not a reason to clean or infer
authority.

Repository evidence is tied to the exact selected repository root. The closed
Git context forces `core.filemode=true` and `/dev/null` external excludes,
rejects `assume-unchanged` and `skip-worktree`, and reads all required blobs
through a single `cat-file --batch`. Ignored caches stay outside the global
unsafe-type scan but their bounded path set remains digest-bound. Gitlinks,
`.git` markers, and bare markers are rejected because nested repositories are
unsupported. For a terminal task with a nonzero lease generation, safe output
also requires the exact release receipt. These checks are observational and
remain `authorizes=false`.

## Adoption rollback quarantine

Core creates or reuses `adoption.lock` and retains the lifecycle inode before the task lock
even when no journal or activation marker exists. Fresh Adoption
apply and rollback acquire that same inode exclusively, so an absence-to-write
race cannot form a second lifecycle domain. An exact raced `ROOT_EMPTY`
bootstrap removes only the lock created by the failed apply; it never removes a
Core writer's inode.

Journal-less recovery accepts only `ROOT_EMPTY`, `P1`, `P2`, `P2Q`, `P3`,
`P3Q`, `P4` and `P4T`. It uses nonblocking opens, complete descriptor/name
revalidation, and no-replace moves into durable quarantine before removing an
empty provisioning directory. Any other inventory or substitution is
`E_ADOPTION_RECOVERY_REQUIRED` and remains untouched.

Product rollback conditionally removes only the exact-value
`core.hooksPath=.codex/git-hooks` entry. It moves the activation and every
managed leaf into linked durable quarantine, revalidates them after the move
and again before the receipt, and does not unlink them during the certifying
rollback. A separate GC is outside this implementation and would need its own
contract and authority. This operational record remains `authorizes=false`.

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
`origin/main@b07418364409f76c900f0595a76c9e3e388ac433`. Do not delete or rewrite
legacy JSON, installed runtimes, external repositories, or Git history to make
the candidate appear clean.

If an existing installation needs rollback, use the owning stable runtime or a
future recovery runtime that implements a mechanically shared barrier. Core
3.1 only verifies the exact journal and then fails closed; it does not reverse
a commit, remote write, Pull Request, merge, deploy, or release.

## Local adoption enablement

The separate `scripts/control-plane-adoption` entrypoint is implemented for
closed local verification only. It is not part of `scripts/control-plane`, the
26-module Core allowlist or the compatibility parser surface.

```text
adoption_tool=IMPLEMENTED_LOCAL
temporary_repository_e2e=PASS
external_consumer_adoption=PROHIBITED
canary=NOT_PREPARED
stable_adoption=NOT_DECIDED
Autopilot OFF
authorizes=false
```

Only harness-owned temporary repositories may exercise `preview`, `apply`,
`status`, `verify` and `rollback` under this implementation decision. Do not
run the tool against a consumer or prepare a canary. The Core parsers `adopt
plan`, `adopt apply`, `upgrade plan` and `upgrade apply` remain zero-mutation
`E_CAPABILITY_QUARANTINED` stubs.

Local implementation rollback removes only the adoption package, entrypoint,
lock, tests and documentation enumerated by its plan, then proves the Core
runtime digest unchanged. Transaction rollback inside a harness-owned target
deactivates the target lock first and removes only exact journal-owned records;
any identity or digest drift is preserved and fails closed.

## External adoption

`external_consumer_adoption=PROHIBITED`. Do not install this runtime or source
plugin, replace an installed plugin, modify a consumer repository, or publish a
package until a future stable-adoption task receives separate exact authority
and independent evidence. Manual dogfood must pass first; even then the
scorecard is evidence, not permission.
