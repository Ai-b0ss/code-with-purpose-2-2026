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
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import requests
import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.utils import get_random_id

# Автоматически добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vk_bot.dispatcher import PerUserDispatcher

from vk_bot.api_client import GimApiClient
from vk_bot.config import (
    PROJECT_ROOT,
    SafeStreamHandler,
    BotConfig,
    load_dotenv_custom,
    print_setup_guide,
    load_config,
    logger,
)
from vk_bot.fsm import (
    DIRECTION_MAPPING,
    DialogState,
    UserSession,
    parse_and_validate_birth_date,
    VolunteerBotFSM,
)
from vk_bot.keyboards import VkKeyboards



# -----------------------------------------------------------------------------
# 5. ОБРАБОТКА СОБЫТИЯ И ЦИКЛ LONG POLL
# -----------------------------------------------------------------------------

class ThreadLocalVkClient:
    """Lazily create a VK session/API per worker, separate from Long Poll."""

    def __init__(self, token: str):
        self.token = token
        self._local = threading.local()

    def get_api(self) -> Any:
        if not hasattr(self._local, "api"):
            session = vk_api.VkApi(token=self.token)
            self._local.api = session.get_api()
            self._local.session = session
        return self._local.api


def _event_message(event: Any) -> Any:
    event_type = getattr(event, "type", None)
    if event_type != VkBotEventType.MESSAGE_NEW and event_type != VkBotEventType.MESSAGE_NEW.value:
        return None
    message = getattr(event, "message", None) or getattr(event, "obj", {})
    return message if callable(getattr(message, "get", None)) else None


def event_user_id(event: Any) -> int | None:
    """Extract a valid user ID without profile requests or FSM access."""
    message = _event_message(event)
    if message is None:
        return None
    raw_id = message.get("from_id") or message.get("user_id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)):
        return None
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError, OverflowError):
        return None
    return user_id if user_id > 0 else None


def _send_processing_status(vk: Any, user_id: int, status_text: str) -> None:
    try:
        vk.messages.setActivity(type="typing", peer_id=user_id)
    except Exception:
        pass
    vk.messages.send(user_id=user_id, random_id=get_random_id(), message=status_text)


def process_vk_message_event(
    event: Any,
    vk: Any,
    fsm: VolunteerBotFSM,
    user_name_cache: dict[int, str],
) -> None:
    """Process one event; callers configure any status notifier once on the FSM."""
    from_id = event_user_id(event)
    if from_id is None:
        return
    msg_obj = _event_message(event)
    text = msg_obj.get("text", "")

    try:
        if from_id not in user_name_cache:
            try:
                u_data = vk.users.get(user_ids=from_id)[0]
                first = u_data.get("first_name", "")
                last = u_data.get("last_name", "")
                user_name = f"{first} {last}".strip()
                user_name_cache[from_id] = user_name or f"Волонтёр #{from_id}"
            except Exception as err:
                logger.warning("profile_fetch_failed error_type=%s", type(err).__name__)
                user_name_cache[from_id] = f"Волонтёр #{from_id}"

        reply_text, keyboard = fsm.handle_message(
            user_id=from_id,
            text=str(text or ""),
            user_name=user_name_cache[from_id],
        )
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
            logger.error("vk_send_failed error_type=%s", type(err).__name__)
            if keyboard:
                send_kwargs.pop("keyboard", None)
                try:
                    vk.messages.send(**send_kwargs)
                except Exception as retry_err:
                    logger.error("vk_send_retry_failed error_type=%s", type(retry_err).__name__)
    except Exception as exc:
        # Exception messages/tracebacks can include an API token or an applicant's answers.
        # Keep this a bounded diagnostic even when a worker handler fails.
        logger.error("vk_event_failed error_type=%s", type(exc).__name__)

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
            logger.error("vk_group_lookup_failed error_type=%s", type(exc).__name__)
            print_setup_guide()
            sys.exit(1)

    # Проверка доступности бэкенда
    api_client = GimApiClient(base_url=config.backend_url)
    health = api_client.check_health()
    if health:
        logger.info("backend_connected")
    else:
        logger.warning("backend_unavailable: check that the local server is running")

    worker_vk = ThreadLocalVkClient(config.token)

    def status_notifier(user_id: int, status_text: str) -> None:
        _send_processing_status(worker_vk.get_api(), user_id, status_text)

    fsm = VolunteerBotFSM(
        api_client=api_client,
        notifier=status_notifier,
        session_path=os.getenv("GIM_VK_SESSION_DB", str(Path(__file__).resolve().parents[1] / "data" / "vk-sessions.sqlite3")),
    )

    logger.info("Подключение к Bots Long Poll сообщества %s...", group_id)
    longpoll = VkBotLongPoll(vk_session, group_id)
    user_name_cache: dict[int, str] = {}

    def process_event(event: Any) -> None:
        process_vk_message_event(event, worker_vk.get_api(), fsm, user_name_cache)

    # Single-process invariant: every event for a user goes through this dispatcher.
    # The shared FSM and caches are safe only with that per-user serialization.
    dispatcher = PerUserDispatcher(process_event, max_workers=16)
    logger.info("🚀 Бот волонтёрской программы ГИМ успешно запущен и ожидает сообщений!")

    try:
        while True:
            try:
                for event in longpoll.listen():
                    try:
                        user_id = event_user_id(event)
                        if user_id is not None:
                            dispatcher.submit(user_id, event)
                    except Exception as exc:
                        # Защитный барьер на уровне цикла: ошибка одного события
                        # не должна завершать генератор Long Poll.
                        logger.error("longpoll_dispatch_failed error_type=%s", type(exc).__name__)

            except requests.exceptions.ReadTimeout:
                # Штатный таймаут опроса Long Poll
                continue
            except requests.exceptions.ConnectionError:
                logger.warning("Потеря связи с сервером VK Long Poll. Переподключение через 5 сек...")
                time.sleep(5)
            except Exception as exc:
                logger.error("longpoll_loop_failed error_type=%s", type(exc).__name__)
                time.sleep(2)
    finally:
        dispatcher.close()


if __name__ == "__main__":
    run_bot()

