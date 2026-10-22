"""Create preliminary, deidentified per-field LLM labels from unique cohorts.

The output is a review queue, not a calibrated scoring file.  A curator must
approve or correct these labels before ``build_field_calibration.py`` uses them.
The script never writes raw answers, names, contacts, or model quotations.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import load_workbook

from backend.evaluator import EvaluationError, adapter_from_env
from backend.field_quality import TAXONOMY, review_fields


ROOT = Path(__file__).resolve().parent.parent
DATASET_CONFIG = json.loads((ROOT / "knowledge" / "historical_dataset.json").read_text(encoding="utf-8"))
SOURCE_FILES = tuple(ROOT / relative for relative in DATASET_CONFIG["source_files"])
FIELD_HEADERS = DATASET_CONFIG["field_headers_contain"]


def historical_answers(row: tuple, field_indexes: dict[str, int | None]) -> dict:
    answers = {
        field: str(row[index] or "").strip()
        for field, index in field_indexes.items() if index is not None
    }
    for source, target in DATASET_CONFIG["nested_fields"].items():
        if source in answers:
            parent, child = target.split(".", 1)
            answers.setdefault(parent, {})[child] = answers.pop(source)
    return answers


def historical_records():
    seen = set()
    for path in SOURCE_FILES:
        with closing(load_workbook(path, read_only=True, data_only=True)) as workbook:
            for sheet in workbook:
                rows = iter(sheet.values)
                headers = [str(x or "").strip() for x in next(rows)]
                score_index = next(i for i, text in enumerate(headers)
                                   if DATASET_CONFIG["score_header_contains"] in text)
                field_indexes = {
                    field: next((i for i, text in enumerate(headers) if marker in text), None)
                    for field, marker in FIELD_HEADERS.items()
                }
                for row in rows:
                    score = row[score_index] if score_index < len(row) else None
                    if not isinstance(score, (int, float)) or score not in (0, 1, 2, 3):
                        continue
                    answers = historical_answers(row, field_indexes)
                    identity = json.dumps([path.name, sheet.title, answers, score], ensure_ascii=False, sort_keys=True)
                    row_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                    if row_hash in seen:
                        continue
                    seen.add(row_hash)
                    yield {"source_row_hash": row_hash, "cohort": path.stem, "historical_score": int(score), "answers": answers}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "historical_field_review_queue.jsonl")
    parser.add_argument("--limit", type=int,
                        help="Optional maximum number of historical applications to review")
    parser.add_argument("--resume", action="store_true", help="Append only rows absent from an existing output")
    parser.add_argument("--continue-on-error", action="store_true", help="Record safe error codes and continue")
    parser.add_argument("--salvage-errors", action="store_true", help="Keep valid field labels from rows with an invalid batch")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    total_label = args.limit if args.limit is not None else "all"
    endpoint = urlsplit(os.getenv("GIM_LLM_ENDPOINT", ""))
    local_endpoint = endpoint.scheme == "http" and endpoint.hostname in {"127.0.0.1", "localhost", "::1"}
    if not local_endpoint and not os.getenv("GIM_LLM_API_KEY"):
        raise SystemExit("GIM_LLM_API_KEY is required for cloud classification")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    error_path = args.out.with_suffix(".errors.jsonl")
    existing = set()
    if args.resume and args.out.is_file():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            previous = json.loads(line)
            if previous.get("taxonomy_version") != TAXONOMY["version"]:
                raise ValueError("Cannot resume a queue created with another taxonomy version")
            existing.add(previous["source_row_hash"])
    processed = 0
    succeeded = 0
    failed = 0
    mode = "a" if args.resume else "w"
    with args.out.open(mode, encoding="utf-8") as target, error_path.open(mode, encoding="utf-8") as errors:
        for record in historical_records():
            if args.limit is not None and processed >= args.limit:
                break
            processed += 1
            if record["source_row_hash"] in existing:
                continue
            adapter = adapter_from_env()
            model_error_fields = []
            try:
                reviews = review_fields(record["answers"], adapter)
            except EvaluationError as exc:
                if not (args.continue_on_error or args.salvage_errors):
                    raise
                reviews = []
                if args.salvage_errors:
                    targets = []
                    for field_id, value in record["answers"].items():
                        if isinstance(value, dict):
                            targets.extend((f"{field_id}.{subkey}", {field_id: {subkey: item}})
                                           for subkey, item in value.items())
                        else:
                            targets.append((field_id, {field_id: value}))
                    for field_id, payload in targets:
                        try:
                            reviews.extend(review_fields(payload, adapter))
                        except EvaluationError:
                            model_error_fields.append(field_id)
                            reviews.append({
                                "field_id": field_id, "category": "unclassified_model_error",
                                "quality_level": None,
                            })
                if not reviews:
                    errors.write(json.dumps({
                        "source_row_hash": record["source_row_hash"],
                        "cohort": record["cohort"], "error_code": exc.code,
                    }, ensure_ascii=False) + "\n")
                    errors.flush()
                    failed += 1
                    print("failed", processed, "of", total_label, "code", exc.code, flush=True)
                    continue
            result = {
                "source_row_hash": record["source_row_hash"],
                "taxonomy_version": TAXONOMY["version"],
                "cohort": record["cohort"],
                "historical_score": record["historical_score"],
                "provider_model": getattr(adapter, "last_model", None),
                "provider_models_attempted": list(getattr(adapter, "attempted_models", [])),
                "provider_failures": list(getattr(adapter, "failed_attempts", [])),
                "fields": [
                    {"field_id": review["field_id"], "category": review["category"],
                     "quality_level": review["quality_level"]}
                    for review in reviews
                ],
                "review_status": ("needs_curator_approval_partial" if model_error_fields
                                  else "needs_curator_approval"),
            }
            if model_error_fields:
                result["model_error_fields"] = model_error_fields
            target.write(json.dumps(result, ensure_ascii=False) + "\n")
            target.flush()
            succeeded += 1
            print("classified", processed, "of", total_label,
                  "partial_fields", len(model_error_fields), flush=True)
    print("review_queue_rows", succeeded, "failed_rows", failed, "processed_rows", processed)


if __name__ == "__main__":
    main()
