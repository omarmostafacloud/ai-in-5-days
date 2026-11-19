"""PII redaction for logs, memory and model traffic.

Two layers:

1. **Local deterministic scrubber** (always on): regex detectors for emails, phone
   numbers, credit cards (Luhn-validated), IBANs, US SSNs, IPv4 addresses, GCP API
   keys, bearer tokens and private keys. Zero latency, works offline and in CI.
2. **Google Cloud Sensitive Data Protection (DLP)** (``ENABLE_CLOUD_DLP=true``):
   ``deidentify_content`` with replace-with-infotype transformation for
   higher-recall detection (person names, street addresses, ...). Falls back to
   the local scrubber on any error so redaction never blocks the request path.

Redaction is applied: (a) in the JSON log formatter, (b) on user messages and model
requests/responses in the guardrail plugin, and (c) before any session summary is
written to long-term memory or the decision store.
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Callable
from typing import Any

from dragons_den.config import SETTINGS

_LOG = logging.getLogger(__name__)


def _luhn_ok(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# Ordered: most specific first.
_PATTERNS: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"), None),
    ("GCP_API_KEY", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), None),
    ("BEARER_TOKEN", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{20,}=*"), None),
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), None),
    ("CREDIT_CARD_NUMBER", re.compile(r"\b(?:\d[ \-]?){13,19}\b"), _luhn_ok),
    ("IBAN_CODE", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b"), None),
    ("US_SOCIAL_SECURITY_NUMBER", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), None),
    ("PHONE_NUMBER", re.compile(r"(?<![\w.])\+?\d{1,3}[\s\-.]?\(?\d{2,4}\)?[\s\-.]\d{3,4}[\s\-.]\d{3,4}\b"), None),
    ("IP_ADDRESS", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), None),
]


class PiiRedactor:
    """Scrubs sensitive data from strings and nested structures."""

    def __init__(self, use_cloud_dlp: bool = False, project: str | None = None) -> None:
        self._use_cloud_dlp = use_cloud_dlp and bool(project)
        self._project = project

    # ------------------------------------------------------------------ local
    @staticmethod
    def redact_local(text: str) -> str:
        """Apply the deterministic regex scrubber."""
        if not text:
            return text
        for info_type, pattern, validator in _PATTERNS:

            def _sub(match: re.Match[str], _it: str = info_type, _v: Callable[[str], bool] | None = validator) -> str:
                if _v is not None and not _v(match.group(0)):
                    return match.group(0)
                return f"[REDACTED_{_it}]"

            text = pattern.sub(_sub, text)
        return text

    # ------------------------------------------------------------------ cloud
    @functools.cached_property
    def _dlp_client(self) -> Any:
        from google.cloud import dlp_v2  # lazy: optional dependency at runtime

        return dlp_v2.DlpServiceClient()

    def _redact_cloud_dlp(self, text: str) -> str:
        info_types = [
            {"name": n}
            for n in (
                "PERSON_NAME",
                "EMAIL_ADDRESS",
                "PHONE_NUMBER",
                "CREDIT_CARD_NUMBER",
                "IBAN_CODE",
                "STREET_ADDRESS",
                "US_SOCIAL_SECURITY_NUMBER",
                "GCP_API_KEY",
            )
        ]
        response = self._dlp_client.deidentify_content(
            request={
                "parent": f"projects/{self._project}/locations/global",
                "inspect_config": {"info_types": info_types, "min_likelihood": "LIKELY"},
                "deidentify_config": {
                    "info_type_transformations": {
                        "transformations": [{"primitive_transformation": {"replace_with_info_type_config": {}}}]
                    }
                },
                "item": {"value": text},
            },
            timeout=5.0,
        )
        return response.item.value

    # ------------------------------------------------------------------ public
    def redact(self, text: str) -> str:
        """Redact a string with Cloud DLP when enabled, always followed by the local pass."""
        if not isinstance(text, str) or not text:
            return text
        if self._use_cloud_dlp:
            try:
                text = self._redact_cloud_dlp(text)
            except Exception as exc:  # never block the request path on DLP
                _LOG.debug("Cloud DLP unavailable, using local redaction: %s", type(exc).__name__)
        return self.redact_local(text)

    def redact_structure(self, value: Any) -> Any:
        """Recursively redact strings inside dicts/lists/tuples."""
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {k: self.redact_structure(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return type(value)(self.redact_structure(v) for v in value)
        return value


redactor = PiiRedactor(use_cloud_dlp=SETTINGS.enable_cloud_dlp, project=SETTINGS.gcp_project)
