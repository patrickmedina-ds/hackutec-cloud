resource "azurerm_storage_account" "funcion" {
  name                            = substr("${local.prefijo_corto}fn${random_string.sufijo.result}", 0, 24)
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.etiquetas
}

# Paquete de despliegue de Flex Consumption.
resource "azurerm_storage_container" "despliegue" {
  name                  = "despliegue"
  storage_account_id    = azurerm_storage_account.funcion.id
  container_access_type = "private"
}

# Dead letter de Event Grid.
resource "azurerm_storage_container" "deadletter" {
  name                  = "eventgrid-deadletter"
  storage_account_id    = azurerm_storage_account.funcion.id
  container_access_type = "private"
}

resource "azurerm_service_plan" "funcion" {
  name                = "${local.prefijo}-plan"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  os_type             = "Linux"
  sku_name            = "FC1" # Flex Consumption
  tags                = local.etiquetas
}

resource "azurerm_function_app_flex_consumption" "main" {
  name                = "${local.prefijo}-funciones-${random_string.sufijo.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.funcion.id
  tags                = local.etiquetas

  storage_container_type      = "blobContainer"
  storage_container_endpoint  = "${azurerm_storage_account.funcion.primary_blob_endpoint}${azurerm_storage_container.despliegue.name}"
  storage_authentication_type = "SystemAssignedIdentity"

  runtime_name           = "python"
  runtime_version        = "3.12"
  maximum_instance_count = 40
  instance_memory_in_mb  = 2048

  identity {
    type = "SystemAssigned"
  }

  site_config {
    cors {
      allowed_origins = concat(
        ["https://${azurerm_static_web_app.estado.default_host_name}"],
        var.cors_origenes_extra,
      )
    }
  }

  app_settings = {
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    # El host usa identidad administrada para su almacenamiento (con la llave fallaba la firma).
    AzureWebJobsStorage__accountName = azurerm_storage_account.funcion.name
    STAGE                            = var.stage
    COSMOS_ENDPOINT                  = azurerm_cosmosdb_account.main.endpoint
    COSMOS_BASE                      = azurerm_cosmosdb_sql_database.telecorp.name
    COSMOS_CONTENEDOR_INCIDENCIAS    = azurerm_cosmosdb_sql_container.incidencias.name
    COSMOS_CONTENEDOR_ZONAS          = azurerm_cosmosdb_sql_container.zonas.name
    COSMOS_CONTENEDOR_EVENTOS        = azurerm_cosmosdb_sql_container.eventos_procesados.name
    RESPALDO_BLOB_ENDPOINT           = azurerm_storage_account.respaldo.primary_blob_endpoint
    RESPALDO_CONTENEDOR              = azurerm_storage_container.respaldo.name
    ACS_ENDPOINT                     = "https://${azurerm_communication_service.main.hostname}"
    # ACS (SMS y correo) se autentica con la cadena de conexión; ver CAMBIOS_PROPUESTOS_E.md.
    ACS_CONNECTION_STRING = azurerm_communication_service.main.primary_connection_string
    ACS_EMAIL_REMITENTE   = "DoNotReply@${azurerm_email_communication_service_domain.azure.mail_from_sender_domain}"
    ACS_SMS_REMITENTE     = var.sms_numero_remitente
  }
}

# La identidad de la función lee y escribe el paquete de despliegue en el almacenamiento.
resource "azurerm_role_assignment" "funcion_blob" {
  scope                = azurerm_storage_account.funcion.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_function_app_flex_consumption.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "funcion_colas" {
  scope                = azurerm_storage_account.funcion.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_function_app_flex_consumption.main.identity[0].principal_id
}

resource "azurerm_role_assignment" "funcion_tablas" {
  scope                = azurerm_storage_account.funcion.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_function_app_flex_consumption.main.identity[0].principal_id
}

# Suscripción: Event Grid -> función `replicar` (CloudEvents, reintentos, dead letter en blob).
resource "azurerm_eventgrid_event_subscription" "replicar" {
  count = var.crear_suscripcion_eventgrid ? 1 : 0

  name                  = "${local.prefijo}-replicar"
  scope                 = azurerm_eventgrid_topic.eventos.id
  event_delivery_schema = "CloudEventSchemaV1_0"

  azure_function_endpoint {
    function_id                       = "${azurerm_function_app_flex_consumption.main.id}/functions/replicar"
    max_events_per_batch              = 1
    preferred_batch_size_in_kilobytes = 64
  }

  retry_policy {
    max_delivery_attempts = 10
    event_time_to_live    = 1440 # minutos
  }

  storage_blob_dead_letter_destination {
    storage_account_id          = azurerm_storage_account.funcion.id
    storage_blob_container_name = azurerm_storage_container.deadletter.name
  }

  depends_on = [azurerm_function_app_flex_consumption.main]
}
