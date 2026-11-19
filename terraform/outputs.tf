output "service_url" {
  description = "Dragons' Den Cloud Run URL."
  value       = google_cloud_run_v2_service.agent.uri
}

output "artifact_registry" {
  description = "Docker repository for agent images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.agent.repository_id}"
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "deployer_service_account" {
  description = "Set as GitHub secret GCP_DEPLOYER_SA."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "Set as GitHub secret GCP_WIF_PROVIDER."
  value       = google_iam_workload_identity_pool_provider.github.name
}
