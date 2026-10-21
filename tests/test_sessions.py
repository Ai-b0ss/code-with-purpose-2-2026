"""Session API contract: isolation, revisions and idempotent assessment."""
import sqlite3

from fastapi.testclient import TestClient

from backend.app import app


def _answer(client, session_id, question, value, key=None):
    return client.post(f"/api/v1/sessions/{session_id}/answers", json={
        "answer_id": f"answer:{question}", "question_id": question,
        "text": str(value), "idempotency_key": key or f"key:{question}:{value}",
    })


def test_session_holdout_independent_review_and_revision(monkeypatch, tmp_path):
    main = tmp_path / "sessions.sqlite3"
    pii = tmp_path / "pii.sqlite3"
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(main))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(pii))
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    calls = []

    def fake_assess(application, *, answer_ids):
        calls.append((application.motivation, dict(answer_ids)))
        return {"score": 2.5, "score_status": "scored", "criteria": []}

    monkeypatch.setattr("backend.scoring.assess", fake_assess)
    client = TestClient(app)
    created = client.post("/api/v1/sessions").json()
    sid = created["session_id"]
    assert _answer(client, sid, "motivation", "A meaningful museum role").status_code == 409
    required = {
        "consent_personal_data": "true", "applicant_name": "Test Person",
        "email": "test@example.org", "vk_url": "https://vk.com/test",
        "birth_date": "1990-01-01", "motivation": "A meaningful museum role",
        "expectations": "Learn about collections", "desired_direction": "project",
        "availability_hours_month": "8", "case_response": "I will alert the coordinator early.",
    }
    for question, value in required.items():
        response = _answer(client, sid, question, value)
        assert response.status_code == 200, response.text
    assert response.json()["state"] == "REVIEW"
    with sqlite3.connect(main) as con:
        raw = " ".join(str(row) for row in con.execute("SELECT * FROM answers"))
        raw += " ".join(str(row) for row in con.execute("SELECT * FROM request_keys"))
        assert "test@example.org" not in raw
        assert "Test Person" not in raw
    with sqlite3.connect(pii) as con:
        assert con.execute("SELECT count(*) FROM contacts").fetchone()[0] == 4

    submit = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit-1"})
    assert submit.status_code == 202, submit.text
    first_run = submit.json()["run_id"]
    result = client.get(f"/api/v1/sessions/{sid}/result").json()
    assert result["run_id"] == first_run
    assert result["status"] == "SCORED"
    assert result["evaluation"]["score"] == 2.5
    assert calls[0][1]["motivation"] == "answer:motivation"
    repeat = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit-1"})
    assert repeat.json()["run_id"] == first_run
    assert len(calls) == 1

    changed = _answer(client, sid, "motivation", "A different meaningful museum role")
    assert changed.status_code == 200
    assert client.get(f"/api/v1/sessions/{sid}/result").json()["score"] is None
    second = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit-2"})
    assert second.json()["run_id"] != first_run
    assert len(calls) == 2
    with sqlite3.connect(main) as con:
        rows = con.execute("SELECT run_id, parent_run_id, status FROM session_runs ORDER BY created_at").fetchall()
        assert len(rows) == 2
        assert rows[1][1] == rows[0][0]


def test_missing_answer_two_clarifications_and_underage_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "pii.sqlite3"))
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setattr("backend.scoring.assess", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model called")))
    client = TestClient(app)
    sid = client.post("/api/v1/sessions").json()["session_id"]
    assert _answer(client, sid, "consent_personal_data", "true").status_code == 200
    first = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "a"}).json()
    second = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "b"}).json()
    assert (first["status"], second["status"]) == ("INCOMPLETE", "NEEDS_REVIEW")
    assert second["next_question"] == "applicant_name"
    fields = {
        "applicant_name": "Young Person", "email": "young@example.org",
        "vk_url": "https://vk.com/young", "birth_date": "2015-01-01",
        "motivation": "I want to help the museum", "expectations": "Learn",
        "desired_direction": "project", "availability_hours_month": "4",
        "case_response": "Tell the coordinator",
    }
    for field, value in fields.items():
        assert _answer(client, sid, field, value).status_code == 200
    response = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "c"})
    assert response.status_code == 202
    result = client.get(f"/api/v1/sessions/{sid}/result").json()
    assert result["status"] == "INELIGIBLE"
    assert result["evaluation"]["score"] is None


def test_unverifiable_birth_date_needs_review_without_model(monkeypatch, tmp_path):
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "pii.sqlite3"))
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setattr("backend.scoring.assess", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model called")))
    client = TestClient(app)
    sid = client.post("/api/v1/sessions").json()["session_id"]
    fields = {
        "consent_personal_data": "true", "applicant_name": "Date Check",
        "email": "date@example.org", "vk_url": "https://vk.com/date",
        "birth_date": "unknown", "motivation": "I want to help the museum",
        "expectations": "Learn", "desired_direction": "project",
        "availability_hours_month": "4", "case_response": "Tell the coordinator",
    }
    for field, value in fields.items():
        assert _answer(client, sid, field, value).status_code == 200
    client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit"})
    result = client.get(f"/api/v1/sessions/{sid}/result").json()
    assert result["status"] == "NEEDS_REVIEW"
    assert result["evaluation"]["score"] is None


def test_withdrawing_consent_removes_session_data(monkeypatch, tmp_path):
    main = tmp_path / "sessions.sqlite3"
    pii = tmp_path / "pii.sqlite3"
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(main))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(pii))
    client = TestClient(app)
    sid = client.post("/api/v1/sessions").json()["session_id"]
    assert _answer(client, sid, "consent_personal_data", "true").status_code == 200
    assert _answer(client, sid, "email", "someone@example.org").status_code == 200
    withdrawn = _answer(client, sid, "consent_personal_data", "false", "withdraw")
    assert withdrawn.json()["state"] == "CLOSED"
    assert _answer(client, sid, "email", "again@example.org").status_code == 409
    with sqlite3.connect(main) as con:
        assert con.execute("SELECT count(*) FROM answers").fetchone()[0] == 0
    with sqlite3.connect(pii) as con:
        assert con.execute("SELECT count(*) FROM contacts").fetchone()[0] == 0
