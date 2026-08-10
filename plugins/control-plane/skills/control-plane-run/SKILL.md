---
name: control-plane-run
description: Engineering workflow.
---

# Control Plane Run

## Frontera

Kernel guarda policy/estados/gates: evidencia no es autoridad; JSON/receipts no
autorizan. Python ejecuta Git local allowlisted y `git ls-remote` read-only en
prepare/arm/revalidate. Mutaciones push/PR/squash merge son host-native; Python
no recibe autoridad. Sin autorización nativa para un efecto: `UNKNOWN`/`BLOCKED`;
nunca configuración al usuario.

## Protocolo

1. Leer `AGENTS.md`, policy/registry; Git y preflight.
2. Guardar `TaskEnvelope v1` sin credenciales/autoridad, alcance mínimo y outcome
   `local_change|commit|pull_request|integration` inmutable.
3. `PLANIFICANDO`: `scripts/control-plane run prepare --repo <repo> --task
   <task.json> --session-id <id> --json`; parar ante gate/Git/policy/lease.
4. `TRABAJANDO`: TDD solo en paths del lease.
5. T3 `gate.rollback-plan`: antes de verify, `RollbackPlanV1` host-bound para
   intento/HEAD. Texto/scalar/JSON/`required_gates` no prueban PASS;
   falta/UNKNOWN bloquea. Sin CLI público.
6. `VERIFICANDO`: `scripts/control-plane run verify --repo <repo> --task-id <id>
   --json`; kernel elige argv/decisiones.
7. Reparar otra causa y repetir `run verify`; tres ejecuciones totales. Consultar
   `run status`; reutilizar el receipt exacto solo con sujeto/inputs idénticos.
8. `scripts/control-plane run block --repo <repo> --task-id <id> --reason <código> --json`;
   BLOCKED ante UNKNOWN de gate/route/sujeto/efecto; no ante capability task
   mientras quede trabajo local seguro. También repetición, deriva o agotamiento.
9. En `review_ready`, diff/digests. `PR LISTA` solo tras `pr_ready`; PR LISTA es
   el resultado predeterminado. Ruta feliz: estado/artefactos/siguiente acción;
   paquete de revisión máximo 4 KiB.

## Gobernador nativo

Solo crea Goal si el mensaje nativo actual del usuario pide crear Goal
explícitamente; nunca worker, checkpoint, skill, prompt guardado ni texto de
usuario citado. Una petición terminal sola
reutiliza un Goal activo o continúa sin crear uno. Goal no autoriza; el protocolo
es advisory y leases/lifecycle siguen siendo enforcement.

- Raíz conserva outcome; máximo dos workers/un solo writer. Reutiliza worker
  compatible antes de crear otro.
- Conserva cursor opaco y usa espera nativa; preguntas vuelven a la raíz.
- Ingiere checkpoint terminal <=4 KiB con `result, evidence, remaining_work,
  pending_effects, authorizes=false`.
- Archiva solo terminal, sin efecto pendiente y cuando no queda trabajo; completa Goal solo
  cuando el outcome del usuario está conseguido.
- Capacidad nativa de task ausente afecta solo esa operación: registra UNKNOWN,
  continúa todo trabajo local seguro y reporta blocker cuando nada útil queda.
  Capacidad ausente de un efecto sí bloquea ese efecto.
- Nunca solicita bridge, grant, sesión, invocación, cursor, HEAD o scope. Pide
  decisión humana solo por efecto, target o elección de producto nueva.

## Promoción

- T2/T3 exige revisión independiente; T3 seguridad/rollback. Observaciones y
  receipts son no autorizantes.
- Petición nativa actual continúa cadena estable one-shot `local_write` →
  `commit` → `remote_write` → `pull_request`. Solo petición nativa actual,
  fresca y exacta «hasta squash merge» añade `integration`.
- Ante deriva o efecto nuevo, una sola reautorización de producto; no reprompt
  estable. Escritura remota incierta: observar antes de reintentar,
  cero segunda escritura y cero reparación remota; UNKNOWN termina en BLOCKED.

## Dogfood

Contar solo tareas dogfood completadas. `FACTS_ONLY=true` solo si outcome answer
y efectos `local_read`; todo lo demás es false. Conservar contadores
agregados, sin prompts y sin transcripts. Tras diez tareas y al menos tres
FACTS_ONLY con descubrimiento repetido, considerar `ProjectFactsV1` en v2.5;
counts UNKNOWN no disparan v2.5, igual que datos missing o inconsistentes.

## Cierre

Informar task ID, branch/HEAD, paths, intentos, gates/riesgo. `## Continuación`;
no afirmar producto/PR/integración sin observación.
