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
- `3.1.0-core.1` fue el candidato prerelease local anterior y su evidencia queda
  como historial no gobernante;
- `3.1.0-core.2` es el candidato prerelease local actual, no instalado ni adoptado.

Core conserva policy, routing, observaciones Git cerradas, guards, task ownership,
leases generacionales, verificación proporcional, recuperación exacta de
instalaciones existentes y checkpoints no autorizantes. Acepta solo `answer` y
`local_change`. Su máximo estado actual es
`GREEN_LOCAL / PENDING_STABLE_ADOPTION`, con `self_certified=false` y
`authorizes=false`. Stable Pause v1 está `IMPLEMENTED_LOCAL` dentro del runtime
exacto de 27 módulos; cada cierre exige evidencia final sobre bytes congelados.

La superficie Advanced está en cuarentena estructural. Los documentos v2.3/v2.4
se conservan como historia y no gobiernan el runtime actual. Consulta el
[índice canónico](docs/engineering/00-canonical-index.md) antes de usar un
runbook antiguo.

## Adoption enablement local

El tool separado `scripts/control-plane-adoption` y su paquete
`adoption_enablement` están implementados y verificados únicamente con
repositorios temporales propiedad del harness:

```text
adoption_tool=IMPLEMENTED_LOCAL
temporary_repository_e2e=PASS
external_consumer_adoption=PROHIBITED
canary=NOT_PREPARED
stable_adoption=NOT_DECIDED
Autopilot OFF
authorizes=false
```

No ejecutes `preview`, `apply`, `verify` o `rollback` contra un consumidor. La
presencia del entrypoint no concede permiso ni convierte un repositorio en
canary. Antes incluso de preparar un único canary desechable hace falta otro
ADR aceptado de forma independiente; la acción exacta exigiría después una
autorización nativa separada.

Los parsers Core `adopt plan`, `adopt apply`, `upgrade plan` y `upgrade apply`
siguen en cuarentena y responden `E_CAPABILITY_QUARANTINED` sin mutación. El
tool local no reactiva esa superficie ni entra en la allowlist de 27 módulos
Core. Consulta la
[especificación](docs/superpowers/specs/2026-08-13-control-plane-core-adoption-enablement-design.md)
y el [plan de implementación](docs/superpowers/plans/2026-08-13-control-plane-core-adoption-enablement.md)
para sus contratos y límites.

## Readiness para un proyecto nuevo

El repositorio incluye un
[pack de auditoría source-only](docs/engineering/23-new-project-audit-bootstrap.md)
para preparar la gobernanza de un proyecto todavía no identificado. Valida un
bundle personalizado mediante cinco comandos read-only; no copia archivos al
consumidor, no instala Control Plane y no concede adopción.

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

### Inventariar el repositorio

```bash
scripts/control-plane survey --repo /ruta/al/repositorio --json
```

Emite exclusivamente `RepositorySurveyV2`: informa clon, worktrees, ramas y
trabajo huérfano. Fija primero los OIDs de base y rama; la equivalencia de
contenido se demuestra con
`git diff --quiet <fixed-base-oid>..<fixed-branch-oid>` o comparando sus tree
OIDs, nunca contando commits. Una rama es `unpublished_unique` solo cuando su
tree difiere, conserva commits no alcanzables desde la base y no existe su ref
remota local homónima.

Las refs remotas locales pueden estar obsoletas respecto del servidor: Survey
no hace red ni constituye prueba remota. `other_clones=UNKNOWN` es permanente
porque un checkout no puede enumerar otros clones. El conteo add-only:

```bash
git diff --diff-filter=A --name-only <fixed-base-oid>..<fixed-branch-oid>
```

alimenta únicamente el campo informativo y nullable `added_paths`;
`added_paths=null` no cambia el resultado normativo.

Los estados y exits son `PASS=0`, `FAIL=1`, `UNKNOWN=2` y `WARN=3`. `FAIL`
señala al menos una rama `unpublished_unique`; `WARN` separa stashes o archivos
sin rastrear cuando no existe esa rama; evidencia normativa incompleta produce
`UNKNOWN`. Todos los resultados siguen siendo locales, read-only y
`authorizes=false`: no prueban final gate, integración, release, adopción,
instalación ni estado remoto.

`doctor` y `preflight` añaden `git_state_materialized`. Un archivo `dataless`
cambia su inodo en la primera lectura, así que un fallo de almacenamiento puede
imitar un defecto de producto; el gate de escritura ahora se detiene antes de
empezar en vez de después. El modo lectura sigue permitiendo investigar.

### Stable Pause verify-only

```bash
scripts/control-plane task checkpoint \
  --mode stable-pause \
  --task-id EXACT-TASK-ID \
  --json
```

La task exacta es obligatoria. El comando observa dos snapshots locales bajo
mutexes preexistentes con `create=false`, no crea estado `paused`, no limpia y
no muta task, lease, Git ni repositorio. Devuelve una única observación cerrada
de hasta 4096 bytes con `SAFE_PAUSE_ACTIVE`, `SAFE_PAUSE_TERMINAL`,
`UNSAFE_PAUSE` o `UNKNOWN`, `checkpoint_digest` determinista y
`authorizes=false`. Un dirty worktree o un RED preservado permanecen visibles;
no son automáticamente inseguros si el snapshot y el lifecycle son coherentes.

El procedimiento progresivo en `skills/control-plane-run/SKILL.md` carga
`skills/control-plane-run/references/stable-pause-v1.md` solo para una petición
de parada/checkpoint. Une la observación con visibilidad del host nativo antes y
después; nunca mejora un `UNSAFE_PAUSE` o `UNKNOWN`. Al resume, repite la
observación para la misma task y worktree, compara `checkpoint_digest`, explica
la deriva y vuelve a los gates ordinarios antes de escribir.

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
- [Alineación de repositorio y ramas](docs/engineering/21-repository-alignment-and-branch-decisions.md)
- [Orientación y trampas conocidas](docs/engineering/22-orientation-and-known-traps.md)
- [SpecPack 3.2: diseño](docs/superpowers/specs/2026-08-18-control-plane-3-2-specpack-design.md)
- [SpecPack 3.2: plan](docs/superpowers/plans/2026-08-18-control-plane-3-2-specpack.md)
- [Stable Pause v1: WHAT/WHY](docs/superpowers/specs/2026-08-14-control-plane-stable-pause-v1-design.md)
- [Stable Pause v1: HOW y rollback](docs/superpowers/plans/2026-08-14-control-plane-stable-pause-v1.md)
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
