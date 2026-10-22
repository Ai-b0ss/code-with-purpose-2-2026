"""Blocked-season diagnostic for semantic field quality and interactions.

The current label queue is model-generated and unapproved.  This command
reports aggregate holdout metrics only; it never installs coefficients.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median

import numpy as np

from scripts.classify_historical_fields import ROOT, historical_records
from scripts.evaluate_historical_baseline import ridge_predict


FIELD_IDS = (
    "expectations", "experience", "scientific_interests", "hobbies",
    "followup_answers.superpower",
)
INTERACTIONS = (
    ("expectations", "experience"),
    ("expectations", "scientific_interests"),
    ("experience", "followup_answers.superpower"),
)
DEFAULT_LABELS = ROOT / "data" / "historical_field_review_queue_v7.jsonl"


def feature_vector(fields: list[dict]) -> list[float]:
    by_id = {item["field_id"]: item for item in fields}
    if len(by_id) != len(fields):
        raise ValueError("duplicate field label")
    levels: dict[str, int | None] = {}
    for field_id in FIELD_IDS:
        value = by_id.get(field_id, {}).get("quality_level")
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)
                                  or value not in (0, 1, 2, 3)):
            raise ValueError("invalid field quality")
        levels[field_id] = value
    vector = [float((levels[field_id] if levels[field_id] is not None else 2) - 2)
              for field_id in FIELD_IDS]
    vector.extend(float(levels[field_id] is None) for field_id in FIELD_IDS)
    vector.extend(
        float((levels[left] - 2) * (levels[right] - 2))
        if levels[left] is not None and levels[right] is not None else 0.0
        for left, right in INTERACTIONS
    )
    return vector


def evaluate(rows: list[dict], labels: list[dict]) -> dict:
    records = {row["source_row_hash"]: row for row in rows}
    if len(records) != len(rows):
        raise ValueError("duplicate historical application")
    by_hash = {row["source_row_hash"]: row for row in labels}
    if len(by_hash) != len(labels) or set(records) != set(by_hash):
        raise ValueError("labels must match the current historical workbooks")
    if len({row["cohort"] for row in rows}) < 2:
        raise ValueError("at least two cohorts are required")
    ordered = [records[digest] for digest in sorted(records)]
    x = np.array([feature_vector(by_hash[row["source_row_hash"]]["fields"])
                  for row in ordered], dtype=float)
    y = np.array([row["historical_score"] for row in ordered], dtype=float)
    cohorts = np.array([row["cohort"] for row in ordered])
    holdouts = []
    for held_out in sorted(set(cohorts)):
        train = cohorts != held_out
        test = ~train
        predicted = ridge_predict(x[train], y[train], x[test], alpha=10.0)
        baseline = float(median(y[train]))
        zero = y[test] == 0
        holdouts.append({
            "held_out_cohort": held_out,
            "train_n": int(train.sum()), "test_n": int(test.sum()),
            "test_zero_n": int(zero.sum()),
            "semantic_ridge_mae": round(float(np.mean(np.abs(predicted - y[test]))), 3),
            "train_median_baseline_mae": round(float(np.mean(np.abs(baseline - y[test]))), 3),
            "semantic_predicted_zero_n": int((predicted < 0.5).sum()),
            "zero_recall": round(float(np.mean(predicted[zero] < 0.5)), 3)
            if zero.any() else None,
        })
    return {
        "n": len(rows), "label_source": "supplied_field_labels_unverified_by_this_script",
        "feature_fields": list(FIELD_IDS),
        "interactions": [list(pair) for pair in INTERACTIONS],
        "ridge_alpha": 10.0,
        "blocked_cohort_holdouts": holdouts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels_jsonl", type=Path, nargs="?", default=DEFAULT_LABELS)
    args = parser.parse_args()
    labels = [json.loads(line) for line in args.labels_jsonl.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    print(json.dumps(evaluate(list(historical_records()), labels), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
