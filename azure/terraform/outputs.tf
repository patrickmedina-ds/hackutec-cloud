output "eventgrid_topic_endpoint" {
  description = "Endpoint del tema (SSM /telecorp/<stage>/azure/eventgrid-endpoint)."
  value       = azurerm_eventgrid_topic.eventos.endpoint
}

output "eventgrid_topic_id" {
  value = azurerm_eventgrid_topic.eventos.id
}

output "cosmos_endpoint" {
  value = azurerm_cosmosdb_account.main.endpoint
}

output "funcion_nombre" {
  description = "Para: func azure functionapp publish <nombre>."
  value       = azurerm_function_app_flex_consumption.main.name
}

output "api_estado_url" {
  description = "URL base de api_estado; se escribe en status-page/config.js."
  value       = "https://${azurerm_function_app_flex_consumption.main.default_hostname}/api"
}

output "pagina_estado_url" {
  value = "https://${azurerm_static_web_app.estado.default_host_name}"
}

output "pagina_estado_token" {
  description = "Token de despliegue de la Static Web App."
  value       = azurerm_static_web_app.estado.api_key
  sensitive   = true
}

output "acs_email_remitente" {
  value = "DoNotReply@${azurerm_email_communication_service_domain.azure.mail_from_sender_domain}"
}

output "openai_endpoint" {
  description = "SSM /telecorp/<stage>/azure/openai-endpoint"
  value       = azurerm_cognitive_account.openai.endpoint
}

output "openai_deployment" {
  description = "SSM /telecorp/<stage>/azure/openai-deployment"
  value       = azurerm_cognitive_deployment.chat.name
}

output "grupo_recursos" {
  value = azurerm_resource_group.main.name
}
