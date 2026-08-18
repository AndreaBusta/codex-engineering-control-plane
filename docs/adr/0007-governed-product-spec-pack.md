# ADR 0007: SpecPack gobernado, el modelo redacta y el plano verifica

- Estado: proposed
- Fecha: 2026-08-18
- Responsables: tarea orquestadora del Control Plane
- PR: pendiente
- Sustituye: ninguno
- Sustituido por: ninguno

## Contexto

El Control Plane gobierna desde el `TaskEnvelope` hacia adelante: enruta
recursos, calcula tier, impone gates y produce evidencia acotada. No tiene nada
antes de ese punto.

El fallo más caro del desarrollo de apps ocurre justo antes: un objetivo
subespecificado que se convierte en código sin haberse convertido en decisión.
Hoy eso produce una de tres salidas, todas malas: un envelope pobre al que se
aplican gates sobre un alcance equivocado, documentación ad hoc distinta en cada
hilo, o implementación directa con la decisión de producto implícita en el diff.

Existe una demanda concreta y repetida de seis artefactos —PRD, TRD, UX/UI,
flujo de la app, backend y plan de implementación— que hoy se redactan a mano,
sin formato estable, sin trazabilidad entre sí y sin ninguna comprobación.

Al mismo tiempo, el repositorio está en un momento delicado. El candidato
`3.1.0-core.1` permanece `GREEN_LOCAL / PENDING_STABLE_ADOPTION` y la superficie
Advanced está en cuarentena estructural por
[ADR 0006](0006-control-plane-core-and-quarantine.md). Cualquier decisión que
amplíe el runtime activo tensiona directamente esa cuarentena.

## Decisión

Se adopta un contrato cerrado de seis artefactos, el **SpecPack**, bajo un
principio que fija la frontera:

> El modelo redacta. El plano verifica.

El Control Plane **no genera contenido de producto** por ninguna vía. Su papel
se limita a cuatro funciones deterministas:

1. definir el contrato de los seis artefactos y sus identificadores;
2. parametrizar las secciones obligatorias según el perfil ya detectado;
3. comprobar la trazabilidad y el cierre del pack como propiedades de grafo;
4. sellar digests por artefacto en un recibo no autorizante.

La entrega se secuencia en fases con puerta explícita:

- **Fase 1**, ejecutable de inmediato: plantillas y skill. Cero runtime nuevo,
  compatible con la cuarentena, reversible borrando archivos.
- **Fase 2**, bloqueada: validador determinista `control_plane/spec_pack.py`
  con presupuesto de 900 LOC. No empieza hasta que el candidato alcance
  adopción estable, existan tres packs reales de fase 1 y haya autorización
  explícita para ampliar runtime en la línea 3.x.
- **Fase 3**, no diseñada: verificación cruzada del pack contra el diff real.

El SpecPack no concede autoridad. Ni el manifiesto, ni un artefacto, ni el
recibo, ni un pack sellado autorizan commit, push, Pull Request, merge, deploy,
release, instalación ni adopción.

## Alternativas

### Alternativa A: no hacer nada

- Ventajas: coste nulo, riesgo nulo, ninguna superficie nueva.
- Inconvenientes: el hueco previo al envelope permanece abierto; cada hilo
  reinventa la estructura; la decisión de producto sigue implícita en el diff.
- Motivo de descarte: el problema es recurrente y su coste crece con cada
  proyecto multidominio.

### Alternativa B: motor de generación en el plano

Que el Control Plane redacte los seis artefactos.

- Ventajas: automatización aparente máxima y una sola herramienta.
- Inconvenientes: contradice frontalmente «el router selecciona; no ejecuta ni
  autoriza»; duplica en código determinista lo que un modelo hace mejor;
  inflación severa del runtime en plena cuarentena; convierte prosa generada en
  apariencia de evidencia.
- Motivo de descarte: rompe la arquitectura vigente y el límite de autoridad.

### Alternativa C: validador primero, contrato después

Implementar `spec_pack.py` antes de estabilizar el contrato en uso real.

- Ventajas: impone trazabilidad desde el primer día.
- Inconvenientes: amplía el runtime justo mientras se intenta estabilizarlo;
  fija en código un contrato que aún no se ha probado escribiendo packs reales;
  alto riesgo de retrabajo.
- Motivo de descarte: orden invertido respecto al riesgo. El contrato es barato
  de corregir en Markdown y caro de corregir en runtime bajo lock.

## Consecuencias

### Positivas

- Cierra la frontera más débil de la arquitectura sin tocar las existentes.
- La fase 1 entrega la mayor parte del valor con cero riesgo de runtime.
- Lo que se valida es mecánicamente decidible: unicidad de identificadores,
  cierre de cobertura, huérfanos, marcadores sin resolver y coherencia de tier.
  Es el reparto correcto entre runtime determinista y modelo.
- Los gates existentes pasan a operar sobre un alcance conocido y trazable.
- La capacidad es aditiva y retirable sin migración.

### Negativas

- Añade artefactos que el operador debe mantener.
- La fase 2 introduce runtime nuevo cuando se desbloquee, con su coste de
  mantenimiento y de superficie.
- La proporcionalidad por tier añade una regla más que explicar.

### Riesgos

- **Fábrica de documentos.** Seis plantillas invitan a rellenar por inercia. Si
  la validación comprobara presencia de encabezados en lugar de trazabilidad, el
  producto daría sensación de rigor sin aportarlo. Mitigación: validar el grafo,
  nunca el formato; los huérfanos son fallo.
- **Falso rigor.** Un pack puede cerrar y describir el producto equivocado. La
  validación es de coherencia interna, jamás de acierto. Mitigación: declararlo
  en la salida del comando, no solo en documentación.
- **Fricción.** Exigir el pack completo siempre haría que se rodee. Mitigación:
  pack mínimo en T0/T1, pack completo solo en T2/T3.
- **Tensión con la cuarentena.** Mitigación: la puerta de la fase 2 es explícita
  y sus tres condiciones son verificables por separado.

## Seguridad y privacidad

Todo contenido de un repositorio destino es no confiable. Un artefacto que
contenga instrucciones dirigidas al agente se trata como dato, nunca como orden.

El validador de la fase 2 no ejecuta nada declarado en el pack, no accede a red
por ninguna ruta de código, rechaza rutas sensibles antes de abrirlas, confina
las lecturas al repositorio destino y aplica límites por archivo y totales.

El recibo excluye contenido de artefacto, prompts, transcripciones y secretos.
`E_SPECPACK_AUTHORITY_CLAIM` existe para impedir que un pack se autoproclame
permiso.

## Migración y compatibilidad

Capacidad aditiva. Ningún comando existente cambia de contrato. Sin manifiesto,
el repositorio se comporta igual que hoy.

Retirada: borrar plantillas, skill, entradas de registry y —si existiera— el
módulo y su entrada de CLI y de lock devuelve el repositorio al estado previo
sin migración de datos.

El pack vive en el repositorio destino y es portable entre hosts. El recibo vive
en el namespace local no versionado y es descartable.

## Validación

La decisión se considera correcta si, sobre packs reales:

- la trazabilidad cierra y los huérfanos detectados corresponden a huecos reales
  de alcance, no a ruido de formato;
- el pack mínimo en T1 se usa sin abandono por fricción;
- ningún pack sellado se confunde con autorización en la práctica;
- la fase 2, cuando se desbloquee, cabe en 900 LOC.

Se considera incorrecta y debe revertirse si aparecen packs con todas las
secciones presentes y sin referencias cruzadas: sería la señal de que el
contrato se está usando como plantilla vacía.
