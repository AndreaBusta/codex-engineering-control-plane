# Seguridad del Control Plane

## Objetivo

Este repositorio reduce errores de proceso; no es un sandbox, un gestor de
secretos ni una frontera completa frente a un agente, plugin o proceso
comprometido. Los controles locales complementan aprobaciones humanas, GitHub,
CI y el proveedor de release.

## Activos

- autoridad explícita del usuario;
- código y documentación del proyecto;
- historial Git y worktrees;
- credenciales gestionadas fuera del repositorio;
- policy, registry, lock, decisiones y recibos;
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
| Prompt injection concede push/release | autoridad host-bound, opaca y one-shot por efecto; ningún payload serializable autoriza | plan/receipt `authorizes=false` y revalidación executor-edge | host emisor comprometido |
| Recurso duplicado o suplantado | ID ASCII, canonicalidad, digest y bloqueo ambiguo | `E_RESOURCE_AMBIGUOUS` | inventario externo degradado |
| Traversal o symlink escape | locators sin ejecución y resolución confinada | `R_LOCATOR`, `R_SYMLINK_ESCAPE` | filesystem alterado tras snapshot |
| Exfiltración por MCP/plugin | egress y data classes; autorización separada | inventory + receipt | proveedor comprometido |
| Escritura en worktree equivocado | preflight y TaskLease | lease digest | proceso fuera del control plane |
| Dos writers se pisan | ownership, lock de leases y validador de grafo | `E_LEASE_CONFLICT`, `G_WRITER_OVERLAP` | edición externa no cooperativa |
| Redirección por entorno Git | se eliminan variables `GIT_*` que cambian repo/worktree | test de repositorio redirigido | binario Git comprometido |
| Locator de skill escapa su raíz | nombre de skill de un solo componente y containment tras resolver | `R_LOCATOR`, `R_SYMLINK_ESCAPE` | filesystem alterado tras snapshot |
| Receipt afirma uso o gate sin prueba | locator digest, report digest y binding al RouteDecision | `E_RECEIPT_RESOURCE_EVIDENCE`, `E_RECEIPT_GATE` | informe subyacente falsificado por proceso local comprometido |
| Hook malicioso o cambiado | trust por hash y `/hooks` | `pending_hook_trust` | hook de otra capa también se ejecuta |
| Bucle de `Stop` | chequeo `stop_hook_active` | test de reentrada | fallo del host |
| Oversharing tras compactación | `SessionStart(compact)` y manifiesto menor de 4 KiB sin prompt | test de presupuesto | otros hooks pueden añadir contexto |
| Receipt filtra información | solo IDs, digests, gates y efectos | contrato v1 | quien escriba fuera del runtime |
| JSON simula una aclaración resuelta | el request es diagnóstico y el gate material queda `pending_host_capability` | contrato puro + tests de ausencia de autoridad | host sin adapter nativo |
| Evidencia ausente parece éxito | `risk-status` separa `PASS`, `FAIL` y `UNKNOWN` | exit codes 0/1/2 + checks locales | observaciones externas pendientes |
| Instalación destruye config | plan inmutable, lock de proceso, transacción, backup y rollback en dos fases | tests con fault injection y drift | caída del sistema operativo durante fsync |
| Guard Git usa policy candidata mutable | snapshot content-addressed bajo Git common dir | manifest y digests instalados | operación fuera de los hooks instalados |
| Perfil técnico mal clasificado | evidencia acotada, híbridos y fallback genérico | `project_profile` + tests por stack | estructura atípica sin marcadores |
| Supply chain CI | stdlib, acciones por SHA y permisos read-only | contrato CI | runner o acción fijada comprometida |

## Hooks

Los hooks se entregan en `audit`. Codex exige revisar y confiar su hash con
`/hooks`; hasta entonces su estado es `pending_hook_trust` y pueden omitirse.
Los hooks que pasan no imprimen nada, usan timeout de tres segundos y nunca
persisten el prompt. Un hook no cubre hosted tools ni todos los caminos
especializados, por lo que no es una frontera de seguridad.

`soft-enforce` queda fuera de la entrega v2.1. Aunque el runtime conserve
comportamiento mecánico compatible para futuras pruebas, el lock fija `audit` y
no existe una ruta soportada de promoción. Cambiar una variable de entorno no
actualiza policy, lock, trust ni evidencia y no constituye enforcement
gobernado.

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
efectos: ni siquiera `source=user_explicit` puede concederlos. La autoridad
del outcome bridge v2.3 permanece opaca y host-bound, se consume una vez por
efecto y nunca se serializa en sus planes, receipts, CLI o recovery. La
evidencia no es autoridad.

Una cadena estable puede continuar sin nuevas preguntas hasta su outcome
actual. `PR LISTA` es el default; integración exige una petición nativa fresca y
exacta hasta squash merge. Deriva o un efecto nuevo exige una sola decisión de
producto. Si falta el adaptador nativo, se bloquea: no se pide al usuario
configurar plumbing interno.

GitHub, Xcode Cloud y TestFlight permanecen
`pending_external_evidence` hasta consultar el proveedor. La protección de
`main` sigue incompleta mientras el plan de GitHub no permita aplicarla.

La distribución v2.1 local-audit no instala `.github/workflows/**`, no añade un
provider GitHub y no acepta policy candidata como sustituto de evidencia
externa. Por ello la dimensión remota de `risk-status` es `UNKNOWN` por diseño.

El modelo específico de replay, drift, stale review y uncertain write está en
[v2.3 outcome bridge threat model](docs/security/2026-08-08-v2-3-outcome-bridge-threat-model.md).
Los hooks y guards locales no son GitHub branch protection.

## Reportar una vulnerabilidad

No abrir una Issue pública con secretos o material explotable. Conservar
evidencia mínima, describir impacto y reproducción sin credenciales, y usar un
canal privado del propietario del repositorio.
