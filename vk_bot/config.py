from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

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

class SafeStreamHandler(logging.StreamHandler):
    """Не позволяет кодировке Windows-консоли ломать основной процесс."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            encoding = getattr(self.stream, "encoding", None)
            if encoding:
                message = message.encode(encoding, errors="backslashreplace").decode(
                    encoding, errors="replace"
                )
            self.stream.write(message + self.terminator)
            self.flush()
        except RecursionError:
            raise
        except Exception:
            self.handleError(record)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[SafeStreamHandler()],
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
                logger.warning("configuration_file_read_failed error_type=%s", type(exc).__name__)


@dataclass
class BotConfig:
    token: str
    group_id: int
    backend_url: str = "http://127.0.0.1:8765"


def print_setup_guide() -> None:
    """Выводит понятную инструкцию при отсутствии ключей."""
    guide = """
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
"""
    try:
        print(guide)
    except UnicodeEncodeError:
        print(guide.encode("ascii", errors="backslashreplace").decode("ascii"))


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


