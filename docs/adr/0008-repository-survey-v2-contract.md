# ADR 0008: RepositorySurveyV2 separa preservación crítica y residuo local

- Estado: accepted para el contrato base de `RepositorySurveyV2`
- Fecha: 2026-08-21
- Responsables: tarea orquestadora del Control Plane
- PR: pendiente
- Sustituye: ningún ADR; sustituirá el contrato `RepositorySurveyV1` del diseño 3.3 cuando se integre
- Sustituido por: ninguno
- Enmienda de alcance shallow: accepted 2026-08-21; excepción condicional al
  RED exacto

## Contexto

La [especificación RepositorySurveyV2](../superpowers/specs/2026-08-21-repository-survey-v2-design.md)
fue aprobada expresamente para ADR y planificación, sin autorización de
implementación. El [diseño 3.3](../superpowers/specs/2026-08-18-control-plane-3-3-operator-orientation-design.md)
define `RepositorySurveyV1`: observa clon, worktrees, ramas, stashes y archivos
sin rastrear, pero su agregado `orphan_work` solo incluye las dos últimas clases.
Una rama local cuyo tree y commits difieren de la base, sin una ref remota
homónima, puede quedar inalcanzable tras un squash y el borrado automático de la
rama del Pull Request mientras Survey responde `PASS`.

El incidente que motiva la decisión ocurrió el 2026-08-20. El commit preservado
`d901bb6c95377074a7fb2fb23762476547335969` contenía más de 3.100 líneas de
hardening y solo seguía alcanzable en remoto a través de la rama de otro PR.
La rama `codex/survey-hardening-wip` se publicó antes del squash para impedir
la pérdida.
Loss Guards v1 cerró el hueco en pre-push con
`GG_UNPUBLISHED_UNIQUE_BRANCH`, pero Survey y el guard continuaron contestando
de forma distinta a la misma pregunta operativa.

V1 tiene además dos defectos de señal:

- `only_in_branch` cuenta paths añadidos, no commits ni todo el contenido único;
- stashes, un archivo sin rastrear y una rama única sin publicar producen el
  mismo `FAIL=1`, de modo que un residuo frecuente puede ocultar la señal de
  pérdida que exige parar.

El conteo add-only es informativo. Hacerlo obligatorio permitiría que hasta 64
comparaciones Git degradaran a `UNKNOWN` una observación normativa ya completa.
Este riesgo es material en almacenamiento lento o parcialmente materializado.

La adopción externa permanece prohibida por
[ADR 0006](0006-control-plane-core-and-quarantine.md). Los consumidores internos
observados no validan un conjunto cerrado de claves JSON, pero sí dependen de
los nombres Python, el discriminador V1 y los exit codes. Por tanto, cambiar la
semántica bajo V1 sería una incompatibilidad oculta; una transición V2 atómica
es más honesta.

## Decisión

Cuando se implemente mediante una autorización posterior,
`scripts/control-plane survey` emitirá por defecto y exclusivamente
`RepositorySurveyV2`, con `schema_version=2` y `kind=RepositorySurveyV2`. No se
mantendrá V1 en paralelo, no habrá `--schema-version` y `only_in_branch`
desaparecerá en lugar de reinterpretarse.

Para una base fijada `B`, una rama local `L` y un remote seleccionado `R`, una
rama es `unpublished_unique` si y solo si se cumplen las tres condiciones:

```text
tree(L) != tree(B)
and existe un commit alcanzable desde L y no desde B
and no existe refs/remotes/R/<L> como ref local válida a commit
```

La equivalencia de contenido, la reachability y el inventario remoto son
evidencia normativa. Se observan sobre OIDs fijados, sin red y con límites. La
ausencia de una ref remota solo es `false` cuando el inventario acotado prueba
esa ausencia. Ambigüedad, timeout o error en evidencia normativa produce
`UNKNOWN`, nunca ausencia inventada. En ese estado, listas y recuentos cuya
integridad no cerró se serializan como `null`, no como `[]` o `0`.

El estado y el exit code separan severidades:

| Estado | Exit | Significado |
|---|---:|---|
| `PASS` | 0 | No se observó trabajo huérfano dentro del clon seleccionado |
| `FAIL` | 1 | Existe al menos una rama `unpublished_unique` |
| `UNKNOWN` | 2 | La evidencia normativa no es íntegra |
| `WARN` | 3 | Hay stashes o archivos sin rastrear, pero no una rama `unpublished_unique` |

`UNKNOWN` prevalece cuando falta evidencia normativa; con evidencia completa,
`FAIL` prevalece sobre `WARN`. `ok=true` queda reservado a `PASS`. Mantener
`FAIL=1` y `UNKNOWN=2` conserva sus significados transversales; `WARN=3` sigue
siendo no-cero sin confundir residuo local con pérdida de rama.

`only_in_branch` se reemplaza por `added_paths: int | null`. El campo conserva
el cálculo add-only solo como enriquecimiento forense. Todas sus comparaciones
comparten un único deadline de 10 segundos. Timeout, error o presupuesto
agotado produce `null` en la rama afectada y en las no observadas después; no
cambia `status`, `error_code` ni el predicado normativo.

Survey y el guard pre-push compartirán el predicado y fixtures cruzadas, no una
dependencia runtime. El guard conserva su contexto de publicación en curso;
Survey sigue siendo una observación read-only sin esa exención. Ambos conservan
la limitación deliberada de confiar en refs remotas locales, que pueden estar
obsoletas respecto del servidor.

`other_clones` permanece `UNKNOWN`, `authorizes=false` permanece obligatorio y
Survey no se convierte en oráculo de borrado. No se añade módulo, dependencia,
red, mutación, capacidad de adopción ni cambio de CI.

## Alternativas

### Alternativa A: ampliar V1 de forma aditiva

- Ventajas: menor rotura inmediata para consumidores internos.
- Inconvenientes: conserva por defecto el status ciego o exige reinterpretarlo
  bajo el mismo discriminador; mantiene `only_in_branch` junto a un alias cuyo
  significado puede divergir.
- Motivo de descarte: oculta un cambio de contrato y duplica conceptos que ya
  indujeron una decisión operativa errónea.

### Alternativa B: V2 opt-in y V1 por defecto

- Ventajas: transición gradual y rollback trivial por flag.
- Inconvenientes: el comando recomendado sigue respondiendo con la semántica
  defectuosa; duplica payloads, tests, documentación y caminos terminales.
- Motivo de descarte: la adopción externa está prohibida y no existe evidencia
  que justifique mantener como default un contrato inseguro.

### Alternativa C: mantener un único `FAIL=1`

- Ventajas: no añade un cuarto estado al CLI.
- Inconvenientes: stashes y untracked frecuentes producen la misma señal que
  una rama en riesgo de quedar inalcanzable; los operadores aprenden a ignorar
  el rojo.
- Motivo de descarte: no corrige el fallo de señal que hizo V1 inútil para la
  decisión de preservación.

### Alternativa D: hacer obligatorio `added_paths`

- Ventajas: cada respuesta contiene el mismo nivel de detalle forense.
- Inconvenientes: un campo que no participa en el predicado puede multiplicar
  latencia o convertir una respuesta normativa completa en `UNKNOWN`.
- Motivo de descarte: la disponibilidad de decoración no debe gobernar una
  decisión de preservación.

### Alternativa E: invocar el guard desde Survey o compartir ejecutor

- Ventajas: una sola implementación aparente del predicado.
- Inconvenientes: acopla diagnóstico a una transición Git con política
  instalada, autoridad y exención de publicación en curso diferentes.
- Motivo de descarte: la paridad correcta es de contrato y fixtures, no de
  ejecución mutable.

## Consecuencias

### Positivas

- El status y el exit code distinguen una rama que puede perderse de residuos
  locales visibles.
- Survey y pre-push responden de forma coherente al mismo predicado de rama.
- La igualdad de trees evita falsos positivos después de squash.
- `added_paths=null` conserva la respuesta útil bajo almacenamiento lento.
- La transición es explícita y comprobable mediante discriminadores V2.

### Negativas

- Es un corte incompatible para nombres Python, payload y consumidores que
  asuman exactamente tres estados.
- El CLI y su renderer deben tratar `WARN=3` de forma explícita; `ok` por sí
  solo ya no conserva la severidad.
- La observación necesita inventarios de refs, trees y reachability más ricos.
- Durante rollback no puede coexistir una mezcla de runtime V2 y documentación
  V1.

### Riesgos

- **Ref remota local obsoleta.** Una ref homónima exime aunque el servidor ya no
  la conserve. Se acepta como límite local; cualquier afirmación remota exige
  evidencia host separada.
- **Deriva entre Survey y guard.** Dos implementaciones pueden separarse. Se
  mitiga con una tabla normativa y fixtures cruzadas, no importando módulos.
- **Paridad shallow aún no demostrada.** La inspección estática del guard
  muestra que su ruta de rama no publicada no consulta por sí sola el estado
  shallow. El primer RED de implementación debe probar la contradicción. Solo
  si devuelve exactamente `GG_UNPUBLISHED_UNIQUE_BRANCH` se permite añadir al
  guard una consulta shallow estricta, candidate-only y dentro de su budget
  agregado para obtener `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN`; un GREEN previo,
  otro RED o un cambio más amplio detienen el frente. Esta enmienda amplía el
  file scope de implementación, no adapta el predicado ni la tabla al runtime.
- **WARN ignorado.** Exit 3 es no-cero y `ok=false`; documentación y tests deben
  impedir que el wrapper lo convierta en PASS o FAIL.
- **Enriquecimiento costoso.** Un deadline único y nullable impide 64 timeouts
  acumulados.
- **Scope creep de hardening.** Gitlinks, alternates, TOCTOU general,
  case-folding APFS, paths con salto de línea y otros hallazgos preservados
  continúan en `codex/survey-hardening-wip`.

## Seguridad y privacidad

Survey continúa sin red, sin hooks y sin mutaciones. Usa Git cerrado y acotado,
no lee contenido de producto ni secretos y no sigue enlaces para inferir el
estado. OIDs, refs y trees son datos no confiables que se validan antes de
participar en el resultado.

La evidencia normativa incompleta falla cerrada como `UNKNOWN`. El campo
opcional incompleto se marca `null`; nunca se presenta como cero. Ninguna
salida concede autoridad para limpiar, borrar, hacer push o fusionar. Una
historia shallow no se trata como reachability completa, y una excepción solo
expone un código Survey permitido y un mensaje estable, nunca su texto
arbitrario. La comprobación condicional del guard solo ocurre después de filtrar
por tree, ref remota y publicación exacta, y consume el tiempo restante del
mismo deadline; no añade otro presupuesto.

## Migración y compatibilidad

La migración es atómica dentro de un único cambio revisable:

1. ejecutar el RED shallow exacto y, solo si produce el error previsto, aplicar
   el cambio mínimo del guard y demostrar GREEN;
2. introducir tests V2 y demostrar RED sobre V1;
3. actualizar modelo, observación y payload;
4. migrar CLI, renderer y consumidores internos a cuatro estados;
5. alinear fixtures con el guard sin añadir dependencia entre runtimes;
6. actualizar documentación gobernante, lock y threat snapshot;
7. ejecutar focales, un full gate final y revisión independiente sobre bytes
   congelados.

No existe migración de datos persistentes. El rollback posterior es un revert
completo de runtime, tests, documentación, sellos y, si llegó a activarse, la
excepción shallow del guard; no se mantiene un modo dual. Este ADR no cambia
por sí mismo la versión publicada, la cuarentena de ADR 0006 ni
`external_consumer_adoption=PROHIBITED`.

## Validación

La decisión se considera implementada solo si:

- cada una de las tres condiciones del predicado tiene contraejemplo probado;
- `PASS=0`, `FAIL=1`, `UNKNOWN=2` y `WARN=3` aparecen en JSON, salida humana y
  proceso con `ok` coherente;
- una rama solo modificada y sin ref remota produce `FAIL=1`, mientras
  untracked o stash sin esa rama produce `WARN=3`;
- la misma fixture satisface la tabla de paridad de Survey y pre-push, y el caso
  shallow termina como `GG_UNPUBLISHED_BRANCH_STATE_UNKNOWN` después de la
  secuencia RED exacto → cambio mínimo → GREEN cuando corresponda;
- timeout exclusivo de `added_paths` produce `null` y conserva el status;
- el deadline opcional es compartido, no multiplicado por rama;
- no hay mutación, red, dependencia, módulo, cambio CI ni ampliación de
  autoridad;
- el Core permanece en 27 módulos, el lock valida, el threat snapshot coincide
  y el full gate final pasa sobre los bytes revisados.

El estado de la enmienda lo determina exclusivamente su línea de metadata:
mientras sea `proposed`, el `accepted` general cubre solo el contrato base y
una instrucción nativa debe aceptar la ampliación sobre los bytes exactos antes
de cualquier edición runtime. Si Task 0 observa esa instrucción y reemplaza la
metadata por `Enmienda de alcance shallow: accepted 2026-08-21; excepción
condicional al RED exacto`, esa nueva línea registra su aceptación antes del
primer commit documental.

Este ADR y su plan son `authorizes=false`: ni el estado del documento ni esa
transición registral autorizan por sí mismos la implementación. Las
transiciones Git se rigen por la policy integrada en la base protegida.
Adopción, instalación, deploy y release conservan sus gates.
