# =============================================================================
# Bloque G · IA en Azure OpenAI (Bedrock está denegado en AWS Learner Lab)
#
# La Lambda `telecorp-<stage>-clasificar-ia` llama a Chat Completions con un token
# de Entra ID obtenido por federación (Cognito Identity → credencial federada de
# azuread_application.puente). Sin llaves: local_auth_enabled = false.
# La cuota de gpt-4.1-mini GlobalStandard está en westus (var.openai_location).
# =============================================================================

resource "azurerm_cognitive_account" "openai" {
  name                  = "${local.prefijo}-openai-${random_string.sufijo.result}"
  location              = var.openai_location
  resource_group_name   = azurerm_resource_group.main.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "${local.prefijo}-openai-${random_string.sufijo.result}"
  local_auth_enabled    = false
  tags                  = local.etiquetas
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.openai_modelo
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.openai_modelo
    version = var.openai_modelo_version
  }

  sku {
    name     = "GlobalStandard"
    capacity = var.openai_capacidad
  }
}

# Solo inferencia (no administra la cuenta ni los despliegues).
resource "azurerm_role_assignment" "puente_openai_user" {
  scope                            = azurerm_cognitive_account.openai.id
  role_definition_name             = "Cognitive Services OpenAI User"
  principal_id                     = azuread_service_principal.puente.object_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
