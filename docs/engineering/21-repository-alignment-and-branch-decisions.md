# Alineación del repositorio y decisiones de rama

Estado: `GOVERNING_CORE`. Observación local del 2026-08-18. `authorizes=false`.

Este documento fija la verdad observable del repositorio en un momento exacto y
la decisión para cada rama viva. No autoriza ninguna transición. Borrar una
rama, empujar una etiqueta o cambiar la protección de `main` son efectos
externos que requieren autorización explícita e independiente.

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
| Protección de rama en `main` | **ausente** (`HTTP 404 Branch not protected`) |

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

## Prueba aplicada a cada rama

Comparar commits induce a error bajo `squash`. La prueba usada aquí es de
contenido: qué archivos existen en la rama y no existen en `origin/main`.

```bash
git diff --diff-filter=A --name-only origin/main..<rama>
```

## Hallazgo determinante

Las cuatro ramas `codex/*` de la línea v2.3–v3 aportan exactamente el mismo
conjunto de archivos ausentes en `main`:

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

**No hay ningún rebase que hacer y ningún merge que hacer.** No existe trabajo
pendiente de integrar en este repositorio.

| Pregunta | Respuesta observada |
|---|---|
| ¿Alguna rama contiene trabajo aprobado sin integrar? | No |
| ¿Hay conflicto pendiente de resolver? | No |
| ¿Hay PR abierto esperando revisión? | No |
| ¿`main` está por detrás de alguna rama en contenido gobernante? | No |
| ¿La divergencia observada es real? | No: es un artefacto de `squash` |

El trabajo real pendiente no es de integración sino de **higiene y de cierre de
gates**: retirar ramas muertas, alinear la protección de `main` con la policy
declarada y cerrar el gate integral del candidato `3.1.0-core.1`.

## Runbook de limpieza

Estas transiciones son externas y destructivas. No se ejecutan sin
autorización explícita para cada una. El orden importa: primero se preserva,
después se borra.

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

Mientras la protección no exista, ninguna afirmación de este repositorio debe
presentar el gate de Pull Request como impuesto por la plataforma.

## Gate integral del candidato

`docs/engineering/20-control-plane-core-dogfood.md` deja diez filas en `PASS` y
`authoritative_full_gate=PENDING`.

Observación del 2026-08-18: `bash tests/run.sh` sobre `main` en
`b07418364409f76c900f0595a76c9e3e388ac433` terminó con `234` tests y `OK`.

Esa ejecución es evidencia local válida, pero no cierra por sí sola el gate
declarado en el scorecard: aquel gate se define sobre los bytes sellados del
candidato y como una única ejecución autoritativa registrada como tal. Cerrarlo
es una decisión de la tarea orquestadora, no un efecto derivado de esta lectura.
El candidato permanece `GREEN_LOCAL / PENDING_STABLE_ADOPTION` y
`external_consumer_adoption=PROHIBITED`.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora de la alineación del repositorio.
- **Para continuar:** autorizar por separado etiquetado, borrado de ramas y protección de `main`.
- **Mensaje exacto:** `Autoriza el paso 1 del runbook de limpieza: crear y empujar las etiquetas archive/*.`
- **Estado de partida:** `main` en `b074183`, limpio, sin PR abiertos, suite local `234 OK`, protección de rama ausente.
- **No hacer todavía:** borrar ramas, empujar etiquetas, cambiar protección, instalar o adoptar el candidato.
- **Autoridad:** `authorizes=false`
