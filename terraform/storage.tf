# Firestore: persistent archive of filed review decisions (precedent search).
resource "google_firestore_database" "decisions" {
  name                    = "(default)"
  location_id             = var.region == "us-central1" ? "nam5" : var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_ENABLED"
  depends_on              = [google_project_service.apis]
}

# Cloud SQL Postgres: ADK DatabaseSessionService (sessions, state, HITL resumption).
resource "google_sql_database_instance" "sessions" {
  count               = var.enable_cloud_sql ? 1 : 0
  name                = "${local.name}-sessions"
  database_version    = "POSTGRES_16"
  region              = var.region
  deletion_protection = true

  settings {
    tier              = var.cloud_sql_tier
    edition           = "ENTERPRISE"
    availability_type = "REGIONAL" # HA: the reliability dragon would insist
    user_labels       = local.labels

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
    }
    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }
    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "sessions" {
  count    = var.enable_cloud_sql ? 1 : 0
  name     = "den_sessions"
  instance = google_sql_database_instance.sessions[0].name
}

resource "google_sql_user" "den" {
  count    = var.enable_cloud_sql ? 1 : 0
  name     = "den"
  instance = google_sql_database_instance.sessions[0].name
  password = random_password.session_db[0].result
}
