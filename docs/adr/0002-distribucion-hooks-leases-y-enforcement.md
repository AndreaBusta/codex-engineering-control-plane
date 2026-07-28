# 0002 — Distribución, hooks, leases y promoción de enforcement

- Estado: accepted
- Fecha: 2026-07-28

## Contexto

Las reglas narrativas no garantizan que el agente ejecute preflight, respete el
worktree o deje recibo. Los hooks permiten observar el ciclo, pero se acumulan
entre capas, requieren confianza y no cubren todos los tools. Una instalación
global automática afectaría repositorios no evaluados y mezclaría autoridad
personal con policy de proyecto.

## Decisión

Versionar runtime, policy, registry, lock y hooks por proyecto. Mantener estado
efímero bajo el Git dir específico del worktree. Adoptar por:

```text
plan → apply → verify → status → rollback
```

El plan resuelve antes de escribir la rama base, remote, perfil, policy,
registry, bloque gestionado de `AGENTS.md` y hooks a fusionar. Queda ligado a
commit fuente, manifest, target, digests previos y `plan_id`. `apply` exige ese
plan exacto, usa exclusión mutua y una transacción recuperable. `rollback`
valida íntegramente antes de la primera mutación.

El runtime adoptado usa el namespace aislado
`codex_control_plane_runtime_v2`; ni el launcher ni el hook importan desde la
raíz del proyecto destino.

Los hooks empiezan en `audit`:

- `UserPromptSubmit`: añade manifiesto compacto sin persistir el prompt;
- `PreToolUse`: advierte el conjunto curado observable de efectos y operaciones
  peligrosas; no se presenta como cobertura universal;
- `Stop`: comprueba receipt sin crear bucles;
- `SessionStart` con `source=compact`: rehidrata solo el estado compacto antes
  de la continuación inmediata. No se usa `PostCompact` para inyectar contexto
  porque su contrato actual no expone esa salida.

Permanecen `pending_hook_trust` hasta revisión humana en `/hooks`. Timeout
máximo: tres segundos. Contexto del control plane: menos de 4 KiB.

Un `TaskLease` vincula tarea, worktree, rama, sesión, rutas y policy. Permite
continuar tras la primera edición legítima, pero no autoriza nuevos efectos.

Promoción:

```text
audit → soft-enforce → enforce
```

`soft-enforce` cubre primero estados mecánicos de alto riesgo. El enforcement
semántico solo se activa después de 100 TaskEnvelopes, detección crítica del
100 % y menos del 10 % de falsos positivos obligatorios.

## Alternativas descartadas

- Hooks globales automáticos: radio de impacto excesivo.
- Plugin como frontera de seguridad: la instalación y el trust no garantizan
  cobertura.
- Ledger compartido versionado: colisiones entre worktrees y riesgo de filtrar
  contexto.
- Árbol siempre limpio: impediría continuar una implementación legítima.
- Bypass de hook trust: oculta una decisión humana necesaria.

## Consecuencias

- Cada worktree mantiene estado independiente y recuperable.
- Un hook omitido no convierte una operación en segura.
- La configuración global exige diff y autorización separados.
- Un plugin privado puede empaquetar adopción cuando el núcleo se estabilice,
  pero no contendrá hooks autoritativos.
- macOS debe aprobarse manualmente antes de confiar hooks en ese sistema.

## Reversión

`adopt rollback` valida primero todos los digests y backups. Ante un solo drift,
realiza cero mutaciones. Si la validación pasa, entra en estado transaccional
reanudable y restaura. Desconfiar o desactivar hooks no elimina task state; el
flujo manual y los gates permanecen disponibles.
