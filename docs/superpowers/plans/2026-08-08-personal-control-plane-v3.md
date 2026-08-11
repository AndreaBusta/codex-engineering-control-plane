# Personal Control Plane v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the smallest trustworthy request-to-review-ready local workflow first, then promote remote Git and plugin surfaces only after their evidence gates pass.

**Architecture:** `control-plane-run` remains the Codex-facing orchestrator. A focused Python module coordinates the existing router, preflight, `TaskStore`, `TaskLease`, closed verification commands, and compact receipts; it never edits product code or acts as an agent. Existing host-bound Git APIs remain the only mutation boundary and stay unavailable without native authority.

**Tech Stack:** Python 3.11 stdlib, Git, `gh`, Codex skills, `unittest`.

---

### Task 1: Materialized runtime gate

**Files:**
- Create: `control_plane/materialization.py`
- Modify: `control_plane/cli.py`, `scripts/control-plane`, `control_plane/adoption.py`
- Test: `tests/test_materialization.py`, `tests/test_lockfile.py`

- [ ] Add a failing test proving tracked files carrying the macOS `dataless` flag are reported without reading their contents.
- [ ] Add a failing launcher contract test proving runtime placeholders are rejected before hashing/import.
- [ ] Implement a bounded `git ls-files` inventory and `st_flags & 0x40000000` check; non-macOS files without `st_flags` are materialized by definition.
- [ ] Add the same pre-read guard to the source launcher and expose the result through `doctor`.
- [ ] Run the targeted tests and then `tests.test_lockfile`.

### Task 2: Closed run contracts and local state

**Files:**
- Create: `control_plane/run_workflow.py`
- Test: `tests/test_run_workflow.py`

- [ ] Write failing tests for `RunPlanV1`, `GateReceiptV1`, `ReviewResultV1`, and `RunSummaryV1` closed schemas, digests, replay rejection, three-total-execution limit, and `UNKNOWN` precedence.
- [ ] Implement contract builders and validators with no prompt text, command output, token, or authority fields.
- [ ] Store plans and attempt receipts atomically under `<worktree-git-dir>/codex-control-plane/runs/<task-id>/`.
- [ ] Implement `prepare_run`: validate the TaskEnvelope and route, require low/autonomous clarification, require clean write preflight, start the existing lifecycle, acquire one writer lease, and reach `implementing`.
- [ ] Implement `block_run` and retry: first and second failed attempts remain repairable; the third is terminal `BLOCKED`; repeated failure cause, scope growth, HEAD drift, foreign work, or `UNKNOWN` blocks immediately.
- [ ] Run the new test module after every red-green cycle.

### Task 3: Verification, CLI, and report

**Files:**
- Modify: `control_plane/run_workflow.py`, `control_plane/cli.py`
- Create: `control_plane/reporting.py`
- Test: `tests/test_run_workflow.py`, `tests/test_cli_run.py`, `tests/test_reporting.py`, `tests/test_local_audit_contract.py`

- [ ] Write failing CLI tests for `run prepare|verify|status|block` and `report --since 30d --format markdown`.
- [ ] Implement a closed Control Plane profile: unittest discovery, policy check, registry check, doctor, and `git diff --check`, using argv arrays, sanitized environment, bounded output, timeout, and before/after worktree snapshots.
- [ ] On success, publish only receipt digests and advance the existing task to `review_ready`; on failure, persist a bounded reason and apply retry policy.
- [ ] Map durable states to `PLANIFICANDO`, `TRABAJANDO`, `VERIFICANDO`, `PR LISTA`, and `BLOCKED` without mapping `UNKNOWN` to success.
- [ ] Aggregate local run summaries by observed timestamp; do not add cloud telemetry.
- [ ] Run targeted CLI/report tests and the local surface contract.

### Task 4: Codex skill and documentation

**Files:**
- Create: `skills/control-plane-run/SKILL.md`
- Modify: `README.md`, `docs/engineering/11-lifecycle-hooks-adoption.md`, `docs/releases/v2.1.1.md`
- Test: `tests/test_control_plane_run_skill.py`, `tests/test_repository_contract.py`

- [ ] Write failing contract tests for exact trigger, TaskEnvelope-first intake, P0/preflight, maximum two retries, T2/T3 independent review, stop conditions, and non-authorizing Git handoff.
- [ ] Implement the skill so Codex performs engineering while the CLI only frames state and evidence.
- [ ] Correct v2.1.1 publication history and document the new local vertical slice without claiming a v2.2 release.
- [ ] Run skill and repository contract tests.

### Task 5: Integration gate and deferred promotions

**Files:**
- Modify: `.codex/control-plane.lock`
- Test: complete suite

- [ ] Refresh only the entrypoint/runtime digests affected by the implemented files and prove `validate_lock` passes.
- [ ] Run the full repository verification commands from `AGENTS.md` and a disposable-repository prepare/verify/rollback smoke.
- [ ] Perform independent review of the stable diff; fix all Critical and Important findings and rerun gates.
- [ ] Leave GitHub writes, repository privatization, version/tag/release, plugin packaging, and skill deduplication unexecuted until their separate authority and dogfood gates exist.
