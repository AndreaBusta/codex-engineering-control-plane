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

v2.1 no añade un estado lateral durable de aclaración. El router diagnostica
ambigüedad material como `pending_host_capability`; `blocked` y
`suspend_for_reframe` siguen siendo las salidas persistentes honestas.

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
coinciden y la task durable permanece en un estado writer activo. Una task
`blocked`, cerrada o en finalización no puede reutilizar su lease. Otro writer
con rutas superpuestas recibe `E_LEASE_CONFLICT`.
La adquisición y liberación están serializadas mediante un lock de proceso; el
inventario de archivos cambiados es obligatorio al validar continuidad. Los
renames staged cuentan origen y destino: mover un archivo hacia una ruta
permitida no oculta una eliminación fuera del scope.

El lease no concede commit, push, PR, merge o release. Solo prueba continuidad
del mismo frente después de una edición autorizada. `risk-status --task-id ...
--lease-session-id ...` puede diagnosticar una lease local exacta como
`local_validated/UNKNOWN`; solo una atestación host exacta puede elevar el check
dirty a `PASS`.

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

La ruta productiva única es
`.codex/hooks/control_plane_hook.py → run_hook → evaluate_hook`. No existe un
evaluador paralelo ni una vista de warning publicada por el host.

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

La distribución local no incluye workflows ni providers GitHub. El ciclo
recomendado de piloto es `plan → apply → verify → gates del proyecto → rollback`;
una segunda aplicación requiere nueva autorización. La restauración incluye el
valor o ausencia anterior de `core.hooksPath`.

Cambios globales de `AGENTS.md`, `config.toml`, plugins o MCP quedan fuera de la
adopción project-local y exigen diff y autorización separados.

## Recorrido soportado v2.1 para un proyecto nuevo

Este es el único recorrido soportado. Parte de una
checkout limpia del Control Plane fijada a un commit o tag y de un proyecto
que ya tenga commit inicial,
remote, rama base observable y una rama de trabajo no protegida. Fuente y
destino deben estar limpios; el JSON de plan se guarda fuera del destino.

1. Ejecutar `adopt plan` con fuente y destino explícitos y guardar su salida
   JSON sin mutar el proyecto:

   ```bash
   /ruta/control-plane/scripts/control-plane adopt plan \
     --source /ruta/control-plane \
     --target /ruta/proyecto \
     --json > /ruta/segura/adoption-plan.json
   ```

2. Antes de escribir, revisar el JSON: `ok`, `source_commit`, `target_git`,
   `changes`, `git_config_changes`, `warnings`, `preflight_errors`, digests y
   acciones manuales. Un error o un hecho ambiguo detiene el recorrido.
3. Ejecutar `adopt apply` únicamente con ese plan exacto:

   ```bash
   /ruta/control-plane/scripts/control-plane adopt apply \
     --plan /ruta/segura/adoption-plan.json
   ```

4. Ejecutar `adopt verify` y después los gates reales del proyecto. La suite
   del Control Plane no sustituye tests, build o smoke propios del destino.
5. Abrir `/hooks`, revisar fuente, comando y hashes, y confiar manualmente solo
   si coinciden. La instalación permanece `pending_hook_trust` hasta entonces.
6. Antes del primer commit, ejecutar una vez `adopt rollback` y comprobar la
   restauración exacta del árbol de trabajo, modos, refs y `core.hooksPath`
   respecto al estado previo.
7. Si el ensayo pasa, volver a ejecutar `adopt plan`, revisar el nuevo JSON,
   aplicar, verificar y repetir los gates. Entonces se prepara la
   Pull Request del proyecto como cambio normal, revisable y reversible.
8. Para una versión posterior, partir de una nueva fuente limpia y usar
   `upgrade plan`, revisar su JSON y ejecutar `upgrade apply`. `adopt verify`
   debe volver a pasar; el rollback original se conserva. Una instalación
   previa que no tenga inventario de directorios recibe
   `E_UPGRADE_ROLLBACK_SCHEMA`: debe revertirse con su runtime instalado y
   volver a entrar mediante una adopción fresca.

La adopción project-local no instala dependencias, no modifica el remote y
no instala workflows de CI; no publica ni despliega ni cambia configuración
global, plugins o MCP. `adopt rollback` restaura bytes, modos, snapshots y la
configuración local gestionada, pero no revierte commits Git, PRs ni efectos
externos: esos se recuperan con el flujo normal del proyecto. El journal de
auditoría se conserva bajo el Git dir y no forma parte del árbol gobernado.

## Upgrade

```bash
scripts/control-plane upgrade plan \
  --target /ruta/al/repositorio --json > /ruta/segura/upgrade-plan.json
scripts/control-plane upgrade apply --plan /ruta/segura/upgrade-plan.json
```

El upgrade queda ligado al `plan_id` instalado, conserva el rollback original
y mantiene historial. Una migración de schema deberá añadir transformación
explícita; nunca se simula mediante un alias de `adopt apply`.

El inventario `created_directories` es obligatorio para nuevos upgrades. Si un
journal pre-release no lo contiene, el runtime nuevo falla antes de mutar: no
puede inferir con seguridad qué directorios existían antes de la adopción.
