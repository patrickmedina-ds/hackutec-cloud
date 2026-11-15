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

## 3. Réplica a Azure (`puente-multinube`)

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

- Se publica en el tema de Event Grid `telecorp-<stage>-eventos` (esquema de entrada **CloudEvents 1.0**, `application/cloudevents-batch+json`).
- Si falla, la Lambda lanza error: Lambda reintenta y, al agotar, el evento va a la DLQ `telecorp-<stage>-puente-dlq`.
- Autenticación sin llaves: federación saliente de IAM (`sts:GetWebIdentityToken`) → credencial federada de una App Registration de Entra ID. Parámetros SSM `/telecorp/<stage>/azure/tenant-id`, `/client-id`, `/eventgrid-endpoint`. Detalle en `docs/federacion.md`.
- Entrega al menos una vez: los consumidores deduplican por el `id` del CloudEvent.

### Cosmos DB y Blob Storage (Azure)
La función `replicar`:
- Guarda el CloudEvent crudo en Blob Storage GRS, contenedor `eventos-respaldo`, ruta `aaaa/mm/dd/<tipo>/<ce_id>.json` (respaldo en otra nube).
- Mantiene en Cosmos DB (base `telecorp`): `incidencias` (pk `/incidenciaId`, último estado), `zonas` (pk `/zonaId`, estado agregado para la página de estado) y `eventos_procesados` (idempotencia por `ce_id`).
- Con `Notificacion.Fallida`, reenvía el aviso por Azure Communication Services.

### Analítica (AWS)
Regla del bus (excepto `Notificacion.Fallida`, que lleva PII) → Firehose → S3 particionado por `tipo/anio/mes/dia` → tabla Glue `telecorp_<stage>.eventos` → vistas de Athena (`v_incidencias`, `v_transiciones`, `v_antenas_eventos`, `v_colectivos`, `v_top_zonas`, `v_top_antenas`, `v_tipos_problema`, `v_recurrencia`, `v_mttr`). La API `GET /analitica/{reporte}?dias=N` (solo operadores) expone los reportes. Detalle en `aws/analitica/README.md`.

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
- Modelo: **Amazon Bedrock** (API `Converse`), perfil de inferencia configurable con el parámetro `ModeloIa` (por defecto `us.anthropic.claude-haiku-4-5-20251001-v1:0`). Salida JSON forzada con tool use (`toolChoice`).
- Autenticación: el rol IAM de la Lambda (`bedrock:InvokeModel`). Hay que habilitar el acceso al modelo en la consola de Bedrock.
- Todas las respuestas incluyen `fuente`: `"bedrock"` o `"reglas"`.
- **Respaldo obligatorio:** si Bedrock falla o tarda más de 8 s, la Lambda responde con reglas locales (palabras clave → tipo y severidad; plantillas fijas para avisos). Nunca debe romper la SFN.
- SMS: máximo 160 caracteres.
