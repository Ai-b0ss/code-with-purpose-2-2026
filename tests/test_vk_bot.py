"""Unit-тесты для чат-бота ВКонтакте волонтёрской программы ГИМ (vk_bot/bot.py)."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

import importlib.util
if importlib.util.find_spec("vk_api") is None:
    raise unittest.SkipTest("VK tests require requirements-vk.txt")

from vk_bot.bot import (
    DIRECTION_MAPPING,
    BotConfig,
    DialogState,
    GimApiClient,
    UserSession,
    VkKeyboards,
    VolunteerBotFSM,
    load_config,
    parse_and_validate_birth_date,
    process_vk_message_event,
)
from backend.app import ApplicationInput
from vk_api.bot_longpoll import VkBotEventType


class VkBotUnitTests(unittest.TestCase):
    """Тестирование логики, клавиатур, FSM и валидации VK-бота."""

    def test_keyboards_are_valid_json(self):
        """Проверка, что все генерируемые клавиатуры возвращают валидный JSON."""
        keyboards = [
            VkKeyboards.start(),
            VkKeyboards.consent(),
            VkKeyboards.skip("Пропустить"),
            VkKeyboards.course(),
            VkKeyboards.languages(),
            VkKeyboards.experience(),
            VkKeyboards.directions_inline(),
            VkKeyboards.hours(),
            VkKeyboards.schedule(),
            VkKeyboards.days(),
            VkKeyboards.commitments(),
            VkKeyboards.clarification_skip(),
            VkKeyboards.finished(),
            VkKeyboards.empty(),
        ]
        for kb_str in keyboards:
            self.assertIsInstance(kb_str, str)
            parsed = json.loads(kb_str)
            self.assertIn("buttons", parsed)

    def test_birth_date_parser(self):
        """Проверка парсинга и валидации даты рождения."""
        self.assertEqual(parse_and_validate_birth_date("15.05.2003"), "2003-05-15")
        self.assertEqual(parse_and_validate_birth_date("01-12-1999"), "1999-12-01")
        self.assertEqual(parse_and_validate_birth_date("15/05/2003"), "2003-05-15")
        self.assertIsNone(parse_and_validate_birth_date("2001-07-20"))
        self.assertIsNone(parse_and_validate_birth_date("15 / 05 / 2003"))
        self.assertIsNone(parse_and_validate_birth_date("15 мая 2003 года"))
        self.assertIsNone(parse_and_validate_birth_date("31.02.2000"))
        self.assertIsNone(parse_and_validate_birth_date("01.01.2099"))
        self.assertIsNone(parse_and_validate_birth_date("не дата"))

    def test_direction_mapping(self):
        """Проверка сопоставления названий направлений (с эмодзи и без)."""
        self.assertEqual(DIRECTION_MAPPING["фондовое"], "collections")
        self.assertEqual(DIRECTION_MAPPING["🏛️ фондовое"], "collections")
        self.assertEqual(DIRECTION_MAPPING["🎭 проектное (квесты)"], "project")
        self.assertEqual(DIRECTION_MAPPING["🎪 организационное"], "organizational")
        self.assertEqual(DIRECTION_MAPPING["🤝 инклюзивное"], "inclusive")
        self.assertEqual(DIRECTION_MAPPING["📸 медиа-волонтёрство"], "media")
        self.assertEqual(DIRECTION_MAPPING["🤔 пока не определился"], "undecided")

    def test_ux_keyboards_do_not_offer_shallow_answers(self):
        """Кнопки не должны подменять обязательные развёрнутые ответы."""
        self.assertFalse(hasattr(VkKeyboards, "full_name_hint"))
        self.assertFalse(hasattr(VkKeyboards, "status"))
        self.assertIn("Выпускник / Не учусь", VkKeyboards.course())
        self.assertNotIn("1 курс", VkKeyboards.course())
        self.assertIn("Английский (B1-B2)", VkKeyboards.languages())
        self.assertIn("Английский (C1-C2)", VkKeyboards.languages())
        self.assertIn("Немецкий (B1+)", VkKeyboards.languages())
        self.assertIn('"label":"8"', VkKeyboards.hours())
        self.assertNotIn("8 часов", VkKeyboards.hours())

    def test_full_name_requires_patronymic_or_explicit_no_patronymic(self):
        """Два слова не переводят FSM дальше шага обязательного ФИО."""
        fsm = VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient))
        user_id = 1009

        fsm.handle_message(user_id, "Привет", user_name="Илья Соколов")
        fsm.handle_message(user_id, "Начать заполнение")
        fsm.handle_message(user_id, "Согласен(на), мне есть 18 лет")

        resp, _ = fsm.handle_message(user_id, "Соколов Илья")
        self.assertIn("отчество", resp)
        self.assertEqual(fsm.sessions[user_id].state, DialogState.FULL_NAME)

        resp, _ = fsm.handle_message(user_id, "Соколов Илья нет")
        self.assertIn("дату рождения", resp)
        self.assertEqual(fsm.sessions[user_id].applicant_name, "Соколов Илья нет")

    def test_hours_accept_zero_number_or_phrase_but_reject_overflow(self):
        """Нулевая доступность сохраняется как факт, а не блокирует анкету."""
        fsm = VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient))
        session = fsm.get_or_create_session(1010, "Иван")
        session.state = DialogState.HOURS

        resp, _ = fsm.handle_message(1010, "0")
        self.assertEqual(session.availability_hours_month, 0)
        self.assertEqual(session.state, DialogState.SCHEDULE)

        session.state = DialogState.HOURS
        resp, _ = fsm.handle_message(1010, "20 часов в месяц")
        self.assertIn("график участия", resp)
        self.assertEqual(session.availability_hours_month, 20)
        self.assertEqual(session.state, DialogState.SCHEDULE)

        session.state = DialogState.HOURS
        resp, _ = fsm.handle_message(1010, "401")
        self.assertIn("от 0 до 400", resp)
        self.assertEqual(session.state, DialogState.HOURS)

    def test_status_is_manual_and_not_silently_truncated(self):
        fsm = VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient))
        session = fsm.get_or_create_session(1012, "Иван")
        session.state = DialogState.STATUS

        too_long = "Студент дневного отделения и работаю по сменному графику, регулярно участвую в проектах"
        resp, _ = fsm.handle_message(1012, too_long)

        self.assertIn("слишком длинным", resp)
        self.assertEqual(session.state, DialogState.STATUS)

    def test_language_buttons_are_normalized_to_russian_values(self):
        fsm = VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient))
        session = fsm.get_or_create_session(1011, "Иван")
        session.state = DialogState.LANGUAGES

        resp, _ = fsm.handle_message(1011, "Английский (B1-B2)")
        self.assertIn("Мотивация и опыт", resp)
        self.assertEqual(session.foreign_languages, ["Английский B1-B2"])

    @patch("vk_bot.config.load_dotenv_custom")
    def test_config_loader_when_missing(self, mock_dotenv):
        """Проверка возврата None при отсутствии VK_GROUP_TOKEN."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_config()
            self.assertIsNone(cfg)

    @patch("vk_bot.config.load_dotenv_custom")
    def test_config_loader_when_present(self, mock_dotenv):
        """Проверка корректного парсинга параметров окружения."""
        env = {
            "VK_GROUP_TOKEN": "test_token_12345",
            "VK_GROUP_ID": "club987654321",
            "BACKEND_URL": "http://127.0.0.1:8765/",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_config()
            self.assertIsNotNone(cfg)
            self.assertEqual(cfg.token, "test_token_12345")
            self.assertEqual(cfg.group_id, 987654321)
            self.assertEqual(cfg.backend_url, "http://127.0.0.1:8765")

    def test_full_happy_path_fsm(self):
        """Сквозной тест сценария анкеты со всеми полями от приветствия до финализации."""
        mock_api = MagicMock(spec=GimApiClient)
        mock_api.get_clarifications.return_value = {
            "questions": [
                {
                    "id": "collections_focus",
                    "category": "Фондовое направление",
                    "question": "Какие именно коллекции вас интересуют?",
                    "reason": "Для подбора отдела хранения",
                }
            ]
        }
        mock_api.submit_application.return_value = {
            "application_id": "GIM-TEST-777",
            "created": True,
        }

        fsm = VolunteerBotFSM(api_client=mock_api)
        user_id = 1001

        # 0. Приветствие
        resp, kb = fsm.handle_message(user_id, "Привет", user_name="Алексей Смирнов")
        self.assertIn("Здравствуйте, Алексей Смирнов!", resp)

        # 1. Начать заполнение -> Возраст и согласие 152-ФЗ
        resp, kb = fsm.handle_message(user_id, "Начать заполнение")
        self.assertIn("Возраст и согласие", resp)

        # 2. Согласие 18+ -> Запрос полного ФИО
        resp, kb = fsm.handle_message(user_id, "Согласен(на), мне есть 18 лет")
        self.assertIn("полное ФИО", resp)
        self.assertNotIn("Использовать имя", resp)

        # 3. Ввод ФИО с отчеством -> Дата рождения
        resp, kb = fsm.handle_message(user_id, "Смирнов Алексей Иванович")
        self.assertIn("дату рождения", resp)

        # 3.1 Невалидная дата рождения -> повторный запрос
        resp, kb = fsm.handle_message(user_id, "31.02.2003")
        self.assertIn("только цифрами", resp)

        # 3.2 Валидная дата рождения -> Email
        resp, kb = fsm.handle_message(user_id, "15.05.2003")
        self.assertIn("Email", resp)
        self.assertIn("https://vk.com/id1001", resp)

        # 4. Email -> Статус
        resp, kb = fsm.handle_message(user_id, "alex@example.com")
        self.assertIn("Статус", resp)

        # 5. Статус -> Место учёбы / ВУЗ
        resp, kb = fsm.handle_message(user_id, "Студент дневного отделения (пары до 16:00)")
        self.assertIn("место учёбы", resp)

        # 6. Место учёбы -> Факультет
        resp, kb = fsm.handle_message(user_id, "МГУ им. М.В. Ломоносова")
        self.assertIn("факультет", resp)

        # 7. Факультет -> Курс
        resp, kb = fsm.handle_message(user_id, "Исторический факультет")
        self.assertIn("курс", resp)

        # 8. Курс -> Научные интересы
        resp, kb = fsm.handle_message(user_id, "2 курс бакалавриата")
        self.assertIn("научных или профессиональных интересов", resp)

        # 9. Научные интересы -> Иностранные языки
        resp, kb = fsm.handle_message(user_id, "История России XIX века")
        self.assertIn("иностранными языками", resp)

        # 10. Иностранные языки -> Мотивация
        resp, kb = fsm.handle_message(user_id, "Английский B2")
        self.assertIn("Мотивация и опыт", resp)

        # 11.1 Слишком короткая мотивация (<10 символов) -> ошибка
        resp, kb = fsm.handle_message(user_id, "Кратко")
        self.assertIn("минимум 10 символов", resp)

        # 11.2 Корректная мотивация -> Опыт
        resp, kb = fsm.handle_message(user_id, "Хочу помогать сохранять историческое наследие России!")
        self.assertIn("ожидаете", resp)
        resp, kb = fsm.handle_message(user_id, "Хочу научиться работе в музее, без ожиданий найма")
        self.assertIn("волонтёрский опыт", resp)

        # 12. Опыт -> Направление
        resp, kb = fsm.handle_message(user_id, "Опыта нет, но хочу научиться!")
        self.assertIn("хобби", resp)
        resp, kb = fsm.handle_message(user_id, "Чтение исторических книг")
        self.assertIn("Откуда узнали", resp)
        resp, kb = fsm.handle_message(user_id, "Социальные сети")
        self.assertIn("Направление и график", resp)

        # 13. Выбор направления -> Часы
        resp, kb = fsm.handle_message(user_id, "🏛️ Фондовое")
        self.assertIn("Сколько часов в месяц", resp)

        # 14. Часы -> График
        resp, kb = fsm.handle_message(user_id, "16 часов")
        self.assertIn("Какой график участия", resp)

        # 15. График -> Доступные дни
        resp, kb = fsm.handle_message(user_id, "Гибкий график")
        self.assertIn("В какие дни недели", resp)

        # 16. Доступные дни -> Другие обязательства
        resp, kb = fsm.handle_message(user_id, "Выходные (Сб, Вс)")
        self.assertIn("другие регулярные обязательства", resp)

        # 17. Другие обязательства -> Запрос уточнений от кураторов
        resp, kb = fsm.handle_message(user_id, "Только учёба")
        self.assertIn("других волонтёрских", resp)
        resp, kb = fsm.handle_message(user_id, "Нет")
        self.assertIn("Уточняющий вопрос от кураторов", resp)
        self.assertIn("Какие именно коллекции вас интересуют?", resp)

        # 18. Ответ на уточнение -> Сохранение заявки
        resp, kb = fsm.handle_message(user_id, "Интересуют рукописи и древнерусская живопись")
        self.assertIn("🎉 Ваша заявка успешно принята!", resp)
        self.assertIn("#GIM-TEST-777", resp)

        # Проверяем переданные в бэкенд данные
        mock_api.submit_application.assert_called_once()
        sent = mock_api.submit_application.call_args[0][0]
        self.assertEqual(sent["applicant_name"], "Смирнов Алексей Иванович")
        self.assertEqual(sent["birth_date"], "2003-05-15")
        self.assertEqual(sent["email"], "alex@example.com")
        self.assertEqual(sent["status"], "Студент дневного отделения (пары до 16:00)")
        self.assertEqual(sent["university"], "МГУ им. М.В. Ломоносова")
        self.assertEqual(sent["faculty"], "Исторический факультет")
        self.assertEqual(sent["course"], "2 курс бакалавриата")
        self.assertEqual(sent["scientific_interests"], "История России XIX века")
        self.assertEqual(sent["foreign_languages"], ["Английский B2"])
        self.assertEqual(sent["desired_direction"], "collections")
        self.assertEqual(sent["availability_hours_month"], 16)
        self.assertEqual(sent["availability_schedule_type"], "Гибкий")
        self.assertEqual(sent["availability_days"], ["Выходные (Сб, Вс)"])
        self.assertEqual(sent["other_commitments"], "Только учёба")
        self.assertTrue(sent["consent_personal_data"])
        self.assertTrue(sent["age_over_18"])
        self.assertEqual(
            sent["followup_answers"]["collections_focus"],
            "Интересуют рукописи и древнерусская живопись",
        )

    def test_consent_refusal_resets_dialog(self):
        """Проверка отказа от согласия 152-ФЗ."""
        mock_api = MagicMock(spec=GimApiClient)
        fsm = VolunteerBotFSM(api_client=mock_api)
        user_id = 1002

        fsm.handle_message(user_id, "Привет")
        fsm.handle_message(user_id, "Начать заполнение")
        resp, _ = fsm.handle_message(user_id, "Отказаться")
        self.assertIn("без согласия на обработку персональных данных", resp)

        session = fsm.sessions[user_id]
        self.assertEqual(session.state, DialogState.START)

    def test_negative_consent_cannot_match_positive_substring(self):
        """Фраза «не согласен» не должна ошибочно считаться согласием."""
        fsm = VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient))
        user_id = 1005

        fsm.handle_message(user_id, "Привет")
        fsm.handle_message(user_id, "Начать заполнение")
        resp, _ = fsm.handle_message(user_id, "Не согласен")

        self.assertIn("без согласия", resp)
        self.assertEqual(fsm.sessions[user_id].state, DialogState.START)
        self.assertFalse(fsm.sessions[user_id].consent_personal_data)

    def test_reset_command_at_any_step(self):
        """Команда /reset или «Заново» сбрасывает сессию пользователя."""
        mock_api = MagicMock(spec=GimApiClient)
        fsm = VolunteerBotFSM(api_client=mock_api)
        user_id = 1003

        fsm.handle_message(user_id, "Начать")
        fsm.handle_message(user_id, "Согласен(на), мне есть 18 лет")
        resp, kb = fsm.handle_message(user_id, "/reset")
        self.assertIn("Здравствуйте", resp)

        session = fsm.sessions[user_id]
        self.assertEqual(session.state, DialogState.START)

    def test_backend_submission_error_handling(self):
        """При сбое API бэкенда бот выдаёт понятное сообщение об ошибке, не падая."""
        mock_api = MagicMock(spec=GimApiClient)
        mock_api.get_clarifications.return_value = {"questions": []}
        mock_api.submit_application.side_effect = RuntimeError("Backend down 500")

        fsm = VolunteerBotFSM(api_client=mock_api)
        user_id = 1004

        session = fsm.get_or_create_session(user_id, "Иван")
        session.state = DialogState.OTHER_VOLUNTEERING
        session.age_over_18 = True
        session.consent_personal_data = True
        session.motivation = "Достаточная мотивация для музея"
        session.desired_direction = "project"

        resp, _ = fsm.handle_message(user_id, "Только учёба")
        self.assertIn("Не удалось отправить заявку", resp)
        self.assertIn("ответы сохранены", resp.casefold())

    def test_application_payload_matches_backend_contract(self):
        """Payload VK-бота должен проходить ApplicationInput без потери типов."""
        session = VolunteerBotFSM(api_client=MagicMock(spec=GimApiClient)).get_or_create_session(
            1006, "Анна Иванова"
        )
        session.application_id = "vk-1006-contract"
        session.age_over_18 = True
        session.consent_personal_data = True
        session.birth_date = "2003-05-15"
        session.email = "anna@example.org"
        session.status = "Студент дневного отделения (пары до 16:00)"
        session.motivation = "Хочу помогать музею и работать с посетителями."
        session.desired_direction = "organizational"
        session.availability_hours_month = 16
        session.foreign_languages = ["Английский B2"]
        session.availability_days = ["Выходные (Сб, Вс)"]

        payload = session.to_application_input()
        validated = ApplicationInput(**payload)

        self.assertEqual(validated.application_id, "vk-1006-contract")
        self.assertIsInstance(payload["availability_hours_month"], int)
        self.assertIsInstance(payload["foreign_languages"], list)
        self.assertIsInstance(payload["availability_days"], list)
        self.assertIsInstance(payload["age_over_18"], bool)
        self.assertIsInstance(payload["consent_personal_data"], bool)
        self.assertEqual(payload["status"], "Студент дневного отделения (пары до 16:00)")
        self.assertEqual(payload["availability_schedule_type"], "Гибкий")
        self.assertEqual(payload["source"], session.source)

    def test_submission_reuses_idempotency_key_after_transient_failure(self):
        """Повторная отправка после потери ответа не создаёт новую заявку."""
        mock_api = MagicMock(spec=GimApiClient)
        mock_api.get_clarifications.return_value = {"questions": []}
        mock_api.submit_application.side_effect = [
            RuntimeError("temporary backend failure"),
            {"application_id": "vk-1007-stable", "created": False},
        ]

        fsm = VolunteerBotFSM(api_client=mock_api)
        session = fsm.get_or_create_session(1007, "Иван")
        session.state = DialogState.OTHER_VOLUNTEERING
        session.age_over_18 = True
        session.consent_personal_data = True
        session.motivation = "Достаточная мотивация для участия в музейной программе."
        session.desired_direction = "project"

        first, _ = fsm.handle_message(1007, "Только учёба")
        second, _ = fsm.handle_message(1007, "Повторить отправку")

        self.assertIn("Не удалось отправить заявку", first)
        self.assertIn("успешно принята", second)
        first_payload = mock_api.submit_application.call_args_list[0].args[0]
        second_payload = mock_api.submit_application.call_args_list[1].args[0]
        self.assertTrue(first_payload["application_id"])
        self.assertEqual(first_payload["application_id"], second_payload["application_id"])

    def test_long_poll_event_error_isolated_from_next_event(self):
        """Ошибка FSM одного сообщения не должна пробрасываться из event handler."""
        vk = MagicMock()
        vk.users.get.return_value = [{"first_name": "Тест", "last_name": "Пользователь"}]
        fsm = MagicMock()
        fsm.handle_message.side_effect = RuntimeError("broken user handler")
        event = MagicMock()
        event.type = VkBotEventType.MESSAGE_NEW
        event.message = {"from_id": 1008, "text": "Привет"}

        process_vk_message_event(event, vk, fsm, {})

        vk.messages.send.assert_not_called()

    def test_free_form_arbitrary_inputs_fsm(self):
        """Проверка, что произвольный текст не блокирует соискателя на любом этапе."""
        mock_api = MagicMock(spec=GimApiClient)
        mock_api.get_clarifications.return_value = {"questions": []}
        mock_api.submit_application.return_value = {"application_id": "GIM-FREE-1", "created": True}

        fsm = VolunteerBotFSM(api_client=mock_api)
        uid = 2001

        fsm.handle_message(uid, "Привет", user_name="Василий Петров")
        fsm.handle_message(uid, "Начать заполнение")
        fsm.handle_message(uid, "Согласен(на), мне есть 18 лет")
        fsm.handle_message(uid, "Петров Василий Сергеевич")

        # Дата словами отклоняется, числовой формат принимается
        resp, _ = fsm.handle_message(uid, "24 июня 2001 года")
        self.assertIn("только цифрами", resp)
        self.assertEqual(fsm.sessions[uid].state, DialogState.BIRTH_DATE)

        resp, _ = fsm.handle_message(uid, "24/06/2001")
        self.assertIn("Email", resp)
        self.assertEqual(fsm.sessions[uid].birth_date, "2001-06-24")

        fsm.handle_message(uid, "petrov@example.org")

        # Статус сохраняется развёрнутым русским текстом, без технического кода.
        resp, _ = fsm.handle_message(uid, "Самозанятый экскурсовод, свободный график")
        self.assertIn("место учёбы", resp)
        self.assertEqual(fsm.sessions[uid].status, "Самозанятый экскурсовод, свободный график")

        fsm.handle_message(uid, "РГГУ")
        fsm.handle_message(uid, "Факультет истории искусств")

        # Произвольный курс
        resp, _ = fsm.handle_message(uid, "Магистратура 2 курс")
        self.assertIn("научных или профессиональных интересов", resp)
        self.assertEqual(fsm.sessions[uid].course, "Магистратура 2 курс")

        fsm.handle_message(uid, "Древнерусская архитектура")

        # Произвольные языки (автоматически разбиваются по запятой)
        resp, _ = fsm.handle_message(uid, "Французский B2, Немецкий B1")
        self.assertIn("Мотивация и опыт", resp)
        self.assertEqual(fsm.sessions[uid].foreign_languages, ["Французский B2", "Немецкий B1"])

        fsm.handle_message(uid, "Обожаю Исторический музей с детства!")
        fsm.handle_message(uid, "Хочу помогать гостям и учиться музейной работе")
        fsm.handle_message(uid, "Проводил городские экскурсии 2 года")
        fsm.handle_message(uid, "Исторические книги")
        fsm.handle_message(uid, "Рекомендации друзей")

        # Произвольное направление со смысловым словом 'архив'
        resp, _ = fsm.handle_message(uid, "Очень хочу в архивы и музейные фонды")
        self.assertIn("Сколько часов в месяц", resp)
        self.assertEqual(fsm.sessions[uid].desired_direction, "collections")

        # Произвольные часы с числом
        resp, _ = fsm.handle_message(uid, "Смогу уделять около 18 часов в месяц")
        self.assertIn("Какой график участия", resp)
        self.assertEqual(fsm.sessions[uid].availability_hours_month, 18)

        # Произвольный график со словом 'фикс'
        resp, _ = fsm.handle_message(uid, "Предпочитаю фиксированные дни")
        self.assertIn("В какие дни недели", resp)
        self.assertEqual(fsm.sessions[uid].availability_schedule_type, "Регулярный")

        # Произвольные дни
        resp, _ = fsm.handle_message(uid, "Среда вечер и вся суббота")
        self.assertIn("другие регулярные обязательства", resp)
        self.assertEqual(fsm.sessions[uid].availability_days, ["Среда вечер и вся суббота"])

        # Произвольные обязательства -> финализация
        resp, _ = fsm.handle_message(uid, "Основная работа на фрилансе, время планирую сам")
        self.assertIn("других волонтёрских", resp)
        resp, _ = fsm.handle_message(uid, "Нет")
        self.assertIn("Ваша заявка успешно принята", resp)
        self.assertEqual(fsm.sessions[uid].other_commitments, "Основная работа на фрилансе, время планирую сам")


if __name__ == "__main__":
    unittest.main()

