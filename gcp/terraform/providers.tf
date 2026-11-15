# Proveedor principal. Las credenciales salen de las credenciales por defecto
# de la aplicación (gcloud auth application-default login) o de WIF en CI.
provider "google" {
  project = var.project_id
  region  = var.region
}
