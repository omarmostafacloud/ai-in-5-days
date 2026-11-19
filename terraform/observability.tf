# Agent observability: structured JSON logs -> BigQuery for AgentOps analytics,
# log-based metrics on guardrail events and an alert on HITL rejections spikes.

resource "google_bigquery_dataset" "agent_logs" {
  dataset_id                  = "dragons_den_logs"
  location                    = "US"
  labels                      = local.labels
  delete_contents_on_destroy  = false
  default_table_expiration_ms = 1000 * 60 * 60 * 24 * 90
  depends_on                  = [google_project_service.apis]
}

resource "google_logging_project_sink" "agent_logs" {
  name                   = "${local.name}-to-bigquery"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.agent_logs.dataset_id}"
  filter                 = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${local.name}\" AND jsonPayload.event_type:*"
  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "sink_writer" {
  dataset_id = google_bigquery_dataset.agent_logs.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.agent_logs.writer_identity
}

resource "google_logging_metric" "guardrail_blocks" {
  name   = "dragons_den/guardrail_blocks"
  filter = "resource.labels.service_name=\"${local.name}\" AND jsonPayload.phase=\"GUARDRAIL\" AND jsonPayload.event_type=~\"BLOCK\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_logging_metric" "tool_errors" {
  name   = "dragons_den/tool_errors"
  filter = "resource.labels.service_name=\"${local.name}\" AND jsonPayload.event_type=\"TOOL_OUTCOME\" AND jsonPayload.status=\"error\""
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

resource "google_monitoring_alert_policy" "guardrail_spike" {
  display_name = "Dragons' Den - prompt injection spike"
  combiner     = "OR"
  conditions {
    display_name = "guardrail blocks > 20 / 10 min"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.guardrail_blocks.name}\" AND resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 20
      duration        = "0s"
      aggregations {
        alignment_period   = "600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
}
