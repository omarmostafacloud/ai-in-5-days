# Secret containers are created by Terraform; secret VALUES are added out-of-band
# (gcloud secrets versions add ...) so they never appear in code or Terraform state.

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "dragons-den-gemini-api-key"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "decision_webhook_token" {
  secret_id = "dragons-den-decision-webhook-token"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Generated DB password lives only in Secret Manager.
resource "random_password" "session_db" {
  count   = var.enable_cloud_sql ? 1 : 0
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "session_db_url" {
  count     = var.enable_cloud_sql ? 1 : 0
  secret_id = "dragons-den-session-db-url"
  labels    = local.labels
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "session_db_url" {
  count       = var.enable_cloud_sql ? 1 : 0
  secret      = google_secret_manager_secret.session_db_url[0].id
  secret_data = "postgresql+asyncpg://den:${random_password.session_db[0].result}@/den_sessions?host=/cloudsql/${google_sql_database_instance.sessions[0].connection_name}"
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  for_each = merge(
    {
      gemini  = google_secret_manager_secret.gemini_api_key.id
      webhook = google_secret_manager_secret.decision_webhook_token.id
    },
    var.enable_cloud_sql ? { db = google_secret_manager_secret.session_db_url[0].id } : {}
  )
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
