# Lifecycle, hooks y adopción

## Máquina de estados

```text
framed → planned → ready → implementing → verifying
→ review_ready → committed → pushed → pr_draft → pr_ready
→ merged → base_verified
→ release_pending → released → observed → closed
```

El resultado pedido fija el estado terminal previo a `closed`:

| Resultado | Terminal |
|---|---|
| answer | planned |
| local_change | review_ready |
| commit | committed |
| pull_request | pr_ready |
| integration | base_verified |
| release | observed |

`blocked` conserva `resume_state`; reanudar vuelve exactamente allí. No se
pueden saltar estados aunque el resultado final tenga más alcance.

Desde `ready`, los estados exigen evidencia machine-readable: preflight,
implementación completa, gates y decisión documental, commit, remote head, PR,
checks, merge commit, base remota, manifest, build de proveedor y observación
según la transición. Nombrar un estado no constituye prueba. El almacén local
valida forma, digests y enlaces entre transiciones; no convierte una declaración
local en evidencia del proveedor. PR, merge, CI y release permanecen
`pending_external_evidence` hasta consultarse por una capacidad autorizada.

```bash
scripts/control-plane task start \
  --task-id TASK-2026-001 \
  --outcome local_change \
  --branch codex/example \
  --task-digest 'sha256:<64-hex>' \
  --decision-digest 'sha256:<64-hex>' \
  --session-id session-local \
  --scope-path 'control_plane/**'

scripts/control-plane task transition --task-id TASK-2026-001 --state planned
scripts/control-plane task transition \
  --task-id TASK-2026-001 \
  --state ready \
  --evidence preflight-evidence.json
scripts/control-plane task status --task-id TASK-2026-001
scripts/control-plane task resume --task-id TASK-2026-001
scripts/control-plane task close --task-id TASK-2026-001
```

## TaskLease

El lease se guarda bajo el Git dir del worktree. Une tarea, worktree, rama,
sesión, rutas y policy digest. Un preflight dirty solo continúa cuando todos
coinciden. Otro writer con rutas superpuestas recibe `E_LEASE_CONFLICT`.
La adquisición y liberación están serializadas mediante un lock de proceso; el
inventario de archivos cambiados es obligatorio al validar continuidad.

El lease no concede commit, push, PR, merge o release. Solo prueba continuidad
del mismo frente después de una edición autorizada.

## Hooks audit

`.codex/hooks.json` registra `UserPromptSubmit`, `PreToolUse`, `Stop` y
`SessionStart` limitado a `source=compact`. Todos:

- resuelven desde la raíz Git;
- usan timeout de tres segundos;
- son silenciosos al pasar;
- emiten como máximo 4 KiB;
- no guardan prompt;
- no usan red;
- no ejecutan recursos seleccionados;
- no crean continuaciones recursivas.

La rehidratación posterior a compactación usa `SessionStart(compact)`, porque
es el evento que Codex entrega antes de la siguiente petición al modelo y que
admite `additionalContext`. `PostCompact` puede observar la compactación, pero
su contrato actual no es la superficie adecuada para inyectar ese contexto.
Véase el contrato oficial de
[hooks de Codex](https://learn.chatgpt.com/docs/hooks).

Después de un cambio de hash, abrir `/hooks`, inspeccionar fuente y comando y
confiar manualmente solo si coinciden. Hasta entonces:

```text
pending_hook_trust
```

No usar `--dangerously-bypass-hook-trust` como instalación normal.

## Adopción

```bash
scripts/control-plane adopt plan --target /ruta/al/repositorio
scripts/control-plane adopt plan \
  --target /ruta/al/repositorio --json > /ruta/segura/adoption-plan.json
scripts/control-plane adopt apply --plan /ruta/segura/adoption-plan.json
scripts/control-plane adopt verify --target /ruta/al/repositorio
scripts/control-plane adopt status --target /ruta/al/repositorio
scripts/control-plane adopt rollback --target /ruta/al/repositorio
```

`plan` no escribe y falla si la fuente está dirty, el target está dirty, la
rama es la base o base/remote son ambiguos. Genera bytes target-specific y liga
commit, manifest, target y estado previo mediante `plan_id`. `apply` exige ese
JSON exacto, usa lock de proceso, backups completos, journal `preparing` y
recuperación automática. `verify` detecta drift. `rollback` valida todos los
archivos y backups antes de mutar uno solo.

Remote, base, nombre, perfil, locators documentales, `AGENTS.md` y hooks se
resuelven antes de aprobar el plan. Comandos reales, arquitectura, release y
recursos project-local canónicos siguen necesitando verificación del proyecto
antes de enforcement. No se instala en BUSTAFIT, `textosv2` u otro repositorio
sin autorización explícita.

Cambios globales de `AGENTS.md`, `config.toml`, plugins o MCP quedan fuera de la
adopción project-local y exigen diff y autorización separados.

## Upgrade

```bash
scripts/control-plane upgrade plan \
  --target /ruta/al/repositorio --json > /ruta/segura/upgrade-plan.json
scripts/control-plane upgrade apply --plan /ruta/segura/upgrade-plan.json
```

El upgrade queda ligado al `plan_id` instalado, conserva el rollback original
y mantiene historial. Una migración de schema deberá añadir transformación
explícita; nunca se simula mediante un alias de `adopt apply`.
