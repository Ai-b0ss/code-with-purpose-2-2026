"""Independently reclassify provisional historical labels that need attention.

The provider sees one answer and its original question, without the old label,
historical score, identity, or acceptance outcome. Output contains no answers,
quotes, names, or contacts. It is an audit queue, never approved calibration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from backend.evaluator import EvaluationError, adapter_from_env
from backend.field_quality import review_fields
from scripts.classify_historical_fields import historical_records


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = ROOT / "data" / "historical_field_review_queue_v7.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "historical_field_label_audit.jsonl"
POSITIVE_OUTPUT = ROOT / "data" / "historical_positive_label_audit.jsonl"
REVIEW_CATEGORIES = frozenset({"unclear", "off_topic", "nonsense", "abusive", "instruction_attempt"})
POSITIVE_CATEGORIES = frozenset({"specific_relevant", "relevant_general"})


def targets(labels: list[dict], source: dict[str, dict], *, mode: str = "flagged",
            per_stratum: int = 1) -> list[tuple[dict, dict, dict]]:
    if len({row["source_row_hash"] for row in labels}) != len(labels):
        raise ValueError("duplicate provisional label row")
    if set(source) != {row["source_row_hash"] for row in labels}:
        raise ValueError("provisional labels do not match current historical XLSX rows")
    selected = []
    for row in labels:
        original = source[row["source_row_hash"]]
        for field in row["fields"]:
            if mode == "flagged" and field["category"] in REVIEW_CATEGORIES:
                selected.append((row, original, field))
            elif (mode == "positive" and field["category"] in POSITIVE_CATEGORIES
                  and field.get("quality_level") is not None):
                selected.append((row, original, field))
    if mode == "positive":
        # Fixed, outcome-blind sample: one per season, field and positive
        # category. Hash order keeps reruns identical across machines.
        selected.sort(key=lambda item: hashlib.sha256(
            (item[0]["source_row_hash"] + ":" + item[2]["field_id"]).encode("ascii")
        ).hexdigest())
        counts = Counter()
        sample = []
        for item in selected:
            stratum = item[0]["cohort"], item[2]["field_id"], item[2]["category"]
            if counts[stratum] < per_stratum:
                sample.append(item)
                counts[stratum] += 1
        selected = sample
    elif mode != "flagged":
        raise ValueError("Unknown audit mode")
    return selected


def disputed_field_keys(labels: list[dict], audit_rows: list[dict]) -> set[tuple[str, str]]:
    """Validate an audit against its label queue before excluding any reference."""
    indexed = {
        (row["source_row_hash"], field["field_id"]): field["category"]
        for row in labels for field in row["fields"]
    }
    disputed = set()
    seen = set()
    for item in audit_rows:
        key = item["source_row_hash"], item["field_id"]
        if key in seen or indexed.get(key) != item["provisional_category"]:
            raise ValueError("Audit does not match current provisional labels")
        seen.add(key)
        if item.get("disagrees") or item.get("error_code"):
            disputed.add(key)
    return disputed


def field_payload(answers: dict, field_id: str) -> dict:
    root, *path = field_id.split(".")
    value = answers[root]
    for part in path:
        value = value[int(part)] if isinstance(value, list) else value[part]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("selected field is not a nonempty text answer")
    for part in reversed(path):
        value = [""] * int(part) + [value] if part.isdecimal() else {part: value}
    return {root: value}


def audit(labels: list[dict], source: dict[str, dict], adapter,
          *, max_fields: int | None = None, mode: str = "flagged",
          per_stratum: int = 1) -> list[dict]:
    selected = targets(labels, source, mode=mode, per_stratum=per_stratum)
    if max_fields is not None:
        selected = selected[:max_fields]
    output = []
    for row, original, field in selected:
        try:
            payload = field_payload(original["answers"], field["field_id"])
            review = review_fields(payload, adapter)
            reviewed = next(item for item in review if item["field_id"] == field["field_id"])
            new_category = reviewed["category"]
            error = None
        except EvaluationError as exc:
            new_category = None
            error = exc.code
        output.append({
            "source_row_hash": row["source_row_hash"],
            "cohort": row["cohort"],
            "field_id": field["field_id"],
            "provisional_category": field["category"],
            "audit_category": new_category,
            "disagrees": new_category is not None and new_category != field["category"],
            "error_code": error,
            "audit_model": getattr(adapter, "last_model", None),
        })
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--max-fields", type=int)
    parser.add_argument("--mode", choices=("flagged", "positive"), default="flagged")
    parser.add_argument("--per-stratum", type=int, default=1,
                        help="Positive mode: fields sampled per season/field/category")
    args = parser.parse_args()
    if args.per_stratum <= 0 or args.max_fields is not None and args.max_fields <= 0:
        parser.error("sample sizes must be positive")
    destination = args.out or (POSITIVE_OUTPUT if args.mode == "positive" else DEFAULT_OUTPUT)
    labels = [json.loads(line) for line in args.labels.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    source = {row["source_row_hash"]: row for row in historical_records()}
    results = audit(labels, source, adapter_from_env(), max_fields=args.max_fields,
                    mode=args.mode, per_stratum=args.per_stratum)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in results), encoding="utf-8")
    counts = Counter("error" if item["error_code"] else "disagreement" if item["disagrees"]
                     else "agreement" for item in results)
    print("audited", len(results), "agreement", counts["agreement"],
          "disagreement", counts["disagreement"], "error", counts["error"],
          "out", destination)


if __name__ == "__main__":
    main()
