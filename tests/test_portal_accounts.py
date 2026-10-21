"""Role accounts, ownership and the optional live portal path."""
import os

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.auth import seed_account


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("GIM_AUTH_REQUIRED", "1")
    monkeypatch.setenv("GIM_DATABASE_URL", "")
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "contacts.sqlite3"))
    seed_account("student_test", "student", "student-test-password")
    seed_account("other_student", "student", "other-student-password")
    seed_account("curator_test", "coordinator", "curator-test-password")
    client = TestClient(app)
    def login(name, password):
        response = client.post("/api/v1/auth/login", json={"username": name, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": "Bearer " + response.json()["access_token"]}
    return client, login


def test_portal_roles_and_session_ownership(monkeypatch, tmp_path):
    client, login = _setup(monkeypatch, tmp_path)
    student = login("student_test", "student-test-password")
    other = login("other_student", "other-student-password")
    curator = login("curator_test", "curator-test-password")
    assert client.post("/api/v1/sessions").status_code == 401
    created = client.post("/api/v1/sessions", headers=student)
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    assert client.get("/api/v1/sessions/" + session_id + "/result", headers=other).status_code == 403
    assert client.get("/api/v1/sessions/" + session_id + "/result", headers=curator).status_code == 403
    assert client.get("/api/v1/admin/runs", headers=student).status_code == 403
    assert client.get("/api/v1/admin/runs", headers=curator).status_code == 200
    assert client.get("/api/v1/auth/me", headers=student).json()["sessions"][0]["id"] == session_id
    assert client.post("/api/v1/auth/logout", headers=student).status_code == 204
    assert client.get("/api/v1/auth/me", headers=student).status_code == 401


@pytest.mark.skipif(os.getenv("GIM_TEST_PORTAL_LIVE") != "1"
                    and os.getenv("GIM_TEST_PORTAL_CLOUD") != "1",
                    reason="Requires Neo4j and a configured local or cloud model")
def test_live_student_queue_score_and_curator_view(monkeypatch, tmp_path):
    client, login = _setup(monkeypatch, tmp_path)
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    if os.getenv("GIM_TEST_PORTAL_CLOUD") != "1":
        monkeypatch.setenv("GIM_LLM_ENDPOINT", "http://127.0.0.1:11434/api/chat")
        monkeypatch.setenv("GIM_LLM_MODEL", "qwen3:14b")
        monkeypatch.setenv("GIM_LLM_API_KEY", "")
    monkeypatch.setenv("GIM_LLM_TIMEOUT", "300")
    student = login("student_test", "student-test-password")
    curator = login("curator_test", "curator-test-password")
    session_id = client.post("/api/v1/sessions", headers=student).json()["session_id"]
    values = {
        "consent_personal_data": "true", "applicant_name": "Synthetic Portal Candidate",
        "email": "portal-candidate@example.test", "vk_url": "https://vk.com/portal_candidate",
        "birth_date": "1998-01-01", "status": "working",
        "scientific_interests": "История", "experience": "Организовал образовательный квест.",
        "foreign_languages": "[]", "hobbies": "История", "source": "Сайт музея",
        "motivation": "Хочу создать квест для посетителей музея.",
        "expectations": "Хочу участвовать в проектной работе.",
        "desired_direction": "project", "availability_hours_month": "8",
        "availability_schedule_type": "weekends", "other_commitments": "Работаю по будням.",
        "case_response": "Если не смогу прийти, заранее предупрежу куратора и согласую другое время.",
    }
    for field, value in values.items():
        response = client.post(f"/api/v1/sessions/{session_id}/answers", headers=student,
                               json={"answer_id": f"answer:{field}", "question_id": field,
                                     "text": value, "idempotency_key": f"save:{field}"})
        assert response.status_code == 200, response.text
    submitted = client.post(f"/api/v1/sessions/{session_id}/submit", headers=student,
                            json={"idempotency_key": "submit"})
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["run_id"]
    result = client.get(f"/api/v1/sessions/{session_id}/result", headers=student).json()
    assert result["status"] in {"SCORED", "FOLLOWUP_REQUIRED", "NEEDS_REVIEW"}, result
    assert result["evaluation"]["evaluation_mode"] == "rag_llm"
    assert result["evaluation"]["decision"]["verdict"] in {"да", "нет", "сомнительно"}
    assert result["evaluation"]["retrieved_doc_ids"]
    if os.getenv("GIM_TEST_PORTAL_CLOUD") == "1":
        assert result["evaluation"]["model_id"] == os.getenv("GIM_LLM_MODEL")
    detail = client.get(f"/api/v1/admin/runs/{result['run_id']}", headers=curator).json()
    assert detail["submitted_answers"]["email"] == values["email"]
    assert detail["evaluation"]["analysis"]["candidate_summary"]
