"""Local HTTP integration check for a running demo server.

Can be run standalone against a running server (default http://127.0.0.1:8765 or custom URL/port):
    python tests/smoke_http.py http://127.0.0.1:8765
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("GIM_TEST_URL") or (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765")


def request(path: str, payload: dict | None = None, base_url: str = BASE) -> tuple[int, bytes]:
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {} if raw is None else {"Content-Type": "application/json"}
    if raw is None and path.startswith("/api/v1/") and path != "/api/v1/health":
        headers["Authorization"] = "Bearer " + os.environ.get("GIM_COORDINATOR_TOKEN", "")
    req = urllib.request.Request(
        base_url + path,
        data=raw,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        return response.status, response.read()


def run_smoke(base_url: str = BASE) -> None:
    status, body = request("/", base_url=base_url)
    assert status == 200 and "Музейный компас" in body.decode("utf-8")

    status, body = request("/api/v1/health", base_url=base_url)
    assert json.loads(body)["evaluation_mode"] == "rules_demo"

    app_id = os.environ.get("GIM_SMOKE_APP_ID") or f"smoke-integration-{int(time.time() * 1000)}"
    entry = {
        "application_id": app_id,
        "applicant_name": "Тестовый кандидат",
        "birth_date": "2000-01-01", "email": "synthetic@example.test",
            "vk_url": "https://vk.com/synthetic", "university": "Тестовый вуз",
            "faculty": "История", "course": "2", "scientific_interests": "История",
            "foreign_languages": ["Нет B1+"], "hobbies": "Чтение", "source": "Другое",
            "age_over_18": True,
        "consent_personal_data": True,
        "motivation": "Мне интересно создавать исторический медиаконтент и помогать музею.",
        "experience": "Делал видео для университетского кружка.",
        "expectations": "Хочу научиться работать с архивными историями",
        "availability_hours_month": 16,
        "other_commitments": "Учёба",
        "desired_direction": "media",
        "followup_answer": "Готов уделять вечера и выходные.",
    }

    status, body = request("/api/v1/clarification", entry, base_url=base_url)
    assert status == 200

    for i in range(2):
        status, body = request("/api/v1/applications", entry, base_url=base_url)
        result = json.loads(body)
        assert status == 201
        assert result["application_id"] == entry["application_id"]
        assert result["created"] is (i == 0)
        assert "evaluation" not in result
        assert result["status"] == "registered"

    status, body = request("/api/v1/applications", base_url=base_url)
    matching = [v for v in json.loads(body)["items"] if v["application_id"] == entry["application_id"]]
    assert len(matching) == 1, "duplicate detected"

    status, body = request("/api/v1/export.csv", base_url=base_url)
    assert status == 200 and "Тестовый кандидат" in body.decode("utf-8-sig")

    # Verify invalid request returns 422 validation error
    invalid_entry = {"applicant_name": ""}
    raw_invalid = json.dumps(invalid_entry).encode("utf-8")
    invalid_req = urllib.request.Request(
        base_url + "/api/v1/applications",
        data=raw_invalid,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(invalid_req, timeout=8) as resp:
            raise AssertionError(f"Expected HTTP 422 validation error, got {resp.status}")
    except urllib.error.HTTPError as exc:
        assert exc.code == 422, f"Expected HTTP 422, got {exc.code}"
        err_body = json.loads(exc.read().decode("utf-8"))
        assert "detail" in err_body, "Validation error response missing 'detail'"

    print("HTTP_SMOKE_OK home health clarification submit repeat-submit list csv validation-error")


if __name__ == "__main__":
    run_smoke()

