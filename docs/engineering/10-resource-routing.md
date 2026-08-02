# Enrutamiento automático de recursos

## Qué automatiza

Codex encuadra la petición como `TaskEnvelope`. El resolver cruza esa intención
con policy, registry e inventario y devuelve una decisión mecánica. Codex debe
leer completamente todo recurso `required` antes de actuar y puede cargar
`recommended` dentro del presupuesto.

La selección y carga son automáticas cuando competa, pero nunca silenciosamente
autoritativas:

```text
required → debe utilizarse o bloquear
recommended → utilizar si aporta dentro del presupuesto
deferred → pertinente, pospuesto por coste
forbidden → no utilizar
unresolved → falta evidencia o disponibilidad
shadowed → sustituido por un canónico de mayor precedencia
```

Ni `enabled` ni `trusted` significan `authorized_for_task`. GitHub se resuelve
como capacidades separadas de lectura y escritura; “usar el plugin” no concede
ambas.

La automatización se reparte por superficie:

- `AGENTS.md` se carga mediante la jerarquía nativa de instrucciones;
- Codex encuadra el prompt y debe ejecutar el router antes de ingeniería
  sustancial;
- documentos/skills `required` se cargan de forma progresiva y las skills
  pueden activarse implícitamente por su descripción;
- plugins, MCP, autenticación, egress y efectos externos nunca se instalan ni
  se autorizan por el mero routing;
- el hook `UserPromptSubmit` recuerda este contrato y rehidrata estado
  compacto, pero no pretende interpretar lenguaje natural por sí solo.

Por tanto, “automático” significa que el usuario no tiene que recordar cada
recurso pertinente. No significa que un script opaco pueda concederse permisos.

Las dos distribuciones `superpowers` detectadas se registran con IDs y digests
separados, `canonical=false` y conflicto mutuo. No se deshabilitan. Si una ruta
pidiera su capacidad compartida antes de designar un canónico verificado, el
resolver fallaría con `E_RESOURCE_AMBIGUOUS`.

## Contratos

### TaskEnvelope

Contiene objetivo, unidades, intención, resultado pedido, dominios, fase,
rutas, señales, riesgo, efectos y procedencia. Las señales son cerradas y
versionadas. Auth, pagos, datos privados, migraciones, secretos, destrucción,
producción y release fuerzan T3.

Un prompt multifrente produce `prompt_multifront=true`. El router no crea cuatro
writers: valida dependencias, ownership y un fork-join de como máximo dos
workers cuando la independencia sea real.

### Resource Registry

`.codex/resource-registry.toml` declara capacidades y restricciones, no
ejecución. Prohíbe comandos, credenciales y URLs ejecutables. Un cambio requiere
actualizar `.codex/control-plane.lock`, tests y PR.

### InventorySnapshot

Separa:

```text
discovered
enabled
trusted
authenticated
healthy
authorized_for_task
ready
```

El snapshot contiene metadatos y digests, nunca contenido del recurso. Su
schema es cerrado, el digest se recalcula, no admite duplicados y `ready` debe
coincidir con availability + discovered + enabled + trust + auth + health.
El snapshot demuestra consistencia, no autoridad. Un recurso con egress
(`network_read`, MCP o equivalente) sigue bloqueado hasta recibir un
`AuthorizationGrant` separado y ligado a la tarea, aunque el inventario lo
marque operativo.
También incluye `project_profile`: detecta por marcadores iOS, Android, PWA,
SaaS/backend, IA textual, híbrido o genérico. El resolver usa esos perfiles
como dominios demostrados y carga sus guías de calidad obligatorias.

### RouteDecision

Incluye tier, modo, recursos, documentos, gates, aprobaciones, estrategia,
presupuesto y digests. En `audit`, un recurso remoto desconocido deja
`decision_ready=false` sin fingir bloqueo autoritativo. En `enforce`, bloquea.
`interaction` recomienda `default`, `plan`, `goal` o `plan_then_goal`, pero no
cambia la interfaz ni amplía autoridad.

### ResourceUseReceipt

Registra IDs, digests, recursos usados u omitidos, gates y efectos. Cada uso se
liga al digest del locator seleccionado en el inventario. Cada gate incluye el
digest de su informe y queda ligado al `RouteDecision` exacto: un booleano
`ok: true` aislado no constituye evidencia. Los gates obligatorios también
deben estar operativamente disponibles como recursos.

Tiene un único schema compartido por constructor, plantilla y `route-verify`;
todos los digests se recomputan y un objeto vacío falla cerrado. No conserva
prompt, transcript, documentos enteros ni output de MCP.

### CrossThreadAuditCapsule shadow

La auto-consulta entre tareas solo se selecciona al normalizar una referencia
completa `codex://threads/<uuid>` mediante
`prepare_cross_thread_audit_lookup()`. El helper añade al `TaskEnvelope` el
recurso explícito `agent.cross-thread-audit-read-shadow`; no interpreta prompts
ni habilita búsqueda global, fuzzy o steering.

La lectura pertenece a la superficie nativa del host. Como todavía no existe
un consumidor productivo que pruebe `read_thread`, el recurso usa un locator
`agent://`, confianza no verificada y selección recomendada. El inventario
local exige evidencia de runtime, por lo que routing lo conserva `unresolved`
con `R_RUNTIME_EVIDENCE_REQUIRED`. El runtime Python no llama, simula ni
reenvuelve resultados del host y no expone constructores de observaciones.

En esta fase la única cápsula schema 1 serializable es un JSON canónico
`UNKNOWN` de como máximo 4096 bytes. Conserva la referencia exacta y la
identidad del consumidor; proyecto, repositorio, HEAD, base, diff y subject se
mantienen explícitamente desconocidos. El renderer rechaza `VALID`, `STALE`,
`PASS`, findings/tests, IDs observados o estados incompatibles aunque el caller
recalcule el digest. No acepta transcript, prompt, hidden reasoning ni output
crudo y siempre fija `authorizes=false`. `VALID` y `STALE` quedan reservados
hasta que exista un evento nativo de lectura, attested y ligado al subject.

Un `ResourceUseReceipt` puede acreditar que el recurso shadow fue consultado,
pero la cápsula no es gate result ni publisher de `IndependentReviewReceipt`.
Por tanto no satisface `gate.independent-review`, no cambia lifecycle y no
concede commit, red, steering ni otro efecto. Esta costura aplica las fronteras
ya decididas por ADR 0001 y ADR 0003; no introduce una arquitectura paralela.

La reversión de esta fase es retirar módulo, registro y documentación en un
diff local. No existe migración, estado durable, hook, dato remoto ni efecto
externo que restaurar.

## Comandos

```bash
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml

scripts/control-plane inventory --json
scripts/control-plane route --task task-envelope.json --mode audit --json
scripts/control-plane route-verify \
  --decision route-decision.json \
  --receipt resource-use-receipt.json \
  --mode enforce
```

El resolver no ejecuta los recursos seleccionados. Codex los invoca mediante la
superficie nativa correspondiente y anota qué utilizó. Instalar un plugin,
habilitar MCP, autenticar o transmitir datos sigue necesitando autorización.

## Coste

Presupuesto:

| Tier | Recomendados | Workers | Context units |
|---|---:|---:|---:|
| T0 | 0 | 0 | 1 |
| T1 | 1 | 0 | 4 |
| T2 | 2 | 1 | 8 |
| T3 | 3 | 2 | 12 |

Los recursos obligatorios no se omiten por coste. Se carga progresivamente,
segmenta o bloquea. El manifiesto rehidratado es menor de 4 KiB. Estas medidas
son proxies; no se afirma ahorro real de tokens sin telemetría de plataforma.
