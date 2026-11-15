# =============================================================================
# Respaldo de eventos en otra nube: la función `replicar` guarda cada CloudEvent
# crudo en Blob Storage (eventos-respaldo/aaaa/mm/dd/<tipo>/<ce_id>.json).
# Cuenta aparte, con replicación GRS, versionado y paso a Cool a los 30 días.
# Sin llaves: la identidad de la Function accede con Entra ID (DefaultAzureCredential).
# =============================================================================

variable "respaldo_location" {
  description = "Región de la cuenta de respaldo. westus2 admite GRS (par: westcentralus)."
  type        = string
  default     = "westus2"
}

variable "respaldo_dias_a_cool" {
  description = "Días tras los cuales los blobs de respaldo pasan al nivel Cool."
  type        = number
  default     = 30
}

resource "azurerm_storage_account" "respaldo" {
  name                            = substr("${local.prefijo_corto}rs${random_string.sufijo.result}", 0, 24)
  location                        = var.respaldo_location
  resource_group_name             = azurerm_resource_group.main.name
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "GRS"
  access_tier                     = "Hot"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  # Terraform administra el contenedor por el plano de datos con llave compartida; la Function
  # escribe con su identidad administrada (Storage Blob Data Contributor), sin llaves.
  shared_access_key_enabled = true
  tags                      = local.etiquetas

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }
    container_delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_container" "respaldo" {
  name                  = "eventos-respaldo"
  storage_account_id    = azurerm_storage_account.respaldo.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "respaldo" {
  storage_account_id = azurerm_storage_account.respaldo.id

  rule {
    name    = "a-cool-tras-${var.respaldo_dias_a_cool}-dias"
    enabled = true

    filters {
      blob_types   = ["blockBlob"]
      prefix_match = ["${azurerm_storage_container.respaldo.name}/"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than = var.respaldo_dias_a_cool
      }
      version {
        change_tier_to_cool_after_days_since_creation = var.respaldo_dias_a_cool
      }
    }
  }
}

# La identidad de la Function escribe (y lee, para la idempotencia) solo en este contenedor.
resource "azurerm_role_assignment" "funcion_respaldo" {
  scope                = azurerm_storage_container.respaldo.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_function_app_flex_consumption.main.identity[0].principal_id
}

output "respaldo_cuenta" {
  description = "Cuenta de almacenamiento del respaldo de eventos."
  value       = azurerm_storage_account.respaldo.name
}

output "respaldo_blob_endpoint" {
  description = "Endpoint de blobs del respaldo (RESPALDO_BLOB_ENDPOINT de la Function)."
  value       = azurerm_storage_account.respaldo.primary_blob_endpoint
}
