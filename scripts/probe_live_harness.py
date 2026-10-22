"""Exercise the production LangGraph pipeline on one fictional application."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from backend.evaluator import EvaluationError, evaluate
from scripts.check_reformboss import sample_candidate


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    if not os.getenv("GIM_LLM_ENDPOINT") or not os.getenv("GIM_LLM_MODEL"):
        raise SystemExit("Live model endpoint and name are required")
    try:
        result = evaluate(sample_candidate(), mode="llm")
    except EvaluationError as exc:
        print("HARNESS_FAIL", exc.code, *exc.details[:8])
        raise SystemExit(1) from None
    ok = (
        result.get("evaluation_mode") == "llm"
        and isinstance(result.get("field_reviews"), list)
        and result.get("provider_model") is not None
        and result.get("score_status") is not None
        and result.get("screening_outcome") is not None
    )
    print("HARNESS_" + ("PASS" if ok else "FAIL"),
          "model", result.get("provider_model"),
          "attempted", ",".join(result.get("provider_models_attempted", [])),
          "score_status", result.get("score_status"),
          "median_status", result.get("field_median_status"),
          "fields", len(result.get("field_reviews", [])))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
