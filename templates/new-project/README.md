# New-project Control Plane starter

This directory is the source-side starting pack for the next repository. This
README stays in the selected clean source bound to an exact integrated commit
and prepared detached for actual first use as the operator guide; it
is never copied over or used to replace the consumer repository's README.
The three authority templates support source-driven audit only. They do not
install Control Plane or authorize consumer adoption.

```text
external_consumer_adoption=PROHIBITED
consumer_adoption_commands=PROHIBITED
source-driven=AUDIT_ONLY
authorizes=false
```

## Before the bootstrap commit

Read this guide from the exact selected source commit. Extract exactly
`AGENTS.md`, `.codex/project-policy.toml` and
`.codex/resource-registry.toml` from that commit into a private staging
directory. Copy exactly `AGENTS.md`, `.codex/project-policy.toml` and
`.codex/resource-registry.toml` from that bound staging, then customize every
generic value before committing.
Never derive governing bytes from the mutable source worktree. Preserve the
consumer's existing README byte-for-byte; never overwrite it with this
source-side guide.

Before copying, follow the governing runbook to require a clean consumer-owned
baseline commit and switch to a non-base work branch. A repository without
`HEAD` is blocked until that baseline is prepared separately. Because these
template instructions are not yet integrated in the protected base, they do
not self-authorize the bootstrap branch or commit; use pre-existing authority
or exact operator authorization.

Before copying, perform one fail-closed precheck across all three destination
paths using no-follow existence semantics equivalent to `os.path.lexists`.
If any destination already exists —regular file, directory, symlink or
dangling symlink— stop with `E_BOOTSTRAP_AUTHORITY_EXISTS`. Write nothing,
preserve every existing byte and open a separately reviewed semantic
reconciliation. Never overwrite or auto-merge existing authority. Copy the
three files only after the precheck proves that all three destinations are
absent.

- replace `new-project` in the project and registry identifiers;
- confirm `project_kind = "generic"` or select the real supported profile;
- confirm the `origin` remote, `main` base and `squash` integration;
- confirm pull-request protection and the ban on direct base pushes;
- confirm the T0-T3 gates, worker budget and sequential default;
- identify the project's real test, build and release-evidence commands;
- review every optional skill and provider resource for actual availability,
  trust, authentication and approval requirements;
- keep the registry in `audit` mode with external effects set to `deny`.

Do not treat schema validity as project-specific correctness. The operator must
review the customized bytes on a non-protected work branch before the
bootstrap commit.

## Boundary

The runtime and this README remain in the separately selected clean Control
Plane source bound to an exact integrated SHA. The three copied authority files include no
runtime, lock, hook, journal, task state or generated receipt. Local audit
artifacts remain `authorizes=false`.

Do not execute `scripts/control-plane-adoption` against the consumer. That
command and every other consumer-adoption command remain prohibited:
`consumer_adoption_commands=PROHIBITED`. Do not run write preflight, install
hooks, create governed task state or mutate the target through Control Plane.

The governing source-driven command sequence belongs to the Control Plane
new-project audit-bootstrap runbook. Until that runbook and the exact source
revision are selected, stop at customization and project-owned review. Any
later canary, installation or stable-adoption transition requires a separate
decision bound to one exact target and source revision.
