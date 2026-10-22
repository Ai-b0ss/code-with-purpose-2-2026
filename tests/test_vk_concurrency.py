"""VK worker regressions with fake network clients and temporary SQLite."""
import importlib.util
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if importlib.util.find_spec("vk_api") is None:
    raise unittest.SkipTest("VK tests require requirements-vk.txt")

from vk_bot.bot import (
    BotConfig, DialogState, GimApiClient, ThreadLocalVkClient,
    VolunteerBotFSM, event_user_id, process_vk_message_event, run_bot,
)
from vk_bot.dispatcher import PerUserDispatcher


def message(user_id, text="Привет"):
    return SimpleNamespace(
        type="message_new", message={"from_id": user_id, "text": text}
    )


class VkConcurrencyTests(unittest.TestCase):
    def test_event_filter_accepts_only_positive_user_ids(self):
        for invalid in (None, "invalid", 0, -10, True, 1.5, [], {}):
            with self.subTest(user_id=invalid):
                self.assertIsNone(event_user_id(message(invalid)))
        self.assertEqual(event_user_id(message("123")), 123)
        self.assertEqual(
            event_user_id(SimpleNamespace(type="message_new", obj={"user_id": 8})),
            8,
        )
        self.assertIsNone(event_user_id(SimpleNamespace(type="message_reply")))
        self.assertIsNone(event_user_id(SimpleNamespace(type="message_new", obj=[])))

    def test_vk_client_is_lazy_reused_and_separate_per_thread(self):
        barrier = threading.Barrier(2)
        factory_threads = []

        def factory(**kwargs):
            factory_threads.append(threading.get_ident())
            return SimpleNamespace(get_api=lambda: object())

        with patch("vk_bot.bot.vk_api.VkApi", side_effect=factory) as create:
            client = ThreadLocalVkClient("synthetic-token")
            create.assert_not_called()

            def use_client():
                first = client.get_api()
                barrier.wait(timeout=3)
                return first, client.get_api()

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(use_client) for _ in range(2)]
                results = [future.result(timeout=5) for future in futures]
            self.assertEqual(create.call_count, 2)
        self.assertIs(results[0][0], results[0][1])
        self.assertIs(results[1][0], results[1][1])
        self.assertIsNot(results[0][0], results[1][0])
        self.assertEqual(len(set(factory_threads)), 2)

    def test_handler_never_replaces_custom_notifier(self):
        notifier = MagicMock()
        fsm = VolunteerBotFSM(MagicMock(spec=GimApiClient), notifier=notifier)
        vk = MagicMock()
        process_vk_message_event(message(1), vk, fsm, {1: "Тест"})
        self.assertIs(fsm.notifier, notifier)
        vk.messages.send.assert_called_once()

    def test_parallel_session_writes_remain_isolated_in_sqlite(self):
        with TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "sessions.sqlite3")
            fsm = VolunteerBotFSM(MagicMock(spec=GimApiClient), session_path=path)
            for user_id in range(1, 9):
                session = fsm.get_or_create_session(user_id)
                session.consent_personal_data = True
                session.state = DialogState.FULL_NAME
            barrier = threading.Barrier(8)

            def handle(user_id):
                barrier.wait(timeout=5)
                fsm.handle_message(user_id, f"Тестовый Пользователь {user_id}")

            dispatcher = PerUserDispatcher(handle, max_workers=8)
            try:
                for user_id in range(1, 9):
                    dispatcher.submit(user_id, user_id)
            finally:
                dispatcher.close()
            restored = VolunteerBotFSM(MagicMock(spec=GimApiClient), session_path=path)
            self.assertEqual(set(restored.sessions), set(range(1, 9)))
            for user_id, session in restored.sessions.items():
                self.assertEqual(session.applicant_name, f"Тестовый Пользователь {user_id}")
                self.assertEqual(session.state, DialogState.BIRTH_DATE)

    def test_long_poll_answers_other_user_while_backend_waits_and_drains_on_exit(self):
        backend_entered = threading.Event()
        backend_release = threading.Event()
        other_answered = threading.Event()
        lock = threading.Lock()
        sent = []
        profile_threads = []
        observations = {}
        instances = []
        main_thread = threading.get_ident()
        api = MagicMock(spec=GimApiClient)

        def submit(payload):
            backend_entered.set()
            if not backend_release.wait(5):
                raise TimeoutError("test gate was not released")
            return {"application_id": payload["application_id"]}

        api.submit_application.side_effect = submit

        def send(**kwargs):
            with lock:
                sent.append((threading.get_ident(), kwargs["user_id"], kwargs["message"]))
            if kwargs["user_id"] == 2:
                other_answered.set()

        def profile(**kwargs):
            with lock:
                profile_threads.append(threading.get_ident())
            return [{"first_name": "Тест", "last_name": "Пользователь"}]

        def vk_factory(**kwargs):
            vk = MagicMock()
            vk.messages.send.side_effect = send
            vk.users.get.side_effect = profile
            return SimpleNamespace(get_api=lambda: vk)

        def listen():
            # Restarting the Long Poll generator must preserve the dispatcher.
            if not observations.get("reconnected"):
                observations["reconnected"] = True
                from requests.exceptions import ReadTimeout
                raise ReadTimeout("synthetic polling timeout")
            try:
                yield message(1, "Повторить отправку")
                observations["backend_started"] = backend_entered.wait(3)
                yield message(1, "/reset")
                yield message(1, "Начать заполнение")
                yield message(2)
                observations["other_answered_before_release"] = other_answered.wait(3)
                observations["first_state_before_release"] = instances[0].sessions[1].state
            finally:
                backend_release.set()
            raise KeyboardInterrupt

        with TemporaryDirectory() as temporary:
            def fsm_factory(**kwargs):
                kwargs["session_path"] = str(Path(temporary) / "sessions.sqlite3")
                fsm = VolunteerBotFSM(**kwargs)
                first = fsm.get_or_create_session(1)
                first.state = DialogState.SUBMIT_RETRY
                first.consent_personal_data = True
                instances.append(fsm)
                return fsm

            with (
                patch("vk_bot.bot.load_config", return_value=BotConfig("synthetic", 123)),
                patch("vk_bot.bot.GimApiClient", return_value=api),
                patch("vk_bot.bot.VolunteerBotFSM", side_effect=fsm_factory),
                patch("vk_bot.bot.vk_api.VkApi", side_effect=vk_factory),
                patch("vk_bot.bot.VkBotLongPoll") as longpoll,
            ):
                longpoll.return_value.listen.side_effect = listen
                with self.assertRaises(KeyboardInterrupt):
                    run_bot()

        self.assertTrue(observations["backend_started"])
        self.assertTrue(observations["other_answered_before_release"])
        self.assertEqual(observations["first_state_before_release"], DialogState.SUBMIT_RETRY)
        self.assertEqual(instances[0].sessions[1].state, DialogState.CONSENT)
        api.submit_application.assert_called_once()
        self.assertTrue(profile_threads)
        self.assertTrue(all(tid != main_thread for tid in profile_threads))
        self.assertTrue(all(tid != main_thread for tid, _, _ in sent))
        first_replies = [text for _, uid, text in sent if uid == 1]
        self.assertEqual(len(first_replies), 4)  # Status, success, reset, consent.
        self.assertIn("сохраняет заявку", first_replies[0])
        self.assertIn("успешно принята", first_replies[1])
        self.assertEqual(longpoll.return_value.listen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
