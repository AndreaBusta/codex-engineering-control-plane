# Control Plane Core manual dogfood

Status: `PENDING_10_TASK_DOGFOOD`. `Autopilot=OFF`.

This is a manual evidence gate, not an execution log or an authorization store.
No prompts, transcripts, or telemetry are persisted. A task counts only after
its observable result and bounded evidence have been reviewed; this initial
matrix deliberately records no completed task and invents no PASS.

## Entry gate

- Use the exact `3.1.0-core.1` source candidate and record its runtime digest.
- Keep one root coordinator, at most two workers, and no overlapping writers.
- Permit only `answer` or `local_change`; defer every external effect.
- Run focal verification while iterating and at most one authoritative full
  suite for the final subject of each task.
- Mark uncertainty `UNKNOWN`; never translate missing evidence into success.

## Scorecard

| ID | Workload | FACTS_ONLY | Outcome | Allowed effects | Writers | Status | Evidence |
|---|---|---|---|---|---:|---|---|
| `CORE-DOGFOOD-01` | `local` | `true` | `answer` | `local_read` | `0` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-02` | `hybrid` | `true` | `answer` | `local_read` | `0` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-03` | `controlled` | `true` | `answer` | `local_read` | `0` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-04` | `local` | `false` | `local_change` | `local_read+local_write` | `1` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-05` | `local` | `false` | `local_change` | `local_read+local_write` | `1` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-06` | `hybrid` | `false` | `local_change` | `local_read+local_write` | `1` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-07` | `hybrid` | `false` | `local_change` | `local_read+local_write` | `1` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-08` | `controlled` | `false` | `local_change` | `local_read+local_write` | `1` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-09` | `controlled` | `false` | `local_change` | `local_read+local_write` | `1` | `PENDING` | `NONE` |
| `CORE-DOGFOOD-10` | `controlled` | `false` | `local_change` | `local_read+local_write` | `1` | `PENDING` | `NONE` |

For `FACTS_ONLY=true`, the outcome must remain `answer`, effects must be exactly
`local_read`, and no writer or durable task state may exist. `hybrid` means more
than one detected project profile. `controlled` covers security, auth, private
data, migration, production, or comparable risk while retaining local effects.

## Exit gate

Autopilot remains OFF until all of the following are evidenced together:

```text
tasks_completed=10
facts_only_total>=3
workloads_include=local,hybrid,controlled
duplicated_effects=0
fabricated_effects=0
overlapping_writers=0
nuisance_warnings<=1
duplicated_full_suites=0
```

Any failed invariant leaves `PENDING_10_TASK_DOGFOOD` or `BLOCKED`; it does not
trigger an automatic repair, install, or adoption. Replacing `PENDING` requires
an evidence reference that contains no prompt, transcript, secret, or authority.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del candidato Core y scorecard manual.
- **Para continuar:** completar una sola tarea pendiente con evidencia observable y sin efectos externos.
- **Mensaje exacto:** `Continúa el dogfood manual de Control Plane Core por la primera fila PENDING; conserva Autopilot OFF y authorizes=false.`
- **Estado de partida:** `3.1.0-core.1`, diez filas `PENDING`, cero evidencia de adopción estable.
- **No hacer todavía:** instalar, adoptar externamente, commit, push, PR, merge, deploy, publicación o release.
- **Autoridad:** `authorizes=false`
