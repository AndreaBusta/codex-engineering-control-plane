# Codex Engineering Control Plane v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir un control plane local, portable y verificable que convierta las reglas profesionales de trabajo con Codex en policy, documentación, plantillas y gates deterministas.

**Architecture:** El juicio del agente clasifica y descompone; `.codex/project-policy.toml` conserva las reglas no deducibles; una CLI Python 3.11 estándar observa Git y bloquea estados inseguros. Los sistemas externos continúan siendo fuentes autoritativas para GitHub, CI y TestFlight.

**Tech Stack:** Python 3.11 estándar (`tomllib`, `unittest`, `subprocess`), Git, TOML y Markdown. Sin dependencias de terceros.

---

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `.codex/project-policy.toml` | única policy ejecutable del proyecto |
| `control_plane/policy.py` | cargar y validar policy |
| `control_plane/git_state.py` | observar Git sin mutarlo |
| `control_plane/cli.py` | comandos, composición de checks y JSON |
| `tests/test_policy.py` | contratos de policy |
| `tests/test_preflight.py` | escenarios Git herméticos |
| `tests/test_cli.py` | contrato de salida |
| `tests/run.sh` | verificación reproducible |
| `docs/engineering/*` | runbooks y criterios profesionales |
| `templates/*` | artefactos operativos |
| `AGENTS.md` | reglas concisas para mantener este repositorio |

## Task 1: Formalizar diseño y frontera de autoridad

**Files:**
- Create: `docs/superpowers/specs/2026-07-28-codex-engineering-control-plane-design.md`
- Create: `docs/superpowers/plans/2026-07-28-codex-engineering-control-plane-v1.md`

- [x] **Step 1: Registrar arquitectura, alcance y exclusiones**

La especificación debe distinguir juicio, policy y gate; declarar que no habrá acciones remotas; y documentar la máquina de estados.

- [x] **Step 2: Registrar criterios de aceptación**

La especificación debe exigir RED/GREEN, policy alternativa, diagnóstico read-only, ausencia de secretos y no creación de commit o release.

- [x] **Step 3: Comprobar contradicciones y marcadores**

Run:

```bash
rg -n 'T[B]D|T[O]DO|implement l[a]ter|fill i[n]' docs/superpowers
```

Expected: ningún marcador incompleto.

## Task 2: Escribir tests de policy antes de producción

**Files:**
- Create: `tests/test_policy.py`
- Create: `tests/fixtures/valid-policy.toml`

- [x] **Step 1: Escribir tests de una policy válida e inválida**

Cubrir schema, claves, razonamiento, concurrencia, Pull Request obligatorio y fuente de release.

- [x] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest tests.test_policy -v
```

Expected: FAIL por ausencia de `control_plane.policy`.

- [x] **Step 3: Implementar el mínimo**

**Files:**
- Create: `control_plane/__init__.py`
- Create: `control_plane/policy.py`
- Create: `.codex/project-policy.toml`

Usar únicamente `tomllib`, tipos estándar y errores estructurados.

- [x] **Step 4: Ejecutar GREEN**

Run:

```bash
python3 -m unittest tests.test_policy -v
```

Expected: todos los tests PASS.

## Task 3: Escribir tests Git antes del preflight

**Files:**
- Create: `tests/git_test_support.py`
- Create: `tests/test_preflight.py`

- [x] **Step 1: Construir repositorios efímeros**

Crear bare remote y clones temporales con identidad Git local al fixture. No usar red ni el repositorio real.

- [x] **Step 2: Cubrir estados adversos**

Probar base, feature limpia, dirty, detached, atrasada, base alternativa, remote ausente, huérfano, release sincronizada y release adelantada.

- [x] **Step 3: Ejecutar RED**

Run:

```bash
python3 -m unittest tests.test_preflight -v
```

Expected: FAIL por ausencia de `control_plane.git_state`.

- [x] **Step 4: Implementar observación Git**

**Files:**
- Create: `control_plane/git_state.py`

No ejecutar comandos mutantes. Devolver hechos y errores estables.

- [x] **Step 5: Ejecutar GREEN**

Run:

```bash
python3 -m unittest tests.test_preflight -v
```

Expected: todos los escenarios PASS.

## Task 4: Escribir contrato CLI antes de implementarlo

**Files:**
- Create: `tests/test_cli.py`

- [x] **Step 1: Definir salida JSON y códigos de proceso**

Probar `policy-check`, `doctor` y `preflight`; éxito devuelve 0 y gate fallido devuelve 1.

- [x] **Step 2: Ejecutar RED**

Run:

```bash
python3 -m unittest tests.test_cli -v
```

Expected: FAIL por ausencia de `control_plane.cli`.

- [x] **Step 3: Implementar CLI mínima**

**Files:**
- Create: `control_plane/cli.py`
- Create: `scripts/control-plane`

La wrapper debe resolver la raíz sin depender del cwd.

- [x] **Step 4: Ejecutar GREEN**

Run:

```bash
python3 -m unittest tests.test_cli -v
```

Expected: todos los tests PASS.

## Task 5: Documentación operativa y plantillas

**Files:**
- Create: `README.md`
- Create: `AGENTS.md`
- Create: `docs/engineering/01-operating-model.md`
- Create: `docs/engineering/02-git-pr-merge.md`
- Create: `docs/engineering/03-reasoning-context-agents.md`
- Create: `docs/engineering/04-documentation-policy.md`
- Create: `docs/engineering/05-release-and-observation.md`
- Create: `docs/engineering/06-recovery.md`
- Create: `docs/engineering/07-adoption.md`
- Create: `docs/adr/README.md`
- Create: `docs/adr/TEMPLATE.md`
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `templates/TASK.md`
- Create: `templates/HANDOFF.md`
- Create: `templates/RELEASE_RECEIPT.json`

- [x] **Step 1: Escribir el flujo por estados**

Cada transición debe señalar evidencia y autoridad.

- [x] **Step 2: Escribir disparadores documentales**

Explicar cuándo crear ADR, plan, Issue, arquitectura, runbook, threat model, rollback y recibo.

- [x] **Step 3: Escribir política de razonamiento y contexto**

Mantener Sol, razonamiento proporcional, secuencial por defecto y máximo dos workers.

- [x] **Step 4: Escribir adopción progresiva**

Separar audit, enforce local y enforce remoto. No activar hooks antes de probarlos.

## Task 6: Crear verificación reproducible del paquete

**Files:**
- Create: `tests/test_repository_contract.py`
- Create: `tests/run.sh`

- [x] **Step 1: Escribir RED del contrato**

Exigir archivos, policy válida, Python compilable, scripts ejecutables, ausencia de placeholders y ausencia de patrones de secretos.

- [x] **Step 2: Ejecutar RED y completar los artefactos ausentes**

Run:

```bash
bash tests/run.sh
```

Expected inicial: FAIL por artefactos todavía ausentes. Expected final: PASS.

- [x] **Step 3: Ejecutar suite completa**

Run:

```bash
bash tests/run.sh
```

Expected: 0 failures.

## Task 7: Mejorar y verificar `verified-workflow`

**Files:**
- Modify: `/Users/bustaseo/.agents/skills/verified-workflow/SKILL.md`
- Create: `tests/skill-pressure-scenarios.md`

- [x] **Step 1: Ejecutar escenarios baseline con la skill actual**

Cubrir petición multifrente, salto directo a main/TestFlight y sobre-documentación. Registrar solo decisiones y fallos, no cadenas de pensamiento.

- [x] **Step 2: Añadir el mínimo necesario**

Incorporar normalización multifrente, transiciones Git, evaluación documental, autoridad externa, razonamiento proporcional y contexto.

- [x] **Step 3: Repetir escenarios**

La skill debe separar frentes, bloquear saltos y evitar ADR innecesario.

- [x] **Step 4: Validar estructura**

Run:

```bash
python3 /Users/bustaseo/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/bustaseo/.agents/skills/verified-workflow
```

Expected: validación satisfactoria.

## Task 8: Ajustar configuración global mínima

**Files:**
- Modify: `/Users/bustaseo/.codex/config.toml`

- [x] **Step 1: Aplicar solo cambios auditados**

Configurar:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
plan_mode_reasoning_effort = "xhigh"

[features.multi_agent_v2]
max_concurrent_threads_per_session = 2

[memories]
disable_on_external_context = true
```

Preservar el resto de configuración y no tocar credenciales.

- [x] **Step 2: Validar configuración**

Run:

```bash
codex doctor
```

Expected: configuración cargada; advertencias opcionales reportadas, no ocultadas.

## Task 9: Auditoría final local

**Files:**
- Modify: `docs/superpowers/plans/2026-07-28-codex-engineering-control-plane-v1.md`

- [x] **Step 1: Ejecutar verificación fresca**

```bash
bash tests/run.sh
python3 -m control_plane.cli policy-check --policy .codex/project-policy.toml --json
python3 -m control_plane.cli doctor --json
```

Resultado final: `58` tests, `0` fallos; policy válida; doctor local válido.

- [x] **Step 2: Inspeccionar seguridad y diff**

```bash
rg -n --hidden -g '!.git/**' '(api[_-]?key|secret|token|password)\\s*=' .
git diff --check
git status --short --branch
```

Resultado final: ningún patrón de asignación de secreto detectado, comprobación
de whitespace satisfactoria y estado Git identificado como rama huérfana
`codex/control-plane-v1` con todos los artefactos aún sin commit.

- [x] **Step 3: Entregar límites honestos**

Informar expresamente:

- dependencias: no instaladas;
- secretos: no leídos ni modificados;
- CI/CD: no configurado;
- remoto: no creado;
- commit/push/PR/merge/release: no ejecutados;
- siguiente autorización: commit inicial y conexión remota.

- [ ] **Step 4: Commit**

No ejecutar en esta entrega. Requiere una autorización posterior y explícita.
