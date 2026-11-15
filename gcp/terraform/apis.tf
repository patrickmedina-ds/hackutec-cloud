# APIs necesarias. disable_on_destroy = false evita apagar APIs compartidas al destruir.
locals {
  apis = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "pubsub.googleapis.com",
    "bigquery.googleapis.com",
    "bigquerydatatransfer.googleapis.com", # consultas programadas
    "storage.googleapis.com",
    "aiplatform.googleapis.com", # Vertex AI (Gemini)
    "iam.googleapis.com",
    "sts.googleapis.com",            # Workload Identity Federation
    "iamcredentials.googleapis.com", # suplantación de cuentas de servicio
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "actual" {
  project_id = var.project_id

  depends_on = [google_project_service.apis]
}

locals {
  # Agente de servicio de Pub/Sub: escribe en BigQuery/GCS y gestiona el dead letter.
  agente_pubsub = "serviceAccount:service-${data.google_project.actual.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  prefijo       = "telecorp-${var.stage}"
}
