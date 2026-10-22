"""Inspect live, source-linked questions for fictional QA applications."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app import ApplicationInput
from backend.clarification_agent import questions_before_verdict
from backend.evaluator import EvaluationError, adapter_from_env
from backend.knowledge_graph import GRAPH_PATH


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", help="Case ID from qa/four_cases.json")
    args = parser.parse_args()
    cases = json.loads((ROOT / "qa" / "four_cases.json").read_text(encoding="utf-8"))["cases"]
    selected = [case for case in cases if not args.case or case["case_id"] in args.case]
    if not selected or args.case and set(args.case) != {case["case_id"] for case in selected}:
        parser.error("Unknown or missing case ID")
    facts = {fact["id"]: fact for fact in
             json.loads(GRAPH_PATH.read_text(encoding="utf-8"))["triples"]}
    errors = 0
    for case in selected:
        adapter = adapter_from_env()
        try:
            result = questions_before_verdict(ApplicationInput.model_validate(case["answers"]),
                                              mode="llm", adapter=adapter)
        except EvaluationError as exc:
            print(case["case_id"], "ERROR", exc.code, flush=True)
            errors += 1
            continue
        print(case["case_id"], "questions", len(result["questions"]),
              "discarded", result["discarded_question_codes"],
              "provider", getattr(adapter, "last_model", None),
              "attempts", getattr(adapter, "attempted_models", []),
              "failures", getattr(adapter, "failed_attempts", []), flush=True)
        for question in result["questions"]:
            print(json.dumps({
                "question": question["question"],
                "criterion": question["id"],
                "trigger_field_ids": question["trigger_field_ids"],
                "sources": [{"id": fact_id, "object": facts[fact_id]["object"]}
                            for fact_id in question["source_fact_ids"]],
            }, ensure_ascii=False), flush=True)
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
