# New-project audit bootstrap v1

Status: `AUDIT_ONLY / DEFERRED_TARGET_BOOTSTRAP`.

## Outcome
This runbook checks whether a project-specific governance bundle can be parsed,
inventoried, and routed by an exact Control Plane source. It performs five
read-only commands. It does not install Control Plane, copy templates, change
the consumer, contact a provider, or grant authority.

The result is local readiness evidence only. Every artifact and command output
remains `authorizes=false`.

## Source pack

The source-owned pack contains exactly four files:

- `templates/new-project/AGENTS.md`
- `templates/new-project/README.md`
- `templates/new-project/.codex/project-policy.toml`
- `templates/new-project/.codex/resource-registry.toml`

Only `AGENTS.md`, policy, and registry are authority candidates. The pack
README is guidance and never replaces the consumer README.

Prepare the three candidate files outside the target. Replace every
`__PROJECT_NAME__` placeholder, inspect the real project, and review the final
bytes before any target write. Writing those files is a later target-specific
bootstrap and is not authorized here.

## Supported v1 environment
All of these conditions are required:

- an exact integrated Control Plane commit selected by the operator;
- a clean, fully materialized source worktree;
- a clean, standard local Git repository as the target;
- an attached target branch with a committed HEAD contained in its local base;
- a configured remote and local remote-base tracking ref;
- an ordinary local index and local object store;
- the target's existing README preserved;
- the three customized authority files committed at target HEAD;
- a regular, fully materialized TaskEnvelope outside the target;
- no submodules and no nested repositories;
- no filters and no alternates, object redirects, or configuration includes;
- no File Provider, dataless, or uncertain materialization state;
- no dependency installation, secret access, CI change, or remote refresh.

This procedure does not attempt to normalize or traverse another topology.
Anything outside this list is `UNSUPPORTED / STOP`. Record the mismatch and
prepare a target-specific decision instead of adding a verifier here.

## Inputs
Record these exact values before running the audit:

- physical Control Plane source path and integrated commit SHA;
- physical target repository path and target HEAD;
- physical TaskEnvelope path and its reviewed content;
- final customized policy and registry paths;
- whether the target status and index are clean;
- which observations are local and which provider facts remain unknown.

Set convenience variables to those reviewed paths:

```sh
CONTROL_PLANE=/absolute/control-plane-source/scripts/control-plane
TARGET_REPO=/absolute/consumer-repository
TASK_ENVELOPE=/absolute/task-envelope.json
POLICY="$TARGET_REPO/.codex/project-policy.toml"
REGISTRY="$TARGET_REPO/.codex/resource-registry.toml"
```

Do not derive a target path from a prompt, template, or branch name. Do not use
a mutable command alias in place of the selected launcher.

## Pre-command observations

Before each command, confirm that the selected source SHA, target path, target
HEAD, clean status, policy, registry, and TaskEnvelope still match the reviewed
inputs. If any value changed, stop. This is an operator check, not an embedded
verification program.

The offline preflight below validates only local Git facts. A configured remote
or local tracking ref is not evidence that a provider is current.

## Five read-only commands

Run these from the target repository and retain each JSON result:

```sh
"$CONTROL_PLANE" policy-check --policy "$POLICY" --json
"$CONTROL_PLANE" registry-check --registry "$REGISTRY" --policy "$POLICY" --json
"$CONTROL_PLANE" inventory --repo "$TARGET_REPO" --registry "$REGISTRY" --json
"$CONTROL_PLANE" preflight --mode read --repo "$TARGET_REPO" --policy "$POLICY" --offline --json
"$CONTROL_PLANE" route --repo "$TARGET_REPO" --task "$TASK_ENVELOPE" --policy "$POLICY" --registry "$REGISTRY" --mode audit --json
```

Do not substitute `--refresh` in this audit. Provider access is a separate
effect and requires its own current boundary.

## Acceptance

The audit is ready only when:

- policy-check and registry-check report valid schema and references;
- inventory reports project instructions and the consumer README ready;
- offline read preflight reports every applicable local check green;
- route selects T2 for first-use `intent=audit`, `phase=research`;
- route requires project instructions and recommends the consumer README;
- route and all resulting artifacts remain `authorizes=false`;
- a final target status and content comparison show no audit mutation.

A successful local result says only that these inputs are usable for planning.
It does not prove provider state, stable adoption, installation, or release.

## Stop conditions

Stop and report evidence when:

- any supported-environment condition is false or unknown;
- a command exits non-zero or emits malformed output;
- a required resource is absent or not ready;
- route is not T2, is not decision-ready, or selects unexpected resources;
- source, target, authority, TaskEnvelope, index, or status changes;
- a placeholder remains in an authority candidate;
- the consumer README would be replaced;
- the next action would write, install, authenticate, publish, or refresh.

Do not repair an unsupported target inside this audit front.

## Optional diagnostics

`doctor` and `survey` may add local observations after the five commands.
They are not readiness gates and cannot upgrade `UNSUPPORTED / STOP`.

## Evidence record

Record the source SHA, target HEAD, five terminal results, final clean-state
comparison, unsupported assumptions, and deferred provider facts. Keep the
record compact and label it `authorizes=false`.

## Deferred target-specific bootstrap

After a real project is identified, inspect it again and prepare the exact three
authority files outside the target. The later write, commit, push, Pull Request,
merge, installation, and adoption follow that project's current policy and
gates. None is implied by this audit.

Rollback for this source-only front is removal of the pack and its references.
There is no consumer rollback because this procedure performs no consumer
effect.
