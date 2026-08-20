# Diseño Control Plane 3.3 — Orientación del operador

Fecha: 2026-08-18. Estado: `design`. `authorizes=false`.

Contrato WHAT/WHY para llevar el Control Plane a su forma útil desde ChatGPT
Codex en cuatro papeles: gobernanza, orquestación, guía experta de Git e
ingeniería de software.

Este documento diseña. No implementa, no instala, no adopta y no autoriza
ninguna transición externa.

---

## 1. Qué falta de verdad

Los cuatro papeles no están igual de maduros. Medirlos antes de planificar evita
construir sobre lo que ya funciona.

| Papel | Estado hoy | Veredicto |
|---|---|---|
| **Gobernanza** | Policy versionada, tiers T0–T3, gates, fronteras de autoridad, leases generacionales, evidencia no autorizante | **Maduro.** No tocar |
| **Ingeniero de software** | Router con perfiles, TDD impuesto por skill, revisión independiente, verificación proporcional | **Maduro.** No tocar |
| **Guía experta de Git** | `preflight`, `git_guards`, `git_state`, contratos de squash | **Parcial, con puntos ciegos probados** |
| **Orquestación** | Continuation Pointer como convención de prosa; lookup nativo del host | **Débil.** El operador establece el estado a mano y autoriza paso a paso |

El trabajo útil está en los dos últimos. Los dos primeros ya hacen su papel y
ampliarlos sería inflar la superficie que ADR 0006 acaba de reducir.

## 2. Evidencia de los puntos ciegos

Todos observados, no supuestos. Cinco fallos reales con su coste.

| # | Punto ciego | Evidencia | Coste |
|---|---|---|---|
| B1 | La guarda de materialización solo cubre archivos **rastreados** | `inspect_tracked_materialization` parte de `git ls-files`; `.git/` nunca aparece ahí | `doctor` informó `tracked_files_materialized=True` con 715 archivos `dataless` bajo `.git`. Cinco días buscando un defecto inexistente |
| B2 | `git worktree list` solo ve el **propio** clon | El repositorio está clonado dos veces; la rama más avanzada vivía fuera del inventario | Un veredicto «no hay nada que fusionar» falso, escrito en un documento gobernante |
| B3 | El trabajo huérfano no se inventaría | Un stash y un plan de 527 líneas sin rastrear, en el worktree de una rama marcada para borrar | Casi se destruyen bytes que no existían en ningún otro sitio |
| B4 | Bajo `squash` los commits engañan | Toda rama fusionada aparece adelantada | Lleva a proponer rebase donde no lo hay |
| B5 | La continuación entre tareas es prosa | «¿En qué hilo sigo?» aparece repetidamente en los hilos del operador | Decisiones de orquestación tomadas a mano, una por una |

B1–B4 son observables desde Git y el sistema de archivos: **deterministas, y por
tanto trabajo del runtime**. B5 no lo es.

## 2 bis. Autonomía acotada

El objetivo es que la IA trabaje sola el máximo tiempo posible con la máxima
calidad. Eso obliga a separar dos fricciones que suelen confundirse bajo el
mismo nombre.

| Fricción | Qué es | Veredicto |
|---|---|---|
| **De estado** | Establecer a mano qué ocurre, quién posee qué y dónde se continúa | Desperdicio puro. Automatizarla no toca la autoridad |
| **De autorización** | Que cada efecto necesite permiso | Deliberada. No se elimina |

El diagnóstico exacto es que **la autorización se concede con la granularidad
equivocada**, no que sobre gobernanza. En la línea Core 3.1 el operador tuvo
que autorizar por separado una liberación de lease idempotente y un cambio de
versión local. Ambos son efectos `local_change`, que el contrato de Core ya
permite. El alcance estaba dibujado tan estrecho que el agente tocaba el borde
constantemente y volvía a preguntar.

### Las tres piezas

1. **Mandato con alcance.** Una autorización declara rutas, efectos y
   presupuesto una sola vez. Dentro, el agente trabaja hasta terminar sin
   volver a preguntar. Fuera, falla cerrado.
2. **Parada recuperable en lugar de pregunta.** Al alcanzar el borde del
   mandato, el agente no interroga: se detiene en un punto reanudable con
   checkpoint exacto. `control_plane/stable_pause.py` ya implementa
   exactamente ese contrato.
3. **Revisión independiente obligatoria.** Quitar al humano del bucle hace la
   revisión fresca **más** necesaria, no menos. La evidencia del repositorio es
   inequívoca: cinco P1 tras un `175/175` verde, siete defectos tras un
   `283/283`. Un agente que se revisa con el mismo contexto con el que escribió
   el código no revisa nada.

### Lo que no cambia

Los efectos externos —commit, push, Pull Request, merge, deploy, release,
instalación y adopción— conservan su puerta individual. Cuestan poca autonomía
porque son momentos contados, y mantenerlos cerrados es lo que hace seguro
conceder el resto de una vez.

### Esto no es Autopilot

Autopilot significa daemon, planificador, almacén de autoridad y telemetría, y
sigue prohibido. Lo que aquí se diseña es autonomía acotada **dentro de una
tarea que el operador ya invocó**. Confundir ambos conceptos en la
documentación sería un error de gobernanza, no de estilo.

## 3. Frontera de diseño

> Lo que el runtime puede observar de forma determinista, lo observa el runtime.
> Lo que depende del host, lo conduce una skill y nunca un adaptador Python.

`AGENTS.md` lo fija explícitamente para el caso de B5: una referencia
`codex://threads/<UUID>` se resuelve solo por la lectura nativa del host, y
**no se crea una API ni un adaptador Python**. PR #8 se cerró exactamente por
intentar lo contrario. Este diseño respeta esa decisión: la orquestación se
entrega como skill, sin una línea de runtime.

## 4. Alcance

### Incluido

| ID | Capacidad | Cubre |
|---|---|---|
| `R-01` | Extender la materialización a los directorios de estado Git | B1 |
| `R-02` | `survey`: inventario de clon, worktrees, ramas y trabajo huérfano | B2, B3, B4 |
| `R-03` | Skill `control-plane-git`: pericia Git, mandato con alcance, parada recuperable y revisión obligatoria | B4, B5 |
| `R-04` | Cablear ambas en registry y rutas | — |

### Excluido, deliberadamente

- Ningún adaptador Python de lectura entre hilos. Prohibido por `AGENTS.md`.
- Ningún daemon, planificador, almacén de autoridad ni telemetría.
- Ninguna capacidad nueva de commit, push, PR, merge, deploy o release.
- Ninguna ampliación de gobernanza ni del router de perfiles: ya son maduros.
- El validador SpecPack, que tiene su propia puerta en
  [el diseño 3.2](2026-08-18-control-plane-3-2-specpack-design.md).

## 5. Requisitos

| ID | Requisito | Prioridad |
|---|---|---|
| `PRD-R-001` | La materialización cubre el Git dir del worktree y el Git common dir, no solo los archivos rastreados | must |
| `PRD-R-002` | Un `dataless` bajo estado Git degrada el resultado y nombra el área, sin volcar rutas completas | must |
| `PRD-R-003` | `survey` informa identidad del clon, sus worktrees, ramas y trabajo huérfano en una sola lectura | must |
| `PRD-R-004` | `survey` declara explícitamente que **no puede ver otros clones**, como `UNKNOWN` y no como ausencia | must |
| `PRD-R-005` | La comparación de ramas es por contenido, no por número de commits | must |
| `PRD-R-006` | El trabajo huérfano —stashes y archivos sin rastrear por worktree— aparece con recuento | must |
| `PRD-R-007` | Ninguna observación muta el repositorio ni ejecuta hooks | must |
| `PRD-R-008` | Toda salida respeta `PASS=0`, `FAIL=1`, `UNKNOWN=2` y `authorizes=false` | must |
| `PRD-R-009` | La skill de Git conduce squash, worktrees, ramas muertas y continuación entre tareas sin runtime nuevo | must |
| `PRD-R-010` | Ninguna lectura sigue enlaces simbólicos ni abre contenido de archivos de producto | must |
| `PRD-R-011` | Un mandato declara rutas, efectos y presupuesto una sola vez; el trabajo dentro de él no requiere autorización adicional | must |
| `PRD-R-012` | Alcanzar el borde del mandato produce una parada recuperable con checkpoint exacto, no una pregunta | must |
| `PRD-R-013` | No se afirma cierre sin revisión independiente de un revisor que no escribió el código | must |
| `PRD-R-014` | Los efectos externos conservan su puerta individual con independencia del mandato | must |
| `PRD-R-015` | Sin daemon, planificador, almacén de autoridad ni telemetría | must |

## 6. Contratos

`EnvironmentObservationV1`, devuelto por la materialización extendida:

```json
{
  "schema_version": 1,
  "kind": "EnvironmentObservationV1",
  "status": "PASS",
  "tracked_files": 0,
  "dataless_tracked_files": 0,
  "git_state_files": 0,
  "dataless_git_state_files": 0,
  "areas": [],
  "truncated": false,
  "error_code": null,
  "authorizes": false
}
```

`areas` nombra zonas, no rutas: `worktree_git_dir`, `common_git_dir`,
`objects`, `core_state`. Suficiente para actuar, insuficiente para filtrar
estructura interna.

`RepositorySurveyV1`, devuelto por `survey`:

```json
{
  "schema_version": 1,
  "kind": "RepositorySurveyV1",
  "clone": {"root": "", "common_git_dir": "", "branch": "", "head": ""},
  "worktrees": [
    {"path": "", "branch": "", "head": "", "dirty": 0, "untracked": 0, "detached": false}
  ],
  "branches": [
    {"name": "", "head": "", "only_in_branch": 0, "content_equivalent_to_base": true}
  ],
  "orphan_work": {"stashes": 0, "untracked_total": 0},
  "other_clones": "UNKNOWN",
  "status": "PASS",
  "authorizes": false
}
```

`other_clones` es siempre `UNKNOWN`. No es un hueco pendiente: es la afirmación
honesta de que un checkout no puede enumerar otros checkouts. Declararlo evita
exactamente el error que produjo B2.

## 7. Presupuestos

| Límite | Valor | Motivo |
|---|---|---|
| LOC de `control_plane/survey.py` | `≤ 450` | Presupuesto propio |
| LOC añadidas a `control_plane/materialization.py` | `≤ 120` | Extensión, no reescritura |
| Archivos de estado Git inspeccionados | `≤ 50 000` | Cota superior con margen sobre los 1 741 observados |
| Worktrees inventariados | `≤ 64` | Cota generosa sobre los 7 observados |
| Ramas comparadas por contenido | `≤ 64` | Igual |
| Tiempo por invocación de Git | `≤ 10 s` | Coherente con la materialización actual |
| Bytes de salida en contexto | `≤ 4 096` | `max_context_output_bytes` del registry |

Una cota superada devuelve `UNKNOWN` con su código, nunca un resultado parcial
presentado como completo.

## 8. Seguridad

- Solo lectura. Ninguna ruta de código muta el repositorio, escribe estado ni
  ejecuta hooks.
- Sin red por ninguna vía.
- No se sigue ningún enlace simbólico ni se abre contenido de archivos de
  producto: solo metadatos de inodo e inventarios de Git acotados.
- Las rutas sensibles se excluyen antes de abrir, reutilizando el patrón ya
  presente en la suite.
- `survey` describe; no propone borrar nada. Un inventario no es una decisión.
- Ninguna observación concede autoridad. `authorizes=false` en todo artefacto.

## 9. Compatibilidad y retirada

Capacidad aditiva. `doctor` gana campos; no pierde ninguno ni cambia el
significado de los existentes. Sin manifiesto ni comando nuevo, el repositorio
se comporta igual que hoy.

Retirada: revertir la extensión de `materialization.py`, borrar `survey.py`, su
entrada de CLI, la skill y las filas de registry. Sin migración de datos.

## 10. Lo que este diseño se niega a prometer

- Que `survey` vea otros clones. No puede, y por eso lo declara.
- Que un inventario limpio signifique que el trabajo sea correcto.
- Que la skill de orquestación sustituya el juicio del operador sobre en qué
  hilo continuar.
- Que un mandato amplio sustituya la revisión independiente. Conceder alcance y
  suprimir revisión es exactamente cómo la autonomía se vuelve cara.
- Que detectar `dataless` arregle iCloud. La solución de fondo es no guardar
  repositorios en carpetas sincronizadas.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del diseño 3.3.
- **Para continuar:** cerrar la evidencia final de R1 sobre los bytes reparados y sellados.
- **Mensaje exacto:** `Verifica los focales 3.3, reseña el candidato y conserva el último gate integral para los bytes finales.`
- **Estado de partida:** `IMPLEMENTED_LOCAL`; la materialización Git y Survey heredados forman parte del candidato `3.1.0-core.2` y su hardening de descubrimiento y recorrido permanece diferido; este diseño no registra el estado vivo de cierre de R1.
- **No hacer todavía:** push, PR, merge, instalación, adopción externa o release.
- **Autoridad:** `authorizes=false`
