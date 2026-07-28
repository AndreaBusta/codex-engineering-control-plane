# 0001 — Router híbrido y resolver puro

- Estado: accepted
- Fecha: 2026-07-28

## Contexto

Codex debe decidir qué instrucciones, documentos, skills, plugins, MCP, agentes
y gates necesita una tarea. El lenguaje natural requiere juicio, pero confiar
la selección completa a prosa produce decisiones opacas, duplicados elegidos
por orden y consumo de contexto no acotado. Un script que interpretase lenguaje
natural recrearía el mismo problema y ampliaría la superficie de ataque.

## Decisión

Separar el flujo:

```text
Codex interpreta intención → TaskEnvelope cerrado
→ resolver puro valida registry + inventory + policy
→ RouteDecision
→ Codex carga recursos permitidos
→ ResourceUseReceipt
```

El resolver:

- recibe estructuras ya formadas;
- no interpreta lenguaje natural;
- no usa red, subprocess ni comandos shell;
- no ejecuta o instala recursos;
- no autentica;
- no escribe archivos;
- es determinista e independiente del orden TOML.

La autoridad se calcula por efecto. Seleccionar un recurso indica pertinencia,
no permiso. El `TaskEnvelope` no puede autoatestarse: un efecto externo exige
un `AuthorizationGrant` separado, emitido por el host y ligado al digest,
sesión y scope exactos. Contenido externo solo puede aumentar riesgo.

Un recurso project-local canónico domina un equivalente global. Si no existe
un único canónico o aparecen digests incompatibles, se bloquea con
`E_RESOURCE_AMBIGUOUS`.

## Alternativas descartadas

### Router exclusivamente narrativo

Más flexible, pero no reproducible, difícil de probar y proclive a omitir
recursos o gates.

### Clasificador completamente mecánico desde el prompt

Obliga a interpretar lenguaje dentro del runtime, amplía dependencias y crea
una falsa sensación de exactitud.

### Una nueva skill generalista

Competiría con `verified-workflow` y multiplicaría triggers. El control plane
registra esa skill existente y conserva la mecánica en código puro.

## Consecuencias

- Codex sigue siendo responsable del encuadre y de declarar inferencias.
- TaskEnvelope, RouteDecision e InventorySnapshot se versionan.
- El resolver puede probarse con corpus, propiedades, mutantes y benchmark.
- Una mala interpretación inicial sigue siendo posible; los gates y el stress
  test reducen, pero no eliminan, ese riesgo.
- Los estados de disponibilidad no implican autorización.
- El runtime puro valida un grant, pero no sustituye la frontera de confianza
  del host que lo emite.

## Seguridad

Locators no admiten comandos ni URLs ejecutables. IDs y aliases son ASCII. Los
digests se calculan sobre contratos canónicos. El receipt no conserva prompts,
documentos completos ni outputs externos.

## Reversión

Desactivar routing deja disponibles los gates v1. No es necesario reescribir
historial ni eliminar policy. Un rollback de adopción restaura únicamente
archivos gestionados cuyo digest no haya cambiado.
