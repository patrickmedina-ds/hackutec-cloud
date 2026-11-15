# Integraciones

## 1. API Gateway → SQS (`telecorp-<stage>-reportes`)

`POST /incidencias` usa integración directa de API Gateway con SQS (`SendMessage`), sin Lambda. La plantilla de mapeo arma este cuerpo:

```json
{
  "incidenciaId": "$context.requestId",
  "usuarioId": "$context.authorizer.claims.sub",
  "recibidoEn": "$context.requestTimeEpoch",
  "reporte": {
    "documento": "...", "numeroServicio": "...", "telefono": "+51...", "email": "...",
    "zonaId": "LIM-MIR-01", "lat": -12.12, "lon": -77.03,
    "fechaHoraEvento": "2026-11-15T13:55:00Z", "tipo": "SIN_INTERNET", "descripcion": "..."
  }
}
```
La API responde `202 {"incidenciaId": "<requestId>", "estado": "REGISTRADA"}`.

Cola: visibilidad 60 s, `maxReceiveCount` 5 → DLQ (retención 14 días). `registrar-reporte` consume en lotes de 10 con `ReportBatchItemFailures` y es idempotente (`registrar_inicial()` ignora un `incidenciaId` ya existente).

## 2. Telemetría de antenas

- Tema MQTT: `telecorp/antenas/{antenaId}/trafico`
- Carga útil:
```json
{ "antenaId": "ANT-LIM-MIR-01-03", "zonaId": "LIM-MIR-01", "ts": "2026-11-15T14:02:00Z", "gbPorMinuto": 41.7 }
```
- Regla de IoT: `SELECT * FROM 'telecorp/antenas/+/trafico'` → Kinesis `telecorp-<stage>-telemetria`, partition key `${antenaId}`.
- El simulador puede publicar en IoT (`iot-data publish`) o escribir directo en Kinesis con la misma carga.
- Umbrales: `telecorp_comun.antenas.clasificar_trafico()`.

## 3. Réplica entre nubes (`puente-multinube`)

La regla del puente envía cada evento del bus a la Lambda, y esta lo convierte a **CloudEvents 1.0 (JSON estructurado)**:

```json
{
  "specversion": "1.0",
  "id": "<id del evento EventBridge>",
  "source": "telecorp/aws/<source>",
  "type": "<detail-type>",
  "time": "<time del evento>",
  "datacontenttype": "application/json",
  "subject": "<zonaId o incidenciaId>",
  "data": { "...": "detail del evento" }
}
```

- **GCP:** se publica en Pub/Sub `telecorp-<stage>-eventos`. `data` del mensaje = el CloudEvent completo en JSON. Atributos: `ce_type`, `ce_id`, `zonaId` si existe.
- **Azure:** se publica en el tema de Event Grid `telecorp-<stage>-eventos` con esquema de entrada **CloudEvents 1.0**.
- Los dos envíos son independientes: si falla uno, el otro sigue. La Lambda reporta el fallo para que EventBridge reintente (con DLQ propia `telecorp-<stage>-puente-dlq`).
- Autenticación sin llaves guardadas:
  - GCP: Workload Identity Federation con proveedor AWS. Configuración en SSM `/telecorp/<stage>/gcp/wif-config` (JSON del archivo de configuración de credenciales externas).
  - Azure: credencial de identidad federada en una App Registration de Entra ID. Parámetros en SSM `/telecorp/<stage>/azure/tenant-id`, `/client-id`, `/eventgrid-endpoint`.

### BigQuery (GCP)
Suscripción BigQuery de Pub/Sub sobre la tabla `telecorp_analitica.eventos_raw` con `write_metadata = true` y columna `data` (JSON). Las vistas (`v_incidencias`, `v_antenas`, `v_colectivos`, `v_recurrencia`) derivan los campos desde `JSON_VALUE(data, '$.data.zonaId')` etc.

### Cosmos DB (Azure)
Base `telecorp`, contenedores:
- `incidencias` (pk `/incidenciaId`): último estado de cada incidencia (upsert desde `Incidencia.CambioEstado`).
- `zonas` (pk `/zonaId`): estado agregado por zona (incidencias abiertas, colectivo activo, antenas degradadas) para la página de estado.

## 4. IA (`clasificar-ia`)

Invocada desde la SFN `ciclo-vida` (tarea `lambda:invoke`) y desde `colectivo`.

Entrada:
```json
{ "accion": "CLASIFICAR", "incidenciaId": "...", "tipo": "SIN_INTERNET", "descripcion": "...", "zonaId": "...", "tipoZona": "MEDICA" }
{ "accion": "RESUMIR_COLECTIVO", "incidenteColectivoId": "...", "zonaId": "...", "tipoZona": "...", "descripciones": ["..."], "antenas": [{"antenaId": "...", "estado": "CAIDA"}] }
{ "accion": "REDACTAR_AVISO", "estado": "ATENDIDA", "zonaNombre": "...", "canal": "SMS" }
```
Salida:
```json
{ "tipo": "SIN_INTERNET", "severidad": "ALTA", "resumen": "...", "confianza": 0.86 }
{ "resumen": "...", "severidad": "CRITICA", "causaProbable": "..." }
{ "asunto": "...", "mensaje": "..." }
```
- Modelo: Gemini en Vertex AI (`gemini-2.5-flash`), salida JSON forzada con `response_schema`.
- Autenticación: la misma federación WIF del puente (SSM `/telecorp/<stage>/gcp/wif-config`).
- **Respaldo obligatorio:** si Vertex falla o tarda más de 8 s, la Lambda responde con reglas locales (palabras clave → tipo y severidad; plantillas fijas para avisos). Nunca debe romper la SFN.
- SMS: máximo 160 caracteres.
