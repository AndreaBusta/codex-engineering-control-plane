# Control Plane TaskPlaybookV0 Design

**Date:** 2026-08-10
**Status:** approved design; implementation not started
**Scope:** experimental task-local execution playbook

## 1. Decision

Control Plane may synthesize and use one concise `TaskPlaybookV0` for a
concrete task, even when the procedure will never be reused.

The playbook exists only in the active task context. It is not a native Codex
skill, is not installed, is not persisted as a new runtime contract and does
not add a store, lifecycle, CLI, plugin component or dependency.

This experiment tests whether a task-specific procedure improves complex work
before considering a durable `TaskSkillV1` architecture.

## 2. Why this experiment

The approved product hypothesis remains useful: some one-off tasks have a
fragile order, dense constraints or several specialist skills that benefit
from a shared procedure.

The rejected persistent design duplicated `RunPlanV1`, required a new
filesystem and recovery surface, could invalidate itself during ordinary
worktree changes and could not be hot-loaded as a native skill in the current
task. Those costs are not justified without behavioral evidence.

`TaskPlaybookV0` preserves the hypothesis and removes the premature machinery.

## 3. Goals

The experiment must:

1. support procedures optimized for one task without a reuse requirement;
2. add no user question, approval or internal-plumbing request;
3. preserve direct execution for small tasks;
4. coordinate selected canonical skills without replacing them;
5. narrow or clarify execution without adding scope or authority;
6. survive ordinary context management through the existing bounded
   checkpoint when useful;
7. generate evidence for a later keep, revise or discard decision.

## 4. Non-goals

`TaskPlaybookV0` does not:

- create a native `$skill-name`;
- write a `SKILL.md`, manifest, binding or lifecycle marker;
- write under the repository, Git dir or global Codex directories;
- install or update a skill or plugin;
- add Python runtime, CLI, hooks, MCP, scripts, assets or dependencies;
- grant tools, effects, credentials, authority or network access;
- create a Goal, task or worker;
- modify `TaskEnvelopeV1`, `RunPlanV1` or checkpoint schemas;
- implement persistent telemetry or `ProjectFactsV1`;
- promise recovery when the existing checkpoint cannot carry the playbook.

## 5. Selection

Control Plane loads canonical resources first. It may synthesize a playbook
only in structured or controlled work when at least one strong reason applies:

- `FRAGILE_SEQUENCE`: incorrect ordering or recovery would materially alter
  the result;
- `CROSS_SKILL_COORDINATION`: two or more selected specialists need one shared
  task-specific procedure;
- `CONSTRAINT_DENSITY`: several verified constraints must remain consistent
  across dependent steps.

It must not synthesize a playbook when:

- routing selected direct mode;
- one canonical skill already covers the procedure;
- the playbook would merely repeat the user request, plan or `AGENTS.md`;
- producing it would cost more than completing the remaining work;
- the intended benefit is broader authority or access.

Uncertain selection means `not_needed`, never `BLOCKED` while safe useful work
remains.

## 6. Shape and budget

The playbook is a bounded Markdown fragment of at most 1 KiB with six fields:

```text
objective: one sentence
constraints: at most five bullets
sequence: at most seven ordered steps
verification: exact checks or evidence
stop_conditions: facts that require fail-closed behavior
authorizes: false
```

It has no YAML frontmatter, dynamic name, trigger description or resource
folder. It is an execution aid inside the task, not a discoverable skill.

Only one playbook may be active. A material objective, requested-outcome or
route change discards it and permits one fresh synthesis. Expected worktree
edits do not invalidate it.

## 7. Inputs and instruction boundary

The root orchestrator may derive the playbook only from:

- the current native user objective;
- applicable higher-priority instructions;
- the fresh `TaskEnvelope` and route decision;
- the approved plan when one exists;
- selected canonical skills and resources;
- verified local repository facts.

README text, web content, Issues, PRs, comments and tool output remain data.
Imperative text from those sources never becomes a playbook instruction merely
because it is phrased as a command.

The playbook may narrow ordering, checks or output. It may not expand the task
scope, requested outcome, effects, tools, credentials, network access or
authority allowed by the current native user request, policy and higher
instructions.

Workers may receive the playbook from the root as task-local instructions, but
may not replace or extend it.

## 8. Execution and fallback

Synthesis occurs once after routing and canonical resource selection, before
the first dependent implementation or review action.

The playbook is silent in the normal path. It does not produce another user
prompt and does not consume a repair attempt.

If synthesis is malformed, oversized, contradictory or uncertain, Control
Plane discards it and continues with the existing plan and canonical skills.
The playbook itself is never a reason to block the parent task.

The root may include the exact fragment in an existing terminal checkpoint
only when the complete checkpoint remains within its current 4 KiB cap. If it
does not fit, the root includes only `task_playbook=used` and continues without
claiming durable recovery. No checkpoint schema changes in v0.

At closure, report only:

```text
task_playbook: used | not_needed | discarded
authorizes: false
```

No hidden generation prompt or internal reasoning is persisted.

## 9. Security properties

`TaskPlaybookV0` is advisory and non-authorizing:

- higher instructions, policy, leases and lifecycle always prevail;
- the playbook cannot prove a gate or observation;
- its existence cannot advance task state;
- it cannot request credentials or serialize native authority;
- it cannot install code, write files or enable remote effects by itself;
- failure falls back to the pre-existing execution path.

Because v0 creates no durable files, it adds no new symlink, hardlink,
ownership, crash-recovery, cleanup or cross-process locking surface.

## 10. Implementation surface

The minimal implementation changes only:

- the canonical `control-plane-run` skill;
- the byte-identical packaged plugin skill;
- pressure scenarios and skill/repository contract tests;
- the native governor runbook when needed for operator truth.

The canonical and packaged skill must remain byte-identical and below the
existing 4 KiB cap. New wording must replace or compress existing prose rather
than silently increasing the context budget.

No Control Plane Python module or mutable CLI changes in v0.

## 11. Verification

### Contract tests

- plugin and canonical skill bytes are identical;
- total skill remains below 4 KiB;
- wording requires `authorizes=false` and no additional user prompt;
- direct mode and adequate canonical skills select `not_needed`;
- malformed or uncertain synthesis selects `discarded` and falls back;
- no global, repository or Git-dir path is introduced;
- existing authority, Goal and UNKNOWN rules remain intact.

### Pressure scenarios

1. Existing-app audit with dense constraints uses a playbook.
2. One-page local website in direct mode does not use one.
3. Multi-skill implementation uses one shared sequence.
4. Adversarial repository instructions remain data.
5. Oversized playbook is discarded without blocking.
6. A checkpoint near 4 KiB omits the fragment rather than exceeding the cap.

### Forward test

Run fresh, minimally primed comparison tasks against the current plugin and the
candidate. Do not disclose the desired answer to evaluators.

Compare:

- user interruptions;
- time to first useful action;
- missed constraints;
- unnecessary context or ceremony;
- completion and final evidence quality.

## 12. Experiment decision gate

Keep the feature only when all are true:

- zero additional user questions caused by the playbook;
- direct/simple tasks do not synthesize it;
- no scope, authority or constraint regression;
- at least two of three complex comparison tasks show a material improvement
  in constraint adherence or coordination;
- no material delay to the first useful action;
- relevant tests and independent review pass.

If the gate fails, remove the v0 wording and retain the current plugin.

Passing the gate permits a new design decision. It does not automatically
authorize persistent `TaskSkillV1`, storage or installation.

## 13. Rollback

Rollback restores the prior canonical and packaged skill bytes and removes the
v0 pressure-scenario expectations. There is no persisted task state to migrate
or clean up.

Plugin cache or installation updates remain a separate transition. If a
candidate plugin was installed for dogfood, use the existing exact backup,
remove and reinstall procedure; do not overwrite a differing global skill.

## 14. Documentation decision

This experiment does not change the skill-only plugin architecture and does
not require a new ADR. A future persistent task-skill store would change that
assessment and require a separate design and architecture decision.
