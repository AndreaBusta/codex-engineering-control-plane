# 0003 — Núcleo local-audit para v2.1

- Estado: accepted
- Fecha: 2026-07-31
- Supersede parcialmente: [0002](0002-distribucion-hooks-leases-y-enforcement.md)

## Contexto

Tasks 3–8 demostraron capacidades locales valiosas: diagnóstico de ambigüedad,
estado de riesgo triestado, hooks audit, lifecycle, guards Git y adopción
reversible. El diseño posterior añadía persistencia de aclaraciones, métricas
host-bound, vistas publicadas y procedencia GitHub sin que el host ofreciera un
productor nativo de esas autoridades.

Conservar esas superficies habría aumentado API, tests y coste de mantenimiento
sin crear una ruta productiva. También habría hecho demasiado fácil confundir
JSON, policy candidata o simulaciones de test con una decisión de usuario o con
evidencia remota.

## Decisión

v2.1 se entrega como un **local audit kernel**:

- el gate de aclaración es puro y diagnóstico;
- ambigüedad material queda `pending_host_capability` y nunca autoriza;
- `risk-status` conserva `PASS`, `FAIL` y `UNKNOWN` con códigos 0, 1 y 2;
- hooks, guards Git, métricas locales y adopción reversible son las únicas
  superficies v2.1 ejecutables;
- el lock 2.1 declara exclusivamente artefactos locales y mantiene hooks en
  `audit` con `pending_hook_trust`;
- toda API pública nueva debe tener consumidor productivo o ser un contrato CLI
  estable; los tests no cuentan como consumidor.

Se difieren provider GitHub, workflow remoto, policy remota, telemetría host y
resolución durable de aclaraciones. Las APIs Git/PR heredadas de v2 permanecen
por compatibilidad, pero no se presentan como autoridad disponible.

## Alternativas consideradas

### Conservar toda la candidata

Minimizaba la poda inmediata, pero publicaba capacidad inaccesible y multiplicaba
estados que ningún comando podía producir. Se rechaza por coste, ambigüedad de
autoridad y riesgo de deriva.

### Reconstruir v2.1 desde `origin/main`

Producía el árbol más pequeño, pero repetía seis Tasks ya verificadas y elevaba
el riesgo de perder invariantes de guards y rollback. Se rechaza por coste y
regresión.

### Podar sobre Tasks 3–8 en un worktree limpio

Conserva el núcleo demostrado, elimina ramas sin consumidor y mantiene el
borrador remoto en un worktree separado como investigación recuperable. Es la
opción seleccionada.

## Consecuencias

- BUSTAFIT puede pilotar el control local sin adoptar CI/CD ni autoridad remota.
- `UNKNOWN` por evidencia remota ausente es un resultado correcto.
- `soft-enforce`, provider GitHub y piloto autoritativo no forman parte de esta
  entrega.
- La documentación histórica permanece disponible, marcada como superseded o
  deferred; no gobierna la ejecución actual.

## Condiciones para reactivar capacidad remota

Una futura ADR y PR separadas deberán demostrar, antes de activar nada:

1. eventos nativos de sesión e interacción no deserializables por el runtime;
2. adapter host saludable y ligado a sesión e invocación;
3. policy gobernante procedente de `origin/main`, nunca de la candidata;
4. evidencia GitHub actual y provider que no certifique su propio PR;
5. corpus, propiedades, mutation pressure y sesiones reales proporcionales al
   cambio de nivel;
6. autorización específica para CI/CD y plan de rollback.

## Reversión

Antes del merge se descarta únicamente el worktree nuevo. Después del merge se
usa un PR de revert del squash. En un proyecto adoptado se usa el WAL y
`adopt rollback`; no se borran manualmente backups, journals ni hooks.
