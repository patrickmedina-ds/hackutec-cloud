# =============================================================================
# Bloque B · Federación AWS → Azure (credencial de identidad federada en Entra ID)
#
# Las Lambdas `puente-multinube` y `clasificar-ia` (con LabRole) obtienen un JWT
# de Cognito Identity (GetOpenIdTokenForDeveloperIdentity, identidad de desarrollador
# fija) y lo canjean en Entra ID por un token para Event Grid o Azure OpenAI.
# Learner Lab no permite la federación saliente de IAM. Sin secretos de cliente.
# Ver docs/federacion.md.
#
# Usa lo que define el bloque E: var.stage, var.location, providers azurerm y
# azuread (>= 3.0), azurerm_resource_group.main y azurerm_eventgrid_topic.eventos.
# =============================================================================

variable "aws_cognito_identity_pool_id" {
  description = <<-EOT
    IdentityPoolId de Cognito (salida IdentityPoolId del stack puente o SSM
    /telecorp/<stage>/aws/identity-pool-id). Es la audiencia (aud) del JWT.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]:[0-9a-f-]{36}$", var.aws_cognito_identity_pool_id))
    error_message = "Formato esperado: <región>:<uuid> (p. ej. us-east-1:1234abcd-...)."
  }
}

variable "aws_cognito_identity_id" {
  description = <<-EOT
    IdentityId de la identidad de desarrollador fija {"telecorp.aws": "telecorp-<stage>"}.
    Es el sujeto (sub) del JWT. Se obtiene con
    aws cognito-identity get-open-id-token-for-developer-identity (ver docs/federacion.md).
  EOT
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]:[0-9a-f-]{36}$", var.aws_cognito_identity_id))
    error_message = "Formato esperado: <región>:<uuid>."
  }
}

data "azuread_client_config" "actual" {}

locals {
  emisor_cognito = "https://cognito-identity.amazonaws.com"
}

# --- App Registration + service principal ---------------------------------------------
resource "azuread_application" "puente" {
  display_name     = "telecorp-${var.stage}-puente-multinube"
  description      = "Lambdas AWS (puente-multinube y clasificar-ia): Event Grid y Azure OpenAI"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.actual.object_id]
}

resource "azuread_service_principal" "puente" {
  client_id = azuread_application.puente.client_id
  owners    = [data.azuread_client_config.actual.object_id]
}

# --- Credencial federada: confía en el JWT de Cognito Identity de la identidad fija -----
resource "azuread_application_federated_identity_credential" "aws_puente" {
  application_id = azuread_application.puente.id
  display_name   = "aws-${var.stage}-cognito"
  description    = "JWT de Cognito Identity (GetOpenIdTokenForDeveloperIdentity) de telecorp-${var.stage}"
  issuer         = local.emisor_cognito
  subject        = var.aws_cognito_identity_id
  audiences      = [var.aws_cognito_identity_pool_id]
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
    "/telecorp/${var.stage}/azure/openai-endpoint"    = azurerm_cognitive_account.openai.endpoint
    "/telecorp/${var.stage}/azure/openai-deployment"  = azurerm_cognitive_deployment.chat.name
  }
}
