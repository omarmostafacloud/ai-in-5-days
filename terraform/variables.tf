variable "project_id" {
  description = "Google Cloud project hosting the Dragons' Den."
  type        = string
}

variable "region" {
  description = "Primary region for Cloud Run, Cloud SQL, Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Container image for the agent (set by CI). Defaults to a placeholder until the first build."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "github_repository" {
  description = "GitHub repository allowed to deploy via Workload Identity Federation (owner/name)."
  type        = string
  default     = "omarmostafacloud/ai-in-5-days"
}

variable "fast_model" {
  description = "Gemini model for fast/specialist tasks."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "deep_model" {
  description = "Gemini model for the Den Chair synthesis."
  type        = string
  default     = "gemini-2.5-pro"
}

variable "enable_cloud_sql" {
  description = "Provision Cloud SQL Postgres for persistent ADK sessions."
  type        = bool
  default     = true
}

variable "cloud_sql_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-1-3840"
}

variable "allow_unauthenticated" {
  description = "Expose the service publicly (demo only). Production should front with IAP."
  type        = bool
  default     = false
}
