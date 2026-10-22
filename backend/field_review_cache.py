"""Persist validated semantic labels without retaining questionnaire text.

Identical field/question/answer inputs reuse the first verified classification,
including when a later request reaches a different failover model. The cache
is invalidated when the classifier implementation or taxonomy changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_PATH = ROOT / "data" / "field_review_cache.sqlite3"
SOURCE_FILES = (Path(__file__).with_name("field_quality.py"),
                Path(__file__).with_name("field_taxonomy.json"),
                ROOT / "knowledge" / "field_quality_prompt.ru.txt")


class FieldReviewCache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.classifier_version = hashlib.sha256(
            b"\0".join(source.read_bytes() for source in SOURCE_FILES)
        ).hexdigest()
        self.taxonomy = json.loads(SOURCE_FILES[1].read_text(encoding="utf-8"))
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS field_review_cache (
                        input_hash TEXT PRIMARY KEY,
                        category TEXT NOT NULL,
                        signals_json TEXT NOT NULL,
                        evidence_start INTEGER NOT NULL,
                        evidence_end INTEGER NOT NULL
                    )
                """)
        finally:
            connection.close()

    def _key(self, field_id: str, answer: str, question: str) -> str:
        payload = json.dumps({"version": self.classifier_version,
                              "field_id": field_id, "question": question,
                              "answer": answer}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, field_id: str, answer: str, question: str) -> dict | None:
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            row = connection.execute(
                "SELECT category, signals_json, evidence_start, evidence_end "
                "FROM field_review_cache WHERE input_hash = ?",
                (self._key(field_id, answer, question),),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        category, signals_json, start, end = row
        if not 0 <= start < end <= len(answer):
            return None
        try:
            signals = json.loads(signals_json)
        except (TypeError, ValueError):
            return None
        if category not in self.taxonomy["categories"]:
            return None
        if (not isinstance(signals, list) or len(signals) > 5
                or any(item not in self.taxonomy["signals"] for item in signals)):
            return None
        return {"category": category, "signals": signals,
                "evidence": answer[start:end]}

    def put(self, field_id: str, answer: str, question: str, review: dict) -> dict:
        evidence = review["evidence"]
        start = answer.find(evidence)
        if start < 0 or not evidence:
            raise ValueError("Only exact verified evidence can be cached")
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                connection.execute(
                    "INSERT OR IGNORE INTO field_review_cache "
                    "(input_hash, category, signals_json, evidence_start, evidence_end) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (self._key(field_id, answer, question), review["category"],
                     json.dumps(review["signals"], ensure_ascii=False),
                     start, start + len(evidence)),
                )
        finally:
            connection.close()
        # Another request may have inserted first. Use its label immediately
        # so concurrent evaluations also receive the same numeric input.
        return self.get(field_id, answer, question) or review


def runtime_cache_for(adapter) -> FieldReviewCache | None:
    from backend.langchain_adapter import FailoverChatAdapter

    if not isinstance(adapter, FailoverChatAdapter):
        return None
    if os.getenv("GIM_FIELD_CACHE_ENABLED", "1") != "1":
        return None
    try:
        return FieldReviewCache(Path(os.getenv("GIM_FIELD_CACHE_PATH", DEFAULT_CACHE_PATH)))
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).warning("field_review_cache_unavailable")
        return None
