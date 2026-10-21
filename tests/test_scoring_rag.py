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
    ], "flags": [], "analysis": {
        "candidate_summary": "Кандидат описал интерес к музейным мероприятиям и назвал конкретный опыт.",
        "program_fit": "Выбранное направление связано с работой с посетителями; формат нужно подтвердить у координатора.",
        "program_information": "Программа предполагает согласованные задачи и собеседование перед окончательным приёмом.",
        "strengths": ["Есть пример организации выставки."],
        "risks": ["Необходимо согласовать доступный график с ролью."],
        "interview_questions": ["Какие музейные задачи вы готовы выполнять регулярно?"],
        "source_doc_ids": ["role:organizational"],
    }}, ensure_ascii=False)


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


def test_scoring_provider_can_use_separate_compatible_api(monkeypatch):
    adapter = Adapter([model_response()])
    captured = {}
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GIM_LLM_ENDPOINT", "https://provider.example/v1/chat/completions")
    monkeypatch.setenv("GIM_LLM_MODEL", "other-model")
    monkeypatch.setenv("GIM_LLM_API_KEY", "provider-test-key")
    monkeypatch.setattr("backend.scoring.adapter_from_env",
                        lambda env: (captured.update(env) or adapter))
    result = assess(candidate())
    assert result["score"] == 2.5
    assert captured["GIM_LLM_ENDPOINT"] == "https://provider.example/v1/chat/completions"
    assert captured["GIM_LLM_MODEL"] == "other-model"
    assert captured["GIM_LLM_API_KEY"] == "provider-test-key"


def test_local_ollama_does_not_receive_embedding_key(monkeypatch):
    adapter = Adapter([model_response()])
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "private-embedding-key")
    monkeypatch.delenv("GIM_LLM_API_KEY", raising=False)
    monkeypatch.setenv("GIM_LLM_ENDPOINT", "http://127.0.0.1:11434/api/chat")
    monkeypatch.setenv("GIM_LLM_MODEL", "qwen3.5:9b")
    monkeypatch.setattr("backend.scoring.adapter_from_env",
                        lambda env: (captured.update(env) or adapter))
    assess(candidate())
    assert captured["GIM_LLM_API_KEY"] == ""


def test_invalid_quotes_never_produce_grades():
    adapter = Adapter([model_response(bad_quote=True)] * 2)
    result = assess(candidate(), adapter)
    assert result["score"] is None
    assert all(item["grade"] is None and not item["evidence"] for item in result["criteria"])
    assert adapter.calls == 1


def test_single_analysis_items_are_normalized_to_lists():
    response = json.loads(model_response())
    response["analysis"]["strengths"] = "Есть пример организации выставки."
    response["analysis"]["risks"] = "Необходимо согласовать доступный график с ролью."
    adapter = Adapter([json.dumps(response, ensure_ascii=False)])
    result = assess(candidate(), adapter)
    assert result["score"] == 2.5
    assert result["analysis"]["strengths"] == ["Есть пример организации выставки."]
    assert result["analysis"]["risks"] == ["Необходимо согласовать доступный график с ролью."]
    assert adapter.calls == 1


def test_http_form_to_score_and_saved_result(monkeypatch, tmp_path):
    adapter = Adapter([model_response()])
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-adapter")
    monkeypatch.setenv("GIM_DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("GIM_COORDINATOR_TOKEN", "test-coordinator-secret")
    monkeypatch.setattr("backend.scoring.adapter_from_env", lambda env: adapter)
    body = candidate().model_dump()
    body["consent_personal_data"] = True
    body["application_id"] = "rag-test"
    client = TestClient(app)
    response = client.post("/api/v1/applications", json=body)
    assert response.status_code == 201
    assert response.json()["evaluation"]["score"] == 2.5
    assert client.get("/api/v1/applications/rag-test", headers={"Authorization": "Bearer test-coordinator-secret"}).json()["evaluation"]["criteria"][0]["id"] == "motivation"
    again = client.post("/api/v1/applications", json=body)
    assert again.json()["created"] is False
    assert adapter.calls == 1
    with sqlite3.connect(tmp_path / "test.sqlite3") as con:
        payload, name = con.execute("SELECT payload, applicant_name FROM applications WHERE id='rag-test'").fetchone()
        assert "private@example.test" not in payload and "Секретное имя" not in payload
        assert name == ""
        assert con.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM assessment_runs").fetchone()[0] == 1
