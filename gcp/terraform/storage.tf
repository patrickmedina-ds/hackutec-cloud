# Bucket de respaldo dual-region NAM4, con versionado y paso a Coldline a los 30 días.
resource "google_storage_bucket" "respaldo" {
  name                        = "${var.project_id}-${local.prefijo}-respaldo"
  project                     = var.project_id
  location                    = var.ubicacion_bucket
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.forzar_destruccion

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  # Las versiones no vigentes se borran a los 90 días para no acumular costo.
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 90
      with_state                 = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}
