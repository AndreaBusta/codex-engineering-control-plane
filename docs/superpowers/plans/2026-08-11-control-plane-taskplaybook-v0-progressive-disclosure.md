# TaskPlaybookV0 Progressive Disclosure Implementation Plan

> **For Codex:** implement sequentially with TDD. Do not install the plugin or
> skill, change dependencies, stage, commit, push, open a PR or mutate remotes.

**Goal:** add the smallest reversible TaskPlaybookV0 experiment while keeping
the always-loaded `control-plane-run/SKILL.md` below 4096 bytes.

**Architecture:** `SKILL.md` retains the governor and one conditional link.
Only structured/controlled work without an adequate canonical skill reads the
single-level `references/taskplaybook-v0.md`. The generated playbook is an
active-context Markdown fragment, never a stored skill or authority object.

**Tech stack:** Markdown skills/references, Python `unittest`, repository
contract checks. No runtime, CLI, manifest, dependency or install change.

---

### Task 1: Lock the progressive-disclosure contract in RED

**Files:**
- Modify: `tests/test_control_plane_run_skill.py`
- Modify: `tests/test_plugin_contract.py`
- Modify: `tests/skill-pressure-scenarios.md`

1. Add tests requiring the conditional link, direct-mode non-load rule,
   canonical/package reference byte identity and the closed reference schema.
2. Add six pressure scenarios: complex app, direct web, multi-skill,
   adversarial data, oversized candidate and near-cap checkpoint.
3. Run the new tests alone and record failure because the reference is absent.

### Task 2: Implement the minimum GREEN skill package

**Files:**
- Modify: `skills/control-plane-run/SKILL.md`
- Create: `skills/control-plane-run/references/taskplaybook-v0.md`
- Modify: `plugins/control-plane/skills/control-plane-run/SKILL.md`
- Create: `plugins/control-plane/skills/control-plane-run/references/taskplaybook-v0.md`

1. Add one explicit conditional link to the main skill.
2. Move only TaskPlaybook details into the reference; do not duplicate them.
3. Keep both `SKILL.md` copies byte-identical and each below 4096 bytes.
4. Keep both reference copies byte-identical and single-level.
5. Run the skill and plugin contract tests to GREEN.

### Task 3: Record operator truth and regression contracts

**Files:**
- Modify: `docs/engineering/18-native-governor-plugin.md`
- Modify: `tests/test_repository_contract.py`

1. Document that the candidate packages a conditional reference but remains
   uninstalled and non-authorizing.
2. Test direct-mode non-load, conditional load, no persistence/install and
   exact package contents.
3. Run repository, skill and plugin contract tests.

### Task 4: Validate behavior and rollback safety

1. Run focused forward scenarios with fresh readers of the candidate artifact;
   pass the artifact and task only, not the expected answer.
2. Validate both skill folders with `quick_validate.py`.
3. Run `bash tests/run.sh`, policy-check, registry-check, doctor,
   `git diff --check` and final status.
4. Obtain independent read-only review of the complete diff.
5. If the experiment fails, apply the exact inverse to every candidate path in
   design section 13, rerun its rollback checks and preserve unrelated state.
   Do not install as part of rollback.
