import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.analyze_acceptance_factors import audit, fisher_two_sided


class AcceptanceFactorTests(unittest.TestCase):
    def test_exact_two_sided_probabilities(self):
        self.assertAlmostEqual(fisher_two_sided(2, 2, 2, 4), 1 / 3)
        self.assertAlmostEqual(fisher_two_sided(1, 2, 2, 4), 1.0)

    def test_complete_selected_cohort_can_be_audited_before_other_cohort(self):
        outcomes = [
            ("a", "fall", 2, True, "other_or_empty"),
            ("b", "fall", 2, False, "other_or_empty"),
            ("c", "fall", 1, None, "other_or_empty"),
            ("d", "spring", 2, True, "other_or_empty"),
        ]
        rows = [{"source_row_hash": digest, "fields": [{
            "field_id": "expectations", "category": "specific_relevant"
        }]} for digest in ("a", "b", "c")]
        with TemporaryDirectory() as directory:
            queue = Path(directory) / "queue.jsonl"
            with patch("scripts.analyze_acceptance_factors.outcomes", return_value=outcomes):
                queue.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
                self.assertEqual(audit(queue, cohort="fall", score=2, minimum_group=1)["observed_n"], 2)
                queue.write_text("".join(json.dumps(row) + "\n" for row in rows[:2]), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "cover the selected cohort"):
                    audit(queue, cohort="fall", score=2, minimum_group=1)


if __name__ == "__main__":
    unittest.main()
