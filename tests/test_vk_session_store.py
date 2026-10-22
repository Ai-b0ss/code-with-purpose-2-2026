from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock,patch
import time
from vk_bot.bot import VolunteerBotFSM,GimApiClient,DialogState

class VkSessionTests(TestCase):
    def test_restart_preserves_consented_draft_and_reset_erases(self):
        with TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'draft.sqlite3')
            fsm=VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient),session_path=path)
            session=fsm.get_or_create_session(42)
            session.consent_personal_data=True;session.state=DialogState.BIRTH_DATE
            fsm.handle_message(42,'15.05.2000')
            other=VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient),session_path=path)
            self.assertEqual(other.sessions[42].birth_date,'2000-05-15')
            self.assertEqual(other.sessions[42].state,DialogState.EMAIL)
            other.handle_message(42,'/reset')
            third=VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient),session_path=path)
            self.assertNotIn(42,third.sessions)

    def test_ttl_expiry_and_no_persistence_without_consent(self):
        with TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'draft.sqlite3')
            fsm=VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient),session_path=path,session_ttl=10)
            fsm.handle_message(1,'Привет');self.assertEqual(fsm.store.load(),[])
            session=fsm.get_or_create_session(1);session.consent_personal_data=True;session.state=DialogState.EMAIL
            fsm.store.save(session)
            with patch('vk_bot.session_store.time.time',return_value=time.time()+11):self.assertEqual(fsm.store.load(),[])

    def test_vk_rejects_underage_and_keeps_correctable_birth_step(self):
        fsm=VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient))
        session=fsm.get_or_create_session(1);session.state=DialogState.BIRTH_DATE
        text,_=fsm.handle_message(1,'01.01.2020')
        self.assertIn('18 лет',text);self.assertEqual(session.state,DialogState.BIRTH_DATE)
        fsm.api_client.get_clarifications.assert_not_called();fsm.api_client.submit_application.assert_not_called()
