# Adopción progresiva

## Objetivo

Introducir controles sin bloquear trabajo válido ni prometer enforcement que
todavía no existe.

## Nivel 0 — Inventario

- localizar `AGENTS.md`;
- inventariar skills duplicadas;
- validar config;
- identificar repositorios y ramas base;
- registrar comandos reales;
- identificar CI y release;
- no modificar remotos.

## Nivel 1 — Audit local

- añadir policy;
- añadir registry y lock;
- ejecutar `policy-check`;
- ejecutar `registry-check`, `inventory` y `route --mode audit`;
- usar preflight read/write;
- aplicar plantillas;
- medir falsos positivos;
- mantener hooks en audit y `pending_hook_trust` hasta revisión humana.

Un fallo produce diagnóstico, no una mutación automática.

## Nivel 2 — Enforce local

Estado v2.1: **diferido**. El kernel distribuido permanece en audit.

Requisitos:

- tests herméticos estables;
- repositorio con commit inicial;
- policy adaptada;
- comandos reales;
- equipo comprende recuperación.

Activar gates antes de escritura, commit o cierre mediante scripts o hooks
auditados. Los hooks siguen siendo defensa adicional y deben tener bypass
explícito y registrado para recuperación.

## Nivel 3 — Enforce remoto

Estado v2.1: **fuera de alcance**. Requiere adapter host, ADR, plan y
autorización CI/CD independientes.

Configurar con autorización:

- remote;
- CI;
- Ruleset de base;
- PR obligatorio;
- checks obligatorios;
- force push y borrado bloqueados;
- permisos mínimos;
- acciones fijadas por SHA.

La protección remota es autoritativa para integración.

## Nivel 4 — Release proof

- Xcode Cloud o pipeline SaaS desde `origin/<base>`;
- manifest;
- TestFlight/proveedor;
- recibo;
- smoke;
- observación;
- rollback.

## Migración de un proyecto

1. ejecutar `adopt plan` sin mutar el destino;
2. detectar rama base y remote como hechos del target, sin modificar el remote;
3. revisar el plan target-specific: policy, registry, `AGENTS.md`, hooks,
   rama base, remote y digests finales;
4. guardar el JSON aprobado y ejecutar `adopt apply --plan ...`;
5. ejecutar `adopt verify`;
6. ejecutar los gates reales del proyecto en modo audit;
7. revisar hooks con `/hooks`;
8. corregir falsos positivos;
9. ejecutar `adopt rollback` y demostrar restauración exacta;
10. reaplicar solo con nueva autorización y dejar el cambio en `review_ready`.

Commit, PR y cualquier promoción se deciden después, como transiciones
separadas. La adopción v2.1 no instala `.github/workflows/**`.

No copiar una policy `main` si el proyecto usa `develop`. No inventar scheme,
workspace o test command.

## Configuración global

Mantener global:

- seguridad;
- manejo de credenciales;
- router de workflow;
- modelo y razonamiento por defecto;
- concurrencia normal;
- memoria externa.

Mantener en proyecto:

- rama base;
- remote;
- comandos;
- gates;
- arquitectura;
- release.

## Skills

Evitar múltiples skills que compitan por el mismo trigger. Mantener:

- `task-framer` para encuadre;
- `decision-stress-test` para decisiones;
- `verified-workflow` para ejecución proporcional;
- skills de dominio solo cuando el proyecto las requiera.

Una regla mecánica pertenece a un gate, no a una skill.

## Plugins y MCP

Instalar o habilitar solo si:

- hay un caso de uso;
- el servidor es confiable;
- permisos son mínimos;
- credenciales se gestionan fuera de prompts;
- la salida puede verificarse.

GitHub MCP aporta evidencia remota. No reemplaza Git local ni Rulesets. Un MCP
de navegador ayuda a investigar, pero el gate final debe ser un test
determinista.

## Métricas

Tras un periodo:

- tareas por nivel;
- reintentos;
- workers;
- bytes de manifest, unidades seleccionadas y bytes de hook;
- gates fallidos;
- incidencias post-merge;
- releases con recibo;
- falsos positivos.

Estas magnitudes describen payload local; no son telemetría exacta de tokens.
No afirmar ahorro económico hasta medirlo fuera del runtime.

## Criterio para avanzar

Subir de nivel solo si:

- el nivel anterior es estable;
- existe rollback;
- los responsables entienden los fallos;
- la automatización no oculta autoridad;
- hay evidencia reproducible.

## Estado actual de este repositorio

Nivel 1 `local-audit` v2.1:

- policy, registry, lock, router, lifecycle y leases locales;
- aclaración diagnóstica sin resolución durable;
- Risk Sentinel triestado, guards Git y adopción/upgrade transaccionales;
- hooks en `audit` y `pending_hook_trust`;
- provider GitHub, workflow de procedencia y policy remota diferidos;
- GitHub, CI y release permanecen `pending_external_evidence` cuando una tarea
  concreta los requiera;
- la adopción en otros repositorios exige piloto separado y reversible.

Estas limitaciones son estados explícitos, no permisos implícitos.
