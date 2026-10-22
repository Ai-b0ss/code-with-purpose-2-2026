"""One fictional application through live HTTP, cloud scoring, SQLite and Sheets."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from backend import google_sheets


ROOT = Path(__file__).resolve().parent.parent
BASE_URL = os.getenv("GIM_TEST_BASE_URL", "http://127.0.0.1:8876")
APPLICATION_ID = os.getenv("GIM_QA_APPLICATION_ID", "qa-signed-cloud-google-20261022-01")
REPLIES = {
    "ai_availability": "Смогу приходить каждую субботу в течение семестра и во время сессии, по пять часов.",
    "ai_skills": "Помогал организовать историческую встречу: составил маршрут и распределил задачи.",
    "ai_motivation": "Хочу помогать посетителям на образовательных событиях ГИМ.",
    "ai_form_and_contact": "Для связи доступен указанный в анкете тестовый адрес электронной почты.",
    "ai_logistics": "Могу добираться до музея общественным транспортом примерно за 40 минут.",
}


def request(path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(BASE_URL + path, data=data,
                  headers={"Content-Type": "application/json"} if data else {},
                  method="POST" if data is not None else "GET")
    try:
        with urlopen(req, timeout=600) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get("detail", "")
        except (ValueError, TypeError):
            detail = ""
        safe_code = detail if isinstance(detail, str) and re.fullmatch(
            r"(?:evaluator|clarification)_[a-z_]+", detail
        ) else ""
        return exc.code, {"error_code": safe_code}


def sheet_id_count(application_id: str) -> int:
    spreadsheet_id = os.environ["GIM_GOOGLE_SPREADSHEET_ID"].strip()
    title = os.getenv("GIM_GOOGLE_SHEET_NAME", "Заявки API")
    root = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
    session = google_sheets._client()
    response = session.get(root + "/values/" + google_sheets._quoted(title) + "!A:A", timeout=30)
    values = google_sheets._json(response).get("values", [])
    return sum(bool(cells) and cells[0] == application_id for cells in values)


def main() -> None:
    load_dotenv(ROOT / ".env", override=False)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{8,80}", APPLICATION_ID):
        raise SystemExit("LIVE_FAIL invalid_qa_application_id")
    cases = json.loads((ROOT / "qa" / "four_cases.json").read_text(encoding="utf-8"))["cases"]
    original = dict(next(case["answers"] for case in cases if case["case_id"] == "CAND-01"))
    original.update({
        "application_id": APPLICATION_ID,
        "applicant_name": "Синтетическая заявка QA (вымышленная)",
        "email": "qa-signed-cloud@example.invalid",
        "vk_url": "",
    })
    status, health = request("/api/v1/health")
    if status != 200 or health.get("evaluation_mode") != "llm":
        raise SystemExit("LIVE_FAIL server_not_in_llm_mode")
    status, clarification = request("/api/v1/clarification", original)
    if status != 200:
        raise SystemExit(f"LIVE_FAIL clarification_http_{status}")
    questions = clarification.get("questions", [])
    signature = clarification.get("followup_signature", "")
    if not questions or len(signature) != 64:
        raise SystemExit("LIVE_FAIL missing_signed_questions")
    print("LIVE_QUESTIONS", len(questions), flush=True)
    payload = {
        **original,
        "followup_questions": [
            {key: question.get(key, "") for key in ("id", "category", "question", "reason")}
            for question in questions
        ],
        "followup_answers": {question["id"]: REPLIES[question["id"]] for question in questions},
        "followup_signature": signature,
    }
    status, _ = request("/api/v1/preview", {**payload, "motivation": original["motivation"] + " Дополнение."})
    if status != 422:
        raise SystemExit(f"LIVE_FAIL tampered_http_{status}")
    print("LIVE_TAMPER_REJECTED", flush=True)
    status, first = request("/api/v1/applications", payload)
    if status != 201:
        raise SystemExit(f"LIVE_FAIL save_http_{status} {first.get('error_code', '')}")
    expected_model = os.getenv("GIM_QA_EXPECT_MODEL", "").strip()
    if expected_model and first["evaluation"].get("provider_model") != expected_model:
        raise SystemExit("LIVE_FAIL unexpected_provider_model")
    sync = first["sheet_sync"]
    if sync["status"] == "failed":
        print("LIVE_SYNC_RETRY", sync.get("error"), flush=True)
        status, retried = request(f"/api/v1/applications/{APPLICATION_ID}/sync", {})
        if status != 200:
            raise SystemExit(f"LIVE_FAIL retry_http_{status}")
        sync = retried["sheet_sync"]
    status, second = request("/api/v1/applications", payload)
    if status != 201 or second.get("created") is not False:
        raise SystemExit("LIVE_FAIL duplicate_created")
    status, saved = request(f"/api/v1/applications/{APPLICATION_ID}")
    if status != 200 or saved["application_id"] != APPLICATION_ID:
        raise SystemExit("LIVE_FAIL sqlite_record_missing")
    if sync["status"] != "synced" or not sync.get("row_url"):
        raise SystemExit(f"LIVE_FAIL google_{sync['status']}")
    matches = sheet_id_count(APPLICATION_ID)
    if matches != 1:
        raise SystemExit(f"LIVE_FAIL sheet_id_count_{matches}")
    print("LIVE_PASS", "created", first["created"], "repeat_created", second["created"],
          "score_status", first["evaluation"].get("score_status"),
          "model", first["evaluation"].get("provider_model"),
          "sqlite", 1, "sheet_ids", matches, flush=True)
    print("SYNTHETIC_ROW", sync["row_url"], flush=True)


if __name__ == "__main__":
    main()
