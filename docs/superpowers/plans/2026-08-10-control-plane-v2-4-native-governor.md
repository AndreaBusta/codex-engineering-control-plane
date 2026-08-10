# Control Plane v2.4 Native Governor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the smallest native task governor to `control-plane-run` and package the exact proven skill as a thin, versioned local plugin candidate.

**Architecture:** The Codex-facing skill owns only native orchestration guidance: Goal use after an explicit terminal request, at most two workers, one writer, cursor-based waits, bounded checkpoints and safe archive eligibility. Existing Python lifecycle, leases and receipts remain the enforcement kernel. The plugin contains no runtime, scheduler, hook, MCP server or app.

**Tech Stack:** Markdown skills, JSON plugin manifest, Python 3.11 stdlib contract tests, Codex plugin validator, Git.

---

### Task 1: Native governor pressure contract

**Files:**
- Modify: `tests/test_control_plane_run_skill.py`
- Modify: `tests/skill-pressure-scenarios.md`
- Modify: `skills/control-plane-run/SKILL.md`

- [ ] **Step 1: Write failing native-governor tests**

Add tests that require the normalized skill text to contain all of:

```python
required = (
    "petición terminal explícita",
    "goal",
    "máximo dos workers",
    "un solo writer",
    "reutiliza",
    "cursor",
    "espera nativa",
    "checkpoint terminal",
    "authorizes=false",
    "archiva",
    "no queda trabajo",
    "facts_only=true",
    "diez tareas",
    "al menos tres",
    "projectfactsv1",
)
```

Assert that the skill remains below 4096 bytes, does not ask for internal
bindings, does not claim enforcement of native concurrency and contains no new
mutable CLI command.

- [ ] **Step 2: Record the pressure scenarios**

Add three deterministic scenarios to `tests/skill-pressure-scenarios.md`:

1. a long request explicitly says to continue until done;
2. two workers are active and a third possible subtask appears;
3. a worker finishes while another effect or handoff remains pending.

The expected behavior is Goal reuse, no third worker, cursor wait without user
status prompts, and no archive until the terminal checkpoint is complete.

- [ ] **Step 3: Run RED**

Run:

```bash
python3 -m unittest tests.test_control_plane_run_skill -v
```

Expected: the new native-governor and FACTS_ONLY assertions fail because the
current skill does not define them.

- [ ] **Step 4: Implement the minimal skill protocol**

Compress existing wording where necessary and add one `Gobernador nativo`
section. It must state:

```text
explicit terminal request -> create or reuse Goal
root owns outcome
workers <= 2
writers <= 1
reuse matching active worker
wait with latest opaque cursor
questions return to root, not user
terminal checkpoint <= 4 KiB and authorizes=false
archive only terminal + no pending effect + no remaining work
missing host task capability -> UNKNOWN
FACTS_ONLY only for answer + local_read-only
ProjectFactsV1 gate only after 10 tasks and at least 3 FACTS_ONLY
```

Do not add Python APIs or commands.

- [ ] **Step 5: Run GREEN**

Run:

```bash
python3 -m unittest tests.test_control_plane_run_skill -v
python3 -m unittest tests.test_repository_contract -q
```

Expected: both modules pass and the skill remains below 4096 bytes.

### Task 2: Thin plugin package

**Files:**
- Create: `plugins/control-plane/.codex-plugin/plugin.json`
- Create: `plugins/control-plane/skills/control-plane-run/SKILL.md`
- Create: `tests/test_plugin_contract.py`

- [ ] **Step 1: Write the failing plugin contract**

Create `tests/test_plugin_contract.py` and assert:

```python
manifest == {
    "name": "control-plane",
    "version": "3.0.0",
    "description": "Native-governed Control Plane workflows for Codex.",
    "author": {"name": "Codex Engineering Control Plane"},
    "skills": "./skills/",
    "interface": {
        "displayName": "Control Plane",
        "shortDescription": "Run bounded engineering without internal prompts.",
        "longDescription": "Routes verified work, governs native tasks, and preserves host-only authority.",
        "developerName": "Codex Engineering Control Plane",
        "category": "Productivity",
        "capabilities": [],
        "defaultPrompt": "Use $control-plane-run to finish this engineering task safely.",
    },
}
```

Also assert that the packaged skill bytes equal
`skills/control-plane-run/SKILL.md`, and that hooks, MCP, apps, scripts and
assets are absent.

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_plugin_contract -v
```

Expected: failure because the plugin package does not exist.

- [ ] **Step 3: Scaffold with the canonical plugin tool**

Run from the plugin-creator skill root:

```bash
python3 scripts/create_basic_plugin.py control-plane \
  --path /Users/bustaseo/.config/superpowers/worktrees/codex-engineering-control-plane/control-plane-v2-3/plugins \
  --with-skills
```

Do not create a marketplace entry in this task.

- [ ] **Step 4: Apply the exact manifest and skill**

Replace the scaffold manifest with the exact object from Step 1 and copy the
canonical skill byte-for-byte to
`plugins/control-plane/skills/control-plane-run/SKILL.md`.

- [ ] **Step 5: Run GREEN and the official validators**

Run:

```bash
python3 -m unittest tests.test_plugin_contract -v
python3 /Users/bustaseo/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/control-plane
python3 /Users/bustaseo/.codex/skills/.system/skill-creator/scripts/quick_validate.py plugins/control-plane/skills/control-plane-run
```

Expected: all pass with no placeholders or unsupported components.

### Task 3: Operating contract and integrated verification

**Files:**
- Create: `docs/engineering/18-native-governor-plugin.md`
- Modify: `README.md`
- Modify: `tests/test_repository_contract.py`
- Modify: `.codex/control-plane.lock`

- [ ] **Step 1: Write failing repository-contract tests**

Require the design, plan, runbook and plugin manifest. Assert the runbook says:

```text
skill-only advisory governor
no scheduler
maximum two workers
one writer
cursor-based wait
archive only after terminal checkpoint
FACTS_ONLY 10/3 gate
duplicate global skill fail-closed
transactional install and rollback
version 3.0.0 is a plugin candidate, not a release
```

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m unittest tests.test_repository_contract -v
```

Expected: the new runbook/link assertions fail.

- [ ] **Step 3: Write the minimal runbook and README link**

Document initial installation as a separate operation: inventory personal
marketplace and duplicate global skills, compare digests, back up only exact
state being replaced, validate/reinstall through the official plugin CLI, open
a fresh task, and restore exact prior bytes on rollback. Do not claim that the
plugin is installed or released in this task.

- [ ] **Step 4: Refresh the runtime lock**

Recompute `control_plane.lockfile.runtime_digest(repo)` and update only the
`runtime` digest in `.codex/control-plane.lock`. Validate it with
`tests.test_lockfile`.

- [ ] **Step 5: Run integrated verification**

Run:

```bash
python3 -m unittest \
  tests.test_control_plane_run_skill \
  tests.test_plugin_contract \
  tests.test_repository_contract \
  tests.test_lockfile -q
bash tests/run.sh
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check
git status --short --branch
```

Expected: all tests and deterministic gates pass. No dependency, secret, CI,
remote Git or release state changes.

- [ ] **Step 6: Commit the local v2.4/plugin candidate**

After fresh review and a clean staged diff check, create a local commit only:

```bash
git commit -m "feat(control-plane): add v2.4 native governor plugin"
```

Do not push, open a PR, merge, deploy or release.
