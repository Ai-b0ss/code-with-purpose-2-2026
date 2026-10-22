"""Measure numeric stability of repeated full live evaluations of a fictional form."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app import ApplicationInput
from backend.evaluator import EvaluationError, evaluate
from scripts.check_reformboss import sample_candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--case", help="Case ID from qa/four_cases.json; default: simple fictional probe")
    args = parser.parse_args()
    if not 2 <= args.runs <= 10:
        parser.error("--runs must be 2..10")
    candidate = sample_candidate()
    if args.case:
        path = Path(__file__).resolve().parent.parent / "qa" / "four_cases.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        found = next((case for case in cases if case["case_id"] == args.case), None)
        if found is None:
            parser.error("Unknown case ID")
        candidate = ApplicationInput.model_validate(found["answers"])
    outputs = []
    for index in range(args.runs):
        try:
            result = evaluate(candidate, mode="llm")
        except EvaluationError as exc:
            print("run", index + 1, "ERROR", exc.code, *exc.details[:4], flush=True)
            raise SystemExit(1) from None
        labels = sorted((item["field_id"], item["category"], tuple(sorted(item.get("signals", []))))
                        for item in result["field_reviews"])
        scorecard = [(item["criterion"], item["level"]) for item in result["scorecard"]]
        row = {"score": result["score"], "score_detailed": result.get("score_detailed"),
               "scorecard": scorecard, "labels": labels,
               "provider_model": result.get("provider_model"),
               "score_raw": result.get("score_raw"),
               "median_delta": result.get("field_median_delta"),
               "content_delta": result.get("content_delta"),
               "content_signals": sorted((item["field_id"], item["signal"])
                                         for item in result.get("content_signal_contributions", []))}
        outputs.append(row)
        print("run", index + 1, "score", row["score"],
              "detailed", row["score_detailed"], "model", row["provider_model"], flush=True)
    numeric_equal = all(
        (item["score"], item["score_detailed"], item["scorecard"])
        == (outputs[0]["score"], outputs[0]["score_detailed"], outputs[0]["scorecard"])
        for item in outputs[1:]
    )
    labels_equal = all(item["labels"] == outputs[0]["labels"] for item in outputs[1:])
    print(json.dumps({"runs": args.runs, "numeric_equal": numeric_equal,
                      "labels_equal": labels_equal}, ensure_ascii=False))
    for index, item in enumerate(outputs[1:], start=2):
        if item == outputs[0]:
            continue
        baseline_labels = {field: (category, signals) for field, category, signals in outputs[0]["labels"]}
        current_labels = {field: (category, signals) for field, category, signals in item["labels"]}
        changed = {
            field: {"first": baseline_labels.get(field), "current": current_labels.get(field)}
            for field in sorted(baseline_labels.keys() | current_labels.keys())
            if baseline_labels.get(field) != current_labels.get(field)
        }
        print(json.dumps({"compared_run": index,
                          "first_scorecard": outputs[0]["scorecard"],
                          "current_scorecard": item["scorecard"],
                          "first_components": {key: outputs[0][key] for key in
                                               ("score_raw", "median_delta", "content_delta", "content_signals")},
                          "current_components": {key: item[key] for key in
                                                 ("score_raw", "median_delta", "content_delta", "content_signals")},
                          "changed_labels": changed}, ensure_ascii=False))
    raise SystemExit(0 if numeric_equal else 1)


if __name__ == "__main__":
    main()
