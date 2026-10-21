import unittest
from unittest.mock import MagicMock
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
