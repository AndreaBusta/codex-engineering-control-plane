# Auditoría, DAFO y registro de riesgos

## Veredicto

El sistema mejora de forma material la manera de trabajar: transforma reglas
narrativas en policy, tests y gates, reduce la carga inicial de la skill y hace
visibles las fronteras entre local, GitHub y release.

No es “completamente óptimo”. Esta v1 quedó `review_ready` tras `63/63` tests,
revisión independiente sin hallazgos Critical o Important y el check remoto
`verify` aprobado en el Draft PR #1.

Ya existen baseline, remote privado, CI y PR. La garantía remota sigue siendo
parcial porque GitHub no habilita Rulesets ni protección clásica para este
repositorio privado con el plan actual. Tampoco existe prueba de TestFlight
hasta conectar Xcode Cloud/App Store Connect con autorización.

## DAFO

### Fortalezas

- Separación honesta entre juicio, policy, gates y evidencia externa.
- Política fail-closed para PR, base, concurrencia y release.
- Git preflight con códigos estables.
- Tests herméticos sin red ni dependencias.
- Modo read que diagnostica dirty/detached sin autorizar escritura.
- Base configurable; la lógica no depende de `main`.
- Razonamiento proporcional con Sol fijo.
- Secuencial por defecto y dos workers.
- Disparadores documentales que evitan burocracia automática.
- Skill con divulgación progresiva: 391 palabras iniciales.
- Autorización diferenciada para commit, push, PR, merge y release.
- Repositorio privado con baseline real y Draft PR.
- CI obligatoria en Ubuntu y smoke macOS manual para controlar coste.
- Acción externa fijada por SHA y permisos de workflow mínimos.
- Contratos adversariales contra regresiones de CI y secretos.

### Debilidades

- `main` no tiene protección efectiva por limitación del plan de GitHub.
- El PR permanece Draft y todavía no existe merge probado en `origin/main`.
- El smoke macOS está disponible, pero no se ejecuta automáticamente.
- Sin hooks en audit/enforce.
- Sin telemetría exacta de tokens.
- Clasificación T0–T3 sigue requiriendo juicio del modelo.
- Policy v1 es intencionalmente pequeña; comandos reales se adaptan por
  proyecto.
- Los cambios globales no tienen historial Git propio.
- Persisten plugins potencialmente duplicados.
- `danger-full-access` y `approval_policy = "never"` amplían el impacto de un
  error de agente.

### Oportunidades

- Habilitar GitHub Pro o mover la policy a un contexto que permita Rulesets.
- Activar PR y `verify` obligatorios cuando el proveedor lo permita.
- Fusionar el PR únicamente después de revisión humana final.
- Hooks primero en audit y después enforce.
- GitHub MCP para demostrar PR y merge commit.
- Xcode Cloud para procedencia TestFlight.
- Policies específicas para iOS, SaaS e híbridos.
- Métricas reales de agentes, reintentos, contexto y tokens.
- Consolidación de skills/plugins duplicados tras una prueba de procedencia.
- Recibos firmados o almacenados como artefactos de CI.

### Amenazas

- Prompt multifrente que contamina una rama.
- Worktree equivocado o detached HEAD.
- Referencia remota obsoleta usada como si fuera actual.
- Push/merge interpretados como sinónimos.
- Archive o build antiguo enviado a TestFlight.
- Prompt injection desde web, Issues, PR o MCP.
- Secretos en logs, Markdown o fixtures.
- Force push, reset o limpieza destructiva.
- Supply chain de acciones, plugins o dependencias.
- Grafo con writers solapados y consumo duplicado.
- Exceso de documentación que oculta los gates importantes.
- Confianza excesiva en tests locales para estados externos.

## Stress test de decisiones

### Caso: cuatro frentes en una rama

Control:

- `PROMPT_MULTIFRONT`;
- inventario;
- referencia recuperable;
- separación por unidad;
- dos workers como máximo;
- integración temporal solo para pruebas.

Fallo evitado: commits gigantes, conflictos y reversión inseparable.

### Caso: “pásalo a main”

Control:

```text
commit ≠ push ≠ PR ≠ merge ≠ origin/<base> verificado
```

Fallo evitado: merge local presentado como integración remota.

### Caso: “publícalo en TestFlight”

Control:

- autorización de release;
- commit remoto;
- Archive nuevo;
- build único;
- estado del proveedor;
- smoke y observación.

Fallo evitado: build antigua o app/equipo incorrectos.

### Caso: errata de una línea

Control: modo directo, sin grafo, subagente, plan ni ADR.

Fallo evitado: gastar contexto y tokens en ritual.

### Caso: auth/persistencia

Control: modo controlado, TDD/caracterización, threat model, rollback y revisión.

Fallo evitado: infra-documentación de un cambio pequeño pero crítico.

## Registro de riesgos

| ID | Riesgo | Probabilidad | Impacto | Control v1 | Residual |
|---|---|---:|---:|---|---|
| R1 | escribir en base | media | alto | preflight write + PR | Ruleset no disponible en el plan |
| R2 | rama atrasada | media | alto | remote + divergence gate + `--refresh` | uso accidental de modo offline |
| R3 | detached HEAD | media | alto | baseline + gate | worktree externo no inspeccionado |
| R4 | cambios mezclados | media | alto | skill + runbook | separación no automatizada |
| R5 | merge no remoto | alta histórica | alto | GitHub + Draft PR + CI | PR todavía sin merge |
| R6 | TestFlight incorrecto | media | muy alto | release preflight/runbook | Xcode Cloud no conectado |
| R7 | secretos | baja-media | muy alto | guardrails + scans | full access |
| R8 | policy maliciosa | baja | alto | schema + revisión + CI | protección de rama ausente |
| R9 | error de Git oculto | baja | alto | fail-closed status/divergence | corrupción extrema |
| R10 | coste de contexto | alta | medio | router corto/progressive disclosure | sin tokens exactos |
| R11 | agentes solapados | media | alto | máximo 2/ownership | juicio no mecánico |
| R12 | plugin duplicado | media | medio | no mutar hasta auditar | duplicación vigente |

## Condiciones para “sobresaliente”

### Local

- suite completa;
- policy y CLI reales;
- revisión independiente sin Critical/Important;
- docs coherentes;
- config estricta;
- no secretos.

### Remoto

- [x] commit inicial;
- [x] remote privado;
- [x] CI;
- [x] PR de la propia configuración;
- [ ] Ruleset o protección equivalente;
- [ ] prueba de merge en base.

### Release

- workflow desde remote protegido;
- manifest;
- proveedor consultado;
- recibo;
- observación.

La ausencia de una capa no invalida las anteriores; sí impide afirmar que la
garantía correspondiente existe.
