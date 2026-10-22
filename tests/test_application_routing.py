"""Protect real API -> evaluator wiring, consent and retry idempotency."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException

from backend.app import ApplicationInput, evaluate_application, save_application
from backend.evaluator import EvaluationError


def candidate(**overrides):
    payload = dict(
        application_id="route-integration-test-1",
        applicant_name="Тестовый кандидат",
        birth_date="2000-01-01",
        age_over_18=True,
        consent_personal_data=True,
        motivation="Хочу регулярно помогать музею в организации исторических мероприятий.",
        availability_hours_month=20,
        course="1-й курс бакалавриата",
        other_commitments="Учёба, свободные субботы",
        desired_direction="organizational",
    )
    payload.update(overrides)
    from test_smoke import applicant
    return applicant(**payload)


class APIRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        changes = patch.dict(os.environ, {"GIM_DB_PATH": str(Path(self.tmp.name) / "test.sqlite3"), "GIM_EVALUATION_MODE": "demo"})
        changes.start()
        self.addCleanup(changes.stop)

    def test_missing_consent_is_not_accepted(self):
        self.assertFalse(candidate(consent_personal_data=False).consent_personal_data)
        self.assertFalse(candidate().model_copy(update={"consent_personal_data": False}).consent_personal_data)
        fields = candidate().model_dump()
        del fields["consent_personal_data"]
        self.assertFalse(ApplicationInput(**fields).consent_personal_data)
        with self.assertRaises(HTTPException) as err:
            save_application(candidate(consent_personal_data=False))
        self.assertEqual(err.exception.status_code, 422)
        self.assertFalse((Path(self.tmp.name) / "test.sqlite3").exists())

    def test_retry_does_not_evaluate_again(self):
        with patch("backend.app.evaluate_application", return_value={"evaluation_mode": "llm", "summary": "sample", "possible_problems": []}) as evaluator:
            first = save_application(candidate())
            second = save_application(candidate())
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertNotIn("evaluation", first)
        self.assertNotIn("evaluation", second)
        self.assertEqual(evaluator.call_count, 1)

    def test_actual_mode_is_routed_to_standalone_evaluator(self):
        with patch("backend.evaluator.evaluate", return_value={"evaluation_mode": "llm", "possible_problems": []}) as evaluator:
            result = evaluate_application(candidate())
        self.assertEqual(result["evaluation_mode"], "llm")
        evaluator.assert_called_once()
        self.assertTrue(evaluator.call_args.kwargs["fallback_to_demo"] is False)

    def test_model_error_is_surfaced_as_503(self):
        with patch("backend.evaluator.evaluate", side_effect=EvaluationError("provider_unavailable")):
            with self.assertRaises(HTTPException) as err:
                evaluate_application(candidate())
        self.assertEqual(err.exception.status_code, 503)
        self.assertEqual(err.exception.detail, "evaluator_provider_unavailable")


if __name__ == "__main__":
    unittest.main()

