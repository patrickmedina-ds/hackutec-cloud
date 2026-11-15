# =============================================================================
# Bloque B · Federación AWS → Azure (credencial de identidad federada en Entra ID)
#
# La Lambda `telecorp-<stage>-puente-multinube` obtiene un JWT de la federación
# saliente de IAM (sts:GetWebIdentityToken) y lo canjea en Entra ID por un token
# para Event Grid. No hay secretos de cliente. Ver docs/federacion.md.
#
# Usa lo que define el bloque E: var.stage, var.location, providers azurerm y
# azuread (>= 3.0), azurerm_resource_group.main y azurerm_eventgrid_topic.eventos.
# =============================================================================

variable "aws_account_id" {
  description = "Cuenta AWS (12 dígitos) donde vive la Lambda puente-multinube."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id debe tener 12 dígitos."
  }
}

variable "aws_oidc_issuer_url" {
  description = <<-EOT
    Emisor OIDC de la cuenta AWS para la federación saliente de IAM, exactamente como
    aparece en el claim `iss` (p. ej. https://<id>.tokens.sts.global.api.aws).
    Se obtiene con `aws iam get-outbound-web-identity-federation-info`.
    Plan B (Cognito): https://cognito-identity.amazonaws.com
  EOT
  type        = string

  validation {
    condition     = startswith(var.aws_oidc_issuer_url, "https://") && !endswith(var.aws_oidc_issuer_url, "/")
    error_message = "El emisor debe empezar con https:// y no terminar en '/' (Entra compara el iss literalmente)."
  }
}

variable "aws_oidc_subject" {
  description = <<-EOT
    Sujeto esperado en el JWT. Vacío = ARN del rol del puente (federación saliente de IAM).
    Plan B (Cognito): el IdentityId de la identidad de desarrollador (us-east-1:<uuid>).
  EOT
  type        = string
  default     = ""
}

variable "aws_oidc_audience" {
  description = "Audiencia del JWT. Federación saliente: api://AzureADTokenExchange. Plan B: el IdentityPoolId de Cognito."
  type        = string
  default     = "api://AzureADTokenExchange"
}

data "azuread_client_config" "actual" {}

locals {
  rol_puente_arn = "arn:aws:iam::${var.aws_account_id}:role/telecorp-${var.stage}-puente-multinube-rol"
  sujeto_aws     = var.aws_oidc_subject != "" ? var.aws_oidc_subject : local.rol_puente_arn
}

# --- App Registration + service principal ---------------------------------------------
resource "azuread_application" "puente" {
  display_name     = "telecorp-${var.stage}-puente-multinube"
  description      = "Lambda AWS puente-multinube: publica CloudEvents en Event Grid"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.actual.object_id]
}

resource "azuread_service_principal" "puente" {
  client_id = azuread_application.puente.client_id
  owners    = [data.azuread_client_config.actual.object_id]
}

# --- Credencial federada: confía en el JWT emitido por AWS para el rol del puente -------
resource "azuread_application_federated_identity_credential" "aws_puente" {
  application_id = azuread_application.puente.id
  display_name   = "aws-${var.stage}-puente-multinube"
  description    = "JWT de AWS (sts:GetWebIdentityToken) del rol ${local.rol_puente_arn}"
  issuer         = var.aws_oidc_issuer_url
  subject        = local.sujeto_aws
  audiences      = [var.aws_oidc_audience]
}

# --- Permiso mínimo: solo enviar eventos al tema ---------------------------------------
resource "azurerm_role_assignment" "puente_eventgrid_sender" {
  scope                            = azurerm_eventgrid_topic.eventos.id
  role_definition_name             = "EventGrid Data Sender"
  principal_id                     = azuread_service_principal.puente.object_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# --- Salidas para SSM (/telecorp/<stage>/azure/...) -------------------------------------
output "federacion_tenant_id" {
  description = "SSM /telecorp/<stage>/azure/tenant-id"
  value       = data.azuread_client_config.actual.tenant_id
}

output "federacion_client_id" {
  description = "SSM /telecorp/<stage>/azure/client-id"
  value       = azuread_application.puente.client_id
}

output "federacion_eventgrid_endpoint" {
  description = "SSM /telecorp/<stage>/azure/eventgrid-endpoint"
  value       = azurerm_eventgrid_topic.eventos.endpoint
}

output "federacion_ssm" {
  description = "Mapa nombre SSM -> valor, listo para `aws ssm put-parameter`."
  value = {
    "/telecorp/${var.stage}/azure/tenant-id"          = data.azuread_client_config.actual.tenant_id
    "/telecorp/${var.stage}/azure/client-id"          = azuread_application.puente.client_id
    "/telecorp/${var.stage}/azure/eventgrid-endpoint" = azurerm_eventgrid_topic.eventos.endpoint
  }
}
