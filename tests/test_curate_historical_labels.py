import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts.curate_historical_labels import curate_row, prioritize_audit_disagreements
from scripts.build_field_calibration import main as build_calibration_main, require_full_cohort


class CurateHistoricalLabelsTests(unittest.TestCase):
    def test_model_error_requires_category_and_output_has_no_answer_text(self):
        source = {"source_row_hash": "example-hash", "answers": {
            "experience": "Провёл школьные экскурсии.",
            "followup_answers": {"superpower": "Умею спокойно объяснять сложные темы."},
        }}
        queue = {"source_row_hash": "example-hash", "cohort": "fictional",
                 "taxonomy_version": "gim-field-quality-v2",
                 "review_status": "needs_curator_approval_partial", "fields": [
                     {"field_id": "experience", "category": "specific_relevant", "quality_level": 3},
                     {"field_id": "followup_answers", "category": "unclassified_model_error",
                      "quality_level": None},
                 ]}
        choices = iter(["", "", "", "1", ""])
        output = curate_row(queue, source, ask=lambda _: next(choices), show=lambda _: None)
        self.assertEqual(output["fields"][1]["field_id"], "followup_answers.superpower")
        self.assertEqual(output["fields"][1]["category"], "specific_relevant")
        self.assertEqual(output["taxonomy_version"], "gim-field-quality-v2")
        self.assertNotIn("Провёл", str(output))
        self.assertNotIn("объяснять", str(output))

    def test_old_proposal_requires_explicit_category_choice(self):
        source = {"source_row_hash": "old-hash", "answers": {
            "experience": "Опыта волонтёрства пока нет.",
        }}
        queue = {"source_row_hash": "old-hash", "cohort": "fictional",
                 "review_status": "needs_curator_approval", "fields": [
                     {"field_id": "experience", "category": "relevant_general", "quality_level": 2},
                 ]}
        choices = iter(["", "explicit_none"])
        messages = []
        output = curate_row(queue, source, ask=lambda _: next(choices), show=messages.append)
        self.assertEqual(output["fields"][0]["category"], "explicit_none")
        self.assertIsNone(output["fields"][0]["quality_level"])
        self.assertTrue(any("Enter cannot accept" in message for message in messages))

    def test_skip_does_not_emit_partial_row(self):
        source = {"source_row_hash": "example-hash", "answers": {"experience": "Опыт"}}
        queue = {"source_row_hash": "example-hash", "cohort": "fictional",
                 "review_status": "needs_curator_approval", "fields": [
                     {"field_id": "experience", "category": "specific_relevant", "quality_level": 3},
                 ]}
        self.assertEqual(curate_row(queue, source, ask=lambda _: "s", show=lambda _: None), "skip")

    def test_runtime_calibration_requires_every_historical_row(self):
        with self.assertRaises(ValueError):
            require_full_cohort([{"source_row_hash": "a"}], {"a", "b"})
        require_full_cohort([{"source_row_hash": "a"}, {"source_row_hash": "b"}], {"a", "b"})

    def test_audit_disagreements_are_reviewed_first_and_stale_audit_fails(self):
        rows = [
            {"source_row_hash": "a", "fields": [{"field_id": "experience", "category": "relevant_general"}]},
            {"source_row_hash": "b", "fields": [{"field_id": "expectations", "category": "nonsense"}]},
        ]
        audit = [{"source_row_hash": "b", "field_id": "expectations",
                  "provisional_category": "nonsense", "audit_category": "relevant_general",
                  "disagrees": True, "error_code": None}]
        self.assertEqual(prioritize_audit_disagreements(rows, audit)[0]["source_row_hash"], "b")
        with self.assertRaises(ValueError):
            prioritize_audit_disagreements(rows, [{**audit[0], "provisional_category": "empty"}])

    def test_old_model_queue_cannot_be_rebranded_as_current_calibration(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "old.jsonl"
            output = Path(directory) / "calibration.json"
            source.write_text(json.dumps({
                "source_row_hash": "example", "cohort": "fictional",
                "review_status": "needs_curator_approval",
                "fields": [{"field_id": "experience", "category": "relevant_general",
                            "quality_level": 2}],
            }) + "\n", encoding="utf-8")
            with patch("sys.argv", ["build_field_calibration", str(source),
                                    "--provisional-llm-labels", "--out", str(output)]):
                with self.assertRaisesRegex(ValueError, "stale or unknown taxonomy"):
                    build_calibration_main()
            self.assertFalse(output.exists())

    def test_explicit_absence_cannot_carry_a_skill_level(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.jsonl"
            output = Path(directory) / "calibration.json"
            source.write_text(json.dumps({
                "source_row_hash": "example", "taxonomy_version": "gim-field-quality-v2",
                "cohort": "fictional", "fields": [{"field_id": "experience",
                                                 "category": "explicit_none", "quality_level": 2}],
            }) + "\n", encoding="utf-8")
            with patch("sys.argv", ["build_field_calibration", str(source),
                                    "--out", str(output)]):
                with self.assertRaisesRegex(ValueError, "non-quality field category"):
                    build_calibration_main()
            self.assertFalse(output.exists())

    def test_runtime_rejects_unreviewed_model_labels(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "partial.jsonl"
            output = Path(directory) / "runtime.json"
            source.write_text(json.dumps({
                "source_row_hash": "a", "taxonomy_version": "gim-field-quality-v2",
                "cohort": "fictional", "review_status": "needs_curator_approval",
                "fields": [{"field_id": "experience", "category": "explicit_none",
                            "quality_level": None}],
            }) + "\n", encoding="utf-8")
            with patch("sys.argv", ["build_field_calibration", str(source),
                                    "--provisional-llm-labels", "--out", str(output)]), \
                 patch("scripts.build_field_calibration.CALIBRATION_PATH", output):
                with self.assertRaisesRegex(ValueError, "Unreviewed model labels"):
                    build_calibration_main()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
