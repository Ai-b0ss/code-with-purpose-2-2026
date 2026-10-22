"""Probe configured cloud models with a tiny JSON request, without answers."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from backend.evaluator import EvaluationError
from backend.langchain_adapter import FailoverChatAdapter, LangChainChatAdapter


ROOT = Path(__file__).resolve().parent.parent
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}


def main() -> None:
    endpoint = os.environ["GIM_LLM_ENDPOINT"]
    catalog = json.loads((ROOT / "knowledge" / "provider_models.json").read_text(encoding="utf-8"))
    models = list(dict.fromkeys([os.environ["GIM_LLM_MODEL"],
                                 *catalog.get(urlsplit(endpoint).hostname, [])]))
    for name in models:
        adapter = LangChainChatAdapter(
            endpoint=endpoint, model=name, api_key=os.environ["GIM_LLM_API_KEY"],
            timeout=45, json_mode=os.getenv("GIM_LLM_JSON_MODE", "1") == "1",
        )
        try:
            parsed = FailoverChatAdapter(primary=adapter).complete_validated(
                system="Return only one JSON object without Markdown or explanation: {\"ok\": true}.",
                user="Connection check. No applicant data. Return only {\"ok\": true}.",
                schema=SCHEMA,
                validator=json.loads,
            )
            code = "ok" if parsed == {"ok": True} else "invalid_model_json"
        except EvaluationError as exc:
            code = exc.code
        except (json.JSONDecodeError, ValueError, TypeError):
            code = "invalid_model_json"
        print(name, code, flush=True)


if __name__ == "__main__":
    main()
