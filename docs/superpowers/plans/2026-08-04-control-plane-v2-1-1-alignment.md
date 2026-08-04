# Control Plane v2.1.1 alignment

## Objective

Prepare a review-ready `v2.1.1` local change that aligns runtime identity,
reversible adoption upgrades, the natural-language installation skill, and
bounded workflow-backed release evidence. This phase ends before commit or any
remote or global effect.

## Scope

- select product version `2.1.1` consistently in runtime, lock and adoption;
- prove an installed `2.1.0` runtime upgrades to `2.1.1`, rolls back to the
  original bytes, and can be upgraded again;
- version `skills/install-control-plane/SKILL.md` with exact install-intent
  triggers and fail-closed authority boundaries;
- keep local release builds non-authorizing while allowing the existing manual
  workflow to bind verified gate evidence to the official four assets;
- correct the historical `v2.1.0` release documentation and add `v2.1.1`
  release notes.

## Exclusions

No commit, push, Pull Request, merge, tag, release, global skill installation,
dependency, secret access, plugin, project rename, or consumer-project change.

## Verification

Use TDD for every behavior. Run focused contracts, the complete suite, policy
and registry checks, doctor, diff validation, release preflight where its source
preconditions apply, the isolated Generic/iOS/BUSTAFIT adoption matrix, macOS
smoke, and independent review. Official evidence remains non-authorizing.

## Rollback

Until commit, revert only the allowlisted working-tree paths. After a future
merge, consumers retain their existing transactional `upgrade plan/apply`,
`adopt verify`, and original `adopt rollback` path. `v2.1.0` and its assets are
immutable and must not be moved or replaced.
