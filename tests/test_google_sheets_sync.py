"""Delivery state and Google API requests, without credentials or real applicants."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from backend.app import db_connection, retry_sheet_sync, save_application
from backend.google_sheets import SheetSyncError, write_row
from test_application_routing import candidate


class SyncTests(unittest.TestCase):
    def test_parallel_retry_claims_one_google_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GIM_DB_PATH": str(Path(tmp) / "test.sqlite3"),
            "GIM_GOOGLE_SPREADSHEET_ID": "test-sheet",
            "GIM_EVALUATION_MODE": "demo",
        }):
            data = candidate(application_id="synthetic-parallel-1")
            with patch("backend.google_sheets.write_row", side_effect=SheetSyncError("google_http_503")):
                save_application(data)
            entered, resume = Event(), Event()

            def slow_write(*args):
                entered.set()
                self.assertTrue(resume.wait(5))
                return "https://docs.google.com/spreadsheets/d/test-sheet/edit#gid=7&range=A2"

            with patch("backend.google_sheets.write_row", side_effect=slow_write) as writer:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(retry_sheet_sync, data.application_id)
                    self.assertTrue(entered.wait(5))
                    second = pool.submit(retry_sheet_sync, data.application_id)
                    self.assertEqual(second.result(timeout=5)["sheet_sync"]["status"], "syncing")
                    resume.set()
                    self.assertEqual(first.result(timeout=5)["sheet_sync"]["status"], "synced")
                self.assertEqual(writer.call_count, 1)

    def test_failure_keeps_sqlite_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GIM_DB_PATH": str(Path(tmp) / "test.sqlite3"),
            "GIM_GOOGLE_SPREADSHEET_ID": "test-sheet",
            "GIM_EVALUATION_MODE": "demo",
        }):
            with patch("backend.google_sheets.write_row", side_effect=SheetSyncError("google_http_503")):
                first = save_application(candidate(application_id="synthetic-sync-1"))
            self.assertTrue(first["created"])
            self.assertEqual(first["sheet_sync"]["status"], "failed")
            self.assertEqual(first["sheet_sync"]["error"], "google_http_503")
            with db_connection() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 1)
            row_url = "https://docs.google.com/spreadsheets/d/test-sheet/edit#gid=7&range=A2"
            with patch("backend.google_sheets.write_row", return_value=row_url) as writer:
                result = retry_sheet_sync("synthetic-sync-1")
                again = retry_sheet_sync("synthetic-sync-1")
                duplicate = save_application(candidate(application_id="synthetic-sync-1"))
            writer.assert_called_once()
            self.assertEqual(result["sheet_sync"]["status"], "synced")
            self.assertEqual(again["sheet_sync"]["row_url"], row_url)
            self.assertFalse(duplicate["created"])
            with db_connection() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 1)

    def test_google_retry_finds_id_and_updates_existing_row(self):
        class Response:
            ok = True
            def __init__(self, data): self.data = data
            def json(self): return self.data

        class Session:
            def __init__(self): self.calls = []
            def get(self, url, **kwargs):
                self.calls.append(("GET", url, kwargs))
                if url.endswith("/values/'Заявки API'!A:A"):
                    return Response({"values": [["ID заявки"], ["synthetic-sync-1"]]})
                return Response({"sheets": [{"properties": {"sheetId": 7, "title": "Заявки API"}}]})
            def put(self, url, **kwargs):
                self.calls.append(("PUT", url, kwargs))
                return Response({})
            def post(self, url, **kwargs):
                self.calls.append(("POST", url, kwargs))
                return Response({})

        session = Session()
        with patch.dict(os.environ, {"GIM_GOOGLE_SPREADSHEET_ID": "test-sheet"}), \
             patch("backend.google_sheets._client", return_value=session):
            result = write_row("synthetic-sync-1", ["synthetic-sync-1"] + [""] * 31)
        self.assertEqual(result, "https://docs.google.com/spreadsheets/d/test-sheet/edit#gid=7&range=A2")
        self.assertEqual([method for method, _, _ in session.calls], ["GET", "GET", "PUT", "POST"])
        resize = session.calls[-1][2]["json"]["requests"][0]["autoResizeDimensions"]["dimensions"]
        self.assertEqual(resize["endIndex"], 32)


if __name__ == "__main__":
    unittest.main()
