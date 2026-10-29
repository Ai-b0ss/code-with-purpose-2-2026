from __future__ import annotations

import re
import time
import uuid
from typing import Any, Callable

import requests

from vk_bot.api_client import GimApiClient
from vk_bot.config import logger
from vk_bot.dialog_steps import (
    DIRECTION_MAPPING,
    DialogState,
    UserSession,
    parse_and_validate_birth_date,
)
from vk_bot.keyboards import VkKeyboards


class VolunteerBotFSM:
    """Конечный автомат (FSM) диалога волонтёрской анкеты ГИМ."""

    RESET_COMMANDS = {
        "/reset", "reset", "заново", "начать заново",
        "сброс", "отмена", "/start", "start",
    }

    EMAIL_REGEX = re.compile(r"^[\w\.\+\-]+@[a-zA-Z0-9\-]+(\.[a-zA-Z0-9\-]+)+$")

    def __init__(
        self,
        api_client: GimApiClient,
        session_ttl: int = 86400,
        notifier: Callable[[int, str], None] | None = None,
        session_path: str | None = None,
    ):
        self.api_client = api_client
        self.session_ttl = session_ttl
        self.notifier = notifier
        self.sessions: dict[int, UserSession] = {}
        self._last_processing_notice: dict[tuple[int, str], float] = {}
        self.store = None
        if session_path:
            from vk_bot.session_store import SessionStore
            self.store = SessionStore(session_path, session_ttl)
            for raw in self.store.load():
                raw['state'] = DialogState(raw['state'])
                session = UserSession(**raw)
                self.sessions[session.user_id] = session

    def _notify_processing(self, user_id: int, phase: str) -> None:
        # A notification is NOT proof of registration; it goes out before network I/O.
        now = time.monotonic()
        key = (user_id, phase)
        if now - self._last_processing_notice.get(key, float("-inf")) < 12:
            return
        self._last_processing_notice[key] = now
        if self.notifier is not None:
            try:
                message = (
                    "ИИ проверяет, нужны ли уточняющие вопросы. Анкета пока не отправлена."
                    if phase == "clarification" else
                    "ИИ формирует итоговую оценку и сохраняет заявку. "
                    "Не нажимайте повторно, пока не появится результат."
                )
                self.notifier(user_id, message)
            except Exception as exc:
                logger.warning("status_delivery_failed error_type=%s", type(exc).__name__)

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

    def handle_message(self, user_id, text, user_name=None):
        try:
            return self._handle_message(user_id, text, user_name)
        finally:
            if self.store and user_id in self.sessions:
                self.store.save(self.sessions[user_id])

    def _handle_message(
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

        if not clean_text:
            return "Ответ пустой. Напишите ответ на текущий вопрос или /reset.", VkKeyboards.empty()

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

        elif session.state == DialogState.EXPECTATIONS:
            return self._step_handle_expectations(session, clean_text)

        elif session.state == DialogState.EXPERIENCE:
            return self._step_handle_experience(session, clean_text, lower_text)

        elif session.state == DialogState.SOURCE:
            return self._step_handle_source(session, clean_text)

        elif session.state == DialogState.HOBBIES:
            return self._step_handle_hobbies(session, clean_text)

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

        elif session.state == DialogState.OTHER_VOLUNTEERING:
            session.other_volunteer_programs = clean_text[:1500]
            return self._fetch_clarifications_or_submit(session)

        elif session.state == DialogState.CHECK_RETRY:
            if lower_text not in {"повторить", "повторить проверку"}:
                return "Анкета пока не отправлена. Напишите «Повторить» для проверки уточнений.", VkKeyboards.empty()
            return self._fetch_clarifications_or_submit(session)

        elif session.state == DialogState.SUBMIT_RETRY:
            if lower_text != "повторить отправку":
                return "Ответы сохранены. Нажмите «Повторить отправку» для повторной попытки.", VkKeyboards.retry()
            return self._submit_final_application(session)

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

    from vk_bot.dialog_steps import (
        _greeting_response,
        _step_start_to_consent,
        _step_handle_consent,
        _step_handle_full_name,
        _step_handle_birth_date,
        _step_handle_email,
        _step_handle_status,
        _step_handle_university,
        _step_handle_faculty,
        _step_handle_course,
        _step_handle_scientific_interests,
        _step_handle_languages,
        _step_handle_motivation,
        _step_handle_expectations,
        _step_handle_experience,
        _step_handle_hobbies,
        _step_handle_source,
        _step_handle_direction,
        _step_handle_hours,
        _step_handle_schedule,
        _step_handle_days,
        _step_handle_commitments,
    )

    def _fetch_clarifications_or_submit(self, session: UserSession) -> tuple[str, str]:
        """Отправляет черновик в POST /api/v1/clarification."""
        if session.clarification_round >= 2:
            return self._submit_final_application(session)
        payload = session.to_application_input()
        self._notify_processing(session.user_id, "clarification")
        try:
            res = self.api_client.get_clarifications(payload)
            session.clarification_round = res.get("round", session.clarification_round + 1)
            questions = res.get("questions", [])
            # Оставляем только те вопросы, на которые ещё нет ответа
            unanswered = [
                q for q in questions
                if isinstance(q, dict) and q.get("id") and q["id"] not in session.followup_answers
            ]
            if unanswered:
                session.followup_questions.extend({k: str(v) for k, v in q.items()} for q in unanswered)
                session.pending_clarifications = unanswered
                session.state = DialogState.CLARIFICATION
                first_q = unanswered[0]
                reason = first_q.get("reason", "Кураторам важно уточнить детали")
                msg = (
                    "Уточняющий вопрос от кураторов 🔍\n\n"
                    f"📌 *{first_q.get('category', 'Уточнение')}*\n"
                    f"{first_q.get('question', '')}\n\n"
                    f"💡 *Пояснение:* _{reason}_\n"
                    "💬 Напишите ответ текстом или нажмите кнопку «Пропустить вопрос»."
                )
                return msg, VkKeyboards.clarification_skip()

        except Exception as exc:
            logger.warning("clarification_failed error_type=%s", type(exc).__name__)
            session.state = DialogState.CHECK_RETRY
            return ("Не удалось проверить уточнения ИИ. Анкета ещё не отправлена, ответы сохранены в текущем диалоге. Напишите «Повторить», чтобы повторить анализ.", VkKeyboards.empty())

        return self._submit_final_application(session)

    def _step_handle_clarification(
        self, session: UserSession, clean_text: str, lower_text: str
    ) -> tuple[str, str]:
        if not session.pending_clarifications:
            return self._fetch_clarifications_or_submit(session)

        curr_q = session.pending_clarifications.pop(0)
        q_id = curr_q.get("id", "followup")

        if lower_text in {"пропустить", "пропустить вопрос", "-", "без ответа"}:
            answer = "Без уточнений"
        else:
            answer = clean_text[:2000]

        session.followup_answers[q_id] = answer
        session.followup_answer = "\n".join(k + ": " + v for k, v in session.followup_answers.items())[:2000]

        # Если есть ещё вопросы — задаём следующий
        if session.pending_clarifications:
            next_q = session.pending_clarifications[0]
            reason = next_q.get("reason", "Кураторам важно уточнить детали")
            msg = (
                f"Следующий уточняющий вопрос 🔍:\n\n"
                f"📌 *{next_q.get('category', 'Уточнение')}*\n"
                f"{next_q.get('question', '')}\n\n"
                f"💡 *Пояснение:* _{reason}_\n"
                "💬 Напишите ответ текстом или нажмите кнопку «Пропустить вопрос»."
            )
            return msg, VkKeyboards.clarification_skip()

        return self._fetch_clarifications_or_submit(session)

    def _submit_final_application(self, session: UserSession) -> tuple[str, str]:
        """Отправляет заявку в POST /api/v1/applications."""
        # Один и тот же ID используется при повторной попытке. Это защищает от
        # дублей, если backend сохранил заявку, но ответ потерялся в сети.
        if not session.application_id:
            session.application_id = f"vk-{session.user_id}-{uuid.uuid4().hex}"
        payload = session.to_application_input()
        if self.store:
            self.store.save(session)
        self._notify_processing(session.user_id, "submission")
        try:
            res = self.api_client.submit_application(payload)
            app_id = res.get("application_id") or session.application_id
            session.application_id = app_id
            session.state = DialogState.DONE

            msg = (
                "🎉 Ваша заявка успешно принята!\n\n"
                f"🏛️ Регистрационный номер: #{app_id}\n\n"
                f"Кураторы волонтёрской программы свяжутся с вами здесь, "
                f"в личных сообщениях ВКонтакте (или по почте {session.email}).\n\n"
                "Спасибо за интерес к Государственному историческому музею! ✨"
            )
            msg += "\n\n" + res.get("candidate_feedback", "Решение принимает координатор после собеседования.")
            return msg, VkKeyboards.finished()

        except Exception as exc:
            detail = None
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                try:
                    detail = exc.response.json().get("detail")
                except (ValueError, AttributeError):
                    pass
            known_evaluator_codes = {
                "evaluator_provider_timeout", "evaluator_provider_http_error",
                "evaluator_provider_network_error", "evaluator_provider_dns_error",
                "evaluator_provider_tls_error", "evaluator_provider_tls_certificate",
                "evaluator_invalid_provider_response", "evaluator_incomplete_response",
                "evaluator_invalid_model_json", "evaluator_invalid_configuration",
                "evaluator_invalid_mode", "evaluator_response_too_large",
            }
            error_code = detail if isinstance(detail, str) and (
                detail in known_evaluator_codes or re.fullmatch(r"evaluator_provider_http_[0-9]{3}", detail)
            ) else "unknown"
            logger.error("submission_failed stage=final code=%s error_type=%s", error_code, type(exc).__name__)
            session.state = DialogState.SUBMIT_RETRY
            failure = (
                "Не удалось завершить оценку ИИ."
                if error_code != "unknown" else
                "Не удалось отправить заявку."
            )
            msg = (
                f"⚠️ {failure} Ответы сохранены, попробуйте кнопку «Повторить отправку».\n"
                "Нажмите «Повторить отправку» — прежний номер заявки будет использован повторно. "
                "Для начала заново используйте /reset."
            )
            return msg, VkKeyboards.retry()

