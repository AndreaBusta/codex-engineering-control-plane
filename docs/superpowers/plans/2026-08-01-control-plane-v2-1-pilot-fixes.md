# Control Plane v2.1 pilot fixes

## Objective

Correct the two operability defects found by the isolated BUSTAFIT pilot without
expanding v2.1 beyond the local-audit kernel:

1. an applied, uncommitted adoption with a complete task lease must remain
   fail-closed but report `UNKNOWN`, never `PASS`, when native host attestation
   is unavailable;
2. an adopted target must not inherit the Control Plane source repository's
   `bash tests/run.sh` command.

The router classifies this patch as T3/controlled and recommends `/plan`
followed by `/goal`, sequential execution, with at most two workers. No mode is
changed automatically.

## Security contract

- A clean worktree remains `PASS` for `RS_LOCAL_DIRTY`.
- A dirty worktree without an exact task, session, policy, branch and scope
  match remains `FAIL`.
- A blocked, closed or finalizing task cannot keep writer continuity even if a
  stale lease file remains.
- A dirty worktree covered by a locally validated lease but lacking native host
  attestation becomes `UNKNOWN`; local data can never promote it to `PASS`.
- Serialized route, task, lease or authorization JSON never becomes host
  authority.
- Existing host-bound evidence remains the only path to `PASS` for a dirty
  leased worktree.
- Staged renames are observed as delete plus add, so a cross-scope move cannot
  hide its source path.
- Adoption guidance delegates product tests to the target repository and lists
  only distributed Control Plane gates explicitly.

## TDD sequence

1. Add RED tests for the new CLI lease-binding contract, cross-scope staged
   renames and target-owned verification commands.
2. Observe the risk test fail with `FAIL/1` and the adoption test fail because
   the rendered block contains `bash tests/run.sh`.
3. Implement the smallest closed local observation needed to produce
   `UNKNOWN/2`; keep absent, mismatched, partial and forged leases at `FAIL/1`.
4. Render a target-safe verification section without guessing product commands.
5. Run focused tests, affected suites, full suite, policy/registry/doctor and
   Darwin smoke.
6. Obtain independent security and maintainability reviews.

## Integration and rollback

The patch is one coherent commit on `codex/local-audit-pilot-fixes`. After a
green PR and squash merge, refresh `origin/main`. The isolated BUSTAFIT pilot is
dirty by design, so it must not use `upgrade plan`: the owning installed runtime
first suspends the old task for reframe and releases its lease, then performs an
exact rollback. Re-adopt from the clean merged source, create a fresh task and
lease owned by that installed runtime, and repeat `adopt verify`, `risk-status`,
product gates and independent review.

Rollback is a GitHub revert of the squash for the Control Plane patch. The
BUSTAFIT pilot remains recoverable through its existing adoption WAL and exact
rollback. A failed rollback or non-byte-exact restoration blocks re-adoption;
no product file, dependency, secret, workflow, deploy or release is in scope.
