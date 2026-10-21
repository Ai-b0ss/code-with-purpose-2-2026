#!/usr/bin/env python3
"""Чат-бот ВКонтакте для волонтёрской программы Государственного исторического музея (ГИМ).

Проводит интерактивное анкетирование соискателя прямо в личных сообщениях группы VK
через Bots Long Poll API и взаимодействует с локальным FastAPI-бэкендом:
  - POST /api/v1/clarification
  - POST /api/v1/applications
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import requests
import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

# Автоматически добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Обеспечиваем безопасный вывод UTF-8 в Windows-консоли (защита от краша charmap/cp1251 на эмодзи)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gim_vk_bot")


# -----------------------------------------------------------------------------
# 1. КОНФИГУРАЦИЯ И ЗАГРУЗКА .ENV
# -----------------------------------------------------------------------------

def load_dotenv_custom() -> None:
    """Загружает переменные окружения из .env, если они ещё не заданы."""
    search_dirs = [
        Path.cwd(),
        Path(__file__).resolve().parent,
        PROJECT_ROOT,
        PROJECT_ROOT.parent,
    ]
    for directory in search_dirs:
        env_file = directory / ".env"
        if env_file.is_file():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val
                break
            except Exception as exc:
                logger.warning("Не удалось прочитать .env файл %s: %s", env_file, exc)


@dataclass
class BotConfig:
    token: str
    group_id: int
    backend_url: str = "http://127.0.0.1:8765"


def print_setup_guide() -> None:
    """Выводит понятную инструкцию при отсутствии ключей."""
    print("""
================================================================================
🏛️  ВОЛОНТЁРСКАЯ ПРОГРАММА ГОСУДАРСТВЕННОГО ИСТОРИЧЕСКОГО МУЗЕЯ (ГИМ)
    Чат-бот ВКонтакте (VK Messenger Bot)
================================================================================

[ВНИМАНИЕ] Не задана переменная окружения VK_GROUP_TOKEN!

Для работы бота сообщества требуется токен доступа ВКонтакте.

Пошаговая инструкция (занимает ~1 минуту):
--------------------------------------------------------------------------------
1. Создайте тестовое сообщество:
   - Откройте: https://vk.com/groups
   - Нажмите «Создать сообщество» -> выберите «Группа по интересам».
   - Укажите название, например: «Волонтёры ГИМ (Тест)».

2. Включите сообщения сообщества:
   - В группе: «Управление» -> «Сообщения».
   - Выберите: «Сообщения сообщества: Включены» -> Нажмите «Сохранить».

3. Создайте ключ доступа сообщества (VK_GROUP_TOKEN):
   - «Управление» -> «Работа с API» -> вкладка «Ключи доступа».
   - Нажмите «Создать ключ».
   - Отметьте:
     [v] Разрешить доступ к сообщениям сообщества
     [v] Разрешить доступ к управлению сообществом
   - Нажмите «Создать» и скопируйте ключ (начинается на vk1.a...).

4. Включите Long Poll API:
   - «Управление» -> «Работа с API» -> вкладка «Long Poll API».
   - Включите Long Poll API (тумблер «Включено»).
   - Версия API: выберите 5.199 или 5.131.
   - Вкладка «Типы событий»:
     [v] Отметьте «Входящие сообщения» (message_new).

5. Сохраните переменные окружения:
   Создайте файл .env в папке проекта со строками:
   VK_GROUP_TOKEN=vk1.a.ВАШ_СКОПИРОВАННЫЙ_ТОКЕН
   VK_GROUP_ID=123456789 (номер группы, можно опустить — бот определит сам)
   BACKEND_URL=http://127.0.0.1:8765

6. Запустите бота повторно через run_vk_bot.bat.
================================================================================
""")


def load_config() -> BotConfig | None:
    """Загружает конфигурацию из окружения или .env."""
    load_dotenv_custom()
    token = os.getenv("VK_GROUP_TOKEN", "").strip()
    if not token:
        return None

    raw_group_id = os.getenv("VK_GROUP_ID", "").strip()
    cleaned_group_id = 0
    if raw_group_id:
        digits = re.findall(r"\d+", raw_group_id)
        if digits:
            cleaned_group_id = int(digits[0])

    backend_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8765").rstrip("/")
    return BotConfig(
        token=token,
        group_id=cleaned_group_id,
        backend_url=backend_url,
    )


def parse_and_validate_birth_date(date_str: str) -> str | None:
    """Проверяет дату (ДД.ММ.ГГГГ или ГГГГ-ММ-ДД) и возвращает её в формате 'ГГГГ-ММ-ДД'."""
    text = date_str.strip()
    # ДД.ММ.ГГГГ, ДД-ММ-ГГГГ, ДД/ММ/ГГГГ
    m = re.match(r"^(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{4})$", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            d = datetime(year, month, day)
            now_year = datetime.now().year
            if 1920 <= year <= now_year:
                return d.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # ГГГГ-ММ-ДД
    m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m_iso:
        year, month, day = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            d = datetime(year, month, day)
            now_year = datetime.now().year
            if 1920 <= year <= now_year:
                return d.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


# -----------------------------------------------------------------------------
# 2. КЛИЕНТ БЭКЕНДА FASTAPI
# -----------------------------------------------------------------------------

class GimApiClient:
    """Клиент для общения с FastAPI-сервером ГИМ."""

    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def check_health(self) -> dict | None:
        try:
            resp = requests.get(f"{self.base_url}/api/v1/health", timeout=3)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        return None

    def get_clarifications(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/clarification."""
        url = f"{self.base_url}/api/v1/clarification"
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def submit_application(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/v1/applications."""
        url = f"{self.base_url}/api/v1/applications"
        resp = requests.post(url, json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


# -----------------------------------------------------------------------------
# 3. КЛАВИАТУРЫ ВКОНТАКТЕ
# -----------------------------------------------------------------------------

class VkKeyboards:
    """Генераторы клавиатур VkKeyboard."""

    @staticmethod
    def start() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Начать заполнение", color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def consent() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Согласен(на), мне есть 18 лет", color=VkKeyboardColor.POSITIVE)
        kb.add_line()
        kb.add_button("Отказаться", color=VkKeyboardColor.NEGATIVE)
        return kb.get_keyboard()

    @staticmethod
    def full_name_hint(vk_name: str) -> str:
        kb = VkKeyboard(one_time=False)
        label = f"Использовать: {vk_name}".strip()
        if len(label) > 40:
            label = label[:37] + "..."
        if label:
            kb.add_button(label, color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def status() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Студент", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Работаю", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("В поиске", color=VkKeyboardColor.SECONDARY)
        kb.add_button("Другое", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def skip(label: str = "Пропустить") -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button(label, color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def course() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("1 курс бакалавриата", color=VkKeyboardColor.PRIMARY)
        kb.add_button("2 курс бакалавриата", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("3 курс бакалавриата", color=VkKeyboardColor.PRIMARY)
        kb.add_button("4 курс бакалавриата", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("1 курс магистратуры", color=VkKeyboardColor.PRIMARY)
        kb.add_button("2 курс магистратуры", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Выпускник", color=VkKeyboardColor.SECONDARY)
        kb.add_button("Другое", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def languages() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Английский", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Немецкий", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Французский", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Китайский", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Только русский / Нет", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def experience() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Опыта нет, но хочу научиться!", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Пропустить", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def directions_inline() -> str:
        kb = VkKeyboard(inline=True)
        kb.add_button("🏛️ Фондовое", color=VkKeyboardColor.PRIMARY)
        kb.add_button("🎭 Проектное (квесты)", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("🎪 Организационное", color=VkKeyboardColor.PRIMARY)
        kb.add_button("🤝 Инклюзивное", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("📸 Медиа-волонтёрство", color=VkKeyboardColor.PRIMARY)
        kb.add_button("🤔 Пока не определился", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def hours() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("8 часов", color=VkKeyboardColor.PRIMARY)
        kb.add_button("16 часов", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("24 часа", color=VkKeyboardColor.PRIMARY)
        kb.add_button("32 часа", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def schedule() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Гибкий график", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Регулярный", color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def days() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Выходные (Сб, Вс)", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Будни", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Любые дни", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Плавающий график", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def commitments() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Только учёба", color=VkKeyboardColor.PRIMARY)
        kb.add_button("Совмещаю с работой", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button("Свободный график", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def clarification_skip() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Пропустить вопрос", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()

    @staticmethod
    def finished() -> str:
        kb = VkKeyboard(one_time=False)
        kb.add_button("Заполнить новую анкету", color=VkKeyboardColor.PRIMARY)
        return kb.get_keyboard()

    @staticmethod
    def empty() -> str:
        return VkKeyboard.get_empty_keyboard()


# -----------------------------------------------------------------------------
# 4. СОСТОЯНИЯ И FSM ДИАЛОГА
# -----------------------------------------------------------------------------

class DialogState(str, Enum):
    START = "START"
    CONSENT = "CONSENT"
    FULL_NAME = "FULL_NAME"
    BIRTH_DATE = "BIRTH_DATE"
    EMAIL = "EMAIL"
    STATUS = "STATUS"
    UNIVERSITY = "UNIVERSITY"
    FACULTY = "FACULTY"
    COURSE = "COURSE"
    SCIENTIFIC_INTERESTS = "SCIENTIFIC_INTERESTS"
    LANGUAGES = "LANGUAGES"
    MOTIVATION = "MOTIVATION"
    EXPERIENCE = "EXPERIENCE"
    DIRECTION = "DIRECTION"
    HOURS = "HOURS"
    SCHEDULE = "SCHEDULE"
    DAYS = "DAYS"
    COMMITMENTS = "COMMITMENTS"
    CLARIFICATION = "CLARIFICATION"
    DONE = "DONE"


DIRECTION_MAPPING: dict[str, str] = {
    "фондовое": "collections",
    "🏛️ фондовое": "collections",
    "collections": "collections",
    "проектное": "project",
    "проектное (квесты)": "project",
    "🎭 проектное (квесты)": "project",
    "project": "project",
    "организационное": "organizational",
    "🎪 организационное": "organizational",
    "organizational": "organizational",
    "инклюзивное": "inclusive",
    "🤝 инклюзивное": "inclusive",
    "inclusive": "inclusive",
    "медиа": "media",
    "медиа-волонтёрство": "media",
    "📸 медиа-волонтёрство": "media",
    "media": "media",
    "пока не определился": "undecided",
    "🤔 пока не определился": "undecided",
    "не определился": "undecided",
    "undecided": "undecided",
}

STATUS_MAPPING: dict[str, str] = {
    "студент": "student",
    "работаю": "working",
    "в поиске": "unemployed",
    "в поиске работы": "unemployed",
    "другое": "other",
}


@dataclass
class UserSession:
    user_id: int
    applicant_name: str = ""
    vk_url: str = ""
    state: DialogState = DialogState.START
    last_action_ts: float = field(default_factory=time.time)

    # Поля анкеты
    age_over_18: bool = False
    consent_personal_data: bool = False
    birth_date: str = ""
    email: str = ""
    status: str = "student"
    university: str = ""
    faculty: str = ""
    course: str = ""
    scientific_interests: str = ""
    foreign_languages: list[str] = field(default_factory=list)
    motivation: str = ""
    experience: str = ""
    desired_direction: str = "undecided"
    availability_hours_month: int = 16
    availability_schedule_type: str = "flexible"
    availability_days: list[str] = field(default_factory=list)
    other_commitments: str = ""

    # Уточняющие вопросы (динамические)
    pending_clarifications: list[dict[str, Any]] = field(default_factory=list)
    followup_answers: dict[str, str] = field(default_factory=dict)
    followup_answer: str = ""

    # Результат регистрации
    application_id: str | None = None

    def to_application_input(self) -> dict[str, Any]:
        """Формирует payload в формате ApplicationInput FastAPI-бэкенда."""
        return {
            "applicant_name": self.applicant_name or f"Пользователь VK {self.user_id}",
            "age_over_18": self.age_over_18,
            "motivation": self.motivation,
            "experience": self.experience,
            "expectations": "",
            "availability_hours_month": self.availability_hours_month,
            "other_commitments": self.other_commitments,
            "desired_direction": self.desired_direction,
            "followup_answer": self.followup_answer,
            "email": self.email,
            "vk_url": self.vk_url or f"https://vk.com/id{self.user_id}",
            "birth_date": self.birth_date,
            "consent_personal_data": self.consent_personal_data,
            "status": self.status,
            "university": self.university,
            "university_other": "",
            "faculty": self.faculty,
            "course": self.course,
            "scientific_interests": self.scientific_interests,
            "foreign_languages": self.foreign_languages,
            "hobbies": "",
            "availability_schedule_type": self.availability_schedule_type,
            "availability_days": self.availability_days,
            "other_volunteer_programs": "",
            "source": "vk_bot",
            "followup_answers": self.followup_answers,
        }


class VolunteerBotFSM:
    """Конечный автомат (FSM) диалога волонтёрской анкеты ГИМ."""

    RESET_COMMANDS = {
        "/reset", "reset", "заново", "начать заново",
        "сброс", "отмена", "/start", "start",
    }

    EMAIL_REGEX = re.compile(r"^[\w\.\+\-]+@[a-zA-Z0-9\-]+(\.[a-zA-Z0-9\-]+)+$")

    def __init__(self, api_client: GimApiClient, session_ttl: int = 86400):
        self.api_client = api_client
        self.session_ttl = session_ttl
        self.sessions: dict[int, UserSession] = {}

    def get_or_create_session(self, user_id: int, user_name: str | None = None) -> UserSession:
        now = time.time()
        session = self.sessions.get(user_id)
        if not session or (now - session.last_action_ts > self.session_ttl):
            session = UserSession(
                user_id=user_id,
                applicant_name=user_name or f"Пользователь #{user_id}",
                vk_url=f"https://vk.com/id{user_id}",
            )
            self.sessions[user_id] = session
        elif user_name and not session.applicant_name:
            session.applicant_name = user_name
        session.last_action_ts = now
        return session

    def reset_session(self, user_id: int, user_name: str | None = None) -> UserSession:
        session = UserSession(
            user_id=user_id,
            applicant_name=user_name or f"Пользователь #{user_id}",
            vk_url=f"https://vk.com/id{user_id}",
        )
        self.sessions[user_id] = session
        return session

    def handle_message(
        self,
        user_id: int,
        text: str,
        user_name: str | None = None,
    ) -> tuple[str, str | None]:
        """Обрабатывает входящее сообщение от пользователя и возвращает ответ и клавиатуру."""
        clean_text = text.strip()
        lower_text = clean_text.lower()

        # 1. Проверка команд сброса диалога
        if lower_text in self.RESET_COMMANDS or lower_text == "заполнить новую анкету":
            session = self.reset_session(user_id, user_name)
            return self._greeting_response(session)

        is_new_user = user_id not in self.sessions
        session = self.get_or_create_session(user_id, user_name)

        # Если пользователь пишет боту впервые — отправляем приветствие со стартовой кнопкой
        if is_new_user:
            return self._greeting_response(session)

        # 2. Обработка по текущему состоянию
        if session.state == DialogState.START:
            return self._step_start_to_consent(session)

        elif session.state == DialogState.CONSENT:
            return self._step_handle_consent(session, lower_text)

        elif session.state == DialogState.FULL_NAME:
            return self._step_handle_full_name(session, clean_text)

        elif session.state == DialogState.BIRTH_DATE:
            return self._step_handle_birth_date(session, clean_text)

        elif session.state == DialogState.EMAIL:
            return self._step_handle_email(session, clean_text)

        elif session.state == DialogState.STATUS:
            return self._step_handle_status(session, clean_text, lower_text)

        elif session.state == DialogState.UNIVERSITY:
            return self._step_handle_university(session, clean_text, lower_text)

        elif session.state == DialogState.FACULTY:
            return self._step_handle_faculty(session, clean_text, lower_text)

        elif session.state == DialogState.COURSE:
            return self._step_handle_course(session, clean_text, lower_text)

        elif session.state == DialogState.SCIENTIFIC_INTERESTS:
            return self._step_handle_scientific_interests(session, clean_text, lower_text)

        elif session.state == DialogState.LANGUAGES:
            return self._step_handle_languages(session, clean_text, lower_text)

        elif session.state == DialogState.MOTIVATION:
            return self._step_handle_motivation(session, clean_text)

        elif session.state == DialogState.EXPERIENCE:
            return self._step_handle_experience(session, clean_text, lower_text)

        elif session.state == DialogState.DIRECTION:
            return self._step_handle_direction(session, clean_text, lower_text)

        elif session.state == DialogState.HOURS:
            return self._step_handle_hours(session, clean_text)

        elif session.state == DialogState.SCHEDULE:
            return self._step_handle_schedule(session, clean_text, lower_text)

        elif session.state == DialogState.DAYS:
            return self._step_handle_days(session, clean_text, lower_text)

        elif session.state == DialogState.COMMITMENTS:
            return self._step_handle_commitments(session, clean_text, lower_text)

        elif session.state == DialogState.CLARIFICATION:
            return self._step_handle_clarification(session, clean_text, lower_text)

        elif session.state == DialogState.DONE:
            return (
                "Ваша анкета уже зарегистрирована! 🏛️\n"
                "Если вы хотите подать новую заявку или исправить данные, "
                "нажмите кнопку «Заполнить новую анкету» или отправьте /reset.",
                VkKeyboards.finished(),
            )

        # Резервный возврат в начало
        session = self.reset_session(user_id, user_name)
        return self._greeting_response(session)

    # -------------------------------------------------------------------------
    # ШАГИ ДИАЛОГА
    # -------------------------------------------------------------------------

    def _greeting_response(self, session: UserSession) -> tuple[str, str]:
        name_display = session.applicant_name or "дорогой кандидат"
        msg = (
            f"Здравствуйте, {name_display}! 🏛️\n\n"
            "Я бот Волонтёрской программы Государственного исторического музея (ГИМ).\n"
            "Помогу вам подать анкету в команду волонтёров всего за 3–5 минут прямо здесь!\n\n"
            "В любой момент вы можете начать диалог заново — просто напишите «Заново» или /reset."
        )
        return msg, VkKeyboards.start()

    def _step_start_to_consent(self, session: UserSession) -> tuple[str, str]:
        session.state = DialogState.CONSENT
        msg = (
            "Возраст и согласие 📋\n\n"
            "По регламенту Государственного исторического музея участие в волонтёрской программе "
            "рассчитано на совершеннолетних кандидатов (18+).\n"
            "Также для регистрации анкеты необходимо ваше согласие на обработку персональных данных "
            "в соответствии с Федеральным законом № 152-ФЗ.\n\n"
            "Вы подтверждаете возраст 18+ и даёте согласие на обработку данных?"
        )
        return msg, VkKeyboards.consent()

    def _step_handle_consent(self, session: UserSession, lower_text: str) -> tuple[str, str]:
        positive_variants = {
            "согласен(на), мне есть 18 лет", "согласен", "согласна",
            "да", "18+", "мне есть 18 лет", "есть 18", "согласие",
        }
        negative_variants = {"отказаться", "нет", "не согласен", "не согласна", "отказ"}

        if any(v in lower_text for v in positive_variants):
            session.age_over_18 = True
            session.consent_personal_data = True
            session.state = DialogState.FULL_NAME
            vk_name = session.applicant_name.strip()
            msg = "Пожалуйста, укажите ваше полное ФИО (Фамилия, Имя и Отчество):"
            kb = VkKeyboards.full_name_hint(vk_name) if vk_name else VkKeyboards.empty()
            return msg, kb

        if any(v in lower_text for v in negative_variants):
            session.state = DialogState.START
            msg = (
                "К сожалению, без согласия на обработку персональных данных и подтверждения "
                "возраста 18+ мы не можем принять официальную заявку в музей. 🏛️\n\n"
                "Если вы измените решение, просто напишите «Начать» или /reset."
            )
            return msg, VkKeyboards.start()

        msg = (
            "Пожалуйста, подтвердите возраст 18+ и согласие на обработку данных, "
            "используя кнопки ниже:"
        )
        return msg, VkKeyboards.consent()

    def _step_handle_full_name(self, session: UserSession, clean_text: str) -> tuple[str, str]:
        if clean_text.lower().startswith("использовать:"):
            name = clean_text.split(":", 1)[1].strip()
            if not name:
                name = session.applicant_name
        else:
            name = clean_text

        if len(name) < 2:
            msg = "Пожалуйста, укажите ваше полное ФИО (например: Иванов Иван Иванович):"
            vk_name = session.applicant_name.strip()
            kb = VkKeyboards.full_name_hint(vk_name) if vk_name else VkKeyboards.empty()
            return msg, kb

        session.applicant_name = name[:200]
        session.state = DialogState.BIRTH_DATE
        msg = "Укажите вашу дату рождения в формате ДД.ММ.ГГГГ (например: 15.05.2003):"
        return msg, VkKeyboards.empty()

    def _step_handle_birth_date(self, session: UserSession, clean_text: str) -> tuple[str, str]:
        bdate = parse_and_validate_birth_date(clean_text)
        if not bdate:
            msg = (
                "Кажется, дата введена некорректно. Пожалуйста, укажите дату рождения "
                "в формате ДД.ММ.ГГГГ (например: 15.05.2003):"
            )
            return msg, VkKeyboards.empty()

        session.birth_date = bdate
        session.state = DialogState.EMAIL
        msg = (
            f"Контакты ✉️\n\n"
            f"Ваш профиль ВКонтакте ({session.vk_url}) уже прикреплён к анкете.\n\n"
            "Пожалуйста, введите ваш контактный Email для связи с куратором:"
        )
        return msg, VkKeyboards.empty()

    def _step_handle_email(self, session: UserSession, clean_text: str) -> tuple[str, str]:
        if not self.EMAIL_REGEX.match(clean_text) or "@" not in clean_text:
            msg = (
                "Кажется, в адресе опечатка. Пожалуйста, введите корректный адрес "
                "электронной почты (например, ivan@example.com):"
            )
            return msg, VkKeyboards.empty()

        session.email = clean_text
        session.state = DialogState.STATUS
        msg = (
            "Статус и образование 🎓\n\n"
            "Укажите ваш текущий статус занятости:"
        )
        return msg, VkKeyboards.status()

    def _step_handle_status(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        mapped = STATUS_MAPPING.get(lower_text, clean_text[:50])
        session.status = mapped
        session.state = DialogState.UNIVERSITY
        msg = (
            "Укажите ваше место учёбы (ВУЗ / колледж) или последнее учебное заведение:"
        )
        return msg, VkKeyboards.skip("Пропустить")

    def _step_handle_university(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if lower_text in {"пропустить", "нет", "-", "—"}:
            session.university = ""
        else:
            session.university = clean_text[:200]

        session.state = DialogState.FACULTY
        msg = "Укажите ваш факультет (направление обучения):"
        return msg, VkKeyboards.skip("Пропустить")

    def _step_handle_faculty(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if lower_text in {"пропустить", "нет", "-", "—"}:
            session.faculty = ""
        else:
            session.faculty = clean_text[:200]

        session.state = DialogState.COURSE
        msg = "Укажите ваш курс:"
        return msg, VkKeyboards.course()

    def _step_handle_course(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if lower_text in {"пропустить", "нет", "-", "—"}:
            session.course = ""
        else:
            session.course = clean_text[:100]

        session.state = DialogState.SCIENTIFIC_INTERESTS
        msg = "Сфера ваших научных или профессиональных интересов (или нажмите «Пропустить»):"
        return msg, VkKeyboards.skip("Пропустить")

    def _step_handle_scientific_interests(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if lower_text in {"пропустить", "нет", "-", "—"}:
            session.scientific_interests = ""
        else:
            session.scientific_interests = clean_text[:2000]

        session.state = DialogState.LANGUAGES
        msg = "Какими иностранными языками вы владеете на уровне B1 и выше?"
        return msg, VkKeyboards.languages()

    def _step_handle_languages(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if lower_text in {"только русский / нет", "нет", "только русский", "русский", "пропустить", "-", "—"}:
            session.foreign_languages = ["Только русский"]
        else:
            langs = [p.strip() for p in re.split(r"[,;]+", clean_text) if p.strip()]
            session.foreign_languages = langs or ["Только русский"]

        session.state = DialogState.MOTIVATION
        msg = (
            "Мотивация и опыт 🏛️\n\n"
            "Почему именно Исторический музей? Расскажите, что вас привлекает в работе ГИМ "
            "и почему вы хотите стать нашим волонтёром (минимум 10 символов):"
        )
        return msg, VkKeyboards.empty()

    def _step_handle_motivation(self, session: UserSession, clean_text: str) -> tuple[str, str]:
        if len(clean_text) < 10:
            msg = (
                "Пожалуйста, расскажите чуть подробнее (минимум 10 символов), "
                "чтобы кураторы лучше поняли вашу мотивацию:"
            )
            return msg, VkKeyboards.empty()

        session.motivation = clean_text[:4000]
        session.state = DialogState.EXPERIENCE
        msg = (
            "Какой у вас есть волонтёрский опыт или полезные навыки "
            "(организация событий, работа с посетителями, фото/видео, языки и др.)?"
        )
        return msg, VkKeyboards.experience()

    def _step_handle_experience(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if lower_text in {"опыта нет, но хочу научиться!", "опыта нет", "нет опыта", "пропустить", "-"}:
            session.experience = "Опыта пока нет, готов(а) учиться"
        else:
            session.experience = clean_text[:4000]

        session.state = DialogState.DIRECTION
        msg = (
            "Направление и график ⏰\n\n"
            "Выберите интересующее вас направление волонтёрской деятельности в ГИМ:"
        )
        return msg, VkKeyboards.directions_inline()

    def _step_handle_direction(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        found_key = None
        for key, code in DIRECTION_MAPPING.items():
            if key in lower_text:
                found_key = code
                break

        if not found_key:
            msg = (
                "Пожалуйста, выберите одно из 6 направлений с помощью кнопок:\n"
                "• 🏛️ Фондовое\n"
                "• 🎭 Проектное (квесты)\n"
                "• 🎪 Организационное\n"
                "• 🤝 Инклюзивное\n"
                "• 📸 Медиа-волонтёрство\n"
                "• 🤔 Пока не определился"
            )
            return msg, VkKeyboards.directions_inline()

        session.desired_direction = found_key
        session.state = DialogState.HOURS
        msg = "Сколько часов в месяц вы готовы уделять волонтёрской деятельности в музее?"
        return msg, VkKeyboards.hours()

    def _step_handle_hours(self, session: UserSession, clean_text: str) -> tuple[str, str]:
        digits = re.findall(r"\d+", clean_text)
        hours = None
        if digits:
            val = int(digits[0])
            if 0 <= val <= 400:
                hours = val

        if hours is None:
            msg = (
                "Пожалуйста, укажите количество часов числом от 0 до 400 "
                "(например: 8, 16, 24):"
            )
            return msg, VkKeyboards.hours()

        session.availability_hours_month = hours
        session.state = DialogState.SCHEDULE
        msg = "Какой график участия вам подходит?"
        return msg, VkKeyboards.schedule()

    def _step_handle_schedule(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if "регуляр" in lower_text:
            session.availability_schedule_type = "regular"
        else:
            session.availability_schedule_type = "flexible"

        session.state = DialogState.DAYS
        msg = "В какие дни недели вы преимущественно готовы помогать в музее?"
        return msg, VkKeyboards.days()

    def _step_handle_days(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if lower_text in {"выходные (сб, вс)", "выходные"}:
            session.availability_days = ["Выходные (Сб, Вс)"]
        else:
            session.availability_days = [clean_text[:100]]

        session.state = DialogState.COMMITMENTS
        msg = "Какие у вас есть другие регулярные обязательства (учёба, работа, проекты)? Как планируете совмещать?"
        return msg, VkKeyboards.commitments()

    def _step_handle_commitments(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        session.other_commitments = clean_text[:1500]
        # Запрос динамических уточнений из backend
        return self._fetch_clarifications_or_submit(session)

    def _fetch_clarifications_or_submit(self, session: UserSession) -> tuple[str, str]:
        """Отправляет черновик в POST /api/v1/clarification."""
        payload = session.to_application_input()
        try:
            res = self.api_client.get_clarifications(payload)
            questions = res.get("questions", [])
            # Оставляем только те вопросы, на которые ещё нет ответа
            unanswered = [
                q for q in questions
                if isinstance(q, dict) and q.get("id") and q["id"] not in session.followup_answers
            ]
            if unanswered:
                session.pending_clarifications = unanswered
                session.state = DialogState.CLARIFICATION
                first_q = unanswered[0]
                msg = (
                    "Уточняющий вопрос от кураторов 🔍\n\n"
                    f"📌 *{first_q.get('category', 'Уточнение')}*\n"
                    f"{first_q.get('question', '')}\n\n"
                    f"💡 _{first_q.get('reason', '')}_"
                )
                return msg, VkKeyboards.clarification_skip()

        except Exception as exc:
            logger.warning("Не удалось получить уточнения от бэкенда: %s", exc)
            # При недоступности сервиса уточнений не блокируем пользователя — переходим к сохранению

        return self._submit_final_application(session)

    def _step_handle_clarification(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if not session.pending_clarifications:
            return self._submit_final_application(session)

        curr_q = session.pending_clarifications.pop(0)
        q_id = curr_q.get("id", "followup")

        if lower_text in {"пропустить", "пропустить вопрос", "нет", "-", "без ответа"}:
            answer = "Без уточнений"
        else:
            answer = clean_text[:2000]

        session.followup_answers[q_id] = answer
        if not session.followup_answer:
            session.followup_answer = answer

        # Если есть ещё вопросы — задаём следующий
        if session.pending_clarifications:
            next_q = session.pending_clarifications[0]
            msg = (
                f"Следующий уточняющий вопрос 🔍:\n\n"
                f"📌 *{next_q.get('category', 'Уточнение')}*\n"
                f"{next_q.get('question', '')}\n\n"
                f"💡 _{next_q.get('reason', '')}_"
            )
            return msg, VkKeyboards.clarification_skip()

        return self._submit_final_application(session)

    def _submit_final_application(self, session: UserSession) -> tuple[str, str]:
        """Отправляет заявку в POST /api/v1/applications."""
        payload = session.to_application_input()
        try:
            res = self.api_client.submit_application(payload)
            app_id = res.get("application_id", "GIM-OK")
            session.application_id = app_id
            session.state = DialogState.DONE

            msg = (
                "🎉 Ваша заявка успешно принята!\n\n"
                f"🏛️ Регистрационный номер: #{app_id}\n\n"
                f"Кураторы волонтёрской программы свяжутся с вами здесь, "
                f"в личных сообщениях ВКонтакте (или по почте {session.email}).\n\n"
                "Спасибо за интерес к Государственному историческому музею! ✨"
            )
            return msg, VkKeyboards.finished()

        except Exception as exc:
            logger.error("Ошибка отправки заявки на бэкенд: %s", exc)
            msg = (
                "⚠️ Не удалось связаться с сервером музея для сохранения заявки.\n"
                "Пожалуйста, убедитесь, что локальный сервер FastAPI запущен (порт 8765), "
                "и отправьте любое сообщение для повторной попытки сохранения или /reset для начала заново."
            )
            return msg, VkKeyboards.empty()


# -----------------------------------------------------------------------------
# 5. ТОЧКА ВХОДА И ЦИКЛ LONG POLL
# -----------------------------------------------------------------------------

def run_bot() -> None:
    """Инициализация и запуск Long Poll слушателя сообщений группы VK."""
    config = load_config()
    if not config:
        print_setup_guide()
        sys.exit(1)

    logger.info("Инициализация VK API...")
    vk_session = vk_api.VkApi(token=config.token)
    vk = vk_session.get_api()

    # Автоматическое определение group_id, если не задан
    group_id = config.group_id
    if not group_id:
        try:
            group_info = vk.groups.getById()[0]
            group_id = int(group_info["id"])
            group_name = group_info.get("name", "")
            logger.info("Автоматически определён ID группы: %s (%s)", group_id, group_name)
        except Exception as exc:
            logger.error("Не удалось автоматически определить ID группы: %s", exc)
            print_setup_guide()
            sys.exit(1)

    # Проверка доступности бэкенда
    api_client = GimApiClient(base_url=config.backend_url)
    health = api_client.check_health()
    if health:
        logger.info("Подключение к бэкенду ГИМ успешно (%s)", config.backend_url)
    else:
        logger.warning(
            "Сервер бэкенда ГИМ по адресу %s пока не отвечает. "
            "Убедитесь, что запущен backend (run.bat)", config.backend_url
        )

    fsm = VolunteerBotFSM(api_client=api_client)

    logger.info("Подключение к Bots Long Poll сообщества %s...", group_id)
    longpoll = VkBotLongPoll(vk_session, group_id)
    user_name_cache: dict[int, str] = {}

    logger.info("🚀 Бот волонтёрской программы ГИМ успешно запущен и ожидает сообщений!")

    while True:
        try:
            for event in longpoll.listen():
                if event.type != VkBotEventType.MESSAGE_NEW:
                    continue

                # Поддержка разных версий Bot Long Poll API (5.80+ и 5.103+)
                msg_obj = event.message if getattr(event, "message", None) else getattr(event, "obj", {})
                from_id = msg_obj.get("from_id") or msg_obj.get("user_id")
                text = msg_obj.get("text", "")

                if not from_id or from_id < 0:
                    continue

                # Автоматическое получение имени пользователя через users.get
                if from_id not in user_name_cache:
                    try:
                        u_data = vk.users.get(user_ids=from_id)[0]
                        first = u_data.get("first_name", "")
                        last = u_data.get("last_name", "")
                        user_name = f"{first} {last}".strip()
                        user_name_cache[from_id] = user_name or f"Волонтёр #{from_id}"
                    except Exception as err:
                        logger.warning("Не удалось получить профиль пользователя %s: %s", from_id, err)
                        user_name_cache[from_id] = f"Волонтёр #{from_id}"

                user_name = user_name_cache[from_id]

                # Обработка через FSM
                reply_text, keyboard = fsm.handle_message(
                    user_id=from_id,
                    text=text,
                    user_name=user_name,
                )

                # Отправка ответа
                send_kwargs: dict[str, Any] = {
                    "user_id": from_id,
                    "random_id": get_random_id(),
                    "message": reply_text,
                }
                if keyboard:
                    send_kwargs["keyboard"] = keyboard

                try:
                    vk.messages.send(**send_kwargs)
                except vk_api.exceptions.ApiError as err:
                    logger.error("Ошибка при отправке сообщения в VK (user_id=%s): %s", from_id, err)
                    # Если клиент пользователя не поддерживает инлайн-кнопки, пробуем без них
                    if keyboard:
                        send_kwargs.pop("keyboard", None)
                        try:
                            vk.messages.send(**send_kwargs)
                        except Exception:
                            pass

        except requests.exceptions.ReadTimeout:
            # Штатный таймаут опроса Long Poll
            continue
        except requests.exceptions.ConnectionError:
            logger.warning("Потеря связи с сервером VK Long Poll. Переподключение через 5 сек...")
            time.sleep(5)
        except Exception as exc:
            logger.error("Непредвиденная ошибка в основном цикле бота: %s", exc, exc_info=True)
            time.sleep(2)


if __name__ == "__main__":
    run_bot()
