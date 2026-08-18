# Backend — <título>

Pack: `SPEC-EXAMPLE-001`. Estado: `draft`. `authorizes=false`.

Si el producto no tiene backend, escribirlo de forma explícita y justificarlo.
No inventar arquitectura.

## Entidades

| ID | Entidad | Campos | Claves | Nulabilidad |
|---|---|---|---|---|
| `BE-D-001` | | | | |

## Endpoints

Todo endpoint es consumido por un paso de `APP_FLOW.md` o se marca `internal`.

| ID | Método | Ruta | Entrada | Salida | Errores | Consumo |
|---|---|---|---|---|---|---|
| `BE-E-001` | | | | | | `FLOW-T-001` |

## Autorización

Matriz rol por endpoint, sin celdas vacías. Es la comprobación de mayor
rendimiento del pack: obliga a decidir quién puede llamar a qué.

| Endpoint | Anónimo | Usuario | Propietario | Administrador |
|---|---|---|---|---|
| `BE-E-001` | | | | |

## Migraciones

Orden, reversibilidad y compatibilidad con clientes antiguos.

## Idempotencia

| Endpoint mutante | Clave | Ventana |
|---|---|---|
| | | |

## Taxonomía de errores

| Código | Causa | Mensaje al cliente | Recuperación |
|---|---|---|---|
| | | | |

## Datos personales

Clasificación, retención y borrado. `no aplica` con justificación si procede.

## Límites

Tasa, tamaño de carga y tiempo de espera.

`authorizes=false`
