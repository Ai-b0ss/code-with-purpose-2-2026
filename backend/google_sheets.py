"""Google Sheets transport. SQLite owns delivery state; Sheets is a projection."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

HEADERS = [
    "ID заявки", "Дата", "Имя", "Возраст 18+", "Согласие ПД", "Дата рождения",
    "Email", "VK", "Статус", "ВУЗ", "Факультет", "Курс", "Научные интересы",
    "Языки", "Мотивация", "Опыт", "Ожидания", "Хобби", "Часов/месяц", "Тип графика",
    "Дни", "Другие обязательства", "Другие волонтерства", "Направление", "Источник",
    "Дополнительный ответ", "Оценка 0-3", "Саммари", "Возможные проблемы",
    "Рекомендуемое направление", "Почему направление", "Вопросы для собеседования",
    "Предварительный вердикт", "Комментарий к вердикту",
    "Мотивация 0-3", "Навыки 0-3", "Доступность 0-3", "Анкета и связь 0-3", "Логистика 0-3",
    "Уточнения: вопросы и ответы", "Оценки критериев с обоснованием",
    "Статус оценки", "Пояснение оценки",
]
SCOPE = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetSyncError(Exception):
    """An error suitable for a stored status, with no PII or credentials."""


def configured() -> bool:
    return bool(os.getenv("GIM_GOOGLE_SPREADSHEET_ID"))


def _client():
    from google.auth import default
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    key_path = os.getenv("GIM_GOOGLE_CREDENTIALS_FILE")
    if key_path:
        credentials = service_account.Credentials.from_service_account_file(key_path, scopes=SCOPE)
    else:
        credentials, _ = default(scopes=SCOPE)
    return AuthorizedSession(credentials)


def _json(response):
    if not response.ok:
        # Provider bodies can contain user data. Keep only a stable HTTP code.
        raise SheetSyncError(f"google_http_{response.status_code}")
    return response.json()


def _column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _quoted(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _url(spreadsheet_id: str, gid: int, row_number: int) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={gid}&range=A{row_number}"


def write_row(application_id: str, values: list) -> str:
    """Find ID before every write, so a retry after an ambiguous timeout updates it."""
    spreadsheet_id = os.environ["GIM_GOOGLE_SPREADSHEET_ID"].strip()
    title = os.getenv("GIM_GOOGLE_SHEET_NAME", "Заявки API")
    root = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
    session = _client()
    if len(values) != len(HEADERS):
        raise SheetSyncError("sheet_values_mismatch")
    last = _column(len(HEADERS))
    metadata = _json(session.get(root, params={"fields": "sheets(properties(sheetId,title,gridProperties(columnCount)))"}, timeout=30))
    sheets = metadata.get("sheets", [])
    sheet = next((s["properties"] for s in sheets if s["properties"]["title"] == title), None)
    if sheet is None:
        created = _json(session.post(root + ":batchUpdate", json={"requests": [
            {"addSheet": {"properties": {"title": title}}}
        ]}, timeout=30))
        sheet = created["replies"][0]["addSheet"]["properties"]
    gid = sheet["sheetId"]
    if sheet.get("gridProperties", {}).get("columnCount", len(HEADERS)) < len(HEADERS):
        _json(session.post(root + ":batchUpdate", json={"requests": [{"updateSheetProperties": {
            "properties": {"sheetId": gid, "gridProperties": {"columnCount": len(HEADERS)}},
            "fields": "gridProperties.columnCount",
        }}]}, timeout=30))
    range_name = _quoted(title) + "!A:A"
    ids = _json(session.get(root + "/values/" + range_name, timeout=30)).get("values", [])
    if ids and ids[0] != [HEADERS[0]]:
        raise SheetSyncError("sheet_header_mismatch")
    existing_header = (_json(session.get(root + "/values/" + _quoted(title) + f"!A1:{last}1", timeout=30)).get("values") or [[]])[0]
    if existing_header and existing_header != HEADERS[:len(existing_header)]:
        raise SheetSyncError("sheet_header_mismatch")
    if existing_header != HEADERS:
        _json(session.put(root + "/values/" + _quoted(title) + f"!A1:{last}1",
                          params={"valueInputOption": "RAW"}, json={"values": [HEADERS]}, timeout=30))
    if not ids:
        ids = [[HEADERS[0]]]
    matches = [i + 1 for i, cells in enumerate(ids) if cells and cells[0] == application_id]
    if len(matches) > 1:
        raise SheetSyncError("duplicate_id_in_sheet")
    if matches:
        row_number = matches[0]
        _json(session.put(root + "/values/" + _quoted(title) + f"!A{row_number}:{last}{row_number}",
                          params={"valueInputOption": "RAW"}, json={"values": [values]}, timeout=30))
    else:
        result = _json(session.post(root + "/values/" + _quoted(title) + f"!A:{last}:append",
                                    params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                                    json={"values": [values]}, timeout=30))
        updated = result.get("updates", {}).get("updatedRange", "")
        match = re.search(r"![A-Z]+(\d+):", updated)
        if not match:
            raise SheetSyncError("missing_updated_range")
        row_number = int(match.group(1))
    _json(session.post(root + ":batchUpdate", json={"requests": [{"autoResizeDimensions": {
        "dimensions": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(HEADERS)}
    }}]}, timeout=30))
    return _url(spreadsheet_id, gid, row_number)
