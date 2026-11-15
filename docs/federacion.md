# Federación entre nubes y puente multinube (bloque B)

El puente replica cada evento `telecorp.*` del bus `telecorp-<stage>-eventos` a **Azure Event Grid** como CloudEvents 1.0 (contratos/integraciones.md §3). No se guardan llaves ni secretos: Azure confía en un JWT de **Cognito Identity** (identidad de desarrollador fija) mediante una credencial federada de Entra ID. El mismo mecanismo autentica a `clasificar-ia` frente a **Azure OpenAI**. Como la cuenta es un **AWS Academy Learner Lab**, las Lambdas usan `LabRole` (no se crean roles IAM) y la federación saliente de IAM (`sts:GetWebIdentityToken`) no está disponible.

| Pieza | Archivo |
|---|---|
| Stack SAM anidado (Lambda, regla, DLQ, alarmas, Identity Pool, parámetro SSM) | `aws/stacks/puente.yaml` |
| Código de la Lambda | `aws/src/puente_multinube/` |
| JWT de Cognito + canje en Entra + caché (compartido con la IA) | `aws/layers/comun/python/telecorp_comun/entra.py` |
| App Registration, credencial federada, rol en Event Grid | `azure/terraform/federacion.tf` |
| Azure OpenAI (cuenta, despliegue `gpt-4.1-mini`, rol) | `azure/terraform/openai.tf` |
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
 Lambda (LabRole)  puente-multinube  /  clasificar-ia
 (1) cognito-identity:GetOpenIdTokenForDeveloperIdentity
     IdentityPoolId=<pool>, Logins={"telecorp.aws": "telecorp-<stage>"}, TokenDuration=300
        │   (mismo login fijo ⇒ mismo IdentityId para ambas Lambdas)
        ▼
     JWT  iss=https://cognito-identity.amazonaws.com
          sub=<IdentityId>  (us-east-1:<uuid>)
          aud=<IdentityPoolId>
        │
 (2) POST login.microsoftonline.com/<tenant>/oauth2/v2.0/token
     grant_type=client_credentials, client_id=<app>
     client_assertion_type=…jwt-bearer, client_assertion=JWT
     scope=https://eventgrid.azure.net/.default            (puente)
           https://cognitiveservices.azure.com/.default    (IA)
     Entra valida iss/sub/aud contra la credencial federada
     y la firma con el JWKS público de Cognito
        │
 (3) access_token Entra (~1 h) → Bearer
     → Event Grid      (rol EventGrid Data Sender sobre el tema)
     → Azure OpenAI    (rol Cognitive Services OpenAI User sobre la cuenta)
```

Lógica compartida en `telecorp_comun.entra` (`jwt_cognito`, `ProveedorTokenEntra`):
- El token de Entra se reutiliza hasta 5 min antes de `expires_in`; el JWT de Cognito se pide solo al renovar.
- Un 401/403 del recurso descarta el token para que el siguiente intento lo renueve.
- Puente: parámetros SSM `tenant-id`, `client-id`, `eventgrid-endpoint` en una sola `GetParameters` (TTL 5 min); el IdentityPoolId llega por la variable de entorno `COGNITO_IDENTITY_POOL_ID` (`!Ref IdentityPool`).
- IA: `tenant-id`, `client-id`, `openai-endpoint`, `openai-deployment` y `/telecorp/<stage>/aws/identity-pool-id` en una sola `GetParameters` (TTL 5 min).

## 3. Decisión: Cognito Identity Pool (identidades de desarrollador)

Entra ID solo acepta, como credencial federada, un JWT de un emisor OIDC público con un `sub` estable. En Learner Lab no se pueden crear roles IAM y la federación saliente de IAM (`sts:GetWebIdentityToken`) está deshabilitada, así que el único camino sin secretos es **Cognito Identity**:

| | Cognito Identity Pool |
|---|---|
| Recurso | `AWS::Cognito::IdentityPool` `telecorp_<stage>_federacion_azure`, `DeveloperProviderName: telecorp.aws`, `AllowUnauthenticatedIdentities: false`, `AllowClassicFlow: false` |
| Emisor (`iss`) | `https://cognito-identity.amazonaws.com` |
| Audiencia (`aud`) | IdentityPoolId (también en SSM `/telecorp/<stage>/aws/identity-pool-id` y salida `IdentityPoolId` del stack) |
| Sujeto (`sub`) | IdentityId de la identidad `{"telecorp.aws": "telecorp-<stage>"}` |
| Quién puede pedir el JWT | Cualquier principal con `cognito-identity:GetOpenIdTokenForDeveloperIdentity` sobre el pool (en el lab, `LabRole`) |

Consecuencias: el emisor es compartido por todo AWS, pero la confianza queda acotada por `aud` (nuestro pool) y `sub` (nuestra identidad). El IdentityId se crea con la primera llamada y debe pasarse a Terraform (§5). Si se borra y recrea el pool, cambian pool e IdentityId y hay que volver a aplicar Terraform.

## 4. Respaldo de eventos en Blob Storage (otra nube)

La función `replicar` de Azure guarda además cada CloudEvent **crudo** (tal como llega de Event Grid) antes de procesarlo:

- Contenedor `eventos-respaldo`, ruta `aaaa/mm/dd/<tipo>/<ce_id>.json` (fecha UTC del campo `time`; `tipo` e `id` se sanean a un solo segmento).
- **Idempotente**: `upload_blob(overwrite=False)`; si el blob existe (reintento o duplicado) se registra «ya existía» y se continúa.
- Si el almacenamiento falla, `replicar` lanza y Event Grid reintenta (luego dead letter); el procesamiento en Cosmos no ocurre hasta que el respaldo quede guardado.
- Autenticación con `DefaultAzureCredential` (identidad administrada de la Function): sin llaves ni cadenas de conexión. La cuenta tiene `shared_access_key_enabled = false`.
- Terraform (`azure/terraform/respaldo.tf`): cuenta nueva `respaldo` en `westus2` (variable `respaldo_location`; GRS, par `westcentralus`), versionado, retención de borrado 30 días, política de ciclo de vida a **Cool a los 30 días** (blobs y versiones) y rol `Storage Blob Data Contributor` para la identidad de la Function, acotado al contenedor.
- Variables de la Function: `RESPALDO_BLOB_ENDPOINT` (obligatoria para activar el respaldo; si falta, se omite) y `RESPALDO_CONTENEDOR` (por defecto `eventos-respaldo`).

## 5. Configuración paso a paso

Requisitos: el stack raíz desplegado (bus, capa y `RolLabArn` = ARN de `LabRole`) y, en Azure, el resto del Terraform (`azurerm_eventgrid_topic.eventos`).

```bash
STAGE=dev

# 1. AWS: desplegar (el stack puente crea el Identity Pool y el parámetro SSM)
cd aws && sam build && sam deploy --config-env $STAGE && cd ..
POOL=$(aws ssm get-parameter --name /telecorp/$STAGE/aws/identity-pool-id --query Parameter.Value --output text)

# 2. AWS: crear/obtener el IdentityId de la identidad fija (idempotente: mismo login ⇒ mismo id)
IDENTIDAD=$(aws cognito-identity get-open-id-token-for-developer-identity \
  --identity-pool-id "$POOL" --logins telecorp.aws=telecorp-$STAGE \
  --query IdentityId --output text)

# 3. Azure
cd azure/terraform
terraform apply -var stage=$STAGE \
  -var aws_cognito_identity_pool_id=$POOL -var aws_cognito_identity_id=$IDENTIDAD
for clave in tenant-id client-id eventgrid-endpoint openai-endpoint openai-deployment; do
  aws ssm put-parameter --overwrite --type String --name /telecorp/$STAGE/azure/$clave \
    --value "$(terraform output -json federacion_ssm | jq -r --arg k /telecorp/$STAGE/azure/$clave '.[$k]')"
done
cd ../..
```

Las Lambdas releen SSM cada 5 min: no hace falta redesplegarlas tras cargar los parámetros. La asignación de roles en Azure tarda hasta ~5 min en propagarse.

Variables de entorno: puente `DESTINOS` (`azure`), `COGNITO_IDENTITY_POOL_ID`, `COGNITO_PROVEEDOR_DESARROLLADOR`; IA `COGNITO_PROVEEDOR_DESARROLLADOR` (el resto por SSM).

## 6. Cómo probarlo

**Local (sin nubes):**
```bash
pip install -r aws/src/puente_multinube/requirements.txt pytest moto jsonschema
pytest aws/tests/puente azure/tests -q
```
Cubre la conversión a CloudEvents (incluida la validación del `data` contra `contratos/eventos.schema.json`), el fallo del destino (Event Grid sin red, Entra rechaza la federación, falta configuración), el error final del handler, el caché de token de Entra y de SSM, la llamada a `GetOpenIdTokenForDeveloperIdentity` (Stubber de botocore) y, en `azure/tests`, el respaldo en Blob con un cliente simulado (ruta, contenido crudo, idempotencia, errores).

**En la nube, por partes:**
```bash
# JWT de Cognito (ver iss/sub/aud)
aws cognito-identity get-open-id-token-for-developer-identity --identity-pool-id "$POOL" \
  --logins telecorp.aws=telecorp-dev --query Token --output text | cut -d. -f2 | base64 -d 2>/dev/null; echo

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
| `AADSTS700211`/`AADSTS70021` tras recrear el stack | El Identity Pool cambió: repetir §5 pasos 2–3 con el nuevo pool e IdentityId |
| `ResourceNotFoundException` de Cognito | `COGNITO_IDENTITY_POOL_ID` o el parámetro SSM `/aws/identity-pool-id` apuntan a un pool inexistente |
| `clasificar-ia` responde siempre `fuente: "reglas"` | Ver logs ("Respaldo por reglas"): falta SSM `openai-*`, rol `Cognitive Services OpenAI User` sin propagar, o 429 por cuota |
| Event Grid `401` | Rol `EventGrid Data Sender` aún propagándose (hasta ~5 min) o token de otro tenant |

**Redirigir la DLQ** cuando se corrija la causa: los mensajes de Lambda traen el evento original en `requestPayload`; los de EventBridge, el evento tal cual. Basta reenviarlos con `aws lambda invoke --invocation-type Event` a la función.
