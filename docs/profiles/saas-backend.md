# Perfil de calidad SaaS y backend

Se activa con evidencia de API/server y persistencia o despliegue. Puede
combinarse con web, móvil o IA en un repositorio híbrido.

## Flujo

- identificar contratos, esquema, migraciones, colas y servicios externos;
- caracterizar API y datos antes de cambiar;
- ejecutar unit, integration y contract tests relevantes;
- usar expand → migrate → contract para cambios compatibles;
- probar retries, idempotencia, concurrencia, timeouts y fallos parciales;
- revisar authn/authz, tenancy, rate limits, privacidad y observabilidad;
- definir rollback de código y datos por separado.

## Release

Staging no demuestra producción. Deploy requiere autorización, commit remoto,
workflow, migración compatible, smoke, métricas y umbral de rollback. Pagos,
auth, datos, migraciones y operaciones destructivas son T3.
