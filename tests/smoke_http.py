"""Local HTTP integration check for a running demo server on port 8765."""
import json
import urllib.request

BASE = "http://127.0.0.1:8765"

def request(path, payload=None):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=raw,
        headers={} if raw is None else {"Content-Type":"application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        return response.status, response.read()

status, body = request("/")
assert status == 200 and "Музейный компас" in body.decode("utf-8")
status, body = request("/api/v1/health")
assert json.loads(body)["evaluation_mode"] == "rules_demo"
entry = {
    "application_id": "smoke-integration-001",
    "applicant_name": "Тестовый кандидат",
    "age_over_18": True,
    "motivation": "Мне интересно создавать исторический медиаконтент и помогать музею.",
    "experience": "Делал видео для университетского кружка.",
    "expectations": "Хочу научиться работать с архивными историями",
    "availability_hours_month": 16,
    "other_commitments": "Учёба",
    "desired_direction": "media",
    "followup_answer": "Готов уделять вечера и выходные.",
}
status, body = request("/api/v1/clarification", entry)
assert status == 200
for i in range(2):
    status, body = request("/api/v1/applications", entry)
    result = json.loads(body)
    assert status == 201
    assert result["application_id"] == entry["application_id"]
    assert result["created"] is (i == 0)
    assert result["evaluation"]["recommended_direction"] == "media"
status, body = request("/api/v1/applications")
matching = [v for v in json.loads(body)["items"] if v["application_id"] == entry["application_id"]]
assert len(matching) == 1, "duplicate detected"
status, body = request("/api/v1/export.csv")
assert status == 200 and "Тестовый кандидат" in body.decode("utf-8-sig")
print("HTTP_SMOKE_OK home health clarification submit repeat-submit list csv")
