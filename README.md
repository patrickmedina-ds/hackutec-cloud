# Red TeleCorp · Gestión de incidencias en tres nubes

Plataforma **100 % serverless** para que Red TeleCorp reciba, priorice y atienda las incidencias de su servicio móvil e Internet en Lima, sin mantener infraestructura encendida. Corre en **AWS, Google Cloud y Microsoft Azure**, cada nube con un rol claro.

## Qué resuelve

- **Reportes de usuarios:** un portal web permite registrar una incidencia (identificación, número de servicio, ubicación, fecha y hora, tipo y descripción) y consultar su estado.
- **Ciclo de vida:** cada incidencia pasa por *Registrada → En revisión → Atendida → Solucionada → Cerrada*, y queda un historial de cada cambio.
- **Incidentes colectivos:** si 10 o más usuarios distintos reportan en una misma zona en menos de 24 horas, se abre un incidente colectivo y se verifican las antenas de la zona.
- **Telemetría de antenas:** cada antena publica su tráfico en GB/min. Menos de 60 GB/min es **caída**, más de 129 GB/min es **saturada** y 0 GB/min es **inhabilitada**.
- **Priorización:** prioridad = usuarios afectados × peso del tipo de zona (residencial, comercial, educativa, industrial, médica o crítica) × severidad estimada por IA.
- **Recurrencia:** si una antena falla 3 veces o más en 7 días, se abre un ticket estructural con prioridad alta.
- **Aviso a usuarios:** SMS o correo en cada cambio relevante (en revisión, atendida, solucionada).
- **Histórico y analítica:** todo el historial llega a BigQuery; el tablero del NOC está en Looker Studio.
- **IA:** Gemini (Vertex AI) clasifica el tipo y la severidad de cada reporte, resume los incidentes colectivos y redacta los avisos. Si la IA falla o tarda más de 8 s, reglas locales toman el relevo.

## Arquitectura

AWS recibe los reportes y la telemetría, detecta los incidentes y los orquesta con **Step Functions**. Los cambios de estado salen de DynamoDB por Streams hacia **EventBridge**, y de ahí parten las réplicas a Google Cloud y Azure. Ningún componente escribe directamente en otro.

El diagrama completo, con las rutas de datos y los eventos, está en **[arquitectura.html](arquitectura.html)**.

## Qué hace cada nube

| Nube | Responsabilidad |
|---|---|
| **AWS** (`us-east-1`) | Portal (CloudFront + S3), API REST con WAF y cuotas, cola SQS de reportes, DynamoDB como fuente de verdad, IoT Core y Kinesis para la telemetría, Step Functions para la automatización, EventBridge como bus, SNS/SES para los avisos. |
| **Google Cloud** (`us-east1`) | Histórico en BigQuery con vistas analíticas, BigQuery ML para pronósticos, Pub/Sub como réplica de eventos, Cloud Storage dual-region (`NAM4`) como respaldo, Vertex AI con Gemini para clasificar, resumir y redactar. Accede con Workload Identity Federation, sin llaves guardadas. |
| **Azure** (`eastus2`) | Réplica activa del estado en Cosmos DB serverless, Event Grid como canal de réplica, Azure Functions (Flex Consumption) para replicar y notificar, Communication Services como canal de respaldo para SMS y correo, y la Static Web App con la página de estado por zona. |

## Estructura de carpetas

Propiedad de carpetas por bloque (`contratos/README.md`):

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

Carpetas transversales:

- `aws/layers/comun/`: capa Python `telecorp_comun` con estados, umbrales, prioridad y eventos. Se usa desde todas las Lambdas.
- `contratos/`: fuente única de verdad (tablas DynamoDB, eventos, integraciones, OpenAPI). No se edita durante la fase 1.
- `datos/`: catálogo semilla de 12 zonas de Lima y Callao y 44 antenas.

## Requisitos

- **Python 3.12** y **bash**.
- **AWS CLI v2** con credenciales de la cuenta, y **AWS SAM CLI**.
- **Terraform**.
- **Azure CLI** (`az`) con sesión iniciada, y **Azure Functions Core Tools** (`func`).
- **Google Cloud SDK** (`gcloud`) con sesión iniciada.
- **Node.js** LTS (20 o superior) y `npm`. `npx` se usa para la CLI de Static Web Apps.
- Para las pruebas: `pip install -r requirements-dev.txt` (boto3, moto, pytest).

## Despliegue

Todo el despliegue lo orquesta `scripts/deploy.sh`. El stage se toma de `STAGE` (por defecto `dev`):

```bash
STAGE=dev AWS_REGION=us-east-1 ./scripts/deploy.sh
```

Los pasos, en orden:

1. **AWS:** `sam build` y `sam deploy` del stack `telecorp-<stage>`.
2. **GCP y Azure:** `terraform apply` en `gcp/terraform` y `azure/terraform`.
3. **SSM:** se guardan los outputs de Terraform en `/telecorp/<stage>/gcp/…` y `/telecorp/<stage>/azure/…` (WIF, proyecto, región, tenant, client y endpoint de Event Grid).
4. **Datos:** `scripts/seed_dynamodb.py` carga zonas y antenas.
5. **Portal:** se genera `portal/public/config.json` con los outputs de CloudFormation, se hace `npm run build`, `aws s3 sync` y la invalidación de CloudFront.
6. **Azure:** se publican las Azure Functions y la Static Web App.

Para eliminar un stage, `scripts/destroy.sh` hace el proceso inverso y pide escribir `eliminar telecorp-<stage>` como confirmación:

```bash
STAGE=dev ./scripts/destroy.sh
```

Para cargar solo los datos semilla (es idempotente):

```bash
python scripts/seed_dynamodb.py --stage dev --region us-east-1
```

Las zonas se sobrescriben. Las antenas que ya existen no se tocan, así que volver a cargar no reinicia el estado en vivo que escribe `evaluar-antena`.

## Demo con el simulador

Con el stack desplegado, el simulador genera reportes y telemetría de antenas para mostrar el flujo completo:

```bash
python simulador/demo.py
```

## Costos estimados

La solución no tiene nada encendido en reposo. El costo sale casi todo de uso:

- **Cómputo:** Lambda, Step Functions y Azure Functions Flex Consumption se cobran por invocación o por transición. Sin tráfico, el costo es cero.
- **Datos:** DynamoDB está en modo *on-demand* (sin capacidad aprovisionada), Cosmos DB en modo *serverless* y BigQuery cobra por consulta y almacenamiento. Sin escrituras ni consultas, no hay cargos de cómputo.
- **Mensajería:** SQS, EventBridge, Pub/Sub y Event Grid se cobran por mensaje o evento, sin costo fijo mensual.
- **IA:** Gemini se cobra por token y solo se invoca para reportes nuevos o colectivos.
- **Lo que sí queda en reposo:** unos pocos MB de almacenamiento (S3, Cloud Storage, copias de respaldo de DynamoDB) y los registros de CloudWatch, que conviene limitar con retención corta.

Por eso el costo en reposo es prácticamente cero, y el costo crece con los reportes y la telemetría, no con el número de servidores.

## Cobertura de la rúbrica

| Criterio | Pts | Cómo se cubre |
|---|---|---|
| Resolver la mayor cantidad de problemas del contexto | 0–3 | Reportes, ciclo de vida, incidentes colectivos, telemetría de antenas, priorización por tipo de zona, recurrencia, avisos, histórico y analítica. |
| Persistencia y alta disponibilidad de los datos | 0–2 | DynamoDB con PITR y cifrado, multi-AZ; Cosmos DB como réplica; Cloud Storage dual-region `NAM4`; histórico en BigQuery. |
| Uso de Step Functions para automatización | 0–2 | Tres máquinas de estado: `ciclo-vida`, `colectivo` y `recurrencia`, con reglas de EventBridge y Scheduler. |
| Uso de 2 nubes o más | 0–3 | Tres nubes con responsabilidades separadas y autenticación sin llaves guardadas (WIF y credencial federada de Entra ID). |
| Arquitectura de la solución | 0–3 | Diagrama en [arquitectura.html](arquitectura.html), infraestructura como código en SAM y Terraform, y una capa común compartida. |
| Pitch de 5 minutos | 0–2 | Se presenta aparte de este repositorio. |
| Solución completamente serverless | 0–2 | Ningún servicio con servidores fijos: Lambda, Azure Functions, Step Functions, DynamoDB en modo on-demand y Cosmos DB serverless. |
| Uso de IA | 0–3 | Gemini en Vertex AI clasifica y resume, redacta avisos, con respaldo de reglas locales. |

## Pruebas

Las pruebas de datos y scripts están en `scripts/tests/`:

```bash
python -m pytest scripts/tests -q
```

Verifican que los JSON sean válidos, que los IDs sean únicos y tengan el formato correcto, que cada antena apunte a una zona existente, que estén los seis tipos de zona, que el seed funcione sobre moto y que los scripts bash tengan sintaxis correcta (`bash -n`).
