"""Live, formula-backed overview in the same spreadsheet as application rows."""
from __future__ import annotations

from backend.google_sheets import SheetSyncError, _json, _quoted


TITLE = "Статистика"
MARKER = "Статистика заявок"
VERSION = "Версия 4"
CHART_TITLES = ("Уровень анкет", "Выбранные направления", "Средний уровень по критериям")


def _cell(value):
    if value == "":
        return {}
    key = "formulaValue" if isinstance(value, str) and value.startswith("=") else "stringValue"
    if isinstance(value, (int, float)):
        key = "numberValue"
    return {"userEnteredValue": {key: value}}


def _layout(application_title: str) -> list[list]:
    src = _quoted(application_title)
    ids = f"{src}!$A$2:$A"
    score = f"{src}!$AA$2:$AA"
    rows = [[""] * 8 for _ in range(29)]

    def put(row, col, value):
        rows[row - 1][col - 1] = value

    put(1, 1, MARKER)
    put(1, 8, VERSION)
    put(2, 1, "Источник: лист заявок. Формулы обновляются при добавлении и удалении строк.")
    put(3, 1, "Балл 0–3 — пилотная оценка анкеты, решение принимает координатор. Тестовые заявки учитываются.")
    for col, label in ((1, "Всего анкет"), (3, "С баллом"), (5, "Без балла"), (7, "Средний балл")):
        put(4, col, label)
    put(5, 2, f"=COUNTA({ids})")
    put(5, 4, "=SUM(B10:B13)")
    put(5, 6, "=B5-D5")
    put(5, 8, '=IF(D5=0,"",SUMPRODUCT(A10:A13,B10:B13)/D5)')

    put(8, 1, "Распределение баллов")
    put(9, 1, "Балл")
    put(9, 2, "Анкет")
    for row, level in enumerate(range(4), 10):
        put(row, 1, level)
        put(row, 2, f'=COUNTIFS({ids},"<>",{score},A{row})')
    put(14, 1, "Без оценки")
    put(14, 2, "=F5")

    put(8, 4, "Выбранное направление")
    put(9, 4, "Направление")
    put(9, 5, "Анкет")
    direction = f"{src}!$X$2:$X"
    for row, label in enumerate(("Организационное", "Проектное", "Фондовое", "Инклюзивное", "Медиа", "Пока не определился"), 10):
        put(row, 4, label)
        put(row, 5, f'=COUNTIFS({ids},"<>",{direction},D{row})')
    put(16, 4, "Не указано")
    put(16, 5, "=B5-SUM(E10:E15)")

    put(8, 7, "Предварительный вердикт")
    put(9, 7, "Вердикт")
    put(9, 8, "Анкет")
    verdict = f"{src}!$AG$2:$AG"
    for row, label in enumerate(("Рекомендовать собеседование", "Ручная проверка куратора", "Предварительно не рекомендован к собеседованию", "Недостаточно сведений"), 10):
        put(row, 7, label)
        put(row, 8, f'=COUNTIFS({ids},"<>",{verdict},G{row})')
    put(14, 7, "Не указан")
    put(14, 8, "=B5-SUM(H10:H13)")

    put(18, 1, "Пять критериев")
    put(19, 1, "Критерий")
    put(19, 2, "Средний балл")
    put(19, 3, "Оценено")
    for row, (col, label) in enumerate((("AI", "Мотивация"), ("AJ", "Опыт и навыки"),
                                        ("AK", "Доступность"), ("AL", "Анкета и связь"),
                                        ("AM", "Логистика")), 20):
        ref = f"{src}!${col}$2:${col}"
        put(row, 1, label)
        put(row, 3, "=" + "+".join(f'COUNTIFS({ids},"<>",{ref},{n})' for n in range(4)))
        put(row, 2, f'=IF(C{row}=0,"",(' + "+".join(
            f'{n}*COUNTIFS({ids},"<>",{ref},{n})' for n in range(1, 4)) + f')/C{row})')

    put(26, 1, "Рейтинг анкет с баллом")
    for col, label in enumerate(("ID заявки", "Имя", "Балл", "Рекомендуемое направление", "Вердикт", "Дата"), 1):
        put(28, col, label)
    # Convert historical RAW text scores to numbers; ignore missing and malformed scores.
    data = ",".join((f"{src}!A2:A", f"{src}!C2:C",
                     f"ARRAYFORMULA(IFERROR(VALUE({src}!AA2:AA)))",
                     f"{src}!AD2:AD", f"{src}!AG2:AG", f"{src}!B2:B"))
    put(29, 1, f'=IFERROR(SORT(FILTER({{{data}}},{src}!A2:A<>"",REGEXMATCH(TO_TEXT({src}!AA2:AA),"^[0-3]$")),3,FALSE,1,TRUE),"")')
    return rows


def _chart(sheet_id: int, title: str, domain_col: int, series_col: int,
           start_row: int, end_row: int, anchor_row: int, chart_type: str):
    def source(col):
        return {"sourceRange": {"sources": [{"sheetId": sheet_id,
                  "startRowIndex": start_row, "endRowIndex": end_row,
                  "startColumnIndex": col, "endColumnIndex": col + 1}]}}
    return {"addChart": {"chart": {
        "spec": {"title": title, "fontName": "Arial", "basicChart": {
            "chartType": chart_type, "legendPosition": "NO_LEGEND",
            "axis": [{"position": "BOTTOM_AXIS", "title": "Категория"},
                     {"position": "LEFT_AXIS", "title": "Анкет" if title != CHART_TITLES[2] else "Балл 0–3"}],
            "domains": [{"domain": source(domain_col)}],
            "series": [{"series": source(series_col), "targetAxis": "LEFT_AXIS"}],
            "headerCount": 1,
        }},
        "position": {"overlayPosition": {"anchorCell": {"sheetId": sheet_id,
            "rowIndex": anchor_row, "columnIndex": 9},
            "widthPixels": 620, "heightPixels": 350}},
    }}}


def ensure_dashboard(session, root: str, application_title: str, sheets: list[dict]):
    """Create or finish the overview; never touch application rows."""
    dashboard = next((s for s in sheets if s["properties"]["title"] == TITLE), None)
    requests = []
    if dashboard is None:
        # IDs are unique within a spreadsheet; Google allocates one for us.
        result = _json(session.post(root + ":batchUpdate", json={"requests": [
            {"addSheet": {"properties": {"title": TITLE,
                "gridProperties": {"rowCount": 1000, "columnCount": 18, "frozenRowCount": 3}}}}
        ]}, timeout=30))
        sheet_id = result["replies"][0]["addSheet"]["properties"]["sheetId"]
        charts = []
        initialize = True
    else:
        sheet_id = dashboard["properties"]["sheetId"]
        charts = dashboard.get("charts", [])
        first = _json(session.get(root + "/values/" + _quoted(TITLE) + "!A1:H1", timeout=30)).get("values", [])
        if first and first[0] and first[0][0] != MARKER:
            raise SheetSyncError("dashboard_name_conflict")
        initialize = not first or len(first[0]) < 8 or first[0][7] != VERSION
    if initialize:
        requests.append({"updateCells": {
            "start": {"sheetId": sheet_id, "rowIndex": 0, "columnIndex": 0},
            "rows": [{"values": [_cell(value) for value in row]} for row in _layout(application_title)],
            "fields": "userEnteredValue",
        }})
        for row in (1, 2):
            requests.append({"mergeCells": {"range": {"sheetId": sheet_id,
                "startRowIndex": row, "endRowIndex": row + 1,
                "startColumnIndex": 0, "endColumnIndex": 8}, "mergeType": "MERGE_ALL"}})
        requests.append({"repeatCell": {"range": {"sheetId": sheet_id,
            "startRowIndex": 1, "endRowIndex": 3,
            "startColumnIndex": 0, "endColumnIndex": 8},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat.wrapStrategy"}})
        for row in (0, 7, 17, 25, 27):
            requests.append({"repeatCell": {"range": {"sheetId": sheet_id,
                "startRowIndex": row, "endRowIndex": row + 1,
                "startColumnIndex": 0, "endColumnIndex": 8},
                "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.1, "green": 0.33, "blue": 0.32},
                    "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
                "fields": "userEnteredFormat"}})
        for col, width in enumerate((180, 180, 100, 280, 340, 210, 360, 120)):
            requests.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id,
                "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1},
                "properties": {"pixelSize": width}, "fields": "pixelSize"}})
        for row, height in ((0, 36), (1, 42), (2, 48)):
            requests.append({"updateDimensionProperties": {"range": {"sheetId": sheet_id,
                "dimension": "ROWS", "startIndex": row, "endIndex": row + 1},
                "properties": {"pixelSize": height}, "fields": "pixelSize"}})
    existing = {c.get("spec", {}).get("title") for c in charts}
    specs = ((CHART_TITLES[0], 0, 1, 8, 14, 2, "COLUMN"),
             (CHART_TITLES[1], 3, 4, 8, 16, 22, "BAR"),
             (CHART_TITLES[2], 0, 1, 18, 24, 42, "COLUMN"))
    for spec in specs:
        if spec[0] not in existing:
            requests.append(_chart(sheet_id, *spec))
    if requests:
        _json(session.post(root + ":batchUpdate", json={"requests": requests}, timeout=30))
