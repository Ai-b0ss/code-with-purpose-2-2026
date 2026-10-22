"""Unsupported model risk prose must not reach a curator."""
import unittest

from backend.narrative_guard import apply_narrative_guard
from test_smoke import applicant


def result():
    return {
        "score": 3, "score_status": "pilot_deterministic_unvalidated",
        "summary": "Не указан университет и курс; это мешает регулярному участию.",
        "possible_problems": ["Отсутствие информации об университете"],
        "fit_for_museum": "Может помочь на мероприятиях.",
        "fit_for_candidate": "Ожидания требуют проверки.",
        "direction_reason": "Кандидат выбрал направление.",
        "verdict_comment": "Нужна проверка вуза.",
        "preliminary_verdict": "manual_review",
        "content_signal_contributions": [],
    }


class NarrativeGuardTests(unittest.TestCase):
    def test_missing_university_is_not_an_individual_risk(self):
        guarded = apply_narrative_guard(result(), applicant(desired_direction="organizational"))
        self.assertIn("missing_institution", guarded["narrative_guard_reasons"])
        self.assertNotIn("университет", guarded["summary"].casefold())
        self.assertEqual(guarded["possible_problems"], [])
        self.assertEqual(guarded["preliminary_verdict"], "suggest_interview")

    def test_explicit_first_year_risk_is_kept(self):
        guarded = apply_narrative_guard(result(), applicant(
            desired_direction="organizational", course="1-й курс бакалавриата",
        ))
        self.assertTrue(any("Первый курс" in item for item in guarded["possible_problems"]))

    def test_unmentioned_funds_claim_is_replaced(self):
        draft = result()
        draft["summary"] = "Кандидат подходит для работы в фондах ГИМ."
        draft["possible_problems"] = []
        guarded = apply_narrative_guard(draft, applicant(
            desired_direction="organizational", motivation="Хочу помогать на мероприятиях музея."
        ))
        self.assertIn("unmentioned_direction", guarded["narrative_guard_reasons"])
        self.assertNotIn("фондах", guarded["summary"].casefold())

    def test_manual_review_replaces_optimistic_model_conclusion(self):
        draft = result()
        draft["score"] = 1
        draft["summary"] = "Отличный кандидат, следует сразу пригласить на собеседование."
        draft["fit_for_museum"] = "Идеально подходит без дополнительных вопросов."
        draft["possible_problems"] = []
        draft["content_signal_contributions"] = [{
            "field_id": "motivation", "signal": "nonsense", "delta": -1.0,
        }]
        guarded = apply_narrative_guard(draft, applicant(desired_direction="organizational"))
        self.assertEqual(guarded["preliminary_verdict"], "manual_review")
        self.assertIn("1/3", guarded["summary"])
        self.assertIn("ручная проверка", guarded["summary"])
        self.assertIn("не имеет понятного смысла", guarded["summary"])
        self.assertNotIn("Отличный", guarded["summary"])
        self.assertNotIn("Идеально", guarded["fit_for_museum"])
        self.assertIn("проверить", guarded["verdict_comment"])


if __name__ == "__main__":
    unittest.main()
