# Contratos del proyecto Red TeleCorp

Fuente única de verdad para todos los equipos y agentes. **Nadie cambia un contrato sin avisar**: si un bloque necesita algo distinto, lo anota en `contratos/CAMBIOS_PROPUESTOS.md` y sigue con lo acordado.

| Archivo | Qué define |
|---|---|
| `README.md` | Convenciones, nombres de recursos, regiones, propiedad de carpetas |
| `eventos.md` + `eventos.schema.json` | Eventos del bus `telecorp-<stage>-eventos` y su réplica a GCP y Azure |
| `dynamodb.md` | Tablas, claves, índices y TTL |
| `openapi.yaml` | API REST del portal |
| `integraciones.md` | Mensajes de SQS, IoT, CloudEvents entre nubes, contrato de la IA |

La lógica compartida (estados, umbrales de antenas, prioridad, eventos) ya está implementada en `aws/layers/comun/python/telecorp_comun/`. **Úsala; no la reimplementes.**

## Stack tecnológico

- Lenguaje: **Python 3.12** en todas las funciones (Lambda `python3.12`, Azure Functions Python 3.12, scripts).
- AWS: **AWS SAM** (`aws/template.yaml` raíz + stacks anidados en `aws/stacks/`).
- GCP y Azure: **Terraform** (`gcp/terraform/`, `azure/terraform/`).
- Pruebas: `pytest` (+ `moto` para AWS). Cada bloque deja sus pruebas en `tests/` dentro de su carpeta.

## Regiones

| Nube | Región |
|---|---|
| AWS | `us-east-1` |
| Google Cloud | `us-east1` (bucket de respaldo dual-region `NAM4`) |
| Azure | `eastus2` |

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
| Bus EventBridge | `telecorp-${Stage}-eventos` |
| Cola SQS / DLQ | `telecorp-${Stage}-reportes` / `telecorp-${Stage}-reportes-dlq` |
| Kinesis | `telecorp-${Stage}-telemetria` |
| Step Functions | `telecorp-${Stage}-ciclo-vida`, `telecorp-${Stage}-colectivo`, `telecorp-${Stage}-recurrencia` |
| Lambdas | `telecorp-${Stage}-<nombre-con-guiones>` (p. ej. `telecorp-dev-clasificar-ia`) |
| Parámetros SSM | `/telecorp/${Stage}/<nube>/<clave>` |
| Pub/Sub (GCP) | tema `telecorp-${stage}-eventos` |
| Event Grid (Azure) | tema `telecorp-${stage}-eventos` |

## Variables de entorno estándar de las Lambdas

| Variable | Valor |
|---|---|
| `STAGE` | `dev` |
| `BUS_EVENTOS` | nombre del bus |
| `TABLA_INCIDENCIAS`, `TABLA_HISTORIAL`, `TABLA_ZONA_VENTANA`, `TABLA_ZONAS`, `TABLA_ANTENAS`, `TABLA_METRICAS` | nombres de las tablas |
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
| B · Federación y puente | Opus | `aws/stacks/puente.yaml`, `aws/src/puente_multinube/`, `gcp/terraform/federacion.tf`, `azure/terraform/federacion.tf`, `docs/federacion.md` |
| C · AWS núcleo | Sonnet | `aws/template.yaml`, `aws/src/registrar_reporte/`, `aws/src/evaluar_antena/`, `aws/src/api_incidencias/`, `aws/samconfig.toml` |
| D · GCP | Sonnet | `gcp/` (excepto `federacion.tf`) |
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
