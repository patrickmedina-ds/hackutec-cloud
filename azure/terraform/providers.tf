provider "azurerm" {
  features {}
  # Si es null se usa la variable de entorno ARM_SUBSCRIPTION_ID.
  subscription_id = var.subscription_id
}

provider "azuread" {}
