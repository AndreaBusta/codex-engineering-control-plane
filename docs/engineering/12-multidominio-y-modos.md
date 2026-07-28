# Operación multidominio y recomendación de modos

## Principio

El control plane no es “para iOS con excepciones”. La base profesional es
independiente del stack:

```text
encuadre → riesgo → recursos → rama/worktree → implementación
→ verificación → documentación → PR/merge → evidencia externa
```

Sobre esa base se cargan perfiles de calidad por evidencia. No detectar un
stack produce `generic`, nunca `ios`.

## Detector

El inventario recorre como máximo cinco niveles y 20.000 entradas, no sigue
symlinks y omite `.git`, dependencias, builds y worktrees. No lee código ni el
contenido de manifests para clasificar.

| Perfil | Evidencia mínima |
|---|---|
| `ios` | `.xcodeproj` o `.xcworkspace` |
| `android` | Gradle + `AndroidManifest.xml` |
| `web_pwa` | framework web, manifest o service worker |
| `saas_backend` | API/server/migrations/prisma + toolchain |
| `ai_text_pipeline` | dos familias entre prompts/evals/pipelines/providers |
| `generic` | sin evidencia suficiente |
| `hybrid` | dos o más perfiles demostrados |

La salida incluye `profiles`, rutas de evidencia, confianza y truncación. En
`hybrid`, se cargan todos los perfiles obligatorios aplicables; no se escoge
uno por orden.

Los perfiles técnicos se activan desde evidencia del inventario, no solo porque
un prompt diga “iOS” o “Android”. Si el dominio declarado contradice los
marcadores, `profile_mismatch` lo hace visible y el router evita cargar el
perfil incorrecto. Un escaneo que alcance el límite se marca
`bounded_scan_incomplete`; no se presenta como detección completa.

La detección no inventa comandos. Policy, manifests, lockfiles, CI y
documentación del proyecto siguen siendo la fuente para test, build y release.

## Calidad constante

T0–T3, autoridad por resultado, leases, docs, gates y receipts no cambian entre
Swift, Kotlin, TypeScript, Python, Go u otro stack. Cambian las comprobaciones:

- iOS: scheme, simulator, XCTest/Swift Testing, firma y TestFlight;
- Android: Gradle wrapper, variants, lint, instrumentación y Play;
- PWA: typecheck/build/E2E, accesibilidad, offline y service worker;
- SaaS: contratos, persistencia, migraciones, idempotencia y observabilidad;
- IA textual: prompts/schemas versionados, corpus golden, evals, procedencia,
  coste, publicación idempotente y egress;
- genérico: descubrir el toolchain real sin añadir dependencias.

Un perfil de release solo se activa si el resultado solicitado incluye release.
TestFlight nunca aparece en un proyecto Android o SaaS por defecto.

## Recomendación de interacción

`RouteDecision.interaction` avisa qué modo conviene:

| Recomendación | Cuándo |
|---|---|
| `default` | T0/T1 acotada y verificable en un flujo corto |
| `plan` | decisiones/pasos, T2/T3 incierta, arquitectura o multifrente |
| `goal` | resultado claro, largo y con varios hitos |
| `plan_then_goal` | trabajo largo cuyo resultado aún necesita diseño |

Codex muestra códigos de razón, acción sugerida y confianza tanto en el JSON
como en la salida humana de `control-plane route`. No cambia el modo
automáticamente. La documentación oficial de Codex indica que
[`/plan`](https://learn.chatgpt.com/docs/app/features) sirve para investigar y
proponer antes de editar, mientras que
[`/goal`](https://learn.chatgpt.com/docs/long-running-work) fija un resultado
persistente. Si el objetivo aún no está claro, se usa `/plan` y después
`/goal`.

Goal mode no amplía sandbox, permisos ni autoridad. Commit, push, PR, merge y
release siguen teniendo límites independientes.

## Ejemplos

### Corrección Android localizada

```text
profile=android
tier=T1
interaction=default
required=perfil Android
```

### Nueva PWA con auth y backend

```text
profile=hybrid(web_pwa,saas_backend)
tier=T3
interaction=plan
required=perfiles PWA + SaaS + verified-workflow
docs=plan + ADR + threat model + rollback
```

### Pipeline de textos con IA de varias fases

```text
profile=ai_text_pipeline
signals=long_running,multiple_milestones
interaction=goal
quality=golden corpus + schema + evals + idempotencia
```

### Iniciativa ambigua de migración multiplataforma

```text
profile=hybrid
tier=T3
interaction=plan_then_goal
```

## Límites

- La detección por marcadores puede quedarse corta en estructuras atípicas.
- La inferencia del agente puede añadir dominios, pero debe declarar evidencia.
- Un perfil project-local canónico puede sustituir estos perfiles genéricos.
- Ninguna cifra de ahorro de tokens se afirma sin telemetría real.
