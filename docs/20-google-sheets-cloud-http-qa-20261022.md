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
