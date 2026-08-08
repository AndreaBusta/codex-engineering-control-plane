# Control Plane v2.3 — revisión independiente, autoridad de resultado y Git Outcome Bridge

- **Estado:** diseño reconciliado; implementación no iniciada
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
   merge» que siga coincidiendo exactamente. No hay auto-merge, force-push,
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

`PR LISTA` es el resultado predeterminado. No significa autorización para
integrar, y ningún objeto serializado puede convertirlo en ella.

## 2. Hechos de partida

El diseño se limita a hechos observables en este worktree:

- `control_plane/host_bridge.py` ya contiene contratos host-bound para
  `TrustedAuthorization`, contexto remoto, staging, commit, push y creación de
  PR, y falla cerrado cuando el adaptador de producción no existe;
- el adaptador que permite tests vive en
  `tests/host_adapter_test_support.py`; no es una capacidad de runtime;
- `ReviewResultV1` es serializable y no autorizante, mientras que los
  publishers tipados de `IndependentReviewReceipt` fallan cerrado;
- `TaskStore` conoce los estados remotos, pero la composición actual exige
  `review_ready` y lease para staging, y exige una tarea cerrada para crear el
  contexto remoto. Eso impide una secuencia real hacia `pr_ready`;
- `prepare_run()` acepta únicamente `local_change`; la v2.3 debe aceptar el
  outcome final elegido al inicio sin reescribirlo después;
- la CLI `run` ya se limita a `prepare`, `verify`, `status` y `block`. Esa
  superficie local se conserva;
- `control-plane-run` ya cubre el vertical slice local y requiere revisión
  para T2/T3, pero no describe una salida remota autoritativa;
- no existe una API productiva que entregue un objeto nativo opaco desde Codex
  a un subprocess Python. El único adaptador actual es test-only y no se
  promociona ni se disfraza como bridge de producción;
- la rama conserva tres commits previos de v2.2. El árbol contiene exactamente
  estos dos documentos de planificación como archivos untracked; son el scope
  de M0 y no se confunden con evidencia de un preflight limpio.

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

- una observación host-only, opaca y one-shot, que contiene sesión,
  invocación, nonce y TTL y nunca se serializa;
- un `IndependentReviewReceipt` durable, cerrado y no autorizante, que el host
  publica únicamente después de consumir una observación válida.

La tarea raíz valida identidad, cursor y resultado del reviewer con las
herramientas nativas de tareas. Python valida después el schema y los bindings
del receipt, pero no afirma haber observado la tarea nativa ni instala un
adaptador test-only en producción.

El recibo durable liga:

- identidad/digest del reviewer y clase de revisión;
- `task_digest`, `run_plan_digest`, base, `head_sha`, `stable_diff_digest` y
  `criteria_digest`;
- digest de findings, resultado `PASS|FAIL|UNKNOWN` y momento observado;
- digest de la observación consumida y `authorizes: false`, sin sesión,
  invocación, nonce, TTL ni objeto host.

T2 requiere una revisión independiente `PASS`; T3 requiere además una revisión
de seguridad `PASS` cuando el perfil de riesgo la solicita. Un finding
`Critical` o `Important`, una revisión `FAIL`, una observación vencida o
reproducida, un receipt con bindings distintos o cualquier `UNKNOWN` mantiene
la tarea en `verifying`. El feedback inicia una revisión del mismo task solo
con un nuevo `HEAD`; invalida gates y recibos ligados al anterior. La transición
a `review_ready` sucede una sola vez, después de validar todos los receipts.

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
  merge».

El contexto contiene un grant-set raíz consumido una vez y claims one-shot para
los únicos efectos `local_write`, `commit`, `remote_write`, `pull_request` e
`integration`. No reutiliza `NativeUserInteractionEvent` ni
`HostAdapterCapability` dentro de Python. La tarea raíz ejecuta Git/`gh`
mediante las herramientas nativas del host; la CLI Python solo prepara el plan,
valida gates y reobserva hechos locales/remotos de forma read-only.

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
- Un sandbox nativo demuestra una sola petición humana hasta PR y otra hasta
  merge, incluida la sucesión `review_head → committed_head → merge_sha`.
- Interrupciones después de staging, del commit o de cada fase de publicación
  se recuperan idempotentemente desde `finalizing_delivery_commit`.
- La composición `verifying → review_ready → committed → ... → pr_ready`
  funciona sin `staged` ni `review_pending` y sin cerrar prematuramente.
- Un T2/T3 con receipt ausente, stale, `UNKNOWN`, `Critical` o `Important`
  queda bloqueado y exige nueva verificación tras el nuevo `HEAD`.
- La allowlist de staging, cambios untracked, worktree sucio, cambios ajenos,
  base/HEAD divergentes y gates requeridos se prueban con resultados triestado.
- La CLI permanece local y no gana flags de mutación remota.
- Un sandbox privado autorizado demuestra push/PR/checks/comentarios y
  observación previa a reintento; si no existe autorización o adaptador, el
  resultado es `BLOCKED`.
- Un merge autorizado usa squash y termina solo tras observar el SHA en
  `origin/<base>`; todos los demás modos quedan denegados.
- No se introducen dependencias, secretos, CI/CD, telemetría cloud ni
  automatización de release.

## 11. Secuencia de promoción

1. Construir y probar las fronteras locales en esta rama, sin efectos remotos.
2. Revisar independientemente el diff estable y resolver hallazgos bloqueantes.
3. Con autorización separada, hacer una PR de implementación y observar sus
   checks; no confundir esa PR con la capability que se está introduciendo.
4. Ejecutar el sandbox privado en una tarea nueva y con autoridad nativa nueva.
5. Solo entonces promover la documentación/skill de v2.3 como operativa.

La v3.0 de plugin continúa siendo una promoción posterior: debe empaquetar el
comportamiento ya demostrado, no introducir uno nuevo.
