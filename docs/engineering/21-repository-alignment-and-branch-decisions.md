# Alineación del repositorio y decisiones de rama

Estado: `GOVERNING_CORE`. Observación local del 2026-08-18. `authorizes=false`.

Este documento fija la verdad observable del repositorio en un momento exacto y
la decisión para cada rama viva. No autoriza ninguna transición. Borrar una
rama, empujar una etiqueta o cambiar la protección de `main` son efectos
externos que requieren autorización explícita e independiente.

> **Corrección del 2026-08-18.** La primera versión de este documento concluyó
> que no existía trabajo pendiente de integrar. Esa conclusión era válida dentro
> de un único clon y **falsa a nivel de proyecto**: el repositorio está clonado
> dos veces y la línea más avanzada vivía fuera de la vista del inventario. Ver
> «Segundo clon» y «Veredicto sobre rebase y merge», ya rectificados.

## Estado observado

Todos los hechos siguientes proceden de lectura local de Git y de la API de
GitHub el 2026-08-18.

| Hecho | Valor observado |
|---|---|
| Remoto | `https://github.com/AndreaBusta/codex-engineering-control-plane.git` |
| Rama base | `main` |
| `origin/main` | `b07418364409f76c900f0595a76c9e3e388ac433` |
| Sincronía local | `main` == `origin/main`, árbol limpio |
| Pull Requests abiertos | `0` |
| Issues abiertos | `0` |
| Pull Requests históricos | `21` (`20` fusionados, `1` cerrado sin fusionar) |
| Releases publicadas | `v2.1.0`, `v2.1.1` |
| Workflow CI | `.github/workflows/control-plane.yml`, job `core-verify` |
| Suite local sobre `main` | `234` tests, `OK`, `52 s` |
| Protección de rama en `main` | **ausente al observar; cerrada el mismo día** (ver «Brecha entre policy y plataforma») |

La estrategia de integración declarada en `.codex/project-policy.toml` es
`squash`. Por eso toda rama fusionada aparece "adelantada" respecto a `main`:
sus commits originales nunca entraron, entró un commit aplastado equivalente.
El número de commits no es evidencia de trabajo pendiente.

## Límites de esta observación

Lo observado es Git, GitHub y el sistema de archivos local. Queda fuera:

- **Tareas del host.** Este documento no prueba qué tareas Codex existen, cuáles
  están activas ni si alguna tiene asignado uno de los worktrees listados.
  `AGENTS.md` fija que esa lectura solo es válida por la vía nativa del host; sin
  ella el estado es `UNKNOWN`, no «ninguna». Antes de retirar un worktree conviene
  confirmar por separado que ninguna tarea viva depende de él.
- **Intención.** Que un artefacto esté superado por contenido no prueba que su
  autor lo diera por cerrado.

La inactividad de archivos entre el 2026-08-08 y el 2026-08-11 es evidencia de
abandono probable, no demostración de que nadie los use.

## Segundo clon

`git worktree list` no muestra los worktrees de otro clon, y las ramas locales
de ese clon tampoco aparecen. Un inventario hecho desde un solo checkout puede
ser exhaustivo dentro de su alcance y ciego fuera de él.

| Clon | Ruta | Estado |
|---|---|---|
| Canónico | `~/Developer/codex-engineering-control-plane` | Sincronizado con `origin`; cero archivos `dataless` |
| Secundario | `~/Documents/Develope-IOS` | `main` atrasado en `934a42c`; **715 archivos `dataless`** |

Del clon secundario cuelga
`~/.config/superpowers/worktrees/Develope-IOS/control-plane-adoption-enablement-design`,
con la rama `codex/control-plane-adoption-enablement-design`: la línea Adoption
Enablement y Stable Pause v1, que llegó a acumular cuatro commits y 5 049
líneas sin comprometer antes de publicarse.

Antes de afirmar nada global sobre el estado del repositorio, comprobar los dos
clones.

## Prueba aplicada a cada rama

Comparar commits induce a error bajo `squash`. La prueba usada aquí es de
contenido: qué archivos existen en la rama y no existen en `origin/main`.

```bash
git diff --diff-filter=A --name-only origin/main..<rama>
```

## Hallazgo determinante

Las ramas históricas `codex/control-plane-v3`, `codex/control-plane-v2-3`,
`codex/control-plane-v2-4` y `codex/taskplaybook-v0-impl` aportan exactamente el
mismo conjunto de archivos ausentes en `main`:

```text
control_plane/adoption.py
control_plane/candidate_receipt.py
control_plane/host_bridge.py
control_plane/lifecycle.py
control_plane/release_source.py
control_plane/run_workflow.py
```

Esos módulos son la superficie Advanced que
[ADR 0006](../adr/0006-control-plane-core-and-quarantine.md) puso en cuarentena
estructural de forma deliberada. No son trabajo perdido: son trabajo retirado
por decisión vigente.

La consecuencia es directa y es el eje de este documento:

> Rebasar o fusionar cualquiera de esas ramas reintroduciría el runtime en
> cuarentena y revertiría la decisión que gobierna hoy el repositorio.

Esta regla no se aplica a todas las ramas `codex/*`. En particular,
`codex/control-plane-adoption-enablement-design` contiene el subject aprobado
para reconciliación y no forma parte de este conjunto histórico en cuarentena.

## Decisión por rama

| Rama | Remota | Último commit | Situación verificada | Decisión |
|---|---|---|---|---|
| `codex/control-plane-v3` | no | `7bd9d2a` 2026-08-08 | Ancestro de `codex/control-plane-v2-4`; su contenido entró por PR #20 | Borrar en local |
| `codex/control-plane-v2-3` | no | `0c2da64` 2026-08-10 | Ancestro de `codex/control-plane-v2-4`; su contenido entró por PR #20 | Borrar en local |
| `codex/control-plane-v2-4` | sí | `a719953` 2026-08-11 | PR #20 fusionado con `squash` | Borrar en local y remoto |
| `codex/taskplaybook-v0-impl` | sí | `b2c1305` 2026-08-11 | PR #19 fusionado con `squash` | Borrar en local y remoto |
| `codex/cross-thread-audit-lookup-v1` | sí | `b2c27ea` 2026-08-03 | PR #8 **cerrado sin fusionar**; enfoque rechazado | Etiquetar y borrar en remoto |
| `claude/codex-control-plane-alignment-cf315e` | no | esta entrega | Worktree de trabajo activo | Conservar hasta integrar |

### Sobre `codex/cross-thread-audit-lookup-v1`

Es la única rama con trabajo genuinamente no fusionado:
`control_plane/cross_thread_audit.py` (347 líneas) más sus tests. No se
descarta por olvido. `AGENTS.md` fija hoy la regla contraria en la sección
«Lookup nativo entre tareas»: para una referencia `codex://threads/<UUID>` se
usa solo la lectura nativa del host y no se crea una API ni un adaptador
Python. PR #11 implantó ese enfoque nativo y dejó obsoleto el adaptador.

Por tanto es un rechazo técnico consciente, no deuda. Se conserva como historia
etiquetada y se retira de la lista de ramas activas.

## Veredicto sobre rebase y merge

**Rectificado.** Dentro del clon canónico no hay ningún rebase ni merge que
hacer: la tabla anterior lo demuestra rama por rama. Pero sí existe trabajo
pendiente de integrar en el proyecto, y vive en el segundo clon:
`codex/control-plane-adoption-enablement-design` adelanta a `main` en cuatro
commits con el paquete `adoption_enablement/`, el CLI
`scripts/control-plane-adoption` y `control_plane/stable_pause.py`.

| Pregunta | Respuesta observada |
|---|---|
| ¿Alguna rama **de este clon** contiene trabajo sin integrar? | No |
| ¿Hay conflicto pendiente de resolver? | No |
| ¿Hay PR abierto esperando revisión? | No |
| ¿`main` está por detrás de alguna rama de este clon? | No |
| ¿`main` está por detrás de una rama de otro clon? | **Sí**: Adoption Enablement |
| ¿La divergencia observada es real? | No: es un artefacto de `squash` |

El trabajo pendiente es por tanto de tres clases distintas, y conviene no
mezclarlas: **integrar** la línea Adoption Enablement, **higiene** de ramas
muertas y worktrees, y **cierre de gates** —protección de `main` y las
rerevisiones que aún exige `AE-09` sobre el candidato `3.1.0-core.2`.

## Almacenamiento: iCloud rompe el runtime sin ser un defecto

El clon secundario está bajo la sincronización de Escritorio y Documentos de
iCloud. macOS deja archivos como marcadores `dataless` —flag APFS
`UF_DATALESS`— y los materializa en la primera lectura. Eso rompe Core por dos
vías, y ninguna es un fallo del código:

| Síntoma observado | Causa real |
|---|---|
| `E_CORE_LEASE_PATH: adoption mutex identity changed` | Materializar cambia el inodo; la guarda TOCTOU compara antes y después y falla cerrada, **correctamente** |
| `E_SNAPSHOT_GIT_TIMEOUT` | Con 468 de 1 148 objetos Git `dataless`, leer el árbol ancla tardó `139 s` frente a un presupuesto de `5 s` |
| `E_LEGACY_STATE_UNKNOWN` | Lo mismo, sobre hojas de estado legacy |

Medición del 2026-08-18 sobre el mismo comando y los mismos bytes: `139 s` en
frío, `0,027 s` una vez materializado. Materializar los archivos hizo pasar el
gate integral de `395` pruebas con dos errores a `395 OK`.

La lección operativa es que un fallo de almacenamiento puede imitar durante
días un defecto de producto. Ante cualquiera de esos tres códigos, comprobar
`st_flags` antes de tocar código. La solución de fondo es mantener los
repositorios fuera de carpetas sincronizadas.

## Runbook de limpieza

**Ejecutado el 2026-08-18.** Se conserva porque describe el orden correcto y
porque el mismo procedimiento sirve para la próxima limpieza. Estas transiciones
son externas y destructivas: no se ejecutan sin autorización explícita para cada
una. El orden importa: primero se preserva, después se borra.

Resultado, verificado por `survey`: `PASS`, cero stashes, cero archivos sin
rastrear, cero worktrees sucios. Ramas remotas reducidas a `main` más las dos
líneas activas. Todo lo retirado sigue alcanzable por etiqueta:

| Etiqueta | Contenido preservado |
|---|---|
| `archive/control-plane-v2-3` | Incluye el plan de 527 líneas que no existía en ningún commit |
| `archive/control-plane-v2-4` | Rama fusionada por PR #20 |
| `archive/control-plane-v3` | Ancestro de la línea v2.4 |
| `archive/taskplaybook-v0-impl` | Rama fusionada por PR #19 |
| `archive/cross-thread-audit-lookup-v1` | El módulo de 347 líneas de PR #8, cerrado sin fusionar |
| `archive/stash-codex-m0-v2-3` | El stash `codex-m0-v2.3-docs-before-runtime` |

### 1. Preservar antes de borrar

```bash
git tag archive/cross-thread-audit-lookup-v1 origin/codex/cross-thread-audit-lookup-v1
git tag archive/control-plane-v2-4 origin/codex/control-plane-v2-4
git tag archive/taskplaybook-v0-impl origin/codex/taskplaybook-v0-impl
```

Empujar las etiquetas es un efecto remoto y requiere autorización propia:

```bash
git push origin archive/cross-thread-audit-lookup-v1 archive/control-plane-v2-4 archive/taskplaybook-v0-impl
```

Con la etiqueta empujada, el borrado de rama deja de ser una pérdida: cualquier
commit sigue siendo alcanzable por nombre.

### 2. Rescatar trabajo huérfano

Comparar ramas no basta. Dos artefactos viven fuera de todo commit y se
perderían en una limpieza ingenua. Ambos cuelgan de `codex/control-plane-v2-3`.

| Artefacto | Contenido | Relación con `main` |
|---|---|---|
| `stash@{0}` `codex-m0-v2.3-docs-before-runtime` | Dos documentos, 793 líneas | Presentes en `main` como versiones posteriores; el stash conserva borradores anteriores con 315 y 181 líneas de diferencia |
| `docs/superpowers/plans/2026-08-10-control-plane-taskplaybook-v0.md` sin rastrear en el worktree v2-3 | Plan de 527 líneas | No existe en ningún commit. `main` contiene otro plan distinto para el mismo trabajo, con 581 líneas de diferencia |

Ninguno es trabajo en vuelo: son borradores de planificación de trabajo ya
entregado en PR #19 y PR #20. Pero el plan sin rastrear no existe en ninguna
otra parte, y `git worktree remove` fallará por su causa.

```bash
# Inspeccionar antes de decidir
git stash show -p stash@{0}
cat /Users/bustaseo/.config/superpowers/worktrees/codex-engineering-control-plane/control-plane-v2-3/docs/superpowers/plans/2026-08-10-control-plane-taskplaybook-v0.md
```

Decidir explícitamente entre conservar el plan como historia —moviéndolo al
repositorio principal o comprometiéndolo en una rama de archivo— o descartarlo
por superado. El stash sobrevive al borrado de rama porque vive en el Git
common dir, pero es frágil: si se conserva, conviene materializarlo.

**Nunca usar `--force` para saltarse esta comprobación.** Que Git se niegue a
retirar un worktree es la señal de que hay bytes que no existen en ningún otro
sitio.

### 3. Retirar worktrees inactivos

Cuatro worktrees quedaron anclados a ramas muertas y bloquean su borrado.
Última actividad observada entre el 2026-08-08 y el 2026-08-11, sin procesos
abiertos sobre ellos. Ausencia de actividad local no prueba por sí sola que
ninguna tarea del host los tenga asignados; esa comprobación es independiente.

```bash
git worktree remove /Users/bustaseo/.config/superpowers/worktrees/codex-engineering-control-plane/control-plane-v2-3
git worktree remove /Users/bustaseo/.config/superpowers/worktrees/codex-engineering-control-plane/control-plane-v2-4
git worktree remove /Users/bustaseo/.config/superpowers/worktrees/codex-engineering-control-plane/control-plane-v3
git worktree remove /Users/bustaseo/.config/superpowers/worktrees/codex-engineering-control-plane/taskplaybook-v0-impl
git worktree prune
```

Un worktree con cambios sin comprometer falla de forma segura. Revisar el
árbol antes de forzar; no usar limpieza destructiva.

### 4. Borrar ramas locales

```bash
git branch -D codex/control-plane-v2-3 codex/control-plane-v2-4 codex/control-plane-v3 codex/taskplaybook-v0-impl
```

Se usa `-D` y no `-d` porque `squash` impide que Git reconozca la fusión.
La equivalencia de contenido ya quedó probada arriba, no la asume Git.

### 5. Borrar ramas remotas

```bash
git push origin --delete codex/control-plane-v2-4
git push origin --delete codex/taskplaybook-v0-impl
git push origin --delete codex/cross-thread-audit-lookup-v1
```

### 6. Estado esperado al cerrar

```text
ramas locales    = main + rama de trabajo activa
ramas remotas    = origin/main
worktrees        = repositorio principal + worktree activo
etiquetas        = v2.1.0, v2.1.1, archive/*
stash            = vacío o materializado de forma explícita
trabajo huérfano = cero archivos sin rastrear fuera de un commit
```

## Brecha entre policy y plataforma

`.codex/project-policy.toml` declara:

```toml
require_pull_request = true
allow_direct_base_push = false
integration_strategy = "squash"
```

GitHub responde `Branch not protected` para `main`. La policy describe una
garantía que la plataforma no impone: hoy un push directo a `main` no está
bloqueado por nada salvo disciplina.

Es coherente con la doctrina del repositorio —la prosa no sustituye a los
gates— y por eso mismo hay que cerrarla. Configuración mínima propuesta para
`main`, pendiente de autorización:

- exigir Pull Request antes de fusionar;
- exigir el check `core-verify` en verde;
- exigir que la rama esté actualizada respecto a la base;
- prohibir push directo y force push;
- permitir únicamente fusión `squash`.

### Cerrada el 2026-08-18

La protección ya está configurada y la brecha no existe:

| Regla | Estado |
|---|---|
| Pull Request obligatorio | activo, con `0` aprobaciones requeridas |
| Check obligatorio | `core-verify` |
| Rama al día con la base | exigido |
| Force push | prohibido |
| Borrado de rama | prohibido |
| Historia lineal | exigida |

A partir de aquí, el gate de Pull Request **sí** está impuesto por la
plataforma, no solo declarado en la policy.

## Gate integral del candidato

`docs/engineering/20-control-plane-core-dogfood.md` deja diez filas en `PASS` y
`authoritative_full_gate=PENDING`.

Observación del 2026-08-18: `bash tests/run.sh` sobre `main` en
`b07418364409f76c900f0595a76c9e3e388ac433` terminó con `234` tests y `OK`.

Esa ejecución es evidencia local válida, pero no cierra por sí sola el gate
declarado en el scorecard: aquel gate se define sobre los bytes sellados del
candidato, con `max_gate_runs=6` ligado a la misma closure lineage. El checkpoint
previo e inmutable del reframe R1 del 2026-08-20 registró `gate_run_count=2` y
programó el intento `3/6`. Los intentos 1 y 2 permanecen consumidos: sus
resultados byte-bound quedaron superseded por reparaciones posteriores, pero no
se borran ni se reclasifican. Los resultados posteriores se registran en el Goal
y handoff nativos sin reescribir este checkpoint histórico. La última ejecución
consumida debe quedar verde sobre los bytes finales; reparar o volver a congelar
no reinicia el contador, y alcanzar `gate_run_count=6` sin ese estado exige
Stable Pause. Cerrarlo es una decisión de
la tarea orquestadora, no un efecto derivado de esta lectura. El candidato
permanece `GREEN_LOCAL / PENDING_STABLE_ADOPTION` y
`external_consumer_adoption=PROHIBITED`.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora de la alineación del repositorio.
- **Para continuar:** cerrar la reconciliación R1 sin reabrir la higiene histórica ya terminada.
- **Mensaje exacto:** `Continúa R1 sobre su worktree exacto; compara el delta completo contra ambos padres y conserva las ramas históricas concretas en cuarentena.`
- **Estado de partida:** protección de `main` activa con `core-verify`; la reconciliación local `3.1.0-core.2` está preservada en `d901bb6` y mantiene su evidencia final pendiente.
- **No hacer todavía:** push, PR, merge, instalar o adoptar el candidato sin la autoridad exacta de esa transición; los commits locales de R1 sí están autorizados.
- **Autoridad:** `authorizes=false`
