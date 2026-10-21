"""Product-level acceptance guardrails: museum availability risks and exceptions."""
import unittest

from backend.app import ApplicationInput
from backend.availability_policy import availability_risks
from backend.evaluator import evaluate, validate_response
from backend.app import evaluate_demo


def candidate(**changes):
    data = dict(
        applicant_name="Тестовый волонтёр", age_over_18=True,
        motivation="Хочу регулярно помогать посетителям музея и проводить исторические мероприятия.",
        experience="", expectations="Участвовать в музейной жизни",
        availability_hours_month=16, other_commitments="",
        desired_direction="organizational",
    )
    data.update(changes)
    return ApplicationInput(**data)


class AvailabilityPolicyTests(unittest.TestCase):
    def test_first_year_is_a_negative_signal_not_rejection(self):
        a = candidate(course="1-й курс бакалавриата", availability_hours_month=20,
                      availability_days=["суббота"], other_commitments="Учёба")
        risks = availability_risks(a)
        self.assertTrue(any("Первый курс" in x and "неблагоприятный" in x for x in risks))
        self.assertTrue(any("Смягчающее" in x for x in risks))
        result = evaluate(a, mode="demo")
        self.assertIsNone(result["score"])
        self.assertTrue(any("Первый курс" in x for x in result["possible_problems"]))
        self.assertIn("Первый курс", result["fit_for_museum"])
        self.assertNotIn("отклонён", result["fit_for_museum"])

    def test_first_year_masters_is_not_mislabeled_bachelor(self):
        a = candidate(course="1 курс магистратуры", availability_hours_month=30,
                      availability_days=["суббота", "воскресенье"])
        risks = availability_risks(a)
        self.assertFalse(any("Первый курс бакалавриата" in x for x in risks))
        self.assertNotIn("Первый курс бакалавриата", evaluate(a, mode="demo")["fit_for_museum"])

    def test_full_time_is_a_risk_even_with_weekend_availability(self):
        a = candidate(other_commitments="Работа на полную ставку; свободные субботы",
                      availability_hours_month=20, availability_days=["суббота"])
        risks = availability_risks(a)
        self.assertTrue(any("полной ставке" in x for x in risks))
        self.assertTrue(any("Смягчающее" in x for x in risks))

    def test_multiple_projects_and_low_hours_are_negative(self):
        a = candidate(other_volunteer_programs="Три других волонтёрских проекта",
                      availability_hours_month=4)
        risks = availability_risks(a)
        self.assertTrue(any("Несколько параллельных" in x for x in risks))
        self.assertTrue(any("мало часов" in x for x in risks))

    def test_no_invented_risk_for_third_year_unencumbered_student(self):
        a = candidate(course="3-й курс бакалавриата", availability_hours_month=24,
                      availability_days=["суббота"])
        self.assertEqual(availability_risks(a), [])
        self.assertNotIn("Первый курс", evaluate(a, mode="demo")["fit_for_museum"])

    def test_baseline_llm_output_is_augmented_with_verifiable_risks(self):
        a = candidate(course="1-й курс бакалавриата", availability_hours_month=16)
        mock = evaluate_demo(a)
        mock.update(
            evaluation_mode="llm",
            preliminary_verdict="manual_review",
            verdict_comment="Нужно сопоставить график и другие обязательства с требованиями музея.",
            fit_for_museum="Кандидат интересуется музейной работой.",
            fit_for_candidate="Есть совпадение интересов.",
            evidence=[{"question_id":"motivation", "reason":a.motivation},
                      {"question_id":"availability_hours_month", "reason":str(a.availability_hours_month)}],
            scorecard=[
                {"criterion": name, "level": 2, "rationale": "Сведения анкеты требуют проверки координатором.",
                 "evidence_question_ids": ["motivation" if name != "availability" else "availability_hours_month"]}
                for name in ("motivation", "skills", "availability", "form_and_contact", "logistics")
            ],
        )
        import json
        class FakeAdapter:
            def complete(self, *, system, user, schema):
                self.system = system
                return json.dumps(mock, ensure_ascii=False)
        adapter = FakeAdapter()
        result = evaluate(a, adapter=adapter)
        self.assertIn("неблагоприятн", adapter.system.lower())
        self.assertTrue(any("Первый курс" in x for x in result["possible_problems"]))
        self.assertIn("Первый курс", result["fit_for_museum"])
        self.assertEqual(result["evaluation_mode"], "llm")
        self.assertEqual(result["score"], 2)
        self.assertEqual(result["score_status"], "pilot_unvalidated")


if __name__ == "__main__":
    unittest.main()
