# Configuración global de Codex

## Valores aplicados

En `/Users/bustaseo/.codex/config.toml`:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
plan_mode_reasoning_effort = "xhigh"

[features.multi_agent_v2]
enabled = true
max_concurrent_threads_per_session = 2

[memories]
generate_memories = true
use_memories = true
disable_on_external_context = true
```

Las claves se probaron primero como overrides con `codex --strict-config
doctor`. Codex cargó la configuración; el diagnóstico conservó advertencias
ambientales independientes.

## Motivo

### Sol fijo

Mantiene un único modelo capaz para iOS, SaaS, arquitectura y revisión. Evita
que el usuario tenga que escoger familia de modelo.

### High por defecto

Reduce el coste frente al `xhigh` global anterior sin degradar la ruta normal de
ingeniería. No sustituye la clasificación:

- T0 puede recomendar `low`;
- T1 `medium`;
- T2 `high`;
- T3 `xhigh`.

Una conversación ya iniciada puede conservar su esfuerzo actual. Si una tarea
necesita un esfuerzo distinto y la interfaz no permite cambiarlo, abrir un hilo
acotado o un subagente con la recomendación correspondiente.

### Plan en xhigh

La fase de plan concentra decisiones de alcance, dependencias y riesgo. Usar
`xhigh` aquí es más eficiente que mantenerlo en cada acción de ejecución.

### Dos workers

Nueve workers permitían paralelismo excesivo y contexto duplicado. Dos cubren el
fork-join normal:

```text
exploración A ─┐
               ├→ síntesis
exploración B ─┘
```

Los workflows especializados que gestionen su propia concurrencia deben
documentar y validar su excepción.

### Memoria con contexto externo

`disable_on_external_context = true` impide generar memoria persistente desde
sesiones contaminadas por web, MCP u otras fuentes externas no confiables.
Consultar memoria existente sigue permitido según política.

## Valores preservados

No se cambiaron:

- `approval_policy = "never"`;
- `sandbox_mode = "danger-full-access"`;
- keyring;
- plugins;
- MCP;
- proyectos confiables;
- configuración Desktop.

El acceso amplio sigue siendo un riesgo operativo mitigado por el `AGENTS.md`
global, aprobaciones específicas y gates. Cambiarlo exige una decisión separada
porque afectaría muchos proyectos.

## Opciones no añadidas

No se añadieron claves de “subagent default model/reasoning” no confirmadas por
la versión instalada. Los subagentes heredan el modelo salvo override válido.

No se deshabilitó ninguno de los dos plugins `superpowers` detectados. Existe
duplicación potencial, pero primero debe demostrarse qué skills aporta cada uno
y qué dependencia tiene el entorno actual.

No se instalaron plugins o MCP nuevos. GitHub ya dispone de capacidad; Linear
no está autenticado y Stitch conserva una variable opcional ausente. Ninguno es
necesario para los gates locales.

## Verificación

Después de editar:

```bash
codex --strict-config doctor
```

Interpretar por secciones:

- `Configuration / config loaded` prueba parse y claves;
- un fallo de terminal no invalida config;
- advertencias MCP opcionales deben reportarse;
- sandbox unrestricted debe seguir tratándose como riesgo visible.

## Rollback manual

Si una versión futura no reconoce una clave:

1. no borrar el archivo completo;
2. identificar la clave rechazada con `--strict-config`;
3. retirar solo esa línea mediante un cambio revisable;
4. repetir `doctor`;
5. documentar la diferencia de versión.

No copiar credenciales ni reconstruir `config.toml` desde cero.
