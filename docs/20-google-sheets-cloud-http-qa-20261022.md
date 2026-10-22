# Google Sheets: полный HTTP-прогон с облачным API (22.10.2026)

Проверка проведена на отдельном процессе Uvicorn с временной SQLite и настоящим Google Sheets API. В рабочую `data/gim.sqlite3` тест не писал. Все данные кандидатов вымышленные; адрес `synthetic@example.invalid` не принадлежит человеку. Режим оценки — `rules_demo`: ключ DeepSeek в этой сессии не был настроен.

Таблица: https://docs.google.com/spreadsheets/d/1iQEevsZFMB307trbntFZWQkatYBOVUiMNBqGzZoDXzE/edit, вкладка `Заявки API` (`gid=220874114`). JSON-ключ и токены в репозиторий и отчёт не включены.

| Сценарий | Наблюдение |
| --- | --- |
| `POST /api/v1/applications` с полным синтетическим набором полей | HTTP 201, `created=true`, `sheet_sync.status=synced`; [строка A5](https://docs.google.com/spreadsheets/d/1iQEevsZFMB307trbntFZWQkatYBOVUiMNBqGzZoDXzE/edit#gid=220874114&range=A5), ID `synthetic-http-6e549fb6c5ca` |
| Повтор того же ID; `GET /applications/{id}`; CSV | `created=false`, GET показывает `synced`, CSV содержит ID; облачное чтение находит ровно одну строку |
| Два параллельных POST с одним ID | Ответы `created=true` и `created=false`; облачное чтение находит одну [строку A6](https://docs.google.com/spreadsheets/d/1iQEevsZFMB307trbntFZWQkatYBOVUiMNBqGzZoDXzE/edit#gid=220874114&range=A6), ID `synthetic-concurrent-http-82dbb3532464` |
| Ошибка Google и повтор после перезапуска сервера | С неверным spreadsheet ID: HTTP 201, `sheet_sync.status=failed`, `error=google_http_404`, GET SQLite — 200. После запуска с верным ID `POST /applications/{id}/sync` дал `synced`; облачное чтение находит одну [строку A7](https://docs.google.com/spreadsheets/d/1iQEevsZFMB307trbntFZWQkatYBOVUiMNBqGzZoDXzE/edit#gid=220874114&range=A7), ID `synthetic-retry-http-5bcf646ed751` |
| Содержимое и формат | Google API вернул 32 значения в каждой строке; имя и тестовый email на нужных позициях, пустую оценку `score=null` без подмены нулём; метаданные ширины всех 32 колонок, диапазон 35–1088 px |

Локальный набор после прогона: `59 passed, 1 skipped, 54 subtests passed`. Этот прогон подтверждает путь HTTP → SQLite → Google Sheets и восстановление после ошибки. Связка с живой моделью DeepSeek и работа при длительной реальной сетевой недоступности Google в него не входили.

## Повторный сквозной прогон подписанных уточнений

После обновления сервера выполнен `python -m scripts.check_full_cloud_google_live`
на полностью вымышленной анкете с адресом `example.invalid`. Живой HTTP API
выдал один подписанный вопрос; изменение исходной мотивации с той же подписью
дало HTTP 422. Первый запрос сохранения вернул HTTP 503 ещё до SQLite-записи:
для тестового ID не осталось ни анкеты, ни резервирования. При повторе
уточнение снова прошло, оценщик перебрал `deepseek-v4.1-flash`, `glm-5.3`,
`kimi-k3` и завершил на локальной `qwen-local` согласно настроенному
порядку отказоустойчивости. Это не подтверждает доступность облачных
моделей на момент повторного сохранения.

Итог второго запроса: HTTP 201, `created=true`, пилотный балл 2,
`sheet_sync.status=synced`. Повтор **того же ID** вернул `created=false`;
SQLite содержит одну запись. Отдельное чтение колонки ID через Google
Sheets API нашло ровно одну [синтетическую строку A13](https://docs.google.com/spreadsheets/d/1iQEevsZFMB307trbntFZWQkatYBOVUiMNBqGzZoDXzE/edit#gid=220874114&range=A13)
с ID `qa-signed-cloud-google-20261022-01`. Перед возвратом статуса `synced`
транспорт выполнил `autoResizeDimensions` для всех записываемых колонок.
Эта проверка затронула рабочую SQLite только одной вымышленной анкетой.

## Полный прогон только с DeepSeek

Отдельный HTTP-сервер запущен с временной SQLite, `deepseek-v4.1-flash` и
пустым списком резервных моделей; локальная модель была отключена в этом
тестовом процессе. Скрипт `python -m scripts.check_full_cloud_google_live`
использовал новый полностью вымышленный ID
`qa-deepseek-cloud-google-20261022-01` и ожидал именно эту модель.

Результат: уточнение выдало один подписанный вопрос, подмена исходного
ответа получила HTTP 422, первый запрос сохранения вернул `created=true`,
`score_status=pilot_hybrid_unvalidated` и
`provider_model=deepseek-v4.1-flash`. Повтор того же ID вернул
`created=false`. В тестовой SQLite одна запись; чтение Google Sheets API
нашло ровно одну [синтетическую строку A14](https://docs.google.com/spreadsheets/d/1iQEevsZFMB307trbntFZWQkatYBOVUiMNBqGzZoDXzE/edit#gid=220874114&range=A14).
Тестовый сервер затем остановлен. Основной сервер на порту 8876 остался
в режиме `llm`.

Это подтверждает работоспособность цепочки «облачная оценка DeepSeek →
SQLite → Google Sheets» для одной синтетической анкеты. Надёжность
провайдера при длительной нагрузке и корректность пилотного балла по
историческим решениям этим прогоном не измерялись.
