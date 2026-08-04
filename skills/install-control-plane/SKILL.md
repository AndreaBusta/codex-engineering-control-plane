---
name: install-control-plane
description: Use when the user explicitly says "instala Control Plane", "instalar Control Plane", or "install Control Plane" for a repository. Do not use for questions, comparisons, audits, or informational mentions.
---

# Install Control Plane

Install only the approved project-local release from
`AndreaBusta/codex-engineering-control-plane` at tag `v2.1.1`. A later version
is unknown until this skill is updated and must not be selected automatically.

## Authority

The install request authorizes local read, a safe local branch, verified
download of the four official assets, `adopt plan`, `adopt apply`, `adopt
verify`, project gates, one `adopt rollback` trial, and a fresh plan, reapply,
and verify. For an existing installation use `upgrade plan` and `upgrade apply`
instead of a second adoption.

This request does not authorize commit, push, Pull Request, merge, deploy,
release, dependencies, plugins, or secrets. It never authorizes overwriting
unrelated work, changing remotes, weakening project gates, trusting hooks, or
global installation.

## Workflow

1. Resolve one repository target. Inspect its instructions, Git root, worktree,
   branch, HEAD, remote, base, and status before writing.
2. Stop on a dirty target, ambiguous target, unknown version, unverified source,
   missing initial commit, missing remote, protected base branch, or unclear
   existing hooks. Report the exact condition without making partial changes.
3. Create a feature branch from the refreshed remote base. Do not change the
   base branch directly.
4. Download exactly the `v2.1.1` tarball, `SHA256SUMS`, manifest, and receipt
   from the canonical GitHub Release. Verify the checksum, source commit, tag,
   release-source marker, and `authorizes=false` before extraction or use.
5. Run `adopt plan` or `upgrade plan` with explicit source and target paths.
   Review its JSON; proceed only when it is clean, unambiguous, and `ok=true`.
6. Run the matching apply command, then `adopt verify` and the repository's own
   gates. Review `/hooks` manually; never bypass hook trust.
7. For a fresh adoption, run one rollback trial and prove exact restoration.
   Then create a new plan, reapply, verify, and rerun the project gates.
8. Stop with a reviewable diff, verification evidence, remaining manual hook
   trust, and a continuation pointer. Leave every Git or remote transition for
   separate authority.
