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

**Live-проверка:** после перезапуска сервера 8766 настоящий API → DeepSeek → SQLite PASS за 50,5 с; повтор ID без дубля за 0,03 с. Прежний 503 вызван клиентским таймаутом 60 с: прямой полный запрос занял 93 с и прошёл schema/evidence. Для ReformBoss используйте `start-reformboss.ps1 -TimeoutSeconds 180` (по умолчанию 180). Пробник использует тот же GIM_LLM_TIMEOUT; `--timeout` явно переопределяет его. Универсальный адаптер без переменной окружения сохраняет собственный default 30 с. `score=null` до калибровки. [Протокол](14-deepseek-timeout-20261021.md).

Публичный экран показывает подтверждение и выбор кандидата. API пока возвращает подробную оценку и не требует авторизации при чтении заявок/CSV. До внешнего размещения выполнить [#28](https://github.com/Ai-b0ss/code-with-purpose-2-2026/issues/28).

## Проверка

```powershell
python -m unittest discover -s tests -v
.\run-tests.bat
```

Проверено на Windows после интеграции Ильи: **43 unit-теста: 42 PASS / 1 live SKIP**; локальный runner **61 PASS / 1 SKIP / 0 FAIL**. HTTP-проверки используют временную SQLite; рабочая база не изменяется. Chrome: полная форма → подтверждение → SQLite, без обнаруженных JS-ошибок.

[Настройка LLM](11-llm-evaluator.md) · [Чек-лист приёмки](12-acceptance-20261021.md).

## Ограничения текущего контракта после аудита веток

Последовательный повтор уже сохранённого ID проверен; два одновременных POST с тем же ID могут вызвать оценщик дважды и завершить один запрос IntegrityError (#31). Это не гарантированная exactly-once обработка. При сбое модели новая запись пока не появляется в SQLite.

В main нет /sessions и rag_llm. Они описаны отдельно в [архитектуре](architecture.md) и существуют только в ветке Саши. Её новые сессии пока не попадают в старый CSV; новую форму нельзя считать готовой выгрузкой без #32. Не заменять запуск main инструкцией start-scoring из экспериментальной ветки.
