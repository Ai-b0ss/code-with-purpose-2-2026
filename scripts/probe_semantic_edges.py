"""Live semantic contract probe with fictional answers and no personal data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.evaluator import EvaluationError, adapter_from_env
from backend.field_quality import review_fields


CASES = Path(__file__).resolve().parent.parent / "qa" / "semantic_edges.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", help="Run only named synthetic cases")
    args = parser.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.case:
        cases = [case for case in cases if case["id"] in args.case]
        if {case["id"] for case in cases} != set(args.case):
            parser.error("Unknown case ID")
    adapter = adapter_from_env()
    failed = 0
    for case in cases:
        try:
            review = review_fields({case["field_id"]: case["answer"]}, adapter)[0]
            signals = {review["category"], *review["signals"]}
            ok = (
                (case["category"] is None or review["category"] == case["category"])
                and set(case["required_signals"]) <= signals
                and not set(case["forbidden_signals"]) & signals
            )
            print(case["id"], "PASS" if ok else "FAIL", review["category"],
                  sorted(review["signals"]),
                  "model", getattr(adapter, "last_model", None), flush=True)
        except EvaluationError as exc:
            ok = False
            print(case["id"], "ERROR", exc.code, flush=True)
        failed += not ok
    print("semantic_edge_cases", len(cases), "failed", failed)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
