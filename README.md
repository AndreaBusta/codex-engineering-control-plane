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

v2.1 se define como un **local audit kernel**. Conserva router, registry,
inventory, lifecycle, leases, receipts y adopción transaccional, y añade:

- aclaración material pura y diagnóstica;
- Risk Sentinel triestado;
- warning y PreToolUse audit con lectura segura y smoke macOS;
- guards Git locales con policy instalada content-addressed;
- rollback exacto de archivos y `core.hooksPath`.

No activa provider GitHub nuevo, workflow de procedencia, configuración remota
ni adapter host simulado. Las APIs Git/PR heredadas siguen fail-closed hasta
recibir capacidades nativas. La evidencia remota ausente produce `UNKNOWN`,
nunca `PASS`. Los hooks permanecen `audit` y `pending_hook_trust` hasta revisión
humana.

Un cambio local o un PR abierto no se describe como integrado hasta demostrar
el squash en `origin/main`. La protección de rama, CI y los proveedores de
release siguen siendo fronteras externas y requieren sus propias evidencias.

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
scripts/control-plane adopt plan \
  --target /ruta/al/repositorio --json > adoption-plan.json
scripts/control-plane adopt apply --plan adoption-plan.json
scripts/control-plane adopt verify --target /ruta/al/repositorio
scripts/control-plane adopt rollback --target /ruta/al/repositorio
scripts/control-plane upgrade plan --target /ruta/al/repositorio
```

### Diagnosticar riesgo local

```bash
scripts/control-plane risk-status --repo /ruta/al/repositorio --json
```

Exit codes: `PASS=0`, `FAIL=1`, `UNKNOWN=2`. En v2.1 local-audit la dimensión
remota permanece `UNKNOWN` mientras no exista evidencia externa autorizada.

### Hooks, lectura segura y guards Git

```bash
scripts/control-plane safe-read --repo /ruta -- git status --short
scripts/control-plane hook-smoke \
  --repo /ruta/al/repositorio \
  --task-id TASK-VERIFY-001 \
  --json
scripts/control-plane git-guard pre-commit
scripts/control-plane git-guard pre-push
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
- [Aclaración y riesgo local-audit v2.1](docs/engineering/13-clarification-and-risk-local-audit.md)
- [ADR 0003: núcleo local-audit](docs/adr/0003-local-audit-kernel-v2-1.md)
- [Threat model](SECURITY.md)

## Límites

El control plane no:

- instala dependencias;
- almacena credenciales;
- hace commit, push, PR, merge o release por sí solo;
- sustituye Rulesets o checks obligatorios;
- prueba el estado de TestFlight sin consultar Apple;
- calcula tokens exactos sin telemetría de plataforma;
- resuelve aclaraciones de forma durable sin interacción nativa;
- instala workflows o modifica CI/CD durante la adopción local;
- trata un hook o plugin como frontera completa de seguridad.
