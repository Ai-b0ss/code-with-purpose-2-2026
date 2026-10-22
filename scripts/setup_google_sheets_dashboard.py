"""Create the live overview for an already configured Google spreadsheet."""
from __future__ import annotations

import os

from dotenv import load_dotenv

from backend.google_sheets import _client, _json
from backend.sheets_dashboard import ensure_dashboard


def main():
    load_dotenv()
    spreadsheet_id = os.environ["GIM_GOOGLE_SPREADSHEET_ID"].strip()
    title = os.getenv("GIM_GOOGLE_SHEET_NAME", "Заявки")
    root = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
    session = _client()
    sheets = _json(session.get(root, params={
        "fields": "sheets(properties(sheetId,title),charts(chartId,spec(title)))"
    }, timeout=30)).get("sheets", [])
    if not any(sheet["properties"]["title"] == title for sheet in sheets):
        raise SystemExit("Лист заявок не найден; статистика не создана.")
    ensure_dashboard(session, root, title, sheets)
    print(f"Статистика: https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit")


if __name__ == "__main__":
    main()
