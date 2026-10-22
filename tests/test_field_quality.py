"""Semantic screening contract without external requests or personal data."""
import json
import unittest

from backend.evaluator import EvaluationError
from backend.field_quality import (
    METADATA_FIELDS, PRIVATE_FIELDS, STRUCTURED_FIELDS, TAXONOMY,
    exact_evidence_span, quality_level_for, review_fields, screening_outcome,
)
from backend.app import ApplicationInput


class FakeAdapter:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.response, ensure_ascii=False)


class FieldQualityTests(unittest.TestCase):
    def test_near_quote_is_anchored_to_exact_source_without_accepting_invention(self):
        source = "Организовывал школьные экскурсии для посетителей музея."
        near = "организовывал школьные экскурсии для посетителей музея."
        anchored = exact_evidence_span(source, near)
        self.assertEqual(anchored, source)
        self.assertIsNone(exact_evidence_span(source, "Три года работал хранителем фондов ГИМ."))
        self.assertIsNone(exact_evidence_span(
            "Не могу регулярно приходить в музей по субботам.",
            "Могу регулярно приходить в музей по субботам.",
        ))
        self.assertIsNone(exact_evidence_span(
            "Могу приходить 10 часов в месяц на регулярные смены.",
            "Могу приходить 30 часов в месяц на регулярные смены.",
        ))

    def test_abusive_field_is_screened_and_identity_is_private(self):
        answers = {
            "applicant_name": "Синтетический кандидат",
            "email": "synthetic@example.invalid",
            "motivation": "Пошли вы все, тупой музей.",
            "experience": "",
            "availability_hours_month": 12,
        }
        adapter = FakeAdapter({"fields": [{
            "field_id": "motivation", "category": "abusive", "signals": [],
            "evidence": "тупой музей",
        }]})
        result = review_fields(answers, adapter)
        self.assertEqual(screening_outcome(result), "screen_out_for_curator_review")
        request = adapter.calls[0]["user"]
        self.assertNotIn("Синтетический кандидат", request)
        self.assertNotIn("synthetic@example.invalid", request)
        self.assertIn("availability_hours_month", [x["field_id"] for x in result])

    def test_requires_every_field_and_exact_quote(self):
        answers = {"motivation": "Люблю историю музея.", "experience": "Помогал на экскурсиях."}
        missing = FakeAdapter({"fields": [{
            "field_id": "motivation", "category": "specific_relevant",
            "signals": [], "evidence": "Люблю историю",
        }]})
        with self.assertRaises(EvaluationError):
            review_fields(answers, missing)
        invented = FakeAdapter({"fields": [
            {"field_id": "motivation", "category": "specific_relevant", "signals": [], "evidence": "История"},
            {"field_id": "experience", "category": "specific_relevant", "signals": [], "evidence": "10 лет в ГИМ"},
        ]})
        with self.assertRaises(EvaluationError):
            review_fields(answers, invented)

    def test_partial_batch_retries_only_missing_field(self):
        class SequencedAdapter:
            def __init__(self):
                self.calls = []
            def complete(self, **kwargs):
                request = json.loads(kwargs["user"])
                self.calls.append(request)
                if "answers" in request:
                    return json.dumps({"fields": [{
                        "field_id": "motivation", "category": "specific_relevant",
                        "signals": [], "evidence": "Люблю историю",
                    }]}, ensure_ascii=False)
                return json.dumps({
                    "field_id": "experience", "category": "specific_relevant",
                    "signals": [], "evidence": "Помогал на экскурсиях",
                }, ensure_ascii=False)
        adapter = SequencedAdapter()
        reviews = review_fields({
            "motivation": "Люблю историю музея.",
            "experience": "Помогал на экскурсиях.",
        }, adapter)
        self.assertEqual(len(reviews), 2)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(adapter.calls[1]["field_id"], "experience")

    def test_nonsense_is_strong_negative_signal(self):
        self.assertEqual(screening_outcome([{"category": "nonsense"}]), "strong_negative_signal")

    def test_institution_and_course_are_not_quality_scores(self):
        self.assertIsNone(quality_level_for("university", "specific_relevant"))
        self.assertIsNone(quality_level_for("course", "specific_relevant"))
        self.assertEqual(quality_level_for("motivation", "specific_relevant"), 3)
        self.assertIsNone(quality_level_for("experience", "explicit_none"))

    def test_structured_string_keeps_source_form_for_exact_evidence(self):
        reviews = review_fields({"desired_direction": "organizational"}, FakeAdapter({"fields": []}))
        self.assertEqual(reviews[0]["evidence"], "organizational")

    def test_long_exact_quote_is_bounded_without_invention(self):
        answer = "музей " * 60
        review = review_fields({"motivation": answer}, FakeAdapter({"fields": [{
            "field_id": "motivation", "category": "specific_relevant",
            "signals": [], "evidence": answer,
        }]}))[0]
        self.assertEqual(len(review["evidence"]), 300)
        self.assertIn(review["evidence"], answer)

    def test_classifier_receives_source_question_for_figurative_field(self):
        answer = "Умею ясно объяснять сложные вещи."
        adapter = FakeAdapter({"fields": [{
            "field_id": "followup_answers.superpower", "category": "specific_relevant",
            "signals": ["concrete_example"], "evidence": answer,
        }]})
        review_fields({"followup_answers": {"superpower": answer}}, adapter)
        request = json.loads(adapter.calls[0]["user"])
        self.assertIn("образный вопрос", request["questions"]["followup_answers.superpower"])

    def test_every_current_text_field_has_question_context(self):
        model_fields = set(ApplicationInput.model_fields)
        text_fields = model_fields - PRIVATE_FIELDS - STRUCTURED_FIELDS - METADATA_FIELDS
        self.assertFalse(text_fields - set(TAXONOMY["field_questions"]))

    def test_nested_choice_gets_parent_question_context(self):
        adapter = FakeAdapter({"fields": [{
            "field_id": "foreign_languages.0", "category": "relevant_general",
            "signals": [], "evidence": "Английский",
        }]})
        review_fields({"foreign_languages": ["Английский"]}, adapter)
        questions = json.loads(adapter.calls[0]["user"])["questions"]
        self.assertIn("B1", questions["foreign_languages.0"])

    def test_unmapped_text_field_fails_before_model_request(self):
        adapter = FakeAdapter({"fields": []})
        with self.assertRaises(EvaluationError) as raised:
            review_fields({"new_unknown_field": "Некоторое значение"}, adapter)
        self.assertEqual(raised.exception.code, "unmapped_field_question")
        self.assertEqual(adapter.calls, [])

    def test_legacy_combined_followup_is_not_counted_twice(self):
        base = dict(applicant_name="Synthetic", age_over_18=True,
                    motivation="Хочу помогать музею", availability_hours_month=8,
                    desired_direction="project")
        duplicate = ApplicationInput(**base, followup_answers={"ai_skills": "Помогал на выставке"},
                                     followup_answer="ai_skills: Помогал на выставке")
        self.assertEqual(duplicate.followup_answer, "")
        separate = ApplicationInput(**base, followup_answers={"ai_skills": "Помогал на выставке"},
                                    followup_answer="Дополнительно могу переводить тексты")
        self.assertEqual(separate.followup_answer, "Дополнительно могу переводить тексты")


if __name__ == "__main__":
    unittest.main()
