# Página de estado (plan Free). El contenido se publica con la CLI de SWA:
#   swa deploy azure/status-page --deployment-token <pagina_estado_token> --env production
resource "azurerm_static_web_app" "estado" {
  name                = "${local.prefijo}-estado-${random_string.sufijo.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku_tier            = "Free"
  sku_size            = "Free"
  tags                = local.etiquetas
}

# Portal de incidencias (React). Se aloja aquí porque el AWS Learner Lab bloquea CloudFront.
#   swa deploy portal/dist --deployment-token <portal_token> --env production
resource "azurerm_static_web_app" "portal" {
  name                = "${local.prefijo}-portal-${random_string.sufijo.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku_tier            = "Free"
  sku_size            = "Free"
  tags                = local.etiquetas
}
