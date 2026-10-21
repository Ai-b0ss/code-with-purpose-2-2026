"""Tests that require only FastAPI/Pydantic, no running server or httpx."""
import os
import tempfile
import unittest
from pathlib import Path

from backend.app import (
    ApplicationInput, clarification_for, db_connection, evaluate_demo,
    safe_csv,
)


def applicant(**changes):
    obj = {
        "applicant_name": "Тестовый кандидат",
        "age_over_18": True,
        "motivation": "Меня интересуют музейные образовательные проекты и исторические медиа.",
        "experience": "Монтаж видео для университетского исторического проекта.",
        "expectations": "Развивать навыки и быть полезным музею.",
        "availability_hours_month": 16,
        "other_commitments": "",
        "desired_direction": "media",
    }
    obj.update(changes)
    return ApplicationInput(**obj)


class BasicTests(unittest.TestCase):
    def test_evaluator_explains_direction(self):
        result = evaluate_demo(applicant())
        self.assertEqual(result["recommended_direction"], "media")
        self.assertEqual(result["score_status"], "not_calibrated")
        self.assertIsNone(result["score"])
        self.assertTrue(result["interview_questions"])

    def test_clarification_on_short_answer(self):
        data = applicant(motivation="Хочу помочь")
        self.assertIsNotNone(clarification_for(data))
        self.assertIsNone(clarification_for(applicant()))

    def test_full_time_commitments_do_not_auto_reject(self):
        result = evaluate_demo(applicant(other_commitments="Работаю на полную ставку"))
        self.assertIsNone(result["score"])
        self.assertTrue(result["possible_problems"])

    def test_csv_formula_escape(self):
        self.assertEqual(safe_csv("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(safe_csv("обычный текст"), "обычный текст")

    def test_sqlite_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("GIM_DB_PATH")
            os.environ["GIM_DB_PATH"] = str(Path(directory) / "store.sqlite3")
            try:
                with db_connection() as con:
                    names = {
                        row[0] for row in
                        con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                    self.assertIn("applications", names)
            finally:
                if old is None:
                    os.environ.pop("GIM_DB_PATH", None)
                else:
                    os.environ["GIM_DB_PATH"] = old


if __name__ == "__main__":
    unittest.main()
