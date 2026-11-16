# Contratos del proyecto Red TeleCorp

Fuente única de verdad para todos los equipos y agentes. **Nadie cambia un contrato sin avisar**: si un bloque necesita algo distinto, lo anota en `contratos/CAMBIOS_PROPUESTOS.md` y sigue con lo acordado.

| Archivo | Qué define |
|---|---|
| `README.md` | Convenciones, nombres de recursos, regiones, propiedad de carpetas |
| `eventos.md` + `eventos.schema.json` | Eventos del bus `telecorp-<stage>-eventos` y su réplica a Azure |
| `dynamodb.md` | Tablas, claves, índices y TTL |
| `openapi.yaml` | API REST del portal |
| `integraciones.md` | Mensajes de SQS, IoT, CloudEvents entre nubes, contrato de la IA |

La lógica compartida (estados, umbrales de antenas, prioridad, eventos) ya está implementada en `aws/layers/comun/python/telecorp_comun/`. **Úsala; no la reimplementes.**

## Stack tecnológico

- Lenguaje: **Python 3.12** en todas las funciones (Lambda `python3.12`, Azure Functions Python 3.12, scripts).
- AWS: **AWS SAM** (`aws/template.yaml` raíz + stacks anidados en `aws/stacks/`).
- Azure: **Terraform** (`azure/terraform/`).
- Pruebas: `pytest` (+ `moto` para AWS). Cada bloque deja sus pruebas en `tests/` dentro de su carpeta.

## Regiones

| Nube | Región |
|---|---|
| AWS | `us-east-1` |
| Azure | `westus2` (la suscripción Azure for Students solo permite chilecentral, westus, canadacentral, westus2 y brazilsouth) |

## Nombres de recursos

Prefijo: `telecorp-${Stage}-`. `Stage` por defecto: `dev`. Los nombres son deterministas, así cada stack referencia recursos de otro con `!Sub` sin exportar salidas.

| Recurso | Nombre |
|---|---|
| Tabla incidencias | `telecorp-${Stage}-incidencias` |
| Tabla historial | `telecorp-${Stage}-historial-estados` |
| Tabla ventana por zona | `telecorp-${Stage}-zona-ventana` |
| Tabla zonas | `telecorp-${Stage}-zonas` |
| Tabla antenas | `telecorp-${Stage}-antenas` |
| Tabla métricas | `telecorp-${Stage}-metricas-antena` |
| Tabla clientes (contratos) | `telecorp-${Stage}-clientes` |
| Bus EventBridge | `telecorp-${Stage}-eventos` |
| Cola SQS / DLQ | `telecorp-${Stage}-reportes` / `telecorp-${Stage}-reportes-dlq` |
| Kinesis | `telecorp-${Stage}-telemetria` |
| Step Functions | `telecorp-${Stage}-ciclo-vida`, `telecorp-${Stage}-colectivo`, `telecorp-${Stage}-recurrencia` |
| Lambdas | `telecorp-${Stage}-<nombre-con-guiones>` (p. ej. `telecorp-dev-clasificar-ia`) |
| Parámetros SSM | `/telecorp/${Stage}/<nube>/<clave>` |
| Event Grid (Azure) | tema `telecorp-${stage}-eventos` |

## Variables de entorno estándar de las Lambdas

| Variable | Valor |
|---|---|
| `STAGE` | `dev` |
| `BUS_EVENTOS` | nombre del bus |
| `TABLA_INCIDENCIAS`, `TABLA_HISTORIAL`, `TABLA_ZONA_VENTANA`, `TABLA_ZONAS`, `TABLA_ANTENAS`, `TABLA_METRICAS`, `TABLA_CLIENTES` | nombres de las tablas |
| `POWERTOOLS_SERVICE_NAME` | nombre de la función |

`telecorp_comun.nombres` lee estas variables y, si faltan, arma el nombre por convención.

## Estructura de stacks SAM

```
aws/template.yaml                 # raíz (bloque C): tablas, bus, cola, Kinesis, IoT, API, Cognito, capa común, Pipes
aws/stacks/orquestacion.yaml      # bloque A: 3 Step Functions + reglas de EventBridge + Scheduler
aws/stacks/puente.yaml            # bloque B: Lambda puente-multinube + regla "todos los eventos"
aws/stacks/ia.yaml                # bloque G: Lambda clasificar-ia
```

Cada stack anidado recibe **exactamente** estos parámetros desde la raíz:

| Parámetro | Tipo |
|---|---|
| `Stage` | String |
| `ComunLayerArn` | String (ARN de la capa `telecorp_comun`) |

## Propiedad de carpetas (fase 1)

| Bloque | Modelo | Carpetas que escribe |
|---|---|---|
| A · Step Functions | Opus | `aws/stacks/orquestacion.yaml`, `aws/statemachines/`, `aws/src/sfn_*/` |
| B · Federación y puente | Opus | `aws/stacks/puente.yaml`, `aws/src/puente_multinube/`, `azure/terraform/federacion.tf`, `docs/federacion.md` |
| C · AWS núcleo | Sonnet | `aws/template.yaml`, `aws/src/registrar_reporte/`, `aws/src/evaluar_antena/`, `aws/src/api_incidencias/`, `aws/samconfig.toml` |
| D · Analítica (antes GCP) | Sonnet | `aws/stacks/analitica.yaml`, `aws/analitica/`, `aws/src/api_analitica/` |
| E · Azure | Sonnet | `azure/` (excepto `federacion.tf`) |
| F · Portal | Sonnet | `portal/` |
| G · IA | Sonnet | `aws/stacks/ia.yaml`, `aws/src/clasificar_ia/` |
| H · Simulador | Haiku | `simulador/` |
| I · Datos y scripts | Haiku | `datos/`, `scripts/`, `README.md` raíz |

Nadie edita `contratos/` ni `aws/layers/comun/` durante la fase 1.

## Decisiones de integración (fase 2)

Detalle de cada bloque en `contratos/CAMBIOS_PROPUESTOS_*.md`. Quedan aceptadas:

- La regla de `colectivo` también recibe `Antena.CambioEstado` con `estadoNuevo = NORMAL`, para restablecer el servicio.
- Los ids de recurrencia son `REC-<antenaId>-<año>-W<semana>` (uno por antena y semana) y los proactivos son `ANT-<ulid>` deterministas.
- El Pipe de `zona-ventana` solo emite `Zona.UmbralSuperado` para marcadores `COL-…`. Los marcadores `ANT-…` de colectivos proactivos no generan evento.
- Remitente de correo: parámetro SSM `/telecorp/<stage>/aws/ses-remitente` (variable `SES_REMITENTE` en `deploy.sh`).
- Azure agrega el contenedor `eventos_procesados` (idempotencia por `ce_id`). Las salidas de Terraform de la federación llevan el prefijo `federacion_`.
- La federación saliente de IAM debe estar activada en la cuenta: `deploy.sh` la activa.
- `Stage` solo admite minúsculas y dígitos, sin guiones (por el dominio de Cognito y la regla de IoT).

## Cambio de alcance: sin Google Cloud

El proyecto quedó en **dos nubes, AWS y Azure** (la cuenta de GCP no tenía facturación activa):
- IA: Gemini en Vertex → **Amazon Bedrock** (`aws/stacks/ia.yaml`).
- Analítica: BigQuery y Looker Studio → **Firehose + S3 + Athena** (`aws/stacks/analitica.yaml`) y API `GET /analitica/{reporte}`.
- Respaldo en otra nube: Cloud Storage → **Azure Blob Storage GRS** (`azure/terraform/respaldo.tf`).
- Se eliminó el pronóstico con BigQuery ML.

## Cambio de entorno: AWS Academy Learner Lab

La cuenta de AWS es un **Learner Lab** (rol `voclabs`). Restricciones verificadas y decisiones:

| Restricción | Decisión |
|---|---|
| No se pueden crear roles IAM | Todas las Lambdas, Step Functions, Pipes, Firehose, Scheduler y reglas usan **`LabRole`**. Cada stack anidado recibe ahora **tres** parámetros: `Stage`, `ComunLayerArn` y `RolLabArn`. |
| Bedrock denegado | La IA usa **Azure OpenAI** (`gpt-4.1-mini`, despliegue GlobalStandard en `westus`). |
| Federación saliente de IAM deshabilitada | AWS → Azure con **Cognito Identity Pool** (identidades de desarrollador, emisor `cognito-identity.amazonaws.com`) + credencial federada de Entra ID. Sin llaves guardadas. |
| SES y SNS SMS denegados | La SFN `ciclo-vida` publica `Notificacion.Solicitada` y **Azure Communication Services** envía el correo (y el SMS si hay número comprado). |

## Cambio de negocio: reporte público solo para clientes con contrato

- Reportar una falla es **público** (sin login), pero solo para personas con un contrato en `telecorp-<stage>-clientes`, identificadas por DNI o número de servicio (`POST /reportes`, `POST /reportes/consulta`, Lambda `api-publica`).
- Cognito queda solo para el personal: operadores (panel) y personal que pide acceso.
- `POST /incidencias` autenticado se mantiene por compatibilidad, pero ahora pasa por `api-publica` con la misma validación de contrato (antes iba directo a SQS sin validar). El portal ya no lo usa.
- `usuarioId` de los reportes es `cliente:<numeroServicio>`: el umbral colectivo cuenta servicios distintos.
