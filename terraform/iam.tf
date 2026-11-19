# ---------------------------------------------------------------------------
# Runtime identity: least-privilege service account for the Cloud Run service.
# ---------------------------------------------------------------------------
resource "google_service_account" "runtime" {
  account_id   = "${local.name}-runtime"
  display_name = "Dragons' Den agent runtime"
}

locals {
  runtime_roles = [
    "roles/aiplatform.user",   # Gemini on Vertex AI, Memory Bank
    "roles/datastore.user",    # Firestore decision store
    "roles/cloudsql.client",   # Session DB
    "roles/dlp.user",          # PII redaction
    "roles/modelarmor.user",   # Prompt/response screening
    "roles/cloudtrace.agent",  # OpenTelemetry export
    "roles/logging.logWriter", # Structured logs
    "roles/monitoring.metricWriter",
  ]
}

resource "google_project_iam_member" "runtime" {
  for_each = toset(local.runtime_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.runtime.email}"
}

# ---------------------------------------------------------------------------
# CI/CD identity: GitHub Actions deploys via Workload Identity Federation (no keys).
# ---------------------------------------------------------------------------
resource "google_service_account" "deployer" {
  account_id   = "${local.name}-deployer"
  display_name = "Dragons' Den GitHub Actions deployer"
}

locals {
  deployer_roles = [
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/secretmanager.viewer",
  ]
}

resource "google_project_iam_member" "deployer" {
  for_each = toset(local.deployer_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${local.name}-github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == \"${var.github_repository}\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}
