---
name: install-control-plane
description: Use when the user explicitly says "instala Control Plane", "instalar Control Plane", or "install Control Plane" for a repository. Do not use for questions, comparisons, audits, or informational mentions.
---

# Install Control Plane

By default, install only the approved project-local release from
`AndreaBusta/codex-engineering-control-plane` at tag `v2.1.1`. A later version
is unknown until this skill is updated and must not be selected automatically.

## Authority

The install request authorizes local reads, one safe branch per exact target,
verified official assets, `adopt plan`, `adopt apply`, `adopt verify`, project gates, one
`adopt rollback`, then a fresh plan, reapply and verify. Existing installations use
`upgrade plan` and `upgrade apply`.

This request does not authorize commit, push, Pull Request, merge, deploy,
release, dependencies, plugins, or secrets. It never authorizes overwriting
unrelated work, changing remotes, weakening project gates, trusting hooks, or
global installation.

## Low-context fast path

- Run a diagnostic Git precheck for root, initial commit, HEAD, branch, status
  and remote before loading project documentation. If dirty, emit
  `BLOCKED_DIRTY_TARGET`; stop before network, branch or adoption commands.
- The default source mode is `verified-release`. `local-candidate` is
  allowed only with explicit user authority, an absolute source path, exact
  targets, and the source path, HEAD, and manifest digest. Require a clean
  source, verify once, and use no network. `local-candidate` skips the release
  download step.
- For multiple repositories, process one target at a time. Never share plans,
  leases, or state between targets.
- Put complete JSON and gate logs in temporary files and surface at most 4 KiB
  per command: status, stable error codes, warnings, digests and the relevant
  failure tail. Reuse a verified download or source only when its input digests
  are unchanged; always regenerate target plans after rollback or target drift.

## Workflow

1. Resolve one repository target and run the fast precheck. Only for a clean
   target, inspect its applicable instructions and directly relevant install
   documentation before writing.
2. Stop on a dirty target, ambiguous target, unknown version, unverified source,
   missing initial commit, missing remote, protected base branch, or unclear
   existing hooks. Report the exact condition without making partial changes.
3. Create a feature branch from the refreshed remote base. Do not change the
   base branch directly.
4. In verified-release mode, download exactly the `v2.1.1` tarball,
   `SHA256SUMS`, manifest, and receipt from the canonical GitHub Release. Verify
   checksum, source commit, tag, release-source marker, and `authorizes=false`.
5. Run `adopt plan` or `upgrade plan` with explicit source and target paths.
   Review its JSON; proceed only when it is clean, unambiguous, and `ok=true`.
6. Run the matching apply command, then `adopt verify` and the repository's own
   gates. Review `/hooks` manually; never bypass hook trust.
7. For a fresh adoption, run one rollback trial and prove exact restoration.
   Then create a new plan, reapply, verify, and rerun the project gates.
8. Stop with a reviewable diff, verification evidence, remaining manual hook
   trust, and a continuation pointer. Leave every Git or remote transition for
   separate authority.
