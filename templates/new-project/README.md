# New-project audit readiness pack

This directory is source-owned material for preparing governance for an
identified project. It is not an installer and must not be copied wholesale
into a consumer repository.

The exact source pack has four files:

- `AGENTS.md`
- `.codex/project-policy.toml`
- `.codex/resource-registry.toml`
- this source-side `README.md`

Only the first three are authority candidates. Before any target write, prepare
their project-specific final bytes outside the target, replace every
`__PROJECT_NAME__` placeholder, review them against the real repository, and
validate them with the canonical source launcher. Preserve the consumer's own
`README.md`; this guide never replaces it.

Adding even customized files to a target is a separate, target-specific
transition. It is deliberately absent from this pack and requires the authority
and gates of that project. All generic pack bytes remain `authorizes=false`.

The governing audit-only procedure is
[`docs/engineering/23-new-project-audit-bootstrap.md`](../../docs/engineering/23-new-project-audit-bootstrap.md).
