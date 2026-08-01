# Aclaración y riesgo en local-audit v2.1

## Propósito

v2.1 ayuda a decidir cuánto control necesita una tarea sin atribuirse autoridad
que solo puede aportar el host, el usuario o un proveedor externo. Su frontera
operativa es local: policy instalada, Git, task state, hooks, guards y archivos
distribuidos.

## Contrato de aclaración

El router calcula un nivel a partir de la incertidumbre validada:

| Nivel | Resultado | Acción |
|---|---|---|
| `low` | `autonomous` | continuar de forma proporcional |
| `medium` / `high` | `pending_host_capability` | esperar interacción nativa |
| `critical` | `blocked` | reencuadrar la tarea |

Una `ClarificationRequest` serializada solo sirve como diagnóstico. Aunque su
schema y digests sean válidos, no representa una respuesta del usuario, no
concede autorización y no cambia durablemente el lifecycle. No existe el
comando `task clarification-status` en v2.1.

## Risk Sentinel

```bash
scripts/control-plane risk-status --repo /ruta/al/repositorio --json
```

El resultado agrega dimensiones `local` y `remote`:

| Estado | Exit | Significado |
|---|---:|---|
| `PASS` | 0 | todos los checks aplicables están demostrados |
| `FAIL` | 1 | al menos un check ha fallado |
| `UNKNOWN` | 2 | falta observación suficiente |

La ausencia de policy gobernante, task, procedencia o evidencia remota nunca se
convierte en `PASS`. En local-audit la dimensión remota es deliberadamente
`UNKNOWN`; no es un fallo del runtime ni una autorización implícita.

## Hooks reales

La única ruta productiva es:

```text
.codex/hooks/control_plane_hook.py → run_hook → evaluate_hook
```

Procesa `UserPromptSubmit`, `SessionStart(source=compact)`, `PreToolUse` y
`Stop`. Mantiene entrada y salida acotadas, timeout, warning mínimo y
deduplicación por sesión/fingerprint. No persiste prompts, no usa red y no
publica una vista host rehidratable.

`safe-read` ejecuta una lectura local cerrada en el worktree explícito:

```bash
scripts/control-plane safe-read --repo /ruta -- git status --short
```

El smoke macOS queda ligado a una task verificadora:

```bash
scripts/control-plane hook-smoke \
  --repo /ruta/al/repositorio \
  --task-id TASK-VERIFY-001 \
  --json
```

Pasar el smoke no equivale a confiar el hook: `/hooks` sigue siendo revisión
humana y el lock permanece `pending_hook_trust`.

## Guards Git

Los launchers instalados llaman a:

```bash
scripts/control-plane git-guard pre-commit
scripts/control-plane git-guard pre-push
```

La policy instalada es content-addressed bajo el Git common dir. Los guards
protegen commit y operaciones push sobre la base, distinguen fast-forward de
non-fast-forward y fallan cerrados ante stdin ambiguo o policy instalada con
drift. No sustituyen branch protection ni observan operaciones realizadas fuera
del repositorio instalado.

## Métricas

`record_context_metrics()` registra únicamente:

- bytes del manifest;
- unidades de contexto seleccionadas;
- bytes de salida del hook;
- deduplicación/replay local.

Son métricas de payload del runtime, no telemetría exacta de tokens ni evidencia
de ahorro económico.

## Adopción y rollback

```bash
scripts/control-plane adopt plan --target /ruta --json > adoption-plan.json
scripts/control-plane adopt apply --plan adoption-plan.json
scripts/control-plane adopt verify --target /ruta
scripts/control-plane adopt status --target /ruta
scripts/control-plane adopt rollback --target /ruta
```

El plan es inmutable y target-specific. `apply` usa exclusión mutua, backup,
WAL y fsync; `verify` detecta drift; `rollback` valida todo antes de la primera
mutación y restaura tanto archivos como el valor o ausencia previa de
`core.hooksPath`.

La distribución local no activa provider GitHub, workflow `risk-sentinel` ni
configuración remota. Conserva las APIs Git/PR heredadas, que siguen fail-closed
sin capacidad host. Una adopción no modifica `.github/workflows/**`.

## Límites y siguiente nivel

Permanecen pendientes:

- interacción/confirmación nativa;
- procedencia GitHub y required checks;
- promoción a `soft-enforce`;
- CI/CD, deploy y release.

Reabrir estas superficies exige un consumidor host real, nueva decisión
arquitectónica, TDD y autorización separada. Hasta entonces el estado honesto es
`audit` local con evidencia remota `UNKNOWN`.
