# Codex Engineering Control Plane v1 — Diseño

- **Estado:** aprobado por la conversación de diseño y autorizado para implementación local el 28 de julio de 2026
- **Repositorio:** `/Users/bustaseo/Documents/Develope-IOS`
- **Rama de implementación:** `codex/control-plane-v1`
- **Propósito:** convertir una metodología útil, pero narrativa, en un sistema pequeño de políticas, gates deterministas y documentación accionable

## 1. Decisión

Se construirá un **control plane ligero**, no un “sistema operativo” monolítico.

El sistema separará tres tipos de decisión:

1. **Juicio del agente:** comprender el objetivo, detectar peticiones multifrente, clasificar el riesgo, elegir el esfuerzo de razonamiento y decidir qué documentación aporta valor.
2. **Política versionada:** declarar las reglas no deducibles del proyecto, como rama base, remote, estrategia de integración, límites de concurrencia y gates por nivel.
3. **Controles deterministas:** demostrar estados observables de Git, validar la política y bloquear transiciones inseguras.

La prosa explica el porqué. La política declara las excepciones del proyecto. Los programas verifican hechos. Ninguna de estas capas fingirá poder sustituir a las otras.

## 2. Resultado que debe conseguir

Una persona puede formular una petición imperfecta y Codex debe transformarla en unidades coherentes, ejecutarlas con el mínimo contexto necesario y cerrar cada unidad con evidencia.

Para una unidad destinada a producción, el recorrido normal será:

```text
intención
→ encuadre
→ clasificación
→ investigación
→ plan y documentación aplicable
→ rama/worktree
→ implementación
→ verificación
→ push de rama
→ Pull Request
→ checks
→ merge remoto
→ prueba en origin/<base>
→ release opcional
→ observación posterior
→ cierre
```

El sistema no podrá demostrar desde el entorno local que GitHub, Xcode Cloud o TestFlight hicieron algo si no consulta su estado autorizado. En esos puntos emitirá `pending_external_evidence`, no una afirmación optimista.

## 3. Alcance de v1

### Incluido

- Política TOML pequeña y validada.
- CLI local sin dependencias de terceros.
- Preflight Git para diagnóstico, escritura y release.
- Salida humana y JSON estable.
- Pruebas herméticas con repositorios temporales.
- Guía profesional de Git, PR, merge, documentación, contexto y release.
- Plantillas de tarea, ADR, Pull Request, handoff y recibo de release.
- Reglas locales `AGENTS.md` para mantener el propio control plane.
- Mejora acotada de la skill global `verified-workflow`.
- Ajustes globales mínimos de razonamiento, contexto externo y concurrencia.

### Deliberadamente excluido

- Crear commits, push, Pull Requests, merges o releases.
- Instalar dependencias o plugins.
- Crear o modificar repositorios remotos.
- Configurar Rulesets de GitHub o Xcode Cloud.
- Gestionar credenciales.
- Prometer cálculo exacto de tokens si Codex no expone telemetría fiable.
- Implementar un clasificador NLP rígido que suplante el juicio del modelo.
- Hooks automáticos antes de tener gates probados y un repositorio con commit inicial.
- Un instalador universal para cualquier stack.

## 4. Principios

### 4.1 Evidencia antes que relato

“Está en main” no es evidencia. La evidencia mínima es:

- PR fusionado en la rama base correcta;
- `mergeCommit` remoto identificado;
- commit contenido en `origin/<base>`;
- checks asociados aprobados;
- estado local sincronizado cuando corresponda.

“Está en TestFlight” exige además:

- fuente remota y commit exactos;
- versión y build;
- workflow;
- artefacto nuevo;
- estado procesado por Apple;
- recibo de release.

### 4.2 Proporcionalidad

No todas las tareas necesitan el mismo ritual:

| Nivel | Perfil | Razonamiento recomendado | Flujo |
|---|---|---|---|
| T0 | pequeño, claro, reversible | `low` | directo + validación localizada |
| T1 | tarea normal, patrón conocido | `medium` | plan breve + tests + PR |
| T2 | varias capas o incertidumbre | `high` | plan explícito + revisión independiente |
| T3 | seguridad, datos, dinero o release | `xhigh` | modo controlado + rollback + evidencia externa |

GPT-5.6 Sol es el modelo único preferido. Los niveles son recomendaciones; no se afirmará que una configuración cambió el esfuerzo de un hilo ya iniciado si la plataforma no lo permite.

### 4.3 Secuencial por defecto

El grafo es una optimización, no un indicador de calidad.

Se usará ejecución secuencial cuando:

- los pasos dependan unos de otros;
- compartan archivos o contratos;
- una conclusión cambie el siguiente paso;
- el coste de coordinación supere el ahorro.

Solo se usará un grafo cuando existan al menos dos nodos realmente independientes. El máximo normal será de dos workers concurrentes. Ningún par de escritores podrá compartir archivos o un contrato mutable.

### 4.4 Contexto bajo demanda

El agente debe:

1. buscar antes de leer;
2. leer fragmentos antes que archivos completos;
3. transferir rutas, hashes y resúmenes en lugar de transcripciones;
4. escalar contexto solo cuando una hipótesis no pueda resolverse con el presupuesto actual;
5. abrir un handoff limpio cuando el objetivo cambie sustancialmente.

### 4.5 Separación de autoridad

La automatización local puede diagnosticar, editar y ejecutar verificaciones seguras dentro del alcance autorizado. Requieren autorización específica:

- publicación, despliegue o release;
- push o merge remoto cuando no se haya pedido expresamente;
- operaciones destructivas;
- lectura o uso de credenciales;
- cambios de dependencias;
- firma o envío a proveedores.

## 5. Peticiones erróneas o multifrente

Una petición con varios cambios dispares no se ejecuta literalmente.

El director:

1. extrae objetivos;
2. agrupa por dominio y contrato;
3. identifica dependencias;
4. calcula riesgo por unidad;
5. determina reversibilidad y estrategia de prueba;
6. separa unidades que puedan integrarse o revertirse por separado;
7. asigna una rama por unidad coherente;
8. limita el paralelismo a frentes independientes.

Se marca `PROMPT_MULTIFRONT` cuando dos o más objetivos cumplen al menos dos de estos criterios:

- distinto resultado funcional;
- distinto nivel de riesgo;
- distinto módulo o sistema;
- distinta estrategia de prueba;
- release o reversión independiente;
- cambios críticos mezclados con cambios cosméticos;
- el PR no puede resumirse honestamente en una frase.

Si la rama está limpia, se reasigna a una unidad. Si ya contiene un frente, se conserva ese alcance. Si contiene cambios mezclados, se entra en modo rescate: inventario, referencia de seguridad, separación no destructiva y verificación de integridad. Nunca se borra el estado mezclado antes de crear una referencia recuperable.

## 6. Máquina de estados

Estados de una unidad:

```text
framed
planned
ready
implementing
verifying
review_ready
pushed
pr_draft
pr_ready
merged
base_verified
release_pending
released
observed
closed
blocked
```

Transiciones mínimas:

| Desde | Hacia | Gate |
|---|---|---|
| `framed` | `planned` | alcance, exclusiones y aceptación claros |
| `planned` | `ready` | rama real, base actual, árbol conforme |
| `implementing` | `verifying` | cambios inventariados |
| `verifying` | `review_ready` | gates del nivel aprobados |
| `review_ready` | `pushed` | autorización de push y remote correcto |
| `pushed` | `pr_ready` | rama remota igual a HEAD, plantilla completa |
| `pr_ready` | `merged` | checks, revisión y documentación aprobados |
| `merged` | `base_verified` | merge remoto demostrado en `origin/<base>` |
| `base_verified` | `release_pending` | release requerida y aprobada |
| `release_pending` | `released` | artefacto nuevo y procedencia registrada |
| `released` | `observed` | procesamiento y smoke/telemetría comprobados |
| cualquier | `blocked` | gate fallido o autoridad ausente |

Los gates locales de v1 cubren `ready` y el preflight de release. Las transiciones remotas se documentan y quedan para una fase posterior con GitHub/Xcode Cloud.

## 7. Política única

El archivo `.codex/project-policy.toml` contiene solo valores que no deben deducirse de Git o del código:

- versión del schema;
- identidad del proyecto;
- remote y rama base;
- política de integración;
- modelo y esfuerzos recomendados;
- máximo normal de workers;
- gates por nivel;
- política de documentación;
- política de release.

No contiene secretos, tokens, rutas a credenciales ni datos personales.

### Validación

La CLI fallará si:

- falta una clave obligatoria;
- el schema no es compatible;
- un nivel de razonamiento no está permitido;
- el máximo de workers está fuera de `1..2`;
- se permite push directo a la rama base;
- la integración no exige Pull Request;
- la release oficial no parte de la rama base remota.

## 8. CLI y códigos de error

La CLI será Python 3.11 estándar, sin paquetes externos:

```bash
python3 -m control_plane.cli policy-check --policy .codex/project-policy.toml
python3 -m control_plane.cli doctor
python3 -m control_plane.cli preflight --mode read
python3 -m control_plane.cli preflight --mode write
python3 -m control_plane.cli preflight --mode write --offline
python3 -m control_plane.cli preflight --mode write --refresh
python3 -m control_plane.cli preflight --mode release
```

El modo por defecto usa referencias locales y no contacta el remote.
`--refresh` actualiza explícitamente la referencia base; no usa `--prune`.
Cada comando admite `--json`. El JSON incluye:

```json
{
  "schema_version": 1,
  "command": "preflight",
  "ok": false,
  "mode": "write",
  "facts": {},
  "checks": [],
  "errors": [
    {
      "code": "E_GIT_BASE_BRANCH",
      "message": "Writing directly on the protected base branch is forbidden."
    }
  ]
}
```

Códigos iniciales:

- `E_POLICY_NOT_FOUND`
- `E_POLICY_PARSE`
- `E_POLICY_INVALID`
- `E_GIT_NOT_REPOSITORY`
- `E_GIT_STATUS_FAILED`
- `E_GIT_UNBORN`
- `E_GIT_NO_REMOTE`
- `E_GIT_NO_REMOTE_BASE`
- `E_GIT_DETACHED`
- `E_GIT_BASE_BRANCH`
- `E_GIT_DIRTY`
- `E_GIT_DIVERGENCE_UNKNOWN`
- `E_GIT_BEHIND_BASE`
- `E_RELEASE_WRONG_BRANCH`
- `E_RELEASE_NOT_SYNCED`
- `E_FETCH_FAILED`

Los errores son seguros: no incluyen credenciales ni el contenido de archivos.

## 9. Documentación en el momento oportuno

| Artefacto | Disparador | No usar para |
|---|---|---|
| Commit | unidad coherente de cambio | decisiones duraderas |
| PR | todo cambio a rama protegida | diario de desarrollo |
| Plan | T2/T3; T1 incierto | microcambios evidentes |
| ADR | decisión estructural duradera con alternativas | aplicación de patrón existente |
| Issue | trabajo pendiente fuera del alcance | ocultar un fallo requerido |
| Arquitectura | cambian módulos, fronteras o flujos | cada refactor interno |
| Runbook | cambia una operación repetible | explicar una función |
| Threat model | auth, pagos, datos o superficie pública | cambios sin impacto de seguridad |
| Rollback | cambio difícil de revertir o release T3 | cambios puramente cosméticos |
| Release receipt | cualquier publicación oficial | builds locales |
| Handoff | cambio de hilo, pausa larga o relevo | cada respuesta |

Antes de declarar una tarea terminada se ejecuta una evaluación de impacto documental y se registra explícitamente qué artefactos se actualizaron y cuáles no eran aplicables.

## 10. Git, PR y merge

### Inicio

1. identificar repositorio y worktree;
2. actualizar referencias remotas cuando haya red;
3. comprobar `origin/<base>`;
4. crear rama real desde esa referencia;
5. confirmar árbol limpio;
6. ejecutar baseline de tests.

### Trabajo

- commits pequeños y coherentes;
- push temprano solo cuando esté autorizado;
- Draft PR para CI y visibilidad;
- incorporar la base según la política;
- no usar force push ni reescritura de historial compartido sin permiso.

### Integración

- PR obligatorio;
- checks requeridos;
- conflictos resueltos semánticamente;
- revisión de diff;
- documentación aplicable;
- merge remoto;
- verificar `mergeCommit` dentro de `origin/<base>`;
- sincronizar la copia local después.

Una rama de integración temporal puede probar varios frentes, pero no reemplaza sus PR individuales ni se fusiona como fuente de verdad.

## 11. Release iOS y SaaS

La release oficial debe construirse desde el remote protegido, nunca desde un worktree arbitrario.

Para iOS:

```text
origin/<base>
→ Xcode Cloud
→ build y tests
→ Archive nuevo
→ manifest
→ TestFlight
→ procesamiento
→ smoke y observación
```

El recibo vincula commit, PR, versión, build, workflow, herramienta, artefacto y estado externo. Si cualquiera falta, la release queda `pending_external_evidence`.

Para SaaS se aplica el equivalente: commit remoto, workflow, artefacto, entorno, despliegue, smoke, métricas y rollback.

## 12. Seguridad

- Ningún archivo almacena secretos.
- No se imprime el valor de variables sensibles.
- Los remotes se normalizan sin exponer credenciales embebidas.
- El modo controlado exige threat model y rollback según impacto.
- CI futuro usará permisos mínimos y acciones fijadas por SHA.
- Los hooks futuros serán defensa adicional; los controles remotos seguirán siendo autoritativos.

## 13. Eficiencia y consumo

Objetivo: reducir trabajo duplicado sin degradar la calidad.

- GPT-5.6 Sol fijo.
- `high` como valor global equilibrado para ingeniería.
- `xhigh` para planificación profunda y T3.
- subagentes en `medium` por defecto.
- dos workers concurrentes como máximo normal.
- secuencial por defecto.
- no cargar todo el repositorio.
- no repetir logs completos.
- no usar un subagente cuando el agente principal ya tiene el contexto suficiente.
- registrar uso aproximado por tarea solo si la plataforma ofrece datos verificables.

No se prometerá un porcentaje de ahorro de tokens sin medición. El criterio de éxito inicial será indirecto: menos agentes, menos archivos leídos, menos reintentos y handoffs más pequeños.

## 14. Estructura

```text
.codex/project-policy.toml
AGENTS.md
README.md
control_plane/
  __init__.py
  cli.py
  git_state.py
  policy.py
docs/
  engineering/
  adr/
  superpowers/specs/
  superpowers/plans/
templates/
tests/
```

Cada módulo tiene una responsabilidad:

- `policy.py`: carga y valida TOML;
- `git_state.py`: observa Git sin modificarlo;
- `cli.py`: compone resultados y salida;
- `tests/`: demuestra el comportamiento con repositorios efímeros.

## 15. Pruebas

### Política

- policy válida;
- clave ausente;
- schema incompatible;
- razonamiento inválido;
- concurrencia mayor de dos;
- push directo a base;
- release con fuente incorrecta.

### Git

- escritura en rama base bloqueada;
- rama feature limpia aceptada;
- árbol sucio bloqueado;
- detached HEAD bloqueado;
- rama atrasada bloqueada;
- base alternativa aceptada;
- remote ausente bloqueado;
- modo lectura conserva capacidad diagnóstica;
- release sincronizada aceptada;
- release local adelantada bloqueada;
- repositorio huérfano bloqueado.

### Calidad

- sintaxis Python;
- `unittest`;
- compilación de módulos;
- búsqueda de secretos y marcadores;
- CLI humana y JSON;
- `codex doctor` después de cualquier ajuste global.

## 16. Rollout

### Fase A — esta entrega

- repositorio local;
- docs;
- policy;
- CLI;
- tests;
- skill global ajustada y verificada;
- configuración global mínima y validada;
- sin efectos remotos.

### Fase B — requiere autorización

- commit inicial;
- remote;
- CI;
- branch ruleset;
- GitHub MCP operativo para evidencia;
- hooks en modo audit.

### Fase C — por proyecto

- adaptación de policy;
- comandos reales de build/test;
- Xcode Cloud o pipeline SaaS;
- hooks enforce;
- recibos reales;
- observación post-release.

## 17. Criterios de aceptación

La v1 local se considera válida solo si:

1. las pruebas se escribieron antes del código y se observó el fallo inicial;
2. todos los tests herméticos pasan;
3. la observación por defecto no muta Git; solo `--refresh` actualiza
   explícitamente la referencia remota base;
4. una base alternativa funciona sin cambiar código;
5. el modo lectura diagnostica estados que escritura bloquea;
6. la policy rechaza configuraciones inseguras;
7. la skill no promete automatización inexistente;
8. `codex doctor` acepta la configuración global;
9. no se instalaron dependencias ni se tocaron secretos;
10. `git diff` y `git status` se reportan;
11. no se creó commit, push, PR, merge ni release;
12. los límites externos quedan visibles.
