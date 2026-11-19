"""History compaction and context caching (context-bloat management).

Three complementary mechanisms keep the context window lean during long review
sessions where a consultant iterates on a design many times:

1. **ADK Events Compaction** (``EventsCompactionConfig`` + ``LlmEventSummarizer``
   on Gemini Flash): sliding-window summarisation every ``compaction_interval``
   invocations with ``overlap_size`` overlap, plus token-threshold triggered
   compaction that retains only the most recent ``event_retention_size`` raw events.
2. **Vertex AI / Gemini Context Caching** (``ContextCacheConfig``): the large static
   constitution + Well-Architected knowledge prefix is cached server-side.
3. **Scoped history** - specialist dragons run with ``include_contents="none"`` and
   read only the structured ``design_pitch`` from state, so the parallel panel never
   re-reads the whole chat transcript. ``trim_text_for_state`` caps any free text
   stored in state.
"""

from __future__ import annotations

from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini

from dragons_den.config import HTTP_RETRY, SETTINGS

COMPACTION_PROMPT = """You are compacting the history of an architecture review session in the
Dragons' Den. Summarise the following events into <= 200 words. Preserve: customer alias,
design title, key components, data classification/regions, each dragon's stance and score,
the chair's decision and Den Score, open remediation items and any human approval/rejection.
Drop pleasantries and repeated tool payloads. Never include personal data.

{conversation_history}"""


def build_events_compaction_config() -> EventsCompactionConfig:
    """Sliding-window + token-threshold compaction summarised by Gemini Flash."""
    return EventsCompactionConfig(
        summarizer=LlmEventSummarizer(
            llm=Gemini(model=SETTINGS.fast_model, retry_options=HTTP_RETRY),
            prompt_template=COMPACTION_PROMPT,
        ),
        compaction_interval=4,
        overlap_size=1,
        token_threshold=40_000,
        event_retention_size=12,
    )


def build_context_cache_config() -> ContextCacheConfig:
    """Cache the static constitution/knowledge prefix for 30 minutes."""
    return ContextCacheConfig(min_tokens=2048, ttl_seconds=1800, cache_intervals=10)


def trim_text_for_state(text: str, max_chars: int = 6000) -> str:
    """Token-budget style truncation for free text persisted into session state."""
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head - 40
    return f"{text[:head]}\n...[truncated {len(text) - head - tail} chars]...\n{text[-tail:]}"
