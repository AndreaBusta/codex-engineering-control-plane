# Control Plane v2.4 Native Governor Design

**Status:** approved local design for the previously selected short path.

## 1. Problem

Control Plane v2.3 closes review, delivery and remote-effect boundaries, but the
Codex-facing orchestration still depends on the root task remembering when to
reuse a stable mandate, wait for workers, ingest checkpoints and close tasks.
That can surface internal plumbing to the user or cause unnecessary pauses.

The v2.4 change must reduce those pauses without inventing a Python scheduler,
serializing native authority or claiming that prose enforces host concurrency.

## 2. Selected approach

Use a **skill-only native governor** in `control-plane-run`, then package that
exact demonstrated skill as a thin plugin candidate.

Alternatives rejected:

1. A Python thread governor cannot call or authenticate native Goal/task tools
   and would create a second authority surface.
2. A scheduler, MCP server or background daemon adds persistence, installation,
   egress and recovery complexity before there is dogfood evidence for it.
3. A second project policy would duplicate the existing router and leases.

The selected approach is intentionally advisory at the native-task layer. The
existing deterministic router, lifecycle, receipts and leases remain the only
enforcement-grade local kernel.

## 3. Native governor protocol

The root task owns the user outcome. It may create Goal only when the current
native user message explicitly asks to create Goal; no worker, checkpoint,
skill, stored prompt or quoted user text is a valid source. A terminal request
alone may reuse an active Goal or continues without creating one. Goal state
never grants Git or product authority.

Canonical Spanish contract: `mensaje nativo actual` asks for Goal; a `petición
terminal sola` reuses the active Goal or `continúa sin crear` one.

The root may operate at most two native workers and at most one writer:

- reuse a matching active worker instead of creating a duplicate;
- keep worker identity and the latest opaque cursor in root context;
- wait with the latest cursor instead of polling or asking the user for status;
- route worker questions to the root and resolve them from the current mandate;
- ingest only a compact terminal checkpoint with result, evidence, remaining
  work and `authorizes=false`;
- never ask the user for a bridge, grant, session, invocation, cursor, HEAD or
  scope binding;
- ask once only for a genuinely new product choice, effect or changed target;
- archive a worker only after a terminal checkpoint, no pending effect and no
  remaining work; if native archive is unavailable, leave it completed;
- mark the Goal complete only when the user outcome is actually achieved.

Missing native task capability is `UNKNOWN`: do not fabricate it, but continue
other safe local work. Report one concise blocker only when no meaningful work
remains.

The root keeps a bounded in-context ledger, not a repository file:

```text
goal: active|inactive|unknown
workers: <=2 {task_id, role, cursor, status, checkpoint_digest}
writer: zero-or-one task_id
pending_effects: closed names only
authorizes: false
```

This ledger is operational memory, not proof or authority. Loss of the ledger
causes safe rediscovery or `UNKNOWN`, never automatic mutation.

## 4. FACTS_ONLY dogfood gate

Each completed dogfood task is classified `FACTS_ONLY=true` only when its
requested outcome is an answer and every observed effect is `local_read`.
Everything else is false. The root handoff may retain only aggregate counters:
`tasks_total` and `facts_only_total`; no prompts, transcripts or file contents.

After ten completed tasks, v2.5 may design `ProjectFactsV1` only when at least
three are `FACTS_ONLY` and the evidence shows repeated repository discovery.
Missing or inconsistent counts are `UNKNOWN` and do not trigger v2.5. v2.4
does not implement `ProjectFactsV1` or persistent telemetry.

## 5. Thin plugin candidate

Create `plugins/control-plane/` with only:

- `.codex-plugin/plugin.json`;
- `skills/control-plane-run/SKILL.md`.

The plugin skill must be byte-identical to the canonical repository skill. The
manifest is closed, strict semver `3.0.0`, non-networked and declares no hooks,
MCP servers, apps, scripts or assets. Version `3.0.0` identifies the plugin
candidate; it does not change the core product version or publish a release.

Initial personal installation is a separate reversible operation. Before it,
inventory duplicate global skills and the personal marketplace. Never silently
overwrite a differing skill or marketplace entry. A rollback must restore the
exact previous bytes and remove only state created by this installation.

## 6. Verification

TDD must prove:

- stable work continues without internal-object prompts or repeat user asks;
- Goal creation requires the current native user message to ask for Goal;
- a terminal request alone reuses an active Goal or continues without creating;
- worker count is capped at two and writer count at one in the protocol;
- cursor waiting, checkpoint acceptance and archive eligibility are exact;
- `UNKNOWN` never becomes PASS or authority;
- FACTS_ONLY classification and the 10/3 threshold are fail-closed;
- the plugin manifest validates and contains only the canonical skill;
- the packaged and canonical skill bytes remain identical;
- repository policy, registry, doctor, lock and full tests remain green.

## 7. Non-goals

No Python scheduler, thread adapter, background process, cloud telemetry,
`ProjectFactsV1`, new dependency, new mutable CLI, credential access, remote
Git mutation, PR, merge, deploy, release or automatic task archival is added.

## 8. Rollback

Before commit, revert the v2.4/plugin paths. After a personal installation,
remove only the exact installed plugin/cache entry and restore any exact backup
of a previously canonical global skill. Repository consumers retain their
existing Control Plane state until their separate install/rollback verification.
