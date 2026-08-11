# Control Plane v2.3 Outcome Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task by task. Use `superpowers:test-driven-development` for every behavior change. Do not overlap writers.

**Goal:** Deliver a host-authorized, independently reviewed path in which one task keeps its final requested outcome from intake through `PR LISTA` in an observed GitHub Pull Request, with a separately mandated optional squash merge and no new mutable CLI commands.

**Architecture:** Preserve `control-plane-run` as the Codex orchestrator and the Python CLI as a deterministic local kernel. Add closed, compact review and remote-outcome receipts. Keep outcome authority exclusively as ephemeral state of the current Codex root task, derive one-shot native claims from one current mandate, and repair the lifecycle handoff so a task is not closed before its remote outcome exists. Python emits non-authorizing plans and validates receipts; it never owns or reconstructs authority. Serialize only receipts and audits, never authority.

**Tech Stack:** Python 3.11 stdlib, `unittest`, Git, `gh`, existing Codex host bridge and existing test-only host adapter. No dependency changes.

**Planning baseline:** Work in `/Users/bustaseo/.config/superpowers/worktrees/codex-engineering-control-plane/control-plane-v2-3` on `codex/control-plane-v2-3@7bd9d2a96f5c3cdd22807a5d7f810d3a6fc1d9d4`. Preserve the three existing commits. This document does not authorize a commit, push, PR, merge, release, installation, credential access, or remote sandbox mutation.

**Promotion truth:** implementación local verificada. Tasks 1–7 describen la
implementación ejecutada, pero este plan no almacena resultados dinámicos. La
única fuente vigente será un `LocalCandidateReceiptV1` validado y persistido
bajo el Git dir en
`codex-control-plane/candidates/v2-3-local-candidate.json`. Ese receipt se crea
solo después de observar suite, gates, worktree, índice y ambas revisiones; su
sujeto y snapshot de seguridad evitan la auto-referencia. Los checkmarks no
reclaman un efecto remoto ni sustituyen su observación.
The operational default gate is a private sandbox (`sandbox privado`) through
`PR LISTA`. A squash
sandbox is a separate test and only runs with a current, exact mandate «hasta
squash merge».

**Release boundary:** `product_version` and the release skill remain `2.1.1`
until a separate release promotion. This task changes neither CI nor release;
the local candidate is identified by its reproducible contract and diff digest.

---

### Phase 0: Locked decisions before behavior changes

- [x] Use one `TaskEnvelope` and one lifecycle task. Set
  `requested_outcome=local_change|commit|pull_request|integration` during
  prepare and never mutate or elevate it. Desired outcome is not authority.
- [x] Keep the current durable lifecycle. Do not add `staged` or
  `review_pending`; staging leaves the task in `review_ready`, and T2/T3 remain
  in `verifying` until all required review receipts pass.
- [x] Keep `task_allows_writer_lease()` for implementation. Add a separate
  owner-bound delivery lease allowed only in `review_ready`, with exact HEAD,
  diff, scope and generation; release it after the local commit.
- [x] Use only canonical effect IDs: `local_write`, `commit`, `remote_write`,
  `pull_request` and `integration`. Stage and commit require separate fresh
  grants derived from the host-only context.
- [x] Separate the opaque one-shot host observation —session, invocation,
  nonce and TTL— from the durable `IndependentReviewReceipt`, which excludes
  those native identifiers and always has `authorizes=false`.
- [x] Preserve the repository error convention `ValueError("E_*: ...")`; do
  not introduce `ContractValidationError` or `HostAuthorizationError`.
- [x] Keep Git reads/snapshots in `git_state.py`, but place fetch, remote-base
  refresh and merge containment behind the validated host bridge/provider.
- [x] Permit merge only when project policy says `integration_strategy=squash`;
  otherwise return `BLOCKED_UNSUPPORTED_INTEGRATION_STRATEGY`.
- [x] Include `run_workflow.py` in the lifecycle handoff so prepare accepts the
  immutable final outcome and verify does not publish `review_ready` early.
- [x] `frame_effect_authorization` recibe `NativeUserInteractionEvent` y
  `HostAdapterCapability`; nunca los reconstruye desde JSON. Python bridge
  recibe y consume la autorización nativa solo para Git local allowlisted
  (`git add`; commit con `git commit-tree` y `git update-ref` CAS). El kernel
  puede observar con `git ls-remote`
  read-only; push/PR/squash merge son host-native.
- [x] Derive a single host-only root grant-set from the current request. Its
  one-shot claims follow the expected lineage `review_head → committed_head →
  merge_sha`, so the intentional commit HEAD change does not cause a reprompt.
- [x] Let `run prepare` accept `local_change|commit|pull_request|integration`
  with future effects deferred in `approval_boundaries`; preparation does not
  authorize them. Reject `answer|release` for this workflow.
- [x] Materialize one bounded `0600` stable-diff artifact under the Git dir for
  the reviewer; bind and delete it by task/attempt/HEAD/base/paths/digest.
- [x] Add a local-review repair transition `verifying → implementing` instead
  of misusing the PR-only `start_revision()` path.
- [x] Make delivery commit crash-consistent with a durable
  `finalizing_delivery_commit` marker and idempotent recovery phases.
- [x] Keep existing `RunPlanV1.session_id` as an inert local correlator. Only
  native authority identifiers are forbidden from new durable contracts.
- [x] Never ask the user to paste class names, session IDs, nonces or mint
  messages. A native sandbox, not Python fakes, is the promotion proof.

M0 changes only this spec and plan. The two files remain untracked until a
later explicit commit authorization; the dirty preflight is recorded and is
not represented as `PASS`.

---

### Task 1: Close the compact review-packet and review-observation contracts

**Files:**

- Modify: `control_plane/run_workflow.py`, `control_plane/lifecycle.py`
- Modify: `tests/test_run_workflow.py`, `tests/test_lifecycle.py`
- Create: `tests/test_independent_review.py`

- [x] **Step 1: Write the failing serializable-contract tests.** In `tests/test_independent_review.py`, construct a valid minimal `ReviewPacketV1` and assert it round-trips only its closed schema. Assert rejection of an unknown key, prompt/transcript field, over-budget aggregate, over-budget path, gate receipt with no argv digest, and a packet whose path digest differs from its stable-diff digest.

  ```python
  with self.assertRaisesRegex(ValueError, "^E_RUN_REVIEW"):
      ReviewPacketV1.from_dict({**packet.to_dict(), "native_authorization": "x"})
  with self.assertRaisesRegex(ValueError, "^E_RUN_REVIEW"):
      ReviewPacketV1.from_dict({**packet.to_dict(), "review_paths": ["a" * 4097]})
  ```

- [x] **Step 2: Run the new test and confirm red.**

  ```bash
  python3 -m unittest tests.test_independent_review -v
  ```

  Expected before implementation: import or contract assertions fail because `ReviewPacketV1` is absent.

- [x] **Step 3: Implement `ReviewPacketV1` in `control_plane/run_workflow.py`.** Add a versioned frozen contract with the fields from the approved design: task/run-plan digests, attempt, repository/base/branch/HEAD, stable-diff digest, criteria digest, review kind, bounded paths, gate summaries and test summaries. Use a deterministic canonical JSON digest helper already used by run contracts; `to_dict()` must include `authorizes: false`. Enforce 4 KiB total and explicit limits per variable field before serialization. Do not include a diff body or user conversation.

- [x] **Step 3a: Add the stable-diff artifact.** Write RED tests for a single `0600` artifact under the worktree Git dir with a closed manifest binding task, attempt, repo, base, HEAD, paths, digest and size. Reject symlink, oversize, path escape and any drift. Implement creation during verify, bounded read by the independent reviewer and deletion after receipt publication or attempt invalidation. The packet carries only artifact ID/digest; the artifact never authorizes an effect.

- [x] **Step 4: Write the failing receipt tests.** In `tests/test_independent_review.py`, replace the current expectations that typed publishers are unavailable. Model only the durable result of a review already observed by the root host: it excludes thread/session/invocation/nonce/TTL, round-trips with `authorizes=false`, and fails lifecycle binding when replayed against another task, attempt, base, HEAD, diff or criteria digest. Unit tests must not claim to prove the native reviewer observation.

- [x] **Step 5: Implement the split review boundary.** `control-plane-run` creates the reviewer task and validates its result using native thread identity/cursor in root memory. It then publishes a closed durable `IndependentReviewReceipt` through `run_workflow.py`; the receipt carries reviewer/observation and review/binding digests, status, observed time and `authorizes=false`. Python consumes only an opaque host-bound proof and never claims scalar or durable input observed the native task. Missing/expired/replayed proof, `UNKNOWN` or mismatched findings block; production without the native adapter fails closed.

- [x] **Step 6: Make lifecycle promotion and local repair consume review evidence.** In `control_plane/lifecycle.py` and `control_plane/run_workflow.py`, keep T2/T3 in `verifying` while the packet and receipts are pending. Require a matching independent review for T2 and a matching security review with a distinct host observation when T3 requires it before the single transition to `review_ready`. Add a dedicated host-observed `verifying → implementing` local-review repair transition for `Critical|Important`: increment attempt, invalidate prior gates/reviews/artifact, acquire a new implementation lease and require a new HEAD. Do not call the existing PR-only `start_revision()` path or overwrite an older receipt.

- [x] **Step 7: Run the focused green suite.**

  ```bash
  python3 -m unittest tests.test_independent_review tests.test_run_workflow tests.test_lifecycle -v
  ```

- [x] **Step 8: Authorization boundary.** Inspect the diff and stop at a clean local test result. Do not stage or commit without a new explicit authorization.

  ```bash
  git diff --check
  git status --short --branch
  ```

### Task 2: Separate deferred routing from host-native outcome authority

**Files:**

- Modify: `control_plane/routing.py`, `control_plane/run_workflow.py`
- Modify: `skills/control-plane-run/SKILL.md`
- Modify: `tests/test_routing.py`, `tests/test_run_workflow.py`, `tests/test_control_plane_run_skill.py`
- Create: `tests/test_outcome_binding.py`

- [x] **Step 1: Write RED preparation tests.** Assert `run prepare` accepts an immutable `pull_request` or `integration` outcome when remote effects remain deferred in `approval_boundaries`; preparation must not mark them authorized. Assert material ambiguity still blocks and `answer|release` are rejected for this workflow.

- [x] **Step 2: Implement deferred-effect preparation.** Add closed `deferred_effects` to `RunPlanV1`. Change routing/run preparation so structural readiness, scope, gates and clarification are evaluated separately from authority for later effects. `prepare` may write lifecycle metadata under the Git dir but cannot execute product or remote effects. Never clear or reinterpret the original `approval_boundaries`.

- [x] **Step 3: Write RED lineage tests.** Add serializable, explicitly non-authorizing `OutcomeBindingV1` for deterministic validation of `review_head`, reviewed tree/diff, `committed_head`, pushed head, PR/check digest and `merge_sha`. Assert only a commit whose parent/tree match the reviewed state can advance by CAS; push/PR/merge drift, reordered effects, duplicate claims and unsupported effect IDs fail with `ValueError("E_*: ...")`.

- [x] **Step 4: Define the native root protocol in the skill.** `OutcomeAuthorizationContext` remains ephemeral state of the current root task and is never exported from Python. From one current native request, the root creates one-shot claims for canonical effects and advances only through `review_head → committed_head → merge_sha`. It invokes Git/`gh` through native host tools, then passes observations—not authority—to the kernel for binding checks. Existing test adapters remain test-only and production fail-closed; no fake adapter may satisfy the native sandbox gate.

- [x] **Step 5: Add RED no-reprompt/UX contract tests.** Assert a stable authorized chain never asks the user for `NativeUserInteractionEvent`, `HostAdapterCapability`, `TrustedAuthorization`, nonce, session ID or an exact mint message. A changed lineage, scope expansion or new effect produces one concise product-level reauthorization request; a host-tool failure produces a host recovery diagnostic and does not consume a repair attempt.

- [x] **Step 6: Run focused regressions.**

  ```bash
  python3 -m unittest tests.test_outcome_binding tests.test_routing tests.test_run_workflow tests.test_control_plane_run_skill tests.test_local_audit_contract -v
  ```

- [x] **Step 7: Authorization boundary.** Unit tests prove only non-authorizing bindings and UX contracts. Actual authority continuity requires the separate native sandbox in Task 8. Retain local changes and do not commit, push, create a PR or merge.

### Task 3: Repair lifecycle, lease and Git handoff before remote effects

**Files:**

- Modify: `control_plane/lifecycle.py`, `control_plane/host_bridge.py`, `control_plane/git_state.py`, `control_plane/run_workflow.py`
- Modify: `tests/test_lifecycle.py`, `tests/test_git_guards.py`
- Reuse: `tests/git_test_support.py`, `tests/host_adapter_test_support.py`

- [x] **Step 1: Write a single end-to-end failing composition test.** In `tests/test_lifecycle.py`, prepare a task whose immutable outcome is `pull_request`; keep it in `verifying` until a matching review receipt promotes it to `review_ready`. With no implementation lease active, acquire a fresh delivery lease. Simulate the root host executing separate native `local_write` and `commit` effects, then validate the resulting index/commit observations and release the lease. Advance the non-authorizing binding to `committed_head` without closing the task. Assert the current implementation fails at the first unsupported precondition.

- [x] **Step 2: Run the composition test and confirm red.**

  ```bash
  python3 -m unittest tests.test_lifecycle.HostBridgeLifecycleCompositionTests.test_review_ready_can_reach_remote_context_after_local_commit -v
  ```

- [x] **Step 3: Change only the state preconditions necessary for the handoff.** In `control_plane/run_workflow.py`, preserve the task's immutable final outcome. In `control_plane/lifecycle.py`, leave `task_allows_writer_lease()` unchanged and add a separate delivery-lease predicate/acquisition allowed only at matching `review_ready`. A validated staged-index observation leaves state at `review_ready`; a validated commit observation transitions directly to `committed` and releases the delivery lease owner-bound and idempotently. Do not allow delivery from `planned`, `implementing`, `verifying`, stale `review_ready`, or a task with an implementation lease.

- [x] **Step 3a: Make staging and delivery commit crash-consistent.** Write `finalizing_delivery_commit.prepared` durably before `git add`, binding snapshot, allowlist, expected index, parent, tree and message digest. Build the commit object with `git commit-tree` from that exact tree and sole parent, then move only the bound branch with `git update-ref <new> <expected-old>` CAS; recheck parent, tree, HEAD and index after the CAS. Add fault-injection tests after marker creation, immediately after staging, after the native commit effect, after lifecycle publication and before lease release. Implement marker phases `prepared → index_observed → git_committed → state_committed → lease_released`; `index_observed` is recovery metadata, not a lifecycle state. Recovery may finalize only when index, parent, tree, message digest and observed SHA match; otherwise it blocks without repeating stage/commit or using destructive reset.

- [x] **Step 4: Add red guard tests for local integrity.** Cover dirty worktree, unrelated staged path, untracked file in scope, changed file after review, release by a different owner, missing required gate, foreign change, HEAD/base divergence and a remote result of `UNKNOWN`. Each must reject the next transition or leave the task `BLOCKED`; no test may assert `PASS` from absence of evidence.

- [x] **Step 5: Implement the smallest guards.** Reuse the existing read-only allowlist/snapshot code in `control_plane/git_state.py` and the verified diff/untracked handling in `run_workflow.py`; ensure untracked files participate in snapshot/diff comparison and required-gate receipts match HEAD, diff, profile and argv digest. Keep all subprocess invocation as argv arrays. Do not add fetch or another remote mutation to `git_state.py`.

- [x] **Step 6: Run the lifecycle/Git suite.**

  ```bash
  python3 -m unittest tests.test_lifecycle tests.test_git_guards tests.test_local_audit_contract -v
  ```

- [x] **Step 7: Authorization boundary.** Verify no base branch mutation occurred and await separate authorization before any commit.

  ```bash
  git status --short --branch
  git diff --check
  ```

### Task 4: Implement push and draft-PR bridge with observe-before-retry

**Files:**

- Modify: `control_plane/host_bridge.py`, `control_plane/lifecycle.py`
- Modify: `tests/test_lifecycle.py`, `tests/test_git_guards.py`
- Create: `tests/test_git_outcome_bridge.py`

- [x] **Step 1: Write red tests for normal push.** In `tests/test_git_outcome_bridge.py`, use a disposable repository to assert a closed, non-authorizing `remote_write` effect plan permits only a normal refspec. Simulate the root host execution and require an exact remote observation before `pushed`. Assert `--force`, `--force-with-lease`, base ref targets and any ref outside the bound feature branch are rejected before a host tool is invoked.

- [x] **Step 2: Write red uncertain-write tests.** Simulate a provider timeout after send. Assert the bridge performs a read-only observation for the same remote/ref/HEAD before any retry; `PASS` resolves from observation, `FAIL` allows only the bounded repair path, and `UNKNOWN` ends in `BLOCKED` with no second write.

- [x] **Step 3: Run the focused red test file.**

  ```bash
  python3 -m unittest tests.test_git_outcome_bridge -v
  ```

- [x] **Step 4: Implement the push handoff without a Python authority adapter.** The kernel emits a closed `OutcomeEffectPlanV1(authorizes=false)` for canonical `remote_write`. The root skill revalidates its native context, invokes the exact Git argv through the host tool and observes the remote result natively. It then publishes a closed `RemoteOutcomeReceiptV1(authorizes=false)`; Python validates only its schema/bindings and lifecycle reaches `pushed` only when repo/ref/SHA match. Python exposes no mutable CLI, production authority constructor or claim of native provenance.

- [x] **Step 5: Add red tests for draft PR creation.** Assert the provider accepts a sanitized, bounded title/body and creates or observes only the bound repository, head branch, base and SHA. Assert duplicate creation is detected via read-before-write and returns the observed matching draft PR. Mismatched PR, base, head or SHA is `FAIL`; unavailable observation is `UNKNOWN`/ `BLOCKED`.

- [x] **Step 6: Implement the typed PR draft handoff and observation.** The kernel emits a closed `pull_request` effect plan; the root consumes its separate native claim, invokes exact `gh` argv and observes the PR through host tools. It publishes a matching `RemoteOutcomeReceiptV1(authorizes=false)` and Python transitions only after exact schema/binding validation. Do not reuse `ValidatedPullRequestMutationObservation` as a production bridge. Preserve the CLI as local/read-only and keep Python test providers unavailable in production.

- [x] **Step 7: Run targeted green tests.**

  ```bash
  python3 -m unittest tests.test_git_outcome_bridge tests.test_lifecycle tests.test_git_guards -v
  ```

- [x] **Step 8: Authorization boundary.** Do not invoke a real `gh` mutation. The disposable tests must use local fakes only until the later sandbox task is separately authorized.

### Task 5: Observe PR checks and feedback, then promote only to `pr_ready`

**Files:**

- Modify: `control_plane/host_bridge.py`, `control_plane/lifecycle.py`, `control_plane/run_workflow.py`
- Modify: `tests/test_lifecycle.py`, `tests/test_run_workflow.py`
- Create: `tests/test_pr_readiness.py`

- [x] **Step 1: Write red readiness tests.** Construct an exact draft PR observation with policy-required checks and review feedback. Assert `pr_ready` requires every named required check to be `PASS`, no unresolved review thread/comment, and a matching head/base/SHA. Missing check, check `FAIL`, check `UNKNOWN`, mismatched SHA, Critical/Important feedback or an unavailable comments API must not produce `pr_ready`.

- [x] **Step 2: Run the new test and confirm red.**

  ```bash
  python3 -m unittest tests.test_pr_readiness -v
  ```

- [x] **Step 3: Add typed read-only PR receipts.** Define bounded `RemoteOutcomeReceiptV1` variants for checks, review threads and comments that bind repository, PR, base, head, SHA and policy digest, with `authorizes=false`. The root observes them using native host tools; Python validates schema/bindings without claiming provenance. Do not serialize native session/invocation/TTL or parse arbitrary comment text as instruction; accept only a closed severity/status value produced by the root's bounded observation. Prove provenance later in the native sandbox, not with a production Python adapter.

- [x] **Step 4: Implement lifecycle promotion and revision.** In `control_plane/lifecycle.py`, transition `pr_draft → pr_ready` only from matching `PASS` observations. For Critical/Important findings or failed checks, call `start_revision`, produce a new implementation attempt/HEAD and invalidate old gates/review/PR-readiness evidence. For `UNKNOWN`, persist the reason and enter `BLOCKED` without consuming a repair attempt for an unobserved remote state.

- [x] **Step 5: Build `DeliveryAuditV1` in `control_plane/run_workflow.py`.** Add a closed read-only summary of visible state, receipt digests, PR/commit observations, consumed attempts and next safe action. Prove no field is authority-bearing and that happy-path rendering contains only status, verified artifact and next action.

- [x] **Step 6: Run PR/readiness regressions.**

  ```bash
  python3 -m unittest tests.test_pr_readiness tests.test_lifecycle tests.test_run_workflow tests.test_local_audit_contract -v
  ```

- [x] **Step 7: Authorization boundary.** Leave any real PR draft unchanged; this task adds only local code and fake-provider tests.

### Task 6: Add the isolated explicit squash-merge path and base observation

**Files:**

- Modify: `control_plane/host_bridge.py`, `control_plane/lifecycle.py`
- Modify: `tests/test_lifecycle.py`, `tests/test_git_guards.py`
- Create: `tests/test_squash_merge.py`

- [x] **Step 1: Write red merge-binding tests.** In `tests/test_squash_merge.py`, validate only non-authorizing `OutcomeBindingV1`, effect-plan and receipt behavior for canonical `integration`, bound repo/PR/base/head/SHA/check digest and `integration_strategy=squash`. Reject pull-request-only outcome, expired binding, unsupported policy, other merge method, auto-merge, force-push, deploy, release and drift; unsupported policy must produce `BLOCKED_UNSUPPORTED_INTEGRATION_STRATEGY`. Do not construct or simulate a native authorization context in Python; the explicit «hasta squash merge» mandate and real claim consumption are proven only in Task 8.

- [x] **Step 2: Write red post-merge observation tests.** Simulate a valid provider response but an `origin/<base>` that does not contain the observed merge SHA. Assert the task stays unclosed and `BLOCKED`. Add the passing case where fetch/containment confirms the SHA and only then reaches `base_verified`.

- [x] **Step 3: Run the focused test and confirm red.**

  ```bash
  python3 -m unittest tests.test_squash_merge -v
  ```

- [x] **Step 4: Implement a closed squash-only host handoff.** The kernel emits only canonical `integration` with strategy `squash`; the root consumes its native claim and invokes exact `gh` argv through the host tool. Both the `READY` pre-read and the post-effect `PASS` require fresh one-shot `ValidatedGitHubObservation` objects bound to the exact plan, PR, checks and provider result; scalar receipts cannot arm or publish. No plan or skill path may represent another merge method or auto-merge. A missing/failed host tool is `BLOCKED` and never falls back to a Python test provider.

- [x] **Step 5: Implement base verification through the host boundary.** Keep `git_state.py` read-only. The root host invokes a closed fetch for only the bound `origin/<base>`; `git_state.py` then reads the refreshed ref and `host_bridge.py` validates merge metadata/containment. In `control_plane/lifecycle.py`, permit `merged → base_verified → close` only after this evidence; retain a clear recovery record if it fails or is unknown.

- [x] **Step 6: Run merge and full local bridge tests.**

  ```bash
  python3 -m unittest tests.test_squash_merge tests.test_git_outcome_bridge tests.test_pr_readiness tests.test_lifecycle tests.test_git_guards -v
  ```

- [x] **Step 7: Authorization boundary.** Do not call the merge provider against GitHub. A private sandbox exercise is a separate task with fresh native authority.

### Task 7: Update the skill, policy-facing documentation and recovery material

**Files:**

- Create: `docs/adr/0005-host-bound-outcome-authorization.md`
- Create: `docs/security/2026-08-08-v2-3-outcome-bridge-threat-model.md`
- Create: `docs/engineering/16-outcome-bridge-rollback.md`
- Modify: `skills/control-plane-run/SKILL.md`, `docs/engineering/02-git-pr-merge.md`, `docs/engineering/06-recovery.md`, `docs/engineering/11-lifecycle-hooks-adoption.md`, `SECURITY.md`, `README.md`
- Modify: `tests/test_control_plane_run_skill.py`, `tests/test_repository_contract.py`

- [x] **Step 1: Write red skill/documentation contract tests.** Assert the skill names the exact user-visible states; differentiates review evidence from authority; forbids mutable CLI commands; requires observe-before-retry; treats `UNKNOWN` as blocked; names PR-ready as default; and states squash merge needs a fresh exact «hasta squash merge» mandate. Add repository tests for the ADR, threat model and rollback document links.

- [x] **Step 2: Run documentation contract tests and confirm red.**

  ```bash
  python3 -m unittest tests.test_control_plane_run_skill tests.test_repository_contract -v
  ```

- [x] **Step 3: Write the ADR and threat model.** Record why host-bound per-effect authority was chosen over serializable grants, a second agent and mutable CLI. Map assets, trust boundaries, replay/drift/stale-review/uncertain-write threats, mitigations and residual limits. State explicitly that local guards are not GitHub branch protection.

- [x] **Step 4: Write the rollback runbook.** Cover recovery before commit, after local commit, after push/PR, uncertain writes and post-merge verification. It must prohibit `reset --hard`, force-push, automatic PR closure and automatic remote rollback.

- [x] **Step 5: Update the user-facing skill and engineering guides.** Keep the skill intake compact; state the 4 KiB review-packet cap, exact receipt-reuse condition, retry ceiling and concise happy-path delivery. Retain current CLI commands only. Do not describe the remote path as available when no native host adapter is installed.

- [x] **Step 5a: Remove authorization plumbing from the user protocol.** The skill may ask only for product intent or a genuinely new effect. It must never tell the user to enable a bridge manually, mint/reemit a grant, repeat session/HEAD/scope bindings, or paste a host class name. Stable in-scope transitions continue automatically; genuine drift produces one concise reauthorization request at the product level.

- [x] **Step 6: Run the green documentation suite.**

  ```bash
  python3 -m unittest tests.test_control_plane_run_skill tests.test_repository_contract -v
  ```

- [x] **Step 7: Authorization boundary.** Review documentation claims against actual tests. Do not mark a release, install a plugin or make a GitHub change.

### Task 8: Verify locally, independently review, then gate promotion at sandbox `PR LISTA`

**Files:**

- Modify only if tests demonstrate a specific defect: files named by Tasks 1–7
- Reuse: `tests/git_test_support.py`, `tests/host_adapter_test_support.py`, `tests/test_local_audit_contract.py`

- [ ] **Step 1: Run the complete local verification set against the final subject.**

  ```bash
  bash tests/run.sh
  scripts/control-plane policy-check --policy .codex/project-policy.toml
  scripts/control-plane registry-check --registry .codex/resource-registry.toml --policy .codex/project-policy.toml
  scripts/control-plane doctor
  git diff --check
  git status --short --branch
  ```

- [ ] **Step 2: Run both fresh reviews against the final subject.** Give each reviewer only task criteria, `ReviewPacketV1`, stable diff read capability, test/gate receipts and no authority. This step is declarative and does not assert current evidence. Fix every Critical/Important finding with a new red-green test sequence; rerun the affected gates and create new packets after every changed subject.

- [ ] **Step 3: Build and persist `LocalCandidateReceiptV1`.** Bind the exact
  repository, branch, `HEAD`, product/runtime versions, worktree subject,
  security snapshot, index digest/emptiness, tracked and untracked counts,
  suite command/count/status, exact gates, both review result digests/status
  and sandbox state. Validate the closed 8 KiB contract and store it only at
  `codex-control-plane/candidates/v2-3-local-candidate.json` under the worktree
  Git dir. The receipt is the single dynamic promotion truth and always has
  `authorizes=false`. New publication retains exactly one reserved internal
  pending name as a hardlink to the canonical inode (`nlink=2`); the canonical
  remains the only public locator. The pending suffix is deterministically the
  receipt's complete 64 lowercase hex digest without `sha256:`; inventory,
  load, replay and recovery require exact name/content agreement. Accept
  canonical-only `nlink=1` as legacy, but never auto-unlink a candidate
  pathname. Exact orphan recovery links and retains the pair; partial,
  replaced, malformed or mismatched pending state is preserved and blocks.

- [ ] **Step 4: Verify no capability leak.** Search contracts, fixtures, logs and docs for fields that could serialize a native mandate, session/invocation secret, grant or credential. Fail the task if a new authority-bearing serializable field exists.

  ```bash
  rg -n -i 'outcomeauthorization.*(to_dict|from_dict|json|pickle)|trustedauthorization.*(to_dict|from_dict|json|pickle)' control_plane tests docs skills
  ```

- [x] **Step 4a: Prepare, but do not execute, the native sandbox packets.** Record two valid non-authorizing task envelopes plus a fail-closed binding record and runbook: the operational default packet goes through `PR LISTA`; the integration packet is a separate explicit «hasta squash merge» test. Each root must use real Codex task and shell tools, never the Python test adapter; it must complete its stable chain with zero internal-object prompts and prove `review_head → committed_head`, plus `merge_sha ∈ origin/<base>` only for integration. See `docs/engineering/17-v2-3-native-sandbox-promotion.md`.

- [ ] **Step 4b: Bind the separately authorized sandbox target.** The private sandbox through `PR LISTA` is the required operational gate, but it needs a current native mandate for that exact scope before binding or execution. The squash packet is separate: su mutación no se exige ni ejecuta salvo que el mandato actual sea exactamente «hasta squash merge». This implementation task cannot select the target, issue authority or execute either packet.

- [x] **Step 5: Stop before sandbox Git transitions.** Integration is implemented and verified locally, but this plan is not an authorization object. A future current native mandate may cover the `PR LISTA` chain and derive a fresh one-shot claim for each ordinary effect. Only drift, scope growth or a genuinely new effect requires another human request. Sandbox mutation, merge, tag and release remain unexecuted here.

## Verification matrix

| Concern | Primary tests | Required invariant |
| --- | --- | --- |
| Closed review input | `test_independent_review`, `test_run_workflow` | bounded packet, no prompt/log/authority |
| Review evidence | `test_independent_review`, `test_lifecycle` | exact binding, fresh, one-shot, `UNKNOWN` blocks |
| Outcome authority | `test_lifecycle`, `test_git_outcome_bridge` | host-only, nonserializable, per-effect, replay rejected |
| Candidate truth | `test_candidate_receipt`, `test_repository_contract` | closed 8 KiB receipt, exact binding, durable canonical/pending pair without pathname cleanup |
| Authorization UX | `test_git_outcome_bridge`, `test_control_plane_run_skill` | one native mandate, no internal-object prompt or stable-chain reprompt |
| Local handoff | `test_lifecycle`, `test_git_guards` | separate delivery lease; no staged state or premature close |
| Remote uncertainty | `test_git_outcome_bridge` | observe before retry; unknown blocks |
| PR readiness | `test_pr_readiness` | checks/comments/reviews exactly match head/base |
| Integration | `test_squash_merge`, `test_lifecycle` | explicit squash and observed base containment |
| User protocol | `test_control_plane_run_skill`, `test_repository_contract` | concise, correct, non-authorizing guidance |

## Continuación

- Escribe en: este hilo.
- Rol: ejecutora.
- Para continuar: observar el sujeto final, ejecutar verificaciones y revisiones, y crear el único `LocalCandidateReceiptV1` en la ruta definida antes de considerar el sandbox.
- Mensaje exacto: `Valida y persiste LocalCandidateReceiptV1 para el sujeto final de v2.3; no ejecutes todavía ningún efecto GitHub.`
- Estado de partida: el schema y la ruta
  `codex-control-plane/candidates/v2-3-local-candidate.json` están definidos;
  el worktree conserva cambios locales sin confirmar, no existe una promoción
  remota observada y los valores dinámicos deben medirse al construir el
  receipt, nunca copiarse desde este plan. `product_version` y release skill
  permanecen en `2.1.1` hasta una promoción separada.
- No hacer todavía: seleccionar o crear el sandbox sin mandato actual separado, completar bindings con valores inventados, ejecutar squash sin el mandato exacto «hasta squash merge», commit, push, PR, merge, tag o release.
