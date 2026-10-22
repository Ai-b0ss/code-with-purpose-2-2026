"""Compare two deidentified historical label queues without reading answers."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def compare(old_rows: list[dict], new_rows: list[dict]) -> tuple[dict, list[dict]]:
    old = {row["source_row_hash"]: row for row in old_rows}
    new = {row["source_row_hash"]: row for row in new_rows}
    if len(old) != len(old_rows) or len(new) != len(new_rows):
        raise ValueError("duplicate historical application")
    if not set(new) <= set(old):
        raise ValueError("new queue contains an unknown historical application")
    field_counts = Counter()
    category_changes = Counter()
    priorities = []
    missing_new_fields = 0
    compared_fields = 0
    for row_hash, current in new.items():
        before = {field["field_id"]: field for field in old[row_hash]["fields"]}
        after = {field["field_id"]: field for field in current["fields"]}
        if len(before) != len(old[row_hash]["fields"]) or len(after) != len(current["fields"]):
            raise ValueError("duplicate field label")
        missing_new_fields += len(set(before) - set(after))
        for field_id, label in after.items():
            prior = before.get(field_id)
            if prior is None:
                continue
            compared_fields += 1
            field_counts[field_id] += 1
            old_category, new_category = prior["category"], label["category"]
            old_quality, new_quality = prior.get("quality_level"), label.get("quality_level")
            if (old_category, old_quality) == (new_category, new_quality):
                continue
            category_changes[field_id] += 1
            priorities.append({
                "source_row_hash": row_hash, "field_id": field_id,
                "provisional_category": new_category,
                "audit_category": old_category,
                "provisional_quality_level": new_quality,
                "old_quality_level": old_quality,
                "disagrees": True,
                "error_code": None,
            })
    return {
        "old_rows": len(old), "new_rows": len(new),
        "complete_coverage": set(new) == set(old),
        "compared_fields": compared_fields,
        "changed_fields": len(priorities),
        "missing_new_fields": missing_new_fields,
        "by_field": [
            {"field_id": field_id, "compared": count,
             "changed": category_changes[field_id]}
            for field_id, count in sorted(field_counts.items())
        ],
        "interpretation": "Cross-run disagreement; neither queue is curator ground truth.",
    }, priorities


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", type=Path,
                        default=ROOT / "data" / "historical_field_review_queue_v7.jsonl")
    parser.add_argument("--new", type=Path,
                        default=ROOT / "data" / "historical_field_review_queue_v9.jsonl")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "historical_queue_v7_v9_disagreements.jsonl")
    args = parser.parse_args()
    summary, priorities = compare(read_rows(args.old), read_rows(args.new))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                                for row in priorities), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
