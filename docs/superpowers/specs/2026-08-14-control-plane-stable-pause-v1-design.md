# Stable Pause v1 — Verify-only Design

**Status:** `IMPLEMENTED_LOCAL / CLOSES_ON_FINAL_EVIDENCE`

**Classification:** `GOVERNING_CORE / IMPLEMENTED_LOCAL`

**Authority:** `authorizes=false`

**Date:** 2026-08-14

**Scope:** local Control Plane state only

This document is the governing WHAT/WHY contract for the locally implemented
Stable Pause v1 candidate. `CLOSES_ON_FINAL_EVIDENCE` requires final frozen-byte
evidence for a closure claim; it is not a release, installation, consumer
proof, canary result, or remote integration.
Stable Pause does not modify the current lifecycle and does not authorize
cleanup, task closure, lease release, Git transitions, a consumer, a canary,
installation, or remote effects. Every result remains `authorizes=false`.

## 1. Problem

Long-running engineering work needs a routine way to stop at a recoverable
boundary without pretending that the task is complete. Today that outcome is
usually assembled manually from Git state, task and lease state, mutexes,
temporary artifacts, the last RED/GREEN evidence, and a continuation message.
The result can be correct, but it is verbose, inconsistent, and easy to weaken
under context pressure.

A stable pause is not a lifecycle transition. It is a bounded observation that
answers:

> Can this local task be left exactly as it is, with no operation still
> running and enough verified evidence to resume from the same owner?

The observer must fail closed when it cannot establish that answer. It must
never turn uncertainty into cleanup or inferred authority.

## 2. Goals

Stable Pause v1 shall:

1. verify a deterministic, local, recoverable boundary without mutating it;
2. distinguish an active-but-safely-paused task from a terminal task;
3. detect active Control Plane operations, inconsistent lifecycle bindings,
   unstable snapshots, and Control Plane-owned recovery residue;
4. preserve unfinished RED evidence and a dirty worktree as valid work in
   progress when the underlying state is stable;
5. emit a compact, bounded observation that a host skill can turn into a
   continuation capsule;
6. keep Core facts separate from facts only the native host can observe; and
7. remain `authorizes=false` in every result and presentation.

Success means a user can say “haz una parada estable” in a governed task and
receive either a reproducible safe-pause capsule or a precise fail-closed
reason, without changing repository or lifecycle state.

## 3. Non-goals

Stable Pause v1 shall not:

- add `paused` as a task state;
- close, block, suspend, resume, reassign, or otherwise transition a task;
- acquire, renew, release, replace, or repair a lease;
- create a Goal, owner, worker, receipt, journal, lock file, or handoff file;
- delete, move, truncate, quarantine, or repair temporary artifacts;
- stage, commit, push, open a PR, merge, deploy, install, publish, or use a
  remote;
- run the full gate, tests, post-gates, a consumer, or a canary;
- scan arbitrary processes, browser state, caches, the global temporary
  directory, or the whole machine;
- decide that code quality is acceptable merely because a pause is safe; or
- transfer authority to another task, session, user, or future invocation.

Durable repository handoffs remain a separate, explicitly authorized write.
V1 emits its observation to stdout and lets the native host render the capsule;
it does not persist a new artifact.

## 4. Selected architecture

Stable Pause v1 uses a hybrid architecture:

1. **Core observer:** a dependency-free Control Plane component reads
   and validates only repository, lifecycle, mutex, and owned-residue facts.
2. **CLI surface:** the observer is exposed as a verify-only task
   checkpoint command.
3. **Progressive skill reference:** the existing canonical
   `control-plane-run` skill loads a small Stable Pause reference only when the
   user asks to stop, checkpoint, hand off, or compact safely.
4. **Native host join:** the skill adds facts unavailable to Core, including
   whether a tool or test session is still active and the exact semantic
   continuation.

The implemented local command is:

```bash
scripts/control-plane task checkpoint \
  --mode stable-pause \
  --task-id EXACT-TASK-ID \
  --json
```

The command name deliberately avoids `task pause`: v1 observes a checkpoint
but creates no pause state. The exact task ID is mandatory. Core does not infer
an owner from branch names, worktree names, process titles, or conversation
text.

### 4.1 Why not a template only

A prose template cannot safely validate live Git identity, lifecycle digests,
mutex ownership, or recovery residue. It would reduce formatting cost but not
the risk of a false safe-pause claim.

### 4.2 Why not a standalone skill

A second top-level skill would duplicate Control Plane routing, authority, and
handoff rules while remaining unable to prove runtime state. Progressive
loading inside `control-plane-run` keeps one canonical entry point and pays the
context cost only for pause requests.

### 4.3 Why not a mutating pause operation

A new lifecycle state would require transition, recovery, ownership, and lease
semantics disproportionate to the need. The existing active owner should stay
active, its lease should stay exactly as policy requires, and resumption should
continue from the same verified state.

### 4.4 Why not automatic cleanup

Cleanup changes the evidence being certified and can destroy the best clue to
an interrupted operation. V1 reports exact, bounded recovery needs but performs
none of them.

## 5. User experience

The progressive reference should recognize intent such as:

- “haz una parada estable”;
- “déjalo en un punto seguro”;
- “prepara un checkpoint para continuar luego”;
- “compacta y deja continuación exacta”; and
- “stable pause” or “safe checkpoint”.

Recognition does not itself grant authority. The skill runs only read-only
checks permitted by the current task, never expands the task scope, and never
interprets “continúa” as permission for later effects.

The user-facing outcome has one of four statuses:

- `SAFE_PAUSE_ACTIVE`: the named task remains active, coherently owned, and no
  observed local operation is in flight;
- `SAFE_PAUSE_TERMINAL`: the named task is terminal, has no active lease, and
  no observed local operation is in flight;
- `UNSAFE_PAUSE`: a definite contradiction, active operation, drift, or owned
  residue prevents a safe stop; or
- `UNKNOWN`: required evidence is unavailable, out of bounds, or cannot be
  observed by the current host.

`UNKNOWN` never upgrades to a safe status through prose or user reassurance.

## 6. Responsibility boundary

### 6.1 Core owns verifiable local facts

Core is responsible for:

- the exact selected repository root, worktree, branch, HEAD, and common
  Git-dir identity, without accepting a local `core.worktree` redirect;
- a bounded, content-bound local worktree snapshot and `git diff --check`
  result;
- exact parsing and digest validation of the named task record;
- exact lease absence or binding to that task and owner;
- exact adoption and verification lifecycle contracts when present;
- no-create, nonblocking observation of existing Control Plane mutexes;
- bounded classification of Control Plane-owned recovery residue;
- a before/after snapshot comparison; and
- deterministic status, issues, and checkpoint digest generation.

### 6.2 The native host owns session facts

Core cannot prove whether the current Codex host still has a yielded command,
test runner, subagent, browser action, or pending tool call. The skill must add:

- `active_host_operation`: `absent`, `present`, or `unknown`;
- the last failing focused evidence, if relevant;
- the last passing focused evidence;
- the files or subsystem intentionally left untouched next;
- remaining work and pending effects;
- the exact next safe action;
- a concise “do not do yet” list; and
- the native task/thread identity only when the host exposes it exactly.

If any required host fact is `unknown`, the effective capsule status is
`UNKNOWN` even if the Core-domain observation was locally safe.

Core output is evidence, not proof of native task existence or authority.
RED/GREEN entries may only summarize evidence already observed in the exact
task or supplied as a digest-bound checkpoint; the pause flow does not rerun a
test merely to populate them and never fabricates a missing result.

## 7. Verify-only observation protocol

The observer performs the following bounded sequence:

1. Resolve and validate the exact selected repository root, worktree Git dir,
   common Git dir, branch, HEAD, and exact task ID without following unsafe
   links or accepting a local `core.worktree` redirect.
2. Capture the first canonical Git and lifecycle snapshot.
3. Open only pre-existing Control Plane mutex paths with no-create,
   no-follow, bounded, owner-safe primitives.
4. Acquire the applicable mutexes exclusively and nonblockingly in the
   canonical Core order: lifecycle/adoption, verification, named task, then
   lease. Missing paths are interpreted only through the exact lifecycle
   contract; they are never created or repaired.
5. Revalidate every opened descriptor against its named path after lock
   acquisition. A held mutex, substituted name, missing required binding, or
   provisioning/rollback state is fail-closed.
6. Validate the named task, lease, adoption journal, activation binding, and
   owned-residue inventory while holding the applicable observation locks.
7. Capture a second canonical Git and lifecycle snapshot.
8. Compare all stable facts, release descriptors, derive status, and emit one
   bounded JSON object.

Lock acquisition is an ephemeral kernel observation, not a repository write.
The implementation uses `create=false` throughout and the exact order
`adoption.lifecycle -> verification -> named task -> leases`. Every success
and failure path is zero mutation. Stable Pause introduces no second lock
order.

No network or remote-ref refresh participates in v1.

### 7.1 Native-host quiescence join

The skill wraps the Core observation with two native checks:

1. Before invoking Core, verify that no other host-owned command, test, tool
   session, or writing worker is running or yielded. The foreground Stable
   Pause observer itself is the only expected operation.
2. If an already authorized operation is still progressing, do not interrupt,
   kill, or clean it merely to obtain a pause. Wait only through the host's
   bounded native wait mechanism when that remains within the user's request;
   otherwise report `UNSAFE_PAUSE` or `UNKNOWN`.
3. Run the Core command once as a bounded foreground observation.
4. After it exits, verify again that there is no yielded command, test, tool
   session, or writing worker and that the observer process is gone.
5. Join those native facts with the Core object. This is the
   native host before and after join: a present operation yields
   `UNSAFE_PAUSE`; unavailable
   native visibility yields `UNKNOWN`; native evidence never upgrades a Core
   `UNSAFE_PAUSE` or `UNKNOWN`.

The host checks only sessions and workers it can identify natively. They do not
authorize a global process scan or claim that an unrelated external process
does not exist.

## 8. StablePauseObservationV1

The Core output is a closed JSON object. Unknown or extra fields are rejected;
all strings, arrays, nesting, and file reads use the repository's existing
bounded strict-decoding rules.

```json
{
  "schema_version": 1,
  "kind": "StablePauseObservationV1",
  "scope": "core-owned-local-state",
  "status": "SAFE_PAUSE_ACTIVE",
  "repository": {
    "root": "/absolute/worktree/path",
    "common_git_dir": "/absolute/common/git/path",
    "branch": "branch-name",
    "head": "40-hex-commit",
    "status_digest": "sha256:...",
    "worktree_digest": "sha256:...",
    "staged_count": 0,
    "unstaged_count": 1,
    "untracked_count": 0,
    "diff_check": "PASS"
  },
  "lifecycle": {
    "task_id": "EXACT-TASK-ID",
    "task_state": "implementing",
    "task_state_digest": "sha256:...",
    "lease_state": "active",
    "lease_digest": "sha256:...",
    "owner_runtime_digest": "sha256:..."
  },
  "control_plane_state": {
    "adoption_mutex": "free",
    "verification_mutex": "free",
    "task_mutex": "free",
    "lease_mutex": "free",
    "residue_count": 0,
    "residue_digest": "sha256:..."
  },
  "checks": {
    "repository_identity": "PASS",
    "snapshot_stability": "PASS",
    "lifecycle_binding": "PASS",
    "mutex_quiescence": "PASS",
    "owned_residue": "PASS"
  },
  "issues": [],
  "checkpoint_digest": "sha256:...",
  "authorizes": false
}
```

### 8.1 Closed value sets

- `status`: the four statuses in section 5;
- every safety check: `PASS`, `FAIL`, or `UNKNOWN`;
- `diff_check`: `PASS`, `FAIL`, or `UNKNOWN`;
- mutex observations: `free`, `held`, `absent`, or `unknown`;
- `lease_state`: `active`, `absent`, or `unknown`; and
- nullable digests: exact lowercase `sha256:<64-hex>` or JSON `null` where the
  closed lifecycle variant requires absence.

Counts are non-negative bounded integers. Issue entries are closed objects of
`code` and `dimension`, drawn from fixed enumerations, sorted deterministically,
and capped before serialization. The complete JSON output must not exceed
4096 bytes (4 KiB). Bounds overflow produces `UNKNOWN`, never truncated safe
evidence.

### 8.2 Deterministic digests

`status_digest` is computed over canonical bounded local Git-status records
with individual untracked paths enabled, not human-formatted output. Git runs
with the closed configuration including `core.filemode=true`; any
`assume-unchanged` or `skip-worktree` index hint is rejected instead of being
allowed to hide drift.

`worktree_digest` additionally binds every indexed path's worktree bytes and
mode, index entries, path types, symlink targets, explicit absence markers,
and the raw bytes of every present staged, modified, renamed, or untracked path
represented by the observation. Index blobs are read through a single
`cat-file --batch` process with global output bounds. A bounded fixed Git
ignored-path observation is also included: ignored caches stay outside the
unsafe-type inventory, while their closed path set is bound to the digest and
cannot expand through an external excludes file. Gitlinks, nested `.git`
markers, and bare markers fail closed because nested repositories are
unsupported. Read-only Git plumbing and descriptor-safe bounded content
hashing must not create Git objects or refresh the index. A dataless,
unreadable, oversized, unstable, or timed-out path yields `UNKNOWN` rather
than a partial safe digest.

`residue_digest` covers only sorted classifications of recognized Control
Plane-owned residue; it does not hash arbitrary cache or temporary-directory
content.

`checkpoint_digest` is the domain-separated SHA-256 of the strict canonical
JSON object with `checkpoint_digest` omitted. It includes every other stable
field, including `authorizes:false`.

The Core object contains no timestamp, duration, hostname, process ID, random
nonce, or host conversation ID. The host may display an observation time
outside the digested object. Identical stable state therefore yields an
identical checkpoint digest.

The digest detects drift; it is not authentication, a receipt, a capability,
or authority.

## 9. Status derivation

Status precedence is closed and deterministic:

1. Any definite safety contradiction produces `UNSAFE_PAUSE`.
2. Otherwise, any required `UNKNOWN` fact produces `UNKNOWN`.
3. Otherwise, a coherent nonterminal named task and its policy-valid owner and
   lease variant produce `SAFE_PAUSE_ACTIVE`.
4. Otherwise, a coherent terminal named task with no active lease produces
   `SAFE_PAUSE_TERMINAL`; when its lease generation is nonzero, the lifecycle
   must also contain the exact release receipt for that task, lease, owner,
   generation, and digest.

Examples of definite contradictions include:

- repository identity changes between snapshots;
- a required named mutex is held, missing, or substituted;
- task, lease, owner, journal, or activation digests disagree;
- an active lease belongs to another task or runtime;
- a lifecycle is in provisioning, rollback, recovery-required, or another
  non-quiescent state; and
- recognized Control Plane recovery residue exists outside its permitted
  lifecycle state.

A dirty worktree, a preserved failing RED, or `git diff --check` failure is
quality evidence, not by itself proof that the state is crash-unsafe. These
facts remain visible in the observation and capsule. They block whatever later
gate or completion policy requires them, but Stable Pause marks them unsafe
only when they also cause snapshot drift, lifecycle inconsistency, or an active
operation: dirty or RED evidence is not automatically unsafe.

## 10. Owned transient inventory

V1 inventories only a closed set of Control Plane-owned paths and patterns
under the worktree Git dir or common Git dir, as defined by the installed Core
generation. It may classify:

- incomplete provisioning prefixes;
- adoption staging, recovery, or rollback quarantine owned by a journal;
- task-scoped pending-write or recovery artifacts;
- lease receipts or lock bindings that contradict their lifecycle; and
- full-gate mutex state owned by Control Plane.

Normal durable task records, a valid active lease, an active adoption journal,
and persistent lock files are lifecycle state, not residue.

The observer shall not enumerate global `/tmp`, user caches, browser storage,
unrelated ignored files, or arbitrary process tables. An unrecognized artifact
inside a protected Control Plane state root is `UNKNOWN` or `UNSAFE_PAUSE`
according to the existing state-root contract; it is never silently ignored or
deleted.

Project-specific legacy or shadow-owner checks may be added by an installed
profile or the native skill. Such checks may downgrade the effective result but
may not upgrade a Core `UNKNOWN` or `UNSAFE_PAUSE` result.

## 11. Continuation capsule

After joining the Core observation with native host facts, the skill emits a
semantic capsule of at most 4096 bytes (4 KiB) containing:

- effective result and checkpoint digest;
- objective and current unresolved question;
- exact repository, worktree, branch, and HEAD facts appropriate to the host;
- named task/owner/lease state without secret values;
- concise Git and owned-residue evidence;
- last RED and last GREEN evidence, explicitly distinguished;
- remaining work and pending effects;
- exact next action;
- transitions not yet authorized;
- a repository-compliant `## Continuación` block; and
- `authorizes=false`.

The capsule excludes transcripts, hidden reasoning, full diffs, raw tool
output, credentials, cookies, tokens, personal data, and unrelated history.
Private absolute paths may be shown locally when required for exact worktree
routing, but must not be copied into public or remote artifacts by default.

Once the capsule is rendered, the skill should retain only the active working
set: Git identity, task/lease facts, residue result, last RED/GREEN, exact next
action, and constraints. It may suggest native context compaction, but it must
not claim a percentage saving until measured.

## 12. Resume semantics

Stable Pause does not reserve the repository or freeze other actors after the
observer releases its locks. On resume, the skill must:

1. rerun the Core observation for the same exact task and worktree;
2. recheck native host-operation state;
3. compare the new checkpoint digest with the capsule digest;
4. report any drift before proposing the next write; and
5. re-run the normal route, preflight, authority, and task-state gates required
   by the resumed action.

An equal digest proves only equality of the bounded observation. A different
digest is not automatically an error, but it invalidates the prior continuation
assumptions until the change is explained. Neither result transfers prior
authorization into the resumed session.

## 13. Error model

Core issues use stable codes without embedding arbitrary paths or exception
text:

- `E_STABLE_PAUSE_REPOSITORY`: repository or worktree identity is invalid;
- `E_STABLE_PAUSE_SNAPSHOT_DRIFT`: the bounded before/after view changed;
- `E_STABLE_PAUSE_LIFECYCLE`: task, lease, owner, journal, or activation is
  inconsistent;
- `E_STABLE_PAUSE_OPERATION_ACTIVE`: a required mutex is held or an exact
  lifecycle transition is active;
- `E_STABLE_PAUSE_RESIDUE`: recognized owned residue prevents a safe stop; and
- `E_STABLE_PAUSE_BOUNDS`: strict read or serialization bounds were exceeded.

The skill may add `E_STABLE_PAUSE_HOST_UNKNOWN` when the native host cannot
establish whether a command, test, worker, or tool session is still active.

All error paths are zero-mutation. The output may propose the exact recovery
command or owner action as inert text, but it never executes it.

## 14. Security and privacy

- All repository content is untrusted input.
- Reads use strict UTF-8, closed schemas, bounded sizes, no-follow descriptor
  traversal, exact ownership/mode/link checks, and named-path revalidation.
- Subprocesses, if needed for Git, use a closed environment, bounded output,
  fixed arguments, and no shell interpolation.
- The selected root must equal Git's discovered root; index-hint hiding,
  external excludes, executable-mode suppression, filter execution, Gitlinks,
  and nested repository collapse all fail closed.
- Mutex probes use existing files only and never create a second lock domain.
- The observer reads no secrets and never prints record payloads wholesale.
- Issue output uses enumerated dimensions rather than attacker-controlled text.
- `authorizes` is required, exact, and always false, including recursively in
  any future nested envelope.
- Same-UID or filesystem compromise after the final descriptor check remains a
  declared platform residual; Stable Pause does not claim to freeze a hostile
  operating system.

## 15. TDD and acceptance strategy

Implementation, if separately authorized, begins with failing tests for:

1. strict schema, value sets, digest domain, recursion, and 4 KiB bounds;
2. coherent active and terminal safe-pause variants;
3. stable dirty/RED work in progress remaining safe but visibly non-green;
4. every applicable mutex held, missing, replaced, or changed after flock;
5. task/lease/owner/adoption binding mismatch with zero mutation;
6. provisioning, recovery, rollback, and each recognized residue class;
7. unrelated global temporary files and caches remaining unobserved and
   untouched;
8. status or byte-level worktree drift between the two bounded captures,
   including changed bytes whose Git status marker remains the same, local
   `core.worktree` substitution, `core.filemode=false`, `assume-unchanged`,
   `skip-worktree`, ignored-cache boundaries, and collapsed untracked trees;
9. malformed, duplicate-key, non-finite, oversized, deep, or unexpected input;
10. deterministic issue ordering and digest replay;
11. native host active-operation and unknown-operation downgrade behavior;
12. exact natural-language trigger and progressive-reference loading;
13. proof that the skill creates no Goal, task state, lease action, cleanup, or
    effect authorization;
14. resume with equal digest and resume with explained/unexplained drift;
15. canonical and plugin skill-copy parity; and
16. nested `.git`, bare, and Gitlink rejection plus one globally bounded blob
    batch; and
17. terminal receipt deletion and mismatched lease/receipt filenames, followed
    by temporary-repository isolation with no consumer, canary, remote, or
    real user-state mutation.

Tests must snapshot bytes and relevant inode identities before and after all
failure cases. A passing focused suite is not a full-gate or release claim.

## 16. Implemented local surface

This list records the local implementation; it is not authorization or a
release claim:

- `control_plane/stable_pause.py` for `StablePauseObservationV1`;
- the existing CLI dispatcher and launcher surface for `task checkpoint`;
- Core contract and focused test modules;
- the canonical `skills/control-plane-run/SKILL.md` progressive loader,
  `skills/control-plane-run/references/stable-pause-v1.md`, and byte-identical
  `plugins/control-plane/skills/control-plane-run/references/stable-pause-v1.md`;
- CLI/skill routing and parity tests;
- the maintenance runbook, security guidance, and threat model; and
- runtime, entrypoint, plugin, and documentation seals required by the exact
  repository generation.

The governing implementation plan records the exact file map, RED/GREEN
discipline, rollback, and final-evidence closure rule.

## 17. Documentation impact

This document is `GOVERNING_CORE / IMPLEMENTED_LOCAL` and is listed once in the
canonical index. Closure aligns:

- the canonical documentation index and spec classification;
- the Control Plane maintenance runbook;
- security policy and threat model;
- task/lifecycle and CLI reference documentation;
- the canonical skill and plugin copy; and
- lock and normalized documentation snapshot seals.

No ADR is required unless implementation introduces a durable lifecycle state,
new authority model, or another structural decision beyond this verify-only
design.

## 18. Rollback

Rollback means removing the CLI route, observer, tests, and progressive
reference together; restoring prior documentation and seals; and proving the
pre-feature full gate. Persisted Stable Pause state does not require migration
because v1 creates none.

## 19. Design decisions closed in v1

- verify-only, not a lifecycle transition;
- no automatic cleanup or repair;
- no new top-level skill;
- Core observation plus progressive `control-plane-run` reference;
- exact task ID required;
- local-only and no remote refresh;
- deterministic bounded JSON with no timestamp in the digest;
- no persisted receipt or handoff artifact;
- dirty/RED work is compatible with a safe pause when stable;
- host uncertainty downgrades to `UNKNOWN`; and
- every output remains `authorizes=false`.

## 20. Final evidence gate

The local implementation exists and `CLOSES_ON_FINAL_EVIDENCE` requires final
frozen-byte evidence: the focused suite, a bounded `bash tests/run.sh` budget
with `max_gate_runs=3`, post-gates, and two final reviews on identical bytes.
The counter persists through repair and re-freeze in the same closure lineage;
the last consumed run must be green on exact final bytes, and exhaustion enters
Stable Pause. A closure claim is truthful only with that external native
Goal/handoff evidence; the classification remains
`GOVERNING_CORE / IMPLEMENTED_LOCAL`, never installed, consumer-proven,
canary-proven, or released. The gate budget grants no Git or remote authority.
`authorizes=false`.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora.
- **Para continuar:** si Task 8 aún carece de evidencia, congelar los bytes y
  completar el gate/reviews; si ya existe, usar el Goal/handoff nativo sin
  reescribir este sujeto.
- **Mensaje exacto:** `Ejecuta el intento disponible de bash tests/run.sh dentro de max_gate_runs=3 en una ejecutora fresca; el último intento consumido debe quedar verde sobre bytes finales.`
- **Estado de partida:** `GOVERNING_CORE / IMPLEMENTED_LOCAL / CLOSES_ON_FINAL_EVIDENCE`;
  la evidencia Task 8 vive fuera de este documento; sin instalación,
  consumidor, canary ni remoto; `authorizes=false`.
- **No hacer todavía:** limpiar residuos, inferir autoridad, commit, push, PR,
  merge, deploy, consumer, canary o release.
