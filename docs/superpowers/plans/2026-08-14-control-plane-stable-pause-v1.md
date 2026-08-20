# Stable Pause v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before any completion claim. Execute tasks sequentially unless the dependency graph below proves read-only independence.

**Goal:** Implement the approved verify-only Stable Pause v1 observer, CLI checkpoint, and progressive `control-plane-run` reference so a governed local task can be left at a deterministic recoverable boundary without changing task, lease, Git, or repository state.

**Architecture:** A dependency-free Core observer captures two bounded snapshots while holding only pre-existing lifecycle, verification, named-task, and lease mutexes in the canonical order. It emits a closed `StablePauseObservationV1` JSON object. The existing `control-plane-run` skill joins that Core evidence with native host quiescence before rendering a compact continuation capsule. No new lifecycle state, persisted receipt, cleanup path, or top-level skill is introduced.

**Tech Stack:** Python 3.11+ standard library, POSIX `sh`, trusted bounded Git plumbing, strict JSON, `fcntl` locks, `unittest`, Markdown; no new dependency, package installation, network access, consumer, or canary.

**Status:** `IMPLEMENTED_LOCAL / CLOSES_ON_FINAL_EVIDENCE`

**Classification:** `GOVERNING_CORE / IMPLEMENTED_LOCAL`

**Authority:** `authorizes=false`

**Date:** 2026-08-14

**Workflow:** `verified-workflow / structured`, risk `T3`

**Front classification:** `SEQUENTIAL_LOCAL_FRONT`; AE-09 closed before this implementation began. Stable Pause `CLOSES_ON_FINAL_EVIDENCE` and requires final frozen-byte evidence, with no consumer, canary, installation, or remote effect.

---

## Execution boundary and activation record

This plan does not authorize its own execution. The following conditions record
the gate used before implementation and remain the reproducible activation
contract; the later native grants are descriptive, non-transferable, and
`authorizes=false`.

Implementation began only after all of these conditions were freshly true on
the exact implementation bytes:

1. the current Core 3.1 Adoption Enablement AE-09 front has reached a stable, locally recoverable closure;
2. no writer, test, gate, yielded command, task transition, or lease operation is active;
3. `scripts/control-plane route` accepts a new exact `TaskEnvelope` for this plan;
4. `scripts/control-plane preflight --mode write` passes;
5. the user grants fresh local-write authority naming this plan and its exact scope; and
6. one exact task owner already exists, or separately authorized task/lease provisioning has completed, and at most one writer is established without overlapping the AE-09 owner.

At plan-writing time, conditions 3 and 4 were not satisfied. Both commands
failed closed with:

```text
E_RUNTIME_BOOTSTRAP: launcher does not match lock
```

That historical failure recorded the intentionally unsealed AE-09 working tree.
AE-09 was subsequently closed and resealed before Stable Pause Task 0. A later
native grant approved Tasks 0-7 and a separate exact task/lease owner; a native
Goal tracks the remaining local work. Those grants do not replay or transfer
authority to another task or effect.

Cleanup of transient state, push, PR, merge, deploy, release, installation,
consumer adoption, canary, secrets, and any remote effect remain excluded. The
Task 8 full gate uses `max_gate_runs=6` on frozen bytes; the last consumed run
must be green on the exact final bytes. The budget is verification evidence and
does not substitute for authority over any Git or remote effect.

## Closed implementation decisions

1. **Verify-only:** the command observes; it never adds a `paused` state or writes a checkpoint artifact.
2. **Exact owner:** `--task-id` is mandatory and is never inferred from branch, worktree, process, or conversation text.
3. **One canonical skill:** Stable Pause is a progressive reference inside `control-plane-run`, not a new top-level skill.
4. **Local-only:** no fetch, remote-ref refresh, browser inspection, global process scan, cache scan, or global temporary-directory scan participates.
5. **Create-false locks:** all mutex paths must already exist under the exact lifecycle variant; the observer never provisions or repairs them.
6. **Status precedence:** definite contradiction becomes `UNSAFE_PAUSE`; otherwise a required unknown becomes `UNKNOWN`; otherwise a coherent nonterminal task becomes `SAFE_PAUSE_ACTIVE`; otherwise a coherent terminal task without an active lease becomes `SAFE_PAUSE_TERMINAL`.
7. **Dirty is evidence:** stable dirty bytes, a preserved RED, and `diff --check` failure remain visible but do not alone make a pause unsafe.
8. **Deterministic output:** Core JSON is closed, canonical, at most 4 KiB, contains no timestamp, PID, hostname, nonce, prompt, or conversation ID, and always contains `authorizes:false`.
9. **Resume re-observes:** a later resume reruns Core and native checks, compares the checkpoint digest, explains drift, and re-enters normal route, preflight, authority, and lifecycle gates.
10. **No Core version bump:** implementation remains `3.1.0-core.2`. That prerelease is still local and unpublished. Adding the observer changes byte seals and invalidates prior byte-bound evidence, but does not justify an artificial `core.3` migration across Adoption Enablement. A later version change is a separate product decision.

## Requirements and traceability

| ID | Approved requirement | Implementation tasks | Required evidence |
|---|---|---|---|
| `SP-01` | Closed, bounded, deterministic `StablePauseObservationV1` and checkpoint digest. | 1, 4 | contract, bounds, recursion, ordering, replay, and output-size tests |
| `SP-02` | Exact local repository and byte-bound worktree identity without Git mutation. | 2 | clean, dirty, staged, rename, symlink, untracked, dataless/unreadable, drift, and zero-mutation tests |
| `SP-03` | Create-false mutex observation in lifecycle, verification, named-task, lease order. | 3 | held, absent, substituted, post-flock drift, lock-order, and inode-snapshot tests |
| `SP-04` | Exact task, owner, lease, adoption, activation, and residue validation. | 1, 3 | active, terminal, forged, mismatch, provisioning, rollback, recovery, and residue tests |
| `SP-05` | CLI `task checkpoint --mode stable-pause --task-id ID --json`. | 4 | parser, exit-code, stdout/stderr, invocation-count, and zero-write tests |
| `SP-06` | Native host before/after quiescence joins Core without upgrading its result. | 5 | trigger, active operation, unknown visibility, no cleanup, and resume tests |
| `SP-07` | Progressive reference inside canonical and packaged `control-plane-run`. | 5, 6 | inventory, parity, bounded-reference, and forbidden-mutation tests |
| `SP-08` | Documentation, security, seals, and rollback remain coherent. | 6, 7 | lock, manifest, documentation, threat snapshot, Adoption regression, and rollback evidence |
| `SP-09` | Final evidence is fresh and bound to frozen bytes, with no consumer or canary. | 8 | focused suite, a full gate whose last consumed run is green within `max_gate_runs=6`, post-gates, two independent reviews, local observation |

No row may be declared closed unless its RED failed for the intended reason, its GREEN passes on the same bytes, and its zero-mutation assertion proves byte and relevant inode identity preservation.

## Exact file responsibility map

### Create

- `control_plane/stable_pause.py`: closed observer, bounded Git/worktree snapshot, create-false lock graph, lifecycle/residue validation, status derivation, and canonical observation assembly.
- `tests/core_stable_pause_test_support.py`: temporary-repository fixtures, strict byte/inode snapshots, exact active/terminal lifecycle builders, race rendezvous helpers, and no-mutation assertions.
- `tests/test_core_stable_pause.py`: governing Core contract, repository, lifecycle, mutex, residue, status, determinism, and adversarial tests.
- `skills/control-plane-run/references/stable-pause-v1.md`: canonical progressive operator procedure and continuation capsule contract.
- `plugins/control-plane/skills/control-plane-run/references/stable-pause-v1.md`: byte-identical packaged reference.

### Modify

- `control_plane/contracts.py`: shared task-state vocabulary, Stable Pause constants, closed validators, and checkpoint digest domain.
- `control_plane/task_state.py`: import the shared task-state vocabulary without changing transition semantics.
- `control_plane/cli.py`: exact `task checkpoint` dispatcher and exit mapping.
- `control_plane/lockfile.py`: add the new runtime module to the exact Core allowlist.
- `scripts/control-plane`: stage-0 allowlist/runtime update and unchanged bootstrap guarantees.
- `.codex/hooks/control_plane_hook.py`: captured runtime allowlist parity.
- `.codex/control-plane.lock`: resealed Core runtime and entrypoint/hook digests after final runtime bytes freeze.
- `adoption_enablement/manifest.py`: keep the exact Core source projection in
  parity with the reconciled 27-module Core allowlist.
- `.codex/adoption-enablement.lock`: reseal only the isolated Adoption runtime
  digest after that projection allowlist changes; keep tool version `0.1.0`.
- `tests/run.sh`: exact governing test/helper/module manifests only; no behavioral shortcut.
- `tests/test_core_cli.py`: CLI grammar, single invocation, output, exit codes, and no mutation.
- `tests/test_core_contract.py`: strict observation schema, value sets, digest, recursion, and bounds.
- `tests/test_core_governing_manifest.py`: exact new module, governing test, helper, documentation, and skill-reference inventory.
- `tests/test_core_lockfile.py`: 27-module runtime allowlist and exact seal coverage.
- `tests/test_core_plugin.py`: canonical/packaged reference parity and exact plugin inventory.
- `tests/test_core_task_state.py`: prove shared constants preserve all existing transitions and bootstrap semantics.
- `tests/test_core_documentation.py`: spec/plan/index/runbook/security/dogfood/threat and progressive-reference assertions.
- `tests/test_adoption_enablement_preview.py`: exact 27-module source projection
  and single-inclusion regressions for `stable_pause.py` and `survey.py`.
- `skills/control-plane-run/SKILL.md`: bounded trigger and progressive reference routing only.
- `plugins/control-plane/skills/control-plane-run/SKILL.md`: byte-identical packaged skill update.
- `README.md`: current local capability and verify-only/non-authorizing boundary.
- `SECURITY.md`: untrusted repository input, create-false locks, bounded Git, output/privacy, and residual risk.
- `docs/engineering/00-canonical-index.md`: promote approved spec and implementation plan only when implementation becomes governing.
- `docs/engineering/19-control-plane-core-maintenance.md`: stable-pause operation, resume recheck, and fail-closed troubleshooting.
- `docs/engineering/20-control-plane-core-dogfood.md`: invalidate pre-feature byte evidence and record final local observation without consumer/canary claims.
- `docs/security/2026-08-12-control-plane-core-threat-model.md`: Stable Pause attacker stories, mitigations, residuals, and final normalized snapshot footer.
- `docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md`: move from draft to implemented-local only after all acceptance evidence exists; correct any review-found ambiguity without changing approved architecture silently.
- `docs/superpowers/plans/2026-08-14-control-plane-stable-pause-v1.md`: checkbox/evidence updates during a separately authorized implementation.

### Explicitly unchanged

- `.codex/project-policy.toml` and `.codex/resource-registry.toml`: no new authority, route, dependency, or resource registration is required.
- `plugins/control-plane/.codex-plugin/plugin.json`: product version remains `3.1.0-core.2`.
- Adoption transaction, repository, recovery, and CLI behavior remain
  unchanged. Only the exact source-projection allowlist in
  `adoption_enablement/manifest.py`, its focused test, and the resulting
  isolated Adoption lock digest change so a future projection cannot omit the
  new Core module.
- Stable Pause behavior never mutates real task records, lease records,
  journals, receipts, mutex files, or Git configuration. The implementation
  workflow separately provisioned one exact task/lease owner under the
  worktree/common Git dirs; product tests still use temporary repositories only.
- ADRs: the approved verify-only design adds no lifecycle or authority decision requiring a new ADR.

## Dependency graph and execution discipline

```text
Task 0: activate sealed implementation front
  -> Task 1: close contracts and lifecycle vocabulary
      -> Task 2: repository snapshot
      -> Task 3: locks, lifecycle, and residue
          -> Task 4: CLI and compact JSON
              -> Task 5: progressive skill and native join
                  -> Task 6: manifests, launcher, plugin, and Core seal
                      -> Task 7: governing docs and threat snapshot
                          -> Task 8: frozen-byte verification and reviews
```

Use one writer. Read-only review may overlap only after a task's bytes are frozen and no reviewer runs a gate or mutates state. Delegate each Task 8 full-gate attempt to a fresh disposable executor that waits internally and returns one terminal summary. Do not exceed `max_gate_runs=6`; exhaustion enters Stable Pause.

## Task 0: Activate a sealed and separately authorized implementation front

**Files:**

- Read: `AGENTS.md`
- Read: `.codex/project-policy.toml`
- Read: `.codex/resource-registry.toml`
- Read: `docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md`
- Read: this plan
- Temporary only: one `TaskEnvelope` outside the repository or under the worktree Git dir

- [x] **Step 1: Re-anchor the exact repository state.**

Run read-only checks for physical cwd, Git top level, worktree list, branch, HEAD, status, and active `tests/run.sh` or `unittest` processes. Require this existing worktree and a non-protected `codex/` branch. Record but do not clean unrelated dirty bytes.

- [x] **Step 2: Prove AE-09 has stopped safely.**

Require its task/lease owner to be terminal or otherwise explicitly handed off, no recovery-required lifecycle, no live test/gate process, and current Core/runtime seals to agree. If any fact is unknown, stop; Stable Pause cannot be used to manufacture its own safe starting point.

- [x] **Step 3: Normalize and route one exact implementation envelope.**

The envelope must identify the approved spec and this plan, list only the file map above, request local reads and local writes, and set every external or Git transition to false. Create and later remove the fixed temporary envelope with `apply_patch`, then run:

```bash
scripts/control-plane route \
  --task /private/tmp/control-plane-stable-pause-v1-task-envelope.json \
  --mode audit \
  --json
scripts/control-plane preflight --mode write
```

Read every required routed resource completely. Do not treat recommendations as authority.

- [x] **Step 4: Obtain fresh implementation authority.**

The required grant is local source/test/documentation editing and focused temporary-repository tests for Tasks 1-7. It must continue to exclude full-gate execution, real task/lease mutation, cleanup, commit, remote, consumer, and canary. If no valid task already owns the work, obtain a separate exact task/lease provisioning grant before starting; neither plan approval nor implementation authority supplies it. If the user grants a narrower set, rewrite the execution scope rather than inferring the remainder.

- [x] **Step 5: Establish TDD evidence discipline.**

For every later task, preserve the exact RED command and failure reason, implement the smallest coherent behavior, run the named GREEN command, and inspect `git diff --check` plus scoped status. Do not advance on a test that passed before the intended production change or failed for fixture/bootstrap noise.

## Task 1: Close the observation contract and share lifecycle vocabulary

**Files:**

- Create: `tests/core_stable_pause_test_support.py`
- Create: `tests/test_core_stable_pause.py`
- Modify: `control_plane/contracts.py`
- Modify: `control_plane/task_state.py`
- Modify: `tests/test_core_contract.py`
- Modify: `tests/test_core_task_state.py`
- Modify: `docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md` only if an ambiguity is found

- [x] **Step 1: Write strict RED contract tests.**

Add exact tests for:

- the four statuses `SAFE_PAUSE_ACTIVE`, `SAFE_PAUSE_TERMINAL`, `UNSAFE_PAUSE`, and `UNKNOWN`;
- safety checks `PASS`, `FAIL`, and `UNKNOWN`;
- mutex values `free`, `held`, `absent`, and `unknown`;
- lease values `active`, `absent`, and `unknown`;
- exact lowercase SHA-256 values or the variant's required `null`;
- exact root keys and exact nested repository/lifecycle/control-plane/check keys;
- recursively forbidden `authorizes:true` and required root `authorizes:false`;
- strict UTF-8 JSON, duplicate-key rejection, non-finite-number rejection, depth/item/string/file bounds, non-negative counts, at most eight sorted unique closed issues, and at most 4096 canonical output bytes;
- a checkpoint digest computed from domain `control-plane-stable-pause-v1` and the canonical object with `checkpoint_digest` omitted; and
- rejection of timestamps, duration, hostname, PID, nonce, session, prompt, transcript, and arbitrary exception/path text.

Issue codes are limited to the six spec codes and closed dimensions `repository`, `snapshot`, `lifecycle`, `operation`, `residue`, and `bounds`. Host-only `E_STABLE_PAUSE_HOST_UNKNOWN` is never forged by Core.

The validator fixes these exact field sets from the approved spec:

| Object | Exact fields |
|---|---|
| root | `schema_version`, `kind`, `scope`, `status`, `repository`, `lifecycle`, `control_plane_state`, `checks`, `issues`, `checkpoint_digest`, `authorizes` |
| repository | `root`, `common_git_dir`, `branch`, `head`, `status_digest`, `worktree_digest`, `staged_count`, `unstaged_count`, `untracked_count`, `diff_check` |
| lifecycle | `task_id`, `task_state`, `task_state_digest`, `lease_state`, `lease_digest`, `owner_runtime_digest` |
| control-plane state | `adoption_mutex`, `verification_mutex`, `task_mutex`, `lease_mutex`, `residue_count`, `residue_digest` |
| checks | `repository_identity`, `snapshot_stability`, `lifecycle_binding`, `mutex_quiescence`, `owned_residue` |
| issue | `code`, `dimension` |

`diff_check` is `FAIL` if either tracked or staged `diff --check` fails, `UNKNOWN` if a required bounded command is unavailable, and otherwise `PASS`.

Use concrete complete fixture objects from `core_stable_pause_test_support.py`; do not import Adoption Enablement production or test modules into a governing Core test.

Run the intended RED:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_stable_pause.StablePauseContractTests \
  tests.test_core_contract.CoreContractTests.test_stable_pause_contract_is_closed
```

Expected RED: Stable Pause constants and validator/digest functions do not exist. A parser or import error elsewhere is not the intended failure.

- [x] **Step 2: Define one shared lifecycle vocabulary.**

Move the existing eight task-state strings to an immutable `CORE_STATES` tuple in `control_plane/contracts.py`; import and re-export that tuple from `task_state.py` so existing callers and transition behavior do not change. Do not define terminality from the state string alone: preserve the current exact record semantics in which `closed` is terminal only with `resume_state:null`, and `blocked` is terminal only with `resume_forbidden:true` and `resume_state:null`. Do not add `paused` or change any transition edge.

Add regression tests that compare the previous exact tuple and exercise every current start, transition, block, resume, close, revision, and bootstrap-UNKNOWN path.

- [x] **Step 3: Implement the pure Stable Pause contract helpers.**

Add dependency-free functions with these responsibilities:

| Interface | Exact responsibility |
|---|---|
| `validate_stable_pause_observation(value)` | Return a normalized exact closed object or raise a stable contract error. |
| `load_stable_pause_observation(payload)` | Strictly decode bounded UTF-8 JSON, then call the validator. |
| `stable_pause_checkpoint_digest(value)` | Remove only `checkpoint_digest`, include `authorizes:false`, and return the domain-separated digest. |
| `derive_stable_pause_status(checks, lifecycle_class)` | Apply the approved contradiction, unknown, coherent-active, coherent-terminal precedence from a closed internal lifecycle classification without I/O. |

Reuse the existing canonical JSON and closed-decoder primitives. Do not accept subclasses where exact JSON scalar types are required, do not normalize attacker strings into evidence, and do not truncate oversized safe output.

- [x] **Step 4: Make the contract GREEN and prove existing lifecycle compatibility.**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_stable_pause.StablePauseContractTests \
  tests.test_core_contract \
  tests.test_core_task_state
```

Require all tests to pass and confirm that the only production behavior added is pure validation/derivation; task lifecycle bytes remain mutation-compatible with the prior contract.

## Task 2: Implement the bounded byte-bound repository snapshot

**Files:**

- Create: `control_plane/stable_pause.py`
- Modify: `tests/core_stable_pause_test_support.py`
- Modify: `tests/test_core_stable_pause.py`
- Read/reuse: `control_plane/repository.py`

- [x] **Step 1: Write repository-observation RED tests.**

Cover temporary repositories with:

1. clean, staged, unstaged, untracked, renamed, deleted, executable, regular, directory, and symlink paths;
2. changed raw bytes whose porcelain status record remains identical;
3. index and worktree bytes that differ for the same path;
4. explicit absence markers for deletions;
5. stable dirty bytes and a stable `diff --check` failure that remain observable quality evidence;
6. a path swapped, chmodded, rewritten through an open descriptor, or renamed between captures;
7. dataless, unreadable, non-UTF-8 path, FIFO, socket, device, hardlinked regular file, too many paths, too-deep path, oversized file, aggregate-byte overflow, Git timeout, malformed `-z` output, and output overflow;
8. hostile filters, attributes, aliases, environment variables, config, pager, replacement-object settings, local `core.worktree` substitution, and `core.filemode=false`;
9. `assume-unchanged` and `skip-worktree` hints; bounded ignored caches versus nonignored unsafe files; nested `.git`, bare, and Gitlink repositories; and collapsed untracked directory content; and
10. before/after byte and inode snapshots proving every failure path is zero-mutation and creates no Git object, index lock, state file, or receipt.

Run the intended RED:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_stable_pause.StablePauseRepositoryTests
```

Expected RED: `observe_repository_snapshot` and the Stable Pause module are absent.

- [x] **Step 2: Implement exact identity and fixed Git commands.**

Resolve and validate the exact selected repository root, worktree Git dir, common Git dir, branch, and `HEAD^{commit}`; reject a local `core.worktree` redirect. Reuse `trusted_git_executable`, its fixed config including `core.filemode=true` and `core.excludesFile=/dev/null`, and its closed environment. Run only bounded read-only commands equivalent to:

```text
status --porcelain=v1 -z --untracked-files=all --no-renames
ls-files --stage -z
cat-file --batch
symbolic-ref --quiet --short HEAD
rev-parse --verify HEAD^{commit}
diff --check
diff --cached --check
```

Do not run `add`, `update-index`, `write-tree`, `hash-object -w`, checkout, reset, clean, fetch, or any command with implicit index refresh. Set `GIT_OPTIONAL_LOCKS=0` and retain the existing hostile-environment exclusions.

- [x] **Step 3: Bind every relevant path to its actual bytes.**

Use descriptor-relative, no-follow reads and stable pre/open/post identity checks. Open regular leaves with `O_RDONLY|O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC`, then require the full regular/owner/mode/link/identity contract through `fstat` and named `lstat` before and after reading. Read symlink targets through bounded no-follow primitives. Reject FIFO, socket, device, hardlink, dataless, identity drift, `assume-unchanged`, and `skip-worktree` without blocking. The digest input must bind sorted status records, sorted index records, every indexed path's worktree bytes and mode, path bytes, file type, symlink target, explicit absence, staged blob identity, a bounded ignored-path set, and bounded raw worktree bytes for every present staged, modified, renamed, or untracked path represented by the observation. Read every required index blob through a single `cat-file --batch` process with one aggregate bound. Ignored caches stay outside the global unsafe-type inventory but remain digest-bound; Gitlinks and nested `.git` or bare markers fail closed because nested repositories are unsupported. Reject a missing, non-blob, malformed, or oversized batch response; never create an object.

Use these closed initial limits, changing them only through an explicit spec amendment supported by RED tests:

| Bound | Value |
|---|---:|
| Git command timeout | 5 seconds |
| Git output bytes | 8 MiB per command |
| porcelain records | 4096 |
| index records | 20000 |
| encoded path | 4096 bytes |
| one file | 8 MiB |
| aggregate worktree bytes | 64 MiB |

Any timeout, instability, unavailable content, or overflow returns an `UNKNOWN` dimension; it must never emit a partial safe digest.

Domain-separate the canonical status and full worktree inputs as `control-plane-stable-pause-status-v1` and `control-plane-stable-pause-worktree-v1`. The second digest includes the first input rather than treating a porcelain marker as byte evidence.

- [x] **Step 4: Make repository observation GREEN.**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_stable_pause.StablePauseRepositoryTests
```

Then rerun the same fixture twice without mutation and assert identical `status_digest` and `worktree_digest`; change only raw dirty bytes and assert `status_digest` may remain equal while `worktree_digest` changes.

## Task 3: Observe mutexes, lifecycle bindings, and owned residue without creating state

**Files:**

- Modify: `control_plane/stable_pause.py`
- Modify: `tests/core_stable_pause_test_support.py`
- Modify: `tests/test_core_stable_pause.py`
- Read/reuse: `control_plane/verification.py`
- Read/reuse: `control_plane/leases.py`
- Read/reuse: `control_plane/task_state.py`
- Read/reuse: `control_plane/contracts.py`

- [x] **Step 1: Write lifecycle and mutex RED tests.**

Create a matrix for coherent active and terminal tasks plus every definite contradiction:

- lifecycle/adoption, verification, named-task, and lease mutex held;
- optional legacy lock legitimately absent versus required journal-bound lock absent;
- file or parent directory replaced before or after `flock`;
- wrong owner, mode, type, link count, size, identity, journal state, activation marker, runtime digest, task digest, lease digest, task ID, or lease owner;
- active lease for another task or runtime, or terminal generation without its
  exact release receipt;
- forged, duplicate-key, non-finite, oversized, resumed-closed, or otherwise invalid task/lease/journal record;
- provisioning prefixes `P1`, `P2`, `P2Q`, `P3`, `P3Q`, `P4`, and `P4T`;
- adoption staging, recovery, rollback quarantine, task pending-write artifacts, contradictory receipts, unknown protected-root entries, and full-gate mutex inconsistency;
- unrelated files in global temporary/cache/browser locations remaining unobserved and untouched; and
- a transition racing between first snapshot, lock acquisition, and second snapshot.

For every case snapshot record bytes, directories, lock paths, inode identities, Git config, and Git object/index metadata before and after.

Run the intended RED:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_stable_pause.StablePauseLifecycleTests \
  tests.test_core_stable_pause.StablePauseResidueTests
```

- [x] **Step 2: Implement the create-false lock graph.**

Open only existing private directories and lock files with descriptor-relative no-follow operations. Retain root, common-Git-dir, state-root, lock-directory, and lock-file descriptors through all protected reads and the second snapshot. Acquire exclusive nonblocking locks in this exact order:

```text
adoption.lifecycle -> verification -> named task -> leases
```

The named task lock is `sha256(task_id) + ".lock"`; the other names are `adoption.lock`, `verification.lock`, and `leases.lock` in their existing canonical roots. Use no `O_CREAT`, revalidate parent and file identities after each `flock` and again before release, and release in reverse order only after the second snapshot. Never call writer helpers that provision a missing mutex. Lifecycle-variant interpretation decides whether absence is valid, unsafe, or unknown.

- [x] **Step 3: Validate state while all applicable locks are held.**

Use existing strict Core validators and public read-only store surfaces where they preserve descriptor and lock guarantees. Validate the named task, owner/runtime, lease, adoption journal, activation lock, and verification binding as one coherent generation. A journal in anything other than the exact active variant is non-quiescent. A terminal task requires no active lease and, when `lease_generation > 0`, its exact release receipt; a coherent nonterminal task requires its exact policy-valid lease variant.

Capture the second repository and lifecycle snapshot before releasing any observation lock. If any stable fact differs, derive `E_STABLE_PAUSE_SNAPSHOT_DRIFT` and do not preserve a digest as safe evidence.

- [x] **Step 4: Implement a closed owned-residue inventory.**

Enumerate only recognized Control Plane state roots under the worktree Git dir and common Git dir. Classify durable valid records separately from provisioning, staging, recovery, rollback quarantine, pending-write, contradictory receipt, and unknown protected-root entries. Hash only sorted closed classifications. Do not scan global `/tmp`, user caches, browser data, arbitrary ignored files, or a process table.

Domain-separate the canonical residue classification as `control-plane-stable-pause-residue-v1`; an empty inventory still has a deterministic digest and count zero.

- [x] **Step 5: Make lifecycle and residue behavior GREEN.**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_stable_pause.StablePauseLifecycleTests \
  tests.test_core_stable_pause.StablePauseResidueTests \
  tests.test_core_task_state \
  tests.test_core_leases \
  tests.test_core_verification
```

Require deterministic issue ordering, exact precedence, and zero mutation in every safe, unsafe, unknown, held-lock, and race case.

## Task 4: Expose one bounded verify-only CLI checkpoint

**Files:**

- Modify: `control_plane/stable_pause.py`
- Modify: `control_plane/cli.py`
- Modify: `tests/test_core_stable_pause.py`
- Modify: `tests/test_core_cli.py`

- [x] **Step 1: Write CLI RED tests.**

Fix the exact accepted grammar:

```bash
scripts/control-plane task checkpoint \
  --mode stable-pause \
  --task-id EXACT-TASK-ID \
  --json
```

Tests must prove:

- every flag is required exactly once and positional/inferred task IDs are rejected;
- no other mode, write flag, cleanup flag, remote flag, or free-form output is accepted;
- the observer is called exactly once;
- stdout contains exactly one canonical JSON object and no banner;
- safe active and terminal results exit 0, unsafe exits 1, unknown exits 2, and usage errors retain the existing CLI usage code;
- stable issue codes reach JSON without arbitrary exception or repository text;
- output above 4096 bytes fails unknown rather than truncating a safe object; and
- success and every failure leave Git, task, lease, journal, lock, receipt, and temporary-state snapshots unchanged.

Run the intended RED:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_cli.CoreCliTests.test_task_checkpoint_stable_pause_has_closed_grammar \
  tests.test_core_cli.CoreCliTests.test_task_checkpoint_maps_closed_results_to_exit_codes \
  tests.test_core_stable_pause.StablePauseCliTests
```

- [x] **Step 2: Add the smallest exact dispatcher.**

Add `task checkpoint` alongside the existing task command family. The dispatcher validates `--mode stable-pause`, exact task ID, and `--json`, calls the observer once, validates its returned object again at the serialization boundary, writes canonical UTF-8 JSON plus one newline, and returns the closed exit mapping.

Do not add `task pause`, a default task, a human mode, persisted output, cleanup, retry, wait, remote refresh, or implicit task/lease action.

- [x] **Step 3: Assemble the final Core object.**

`observe_stable_pause(repository, task_id)` must perform the approved eight-step protocol, fill every closed field, derive checks and status, sort issues, compute the checkpoint digest last, validate the full object, enforce 4096 bytes, and only then return it. Error conversion maps only known repository/contract/lock/bounds failures to stable issue dimensions; unknown exception text is not serialized.

- [x] **Step 4: Make the CLI GREEN.**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_stable_pause \
  tests.test_core_cli \
  tests.test_core_contract
```

Inspect captured stdout/stderr and prove repeated observation of the same stable fixture produces byte-identical JSON.

## Task 5: Add progressive skill routing and the native-host quiescence join

**Files:**

- Create: `skills/control-plane-run/references/stable-pause-v1.md`
- Create: `plugins/control-plane/skills/control-plane-run/references/stable-pause-v1.md`
- Modify: `skills/control-plane-run/SKILL.md`
- Modify: `plugins/control-plane/skills/control-plane-run/SKILL.md`
- Modify: `tests/test_core_plugin.py`
- Modify: `tests/test_core_documentation.py`

- [x] **Step 1: Write progressive-loading and host-join RED tests.**

The canonical skill tests must require:

- exact stop/checkpoint intent routes to `references/stable-pause-v1.md`;
- ordinary Control Plane work does not load that reference;
- the reference is no more than 4096 bytes and the packaged copy is byte-identical;
- the reference contains the exact CLI command, statuses, exit semantics, before/after native check, same-task resume check, and `authorizes=false`;
- Core `UNSAFE_PAUSE` or `UNKNOWN` can never be upgraded by the host;
- a native active operation downgrades to unsafe and unavailable native visibility downgrades to unknown;
- the foreground observer is allowed only during the bounded invocation and is absent in the after-check;
- the procedure never kills, interrupts, cleans, waits unboundedly, mutates task/lease, creates a Goal, runs tests/gates, or performs a Git/remote transition; and
- the continuation capsule excludes transcript, hidden reasoning, raw output, full diff, secrets, and personal data.

Run the intended RED:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_plugin.CorePluginTests.test_stable_pause_reference_is_progressive_and_packaged \
  tests.test_core_documentation.CoreDocumentationTests.test_stable_pause_skill_join_is_verify_only
```

- [x] **Step 2: Write the canonical progressive reference.**

The reference procedure must:

1. resolve the exact current task ID and worktree from trusted native context;
2. check native host-owned commands, tests, tool sessions, and writers before Core;
3. refuse global process, cache, browser, or `/tmp` scanning;
4. invoke the Core command once in the foreground with a bounded native wait;
5. repeat the native host check after exit;
6. derive an effective result that only preserves or downgrades Core status;
7. render a semantic capsule at most 4096 bytes; and
8. on resume, rerun observation, compare the digest, explain drift, and re-enter normal route/preflight/authority gates.

Use this closed effective-result join:

| Core status | Native host fact | Effective result |
|---|---|---|
| safe active or safe terminal | clear before and after | preserve the Core status |
| any status | active operation before or after | `UNSAFE_PAUSE` |
| safe active or safe terminal | visibility unavailable | `UNKNOWN` |
| `UNSAFE_PAUSE` | any native fact | `UNSAFE_PAUSE` |
| `UNKNOWN` | clear or unavailable | `UNKNOWN` |

The capsule must contain objective, unresolved question, exact Git identity, named task/lease facts, compact dirty/residue evidence, explicitly separate last RED and last GREEN, remaining work, pending effects, next exact action, unauthorized transitions, and the repository's `## Continuación` fields. Observation time may be displayed outside the digested Core object.

- [x] **Step 3: Link the reference from both skill entrypoints.**

Add only bounded intent routing and one relative reference link. Keep the canonical and packaged `SKILL.md` files byte-identical. Do not duplicate the full procedure into the entrypoint and do not introduce a new skill registration.

- [x] **Step 4: Make the skill surface GREEN.**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_plugin \
  tests.test_core_documentation.CoreDocumentationTests.test_stable_pause_skill_join_is_verify_only
```

Then inspect both skill inventories and exact byte parity. This is documentation/test validation, not a live task pause.

## Task 6: Register runtime/test manifests and reseal Core only

**Files:**

- Modify: `control_plane/lockfile.py`
- Modify: `scripts/control-plane`
- Modify: `.codex/hooks/control_plane_hook.py`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/run.sh`
- Modify: `tests/test_core_lockfile.py`
- Modify: `tests/test_core_governing_manifest.py`
- Modify: `tests/test_core_plugin.py`
- Modify after a projection RED only: `adoption_enablement/manifest.py`
- Modify after that runtime changes: `.codex/adoption-enablement.lock`
- Modify: `tests/test_adoption_enablement_preview.py`

- [x] **Step 1: Write manifest and seal RED tests.**

Require the exact runtime tuple to contain `stable_pause.py` once, in canonical order, with no missing or extra module. Require the launcher, hook bootstrap, lockfile module list, lockfile validator, and independent lockfile test inventory to agree. Add the new governing test/helper/reference/doc paths to every exact manifest and require `tests/run.sh` to reject omission, duplication, or drift.

Run the intended RED:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_lockfile.CoreLockfileTests.test_schema_two_declares_independent_exact_core_inventory \
  tests.test_core_governing_manifest \
  tests.test_core_plugin
```

Expected RED: the new files are not yet recognized by all exact inventories and the old Core runtime seal does not cover them.

- [x] **Step 2: Update exact inventories without changing the version.**

Change all duplicate stage-0/runtime allowlists together. The Core runtime grows from 25 to 26 modules. Keep `product_version = "3.1.0-core.2"` in the Core lock, launcher, hook, package, and plugin manifest. The initial implementation expected Adoption Enablement to remain byte-identical; Task 8 calibration later proved that its exact source-projection allowlist must also name `stable_pause.py`. That bounded correction does not change Adoption behavior or tool version.

Recompute active Core LOC and require it to stay within the existing 21,530-line policy ceiling. Do not raise that ceiling as part of Stable Pause; split or simplify the observer if the candidate exceeds it.

- [x] **Step 3: Freeze runtime and bootstrap bytes, then reseal once.**

After Tasks 1-5 code, tests, launcher, and hook bytes are final:

1. compute the Core runtime digest using `control_plane.lockfile.runtime_digest` and independently with the existing lockfile-test algorithm;
2. compute exact policy, registry, hooks manifest, hook entrypoint, pre-commit, pre-push, and launcher SHA-256 values;
3. update only their matching fields in `.codex/control-plane.lock`;
4. rerun both computations and require exact equality; and
5. prove `.codex/adoption-enablement.lock` remains byte-identical to its pre-task snapshot.

Do not reseal around a failing test, do not edit a digest to satisfy only one implementation, and do not touch the threat-model footer yet.

- [x] **Step 4: Run the focused bootstrap/manifests GREEN.**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_lockfile \
  tests.test_core_governing_manifest \
  tests.test_core_plugin \
  tests.test_core_cli \
  tests.test_core_stable_pause
```

Then run the four launcher-backed read-only checks only after the lock is exact:

```bash
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
scripts/control-plane preflight --mode write
```

These checks validate the local implementation front. On the expected
uncommitted candidate, policy-check, registry-check and doctor pass; write
preflight fails closed only with `E_GIT_DIRTY`. That result is not a runtime or
seal failure and does not authorize an implicit commit. These checks do not
authorize a real task checkpoint, full gate, commit, or remote effect.

## Task 7: Align governing documentation, security, dogfood, and the normalized snapshot

**Files:**

- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `docs/engineering/00-canonical-index.md`
- Modify: `docs/engineering/19-control-plane-core-maintenance.md`
- Modify: `docs/engineering/20-control-plane-core-dogfood.md`
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Modify: `docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md`
- Modify: `docs/superpowers/plans/2026-08-14-control-plane-stable-pause-v1.md`
- Modify: `tests/test_core_documentation.py`

- [x] **Step 1: Write documentation RED tests before changing governing prose.**

Segment exact governing sections and require:

- the verify-only command and four statuses;
- mandatory exact task ID and `authorizes=false`;
- create-false canonical lock order and zero-mutation behavior;
- dirty/RED work remaining visible but not automatically unsafe;
- native host before/after quiescence and no result upgrade;
- deterministic 4 KiB observation/capsule bounds and resume digest comparison;
- no cleanup, lifecycle transition, Goal, test/gate, Git transition, remote, consumer, or canary;
- exact canonical spec, plan, skill, and reference classification/inventory;
- attacker stories for repository byte substitution, lock-domain substitution, malicious Git config/filter, residue smuggling, digest-as-authority confusion, and host-visibility uncertainty; and
- explicit residual risk for same-UID/filesystem compromise after the last descriptor check and non-cooperating external writers.

Run the intended RED:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_documentation.CoreDocumentationTests.test_stable_pause_governing_contract \
  tests.test_core_documentation.CoreDocumentationTests.test_stable_pause_threats_and_runbook_are_aligned
```

- [x] **Step 2: Promote the approved design only with implementation evidence.**

After Tasks 1-6 pass, change the spec and plan from draft/non-governing to the repository's exact implemented-local classification. Add both to the canonical index once, with purposes that distinguish WHAT/WHY from HOW. Do not describe Stable Pause as released, installed, consumer-proven, or remotely integrated.

If implementation diverges from the approved verify-only architecture, stop and request a spec amendment. Do not silently rewrite the spec to fit code.

- [x] **Step 3: Write the operator and security contract.**

The maintenance runbook must give exact safe invocation, status/exit interpretation, native join, continuation capsule, resume recheck, and fail-closed next actions. README and dogfood must describe it as a local Core capability. SECURITY and the threat model must document inputs, trust boundaries, fixed Git execution, descriptor/lock identity, bounded output, privacy exclusions, non-authority of digests, and residuals.

Mark every prior Core byte-bound gate/review/dogfood digest as superseded by the reconciled 27-module runtime. Preserve historical evidence as history; do not overwrite it as if it had covered Stable Pause or Survey together.

- [x] **Step 4: Make governing documentation GREEN.**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_documentation \
  tests.test_core_governing_manifest \
  tests.test_core_plugin
```

- [x] **Step 5: Reseal the threat-model footer last.**

Only after every tracked and governing untracked byte is frozen, compute the canonical value:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -c \
  'from tests.test_core_documentation import normalized_snapshot_version; print(normalized_snapshot_version())'
```

Use `apply_patch` to change only the final `Version:` line in the threat model, preserve the `Repository:` line, rerun the helper, and require the value to remain identical. Then rerun the exact threat-model footer test. Any later byte change invalidates this step and requires one new final reseal.

## Pre-freeze implementation evidence

This receipt records Tasks 0-7 before the normalized footer is sealed. It is
descriptive, never transfers authority, and remains `authorizes=false`.

| Task | Intended RED | Current pre-seal evidence |
|---|---|---|
| 1-6 | task-specific contract, observer, lifecycle, CLI, skill, and exact-inventory omissions. | Core non-document focus `175/175`; Stable Pause plus repository regression `44/44`; 26 modules; 18,505/21,530 LOC |
| 7 | governing-document controls and final snapshot footer absent or stale. | documentation `26/27`; the sole expected RED is the normalized `Version:` mismatch closed by the final footer reseal |
| Task 8 calibration remediation | 26th Core projection record absent and isolated Adoption runtime lock stale. | intended projection RED `1/1`; projection/lock focal `2/2`; full Adoption regression `89/89` |

The implementation owner `TASK-CONTROL-PLANE-STABLE-PAUSE-V1-R1` reached
`closed`; its generation-1 lease has zero active records and one immutable
release receipt. Core runtime seal
`sha256:4289fd2683336dbdf76108c6644c717ec34b9be480dbc72fd28e813ba35d4dc3`
matches two independent oracles; its lock file SHA-256 is
`78c06e750b625fc7cbdfb9787feb5351982676e19af1afc4e26409626fc2e21a`.
The separately domain-bound Adoption runtime is
`sha256:6f4cd41b869c9a55b900cdb3c3f1e9acf73c2a2210632b48321486d07ee6f2eb`;
its lock file SHA-256 is
`6dffb70c452885b9984c3b23a7ace4d81d0a7c9d19b1366ba8f96e58645c80b3`.

Task 8 checkboxes remain intentionally open in the frozen governing subject.
Its authoritative gate, post-gates, review verdicts and final acceptance belong
to the native Goal and final handoff; editing this plan afterward would change
the bytes those results certify.

## Task 8: Verify frozen bytes, obtain independent reviews, and record local evidence

**Files:**

- Verify: every path in the exact file responsibility map
- Modify only if a RED or review finding requires a TDD correction: the narrow owning files and their tests
- Never modify: a consumer repository, remote, canary, release, or real user data

### Task 8 calibration remediation: exact Adoption projection

The prior unchanged-Adoption assumption was falsified by the first aggregate
calibration: the Core lock correctly declared 26 modules while
`adoption_enablement/manifest.py` still accepted and projected only 25. The
suite therefore failed before Adoption behavior with
`E_ADOPTION_SOURCE_LOCK`, and the isolated Adoption runtime digest was stale.

The repair followed TDD on the local candidate only:

1. `tests/test_adoption_enablement_preview.py` required exactly 26 projected
   Core modules and one `control_plane/stable_pause.py` record, then failed with
   the intended source-lock error;
2. `adoption_enablement/manifest.py` added that single module to its closed
   projection allowlist;
3. the focused projection test passed;
4. two independent Adoption digest implementations agreed on the new isolated
   runtime value before `.codex/adoption-enablement.lock` was resealed; and
5. the projection and lock focal tests passed `2/2`.

This is a descriptive local remediation, remains `authorizes=false`, and does
not alter Adoption transactions, install in a consumer, run a canary, or grant
any Git or remote transition.

- [ ] **Step 1: Freeze the candidate and prove no operation is active.**

Record branch, HEAD, scoped status, changed-path inventory, Core lock digest, Adoption lock byte identity, and threat snapshot. Require no overlapping writer, test, gate, or yielded session. From this point, any byte correction returns to its owning RED/GREEN task and invalidates all later evidence.

- [ ] **Step 2: Run the focused suite once on the frozen candidate.**

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
  tests.test_core_contract \
  tests.test_core_stable_pause \
  tests.test_core_cli \
  tests.test_core_task_state \
  tests.test_core_leases \
  tests.test_core_verification \
  tests.test_core_lockfile \
  tests.test_core_plugin \
  tests.test_core_governing_manifest \
  tests.test_core_documentation \
  tests.test_adoption_enablement_contracts \
  tests.test_adoption_enablement_repository \
  tests.test_adoption_enablement_preview \
  tests.test_adoption_enablement_transaction \
  tests.test_adoption_enablement_recovery \
  tests.test_adoption_enablement_bootstrap \
  tests.test_adoption_enablement_e2e
```

The Adoption tests are regressions against the projected Core generation; they do not install in a consumer. Record exact test count, exit code, and frozen candidate digests.

- [ ] **Step 3: Execute the bounded full-gate budget.**

Load or initialize `gate_run_count` for the same closure lineage and set
`max_gate_runs=6`. Repair and re-freeze do not reset it. A fresh disposable
executor acquires the verification mutex, starts the command, waits internally,
and returns one terminal summary:

```bash
bash tests/run.sh
```

Acquiring the mutex and starting the command increments `gate_run_count`.
Content repair returns to TDD, invalidates the previous final-byte evidence and
may consume the next run after a new freeze. The last consumed run must be
green on the exact final bytes. At `gate_run_count=6` without that state, enter
Stable Pause. The budget grants no Git or remote authority.

- [ ] **Step 4: Run post-gates only after an exit-zero authoritative gate.**

```bash
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check
git status --short --branch
```

Record all exit codes and outputs by summary, not transcript. A dirty worktree is expected until a separately authorized Git transition, but every dirty path must be explained and byte-bound.

- [ ] **Step 5: Obtain two independent final reviews on identical bytes.**

Review A covers spec/plan traceability, closed schemas, status derivation, UX, docs, rollback, and exact acceptance evidence. Review B covers security, descriptor/no-follow I/O, Git execution, mutex order/identity, lifecycle/residue races, privacy, seals, and zero mutation. For a reconciliation merge, each reviewer verifies inherited-parent provenance and concentrates line-level inspection on conflict resolutions and bytes not identical to either exact parent, while retaining every crossed security boundary in scope. Each reports Critical, Important, and Minor findings without editing or running the full gate.

Any Critical or Important finding blocks closure, returns to TDD, invalidates the gate and both reviews, and requires fresh frozen-byte evidence. Minor findings must be fixed or explicitly accepted with rationale before closure.

- [ ] **Step 6: Run temporary and local verify-only acceptance.**

First prove both `SAFE_PAUSE_ACTIVE` and `SAFE_PAUSE_TERMINAL` in isolated temporary repositories. Then, only if the exact implementation task already exists and its lifecycle is coherent, run once against that same task:

```bash
scripts/control-plane task checkpoint \
  --mode stable-pause \
  --task-id TASK-CONTROL-PLANE-STABLE-PAUSE-V1-R1 \
  --json
```

Do not create, transition, close, suspend, or release anything merely to make this observation possible. If the named real task does not exist, use only temporary-repository evidence and record local dogfood as unproven. Join native before/after quiescence, render the compact capsule, and prove a second unchanged observation has the same checkpoint digest.

- [ ] **Step 7: Close only the local implementation claim.**

Closure requires every `SP-*` row GREEN, exact seals, the authoritative gate and post-gates PASS on the reviewed bytes, two independent zero-Critical/zero-Important reviews, and zero unresolved mutation/recovery state. The claim is `IMPLEMENTED_LOCAL`; consumer, canary, installation, Git integration, and remote proof remain absent and unauthorized.

## Acceptance matrix

| Dimension | Pass condition | Fail-closed result |
|---|---|---|
| Repository | exact root/common dir/branch/HEAD and stable byte-bound digest | `UNSAFE_PAUSE` for contradiction; `UNKNOWN` for unavailable/bounded input |
| Lifecycle | exact coherent task, owner/runtime, lease, journal, activation | `E_STABLE_PAUSE_LIFECYCLE` |
| Mutexes | all applicable named identities acquired EX nonblocking with create=false | `E_STABLE_PAUSE_OPERATION_ACTIVE` or lifecycle error |
| Residue | no disallowed recognized residue and no unknown protected-root entry | `E_STABLE_PAUSE_RESIDUE` or `UNKNOWN` |
| Output | strict closed object, deterministic digest, at most 4096 bytes | `E_STABLE_PAUSE_BOUNDS` |
| Host join | no other native operation before or after | downgrade to unsafe or unknown; never upgrade Core |
| Mutation | byte/inode/Git/state snapshots identical before and after | test failure and no completion claim |
| Resume | same task/worktree re-observed and digest compared before writes | drift invalidates continuation assumptions |

## Rollback plan

Stable Pause v1 persists no feature state, so source rollback is path-exact:

1. remove the CLI route, `control_plane/stable_pause.py`, its test/helper modules, and both progressive reference copies together;
2. restore shared contract/task-state imports without changing lifecycle semantics;
3. remove the new paths from every runtime, test, helper, skill, and governing inventory;
4. restore prior README, SECURITY, index, runbook, dogfood, spec/plan classification, and threat-model content;
5. restore the prior Core lock allowlist and recompute every live digest from restored bytes;
6. reseal the normalized threat-model footer last; and
7. prove the pre-feature focused suite and a final green full gate within
   `max_gate_runs=6`.

Use `apply_patch` for rollback edits. Do not use `git reset --hard`, `git checkout --`, `git clean`, recursive deletion, or mutation of task/lease/journal state. If implementation ever creates persisted Stable Pause state, that is a spec violation requiring a new migration/rollback design rather than improvisation.

## Documentation assessment

- **ADR:** not required while the approved verify-only, no-new-authority architecture remains intact.
- **Plan:** this document is required because the work is T3, multi-file, security-sensitive, and seal-bound.
- **Runbook:** required because operators gain a new checkpoint procedure and resume check.
- **Threat model:** required because untrusted Git/worktree bytes and lock-domain races are new observation surfaces.
- **Release receipt:** not required; no release is authorized.
- **Issue:** use only for work explicitly deferred outside this plan; do not hide a failed acceptance row in prose.

## Implementation authority gate

Approval of this plan authorized no implementation. The later native grants for
Tasks 0-7 and exact task/lease provisioning were separately observed and are
not replayable authority. Task 8's read-only gate is limited by
`max_gate_runs=6`; its last consumed run must be green on frozen final bytes.
That evidence never authorizes a Git or remote transition.

Dependencies, secrets, CI/CD, consumer repositories, canaries, and remote systems are outside the implementation scope. Any later need to touch one of them stops execution for a scope amendment.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora.
- **Para continuar:** ejecutar Task 8 solo sobre los bytes congelados y registrar
  sus resultados en el Goal/handoff nativo sin reescribir el sujeto certificado.
- **Mensaje exacto:** `Ejecuta el siguiente intento disponible de bash tests/run.sh dentro de max_gate_runs=6 en una ejecutora fresca; la última ejecución debe quedar verde sobre bytes finales, sin consumidor, canary, commit ni remoto.`
- **Estado de partida histórico, previo a la reconciliación R1:** worktree `control-plane-adoption-enablement-design`, rama
  `codex/control-plane-adoption-enablement-design`, HEAD
  `4d5c0e5bfde35978b872f9e6b2eae5a52a759c95`; runtime Core de 26 módulos antes
  de unir Survey; el candidato reconciliado R1 debe cerrar con 27 módulos;
  implementación task `closed`, cero lease activa; sin consumidor, canary ni
  efecto remoto; `authorizes=false`.
- **No hacer todavía:** superar `max_gate_runs=6`, limpiar evidencia,
  commit, push, PR, merge, deploy, instalar, usar consumidor o canary.
