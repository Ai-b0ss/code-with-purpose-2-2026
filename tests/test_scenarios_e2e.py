"""End-to-End verification script for 3 GIM candidate scenarios and API integration."""
import csv
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from backend.app import (
    ApplicationInput,
    clarifications_for,
    clarification_for,
    evaluate_demo,
    db_connection,
    safe_csv,
    format_created_at,
    format_birth_date,
    DIRECTIONS,
)

class ScenariosVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db = os.environ.get("GIM_DB_PATH")
        os.environ["GIM_DB_PATH"] = str(Path(self.temp_dir.name) / "test_gim.sqlite3")

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop("GIM_DB_PATH", None)
        else:
            os.environ["GIM_DB_PATH"] = self.old_db
        self.temp_dir.cleanup()

    def test_scenario_history_student(self):
        """Scenario A: Студент-историк (МГУ, старший курс, интерес к архивам и фондам)."""
        payload = {
            "application_id": "test-student-history-001",
            "applicant_name": "Алексей Смирнов",
            "email": "smirnov.hist@msu.ru",
            "vk_url": "https://vk.com/smirnov_alex_history",
            "birth_date": "2003-04-15",
            "age_over_18": True,
            "consent_personal_data": True,
            "status": "student",
            "university": "МГУ имени М.В. Ломоносова",
            "university_other": "",
            "faculty": "Исторический факультет, кафедра истории России",
            "course": "3-й курс бакалавриата",
            "scientific_interests": "История повседневности Москвы XIX века, музейные архивы",
            "foreign_languages": ["Английский", "Немецкий"],
            "motivation": "Давно изучаю фонды Исторического музея в рамках курсовых работ. Хочу внести реальный вклад в работу с архивными описями и популяризацию исторического наследия.",
            "expectations": "Получить практический навык атрибуции экспонатов и познакомиться с музейным коллективом.",
            "experience": "Стажировка в архиве РГАЛИ, обработка текстовых документов фонда рубежа веков.",
            "hobbies": "Научные публикации, экскурсионные маршруты по Москве",
            "source": "Сайт Исторического музея",
            "desired_direction": "collections",
            "availability_hours_month": 24,
            "availability_schedule_type": "regular",
            "availability_days": ["пт", "сб"],
            "other_commitments": "Учёба на дневном отделении (пн-чт)",
            "other_volunteer_programs": "",
            "followup_answers": {
                "collections_focus": "Интересуют письменные источники и фотоархивы. Готов также помогать на выставках при высокой загрузке."
            },
            "followup_answer": "Интересуют письменные источники и фотоархивы."
        }

        # 1. Pydantic validation (Contract test)
        app_data = ApplicationInput(**payload)
        self.assertEqual(app_data.applicant_name, "Алексей Смирнов")
        self.assertEqual(app_data.desired_direction, "collections")
        self.assertEqual(len(app_data.foreign_languages), 2)
        self.assertEqual(len(app_data.availability_days), 2)

        # 2. Clarifications evaluation
        # With collections answered, collections_focus is satisfied
        clarifs = clarifications_for(app_data)
        self.assertEqual(len(clarifs), 0)

        # 3. SQLite storage & Idempotency
        eval_result = evaluate_demo(app_data)
        self.assertEqual(eval_result["recommended_direction"], "collections")

        with db_connection() as con:
            con.execute(
                "INSERT INTO applications(id, created_at, applicant_name, payload, evaluation) "
                "VALUES(?, datetime('now'), ?, ?, ?)",
                (app_data.application_id, app_data.applicant_name, app_data.model_dump_json(), json.dumps(eval_result)),
            )

        # Verify retrieval
        with db_connection() as con:
            row = con.execute("SELECT payload, evaluation FROM applications WHERE id = ?", (app_data.application_id,)).fetchone()
            self.assertIsNotNone(row)
            saved = json.loads(row["payload"])
            self.assertEqual(saved["university"], "МГУ имени М.В. Ломоносова")
            self.assertEqual(saved["course"], "3-й курс бакалавриата")
            self.assertEqual(saved["availability_schedule_type"], "regular")

    def test_scenario_media_photographer(self):
        """Scenario B: Фотограф / Медиа (ВШЭ, медиа-направление, гибкий график под съемки)."""
        payload = {
            "application_id": "test-media-photo-002",
            "applicant_name": "Екатерина Романова",
            "email": "romanova.media@hse.ru",
            "vk_url": "https://vk.com/katya_photo_moscow",
            "birth_date": "2002-11-20",
            "age_over_18": True,
            "consent_personal_data": True,
            "status": "student",
            "university": "НИУ ВШЭ",
            "university_other": "",
            "faculty": "Медиакоммуникации и дизайн",
            "course": "4-й курс бакалавриата",
            "scientific_interests": "Визуальный сторителлинг, репортажная фотосъемка в музеях",
            "foreign_languages": ["Английский", "Французский"],
            "motivation": "Хочу создавать красивый и вовлекающий фото- и видеоконтент для соцсетей ГИМ. Архитектура и залы музея невероятно кинематографичны.",
            "expectations": "Сформировать портфолио культурных проектов и помочь музею привлечь молодёжную аудиторию.",
            "experience": "2 года штатный репортажный фотограф студенческого медиацентра, опыт съемки мероприятий на 500+ человек.",
            "hobbies": "Пленочная фотография, монтаж рилсов, графический дизайн",
            "source": "Социальные сети",
            "desired_direction": "media",
            "availability_hours_month": 16,
            "availability_schedule_type": "flexible",
            "availability_days": ["сб", "вс"],
            "other_commitments": "Дипломный проект весной, график гибкий",
            "other_volunteer_programs": "",
            "followup_answers": {
                "media_tasks": "В первую очередь репортажное фото и короткие видео/клипы для ВК."
            },
            "followup_answer": "Репортажное фото и клипы."
        }

        app_data = ApplicationInput(**payload)
        self.assertEqual(app_data.desired_direction, "media")
        eval_result = evaluate_demo(app_data)
        self.assertEqual(eval_result["recommended_direction"], "media")

        with db_connection() as con:
            con.execute(
                "INSERT INTO applications(id, created_at, applicant_name, payload, evaluation) "
                "VALUES(?, datetime('now'), ?, ?, ?)",
                (app_data.application_id, app_data.applicant_name, app_data.model_dump_json(), json.dumps(eval_result)),
            )

    def test_scenario_inclusive_volunteer(self):
        """Scenario C: Волонтёр инклюзии (РГГУ, психологическое/педагогическое образование, сопровождение)."""
        payload = {
            "application_id": "test-inclusive-003",
            "applicant_name": "Мария Васильева",
            "email": "m.vasilieva@rggu.ru",
            "vk_url": "https://vk.com/masha_vasilieva_art",
            "birth_date": "2000-08-05",
            "age_over_18": True,
            "consent_personal_data": True,
            "status": "working",
            "university": "Российский государственный гуманитарный университет (РГГУ)",
            "university_other": "",
            "faculty": "Институт психологии им. Выготского",
            "course": "Окончил(а) обучение",
            "scientific_interests": "Инклюзивные музейные практики, тифлокомментирование, адаптация выставок",
            "foreign_languages": ["Английский"],
            "motivation": "Имею опыт сопровождения посетителей с ОВЗ и хочу развивать инклюзивные инициативы в главном историческом музее страны.",
            "expectations": "Помогать в организации доступных экскурсий и тактильных мастер-классов.",
            "experience": "Волонтёр инклюзивных проектов ГМИИ им. Пушкина, прохождение курса по этике взаимодействия с людьми с инвалидностью.",
            "hobbies": "Жестовый язык (базовый), керамика",
            "source": "Рекомендации друзей",
            "desired_direction": "inclusive",
            "availability_hours_month": 16,
            "availability_schedule_type": "regular",
            "availability_days": ["ср", "вс"],
            "other_commitments": "Работа 5/2, свободна по выходным и в среду после 18:00",
            "other_volunteer_programs": "Инклюзивный центр 'Тверская 15'",
            "followup_answers": {
                "other_volunteering": "Нагрузка в стороннем центре не превышает 4 часов в месяц, приоритет по выходным будет за ГИМ."
            },
            "followup_answer": "Готова выделять фиксированные выходные."
        }

        app_data = ApplicationInput(**payload)
        self.assertEqual(app_data.desired_direction, "inclusive")
        eval_result = evaluate_demo(app_data)
        self.assertEqual(eval_result["recommended_direction"], "inclusive")

        with db_connection() as con:
            con.execute(
                "INSERT INTO applications(id, created_at, applicant_name, payload, evaluation) "
                "VALUES(?, datetime('now'), ?, ?, ?)",
                (app_data.application_id, app_data.applicant_name, app_data.model_dump_json(), json.dumps(eval_result)),
            )

        # 4. Check CSV format generation with all 3 scenarios
        with db_connection() as con:
            records = con.execute("SELECT id, created_at, payload, evaluation FROM applications").fetchall()
            self.assertEqual(len(records), 1)
            row = records[0]
            entry = json.loads(row["payload"])
            self.assertEqual(entry["university"], "Российский государственный гуманитарный университет (РГГУ)")
            self.assertEqual(entry["status"], "working")
            self.assertEqual(entry["other_volunteer_programs"], "Инклюзивный центр 'Тверская 15'")

    def test_rejection_without_consent(self):
        from fastapi import HTTPException
        from backend.app import save_application
        payload = {
            "application_id": "test-no-consent-004",
            "applicant_name": "Иван Безсогласия",
            "age_over_18": True,
            "consent_personal_data": False,
            "motivation": "Хочу помогать музею на выставках и событиях.",
            "desired_direction": "organizational",
            "availability_hours_month": 16,
        }
        app_data = ApplicationInput(**payload)
        with self.assertRaises(HTTPException) as cm:
            save_application(app_data)
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("согласие", cm.exception.detail)

    def test_date_formatting_helpers(self):
        # 1. format_created_at with UTC ISO string -> converted to MSK (+3)
        self.assertEqual(format_created_at("2026-10-21T14:30:00+00:00"), "21.10.2026 17:30")
        # 2. format_created_at with SQLite datetime('now') naive string -> assumed UTC -> MSK (+3)
        self.assertEqual(format_created_at("2026-10-21 14:30:00"), "21.10.2026 17:30")
        # 3. format_created_at with empty string
        self.assertEqual(format_created_at(""), "")
        # 4. format_birth_date from YYYY-MM-DD to DD.MM.YYYY
        self.assertEqual(format_birth_date("2000-11-15"), "15.11.2000")
        self.assertEqual(format_birth_date(""), "")
        self.assertEqual(format_birth_date("invalid-date"), "invalid-date")

    def test_export_csv_semicolon_format(self):
        from backend.app import export_csv
        app_data = ApplicationInput(
            application_id="csv-fixture-001",
            applicant_name="=HYPERLINK(1)",
            age_over_18=True,
            consent_personal_data=True,
            birth_date="2000-11-25",
            motivation="Первая строка\nВторая строка",
            desired_direction="media",
            availability_hours_month=16,
        )
        evaluation = evaluate_demo(app_data)
        with db_connection() as con:
            con.execute(
                "INSERT INTO applications(id, created_at, applicant_name, payload, evaluation) "
                "VALUES(?, datetime('now'), ?, ?, ?)",
                (
                    app_data.application_id,
                    app_data.applicant_name,
                    app_data.model_dump_json(),
                    json.dumps(evaluation, ensure_ascii=False),
                ),
            )

        response = export_csv()
        content = response.body.decode("utf-8")
        rows = list(csv.reader(io.StringIO(content), delimiter=";"))
        header = rows[0]
        columns = [header[0].lstrip("\ufeff")] + header[1:]
        self.assertNotIn("Дата", columns)
        self.assertIn("Согласие ПД", columns)
        self.assertIn("Дата рождения", columns)
        self.assertIn("Научные интересы", columns)
        self.assertIn("Хобби", columns)
        self.assertEqual(len(columns), 31)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[1]), len(columns))
        # Name column
        self.assertEqual(rows[1][1], "'=HYPERLINK(1)")
        # Birth date column (formatted as DD.MM.YYYY)
        self.assertEqual(rows[1][4], "25.11.2000")
        self.assertEqual(rows[1][13], "Первая строка Вторая строка")


if __name__ == "__main__":
    unittest.main()
