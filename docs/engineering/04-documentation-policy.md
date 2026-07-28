# Política de documentación

## Principio

Documentar decisiones y operaciones que deben sobrevivir a la conversación.
No generar documentos por reflejo. Cada artefacto tiene un disparador, un dueño
y una condición de actualización.

Antes de cerrar una tarea, registrar una evaluación:

```text
artifact
required: yes/no
reason
path or external reference
status
```

## Matriz

| Situación | Plan | ADR | Issue | Arquitectura | Runbook | Threat model | Rollback |
|---|---:|---:|---:|---:|---:|---:|---:|
| Copy/UI pequeña | no | no | si queda trabajo | no | no | no | no |
| Bug localizado | breve si incierto | normalmente no | si queda deuda | si cambia contrato | no | según impacto | según impacto |
| Feature normal | breve | si decide patrón | según pendientes | si añade flujo | según operación | según superficie | según riesgo |
| Refactor amplio | sí | frecuente | sí | sí | no | según impacto | recomendable |
| Auth/pagos/datos | sí | sí | sí | sí | sí | sí | sí |
| Migración | sí | sí | sí | sí | sí | sí | obligatorio |
| Release | release plan | según cambio | según pendiente | si cambia sistema | sí | según superficie | sí |

## Commit

Explica qué cambio introduce un checkpoint. Debe ser:

- coherente;
- pequeño;
- revisable;
- revertible.

No es almacén principal de decisiones, riesgos ni tareas pendientes.

## Pull Request

Es el expediente de una integración:

- problema;
- resultado;
- alcance y exclusiones;
- cambios;
- pruebas;
- riesgo;
- rollback;
- documentación;
- capturas;
- follow-ups.

El PR enlaza artefactos duraderos; no duplica sus contenidos completos.

## Plan

Crear para T2/T3 o T1 incierta. Incluir:

- aceptación;
- diseño;
- archivos;
- pasos;
- tests;
- riesgos;
- rollback;
- autoridad.

Actualizar cuando el alcance cambia materialmente. Cerrar indicando qué se
ejecutó y qué quedó fuera.

## ADR

Crear un Architecture Decision Record cuando:

- existe una decisión estructural duradera;
- hay alternativas razonables;
- la reversión será costosa;
- cambia auth, datos, navegación, sincronización, API, almacenamiento,
  observabilidad o dependencia estructural.

No crear ADR para:

- aplicar una decisión ya vigente;
- copy o padding;
- bug sin cambio de diseño;
- una prueba adicional.

Contenido:

- contexto;
- decisión;
- alternativas;
- consecuencias;
- seguridad;
- migración;
- estado.

Un ADR no se reescribe para fingir que el pasado fue distinto. Se marca
superseded y se enlaza el nuevo.

## Issue

Crear cuando se descubre trabajo válido fuera del alcance:

- bug;
- deuda;
- investigación;
- riesgo aceptado;
- mejora;
- seguimiento de seguridad.

Una Issue no puede diferir un requisito necesario para que el cambio actual sea
seguro.

## Arquitectura

Actualizar cuando cambia la realidad:

- módulos;
- fronteras;
- flujo de datos;
- dependencias;
- contratos;
- seguridad;
- despliegue.

Evitar diagramas que no puedan mantenerse.

## Runbook

Crear o modificar cuando una operación repetible cambia:

- release;
- rollback;
- restauración;
- rotación;
- incidente;
- recuperación Git;
- migración.

Debe incluir precondiciones, pasos, evidencia, fallos, escalado y reversión.

## Threat model

Activar para auth, autorización, pagos, datos personales, uploads, APIs
públicas, enlaces externos, administración o integraciones sensibles.

Actualizar la superficie afectada; no rehacer el sistema completo en cada PR.

## Rollback

Obligatorio para:

- migración;
- incompatibilidad de backend;
- pagos;
- auth;
- release progresiva;
- cambio difícil de revertir.

Debe distinguir:

- rollback de código;
- rollback de datos;
- desactivación por feature flag;
- compatibilidad de versiones;
- señal que lo activa.

## Estado de proyecto

Si existe `PROJECT_STATE.md`, mantener:

- estado actual;
- riesgos activos;
- hitos;
- decisiones pendientes;
- deuda relevante.

No convertirlo en un diario de commits.

## Release notes y recibo

Las notas explican el cambio a testers o usuarios. El recibo prueba la
procedencia técnica. Ambos pueden derivarse del PR, pero tienen audiencias
distintas.

## Gate documental

Antes de `review_ready`:

1. listar artefactos de la matriz;
2. justificar cada sí/no;
3. comprobar que los documentos describen el sistema real;
4. trasladar pendientes;
5. enlazar desde el PR.

La respuesta de cierre debe decir, por ejemplo:

> Plan actualizado. No se requiere ADR porque se aplicó el contrato vigente.
> Se creó una Issue para el follow-up no bloqueante. El runbook no cambia.
