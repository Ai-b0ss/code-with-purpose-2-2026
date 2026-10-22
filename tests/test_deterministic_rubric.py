"""Numeric pilot levels must be reproducible and abstain on missing evidence."""
import unittest
import json

from backend.deterministic_rubric import build_deterministic_rubric
from backend.field_calibration import SCORING_POLICY_PATH
from test_smoke import applicant


def answers_for(data):
    answers = data.model_dump(exclude={"applicant_name", "application_id", "email", "vk_url", "birth_date"})
    answers["email_provided"] = bool(data.email)
    answers["vk_provided"] = bool(data.vk_url)
    return answers


def reviews_for(data, *, experience=True):
    reviews = [
        {"field_id": "motivation", "category": "specific_relevant", "quality_level": 3,
         "evidence": data.motivation},
        {"field_id": "availability_hours_month", "category": "structured_value",
         "quality_level": None, "evidence": str(data.availability_hours_month)},
    ]
    if experience:
        reviews.append({"field_id": "experience", "category": "specific_relevant",
                        "quality_level": 3, "evidence": data.experience})
    return reviews


class DeterministicRubricTests(unittest.TestCase):
    def test_criterion_catalog_matches_score_and_question_policies(self):
        policy = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
        ids = policy["criterion_order"]
        self.assertEqual(len(ids), len(set(ids)))
        for section in ("criterion_labels", "criterion_evidence_fields"):
            self.assertEqual(set(ids), set(policy[section]))
        self.assertEqual(set(ids), set(policy["deterministic_rubric"]["criterion_weights"]))

    def test_missing_experience_is_unknown_not_zero(self):
        data = applicant(experience="", availability_hours_month=12)
        result = build_deterministic_rubric(data, reviews_for(data, experience=False), answers_for(data))
        skills = next(item for item in result["scorecard"] if item["criterion"] == "skills")
        self.assertIsNone(skills["level"])
        self.assertEqual(skills["evidence_question_ids"], [])
        self.assertIsInstance(result["score"], int)

    def test_first_year_has_weak_cap_not_severe_penalty(self):
        base = applicant(course="1-й курс бакалавриата", availability_hours_month=12,
                         availability_days=[])
        no_days = build_deterministic_rubric(base, reviews_for(base), answers_for(base))
        with_days = base.model_copy(update={"availability_days": ["сб"]})
        mitigated = build_deterministic_rubric(with_days, reviews_for(with_days), answers_for(with_days))
        level = lambda result: next(item["level"] for item in result["scorecard"]
                                    if item["criterion"] == "availability")
        self.assertEqual(level(no_days), 2)
        self.assertEqual(level(mitigated), 2)
        self.assertIn("mitigation", mitigated["availability_signal_ids"])
        later_course = with_days.model_copy(update={"course": "3-й курс бакалавриата"})
        ordinary = build_deterministic_rubric(
            later_course, reviews_for(later_course), answers_for(later_course))
        self.assertEqual(level(ordinary), 3)

    def test_full_time_commitment_still_caps_unclarified_availability(self):
        data = applicant(other_commitments="Работаю на полной ставке 5/2",
                         availability_hours_month=12, availability_days=[])
        result = build_deterministic_rubric(data, reviews_for(data), answers_for(data))
        availability = next(item for item in result["scorecard"]
                            if item["criterion"] == "availability")
        self.assertEqual(availability["level"], 1)

    def test_structured_hours_evidence_must_match_value(self):
        data = applicant(availability_hours_month=12, availability_days=[])
        reviews = reviews_for(data)
        next(item for item in reviews if item["field_id"] == "availability_hours_month")["evidence"] = "40"
        result = build_deterministic_rubric(data, reviews, answers_for(data))
        availability = next(item for item in result["scorecard"]
                            if item["criterion"] == "availability")
        self.assertIsNone(availability["level"])
        self.assertEqual(availability["evidence_question_ids"], [])

    def test_explicit_long_term_schedule_lifts_first_year_cap(self):
        answer = "Смогу приходить каждую субботу весь семестр и во время сессии."
        data = applicant(course="1-й курс бакалавриата", availability_hours_month=20,
                         availability_days=["суббота"],
                         followup_answers={"ai_availability": answer})
        reviews = reviews_for(data)
        reviews.append({"field_id": "followup_answers.ai_availability",
                        "category": "specific_relevant",
                        "signals": ["long_term_schedule_commitment"],
                        "quality_level": 3, "evidence": answer})
        result = build_deterministic_rubric(data, reviews, answers_for(data))
        availability = next(item for item in result["scorecard"]
                            if item["criterion"] == "availability")
        self.assertEqual(availability["level"], 3)
        self.assertIn("followup_answers.ai_availability", availability["evidence_question_ids"])
        self.assertIn("самоотчёт", availability["rationale"])
        self.assertIn(answer, [item["reason"] for item in result["evidence"]])

        reviews[-1]["evidence"] = "Несуществующее обещание."
        uncited = build_deterministic_rubric(data, reviews, answers_for(data))
        self.assertEqual(next(item["level"] for item in uncited["scorecard"]
                              if item["criterion"] == "availability"), 2)

        reviews[-1]["evidence"] = answer
        reviews[-1]["signals"] = ["long_term_schedule_commitment", "schedule_conflict"]
        contradictory = build_deterministic_rubric(data, reviews, answers_for(data))
        self.assertEqual(next(item["level"] for item in contradictory["scorecard"]
                              if item["criterion"] == "availability"), 2)

    def test_hobby_and_days_do_not_invent_skill_or_travel_evidence(self):
        data = applicant(experience="", hobbies="Рисую акварелью.",
                         availability_days=["суббота"], availability_hours_month=20)
        reviews = reviews_for(data, experience=False)
        reviews.append({"field_id": "hobbies", "category": "specific_relevant",
                        "quality_level": 3, "evidence": data.hobbies})
        result = build_deterministic_rubric(data, reviews, answers_for(data))
        by_criterion = {item["criterion"]: item for item in result["scorecard"]}
        self.assertIsNone(by_criterion["skills"]["level"])
        self.assertIsNone(by_criterion["logistics"]["level"])

    def test_unrecognized_day_does_not_raise_availability(self):
        data = applicant(availability_hours_month=20,
                         availability_days=["Шмяк бум мурлыка кабачок."])
        result = build_deterministic_rubric(data, reviews_for(data), answers_for(data))
        availability = next(item for item in result["scorecard"]
                            if item["criterion"] == "availability")
        self.assertEqual(availability["level"], 2)
        self.assertNotIn("availability_days", availability["evidence_question_ids"])


if __name__ == "__main__":
    unittest.main()
