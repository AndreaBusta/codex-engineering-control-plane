# Control Plane adoption readiness v1 implementation plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

Status: `AUDIT_ONLY / DEFERRED_TARGET_BOOTSTRAP`.

**Goal:** Produce and verify a minimal source-owned new-project audit kit without
mutating or adopting a consumer.

**Architecture:** Four templates define candidate governance; one compact
runbook binds a clean detached source and documents five read-only commands;
existing documentation tests execute the same source launcher against a
harness-owned customized target.

**Constraints:** No runtime, installer, copier, Git transaction engine, hook,
lock, state, CI, dependency, secret, target write, or remote effect.

## Recut decision

The earlier exhaustive direction is intentionally not continued. A generalized
branch/copy/commit engine would make this source front responsible for unknown
consumer topology. The minimal recut keeps only evidence required to decide a
later target-specific bootstrap. A reusable mutator ADR is deferred until
repeated real-project evidence justifies it.

## Unit 1 — Freeze the contract with RED tests

**Files:** `tests/test_core_documentation.py`

- Assert the exact four-file pack and minimum schema-valid policy/registry.
- Assert restrictive non-authorizing instructions and documentation boundaries.
- Build a target fixture that preserves its README and receives only three
  already-customized authority files.
- Run the exact five source commands and prove materialization/permission stops,
  one-wrapper source/target/TaskEnvelope revalidation, local object-store and
  ancestor containment, exact clean indexes/status, explicit submodule rejection,
  narrow audit/research routing, and zero target mutation.

## Unit 2 — Add the source pack

**Files:** `templates/new-project/AGENTS.md`, `templates/new-project/README.md`,
`templates/new-project/.codex/project-policy.toml`,
`templates/new-project/.codex/resource-registry.toml`

- Keep placeholders explicit and generic bytes source-only.
- Include only resources and builtin gates justified by policy.
- Drive the Unit 1 tests from RED to GREEN.

## Unit 3 — Publish the audit-only operator contract

**Files:** `docs/engineering/23-new-project-audit-bootstrap.md`, this design,
this plan, `README.md`, `docs/engineering/00-canonical-index.md`

- Document exact-tree SHA/clean/detached binding for fully materialized,
  same-owner, non-writable local roots; external customization; exact reviewed
  TaskEnvelope bytes; local object stores; clean source/target state; no
  submodules; checks around every launcher command; stop conditions; and
  deferred target transition.
- Add concise discoverability links; do not add an executable mutator.

## Unit 4 — Threat bind and verify final bytes

**Files:** `docs/security/2026-08-12-control-plane-core-threat-model.md`,
`tests/test_core_documentation.py`

- Add only the uncustomized-governance/input-redirection attacker story,
  mitigation, and residual.
- Run the focused class and full documentation module.
- Recompute the snapshot footer, rerun the documentation module, then run the
  repository's final gates and independent reviews on frozen bytes.

## Deferred continuation

After this source work is integrated, identify and inspect the actual next
project. Prepare final authority bytes outside it and request the concrete
`DEFERRED_TARGET_BOOTSTRAP` decision. Do not infer that authority from this plan;
it remains `authorizes=false`.
