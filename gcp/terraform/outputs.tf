output "project_id" {
  value = var.project_id
}

output "project_number" {
  value = data.google_project.actual.number
}

output "tema_eventos" {
  description = "Nombre del tema donde publica el puente de AWS."
  value       = google_pubsub_topic.eventos.name
}

output "tema_eventos_id" {
  value = google_pubsub_topic.eventos.id
}

output "tema_dlq" {
  value = google_pubsub_topic.eventos_dlq.name
}

output "dataset_analitica" {
  value = google_bigquery_dataset.analitica.dataset_id
}

output "tabla_eventos_raw" {
  value = "${var.project_id}.${google_bigquery_dataset.analitica.dataset_id}.${google_bigquery_table.eventos_raw.table_id}"
}

output "bucket_respaldo" {
  value = google_storage_bucket.respaldo.name
}

output "vistas" {
  description = "Vistas para conectar Looker Studio."
  value = concat(
    [google_bigquery_table.v_eventos.table_id, google_bigquery_table.v_incidencias.table_id],
    [for v in google_bigquery_table.vistas_base : v.table_id],
    [for v in google_bigquery_table.vistas_derivadas : v.table_id],
  )
}
