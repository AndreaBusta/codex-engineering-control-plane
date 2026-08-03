# Supported adoption v2.1

## Goal

Turn the existing transactional adoption and upgrade APIs into one supported,
novice-usable local-audit path without adding a global installer, dependency,
workflow, provider, or second runtime.

## Scope

- Document one sequence from clean Control Plane source through read-only plan,
  reviewed apply, verify, project gates, manual hook trust, exact rollback,
  re-application, and a normal project Pull Request.
- Add a separate acceptance module for a generic repository, an iOS-marked
  repository, and an isolated BUSTAFIT-shaped hybrid clone.
- Exercise the public CLI contracts `adopt plan/apply/verify/status/rollback`
  and `upgrade plan/apply`; keep their interface unchanged.
- Prove profile detection and routing, installed-runtime isolation, Git guards,
  exact pre-commit rollback, and upgrade preservation of the original rollback.

## TDD and gates

1. Add the runbook and end-to-end acceptance contracts.
2. Observe RED because the supported sequence and its safety boundaries are
   absent from the lifecycle guide.
3. Add the minimum runbook section and update the adoption status wording.
4. Run the runbook contract, then all three acceptance scenarios.
5. Exercise a fresh isolated clone of the real BUSTAFIT `main` without writing
   to its canonical checkout.
6. Run the full repository gates and independent review before any commit or
   remote transition.

## Threats and rollback

- A dirty, unborn, protected-branch, ambiguous-remote, or conflicting-hook
  target remains fail-closed through the existing plan preflight.
- Plans are immutable and target-specific; apply revalidates them.
- A pre-release journal without the exact created-directory inventory is not
  upgraded or rolled back by the new runtime; it fails closed and requires
  rollback with its installed version followed by fresh adoption.
- Tests use temporary repositories only and never share hooks or state with a
  real project.
- No secret, transcript, dependency, workflow, deploy, or release enters the
  flow.
- Reverting this change removes the acceptance module and the two documentation
  deltas; the public runtime remains unchanged.

`adopt rollback` restores managed files, modes, snapshots, and the prior local
hook configuration. It deliberately does not rewrite Git history or reverse an
external effect.
