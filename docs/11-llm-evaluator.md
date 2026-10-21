# LLM-оценщик ГИМ — интеграция и контракт

## Фактическое состояние

`backend/evaluator.py` создан Codex в PR #13 и интегрирован с действующими HTTP-маршрутами через `backend/app.py` в объединённой сборке PR #16. При `GIM_EVALUATION_MODE=llm` маршруты `/api/v1/preview` и `/api/v1/applications` вызывают настоящий OpenAI-compatible адаптер. По умолчанию остаётся `rules_demo`.

**Проверено:** реальный локальный FastAPI → HTTP chat/completions (тестовый совместимый сервер) → валидируемый JSON → сохранение; повторная заявка по ID не повторяет запрос к модели. Это проверка транспорта, **не настоящей LLM**. Реальную бесплатную модель ещё предстоит найти и протестировать.

## Настройки

- `GIM_EVALUATION_MODE=demo` или `llm` (по умолчанию demo).
- `GIM_LLM_ENDPOINT` — полный URL совместимого `/v1/chat/completions`.
- `GIM_LLM_MODEL` — имя модели, загруженной сервером.
- `GIM_LLM_API_KEY` — необязательный Bearer, **не коммитить ключ**.
- `GIM_LLM_TIMEOUT` — секунды (по умолчанию 30).
- `GIM_LLM_JSON_MODE=1` — попросить ответ в формате JSON.
- `GIM_LLM_FALLBACK_TO_DEMO=1` — явный резерв при ошибке модели; без него ошибку показываем как HTTP 503.

Можно использовать локальную совместимую модель или доступный без оплаты сервер. **Ollama на подключённом Windows-компьютере в ходе проверки не обнаружена**, поэтому реальный live-модельный тест пропущен.

## Выход

JSON со следующими полями: `score: null`, `score_status: not_calibrated`, `evaluation_mode` (llm/rules_demo), `summary`, `strengths`, `possible_problems`, `recommended_direction`, `recommended_direction_label`, `direction_reason`, `fit_for_museum`, `fit_for_candidate`, `interview_questions` и `evidence`. Вызовы LLM проверяются по схеме Pydantic и дословным цитатам из переданных ответов. Текстовые ошибки провайдера не возвращаются пользователю в необработанном виде.

### Продуктовое правило ГИМ по нагрузке

**Первый курс бакалавриата, полная ставка и несколько параллельных волонтёрских проектов — отрицательные предварительные сигналы** по регулярности и риску недолгого участия. Логика `backend/availability_policy.py` добавляет их в `possible_problems` и `fit_for_museum` даже когда LLM недостаточно явно их выделила.

При конкретных стабильных свободных часах/днях возможно **индивидуальное исключение**. Никаких жёстких автоматических отказов по статусу и выдуманных числовых штрафов; решение остаётся за куратором. Пока шкала 0–3 не откалибрована, `score` — `null`.

[Технический запуск](10-first-run.md) · [Ответы музея](06-gim-stakeholder-answers.md) · [Проверка](12-acceptance-20261021.md).


## ReformBoss — запуск живой модели без записи API-ключа

Этот вариант предназначен для команды ГИМ и использует предоставленный участником OpenAI-compatible API:

- **Base URL**: `https://api.reformboss.com/v1`
- **Полный endpoint**: `https://api.reformboss.com/v1/chat/completions`
- **Первый кандидат**: `deepseek-v4.1-flash-1017` (ID из меню ReformBoss; НЕ путать с `deepseek-flash` официального сервиса DeepSeek)
- Для сравнения: `glm-5.3`, `kimi-k3`.

В PowerShell из корня клонированного репозитория:

```powershell
python -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-reformboss.ps1
```

Скрипт спросит ключ **скрытым вводом**, настроит переменные окружения только в своём процессе, выполнит **один настоящий API-запрос с вымышленной анкетой**, проверит JSON-контракт, после чего запустит FastAPI на `http://127.0.0.1:8765`. Ключ не сохраняется ни в файлах, ни в Git. Остановка: Ctrl+C.

Выбрать другую модель (одна проверка):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-reformboss.ps1 -Model glm-5.3
```

Сравнить **три модели** (три реальных вызова, расходуют лимит API; после проверки приложение запускается на выбранной модели):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-reformboss.ps1 -Compare
```

Если шлюз не принимает `response_format: json_object`, повторите с `-DisableJsonMode`; проверка JSON остаётся включённой в приложении. Проверка без дополнительного вызова при запуске: `-SkipProbe`.

**Не путать лимит сообщений Telegram-бота и лимит ReformBoss API.** Узнайте ограничения непосредственно в сервисе. Используйте только вымышленные анкеты до подтверждения условий обработки данных. Если live-проверка не прошла, скрипт не запускает приложение в режиме LLM и не выдаёт `rules_demo` за настоящий ИИ.

Следующий шаг после подключения: 4 кейса Евы, ручное сравнение полезности вывода и проверка Google Sheets. Запись в Google Sheets в этом скрипте **не реализована**.
