# Eventos

Bus: `telecorp-${Stage}-eventos`. Esquema JSON formal en `eventos.schema.json`. Constantes en `telecorp_comun.eventos`.

| `detail-type` | `source` | Quién lo emite | Quién lo consume |
|---|---|---|---|
| `Incidencia.CambioEstado` | `telecorp.incidencias` | Pipe desde stream de `historial-estados` | SFN `ciclo-vida`, puente |
| `Zona.UmbralSuperado` | `telecorp.zonas` | Pipe desde stream de `zona-ventana` (marcador) | SFN `colectivo`, puente |
| `Antena.CambioEstado` | `telecorp.antenas` | Pipe desde stream de `antenas` | SFN `colectivo` (si `estadoNuevo != NORMAL`), puente |
| `Notificacion.Solicitada` | `telecorp.notificaciones` | SFN `ciclo-vida` vía `events:PutEvents` | puente → Azure (`replicar` envía por Communication Services) |

Regla del puente: todos los eventos con `source` con prefijo `telecorp.`.

## `detail` de cada evento

### `Incidencia.CambioEstado`
```json
{
  "incidenciaId": "c7a1e0f2-...",
  "estadoAnterior": "REGISTRADA",
  "estadoNuevo": "EN_REVISION",
  "actor": "STEPFN:ciclo-vida",
  "comentario": null,
  "ts": "2026-11-15T14:03:00Z",
  "snapshot": {
    "zonaId": "LIM-MIR-01", "clase": "INDIVIDUAL", "origen": "USUARIO", "tipo": "SIN_INTERNET",
    "prioridad": 42, "nivelPrioridad": "P3", "severidadIA": "ALTA", "incidenteColectivoId": null
  }
}
```
`estadoAnterior` es `null` en el registro inicial (estadoNuevo = `REGISTRADA`).

### `Zona.UmbralSuperado`
```json
{
  "zonaId": "LIM-MIR-01",
  "incidenteColectivoId": "COL-01JD...",
  "usuariosDistintos": 10,
  "ventanaInicio": "2026-11-14T15:00:00Z",
  "ventanaFin": "2026-11-15T14:03:00Z",
  "incidencias": ["c7a1e0f2-...", "..."]
}
```

### `Antena.CambioEstado`
```json
{
  "antenaId": "ANT-LIM-MIR-01-03",
  "zonaId": "LIM-MIR-01",
  "estadoAnterior": "NORMAL",
  "estadoNuevo": "CAIDA",
  "gbPorMinuto": 41.7,
  "ts": "2026-11-15T14:02:00Z"
}
```

### `Notificacion.Solicitada`
```json
{
  "incidenciaId": "c7a1e0f2-...",
  "estado": "ATENDIDA",
  "canal": "EMAIL",
  "destino": "usuario@example.com",
  "asunto": "Red TeleCorp: incidencia identificada en tu zona",
  "mensaje": "Identificamos una falla ..."
}
```
`canal`: `SMS` | `EMAIL`. AWS no envía avisos (el laboratorio bloquea SES y SNS SMS): la SFN `ciclo-vida` publica un evento por canal y Azure Communication Services los envía. Es el **único** evento con PII (destino); la analítica lo excluye.

## Mensajes al usuario según estado

| Estado | Mensaje (resumen) |
|---|---|
| `EN_REVISION` | "Recibimos tu reporte y lo estamos evaluando." |
| `ATENDIDA` | "Identificamos una incidencia en tu zona y un equipo la está atendiendo." |
| `SOLUCIONADA` | "El servicio fue restablecido." |

`REGISTRADA` y `CERRADA` no notifican.
