# Diseño Control Plane 3.2 — SpecPack gobernado

Fecha: 2026-08-18. Estado: `design`. `authorizes=false`.

Documento único de diseño con los seis artefactos solicitados: PRD, TRD, UX/UI,
flujo de la app, backend y —en documento aparte— plan de implementación. Cierra
con el análisis de la meta-capacidad: qué tan interesante es que el propio
Control Plane gobierne estos seis artefactos sobre desarrollos de apps.

Este documento diseña. No implementa, no instala, no adopta y no autoriza
ninguna transición externa.

---

## 0. Tesis

El Control Plane gobierna hoy la **ejecución**: enruta recursos, impone gates,
acota autoridad y produce evidencia. Su entrada es un `TaskEnvelope` que alguien
ya redactó.

El fallo más caro del desarrollo de apps ocurre **antes** de ese punto: un
objetivo subespecificado que se convierte en código antes de convertirse en
decisión. Los seis artefactos del encargo son exactamente esa pieza ausente.

La tentación es hacer que el Control Plane los **escriba**. Es la decisión
equivocada y contradice la arquitectura vigente: «el router selecciona; no
ejecuta ni autoriza».

Principio rector de este diseño:

> **El modelo redacta. El plano verifica.**

El Control Plane no genera prosa. Define el contrato del artefacto, enruta el
perfil correcto, comprueba de forma determinista la trazabilidad y el cierre, y
sella digests no autorizantes. Codex escribe; el plano dice si lo escrito es
completo, trazable y coherente con el riesgo declarado.

---

## 1. PRD — Product Requirements Document

### 1.1 Problema

Un objetivo en lenguaje natural llega al plano sin estructura. Hoy ocurre una de
tres cosas, todas malas:

1. se enruta un envelope pobre y los gates se aplican a un alcance equivocado;
2. se produce documentación ad hoc, distinta en cada hilo, no comparable ni
   verificable;
3. se implementa directamente y la decisión de producto queda implícita en el
   diff.

No existe artefacto intermedio que sea a la vez legible por una persona,
consumible por un agente y comprobable por una máquina.

### 1.2 Usuarios y trabajos

| Usuario | Trabajo que necesita resolver |
|---|---|
| Operador (desarrollador en solitario que orquesta Codex) | Convertir una idea en una unidad de ingeniería acotada sin escribir cinco documentos a mano |
| Codex ejecutor | Recibir alcance, contrato y criterios de aceptación sin reconstruirlos por conversación |
| Revisor futuro (incluido el propio operador semanas después) | Saber por qué existe cada requisito y qué lo cubre |
| Tarea de relevo tras pérdida de contexto | Reanudar sin releer el hilo original |

### 1.3 Requisitos

Cada requisito tiene ID estable. Los IDs son la base de la trazabilidad y no se
reutilizan.

| ID | Requisito | Prioridad |
|---|---|---|
| `PRD-R-001` | El sistema define un contrato cerrado de seis artefactos: PRD, TRD, UX/UI, flujo, backend y plan | must |
| `PRD-R-002` | Cada artefacto declara requisitos con identificadores estables y únicos | must |
| `PRD-R-003` | El sistema comprueba de forma determinista que la trazabilidad entre artefactos cierra en ambos sentidos | must |
| `PRD-R-004` | El sistema detecta requisitos huérfanos: sin cobertura aguas abajo y sin origen aguas arriba | must |
| `PRD-R-005` | El sistema exige que no queden marcadores sin resolver antes de sellar un pack | must |
| `PRD-R-006` | Las secciones obligatorias dependen del perfil detectado del proyecto destino | must |
| `PRD-R-007` | El tier declarado en el pack debe coincidir con el que el router calcula desde el envelope | must |
| `PRD-R-008` | El sistema sella el pack con digests por artefacto en un recibo no autorizante | must |
| `PRD-R-009` | Ningún artefacto del pack concede autoridad para efectos externos | must |
| `PRD-R-010` | El sistema nunca redacta contenido de producto: solo valida el redactado por el modelo | must |
| `PRD-R-011` | El operador puede trabajar con el pack en un repositorio destino distinto del propio plano | should |
| `PRD-R-012` | Un pack incompleto es utilizable: la validación informa qué falta sin bloquear el trabajo local seguro | should |
| `PRD-R-013` | La salida de validación prioriza los enlaces rotos antes que el inventario completo | should |

### 1.4 No objetivos

- No generar prosa de producto, copy, wireframes ni diagramas.
- No sustituir el juicio del operador sobre qué construir.
- No introducir daemon, planificador, telemetría ni almacén de autoridad.
- No convertirse en gestor de proyecto, backlog ni sistema de tickets.
- No prometer que un pack trazable sea un pack correcto.

### 1.5 Métricas de éxito

| Métrica | Definición | Umbral |
|---|---|---|
| Cierre de trazabilidad | Requisitos con cobertura aguas abajo / requisitos totales | `= 1.0` para sellar |
| Huérfanos | Elementos de plan sin requisito de origen | `= 0` para sellar |
| Marcadores sin resolver | Ocurrencias de `UNKNOWN` o `TBD` en artefactos sellados | `= 0` |
| Deriva de tier | Packs cuyo tier declarado difiere del calculado | `= 0` |
| Coste de reanudación | Preguntas necesarias para retomar un pack sellado tras pérdida de contexto | `≤ 1` |

### 1.6 Riesgos de producto

| Riesgo | Efecto | Mitigación |
|---|---|---|
| Fábrica de documentos | Seis plantillas rellenadas por inercia, sin decisión real | Validar trazabilidad, no presencia de encabezados; huérfanos como fallo |
| Falso rigor | Un pack que cierra pero describe el producto equivocado | Declarar explícitamente que la validación es de coherencia, nunca de acierto |
| Inflación del runtime | Ampliar superficie mientras el candidato sigue pendiente de adopción estable | Entrega por fases: plantillas y skill primero, validador solo tras adopción estable |
| Fricción excesiva | El operador abandona el pack por coste | Perfil mínimo obligatorio en T0/T1; pack completo solo en T2/T3 |

---

## 2. TRD — Technical Requirements Document

### 2.1 Arquitectura

Tres capas, una sola de ellas con código nuevo.

```text
┌─ Capa de contrato ─────────────────────────────────────────┐
│ templates/spec-pack/*.md   plantillas de los 6 artefactos  │
│ spec-pack.manifest.json    esquema del pack y su índice    │
│ sin código                                                  │
└─────────────────────────────────────────────────────────────┘
┌─ Capa de redacción ────────────────────────────────────────┐
│ skills/control-plane-specpack/SKILL.md                     │
│ el modelo redacta dentro del contrato; sin código          │
└─────────────────────────────────────────────────────────────┘
┌─ Capa de verificación ─────────────────────────────────────┐
│ control_plane/spec_pack.py   validador determinista        │
│ CLI: specpack init | check | seal                          │
│ único componente con runtime nuevo                          │
└─────────────────────────────────────────────────────────────┘
```

La separación es deliberada: las dos primeras capas aportan la mayor parte del
valor con cero riesgo de runtime, y pueden entregarse mientras el candidato
sigue en `PENDING_STABLE_ADOPTION`.

### 2.2 Requisitos técnicos

| ID | Requisito | Cubre |
|---|---|---|
| `TRD-R-001` | Un módulo único `control_plane/spec_pack.py`, sin dependencias nuevas | `PRD-R-001` |
| `TRD-R-002` | Esquema `SpecPackManifestV1` versionado con `schema_version` | `PRD-R-001`, `PRD-R-002` |
| `TRD-R-003` | Parser de IDs con expresión cerrada `^(PRD|TRD|UX|FLOW|BE|PLAN)-[A-Z]-\d{3}$` | `PRD-R-002` |
| `TRD-R-004` | Grafo de trazabilidad dirigido y comprobación de alcanzabilidad en ambos sentidos | `PRD-R-003`, `PRD-R-004` |
| `TRD-R-005` | Detección de marcadores sin resolver por expresión cerrada, con recuento y ubicación | `PRD-R-005` |
| `TRD-R-006` | Secciones obligatorias parametrizadas por los perfiles ya existentes | `PRD-R-006` |
| `TRD-R-007` | Comparación del tier declarado contra el que devuelve el router para el mismo envelope | `PRD-R-007` |
| `TRD-R-008` | `SpecPackReceiptV1` con digest sha256 por artefacto y `authorizes=false` | `PRD-R-008`, `PRD-R-009` |
| `TRD-R-009` | Lectura acotada de archivos con límite por archivo y total, reutilizando el patrón de lectura segura ya presente | `PRD-R-010` |
| `TRD-R-010` | Ningún camino de código emite, escribe ni completa contenido de artefacto | `PRD-R-010` |
| `TRD-R-011` | El repositorio destino se pasa por parámetro y nunca se muta | `PRD-R-011` |
| `TRD-R-012` | Códigos de salida `PASS=0`, `FAIL=1`, `UNKNOWN=2` coherentes con el resto del CLI | `PRD-R-012` |
| `TRD-R-013` | Salida ordenada por severidad: enlaces rotos, huérfanos, marcadores, inventario | `PRD-R-013` |

### 2.3 Esquemas

`SpecPackManifestV1`, versionado en el repositorio destino:

```json
{
  "schema_version": 1,
  "kind": "SpecPackManifestV1",
  "pack_id": "SPEC-<slug>-<nnn>",
  "title": "",
  "target_repository": "",
  "profiles": ["generic"],
  "tier": "T2",
  "envelope": "docs/spec-packs/<pack_id>/envelope.json",
  "artifacts": {
    "prd": "docs/spec-packs/<pack_id>/PRD.md",
    "trd": "docs/spec-packs/<pack_id>/TRD.md",
    "ux": "docs/spec-packs/<pack_id>/UX_UI.md",
    "flow": "docs/spec-packs/<pack_id>/APP_FLOW.md",
    "backend": "docs/spec-packs/<pack_id>/BACKEND.md",
    "plan": "docs/spec-packs/<pack_id>/IMPLEMENTATION_PLAN.md"
  },
  "status": "draft",
  "authorizes": false
}
```

`SpecPackReceiptV1`, no versionado, bajo el Git dir del worktree igual que el
resto de estado local:

```json
{
  "schema_version": 1,
  "kind": "SpecPackReceiptV1",
  "pack_id": "",
  "result": "PASS",
  "manifest_digest": "sha256:",
  "artifact_digests": {"prd": "sha256:", "trd": "sha256:"},
  "traceability": {"closed": true, "orphans": [], "broken_links": []},
  "unresolved_markers": 0,
  "tier_declared": "T2",
  "tier_computed": "T2",
  "profiles": ["generic"],
  "observed_at": "",
  "authorizes": false
}
```

### 2.4 Reglas de trazabilidad

El grafo de cobertura es fijo y direccional:

```text
PRD ──► TRD ──► PLAN
 │       │
 ├──► UX ──► FLOW
 │             │
 └─────────────┴──► BACKEND
```

| Regla | Comprobación | Código de error |
|---|---|---|
| Referencia existente | Todo ID referenciado existe en su artefacto origen | `E_SPECPACK_TRACE_BROKEN` |
| Cobertura aguas abajo | Todo `PRD-R-*` es referenciado por al menos un `TRD-R-*` o un `UX-S-*` | `E_SPECPACK_UNCOVERED_REQUIREMENT` |
| Origen aguas arriba | Toda fase `PLAN-P-*` referencia al menos un `TRD-R-*` | `E_SPECPACK_ORPHAN_PHASE` |
| Pantallas del flujo | Toda transición de `APP_FLOW` referencia pantallas declaradas en `UX_UI` | `E_SPECPACK_TRACE_BROKEN` |
| Endpoints consumidos | Todo `BE-E-*` es consumido por un paso de flujo o marcado `internal` | `E_SPECPACK_UNCONSUMED_ENDPOINT` |
| Unicidad | Ningún ID aparece definido dos veces | `E_SPECPACK_DUPLICATE_ID` |
| Cierre | Cero marcadores sin resolver al sellar | `E_SPECPACK_UNRESOLVED` |
| Coherencia de riesgo | Tier declarado igual al calculado por el router | `E_SPECPACK_TIER_DRIFT` |
| No autoridad | Ningún artefacto afirma un efecto externo como realizado | `E_SPECPACK_AUTHORITY_CLAIM` |
| Perfil completo | Secciones obligatorias del perfil presentes y no vacías | `E_SPECPACK_PROFILE_INCOMPLETE` |

### 2.5 Secciones obligatorias por perfil

Los perfiles reutilizan los ya implementados: `ios`, `android`, `web_pwa`,
`saas_backend`, `ai_text_pipeline`, `generic`. Un repositorio híbrido acumula
las secciones de todos sus perfiles.

| Perfil | Secciones adicionales exigidas |
|---|---|
| `ios` | Capacidades y entitlements, matriz de dispositivos y versiones mínimas, permisos y textos de uso, estrategia de distribución |
| `android` | Permisos y niveles de API, estrategia de compatibilidad de pantallas, firma y canales de distribución |
| `web_pwa` | Estrategia offline y de caché, presupuesto de rendimiento, accesibilidad, compatibilidad de navegadores |
| `saas_backend` | Modelo de datos y migraciones, autenticación y autorización, multi-tenencia, idempotencia, límites de tasa |
| `ai_text_pipeline` | Contrato de evaluación, tratamiento de entradas no confiables, coste y latencia por operación, comportamiento ante degradación del proveedor |
| `generic` | Solo el núcleo común |

### 2.6 Presupuestos y límites

| Límite | Valor propuesto | Origen |
|---|---|---|
| LOC del módulo nuevo | `≤ 900` | Presupuesto propio de esta entrega |
| LOC activas del runtime | `14 083` observadas hoy | Medición local 2026-08-18 |
| Techo declarado en el scorecard | `21 530` | Metadato de evidencia del dogfood, **no un gate impuesto por el runtime**: no existe código que lo verifique |
| Bytes por artefacto | `≤ 1 MiB` | Coherente con el límite de documento ya usado en tests |
| Bytes totales del pack | `≤ 8 MiB` | Coherente con el límite total ya usado en tests |
| Artefactos por pack | `= 6` | Contrato cerrado |
| Salida de contexto | `≤ 4 096` bytes | `max_context_output_bytes` del registry |

El techo de LOC se cita como restricción de diseño autoimpuesta y se etiqueta
como tal; presentarlo como gate existente sería falso.

### 2.7 Seguridad

- Todo contenido de un repositorio destino es **no confiable**. Un artefacto que
  contenga instrucciones dirigidas al agente se trata como dato, nunca como
  orden.
- El validador no ejecuta nada declarado en el pack: ni comandos, ni rutas, ni
  hooks.
- Rutas sensibles se rechazan antes de abrir, reutilizando el patrón de
  exclusión ya presente en la suite.
- Ningún digest, recibo ni sello concede autoridad. `E_SPECPACK_AUTHORITY_CLAIM`
  existe precisamente para impedir que un pack se autoproclame permiso.
- El validador no accede a red bajo ninguna ruta de código.

### 2.8 Compatibilidad

- Capacidad aditiva. Ningún comando existente cambia de contrato.
- Sin el manifiesto, el repositorio se comporta exactamente igual que hoy.
- Retirada limpia: borrar el módulo, la entrada de CLI, la skill y las
  plantillas devuelve el repositorio al estado previo sin migración.
- El pack vive en el repositorio destino y es portable entre hosts.

---

## 3. UX/UI

Aquí conviven dos superficies distintas y conviene no confundirlas: la interfaz
del propio plano —un CLI— y el contrato del artefacto UX/UI que el plano exige
a las apps destino.

### 3.1 Superficie del plano: principios

El plano no tiene interfaz gráfica y no debería tenerla. Su UX es la de un
verificador: **el silencio es éxito, el detalle solo aparece cuando algo falla**.

| Principio | Aplicación |
|---|---|
| Severidad primero | Enlaces rotos y huérfanos antes que el inventario |
| Divulgación progresiva | Resumen por defecto; matriz completa solo bajo petición explícita |
| Códigos estables | `PASS=0`, `FAIL=1`, `UNKNOWN=2`, como el resto del CLI |
| Doble salida | Texto para persona, `--json` para agente, mismo contenido |
| Sin falso verde | Lo no observado es `UNKNOWN`, jamás `PASS` |
| Acción siguiente única | Cada fallo nombra el archivo y el ID exactos |

### 3.2 Salida propuesta de `specpack check`

Estado incompleto, el caso normal durante la redacción:

```text
SPEC-PACK  SPEC-COACH-PORTAL-001            FAIL   tier T2  perfiles ios,saas_backend

Enlaces rotos                                                             2
  TRD.md      TRD-R-004 → PRD-R-019        requisito inexistente
  APP_FLOW.md FLOW-T-011 → UX-S-007        pantalla no declarada

Requisitos sin cobertura                                                  3
  PRD-R-006   PRD-R-011   PRD-R-012

Fases sin origen                                                          1
  PLAN-P-005  no referencia ningún TRD-R-*

Marcadores sin resolver                                                   4
  BACKEND.md  líneas 41, 58     UX_UI.md  líneas 12, 77

Secciones de perfil ausentes                                              1
  ios         permisos y textos de uso

Cobertura 21/24    Siguiente acción: resolver TRD-R-004 en TRD.md
```

Estado sellable:

```text
SPEC-PACK  SPEC-COACH-PORTAL-001            PASS   tier T2  perfiles ios,saas_backend
Cobertura 24/24    huérfanos 0    marcadores 0    tier coherente
Sellado disponible: control-plane specpack seal --pack SPEC-COACH-PORTAL-001
```

El bloque de fallo cabe en pantalla y no obliga a desplazarse. Ese es el
requisito de UX real: un verificador que exige leer tres pantallas de salida se
deja de usar.

### 3.3 Contrato del artefacto UX/UI para apps destino

Para que UX/UI sea comprobable, deja de ser prosa libre y pasa a tener
estructura mínima:

| Elemento | Formato | Comprobación |
|---|---|---|
| Inventario de pantallas | `UX-S-001` … con nombre y propósito | IDs únicos y bien formados |
| Requisito de origen | Cada pantalla referencia ≥1 `PRD-R-*` | Referencia existente |
| Estados por pantalla | Vacío, carga, error, sin conexión, sin permiso | Presencia de los cinco |
| Entradas y validación | Campos, reglas, mensajes | Sección no vacía |
| Accesibilidad | Contraste, tamaño de objetivo táctil, lectores de pantalla, tipografía dinámica | Sección no vacía |
| Copy | Referencia al inventario de textos | Sección no vacía |

La exigencia de los cinco estados por pantalla es deliberada: son exactamente
los que se olvidan y los que después generan retrabajo.

---

## 4. Flujo de la app

También dos flujos: el del operador a través del plano y el contrato del
artefacto de flujo para las apps destino.

### 4.1 Flujo del operador

```text
  objetivo en lenguaje natural
            │
            ▼
  ┌───────────────────┐   El plano normaliza. Sin envelope no hay tier
  │ TaskEnvelope      │   ni perfil, y sin ellos el pack no es comprobable.
  └───────────────────┘
            │
            ▼
  ┌───────────────────┐   route → tier T0..T3, perfiles, recursos, gates
  │ decisión de ruta  │
  └───────────────────┘
            │
      ┌─────┴─────┐
      │           │
   T0 / T1     T2 / T3
      │           │
      ▼           ▼
  pack mínimo   pack completo
  PRD + PLAN    los seis artefactos
      │           │
      └─────┬─────┘
            ▼
  ┌───────────────────┐   specpack init  →  esqueleto con IDs y secciones
  │ esqueleto         │   del perfil detectado. El plano no rellena nada.
  └───────────────────┘
            │
            ▼
  ┌───────────────────┐   Codex redacta dentro del contrato.
  │ redacción         │   Bucle con specpack check tras cada tanda.
  └───────────────────┘
            │
            ▼
  ┌───────────────────┐   FAIL → vuelve a redacción con la lista de fallos
  │ specpack check    │   PASS → habilita el sellado
  └───────────────────┘
            │ PASS
            ▼
  ┌───────────────────┐   digests por artefacto, authorizes=false
  │ specpack seal     │
  └───────────────────┘
            │
            ▼
  ┌───────────────────┐   El plan sellado alimenta el ciclo de ejecución
  │ implementación    │   que ya existe: TDD, gates, evidencia.
  └───────────────────┘
```

La proporcionalidad es el punto crítico. Exigir seis artefactos para una tarea
T0 mataría la capacidad por fricción. T0 y T1 usan pack mínimo; el pack completo
se reserva a T2 y T3, que es justo donde el coste de un alcance mal definido
supera al de escribirlo.

### 4.2 Contrato del artefacto de flujo para apps destino

El flujo de una app se declara como máquina de estados explícita, no como
narración:

| Elemento | Formato | Comprobación |
|---|---|---|
| Estado inicial | Un único `UX-S-*` marcado como entrada | Existe y es único |
| Transiciones | `FLOW-T-001: UX-S-001 --evento--> UX-S-002` | Ambas pantallas declaradas |
| Guardas | Condición por transición: sesión, permiso, conectividad, estado de datos | Sección no vacía |
| Rutas de fallo | Toda transición con efecto remoto declara su rama de error | Presencia obligatoria |
| Estados terminales | Salida, cierre de sesión, error irrecuperable | Al menos uno |
| Consumo de backend | Toda transición con efecto declara el `BE-E-*` que invoca | Endpoint existente |

La última fila cierra el círculo: es lo que permite detectar endpoints que nadie
consume y transiciones que invocan lo que no existe.

---

## 5. Backend

### 5.1 Backend del plano

Conviene ser exacto: **este producto no tiene backend**. No hay servidor, no hay
base de datos, no hay servicio. Presentarlo de otro modo sería inventar
arquitectura.

Lo que sí existe es un contrato de persistencia local, y sigue la separación ya
establecida en el repositorio:

| Dato | Ubicación | Versionado | Motivo |
|---|---|---|---|
| Manifiesto y seis artefactos | `docs/spec-packs/<pack_id>/` del repositorio destino | Sí | Son decisiones de producto: deben revisarse en PR |
| `SpecPackReceiptV1` | Git dir del worktree | No | Es observación local, no decisión |
| Digests de sellado | Dentro del recibo | No | Evidencia local, no autoridad |
| Envelope del pack | Junto a los artefactos | Sí | Determina tier y perfil, debe revisarse |

Ninguna escritura ocurre fuera de esas rutas. El validador es de solo lectura
salvo el recibo, que escribe en el namespace local no versionado.

Sin red, sin credenciales, sin egress, sin proceso persistente. La ausencia de
backend es una propiedad de seguridad, no una carencia.

### 5.2 Contrato del artefacto backend para apps destino

| Elemento | Formato | Comprobación |
|---|---|---|
| Entidades | `BE-D-001` con campos, tipos, nulabilidad, claves | IDs únicos |
| Endpoints | `BE-E-001` con método, ruta, entrada, salida, errores | IDs únicos y consumo desde flujo |
| Autorización | Matriz rol × endpoint, sin celdas vacías | Cobertura completa de `BE-E-*` |
| Migraciones | Orden, reversibilidad, compatibilidad con clientes antiguos | Sección no vacía |
| Idempotencia | Por endpoint mutante: clave y ventana | Cobertura de endpoints mutantes |
| Taxonomía de errores | Código, causa, mensaje al cliente, acción de recuperación | Sección no vacía |
| Datos personales | Clasificación, retención, borrado | Sección no vacía si el perfil lo exige |
| Límites | Tasa, tamaño de carga, tiempo de espera | Sección no vacía |

La matriz rol × endpoint sin celdas vacías es la comprobación de mayor
rendimiento de todo el conjunto: obliga a decidir explícitamente quién puede
llamar a qué, que es donde se concentran los fallos de autorización.

---

## 6. Análisis: ¿qué tan interesante es esta meta-capacidad?

La pregunta del encargo era si merece la pena que el propio Control Plane
ejecute estos seis artefactos sobre desarrollos de apps. Respuesta razonada.

### 6.1 A favor

**Cubre el hueco real de la arquitectura.** El plano gobierna desde el
`TaskEnvelope` hacia adelante y no tiene nada antes. Es su frontera más débil y
la que más caro sale.

**La demanda está probada por dogfood.** Este encargo pide exactamente esos seis
artefactos para el propio plano. Un producto cuyo primer usuario ya lo necesita
para sí mismo tiene la validación más barata que existe.

**Reutiliza infraestructura ya construida.** Perfiles, tiers, router, gates,
digests, recibos no autorizantes y lectura acotada existen y funcionan. La
capacidad añade contrato y validación, no fundaciones.

**Lo que valida es mecánicamente decidible.** Trazabilidad, unicidad de IDs,
cierre de cobertura, huérfanos, marcadores sin resolver y coherencia de tier son
propiedades de grafo y de texto. No requieren juicio. Es exactamente el tipo de
trabajo que un runtime determinista hace mejor que un modelo, y lo complementario
del que hace mejor un modelo.

**Aumenta el valor de todo lo demás.** Un plan con IDs trazables convierte los
gates existentes en gates sobre alcance conocido, no sobre alcance supuesto.

### 6.2 En contra

**Riesgo de fábrica de documentos.** Seis plantillas invitan a rellenar por
inercia. Si la validación comprobara presencia de encabezados en vez de
trazabilidad, el producto sería peor que nada: daría sensación de rigor sin
aportarlo. Toda la defensa depende de validar el grafo, no el formato.

**Falso rigor.** Un pack puede cerrar perfectamente y describir el producto
equivocado. La validación es de **coherencia interna**, nunca de acierto. Esto
debe decirse en la salida del comando, no solo en la documentación.

**Momento inoportuno para tocar el runtime.** El candidato `3.1.0-core.1` está
en `GREEN_LOCAL / PENDING_STABLE_ADOPTION` con la superficie Advanced en
cuarentena. Añadir un módulo ahora amplía justo la superficie que se intenta
estabilizar, y contradice el espíritu de ADR 0006.

**Fricción.** Si el pack completo se exige siempre, el operador lo rodeará. La
proporcionalidad por tier no es un adorno: es la condición de supervivencia.

### 6.3 Alternativas evaluadas

| Alternativa | Ventaja | Inconveniente | Veredicto |
|---|---|---|---|
| **A. No hacer nada** | Cero riesgo y cero coste | Mantiene el hueco; cada hilo reinventa la estructura | Descartada |
| **B. Solo contrato y skill** (plantillas + skill, sin código) | Entrega la mayor parte del valor con cero runtime nuevo; compatible con la cuarentena; retirable borrando archivos | La trazabilidad depende de la disciplina del modelo, no está impuesta | **Fase 1** |
| **C. Contrato, skill y validador** (B + `spec_pack.py`) | Trazabilidad impuesta de forma determinista; sellado con digests | ~900 LOC nuevas; exige adopción estable previa | **Fase 2** |
| **D. Motor de generación** (el plano redacta los artefactos) | Máxima automatización aparente | Contradice «el router selecciona, no ejecuta»; duplica lo que el modelo ya hace mejor; inflación severa | Descartada |

### 6.4 Recomendación

**Sí, con secuencia estricta y condición de entrada.**

1. **Fase 1 ahora.** Plantillas y skill. Cero código, cero riesgo de runtime,
   compatible con la cuarentena vigente, utilizable de inmediato y reversible
   borrando archivos. Captura la mayor parte del valor.
2. **Fase 2 después.** El validador `spec_pack.py` solo cuando el candidato haya
   alcanzado adopción estable. Antes de eso, añadir runtime es empeorar el
   problema que el repositorio está resolviendo.
3. **Fase 3 condicionada.** Verificación cruzada de un pack contra el diff real
   —qué requisitos tocó de verdad una rama— únicamente si las fases previas se
   usan de forma sostenida. No diseñar esto todavía.

La condición de entrada a la fase 2 es explícita y comprobable: candidato
adoptado de forma estable, gate integral cerrado y al menos tres packs de fase 1
redactados en trabajo real. Sin las tres, la fase 2 no empieza.

### 6.5 Lo que este diseño se niega a prometer

- Que un pack sellado signifique un producto correcto.
- Que la validación sustituya la revisión humana.
- Que la trazabilidad reduzca el riesgo técnico de una decisión mala.
- Que la capacidad sirva sin proporcionalidad por tier.

---

## 7. Trazabilidad de este documento

Este diseño se somete a su propia regla. Cobertura de `PRD-R-*`:

| Requisito | Cubierto por |
|---|---|
| `PRD-R-001` | `TRD-R-001`, `TRD-R-002`, sección 2.1 |
| `PRD-R-002` | `TRD-R-003` |
| `PRD-R-003` | `TRD-R-004`, sección 2.4 |
| `PRD-R-004` | `TRD-R-004`, sección 2.4 |
| `PRD-R-005` | `TRD-R-005` |
| `PRD-R-006` | `TRD-R-006`, sección 2.5 |
| `PRD-R-007` | `TRD-R-007` |
| `PRD-R-008` | `TRD-R-008` |
| `PRD-R-009` | `TRD-R-008`, sección 2.7 |
| `PRD-R-010` | `TRD-R-009`, `TRD-R-010` |
| `PRD-R-011` | `TRD-R-011` |
| `PRD-R-012` | `TRD-R-012`, sección 4.1 |
| `PRD-R-013` | `TRD-R-013`, sección 3.2 |

Cobertura `13/13`. Huérfanos `0`. Marcadores sin resolver `0`.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora del diseño 3.2.
- **Para continuar:** ejecutar la fase 1 del plan de implementación asociado.
- **Mensaje exacto:** `Implementa la fase 1 de SpecPack: plantillas y skill, sin código de runtime.`
- **Estado de partida:** diseño completo, plantillas creadas, validador no implementado, candidato `3.1.0-core.1` pendiente de adopción estable.
- **No hacer todavía:** implementar el validador, instalar, adoptar externamente, commit, push, PR, merge o release.
- **Autoridad:** `authorizes=false`
