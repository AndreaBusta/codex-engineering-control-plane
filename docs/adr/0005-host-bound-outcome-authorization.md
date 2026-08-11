# ADR 0005: Host-bound outcome authorization

Status: accepted for implementation

## Context

The local kernel can validate plans, reviewed subjects, Git state and durable
receipts, but none of those serializable artifacts can prove a current user
decision. The same distinction must survive commit, push, Pull Request and an
optional squash merge without asking the user to operate internal machinery.

The production repository does not expose a native host adapter to Python.
Test-only adapters demonstrate the contract but do not make the remote path
available in an installed product.

## Decision

Outcome authority is host-bound and per-effect. It remains opaque in current
host memory, is consumed once at the effect boundary and is checked against the
current task, repository, action and reviewed subject. The Python kernel accepts
only non-authorizing plans, evidence and receipts on the v2.3 outcome path. In
short: evidence is not authority.

A stable product request may carry its ordinary chain forward without repeated
prompts. Pull Request readiness is the default boundary. Integration requires a
fresh, current and exact product-level request through squash merge. Drift or a
new effect requires one concise product-level reauthorization. Missing native
host capability is `BLOCKED`, never an instruction to expose implementation
objects to the user.

## Alternatives

### Serializable grants

Rejected. Serializable grants can be copied, replayed, redigested or moved
between tasks and sessions. Expiry and signatures do not solve confused-deputy
use after subject drift, and the kernel must not become an authority issuer.

### A second agent

Rejected. A second agent would duplicate orchestration while still lacking a
trusted user-interaction boundary. Its messages would be evidence, not current
authority, and would add another replay and prompt-injection surface.

### Mutable CLI

Rejected. A mutable CLI for commit, push, PR, merge, retry or authorization
would turn shell input or serialized files into a competing control surface.
The supported CLI remains `run prepare`, `run verify`, `run status` and `run
block`; remote mutations stay outside the Python product.

## Consequences

- Plans and receipts are closed, digest-bound and `authorizes=false`.
- Review evidence cannot authorize staging; PR evidence cannot authorize merge.
- An uncertain remote write is observed before any retry and never repaired
  automatically.
- Host absence blocks the remote path even when every local contract passes.
- Local guards reduce mistakes but do not provide GitHub branch protection.

## Recovery and security

See the
[v2.3 threat model](../security/2026-08-08-v2-3-outcome-bridge-threat-model.md)
and the
[outcome bridge rollback runbook](../engineering/16-outcome-bridge-rollback.md).
