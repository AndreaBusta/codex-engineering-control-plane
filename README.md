# Codex Engineering Control Plane

Control plane local para convertir un objetivo expresado en lenguaje natural en
un flujo de ingeniería proporcional, verificable y recuperable.

No intenta reemplazar el juicio de Codex ni fingir que un script local controla
GitHub o TestFlight. Separa:

- decisiones de alcance y riesgo;
- policy versionada;
- hechos deterministas de Git;
- evidencia externa.

## Estado de esta entrega

La v1 contiene policy, CLI, tests herméticos, runbooks y plantillas. Este
repositorio todavía no tiene commit inicial ni remote; por tanto, no puede
demostrar integración remota ni crear un worktree basado en un commit real.

## Inicio rápido

```bash
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane doctor
scripts/control-plane preflight --mode read --offline
bash tests/run.sh
```

Cuando el repositorio tenga remote y referencias actualizadas:

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

## Límites

El control plane no:

- instala dependencias;
- almacena credenciales;
- hace commit, push, PR, merge o release por sí solo;
- sustituye Rulesets o checks obligatorios;
- prueba el estado de TestFlight sin consultar Apple;
- calcula tokens exactos sin telemetría de plataforma.
