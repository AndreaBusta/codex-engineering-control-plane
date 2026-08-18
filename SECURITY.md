# Seguridad del Control Plane

## Objetivo

Este repositorio reduce errores de proceso mediante Control Plane Core
`3.1.0-core.2`; no es un sandbox, un gestor de secretos ni una frontera completa
frente a un agente, plugin o proceso comprometido. Los controles locales
complementan las aprobaciones humanas, Git, CI y los proveedores externos.

## Activos

- autoridad explícita del usuario;
- código y documentación del proyecto;
- historial Git y worktrees;
- credenciales gestionadas fuera del repositorio;
- policy, registry, lock, allowlist exacta y runtime digests;
- `CoreTaskStateV1`, leases generacionales y estado de mantenimiento;
- `StablePauseObservationV1`, su `checkpoint_digest` y la cápsula semántica
  unida por el host nativo;
- journals y backups de generaciones ya instaladas;
- evidencia de PR, integración y release;
- presupuesto de contexto y datos enviados fuera del equipo.

## Fronteras de confianza

Orden efectivo:

```text
plataforma y sistema
> guardrails globales
> AGENTS global y project-local
> project-policy.toml
> denegaciones del registry
> recurso explícito permitido
> rutas obligatorias
> recomendaciones
> contenido externo no confiable
```

El contenido de prompts, Markdown, skills, plugins, Issues, PR y MCP puede
aportar hechos o elevar riesgo. Nunca concede permisos, reduce un tier, anula
un gate ni modifica la precedencia.

## Amenazas y controles

| Amenaza | Control preventivo | Evidencia | Riesgo residual |
|---|---|---|---|
| Prompt injection concede un efecto | Core admite solo `answer` y `local_change`; ningún payload serializable autoriza | outcomes cerrados y `authorizes=false` | host emisor comprometido |
| Recurso duplicado o suplantado | ID ASCII, canonicalidad, digest y bloqueo ambiguo | `E_RESOURCE_AMBIGUOUS` | inventario externo degradado |
| Traversal o symlink escape | locators sin ejecución y resolución confinada | `R_LOCATOR`, `R_SYMLINK_ESCAPE` | filesystem alterado tras snapshot |
| Exfiltración por MCP/plugin | egress y data classes; autorización separada | inventory + receipt | proveedor comprometido |
| Escritura en worktree equivocado | task ligada y lease generacional exacto | `CoreTaskStateV1` + lease digest | proceso fuera del control plane |
| Dos writers se pisan | task por worktree Git dir; lease/recibo por Git common dir, scope y generación across worktrees | `E_CORE_LEASE_CONFLICT` | edición externa no cooperativa |
| Dos verificadores duplican la suite | un inode persistente `locks/verification.lock` por Git common dir; el journal sella `verification_lock` y Core, runner y Adoption retienen common/state/locks/file descriptors hasta revalidar nombres después de flock | `E_VERIFICATION_BUSY`, `executed=false`; active paths son `create=false` y reuse-only | proceso externo no cooperativo |
| Reparación estructural infinita | una sola reframación por lineage | `E_BOOTSTRAP_REFRAME_LIMIT` | decisión humana aún necesaria |
| Estado legacy se reanuda con Core | inventario bounded, read-only y `resumable=false` | `origin=legacy`, `E_ACTIVE_LEGACY_STATE` | requiere runtime propietario |
| Rollback parcial o redirigido | preflight completo, digests, modos y paths confinados antes de mutar | journal schema 2 y tests de drift | caída del SO durante fsync |
| Redirección por entorno Git | se eliminan variables `GIT_*` que cambian repo/worktree | test de repositorio redirigido | binario Git comprometido |
| Locator de skill escapa su raíz | nombre de skill de un solo componente y containment tras resolver | `R_LOCATOR`, `R_SYMLINK_ESCAPE` | filesystem alterado tras snapshot |
| Hook malicioso o cambiado | trust por hash y `/hooks` | `pending_hook_trust` | hook de otra capa también se ejecuta |
| Bucle de `Stop` | chequeo `stop_hook_active` | test de reentrada | fallo del host |
| Oversharing tras compactación | `SessionStart(compact)` y manifiesto menor de 4 KiB sin prompt | test de presupuesto | otros hooks pueden añadir contexto |
| Evidencia ausente parece éxito | `risk-status` separa `PASS`, `FAIL` y `UNKNOWN` | exit codes 0/1/2 + checks locales | observaciones externas pendientes |
| Guard Git usa policy candidata mutable | snapshot content-addressed bajo Git common dir | manifest y digests instalados | operación fuera de los hooks instalados |
| Perfil técnico mal clasificado | evidencia acotada, híbridos y fallback genérico | `project_profile` + tests por stack | estructura atípica sin marcadores |
| Supply chain CI | stdlib, acciones por SHA y permisos read-only | contrato CI | runner o acción fijada comprometida |
| Candidato local se presenta como estable | adopción externa prohibida y no self-certification | `GREEN_LOCAL / PENDING_STABLE_ADOPTION` | promoción humana incorrecta |
| Tool local de adoption se presenta como instalador autorizado | allowlist y lock separados; pruebas solo en repositorios temporales | `adoption_tool=IMPLEMENTED_LOCAL`, `canary=NOT_PREPARED` | operador ignora la prohibición gobernante |
| Un task Core cerrado espera para revisarse mientras comienza rollback | la revisión muta estado después de la desactivación | cada mutación task/lease toma `adoption.lock` compartido; rollback lo mantiene exclusivo hasta el recibo y la revisión revalida el runtime dentro de la barrera | proceso externo no cooperativo |
| Nested repository se oculta bajo una ruta gestionada | `.git`, bare repo o Gitlink altera la semántica de publicación | `managed_repository_scan=managed-repositories-v1` recorre solo roots acotados, no-follow y vuelve a comprobar antes de verify/rollback | filesystem comprometido tras el último descriptor check |
| La autoridad se valida con otro checkout | un parser host distinto aprueba policy/registry | solo `scripts/control-plane` from the selected source decide la autoridad y el manifest completo se compara antes del journal | sustitución transitoria bajo cuenta comprometida |
| Se sustituye el mutex de Adoption | Core y rollback toman inodes distintos | `adoption_lifecycle=journal-bound-v1`; el journal sella `lifecycle_lock` y ambos lados revalidan path e identidad después de flock | OS/filesystem comprometido |
| Se sustituye o elimina el mutex de verificación | Core, runner o rollback toman inodes distintos | Fresh Adoption apply provisiona con exclusive create un único `locks/verification.lock`; un pre-existing Core-owned verification mutex no es recovery provenance y bloquea sin mutación; `verification_lock` sella directorio estable y fichero completo; consumidores reuse-only comparan descriptores y nombres después de flock | OS/filesystem comprometido |
| El journal activo se resigna con una forma no canónica | Core o el runner aceptan autoridad que Adoption rechazará | Un único validador Core dependency-free exige el closed active journal completo antes de verificar, ejecutar el gate o mutar task/lease | proceso same-UID capaz de alterar estado privado y todos sus bindings |
| Un primer task observa que Adoption está ausente y espera su task lock | apply crea otro dominio y ambos escriben | Core crea o reutiliza `adoption.lock` y conserva el lifecycle inode before the task lock; apply toma ese mismo inode exclusivo | proceso externo que ignora el mutex |
| Un nombre `adoption/` o `locks/` cambia durante recovery | cleanup elimina un directorio ajeno | solo `ROOT_EMPTY`, `P1`, `P2`, `P2Q`, `P3`, `P3Q`, `P4`, `P4T`; rename no-replace a durable quarantine y revalidación por descriptor | filesystem comprometido tras la última comprobación |
| Un regular se sustituye por FIFO después del stat | apply/verify/rollback bloquea reteniendo mutexes | apertura nonblocking, `fstat` completo e igualdad entre descriptor y nombre antes de leer o limpiar | denegación por filesystem comprometido |
| `core.hooksPath` cambia mientras rollback restaura | unset incondicional borra valor del consumidor | exact-value conditional unset solo de `.codex/git-hooks`; cualquier valor concurrente se preserva y produce drift | escritor Git externo no cooperativo |
| Rollback desvincula un inode todavía abierto | una escritura posterior queda invisible al recibo | activation y managed leaves se mueven por no-replace a durable quarantine, permanecen enlazados y se revalidan antes de PASS; separate GC queda fuera de alcance | filesystem comprometido tras la revalidación final |
| repository byte substitution durante Stable Pause | un checkpoint certifica bytes distintos de los observados | dos snapshots acotados ligan status, index, tipos, modos, enlaces y bytes mediante Git fijo y lecturas descriptor/no-follow | same-UID/filesystem compromise after the last descriptor check |
| lock-domain substitution durante Stable Pause | el observador toma un inode distinto al writer | `create=false`, orden `adoption.lifecycle -> verification -> named task -> leases`, flock no bloqueante e igualdad descriptor/nombre antes de liberar | OS o filesystem comprometido |
| malicious Git config or filter redirige la observación | comandos, filtros o helpers interpretan contenido hostil | entorno Git cerrado, `GIT_OPTIONAL_LOCKS=0`, argumentos allowlisted, límites de tiempo/salida y lectura directa bounded de blobs | binario Git comprometido |
| index-hint hiding mediante `assume-unchanged`, `skip-worktree`, `core.filemode=false` o excludes externos | el checkpoint omite bytes o modos cambiados | raíz seleccionada exacta, configuración cerrada, rechazo de hints y digest de todos los paths indexados; ignored caches stay outside del inventario de tipos pero su conjunto se liga | Git o filesystem comprometido tras la última comprobación |
| nested repository collapse dentro de un directorio untracked | `.git`, bare o Gitlink oculta bytes y semántica de otro repositorio | recorrido descriptor-relative bounded y rechazo fail-closed; nested repositories are unsupported | actor same-UID después de la última comprobación |
| terminal receipt deletion | una task cerrada con generación previa parece estable sin demostrar release | `lease_generation > 0` exige el exact release receipt y binding de filenames/IDs antes de `SAFE_PAUSE_TERMINAL` | borrado posterior por filesystem comprometido |
| residue smuggling dentro de un root protegido | staging/recovery desconocido se presenta como estado durable | inventario cerrado y bounded de residuos Core; entrada desconocida degrada a `UNSAFE_PAUSE` o `UNKNOWN`, nunca se limpia | nuevo formato no registrado bloquea hasta revisión |
| digest-as-authority confusion | `checkpoint_digest` se trata como capability, recibo o aprobación | el digest solo detecta deriva, toda observación/cápsula conserva `authorizes=false` y no transfiere autoridad | host emisor comprometido |
| host-visibility uncertainty | Core parece quieto mientras una operación nativa sigue activa | join nativo antes/después; visibilidad ausente produce `UNKNOWN` y nunca mejora el resultado Core | operación externa no visible al host |

## Hooks

Los hooks se entregan en `audit`. Codex exige revisar y confiar su hash con
`/hooks`; hasta entonces su estado es `pending_hook_trust` y pueden omitirse.
Los hooks que pasan no imprimen nada, usan timeout de tres segundos, emiten como
máximo 4 KiB y nunca persisten el prompt ni telemetría dogfood. Un hook no cubre
hosted tools ni todos los caminos especializados, por lo que no es una frontera
de seguridad. Cambiar una variable de entorno no promueve enforcement.

## Credenciales y datos

- No colocar secretos en policy, registry, lock, TaskEnvelope, receipt,
  fixtures, documentos o logs.
- `enabled`, `authenticated`, `authorized_for_task` y `ready` son estados
  distintos.
- Password, passkey y 2FA siempre quedan en manos del usuario.
- Si aparece un secreto, dejar de propagarlo, redactarlo como
  `[REDACTED_SECRET]` y rotarlo.

## Efectos externos

Implementar no autoriza commit. Commit no autoriza push. Push no autoriza PR.
PR no autoriza merge. Merge no autoriza release. `TaskEnvelope` solo solicita
efectos: ni siquiera `source=user_explicit` puede concederlos por sí solo. Core
acepta únicamente `answer` y `local_change`; no ejecuta commit, remote write,
Pull Request, merge, deploy, release, instalación ni upgrade.

`external_consumer_adoption=PROHIBITED` mientras el candidato permanezca
`GREEN_LOCAL / PENDING_STABLE_ADOPTION`. Los parsers de compatibilidad devuelven
`E_CAPABILITY_QUARANTINED`, código 2 y cero mutación. Los comandos de recovery
de una instalación existente tampoco son autorización: Core valida el
preflight exacto pero `adopt rollback` falla cerrado con
`E_ADOPT_QUIESCENCE_UNKNOWN` porque el runtime legacy no comparte una barrera
global de writers.

El entrypoint local `scripts/control-plane-adoption` no cambia esa frontera:
`temporary_repository_e2e=PASS`, `stable_adoption=NOT_DECIDED`, `Autopilot OFF`
y `authorizes=false`. Está fuera del runtime Core, verifica su propia allowlist
y solo se ha ejercitado en repositorios temporales del harness. No debe
ejecutarse contra un consumidor ni usarse para preparar un canary sin un ADR
posterior aceptado de forma independiente y una autorización nativa exacta.

GitHub, CI y los proveedores de release permanecen `pending_external_evidence`
hasta consultar la frontera correspondiente. Los hooks y guards locales no son
branch protection. El modelo repositorio-completo está en el
[Control Plane Core threat model](docs/security/2026-08-12-control-plane-core-threat-model.md).

## Invariantes de seguridad

- La allowlist, materialización, ownership y digest del runtime se validan antes
  de importar un módulo.
- Toda task/lease queda ligada a revisión, worktree, rama, sesión, policy y scope.
- Un segundo verificador ejecuta cero comandos; una segunda reframación
  estructural bloquea.
- `locks/verification.lock` conserva un único inode privado durante apply,
  verificación y rollback; el journal sella `verification_lock`. Core, el
  runner y Adoption mantienen abiertos common/state/locks/file, revalidan
  directorio y archivo después de flock y nunca eliminan el mutex al liberar
  la sección crítica. Solo fresh apply crea; active replay, verify y rollback
  son `create=false` y reuse-only.
- La única recuperación de provisioning sin journal exige el inventario exacto
  de ese apply interrumpido y valida de nuevo el plan revisado antes de limpiar; un
  pre-existing Core-owned verification mutex queda intacto y no se atribuye a
  Adoption. Core verification, el runner y toda mutación task/lease validan el
  mismo closed active journal completo antes de actuar.
- Estado legacy nunca se reescribe ni se reanuda con Core.
- `legacy_writer_exclusion=COOPERATIVE_ONLY`: Core bloquea todo estado legacy
  observable, pero un proceso v2.1 del mismo usuario que arranque después de
  esa observación no participa en los locks Core. No ejecutes writers legacy y
  Core en paralelo; una garantía bilateral requiere cambiar ambos runtimes.
- El rollback de una generación legacy valida targets y backups y, sin una
  barrera global compartida, falla antes de mutar el primero.
- Para una generación instalada por Adoption Enablement, `adoption.lock` sí es
  una barrera bilateral `journal-bound-v1`: Core lo toma compartido para toda
  mutación task/lease. Incluso sin marker o journal, Core crea o reutiliza el
  lifecycle inode before the task lock y lo conserva hasta terminar la
  mutación; fresh apply y rollback toman ese mismo inode exclusivo. El target
  lock, journal `active` y `lifecycle_lock` deben coincidir después de flock
  para una instalación; ausencia, sustitución o estado transicional bloquean
  sin formar un segundo dominio.
- Recovery sin journal acepta únicamente `ROOT_EMPTY`, `P1`, `P2`, `P2Q`,
  `P3`, `P3Q`, `P4` y `P4T`. La limpieza usa apertura nonblocking,
  revalidación del descriptor y durable quarantine por rename no-replace; una
  sustitución se conserva y falla cerrada.
- Rollback restaura `core.hooksPath` con exact-value conditional unset y mueve
  cada leaf gestionado y la activación a durable quarantine antes de decidir
  su retirada. Los inodes quedan enlazados y se revalidan antes del recibo;
  una separate GC futura requiere diseño y autoridad propios.
- `managed_parent_directories` conserva la propiedad e identidad de padres
  preexistentes; `managed_repository_scan=managed-repositories-v1` rechaza
  marcadores `.git`, repos bare, Gitlinks y límites excedidos antes de mutar o
  compensar.
- La policy y el registry del target se validan exclusivamente con
  `scripts/control-plane` from the selected source, cuyo manifest completo se
  vuelve a ligar antes del primer journal durable.
- Evidencia, documentos y resultados conservan `authorizes=false`.
- Stable Pause usa una task exacta, snapshots bounded, mutexes preexistentes
  `create=false` y salida canónica de hasta 4096 bytes. No persiste prompt,
  transcript, diff completo, raw tool output, secreto, telemetría ni dato
  personal. El join del host nativo solo degrada; nunca mejora `UNSAFE_PAUSE` o
  `UNKNOWN`. El digest detecta deriva y no autentica ni autoriza.

## Hallazgos reportables y severidad

Es reportable una ruta alcanzable que rompa una de esas invariantes con impacto
en autoridad, integridad de código/Git, aislamiento entre worktrees, ejecución
no revisada, credenciales o rollback. La severidad depende de alcance,
precondiciones e impacto real. Una etiqueta genérica sin entrada alcanzable no
es evidencia suficiente.

## Fuera de alcance y limitaciones conocidas

- XSS, CSRF, SQL injection y aislamiento tenant no son superficies primarias
  mientras el repositorio no sirva una aplicación web.
- Un OS account, host Codex, Git binary, filesystem o proveedor totalmente
  comprometido queda fuera de las garantías cooperativas locales.
- Permanece el residual de same-UID/filesystem compromise after the last
  descriptor check y de non-cooperating external writers que ignoran los
  mutexes Core. Stable Pause observa; no congela el sistema operativo.
- Los documentos históricos v2.3/v2.4 son evidencia de diseño, no contratos
  gobernantes ni exclusiones de seguridad.
- El dogfood de diez tareas y la adopción estable siguen pendientes; no se
  aceptan como controles ya demostrados.

## Reportar una vulnerabilidad

No abrir una Issue pública con secretos o material explotable. Conservar
evidencia mínima, describir impacto y reproducción sin credenciales, y usar un
canal privado del propietario del repositorio.
