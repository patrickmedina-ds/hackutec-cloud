# Cambios propuestos por el bloque E (Azure)

Ninguno bloquea; son adiciones compatibles con el contrato.

1. **Contenedor extra `eventos_procesados`** (pk `/id`, TTL 7 días) en la base `telecorp` de Cosmos DB, para la idempotencia por `ce_id`. El contrato solo lista `incidencias` y `zonas`.
2. **Forma del documento de `zonas`** (`integraciones.md` solo dice "estado agregado"): `id`/`zonaId`, `estado` (`normal|en_evaluacion|incidencia_identificada|restablecido`), `abiertas` (REGISTRADA/EN_REVISION/ATENDIDA), `totalAbiertas`, `resueltas`, `colectivoActivo`, `incidenteColectivoId`, `prioridadMax`, `antenas` (mapa), `antenasDegradadas` (lista), `actualizadoEn`. El bloque F puede leerlo vía `GET /api/zonas`.
3. **ACS con cadena de conexión** (app setting `ACS_CONNECTION_STRING` inyectada por Terraform) en vez de identidad administrada, por incertidumbre sobre el soporte de Entra ID en SMS/correo. El código usa `DefaultAzureCredential` si la cadena no está.
4. **`Zona.UmbralSuperado` se ignora** en Azure: el colectivo activo se deduce de `snapshot.incidenteColectivoId` de las incidencias abiertas.
