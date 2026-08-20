# Plan de implementación — Control Plane 3.2 SpecPack

Fecha: 2026-08-18. Estado:
`PREPARED / BLOCKED_ON_R1_FINAL_EVIDENCE`. `authorizes=false`.

Diseño de referencia:
[SpecPack gobernado](../specs/2026-08-18-control-plane-3-2-specpack-design.md).
Alineación de ramas:
[decisiones de rama](../../engineering/21-repository-alignment-and-branch-decisions.md).

Este plan está escrito para ser ejecutado por ChatGPT Codex en sesiones
independientes, sin acceso al hilo que lo originó. Cada fase es autocontenida:
declara entrada, salida, prueba y criterio de cierre.

## Contexto mínimo para retomar sin historia

| Hecho | Valor |
|---|---|
| Repositorio | `codex-engineering-control-plane` |
| Remoto | `https://github.com/AndreaBusta/codex-engineering-control-plane.git` |
| Base | `main`; reobservar su SHA exacto antes de abrir la fase |
| Versión candidata | reconciliación `3.1.0-core.2`; `R1_OPEN`: reparaciones, prerevisiones frescas y evidencia final pendientes |
| Suite | la evidencia previa no cierra R1; la última ejecución integral debe quedar verde sobre los bytes finales dentro de `max_gate_runs=3` |
| Estrategia de integración | `squash` |
| Superficie Advanced | en cuarentena estructural, no reactivar |
| Outcomes permitidos | `answer` y `local_change` únicamente |

Antes de tocar nada, ejecutar el gate local y leer las reglas del repositorio:

```bash
scripts/control-plane preflight --mode write
```

## Orden de fases y condiciones de entrada

| Fase | Contenido | Runtime nuevo | Condición de entrada |
|---|---|---|---|
| 0 | Higiene de ramas | no | Autorización explícita por transición |
| 1 | Contrato: plantillas y skill | no | `R1_CLOSED_ON_FINAL_EVIDENCE`; no ejecutar mientras R1 siga abierto |
| 2 | Validador `spec_pack.py` y CLI | sí | Las tres condiciones de la sección «Puerta de la fase 2» |
| 3 | Verificación cruzada contra diff | sí | No diseñar todavía |

Las fases 0 y 1 no dependen entre sí, pero la fase 1 permanece bloqueada hasta
`R1_CLOSED_ON_FINAL_EVIDENCE`. La fase 2 depende de la 1.

---

## Fase 0 — Higiene de ramas

Sin código. Cada paso es un efecto externo y requiere autorización propia e
independiente. El runbook completo, con comandos exactos y estado esperado,
está en
[decisiones de rama](../../engineering/21-repository-alignment-and-branch-decisions.md).

Resumen de la decisión ya tomada: no hay rebase ni merge pendientes. Las ramas
históricas `codex/control-plane-v3`, `codex/control-plane-v2-3`,
`codex/control-plane-v2-4` y `codex/taskplaybook-v0-impl` solo aportan módulos
que ADR 0006 puso en cuarentena; fusionarlas revertiría la decisión vigente.
Esta regla no se aplica a todas las ramas `codex/*`: en particular,
`codex/control-plane-adoption-enablement-design` es el subject aprobado para la
reconciliación R1 y queda fuera de esa cuarentena.
`codex/cross-thread-audit-lookup-v1` es un rechazo técnico consciente,
sustituido por el lookup nativo.

Criterio de cierre: ramas remotas reducidas a `origin/main`, etiquetas
`archive/*` empujadas, worktrees inactivos retirados y protección de `main`
alineada con `.codex/project-policy.toml`.

---

## Fase 1 — Contrato del SpecPack

Objetivo: entregar el contrato completo sin una línea de runtime nuevo.

Tier propuesto: `T1`. Efectos: `local_read`, `local_write`. Workers: `1`.

### 1.1 Plantillas

Ya creadas en `templates/spec-pack/`:

```text
templates/spec-pack/README.md
templates/spec-pack/SPEC_PACK_MANIFEST.json
templates/spec-pack/PRD.md
templates/spec-pack/TRD.md
templates/spec-pack/UX_UI.md
templates/spec-pack/APP_FLOW.md
templates/spec-pack/BACKEND.md
templates/spec-pack/IMPLEMENTATION_PLAN.md
templates/spec-pack/TASK_ENVELOPE.specpack.json
```

Verificar que cada plantilla declara las secciones exigidas por el diseño y que
los identificadores de ejemplo respetan `^(PRD|TRD|UX|FLOW|BE|PLAN)-[A-Z]-\d{3}$`.

### 1.2 Skill de redacción

Crear `skills/control-plane-specpack/SKILL.md` siguiendo la forma de
`skills/control-plane-run/SKILL.md`: frontmatter con `name` y `description`,
cuerpo breve, sin autoridad.

Contenido obligatorio de la skill:

- el modelo redacta, el plano verifica;
- proporcionalidad: pack mínimo en T0/T1, pack completo en T2/T3;
- reglas de identificador y de referencia entre artefactos;
- prohibición de afirmar efectos externos como realizados;
- cierre con bloque `## Continuación` y `authorizes=false`.

### 1.3 Registro en el registry

Añadir a `.codex/resource-registry.toml`:

- recurso `skill.control-plane-specpack`, `kind = "skill"`,
  `capabilities = ["spec.pack"]`, `effects = ["local_read"]`, `egress = "none"`,
  `canonical = true`;
- recurso `document.spec-pack-contract` apuntando a
  `repo://templates/spec-pack/README.md`;
- ruta `spec-pack-authoring` con `tiers = ["T2", "T3"]`,
  `phases = ["frame", "plan"]`, `required_capabilities = ["spec.pack"]`.

Restricción verificada por la suite: un recurso `document` canónico no puede
apuntar a un documento marcado `HISTORICAL_NON_GOVERNING` en el índice
canónico, y todo recurso enrutado debe existir en `resources`.

### 1.4 Pruebas de la fase 1

Nuevo archivo `tests/test_spec_pack_contract.py`. TDD estricto: prueba que
falla, cambio mínimo, prueba que pasa.

| Prueba | Afirma |
|---|---|
| `test_templates_exist_and_declare_required_sections` | Los ocho archivos existen y cada plantilla contiene sus encabezados obligatorios |
| `test_template_identifiers_match_closed_pattern` | Todo ID de ejemplo respeta la expresión cerrada |
| `test_manifest_template_is_valid_json_and_non_authorizing` | El manifiesto parsea y declara `authorizes: false` |
| `test_skill_declares_ownership_and_no_authority` | La skill contiene la frontera de autoridad y no promete efectos externos |
| `test_registry_routes_spec_pack_capability` | El recurso existe, es canónico y la ruta lo referencia |
| `test_envelope_template_matches_task_envelope_schema` | El envelope de ejemplo tiene los campos del esquema vigente |

### 1.5 Criterio de cierre de la fase 1

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

Todo en verde, cero runtime nuevo en `control_plane/`, y el diff limitado a
`templates/`, `skills/`, `.codex/resource-registry.toml`, `tests/` y `docs/`.

---

## Puerta de la fase 2

La fase 2 **no empieza** hasta que las tres condiciones se cumplan a la vez y
sean verificables por separado:

1. el candidato `3.1.0-core.2` ha alcanzado adopción estable y la última
   ejecución consumida del gate integral está verde sobre sus bytes finales,
   dentro de `max_gate_runs=3`;
2. la fase 1 lleva al menos tres packs redactados en trabajo real, no de prueba;
3. existe autorización explícita para ampliar la superficie de runtime durante
   la línea 3.x.

Si alguna falta, el estado correcto es detenerse y reportarlo. Ampliar el
runtime mientras la superficie Advanced sigue en cuarentena contradice ADR 0006.

---

## Fase 2 — Validador determinista

Objetivo: imponer la trazabilidad que la fase 1 solo recomienda.

Tier propuesto: `T2`. Efectos: `local_read`, `local_write`. Workers: `1`.
Presupuesto: `≤ 900` LOC en `control_plane/spec_pack.py`.

### 2.1 Secuencia TDD

Cada paso es prueba que falla, implementación mínima, prueba que pasa. No
adelantar implementación de un paso posterior.

| Paso | Prueba primero | Implementación mínima |
|---|---|---|
| `PLAN-P-001` | Manifiesto inválido, ausente o con `schema_version` desconocido falla con `E_SPECPACK_SCHEMA` | Carga y validación del manifiesto |
| `PLAN-P-002` | ID mal formado o duplicado falla con `E_SPECPACK_DUPLICATE_ID` | Extracción de IDs con la expresión cerrada |
| `PLAN-P-003` | Referencia a un ID inexistente falla con `E_SPECPACK_TRACE_BROKEN` | Construcción del grafo de referencias |
| `PLAN-P-004` | Requisito sin cobertura aguas abajo falla con `E_SPECPACK_UNCOVERED_REQUIREMENT` | Alcanzabilidad directa |
| `PLAN-P-005` | Fase de plan sin origen falla con `E_SPECPACK_ORPHAN_PHASE` | Alcanzabilidad inversa |
| `PLAN-P-006` | Endpoint no consumido y no marcado interno falla con `E_SPECPACK_UNCONSUMED_ENDPOINT` | Cruce flujo × backend |
| `PLAN-P-007` | Marcador sin resolver impide sellar con `E_SPECPACK_UNRESOLVED` | Detección con ubicación por línea |
| `PLAN-P-008` | Tier declarado distinto del calculado falla con `E_SPECPACK_TIER_DRIFT` | Comparación contra la decisión del router |
| `PLAN-P-009` | Sección de perfil ausente falla con `E_SPECPACK_PROFILE_INCOMPLETE` | Tabla de secciones por perfil |
| `PLAN-P-010` | Artefacto que afirma un efecto externo realizado falla con `E_SPECPACK_AUTHORITY_CLAIM` | Detección de afirmaciones de autoridad |
| `PLAN-P-011` | Archivo que excede el límite se rechaza sin leerlo entero | Lectura acotada por archivo y total |
| `PLAN-P-012` | Ruta sensible se rechaza antes de abrir | Exclusión previa a la apertura |
| `PLAN-P-013` | Ruta fuera del repositorio destino se rechaza | Normalización y confinamiento de rutas |
| `PLAN-P-014` | Recibo con digests estables ante reordenación de claves | Serialización canónica y sha256 |
| `PLAN-P-015` | El recibo nunca contiene contenido de artefacto ni secretos | Filtro de campos del recibo |
| `PLAN-P-016` | `PASS=0`, `FAIL=1`, `UNKNOWN=2` en el CLI | Superficie `specpack init | check | seal` |

Trazabilidad exigida: cada `PLAN-P-*` referencia el `TRD-R-*` que implementa,
según la tabla de la sección 2.2 del diseño.

### 2.2 Adversariales obligatorios

El repositorio exige que el fallo sea cerrado. Cubrir al menos:

- enlace simbólico que apunta fuera del repositorio destino;
- artefacto con instrucciones dirigidas al agente, tratado como dato;
- manifiesto que declara una ruta de artefacto fuera de su directorio;
- pack cuyo recibo previo fue manipulado;
- artefacto de 2 MiB que debe cortar antes de agotar memoria;
- grafo de referencias con ciclo.

### 2.3 Integración en el lock

Añadir `spec_pack.py` a `runtime_modules` en `.codex/control-plane.lock` y
regenerar el digest `runtime`. Un módulo no declarado en el lock debe seguir
fallando de forma cerrada.

### 2.4 Criterio de cierre de la fase 2

La suite completa en verde, los seis comandos de verificación del repositorio en
verde, `spec_pack.py` por debajo de 900 LOC, cobertura de los dieciséis pasos y
de los seis adversariales, y ningún cambio en el contrato de comandos ya
existentes.

---

## Documentación asociada

Evaluar impacto documental antes de cerrar cada fase:

| Artefacto | Cuándo |
|---|---|
| ADR 0007 pasa de `proposed` a `accepted` | Al cerrar la fase 1 |
| Runbook de operación del SpecPack | Al cerrar la fase 2 |
| Fila en el índice canónico | Con cada documento nuevo |
| Threat model | Solo si la fase 2 introduce lectura de repositorios de terceros |

## Riesgos del plan

| Riesgo | Señal temprana | Respuesta |
|---|---|---|
| La fase 1 se usa como plantilla vacía | Packs con secciones presentes y sin referencias cruzadas | Adelantar la fase 2 o retirar la capacidad |
| El validador crece más de lo previsto | `spec_pack.py` supera 900 LOC | Recortar alcance, no ampliar presupuesto |
| Fricción en tareas pequeñas | El operador evita el pack en T2 | Revisar el umbral de proporcionalidad |
| Reactivación accidental de la cuarentena | Aparecen importaciones de módulos Advanced | Detener y revertir |

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del plan 3.2.
- **Para continuar:** tras cerrar R1, ejecutar la fase 1 completa y detenerse antes de la puerta de la fase 2.
- **Mensaje exacto:** `Con R1 cerrado y reobservado, ejecuta la fase 1 del plan SpecPack 3.2: skill, registry y tests de contrato. No implementes el validador ni realices efectos remotos.`
- **Estado de partida:** plantillas creadas en `templates/spec-pack/`; skill y entradas de registry pendientes; validador no implementado; fase bloqueada hasta evidencia final de R1.
- **No hacer todavía:** implementar `spec_pack.py`, tocar el lock, instalar, adoptar externamente, commit, push, PR, merge o release.
- **Autoridad:** `authorizes=false`
