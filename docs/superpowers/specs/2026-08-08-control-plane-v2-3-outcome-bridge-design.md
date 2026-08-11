# Control Plane v2.3 — revisión independiente, autoridad de resultado y Git Outcome Bridge

- **Estado:** diseño reconciliado; implementación local verificada
- **Fecha:** 8 de agosto de 2026
- **Rama de planificación:** `codex/control-plane-v2-3`
- **HEAD observado:** `7bd9d2a96f5c3cdd22807a5d7f810d3a6fc1d9d4`
- **Base verificable:** `v2.1.1@934a42c295496583baf754738e34a3bbe6855a0c`
- **Método de entrega:** TDD, un único writer, revisión independiente y
  autorización nativa por efecto

## 1. Decisión

La v2.3 amplía el vertical slice local para que una única tarea pueda alcanzar
su outcome final `local_change|commit|pull_request|integration`, conservando al
host como única frontera de autoridad. El producto no adquiere un scheduler,
un segundo agente, una API GitHub propia ni comandos CLI que muten el remoto.

La decisión tiene cinco piezas inseparables:

1. `TaskEnvelope.requested_outcome` se fija al preparar la tarea, se copia a
   `RunPlanV1` y al lifecycle, y nunca se eleva ni muta durante la ejecución.
   Describe el resultado pedido, no concede autoridad.
2. Un reviewer independiente recibe un paquete compacto y determinista del
   cambio estable; su resultado se vuelve evidencia host-bound, no autoridad.
3. `OutcomeAuthorizationContext` vive únicamente en memoria del host. Vincula
   un mandato nativo a una tarea, sesión, repositorio, base, rama, `HEAD`,
   alcance y digest del cambio, y deriva claims nativos one-shot para cada
   efecto permitido. No existe como objeto Python. El usuario nunca construye,
   copia ni reemite objetos internos de autorización.
4. El Git Outcome Bridge conserva los estados existentes:
   `review_ready → committed → pushed → pr_draft → pr_ready`. El
   staging es una operación efímera verificada, nunca un estado durable, y no
   se introduce `review_pending`.
5. Un merge squash solo se intenta con un mandato nativo explícito «hasta
   squash merge» que siga coincidiendo exactamente. No hay auto-merge, force-push,
   deploy ni release.

```text
TaskEnvelope(outcome inmutable) + RunPlan + diff estable
        │
        ├── gates locales requeridos ── FAIL/UNKNOWN → BLOCKED o reparación
        │
        └── T2/T3 en verifying: ReviewPacketV1 → reviewer independiente
                         │
                         └── observación host-only → receipt durable
                                                        │
review_ready + OutcomeAuthorizationContext (host, one-shot por efecto)
        │
        └── delivery lease → local_write(stage) → commit → committed
                                                             │
                                                             ├── remote_write → push normal → observación remota
                                                             ├── pull_request → PR draft/checks/comentarios → PR LISTA
                                                             └── integration → squash → origin/<base> observado
```

`PR LISTA` es el resultado predeterminado. El gate operativo predeterminado de
promoción es un sandbox privado hasta `PR LISTA`, con una petición nativa actual
que cubra exactamente ese alcance. No significa autorización para integrar, y
ningún objeto serializado puede convertirlo en ella.

## 2. Hechos de partida

El diseño se limita a hechos observables en este worktree:

- `control_plane/host_bridge.py` ya contiene contratos host-bound para
  `TrustedAuthorization`, contexto remoto, staging, commit, push y creación de
  PR, y falla cerrado cuando el adaptador de producción no existe;
- el adaptador que permite tests vive en
  `tests/host_adapter_test_support.py`; no es una capacidad de runtime;
- `ReviewResultV1` es serializable y no autorizante, mientras que los
  publishers tipados de `IndependentReviewReceipt` fallan cerrado;
- `TaskStore`, lifecycle y el bridge ya conservan el outcome inmutable, admiten
  la cadena local hasta `pr_ready` y validan sus bindings sin atribuir autoridad
  a Python;
- `prepare_run()` acepta el outcome final elegido al inicio sin reescribirlo y
  mantiene los efectos posteriores diferidos hasta recibir observaciones
  host-native;
- la CLI `run` ya se limita a `prepare`, `verify`, `status` y `block`. Esa
  superficie local se conserva;
- `control-plane-run` y los contratos locales cubren el vertical slice y la
  revisión para T2/T3; la implementación local está verificada, mientras que
  toda mutación y evidencia GitHub continúa separada;
- no existe una API productiva que entregue un objeto nativo opaco desde Codex
  a un subprocess Python. El único adaptador actual es test-only y no se
  promociona ni se disfraza como bridge de producción;
- suite, gates, índice, inventario del worktree y revisiones son hechos
  dinámicos. El spec y el plan no fijan sus valores: la única verdad dinámica
  del candidato es un `LocalCandidateReceiptV1` válido, creado después de
  observarlos y ligado a sus digests. El candidato se identifica por ese
  receipt y no por una versión de release.

Estas observaciones no prueban credenciales, reglas de rama, checks alojados ni
capacidad de GitHub. Toda evidencia remota ausente sigue siendo `UNKNOWN`.

## 3. Alcance y exclusiones

### Incluido

- Paquete compacto de revisión, recibo de revisión independiente y sus
  invalidaciones.
- Autoridad de outcome host-bound, no serializable, con caducidad y consumo
  único por efecto.
- Corrección del handoff lifecycle/lease/commit/contexto remoto.
- Push normal, PR draft, observación de checks y comentarios, y promoción a
  `pr_ready`.
- Merge squash explícito y observación posterior del SHA en `origin/<base>`.
- Auditoría de entrega compacta y salida de éxito tersa.
- Recibos de gates requeridos, reuso exacto de evidencia local y bloqueo ante
  `UNKNOWN`.
- Pruebas unitarias, repositorios temporales y un sandbox privado autorizado
  para las mutaciones reales.

### Excluido

- Serializar, transferir o reproducir autoridad entre tareas, sesiones,
  procesos, worktrees o artefactos.
- Nuevos comandos CLI de commit, push, PR o merge.
- Force-push, auto-merge, merge rebase, deploy, release, publicación, cambios
  de CI, autenticación automática o lectura de secretos.
- Un agente secundario, scheduler, RAG, vector store, dashboard, telemetría
  cloud, dependencias runtime o una segunda policy de proyecto.
- El gobernador automático nativo se implementa en v2.4, no en esta entrega.
- `ProjectFactsV1` sigue condicionado al umbral definido para v2.5.
- Promesas de precio/token y limpieza automática no demostrada: se difieren
  hasta que haya métricas locales y una autorización separada.

### Límite de versión y release

`product_version` y la release skill siguen en `2.1.1` hasta una promoción de
release separada. Esta reconciliación no cambia CI, release ni la versión
publicada: identifica el candidato local por digest y conserva la promoción
oficial como una transición posterior autorizada.

## 4. Contratos cerrados

Todos los contratos serializables usan schema cerrado, versión explícita,
límites de bytes y `authorizes: false`. Ninguno incluye prompt completo,
transcript, salida de comandos, credenciales, headers de `gh`, token, nonce
nativo, identificador de autoridad nativa ni objeto de autoridad. El
`RunPlanV1.session_id` preexistente es únicamente un correlator local inerte;
no identifica una sesión host ni puede participar en una decisión de
autoridad.

### 4.1 `ReviewPacketV1`

Es el único input durable que se entrega al reviewer. Contiene:

- `task_id`, `task_digest`, `run_plan_digest` y `attempt`;
- repositorio canónico, base observada, rama y `head_sha`;
- `stable_diff_digest`, algoritmo, tamaño y resumen de paths permitidos;
- `criteria_digest`, tipo `independent|security` y perfiles de gates;
- recibos de gates: nombre, argv digest, `HEAD`, diff digest, estado y digest
  de salida limitado;
- resumen de pruebas: argv digest, estado y digest de salida limitado.

El paquete no contiene el diff completo por duplicado. El reviewer obtiene el
diff estable por una capability de lectura host-bound que comprueba los mismos
bindings antes de leerlo. Si el `HEAD`, base, paths o digest derivan, el paquete
queda obsoleto y se bloquea la promoción.

El límite agregado inicial es 4 KiB; cada path y receipt tiene un límite menor.
El constructor rechaza listas, texto o metadatos que excedan su presupuesto en
vez de truncar información de seguridad silenciosamente.

### 4.2 `IndependentReviewReceipt`

El reviewer no publica por sí mismo una evidencia fiable. La frontera se divide
en dos objetos distintos:

- una observación host-only, opaca y one-shot, ligada a sesión, invocación,
  identidad del reviewer y una frontera de frescura que nunca se serializa;
- un `IndependentReviewReceipt` durable, cerrado y no autorizante, que el host
  publica únicamente después de consumir una observación válida.

La tarea raíz valida identidad, cursor y resultado del reviewer con las
herramientas nativas de tareas. Python valida después el schema y los bindings
del receipt, pero no afirma haber observado la tarea nativa ni instala un
adaptador test-only en producción.

El recibo durable liga:

- `reviewer_identity_digest`, clase de revisión y criterios exactos;
- `task_digest`, `run_plan_digest`, base, `head_sha`, `stable_diff_digest` y
  `criteria_digest`;
- digest de findings, resultado `PASS|FAIL|UNKNOWN` y momento observado;
- `observation_digest` de la observación consumida y `authorizes: false`, sin sesión,
  invocación, nonce, TTL ni objeto host.

`RunStore.persist_review_receipt` consume la prueba process-local antes de la
escritura durable. Un fallo posterior no permite reutilizarla; replay, deriva,
expiración o ausencia del adapter host bloquean. Cuando T3 exige review
independiente y de seguridad, sus `observation_digest` deben ser distintos.

T2 requiere una revisión independiente `PASS`; T3 requiere además una revisión
de seguridad `PASS` cuando el perfil de riesgo la solicita. Un finding
`Critical` o `Important`, una revisión `FAIL`, una observación vencida o
reproducida, un receipt con bindings distintos o cualquier `UNKNOWN` mantiene
la tarea en `verifying`. El feedback inicia una revisión del mismo task solo
con un nuevo `HEAD`; invalida gates y recibos ligados al anterior. La transición
a `review_ready` sucede una sola vez, después de validar todos los receipts.

Cuando T3 requiere `gate.rollback-plan`, la tarea raíz debe framear antes de
`verify` una observación host-bound fresca para el intento exacto. De ella se
deriva un `RollbackPlanV1` durable bajo el Git dir del worktree, ligado a
`task_digest`, `RunPlan`, revisión activa, intento, repositorio, rama, `HEAD` y
digest de alcance. El plan contiene triggers estructurados, pasos ordenados con
target y condición de éxito, comprobaciones posteriores y límites irreversibles
con mitigación; siempre lleva `authorizes: false`. La mera cadena
`gate.rollback-plan` en `required_gates`, texto libre o un scalar autocertificado
no son evidencia. Falta, deriva, replay, expiración o `UNKNOWN` produce un gate
receipt `UNKNOWN`, bloquea el intento y no permite preparar el paquete ni
publicar `review_ready`. Un receipt `PASS` se vuelve a ligar al plan durable,
intento y `HEAD` tanto al preparar review como al promocionar.

### 4.3 `OutcomeAuthorizationContext`

`OutcomeAuthorizationContext` es estado efímero de la tarea raíz de Codex, no
una clase ni entrada de la CLI Python. No tiene `to_dict`, `from_dict`, JSON,
pickle, persistencia bajo `.git`, log o recibo. `control-plane-run` lo deriva
de la petición nativa actual y lo liga a:

- tarea, sesión e invocación actuales;
- repositorio, worktree, remoto `origin`, base y rama de feature;
- `head_sha`, `subject_digest`, `stable_diff_digest` y `scope_paths` exactos;
- outcome final permitido `commit`, `pull_request` o `integration`;
- efectos permitidos, nonce, expiración y uso por efecto;
- para integración, PR, checks observados y mandato nativo explícito «hasta
  squash merge».

El contexto contiene un grant-set raíz consumido una vez y claims one-shot para
los únicos efectos `local_write`, `commit`, `remote_write`, `pull_request` e
`integration`. En la frontera local real, `frame_effect_authorization` recibe
`NativeUserInteractionEvent` y `HostAdapterCapability`; esos objetos opacos no
se serializan ni se reconstruyen desde un receipt. Python bridge recibe y
consume la autorización nativa solo para Git local allowlisted (`git add`;
commit con `git commit-tree` y `git update-ref` CAS). El kernel puede observar
con `git ls-remote` read-only;
push/PR/squash merge son host-native y Python solo valida sus planes y receipts
no autorizantes.

El grant-set permite únicamente sucesores causales esperados:

1. `review_head` y el digest del árbol/diff revisado permiten stage.
2. El commit debe tener `review_head` como parent y el árbol esperado; su SHA
   observado se convierte por CAS en `committed_head`.
3. Push y PR deben observar exactamente `committed_head`.
4. Integración debe observar la PR/checks ligados a ese SHA y produce un
   `merge_sha` que después debe estar contenido en `origin/<base>`.

Un cambio fuera de esa cadena invalida el contexto. Los fallos del kernel
conservan la convención existente `ValueError("E_*: ...")`; el host expresa
errores al usuario en términos de producto, no como objetos de Python.

La petición nativa actual es la única fuente humana. La tarea raíz la valida
una vez y deriva internamente los claims ordinarios cubiertos por ella;
no solicita microautorizaciones mientras tarea, sesión, repositorio, base,
rama, lineage, scope y digest permanezcan estables. `NativeUserInteractionEvent`,
`HostAdapterCapability`, `TrustedAuthorization`, nonces y mensajes de mint no
forman parte de la UX y nunca se piden como texto al usuario.

Si el efecto no estaba en el mandato, se alcanza el último outcome permitido
—por defecto `pr_ready`— sin insistir. Solo deriva, ampliación de alcance o una
acción nueva requieren otra interacción humana. Un subprocess sin contexto
nativo no intenta reconstruirlo. La instalación soportada debe demostrar el
recorrido desde la tarea raíz con las herramientas reales de Codex; fakes de
Python no satisfacen promoción. Un fallo de host produce un diagnóstico estable
y una reparación del host/instalación, nunca instrucciones para fabricar
manualmente un grant.

### 4.4 Preparación con efectos diferidos

Routing separa outcome solicitado de efecto ejecutable ahora. `run prepare` es
una operación de planificación/estado bajo el Git dir: acepta outcomes
`local_change|commit|pull_request|integration` con efectos posteriores aún en
`approval_boundaries`, siempre que no haya ambigüedad material y los gates,
scope y digests sean válidos. No convierte esos efectos en autorizados.

`RunPlanV1` conserva los efectos diferidos y el outcome inmutable. Rechaza
`answer` y `release` para este workflow. `RunPlanV1.session_id` sigue siendo un
correlator local no autorizante; la prohibición de serialización se refiere a
identificadores nativos de autoridad, no a ese campo existente.

### 4.5 `OutcomeEffectPlanV1` y `RemoteOutcomeReceiptV1`

El kernel produce un `OutcomeEffectPlanV1` cerrado, limitado y siempre
`authorizes: false` para describir el siguiente efecto canónico, sus bindings,
precondiciones y argv digest. No lo ejecuta ni lo convierte en autoridad. La
tarea raíz comprueba su contexto nativo y ejecuta Git/`gh` mediante las
herramientas reales del host.

La misma tarea raíz observa después el resultado con herramientas nativas y
publica un `RemoteOutcomeReceiptV1` durable, cerrado y no autorizante. El
receipt liga tarea, repositorio, remoto, base, rama/PR, efecto, HEAD/SHA,
scope, digest, resultado `PASS|FAIL|UNKNOWN` y momento observado; nunca incluye
sesión host, invocación, nonce, TTL, grant o credencial. Python valida su schema
y bindings, pero no afirma su procedencia nativa. Esa procedencia solo se
demuestra en el sandbox nativo. Los objetos
`ValidatedPullRequestMutationObservation` y los adapters de tests existentes
no son la vía de producción.

La integración es más estricta: tanto el receipt `READY` previo al merge como
el `PASS` posterior requieren una `ValidatedGitHubObservation` fresca,
host-bound y one-shot, ligada al plan, PR, checks, estado y SHA exactos. Un
receipt escalar nunca arma el ticket ni publica `merged`.

### 4.6 `StableReviewDiffArtifactV1`

`run verify` materializa una copia acotada del diff estable bajo el Git dir del
worktree, modo `0600`, con manifest que liga repo, base, HEAD, paths, digest y
tamaño. `ReviewPacketV1` referencia su ID/digest y el reviewer recalcula los
bindings antes de leerlo. Deriva, symlink, oversize o lectura fuera del
worktree bloquea. El artefacto no autoriza y se elimina tras publicar el
receipt o al invalidar el intento.

### 4.7 `DeliveryAuditV1`

Es una vista serializable y read-only para el usuario. Presenta estado visible,
identificadores/digests de recibos, PR/commit observados, reintentos consumidos
y siguiente acción. Nunca contiene grants, ni transforma `UNKNOWN` en `PASS`,
ni ejecuta limpieza. Un éxito normal usa una línea compacta; los bloqueos
incluyen causa, evidencia faltante y acción segura siguiente.

### 4.8 `LocalCandidateReceiptV1`

Es la única verdad dinámica de promoción local. El builder y validator forman
un contrato JSON cerrado y acotado a 8 KiB, siempre con `authorizes: false` y
`receipt_digest` canónico. Liga `candidate_id`, repositorio, rama, `HEAD`,
`product_version`, runtime digest, sujeto del worktree, snapshot de seguridad,
índice e inventario, suite, gates exactos, resultados de las revisiones y el
estado no autorizante del sandbox. Cada sujeto o snapshot separa `algorithm` y
`digest`; comandos se guardan como argv acotado, nunca con output o log bodies.

El store tiene una única ruta bajo el Git dir del worktree:
`codex-control-plane/candidates/v2-3-local-candidate.json`. Acepta un parent
owner-safe `0755` preexistente sin `chmod`, mientras `candidates` permanece
`0700` y el leaf `0600`; un parent escribible por group/other, symlink o foreign
owner se preserva y falla cerrado. Usa descriptores con `O_NOFOLLOW`, creación
exclusiva, fsync y publicación atómica.

Recovery hace un inventario acotado y descriptor-relative. El estado publicado
normal conserva el canonical y exactamente un pending reservado como hardlinks
del mismo inode, owner, modo, contenido y digest, con `nlink=2`; `load()` y el
replay byte-equivalente validan el par completo. El suffix del pending son los
64 hex completos de su `receipt_digest`, sin `sha256:`; inventario, `load()`,
replay y recovery exigen que nombre y contenido coincidan exactamente. Un
canonical-only con
`nlink=1` sigue siendo válido únicamente como formato legacy. Un orphan pending
se publica solo si es el receipt exacto, regular, owner-safe, `0600`, con nombre
reservado y link único, y conserva ambos nombres después de enlazar el
canonical. El store nunca ejecuta cleanup ni `unlink` sobre pathnames candidate:
un partial pre-link, múltiples pending, mismatch, symlink, hardlink inesperado,
nombre foreign, replacement, modo/owner incorrecto, JSON malformado u oversize
se preserva y falla cerrado. La ruta pública única sigue siendo el canonical;
el pending es estado interno no autorizante. Nunca persiste autoridad, sesión
nativa, nonce, grant, credencial, prompt ni cuerpo de log.

## 5. Lifecycle y handoff de Git

El cambio corrige la frontera defectuosa sin crear un lifecycle paralelo.

| Precondición comprobada | Efecto permitido | Estado posterior | Evidencia que lo confirma |
| --- | --- | --- | --- |
| gates y receipts aplicables en `PASS`; `review_ready`; sin implementation lease | adquirir delivery lease exacta | review_ready | owner, HEAD, diff, scope y generación ligados |
| delivery lease viva; grant `local_write` fresco | staging exacto de `scope_paths` | review_ready | índice y snapshot local exactos |
| índice verificado; grant `commit` fresco; sin cambios ajenos | commit local y liberar delivery lease | committed | SHA local y observación Git host-bound |
| committed; sin lease activo | consumir grant `remote_write` y push normal | pushed | observación remota exacta |
| pushed; grant `pull_request` fresco | crear/actualizar PR draft | pr_draft | PR, base, head y SHA observados |
| PR draft; checks y feedback requeridos observados | marcar ready | pr_ready | checks `PASS`, comentarios resueltos y receipt de PR |
| pr_ready; policy squash; grant `integration` exacto | squash merge | merged | método, PR y merge SHA observados |
| merged | tarea raíz refresca `origin/<base>` con argv cerrado; kernel comprueba | base_verified | contención del SHA y base observada |

`task_allows_writer_lease()` conserva sus estados de implementación. Una ruta
separada permite una delivery lease exclusivamente en `review_ready`; no
autoriza editar implementación ni reanudarla. La tarea no se cierra para
fabricar un contexto remoto. `TaskStore.close()` permanece para un outcome
terminal demostrado: `local_change` tras `review_ready`, `commit` tras
`committed`, `pull_request` tras `pr_ready` e `integration` tras
`base_verified`.

El commit usa un marker durable `finalizing_delivery_commit` bajo el Git dir.
La fase `prepared` se escribe antes de staging y liga snapshot, allowlist,
índice esperado, parent, árbol y digest del mensaje; le siguen `index_observed`,
`git_committed`, `state_committed` y `lease_released`. Recovery observa índice,
parent, tree, mensaje y SHA antes de completar idempotentemente lifecycle y
lease. Una interrupción o mismatch nunca repite stage ni commit a ciegas.

Una escritura con resultado incierto no se reintenta a ciegas. El bridge
observa remoto con los bindings originales y clasifica `PASS`, `FAIL` o
`UNKNOWN`; este último deja la ejecución `BLOCKED` hasta intervención humana.

## 6. Revisión, gates y reintentos

Los gates requeridos proceden de policy/perfil y se validan contra el mismo
`HEAD`, diff, repositorio y argv digest. Falta, fallo, vencimiento, deriva o
`UNKNOWN` bloquea. Un recibo local se puede reusar únicamente si todos esos
bindings —incluido perfil y versión de comando— son idénticos; nunca sustituye
la observación remota, la revisión independiente ni una autorización.

T2/T3 permanecen en `verifying` mientras se construye y valida el paquete de
revisión. No existe un estado `review_pending` ni se publica `review_ready` de
forma anticipada.

Un finding local `Critical|Important` usa una transición específica
`verifying → implementing`: consume el receipt exacto, incrementa el intento,
invalida gates/reviews/artefacto previos y adquiere una implementation lease
nueva. `start_revision()` conserva su significado actual para feedback de PR;
no se reutiliza fuera de `pr_draft|pr_ready`.

El flujo conserva máximo tres ejecuciones totales: intento inicial más dos
reparaciones. La causa repetida, el crecimiento de alcance, trabajo ajeno,
divergencia de base/HEAD, evidencia remota `UNKNOWN` o ambigüedad sensible
terminan en `BLOCKED` antes de gastar otro intento. Critical/Important de
revisión obligan a nuevo `HEAD`, gates nuevos y una nueva revisión.

## 7. Operación GitHub limitada

La primera mutación de PR crea una PR draft. El bridge observa de forma
limitada y con identidad exacta: PR, repositorio, base, head, SHA, required
checks, comentarios y threads de revisión. Solo pasa a `pr_ready` si no hay
hallazgos bloqueantes y la policy define todas las comprobaciones como `PASS`.
Si la API no puede observar una condición, el resultado es `UNKNOWN` y se
bloquea.

El merge solo admite squash, requiere `OutcomeAuthorizationContext` de
`integration` aún válido y exige que `git.integration_strategy` sea `squash`;
otra estrategia produce `BLOCKED_UNSUPPORTED_INTEGRATION_STRATEGY`. Quedan
denegados estructuralmente force-push,
auto-merge, rebase merge, deploy y release. Tras recibir observación de merge,
la tarea raíz refresca el ref exacto mediante la herramienta host y el kernel
read-only prueba que contiene el SHA esperado. Un merge devuelto por GitHub
pero no verificable en la base no se declara integrado.

## 8. Eficiencia de contexto y experiencia personal

La v2.3 usa el coste de contexto como constraint de diseño, no como fuente de
seguridad:

- el intake entrega al agente un paquete de tarea limitado, criterios, paths,
  estado y última causa de bloqueo, no transcripciones completas;
- el reviewer recibe solo `ReviewPacketV1` y una lectura limitada del diff
  estable, no historia de chat ni instrucciones de ejecución;
- la ruta feliz emite estado, artefactos verificados y siguiente acción en
  formato terso; excepciones incluyen detalle suficiente para reparar;
- las comprobaciones ya recibidas no se repiten si su igualdad exacta está
  demostrada. Los checks remotos se vuelven a observar cuando cambia su objeto
  o hay incertidumbre;
- la auditoría de entrega es local, read-only y agregada por recibos. No se
  añade telemetría cloud ni se afirma ahorro de tokens sin medidas.

## 9. Seguridad, recuperación y documentación

El adaptador de producción sigue fail-closed. Los fixtures de host de tests no
pueden importarse como runtime. Los comandos `git` y `gh` reciben argv
cerrado/saneado por proveedores ya validados; no se interpolan títulos,
comentarios, paths o contenido remoto como shell.

La recuperación es intencionalmente conservadora:

- antes de commit, se puede restaurar solo el conjunto staged identificado;
- después de commit local, una corrección es un commit nuevo en la rama, nunca
  `reset --hard`;
- tras push o PR no hay borrado, force-push ni cierre automático;
- ante escritura incierta se observa antes de cualquier acción posterior;
- tras squash merge se observa base; no se revierte remoto automáticamente.

La implementación debe crear el ADR de autoridad, un threat model específico y
un runbook de rollback; además actualizar la guía Git/PR, lifecycle, skill y
`SECURITY.md`. Esos documentos explican límites locales frente a GitHub Free y
no prometen branch protection remota.

## 10. Criterios de aceptación de v2.3

- Ningún contrato serializable concede ni reproduce autoridad; las pruebas
  cubren serialización, replay, expiración y cada binding derivado.
- Una petición nativa válida cubre de forma continua todos sus efectos
  ordinarios mientras los bindings sigan estables; ninguna ruta feliz pide al
  usuario tipos, grants, nonces, IDs de sesión o un «mensaje exacto» interno.
- `E_RUN_AUTHORITY` por ausencia del bridge en una instalación soportada es un
  fallo de aceptación, no un estado operativo aceptable ni un reintento del
  usuario.
- `pull_request` e `integration` se preparan con efectos remotos diferidos y
  sin autoridad sintética; `answer|release` se rechazan.
- El sandbox privado hasta `PR LISTA` es el gate operativo predeterminado antes
  de promover el candidato fuera de la evidencia local; exige una petición
  nativa actual y exacta para ese alcance.
- Interrupciones después de staging, del commit o de cada fase de publicación
  se recuperan idempotentemente desde `finalizing_delivery_commit`.
- La composición `verifying → review_ready → committed → ... → pr_ready`
  funciona sin `staged` ni `review_pending` y sin cerrar prematuramente.
- Un T2/T3 con receipt ausente, stale, `UNKNOWN`, `Critical` o `Important`
  queda bloqueado y exige nueva verificación tras el nuevo `HEAD`.
- La allowlist de staging, cambios untracked, worktree sucio, cambios ajenos,
  base/HEAD divergentes y gates requeridos se prueban con resultados triestado.
- La CLI permanece local y no gana flags de mutación remota.
- El sandbox squash es una prueba separada y no se exige ni ejecuta para el
  gate `PR LISTA`. Solo puede ejecutarse con un mandato nativo actual, fresco y
  exacto «hasta squash merge»; sin él, la integración permanece sin mutación.
- Un merge autorizado usa squash y termina solo tras observar el SHA en
  `origin/<base>`; todos los demás modos quedan denegados.
- No se introducen dependencias, secretos, CI/CD, telemetría cloud ni
  automatización de release.

## 11. Secuencia de promoción

1. La implementación local está verificada en esta rama, sin atribuirle efectos
   remotos ni evidencia GitHub.
2. Mantener la revisión independiente del diff estable y resolver hallazgos
   bloqueantes antes de cualquier sandbox.
3. Con una autorización nativa separada y actual, ejecutar el gate operativo
   predeterminado: sandbox privado hasta `PR LISTA`, con observación de sus
   checks y feedback.
4. Tratar el sandbox squash como una prueba separada: no se exige ni ejecuta
   salvo mandato exacto «hasta squash merge» y bindings actuales.
5. Promover `product_version` y la release skill desde `2.1.1` solo mediante
   una promoción de release separada; el candidato local se refiere por digest.

La v3.0 de plugin continúa siendo una promoción posterior: debe empaquetar el
comportamiento ya demostrado, no introducir uno nuevo.
