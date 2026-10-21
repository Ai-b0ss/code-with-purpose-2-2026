"""Write one fictional application, retry its ID, and print the Google row URL.

Requires GIM_GOOGLE_SPREADSHEET_ID and GIM_GOOGLE_CREDENTIALS_FILE or ADC.
Uses a temporary SQLite database and does not touch the working applicant store.
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
import argparse
from pathlib import Path

from backend.app import ApplicationInput, db_connection, retry_sheet_sync, save_application


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise-failure", action="store_true",
                        help="First use an invalid spreadsheet ID, then retry against the configured sheet")
    args = parser.parse_args()
    if not os.getenv("GIM_GOOGLE_SPREADSHEET_ID"):
        print("Set GIM_GOOGLE_SPREADSHEET_ID before running this probe.", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        real_spreadsheet_id = os.environ["GIM_GOOGLE_SPREADSHEET_ID"]
        os.environ["GIM_DB_PATH"] = str(Path(tmp) / "synthetic.sqlite3")
        os.environ["GIM_EVALUATION_MODE"] = "demo"
        application_id = "synthetic-google-" + uuid.uuid4().hex[:12]
        candidate = ApplicationInput(
            application_id=application_id,
            applicant_name="Тестовый кандидат (вымышленный)",
            age_over_18=True,
            consent_personal_data=True,
            motivation="Это синтетическая тестовая заявка для проверки записи в Google Sheets API.",
            availability_hours_month=12,
            desired_direction="organizational",
        )
        if args.exercise_failure:
            os.environ["GIM_GOOGLE_SPREADSHEET_ID"] = "invalid-sheet-for-synthetic-retry"
        first = save_application(candidate)
        os.environ["GIM_GOOGLE_SPREADSHEET_ID"] = real_spreadsheet_id
        if first["sheet_sync"]["status"] == "failed":
            print(f"Initial Google status: {first['sheet_sync']['error']}")
            with db_connection() as con:
                local_count = con.execute("SELECT COUNT(*) FROM applications WHERE id=?", (application_id,)).fetchone()[0]
            print(f"SQLite rows after Google error: {local_count}")
            first["sheet_sync"] = retry_sheet_sync(application_id)["sheet_sync"]
        second = save_application(candidate)
        with db_connection() as con:
            count = con.execute("SELECT COUNT(*) FROM applications WHERE id=?", (application_id,)).fetchone()[0]
        print(f"ID: {application_id}; SQLite rows: {count}; repeat created: {second['created']}")
        print(f"Google status: {first['sheet_sync']['status']}")
        if first["sheet_sync"]["status"] != "synced" or count != 1 or second["created"]:
            print(f"Error: {first['sheet_sync']['error']}", file=sys.stderr)
            return 1
        print("Synthetic row:", first["sheet_sync"]["row_url"])
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
