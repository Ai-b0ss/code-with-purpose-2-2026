"""Cloud HTTP handshake for signed questions using a fictional QA application."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from backend.app import app


ROOT = Path(__file__).resolve().parent.parent
REPLIES = {
    "ai_availability": "Смогу приходить каждую субботу в течение семестра и во время сессии, по пять часов.",
    "ai_skills": "Помогал организовать историческую встречу: составил маршрут и распределил задачи.",
    "ai_motivation": "Интересуюсь историей музея и хочу помогать посетителям на образовательных событиях.",
    "ai_form_and_contact": "Для связи доступен указанный в анкете адрес электронной почты.",
    "ai_logistics": "Могу добираться до музея общественным транспортом примерно за 40 минут.",
}


def main() -> None:
    cases = json.loads((ROOT / "qa" / "four_cases.json").read_text(encoding="utf-8"))["cases"]
    original = next(case["answers"] for case in cases if case["case_id"] == "CAND-01")
    os.environ["GIM_EVALUATION_MODE"] = "llm"
    with TemporaryDirectory() as directory:
        os.environ["GIM_DB_PATH"] = str(Path(directory) / "qa.sqlite3")
        with TestClient(app) as client:
            clarification = client.post("/api/v1/clarification", json=original)
            if clarification.status_code != 200:
                print("SIGNED_FOLLOWUP_FAIL", "clarification_http", clarification.status_code)
                raise SystemExit(1)
            response = clarification.json()
            questions = response.get("questions", [])
            signature = response.get("followup_signature", "")
            if not questions or len(signature) != 64:
                print("SIGNED_FOLLOWUP_FAIL", "missing_signed_questions", len(questions))
                raise SystemExit(1)
            payload = {
                **original,
                "followup_questions": [
                    {key: question.get(key, "") for key in ("id", "category", "question", "reason")}
                    for question in questions
                ],
                "followup_answers": {question["id"]: REPLIES[question["id"]] for question in questions},
                "followup_signature": signature,
            }
            tampered = {**payload, "motivation": original["motivation"] + " Дополнение."}
            rejected = client.post("/api/v1/preview", json=tampered)
            accepted = client.post("/api/v1/preview", json=payload)
            result = accepted.json().get("evaluation", {}) if accepted.status_code == 200 else {}
            ok = (rejected.status_code == 422 and accepted.status_code == 200
                  and result.get("evaluation_mode") == "llm")
            print("SIGNED_FOLLOWUP_" + ("PASS" if ok else "FAIL"),
                  "questions", len(questions), "tampered_http", rejected.status_code,
                  "preview_http", accepted.status_code,
                  "model", result.get("provider_model"),
                  "score_status", result.get("score_status"))
            raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
