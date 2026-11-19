"""Structured JSON logging with intent/outcome semantics.

Every log line is a single JSON object (Cloud Logging ``jsonPayload`` compatible)
with ``severity``, ``event_type``, ``phase`` (``INTENT`` | ``OUTCOME`` | ``GUARDRAIL``
| ``LIFECYCLE``), agent/tool names, session/invocation ids and the active
OpenTelemetry ``trace_id``/``span_id`` so logs and traces are correlated in Cloud
Logging / Cloud Trace (``logging.googleapis.com/trace``). All string fields are
PII-scrubbed in the formatter *before* the line leaves the process.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

from opentelemetry import trace

from dragons_den.config import SETTINGS
from dragons_den.observability.pii import redactor

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formats log records as PII-redacted, trace-correlated JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx and span_ctx.is_valid:
            trace_id = format(span_ctx.trace_id, "032x")
            payload["trace_id"] = trace_id
            payload["span_id"] = format(span_ctx.span_id, "016x")
            if SETTINGS.gcp_project:
                payload["logging.googleapis.com/trace"] = f"projects/{SETTINGS.gcp_project}/traces/{trace_id}"
                payload["logging.googleapis.com/spanId"] = payload["span_id"]

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(redactor.redact_structure(payload), default=str, ensure_ascii=False)


def configure_logging() -> logging.Logger:
    """Install the JSON formatter on the ``dragons_den`` logger (idempotent)."""
    logger = logging.getLogger("dragons_den")
    if not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(SETTINGS.log_level)
    return logger


LOGGER = configure_logging()


def log_event(
    event_type: str,
    message: str,
    *,
    phase: str = "LIFECYCLE",
    severity: int = logging.INFO,
    **fields: Any,
) -> None:
    """Emit one structured event.

    Args:
        event_type: Stable machine-readable event name (e.g. ``TOOL_INTENT``).
        message: Human readable message.
        phase: ``INTENT`` before an action, ``OUTCOME`` after, ``GUARDRAIL`` or ``LIFECYCLE``.
        severity: Python logging level.
        **fields: Extra structured metadata (agent, tool, args, latency_ms, ...).
    """
    safe = {(f"den_{k}" if k in _RESERVED else k): v for k, v in fields.items()}
    LOGGER.log(severity, message, extra={"event_type": event_type, "phase": phase, **safe})
