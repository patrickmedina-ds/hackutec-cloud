# =============================================================================
# Bloque B · Federación AWS → GCP (Workload Identity Federation, proveedor AWS)
#
# Las Lambdas `telecorp-<stage>-puente-multinube` y `telecorp-<stage>-clasificar-ia`
# canjean las credenciales temporales de su rol IAM por tokens de Google sin
# llaves guardadas. Ver docs/federacion.md.
#
# Usa lo que define el bloque D: var.project_id, var.region, var.stage,
# provider "google" y google_pubsub_topic.eventos.
# =============================================================================

variable "aws_account_id" {
  description = "Cuenta AWS (12 dígitos) donde viven las Lambdas federadas."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id debe tener 12 dígitos."
  }
}

locals {
  # Roles IAM de nombre fijo (contratos: acuerdos entre bloques B y G).
  rol_puente = "telecorp-${var.stage}-puente-multinube-rol"
  rol_ia     = "telecorp-${var.stage}-clasificar-ia-rol"

  # Valor de attribute.aws_role para una Lambda: el ARN de la sesión asumida sin
  # el nombre de sesión (arn:aws:sts::<cuenta>:assumed-role/<rol>).
  aws_role_puente = "arn:aws:sts::${var.aws_account_id}:assumed-role/${local.rol_puente}"
  aws_role_ia     = "arn:aws:sts::${var.aws_account_id}:assumed-role/${local.rol_ia}"

  wif_pool_name = google_iam_workload_identity_pool.aws.name # projects/<número>/locations/global/workloadIdentityPools/<id>

  principal_puente = "principalSet://iam.googleapis.com/${local.wif_pool_name}/attribute.aws_role/${local.aws_role_puente}"
  principal_ia     = "principalSet://iam.googleapis.com/${local.wif_pool_name}/attribute.aws_role/${local.aws_role_ia}"

  # Archivo de configuración de credenciales externas (equivalente a
  # `gcloud iam workload-identity-pools create-cred-config ... --aws`).
  # En Lambda, google-auth toma las credenciales y la región de las variables
  # de entorno AWS_*; las URL de metadatos solo se usan fuera de Lambda.
  wif_config_base = {
    universe_domain    = "googleapis.com"
    type               = "external_account"
    audience           = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.aws.name}"
    subject_token_type = "urn:ietf:params:aws:token-type:aws4_request"
    token_url          = "https://sts.googleapis.com/v1/token"
    credential_source = {
      environment_id                 = "aws1"
      region_url                     = "http://169.254.169.254/latest/meta-data/placement/availability-zone"
      url                            = "http://169.254.169.254/latest/meta-data/iam/security-credentials"
      regional_cred_verification_url = "https://sts.{region}.amazonaws.com?Action=GetCallerIdentity&Version=2011-06-15"
    }
    # El puente toma de aquí el proyecto del tema de Pub/Sub; además es el proyecto de cuota.
    quota_project_id = var.project_id
  }

  url_impersonacion = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/%s:generateAccessToken"
}

# --- APIs necesarias para la federación ------------------------------------------
resource "google_project_service" "federacion" {
  for_each = toset([
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "serviceusage.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- Pool y proveedor AWS ------------------------------------------------------------
# Ojo: un pool borrado queda 30 días en estado eliminado y su ID no se puede reusar.
resource "google_iam_workload_identity_pool" "aws" {
  project                   = var.project_id
  workload_identity_pool_id = "telecorp-${var.stage}-aws"
  display_name              = "TeleCorp ${var.stage} AWS"
  description               = "Lambdas de Red TeleCorp en la cuenta AWS ${var.aws_account_id}"

  depends_on = [google_project_service.federacion]
}

resource "google_iam_workload_identity_pool_provider" "aws" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.aws.workload_identity_pool_id
  workload_identity_pool_provider_id = "aws-lambdas"
  display_name                       = "AWS Lambdas"
  description                        = "Proveedor AWS: solo los roles del puente y de la IA"

  aws {
    account_id = var.aws_account_id
  }

  attribute_mapping = {
    "google.subject"        = "assertion.arn"
    "attribute.aws_account" = "assertion.account"
    "attribute.aws_role"    = "assertion.arn.contains('assumed-role') ? assertion.arn.extract('{account_arn}assumed-role/') + 'assumed-role/' + assertion.arn.extract('assumed-role/{role_name}/') : assertion.arn"
  }

  # Ningún otro principal de la cuenta AWS puede canjear tokens.
  attribute_condition = "attribute.aws_role in ['${local.aws_role_puente}', '${local.aws_role_ia}']"
}

# --- Acceso directo de las identidades federadas (modo por defecto) -------------------
# Con esto basta el wif-config de SSM, sin cuentas de servicio.
resource "google_pubsub_topic_iam_member" "puente_publisher" {
  project = google_pubsub_topic.eventos.project
  topic   = google_pubsub_topic.eventos.name
  role    = "roles/pubsub.publisher"
  member  = local.principal_puente
}

resource "google_project_iam_member" "ia_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = local.principal_ia
}

# quota_project_id hace que google-auth envíe x-goog-user-project: hace falta serviceusage.services.use.
resource "google_project_iam_member" "federados_service_usage" {
  for_each = {
    puente = local.principal_puente
    ia     = local.principal_ia
  }
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = each.value
}

# --- Cuentas de servicio (plan B: impersonación) ---------------------------------------
# Para servicios que no acepten identidades federadas directas, o si se prefiere
# auditar por cuenta de servicio. El puente lo activa con GCP_IMPERSONAR=true.
resource "google_service_account" "puente" {
  project      = var.project_id
  account_id   = "telecorp-${var.stage}-puente"
  display_name = "TeleCorp ${var.stage} puente multinube (AWS Lambda)"
}

resource "google_service_account" "ia" {
  project      = var.project_id
  account_id   = "telecorp-${var.stage}-ia"
  display_name = "TeleCorp ${var.stage} clasificar-ia (AWS Lambda)"
}

resource "google_service_account_iam_member" "puente_wif" {
  service_account_id = google_service_account.puente.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.principal_puente
}

resource "google_service_account_iam_member" "ia_wif" {
  service_account_id = google_service_account.ia.name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.principal_ia
}

resource "google_pubsub_topic_iam_member" "sa_puente_publisher" {
  project = google_pubsub_topic.eventos.project
  topic   = google_pubsub_topic.eventos.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.puente.email}"
}

resource "google_project_iam_member" "sa_ia_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.ia.email}"
}

# --- Salidas para SSM --------------------------------------------------------------
output "wif_config" {
  description = "JSON para SSM /telecorp/<stage>/gcp/wif-config (acceso directo, lo usan el puente y la IA)."
  value       = jsonencode(local.wif_config_base)
}

output "wif_config_ssm_nombre" {
  value = "/telecorp/${var.stage}/gcp/wif-config"
}

output "wif_config_impersonacion" {
  description = "Plan B: configuraciones con impersonación de cuenta de servicio, por Lambda."
  value = {
    puente = jsonencode(merge(local.wif_config_base, {
      service_account_impersonation_url = format(local.url_impersonacion, google_service_account.puente.email)
    }))
    ia = jsonencode(merge(local.wif_config_base, {
      service_account_impersonation_url = format(local.url_impersonacion, google_service_account.ia.email)
    }))
  }
}

output "wif_proveedor" {
  description = "Nombre completo del proveedor (audience de STS de Google)."
  value       = google_iam_workload_identity_pool_provider.aws.name
}
