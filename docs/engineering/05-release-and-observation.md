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
