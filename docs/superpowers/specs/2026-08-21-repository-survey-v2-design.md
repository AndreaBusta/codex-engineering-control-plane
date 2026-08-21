# Diseño RepositorySurveyV2 — semántica de trabajo huérfano

Fecha: 2026-08-21. Estado: `IMPLEMENTED_LOCAL_CANDIDATE / FINAL_GATE_PENDING`.
`authorizes=false`.

Contrato WHAT/WHY para que `survey` describa de forma veraz el trabajo que
puede perderse dentro del clon seleccionado. Este documento sustituye el
contrato de salida `RepositorySurveyV1`; no implementa ni modifica el runtime.
Como documento sigue siendo `authorizes=false`; las transiciones Git se rigen
por la policy integrada en la base protegida, no por este artefacto.
El estado declara únicamente el candidato local observado tras Tasks 1–5: no
prueba gate final, integración, CI, release, adopción, instalación ni estado
remoto.

---

## 1. Decisión

El comando `survey` emitirá `RepositorySurveyV2` por defecto. No habrá salida
V1 paralela, flag de compatibilidad ni reinterpretación silenciosa de campos.

V2 hace cuatro cambios de contrato inseparables:

1. `only_in_branch` desaparece. Su cálculo actual cuenta paths añadidos, no
   commits ni todo lo que existe solo en la rama, y pasa a llamarse
   `added_paths`. El campo es informativo y nullable: si su enriquecimiento
   acotado no termina, vale `null` sin degradar la observación normativa.
2. Cada rama declara las tres señales que determinan si conserva trabajo único:
   contenido distinto de la base, commits no alcanzables desde la base y
   ausencia de una ref remota local homónima.
3. `orphan_work` incluye el número de ramas locales con trabajo único sin
   publicar.
4. La salida separa severidades: una rama única sin publicar es `FAIL`; stashes
   o archivos sin rastrear, sin esa condición, son `WARN`. Automatización y
   humanos ya no reciben el mismo status ni el mismo exit code para ambos
   riesgos.

El corte es explícito porque cambiar el significado de `status`, el exit code y
`only_in_branch` bajo el discriminador V1 sería una incompatibilidad oculta.
La adopción externa continúa prohibida y no se ha observado dentro del
repositorio ningún consumidor que valide un conjunto cerrado de claves JSON.

## 2. Evidencia que obliga al cambio

Sobre `origin/main@cda6f501514b5c10c456cf66e176c92828f63f3f`, una lectura
real de `survey` devolvió:

- `status=PASS`;
- `orphan_work.stashes=0`;
- `orphan_work.untracked_total=0`;
- `codex/survey-hardening-wip` con
  `content_equivalent_to_base=false` y `only_in_branch=0`.

La rama preservada contiene trabajo fuera de `main`, pero V1 no lo incorpora a
`orphan_work`; además, el nombre `only_in_branch` oculta que solo cuenta paths
con estado `A`. El guard pre-push fusionado en PR #26 sí bloquea una rama local
con contenido y commits únicos sin ref remota homónima. Dos comandos del mismo
producto responden de forma distinta a la misma pregunta operativa.

## 3. Objetivo y no objetivos

### Objetivo

Una única observación read-only debe responder, dentro del clon seleccionado:

> ¿Existe trabajo que puede quedar sin una referencia remota propia y perderse
> durante una limpieza, un squash o el borrado automático de una rama?

Survey y el guard pre-push compartirán la misma definición lógica de rama no
publicada con contenido único. No necesitan compartir implementación mutable:
la paridad se fija mediante el contrato y pruebas cruzadas sobre las mismas
fixtures.

### No objetivos

- No ver otros clones. `other_clones` permanece siempre `UNKNOWN`.
- No comprobar el servidor remoto ni ejecutar red. Una ref
  `refs/remotes/<remote>/<branch>` es evidencia local y puede estar obsoleta.
- No reclasificar cambios tracked sin commit. Permanecen visibles como
  `worktrees[].dirty`; este frente corrige la propiedad/publicación de ramas,
  no redefine la limpieza de un worktree.
- No conceder autoridad ni recomendar borrado. `authorizes=false` permanece.
- No añadir módulos al Core de 27 módulos ni dependencias.
- No cablear mantenimiento, last-green, risk sentinel, adopción o Autopilot.
- No absorber el hardening preservado en `codex/survey-hardening-wip`:
  ejecución de filtros, límites reales de memoria, Gitlinks, alternates,
  sustitución de worktrees, TOCTOU general, case-folding APFS, symlinks y
  canonicalización completa siguen siendo un frente separado.
- No modificar CI/CD.

## 4. Definición normativa

Para una base fijada `B`, una rama local `L` y el remote seleccionado `R`:

- `content_equivalent_to_base(L)` es verdadero si el tree de `L` es idéntico
  al tree de `B`.
- `has_unique_commits(L)` es verdadero si existe al menos un commit alcanzable
  desde `L` que no sea alcanzable desde `B`.
- `remote_tracking_ref_present(L, R)` es verdadero si existe exactamente
  `refs/remotes/R/<nombre-de-L>` y el objeto observado es un commit válido.
- `unpublished_unique(L)` es verdadero si y solo si:

```text
not content_equivalent_to_base(L)
and has_unique_commits(L)
and not remote_tracking_ref_present(L, R)
```

Las tres condiciones son necesarias:

- Una rama adelantada por ancestry pero con tree equivalente tras squash no es
  trabajo huérfano.
- Una rama atrasada cuyo tree difiere de la base, pero sin commits exclusivos,
  no es trabajo huérfano.
- Una ref remota local homónima exime aunque esté atrasada. Es la misma frontera
  deliberada del guard pre-push; la frescura del servidor queda fuera de esta
  observación.

Survey no tiene contexto de una publicación en curso. Por ello no aplica la
exención transitoria del guard para el ref+OID que está siendo empujado: la rama
deja de aparecer como no publicada cuando la ref remota local exacta existe.

## 5. Contrato JSON

El payload de dominio queda fijado así. El CLI lo envuelve después con
`command`, `ok` y `facts`, sin cambiar el discriminador ni la semántica:

```json
{
  "schema_version": 2,
  "kind": "RepositorySurveyV2",
  "comparison": {
    "base_ref": "origin/main",
    "base_head": "cda6f501514b5c10c456cf66e176c92828f63f3f",
    "remote_name": "origin"
  },
  "clone": {
    "root": "",
    "common_git_dir": "",
    "branch": "codex/survey-orphan-semantics-v1",
    "head": "cda6f501514b5c10c456cf66e176c92828f63f3f"
  },
  "worktrees": [
    {
      "path": "",
      "branch": "",
      "head": "",
      "dirty": 0,
      "untracked": 0,
      "detached": false
    }
  ],
  "branches": [
    {
      "name": "codex/survey-hardening-wip",
      "head": "d901bb6c95377074a7fb2fb23762476547335969",
      "added_paths": null,
      "content_equivalent_to_base": false,
      "has_unique_commits": true,
      "remote_tracking_ref_present": false,
      "unpublished_unique": true
    }
  ],
  "orphan_work": {
    "stashes": 0,
    "untracked_total": 0,
    "unpublished_unique_branches": 1
  },
  "other_clones": "UNKNOWN",
  "status": "FAIL",
  "error_code": null,
  "authorizes": false
}
```

### Campos nuevos o sustituidos

| Campo | Semántica |
|---|---|
| `comparison.base_ref` | Ref solicitada por el operador; descriptiva, no autoridad |
| `comparison.base_head` | Commit al que se ligó toda la comparación; `null` si no pudo fijarse y el payload es `UNKNOWN` |
| `comparison.remote_name` | Remote exacto usado para refs homónimas; por defecto `origin` |
| `branches[].added_paths` | Número de paths con estado añadido entre la base fijada y la rama; `null` si el enriquecimiento opcional no termina de forma íntegra |
| `branches[].has_unique_commits` | Existencia, no cantidad, de commits no alcanzables desde la base |
| `branches[].remote_tracking_ref_present` | Existencia local de la ref remota homónima exacta y válida |
| `branches[].unpublished_unique` | Resultado del predicado normativo de la sección 4 |
| `orphan_work.unpublished_unique_branches` | Número de ramas con `unpublished_unique=true` |

`added_paths` es informativo y no participa en el predicado de trabajo
huérfano. Su timeout, error Git o salida no íntegra produce `null` solo en ese
campo: nunca cambia `status`, `error_code` ni las tres señales normativas. Los
nombres de las ramas afectadas se obtienen filtrando
`branches[].unpublished_unique`; el agregado no duplica esa lista.

En un payload `UNKNOWN` no se emite una observación parcial como si estuviera
vacía: `worktrees` y `branches` son `null`, y los tres valores de `orphan_work`
son `null`. `comparison.base_head` y los campos de identidad de clon no
observados también son `null`; la ref, el remote y la raíz solicitados se
conservan como contexto. Así, evidencia ausente nunca se serializa como `0`,
`false` o `[]`. Esta nulabilidad de evidencia normativa es distinta de
`branches[].added_paths=null`: esta última vive dentro de una rama ya observada
íntegramente y no produce `UNKNOWN`.

## 6. API y CLI

- `survey_repository()` conserva su responsabilidad read-only y gana un
  `remote_name` explícito con default `origin`.
- `BranchObservation.only_in_branch` se sustituye por `added_paths: int | None`
  y por las tres señales booleanas de V2.
- `RepositorySurvey` conserva el nombre interno, pero almacena la base fijada,
  el remote y el agregado de ramas no publicadas.
- `survey_payload()` pasa a producir exclusivamente V2.
- `scripts/control-plane survey` continúa siendo el único comando. Gana
  `--remote`, con default `origin`; no se añade `--schema-version`.
- El wrapper CLI añade a `facts`
  `orphan_unpublished_unique_branches`, manteniendo los facts de stashes y
  untracked.
- El renderer humano reconoce `WARN` y `_emit()` devuelve el exit propio de los
  cuatro estados. `ok=true` queda reservado a `PASS`.
- Toda ruta excepcional de `survey` conserva la misma forma cerrada V2 para
  `UNKNOWN`. Solo expone un `error_code` perteneciente al vocabulario Survey y
  un mensaje estable; texto arbitrario de una excepción nunca cruza al payload.

`remote_name` debe ser un nombre Git remoto acotado y no ambiguo para construir
`refs/remotes/<remote_name>/...`. Un valor inválido no se normaliza ni se trata
como ausencia: devuelve `E_SURVEY_REMOTE_UNKNOWN`.

No se conserva una API V1 deprecada: hacerlo duplicaría pruebas, documentación
y caminos de error para un contrato que todavía no tiene adopción externa
permitida.

## 7. Status, exit codes y UNKNOWN

El estado se calcula sobre la evidencia normativa completa, con esta
precedencia:

```text
UNKNOWN si cualquier evidencia necesaria no es íntegra
FAIL si unpublished_unique_branches > 0
WARN si unpublished_unique_branches == 0
        y (stashes > 0 o untracked_total > 0)
PASS en otro caso
```

`added_paths` no es evidencia necesaria: puede ser `null` sin producir
`UNKNOWN`. Si coinciden una rama no publicada y stashes o archivos sin
rastrear, prevalece `FAIL`.

Los tres exit codes existentes conservan su significado y V2 añade uno nuevo:

| Estado | Exit |
|---|---:|
| `PASS` | 0 |
| `FAIL` | 1 |
| `UNKNOWN` | 2 |
| `WARN` | 3 |

Asignar `WARN=3` evita dos degradaciones: no reinterpreta `FAIL=1` ni
`UNKNOWN=2`, usados transversalmente por el Control Plane, y tampoco convierte
una advertencia en éxito de proceso. En `WARN`, `FAIL` y `UNKNOWN`, el wrapper
expone `ok=false`. `WARN` y `FAIL` son resultados de dominio y conservan
`error_code=null`; `UNKNOWN` nombra la causa de observación incompleta.

Son condiciones de `UNKNOWN`, como mínimo:

- base ausente, no commit o no fijable;
- remote inválido o inventario remoto ambiguo;
- head/tree/ref no válido o duplicado;
- ref remota exacta existente que no apunta a commit;
- observación de reachability incompleta;
- repositorio shallow o estado shallow no observable, porque la ausencia de un
  camino de ancestry no prueba ausencia en historia truncada;
- timeout, error Git o salida no decodificable de forma íntegra en evidencia
  normativa, o límite de ramas/worktrees excedido.

La ausencia de una ref remota solo se convierte en `false` cuando el inventario
acotado prueba la ausencia. Un fallo de observación normativa nunca se presenta
como ref ausente. No hay `PASS`, `WARN` ni `FAIL` parcial. Esta regla no convierte
el enriquecimiento opcional de `added_paths` en evidencia normativa.

Todo fallo de evidencia obligatoria fija `status="UNKNOWN"`; `error_code`
identifica su dominio:

- `E_SURVEY_BASE_UNKNOWN` cubre una base ausente, inválida o no fijable;
- `E_SURVEY_INVENTORY` cubre inventario local, reachability y postinventario
  (pasos 1, 3 y 4), incluidos timeout, error Git, decode o estructura inválida,
  ref local inválida o duplicada, drift y ambigüedad shallow;
- `E_SURVEY_REMOTE_UNKNOWN` cubre `remote_name` y el inventario remoto
  obligatorio (paso 2), incluidos timeout, error Git, decode o estructura
  inválida y una ref remota que no apunte a commit;
- `E_SURVEY_LIMIT` cubre los límites declarados de ramas y worktrees.

## 8. Modelo de observación

La implementación deberá fijar primero `base_ref` a un commit y tree concretos.
Todas las comparaciones posteriores usan ese commit fijado, no una ref mutable.

El diseño exige resolver la señal normativa sin un proceso Git de reachability
por rama:

1. inventario conjunto de refs locales con head y tree;
2. inventario exacto de las refs remotas homónimas esperadas;
3. una consulta agregada `--merged` que serialice para cada fila `refname`,
   `objectname`, `objecttype` y `tree`, y valide esa identidad completa contra
   el inventario local inicial congelado;
4. inmediatamente después, repetición del mismo inventario local acotado y
   exigencia de igualdad exacta del mapa ref/head/type/tree con el inicial;
5. solo después de fijar las tres señales y el status, enriquecimiento
   best-effort para contar `added_paths`.

El tree ya inventariado decide equivalencia de contenido. No se usa el número
de commits para inferir equivalencia bajo squash. Una fila `--merged`
duplicada, inesperada o cuyo head, tipo o tree no coincida con el inventario
inicial, así como cualquier diferencia en el inventario local repetido,
produce `UNKNOWN` con `E_SURVEY_INVENTORY`. Solo después de cerrar ambas
validaciones se deriva `has_unique_commits`; la consulta permanece agregada y
no se añade un proceso de reachability por rama.

Las comparaciones de `added_paths` comparten un único deadline de enriquecimiento
de 10 segundos para todas las ramas, no 10 segundos multiplicados por rama.
Cada subprocess recibe como timeout, como máximo, el tiempo restante. Si una
comparación falla o el deadline se agota, esa rama y todas las que no llegaron a
observarse usan `added_paths=null`; la respuesta normativa ya calculada se
conserva. Cualquier timeout o error en cualquier paso de evidencia normativa
anterior al enriquecimiento, incluido el postinventario (pasos 1–4), produce
`UNKNOWN`. Los pasos locales 1, 3 y 4 usan `E_SURVEY_INVENTORY`; `remote_name`
y el paso remoto 2 usan `E_SURVEY_REMOTE_UNKNOWN`; un límite declarado de
ramas o worktrees usa `E_SURVEY_LIMIT`.

Los límites existentes de 64 ramas, 64 worktrees, 10 segundos por invocación y
1 MiB de salida siguen gobernando este frente. Cambiar esos presupuestos o
reemplazar el runner de subprocess pertenece a Survey hardening, no a V2.

## 9. Paridad con el guard pre-push

La paridad es contractual y comprobable:

| Escenario | Survey V2 | Guard pre-push |
|---|---|---|
| Tree distinto, commits únicos, sin ref homónima | `unpublished_unique=true`, `FAIL` | `GG_UNPUBLISHED_UNIQUE_BRANCH` |
| Ref homónima local válida, aunque atrasada | no huérfana | permite por esa señal |
| Tree equivalente con commits únicos por squash | no huérfana | permite |
| Tree distinto pero rama enteramente alcanzable desde base | no huérfana | permite |
| Evidencia remota o reachability ambigua | `UNKNOWN` | `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN` |
| Historia shallow sin reachability íntegra | `UNKNOWN` | `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN` |
| `added_paths` no observable con predicado íntegro | campo `null`; conserva status | no aplica |

Las pruebas de ambos componentes reutilizan escenarios equivalentes, pero el
guard no importa `survey.py` y Survey no importa el ejecutor del guard. Así se
evita convertir un comando diagnóstico en dependencia de una transición Git.

## 10. Compatibilidad y migración

Este es un corte de contrato, no una extensión compatible:

- `schema_version` y `kind` cambian juntos a V2.
- `only_in_branch` desaparece; nunca cambia de significado en V1.
- `status` y el exit code separan ramas no publicadas (`FAIL=1`) de
  stashes/untracked sin ese riesgo (`WARN=3`). Para `survey`, esta especificación
  sustituye el requisito 3.3 que solo enumeraba `PASS=0`, `FAIL=1` y
  `UNKNOWN=2`; los otros comandos no cambian.
- Los tests y consumidores internos deben migrar de forma atómica con el
  runtime y la documentación. Ningún consumidor puede colapsar `WARN` y
  `FAIL` a un único status o asumir que todo no-cero es exit 1.
- El diseño gobernante 3.3 debe declarar su bloque V1 como sustituido por este
  documento; el plan histórico 3.3 no se reescribe.
- README, skill `control-plane-git`, orientación, runbook de ramas, threat model
  y contratos CLI deben describir V2 antes del cierre.

No se ofrece compatibilidad externa porque `external_consumer_adoption` sigue
`PROHIBITED`. Si aparece un consumidor externo antes de implementar, la nueva
evidencia invalida este corte y exige reabrir la decisión, no añadir un flag de
forma oportunista.

## 11. Seguridad y límites residuales

- Survey sigue siendo read-only, sin red y `authorizes=false`.
- La ref remota observada es local. Puede estar atrasada respecto del servidor;
  esta exención residual es idéntica a la del guard fusionado.
- `other_clones=UNKNOWN` significa que `PASS` solo cubre el clon seleccionado.
- V2 no convierte Survey en oráculo de borrado ni hace segura una limpieza.
- Evidencia ambigua falla cerrada como `UNKNOWN`.
- `added_paths=null` declara una carencia informativa, no una ausencia ni una
  degradación de la señal de pérdida.
- `WARN` sigue siendo no-cero y no autorizante; no permite limpiar ni borrar.
- El threat model debe declarar que Survey y pre-push comparten el predicado,
  además de nombrar las diferencias de contexto: publicación en curso, frescura
  remota y visibilidad de otros clones.
- Los hallazgos preservados de hardening permanecen abiertos y no bloquean esta
  decisión semántica salvo que una prueba V2 dependa de resolverlos.

## 12. Alcance de implementación posterior

Rutas candidatas, sujetas al plan escrito y TDD:

- `control_plane/survey.py`
- `control_plane/cli.py`
- `control_plane/git_guards.py`, solo para la excepción shallow condicionada
  al RED exacto descrito abajo
- `tests/test_core_survey.py`
- `tests/test_core_cli.py`
- `tests/test_core_git_guards.py`
- `.codex/control-plane.lock`
- `README.md`
- `skills/control-plane-git/SKILL.md`
- documentación gobernante y threat model directamente afectados
- footer repository-scoped del threat model, calculado al final

No se añade un módulo. No se tocan hooks, Adoption, CI ni dependencias. La
implementación del guard permanece fuera de alcance salvo una excepción
cerrada: si el RED exacto de Task 0 demuestra que un candidato unpublished en
un repositorio shallow devuelve `GG_UNPUBLISHED_UNIQUE_BRANCH`, se permite
únicamente una consulta estricta y acotada del estado shallow, dentro del mismo
budget agregado y antes de observar reachability, para devolver
`GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN`. Si el test ya es GREEN, falla por otra
causa o exige cualquier otro cambio del guard, se para y se reframa.

El router ha clasificado el frente como T2 estructurado. Antes de implementar
requiere, además de esta especificación aprobada:

- ADR para el corte V1 → V2;
- plan escrito;
- TDD con relevante tests;
- revisión independiente;
- full gate sobre bytes finales;
- reseal de locks y threat model.

## 13. Criterios de aceptación

1. La salida por defecto usa solo `schema_version=2` y
   `kind=RepositorySurveyV2`.
2. Ningún payload V2 contiene `only_in_branch`.
3. `added_paths` conserva exactamente el cálculo add-only existente cuando se
   observa; ante timeout/error propio vale `null` y no cambia el status.
4. Una rama que solo modifica un fichero, tiene commits únicos y carece de ref
   remota homónima produce `unpublished_unique=true`, agregado 1, `FAIL` y exit
   1.
5. Solo archivos sin rastrear, o solo stashes, producen `WARN`, `ok=false` y
   exit 3; nunca `FAIL`/1.
6. Si coexisten `unpublished_unique_branches > 0` y stashes/untracked,
   prevalecen `FAIL` y exit 1.
7. Una rama con ref remota homónima válida queda exenta aunque la ref esté
   atrasada.
8. Una rama con tree equivalente tras squash no es huérfana aunque aparezca
   adelantada por ancestry.
9. Una rama atrasada y sin commits únicos no es huérfana aunque su tree difiera.
10. Base, remote, ref o reachability ambiguos producen `UNKNOWN` y exit 2, sin
    inventar ceros, booleanos ni colecciones vacías para evidencia no probada.
11. Un timeout exclusivo de `added_paths` no produce `UNKNOWN`; el deadline
    compartido impide acumular hasta 64 timeouts de 10 segundos.
12. Los escenarios cruzados de Survey y guard satisfacen la tabla de paridad;
    el caso shallow demuestra RED exacto, cambio mínimo y GREEN si la ruta
    actual devuelve `GG_UNPUBLISHED_UNIQUE_BRANCH`.
13. La observación sigue sin mutar repo, refs, worktrees, index, stash o config.
14. El inventario Core permanece en 27 módulos, sin dependencias ni cambios CI.
15. Renderer, wrapper y consumidores internos distinguen los cuatro estados;
    `WARN` nunca se serializa ni termina como `FAIL`.
16. Locks, documentación y footer del threat model coinciden con los bytes
    finales; relevante tests, full gate y revisión independiente quedan verdes.

## 14. Rollback

No hay migración de datos ni estado persistente. Antes de integrar, se descarta
el frente conservando el worktree o se crea un commit inverso autorizado; no se
usa reset, clean ni force push. Después de integrar, el rollback es un revert
revisado que restaura V1 de forma completa —runtime, tests, docs, lock, snapshot
y, si se activó la excepción condicional, el ajuste shallow del guard—, nunca
una mezcla V1/V2.

## 15. Alternativas rechazadas

### Reinterpretar `only_in_branch` como commits dentro de V1

Rechazada porque cambia silenciosamente un campo existente y no resuelve por sí
sola el status ni la publicación remota.

### Mantener V1 por defecto y añadir V2 opt-in

Rechazada porque el comando recomendado seguiría dando `PASS` ante el riesgo
que motivó el frente y duplicaría todos los caminos terminales.

### Mantener `orphan_work` solo para stashes/untracked y crear otra sección

Rechazada porque conserva dos definiciones de trabajo huérfano en el mismo
producto. El nombre debe abarcar todas las copias locales únicas observables.

### Conservar `FAIL=1` para toda clase de trabajo huérfano

Rechazada porque mezcla una señal de preservación remota crítica con residuos
locales visibles y frecuentes. El resultado histórico es un FAIL permanente
que los operadores aprenden a ignorar. `WARN=3` conserva el carácter no-cero
sin ocultar el riesgo de rama que permanece en `FAIL=1`.

### Hacer obligatorio `added_paths`

Rechazada porque no participa en el predicado de publicación y puede requerir
una comparación Git por rama. Un dato forense opcional no debe transformar una
respuesta normativa completa en `UNKNOWN` ni multiplicar el timeout por el
número de ramas.

### Hacer que Survey invoque el guard pre-push

Rechazada porque mezcla diagnóstico read-only con contexto de una transición.
La definición se comparte por contrato y tests, no mediante una dependencia de
ejecución.

## Continuación

- **Escribe en:** este hilo.
- **Rol:** orquestadora y ejecutora principal.
- **Para continuar:** ejecutar Task 7 sobre los bytes congelados, reseñar el
  footer y completar solo los gates finales previstos.
- **Mensaje exacto:** `Continúa con Task 7: congela bytes, reseña el threat footer y ejecuta los gates finales sin ampliar alcance.`
- **Estado de partida:** `RepositorySurveyV2` es
  `IMPLEMENTED_LOCAL_CANDIDATE / FINAL_GATE_PENDING`; Tasks 1–5 produjeron el
  candidato local y la evidencia final, la revisión independiente, el footer y
  cualquier estado remoto permanecen pendientes. `authorizes=false`.
