# Helix Health - clinical note summarisation with Gemini (expected: I'm in with conditions)

Helix Health wants clinicians' free-text notes summarised by the Gemini API on Vertex AI, grounded
on their care-pathway documents with Vertex AI Search. A Cloud Run frontend (8 vCPU) serves the
clinician UI; PHI notes are stored in Cloud Storage (2 TB). Restricted data in us-central1 under
HIPAA. Generated summaries are written back automatically to the EHR. ~40M output tokens/month.
Target SLO 99.5%. Budget $6,000/month.
