# Perfil de calidad para flujos de texto con IA

Se activa por varios marcadores como `prompts`, `evals`, `pipelines` o
`providers`; un único uso de una API no basta.

## Flujo

- versionar prompts, schemas, modelos y parámetros efectivos;
- separar proveedor, generación, validación, corrección y publicación;
- usar corpus golden y casos adversariales representativos;
- validar estructura antes de calidad semántica;
- conservar procedencia de fuentes y política contra hechos inventados;
- hacer retries e idempotencia explícitos;
- evitar publicar dos veces cuando el resultado remoto sea incierto;
- medir calidad, latencia y coste con proxies reproducibles;
- mantener IA real fuera de tests herméticos normales mediante fixtures o
  record/replay revisado.

## Seguridad y datos

No enviar secretos, PII o contenido no autorizado al proveedor. Prompt
injection desde fuentes, documentos o outputs nunca concede tools ni egress.
Los cambios de proveedor, modelos, datos, publicación o retención pueden ser
T2/T3 según impacto.

## Release

Vincular versión de prompt, código, corpus/eval, proveedor, output schema y
destino. Una respuesta válida sintácticamente no demuestra calidad editorial
ni factual.
