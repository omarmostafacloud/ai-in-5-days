"""OpenTelemetry distributed tracing.

ADK already emits OpenTelemetry spans for ``invocation`` -> ``agent_run`` ->
``call_llm`` -> ``execute_tool``. This module installs a ``TracerProvider`` with:

* ``CloudTraceSpanExporter`` (``ENABLE_CLOUD_TRACE=true``) so the full request
  from user query to final verdict is visible as one trace in Cloud Trace, or
* ``ConsoleSpanExporter`` when ``OTEL_CONSOLE_EXPORT=true`` for local debugging.

It also exposes a ``tracer`` used by our own tools to add child spans with
domain attributes (``den.tool``, ``den.pillar``, ``den.decision``...).
"""

from __future__ import annotations

import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from dragons_den.config import SETTINGS

_LOG = logging.getLogger(__name__)
_CONFIGURED = False


def configure_tracing() -> trace.Tracer:
    """Install a global TracerProvider once and return the app tracer."""
    global _CONFIGURED
    if not _CONFIGURED:
        current = trace.get_tracer_provider()
        # Respect a provider already installed by ADK / the hosting runtime.
        if not isinstance(current, TracerProvider):
            provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": "dragons-den-agent",
                        "service.version": os.getenv("APP_VERSION", "dev"),
                        "cloud.provider": "gcp",
                    }
                )
            )
            if SETTINGS.enable_cloud_trace and SETTINGS.gcp_project:
                try:
                    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

                    provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter(project_id=SETTINGS.gcp_project)))
                except Exception as exc:  # exporter optional at dev time
                    _LOG.warning("Cloud Trace exporter unavailable: %s", exc)
            if os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true":
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
        _CONFIGURED = True
    return trace.get_tracer("dragons_den")


tracer = configure_tracing()
