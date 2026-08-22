# Control Plane adoption readiness v1 design

Status: `AUDIT_ONLY / DEFERRED_TARGET_BOOTSTRAP`.

## Decision

Provide the smallest source-owned kit that can answer one question: can an
already-customized governance bundle be parsed, inventoried, and routed for the
next project without changing that project?

The answer is local audit evidence, never adoption. The earlier exhaustive
prototype is preserved on its own branch but is not part of this candidate.

## Contract

The source pack contains exactly four files under `templates/new-project/`:
`AGENTS.md`, `README.md`, `.codex/project-policy.toml`, and
`.codex/resource-registry.toml`.

Only the first file and the two TOML files are future authority candidates.
They retain `__PROJECT_NAME__` in source, are customized and reviewed outside the
consumer, and never replace its README. Generic bytes and every result remain
`authorizes=false`.

The instruction template starts restrictive: evidence, TDD, explicit scope,
protected-base work, non-destructive Git, preservation before squash, and
target-specific authorization. Reserved effects stay separately authorized.

Policy uses schema v1 and conservative Git and release defaults. Registry
contains project instructions, the consumer README, and only policy-referenced
builtin gates. Its single special route recommends the consumer README only for
first-use T2 `intent=audit`, `phase=research`.

## Supported v1 environment

The supported v1 environment is deliberately ordinary and local:

- exact integrated, clean, fully materialized Control Plane source;
- clean standard local Git target with attached branch and committed HEAD
  contained in its local base;
- configured remote and local remote-base tracking ref;
- ordinary index and local object store;
- customized authority committed at target HEAD;
- consumer README preserved;
- regular fully materialized TaskEnvelope outside the target;
- no submodules, nested repositories, filters, alternates, object redirects,
  configuration includes, File Provider, or dataless state.

Any false or unknown condition is `UNSUPPORTED / STOP`. V1 contains no
embedded verifier and makes no claim about hostile or exotic topology. This is
an explicit containment boundary, not an implementation gap to repair inside
the audit.

## Read-only proof

The canonical launcher executes exactly five operations: policy-check,
registry-check, inventory, offline read preflight, and audit route. Tests run
those operations against a harness-owned standard local repository containing
only its README and the three customized authority files.

The proof requires valid JSON, ready project instructions and README, green
applicable local preflight checks, T2 audit/research routing, and zero change to
target HEAD, index, status, or visible files. A local remote and tracking ref
make offline facts testable but prove no provider state.

The runbook is 100–150 lines. The test delta is 300–500 lines. There is no
runtime module, installer, mutator, copier, hook, lock, state store, dependency,
CI change, consumer write, provider refresh, or publication effect.

## Review convergence

A review finding blocks this v1 only when all five predicates hold:

1. it is reproducible;
2. it is introduced by this delta;
3. it affects the supported v1 environment;
4. it prevents the promised outcome;
5. it cannot be honestly contained by `UNSUPPORTED / STOP`.

Otherwise record the finding with its reproduction and destination, without
expanding this front. Set `max_repair_rounds=2`. If either round causes
`surface_growth_limit=20%` to be exceeded relative to the frozen recut,
reframe instead of continuing repair.

## Security and rollback

Primary risks are confusing generic governance with reviewed project authority,
replacing the consumer README, treating local readiness as remote proof, or
letting prose mint a later write. Source/target separation, exact pack
inventory, real command tests, restrictive templates, explicit environment
containment, and `authorizes=false` mitigate them.

Residual risk begins when an operator chooses substitutions or crosses into the
target-specific bootstrap. That later transition requires current project
evidence and authority.

Rollback removes the source pack and discoverability links. There is no
consumer rollback because this front performs no consumer effect.

## Deferred decisions

Project selection, target inspection, customization approval, target write,
commit, push, Pull Request, merge, installation, adoption, deploy, and release
remain `DEFERRED_TARGET_BOOTSTRAP`. Reusable write automation needs a
separate decision based on repeated real-project evidence.
