# Control Plane v2.1.0 release preparation

## Objective

Leave `v2.1.0` reproducible and release-ready without creating a tag, GitHub
Release, deployment, or publication. The candidate must be bound to the clean
remote `main`, pass the real macOS smoke, and remain explicitly
non-authorizing until a separate publication decision.

## Scope

- make the Python runtime identity equal the locked product version;
- generate a deterministic source tarball, SHA-256 list, release manifest and
  draft receipt from a clean `main == origin/main`;
- make the manual workflow execute the real Darwin smoke and reproduce the
  candidate digest with read-only permissions;
- correct the documented lifecycle command so the runbook is executable;
- add release notes and verification contracts.

No dependency, secret, provider, deployment, tag or release publication is in
scope. The workflow does not upload an artifact through a new action: it emits
the hashes from the trusted remote-base run, and the exact deterministic bytes
can be regenerated for later publication and compared with those hashes.

## TDD sequence

1. Add failing contracts for version identity, candidate determinism, source
   binding, manifest/receipt shape, workflow coverage and the lifecycle CLI.
2. Implement the smallest stdlib-only candidate builder and update the lock.
3. Update the manual workflow, runbook and release notes.
4. Run focused tests, full suite, policy, registry, doctor, inventory, diff and
   release preflight from clean `main` after merge.
5. Obtain independent security and code review, merge through a PR, then run
   the manual workflow on the exact merged commit.

## Threat-model delta

- Candidate source is accepted only when `HEAD`, local `main` and
  `origin/main` are the same commit and the worktree is clean.
- Archive entries come only from `git archive` with a fixed release prefix;
  the builder never extracts input archives or follows user-provided members.
- Output creation is confined to a new directory below an owner-controlled,
  non-shared parent; descriptor and inode bindings reject symlinks,
  substitution races and pre-existing content.
- Manifest and receipt bind commit, tree, workflow URL, PRs, gates, artifact
  size and SHA-256, and always state `authorizes=false` / release unauthorized.
- CI keeps `contents: read`, uses only the already pinned checkout action and
  receives no credentials or release permission.

## Rollback

Before merge, discard the branch. After merge, revert the single squash in a
new reviewed PR. A manual workflow run has read-only repository permissions and
its candidate directory is ephemeral, so rollback is stopping publication;
no tag or release exists until separately authorized.
