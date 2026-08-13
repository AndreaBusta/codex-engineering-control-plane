# Seguridad del Control Plane

## Objetivo

Este repositorio reduce errores de proceso mediante Control Plane Core
`3.1.0-core.1`; no es un sandbox, un gestor de secretos ni una frontera completa
frente a un agente, plugin o proceso comprometido. Los controles locales
complementan las aprobaciones humanas, Git, CI y los proveedores externos.

## Activos

- autoridad explícita del usuario;
- código y documentación del proyecto;
- historial Git y worktrees;
- credenciales gestionadas fuera del repositorio;
- policy, registry, lock, allowlist exacta y runtime digests;
- `CoreTaskStateV1`, leases generacionales y estado de mantenimiento;
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
| Dos verificadores duplican la suite | mutex no bloqueante por Git common dir | `E_VERIFICATION_BUSY`, `executed=false` | proceso externo no cooperativo |
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
- Estado legacy nunca se reescribe ni se reanuda con Core.
- `legacy_writer_exclusion=COOPERATIVE_ONLY`: Core bloquea todo estado legacy
  observable, pero un proceso v2.1 del mismo usuario que arranque después de
  esa observación no participa en los locks Core. No ejecutes writers legacy y
  Core en paralelo; una garantía bilateral requiere cambiar ambos runtimes.
- El rollback valida targets y backups y, sin una barrera global compartida,
  falla antes de mutar el primero.
- Evidencia, documentos y resultados conservan `authorizes=false`.

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
- Los documentos históricos v2.3/v2.4 son evidencia de diseño, no contratos
  gobernantes ni exclusiones de seguridad.
- El dogfood de diez tareas y la adopción estable siguen pendientes; no se
  aceptan como controles ya demostrados.

## Reportar una vulnerabilidad

No abrir una Issue pública con secretos o material explotable. Conservar
evidencia mínima, describir impacto y reproducción sin credenciales, y usar un
canal privado del propietario del repositorio.
