import itertools
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app import ApplicationInput
from backend.app import app
from backend.evaluator import EvaluationError
from backend.scoring import RUBRIC, assess, score_criteria


@pytest.fixture(autouse=True)
def graph_context(monkeypatch):
    monkeypatch.setattr("backend.scoring.retrieve", lambda role, question: [
        {"doc_id": "role:organizational", "version_id": "gim-graph-v2",
         "source_path": "docs/volunteer-questionnaire.docx",
         "quote": "Организационное направление", "text": "Помощь на мероприятиях",
         "links": [{"type": "HAS_TASK", "id": "task:audience"}]}
    ])


def candidate():
    return ApplicationInput(
        applicant_name="Секретное имя", email="private@example.test", age_over_18=True,
        birth_date="1990-01-01",
        motivation="Хочу помогать посетителям музея на мероприятиях.",
        experience="Организовал выставку.", expectations="Работа с посетителями.",
        availability_hours_month=16, desired_direction="organizational",
        case_response="Предупрежу координатора заранее и согласую замену.",
    )


def model_response(grades=None, bad_quote=False):
    grades = grades or [3, 2, 3, 2, 2, 3]
    sources = ["motivation", "desired_direction", "experience", "availability_hours_month", "expectations", "case_response"]
    quotes = ["Хочу помогать", "organizational", "Организовал выставку", "16", "Работа с посетителями", "Предупрежу координатора"]
    return json.dumps({"rubric_version": "pilot-v1", "criteria": [
        {"id": row[0], "grade": grade, "answer_id": source if grade is not None else "",
         "evidence": ("придуманная цитата" if bad_quote else quote) if grade is not None else "",
         "rationale": "Основание по ответу"}
        for row, grade, source, quote in zip(RUBRIC, grades, sources, quotes)
    ], "flags": []}, ensure_ascii=False)


class Adapter:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0
        self.user = ""

    def complete(self, *, system, user, schema):
        self.calls += 1
        self.user = user
        return next(self.responses)


def test_score_formula_all_combinations():
    scores = set()
    for grades in itertools.product((1, 2, 3), repeat=6):
        numerator, score = score_criteria([{"grade": grade} for grade in grades])
        assert numerator == sum(row[2] * grade for row, grade in zip(RUBRIC, grades))
        assert score == ((2 * numerator + 25) // 50) / 4
        scores.add(score)
    assert scores == {1 + i / 4 for i in range(9)}
    assert score_criteria([{"grade": n} for n in (3, 2, 3, 2, 2, 3)]) == (255, 2.5)


def test_rag_uses_role_documents_without_contact_details():
    adapter = Adapter([model_response()])
    result = assess(candidate(), adapter)
    assert result["score"] == 2.5
    assert result["retrieved_doc_ids"] == ["role:organizational"]
    assert "Секретное имя" not in adapter.user
    assert "private@example.test" not in adapter.user
    assert "role-collections" not in adapter.user


def test_missing_grade_has_no_score():
    adapter = Adapter([model_response([3, 2, 3, 2, 2, None])])
    result = assess(candidate(), adapter)
    assert result["score"] is None
    assert result["score_status"] == "needs_review"


def test_invalid_quote_retries_once_then_errors():
    adapter = Adapter([model_response(bad_quote=True)] * 2)
    with pytest.raises(EvaluationError, match="invalid_scoring_response"):
        assess(candidate(), adapter)
    assert adapter.calls == 2


def test_http_form_to_score_and_saved_result(monkeypatch, tmp_path):
    adapter = Adapter([model_response()])
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-adapter")
    monkeypatch.setenv("GIM_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr("backend.scoring.adapter_from_env", lambda env: adapter)
    body = candidate().model_dump()
    body["consent_personal_data"] = True
    body["application_id"] = "rag-test"
    client = TestClient(app)
    response = client.post("/api/v1/applications", json=body)
    assert response.status_code == 201
    assert response.json()["evaluation"]["score"] == 2.5
    assert client.get("/api/v1/applications/rag-test").json()["evaluation"]["criteria"][0]["id"] == "motivation"
    again = client.post("/api/v1/applications", json=body)
    assert again.json()["created"] is False
    assert adapter.calls == 1
    with sqlite3.connect(tmp_path / "test.sqlite3") as con:
        payload, name = con.execute("SELECT payload, applicant_name FROM applications WHERE id='rag-test'").fetchone()
        assert "private@example.test" not in payload and "Секретное имя" not in payload
        assert name == ""
        assert con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM assessment_runs").fetchone()[0] == 1
