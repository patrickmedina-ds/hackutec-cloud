#!/usr/bin/env bash
# Desmonta Red TeleCorp en orden inverso al despliegue (deploy.sh).
#
# Uso:
#   STAGE=dev ./scripts/destroy.sh
#
# Pide confirmación escrita antes de borrar nada. Variables opcionales:
#   STAGE       stage a eliminar (por defecto: dev)
#   AWS_REGION  región de AWS (por defecto: us-east-1)

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="${STAGE:-dev}"
REGION="${AWS_REGION:-us-east-1}"
STACK="telecorp-${STAGE}"

paso() { printf '\n==> [%s] %s\n' "$1" "$2"; }
info() { printf '    %s\n' "$*"; }
advertir() { printf 'AVISO: %s\n' "$*" >&2; }
morir() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Devuelve 0 si el stack de CloudFormation existe.
stack_existe() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
paso "0" "Confirmación"
for herramienta in aws sam terraform; do
  command -v "$herramienta" >/dev/null 2>&1 || morir "No se encontró '$herramienta' en el PATH."
done

printf 'Esta acción borra TODO el stage "%s" en AWS y Azure, incluidos los datos.\n' "$STAGE"
printf 'Para confirmar, escribe exactamente: eliminar %s\n> ' "$STACK"
read -r confirmacion
if [[ "$confirmacion" != "eliminar ${STACK}" ]]; then
  morir "Confirmación incorrecta. No se borró nada."
fi

# ---------------------------------------------------------------------------
paso "1/4" "Portal: vaciar el bucket de S3 (CloudFormation no borra buckets con objetos)"
if stack_existe; then
  BUCKET="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='PortalBucket'].OutputValue" --output text)"
  if [[ -n "$BUCKET" && "$BUCKET" != "None" ]]; then
    aws s3 rm "s3://${BUCKET}" --recursive --region "$REGION"
    info "Bucket ${BUCKET} vaciado."
  fi
else
  advertir "El stack ${STACK} no existe en AWS; se omite el vaciado del bucket."
fi

# ---------------------------------------------------------------------------
paso "2/4" "SSM: eliminar los parámetros /telecorp/${STAGE}/"
parametros="$(aws ssm get-parameters-by-path --path "/telecorp/${STAGE}/" --recursive \
  --region "$REGION" --query 'Parameters[].Name' --output text 2>/dev/null || true)"
if [[ -n "$parametros" && "$parametros" != "None" ]]; then
  for nombre in $parametros; do
    aws ssm delete-parameter --name "$nombre" --region "$REGION"
    info "Eliminado ${nombre}"
  done
else
  info "No hay parámetros SSM que eliminar."
fi

# ---------------------------------------------------------------------------
# Las variables obligatorias de federacion.tf también se piden al destruir.
POOL_ID="$(aws ssm get-parameter --name "/telecorp/${STAGE}/aws/identity-pool-id" --region "$REGION" \
  --query Parameter.Value --output text 2>/dev/null || echo "pendiente")"
IDENTIDAD_ID="pendiente"

paso "3/4" "Azure: terraform destroy (Functions, Static Web App, Cosmos DB, Event Grid, etc.)"
terraform -chdir="${RAIZ}/azure/terraform" destroy -input=false -auto-approve \
  -var "stage=${STAGE}" -var "aws_cognito_identity_pool_id=${POOL_ID}" -var "aws_cognito_identity_id=${IDENTIDAD_ID}"

# ---------------------------------------------------------------------------
paso "4/4" "AWS: sam delete del stack ${STACK} (tablas, bus, colas, API, Step Functions, Lambdas)"
if stack_existe; then
  (
    cd "${RAIZ}/aws"
    sam delete --stack-name "$STACK" --region "$REGION" --no-prompts
  )
else
  advertir "El stack ${STACK} ya no existe; no hay nada que borrar en AWS."
fi
info "Los buckets con DeletionPolicy Retain (analítica) se conservan: bórralos a mano si ya no los necesitas."

printf '\nStage %s eliminado. Revisa en la consola que no queden recursos huérfanos.\n' "$STAGE"
