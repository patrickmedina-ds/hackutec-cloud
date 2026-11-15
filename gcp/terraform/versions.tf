terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Backend remoto opcional (descomentar tras crear a mano el bucket de estado).
  # backend "gcs" {
  #   bucket = "mi-bucket-de-estado-terraform"
  #   prefix = "telecorp/gcp"
  # }
}
