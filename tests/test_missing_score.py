"""Missing scores are checked separately from score consistency."""
import json
import unittest
from backend.evaluator import validate_response
from test_evaluator import candidate, response


class MissingScoreTests(unittest.TestCase):
    def test_missing_total_is_computed_from_complete_criterion_levels(self):
        data = candidate()
        raw = response(data)
        raw.pop("score")
        result = validate_response(json.dumps(raw), data.model_dump())
        self.assertEqual(result["score"], 2)
        self.assertEqual(result["score_status"], "pilot_unvalidated")

    def test_insufficient_information_explains_null_without_fabricating_zero(self):
        data = candidate()
        raw = response(data)
        for rating in raw["scorecard"]:
            rating.update(level=None, evidence_question_ids=[], rationale="Нет сведений для оценки данного критерия.")
        result = validate_response(json.dumps(raw), data.model_dump())
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_status"], "insufficient_evidence")
        self.assertIn("не нулевая", result["score_explanation"])
