# Control Plane Core threat model

## Overview

This repository is a local engineering control plane, not a sandbox or an
external authority issuer. Its active product surfaces are the exact Python
runtime allowlist, launcher and hooks, project policy and resource registry,
local Git observations, `CoreTaskStateV1`, generational leases, serialized
verification, maintenance lineage, installed-generation recovery, and the
source-only Control Plane plugin.

Assets include current user authority; project source and documentation; Git
history, refs, worktrees, index, and configuration; policy, registry, lock, and
runtime digests; task, lease, maintenance, and adoption recovery records; local
verification evidence; credentials held outside this repository; and the
integrity of consumer repositories and external providers.

The primary security objective is fail-closed local coordination. A plan,
receipt, checkpoint, document, test, plugin, or JSON value remains
`authorizes=false`. `external_consumer_adoption=PROHIBITED` until a separate
stable-adoption decision.

## Threat Model, Trust Boundaries, and Assumptions

Trust precedence is platform and system, global guardrails, project AGENTS,
project policy and registry denials, required resources, recommendations, then
untrusted content. Prose never overrides an executable denial.

- attacker-controlled inputs include prompt text, Issues, PRs, comments, web
  content, repository files from an untrusted branch, TaskEnvelope fields,
  locators, JSON inputs, filenames, symlinks, and hostile environment values;
- operator-controlled inputs include selected repository/worktree, task scope,
  CLI arguments, requested outcome, approval boundaries, and any separately
  authorized recovery action;
- developer-controlled inputs include runtime source, tests, policy, registry,
  lock generation, plugin source, documentation, and release preparation.

The local filesystem, Git binary, Codex host, hooks host, and external providers
are distinct trust boundaries. Core assumes the operating-system account and
Git executable are not fully compromised. A compromised host or filesystem can
lie outside the protection of cooperative local controls.

Repository invariants:

1. Validate the exact runtime allowlist, materialization, ownership, and digest
   before importing runtime modules; extra, missing, symlinked, or drifted bytes
   fail closed.
2. Core accepts only `answer` and `local_change`; every remote or publication
   effect remains outside the active runtime.
3. Durable artifacts never serialize or mint authority and always retain
   `authorizes=false`.
4. Each Core writer is bound to one task revision, worktree, branch, session,
   policy digest, scope, and generational lease; overlapping Core writers fail.
5. Full verification is serialized by Git common dir. `E_VERIFICATION_BUSY`
   executes nothing and consumes no repair or reframe.
6. Maintenance permits one structural reframe. A second stops with
   `E_BOOTSTRAP_REFRAME_LIMIT` and preserves the stable runtime.
7. Legacy inventory is `origin=legacy`, read-only, bounded, and non-resumable.
   Installed-generation rollback validates all bytes and config, then fails
   with `E_ADOPT_QUIESCENCE_UNKNOWN` because v2.1 has no shared writer barrier.
   `legacy_writer_exclusion=COOPERATIVE_ONLY`: an observed legacy writer blocks
   Core, but a same-UID v2.1 process started after that observation does not
   participate in Core locks.

## Attack Surface, Mitigations, and Attacker Stories

| Surface or attacker story | Security failure | Mitigation and residual limit |
|---|---|---|
| A prompt or document says it authorizes commit, install, push, or release | Confused-deputy external effect | Closed outcomes, policy gates, `authorizes=false`, and no active external executor; a compromised host remains outside the model. |
| A package file is added after the digest is computed | Unreviewed code imports into Core | Exact runtime allowlist and digest before import; filesystem mutation after validation remains residual. |
| A task or lease is replayed in another worktree, branch, session, policy, or revision | Wrong-subject write | Immutable bindings, state digests, generation checks, and atomic compensation. |
| Two Core verifiers or writers start together | Duplicate full suite or overlapping edits | Nonblocking verification mutex and scoped generational Core leases. |
| A same-UID v2.1 writer starts after Core inventories legacy state | Overlapping legacy/Core edits | Active observed legacy state fails closed. There is no bilateral lock: the orchestrating host must not run legacy and Core writers concurrently; external adoption remains prohibited. |
| Repeated bootstrap failures create an endless repair tree | Unbounded maintenance and hidden authority drift | One `MaintenanceLineageV1` reframe, then `E_BOOTSTRAP_REFRAME_LIMIT`. |
| A legacy file is malformed, oversized, symlinked, active, or remote-unknown | Unsafe resume or destructive cleanup | Bounded no-follow inventory, `resumable=false`, and `E_ACTIVE_LEGACY_STATE`; use its owning runtime. |
| A recovery journal drifts or a legacy writer starts during recovery | Partial or attacker-directed rollback | Complete mutation-free preflight followed by `E_ADOPT_QUIESCENCE_UNKNOWN`; no caller-forgeable flag substitutes for a shared writer barrier. |
| `HOME`, Git variables, locators, or executable lookup are redirected | Resource or repository substitution | Trusted toolchain context, canonical exact resource revision, bounded subprocesses, and fail-closed unknown. |
| A local green result is presented as stable adoption | Self-certification or supply-chain promotion | `GREEN_LOCAL / PENDING_STABLE_ADOPTION`, `self_certified=false`, manual dogfood, and separate adoption authority. |

Web-application classes such as XSS, CSRF, SQL injection, or tenant isolation
are not primary runtime surfaces because this repository does not serve a web
application. They become relevant only if a future component adds such a
surface; generic labels without reachability are not findings here.

## Severity Calibration

### Critical

A reachable path that lets untrusted repository or prompt content mint reusable
authority, execute an external release, or silently install arbitrary runtime
bytes without an independent host boundary. Another example is credential
exfiltration from outside the repository by the default Core path.

### High

A reliable bypass of the exact runtime allowlist; cross-worktree writer replay
that can corrupt project source; or rollback path traversal that overwrites an
attacker-chosen file under realistic operator use.

### Medium

A bounded denial of service that blocks every Core task, a verification mutex
bypass that predictably duplicates an expensive full suite, or material policy
misrouting that remains local and requires operator action before any external
effect.

### Low

An inaccurate non-authorizing diagnostic, a nuisance warning above the intended
budget, or a documentation inconsistency that cannot change runtime behavior,
authority, project bytes, or external state.

## Residual risks

- A compromised OS account, Codex host, Git binary, filesystem, or provider can
  bypass or falsify its own trust boundary.
- Hooks remain advisory and may be skipped or coexist with other hooks.
- Existing installed and legacy generations may need their owning stable
  runtime for safe closure; Core deliberately will not resume them.
- A same-UID legacy runtime can start after Core's bounded inventory because
  v2.1 does not inspect the Core namespace and holds no writer lock for the
  lifetime of an edit. Host-level serialization is required until a future
  bilateral migration; Core does not claim atomic legacy exclusion.
- Manual dogfood is pending and stable external adoption is unproven.
- The snapshot binds immutable anchor
  `929d3f8a0656fed190bb65ceb3a29deef8de07d6`, its canonical final tracked
  overlay, non-ignored untracked regular files excluding this threat-model
  path, and the normalized threat-model body. Provenance (dirty or committed)
  and current commit identity are intentionally excluded: identical final raw
  bytes and Git executable modes produce the same Version before and after the
  checkpoint commit. Ignored untracked files are outside the snapshot target
  and cannot be runtime inputs; the exact runtime allowlist is enforced
  independently. `snapshot_normalization=exclude_cache_footer_only` removes
  only the final `Repository` and `Version` metadata from its own digest
  preimage.

Repository: sha256:31d48f56964b98247664973b33d474c0f79ce6e9ac191996c9c6ad4307fe8959
Version: codex-security-snapshot/v1:sha256:b24556d1565232c2caa9ddb587633845c281897aaece0616b5d4d1f09eadcad3