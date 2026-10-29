from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from vk_bot.keyboards import VkKeyboards

def parse_and_validate_birth_date(date_str: str) -> str | None:
    """Принимает только числовую дату ДД.ММ.ГГГГ/ДД-ММ-ГГГГ/ДД/ММ/ГГГГ."""
    text = str(date_str).strip()
    today = datetime.now().date()

    def normalize_date(year: int, month: int, day: int) -> str | None:
        try:
            parsed = datetime(year, month, day)
        except ValueError:
            return None
        if 1920 <= year <= today.year and parsed.date() <= today:
            return parsed.strftime("%Y-%m-%d")
        return None

    # ДД.ММ.ГГГГ, ДД-ММ-ГГГГ, ДД/ММ/ГГГГ
    m = re.fullmatch(r"(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{4})", text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return normalize_date(year, month, day)

    return None

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
    EXPECTATIONS = "EXPECTATIONS"
    EXPERIENCE = "EXPERIENCE"
    HOBBIES = "HOBBIES"
    SOURCE = "SOURCE"
    DIRECTION = "DIRECTION"
    HOURS = "HOURS"
    SCHEDULE = "SCHEDULE"
    DAYS = "DAYS"
    COMMITMENTS = "COMMITMENTS"
    OTHER_VOLUNTEERING = "OTHER_VOLUNTEERING"
    CLARIFICATION = "CLARIFICATION"
    CHECK_RETRY = "CHECK_RETRY"
    SUBMIT_RETRY = "SUBMIT_RETRY"
    DONE = "DONE"


DIRECTION_MAPPING: dict[str, str] = {
    # Точные совпадения с кнопок (с эмодзи и без)
    "фондовое": "collections",
    "🏛️ фондовое": "collections",
    "проектное": "project",
    "проектное (квесты)": "project",
    "🎭 проектное (квесты)": "project",
    "организационное": "organizational",
    "🎪 организационное": "organizational",
    "инклюзивное": "inclusive",
    "🤝 инклюзивное": "inclusive",
    "медиа": "media",
    "медиа-волонтёрство": "media",
    "📸 медиа-волонтёрство": "media",
    "пока не определился": "undecided",
    "🤔 пока не определился": "undecided",
    "не определился": "undecided",
    # Ключевые слова для свободного ввода
    "фонд": "collections",
    "collections": "collections",
    "архив": "collections",
    "проект": "project",
    "квест": "project",
    "мастер-класс": "project",
    "мастер": "project",
    "project": "project",
    "организацион": "organizational",
    "мероприят": "organizational",
    "выставк": "organizational",
    "organizational": "organizational",
    "инклюзив": "inclusive",
    "овз": "inclusive",
    "inclusive": "inclusive",
    "медиа": "media",
    "фото": "media",
    "видео": "media",
    "дизайн": "media",
    "smm": "media",
    "media": "media",
    "любое": "undecided",
    "undecided": "undecided",
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
    status: str = ""
    university: str = ""
    faculty: str = ""
    course: str = ""
    scientific_interests: str = ""
    foreign_languages: list[str] = field(default_factory=list)
    motivation: str = ""
    expectations: str = ""
    experience: str = ""
    hobbies: str = ""
    source: str = ""
    other_volunteer_programs: str = ""
    desired_direction: str = "undecided"
    availability_hours_month: int = 16
    availability_schedule_type: str = "Гибкий"
    availability_days: list[str] = field(default_factory=list)
    other_commitments: str = ""

    # Уточняющие вопросы (динамические)
    pending_clarifications: list[dict[str, Any]] = field(default_factory=list)
    followup_answers: dict[str, str] = field(default_factory=dict)
    followup_answer: str = ""
    followup_questions: list[dict[str, str]] = field(default_factory=list)
    clarification_round: int = 0

    # Результат регистрации
    application_id: str | None = None

    def to_application_input(self) -> dict[str, Any]:
        """Формирует payload в формате ApplicationInput FastAPI-бэкенда."""
        return {
            "application_id": self.application_id,
            "applicant_name": self.applicant_name or f"Пользователь VK {self.user_id}",
            "age_over_18": self.age_over_18,
            "motivation": self.motivation,
            "experience": self.experience,
            "expectations": self.expectations,
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
            "hobbies": self.hobbies,
            "availability_schedule_type": self.availability_schedule_type,
            "availability_days": self.availability_days,
            "other_volunteer_programs": self.other_volunteer_programs,
            "source": self.source,
            "followup_answers": self.followup_answers,
            "followup_questions": self.followup_questions,
            "clarification_round": self.clarification_round,
        }


# -------------------------------------------------------------------------
# ШАГИ ДИАЛОГА
# -------------------------------------------------------------------------

def _greeting_response(self, session: UserSession) -> tuple[str, str]:
    name_display = session.applicant_name or "дорогой кандидат"
    msg = (
        f"Здравствуйте, {name_display}! 🏛️\n\n"
        "Я бот Волонтёрской программы Государственного исторического музея (ГИМ).\n"
        "Помогу заполнить анкету волонтёра прямо здесь. Ориентир заполнения — 5–7 минут; анализ ИИ может занять дополнительное время.\n\n"
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
        "Вы подтверждаете возраст 18+ и даёте согласие на обработку данных?\n\n"
        "💡 Пример: Нажмите кнопку согласия или отправьте «Да / Согласен(на)»\n"
        "💬 Выберите вариант на кнопках ниже или просто напишите ответ текстом в чат."
    )
    return msg, VkKeyboards.consent()

def _step_handle_consent(self, session: UserSession, lower_text: str) -> tuple[str, str]:
    normalized = " ".join(lower_text.replace("ё", "е").split())
    negative_markers = ("не соглас", "отказ", "нет")
    if any(marker in normalized for marker in negative_markers):
        session.state = DialogState.START
        msg = (
            "К сожалению, без согласия на обработку персональных данных и подтверждения "
            "возраста 18+ мы не можем принять официальную заявку в музей. 🏛️\n\n"
            "Если вы измените решение, просто напишите «Начать» или /reset."
        )
        return msg, VkKeyboards.start()

    positive_variants = {
        "согласен(на), мне есть 18 лет", "согласен", "согласна",
        "да", "18+", "мне есть 18 лет", "есть 18", "согласие",
    }
    if normalized in positive_variants or (
        "соглас" in normalized and "18" in normalized
    ):
        session.age_over_18 = True
        session.consent_personal_data = True
        session.state = DialogState.FULL_NAME
        msg = (
            "Пожалуйста, напишите ваше полное ФИО (Фамилию, Имя и Отчество):\n\n"
            "💡 Пример: Иванов Иван Иванович\n"
        )
        return msg, VkKeyboards.empty()

    msg = (
        "Пожалуйста, подтвердите возраст 18+ и согласие на обработку данных, "
        "используя кнопки ниже:"
    )
    return msg, VkKeyboards.consent()

def _step_handle_full_name(self, session: UserSession, clean_text: str) -> tuple[str, str]:
    name = " ".join(clean_text.split())
    words = name.split(" ") if name else []
    if len(words) < 3:
        msg = (
            "Пожалуйста, укажите также ваше отчество (если отчества нет по паспорту — "
            "напишите «нет»):\n\n"
            "💡 Пример: Иванов Иван Иванович"
        )
        return msg, VkKeyboards.empty()

    session.applicant_name = name[:200]
    session.state = DialogState.BIRTH_DATE
    msg = (
        "Укажите вашу дату рождения числом в формате ДД.ММ.ГГГГ:\n\n"
        "💡 Пример: 15.05.2003\n"
    )
    return msg, VkKeyboards.empty()

def _step_handle_birth_date(self, session: UserSession, clean_text: str) -> tuple[str, str]:
    bdate = parse_and_validate_birth_date(clean_text)
    if not bdate:
        msg = (
            "Пожалуйста, укажите вашу дату рождения только цифрами в формате ДД.ММ.ГГГГ:\n\n"
            "💡 Пример: 15.05.2003\n"
        )
        return msg, VkKeyboards.empty()

    from backend.eligibility import validate_birth_date
    try:
        validate_birth_date(bdate)
    except ValueError as exc:
        return str(exc) + " Укажите исправленную дату или начните заново.", VkKeyboards.empty()

    session.birth_date = bdate
    session.state = DialogState.EMAIL
    msg = (
        f"Контакты ✉️\n\n"
        f"Ваш профиль ВКонтакте ({session.vk_url}) уже прикреплён к анкете.\n\n"
        "Укажите ваш контактный адрес электронной почты (Email):\n\n"
        "💡 Пример: ivan.ivanov@mail.ru\n"
        "💬 Напишите действующий Email для связи с куратором."
    )
    return msg, VkKeyboards.empty()

def _step_handle_email(self, session: UserSession, clean_text: str) -> tuple[str, str]:
    if not self.EMAIL_REGEX.match(clean_text) or "@" not in clean_text:
        msg = (
            "Кажется, в адресе опечатка. Пожалуйста, введите корректный адрес "
            "электронной почты (Email):\n\n"
            "💡 Пример: ivan.ivanov@mail.ru\n"
            "💬 Напишите действующий Email для связи с куратором."
        )
        return msg, VkKeyboards.empty()

    session.email = clean_text
    session.state = DialogState.STATUS
    msg = (
        "Статус и образование 🎓\n\n"
        "Укажите ваш текущий статус занятости и примерный график:\n\n"
        "💡 Пример: «Студент дневного отделения (пары до 16:00)», "
        "«Работаю в офисе 5/2 с 9 до 18», «Сменный график 2/2», "
        "«Фриланс / свободный график».\n"
        "💬 Напишите ответ подробно, без выбора шаблонной кнопки."
    )
    return msg, VkKeyboards.empty()

def _step_handle_status(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    status = " ".join(clean_text.split())
    if len(status) < 3 or status.lower() in {"-", "нет"}:
        msg = (
            "Пожалуйста, опишите статус занятости и график подробнее:\n\n"
            "💡 Пример: «Студент дневного отделения (пары до 16:00)» "
            "или «Работаю в офисе 5/2 с 9 до 18»."
        )
        return msg, VkKeyboards.empty()
    if len(status) > 50:
        return (
            "Описание получилось слишком длинным. Укажите статус и график "
            "не более чем в 50 символах, например: «Работаю 5/2, 9–18»."
        ), VkKeyboards.empty()

    session.status = status
    session.state = DialogState.UNIVERSITY
    msg = (
        "Укажите ваш ВУЗ, колледж или место учёбы:\n\n"
        "💡 Пример: НИТУ МИСИС, НИУ ВШЭ, Колледж связи №54\n"
        "💬 Напишите название учебного заведения или нажмите «Пропустить», если не учитесь/учились."
    )
    return msg, VkKeyboards.skip("Пропустить")

def _step_handle_university(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    if lower_text in {"другое", "other"}:
        return "Напишите название другого учебного заведения.", VkKeyboards.empty()
    if lower_text in {"пропустить", "нет", "-", "—"}:
        session.university = "Не учусь"
    else:
        session.university = clean_text[:200]

    session.state = DialogState.FACULTY
    msg = (
        "Укажите ваш факультет или специальность:\n\n"
        "💡 Пример: Исторический факультет, Дизайн и медиа, Юриспруденция\n"
        "💬 Напишите направление текстом или нажмите «Пропустить»."
    )
    return msg, VkKeyboards.skip("Пропустить")

def _step_handle_faculty(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    if lower_text in {"пропустить", "нет", "-", "—"}:
        session.faculty = "Не указываю"
    else:
        session.faculty = clean_text[:200]

    session.state = DialogState.COURSE
    msg = (
        "На каком курсе и ступени обучения вы находитесь?\n\n"
        "💡 Пример: «1 курс аспирантуры», «3 курс бакалавриата», "
        "«2 курс базового высшего образования», «5 курс специалитета» "
        "или «Окончил обучение (выпускник)».\n"
        "💬 Напишите ваш вариант текстом."
    )
    return msg, VkKeyboards.course()

def _step_handle_course(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    if lower_text in {
        "выпускник / не учусь",
        "выпускник",
        "не учусь",
        "окончила обучение",
        "окончил обучение",
        "пропустить",
        "нет",
        "-",
        "—",
    }:
        session.course = "Окончил обучение (выпускник)"
    elif not clean_text:
        session.course = ""
    else:
        session.course = clean_text[:100]

    session.state = DialogState.SCIENTIFIC_INTERESTS
    msg = (
        "Какие темы, эпохи или направления в науке вам интересны? (Сфера научных или профессиональных интересов)\n\n"
        "💡 Пример: История России XIX века, Музейное дело и археология, История искусства\n"
        "💬 Напишите кратко своими словами или нажмите кнопку «Пропустить»."
    )
    return msg, VkKeyboards.skip("Пропустить")

def _step_handle_scientific_interests(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    if lower_text in {"пропустить", "нет", "-", "—"}:
        session.scientific_interests = "Нет"
    else:
        session.scientific_interests = clean_text[:2000]

    session.state = DialogState.LANGUAGES
    msg = (
        "Владеете ли вы иностранными языками на уровне B1 и выше "
        "(средний уровень и свободное владение)?\n\n"
        "💡 Пример: «Английский B2», «Французский B1», «Немецкий свободно» "
        "или напишите «Только русский», если не владеете.\n"
        "💬 Выберите подходящий вариант или перечислите языки текстом."
    )
    return msg, VkKeyboards.languages()

def _step_handle_languages(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    language_buttons = {
        "английский (b1-b2)": "Английский B1-B2",
        "английский (c1-c2)": "Английский C1-C2",
        "немецкий (b1+)": "Немецкий B1+",
    }
    if lower_text in language_buttons:
        session.foreign_languages = [language_buttons[lower_text]]
    elif lower_text in {"только русский / нет", "нет", "только русский", "русский", "пропустить", "-", "—"}:
        session.foreign_languages = ["Только русский"]
    else:
        langs = [p.strip() for p in re.split(r"[,;]+", clean_text) if p.strip()]
        session.foreign_languages = langs or ["Только русский"]

    session.state = DialogState.MOTIVATION
    msg = (
        "Мотивация и опыт 🏛️\n\n"
        "Почему именно Исторический музей? Что хотите попробовать и чему научиться?\n\n"
        "💡 Пример: Давно люблю экспозицию ГИМ, хочу увидеть музей изнутри, набраться опыта работы с выставками и общаться с интересными людьми.\n"
        "💬 Напишите ответ своими словами (минимум 10 символов)."
    )
    return msg, VkKeyboards.empty()

def _step_handle_motivation(self, session: UserSession, clean_text: str) -> tuple[str, str]:
    if len(clean_text) < 10:
        msg = (
            "Пожалуйста, расскажите чуть подробнее (минимум 10 символов), "
            "чтобы кураторы лучше поняли вашу мотивацию:\n\n"
            "💡 Пример: Давно люблю экспозицию ГИМ, хочу увидеть музей изнутри, набраться опыта работы с выставками и общаться с интересными людьми.\n"
            "💬 Напишите ответ своими словами."
        )
        return msg, VkKeyboards.empty()

    session.motivation = clean_text[:4000]
    session.state = DialogState.EXPECTATIONS
    return "Чего вы ожидаете от волонтёрства в ГИМ? Например: опыт общения с гостями, обучение, вклад в работу музея.", VkKeyboards.empty()

def _step_handle_expectations(self, session: UserSession, clean_text: str) -> tuple[str, str]:
    session.expectations = clean_text[:4000]
    session.state = DialogState.EXPERIENCE
    msg = (
        "Какой у вас есть волонтёрский опыт или полезные навыки?\n\n"
        "💡 Пример: Опыта в музеях нет, но организовывал мероприятия в школе/вузе, умею фотографировать и монтировать видео, грамотная речь.\n"
        "💬 Нажмите «Опыта нет, но хочу научиться!» или напишите свои навыки текстом."
    )
    return msg, VkKeyboards.experience()

def _step_handle_experience(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    if lower_text in {"опыта нет, но хочу научиться!", "опыта нет", "нет опыта", "пропустить", "-"}:
        session.experience = "Опыта пока нет, готов(а) учиться"
    else:
        session.experience = clean_text[:4000]

    session.state = DialogState.HOBBIES
    return "Какие у вас хобби и увлечения? Если не хотите указывать, напишите «Не указываю».", VkKeyboards.empty()

def _step_handle_hobbies(self, session: UserSession, clean_text: str) -> tuple[str, str]:
    session.hobbies = clean_text[:2000]
    session.state = DialogState.SOURCE
    return "Откуда узнали о программе: социальные сети, друзья, сайт Исторического музея или другое?", VkKeyboards.empty()

def _step_handle_source(self, session: UserSession, clean_text: str) -> tuple[str, str]:
    if not clean_text.strip():
        return "Укажите источник информации о программе.", VkKeyboards.empty()
    session.source = clean_text[:200]
    session.state = DialogState.DIRECTION
    msg = (
        "Направление и график ⏰\n\n"
        "В работу какого направления волонтёров ГИМ вам хотелось бы включиться?\n\n"
        "💡 Пример: Фондовое, Медиа-волонтёрство, Квесты и мастер-классы\n"
        "💬 Нажмите кнопку интересующего направления или напишите его название текстом."
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
        # Не блокируем пользователя на произвольном тексте: если написал что-то осмысленное, ставим undecided
        if len(clean_text) > 0:
            found_key = "undecided"
        else:
            msg = (
                "В работу какого направления волонтёров ГИМ вам хотелось бы включиться?\n\n"
                "💡 Пример: Фондовое, Медиа-волонтёрство, Квесты и мастер-классы\n"
                "💬 Нажмите кнопку интересующего направления или напишите его название текстом."
            )
            return msg, VkKeyboards.directions_inline()

    session.desired_direction = found_key
    session.state = DialogState.HOURS
    msg = (
        "Сколько часов в месяц вы готовы уделять волонтёрству в музее? "
        "Напишите просто число (например: 16) или выберите кнопку:\n\n"
        "💡 Можно написать «20 часов», но в ответе должно быть число от 0 до 400."
    )
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
            "Пожалуйста, укажите количество часов числом от 0 до 400.\n\n"
            "💡 Примеры: «16», «20 часов», «10–12 часов» — бот возьмёт первое число."
        )
        return msg, VkKeyboards.hours()

    session.availability_hours_month = hours
    session.state = DialogState.SCHEDULE
    msg = (
        "Какой формат графика вам предпочтительнее? (Какой график участия вам подходит?)\n\n"
        "💡 Пример: Гибкий (по сменам в свободные дни) или Регулярный (по фиксированным дням)\n"
        "💬 Нажмите кнопку выбора или укажите текстом, как вам удобнее."
    )
    return msg, VkKeyboards.schedule()

def _step_handle_schedule(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    if "регуляр" in lower_text or "фикс" in lower_text:
        session.availability_schedule_type = "Регулярный"
    else:
        session.availability_schedule_type = "Гибкий"

    session.state = DialogState.DAYS
    msg = (
        "В какие дни недели вам удобнее всего помогать?\n\n"
        "💡 Пример: Суббота и воскресенье, Вторник и четверг вечер, Любые дни по согласованию\n"
        "💬 Нажмите быструю кнопку или напишите конкретные дни текстом."
    )
    return msg, VkKeyboards.days()

def _step_handle_days(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    session.availability_days = [clean_text[:100]]
    session.state = DialogState.COMMITMENTS
    msg = (
        "Какие ещё дела могут влиять на ваш график (учёба, работа, сессии)? Как планируете совмещать? (Какие у вас есть другие регулярные обязательства?)\n\n"
        "💡 Пример: Учусь до 16:00 по будням, в декабре сессия, выходные полностью свободны.\n"
        "💬 Выберите вариант на кнопках или опишите вашу занятость текстом."
    )
    return msg, VkKeyboards.commitments()

def _step_handle_commitments(
    self, session: UserSession, clean_text: str, lower_text: str
) -> tuple[str, str]:
    session.other_commitments = clean_text[:1500]
    session.state = DialogState.OTHER_VOLUNTEERING
    return "Участвуете ли вы в других волонтёрских программах? Какие обязательства по времени они занимают? Можно ответить «Нет».", VkKeyboards.empty()

