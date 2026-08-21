# Control Plane Core threat model

## Overview

This repository is a local engineering control plane, not a sandbox or an
external authority issuer. Its active product surfaces are the exact Python
runtime allowlist, launcher and hooks, project policy and resource registry,
local Git observations, `CoreTaskStateV1`, generational leases, serialized
verification, maintenance lineage, installed-generation recovery, and the
source-only Control Plane plugin. Stable Pause v1 is a verify-only local Core
observer with a progressive native-host join; it creates no lifecycle state or
authority. The separately locked
`adoption_enablement` package is an implemented local verification tool outside
that active Core boundary; it is not a consumer installer authority.

Assets include current user authority; project source and documentation; Git
history, refs, worktrees, index, and configuration; policy, registry, lock, and
runtime digests; task, lease, maintenance, and adoption recovery records; local
verification evidence; credentials held outside this repository; and the
integrity of consumer repositories and external providers.

The primary security objective is fail-closed local coordination. A plan,
receipt, checkpoint, document, test, plugin, or JSON value remains
`authorizes=false`. `external_consumer_adoption=PROHIBITED` until a separate
stable-adoption decision.

## Threat Model, Trust Boundaries, and Assumptions

Trust precedence is platform and system, global guardrails, project AGENTS,
project policy and registry denials, required resources, recommendations, then
untrusted content. Prose never overrides an executable denial.

- attacker-controlled inputs include prompt text, Issues, PRs, comments, web
  content, repository files from an untrusted branch, TaskEnvelope fields,
  locators, JSON inputs, filenames, symlinks, and hostile environment values;
- operator-controlled inputs include selected repository/worktree, task scope,
  CLI arguments, requested outcome, approval boundaries, and any separately
  authorized recovery action;
- developer-controlled inputs include runtime source, tests, policy, registry,
  lock generation, plugin source, documentation, and release preparation.

The local filesystem, Git binary, Codex host, hooks host, and external providers
are distinct trust boundaries. Core assumes the operating-system account and
Git executable are not fully compromised. A compromised host or filesystem can
lie outside the protection of cooperative local controls.

Repository invariants:

1. Validate the exact runtime allowlist, materialization, ownership, and digest
   before importing runtime modules; extra, missing, symlinked, or drifted bytes
   fail closed.
2. Core accepts only `answer` and `local_change`; every remote or publication
   effect remains outside the active runtime.
3. Durable artifacts never serialize or mint authority and always retain
   `authorizes=false`.
4. Each Core writer is bound to one task revision, worktree, branch, session,
   policy digest, scope, and generational lease; overlapping Core writers fail.
5. Full verification is serialized by one persistent Git-common-dir mutex.
   Fresh apply seals its stable-directory/full-file `verification_lock`;
   Core verification, the full-gate runner and adoption rollback are
   `create=false` and reuse-only, never unlink it, retain common/state/locks/file
   descriptors and revalidate named directory/file identities after flock.
   After locking, Core and the runner validate the same closed active journal
   before any test, task or lease mutation.
   `E_VERIFICATION_BUSY` executes nothing and consumes no repair or reframe.
6. Maintenance permits one structural reframe. A second stops with
   `E_BOOTSTRAP_REFRAME_LIMIT` and preserves the stable runtime.
7. Legacy inventory is `origin=legacy`, read-only, bounded, and non-resumable.
   Installed-generation rollback validates all bytes and config, then fails
   with `E_ADOPT_QUIESCENCE_UNKNOWN` because v2.1 has no shared writer barrier.
   `legacy_writer_exclusion=COOPERATIVE_ONLY`: an observed legacy writer blocks
   Core, but a same-UID v2.1 process started after that observation does not
   participate in Core locks.
8. Local adoption plans, journals, locks and receipts use closed bounded
   schemas, exact source and target bindings and `authorizes=false`. Target
   policy and registry are evaluated only by `scripts/control-plane` from the
   selected source, and the full source manifest is compared before journaling.
   `adoption_tool=IMPLEMENTED_LOCAL` and `temporary_repository_e2e=PASS` do not
   change `external_consumer_adoption=PROHIBITED`, `canary=NOT_PREPARED`,
   `stable_adoption=NOT_DECIDED` or `Autopilot OFF`.
9. Adoption snapshots bind `managed_parent_directories` and
   `managed_repository_scan=managed-repositories-v1`. The generated target lock
   declares `adoption_lifecycle=journal-bound-v1`; the journal and receipt bind
   the exact `lifecycle_lock` identity. Every Core task/lease mutation creates
   or reuses the lifecycle inode before the task lock, including an initially
   absent state; fresh apply and rollback take that same inode exclusively.
   Installed Core additionally requires exact marker, active journal and lock
   identity after flock.
10. A journal-less provisioning crash is recoverable only in `ROOT_EMPTY`,
    `P1`, `P2`, `P2Q`, `P3`, `P3Q`, `P4` or `P4T` with the reviewed plan still
    exact. Recovery uses nonblocking post-open validation and no-replace durable
    quarantine for directory cleanup; any extra or substituted entry blocks. A
    pre-existing Core-owned verification mutex is not crash provenance and
    remains untouched.
11. Rollback conditionally removes only the exact-value Adoption hooks setting.
    It moves activation and managed leaves into linked durable quarantine,
    revalidates them before the receipt, and leaves reclamation to a separate GC
    that this implementation neither provides nor authorizes.
12. Stable Pause requires one exact task ID and the exact selected repository
    root. Before any Git snapshot it inspects Git-state inode materialization;
    dataless or unobservable Git state returns fail-closed
    `E_STABLE_PAUSE_REPOSITORY`. It then captures two content-bound local
    snapshots and takes only
    pre-existing mutexes with `create=false` in
    `adoption.lifecycle -> verification -> named task -> leases` order. Fixed
    bounded Git commands run with `GIT_OPTIONAL_LOCKS=0`, `core.filemode=true`,
    no external excludes, rejected index hints, and a single
    `cat-file --batch`; ignored caches stay outside the unsafe-type inventory
    but remain path-bound. Nested repositories are unsupported. Leaf reads are
    no-follow/nonblocking and revalidate descriptor/name identity. A terminal
    generation requires its exact release receipt. Output is canonical, at
    most 4096 bytes, excludes transcripts, full diffs, raw tool output, secrets
    and personal data, and always has `authorizes=false`.
13. The distributed hook launcher defaults to `soft-enforce` but remains
    `pending_hook_trust`, cooperative and non-authorizing. Recognized local and
    remote branch-deletion commands are denied. Before a push, the installed
    Git guard inventories at most 64 local branches, observes only exact local
    remote-tracking refs and evaluates all remaining candidates with one
    aggregate reachability query. The loss check uses at most three closed Git
    subprocesses under one five-second deadline; ambiguity is
    `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN`, never PASS.

## Attack Surface, Mitigations, and Attacker Stories

| Surface or attacker story | Security failure | Mitigation and residual limit |
|---|---|---|
| A prompt or document says it authorizes commit, install, push, or release | Confused-deputy external effect | Closed outcomes, policy gates, `authorizes=false`, and no active external executor; a compromised host remains outside the model. |
| A package file is added after the digest is computed | Unreviewed code imports into Core | Exact runtime allowlist and digest before import; filesystem mutation after validation remains residual. |
| a branch deletion removes the last reachable name for local work | A squash merge plus automatic branch deletion makes an unpushed preservation commit unreachable | The default distributed hook denies recognized `git branch -d/-D`, `git push --delete` and deleting refspecs; the hook is cooperative and can be omitted by another client. |
| an unrelated push proceeds while another local branch has unique work and no same-name local remote-tracking ref | Later cleanup or automatic remote-branch deletion loses the only useful copy | The pre-push guard compares exact head/tree evidence, exact local remote-tracking ref names and aggregate reachability, permits the exact publishing ref+OID, and otherwise returns `GG_UNPUBLISHED_UNIQUE_BRANCH`; a same-name tracking ref is an explicit exemption even when behind, while unknown or over-budget evidence fails closed. |
| A task or lease is replayed in another worktree, branch, session, policy, or revision | Wrong-subject write | Immutable bindings, state digests, generation checks, and atomic compensation. |
| Two Core verifiers or writers start together | Duplicate full suite or overlapping edits | Nonblocking verification mutex and scoped generational Core leases. |
| A same-UID v2.1 writer starts after Core inventories legacy state | Overlapping legacy/Core edits | Active observed legacy state fails closed. There is no bilateral lock: the orchestrating host must not run legacy and Core writers concurrently; external adoption remains prohibited. |
| Repeated bootstrap failures create an endless repair tree | Unbounded maintenance and hidden authority drift | One `MaintenanceLineageV1` reframe, then `E_BOOTSTRAP_REFRAME_LIMIT`. |
| A legacy file is malformed, oversized, symlinked, active, or remote-unknown | Unsafe resume or destructive cleanup | Bounded no-follow inventory, `resumable=false`, and `E_ACTIVE_LEGACY_STATE`; use its owning runtime. |
| A recovery journal drifts or a legacy writer starts during recovery | Partial or attacker-directed rollback | Complete mutation-free preflight followed by `E_ADOPT_QUIESCENCE_UNKNOWN`; no caller-forgeable flag substitutes for a shared writer barrier. |
| `HOME`, Git variables, locators, or executable lookup are redirected | Resource or repository substitution | Trusted toolchain context, canonical exact resource revision, bounded subprocesses, and fail-closed unknown. |
| A local green result is presented as stable adoption | Self-certification or supply-chain promotion | `GREEN_LOCAL / PENDING_STABLE_ADOPTION`, `self_certified=false`, manual dogfood, and separate adoption authority. |
| source substitution changes managed bytes after preview | A different runtime is published under a reviewed plan | Apply repeats the immutable source observation, manifest and plan binding; any drift is `E_ADOPTION_SOURCE_DRIFT` with zero target mutation. |
| selected-source authority substitution makes the host checkout decide target validity | A different parser approves policy or registry than the Core bytes being installed | Execute only `scripts/control-plane` from the selected source and compare its full source manifest, including HEAD and tree, before journal creation. |
| wrong-target selection redirects a valid plan | A fresh but unintended repository is mutated | Canonical repo, common-dir, worktree, branch, HEAD, policy and registry bindings are re-observed before journal creation. |
| nested-repository smuggling hides `.git`, bare markers or a Gitlink under managed scope | Publication or rollback crosses repository semantics that preview did not bind | The bounded, descriptor-relative `managed-repositories-v1` scan rejects markers and Gitlinks in preview/apply/verify/rollback; drift stops before deactivation. |
| partial publication leaves launchable files | Incomplete managed bytes execute | All managed bytes are published inactive; launcher and hooks fail closed until the target lock is atomically published last. |
| journal tampering changes recovery intent | Rollback overwrites or deletes unrelated state | Closed schema, separate expected digests, private no-follow storage and exact record revalidation stop before compensation. |
| rollback deletion removes user data | A created-path record is stale or substituted | Deactivate first; remove only exact journal-owned bytes and empty directories with unchanged descriptor identity, otherwise preserve and fail closed. |
| A normal target already has `scripts/` or `.codex/hooks/` | Apply claims a consumer-owned parent, then fails partially or rollback deletes it | Bind the pre-existing parent's device/inode and mode in the reviewed snapshot, create only parents observed absent, and preserve pre-existing parents across exact rollback. |
| A closed Core task waits to revise while adoption rollback begins | Rollback emits PASS, then the queued revision recreates active lifecycle state after deactivation | Every task/lease mutation takes `adoption.lock` shared; rollback holds it exclusive through receipt durability, and `next_revision` revalidates the activation/runtime binding after entering that barrier. |
| lifecycle-lock substitution unlinks or replaces `adoption.lock` | Core and rollback form independent flock domains and both mutate | `journal-bound-v1` makes the mutex mandatory; sealed `lifecycle_lock` metadata and post-flock path identity must match, and recovery never recreates a bound mutex. |
| verification-mutex substitution unlinks, replaces or redirects `verification.lock` or an ancestor | Core, runner or rollback form independent flock domains | Closed `verification_lock`, descriptor-relative no-follow journal reads and retained common/state/locks/file descriptors make every active consumer reuse-only; name/binding drift fails immediately after flock. |
| pre-existing Core-owned verification mutex is mistaken for an Adoption provisioning crash | Fresh apply deletes a legitimate mutex and opens a second exclusion domain | Recovery requires the exact journal-less inventory, revalidates the reviewed plan before cleanup and uses exclusive create; a normal Core mutex blocks unchanged. |
| active-journal schema is only partially checked | Core task/lease writes or the full runner proceed on state that Adoption rejects | A dependency-free Core validator enforces the complete closed active journal before verification, runner execution, or task/lease mutation. |
| a first Core writer observes no Adoption state and waits before its task lock | Fresh apply creates a second lifecycle domain and both write | Core creates or reuses and holds the lifecycle inode before the task lock; apply must acquire that exact inode exclusively. |
| `P2` or `P3` provisioning names are substituted during cleanup | Recovery removes a concurrent directory | Exact `P2Q`/`P3Q` no-replace durable quarantine retains the observed inode and fails if the original name reappears or identity changes. |
| a regular state leaf becomes a FIFO after `stat` | Apply, verify or rollback blocks indefinitely while holding exclusion | Every bounded read/cleanup open is nonblocking, then checks regular type, owner, mode, links, bounds and opened/named identity. |
| `core.hooksPath` changes after rollback preflight | Unconditional unset deletes a consumer's new value | exact-value conditional unset targets only `.codex/git-hooks`; a concurrent value is preserved and rollback reports drift. |
| rollback unlinks a managed inode while another descriptor remains open | Later writes evade final path verification and receipt evidence | Activation and managed leaves remain linked in durable quarantine and are revalidated after the move and before PASS; separate GC is outside scope. |
| filter execution is triggered by Git observation | Target-controlled clean, smudge, textconv or external diff code executes | Closed Git argv disables hooks, filters, textconv and external diff; unsafe attributes fail before content observation. |
| hostile environment redirects Python, Git or startup code | Unverified code executes before lock validation | POSIX `env -i`, absolute tool candidates, `-I -S -B`, disabled bytecode cache and verified captured-byte loaders. |
| lock replay reuses another source, target or plan | A prior local result is treated as current | Adoption and target locks bind exact schema, runtime, source, target and manifest digests; non-exact replay is `E_ADOPTION_REPLAY`. |
| serialized-authority confusion treats a receipt as permission | A plan or receipt is replayed as canary approval | Every nested artifact requires boolean `authorizes=false`; the host must obtain a later ADR and separate native authorization. |
| Git metadata is dataless or unobservable before Stable Pause snapshots | Git observation misreads state and certifies a changed substrate | Stable Pause invokes the inherited Git-state materialization check before its own snapshot commands; reported dataless or any non-PASS result becomes privacy-safe `UNKNOWN / E_STABLE_PAUSE_REPOSITORY`. The inherited discovery and traversal gaps remain explicitly deferred below. |
| repository byte substitution during Stable Pause | The checkpoint certifies bytes different from those observed | Two bounded snapshots bind status, index, types, modes, symlink targets and raw changed bytes; same-UID/filesystem compromise after the last descriptor check remains residual. |
| lock-domain substitution during Stable Pause | Observer and writer hold different inodes | `create=false`, canonical order, nonblocking flock, retained descriptors and named-identity revalidation; a compromised OS/filesystem remains outside the model. |
| malicious Git config or filter redirects observation | Hostile helpers or filters execute or hide bytes | Fixed absolute Git, closed environment, allowlisted read-only plumbing, `GIT_OPTIONAL_LOCKS=0`, bounded output/time and direct blob verification. |
| index-hint hiding uses `assume-unchanged`, `skip-worktree`, mode suppression, or external excludes | Changed indexed bytes or executable modes disappear from the checkpoint | The exact selected root, closed Git config, hint rejection, all-indexed-path byte/mode binding, and one globally bounded blob batch fail closed. |
| nested repository collapse hides a `.git`, bare repository, or Gitlink beneath an untracked directory | The observer digests a collapsed name instead of the repository boundary and its bytes | Bounded descriptor traversal rejects every nested marker and Gitlink; nested repositories are unsupported. |
| terminal receipt deletion removes proof for a prior lease generation | A closed task is reported safely terminal without an exact completed lease lifecycle | Any nonzero terminal generation requires the exact release receipt and matching task, lease, owner, generation, digest, and filename identity. |
| residue smuggling under a protected Core state root | Recovery or staging bytes are mistaken for durable state | Closed bounded owned-residue classifications; unknown entries yield `UNSAFE_PAUSE` or `UNKNOWN` and are never cleaned. |
| digest-as-authority confusion | `checkpoint_digest` is replayed as a capability or approval | Digest means bounded equality only; observation and capsule are `authorizes=false` and cannot transfer authority. |
| host-visibility uncertainty | Core is quiet while a yielded native operation remains active | Native host checks before and after the foreground observer may only downgrade; unavailable visibility is `UNKNOWN` and never upgrades Core. |

Web-application classes such as XSS, CSRF, SQL injection, or tenant isolation
are not primary runtime surfaces because this repository does not serve a web
application. They become relevant only if a future component adds such a
surface; generic labels without reachability are not findings here.

## Severity Calibration

### Critical

A reachable path that lets untrusted repository or prompt content mint reusable
authority, execute an external release, or silently install arbitrary runtime
bytes without an independent host boundary. Another example is credential
exfiltration from outside the repository by the default Core path.

### High

A reliable bypass of the exact runtime allowlist; cross-worktree writer replay
that can corrupt project source; or rollback path traversal that overwrites an
attacker-chosen file under realistic operator use.

### Medium

A bounded denial of service that blocks every Core task, a verification mutex
bypass that predictably duplicates an expensive full suite, or material policy
misrouting that remains local and requires operator action before any external
effect.

### Low

An inaccurate non-authorizing diagnostic, a nuisance warning above the intended
budget, or a documentation inconsistency that cannot change runtime behavior,
authority, project bytes, or external state.

## Residual risks

- A compromised OS account, Codex host, Git binary, filesystem, or provider can
  bypass or falsify its own trust boundary.
- Stable Pause cannot exclude same-UID/filesystem compromise after the last
  descriptor check or non-cooperating external writers that ignore its lock
  domains. It is an observation, not an OS freeze.
- Hooks default to `soft-enforce` in the distributed launcher but remain
  cooperative, `pending_hook_trust`, bypassable by clients that do not invoke
  them, and may coexist with other hooks. They are not branch protection.
- The unpublished-branch guard binds the local `refs/remotes/<remote>/...`
  inventory by name, not containment of the local HEAD or current server
  state. A behind or stale tracking ref can overstate preservation until a
  separate authenticated remote observation refreshes it; same-UID ref
  mutation after the final observation remains TOCTOU.
- More than 64 local branches or an aggregate observation longer than five
  seconds returns `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN`. This is a bounded
  safety stop, not evidence that work is unpublished.
- Existing installed and legacy generations may need their owning stable
  runtime for safe closure; Core deliberately will not resume them.
- A same-UID legacy runtime can start after Core's bounded inventory because
  v2.1 does not inspect the Core namespace and holds no writer lock for the
  lifetime of an edit. Host-level serialization is required until a future
  bilateral migration; Core does not claim atomic legacy exclusion.
- Fresh `3.1.0-core.2` manual dogfood is pending; the `3.1.0-core.1` rows are
  historical only, and stable external adoption is unproven.
- The local adoption tool has passed only harness-owned temporary repository
  tests. It has not been run against a consumer, and no canary has been
  prepared. A later independently accepted ADR and separate native
  authorization remain mandatory boundaries.
- Proven inherited Survey and Adoption hardening gaps that were not introduced
  by the R1 reconciliation are deferred to `codex/survey-hardening-wip` at
  preservation commit `d901bb6c95377074a7fb2fb23762476547335969`: filter
  execution and output/resource bounds, submodule/Gitlink and object-alternate
  handling, detached-worktree substitution, discovery-to-walk TOCTOU,
  newline-bearing paths, APFS case-equivalent Git markers, canonical lock
  completeness, redirected `.git/config` writes, linked-worktree rollback
  inventory, the top-level `orphan_work` meaning, and the add-only
  `only_in_branch` field whose name does not describe commits. They remain
  non-authorizing local risks; external consumer adoption stays prohibited
  until a later bounded front closes or explicitly accepts them.
- The snapshot binds immutable anchor
  `929d3f8a0656fed190bb65ceb3a29deef8de07d6`, its canonical final tracked
  overlay, non-ignored untracked regular files excluding this threat-model
  path, and the normalized threat-model body. Provenance (dirty or committed)
  and current commit identity are intentionally excluded: identical final raw
  bytes and Git executable modes produce the same Version before and after the
  checkpoint commit. Ignored untracked files are outside the snapshot target
  and cannot be runtime inputs; the exact runtime allowlist is enforced
  independently. `snapshot_normalization=exclude_cache_footer_only` removes
  only the final `Repository` and `Version` metadata from its own digest
  preimage.

Repository: sha256:31d48f56964b98247664973b33d474c0f79ce6e9ac191996c9c6ad4307fe8959
Version: codex-security-snapshot/v1:sha256:8d55b2105c95316e14836d31ad943e65e37f04f07d0ccebfb41d79070446c26d
