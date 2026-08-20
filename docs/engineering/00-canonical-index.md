# Canonical documentation index

This index separates current governance from preserved design history. Runtime
bytes, the exact lock, policy, registry, and executable gates take precedence
over prose. A historical document can explain a decision but cannot reactivate
a quarantined capability or grant authority.

## Version truth

- 2.1.1 — last official release, published as tag and GitHub Release.
- 3.0.0 — unpublished plugin candidate; not a product release.
- 3.1.0-core.1 — superseded local prerelease candidate; its ten-task dogfood and
  other version-bound evidence remain historical only.
- 3.1.0-core.2 — current local prerelease candidate; its maximum truthful status
  is `GREEN_LOCAL / PENDING_STABLE_ADOPTION`. Stable Pause v1 is
  `IMPLEMENTED_LOCAL / CLOSES_ON_FINAL_EVIDENCE` in the exact 27-module candidate;
  this invalidates earlier Core byte-bound evidence without creating a new
  product version. Fresh ten-task dogfood is required for `3.1.0-core.2`
  before any separate stable-adoption decision.

## Governing Core documents

| Path | Status | Purpose |
|---|---|---|
| `README.md` | `GOVERNING_CORE` | Current entry point and supported surface. |
| `AGENTS.md` | `GOVERNING_CORE` | Repository working and authority rules. |
| `SECURITY.md` | `GOVERNING_CORE` | Repository security policy and reportability. |
| `docs/adr/0006-control-plane-core-and-quarantine.md` | `GOVERNING_CORE` | Structural Core decision. |
| `docs/engineering/00-canonical-index.md` | `GOVERNING_CORE` | This status map and version truth. |
| `docs/engineering/03-reasoning-context-agents.md` | `GOVERNING_CORE` | Proportional reasoning and non-overlapping workers. |
| `docs/engineering/04-documentation-policy.md` | `GOVERNING_CORE` | Document ownership and evidence roles. |
| `docs/engineering/08-global-codex-configuration.md` | `GOVERNING_CORE` | Safe global-versus-project configuration boundary. |
| `docs/engineering/09-audit-dafo-and-risk-register.md` | `GOVERNING_CORE` | Local audit and residual-risk register. |
| `docs/engineering/10-resource-routing.md` | `GOVERNING_CORE` | Current resource selection contract. |
| `docs/engineering/12-multidominio-y-modos.md` | `GOVERNING_CORE` | Current multidomain and interaction guidance. |
| `docs/engineering/19-control-plane-core-maintenance.md` | `GOVERNING_CORE` | Core operation, compatibility, recovery, and rollback. |
| `docs/engineering/20-control-plane-core-dogfood.md` | `GOVERNING_CORE` | Historical core.1 evidence plus the fresh core.2 dogfood gate. |
| `docs/engineering/21-repository-alignment-and-branch-decisions.md` | `GOVERNING_CORE` | Observed repository state, per-branch decisions, and cleanup runbook. |
| `docs/engineering/22-orientation-and-known-traps.md` | `GOVERNING_CORE` | Cold-start entry point: where to work, what is true now, and environment faults that imitate defects. |
| `docs/security/2026-08-12-control-plane-core-threat-model.md` | `GOVERNING_CORE` | Repository-scoped threat model. |
| `docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md` | `GOVERNING_CORE` | Stable Pause v1 WHAT/WHY verify-only contract; `IMPLEMENTED_LOCAL / CLOSES_ON_FINAL_EVIDENCE`. |
| `docs/superpowers/plans/2026-08-14-control-plane-stable-pause-v1.md` | `GOVERNING_CORE` | Stable Pause v1 HOW, TDD traceability and rollback; `IMPLEMENTED_LOCAL / CLOSES_ON_FINAL_EVIDENCE`. |
| `docs/superpowers/plans/2026-08-18-control-plane-3-2-specpack.md` | `GOVERNING_CORE` | Current phased plan for the SpecPack contract and its gate. |
| `docs/superpowers/specs/2026-08-18-control-plane-3-2-specpack-design.md` | `GOVERNING_CORE` | Current SpecPack design: PRD, TRD, UX/UI, flow, and backend contracts. |
| `docs/superpowers/specs/2026-08-18-control-plane-3-3-operator-orientation-design.md` | `GOVERNING_CORE` | Implemented operator-orientation contract and blind-spot evidence. |

## Governing local enablement documents

These artifacts govern only the separately locked local adoption tool. Their
status does not add modules to Core or grant consumer adoption.

| Path | Status | Purpose |
|---|---|---|
| `docs/superpowers/specs/2026-08-13-control-plane-core-adoption-enablement-design.md` | `GOVERNING_LOCAL_ENABLEMENT` | Accepted transaction and rollback design; canary remains prohibited. |
| `docs/superpowers/plans/2026-08-13-control-plane-core-adoption-enablement.md` | `GOVERNING_LOCAL_ENABLEMENT` | Local implementation, TDD, temporary-repository evidence and exact rollback. |

## Historical non-governing documents

These files are intentionally not rewritten. Their operational examples and
authority models do not govern Core.

| Path | Status | Historical scope |
|---|---|---|
| `docs/adr/0001-router-hibrido-y-resolver-puro.md` | `HISTORICAL_NON_GOVERNING` | Original routing architecture. |
| `docs/adr/0002-distribucion-hooks-leases-y-enforcement.md` | `HISTORICAL_NON_GOVERNING` | Original distribution and lifecycle architecture. |
| `docs/adr/0003-local-audit-kernel-v2-1.md` | `HISTORICAL_NON_GOVERNING` | v2.1 local-audit architecture. |
| `docs/adr/0004-skill-led-local-run-loop.md` | `HISTORICAL_NON_GOVERNING` | Skill-led candidate run loop. |
| `docs/adr/0005-host-bound-outcome-authorization.md` | `HISTORICAL_NON_GOVERNING` | v2.3 outcome authority design. |
| `docs/engineering/01-operating-model.md` | `HISTORICAL_NON_GOVERNING` | Pre-Core operating model. |
| `docs/engineering/02-git-pr-merge.md` | `HISTORICAL_NON_GOVERNING` | Pre-Core Git and remote transition runbook. |
| `docs/engineering/05-release-and-observation.md` | `HISTORICAL_NON_GOVERNING` | Pre-Core release workflow. |
| `docs/engineering/06-recovery.md` | `HISTORICAL_NON_GOVERNING` | Pre-Core lifecycle recovery. |
| `docs/engineering/07-adoption.md` | `HISTORICAL_NON_GOVERNING` | Pre-Core adoption flow. |
| `docs/engineering/11-lifecycle-hooks-adoption.md` | `HISTORICAL_NON_GOVERNING` | v2.1-v2.3 lifecycle and adoption runbook. |
| `docs/engineering/13-clarification-and-risk-local-audit.md` | `HISTORICAL_NON_GOVERNING` | v2.1 local-audit risk contract. |
| `docs/engineering/14-bustafit-dogfood-pilot.md` | `HISTORICAL_NON_GOVERNING` | Historical v2.1 dogfood evidence. |
| `docs/engineering/16-outcome-bridge-rollback.md` | `HISTORICAL_NON_GOVERNING` | Advanced outcome rollback. |
| `docs/engineering/17-v2-3-native-sandbox-promotion.md` | `HISTORICAL_NON_GOVERNING` | Unexecuted sandbox promotion. |
| `docs/engineering/18-native-governor-plugin.md` | `HISTORICAL_NON_GOVERNING` | v2.4/plugin candidate. |
| `docs/security/2026-08-08-v2-3-outcome-bridge-threat-model.md` | `HISTORICAL_NON_GOVERNING` | Advanced-specific threat model. |
| `docs/superpowers/plans/2026-07-28-codex-engineering-control-plane-v1.md` | `HISTORICAL_NON_GOVERNING` | Initial Control Plane plan. |
| `docs/superpowers/plans/2026-07-29-clarification-gate-risk-sentinel-v2-1.md` | `HISTORICAL_NON_GOVERNING` | v2.1 clarification and risk plan. |
| `docs/superpowers/plans/2026-07-31-control-plane-v2-1-local-audit-consolidation.md` | `HISTORICAL_NON_GOVERNING` | v2.1 consolidation plan. |
| `docs/superpowers/plans/2026-08-01-control-plane-v2-1-pilot-fixes.md` | `HISTORICAL_NON_GOVERNING` | v2.1 pilot-fix plan. |
| `docs/superpowers/plans/2026-08-02-bustafit-dogfood-pilot.md` | `HISTORICAL_NON_GOVERNING` | v2.1 dogfood plan. |
| `docs/superpowers/plans/2026-08-03-continuation-pointer-v1.md` | `HISTORICAL_NON_GOVERNING` | v2.1 continuation design plan. |
| `docs/superpowers/plans/2026-08-03-cross-thread-host-lookup-v1.md` | `HISTORICAL_NON_GOVERNING` | v2.1 host lookup plan. |
| `docs/superpowers/plans/2026-08-03-release-v2-1-prep.md` | `HISTORICAL_NON_GOVERNING` | v2.1 release preparation. |
| `docs/superpowers/plans/2026-08-03-supported-adoption-v2-1.md` | `HISTORICAL_NON_GOVERNING` | v2.1 supported-adoption plan. |
| `docs/superpowers/plans/2026-08-04-control-plane-v2-1-1-alignment.md` | `HISTORICAL_NON_GOVERNING` | v2.1.1 alignment plan. |
| `docs/superpowers/plans/2026-08-08-control-plane-v2-3-outcome-bridge.md` | `HISTORICAL_NON_GOVERNING` | v2.3 implementation plan. |
| `docs/superpowers/plans/2026-08-08-personal-control-plane-v3.md` | `HISTORICAL_NON_GOVERNING` | Personal v3 candidate plan. |
| `docs/superpowers/plans/2026-08-10-control-plane-v2-4-native-governor.md` | `HISTORICAL_NON_GOVERNING` | v2.4 implementation plan. |
| `docs/superpowers/plans/2026-08-11-control-plane-taskplaybook-v0-progressive-disclosure.md` | `HISTORICAL_NON_GOVERNING` | Pre-Core taskplaybook plan. |
| `docs/superpowers/plans/2026-08-12-control-plane-core-3-1.md` | `HISTORICAL_NON_GOVERNING` | Original 3.1.0-core.1 implementation plan and checkpoint; superseded by the 3.1.0-core.2 local candidate. |
| `docs/superpowers/plans/2026-08-18-control-plane-3-3-operator-orientation.md` | `HISTORICAL_NON_GOVERNING` | Executed 3.3 implementation transcript; the design and current runtime govern. |
| `docs/superpowers/specs/2026-07-28-codex-engineering-control-plane-design.md` | `HISTORICAL_NON_GOVERNING` | Initial design specification. |
| `docs/superpowers/specs/2026-07-29-clarification-gate-risk-sentinel-design.md` | `HISTORICAL_NON_GOVERNING` | v2.1 risk design specification. |
| `docs/superpowers/specs/2026-08-08-control-plane-v2-3-outcome-bridge-design.md` | `HISTORICAL_NON_GOVERNING` | v2.3 design specification. |
| `docs/superpowers/specs/2026-08-10-control-plane-taskplaybook-v0-design.md` | `HISTORICAL_NON_GOVERNING` | Pre-Core taskplaybook design. |
| `docs/superpowers/specs/2026-08-10-control-plane-v2-4-native-governor-design.md` | `HISTORICAL_NON_GOVERNING` | v2.4 design specification. |
| `docs/releases/v2.1.0.md` | `HISTORICAL_RELEASE_EVIDENCE_NON_GOVERNING` | Immutable evidence for release v2.1.0, not current operation. |
| `docs/releases/v2.1.1.md` | `HISTORICAL_RELEASE_EVIDENCE_NON_GOVERNING` | Immutable evidence for the last official release, not current operation. |

Release records remain canonical evidence of their own published versions. The
special status makes explicit that they are not instructions for Core.

## Proposed decisions

A proposed decision records an intent under review. It does not govern Core, does
not enable a capability, and does not grant authority until it is accepted.

| Path | Status | Purpose |
|---|---|---|
| `docs/adr/0007-governed-product-spec-pack.md` | `PROPOSED_NON_GOVERNING` | SpecPack contract and its phased gate; awaiting acceptance. |
