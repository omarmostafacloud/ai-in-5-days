"""Runtime configuration and Strategic Model Routing.

The Dragons' Den deliberately routes work to different Gemini tiers:

* ``FAST`` (Gemini Flash)  - extraction, tool-heavy specialist reviews, critic checks,
  clerk/back-office work, compaction summaries. Cheap and low latency.
* ``DEEP`` (Gemini Pro)    - the Den Chair's synthesis of conflicting expert opinions
  into a single scored verdict (multi-step reasoning / planning).
* ``ESCALATE``              - automatic upgrade of a specialist from Flash to Pro when the
  pitch is high complexity or handles regulated/restricted data (see
  ``complexity_aware_model_router`` callback in ``guardrails``).

All model ids are environment-configurable so ops can roll forward to new
model versions without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from google.genai import types


class TaskTier(str, Enum):
    """Workload tiers used by the model router."""

    FAST = "fast"
    DEEP = "deep"


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings resolved from the environment (12-factor)."""

    app_name: str = "dragons_den"
    fast_model: str = field(default_factory=lambda: os.getenv("DEN_FAST_MODEL", "gemini-2.5-flash"))
    deep_model: str = field(default_factory=lambda: os.getenv("DEN_DEEP_MODEL", "gemini-2.5-pro"))
    gcp_project: str | None = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT"))
    gcp_location: str = field(default_factory=lambda: os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    agent_engine_id: str | None = field(default_factory=lambda: os.getenv("AGENT_ENGINE_ID"))
    session_db_url: str = field(default_factory=lambda: os.getenv("SESSION_DB_URL", "sqlite+aiosqlite:///./.den_sessions.db"))
    decision_store_backend: str = field(default_factory=lambda: os.getenv("DECISION_STORE_BACKEND", "sqlite"))
    decision_store_path: str = field(default_factory=lambda: os.getenv("DECISION_STORE_PATH", "./.den_decisions.db"))
    enable_cloud_dlp: bool = field(default_factory=lambda: os.getenv("ENABLE_CLOUD_DLP", "false").lower() == "true")
    enable_cloud_trace: bool = field(default_factory=lambda: os.getenv("ENABLE_CLOUD_TRACE", "false").lower() == "true")
    enable_model_armor: bool = field(default_factory=lambda: os.getenv("ENABLE_MODEL_ARMOR", "false").lower() == "true")
    model_armor_template: str | None = field(default_factory=lambda: os.getenv("MODEL_ARMOR_TEMPLATE"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    max_review_loops: int = field(default_factory=lambda: int(os.getenv("DEN_MAX_REVIEW_LOOPS", "3")))


SETTINGS = Settings()

# Retry policy shared by all model calls (transient 429/5xx resilience).
HTTP_RETRY = types.HttpRetryOptions(attempts=4, initial_delay=1.0, exp_base=2.0, max_delay=20.0)

# Routing table: which agent runs on which tier, and why. Exposed for docs/tests.
AGENT_ROUTING_TABLE: dict[str, TaskTier] = {
    "den_host": TaskTier.FAST,  # conversational routing / greeting
    "pitch_intake_analyst": TaskTier.FAST,  # structured extraction
    "security_dragon": TaskTier.FAST,  # tool-grounded specialist (auto-escalates)
    "reliability_dragon": TaskTier.FAST,
    "finops_dragon": TaskTier.FAST,
    "data_ai_dragon": TaskTier.FAST,
    "den_chair": TaskTier.DEEP,  # multi-perspective synthesis + planning
    "verdict_critic": TaskTier.FAST,  # rubric-based self-evaluation
    "review_clerk": TaskTier.FAST,  # back-office tool calls
    "review_historian": TaskTier.FAST,  # memory retrieval Q&A
}


def model_for(tier: TaskTier) -> str:
    """Return the model id for a task tier."""
    return SETTINGS.deep_model if tier is TaskTier.DEEP else SETTINGS.fast_model


def model_for_agent(agent_name: str) -> str:
    """Return the routed model id for a named agent (defaults to FAST)."""
    return model_for(AGENT_ROUTING_TABLE.get(agent_name, TaskTier.FAST))


def should_escalate(complexity: str | None, data_classification: str | None) -> bool:
    """Decide whether a specialist should be escalated from Flash to Pro.

    Args:
        complexity: Pitch complexity from intake ("low" | "medium" | "high").
        data_classification: Data classification from intake.

    Returns:
        True when the pitch is high complexity or handles restricted data.
    """
    return (complexity or "").lower() == "high" or (data_classification or "").lower() == "restricted"


def generation_config(tier: TaskTier) -> types.GenerateContentConfig:
    """Per-tier generation settings: deterministic specialists, slightly creative chair."""
    return types.GenerateContentConfig(
        temperature=0.4 if tier is TaskTier.DEEP else 0.1,
        http_options=types.HttpOptions(retry_options=HTTP_RETRY),
    )
