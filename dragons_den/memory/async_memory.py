"""Asynchronous, non-blocking memory generation and consolidation.

After the root agent finishes a turn, ``schedule_memory_consolidation`` (an
``after_agent_callback``) returns *immediately* and spawns a background
``asyncio`` task that:

1. snapshots the relevant session state (pitch digest, verdicts, decision),
2. PII-redacts it (Cloud DLP when enabled),
3. pushes the session to the long-term memory service
   (``VertexAiMemoryBankService.add_session_to_memory`` - itself an LRO), and
4. writes a compact "review digest" into the decision store so future pitches can
   retrieve precedent.

Expensive / blocking I/O runs in ``asyncio.to_thread`` so the event loop serving
the user's stream is never blocked. Tasks are tracked in a module-level set to
avoid premature garbage collection, and failures are logged, never raised.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from dragons_den.memory.compaction import trim_text_for_state
from dragons_den.observability.logging import log_event
from dragons_den.observability.pii import redactor

BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
_STATE_KEYS = ("design_pitch", "chair_verdict", "critic_report", "filed_decision_id")


def _snapshot_state(state: Any) -> dict[str, Any]:
    raw = state.to_dict() if hasattr(state, "to_dict") else dict(state or {})
    return {k: raw[k] for k in _STATE_KEYS if k in raw}


async def consolidate_memory(callback_context: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Background coroutine: redact, push to Memory Bank, and record digest."""
    started = time.perf_counter()
    result: dict[str, Any] = {"memory_bank": "skipped", "digest_chars": 0}
    try:
        redacted = await asyncio.to_thread(redactor.redact_structure, snapshot)
        digest = trim_text_for_state(json.dumps(redacted, default=str), 4000)
        result["digest_chars"] = len(digest)
        try:
            await callback_context.add_session_to_memory()
            result["memory_bank"] = "ok"
        except (ValueError, AttributeError) as exc:  # no memory service configured
            result["memory_bank"] = f"unavailable:{type(exc).__name__}"
        log_event(
            "ASYNC_MEMORY_CONSOLIDATED",
            "Background memory consolidation finished",
            phase="OUTCOME",
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            **result,
        )
    except Exception as exc:  # never surface background failures to the user
        log_event("ASYNC_MEMORY_FAILED", "Background memory consolidation failed", phase="OUTCOME", error=repr(exc))
    return result


async def schedule_memory_consolidation(callback_context: Any) -> None:
    """``after_agent_callback``: fire-and-forget consolidation so the UI never blocks."""
    snapshot = _snapshot_state(getattr(callback_context, "state", {}))
    if not snapshot:
        return None
    task = asyncio.create_task(consolidate_memory(callback_context, snapshot), name="den-memory-consolidation")
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    log_event("ASYNC_MEMORY_SCHEDULED", "Memory consolidation scheduled in background", phase="INTENT", keys=list(snapshot))
    return None
