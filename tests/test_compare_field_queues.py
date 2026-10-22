"""Queue drift must remain auditable without questionnaire text."""
import unittest

from scripts.compare_field_queues import compare
from scripts.curate_historical_labels import prioritize_audit_disagreements


class CompareFieldQueuesTests(unittest.TestCase):
    def test_partial_coverage_and_curator_priority(self):
        old = [
            {"source_row_hash": "a", "fields": [{"field_id": "experience",
                                                  "category": "relevant_general", "quality_level": 2}]},
            {"source_row_hash": "b", "fields": [{"field_id": "experience",
                                                  "category": "empty", "quality_level": None}]},
        ]
        new = [{"source_row_hash": "a", "fields": [{"field_id": "experience",
                                                 "category": "explicit_none", "quality_level": None}]}]
        summary, priority = compare(old, new)
        self.assertFalse(summary["complete_coverage"])
        self.assertEqual(summary["changed_fields"], 1)
        self.assertEqual(priority[0]["provisional_category"], "explicit_none")
        self.assertEqual(prioritize_audit_disagreements(new, priority), new)
        self.assertNotIn("answer", str((summary, priority)))

    def test_unknown_or_duplicate_rows_fail(self):
        old = [{"source_row_hash": "a", "fields": []}]
        with self.assertRaisesRegex(ValueError, "unknown"):
            compare(old, [{"source_row_hash": "b", "fields": []}])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            compare(old + old, old)


if __name__ == "__main__":
    unittest.main()
