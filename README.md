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

La verdad de versiones es explícita:

- `2.1.1` es la última release oficial;
- `3.0.0` fue un candidato de plugin no publicado como release de producto;
- `3.1.0-core.1` es un candidato prerelease local, no instalado ni adoptado.

Core conserva policy, routing, observaciones Git cerradas, guards, task ownership,
leases generacionales, verificación proporcional, recuperación exacta de
instalaciones existentes y checkpoints no autorizantes. Acepta solo `answer` y
`local_change`. Su máximo estado actual es
`GREEN_LOCAL / PENDING_STABLE_ADOPTION`, con `self_certified=false` y
`authorizes=false`.

La superficie Advanced está en cuarentena estructural. Los documentos v2.3/v2.4
se conservan como historia y no gobiernan el runtime actual. Consulta el
[índice canónico](docs/engineering/00-canonical-index.md) antes de usar un
runbook antiguo.

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

El runtime Core no refresca ni muta remotes. Un preflight con una transición que
dependa del remote queda `UNKNOWN` o bloqueado y exige observación host separada.
Ni caché local ni prosa sustituyen evidencia del proveedor.

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
scripts/control-plane preflight --mode write --offline
```

El modo `write` local devuelve exit code 1 si el gate falla. El modo `read`
conserva la capacidad de investigar un estado dirty o detached. Core no ofrece
preflight de release ni presenta una observación remota como localmente probada.

### Seleccionar recursos

```bash
scripts/control-plane route \
  --task templates/TASK_ENVELOPE.json \
  --mode audit \
  --json
```

El router selecciona; no ejecuta ni autoriza. Consulta
[Enrutamiento automático](docs/engineering/10-resource-routing.md).

### Task Core y recuperación

```bash
scripts/control-plane task status --task-id TASK-EXAMPLE-001
scripts/control-plane adopt status --target /ruta/al/repositorio
scripts/control-plane adopt verify --target /ruta/al/repositorio
```

`answer` es facts-only y no persiste task. `local_change` usa
`CoreTaskStateV1`, una rama de feature exacta y un lease generacional ligado a
revisión, worktree, sesión, policy y scope. Un resultado `review_ready` no es
commit, push, PR ni integración.

La task queda bajo el worktree Git dir. El lease Core y su recibo de liberación
quedan bajo el Git common dir para coordinar scopes y generaciones across
worktrees; ninguno se versiona.

Los leases Core coordinan únicamente writers Core. Antes de persistir una
task, Core bloquea cualquier estado legacy observable, pero un binario legacy
del mismo usuario no consulta el namespace Core y no comparte una exclusión de
vida completa. Por tanto, no ejecutes Control Plane v2.1 y Core `local_change`
en paralelo; esa exclusión sigue siendo responsabilidad de la tarea
orquestadora mientras el candidato no esté adoptado.

`adopt rollback` conserva el parser y valida journal, bytes, backups, modos y
Git config, pero falla antes de mutar con `E_ADOPT_QUIESCENCE_UNKNOWN`: el
runtime legacy no comparte una barrera global capaz de cerrar nuevos writers.
`adopt plan/apply`, `upgrade`, `run`, `report` y `verification-run`
permanecen como parsers de compatibilidad fail-closed durante la línea 3.1 y
responden `E_CAPABILITY_QUARANTINED` sin mutación.

El contrato completo está en
[mantenimiento Core](docs/engineering/19-control-plane-core-maintenance.md).

### Diagnosticar riesgo local

```bash
scripts/control-plane risk-status --repo /ruta/al/repositorio --json

# Continuidad local de una task con cambios aún no comprometidos:
scripts/control-plane risk-status \
  --repo /ruta/al/repositorio \
  --task-id TASK-EXAMPLE-001 \
  --lease-session-id session-example-001 \
  --json
```

Exit codes: `PASS=0`, `FAIL=1`, `UNKNOWN=2`. La dimensión remota permanece
`UNKNOWN` mientras no exista evidencia externa autorizada. Una task y lease
locales exactas validan continuidad, pero `--lease-session-id` nunca concede
autoridad.

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

- [Índice canónico y documentos históricos](docs/engineering/00-canonical-index.md)
- [ADR 0006: Core y cuarentena estructural](docs/adr/0006-control-plane-core-and-quarantine.md)
- [Mantenimiento, compatibilidad y rollback Core](docs/engineering/19-control-plane-core-maintenance.md)
- [Dogfood manual de 10 tareas](docs/engineering/20-control-plane-core-dogfood.md)
- [Threat model Core](docs/security/2026-08-12-control-plane-core-threat-model.md)
- [Plan vigente 3.1 Core](docs/superpowers/plans/2026-08-12-control-plane-core-3-1.md)
- [Razonamiento, contexto y agentes](docs/engineering/03-reasoning-context-agents.md)
- [Política documental](docs/engineering/04-documentation-policy.md)
- [Configuración global de Codex](docs/engineering/08-global-codex-configuration.md)
- [Auditoría, DAFO y riesgos](docs/engineering/09-audit-dafo-and-risk-register.md)
- [Router y contratos](docs/engineering/10-resource-routing.md)
- [Multidominio y recomendación de `/plan` o `/goal`](docs/engineering/12-multidominio-y-modos.md)
- [Política de seguridad](SECURITY.md)

## Límites

El control plane no:

- instala dependencias;
- almacena credenciales;
- hace commit, push, PR, merge o release por sí solo;
- crea nuevas adopciones o upgrades;
- instala el candidato Core ni su plugin en consumidores;
- sustituye Rulesets o checks obligatorios;
- prueba el estado de TestFlight sin consultar Apple;
- calcula tokens exactos sin telemetría de plataforma;
- descubre comandos genéricos de verificación fuera del perfil cerrado;
- resuelve aclaraciones de forma durable sin interacción nativa;
- instala workflows o modifica CI/CD;
- trata un hook o plugin como frontera completa de seguridad.
