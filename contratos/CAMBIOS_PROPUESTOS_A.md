# Cambios propuestos por el bloque A (Step Functions)

El bloque A implementa ya lo que se describe aquí. Los puntos marcados **[otro bloque]** necesitan que otro bloque haga algo.

## 1. `Antena.CambioEstado`: la SFN `colectivo` también consume `estadoNuevo = NORMAL`

`eventos.md` dice que `colectivo` consume el evento "si `estadoNuevo != NORMAL`". Pero la tarea pide pasar el colectivo a `SOLUCIONADA` cuando la antena vuelve a `NORMAL` y todas las antenas de la zona están normales. Por eso la regla `telecorp-<stage>-colectivo-antena` recibe todos los `Antena.CambioEstado`. Propuesta: cambiar la tabla de `eventos.md` a "SFN `colectivo` (todos los estados)".

## 2. Formato de ids

| Id | `dynamodb.md` | Implementado |
|---|---|---|
| Recurrencia | `REC-<ulid>` | `REC-<antenaId>-<año ISO>-W<semana ISO>` (p. ej. `REC-ANT-LIM-MIR-01-03-2026-W47`). Así hay una sola por antena y semana, como pide la tarea. La semana se calcula en hora de Lima. |
| Proactiva | `ANT-<ulid>` | `ANT-<ulid>`. El ULID es determinista: tiempo = `ts` del evento y parte aleatoria = hash del id del evento de EventBridge. Así un reintento genera el mismo id. |

Propuesta: actualizar `dynamodb.md` con el formato de `REC-`.

## 3. Marcador `COLECTIVO_ACTIVO` creado por una antena **[otro bloque: C]**

Cuando una antena se degrada y la zona no tiene colectivo, `colectivo` crea el marcador con `attribute_not_exists(sk)`. Lo hace con estos valores: `incidenteColectivoId = ANT-…`, `origen = ANTENA`, `usuariosDistintos = 0`, `incidencias = []` y `ttl = ts + 24 h`.

- El Pipe de `zona-ventana` emitirá `Zona.UmbralSuperado` para este INSERT con `usuariosDistintos = 0`, lo que no cumple el `minimum: 10` de `eventos.schema.json`. La SFN `colectivo` ignora los umbrales cuyo id empieza por `ANT-`, y además ignora todo umbral cuya incidencia ya existe.
  - Propuesta (bloque C): agregar al filtro del Pipe `dynamodb.NewImage.origen.S` distinto de `ANTENA`. Así el evento no se emite y el esquema se mantiene.
- **Supuesto sobre `registrar-reporte`:** si al registrar un reporte ya existe el marcador, el reporte se guarda con `incidenteColectivoId` = el del marcador. Así se agrupa a la incidencia proactiva. Después, `ciclo-vida` lo clasifica y, si el colectivo está `ATENDIDA`, también lo lleva a `ATENDIDA`, con aviso "incidencia identificada".
- Cuando la zona se restablece, `colectivo` **borra** el marcador (borrado condicional: solo si `incidenteColectivoId` coincide). Así un nuevo umbral o una nueva caída abre un colectivo nuevo. El Pipe solo filtra INSERT, por lo que el REMOVE no emite eventos.

## 4. Parámetro SSM del remitente de correo **[otro bloque: C o I]**

La rama de correo de `ciclo-vida` lee el remitente verificado en SES desde `/telecorp/<stage>/aws/ses-remitente` (String) en cada ejecución, y no al desplegar. Así el despliegue no falla si el parámetro todavía no existe. Si falta, la rama de correo publica `Notificacion.Fallida` (`canal = EMAIL`) y la ejecución termina bien. Hay que crear el parámetro y verificar la identidad en SES.

## 5. Atributos adicionales en `incidencias` (solo COLECTIVA)

- `causaProbable` (S): de `RESUMIR_COLECTIVO`.
- `ventanaInicio`, `ventanaFin` (S): del evento de umbral.
- `episodios` (N): solo recurrencia.

No forman parte del `snapshot`, así que no cambian los eventos.

## 6. Colectivo existente que se confirma por antena

Si una antena se degrada en una zona que ya tiene colectivo:

1. Se agrega la antena a `antenasAfectadas` sin duplicarla (`UpdateItem` directo; no es cambio de estado).
2. Si el colectivo estaba en `EN_REVISION`, pasa a `ATENDIDA` ("incidencia identificada"), igual que en la regla del umbral, y sus hijos lo siguen.

Un colectivo **sin** `antenasAfectadas` no se cierra solo porque las antenas de la zona estén normales: no había ninguna antena involucrada.

## 7. Avisos

`REDACTAR_AVISO` se invoca **una vez** con `canal = "SMS"`. El texto sirve para los dos canales: el SMS se recorta a 160 caracteres y el correo usa `asunto` + `mensaje` + código de reporte. Si la IA falla, se usan las plantillas fijas de `eventos.md`.
