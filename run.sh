#!/usr/bin/env bash
set -e

# Переход в корень репозитория
cd "$(dirname "$0")"

# Проверка Python 3.11+
if ! command -v python3 &> /dev/null; then
    echo "Ошибка: python3 не найден. Установите Python 3.11+."
    exit 1
fi

# Создание виртуального окружения, если его ещё нет
if [ ! -d ".venv" ]; then
    echo "Создание виртуального окружения .venv..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi

# Активация окружения
source .venv/bin/activate

# Если зависимости не установлены — доустанавливаем
python -c "import fastapi, uvicorn, pydantic, certifi" 2>/dev/null || pip install -r requirements.txt certifi

# Настройка SSL-сертификатов для macOS (предотвращает CERTIFICATE_VERIFY_FAILED)
if python -c "import certifi" 2>/dev/null; then
    export SSL_CERT_FILE="$(python -m certifi)"
fi

PORT=${PORT:-8765}

echo "========================================================"
echo "  Музейный компас — Государственный исторический музей  "
echo "  Адрес: http://127.0.0.1:$PORT                        "
echo "  Режим: ${GIM_EVALUATION_MODE:-rules_demo}             "
echo "  Остановить сервер: Ctrl+C                             "
echo "========================================================"

exec uvicorn backend.app:app --host 127.0.0.1 --port "$PORT"
