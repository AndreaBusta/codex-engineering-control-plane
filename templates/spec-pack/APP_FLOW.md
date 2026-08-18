# Flujo de la app — <título>

Pack: `SPEC-EXAMPLE-001`. Estado: `draft`. `authorizes=false`.

Se declara como máquina de estados explícita, no como narración.

## Estado inicial

Una única pantalla de entrada, declarada en `UX_UI.md`.

- Entrada: `UX-S-001`

## Transiciones

Ambas pantallas de cada transición deben existir en `UX_UI.md`. Toda transición
con efecto declara el endpoint que invoca.

| ID | Origen | Evento | Destino | Guarda | Invoca |
|---|---|---|---|---|---|
| `FLOW-T-001` | `UX-S-001` | | `UX-S-002` | | `BE-E-001` |

## Rutas de fallo

Toda transición con efecto remoto declara su rama de error.

| Transición | Fallo | Destino | Recuperación |
|---|---|---|---|
| `FLOW-T-001` | | | |

## Estados terminales

Salida, cierre de sesión y error irrecuperable. Al menos uno.

`authorizes=false`
