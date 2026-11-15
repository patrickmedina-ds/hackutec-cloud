resource "azurerm_communication_service" "main" {
  name                = "${local.prefijo}-acs-${random_string.sufijo.result}"
  resource_group_name = azurerm_resource_group.main.name
  data_location       = "United States"
  tags                = local.etiquetas
}

resource "azurerm_email_communication_service" "main" {
  name                = "${local.prefijo}-correo-${random_string.sufijo.result}"
  resource_group_name = azurerm_resource_group.main.name
  data_location       = "United States"
  tags                = local.etiquetas
}

# Dominio administrado por Azure (<guid>.azurecomm.net): no requiere DNS propio.
resource "azurerm_email_communication_service_domain" "azure" {
  name              = "AzureManagedDomain"
  email_service_id  = azurerm_email_communication_service.main.id
  domain_management = "AzureManaged"
  tags              = local.etiquetas
}

resource "azurerm_communication_service_email_domain_association" "main" {
  communication_service_id = azurerm_communication_service.main.id
  email_service_domain_id  = azurerm_email_communication_service_domain.azure.id
}

# SMS: Terraform no puede comprar el número. Se compra en el portal (ACS > Phone numbers)
# y se indica en var.sms_numero_remitente.
