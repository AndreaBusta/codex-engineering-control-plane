# Clarification Gate and Risk Sentinel v2.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Estabilizar el control plane, hacer veraz su pre-framing y añadir
aclaración material, estado de riesgo triestado, guards Git reversibles y
detección post-push sin confundir ninguna capa con autorización o branch
protection.

**Architecture:** el host usa la skill canónica `task-framer`, normaliza su
Markdown a un `TaskEnvelope` validado y solo entonces entra al resolver puro.
El inventario operativo y las evidencias de lifecycle se observan dentro del
host y viajan en wrappers no serializables; ningún JSON puede autoatestiguar
salud, autenticación, push, PR, merge o release. El layout del runtime queda
seleccionado de forma cerrada por el lock, nunca por la mera existencia de un
directorio.
`ClarificationRequest` es serializable, mientras `ClarificationResolution` e
`IrreversibleConfirmation` solo adquieren confianza mediante un wrapper interno
del host y permanecen separados de `TaskEnvelope` y
`TrustedAuthorization`.
`risk-status` agrega hechos locales y evidencia remota en `PASS`, `UNKNOWN` o
`FAIL`; hooks y guards constituyen defensa local, mientras GitHub Actions solo
detecta procedencia después del push.

**Tech Stack:** GPT-5.6 Sol `xhigh`; Python 3.11 estándar (`argparse`, `dataclasses`, `json`, `shlex`, `subprocess`, `tomllib`, `urllib`, `unittest`), Git, TOML, JSON, POSIX shell, Markdown y GitHub Actions. Sin dependencias nuevas.

---

## Contrato de ejecución xhigh

El siguiente hito se ejecutará así:

```text
modelo: gpt-5.6-sol
razonamiento: xhigh
coordinación: secuencial
writer simultáneo: 1
workers de lectura/revisión: máximo 2
modo recomendado por router: plan_then_goal
```

Antes de escribir, el agente debe confirmar que el hilo actual está realmente
en `xhigh`. Si la interfaz no permite demostrarlo, debe declararlo sin fingir un
cambio de configuración.

Este worktree y rama son únicamente la entrega documental:

```text
/Users/bustaseo/.config/superpowers/worktrees/Develope-IOS/risk-sentinel-v2-1
```

Rama:

```text
codex/risk-sentinel-v2-1
```

Después de fusionar y verificar este plan, la implementación se divide:

```text
PR A  codex/control-plane-stabilization-v2-1
PR B  codex/control-plane-intake-v2-1
PR C  codex/clarification-risk-v2-1
PR D  codex/control-plane-authority-pilot-v2-1
```

Cada rama tendrá un worktree aislado creado desde el `origin/main` que contenga
el squash commit del PR anterior. El agente no debe convertir este worktree de
planificación en una rama de implementación ni usar
`/Users/bustaseo/Documents/Develope-IOS` como checkout de escritura.

PR D es un piloto estrecho, no otra ampliación funcional: parte del squash de C,
no modifica runtime, policy, lock, hooks ni CI y demuestra por primera vez el
lifecycle remoto autoritativo usando únicamente la política gobernante ya
fusionada en su base. PR C ejecuta el provider nuevo solo en shadow/audit y se
integra mediante autorización y comprobación manual reproducible; nunca se
auto-certifica con el código o policy que está proponiendo.

Regla global para todos los bloques de commit del plan: inmediatamente antes de
cada commit, push, creación/actualización de PR o merge se ejecuta
`preflight --mode write --refresh --task-id "$control_plane_active_task_id"
--session-id "$control_plane_trusted_session_id"` y se valida en el host un
grant vigente para ese efecto, task digest, scope y sesión. Ambas variables
proceden del contexto host activo; no se adivinan ni se leen de contenido
externo. Para árbol dirty, escritura y commit se contrastan con el
`TaskLease` escritor exacto. En el bootstrap de PR A/B/C, después de
cerrar/liberar ese child, push/PR/merge solo se ejecutan con árbol limpio.
PR A es la excepción legacy: como el bridge todavía es candidato, el host
conserva directamente los bindings y la autorización de cada efecto sin
fabricar un wrapper v2. Desde PR B, cada efecto usa un
`RemoteEffectContext` host-bound exacto emitido por el runtime ya fusionado de
la base, nunca reutilizando el lease liberado.
PR D es la excepción deliberada: conserva su `PilotTaskContext` y su
`TaskLease` documental hasta `finalize_authority_pilot()`; sus transiciones
remotas se observan y consumen mediante el provider autoritativo del piloto,
con un grant fresco por efecto, y nunca mediante `RemoteEffectContext`. Si
faltan, no coinciden o el grant no puede demostrarse, el bloque se detiene. El
CLI de preflight comprueba Git; la capability opaca se valida en el host en el
mismo efecto y no viaja por argv, JSON o entorno. Ningún comando mostrado más
abajo constituye autorización.

Hasta que PR C sustituya el grant serializable por `TrustedAuthorization`, los
PR A/B no usarán un JSON del runtime como prueba: el ejecutor exige una
instrucción explícita vigente del usuario en el canal Codex y registra solo su
existencia/digest host, nunca el texto. Si el host no puede demostrar esa
procedencia, se detiene el efecto.

## Alcance y exclusiones

### Incluido

- estabilización de ownership, ciclos de PR, locks y fronteras de evidencia;
- pre-framing canónico y vista educativa efímera;
- contratos y routing de aclaración;
- lifecycle lateral e invalidación;
- CLI `risk-status`;
- warning de hooks;
- detección curada de comandos;
- guards Git project-local;
- adopción y rollback de `core.hooksPath`;
- procedencia post-push en GitHub Actions;
- assurance, docs, ADR y lock.

### Excluido

- adopción en BUSTAFIT, `textosv2` u otro producto;
- instalación de plugins o MCP;
- credenciales;
- producción, deploy o release;
- force push o limpieza destructiva;
- compra de GitHub Pro;
- enforcement remoto imposible con el plan actual.

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `control_plane/clarification.py` | crear | contratos y gate puro de aclaración |
| `control_plane/host_bridge.py` | crear | wrappers host-bound y adaptadores tipados |
| `control_plane/intake.py` | crear | renderer educativo puro y acotado |
| `control_plane/risk_sentinel.py` | crear | agregación triestado y contrato `risk-status` |
| `control_plane/github_provenance.py` | crear | evento GitHub, REST y prueba post-push |
| `control_plane/git_guards.py` | crear | guardas pre-commit y pre-push |
| `control_plane/assurance.py` | crear | evaluadores y completed results de assurance |
| `control_plane/scopes.py` | crear | normalización y solapamiento único de ownership |
| `control_plane/contracts.py` | modificar | vocabularios compartidos y validadores públicos |
| `control_plane/resource_registry.py` | modificar | observación host-bound del inventario |
| `control_plane/routing.py` | modificar | integrar gate bajo `interaction` |
| `control_plane/lifecycle.py` | modificar | estados laterales y reencuadre |
| `control_plane/graph.py` | modificar | semántica común de scopes de writers |
| `control_plane/hooks.py` | modificar | fingerprint, warning y clasificación de acciones |
| `control_plane/cli.py` | modificar | requests/estado, `risk-status` y `git-guard` |
| `control_plane/adoption.py` | modificar | archivos, config, journal y rollback |
| `control_plane/lockfile.py` | modificar | validar versión/digests nuevos |
| `.codex/git-hooks/pre-commit` | crear | launcher POSIX del guard |
| `.codex/git-hooks/pre-push` | crear | launcher POSIX del guard |
| `.codex/control-plane.lock` | modificar | versión 2.1 y hashes |
| `.codex/hooks.json` | modificar si cambia hash/descripcion | declarar hooks audit reales |
| `.github/workflows/risk-sentinel.yml` | crear | alarma post-push no cancelable |
| `.codex/templates/risk-sentinel.yml.tmpl` | crear | workflow target-specific para adopción |
| `tests/test_clarification.py` | crear | contratos y matriz de aclaración |
| `tests/test_intake.py` | crear | brief efímero e invariancia |
| `tests/test_risk_sentinel.py` | crear | triestado y GitHub degradado |
| `tests/test_git_guards.py` | crear | commit/push local |
| `tests/test_risk_integration.py` | crear | launchers y smoke end-to-end hermético |
| `tests/macos_hook_smoke.py` | crear | smoke Darwin host-bound de hooks, guards y rollback |
| `tests/mutation_runner.py` | crear | source mutants stdlib en copias temporales |
| `tests/skill_pressure_evaluator.py` | crear | evaluación reproducible de agentes limpios sin oracle |
| `tests/independent_review_evaluator.py` | crear | dos reviews cerradas y ligadas al diff |
| `tests/skill-pressure-manifest.json` | crear | schedule 12/10+2 sin golden labels |
| `tests/assurance_budget.py` | crear | assurance ampliada con límite de cinco minutos |
| `tests/normal_budget.py` | crear | suite normal portable con límite de 90 segundos |
| `tests/router_performance_budget.py` | crear | benchmark determinista de 10.000 recursos |
| `tests/test_contracts_v2.py` | modificar | separación de pruebas |
| `tests/test_routing.py` | modificar | gate, autoridad y receipt |
| `tests/test_lifecycle.py` | modificar | estados, resume e invalidación |
| `tests/test_lockfile.py` | modificar | layout de runtime y shadowing |
| `tests/test_cli_v2.py` | modificar | CLI humana/JSON |
| `tests/test_hooks.py` | modificar | sesión, fingerprint y comandos |
| `tests/test_adoption.py` | modificar | config reversible y fault injection |
| `tests/test_graph.py` | modificar | raíz universal y writers |
| `tests/skill-pressure-scenarios.md` | modificar | forward-tests del pre-framing |
| `tests/test_repository_contract.py` | modificar | artefactos y CI |
| `tests/contract_support.py` | modificar | permisos CI read-only permitidos |
| `tests/test_assurance.py` | modificar | corpus, propiedades y mutantes |
| `README.md` | modificar | estado v2.1 real |
| `SECURITY.md` | modificar | límites y amenazas |
| `AGENTS.md` | modificar solo si sigue conciso | gate de riesgo antes de escribir |
| `docs/adr/0003-host-bound-clarification.md` | crear | trust y autoridad |
| `docs/adr/0004-risk-sentinel-and-local-guards.md` | crear | triestado y defensas |
| `docs/engineering/07-adoption.md` | modificar | adopción y rollback |
| `docs/engineering/09-audit-dafo-and-risk-register.md` | modificar | riesgo residual |
| `docs/engineering/11-lifecycle-hooks-adoption.md` | modificar | lifecycle y hooks |
| `docs/engineering/12-multidominio-y-modos.md` | modificar | modos y perfiles |
| `docs/engineering/13-clarification-and-risk.md` | crear | runbook operativo |

## Task 0: Revalidar baseline y fijar el ledger

**Files:**
- Read: `AGENTS.md`
- Read: `.codex/project-policy.toml`
- Read: `.codex/resource-registry.toml`
- Read: `docs/superpowers/specs/2026-07-29-clarification-gate-risk-sentinel-design.md`
- Read: `docs/superpowers/plans/2026-07-29-clarification-gate-risk-sentinel-v2-1.md`

- [ ] **Step 1: Verificar la precondición documental**

Task 0 comienza únicamente después de que el PR documental esté fusionado con
autorización y su squash commit esté demostrado en `origin/main`. Crear después
un worktree aislado con rama `codex/control-plane-stabilization-v2-1` desde esa
base. Comprobar que ambos archivos están tracked y que no hay otros cambios:

```bash
git ls-files --error-unmatch \
  docs/superpowers/specs/2026-07-29-clarification-gate-risk-sentinel-design.md \
  docs/superpowers/plans/2026-07-29-clarification-gate-risk-sentinel-v2-1.md
git status --short
```

Además, verificar mediante GitHub que el PR documental está `MERGED`, su base es
la configurada y su `mergeCommit` está contenido en `origin/main`. Expected:
las dos rutas se imprimen y `git status --short` queda vacío. Si no, detenerse;
un lease futuro no convierte cambios previos desconocidos en estado seguro.

- [ ] **Step 2: Confirmar entorno exacto**

Run:

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git worktree list --porcelain
```

Expected:

```text
cwd y raíz = nuevo worktree aislado registrado
branch = codex/control-plane-stabilization-v2-1
árbol limpio
HEAD = origin/main verificado
```

- [ ] **Step 3: Ejecutar preflight remoto**

Run:

```bash
scripts/control-plane preflight --mode write --refresh
```

Expected: `PASS preflight`, rama feature, `behind=0`. Este preflight ocurre con
árbol limpio antes de crear el task/lease; no se reutiliza como gate del primer
commit dirty.

- [ ] **Step 4: Ejecutar baseline completo**

Run:

```bash
bash tests/run.sh
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
```

Expected: `174` o más tests, cero fallos, policy/registry/doctor PASS.

- [ ] **Step 5: Revalidar correcciones de seguridad heredadas**

Run:

```bash
python3 -m unittest \
  tests.test_lifecycle.LifecycleTests.test_task_lease_acquisition_is_atomic_for_overlapping_writers \
  tests.test_lifecycle.LifecycleTests.test_task_lease_validation_requires_changed_path_inventory \
  tests.test_resource_registry.ResourceRegistryTests.test_user_skill_locator_rejects_traversal_and_path_components \
  tests.test_repository_contract.RepositoryContractTests.test_repository_discovery_ignores_git_environment_redirection \
  tests.test_routing.RoutingTests.test_required_gate_must_be_operationally_ready \
  -v
```

Expected: cinco tests PASS. Si alguno falla, detener este plan y diagnosticar el
baseline; no apilar el nuevo producto sobre una garantía rota.

- [ ] **Step 6: Construir el ledger de ejecución**

Invocar primero el `task-framer` canónico y conservar únicamente sus notas
compactas. El host las normaliza y valida como `TaskEnvelope`; no se pasa el
prompt crudo al resolver.

Crear el envelope del primer PR con resultado solicitado `pull_request`,
`task_id = TASK-CONTROL-PLANE-STABILIZATION-V2-1`, tier T2/T3 según el
clasificador y una sola unidad coherente. PR B usa también
`requested_outcome=pull_request`. PR C usa
`requested_outcome=integration`, porque su Definition of Done incluye
merge y verificación manual de base, aunque su lifecycle permanezca
`authoritative_lifecycle=false`; `pull_request` termina contractualmente en
`pr_ready`. PR D usa `requested_outcome=integration` y es el primero que intenta
recorrer `merged → base_verified` con provider autoritativo. Repetir Task 0 con
task IDs, digests y ramas nuevos para cada PR; no reutilizar ningún ledger.

Efectos solicitados:

```text
local_read      source=user_explicit
local_write     source=user_explicit
commit          source=user_explicit
network_read    source=user_explicit
remote_write    source=user_explicit
pull_request    source=user_explicit
integration     source=user_explicit  # PR C manual y PR D pilot-scoped
```

`integration` no aparece en A/B. No usar `merge` como nombre de efecto: la
operación humana es merge y el vocabulario cerrado es `integration`. Eso
describe la petición, no concede los efectos.

Antes de crear directorio o archivo alguno, el host debe entregar una
`session_id` verificable. Si no la expone, detenerse con
`pending_host_session`; no crear ledger parcial. En el mismo proceso host:

1. construir el mapping del envelope únicamente en memoria;
2. ejecutar `validate_task_envelope(payload)` y exigir cero issues;
3. calcular sus bytes JSON canónicos y `contract_digest()` sin escribir;
4. solo entonces crear `STATE_DIR` y publicar esos bytes de forma atómica,
   `0600`, con write/fsync/replace/fsync-dir.

No se valida un archivo como precondición de su propia escritura, no se escriben
digests a mano y `apply_patch` queda reservado a archivos versionados, no al
estado efímero bajo Git dir. Después de publicar el envelope ya validado:

```bash
STATE_DIR="$(git rev-parse --git-path codex-control-plane/executions/TASK-CONTROL-PLANE-STABILIZATION-V2-1)"
test -f "$STATE_DIR/task-envelope.json"
scripts/control-plane inventory --repo . --json > "$STATE_DIR/inventory-report.json"
scripts/control-plane route \
  --repo . \
  --task "$STATE_DIR/task-envelope.json" \
  --mode audit \
  --json > "$STATE_DIR/route-decision.json"
```

Obtener `decision_digest` de la salida del router y el task digest con
`contract_digest()`. El `session_id` usado para el lease es el mismo que el host
validó antes de toda escritura, nunca prompt/env arbitrario.
`inventory-report.json` es solo diagnóstico: `route` debe observar el inventario
de nuevo dentro del mismo proceso y no aceptar ese archivo como entrada
confiable.

Excepción bootstrap obligatoria:

```text
PR A
  → initiative envelope outcome=pull_request queda solo como framing
  → crear/cerrar con el attestor v1 el child legacy schema-1 y su lease
  → observar árbol/HEAD por el canal host, sin LocalGitObservation ni contexto v2
  → push/PR usan bindings host directos, grants separados y verificación manual
  → integration usa una instrucción/task host separada, nunca amplía el outcome
  → guardar bootstrap-execution.json con authoritative_lifecycle=false

PR B
  → initiative envelope outcome=pull_request queda solo como framing
  → crear child envelope TASK-...-LOCAL-R0 outcome=commit con runtime A gobernante
  → stage/commit por los efectos cerrados gobernantes; cerrar y liberar lease
  → crear un RemoteEffectContext host-bound `remote_write` para push limpio
  → crear otro RemoteEffectContext `pull_request` después del push
  → antes del merge, crear task/contexto separado outcome=integration ligado
    al PR/head/base/checks exactos; no ampliar el outcome pull_request
  → si review, CI o base avanzada exige cambios, crear LOCAL-R1, R2...
    con task/decision digests y lease nuevos; nunca reabrir un child cerrado
  → guardar bootstrap-execution.json con authoritative_lifecycle=false
  → verificar Git/gh de forma read-only al cierre, sin promover estados

PR C
  → conservar authoritative_lifecycle=false también para la initiative
  → initiative outcome=integration cubre push/PR/merge manual, sin concederlos
  → usar LOCAL-R<n> para cada ronda de escritura como en A/B
  → ejecutar GitHubLifecycleProvider candidato solo en shadow/audit
  → merge e identificación del squash son manuales, autorizados y reproducibles
  → el runtime/policy candidato nunca gobierna su propio PR

PR D
  → crear desde el squash C ya demostrado en origin/main
  → iniciar TaskStore outcome=integration
  → cargar GoverningPolicy exclusivamente desde el objeto Git de esa base
  → LocalGitObservation permite avanzar hasta pushed
  → GitHubLifecycleProvider local, si está doctorado y autorizado, permite
    pr_draft → pr_ready → merged → base_verified
  → el diff piloto no puede modificar runtime/policy/lock/hooks/CI
  → sin provider local, permanece pending_github_host_adapter y no finge cierre
```

`RemoteEffectContext` es un wrapper host-only, no serializable y sin autoridad
de escritura local. Liga initiative/integration task digest, repo, worktree,
branch, committed HEAD, session, outcome y el efecto remoto exacto. Desde B,
push consume un contexto `remote_write` y crear/actualizar PR consume otro
`pull_request`; nunca se reutiliza el wrapper entre efectos. Ninguno cubre
`integration`; el merge exige otro TaskEnvelope
`TASK-...-INTEGRATE-R<n>` outcome `integration`, PR/base/head/checks exactos y
otro contexto/grant. C deriva su contexto de la initiative `integration`.
Cada cambio de HEAD invalida el contexto y obliga a una ronda LOCAL nueva.
Un mapping/JSON no lo reconstruye, nunca autoriza `local_write`/`commit` y el
preflight Git limpio no lo sustituye.

Antes de que exista el bridge de PR A, el host Codex debe conservar estos
bindings directamente desde la instrucción explícita vigente y el estado Git;
si no puede demostrarlos, se detiene antes del efecto remoto. El runtime
candidato no puede autoemitir el wrapper que lo autoriza.

Cada ronda bootstrap queda gobernada de principio a fin por un runtime
inmutable del `governing_base_commit`. Antes de `task start`, el host crea un
attestor detached/limpio de esa base, valida launcher+lock y usa **ese
ejecutable absoluto** contra `--repo <candidate-worktree>` para start,
preflight, transiciones, close y release. El candidate puede modificar
lifecycle/state/CAS, pero jamás abre, migra ni cierra el state creado por la
base. El attestor se retira solo después de demostrar task final y lease
ausente. PR B/C repiten el patrón desde su base fusionada; no heredan
automáticamente el runtime de A.

PR A parte de v1 y por tanto usa una **ronda legacy en cuarentena** con el
schema y las APIs exactas de v1. No intenta obtener `LocalGitObservation`,
`generation`, marker de dos fases ni receipt v2 que ese runtime no implementa.
El host observa el HEAD y el índice por su canal read-only, pero el attestor v1
solo persiste sus evidencias schema-1 admitidas y recorre secuencialmente
`framed → planned → ready → implementing → verifying → review_ready →
committed → closed`; `TaskStore.close()` del propio v1 elimina el lease del
child. Ese state prueba únicamente contención bootstrap, no autoridad v2 ni
procedencia remota. Push, PR e integración siguen siendo contextos host
separados.

No se implementa una migración v1→candidate implícita. Si el runtime de base no
puede cerrar su propio formato, la ronda se aborta antes de edición o se usa su
ruta de recuperación documentada; el candidate devuelve
`E_FOREIGN_RUNTIME_STATE` al intentar consumirlo. Un fixture crea state+lease
v1 en el Git dir del target, modifica simultáneamente schemas/CAS en candidate,
verifica que candidate lo rechaza y que **solo** el ejecutable attestor v1
puede completar las transiciones v1, cerrar y liberar de forma segura. Ninguna
prueba llama al close v2 sobre ese state.

Invocación bootstrap A con los valores calculados, no placeholders literales,
y siempre mediante el launcher absoluto del attestor v1:

```bash
"$control_plane_governing_attestor/scripts/control-plane" task start \
  --repo "$control_plane_candidate_worktree" \
  --task-id "$LOCAL_CHILD_TASK_ID" \
  --outcome commit \
  --branch "$control_plane_candidate_branch" \
  --task-digest "$LOCAL_CHILD_TASK_DIGEST" \
  --decision-digest "$LOCAL_CHILD_DECISION_DIGEST" \
  --session-id "$TRUSTED_HOST_SESSION_ID" \
  --scope-path .
```

Después de un `task start` válido, ligar las variables de todos los preflights
dirty de esa ronda:

```bash
control_plane_active_task_id="$LOCAL_CHILD_TASK_ID"
control_plane_trusted_session_id="$TRUSTED_HOST_SESSION_ID"
```

Añadir RED: dirty + lease/task/session exactos pasa; task/session ausente o
distinto falla. El CLI compara strings y no puede conocer su canal de origen;
la procedencia host-bound pertenece al grant/contexto opaco validado por el
host. Un JSON con IDs correctos puede reproducir los argv diagnósticos, pero no
crear ese grant ni autorizar el efecto. El runtime no busca implícitamente “el
único lease activo”.

A/B están sustituyendo precisamente la evidencia serializable insegura y aún no
tienen el adaptador GitHub que la reemplaza. Antes de editar PR A, el CLI del
attestor v1 ejecuta `task start --outcome commit` para `LOCAL-R0`, con
session/scope host-bound; no se necesita un subcomando futuro. Tras el commit,
el mismo attestor v1 recorre sus transiciones schema-1 y `task close` elimina el
lease; no se atribuye un `LocalGitObservation` inexistente a v1. Desde PR B, el
attestor de la base ya puede usar el contrato nuevo si ese commit lo contiene,
pero nunca mezcla formatos en una misma ronda. El remote se gestiona fuera de
ese child. Si después aparece feedback, un check fallido o una base avanzada,
no se edita con el child cerrado: se reencuadra
`LOCAL-R<n+1>` desde el HEAD actual, se obtienen digests/lease nuevos y se
repite `implementing → verifying → review_ready → committed → close`.
No usarán `--evidence PATH` para estados remotos, no dejarán una task
autoritativa abierta y no fingirán lifecycle completo.
`bootstrap-execution.json` contiene solo task/route digests, branch, lease ID,
revision, motivo estructurado, prior/new HEAD, limitación y hashes Git
observados; no se trata como grant ni receipt de provider. PR C usa el mismo
bootstrap por rondas y añade únicamente resultados shadow del provider
candidato. Tras merge y verificación manual de base, el cleanup comprueba que
no quede ningún child ni lease activo y publica una decisión explícita
`retain|remove_local|remove_local_and_remote` con receipt —o un
`POST_MERGE_CLEANUP_PENDING` visible— antes de crear el siguiente worktree. No
se elimina por omisión ninguna rama ni worktree. La creación/merge de A/B/C
continúa sujeta a autorización host y a verificación manual reproducible. La
excepción solo termina en PR D, cuyo task lee runtime,
lock y policy desde la base C fusionada. Si el provider no supera el
forward-test, D conserva `pending_github_host_adapter` y no se promueve
lifecycle autoritativo.

En toda task autoritativa se separan:

```text
governing_base_commit
governing_policy_digest
candidate_policy_digest
```

El host lee `.codex/project-policy.toml` mediante el objeto Git inmutable
`governing_base_commit` de `origin/<base>` verificado, lo valida y crea un
`GoverningPolicy` opaco. Base, remote, integration strategy y required checks
proceden siempre de ese wrapper. La policy del worktree solo calcula
`candidate_policy_digest` y drift. Si el diff cambia policy, provider o gates,
una transición especializada marca `policy_change_pending`; la candidata no
gobierna ese PR y solo podrá ser governing para una task nacida después de su
merge demostrado.

La factoría ejecutable se introduce en Task 1 —no se deja como narración—:
`load_governing_policy_from_runtime()` consume el
`GoverningRuntimeObservation` del attestor limpio, lee únicamente el path
canónico desde ese checkout/base, valida schema/cap/no-symlink y liga
runtime/base/lock/policy/session/invocation/TTL en un wrapper opaco one-shot.
No acepta policy bytes/path/digest del candidate o caller.

Registrar task/route/policy/registry/inventory digests en estado worktree-local.
Cargar todos los recursos `required` y crear su receipt después de leerlos; un
recurso obligatorio no ready detiene la implementación. `route-verify` se
ejecuta sobre ese receipt real, no sobre un recibo inicial vacío.

El plan no es un grant. Antes del primer commit, push, creación/actualización de
PR o merge, comprobar en el canal host que existe autorización vigente para ese
efecto y el mismo task digest.

Ejemplo multifrente de control: si el prompt mezclase leases, onboarding,
pagos y estadísticas, el pre-framing produciría cuatro `goals[]`, relaciones
`depends_on` reales y `independent_work` solo donde se demuestre independencia.
No crearía cuatro ramas, agentes ni writers durante el encuadre.

## Task 1: Estabilizar invariantes heredadas antes de ampliar el producto

Esta tarea es un gate de entrada derivado de la comprobación del código real y
del chat `Aplicacionesss`. No debe mezclarse con Clarification Gate ni Risk
Sentinel. Se entrega en un PR de estabilización independiente, se fusiona solo
con autorización host-bound y la siguiente rama nace del `origin/main`
verificado.

**Files:**
- Modify: `scripts/control-plane`
- Modify: `.codex/hooks/control_plane_hook.py`
- Modify: `.codex/control-plane.lock`
- Modify: `control_plane/resource_registry.py`
- Modify: `control_plane/routing.py`
- Create: `control_plane/host_bridge.py`
- Create: `control_plane/scopes.py`
- Modify: `control_plane/lifecycle.py`
- Modify: `control_plane/graph.py`
- Modify: `control_plane/cli.py`
- Modify: `control_plane/adoption.py`
- Modify: `control_plane/policy.py`
- Modify: `control_plane/lockfile.py`
- Modify: `tests/test_resource_registry.py`
- Modify: `tests/test_routing.py`
- Modify: `tests/router_test_support.py`
- Modify: `tests/test_project_profiles.py`
- Modify: `tests/test_assurance.py`
- Modify: `tests/test_lifecycle.py`
- Modify: `tests/test_graph.py`
- Modify: `tests/test_cli_v2.py`
- Modify: `tests/test_adoption.py`
- Modify: `tests/test_policy.py`
- Modify: `tests/test_lockfile.py`
- Modify: `tests/test_repository_contract.py`

- [ ] **Step 1: Escribir RED para los defectos concretos**

```text
test_repository_root_lease_conflicts_with_every_child_scope
  Given: un lease sobre "." y otro sobre "src/**".
  Expect: E_LEASE_CONFLICT en ambos órdenes; cubrir "./", trailing "/**",
  simetría y normalización idempotente.

test_repository_root_writer_overlaps_every_parallel_writer
  Given: writers paralelos sobre "." y "src/**".
  Expect: G_WRITER_OVERLAP; si dependen entre sí, la secuencia es válida.

test_pr_review_cycle_can_record_a_new_committed_and_pushed_head
  Given: task en pr_draft y un nuevo commit solicitado por revisión o CI.
  Expect: API especializada vuelve a implementing, nuevo committed/pushed y
  actualización del mismo PR; el head anterior queda como historial, no como
  evidencia vigente.

test_overlapping_leases_conflict_across_two_worktree_git_dirs
  Given: leases persistidos en dos Git dirs de worktree diferentes del mismo
  common Git dir.
  Expect: adquisición serializada y E_LEASE_CONFLICT; no ledger compartido.

test_worktree_inventory_overflow_or_truncation_is_unknown
  Given: 256, 257 worktrees, porcelain truncado/malformado, duplicado o Git dir
  no resoluble.
  Expect: 256 completos se observan; cap+1 o cualquier incompletitud devuelve
  E_LEASE_OBSERVATION_UNKNOWN antes de adquirir/escribir el lease.

test_writer_lease_never_expires_or_transfers_implicitly
  Given: TTL transcurrido, process ID ausente o session distinta.
  Expect: el lease sigue bloqueando; reloj/PID no demuestran abandono y no existe
  auto-takeover.

test_abandoned_lease_recovery_requires_exact_owner_and_host_authorization
  Given: lease owner exacto y TrustedLeaseRecoveryAuthorization one-shot frente a
  mapping, binding distinto, owner todavía confirmado activo o inventario
  incompleto.
  Expect: solo el primer caso escribe tombstone/release; los demás devuelven
  E_LEASE_RECOVERY_UNAUTHORIZED|E_LEASE_OBSERVATION_UNKNOWN sin tomar ownership.

test_abandoned_lease_recovery_requires_a_new_task
  Given: recovery válida tras crash/cambio de sesión.
  Expect: la task antigua queda blocked/resume_forbidden con razón durable y la
  nueva sesión solo puede escribir después de crear task/decision/lease nuevos.

test_worktree_inventory_observation_is_host_bound_one_shot_and_complete
  Given: inventario completo exacto frente a mapping/JSON, replay, TTL,
  cross-common-dir, cap+1, symlink, duplicado o salida truncada.
  Expect: solo el primero produce `ValidatedWorktreeInventoryObservation`; todos
  los demás fallan E_LEASE_OBSERVATION_UNKNOWN antes del rescue.

test_worktree_registry_race_is_detected_after_observation_before_lease_scan
  Given: inventario host-bound completo y, antes de consumirlo bajo flock, un
  add/remove/prune externo cambia cualquier registro worktree/Git-dir.
  Expect: el scan local produce identity digest distinto,
  E_LEASE_OBSERVATION_STALE, cero lease escrito/liberado y reintento desde una
  observación nueva; nunca ejecuta Git bajo el flock.

test_verification_execution_context_denies_product_writes_and_unlisted_commands
  Given: verifier con lease raíz frente a Edit/Write/apply_patch, git add/commit,
  argv no perfilado o tool distinto.
  Expect: `VerificationExecutionContext` solo admite gates exactos de su profile;
  todo write/stage/commit de producto se deniega aunque el lease cubra ".".

test_verification_command_observation_detects_tracked_index_and_untracked_mutation
  Given: gate permitido con snapshot Git before/after exacto frente a comando
  que altera/stagea un archivo o crea un untracked/symlink fuera del temp/state.
  Expect: solo HEAD/index/tracked e inventario untracked byte-idénticos y
  residuos limitados a temp/state permiten continuar; output parcial/cap+1 o
  cualquier drift bloquea/cierra el verifier sin limpiar.

test_verification_runner_uses_sanitized_environment_and_reports_host_isolation
  Given: canarios en token/proxy/askpass/keychain env, HOME real y socket de red
  frente a temp HOME/env allowlist y adapter sandbox disponible/ausente.
  Expect: ningún canario llega al hijo; HOME/TMP/cache son dedicados, askpass y
  proxy quedan desactivados. Sin prueba nativa de read-root/no-network el receipt
  marca pending_verification_host_isolation y no habilita semantic enforcement;
  código external_untrusted no se ejecuta localmente.

test_verification_runner_is_mechanical_closed_and_needs_no_host_adapter
  Given: profile assurance C o governing-base verifier D frente a
  profile/HEAD/task/session/lease distinto, command ID repetido o argv
  proporcionado por caller.
  Expect: el runner single-process resuelve sus argv desde constantes, posee
  Popen+snapshots y funciona sin `HostAdapterCapability`; falla cerrado ante
  profile/binding/command mismatch y TaskEnvelope no selecciona ni amplía el
  profile.

test_verification_task_envelope_factory_emits_complete_schema1_only
  Given: profiles/IDs permitidos frente a profile arbitrario, task ID inválido
  o override de objective/goals/risk/effects/resources.
  Expect: la factoría cerrada emite todos y solo los campos TaskEnvelope v1,
  validate_task_envelope PASS y ningún caller puede omitir/ampliar campos.

test_bootstrap_state_is_owned_and_closed_by_immutable_base_runtime
  Given: LOCAL-R0 creado por runtime v1 y candidate cambia lifecycle/state/CAS.
  Expect: candidate devuelve E_FOREIGN_RUNTIME_STATE; attestor limpio en la base
  avanza/cierra/libera el state exacto y no queda lease. Crash conserva recovery
  del runtime propietario, nunca migración oportunista.

test_abort_verification_is_owner_bound_two_phase_and_not_resumable
  Given: verifier en implementing/verifying con gate FAIL frente a task,
  generation, session, lease o reason code distinto y fault injection tras
  marker/tombstone/unlink.
  Expect: solo `abort_verification()` publica
  finalizing_verification_abort, libera owner-bound y termina
  blocked/resume_forbidden; recovery acepta las tres fases durables y generic
  resume/close no revive ni intenta liberar otra vez.

test_adoption_mutex_is_released_after_process_kill
  Given: subprocess que toma el mutex y termina abruptamente.
  Expect: el kernel libera flock; adopt y upgrade posteriores recuperan el
  journal sin E_ADOPT_BUSY permanente.

test_other_worktree_recovers_owner_transaction_after_each_crash_point
  Given: A muere después de cada write/rename/fsync de manifest, pointer,
  generación WAL, COMMITTED o config y B continúa.
  Expect: B resuelve el manifest inmutable, valida la cadena y restaura el
  snapshot exacto antes de iniciar otra transacción; COMMITTED durable permite
  limpiar un pointer residual sin rollback.

test_owner_pointer_never_hashes_mutable_journal
  Given: varias generaciones válidas cambian el WAL tras publicar el pointer.
  Expect: el digest del manifest apuntado permanece estable; ninguna evolución
  normal produce E_ADOPT_RECOVERY_UNKNOWN.

test_broken_wal_chain_or_ambiguous_generation_fails_closed
  Given: previous digest incorrecto, dos generaciones con mismo número,
  manifest/path fuera del owner o COMMITTED no ligado al final.
  Expect: E_ADOPT_RECOVERY_UNKNOWN sin nueva mutación.

test_serialized_inventory_cannot_self_attest_readiness
  Given: JSON que cambia un MCP unknown por authenticated/healthy/ready y
  recalcula su digest.
  Expect: el CLI operativo no lo acepta; solo InventoryObservation in-memory.

test_inventory_observation_rejects_binding_expiry_and_replay
  Given: subcasos repo, worktree, registry digest o task digest distinto,
  deadline vencido y segundo consumo del mismo observation ID.
  Expect: código O_* exacto en cada caso; reloj monotónico inyectado.

test_serialized_transition_evidence_cannot_advance_authoritative_state
  Given: JSON coherente que afirma push, PR, checks, merge o release.
  Expect: no promueve lifecycle; exige observación host-bound del proveedor.

test_lifecycle_observation_rejects_binding_expiry_and_replay
  Given: task, repo, worktree, branch/HEAD o invocation distinto, deadline
  vencido u observation ID ya consumido.
  Expect: transición no muta estado y devuelve código O_* exacto.

test_task_lease_release_is_owner_bound_idempotent_and_unblocks_next_worktree
  Given: release exacto, segundo release, identidad errónea y lease raíz B.
  Expect: mismo owner es idempotente; identidad distinta no borra; B adquiere
  solo después del release de A.

test_release_locked_never_reacquires_common_dir_flock
  Given: close/finalize/recovery ya poseen LeaseLockToken válido frente a token
  falso/cross-common-dir y fault injection en cada fase.
  Expect: `_release_locked()` completa sin self-deadlock; token inválido falla
  antes de mutar y ningún camino intenta adquirir el flock dos veces.

test_close_and_suspend_writer_are_two_phase_and_crash_recoverable
  Given: fault injection tras marker, tombstone/unlink y antes del state final
  para close y suspend/reframe.
  Expect: finalizing_close|finalizing_suspend nunca son escribibles/reanudables;
  recovery cubre marker+lease, marker+lease+tombstone y
  marker+tombstone/sin lease; completa el destino sin wrapper opaco vivo ni
  lease bloqueante.

test_abandoned_recovery_never_leaves_released_lease_with_resumable_task
  Given: crash en cada frontera de recover_abandoned().
  Expect: marker `finalizing_abandon` bloquea resume, `_release_locked()` es
  idempotente y recovery termina blocked/resume_forbidden con cambios intactos.

test_bootstrap_review_round_uses_a_fresh_child_and_lease
  Given: LOCAL-R0 cerrado y checks_failed o base_advanced.
  Expect: el task cerrado no reabre; LOCAL-R1 usa task/decision digests y lease
  nuevos, alcanza committed, cierra y libera sin perder el historial R0.

test_start_revision_acquires_new_writer_lease_before_implementing
  Given: review_feedback|checks_failed en mismo base/PR y scope libre frente a
  otro worktree ocupando scope, crash o observation stale.
  Expect: transición intermedia no escribible; solo common→per-task + lease
  nuevo + state durable llega a implementing. Conflicto conserva pr_draft/
  pr_ready sin writer y no pierde evidencia.

test_start_revision_uses_lock_token_aware_acquire_without_relocking_or_subprocess
  Given: `start_revision()` ya posee common-dir y per-task, inventario
  prevalidado exacto y `LeaseLockToken` válido frente a token falso, inventario
  consumido/stale o intento de llamar al acquire público.
  Expect: solo `_acquire_locked()` escribe el lease sin readquirir flock ni
  ejecutar Git; los demás fallan antes de mutar y no producen self-deadlock.

test_base_advance_cannot_use_start_revision
  Expect: reason=base_advanced devuelve E_REFRAME_REQUIRED y exige
  suspend/release + TaskEnvelope/RouteDecision/lease nuevos.

test_dirty_preflight_requires_exact_active_task_and_session
  Given: árbol dirty con lease válido frente a task/session exactos, ausentes o
  distintos.
  Expect: solo ambos IDs exactos pasan el gate Git/lease; no se autoelige un
  lease por unicidad y el efecto aún exige grant host-bound separado.

test_clean_remote_preflight_requires_host_bound_remote_effect_context
  Given: child local cerrado/lease liberado y árbol limpio frente a contexto
  host exacto, mapping equivalente, HEAD anterior o session distinta.
  Expect: solo el wrapper exacto permite evaluar el efecto; nunca se busca ni
  revive el lease liberado.

test_remote_effect_context_revalidates_task_schema_digest_and_outcome
  Given: task exacta frente a mapping inválido, digest distinto, outcome
  insuficiente o effect distinto.
  Expect: solo task exacta produce wrapper one-shot para ese effect.

test_remote_effect_context_revalidates_pr_base_checks_and_invocation_at_use
  Given: contexto exacto frente a PR, base SHA, checks digest o invocation
  distinto/cambiado entre creación y consumo.
  Expect: solo la identidad fresca completa valida; cualquier drift invalida el
  wrapper antes del efecto y exige reobservar/recrear.

test_pull_request_outcome_cannot_reuse_context_for_integration
  Given: initiative B outcome pull_request y contexto v2 válido de push/PR.
  Expect: integration queda bloqueada hasta crear task outcome integration,
  contexto exacto de PR/base/head/checks y grant nuevo; A prueba la misma
  separación mediante bindings host legacy, no con este wrapper.

test_remote_effect_context_never_authorizes_local_write_or_commit
  Expect: incluso un wrapper válido solo habilita validación del efecto remoto
  nombrado; dirty tree, local_write o commit exigen child+TaskLease.

test_task1_defines_native_host_types_without_serialized_factory
  Given: NativeSessionEvent/NativeUserInteractionEvent reales del adapter
  frente a mapping, JSON, prompt, env o evento de otra session/invocation.
  Expect: solo el evento host produce HostAdapterCapability o autorización de
  recuperación one-shot; Task 1 compila sin depender de contratos de Task 3.

test_governing_policy_has_a_task1_loader_bound_to_clean_base_runtime
  Given: attestor limpio/base/lock/policy exactos frente a path/bytes/digest
  candidate, symlink, cap, schema, session/invocation/TTL o runtime drift.
  Expect: solo `load_governing_policy_from_runtime()` produce
  `GoverningPolicy`; PR B puede consumirla sin usar la factoría posterior de
  Risk Sentinel ni autoatestiguar la policy del worktree.

test_verification_target_attestors_produce_both_closed_target_types
  Given: inventario worktree fresco y target candidate exacto frente a verifier
  limpio en el commit gobernante.
  Expect: las factorías host emiten respectivamente
  ValidatedCandidateWorktreeObservation y
  ValidatedGoverningBaseWorktreeObservation; tipo cruzado, mapping, replay,
  dirty/HEAD/branch/common-dir drift falla antes del bootstrap.

test_governing_stage_and_commit_effects_are_closed_and_one_shot
  Given: runtime gobernante, child/lease/scope/grant exactos frente a argv,
  paths, message, HEAD, index, session, invocation o grant distintos.
  Expect: solo stage_allowlisted_paths y commit_staged_change construyen argv
  internos, observan índice/commit exactos y consumen grants separados.

test_candidate_cannot_self_host_stage_commit_push_or_pr
  Given: runtime gobernante de la base frente al runtime candidate coordinado
  con policy/lock.
  Expect: PR B/C usan exclusivamente el runtime ya fusionado; PR A conserva la
  excepción legacy host-direct y ningún candidate gobierna su propio efecto.

test_feature_push_and_pr_mutation_consume_distinct_closed_contexts
  Given: child cerrado/árbol limpio, contextos remote_write y pull_request,
  provider host doctorado y grants separados frente a reuse, body libre,
  repo/base/head/PR/session/invocation/provider drift.
  Expect: push y create/update PR ejecutan plantillas cerradas distintas,
  producen observaciones host-bound y ningún contexto/grant sirve al otro.

test_pr_write_provider_is_pre_authenticated_host_bound_and_secret_free
  Given: provider nativo/gh ya autenticado y doctored frente a mapping, plugin
  candidate, token/env, host/repo distinto o provider no ready.
  Expect: solo `ValidatedGitHubPullRequestWriteProvider` permite la mutación
  exacta; nunca instala, autentica, lee/imprime token ni acepta body no validado.

test_remote_policy_decision_and_policy_only_update_are_governing_base_owned
  Given: runtime A gobernante que encuadra evento nativo y muta solo policy
  frente al mismo código candidate C, mapping, adopción completa, worktree/
  lease/draft/generation distintos.
  Expect: solo tipos/factorías ya fusionados en la base producen decision,
  draft y receipt policy-only; candidate no se gobierna ni toca MANAGED_FILES.

test_source_launcher_ignores_isolated_runtime_shadow
  Given: lock runtime_layout=source y runtime aislado espurio.
  Expect: launcher y hook nunca importan el shadow.

test_isolated_launcher_ignores_top_level_runtime_shadow
  Given: distribución adoptada y un control_plane/ raíz espurio.
  Expect: solo el runtime aislado validado puede ejecutarse.

test_runtime_layout_mismatch_missing_or_empty_fails_before_import
  Given: combinación layout/package/ruta incompatible, runtime ausente o vacío.
  Expect: error estable antes de importar código de runtime.

test_pr_a_adopted_runtime_imports_host_bridge_and_scopes_without_source
  Given: adopt/upgrade de PR A y source tree oculto.
  Expect: runtime isolated incluye ambos módulos, doctor/preflight los importa.
```

Añadir los mismos casos para `upgrade_apply()` y fault injection antes/después
de journal.

- [ ] **Step 2: Ejecutar RED completo**

```bash
python3 -m unittest \
  tests.test_resource_registry \
  tests.test_routing \
  tests.test_project_profiles \
  tests.test_assurance \
  tests.test_lifecycle \
  tests.test_graph \
  tests.test_cli_v2 \
  tests.test_adoption \
  tests.test_policy \
  tests.test_lockfile \
  tests.test_repository_contract \
  -v
```

Expected: solo los casos nuevos fallan por las garantías ausentes.

- [ ] **Step 3: Unificar la semántica de ownership**

Extraer un helper puro común que normalice scopes y aplique:

```text
"." es la raíz universal
"path/**" posee path y todos sus descendientes
dos scopes solapan si uno posee la raíz del otro
un writer dependiente puede reutilizar scope; dos writers activos no
```

Crear `control_plane/scopes.py` con `normalize_scope()`, `scope_owns()` y
`scopes_overlap()`. `TaskLease`, `validate_graph()` y los tests de propiedades
deben consumir esa única semántica. No mantener dos implementaciones parecidas.

- [ ] **Step 4: Coordinar leases entre worktrees sin ledger común**

Cada JSON de lease continúa bajo el Git dir específico de su worktree. Para
evitar dos writers invisibles entre sí:

1. fuera de todo lock, ejecutar una sola vez
   `git worktree list --porcelain`, observar como máximo 256 y construir una
   `ValidatedWorktreeInventoryObservation`;
2. adquirir `fcntl.flock` sobre
   `<git-common-dir>/codex-control-plane/locks/leases.lock`;
3. sin subprocess, escanear las entradas de registro Git/worktree del common
   dir y exigir que su identity digest coincida con la observación fresca;
4. dentro del mutex, leer y validar los leases de cada Git dir observado;
5. rechazar solapamientos;
6. escribir atómicamente el lease solo en el Git dir del worktree propietario.

El archivo común es un mutex de coordinación, no una fuente de estado ni un
ledger. Un Git dir ilegible, un lease malformado o una identidad no demostrable
produce `E_LEASE_OBSERVATION_UNKNOWN`; no se asume ausencia. El parser debe
demostrar EOF/estructura completa. Si observa un worktree 257, output cortado,
registro duplicado/malformado o límite de bytes antes del EOF, falla con el
mismo código sin omitir el posible conflicto.

Todo add/remove/prune de worktree realizado por el control plane toma ese mismo
flock para su fase de mutación, pero ejecuta Git fuera del lock mediante un
protocolo prepare→Git→reobserve/commit. Un cambio de registro, incluso externo,
entre la observación y el consumo altera el identity digest del scan local y
devuelve `E_LEASE_OBSERVATION_STALE`; se reintenta desde el paso 1. Nunca se
ejecuta Git ni otro subprocess bajo el flock.

La API pública `TaskLease.acquire()` realiza observación/validación fuera del
lock, adquiere el flock y delega sin relock en:

```text
TaskLease._acquire_locked(
  lease_lock_token: LeaseLockToken,
  *,
  task_id,
  worktree,
  branch,
  session_id,
  policy_digest,
  scopes,
  inventory: ValidatedWorktreeInventoryObservation
) -> TaskLease
```

`_acquire_locked()` consume una observación prevalidada de la misma invocation,
recalcula bajo el mutex el identity digest de los registros ya observados y
escribe el lease solo si sigue siendo exacto. Valida que el token pertenezca al
common-dir esperado y jamás llama a Git, subprocess ni al acquire público. Solo
los caminos internos que ya poseen el flock —incluido `start_revision()`—
pueden invocarlo; no existe CLI ni constructor de `LeaseLockToken`.

El host expone, sin deserializador público:

```text
observe_worktree_inventory(
  *,
  canonical_common_git_dir,
  invocation_id,
  clock,
  ttl_seconds,
  max_worktrees=256,
  max_output_bytes
) -> WorktreeInventoryObservation

validate_worktree_inventory_observation(
  observation,
  *,
  expected_common_git_dir,
  expected_invocation_id,
  clock
) -> ValidatedWorktreeInventoryObservation

attest_candidate_verification_target(
  *,
  inventory: ValidatedWorktreeInventoryObservation,
  canonical_repository,
  candidate_worktree,
  expected_branch,
  expected_head,
  expected_candidate_policy_digest,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ValidatedCandidateWorktreeObservation

attest_governing_base_verification_target(
  *,
  inventory: ValidatedWorktreeInventoryObservation,
  canonical_repository,
  verifier_worktree,
  expected_governing_base_commit,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ValidatedGoverningBaseWorktreeObservation
```

La observación liga common-dir canónico, invocation, monotonic timestamps,
parser/version, cap, bytes consumidos, EOF demostrado, conjunto ordenado de
worktree/Git-dir/lease digests y nonce one-shot. `recover_abandoned()` consume
el wrapper en la misma operación. Mapping/JSON, replay, TTL, common-dir distinto,
cap+1, symlink/traversal, registro duplicado o output parcial devuelven
`E_LEASE_OBSERVATION_UNKNOWN`; no existe fallback a un listado aportado por el
caller. Tests cubren observación/validación/consumo además del parser puro.

Las dos factorías de target pertenecen ya a Task 1 y poseen su inspección Git
read-only cerrada: revalidan repo/common-dir/worktree, registro, branch o
detached exacto, HEAD, árbol/índice limpios, policy/base digest, session,
invocation, TTL y nonce one-shot. No convierten
`ValidatedCreatedWorktreeObservation` por nominalidad ni aceptan paths/estado
aportados por JSON. El target candidate y el verifier de base son tipos opacos
distintos y no intercambiables. Task 12 y Task 14 deben invocar explícitamente
la factoría correspondiente después de una observación fresca; así sus
producers existen antes de cualquier `bind_*_bootstrap_authority()`.

Un lease raíz de verificación no equivale a permiso general de escritura. Este
control es deliberadamente mecánico y no depende del adapter host semántico.
`lifecycle.py`/`cli.py` exponen:

```text
attest_verification_governing_runtime(
  *,
  attestor_worktree,
  governing_base_commit,
  target_worktree,
  expected_runtime_layout,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> GoverningRuntimeObservation

bind_candidate_assurance_bootstrap_authority(
  *,
  governing_runtime: GoverningRuntimeObservation,
  candidate_target: ValidatedCandidateWorktreeObservation,
  expected_head,
  session_id,
  invocation_id,
  clock
) -> CandidateAssuranceBootstrapAuthority

bind_governing_base_bootstrap_authority(
  *,
  governing_runtime: GoverningRuntimeObservation,
  verifier_target: ValidatedGoverningBaseWorktreeObservation,
  expected_governing_base_commit,
  session_id,
  invocation_id,
  clock
) -> GoverningBaseBootstrapAuthority

create_verification_task_bootstrap(
  *,
  task_id,
  authority:
    CandidateAssuranceBootstrapAuthority |
    GoverningBaseBootstrapAuthority
) -> VerificationTaskBootstrap

create_verification_execution_context(
  *,
  task_context,
  lease,
  canonical_repo,
  expected_head,
  session_id,
  dedicated_temp_root,
  clock
) -> VerificationExecutionContext

_run_verification_command(
  *,
  context: VerificationExecutionContext,
  command_id: str,
  clock
) -> CompletedVerificationCommand

run_verification_profile(
  *,
  context: VerificationExecutionContext,
  task_store,
  expected_generation: int,
  clock
) -> VerificationExecutionReceipt

build_verification_task_envelope(
  *,
  task_id,
  profile: control_plane_assurance | governing_base_verification
) -> Mapping[str, Any]
```

Task 1 introduce `GoverningRuntimeObservation` y esta primera factoría. El tipo
liga attestor common-dir/worktree/HEAD/cleanliness, governing base, target
worktree/HEAD, launcher/runtime/lock paths+digests, layout, session/invocation y
TTL/nonce. No contiene provider GitHub y no acepta purpose/paths/digests del
caller. Task 9 añadirá una factoría distinta que completa el mismo contrato con
provider bindings para lifecycle remoto; no redefine el tipo ni obliga a Task 1
a importar código futuro.

Los profiles son constantes cerradas del runtime, no strings ampliables desde
TaskEnvelope/route. El caller no aporta `task_kind`, profile ni un runtime
genérico. Las dos factorías host-only anteriores atestiguan relaciones
distintas: C liga un attestor gobernante a un candidate target; la comprobación
previa a D liga attestor y verifier target separados al mismo commit
gobernante. Cada una consume observaciones tipadas, frescas, invocation-bound y
one-shot y devuelve un tipo opaco distinto. Un target piloto reutilizado como
verifier, un mapping, un string de kind o un runtime no ligado al target falla
antes de crear estado.

`create_verification_task_bootstrap()` deriva por el **tipo del wrapper** el mapa
inmutable `CandidateAssuranceBootstrapAuthority →
control_plane_assurance` o `GoverningBaseBootstrapAuthority →
governing_base_verification`; no acepta profile directo. Llama internamente a
`build_verification_task_envelope()`, liga authority digest +
`verification_profile_id + profile_digest + runtime_digest + target_digest` en
un wrapper opaco y TaskStore los persiste al crear el child.
`create_verification_execution_context()` los relee del
`task_context` validado; el caller y el CLI no eligen profile. Un ID/digest
distinto, mapping o cambio después de start falla antes de ejecutar.

Cada profile enumera command IDs, required supplemental evidence y construye
internamente cwd/argv absolutos de Tasks 12/14; el caller nunca pasa argv. El
runner posee `subprocess.Popen` sin shell, timeout/caps, process group y
snapshots Git before/after. Edit, Write, apply_patch, `git add`, `git commit` y
comandos ajenos no tienen entrypoint. `_run_verification_command()` es privado
y sus wrappers no cruzan procesos. `scripts/control-plane verification-run`
acepta únicamente repo y task-id; revalida task/session/lease/HEAD, deriva el
profile ligado y `run_verification_profile()` ejecuta **toda** la secuencia y el
agregado en ese mismo proceso. `--profile`, `--command-id`, results o receipts
aportados por el caller son argumentos desconocidos.

Los command IDs de v2.1 son cerrados. `control_plane_assurance` incluye, sobre
el HEAD final de PR C: `normal_budget`, `assurance_budget` —que ejecuta
propiedades, 24 mutantes y el benchmark de 10.000 recursos—,
`policy_check`, `registry_check`, `doctor`, `risk_integration_smoke`,
`security_regression` y `candidate_diff_check`.
`governing_base_verification` incluye `normal_budget`, `policy_check`,
`registry_check`, `doctor` y `governing_tree_clean`. El runner tiene además un
deadline agregado de 300 segundos para el profile C y 90 para el profile base;
un child timeout no se convierte en PASS. Modificar runtime/tests/plan/spec o
corregir un reviewer invalida el profile digest y obliga a ejecutar de nuevo
**normal, mutation y performance** en el HEAD resultante.

`build_verification_task_envelope()` es también una factoría interna de
plantillas cerradas, no un overlay parcial. Para
`control_plane_assurance` emite exactamente:

```json
{
  "schema_version": 1,
  "task_id": "<validated task id>",
  "objective": "Verify the bound control-plane candidate without changing repository content.",
  "intent": "operate",
  "phase": "verify",
  "requested_outcome": "local_change",
  "goals": [{
    "id": "verify-candidate",
    "summary": "Run the closed verification profile and publish bounded receipts.",
    "domains": ["generic"],
    "depends_on": []
  }],
  "domains": ["generic"],
  "signals": ["regression_risk"],
  "scope_paths": ["."],
  "risk": {
    "uncertainty": 1,
    "blast_radius": 2,
    "irreversibility": 0,
    "verification_complexity": 2
  },
  "risk_provenance": "project_policy",
  "effects": [
    {"name": "local_read", "source": "project_policy"},
    {"name": "local_write", "source": "project_policy"}
  ],
  "explicit_resources": [],
  "excluded_resources": []
}
```

Para `governing_base_verification` cambia únicamente dos constantes cerradas:
objective=`Verify the bound governing base without changing repository
content.`, goal ID=`verify-governing-base` y summary=`Run the closed governing
base profile and publish bounded receipts.`; el resto del objeto schema-1 es
idéntico. El caller no aporta esos strings ni un overlay.

El profile/HEAD/session/commands no se introducen como campos extra: se ligan
en `VerificationExecutionContext`. La factoría valida el resultado antes de
devolverlo; el caller proporciona un task ID válido y el authority wrapper
host-bound, nunca uno de los profiles.

Antes y después de cada gate, el mismo proceso observa HEAD, índice y blobs
tracked, enumera con límites/EOF `git status --porcelain=v2 -z
--untracked-files=all` y toma un `WorktreeResidueSnapshot` acotado de todas las
entradas bajo el worktree salvo el Git dir, incluidas las ignoradas; hace
`lstat`, rechaza symlinks y liga paths/tipos/tamaños/modos/content digests.
Overflow, permiso no observable o carrera de directorio es UNKNOWN. Así un
`__pycache__` o artefacto ignorado no desaparece de la evidencia por no salir
en `git status`.
`CompletedVerificationCommand` solo es PASS si HEAD/index/tracked tree e
inventario completo —tracked, untracked e ignored— siguen byte-idénticos y todo
residuo se limita al Git-dir state o al temp root dedicado, ambos fuera del
worktree. Salida parcial, cap+1, symlink inesperado o parser incompleto es
UNKNOWN y bloquea.

El hijo recibe un entorno mínimo allowlisted: `PATH` no procede de
TaskEnvelope ni amplía `.codex/project-policy.toml` schema 1; cada profile
contiene un mapa cerrado de ejecutables absolutos resuelto por `doctor` al crear
el contexto y ligado por digest al `VerificationExecutionContext`,
`LANG/LC_ALL`, `PYTHONDONTWRITEBYTECODE=1`, `HOME`, `TMPDIR`,
`XDG_CACHE_HOME`, `PYTHONPYCACHEPREFIX` y caches de herramientas apuntan a roots
efímeros `0700` fuera del worktree. `compileall` se prueba expresamente y sus
`.pyc` deben aparecer únicamente bajo ese prefix; no se confía en
`PYTHONDONTWRITEBYTECODE` para impedir su escritura.
Eliminar `GH_TOKEN`, `GITHUB_TOKEN`, variables `*_TOKEN|*_SECRET|*_PASSWORD`,
credenciales cloud, cookies, proxies, `GIT_ASKPASS`, `SSH_ASKPASS`,
`SSH_AUTH_SOCK` y config Git heredada; stdin es `/dev/null`, terminal prompts
están deshabilitados. El profile no permite comandos que necesiten credenciales
o red.

Esto reduce exposición pero **no es un sandbox de seguridad**: un proceso del
mismo usuario todavía podría leer rutas absolutas o abrir red si el host no
impone read-roots/egress. Cuando existe, el adapter host aporta una observación
opaca de sandbox/no-network y un canario prueba denegación. Si no existe, el
receipt registra `pending_verification_host_isolation`; permite gates locales
solo para el flujo audit explícitamente autorizado, pero bloquea
semantic-soft-enforce/enforce. Código o dependencias `external_untrusted` no se
ejecutan localmente sin aislamiento; se remiten a CI efímera sin secretos o se
bloquean. Mismatch bloquea el verifier,
conserva los artefactos para diagnóstico y nunca limpia/restaura
automáticamente. No concede autoridad semántica ni externa; por eso no requiere
`HostAdapterCapability`.

`run_verification_profile()` es la única agregación positiva. En un único
proceso crea el contexto, ejecuta en orden canónico exactamente un command por
ID y conserva sus `CompletedVerificationCommand` solo en memoria, todos PASS,
no truncados/no replay y ligados al mismo context
digest/HEAD/task/session/lease/profile ID+digest. Después carga desde TaskStore
—no desde paths/JSON del caller— el set profile-specific exacto de receipts
durables, verifica schema/digest canónico/task/HEAD/profile/generation/owner y
los convierte en
`HostBoundVerificationEvidence` one-shot en memoria:
`control_plane_assurance` requiere `MacOSHookSmokeReceipt`,
`SkillPressureEvaluationReceipt` e `IndependentReviewReceipt` con sus estados
audit/pending explícitos; `governing_base_verification` no admite evidencia
suplementaria. Un `HookReviewReceipt` solo se exige si la policy solicita
promoción semántica, no para C audit-only. Repite el snapshot final completo y
hace compare-and-swap sobre `expected_generation`; solo entonces publica
`VerificationExecutionReceipt` y avanza `verifying → review_ready`. No existe
una transición genérica ni un boolean `all_passed` que sustituya comandos o
receipts. Si falta/sobra/duplica evidencia, hay digest/profile drift o cualquier
resultado FAIL/UNKNOWN, llama la ruta especializada `abort_verification()`; el
receipt no se construye desde JSON. Las ejecuciones diagnósticas anteriores
pueden orientar a reviewers, pero no cuentan: el profile completo se repite
después de que todos los supplemental receipts existan.

Un gate FAIL no usa la transición genérica `blocked` seguida de `close`, porque
esa secuencia no existe en la matriz. Definir:

```text
TaskStore.abort_verification(
  *,
  task_id,
  expected_generation,
  task_digest,
  repo,
  worktree,
  branch,
  session_id,
  lease_digest,
  reason_code: closed_verification_reason_code,
  clock
) -> dict

TaskStore.recover_verification_abort(
  *,
  task_id,
  state_dir,
  common_dir,
  clock
) -> dict
```

Es la única salida de fallo para un verifier en `implementing|verifying`.
Ejecuta common-dir→per-task, publica
`finalizing_verification_abort/resume_forbidden=true`, llama a
`_release_locked()` con el owner exacto y publica
`blocked/verification_aborted=true/resume_forbidden=true`. Recovery acepta
marker+lease, marker+lease+tombstone o marker+tombstone sin lease, con bindings
coincidentes. `TaskStore.resume()`, `close()` y `transition()` rechazan el
resultado; para corregir se crea `LOCAL-R<n+1>` y, tras nuevo HEAD, un verifier
nuevo.

- [ ] **Step 5: Modelar el ciclo de revisión de PR**

No rebobinar arbitrariamente toda la máquina. Añadir una API especializada
`TaskStore.start_revision()` desde `pr_draft` o `pr_ready` a `implementing`,
con evidencia exacta:

```text
pull_request.number
prior_head
reason = review_feedback | checks_failed
observation_digest
revision = contador anterior + 1
```

La API invalida únicamente commit, push y checks ligados al HEAD anterior,
preserva identidad e historial del PR y vuelve a exigir
`verifying → review_ready → committed → pushed → pr_draft → pr_ready`. El
número de PR y governing base deben ser los mismos. Antes de publicar
`implementing`, `start_revision()` valida/consume una observación host-bound
fresca y ejecuta:

1. common-dir → per-task; publica+fsync
   `finalizing_revision/resume_forbidden=true` con prior state/generation,
   observation digest, invocation, scope y lease proposal;
2. mantiene ambos locks y llama a `TaskLease._acquire_locked()` con el
   `LeaseLockToken` ya poseído y la observación de inventario prevalidada;
   adquiere/sincroniza así el lease nuevo para el scope exacto sin relock ni
   subprocess;
3. publica+sincroniza `implementing` con nueva generation/lease digest y retira
   el marker.

`TaskStore.recover_revision_start()` acepta marker sin lease y revierte al
prior `pr_draft|pr_ready`, o marker+lease coincidente y completa
`implementing`; ambos ausentes/presentes incoherentemente o digest distinto
fallan cerrado. No necesita reconstruir la observación opaca consumida. Fault
tests cubren crash antes/después del lease/state. Conflicto de otro worktree
elimina el marker y conserva el prior state sin writer; nunca queda lease
huérfano ni implementing sin lease.
Después, `head_commit` debe ser el nuevo remote head y los checks deben ligar
ese head. `base_advanced` no es una reason válida: devuelve
`E_REFRAME_REQUIRED`, finaliza/suspende y libera la task actual mediante el
protocolo de dos fases, y exige TaskEnvelope/RouteDecision/lease/GoverningPolicy
nuevos. Desde `merged` se exige también una task nueva. El resultado solicitado
no cambia y ninguna transición concede commit, push o actualización del PR.

- [ ] **Step 6: Hacer recuperable el lock sin abrir carreras**

Sustituir el mutex `O_EXCL` persistente por `fcntl.flock` sobre
`<git-common-dir>/codex-control-plane/locks/adoption.lock`. El kernel libera la
exclusión al cerrar o morir el proceso. El journal worktree-local continúa
siendo la evidencia transaccional, pero una transacción que muta config común
publica primero un manifiesto inmutable y después un puntero mínimo en:

```text
<git-common-dir>/codex-control-plane/transactions/adoption-owner.json
```

El puntero contiene únicamente schema, transaction ID, target identity,
owner Git dir registrado y digest/ruta confinada de
`transaction-manifest.json`; no apunta al digest de un journal mutable. El
manifiesto liga target, owner, operación, snapshot previo y ruta confinada del
WAL y nunca se reescribe. El WAL usa generaciones checksummed con
`previous_generation_digest`; un marcador `COMMITTED` liga transaction ID,
última generación y digest final.

Orden durable bajo el mismo flock:

```text
write manifest temp → fsync file → replace → fsync owner dir
write owner pointer temp → fsync file → replace → fsync common dir
por cada paso: write WAL generation temp → fsync → replace → fsync owner dir
fin/rollback: write COMMITTED temp → fsync → replace → fsync owner dir
unlink owner pointer → fsync common dir
```

Después de tomar el mutex, cualquier worktree valida el owner contra
`git worktree list`, abre el manifiesto inmutable, verifica la cadena acotada de
generaciones y recupera desde la última generación válida. Un crash después de
`COMMITTED` pero antes de borrar el pointer se reconoce como operación
terminada y limpia únicamente el pointer. Manifiesto ausente, ruta fuera de un
worktree registrado, cadena rota, generación ambigua o target distinto falla
cerrado con `E_ADOPT_RECOVERY_UNKNOWN`.

No borrar el archivo de lock para “liberarlo” ni usar edad o PID como prueba.
Probar `SIGKILL`/fault injection después de cada write, rename y fsync, incluido
recovery desde otro worktree y el caso `COMMITTED` con pointer aún presente.

- [ ] **Step 7: Cerrar la autoatestación del inventario**

Introducir un `InventoryObservation` opaco y no serializable, creado únicamente
por el builder/adaptador host en memoria. Debe ligar:

```text
observation_id
invocation_id
task_digest
repository_identity
worktree_identity
registry_digest
snapshot_digest
observed_at_monotonic
freshness_deadline
```

`resolve_route()` sigue siendo puro, pero exige ese wrapper; no interpreta su
procedencia. Los tiempos y la capacidad opaca no se serializan ni entran en
`decision_digest`; la decisión liga únicamente el snapshot determinista.
Eliminar `route --inventory PATH`. `inventory` permanece como
informe diagnóstico, y un snapshot serializado no puede reconstruir confianza.
Auth/health de MCP o plugins permanece `UNKNOWN` hasta que un adaptador host
autorizado lo observe. Replays, repo/worktree distinto o expiración fallan
cerrado.

API exacta del boundary:

```text
observe_inventory(
  registry, repo, worktree, task_digest, invocation_id, *, clock, ttl_seconds
) -> InventoryObservation

validate_inventory_observation(
  observation,
  *,
  expected_repo,
  expected_worktree,
  expected_registry_digest,
  expected_task_digest,
  expected_invocation_id,
  clock
) -> ValidatedInventory
```

La factoría llama al validador antes de devolver. `host_bridge.py` consume
`observation_id` una sola vez por invocación; JSON no puede reconstruir la
capacidad. El clock monotónico es parámetro inyectado, por lo que el resolver
recibe únicamente `ValidatedInventory` —wrapper opaco que expone el snapshot
determinista— y conserva pureza/determinismo.
Migrar todos los callers directos, incluidos `router_test_support`,
`test_project_profiles` y `test_assurance`. Una factoría test-only puede emitir
la capacidad opaca únicamente bajo `tests/`; no se empaqueta en el runtime.

- [ ] **Step 8: Cerrar la autoatestación del lifecycle**

Eliminar `task transition --evidence PATH` para estados probatorios. Crear tipos
host-bound separados:

```text
LocalGitObservation
GitHubObservation
ReleaseProviderObservation
```

Cada uno liga task, repository/worktree, branch/HEAD, provider, subject digest
y `invocation_id` además de frescura. `TaskStore` acepta únicamente el wrapper adecuado para cada
transición; un mapping, JSON deserializado, replay, head distinto o evidencia
stale falla. Los observadores locales comprueban Git dentro del mismo proceso.
Hasta que Tasks 9 y el adaptador Apple aporten observadores reales, los estados
remotos permanecen `pending_external_evidence`; nunca se promueven de forma
optimista. `start_revision()` exige también una observación host-bound.

`host_bridge.py` expone validadores host-side con `clock` inyectado y expected
task/repo/worktree/branch/HEAD/provider/invocation. `TaskStore` registra cada
`observation_id` consumido; un ID repetido, binding distinto o expirado falla
antes de mutar el archivo.

Contratos mínimos, sin deserializador público:

```text
observe_local_git_state(
  *,
  task_state,
  expected_repo,
  expected_worktree,
  expected_branch,
  expected_prior_head,
  target_state,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> LocalGitObservation

validate_local_git_observation(
  observation,
  *,
  expected_task_digest,
  expected_repo,
  expected_worktree,
  expected_branch,
  expected_prior_head,
  expected_target_state,
  expected_session_id,
  expected_invocation_id,
  clock
) -> ValidatedLocalGitObservation
```

La observación liga también el nuevo HEAD/ref remoto/índice/tree/diff que
corresponda al `target_state`; el validador compara la invocation viva, no una
cadena aportada por state o CLI. El provider GitHub de Task 9 sigue el mismo
patrón con `expected_invocation_id` en factory, validator y consumo.

Task 1 define además la frontera host mínima que usan sus propias APIs, sin
esperar a Task 3:

```text
NativeSessionEvent
NativeUserInteractionEvent
HostAdapterCapability
TrustedAuthorization

attest_host_adapter_capability(
  native_session_event: NativeSessionEvent,
  *,
  expected_session_id,
  expected_invocation_id,
  clock,
  ttl_seconds
) -> HostAdapterCapability

frame_effect_authorization(
  native_user_event: NativeUserInteractionEvent,
  *,
  host_capability: HostAdapterCapability,
  task_digest,
  session_id,
  repository_identity,
  worktree_identity,
  branch,
  expected_head,
  subject_digest,
  scope_paths,
  effect,
  operation_nonce,
  invocation_id,
  clock,
  ttl_seconds
) -> TrustedAuthorization

consume_authorization(
  authorization: TrustedAuthorization,
  *,
  expected bindings exactos,
  clock
) -> ConsumedAuthorization
```

Los dos eventos son handles opacos aportados por el host Codex actual, no
dataclasses construibles por el runtime. `attest_host_adapter_capability()` es
la única factoría de capability, liga session/invocation/event identity,
TTL/nonce y consumo one-shot, y carece de ruta CLI/JSON/env.
`frame_effect_authorization()` deriva del evento nativo actual un grant para un
único task/effect/scope/subject/HEAD/operación; TTL máximo 300 segundos y
consumo atómico one-shot. Un mapping, request serializable, texto de este plan
o autorización para otro efecto no entra. Los tests usan un
adapter test-only fuera del runtime distribuido. Task 3 reutiliza y amplía
estos tipos para aclaración y confirmación irreversible; no los redefine. Si el host no los ofrece, las
operaciones semánticas quedan `pending_host_capability` y nunca se
autoatestiguan.

Task 1 deja también fusionada, aunque todavía no active campos remotos en la
policy actual, la frontera de decisión/mutación policy-only que necesitará
Task 9:

```text
load_governing_policy_from_runtime(
  governing_runtime: GoverningRuntimeObservation,
  *,
  expected_policy_relative_path=".codex/project-policy.toml",
  expected_schema_version=1,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> GoverningPolicy

parse_required_check_selector(
  "NAME:APP:CONCLUSION[,CONCLUSION]"
) -> RequiredCheckCandidate

frame_project_remote_policy_decision(
  native_user_event: NativeUserInteractionEvent,
  *,
  governing_runtime: GoverningRuntimeObservation,
  host_capability: HostAdapterCapability,
  operation_kind: adoption | policy_update,
  draft_plan_digest,
  source_repository_identity,
  target_repository_identity,
  target_worktree_identity,
  repository_identity,
  required_checks: tuple[RequiredCheckCandidate, ...],
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ProjectRemotePolicyDecision

project_remote_policy_update_plan(
  *,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  candidate_policy_path,
  task_context,
  lease: TaskLease,
  repository_identity,
  required_checks: tuple[RequiredCheckCandidate, ...]
) -> ProjectRemotePolicyUpdateDraft

apply_project_remote_policy_update(
  draft: ProjectRemotePolicyUpdateDraft,
  *,
  governing_runtime: GoverningRuntimeObservation,
  remote_policy_decision: ProjectRemotePolicyDecision,
  authorization: TrustedAuthorization,
  expected_generation,
  clock
) -> ProjectRemotePolicyUpdateReceipt
```

El loader posee la lectura filesystem del attestor ya fijado al
`governing_base_commit`, exige archivo regular no symlink, bytes cap, schema
cerrado y digest ligado a runtime/lock/base; no recibe bytes ni digest de
policy del caller. Estas factorías se ejecutan siempre desde el runtime inmutable de la base y
ligan su runtime/lock/policy digest al draft, decision y receipt. La operación
policy-only relee bajo lease el único path, cambia solo las claves remotas
allowlisted, valida el schema resultante y publica con
backup+journal/temp/fsync/replace/fsync-dir; no recorre `MANAGED_FILES` ni exige
target limpio. Un runtime candidate, mapping, CLI/env, operation kind distinto,
draft/generation/lease drift o policy que intente cambiar cualquier otra clave
falla antes de escribir. PR A solo incorpora/probará esta frontera mediante su
bootstrap legacy; su primer uso gobernante será una base posterior.

Task 1 instala asimismo, para que formen parte de la base gobernante desde PR B,
los dos efectos Git locales mínimos:

```text
stage_allowlisted_paths(
  *,
  governing_runtime: GoverningRuntimeObservation,
  task_context,
  inventory: ValidatedWorktreeInventoryObservation,
  lease: TaskLease,
  authorization: TrustedAuthorization,
  paths: tuple[str, ...],
  expected_head,
  session_id,
  invocation_id,
  tool_use_id,
  clock
) -> LocalGitIndexObservation

commit_staged_change(
  *,
  governing_runtime: GoverningRuntimeObservation,
  task_context,
  inventory: ValidatedWorktreeInventoryObservation,
  lease: TaskLease,
  index_observation: LocalGitIndexObservation,
  authorization: TrustedAuthorization,
  message: str,
  expected_prior_head,
  session_id,
  invocation_id,
  tool_use_id,
  clock
) -> LocalGitObservation
```

Ambos poseen un argv directo interno, `shell=False`, env saneado y gramática
cerrada; el caller no entrega argv. Stage exige allowlist/scope/lease exactos y
commit exige exactamente ese índice, mensaje acotado, HEAD previo y un grant
`commit` distinto del grant `local_write` de stage. Revalidan runtime/lock/
policy gobernantes, worktree/branch/HEAD/session/invocation/tool-use y consumen
cada wrapper una vez. PR A no puede usar estas funciones candidatas para
gobernarse: las incorpora mediante la excepción legacy ya descrita. Desde PR B
son la única ruta normativa de stage/commit; Task 9 las integra en
`ClosedGitEffectOperation` sin cambiar ni debilitar su frontera.

Definir también la frontera limpia posterior al child local:

```text
create_remote_effect_context(
  *,
  task: Mapping[str, Any],
  expected_task_digest: str,
  local_git: LocalGitObservation,
  session_id,
  invocation_id,
  effect: remote_write | pull_request | integration,
  expected_pr_number: int | None,
  expected_base_sha: str | None,
  expected_checks_digest: str | None,
  host_capability: HostAdapterCapability
) -> RemoteEffectContext

validate_remote_effect_context(
  context,
  *,
  expected_task_digest,
  expected_repo,
  expected_worktree,
  expected_branch,
  expected_head,
  expected_session,
  expected_invocation_id,
  expected_effect,
  expected_pr_number,
  expected_base_sha,
  expected_checks_digest
) -> ValidatedRemoteEffectContext
```

La factoría exige árbol limpio, HEAD committed exacto, outcome que alcance el
efecto y que el child escritor esté cerrado con lease liberado. Revalida el
TaskEnvelope schema cerrado y su digest dentro de la llamada; no acepta un
`validated_task` nominal, mapping inválido ni task/outcome/digest distinto.
Tests cubren raw-invalid, digest mismatch y outcome insuficiente. El wrapper no
se serializa, no contiene ni sustituye `TrustedAuthorization`, se invalida al
cambiar HEAD/PR/base/checks, es one-shot y nunca entra en el resolver. Cada
efecto crea/consume su propio wrapper aunque task y HEAD coincidan. Desde B,
`integration` exige un TaskEnvelope separado outcome integration; el contexto
de la initiative pull_request no puede ampliarse. Los valores vivos de
PR/base/checks e invocation se obtienen del host al consumir el contexto y se
comparan exactamente, incluso cuando el valor esperado legítimo sea `None`;
no se confía en los campos serializados del propio wrapper.

La misma base A implementa los efectos remotos mínimos que Task 13 necesita
antes de que exista el provider de lectura de Task 9:

```text
push_validated_feature(
  *,
  context: ValidatedRemoteEffectContext,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  authorization: TrustedAuthorization,
  inventory: ValidatedWorktreeInventoryObservation,
  session_id,
  invocation_id,
  tool_use_id,
  clock
) -> LocalGitObservation

approve_github_pr_write_provider(
  native_provider_event,
  *,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  expected_repository,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ValidatedGitHubPullRequestWriteProvider

build_pull_request_mutation_request(
  *,
  context: ValidatedRemoteEffectContext,
  provider: ValidatedGitHubPullRequestWriteProvider,
  authorization: TrustedAuthorization,
  title: ValidatedPullRequestTitle,
  body: ValidatedPullRequestBody,
  draft: bool,
  expected_pr_number: int | None,
  session_id,
  invocation_id,
  tool_use_id,
  clock
) -> ValidatedPullRequestMutationRequest

execute_pull_request_mutation(
  request: ValidatedPullRequestMutationRequest,
  *,
  clock
) -> PullRequestMutationObservation

validate_pull_request_mutation(
  observation: PullRequestMutationObservation,
  *,
  expected_repository,
  expected_base,
  expected_head_branch,
  expected_head_sha,
  expected_pr_number,
  expected_draft,
  expected_session_id,
  expected_invocation_id,
  clock
) -> ValidatedPullRequestMutationObservation
```

Push posee el argv exacto para el remote/feature de `GoverningPolicy`; no
acepta force, refspec, remote o argv del caller. La mutación PR acepta solo
create cuando `expected_pr_number=None` o update del PR exacto ya observado;
title/body pasan schemas cerrados, cap, saneado y digest, y no contienen
secreto. El provider es una capability host preautenticada y one-shot
(`host.github-pr-write`), no una entry del registry que se autodeclare ready:
doctor debe demostrar binario/host/repo/auth preexistentes sin leer ni imprimir
token. No instala, autentica, fusiona ni amplía permisos. Contexto, provider y
grant se consumen en el mismo proceso; cada efecto necesita wrappers nuevos.
PR A conserva el bootstrap host-direct porque este código todavía es
candidate; desde B estas funciones son obligatorias. Task 9 puede integrar el
push en su executor genérico, pero no cambia esta frontera ni convierte el
provider read-only en write.

Añadir:

```text
TaskLease.release(
  common_dir,
  state_dir,
  *,
  task_id,
  worktree,
  branch,
  session_id,
  policy_digest,
  lease_digest
) -> dict

TaskLease._release_locked(
  lease_lock_token: LeaseLockToken,
  *,
  task_id,
  worktree,
  branch,
  session_id,
  policy_digest,
  lease_digest
) -> dict

frame_lease_recovery_authorization(
  *,
  native_confirmation_event: NativeUserInteractionEvent,
  task_id,
  worktree,
  branch,
  owner_session_id,
  recovering_session_id,
  policy_digest,
  lease_digest,
  inventory: ValidatedWorktreeInventoryObservation,
  invocation_id,
  host_capability: HostAdapterCapability,
  clock,
  ttl_seconds
) -> TrustedLeaseRecoveryAuthorization

TaskLease.recover_abandoned(
  common_dir,
  state_dir,
  *,
  task_id,
  worktree,
  branch,
  owner_session_id,
  policy_digest,
  lease_digest,
  recovery_authorization: TrustedLeaseRecoveryAuthorization,
  worktree_inventory: ValidatedWorktreeInventoryObservation
) -> dict
```

Usa el mismo common-dir flock, valida identidad/digest exactos y escribe un
tombstone compacto worktree-local antes de borrar el lease. Repetir con el
mismo owner/tombstone es idempotente; otra identidad falla. `TaskStore.close()`
usa la transacción `_release_locked()` descrita abajo, no llama a la API pública
mientras posee el flock. Exponer `task lease-release` solo con todos los bindings,
para cleanup idempotente solicitado por el owner vivo del child bootstrap;
crash entre tombstone y unlink se recupera bajo flock. El subcomando no acepta
takeover, session distinta ni `TrustedLeaseRecoveryAuthorization`: recuperar un
owner abandonado solo existe mediante `recover_abandoned()` host-bound.
`release()` adquiere common-dir y delega en `_release_locked()`; una transacción
que ya posee el lock usa únicamente `_release_locked()` con un
`LeaseLockToken` opaco creado por ese lock. El helper valida token/common-dir y
no intenta adquirirlo de nuevo. No existe overload que adivine si el lock ya
está poseído.

`TaskStore.close()` y `TaskStore.suspend_for_reframe()` no hacen dos
mutaciones sueltas. Comparten una transacción:

1. common-dir → per-task; releer lease/state/generation y publicar+fsync
   `finalizing_close|finalizing_suspend` con `resume_forbidden=true`, destino y
   lease digest;
2. soltar per-task, conservar common-dir y llamar `_release_locked()` con el
   token ya poseído;
3. retomar per-task, validar generation/tombstone y publicar `closed` o
   `blocked/E_REFRAME_REQUIRED/resume_forbidden=true`;
4. liberar ambos locks.

`TaskStore.recover_writer_finalization(task_id, state_dir, common_dir)` acepta
exactamente una de tres fases coherentes:

```text
marker + live lease coincidente + sin tombstone
  → iniciar `_release_locked()`
marker + live lease coincidente + tombstone coincidente
  → reanudar `_release_locked()` en unlink/fsync-dir sin reescribir identidad
marker + tombstone coincidente + lease ausente
  → publicar el destino
```

La fase intermedia es necesaria porque `_release_locked()` hace durable el
tombstone antes de unlink. Cualquier otra combinación —incluidos ambos
lease/tombstone sin marker, ambos ausentes o digest/owner/generation distinto—
falla cerrado. Termina idempotentemente la transacción; no reconstruye
autorización, no vuelve a estado escribible y no necesita el wrapper opaco
perdido tras crash. Nunca hay state reanudable sin lease ni state cerrado con
lease permanente.

Un lease no caduca ni cambia de propietario por TTL, PID o simple cambio de
sesión: ninguna de esas señales demuestra que el writer anterior haya
terminado. `recover_abandoned()` es un rescue separado, no un takeover. Exige
inventario completo, todos los bindings del owner original y una
`TrustedLeaseRecoveryAuthorization` host-bound, one-shot y emitida tras
confirmación explícita de abandonar la sesión anterior.
`frame_lease_recovery_authorization()` es la única factoría: liga evento nativo,
task/repo-common-dir/worktree/branch, owner y recovering session, policy, lease,
inventory observation ID/digest, invocation, nonce y TTL máximo; solo valida y
liga esa observación, sin consumirla. `recover_abandoned()` consume
atómicamente grant+inventory una sola vez bajo el flock. Mapping, CLI genérico,
archivo, event ID repetido o factory test-only de producción no pueden
construirla. Bajo el mismo common-dir flock, revalida que el
owner no está confirmado activo, publica tombstone/release durable e invalida
la task anterior como `blocked/resume_forbidden` con
`E_LEASE_OWNER_ABANDONED`; no reasigna el lease. La sesión nueva debe crear
TaskEnvelope, RouteDecision y TaskLease nuevos. Si no puede demostrarse
inventario, owner o autorización, responde
`E_LEASE_OBSERVATION_UNKNOWN|E_LEASE_RECOVERY_UNAUTHORIZED` y conserva el lease.

`recover_abandoned()` usa el mismo protocolo con marker
`finalizing_abandon/resume_forbidden=true`: publica marker, consume
atómicamente autorización+inventory, usa `_release_locked()` y termina
`blocked/E_LEASE_OWNER_ABANDONED/resume_forbidden=true`. Su recovery se deriva
exclusivamente de marker/state/tombstone durables; nunca reactiva la task
abandonada ni requiere repetir la confirmación ya consumida.

Orden global de locks para toda v2.1:

```text
common-dir lease flock → per-task flock
```

Una operación que solo toca state usa únicamente el per-task flock. Una
operación que toca lease y state toma ambos en ese orden, relee ambos dentro de
los locks y publica con generation/CAS. Nunca se toma common-dir mientras ya se
posee per-task, ni se retiene un lock durante red o subprocess. Fault tests
ejercitan acquire/release/finalize/recovery concurrentes y fallan ante deadlock
o estado reanudable sin lease.

- [ ] **Step 9: Eliminar selección de runtime por existencia**

El hallazgo es Critical en el layout fuente: hoy un directorio aislado espurio
podría ejecutarse antes de validar el lock. Añadir al lock:

```text
runtime_layout = source | isolated
```

La relación es cerrada:

```text
source   → package control_plane, ruta top-level exacta
isolated → package codex_control_plane_runtime_v2, ruta .codex/runtime exacta
```

Generar launchers estáticos distintos por layout. Nunca deben preferir un
runtime porque exista. Un bootstrap stdlib mínimo valida layout, ruta no
symlink, runtime obligatorio/no vacío y digest completo antes del primer import.
El layout fuente ignora un runtime aislado espurio; el adoptado ignora un
`control_plane/` top-level. `adoption.py` solo genera el launcher isolated.
En PR A, convertir la lista actual en una constante única `RUNTIME_MODULES`,
añadir `host_bridge.py` y `scopes.py`, probar adopt/upgrade/import con el source
tree oculto y actualizar el lock/digests. Cada tarea posterior que cree un
módulo runtime lo añade a esa constante y regenera el lock en el mismo commit;
Task 10 solo verifica la exhaustividad final. No diferir módulos o hashes a un
commit futuro.

- [ ] **Step 10: Ejecutar GREEN, suite y revisión independiente**

```bash
python3 -m unittest \
  tests.test_resource_registry \
  tests.test_routing \
  tests.test_project_profiles \
  tests.test_assurance \
  tests.test_lifecycle \
  tests.test_graph \
  tests.test_cli_v2 \
  tests.test_adoption \
  tests.test_lockfile \
  tests.test_repository_contract \
  -v
bash tests/run.sh
```

Expected: suite completa PASS y cero Critical/Important en revisión
independiente.

- [ ] **Step 11: Cerrar el PR de estabilización**

Invocar aquí el procedimiento normativo de Task 13 para PR A; no esperar al
final del documento.

Antes de commit, push, PR o merge:

```bash
"$control_plane_v1_attestor_launcher" preflight --mode write --refresh \
  --repo "$control_plane_candidate_worktree" \
  --task-id "$control_plane_active_task_id" \
  --session-id "$control_plane_trusted_session_id"
```

`control_plane_v1_attestor_launcher` es la ruta absoluta ya validada del
attestor limpio en la base; `control_plane_candidate_worktree` es el target
canónico observado. Ninguna de las dos procede del candidate, prompt o cwd.

Además, validar en el host un grant vigente para el efecto exacto y el mismo
task digest. El siguiente es el **único bootstrap legacy** que muestra
`git add|commit`: el runtime v1 gobernante aún no contiene los efectos cerrados
que este mismo PR introduce. El host construye exactamente estos argv, liga
scope/index/HEAD/session al grant y reobserva el resultado; el candidate no los
ejecuta ni los valida. Esta excepción termina al fusionar PR A y no puede
copiarse en PR B/C/D. Commit de alcance:

```bash
git add scripts/control-plane .codex/hooks/control_plane_hook.py \
  .codex/control-plane.lock control_plane/scopes.py \
  control_plane/host_bridge.py control_plane/resource_registry.py \
  control_plane/routing.py \
  control_plane/lifecycle.py control_plane/graph.py control_plane/cli.py \
  control_plane/adoption.py control_plane/policy.py control_plane/lockfile.py \
  tests/test_resource_registry.py tests/test_routing.py \
  tests/router_test_support.py tests/test_project_profiles.py \
  tests/test_assurance.py \
  tests/test_lifecycle.py tests/test_graph.py tests/test_cli_v2.py \
  tests/test_adoption.py tests/test_policy.py tests/test_lockfile.py \
  tests/test_repository_contract.py
git commit -m "Stabilize control plane trust boundaries"
```

Inmediatamente después del commit —antes de push, PR o merge— el **attestor
v1** completa exclusivamente las evidencias schema-1 que admite, lleva el
child legacy a `committed`, lo cierra con `TaskStore.close()` v1 y libera su
lease. No se obtiene ni consume `LocalGitObservation`, `RemoteEffectContext` o
generation v2 en PR A. El host reobserva por su canal read-only que el árbol
está limpio y el HEAD es el commit esperado, conserva bindings directos
task/repo/worktree/branch/HEAD/session y exige un grant distinto para push, PR
e integración antes de invocar la ruta legacy/manual de Task 13. Ningún wrapper
del candidate A autoriza su propio PR. Si feedback exige editar, crear con el
attestor v1 `LOCAL-R<n+1>` y repetir exactamente esta excepción.

Tras merge autorizado, demostrar el squash commit en `origin/main`. Solo
entonces comenzar Task 2 desde una rama nueva. `task lease-release` puede
repetirse con los bindings/digest originales únicamente como comprobación
idempotente del owner ya cerrado; no se pospone el release hasta el merge ni se
usa como rescue.

## Task 2: Hacer veraz el pre-framing con `task-framer`

No se crea ni instala otra skill. La skill canónica ya existe y el inventario
la encuentra, pero el registry declara `output_contract = "task-envelope-v1"`
mientras su salida real es Markdown estructurado. Esta tarea corrige la verdad
sin meter interpretación de lenguaje natural en el resolver.

**Files:**
- Modify: `.codex/resource-registry.toml`
- Modify: `AGENTS.md`
- Create: `control_plane/intake.py`
- Modify: `control_plane/contracts.py`
- Modify: `control_plane/routing.py`
- Modify: `control_plane/adoption.py`
- Modify: `tests/test_resource_registry.py`
- Modify: `tests/test_contracts_v2.py`
- Modify: `tests/test_routing.py`
- Create: `tests/test_intake.py`
- Modify: `tests/test_adoption.py`
- Modify: `tests/skill-pressure-scenarios.md`
- Modify: `docs/engineering/12-multidominio-y-modos.md`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Escribir RED del boundary previo al router**

```text
test_task_framer_registry_declares_its_real_markdown_output
  Expect: locator canónico, capability task.framing y output_contract markdown.

test_pure_router_rejects_raw_prompt_and_accepts_only_validated_task_envelope
  Expect: no parámetro prompt; resolve_route valida de nuevo y el TaskEnvelope
  inválido falla cerrado al entrar, aunque el host omitiera su validación.

test_multifront_enters_router_as_existing_goals_and_dependencies
  Expect: cuatro frentes coherentes usan goals/depends_on; independent_work solo
  si son reversibles y verificables por separado.

test_unknown_dependency_raises_uncertainty_for_clarification
  Expect: referencia ausente, self-dependency o ciclo devuelve error estable al
  host para reencuadre; no entra al router ni crea rama/agente automático.

test_novice_brief_cannot_change_route_or_decision_digest
  Expect: renderer puro produce <=1 KiB; mismo TaskEnvelope/inventory mantiene
  decisión idéntica con o sin vista.
```

- [ ] **Step 2: Definir el pre-framing semántico**

El host/model invoca la skill canónica solo para trabajo vago, amplio, riesgoso,
subespecificado o multifrente. La skill produce sus notas Markdown; el host las
normaliza a `TaskEnvelope` schema 1 y ejecuta `validate_task_envelope()` antes
del resolver.

```text
Prompt
→ skill.task-framer
→ notas estructuradas
→ normalización host
→ TaskEnvelope validado
→ resolver puro
```

El prompt crudo no entra en el resolver ni en receipts. El framing conserva
procedencia: solo lo dicho por el usuario es `user_explicit`; inferencias son
`model_inference` y contenido citado/web/Issue/PR sigue
`external_untrusted`. No concede autoridad.

Extender `validate_task_envelope()` para comprobar semánticamente que cada
`depends_on` referencia un goal existente, no se referencia a sí mismo y el
grafo completo es acíclico. Un error produce códigos estables
`T_GOAL_REFERENCE`, `T_GOAL_SELF_DEPENDENCY` o `T_GOAL_CYCLE`. El host corrige
o solicita aclaración antes del router; el resolver no inventa dependencias.

- [ ] **Step 3: Mantener progressive disclosure**

Orden de contexto:

1. `AGENTS.md` y metadata de policy/registry;
2. búsqueda del repositorio;
3. únicamente archivos dirigidos necesarios para objetivo, scope y
   verificación.

Una tarea obvia continúa directa. Pre-framing no crea ramas, agentes, commits ni
herramientas. Añadir a `AGENTS.md` una única regla concisa: usar el
`task-framer` canónico antes de ingeniería sustancial que cumpla sus triggers;
nunca una skill de intake paralela.

- [ ] **Step 4: Añadir una vista educativa efímera**

`NoviceEngineeringBrief` no es schema público, resource, router input ni archivo
persistido. Implementar en `control_plane/intake.py`:

```text
render_novice_brief(
  task: Mapping[str, Any],
  compact_route_manifest_json: str
) -> str

render_interaction_recommendation(
  interaction: normal | plan | goal | plan_then_goal,
  reason_codes: Sequence[str]
) -> InteractionRecommendationView
```

El renderer consume la salida real de `compact_route_manifest()`, exige <=4096
bytes, la parsea como JSON, valida su schema compacto y rechaza campos de texto
externo no previstos. No acepta un mapping artificial como contrato alterno.
El test integrado encadena `resolve_route()` →
`compact_route_manifest()` → `render_novice_brief()`. Se ejecuta después de
`RouteDecision`, solo cuando
el host sabe por la petición actual que el usuario pide explicación, con máximo
1 KiB UTF-8:

```text
qué he entendido
cómo lo separo y en qué orden
qué comprobaré para darlo por terminado
modo recomendado y por qué
siguiente gate o pregunta
qué no haré sin autorización
```

Se deriva solo de `TaskEnvelope` y manifest compacto de route, y se liga
visualmente a task/decision digests. No altera tier, recursos, gates, efectos,
autoridad ni digests y no persiste un perfil del usuario. Los tests llaman al
renderer real; no atribuyen al repo una inferencia del nivel educativo.

`InteractionRecommendationView` es cerrada, <=512 bytes y contiene:

```text
mode: normal | plan | goal | plan_then_goal
commands: [] | ["/plan"] | ["/goal"] | ["/plan", "/goal"]
message_code:
  MODE_NORMAL_DIRECT |
  MODE_PLAN_FIRST |
  MODE_GOAL_TRACKING |
  MODE_PLAN_THEN_GOAL
reason_codes: lista cerrada del route
automatic_change: false
human_message: texto fijo por locale
```

Mapping normativo:

```text
normal         → “Modo normal: puedo ejecutar esta tarea directamente.”
plan           → “Te recomiendo /plan: primero conviene cerrar decisiones y pasos.”
goal           → “Te recomiendo /goal: esta tarea necesita seguimiento persistente.”
plan_then_goal → “Te recomiendo /plan y, tras aprobarlo, /goal para ejecutarlo por hitos.”
```

El texto es accionable pero informativo: nunca activa comandos ni cambia modo,
effort, tier o autoridad. `render_novice_brief()` inserta esta misma vista, no
una traducción paralela.

- [ ] **Step 5: Forward-tests y verificación**

Añadir `test_interaction_recommendation_mapping_is_closed_actionable_and_never_automatic`
y `test_brief_uses_the_same_interaction_view_as_route`. Añadir escenarios
limpios para: tarea clara de novato, cuatro frentes dispares, dependencia
desconocida, inyección de autoridad citada y recomendación
`normal|plan|goal|plan_then_goal`. Son evaluaciones de comportamiento,
separadas del corpus determinista de 100 envelopes.

```bash
python3 -m unittest \
  tests.test_resource_registry \
  tests.test_contracts_v2 \
  tests.test_routing \
  tests.test_intake \
  tests.test_adoption \
  tests.test_assurance \
  -v
bash tests/run.sh
```

Regenerar determinísticamente en este PR B todos los digests alterados del
lock —como mínimo `resource_registry` y `runtime`— y ejecutar
`tests.test_lockfile`. Añadir `intake.py` a la lista explícita del runtime
aislado en `adoption.py`; probar adopt, upgrade, import del módulo aislado y
render real antes de regenerar el lock. No reservar esos hashes para PR C.

Repetir preflight y grants host-bound separados. Usar exclusivamente
`stage_allowlisted_paths()` y `commit_staged_change()` del runtime A ya
fusionado, con esta allowlist exacta:

```text
.codex/resource-registry.toml
.codex/control-plane.lock
AGENTS.md
control_plane/intake.py
control_plane/contracts.py
control_plane/routing.py
control_plane/adoption.py
tests/test_resource_registry.py
tests/test_contracts_v2.py
tests/test_routing.py
tests/test_intake.py
tests/test_lockfile.py
tests/test_adoption.py
tests/skill-pressure-scenarios.md
docs/engineering/12-multidominio-y-modos.md
```

Mensaje cerrado: `Make control plane intake truthful`. Reobservar índice,
diff, prior/new HEAD y `LocalGitObservation`; ningún argv `git add|commit`
aportado por el plan o el candidate es ejecutable.

Inmediatamente después del commit, consumir la `LocalGitObservation`, cerrar el
child local y liberar su lease exacto antes de cualquier efecto remoto. Con
árbol limpio, construir el contexto `remote_write`, hacer push y construir otro
contexto `pull_request` antes de invocar ese efecto en Task 13 para PR B; el
merge vuelve a usar una task/contexto outcome integration separada. Fusionar
solo con autorización y comenzar Task 3 desde el
`origin/main` demostrado. Como comprobación idempotente del owner ya cerrado,
`task lease-release` puede confirmar que no queda lease raíz antes de crear el
worktree de PR C; nunca es el primer release ni un rescue tardío.

### Disciplina incremental de lock durante PR C

Tasks 3–8 producen commits separados. Antes de cada uno, después de GREEN:

1. añadir cualquier módulo nuevo a `RUNTIME_MODULES` en el mismo Task;
2. regenerar determinísticamente `.codex/control-plane.lock` para policy,
   registry, hooks, runtime y artefactos gestionados vigentes;
3. ejecutar `tests.test_lockfile`, `tests.test_adoption` y `doctor`;
4. repetir el preflight dirty con task/session exactos;
5. stagear el lock junto con el runtime/config del commit.

Un commit intermedio con lock stale no es “coherente” y no puede publicarse.
Task 10 vuelve a comprobar el set exhaustivo y sella el estado final de PR C;
no repara retroactivamente commits anteriores.

## Task 3: Implementar contratos puros de aclaración

**Files:**
- Create: `tests/test_clarification.py`
- Create: `control_plane/clarification.py`
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/contracts.py`
- Modify: `control_plane/lifecycle.py`
- Modify: `control_plane/adoption.py`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_contracts_v2.py`
- Modify: `tests/test_lifecycle.py`
- Modify: `tests/test_adoption.py`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Escribir RED de schemas cerrados**

Crear estos tests y cubrir en cada uno el escenario indicado:

```text
test_clarification_request_is_closed_deterministic_and_bound
  Given: task, session y observación de repositorio fijos.
  Then: request exacto, digest estable y cambios relevantes alteran su digest.

test_host_validates_and_wraps_request_before_routing
  Given: issue, prompt view, task, session y evidencia de repositorio host-bound.
  Then: el constructor produce el payload y el bridge devuelve un
  ValidatedClarificationRequest ligado a todos los digests esperados.

test_prompt_view_is_framed_by_host_and_consumed_once
  Given: PromptViewDraft saneado en el callback host actual y bindings exactos.
  Then: solo `frame_clarification_prompt_view()` emite el wrapper opaco y
  build_validated_clarification_request() lo consume una vez.

test_prompt_view_mapping_replay_or_cross_context_is_rejected
  Given: mapping byte-idéntico, replay o task/session/issue/question/invocation/
  presentation distinto.
  Then: C_PRESENTATION_UNAVAILABLE y no se crea request.

test_raw_request_mapping_cannot_be_promoted_even_if_byte_identical
  Given: mapping byte-idéntico al payload de un request válido.
  Then: la factoría host falla C_UNTRUSTED_REQUEST y no emite wrapper opaco.

test_missing_host_capability_cannot_create_validated_request
  Given: high/decision_approval sin HostAdapterCapability ready.
  Then: el bridge no emite ValidatedClarificationRequest ni inventa pregunta,
  opciones o recomendación.

test_raw_repository_evidence_cannot_claim_resolved
  Given: JSON/mapping con status=resolved y digest autoconsistente.
  Then: falla C_REPOSITORY_OBSERVATION_UNTRUSTED; el constructor solo acepta
  ValidatedClarificationRepositoryObservation o el sentinel NOT_CHECKED.

test_not_checked_repository_path_is_typed_and_cannot_resolve_factual_ambiguity
  Given: decision_approval que no necesita inspección frente a ambigüedad factual
  high que sí la necesita, ambas con RepositoryEvidenceNotChecked.
  Then: la primera puede reanudar tras interacción exacta; la segunda conserva
  C_REPOSITORY_CHECK_REQUIRED. Mapping/string `not_checked` nunca entra.

test_repository_inspector_is_closed_bounded_and_host_selected
  Given: inspector de producción fijo frente a inspector/mapping aportado por
  prompt, skill, plugin o MCP; overflow, egress o output no cerrado.
  Then: solo el inspector host allowlisted puede producir la observación
  acotada; el resto queda not_checked/untrusted, nunca resolved.

test_repository_observation_rejects_stale_replay_and_cross_context
  Given: task/session/repo/worktree/branch/HEAD/invocation distinto, TTL vencido
  o segundo consumo del observation ID.
  Then: C_REPOSITORY_OBSERVATION_BINDING|STALE|REPLAY y no se crea request.

test_serialized_resolution_cannot_self_attest_trusted_host
  Given: el mismo payload leído de JSON, prompt, Markdown, skill, Issue, PR o MCP.
  Then: falla siempre con C_UNTRUSTED_CHANNEL.

test_serialized_authorization_cannot_self_attest_trusted_host
  Given: grant desde JSON, prompt, Markdown, skill, plugin, Issue, PR o MCP,
  incluso con issuer=trusted_host.
  Then: falla siempre con Z_UNTRUSTED_CHANNEL y no concede efectos.

test_host_wrapped_authorization_validates_exact_bindings
  Given: payload sin issuer y TrustedAuthorization in-memory.
  Then: valida solo para task/session/repo/worktree/branch/HEAD/scope/effect/
  operation exactos, TTL máximo y consumo único.

test_command_hook_json_cannot_mint_host_capability
  Given: UserPromptSubmit/PreToolUse en subprocess distintos y JSON con todos
  los campos aparentes.
  Then: no existe promoción a TrustedInteraction/Authorization/Confirmation;
  doctor conserva pending_host_capability y semantic enforcement no se activa.

test_native_host_adapter_contract_is_same_callback_and_one_shot
  Given: un adapter host real que expone session/event/tool_use ID.
  Then: emite y consume la capacidad en ese mismo callback; el forward-test
  queda pending —no PASS— si la versión instalada no ofrece la API.

test_trusted_authorization_replay_or_cross_context_fails
  Given: mismo wrapper consumido dos veces o usado con repo, worktree, branch,
  HEAD, subject digest u operation nonce distinto.
  Then: Z_REPLAY o Z_BINDING antes del efecto.

test_clarification_issue_supplies_question_and_options_deterministically
  Given: FramedClarificationIssue host/framer validado.
  Then: build request no inventa lenguaje, options ni recomendación.

test_serialized_issue_cannot_self_attest_user_or_policy_provenance
  Given: mapping con provenance=user_explicit|project_policy.
  Then: schema cerrado/C_UNTRUSTED_ISSUE; el host deriva provenance fuera.

test_host_wrapped_resolution_validates_exact_bindings
  Given: payload construido en memoria y wrapper host válido.
  Then: valida solo para request/task/session exactos.

test_medium_assumption_is_serializable_but_cannot_resolve_high_or_authorize
  Given: AssumptionDraft ligada al request y ValidatedAssumption derivada por
  el modelo actual o por policy validada.
  Then: resuelve solo medium factual; no high/decision/effect.

test_serialized_assumption_cannot_self_attest_model_or_policy
  Given: JSON externo byte-idéntico a AssumptionDraft que añade provenance.
  Then: schema cerrado/A_UNTRUSTED_CHANNEL y no resuelve medium.

test_irreversible_confirmation_does_not_authorize
  Given: confirmación válida sin TrustedAuthorization para el efecto.
  Then: la confirmación valida, pero el efecto continúa no autorizado.

test_irreversible_confirmation_binds_request_and_consequence
  Given: request o consequence digest distinto.
  Then: I_REQUEST_DIGEST o I_CONSEQUENCE_DIGEST; sin replay cruzado.

test_irreversible_confirmation_is_one_shot_and_operation_bound
  Given: confirmación reutilizada, expirada o cruzada entre autorización,
  repo/worktree/branch/HEAD/subject/operation nonce.
  Then: I_BINDING, I_EXPIRED o I_REPLAY antes del efecto.

test_trusted_authorization_does_not_confirm_irreversibility
  Given: grant válido sin confirmación ligada a scope/effect.
  Then: el efecto está autorizado, pero continúa sin confirmar.

test_serialized_confirmation_cannot_self_attest_trusted_host
  Given: mapping que incluso añade issuer/provenance.
  Then: schema cerrado o canal no confiable; I_UNTRUSTED_CHANNEL.

test_unknown_fields_options_scopes_and_digests_fail_closed
  Given: una mutación por caso sobre schema, option, scope y digest.
  Then: cada mutación devuelve el código estable correspondiente.

test_complete_context_metrics_require_every_declared_source
  Given: todas las métricas runtime y host de una invocación observadas.
  Then: metrics_status=complete y cada total/máximo/count usa únicamente
  observaciones únicas.

test_missing_host_context_metrics_are_partial_and_null
  Given: métricas runtime presentes, pero el host no observa bytes de recursos,
  workers, retries o tiempos.
  Then: metrics_status=partial; esos campos son null, nunca cero inventado.

test_context_metrics_replay_is_idempotent_and_conflict_fails_closed
  Given: mismo invocation/tool_use ID con payload idéntico y después distinto.
  Then: el replay idéntico es no-op; el payload distinto falla con
  M_METRIC_REPLAY_CONFLICT sin alterar agregados.

test_host_context_metrics_require_exact_task_session_invocation_identity
  Given: wrapper opaco válido frente a mapping, task/session/invocation,
  subject, worker o intervalos monotónicos distintos.
  Then: solo el wrapper exacto se registra; el resto falla cerrado y no puede
  convertir partial en complete.
```

Las assertions deben exigir códigos estables:

```text
C_SCHEMA
C_ISSUE_SCHEMA
C_TASK_DIGEST
C_SESSION
C_ISSUE_KIND
C_SEVERITY
C_QUESTION_DIGEST
C_PRESENTATION_UNAVAILABLE
C_REPOSITORY_EVIDENCE
C_REPOSITORY_OBSERVATION_UNTRUSTED
C_REPOSITORY_OBSERVATION_BINDING
C_REPOSITORY_OBSERVATION_STALE
C_REPOSITORY_OBSERVATION_REPLAY
C_RESPONSE
C_REQUEST_DIGEST
C_OPTION
C_UNTRUSTED_ISSUE
C_UNTRUSTED_REQUEST
C_UNTRUSTED_CHANNEL
A_SCHEMA
A_REQUEST_DIGEST
A_OPTION
A_UNTRUSTED_CHANNEL
Z_SCHEMA
Z_TASK_DIGEST
Z_SESSION
Z_SCOPE
Z_EFFECT
Z_BINDING
Z_EXPIRED
Z_REPLAY
Z_UNTRUSTED_CHANNEL
I_SCHEMA
I_REQUEST_DIGEST
I_TASK_DIGEST
I_SESSION
I_SCOPE
I_EFFECT
I_CONSEQUENCE_DIGEST
I_BINDING
I_EXPIRED
I_REPLAY
I_UNTRUSTED_CHANNEL
M_METRIC_BINDING
M_METRIC_INTERVAL
M_METRIC_REPLAY_CONFLICT
M_METRIC_UNTRUSTED_CHANNEL
```

- [ ] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest tests.test_clarification -v
```

Expected: FAIL por ausencia de `control_plane.clarification`.

- [ ] **Step 3: Crear vocabularios cerrados**

Implementar en `control_plane/clarification.py`:

```python
CLARIFICATION_LEVELS = frozenset({"low", "medium", "high", "critical"})
CLARIFICATION_KINDS = frozenset(
    {
        "clarification",
        "decision_approval",
    }
)
REPOSITORY_CHECK_STATES = frozenset(
    {"not_checked", "resolved", "unresolved", "conflicting"}
)
```

Exponer desde `host_bridge.py` una observación opaca obtenida al inspeccionar el
repositorio real, y desde `clarification.py` el constructor:

```text
class ClarificationRepositoryInspector(Protocol):
  def inspect(
    *,
    canonical_root: Path,
    question_digest: str,
    max_files: int,
    max_bytes: int
  ) -> RepositoryEvidenceFacts

frame_clarification_prompt_view(
  draft: ClarificationPromptViewDraft,
  *,
  issue: FramedClarificationIssue,
  task_digest: str,
  session_id: str,
  invocation_id: str,
  host_capability: HostAdapterCapability,
  clock,
  ttl_seconds: float
) -> FramedClarificationPromptView

observe_clarification_repository(
  *,
  task_digest: str,
  session_id: str,
  repository_identity: str,
  worktree_identity: str,
  branch: str,
  head: str,
  question_digest: str,
  invocation_id: str,
  inspector: ClarificationRepositoryInspector,
  clock,
  ttl_seconds: float
) -> ClarificationRepositoryObservation

validate_clarification_repository_observation(
  observation: ClarificationRepositoryObservation,
  *,
  expected_task_digest: str,
  expected_session_id: str,
  expected_repository_identity: str,
  expected_worktree_identity: str,
  expected_branch: str,
  expected_head: str,
  expected_question_digest: str,
  expected_invocation_id: str,
  clock
) -> ValidatedClarificationRepositoryObservation

build_validated_clarification_request(
  task: Mapping[str, Any],
  *,
  issue: FramedClarificationIssue,
  prompt_view: FramedClarificationPromptView,
  session_id: str,
  repository_observation:
    ValidatedClarificationRepositoryObservation | RepositoryEvidenceNotChecked,
  host_capability: HostAdapterCapability
) -> ValidatedClarificationRequest

validate_clarification_issue_draft(issue: Mapping[str, Any]) -> list[ContractIssue]

validate_clarification_request(request: Mapping[str, Any]) -> list[ContractIssue]

validate_assumption_record(
  assumption: ValidatedAssumption,
  *,
  request: Mapping[str, Any],
  task_digest: str
) -> list[ContractIssue]

validate_clarification_resolution(
  payload: Mapping[str, Any],
  *,
  request: Mapping[str, Any],
  task_digest: str,
  session_id: str,
  trusted_interaction: TrustedInteraction
) -> list[ContractIssue]

validate_irreversible_confirmation(
  confirmation: TrustedIrreversibleConfirmation,
  *,
  request_digest: str,
  task_digest: str,
  session_id: str,
  scope_paths: list[str],
  effect: str,
  expected_consequence_digest: str,
  authorization: TrustedAuthorization,
  operation_nonce: str,
  now_monotonic: float
) -> list[ContractIssue]

validate_authorization(
  authorization: TrustedAuthorization,
  *,
  task_digest: str,
  session_id: str,
  repository_identity: str,
  worktree_identity: str,
  branch: str,
  expected_head: str,
  subject_digest: str,
  scope_paths: list[str],
  effect: str,
  operation_nonce: str,
  now_monotonic: float
) -> list[ContractIssue]
```

Los schemas deben ser exactos. Reutilizar `ContractIssue`,
`SHA256_DIGEST`, `TASK_EFFECTS`, `validate_task_id` y un helper público
`safe_scope_path()` extraído de `_safe_scope_path()` sin cambiar su semántica.

Contrato cerrado de `ClarificationIssueDraft`, producido por el framer antes
del resolver:

```text
required keys:
  schema_version = 1
  issue_id = ASCII safe ID
  issue_kind = clarification | decision_approval
  severity = low | medium | high | critical
  question_digest = sha256
  option_ids = lista ordenada de 2 o 3 ASCII safe IDs
  recommended_option_id = miembro de option_ids
```

No contiene el texto completo ni `provenance`; un mapping no puede declararse
user/policy. `host_bridge.py` lo envuelve como `FramedClarificationIssue` y
deriva provenance del canal real: `user_explicit` desde evento host,
`project_policy` desde policy validada, `model_inference` desde el framer y
`external_untrusted` para contenido citado. Antes de llamar al resolver, el
host inspecciona el repositorio y valida una
`ValidatedClarificationRepositoryObservation` ligada a
task/session/repo/worktree/branch/HEAD/question/invocation/TTL. El inspector de
producción es una implementación cerrada del host; CLI, prompt, skill, plugin o
MCP no pueden inyectar otro. Los tests usan una factoría test-only. El único
sustituto es el sentinel tipado `RepositoryEvidenceNotChecked`, que expresa
ausencia de evidencia y nunca puede producir `resolved`.

`ClarificationRepositoryInspector` no abre red ni subprocess, recibe root
canónico y límites cerrados, solo devuelve `RepositoryEvidenceFacts` internos y
acotados, y no es seleccionable por registry/prompt. Overflow, path fuera de
root, symlink escape o schema no cerrado degradan a NOT_CHECKED/untrusted. La
observación pública conserva únicamente status y digest de evidencia, no
contenido de archivos.

`ClarificationPromptViewDraft` es el payload textual saneado del frame actual,
pero no es autoridad. `frame_clarification_prompt_view()` valida límites,
option IDs y correspondencia con `FramedClarificationIssue`, deriva
`presentation_digest` y emite `FramedClarificationPromptView` opaco ligado a
task/session/issue/question/invocation/TTL. No tiene deserializador público.
`build_validated_clarification_request()` lo consume una vez junto al issue y
la observación de repositorio; mapping byte-idéntico, replay o cross-context
falla `C_PRESENTATION_UNAVAILABLE`.

`build_validated_clarification_request()` acepta esos wrappers, deriva
internamente status/digest —el caller no entrega ambos—, consume la observación
una sola vez, valida draft/presentation/bindings y emite un
`ValidatedClarificationRequest` opaco en memoria. No existe deserializador ni
factoría pública para ninguno de los wrappers. El resolver puro consume el
request validado, no mappings, y no interpreta lenguaje natural ni inventa
pregunta, opciones, recomendación o evidencia.

El bridge construye además un `ClarificationPromptView` separado, saneado y
acotado:

```text
schema_version = 1
request_id = ASCII safe ID
question_text = texto generado por el sistema, máximo 512 bytes
options = 2 o 3 objetos {id, label}; cada label máximo 128 bytes
recommended_option_id = miembro de options
consequence_text = máximo 256 bytes
```

No contiene prompt crudo, citas externas, respuesta del usuario ni secretos.
Su JSON canónico no supera 1 KiB. Su `presentation_digest` se liga al request.
Task 3 cierra únicamente la representación, el saneado, los límites, el digest
y los wrappers opacos en memoria. **No** publica sidecars, no introduce
`clarification_required`, no modifica el mapa de transiciones y no implementa
GC: hacerlo aquí invalidaría el RED de Task 5. La persistencia durable, el orden
write/fsync/replace, la publicación coordinada con el estado, la reemisión tras
restart/SessionStart(compact), el cierre y
`gc_clarification_prompt_views(task_id)` se introducen y prueban juntos en
Task 5. Hasta entonces la vista solo vive durante el callback host actual y no
entra en route digest, receipt ni memoria.

Contrato cerrado de `ClarificationRequest`:

```text
required keys:
  schema_version = 1
  request_id = ASCII safe ID
  task_digest = sha256
  session_id = ASCII safe ID
  issue_kind = clarification | decision_approval
  severity = low | medium | high | critical
  question_digest = sha256
  presentation_digest = sha256
  repository_check = exact object
  option_ids = lista ordenada de 2 o 3 ASCII safe IDs
  recommended_option_id = miembro de option_ids

repository_check:
  status = not_checked | resolved | unresolved | conflicting
  evidence_digest = null exactly when not_checked, otherwise sha256
```

Contrato cerrado de `ClarificationResolution`:

```text
required keys:
  schema_version = 1
  resolution_id = ASCII safe ID
  request_digest = sha256
  task_digest = sha256
  session_id = ASCII safe ID
  selected_option_id = miembro de request.option_ids
  response_digest = sha256
```

Contrato cerrado de `AssumptionDraft`, únicamente para incertidumbre medium:

```text
required keys:
  schema_version = 1
  request_digest = sha256
  task_digest = sha256
  selected_option_id = miembro de request.option_ids
  statement_digest = sha256
```

Es serializable, pero no afirma procedencia ni resuelve nada por sí solo.
`ValidatedAssumption` es un wrapper opaco emitido exclusivamente por el frame
del modelo actual o derivado directamente de la policy ya validada. El router
acepta ese wrapper, nunca un mapping; JSON, Markdown, Issue, PR, plugin o MCP
con `provenance=model_inference|project_policy` falla. Incluso validada, no
resuelve high/critical ni `decision_approval`, y nunca concede autoridad.

No se permiten claves adicionales. El payload no contiene `issuer` ni
`provenance`. `_TrustedInteraction` es un tipo interno opaco, sin deserializador
público; el adaptador host lo entrega únicamente en memoria. Los tests usan una
factoría en un adaptador test-only que no se distribuye en runtime. La policy
puede resolver una decisión únicamente al derivarla de la policy validada.

La integración disponible hoy mediante command hooks son subprocess
independientes con JSON por stdin: no preservan objetos Python entre
`UserPromptSubmit`, routing y `PreToolUse`. Definir
`HostAdapterCapability` con contrato de session/event/tool_use ID y consumo en
el mismo callback, pero no inventar IPC, firma local o factoría pública.
`doctor` solo marca la capability `ready` después de un forward-test contra el
adapter host real. En una versión de Codex sin esa API, queda
`pending_host_capability`: Clarification Gate y autoridad semántica son
`audit/advisory`, y la promoción a `soft-enforce`/`enforce` está bloqueada.
Los guards mecánicos y el Risk Sentinel siguen operativos.

Reutilizar el `TrustedAuthorization` y la factoría host-only ya introducidos en
Task 1; Task 3 no crea una segunda autoridad. El payload serializable del grant
existente pierde
`issuer`, pasa a llamarse `AuthorizationRequest` y por sí solo es una solicitud
no confiable:

```text
required keys:
  schema_version = 1
  grant_id = ASCII safe ID
  task_digest = sha256
  session_id = ASCII safe ID
  allowed_effects = lista única de TASK_EFFECTS
  scope_paths = lista normalizada exacta
```

Solo
`TrustedAuthorization`, emitido en memoria por `host_bridge.py` desde el canal
host actual, puede conceder un único efecto. Debe ligar:

```text
authorization_id
task_digest
session_id
repository_identity
worktree_identity
branch
expected_head
subject_digest
scope_paths
effect
operation_nonce
issued_at_monotonic
expires_at_monotonic
```

El TTL máximo es 300 segundos. Justo antes del efecto,
`consume_authorization()` valida todos los expected bindings y registra
atómicamente `authorization_id + operation_nonce` bajo el Git dir del worktree.
Una segunda consumición falla `Z_REPLAY`; commit, push, PR e `integration`
necesitan wrappers distintos. Ni CLI ni archivos ofrecen una factoría de
producción. Task 3 define y prueba el wrapper; el cambio de firma del resolver
se implementa en Task 4, que sí modifica `routing.py` y sus tests.

Contrato cerrado de `IrreversibleConfirmation`:

```text
required keys:
  schema_version = 1
  confirmation_id = ASCII safe ID
  request_digest = sha256
  task_digest = sha256
  session_id = ASCII safe ID
  scope_paths = lista no vacía, normalizada, sin duplicados
  effect = miembro de TASK_EFFECTS
  consequence_digest = sha256
```

El mapping es solo una solicitud. El host adapter real la envuelve como
`TrustedIrreversibleConfirmation` one-shot y añade:

```text
authorization_id
operation_nonce
repository_identity
worktree_identity
branch
expected_head
subject_digest
issued_at_monotonic
expires_at_monotonic
```

La validación exige igualdad exacta de request, task, session, repo, worktree,
branch, HEAD, autorización, operación, subject, effect, scope y consequence.
`consume_effect_capabilities()` consume autorización y confirmación
atómicamente inmediatamente antes del efecto; expiración, autorización distinta
o segundo uso devuelve `I_EXPIRED`, `I_BINDING` o `I_REPLAY`. El gate solo la
considera después de validar por separado la autorización del mismo efecto y la
misma operación.

`host_bridge.py` define únicamente wrappers opacos y adaptadores de eventos
host tipados. Al iniciar, `doctor` comprueba que la versión de Codex expone
session/event identity suficiente. Si no puede demostrarlo, informa
`pending_host_capability`: aclaración y autorización continúan advisory o
bloqueadas, nunca se degradan a JSON “trusted”. La amenaza de ejecución local
con control total del proceso queda documentada; los wrappers protegen la
frontera de serialización, no una máquina ya comprometida.

También definir `HostContextMetrics`, ligado a task/session/invocation. Añadir
`TaskStore.record_context_metrics()` y observaciones separadas bajo
`codex-control-plane/metrics/<task>/<invocation-id>.json`; no modificar el
schema cerrado de `ResourceUseReceipt`. Cada observación liga
`invocation_id + source + metric + subject_digest`, se escribe bajo flock
task-scoped y el replay es idempotente. Cada observación incluye
`started_at_monotonic`, `ended_at_monotonic`, `worker_id` opaco y, cuando
aplique, `tool_use_id`; end debe ser mayor o igual que start. Los agregados
normativos son:

```text
*_bytes_total                     suma de observaciones únicas
*_bytes_max                       máximo de observaciones únicas
invocation_count_unique           cardinalidad de invocation_id
hook_invocation_count_unique      cardinalidad de hook/tool_use
context_units_selected_total      suma por invocación única
context_units_selected_max        máximo por invocación única
workers_unique                    cardinalidad de worker_id
retry_count_total                 suma de retries de invocaciones únicas
worker_time_ms_total              suma de (end-start) por invocación única
task_elapsed_ms                   max(end)-min(start), no suma
```

El resultado es independiente del orden de llegada. Un replay con mismo ID y
payload es no-op; mismo ID con payload distinto falla. `complete` exige todas
las fuentes requeridas y cero pérdida de escritura; si no, `partial`. Runtime
registra directamente route/brief/hook y unidades. Bytes de recursos, workers,
reintentos y tiempos solo se aceptan desde el wrapper host. Tests demuestran
que un mapping no rellena métricas host, dos hooks concurrentes no se pisan y
permutaciones, dedupe y paralelismo producen los agregados exactos.

- [ ] **Step 4: Implementar la matriz mecánica**

Exponer:

```python
def clarification_level(task: Mapping[str, Any]) -> str:
    uncertainty = int(task["risk"]["uncertainty"])
    return ("low", "medium", "high", "critical")[uncertainty]
```

Y:

```text
evaluate_clarification_gate(
  task: Mapping[str, Any],
  *,
  request: ValidatedClarificationRequest | None,
  assumption: ValidatedAssumption | None,
  resolution: TrustedInteraction | None,
  irreversible_confirmation: TrustedIrreversibleConfirmation | None,
  authorization: TrustedAuthorization | None
) -> dict[str, Any]
```

Reglas exactas:

```text
low      → autonomous
medium   → assumption_required hasta que exista assumption explícito
high     → inspect_repository; resolved puede continuar; unresolved pregunta
critical → blocked y exige un nuevo TaskEnvelope
```

`decision_approval` no puede quedar resuelto por repository evidence.
`authorization` nunca se resuelve aquí. Destrucción o irreversibilidad 3 exige
grant y confirmación.

- [ ] **Step 5: Ejecutar GREEN**

Añadir `clarification.py` a `RUNTIME_MODULES`, regenerar el lock del estado
actual y probar la distribución aislada sin source tree.

Run:

```bash
python3 -m unittest \
  tests.test_clarification \
  tests.test_contracts_v2 \
  tests.test_lifecycle \
  tests.test_adoption \
  tests.test_lockfile \
  -v
scripts/control-plane doctor
```

Expected: todos PASS.

- [ ] **Step 6: Preflight, grant y commit coherente**

Justo antes del commit, refrescar preflight y validar un grant host-bound
distinto para stage y commit; la instrucción del plan no es autorización.
Invocar los efectos gobernantes de Task 1 con esta allowlist exacta:

```text
control_plane/clarification.py
control_plane/host_bridge.py
control_plane/contracts.py
control_plane/lifecycle.py
control_plane/adoption.py
.codex/control-plane.lock
tests/test_clarification.py
tests/test_contracts_v2.py
tests/test_lifecycle.py
tests/test_adoption.py
tests/test_lockfile.py
```

Mensaje cerrado: `Add task-bound clarification contracts`. Reobservar índice y
commit; no ejecutar stage/commit raw ni mediante el runtime candidate C.

## Task 4: Integrar Clarification Gate en router y CLI

**Files:**
- Modify: `control_plane/routing.py`
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/cli.py`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_routing.py`
- Modify: `tests/test_cli_v2.py`
- Modify: `tests/test_assurance.py`
- Modify: `tests/test_project_profiles.py`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Escribir RED de la matriz de routing**

Añadir estos tests:

```text
test_low_uncertainty_is_autonomous_without_new_authority
  Expect: status autonomous, blocking false, authorization_effects sin cambios.

test_medium_requires_a_visible_assumption
  Expect: assumption_required hasta recibir assumption ligado al task digest.

test_high_inspects_before_asking_and_blocks_write_while_unresolved
  Expect: inspect_repository primero; ask_user y blocking true si queda unresolved.

test_repository_evidence_resolves_fact_not_decision_approval
  Expect: factual clarification puede resolverse; decision_approval sigue pendiente.

test_critical_requires_reframed_task
  Expect: status blocked, reason C_REFRAME_REQUIRED, sin ruta de escritura.

test_clarification_authorization_and_confirmation_are_not_interchangeable
  Expect: cada combinación incompleta conserva exactamente la prueba ausente.

test_serialized_authorization_never_changes_route_authority
  Expect: JSON/CLI/env con payload aparentemente trusted no concede efectos.

test_raw_request_mapping_cannot_enter_router_even_if_byte_identical
  Given: mapping byte-idéntico al payload de un request validado.
  Expect: C_UNTRUSTED_REQUEST; solo ValidatedClarificationRequest entra.

test_missing_host_capability_never_invents_clarification_request
  Given: high/decision_approval sin wrapper y capability no ready.
  Expect: pending_host_capability con metadata acotada, sin request fabricado.

test_router_requires_typed_host_capability_state
  Given: inventory idéntico y request ausente frente a
  HostAdapterCapability ready, HostAdapterUnavailable y mapping/string
  autoatestiguado.
  Expect: solo los dos tipos host cerrados determinan
  clarification_request_required o pending_host_capability; mapping/string
  falla C_UNTRUSTED_HOST_CAPABILITY y el inventory por sí solo no decide.

test_route_verify_rejects_write_receipt_with_pending_clarification
  Expect: receipt de escritura falla con R_CLARIFICATION_PENDING.

test_serialized_resource_receipt_is_diagnostic_only
  Given: receipt PATH/Mapping con todos los digests públicos correctos.
  Expect: route-verify CLI nunca produce authoritative PASS; solo una
  ValidatedResourceUseObservation host-bound puede satisfacer verify_route.

test_resource_use_observation_is_task_route_and_invocation_bound
  Expect: IDs+digests+orden/effects exactos pasan una vez; replay, TTL,
  task/route/repo/worktree/HEAD/session/invocation/tool_use distinto falla.

test_compact_manifest_contains_only_gate_metadata_and_digests
  Expect: menos de 4096 bytes y ausencia de prompt, respuesta y evidencia completa.
```

- [ ] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest \
  tests.test_routing tests.test_cli_v2 tests.test_lockfile \
  tests.test_adoption -v
scripts/control-plane doctor
```

Expected: FAIL porque `resolve_route()` todavía no acepta los contratos.

- [ ] **Step 3: Extender la API sin romper callers**

Firma objetivo:

```text
resolve_route(
  task: Mapping[str, Any],
  policy: Mapping[str, Any],
  registry: Mapping[str, Any],
  inventory: ValidatedInventory,
  *,
  mode: str,
  host_capability:
    HostAdapterCapability | HostAdapterUnavailable,
  clarification_request: ValidatedClarificationRequest | None = None,
  authorization: TrustedAuthorization | None = None,
  assumption: ValidatedAssumption | None = None,
  clarification_resolution: TrustedInteraction | None = None,
  irreversible_confirmation: TrustedIrreversibleConfirmation | None = None
) -> dict[str, Any]
```

El perfil se deriva exclusivamente del snapshot dentro de
`ValidatedInventory`; el caller no puede inyectar una segunda fuente de
verdad ni reconstruir el wrapper desde JSON. La disponibilidad del callback
host no forma parte de ese inventory de recursos: entra únicamente como
`HostAdapterCapability` opaca o el singleton tipado
`HostAdapterUnavailable`. Mapping, string, bool o campo del TaskEnvelope no
pueden fabricarla. `resolve_route()` sigue siendo puro: solo deriva el estado
determinista `ready|unavailable` de esos tipos y excluye nonce/session/TTL del
decision digest; no consume ni crea la capability.

El adaptador host construye y valida `clarification_request` antes de esta
llamada. Un mapping, aunque tenga los mismos bytes, falla
`C_UNTRUSTED_REQUEST`. Si una tarea high o `decision_approval` necesita
pregunta y el wrapper falta, el resolver no crea texto ni opciones:

```text
HostAdapterCapability no ready → pending_host_capability
capability ready, request ausente → clarification_request_required
```

La salida contiene solo level/status, question digest si ya existe, option IDs
si ya fueron validados, efectos bloqueados y reason codes. Nunca afirma haber
emitido un request confiable.

Mantener `ClarificationGate` bajo:

```python
interaction["clarification_gate"] = gate
```

No añadir una nueva clave top-level. `blocking` incorpora un reason code cuando:

```text
status ∈ pending_host_capability, clarification_request_required, ask_user,
         authorization_required, confirmation_required, blocked
```

Una tarea read-only high puede inspeccionar aunque `decision_ready` sea falso
para escritura.

- [ ] **Step 4: Extender verify y manifest**

Definir en Task 4, antes de `verify_route()`, el contexto opaco mínimo que
también reutilizará Task 5:

```text
build_trusted_route_context(
  *,
  task: Mapping[str, Any],
  decision: Mapping[str, Any],
  inventory: ValidatedInventory,
  session_id: str,
  invocation_id: str,
  host_capability: HostAdapterCapability,
  clock,
  ttl_seconds
) -> TrustedRouteContext

observe_resource_use(
  *,
  native_resource_events,
  task_context: TrustedRouteContext,
  route_decision,
  expected_repository,
  expected_worktree,
  expected_branch,
  expected_head,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ResourceUseObservation

validate_resource_use_observation(
  observation: ResourceUseObservation,
  *,
  expected_task_digest,
  expected_route_digest,
  expected_resource_bindings,
  expected_repository,
  expected_worktree,
  expected_branch,
  expected_head,
  expected_session_id,
  expected_invocation_id,
  clock
) -> ValidatedResourceUseObservation

verify_route(
  decision,
  observation: ValidatedResourceUseObservation,
  *,
  mode
) -> ResourceUseReceipt
```

`build_trusted_route_context()` pertenece desde este commit a
`host_bridge.py`; liga task/decision/inventory/repo/worktree/branch/HEAD/
session/invocation/TTL/nonce, carece de deserializador y cada operación consume
un wrapper nuevo. Así Task 4 compila y prueba su propia frontera sin depender de
una factoría que aparezca en Task 5.

La observación host enumera ID+registry/content digest+operation+ordinal y
efectos realmente observados; liga task/route/repo/worktree/branch/HEAD,
session/invocation/tool_use, TTL y nonce one-shot. El validator exige required
completos, forbidden ausentes, recommended usados solo si seleccionados y
closure exacta. Mapping, JSON, receipt previo, replay o contenido declarado por
el agente no puede construir el wrapper. `ResourceUseReceipt` es **salida**
compacta de esa verificación, no entrada ni grant.

`verify_route()` debe rechazar:

- receipt con `local_write` y high sin resolver;
- receipt con efecto no autorizado;
- destrucción sin confirmación;
- resolution/confirmation cuyo digest no coincide;
- resolución emitida por contenido externo.

`compact_route_manifest()` incluye únicamente:

```json
{
  "clarification": {
    "level": "high",
    "status": "ask_user",
    "decision_ready": false,
    "reason_codes": ["CLARIFY_REPOSITORY_UNRESOLVED"]
  }
}
```

Medir `len(manifest.encode("utf-8"))` después de serializar y registrar
`router_manifest_bytes` y `context_units_selected` mediante
`TaskStore.record_context_metrics()`. Si se renderiza brief, medir el string
real y registrar `novice_brief_bytes`; no aceptar esos dos valores desde JSON.

- [ ] **Step 5: Mantener el CLI fuera de la frontera de confianza**

`route` solo puede emitir estado y digests compactos. No construye ni acepta
`ClarificationRequest`, inventario, resolución, confirmación o autorización por
`PATH`, stdin o entorno. El adaptador host construye y valida el request antes
del resolver y llama a la API interna con `ValidatedClarificationRequest`,
`ValidatedInventory`, `TrustedInteraction` y, cuando corresponda,
`TrustedAuthorization`. Sin esa capacidad, el CLI informa
`pending_host_capability`; no inventa una pregunta.

El `route-verify` heredado que acepta decision/receipt por PATH se conserva solo
como diagnóstico `authoritative=false` en audit y jamás devuelve PASS
autoritativo ni habilita un efecto. La ruta gobernante es una API host
same-process con `ValidatedResourceUseObservation`; no existe flag para cargar
esa observación.

La salida humana añade:

```text
clarification_level=
clarification_status=
clarification_next_action=
clarification_ready=
```

No imprimir pregunta ni respuesta completas.

- [ ] **Step 6: Ejecutar GREEN y regresión**

Run:

```bash
python3 -m unittest \
  tests.test_routing tests.test_cli_v2 tests.test_assurance \
  tests.test_project_profiles tests.test_lockfile -v
```

Expected: todos PASS.

- [ ] **Step 7: Commit coherente**

Usar `stage_allowlisted_paths()` y `commit_staged_change()` gobernantes con
grants separados, la allowlist exacta:

```text
control_plane/routing.py
control_plane/host_bridge.py
control_plane/cli.py
.codex/control-plane.lock
tests/test_routing.py
tests/test_cli_v2.py
tests/test_assurance.py
tests/test_project_profiles.py
tests/test_lockfile.py
```

Mensaje cerrado: `Route material clarifications safely`. Reobservar
`LocalGitObservation`; no ejecutar stage/commit raw.

## Task 5: Incorporar lifecycle lateral e invalidación

**Files:**
- Modify: `control_plane/lifecycle.py`
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/cli.py`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_lifecycle.py`
- Modify: `tests/test_clarification.py`
- Modify: `tests/test_cli_v2.py`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Escribir RED de transiciones**

Añadir estos tests:

```text
test_clear_task_can_transition_from_framed_to_planned
  Expect: flujo ordinal existente sin alteración.

test_material_ambiguity_enters_clarification_required_from_active_states
  Expect: preservar resume_state exacto para cada estado activo admitido.

test_clarification_required_cannot_use_generic_resume
  Expect: task resume falla; solo la API host puede resolver el lateral.

test_generic_transition_cannot_enter_or_resolve_clarification_states
  Expect: CLI/TaskStore.transition rechazan entrar o salir de
  clarification_required.

test_host_bridge_is_the_only_entry_to_clarification_required
  Expect: TaskStore.require_clarification() preserva resume_state/digests desde
  cada estado activo admitido; mapping/CLI no puede invocarlo como transición.

test_host_bridge_records_resolution_and_resumes_in_same_process
  Expect: una sola API valida/consume TrustedInteraction y escribe directamente
  el estado final; misma información como mapping/JSON falla C_UNTRUSTED_CHANNEL.

test_trusted_route_context_is_fresh_one_shot_and_route_bound
  Given: contexto construido en el callback que observa inventory y resuelve
  route frente a mapping, replay, TTL, task/decision/inventory/session/
  invocation distintos.
  Expect: solo el wrapper exacto permite require/resolve; cada operación necesita
  un contexto nuevo y el resto falla C_ROUTE_CONTEXT_UNTRUSTED.

test_trusted_interaction_is_native_event_bound_and_one_shot
  Given: evento host actual frente a payload/JSON, replay, TTL, request/task/
  session/option/response/invocation distinto.
  Expect: solo el evento exacto produce y consume TrustedInteraction; nunca
  concede autorización.

test_resolution_requires_task_question_context_and_evidence_digests
  Expect: ausencia o cambio de cualquier digest falla cerrado.

test_same_task_digest_resumes_preserved_state
  Expect: aclaración válida recupera exactamente resume_state.

test_changed_task_digest_returns_to_planned_and_invalidates_descendants
  Expect: planned, invalidated_from informado y evidencia descendiente eliminada.

test_changed_question_or_repository_evidence_invalidates_resolution
  Expect: vuelve a clarification_required sin reutilizar la respuesta.

test_outcome_limits_ignore_lateral_states
  Expect: allowed terminal rank no compara estados laterales.

test_clarification_resolution_is_crash_and_race_safe
  Given: fault injection en write/fsync/replace/unlink de sidecar y state, dos
  resolvers o una transición ordinal concurrente.
  Expect: queda clarification_required o el estado final exacto; nunca un
  state publicado que apunte a sidecar ausente/no durable ni una pérdida
  silenciosa del gate; sidecars huérfanos se ignoran y el GC los elimina.

test_prompt_view_gc_uses_same_task_flock_and_generation
  Given: publisher y GC concurrentes en cada frontera write/fsync/replace.
  Expect: GC relee state bajo lock y solo borra generaciones antiguas no
  referenciadas; nunca elimina la vista del state vigente.

test_cold_restart_reemits_exact_prompt_view
  Given: restart/SessionStart(compact) con sidecar válido.
  Expect: pregunta, labels, recomendación y consecuencia byte-idénticos; sidecar
  ausente/derivado deja C_PRESENTATION_UNAVAILABLE y no regenera texto.

test_same_session_dirty_clarification_revalidates_existing_writer_lease
  Given: task entra desde implementing con árbol dirty y vuelve en la misma
  sesión.
  Expect: solo reanuda implementing si lease digest, repo/worktree/branch,
  policy, scope y changed-path inventory siguen coincidiendo exactamente.

test_cross_session_clarification_never_adopts_dirty_writer_lease
  Given: restart/SessionStart(compact) en session distinta con lease y cambios del owner
  anterior.
  Expect: permanece blocked; no reanuda ni adopta el lease y exige
  recover_abandoned() explícito más task/decision/lease nuevos.

test_dirty_clarification_reframe_releases_old_lease_before_new_task
  Given: origen implementing dirty y resolución cambia task/decision digest.
  Expect: finalizing_suspend → release owner-bound → blocked/resume_forbidden;
  nunca planned con lease viejo ni lease ausente con estado escribible.

test_crashed_clarification_owner_recovery_is_explicit_and_non_destructive
  Given: owner desaparecido, árbol dirty y lease sin TTL.
  Expect: sin TrustedLeaseRecoveryAuthorization exacta el lease sigue
  bloqueando; rescue autorizado publica tombstone, conserva los cambios,
  invalida la task antigua y nunca transfiere ownership.
```

- [ ] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest tests.test_lifecycle tests.test_clarification -v
```

Expected: los tests nuevos FAIL por estados/transiciones inexistentes.

- [ ] **Step 3: Separar progresión y estados laterales**

Definir:

```python
ORDERED_STATES = (
    "framed",
    "planned",
    "ready",
    "implementing",
    "verifying",
    "review_ready",
    "committed",
    "pushed",
    "pr_draft",
    "pr_ready",
    "merged",
    "base_verified",
    "release_pending",
    "released",
    "observed",
    "closed",
)

LATERAL_STATES = frozenset({"clarification_required", "blocked"})
```

Sustituir la generación implícita de transiciones por un mapa explícito.
Conservar `OUTCOME_LIMITS` ligado solo a `ORDERED_STATES`.

Matriz normativa; las flechas laterales solo existen mediante métodos
especializados, nunca mediante `transition()` genérico:

```text
framed|planned|ready|implementing|verifying|review_ready
  → siguiente ordinal | clarification_required | blocked

committed|pushed|pr_draft|pr_ready|merged|base_verified|
release_pending|released|observed
  → siguiente ordinal | blocked

clarification_required → resume_state|planned solo
  resolve_and_resume_clarification()
blocked → solo TaskStore.resume()
closed → ninguna
```

Excepción lateral cerrada: un verifier con gate fallido usa exclusivamente
`TaskStore.abort_verification()` y termina
`blocked/verification_aborted/resume_forbidden`; ese subtipo no admite
`resume()` ni `close()` y solo sirve como evidencia para crear una ronda nueva.

- [ ] **Step 4: Añadir evidencia y resume especializado**

Al entrar en `clarification_required`, exigir:

```text
clarification_context_digest
question_digest
repository_evidence_digest
task_digest
decision_digest
```

Guardar `clarification_resume_state`.
Si el estado de origen es `implementing`, guardar además el `lease_digest`,
owner session, scope normalizado y digest del inventario de paths ya cambiados;
la aclaración no libera ni transfiere automáticamente ese writer.

Al resolver, exigir:

```text
clarification_resolution_digest
current_context_digest
current_question_digest
new_task_digest
new_decision_digest
```

Implementar, reutilizando `TrustedRouteContext` y
`build_trusted_route_context()` ya introducidos y probados en Task 4:

```text
frame_trusted_interaction(
  *,
  native_event: NativeUserInteractionEvent,
  request: ValidatedClarificationRequest,
  selected_option_id: str,
  response_digest: str,
  session_id: str,
  invocation_id: str,
  host_capability: HostAdapterCapability,
  clock,
  ttl_seconds
) -> TrustedInteraction

TaskStore.require_clarification(
  task_id: str,
  *,
  request: ValidatedClarificationRequest,
  route_context: TrustedRouteContext,
  current_branch: str,
  task_digest: str,
  decision_digest: str
) -> dict[str, Any]

TaskStore.resolve_and_resume_clarification(
  task_id: str,
  *,
  interaction: TrustedInteraction,
  route_context: TrustedRouteContext,
  repository_context:
    ValidatedClarificationRepositoryObservation | RepositoryEvidenceNotChecked,
  expected_generation: int,
  current_branch: str,
  expected_head: str,
  task_digest: str,
  decision_digest: str,
  context_digest: str,
  question_digest: str
) -> dict[str, Any]
```

`require_clarification()` es el único punto de entrada lateral.
`TrustedRouteContext` se crea en la misma invocación que validó inventory y
resolvió route; liga task/decision/inventory/session/invocation/TTL/nonce,
recomputa los digests y se consume una sola vez. Entrada y salida del lateral
usan contextos distintos; no se reusa la observación consumida.
`TrustedInteraction` solo lo emite el bridge desde el evento de usuario nativo
actual y liga request/task/session/option/response/invocation/TTL/nonce; se
consume una vez y no concede efectos. Ninguno tiene deserializador/factoría CLI.
`RepositoryEvidenceNotChecked` también es una ruta de reanudación tipada, no un
hueco. Solo es válido si el request durable original registró
`repository_check_state=not_checked` y el issue es `decision_approval` o una
aclaración cuya policy no requiere hechos del repo. Nunca convierte en resolved
una ambigüedad factual high/critical, contradice un request que exigía
inspección ni se acepta como string/mapping; en esos casos la task permanece
bloqueada con `C_REPOSITORY_CHECK_REQUIRED`.
Todas las mutaciones de `TaskStore` toman un `fcntl.flock`
per-task, releen dentro del lock y verifican un contador `generation`; no existe
read-modify-replace sin CAS. Cuando el origen o destino es `implementing`, la
operación toma primero el common-dir lease flock y después el per-task flock,
conforme al orden global de Task 1; nunca los toma al revés. El bridge valida
request, presentation/context,
repo/worktree/branch/HEAD y estado activo. Bajo el flock, publica primero el
sidecar `0600` con write/fsync/replace/fsync-dir y después publica el state con
`clarification_resume_state`, digests y generation mediante
write/fsync/replace/fsync-dir. La atomicidad es por archivo y el orden garantiza
que un state visible nunca referencie una vista no durable.

`resolve_and_resume_clarification()` revalida y consume el wrapper una vez,
decide `resume_state` o `planned`, invalida evidencia si corresponde y escribe
directamente ese estado final bajo el mismo lock; sincroniza el state antes de
eliminar y sincronizar el sidecar. No persiste `clarified` como estado
intermedio. Un crash antes del replace conserva `clarification_required`;
después conserva el estado final aunque quede un sidecar huérfano recuperable
por GC. Si el host no entrega session/event identity suficiente, no existe
fallback serializado y la task permanece `clarification_required` en modo
advisory.

Si se pretende volver a `implementing` en la misma sesión, la operación relee
el lease bajo el common-dir flock y exige igualdad exacta de owner session,
lease digest, repo/worktree/branch, policy, scope y changed-path inventory
actual. Mismatch mantiene `blocked/E_CLARIFICATION_LEASE_DRIFT`; no reduce scope
ni amplía paths. Un restart/SessionStart(compact) que conserve la misma identidad host
puede revalidar esos bindings, pero no fiarse del sidecar por sí solo.

Una session distinta nunca adopta el lease dirty ni usa
`resolve_and_resume_clarification()` para tomar ownership. Queda
`blocked/E_CLARIFICATION_OWNER_CHANGED` y requiere el rescue explícito
`TaskLease.recover_abandoned()` definido en Task 1; ese rescue preserva el árbol,
invalida la task anterior y obliga a encuadrar una task/decision/lease nuevos.
No hay expiración automática: si el host no puede emitir la autorización de
recuperación exacta, el lease puede seguir bloqueando deliberadamente hasta
intervención del usuario.

Si task/decision cambian, volver a `planned`; borrar exactamente las evidencias
`ready`, `verifying` y `review_ready` y registrar un
`clarification_invalidation` compacto. Si question o repository evidence
cambian, volver a `clarification_required` y no reutilizar la resolución. Si
los digests permanecen, recuperar el estado exacto.

Excepción obligatoria: si el lateral salió de `implementing` con lease dirty y
task/decision cambian, no puede publicar `planned` conservando el writer viejo.
Usa common-dir → per-task y la misma transacción
`finalizing_suspend`/`_release_locked()` de Task 1; termina
`blocked/E_REFRAME_REQUIRED/resume_forbidden=true` con el árbol intacto. La
task nueva parte después desde ese estado explícitamente rescatado. Si falla el
release, permanece en `finalizing_suspend`, nunca planned/escribible. Del mismo
modo, `E_CLARIFICATION_LEASE_DRIFT` y
`E_CLARIFICATION_OWNER_CHANGED` llevan `resume_forbidden=true`; generic
`TaskStore.resume()` los rechaza y no adopta lease.

- [ ] **Step 5: Exponer solo estado/request por CLI**

No añadir `task clarify --resolution PATH`. El CLI puede consultar
`task clarification-status` y reemitir únicamente el request más
`ClarificationPromptView` ya validados y publicados por el bridge; nunca los
construye ni acepta por input. `task transition` rechaza el estado lateral.
Resolver y reanudar pertenece a una única llamada del adaptador host en memoria.
`task resume` genérico sigue atendiendo únicamente `blocked`.

- [ ] **Step 6: Ejecutar GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_lifecycle tests.test_clarification tests.test_cli_v2 tests.test_lockfile \
  tests.test_adoption -v
scripts/control-plane doctor
```

Expected: todos PASS.

- [ ] **Step 7: Commit coherente**

Usar los efectos stage/commit gobernantes, grants distintos y esta allowlist:

```text
control_plane/lifecycle.py
control_plane/host_bridge.py
control_plane/cli.py
.codex/control-plane.lock
tests/test_lifecycle.py
tests/test_clarification.py
tests/test_cli_v2.py
tests/test_lockfile.py
```

Mensaje cerrado: `Add resumable clarification lifecycle`. Reobservar
`LocalGitObservation`; no ejecutar stage/commit raw.

## Task 6: Crear Risk Sentinel triestado y CLI

**Files:**
- Create: `tests/test_risk_sentinel.py`
- Create: `control_plane/risk_sentinel.py`
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/cli.py`
- Modify: `control_plane/adoption.py`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_cli_v2.py`
- Modify: `tests/test_preflight.py`
- Modify: `tests/test_adoption.py`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Escribir RED del contrato triestado**

Tests:

```text
test_status_precedence_is_fail_unknown_pass
  Expect: FAIL domina UNKNOWN y PASS; UNKNOWN domina PASS.

test_local_safe_remote_unobserved_is_unknown_not_pass
  Expect: local PASS, remote UNKNOWN y agregado UNKNOWN.

test_human_and_json_contracts_use_exit_0_1_2
  Expect: PASS=0, FAIL=1, UNKNOWN=2 en ambos formatos.

test_git_observation_failure_becomes_unknown
  Expect: error estructurado, sin excepción cruda y agregado UNKNOWN.

test_candidate_policy_cannot_make_local_risk_pass
  Given: feature cambia base/remote/hooks y candidate lock coordinadamente.
  Expect: solo GoverningPolicy/ProtectedGitPolicy del base o manifest instalado
  gobierna; candidate drift queda FAIL/UNKNOWN y nunca SAFE_PATH_CONFIRMED.

test_local_base_policy_observation_is_host_bound_complete_and_one_shot
  Expect: NativeGitBaseEvent + RegisteredGoverningBaseContext y
  repo/remote/base/ref/commit/blob/EOF/session/invocation/TTL exactos
  producen ValidatedLocalBaseObservation; mapping, replay, ref móvil, blob
  ausente, partial clone/lazy fetch o cross-repo queda UNKNOWN sin red.

test_task6_has_no_unimplemented_installed_policy_shortcut
  Expect: antes de Task 8 solo se acepta ValidatedLocalBaseObservation; string,
  manifest candidato o nombre de tipo futuro no crea GoverningPolicy; host sin
  evento/contexto base opaco conserva UNKNOWN.

test_serialized_decision_cannot_make_authority_or_clarification_pass
  Expect: --decision PATH es solo hint diagnóstico; ambos checks quedan UNKNOWN.

test_host_risk_context_can_prove_current_authority
  Given: existe un efecto protegido vigente.
  Expect: PASS solo con ValidatedHostRiskContext ligado a task/session/HEAD,
  efecto/grant exactos, fresco y one-shot.

test_authority_check_is_pass_not_applicable_without_protected_effect
  Expect: RS_AUTHORITY_REQUIRED=PASS con reason=NOT_APPLICABLE sin
  ValidatedHostRiskContext únicamente cuando no se solicitó task/route/efecto;
  para una task, ausencia de contexto nunca absuelve un efecto ni prueba que no
  exista.
```

Contrato exacto:

```python
@dataclass(frozen=True)
class RiskCheck:
    code: str
    status: str
    message: str
    facts: dict[str, Any]


@dataclass(frozen=True)
class RiskDimension:
    status: str
    checks: tuple[RiskCheck, ...]
    errors: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class RiskStatus:
    command: str
    dimensions: dict[str, RiskDimension]
    facts: dict[str, Any]
    errors: tuple[dict[str, str], ...]
```

`RiskStatus.to_dict()` debe producir exactamente
`schema_version`, `command`, `ok`, `status`, `dimensions`, `facts` y `errors`.
`dimensions` contiene exactamente `local` y `remote`; cada una contiene
`status`, `checks` y `errors`. `status` se calcula mediante
`aggregate_status()` y `ok` es exactamente `status == "PASS"`. Errores top-level
son únicamente fallos del comando/contrato; incertidumbre de observación vive
en la dimensión correspondiente.

- [ ] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest tests.test_risk_sentinel -v
```

Expected: FAIL por ausencia de `control_plane.risk_sentinel`.

- [ ] **Step 3: Implementar agregación local**

Exponer:

```text
aggregate_status(statuses: Iterable[str]) -> str
  rank = PASS:0, UNKNOWN:1, FAIL:2
  return the status with the highest rank

observe_host_risk_context(
  *,
  native_task_event,
  trusted_route_context: TrustedRouteContext,
  clarification_resolution: TrustedInteraction | None,
  authorization: TrustedAuthorization | None,
  repository_identity,
  worktree_identity,
  branch,
  head,
  session_id,
  invocation_id,
  host_capability: HostAdapterCapability,
  clock,
  ttl_seconds
) -> HostRiskContextObservation

validate_host_risk_context(
  observation: HostRiskContextObservation,
  *,
  expected_task_digest,
  expected_decision_digest,
  expected_repository_identity,
  expected_worktree_identity,
  expected_branch,
  expected_head,
  expected_session_id,
  expected_invocation_id,
  expected_effect,
  expected_subject_digest,
  clock
) -> ValidatedHostRiskContext

frame_local_base_policy_source(
  native_git_base_event: NativeGitBaseEvent,
  *,
  host_capability: HostAdapterCapability,
  registered_base: RegisteredGoverningBaseContext,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> LocalBasePolicyObservation

validate_local_base_policy_source(
  observation: LocalBasePolicyObservation,
  *,
  expected_registered_base: RegisteredGoverningBaseContext,
  expected_invocation_id,
  clock
) -> ValidatedLocalBaseObservation

load_governing_local_policy(
  *,
  canonical_repo,
  governing_base_observation: ValidatedLocalBaseObservation,
  expected_invocation_id,
  clock
) -> GoverningPolicy

evaluate_local_risk(
  repo: Path,
  policy: GoverningPolicy,
  *,
  task_state: Mapping[str, Any] | None = None,
  route_decision_hint: Mapping[str, Any] | None = None,
  host_context: ValidatedHostRiskContext | None = None
) -> RiskDimension

evaluate_risk_status(
  repo: Path,
  policy: GoverningPolicy,
  *,
  task_state: Mapping[str, Any] | None = None,
  route_decision_hint: Mapping[str, Any] | None = None,
  host_context: ValidatedHostRiskContext | None = None,
  remote: RiskDimension | None = None
) -> RiskStatus
```

`load_governing_local_policy()` no es el primer productor ni una ruta más
débil: es el adaptador posterior para el manifest instalado de Task 8 y
reutiliza el parser/schema cerrado de
`load_governing_policy_from_runtime()` de Task 1. Solo cambia la procedencia
host-bound (`ValidatedLocalBaseObservation`); mapping/path candidate sigue sin
producir `GoverningPolicy`.

`HostRiskContextObservation` solo lo crea el bridge desde el evento host actual
y wrappers opacos ya validados; liga task/decision/repo/worktree/branch/HEAD,
clarification state, efecto/subject/grant, session/invocation/TTL/nonce. El
validator reobserva todos los expected bindings y devuelve un wrapper one-shot
consumido por una única evaluación. Mapping, JSON, decision hint, state edit,
replay, efecto distinto o grant expirado no producen PASS. El resultado y sus
facts no contienen prompt, respuesta ni autorización serializada.

Task 6 implementa y prueba la primera fuente solo para un host que pueda
entregar `NativeGitBaseEvent`. `RegisteredGoverningBaseContext` es opaco y se
crea por el host **antes** de entrar en el candidate a partir del registro de
repositorio/base ya validado; no acepta repo/remote/base/ref desde CLI, env,
TaskEnvelope o policy candidata. La observación encuadra el evento nativo,
demuestra repo/remote/base ref/commit/blob/EOF, liga capability,
session/invocation/TTL/nonce y se valida one-shot.
`load_governing_local_policy()` lee únicamente ese blob, comprueba
schema/digest y devuelve un wrapper opaco. Mapping, candidate policy, ref
ambigua, partial clone que intentaría fetch, replay o base no demostrable deja
UNKNOWN. Si el host actual no dispone de ese evento/contexto, el CLI de Task 6
queda deliberadamente UNKNOWN hasta la instalación de Task 8; jamás fabrica el
anchor leyendo `origin/<base>` elegido por el caller. En este hito no existe aún
`ValidatedInstalledPolicyObservation`: Task 8 añadirá explícitamente esa
segunda fuente y ampliará la unión después de implementar su manifest externo.

Si `remote is None`, crear `UNKNOWN/RS_REMOTE_NOT_OBSERVED`.

- [ ] **Step 4: Implementar la matriz local normativa**

No usar `evaluate_preflight(mode="read").ok` como agregado: read-only puede
seguir siendo `ok=True` mientras dirty o detached son inseguros. Traducir cada
hecho/check de forma explícita:

| Código | PASS | UNKNOWN | FAIL |
|---|---|---|---|
| `RS_LOCAL_POLICY` | policy validada | error de lectura no clasificable | ausente, inválida o schema no admitido |
| `RS_LOCAL_LOCK` | lock/digests coinciden | I/O u observación incompleta | ausente o drift demostrado |
| `RS_LOCAL_REPOSITORY` | repo/worktree observados | Git no responde | no es repositorio |
| `RS_LOCAL_BASE_BRANCH` | feature real | rama no observable | base actual |
| `RS_LOCAL_DETACHED` | HEAD unido a rama | HEAD no observable | detached |
| `RS_LOCAL_BASE_DIVERGENCE` | `behind=0` | fetch/divergencia no observable | `behind>0` |
| `RS_LOCAL_DIRTY` | limpio o lease válido cubre todo | status/lease no observable | dirty sin lease válido |
| `RS_LOCAL_HOOK_PATH` | ruta gestionada exacta | config no observable | ausente u otra ruta conocida |
| `RS_LOCAL_HOOK_DIGEST` | ambos ejecutables y digest exacto | error de lectura | ausentes, no ejecutables o drift |
| `RS_HOOK_TRUST` | trusted | pending | rejected/invalid |
| `RS_HOOK_MODE` | soft-enforce/enforce | audit | disabled/invalid |
| `RS_CLARIFICATION_REQUIRED` | ValidatedHostRiskContext prueba no pendiente/resuelto | contexto host no observable | high/critical pendiente demostrado |
| `RS_AUTHORITY_REQUIRED` | sin task/route/efecto solicitado (`NOT_APPLICABLE`), o ValidatedHostRiskContext prueba ausencia de efecto protegido/TrustedAuthorization vigente | autoridad no observable para una task | efecto solicitado y host demuestra ausencia/rechazo |
| `RS_PROFILE` | perfil completo detectado o generic completo | scan truncado/incompleto | perfil inválido |
| `RS_TASK_STATE` | task válida o no solicitada | task ID solicitado ausente | estado/digest manipulado |

Tests table-driven cubren los tres estados por código, incluido `remote=None`.

- [ ] **Step 5: Añadir CLI local**

Parser:

```text
risk-status
  --repo PATH
  [--policy PATH]
  --task-id ID
  --decision PATH
  --json
```

El flag `--github-event` se añadirá en Task 9, junto al adaptador remoto.
`--policy PATH` es únicamente un candidate-drift hint para compatibilidad y
nunca construye `GoverningPolicy`. El CLI deriva el Git common-dir y carga el
`ProtectedGitPolicy`/governing manifest instalado de Task 8; el host attestor
puede pasar un `GoverningPolicy` opaco en memoria. Si ninguno existe, los checks
dependientes de base/remote/hooks son UNKNOWN y no se emite
`SAFE_PATH_CONFIRMED`. Mismatch candidato se muestra, no sustituye al gobernante.
`_emit()` devuelve 2 cuando `command == "risk-status"` y
`status == "UNKNOWN"`.

`--decision PATH` puede ayudar a mostrar el task/route esperado, pero nunca
construye `HostRiskContext`, `TrustedInteraction` ni `TrustedAuthorization`.
Por tanto no puede producir PASS en `RS_CLARIFICATION_REQUIRED` o
`RS_AUTHORITY_REQUIRED`. Solo el adaptador host en memoria pasa
`ValidatedHostRiskContext`; si no está disponible, ambos checks son UNKNOWN, no
FAIL ni PASS inventado, salvo el caso cerrado `NOT_APPLICABLE` donde ni siquiera
se pidió evaluar una task/route/efecto. Un `task_state` o decision hint
serializado nunca produce PASS de aclaración ni authority.

Salida humana:

```text
PASS|FAIL|UNKNOWN risk-status
local=
remote=
interaction_recommended=
interaction_commands=
interaction_message=
automatic_change=false
project_profiles=
CODE message
```

`risk-status` llama al mismo `render_interaction_recommendation()` de Task 2.
La salida JSON incluye su objeto cerrado; la humana muestra commands/mensaje
fijos. Añadir tests cruzados que para los cuatro modos comparen byte a byte la
vista de `NoviceEngineeringBrief`, JSON y CLI humana, y que demuestren que
ninguna ruta ejecuta `/plan` o `/goal`, cambia effort o concede efectos.

- [ ] **Step 6: Ejecutar GREEN**

Añadir `risk_sentinel.py` a `RUNTIME_MODULES`, regenerar el lock y probar el
CLI desde un runtime aislado sin source tree.

Run:

```bash
python3 -m unittest \
  tests.test_risk_sentinel \
  tests.test_lifecycle \
  tests.test_resource_registry \
  tests.test_routing \
  tests.test_cli_v2 \
  tests.test_preflight \
  tests.test_adoption \
  tests.test_lockfile \
  -v
scripts/control-plane doctor
```

Expected: todos PASS.

- [ ] **Step 7: Commit coherente**

Usar los efectos stage/commit gobernantes, grants distintos y esta allowlist:

```text
control_plane/risk_sentinel.py
control_plane/host_bridge.py
control_plane/cli.py
control_plane/adoption.py
.codex/control-plane.lock
tests/test_risk_sentinel.py
tests/test_cli_v2.py
tests/test_preflight.py
tests/test_adoption.py
tests/test_lockfile.py
```

Mensaje cerrado: `Add tri-state engineering risk status`. Reobservar
`LocalGitObservation`; no ejecutar stage/commit raw.

## Task 7: Añadir warning de sesión y PreToolUse

**Files:**
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/hooks.py`
- Modify: `control_plane/cli.py`
- Modify: `control_plane/adoption.py`
- Modify: `tests/test_hooks.py`
- Modify: `tests/test_cli_v2.py`
- Modify: `tests/test_adoption.py`
- Create: `tests/macos_hook_smoke.py`
- Modify: `.codex/hooks.json`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Escribir RED del fingerprint**

Tests:

```text
test_first_prompt_warns_second_identical_prompt_is_silent
  Expect: primera invocación advisory, segunda sin contenido.

test_changed_risk_fingerprint_warns_again
  Expect: un cambio de branch/base/policy/risk vuelve a emitir.

test_session_state_uses_hashed_id_under_worktree_git_dir
  Expect: ruta worktree-local y nombre sha256(session_id), nunca session_id crudo.

test_warning_state_never_contains_prompt_or_command
  Expect: archivo limitado a schema, fingerprint y timestamps técnicos.

test_unwritable_or_invalid_state_returns_unknown_warning
  Expect: salida acotada UNKNOWN; nunca PASS silencioso ni traceback.

test_no_pro_remote_unknown_renders_visible_continue_with_caution_warning
  Expect: local PASS + remote UNKNOWN/protection no demostrable produce
  `CONTROL PLANE RISK`, `CONTINUE_WITH_CAUTION`,
  `RS_REMOTE_PROTECTION_UNVERIFIED` y safe path profesional completo.

test_prompt_postcompact_and_red_action_use_the_same_bounded_renderer
  Expect: UserPromptSubmit inicial, fingerprint cambiado, trigger conceptual
  post_compact y acción roja llaman el mismo renderer; payload <=4096 bytes,
  sin prompt/comando/secreto, y la acción roja no queda silenciada por dedupe
  de sesión.

test_user_prompt_before_framing_never_invents_route_or_interaction
  Expect: el hook temprano emite una vista `pending_framing` basada solo en
  riesgo local/policy observables; no fabrica route digest, tier ni `/plan`/
  `/goal`. La recomendación accionable aparece después del framing.

test_sessionstart_compact_maps_to_post_compact_and_always_reemits_current_view
  Given: evento raw soportado `SessionStart` con matcher/source `compact` y el
  mismo fingerprint ya emitido por UserPromptSubmit.
  Expect: el adapter lo normaliza a `post_compact`, reemite bytes idénticos de
  la vista current-task saneada, no consulta ni modifica el dedupe normal y el
  doctor/smoke demuestran la configuración real de hooks.

test_current_warning_view_is_separate_atomic_session_bound_and_restart_safe
  Given: vista publicada, restart/compact en la misma sesión, sesión distinta,
  archivo corrupto, route/task/fingerprint drift y GC concurrente.
  Expect: solo la misma sesión/bindings rehidrata bytes idénticos; corrupción o
  cross-session devuelve warning mínimo UNKNOWN, y GC nunca borra la generación
  vigente ni mezcla el archivo de dedupe.

test_risk_warning_reuses_interaction_recommendation_without_switching_mode
  Expect: commands/mensaje coinciden con `InteractionRecommendationView` de
  Task 2 y siempre `automatic_change=false`; el hook no ejecuta `/plan`/`/goal`.

test_candidate_mapping_cannot_weaken_warning_or_bash_classification
  Given: `GoverningPolicy` opaca de base frente a Mapping/path candidate que
  cambia base, remote, safe path o clasificación de push.
  Expect: renderer y classifier aceptan solo el wrapper gobernante; mapping,
  TaskEnvelope, hook input o policy candidate falla/queda drift y nunca rebaja
  warning, base push o efecto destructivo.
```

- [ ] **Step 2: Escribir RED de comandos**

Tabla mínima:

```text
git push origin main              → direct base
git push origin HEAD:main         → direct base
git push origin feature:main      → direct base
git push --delete origin main     → base deletion
git -C repo push origin main      → direct base
git push                           → direct base si cwd=base/upstream=base
git push                           → allowed si cwd=feature/upstream=feature
git push --all                    → ambiguous dangerous
git push --mirror                 → dangerous
git push origin feature           → allowed
git push origin HEAD              → allowed off base
git push --force origin feature   → destructive
git reset --hard                  → destructive
git clean -f|-fdx                 → destructive
rm -rf explicit-target           → destructive
compound shell                    → UNKNOWN warning
Edit|Write|apply_patch            → deny con clarification high/critical pendiente
Read|Glob|Grep                    → allowed para investigar la aclaración
raw git status/diff/log/show      → read_only_unsanitized; usar safe-read
raw rg aunque parezca lectura     → read_only_unsanitized; usar safe-read
```

Añadir RED específicos del resultado:

```text
test_completed_safe_read_normal_and_nonzero_are_bounded_and_exact
  Expect: status=completed, exit code exacto, stdout/stderr dentro de caps y
  ningún payload persistido en hook state.

test_completed_safe_read_timeout_kills_process_group
  Expect: status=timeout, timed_out=true, exit_code=null, hijos terminados y
  output parcial acotado; nunca read_only_known PASS silencioso.

test_completed_safe_read_overflow_terminates_and_marks_truncated
  Expect: al superar cualquiera de los caps termina el grupo, status=truncated,
  truncated=true y no devuelve bytes extra.

test_safe_read_cli_and_isolated_runtime_share_the_same_contract
  Expect: source e isolated producen el mismo CompletedSafeRead para normal,
  nonzero, timeout, overflow y argv rechazado.

test_safe_read_repo_option_binds_execution_to_explicit_worktree
  Expect: un launcher gobernante ejecutado desde otro checkout con
  `--repo <pilot>` lee exclusivamente el root/worktree Git del piloto y el
  resultado liga el digest de esa identidad.

test_safe_read_repo_rejects_non_root_symlink_and_unregistered_target
  Expect: target relativo, symlink, path que no sea raíz canónica o worktree no
  presente en el inventario host-bound falla antes de ejecutar argv.

test_safe_read_inventory_wrapper_is_fresh_exact_and_one_shot
  Expect: solo `ValidatedWorktreeInventoryObservation` fresca de la misma
  invocation/common-dir permite ejecutar; mapping/JSON, replay, TTL,
  cross-common-dir, cap+1 o inventario parcial fallan antes del subprocess.

test_safe_read_rg_quiet_single_path_grammar
  Expect: admite exactamente `rg --no-config --quiet -e PATTERN --
  <single-in-root-path>`; path adicional, patrón desde archivo, glob,
  `--pre`, config o escape se rechazan.

test_macos_hook_smoke_runner_is_mechanical_single_process_and_darwin_only
  Expect: `run_macos_hook_smoke()` posee platform check, Popen, caps, temp repos
  y snapshots before/after; solo Darwin/repo/HEAD/runtime/lock/hooks/session/
  invocation exactos producen `CompletedMacOSHookSmoke`. Mapping, JSON, replay,
  Linux simulado o drift fallan y no hace falta evento nativo para el gate
  mecánico audit-only.

test_macos_hook_smoke_requires_every_mechanical_scenario
  Expect: warning once, segunda invocación silenciosa,
  SessionStart(compact)→post_compact y reemisión compacta,
  safe-read del target explícito, allow en feature, deny en base,
  Stop/receipt y rollback byte-idéntico deben estar observados; faltar, omitir
  o duplicar ambiguamente uno produce UNKNOWN/FAIL.

test_macos_hook_smoke_receipt_never_authorizes_or_promotes_semantics
  Expect: el receipt guarda solo OS/resultados/digests/reason codes; sin
  revisión humana de `/hooks` no cambia hook trust, y adapter nativo ausente
  conserva enforcement semántico en audit/advisory.

test_hook_review_requires_native_hooks_ui_event_and_exact_smoke
  Expect: solo un evento host nativo `/hooks`, ligado al smoke/HEAD/hashes/
  session/invocation/TTL exactos, produce
  `ValidatedHookReviewObservation`; mapping, texto, replay, drift o smoke no
  PASS fallan antes de publicar `HookReviewReceipt`.

test_hook_review_publisher_requires_refreshed_context_and_rotates_generation
  Given: `VerificationTaskContext` devuelto por el publisher del smoke frente a
  contexto previo, mapping byte-idéntico, expected_generation suelta,
  task/profile/HEAD/owner drift o replay.
  Expect: solo el contexto fresco publica por CAS y devuelve
  `HookReviewPublicationResult` con receipt + nuevo contexto; los demás no
  escriben ni reutilizan generation.

test_macos_smoke_without_native_event_or_hooks_review_stays_audit_only
  Expect: mechanical PASS publica receipt válido para PR C en audit, pero
  `native_adapter=absent` y/o revisión `/hooks` pendiente conservan
  pending_hook_trust y bloquean soft-enforce/enforce.

test_compact_raw_hook_event_uses_supported_sessionstart_mapping
  Expect: hooks.json instala `SessionStart` matcher/source `compact`; doctor y
  smoke prueban que el adapter lo normaliza a `post_compact`. Un evento literal
  desconocido `PostCompact` no se presupone soportado.
```

Tests audit y soft-enforce para cada acción roja. Cubrir payload real de
`PreToolUse` para Bash, Edit, Write, apply_patch y MCP; no probar únicamente el
helper aislado.

Añadir casos de scope:

```text
Edit/Write dentro de lease y mismo task/worktree/branch/session/policy → allowed
Edit/Write fuera de lease                                       → deny
apply_patch con cualquier target fuera de lease                 → deny
target relativo con symlink escape                              → deny
lease de otro task/worktree/branch/session/policy                → deny
payload sin ruta demostrable                                    → UNKNOWN en audit, deny en soft-enforce
MCP con egress/efecto sin handshake host en el mismo tool_use    → ask/advisory;
                                                                  nunca allow por JSON
```

Añadir canarios que demuestren que `safe-read` no toca el índice, no arranca
pager, fsmonitor, diff driver, textconv ni preprocessor, incluso si config local
o global intenta habilitarlos. Verificar también que el comando crudo
equivalente no recibe clasificación `read_only_known`.

- [ ] **Step 3: Ejecutar RED**

Run:

```bash
python3 -m unittest \
  tests.test_hooks tests.test_cli_v2 tests.test_lockfile tests.test_adoption -v
scripts/control-plane doctor
```

Expected: nuevos tests FAIL.

- [ ] **Step 4: Implementar estado mínimo**

Exponer en `hooks.py`:

```text
risk_fingerprint(root: Path) -> str
  Canonical JSON sha256 of branch, base ref, policy digest, current validated
  route digest or the literal state marker "pending_framing", and risk status.

warning_state_path(root: Path, session_id: str) -> Path
  worktree_git_dir(root) / "codex-control-plane" / "warnings" /
  (sha256(session_id UTF-8) + ".json")

should_emit_warning(root: Path, session_id: str, fingerprint: str) -> bool
  Atomically persist only schema_version, fingerprint and emitted_at.
  Return true for missing/changed state and false only for an exact valid match.

current_warning_view_path(root: Path, session_id: str) -> Path
  worktree_git_dir(root) / "codex-control-plane" / "warning-views" /
  (sha256(session_id UTF-8) + ".json")

publish_current_warning_view(
  root,
  session_id,
  payload: HookWarningPayload,
  *,
  task_digest,
  route_digest,
  fingerprint,
  generation
) -> CurrentWarningView

load_current_warning_view(
  root,
  session_id,
  *,
  expected_task_digest,
  expected_route_digest,
  expected_fingerprint
) -> CurrentWarningView | None

render_risk_warning(
  risk_status: RiskStatus,
  interaction: InteractionRecommendationView | None,
  *,
  trigger: user_prompt | fingerprint_changed | pre_red_action | post_compact,
  framing_status: pending_framing | framed,
  governing_policy: GoverningPolicy
) -> HookWarningPayload
```

Escritura atómica, permisos mínimos, sin prompt/tool input. El estado de dedupe
y la vista rehidratable son archivos distintos. `CurrentWarningView` contiene
solo schema/generation, `HookWarningPayload` cerrado, task/route/fingerprint
digests y payload digest; no prompt, objetivo, comando, documento externo,
output ni secreto. Publish/load/GC comparten flock y generation;
temp+fsync+replace+fsync-dir hace restart seguro. Un sidecar corrupto, symlink,
digest distinto o sesión distinta no se reutiliza.

`HookWarningPayload` es cerrado y <=4096 bytes:

```text
title = CONTROL PLANE RISK
local = PASS | UNKNOWN | FAIL
remote = PASS | UNKNOWN | FAIL
action = SAFE_PATH_CONFIRMED | CONTINUE_WITH_CAUTION | PAUSE_AND_VERIFY | STOP
reason_code = vocabulario RS_* cerrado
safe_path = feature→commit→push-feature→PR→checks→authorized-merge
interaction = pending_framing | InteractionRecommendationView
automatic_change = false
```

Precedencia: cualquier FAIL→STOP; local UNKNOWN→PAUSE_AND_VERIFY; local PASS +
remote UNKNOWN por protección no demostrable→CONTINUE_WITH_CAUTION y
`RS_REMOTE_PROTECTION_UNVERIFIED`; PASS/PASS→SAFE_PATH_CONFIRMED. El reason
principal se elige por orden cerrado, no por texto externo. `safe_path` deriva
de policy/registry validadas y nunca implica que commit/push/merge estén
autorizados.

`UserPromptSubmit` ocurre antes de encuadrar el prompt nuevo: llama al renderer
solo con `framing_status=pending_framing`, no requiere `RouteDecision` y no
inventa una recomendación ni digest; el fingerprint usa el marcador cerrado
`pending_framing`. Tras task-framer+resolver, el
`NoviceEngineeringBrief`/comentario host muestra la
`InteractionRecommendationView` accionable y publica una vista current-task
saneada mediante `publish_current_warning_view()`, ligada a
task/route/fingerprint. `fingerprint_changed` usa
`should_emit_warning()` únicamente si existe esa vista vigente.

El trigger conceptual `post_compact` no es un nombre de evento instalado:
`.codex/hooks.json` conserva el schema soportado
`SessionStart` con matcher/source `compact`, y el adapter valida ese raw event
antes de mapearlo. Siempre reemite la vista current-task vigente —o un warning
mínimo `pending_framing` si falta/deriva— aunque el fingerprint sea idéntico;
no consulta, escribe ni incrementa el dedupe de UserPromptSubmit.
`pre_red_action` también llama siempre al renderer y no consulta el dedupe, para
no ocultar riesgo inmediato. Un fallo de render o payload sobre cap devuelve un
warning mínimo UNKNOWN, nunca silencio.

- [ ] **Step 5: Implementar clasificador curado**

Exponer:

```text
classify_bash_command(
  command: str,
  *,
  root: Path,
  governing_policy: GoverningPolicy
) -> BashEffect

execute_safe_read(
  argv: Sequence[str],
  *,
  root: Path,
  worktree_inventory: ValidatedWorktreeInventoryObservation,
  timeout_seconds: float,
  output_limit_bytes: int
) -> CompletedSafeRead
```

Tanto el renderer como el clasificador consumen el mismo `GoverningPolicy`
opaco cargado desde la base/manifest instalado. Un mapping de policy candidata,
`--policy PATH`, TaskEnvelope, hook input o JSON no puede elegir base, remote,
guards, safe path ni clasificación de un efecto; solo puede aparecer como
candidate drift diagnóstico.

`CompletedSafeRead` es una dataclass frozen con schema interno cerrado:

```text
argv_digest: sha256
repository_binding_digest: sha256
status: completed | timeout | truncated | rejected
exit_code: int | null
timed_out: bool
truncated: bool
stdout: bytes
stderr: bytes
stdout_bytes: int
stderr_bytes: int
duration_ms: int
```

`completed` admite exit cero o no cero y conserva el código exacto.
`timeout|truncated|rejected` nunca se reclasifican como lectura segura exitosa;
timeout/overflow terminan el process group y esperan su cierre. Longitudes
coinciden con los bytes devueltos y cada stream respeta su cap; no hay flag de
“truncated=false” con bytes omitidos. El objeto no se serializa en warning,
receipt ni ledger. Errores de validación solo incluyen código y argv digest,
nunca el comando/output crudo.

La allowlist `git` de v2.1 se parsea como argv y admite únicamente las formas
cerradas necesarias:

```text
git status --short
git diff [--cached] --check
git diff [--cached] --name-only [<validated-base>...HEAD] [-- <closed-paths>]
git diff --exit-code <validated-base>...HEAD -- <closed-paths>
git log|show <selectores/opciones read-only cerrados>
```

`<validated-base>` deriva de policy/ref observada y `<closed-paths>` de la
allowlist/task, nunca de texto externo. Orden, duplicado, flag adicional,
pathspec mágico, config/env, pager, ext-diff, textconv, submodule helper,
replace object, lazy fetch o selector ambiguo se rechaza. Así los `git diff`
staged/post-commit de Task 14 pueden producir `CompletedSafeRead`; sus formas
raw siguen siendo solo diagnóstico no autoritativo.

Crear además `tests/macos_hook_smoke.py`, un harness stdlib hermético que usa
repositorios/worktrees temporales y ejecuta los launchers y procesos hook
reales, no helpers importados, para demostrar:

```text
UserPromptSubmit avisa una vez y luego calla con fingerprint idéntico
SessionStart(compact) se normaliza a post_compact y rehidrata solo estado compacto
PreToolUse permite safe-read --repo sobre el target exacto
pre-commit permite feature y bloquea base/detached
pre-push permite feature y bloquea base/force
Stop valida receipt sin loop
rollback restaura config, hooksPath y bytes preexistentes
source e isolated runtime producen la misma decisión
adapter host nativo, si existe, pasa su forward-test real
```

API normativa del gate mecánico:

```text
run_macos_hook_smoke(
  *,
  canonical_repo,
  expected_head,
  expected_artifact_digests,
  session_id,
  invocation_id,
  dedicated_temp_root,
  clock,
  timeout_seconds
) -> CompletedMacOSHookSmoke

publish_macos_hook_smoke_receipt(
  completed: CompletedMacOSHookSmoke,
  *,
  task_store,
  task_context: VerificationTaskContext,
  expected_generation
) -> HookSmokePublicationResult

observe_native_macos_hook_smoke(
  *,
  native_process_event,
  completed_digest,
  expected_repo,
  expected_head,
  expected_artifact_digests,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ValidatedNativeMacOSHookSmokeObservation

frame_hook_review_observation(
  *,
  native_hooks_review_event,
  smoke_receipt_digest,
  expected_repo,
  expected_head,
  expected_artifact_digests,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ValidatedHookReviewObservation

publish_hook_review_receipt(
  validated_review,
  *,
  task_store,
  task_context: VerificationTaskContext,
  expected_generation
) -> HookReviewPublicationResult
```

`run_macos_hook_smoke()` es un runner stdlib cerrado de un solo proceso: valida
Darwin y bindings, construye internamente cwd/argv, posee todos los
`subprocess.Popen` sin shell, timeout/caps/process groups, repos temporales y
snapshots tracked/index before/after. El caller nunca aporta argv ni resultados.
`CompletedMacOSHookSmoke` es un objeto in-memory one-shot sin
constructor/deserializador público; el publisher lo consume bajo generation
compare-and-swap. El CLI `hook-smoke --repo PATH --task-id ID` solo selecciona
un task/profile ya ligados y llama runner+publisher en el mismo proceso; no
acepta result/observation por archivo, stdin o env.

`observe_native_macos_hook_smoke()` es assurance adicional cuando el host
dispone de evento opaco real; liga ese evento al digest del resultado mecánico.
No es requisito para publicar el receipt audit-only y no puede transformar un
FAIL/UNKNOWN mecánico en PASS. Solo el publisher muta TaskStore; un receipt no
puede volver a convertirse en completed/observation.

El runner emite un `CompletedMacOSHookSmoke` cerrado y acotado con IDs de caso,
exit/decision, hashes de stdout/stderr saneados y digests de artefactos; nunca
incluye output crudo, prompt, comando, hostname, usuario ni secreto. Liga
`platform.system() == "Darwin"`, repo, HEAD, policy, registry, lock, launcher,
hooks, session, invocation y snapshots exactos. El publisher consume ese objeto
y publica atómicamente bajo el Git dir un `MacOSHookSmokeReceipt` compacto;
incrementa generation y devuelve un `VerificationTaskContext` refrescado que
deben usar los publishers posteriores. Mapping/JSON no se deserializa como
resultado.

`publish_hook_review_receipt()` aplica la misma cadena CAS: consume la
`ValidatedHookReviewObservation` one-shot y el
`VerificationTaskContext` **refrescado devuelto por el smoke**, relee
task/profile/HEAD/owner/generation bajo flock, publica JSON canónico
`0600`+fsync/replace y devuelve receipt más otro contexto refrescado. Contexto
anterior, expected_generation aportado sin wrapper, mapping, replay o drift no
publica. Si no hay revisión nativa, no se inventa este receipt y la cadena
continúa desde el contexto del smoke en estado `pending_hook_trust`.

El receipt distingue:

```text
mechanical_result = PASS | FAIL | UNKNOWN
native_adapter = ready | absent | failed
human_hooks_review = pending
authorizes = false
```

No concede efectos ni promueve hooks por sí solo. `mechanical_result=PASS`
permite considerar el smoke macOS satisfecho; `native_adapter=absent` mantiene
solo las barreras mecánicas y deja enforcement semántico en audit/advisory. La
confianza exige además revisión humana de los hashes/contenido en `/hooks`;
sin ella, el estado sigue `pending_hook_trust`. Tras esa UI,
`frame_hook_review_observation()` consume el evento nativo y devuelve una
`ValidatedHookReviewObservation` one-shot ligada al digest del smoke, HEAD y
hashes exactos de hooks/launcher/lock; el publisher crea un
`HookReviewReceipt` separado, sin texto de la conversación. Ninguno de los dos
receipts es una autorización de efectos. Drift de cualquier binding invalida
la confianza y exige smoke/revisión nuevos.

Usar `shlex.split()` solo para comando simple. Si contiene operadores de shell
o no se puede analizar, devolver `ambiguous_shell_command`.

Vocabulario cerrado:

```text
read_only_known
read_only_unsanitized
git_effect
write_paths_known
may_write_unknown_paths
ambiguous_shell_command
destructive
```

`read_only_known` no se decide solo por el verbo y el clasificador no pretende
sanear un comando que ya va a ejecutar otra herramienta. Solo la forma cerrada

```text
scripts/control-plane safe-read --repo <raíz-canónica> -- <argv permitido>
```

entra en `read_only_known`. El CLI llama `execute_safe_read()` sin shell y
revalida `argv`; `--repo` es obligatorio, se resuelve como raíz Git canónica,
no admite symlink y debe corresponder exactamente a un worktree de la
`ValidatedWorktreeInventoryObservation` fresca, exacta y one-shot de la misma
invocation/common-dir. El entrypoint observa y valida el inventario dentro de
esa invocación; el caller no puede aportarlo por JSON. Mapping, replay, TTL,
cross-common-dir, cap+1 o salida parcial fallan antes del subprocess. El
launcher puede proceder de un attestor distinto: su `cd` interno nunca
sustituye el target explícito. El resultado liga un
`repository_binding_digest` de root, git-dir y common-dir; un `safe-read`
anidado, path de ejecutable distinto o flag no enumerada falla. Git se ejecuta
con cwd en esa raíz y entorno construido desde allowlist:

```text
GIT_OPTIONAL_LOCKS=0
GIT_NO_LAZY_FETCH=1
GIT_NO_REPLACE_OBJECTS=1
GIT_TERMINAL_PROMPT=0
GIT_PAGER=cat
PAGER=cat
GIT_EXTERNAL_DIFF=
GIT_CONFIG_NOSYSTEM=1
```

Además añade `-c core.fsmonitor=false`, deshabilita pagers por subcomando y
apunta `GIT_CONFIG_GLOBAL` a un archivo regular vacío, gestionado por el
runtime y validado como no symlink,
exige `--no-ext-diff --no-textconv` donde existan. Solo admite built-ins
`status|diff|log|show|rev-parse` con gramática exacta; limita tiempo y bytes de
salida. Para `rg`, usa el ejecutable esperado con `--no-config`, elimina
`RIPGREP_CONFIG_PATH`, rechaza `--pre`, `--pre-glob` y cualquier opción capaz de
ejecutar un proceso. La gramática cerrada necesaria para el piloto admite
`--no-config --quiet -e PATTERN -- <single-in-root-path>`; no admite patrón
desde archivo, más de un target, glob ni path fuera de la raíz. El entorno se
construye desde allowlist mínima y elimina
`GIT_ASKPASS`, `SSH_ASKPASS`, `GIT_SSH`, `GIT_SSH_COMMAND`,
`GIT_PROXY_COMMAND`, `GIT_PROTOCOL_FROM_USER` y variables proxy
HTTP/HTTPS/ALL/NO_PROXY en mayúsculas y minúsculas. Config, alias, pager,
fsmonitor, replace objects, lazy fetch de promisor, diff driver, textconv,
preprocessor, `-c`,
`--config-env` o flags desconocidas no se heredan ni se aceptan.

Para secretos no acepta regex ad hoc del caller. La forma interna cerrada
`secret-scan-governing -- <single-in-root-path>` carga desde el runtime
gobernante el mismo pattern-set/version digest que usa el scanner de
repositorio, incluye asignaciones sensibles, bearer/token y cabeceras de clave
privada, ejecuta en el proceso de `safe-read` sin imprimir matches y liga ese
digest al `CompletedSafeRead`. Pattern-set ausente, drift frente al lock o
resultado no demostrable es UNKNOWN; una búsqueda `rg` más estrecha nunca
satisface el gate de secretos.

Un test crea un partial clone/promisor hermético con transporte canario:
`show/log/diff` sobre objeto ausente deben fallar localmente sin invocar fetch,
askpass, SSH ni proxy.
Otro crea `refs/replace` que altera un commit/árbol y demuestra que
`log/show/diff` seguros observan los objetos originales con
`GIT_NO_REPLACE_OBJECTS=1`; si el host no puede fijar/verificar esa variable,
el resultado es UNKNOWN, no evidencia sobre el reemplazo.

Registrar el subcomando `safe-read` en `control_plane/cli.py`, probar
`--repo`, su parser, separador `--`, exits, caps, binding del worktree y rechazo
de argv no permitido en
`tests/test_cli_v2.py`. `control_plane/adoption.py` debe incluir el launcher y
el módulo `hooks.py` que contiene `execute_safe_read()` dentro del runtime
aislado; `tests/test_adoption.py` lo ejecuta con el source tree oculto. La
presencia del comando en el checkout fuente no demuestra distribución.

Un `git`/`rg` crudo, aunque sus tokens parezcan de lectura, se clasifica
`read_only_unsanitized`: audit puede explicarlo, pero soft-enforce exige volver
a emitirlo mediante `safe-read --repo <raíz-canónica>`. Así el hook autoriza la
variante que realmente se ejecuta, no una versión ideal que nunca sustituyó.
Formatters/generadores con
targets parseables entran en
`write_paths_known`. Tests/builds (`python -m unittest`, `pytest`, `xcodebuild`,
`npm|pnpm test`, scripts configurados por project policy) se consideran
`may_write_unknown_paths` porque pueden crear caches/derived data. Un binario no
reconocido o shell compuesto es ambiguous, no read-only.

`git_effect` tampoco es una autorización genérica ni una categoría que pueda
caer por omisión en `write_paths_known`. Solo existe cuando un adaptador tipado
reconoce un argv **directo, sin shell**, y lo vincula a uno de estos operation
IDs cerrados:

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

El hook no reconstruye, completa ni ejecuta esos argv. Para cada operation ID,
un wrapper host de `control_plane/host_bridge.py` es propietario de la plantilla
argv y valida inmediatamente antes del proceso: task/estado/resultado
solicitado, session+invocation+tool_use, repo/common-dir/worktree/branch/HEAD,
policy/registry/lock, lease y scope cuando existe escritura, efecto exacto,
subject digest, remote/base/feature allowlisted y `TrustedAuthorization`
one-shot específica. El caller solo aporta selectores ya validados; nunca
shell, argv libre, remote arbitrario ni flags. Fetch no es lectura pura:
escribe objetos/refs/reflogs en el common Git dir y exige simultáneamente
grants one-shot `network_read` + `local_write`, además de un
`RemoteRefMutationGuard` prepare→arm→execute→reobserve que serializa operaciones
del control plane y detecta carreras externas. `prepare` es lectura pura; solo
`arm`, después de consumir ambos grants, publica intent. `lease=None` solo
significa que no edita archivos del worktree; nunca elimina la mutación local.
Push y borrado
de feature remota exigen `remote_write` distinto para su subject/ref exactos;
stage/commit/worktree/local-base-sync su efecto local exacto, y ningún lease
convierte un push directo a base, force, reset, clean, checkout de otro worktree
o borrado no allowlisted en operación válida.

En `audit`, un `git_effect` sin todos esos bindings produce warning
`UNKNOWN/RS_GIT_EFFECT_NOT_ATTESTED`. En `soft-enforce` y `enforce`, solo el
handshake host nativo de la misma tool invocation y el wrapper cerrado pueden
permitirlo; ausencia, replay, drift, shell compuesto, operación desconocida o
grant de otro efecto devuelven deny/pending antes de Git. Los snippets
operativos posteriores nombran estos wrappers: una línea `git ...` mostrada
como argv interno nunca autoriza a ejecutarla directamente desde Bash.

Soft-enforce deniega:

```text
destructive_command_requires_explicit_authority
direct_base_push_forbidden
base_deletion_forbidden
material_clarification_blocks_write
write_outside_task_lease
worktree_or_task_identity_mismatch
unresolvable_write_scope
```

Una acción de lectura continúa permitida para resolver hechos. Un MCP con
egress o efecto externo no queda autorizado por ser seleccionable: produce
warning/gate de autoridad separado. Los command hooks no pueden transportar un
`TrustedAuthorization` entre procesos. Si Codex ofrece una decisión host
one-shot ligada a `session_id + tool_use_id + task/repo/worktree/HEAD/scope/
effect/subject_digest`, `PreToolUse` puede devolver `ask` y consumirla en ese
mismo ciclo. Si no existe esa prueba nativa, el estado es
`pending_host_authorization_bridge`: audit avisa y la promoción semántica queda
bloqueada; nunca se persiste/reconstruye un grant desde JSON.

Implementar adaptadores por tool, no una búsqueda textual genérica:

```text
Edit/Write     → campo file_path exacto
apply_patch    → todos los headers Add/Update/Delete/Move
Bash           → cwd + parser curado; shell ambiguo no demuestra scope
MCP            → metadata de recurso/effect/egress del registry
```

Normalizar cada ruta con `resolve(strict=False)`, comprobar ancestros existentes
sin symlink escape y exigir confinamiento al root. Antes de permitir escritura,
validar el `TaskLease` activo contra task ID actual, worktree, branch, session,
policy digest y todos los targets. El task ID procede del contexto host activo,
no de `tool_input`, ruta o lease seleccionado por el agente. El evento hook no
obtiene autoridad del contenido de
`tool_input`. Si el host no expone una session identity verificable,
soft-enforce falla cerrado para escritura y audit muestra UNKNOWN.

Matriz:

```text
audit:
  read_only_known → allow silencioso
  git_effect atestiguado → observar decisión host; nunca inferir autoridad
  cualquier otra categoría no demostrada → warning UNKNOWN/rojo

soft-enforce:
  read_only_known → allow
  read_only_unsanitized → deny y devolver forma safe-read equivalente
  git_effect → allow solo por wrapper/operation ID cerrado, bindings completos
               y grant host one-shot de la misma invocation; en otro caso deny
  write_paths_known → allow solo si lease cubre todos los targets
  may_write_unknown_paths → allow solo con lease raíz "."
  ambiguous_shell_command → deny
  destructive/direct-base-push → deny aunque exista lease

enforce:
  mismas barreras + todos los gates mecánicos requeridos
```

Tests cubren `git status`, `rg`, unittest/pytest, xcodebuild, npm test,
SwiftFormat, generador con output, binario desconocido y shell compuesto, con
lease preciso, raíz, ausente y de otra identidad. Cubren además cada operation
ID de `git_effect`, argv directo frente a shell compuesto, task/state/lease/
effect/grant correctos, replay, remote/base/branch/HEAD drift y operación
desconocida; commit, push, fetch y fast-forward quedan denegados si no pasan por
su wrapper exacto.

Un forward-test ejecuta dos procesos hook reales y solo se considera PASS si el
host conserva y consume su aprobación one-shot en el mismo `tool_use_id`; una
factoría test-only en un único proceso no satisface este gate.

- [ ] **Step 6: Mantener límites**

Run:

```bash
python3 -m unittest \
  tests.test_hooks tests.test_cli_v2 tests.test_lockfile \
  tests.test_adoption -v
scripts/control-plane doctor
```

Expected: las mismas suites del RED y doctor vuelven PASS; source e isolated
runtime exponen `safe-read`, y outputs serializados son menores de 4096 bytes.
Aquí solo se prueba el contrato del smoke con fixtures; el PASS Darwin real y
la revisión humana no se simulan y son gates explícitos de Task 12.

Antes de devolver cada payload hook, medir su UTF-8 serializado y registrar
`hook_output_bytes` con la API runtime del ledger. El tool input no puede
declarar ni sobrescribir esa cifra.

- [ ] **Step 7: Commit coherente**

Usar los efectos stage/commit gobernantes, grants distintos y esta allowlist:

```text
control_plane/hooks.py
control_plane/cli.py
control_plane/host_bridge.py
control_plane/adoption.py
tests/test_hooks.py
tests/test_cli_v2.py
tests/macos_hook_smoke.py
tests/test_adoption.py
.codex/hooks.json
.codex/control-plane.lock
tests/test_lockfile.py
```

Mensaje cerrado: `Warn once and block curated unsafe actions`. Reobservar
`LocalGitObservation`; no ejecutar stage/commit raw.

## Task 8: Crear guards Git y adopción reversible

**Files:**
- Create: `control_plane/git_guards.py`
- Create: `tests/test_git_guards.py`
- Create: `.codex/git-hooks/pre-commit`
- Create: `.codex/git-hooks/pre-push`
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/risk_sentinel.py`
- Modify: `control_plane/cli.py`
- Modify: `control_plane/adoption.py`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_adoption.py`
- Modify: `tests/test_risk_sentinel.py`
- Modify: `tests/test_cli_v2.py`
- Modify: `tests/test_lockfile.py`
- Create: `tests/test_risk_integration.py`

- [ ] **Step 1: Escribir RED de guards**

Tests:

```text
test_pre_commit_blocks_base_and_detached_but_allows_feature
  Expect: base/detached exit 1; feature válida exit 0.

test_pre_push_blocks_every_base_update_and_deletion
  Expect: cualquier update o zero-SHA delete de refs/heads/<base> falla.

test_pre_push_allows_feature_fast_forward
  Expect: update demostrablemente fast-forward de feature pasa.

test_pre_push_allows_new_feature_and_feature_deletion
  Expect: zero SHA se interpreta por columna/ref; no confundir create/delete.

test_pre_push_blocks_proven_non_fast_forward
  Expect: update demostrablemente no-fast-forward falla.

test_pre_push_consumes_every_line_and_rejects_malformed_or_oversized_input
  Expect: una línea peligrosa entre varias bloquea; formato != cuatro campos o
  stdin > 1 MiB falla cerrado.

test_launchers_run_as_real_processes
  Expect: ambos scripts invocan el CLI real; base update/delete bloquea y
  feature fast-forward pasa.

test_invalid_policy_or_unobservable_state_fails_closed
  Expect: config o estado no observable devuelve error estable y exit 1.

test_candidate_policy_or_hook_cannot_weaken_installed_guard
  Given: feature cambia base/remote, policy+candidate lock y
  `.codex/git-hooks/pre-push` coordinadamente.
  Expect: el hook instalado fuera del worktree sigue cargando ProtectedGitPolicy
  del manifest gobernante, informa candidate drift y bloquea base push.

test_protected_git_policy_requires_explicit_migration
  Expect: solo adopt/upgrade transaccional autorizado puede cambiar manifest,
  runtime/hook digests o base/remote protegidos; editar el repo no migra nada.

test_installed_policy_observation_is_manifest_bound_one_shot
  Expect: manifest/runtime/hooks/repo/session/invocation/TTL exactos producen
  ValidatedInstalledPolicyObservation; mapping, replay, drift o candidate
  coordinada no entran en load_governing_local_policy.
```

- [ ] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest tests.test_git_guards -v
```

Expected: FAIL por ausencia del módulo.

- [ ] **Step 3: Implementar API**

```text
load_protected_git_policy(
  *,
  canonical_repo,
  common_git_dir,
  installed_manifest_digest,
  invocation_id,
  clock
) -> ProtectedGitPolicy

observe_installed_policy_source(
  *,
  protected_policy: ProtectedGitPolicy,
  canonical_repo,
  expected_manifest_digest,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> InstalledPolicyObservation

validate_installed_policy_source(
  observation: InstalledPolicyObservation,
  *,
  expected_repository_identity,
  expected_manifest_digest,
  expected_invocation_id,
  clock
) -> ValidatedInstalledPolicyObservation

guard_pre_push(
  repo: Path,
  protected_policy: ProtectedGitPolicy,
  *,
  remote_name: str,
  remote_url: str,
  updates: Iterable[tuple[str, str, str, str]]
) -> dict[str, Any]

guard_pre_commit(
  repo: Path,
  protected_policy: ProtectedGitPolicy
) -> dict[str, Any]
```

`ProtectedGitPolicy` es opaca y one-shot; se carga del manifest de instalación
durable bajo el Git common-dir y liga base, remote, repository identity,
governing policy blob/digest, lock/runtime/hook digests e invocation. El loader
revalida manifest/runtime/hooks antes de devolverla. No acepta
`.codex/project-policy.toml` del worktree, Mapping, env ni PATH. El guard compara
la policy candidata solo para mostrar `GG_CANDIDATE_POLICY_DRIFT`; nunca deja
que esa candidata gobierne su propio commit/push.

Task 8 implementa así la segunda fuente prometida por Task 6. Solo después de
validar manifest/runtime/hooks y consumir
`ValidatedInstalledPolicyObservation`, `load_governing_local_policy()` amplía
su entrada a la unión
`ValidatedLocalBaseObservation | ValidatedInstalledPolicyObservation`. Tests
prueban source instalada, replay, manifest drift, candidate coordinada,
cross-repo y que el tipo no existía como shortcut en el commit de Task 6.

El contrato devuelve:

```json
{
  "schema_version": 1,
  "command": "git-guard",
  "ok": false,
  "event": "pre-push",
  "errors": [
    {
      "code": "GG_BASE_PUSH",
      "message": "Direct updates to the configured base are forbidden."
    }
  ]
}
```

Cablear parser y handlers:

```text
git-guard pre-commit --repo PATH [--json]
git-guard pre-push --repo PATH
  --remote-name NAME --remote-url URL [--json]
```

El handler `pre-push` lee stdin completo una sola vez, con límite 1 MiB,
procesa todas las líneas no vacías y exige exactamente:

```text
local-ref local-oid remote-ref remote-oid
```

OID cero se interpreta según local/remote y nunca se pasa como ancestro a Git.
Todo destino `refs/heads/<base>` se bloquea en cualquier remote; discrepancia
entre nombre/URL del remote configurado y el observado queda además como
`GG_REMOTE_UNVERIFIED`. Input truncado, malformado o estado Git no observable
falla cerrado.

- [ ] **Step 4: Crear launchers POSIX**

La fuente versionada `.codex/git-hooks/pre-commit` se empaqueta durante
adopción en un snapshot gestionado fuera de cualquier worktree:

```sh
#!/bin/sh
set -eu
repo=$(git rev-parse --show-toplevel)
exec "<installed-runtime>/scripts/control-plane" git-guard pre-commit \
  --repo "$repo"
```

La fuente versionada `.codex/git-hooks/pre-push` se empaqueta igual:

```sh
#!/bin/sh
set -eu
repo=$(git rev-parse --show-toplevel)
exec "<installed-runtime>/scripts/control-plane" git-guard \
  pre-push \
  --repo "$repo" \
  --remote-name "$1" \
  --remote-url "$2"
```

`<installed-runtime>` es un path absoluto saneado bajo
`<git-common-dir>/codex-control-plane/installs/<manifest-digest>/`, resuelto y
renderizado por adopción; no es placeholder en el artefacto instalado. El
manifest `0600` y los launchers ejecutables se publican temp/fsync/replace bajo
flock antes de cambiar `core.hooksPath`, que apunta al directorio instalado, no
a `.codex/git-hooks` del checkout. La feature puede cambiar las fuentes
versionadas, pero no el guard activo sin `upgrade apply` autorizado.

- [ ] **Step 5: Extender adopción con config digestada**

`adoption_plan()` añade:

```json
{
  "git_config_changes": [
    {
      "key": "core.hooksPath",
      "observed_records": [],
      "previous_local_values": [],
      "planned_value": "<common-dir>/codex-control-plane/installs/<digest>/git-hooks"
    }
  ]
}
```

Observar con:

```bash
git config --show-origin --show-scope --get-all core.hooksPath
git config --local --get-all core.hooksPath
git config --worktree --get-all core.hooksPath
```

Capturar todos los valores, orígenes y scopes. Si hay múltiples definiciones,
un valor heredado global/system/include, una definición worktree no gestionada
o un valor local diferente:

```text
E_ADOPT_HOOK_PATH_CONFLICT
```

No encadenar, sombrear ni “restaurar” como local un gestor heredado. La v2.1
solo muta una ausencia local demostrada o su propio valor local idempotente.

Aunque `core.hooksPath` esté ausente, inspeccionar el directorio default del Git
common dir. Cualquier hook ejecutable no gestionado, excepto `*.sample`, bloquea
el plan antes de mutar con `E_ADOPT_EXISTING_HOOKS`. No desactivarlo
silenciosamente al fijar la nueva ruta.

- [ ] **Step 6: Hacer apply/verify/rollback transaccional**

Orden de apply:

```text
prevalidar plan, config y governing policy
→ preparar backups
→ escribir snapshot runtime/hooks + ProtectedGitPolicy manifest fuera del worktree
→ verificar digests/permisos y fsync
→ fijar core.hooksPath
→ verificar config
→ marcar applied
```

Ante fallo, restaurar archivos y config. Rollback prevalida todo antes de la
primera mutación y elimina exactamente la definición local creada, dejando que
la configuración heredada vuelva a operar; nunca copia un valor global/include
al scope local. Aplicar el mismo
`git_config_changes`, journal, rollback y fault injection a `upgrade_plan()` y
`upgrade_apply()`; no asumir que el camino upgrade hereda los records de adopt.

- [ ] **Step 7: Probar fault injection y worktrees**

Añadir pruebas:

```text
test_adoption_installs_guards_and_config
  Expect: snapshot externo exacto, launchers ejecutables, policy/manifest digest
  verificado y hooksPath gestionado fuera del worktree.

test_adoption_refuses_existing_unmanaged_hook_path
  Expect: conflicto previo detiene apply antes de mutar.

test_adoption_refuses_executable_default_hook
  Expect: hook default no gestionado detiene plan aunque hooksPath esté ausente.

test_rollback_restores_absent_and_existing_config_exactly
  Expect: ausencia local vuelve a ausencia; valor local gestionado idempotente
  permanece exacto.

test_global_include_and_worktree_hook_paths_block_without_mutation
  Expect: cada origen/scope heredado o múltiple devuelve conflicto y no queda
  sombreado por una escritura local.

test_fault_injection_restores_files_and_git_config
  Expect: cada punto de fallo deja estado igual al snapshot anterior.

test_upgrade_fault_injection_restores_files_and_git_config
  Expect: plan/apply/rollback de upgrade conserva instalación anterior exacta.

test_shared_core_hooks_path_limitation_is_reported_for_worktrees
  Expect: plan/status avisan del alcance común y no prometen aislamiento falso.
```

- [ ] **Step 8: Ejecutar GREEN**

Añadir `git_guards.py` a `RUNTIME_MODULES`, regenerar el lock e incluir guards,
config y runtime aislado en el test de adopt/upgrade/rollback.

Run:

```bash
python3 -m unittest \
  tests.test_git_guards \
  tests.test_adoption \
  tests.test_risk_sentinel \
  tests.test_cli_v2 \
  tests.test_risk_integration \
  tests.test_lockfile \
  -v
scripts/control-plane doctor
```

Expected: todos PASS.

- [ ] **Step 9: Commit coherente**

Los dos efectos gobernantes existen desde Task 1 y Task 7 ya los reconoce en
hooks sin redefinir su autoridad. Usar `stage_allowlisted_paths` y después
`commit_staged_change`, con preflight, inventory, lease y grants
`local_write`/`commit` distintos. Allowlist exacta:

```text
control_plane/git_guards.py
control_plane/host_bridge.py
control_plane/risk_sentinel.py
control_plane/cli.py
control_plane/adoption.py
.codex/control-plane.lock
tests/test_git_guards.py
tests/test_adoption.py
tests/test_risk_sentinel.py
tests/test_cli_v2.py
tests/test_risk_integration.py
tests/test_lockfile.py
.codex/git-hooks/pre-commit
.codex/git-hooks/pre-push
```

Mensaje cerrado: `Install reversible local Git guards`. Reobservar
`LocalGitObservation` y cerrar la ronda; no ejecutar add/commit raw.

## Task 9: Añadir procedencia post-push en GitHub Actions

**Files:**
- Create: `control_plane/github_provenance.py`
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/lifecycle.py`
- Modify: `control_plane/risk_sentinel.py`
- Modify: `control_plane/routing.py`
- Modify: `control_plane/resource_registry.py`
- Modify: `tests/test_lifecycle.py`
- Modify: `tests/test_risk_sentinel.py`
- Modify: `.codex/resource-registry.toml`
- Modify: `tests/test_resource_registry.py`
- Modify: `tests/test_routing.py`
- Modify: `.codex/project-policy.toml`
- Modify: `.codex/control-plane.lock`
- Modify: `control_plane/policy.py`
- Modify: `tests/test_policy.py`
- Modify: `control_plane/cli.py`
- Modify: `tests/test_cli_v2.py`
- Modify: `tests/test_risk_integration.py`
- Create: `.github/workflows/risk-sentinel.yml`
- Create: `.codex/templates/risk-sentinel.yml.tmpl`
- Modify: `control_plane/adoption.py`
- Modify: `tests/test_adoption.py`
- Modify: `tests/contract_support.py`
- Modify: `tests/test_repository_contract.py`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Escribir RED de GitHub degradado**

Tests con cliente HTTP inyectado:

```text
test_every_introduced_commit_from_merged_pr_is_pass
  Expect: todos los commits asociados a PR merged hacia base producen PASS.

test_empty_pr_association_remains_unknown_after_bounded_retries
  Expect: HTTP 200 vacío tras cualquier ventana finita produce
  UNKNOWN/RS_REMOTE_ASSOCIATION_NOT_YET_OBSERVED, no acusa push directo.

test_one_positively_contradictory_commit_in_multi_commit_push_is_fail
  Expect: FAIL solo si forced/delete o, tras enumeración completa, el conjunto
  exhaustivo demuestra una contradicción exclusiva sin candidato compatible;
  asociación ausente o ambigua conserva UNKNOWN.

test_forced_or_deleted_base_is_fail
  Expect: forced=true o deleted=true en base produce FAIL.

test_unauthorized_rate_limited_timeout_and_server_error_are_unknown
  Expect: 401/403/429/5xx/timeout producen UNKNOWN, nunca PASS.

test_malformed_or_truncated_pagination_is_unknown
  Expect: cualquier duda de completitud produce UNKNOWN.

test_compare_requires_complete_unique_commit_enumeration
  Expect: count exacto, sin duplicados y dentro del cap; discrepancia UNKNOWN.

test_pr_association_paginates_and_retries_eventual_consistency
  Expect: páginas completas de 100; asociación vacía se reintenta tres veces
  antes de UNKNOWN; una observación posterior asociada produce PASS.

test_mixed_pr_associations_search_all_pages_before_deciding
  Given: un PR open/base distinta en página 1 y el PR merged compatible en una
  página posterior, además de permutaciones del mismo conjunto.
  Expect: PASS si existe el candidato compatible único/suficiente; nunca FAIL
  temprano por el primer elemento. Con dos candidatos compatibles ambiguos o
  paginación incompleta, UNKNOWN.

test_repository_ref_and_event_identity_must_match_policy
  Expect: GITHUB_REPOSITORY, event repo, identidad canónica de policy y ref/base deben
  coincidir; discrepancia es FAIL e identidad no observable UNKNOWN.

test_second_push_cannot_cancel_first_provenance_observation
  Expect: workflow sentinel sin cancel-in-progress para eventos push de base.

test_adoption_renders_target_base_without_hardcoded_main
  Expect: workflow del target usa policy.base_branch exacta y rollback lo retira.

test_zero_sha_without_complete_proof_is_unknown
  Expect: before zero sin enumeración completa no se interpreta como PASS.

test_unsupported_merge_topology_is_unknown_without_overclaiming_ui_method
  Expect: dos parents o rebase multicommit producen
  RS_REMOTE_STRATEGY_UNSUPPORTED. Un único commit con parent base exacto se
  etiqueta squash_compatible, no “botón squash demostrado”.

test_github_token_is_never_serialized
  Expect: canario ausente de result, error, repr, stdout y stderr.

test_actions_token_and_local_gh_use_separate_transports
  Given: Actions con token de entorno y provider local con gh preautenticado,
  además de canarios en GH_TOKEN/GITHUB_TOKEN y host enterprise unsupported.
  Expect: `UrllibTokenTransport` es la única ruta que recibe token;
  `GhCliTransport` sanea esas variables antes del subprocess, nunca las
  solicita, lee ni imprime y ambos alimentan los mismos evaluadores puros. Sin
  auth preexistente después del saneado, queda pending sin fallback; GHES queda
  pending antes de tocar credenciales.

test_github_response_contract_is_closed_bounded_and_sanitized
  Given: status/header/body normales frente a status inválido, body cap+1,
  JSON no object/list, Link malformado o headers sensibles.
  Expect: solo el primero produce GitHubResponse; nunca conserva
  authorization, cookie, request URL cruda ni bytes fuera del cap.

test_github_transports_have_response_and_error_parity
  Given: fixtures idénticos para success, non-2xx, timeout, overflow, JSON
  inválido, Link/pagination y rate-limit.
  Expect: UrllibTokenTransport y GhCliTransport producen el mismo
  GitHubResponse o código UNKNOWN saneado para los evaluadores puros.

test_gh_cli_transport_real_subprocess_argv_preserves_get_headers_and_pagination
  Given: fake `gh` ejecutable realista que registra argv y devuelve framing
  `--include`, Link y status.
  Expect: argv directo contiene `gh api --include --method GET`; parámetros de
  query usan `-f` solo tras fijar GET, no existe shell y status/headers/body se
  parsean una vez con los mismos caps que urllib.

test_actions_endpoint_binding_blocks_cross_host_token_before_network
  Given: github.com frente a GHES, GITHUB_SERVER_URL/GITHUB_API_URL
  inconsistentes, Unicode, userinfo o redirect cross-host.
  Expect: v2.1 solo crea GitHubEndpointBinding para
  `https://github.com` + `https://api.github.com`; GHES queda
  pending_remote_host_unsupported y cualquier mismatch falla antes de leer o
  envolver token, DNS o request.

test_transport_provider_is_doctored_host_bound_and_one_shot
  Given: provider instalado/preautenticado exacto frente a mapping, candidate
  runtime, hostname/repo/auth/session/invocation/TTL distinto o replay.
  Expect: solo `approve_github_transport_provider()` emite el wrapper consumible;
  el resto queda pending antes de consultar PR/checks.

test_github_observation_advances_only_exact_task_state
  Given: GitHubClient fake observa PR/head/checks/merge/base exactos.
  Expect: GitHubObservation host-bound promueve pr_draft, pr_ready, merged y
  base_verified; mapping equivalente, replay o HEAD/task/repo distinto no.

test_pilot_pr_mutation_requires_latest_pushed_context_and_write_provider
  Given: PilotTaskContext pushed actual, provider write gobernante y grant
  pull_request frente a committed/generation vieja, provider read-only,
  mapping, PR/repo/base/head/draft o session drift.
  Expect: solo `build_pilot_pull_request_mutation_request()` crea/update el PR
  D exacto; produce observación validada antes de avanzar a pr_draft.

test_pr_ready_requires_exact_pr_identity_and_complete_required_checks
  Given: PR number/repo/base/head-ref/head-SHA exactos y policy.required_checks.
  Expect: solo state=open, draft=false y cada check obligatorio único,
  completed y con conclusión admitida promueven pr_ready.

test_pr_ready_rejects_partial_stale_ambiguous_or_wrong_pr_evidence
  Given: >100 checks, missing/pending/failing, rerun activo, duplicado, fork,
  PR distinto, draft, head/base/ref/SHA stale o paginación incompleta.
  Expect: no promoción; contradicción positiva FAIL y completitud dudosa UNKNOWN.

test_check_run_caps_and_changing_totals_are_unknown
  Given: cap-1, cap, cap+1, total_count que cambia entre páginas o Link que
  anuncia una página adicional tras el cap.
  Expect: cap-1/cap exactos pueden completar; overflow, total inestable o página
  extra son UNKNOWN y nunca omiten checks silenciosamente.

test_adoption_required_checks_need_typed_project_decision
  Given: candidates CLI frente a ProjectRemotePolicyDecision host-bound.
  Expect: el plan muestra candidates, pero apply solo escribe identidad/checks
  del wrapper ligado al plan digest exacto.

test_adoption_rejects_edited_or_self_confirmed_remote_policy_plan
  Given: JSON editado, digest copiado, check añadido/quitado o campo
  confirmed=true aportado por caller.
  Expect: pending_remote_policy_configuration y cero mutación.

test_current_repo_remote_policy_migration_requires_native_decision_before_mutation
  Given: identidad/check candidate exactos del repo actual frente a ausencia,
  evento nativo stale/cross-target o JSON auto-confirmado.
  Expect: `.codex/project-policy.toml` solo incorpora repository_identity y
  required_checks mediante la operación policy-only después de
  `ProjectRemotePolicyDecision` host-bound más autorización local_write
  separada; no ejecuta adopción completa ni toca otros managed files.

test_required_check_selector_cli_is_closed_and_repeatable
  Expect: NAME:APP:CONCLUSION[,CONCLUSION] ASCII válido se normaliza; vacío,
  duplicado, conclusión fuera de allowlist, Unicode/confusable o campo extra
  falla antes de generar plan.

test_local_github_provider_observes_and_consumes_in_one_process
  Given: provider preautenticado inyectado, task/target y repo worktree-local.
  Expect: lee task/policy, consulta GitHub, crea GitHubObservation y llama
  TaskStore.advance_from_github sin serializar facts ni token.

test_candidate_policy_cannot_govern_its_own_pull_request
  Given: la feature elimina verify, admite skipped, cambia base/remote/provider
  o altera integration_strategy.
  Expect: provider usa GoverningPolicy del base commit verificado o devuelve
  UNKNOWN; candidate_policy_digest solo informa drift y nunca reduce gates.

test_governing_policy_is_loaded_from_verified_base_object
  Given: governing_base_commit fijo y worktree con policy distinta.
  Expect: base, remote, required_checks y estrategia proceden de
  `.codex/project-policy.toml` leído del objeto Git de ese commit, con digest
  exacto; mismatch u objeto ausente bloquea antes de red.

test_governing_runtime_executes_from_clean_base_attestor
  Given: candidate modifica provider y lock de forma coordinada.
  Expect: detached attestor en governing_base_commit valida launcher/runtime/
  provider/lock antes del import y bloquea el candidate drift antes de red o
  TaskStore; no acepta digests escalares del runtime evaluado. La observación
  liga session/invocation/TTL, es one-shot y replay/cross-target falla.

test_premerge_pr_and_base_ref_sha_must_equal_governing_base
  Given: pr_draft/pr_ready con PR.base.sha o live base SHA distinto del
  governing_base_commit.
  Expect: observaciones/checks se invalidan y no hay pr_ready.

test_postmerge_base_can_advance_exactly_to_merge_commit
  Given: mismo PR/head/revision merged, governing base ancestro y live base ref
  exactamente en merge_commit; strategy=squash y parent único de merge_commit
  exactamente governing_base_commit.
  Expect: merged y base_verified pueden avanzar con observaciones separadas.

test_intermediate_base_advance_before_squash_merge_invalidates_pilot
  Given: G era governing/pr_ready, base avanza a B y el squash M tiene parent B,
  aunque live base termine exactamente en M y G sea ancestro.
  Expect: BASE_ADVANCED_BEFORE_MERGE/UNKNOWN; no merged/base_verified, hint ni
  capability.

test_unrelated_or_stale_postmerge_base_advance_is_unknown
  Given: live base antes/no contiene merge o avanzó más allá antes de verificar.
  Expect: UNKNOWN/BASE_ADVANCED_AFTER_MERGE y no hint/capability.

test_base_advance_always_requires_a_new_task_and_pilot
  Given: base avanza con policy/runtime idénticos frente a policy/provider/gates
  distintos.
  Expect: ningún contexto rota in-place; task ordinaria y D se cierran/suspenden
  y se reencuadran con task/decision/lease/GoverningPolicy nuevos.

test_policy_change_requires_specialized_transition_and_future_task
  Expect: policy_change_pending impide auto-promoción del PR candidato; solo una
  task creada desde el squash ya contenido en origin/base puede usarla como
  governing.

test_pr_c_provider_is_shadow_and_pr_d_is_first_authoritative_forward_test
  Expect: C no muta lifecycle aunque el shadow sea PASS; D usa runtime/policy
  de base C, diff fuera de control-plane y entra en authority_mode=pilot.

test_manual_merge_bootstrap_observation_is_host_bound_and_exact
  Given: attestor limpio en la base que ya contiene C reobserva el PR C exacto,
  base/head/checks, state=merged, strategy=squash y mergeCommit live.
  Expect: emite `ValidatedManualMergeObservation` one-shot ligado a
  repo/PR/base/head/merge/provider/runtime/lock/session/TTL.

test_manual_merge_bootstrap_mapping_stale_replay_or_drift_is_rejected
  Given: receipt/JSON/env, PR/base/head/merge distinto, base avanzada, replay,
  TTL vencido, paginación incompleta o provider/runtime/lock no gobernante.
  Expect: no wrapper y D no obtiene governing_base_commit; esta observación
  nunca llama `TaskStore.advance_from_github()` ni concede autoridad.

test_pilot_mode_is_task_base_policy_lock_and_path_bound
  Given: task distinto de D, path fuera del charter, base/policy/lock distinto o
  intento de reutilizar pilot en otra task.
  Expect: PILOT_BINDING_MISMATCH y ninguna transición/capability global.

test_start_authority_pilot_requires_one_shot_host_authorization
  Given: TrustedPilotAuthorization exacta frente a mapping/CLI/raw state,
  task/branch/scope/base/policy/lock/runtime/provider/session/nonce distinto,
  TTL o replay.
  Expect: solo el wrapper host-bound inicia D; cualquier otro caso falla antes
  de crear TaskStore/lease.

test_pilot_inputs_are_recomputed_from_task_inventory_policy_and_registry
  Given: TaskEnvelope serializable válido más inventory host-bound frente a
  route mapping/digest forjado, stale inventory o policy/registry distinto.
  Expect: `build_validated_pilot_inputs()` ejecuta schema+resolver en el mismo
  proceso y solo emite wrapper opaco para la decisión exacta.

test_start_pilot_consumes_manual_merge_observation_not_scalar_sha
  Given: ValidatedManualMergeObservation fresca frente a merge SHA/env/mapping,
  replay o wrapper de otro runtime/session/invocation.
  Expect: solo el wrapper exacto fija governing_base_commit; el caller no puede
  pasar expected_head/base como autoridad.

test_pilot_local_commit_rebinds_head_without_weakening_other_bindings
  Given: PilotTaskContext en base G y LocalGitObservation exacta del commit D
  allowlisted frente a mapping, replay, otro prior/new HEAD o path extra.
  Expect: solo desde review_ready,
  `advance_pilot_local_commit()` publica committed y devuelve generación nueva
  ligada a D; task/session/base/policy/runtime/provider/scope permanecen
  byte-idénticos.

test_pilot_local_lifecycle_cannot_skip_verification_commit_or_push
  Given: observaciones host-bound exactas frente a llamadas directas
  implementing→committed, review_ready→pushed o committed→pr_draft.
  Expect: la única secuencia válida es
  implementing→verifying→review_ready→committed→pushed; mapping, replay,
  contexto/generation stale o evidencia incompleta falla sin transición.

test_pilot_push_requires_exact_remote_branch_observation
  Given: PilotTaskContext committed y LocalGitObservation fresca que demuestra
  tree/index limpios y origin/feature==current_head frente a remote/base/HEAD
  distintos, push no observado o mapping.
  Expect: solo `advance_pilot_push()` publica pushed; el provider rechaza
  pr_draft desde cualquier estado anterior.

test_fetch_policy_remote_requires_dual_authority_and_common_dir_guard
  Given: grants one-shot exactos `network_read` + `local_write`, inventario
  fresco y `RemoteRefMutationGuard` ligado al remote/base/preimage frente a
  ausencia, replay, scope distinto o intento por la factoría Git genérica.
  Expect: `prepare_remote_ref_mutation()` es side-effect-free; solo
  `build_fetch_policy_remote_request()` revalida/arma la guarda y consume ambos
  grants de forma atómica. La factoría genérica rechaza esta operación y, si
  falta uno, ninguno se consume ni se publica marker o efecto.

test_fetch_policy_remote_race_or_crash_never_yields_positive_base_evidence
  Given: otra sesión cambia la ref bajo observación entre prepare, Git y
  reobserve, o el proceso cae tras publicar intent/tras ejecutar fetch.
  Expect: recovery/reobserve marca STALE o UNKNOWN, nunca PASS; no se ejecuta
  subprocess bajo flock, el marcador es recuperable y una prueba positiva exige
  una invocation nueva con preimage y postimage coherentes.

test_pilot_finalize_requires_latest_context_generation_and_head
  Given: contexto inicial G frente al último contexto D después del rebind.
  Expect: G devuelve PILOT_CONTEXT_STALE y no libera; solo D/generation actual
  puede finalizar y liberar el lease exacto.

test_generic_task_start_cannot_select_pilot_mode
  Expect: CLI/schema no exponen authority_mode; JSON con pilot falla cerrado.

test_pilot_does_not_mark_provider_ready_before_base_verified
  Given: D en pr_draft, pr_ready o merged.
  Expect: solo su TaskStore avanza; provider_capability sigue pending_pilot.

test_pilot_finalize_always_releases_writer_lease
  Given: capability validada, pending en base_verified, base avanzada entre
  merged/base_verified y abort antes de merge.
  Expect: success cierra base_verified; carrera conserva blocked/resume=merged
  sin outcome falso; pending/abort conserva razón y resume_state auditables;
  todos liberan exactamente el lease owner-bound y ninguna reanudación
  posterior puede escribir sin task/piloto nuevo.

test_finalized_or_pending_pilot_cannot_use_generic_resume
  Given: success, pending post-merge y abort con
  `pilot_finalized=true/resume_forbidden=true`.
  Expect: `TaskStore.resume()` devuelve E_PILOT_FINALIZED; solo una task/piloto
  nueva puede escribir.

test_pilot_finalize_crash_never_leaves_released_lease_with_writable_state
  Given: crash tras publicar finalizing, tras tombstone/unlink y antes del state
  final.
  Expect: recovery idempotente completa release/final state; `finalizing`
  bloquea toda escritura y nunca coincide lease ausente con estado reanudable.

test_capability_hint_survives_pilot_worktree_removal
  Given: D alcanza base_verified, se publica un hint bajo git-common-dir y
  se poda su worktree.
  Expect: otro worktree puede reobservar la cadena; no depende del Git dir
  eliminado ni confía en el hint.

test_capability_hint_revalidation_invalidates_on_drift
  Given: upgrade, lock/policy/provider drift, base sin merge piloto o mapping
  forjado.
  Expect: capability stale/pending y nuevo piloto requerido; nunca ready por
  mera existencia.

test_workflow_provenance_observation_is_host_bound_and_exact
  Given: attestor consulta el workflow/run post-merge exacto de D.
  Expect: wrapper liga repo, workflow path/blob digest, push event, run/attempt,
  merge/base SHA, PASS, task/provider/TTL y se consume una vez.

test_workflow_provenance_missing_ambiguous_stale_or_cross_context_is_pending
  Given: cero o varios runs, rerun no último, workflow/path/digest distinto,
  run stale, cross-repo/SHA, replay o conclusión UNKNOWN/FAIL.
  Expect: no observación válida ni hint/capability; nunca se acepta JSON/receipt de
  Actions.

test_workflow_runs_attempts_and_jobs_require_complete_bounded_enumeration
  Given: cap-1, cap, cap+1, run candidato en página posterior, total_count o
  Link cambiante, IDs duplicados, varios run_attempt y job duplicado.
  Expect: solo enumeración completa, única y estable selecciona el run y su
  último attempt; overflow, truncación o ambigüedad quedan UNKNOWN.

test_workflow_job_identity_and_conclusion_mapping_are_exact
  Given: job key `risk-sentinel`, display name correcto/incorrecto, status y
  conclusion de GitHub y exit 0/1/2 del comando.
  Expect: solo display name `risk-provenance`, status `completed` y conclusion
  `success` demuestran el PASS interno producido por exit 0; la cadena no busca
  una conclusion GitHub llamada PASS.

test_forged_capability_hint_with_all_public_digests_never_grants_authority
  Given: archivo project-wide fabricado con schema, IDs y todos los digests
  públicos correctos, pero sin reobservación live del PR/diff/merge/run/job.
  Expect: es solo selector no confiable; falta o contradicción remota deja
  pending y nunca produce ValidatedProviderCapability.

test_missing_local_github_provider_is_pending_not_success
  Expect: pending_github_host_adapter; PR C/D no afirman base_verified.

test_integration_outcome_route_selects_concrete_remote_proof_provider
  Given: TaskEnvelope intent=integrate, phase=integrate, outcome=integration y
  effect integration/network_read frente a provider gh ready, solo connector
  ready o ninguno ready.
  Expect: `remote-integration-proof` requiere `git.remote-proof`; RouteDecision
  liga exactamente el resource ID+digest del provider seleccionado para una
  task ordinaria, o deja unresolved/pending. Nunca instancia un provider no
  seleccionado.

test_local_gh_provider_precedes_optional_connector_only_when_ready
  Expect: `host.github-gh-read` ready/canónico sombrea
  `mcp.github-pr-read`; si está unavailable, el connector autorizado/ready puede
  sustituirlo para rutas generales read-only. Dos candidatos no ordenables o
  digest ambiguo fallan cerrado.

test_authority_pilot_requires_exact_host_gh_provider
  Given: PR D frente a host.github-gh-read ready, solo mcp.github-pr-read ready,
  provider distinto o ID/digest drift.
  Expect: únicamente `host.github-gh-read` exacto habilita el attestor/piloto;
  connector-only queda `pending_github_host_adapter`. Nunca se intenta
  serializar un connector a través del CLI/JSON del control plane.

test_provider_identity_is_bound_from_route_through_observation_and_receipt
  Expect: provider ID/digest distinto entre RouteDecision, transport wrapper,
  GitHubObservation o ResourceUseReceipt invalida la operación antes de red o
  transición.

test_local_base_reconciliation_fast_forwards_only_exact_clean_registered_worktree
  Given: base_verified, un worktree base registrado/clean/behind frente a
  absent, duplicated, dirty, detached, ahead/diverged, cross-repo o race.
  Expect: solo el primero produce candidato FF; los demás emiten
  LOCAL_BASE_NOT_SYNCED con razón cerrada y cero mutación.

test_local_base_sync_requires_separate_local_write_authorization_and_lease
  Expect: autorización one-shot subject=local_base_sync, task
  operate/local_change, lease/worktree/base/heads exactos y revalidación
  inmediata permiten `merge --ff-only`; mapping/replay/drift falla sin reset,
  checkout, pull, rebase ni limpieza.

test_post_merge_cleanup_defaults_to_visible_retention_without_native_decision
  Given: base_verified pero ningún `NativeUserInteractionEvent` de cleanup.
  Expect: cero mutación, decisión efectiva retain y aviso
  POST_MERGE_CLEANUP_PENDING con worktree/rama/remote exactos y siguiente paso.

test_post_merge_cleanup_requires_exact_proof_cleanliness_and_no_live_lease
  Given: PR/head/base_verified exactos frente a estado pre-merge, feature HEAD
  distinto al PR head, árbol dirty, child/lease vivo, worktree ambiguo o race.
  Expect: solo el primer conjunto limpio puede producir plan; cualquier otro
  conserva todos los recursos y publica razón cerrada sin force/prune.

test_post_merge_cleanup_local_order_and_remote_authority_are_separate
  Given: remove_local o remove_local_and_remote con grants separados.
  Expect: retirar worktree limpio precede a borrar rama local; la feature squash
  exige `delete_validated_local_feature` con CAS sobre el PR head, no
  `delete_ephemeral_branch`. Borrar la remota exige otro grant `remote_write` y
  `delete_validated_remote_feature`; su ausencia/replay/drift retiene esa ref
  sin deshacer el cierre local ni afirmar cleanup completo.

test_post_merge_cleanup_squash_uses_pr_head_not_ancestor_membership
  Given: PR fusionado por squash, feature HEAD exacto y merge commit nuevo.
  Expect: elegibilidad deriva de PR identity/head + merge/base proof;
  `delete_validated_local_feature` ejecuta CAS `update-ref -d` con old OID
  exacto, no exige ancestry ni `branch -D`, y nunca confunde una rama ajena con
  la rama integrada. `delete_ephemeral_branch` rechaza esta rama.

test_ci_remote_provenance_ignores_detached_local_checkout_dimension
  Given: checkout Actions detached/base sin hooksPath local y provenance remota PASS.
  Expect: `risk-provenance` sale 0 por remote PASS; no agrega local FAIL/UNKNOWN.
```

- [ ] **Step 1b: Crear un scaffold importable, todavía rojo**

Antes del primer RED, crear `control_plane/github_provenance.py` con las
constantes, dataclasses/interfaces y firmas exactas que importan los tests, pero
sin implementación funcional. Cada método devuelve un resultado explícito
`UNKNOWN/RS_REMOTE_NOT_IMPLEMENTED` o lanza `NotImplementedError` solo al ser
invocado; importar o descubrir tests debe funcionar. Añadir el módulo a
`RUNTIME_MODULES` y regenerar el lock provisional en el mismo cambio de trabajo,
sin commit todavía. No introducir una implementación suficiente para volver
verde ninguna garantía nueva.

- [ ] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest \
  tests.test_risk_sentinel \
  tests.test_lifecycle \
  tests.test_resource_registry \
  tests.test_routing \
  tests.test_cli_v2 \
  tests.test_policy \
  tests.test_risk_integration \
  tests.test_adoption \
  tests.test_repository_contract \
  -v
```

Expected: cada RED nuevo falla por la garantía concreta ausente; los tests
preexistentes de esas suites permanecen verdes. Un import/collection error no
cuenta como RED válido. Conservar la salida que demuestra que los failures son
assertions funcionales contra `RS_REMOTE_NOT_IMPLEMENTED`, no errores de
descubrimiento.

Actualizar el registry en esta misma Task —no crear el provider directamente
desde narrativa— con un recurso canónico:

```text
id = host.github-gh-read
kind = automation
provider = codex-host
locator = builtin://host/github-gh-read
capabilities = [git.remote-proof]
scope = host
authority = system
trust = trusted_host
selection = available
effects = [network_read]
egress = metadata
data_classes = [repository_metadata]
approval = task
load_strategy = tool
context_class = tiny
canonical = true
priority = 950
requires = []
conflicts = []
output_contract = host-github-provider-v1
supersedes = [mcp.github-pr-read]
aliases = []
```

Un locator `builtin://` y `kind=automation` no prueban instalación,
autenticación ni salud. Añadir:

```text
observe_github_host_inventory_capability(
  *,
  governing_runtime: GoverningRuntimeObservation,
  registry_entry,
  canonical_repository,
  endpoint_binding: GitHubEndpointBinding,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> GitHubHostInventoryObservation

validate_github_host_inventory_capability(
  observation,
  *,
  expected_registry_digest,
  expected_repository,
  expected_endpoint_binding,
  expected_invocation_id,
  clock
) -> ValidatedGitHubHostInventoryCapability
```

La observación llama el doctor del provider gobernante, con argv/env saneados,
y liga executable digest, auth-status no secreto, repo/host, TTL y nonce. Hasta
consumir ese wrapper, inventory conserva
`authenticated=unknown`, `healthy=unknown`, `ready=false`, aunque el entry esté
enabled/trusted. `authorized_for_task` se calcula aparte desde efectos/grants y
nunca se infiere de enabled. `_inventory_ready()` exige simultáneamente
enabled+trusted+authenticated+healthy+authorized_for_task; `supersedes` solo se
aplica después de esa conjunción. Missing `gh`, auth pendiente, doctor
degradado, mapping/replay o grant ausente dejan visible el fallback sin
sombrearlo.

Añadir RED:

```text
test_builtin_automation_is_not_ready_by_declaration
test_github_host_inventory_requires_doctored_auth_health_and_task_authority
test_supersedes_applies_only_after_canonical_resource_is_fully_ready
test_unready_canonical_resource_keeps_mcp_fallback_visible
```

Mantener `mcp.github-pr-read` como sustituto opcional, nunca instalarlo ni
autenticarlo. `supersedes` solo sombrea al sustituto cuando el recurso canónico
está ready+authorized; unavailable/unknown no elimina el fallback. Ampliar la
ruta existente `remote-integration-proof` para que
coincida de forma verdadera con `phase=integrate`, `intent=integrate` y
cualquiera de `integration|remote_write|network_read`; el TaskEnvelope validator
y `OUTCOME_LIMITS` comprueban por separado
`requested_outcome=integration`. No se amplía el schema-1 de rutas con un campo
ad hoc ni se infiere outcome desde texto. La selección determinista escribe
resource ID+digest en `RouteDecision`;
transport, observación y receipt deben conservarlos. Ninguna API puede crear
`GhCliTransportProvider` o connector si el recurso no está seleccionado,
ready y autorizado para esa task.

La sustitución anterior se aplica a rutas generales de lectura/remoto, no al
forward-test de autoridad PR D. D prueba deliberadamente el adapter local
instalable que cruza el entrypoint tipado del attestor; por tanto sus
`ValidatedPilotInputs`, transport, observaciones y receipts exigen
`resource_id=host.github-gh-read` y su digest gobernante exacto. Si únicamente
`mcp.github-pr-read` está ready, el router puede recomendarlo para otra task,
pero D queda `pending_github_host_adapter`: no existe puente que convierta un
connector opaco en stdin, JSON o argumentos de
`<attestor>/scripts/control-plane`, y no se inventará.

- [ ] **Step 3: Implementar cliente estándar**

Constantes cerradas:

```text
PER_PAGE = 100
MAX_COMPARE_PAGES = 3
MAX_COMMITS = 250
MAX_PR_PAGES = 3
MAX_PR_FILE_PAGES = 3
MAX_PR_FILES = 250
MAX_CHECK_PAGES = 3
MAX_CHECK_RUNS = 250
MAX_WORKFLOW_RUN_PAGES = 3
MAX_WORKFLOW_RUNS = 250
MAX_WORKFLOW_ATTEMPTS = 10
MAX_WORKFLOW_JOB_PAGES = 3
MAX_WORKFLOW_JOBS = 250
MAX_RESPONSE_BYTES = 2097152
ASSOCIATION_ATTEMPTS = 3
ASSOCIATION_BACKOFF_SECONDS = (0.0, 2.0, 5.0)
```

```text
GitHubTransport.request_json(
  *,
  method: str,
  endpoint: str,
  query: Mapping[str, str | int]
) -> GitHubResponse

bind_github_endpoint(
  *,
  governing_policy: GoverningPolicy,
  event_repository_identity: str,
  github_server_url: str,
  github_api_url: str
) -> GitHubEndpointBinding

class ApprovedGitHubTransportProvider(Protocol):
  def create_transport(
    *,
    canonical_repository: str,
    endpoint_binding: GitHubEndpointBinding
  ) -> GitHubTransport

approve_github_transport_provider(
  *,
  provider: ApprovedGitHubTransportProvider,
  governing_runtime: GoverningRuntimeObservation,
  canonical_repository: str,
  endpoint_binding: GitHubEndpointBinding,
  authenticated_account_digest: str,
  session_id: str,
  invocation_id: str,
  clock,
  ttl_seconds
) -> ValidatedGitHubTransportProvider

UrllibTokenTransport(
  *,
  bearer_credential: SecretValue,
  endpoint_binding: GitHubEndpointBinding,
  timeout_seconds: float = 5.0
)

GhCliTransport(
  *,
  gh_path: Path,
  endpoint_binding: GitHubEndpointBinding,
  timeout_seconds: float = 5.0
)

GitHubClient(
  *,
  repository: str,
  transport: GitHubTransport
)

GitHubClient.compare(
  before: str,
  after: str,
  *,
  page: int,
  per_page: int = PER_PAGE
) -> GitHubPage[GitCommitSummary]

GitHubClient.pull_requests_for_commit(
  sha: str,
  *,
  page: int,
  per_page: int = PER_PAGE
) -> GitHubPage[GitHubPullRequestSummary]

GitHubClient.pull_request(number: int) -> GitHubObject[GitHubPullRequest]
GitHubClient.commit(sha: str) -> GitHubObject[GitHubCommit]
GitHubClient.pull_request_files(
  number: int,
  *,
  page: int,
  per_page: int = PER_PAGE
) -> GitHubPage[GitHubPullRequestFile]
GitHubClient.check_runs(
  sha: str,
  *,
  page: int,
  per_page: int = PER_PAGE,
  filter: str = "latest"
) -> GitHubPage[GitHubCheckRun]
GitHubClient.ref(branch: str) -> GitHubObject[GitHubRef]
GitHubClient.workflow_runs(
  workflow_id: str,
  *,
  branch: str,
  event: str,
  head_sha: str,
  page: int,
  per_page: int = PER_PAGE
) -> GitHubPage[GitHubWorkflowRunSummary]
GitHubClient.workflow_run(run_id: int) -> GitHubObject[GitHubWorkflowRun]
GitHubClient.workflow_run_attempt(
  run_id: int,
  attempt_number: int
) -> GitHubObject[GitHubWorkflowRun]
GitHubClient.workflow_jobs_for_attempt(
  run_id: int,
  attempt_number: int,
  *,
  page: int,
  per_page: int = PER_PAGE
) -> GitHubPage[GitHubWorkflowJob]
```

`GitHubResponse` es una dataclass frozen, construida solo por una factoría
común a ambos transportes:

```text
status_code: int entre 100 y 599
headers: mapping lowercase allowlisted {link, etag, x-ratelimit-*}
json_value: object | list
body_bytes: int entre 0 y MAX_RESPONSE_BYTES
request_id_digest: sha256
```

La factoría cuenta bytes antes de parsear, rechaza cap+1, JSON escalar,
duplicados ambiguos y headers/Link no válidos. El parser cerrado de `Link`
conserva únicamente rel+page normalizados; no guarda Authorization, Cookie,
Set-Cookie, URL cruda, hostname no validado ni body raw. Non-2xx se transforma
en el mismo error saneado/código UNKNOWN en ambos transports. Timeout,
truncación o parse error no fabrican `GitHubResponse`. Fixtures contractuales
obligan paridad byte/status/header/error entre urllib y gh.

`GitHubClient` no descarta esa metadata al parsear. `GitHubObject[T]` conserva
un valor schema-cerrado y `request_id_digest`. `GitHubPage[T]` conserva items
ordenados, `page`, `per_page`, `total_count` cuando el endpoint lo ofrece,
`next_page` derivado del Link validado, `body_bytes` y
`request_id_digest`; no expone headers crudos. Los evaluadores reciben estos
objetos, no mappings/listas, y solo declaran completitud si consumieron todas
las páginas hasta `next_page=None` y cumplieron caps/totals. Un parser que
pierda Link, total o request digest produce UNKNOWN.

Headers mínimos:

```text
Accept: application/vnd.github+json
Authorization: Bearer <environment token>
X-GitHub-Api-Version: 2022-11-28
```

Nunca incluir el token en `repr`, error, facts o logs.

`GitHubClient` contiene endpoints, caps, paginación y validación, pero no
credenciales ni subprocess. En Actions se construye con
`UrllibTokenTransport`, que recibe `GITHUB_TOKEN` como `SecretValue` privado
**solo después** de validar un `GitHubEndpointBinding` host-bound. v2.1 soporta
remoto autoritativo únicamente en github.com y exige
`GITHUB_SERVER_URL=https://github.com` y
`GITHUB_API_URL=https://api.github.com`. GHES devuelve
`pending_remote_host_unsupported` antes de leer el token; userinfo, puerto,
path/query/fragment extra, Unicode/homoglifos, redirect cross-host o mismatch
falla antes de leer/envolver el token, resolver DNS o abrir red. El
transport no tiene default cross-host;
aplica headers mínimos y nunca expone el secreto. En local se construye con
`GhCliTransport`: ejecuta únicamente `gh api` ya preautenticado con argv
cerrado `gh api --include --method GET --hostname <bound-host> <endpoint>`;
solo después añade query `-f key=value`, de modo que `gh` no cambie
implícitamente a POST. `--include` es obligatorio para conservar status y Link;
un parser único separa framing/body acotados. Un fake `gh` ejecutado como
subprocess verifica argv, env, status y paginación; fixtures puras solas no
cuentan. Nunca ejecuta `gh auth token` y construye el env desde allowlist.
Elimina
`GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`,
`GITHUB_ENTERPRISE_TOKEN`, `GH_HOST`, `GH_REPO` y `GH_CONFIG_DIR`; fija
hostname/repo por argumentos validados, `GH_PROMPT_DISABLED=1`,
`GIT_TERMINAL_PROMPT=0` y límites de timeout/stdout/stderr antes de parsear. Si
`gh auth status --hostname` para el hostname validado no demuestra auth preexistente con ese
mismo env saneado, queda `pending_github_host_adapter` sin recurrir al entorno
original. Un connector/OAuth tipado puede
implementar el mismo protocolo. Ambos devuelven `GitHubResponse` saneada y usan
los mismos evaluadores puros; error/redacción/cap de bytes son idénticos.

`ApprovedGitHubTransportProvider` es un Protocol cerrado implementado solo por
adaptadores compilados en el runtime gobernante. `approve_github_transport_provider()`
ejecuta doctor sin imprimir credenciales y emite
`ValidatedGitHubTransportProvider` opaco, TTL/nonce/one-shot ligado a runtime,
lock/provider digest, hostname, repositorio canónico, auth account digest,
session e invocation. `HostGitHubLifecycleProvider` lo consume en esa misma
operación. Mapping, registry entry, provider importado del candidate, replay o
binding distinto queda `pending_github_host_adapter`; no hay factoría CLI.

El sleeper/backoff se inyecta en tests. Cada response tiene límite antes de
parsear JSON. Compare es completo solo si `total_commits <= MAX_COMMITS`, no
hay duplicados, el número recolectado coincide exactamente y no quedan páginas
fuera del cap. PR association **siempre enumera todas las páginas** hasta EOF
demostrado; no retorna al encontrar el primer merged compatible ni al ver el
primer incompatible. Deduplica por identidad cerrada y decide solo sobre el
conjunto exhaustivo: exactamente un candidato merged/base/head/strategy
compatible produce PASS; más de uno compatible es UNKNOWN por ambigüedad;
cero compatibles con conjunto no vacío y exclusivamente contradictorio
produce FAIL; un incompatible junto al único compatible no rebaja ese PASS.
Página/cap/Link incompletos son UNKNOWN aunque ya se haya visto un candidato.
Una asociación vacía se repite tres veces por consistencia eventual; después
continúa `UNKNOWN/RS_REMOTE_ASSOCIATION_NOT_YET_OBSERVED`. La ausencia durante
una ventana finita no demuestra push directo. Fuera de esa contradicción
exhaustiva, FAIL remoto exige forced/delete.
401/403/404/429/5xx, timeout, overflow o JSON inválido son UNKNOWN.

`check_runs` pagina hasta que el número único recolectado coincide exactamente
con un `total_count` estable, sin superar `MAX_CHECK_PAGES` ni
`MAX_CHECK_RUNS`. Un header `Link` con página siguiente después del límite,
cap+1, total cambiante, duplicados, overflow, truncación, nombres/app ambiguos o
reruns cuya última tentativa no pueda demostrarse son UNKNOWN. Tests cubren
exactamente cap-1, cap, cap+1, total_count inestable y Link extra para que
memoria, latencia y completitud sean reproducibles.

`pull_request_files`, `workflow_runs` y `workflow_jobs_for_attempt` aplican
contratos cerrados de completitud. Runs/jobs exigen `total_count` estable, IDs
únicos, `Link` exhaustivo y límites rigurosos de páginas/elementos. Para
`pull_request_files`, que no ofrece un `total_count` autoritativo, la
terminación exige página final corta o ausencia inequívoca de `rel="next"`,
sin superar `MAX_PR_FILE_PAGES/MAX_PR_FILES`, sin path duplicado y con el
conjunto completo ligado al PR/head exactos.
El selector de provenance enumera todos los
runs del workflow gobernante filtrados por branch/event/head SHA dentro del
cap, no se detiene al encontrar el primero. Después relee el run exacto,
exige `run_attempt` entero entre 1 y `MAX_WORKFLOW_ATTEMPTS`, consulta ese
attempt y pagina todos sus jobs. Solo un candidato único, la tentativa actual
demostrada y un único job con display name `risk-provenance`,
`status=completed` y `conclusion=success` son aceptables. `PASS` es el estado
interno que hizo terminar el comando `risk-provenance` con exit 0; no es una
conclusion literal de la API GitHub.
Un candidato en una página posterior debe descubrirse; cap+1, `total_count` o
`Link` cambiante, run/job duplicado, intento fuera de cap, rerun que cambia
durante la lectura o cualquier página ausente devuelven UNKNOWN.

- [ ] **Step 4: Implementar prueba de evento**

```text
evaluate_push_risk(
  event: Mapping[str, Any],
  *,
  host_repository: str,
  policy_repository_identity: str,
  base_branch: str,
  integration_strategy: str,
  client: GitHubClient
) -> RiskDimension
```

`host_repository` procede de `GITHUB_REPOSITORY` en el proceso Actions;
`policy_repository_identity` es el `owner/repo` canónico, no secreto, derivado
en adopción del remote elegido por policy e incluido en la configuración
renderizada; es independiente de que el alias local se llame `origin`,
`upstream` u otro nombre. La gramática solo admite URLs GitHub HTTPS o SSH
cerradas. Rechaza userinfo/credenciales, host distinto, puerto inesperado,
query/fragment, escapes codificados, `..`, Unicode/homoglifos y cualquier
ambigüedad; los errores nunca reproducen la URL cruda. Validar igualdad entre
la identidad canónica, el host, el
`repository.full_name` del evento, `ref == refs/heads/<base>`, `before`, `after`,
`forced` y `deleted` antes de red. Forced o deleted es FAIL. El evento o un
argumento CLI nunca eligen por sí solos qué repositorio consultar.

El mismo cliente alimenta un adaptador separado de lifecycle:

```text
HostGitHubLifecycleProvider.advance(
  task_id,
  target_state,
  *,
  expected_pr_number: int | None,
  governing_policy: GoverningPolicy,
  transport_provider: ValidatedGitHubTransportProvider,
  expected_provider_resource_id,
  expected_provider_resource_digest,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> dict

observe_github_task_state(
  client,
  task_state,
  *,
  expected_pr_number,
  expected_repository,
  expected_base_ref,
  expected_head_repository,
  expected_head_ref,
  expected_head_sha,
  governing_base_commit,
  required_checks,
  target_state,
  expected_provider_resource_id,
  expected_provider_resource_digest,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> GitHubObservation

validate_github_task_observation(
  observation,
  *,
  expected_task_digest,
  expected_repository,
  expected_worktree,
  expected_branch,
  expected_head_sha,
  expected_pr_number,
  expected_governing_base_commit,
  expected_target_state,
  expected_provider_resource_id,
  expected_provider_resource_digest,
  expected_session_id,
  expected_invocation_id,
  clock
) -> ValidatedGitHubObservation

TaskStore.advance_from_github(
  task_id,
  observation: ValidatedGitHubObservation,
  *,
  current_branch,
  expected_invocation_id,
  clock
) -> dict

build_pilot_pull_request_mutation_request(
  *,
  pilot_context: PilotTaskContext,
  provider: ValidatedGitHubPullRequestWriteProvider,
  authorization: TrustedAuthorization,
  title: ValidatedPullRequestTitle,
  body: ValidatedPullRequestBody,
  draft: bool,
  expected_pr_number: int | None,
  session_id,
  invocation_id,
  tool_use_id,
  clock
) -> ValidatedPullRequestMutationRequest

prepare_remote_ref_mutation(
  *,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  inventory: ValidatedWorktreeInventoryObservation,
  task_context,
  expected_remote,
  expected_base,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> RemoteRefMutationGuard

build_fetch_policy_remote_request(
  *,
  target_worktree,
  task_context,
  inventory: ValidatedWorktreeInventoryObservation,
  governing_policy: GoverningPolicy,
  guard: RemoteRefMutationGuard,
  network_authorization: TrustedAuthorization,
  local_mutation_authorization: TrustedAuthorization,
  expected_branch,
  expected_head,
  parameters: FetchPolicyRemoteParameters,
  session_id,
  invocation_id,
  tool_use_id,
  clock
) -> ValidatedGitEffectRequest

build_validated_git_effect_request(
  *,
  operation_id: ClosedGitEffectOperation,
  target_worktree,
  task_context,
  inventory: ValidatedWorktreeInventoryObservation,
  governing_policy,
  lease: TaskLease | None,
  authorization: TrustedAuthorization,
  expected_branch,
  expected_head,
  parameters: ClosedGitEffectParameters,
  session_id,
  invocation_id,
  tool_use_id,
  clock
) -> ValidatedGitEffectRequest

execute_closed_git_effect(
  request: ValidatedGitEffectRequest,
  *,
  git_runtime: DoctoredGitRuntimeProfile,
  clock
) -> GitEffectObservation

validate_created_worktree(
  result: GitEffectObservation,
  inventory: ValidatedWorktreeInventoryObservation,
  *,
  expected_parameters: CreateAuthorizedWorktreeParameters,
  expected_invocation_id,
  clock
) -> ValidatedCreatedWorktreeObservation

validate_removed_worktree(
  result: GitEffectObservation,
  inventory: ValidatedWorktreeInventoryObservation,
  *,
  expected_parameters: RemoveAuthorizedWorktreeParameters,
  expected_invocation_id,
  clock
) -> ValidatedRemovedWorktreeObservation

validate_deleted_ephemeral_branch(
  result: GitEffectObservation,
  *,
  expected_parameters: DeleteEphemeralBranchParameters,
  expected_invocation_id,
  clock
) -> ValidatedDeletedBranchObservation

validate_deleted_local_feature(
  result: GitEffectObservation,
  *,
  expected_repository,
  expected_common_dir,
  expected_feature_branch,
  expected_feature_head,
  expected_pr_number,
  expected_merge_commit,
  expected_base_ref,
  expected_invocation_id,
  clock
) -> ValidatedDeletedLocalFeatureObservation

validate_remote_ref_mutation(
  result: GitEffectObservation,
  *,
  guard: RemoteRefMutationGuard,
  fresh_inventory: ValidatedWorktreeInventoryObservation,
  expected_remote,
  expected_base,
  expected_prior_ref_digest,
  expected_invocation_id,
  clock
) -> ValidatedRemoteRefMutationObservation

validate_deleted_remote_feature(
  result: GitEffectObservation,
  *,
  expected_repository,
  expected_remote,
  expected_feature_branch,
  expected_feature_head,
  expected_pr_number,
  expected_invocation_id,
  clock
) -> ValidatedDeletedRemoteFeatureObservation

observe_staged_change(
  result: GitEffectObservation,
  *,
  expected_task_context,
  expected_paths,
  expected_invocation_id,
  clock
) -> LocalGitIndexObservation

observe_committed_change(
  result: GitEffectObservation,
  *,
  expected_task_context,
  expected_prior_head,
  expected_paths,
  expected_message_digest,
  expected_invocation_id,
  clock
) -> LocalGitObservation

observe_feature_push_result(
  result: GitEffectObservation,
  *,
  expected_task_context,
  expected_remote,
  expected_branch,
  expected_head,
  expected_invocation_id,
  clock
) -> LocalGitObservation

observe_remote_base_for_integration(
  result: ValidatedRemoteRefMutationObservation,
  *,
  expected_task_context,
  expected_remote,
  expected_base,
  expected_feature_head,
  expected_governing_base,
  expected_invocation_id,
  clock
) -> RemoteBaseIntegrationObservation

observe_manual_merge_bootstrap(
  client,
  *,
  expected_repository,
  expected_pr_number,
  expected_base_ref,
  expected_head_ref,
  expected_head_sha,
  expected_merge_strategy: squash,
  governing_runtime: GoverningRuntimeObservation,
  expected_provider_resource_id,
  expected_provider_resource_digest,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ManualMergeObservation

validate_manual_merge_bootstrap(
  observation: ManualMergeObservation,
  *,
  expected_repository,
  expected_pr_number,
  expected_base_ref,
  expected_head_ref,
  expected_head_sha,
  expected_live_base_sha,
  expected_runtime_digest,
  expected_lock_digest,
  expected_provider_resource_id,
  expected_provider_resource_digest,
  expected_session_id,
  expected_invocation_id,
  clock
) -> ValidatedManualMergeObservation

observe_local_base_reconciliation(
  *,
  inventory: ValidatedWorktreeInventoryObservation,
  governing_policy: GoverningPolicy,
  verified_remote_base_head,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> LocalBaseReconciliationObservation

execute_local_base_sync(
  *,
  observation: LocalBaseReconciliationObservation,
  task_context,
  lease,
  authorization: TrustedAuthorization,
  expected_operation: local_base_sync_ff,
  clock
) -> LocalBaseSyncReceipt

frame_post_merge_cleanup_decision(
  event: NativeUserInteractionEvent | None,
  *,
  merged_pr: ValidatedGitHubObservation,
  target_worktree,
  feature_branch,
  allowed_choices: retain | remove_local | remove_local_and_remote,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> PostMergeCleanupDecision

plan_post_merge_cleanup(
  *,
  decision: PostMergeCleanupDecision,
  merged_pr: ValidatedGitHubObservation,
  base_verified: ValidatedGitHubObservation,
  inventory: ValidatedWorktreeInventoryObservation,
  target_worktree,
  feature_branch,
  lease_state,
  governing_policy: GoverningPolicy,
  session_id,
  invocation_id,
  clock
) -> PostMergeCleanupPlan

publish_post_merge_cleanup_receipt(
  *,
  plan: PostMergeCleanupPlan,
  observed_local_effects,
  observed_remote_effect: ValidatedDeletedRemoteFeatureObservation | None,
  clock
) -> PostMergeCleanupReceipt
```

`ClosedGitEffectOperation` conserva stage/commit de Task 1 y añade
exclusivamente el resto de la tabla cerrada reconocida por Task 7.
`ClosedGitEffectParameters` es una unión sellada de dataclasses por operation:
fetch(remote/base), push(remote/feature), delete-remote(remote/feature/HEAD/PR),
stage(paths), commit(paths/message), create(kind/path/branch/start),
remove(path/HEAD), delete-ephemeral(branch/HEAD),
delete-local-feature(branch/HEAD/PR/merge/base) y local-sync(remote/base/heads).
El caller no puede usar una Mapping ni mezclar campos de dos variantes.

`fetch_policy_remote` es la excepción deliberada a la factoría Git genérica:
`build_validated_git_effect_request()` debe rechazarlo y solo
`build_fetch_policy_remote_request()` puede construirlo. Esta factoría consume
de forma atómica dos grants diferentes —`network_read` y `local_write` limitado
al common Git dir— y un `RemoteRefMutationGuard` preparado con inventario y
preimage frescos. `prepare_remote_ref_mutation()` solo lee/compone el wrapper y
no crea marker ni consume grant. La factoría especializada toma el lock común
del almacén de autorizaciones y del common Git dir en orden cerrado, revalida la
preimage y consume ambos grants o ninguno; solo entonces arma la guarda y
publica intent/nonce/preimage. Libera los locks antes del subprocess, ejecuta
Git y vuelve a adquirir el common-dir mutex para reobservar. Una mutación
externa, crash, marker incoherente, postimage inesperado o replay devuelve
STALE/UNKNOWN y nunca evidencia positiva. No se ejecuta subprocess bajo flock y
recovery no completa una operación a partir de un exit code huérfano.
`lease=None` solo confirma que no se editan archivos del worktree; no elimina el
efecto `local_write`.

Para las demás operaciones, `build_validated_git_effect_request()` emite un
wrapper opaco, TTL/nonce/one-shot, ligado al mismo `tool_use_id`; rechaza campos
sobrantes o ausentes. `execute_closed_git_effect()` elige el argv fijo
internamente y ejecuta con `shell=False`, cwd canónico, env saneado,
timeout/caps y snapshots before/after. El caller no puede suministrar argv,
remote, refspec, pathspec ni flags. El fetch produce primero una
`ValidatedRemoteRefMutationObservation`, que es la única entrada válida de
`observe_remote_base_for_integration()`; `push_validated_feature` debe pasar por
`observe_feature_push_result()`. Un exit 0 aislado, output parseado por el
agente o remote-tracking leído antes del proceso no es evidencia.

Las operaciones de setup/teardown, stage, commit y fast-forward usan la misma
factoría con sus operation IDs, pero cada una tiene subject/effect/state/scope
y resultado cerrado propio. Las plantillas internas nunca incluyen shell,
substituciones, `pull`, `rebase`, `reset`, `clean`, force ni base push. La
autorización se consume en el proceso que ejecuta Git; el command hook solo
verifica el handshake nativo y no reconstruye el request desde JSON.

`HostGitHubLifecycleProvider.advance()` se ejecuta desde un worktree attestor
limpio y detached exactamente en `governing_base_commit`, no importa provider
desde el candidate. Su launcher estático valida, antes del primer import,
HEAD/cleanliness, runtime layout y los blobs de launcher/runtime/provider/lock
contra el lock de ese mismo objeto Git. Después crea una
`GoverningRuntimeObservation` opaca y en el mismo proceso opera sobre el
TaskStore del target worktree registrado: lee task/repo/base/head, exige
`GoverningPolicy` de ese base, obtiene un `GitHubClient` con transporte
aprobado/doctorado, consulta GitHub, crea `GitHubObservation` y la
valida/consume inmediatamente dentro de la misma invocation. Candidate no puede suministrar
`current_runtime_digest`, `current_lock_digest` ni otro provider.

`ValidatedManualMergeObservation` es un wrapper host-bound, one-shot y no
serializable para bootstrap de PR D. Lo crea el mismo provider desde el
attestor limpio después de enumerar y validar el PR C completo, su diff
allowlisted, checks, estado merged, estrategia squash, mergeCommit y ref base
live exactamente igual a ese mergeCommit. Liga repo/PR/base/head/merge,
runtime, lock, provider, session, invocation y TTL. Su único dato consumible es
el `merge_commit` que fija el governing base de la task nueva. No promueve la
task C, no sustituye `TrustedPilotAuthorization`, no autoriza Git y no se
reconstruye desde receipt, JSON, env o `rev-parse`. Replay, drift o
incompletitud lo invalidan y obligan a reobservar.

`observe_local_base_reconciliation()` solo corre después de
`base_verified`, identifica por inventario el worktree exacto de la base y
devuelve o bien un candidato one-shot clean/FF, o una vista cerrada
`LOCAL_BASE_NOT_SYNCED`. `execute_local_base_sync()` consume el candidato junto
con task/lease y `TrustedAuthorization(allowed_effect=local_write,
subject=local_base_sync, operation=local_base_sync_ff)`, revalida
heads/cleanliness y ejecuta
internamente argv fijo `git -C <base-worktree> merge --ff-only
<policy-remote>/<base>`. Publica prior/final/remote HEAD y nunca altera la prueba
remota. No existe CLI que acepte observation/result por JSON.

El cierre post-merge tampoco se infiere de un merge exitoso.
`frame_post_merge_cleanup_decision()` convierte únicamente un evento nativo
fresco en `retain|remove_local|remove_local_and_remote`; sin evento devuelve
`retain` más `POST_MERGE_CLEANUP_PENDING`. El plan exige PR/head exactos,
`base_verified`, inventario fresco, feature worktree limpio, cero leases/child
vivos y rama no base. `remove_local` retira primero el worktree y reobserva,
después borra la rama local con grants `local_write` distintos. Una rama
verifier sin commits propios usa `delete_ephemeral_branch`; una feature
fusionada por squash usa exclusivamente `delete_validated_local_feature`,
ligada a PR/head/merge/base exactos y ejecutada como CAS
`git update-ref -d refs/heads/<feature> <expected-feature-head>`. No usa
`branch -D`, rechaza base/protected refs y falla si el old OID cambió.
`remove_local_and_remote` añade una tercera operación cerrada
`delete_validated_remote_feature` con autorización `remote_write` independiente,
ref exacta del PR head y prohibición absoluta sobre la base/protected refs. En
squash la identidad procede del PR head, no de un test de ancestry. Todo fallo
retiene lo no eliminado, deja razón y safe-next-step, y nunca usa force, prune o
borrado recursivo.

El CLI del attestor expone:

```text
task observe-github --target-worktree PATH --task-id ID
                    --target pr_draft|pr_ready|merged|base_verified
                    [--pr-number N]
```

`--target-worktree` debe coincidir con un path canónico de
`git worktree list --porcelain` del mismo common dir; no selecciona runtime ni
policy. No acepta repo, base, HEAD, token, facts, digests ni observation por
PATH/stdin/env. `--pr-number` solo selecciona; la respuesta de GitHub debe verificar
todos los bindings. El provider local preferido es
`GhCliTransportProvider`: solo si
`doctor` demuestra que un `gh` ya instalado está autenticado para el repo,
invoca `gh api` con argumentos cerrados en ese mismo proceso padre, valida el
JSON y nunca ejecuta `gh auth token` ni expone la credencial. Un
connector/OAuth host tipado puede ser sustituto. Acceder al provider requiere
autorización separada; nunca se instala ni autentica automáticamente, y el token
no se imprime ni persiste. Si ninguno está ready:
`pending_github_host_adapter`; no se promueve el lifecycle.

Excepción de forward-test: `authority_mode=pilot` admite únicamente el recurso
gobernante exacto `host.github-gh-read`. Un connector puede implementar el
protocolo para tasks ordinarias, pero no atravesar el launcher/CLI del attestor
ni satisfacer D. Si la RouteDecision de D selecciona otro ID, o solo está ready
`mcp.github-pr-read`, el provider devuelve
`pending_github_host_adapter` antes de red.

El state conserva `governing_base_commit`, `governing_policy_digest` y
`candidate_policy_digest`. `GoverningPolicy` y `GoverningResourceRegistry` no
se deserializan desde state: cada invocación relee ambos blobs exactos desde el
commit base, valida schemas, referencias cruzadas y digests y los envuelve en
memoria. La única factoría es
`load_governing_configuration(governing_runtime, governing_base_commit)`;
mapping/candidate no produce estos wrappers. Required checks, canonical
remote/base, integration strategy y recursos salen exclusivamente de ellos. Si
el candidate digest
difiere o el diff toca policy/provider/gates, `mark_policy_change_pending()`
impide que ese PR se autoevalúe con la candidata. El shadow puede comparar
resultados para auditoría, pero no llama `TaskStore.advance_from_github()`.

Cada `GitHubObservation` liga `governing_base_commit`,
`observed_base_ref_sha` y, tras merge, `merge_commit`. Los bindings dependen del
estado:

```text
pr_draft/pr_ready
  PR.base.sha == live refs/heads/base == governing_base_commit

merged
  mismo PR/head/revision, merged=true y merge_commit exacto;
  strategy=squash, commit object completo y parents == [governing_base_commit]

base_verified en v2.1
  live refs/heads/base == merge_commit exacto y
  parents(merge_commit) == [governing_base_commit]
```

Así el avance normal provocado por fusionar D no se confunde con drift. Un
avance de base antes del merge invalida observaciones/checks. Una task ordinaria
no rota contexto in-place en v2.1: cierra/suspende el contexto anterior,
incorpora la base de forma autorizada y crea una task nueva con
task/decision/lease/GoverningPolicy recalculados, aunque runtime/policy/gates
parezcan idénticos. Durante D, cualquier avance pre-merge invalida el piloto y exige
otro D desde la nueva base. Un avance adicional después del merge antes de
`base_verified` devuelve `BASE_ADVANCED_AFTER_MERGE`/UNKNOWN y no publica
hint/capability; v2.1 no intenta atribuir esa carrera. Ser ancestro no basta:
para squash, el parent único de M debe ser exactamente G; la cadena G→B→M se
rechaza aunque la ref live sea M.

Definir modos cerrados:

```text
shadow         → observa, nunca muta lifecycle
pilot          → solo task/branch/scope/base/policy/lock de PR D
authoritative  → exige reobservación live y ValidatedProviderCapability fresca
```

`pilot` es una capability candidata bajo prueba, no `ready`; solo puede avanzar
el TaskStore ligado al piloto. La única entrada es:

```text
build_validated_pilot_inputs(
  *,
  task: Mapping[str, Any],
  inventory: ValidatedInventory,
  policy: GoverningPolicy,
  registry: GoverningResourceRegistry,
  expected_route_digest: str,
  session_id: str,
  invocation_id: str,
  host_capability: HostAdapterCapability,
  clock,
  ttl_seconds
) -> ValidatedPilotInputs

TaskStore.start_authority_pilot(
  *,
  inputs: ValidatedPilotInputs,
  target_worktree: Path,
  governing_runtime: GoverningRuntimeObservation,
  manual_merge_observation: ValidatedManualMergeObservation,
  governing_policy: GoverningPolicy,
  trusted_authorization: TrustedPilotAuthorization,
  expected_branch: str,
  clock
) -> PilotTaskContext

TaskStore.resume_authority_pilot(
  *,
  task_id: str,
  target_worktree: Path,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  registry: GoverningResourceRegistry,
  inventory: ValidatedWorktreeInventoryObservation,
  transport_provider: ValidatedGitHubTransportProvider | None,
  trusted_authorization: TrustedPilotResumeAuthorization,
  session_id: str,
  invocation_id: str,
  clock
) -> PilotTaskContext

TaskStore.begin_pilot_verification(
  *,
  context: PilotTaskContext,
  preflight: ValidatedPilotPreflightObservation,
  expected_generation: int,
  clock
) -> PilotTaskContext

TaskStore.complete_pilot_verification(
  *,
  context: PilotTaskContext,
  verification: ValidatedPilotLocalVerificationObservation,
  expected_generation: int,
  clock
) -> PilotTaskContext

TaskStore.advance_pilot_local_commit(
  *,
  context: PilotTaskContext,
  local_git: LocalGitObservation,
  expected_generation: int,
  clock
) -> PilotTaskContext

TaskStore.advance_pilot_push(
  *,
  context: PilotTaskContext,
  local_git: LocalGitObservation,
  expected_generation: int,
  clock
) -> PilotTaskContext

TaskStore.finalize_authority_pilot(
  *,
  context: PilotTaskContext,
  capability_status:
    validated | pending_external_evidence | aborted,
  validated_capability: ValidatedProviderCapability | None,
  reason_code: str,
  clock
) -> ResourceUseReceipt

TaskStore.recover_authority_pilot_finalization(
  *,
  task_id: str,
  state_dir: Path,
  common_dir: Path,
  clock
) -> ResourceUseReceipt
```

`build_validated_pilot_inputs()` valida el TaskEnvelope, consume inventory
host-bound y ejecuta el resolver con policy+registry gobernantes en la misma
invocación; compara el route digest esperado solo como aserción y emite un
wrapper opaco, TTL/nonce/one-shot ligado a task/decision/inventory/session/
invocation. No acepta una `ValidatedTaskEnvelope` o `ValidatedRouteDecision`
autodeclarada ni una decisión por JSON.

`start_authority_pilot()` consume en el mismo proceso inputs, runtime
attestation, manual merge observation y autorización; deriva internamente
session, scope, provider y `governing_base_commit`. El merge wrapper —no un SHA,
env o `rev-parse`— fija el HEAD inicial y debe coincidir con target/base live.
`start_authority_pilot()` publica `implementing`.
El piloto puede atravesar restart/compaction, pero nunca mediante
`TaskStore.resume()` ni reconstruyendo `PilotTaskContext` desde JSON.
`resume_authority_pilot()` es la única rehidratación: bajo orden de locks relee
state+lease durables no finalizados, atestigua de nuevo runtime/policy/registry
gobernantes, inventario/worktree/branch/HEAD/scope y exige una
`TrustedPilotResumeAuthorization` nueva, opaca y one-shot, ligada al state
digest, prior/new session, invocation y fase exacta. Para `pushed` reobserva
feature ref remota; para `pr_draft` y `pr_ready` reobserva PR/base/head y, donde
corresponde, checks completos; para `merged` reobserva merge/topología/base
actual. Un provider no necesario todavía puede ser `None`; una fase remota no
se reanuda sin `host.github-gh-read` exacto y red autorizada. Solo entonces
rota generation/session, rebindea owner del mismo lease bajo common-dir flock y
emite un contexto opaco nuevo; todos los anteriores quedan stale. Esta
autorización solo permite reanudar identidad, no commit, red, push, PR, merge,
local-base-sync ni release.

Si falta lease, el árbol/HEAD cambió, la base avanzó, el provider no coincide,
el state está `finalizing/finalized` o la observación remota es incompleta, la
API falla cerrado y no publica contexto. Antes de merge se aborta el intento y
cualquier corrección usa task/branch/PR nuevos desde base fresca; después de
merge se conserva el estado auditable y se usa la finalización pending, nunca
se abre un segundo PR para el mismo merge. Fault tests cortan el proceso justo
después de `pushed`, `pr_draft`, `pr_ready` y `merged`, reinician con sesión e
invocation nuevas y demuestran rebind válido o abort/pending sin doble efecto.
`begin_pilot_verification()` exige preflight/lease/repo/worktree/scope exactos y
publica `verifying`; `complete_pilot_verification()` consume los
`CompletedSafeRead` ligados al piloto, allowlist completa y cero gate rojo, y
publica `review_ready`. `advance_pilot_local_commit()` solo acepta ese estado,
consume una `LocalGitObservation` fresca que liga prior HEAD, nuevo commit,
índice limpio y diff allowlisted, publica `committed` y rota `current_head` y
generation. `advance_pilot_push()` exige otra `LocalGitObservation` host-bound
que demuestre tree/index limpios y `origin/<feature> == current_head`, y publica
`pushed`. Todo contexto anterior queda stale. Ningún otro binding puede cambiar
y el provider solo acepta `pr_draft` desde el contexto pushed vigente.

`ValidatedPilotPreflightObservation` y
`ValidatedPilotLocalVerificationObservation` solo se crean en
`host_bridge.py` desde procesos/`CompletedSafeRead` observados en la misma
invocation; ligan task/context generation, repo/worktree/branch/HEAD,
lease/policy/runtime/scope, IDs de gate, digests de resultados, TTL y nonce
one-shot. No aceptan output crudo, mapping, JSON, replay ni resultado de otro
root/generation. La segunda exige exactamente los cuatro safe-reads de Task 14
y consume sus `repository_binding_digest`; no puede autoatestiguar PASS con
booleans.

`TrustedPilotAuthorization` es opaco, TTL/nonce/one-shot y liga
task/repo/worktree/branch/HEAD/scope/base/policy/lock/runtime/provider/session.
Autoriza únicamente iniciar el modo de prueba, no commit, red, push, PR ni
integration. `task start`, CLI, JSON y state edits no aceptan
`authority_mode=pilot`; el host nativo llama esta API dentro del proceso
attestor. Sin HostAdapterCapability queda `pending_host_capability`.
`TaskStore.resume()` genérico nunca acepta un task pilot; antes de finalización
devuelve `E_PILOT_SPECIAL_RESUME_REQUIRED`, y después conserva
`E_PILOT_FINALIZED`.
`finalize_authority_pilot()` es fail-closed. Todos sus destinos publican
`pilot_finalized=true` y `resume_forbidden=true`; `TaskStore.resume()` y
`transition()` rechazan tanto `finalizing` como cualquier state con esas marcas
mediante `E_PILOT_FINALIZED`. Con task en `base_verified` cierra el outcome
integration tanto si la capability quedó validada como pending, sin convertir
pending en ready. Si la base avanza después de `merged` pero antes de
`base_verified`, finaliza el intento como
`blocked/pending_external_evidence`, preserva `resume_state=merged` y
`BASE_ADVANCED_AFTER_MERGE`, no afirma outcome/close exitoso y libera el
writer. Un abort anterior conserva igualmente estado/reason/resume auditables,
libera el writer y prohíbe reanudar escrituras: cualquier corrección exige
task/piloto nuevo.

La finalización sigue el orden global common-dir → per-task, no ejecuta red ni
subprocess bajo locks y es recuperable como una transacción de dos fases:

1. tomar common-dir y después per-task; releer lease/state/context/generation y
   publicar+sincronizar un marker `finalizing` con
   `pilot_finalized=true/resume_forbidden=true`, destino, reason y lease digest;
2. liberar per-task, mantener common-dir y ejecutar
   `TaskLease._release_locked(lease_lock_token, ...)` owner-bound, con
   tombstone+unlink durables e idempotentes; nunca llamar `release()` ni
   readquirir el flock ya poseído;
3. volver a tomar per-task todavía bajo common-dir, revalidar generation y
   publicar+sincronizar el state final con el digest del tombstone;
4. liberar per-task y common-dir.

Un crash antes del tombstone deja `finalizing` más lease; entre tombstone y
unlink deja `finalizing` más lease+tombstone; después del unlink pero antes del
state final deja `finalizing` más tombstone y ningún writer. Recovery continúa
desde la fase exacta. En todos los casos `finalizing` bloquea toda escritura y
generic resume.
Nunca existe lease ausente junto a un state escribible/reanudable. La
recuperación llama `recover_authority_pilot_finalization()` y acepta las mismas
tres fases mutuamente excluyentes, incluida
marker+state+lease+tombstone coincidentes. Deriva todos los
bindings, destino y digests de esos artefactos durables validados; no
necesita ni reconstruye el `PilotTaskContext` opaco perdido, no abre red y no
reabre autoridad. Repite la misma secuencia idempotente, no borra JSON
manualmente, no cambia el destino y no necesita la sesión piloto viva.

Añadir helpers host-only:

```text
attest_governing_provider_runtime(
  *,
  attestor_worktree,
  governing_base_commit,
  target_worktree,
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> GoverningRuntimeObservation

publish_github_lifecycle_hint(
  *,
  common_git_dir,
  pilot_state,
  base_verified_observation,
  provenance_observation: GitHubWorkflowProvenanceObservation,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  provider_identity
) -> CapabilityAttestationHint

load_hint_and_revalidate_github_lifecycle(
  *,
  common_git_dir,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  transport_provider: ValidatedGitHubTransportProvider,
  expected_repository,
  expected_invocation_id,
  clock,
  ttl_seconds
) -> ValidatedProviderCapability

observe_github_workflow_provenance(
  *,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  transport_provider: ValidatedGitHubTransportProvider,
  pilot_state,
  expected_workflow_path,
  expected_workflow_blob_digest,
  expected_merge_commit,
  clock,
  ttl_seconds
) -> GitHubWorkflowProvenanceObservation
```

La factoría de Task 9 amplía el `GoverningRuntimeObservation` ya introducido en
Task 1 con bindings de provider. Para el purpose cerrado `github_lifecycle`
liga además:

```text
attestation_id + nonce
attestor common-dir/worktree/HEAD/cleanliness
governing_base_commit
target common-dir/worktree/branch/HEAD
launcher/runtime/provider/lock paths y digests
session_id + invocation_id
issued_at_monotonic + expires_at_monotonic
```

No se serializa ni se acepta por input. Solo vive durante la invocación host que
lo creó. Puede alimentar varios consumidores de esa misma invocación mediante
`consume_governing_runtime_use(observation, purpose)`, con purposes cerrados y
cada uno consumible una vez; no puede pasar a otra invocation ni reutilizarse
en una operación futura. Operaciones posteriores crean una atestación nueva.
Replay de purpose, TTL, target/base/blob/cleanliness distinto o attestor
retirado falla antes de import/red/TaskStore. Tests cubren cada purpose,
permutación válida, replay y drift.

La publicación requiere `base_verified`, `risk-provenance == PASS` y todos los
bindings exactos; UNKNOWN o FAIL no publican siquiera el índice. Los digests se
derivan del wrapper del attestor, nunca de escalares del runtime evaluado. Usa
`capabilities.lock` y temp/fsync/replace/fsync-dir. El archivo persistido es
solo `CapabilityAttestationHint`: un índice no confiable con selectores
públicos del piloto, no una capability ni una prueba. Su existencia, contenido
o digest nunca habilita `authoritative`.

Cada uso futuro crea un attestor limpio nuevo y reconstruye en vivo toda la
cadena antes de emitir un `ValidatedProviderCapability` opaco, fresco,
invocation-bound y one-shot:

```text
runtime/launcher/provider/lock gobernantes válidos antes del import
→ policy gobernante vigente y mismo product/schema
→ PR piloto exacto y head original
→ lista remota completa de files limitada a la allowlist documental
→ PR merged hacia la base configurada y merge commit exacto
→ merge commit contenido en la ref base live
→ workflow path/blob gobernante en ese merge
→ run push exacto, intento actual y job risk-provenance completed/success
```

Los IDs/digests del hint solo seleccionan qué consultar; las respuestas live y
wrappers host-bound deciden. GitHub no disponible, truncación, mismatch, hint
fabricado, PR/diff/run/job ausente o drift devuelve pending sin mutar
lifecycle. No existe cache serializable de `ready`.

La observación de provenance se produce y consume dentro del mismo proceso
attestor consultando GitHub: workflow path y blob digest gobernantes, evento
`push`, base branch, `head_sha == merge_commit`, enumeración completa de runs,
run ID/attempt único y último, estado completed, enumeración completa de jobs y
un único job `name=risk-provenance`, `status=completed`,
`conclusion=success`; esa conclusion prueba el exit 0/PASS interno. Liga
repo/task/provider/base/merge/run/TTL y es one-shot. La Action continúa sin
mutar TaskStore; output, artifact, PATH, stdin o JSON de CI nunca se promueven a
esta capability. Missing, múltiples candidatos, rerun ambiguo, stale,
cross-repo/SHA o UNKNOWN/FAIL conservan pending.

El adaptador admite solo `pr_draft`, `pr_ready`, `merged` y `base_verified`.
Para `pr_draft`, exige un único PR exacto o número verificado. Para `pr_ready`,
exige mismo PR, `state=open`, `draft=false`, base repo/ref, head repo/ref/SHA y
el conjunto cerrado `git.required_checks` de policy. Cada check obligatorio
liga nombre y app, debe aparecer una sola vez para el HEAD, estar completed y
tener una conclusión expresamente admitida. Missing, queued, in_progress,
failing, stale, duplicado ambiguo, cap o paginación incompleta no promueve.
`merged` y `base_verified` conservan PR/head/revision y verifican merge commit y
ref base exactos. `pushed` continúa usando `LocalGitObservation`; release Apple
permanece `pending_external_evidence`. No existe opción para cargar una
`GitHubObservation`.

Añadir a `[git]` una lista cerrada `required_checks` con objetos
`{name, app_slug, allowed_conclusions}`. En este repo `verify` es obligatorio y
sólo admite `success`; su identidad completa candidata es
`{name="verify", app_slug="github-actions",
allowed_conclusions=["success"]}`. La inspección read-only del commit base
`20e999fe1be34b25ca969840529b083b2e39a461` observó además
`macos-smoke/github-actions=skipped`, por lo que `macos-smoke` no se declara
obligatorio mientras su skip sea parte del diseño. Esta observación puntual no
es una decisión ni se reutiliza: Task 9 debe revalidar nombre/app desde la base
gobernante actual antes de presentar el draft. Esto se refiere únicamente al
check remoto de GitHub y no
sustituye el smoke Darwin host-bound ni la revisión `/hooks` de Task 12.
Ausencia o ambigüedad de esta lista deja `pr_ready` UNKNOWN, no verde. La
adopción debe obtener una decisión explícita por proyecto.

La decisión tiene un canal ejecutable, no una frase en el plan. Task 9 **no**
implementa ni importa esta autoridad desde su candidate: reutiliza las
factorías ya fusionadas por Task 1 en el runtime gobernante. Se repiten aquí
solo para fijar el uso:

```text
parse_required_check_selector(
  "NAME:APP:CONCLUSION[,CONCLUSION]"
) -> RequiredCheckCandidate

frame_project_remote_policy_decision(
  native_user_event: NativeUserInteractionEvent,
  *,
  governing_runtime: GoverningRuntimeObservation,
  host_capability: HostAdapterCapability,
  operation_kind: adoption | policy_update,
  draft_plan_digest,
  source_repository_identity,
  target_repository_identity,
  target_worktree_identity,
  repository_identity,
  required_checks: tuple[RequiredCheckCandidate, ...],
  session_id,
  invocation_id,
  clock,
  ttl_seconds
) -> ProjectRemotePolicyDecision

adoption_plan(
  source,
  target,
  *,
  base_branch,
  remote,
  required_check_candidates=()
) -> AdoptionPlanDraft

adoption_apply(
  draft: AdoptionPlanDraft,
  *,
  remote_policy_decision: ProjectRemotePolicyDecision,
  authorization: TrustedAuthorization
) -> AdoptionReceipt

project_remote_policy_update_plan(
  *,
  governing_runtime: GoverningRuntimeObservation,
  governing_policy: GoverningPolicy,
  candidate_policy_path,
  task_context,
  lease: TaskLease,
  repository_identity,
  required_checks: tuple[RequiredCheckCandidate, ...]
) -> ProjectRemotePolicyUpdateDraft

apply_project_remote_policy_update(
  draft: ProjectRemotePolicyUpdateDraft,
  *,
  governing_runtime: GoverningRuntimeObservation,
  remote_policy_decision: ProjectRemotePolicyDecision,
  authorization: TrustedAuthorization,
  expected_generation,
  clock
) -> ProjectRemotePolicyUpdateReceipt
```

`adopt plan` admite `--required-check
NAME:APP:CONCLUSION[,CONCLUSION]` repetible y `--repository-identity
OWNER/REPO` únicamente para mostrar un draft normalizado con
`decision_status=pending_user_confirmation`; esos argv nunca crean el wrapper.
El adapter host encuadra después la selección explícita del usuario mediante
el evento/capability nativos; liga digest del draft, source/target repo y
worktree, session/invocation y TTL. El wrapper no tiene factoría pública,
deserializador ni fallback CLI. `apply` rederiva el
draft y exige igualdad byte/digest, el wrapper one-shot y una autorización de
mutación separada. Editar el JSON, copiar el plan ID, añadir `confirmed=true` o
reordenar checks invalida la decisión; no existe autofirma. Sin callback host,
el CLI conserva pending y no escribe `repository_identity` ni
`required_checks`.

La operación especializada policy-only del runtime **gobernante** es la única
que Task 9 puede usar sobre su propio worktree ya dirty. Relee bajo el lease exacto únicamente
`.codex/project-policy.toml`, exige digest/generation del draft, cambia solo
`git.repository_identity` y `git.required_checks`, valida el schema completo y
publica con backup+journal+temp/fsync/replace/fsync-dir. Preserva todos los
demás campos/ediciones candidate y no renderiza, copia ni restaura
`MANAGED_FILES`. Drift, otro path cambiado desde el draft o rollback ambiguo
falla sin escribir. `adoption_apply()` sigue reservado para adopción
source→target limpio y **no** se invoca dentro de Task 9.

Antes de mutar `.codex/project-policy.toml` en **este** Task 9, ejecutar una
transición explícita, no una edición manual:

```text
1. reobservar desde la base gobernante el remote canónico, repository identity
   y metadata completa del check verify;
2. construir con el attestor/runtime gobernante
   `project_remote_policy_update_plan()` para
   repository_identity=AndreaBusta/codex-engineering-control-plane y
   required_check=verify:github-actions:success;
3. mostrar el draft exacto con decision_status=pending_user_confirmation;
4. recibir un NativeUserInteractionEvent de selección explícita y hacer que el
   runtime gobernante cree ProjectRemotePolicyDecision ligado a
   operation_kind=policy_update, draft/source/target/session/invocation;
5. obtener aparte TrustedAuthorization(local_write) para la policy exacta;
6. ejecutar `apply_project_remote_policy_update()`, verificar que ningún otro
   managed file cambió, su receipt/rollback y continuar el GREEN de Task 9.
```

Si metadata, evento nativo, decision wrapper o autorización no están
disponibles, Task 9 se detiene en
`pending_remote_policy_configuration`; no escribe una tuple aproximada ni
continúa hacia el commit. El texto de este plan y la observación puntual del
commit anterior no sustituyen el evento.

El schema 1 de policy declara además de forma ejecutable
`git.repository_identity = "owner/repo"` y valida la gramática ASCII cerrada
contra el remote canónico, sin userinfo, puerto, query, fragment ni Unicode.
El lifecycle remoto autoritativo v2.1 exige además que ese remote normalice a
`github.com`; otro host valida como configuración Git pero queda
`pending_remote_host_unsupported` para provider/provenance, sin tocar token o
red.
`required_checks` valida lista no vacía cuando remote lifecycle está habilitado,
IDs/nombres/app únicos, conclusiones allowlisted y orden canónico. No se deriva
ninguno de estos valores en tiempo de evaluación desde el evento, PR o cwd.
`adoption plan` normaliza el remote ya configurado, muestra la identidad y
checks candidatos; `apply` solo los escribe tras decisión explícita del
proyecto. Ausencia/mismatch deja `pending_remote_policy_configuration`, nunca
defaults silenciosos. Tests de policy/adoption cubren SSH/HTTPS github.com
equivalentes, host enterprise como pending/unsupported antes de token o red,
userinfo/homoglifos, duplicados, lista vacía,
rollback y preservación de campos no gestionados.

Para la policy actual `integration_strategy = "squash"`, `PASS` exige para cada
commit:

```text
merged_at != null
base.ref == base_branch
merge_commit_sha == commit sha
un parent exacto que sea before o el commit previo de la cadena observada
```

HTTP 200 con lista vacía tras retries es `UNKNOWN`; alerta
`PROVENANCE_UNPROVEN`, pero no afirma causalmente push directo. Problemas de
disponibilidad, permisos, consistencia o completitud son `UNKNOWN`. Una policy
`merge-commit` o
`rebase-merge` devuelve `UNKNOWN/RS_REMOTE_STRATEGY_UNSUPPORTED` hasta
implementar y probar su adaptador de procedencia; la v2.1 no generaliza
evidencia de squash a otros métodos.

Esta evidencia certifica topología/resultado `squash_compatible`, no qué botón
se pulsó. Un rebase de un PR de un único commit puede ser indistinguible de
squash con la API disponible y se acepta solo bajo esa etiqueta honesta. Dos
parents, rebase multicommit o cadena no contigua siguen UNKNOWN. Si el requisito
es demostrar el gesto UI exacto, falta evidencia externa y no se emite PASS de
método.

- [ ] **Step 5: Cablear CLI remoto**

Añadir aquí, no en Task 6:

```text
risk-provenance --github-event PATH --json
```

`risk-provenance` devuelve únicamente la `RiskDimension remote` y sus exits
0/1/2. No agrega `local`: un checkout Actions detached sobre la base, sin
hooksPath/trust local, no puede convertir un PASS remoto en FAIL/UNKNOWN. No
marca la dimensión local como PASS; simplemente queda fuera de este comando CI.
`risk-status` humano/local conserva su contrato agregado.

El evento debe ser archivo regular y acotado. Solo este entrypoint Actions
valida primero `GITHUB_SERVER_URL` y `GITHUB_API_URL` contra la identidad/host
canónicos de policy y crea `GitHubEndpointBinding`; solo después envuelve
`GITHUB_TOKEN` en `SecretValue` y construye
`UrllibTokenTransport`; el token no entra en `GitHubClient`, `RiskStatus`,
errores o receipts. Mismatch o redirect cross-host falla antes de red y no
incluye el valor del token. Fuera de CI, el comando post-push no se usa; el provider
local construye `GhCliTransport`, ignora cualquier token de entorno y es la
única vía para mutar TaskStore con su capacidad aprobada.

- [ ] **Step 6: Modificar workflow**

Crear un workflow separado, no añadir el job al workflow general cancelable:

```yaml
name: risk-sentinel
on:
  push:
    branches:
      - main

permissions:
  contents: read

concurrency:
  group: risk-sentinel-${{ github.event.after }}
  cancel-in-progress: false

jobs:
  risk-sentinel:
    name: risk-provenance
    permissions:
      contents: read
      pull-requests: read
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - name: Check out repository
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
        with:
          fetch-depth: 1
          persist-credentials: false

      - name: Verify base push provenance
        shell: bash
        env:
          GITHUB_TOKEN: ${{ github.token }}
        run: |
          scripts/control-plane risk-provenance \
            --github-event "$GITHUB_EVENT_PATH" \
            --json
```

No añadir permisos write, Issues, statuses, releases ni acciones externas.
Este job es una alarma post-push. No es un check pre-merge ni branch protection.
El group por `event.after` y `cancel-in-progress: false` impide que un segundo
push cancele la observación del primero.

`.codex/templates/risk-sentinel.yml.tmpl` contiene un placeholder validado solo
para la rama base. `adoption_plan()` renderiza el workflow del target usando
`project-policy.toml`, lo incluye en plan/digests/backups y rollback. Un nombre
de rama que no pueda representarse de forma segura en YAML bloquea adopción.
No se copia el `main` del repositorio fuente a otros proyectos.

- [ ] **Step 7: Endurecer contrato CI**

`ci_contract_issues()` identifica por la clave YAML exacta
`jobs.risk-sentinel`, exige además display name exacto `risk-provenance` y el
step cerrado que invoca `scripts/control-plane risk-provenance`. Debe permitir
`pull-requests: read` solo allí y seguir rechazando:

```text
pull_request_target
cualquier write
acción sin SHA
persist-credentials distinto de false
script remoto
token impreso
cancel-in-progress true o group compartido entre pushes de base
base del workflow distinta de project-policy
workflow invoca risk-status agregado en vez de risk-provenance remote-only
```

- [ ] **Step 8: Ejecutar GREEN**

Antes de GREEN, añadir `github_provenance.py` a `RUNTIME_MODULES` y regenerar
un lock provisional coherente con todo Task 9. No se publica: Task 10 aún debe
actualizar lockfile/distribución/documentación y regenerarlo otra vez antes del
commit conjunto.

Run:

```bash
python3 -m unittest \
  tests.test_risk_sentinel \
  tests.test_lifecycle \
  tests.test_resource_registry \
  tests.test_routing \
  tests.test_cli_v2 \
  tests.test_policy \
  tests.test_risk_integration \
  tests.test_adoption \
  tests.test_repository_contract \
  tests.test_lockfile \
  -v
scripts/control-plane doctor
```

Expected: todos PASS.

- [ ] **Step 9: No sellar un commit con lock incoherente**

Task 9 modifica policy, runtime y distribución. No hacer commit ni staging
parcial todavía: `.codex/control-plane.lock` quedaría deliberadamente stale.
Continuar en el mismo worktree y child con Task 10, definir el set exhaustivo
`RUNTIME_MODULES`, actualizar documentación y regenerar el lock sobre el
runtime definitivo. Tasks 9 y 10 forman una sola unidad de commit; si Task 10
se bloquea, no se publica ni se empuja el estado intermedio.

## Task 10: Versionar distribución, lock y documentación

**Files:**
- Carry uncommitted from Task 9:
  `control_plane/github_provenance.py`, `control_plane/host_bridge.py`,
  `control_plane/lifecycle.py`, `control_plane/risk_sentinel.py`,
  `control_plane/policy.py`, `control_plane/routing.py`,
  `control_plane/resource_registry.py`, `control_plane/cli.py`,
  `.codex/project-policy.toml`, `.codex/resource-registry.toml`,
  `.github/workflows/risk-sentinel.yml`,
  `.codex/templates/risk-sentinel.yml.tmpl`,
  `tests/test_risk_sentinel.py`, `tests/test_lifecycle.py`,
  `tests/test_policy.py`, `tests/test_risk_integration.py`,
  `tests/test_cli_v2.py`, `tests/test_resource_registry.py`,
  `tests/test_routing.py`, `tests/contract_support.py`
- Modify: `.codex/control-plane.lock`
- Modify: `control_plane/lockfile.py`
- Modify: `control_plane/adoption.py`
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `AGENTS.md`
- Create: `docs/adr/0003-host-bound-clarification.md`
- Create: `docs/adr/0004-risk-sentinel-and-local-guards.md`
- Modify: `docs/engineering/02-git-pr-merge.md`
- Modify: `docs/engineering/07-adoption.md`
- Modify: `docs/engineering/09-audit-dafo-and-risk-register.md`
- Modify: `docs/engineering/11-lifecycle-hooks-adoption.md`
- Modify: `docs/engineering/12-multidominio-y-modos.md`
- Create: `docs/engineering/13-clarification-and-risk.md`
- Modify: `tests/test_lockfile.py`
- Modify: `tests/test_adoption.py`
- Modify: `tests/test_repository_contract.py`

- [ ] **Step 1: Escribir RED de artefactos y lock**

Exigir:

```text
product_version = 2.1.0
clarification schema = 1
risk schema = 1
git guards presentes y ejecutables
risk runtime incluido en digest agregado
ADR 0003, ADR 0004 y documento 13 presentes
runtime aislado contiene todos los módulos normativos e importa sin source tree
```

Verificar la constante única `RUNTIME_MODULES` introducida en PR A y añadir
`github_provenance.py` antes de regenerar el lock final:

```text
__init__.py
adoption.py
clarification.py
cli.py
contracts.py
git_guards.py
git_state.py
github_provenance.py
graph.py
hooks.py
host_bridge.py
intake.py
lifecycle.py
lockfile.py
policy.py
project_profiles.py
repository.py
resource_registry.py
risk_sentinel.py
routing.py
scopes.py
```

`MANAGED_FILES` se deriva de esa constante. Un test compara el set con todos
los módulos runtime de source salvo una allowlist dev-only explícita; así un
módulo nuevo no queda fuera silenciosamente. En un repo temporal adoptado,
ocultar el source tree y ejecutar/importar `policy-check`, `registry-check`,
`doctor`, `preflight`, `inventory`, `route`, `task`, `risk-status`,
`risk-provenance` y `git-guard`; repetir después de upgrade. Después de
rollback no se intenta ejecutar el runtime retirado: comprobar ausencia exacta
de la instalación gestionada o restauración byte-idéntica de la preexistente, y
que el launcher retirado ya no ejecuta.

Run:

```bash
python3 -m unittest \
  tests.test_lockfile \
  tests.test_adoption \
  tests.test_repository_contract \
  -v
```

Expected: FAIL hasta actualizar artefactos/digests.

- [ ] **Step 2: Escribir ADRs**

ADR 0003 debe registrar:

- `ClarificationRequest` serializable frente a resolución host-bound;
- `AuthorizationGrant` serializable como solicitud frente a
  `TrustedAuthorization` host-bound;
- por qué JSON/CLI no puede autoatestiguar trusted_host;
- por qué command hooks separados no transportan wrappers in-memory y bloquean
  promoción semántica sin HostAdapterCapability real;
- atomicidad per-task y publicación durable ordenada de
  `ClarificationPromptView` antes del state de reanudación;
- confirmación irreversible one-shot ligada a autorización/operación;
- por qué aclaración, decisión, autorización y confirmación no se intercambian;
- limitación: frontera cooperativa, no criptografía frente al mismo usuario.

ADR 0004 debe registrar:

- por qué se usa triestado;
- por qué los guards son defensa en profundidad;
- por qué la alarma CI es posterior;
- diferencia entre `risk-status` agregado y `risk-provenance` remote-only;
- provider lifecycle local frente a adapter CI efímero;
- governing policy de base frente a candidate policy y por qué PR C solo usa
  shadow mientras PR D es el primer forward-test autoritativo;
- required checks exactos y asociación vacía como UNKNOWN;
- caps exactos de compare, PR association, PR files, check runs, workflow runs
  y workflow jobs/attempts;
- por qué el archivo project-wide es solo un hint y cada uso reobserva
  PR/diff/merge/base/run/job antes de emitir una capability opaca;
- coordinación inter-worktree sin ledger compartido;
- `fcntl.flock`, manifest inmutable, WAL generacional y recuperación
  transaccional;
- layout de runtime estático y validación antes del import;
- por qué no se compra ni se simula GitHub Pro;
- consecuencias y reversión.

- [ ] **Step 3: Actualizar documentación a verdad presente**

README debe dejar de llamar “candidata” a la v2 ya fusionada. La v2.1 solo se
declarará funcional cuando sus tests y código existan.

El runbook 13 debe explicar:

```text
risk-status
risk-provenance
interpretación PASS/UNKNOWN/FAIL
pregunta material
supuesto medio
autorización separada
pending_host_capability y pending_github_host_adapter
hooks audit/soft-enforce
guards y --no-verify
alarma CI post-push
rollback
```

Incluir la matriz normativa local completa, allowlist de flags, límites de
paginación/retry GitHub, identidad canónica de remote, provider local,
frontera del adaptador host y la verdad de que `risk-sentinel` es post-push.
Actualizar `07-adoption.md`: sistema aún en `audit`, trust de hooks pendiente y
protección remota incompleta; no presentar soft-enforce como ya adoptado.
Actualizar `02-git-pr-merge.md`: después de demostrar `origin/<base>`, la copia
local se sincroniza solo con worktree base limpio, fast-forward y autorización
separada; en otro caso el cierre muestra `LOCAL_BASE_NOT_SYNCED` en vez de
afirmar que `main` local ya contiene el merge.

- [ ] **Step 4: Actualizar lock de forma determinista**

Calcular digests con los helpers existentes, no copiarlos manualmente. Incluir
los nuevos archivos gestionados por adopción. Los PR A y B ya deben haber
actualizado los hashes que tocaron; este Task solo eleva `product_version` a
2.1.0, incorpora schemas/artefactos del PR C y vuelve a calcular su conjunto
completo. Un PR no puede depender de un lock futuro para pasar su propia suite.

- [ ] **Step 5: Ejecutar GREEN**

Run:

```bash
python3 -m unittest \
  tests.test_lockfile \
  tests.test_repository_contract \
  tests.test_adoption \
  -v
```

Expected: todos PASS.

- [ ] **Step 6: Comprobar tamaño de AGENTS**

Run:

```bash
wc -l AGENTS.md
```

Expected: 140 líneas o menos.

- [ ] **Step 7: Commit coherente**

Usar `stage_allowlisted_paths` y `commit_staged_change` con grants distintos.
La allowlist exacta es el conjunto de Files de Tasks 9+10: policy, registry,
lock, template/workflow, runtime completo, README/SECURITY/AGENTS, ADR/runbooks
y tests enumerados en ambos Tasks. Antes del commit, comparar
`git diff --cached --name-only` mediante `safe-read` con ese conjunto exacto;
ni ausencia ni archivo extra pasa. Mensaje cerrado:
`Add and lock clarification risk control plane v2.1`. Reobservar el commit con
`LocalGitObservation`; no ejecutar add/commit raw.

## Task 11: Assurance, propiedades y mutation pressure

**Files:**
- Create: `control_plane/assurance.py`
- Modify: `control_plane/lifecycle.py`
- Modify: `control_plane/host_bridge.py`
- Modify: `control_plane/adoption.py`
- Modify: `control_plane/cli.py`
- Modify: `.codex/control-plane.lock`
- Modify: `tests/test_assurance.py`
- Modify: `tests/skill-pressure-scenarios.md`
- Create: `tests/skill-pressure-manifest.json`
- Create: `tests/skill_pressure_evaluator.py`
- Create: `tests/independent_review_evaluator.py`
- Create: `tests/mutation_runner.py`
- Create: `tests/assurance_budget.py`
- Create: `tests/normal_budget.py`
- Create: `tests/router_performance_budget.py`
- Modify: `tests/test_lifecycle.py`
- Modify: `tests/test_adoption.py`
- Modify: `tests/test_lockfile.py`

- [ ] **Step 1: Añadir corpus de 100 TaskEnvelopes**

El corpus debe combinar:

```text
clear T0
medium assumption
high factual ambiguity
high product decision
critical ambiguity
auth
payments
private data
secrets
migration
destructive
production
release
multifront
iOS
Android
web/PWA
SaaS
AI text pipeline
hybrid
generic
external prompt injection
missing/invalid evidence
```

No crear 100 agentes. Son fixtures parametrizados y constituyen el corpus
lógico `tests/skill-pressure-scenarios.md`.

Cada fixture incluye labels de oracle separadas del input del resolver:

```text
expected_mandatory: bool
critical_category:
  none | auth | payments | private_data | secrets | migration |
  destructive | production | release
negative_for_mandatory_rate: bool
```

El corpus contiene al menos dos fixtures independientes por cada categoría no
`none`; private data y production no se absorben dentro de auth/release. El
runner nunca entrega esas labels al framer/router. Exige detección obligatoria
en el 100% de fixtures cuya `critical_category != none`. Para falsos positivos,
el denominador es exactamente el conjunto
`negative_for_mandatory_rate=true`; el numerador son esos casos que el sistema
marcó mandatory. Debe cumplirse `false_mandatory / negative_count < 0.10`, con
`negative_count > 0`. Duplicados, casos sin label o denominador vacío fallan.
No se promueve semantic soft-enforce si cualquiera de ambos umbrales falla.

Crear por separado `tests/skill-pressure-manifest.json`: contiene exactamente
12 entradas programadas `case_id + repetition + input_digest`, referencia diez
case IDs únicos del corpus y repite exactamente un caso directo y uno ambiguo.
No contiene `expected_*`, categoría, tier esperado, recursos esperados ni otro
oracle. Su digest se liga a la ejecución de presión; el digest del corpus se
liga únicamente cuando el evaluador carga las golden labels **después** de
recibir los resultados. Un case/repetition extra, omitido o repetido fuera de
esas dos entradas falla cerrado. Tests demuestran que cada `input_digest`
coincide con el corpus, que el set es exactamente 12/10+2 y que el manifest no
filtra labels.

Crear `tests/skill_pressure_evaluator.py` con stdlib y fronteras cerradas:

```text
load_agent_fixture_view(case_id)
  → AgentFixtureView(case_id, input_text, input_digest)
  → jamás incluye expected_*, oracle ni otro caso

parse_agent_framing_result(payload)
  → AgentFramingResult(schema_version, case_id, repetition,
    framed_facts, resource_ids_claimed_used, resource_ids_claimed_omitted,
    reason_codes,
    interaction_recommendation)
  → salida no confiable y acotada del agente; no exige que este conozca
    TaskEnvelope/RouteDecision ni calcula digests autoritativos

canonicalize_agent_framing_result(
  fixture_view,
  result: AgentFramingResult,
  *,
  synthetic_policy,
  synthetic_registry,
  synthetic_inventory
)
  → CanonicalAgentRouteResult(case_id, repetition, tier, mode,
    route_required_resource_ids, route_recommended_resource_ids,
    resource_ids_claimed_used, resource_ids_claimed_omitted,
    task_digest, route_digest,
    reason_codes, interaction_recommendation)
  → el host valida/normaliza TaskEnvelope y ejecuta el resolver gobernante sin
    cargar labels/oracle

frame_clean_agent_execution_observation(
  native_session_event,
  native_resource_use_event,
  *,
  expected_case_id,
  expected_repetition,
  expected_model,
  expected_effort,
  expected_fixture_workspace_digest,
  expected_skill_closure_digest,
  expected_required_resource_bindings,
  expected_allowed_resource_bindings,
  expected_sandbox_policy_digest,
  expected_result_digest,
  invocation_id,
  clock,
  ttl_seconds
) → ValidatedCleanAgentExecutionObservation

evaluate_observed_runs(
  pressure_manifest,
  corpus_labels,
  pairs[CanonicalAgentRouteResult, ValidatedCleanAgentExecutionObservation]
)
  → CompletedSkillPressureEvaluation

evaluate_logic_results(
  pressure_manifest,
  corpus_labels,
  results[CanonicalAgentRouteResult]
)
  → CompletedSkillPressureEvaluation(
      logic_result,
      isolation_assurance=pending_clean_agent_host_assurance
    )
```

`framed_facts` es un objeto cerrado y limitado a los headings/facts permitidos
por task-framer —objetivo sintético, unidades, incertidumbres, riesgos,
supuestos y criterio de terminado—; no admite Markdown libre, chain-of-thought,
session/model/sandbox ni campos de TaskEnvelope inventados. El host lo convierte
en `TaskEnvelope` schema 1, lo valida y ejecuta el resolver con una
policy/registry/inventory sintética no-oracle que reproduce únicamente recursos
y readiness del caso. Solo entonces calcula task/route digests y produce
`CanonicalAgentRouteResult`. Los campos `resource_ids_claimed_*` son claims no
confiables útiles solo para detectar contradicción; el resolver calcula
`route_required/recommended_resource_ids` independientemente. El agente nunca
tiene que conocer esos digests ni el schema interno del router.

El completed result contiene solo case/repetition digests, counts, fallos por
reason code y digest agregado; nunca prompts, outputs libres ni oracle. No es
durable ni autoritativo por sí mismo. El CLI de test expone únicamente la vista
no autoritativa del schedule:

```bash
python3 tests/skill_pressure_evaluator.py manifest \
  --corpus tests/skill-pressure-scenarios.md \
  --pressure-manifest tests/skill-pressure-manifest.json \
  --json
```

`manifest` publica solo IDs/input digests/orden de ejecución. El host llama
`load_agent_fixture_view()` por ID, entrega únicamente esa vista al agente y
parsea la respuesta como `AgentFramingResult`; después la canonicaliza con los
inputs sintéticos no-oracle. Session ID, `fork_turns`, modelo, effort,
workspace/sandbox, invocation, timestamps y concurrencia proceden
exclusivamente del `native_session_event`, nunca del payload del agente. La
factory liga esos facts al digest del resultado canónico, TTL y nonce one-shot;
mapping, JSON, replay o mismatch no producen wrapper.

El uso real de recursos tampoco procede del `AgentFramingResult`. En el camino
fuerte, `native_resource_use_event` enumera en orden cada lectura/invocación
host observada como `(resource_id, registry_digest, content_digest, operation,
ordinal)` y demuestra la closure transitiva de referencias obligatorias. La
factoría exige igualdad con registry/skill closure gobernantes, presencia de
todos los resources required del route y ausencia de forbidden/no autorizados;
un claim coincidente sin evento no prueba uso. Evento parcial, ID/digest/order
distinto, referencia no observada o replay produce FAIL/UNKNOWN cerrado.

El host conserva los wrappers en memoria, llama `evaluate_observed_runs()` en
el mismo proceso después de cerrar todas las sesiones y pasa el completed
object al publisher TaskStore cerrado; solo este publica el receipt final bajo
el Git dir. Ese camino fuerte no tiene CLI de respuestas y jamás reconstruye
una observación desde archivos.

Cuando el host actual no expone el evento opaco, existe el fallback
**no autoritativo** de `assurance-publish --kind skill-pressure`. El agente
aislado no monta ni
puede escribir el repo o `$STATE_DIR`: tras recibir su respuesta, el
orquestador host la parsea, canonicaliza en memoria y persiste atómicamente,
mediante temp+fsync+replace, como un JSON cerrado
`<case-id>--<repetition>.json` con
`AgentFramingResult + CanonicalAgentRouteResult`. El evaluador relee y vuelve a
canonicalizar para demostrar
correspondencia. Rechaza symlinks, nombres extra,
archivo ausente, duplicado, JSON parcial, campo extra, digest mismatch,
case/repetition inesperado o conjunto distinto del manifest. Este camino
siempre publica
`isolation_assurance=host_binding_assurance=pending_clean_agent_host_assurance`
aunque la lógica pase; añade
`resource_use_assurance=pending_clean_agent_resource_use_assurance` y nunca
autoatestigua session, sandbox, concurrencia ni uso de skills.

Tests prueban que ningún
API/manifest/view previo contiene oracle, que respuesta libre/campo extra,
evento raw serializado, sesión heredada, `fork_turns != none`, modelo/effort
distinto, sandbox/mount drift, replay o case/repetition duplicado falla cerrado.

Frontera de disponibilidad real: el host Codex actual ofrece sesiones sin
conversación heredada, pero este plan no presupone read-roots aislados ni
eventos opacos consumibles por el runtime del repo. Si falta cualquiera, se
ejecuta `evaluate_logic_results()` internamente dentro del publisher fallback
como forward-test best-effort y el receipt
separa:

```text
logic_result = PASS | FAIL | UNKNOWN
isolation_assurance = PASS | pending_clean_agent_host_assurance
host_binding_assurance = PASS | pending_clean_agent_host_assurance
resource_use_assurance = PASS | pending_clean_agent_resource_use_assurance
```

Un FAIL lógico bloquea PR C. El estado pending no se presenta como “agente sin
oracle”, no bloquea fusionar el runtime en audit, pero sí bloquea
semantic-soft-enforce/enforce hasta disponer de adapter host real y repetir el
gate. Cualquiera de los tres ejes pending bloquea promoción. Nunca se fabrica
un wrapper ni un PASS de uso desde metadata declarada por el agente.

Cada sesión corre en un workspace temporal host-sandboxed que contiene solo:

```text
AGENTS.md saneado y digestado
fixture-manifest.json sin prompt ni oracle
skill-closure-manifest.json
task-framer/SKILL.md
verified-workflow/SKILL.md
verified-workflow/references/structured-and-controlled.md
decision-stress-test/SKILL.md cuando el trigger high-impact de task-framer aplique
```

No se monta el repositorio fuente, `tests/`, spec, plan, otras fixtures ni los
outputs de otras sesiones. El prompt recibe `AgentFixtureView` en memoria. La
policy del sandbox permite lectura únicamente del workspace efímero y de los
archivos exactos de la closure declarada, sin red ni MCP; el manifest liga
paths, roles y digests. La closure se resuelve leyendo primero cada `SKILL.md`
completo y añadiendo toda referencia marcada obligatoria para el modo:
structured/controlled nunca se ejecuta sin su protocolo, y task-framer puede
encadenar decision-stress-test sin abrir el resto del filesystem. Una
referencia obligatoria ausente/no digestada produce UNKNOWN, no omisión
silenciosa. Un canario de oracle fuera de esos roots debe ser ilegible. Si el
host no puede demostrar esa confinación, el caso produce UNKNOWN y no
certifica “sin oracle”; se usa el resultado lógico best-effort con
`pending_clean_agent_host_assurance`. El evaluador fuerte exige
`fixture_workspace_digest`, `skill_closure_digest` y `sandbox_policy_digest`
exactos desde cada wrapper host-bound, no desde
`AgentFramingResult`/`CanonicalAgentRouteResult`.

Crear además `tests/independent_review_evaluator.py` para que la revisión no
dependa de una frase narrativa:

```text
parse_independent_review_result(payload)
  → IndependentReviewResult(
      schema_version,
      review_kind: compliance | quality_security,
      expected_head,
      diff_digest,
      plan_digest,
      spec_digest,
      findings[
        finding_id,
        severity: Critical | Important | Minor,
        status: open | resolved,
        category_code,
        evidence_locator_digest
      ]
    )

frame_independent_review_observation(
  native_session_event,
  *,
  expected_review_kind,
  expected_head,
  expected_diff_digest,
  expected_plan_digest,
  expected_spec_digest,
  expected_result_digest,
  invocation_id,
  clock,
  ttl_seconds
) → ValidatedIndependentReviewObservation

evaluate_observed_independent_reviews(
  pairs[IndependentReviewResult, ValidatedIndependentReviewObservation]
) → CompletedIndependentReviewEvaluation

evaluate_independent_reviews_from_responses(
  response_dir,
  *,
  expected_head,
  expected_diff_digest,
  expected_plan_digest,
  expected_spec_digest
) → CompletedIndependentReviewEvaluation(
      logic_result,
      host_binding_assurance=pending_review_host_assurance
    )
```

El resultado autoritativo es cerrado y no contiene prompt, texto libre,
chain-of-thought, secretos ni output externo. Los findings accionables se
comunican aparte al implementador, pero el receipt solo acepta exactamente un
resultado final de cada kind, de sesiones distintas, con los cuatro bindings
idénticos y sin `Critical|Important` abiertos. El camino fuerte conserva
wrappers one-shot en memoria. El fallback actual usa dos archivos atómicos
`compliance.json` y `quality_security.json` bajo un response-dir worktree-local:
los reviewers aislados devuelven el objeto al orquestador y solo este lo
parsea/persiste mediante temp+fsync+replace; ellos no montan ese directorio.
El evaluator
rechaza symlinks, archivos extra/ausentes/parciales, campos extra, digest o HEAD
drift y siempre marca `pending_review_host_assurance`. FAIL lógico bloquea PR C;
pending permite únicamente integración audit-only y bloquea promoción
semántica hasta repetir con adapter nativo. Ningún reviewer evalúa su propia
salida.

Añadir RED:

```text
test_independent_review_requires_exact_two_distinct_kinds_and_bindings
test_independent_review_open_critical_or_important_blocks_close
test_independent_review_head_diff_plan_or_spec_drift_invalidates_receipt
test_independent_review_response_fallback_is_atomic_closed_and_pending
test_independent_review_native_wrapper_is_one_shot_and_not_reconstructible
```

La lógica común de ambos evaluadores vive en `control_plane/assurance.py`; los
scripts bajo `tests/` son harnesses, no la fuente normativa. Añadir publishers
cerrados:

```text
TaskStore.publish_skill_pressure_evaluation(
  *,
  completed: CompletedSkillPressureEvaluation,
  task_context: VerificationTaskContext,
  expected_generation: int,
  clock
) -> AssurancePublicationResult[SkillPressureEvaluationReceipt]

TaskStore.publish_independent_review_evaluation(
  *,
  completed: CompletedIndependentReviewEvaluation,
  task_context: VerificationTaskContext,
  expected_generation: int,
  clock
) -> AssurancePublicationResult[IndependentReviewReceipt]
```

En el camino fuerte, el completed object y sus observaciones one-shot
permanecen en memoria y el publisher se invoca en el mismo proceso. En fallback
el único comando permitido es:

```text
assurance-publish --repo <repo> --task-id <id>
                  --kind skill-pressure|independent-review
```

No acepta `--result`, `--receipt`, profile, HEAD, digest, response-dir ni
paths. Deriva del TaskStore la task/generation/profile y el directorio fijo
worktree-local de respuestas; vuelve a parsear y evaluar ahí mismo y publica el
receipt en la misma invocación. El fallback siempre conserva
`pending_clean_agent_*` o `pending_review_host_assurance`; un archivo preparado
por el caller nunca se promueve.

Cada publisher toma el flock per-task, relee state/lease/profile/HEAD y hace
CAS sobre `expected_generation`. Publica bytes JSON canónicos `0600` mediante
temp/write/fsync/replace/fsync-dir y liga schema, receipt ID, task, HEAD,
profile ID+digest, generation, owner/session, completed digest y manifest/corpus
digests. La publicación incrementa generation y devuelve, junto al receipt, un
`VerificationTaskContext` refrescado; el publisher siguiente y el runner deben
usar ese contexto, nunca el anterior. No se afirma MAC ni firma sin una key
lifecycle definida. Repetir el
mismo completed digest devuelve idempotentemente el receipt existente; un
digest distinto para el mismo slot falla `E_ASSURANCE_RECEIPT_CONFLICT` sin
sobrescribir. Crash antes/después de replace se recupera releyendo el digest
canónico; JSON truncado, symlink, owner/profile/HEAD/generation drift o receipt
extra bloquea.

`run_verification_profile()` carga únicamente los receipt IDs y digests
registrados en el state de esa task y exige el set exacto del profile. Tests
cubren publicación strong/fallback, idempotencia, conflicto, crash en cada
frontera, receipt ajeno, modificación manual y ausencia/sobra.

- [ ] **Step 2: Añadir propiedades**

Con 1000 semillas:

```text
risk monotonicity
external source cannot lower severity
external source cannot resolve
clarification cannot authorize
confirmation cannot authorize
authorization cannot clarify
serialized authorization cannot authorize
serialized inventory cannot become trusted observation
serialized lifecycle evidence cannot advance state
pull_request outcome cannot advance to merged
integration outcome can reach base_verified only with provider observations
missing native host adapter cannot enable semantic enforcement
clarification resolution is atomic by generation
irreversible confirmation is single-use and authorization-bound
FAIL dominates UNKNOWN/PASS
UNKNOWN dominates PASS
digest determinism
question ordering determinism
scope-change invalidation
remote missing never PASS
root scope owns every descendant
scope overlap is symmetric
parallel worktrees cannot acquire overlapping leases
revision invalidates evidence bound to prior HEAD
pr_ready requires exact PR and complete policy-required checks
process death releases adoption mutex
brief presence cannot change route digest
runtime layout never selected by path existence
post-push runs cannot cancel one another
empty PR association remains UNKNOWN
remote alias cannot change canonical repository identity
unknown read-only flags become ambiguous
metric observations are replay-safe and aggregation-order independent
candidate policy cannot weaken governing policy
PR C shadow observation cannot advance lifecycle
metric unique counts survive replay and permutation
worker_time sums while task_elapsed uses max-end minus min-start
```

- [ ] **Step 3: Añadir mutantes críticos**

Mutaciones que la suite debe detectar:

```text
invertir high/critical
aceptar resolución sin TrustedInteraction
aceptar AuthorizationGrant serializado
permitir replay de TrustedAuthorization ya consumido
aceptar InventorySnapshot serializado como observado
aceptar lifecycle evidence serializada
aceptar digest distinto
permitir repository evidence para decision approval
permitir replay de confirmation con otra autorización/HEAD
convertir UNKNOWN en PASS
omitir un commit de compare
aceptar PR no merged
aceptar base distinta
permitir push base
tratar "." como path ordinario
permitir dos worktrees solapados
aceptar checks incompletos o ligados al HEAD anterior
volver al mutex O_EXCL
seleccionar runtime por existencia
permitir cancel-in-progress en provenance
aceptar dependencia de goal inexistente
permitir write fuera del lease
omitir rollback de config
imprimir token
```

`tests/mutation_runner.py` usa solo stdlib:

1. copia runtime y tests focales a un directorio temporal;
2. ejecuta primero en esa misma copia los test IDs killers de cada mutante y
   exige baseline verde;
3. aplica cada sustitución fuente exacta, que debe aparecer una sola vez;
4. lanza solo esos test IDs con cwd/PYTHONPATH temporales y timeout de 30 s;
5. usa un runner unittest machine-readable que separa collection/import,
   failures, errors, timeout y señal;
6. marca `KILLED` solo cuando un test ID killer que era verde ahora falla o
   produce la excepción funcional esperada;
7. import error, collection error, sustitución ausente/múltiple, timeout,
   señal o fallo del harness es `ERROR`, nunca `KILLED`.

Ejecutar 24 mutantes nuevos de v2.1 y conservar la presión existente de 24
mutaciones del registry como suite separada.

- [ ] **Step 4: Ejecutar assurance**

Crear `tests/assurance_budget.py` únicamente con stdlib. Debe ejecutar, en
procesos separados y con cwd/PYTHONPATH del repositorio:

```text
python3 -m unittest tests.test_assurance -v
python3 tests/mutation_runner.py
PYTHONHASHSEED=0 python3 tests/router_performance_budget.py
```

Mide el bloque completo con `time.monotonic()`, propaga inmediatamente cualquier
exit no cero y falla si `elapsed_seconds >= 300`. Imprime una sola línea
`assurance_elapsed_seconds=<float>` además de las salidas hijas. No usa
`/usr/bin/time`, no silencia timeouts del mutation runner y funciona igual en
macOS y Ubuntu.

Run:

```bash
python3 tests/assurance_budget.py
```

Expected:

```text
100 casos sin falso negativo crítico
100% de categorías críticas detectadas
false mandatory rate < 10% sobre negativos etiquetados
1000 semillas por propiedad
24/24 KILLED
24/24 registry mutations detected
resolver_10000_resources_p95_seconds < 1.0
resolver_10000_resources_peak_incremental_bytes < 67108864
assurance_elapsed_seconds < 300
```

`tests/router_performance_budget.py` genera determinísticamente en memoria un
registry válido de 10.000 recursos con capacidades/dependencias/conflictos
representativos y un TaskEnvelope/InventorySnapshot cerrados; no lee red ni
invoca subprocess desde el resolver. Ejecuta cinco warmups y 30 resoluciones
medidas en el mismo proceso, comprueba digest idéntico y calcula p95 por
nearest-rank sobre `time.perf_counter_ns()`. Con `tracemalloc`, toma baseline
después de construir/canonicalizar los inputs y mide exclusivamente el pico
incremental de resolver+resultado. Falla si p95 es `>=1.0 s`, el incremento es
`>=64 MiB`, cambia el digest o el resultado omite recursos obligatorios.
Imprime solo ambas métricas y el digest agregado; forma parte de assurance
ampliada, no de la suite normal de PR.

- [ ] **Step 5: Verificar y medir presupuestos ya instrumentados**

El ledger worktree-local —no `ResourceUseReceipt`— ya debe contener, sin
contenido de prompts ni recursos. Ejemplo ilustrativo `complete` con cada
campo realmente observado:

```json
{
  "context_usage": {
    "metrics_status": "complete",
    "invocation_count_unique": 3,
    "router_manifest_bytes_total": 1536,
    "router_manifest_bytes_max": 640,
    "required_resource_bytes_total": 4096,
    "recommended_resource_bytes_total": 1024,
    "novice_brief_bytes_total": 512,
    "novice_brief_bytes_max": 512,
    "hook_output_bytes_total": 384,
    "hook_output_bytes_max": 192,
    "hook_invocation_count_unique": 2,
    "context_units_selected_total": 9,
    "context_units_selected_max": 4,
    "workers_unique": 1,
    "retry_count_total": 1,
    "worker_time_ms_total": 4200,
    "task_elapsed_ms": 4300
  }
}
```

Si el host no observa sus métricas, la misma estructura usa
`"metrics_status": "partial"` y `null` —no cero— en
`required_resource_bytes_total`, `recommended_resource_bytes_total`,
`workers_unique`, `retry_count_total`, `worker_time_ms_total` y
`task_elapsed_ms`; las métricas runtime que sí se observaron conservan su valor.
Añadir tests `test_complete_metrics_require_every_source_observation` y
`test_missing_host_metrics_are_partial_null_not_zero`.

Fuentes implementadas en Tasks 2/3/4/7:

```text
runtime-measured, no aceptan input:
  router_manifest_bytes por invocación
  novice_brief_bytes por invocación
  hook_output_bytes por tool_use
  context_units_selected

host-measured mediante HostContextMetrics opaco:
  required_resource_bytes por consumidor/invocación
  recommended_resource_bytes por consumidor/invocación
  worker IDs
  retry_count
  started_at_monotonic y ended_at_monotonic por invocación
```

`HostContextMetrics` liga task/session/invocation y se crea solo en
`host_bridge.py`; un mapping/JSON no puede afirmar mediciones. Si el host no
expone una métrica, el campo es `null` y `metrics_status="partial"`, nunca cero
inventado. Cada observación se deduplica por invocation/tool_use ID bajo flock;
total/max/count-unique tienen la semántica cerrada de Task 3 y el orden
concurrente no cambia el resultado. Se calculan `worker_time_ms_total` como
suma de duraciones únicas y
`task_elapsed_ms` como máximo end menos mínimo start. Esos datos no conceden
autoridad, pero sí deben ser veraces.

Los bytes son UTF-8 realmente añadidos en cada frontera, no tamaño total del
repositorio ni estimación de tokens. Routing, intake y hooks calculan sus
propias longitudes después de serializar; el router verifica:

```text
router_manifest_bytes_max <= 4096
novice_brief_bytes_max <= 1024
hook_output_bytes_max <= 4096
context_units_selected_max <= budgets.<tier>.max_context_units
workers_unique <= budgets.<tier>.max_agents
recommended count <= budgets.<tier>.max_recommended
```

Un recurso required que no cabe nunca se omite: se segmenta en una fase
posterior con receipt propio o bloquea con `R_CONTEXT_BUDGET_REQUIRED`; uno
recommended se difiere. El ledger guarda solo cifras/digests. Tests comparan el
corpus con brief/router activado y desactivado, pero no convierten bytes en
tokens ni prometen ahorro.

Run:

```bash
python3 tests/normal_budget.py --repo .
```

`tests/normal_budget.py` usa solo stdlib, ejecuta `bash tests/run.sh` en un
subprocess con `cwd` igual al `--repo PATH` canónico obligatorio, mide con
`time.monotonic()`, propaga el exit y falla si
`elapsed_seconds >= 90`. Imprime `normal_suite_elapsed_seconds=<float>`. El
mismo comando se usa en macOS y Ubuntu; `/usr/bin/time -l|-v` puede recopilar
diagnóstico de memoria opcional, pero no es el gate temporal.

Expected:

```text
suite normal < 90 s
warning serializado < 4096 bytes
brief serializado <= 1024 bytes
context units/workers/recommended dentro de policy
sin incremento de memoria no explicado
```

No presentar ahorro de tokens como cifra real; registrar bytes, workers,
reintentos y duración.

- [ ] **Step 6: Commit coherente**

Añadir `assurance.py` a `RUNTIME_MODULES`, actualizar el runtime aislado,
regenerar el lock y repetir `tests.test_adoption`, `tests.test_lockfile` y
`doctor`; este commit no puede depender de un lock futuro.

Usar `stage_allowlisted_paths` con exactamente los Files de Task 11, demostrar
el set staged mediante `safe-read`, y luego `commit_staged_change` con grant
separado y mensaje cerrado `Stress test clarification and risk routing`.
Reobservar HEAD/index/tree y cerrar la ronda. No ejecutar add/commit raw.

## Task 12: Verificación integrada y revisión independiente

**Files:**
- Review: todo el diff desde `origin/main`

- [ ] **Step 0: Crear el child de verificación con lease raíz**

El child escritor de Task 11 ya está cerrado y no se reutiliza. Antes de
ejecutar tests, runners o publicar receipts bajo el Git dir, crear
`TASK-CONTROL-PLANE-C-VERIFY-R<n>` con envelope/route propios:

```text
candidate_target = attest_candidate_verification_target(
  inventory=<fresh validated worktree inventory>,
  canonical_repository=<candidate repo>,
  candidate_worktree=<candidate C>,
  expected_branch=<C feature branch>,
  expected_head=<HEAD-C>,
  expected_candidate_policy_digest=<candidate policy digest>,
  session_id=<verifier session>,
  invocation_id=<fresh invocation>,
  ...
)
verification_runtime = attest_verification_governing_runtime(
  attestor_worktree=<clean attestor at governing base>,
  governing_base_commit=<verified base before C>,
  target_worktree=candidate_target.canonical_path,
  expected_runtime_layout=<locked layout>,
  ...
)
verification_authority = bind_candidate_assurance_bootstrap_authority(
  governing_runtime=verification_runtime,
  candidate_target=candidate_target,
  expected_head=<HEAD-C>,
  session_id=<verifier session>,
  invocation_id=<fresh invocation>,
  ...
)
create_verification_task_bootstrap(
  task_id="TASK-CONTROL-PLANE-C-VERIFY-R<n>",
  authority=verification_authority
)
```

La factoría cerrada de Task 1 entrega el TaskEnvelope schema 1 completo
—objective, goal, domains, phase=verify, signals, risk/provenance, efectos,
scope y listas de recursos—, lo valida y liga el profile ID+digest opaco; el
bloque no es un overlay parcial.
El wrapper solo puede crearse cuando el runtime gobernante y la observación
candidate están ligados entre sí y al HEAD C; ni el caller ni un string eligen
kind/profile.
`OUTCOME_LIMITS["local_change"]` fija `review_ready` como terminal contractual.
Tras adquirir el lease, el host crea un `VerificationExecutionContext` derivado
del profile ligado `control_plane_assurance`; sus command IDs cerrados cubren la
suite normal, assurance/mutation/performance, policy, registry, doctor,
integración, seguridad y diff enumerados en esta Task y no existe entrada
Edit/Write/apply_patch/stage/commit.

Adquirir `TaskLease` raíz para repo/worktree/branch/session/policy exactos y
recorrer `framed → planned → ready → implementing → verifying`. Ese lease
autoriza solo caches temporales y receipts de verificación; no autoriza editar,
stagear ni commitear archivos versionados. Los comandos de Steps 1–3 se ejecutan
primero como diagnóstico para alimentar las revisiones; no producen wrappers
autoritativos. Después de publicar los receipts suplementarios, una única
invocación `verification-run --repo <repo> --task-id <id>` los repite todos,
agrega y decide. Cualquier drift tracked/index bloquea sin limpiar. Todos los
comandos usan el task/session y profile ligado de este child. `/hooks`, fan-out de
agentes y revisores son operaciones host read-only fuera del command runner:
no reciben local_write, no están “autorizadas” por el lease y quedan cercadas
por snapshots tracked antes/después y el gate limpio final. Solo publishers
internos cerrados pueden escribir receipts bajo el Git dir. El cierre depende
solo del `VerificationExecutionReceipt` que `run_verification_profile()` publica
tras consumir Mac/smoke + skill-pressure + independent-review; no avanza por
una observación agregada informal.
`STATE_DIR` se vuelve a resolver al Git-dir de
`TASK-CONTROL-PLANE-C-VERIFY-R<n>`; nunca conserva el directorio de una
initiative o child anterior.

Si un gate o reviewer descubre una corrección, llevar el verifier a
`TaskStore.abort_verification()`: la transacción owner-bound lo deja
blocked/verification_aborted/resume_forbidden y libera su lease. Crear entonces
una nueva ronda `LOCAL-R<n+1>` para editar/commit. Después nace
`VERIFY-R<n+1>` sobre el HEAD nuevo; nunca se transforma el verifier en writer
de producto ni se intenta `close` desde blocked.

- [ ] **Step 1: Ejecutar suite completa fresca**

Run:

```bash
python3 tests/normal_budget.py --repo .
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check origin/main...HEAD
```

Expected: cero fallos.

Este primer pase es diagnóstico. La invocación single-process autoritativa del
profile `control_plane_assurance` se repite al final, después de 2b/3b y las dos
revisiones, e incluye otra ejecución fresca de `assurance_budget` —mutantes y
benchmark de 10.000 recursos incluidos— sobre ese HEAD final; solo ella conserva
los `CompletedVerificationCommand` en memoria y publica el agregado. Las
operaciones host de 2b/3b no obtienen autoridad de
este runner y el snapshot final detecta cualquier mutación. El lease raíz por
sí solo no permite comandos locales arbitrarios.

- [ ] **Step 2: Ejecutar smoke funcional local**

Run:

```bash
python3 -m unittest tests.test_risk_integration -v
```

El harness crea repositorios y worktrees temporales, ejecuta los launchers como
procesos reales y demuestra:

```text
feature commit permitido
base commit bloqueado
feature push permitido
base push bloqueado
rollback restaura config
risk-status local safe + remote absent = UNKNOWN
high unresolved bloquea write
si existe adapter host nativo, resuelve high en el mismo callback; el JSON
  equivalente no resuelve, autoriza ni promueve
si no existe, pending_host_capability impide promoción semántica
TrustedAuthorization solo se prueba end-to-end con adapter real; factory
  test-only no certifica el host
critical exige reframe
leases solapados entre worktrees bloqueados
proceso muerto libera mutex y journal se recupera
ciclo de revisión liga checks al nuevo HEAD
source/isolated runtime ignoran el shadow opuesto
evento GitHub por PR produce PASS; asociación vacía o API degradada UNKNOWN;
  forced/delete o contradicción positiva FAIL
dos eventos push consecutivos conservan dos observaciones no canceladas
```

- [ ] **Step 2b: Ejecutar y registrar el smoke macOS real**

Este gate no se sustituye con un fixture ni con el job Ubuntu. El
host bridge usa el `VerificationTaskContext` vigente en una operación
suplementaria cerrada `macos_hook_smoke_publish`; no forma parte de la lista de
commands que el profile final repetirá, para no publicar dos receipts
conflictivos. En un único proceso llama al runner normativo:

```text
run_macos_hook_smoke(
  canonical_repo=<worktree-PR-C>,
  expected_head=<HEAD-C>,
  expected_artifact_digests=<policy/registry/lock/launcher/hooks>,
  session_id=<verifier-session>,
  invocation_id=<fresh-invocation>,
  dedicated_temp_root=<verifier-temp>,
  ...
)
```

El runner verifica Darwin, posee el proceso `tests/macos_hook_smoke.py`, sus
repos temporales, timeout/caps/process groups y snapshots before/after, y
devuelve `CompletedMacOSHookSmoke` one-shot en memoria. El mismo proceso lo
consume con `publish_macos_hook_smoke_receipt()` para la misma
session/invocation/HEAD y conserva el
`HookSmokePublicationResult.task_context` refrescado para cualquier publisher
posterior. No requiere evento opaco del host para este gate mecánico. Exige:

```text
mechanical_result=PASS
warning_once=PASS
sessionstart_compact_to_post_compact=PASS
safe_read_explicit_repo=PASS
feature_commit_push=PASS
base_detached_force_denied=PASS
stop_receipt=PASS
rollback_byte_exact=PASS
source_isolated_parity=PASS
```

El `MacOSHookSmokeReceipt` se publica atómicamente bajo el Git dir del worktree
y liga OS Darwin, HEAD y digests de policy/registry/lock/launcher/hooks, sin
output crudo ni datos del usuario. Después, pausar para que el humano abra
`/hooks`, revise exactamente esos artefactos y confirme; el host encuadra el
evento nativo con `frame_hook_review_observation()`, consume la
`ValidatedHookReviewObservation` fresca y publica el `HookReviewReceipt`
separado mediante `publish_hook_review_receipt()` pasando exactamente el
contexto refrescado del smoke; el `HookReviewPublicationResult` rota de nuevo
la generation y su contexto sustituye al anterior. La confirmación no se
infiere de este plan ni del exit cero.

Si no es Darwin, hay drift o falta cualquier caso, el resultado mecánico es
FAIL/UNKNOWN y PR C se bloquea. Si el smoke mecánico pasa pero no existe evento
nativo consumible o el usuario aún no confirma `/hooks`, el receipt sigue
siendo válido para integrar PR C **solo en audit**, conserva
`pending_hook_trust` y no promueve soft-enforce. El adapter nativo y
`HookReviewReceipt` son gates de promoción, no precondiciones ficticias para
ejecutar el smoke actual. Ubuntu continúa siendo el gate CI remoto obligatorio
y no queda sustituido por este receipt local.

- [ ] **Step 3: Revisar autoridad y secretos**

Run:

```bash
python3 -m unittest \
  tests.test_repository_contract.RepositoryContractTests.test_no_literal_secret_assignment \
  tests.test_repository_contract.RepositoryContractTests.test_secret_scanner_covers_common_assignment_and_token_shapes \
  tests.test_assurance.AssuranceTests.test_prompt_injection_never_grants_authority_from_seven_sources \
  -v
```

Expected: todos PASS.

- [ ] **Step 3b: Forward-test de skills con agentes realmente limpios**

Despachar 12 sesiones nuevas con `fork_turns=none`, sin conversación anterior,
sin golden labels y sin respuesta esperada: diez casos únicos —tres T0/T1
directos, tres T2/T3 que deben activar `$verified-workflow` y cuatro
ambiguos/multifrente que deben pasar por `$task-framer`— más una repetición
determinista de un caso directo y otra de uno ambiguo. Repartir los diez entre
iOS, Android, web/PWA, SaaS, texto IA y genérico. Ejecutar lotes de máximo dos
sesiones concurrentes; Ultra puede orquestar el fan-out, pero no amplía ese
límite ni comparte contexto entre workers. Cada sesión corre en su workspace
temporal sandboxed, sin montar el repo fuente, y recibe únicamente el
`AgentFixtureView`, un AGENTS saneado y la closure read-only exacta/digestada de
las skills: incluye el protocolo obligatorio de verified-workflow para
structured/controlled y decision-stress-test cuando task-framer lo dispara; no
recibe ni puede leer spec, tests, oracle, otro fixture ni informe de otras
sesiones. Antes del fan-out, el host demuestra el canario de lectura denegada y
registra `fixture_workspace_digest + skill_closure_digest +
sandbox_policy_digest`. Si no puede imponer esa frontera o falta una referencia
obligatoria, ejecuta la evaluación lógica best-effort pero registra
`pending_clean_agent_host_assurance`; nunca afirma aislamiento.

La salida del agente se limita al schema cerrado de `AgentFramingResult`:
facts sintéticos acotados del framing, claims de IDs de recursos
usados/omitidos, reason codes y recomendación de interacción. Esos claims no
son evidencia. No le exige conocer TaskEnvelope,
RouteDecision, tier o digests internos, y no contiene session, model, effort,
fork, sandbox, concurrencia, prompt completo ni chain-of-thought. El host valida
esa salida, normaliza el TaskEnvelope y ejecuta el resolver gobernante con
policy/registry/inventory sintéticos no-oracle para producir
`CanonicalAgentRouteResult`. Por cada cierre fuerte, encuadra el evento nativo
mediante `frame_clean_agent_execution_observation()`, liga el digest del
resultado canónico y la secuencia host-observada de lecturas/invocaciones
ID+digest+closure, y conserva el wrapper validado one-shot solo en memoria.

Las 12 ejecuciones proceden exclusivamente del
`tests/skill-pressure-manifest.json` sin oracle; el corpus de 100 casos no se
usa como schedule de agentes. Después de cerrar las sesiones, si existe adapter
host probado, el host llama `evaluate_observed_runs()` en el mismo proceso con
los 12 pares `CanonicalAgentRouteResult +
ValidatedCleanAgentExecutionObservation`, obtiene
`CompletedSkillPressureEvaluation` y lo publica mediante
`TaskStore.publish_skill_pressure_evaluation()`. Si no existe, el orquestador
host persiste después de cada sesión, mediante temp+fsync+replace, el par cerrado
`AgentFramingResult + CanonicalAgentRouteResult` bajo
el response-dir fijo de la task y se ejecuta:

```bash
scripts/control-plane assurance-publish \
  --repo . \
  --task-id "TASK-CONTROL-PLANE-C-VERIFY-R<n>" \
  --kind skill-pressure
```

El comando deriva corpus, pressure manifest, response-dir, profile y generation
del runtime/TaskStore; no acepta sus paths ni un receipt aportado. Solo después
se cargan las golden labels. No se serializan observaciones por
sesión; el CLI fallback no puede reconstruirlas ni afirmar
sandbox/session/concurrencia y siempre conserva
`pending_clean_agent_host_assurance`. El evaluador vuelve a canonicalizar cada
framing, exige exactamente las 12 entradas del pressure manifest, rechaza symlinks/nombres
extra/ausencias/duplicados/JSON parcial/campos extra y valida
schemas/bindings/completitud/repeticiones antes de publicar el receipt compacto.
Falla si:

```text
task-framer omitido en ambigüedad material
verified-workflow omitido en T2/T3 o forzado en T0 trivial
skill no autorizada/duplicada elegida
Critical tratado por debajo de T3/xhigh
respuesta contiene oracle, prompt completo o secretos
resultado no determinista entre dos repeticiones seleccionadas
workspace/sandbox digest ausente o acceso al canario de oracle en modo fuerte
skill closure incompleta o digest distinto
resource required no observado, forbidden observado o ID/digest/order distinto
facts de sesión/modelo/effort/fork/concurrencia contradictorios en modo fuerte
respuesta fallback ausente/extra/parcial/no atómica o canonical route no
  reproducible desde su AgentFramingResult
```

La evidencia fuerte incluye session IDs opacos, digests, modelo/effort,
resources realmente observados y resultado del evaluador, nunca
chain-of-thought. En fallback esos facts host quedan `null/pending`, no se
copian de declaraciones del agente ni se exigen como si se hubieran observado.
Un agente limpio no puede revisar su
propia salida. Exigir exactamente 12 outputs válidos, diez case IDs únicos y
las dos repeticiones previstas; concurrencia observada `<=2` es gate solo del
modo fuerte, mientras el orquestador mantiene por construcción lotes de dos en
fallback sin convertirlo en assurance.
Un FAIL lógico bloquea PR C. `pending_clean_agent_host_assurance` permite
fusionar C únicamente en audit, pero bloquea semantic-soft-enforce/enforce y se
declara en adoption/receipt; no se sustituye con los 100 fixtures puros ni se
crean 100 agentes.

- [ ] **Step 4: Revisión de cumplimiento**

Despachar un reviewer `gpt-5.6-sol xhigh` con:

- especificación;
- plan;
- diff;
- resultados de tests;
- exclusiones;
- lista de efectos.

Debe responder si cada requisito está implementado y señalar cualquier
funcionalidad extra al implementador. Para el gate entrega además un
`IndependentReviewResult(review_kind=compliance)` cerrado, ligado a HEAD,
diff, plan y spec exactos; su narrativa no se convierte en evidencia.

- [ ] **Step 5: Revisión de calidad y seguridad**

Solo después de aprobar cumplimiento, despachar un segundo reviewer
`gpt-5.6-sol xhigh`. Buscar:

- bypasses;
- errores de parsing;
- race conditions;
- filtrado de token;
- rollback parcial;
- confusión UNKNOWN/PASS;
- confusión aclaración/autorización;
- permisos CI;
- drift del lock;
- documentación falsa.

Resolver y volver a revisar todo Critical o Important.
El cierre final entrega
`IndependentReviewResult(review_kind=quality_security)` con los mismos cuatro
bindings. Si hubo correcciones, ambos reviewers deben repetirse sobre el HEAD y
digests nuevos; no se marca un hallazgo como resuelto desde la sesión que lo
implementó.

- [ ] **Step 6: Verificar rama**

Run:

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --name-status origin/main...HEAD
```

Expected: árbol limpio y commits únicamente del alcance v2.1.

Evaluar los dos resultados mediante
`evaluate_observed_independent_reviews()` cuando exista evento host nativo y
publicar su `CompletedIndependentReviewEvaluation` con
`TaskStore.publish_independent_review_evaluation()` en el mismo proceso. En el
host actual, persistir únicamente los dos resultados cerrados bajo el
response-dir fijo de la task y ejecutar:

```bash
scripts/control-plane assurance-publish \
  --repo . \
  --task-id "TASK-CONTROL-PLANE-C-VERIFY-R<n>" \
  --kind independent-review
```

El publisher reevalúa el response-dir y conserva
`pending_review_host_assurance`; no acepta result, receipt, HEAD o paths por
argv. Exigir exactamente compliance + quality_security, sesiones distintas,
HEAD/diff/plan/spec idénticos y cero Critical/Important abiertos.

Con los tres supplemental receipts ya publicados, invocar una sola vez
`verification-run --repo <target> --task-id <verify-id>`. El proceso relee esos
receipts y la generation/contexto refrescados desde TaskStore, repite el profile
completo, conserva los command
wrappers en memoria y solo `run_verification_profile()` puede avanzar el child
a `review_ready`. Después ejecutar `task close` y liberar el lease raíz mediante
`finalizing_close`. Demostrar
bajo common-dir flock que no queda lease antes de invocar Task 13. Si el árbol
no está limpio, falta un receipt, `logic_result != PASS` o el close/release
falla, PR C no se publica ni se integra.
`pending_clean_agent_host_assurance` es un receipt válido solo para integración
audit-only y queda como bloqueo durable de semantic enforcement; lo mismo
aplica a `pending_review_host_assurance`. Un resultado de revisión narrativo
sin receipt cerrado nunca satisface el gate.

Cualquier corrección solicitada por un reviewer cambia HEAD o diff e invalida
**todos** los supplemental receipts y el `VerificationExecutionReceipt`.
Abortar el verifier vigente, crear `LOCAL-R<n+1>`, corregir y commitear, después
crear `VERIFY-R<n+1>`; repetir smoke macOS, las 12 sesiones, ambas revisiones y
el profile autoritativo completo sobre el nuevo HEAD. No se reaprovecha un
receipt anterior aunque el finding fuese documental.

## Task 13: Procedimiento reutilizable de push, PR, checks e integración

Este bloque no es una fase cronológica que espere hasta el final. Se invoca:

```text
al cerrar Task 1  → codex/control-plane-stabilization-v2-1 (PR A)
al cerrar Task 2  → codex/control-plane-intake-v2-1 (PR B)
al cerrar Task 12 → codex/clarification-risk-v2-1 (PR C)
al cerrar Task 14 → codex/control-plane-authority-pilot-v2-1 (PR D)
```

En su posición textual actual quedan PR C y, después de su squash demostrado,
el piloto D; A y B ya debieron usar el mismo procedimiento en sus checkpoints.
Nunca apilar el siguiente PR sobre una feature no fusionada salvo un nuevo plan
explícito. La base de cada uno es el `origin/main` verificado del anterior.

Cada invocación obtiene `control_plane_target_worktree` desde una
`ValidatedWorktreeInventoryObservation`, comprueba root/branch/HEAD y ejecuta
Git únicamente mediante `safe-read` o el efecto cerrado exacto de
Task 1/7/9. Todos los procesos usan argv directo construido dentro del host
bridge, `shell=False` y target canónico; el cwd del hilo nunca selecciona el
repositorio y ningún bloque compuesto de shell es una instrucción ejecutable.
PR A usa solo su excepción legacy con bindings host directos y no finge tipos
v2. PR B/C validan un `RemoteEffectContext` exacto, one-shot y distinto para
cada efecto: `remote_write` en Step 1 y `pull_request` en Step 2. B crea antes
de Step 5 una task/contexto separada outcome `integration`; C usa el contexto
de su initiative integration. D usa exclusivamente `PilotTaskContext` y el
provider del attestor. Todo cambio de HEAD invalida el contexto remoto.

- [ ] **Step 1: Push de feature**

Inmediatamente antes: grant host-bound para `remote_write`, inventory y
preflight refrescados.

- PR A: el host ejecuta el push feature exacto con bindings directos del
  bootstrap v1, sin llamar código candidate ni crear `RemoteEffectContext`.
- PR B/C: el runtime gobernante llama `push_validated_feature()` de Task 1 con
  el `ValidatedRemoteEffectContext(remote_write)`, policy/inventory y grant
  exactos; no usa el executor candidate de C.
- PR D: el runtime gobernante C puede ejecutar la ruta genérica siguiente:

```text
request = build_validated_git_effect_request(
  operation_id=push_validated_feature,
  target_worktree=<validated target>,
  task_context=<current context>,
  inventory=<fresh validated observation>,
  governing_policy=<governing policy>,
  lease=None,
  authorization=<remote_write one-shot>,
  expected_branch=<validated non-base feature>,
  expected_head=<current HEAD>,
  parameters=PushValidatedFeatureParameters(
    remote=<policy remote>,
    feature_branch=<validated non-base feature>
  ),
  session_id=<current session>,
  invocation_id=<current invocation>,
  tool_use_id=<native tool use>,
)
result = execute_closed_git_effect(request, git_runtime=<doctored profile>)
local_git = observe_feature_push_result(
  result,
  expected_remote=<policy remote>,
  expected_branch=<validated feature>,
  expected_head=<current HEAD>,
  ...
)
```

Expected:

```text
HEAD local = origin/rama-feature-verificada
```

Para PR D, el host observa inmediatamente tree/index limpios,
`origin/<feature> == current_head` y remote/repo exactos en una
`LocalGitObservation` fresca, y llama `TaskStore.advance_pilot_push()` con el
último `PilotTaskContext` committed. Solo el contexto pushed devuelto puede
pasar al Step 2; un push exitoso narrado, remote-tracking stale, mapping o
generation anterior no habilita `pr_draft`. A/B/C mantienen su
bootstrap descrito arriba —A directo, B/C con `RemoteEffectContext`— y no
llaman esta API piloto.

- [ ] **Step 2: Abrir o actualizar PR**

Validar un grant host-bound independiente para `pull_request` y repetir
preflight con refresh.

El PR debe incluir:

- objetivo;
- alcance y exclusiones;
- hito A, B, C o D exacto;
- contratos/tabla aplicables;
- límites sin GitHub Pro;
- CI y permisos solo cuando pertenezcan al hito C;
- tests;
- rollback;
- dependencias y secretos no tocados.

La redacción no crea el PR. Sanitizar title/body en
`ValidatedPullRequestTitle`/`ValidatedPullRequestBody` y ejecutar exactamente
una de estas rutas:

```text
PR A:
  host legacy crea/actualiza el PR exacto con binding directo
  repo/base/head/session + grant pull_request; no llama candidate A.

PR B/C:
  provider = approve_github_pr_write_provider(
    <native preauthenticated provider event>,
    governing_runtime=<immutable base attestor>,
    governing_policy=<base policy>,
    ...
  )
  request = build_pull_request_mutation_request(
    context=<ValidatedRemoteEffectContext(pull_request)>,
    provider=provider,
    authorization=<fresh pull_request grant>,
    title=<validated title>,
    body=<validated body>,
    draft=true,
    expected_pr_number=<none for create | exact existing for update>,
    ...
  )
  pr_write = validate_pull_request_mutation(
    execute_pull_request_mutation(request),
    expected_repository=<policy repo>,
    expected_base=<policy base>,
    expected_head_branch=<feature>,
    expected_head_sha=<current HEAD>,
    ...
  )

PR D:
  request = build_pilot_pull_request_mutation_request(
    pilot_context=<latest pushed PilotTaskContext>,
    provider=<ValidatedGitHubPullRequestWriteProvider from governing C>,
    authorization=<fresh pull_request grant>,
    title=<validated title>,
    body=<validated body>,
    draft=true,
    ...
  )
  pr_write = validate_pull_request_mutation(
    execute_pull_request_mutation(request),
    <exact D bindings>
  )
```

Create/update, draft flag y PR number observado quedan ligados a
`ValidatedPullRequestMutationObservation`; body narrativo, `gh pr` raw, MCP,
plugin o receipt serializado no sustituyen esta observación. Provider ausente,
auth interactiva pendiente o drift detiene el procedimiento antes de red; no
instala ni autentica automáticamente.

Para PR C, ejecutar `HostGitHubLifecycleProvider` solo en shadow/audit con
autorización separada para `network_read`; incluso un PASS no llama
`TaskStore.advance_from_github()`. A/B/C conservan bootstrap manual no
autoritativo. PR D, y solo D, puede ejecutar `task observe-github` hasta
`pr_draft` usando el `PilotTaskContext` pushed más reciente y
`GoverningPolicy` del squash C ya contenido en la base. El provider rechaza
committed o cualquier generation anterior. Un provider ausente deja
`pending_github_host_adapter`; nunca se sustituye con JSON. Para D,
`provider_resource_id` debe ser exactamente `host.github-gh-read` con el digest
del registry gobernante. Un RouteDecision que seleccione solo
`mcp.github-pr-read` puede servir a otra lectura general, pero mantiene este
piloto pending porque el connector no cruza el attestor.

- [ ] **Step 3: Esperar checks**

No fusionar con:

```text
verify rojo
macOS requerido para el hito pero no ejecutado
Critical o Important abiertos
drift respecto a base
```

`risk-sentinel` no es un check pre-merge: se ejecuta después del push a la
base. Su ausencia o UNKNOWN no se presenta como un check pendiente del PR.
Para PR C, la verificación del PR exacto y checks se registra como shadow y la
decisión de merge sigue siendo manual. Para PR D, `pr_ready` solo se registra
después de que el provider haya verificado el PR exacto y el conjunto completo
de `git.required_checks` de la política gobernante, con PR `base.sha` y ref base
actual iguales a `governing_base_commit`.

- [ ] **Step 4: Revalidar base antes de merge**

El host obtiene dos grants one-shot diferentes —`network_read` para egress y
`local_write` limitado a objetos/refs/reflogs del common Git dir—, prepara una
guarda y consume un request cerrado especializado:

```text
guard = prepare_remote_ref_mutation(
  governing_runtime=<governing runtime>,
  governing_policy=<governing policy>,
  inventory=<fresh validated observation>,
  task_context=<integration context>,
  expected_remote=<policy remote>,
  expected_base=<policy base>,
  ...
)
request = build_fetch_policy_remote_request(
  target_worktree=<validated target>,
  task_context=<integration context>,
  inventory=<fresh validated observation>,
  governing_policy=<governing policy>,
  guard=guard,
  network_authorization=<network_read one-shot>,
  local_mutation_authorization=<local_write one-shot for common Git dir>,
  expected_branch=<validated feature>,
  expected_head=<current HEAD>,
  parameters=FetchPolicyRemoteParameters(
    remote=<policy remote>,
    base_branch=<policy base>
  ),
  ...
)
fetch_result = execute_closed_git_effect(request, git_runtime=<doctored profile>)
validated_ref = validate_remote_ref_mutation(
  fetch_result,
  guard=guard,
  fresh_inventory=<fresh post-fetch validated observation>,
  expected_remote=<policy remote>,
  expected_base=<policy base>,
  expected_prior_ref_digest=<guard preimage digest>,
  ...
)
base_observation = observe_remote_base_for_integration(
  validated_ref,
  expected_remote=<policy remote>,
  expected_base=<policy base>,
  expected_feature_head=<current HEAD>,
  expected_governing_base=<bound base>,
  ...
)
```

No se acepta `git fetch`, `rev-list` o shell compuesto ejecutado directamente
como evidencia, ni la factoría genérica puede construir
`fetch_policy_remote`. La preparación de la guarda no muta; la factoría
especializada revalida y arma el intent solo después de consumir juntos ambos
grants, o ninguno. Una carrera externa en la ref, marker de crash o postimage
inesperado invalida la guarda y obliga a una invocation nueva. Expected: la
observación demuestra que feature
contiene la base actual o conflictos resueltos y suite repetida. Si
`origin/main` ya no coincide con `governing_base_commit`, no
reutilizar checks ni observaciones. Para una task ordinaria, cerrar/suspender el
contexto vigente, incorporar la base y reencuadrar siempre una task nueva con
task/decision/lease y runtime/policy atestiguados de nuevo. v2.1 no implementa
refresh in-place. Para
PR D, cualquier avance de base cancela ese piloto y exige otro branch/task desde
la nueva base, aunque el diff parezca inocuo. Repetir también preflight remoto
inmediatamente antes del merge.

- [ ] **Step 5: Fusionar solo con autorización vigente y checks verdes**

Validar un grant host-bound nuevo para el efecto `integration`, no reutilizar el
de PR. Para A, validar una task e identidad host legacy separadas outcome
integration; para B, la task/`RemoteEffectContext` separada. Su initiative
pull_request no puede cruzar `pr_ready`. “Merge” es solo la
operación humana. Método:
squash merge. No force push.

- [ ] **Step 6: Demostrar integración**

Después del merge, obtener una `GitHubObservation` nueva mediante el provider
seleccionado. Antes de cualquier prueba local de contención, ejecutar **otro**
`prepare_remote_ref_mutation()` → `build_fetch_policy_remote_request()` →
`execute_closed_git_effect()` → `validate_remote_ref_mutation()` con inventario,
invocation y grants `network_read` + `local_write` completamente nuevos; no
reutilizar guarda, grants, resultado ni `base_observation` del pre-merge. La
observación local post-merge debe demostrar la ref remota exacta o quedar
UNKNOWN; no ejecutar un fetch Bash suelto. Obtener el PR como una vista
coherente y demostrar:

```text
base = main
state = merged
mergeCommit identificado
mergeCommit contenido en origin/main
checks de main observados
```

Para PR C, guardar únicamente evidencia shadow y comprobación humana; no
promover `merged` ni `base_verified` con el provider candidato. Para PR D,
consumir observaciones nuevas y separadas para `merged` y `base_verified`; no
reutilizar la de `pr_ready`. Global `ready` no es prerrequisito de D: se exige
transport/provider live y elegibilidad exacta de `authority_mode=pilot`. Si eso
falta, el recibo queda `pending_external_evidence` aunque la comprobación humana
permita continuar según la autorización expresa; no se marca lifecycle cerrado.

El cierre manual de PR C conserva en el receipt solo selectores/digests públicos
de repositorio, PR, base y head. Al iniciar Task 14, el host vuelve a consultar
ese PR de forma coherente y crea un `ValidatedManualMergeObservation` opaco con
el `mergeCommit` de squash exacto. De ese wrapper en memoria procede
`control_plane_pr_c_merge_commit`; el receipt no concede autoridad y Task 14
exige igualdad exacta con la ref live.

Después del hito C, observar la ejecución post-push de `risk-sentinel`. `PASS`
es evidencia adicional de procedencia squash. `UNKNOWN` deja
`pending_external_evidence`; `FAIL` dispara alerta y diagnóstico, pero ninguno
de los dos reescribe automáticamente Git. PR D es el primer forward-test
autoritativo; solo tras cerrarlo sin drift podrá declararse disponible esa
capability para futuras tasks. Tras cada PR, crear la siguiente rama solo desde
este `origin/main` demostrado.

- [ ] **Step 7: Reconciliar —o advertir sobre— la copia local de la base**

`base_verified` demuestra el remoto y no depende de esta comodidad local.
Después, observar de nuevo el inventario completo y buscar el worktree
registrado cuya rama sea exactamente `policy.git.base_branch`. Nunca hacer
`switch` en el worktree feature ni escoger por cwd.

Para PR D, aplazar este Step hasta que
`finalize_authority_pilot()` de Task 14 haya publicado su destino y liberado el
lease piloto; no solapar una task raíz de mantenimiento con el writer
documental todavía activo. La prueba `base_verified` se conserva mientras se
revalida que la ref remota sigue en el mismo merge commit.

Si existe exactamente un worktree base, está limpio, no detached, su HEAD es
ancestro de `<policy-remote>/<base>` y el avance es fast-forward, mostrar el cambio y
pedir/consumir una autorización host-bound separada
`allowed_effect=local_write, operation=local_base_sync_ff`. Con ella, una task
schema-1 `operate/local_change` estrecha —effects local_read/local_write— y
lease de ese worktree ejecuta sin shell:

```text
git -C <registered-base-worktree> merge --ff-only <policy-remote>/<base>
```

Después exige HEAD local == ref remota, tree/index/untracked limpios y publica
`LocalBaseSyncReceipt(base_worktree, prior_head, final_head,
remote_base_head, result=SYNCED)`. No usa pull, rebase, reset, force ni borra
trabajo.

Si el worktree base está ausente, hay más de uno ambiguo, está dirty, detached,
adelantado/divergido o falta autorización, **no tocarlo**. Emitir de forma
visible:

```text
LOCAL_BASE_NOT_SYNCED
reason=<ABSENT|AMBIGUOUS|DIRTY|DETACHED|DIVERGED|AUTHORIZATION_REQUIRED>
base=<policy base>
path=<canonical path or unavailable>
local_head=<sha or unavailable>
remote_head=<verified sha>
safe_next_step=<create/sync a clean base worktree with explicit authorization>
```

El aviso contiene solo metadata Git pública y nunca degrada ni revoca la prueba
remota ya alcanzada. Tests cubren cada razón, FF exitoso, race tras
autorización, symlink/cross-repo y demuestran que un fallo local no se presenta
como “merge perdido”: el estado distingue `origin_base=VERIFIED` de
`local_base=NOT_SYNCED`.

- [ ] **Step 8: Retener o limpiar explícitamente la rama y su worktree**

La integración no autoriza cleanup. Después de `base_verified` —y, para D,
después de `finalize_authority_pilot()` y Step 7— reobservar PR, feature head,
ref base, inventario, tree/index/untracked y estado completo de leases/children.
El head de la feature debe ser exactamente el head observado del PR; en squash
no se exige que sea ancestro del merge commit. La rama debe ser no-base, el
worktree único/registrado/limpio y no puede quedar writer, verifier o recovery
activo.

Presentar mediante interacción nativa estas opciones:

```text
retain
remove_local
remove_local_and_remote
```

Sin evento nativo fresco, `frame_post_merge_cleanup_decision()` selecciona
conservación efectiva, no muta y emite:

```text
POST_MERGE_CLEANUP_PENDING
worktree=<canonical feature path>
local_branch=<exact feature branch>
remote_branch=<policy remote>/<exact feature branch or absent>
safe_next_step=<choose retain or an authorized cleanup option>
```

`retain` publica un `PostMergeCleanupReceipt(result=RETAINED)` sin grants ni
mutación. `remove_local` construye un `PostMergeCleanupPlan`, obtiene dos grants
`local_write` independientes y ejecuta en orden:

```text
remove_authorized_worktree
→ fresh inventory proves path absent and branch no longer checked out
→ delete_validated_local_feature
→ fresh inventory proves local branch absent
```

La segunda operación recibe
`DeleteValidatedLocalFeatureParameters(repository, common_dir, feature_branch,
feature_head, pull_request_number, merge_commit, base_ref)` y valida todos los
bindings contra observaciones post-merge frescas. Internamente ejecuta el CAS
cerrado `git update-ref -d refs/heads/<feature> <feature_head>`; no usa
`branch -D`, force, prune, `rm`, cwd implícito ni ancestry del squash.
`remove_local_and_remote` repite el cierre local y solo después solicita otro
grant host-bound `remote_write`; ejecuta
`delete_validated_remote_feature` sobre la ref exacta del PR head, nunca sobre
base/protected refs, y valida la ausencia remota con observación fresca. Si la
rama remota ya fue eliminada por GitHub, lo registra como `ALREADY_ABSENT`
después de observarlo; no inventa que la borró.

```text
remote_delete = build_validated_git_effect_request(
  operation_id=delete_validated_remote_feature,
  target_worktree=<fresh registered source or base worktree>,
  task_context=<post-merge cleanup context>,
  inventory=<fresh inventory after local cleanup>,
  governing_policy=<governing policy>,
  lease=None,
  authorization=<fresh remote_write one-shot>,
  expected_branch=<registered source/base branch>,
  expected_head=<fresh observed source/base HEAD>,
  parameters=DeleteValidatedRemoteFeatureParameters(
    repository=<policy repository identity>,
    remote=<policy remote>,
    feature_branch=<exact PR head ref>,
    feature_head=<exact PR head SHA>,
    pull_request_number=<exact merged PR>
  ),
  ...
)
remote_result = execute_closed_git_effect(remote_delete, <doctored Git runtime>)
validate_deleted_remote_feature(remote_result, <exact PR/ref bindings>)
```

Una decisión caducada, árbol dirty, lease/child vivo, ref/head drift,
autorización ausente o carrera conserva todos los recursos todavía existentes,
publica `PostMergeCleanupReceipt(result=PENDING, reason, safe_next_step)` y no
revierte un cleanup local ya demostrado. La siguiente rama puede crearse desde
la base remota verificada aunque el usuario elija retener, pero el estado de
retención queda visible y auditable.

## Task 14: Ejecutar PR D como primer piloto autoritativo

**Files:**
- Create: `docs/engineering/pilots/control-plane-authority-v2-1.md`

- [ ] **Step 1: Probar la base gobernante**

Solo después de fusionar PR C manualmente y verificar su squash, crear una task
bootstrap estrecha `TASK-CONTROL-PLANE-D-BOOTSTRAP` —intent `operate`, phase
`integrate`, outcome `local_change`, effects
`local_read+network_read+local_write`, sin lease de archivos de producto—.
`local_write` queda limitado al common Git dir que `fetch` modifica; la task no
puede editar el worktree. Con grants one-shot separados `network_read` +
`local_write`, el host nativo:

```text
manual_merge = validate_manual_merge_bootstrap(
  observe_manual_merge_bootstrap(<host.github-gh-read exacto>, PR-C selectors),
  expected_live_base_sha=<merge commit>,
  ...
)
fetch_guard = prepare_remote_ref_mutation(
  governing_runtime=<pre-C governing runtime>,
  governing_policy=<pre-C governing policy>,
  inventory=<fresh validated observation>,
  task_context=<D-bootstrap context>,
  expected_remote=<pre-C policy remote>,
  expected_base=<pre-C policy base>,
  ...
)
fetch_request = build_fetch_policy_remote_request(
  target_worktree=<registered canonical source>,
  task_context=<D-bootstrap context>,
  inventory=<fresh validated observation>,
  governing_policy=<pre-C governing policy>,
  guard=fetch_guard,
  network_authorization=<network_read one-shot>,
  local_mutation_authorization=<local_write one-shot for common Git dir>,
  expected_branch=<registered source branch>,
  expected_head=<observed source HEAD>,
  parameters=FetchPolicyRemoteParameters(
    remote=<pre-C policy remote>,
    base_branch=<pre-C policy base>
  ),
  ...
)
fetch_result = execute_closed_git_effect(fetch_request, <doctored host profile>)
validated_fetch = validate_remote_ref_mutation(
  fetch_result,
  guard=fetch_guard,
  fresh_inventory=<fresh post-fetch validated observation>,
  expected_remote=<pre-C policy remote>,
  expected_base=<pre-C policy base>,
  expected_prior_ref_digest=<guard preimage digest>,
  ...
)
governing_base = observe_remote_base_for_integration(
  validated_fetch,
  expected_remote=<policy remote>,
  expected_base=<policy base>,
  expected_governing_base=manual_merge.merge_commit,
  ...
)
```

Este bootstrap solo actualiza refs/objetos/reflogs del remote configurado y
observa; no edita archivos del worktree, no crea D y no usa shell. Preparar la
guarda es side-effect-free; armarla y mutar el common Git dir requiere ambos
grants: ausencia de cualquiera, replay, crash o carrera deja UNKNOWN y no
produce governing base. El
SHA/remote/base proceden del wrapper manual y policy gobernante, no del
candidate. Un `git fetch`, `rev-parse`, `merge-base` o `show` crudo no sustituye
estas observaciones.

`control_plane_source_repo` y
`control_plane_registered_canonical_repository_worktree` proceden del registro
host/worktree inventory validado al cerrar C, no del cwd, env arbitrario ni un
`git rev-parse` ejecutado sin target. Cualquier mismatch detiene D.

`control_plane_pr_c_merge_commit` procede del
`ValidatedManualMergeObservation` fresco que el host creó al reobservar los
selectores del PR C —PR/base/head/checks/mergeCommit exactos—, no de texto, env
arbitrario, receipt o `rev-parse origin/main`. La igualdad anterior exige que la
ref live siga exactamente en ese squash; si avanzó, D se reencuadra desde una
verificación nueva y no acepta “es ancestro” como suficiente. El escalar se usa
solo como selector diagnóstico: el host conserva el wrapper opaco y
`start_authority_pilot()` debe consumirlo; sin ese consumo, D no empieza aunque
el SHA coincida.

Crear un worktree/branch aislado
`codex/control-plane-authority-pilot-v2-1` exactamente desde ese
`origin/main`. Como operación host separada, con autorización local específica,
ligar `control_plane_pilot_worktree` al path canónico observado en
`git worktree list --porcelain` y crear también un attestor temporal detached
en el mismo commit. Ninguna de estas operaciones se expresa como Bash ni argv
aportado por el caller. El host crea tres contextos de mantenimiento distintos
—pilot setup, attestor setup y, más abajo, verifier setup— y obtiene para cada
uno una autorización `local_write` one-shot. Los paths temporales los entrega
un allocator host `0700` fuera de cualquier worktree; no proceden de env,
command substitution ni texto del modelo.

Para pilot y attestor, ejecutar por separado:

```text
request = build_validated_git_effect_request(
  operation_id=create_authorized_worktree,
  target_worktree=<registered canonical source>,
  task_context=<exact setup context>,
  inventory=<fresh validated pre-mutation inventory>,
  governing_policy=<pre-C policy>,
  lease=None,
  authorization=<distinct local_write one-shot>,
  expected_branch=<source branch>,
  expected_head=<source HEAD>,
  parameters=CreateAuthorizedWorktreeParameters(
    kind=real_branch | detached_attestor,
    new_path=<host-allocated canonical empty path>,
    new_branch=<pilot branch only or None>,
    start_commit=<validated squash C>
  ),
  ...
)
result = execute_closed_git_effect(request, <doctored Git runtime>)
post = observe_worktree_inventory(...)
validate_created_worktree(result, post, expected bindings...)
```

Cada request ejecuta internamente el argv fijo de
`create_authorized_worktree`, bajo el common-dir mutex prepare→Git→reobserve,
sin shell. El segundo no hereda contexto/grant del primero. La observación
posterior debe demostrar pilot con rama real/HEAD C y attestor detached,
limpio/HEAD C; solo entonces el launcher gobernante ejecuta `doctor` mediante
su command capability cerrada.

El attestor debe estar limpio, tener HEAD exacto y ejecutar el launcher/lock del
squash C. La creación y retirada del worktree son efectos host de setup con
target/base exactos; no amplían el lease escritor del documento. Si no puede
crearse o verificarse, el piloto no empieza. En este paso aún no se llama
`start_authority_pilot()`: primero debe existir el TaskEnvelope validado del
Step 2. `doctor` solo demuestra transport/provider live y elegibilidad
potencial; no crea task, lease ni `ready`.

Antes de iniciar D, crear un tercer worktree con rama real
`codex/control-plane-c-verify-v2-1` exactamente en squash C. El attestor
permanece detached/solo lectura y nunca recibe TaskLease. El verifier usa
`TASK-CONTROL-PLANE-C-VERIFY`, task/decision propios y TaskLease raíz `"."`.
Su framing ejecutable y cerrado es:

```text
verifier_setup = build_validated_git_effect_request(
  operation_id=create_authorized_worktree,
  target_worktree=<registered canonical source>,
  task_context=<D verifier-setup context>,
  inventory=<fresh validated inventory after attestor creation>,
  governing_policy=<policy at squash C>,
  lease=None,
  authorization=<new local_write one-shot>,
  parameters=CreateAuthorizedWorktreeParameters(
    kind=real_branch,
    new_path=<new host-allocated canonical empty path>,
    new_branch="codex/control-plane-c-verify-v2-1",
    start_commit=<validated squash C>
  ),
  ...
)
execute_closed_git_effect(verifier_setup, <doctored Git runtime>)
verifier_observation = validate_created_worktree(
  <effect result>, <fresh post-mutation inventory>, ...
)
verifier_target = attest_governing_base_verification_target(
  inventory=<fresh validated post-mutation inventory>,
  canonical_repository=<repo>,
  verifier_worktree=verifier_observation.canonical_path,
  expected_governing_base_commit=<squash C>,
  session_id=<verifier session>,
  invocation_id=<fresh invocation>,
  ...
)
verifier_runtime = attest_verification_governing_runtime(
  attestor_worktree=<attestor>,
  governing_base_commit=<squash C>,
  target_worktree=verifier_target.canonical_path,
  ...
)
verification_authority = bind_governing_base_bootstrap_authority(
  governing_runtime=verifier_runtime,
  verifier_target=verifier_target,
  expected_governing_base_commit=<squash C>,
  ...
)
create_verification_task_bootstrap(
  task_id="TASK-CONTROL-PLANE-C-VERIFY",
  authority=verification_authority
)
```

La factoría devuelve y valida el envelope v1 completo de Task 1 y liga profile
ID+digest; profile, HEAD/session y commands permanecen fuera del mapping y
ligados al contexto.
`OUTCOME_LIMITS["local_change"]` fija `review_ready` como terminal. Tras
adquirir el lease, el host crea un `VerificationExecutionContext` derivado del
profile ligado `governing_base_verification`; sus command capabilities cerradas permiten solo
los gates listados, redirigen temp/cache y deniegan Edit/Write/apply_patch/
stage/commit aunque el lease sea raíz. El child recorre
`framed → planned → ready → implementing → verifying → review_ready`, consume
mediante una sola invocación `run_verification_profile()` el set exacto de
commands y cero supplemental receipts, y solo entonces `task close` puede
finalizarlo/liberar el lease. No se usa outcome `commit`, porque este verifier
no produce commit. Como todavía no existe el lease documental D, no hay
solapamiento. El runtime observation usado aquí está ligado al verifier
separado, no al futuro target piloto.

El host ejecuta cada proceso con `cwd` fijado explícitamente al path canónico
`control_plane_verifier_worktree`, argv sin shell y paths absolutos:

```text
cwd=<verifier>, argv=[
  python3, <verifier>/tests/normal_budget.py, --repo, <verifier>
]
cwd=<verifier>, argv=[
  <verifier>/scripts/control-plane, policy-check,
  --policy, <verifier>/.codex/project-policy.toml
]
cwd=<verifier>, argv=[
  <verifier>/scripts/control-plane, registry-check,
  --registry, <verifier>/.codex/resource-registry.toml,
  --policy, <verifier>/.codex/project-policy.toml
]
cwd=<verifier>, argv=[<verifier>/scripts/control-plane, doctor]
git -C <verifier> status --short
```

Cada argv mostrado es la expansión normativa de un command ID del profile; el
runner lo ejecuta y exige `CompletedVerificationCommand=PASS` con
HEAD/index/tracked tree idénticos. El `git status` final también debe quedar
vacío.

Expected: gates PASS y verifier limpio. Consumir la observación de verificación,
cerrar el child mediante `finalizing_close`, liberar su lease raíz y demostrar
bajo common-dir flock que ya no existe antes de `start_authority_pilot()`.
Después crear requests/grants separados
`remove_authorized_worktree` y `delete_ephemeral_branch`: el primero exige
worktree limpio, registrado, HEAD C, lease ausente y no usa force; el segundo
exige que la rama efímera no tenga commits propios y ya no esté checked out.
Cada efecto se reobserva con inventory fresco y toma su common-dir mutex. No se
ejecutan `git worktree remove` ni `git branch -d` crudos. Si tests dejan
artefactos o no se puede
cerrar/liberar/retirar, D no empieza; no se limpia ni amplía el lease
silenciosamente. El receipt liga squash C, task, suite y digests, pero no
concede autoridad.

- [ ] **Step 2: Crear una unidad piloto deliberadamente estrecha**

Pre-framing:

```text
intent=integrate
phase=integrate
requested_outcome=integration
scope_paths=["docs/engineering/pilots/control-plane-authority-v2-1.md"]
effects=local_read,local_write,commit,remote_write,pull_request,network_read,integration
```

`tier` no forma parte del TaskEnvelope: se acepta el tier que calcule el
resolver a partir de señales/efectos reales y se aplican íntegros su effort,
gates y budget. El diff estrecho no reduce riesgo; nunca se reencuadra la
petición para conseguir T1. Solo se bloquea un tier/route inválido o
incompatible con policy —por ejemplo T0 ante commit/red/PR/integration—, no un
T2/T3 honesto.

El orden es normativo y atómico antes de la primera edición:

```text
task-framer
→ TaskEnvelope schema 1 validado en memoria
→ ValidatedInventory fresca
→ GoverningPolicy + GoverningResourceRegistry desde squash C
→ build_validated_pilot_inputs(...) ejecuta resolver, selecciona
  remote-integration-proof y liga provider resource ID+digest
→ ValidatedManualMergeObservation fresca + GoverningRuntimeObservation
→ TrustedPilotAuthorization host-bound one-shot
→ TaskStore.start_authority_pilot(inputs, runtime, manual_merge, ...)
→ PilotTaskContext + TaskLease
→ primera edición
```

El host nativo carga la API solo desde el attestor, reobserva inventory y PR C,
carga policy+registry del objeto squash C, construye
`ValidatedPilotInputs` y llama `TaskStore.start_authority_pilot()` consumiendo
en el mismo proceso inputs, runtime attestation, manual merge observation y
autorización. El caller no pasa un route confiable ni un SHA base escalar. No
existe subcomando/JSON para seleccionar pilot. El contexto devuelto fija
`control_plane_active_task_id`, `control_plane_trusted_session_id` y
`control_plane_target_worktree`, además del provider resource ID+digest elegido.
Si `git.remote-proof` queda unresolved, el provider no está ready/authorized o
falta `HostAdapterCapability`, D queda pendiente sin task/lease y no edita.

Guardar como estado compacto:

```text
governing_base_commit = origin/main verificado al crear la task
governing_policy_digest = digest del blob policy en ese commit
candidate_policy_digest = digest de la policy del worktree
authority_mode = pilot
provider_capability = pending_pilot
provider_resource_id = host.github-gh-read
provider_resource_digest = exact governing registry entry digest selected by RouteDecision
```

Un mismatch, base móvil no revalidada o policy no cargable bloquea antes de
editar o consultar red. `authority_mode=pilot` no declara capability global:
es una excepción cerrada que solo acepta el task ID de D, la governing base
exacta y la allowlist documental. Ninguna otra task puede solicitarla. Todo
`apply_patch` o editor recibe la ruta absoluta bajo
`control_plane_target_worktree`; el cwd del hilo nunca decide el destino.

El documento es el charter previo: explica objetivo, governing/candidate
digests y gates esperados, sin afirmar el resultado futuro y sin prompts,
tokens, respuestas externas ni credenciales. El resultado real queda en la
capability host-bound efímera posterior, no se intenta reescribir este PR después de
fusionarlo.
No puede modificar:

```text
.codex/project-policy.toml
.codex/control-plane.lock
.codex/resource-registry.toml
.codex/hooks/**
.codex/git-hooks/**
.github/workflows/**
control_plane/**
scripts/control-plane
tests/**
```

El provider valida esta exclusión desde el diff remoto antes de cualquier
promoción. Un path fuera de la allowlist convierte el piloto en
`policy_change_pending` y exige una task nueva; no se degrada a shadow
silenciosamente.

- [ ] **Step 3: Verificar localmente sin autoatestación**

No ejecutar suite, build, policy/registry/doctor ni unittest dentro del
worktree D: Task 7 los clasifica `may_write_unknown_paths` y el lease D cubre
solo el documento. La suite completa ya se ejecutó en el child raíz del
attestor y su lease quedó cerrado antes de D. En D ejecutar únicamente
preflight y gates Git/documentales demostrablemente no escritores:

Primero refrescar preflight dirty con task/session/lease exactos. El host
encuadra su resultado como `ValidatedPilotPreflightObservation`, llama
`TaskStore.begin_pilot_verification()` sobre el contexto implementing vigente
y conserva el nuevo `PilotTaskContext` en verifying; un mapping o contexto
anterior no sirve. Solo entonces ejecutar los cuatro safe-reads:

```text
cwd=<pilot>, argv=[
  <attestor>/scripts/control-plane, safe-read,
  --repo, <pilot>, --,
  git, status, --short
]
cwd=<pilot>, argv=[
  <attestor>/scripts/control-plane, safe-read,
  --repo, <pilot>, --,
  git, diff, --exit-code, origin/main...HEAD, --,
  .codex, .github, control_plane, scripts, tests
]
```

Expected: status muestra únicamente el documento piloto sin seguimiento y el
diff del control plane es vacío. El host lanza además, con el mismo cwd y argv
sin shell, dos búsquedas no reveladoras sobre el path absoluto exacto del
documento:

```text
<attestor>/scripts/control-plane safe-read --repo <pilot> -- \
  rg --no-config --quiet \
  -e 'T[B]D|T[O]DO|<SCHEME[_]REAL>|<COMANDO[_]' -- <pilot-document>

<attestor>/scripts/control-plane safe-read --repo <pilot> -- \
  secret-scan-governing -- <pilot-document>
```

Para el primer `rg`, exigir `CompletedSafeRead(status=completed, exit_code=1,
stdout_bytes=0, stderr_bytes=0, truncated=false, timed_out=false)`: 0 significa
placeholder encontrado y bloquea; cualquier otro estado/exit es UNKNOWN y
bloquea. Para `secret-scan-governing`, exigir el mismo resultado negativo y,
además, `pattern_set_digest` exactamente igual al scanner gobernante ligado al
lock; match bloquea y drift/ausencia es UNKNOWN. Nunca sustituirlo por un regex
`rg` más estrecho.
Los cuatro resultados deben ligar el `repository_binding_digest` del root,
git-dir y common-dir exactos del piloto; el cwd por sí solo no selecciona el
target porque el launcher gobernante pertenece al attestor. `--quiet` impide
imprimir la línea potencialmente sensible. No se afirma un
gate de tamaño local ni se ejecuta Python/herramienta que pueda crear cache en
D; el contrato/CI posterior cubre el documento ya committed. El host liga los
cuatro resultados y la allowlist a
`ValidatedPilotLocalVerificationObservation`, llama
`TaskStore.complete_pilot_verification()` sobre el contexto verifying y exige
un contexto nuevo `review_ready`. Stage y commit son dos efectos distintos,
cada uno con preflight fresco, request cerrado y autorización one-shot propia;
el lease o una autorización `commit` no autorizan `git add`:

```text
stage_request = build_validated_git_effect_request(
  operation_id=stage_allowlisted_paths,
  target_worktree=<pilot canonical path>,
  task_context=<current review_ready PilotTaskContext>,
  inventory=<fresh validated inventory>,
  governing_policy=<squash-C policy>,
  lease=<exact pilot lease>,
  authorization=<local_write one-shot for stage>,
  expected_branch=<pilot branch>,
  expected_head=<squash C>,
  parameters=StageAllowlistedPathsParameters(paths=[
    "docs/engineering/pilots/control-plane-authority-v2-1.md"
  ]),
  ...
)
stage_result = execute_closed_git_effect(stage_request, <doctored Git runtime>)
```

El host reobserva índice/árbol, exige que solo ese path esté staged y ejecuta
mediante `safe-read` gobernante `git diff --cached --check` y
`git diff --cached --name-only`. Solo si ambos `CompletedSafeRead` pasan,
refresca el preflight y crea un segundo request:

```text
commit_request = build_validated_git_effect_request(
  operation_id=commit_staged_change,
  target_worktree=<pilot canonical path>,
  task_context=<same current review_ready context>,
  inventory=<new fresh validated inventory>,
  governing_policy=<squash-C policy>,
  lease=<exact pilot lease>,
  authorization=<distinct commit one-shot>,
  expected_branch=<pilot branch>,
  expected_head=<squash C>,
  parameters=CommitStagedChangeParameters(
    paths=["docs/engineering/pilots/control-plane-authority-v2-1.md"],
    message="Add control plane authority pilot charter"
  ),
  ...
)
commit_result = execute_closed_git_effect(commit_request, <doctored Git runtime>)
local_git = observe_committed_change(
  commit_result,
  expected_prior_head=<squash C>,
  expected_paths=<exact allowlist>,
  expected_tree_clean=true,
  ...
)
```

Las plantillas argv viven dentro del host bridge y usan `shell=False`; el plan
no ejecuta `git add` o `git commit` directamente.

Expected: ambos `CompletedSafeRead` son `completed/exit_code=0`, no truncados,
ligados al `repository_binding_digest` del piloto; el segundo contiene
únicamente el path allowlisted. El índice contiene exactamente el documento
piloto y `HEAD` avanza.
El host consume inmediatamente la `LocalGitObservation` fresca anterior y
llama `TaskStore.advance_pilot_local_commit()` con el contexto review_ready y
la generation vigente. Debe publicar `committed` y obtener un
`PilotTaskContext` nuevo cuyo `current_head` sea el commit D y cuyos
task/session/base/policy/runtime/provider/scope permanezcan idénticos; todos
los contextos anteriores —incluido el inicial en squash C— quedan stale. Si la
observación no demuestra el diff allowlisted o el rebind falla, no hacer push.

Repetir entonces:

```bash
"$control_plane_attestor_worktree/scripts/control-plane" safe-read \
  --repo "$control_plane_target_worktree" -- \
  git diff --name-only origin/main...HEAD
"$control_plane_attestor_worktree/scripts/control-plane" safe-read \
  --repo "$control_plane_target_worktree" -- \
  git diff --exit-code origin/main...HEAD -- \
  .codex .github control_plane scripts tests
```

Expected: ambos `CompletedSafeRead` son `completed/exit_code=0`, no truncados y
ligados al piloto; el primero enumera solo el documento piloto y el segundo
demuestra cero drift del control plane. Ahora el archivo ya está trackeado y la
comprobación de allowlist es completa. Los comandos raw equivalentes no son
evidencia autoritativa bajo soft-enforce. Push y PR requieren grants host-bound
nuevos y separados. Tras push, el workflow `verify` ejecuta la suite sobre el
commit D en un runner aislado; `pr_ready` espera esos checks. Así el contenido
nuevo sí se prueba sin otorgar lease raíz al piloto.

- [ ] **Step 4: Recorrer el lifecycle remoto con política de base**

Invocar ahora el procedimiento de Task 13 para PR D. En
`authority_mode=pilot`, el provider:

1. se ejecuta exclusivamente mediante
   `"$control_plane_attestor_worktree/scripts/control-plane"` con
   `--target-worktree` igual al path canónico de D;
2. atestigua antes del import launcher/runtime/provider/lock desde el squash C;
3. relee policy desde `governing_base_commit`;
4. verifica digest, repo, base, remote, strategy y base SHA;
5. exige que RouteDecision, registry, transporte, observaciones y receipts
   coincidan con el recurso gobernante exacto `host.github-gh-read`; un MCP o
   connector alternativo deja D pending antes de red;
6. observa PR exacto y required checks gobernantes;
7. consume observaciones separadas para `pr_draft` y `pr_ready`;
8. no ejecuta merge;
9. después de autorización/merge humano, consume observaciones nuevas para
   `merged` y `base_verified`;
10. consulta mediante el provider el run post-push exacto y crea/consume una
   `GitHubWorkflowProvenanceObservation`; no carga output JSON de Actions.

Este modo puede avanzar únicamente el TaskStore del piloto; no escribe una
capability `ready` antes de `base_verified`. Un task ID, diff, base, policy,
lock o provider distinto devuelve `PILOT_BINDING_MISMATCH`. Tests prueban que
una task ordinaria no puede seleccionar pilot y que D no habilita otras tasks
durante `pr_draft`, `pr_ready` o `merged`.

La policy candidata no participa en ninguna decisión. Un cambio de
`total_count`, provider no ready, required check ausente, base avanzada o
evidencia incompleta deja UNKNOWN/pending y no puede maquillarse con
comprobación manual.

- [ ] **Step 5: Promover capability solo tras el forward-test**

Solo después de `base_verified` y `risk-provenance == PASS` se publica,
bajo `fcntl.flock`, un hint compacto no autoritativo en:

```text
<git-common-dir>/codex-control-plane/capabilities/github-lifecycle-v2-1.json
```

El hint liga schema, product version, runtime/lock digest, governing policy
digest, provider kind/version, PR, merge commit y base commit observados.
No contiene outputs de GitHub, token ni task ledger. Es un índice project-wide
para reconstruir la prueba, no una capability ni un ledger compartido, y se escribe
temp/fsync/replace/fsync-dir bajo `capabilities.lock`; eliminar el worktree D no
lo elimina.

Cada uso futuro trata todos sus campos como selectores públicos, relee runtime,
lock, governing policy y provider live mediante un attestor limpio en la
governing base y vuelve a observar en GitHub el PR piloto exacto, la lista
completa de files allowlisted, merge/base y el run/attempt/job PASS exactos.
Solo esa reconstrucción host-bound emite una capability opaca, invocation-bound
y one-shot. Upgrade, lock drift, policy change, provider distinto, base que no
contiene el merge, PR/diff alterado, run/job no demostrable o GitHub degradado
devuelven stale/pending y exigen otro piloto o reintento; nunca se acepta solo
porque el archivo exista o sus digests públicos coincidan. Tests modifican
candidate provider+lock coordinadamente y demuestran que el attestor bloquea
antes de red. También crean un segundo worktree, podan el piloto, prueban que el
hint sigue visible pero necesita reobservación completa, cubren cada
invalidación y fabrican un mapping con todos los digests públicos sin obtener
readiness.

En éxito, pending o carrera post-merge, el host del attestor llama
`TaskStore.finalize_authority_pilot()` con el último `PilotTaskContext`
vigente —generation/current_head ya rotados por
`advance_pilot_local_commit()`— y la capability live o razón estable. El
contexto inicial en squash C y cualquier generation anterior deben fallar
`PILOT_CONTEXT_STALE` sin liberar el lease. Desde `base_verified` cierra sin mentir sobre
readiness; desde `merged` con base avanzada conserva
`blocked/resume_state=merged`, no promueve ni declara integration completada.
Ambos liberan el TaskLease owner-bound. Si se aborta antes de merge, la misma
API conserva estado/resume/reason auditables, libera el writer y exige un
piloto nuevo para cualquier edición. Antes del handoff se demuestra
bajo el common-dir flock que no queda lease del piloto; no se borra un JSON de
lease manualmente.

Inmediatamente después de que `finalize_authority_pilot()` publique el destino
y se demuestre la liberación —nunca antes—, revalidar que la ref remota sigue en
el mismo merge commit y ejecutar Task 13 Steps 7–8. La primera task de
mantenimiento sincroniza por fast-forward el worktree local de base con
autorización propia o emite `LOCAL_BASE_NOT_SYNCED`; no reutiliza el contexto ni
el lease D. Después, otra decisión nativa separada retiene o limpia el worktree
y rama exactos de D. Sin decisión queda `POST_MERGE_CLEANUP_PENDING`; nunca se
borra el piloto por asumir que “merged” autoriza cleanup. Si se elige
`remove_local_and_remote`, cada worktree/rama local/ref remota usa su operación,
grant y reobservación propios, y la ref remota se liga al head del PR D aunque
el merge haya sido squash.

Tras finalizar el intento —y, en success, publicar el hint y verificar la
capability live— comprobar que el attestor sigue detached, limpio y en el
commit gobernante. Una observación `safe-read` gobernante y el inventory fresco
deben probarlo. Solo entonces construir
`ValidatedGitEffectRequest(operation_id=remove_authorized_worktree)` con un
contexto de teardown separado, lease ausente y autorización `local_write`
one-shot; la plantilla interna elimina **sin force** exactamente el path
registrado y después reobserva el common dir. El host puede eliminar el
directorio padre que él mismo creó únicamente si el inventory ya no lo
registra, está vacío, sigue dentro del temp root host-owned y no es symlink.
Eso es cleanup del allocator, no un `rmdir` aportado por el modelo. Un fallo
conserva el path para diagnóstico y muestra teardown pending; nunca hace prune,
force ni borrado recursivo.

Si PR D no puede completar todo el recorrido, v2.1 sigue siendo funcional en
audit/shadow y guards mecánicos, pero el lifecycle remoto permanece
`pending_github_host_adapter`. Eso no bloquea el handoff honesto.

## Task 15: Handoff read-only de adopción, sin aplicarla

**Files:**
- Read: `docs/engineering/07-adoption.md` ya actualizado y comprometido en
  Task 10.

- [ ] **Step 1: Preparar orden de futuros pilotos**

```text
1. fixture hermético
2. este repositorio
3. BUSTAFIT en una tarea específica
4. textosv2 en su worktree canónico
5. otros proyectos
```

- [ ] **Step 2: Definir gate por proyecto**

Cada adopción posterior debe:

```text
detectar perfil
verificar worktree y base
adopt plan
revisar diff
apply autorizado
verify
periodo audit
medir falsos positivos
promover o rollback
```

- [ ] **Step 3: No ejecutar adopciones desde este plan**

El cierre v2.1 entrega el handoff usando la documentación ya versionada en Task
10 y el resultado del piloto D. Task 15 no modifica archivos después del
commit/PR D. No modifica otros repositorios ni la configuración global de
Codex.

## Verificación documental del plan antes de ejecutarlo

Run:

```bash
rg -n 'T[B]D|T[O]DO|implement l[a]ter|fill i[n]|<SCHEME[_]REAL>|<COMANDO[_]' \
  docs/superpowers/plans/2026-07-29-clarification-gate-risk-sentinel-v2-1.md \
  docs/superpowers/specs/2026-07-29-clarification-gate-risk-sentinel-design.md
```

Expected: ninguna coincidencia.

Run:

```bash
git diff --check
```

Expected: salida vacía.

## Criterio de parada

Detener la ejecución y conservar evidencia si:

- no puede confirmarse `xhigh`;
- cambia sustancialmente el objetivo;
- la base remota deja de estar contenida;
- aparece un hooksPath no gestionado;
- un test baseline falla;
- GitHub no permite demostrar procedencia;
- se requiere un secreto;
- una operación necesita force push, destrucción, deploy o release;
- dos writers necesitarían modificar el mismo contrato;
- el plan contradice el código observado.

El agente debe formular una sola pregunta material, con recomendación, cuando
la decisión no pueda derivarse del repositorio.
