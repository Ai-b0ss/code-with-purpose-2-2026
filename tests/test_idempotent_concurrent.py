"""SQLite reservation regressions without external models or credentials."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from unittest import TestCase
from unittest.mock import patch
import os
import time

from backend.app import db_connection, evaluate_demo, save_application
from test_smoke import applicant


class ConcurrentSubmissionTests(TestCase):
    def test_same_id_concurrent_submissions_evaluate_once(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GIM_DB_PATH": str(Path(tmp) / "gim.sqlite3")}
        ):
            data = applicant(application_id="test-concurrent-42")
            entered, resume = Event(), Event()
            counter = [0]
            counter_lock = Lock()

            def slow_eval(candidate):
                with counter_lock:
                    counter[0] += 1
                entered.set()
                self.assertTrue(resume.wait(5), "the model call was not released")
                return evaluate_demo(candidate)

            with patch("backend.app.evaluate_application", side_effect=slow_eval):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(save_application, data)
                    self.assertTrue(entered.wait(5), "the evaluation did not begin")
                    second = pool.submit(save_application, data)
                    time.sleep(0.1)
                    resume.set()
                    a, b = first.result(timeout=6), second.result(timeout=6)

            self.assertEqual(counter[0], 1)
            self.assertEqual([a["created"], b["created"]], [True, False])
            self.assertNotIn("evaluation", a)
            self.assertNotIn("evaluation", b)
            with db_connection() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM applications").fetchone()[0], 1)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM application_reservations").fetchone()[0], 0)

    def test_failed_model_releases_reservation_for_retry(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GIM_DB_PATH": str(Path(tmp) / "gim.sqlite3")}
        ):
            data = applicant(application_id="test-failed-model")
            with patch("backend.app.evaluate_application", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    save_application(data)
            with db_connection() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM application_reservations").fetchone()[0], 0)
            with patch("backend.app.evaluate_application", return_value=evaluate_demo(data)):
                result = save_application(data)
            self.assertTrue(result["created"])

    def test_stale_reservation_is_claimed_without_overwriting(self):
        with TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GIM_DB_PATH": str(Path(tmp) / "gim.sqlite3")}
        ):
            data = applicant(application_id="test-crash-recovery")
            with db_connection() as con:
                con.execute("INSERT INTO application_reservations VALUES (?, ?, ?)",
                            (data.application_id, "orphaned", time.time() - 601))
            with patch("backend.app.evaluate_application", return_value=evaluate_demo(data)):
                result = save_application(data)
            self.assertTrue(result["created"])
            with db_connection() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM application_reservations").fetchone()[0], 0)

