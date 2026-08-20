# Control Plane Core 3.1 Adoption Enablement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a separately locked, local-only adoption tool that can preview, apply, verify, and exactly roll back Control Plane Core 3.1 in fresh temporary repositories while keeping consumer adoption prohibited.

**Architecture:** A new stdlib-only `adoption_enablement` package and `scripts/control-plane-adoption` entrypoint remain outside the 25-module Core runtime and outside `scripts/control-plane`. The tool observes immutable source bytes and an eligible fresh target, publishes all managed bytes inactive, atomically publishes the target `.codex/control-plane.lock` last, verifies the exact installed generation, and deactivates before exact rollback. Its plans, journals, locks, and receipts are closed, bounded, digest-bound, and always `authorizes=false`.

**Tech Stack:** Python 3.11+ standard library, POSIX `sh`, Git plumbing, TOML/JSON contracts, `unittest`; no new dependency, package installation, plugin installation, or network access.

**Authority boundary:** The exact implementation authorization covered local source edits and temporary-repository tests only. It did not authorize installation in a consumer, a canary, migration, commit, push, PR, merge, tag, release, publication, Autopilot, or any remote effect. `external_consumer_adoption=PROHIBITED` remains governing.

---

## Decision on GitHub Spec Kit

Control Plane already implements the same development stages with stronger operational boundaries:

| Spec-driven stage | Existing Control Plane mechanism | Decision |
|---|---|---|
| Constitution | `AGENTS.md`, project policy, registry, ADRs | Keep Control Plane canonical. |
| Specify | approved design specs and `TaskEnvelope` | Keep; add stable requirement IDs in this plan. |
| Clarify | task framing and clarification gate | Keep fail-closed behavior. |
| Plan | `docs/superpowers/plans` plus `writing-plans` | Keep this format. |
| Tasks | checkbox tasks, dependency order, verified workflow | Keep; bind every task to requirements and tests. |
| Implement | TDD, exact gates, review, rollback | Keep; implementation occurred only under the later exact local authorization. |
| Analyze | route, policy, registry, security and independent review | Adopt an explicit pre-execution convergence check. |
| Checklist | required gates and acceptance evidence | Adopt a closed traceability matrix below. |

The useful Spec Kit concepts adopted here are:

1. `specification -> implementation task -> test/evidence` traceability;
2. a consistency pass before implementation begins and again before completion;
3. strict separation between the specification's WHAT/WHY and the plan's HOW;
4. test-first task ordering.

The Spec Kit CLI is not installed, vendored, or invoked. Do not add `.specify/`, generated slash commands, an updater, `uv`, `pipx`, PyPI artifacts, or copied templates. This avoids a second constitution and command system competing with Control Plane. Because no Spec Kit code or template is incorporated, this task creates no third-party code attribution or dependency obligation. Reassess licensing before copying any upstream bytes in a later task.

## Requirements and traceability

| ID | Requirement from the approved specification | Implementation tasks | Governing tests/evidence |
|---|---|---|---|
| `AE-01` | Adoption runtime is structurally separate from Core and old `adopt/upgrade` actions remain quarantined. | 1, 6, 7, 8 | contracts, bootstrap, quarantine, manifest tests |
| `AE-02` | Plans, journals, locks, and receipts use closed bounded schemas and `authorizes=false`; `verification_lock` binds one directory/file domain. | 1, 4, 5 | contract, transaction and closed-journal verifier tests |
| `AE-03` | Source and target observation is closed, bounded, no-follow, owner-bound, and ambient-environment independent. | 2, 3 | safe-I/O, repository, preview adversarials |
| `AE-04` | `preview` proves exact source/target binding and zero mutation. | 3 | preview before/after and drift matrix |
| `AE-05` | `apply` publishes inactive bytes and atomically publishes the target lock last. | 4 | interruption-boundary transaction tests |
| `AE-06` | Status and verify are read-only; rollback deactivates first and restores exact prior state; every active verifier is `create=false` and reuse-only. | 5 | status/verify/rollback/recovery and Core/runner mutex tests |
| `AE-07` | The new entrypoint validates its own exact allowlist before imports. | 6 | bootstrap, pyc, shadow, site, and lock tests |
| `AE-08` | Temporary-repository E2E proves install, a synthetic Core task cycle, and exact rollback without becoming a consumer canary. | 7 | local E2E fixture and before/after digest |
| `AE-09` | Full gate, security review, documentation, and Core quarantine stay green on final immutable bytes. | 8 | full local gate and independent review |

Implementation began only after convergence review confirmed every `AE-*` row had an implementation owner, a RED test, a GREEN test, and a rollback assertion. A missing or contradictory row would have blocked execution; it was not silently reinterpreted.

## Implementation evidence

The rows below name the test-first failure surface, the final governing
evidence and the exact rollback property. They record local implementation
only; none is a canary or an adoption authorization.

| ID | RED evidence | GREEN evidence | Rollback evidence | Resolution |
|---|---|---|---|---|
| `AE-01` | RED: separate runtime and quarantine contract absent | GREEN: `test_adoption_enablement_is_structurally_separate_from_core` and exact manifest scanner | ROLLBACK: the adoption runtime remains outside the exact Core module tuple; the separately authorized bootstrap correction is version-bound below | `CLOSED` |
| `AE-02` | RED: closed plan, journal, verification binding and receipt parsers absent | GREEN: `test_closed_contracts_accept_only_the_exact_schema` and `test_core_and_runner_require_a_closed_active_adoption_journal` plus authorizes-false adversarials | ROLLBACK: replay and malformed evidence mutate nothing | `CLOSED` |
| `AE-03` | RED: no bounded no-follow repository observer | GREEN: `test_git_markers_inside_managed_scope_are_rejected_without_mutation`, `test_gitlink_inside_managed_scope_is_rejected_without_mutation`, `test_managed_repository_scan_depth_and_count_are_bounded_without_mutation`, `test_target_authority_uses_the_exact_selected_source_entrypoint` and `test_runner_rejects_a_symlinked_adoption_binding_ancestor` | ROLLBACK: `test_nested_repository_drift_after_apply_blocks_verify_and_rollback_before_mutation` preserves activation and pre-existing parents | `CLOSED` |
| `AE-04` | RED: preview and immutable source-target binding absent | GREEN: `test_preview_is_path_safe_and_zero_mutation` and `test_source_head_drift_after_locked_preview_fails_before_journal` | ROLLBACK: preview has exact before-equals-after proof and source drift creates no journal | `CLOSED` |
| `AE-05` | RED: no inactive publication state machine | GREEN: `test_every_durable_boundary_is_recoverable_and_never_partially_active` | ROLLBACK: target lock is published last and every earlier boundary recovers | `CLOSED` |
| `AE-06` | RED: status, verify, one verification domain and exact rollback absent | GREEN: `test_missing_or_replaced_lifecycle_lock_blocks_core_and_rollback`, `test_unjournaled_mutex_provisioning_is_exactly_recoverable`, `test_core_verifier_retains_the_locked_directory_identity`, `test_runner_retains_the_locked_directory_identity`, `test_verification_guard_keeps_one_persistent_mutex_domain` and `test_verification_guard_revalidates_named_identity_after_flock` cover closed projection, crash replay and descriptor-held exclusion | ROLLBACK: `test_rollback_rejects_a_missing_or_replaced_bound_verification_mutex`, `test_rollback_and_recovery_restore_exact_consumer_tree` and `test_rollback_excludes_a_waiting_closed_task_revision` | `CLOSED` |
| `AE-07` | RED: unverified adoption bootstrap absent | GREEN: bootstrap suite rejects pyc, shadow, site, hostile environment and lock drift | ROLLBACK: bootstrap failures import nothing and mutate nothing | `CLOSED` |
| `AE-08` | RED: no complete temporary-repository lifecycle | GREEN: `test_full_temporary_repository_lifecycle` | ROLLBACK: harness target bytes, modes and Git config equal the before snapshot | `CLOSED` |
| `AE-09` | RED: adoption sources and tests absent from the authoritative manifest | GREEN: closure requires one passing final-byte focal set, a full gate whose last consumed run is green within `max_gate_runs=3`, all post-gates and both independent rereviews on identical bytes | ROLLBACK: implementation rollback is path-exact; the bootstrap correction remains limited and version-bound | `CLOSES_ON_FINAL_EVIDENCE` |

The later AE-06/AE-09 verification-lock remediation is fixed by
`test_core_owned_verification_mutex_is_not_adoption_provisioning`,
`test_provisioning_recovery_validates_plan_before_cleanup`,
`test_fresh_verification_provisioning_is_exclusive`,
`test_verification_guard_revalidates_common_and_state_after_flock`,
`test_core_and_runner_require_a_closed_active_adoption_journal`,
`test_invalid_active_adoption_journal_blocks_task_and_lease_mutation`, and
`test_invalid_active_adoption_journal_blocks_new_lease_claim`. These tests
prove that recovery never claims a normal Core mutex, validates the reviewed
plan before cleanup, retains one descriptor-bound mutex domain, and rejects a
non-exact active journal before verification or task/lease mutation.

The final bounded-concurrency matrix is fixed by
`test_active_adoption_journal_counts_the_root_toward_the_item_bound`,
`test_invalid_active_journal_blocks_new_task_before_creating_its_lock`,
`test_each_partial_journalless_provisioning_prefix_is_recoverable`,
`test_each_provisioning_cleanup_boundary_remains_retryable`,
`test_post_cleanup_validation_failure_leaves_a_retryable_prefix`,
`test_core_only_verification_prefixes_are_preserved`,
`test_forged_closed_task_blocks_rollback_without_mutation`,
`test_rollback_preserves_a_record_substituted_after_preflight`,
`test_confined_read_opens_the_leaf_nonblocking`,
`test_root_empty_core_prefix_race_removes_only_the_created_lifecycle_lock`,
`test_p2_p3_cleanup_never_removes_a_substituted_directory`,
`test_p4t_cleanup_opens_and_revalidates_the_observed_temporary`,
`test_new_task_holds_a_lifecycle_domain_even_when_adoption_was_absent`,
`test_rollback_conditionally_removes_only_its_exact_hooks_path`,
`test_rollback_retains_open_managed_and_activation_inodes_in_quarantine`,
`test_rollback_rechecks_managed_quarantine_after_an_open_descriptor_write`, and
`test_rollback_rechecks_activation_quarantine_after_an_open_descriptor_write`.
Together they bind `ROOT_EMPTY`, `P1`, `P2`, `P2Q`, `P3`, `P3Q`, `P4` and
`P4T`; a nonblocking post-open check; the lifecycle inode before the task lock;
exact-value hooks restoration; and linked durable quarantine through final
receipt evidence.

## File responsibility map

New adoption runtime, outside `control_plane/`:

- `adoption_enablement/__init__.py`: tool/schema version only.
- `adoption_enablement/contracts.py`: canonical JSON, duplicate-key rejection, closed plan/journal/receipt contracts, semantic digests.
- `adoption_enablement/safe_io.py`: descriptor-relative bounded reads/writes, identity, materialization, ownership, mode, and fsync primitives.
- `adoption_enablement/repository.py`: fixed closed Git execution and immutable source/fresh-target observations.
- `adoption_enablement/manifest.py`: exact source manifest, target projection, target lock rendering, and before-snapshot digest.
- `adoption_enablement/transaction.py`: adoption lock, journal state machine, apply, status, verify, rollback, and crash recovery.
- `adoption_enablement/lockfile.py`: exact adoption-module allowlist and adoption runtime digest.
- `adoption_enablement/cli.py`: closed JSON command dispatch only.
- `scripts/control-plane-adoption`: isolated stage-0 bootstrap that verifies captured bytes before importing the package.
- `.codex/adoption-enablement.lock`: separate tool lock; never a Core runtime pointer.

New test surfaces:

- `tests/adoption_enablement_test_support.py`: private temporary source/target repositories and bounded snapshot helpers.
- `tests/test_adoption_enablement_contracts.py`
- `tests/test_adoption_enablement_repository.py`
- `tests/test_adoption_enablement_preview.py`
- `tests/test_adoption_enablement_transaction.py`
- `tests/test_adoption_enablement_recovery.py`
- `tests/test_adoption_enablement_bootstrap.py`
- `tests/test_adoption_enablement_e2e.py`

Existing files modified only during a separately authorized implementation:

- `tests/run.sh`: add separate adoption manifests; do not add adoption modules to `CORE_MODULES`.
- `tests/test_core_governing_manifest.py`: prove exact separation and closed test manifests.
- `tests/test_core_quarantine.py`: prove Core cannot dispatch the new tool and old compatibility actions remain inert.
- `tests/test_core_documentation.py`: bind governing documentation, requirement IDs, and prohibition wording.
- `README.md`, `SECURITY.md`, `docs/adr/0006-control-plane-core-and-quarantine.md`, `docs/engineering/00-canonical-index.md`, `docs/engineering/19-control-plane-core-maintenance.md`, and `docs/security/2026-08-12-control-plane-core-threat-model.md`: document the locally implemented but non-authorized enablement boundary.
- `control_plane/task_state.py` and its focal tests: the bootstrap correction
  distinguishes terminal local `UNKNOWN` run outcomes from remote or pending
  `UNKNOWN`.
- `control_plane/leases.py` and its focal tests: the later lifecycle-barrier
  work excludes task/lease mutation while an installed generation rolls back.
- `control_plane/contracts.py`, `control_plane/verification.py`,
  `tests/test_core_task_state.py`, `tests/test_core_verification.py`, and
  `tests/run.sh`: the subsequent AE-09 verification-lock remediation shares a
  closed active-journal contract and one persistent descriptor-bound mutex
  domain across Core, the runner, and Adoption.
- `.codex/control-plane.lock` and `.codex/adoption-enablement.lock`: separately
  seal the final Core and Adoption runtime bytes.

The original adoption authorization prohibited changes to the 25 Core modules.
The later bootstrap correction, candidate bump, lifecycle barrier, and
verification-lock remediation are recorded here as separate chronological
local implementation boundaries. This chronology records implemented local
scope only; it does not grant, replay, or transfer authority and remains
`authorizes=false`. The authorized bump to `3.1.0-core.2` binds the corrected
managed Core bytes to a new prerelease; evidence bound to `3.1.0-core.1` remains historical
and cannot certify `3.1.0-core.2`. Do not modify
`.codex/project-policy.toml`, `.codex/resource-registry.toml`, consumer
`AGENTS.md`, `.github/workflows/`, package manifests, dependency locks, or any
other Core behavior without exact additional authority.

## Task 1: Freeze adoption contracts and traceability in RED

**Files:**

- Create: `adoption_enablement/__init__.py`
- Create: `adoption_enablement/contracts.py`
- Create: `tests/test_adoption_enablement_contracts.py`
- Modify later: `tests/test_core_documentation.py`

**Requirements:** `AE-01`, `AE-02`, `AE-09`.

- [ ] Record the source baseline: branch, HEAD, status, Core runtime digest, source lock digest, and the SHA-256 of the approved specification and this plan. Do not fetch or mutate Git.
- [ ] Add RED tests for exact `CoreAdoptionPlanV1`, `CoreAdoptionJournalV1`, and `CoreAdoptionReceiptV1` key sets, schema version, kind, bounded collections, digest format, duplicate JSON keys, nesting depth, unknown keys, and recursive `authorizes=false`.
- [ ] Add a RED documentation test requiring the exact requirement IDs `AE-01` through `AE-09`, one traceability row per ID, and at least one named test surface per row.
- [ ] Add adversarial tests for `authorizes=true`, integer-as-boolean, non-finite numbers, invalid UTF-8, oversized JSON, duplicate keys, and a recomputed digest over a mutated payload.

Use closed constructors rather than passing arbitrary mappings onward:

```python
REQUIREMENT_IDS = tuple(f"AE-{index:02d}" for index in range(1, 10))

def load_closed_json(payload: bytes, *, limit: int) -> dict[str, object]: ...

def validate_plan(value: object) -> tuple[ContractIssue, ...]: ...

def plan_digest(value: Mapping[str, object]) -> str: ...
```

- [ ] Run the RED test:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest \
  tests.test_adoption_enablement_contracts -v
```

Expected: import failure because `adoption_enablement` does not exist.

- [ ] Implement only canonical JSON, semantic digest, the three closed schemas, bounded parsing, and stable error codes required by the tests. Do not add filesystem or Git behavior.
- [ ] Re-run the module and expect all contract tests PASS.
- [ ] Run the convergence check: every `AE-*` ID must still map to at least one later task and test; any mismatch blocks Task 2.
- [ ] Do not commit unless a later user message explicitly authorizes that checkpoint commit.

## Task 2: Build bounded filesystem and closed Git observations

**Files:**

- Create: `adoption_enablement/safe_io.py`
- Create: `adoption_enablement/repository.py`
- Create: `tests/adoption_enablement_test_support.py`
- Create: `tests/test_adoption_enablement_repository.py`

**Requirements:** `AE-03`.

- [ ] Add RED tests for source and target path ancestors that are symlinks, leaf symlinks, hardlinks, FIFOs, sockets, dataless placeholders, wrong owner, group/world writable paths, excessive depth, excessive entry count, oversized files, and identity drift between observation and read.
- [ ] Add RED tests proving fixed Git uses an absolute trusted executable, stdin `DEVNULL`, closed environment, fixed `-c` hardening, output caps, deadlines, process-group cleanup, and no ambient `HOME`, `PATH`, `GIT_*`, filters, textconv, fsmonitor, pager, replace refs, grafts, or transport.
- [ ] Add RED target-eligibility tests for bare repositories, detached/protected branches, dirty worktrees, second worktrees, `.git` directories and gitfiles, nested bare repositories, Gitlinks, depth/count overflow, submodules, pre-existing managed leaves, existing `core.hooksPath`, Core/legacy state, and unobservable state. Bind each managed parent's absence or safe device/inode and mode so ordinary pre-existing `scripts/` or `.codex/hooks/` directories remain usable and consumer-owned. Re-run the same bounded scan before verify and rollback; each rejection must return `UNKNOWN` or a stable `E_ADOPTION_*` code with zero mutation.

Implement these typed observations:

```python
@dataclass(frozen=True)
class SourceObservation:
    repository_id: tuple[int, int]
    head: str
    tree: str
    core_version: str
    core_runtime_digest: str
    source_lock_digest: str

@dataclass(frozen=True)
class TargetObservation:
    repository_id: tuple[int, int]
    common_dir_id: tuple[int, int]
    worktree_id: tuple[int, int]
    branch: str
    head: str
    policy_digest: str
    registry_digest: str
    before_snapshot_digest: str
    core_hooks_path_before: None
    managed_parent_directories: tuple[Mapping[str, object], ...]
    managed_repository_scan: Mapping[str, object]
```

The closed parent tuple is serialized as `managed_parent_directories`. The scan
assertion is exactly `managed_repository_scan=managed-repositories-v1` and binds
absence of nested `.git` markers, bare repositories and Gitlinks below the
bounded managed roots. Target policy and registry checks execute only
`scripts/control-plane` from the selected source.

- [ ] Ensure callers cannot provide a `fresh=true` assertion. Freshness is derived only from the closed observation.
- [ ] Inventory only fixed known Core and legacy state roots with count/depth/byte caps. Unknown schema, malformed JSON, permission errors, or budget exhaustion is not absence.
- [ ] Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest \
  tests.test_adoption_enablement_repository -v
```

Expected after implementation: PASS, no network, no target-content subprocess, and every blocked fixture byte-identical before/after.
- [ ] Do not commit without later exact authority.

## Task 3: Generate the exact managed projection and zero-mutation preview

**Files:**

- Create: `adoption_enablement/manifest.py`
- Create: `tests/test_adoption_enablement_preview.py`

**Requirements:** `AE-03`, `AE-04`.

- [ ] Add RED tests requiring the source manifest to contain all and only:
  - `.codex/hooks.json`;
  - `.codex/hooks/control_plane_hook.py`;
  - `.codex/git-hooks/pre-commit` and `pre-push`;
  - `scripts/control-plane`;
  - the exact 25 `control_plane/*.py` files declared by the source Core lock.
- [ ] Require every manifest record to bind repository-relative path, role, raw SHA-256, Git executable mode, byte size, source commit, source tree, product version, source runtime digest, and source-manifest digest.
- [ ] Add RED tests proving consumer-owned `.codex/project-policy.toml`, `.codex/resource-registry.toml`, and `AGENTS.md` are observed but never projected or changed.
- [ ] Add RED tests for `preview --source SOURCE --target TARGET --json`: before/after target snapshots match exactly; output contains only the closed `CoreAdoptionPlanV1`; output is bounded and path-safe; no absolute source/target path, prompt, content, secret, or authority is serialized.
- [ ] Validate target policy and registry using only `scripts/control-plane` from the selected source, then bind the same full source manifest to the plan and final pre-journal projection. A clean empty commit after preview is `E_ADOPTION_SOURCE_DRIFT` even when managed records are byte-identical.
- [ ] Render a target-specific `.codex/control-plane.lock` in memory. It must bind the copied Core bytes plus the target's existing policy and registry digests. It is not written during preview.

The command contract is:

```text
scripts/control-plane-adoption preview \
  --source <canonical-local-source> \
  --target <canonical-fresh-target> \
  --json
```

Success returns exit `0`, `result=PASS`, `applicable=true`, `mutation=false`, and `authorizes=false`. Uncertainty returns exit `2`, `result=UNKNOWN`, `applicable=false`, `mutation=false`, and `authorizes=false`.

- [ ] Re-observe source and target after plan construction and reject any identity or digest drift.
- [ ] Run the preview module; expect PASS with a byte-for-byte zero-mutation proof.
- [ ] Do not execute `apply`, install anything, or modify a non-temporary target.

## Task 4: Implement inactive publication and atomic activation

**Files:**

- Create: `adoption_enablement/transaction.py`
- Create: `tests/test_adoption_enablement_transaction.py`

**Requirements:** `AE-02`, `AE-05`.

- [ ] Add RED tests for every interruption point in this exact state machine:

```text
prepared -> staged -> published_inactive -> active
                                   \-> rolling_back -> rolled_back
active -> rolling_back -> rolled_back
```

- [ ] Add RED tests proving a different plan/source/target binding returns `E_ADOPTION_REPLAY` and mutates nothing; exact replay returns the existing safe receipt without additional mutation.
- [ ] Open `<git-common-dir>/codex-control-plane-core/adoption.lock` descriptor-relative, no-follow, private, owner-bound, mode `0600`, link count one and size zero before journal creation. Seal the policy as `adoption_lifecycle=journal-bound-v1`; every Core writer creates or reuses the lifecycle inode before the task lock, and fresh apply takes that same inode exclusively. A raced `ROOT_EMPTY` cleanup removes only the inode created by that apply. Hold the lock through final receipt durability.
- [ ] Require both `--plan` and `--plan-digest`; the digest prevents mistakes but never grants authority.
- [ ] Re-observe source and target under the adoption lock before the first write. Any drift invalidates the plan.
- [ ] Persist `CoreAdoptionJournalV1` before publication, including exact `lifecycle_lock` identity, `managed_parent_directories`, `managed_repository_scan`, absent-before records, only directories observed absent and created by this transaction, target identity, prior local Git config, source/target digests, and transaction state. Never claim a pre-existing managed parent.
- [ ] Stage all managed files in private same-filesystem directories, validate bytes/modes, fsync files and directories, then publish everything except `.codex/control-plane.lock`.
- [ ] Prove the target launcher and hooks fail closed while the target lock is absent.
- [ ] Set the previously absent local `core.hooksPath` only while hooks remain inactive. Reject an existing value; never merge or replace it.
- [ ] Publish `.codex/control-plane.lock` by same-directory atomic rename as the last activation step, fsync the directory, re-read through no-follow descriptors, and then mark the journal `active`.

The future command is:

```text
scripts/control-plane-adoption apply \
  --source <canonical-local-source> \
  --target <canonical-fresh-target> \
  --plan <reviewed-plan.json> \
  --plan-digest sha256:<64-lowercase-hex> \
  --json
```

- [ ] Run transaction tests with deterministic fault injection at every durable boundary. Expected: either exact active state or exact rollback-safe journal; never a partially active generation.
- [ ] Do not run this command against a consumer. Test only harness-owned temporary repositories.

## Task 5: Add read-only status/verify and exact recovery/rollback

**Files:**

- Modify: `adoption_enablement/transaction.py`
- Create: `tests/test_adoption_enablement_recovery.py`

**Requirements:** `AE-02`, `AE-06`.

- [ ] Add RED tests that `status` emits only state, product/tool versions, install digest, verification state, error codes, and `authorizes=false`. It must not echo raw journal paths, backups, source paths, contents, prompts, or credentials.
- [ ] Add RED tests that `verify` revalidates the journal-bound adoption lock identity, journal, target lock, installed manifest, every managed byte/mode, parent bindings, nested-repository scan, policy/registry digests, Git hooks configuration, and unexpected managed entries without repair.
- [ ] Add RED rollback tests for active Core tasks, leases, a closed revision waiting on the shared lifecycle mutex before any task or lease lock, verifier mutex contention, journal drift, installed-byte drift, mode drift, target identity drift, missing records, extra managed entries, and unsafe paths. Every rejected case must be zero-mutation.
- [ ] Treat `adoption.lock` as a bilateral lifecycle barrier from the first Core mutation: every task/lease writer creates or reuses the lifecycle inode before the task lock, retains it shared before `leases.lock`, and fresh apply/rollback take the same inode exclusively. After flock, both sides revalidate the named inode; an installed Core additionally requires the activation marker, journal state exactly `active` and matching `lifecycle_lock`. Missing, substituted or non-active journal-bound state fails before mutation.
- [ ] Provision one persistent private `locks/verification.lock` inode during fresh apply and seal it as `verification_lock` with stable directory identity plus full file identity. Active replay, verify, Core, runner and rollback are `create=false` and reuse-only, retain common/state/locks/file descriptors through flock, and never unlink on release.
- [ ] Recover only the closed journal-less prefixes `ROOT_EMPTY`, `P1`, `P2`, `P2Q`, `P3`, `P3Q`, `P4` and `P4T`. Use nonblocking post-open validation for every leaf and no-replace durable quarantine for `P2`/`P3` cleanup; reject unexpected/Core-owned state and leave every interrupted cleanup retryable.
- [ ] On allowed rollback, atomically move the target `.codex/control-plane.lock` and every managed leaf into private journal-owned `.recovery-*`/`.staging-*` durable quarantine and fsync. Revalidate the moved inode, restore only the exact-value `.codex/git-hooks` config entry, preserve any concurrent consumer value, and retain the linked quarantined inode through final proof. Reclamation is separate GC outside this task.
- [ ] Delete created directories only in reverse order and only if empty with unchanged descriptor identity. Preserve any differing record and return `E_ADOPTION_ROLLBACK_DRIFT`.
- [ ] Prove bytes, Git executable modes, local Git config, managed-directory topology, and before-snapshot digest are exact after rollback.
- [ ] Retain the safe immutable receipt plus the exact linked activation/managed quarantine in private Git-common-dir adoption evidence. Recheck every retained record before PASS. Exact rollback replay returns that receipt; a different binding fails without mutation, and separate GC remains unimplemented and non-authorizing.
- [ ] For a crash before `active`, recovery may continue exact rollback. For a crash after `active`, status is `UNKNOWN` until a separately invoked `verify` or `rollback`; never guess whether a Core task ran.

Closed command surface:

```text
scripts/control-plane-adoption status --target <target> --json
scripts/control-plane-adoption verify --target <target> --json
scripts/control-plane-adoption rollback \
  --target <target> \
  --install-digest sha256:<64-lowercase-hex> \
  --json
```

- [ ] Run transaction and recovery modules together; expect PASS and exact before/post-rollback equivalence.
- [ ] Do not add a canary, migration, repair, upgrade, or force option.

## Task 6: Build the isolated adoption bootstrap and exact tool lock

**Files:**

- Create: `adoption_enablement/lockfile.py`
- Create: `adoption_enablement/cli.py`
- Create: `scripts/control-plane-adoption`
- Create: `.codex/adoption-enablement.lock`
- Create: `tests/test_adoption_enablement_bootstrap.py`

**Requirements:** `AE-01`, `AE-07`.

- [ ] Add RED tests requiring exactly these adoption modules:

```python
ADOPTION_MODULES = (
    "__init__.py",
    "cli.py",
    "contracts.py",
    "lockfile.py",
    "manifest.py",
    "repository.py",
    "safe_io.py",
    "transaction.py",
)
```

- [ ] Add RED attacks for timestamp-valid pyc, package-directory shadowing, extra importable files, source replacement after hash, `.pth`, `sitecustomize`, hostile `BASH_ENV`/`ENV`, ambient loader variables, inherited Python modules, unsafe modes, wrong ownership, hardlinks, symlinks, and dataless files.
- [ ] Make `scripts/control-plane-adoption` a POSIX stage-0 wrapper that chooses an absolute Python 3.11+ candidate and re-execs under `env -i` with `-I -S -B -X pycache_prefix=/dev/null` while preserving stdin.
- [ ] Capture and validate every adoption source byte and `.codex/adoption-enablement.lock` before any package import. Use a verified in-memory loader; standard importlib must not reopen repository files or read `__pycache__`.
- [ ] The adoption lock binds schema/tool version, exact module order, entrypoint digest, and domain-separated runtime digest. It does not replace or amend `.codex/control-plane.lock`.
- [ ] CLI parsing imports no `control_plane` module and accepts only `preview`, `apply`, `status`, `verify`, and `rollback`. All responses contain `authorizes=false`; exceptions become bounded stable JSON without traceback.
- [ ] Assert `scripts/control-plane` cannot discover or dispatch `adoption_enablement`, and the new entrypoint cannot dispatch Core's quarantined `adopt plan/apply` or `upgrade` names.
- [ ] Run bootstrap and contract suites; expect PASS with no pycache and no ambient module load.

## Task 7: Prove the lifecycle in a harness-owned temporary repository

**Files:**

- Create: `tests/test_adoption_enablement_e2e.py`
- Modify: `tests/adoption_enablement_test_support.py`
- Modify: `tests/test_core_quarantine.py`

**Requirements:** `AE-01`, `AE-08`.

- [ ] Create the source fixture from the current committed Core subject and exact source lock; do not fetch or download anything.
- [ ] Create a new temporary non-bare, single-worktree target with committed valid project-specific policy and registry, a non-protected branch, no managed paths, no state, and absent `core.hooksPath`.
- [ ] Capture a bounded no-follow before snapshot.
- [ ] Run preview once; independently validate its closed contract and digest; prove zero mutation.
- [ ] Run apply once against only that temporary target; assert target lock was the last visible activation record and installed bytes equal the reviewed manifest.
- [ ] Run installed target `policy-check`, `registry-check`, `doctor`, and hook smoke under the closed environment.
- [ ] Execute one synthetic scoped `local_change` through the installed Core CLI with a real task, exact session, generation lease, state transitions, close, and exact lease-release receipt.
- [ ] Prove no active task, lease, verifier, or legacy state remains.
- [ ] After that proof, remove only the exact terminal task, release-receipt and empty task/lease directories created by this disposable harness. Preserve the persistent verification mutex. This is fixture teardown before rollback, not adoption behavior; product rollback must never delete Core-owned task evidence.
- [ ] Run verify, then rollback once, then compare the complete bounded after snapshot with the before snapshot.
- [ ] Assert only the safe receipt remains below the temporary target's Git common dir; no working-tree managed byte, hooks config, or created directory remains.
- [ ] Re-run exact apply/rollback replay cases and every wrong-binding matrix; expect idempotent safe receipt or stable failure with zero mutation.
- [ ] Prove existing Core `adopt plan`, `adopt apply`, `upgrade plan`, and `upgrade apply` still return exit `2`, `E_CAPABILITY_QUARANTINED`, and zero mutation.

This E2E test is implementation verification, not a canary and not consumer adoption. It must use `tempfile.TemporaryDirectory()` and may not accept an external target path.

## Task 8: Integrate the local gate, documentation, and independent review

**Files:**

- Modify: `tests/run.sh`
- Modify: `tests/test_core_governing_manifest.py`
- Modify: `tests/test_core_documentation.py`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `docs/adr/0006-control-plane-core-and-quarantine.md`
- Modify: `docs/engineering/00-canonical-index.md`
- Modify: `docs/engineering/19-control-plane-core-maintenance.md`
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Modify last: `.codex/adoption-enablement.lock`

**Requirements:** `AE-01`, `AE-09`.

- [ ] Add exact `ADOPTION_MODULES`, `ADOPTION_TESTS`, `ADOPTION_TEST_FILES`, `ADOPTION_TEST_HELPERS`, and `ADOPTION_GATE_FILES` manifests to `tests/run.sh`. Capture, compile, scan, and load their bytes under the existing verification mutex before any import.
- [ ] Preserve the exact `CORE_MODULES` tuple and keep the adoption package absent from the Core allowlist. Bind the separately authorized `task_state.py` bootstrap correction to a bumped Core prerelease and newly sealed runtime digest before final evidence.
- [ ] Extend the governing-manifest scanner to reject Advanced imports, dynamic nonliteral imports, Core-to-adoption imports, adoption-to-Core imports, package shadows, and any undeclared adoption source/test/helper.
- [ ] Document the new tool as locally implemented but unusable for consumer adoption. Keep exact statements `external_consumer_adoption=PROHIBITED`, `Autopilot OFF`, `authorizes=false`, and compatibility stubs quarantined.
- [ ] Amend ADR 0006 additively: implementation outside Core does not supersede the adoption prohibition. A later independently accepted ADR remains mandatory before even preparing one disposable canary action.
- [ ] Extend the threat model for source substitution, selected-source authority substitution, wrong-target selection, nested-repository smuggling, lifecycle-lock substitution, partial publication, journal tampering, rollback deletion, filter execution, hostile environment, lock replay, and serialized-authority confusion.
- [ ] Recompute the threat-model normalized snapshot footer only after every tracked implementation/documentation byte is final.
- [ ] Run the traceability/convergence test again. Every `AE-*` requirement must have RED evidence, GREEN evidence, rollback evidence, and no unresolved contradiction.

### Subsequent AE-09 verification-lock remediation

After the bootstrap, candidate bump, and lifecycle-barrier work, the local
AE-09 remediation added `control_plane/contracts.py`,
`control_plane/verification.py`, `tests/test_core_task_state.py`,
`tests/test_core_verification.py`, and the full-gate bootstrap/mutex in
`tests/run.sh`. It also reseals `.codex/control-plane.lock` and
`.codex/adoption-enablement.lock` and updates their governing tests and
documentation. The shared closed-journal decoder rejects a non-exact
`verification_lock` before Core verification, runner execution, or task/lease
mutation; Adoption provisions the mutex with exclusive create and retains its
common/state/locks/file descriptor chain.

This addendum is descriptive chronology only. It does not grant, replay, or transfer authority,
remains `authorizes=false`, and does not authorize a consumer, canary, commit,
or remote effect.

### Subsequent AE-09 final concurrency and quarantine remediation

After the verification-lock remediation, the final local AE-09 review closed
the absence-to-first-write lifecycle race, the exact provisioning-prefix
cleanup races, the regular-to-FIFO open gap, conditional Git-config
restoration, and unlink-after-verification rollback races. The bounded scope is
`control_plane/leases.py`, `adoption_enablement/safe_io.py`,
`adoption_enablement/repository.py`, `adoption_enablement/transaction.py`,
`tests/test_core_task_state.py`, `tests/test_core_leases.py`,
`tests/test_adoption_enablement_repository.py`,
`tests/test_adoption_enablement_transaction.py`, and
`tests/test_adoption_enablement_recovery.py`, with the existing
`verification_lock`, Core lock, Adoption lock and governing documentation
resealed afterward.

The final contract creates or reuses the lifecycle inode before the task lock,
recognizes only `ROOT_EMPTY`, `P1`, `P2`, `P2Q`, `P3`, `P3Q`, `P4` and `P4T`,
uses nonblocking post-open validation, removes only the exact-value hooks entry,
and retains activation and managed inodes in durable quarantine until a
separate GC exists. This chronology is descriptive only: it does not grant, replay, or transfer authority,
remains `authorizes=false`, and authorizes no consumer, canary, commit or remote
effect.

Run focused tests first:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest \
  tests.test_adoption_enablement_contracts \
  tests.test_adoption_enablement_repository \
  tests.test_adoption_enablement_preview \
  tests.test_adoption_enablement_transaction \
  tests.test_adoption_enablement_recovery \
  tests.test_adoption_enablement_bootstrap \
  tests.test_adoption_enablement_e2e \
  tests.test_core_governing_manifest \
  tests.test_core_quarantine \
  tests.test_core_documentation -v
```

Then run the authoritative local gate within `max_gate_runs=3`. Each consumed
run starts only after acquiring the verification mutex; repair invalidates the
earlier result without resetting the same closure-lineage counter, and the last
consumed run must be green on exact final bytes. Exhausting the budget without
that state requires Stable Pause:

```bash
bash tests/run.sh
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check
git status --short --branch
```

- [ ] Expect all commands to pass on final immutable bytes. If `E_VERIFICATION_BUSY` appears, do not run a duplicate suite; wait for the existing verifier and use its exact result.
- [ ] Obtain independent specification and security reviews with `0 Critical / 0 Important`. A self-review cannot satisfy those gates.
- [ ] Confirm no dependency, secret, `.github/workflows` file, consumer, package, plugin, remote, or release surface changed.
- [ ] Stop at `IMPLEMENTED_LOCAL / CANARY_PROHIBITED`, `self_certified=false`, `authorizes=false`. Do not prepare or execute a canary.
- [ ] Commit only if the user later authorizes that exact local checkpoint. No push, PR, merge, tag, release, or installation is implied.

## Pre-mortem and stop conditions

| Failure chain | Early signal | Mandatory stop |
|---|---|---|
| Spec Kit concepts become a second governing framework | `.specify/`, generated slash commands, duplicate constitution | Remove the duplicate surface; keep only traceability and consistency practices. |
| Adoption leaks into Core | adoption imports enter Core, the module allowlist changes, or a Core byte changes without exact separate authority and version binding | Stop and request a new structural decision. |
| Freshness is caller-supplied | boolean/flag can bypass target inventory | Reject the API; derive freshness from closed observations only. |
| Partial bytes become active | launcher/hook succeeds while target lock absent | Block release of the transaction implementation. |
| Wrong target is mutated | repo/common/worktree identity changes between preview and apply | Invalidate the plan; zero mutation. |
| Rollback deletes user data | current descriptor/digest differs from journal | Preserve the differing path and return drift. |
| Temporary E2E is called a canary | external target path or consumer repository enters test | Stop; tests may use only harness-owned temporary repositories. |
| Plan/receipt is treated as authority | any serialized `authorizes=true` or action inferred from a digest | Fail closed; require a later native authorization for each effect. |

## Rollback of the implementation task

Before any consumer or canary exists, implementation rollback is local and exact:

1. stop the adoption test process and prove no verifier holds the repository mutex;
2. remove only the new adoption package, entrypoint, adoption lock, and adoption tests created by this plan;
3. restore only the explicitly modified local test/documentation files from their reviewed pre-task bytes;
4. verify the Core runtime digest, policy, registry, Core lock, full gate, and Git diff;
5. preserve no adoption state outside harness-owned temporary directories.

Do not use `git reset --hard`, `git clean`, or broad recursive deletion. If any consumer path was touched, the plan boundary has already been violated: stop, inventory the exact effect, and request recovery authority rather than improvising cleanup.

## Completion truth and next decision

Completion of this plan's local implementation can prove only:

```text
adoption_tool=IMPLEMENTED_LOCAL
temporary_repository_e2e=PASS
external_consumer_adoption=PROHIBITED
canary=NOT_PREPARED
stable_adoption=NOT_DECIDED
authorizes=false
```

A future canary requires, in order: an independently reviewed ADR creating one exact fresh-consumer exception, a prepared non-authorizing action card bound to one disposable repository, and a new native authorization for that exact action. Stable adoption and existing-consumer migration remain later, separate decisions.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del adoption enablement de Core 3.1.
- **Para continuar:** ejecutar los focales finales, el intento disponible del gate integral dentro de `max_gate_runs=3`, los post-gates y dos revisiones independientes sobre los mismos bytes; el último intento consumido debe estar verde para cerrar AE-09 y el agotamiento exige Stable Pause.
- **Mensaje exacto:** `Cierra la evidencia final local de AE-09 en 3.1.0-core.2; no prepares ni ejecutes un canary.`
- **Estado de partida:** `origin/main@b07418364409f76c900f0595a76c9e3e388ac433`, rama `codex/control-plane-adoption-enablement-design`, candidato local `3.1.0-core.2` sin commit y adopción externa prohibida; el cierre depende solo de la evidencia final definida arriba.
- **No hacer todavía:** instalar, tocar consumidores, ejecutar canary, migrar v2.1, añadir dependencias, commit, push, PR, merge, tag, release, plugin, Autopilot o cualquier efecto remoto.
- **Autoridad:** `authorizes=false`.
