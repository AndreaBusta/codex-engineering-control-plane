# Control Plane new-project audit bootstrap v1

## Status

Accepted for implementation by the native operator request on 2026-08-22.
This document defines a project-owned, audit-only bootstrap. It does not
install Control Plane, authorize Adoption Enablement against a consumer, or
change the stable-adoption decision.

```text
bootstrap_pack=PROJECT_OWNED_TEMPLATE
source_driven_audit=SUPPORTED
runtime_installation=NOT_PERFORMED
external_consumer_adoption=PROHIBITED
consumer_adoption_commands=PROHIBITED
canary=NOT_PREPARED
authorizes=false
```

## Decision

The next project starts with three project-owned governance files extracted
from the fixed source commit objects for `templates/new-project/`: `AGENTS.md`,
`.codex/project-policy.toml`, and `.codex/resource-registry.toml`. The fourth
pack file, the template README, is the source-side customization guide and
remains in the Control Plane source; it never overwrites the consumer's own
README.

The runtime remains in a selected clean source: a detached worktree prepared
from the integrated `origin/main` and bound to an exact reviewed SHA
(`selected clean source`). The new project invokes that exact
source entrypoint with explicit `--repo`, `--policy` and `--registry`
arguments. No runtime, hook, lock, journal, or generated file is copied into
the consumer. The supported first use is observation and routing in `audit`
mode only.

This is readiness for a later real project, not stable adoption. The actual
project path, initial project-specific values, and any later installation or
canary remain separate operator decisions.

## Audit evidence and dispositions

The external review titled “Revisión de cambios entre hilos” was inspected as
untrusted, stale input and checked against `origin/main` at
`f1fdecbb26fed9272d07823c31f06ef15ac89f78`.

| Review claim | Current evidence | Disposition |
|---|---|---|
| Automatic branch deletion risks losing preservation commits | The repository now has `deleteBranchOnMerge=false`; loss guards and Survey V2 are integrated. | Resolved for this repository; preserve the setting and content-containment checks. |
| 37,356 lines of tests are outside `tests/run.sh` | The count is reproducible, but the closed governing manifest is intentional. Twenty-three omitted modules still collect tests, seven fail collection against removed runtime, and `tests/test_hooks.py` contains current focal coverage. | Do not bulk-delete. Open a separate test-provenance contract that classifies every test before migration or deletion. |
| `maintenance.py` is disconnected from the CLI and the failure path does not consume the reframe budget | Both code observations are current. | Separate runtime front; no runtime change in this bootstrap. |
| Leases need clock expiry | Current leases are generation-, owner-, session-, branch-, worktree- and state-bound but have no TTL. A clock expiry could release a valid paused writer without a stronger ownership protocol. | Reject the proposed shortcut. Reconsider only with a replacement ownership design and failure proof. |
| The aggregate risk sentinel can never become locally green because remote evidence remains `UNKNOWN` | Current tests intentionally preserve remote `UNKNOWN`, while the aggregate includes it. | Separate contract front for local versus remote readiness; do not silently drop the remote dimension. |
| Start using Control Plane in a real project now | Adoption Enablement remains harness-only and consumer execution is prohibited. | Start with source-driven, read-only audit from this bootstrap; collect evidence before any installation/canary ADR. |

The canonical index and the three RepositorySurveyV2 contract documents still
label the already integrated behavior as `FINAL_GATE_PENDING`. This bootstrap
corrects that documentary drift because the exact implementation commit is
contained by current `origin/main` and its CI is terminal green. Historical
task steps remain historical; only their governing status and stale
Continuation Pointer are realigned.

## Goals

1. Give a new repository a small, valid and reviewable project-owned starting
   policy without copying Control Plane runtime bytes.
2. Make the first source-driven audit commands exact and reproducible.
3. Prove the pack is syntactically valid and compatible with the existing
   read-only Adoption Enablement preview inside a harness-owned temporary repo.
4. Preserve every existing prohibition on consumer adoption, canary,
   installation, dependencies, CI mutation, secrets, deploy and release.

## Non-goals

- No changes to `control_plane/`, `adoption_enablement/`, Core locks, hooks or CI.
- No consumer repository is selected or mutated by this change.
- No `scripts/control-plane-adoption` command is run against a consumer.
- No hook installation, enforcement promotion, task creation, lease creation,
  release, deployment, package publication or dependency installation.
- No deletion or reclassification of the omitted test inventory.
- No maintenance-budget, lease-expiry or risk-sentinel implementation.
- No claim that `3.1.0-core.2` has completed fresh ten-task dogfood or stable
  adoption.

## Bootstrap pack contract

The tracked pack is exactly:

```text
templates/new-project/
├── AGENTS.md
├── README.md
└── .codex/
    ├── project-policy.toml
    └── resource-registry.toml
```

The pack contains four source files, but only `AGENTS.md` and the two TOML
authority files are copied into a consumer. `README.md` remains the
source-side operator guide. A consumer README is project content and must be
preserved byte-for-byte.

Copying the three authority files is fail-closed and all-or-none at the
existence boundary. A closed source-binding probe runs immediately before
object extraction; the three bytesets come only from `cat-file blob` at the
fixed commit, never from live template paths. Before the first target write,
the operator or harness checks all three staged authority paths and all three
destination paths with no-follow semantics equivalent to
`os.path.lexists`, including dangling symlinks. If any destination exists,
the operation returns `E_BOOTSTRAP_AUTHORITY_EXISTS`, writes nothing and
preserves the target snapshot. Existing authority is never overwritten or
auto-merged; it starts a separate, reviewed semantic reconciliation.

The policy and registry must parse under schema 1, pass their respective Core
validators, and resolve every named policy gate. Defaults are deliberately
generic and safe:

- project and registry identifiers are `new-project` and must be customized;
- remote is `origin`, base is `main`, PRs are required and direct base pushes
  are denied;
- integration is squash, normal work is sequential, and normal concurrency is
  capped at two workers;
- release truth is `remote_base` and a release manifest is required;
- registry mode is `audit` and external effects default to `deny`;
- only project instructions are required at cold start; optional host resources
  may remain unavailable without being presented as ready.

The template `AGENTS.md` is an operator contract, not runtime authority. It
requires customization, TDD for behavior, explicit evidence, a non-protected
work branch, and no direct base writes. Once those instructions are integrated
in the protected base, a work-branch commit requires current local gates;
push/PR/update/merge additionally require fresh provider observation, and merge
requires exact-head CI. A branch edit cannot self-authorize. Before integration,
creating the bootstrap branch and commit requires pre-existing project
authority or exact operator authorization. Deploy, release, publication,
dependency installation, CI changes, secrets and a merge that triggers any of
them remain transition-specific.

## Source-driven audit contract

The operator selects an absolute clean source path and a separately reviewed
40-hex source SHA. The contract never hardcodes a checkout as canonical or
derives the selected SHA from whatever `HEAD` happens to be present at that
path. Source binding requires exact HEAD, clean tracked/staged/untracked state
and rejects every tracked entry marked `assume-unchanged` or `skip-worktree`.
It never invokes a worktree-facing Git diff or status: index entries and blob
OIDs must equal the fixed HEAD tree, while bounded descriptor-relative
no-follow reads hash the regular worktree bytes directly. Symlinks, Gitlinks,
unmerged entries and other unsupported modes fail closed instead of being
ignored. The target branch and commit probes use the same raw rule and reject
every non-normal index tag before mutation.
It runs immediately before object extraction, immediately before audit, and
after audit. The first probe binds the private staging bytes; the latter two
bind the launcher that performs the observation.

From the new project, the supported sequence is:

1. Require a clean consumer-owned baseline commit, then create and verify a
   non-base work branch using pre-existing authority or exact operator
   authorization. A repository without `HEAD` is blocked until its baseline is
   prepared separately.
2. Read the source-side guide from the reviewed commit, extract the three
   authority blobs from that same commit into a private staging directory,
   then copy them only after an all-path `lexists` precheck proves all targets
   are absent.
   Preserve any consumer README byte-for-byte; on existing authority, return
   `E_BOOTSTRAP_AUTHORITY_EXISTS` and open reviewed semantic reconciliation.
3. Replace `new-project`, confirm project kind, base, remote, gates, resources
   and real test/build commands, then commit those values on a work branch.
   Author and committer identities are exact operator inputs; repository and
   global Git configuration are not identity authority. Staging uses a private
   copy of the exact index; its final bytes are atomically published only after
   validation, and a failed ref compare-and-swap performs byte-exact atomic
   restoration of the prior index bytes and mode.
4. Invoke the exact selected-source entrypoint for `policy-check`,
   `registry-check`, `inventory`, `doctor`, `preflight --mode read --offline`,
   and `route --mode audit` with explicit target paths.
5. Use `survey` only after the configured base ref exists locally.
6. Treat every artifact as local, non-authorizing evidence. Missing resources,
   missing base refs or incomplete host capability stay `UNKNOWN` or blocked.

`doctor --repo <target>` is expected to report the missing target lock and is
therefore `BLOCKED`, while also proving whether target policy, registry, Git
and Python observations are valid. That result is positive evidence that the
runtime was not installed, not a failed bootstrap. `route` may remain
`pending_host_capability` because the Core CLI cannot mint native host
capability; its decision is still useful, read-only and non-authorizing.

The bootstrap does not support write preflight, task lifecycle, Git guards or
Adoption Enablement against the consumer. Those require a later decision that
binds an exact target and exact source revision.

The central acceptance test executes all six source-driven commands through
the exact `scripts/control-plane` launcher against a harness-owned target and a
valid TaskEnvelope. It checks command-specific exits and JSON, requires
`authorizes=false`, compares target metadata before and after the entire
sequence, and separately proves the consumer README stayed byte-identical.
Loader-only validation is insufficient.

## Harness compatibility proof

The repository tests create a temporary Git target with its own README, copy
only the starter policy, registry and instructions into it, commit them as
target-owned bytes, and use the existing full-source fixture.
`preview(source, target)` must:

- return a valid closed plan;
- retain `authorizes=false` and `mutation=false`;
- leave source and target metadata byte-for-byte unchanged;
- leave the consumer README byte-for-byte unchanged;
- exclude `AGENTS.md`, project policy and resource registry from managed
  records because they remain target authority;
- include only the existing managed projection, including the target lock.

This proof is deliberately harness-only. It demonstrates compatibility, not
permission to execute preview against the next project.

## Failure semantics

- A template that does not parse or validate is a test failure.
- Any pre-existing authority destination, including a dangling symlink,
  returns `E_BOOTSTRAP_AUTHORITY_EXISTS` before any write; overwrite and
  auto-merge are prohibited.
- A policy gate without a registry alias is a test failure.
- A source-driven command that cannot observe the target truth must return its
  existing fail-closed status; documentation may not reinterpret it as PASS.
- A failing Git producer, unexpected return code, identity mismatch or failed
  index restoration is a distinct closed error; a shell consumer may not mask
  it as absence or success.
- Every environment input used by a governing block is exported explicitly by
  the documented setup. No `export VAR=$(...)` derives authoritative state or
  hides a producer return code.
- A preview that mutates either harness repository is a test failure.
- Any diff under runtime, locks, hooks, `.github/`, dependencies or unlisted
  product surfaces is out of scope and stops the front.
- Forgetting to customize `new-project` is an operator error made conspicuous
  by both template documents. It does not grant authority, but the current
  schema cannot make a valid generic template fail solely on that marker; this
  residual is recorded in the threat model.

## Security and privacy

The pack contains no credentials, repository tokens, identities, environment
snapshots or consumer paths. Registry resources describe capabilities but do
not attest availability, authentication or trust. Source-driven commands run
the selected clean source bootstrap and preserve its exact SHA, fixed-object
authority bytes, clean environment and bounded observation contracts.

The main new attacker story is a copied template being treated as customized
governance. Mitigations are the conspicuous `new-project` marker, mandatory
checklist in both project-owned documents, audit-only registry defaults,
external-effect denial, no runtime installation, and no consumer Adoption
Enablement. Project-specific correctness still requires operator review.

## Documentation impact

- Add the governing new-project audit bootstrap runbook.
- Link it from `README.md` and the canonical index.
- Correct RepositorySurveyV2 from pending candidate to integrated governing
  local behavior.
- Record the bootstrap attacker story and residual in the repository threat
  model, then reseal the normalized snapshot footer over final bytes.

No ADR is required because this applies the existing Core/adoption boundary; it
does not change runtime architecture or authorize a new adoption state.

## Verification and closure

The implementation is complete only when:

1. documentation, real source-driven command and preview focal tests are green
   on final bytes;
2. policy, registry, doctor, diff and the exact `tests/run.sh` gate are green;
3. independent spec and quality reviews report zero Critical and zero
   Important findings;
4. the branch is pushed, its PR is integrated under the repository's standing
   Git authority, exact CI is green, and content containment against
   `origin/main` is empty;
5. no external project was mutated and no installation/adoption claim was
   made.

## Rollback

Before integration, revert the branch commits. After squash integration,
revert the squash commit. Because this front adds only project templates,
documentation and tests, rollback requires no runtime migration, target
cleanup, journal recovery or consumer action.

## Deferred fronts

1. Test provenance: classify every test as governing, current focal,
   historical quarantine, release evidence or fail-closed unclassified; migrate
   current hook coverage before deleting anything.
2. Maintenance circuit: expose the existing maintenance budget through the CLI
   and make the verified failure branch consume its reframe.
3. Risk readiness: separate local readiness from authenticated remote proof
   without weakening either status.
4. Core.2 dogfood: complete the fresh ten-task evidence and make the stable
   adoption decision.
5. Consumer canary/install: only after an independently accepted ADR and exact
   native authorization for one named target and source revision.
