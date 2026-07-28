# Perfil de calidad web y PWA

Se activa con marcadores web runtime como Vite, Next, un manifest o un service
worker; PWA exige además manifest o service worker. Una web sin evidencia
suficiente usa el perfil genérico o SaaS.

## Flujo

- respetar package manager y lockfile existentes;
- no instalar paquetes sin autorización;
- ejecutar lint, typecheck, unit tests y build definidos por el proyecto;
- validar flujos críticos con tests de navegador deterministas cuando existan;
- comprobar responsive, teclado, accesibilidad y estados loading/error/empty;
- para PWA, comprobar manifest, scope, offline, actualización, precache y
  invalidación de service worker;
- separar deploy de activación con flags cuando el riesgo lo justifique.

## Release

Demostrar commit, build CI, artefacto y entorno. Un deploy local o preview no
prueba producción. Auth, pagos, uploads, datos privados y cambios de caché
irreversibles escalan a T3.
