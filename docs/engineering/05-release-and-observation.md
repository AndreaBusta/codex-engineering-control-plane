# Release, TestFlight y observación

## Principio de procedencia

Una release oficial se construye desde el commit protegido que el proveedor de
CI obtuvo del remote. Nunca se infiere desde el contenido visible en un Xcode
local.

```text
origin/<base>
→ workflow identificado
→ tests y build
→ artefacto nuevo
→ manifest
→ proveedor de distribución
→ procesamiento
→ smoke
→ observación
```

## Preflight local

Antes de autorizar una release:

```bash
scripts/control-plane preflight --mode release --refresh
```

Exige:

- rama local igual a base;
- árbol limpio;
- remote y base presentes;
- HEAD igual a `origin/<base>`.

Es necesario pero no suficiente. No demuestra que CI haya compilado ese commit.

## iOS

### Fuente

Xcode Cloud debe obtener el repositorio remoto y el commit exacto. Esto evita:

- Xcode abierto en otro worktree;
- Archive antiguo;
- cambios locales no publicados;
- build sin relación con el PR.

### Build

Cada publicación usa:

- número de build nuevo;
- Archive nuevo;
- scheme/configuración declarados;
- Xcode/macOS registrados;
- tests del workflow;
- commit registrado.

No reutilizar un Archive anterior para “subir los cambios”.

### TestFlight

Una build puede estar:

- subida;
- procesando;
- inválida;
- procesada;
- disponible para grupo;
- expirada.

“Upload terminó” no equivale a “TestFlight procesado”. Hasta consultar Apple, el
estado es `pending_external_evidence`.

### Recibo

Completar `templates/RELEASE_RECEIPT.json` con:

- repositorio;
- base;
- commit;
- PR y merge commit;
- versión;
- build;
- workflow;
- hash del Archive cuando esté disponible;
- gates;
- estado externo;
- smoke;
- autorización.

No guardar credenciales, perfiles, certificados ni datos de firma.

## SaaS

Aplicar la misma cadena:

```text
origin/<base>
→ CI
→ artefacto inmutable
→ entorno
→ migración compatible
→ deploy
→ smoke
→ métricas
→ cierre
```

Registrar:

- digest del artefacto;
- entorno;
- commit;
- workflow;
- migraciones;
- feature flags;
- rollback;
- salud posterior.

## Migraciones

Preferir:

```text
expand → migrate → contract
```

1. añadir compatibilidad;
2. desplegar lectores/escritores compatibles;
3. migrar;
4. verificar;
5. retirar lo antiguo en otra unidad.

Una release no puede depender de revertir mágicamente datos irreversibles.

## Feature flags

Separar despliegue de activación cuando el riesgo lo justifique:

- flag con dueño;
- valor seguro por defecto;
- población;
- métricas;
- fecha de retirada;
- kill switch probado.

El flag no sustituye tests ni migración compatible.

## Gate de autorización

Antes de cualquier upload o deploy:

- usuario autorizó la release concreta;
- fuente y commit identificados;
- gates aprobados;
- rollback viable;
- observación preparada.

La autorización para implementar no implica autorización para publicar.

## Candidata reproducible v2.1.0

El workflow manual ejecuta suite, smoke Darwin, preflight de release y matriz
de adopción antes de construir la candidata desde `origin/main`. Mantiene
`contents: read` y no publica ni sube assets. El log conserva los SHA-256
generados por el runner remoto.

Después de ese PASS, los mismos bytes pueden regenerarse fuera del repositorio:

```bash
candidate_parent=/ruta/privada/control-plane-release
mkdir -m 700 "$candidate_parent"
scripts/build-release-candidate \
  --repo "$PWD" \
  --output-dir "$candidate_parent/candidate" \
  --workflow-url https://github.com/OWNER/REPO/actions/runs/RUN_ID
```

La regeneración solo acepta una checkout limpia de `main` idéntica a
`origin/main` y un directorio padre controlado por el usuario, no escribible
por grupo u otros. Antes de publicar, sus hashes deben coincidir con el log
del workflow. El manifest y el receipt siguen declarando `authorizes=false`,
`release_authorized=false` y `pending_external_evidence`, incluso cuando los
gates precedentes pasaron: el JSON local no puede autoatestiguar el estado del
host. Se verifica el workflow por separado y únicamente una autorización
posterior permite crear tag o GitHub Release y adjuntar esos assets.

El tarball contiene `.codex/release-source.json`, generado desde los objetos
inmutables del commit. La cápsula vincula versión, commit, objeto commit, árbol,
paths, modos, tamaños, OID de blob y SHA-256. El productor y el consumidor
comparten límites de entradas y bytes; además reconstruyen el OID del árbol y
comprueban que el objeto commit referencia ese árbol. El tar se compone desde
esos blobs verificados, por lo que `export-ignore` y `export-subst` no pueden
omitir ni transformar su contenido. Tras comprobar
`SHA256SUMS`, una extracción sin `.git` puede usarse directamente como
`--source`; `adopt plan` y `adopt apply` vuelven a validar la cápsula y fallan
cerrados ante bytes, modos, paths, identidades Git o schema extra.

## Observación posterior

Definir antes de release:

- señal de éxito;
- señal de degradación;
- ventana de observación;
- persona o agente responsable;
- umbral de rollback;
- canal de incidentes.

Comprobar:

- crashes y errores;
- latencia;
- auth;
- pagos;
- flujo crítico;
- métricas de negocio relevantes;
- feedback de testers.

No cerrar en `released`; cerrar en `observed`.

## Respuesta correcta

Antes de evidencia externa:

> Commit y gates locales verificados. La publicación todavía no está demostrada:
> `pending_external_evidence`.

Después:

> Fuente `origin/<base>` y commit verificados; workflow y build identificados;
> TestFlight procesado; smoke aprobado; recibo guardado.
