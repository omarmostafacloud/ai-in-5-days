"""Secure secret management via Google Cloud Secret Manager.

Resolution order for a secret named ``NAME``:

1. Environment variable ``NAME`` - populated at deploy time by Cloud Run's native
   Secret Manager integration (``--set-secrets`` / Terraform ``value_source``),
   so the value never lives in code, images or Terraform state.
2. Secret Manager API ``projects/$GOOGLE_CLOUD_PROJECT/secrets/<secret-id>/versions/latest``
   using Application Default Credentials (workload identity, no key files).

No secret values are hard-coded anywhere in this repository; ``tests/test_secrets.py``
scans the tree to enforce this.
"""

from __future__ import annotations

import functools
import os

from dragons_den.config import SETTINGS
from dragons_den.observability.logging import log_event

# Logical name -> Secret Manager secret id (provisioned by terraform/secrets.tf).
SECRET_IDS: dict[str, str] = {
    "GOOGLE_API_KEY": "dragons-den-gemini-api-key",
    "DECISION_WEBHOOK_TOKEN": "dragons-den-decision-webhook-token",
}


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret cannot be resolved from env or Secret Manager."""


@functools.cache
def _client():  # pragma: no cover - network client
    from google.cloud import secretmanager

    return secretmanager.SecretManagerServiceClient()


@functools.cache
def get_secret(name: str, *, required: bool = True) -> str | None:
    """Resolve a secret value without ever logging it.

    Args:
        name: Logical secret name (key of ``SECRET_IDS``) or env var name.
        required: Raise ``SecretNotFoundError`` when missing if True.

    Returns:
        The secret value, or None when optional and absent.
    """
    if value := os.getenv(name):
        log_event("SECRET_RESOLVED", f"Secret {name} resolved from injected env", source="env", secret=name)
        return value

    secret_id = SECRET_IDS.get(name, name.lower().replace("_", "-"))
    if SETTINGS.gcp_project:
        try:
            path = f"projects/{SETTINGS.gcp_project}/secrets/{secret_id}/versions/latest"
            payload = _client().access_secret_version(request={"name": path}, timeout=5.0).payload.data
            log_event("SECRET_RESOLVED", f"Secret {name} resolved from Secret Manager", source="secret_manager", secret=name)
            return payload.decode("utf-8")
        except Exception as exc:
            log_event("SECRET_LOOKUP_FAILED", f"Secret Manager lookup failed for {name}", error=type(exc).__name__)

    if required:
        raise SecretNotFoundError(
            f"Secret '{name}' not found. Inject it via Cloud Run --set-secrets or create "
            f"Secret Manager secret '{secret_id}' in project '{SETTINGS.gcp_project}'."
        )
    return None


def bootstrap_model_credentials() -> None:
    """Make Gemini credentials available to google-genai without hard-coding them.

    On Vertex AI (``GOOGLE_GENAI_USE_VERTEXAI=true``) ADC is used and no key is needed.
    Otherwise the Gemini API key is pulled from Secret Manager into the process env.
    """
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in {"1", "true"}:
        return
    key = get_secret("GOOGLE_API_KEY", required=False)
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)
