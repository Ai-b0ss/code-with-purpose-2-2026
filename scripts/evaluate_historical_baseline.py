"""Reproducible diagnostic ridge baseline on deidentified historical features.

This tests whether simple field-completeness rules transport between seasons.
It does not publish or install learned coefficients in the application.
"""
from __future__ import annotations

import json
import math
import re
from statistics import median

import numpy as np

from scripts.classify_historical_fields import historical_records
from backend.availability_policy import RULES


FEATURES = (
    "first_bachelor", "experience_present", "log_expectations",
    "log_experience", "log_scientific_interests", "log_hobbies",
    "log_superpower", "first_year_x_experience",
)


def features(row: dict) -> list[float]:
    answers = row["answers"]
    text = lambda key: str(answers.get(key, "") or "").strip()
    length = lambda key: math.log1p(len(text(key)))
    course = text("course").casefold()
    first = float(bool(re.search(RULES["patterns"]["first_year"], course))
                  and not re.search(RULES["patterns"]["graduate_course"], course))
    experience = float(text("experience") not in ("", "-", "—", "."))
    return [
        first, experience, length("expectations"), length("experience"),
        length("scientific_interests"), length("hobbies"),
        math.log1p(len(str(answers.get("followup_answers", {}).get("superpower", "")))),
        first * experience,
    ]


def ridge_predict(train_x, train_y, test_x, alpha=10.0):
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale == 0] = 1
    x = (train_x - mean) / scale
    z = (test_x - mean) / scale
    x = np.column_stack([np.ones(len(x)), x])
    z = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(x.shape[1]) * alpha
    penalty[0, 0] = 0
    coefficients = np.linalg.solve(x.T @ x + penalty, x.T @ train_y)
    return np.clip(z @ coefficients, 0, 3)


def main() -> None:
    rows = list(historical_records())
    x = np.array([features(row) for row in rows], dtype=float)
    y = np.array([row["historical_score"] for row in rows], dtype=float)
    seasons = ["2024" if "2024" in row["cohort"] else "2025" for row in rows]
    result = {"n": len(rows), "features": FEATURES, "blocked_season_holdout": []}
    for held_out in ("2024", "2025"):
        train = np.array([season != held_out for season in seasons])
        test = ~train
        pred = ridge_predict(x[train], y[train], x[test])
        baseline = float(median(y[train]))
        result["blocked_season_holdout"].append({
            "test_season": held_out,
            "train_n": int(train.sum()), "test_n": int(test.sum()),
            "train_score_zero": int((y[train] == 0).sum()),
            "test_score_zero": int((y[test] == 0).sum()),
            "ridge_mae": round(float(np.mean(np.abs(pred - y[test]))), 3),
            "median_baseline_mae": round(float(np.mean(np.abs(baseline - y[test]))), 3),
            "predicted_zero_count": int((pred < 0.5).sum()),
            "actual_zero_recall": (
                round(float(np.mean(pred[y[test] == 0] < 0.5)), 3)
                if (y[test] == 0).any() else None
            ),
        })
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
