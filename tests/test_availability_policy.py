"""Current documents permit schedule clarification but no demographic proxies."""
from backend.app import ApplicationInput
from backend.availability_policy import availability_risks


def candidate(**changes):
    data = dict(applicant_name="Тест", age_over_18=True,
                motivation="Хочу помогать посетителям исторического музея.",
                availability_hours_month=16, desired_direction="organizational")
    data.update(changes)
    return ApplicationInput(**data)


def test_first_year_and_full_time_are_not_automatic_risks():
    assert availability_risks(candidate(course="1-й курс бакалавриата")) == []
    prompts = availability_risks(candidate(course="1-й курс бакалавриата",
                                           other_commitments="Работаю полный день"))
    assert len(prompts) == 1
    assert "Уточнить совместимость" in prompts[0]
    assert "Первый курс" not in prompts[0]


def test_zero_hours_is_review_question_without_invented_minimum():
    prompts = availability_risks(candidate(availability_hours_month=0))
    assert len(prompts) == 1
    assert "0 часов" in prompts[0]
    assert "8" not in prompts[0]


def test_nonzero_hours_without_documented_role_schedule_has_no_risk():
    assert availability_risks(candidate(availability_hours_month=4)) == []
