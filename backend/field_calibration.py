"""Median-relative field evidence with small-sample shrinkage.

This module consumes independent, reviewed historical field labels.  Raw
historical 0-3 outcomes are *not* individual field quality labels.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median


CALIBRATION_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "field_calibration.json"
SCORING_POLICY_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "scoring_policy.json"
TAXONOMY_PATH = Path(__file__).with_name("field_taxonomy.json")


def current_taxonomy_version() -> str:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))["version"]


def build_calibration(reviewed_rows: list[dict], *, shrinkage: float | None = None,
                      source: str = "curator_reviewed_field_labels",
                      exclude_field_keys: set[tuple[str, str]] | None = None) -> dict:
    """Build per-field reference medians from curator-reviewed field levels."""
    if shrinkage is None:
        policy = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
        shrinkage = float(policy["median_shrinkage"])
    if shrinkage <= 0:
        raise ValueError("shrinkage must be positive")
    values: dict[str, list[int]] = {}
    cohort_values: dict[str, dict[str, list[int]]] = {}
    seen_hashes: set[str] = set()
    excluded = 0
    exclude_field_keys = exclude_field_keys or set()
    for row in reviewed_rows:
        row_hash = row.get("source_row_hash")
        if row_hash:
            if row_hash in seen_hashes:
                raise ValueError("duplicate historical application")
            seen_hashes.add(row_hash)
        for review in row.get("fields", []):
            field_id = review.get("field_id")
            if (row_hash, field_id) in exclude_field_keys:
                excluded += 1
                continue
            quality = review.get("quality_level")
            if not isinstance(field_id, str) or not isinstance(quality, int) or isinstance(quality, bool) or not 0 <= quality <= 3:
                continue
            values.setdefault(field_id, []).append(quality)
            cohort = row.get("cohort")
            if isinstance(cohort, str) and cohort:
                cohort_values.setdefault(cohort, {}).setdefault(field_id, []).append(quality)
    return {
        "version": "gim-field-median-v1",
        "taxonomy_version": current_taxonomy_version(),
        "source": source,
        "shrinkage": shrinkage,
        "excluded_disputed_fields": excluded,
        "fields": {
            field_id: {"n": len(levels), "median": median(levels)}
            for field_id, levels in sorted(values.items())
        },
        "cohorts": {
            cohort: {
                field_id: {"n": len(levels), "median": median(levels)}
                for field_id, levels in sorted(fields.items())
            }
            for cohort, fields in sorted(cohort_values.items())
        },
    }


def field_deviations(reviews: list[dict], calibration: dict | None = None) -> list[dict]:
    """Return bounded median deviations; n=2 has only 2/22 of full weight."""
    if calibration is None:
        if not CALIBRATION_PATH.is_file():
            return []
        calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    policy = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
    if calibration.get("taxonomy_version") != current_taxonomy_version():
        return []
    if calibration.get("source") not in policy["median_reference_confidence"]:
        return []
    shrinkage = float(calibration["shrinkage"])
    result = []
    for review in reviews:
        quality = review.get("quality_level")
        reference = calibration["fields"].get(review["field_id"])
        if quality is None or reference is None:
            continue
        n = int(reference["n"])
        if n <= 0:
            continue
        difference = quality - float(reference["median"])
        result.append({
            "field_id": review["field_id"], "quality_level": quality,
            "reference_median": reference["median"], "reference_n": n,
            "shrunk_deviation": round(difference * n / (n + shrinkage), 3),
        })
    return result


def median_adjustment(reviews: list[dict], *, calibration: dict | None = None) -> dict:
    """Compute a bounded pilot adjustment from comparably labeled fields."""
    if calibration is None:
        if not CALIBRATION_PATH.is_file():
            return {"status": "missing_reference", "delta": 0.0, "fields": []}
        calibration = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    policy = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
    if calibration.get("taxonomy_version") != current_taxonomy_version():
        return {"status": "stale_taxonomy", "delta": 0.0, "fields": []}
    confidence = policy["median_reference_confidence"].get(calibration.get("source"))
    if confidence is None:
        return {"status": "unapproved_source", "delta": 0.0, "fields": []}
    deviations = field_deviations(reviews, calibration)
    if not deviations:
        return {"status": "no_comparable_fields", "delta": 0.0, "fields": []}
    mean_difference = sum(item["shrunk_deviation"] for item in deviations) / len(deviations)
    weighted = mean_difference * policy["median_score_weight"] * confidence
    cap = policy["median_max_abs_adjustment"]
    return {
        "status": "pilot_adjustment", "source": calibration["source"],
        "delta": round(max(-cap, min(cap, weighted)), 3), "fields": deviations,
    }
