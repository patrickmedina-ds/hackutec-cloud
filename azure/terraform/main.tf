locals {
  prefijo = "telecorp-${var.stage}"
  # Sin guiones para cuentas de almacenamiento (3-24, minúsculas y números).
  prefijo_corto = "telecorp${var.stage}"
  etiquetas     = merge(var.etiquetas, { stage = var.stage })
}

# Sufijo para nombres globalmente únicos (Cosmos DB, almacenamiento, funciones, SWA).
resource "random_string" "sufijo" {
  length  = 5
  special = false
  upper   = false
}

resource "azurerm_resource_group" "main" {
  name     = "${local.prefijo}-rg"
  location = var.location
  tags     = local.etiquetas
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.prefijo}-logs"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.etiquetas
}

resource "azurerm_application_insights" "main" {
  name                = "${local.prefijo}-appi"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = local.etiquetas
}

# Tema de Event Grid. Acuerdo con el bloque B: nombre y esquema fijos. El bloque B
# (federacion.tf) asigna "EventGrid Data Sender" sobre este recurso.
resource "azurerm_eventgrid_topic" "eventos" {
  name                = "${local.prefijo}-eventos"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  input_schema        = "CloudEventSchemaV1_0"
  tags                = local.etiquetas
}
