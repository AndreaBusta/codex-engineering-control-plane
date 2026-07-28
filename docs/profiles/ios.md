# Perfil de calidad iOS

Se activa solo con evidencia Apple como `.xcodeproj` o `.xcworkspace`. No se
activa por la conversación histórica ni por el nombre de una carpeta.

## Flujo

- descubrir workspace/proyecto, scheme y destinos reales;
- preferir Swift Testing/XCTest ya existentes;
- ejecutar build y tests dirigidos antes de ampliar;
- comprobar concurrencia, ciclo de vida, accesibilidad y estado de UI cuando
  el cambio los afecte;
- tratar firma, capabilities, entitlements, privacidad, migraciones y release
  como T3;
- generar Archive nuevo desde el commit demostrado.

## Release

TestFlight solo entra cuando el resultado solicitado es `release`. La fuente
oficial es `origin/<base>` mediante Xcode Cloud o pipeline autorizado. Versión,
build, commit, workflow y estado procesado permanecen
`pending_external_evidence` hasta consultar Apple.

No reutilizar Archives ni inferir que Xcode está abierto en el worktree
correcto.
