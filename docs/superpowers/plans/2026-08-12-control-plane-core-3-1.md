# Control Plane Core 3.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `3.1.0-core.1`, a small local-first Control Plane that preserves policy, routing, Git safety, task ownership, proportional verification, and non-authorizing handoffs while structurally quarantining the failed Advanced runtime.

**Architecture:** The active package is an exact allowlist with no import path to Advanced lifecycle, remote host bridges, delivery workflows, candidate receipts, or self-verification. Local changes use `CoreTaskStateV1`, revision-scoped generational leases, one closed toolchain context, a repository-wide verification mutex, and a one-reframe maintenance circuit breaker. Existing Advanced state and installed generations remain read-only and recoverable; blocked compatibility commands return a stable non-authorizing error without importing or mutating Advanced state.

**Tech Stack:** Python 3 standard library, Bash launchers, Git plumbing, TOML/JSON contracts, `unittest`; no new dependency.

**Authority boundary:** This plan authorizes only local edits and local verification in `codex/control-plane-core-3-1`. It does not authorize commit, installation, upgrade, push, PR, merge, deploy, publication, or release.

---

## File responsibility map

Active runtime:

- `control_plane/contracts.py`: closed `TaskEnvelope` and shared canonical JSON.
- `control_plane/core_types.py`: inert host observations and trusted in-process seals needed by policy/routing; no remote adapter or egress.
- `control_plane/task_state.py`: `CoreTaskStateV1`, state transitions, legacy inventory.
- `control_plane/leases.py`: revision/generation writer leases and immutable release receipts.
- `control_plane/toolchain.py`: `ClosedExecutionContextV1` and absolute Python/Git/Node attestation.
- `control_plane/verification.py`: bounded local gates and repository-wide mutex.
- `control_plane/maintenance.py`: `MaintenanceLineageV1` and one-reframe circuit breaker.
- `control_plane/adoption_recovery.py`: read-only status/verify and exact rollback of already installed generations.
- Existing focused modules keep policy, registry, routing, Git/preflight, scopes, hooks, guards, intake, profiles, and materialization.
- `control_plane/cli.py`: lazy dispatch, Core commands, and zero-mutation quarantine stubs.
- `control_plane/lockfile.py`: schema 2 exact module allowlist and digest.

Removed from the active package and distribution (recoverable at `origin/main@929d3f8`):

- `control_plane/host_bridge.py`
- `control_plane/lifecycle.py`
- `control_plane/run_workflow.py`
- `control_plane/candidate_receipt.py`
- `control_plane/release_source.py`
- full write-capable `control_plane/adoption.py`

## Task 1: Freeze contracts and compatibility in RED

**Files:**

- Create: `tests/test_core_contract.py`
- Create: `tests/test_core_cli.py`
- Create: `tests/test_core_lockfile.py`
- Create: `tests/test_core_quarantine.py`

- [ ] Add a contract test asserting product version `3.1.0-core.1`, lock schema 2, and active Python LOC `<= 21530`.
- [ ] Add import-graph tests asserting no active module contains/imports `host_bridge`, `lifecycle`, `run_workflow`, `candidate_receipt`, or `release_source`.
- [ ] Add CLI snapshot tests for `policy-check`, `doctor`, `preflight`, `registry-check`, `inventory`, `route`, `risk`, `safe-read`, guards, `task`, `run`, `report`, `verification-run`, `adopt`, and `upgrade`.
- [ ] Assert quarantined actions exit 2 with this minimum JSON:

```python
assert payload["ok"] is False
assert payload["error_code"] == "E_CAPABILITY_QUARANTINED"
assert payload["authorizes"] is False
```

- [ ] Snapshot the target state directory before and after every quarantined action and assert byte-for-byte equality.
- [ ] Run `python3 -m unittest tests.test_core_contract tests.test_core_cli tests.test_core_lockfile tests.test_core_quarantine -v`; expect failures because the Core modules and schema 2 do not exist.

## Task 2: Exact runtime inventory and structural quarantine

**Files:**

- Modify: `control_plane/__init__.py`
- Modify: `control_plane/lockfile.py`
- Modify: `scripts/control-plane`
- Modify: `.codex/control-plane.lock`
- Delete from active tree: Advanced files listed above

- [ ] Define `ACTIVE_RUNTIME_MODULES` once in `lockfile.py` and require the exact set in both source and isolated layouts.
- [ ] Make `runtime_digest()` hash only the ordered allowlist and raise `E_RUNTIME_MODULE_SET` for an extra, missing, symlinked, or non-regular module.
- [ ] Make the launcher validate schema 2 and the same exact allowlist before importing `control_plane.cli`.
- [ ] Change `__version__` to `3.1.0-core.1` and update the lock only after all active bytes are final.
- [ ] Delete Advanced modules with `apply_patch`; Git history is the archive and no duplicate source archive is created.
- [ ] Run `python3 -m unittest tests.test_core_lockfile tests.test_core_quarantine -v`; expect pass.

## Task 3: Replace remote host types with inert Core types

**Files:**

- Create: `control_plane/core_types.py`
- Modify: `control_plane/policy.py`
- Modify: `control_plane/routing.py`
- Modify: `control_plane/clarification.py`
- Modify: `control_plane/intake.py`
- Test: `tests/test_core_routing.py`

- [ ] Extract only the in-process types required to keep routing deterministic: `HostAdapterCapability`, `TrustedAuthorization`, `TrustedRouteDecision`, `ValidatedInventory`, and governing-runtime observation.
- [ ] Ensure constructors cannot be reconstructed from serialized JSON and every returned route keeps `authorizes=false` unless the current host supplies a live in-process capability.
- [ ] Keep all remote outcomes as deferred non-authorizing effects; Core lifecycle accepts only `local_change`.
- [ ] Run focused policy/routing/contract tests; expect pass without importing any Advanced module.

## Task 4: Local state and generational leases

**Files:**

- Create: `control_plane/task_state.py`
- Create: `control_plane/leases.py`
- Create: `tests/test_core_task_state.py`
- Create: `tests/test_core_leases.py`

- [ ] Define the exact state set:

```python
CORE_STATES = (
    "framed", "planned", "ready", "implementing", "verifying",
    "review_ready", "blocked", "closed",
)
```

- [ ] Store `CoreTaskStateV1` below the worktree Git dir and bind it to `task_id`, `revision_id`, runtime digest, repo, branch, head, scope, generation, and monotonic revision.
- [ ] Do not create state or leases for facts-only/answer tasks.
- [ ] Define lease identity as `task_id + revision_id + lease_generation`; allocate the next generation under the Git-common-dir lock.
- [ ] Release by writing an immutable receipt keyed by `lease_id`; never create a global task tombstone.
- [ ] Assert overlapping scopes conflict across worktrees, exact replay is idempotent, and a new revision can acquire a new generation.
- [ ] Run `python3 -m unittest tests.test_core_task_state tests.test_core_leases -v`; expect pass.

## Task 5: Legacy inventory and fail-closed recovery

**Files:**

- Modify: `control_plane/task_state.py`
- Create: `control_plane/adoption_recovery.py`
- Create: `tests/test_core_legacy.py`
- Create: `tests/test_core_adoption_recovery.py`

- [ ] Inventory legacy task, lease, delivery-lease, run, and remote-unknown records read-only with bounded file count/size and no symlink traversal.
- [ ] Report legacy records as `origin=legacy`, `resumable=false`.
- [ ] Return `E_ACTIVE_LEGACY_STATE` before any apply/upgrade mutation when a task is non-terminal, a lease exists, a run is open, or remote state is `UNKNOWN`.
- [ ] Keep `adopt status`, `adopt verify`, and `adopt rollback` for an existing installation; block `adopt plan/apply` and every `upgrade` action with `E_CAPABILITY_QUARANTINED`.
- [ ] For rollback, verify the existing journal and restore recorded bytes, modes, runtime pointer, and `core.hooksPath`; reject drift before mutation.
- [ ] Run focused legacy/recovery tests; expect pass and exact pre/post snapshots for every blocked path.

## Task 6: One closed toolchain context

**Files:**

- Create: `control_plane/toolchain.py`
- Create: `tests/test_core_toolchain.py`
- Modify: `control_plane/repository.py`

- [ ] Resolve Python, Git, and Node once from trusted candidates, storing absolute path, realpath, device, inode, mode, and bounded version output.
- [ ] Build `PATH` only from the selected executable directories and isolate Git configuration.
- [ ] Probe each executable from the final closed environment, including a nested Node relaunch.
- [ ] Apply `GIT_NO_REPLACE_OBJECTS=1` only to authoritative Git observations, never to the global test environment.
- [ ] Assert absent ambient `PATH`, Git variables, and Node variables cannot change selected tools.
- [ ] Run `python3 -m unittest tests.test_core_toolchain -v`; expect pass.

## Task 7: Verification mutex and maintenance circuit breaker

**Files:**

- Create: `control_plane/verification.py`
- Create: `control_plane/maintenance.py`
- Create: `tests/test_core_verification.py`
- Create: `tests/test_core_maintenance.py`

- [ ] Acquire a nonblocking exclusive mutex below Git common dir before authoritative/full verification.
- [ ] On contention, return `UNKNOWN/E_VERIFICATION_BUSY` without running Git or tests and without consuming a reframe.
- [ ] Keep safe review inspection read-only; only one full gate may run.
- [ ] Persist one open `MaintenanceLineageV1` per Git common dir. Allow one structural reframe; the second returns `E_BOOTSTRAP_REFRAME_LIMIT`, preserves the stable base, and creates no R3 task/worktree.
- [ ] Return initial migration truth only as `GREEN_LOCAL / PENDING_STABLE_ADOPTION`; a candidate cannot certify or adopt itself.
- [ ] Run the two focused test modules, including a second verifier process; expect one executor and one clean busy result.

## Task 8: Lazy Core CLI and proportional verification

**Files:**

- Rewrite: `control_plane/cli.py`
- Modify: `control_plane/risk_sentinel.py`
- Modify: `control_plane/hooks.py`
- Modify: `tests/run.sh`
- Test: `tests/test_core_cli.py`

- [ ] Make parser construction import no optional capability module.
- [ ] Preserve Core command names and stable JSON shapes for supported read/local actions.
- [ ] Implement local `task` commands on `CoreTaskStateV1` and generational leases.
- [ ] Keep legacy `run`, `report`, `verification-run`, `adopt apply/plan`, and `upgrade` parsers for one 3.1 cycle as zero-mutation quarantine stubs.
- [ ] Make `tests/run.sh` execute only the exact Core test manifest plus policy, registry, inventory, doctor, compile, shell syntax, `git diff --check`, and status checks. Advanced assurance is not discoverable or runnable from this entrypoint.
- [ ] Run CLI snapshots and the Core suite; expect pass and runtime import/LOC constraints satisfied.

## Task 9: Canonical Superpowers pin and plugin candidate

**Files:**

- Modify: `.codex/resource-registry.toml`
- Modify: `skills/control-plane-run/SKILL.md`
- Modify: `skills/control-plane-run/references/TaskPlaybookV0.md`
- Modify: `plugins/control-plane/.codex-plugin/plugin.json`
- Modify: `plugins/control-plane/skills/control-plane-run/SKILL.md`
- Test: `tests/test_core_plugin.py`

- [ ] Select local Superpowers at commit `dd237283dbfe466e11bd4be55acf14ecb8f6636e` as the sole canonical workflow resource; mismatch returns `E_RESOURCE_REVISION_DRIFT`.
- [ ] Document: Control Plane owns scope/authority/evidence; Superpowers owns TDD/debug/worktrees/review.
- [ ] Keep `TaskPlaybookV0` skill-only, at most 1 KiB, with `authorizes=false` and no runtime/store.
- [ ] Keep Autopilot OFF until 10 real Core tasks pass the scorecard; no daemon, scheduler, authority store, or telemetry.
- [ ] Prepare source plugin `3.1.0-core.1` but do not install or replace the currently installed plugin.
- [ ] Assert packaged skill/reference are byte-identical to canonical files and contain no Advanced runtime.

## Task 10: Durable governance documentation

**Files:**

- Create: `docs/adr/0006-control-plane-core-and-quarantine.md`
- Create: `docs/engineering/19-control-plane-core-maintenance.md`
- Create: `docs/engineering/20-control-plane-core-dogfood.md`
- Create: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Create: `docs/engineering/00-canonical-index.md`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `SECURITY.md`
- Modify: directly affected version/Advanced documents
- Test: `tests/test_core_documentation.py`

- [ ] State canonical version truth: `2.1.1` is the last official release; `3.0.0` was an unpublished candidate/plugin version; `3.1.0-core.1` is a local prerelease candidate.
- [ ] Mark historical v2.3/v2.4/Advanced documents non-governing through the canonical index rather than rewriting history.
- [ ] Document legacy recovery, mutex, circuit breaker, exact compatibility window, rollback, and external consumer prohibition.
- [ ] Add the 10-task manual dogfood scorecard: at least 3 facts-only; mixed local/hybrid/controlled; zero duplicated/fabricated effects; zero overlapping writers; at most one nuisance warning; zero duplicated full suites.
- [ ] Keep every checkpoint’s `## Continuación` compact and non-authorizing.

## Task 11: Integrated verification and independent review

**Files:**

- Update: this plan’s checkboxes and `## Continuación`
- Add after verified close: `/Users/bustaseo/.codex/memories/extensions/ad_hoc/notes/<timestamp>-control-plane-core.md`

- [ ] Run focused tests after each slice, then run exactly once:

```bash
bash tests/run.sh
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check --registry .codex/resource-registry.toml --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check
git status --short --branch
```

- [ ] Record elapsed Core-suite time and compare it with the clean baseline: `805 tests`, `1527.741 s`, SHA `929d3f8a0656fed190bb65ceb3a29deef8de07d6`.
- [ ] Dispatch an independent spec review, repair only confirmed gaps, then dispatch a code-quality/security review and rerun affected gates.
- [ ] Confirm active Python LOC `<= 21530`, no Advanced imports/distribution/digest, and no installation or external transition.
- [ ] Add one small durable memory note only after verification, because the user explicitly requested future resumability.
- [ ] Leave the branch uncommitted and report `GREEN_LOCAL / PENDING_STABLE_ADOPTION`, `authorizes=false`.

## Rollback

The implementation is isolated in a dedicated worktree and remains uncommitted. Until a future separately authorized adoption, rollback is simply to stop using this worktree; the stable source remains `origin/main@929d3f8a0656fed190bb65ceb3a29deef8de07d6`. Never rewrite legacy JSON, installed runtimes, or external consumer repositories as part of this plan.

## Checkpoint 2026-08-13 — candidato local antes del gate autoritativo

- **Objetivo vigente:** cerrar `3.1.0-core.1` como candidato local con Advanced eliminado del runtime, distribución y digest; implementación y revisiones independientes están completas, pero la adopción estable y el dogfood siguen pendientes.
- **Git observable:** worktree `/Users/bustaseo/.config/superpowers/worktrees/Develope-IOS/control-plane-core-3-1`, rama `codex/control-plane-core-3-1`, HEAD/base `929d3f8a0656fed190bb65ceb3a29deef8de07d6`, sin commit nuevo, sin PR y con cambios locales deliberados.
- **Decisiones ya materializadas:** lock schema 2 y allowlist exacta de 25 módulos; CLI Core lazy; cuarentena estructural; task/lease generacional; policy canónica; inventario legacy read-only; `ClosedExecutionContextV1`; mutex por Git common-dir; bounded subprocesses; una sola reframación; Superpowers fijado; `legacy_writer_exclusion=COOPERATIVE_ONLY`.
- **Cambios principales:** runtime Core, launcher/hook cerrados, runner gobernante, plugin fuente `3.1.0-core.1`, ADR/runbook/threat model/scorecard, manifiesto exacto de pruebas y eliminación de los seis módulos Advanced.
- **Verificación ejecutada antes de este checkpoint:** remediación `84/84`, lock/contrato/manifiesto `30/30`, revisión final Git/subprocess `32/32`, revisión de especificación `16/16`, compilación en memoria y `validate_lock` verdes; dos revisiones independientes cerraron con `0 Critical / 0 Important`.
- **Gate pendiente al fijar estos bytes:** calcular el footer reproducible y ejecutar una sola vez `bash tests/run.sh`, seguido de `policy-check`, `registry-check`, `doctor`, `git diff --check` y estado Git. El resultado debe quedar en el relevo/memoria externa para no modificar después el snapshot verificado.
- **Límite de uso:** `GREEN_LOCAL / PENDING_STABLE_ADOPTION`; no instalar ni usar en consumidores externos. Autopilot permanece OFF y el scorecard de diez tareas sigue PENDING. El runtime estable sigue siendo `origin/main@929d3f8`.
- **Autoridad:** `authorizes=false`; no se autorizó commit, instalación, upgrade, push, PR, merge, deploy, publicación ni release.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del cierre local.
- **Para continuar:** si el relevo externo no registra el resultado final, verificar primero el footer y la evidencia del único gate autoritativo; no repetir implementación ni restaurar Advanced.
- **Mensaje exacto:** `Consulta el cierre de Control Plane Core 3.1; no repitas el gate si ya figura PASS y conserva authorizes=false.`
- **Estado de partida:** `/Users/bustaseo/.config/superpowers/worktrees/Develope-IOS/control-plane-core-3-1`, rama `codex/control-plane-core-3-1`, HEAD `929d3f8a0656fed190bb65ceb3a29deef8de07d6`, implementación y revisiones cerradas, sin commit ni PR; gate final registrado fuera del snapshot.
- **No hacer todavía:** borrar estado legacy, commit, instalación, upgrade, push, PR, merge, deploy, publicación o release.
