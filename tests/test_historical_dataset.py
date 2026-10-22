"""Configured historical columns must identify the same row in both audits."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from scripts.classify_historical_fields import DATASET_CONFIG, historical_records
from scripts.analyze_acceptance_anomalies import outcomes


class HistoricalDatasetTests(unittest.TestCase):
    def test_configured_headers_and_nested_field_keep_row_identity(self):
        config = DATASET_CONFIG
        with TemporaryDirectory() as folder:
            path = Path(folder) / "historical.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            headers = [
                config["score_header_contains"],
                config["accepted_header_contains"],
                config["direction_header_contains"],
                *config["field_headers_contain"].values(),
            ]
            sheet.append(headers)
            values = [2, "ДА", "Работа в фондах"]
            values.extend("Опыта пока нет" if field == "experience" else "Ответ"
                          for field in config["field_headers_contain"])
            sheet.append(values)
            workbook.save(path)

            with patch("scripts.classify_historical_fields.SOURCE_FILES", (path,)), \
                 patch("scripts.analyze_acceptance_anomalies.SOURCE_FILES", (path,)):
                records = list(historical_records())
                decisions = list(outcomes())
            self.assertEqual(len(records), 1)
            self.assertEqual(len(decisions), 1)
            self.assertEqual(records[0]["source_row_hash"], decisions[0][0])
            self.assertEqual(records[0]["answers"]["followup_answers"]["superpower"], "Ответ")
            self.assertTrue(decisions[0][3])
            self.assertEqual(decisions[0][4], "funds_only")


if __name__ == "__main__":
    unittest.main()
