"""Issue #57: VK worker failures must never log raw user data or provider secrets."""
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

if importlib.util.find_spec("vk_api") is None:
    raise unittest.SkipTest("VK tests require requirements-vk.txt")

from vk_bot.bot import process_vk_message_event


LEAK = "SYNTHETIC_TEST_SECRET_57 example@invalid.test"
EVENT = SimpleNamespace(type="message_new", message={"from_id": 91234567, "text": LEAK})


class VkPrivateLoggingTests(unittest.TestCase):
    def assert_private_log(self, records, code):
        self.assertTrue(any(code in record.getMessage() for record in records))
        for record in records:
            self.assertNotIn(LEAK, record.getMessage())
            self.assertNotIn("91234567", record.getMessage())
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)

    def test_profile_failure_does_not_log_exception_payload_or_user_id(self):
        vk, fsm = MagicMock(), MagicMock()
        vk.users.get.side_effect = RuntimeError(LEAK)
        fsm.handle_message.return_value = ("Ответ", None)
        with self.assertLogs("gim_vk_bot", level="WARNING") as log:
            process_vk_message_event(EVENT, vk, fsm, {})
        self.assert_private_log(log.records, "profile_fetch_failed error_type=RuntimeError")
        vk.messages.send.assert_called_once()

    def test_worker_failure_does_not_log_exception_message_or_traceback(self):
        vk, fsm = MagicMock(), MagicMock()
        fsm.handle_message.side_effect = RuntimeError(LEAK)
        with self.assertLogs("gim_vk_bot", level="ERROR") as log:
            process_vk_message_event(EVENT, vk, fsm, {91234567: "Тест"})
        self.assert_private_log(log.records, "vk_event_failed error_type=RuntimeError")

    def test_message_send_failure_does_not_log_provider_response(self):
        vk, fsm = MagicMock(), MagicMock()
        fsm.handle_message.return_value = ("Ответ", None)
        vk.messages.send.side_effect = RuntimeError(LEAK)
        with self.assertLogs("gim_vk_bot", level="ERROR") as log:
            process_vk_message_event(EVENT, vk, fsm, {91234567: "Тест"})
        self.assert_private_log(log.records, "vk_event_failed error_type=RuntimeError")
        self.assertEqual(vk.messages.send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
