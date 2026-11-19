terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.10, < 8.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }
  # Remote state: pass -backend-config="bucket=<tfstate-bucket>" at init time (see CI).
  backend "gcs" {
    prefix = "dragons-den/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name   = "dragons-den"
  labels = { app = "dragons-den", managed-by = "terraform", team = "pso" }
}
