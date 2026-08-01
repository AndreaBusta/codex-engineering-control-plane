# Consolidación de Control Plane v2.1 como local-audit

## Estado y objetivo

Este plan sustituye para la entrega v2.1 las Tasks 9–15 del plan de 2026-07-29.
Conserva Tasks 3–8 y reduce la superficie a capacidad local con consumidor real.
No autoriza commit, push, PR, merge, CI/CD ni adopción externa.

Resultado esperado:

- una PR `Control Plane v2.1 local audit kernel`;
- runtime y lock 2.1 exclusivamente locales;
- documentación alineada con la capacidad ejecutable;
- handoff honesto en `audit`;
- piloto BUSTAFIT posterior, aislado y reversible, hasta `review_ready`.

## Invariantes

- Un writer, ejecución secuencial, máximo dos revisores cuando exista
  independencia.
- TDD: RED causal, cambio mínimo, GREEN focalizado y suite integral.
- Ninguna API v2.1 pública sin consumidor productivo o contrato CLI estable.
- JSON, Markdown, policy candidata y tests nunca crean autoridad host.
- Sin dependencias, secretos, CI/CD, deploy o release.
- Worktrees históricos y el borrador remoto se retienen sin limpieza implícita.

## Fase A — Preservación

1. Hashear el worktree borrador y conservarlo byte-exacto.
2. Suspender su task mediante el runtime propietario y liberar la lease de forma
   gobernada.
3. Podar solo metadata `prunable` demostrada y autorizada.
4. Crear `codex/control-plane-v2-1-local-audit` desde `66373bc` en worktree
   limpio y demostrar su descendencia de `origin/main`.
5. Crear task T3 con lease exacta, ejecutar preflight write y baseline completo.

## Fase B — Contrato y poda

1. Caracterizar comandos, imports heredados, hooks, triestado, guards,
   adopción y ausencia remota.
2. Reducir aclaración a validación y gate puros.
3. Eliminar sidecars, resolución durable y objetos host sin productor.
4. Limitar métricas a payload local.
5. Conservar una sola ruta productiva de hooks y el clasificador mínimo usado
   por ella.
6. Mantener guards Git, WAL, snapshots, fsync, upgrade y rollback.

## Fase C — Distribución y documentación

1. Seleccionar `product_version = 2.1.0` y schemas de aclaración/riesgo.
2. Bloquear digests de runtime, hooks, launchers y guards locales.
3. Demostrar que adopción no distribuye workflows ni providers remotos.
4. Registrar la decisión en ADR 0003 y publicar la guía local-audit.
5. Marcar las Tasks 9–15 históricas como superseded/deferred.

## Fase D — Verificación

Gates focales:

- aclaración/routing;
- `risk-status` 0/1/2;
- hook entrypoint real y smoke macOS;
- métricas locales;
- guards Git;
- adopción/upgrade/rollback;
- lock, distribución y ausencia remota.

Gates integrales:

```bash
bash tests/run.sh
scripts/control-plane policy-check --policy .codex/project-policy.toml
scripts/control-plane registry-check \
  --registry .codex/resource-registry.toml \
  --policy .codex/project-policy.toml
scripts/control-plane doctor
git diff --check
git status --short --branch
```

Además se exige un ciclo temporal `plan → apply → verify → rollback`,
restauración byte-exacta y dos revisiones independientes: seguridad y
mantenibilidad/proporcionalidad.

## Fase E — Integración

1. Solicitar autorización de commit para dos unidades coherentes:
   `Consolidate v2.1 around the local audit kernel` y
   `Align v2.1 local distribution and documentation`.
2. Solicitar por separado push, creación de PR y squash merge.
3. Refrescar `origin/main`, demostrar el squash y repetir gates.
4. Cerrar task y lease con su runtime propietario; conservar worktrees salvo
   autorización explícita de limpieza.

## Fase F — Piloto BUSTAFIT posterior al merge

Crear un worktree aislado desde el `origin/main` vigente de BUSTAFIT, nunca
modificar el checkout `codex/routines`. Tras baseline verde:

1. revisar `adopt plan` desde la v2.1 fusionada;
2. con autorización, ejecutar apply y verify;
3. repetir gates BUSTAFIT;
4. ejecutar rollback y demostrar restauración byte-exacta;
5. con nueva autorización, reaplicar y verificar;
6. ejecutar cuatro escenarios: iOS read-only, Android+PWA, cambio
   multiplataforma y auth/producción crítica.

El estado terminal es `review_ready`, sin commit ni PR en BUSTAFIT.
`risk-status=UNKNOWN/2` por evidencia remota ausente es esperado.

## Criterio de parada

Detener ante base incompatible, baseline rojo, drift no explicado, hooksPath
conflictivo, necesidad de dependencia, secreto, CI/CD o cualquier consumidor
productivo real de una API candidata a poda. No ampliar silenciosamente la
entrega para resolver el bloqueo.
