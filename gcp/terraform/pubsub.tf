# Tema principal: aquí publica el puente de AWS (permisos en federacion.tf, bloque B).
resource "google_pubsub_topic" "eventos" {
  name    = "${local.prefijo}-eventos"
  project = var.project_id

  depends_on = [google_project_service.apis]
}

# Tema de mensajes muertos (no se pudieron escribir en BigQuery/GCS).
resource "google_pubsub_topic" "eventos_dlq" {
  name    = "${local.prefijo}-eventos-dlq"
  project = var.project_id

  depends_on = [google_project_service.apis]
}

# Suscripción pull para conservar los mensajes muertos (sin ella se pierden). Costo ~0.
resource "google_pubsub_subscription" "eventos_dlq_pull" {
  name                       = "${local.prefijo}-eventos-dlq-pull"
  project                    = var.project_id
  topic                      = google_pubsub_topic.eventos_dlq.id
  message_retention_duration = "604800s" # 7 días
  ack_deadline_seconds       = 20

  expiration_policy {
    ttl = "" # nunca expira
  }
}

# --- Permisos del agente de servicio de Pub/Sub ---

# Dead letter: publicar en el tema DLQ...
resource "google_pubsub_topic_iam_member" "dlq_publicador" {
  project = var.project_id
  topic   = google_pubsub_topic.eventos_dlq.name
  role    = "roles/pubsub.publisher"
  member  = local.agente_pubsub
}

# ...y confirmar (ack) en las suscripciones de origen.
resource "google_pubsub_subscription_iam_member" "bq_dlq_suscriptor" {
  project      = var.project_id
  subscription = google_pubsub_subscription.eventos_bigquery.name
  role         = "roles/pubsub.subscriber"
  member       = local.agente_pubsub
}

resource "google_pubsub_subscription_iam_member" "gcs_dlq_suscriptor" {
  project      = var.project_id
  subscription = google_pubsub_subscription.eventos_gcs.name
  role         = "roles/pubsub.subscriber"
  member       = local.agente_pubsub
}

# BigQuery: editar datos de la tabla y ver metadatos del dataset.
resource "google_bigquery_table_iam_member" "pubsub_editor" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.analitica.dataset_id
  table_id   = google_bigquery_table.eventos_raw.table_id
  role       = "roles/bigquery.dataEditor"
  member     = local.agente_pubsub
}

resource "google_bigquery_dataset_iam_member" "pubsub_metadata" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.analitica.dataset_id
  role       = "roles/bigquery.metadataViewer"
  member     = local.agente_pubsub
}

# Cloud Storage: crear objetos y leer metadatos del bucket.
resource "google_storage_bucket_iam_member" "pubsub_crea_objetos" {
  bucket = google_storage_bucket.respaldo.name
  role   = "roles/storage.objectCreator"
  member = local.agente_pubsub
}

resource "google_storage_bucket_iam_member" "pubsub_lee_bucket" {
  bucket = google_storage_bucket.respaldo.name
  role   = "roles/storage.legacyBucketReader"
  member = local.agente_pubsub
}

# --- Suscripciones ---

# Suscripción BigQuery: escribe cada mensaje en eventos_raw con metadatos.
resource "google_pubsub_subscription" "eventos_bigquery" {
  name    = "${local.prefijo}-eventos-bigquery"
  project = var.project_id
  topic   = google_pubsub_topic.eventos.id

  bigquery_config {
    table = "${var.project_id}.${google_bigquery_dataset.analitica.dataset_id}.${google_bigquery_table.eventos_raw.table_id}"
    # Llena subscription_name, message_id, publish_time y attributes.
    write_metadata = true
    # Sin use_topic_schema ni use_table_schema: el cuerpo va a la columna data (JSON).
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.eventos_dlq.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  expiration_policy {
    ttl = ""
  }

  depends_on = [
    google_bigquery_table_iam_member.pubsub_editor,
    google_bigquery_dataset_iam_member.pubsub_metadata,
  ]
}

# Suscripción Cloud Storage: respaldo crudo en JSON lineal, un archivo cada 5 min o 50 MB.
resource "google_pubsub_subscription" "eventos_gcs" {
  name    = "${local.prefijo}-eventos-gcs"
  project = var.project_id
  topic   = google_pubsub_topic.eventos.id

  cloud_storage_config {
    bucket          = google_storage_bucket.respaldo.name
    filename_prefix = "eventos/"
    filename_suffix = ".jsonl"
    max_duration    = "300s"
    max_bytes       = 52428800

    # Salida de texto: el CloudEvent JSON tal cual, un mensaje por línea.
    text_config {}
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.eventos_dlq.id
    max_delivery_attempts = 5
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  expiration_policy {
    ttl = ""
  }

  depends_on = [
    google_storage_bucket_iam_member.pubsub_crea_objetos,
    google_storage_bucket_iam_member.pubsub_lee_bucket,
  ]
}
