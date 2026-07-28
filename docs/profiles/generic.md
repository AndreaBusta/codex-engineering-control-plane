# Perfil de calidad genérico

Usar cuando no existen marcadores suficientes para un stack concreto. `generic`
no significa baja calidad ni autoriza inventar comandos.

## Flujo

1. Identificar manifests, lockfiles, CI, estructura y comandos documentados.
2. Obtener baseline reproducible antes de editar.
3. Aplicar T0–T3 por riesgo, no por lenguaje.
4. Ejecutar la comprobación más cercana disponible: test, lint, typecheck,
   build o smoke.
5. Revisar diff, documentación, Git y efectos externos con los gates comunes.

Si no existe verificación automatizada, registrar la limitación y el smoke
manual exacto. No añadir una dependencia solo para satisfacer el perfil.

## Calidad mínima

- comportamiento y compatibilidad preservados;
- errores fail-closed cuando hay riesgo;
- secretos fuera de código y logs;
- cambios pequeños, revisables y reversibles;
- evidencia separada de afirmaciones.
