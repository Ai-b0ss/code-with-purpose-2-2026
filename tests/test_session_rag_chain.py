"""Questionnaire -> missing answer -> GraphRAG context -> model -> score -> routing."""
import json

from fastapi.testclient import TestClient

from backend.app import app
from backend.scoring import RUBRIC


def test_complete_session_uses_graph_context_and_returns_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "pii.sqlite3"))
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-adapter")
    chunks = [{"doc_id": "case:admission-flow", "version_id": "gim-graph-v3",
               "source_path": "docs/case.txt", "quote": "сначала заполнить анкету на сайте",
               "text": "Анкета, собеседование и решение координатора — отдельные этапы.", "links": []}]
    retrieved = []
    monkeypatch.setattr("backend.scoring.retrieve", lambda role, query: (retrieved.append((role, query)) or chunks))

    quotes = [
        ("motivation", "I want to help the museum", 3),
        ("desired_direction", "project", 2),
        ("experience", "I organized an event", 2),
        ("availability_hours_month", "12", 2),
        ("expectations", "I hope to learn museum work", 2),
        ("case_response", "I will warn the coordinator early", 3),
    ]
    response = json.dumps({"rubric_version": "pilot-v1", "criteria": [
        {"id": rubric[0], "grade": grade, "answer_id": f"answer:{field}",
         "evidence": quote, "rationale": "Evidence is present in the confirmed answer."}
        for rubric, (field, quote, grade) in zip(RUBRIC, quotes)
    ], "flags": [], "analysis": {
        "candidate_summary": "The candidate described a museum task, a relevant event and a response to missed work.",
        "program_fit": "Project work may fit the candidate, subject to the real task schedule and interview.",
        "program_information": "The museum uses an application and interview before the coordinator decides on admission.",
        "strengths": ["There is an example of event organization."],
        "risks": ["The exact schedule for the project role needs confirmation."],
        "interview_questions": ["Which museum project would you help deliver?"],
        "source_doc_ids": ["case:admission-flow"],
    }}, ensure_ascii=False)

    class Adapter:
        model = "test-adapter"
        calls = 0
        prompt = ""

        def complete(self, *, system, user, schema):
            self.calls += 1
            self.prompt = user
            return response

    adapter = Adapter()
    monkeypatch.setattr("backend.scoring.adapter_from_env", lambda env: adapter)
    client = TestClient(app)
    sid = client.post("/api/v1/sessions").json()["session_id"]
    fields = {
        "consent_personal_data": "true", "applicant_name": "Private Name",
        "email": "private@example.org", "vk_url": "https://vk.com/private",
        "birth_date": "1990-01-01", "status": "working",
        "scientific_interests": "History", "experience": "I organized an event",
        "foreign_languages": "[]", "source": "Museum website",
        "motivation": "I want to help the museum",
        "expectations": "I hope to learn museum work", "desired_direction": "project",
        "availability_hours_month": "12", "availability_schedule_type": "flexible",
        "other_commitments": "I can come on weekends after work",
        "case_response": "I will warn the coordinator early",
    }
    for field, value in fields.items():
        saved = client.post(f"/api/v1/sessions/{sid}/answers", json={
            "answer_id": f"answer:{field}", "question_id": field,
            "text": value, "idempotency_key": f"save:{field}"})
        assert saved.status_code == 200, saved.text
    incomplete = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit:1"})
    assert incomplete.json()["next_question"] == "hobbies"
    assert adapter.calls == 0
    client.post(f"/api/v1/sessions/{sid}/answers", json={
        "answer_id": "answer:hobbies", "question_id": "hobbies",
        "text": "Reading", "idempotency_key": "save:hobbies"})
    submitted = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit:2"})
    assert submitted.status_code == 202
    result = client.get(f"/api/v1/sessions/{sid}/result").json()
    assert result["status"] == "SCORED"
    assert result["evaluation"]["score"] == 2.25
    assert result["evaluation"]["workflow_version"] == "gim-langgraph-v1"
    assert result["evaluation"]["decision"]["status"] == "coordinator_review"
    assert result["evaluation"]["analysis"]["source_doc_ids"] == ["case:admission-flow"]
    assert adapter.calls == 1 and len(retrieved) == 1
    assert "case:admission-flow" in adapter.prompt
    assert "Private Name" not in adapter.prompt and "private@example.org" not in adapter.prompt
