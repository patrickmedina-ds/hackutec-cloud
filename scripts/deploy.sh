#!/usr/bin/env bash
# Despliegue completo de Red TeleCorp en AWS y Azure.
#
# Uso:
#   STAGE=dev ./scripts/deploy.sh
#
# Variables opcionales:
#   STAGE       stage de despliegue (por defecto: dev)
#   AWS_REGION  región de AWS (por defecto: us-east-1)
#   PYTHON      intérprete de Python 3.12 (por defecto: python3)
#   SWA_DIR     carpeta de la página de estado (por defecto: azure/status-page)

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="${STAGE:-dev}"
REGION="${AWS_REGION:-us-east-1}"
PYTHON="${PYTHON:-python3}"
STACK="telecorp-${STAGE}"
SWA_DIR="${SWA_DIR:-${RAIZ}/azure/status-page}"

paso() { printf '\n==> [%s] %s\n' "$1" "$2"; }
info() { printf '    %s\n' "$*"; }
error() { printf 'ERROR: %s\n' "$*" >&2; }
morir() { error "$*"; exit 1; }

# Lee un output de Terraform en crudo. Si falta, se detiene con un mensaje claro.
tf_salida() {
  local dir="$1" nombre="$2"
  terraform -chdir="$dir" output -raw "$nombre" 2>/dev/null \
    || morir "Falta el output '$nombre' de Terraform en $dir (revisar outputs.tf)."
}

# Lee un output del stack de CloudFormation.
cfn_salida() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text \
    || morir "No se pudo leer el output '$1' del stack $STACK."
}

# Guarda un parámetro en SSM Parameter Store con el prefijo /telecorp/<stage>/.
ssm_guardar() {
  local clave="$1" valor="$2"
  aws ssm put-parameter --name "/telecorp/${STAGE}/${clave}" --type String \
    --value "$valor" --overwrite --region "$REGION" >/dev/null
  info "SSM /telecorp/${STAGE}/${clave} guardado."
}

# ---------------------------------------------------------------------------
paso "0/6" "Verificando herramientas necesarias"
for herramienta in aws sam terraform az node func; do
  if ! command -v "$herramienta" >/dev/null 2>&1; then
    morir "No se encontró '$herramienta' en el PATH. Instálala y vuelve a intentar."
  fi
  info "$herramienta: OK"
done
command -v "$PYTHON" >/dev/null 2>&1 || morir "No se encontró el intérprete '$PYTHON'."
info "Stage: ${STAGE} · Región AWS: ${REGION}"
CUENTA_AWS="$(aws sts get-caller-identity --query Account --output text)" \
  || morir "Las credenciales de AWS no son válidas o expiraron (Learner Lab: copia las nuevas en ~/.aws/credentials)."
info "Cuenta AWS: ${CUENTA_AWS}"

# ---------------------------------------------------------------------------
paso "1/6" "AWS: sam build y sam deploy (stack ${STACK})"
(
  cd "${RAIZ}/aws"
  sam build
  sam deploy \
    --stack-name "$STACK" \
    --region "$REGION" \
    --resolve-s3 \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --parameter-overrides "Stage=${STAGE}"
)

# ---------------------------------------------------------------------------
paso "2/6" "Identidad de Cognito para Azure y terraform apply"
# AWS se autentica en Entra ID con un JWT de Cognito Identity Pool (docs/federacion.md).
# El mismo login devuelve siempre el mismo IdentityId, así que este paso es idempotente.
POOL_ID="$(aws ssm get-parameter --name "/telecorp/${STAGE}/aws/identity-pool-id" --region "$REGION" \
  --query Parameter.Value --output text)"
IDENTIDAD_ID="$(aws cognito-identity get-open-id-token-for-developer-identity --region "$REGION" \
  --identity-pool-id "$POOL_ID" --logins "telecorp.aws=telecorp-${STAGE}" --query IdentityId --output text)"
info "Identity Pool: ${POOL_ID} · IdentityId: ${IDENTIDAD_ID}"

# Azure va en dos pasadas: la suscripción de Event Grid exige que la función ya esté
# publicada, así que se crea en el paso 6.
VARS_AZ=(-var "stage=${STAGE}" -var "aws_cognito_identity_pool_id=${POOL_ID}" -var "aws_cognito_identity_id=${IDENTIDAD_ID}")
terraform -chdir="${RAIZ}/azure/terraform" init -input=false -upgrade >/dev/null
terraform -chdir="${RAIZ}/azure/terraform" apply -input=false -auto-approve "${VARS_AZ[@]}" \
  -var "crear_suscripcion_eventgrid=false"

# ---------------------------------------------------------------------------
paso "3/6" "Guardando outputs de Terraform en SSM"
AZ_DIR="${RAIZ}/azure/terraform"
# Se asigna a variables antes de guardar: así un output faltante detiene el script.
AZ_TENANT="$(tf_salida "$AZ_DIR" federacion_tenant_id)"
AZ_CLIENTE="$(tf_salida "$AZ_DIR" federacion_client_id)"
AZ_EVENTGRID="$(tf_salida "$AZ_DIR" federacion_eventgrid_endpoint)"
AZ_OPENAI="$(tf_salida "$AZ_DIR" openai_endpoint)"
AZ_OPENAI_DESPLIEGUE="$(tf_salida "$AZ_DIR" openai_deployment)"
ssm_guardar "azure/tenant-id"          "$AZ_TENANT"
ssm_guardar "azure/client-id"          "$AZ_CLIENTE"
ssm_guardar "azure/eventgrid-endpoint" "$AZ_EVENTGRID"
ssm_guardar "azure/openai-endpoint"    "$AZ_OPENAI"
ssm_guardar "azure/openai-deployment"  "$AZ_OPENAI_DESPLIEGUE"

# ---------------------------------------------------------------------------
paso "4/6" "Sembrando zonas y antenas en DynamoDB"
"$PYTHON" "${RAIZ}/scripts/seed_dynamodb.py" --stage "$STAGE" --region "$REGION"

# ---------------------------------------------------------------------------
paso "5/6" "Portal: callback de Cognito, config.json, build y Azure Static Web App"
# El Learner Lab bloquea CloudFront: el portal vive en Azure Static Web Apps (HTTPS).
PORTAL="${RAIZ}/portal"
[[ -d "$PORTAL" ]] || morir "No existe la carpeta del portal: $PORTAL"
URL_PORTAL="$(tf_salida "$AZ_DIR" portal_url)"

info "Registrando ${URL_PORTAL} como callback de Cognito (segunda pasada de sam deploy)"
(
  cd "${RAIZ}/aws"
  sam deploy \
    --stack-name "$STACK" \
    --region "$REGION" \
    --resolve-s3 \
    --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --parameter-overrides "Stage=${STAGE}" "UrlPortal=${URL_PORTAL}"
)

aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output json \
  | REGION="$REGION" URL_PORTAL="$URL_PORTAL" "$PYTHON" -c 'import json, os, sys
salidas = {s["OutputKey"]: s["OutputValue"] for s in json.load(sys.stdin)}
json.dump({
    "apiUrl": salidas["ApiUrl"],
    "region": os.environ["REGION"],
    "cognitoDomain": salidas["CognitoDomain"],
    "clientId": salidas["UserPoolClientId"],
    "redirectUri": os.environ["URL_PORTAL"],
}, sys.stdout, indent=2)
print()' > "${PORTAL}/public/config.json"
info "portal/public/config.json generado."

(
  cd "$PORTAL"
  if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
  npm run build
)
npx --yes @azure/static-web-apps-cli deploy "${PORTAL}/dist" \
  --deployment-token "$(tf_salida "$AZ_DIR" portal_token)" --env production
info "Portal publicado en ${URL_PORTAL}"

# ---------------------------------------------------------------------------
paso "6/6" "Azure: Functions, suscripción de Event Grid y página de estado"
FUNCIONES_DIR="${RAIZ}/azure/functions"
[[ -d "$FUNCIONES_DIR" ]] || morir "No existe la carpeta de Azure Functions: $FUNCIONES_DIR"
FUNCTION_APP="$(tf_salida "$AZ_DIR" funcion_nombre)"
(
  cd "$FUNCIONES_DIR"
  func azure functionapp publish "$FUNCTION_APP" --python
)

info "Segunda pasada de Terraform en Azure: suscripción de Event Grid hacia replicar"
terraform -chdir="$AZ_DIR" apply -input=false -auto-approve "${VARS_AZ[@]}" \
  -var "crear_suscripcion_eventgrid=true"

# El recurso Flex Consumption reinyecta AzureWebJobsStorage con llave compartida, y el host la
# rechaza (firma MAC inválida). El host usa AzureWebJobsStorage__accountName con identidad.
az functionapp config appsettings delete -g "$(tf_salida "$AZ_DIR" grupo_recursos)" -n "$FUNCTION_APP" \
  --setting-names AzureWebJobsStorage -o none
info "Host de Functions configurado con identidad administrada para su almacenamiento."

[[ -d "$SWA_DIR" ]] || morir "No existe la página de estado: $SWA_DIR"
API_ESTADO="$(tf_salida "$AZ_DIR" api_estado_url)"
printf 'window.TELECORP_API = window.TELECORP_API || "%s";\n' "$API_ESTADO" > "${SWA_DIR}/config.js"
TOKEN_SWA="$(tf_salida "$AZ_DIR" pagina_estado_token)"
npx --yes @azure/static-web-apps-cli deploy "$SWA_DIR" \
  --deployment-token "$TOKEN_SWA" --env production
info "Página de estado: $(tf_salida "$AZ_DIR" pagina_estado_url)"

printf '\nDespliegue del stage %s terminado.\n' "$STAGE"
