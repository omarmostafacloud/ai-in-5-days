"""Test configuration: isolate stores, disable cloud calls."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("DECISION_STORE_PATH", ":memory:")
os.environ.setdefault("DECISION_STORE_BACKEND", "sqlite")
os.environ.setdefault("ENABLE_CLOUD_DLP", "false")
os.environ.setdefault("ENABLE_CLOUD_TRACE", "false")
os.environ.setdefault("ENABLE_MODEL_ARMOR", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("GOOGLE_API_KEY", "test-placeholder-not-a-key")
