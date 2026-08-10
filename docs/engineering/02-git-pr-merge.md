# Git, worktrees, Pull Requests y merge

## Modelo mental

- **Worktree:** carpeta con una copia de trabajo.
- **Rama:** nombre que apunta a una línea de commits.
- **Commit:** checkpoint local versionado.
- **Push:** publica una referencia local en un remote.
- **Pull Request:** propuesta revisable de integración.
- **Merge:** integración aceptada.
- **`origin/<base>`:** referencia local de la rama base remota tras un fetch.

Un commit en feature no está en main. Un push de feature tampoco. Un PR abierto
tampoco. La integración existe cuando el proveedor la registra y el commit de
merge está contenido en la rama base remota.

## Estado inicial obligatorio

Antes de crear una rama:

```text
repositorio correcto
+ worktree correcto
+ origin identificado
+ base identificada desde policy
+ referencias remotas actualizadas
+ commit de partida identificado
+ baseline válida
```

Ejecutar:

```bash
scripts/control-plane preflight --mode read
```

El modo `read` diagnostica; no autoriza escritura. Para escribir:

```bash
scripts/control-plane preflight --mode write
```

Antes de crear o integrar contra la base remota:

```bash
scripts/control-plane preflight --mode write --refresh
```

El refresco es una acción explícita que contacta el remote y actualiza solo la
referencia base; el modo por defecto usa la caché local.

Este gate exige:

- repositorio con commit;
- rama real;
- rama distinta de la base;
- árbol limpio al iniciar la transición;
- remote configurado;
- referencia `origin/<base>`;
- feature que contiene la base observada.

## Worktrees

Crear un worktree por unidad cuando ya exista un commit base:

```text
worktree A → codex/auth
worktree B → codex/stats
```

Reglas:

- verificar que `.worktrees/` está ignorado;
- crear desde la referencia remota actual;
- no abrir dos writers sobre los mismos archivos;
- conservar worktree y rama hasta probar la integración;
- no borrar un worktree con cambios no inventariados.

Un repositorio huérfano no puede generar un worktree útil desde una base
inexistente. Primero requiere un commit inicial autorizado.

## Commits

Un commit representa una unidad coherente. Su mensaje explica qué cambió:

```text
feat: add onboarding completion event
fix: preserve session after token refresh
docs: record authentication boundary decision
```

No usar:

```text
changes
various fixes
work in progress final
```

Antes del commit:

- tests correspondientes;
- diff revisado;
- secretos descartados;
- alcance coherente.

La existencia de un commit no implica permiso para push.

## Push

El push temprano protege trabajo y activa CI, pero es un efecto remoto. Debe
estar autorizado.

Antes:

```text
HEAD local
= commit que se pretende publicar

remote/rama
= destino correcto
```

Después:

```text
HEAD local
= origin/rama
```

No usar force push en una rama compartida salvo política y autorización
explícitas.

## Pull Request

Todo cambio a base protegida entra por PR.

Abrir Draft PR cuando:

- se quieren checks tempranos;
- el diseño necesita feedback;
- el trabajo aún no satisface todos los gates.

Marcar ready cuando:

- alcance completo;
- HEAD publicado;
- tests locales aprobados;
- documentación evaluada;
- conflictos conocidos resueltos;
- diff completo revisado.

El PR debe explicar:

- problema y resultado;
- alcance y no alcance;
- cambios;
- validación;
- riesgo;
- rollback;
- documentación;
- capturas cuando aporten;
- pendientes fuera de alcance.

## Incorporar cambios de la base

La policy define estrategia. Por defecto conservador:

- `merge origin/<base>` para rama compartida;
- rebase solo si la rama no está compartida o existe política explícita;
- nunca reescribir historial para ocultar conflictos.

Resolver conflictos por significado:

1. comprender ambos cambios;
2. preservar contratos válidos;
3. ejecutar tests de ambas áreas;
4. revisar el diff resultante.

Aceptar “ours” o “theirs” sobre archivos completos sin inspección no es una
estrategia.

## Autorizar merge

El merge requiere:

- PR hacia la base correcta;
- branch actualizada según Ruleset;
- checks requeridos aprobados;
- conversaciones resueltas;
- revisión completada;
- documentación aplicable;
- gates T3 cuando correspondan.

Si falta algo, Codex responde qué gate falta y mantiene el PR en Draft o
bloqueado.

## Outcome bridge v2.3

`PR LISTA` es el resultado predeterminado: significa `pr_ready` observado, no
permiso para integrar. La regla es **evidence != authority**. Review packets,
checks, RunPlan, effect plans y receipts son evidencia cerrada y no autorizante;
ningún JSON ni comando local concede commit, push, PR o merge.

Una petición de producto estable puede cubrir automáticamente su cadena normal
hasta PR. Solo una petición nativa actual, fresca y exacta «hasta squash merge»
incluye integración. Un efecto nuevo o deriva de repository, base, branch,
reviewed HEAD, scope, checks, policy o digest requiere una única reautorización
de producto. Nunca se pide al usuario operar clases, grants, nonces o bindings
internos.

Los efectos remotos son host-bound y one-shot. Sin adaptador nativo del host,
la ruta remota es `BLOCKED`; los adapters de tests no la vuelven disponible. Si
una escritura es incierta, se observa el destino exacto antes de reintentar y
no se emite una segunda escritura ni una reparación automática.

## Demostrar integración

Tras el merge:

1. obtener una sola vista coherente del PR;
2. comprobar estado `MERGED`;
3. comprobar base;
4. obtener `mergeCommit`;
5. refrescar refs;
6. demostrar que el commit está contenido en `origin/<base>`;
7. comprobar checks de la base;
8. sincronizar la copia local con fast-forward.

El fetch de verificación debe usar la URL credential-free y la identidad remota
exactas ligadas al plan, no un alias mutable. La contención local solo puede
probarse después de esa observación host. Véase el
[runbook del outcome bridge](16-outcome-bridge-rollback.md).

Squash merge crea un commit nuevo. No basta con buscar los hashes originales de
feature.

Evidencia mínima:

```text
repository
source branch and HEAD
PR URL
base branch
merge method
merge commit
origin/<base> hash
checks
working tree
```

## Rama de integración temporal

Para probar varios frentes:

```text
feature/auth ───────┐
feature/payments ───┼→ integration/end-to-end
feature/onboarding ─┘
```

La rama temporal:

- puede recrearse;
- no sustituye PR individuales;
- no se fusiona como historia definitiva;
- no convierte dependencias ocultas en aceptables.

## Limpieza

Solo después de integración demostrada:

- actualizar base local;
- verificar que no quedan cambios;
- conservar recibo;
- borrar feature remota si la política lo indica;
- retirar worktree;
- podar referencias.

Si la integración no está demostrada, rama y worktree se conservan.
