resource "azurerm_cosmosdb_account" "main" {
  name                = "${local.prefijo}-cosmos-${random_string.sufijo.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"
  tags                = local.etiquetas

  # Solo Microsoft Entra ID (RBAC de datos); sin llaves.
  local_authentication_enabled = false

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }
}

resource "azurerm_cosmosdb_sql_database" "telecorp" {
  name                = "telecorp"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "incidencias" {
  name                  = "incidencias"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.telecorp.name
  partition_key_paths   = ["/incidenciaId"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "zonas" {
  name                  = "zonas"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.telecorp.name
  partition_key_paths   = ["/zonaId"]
  partition_key_version = 2
}

# Contenedor adicional (no está en el contrato): marcas de idempotencia por ce_id.
# TTL de 7 días; ver contratos/CAMBIOS_PROPUESTOS_E.md.
resource "azurerm_cosmosdb_sql_container" "eventos_procesados" {
  name                  = "eventos_procesados"
  resource_group_name   = azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.telecorp.name
  partition_key_paths   = ["/id"]
  partition_key_version = 2
  default_ttl           = 604800
}

# Rol integrado "Cosmos DB Built-in Data Contributor" para la identidad de la Function.
resource "azurerm_cosmosdb_sql_role_assignment" "funcion" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_function_app_flex_consumption.main.identity[0].principal_id
  scope               = azurerm_cosmosdb_account.main.id
}
