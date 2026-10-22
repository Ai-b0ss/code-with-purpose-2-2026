import unittest
from unittest.mock import MagicMock
import requests
import importlib.util
if importlib.util.find_spec("vk_api") is None:
    raise unittest.SkipTest("VK tests require requirements-vk.txt")

from vk_bot.bot import DialogState, GimApiClient, VolunteerBotFSM


class VkAgentIntegrationTests(unittest.TestCase):
    def test_clarification_failure_never_skips_to_submission_or_overwrites_profile(self):
        api = MagicMock(spec=GimApiClient)
        api.get_clarifications.side_effect = [RuntimeError("failed"), {"questions": [], "round": 1}]
        api.submit_application.return_value = {"application_id": "qa-vk"}
        fsm = VolunteerBotFSM(api)
        session = fsm.get_or_create_session(123, "Тест")
        session.state = DialogState.OTHER_VOLUNTEERING
        session.other_commitments = "Свободен по субботам"
        first, _ = fsm.handle_message(123, "Нет других программ")
        self.assertIn("ещё не отправлена", first)
        api.submit_application.assert_not_called()
        self.assertEqual(session.state, DialogState.CHECK_RETRY)
        fsm.handle_message(123, "Повторить")
        self.assertEqual(session.other_commitments, "Свободен по субботам")
        self.assertEqual(session.other_volunteer_programs, "Нет других программ")
        api.submit_application.assert_called_once()

    def test_questions_and_negative_answer_survive_both_rounds(self):
        api = MagicMock(spec=GimApiClient)
        q = {"id": "ai_availability", "question": "Вы можете участвовать по субботам?", "reason": "Нужно уточнить график."}
        api.get_clarifications.side_effect = [{"questions": [q], "round": 1}, {"questions": [], "round": 2}]
        api.submit_application.return_value = {"application_id": "qa-vk"}
        fsm = VolunteerBotFSM(api)
        session = fsm.get_or_create_session(123, "Тест")
        session.state = DialogState.OTHER_VOLUNTEERING
        fsm.handle_message(123, "Нет")
        fsm.handle_message(123, "Нет")
        sent = api.submit_application.call_args.args[0]
        self.assertEqual(sent["followup_answers"][q["id"]], "Нет")
        self.assertEqual(sent["followup_questions"][0]["question"], q["question"])
        self.assertEqual(sent["clarification_round"], 2)

    def test_processing_notice_precedes_backend_and_retry_keeps_same_id(self):
        api = MagicMock(spec=GimApiClient)
        seen = []
        def notify(uid, msg):
            seen.append((uid, msg))
        def submit(payload):
            self.assertTrue(seen)
            raise RuntimeError("temporary backend error")
        api.submit_application.side_effect = submit
        fsm = VolunteerBotFSM(api, notifier=notify)
        session = fsm.get_or_create_session(808, "Тест")
        session.state = DialogState.SUBMIT_RETRY
        session.motivation = "Хочу участвовать в программе"
        session.consent_personal_data = True
        first, keyboard = fsm.handle_message(808, "Повторить отправку")
        self.assertIn("Повторить отправку", first)
        self.assertIn("Повторить отправку", keyboard)
        first_id = api.submit_application.call_args.args[0]["application_id"]
        self.assertEqual(session.state, DialogState.SUBMIT_RETRY)
        api.submit_application.side_effect = None
        api.submit_application.return_value = {"application_id": first_id}
        second, _ = fsm.handle_message(808, "Повторить отправку")
        self.assertIn("успешно принята", second)
        self.assertEqual(api.submit_application.call_args.args[0]["application_id"], first_id)
        self.assertEqual(session.state, DialogState.DONE)

    def test_notices_distinguish_clarification_from_submission(self):
        api = MagicMock(spec=GimApiClient)
        api.get_clarifications.return_value = {"questions": [], "round": 1}
        api.submit_application.return_value = {"application_id": "qa-vk"}
        notices = []
        fsm = VolunteerBotFSM(api, notifier=lambda uid, msg: notices.append(msg))
        session = fsm.get_or_create_session(810, "Тест")
        session.state = DialogState.OTHER_VOLUNTEERING
        fsm.handle_message(810, "Нет")
        self.assertEqual(len(notices), 2)
        self.assertIn("Анкета пока не отправлена", notices[0])
        self.assertIn("сохраняет заявку", notices[1])

    def test_provider_503_retry_preserves_followup_and_needs_explicit_command(self):
        api = MagicMock(spec=GimApiClient)
        response = requests.Response()
        response.status_code = 503
        response._content = b'{"detail":"evaluator_provider_timeout"}'
        error = requests.HTTPError(response=response)
        api.submit_application.side_effect = [error, {"application_id": "qa-vk", "created": False}]
        fsm = VolunteerBotFSM(api)
        session = fsm.get_or_create_session(811, "Тест")
        session.state = DialogState.SUBMIT_RETRY
        session.followup_answers = {"q1": "Да"}
        session.pending_clarifications = []
        first, _ = fsm.handle_message(811, "Повторить отправку")
        self.assertIn("Не удалось завершить оценку ИИ", first)
        first_payload = api.submit_application.call_args.args[0]
        blocked, _ = fsm.handle_message(811, "Случайное сообщение")
        self.assertIn("Повторить отправку", blocked)
        self.assertEqual(api.submit_application.call_count, 1)
        fsm.handle_message(811, "Повторить отправку")
        second_payload = api.submit_application.call_args.args[0]
        self.assertEqual(first_payload["application_id"], second_payload["application_id"])
        self.assertEqual(second_payload["followup_answers"], {"q1": "Да"})
        self.assertEqual(session.state, DialogState.DONE)

    def test_processing_notice_does_not_skip_required_clarifications(self):
        api = MagicMock(spec=GimApiClient)
        q = {"id": "ai_motivation", "question": "Почему хотите участвовать?", "reason": "Недостаточно сведений"}
        api.get_clarifications.return_value = {"round": 1, "questions": [q]}
        seen = []
        fsm = VolunteerBotFSM(api, notifier=lambda uid, msg: seen.append((uid, msg)))
        session = fsm.get_or_create_session(809, "Тест")
        session.state = DialogState.CHECK_RETRY
        msg, _ = fsm.handle_message(809, "Повторить")
        self.assertTrue(seen)
        self.assertIn("Почему хотите участвовать?", msg)
        api.submit_application.assert_not_called()
        self.assertEqual(session.state, DialogState.CLARIFICATION)

    def test_vk_client_timeout_covers_full_model_call(self):
        self.assertGreaterEqual(GimApiClient().timeout, 180)
