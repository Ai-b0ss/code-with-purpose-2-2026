"""Provisional criterion-based review: contract and source-grounded aggregation."""
import json
import unittest

from backend.evaluator import EvaluationError, evaluate, validate_response
from test_evaluator import FakeAdapter, candidate, response


class PilotRubricTests(unittest.TestCase):
    def evaluate_case(self, busy=False):
        data = candidate(busy)
        return evaluate(data, adapter=FakeAdapter(json.dumps(response(data), ensure_ascii=False)))

    def test_ordinal_score_is_computed_not_llm_supplied(self):
        strong = self.evaluate_case()
        constrained = self.evaluate_case(True)
        self.assertEqual(strong["score"], 2)
        self.assertEqual(constrained["score"], 1)
        for value in (strong, constrained):
            self.assertEqual(value["score_status"], "pilot_unvalidated")
            self.assertEqual(value["score_rubric"], "gim-five-groups-pilot-v1")
            self.assertEqual(value["rated_criteria_count"], 5)
            self.assertEqual(len(value["scorecard"]), 5)
            self.assertTrue(0 <= value["score_raw"] <= 3)

    def test_missing_or_duplicate_groups_fail_closed(self):
        data = candidate()
        answers = data.model_dump(exclude={"applicant_name", "application_id"})
        original = response(data)
        for rubric in ([], original["scorecard"][:-1],
                       original["scorecard"][:-1] + [original["scorecard"][0]]):
            with self.subTest(rubric=rubric), self.assertRaises(EvaluationError):
                validate_response(json.dumps({**original, "scorecard": rubric}), answers)

    def test_criterion_level_must_be_valid(self):
        data = candidate()
        answers = data.model_dump(exclude={"applicant_name", "application_id"})
        for bad_level in (-1, 4, 1.5, "3", True):
            rubric = [dict(r) for r in response(data)["scorecard"]]
            rubric[0] = {**rubric[0], "level": bad_level}
            with self.subTest(level=bad_level), self.assertRaises(EvaluationError):
                validate_response(json.dumps({**response(data), "scorecard": rubric}), answers)

    def test_criterion_must_cite_an_exactly_validated_evidence_field(self):
        data = candidate()
        answers = data.model_dump(exclude={"applicant_name", "application_id"})
        for ids in ([], ["unknown"], ["email"]):
            rubric = [dict(r) for r in response(data)["scorecard"]]
            rubric[0] = {**rubric[0], "evidence_question_ids": ids}
            with self.subTest(ids=ids), self.assertRaises(EvaluationError):
                validate_response(json.dumps({**response(data), "scorecard": rubric}), answers)

    def test_missing_information_is_not_zero(self):
        data = candidate()
        answers = data.model_dump(exclude={"applicant_name", "application_id"})
        rubric = [dict(r) for r in response(data)["scorecard"]]
        for i in (1, 3, 4):
            rubric[i] = {**rubric[i], "level": None, "evidence_question_ids": [],
                         "rationale": "Недостаточно данных для этого критерия."}
        result = validate_response(json.dumps({**response(data), "scorecard": rubric}), answers)
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_status"], "insufficient_evidence")
        self.assertEqual(result["rated_criteria_count"], 2)

    def test_model_cannot_self_assign_a_total_or_admission(self):
        data = candidate()
        answers = data.model_dump(exclude={"applicant_name", "application_id"})
        original = response(data)
        for changes in ({"score": 3}, {"score_status": "pilot_unvalidated"},
                        {"accepted": True}):
            with self.subTest(changes=changes), self.assertRaises(EvaluationError):
                validate_response(json.dumps({**original, **changes}), answers)

    def test_scored_result_is_persisted_and_visible_to_curator(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        import os
        from backend.app import export_csv, save_application
        from scripts.local_curator_report import build

        data = candidate().model_copy(update={
            "application_id": "synthetic-rubric-001",
            "consent_personal_data": True,
        })
        with TemporaryDirectory() as directory:
            db = Path(directory) / "test.sqlite3"
            report = Path(directory) / "curator.html"
            adapter = FakeAdapter(json.dumps(response(data), ensure_ascii=False))
            with patch.dict(os.environ, {"GIM_DB_PATH": str(db)}), patch(
                "backend.app.evaluate_application",
                side_effect=lambda value: evaluate(value, adapter=adapter),
            ):
                first = save_application(data)
                repeat = save_application(data)
                csv_text = export_csv().body.decode("utf-8-sig")
                count = build(db, report)
            self.assertTrue(first["created"])
            self.assertFalse(repeat["created"])
            self.assertEqual(len(adapter.calls), 1)
            self.assertNotIn("evaluation", first)
            self.assertIn(";2;", csv_text)
            self.assertIn("Оценка 0-3", csv_text)
            self.assertIn(";2;", csv_text)
            self.assertEqual(count, 1)
            html = report.read_text(encoding="utf-8")
            self.assertIn("pilot_unvalidated", html)
            self.assertIn("Пилотные оценки пяти критериев", html)

    def test_legacy_fixture_requires_explicit_opt_in(self):
        data = candidate()
        answers = data.model_dump(exclude={"applicant_name", "application_id"})
        legacy = response(data)
        legacy.pop("scorecard")
        with self.assertRaises(EvaluationError):
            validate_response(json.dumps(legacy), answers)
        result = validate_response(json.dumps(legacy), answers, allow_legacy=True)
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_status"], "not_calibrated")


if __name__ == "__main__":
    unittest.main()

