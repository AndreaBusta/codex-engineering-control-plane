# Orientación y trampas conocidas

Estado: `GOVERNING_CORE`. Actualizado el 2026-08-18. `authorizes=false`.

Punto de entrada para una tarea que llega sin historia. Responde tres preguntas
antes de que cuesten tiempo: **dónde trabajar**, **qué es verdad ahora mismo** y
**qué fallos del entorno imitan defectos de producto**.

Este documento describe. No autoriza ninguna transición.

## 1. Dónde trabajar

El repositorio está clonado dos veces. **No son equivalentes.**

| Ruta | Uso |
|---|---|
| `~/Developer/codex-engineering-control-plane` | **Canónico.** Trabaja aquí y crea aquí los worktrees |
| `~/Developer/control-plane-worktrees/` | Worktrees sanos, fuera de carpetas sincronizadas |
| `~/Documents/Develope-IOS` | **Histórico. No trabajes aquí.** Bajo sincronización de iCloud; su `main` está atrasado |

Comprobación antes de empezar:

```bash
scripts/control-plane survey --repo . --json
scripts/control-plane doctor
```

`survey` te da clon, worktrees, ramas por contenido y trabajo huérfano en una
lectura. `doctor` informa `git_state_materialized`. Además, el gate de escritura
ya se detiene solo: `preflight --mode write` falla si el estado Git no está
materializado, así que la trampa de la sección 3.1 dejó de ser evitable por
disciplina y pasó a ser imposible de pisar en el caso común.

Residuo conocido: si el propio archivo de policy es un marcador, `preflight`
falla antes con `E_POLICY_PARSE` y sin pista del entorno. `doctor` y `survey`
sí lo dicen.

`git worktree list` solo muestra los worktrees de **su propio** clon. Un
inventario hecho desde un checkout puede ser exhaustivo dentro de su alcance y
ciego fuera de él. Antes de afirmar algo global sobre el repositorio, comprueba
los dos clones.

## 2. Qué es verdad ahora

| Hecho | Valor |
|---|---|
| Rama base | `main` |
| Última release oficial | `v2.1.1` |
| Candidato activo | `3.1.0-core.2`, `GREEN_LOCAL / PENDING_STABLE_ADOPTION` |
| `external_consumer_adoption` | `PROHIBITED` |
| Autopilot | `OFF` |
| Outcomes permitidos por Core | `answer` y `local_change`, nada más |
| Superficie Advanced | en cuarentena estructural por ADR 0006 |
| Protección de rama en `main` | **ausente**, pese a que la policy declara `require_pull_request = true` |

### Líneas de trabajo publicadas

| Rama | Contenido | Estado |
|---|---|---|
| `codex/control-plane-adoption-enablement-design` | Paquete `adoption_enablement/`, `scripts/control-plane-adoption`, `control_plane/stable_pause.py` | Publicada. Gate integral `395 OK`. **`AE-09` pendiente de dos rerevisiones independientes en `0 Critical / 0 Important`** |

### Contrato SpecPack

`templates/spec-pack/` contiene el contrato cerrado de seis artefactos —PRD,
TRD, UX/UI, flujo, backend y plan— bajo una frontera única: **el modelo redacta,
el plano verifica**. Diseño y plan por fases en
[el diseño 3.2](../superpowers/specs/2026-08-18-control-plane-3-2-specpack-design.md).
La fase 2, el validador determinista, está **bloqueada tras una puerta
explícita**; no la implementes sin comprobar sus tres condiciones.

## 3. Trampas conocidas

### 3.1 iCloud: fallos de almacenamiento que parecen defectos

Esta trampa costó cinco días de diagnóstico. macOS deja archivos como
marcadores `dataless` —flag APFS `UF_DATALESS`, `0x40000000`— y los materializa
en la primera lectura, lo que **cambia su inodo**.

| Código de error | Causa real |
|---|---|
| `E_CORE_LEASE_PATH: adoption mutex identity changed` | La guarda TOCTOU compara el inodo antes y después de abrir; materializar lo cambia y falla cerrada, **correctamente** |
| `E_SNAPSHOT_GIT_TIMEOUT` | Leer el árbol ancla tardó `139 s` frente a un presupuesto de `5 s`, con 468 de 1 148 objetos Git `dataless` |
| `E_LEGACY_STATE_UNKNOWN` | Lo mismo, sobre hojas de estado legacy |
| `git` que se cuelga sin salida | `git worktree list`, `branch --contains` o `status` esperando materialización |

Medición sobre el mismo comando y los mismos bytes: `139 s` en frío,
`0,027 s` materializado. Materializar movió el gate integral de `395` pruebas
con dos errores a `395 OK` sin tocar un byte de producto.

**Regla:** ante cualquiera de esos códigos, comprueba `st_flags` antes de tocar
código. El código falla cerrado como debe; el fallo es de almacenamiento.
Confundirlos lleva a «arreglar» guardas que funcionan bien.

### 3.2 `squash` hace que toda rama fusionada parezca adelantada

La policy declara `integration_strategy = "squash"`. Los commits originales de
una rama fusionada nunca entran en `main`; entra un commit aplastado
equivalente. **El número de commits no es evidencia de trabajo pendiente.** La
prueba válida es de contenido:

```bash
git diff --diff-filter=A --name-only origin/main..<rama>
```

### 3.3 Las ramas `codex/*` de la línea v2.3–v3 no se fusionan

Solo aportan los módulos que ADR 0006 puso en cuarentena. Fusionarlas
revertiría la decisión vigente. Detalle rama por rama en
[decisiones de rama](21-repository-alignment-and-branch-decisions.md).

### 3.4 El threat model se rompe con cualquier cambio de contenido

`docs/security/2026-08-12-control-plane-core-threat-model.md` termina con un
digest ligado al árbol completo, incluidos archivos no rastreados. Es un
detector de deriva deliberado. Termina el resto del cambio **primero** y solo
entonces recalcula:

```bash
python3 -c "import sys;sys.path.insert(0,'.');from tests.test_core_documentation import normalized_snapshot_version as v;print('Version:',v())"
```

Si tocaste runtime, hooks, policy, registry o lock, revisa el análisis de
verdad; no solo el digest.

### 3.5 El estado de las tareas del host es `UNKNOWN`, no «ninguna»

Git no demuestra qué tareas existen ni cuáles están activas. Antes de retirar un
worktree, confirma por separado que ninguna tarea viva depende de él.

## 4. Verificación antes de afirmar que esta base pasa

```bash
bash tests/run.sh
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check
git status --short --branch
```

## 5. Dónde seguir leyendo

- [Índice canónico](00-canonical-index.md) — qué documento gobierna y cuál es historia
- [ADR 0006](../adr/0006-control-plane-core-and-quarantine.md) — la cuarentena
- [Mantenimiento Core](19-control-plane-core-maintenance.md) — operación y recuperación
- [Decisiones de rama](21-repository-alignment-and-branch-decisions.md) — estado Git
- [Threat model](../security/2026-08-12-control-plane-core-threat-model.md)

## Continuación

- **Escribe en:** este hilo.
- **Rol:** relevo que llega sin historia.
- **Para continuar:** comprobar `dataless`, leer el índice canónico y normalizar la petición como `TaskEnvelope`.
- **Mensaje exacto:** `Comprueba dataless en la raíz de trabajo, resuelve el route y dime el tier antes de escribir nada.`
- **Estado de partida:** `main` publicado, candidato `3.1.0-core.2`, `AE-09` pendiente de rerevisiones.
- **No hacer todavía:** instalar, adoptar externamente, commit, push, PR, merge, deploy o release sin autorización exacta.
- **Autoridad:** `authorizes=false`
