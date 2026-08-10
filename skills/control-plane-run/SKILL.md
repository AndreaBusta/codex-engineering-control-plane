---
name: control-plane-run
description: Use for bounded, policy-routed engineering with durable lifecycle, verification, and review handoff.
---

# Control Plane Run

## Overview

Codex dirige; el kernel guarda policy, estados, gates y recibos: evidencia no es autoridad.
JSON y receipts no autorizan; lo remoto sigue host-bound. Python ejecuta
Git local allowlisted y hace `git ls-remote` read-only en prepare/arm/revalidate.
Las mutaciones push/PR/squash merge son host-native; Python no recibe autoridad. La autorización nativa queda
en el host; sin adaptador queda `BLOCKED`.

## Protocolo

1. Leer `AGENTS.md`, policy y registry; inspeccionar Git y ejecutar preflight.
2. Guardar un `TaskEnvelope v1` sin credenciales ni autoridad, con alcance
   mínimo y outcome inmutable: `local_change|commit|pull_request|integration`.
3. Mostrar `PLANIFICANDO` y ejecutar `scripts/control-plane run prepare --repo <repo>
   --task <task.json> --session-id <id> --json`. Detenerse si el route,
   materialización, branch, HEAD, policy o lease no pasan.
4. Mostrar `TRABAJANDO`; aplicar TDD solo en paths del lease.
5. Si T3 exige `gate.rollback-plan`, antes de `verify` la raíz persiste un
   `RollbackPlanV1` host-bound estructurado para intento/`HEAD`. Texto, scalar,
   `required_gates` o JSON no prueban PASS; falta/`UNKNOWN` bloquea. Sin CLI público.
6. Mostrar `VERIFICANDO` y ejecutar `scripts/control-plane run verify --repo <repo>
   --task-id <id> --json`. El kernel elige los comandos; no aceptar argv ni
   decisiones externas.
7. Reparar solo una causa distinta y repetir `run verify`.
   Permitir tres ejecuciones totales. Consultar `run status`. Reutilizar el
   receipt exacto solo con sujeto, inputs y digests idénticos.
8. Ejecutar `scripts/control-plane run block --repo <repo> --task-id <id> --reason <código>
   --json` y mostrar `BLOCKED`
   ante `UNKNOWN`, repetición, scope creciente, trabajo ajeno, deriva o
   agotamiento.
9. En `review_ready`, entregar diff y digests. Mostrar `PR LISTA` solo tras
   observar `pr_ready`; `review_ready` no concede efectos remotos.

Estados visibles: `PLANIFICANDO`, `TRABAJANDO`, `VERIFICANDO`, `PR LISTA` y
`BLOCKED`. PR LISTA es el resultado predeterminado. La ruta feliz es concisa:
estado, artefactos verificados y siguiente acción; el paquete de revisión tiene
un máximo de 4 KiB.

## Límites de promoción

- Para T2/T3 exigir revisión independiente con contexto nuevo; exigir además
  revisión de seguridad cuando lo active el riesgo. Si el host no puede
  producir esos recibos, terminar `BLOCKED`.
- En T3, plan y receipt rollback no autorizantes se revalidan contra revisión,
  intento, `HEAD` y alcance al revisar/promocionar.
- Una petición nativa actual puede continuar automáticamente su cadena estable
  one-shot `local_write` → `commit` → `remote_write` → `pull_request`. Solo una
  petición nativa actual, fresca y exacta que diga «hasta squash merge» permite
  añadir `integration`; PR LISTA por sí sola nunca lo permite.
- Ante deriva o un efecto nuevo, pedir una sola reautorización de producto,
  concisa. No repetir peticiones en una cadena estable ni pedir al usuario
  configuración o piezas internas. Si falta el adaptador nativo del host,
  finalizar `BLOCKED` con diagnóstico; nunca trasladar su configuración al
  usuario ni gastar un reintento.
- Ante una escritura remota incierta, observar antes de reintentar: cero
  segunda escritura y cero reparación remota hasta conocer el efecto. UNKNOWN
  termina en BLOCKED y conserva recovery; no se promueve a evidencia verde.
- Observaciones y receipts son no autorizantes; no conceden el efecto siguiente.

## Cierre

Informar estado visible, task ID, branch/HEAD, paths cambiados, número de
intentos, gates y riesgo residual. Terminar con el `## Continuación` requerido
por el proyecto. No afirmar producto acabado, PR o integración sin observación.
