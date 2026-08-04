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

## Candidata reproducible v2.1.1

El workflow manual ejecuta suite, smoke Darwin, preflight de release y matriz
de adopción antes de construir la candidata desde `origin/main`. Mantiene
`actions: read` y `contents: read`; no crea un tag ni una GitHub Release. Genera
un JSON acotado con commit, árbol, URL del run y los cuatro resultados reales.
Después entrega un artefacto efímero de GitHub Actions, con retención de un día,
que contiene exactamente los cuatro assets verificados y falla si falta alguno.
Ese transporte permite descargarlos y comprobarlos fuera del runner, pero no
los convierte en una release ni concede autoridad para publicarlos.

El workflow construye los assets dentro del mismo run y attempt que pasó los
gates:

```bash
candidate_parent=/ruta/privada/control-plane-release
mkdir -m 700 "$candidate_parent"
scripts/build-release-candidate \
  --repo "$PWD" \
  --output-dir "$candidate_parent/candidate" \
  --workflow-url https://github.com/OWNER/REPO/actions/runs/RUN_ID/attempts/ATTEMPT \
  --workflow-evidence /ruta/privada/workflow-evidence.json
```

La elevación a `verified_candidate` exige una checkout limpia de `main`
idéntica a `origin/main`, el contexto exacto del job `release-candidate` y una
consulta read-only a la API oficial de GitHub para ese run y attempt. En el
repositorio privado usa únicamente el `GITHUB_TOKEN` efímero del job con
`actions: read`; no guarda ni imprime su valor. La API debe confirmar el
commit, árbol, workflow `control-plane.yml`, jobs y steps que corresponden
a los cuatro gates. Un JSON local, una URL inventada, otro attempt o una
observación incompleta fallan cerrados; no pueden declarar gates exitosos. El directorio
padre también debe estar controlado por el usuario y no ser escribible por
grupo u otros. Con evidencia observada los gates quedan `success`, el estado es
`verified_candidate` y la procedencia `workflow_api_observed`; sin
`--workflow-evidence` el builder local conserva `candidate` y
`pending_external_evidence`. En ambos casos manifest y receipt declaran
`authorizes=false` y
`release_authorized=false`: el JSON no concede autoridad ni sustituye la
consulta separada del workflow. Solo una autorización posterior permite crear
tag o GitHub Release y adjuntar esos assets.

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
