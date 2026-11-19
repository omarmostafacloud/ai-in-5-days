"""Shared tool plumbing: reference data, tracing decorator and guided errors."""

from __future__ import annotations

import functools
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from dragons_den.observability.logging import log_event
from dragons_den.observability.tracing import tracer
from dragons_den.schemas import GuidedToolError, to_tool_result

_DATA = Path(__file__).resolve().parent.parent / "data"
F = TypeVar("F", bound=Callable[..., dict[str, Any]])


@functools.cache
def reference_catalog() -> dict[str, Any]:
    """Load the SLA / pricing / residency reference catalog."""
    return json.loads((_DATA / "reference_catalog.json").read_text())


@functools.cache
def principles() -> list[dict[str, Any]]:
    """Load the Well-Architected principle knowledge base."""
    return json.loads((_DATA / "well_architected_principles.json").read_text())


def match_catalog_key(name: str, catalog: dict[str, Any]) -> str | None:
    """Fuzzy-match a free-text component name to the longest catalog key it contains."""
    norm = " ".join(name.lower().replace("_", " ").replace("(", " ").replace(")", " ").split())
    if norm in catalog:
        return norm
    for key in sorted(catalog, key=len, reverse=True):
        if key in norm:
            return key
    return None


def guided_error(
    error_code: str,
    message: str,
    recovery_instructions: str,
    *,
    valid_values: list[str] | None = None,
    suggested_next_tool: str | None = None,
    retryable: bool = True,
) -> dict[str, Any]:
    """Build a structured, LLM-actionable error payload (never raise to the model)."""
    return to_tool_result(
        GuidedToolError(
            error_code=error_code,
            message=message,
            recovery_instructions=recovery_instructions,
            valid_values=valid_values,
            suggested_next_tool=suggested_next_tool,
            retryable=retryable,
        )
    )


def _validation_hint(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        parts.append(f"'{loc}': {err['msg']}")
    return "; ".join(parts)


def den_tool(func: F) -> F:
    """Decorate a tool with an OTel span, latency metric and guided error handling.

    * ``ValidationError`` -> ``INVALID_ARGUMENT`` with per-field fix instructions.
    * Any other exception -> ``TOOL_EXECUTION_FAILED`` with a safe retry hint.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        with tracer.start_as_current_span(f"den.tool.{func.__name__}") as span:
            span.set_attribute("den.tool", func.__name__)
            try:
                result = func(*args, **kwargs)
            except ValidationError as exc:
                result = guided_error(
                    "INVALID_ARGUMENT",
                    f"{func.__name__} rejected its arguments: {_validation_hint(exc)}",
                    f"Fix the listed fields to match the documented schema and call {func.__name__} again.",
                )
            except Exception as exc:  # defensive: tools must never crash the agent
                log_event("TOOL_CRASH", f"{func.__name__} raised", phase="OUTCOME", error=repr(exc), tool=func.__name__)
                result = guided_error(
                    "TOOL_EXECUTION_FAILED",
                    f"{func.__name__} failed unexpectedly ({type(exc).__name__}).",
                    "Retry once with simplified arguments; if it fails again, continue the review "
                    "without this evidence and state the gap explicitly in your findings.",
                )
            span.set_attribute("den.status", str(result.get("status")))
            span.set_attribute("den.latency_ms", round((time.perf_counter() - started) * 1000, 2))
            return result

    return wrapper  # type: ignore[return-value]
