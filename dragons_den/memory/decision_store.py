"""Persistent store of filed architecture review decisions.

Backends (``DECISION_STORE_BACKEND``):

* ``firestore`` - Google Cloud Firestore collection ``den_review_decisions``
  (provisioned by Terraform). Production default on Cloud Run.
* ``sqlite``    - local file (``DECISION_STORE_PATH``) with an FTS5 full-text index
  for fast keyword retrieval; used for local dev, CI and the eval harness.

The store is seeded with historical decisions so the ``review_historian`` and the
dragons can cite precedent ("we rejected a single-VM storefront for Orbit Retail").
All summaries are PII-redacted before they are written.
"""

from __future__ import annotations

import functools
import json
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from dragons_den.config import SETTINGS
from dragons_den.observability.pii import redactor

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_COLLECTION = "den_review_decisions"


class DecisionStore(Protocol):
    def save(self, record: dict[str, Any]) -> str: ...
    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]: ...


def _seed_records() -> list[dict[str, Any]]:
    return json.loads((_DATA_DIR / "seed_review_decisions.json").read_text())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]


class SqliteDecisionStore:
    """SQLite + FTS5 keyword-indexed decision store (thread-safe)."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS decisions USING fts5("
            "decision_id UNINDEXED, customer_alias, title, decision UNINDEXED, den_score UNINDEXED, summary, filed_at UNINDEXED)"
        )
        if self._conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0:
            for rec in _seed_records():
                self._insert(rec)
        self._conn.commit()

    def _insert(self, rec: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
            (
                rec["decision_id"],
                rec["customer_alias"],
                rec["title"],
                rec["decision"],
                int(rec["den_score"]),
                rec["summary"],
                rec.get("filed_at", ""),
            ),
        )

    def save(self, record: dict[str, Any]) -> str:
        rec = {**redactor.redact_structure(record)}
        rec.setdefault("decision_id", f"DEN-{datetime.now(UTC):%Y}-{uuid.uuid4().hex[:6].upper()}")
        rec.setdefault("filed_at", datetime.now(UTC).isoformat())
        with self._lock:
            self._insert(rec)
            self._conn.commit()
        return rec["decision_id"]

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        toks = _tokens(query)
        if not toks:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in toks)
        with self._lock:
            rows = self._conn.execute(
                "SELECT decision_id, customer_alias, title, decision, den_score, summary FROM decisions "
                "WHERE decisions MATCH ? ORDER BY bm25(decisions) LIMIT ?",
                (fts_query, limit),
            ).fetchall()
        keys = ("decision_id", "customer_alias", "title", "decision", "den_score", "summary")
        return [dict(zip(keys, r, strict=True)) for r in rows]


class FirestoreDecisionStore:  # pragma: no cover - requires GCP
    """Firestore-backed decision store with keyword-array retrieval."""

    def __init__(self, project: str | None) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=project)
        self._col = self._db.collection(_COLLECTION)
        if not any(True for _ in self._col.limit(1).stream()):
            for rec in _seed_records():
                self.save(rec)

    def save(self, record: dict[str, Any]) -> str:
        rec = {**redactor.redact_structure(record)}
        rec.setdefault("decision_id", f"DEN-{datetime.now(UTC):%Y}-{uuid.uuid4().hex[:6].upper()}")
        rec.setdefault("filed_at", datetime.now(UTC).isoformat())
        rec["keywords"] = sorted(set(_tokens(f"{rec['customer_alias']} {rec['title']} {rec['summary']}")))
        self._col.document(rec["decision_id"]).set(rec)
        return rec["decision_id"]

    def search(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        toks = _tokens(query)[:30]  # Firestore array-contains-any limit
        if not toks:
            return []
        docs = self._col.where("keywords", "array_contains_any", toks).limit(limit * 3).stream()
        scored = []
        for d in docs:
            rec = d.to_dict()
            score = len(set(toks) & set(rec.get("keywords", [])))
            scored.append((score, rec))
        scored.sort(key=lambda x: -x[0])
        keys = ("decision_id", "customer_alias", "title", "decision", "den_score", "summary")
        return [{k: rec[k] for k in keys} for _, rec in scored[:limit]]


@functools.cache
def get_decision_store() -> DecisionStore:
    """Return the configured decision store singleton."""
    if SETTINGS.decision_store_backend == "firestore":
        return FirestoreDecisionStore(SETTINGS.gcp_project)
    return SqliteDecisionStore(SETTINGS.decision_store_path)
