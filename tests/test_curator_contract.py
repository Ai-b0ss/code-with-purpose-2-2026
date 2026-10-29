import csv
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import db_connection, export_csv, save_application
from backend.google_sheets import HEADERS, write_row, SheetSyncError
from scripts.local_curator_report import build
from test_evaluator import candidate, response


class CuratorContractTests(unittest.TestCase):
    def test_full_curator_context_and_zero_survive_sqlite_html_and_csv(self):
        data = candidate().model_copy(update={"application_id": "curator-zero", "consent_personal_data": True,
            "followup_questions": [{"id": "ai_availability", "question": "Какие дни свободны?"}],
            "followup_answers": {"ai_availability": "Нет свободных дней"}})
        output = response(data)
        output.update(score=0, score_status="pilot_unvalidated", interview_focus=["Проверить готовность к регулярным сменам"],
                      score_explanation="Подтверждённые противоречия", direction_reason="Обоснование направления")
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"GIM_DB_PATH": str(Path(tmp)/"qa.sqlite3")}), \
                patch("backend.app.evaluate_application", return_value=output):
            save_application(data)
            csv_rows = list(csv.reader(io.StringIO(export_csv().body.decode("utf-8-sig")), delimiter=";"))
            self.assertEqual(len(csv_rows[0]), 43)
            row = dict(zip(csv_rows[0], csv_rows[1]))
            self.assertEqual(row["Оценка 0-3"], "0")
            self.assertIn("Ручная проверка", row["Предварительный вердикт"])
            self.assertEqual(json.loads(row["Уточнения: вопросы и ответы"])[0]["answer"], "Нет свободных дней")
            self.assertIn("готовность", row["Вопросы для собеседования"])
            report = Path(tmp)/"report.html"
            build(Path(tmp)/"qa.sqlite3", report)
            html = report.read_text(encoding="utf-8")
            self.assertIn("Оценка: 0 / 3", html)
            self.assertIn("<table>", html)
            self.assertIn("Обоснование направления", html)
            self.assertIn("Нет свободных дней", html)
            self.assertIn("Темы очного собеседования", html)

    def test_legacy_sheet_header_extends_without_moving_original_columns(self):
        class Reply:
            ok = True
            def __init__(self, body): self.body = body
            def json(self): return self.body
        class Session:
            def __init__(self): self.writes = []
            def get(self, url, **kwargs):
                if url.endswith("!A:A"): return Reply({"values": [[HEADERS[0]], ["qa"]]})
                if "/values/" in url: return Reply({"values": [HEADERS[:32]]})
                return Reply({"sheets": [{"properties": {"sheetId": 1, "title": "Заявки"}}]})
            def put(self, url, **kwargs): self.writes.append((url, kwargs["json"]["values"])); return Reply({})
            def post(self, *args, **kwargs): return Reply({})
        session = Session()
        with patch.dict(os.environ, {"GIM_GOOGLE_SPREADSHEET_ID": "test"}), \
             patch("backend.google_sheets._client", return_value=session), \
             patch("backend.sheets_dashboard.ensure_dashboard"):
            write_row("qa", ["qa"] + [""]*42)
        self.assertTrue(session.writes[0][0].endswith("!A1:AQ1"))
        self.assertEqual(session.writes[0][1][0][:32], HEADERS[:32])
        self.assertTrue(session.writes[1][0].endswith("!A2:AQ2"))
