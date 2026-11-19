"""Production HTTP entrypoint (Cloud Run): ADK FastAPI server + Dragons' Den agent.

* Persistent sessions via ``session_service_uri`` (Cloud SQL Postgres / Agent Engine).
* Long-term memory via ``memory_service_uri`` (Vertex AI Memory Bank) when configured.
* OpenTelemetry traces exported to Cloud Trace (``trace_to_cloud``).
* ``/healthz`` for Cloud Run probes.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from google.adk.cli.fast_api import get_fast_api_app

from dragons_den.config import SETTINGS
from dragons_den.memory.sessions import memory_service_uri, session_service_uri
from dragons_den.observability.logging import log_event
from dragons_den.secrets import bootstrap_model_credentials

AGENTS_DIR = str(Path(__file__).resolve().parent)

bootstrap_model_credentials()

app = get_fast_api_app(
    agents_dir=AGENTS_DIR,
    session_service_uri=session_service_uri(),
    memory_service_uri=memory_service_uri(),
    allow_origins=[o for o in os.getenv("ALLOW_ORIGINS", "").split(",") if o],
    web=os.getenv("SERVE_WEB_UI", "true").lower() == "true",
    trace_to_cloud=SETTINGS.enable_cloud_trace,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8080")),
)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness/readiness probe."""
    return {"status": "ok", "agent": SETTINGS.app_name}


if __name__ == "__main__":
    log_event("SERVER_START", "Starting Dragons' Den API server", port=os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
