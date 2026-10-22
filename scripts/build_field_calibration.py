"""Build median references from curator-reviewed, deidentified field labels.

Input JSONL: one object per historical application, with a ``fields`` array
containing ``field_id`` and curator-approved ``quality_level`` 0..3.
The source must not contain names, contacts, birth dates, or raw answers.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.field_calibration import CALIBRATION_PATH, build_calibration
from backend.field_quality import TAXONOMY, quality_level_for
from scripts.classify_historical_fields import historical_records
from scripts.audit_historical_field_labels import disputed_field_keys


def require_full_cohort(rows: list[dict], expected: set[str]) -> None:
    approved = {row.get("source_row_hash") for row in rows}
    if approved != expected:
        raise ValueError(
            f"Runtime calibration requires the full reviewed cohort: "
            f"approved={len(approved & expected)}, expected={len(expected)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviewed_jsonl", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--provisional-llm-labels", action="store_true")
    parser.add_argument("--audit-disagreements", type=Path,
                        help="Exclude independently disputed provisional fields until curator review")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.reviewed_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.audit_disagreements and not args.provisional_llm_labels:
        parser.error("--audit-disagreements applies only to provisional LLM labels")
    disputed = set()
    if args.audit_disagreements:
        audit_rows = [json.loads(line) for line in args.audit_disagreements.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
        disputed = disputed_field_keys(rows, audit_rows)
    allowed_top = {"fields", "cohort", "source_row_hash", "taxonomy_version"}
    if args.provisional_llm_labels:
        allowed_top |= {"historical_score", "review_status", "model_error_fields",
                        "provider_model", "provider_models_attempted", "provider_failures"}
    for row in rows:
        if row.get("taxonomy_version") != TAXONOMY["version"]:
            raise ValueError("Field labels use a stale or unknown taxonomy version")
        if set(row) - allowed_top:
            raise ValueError("Input contains unexpected keys; use only deidentified field labels")
        if args.provisional_llm_labels and row.get("review_status") not in {
            "needs_curator_approval", "needs_curator_approval_partial"
        }:
            raise ValueError("Provisional input must retain its unreviewed status")
        for field in row.get("fields", []):
            permitted = {"field_id", "quality_level", "category"}
            if set(field) - permitted:
                raise ValueError("Field labels must not contain raw answers or identity")
            category = field.get("category")
            if category not in TAXONOMY["categories"] and not (
                args.provisional_llm_labels and category == "unclassified_model_error"
            ):
                raise ValueError("Field category must be an approved taxonomy value")
            level = field.get("quality_level")
            if category == "unclassified_model_error" and level is not None:
                raise ValueError("Model-error fields cannot have a quality level")
            if level is not None and (not isinstance(level, int) or isinstance(level, bool)
                                      or not 0 <= level <= 3):
                raise ValueError("Field quality must be null or an integer from 0 to 3")
            if level is not None and quality_level_for(field.get("field_id", ""), category) is None:
                raise ValueError("A non-quality field category cannot have a numeric quality")
    calibration = build_calibration(
        rows,
        source=("llm_historical_fields" if args.provisional_llm_labels
                else "curator_reviewed_field_labels"),
        exclude_field_keys=disputed,
    )
    destination = args.out or (
        CALIBRATION_PATH.parent.parent / "data" / "field_calibration_preview.json"
        if args.provisional_llm_labels else CALIBRATION_PATH
    )
    if destination.resolve() == CALIBRATION_PATH.resolve():
        if args.provisional_llm_labels:
            raise ValueError("Unreviewed model labels cannot be installed as runtime calibration")
        expected = {row["source_row_hash"] for row in historical_records()}
        require_full_cohort(rows, expected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(calibration, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("input_rows", len(rows), "source", calibration["source"],
          "calibrated_fields", len(calibration["fields"]),
          "excluded_disputed_fields", calibration["excluded_disputed_fields"], "out", destination)


if __name__ == "__main__":
    main()
