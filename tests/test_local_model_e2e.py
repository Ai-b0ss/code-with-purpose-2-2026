"""Optional live check: student form -> Neo4j -> local model -> curator view."""
import os

import pytest
from fastapi.testclient import TestClient

from backend.app import app


@pytest.mark.skipif(os.getenv("GIM_TEST_LOCAL_MODEL") != "1",
                    reason="Requires isolated Neo4j and Ollama with qwen3:14b")
def test_live_local_model_student_and_curator(monkeypatch, tmp_path):
    monkeypatch.delenv("GIM_DATABASE_URL", raising=False)
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "contacts.sqlite3"))
    monkeypatch.setenv("GIM_COORDINATOR_TOKEN", "synthetic-test-curator-token")
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("GIM_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions")
    monkeypatch.setenv("GIM_LLM_MODEL", "qwen3:14b")
    monkeypatch.setenv("GIM_LLM_TIMEOUT", "300")
    client = TestClient(app)
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    values = {
        "consent_personal_data": "true", "applicant_name": "Synthetic Local Candidate",
        "email": "synthetic-local@example.test", "vk_url": "https://vk.com/synthetic_local",
        "birth_date": "1998-01-01", "status": "working",
        "scientific_interests": "Museum education", "experience": "I organized two educational games",
        "foreign_languages": "[]", "hobbies": "History", "source": "Museum website",
        "motivation": "I want to create a museum quest and help visitors learn history",
        "expectations": "I want to learn how museum education programs work",
        "desired_direction": "project", "availability_hours_month": "8",
        "availability_schedule_type": "weekends", "other_commitments": "I work weekdays until 18:00",
        "case_response": "If I cannot come, I will warn the curator in advance and agree a replacement",
    }
    for field, value in values.items():
        response = client.post(f"/api/v1/sessions/{session_id}/answers", json={
            "answer_id": f"answer:{field}", "question_id": field,
            "text": value, "idempotency_key": f"save:{field}"})
        assert response.status_code == 200, response.text
    response = client.post(f"/api/v1/sessions/{session_id}/submit",
                           json={"idempotency_key": "submit"})
    assert response.status_code == 202, response.text
    result = client.get(f"/api/v1/sessions/{session_id}/result").json()
    assert result["status"] in {"SCORED", "NEEDS_REVIEW", "FOLLOWUP_REQUIRED"}, result
    assert result["error"] is None
    evaluation = result["evaluation"]
    assert evaluation["evaluation_mode"] == "rag_llm"
    assert "case:admission-flow" in evaluation["retrieved_doc_ids"]
    headers = {"Authorization": "Bearer synthetic-test-curator-token"}
    detail = client.get(f"/api/v1/admin/runs/{result['run_id']}", headers=headers).json()
    assert detail["submitted_answers"]["email"] == values["email"]
    assert values["email"] not in str(evaluation)
    assert detail["evaluation"]["analysis"]["candidate_summary"]
    assert client.get("/api/v1/admin/analytics", headers=headers).json()["total"] == 1
