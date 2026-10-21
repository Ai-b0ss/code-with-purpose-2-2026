"""Unit-тесты для чат-бота ВКонтакте волонтёрской программы ГИМ (vk_bot/bot.py)."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

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
)


class VkBotUnitTests(unittest.TestCase):
    """Тестирование логики, клавиатур, FSM и валидации VK-бота."""

    def test_keyboards_are_valid_json(self):
        """Проверка, что все генерируемые клавиатуры возвращают валидный JSON."""
        keyboards = [
            VkKeyboards.start(),
            VkKeyboards.consent(),
            VkKeyboards.full_name_hint("Иван Иванов"),
            VkKeyboards.status(),
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
        self.assertEqual(parse_and_validate_birth_date("2001-07-20"), "2001-07-20")
        self.assertIsNone(parse_and_validate_birth_date("31.02.2000"))
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

    @patch("vk_bot.bot.load_dotenv_custom")
    def test_config_loader_when_missing(self, mock_dotenv):
        """Проверка возврата None при отсутствии VK_GROUP_TOKEN."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_config()
            self.assertIsNone(cfg)

    @patch("vk_bot.bot.load_dotenv_custom")
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

        # 3. Ввод ФИО с отчеством -> Дата рождения
        resp, kb = fsm.handle_message(user_id, "Смирнов Алексей Иванович")
        self.assertIn("дату рождения", resp)

        # 3.1 Невалидная дата рождения -> повторный запрос
        resp, kb = fsm.handle_message(user_id, "31.02.2003")
        self.assertIn("дата введена некорректно", resp)

        # 3.2 Валидная дата рождения -> Email
        resp, kb = fsm.handle_message(user_id, "15.05.2003")
        self.assertIn("Email", resp)
        self.assertIn("https://vk.com/id1001", resp)

        # 4. Email -> Статус
        resp, kb = fsm.handle_message(user_id, "alex@example.com")
        self.assertIn("Статус", resp)

        # 5. Статус -> Место учёбы / ВУЗ
        resp, kb = fsm.handle_message(user_id, "Студент")
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
        resp, kb = fsm.handle_message(user_id, "Английский")
        self.assertIn("Мотивация и опыт", resp)

        # 11.1 Слишком короткая мотивация (<10 символов) -> ошибка
        resp, kb = fsm.handle_message(user_id, "Кратко")
        self.assertIn("минимум 10 символов", resp)

        # 11.2 Корректная мотивация -> Опыт
        resp, kb = fsm.handle_message(user_id, "Хочу помогать сохранять историческое наследие России!")
        self.assertIn("волонтёрский опыт", resp)

        # 12. Опыт -> Направление
        resp, kb = fsm.handle_message(user_id, "Опыта нет, но хочу научиться!")
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
        self.assertEqual(sent["status"], "student")
        self.assertEqual(sent["university"], "МГУ им. М.В. Ломоносова")
        self.assertEqual(sent["faculty"], "Исторический факультет")
        self.assertEqual(sent["course"], "2 курс бакалавриата")
        self.assertEqual(sent["scientific_interests"], "История России XIX века")
        self.assertEqual(sent["foreign_languages"], ["Английский"])
        self.assertEqual(sent["desired_direction"], "collections")
        self.assertEqual(sent["availability_hours_month"], 16)
        self.assertEqual(sent["availability_schedule_type"], "flexible")
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
        session.state = DialogState.COMMITMENTS
        session.age_over_18 = True
        session.consent_personal_data = True
        session.motivation = "Достаточная мотивация для музея"
        session.desired_direction = "project"

        resp, _ = fsm.handle_message(user_id, "Только учёба")
        self.assertIn("Не удалось связаться с сервером музея", resp)


if __name__ == "__main__":
    unittest.main()
