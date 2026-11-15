#!/usr/bin/env bash
# Despliegue completo de Red TeleCorp en AWS, GCP y Azure.
#
# Uso:
#   STAGE=dev ./scripts/deploy.sh
#
# Variables opcionales:
#   STAGE       stage de despliegue (por defecto: dev)
#   AWS_REGION  región de AWS (por defecto: us-east-1)
#   PYTHON      intérprete de Python 3.12 (por defecto: python3)
#   GCP_PROJECT_ID  proyecto de Google Cloud (o defínelo en gcp/terraform/terraform.tfvars)
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
for herramienta in aws sam terraform az gcloud node func; do
  if ! command -v "$herramienta" >/dev/null 2>&1; then
    morir "No se encontró '$herramienta' en el PATH. Instálala y vuelve a intentar."
  fi
  info "$herramienta: OK"
done
command -v "$PYTHON" >/dev/null 2>&1 || morir "No se encontró el intérprete '$PYTHON'."
info "Stage: ${STAGE} · Región AWS: ${REGION}"
CUENTA_AWS="$(aws sts get-caller-identity --query Account --output text)"
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
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
    --no-confirm-changeset \
    --no-fail-on-empty-changeset \
    --parameter-overrides "Stage=${STAGE}"
)

# ---------------------------------------------------------------------------
paso "2/6" "Federación saliente de IAM, GCP y Azure: terraform apply"
# El puente obtiene su JWT para Entra ID con sts:GetWebIdentityToken (docs/federacion.md).
aws iam enable-outbound-web-identity-federation >/dev/null 2>&1 || true
EMISOR_AWS="$(aws iam get-outbound-web-identity-federation-info --query IssuerUrl --output text)"
info "Emisor OIDC de la cuenta: ${EMISOR_AWS}"

VARS_GCP=(-var "stage=${STAGE}" -var "aws_account_id=${CUENTA_AWS}")
[[ -n "${GCP_PROJECT_ID:-}" ]] && VARS_GCP+=(-var "project_id=${GCP_PROJECT_ID}")
terraform -chdir="${RAIZ}/gcp/terraform" init -input=false -upgrade >/dev/null
terraform -chdir="${RAIZ}/gcp/terraform" apply -input=false -auto-approve "${VARS_GCP[@]}"

# Azure va en dos pasadas: la suscripción de Event Grid exige que la función ya esté
# publicada, así que se crea en el paso 6.
VARS_AZ=(-var "stage=${STAGE}" -var "aws_account_id=${CUENTA_AWS}" -var "aws_oidc_issuer_url=${EMISOR_AWS}")
terraform -chdir="${RAIZ}/azure/terraform" init -input=false -upgrade >/dev/null
terraform -chdir="${RAIZ}/azure/terraform" apply -input=false -auto-approve "${VARS_AZ[@]}" \
  -var "crear_suscripcion_eventgrid=false"

# ---------------------------------------------------------------------------
paso "3/6" "Guardando outputs de Terraform en SSM"
GCP_DIR="${RAIZ}/gcp/terraform"
AZ_DIR="${RAIZ}/azure/terraform"
# Se asigna a variables antes de guardar: así un output faltante detiene el script.
GCP_WIF="$(tf_salida "$GCP_DIR" wif_config)"
GCP_PROYECTO="$(tf_salida "$GCP_DIR" project_id)"
GCP_REGION="$(tf_salida "$GCP_DIR" region)"
AZ_TENANT="$(tf_salida "$AZ_DIR" federacion_tenant_id)"
AZ_CLIENTE="$(tf_salida "$AZ_DIR" federacion_client_id)"
AZ_EVENTGRID="$(tf_salida "$AZ_DIR" federacion_eventgrid_endpoint)"
ssm_guardar "gcp/wif-config"           "$GCP_WIF"
ssm_guardar "gcp/project-id"           "$GCP_PROYECTO"
ssm_guardar "gcp/region"               "$GCP_REGION"
ssm_guardar "azure/tenant-id"          "$AZ_TENANT"
ssm_guardar "azure/client-id"          "$AZ_CLIENTE"
ssm_guardar "azure/eventgrid-endpoint" "$AZ_EVENTGRID"

# ---------------------------------------------------------------------------
paso "4/6" "Sembrando zonas y antenas en DynamoDB"
"$PYTHON" "${RAIZ}/scripts/seed_dynamodb.py" --stage "$STAGE" --region "$REGION"

# ---------------------------------------------------------------------------
paso "5/6" "Portal: config.json, build, S3 sync e invalidación de CloudFront"
PORTAL="${RAIZ}/portal"
[[ -d "$PORTAL" ]] || morir "No existe la carpeta del portal: $PORTAL"

aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output json \
  | REGION="$REGION" "$PYTHON" -c 'import json, os, sys
salidas = {s["OutputKey"]: s["OutputValue"] for s in json.load(sys.stdin)}
dominio = salidas["CloudFrontDomain"]
json.dump({
    "apiUrl": salidas["ApiUrl"],
    "region": os.environ["REGION"],
    "cognitoDomain": salidas["CognitoDomain"],
    "clientId": salidas["UserPoolClientId"],
    "redirectUri": dominio if dominio.startswith("https://") else f"https://{dominio}/",
}, sys.stdout, indent=2)
print()' > "${PORTAL}/public/config.json"
info "portal/public/config.json generado desde los outputs de CloudFormation."

(
  cd "$PORTAL"
  if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
  npm run build
)

BUCKET="$(cfn_salida PortalBucket)"
DISTRIBUCION="$(cfn_salida CloudFrontDistributionId)"
aws s3 sync "${PORTAL}/dist" "s3://${BUCKET}" --delete --region "$REGION"
aws cloudfront create-invalidation --distribution-id "$DISTRIBUCION" --paths "/*" >/dev/null
info "Portal publicado en s3://${BUCKET} e invalidación de CloudFront ${DISTRIBUCION} creada."

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

[[ -d "$SWA_DIR" ]] || morir "No existe la página de estado: $SWA_DIR"
API_ESTADO="$(tf_salida "$AZ_DIR" api_estado_url)"
printf 'window.TELECORP_API = window.TELECORP_API || "%s";\n' "$API_ESTADO" > "${SWA_DIR}/config.js"
TOKEN_SWA="$(tf_salida "$AZ_DIR" pagina_estado_token)"
npx --yes @azure/static-web-apps-cli deploy "$SWA_DIR" \
  --deployment-token "$TOKEN_SWA" --env production
info "Página de estado: $(tf_salida "$AZ_DIR" pagina_estado_url)"

printf '\nDespliegue del stage %s terminado.\n' "$STAGE"
