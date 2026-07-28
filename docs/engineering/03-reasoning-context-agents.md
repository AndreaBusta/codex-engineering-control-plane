# Razonamiento, contexto y agentes

## Política base

- Modelo: GPT-5.6 Sol.
- Esfuerzo global recomendado: `high`.
- Planificación profunda: `xhigh`.
- Subagentes normales: `medium`.
- T0 localizado: `low`.
- T3: `xhigh`.
- Ejecución secuencial por defecto.
- Máximo normal: dos workers.

El esfuerzo se recomienda al iniciar una tarea o agente. No se afirmará que se
ha cambiado el razonamiento de una ejecución ya iniciada si Codex no ofrece ese
control.

## Elegir esfuerzo sin que el usuario clasifique

Evaluar:

- incertidumbre causal;
- sistemas y contratos afectados;
- reversibilidad;
- impacto en datos, seguridad o dinero;
- exposición externa;
- dificultad de verificar;
- intentos fallidos;
- novedad del patrón.

### Low

Para búsqueda localizada, explicación o cambio obvio de una sola pieza.

Escalar si aparece:

- contrato no documentado;
- más de un módulo;
- comportamiento no reproducible.

### Medium

Para T1 conocida, síntesis de investigación y subagentes con salida acotada.

### High

Para implementación normal de ingeniería, bugs con varias hipótesis, refactors
locales y coordinación T2.

### Xhigh

Para T3, arquitectura difícil, seguridad, migraciones, procedencia de release o
revisión crítica. No usarlo solo porque la conversación sea larga.

### Max y Ultra

No son valores automáticos:

- `max` solo para una cadena lógica excepcionalmente difícil;
- `ultra` solo cuando la plataforma pueda dividir trabajo independiente con una
  ganancia clara.

La tarea debe justificar el coste.

## Presupuesto de contexto

### Pequeño

- objetivo;
- diff;
- entre uno y cinco archivos;
- contrato directo;
- test afectado.

### Medio

- módulo;
- interfaces vecinas;
- tests;
- arquitectura relevante;
- historial puntual.

### Grande y segmentado

- varios módulos;
- seguridad;
- datos;
- release.

“Grande” no autoriza cargar todo el repositorio. Se divide por hipótesis o
dominio.

## Protocolo de lectura

1. usar `rg` para localizar;
2. leer rangos relevantes;
3. seguir imports o llamadas necesarios;
4. confirmar contrato;
5. ampliar solo si la evidencia no basta.

No copiar:

- logs completos;
- archivos enteros si bastan líneas;
- conversación completa a un subagente;
- resultados duplicados.

## Handoff de subagente

Entrada mínima:

- objetivo;
- pregunta;
- rutas iniciales;
- restricciones;
- acciones permitidas;
- criterio de terminado;
- formato de salida.

Salida:

- hallazgos;
- evidencia y rutas;
- riesgos;
- incertidumbre;
- siguiente acción.

No pedir cadena de pensamiento ni transcripción exhaustiva.

## Secuencial frente a grafo

Usar secuencial cuando:

- un paso cambia el siguiente;
- comparten contrato;
- comparten archivos;
- la investigación decide el diseño;
- el agente principal ya posee el contexto.

Usar grafo cuando:

- hay dos investigaciones disjuntas;
- tests y revisión pueden ejecutarse tras una implementación estable;
- dominios no comparten writers;
- aislar contexto reduce contaminación.

Ejemplo:

```text
explorar iOS ─────┐
                  ├→ síntesis → implementación → tests
explorar backend ─┘
```

No confundir cuatro objetivos con cuatro workers. El límite normal son dos
workers, y varios writers requieren ownership disjunto.

## Gate de independencia

Antes de paralelizar, responder sí a todo:

- ¿cada nodo puede terminar sin esperar mensajes intermedios?
- ¿sus rutas de escritura no se cruzan?
- ¿sus contratos no cambian durante la ejecución?
- ¿el join tiene formato claro?
- ¿ahorra tiempo o protege contexto?

Si alguna respuesta es no, usar secuencial.

## Control de coste

Medir, cuando existan datos fiables:

- workers;
- archivos leídos;
- reintentos;
- duración;
- tokens reportados por plataforma;
- fallos de coordinación.

Sin telemetría, usar indicadores, no cifras inventadas.

Revisar después de varias tareas:

- si `xhigh` mejoró el resultado;
- si un subagente evitó o duplicó lectura;
- si el grafo redujo tiempo;
- si el handoff fue suficiente.

## Conversaciones largas

Crear un hilo o handoff nuevo cuando:

- cambia el objetivo;
- se integra una unidad y empieza otra;
- el contexto antiguo domina al vigente;
- existe un relevo.

Conservar en archivos:

- decisiones;
- estado;
- pruebas;
- commits y PR;
- pendientes.

La conversación no es la fuente permanente de verdad.
