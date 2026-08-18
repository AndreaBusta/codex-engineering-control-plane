# Contrato SpecPack v1

Conjunto cerrado de seis artefactos que convierten un objetivo en lenguaje
natural en una unidad de ingeniería acotada, trazable y verificable.

Principio: **el modelo redacta, el plano verifica.** El Control Plane no escribe
contenido de producto. Define este contrato, enruta el perfil y comprueba la
trazabilidad. Ningún artefacto de un pack concede autoridad.

## Artefactos

| Archivo | Prefijo de ID | Responde a |
|---|---|---|
| `PRD.md` | `PRD-R-###` | Qué se construye y por qué |
| `TRD.md` | `TRD-R-###` | Cómo se construye técnicamente |
| `UX_UI.md` | `UX-S-###` | Qué ve y toca la persona usuaria |
| `APP_FLOW.md` | `FLOW-T-###` | Cómo se navega entre estados |
| `BACKEND.md` | `BE-D-###`, `BE-E-###` | Qué datos y contratos lo sostienen |
| `IMPLEMENTATION_PLAN.md` | `PLAN-P-###` | En qué orden se entrega |

## Regla de identificadores

```text
^(PRD|TRD|UX|FLOW|BE|PLAN)-[A-Z]-\d{3}$
```

Únicos dentro del pack. Estables una vez publicados. Nunca reutilizados: un
requisito retirado conserva su ID marcado como retirado.

## Grafo de trazabilidad

```text
PRD ──► TRD ──► PLAN
 │       │
 ├──► UX ──► FLOW
 │             │
 └─────────────┴──► BACKEND
```

Reglas que deben cerrar antes de sellar:

- toda referencia apunta a un ID que existe;
- todo `PRD-R-*` es referenciado por al menos un `TRD-R-*` o un `UX-S-*`;
- toda fase `PLAN-P-*` referencia al menos un `TRD-R-*`;
- toda transición de flujo referencia pantallas declaradas en `UX_UI.md`;
- todo `BE-E-*` es consumido por un paso de flujo o está marcado `internal`;
- cero marcadores sin resolver;
- el tier declarado coincide con el que calcula el router.

## Proporcionalidad

| Tier | Pack exigido |
|---|---|
| `T0` | Ninguno |
| `T1` | Mínimo: `PRD.md` e `IMPLEMENTATION_PLAN.md` |
| `T2` | Completo: los seis artefactos |
| `T3` | Completo, más las secciones adicionales del perfil |

Exigir seis artefactos para una tarea pequeña mata la capacidad por fricción.

## Secciones adicionales por perfil

| Perfil | Secciones exigidas |
|---|---|
| `ios` | Capacidades y entitlements, matriz de dispositivos y versiones mínimas, permisos y textos de uso, distribución |
| `android` | Permisos y niveles de API, compatibilidad de pantallas, firma y canales |
| `web_pwa` | Estrategia offline y de caché, presupuesto de rendimiento, accesibilidad, navegadores |
| `saas_backend` | Modelo de datos y migraciones, autenticación y autorización, multi-tenencia, idempotencia, límites de tasa |
| `ai_text_pipeline` | Contrato de evaluación, entradas no confiables, coste y latencia por operación, degradación del proveedor |
| `generic` | Solo el núcleo común |

Un repositorio híbrido acumula las secciones de todos sus perfiles.

## Marcadores

Durante la redacción se permite `UNKNOWN` con una nota de qué falta observar.
Un pack con marcadores es utilizable; simplemente no es sellable. Nunca
sustituir un `UNKNOWN` por una suposición redactada como hecho.

## Autoridad

Todo artefacto cierra con `authorizes=false`. Un pack no autoriza commit, push,
Pull Request, merge, deploy, release, instalación ni adopción. Ningún artefacto
afirma como realizado un efecto externo que no haya sido observado de forma
independiente.
