resource "google_bigquery_dataset" "analitica" {
  dataset_id                 = "telecorp_analitica"
  project                    = var.project_id
  location                   = var.ubicacion_dataset
  friendly_name              = "Analítica Red TeleCorp"
  description                = "Eventos replicados desde AWS y vistas para el tablero del NOC."
  delete_contents_on_destroy = var.forzar_destruccion

  depends_on = [google_project_service.apis]
}

# Esquema exigido por una suscripción BigQuery con write_metadata = true.
resource "google_bigquery_table" "eventos_raw" {
  dataset_id          = google_bigquery_dataset.analitica.dataset_id
  table_id            = "eventos_raw"
  project             = var.project_id
  description         = "Mensajes de Pub/Sub; data contiene el CloudEvent completo."
  deletion_protection = !var.forzar_destruccion

  time_partitioning {
    type          = "DAY"
    field         = "publish_time"
    expiration_ms = var.expiracion_particion_dias > 0 ? var.expiracion_particion_dias * 86400000 : null
  }

  # El clustering no admite columnas JSON; se agrupa por las columnas de texto.
  clustering = ["subscription_name", "message_id"]

  schema = jsonencode([
    { name = "subscription_name", type = "STRING", mode = "NULLABLE", description = "Suscripción de origen" },
    { name = "message_id", type = "STRING", mode = "NULLABLE", description = "ID del mensaje en Pub/Sub" },
    { name = "publish_time", type = "TIMESTAMP", mode = "REQUIRED", description = "Momento de publicación" },
    { name = "data", type = "JSON", mode = "NULLABLE", description = "CloudEvent completo" },
    { name = "attributes", type = "JSON", mode = "NULLABLE", description = "Atributos: ce_type, ce_id, zonaId" },
  ])
}

# --- Vistas ---
# Los SQL viven en gcp/sql/*.sql y usan ${proyecto} y ${dataset}.
# Se crean por capas porque unas vistas leen de otras.
locals {
  vars_sql = {
    proyecto = var.project_id
    dataset  = google_bigquery_dataset.analitica.dataset_id
  }
}

# Capa 0: eventos deduplicados con el sobre CloudEvent extraído.
resource "google_bigquery_table" "v_eventos" {
  dataset_id          = google_bigquery_dataset.analitica.dataset_id
  table_id            = "v_eventos"
  project             = var.project_id
  deletion_protection = false

  view {
    query          = templatefile("${path.module}/../sql/v_eventos.sql", local.vars_sql)
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.eventos_raw]
}

# Capa 1: vistas que leen v_eventos.
resource "google_bigquery_table" "vistas_base" {
  for_each = toset(["v_transiciones", "v_antenas_eventos", "v_colectivos", "v_tipos_problema"])

  dataset_id          = google_bigquery_dataset.analitica.dataset_id
  table_id            = each.value
  project             = var.project_id
  deletion_protection = false

  view {
    query          = templatefile("${path.module}/../sql/${each.value}.sql", local.vars_sql)
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.v_eventos]
}

# Capa 2: v_incidencias lee v_transiciones.
resource "google_bigquery_table" "v_incidencias" {
  dataset_id          = google_bigquery_dataset.analitica.dataset_id
  table_id            = "v_incidencias"
  project             = var.project_id
  deletion_protection = false

  view {
    query          = templatefile("${path.module}/../sql/v_incidencias.sql", local.vars_sql)
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.vistas_base]
}

# Capa 3: agregados sobre las capas anteriores.
resource "google_bigquery_table" "vistas_derivadas" {
  for_each = toset(["v_top_zonas", "v_top_antenas", "v_mttr", "v_recurrencia"])

  dataset_id          = google_bigquery_dataset.analitica.dataset_id
  table_id            = each.value
  project             = var.project_id
  deletion_protection = false

  view {
    query          = templatefile("${path.module}/../sql/${each.value}.sql", local.vars_sql)
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.v_incidencias]
}
