# Tablas DynamoDB

Todas: `BillingMode: PAY_PER_REQUEST`, `PointInTimeRecoverySpecification` activado, `SSESpecification` activado. Fechas en ISO 8601 UTC (`2026-11-15T14:03:00Z`). `ttl` en segundos epoch.

## `incidencias`

| Clave | Atributo | Tipo |
|---|---|---|
| PK | `incidenciaId` | S (el `requestId` de API Gateway para reportes; `COL-<ulid>` para colectivas; `REC-<ulid>` para recurrencia; `ANT-<ulid>` para proactivas) |

Atributos:

| Atributo | Tipo | Notas |
|---|---|---|
| `clase` | S | `INDIVIDUAL` \| `COLECTIVA` |
| `origen` | S | `USUARIO` \| `ANTENA` \| `RECURRENCIA` |
| `estado` | S | ver `telecorp_comun.estados.Estado` |
| `version` | N | control optimista; lo maneja `transicionar()` |
| `usuarioId` | S | `sub` de Cognito (solo INDIVIDUAL) |
| `documento`, `numeroServicio`, `telefono`, `email` | S | PII, solo INDIVIDUAL |
| `zonaId` | S | |
| `lat`, `lon` | N | opcional |
| `fechaHoraEvento` | S | cuándo ocurrió según el usuario |
| `tipo` | S | `SIN_INTERNET` \| `SIN_LLAMADAS` \| `SIN_SMS` \| `LENTITUD` \| `INTERMITENCIA` \| `OTRO` |
| `descripcion` | S | |
| `tipoIA`, `severidadIA`, `resumenIA` | S | los llena la IA. Severidad: `BAJA` \| `MEDIA` \| `ALTA` \| `CRITICA` |
| `prioridad` | N | 0–100 (`telecorp_comun.prioridad`) |
| `nivelPrioridad` | S | `P1`..`P4` |
| `incidenteColectivoId` | S | en reportes hijos agrupados |
| `usuariosAfectados` | N | solo COLECTIVA |
| `antenasAfectadas` | L de S | solo COLECTIVA / ANTENA |
| `creadoEn`, `actualizadoEn`, `resueltoEn` | S | |

GSIs (proyección `ALL`):

| Índice | PK | SK |
|---|---|---|
| `por-zona` | `zonaId` | `creadoEn` |
| `por-estado` | `estado` | `creadoEn` |
| `por-colectivo` | `incidenteColectivoId` | `creadoEn` |
| `por-usuario` | `usuarioId` | `creadoEn` |

## `historial-estados`

| Clave | Atributo | Tipo |
|---|---|---|
| PK | `incidenciaId` | S |
| SK | `sk` | S = `<ts ISO>#<estadoNuevo>` |

Atributos: `estadoAnterior` (S o ausente si es el registro inicial), `estadoNuevo`, `actor` (`SISTEMA` \| `STEPFN:<nombre>` \| `OPERADOR:<sub>`), `comentario`, `ts`, `snapshot` (M sin PII: `zonaId`, `clase`, `origen`, `tipo`, `prioridad`, `nivelPrioridad`, `severidadIA`, `incidenteColectivoId`).

**Streams: `NEW_IMAGE`.** Cada INSERT es exactamente un cambio de estado → Pipe → evento `Incidencia.CambioEstado`.

Solo `telecorp_comun.estados.registrar_inicial()` y `transicionar()` escriben aquí.

## `zona-ventana`

Ventana deslizante de 24 h para detectar incidentes colectivos.

| Clave | Atributo | Tipo |
|---|---|---|
| PK | `zonaId` | S |
| SK | `sk` | S |

Dos tipos de ítem:

1. **Reporte**: `sk = R#<ts ISO>#<usuarioId>`, atributos `incidenciaId`, `usuarioId`, `tipo`, `ttl = ts + 24h`.
2. **Marcador de colectivo activo**: `sk = COLECTIVO_ACTIVO`, atributos `incidenteColectivoId`, `usuariosDistintos`, `ventanaInicio`, `ventanaFin`, `incidencias` (L de S), `ttl = ahora + 24h`. Se crea con `attribute_not_exists(sk)` para no disparar dos veces.

`registrar-reporte` inserta el ítem 1, consulta `sk BETWEEN R#<ahora-24h> AND R#<ahora>`, cuenta `usuarioId` distintos y, si llega a **10**, intenta crear el ítem 2.

**Streams: `NEW_IMAGE`.** Pipe con filtro `eventName = INSERT` y `sk = COLECTIVO_ACTIVO` → evento `Zona.UmbralSuperado`.

## `zonas` (catálogo)

PK `zonaId` (S). Atributos: `nombre`, `tipoZona` (`RESIDENCIAL` \| `COMERCIAL` \| `EDUCATIVA` \| `INDUSTRIAL` \| `MEDICA` \| `CRITICA`), `ciudad`, `lat`, `lon`, `usuariosEstimados` (N).

## `antenas`

PK `antenaId` (S). Atributos: `zonaId`, `estado` (`NORMAL` \| `CAIDA` \| `SATURADA` \| `INHABILITADA`), `gbPorMinuto` (N), `ultimaLectura` (S), `cambioEstadoEn` (S), `lat`, `lon`.
GSI `por-zona`: PK `zonaId`, SK `antenaId`.

`evaluar-antena` actualiza `estado` **solo cuando cambia** (`ConditionExpression: estado <> :nuevo`), así cada MODIFY del stream es un cambio real.

**Streams: `NEW_AND_OLD_IMAGES`.** Pipe con filtro `eventName = MODIFY` → evento `Antena.CambioEstado`.

## `metricas-antena`

PK `antenaId` (S), SK `ts` (S). Atributos `gbPorMinuto`, `estado`, `zonaId`, `ttl = ts + 30 días`. Sin streams.
