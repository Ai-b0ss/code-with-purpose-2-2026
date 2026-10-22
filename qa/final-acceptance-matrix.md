# Приёмка T01–T40

Дата: 22.10.2026, исполнитель Codex. Версия кода: product-source-sha256.txt, GitHub SHA в PR. Источник групп испытаний — tracking #9; названия строк ниже являются воспроизводимыми инженерными интерпретациями, а не новой официальной формулировкой ГИМ. PASS означает указанный технический результат, не готовность всего продукта. Входы только синтетические, кроме локального dry-run T35/T36 без вывода строк.

| ID | Проверка | Статус | Результат / шаг | Доказательство / остаток |
|---|---|---|---|---|
| T01 | web/VK entry | BLOCKED | Browser web PASS; FSM PASS; новый live VK не выполнен | #31/#57 |
| T02 | явное согласие | PASS | HTTP/FSM: false →422 до модели/SQLite | test_product_acceptance.py |
| T03 | политика и оператор | BLOCKED | Ссылка сохранена; основание/текст оператора не утверждены | #28 |
| T04 | календарные 18+ | PASS | 17л364д запрещено, ровно18 разрешено | test_age_boundary_dates_and_no_side_effects |
| T05 | ложный checkbox/границы | PASS | true не обходит возраст; false взрослого отклоняется; VK исправляем | test_vk_session_store.py |
| T06 | некорректные даты | PASS | пустые, будущие, невозможные/формат; 29.02; 0 side effects | test_age_boundary_dates_and_no_side_effects |
| T07 | 19 групп | PASS | Обязательные ответы, Другое, source VK; web browser | docs/gim-question-coverage.md |
| T08 | нет опыта/ноль часов | PASS | Нет опыта, нет интересов, 0ч, undecided принимаются | test_all_required_answers_and_valid_negative_answers |
| T09 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T10 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T11 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T12 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T13 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T14 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T15 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T16 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T17 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T18 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T19 | содержательность уточнений | BLOCKED | Offline контракт/бюджет есть; нужна экспертиза реальных новых ответов | qa/clarification-matrix.md; #27/#49 |
| T20 | время 5–7 минут | BLOCKED | Нет 5–10 живых проходов | #31 |
| T21 | лимит и отсутствие циклов | PASS | 2 раунда ≤3 вопроса; unknown и ответы не повторяются | test_agent_reliability.py; browser_acceptance.cjs |
| T22 | неизменность ответов | PASS | SQLite/CSV roundtrip кириллицы/переносов | test_csv_single_full_roundtrip_and_formula_safety |
| T23 | уровни 0/1/2/3/null | PASS | Contract валидирует, сервер считает; не доказательство точности | test_pilot_scoring.py |
| T24 | содержательная правильность оценок | BLOCKED | Нужна независимая разметка и сравнение | #27/#55 |
| T25 | стабильность модели | BLOCKED | Версии/run_id есть; новая реальная серия не выполнена | #56 |
| T26 | точность evidence | PASS | Чужие ID/выдуманные цитаты отвергаются; evidence exact | test_evaluator.py; test_agent_reliability.py |
| T27 | темы интервью и уточнения | BLOCKED | Разделены в контракте/UI; нужна оценка полезности экспертом | #27 |
| T28 | обратная связь кандидату | PASS | Шаблоны по направлению/часам/оплате, без score/HTML; browser+VK | test_feedback_has_specific_constraints_and_no_raw_html_or_score |
| T29 | рейтинг | PASS | 1000 строк, ничьи, null, стабильная пагинация и права | test_ranking_analytics_all_1000_records_and_filters |
| T30 | карточка | PASS | Защищённая карточка, исходные ответы и обоснования; browser | browser_acceptance.cjs |
| T31 | макроаналитика | PASS | Все 1000, знаменатели, unknown, фильтры, графики | test_ranking_analytics_all_1000_records_and_filters |
| T32 | одиночный CSV | PASS | header+одна запись, 43 поля, BOM, 404, auth | test_csv_single_full_roundtrip_and_formula_safety |
| T33 | полный CSV/Sheets | BLOCKED | CSV и RAW mocks PASS; новый live Google не выполнен | #53/#57 |
| T34 | Unicode/переносы/формулы | PASS | Roundtrip/download и Microsoft Excel 16.0 Open: 2 записи, 43 поля, Unicode/переносы, HasFormula=false | qa/windows-excel-acceptance-20261022.md |
| T35 | 112 строк | PASS | 33+24+55, 3 листа, хеши | qa/historical-import-summary.json |
| T36 | пропуск приёма не отказ | PASS | 67 unknown сохранены, 27 нулей балла сохранены | test_historical_import.py |
| T37 | аварийные состояния | BLOCKED | Concurrency/retry/deletion/restart unit PASS; полный live chaos не выполнен | #57 |
| T38 | чистый Windows старт | BLOCKED | Свежая папка/venv на Windows, demo HTTP/restart PASS; отдельная чистая ОС и llm/live транспорт не выполнены | #57; qa/windows-excel-acceptance-20261022.md |
| T39 | конфиденциальность | BLOCKED | Auth/receipt/LLM boundary/deletion negative tests PASS; внешние копии/основания не приняты | #28 |
| T40 | точность holdout | BLOCKED | Метрики/split/baselines реализованы; нет реальных predictions | #55 |
