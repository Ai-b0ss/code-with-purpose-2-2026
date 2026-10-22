"""Review historical field labels locally without exporting questionnaire text.

The source XLSX is read only to show the answer in the curator's terminal.
Approved output contains hashes, cohort IDs and field labels only.  Historical
outcomes are deliberately hidden from the reviewer to reduce label leakage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from backend.field_quality import TAXONOMY, quality_level_for
from scripts.classify_historical_fields import ROOT, historical_records


CATEGORIES = tuple(TAXONOMY["categories"])
DEFAULT_QUEUE = ROOT / "data" / "historical_field_review_queue_v9.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "curator_reviewed_fields.jsonl"


def prioritize_audit_disagreements(rows: list[dict], audit_rows: list[dict]) -> list[dict]:
    """Show disputed provisional rows first; do not treat a second model as approval."""
    by_hash = {row["source_row_hash"]: row for row in rows}
    priority: set[str] = set()
    for item in audit_rows:
        row_hash = item["source_row_hash"]
        if row_hash not in by_hash:
            raise ValueError("Audit row is absent from provisional queue")
        if not any(field["field_id"] == item["field_id"]
                   and field["category"] == item["provisional_category"]
                   for field in by_hash[row_hash]["fields"]):
            raise ValueError("Audit label differs from provisional queue")
        if item.get("disagrees") or item.get("error_code"):
            priority.add(row_hash)
    return sorted(rows, key=lambda row: row["source_row_hash"] not in priority)


def flatten_answers(answers: dict) -> dict[str, str]:
    result = {}
    for field_id, value in answers.items():
        if isinstance(value, dict):
            result.update({f"{field_id}.{subkey}": str(item or "")
                           for subkey, item in value.items()})
        else:
            result[field_id] = str(value or "")
    return result


def curate_row(queue_row: dict, source_row: dict,
               ask: Callable[[str], str] = input,
               show: Callable[[str], None] = print) -> dict | str:
    """Return an approved, deidentified row or a skip/quit command."""
    if queue_row["source_row_hash"] != source_row["source_row_hash"]:
        raise ValueError("Source identity mismatch")
    if not str(queue_row.get("review_status", "")).startswith("needs_curator_approval"):
        raise ValueError("Input is not a provisional review queue")
    answers = flatten_answers(source_row["answers"])
    output_fields = []
    same_taxonomy = queue_row.get("taxonomy_version") == TAXONOMY["version"]
    show(f"Record {queue_row['source_row_hash'][:12]} | cohort {queue_row['cohort']}")
    if not same_taxonomy:
        show("Old taxonomy proposal: choose a category explicitly for every field.")
    show("Category codes: " + ", ".join(f"{i + 1}={name}" for i, name in enumerate(CATEGORIES)))
    for proposed in queue_row["fields"]:
        field_id = proposed["field_id"]
        # One early salvage run named the nested container rather than its
        # only answer.  Resolve it without altering the historical row hash.
        if field_id not in answers and field_id == "followup_answers":
            nested = [key for key in answers if key.startswith("followup_answers.")]
            if len(nested) == 1:
                field_id = nested[0]
        if field_id not in answers:
            raise ValueError(f"Field absent from source: {field_id}")
        show(f"\n{field_id}\n{answers[field_id]}")
        current = proposed.get("category")
        show(f"Proposed: {current} / {proposed.get('quality_level')}")
        while True:
            choice = ask("Category number/name [Enter=accept, s=skip record, q=quit]: ").strip()
            if choice.casefold() == "s":
                return "skip"
            if choice.casefold() == "q":
                return "quit"
            if not choice and not same_taxonomy:
                show("Choose a category from the current taxonomy; Enter cannot accept an old proposal.")
                continue
            category = current if not choice else (
                CATEGORIES[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(CATEGORIES)
                else choice
            )
            if category in CATEGORIES:
                break
            show("Choose a valid category; model errors cannot be approved unchanged.")
        default_level = quality_level_for(field_id, category)
        level = default_level
        if default_level is not None:
            while True:
                raw_level = ask(f"Quality 0..3 [Enter={default_level}]: ").strip()
                if not raw_level:
                    break
                if raw_level in {"0", "1", "2", "3"}:
                    level = int(raw_level)
                    break
                show("Enter 0, 1, 2 or 3.")
        output_fields.append({"field_id": field_id, "category": category,
                              "quality_level": level})
    return {"source_row_hash": queue_row["source_row_hash"],
            "taxonomy_version": TAXONOMY["version"],
            "cohort": queue_row["cohort"], "fields": output_fields}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0, help="Maximum new rows; 0 means all")
    parser.add_argument("--only-partial", action="store_true")
    parser.add_argument("--row-hash", help="Review only a matching source hash prefix")
    parser.add_argument("--priority-audit", type=Path, action="append",
                        help="Show rows with model disagreement or error first; repeat for multiple audits")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    if args.queue.resolve() == args.out.resolve():
        parser.error("--out must differ from --queue")
    rows = [json.loads(line) for line in args.queue.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if len({row["source_row_hash"] for row in rows}) != len(rows):
        raise ValueError("Duplicate queue rows")
    if args.priority_audit:
        audit_rows = [json.loads(line) for path in args.priority_audit
                      for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = prioritize_audit_disagreements(rows, audit_rows)
    sources = {row["source_row_hash"]: row for row in historical_records()}
    existing = set()
    if args.out.exists():
        existing = {json.loads(line)["source_row_hash"]
                    for line in args.out.read_text(encoding="utf-8").splitlines() if line.strip()}
    reviewed = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as target:
        for queue_row in rows:
            row_hash = queue_row["source_row_hash"]
            if row_hash in existing or (args.row_hash and not row_hash.startswith(args.row_hash)):
                continue
            if args.only_partial and queue_row.get("review_status") != "needs_curator_approval_partial":
                continue
            if row_hash not in sources:
                raise ValueError(f"Historical source missing for {row_hash[:12]}")
            approved = curate_row(queue_row, sources[row_hash])
            if approved == "skip":
                print("Row skipped; no partial row was saved.")
                continue
            if approved == "quit":
                print("Review stopped; no partial row was saved.")
                break
            target.write(json.dumps(approved, ensure_ascii=False) + "\n")
            target.flush()
            reviewed += 1
            print("Approved", row_hash[:12], "total_new", reviewed)
            if args.limit and reviewed >= args.limit:
                break
    print("newly_approved_rows", reviewed)


if __name__ == "__main__":
    main()
