# Запуск ГИМ и работа с API

Текущая версия в `main` — **запускаемый прототип**, в котором оценивание пока реализовано демонстрационными правилами `rules_demo`. Это **не полноценный LLM** и не согласованный балл куратора 0–3.

## Начало работы

```bash
git clone https://github.com/Ai-b0ss/code-with-purpose-2-2026.git
cd code-with-purpose-2-2026
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8765
```

На Windows можно запустить `run.bat`. Затем открыть **http://127.0.0.1:8765** *на ноутбуке, где запущен сервер*. Документация API: **http://127.0.0.1:8765/docs**.

## Фактически реализованные HTTP-методы

| Метод | URL | Результат |
| --- | --- | --- |
| GET | `/` | Четырёхшаговая веб-анкета |
| GET | `/api/v1/health` | Статус сервера и `evaluation_mode: rules_demo` |
| POST | `/api/v1/clarification` | Уточняющий вопрос по правилам |
| POST | `/api/v1/preview` | Демонстрационный разбор анкеты |
| POST | `/api/v1/applications` | Сохранение заявки и её демонстрационной оценки |
| GET | `/api/v1/applications` | Список записей |
| GET | `/api/v1/applications/{application_id}` | Полная сохранённая заявка |
| GET | `/api/v1/export.csv` | Общий CSV с сохранёнными заявками |

Основная модель запроса — `ApplicationInput` в `backend/app.py`. Новые поля согласовывать с Ильёй, который редактирует форму.

Сейчас **нет** метода записи в Google Sheets. **Отдельный LLM-оценщик предложен в [PR #13](https://github.com/Ai-b0ss/code-with-purpose-2-2026/pull/13), но пока не в `main` и не вызывается HTTP-маршрутами**. Поэтому существующее API по-прежнему использует `rules_demo`. Документы старого PR #10 описывали *планируемый*, а не реально работающий API — для интеграции ориентироваться на этот файл и `backend/app.py`.

## Где лежит код

- `frontend/index.html` — внешний вид и пошаговая анкета (**Илья**).
- `backend/app.py` — HTTP-эндпоинты, демонстрационный оценщик, SQLite и CSV (**Саня + Игорь**, согласовать разные функции).
- `tests/test_smoke.py` — пять модульных тестов.
- `tests/smoke_http.py` — проверка HTTP; требует **запущенного** сервера.
- `data/gim.sqlite3` — локальная база; по умолчанию каталог `data/` не коммитится в Git.

## Проверка

```bash
python -m unittest discover -s tests -v
python tests/smoke_http.py
```

Первый тест можно запускать без сервера. Второй — только после запуска Uvicorn на `127.0.0.1:8765`; он создаёт тестовую запись в локальной базе. Если `smoke_http.py` уже выполняли с фиксированным `application_id`, повторный запуск может корректно вернуть `created: false` (идемпотентность); используйте чистую тестовую базу для первого прогона.

## Предложенные изменения, ещё не слитые в `main`

- [PR #13 — самостоятельный оценщик](https://github.com/Ai-b0ss/code-with-purpose-2-2026/pull/13): `backend/evaluator.py`, `tests/test_evaluator.py`, [документация запуска](https://github.com/Ai-b0ss/code-with-purpose-2-2026/blob/feat/llm-evaluator/docs/11-llm-evaluator.md). По отчёту автора: 16 тестов PASS и 1 live SKIP. Live-модель и FastAPI-подключение пока не проверены.
- [PR #14 — локальный тестировщик](https://github.com/Ai-b0ss/code-with-purpose-2-2026/pull/14): `run-tests.bat`, `tests/run_local_smoke.py`, `tests/smoke_http.py`. По отчёту автора: 24 локальные проверки PASS. **Этот .bat ещё нельзя запускать из `main`, пока PR не слит.**
- **Зависимость:** PR #14 содержит также более ранние варианты файлов PR #13; перед merge нужно устранить это пересечение. GitHub Actions не используем из-за исчерпанного лимита.

## Следующие этапы

Полная анкета и UX → согласование/слияние PR оценщика и тестов → подключение настоящей модели → Google Sheets → проверка качества на исторических оценках → репетиция защиты. [Задачи](https://github.com/Ai-b0ss/code-with-purpose-2-2026/issues) · [План](09-delivery-roadmap.md).

## Проверочная сборка трёх линий — ещё не main

Чтобы проверить новую полную анкету Ильи, автономный оценщик Codex и локальные тесты Antigravity, переключиться на ветку review/gim-acceptance-20261021.

Windows: git fetch origin; git switch review/gim-acceptance-20261021; python -m pip install -r requirements.txt; run-tests.bat.

Ожидается 26 unit PASS, 1 live-LLM SKIP плюс успешный HTTP smoke. **Настоящий LLM пока не вызывается FastAPI-маршрутами.** См. [личную приёмку](12-acceptance-20261021.md).
