from datetime import date

from fastapi.testclient import TestClient

from backend.app import ApplicationInput, app
from backend.eligibility import check_eligibility


def candidate(**changes):
    data = dict(applicant_name="Кандидат", age_over_18=True,
                consent_personal_data=True, birth_date="2007-10-22",
                motivation="Хочу проводить исторические квесты для посетителей.",
                availability_hours_month=12, desired_direction="project")
    data.update(changes)
    return ApplicationInput(**data)


def test_age_boundary_and_consent_are_the_only_hard_gates():
    today = date(2026, 10, 21)
    assert check_eligibility(candidate(), on_date=today)["status"] == "eligible"
    assert check_eligibility(candidate(birth_date="2008-10-21"), on_date=today)["status"] == "eligible"
    assert check_eligibility(candidate(birth_date="2008-10-22"), on_date=today)["status"] == "ineligible"
    assert check_eligibility(candidate(consent_personal_data=False), on_date=today)["status"] == "no_consent"
    assert check_eligibility(candidate(birth_date=""), on_date=today)["status"] == "age_unverified"


def test_underage_application_never_calls_model(monkeypatch, tmp_path):
    monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
    monkeypatch.setenv("GIM_DB_PATH", str(tmp_path / "gim.sqlite3"))
    monkeypatch.setattr("backend.scoring.assess", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model called")))
    body = candidate(birth_date="2010-01-01").model_dump()
    response = TestClient(app).post("/api/v1/applications", json=body)
    assert response.status_code == 201
    result = response.json()["evaluation"]
    assert result["score"] is None
    assert result["score_status"] == "ineligible"
    assert result["evaluation_mode"] == "eligibility_rules"


def test_form_can_check_age_before_collecting_full_application():
    response = TestClient(app).post("/api/v1/eligibility", json={
        "birth_date": "2010-01-01", "consent_personal_data": True})
    assert response.status_code == 200
    assert response.json()["status"] == "ineligible"
