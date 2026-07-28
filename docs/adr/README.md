# Architecture Decision Records

Los ADR registran decisiones estructurales duraderas con alternativas reales.

## Nombres

```text
NNNN-titulo-corto.md
```

Usar numeración creciente. No reutilizar números.

## Estados

- proposed;
- accepted;
- deprecated;
- superseded.

Una decisión reemplazada conserva su historia y enlaza el ADR nuevo.

## Cuándo crear

- arquitectura;
- datos;
- auth;
- API;
- navegación;
- sincronización;
- almacenamiento;
- dependencia estructural;
- observabilidad;
- seguridad;
- release difícil de revertir.

No crear para aplicar un patrón aceptado, copy, estilo o bug sin decisión nueva.

## Revisión

El PR enlaza el ADR. La revisión comprueba que contexto, alternativas,
consecuencias, seguridad y migración reflejan la realidad.
