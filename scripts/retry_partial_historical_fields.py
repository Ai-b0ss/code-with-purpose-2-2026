"""Retry only failed historical field labels without exporting answer text.

The output remains an unreviewed model queue. Source answers are read from the
local XLSX files and sent only to the configured model endpoint.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.evaluator import EvaluationError, adapter_from_env
from backend.field_quality import review_fields
from scripts.classify_historical_fields import ROOT, historical_records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path,
                        default=ROOT / "data" / "historical_field_review_queue_v7.jsonl")
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "historical_field_review_queue_retry.jsonl")
    args = parser.parse_args()
    if args.source.resolve() == args.out.resolve():
        raise SystemExit("Choose a separate output path for the repaired queue")
    source = {row["source_row_hash"]: row for row in historical_records()}
    rows = [json.loads(line) for line in args.source.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if len(rows) != len(source) or {row["source_row_hash"] for row in rows} != set(source):
        raise ValueError("Historical label queue does not match current source workbooks")
    adapter = adapter_from_env()
    repaired = 0
    remaining = 0
    output = []
    for row in rows:
        row = dict(row)
        failures = []
        fields = list(row["fields"])
        for field_id in row.get("model_error_fields", []):
            answer = source[row["source_row_hash"]]["answers"].get(field_id)
            if answer is None:
                raise ValueError("Failed field is absent from its source row")
            try:
                reviews = review_fields({field_id: answer}, adapter)
                if not reviews or any(review["category"] == "unclassified_model_error"
                                      for review in reviews):
                    raise EvaluationError("invalid_model_json")
            except EvaluationError as error:
                failures.append(field_id)
                remaining += 1
                print("still_unclassified", field_id, error.code, flush=True)
                continue
            fields = [field for field in fields if field["field_id"] != field_id]
            fields.extend({"field_id": review["field_id"],
                           "category": review["category"],
                           "quality_level": review["quality_level"]}
                          for review in reviews)
            repaired += 1
            print("reclassified", field_id, flush=True)
        row["fields"] = fields
        row["review_status"] = ("needs_curator_approval_partial" if failures
                                else "needs_curator_approval")
        if failures:
            row["model_error_fields"] = failures
        else:
            row.pop("model_error_fields", None)
        output.append(row)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in output) + "\n",
                        encoding="utf-8")
    print("applications", len(output), "reclassified_fields", repaired,
          "remaining_model_errors", remaining, "out", args.out)


if __name__ == "__main__":
    main()
