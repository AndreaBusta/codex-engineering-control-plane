# Modelo operativo

## Propósito

Este modelo convierte una petición en una unidad de trabajo verificable. Codex
puede recibir prompts incompletos; su responsabilidad es normalizarlos sin
alterar el resultado que busca el usuario.

## Tres capas

### Juicio

El agente comprende intención, incertidumbre, dependencias y riesgo. Decide:

- si hay uno o varios objetivos;
- el nivel T0–T3;
- si necesita investigar;
- el razonamiento recomendado;
- secuencial o grafo;
- documentación aplicable.

Estas decisiones no deben fingirse como exactas. Se explican con hechos y
supuestos relevantes.

### Policy

`.codex/project-policy.toml` contiene las reglas no deducibles:

- remote y rama base;
- Pull Request obligatorio;
- modelo y razonamientos;
- máximo de workers;
- gates por nivel;
- fuente de release.

Un cambio de policy es un cambio de comportamiento y requiere tests y PR.

### Gates

Los gates observan hechos. Pueden demostrar que una rama es real, que el árbol
está limpio o que HEAD coincide con una referencia remota almacenada. No pueden
demostrar una aprobación humana ni el estado actual de un proveedor sin
consultarlo.

## Encuadre de una petición

Antes de editar, producir este contrato:

- objetivo;
- criterios de aceptación;
- alcance;
- exclusiones;
- restricciones;
- estado inicial;
- riesgos;
- verificaciones;
- autoridad externa;
- definición de terminado.

Una ambigüedad material bloquea. Una ambigüedad menor se resuelve con un
supuesto explícito y reversible.

## Detección multifrente

Usar `PROMPT_MULTIFRONT` cuando la petición mezcla objetivos independientes.

Ejemplo:

```text
cambiar onboarding
+ migrar autenticación
+ añadir pagos
+ rediseñar estadísticas
```

La respuesta no es cuatro writers en una rama. El director:

1. separa unidades;
2. traza dependencias;
3. clasifica cada unidad;
4. asigna ramas;
5. limita concurrencia;
6. mantiene un posible frente de integración solo para pruebas conjuntas.

Separar cuando dos o más criterios coincidan:

- objetivo funcional distinto;
- riesgo distinto;
- módulo o contrato distinto;
- prueba distinta;
- reversión o release independiente;
- mezcla de cambio crítico y cosmético;
- PR imposible de resumir en una frase.

### Rama limpia

Se puede dedicar a la primera unidad coherente.

### Rama con un frente

Se conserva ese alcance. Los nuevos objetivos se convierten en tareas
posteriores.

### Rama mezclada

Entrar en rescate de alcance:

1. no editar más;
2. inventariar archivos y hunks;
3. identificar dependencias compartidas;
4. crear una referencia recuperable cuando esté autorizado;
5. separar de forma no destructiva;
6. comparar cada rama con el estado de rescate.

## Clasificación

### T0

Cambio pequeño, evidente, reversible y localizado. Ejemplos: copy, ajuste
visual mínimo, nombre privado.

Definition of Done:

- cambio correcto;
- validación localizada;
- diff revisado;
- documentación evaluada;
- PR ligero si va a base protegida.

### T1

Tarea normal con patrón conocido. Ejemplos: pantalla sencilla, bug localizado,
evento analítico.

Definition of Done:

- aceptación satisfecha;
- tests relevantes;
- diff revisado;
- documentación afectada;
- rama remota y PR cuando estén autorizados.

### T2

Varias capas, causa incierta, cambio amplio o arquitectura local.

Definition of Done:

- plan;
- baseline y tests amplios;
- revisión independiente;
- documentación;
- integración demostrada.

### T3

Auth, autorización, pagos, datos privados, migración, producción, firma,
TestFlight, secretos o difícil reversión.

Definition of Done:

- plan controlado;
- threat model cuando aplique;
- seguridad;
- rollback;
- pruebas de regresión;
- revisión independiente;
- autorización externa;
- release proof y observación.

## Estados

```text
framed → planned → ready → implementing → verifying
→ review_ready → committed → pushed → pr_draft → pr_ready → merged
→ base_verified → release_pending → released → observed → closed
```

`blocked` puede alcanzarse desde cualquier estado. Para salir se identifica el
gate concreto; no se salta.

## Cómo responde Codex

Antes de trabajar:

> He separado la petición en dos unidades porque pueden revisarse y revertirse
> por separado. Esta rama se dedicará a autenticación. Estadísticas queda como
> frente independiente. Ejecutaré secuencialmente los cambios que comparten
> contratos y solo paralelizaré lectura o pruebas disjuntas.

Ante un gate:

> La implementación está lista, pero todavía no está en main. HEAD local no
> coincide con la rama remota, por lo que el PR aún no contiene el último
> cambio.

Ante evidencia externa ausente:

> El código local y los tests están verificados. El estado de GitHub/TestFlight
> es `pending_external_evidence`; no afirmaré integración o publicación hasta
> consultar la fuente autorizada.

## Cambio de alcance

Si aparece trabajo nuevo:

1. pausar;
2. encuadrarlo;
3. decidir si es requisito del objetivo original;
4. si no lo es, crear Issue o unidad independiente;
5. reclasificar si aumenta riesgo o radio de impacto;
6. actualizar plan y gates.

No incorporar mejoras “ya que estamos”.
