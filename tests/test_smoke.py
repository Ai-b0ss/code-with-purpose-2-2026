"""Tests that require only FastAPI/Pydantic, no running server or httpx."""
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from backend.app import (
    ApplicationInput, clarification_for, clarifications_for, db_connection, evaluate_demo,
    safe_csv, save_application,
)


def applicant(**changes):
    obj = {
        "applicant_name": "Тестовый кандидат",
        "birth_date": "2000-01-01",
        "email": "synthetic@example.test", "vk_url": "https://vk.com/synthetic",
        "university": "Другое", "university_other": "Тестовый вуз",
        "faculty": "История", "course": "2", "scientific_interests": "История",
        "foreign_languages": ["Нет B1+"], "hobbies": "Чтение", "source": "Другое",
        "age_over_18": True,
        "consent_personal_data": True,
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
        self.assertEqual(safe_csv("строка 1\nстрока 2\r\nстрока 3"), "строка 1\nстрока 2\r\nстрока 3")

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

    def test_save_application_requires_consent(self):
        data = applicant(consent_personal_data=False)
        with self.assertRaises(HTTPException) as cm:
            save_application(data)
        self.assertEqual(cm.exception.status_code, 422)
        self.assertIn("согласие", cm.exception.detail)

    def test_extended_application_fields(self):
        app = applicant(
            email="volunteer@shm.ru",
            vk_url="https://vk.com/history_volunteer",
            birth_date="2003-05-12",
            status="student",
            university="МГУ имени М.В. Ломоносова",
            faculty="Исторический",
            course="3-й курс бакалавриата",
            scientific_interests="История России XIX века",
            foreign_languages=["Английский (B2)", "Немецкий (B1)"],
            hobbies="Фотография, архивные исследования",
            availability_schedule_type="regular",
            availability_days=["пн", "ср", "сб"],
            other_volunteer_programs="Волонтёры культуры",
            source="Сайт Исторического музея",
        )
        self.assertEqual(app.email, "volunteer@shm.ru")
        self.assertEqual(app.university, "МГУ имени М.В. Ломоносова")
        self.assertEqual(len(app.foreign_languages), 2)
        self.assertEqual(len(app.availability_days), 3)

    def test_dynamic_clarifications_multiple(self):
        # 1. Underage + short motivation + collections direction -> multiple distinct clarifications
        data = applicant(
            age_over_18=False,
            motivation="Хочу в фонд",
            desired_direction="collections",
            availability_hours_month=4,
        )
        questions = clarifications_for(data)
        self.assertEqual(len(questions), 3)
        categories = [q["category"] for q in questions]
        self.assertIn("Возраст и регламент", categories)
        self.assertIn("Мотивация", categories)
        self.assertIn("График и занятость", categories)
        self.assertNotIn("Фондовое направление", categories)

        # 2. When answers are provided in followup_answers, questions are cleared
        answered = applicant(
            age_over_18=True,
            motivation="Хочу в фонд",
            desired_direction="collections",
            availability_hours_month=16,
            followup_answers={
                "motivation_detail": "Меня интересует архив документов начала XX века.",
                "collections_focus": "Готов работать с документами, также готов на экскурсии.",
            },
        )
        questions_answered = clarifications_for(answered)
        self.assertEqual(len(questions_answered), 0)


if __name__ == "__main__":
    unittest.main()

