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
#   SWA_DIR     carpeta con el build de la Static Web App (por defecto: azure/swa/dist)

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="${STAGE:-dev}"
REGION="${AWS_REGION:-us-east-1}"
PYTHON="${PYTHON:-python3}"
STACK="telecorp-${STAGE}"
SWA_DIR="${SWA_DIR:-${RAIZ}/azure/swa/dist}"

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
for herramienta in aws sam terraform az gcloud node; do
  if ! command -v "$herramienta" >/dev/null 2>&1; then
    morir "No se encontró '$herramienta' en el PATH. Instálala y vuelve a intentar."
  fi
  info "$herramienta: OK"
done
command -v "$PYTHON" >/dev/null 2>&1 || morir "No se encontró el intérprete '$PYTHON'."
info "Stage: ${STAGE} · Región AWS: ${REGION}"

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
paso "2/6" "GCP y Azure: terraform apply"
for nube in gcp azure; do
  info "terraform apply en ${nube}/terraform"
  terraform -chdir="${RAIZ}/${nube}/terraform" init -input=false -upgrade >/dev/null
  terraform -chdir="${RAIZ}/${nube}/terraform" apply -input=false -auto-approve \
    -var "stage=${STAGE}"
done

# ---------------------------------------------------------------------------
paso "3/6" "Guardando outputs de Terraform en SSM"
GCP_DIR="${RAIZ}/gcp/terraform"
AZ_DIR="${RAIZ}/azure/terraform"
# Se asigna a variables antes de guardar: así un output faltante detiene el script.
GCP_WIF="$(tf_salida "$GCP_DIR" wif_config)"
GCP_PROYECTO="$(tf_salida "$GCP_DIR" project_id)"
GCP_REGION="$(tf_salida "$GCP_DIR" region)"
AZ_TENANT="$(tf_salida "$AZ_DIR" tenant_id)"
AZ_CLIENTE="$(tf_salida "$AZ_DIR" client_id)"
AZ_EVENTGRID="$(tf_salida "$AZ_DIR" eventgrid_endpoint)"
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
  | "$PYTHON" -c 'import json, sys
salidas = json.load(sys.stdin)
json.dump({s["OutputKey"]: s["OutputValue"] for s in salidas}, sys.stdout, indent=2, ensure_ascii=False)
print()' > "${PORTAL}/public/config.json"
info "portal/public/config.json generado desde los outputs de CloudFormation."

(
  cd "$PORTAL"
  if [[ -f package-lock.json ]]; then npm ci; else npm install; fi
  npm run build
)

BUCKET="$(cfn_salida PortalBucketName)"
DISTRIBUCION="$(cfn_salida CloudFrontDistributionId)"
aws s3 sync "${PORTAL}/dist" "s3://${BUCKET}" --delete --region "$REGION"
aws cloudfront create-invalidation --distribution-id "$DISTRIBUCION" --paths "/*" >/dev/null
info "Portal publicado en s3://${BUCKET} e invalidación de CloudFront ${DISTRIBUCION} creada."

# ---------------------------------------------------------------------------
paso "6/6" "Azure: Functions y Static Web App"
FUNCIONES_DIR="${RAIZ}/azure/functions"
[[ -d "$FUNCIONES_DIR" ]] || morir "No existe la carpeta de Azure Functions: $FUNCIONES_DIR"
FUNCTION_APP="$(tf_salida "$AZ_DIR" function_app_name)"
SWA_NOMBRE="$(tf_salida "$AZ_DIR" static_web_app_name)"

(
  cd "$FUNCIONES_DIR"
  func azure functionapp publish "$FUNCTION_APP" --python
)

[[ -d "$SWA_DIR" ]] || morir "No existe el build de la Static Web App: $SWA_DIR"
TOKEN_SWA="$(az staticwebapp secrets list --name "$SWA_NOMBRE" --query properties.apiKey -o tsv)"
npx --yes @azure/static-web-apps-cli deploy "$SWA_DIR" \
  --deployment-token "$TOKEN_SWA" --env production

printf '\nDespliegue del stage %s terminado.\n' "$STAGE"
