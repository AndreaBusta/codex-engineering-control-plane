# New-project governance bootstrap

## Customize before the bootstrap commit

Replace every occurrence of `new-project` with the real project identity and
review `project_kind`, Git remote, base branch, integration strategy, gates,
resources, test commands, build commands and release evidence. This bootstrap
must not be committed to a real project while the generic identity remains.

```text
external_consumer_adoption=PROHIBITED
consumer_adoption_commands=PROHIBITED
source-driven=AUDIT_ONLY
authorizes=false
```

## Supported first use

Control Plane is used only as a source-driven, read-only audit from the
separately selected canonical source checkout. This pack is project-owned
governance; it is not a runtime installation, adoption receipt, canary or
provider proof.

Do not run `scripts/control-plane-adoption` against this consumer repository.
Do not run write preflight, task lifecycle, hook installation, Git guards or
any other consumer-adoption command. Missing local or remote evidence remains
`UNKNOWN` or blocked; a local result never proves provider state.

## Engineering

- Observe cwd, Git root, worktree, branch, HEAD, base and status before edits.
- Distinguish observed evidence from inference. Never call a skipped or
  unavailable gate green.
- Use a non-protected work branch. Never write directly to the protected base.
- Apply TDD to behavior: reproduce RED, implement the minimum GREEN change,
  then run the applicable gates without weakening tests.
- Keep scope explicit, preserve unrelated work and avoid destructive Git,
  force push and automatic branch cleanup.
- Do not add dependencies, read secrets or change CI without exact
  transition-specific authorization.

## Git authority

This standing Git authority exists only when these instructions are already
integrated in the protected base. An edit on a work branch does not authorize
itself. Before these instructions are integrated, the bootstrap branch and
commit require pre-existing authority or exact operator authorization.

On a work branch that is not the protected base:

- Commit requires the applicable local gates over the current bytes; it does
  not require remote CI.
- Push, opening or updating a Pull Request, and merge require fresh
  host/provider observation of repository identity, source and target refs,
  their protection, the PR and required checks. Absent, stale or `UNKNOWN`
  evidence stops the transition.
- The confirmed absence of a remote work ref authorizes only creation of that
  exact ref by the first push. It proves neither PR state nor merge readiness.
- Merge requires required CI to be terminal green for the exact head, plus
  exact source, target, protection and mergeability evidence.

Deploy, release, publication, dependency installation, CI changes and secret
handling still require explicit authorization for that exact transition. A
merge that would automatically trigger deploy, release, publication,
dependency installation, a CI change or secret handling is not covered by the
standing Git authority and requires that separate authorization before merge.
A task, plan, checkpoint, receipt or generated artifact is `authorizes=false`.
