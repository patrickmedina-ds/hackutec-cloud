# Federación entre nubes y puente multinube (bloque B)

El puente replica cada evento `telecorp.*` del bus `telecorp-<stage>-eventos` a **Azure Event Grid** como CloudEvents 1.0 (contratos/integraciones.md §3). No se guardan llaves ni secretos: Azure confía en la identidad del rol IAM de la Lambda (credencial federada de Entra ID).

| Pieza | Archivo |
|---|---|
| Stack SAM anidado (Lambda, regla, DLQ, alarmas, rol) | `aws/stacks/puente.yaml` |
| Código de la Lambda | `aws/src/puente_multinube/` |
| App Registration, credencial federada, rol en Event Grid | `azure/terraform/federacion.tf` |
| Respaldo de eventos en Blob Storage (cuenta GRS, versionado, ciclo de vida) | `azure/terraform/respaldo.tf`, `azure/functions/telecorp_azure/respaldo.py` |
| Pruebas | `aws/tests/puente/`, `azure/tests/` |

## 1. Flujo de eventos

```
EventBridge  telecorp-<stage>-eventos
   │  regla telecorp-<stage>-puente-todos   {"source":[{"prefix":"telecorp."}]}
   │  (si no logra entregar: reintentos de EventBridge 24 h → DLQ)
   ▼
Lambda telecorp-<stage>-puente-multinube  (invocación asíncrona)
   │  a_cloudevent(evento)  →  {"specversion":"1.0","id","source":"telecorp/aws/<source>","type","time","subject","data"}
   └──► Event Grid  <endpoint>/api/events?api-version=2018-01-01
            Content-Type: application/cloudevents-batch+json · cuerpo [CloudEvent]
   Si el envío falla → la función lanza ErrorReplica
   → Lambda reintenta 2 veces → destino OnFailure = DLQ telecorp-<stage>-puente-dlq
   → alarma telecorp-<stage>-puente-dlq-con-mensajes
```

**Reintentos, con precisión.** EventBridge invoca a Lambda de forma *asíncrona*; su `RetryPolicy` y su `DeadLetterConfig` solo cubren fallos de *entrega* (throttling, función inexistente). Cuando la función lanza un error, quien reintenta es Lambda (`EventInvokeConfig`: 2 reintentos, máx. 1 h) y luego envía el evento al destino `OnFailure`. Ambos caminos terminan en la misma DLQ: los mensajes de EventBridge traen el evento tal cual y los de Lambda lo traen en `requestPayload`.

**Duplicados.** Un reintento vuelve a publicar el evento, aunque Event Grid ya lo hubiera recibido. Es entrega *al menos una vez*: los consumidores deben deduplicar por el `id` del CloudEvent (en Cosmos se reclama el `ce_id`; el respaldo en Blob usa `overwrite=False`).

## 2. Flujo de tokens

```
 Lambda (rol telecorp-<stage>-puente-multinube-rol)
 (1) sts:GetWebIdentityToken (STS regional us-east-1)
     Audience=api://AzureADTokenExchange, RS256, 300 s
        │
        ▼
     JWT  iss=https://<id>.tokens.sts.global.api.aws
          sub=arn:aws:iam::<cuenta>:role/telecorp-<stage>-puente-multinube-rol
          aud=api://AzureADTokenExchange
        │
 (2) POST login.microsoftonline.com/<tenant>/oauth2/v2.0/token
     grant_type=client_credentials, client_id=<app>
     client_assertion_type=…jwt-bearer, client_assertion=JWT
     scope=https://eventgrid.azure.net/.default
     Entra valida iss/sub/aud contra la credencial federada
     y la firma con el JWKS público del emisor AWS
        │
 (3) access_token Entra (~1 h) → Bearer → Event Grid
     (rol EventGrid Data Sender sobre el tema)
```

**Caché entre invocaciones** (variables de módulo, viven lo que el contenedor):
- Parámetros SSM: una sola llamada `GetParameters` para los tres, TTL 5 min (`CONFIG_TTL_SEGUNDOS`).
- Azure: el token de Entra se reutiliza hasta 5 min antes de `expires_in`; el JWT de AWS se pide solo al renovar.
- Un 401/403 de Event Grid descarta el token para que el siguiente intento lo renueve.
- Una sola `requests.Session` (conexiones keep-alive) para el destino.

## 3. Decisión: cómo obtiene la Lambda un JWT OIDC para Entra ID

Entra ID solo acepta, como credencial federada, un JWT de un emisor OIDC público con un `sub` estable. AWS no da a una Lambda un JWT por defecto, así que se evaluaron dos opciones:

| | **A. Federación saliente de IAM** (`sts:GetWebIdentityToken`) — **elegida** | B. Cognito Identity Pool (`GetOpenIdTokenForDeveloperIdentity`) — plan B |
|---|---|---|
| Disponibilidad | GA desde 19‑nov‑2025; se activa una vez por cuenta (`aws iam enable-outbound-web-identity-federation`) | Disponible desde hace años |
| Recursos extra | Ninguno | Identity Pool + proveedor de desarrollador + permiso `cognito-identity:GetOpenIdTokenForDeveloperIdentity` |
| Emisor (`iss`) | `https://<id>.tokens.sts.global.api.aws` (propio de la cuenta) | `https://cognito-identity.amazonaws.com` (compartido por todo AWS) |
| `sub` | ARN del rol IAM (`arn:aws:iam::<cuenta>:role/<rol>`): estable y legible | IdentityId (`us-east-1:<uuid>`): hay que crearlo antes y copiarlo a Terraform |
| `aud` | Elegida por el que llama; IAM la restringe (`sts:IdentityTokenAudience`) | Siempre el IdentityPoolId |
| Control en IAM | Condiciones `sts:IdentityTokenAudience`, `sts:SigningAlgorithm`, `sts:DurationSeconds` | Solo quién puede llamar a la API |
| Claims extra | Cuenta, organización, tags del principal, ARN de la función Lambda de origen | Ninguno útil |

**Se elige A** porque el `sub` *es* el rol de nombre fijo que ya acordamos (`telecorp-<stage>-puente-multinube-rol`), la confianza en Entra es por emisor propio de la cuenta (no por un emisor global compartido), no requiere recursos ni pasos manuales adicionales, y la política del rol limita la audiencia a `api://AzureADTokenExchange`, el algoritmo a RS256 y la vida a 5 min. Se usa RS256 (no ES384) porque es el algoritmo que Entra valida con certeza.

Requisitos de A: `boto3 >= 1.42` (se empaqueta en `requirements.txt` por si el del runtime es anterior) y endpoint **regional** de STS (la API no existe en el endpoint global; el código usa `https://sts.<región>.amazonaws.com`).

**Plan B (Cognito)**, por si la federación saliente no puede activarse en la cuenta:
1. Crear un Identity Pool con un *developer provider* (p. ej. `telecorp.puente`) y dar al rol `cognito-identity:GetOpenIdTokenForDeveloperIdentity` sobre el pool.
2. Invocar una vez `GetOpenIdTokenForDeveloperIdentity` con `Logins={"telecorp.puente": "telecorp-<stage>-puente-multinube"}` y anotar el `IdentityId`.
3. En `azure/terraform`: `aws_oidc_issuer_url = "https://cognito-identity.amazonaws.com"`, `aws_oidc_subject = <IdentityId>`, `aws_oidc_audience = <IdentityPoolId>`.
4. En la Lambda: `AZURE_ORIGEN_JWT=cognito`, `COGNITO_IDENTITY_POOL_ID`, `COGNITO_PROVEEDOR_DESARROLLADOR` (ya implementado en `destino_azure.jwt_cognito`).

## 4. Respaldo de eventos en Blob Storage (otra nube)

La función `replicar` de Azure guarda además cada CloudEvent **crudo** (tal como llega de Event Grid) antes de procesarlo:

- Contenedor `eventos-respaldo`, ruta `aaaa/mm/dd/<tipo>/<ce_id>.json` (fecha UTC del campo `time`; `tipo` e `id` se sanean a un solo segmento).
- **Idempotente**: `upload_blob(overwrite=False)`; si el blob existe (reintento o duplicado) se registra «ya existía» y se continúa.
- Si el almacenamiento falla, `replicar` lanza y Event Grid reintenta (luego dead letter); el procesamiento en Cosmos no ocurre hasta que el respaldo quede guardado.
- Autenticación con `DefaultAzureCredential` (identidad administrada de la Function): sin llaves ni cadenas de conexión. La cuenta tiene `shared_access_key_enabled = false`.
- Terraform (`azure/terraform/respaldo.tf`): cuenta nueva `respaldo` en `westus2` (variable `respaldo_location`; GRS, par `westcentralus`), versionado, retención de borrado 30 días, política de ciclo de vida a **Cool a los 30 días** (blobs y versiones) y rol `Storage Blob Data Contributor` para la identidad de la Function, acotado al contenedor.
- Variables de la Function: `RESPALDO_BLOB_ENDPOINT` (obligatoria para activar el respaldo; si falta, se omite) y `RESPALDO_CONTENEDOR` (por defecto `eventos-respaldo`).

## 5. Configuración paso a paso

Requisitos: el stack raíz desplegado (bus y capa) y, en Azure, el recurso del bloque E (`azurerm_eventgrid_topic.eventos` con esquema de entrada `CloudEventSchemaV1_0`).

```bash
STAGE=dev
CUENTA=$(aws sts get-caller-identity --query Account --output text)

# 1. AWS: activar la federación saliente (una vez por cuenta) y anotar el emisor
aws iam enable-outbound-web-identity-federation || true
EMISOR=$(aws iam get-outbound-web-identity-federation-info --query IssuerUrl --output text)

# 2. AWS: desplegar (el stack puente crea el rol de nombre fijo)
cd aws && sam build && sam deploy --config-env $STAGE && cd ..

# 3. Azure
cd azure/terraform
terraform apply -var stage=$STAGE -var aws_account_id=$CUENTA -var aws_oidc_issuer_url=$EMISOR
for clave in tenant-id client-id eventgrid-endpoint; do
  salida=federacion_$(echo $clave | tr - _)
  aws ssm put-parameter --overwrite --type String \
    --name /telecorp/$STAGE/azure/$clave --value "$(terraform output -raw $salida)"
done
cd ../..
```

El orden 2 → 3 no es obligatorio: Entra no verifica que el rol exista al crear la confianza. La Lambda relee SSM cada 5 min, así que no hace falta redesplegarla tras cargar los parámetros.

Variables de entorno de la Lambda (en `puente.yaml`): `DESTINOS` (`azure`), `AZURE_ORIGEN_JWT` (`iam` | `cognito`).

## 6. Cómo probarlo

**Local (sin nubes):**
```bash
pip install -r aws/src/puente_multinube/requirements.txt pytest moto jsonschema
pytest aws/tests/puente azure/tests -q
```
Cubre la conversión a CloudEvents (incluida la validación del `data` contra `contratos/eventos.schema.json`), el fallo del destino (Event Grid sin red, Entra rechaza la federación, falta configuración), el error final del handler, el caché de token de Entra y de SSM, la llamada a `GetWebIdentityToken` (Stubber de botocore) y, en `azure/tests`, el respaldo en Blob con un cliente simulado (ruta, contenido crudo, idempotencia, errores).

**En la nube, por partes:**
```bash
# JWT de AWS (con credenciales de un principal que tenga sts:GetWebIdentityToken)
aws sts get-web-identity-token --region us-east-1 --endpoint-url https://sts.us-east-1.amazonaws.com \
  --audience api://AzureADTokenExchange --signing-algorithm RS256 --duration-seconds 300 \
  --query WebIdentityToken --output text | cut -d. -f2 | base64 -d 2>/dev/null; echo   # ver iss/sub/aud

# Evento de prueba de punta a punta
aws events put-events --entries '[{"EventBusName":"telecorp-dev-eventos","Source":"telecorp.zonas",
  "DetailType":"Zona.UmbralSuperado","Detail":"{\"zonaId\":\"LIM-MIR-01\",\"incidenteColectivoId\":\"COL-PRUEBA\",\"usuariosDistintos\":10,\"ventanaInicio\":\"2026-11-14T15:00:00Z\",\"ventanaFin\":\"2026-11-15T14:03:00Z\",\"incidencias\":[]}"}]'

sam logs -n telecorp-dev-puente-multinube --tail          # "replicado" (destino azure)
az storage blob list --account-name <respaldo> -c eventos-respaldo --auth-mode login -o table   # respaldo
# Azure: métricas "Published Events" del tema o el consumidor del bloque E
aws sqs get-queue-attributes --queue-url <PuenteDLQUrl> --attribute-names ApproximateNumberOfMessages
```

**Errores típicos:**
| Síntoma (logs del puente) | Causa |
|---|---|
| `AADSTS70021: No matching federated identity record` | `issuer`, `subject` o `audience` de la credencial federada no coinciden con el JWT (comparar con el JWT decodificado; el `iss` sin `/` final) |
| `AccessDenied ... sts:GetWebIdentityToken` | Federación saliente sin activar o la petición no cumple las condiciones de la política (audiencia, RS256, ≤ 300 s) |
| `OutboundWebIdentityFederationDisabledException` | Falta `aws iam enable-outbound-web-identity-federation` |
| Event Grid `401` | Rol `EventGrid Data Sender` aún propagándose (hasta ~5 min) o token de otro tenant |

**Redirigir la DLQ** cuando se corrija la causa: los mensajes de Lambda traen el evento original en `requestPayload`; los de EventBridge, el evento tal cual. Basta reenviarlos con `aws lambda invoke --invocation-type Event` a la función.
