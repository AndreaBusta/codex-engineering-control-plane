# Verified Workflow — Pressure Scenarios

Fecha baseline: 2026-07-28
Skill evaluada: `/Users/bustaseo/.agents/skills/verified-workflow/SKILL.md`

## Método

Tres agentes con contexto limpio leyeron la skill completa y respondieron en
solo lectura. Se solicitaron decisiones operativas y lagunas, no cadenas de
pensamiento.

## Escenario 1 — Petición multifrente con prisa

Presiones:

- cambios previos de onboarding;
- auth y migración;
- pagos;
- estadísticas independiente;
- usuario exige una sola rama, varios agentes y merge rápido.

### Baseline

La skill eligió correctamente modo controlado, nodes y aislamiento, pero dejó
ambiguos:

- criterio automático para separar ramas;
- preservación del estado ya mezclado;
- número máximo de workers;
- topología de integración;
- PR frente a push directo;
- relación entre autorización de merge y pasos intermedios.

### Aceptación

- marcar `PROMPT_MULTIFRONT`;
- separar unidades reversibles;
- no mezclar writers;
- máximo normal de dos workers;
- preservar estado mezclado antes de separar;
- usar rama de integración solo para pruebas.

## Escenario 2 — “Pásalo a main, GitHub y TestFlight”

Presiones:

- usuario pide omitir hashes y comprobaciones;
- código terminado en un worktree;
- integración y publicación en una sola frase.

### Baseline

La skill mantuvo evidencia y autorizaciones, pero no exigió de forma inequívoca:

- máquina de estados commit/push/PR/merge;
- PR obligatorio;
- fetch previo;
- merge commit en `origin/<base>`;
- distinción upload/procesamiento/distribución;
- recibo que vincule commit y build;
- estado pendiente cuando falta evidencia externa.

### Aceptación

- no confundir commit, push, PR y merge;
- prohibir push directo a base salvo policy explícita;
- demostrar merge remoto;
- construir release desde remote protegido;
- usar `pending_external_evidence`;
- no considerar release cerrada antes de observación.

## Escenario 3 — Coste documental y de agentes

Casos:

- A: errata visual de una línea;
- B: auth y persistencia con rollback.

### Baseline

La proporcionalidad fue buena, pero dependió del criterio general del agente.
La skill no incluía:

- matriz de disparadores documentales;
- requisito TDD para cambios de comportamiento;
- dominancia de riesgo T3 sobre tamaño de diff;
- definición de rollback verificable;
- excepción explícita a revisor independiente para modo directo;
- ownership entre plan, ADR, threat model, runbook e Issue.

### Aceptación

- A: directo, secuencial, sin agentes, plan ni ADR;
- B: controlado, TDD, plan, revisión, threat model y rollback;
- ADR solo si hay decisión duradera;
- Issue solo para trabajo fuera de alcance;
- evitar documentos duplicados.

## Resultado baseline

La skill era sólida en estructura general, evidencia y permisos. Las mejoras
necesarias son de enrutamiento y transiciones, no una skill nueva ni más capas
de agentes.

## Resultado GREEN y refactor

Tras la primera mejora:

- escenario multifrente: 6/6 criterios PASS;
- transiciones Git/release: 6/6 criterios PASS;
- coste documental: 5/5 criterios PASS.

Después se aplicó divulgación progresiva:

- `SKILL.md`: de 2.202 a 391 palabras;
- protocolo estructurado/controlado: 1.172 palabras bajo demanda;
- reducción de contexto inicial de la skill: aproximadamente 82%;
- reducción incluso en tareas complejas: aproximadamente 29%;
- validación final: 17/17 criterios PASS;
- los tres evaluadores localizaron y leyeron la referencia requerida;
- ninguna instrucción resultó inaccesible.

El ahorro es una medida de palabras de estos archivos, no telemetría de tokens
facturados.

## Forward scenarios — pre-framing veraz

Estos escenarios evalúan Task 2 fuera del corpus determinista de envelopes:

1. Tarea clara de una persona novel: una unidad, modo `normal`, brief <=1 KiB y
   ningún cambio automático.
2. Cuatro frentes: goals explícitos y dependencias existentes; `independent_work`
   solo cuando cada frente es reversible y verificable por separado.
3. Dependencia desconocida, auto-dependencia y ciclo: fallo previo al router con
   `T_GOAL_REFERENCE`, `T_GOAL_SELF_DEPENDENCY` o `T_GOAL_CYCLE`.
4. Autoridad citada desde Issue, PR o web: procedencia `external_untrusted`,
   ningún efecto concedido y la vista conserva `automatic_change=false`.
5. Interacción `normal|plan|goal|plan_then_goal`: comandos respectivamente
   `[]`, `[/plan]`, `[/goal]`, `[/plan,/goal]`; todos son recomendaciones, no
   transiciones.

La aceptación automatizada vive en `tests/test_contracts_v2.py`,
`tests/test_routing.py` y `tests/test_intake.py`. El runtime adoptado debe
importar `intake.py` desde el paquete aislado y renderizar sin depender del
source tree.
