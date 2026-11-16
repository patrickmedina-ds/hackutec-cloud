# Red TeleCorp · Gestión de incidencias multinube

Plataforma **100 % serverless** para que Red TeleCorp reciba, priorice y atienda las incidencias de su servicio móvil e Internet en Lima, sin mantener infraestructura encendida. Corre en **AWS y Microsoft Azure**, cada nube con un rol claro.

## Qué resuelve

- **Reportes de clientes (público, sin login):** cualquier persona con un **contrato de servicio registrado** reporta una falla en el portal con su DNI o su número de servicio, el tipo, la descripción y la hora. La zona y el contacto salen del contrato (tabla `telecorp-<stage>-clientes`); si no hay contrato, no puede reportar (404), si su DNI tiene varios servicios elige uno (409) y si el contrato está suspendido se rechaza (403). Con DNI + número de servicio consulta sus reportes y la línea de tiempo de estados. El login (Cognito) es solo para el personal: el botón **Acceso operadores** abre el Panel de operación.
- **Ciclo de vida:** cada incidencia pasa por *Registrada → En revisión → Atendida → Solucionada → Cerrada*, y queda un historial de cada cambio.
- **Incidentes colectivos:** si 10 o más usuarios distintos reportan en una misma zona en menos de 24 horas, se abre un incidente colectivo y se verifican las antenas de la zona.
- **Telemetría de antenas:** cada antena publica su tráfico en GB/min. Menos de 60 GB/min es **caída**, más de 129 GB/min es **saturada** y 0 GB/min es **inhabilitada**.
- **Priorización:** prioridad = usuarios afectados × peso del tipo de zona (residencial, comercial, educativa, industrial, médica o crítica) × severidad estimada por IA.
- **Recurrencia:** si una antena falla 3 veces o más en 7 días, se abre un ticket estructural con prioridad alta.
- **Aviso a usuarios:** SMS o correo en cada cambio relevante (en revisión, atendida, solucionada).
- **Histórico y analítica:** todos los eventos llegan a S3 por Firehose y se consultan con vistas de Athena; el operador ve los reportes en el portal (`GET /analitica/{reporte}`).
- **IA:** Azure OpenAI (gpt-4.1-mini) clasifica el tipo y la severidad de cada reporte, resume los incidentes colectivos y redacta los avisos. Si la IA falla o tarda más de 8 s, reglas locales toman el relevo.


## Entorno desplegado (stage `dev`)

| Qué | URL |
|---|---|
| Portal (usuario y operador) | https://green-glacier-0a847041e.4.azurestaticapps.net/ |
| Página de estado pública | https://victorious-river-026920e1e.5.azurestaticapps.net |
| API REST | https://v8wh8zzm8e.execute-api.us-east-1.amazonaws.com/dev |
| Diagrama | [arquitectura.html](arquitectura.html) |

El guion del pitch y la demo en vivo están en [docs/pitch.md](docs/pitch.md).

Entorno real: AWS Academy Learner Lab (todo usa `LabRole`; sin CloudFront, Bedrock, SES ni SNS SMS) y una suscripción personal de Azure. Por eso el portal se aloja en Azure Static Web Apps, la IA es Azure OpenAI y los avisos salen por Azure Communication Services.

## Arquitectura

AWS recibe los reportes y la telemetría, detecta los incidentes y los orquesta con **Step Functions**. Los cambios de estado salen de DynamoDB por Streams hacia **EventBridge**, y de ahí parten la analítica en Athena y la réplica a Azure. Ningún componente escribe directamente en otro.

El diagrama completo, con las rutas de datos y los eventos, está en **[arquitectura.html](arquitectura.html)**.

## Qué hace cada nube

| Nube | Responsabilidad |
|---|---|
| **AWS** (`us-east-1`) | API REST con WAF y cuotas, cola SQS de reportes, DynamoDB como fuente de verdad, IoT Core y Kinesis para la telemetría, Step Functions para la automatización, EventBridge como bus, y Firehose + S3 + Athena para el histórico y la analítica. |
| **Azure** (`westus2`) | Respaldo de todos los eventos en Blob Storage GRS, réplica activa del estado en Cosmos DB serverless, Event Grid como canal de réplica (con credencial federada de Entra ID, sin llaves), Azure Functions (Flex Consumption) para replicar y notificar, Communication Services como canal de respaldo para SMS y correo, y la Static Web App con la página de estado por zona. |

## Estructura de carpetas

Propiedad de carpetas por bloque (`contratos/README.md`):

| Bloque | Carpetas que escribe |
|---|---|
| A · Step Functions | `aws/stacks/orquestacion.yaml`, `aws/statemachines/`, `aws/src/sfn_*/` |
| B · Federación y puente | `aws/stacks/puente.yaml`, `aws/src/puente_multinube/`, `azure/terraform/federacion.tf`, `docs/federacion.md` |
| C · AWS núcleo | `aws/template.yaml`, `aws/src/registrar_reporte/`, `aws/src/evaluar_antena/`, `aws/src/api_incidencias/`, `aws/samconfig.toml` |
| D · Analítica | `aws/stacks/analitica.yaml`, `aws/analitica/`, `aws/src/api_analitica/` |
| E · Azure | `azure/` (excepto `federacion.tf`) |
| F · Portal | `portal/` |
| G · IA | `aws/stacks/ia.yaml`, `aws/src/clasificar_ia/` |
| H · Simulador | `simulador/` |
| I · Datos y scripts | `datos/`, `scripts/`, `README.md` raíz |

Carpetas transversales:

- `aws/layers/comun/`: capa Python `telecorp_comun` con estados, umbrales, prioridad y eventos. Se usa desde todas las Lambdas.
- `contratos/`: fuente única de verdad (tablas DynamoDB, eventos, integraciones, OpenAPI). No se edita durante la fase 1.
- `datos/`: catálogo semilla de 12 zonas de Lima y Callao y 44 antenas.

## Requisitos

- **Python 3.12** y **bash**.
- **AWS CLI v2** con credenciales de la cuenta, y **AWS SAM CLI**.
- **Terraform**.
- **Azure CLI** (`az`) con sesión iniciada, y **Azure Functions Core Tools** (`func`).
- Acceso al modelo de Anthropic habilitado en la consola de **Amazon Bedrock** (`us-east-1`).
- **Node.js** LTS (20 o superior) y `npm`. `npx` se usa para la CLI de Static Web Apps.
- Para las pruebas: `pip install -r requirements-dev.txt` (boto3, moto, pytest).

## Despliegue

Todo el despliegue lo orquesta `scripts/deploy.sh`. El stage se toma de `STAGE` (por defecto `dev`):

```bash
STAGE=dev AWS_REGION=us-east-1 ./scripts/deploy.sh
```

Los pasos, en orden:

1. **AWS:** `sam build` y `sam deploy` del stack `telecorp-<stage>`.
2. **Azure:** activa la federación saliente de IAM y hace `terraform apply` en `azure/terraform`.
3. **SSM:** se guardan los outputs de Terraform en `/telecorp/<stage>/azure/…` (tenant, client y endpoint de Event Grid) y el remitente de SES si se definió `SES_REMITENTE`.
4. **Datos:** `scripts/seed_dynamodb.py` carga zonas, antenas y clientes (contratos). El cliente de demo usa el correo de `CORREO_DEMO` (por defecto `cliente.demo@example.com`).
5. **Portal:** se genera `portal/public/config.json` con los outputs de CloudFormation, se hace `npm run build`, `aws s3 sync` y la invalidación de CloudFront.
6. **Azure:** se publican las Azure Functions, se crea la suscripción de Event Grid y se publica la Static Web App.

Para eliminar un stage, `scripts/destroy.sh` hace el proceso inverso y pide escribir `eliminar telecorp-<stage>` como confirmación:

```bash
STAGE=dev ./scripts/destroy.sh
```

Para cargar solo los datos semilla (es idempotente):

```bash
python scripts/seed_dynamodb.py --stage dev --region us-east-1 --correo-demo cliente.demo@example.com
```

Las zonas y los clientes se sobrescriben. Las antenas que ya existen no se tocan, así que volver a cargar no reinicia el estado en vivo que escribe `evaluar-antena`.

### Clientes de demo (`datos/clientes.json`)

`datos/clientes.json` trae 18 contratos por zona (16 activos y 2 suspendidos) y estos clientes de demo:

| DNI | Número de servicio | Zona | Para mostrar |
|---|---|---|---|
| `70000001` | `SRV-00000001` | `LIM-JMA-01` | Reporte normal; el aviso llega a **cliente.demo@example.com** (`--correo-demo` del seed) |
| `70000002` | `SRV-00000002`, `SRV-00000003` | `LIM-JMA-01`, `LIM-MIR-01` | DNI con dos servicios (409, elegir servicio) |
| `70000004` | `SRV-00000004` | `LIM-JMA-01` | Contrato suspendido (403) |

## Demo con el simulador

Con el stack desplegado, el simulador genera reportes y telemetría de antenas para mostrar el flujo completo:

```bash
python simulador/demo.py
```

## Costos estimados

La solución no tiene nada encendido en reposo. El costo sale casi todo de uso:

- **Cómputo:** Lambda, Step Functions y Azure Functions Flex Consumption se cobran por invocación o por transición. Sin tráfico, el costo es cero.
- **Datos:** DynamoDB está en modo *on-demand* (sin capacidad aprovisionada), Cosmos DB en modo *serverless* y Athena cobra por datos escaneados (con tope de 1 GB por consulta). Sin escrituras ni consultas, no hay cargos de cómputo.
- **Mensajería:** SQS, EventBridge, Firehose y Event Grid se cobran por mensaje o evento, sin costo fijo mensual.
- **IA:** Bedrock se cobra por token y solo se invoca para reportes nuevos o colectivos.
- **Lo que sí queda en reposo:** unos pocos MB de almacenamiento (S3, Cloud Storage, copias de respaldo de DynamoDB) y los registros de CloudWatch, que conviene limitar con retención corta.

Por eso el costo en reposo es prácticamente cero, y el costo crece con los reportes y la telemetría, no con el número de servidores.

## Cobertura de la rúbrica

| Criterio | Pts | Cómo se cubre |
|---|---|---|
| Resolver la mayor cantidad de problemas del contexto | 0–3 | Reportes, ciclo de vida, incidentes colectivos, telemetría de antenas, priorización por tipo de zona, recurrencia, avisos, histórico y analítica. |
| Persistencia y alta disponibilidad de los datos | 0–2 | DynamoDB con PITR y cifrado, multi-AZ; Cosmos DB como réplica; todos los eventos respaldados en Azure Blob Storage GRS; histórico en S3 con Athena. |
| Uso de Step Functions para automatización | 0–2 | Tres máquinas de estado: `ciclo-vida`, `colectivo` y `recurrencia`, con reglas de EventBridge y Scheduler. |
| Uso de 2 nubes o más | 0–3 | AWS y Azure con responsabilidades separadas y autenticación sin llaves guardadas (federación saliente de IAM y credencial federada de Entra ID). |
| Arquitectura de la solución | 0–3 | Diagrama en [arquitectura.html](arquitectura.html), infraestructura como código en SAM y Terraform, y una capa común compartida. |
| Pitch de 5 minutos | 0–2 | Se presenta aparte de este repositorio. |
| Solución completamente serverless | 0–2 | Ningún servicio con servidores fijos: Lambda, Azure Functions, Step Functions, DynamoDB en modo on-demand y Cosmos DB serverless. |
| Uso de IA | 0–3 | Azure OpenAI clasifica y resume, redacta avisos, con respaldo de reglas locales. |

## Pruebas

Las pruebas de datos y scripts están en `scripts/tests/`:

```bash
python -m pytest scripts/tests -q
```

Verifican que los JSON sean válidos, que los IDs sean únicos y tengan el formato correcto, que cada antena apunte a una zona existente, que estén los seis tipos de zona, que el seed funcione sobre moto y que los scripts bash tengan sintaxis correcta (`bash -n`).
