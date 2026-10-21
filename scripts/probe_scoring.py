"""One live scoring request on synthetic, non-personal data."""
from backend.app import ApplicationInput
from backend.agent_workflow import run_assessment
from backend.eligibility import check_eligibility


def main():
    candidate = ApplicationInput(
        applicant_name="Тестовый кандидат", age_over_18=True,
        birth_date="1990-01-01", consent_personal_data=True,
        motivation="Хочу помогать на музейных мероприятиях и встречать посетителей, потому что мне интересна история.",
        experience="Помогал организовать школьную выставку и встречал гостей.",
        expectations="Хочу помогать посетителям на мероприятиях.",
        availability_hours_month=16, desired_direction="organizational",
        availability_days=["сб"],
        case_response="Предупрежу координатора заранее, объясню ситуацию и согласую замену смены.",
    )
    result = run_assessment(candidate, eligibility=check_eligibility(candidate))
    print(f"status={result['score_status']} score={result['score']} rubric={result['rubric_version']} workflow={result['workflow_version']}")
    for item in result["criteria"]:
        print(f"{item['id']}: {item['grade']} ({item['answer_id']})")


if __name__ == "__main__":
    main()
