# Perfil de calidad Android

Se activa con Gradle y `AndroidManifest.xml`; nunca hereda gates de TestFlight.

## Flujo

- descubrir wrapper, módulos, variants, flavors y SDK real;
- usar el Gradle Wrapper del repositorio;
- ejecutar unit tests, lint y build de la variant afectada;
- usar tests instrumentados/emulador cuando el cambio dependa de Android
  runtime, permisos, navegación, lifecycle o almacenamiento;
- revisar ProGuard/R8, manifests, permisos, deep links y compatibilidad;
- tratar firma, Play Console, billing, migraciones y datos privados como T3.

## Release

Relacionar commit, variant, version code/name, artefacto AAB/APK, workflow y
estado de Play Console. No publicar ni reutilizar un artefacto anterior sin
autorización y evidencia del proveedor.
