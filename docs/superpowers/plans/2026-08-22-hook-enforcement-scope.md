# Hook Enforcement Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que el modo `soft-enforce` deniegue únicamente comandos destructivos, y avise —sin bloquear— para lecturas, ediciones y el resto de efectos.

**Architecture:** `_untrusted_pretool_reason` clasifica cada acción y devuelve `(motivo, block_without_host)`. El modo de hook decide con `deny = enforce or (soft-enforce and block_without_host)`. Hoy `block_without_host` es `True` en ocho de sus nueve ramas, así que `soft-enforce` se comporta prácticamente como `enforce`. El arreglo no toca el modo por defecto ni el mecanismo: reduce `block_without_host` a la única rama que representa un efecto irreversible.

**Tech Stack:** Python 3.11, biblioteca estándar exclusivamente, `unittest`. Sin dependencias nuevas.

**Spec:** No hay documento de diseño previo. Este plan es la especificación; la sección «Contexto y decisión» de abajo cumple esa función y debe leerse antes de tocar código.

## Global Constraints

- Core permanece en **27 módulos exactos**. Este frente no añade, renombra ni elimina ninguno.
- **Cero dependencias nuevas.** Solo biblioteca estándar de Python 3.11.
- **No se toca CI** (`.github/workflows/`) ni la política, el registry ni los locks salvo el sello documental.
- **No se toca ADR 0006** ni la cuarentena de `adopt`/`upgrade`.
- El footer del threat model se recalcula **al final**, cuando el resto de bytes sea definitivo.
- Base exacta: `origin/main@f1fdecbb26fed9272d07823c31f06ef15ac89f78`.
- Worktree: `/Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1`, rama `codex/hook-enforcement-scope-v1`.

---

## Contexto y decisión

*Esta sección existe para que quien continúe entienda **por qué**, no solo qué. Si se
implementa el plan sin leerla, es probable que se «arregle» de la forma equivocada.*

### Qué pasó

El 2026-08-21, el PR #26 (*Loss Guards v1*) cambió el modo por defecto de los hooks de
`audit` a `soft-enforce`. El objetivo era legítimo y acotado: que los cuatro patrones nuevos
de borrado de rama (`git branch -d/-D`, `git push --delete`, `git push :refs/heads/…`)
bloquearan de verdad en vez de solo avisar.

El cambio se propuso razonando sobre `DESTRUCTIVE_PATTERNS`. No se comprobó qué **más**
gobierna ese mismo interruptor.

### El defecto

`_untrusted_pretool_reason` (`control_plane/hooks.py:1416-1466`) devuelve
`block_without_host=True` en ocho de sus nueve ramas de retorno. Solo dos casos escapan: una
invocación exacta de `scripts/control-plane safe-read` y las herramientas `mcp__*`.

Combinado con `deny = mode == "enforce" or (mode == "soft-enforce" and block_without_host)`
(`hooks.py:1598-1600`), el resultado es que con el nuevo valor por defecto se deniegan:

| Acción | Motivo devuelto | Denegada hoy |
|---|---|---|
| `Edit`, `Write`, `apply_patch` | `pending_host_authorization_bridge` | sí |
| `git status`, `diff`, `log`, `show`, `rev-parse` | `raw_read_requires_safe_read` | sí |
| `rg` | `raw_read_requires_safe_read` | sí |
| cualquier comando con metacaracteres de shell | `ambiguous_shell_command` | sí |
| cualquier otro comando Bash | `unresolved_bash_effect` | sí |
| `git push` (simple) | `git_effect_not_host_attested` | sí |
| herramienta desconocida | `unrecognized_tool_effect` | sí |
| `git branch -D`, `reset --hard`, `clean -f`, `push --force`, `rm -rf` | `destructive_command_requires_explicit_authority` | sí *(correcto)* |

`tests/test_hooks.py:287` ya documenta la consecuencia: se llama
`test_raw_read_is_denied_by_default_and_advisory_in_explicit_audit`.

### Por qué importa ahora y no antes

Nada está roto en el clon canónico **porque los hooks no están instalados**: `core.hooksPath`
sin definir, `.git/hooks` solo con `.sample`, y `.codex/git-hooks/pre-commit` todavía con el
marcador `__CONTROL_PLANE_ENTRYPOINT__` sin sustituir. El hook no se ha disparado ni una vez.

`adoption_enablement` instala exactamente esos ficheros y fija `core.hooksPath`
(`adoption_enablement/manifest.py:49-68`). Es decir: **el defecto se activa en el momento en
que el control plane se adopta en un proyecto real**, que es justo el siguiente paso del
roadmap. El agente que trabajara en ese proyecto no podría editar ficheros ni ejecutar
`git status`.

### Por qué se corrige la granularidad y no el valor por defecto

Volver a `audit` es la reparación obvia y es la equivocada: revierte también el bloqueo del
borrado de ramas, que era el objetivo legítimo del PR #26 y que responde a una pérdida de
trabajo real ocurrida el 2026-08-20 (rama `codex/survey-hardening-wip`, 3.102 líneas que
estuvieron a un merge de quedar inalcanzables).

El interruptor no está mal. Lo que está mal es que casi todas las ramas lo activen.

### El reparto elegido, y su razón

| Motivo | Nuevo valor | Por qué |
|---|---|---|
| `destructive_command_requires_explicit_authority` | **True** | Único caso irreversible o de difícil recuperación. Es el objetivo original del PR #26. |
| `git_effect_not_host_attested` (push simple) | False | `AGENTS.md` concede desde el PR #27 autoridad permanente para push sobre ramas de trabajo. Bloquearlo aquí contradice esa concesión en otra capa. `push --force` y `push --delete` siguen bloqueados porque la rama destructiva se evalúa **antes** (`hooks.py:1446` frente a `:1459`). |
| `raw_read_requires_safe_read` | False | Son lecturas. No mutan nada. Bloquearlas impide incluso diagnosticar. |
| `pending_host_authorization_bridge` | False | Editar y escribir es el trabajo. La reversibilidad la dan git y los gates, no el hook. |
| `unresolved_bash_effect` | False | Cajón de sastre: bloquearlo equivale a denegar por defecto todo lo no enumerado. |
| `ambiguous_shell_command` | False | Un comando con `&&` o `\|` no es destructivo por serlo. |
| `unrecognized_tool_effect` | False | Misma lógica que el anterior. |
| `mcp_use_…` | False *(sin cambio)* | Ya era advisory. |

`enforce` sigue denegando **todo**, sin cambios. Quien quiera la postura estricta la tiene
disponible de forma explícita; deja de ser el comportamiento por defecto por accidente.

### Qué NO entra en este frente

Registrado aquí para que no se amplíe el alcance a mitad de la implementación:

- **Instalar los hooks en el clon canónico.** El marcador `__CONTROL_PLANE_ENTRYPOINT__` sin
  sustituir y `core.hooksPath` sin definir son un frente propio: hay que decidir si el clon
  del propio control plane debe auto-adoptarse.
- **Cablear `maintenance.py`**, el almacén de «último verde», el TTL de leases y la agregación
  de `risk_sentinel`. Todos siguen abiertos.
- **Borrar las 37.356 líneas de tests que el gate no ejecuta.**
- Cualquier cambio en `adoption_enablement`.

---

## File Structure

| Fichero | Responsabilidad en este frente |
|---|---|
| `control_plane/hooks.py` | Modificar. Siete valores de retorno en `_untrusted_pretool_reason`. Nada más. |
| `tests/test_core_hooks.py` | Modificar. Test nuevo que fija el contrato: destructivo deniega, el resto avisa. **Este fichero sí lo ejecuta el gate.** |
| `tests/test_hooks.py` | Modificar. Un test afirma el contrato viejo y hay que actualizarlo. **El gate no ejecuta este fichero** (`grep -c "tests\.test_hooks\b" tests/run.sh` = 0); se actualiza igualmente para no dejar una afirmación falsa en el árbol. |
| `docs/security/2026-08-12-control-plane-core-threat-model.md` | Modificar. Declarar el alcance real de `soft-enforce` y recalcular el footer. |
| `docs/superpowers/plans/2026-08-22-hook-enforcement-scope.md` | Este documento. Ya creado. |

---

## Task 1: Restringir el bloqueo de `soft-enforce` a lo destructivo

**Files:**
- Modify: `control_plane/hooks.py:1416-1466`
- Test: `tests/test_core_hooks.py` (añadir al final de `CoreHookTests`)
- Test: `tests/test_hooks.py:287-322` (actualizar el test del contrato viejo)

**Interfaces:**
- Consumes: `run_hook(payload: bytes, *, expected_root: Path | None = None) -> str` y `make_repo(path: Path) -> Path`, ya usados en `tests/test_core_hooks.py`.
- Produces: `_untrusted_pretool_reason(tool_name: str, tool_input: object, root: Path) -> tuple[str | None, bool]` con la misma firma. Solo cambian valores de retorno; ningún consumidor necesita adaptarse.

- [ ] **Step 1: Escribir el test que falla**

Añadir al final de la clase `CoreHookTests` en `tests/test_core_hooks.py`:

```python
    def test_soft_enforce_denies_only_destructive_commands(self) -> None:
        from control_plane.hooks import run_hook

        advisory_cases = (
            ("Bash", {"command": "git status --short"}),
            ("Bash", {"command": "git diff --stat"}),
            ("Bash", {"command": "rg pattern"}),
            ("Bash", {"command": "git push origin feature/work"}),
            ("Bash", {"command": "make build && make test"}),
            ("Edit", {"file_path": "README.md"}),
            ("Write", {"file_path": "README.md"}),
            ("apply_patch", {"input": "*** Begin Patch"}),
        )
        with tempfile.TemporaryDirectory() as temporary:
            repo = make_repo(Path(temporary) / "repo").resolve()
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)

                for tool_name, tool_input in advisory_cases:
                    with self.subTest(tool=tool_name, input=tool_input):
                        output = json.loads(
                            run_hook(
                                json.dumps(
                                    {
                                        "hook_event_name": "PreToolUse",
                                        "cwd": str(repo),
                                        "tool_name": tool_name,
                                        "tool_input": tool_input,
                                    },
                                    separators=(",", ":"),
                                ).encode(),
                                expected_root=repo,
                            )
                        )["hookSpecificOutput"]

                        self.assertNotIn("permissionDecision", output)

                destructive = json.loads(
                    run_hook(
                        json.dumps(
                            {
                                "hook_event_name": "PreToolUse",
                                "cwd": str(repo),
                                "tool_name": "Bash",
                                "tool_input": {
                                    "command": "git branch -D feature/old"
                                },
                            },
                            separators=(",", ":"),
                        ).encode(),
                        expected_root=repo,
                    )
                )["hookSpecificOutput"]

                self.assertEqual(destructive.get("permissionDecision"), "deny")
                self.assertEqual(
                    destructive.get("permissionDecisionReason"),
                    "CONTROL_PLANE_SOFT_ENFORCE: "
                    "destructive_command_requires_explicit_authority",
                )
```

- [ ] **Step 2: Ejecutar el test y comprobar que falla**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && python3 -m unittest tests.test_core_hooks.CoreHookTests.test_soft_enforce_denies_only_destructive_commands -v
```

Esperado: FAIL en el primer subTest (`git status --short`), con `'permissionDecision' unexpectedly found in ...`. Eso demuestra el muro.

- [ ] **Step 3: Aplicar el cambio mínimo**

En `control_plane/hooks.py`, dentro de `_untrusted_pretool_reason`, cambiar `True` por `False` en **siete** valores de retorno, dejando intacto el destructivo:

```python
        if _SHELL_META.search(command):
            return "ambiguous_shell_command", False
        try:
            argv = tuple(shlex.split(command, posix=True))
        except ValueError:
            return "ambiguous_shell_command", False
```

```python
        if any(pattern.search(command) for pattern in DESTRUCTIVE_PATTERNS):
            return "destructive_command_requires_explicit_authority", True
```
*(sin cambios: es la única rama que sigue bloqueando)*

```python
            return "raw_read_requires_safe_read", False
        if argv and argv[0] == "rg":
            return "raw_read_requires_safe_read", False
        if parsed_git is not None and parsed_git[0][0] == "push":
            return "git_effect_not_host_attested", False
        return "unresolved_bash_effect", False
    if tool_name in {"Edit", "Write", "apply_patch"}:
        return "pending_host_authorization_bridge", False
    if tool_name.startswith("mcp__"):
        return "mcp_use_requires_task_authorization_and_egress_check", False
    return "unrecognized_tool_effect", False
```

Actualizar también el docstring de la función para que describa el contrato nuevo:

```python
    """Classify tool effects. Only irreversible ones block under soft-enforce."""
```

- [ ] **Step 4: Ejecutar el test y comprobar que pasa**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && python3 -m unittest tests.test_core_hooks.CoreHookTests.test_soft_enforce_denies_only_destructive_commands -v
```

Esperado: PASS.

- [ ] **Step 5: Comprobar que no se rompió el bloqueo de borrado de ramas**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && python3 -m unittest tests.test_core_hooks -v
```

Esperado: todos PASS. En particular `test_branch_deletion_commands_are_denied_by_default` y `test_invalid_hook_modes_fail_closed_to_soft_enforce`, que son el objetivo del PR #26 y no deben cambiar de comportamiento.

- [ ] **Step 6: Actualizar el test que afirma el contrato viejo**

En `tests/test_hooks.py`, sustituir el test de las líneas 287-322 por esta versión, que renombra e invierte la afirmación:

```python
    def test_raw_read_is_advisory_in_soft_enforce_and_in_explicit_audit(
        self,
    ) -> None:
        from control_plane.hooks import run_hook

        encoded = json.dumps(
            self.payload(
                "PreToolUse",
                tool_name="Bash",
                tool_input={"command": "git status --short"},
            )
        ).encode()
        with patch.dict(
            os.environ,
            {"CODEX_CONTROL_PLANE_HOOK_MODE": "audit"},
            clear=False,
        ):
            audit = json.loads(run_hook(encoded))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_CONTROL_PLANE_HOOK_MODE", None)
            default = json.loads(run_hook(encoded))

        self.assertIn(
            "CONTROL PLANE RISK",
            audit["hookSpecificOutput"]["additionalContext"],
        )
        self.assertNotIn(
            "permissionDecision", audit["hookSpecificOutput"]
        )
        self.assertNotIn(
            "permissionDecision", default["hookSpecificOutput"]
        )
```

- [ ] **Step 7: Ejecutar la batería de hooks completa**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && python3 -m unittest tests.test_core_hooks tests.test_hooks -v 2>&1 | tail -20
```

Esperado: todos PASS. Si algún otro test afirma que una lectura o edición se deniega, actualízalo con el mismo criterio y anótalo en el mensaje de commit.

- [ ] **Step 8: Commit**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && git add control_plane/hooks.py tests/test_core_hooks.py tests/test_hooks.py && git commit -m "fix: limit soft-enforce denial to destructive commands

El modo por defecto pasó a soft-enforce en PR #26 para bloquear el borrado de
ramas, pero block_without_host era True en ocho de las nueve ramas de
_untrusted_pretool_reason. El efecto real era denegar Edit, Write, apply_patch,
git status, rg y cualquier comando no enumerado.

No se toca el modo por defecto: se reduce block_without_host a la única rama
irreversible. enforce sigue denegando todo.

El defecto era latente: los hooks no están instalados en el clon canónico, pero
adoption_enablement los instala, así que se habría activado en la primera
adopción real."
```

---

## Task 2: Declarar el alcance en el threat model y re-vincular el sello

**Files:**
- Modify: `docs/security/2026-08-12-control-plane-core-threat-model.md`
- Test: `tests/test_core_documentation.py` (no se edita; se ejecuta)

**Interfaces:**
- Consumes: `normalized_snapshot_version()` de `tests.test_core_documentation`.
- Produces: nada que consuma otra tarea. Es la última.

**Por qué esta tarea existe:** el footer del threat model es un digest sobre todo el árbol
trackeado. Cualquier cambio de Task 1 lo invalida y deja la suite en rojo. Recalcularlo es
obligatorio, y es la ocasión de declarar el alcance real del modo por defecto para que no
vuelva a leerse como «bloquea todo».

- [ ] **Step 1: Localizar la sección del modo de hooks**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && grep -n "soft-enforce\|hook_mode\|audit" docs/security/2026-08-12-control-plane-core-threat-model.md
```

- [ ] **Step 2: Escribir la declaración de alcance**

Añadir en la sección de límites residuales del threat model, adaptando la redacción al estilo
del documento:

```markdown
- El modo por defecto `soft-enforce` deniega únicamente
  `destructive_command_requires_explicit_authority`: borrado de ramas local y remoto,
  `reset --hard`, `clean -f`, `push --force` y `rm -rf`. Lecturas, ediciones, `push` simple
  y comandos no enumerados producen aviso sin bloqueo. El modo `enforce`, explícito, sigue
  denegando toda acción no atestiguada por el host. Residuo aceptado: bajo `soft-enforce` un
  efecto irreversible que no coincida con `DESTRUCTIVE_PATTERNS` no se bloquea; la contención
  de esa clase recae en la protección de rama del proveedor y en los gates, no en el hook.
```

- [ ] **Step 3: Ejecutar el test documental y comprobar que falla solo por el sello**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && python3 -m unittest tests.test_core_documentation -v 2>&1 | tail -25
```

Esperado: un único fallo, `test_threat_model_is_repository_scoped_and_snapshot_bound`, por
digest desalineado. Si falla algún otro test, arréglalo antes de sellar: el sello es lo último.

- [ ] **Step 4: Recalcular y escribir el footer**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && python3 -c "import sys;sys.path.insert(0,'.');from tests.test_core_documentation import normalized_snapshot_version as v;print('Version:',v())"
```

Sustituir la última línea del threat model por la salida exacta de ese comando.

- [ ] **Step 5: Verificar que el sello coincide**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && expected=$(tail -n 1 docs/security/2026-08-12-control-plane-core-threat-model.md | sed 's/^Version: //') && observed=$(python3 -c "import sys;sys.path.insert(0,'.');from tests.test_core_documentation import normalized_snapshot_version as v;print(v())") && test "$expected" = "$observed" && echo "SELLO OK"
```

Esperado: `SELLO OK`.

- [ ] **Step 6: Ejecutar el documental completo**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && python3 -m unittest tests.test_core_documentation -v 2>&1 | tail -5
```

Esperado: OK.

- [ ] **Step 7: Commit**

```bash
cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && git add docs/security/2026-08-12-control-plane-core-threat-model.md && git commit -m "docs: declare the real scope of soft-enforce and reseal

Superficie cambiada: clasificación de efectos del hook PreToolUse. No toca
runtime de Core, policy, registry ni CI, así que el análisis del threat model no
requiere revisión de fondo, solo re-vinculación del digest."
```

---

## Verificación final

Antes de considerar el frente terminado:

- [ ] **Frontera respetada.** `git diff --name-only origin/main` debe devolver exactamente:
  `control_plane/hooks.py`, `tests/test_core_hooks.py`, `tests/test_hooks.py`,
  `docs/security/2026-08-12-control-plane-core-threat-model.md`,
  `docs/superpowers/plans/2026-08-22-hook-enforcement-scope.md`. Cualquier otra ruta es
  desbordamiento: para y dilo.

- [ ] **Inventario intacto.** Core sigue en 27 módulos; ningún fichero nuevo bajo `control_plane/`.

- [ ] **Gate integral.** Una sola ejecución sobre los bytes finales:
  ```bash
  cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && bash tests/run.sh
  ```
  Presupuesto `max_gate_runs=6`, invariante: la última ejecución verde sobre los bytes
  finales. Tarda ~13 minutos. **Delégalo a una ejecutora fresca y desechable con el timeout
  más largo que acepte la herramienta y no comentes la espera.**

  ⚠️ **Contención de mutex:** el gate toma `verification.lock` en el git dir común, que
  comparten todos los worktrees de este clon. Si otro frente está corriendo su gate,
  aparecerá `E_TEST_MUTEX` sin ejecutar nada. No es un defecto: espera y reintenta.

- [ ] **Post-gates.**
  ```bash
  cd /Users/bustaseo/Developer/control-plane-worktrees/hook-enforcement-scope-v1 && scripts/control-plane policy-check --policy .codex/project-policy.toml && scripts/control-plane registry-check --registry .codex/resource-registry.toml --policy .codex/project-policy.toml && scripts/control-plane doctor && git diff --check
  ```

- [ ] **Dos revisiones frescas** sobre los bytes congelados, restringidas al delta real.

---

## Estado para quien continúe

**Qué queda abierto tras este frente**, con su motivo, para que no haya que reconstruirlo:

1. **Los hooks no están instalados en el clon canónico.** `core.hooksPath` sin definir,
   `.git/hooks` solo con `.sample`, `.codex/git-hooks/pre-commit` con el marcador
   `__CONTROL_PLANE_ENTRYPOINT__` sin sustituir. Consecuencia: `guard_pre_push` y
   `GG_UNPUBLISHED_UNIQUE_BRANCH` —el objetivo del PR #26— **no se ejecutan aquí**. Decidir si
   el control plane debe auto-adoptarse es un frente propio.

2. **`deleteBranchOnMerge` sigue en `true`** en la configuración de GitHub. Es la causa
   original de la clase de riesgo que el PR #26 mitiga desde el otro lado. Desactivarlo es un
   clic del operador, no código.

3. **`maintenance.py` sigue sin cablear.** 282 líneas probadas que implementan un presupuesto
   acotado con estado terminal; `grep -c maintenance control_plane/cli.py` = 0, y su consumidor
   `VerificationResult.consumes_reframe` está fijado a `False` en las tres ramas de retorno
   (`verification.py:599,610,618`). Es el cambio con mejor relación valor/riesgo pendiente.

4. **No existe almacén de «último verde».** `GateReceiptV1`/`RunAttemptV1` solo aparecen como
   cadenas en una allowlist legacy de solo lectura, sin escritor vivo.

5. **Los leases no tienen TTL.** Una sesión muerta deja un bloqueo permanente que hoy solo
   levanta una intervención humana.

6. **`risk_sentinel` no puede devolver `PASS` jamás:** cablea `remote=UNKNOWN` y agrega por
   rango máximo, así que `RiskStatus.ok` es siempre `False`.

7. **37.356 líneas de tests que el gate no ejecuta.** `tests/run.sh` usa lista explícita, y CI
   solo lanza ese script. Borrarlas es un PR sin riesgo funcional.

8. **Solape con herramientas gratuitas.** `pre-commit`, la protección de rama de GitHub y
   GitHub Spec Kit cubren buena parte de lo que el roadmap todavía planea construir —en
   particular SpecPack (R2/R5), que Spec Kit ya resuelve bajo licencia MIT y con soporte para
   Codex CLI. Conviene decidirlo antes de invertir más.
