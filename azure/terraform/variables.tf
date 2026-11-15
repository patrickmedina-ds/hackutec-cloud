variable "stage" {
  description = "Etapa del despliegue; forma el prefijo telecorp-<stage>-."
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Región de Azure (contratos/README.md: eastus2)."
  type        = string
  default     = "eastus2"
}

variable "subscription_id" {
  description = "ID de la suscripción. Si es null se usa ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "crear_suscripcion_eventgrid" {
  description = <<-EOT
    Crea la suscripción de Event Grid hacia la función `replicar`. Azure exige que la
    función ya esté desplegada, así que: 1) terraform apply con false, 2) publicar el
    código (func azure functionapp publish), 3) terraform apply con true.
  EOT
  type        = bool
  default     = false
}

variable "sms_numero_remitente" {
  description = <<-EOT
    Número de Azure Communication Services (formato +1...) comprado a mano en el portal
    (ACS > Phone numbers). Terraform no lo compra. Si es vacío, los avisos por SMS fallan
    (y quedan en dead letter); el correo sigue funcionando.
  EOT
  type        = string
  default     = ""
}

variable "cors_origenes_extra" {
  description = "Orígenes adicionales permitidos para api_estado (p. ej. un dominio propio)."
  type        = list(string)
  default     = []
}

variable "etiquetas" {
  description = "Etiquetas aplicadas a todos los recursos."
  type        = map(string)
  default = {
    proyecto = "red-telecorp"
  }
}
