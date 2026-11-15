# Reentrenamiento semanal del modelo ARIMA_PLUS mediante consulta programada.
# El archivo modelo_pronostico.sql tiene dos sentencias separadas por la marca
# "-- @@ PRONOSTICO"; aquí solo se programa la primera (CREATE OR REPLACE MODEL).
# Si aún no hay datos suficientes la ejecución falla sin costo y se reintenta la semana siguiente.
locals {
  sql_modelo      = templatefile("${path.module}/../sql/modelo_pronostico.sql", local.vars_sql)
  sql_entrenar    = trimspace(split("-- @@ PRONOSTICO", local.sql_modelo)[0])
  agente_transfer = "serviceAccount:service-${data.google_project.actual.number}@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com"
}

resource "google_service_account" "reentrenar" {
  count        = var.habilitar_reentrenamiento ? 1 : 0
  account_id   = "${local.prefijo}-reentrenar"
  display_name = "Reentrenamiento semanal ARIMA_PLUS"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "reentrenar_job_user" {
  count   = var.habilitar_reentrenamiento ? 1 : 0
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.reentrenar[0].email}"
}

resource "google_bigquery_dataset_iam_member" "reentrenar_editor" {
  count      = var.habilitar_reentrenamiento ? 1 : 0
  project    = var.project_id
  dataset_id = google_bigquery_dataset.analitica.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.reentrenar[0].email}"
}

# El servicio de transferencias debe poder generar tokens de la cuenta de servicio.
resource "google_service_account_iam_member" "transfer_token_creator" {
  count              = var.habilitar_reentrenamiento ? 1 : 0
  service_account_id = google_service_account.reentrenar[0].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.agente_transfer
}

resource "google_bigquery_data_transfer_config" "reentrenar_pronostico" {
  count = var.habilitar_reentrenamiento ? 1 : 0

  project                = var.project_id
  display_name           = "${local.prefijo}-reentrenar-pronostico"
  location               = var.ubicacion_dataset
  data_source_id         = "scheduled_query"
  schedule               = var.horario_reentrenamiento
  destination_dataset_id = google_bigquery_dataset.analitica.dataset_id
  service_account_name   = google_service_account.reentrenar[0].email

  params = {
    query = local.sql_entrenar
  }

  depends_on = [
    google_project_iam_member.reentrenar_job_user,
    google_bigquery_dataset_iam_member.reentrenar_editor,
    google_service_account_iam_member.transfer_token_creator,
    google_bigquery_table.vistas_base,
  ]
}
