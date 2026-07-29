# Control Plane v2.1 — Clarification Gate y Risk Sentinel

- **Estado:** diseño aprobado; implementación deliberadamente no iniciada
- **Fecha:** 29 de julio de 2026
- **Rama de planificación:** `codex/risk-sentinel-v2-1`
- **Base observada:** `origin/main@20e999fe1be34b25ca969840529b083b2e39a461`
- **Perfil de ejecución del siguiente hito:** GPT-5.6 Sol con razonamiento `xhigh`
- **Método:** TDD, ejecución secuencial, un writer y como máximo dos workers
  independientes de lectura o revisión

## 1. Decisión

La siguiente evolución del Control Plane combinará dos controles distintos:

1. **Clarification Gate:** evita ejecutar con precisión una intención materialmente
   ambigua.
2. **Risk Sentinel:** informa del riesgo observable, bloquea localmente un conjunto
   curado de operaciones peligrosas y alerta después del hecho cuando la
   procedencia de un push a base es contradictoria o no puede demostrarse.

No se fusionarán conceptualmente. El primero gobierna comprensión y decisión; el
segundo gobierna estado y acción. Ambos alimentan el lifecycle y los hooks.

```text
Prompt, posiblemente vago o multifrente
→ host/model usa skill.task-framer
→ notas Markdown estructuradas
→ host normaliza y valida TaskEnvelope v1
→ InventoryObservation host-bound, no JSON autoatestiguado
→ Clarification Gate
→ Resource Router
→ NoviceEngineeringBrief efímero, solo cuando aporta valor
→ Risk Sentinel
→ recursos autorizados
→ lifecycle, gates y evidencia
```

La selección, la aclaración, la aprobación de una alternativa, la autorización
de un efecto y la confirmación irreversible seguirán siendo pruebas diferentes.

## 2. Verdad inicial

El diseño parte de hechos comprobados en el worktree aislado:

- `HEAD == origin/main == 20e999fe1be34b25ca969840529b083b2e39a461`;
- rama real `codex/risk-sentinel-v2-1`;
- árbol limpio;
- preflight de escritura aprobado;
- suite base: `174/174 PASS`;
- policy, registry y doctor aprobados;
- hooks entregados en `audit`;
- trust de hooks pendiente de revisión humana;
- la policy exige Pull Request y prohíbe push directo a la base;
- esa prohibición todavía es declarativa y cooperativa;
- el repositorio privado no dispone de Rulesets ni branch protection con el plan
  actual de GitHub;
- el runtime no contiene todavía `risk-status`, guards Git ni alarma de
  procedencia post-push;
- el lifecycle no distingue todavía una aclaración pendiente de un bloqueo
  operativo general.
- el registry declara que `skill.task-framer` produce `task-envelope-v1`, pero
  la skill global observada solo genera un encuadre narrativo; hoy no satisface
  ese contrato de salida;
- un lease sobre `.` no solapa mecánicamente un lease sobre `src/**`;
- leases persistidos en Git dirs de worktrees diferentes no se observan entre
  sí durante adquisición;
- el mismo defecto de raíz universal existe entre writers del grafo;
- el lifecycle lineal no permite registrar un nuevo `HEAD` del mismo PR tras
  feedback o checks fallidos;
- el lock de adopción contiene únicamente un PID y un cierre abrupto puede
  dejarlo obsoleto sin recuperación segura.
- `route --inventory PATH` permite que un JSON internamente coherente se
  autoatestigüe authenticated/healthy/ready;
- `task transition --evidence PATH` permite afirmar push, PR, merge o release
  sin observación del proveedor;
- el launcher y el hook seleccionan runtime por existencia antes de validar el
  lock; en layout fuente esto es un bypass Critical del trust anchor, aunque no
  existe hoy el directorio espurio;
- el `AuthorizationGrant` serializable actual puede declarar por sí mismo
  `issuer=trusted_host` y debe entrar en la frontera host-bound.

Los tests actuales ya cubren las correcciones de atomicidad de leases,
confinamiento de locators, saneamiento de variables `GIT_*`, evidencia de gates
y aislamiento del runtime. No cubren todavía los defectos anteriores; se
añadirán como estabilización verificable antes de ampliar el producto.

## 3. Alcance

### Incluido

- Contratos cerrados de aclaración y confirmación irreversible.
- Corrección previa de la semántica raíz en leases/grafos, ciclos de revisión
  de PR, recuperación fail-closed de locks, inventario/evidencia host-bound y
  layout de runtime estático.
- Frontera previa al router con la skill global existente `task-framer`; no se
  creará una skill competidora.
- `NoviceEngineeringBrief` opcional, educativo, efímero y separado del contrato
  del resolver.
- Matriz mecánica de ambigüedad `low → critical`.
- Una pregunta material cada vez, con opciones y recomendación.
- Lifecycle lateral atómico `clarification_required → estado reanudado|planned`.
- Reencuadre e invalidación cuando la respuesta cambia el task digest.
- `risk-status` humano y JSON con estado triestado.
- Aviso compacto por sesión o cambio de fingerprint.
- Bloqueo local curado de push directo y alarma remota de procedencia
  `PASS|UNKNOWN|FAIL`.
- `pre-commit` y `pre-push` project-local, reversibles mediante adopción.
- Alarma post-push en GitHub Actions mediante Python estándar.
- Recomendación visible de modo normal, `/plan`, `/goal` o
  `/plan` seguido de `/goal`.
- Conservación de los perfiles iOS, Android, web/PWA, SaaS/backend,
  flujo de textos con IA, híbrido y genérico.
- Documentación, ADR, threat model, adopción, rollback y assurance.

### Excluido

- Comprar GitHub Pro o cambiar el plan del repositorio.
- Presentar los hooks locales como branch protection.
- Impedir técnicamente un push realizado desde la web, API, otro clon o con
  `--no-verify`.
- Deshacer automáticamente un push directo ya recibido por GitHub.
- Instalar plugins, MCP, dependencias o autenticaciones.
- Leer o almacenar credenciales.
- Activar enforcement semántico sin corpus audit y promoción revisada.
- Adoptar automáticamente el sistema en BUSTAFIT, `textosv2` u otros proyectos.
- Publicar, desplegar, fusionar o lanzar releases desde esta fase de diseño.

### 3.1 Trazabilidad del chat `Aplicacionesss`

Se incorporan porque fortalecen el producto sin duplicar autoridad:

- pre-framing automático con la skill canónica `task-framer`;
- descomposición multifrente mediante goals, dependencias e independencia real;
- ownership universal de `.` y coordinación entre worktrees;
- ciclos de revisión que invalidan evidencia del HEAD anterior;
- recomendación visible de `/plan`, `/goal` o ambos;
- `NoviceEngineeringBrief` compacto para explicar arquitectura, orden y gates;
- perfiles iOS, Android, web/PWA, SaaS/backend, texto con IA, híbrido y
  genérico.

Se rechazan o difieren deliberadamente:

- crear otra skill generalista que compita con `verified-workflow` o
  `task-framer`;
- interpretar “varios frentes” como varios writers automáticos;
- cambiar de modo sin intervención visible del usuario;
- añadir campos narrativos al schema cerrado de `TaskEnvelope`;
- persistir un perfil educativo o conversación completa;
- instalar plugins/MCP/dependencias o autenticar servicios automáticamente;
- afirmar ahorro real de tokens sin telemetría;
- tratar una recomendación de recurso como autorización para usarlo.

## 4. Secuencia de estabilización e intake

La v2.1 es un programa de cambios, no un único PR gigantesco. Cada hito nace del
`origin/main` demostrado después del anterior:

```text
PR A — estabilización heredada
  raíz universal de ownership
  + ciclo de revisión del mismo PR
  + recuperación segura de locks
  + inventario y lifecycle sin autoatestación
  + runtime seleccionado por lock antes de import

PR B — pre-framing veraz
  task-framer canónico con salida Markdown real
  + normalización host a TaskEnvelope v1 válido
  + NoviceEngineeringBrief efímero

PR C — Clarification Gate y Risk Sentinel
  contratos host-bound
  + lifecycle lateral
  + warning/guards
  + alarma post-push

PR D — piloto de autoridad
  diff documental estrecho
  + governing policy del squash C ya fusionado
  + primer forward-test remoto autoritativo

promoción posterior
  audit → soft-enforce → enforce
```

No se empieza el siguiente PR por el mero hecho de que los tests locales del
anterior pasen. Se exige autorización para merge, verificación del squash
commit en `origin/main` y una nueva rama desde esa base.

Bootstrap honesto: PR A, B y C no usan el lifecycle remoto autoritativo mientras
el runtime y el adaptador que deben sustituir `--evidence PATH` siguen siendo
candidatos dentro de esos mismos PR. A/B conservan initiative outcome
`pull_request`; C usa `integration` porque su cierre incluye merge manual. Cada
ronda local usa un child `LOCAL-R<n>` outcome commit, TaskStore/lease y cierre
tras observar su commit.
Feedback, CI o base avanzada crean un child y lease nuevos; nunca reabren uno
cerrado. PR A es la excepción legacy: el attestor v1 cierra su state/lease y el
host conserva bindings directos, grant separado y comprobación manual para
push/PR/integración, sin inventar `LocalGitObservation` o contexto v2. Desde
PR B, tras liberar el writer, push/PR usan un `RemoteEffectContext` host-bound
distinto y one-shot para cada efecto (`remote_write` y `pull_request`) con
árbol limpio, no el lease viejo. B requiere una task/contexto separada outcome
integration antes del merge; C deriva el suyo de su initiative. Ningún
contexto remoto autoriza local_write/commit. El receipt
queda `authoritative_lifecycle=false`, y Git/PR se verifica manualmente sin
promover estados. `TaskLease.release()` owner-bound e idempotente evita que un
hito bloquee el siguiente. En C, el provider candidato solo corre en
shadow/audit: no puede certificar el runtime, policy o checks que él mismo
introduce.

Crear o actualizar el PR también es un efecto cerrado, no una frase: desde B
consume `ValidatedRemoteEffectContext(pull_request)`, un
`ValidatedGitHubPullRequestWriteProvider` preautenticado/doctorado de la base,
title/body saneados y `TrustedAuthorization` separada, y produce
`ValidatedPullRequestMutationObservation`. D usa el mismo provider con su
último `PilotTaskContext` pushed. El provider read-only, un plugin/MCP, `gh pr`
raw o JSON no mutan. PR A conserva únicamente la excepción host-direct v1;
ningún candidate crea su propio PR.

PR D nace del squash C ya demostrado en `origin/main`, no modifica runtime,
policy, lock, hooks ni CI y es el primer forward-test autoritativo. Su state
separa `governing_base_commit`, `governing_policy_digest` y
`candidate_policy_digest`. Base, remote, strategy y required checks proceden
exclusivamente de una `GoverningPolicy` opaca cargada del objeto Git de la base
verificada; la policy candidata solo informa drift. Un cambio de policy queda
`policy_change_pending` y solo gobierna una task futura creada después de su
merge.

`GoverningPolicy` tiene productor desde PR A:
`load_governing_policy_from_runtime()` consume el
`GoverningRuntimeObservation` del attestor limpio en la base inmutable, lee el
archivo regular canónico con cap/schema cerrado y liga
runtime/base/lock/policy/session/invocation/TTL. No acepta path, bytes o digest
candidate. Por eso PR B puede usar el wrapper antes de que Risk Sentinel añada
su adaptador posterior para policy instalada.

D entra en `authority_mode=pilot`, no en `ready`: esa capability candidata solo
puede avanzar el TaskStore, branch, base, policy, lock y allowlist documental
exactos del piloto. Ninguna otra task puede usarla. Solo la API host
`build_validated_pilot_inputs()` valida TaskEnvelope, consume
`ValidatedInventory` y ejecuta el resolver con policy+registry gobernantes;
emite un wrapper opaco en vez de confiar en un RouteDecision serializado.
El envelope de D declara `intent=integrate`, `phase=integrate`,
`requested_outcome=integration` y sus efectos reales. Así la ruta
`remote-integration-proof` selecciona la capability `git.remote-proof` sin
atajos narrativos. El registry declara `host.github-gh-read` como recurso
canónico y `mcp.github-pr-read` como sustituto opcional; solo readiness,
authorization y precedencia determinista permiten elegir uno. RouteDecision,
transport wrapper, observaciones y receipts ligan el mismo resource ID+digest.
Un provider no seleccionado no se instancia ni consulta red; ambigüedad o
ausencia queda unresolved/pending.
`start_authority_pilot()` acepta esos inputs y una
`TrustedPilotAuthorization` opaca, TTL/nonce/one-shot y ligada a todos los
bindings; CLI, JSON y `task start` genérico no pueden seleccionar pilot y cada
efecto posterior necesita su propio grant. Todas las mutaciones y Git se anclan
al worktree piloto canónico.
El squash C se obtiene de una `ValidatedManualMergeObservation` opaca: un
attestor limpio reobserva live el PR C exacto, diff completo, checks,
base/head/mergeCommit y estrategia squash, y exige que la ref base siga
exactamente en ese mergeCommit. Receipt, JSON, env o `rev-parse` no pueden
fabricarla; solo sirve para fijar la base gobernante de D y no promueve
lifecycle ni concede autoridad. `start_authority_pilot()` consume el wrapper
en el mismo proceso y deriva la base; un SHA escalar coincidente no basta.
Al finalizar success, pending o abort, la operación libera el writer
owner-bound y cualquier corrección exige piloto nuevo. Provider y revalidación se
ejecutan desde un attestor limpio/detached en `governing_base_commit`; su
launcher valida blobs runtime/provider/lock antes del import y no acepta digests
del candidate. PR `base.sha` y ref base actual deben coincidir con ese commit;
avance de base invalida D. Tras `base_verified` y `risk-provenance == PASS`
publica bajo flock un hint compacto no autoritativo en git-common-dir, separado
de cualquier task ledger. Worktrees futuros usan sus selectores para reobservar
desde otro attestor runtime, lock, governing policy, provider, PR/diff,
merge/base y run/job PASS; borrar el worktree D no lo elimina, mientras upgrade
o drift impiden emitir la capability live. Si el provider ni
siquiera es elegible para pilot, D queda
`pending_github_host_adapter` y no finge cierre autoritativo.

### 4.1 Puerta de entrada

La pieza previa correcta es la skill `task-framer`, no un agente autónomo y no
un hook que interprete lenguaje natural. El hook solo recuerda y audita el
contrato; la skill comprende la petición y el resolver permanece puro.

La skill produce notas Markdown estructuradas. El host/model las normaliza a un
`TaskEnvelope v1`, ejecuta `validate_task_envelope()` y solo entonces llama al
resolver. `resolve_route()` vuelve a validar su entrada. El prompt crudo nunca
entra en el resolver puro ni en un receipt.

Las dependencias multifrente deben referenciar goals existentes, no pueden
apuntarse a sí mismas y el grafo debe ser acíclico. Una referencia ausente o un
ciclo vuelve al host para reencuadre antes de routing; el resolver no inventa
relaciones.

Después de `RouteDecision`, un renderer puro project-local consume el JSON real
de `compact_route_manifest()`, lo valida y puede mostrar un
`NoviceEngineeringBrief`
de máximo 1 KiB para explicar lo entendido, orden, verificación, modo recomendado
y siguiente gate. Es una vista efímera: no modifica `TaskEnvelope`, no autoriza
efectos, no se persiste y no carga una enciclopedia. Referencia solo áreas
pertinentes:

```text
architecture, data, security, testing, debugging, git, networking,
concurrency, ux, observability, release
```

Una tarea pequeña no recibe brief. Una petición multifrente se descompone antes
del router, pero eso no significa ejecutar todos los frentes ni crear varios
writers.

`intake.py` forma parte también del runtime aislado generado por adopt/upgrade;
los tests encadenan router → manifest JSON → renderer tanto en source como en
una instalación adoptada.

### 4.2 Verdad de distribución

El ID canónico continúa siendo `skill.task-framer`. La verdad actual y objetivo
son:

```text
skill ausente                      → unresolved
skill presente                     → inventory ready por disponibilidad
output_contract del registry       → markdown
TaskEnvelope resultante inválido   → el host bloquea antes del router
copias con distinto digest         → E_RESOURCE_AMBIGUOUS
```

El repo no sobrescribe silenciosamente `~/.codex` ni necesita modificar el
schema cerrado de `TaskEnvelope`. Forward-tests de la skill comprueban su
conducta, pero no se convierten en una afirmación criptográfica sobre lenguaje
natural.

## 5. Clarification Gate

### 5.1 Principio

El sistema preguntará únicamente cuando respuestas diferentes puedan cambiar de
forma material:

- el resultado solicitado;
- el comportamiento que debe conservarse;
- el alcance o los contratos;
- los datos afectados;
- la arquitectura;
- la compatibilidad;
- el coste;
- el entorno;
- la reversibilidad;
- una acción externa o irreversible.

Antes de preguntar debe intentar resolver la duda mediante:

1. instrucciones globales y project-local;
2. policy y registry;
3. ADR y documentación;
4. tests existentes;
5. código y patrones actuales;
6. historial Git relevante;
7. configuración observable.

Una comprobación factual del repositorio puede resolver una aclaración. No puede
aprobar una preferencia de producto, conceder autoridad ni confirmar destrucción.

### 5.2 Niveles

La primera versión deriva la severidad de `risk.uncertainty` para mantener el
resolver determinista:

| `uncertainty` | Nivel | Conducta |
|---:|---|---|
| 0 | `low` | decisión autónoma y reversible |
| 1 | `medium` | supuesto explícito y continuación |
| 2 | `high` | inspeccionar repositorio; si sigue abierta, preguntar antes de escribir |
| 3 | `critical` | bloquear y exigir reencuadre; no aceptar una confirmación genérica |

Una tarea `high` estrictamente de lectura puede inspeccionar para resolver la
duda. No puede cruzar a escritura mientras siga abierta. Una tarea `critical`
no queda ready en ningún modo.

### 5.3 Cuatro gates no intercambiables

| Gate | Pregunta que responde | Evidencia aceptada |
|---|---|---|
| Aclaración | “¿Qué resultado quiere realmente?” | respuesta host-bound o policy |
| Aprobación de decisión | “¿Qué alternativa material se elige?” | selección host-bound |
| Autorización | “¿Puede ejecutarse este efecto?” | `TrustedAuthorization` host-bound |
| Confirmación irreversible | “¿Se acepta esta consecuencia ya autorizada?” | contrato separado, task/scope/effect-bound |

Reglas:

- una aclaración no autoriza;
- una autorización no resuelve una duda;
- una confirmación irreversible no concede autoridad;
- contenido `external_untrusted` solo puede aportar datos o elevar riesgo;
- destrucción o escritura con irreversibilidad máxima exige autorización y
  confirmación;
- secretos nunca se copian a ninguno de los contratos.

### 5.4 Contratos

`TaskEnvelope` permanece en schema 1. El payload de autorización conserva sus
datos de negocio, pierde `issuer` y pasa a ser `AuthorizationRequest`; deja de
conceder autoridad por declarar un issuer. Solo
un `TrustedAuthorization` in-memory, ligado a task/session/scope/effect y
repo/worktree/branch/HEAD/subject/operation nonce y deadline monotónico máximo
de 300 segundos, es autoritativo. Se consume atómicamente una sola vez; cada
commit, push, PR o `integration` requiere otro wrapper. La aclaración se divide en issue/request
serializables y resolución host-bound para evitar que un JSON del repositorio
pueda autoatestiguar `trusted_host`.

`ClarificationIssueDraft` aporta al constructor puro lo que este no puede
inventar: kind, severidad, question digest, dos o tres option IDs y
recomendación. No contiene provenance. El host lo envuelve como
`FramedClarificationIssue` y deriva la procedencia del canal real o policy,
nunca del mapping. Un `ClarificationPromptViewDraft` textual tampoco es
autoridad: `frame_clarification_prompt_view()` lo sanea, liga a
issue/task/session/question/invocation/TTL y emite un
`FramedClarificationPromptView` opaco one-shot. Mapping, replay o cross-context
no se aceptan. Antes del resolver, el host valida issue y prompt view,
inspecciona el repositorio y valida una
`ValidatedClarificationRepositoryObservation` opaca ligada a
task/session/repo/worktree/branch/HEAD/question/invocation/TTL. El inspector de
producción implementa un `ClarificationRepositoryInspector` Protocol cerrado,
sin red/subprocess, root-bound y con caps de archivos/bytes; no puede inyectarse
desde CLI o contenido externo. Solo persiste status+digest, no contenido. Un mapping con
`status=resolved`, aunque su digest coincida, no se acepta. El único sentinel
serializable/inerte es `not_checked`, que nunca resuelve nada. El host construye
`ClarificationRequest`, deriva internamente status/digest, consume la
observación y prompt wrapper una vez y lo envuelve como
`ValidatedClarificationRequest` opaco.
No existe deserializador público. El resolver consume ese wrapper y jamás crea
pregunta, opciones, recomendación o evidencia.

`ClarificationRequest` es la salida determinista del constructor host-side
anterior al resolver:

```json
{
  "schema_version": 1,
  "request_id": "clarify-session-policy",
  "task_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "session_id": "session-20260729-001",
  "issue_kind": "clarification",
  "severity": "high",
  "question_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "presentation_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "repository_check": {
    "status": "unresolved",
    "evidence_digest": "sha256:2222222222222222222222222222222222222222222222222222222222222222"
  },
  "option_ids": ["preserve-current-behavior", "change-contract"],
  "recommended_option_id": "preserve-current-behavior"
}
```

`issue_kind` admite únicamente `clarification` y `decision_approval`.
`repository_check.status` admite `not_checked`, `resolved`, `unresolved` y
`conflicting`; su digest es `null` solo con `not_checked`. Evidencia del
repositorio puede resolver únicamente una aclaración factual.

`AssumptionDraft` es serializable y se admite solo para incertidumbre medium:
liga request/task/option y statement digest, pero no declara provenance ni
resuelve por sí mismo. Solo un `ValidatedAssumption` opaco derivado del frame
actual o de policy validada puede entrar al router. JSON externo no puede
autoatribuirse `model_inference|project_policy`. No representa una respuesta
humana, no resuelve high/critical ni `decision_approval` y nunca concede
autoridad.

`ClarificationResolution` contiene IDs y digests, nunca texto:

```json
{
  "schema_version": 1,
  "resolution_id": "resolution-session-policy",
  "request_digest": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
  "task_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "session_id": "session-20260729-001",
  "selected_option_id": "preserve-current-behavior",
  "response_digest": "sha256:4444444444444444444444444444444444444444444444444444444444444444"
}
```

Ese payload no contiene `issuer` ni `provenance`. El canal confiable se aporta
fuera del JSON mediante un wrapper interno no deserializable que solo el
adaptador del host puede entregar a la API. Un mapping byte-idéntico leído de
archivo, prompt, Markdown, skill, Issue, PR, plugin o MCP se clasifica
`external_untrusted` y falla con `C_UNTRUSTED_CHANNEL`.

El CLI `route` solo emite estado y digests: no construye ni ingiere requests,
inventario confiable, `ClarificationResolution`, autorización o
`IrreversibleConfirmation` desde `PATH`, stdin o variables de entorno.
`task clarification-status` puede reemitir únicamente un request/view ya
validados y publicados por el bridge. Sin `HostAdapterCapability`, el route
devuelve `pending_host_capability`; no inventa una pregunta. La policy puede
resolver una decisión solo cuando el host deriva el wrapper directamente de la
policy validada, no porque un payload declare `project_policy`.

El resolver recibe obligatoriamente uno de dos tipos cerrados:
`HostAdapterCapability` o `HostAdapterUnavailable`; inventory, bool, string o
mapping no pueden autoatestiguar disponibilidad. Si la capability está lista
pero falta un request validado, devuelve
`clarification_request_required`; si no está disponible, devuelve
`pending_host_capability`.

Al reanudar, el contexto de repositorio es
`ValidatedClarificationRepositoryObservation |
RepositoryEvidenceNotChecked`. El segundo es un singleton tipado, válido solo
si el request durable declaró `repository_check=not_checked` y la cuestión es
`decision_approval` o una aclaración no factual que la policy no obliga a
inspeccionar. Nunca resuelve incertidumbre factual high/critical ni contradice
un request que exigía repo; mapping/string falla y la task sigue bloqueada.

`IrreversibleConfirmation` contiene `request_digest` y `consequence_digest`,
pero el mapping es solo una solicitud. `TrustedIrreversibleConfirmation` añade
authorization/operation nonce, repo/worktree/branch/HEAD, subject, TTL y
consumo one-shot. Se consume atómicamente junto con el
`TrustedAuthorization` de esa misma operación; replay, auth distinta,
cross-HEAD o expiración fallan. Un mapping aislado devuelve
`I_UNTRUSTED_CHANNEL`.

El wrapper interno es una frontera de integración, no criptografía frente a
código arbitrario ejecutándose como el mismo usuario. La v2.1 no afirma una
garantía mayor: el host debe preservar la procedencia de roles y nunca exponer
una fábrica pública o un deserializador que “promueva” JSON a evidencia
confiable.

No se persiste el prompt crudo ni la respuesta. Para poder reanudar tras restart
o el evento soportado `SessionStart(compact)`, se conserva bajo el Git dir worktree-local una
`ClarificationPromptView` saneada, modo `0600` y máximo 1 KiB con pregunta
generada por el sistema, labels, recomendación y consecuencia. Su digest liga
el request; no entra en receipts ni memoria. Bajo flock se publica y sincroniza
primero el sidecar y después el state que lo referencia. Al resolver/cerrar se
publica y sincroniza primero el state final y después se elimina/sincroniza el
sidecar. Un crash puede dejar un sidecar huérfano eliminable por GC, pero nunca
un state publicado que apunte a una vista no durable. Sidecar ausente o
derivado conserva el bloqueo y nunca regenera wording distinto.
El GC toma el mismo flock per-task, relee state/generation dentro del lock y
solo elimina generaciones antiguas no referenciadas; no puede competir con el
publisher para borrar la vista vigente.

Los command hooks actuales son subprocess independientes que reciben JSON; no
pueden transportar objetos opacos entre `UserPromptSubmit`, routing y
`PreToolUse`. `HostAdapterCapability` solo queda ready si un forward-test
demuestra una API nativa con session/event/tool_use ID y consumo en el mismo
callback. Mientras Codex no la exponga, aclaración/autoridad semántica son
`audit/advisory` y no pueden promover `soft-enforce`/`enforce`. No se inventará
un broker, firma local o factory pública para aparentar esa frontera.

`InventoryObservation`, `LocalGitObservation`, `GitHubObservation` y
`ReleaseProviderObservation` siguen el mismo principio. Un informe JSON puede
auditarse, pero no afirmar readiness ni promover estados probatorios. Si el
adaptador real no está disponible, el estado es `pending_external_evidence`.
Las observaciones ligan observation/invocation ID, task, repo/worktree,
registry o HEAD, y deadline monotónico. Validadores host-side reciben un clock
inyectado; mismatch, expiración o replay fallan antes de llegar al resolver o
mutar lifecycle. Factory, validator y consumo reciben la invocation esperada;
un ID aportado por state/JSON no basta. `RemoteEffectContext` revalida además
en el instante de uso PR number, base SHA y checks digest vivos —incluido
`None` exacto— junto con task/session/effect/HEAD; cualquier drift exige un
contexto nuevo.

### 5.5 Salida del gate

El gate se anida bajo `RouteDecision.interaction.clarification_gate` para no
romper el schema top-level:

```json
{
  "level": "high",
  "status": "ask_user",
  "decision_ready": false,
  "next_action": "ask_one_material_question",
  "blocked_effects": ["local_write"],
  "context_digest": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
  "reason_codes": ["CLARIFY_REPOSITORY_UNRESOLVED"]
}
```

Estados posibles:

```text
autonomous
assumption_required
inspect_repository
pending_host_capability
clarification_request_required
ask_user
authorization_required
confirmation_required
blocked
resolved
```

`pending_host_capability` y `clarification_request_required` son estados
bloqueantes, no aliases narrativos. El primero usa
`C_HOST_CAPABILITY_PENDING` cuando el host no puede producir/consumir wrappers;
el segundo usa `C_CLARIFICATION_REQUEST_REQUIRED` cuando la capability está
lista pero todavía falta un request host-bound válido. Ninguno crea preguntas,
autoriza efectos ni permite `decision_ready=true`.

La UI o Codex formula una sola pregunta: dos o tres opciones mutuamente
exclusivas, recomendación y consecuencia. Las restantes quedan en cola estable.

### 5.6 Lifecycle

`clarification_required` es un estado lateral, no una posición insertada en el
ranking que limita el resultado solicitado:

```text
framed ─────────────→ planned
   │
   └→ clarification_required
       ├→ framed, si task digest no cambió
       └→ planned, si task digest cambió

framed|planned|ready|implementing|verifying|review_ready
   └→ clarification_required
       ├→ resume_state, si el task digest no cambió
       └→ planned, si el task digest cambió
```

Al entrar en `clarification_required` se preservan:

- estado de reanudación;
- task y route digests;
- context y question digests;
- evidencia de inspección del repositorio;
- efectos bloqueados.

La resolución se invalida si cambia cualquiera de esas entradas. Un cambio de
task digest elimina evidencia `ready`, `verifying` y `review_ready`, conserva un
registro compacto de invalidación y vuelve a `planned`. Desde `committed` en
adelante no se rebobina historia: un cambio material bloquea el task y exige una
nueva unidad de trabajo. El estado `blocked` conserva su significado operativo
actual y no se reutiliza para permitir un resume genérico de una ambigüedad
crítica.

El camino genérico `TaskStore.transition()` y el CLI rechazan entrar o salir del
estado lateral. `TaskStore.require_clarification()` —invocado por el bridge tras
validar request e inventory host-bound— es la única entrada.
`resolve_and_resume_clarification()` revalida y consume una sola
`TrustedInteraction` y escribe directamente el estado final. Todas las
mutaciones usan flock per-task, relectura bajo lock y generation/CAS; no se
persiste un estado `clarified` intermedio. Crash antes del replace conserva la
pregunta; después conserva el destino. Si Codex no expone session/event identity
verificable, el gate queda advisory o bloqueado; no existe fallback serializado.

Si la aclaración interrumpe `implementing`, el state conserva owner session,
lease digest, scope y digest de changed paths. La misma sesión solo vuelve a
`implementing` tras revalidar exactamente task/repo/worktree/branch/policy,
lease y paths actuales. Otra sesión nunca adopta ese writer: queda bloqueada y
requiere una recuperación explícita más una task/decision/lease nuevas. Los
leases no expiran ni se transfieren por reloj o PID. El rescue exige
`TrustedLeaseRecoveryAuthorization` host-bound, one-shot, todos los bindings del
owner original e inventario completo; escribe tombstone durable, conserva el
árbol dirty e invalida la task anterior. Si esa prueba falta, el lease continúa
bloqueando deliberadamente.

Cuando una operación toca lease y state, el orden global es common-dir lease
flock seguido del per-task flock; nunca al revés y nunca durante red o
subprocess. Finalizar el piloto usa un marker `finalizing` durable,
`pilot_finalized=true/resume_forbidden=true`, release owner-bound y publicación
del destino mediante un protocolo recuperable. Un crash puede dejar
marker+lease sin tombstone, marker+lease+tombstone o marker+tombstone sin
lease. Recovery valida owner/digest/generation, reanuda unlink/fsync cuando
existen lease+tombstone y solo publica el destino cuando el lease ya no existe.
Cualquier otra combinación falla cerrado; jamás hay lease ausente con estado
escribible y generic resume rechaza todas las fases hasta completar recovery.

Close, suspend/reframe y abandon recovery usan el mismo patrón con markers
`finalizing_close|finalizing_suspend|finalizing_abandon`,
`resume_forbidden=true`, `_release_locked()` y destino durable. Recovery se
deriva de marker/state/tombstone y nunca necesita reconstruir un wrapper opaco
perdido. `TaskLease.release()` público toma el flock; una transacción que ya lo
posee usa un `LeaseLockToken` opaco para no readquirirlo. Del mismo modo,
`TaskLease.acquire()` observa Git fuera del lock y una transacción ya bloqueada
usa `_acquire_locked(LeaseLockToken, ValidatedWorktreeInventoryObservation)`;
ese helper no relockea ni ejecuta subprocess y detecta cualquier race del
registro worktree por identity digest. La aclaración dirty
que cambia task digest suspende/libera antes de bloquear para reframe; no deja
lease antiguo asociado a `planned`.

Un verifier con gate fallido no intenta `blocked→close`. La API especializada
`abort_verification()` publica
`finalizing_verification_abort/resume_forbidden`, libera el lease owner-bound y
termina `blocked/verification_aborted/resume_forbidden`; recovery usa las mismas
tres fases. Solo una ronda local/verifier nueva puede continuar.

Un ciclo normal de feedback de un PR no es un reencuadre. Desde `pr_draft` o
`pr_ready`, `TaskStore.start_revision()` conserva el número/identidad del PR,
incrementa revision, invalida commit/push/checks ligados al HEAD anterior y
adquiere atómicamente un TaskLease nuevo mediante `_acquire_locked()` antes de
publicar `implementing`. Solo
admite review feedback o checks fallidos sobre la misma base; `base_advanced`
exige suspend/release y task nueva. El nuevo camino debe pasar otra vez por verificación,
review, commit, push, Draft/ready y checks. Desde `merged` no existe ese retorno:
se crea una task nueva.

## 6. Risk Sentinel

### 6.1 Triestado

Cada dimensión devuelve:

```text
PASS
UNKNOWN
FAIL
```

Precedencia:

```text
FAIL > UNKNOWN > PASS
```

`UNKNOWN` nunca se representa como verde. La evaluación local y la remota se
mantienen separadas:

```json
{
  "schema_version": 1,
  "command": "risk-status",
  "ok": false,
  "status": "UNKNOWN",
  "dimensions": {
    "local": {
      "status": "PASS",
      "checks": [],
      "errors": []
    },
    "remote": {
      "status": "UNKNOWN",
      "checks": [],
      "errors": [
        {
          "code": "RS_REMOTE_NOT_OBSERVED",
          "message": "Remote protection or provenance has not been observed."
        }
      ]
    }
  },
  "facts": {
    "base_branch": "main",
    "remote": "origin"
  },
  "errors": []
}
```

Exit codes:

- `PASS`: 0;
- `FAIL`: 1;
- `UNKNOWN`: 2.

### 6.2 Dimensión local

Reutiliza el preflight read-only y añade:

- policy y lock válidos;
- rama y worktree;
- detached HEAD;
- base actual;
- divergencia respecto a `origin/<base>`;
- árbol dirty con o sin lease válido;
- presencia y digest de guards Git;
- `core.hooksPath`;
- modo y trust de hooks;
- route y task state, si existen;
- aclaración pendiente;
- autoridad requerida;
- perfil técnico y recomendación de interacción.

Códigos mínimos:

```text
RS_LOCAL_POLICY
RS_LOCAL_LOCK
RS_LOCAL_REPOSITORY
RS_LOCAL_BASE_BRANCH
RS_LOCAL_DETACHED
RS_LOCAL_BASE_DIVERGENCE
RS_LOCAL_DIRTY
RS_LOCAL_HOOK_PATH
RS_LOCAL_HOOK_DIGEST
RS_HOOK_TRUST
RS_HOOK_MODE
RS_CLARIFICATION_REQUIRED
RS_AUTHORITY_REQUIRED
RS_PROFILE
RS_TASK_STATE
```

Un `RouteDecision` leído desde archivo es solo hint diagnóstico. Los checks de
aclaración y autoridad solo pueden ser PASS mediante un
`ValidatedHostRiskContext`
entregado y consumido por una API host nativa en el mismo callback. Los command
hooks JSON actuales no satisfacen esa frontera. Sin
`HostAdapterCapability=ready`, son UNKNOWN/advisory, la promoción semántica se
bloquea y un JSON nunca se promueve. Excepción cerrada: cuando no existe task,
route ni efecto solicitado, `RS_AUTHORITY_REQUIRED` puede ser
`PASS/reason=NOT_APPLICABLE` sin contexto; eso no absuelve un efecto ni
atestigua que no haya autoridad pendiente. En cuanto existe task/route/efecto,
la ausencia del wrapper vuelve a UNKNOWN.

### 6.3 Aviso compacto

Ejemplo:

```text
CONTROL PLANE RISK
local=PASS remote=UNKNOWN action=CONTINUE_WITH_CAUTION
reason=RS_REMOTE_PROTECTION_UNVERIFIED
safe_path=feature→commit→push-feature→PR→checks→authorized-merge
interaction=plan_then_goal automatic_change=false
```

`render_risk_warning()` produce el mismo payload cerrado <=4 KiB para
UserPromptSubmit, fingerprint cambiado, trigger conceptual `post_compact` y
acción roja; las dos últimas acciones de rehidratación/riesgo inmediato nunca
se silencian por el dedupe normal. Precedencia: FAIL→STOP, local UNKNOWN→PAUSE_AND_VERIFY,
local PASS + remote protection UNKNOWN→CONTINUE_WITH_CAUTION /
`RS_REMOTE_PROTECTION_UNVERIFIED`, PASS/PASS→SAFE_PATH_CONFIRMED. Reutiliza
`InteractionRecommendationView` solo después del framing, conserva
`automatic_change=false` y nunca ejecuta el safe path ni `/plan`/`/goal`.
El renderer recibe únicamente la `GoverningPolicy` opaca de la base/manifest
instalado; una policy candidata o mapping serializado solo informa drift y no
puede cambiar safe path, base, remote ni severidad.

`UserPromptSubmit` precede al task-framer/router del prompt nuevo. Por tanto
emite solo riesgo observable más `interaction=pending_framing`: no inventa tier,
route digest o recomendación. Tras framing, el `NoviceEngineeringBrief` muestra
la recomendación accionable y publica una `CurrentWarningView` saneada ligada a
task/route/fingerprint.

Se emite:

- en el primer `UserPromptSubmit` de una sesión;
- cuando cambia el fingerprint;
- inmediatamente antes de una acción roja;
- después de compactar, como estado mínimo vigente, aunque el fingerprint no
  haya cambiado.

El fingerprint liga policy, registry, lock, rama y resultado local. El nombre
del archivo de sesión es un hash del session ID y vive bajo el Git dir del
worktree. No se persisten prompt, comando, documento externo ni secreto.
El dedupe (`schema/fingerprint/timestamp`) y la vista rehidratable son archivos
distintos `0600` bajo el Git dir, ambos con nombre
`sha256(session_id)`. `CurrentWarningView` contiene solo el payload cerrado,
task/route/fingerprint/payload digests y generation. Publish/load/GC comparten
flock, usan temp+fsync+replace+fsync-dir y nunca cruzan sesiones ni reutilizan
una vista corrupta o stale.

El evento instalado real sigue el schema ya soportado:
`SessionStart` con matcher/source `compact`. El adapter valida ese raw event y
lo normaliza a `post_compact`; no presupone un evento literal `PostCompact`.
Este camino siempre carga/reemite la vista current-task exacta o un warning
mínimo UNKNOWN/pending-framing, sin consultar ni alterar el dedupe de
UserPromptSubmit.

Si falta un session ID confiable, el hook puede repetir el aviso; nunca inventa
silencio seguro.

### 6.4 `PreToolUse`

El conjunto curado detecta:

- `git push origin main`;
- `git push origin HEAD:main`;
- cualquier refspec cuyo destino sea la rama base;
- borrado de la rama base;
- `--all` y `--mirror`;
- push implícito desde la base hacia su upstream;
- force push;
- `reset --hard`;
- `clean -f`;
- `rm -rf`;
- escritura local cuando existe una aclaración crítica pendiente;
- escritura fuera del scope de un TaskLease vigente;
- task, worktree, branch, session o policy distintos de los ligados al lease;
- MCP como punto de autorización y egress, no como denegación automática.

Comandos simples se analizan con `shlex`. Shell compuesto, alias o sintaxis no
demostrable produce `UNKNOWN`, nunca una declaración de seguridad. El
clasificador no puede reescribir el comando crudo: solo
`scripts/control-plane safe-read --repo <raíz-canónica> -- <argv permitido>`
puede ser `read_only_known`. `--repo` es obligatorio, no admite symlink, debe
coincidir con un worktree de una
`ValidatedWorktreeInventoryObservation` fresca y one-shot obtenida en la misma
invocación, y el resultado liga un digest de root/git-dir/common-dir; mapping,
replay, TTL, cross-common-dir, cap+1 o inventario parcial fallan antes del
subprocess. El `cd` interno de un launcher procedente de otro checkout no
sustituye ese target. Ese wrapper ejecuta sin shell, con
timeout/output cap y entorno saneado: `GIT_OPTIONAL_LOCKS=0`,
`GIT_NO_LAZY_FETCH=1`,
`GIT_TERMINAL_PROMPT=0`, pagers desactivados, fsmonitor false, config
global/system anulada, ext-diff/textconv desactivados y sin askpass, SSH ni
variables proxy; `rg` usa `--no-config` y no hereda `RIPGREP_CONFIG_PATH`. La
gramática piloto cerrada admite `rg --no-config --quiet -e PATTERN --
<single-in-root-path>` y rechaza targets adicionales, patrón desde archivo,
glob o escape. La gramática Git cerrada admite solo `status --short`,
`diff [--cached] --check`, `diff [--cached] --name-only` y
`diff --exit-code` con base/ref/path allowlisted, además de selectores
read-only cerrados de log/show. Eso cubre los gates staged y post-commit del
piloto; flag, pathspec o selector extra falla. `rg --pre`, Git
`-c/--config-env`, pager, aliases, drivers o flags no enumeradas fallan. Un
`git diff`, `git status` o `rg` crudo es `read_only_unsanitized`, no la variante
segura que nunca llegó a ejecutarse. Canarios verifican índice, pager,
fsmonitor, diff driver, textconv, preprocessor y que un partial clone no inicia
lazy fetch ni transporte.

Edit/Write usan su `file_path`; apply_patch valida todos los headers de archivo;
Bash usa cwd y el parser curado; MCP usa metadata de effect/egress del registry.
Las rutas se confinan al root sin symlink escape. En soft-enforce, una escritura
sin scope/lease/identidad demostrables se deniega.

Comportamiento:

| Modo | Acción |
|---|---|
| `audit` | permite read-only conocido; avisa todo efecto no demostrado |
| `soft-enforce` | deniega destructivos/base push, shell ambiguo y escritura sin lease/scope/identidad |
| `enforce` | conserva lo anterior y deniega todo gate mecánico rojo aplicable |

El clasificador Bash cerrado distingue read-only conocido, efecto Git, escritura
con paths conocidos, comando que puede escribir paths desconocidos, shell
ambiguo y destructivo. Tests/builds requieren lease raíz; writes parseables
requieren lease sobre sus targets; el lease nunca autoriza destrucción.
También consume la misma `GoverningPolicy` opaca; no acepta policy desde
TaskEnvelope, hook input, PATH, JSON o el worktree candidato.

El matcher existente se conserva mientras cubra las tools observadas. El hook
continúa siendo una defensa cooperativa y revisable mediante `/hooks`.
PreToolUse no puede recordar un `TrustedAuthorization` de otro subprocess. Solo
un handshake nativo one-shot ligado a session/tool_use/task/effect podría
autorizar en ese callback; si no existe, muestra `ask`/advisory o deniega según
policy, nunca reconstruye autoridad desde JSON.

Antes de confiar hooks en macOS, `run_macos_hook_smoke()` es un runner stdlib
cerrado de un solo proceso: verifica Darwin, posee Popen/caps/timeouts/repos
temporales/snapshots y cubre warning once,
`SessionStart(compact)→post_compact`, `safe-read --repo`, feature allow,
base/detached/force deny, Stop/receipt y rollback byte-idéntico en
source/isolated. Produce `CompletedMacOSHookSmoke` one-shot y el mismo proceso
publica un receipt ligado a repo/HEAD/digests sin output crudo. No necesita un
evento host opaco para satisfacer el gate mecánico de C en audit.

Ese receipt no autoriza ni promueve hooks. Una observación nativa del proceso,
si existe, aporta assurance adicional; una revisión humana separada en
`/hooks` produce otro receipt ligado a los mismos hashes. Smoke mecánico
FAIL/UNKNOWN bloquea C. Smoke PASS con evento nativo o revisión humana ausentes
permite C solo en audit, conserva `pending_hook_trust` y bloquea
soft-enforce/enforce hasta completar ambos gates de promoción.
Los dos publishers forman una cadena CAS: el smoke devuelve receipt más
`VerificationTaskContext` refrescado; el publisher de revisión acepta
únicamente ese contexto, rota generation y devuelve otro. Mapping, contexto
stale, generation suelta o replay no publica.

### 6.5 Guards Git

Se versionan:

```text
.codex/git-hooks/pre-commit
.codex/git-hooks/pre-push
```

`pre-commit` bloquea commit en base o detached HEAD.

`pre-push` consume todas las líneas:

```text
<local-ref> <local-oid> <remote-ref> <remote-oid>
```

y bloquea cualquier actualización o borrado de
`refs/heads/<base>`. También bloquea un non-fast-forward demostrable.

La adopción:

1. captura todos los valores, origin y scope de `core.hooksPath`, además del
   estado local separado;
2. bloquea valores múltiples, heredados, worktree o includes no gestionados,
   además de otro hooksPath o un hook ejecutable no
   gestionado en el directorio default;
3. publica bajo
   `<git-common-dir>/codex-control-plane/installs/<manifest-digest>/` un
   snapshot inmutable de runtime, ProtectedGitPolicy y guards, y verifica
   digests/permisos;
4. fija `core.hooksPath` al path absoluto del directorio `git-hooks` de ese
   snapshot instalado;
5. registra el cambio en el journal;
6. verifica archivos, permisos y config;
7. elimina exactamente la definición local creada durante rollback, sin copiar
   un valor heredado al scope local.

`core.hooksPath` local es compartido por los worktrees del repositorio. Al
apuntar a un snapshot absoluto bajo el common-dir, una rama histórica no puede
debilitar el guard editando o careciendo de `.codex/git-hooks`. Las fuentes
versionadas solo cambian la instalación activa mediante `upgrade apply`
transaccional y autorizado. El riesgo residual visible es `--no-verify` y la
mutación manual de config/instalación por el mismo usuario, no la resolución
relativa por worktree.

Los guards pueden omitirse con `--no-verify`; la alarma de CI existe para
detectar, no para fingir que esa omisión es imposible.

Los leases conservan su JSON bajo el Git dir específico del worktree, pero la
adquisición usa un `fcntl.flock` en el git-common-dir y, dentro de esa sección
crítica, inspecciona de forma acotada los leases de todos los worktrees
registrados. El archivo común es solo coordinación, nunca ledger. Estado
ilegible o identidad incompleta bloquea como `UNKNOWN`.

Adopción y upgrade sustituyen el lock persistente `O_EXCL` por `fcntl.flock`.
Así, un proceso muerto libera la exclusión y el siguiente proceso puede alcanzar
la recuperación transaccional. Un owner pointer mínimo en git-common-dir apunta
a un manifiesto inmutable worktree-local, nunca al digest de un journal mutable.
El WAL usa generaciones checksummed y encadenadas y termina con un marcador
`COMMITTED`. Bajo el flock, se sincronizan en orden manifest, pointer, cada
generación y marcador; solo entonces se elimina y sincroniza el pointer. Otro
worktree valida manifest/owner/cadena y recupera desde la última generación o
limpia un pointer residual si `COMMITTED` ya es durable. Path, chain, target o
generation ambiguos fallan cerrados. No se infiere seguridad por la edad de un
PID ni se borra un lock a ciegas.

El lock añade `runtime_layout=source|isolated` con relación cerrada a package y
ruta. Launchers distintos por layout validan runtime obligatorio, no vacío,
confinado y digestado antes del primer import. Source ignora un runtime aislado
espurio; adopted ignora un `control_plane/` raíz. Nunca se elige código porque
un directorio exista.

### 6.6 Alarma post-push

El workflow se ejecuta únicamente en `push` a la base:

1. lee el evento GitHub y exige coincidencia entre `GITHUB_REPOSITORY`,
   `repository.full_name` y la identidad canónica `owner/repo` renderizada desde
   el remote de policy, independiente de su alias local;
2. rechaza push forzado o borrado;
3. compara `before...after` mediante API;
4. enumera todos los commits introducidos con paginación acotada;
5. enumera por completo todos los Pull Requests asociados a cada commit;
6. devuelve `PASS` solo cuando el conjunto exhaustivo contiene para cada
   commit exactamente un candidato compatible: PR fusionado cuya
   base es la rama configurada y la evidencia concuerda con
   `git.integration_strategy`;
7. devuelve `FAIL` solo para forced/delete o contradicción **exclusiva**
   demostrada después de agotar todas las páginas; un PR open/base distinta no
   hace FAIL si el conjunto contiene el único merged compatible, y dos
   compatibles devuelven UNKNOWN por ambigüedad;
8. devuelve `UNKNOWN` para asociación vacía tras retries, 401, 403, 404, 429,
   5xx, timeout, JSON malformado, truncación o carrera no resoluble.

Utiliza Python estándar, `GITHUB_TOKEN` desde entorno y permiso mínimo:

```yaml
permissions:
  contents: read

jobs:
  risk-sentinel:
    permissions:
      contents: read
      pull-requests: read
```

El token nunca aparece en output, excepción, fixture o receipt. La alarma vive
en un workflow separado, con group por `event.after` y
`cancel-in-progress: false`; un segundo push no puede cancelar la observación
del primero. Adopción renderiza trigger/base desde la policy del target y puede
revertir ese archivo; no hardcodea `main` en proyectos cuya base sea otra.

El workflow invoca `risk-provenance`, cuyo exit depende solo de la dimensión
remota. No agrega el estado local del checkout Actions detached/base; tampoco
lo falsifica como PASS. `risk-status` conserva el agregado para uso local.

La alarma ocurre después del push. No deshace el commit, no impide que un actor
modifique el workflow y no sustituye un check obligatorio.

La primera implementación certifica únicamente
`integration_strategy = "squash"`, que es la policy actual. Para
`merge-commit` o `rebase-merge`, devuelve
`UNKNOWN/RS_REMOTE_STRATEGY_UNSUPPORTED` hasta disponer de una prueba de
procedencia específica; no convierte una estrategia no implementada en un
falso `PASS` ni en un falso positivo de push directo.

La Action no muta el TaskStore local. Un `HostGitHubLifecycleProvider` separado
se ejecuta desde un worktree attestor limpio/detached en la base gobernante, no
desde el candidate. El launcher valida runtime/provider/lock antes del import y
emite una `GoverningRuntimeObservation` opaca; después usa `gh api`
preautenticado o connector host ya doctorado en el mismo proceso, lee task y
`GoverningPolicy`, consulta GitHub y crea/consume una `GitHubObservation`
fresca. No acepta evidence/repo/HEAD/token/policy ni digests gobernantes por
PATH o stdin. La candidata no gobierna su propio PR; C usa shadow y D es el
primer piloto autoritativo. Sin provider queda `pending_github_host_adapter`.

El cliente comparte endpoints/evaluadores puros sobre un `GitHubTransport`.
Cada método devuelve `GitHubObject[T]` o `GitHubPage[T]`, conservando status,
request digest, `Link`, page/per-page y `total_count`; perder esa metadata
convierte completitud en UNKNOWN. Actions usa un transporte urllib cuyo único
poseedor del `SecretValue` es el entrypoint CI. Antes de envolver el token crea
un `GitHubEndpointBinding`. v2.1 soporta remote lifecycle autoritativo solo en
github.com y exige `GITHUB_SERVER_URL=https://github.com` más
`GITHUB_API_URL=https://api.github.com`; GHES queda
`pending_remote_host_unsupported` antes de leer credenciales. Mismatch,
userinfo, Unicode o redirect cross-host falla antes de DNS/red.

Local usa `GhCliTransport` con `gh api` preautenticado y jamás ejecuta
`gh auth token`. Su argv directo fija siempre `--include --method GET
--hostname <bound-host>` antes de cualquier query `-f`, para preservar
status/Link y evitar que `gh` cambie implícitamente a POST. Un fake executable
prueba el proceso/framing real, no solo parsers puros. Su subprocess usa un env
allowlisted que elimina
tokens GitHub/GH, host/repo/config overrides y desactiva prompts; si la auth
preexistente no sobrevive al saneado, queda pending sin fallback. Ambos
transportes aplican los mismos caps, timeout, límite de bytes y redacción.

El registry no considera `host.github-gh-read` ready por tener
`builtin://`/`automation`. Hasta que un doctor host-bound pruebe ejecutable,
auth, salud, repo y host, inventory conserva authenticated/healthy UNKNOWN y
ready=false. `authorized_for_task` es otra condición; `supersedes` solo oculta
`mcp.github-pr-read` cuando las cinco dimensiones están listas. Para PR D se
exige específicamente el ID+digest gobernante `host.github-gh-read`; un MCP o
connector alternativo puede servir rutas generales, pero deja D
`pending_github_host_adapter`.

Los modos son `shadow`, `pilot` y `authoritative`. Pilot no significa ready:
está ligado por código a D y su allowlist y no habilita otras tasks. Solo tras
base_verified/provenance se publica un hint project-wide durable bajo
git-common-dir. Es un índice no confiable, no una capability. Authoritative
exige reconstruir en cada uso, desde un attestor limpio, runtime/policy/provider
y la cadena GitHub exacta del piloto: PR, files allowlisted, merge/base y
workflow run/attempt/job PASS. Un mapping, todos los digests públicos o la mera
existencia del archivo no conceden la capability.

`provenance` no es un JSON de CI. El attestor consulta el run post-merge exacto
y crea/consume una `GitHubWorkflowProvenanceObservation` opaca ligada a
repo/workflow path y blob digest/push event/run attempt/merge y base
SHA/provider/task/TTL. Solo un run único, último, completed y PASS permite
publicar. El job YAML tiene key `risk-sentinel`, display name
`risk-provenance`; GitHub debe observarlo `completed/success`, que corresponde
al exit 0/PASS interno del comando, no a una conclusion literal llamada PASS.
Runs y jobs se enumeran con paginación/caps propios, `total_count`
estable, IDs únicos y `Link` completo; la tentativa actual se relee y se acota.
Cap+1, truncación, página posterior omitida, duplicado, total cambiante,
missing, ambiguo, stale, rerun, cross-repo/SHA, UNKNOWN o FAIL conservan
pending.

El piloto no salta estados locales. `start_authority_pilot()` nace en
`implementing`; wrappers host-bound frescos llevan a `verifying` y
`review_ready` con preflight y los cuatro `CompletedSafeRead` del target
exacto. `advance_pilot_local_commit()` exige review_ready, observa el commit
allowlisted, publica `committed` y rota HEAD/generation.
`advance_pilot_push()` exige otra `LocalGitObservation` que demuestre
tree/index limpios y `origin/<feature> == current_head`, y publica `pushed`.
Solo ese último contexto puede producir `pr_draft`; mapping, replay o
generation anterior no avanzan.

Tras restart/compaction, un piloto no usa `TaskStore.resume()` genérico.
`resume_authority_pilot()` revalida bajo locks state+lease, runtime/policy/
registry/inventory, target/branch/HEAD/scope y autorización nueva; para fases
remotas reobserva feature ref, PR/checks o merge/base con
`host.github-gh-read`. Rota session/generation y vuelve stale todo contexto
anterior. Drift, provider ausente, lease perdido o state finalizing/finalized
falla cerrado; fault tests cubren crash tras pushed, pr_draft, pr_ready y
merged sin duplicar efectos.

Para `pr_ready`, la policy declara el set cerrado de checks obligatorios
`name + app_slug + allowed_conclusions`. El provider exige PR number, repo,
base/head refs, HEAD SHA, `PR.base.sha` y ref base SHA iguales al
`governing_base_commit`, open/no draft y paginación completa; missing, pending,
failing, stale, base avanzada, rerun/duplicado ambiguo o truncado no promueve.
Una task ordinaria solo rota governing context tras incorporar base y recalcular
task/decision/lease; cambio de policy/provider/gates exige task nueva y D exige
siempre piloto nuevo. `merged` conserva PR/head/revision, exige merge commit
exacto y, para la policy squash, un único parent exactamente igual a
`governing_base_commit`; ser mero ancestro no basta. `base_verified` exige que
la live base sea exactamente ese merge commit. El avance normal del merge es válido, pero
un commit adicional antes de verificar devuelve UNKNOWN y no hint/capability. Cada
transición consume una observación distinta. Apple/release permanece
`pending_external_evidence`.

La migración del repo actual propone exactamente
`verify/github-actions/[success]`; `macos-smoke` sigue fuera mientras su
conclusión diseñada sea `skipped`. Es solo un candidate: antes de escribir
policy se reobserva la metadata en la base gobernante, se presenta un
`ProjectRemotePolicyUpdateDraft`, un evento nativo produce
`ProjectRemotePolicyDecision` ligado al target y una autorización
`local_write` separada permite `apply_project_remote_policy_update()`. Esta
factoría y operación ya están fusionadas desde PR A y se ejecutan mediante el
runtime gobernante inmutable, nunca por el candidate C. La operación
policy-only funciona bajo el lease dirty, preserva el resto de la policy y
nunca renderiza/copia `MANAGED_FILES`; `adoption_apply()` completo
queda prohibido dentro de Task 9. Sin cualquiera de esas pruebas queda
`pending_remote_policy_configuration` y los bytes no cambian.

Después de `base_verified`, el sistema distingue explícitamente remoto y copia
local. Reobserva el inventario y localiza el worktree registrado de
`policy.git.base_branch`. Solo con worktree único, limpio, no detached,
fast-forwardable y autorización separada con subject `local_base_sync`, una task
`operate/local_change` con `allowed_effect=local_write` y
`operation=local_base_sync_ff` ejecuta
`merge --ff-only <policy-remote>/<base>` y publica
`LocalBaseSyncReceipt`. Si está ausente, ambiguo, dirty, detached, diverged o
sin autorización, no muta nada y muestra `LOCAL_BASE_NOT_SYNCED` con
path/HEAD/razón/safe-next-step. Esto nunca rebaja la prueba de
`origin_base=VERIFIED`, pero evita afirmar al usuario que su `main` local ya está
sincronizado cuando no lo está.

El cierre post-merge es otra decisión explícita. Con PR/head/base_verified,
inventario fresco, feature worktree limpio y cero leases/children vivos, el
host presenta `retain|remove_local|remove_local_and_remote`. Sin evento nativo
fresco conserva todo y muestra `POST_MERGE_CLEANUP_PENDING`; `merged` nunca
autoriza borrado por implicación. `remove_local` retira primero el worktree y
después la rama local, con grants `local_write` distintos y reobservación entre
efectos. Una feature fusionada por squash usa
`delete_validated_local_feature`: liga PR/head/merge/base exactos y ejecuta CAS
`update-ref -d` con el old OID esperado, no `branch -D` ni ancestry.
`remove_local_and_remote` necesita además autorización `remote_write` y una
operación cerrada sobre la ref exacta del PR head, nunca base/protected refs.
Todo resultado produce `PostMergeCleanupReceipt` o conserva un pendiente
visible con safe-next-step.

La API no demuestra qué botón de GitHub produjo una topología de un solo commit:
un squash y un rebase de un PR de un único commit pueden ser
indistinguibles. Por tanto, v2.1 certifica únicamente
`squash-compatible topology/result` —un commit resultante, parent exacto G,
diff/head/PR/checks ligados—, no el gesto UI. Merge commit con dos parents,
rebase multicommit o cualquier topología distinta queda UNKNOWN. Si una policy
futura exige demostrar el botón exacto, debe aportar evidencia externa nueva;
no puede reutilizar este PASS.

Los límites cerrados son `PER_PAGE=100`, `MAX_CHECK_PAGES=3` y
`MAX_CHECK_RUNS=250`. El número único recolectado debe coincidir con un
`total_count` estable y no puede existir `Link` a otra página tras el cap.
Cap+1, total cambiante, página extra, duplicado o duda de completitud devuelve
UNKNOWN. Los tests cubren cap-1, cap y cap+1.

### 6.7 Efectos Git cerrados y lectura gobernante

Ninguna etiqueta `git_effect` autoriza Git genérico. Solo existen estos
`ClosedGitEffectOperation`:

```text
fetch_policy_remote
push_validated_feature
delete_validated_remote_feature
stage_allowlisted_paths
commit_staged_change
create_authorized_worktree
remove_authorized_worktree
delete_ephemeral_branch
delete_validated_local_feature
local_base_sync_ff
```

`build_validated_git_effect_request()` recibe task/contexto/estado,
inventory fresco, governing policy, lease cuando corresponda, repo/common-dir/
worktree/branch/HEAD/scope, subject y un `TrustedAuthorization` one-shot del
efecto exacto. Rechaza `fetch_policy_remote`: esa operación solo puede crearse
mediante `build_fetch_policy_remote_request()` con dos grants atómicos
`network_read` + `local_write` y un `RemoteRefMutationGuard` ligado a
remote/base/preimage/inventory/session/invocation.
`prepare_remote_ref_mutation()` es side-effect-free. La factoría especializada
revalida la preimage y consume ambos grants o ninguno bajo orden de locks
cerrado; solo entonces arma la guarda y publica intent bajo el common-dir mutex.
Libera el lock antes de Git y vuelve a adquirirlo para reobservar; carrera
externa, crash, replay o postimage incoherente queda STALE/UNKNOWN.
`lease=None` significa que no edita archivos del worktree, no que sea una
lectura local. El host bridge posee las plantillas argv; el caller nunca aporta
shell, flags, remote/refspec/pathspec o mensaje libre.
`execute_closed_git_effect()` usa `shell=False`, cwd canónico, entorno saneado,
timeout/caps y snapshots antes/después. Cada push, stage, commit,
creación/retirada de worktree, borrado local/remoto de feature y sync local
necesita request, grant e invocation distintos; cada fetch necesita sus dos
grants y guarda propios. No admite force, reset, clean, pull, rebase, base push
ni borrado recursivo.

Los worktrees se mutan bajo el protocolo
inventory→common-dir mutex→Git fuera del lock→reobserve. El piloto, attestor y
verifier se crean con `create_authorized_worktree`; verifier/attestor se retiran
con `remove_authorized_worktree`, y solo una rama efímera sin commits propios
usa `delete_ephemeral_branch`. Stage y commit del piloto usan
`stage_allowlisted_paths` y `commit_staged_change`; un lease o grant de commit
no autoriza stage. La rama feature fusionada se conserva por defecto; solo una
decisión post-merge fresca puede ordenar
`delete_validated_local_feature`, ligada a la prueba exacta y con CAS sobre la
ref local; nunca se reutiliza `delete_ephemeral_branch` para saltarse squash.
Con un grant `remote_write` adicional puede ejecutar
`delete_validated_remote_feature`. El receipt distingue `RETAINED`,
`REMOVED_LOCAL`, `REMOVED_LOCAL_AND_REMOTE`, `ALREADY_ABSENT` y `PENDING`.

La lectura autoritativa pasa por `safe-read --repo <root>`. Git se ejecuta con
prompts, lazy fetch, aliases, pager, diff/textconv/fsmonitor, proxies y replace
objects deshabilitados, incluido `GIT_NO_REPLACE_OBJECTS=1`; un canario con
`refs/replace` demuestra que se observan objetos originales. La forma cerrada
`secret-scan-governing -- <single-path>` usa el pattern-set digestado del
runtime gobernante y nunca imprime matches. Un `rg` ad hoc no satisface ese
gate. Cada `CompletedSafeRead` liga root/git-dir/common-dir, invocation,
argv/grammar, exit, caps y digest; un comando raw equivalente es solo
diagnóstico.

### 6.8 Verificación, uso real de recursos y assurance

El verifier no acepta `task_kind`, profile o runtime genérico desde CLI. Dos
factorías host-only producen tipos opacos distintos:

```text
CandidateAssuranceBootstrapAuthority
  = attestor gobernante + candidate worktree/HEAD C

GoverningBaseBootstrapAuthority
  = attestor + verifier worktree separados, ambos ligados al mismo base commit
```

`create_verification_task_bootstrap(task_id, authority=...)` deriva el profile
por el tipo del wrapper:
`control_plane_assurance` o `governing_base_verification`. Persiste profile,
runtime y target digests en TaskStore; mapping, string de kind o target piloto
reutilizado como verifier falla. `_run_verification_command()` es privado y
`verification-run --repo --task-id` ejecuta el profile completo en un solo
proceso; no acepta argv, command ID, result ni receipt.

`control_plane_assurance` vuelve a ejecutar sobre el HEAD final suite normal,
assurance/mutation, benchmark determinista de 10.000 recursos, policy,
registry, doctor, integración, seguridad y diff. El profile completo tiene
deadline menor de cinco minutos. `governing_base_verification` ejecuta la suite
normal y gates de policy/registry/doctor/cleanliness. Antes y después de cada
command se comparan HEAD, index, tracked tree y un inventario `lstat` acotado
que incluye untracked **e ignored**; caches y `__pycache__` no desaparecen por
estar ignorados. `PYTHONPYCACHEPREFIX` apunta fuera del worktree y un test de
compileall lo demuestra. Output parcial, overflow, symlink, red/credencial o
residuo inesperado es UNKNOWN/FAIL.

El uso real de recursos entra mediante
`ValidatedResourceUseObservation` host-bound: secuencia ID+registry/content
digest+operation+ordinal y efectos, ligada a task/route/repo/worktree/HEAD/
session/invocation/tool-use/TTL/nonce. `verify_route()` consume ese wrapper y
produce `ResourceUseReceipt`; un receipt o JSON previo es solo diagnóstico.
Required ausente, forbidden presente, closure distinta o replay bloquea.

El corpus lógico tiene 100 TaskEnvelopes; no crea 100 agentes. Un
`skill-pressure-manifest.json` separado, sin oracle, programa exactamente 12
sesiones: diez casos únicos y dos repeticiones. Strong mode encuadra eventos
nativos de sesión y uso; fallback reevalúa el response-dir fijo de la task y
queda pending. Los evaluadores producen
`CompletedSkillPressureEvaluation` y
`CompletedIndependentReviewEvaluation`, nunca receipts directos.

Solo `TaskStore.publish_skill_pressure_evaluation()` y
`publish_independent_review_evaluation()` publican receipts mediante CAS,
JSON canónico `0600`, fsync/replace, task/HEAD/profile/generation/owner y
completed digest. No se afirma MAC sin key lifecycle. Repetición byte-idéntica
es idempotente; conflicto no sobrescribe. `run_verification_profile()` carga
por IDs/digests del state el set supplemental exacto —smoke macOS,
skill-pressure y **un** `IndependentReviewReceipt` agregado que contiene
exactamente los dos resultados compliance + quality_security— y agrega solo si
todos coinciden. Cualquier
corrección de reviewer invalida HEAD/diff, todos los receipts y el verifier:
se crea ronda nueva y se repiten normal, mutation, performance, agentes y
reviews.

PR A es una excepción legacy confinada: un attestor v1 inmutable crea, avanza y
cierra exclusivamente su state/lease schema-1; el candidate nunca lo migra ni
lo consume. Desde cada base posterior, el runtime gobernante de esa base posee
toda la ronda. PR C se evalúa en shadow; PR D es el primer piloto autoritativo.

## 7. Modos y multidominio

El aviso muestra la recomendación ya calculada por el router:

| Situación | Recomendación |
|---|---|
| pequeña, clara y reversible | modo normal |
| ambigua, T2/T3 o arquitectónica | `/plan` |
| larga, persistente o con varios hitos | `/goal` |
| compleja y persistente | `/plan` seguido de `/goal` |

Un único renderer cerrado produce para brief, JSON y CLI:
`mode`, commands exactos, message code, reason codes,
`automatic_change=false` y texto fijo accionable. Los mappings son
`normal→[]`, `plan→["/plan"]`, `goal→["/goal"]` y
`plan_then_goal→["/plan","/goal"]`. No cambia automáticamente el modo, el
razonamiento ni la autoridad, y tests comparan las tres superficies.

El siguiente hito debe ejecutarse con GPT-5.6 Sol `xhigh`, pero el runtime sigue
manteniendo recomendaciones proporcionales para el uso cotidiano.

El Risk Sentinel no presupone iOS. Consume el perfil detectado:

```text
ios
android
web_pwa
saas_backend
ai_text_pipeline
hybrid
generic
```

Un perfil desconocido produce warning; no carga silenciosamente gates iOS.

## 8. Autoridad

Este diseño y su plan no conceden autoridad. El ejecutor debe construir el
`TaskEnvelope` vigente y comprobar, en el canal host, autorización separada
antes del primer commit, push, creación o cambio de estado del Pull Request,
merge, adopción, deploy o release.

Un permiso previo solo permanece válido si sigue ligado al mismo objetivo,
task digest, scope, sesión y efecto. Nunca autoriza por implicación force push,
limpieza destructiva, secretos, autenticación, deploy, release, adopción en
otros repositorios ni bypass de gates. Antes de todo efecto remoto se repite
preflight con refresh y se registra la evidencia, pero el preflight tampoco
concede la autorización.

## 9. Seguridad y privacidad

- Resolver y contratos permanecen sin red, subprocess ni mutación.
- El resolver recibe `TaskEnvelope` validado, nunca prompt crudo ni brief
  educativo.
- Solo los adaptadores GitHub usan red: `CiPushProvenanceAdapter` dentro del
  evento Actions y `HostGitHubLifecycleProvider` local mediante `gh`/connector
  ya autenticado y autorizado. Resolver/contratos nunca usan red.
- El hook no persiste prompts ni comandos.
- Los verification runners usan profiles/command IDs cerrados, ejecutables
  absolutos doctorados y entorno mínimo con HOME/TMP/cache efímeros; eliminan
  tokens, secretos, cookies, proxies, askpass, SSH agent y config heredada.
  Comparan antes/después HEAD, index, tracked blobs e inventario untracked
  completo con symlinks. Aun así, mismo usuario no equivale a sandbox:
  sin read-roots/no-network host demostrables el receipt marca
  `pending_verification_host_isolation`, permite solo audit autorizado y
  bloquea enforcement semántico. Código external_untrusted no se ejecuta
  localmente sin aislamiento.
- Task state, receipts y métricas viven bajo el Git dir del worktree. La única
  excepción project-wide es el hint compacto bajo git-common-dir; no contiene
  task ledger, outputs externos ni secretos y nunca concede autoridad. Cada uso
  reconstruye la prueba externa y emite una capability opaca efímera.
- Las respuestas se representan mediante digests; solo la vista saneada de la
  pregunta se conserva temporalmente para restart/compactación.
- Contenido externo no concede permisos ni resuelve gates.
- Un fallo de observación devuelve `UNKNOWN`.
- Los tests usan repositorios y respuestas HTTP herméticas.
- No se añade dependencia.
- Acciones de GitHub continúan fijadas por SHA.
- CI no obtiene permisos de escritura.

## 10. Adopción

Promoción:

```text
audit
→ soft-enforce local
→ enforce local
```

Condiciones para `soft-enforce`:

- suite completa verde en Ubuntu;
- smoke macOS mecánico Darwin PASS para warning, hooks, safe-read, guards y
  rollback, más observación nativa si el host la ofrece;
- hooks revisados humanamente en `/hooks` y receipt ligado a los mismos hashes;
- rollback ensayado;
- corpus audit de al menos 100 TaskEnvelopes;
- detección del 100 % de categorías críticas;
- menos del 10 % de activaciones obligatorias falsas;
- cero Critical o Important abiertos en revisión independiente.
- `HostAdapterCapability=ready` mediante forward-test real antes de promover
  enforcement semántico; si falta, solo se promueven barreras mecánicas y el
  estado semántico permanece audit/advisory.
- clean-agent `logic_result=PASS` y aislamiento host demostrado; si solo existe
  `pending_clean_agent_host_assurance`, C puede integrarse audit-only pero no
  promover enforcement semántico.
- dos `IndependentReviewResult` cerrados —compliance y quality/security— ligados
  a HEAD/diff/plan/spec, sin Critical/Important abiertos y con host binding
  demostrado; `pending_review_host_assurance` permite C solo en audit.
- `pending_verification_host_isolation` ausente para promoción semántica.
- benchmark determinista de 10.000 recursos con p95 <1 s y pico incremental
  <64 MiB.

Los 100 casos son fixtures, no 100 agentes. El siguiente hito usa `xhigh`, un
writer y como máximo dos revisores o investigadores independientes.

El corpus lógico contiene 100 fixtures. Un
`tests/skill-pressure-manifest.json` distinto y sin oracle programa el
forward-test de 12 sesiones sin contexto heredado: diez casos y dos
repeticiones, máximo dos concurrentes. El agente devuelve
`AgentFramingResult` cerrado; el host normaliza TaskEnvelope y ejecuta el
resolver con inputs sintéticos no-oracle para producir
`CanonicalAgentRouteResult`. Con evento opaco, pares resultado+observación se
evalúan en memoria. Sin él, el orquestador —no el agente aislado— persiste
atómicamente un response-dir cerrado y
`assurance-publish --kind skill-pressure` recanonicaliza y publica vía
TaskStore; siempre queda `pending_clean_agent_host_assurance`.

Las dos revisiones independientes producen resultados estructurados con
kind/HEAD/diff/plan/spec y findings codificados, no chain-of-thought. Un
evaluator exige ambos kinds y cero Critical/Important abiertos. El fallback de
dos archivos atómicos se reevalúa mediante
`assurance-publish --kind independent-review` y marca
`pending_review_host_assurance`; narrativas de review por sí solas no
satisfacen el gate.

El consumo se gobierna con proxies, no con afirmaciones inventadas de tokens:
bytes UTF-8 realmente añadidos por manifest, recursos, brief y hook; unidades
de contexto; workers; reintentos y duración. Router <=4 KiB, brief <=1 KiB y
cada salida hook <=4 KiB. Los límites de recursos/workers proceden del tier. Un
required que no cabe se segmenta o bloquea; un recommended se difiere. Solo se
persisten cifras y digests. Router/brief/hook/unidades se miden dentro del
runtime; bytes de recursos, workers, reintentos y duración llegan en un
`HostContextMetrics` opaco task/session/invocation-bound. Si el host no los
observa, quedan null/partial, nunca cero autoatestiguado por JSON. Las
observaciones son invocation/tool_use-bound, incluyen start/end monotónicos y
worker ID, y se deduplican bajo flock. Los agregados cerrados son bytes
total/máximo, `invocation_count_unique`,
`hook_invocation_count_unique`, `context_units_selected_total`,
`context_units_selected_max`, `workers_unique`, `retry_count_total`,
`worker_time_ms_total=sum(end-start)` y
`task_elapsed_ms=max(end)-min(start)`. Mismo ID/mismo payload es no-op; mismo
ID/payload distinto falla. Permutación, replay y workers solapados producen el
mismo resultado sin pérdida por concurrencia.

La adopción en cada producto será una tarea posterior:

```text
adopt plan
→ revisión del diff y del perfil
→ apply autorizado
→ verify
→ periodo audit
→ promoción específica
```

La policy remota no se autocompleta. `adopt plan` puede mostrar candidatos
normalizados mediante `--repository-identity OWNER/REPO` y
`--required-check NAME:APP:CONCLUSION[,CONCLUSION]` repetible, pero conserva
`pending_user_confirmation`. Un `ProjectRemotePolicyDecision` host-bound,
one-shot y ligado al draft digest selecciona identidad/checks;
`adoption_apply()` rederiva el draft y exige además autorización de mutación
separada. JSON editado, plan ID copiado o `confirmed=true` no escribe esos
campos.
En este repositorio el candidate mostrado es
`AndreaBusta/codex-engineering-control-plane` +
`verify:github-actions:success`; sigue pendiente hasta la decisión host nativa
y debe revalidarse contra la base viva al aplicar mediante la operación
policy-only, no mediante una adopción source→target.

## 11. Evidencia y Definition of Done

La implementación v2.1 no estará terminada hasta demostrar:

1. Los 174 tests actuales continúan pasando.
2. Contratos nuevos cerrados, deterministas y ligados por digest.
3. Matriz low/medium/high/critical completa.
4. External untrusted no aclara, aprueba, autoriza ni confirma.
5. Una aclaración no puede utilizarse como `TrustedAuthorization`.
6. Lifecycle lateral atómico probado para transiciones, crash y carreras.
7. Cambio de task digest invalida evidencia descendiente.
8. `risk-status` agregado y `risk-provenance` remote-only cumplen exits 0/1/2.
9. Estado remoto no observado nunca aparece como PASS.
10. Aviso bajo 4 KiB y sin prompt.
11. Direct push estándar a base se detecta en hooks.
12. Push feature normal permanece permitido.
13. Guards Git aplican y hacen rollback sin destruir hooks previos.
14. `--no-verify` queda documentado como bypass residual.
15. CI prueba PR válido, asociación no observada, contradicción, forced,
    multi-commit y degradación.
16. Token GitHub no aparece en output ni errores.
17. Workflow mantiene acciones por SHA y permisos read-only.
18. Perfil genérico, iOS, Android, web/PWA, SaaS y texto IA siguen correctos.
19. Recomendación `/plan` y `/goal` permanece informativa.
20. `tests/normal_budget.py` falla si la suite normal alcanza 90 segundos.
21. `tests/assurance_budget.py` falla si assurance ampliada alcanza cinco
    minutos.
22. Rollback y fault injection aprobados.
23. Documentación refleja estado real, no funcionalidad futura.
24. Un `IndependentReviewReceipt` agregado, derivado de exactamente dos
    resultados de sesiones/kinds distintos y ligado a HEAD/diff/plan/spec, sin
    Critical o Important abiertos.
25. Branch, upstream, diff y worktree quedan identificados al cierre; tras
    `base_verified` existe además un `PostMergeCleanupReceipt` explícito o un
    `POST_MERGE_CLEANUP_PENDING` visible, nunca borrado implícito.
26. `.` posee todo descendiente en leases y grafos.
27. Worktrees distintos no pueden adquirir scopes solapados.
28. Un ciclo de revisión liga commit, push y checks al nuevo HEAD del mismo PR.
29. Muerte abrupta libera el mutex y adopt/upgrade recuperan su journal.
30. `task-framer` es la única skill de pre-framing y su output declarado es
    veraz.
31. El prompt crudo no llega al resolver ni a receipts.
32. El brief educativo no cambia tier, route digest, gates, efectos ni
    autoridad.
33. Multifrente usa `goals`, `depends_on` e `independent_work` existentes, sin
    crear writers durante framing.
34. Cada PR A/B/C/D parte del squash commit anterior demostrado en `origin/main`.
35. Un JSON no puede autoatestiguar inventario, lifecycle ni autorización.
36. Layout y runtime se validan antes del import; shadows opuestos se ignoran.
37. El host construye y envuelve `ClarificationRequest` antes del resolver; un
    mapping byte-idéntico no entra al router.
38. Confirmación irreversible liga request, consequence, autorización y
    operación, caduca y se consume una vez.
39. Solo un bridge host nativo recorre estados laterales; si no existe, queda
    advisory y CLI genérico no los recorre.
40. Cada PR actualiza sus digests y pasa lock tests independientemente.
41. Origen/scope de hooksPath se conserva sin shadowing.
42. Cada push de base conserva una ejecución de provenance no cancelable.
43. PreToolUse confina rutas y valida lease/worktree/session/policy.
44. Mutation runner distingue killed de timeout/import/harness.
45. Proxies de contexto cumplen budgets, concurrencia/dedupe y no afirman
    ahorro real de tokens.
46. Solo `require_clarification()` entra al lateral.
47. Cada autorización liga repo/worktree/HEAD/operación y se consume una vez.
48. Otro worktree recupera la transacción mediante manifest inmutable, WAL
    checksummed y marcador COMMITTED tras crash en cada mutation point.
49. PR A/B usan bootstrap explícito por ronda de revisión y no fingen lifecycle
    remoto.
50. Runtime adoptado contiene/importa todos los módulos normativos.
51. Bash effect classifier solo llama read-only a `safe-read --repo` ligado al
    worktree canónico y ejecutado con entorno saneado; el Git/rg crudo no se
    autoriza como una variante ficticia.
52. CI valida `jobs.risk-sentinel`, su display name `risk-provenance`, el step
    remote-only y el mapeo exit 0/PASS interno → GitHub `completed/success`.
53. Métricas host-only faltantes quedan partial/null; invocaciones concurrentes
    agregan counts únicos, total/máximo y tiempos definidos sin autoatestiguación
    ni pérdida.
54. `ClarificationIssueDraft` no puede declarar provenance.
55. Release owner-bound del lease de A permite adquirir el lease de B.
56. PR C usa outcome/effect `integration`; A/B conservan `pull_request` y crean
    una identidad integration separada antes del merge —A mediante binding
    host legacy, B mediante task/`RemoteEffectContext`. Ningún `pull_request`
    cruza `pr_ready`.
57. Command hooks JSON no pueden habilitar enforcement semántico ni autoridad.
58. El provider GitHub local observa y consume en TaskStore en el mismo proceso,
    o queda `pending_github_host_adapter`.
59. `pr_ready` exige PR exacto y todos los checks policy-required completos.
60. Restart/SessionStart(compact) reemite la vista de pregunta exacta o conserva
    bloqueo.
61. Asociación PR vacía tras retries es UNKNOWN, no acusación de push directo.
62. La identidad GitHub es canónica e independiente de alias `origin/upstream`.
63. PR C usa provider candidato solo en shadow; PR D es el primer lifecycle
    remoto autoritativo.
64. Governing policy/base provienen del objeto Git de la base verificada y la
    policy candidata nunca reduce sus propios gates.
65. Un state de aclaración publicado siempre referencia PromptView durable; GC
    elimina sidecars huérfanos sin cambiar el estado.
66. Check runs cumplen caps cerrados y total estable; cap+1 o Link extra es
    UNKNOWN.
67. Route sin HostAdapterCapability devuelve pending y no inventa request.
68. Policy/provider/gate modificado activa policy_change_pending y requiere una
    task futura desde la base fusionada.
69. El piloto D no modifica runtime, policy, lock, hooks, CI ni tests.
70. La promoción del provider se invalida ante upgrade, lock drift o cambio de
    governing policy y exige otro forward-test.
71. Repository evidence de aclaración entra solo como wrapper host-bound
    fresco; JSON resolved, stale, replay o cross-context falla.
72. GC y publisher de PromptView comparten flock/generation y el GC nunca borra
    la vista vigente.
73. `authority_mode=pilot` permite probar D sin declarar ready ni habilitar otra
    task antes de base_verified.
74. El hint de capability vive bajo git-common-dir, sobrevive al prune del
    worktree D y nunca concede autoridad sin reobservación live completa.
75. PR D contiene un commit real del charter antes de push; el charter no afirma
    el resultado futuro.
76. Provider y reconstrucción de la capability se ejecutan desde attestor limpio
    en governing base;
    candidate provider+lock coordinados bloquean antes de red.
77. PR/base ref SHA queda ligado al governing commit; para squash el parent
    único del merge commit debe ser exactamente esa base. Cualquier base advance
    invalida checks y exige task/piloto nuevo; v2.1 no rota base in-place.
78. `safe-read --repo` liga root/git-dir/common-dir, desactiva lazy fetch,
    terminal prompt, askpass, SSH y proxies; un partial clone falla localmente
    sin egress.
79. Todo preflight dirty recibe task ID y session ID exactos del lease; ausencia
    o mismatch falla.
80. Provenance para capability entra solo como observación host-bound del run
    exacto; ningún output/JSON de CI se promueve.
81. Pilot solo nace mediante `start_authority_pilot()` y autorización opaca
    one-shot; CLI/state injection falla.
82. Pre-merge base SHA coincide con governing base; post-merge avanza
    exactamente al merge commit sin confundirse con drift.
83. Un avance adicional de base antes de base_verified deja el piloto pending y
    no publica hint ni capability; finalize conserva `resume_state=merged`,
    no declara integration completada y libera el writer exacto.
84. Cada child bootstrap cerrado libera su lease con identidad y digest exactos;
    el release es idempotente y el siguiente worktree puede adquirir el scope raíz
    sin borrar estado ajeno.
85. Workflow runs, attempts y jobs se enumeran con caps y completitud cerrada;
    página omitida, cap+1, duplicado o total cambiante queda UNKNOWN.
86. Un hint fabricado con todos los IDs y digests públicos no produce
    `ValidatedProviderCapability`; cada uso reconstruye PR, diff, merge/base y
    provenance PASS mediante wrappers host-bound frescos.
87. Tasks 9 y 10 se sellan en un único commit después de regenerar el lock sobre
    policy, runtime, distribución y documentación definitivos de PR C.
88. GitHubClient recibe un `GitHubTransport`: token solo dentro del transporte
    Actions y `gh api` local con env saneado, sin leer ni exponer token; ambos
    comparten evaluadores y límites.
89. D construye `ValidatedPilotInputs` resolviendo TaskEnvelope, inventory,
    policy y registry gobernantes; `start_authority_pilot` consume además
    runtime y manual-merge wrappers, ancla toda mutación al worktree canónico y
    finaliza success/pending/abort liberando el lease exacto.
90. Un `RemoteEffectContext` limpio, exact-effect y one-shot nunca revive el
    writer ni autoriza local_write/commit; cambiar HEAD/session/task/effect lo
    invalida y push/PR usan wrappers separados.
91. Cada commit separado de Tasks 3–8 actualiza `RUNTIME_MODULES` cuando
    corresponde, regenera/stagea el lock y pasa lock/adoption/doctor; ningún
    commit intermedio depende de Task 10 para ser ejecutable.
92. Close, suspend, abandoned recovery y pilot finalize usan markers de dos
    fases, orden common→task y `_release_locked`; recovery acepta exactamente
    marker+lease sin tombstone, marker+lease+tombstone o marker+tombstone sin
    lease, siempre con owner/digest/generation coincidentes.
93. Una aclaración dirty solo reanuda el mismo writer con task/session/lease/
    scope/paths exactos; cambio de owner o reframe queda resume_forbidden y
    exige recovery+task nueva.
94. Worktree inventory y lease recovery son host-bound, completos, TTL/nonce/
    one-shot; mapping, replay, cap+1 y cross-common-dir no liberan un lease.
95. `ValidatedManualMergeObservation` se consume en start pilot; parent squash,
    PR/head/checks/provider/runtime y ref base exactos se reobservan, no se
    degradan a un SHA autoritativo.
96. D recorre implementing→verifying→review_ready→committed→pushed mediante
    observaciones host-bound; el commit rota `PilotTaskContext.current_head` y
    cada fase rota generation sin alterar los demás bindings.
97. `CompletedSafeRead` y `GitHubResponse` tienen contratos cerrados, caps,
    timeout/truncation y pruebas de paridad source/isolated y urllib/gh.
98. Doce sesiones sin conversación heredada —diez casos y dos repeticiones— y
    lotes de máximo dos forward-testan task-framer/verified-workflow. FAIL
    lógico bloquea C; si el host no demuestra sandbox/eventos opacos, el receipt
    marca `pending_clean_agent_host_assurance`, permite C solo en audit y
    bloquea semantic enforcement hasta repetir con adapter real.
99. El corpus separa labels del input, detecta 100% de categorías críticas y
    cubre explícitamente auth, pagos, datos privados, secretos, migraciones,
    destrucción, producción y release; mide menos de 10% de mandatory falsos
    sobre un denominador negativo no vacío.
100. La suite gobernante de C corre con un child `operate/local_change`, lease
     raíz y terminal `review_ready` antes de D; el worktree D conserva lease
     documental y solo ejecuta gates no escritores hasta CI.
101. `safe-read --repo` queda registrado en CLI, empaquetado en runtime aislado
     y probado, incluida la gramática `rg --quiet` del piloto; PreToolUse liga
     cada write al task ID exacto además de lease, worktree, session, policy y
     paths.
102. Runtime y transporte GitHub se doctoran como wrappers host-bound ligados a
     base/target/session/invocation/TTL; candidate, mapping, replay o drift queda
     pending antes de import/red.
103. El runner mecánico Darwin ejecuta procesos hook/launcher source e
     isolated y publica `MacOSHookSmokeReceipt` ligado al HEAD; PASS permite C
     solo en audit. Observación nativa y revisión humana `/hooks` son gates de
     promoción: si faltan, hooks siguen `pending_hook_trust`.
104. Task 12 usa un child `operate/local_change` con lease raíz y terminal
     `review_ready` para caches/receipts; se cierra antes de push y nunca se
     reutiliza como writer de producto ni tras un cambio de HEAD.
105. Los verifiers raíz usan `VerificationExecutionContext` con profiles/argv
     cerrados y observación before/after de HEAD/index/tracked/untracked; el
     lease raíz nunca autoriza Edit/Write/apply_patch/stage/commit por sí solo,
     y un gate fallido usa `abort_verification()` recuperable.
106. El resolver procesa un registry determinista de 10.000 recursos con p95
     menor de un segundo y pico incremental menor de 64 MiB, medidos por el
     benchmark stdlib reproducible de assurance.
107. El forward-test transforma `AgentFramingResult` en
     `CanonicalAgentRouteResult` en el host; el fallback response-dir
     recanonicaliza el set exacto y siempre marca
     `pending_clean_agent_host_assurance`.
108. Dos `IndependentReviewResult` de kinds distintos y bindings
     HEAD/diff/plan/spec exactos producen un receipt sin Critical/Important
     abiertos; fallback queda `pending_review_host_assurance` y solo permite
     audit.
109. UserPromptSubmit previo al framing nunca inventa tier/route/interacción;
     SessionStart(compact) se normaliza a post_compact y reemite la
     `CurrentWarningView` atómica aunque el fingerprint sea idéntico.
110. `LocalGitObservation`, `GitHubObservation` y `RemoteEffectContext` ligan
     invocation; el contexto remoto revalida PR/base/checks exactos al consumo.
111. `TaskLease._acquire_locked()` usa token+inventario prevalidado sin relock
     ni subprocess; una carrera de registros worktree falla stale antes de
     escribir.
112. D declara intent/phase integrate y outcome integration; el router
     selecciona `git.remote-proof`, y provider resource ID+digest coinciden en
     RouteDecision, transport, observaciones y receipt.
113. Los verification runners sanean env y registran
     `pending_verification_host_isolation` sin sandbox host; ese estado bloquea
     semantic enforcement y código external_untrusted no se ejecuta localmente.
114. Tras `base_verified`, el sistema sincroniza la copia local de base solo por
     fast-forward y autorización separada; en cualquier otro caso muestra
     `LOCAL_BASE_NOT_SYNCED` sin tocarla ni rebajar la prueba remota.
115. Los `git diff` staged/post-commit autoritativos del piloto pasan por
     `safe-read --repo` y producen `CompletedSafeRead`; el comando raw no cuenta
     como evidencia.
116. `build_verification_task_envelope()` emite todos y solo los campos schema
     1 para ambos profiles; los snippets operativos no son overlays parciales y
     profile/HEAD/session quedan ligados fuera del mapping.
117. El verifier no acepta kind/profile/runtime genéricos: C y base usan
     authority wrappers host-bound de tipos distintos y target observations
     separadas.
118. Skill-pressure y reviews solo publican receipts mediante TaskStore,
     CAS/digest canónico/fsync; no se afirma MAC sin key lifecycle, replay
     idempotente no sobrescribe conflicto.
119. El corpus lógico de 100 y el schedule clean-agent de 12 son artefactos
     distintos; el segundo contiene 10 IDs únicos+2 repeticiones y cero oracle.
120. El profile final de C vuelve a ejecutar normal, mutation, 10k performance,
     policy, registry, doctor, seguridad e integración sobre el HEAD posterior
     a todas las correcciones, dentro del budget agregado.
121. Crear/retirar worktrees, borrar ramas efímeras/features locales/remotas,
     stage y commit del piloto pasan por operation IDs cerrados con grants
     distintos; squash local usa CAS `delete_validated_local_feature`, fetch
     exige autoridad dual y `RemoteRefMutationGuard`, y no quedan snippets Bash
     que los salten.
122. `GhCliTransport` prueba subprocess real con `--include --method GET`;
     `GitHubEndpointBinding` limita v2.1 a github.com y deja GHES pending antes
     de leer token o abrir red.
123. `host.github-gh-read` no está ready por declaración builtin: doctor,
     auth, salud y autorización de task deben estar probados antes de
     `supersedes`; el fallback no desaparece si falta una dimensión.
124. `repository_identity` y `required_checks` se adoptan solo mediante
     `ProjectRemotePolicyDecision` nativo ligado al draft/target y autorización
     de apply separada; JSON editado/autoconfirmado no muta.
125. La policy local gobernante procede de evento/contexto base host-bound o del
     manifest instalado validado; sin anchor, Task 6 devuelve UNKNOWN y la
     candidata no puede elegirse su propia base.
126. Asociaciones PR mixtas se enumeran completas y son invariantes a
     orden/página; un candidato incompatible temprano no oculta el merged
     compatible posterior ni causa FAIL falso.
127. `pending_host_capability` y `clarification_request_required` forman parte
     del enum cerrado, bloquean efectos y tienen reason codes distintos.
128. `core.hooksPath` apunta al snapshot absoluto del common-dir; una rama
     histórica/candidate no gobierna el guard activo sin upgrade autorizado.
129. Restart/compaction del piloto usa `resume_authority_pilot()` con
     reobservación y autorización nuevas; generic resume, state JSON o contexto
     anterior no duplican efectos.
130. Safe-read desactiva replace objects/lazy fetch y el scanner de secretos usa
     el pattern-set gobernante digestado; `rg` ad hoc nunca sustituye ambos
     gates.

## 12. Estrategia de implementación

El plan detallado está en:

```text
docs/superpowers/plans/2026-07-29-clarification-gate-risk-sentinel-v2-1.md
```

Orden:

```text
estabilización de ownership, revisión y locks
→ pre-framing veraz
→ contratos de aclaración
→ router
→ lifecycle
→ risk-status
→ hooks
→ guards y adopción
→ alarma CI
→ documentación y lock
→ assurance
→ revisión independiente
→ PR C manual/shadow y squash verificado
→ PR D pilot-scoped desde attestor gobernante
→ hint no autoritativo + capability live, o pending honesto
→ handoff read-only
```

No se paralelizan writers. La revisión de una unidad estable puede ejecutarse
en paralelo con inspección de la unidad siguiente, con máximo dos workers.

## 13. Handoff

El siguiente agente debe:

1. confirmar GPT-5.6 Sol `xhigh`;
2. trabajar exclusivamente en el worktree/branch indicados por el handoff;
3. leer `AGENTS.md`, esta especificación y el plan completo;
4. ejecutar baseline antes de editar;
5. seguir TDD sin saltarse RED;
6. detenerse si la base remota cambió o el plan contradice el código real;
7. no adoptar el producto en otros repositorios;
8. no publicar ni hacer release;
9. actualizar los checkboxes del plan solo con evidencia fresca;
10. dejar PR y pruebas verificables al cierre.
