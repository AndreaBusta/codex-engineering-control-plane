# Control Plane adoption readiness v1 implementation plan

Status: `AUDIT_ONLY / DEFERRED_TARGET_BOOTSTRAP`.

## Goal

Ship a minimal source-owned audit kit for the next project without mutating or
adopting a consumer.

## Frozen budgets

- exactly four files under `templates/new-project/`;
- runbook: 100–150 lines;
- test delta relative to base: 300–500 lines;
- `max_repair_rounds=2`;
- `surface_growth_limit=20%`;
- no embedded verifier;
- no runtime, installer, copier, transactional Git automation, hook, lock,
  state, dependency, CI, secret, consumer write, or remote effect.

The supported v1 environment and every out-of-contract state use
`UNSUPPORTED / STOP`. All artifacts remain `authorizes=false`.

## Task 0 — Preserve and RED

**Files:** `tests/test_core_documentation.py`

1. Preserve the exhaustive prototype and current hardened candidate on local
   refs before reducing anything.
2. Make the readiness test class top-level so normal module discovery is
   explicit.
3. Assert pack inventory, budgets, supported environment, deferred boundary,
   discoverability, and threat-model wording.
4. Build a standard local target that preserves its README and contains only
   three customized authority files.
5. Run the exact five launcher commands and prove target HEAD, index, status,
   and visible bytes are unchanged.
6. Capture RED only for missing recut behavior; do not weaken passing template
   assertions.

## Task 1 — Keep the four-file pack

**Files:** `templates/new-project/AGENTS.md`,
`templates/new-project/README.md`,
`templates/new-project/.codex/project-policy.toml`,
`templates/new-project/.codex/resource-registry.toml`

1. Keep placeholders source-only and consumer README ownership explicit.
2. Keep instructions restrictive and non-authorizing.
3. Keep policy schema v1 and only the resources and builtin gates it needs.
4. Make no template change when the RED already proves the preserved bytes meet
   the contract.

## Task 2 — Recut operator documentation

**Files:** `docs/engineering/23-new-project-audit-bootstrap.md`, design,
this plan, `README.md`, and `docs/engineering/00-canonical-index.md`

1. Replace the executable-verifier direction with a 100–150 line operator
   runbook.
2. State the supported v1 environment and `UNSUPPORTED / STOP` boundary.
3. List exactly five read-only commands and their acceptance evidence.
4. Keep project identification and target-specific bootstrap deferred.
5. Retain concise discoverability links.

## Task 3 — Threat bind

**Files:** `docs/security/2026-08-12-control-plane-core-threat-model.md`

1. Replace prototype claims with the source-owned four-file pack, supported v1
   environment, and zero-write test evidence.
2. Name unsupported topology and operator substitution as residual risks.
3. Recompute the repository-scoped footer only after all other tracked bytes are
   frozen.

## Task 4 — Verify and review

1. Run the focused readiness class.
2. Run the full documentation module and focal policy/registry checks.
3. Check the four-file, 100–150, and 300–500 budgets mechanically.
4. Run post-gates and `git diff --check`.
5. Apply independent read-only review to the frozen delta.

A finding blocks only if it is reproducible, introduced by this delta, affects
the supported v1 environment, prevents the promised outcome, and cannot be
honestly contained by `UNSUPPORTED / STOP`. Record all other findings
without expanding scope.

## Deferred continuation

After integration, inspect the actual next project and prepare the exact
authority bytes outside it. The later target-specific bootstrap is a separate
front with that project's current gates and authority. Do not infer it from this
plan.
