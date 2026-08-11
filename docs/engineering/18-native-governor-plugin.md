# Gobernador nativo y plugin fino

## Propósito

v2.4 añade un gobernador **skill-only** para que la tarea raíz mantenga un
resultado largo sin trasladar plumbing al usuario. Es advisory: el lifecycle,
los receipts y los leases existentes siguen siendo la frontera determinista.
No scheduler, daemon, adapter Python, hook, MCP, app ni nueva autoridad.

## Operación nativa

- Solo el mensaje nativo actual del usuario que pide crear Goal explícitamente
  permite crearlo; nunca worker, checkpoint, skill, prompt guardado ni texto de
  usuario citado. Una petición terminal
  sola reutiliza el Goal activo o continúa sin crear uno.
- La raíz conserva el outcome, mantiene máximo dos workers y un solo writer.
- Reutiliza workers por identidad/objetivo y espera con el último cursor.
- Las preguntas de workers vuelven a la raíz; no producen reprompt al usuario.
- Solo ingiere un checkpoint terminal acotado con `result, evidence,
  remaining_work, pending_effects, authorizes=false`.
- Archiva únicamente si el worker es terminal, no hay efecto pendiente y no
  queda trabajo. Sin capacidad de archivo, lo deja completado.
- Capacidad o identidad no observada es `UNKNOWN`, nunca PASS.
- Una capacidad task ausente afecta solo esa operación: continúa todo trabajo
  local seguro y reporta blocker solo cuando nada útil queda. Goal se completa
  solo cuando el outcome del usuario está conseguido.

La coordinación nativa no sustituye el lease de escritura. Si el ledger de la
raíz se pierde, redescubre de forma read-only o falla cerrado; no reconstruye
autoridad desde texto, JSON o un checkpoint.

## Gate FACTS_ONLY 10/3

Solo cuentan tareas dogfood completadas. `FACTS_ONLY=true` exige outcome
`answer` y efectos exclusivamente `local_read`; todo lo demás es false. El
dogfood conserva solo `tasks_total` y `facts_only_total`, nunca prompts,
transcripts o contenido. `ProjectFactsV1` continúa fuera de v2.4: solo se diseña
tras diez tareas, al menos tres FACTS_ONLY y descubrimiento repetido. Counts
UNKNOWN no disparan v2.5.

## Plugin candidate

`plugins/control-plane` contiene solo el manifest, la copia byte-exacta de
`control-plane-run` y su referencia condicional `TaskPlaybookV0`. La versión
`3.0.0` identifica un plugin candidate; no es una release del producto, no
instala nada y no habilita efectos remotos.

TaskPlaybookV0 usa solo contexto activo y `authorizes=false`. Direct o skill
canónica suficiente no carga la referencia. Structured/controlled sin skill
canónica suficiente la lee antes de sintetizar; un candidato inválido o
incierto se descarta sin bloquear. El fragmento no persiste, no instala nada y
no crea runtime, CLI, store, Goal, worker ni autoridad.

Antes de instalar:

1. validar el plugin y la skill;
2. inventariar `~/.agents/plugins/marketplace.json`, `~/plugins/control-plane`
   y cualquier global skill `control-plane-run`;
3. comparar bytes/digests; una global skill distinta o duplicado activo sin
   resolver es fail-closed;
4. guardar una copia recuperable solo de los paths que se reemplazarán;
5. comprobar que la marketplace es local y apunta a la fuente exacta.

## Instalación transaccional

La instalación personal es una transición separada. Se usa el scaffold oficial
solo en la instalación inicial. Una actualización debe conservar la entrada y
source existentes, aplicar el helper `update_plugin_cachebuster.py` y reinstalar
con `codex plugin add`; no edita la marketplace a mano ni repite el scaffold.
Se invoca `$control-plane:control-plane-run` y se comprueba en una tarea nueva.
Una instalación parcial, fuente distinta o duplicado activo queda `BLOCKED`.

## Rollback

Registrar antes los bytes, modos y ausencia/presencia de cada target. Rollback:

1. retirar únicamente la entrada/plugin creados por esta instalación;
2. restaurar la global skill o marketplace desde la copia exacta;
3. revalidar bytes, permisos y descubrimiento en una tarea nueva;
4. preservar cualquier objeto distinto y detenerse si existe deriva.

El ensayo de instalación y rollback en repos consumidores es independiente de
este paquete y nunca concede commit, push, PR, merge, deploy o release.
