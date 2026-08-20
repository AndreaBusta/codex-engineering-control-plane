# Control Plane Core 3.1 adoption enablement design

Status: accepted for local implementation.

Preparation state: `IMPLEMENTED_LOCAL / CANARY_PROHIBITED`.

Authority: `authorizes=false`.

## Decision

Design a dedicated, locally executed adoption tool outside the active
`control_plane` runtime. Its first supported target class is a new, disposable,
single-worktree canary repository with no installed Control Plane, no legacy
state and no pre-existing managed leaves. Safe consumer-owned parent
directories are allowed only when their identity and mode are snapshot-bound.

Do not reactivate the Core compatibility commands `adopt plan`, `adopt apply`,
`upgrade plan` or `upgrade apply`. They continue to return
`E_CAPABILITY_QUARANTINED`, exit code `2` and zero mutation throughout the 3.1
compatibility window.

The design and its local implementation do not authorize a canary, installation,
consumer mutation, package, plugin installation, tag, release, remote write or
Autopilot. `external_consumer_adoption=PROHIBITED` remains governing after the
tool is implemented. Before any canary can be requested, a separately reviewed
and accepted ADR must supersede that prohibition only for one bounded
fresh-consumer experiment. The exact canary still requires a later native
authorization. Those are separate decisions.

## Objective and success

The objective is to close the installation and rollback evidence gap without
weakening the small Core runtime or pretending that existing v2.1 consumers can
be migrated safely.

The local implementation satisfies this design when it can:

1. inspect an immutable source subject and an eligible fresh target without
   mutation;
2. emit a closed, bounded and non-authorizing preview;
3. install only an exact managed manifest while an inactive marker keeps
   partial bytes inert;
4. activate one generation through a single atomic pointer;
5. verify the exact installed generation and execute a bounded canary task;
6. deactivate first, restore the pre-install state exactly and prove the target
   matches its before snapshot; and
7. fail closed on drift, ambiguity, unsafe paths, active state, interruption or
   unproven eligibility.

Success in a disposable canary remains evidence for a later stable-adoption
decision. It is not stable adoption itself.

## Scope

### Included

- A separate source-side tool, provisionally named
  `scripts/control-plane-adoption`.
- A separately verified `adoption_enablement` package that is not part of the
  25-module Core runtime allowlist.
- Commands `preview`, `apply`, `status`, `verify` and `rollback` with closed JSON
  contracts.
- A source manifest, target snapshot, durable transaction journal, activation
  pointer and safe receipt projections.
- A disposable fresh-consumer canary protocol.
- Exact rollback of files, modes, managed directories and local
  `core.hooksPath` configuration created by the adoption.
- Tests for zero mutation, crash recovery, hostile paths, binding drift and
  before/after equivalence.

### Excluded

- Any existing consumer repository, including active product worktrees.
- v2.1, v2.3, v2.4 or plugin 3.0 migration and upgrade.
- Any target with legacy task, lease, run, adoption or remote-unknown state.
- Any target with an installed Core generation or pre-existing managed leaf.
- Multiple worktrees, submodules, bare repositories, protected-base writes or
  an existing `core.hooksPath` value in the first phase.
- Remote discovery, download, package resolution, plugin installation, tag,
  GitHub Release, commit, push, Pull Request, merge, deploy or publication.
- Enabling Autopilot or serializing human authority.
- Editing consumer `AGENTS.md`, project policy or resource registry. The target
  must already contain reviewed, committed and valid project-specific policy
  and registry files.

## Alternatives

### A. Fresh-consumer tool outside Core — selected

Keep installation mechanics in a separate exact allowlist and preserve the
active Core runtime unchanged. Reject every existing installation and prove the
transaction first in a disposable repository.

This adds one narrow tool, but keeps installation authority and failure modes
out of normal Core commands. It is the smallest path that can produce real
rollback evidence without claiming a bilateral legacy writer barrier.

A later, separately authorized bootstrap repair changes only
`control_plane/task_state.py` so terminal local `UNKNOWN` run outcomes do not
masquerade as remote uncertainty. That repair is not part of the adoption
runtime and does not create an import edge, but it does invalidate the original
Core runtime digest. The later authorized bump is bound to `3.1.0-core.2`;
evidence tied to `3.1.0-core.1` remains historical and cannot close AE-09 for
the new bytes.

### B. Reactivate `adopt apply` inside Core — rejected

This would make the compatibility parser operational again, expand the active
runtime boundary and contradict ADR 0006. It would also make ordinary Core
availability look like installation approval.

### C. Publish a package or plugin before installation proof — rejected

Distribution would create authority-looking surface and more consumers before
the transaction and rollback are demonstrated. A package digest does not prove
target eligibility, correct hooks configuration or exact recovery.

### D. Migrate existing v2.1 consumers first — rejected

`legacy_writer_exclusion=COOPERATIVE_ONLY`. v2.1 and Core do not hold one shared
writer barrier for the lifetime of an edit. A second observation, marker or
caller flag cannot close that race.

## First principles

1. Source quality, installation safety and stable adoption are different
   claims.
2. An installer must know both the exact bytes it will add and the exact state
   it must restore before the first mutation.
3. Partial bytes must remain inactive. Activation and deactivation require one
   atomic local pointer, not a best-effort sequence of operational switches.
4. A fresh target is an observed closed state, not a caller assertion.
5. Every plan, journal, pointer, receipt and test remains
   `authorizes=false`.
6. Absence, drift, concurrency or parsing uncertainty is `UNKNOWN` or failure,
   never permission.
7. The first real target is disposable. Product repositories remain excluded
   until a later migration design proves a stronger boundary.

## Architecture

### Separation from Core

The adoption entrypoint uses its own stdlib-only bootstrap, exact module
allowlist and lock. It does not import `control_plane` before validating the
source manifest, and it never imports quarantined Advanced modules. Its package
is excluded from the Core runtime lock and cannot be reached through
`scripts/control-plane`.

The source repository owns the adoption tool. The tool is never copied into the
consumer. The consumer receives only the managed Core projection declared by
the plan.

### Source subject

`preview` accepts a local source checkout only. It requires:

- an exact committed HEAD and clean tracked/non-ignored worktree;
- a supported Core product version and valid source lock;
- the exact 25-module runtime set and verified launcher, hook and Git-hook
  entrypoints;
- bounded, regular, owner-controlled, non-symlinked and materialized source
  files; and
- no network or ambient Git/Python configuration.

The source manifest binds relative path, raw SHA-256, Git executable mode and
role for every managed file. It also binds the source commit, source tree,
product version, source runtime digest and manifest digest.

Target policy and registry authority is evaluated only by
`scripts/control-plane` from the selected source, never by the checkout hosting
the adoption tool. The same selected source manifest is rebuilt immediately
before journal creation and compared in full, including HEAD, tree and manifest
digest; an empty source commit with identical managed bytes is still
`E_ADOPTION_SOURCE_DRIFT`.

### Fresh target eligibility

The target must be a local non-bare Git repository with one worktree, an exact
committed HEAD, a clean attached non-protected branch and no submodule or nested
repository in managed scope. Closed Git observation must prove all of the
following:

- valid committed `.codex/project-policy.toml` and
  `.codex/resource-registry.toml` for that target;
- no `.codex/control-plane.lock`, `.codex/hooks.json`, managed Core hook,
  managed Git hooks, `scripts/control-plane` or `control_plane` runtime path;
- every managed parent is either absent or a safe pre-existing directory whose
  device/inode and mode are bound by the before snapshot; pre-existing parents
  remain consumer-owned and must survive apply plus rollback unchanged;
- `managed_parent_directories` binds the canonical five-parent surface, while
  `managed_repository_scan=managed-repositories-v1` proves no `.git` marker,
  nested bare repository or Gitlink exists below the bounded `.codex`,
  `control_plane` and `scripts` roots;
- no Core or legacy task, lease, receipt, run, maintenance, adoption or
  remote-unknown state in known bounded roots;
- no `core.hooksPath` value;
- no second worktree; and
- no symlink, hardlink, dataless placeholder, unsafe ownership, unsafe mode,
  path traversal or filesystem identity drift in a target ancestor.

If any fact is missing or cannot be observed within its count, byte or time
budget, eligibility is `UNKNOWN` and the plan is not applicable.

### Managed projection

The initial managed projection is exact and intentionally small:

- `.codex/control-plane.lock` generated for the target policy and registry;
- `.codex/hooks.json`;
- `.codex/hooks/control_plane_hook.py`;
- `.codex/git-hooks/pre-commit` and `pre-push`;
- `scripts/control-plane`; and
- the exact 25 files in `control_plane/`.

Policy, registry and `AGENTS.md` remain consumer-owned inputs. The installer
does not merge Markdown, infer policy or overwrite an existing file.

All projected files except `.codex/control-plane.lock` are staged in private
directories and validated before publication. Files may appear on disk before
activation, but the existing launcher and hooks fail closed while the target
lock is absent. The generated target lock is published last by atomic rename;
it is the single activation pointer and binds the published manifest, target
policy and target registry. It also declares
`adoption_lifecycle=journal-bound-v1`, which makes the Git-common lifecycle
mutex mandatory for installed Core writers.

Only managed parents observed absent are prepared under private adoption state,
identity-bound durably, then moved with an atomic no-replace rename into the
target. A safe pre-existing parent is reused without being claimed by the
journal. All managed-file publication and activation use the same no-replace
property, so a concurrent destination is preserved and produces target drift
rather than being overwritten.

### State placement

Adoption state lives below the Git common dir:

```text
codex-control-plane-core/
  adoption.lock
  locks/
    verification.lock
  adoption/
    journal.json
    evidence/
      <install-digest>.json
```

The lock and directory chain are descriptor-relative, no-follow, private and
owner-bound. The plan seals the `journal-bound-v1` creation policy without
claiming a not-yet-created inode. Before any target write, the journal records
the exact `lifecycle_lock` device, inode, mode, owner, link count, size,
timestamps and flags observed after exclusive acquisition. Status, verify,
replay and rollback reopen that same named inode with `create=false`; a missing
or substituted mutex fails closed. The journal is durable and bounded.
Adoption state does not add a second runtime pointer: the atomically published
target `.codex/control-plane.lock` is authoritative and contains no authority.

Fresh apply is the only path allowed to create a journal-bound verification
mutex. It uses `O_CREAT|O_EXCL`; a failed exclusive create is never followed by
opening a competing inode. Journal-less provisioning has one closed prefix
language: `ROOT_EMPTY`; `P1` with only `adoption.lock`; `P2` with the empty
`adoption/` directory; `P3` with the empty `locks/` directory; `P4` with the
empty `verification.lock`; and `P4T` with its one bounded journal temporary.
The empty private `P2Q` and `P3Q` directories are durable quarantine
intermediates used while recovering `P2` and `P3`; they are never normal Core
state. Any different inventory is blocked.

Every Core task or lease writer creates or reuses and shares the lifecycle
inode before the task lock, even while the target still has no Adoption marker
or journal. Fresh apply takes that same inode exclusively, validates the reviewed plan before cleanup,
and removes only an inode it created itself
when the exact `ROOT_EMPTY` bootstrap races with Core. Recovery retains the
common/state/locks/file descriptor chain and uses no-replace quarantine before
removing an empty provisioning directory. A pre-existing Core-owned verification mutex
without exact Adoption provenance is not a provisioning prefix and remains
untouched. Every active replay, verify, Core verifier,
full-gate runner and rollback path is `create=false` and reuse-only; an
unexpected bootstrap entry is recovery required, never permission to recreate
state.

### Command contracts

`preview` is read-only. It emits `CoreAdoptionPlanV1` and a mutation proof. A
plan is valid only for the exact source and target snapshots it contains.

`apply` requires the exact plan file and the expected plan digest as separate
arguments. That digest is mistake prevention, not authority. Apply acquires the
adoption lock, repeats every source and target observation, compares the full
selected-source manifest, `managed_parent_directories` and
`managed_repository_scan`, writes the initial journal, stages and publishes the
managed projection, and performs every bounded leaf read with a nonblocking
open followed by descriptor and named-identity revalidation. It verifies the
projection while inactive, sets the previously absent local
`core.hooksPath` while the hooks still fail closed, publishes
`.codex/control-plane.lock` atomically as the last activation step, verifies
the active generation and emits a safe receipt.

`status` emits only a bounded projection: state, product version, install
digest, verification status, error codes and `authorizes=false`. It omits raw
paths, file contents, prompts, credentials and journal backups.

A terminal rollback receipt records that the operation proved its restored
surface at that moment; it is not current attestation. Later `status` reports
`ROLLED_BACK` with `verification=UNKNOWN` until a new bounded observation is
performed, and never upgrades stored evidence into present-tense proof.

`verify` revalidates the target lock, journal, source/installed manifest, every
managed byte and mode, policy/registry digests, hooks configuration and absence
of unexpected managed entries. It never repairs.

The source and installed locks use one closed canonical contract: the complete
top-level key set, all seven schema selectors, audit-only hook mode, pending
hook trust, exact 27-module order, exact digest-key set and digest shapes. A
source lock with an omitted or extra field, including a non-audit `hook_mode`,
fails before projection; verify cannot emit PASS for a lock Core would reject.

The adoption lock is bilateral from the first Core write, not only after an
installation: every Core task/lease mutation creates or reuses the lifecycle
inode before the task lock, holds it shared, and only then takes `leases.lock`.
`rollback` holds the journal-bound inode exclusive through terminal receipt
durability. After acquiring the shared lock for an installed target, Core
requires the activation marker, journal state `active` and exact
`lifecycle_lock` identity; missing, replaced, prepared or rolling-back state
blocks mutation. Inside that barrier rollback validates the complete journal
and rejects active Core task, lease or verification state. A closed task
revision waiting on the barrier must revalidate the activation/runtime binding
after acquisition and therefore cannot mutate after deactivation.

Rollback atomically moves the target lock and each managed leaf by no-replace
rename into its private journal-owned durable quarantine before deciding that
the name is safe to remove. It reopens and revalidates the moved inode, restores
only the exact-value `core.hooksPath=.codex/git-hooks` entry, and never deletes
a concurrent consumer value. The certifying rollback retains the activation
and managed inodes linked in the private `.recovery-*` and `.staging-*`
quarantines, rechecks them before the receipt, fsyncs affected directories and
proves the target equals the before snapshot. Reclamation is a separate GC
operation outside this implementation; rollback does not unlink those retained
inodes.

Local Git configuration is also a path-bound mutation. Preview requires the
exact `.git/config` leaf to be an owned, single-link, bounded regular file.
Apply and rollback repeat that check before any lifecycle mutation, prepare the
new config with fixed Git against a private random off-path regular file, then
atomically exchange names. The displaced inode and original bytes are checked
through the retained descriptor before it is removed; symlink, substitution or
exchange drift preserves the original config and fails closed. Rollback also
reasserts the single-worktree topology before journal transition, so a linked
worktree added after apply blocks deactivation.

Verification exclusion uses one persistent owner-bound
`locks/verification.lock` inode provisioned during apply. Core verification and
the full-gate runner, active apply replay, verify and adoption rollback reuse
that inode and never unlink the mutex on release. The journal seals a
`verification_lock` binding: stable device/inode, mode, owner and flags for the
`locks` directory plus full device/inode, mode, owner, link count, size,
timestamps and flags for the file. Each consumer retains the Git-common,
state, `locks` and file descriptors through nonblocking flock, then compares
both named directories and the named file with those open descriptors and the
sealed binding. A missing, substituted, symlinked or second domain fails before
test execution, quiescence checks or deactivation.

Core uses one dependency-free validator for the complete closed active journal
before verification and before every task or lease mutation. It rejects
duplicate or non-finite JSON, unknown fields, invalid bounds, nested authority,
noncanonical paths and any malformed source, target, parent, repository-scan,
lifecycle-lock, verification-lock, publication or rollback record. The
full-gate runner reads only the minimum mutex envelope before locking; after it
has captured and loaded the verified Core source under that mutex, it invokes
the same closed active journal validator before toolchain discovery or test
execution. Each consumer rereads and compares the full journal while retaining
common/state/locks/file descriptors through flock.

The comparable before/after surface covers the exact repository identity,
branch, HEAD, policy and registry digests, working-tree managed bytes and modes,
managed-directory absence or pre-existing device/inode and mode, cleanliness,
and local hooks configuration. Private Git-common adoption evidence and the
persistent verification mutex, durable quarantine and retained exact managed
records are outside that surface; otherwise retaining the safe immutable
receipt and rollback proof would contradict before/after equality.

### Closed schemas

`CoreAdoptionPlanV1` binds:

- schema, kind, source identity and manifest;
- target repo/common/worktree identities, branch and HEAD;
- target policy and registry digests;
- `adoption_lifecycle=journal-bound-v1`, `managed_parent_directories` and the
  closed `managed_repository_scan=managed-repositories-v1` assertion;
- exact managed paths and pre-install absence records;
- exact prior local Git configuration;
- before snapshot digest and bounded eligibility facts;
- plan digest, result and `authorizes=false`.

`CoreAdoptionJournalV1` additionally binds transaction state, install digest,
the full `lifecycle_lock` identity, the stable-directory/full-file
`verification_lock` identity, pre-existing managed parents, repository scan
contract, created directories, published records, the target lock record and
rollback records. Unknown keys, duplicate JSON keys, invalid types, excessive
nesting or unsupported schema fail closed in Adoption, Core and the full-gate
runner.

`CoreAdoptionReceiptV1` is an immutable safe projection of one completed
operation. It binds plan, install, before and after digests, result
`PASS|FAIL|UNKNOWN`, error codes and `authorizes=false`. It never becomes an
authorization token.

## Transaction and recovery

The journal state machine is:

```text
prepared -> staged -> published_inactive -> active
                                   \-> rolling_back -> rolled_back
active -> rolling_back -> rolled_back
```

An unexpected or impossible transition is `E_ADOPTION_JOURNAL`. Replaying an
operation with the same exact binding is idempotent; a different binding is
`E_ADOPTION_REPLAY` and mutates nothing.

Crash recovery is conservative:

- only exact `ROOT_EMPTY`, `P1`, `P2`, `P2Q`, `P3`, `P3Q`, `P4` and `P4T`
  journal-less prefixes are accepted; cleanup uses nonblocking opens, full
  descriptor revalidation and no-replace durable quarantine while the lifecycle
  mutex is held; any extra entry blocks;
- before `active`, verify staged/published records and continue rollback;
- after `active`, never guess whether the canary task ran; status becomes
  `UNKNOWN` until an operator separately authorizes verify or rollback;
- during rollback, repeat only exact journaled compensations whose current
  identity still matches the installed record; exact-value Git configuration
  restoration and linked quarantine prevent deleting a substituted or still
  open inode; and
- on any drift, preserve evidence and stop before overwriting or deleting the
  differing path.

No recovery path follows a symlink, scans an unbounded tree, executes a target
file or rewrites legacy state.

## Canary protocol

Implementation verification uses temporary repositories created and owned
by the test harness. That is not a consumer adoption.

After the harness has observed its synthetic task as closed, released and free
of active verifier state, fixture teardown removes only the exact terminal task,
release receipt and empty state directories that the harness itself created.
This happens before adoption rollback so the final evidence inventory is exact.
It is not product behavior: adoption rollback never purges Core-owned task
evidence in a real target.

No canary is permitted under ADR 0006. After implementation and verification,
an independently reviewed ADR must be accepted that creates an explicit,
single-subject exception to `external_consumer_adoption=PROHIBITED` without
declaring stable adoption. Only then may a canary action be prepared.

That first canary must be a newly created, disposable local repository dedicated
to this purpose. Immediately before execution, a separate action card must bind
its canonical identity, branch, HEAD, plan digest, source commit, source
manifest, allowed effects, rollback and limits. The user must then authorize
that exact canary in the native task; neither the ADR, plan nor card authorizes
execution and each remains `authorizes=false`.

The canary sequence is exactly:

1. capture the bounded before snapshot;
2. run `preview` and independently review the plan;
3. after separate exact authorization, run `apply` once;
4. run `verify`, policy-check, registry-check, doctor and hook smoke;
5. execute one synthetic scoped `local_change` using a real task, exact session,
   generation lease, close and release receipt;
6. prove zero active task, lease or verifier state;
7. run `rollback` once;
8. prove the repository bytes, modes, Git configuration and managed-directory
   topology equal the before snapshot; and
9. retain only the safe receipt outside the target.

The canary performs no commit, remote write, PR, merge, deploy, publication or
release. Any `FAIL`, `UNKNOWN`, drift or contention stops the sequence.

## Failure modes and red team

| Failure | Causal chain | Early signal | Required response |
|---|---|---|---|
| Partial install becomes executable | files are copied before validation | target lock absent or manifest mismatch | launcher fails closed; rollback exact records |
| Wrong repository is targeted | ambient Git or path substitution | repo/common/worktree binding differs | `E_ADOPTION_TARGET_DRIFT`, zero mutation |
| Existing consumer is mislabelled fresh | caller assertion replaces inventory | managed leaf or legacy/Core record exists | `E_ADOPTION_NOT_FRESH`, zero mutation |
| Concurrent writer starts | state changes after preview | lock/state/snapshot drift at apply | invalidate plan; do not retry blindly |
| Selected-source authority is substituted | host checkout validates a different parser or source HEAD advances with identical bytes | selected launcher or full source manifest differs | `E_ADOPTION_SOURCE_DRIFT`, zero target mutation |
| Nested repository is smuggled through managed scope | `.git`, bare markers or Gitlink survive freshness checks, including case variants on case-insensitive filesystems | case-folded `managed-repositories-v1` scan fails | reject before journal or stop rollback before deactivation |
| Source or installed lock is only partially validated | Adoption emits PASS for a lock Core rejects | closed canonical field, schema, hook, module and digest-key contract differs | `E_ADOPTION_SOURCE_LOCK` or `E_ADOPTION_VERIFY_DRIFT`; zero activation |
| `.git/config` redirects through a symlink or is replaced | fixed hooks mutation writes outside the selected repository | no-follow regular-leaf binding plus off-path preparation and atomic exchange cannot prove the displaced inode | preserve the original config and fail `E_ADOPTION_GIT_CONFIG` before activation/deactivation |
| A linked worktree appears after apply | rollback misses task state in another worktree Git dir | fresh NUL-delimited single-worktree inventory differs | `E_ADOPTION_TARGET_WORKTREES`; keep journal active and activation intact |
| Lifecycle mutex path is replaced | Core and rollback lock different inodes | `lifecycle_lock` identity differs | fail closed without mutation; never recreate recovery mutex |
| Verification mutex name, parent or journal binding is replaced | Core, runner or rollback form different inode domains | closed `verification_lock`, descriptor-relative no-follow reads and post-flock directory/file identity differ | reuse-only `E_ADOPTION_VERIFICATION`/`E_VERIFICATION_LOCK`/`E_TEST_MUTEX`; stop before execution or rollback mutation |
| Lifecycle state appears while a first task waits for its task lock | Core writes outside Adoption exclusion | Core holds the lifecycle inode before the task lock even from an absent state | apply remains excluded or fails without orphaning its lock |
| A provisioning directory name is substituted during cleanup | recovery removes a concurrent directory | `P2Q`/`P3Q` no-replace quarantine plus descriptor identity | preserve the differing name and fail recovery |
| A regular leaf becomes a FIFO after observation | a mutex-holding operation blocks indefinitely | nonblocking open plus complete post-open `fstat` and named identity | reject without reading or unlinking the FIFO |
| Crash after activation | caller does not know whether task ran | journal active without terminal receipt | `UNKNOWN`; observe before rollback |
| Rollback deletes user data or an open managed inode | created-path assumption is stale or unlink hides later writes | no-replace durable quarantine, current digest/mode/identity checks and final revalidation | retain linked evidence, stop and preserve drift |
| `core.hooksPath` changes during restoration | unconditional unset removes a consumer value | exact-value conditional unset of only `.codex/git-hooks` | preserve the concurrent value and fail drift |
| Plan is treated as permission | serialized confirmation is replayed | no fresh native authorization | host must refuse execution |
| Canary is promoted directly to fleet | technical rehearsal is called stable | no later adoption decision | keep external adoption prohibited |

Red-team conclusions:

- A marker saying “fresh” is not evidence; the tool must derive freshness from
  the exact closed inventory.
- Rechecking legacy state does not solve the existing-consumer race; exclusion
  is the correct first boundary.
- An atomic pointer makes activation coherent, but cannot make arbitrary
  multi-file restoration atomic. Deactivate first and make compensations exact
  and replayable.
- A successful temporary-repo test does not replace a separately authorized
  canary.
- A successful canary does not authorize package publication, fleet rollout,
  existing-consumer migration or Autopilot.

## Testing strategy

Implementation follows strict RED-GREEN TDD. The governing test plan covers:

- preview before/after equivalence and no subprocess from target content;
- exact source and target binding, dirty/ref/worktree/config drift;
- selected-source authority and full-manifest drift, including empty commits;
- missing, extra, symlinked, hardlinked, dataless, unsafe-mode, wrong-owner,
  oversized, deeply nested and count-overflow inputs;
- `.git` directory/gitfile, case-variant markers on case-insensitive filesystems,
  nested bare repository and Gitlink smuggling in the bounded managed roots,
  including post-apply rollback zero-mutation rejection;
- exact source/installed lock fields and digest keys, symlinked local Git config
  zero-mutation rejection for apply and rollback, and a linked worktree added
  after apply blocking rollback before journal transition;
- duplicate JSON keys, malformed schemas and recursion bounds;
- ambient `HOME`, `PATH`, `GIT_*`, Python/site/bytecode and loader attacks;
- exact manifest, unexpected runtime entries and bootstrap-before-import;
- every transaction interruption boundary and idempotent replay;
- inactive bytes cannot launch; pointer activation and deactivation are atomic;
- wrong plan/install digest and concurrent lock attempts mutate nothing;
- safe pre-existing managed parents retain identity, mode and consumer bytes;
- journal-bound lifecycle lock absence, replacement and post-flock identity
  drift fail before task/lease or rollback mutation;
- verification mutex persistence prevents old/new inode domains and post-flock
  directory/name substitution fails before Core/runner execution or rollback;
- exact unjournaled provisioning replay, closed active-journal parsing and
  symlinked marker/journal ancestors fail or recover without target mutation;
- every `ROOT_EMPTY`/`P1`/`P2`/`P2Q`/`P3`/`P3Q`/`P4`/`P4T` prefix and cleanup
  boundary remains retryable, while Core-only state is preserved;
- every confined leaf open is nonblocking and is revalidated after open;
- the lifecycle inode before the task lock excludes fresh apply even when the
  writer observed no journal or activation marker;
- rollback excludes a closed Core revision already waiting on the lifecycle barrier;
- rollback restores bytes, modes, directories and Git config with exact-value
  conditional removal only;
- managed and activation inodes remain linked in durable quarantine and are
  rechecked after open-descriptor writes before a receipt can pass;
- drifted rollback target is preserved, never overwritten or deleted;
- a complete synthetic task/lease/close/release cycle after installation;
- target before snapshot equals post-rollback snapshot; and
- the existing Core quarantine tests still prove the old `adopt` and `upgrade`
  commands are non-mutating stubs.

The relevant adoption suite, Core structural suite, full local gate, lock
validation, policy-check, registry-check, doctor, shell syntax and diff check
must pass on the final implementation bytes. An independent security review
must report zero Critical or Important findings before any canary card is
prepared.

## Acceptance gates

The local implementation is ready to request a separate canary decision only if:

1. this design has explicit user approval and a reviewed implementation plan;
2. the dedicated tool remains outside the active Core runtime allowlist;
3. source and target observations are bounded, no-follow and closed;
4. preview proves zero mutation and apply rejects all non-fresh targets;
5. the journal, inactive publication, atomic pointer and rollback are covered at
   every interruption boundary;
6. exact before/after equivalence passes in temporary repositories;
7. compatibility stubs and external adoption prohibition remain intact;
8. the full relevant local gate passes on immutable final bytes;
9. independent security review reports zero Critical or Important findings;
10. no dependency, secret, package, plugin or remote effect was introduced; and
11. an independently reviewed and accepted ADR supersedes the governing
    prohibition only for one fresh-consumer canary; and
12. after that ADR is accepted, a separate native authorization is requested
    for the exact disposable canary only.

Stable adoption still requires the canary to pass and a later decision to
supersede the relevant boundary. Existing-consumer migration requires a
separate design with a bilateral writer barrier or an equally strong offline
protocol.

The adoption tool has its own schema and tool version. It does not reuse the
Core product version as proof of installer quality. If implementation changes
any managed Core runtime byte, bootstrap contract or Core lock schema, it must
bump the Core prerelease version and invalidate evidence bound to the prior
runtime. If the managed Core bytes remain exact, the source commit and adoption
tool digest still change and must be verified independently.

## Documentation impact

The approved implementation plan, ADR 0006, canonical index, maintenance
runbook, security policy and threat model now record the executable local tool.
`external_consumer_adoption=PROHIBITED` remains governing through design,
implementation and local verification. A later independently accepted ADR is a
mandatory predecessor to preparing any canary action. No release note or
consumer receipt exists because no release or consumer adoption occurred.

## Rollback of this design

Before any consumer or canary exists, rollback removes only the local adoption
package, entrypoint, lock, tests and governing documentation introduced by the
approved plan, then revalidates the newly sealed Core runtime. Harness-owned
temporary repositories are disposable; no consumer, package, plugin, remote or
release state is part of this rollback.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del adoption enablement local.
- **Para continuar:** completar la verificación final e informar `IMPLEMENTED_LOCAL / CANARY_PROHIBITED` sin preparar una adopción.
- **Mensaje exacto:** `Revisa el resultado local de adoption enablement de Core 3.1; no prepares ni ejecutes un canary.`
- **Estado de partida:** `origin/main@b07418364409f76c900f0595a76c9e3e388ac433`, rama `codex/control-plane-adoption-enablement-design`, herramienta local implementada sin commit y adopción externa prohibida.
- **No hacer todavía:** instalar, tocar consumidores, migrar v2.1, preparar o ejecutar canary, commit, push, PR, merge, tag, release, plugin, Autopilot o cualquier efecto remoto.
- **Autoridad:** `authorizes=false`.
