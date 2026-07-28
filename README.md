# Codex Engineering Control Plane

Control plane local para convertir un objetivo expresado en lenguaje natural en
un flujo de ingeniería proporcional, verificable y recuperable.

Es multidominio: detecta perfiles iOS, Android, PWA/web, SaaS/backend, flujos de
texto con IA, híbridos y genéricos. Los perfiles cambian los checks técnicos,
no rebajan los gates profesionales comunes.

No intenta reemplazar el juicio de Codex ni fingir que un script local controla
GitHub o TestFlight. Separa:

- decisiones de alcance y riesgo;
- policy versionada;
- hechos deterministas de Git;
- evidencia externa.

## Estado de esta entrega

La v1 fue fusionada mediante PR #1. La v2 parte del squash commit
`5476fa7d6a40773ef478f9c090154b78195a28af` demostrado en `origin/main` y
añade router, registry, inventory, lifecycle, leases, receipts, hooks audit,
adopción reversible y assurance. Está versionada en el repositorio privado
[`AndreaBusta/codex-engineering-control-plane`](https://github.com/AndreaBusta/codex-engineering-control-plane).

La candidata v2 se desarrolla en `codex/resource-router-v2`. Un cambio local o
un PR abierto no se describe como integrado hasta demostrar el merge remoto.
Los hooks permanecen `pending_hook_trust` y macOS se ejecuta manualmente antes
de confiar sus hashes.

GitHub ha rechazado Rulesets y protección clásica de rama para este repositorio
privado con el plan actual. Se permite únicamente squash merge y se borrará la
rama remota después de una futura integración. Hasta disponer de protección
remota, el preflight, el Draft PR y CI reducen riesgo, pero no impiden por sí
solos un push directo realizado fuera del proceso.

## Inicio rápido

```bash
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane inventory --json
scripts/control-plane doctor
scripts/control-plane preflight --mode read --offline
bash tests/run.sh
```

Con remote y referencias actualizadas:

```bash
scripts/control-plane preflight --mode write --refresh
scripts/control-plane preflight --mode release --refresh
```

El modo por defecto no contacta el remote y usa referencias almacenadas
localmente. `--refresh` es explícito: contacta el remote y actualiza únicamente
su referencia base antes de evaluar. `--offline` expresa de forma redundante el
modo local cuando conviene dejar clara esa limitación. Una release o integración
requiere evidencia remota actual, no solo caché.

## Comandos

### Validar policy

```bash
scripts/control-plane policy-check \
  --policy .codex/project-policy.toml \
  --json
```

### Diagnosticar requisitos

```bash
scripts/control-plane doctor --json
```

### Comprobar Git

```bash
scripts/control-plane preflight --mode read --offline
scripts/control-plane preflight --mode write --refresh
scripts/control-plane preflight --mode release --refresh
```

Los modos `write` y `release` devuelven exit code 1 si el gate falla. El modo
`read` conserva la capacidad de investigar un estado dirty o detached.

### Seleccionar recursos

```bash
scripts/control-plane route \
  --task templates/TASK_ENVELOPE.json \
  --mode audit \
  --json
```

El router selecciona; no ejecuta ni autoriza. Consulta
[Enrutamiento automático](docs/engineering/10-resource-routing.md).

### Lifecycle y adopción

```bash
scripts/control-plane task status --task-id TASK-EXAMPLE-001
scripts/control-plane adopt plan --target /ruta/al/repositorio
scripts/control-plane upgrade plan --target /ruta/al/repositorio
```

## Dónde leer

- [Diseño](docs/superpowers/specs/2026-07-28-codex-engineering-control-plane-design.md)
- [Modelo operativo](docs/engineering/01-operating-model.md)
- [Git, PR y merge](docs/engineering/02-git-pr-merge.md)
- [Razonamiento, contexto y agentes](docs/engineering/03-reasoning-context-agents.md)
- [Política documental](docs/engineering/04-documentation-policy.md)
- [Release y observación](docs/engineering/05-release-and-observation.md)
- [Recuperación](docs/engineering/06-recovery.md)
- [Adopción](docs/engineering/07-adoption.md)
- [Configuración global de Codex](docs/engineering/08-global-codex-configuration.md)
- [Auditoría, DAFO y riesgos](docs/engineering/09-audit-dafo-and-risk-register.md)
- [Router y contratos](docs/engineering/10-resource-routing.md)
- [Lifecycle, hooks y adopción](docs/engineering/11-lifecycle-hooks-adoption.md)
- [Multidominio y recomendación de `/plan` o `/goal`](docs/engineering/12-multidominio-y-modos.md)
- [Threat model](SECURITY.md)

## Límites

El control plane no:

- instala dependencias;
- almacena credenciales;
- hace commit, push, PR, merge o release por sí solo;
- sustituye Rulesets o checks obligatorios;
- prueba el estado de TestFlight sin consultar Apple;
- calcula tokens exactos sin telemetría de plataforma;
- trata un hook o plugin como frontera completa de seguridad.
