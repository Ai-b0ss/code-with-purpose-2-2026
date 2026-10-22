"""Aggregate historical outcomes and provisional field labels without exposing answers.

Acceptance is an observed later decision, not a field-level ground truth.  Blank
outcomes are excluded; the two spring files are duplicate snapshots, so use the
newest one only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path

from openpyxl import load_workbook

from scripts.classify_historical_fields import (
    DATASET_CONFIG, FIELD_HEADERS, ROOT, SOURCE_FILES, historical_answers,
)


def outcomes():
    seen = set()
    for path in SOURCE_FILES:
        with closing(load_workbook(path, read_only=True, data_only=True)) as workbook:
            for sheet in workbook:
                rows = iter(sheet.values)
                headers = [str(value or "").strip() for value in next(rows)]
                score_index = next(i for i, h in enumerate(headers)
                                   if DATASET_CONFIG["score_header_contains"] in h)
                accepted_index = next(i for i, h in enumerate(headers)
                                      if DATASET_CONFIG["accepted_header_contains"] in h)
                direction_index = next(i for i, h in enumerate(headers)
                                       if DATASET_CONFIG["direction_header_contains"] in h)
                field_indexes = {
                    field: next((i for i, h in enumerate(headers) if marker in h), None)
                    for field, marker in FIELD_HEADERS.items()
                }
                for row in rows:
                    score = row[score_index] if score_index < len(row) else None
                    if not isinstance(score, (int, float)) or score not in (0, 1, 2, 3):
                        continue
                    answers = historical_answers(row, field_indexes)
                    identity = json.dumps(
                        [path.name, sheet.title, answers, score],
                        ensure_ascii=False, sort_keys=True,
                    )
                    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    if digest in seen:
                        continue
                    seen.add(digest)
                    raw = str(row[accepted_index] or "").strip().upper()
                    accepted = DATASET_CONFIG["accepted_values"].get(raw)
                    directions = str(row[direction_index] or "").casefold()
                    direction_rules = DATASET_CONFIG["direction_groups"]
                    has_funds = direction_rules["funds_marker"] in directions
                    has_other = any(marker in directions for marker in direction_rules["other_markers"])
                    direction_group = (
                        "funds_only" if has_funds and not has_other else
                        "funds_plus_other" if has_funds else "other_or_empty"
                    )
                    yield digest, path.stem, int(score), accepted, direction_group


def report(queue_path: Path) -> dict:
    records = list(outcomes())
    score_counts = Counter((cohort, score, accepted) for _, cohort, score, accepted, _ in records)
    direction_counts = Counter((cohort, score, direction, accepted)
                               for _, cohort, score, accepted, direction in records)
    labels = {}
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            row_hash = item["source_row_hash"]
            if row_hash in labels:
                raise ValueError("duplicate historical label row")
            labels[row_hash] = item
    current_hashes = {digest for digest, *_ in records}
    if set(labels) != current_hashes:
        raise ValueError("field labels do not match the current historical workbooks")
    groups = defaultdict(lambda: Counter())
    for digest, cohort, score, accepted, _ in records:
        if accepted is None or digest not in labels:
            continue
        for field in labels[digest]["fields"]:
            category = field["category"]
            if category == "unclassified_model_error":
                continue
            groups[(cohort, score, field["field_id"], category)]["accepted" if accepted else "rejected"] += 1
    # Small cells are suppressed so the output is useful for audit without
    # encouraging rules from single examples.
    associations = [
        {"cohort": cohort, "score": score, "field": field, "category": category,
         "accepted": counts["accepted"], "rejected": counts["rejected"]}
        for (cohort, score, field, category), counts in sorted(groups.items())
        if sum(counts.values()) >= 5
    ]
    return {
        "historical_rows": len(records),
        "labelled_rows": len(labels),
        "outcomes_by_score": [
            {"cohort": cohort, "score": score, "accepted": accepted, "count": count}
            for (cohort, score, accepted), count in sorted(
                score_counts.items(), key=lambda x: (x[0][0], x[0][1], str(x[0][2]))
            )
        ],
        "directions_by_score": [
            {"cohort": cohort, "score": score, "direction_group": direction,
             "accepted": accepted, "count": count}
            for (cohort, score, direction, accepted), count in sorted(
                direction_counts.items(), key=lambda x: (x[0][0], x[0][1], x[0][2], str(x[0][3]))
            )
        ],
        "field_associations_minimum_n_5": associations,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path,
                        default=ROOT / "data" / "historical_field_review_queue_v7.jsonl")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "acceptance_anomaly_report.json")
    args = parser.parse_args()
    result = report(args.queue)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("historical_rows", result["historical_rows"], "labelled_rows", result["labelled_rows"])
    print("outcomes_by_score", result["outcomes_by_score"])
    print("associations_n_ge_5", len(result["field_associations_minimum_n_5"]))


if __name__ == "__main__":
    main()
