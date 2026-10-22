"""Deterministic concurrency checks; no VK or backend requests."""
import threading
import unittest
from unittest.mock import patch

from vk_bot.dispatcher import PerUserDispatcher


class PerUserDispatcherTests(unittest.TestCase):
    def test_busy_user_backlog_does_not_occupy_other_workers(self):
        entered = threading.Event()
        release = threading.Event()
        other_done = threading.Event()
        seen = []

        def handler(event):
            if event == (1, 0):
                entered.set()
                release.wait(5)
            seen.append(event)
            if event == (2, 0):
                other_done.set()

        dispatcher = PerUserDispatcher(handler, max_workers=2)
        try:
            dispatcher.submit(1, (1, 0))
            self.assertTrue(entered.wait(3))
            for index in range(1, 30):
                dispatcher.submit(1, (1, index))
            dispatcher.submit(2, (2, 0))
            self.assertTrue(other_done.wait(3), "User B waited for user A")
            self.assertEqual(seen, [(2, 0)])
        finally:
            release.set()
            dispatcher.close()
        self.assertEqual(
            [event for event in seen if event[0] == 1],
            [(1, index) for index in range(30)],
        )

    def test_handler_failure_does_not_strand_following_events(self):
        seen = []

        def handler(event):
            seen.append(event)
            if event == 1:
                raise ValueError("synthetic private payload")

        dispatcher = PerUserDispatcher(handler, max_workers=2)
        with self.assertLogs("vk_bot.dispatcher", level="ERROR") as logs:
            dispatcher.submit(1, 1)
            dispatcher.submit(1, 2)
            dispatcher.close()
        self.assertEqual(seen, [1, 2])
        self.assertNotIn("synthetic private payload", " ".join(logs.output))

    def test_close_drains_all_accepted_events_and_rejects_new_events(self):
        seen = []
        dispatcher = PerUserDispatcher(seen.append, max_workers=2)
        for index in range(100):
            dispatcher.submit(index % 3, (index % 3, index))
        dispatcher.close()
        for user_id in range(3):
            self.assertEqual(
                [index for uid, index in seen if uid == user_id],
                list(range(user_id, 100, 3)),
            )
        self.assertEqual(dispatcher._queues, {})
        with self.assertRaises(RuntimeError):
            dispatcher.submit(1, "not accepted")
        dispatcher.close()  # Repeated shutdown is harmless.

    def test_failed_executor_submission_does_not_strand_user(self):
        seen = []
        dispatcher = PerUserDispatcher(seen.append, max_workers=1)
        try:
            with patch.object(
                dispatcher._executor, "submit", side_effect=RuntimeError("stopped")
            ):
                with self.assertRaises(RuntimeError):
                    dispatcher.submit(1, "rejected")
            dispatcher.submit(1, "accepted")
        finally:
            dispatcher.close()
        self.assertEqual(seen, ["accepted"])

    def test_same_user_can_resume_after_queue_becomes_idle(self):
        seen = []
        dispatcher = PerUserDispatcher(seen.append, max_workers=1)
        try:
            for index in range(20):
                dispatcher.submit(1, index)
                # A sentinel on the same executor runs after the drain exits.
                dispatcher._executor.submit(lambda: None).result(timeout=3)
                with dispatcher._lock:
                    self.assertNotIn(1, dispatcher._queues)
        finally:
            dispatcher.close()
        self.assertEqual(seen, list(range(20)))


if __name__ == "__main__":
    unittest.main()
