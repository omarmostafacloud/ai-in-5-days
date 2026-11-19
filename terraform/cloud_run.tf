resource "google_artifact_registry_repository" "agent" {
  location      = var.region
  repository_id = local.name
  format        = "DOCKER"
  labels        = local.labels
  depends_on    = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "agent" {
  name                = local.name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = local.labels

  template {
    service_account = google_service_account.runtime.email
    timeout         = "900s"

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    dynamic "volumes" {
      for_each = var.enable_cloud_sql ? [1] : []
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.sessions[0].connection_name]
        }
      }
    }

    containers {
      image = var.image

      resources {
        limits = { cpu = "2", memory = "2Gi" }
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "DEN_FAST_MODEL"
        value = var.fast_model
      }
      env {
        name  = "DEN_DEEP_MODEL"
        value = var.deep_model
      }
      env {
        name  = "DECISION_STORE_BACKEND"
        value = "firestore"
      }
      env {
        name  = "ENABLE_CLOUD_DLP"
        value = "true"
      }
      env {
        name  = "ENABLE_CLOUD_TRACE"
        value = "true"
      }
      env {
        name  = "ENABLE_MODEL_ARMOR"
        value = "true"
      }
      env {
        name  = "MODEL_ARMOR_TEMPLATE"
        value = "projects/${var.project_id}/locations/${var.region}/templates/dragons-den"
      }

      # Secrets injected at runtime from Secret Manager - never baked into the image.
      dynamic "env" {
        for_each = var.enable_cloud_sql ? [1] : []
        content {
          name = "SESSION_DB_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.session_db_url[0].secret_id
              version = "latest"
            }
          }
        }
      }
      env {
        name = "DECISION_WEBHOOK_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.decision_webhook_token.secret_id
            version = "latest"
          }
        }
      }

      dynamic "volume_mounts" {
        for_each = var.enable_cloud_sql ? [1] : []
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image] # image rolled by CI
  }

  depends_on = [
    google_project_iam_member.runtime,
    google_secret_manager_secret_iam_member.runtime_access,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.allow_unauthenticated ? 1 : 0
  name     = google_cloud_run_v2_service.agent.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
