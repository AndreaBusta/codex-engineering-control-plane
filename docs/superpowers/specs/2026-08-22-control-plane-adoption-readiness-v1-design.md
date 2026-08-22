# Control Plane adoption readiness v1 design

Status: `AUDIT_ONLY / DEFERRED_TARGET_BOOTSTRAP`.

## Decision

Provide a compact source-owned readiness kit for an unidentified next project.
The kit proves that current Control Plane bytes can validate and route a
project-specific governance bundle without changing a consumer. It is not an
installer, adoption mechanism, or generic Git workflow.

This is a deliberate recut from an exhaustive prototype: generalized copying,
transaction, branch, staging, and commit behavior added more trust surface than
the current objective needs. The preserved prototype is evidence, not an input
to this candidate.

## Contract

The source pack contains exactly four files under `templates/new-project/`:
template `AGENTS.md`, policy, registry, and a source-side README. Only the first
three can later become project authority. Their generic bytes never enter the
target: customization and review occur outside it, and the consumer README is
preserved.

The instruction template starts restrictive. It requires evidence, TDD, bounded
scope, protected-base work, non-destructive Git, and exact target-specific
authorization for commit, push, Pull Request, and merge. Reserved effects remain
separately authorized. Every template artifact is `authorizes=false`.

The generic policy uses schema v1 and conservative Git/release defaults. The
minimum registry contains:

- required project instructions;
- the real consumer README as a recommendation only for first-use
  `intent=audit`, `phase=research`, T2 routing;
- only the builtin gates referenced by policy.

It contains no speculative remote or release provider.

## Read-only proof

A clean detached, fully materialized local Control Plane source is bound to an
operator-selected exact integrated SHA by a bounded raw comparison of every tracked blob, symlink and
Git mode and exact equality between tracked paths and observed non-`.git`
leaves. The binding forces the physical worktree, so index hints, filters and
repo-local `core.worktree` cannot redirect it. It disables lazy fetch, bounds
blob types and sizes before deadlock-free batch capture, streams directory
fanout, and has one whole-verifier watchdog that reaps any active Git process.
The selected source index must equal its selected HEAD tree and every bounded
`ls-files -v` entry must be normal; staged-only content, skip-worktree,
assume-unchanged, gitlinks and non-zero/error command states stop.
Before Git or content reads, bounded no-follow metadata inspection rejects
`UF_DATALESS`, wrong-owner, or group/world-writable source, Git metadata, target,
README, and authority inputs. File Provider or materialization uncertainty is
unsupported and stops before audit. Every physical ancestor is identity-bound;
a writable ancestor is accepted only when sticky-bit semantics protect its
effective-UID-owned child and the sticky directory is owned by root or that
effective UID. Source and target object stores must be local: object
redirect symlinks, alternates, HTTP alternates and repository config includes
stop before the launcher; a UTF-8 BOM cannot obscure an include. Every direct
`filter.*` namespace is rejected from repository config before target Git can
run status or a filter; fsmonitor and untracked cache remain forcibly disabled.
External stores are not inspected or changed.

A read-only guard requires the three externally reviewed SHA-256 values, an
exact physical target path, and descriptor-relative no-follow reads with stable
same-owner ancestor and leaf identities. Missing, symlinked, special,
substituted, oversized, or placeholder-bearing authority fails before the five
Control Plane commands. All three authority files must also be supported regular
stage-0 entries in both target index and `HEAD`; each `HEAD` blob, reviewed
digest and live byte digest must agree, so ignored or merely untracked authority
cannot pass. It also binds the externally validated TaskEnvelope to
its literal physical path and reviewed SHA-256 with the same materialization,
ownership, permissions, ancestor, size and stable-descriptor rules. The target
Git top, gitdir and common dir must resolve back to those physical bindings, so
a repo-local `core.worktree` redirect stops. Its index must equal HEAD, every
index tag must be normal, and an explicitly configured porcelain status must be
empty with untracked visibility and file-mode comparison forced despite
repository attempts to hide dirty work. Submodules,
common-dir module storage and nested `.git` entries are unsupported in v1 and
stop rather than being traversed. A single
silent wrapper repeats source, target-authority and TaskEnvelope verification
immediately before and after every launcher invocation, including optional
diagnostics.

The harness-owned target preserves its consumer README, contains only the
already-customized authority files, and publishes `main` to its own local bare
`origin`. This makes every applicable local preflight check factual and green;
the configured remote and local tracking ref remain no proof of provider
freshness. The source launcher runs policy-check, registry-check, inventory,
offline read preflight, and audit route only for the first-use
`intent=audit`, `phase=research`, T2 `TaskEnvelope`; other intents/phases select
neither this route nor its README recommendation.

All five run under a closed process environment, exit zero, emit bounded valid
JSON, leave target-visible bytes, symlink state, and exact index state unchanged,
and keep `authorizes=false` for fully materialized local roots. Inventory must prove the project instructions and
consumer README ready with no `R_NOT_FOUND`; route success cannot override
missing local resources. `doctor` and `survey` remain optional diagnostics, not
expected-red gates.

## Security and rollback

The attacker story is uncustomized or substituted governance or TaskEnvelope
being mistaken for reviewed input, including staged/index-hidden bytes, a
submodule boundary, a promisor source that fills missing bytes, an alternate
object store, a config include/filter, ignored authority or a writable ancestor
redirecting verification.
Mitigation is source/target separation, exact clean index/status checks,
reviewed digests, physical no-follow ancestor binding, local object stores,
disabled lazy fetch, explicit submodule rejection, per-command revalidation,
target-specific authorization, and a no-write audit. Residual risk begins only
when a later operator chooses target values or performs the deferred write.

Rollback of this source candidate is removal of the pack and its references.
There is no consumer rollback because this front performs no consumer effect.

## Deferred decisions

Target identification, inspection, customization, target write, installation,
and adoption remain `DEFERRED_TARGET_BOOTSTRAP`. If multiple real projects later
prove the same safe write mechanics are needed, design a reusable mutator ADR
from that evidence; do not pre-build one here.
