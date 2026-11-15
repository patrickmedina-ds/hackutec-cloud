variable "project_id" {
  description = "ID del proyecto de Google Cloud."
  type        = string
}

variable "region" {
  description = "Región de Google Cloud (contrato: us-east1)."
  type        = string
  default     = "us-east1"
}

variable "stage" {
  description = "Etapa del despliegue; forma parte de los nombres (telecorp-<stage>-...)."
  type        = string
  default     = "dev"
}

variable "ubicacion_dataset" {
  description = "Ubicación del dataset de BigQuery (multirregión US)."
  type        = string
  default     = "US"
}

variable "ubicacion_bucket" {
  description = "Ubicación del bucket de respaldo (dual-region NAM4 = us-central1 + us-east1)."
  type        = string
  default     = "NAM4"
}

variable "habilitar_reentrenamiento" {
  description = "Crea la consulta programada semanal que reentrena el modelo ARIMA_PLUS."
  type        = bool
  default     = true
}

variable "horario_reentrenamiento" {
  description = "Horario de la consulta programada (sintaxis de BigQuery Data Transfer, UTC)."
  type        = string
  default     = "every monday 06:00"
}

variable "expiracion_particion_dias" {
  description = "Días de retención de las particiones de eventos_raw (0 = sin expiración)."
  type        = number
  default     = 0
}

variable "forzar_destruccion" {
  description = "Permite destruir dataset, tablas y bucket con datos (útil en hackathon)."
  type        = bool
  default     = true
}
