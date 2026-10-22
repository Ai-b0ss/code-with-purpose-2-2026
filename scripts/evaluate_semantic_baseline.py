"""Evaluate provisional semantic field labels against historical outcomes.

This diagnostic never installs coefficients into the live scorer.  It uses
season-blocked holdouts and reports the zero-score recall explicitly.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from backend.availability_policy import RULES
from scripts.classify_historical_fields import historical_records
from scripts.evaluate_historical_baseline import features as text_features, ridge_predict


ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("experience", "expectations", "scientific_interests", "hobbies", "followup_answers.superpower")
FEATURES = (
    *(f"{field}.level" for field in FIELDS),
    *(f"{field}.measured" for field in FIELDS),
    "first_bachelor", "any_nonsense", "any_off_topic",
    "first_bachelor_x_experience", "first_bachelor_x_expectations",
)


def features(label_row: dict, source_row: dict) -> list[float]:
    fields = {field["field_id"]: field for field in label_row["fields"]}
    quality = [fields.get(name, {}).get("quality_level") for name in FIELDS]
    levels = [float(value or 0) for value in quality]
    measured = [float(value is not None) for value in quality]
    course = str(source_row["answers"].get("course", "")).casefold()
    first = float(bool(re.search(RULES["patterns"]["first_year"], course))
                  and not re.search(RULES["patterns"]["graduate_course"], course))
    categories = {field["category"] for field in label_row["fields"]
                  if field["field_id"] in FIELDS}
    return [
        *levels, *measured, first,
        float("nonsense" in categories), float("off_topic" in categories),
        first * levels[0], first * levels[1],
    ]


def metrics(actual: np.ndarray, predicted: np.ndarray, baseline: float) -> dict:
    zeros = actual == 0
    return {
        "n": len(actual), "zero_n": int(zeros.sum()),
        "ridge_mae": round(float(np.mean(np.abs(predicted - actual))), 3),
        "median_baseline_mae": round(float(np.mean(np.abs(baseline - actual))), 3),
        "predicted_zero_n": int((predicted < 0.5).sum()),
        "zero_recall": round(float(np.mean(predicted[zeros] < 0.5)), 3) if zeros.any() else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels_jsonl", type=Path, nargs="?",
                        default=ROOT / "data" / "historical_field_review_queue_v7.jsonl")
    parser.add_argument("--rated-only", action="store_true",
                        help="Compare only scores 1–3; historical zero means no interview invitation")
    args = parser.parse_args()
    source = {row["source_row_hash"]: row for row in historical_records()}
    labels = [json.loads(line) for line in args.labels_jsonl.read_text(encoding="utf-8").splitlines()
              if line.strip()]
    if len({row["source_row_hash"] for row in labels}) != len(labels):
        raise ValueError("duplicate label rows")
    labels = [row for row in labels if row["source_row_hash"] in source]
    if args.rated_only:
        labels = [row for row in labels if row["historical_score"] > 0]
    x = np.asarray([features(row, source[row["source_row_hash"]]) for row in labels], dtype=float)
    text_x = np.asarray([text_features(source[row["source_row_hash"]]) for row in labels], dtype=float)
    y = np.asarray([row["historical_score"] for row in labels], dtype=float)
    seasons = np.asarray(["2024" if "2024" in row["cohort"] else "2025" for row in labels])
    result = {"labeled_n": len(labels), "target": "rated_1_to_3" if args.rated_only else "all_0_to_3",
              "features": FEATURES, "blocked_season_holdout": []}
    for held_out in ("2024", "2025"):
        train = seasons != held_out
        test = ~train
        if train.sum() < 10 or test.sum() < 10:
            continue
        prediction = ridge_predict(x[train], y[train], x[test])
        text_prediction = ridge_predict(text_x[train], y[train], text_x[test])
        if args.rated_only:
            prediction = np.clip(prediction, 1, 3)
            text_prediction = np.clip(text_prediction, 1, 3)
        zeros = y[test] == 0
        result["blocked_season_holdout"].append({
            "test_season": held_out, "train_n": int(train.sum()),
            **metrics(y[test], prediction, float(np.median(y[train]))),
            "text_length_mae": round(float(np.mean(np.abs(text_prediction - y[test]))), 3),
            "text_length_zero_recall": (
                round(float(np.mean(text_prediction[zeros] < 0.5)), 3)
                if zeros.any() else None
            ),
        })
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
