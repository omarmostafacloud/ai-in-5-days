"""Persistent session state and long-term memory services.

* **Sessions** - ``DatabaseSessionService`` (SQLAlchemy: Cloud SQL Postgres in prod,
  SQLite locally) or ``VertexAiSessionService`` on Vertex AI Agent Engine. Every turn,
  state delta (``design_pitch``, dragon verdicts, ``chair_verdict``...) and pending
  HITL confirmation survives restarts and scales across Cloud Run instances.
* **Memory** - ``VertexAiMemoryBankService`` (Agent Engine Memory Bank) for
  cross-session recall of previous pitches, falling back to ``InMemoryMemoryService``.

The same URIs are passed to ``adk web`` / ``adk api_server`` / ``get_fast_api_app``
via ``--session_service_uri`` and ``--memory_service_uri`` (see Makefile).
"""

from __future__ import annotations

import functools
from typing import Any

from dragons_den.config import SETTINGS


def session_service_uri() -> str:
    """URI understood by the ADK CLI/FastAPI server for persistent sessions."""
    if SETTINGS.agent_engine_id and SETTINGS.gcp_project:
        return f"agentengine://{SETTINGS.agent_engine_id}"
    return SETTINGS.session_db_url


def memory_service_uri() -> str | None:
    """URI for Vertex AI Memory Bank when running against Agent Engine."""
    if SETTINGS.agent_engine_id and SETTINGS.gcp_project:
        return f"agentengine://{SETTINGS.agent_engine_id}"
    return None


@functools.cache
def create_session_service() -> Any:
    """Instantiate the persistent session service for programmatic runners."""
    if SETTINGS.agent_engine_id and SETTINGS.gcp_project:
        from google.adk.sessions import VertexAiSessionService

        return VertexAiSessionService(
            project=SETTINGS.gcp_project,
            location=SETTINGS.gcp_location,
            agent_engine_id=SETTINGS.agent_engine_id,
        )
    from google.adk.sessions import DatabaseSessionService

    return DatabaseSessionService(db_url=SETTINGS.session_db_url)


@functools.cache
def create_memory_service() -> Any:
    """Instantiate the long-term memory service (Memory Bank or in-memory fallback)."""
    if SETTINGS.agent_engine_id and SETTINGS.gcp_project:
        from google.adk.memory import VertexAiMemoryBankService

        return VertexAiMemoryBankService(
            project=SETTINGS.gcp_project,
            location=SETTINGS.gcp_location,
            agent_engine_id=SETTINGS.agent_engine_id,
        )
    from google.adk.memory import InMemoryMemoryService

    return InMemoryMemoryService()
