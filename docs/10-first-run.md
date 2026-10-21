# Запуск и фактические API ГИМ

Нужны Python 3.11+, Git. Из корня репозитория:

```powershell
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

На Windows возможен `run.bat`. Открыть **http://127.0.0.1:8765** на том же ноутбуке; OpenAPI **http://127.0.0.1:8765/docs**.

## Методы

| Метод | Путь | Поведение |
| --- | --- | --- |
| GET | `/` | Четырёхшаговая анкета с дополнительными динамическими вопросами |
| GET | `/api/v1/health` | `status`, фактический `evaluation_mode` (llm или rules_demo), доступность fallback-настройки |
| POST | `/api/v1/clarification` | Дополнительные вопросы по правилам, без LLM |
| POST | `/api/v1/preview` | Предварительный разбор через настроенный оценщик |
| POST | `/api/v1/applications` | Сохранение заявки + результат оценщика (201); без явного согласия HTTP 422 |
| GET | `/api/v1/applications` | Список заявок |
| GET | `/api/v1/applications/{application_id}` | Ответы и результат по одной заявке |
| GET | `/api/v1/export.csv` | Выгрузка координатору: 32 колонки, разделитель `;`, UTF-8 BOM |

Повторная отправка того же `application_id` не создаёт строку повторно и **не обращается заново к LLM**. SQLite лежит в `data/gim.sqlite3`; для изолированного теста задать `GIM_DB_PATH`.

## Режимы оценки

По умолчанию используется честно обозначенный `rules_demo`. Настоящий LLM поддерживается через переменные окружения (полный endpoint URL):

```powershell
$env:GIM_EVALUATION_MODE = "llm"
$env:GIM_LLM_ENDPOINT = "http://127.0.0.1:8000/v1/chat/completions"
$env:GIM_LLM_MODEL = "your-local-model"
# $env:GIM_LLM_API_KEY = "..."  # только если провайдер требует ключ
# $env:GIM_LLM_FALLBACK_TO_DEMO = "1"  # необязательный явный резерв
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

Если `GIM_LLM_FALLBACK_TO_DEMO` не включён, ошибка/некорректный JSON модели возвращает HTTP 503 и **не выдаёт демо за ответ ИИ**. Если fallback включён, ответ содержит `evaluation_mode: rules_demo` и `evaluation_error`.

**Live-проверка:** отдельный ReformBoss DeepSeek-probe ранее PASS; контрольный POST на сервер 8766 21.10 вернул `503 / evaluator_provider_timeout` через 61,1 с. `health=llm` подтверждает конфигурацию, но не успешное обращение к провайдеру. `score=null` до калибровки.

Публичный экран показывает подтверждение и выбор кандидата. API пока возвращает подробную оценку и не требует авторизации при чтении заявок/CSV. До внешнего размещения выполнить [#28](https://github.com/Ai-b0ss/code-with-purpose-2-2026/issues/28).

## Проверка

```powershell
python -m unittest discover -s tests -v
.\run-tests.bat
```

Проверено на Windows после интеграции Ильи: **42 unit-теста: 41 PASS / 1 live SKIP**; локальный runner **60 PASS / 1 SKIP / 0 FAIL**. HTTP-проверки используют временную SQLite; рабочая база не изменяется. Chrome: полная форма → подтверждение → SQLite, без обнаруженных JS-ошибок.

[Настройка LLM](11-llm-evaluator.md) · [Чек-лист приёмки](12-acceptance-20261021.md).
