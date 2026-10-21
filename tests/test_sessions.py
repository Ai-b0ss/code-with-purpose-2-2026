"""Session API contract: isolation, revisions and idempotent assessment."""
import sqlite3

from fastapi.testclient import TestClient

from backend.app import app

PROFILE = {
    "status": "unemployed", "scientific_interests": "History",
    "experience": "No formal experience", "foreign_languages": "[]",
    "hobbies": "Reading", "source": "Museum website",
    "availability_schedule_type": "flexible",
}


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

    monkeypatch.setattr("backend.scoring.retrieve", lambda role, question: [{
        "doc_id": "test:fact", "version_id": "test", "text": "Test context"}])

    def fake_assess(application, *, answer_ids, knowledge, adapter=None):
        calls.append((application.motivation, dict(answer_ids)))
        assert knowledge[0]["doc_id"] == "test:fact"
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
        **PROFILE,
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
        **PROFILE,
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
        **PROFILE,
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


def test_coordinator_review_creates_immutable_child_run(monkeypatch, tmp_path):
    main = tmp_path / "sessions.sqlite3"
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(main))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "pii.sqlite3"))
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("GIM_COORDINATOR_TOKEN", "test-coordinator-secret")
    monkeypatch.setattr("backend.scoring.retrieve", lambda role, question: [{
        "doc_id": "test:fact", "version_id": "test", "text": "Test context"}])
    monkeypatch.setattr("backend.scoring.assess", lambda *args, **kwargs: {
        "score": 2.5, "score_status": "pilot_scored", "criteria": []})
    client = TestClient(app)
    sid = client.post("/api/v1/sessions").json()["session_id"]
    fields = {
        "consent_personal_data": "true", "applicant_name": "Review Person",
        "email": "review@example.org", "vk_url": "https://vk.com/review",
        "birth_date": "1990-01-01", "motivation": "I want to help the museum",
        "expectations": "Learn about collections", "desired_direction": "project",
        "availability_hours_month": "8", "case_response": "I will alert the coordinator early.",
        **PROFILE,
    }
    for field, value in fields.items():
        assert _answer(client, sid, field, value).status_code == 200
    first = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit"}).json()["run_id"]
    assert client.get(f"/api/v1/admin/runs/{first}").status_code == 401
    headers = {"Authorization": "Bearer test-coordinator-secret"}
    detail = client.get(f"/api/v1/admin/runs/{first}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["submitted_answers"]["email"] == "review@example.org"
    assert "review@example.org" not in str(detail.json()["answers"])
    values = [
        ("motivation", "motivation", "I want to help the museum", 3),
        ("role_fit", "desired_direction", "project", 2),
        ("skills", "experience", "No formal experience", 1),
        ("availability", "availability_hours_month", "8", 2),
        ("task_understanding", "expectations", "Learn about collections", 2),
        ("responsibility", "case_response", "I will alert the coordinator early.", 3),
    ]
    criteria = [{"id": cid, "answer_id": f"answer:{field}", "evidence": evidence,
                 "grade": grade, "rationale": "Coordinator checked the quoted answer."}
                for cid, field, evidence, grade in values]
    reviewed = client.patch(f"/api/v1/admin/runs/{first}", headers=headers,
                            json={"reason": "Corrected all six criterion grades after review.",
                                  "criteria": criteria})
    assert reviewed.status_code == 201, reviewed.text
    child = reviewed.json()["run_id"]
    assert child != first
    assert reviewed.json()["parent_run_id"] == first
    assert client.get(f"/api/v1/sessions/{sid}/result").json()["run_id"] == child
    assert client.patch(f"/api/v1/admin/runs/{first}", headers=headers,
                        json={"reason": "Trying stale review again.", "criteria": criteria}).status_code == 409
    with sqlite3.connect(main) as con:
        parent = con.execute("SELECT result FROM session_runs WHERE run_id=?", (first,)).fetchone()[0]
        assert '"score": 2.5' in parent


def test_missing_model_key_is_recorded_for_coordinator_without_score(monkeypatch, tmp_path):
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "contacts.sqlite3"))
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("GIM_COORDINATOR_TOKEN", "test-coordinator-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GIM_LLM_API_KEY", raising=False)
    monkeypatch.delenv("GIM_LLM_ENDPOINT", raising=False)
    monkeypatch.setattr("backend.scoring.retrieve", lambda role, question: [{
        "doc_id": "test:fact", "version_id": "test", "text": "Museum context"}])
    client = TestClient(app)
    assert client.get("/api/v1/health").json()["model_configured"] is False
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    values = {
        "consent_personal_data": "true", "applicant_name": "Synthetic Candidate",
        "email": "synthetic@example.test", "vk_url": "https://vk.com/synthetic",
        "birth_date": "1990-01-01", "motivation": "I want to help the museum",
        "expectations": "I want to learn museum work", "desired_direction": "project",
        "availability_hours_month": "8", "case_response": "I will warn the coordinator early.",
        **PROFILE,
    }
    for field, value in values.items():
        assert _answer(client, session_id, field, value).status_code == 200
    client.post(f"/api/v1/sessions/{session_id}/submit", json={"idempotency_key": "submit"})
    result = client.get(f"/api/v1/sessions/{session_id}/result").json()
    assert result["status"] == "ERROR"
    assert result["evaluation"] is None
    assert result["error"] == "llm_api_key_required"
    detail = client.get(f"/api/v1/admin/runs/{result['run_id']}",
                        headers={"Authorization": "Bearer test-coordinator-secret"}).json()
    assert detail["error"] == "llm_api_key_required"


def test_student_to_curator_series_and_macro_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
    monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "contacts.sqlite3"))
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("GIM_COORDINATOR_TOKEN", "test-coordinator-secret")
    monkeypatch.setattr("backend.scoring.retrieve", lambda role, question: [{
        "doc_id": "case:admission-flow", "version_id": "test", "text": "Coordinator decides after interview."}])
    calls = []

    def assess(application, *, answer_ids, knowledge, adapter=None):
        calls.append(application.applicant_name)
        score = 2.75 if "strong" in application.motivation else 1.5
        return {"score": score, "score_status": "pilot_scored", "rubric_version": "pilot-v1",
                "criteria": [{"id": "motivation", "grade": 3 if score > 2 else 1}],
                "analysis": {"candidate_summary": "Synthetic candidate summary for curator review.",
                             "program_fit": "Interview will verify project fit.",
                             "program_information": "Museum coordinator makes the admission decision.",
                             "strengths": ["Relevant example"], "risks": ["Schedule confirmation"],
                             "interview_questions": ["What schedule works?"],
                             "source_doc_ids": ["case:admission-flow"]},
                "interview_questions": ["What schedule works?"],
                "summary": "Synthetic candidate summary for curator review."}

    monkeypatch.setattr("backend.scoring.assess", assess)
    client = TestClient(app)
    assert client.get("/student").status_code == 200
    assert client.get("/coordinator").status_code == 200
    headers = {"Authorization": "Bearer test-coordinator-secret"}
    assert client.get("/api/v1/admin/analytics").status_code == 401
    cases = [
        ("Strong Student", "strong motivation and a museum project", "1999-01-01", "project"),
        ("Weak Student", "general motivation", "1998-01-01", "project"),
        ("Young Student", "strong motivation", "2015-01-01", "collections"),
    ]
    run_ids = []
    for index, (name, motivation, birth_date, direction) in enumerate(cases):
        sid = client.post("/api/v1/sessions").json()["session_id"]
        values = {"consent_personal_data": "true", "applicant_name": name,
                  "email": f"candidate{index}@example.test", "vk_url": f"https://vk.com/candidate{index}",
                  "birth_date": birth_date, "motivation": motivation,
                  "expectations": "Learn museum work", "desired_direction": direction,
                  "availability_hours_month": "8", "case_response": "Warn the coordinator in advance",
                  **PROFILE}
        for field, value in values.items():
            assert _answer(client, sid, field, value).status_code == 200
        submitted = client.post(f"/api/v1/sessions/{sid}/submit", json={"idempotency_key": "submit"})
        assert submitted.status_code == 202, submitted.text
        result = client.get(f"/api/v1/sessions/{sid}/result").json()
        run_ids.append(result["run_id"])
        assert result["status"] == ("INELIGIBLE" if index == 2 else "SCORED")
        detail = client.get(f"/api/v1/admin/runs/{result['run_id']}", headers=headers).json()
        assert detail["submitted_answers"]["applicant_name"] == name
        assert detail["submitted_answers"]["motivation"] == motivation
        assert name not in str(detail["answers"])
        if index < 2:
            assert detail["evaluation"]["score"] == (2.75 if index == 0 else 1.5)
            assert detail["evaluation"]["analysis"]["source_doc_ids"] == ["case:admission-flow"]
        else:
            assert detail["evaluation"]["decision"]["may_proceed"] is False
    assert calls == ["[withheld]", "[withheld]"]
    listed = client.get("/api/v1/admin/runs", headers=headers).json()["runs"]
    assert {run["run_id"] for run in listed if run["active"]} == set(run_ids)
    analytics = client.get("/api/v1/admin/analytics", headers=headers).json()
    assert analytics["total"] == 3 and analytics["scored"] == 2
    assert analytics["mean_score"] == 2.12
    assert analytics["by_direction"] == {"project": 2, "collections": 1}
    assert analytics["by_status"] == {"SCORED": 2, "INELIGIBLE": 1}
