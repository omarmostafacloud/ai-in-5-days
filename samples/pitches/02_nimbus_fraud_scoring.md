# Nimbus Bank - real-time fraud scoring (expected: I'm in / I'm in with conditions)

Nimbus Bank needs real-time card fraud scoring. Global Cloud Load Balancing with Cloud Armor in
front of GKE Autopilot (64 vCPU baseline) using Workload Identity; feature store and scores in
Spanner multi-region (9 nodes), deployed active-active across europe-west1 and europe-west4.
Secrets in Secret Manager, CMEK with Cloud KMS, VPC Service Controls perimeter around Spanner
and BigQuery. Confidential data under GDPR and PSD2. Target SLO 99.99%. Budget $40,000/month.
Terraform + Cloud Deploy canary rollouts, OpenTelemetry to Cloud Trace.
