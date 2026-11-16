# Integraciones

## 1. API pública → SQS (`telecorp-<stage>-reportes`)

`POST /reportes` (público, sin login) lo atiende la Lambda `api-publica`: valida que la persona tenga un contrato en la tabla `clientes` (por DNI vía GSI `por-documento` o por número de servicio) y encola este cuerpo con `SendMessage`. `POST /incidencias` (autenticado, compatibilidad) usa la misma Lambda y validación; ya no hay integración directa API Gateway → SQS.

```json
{
  "incidenciaId": "<uuid4>",
  "usuarioId": "cliente:SRV-00000001",
  "recibidoEn": 1763215980000,
  "reporte": {
    "documento": "...", "numeroServicio": "SRV-00000001", "telefono": "+51...", "email": "...",
    "zonaId": "LIM-JMA-01",
    "fechaHoraEvento": "2026-11-15T13:55:00Z", "tipo": "SIN_INTERNET", "descripcion": "..."
  }
}
```
`documento`, `numeroServicio`, `telefono`, `email` y `zonaId` salen **del contrato**, nunca del cuerpo de la petición. `lat`/`lon` siguen siendo opcionales para otros productores (el simulador por SQS). `recibidoEn` en epoch ms. La API responde `202 {"incidenciaId", "estado": "REGISTRADA", "zonaId", "zonaNombre"}`; errores `404 CLIENTE_NO_ENCONTRADO`, `409 VARIOS_SERVICIOS`, `403 CONTRATO_SUSPENDIDO` (ver `openapi.yaml`).

`POST /reportes/consulta` (DNI + número de servicio del mismo contrato) devuelve los reportes del GSI `por-usuario` con `usuarioId = cliente:<numeroServicio>` y su historial, sin PII.

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
- Autenticación sin llaves: JWT de **Cognito Identity** (`GetOpenIdTokenForDeveloperIdentity`, identidad de desarrollador fija `{"telecorp.aws": "telecorp-<stage>"}` del Identity Pool del stack puente) → credencial federada de una App Registration de Entra ID (iss `https://cognito-identity.amazonaws.com`, aud = IdentityPoolId, sub = IdentityId) → token con scope `https://eventgrid.azure.net/.default`. Lógica compartida en `telecorp_comun.entra`. La Lambda usa `LabRole`. Parámetros SSM `/telecorp/<stage>/azure/tenant-id`, `/client-id`, `/eventgrid-endpoint` y `/telecorp/<stage>/aws/identity-pool-id`. Detalle en `docs/federacion.md`.
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
- Modelo: **Azure OpenAI** `gpt-4.1-mini` (versión `2025-04-14`, despliegue GlobalStandard en `westus`), API Chat Completions REST `POST <endpoint>/openai/deployments/<despliegue>/chat/completions?api-version=2024-10-21`. Salida JSON forzada con `response_format` `json_schema` y `strict: true` (los esquemas de `prompts.ESQUEMAS`, con `additionalProperties: false`).
- Autenticación sin llaves (la cuenta tiene `local_auth_enabled = false`): mismo flujo que el puente (Cognito Identity → Entra ID) con scope `https://cognitiveservices.azure.com/.default`; el service principal tiene el rol `Cognitive Services OpenAI User`. La Lambda usa `LabRole`.
- Configuración por SSM: `/telecorp/<stage>/azure/tenant-id`, `/client-id`, `/openai-endpoint`, `/openai-deployment` y `/telecorp/<stage>/aws/identity-pool-id` (caché 5 min).
- Todas las respuestas incluyen `fuente`: `"azure-openai"` o `"reglas"`.
- **Respaldo obligatorio:** si Azure OpenAI (o Entra/Cognito/SSM) falla, devuelve algo inválido o tarda más de 8 s en total, la Lambda responde con reglas locales (palabras clave → tipo y severidad; plantillas fijas para avisos). Nunca debe romper la SFN.
- SMS: máximo 160 caracteres.
