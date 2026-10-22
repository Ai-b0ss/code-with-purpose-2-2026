"""Historical diagnostics must reject stale labels and keep cohorts separate."""
import unittest

from scripts.evaluate_semantic_regression import evaluate


class SemanticRegressionTests(unittest.TestCase):
    def test_rejects_labels_from_another_workbook_version(self):
        records = [
            {"source_row_hash": "a", "cohort": "autumn", "historical_score": 2},
            {"source_row_hash": "b", "cohort": "spring", "historical_score": 0},
        ]
        labels = [
            {"source_row_hash": "a", "fields": []},
            {"source_row_hash": "stale", "fields": []},
        ]
        with self.assertRaisesRegex(ValueError, "current historical workbooks"):
            evaluate(records, labels)

    def test_each_cohort_is_held_out_and_report_is_aggregate(self):
        records = [
            {"source_row_hash": "a", "cohort": "autumn", "historical_score": 2},
            {"source_row_hash": "b", "cohort": "autumn", "historical_score": 3},
            {"source_row_hash": "c", "cohort": "spring", "historical_score": 0},
            {"source_row_hash": "d", "cohort": "spring", "historical_score": 1},
        ]
        labels = [{"source_row_hash": row["source_row_hash"], "fields": []} for row in records]
        report = evaluate(records, labels)
        self.assertEqual(report["n"], 4)
        self.assertEqual({item["held_out_cohort"] for item in report["blocked_cohort_holdouts"]},
                         {"autumn", "spring"})
        self.assertTrue(all(item["train_n"] == item["test_n"] == 2
                            for item in report["blocked_cohort_holdouts"]))
        self.assertNotIn("source_row_hash", str(report))


if __name__ == "__main__":
    unittest.main()
