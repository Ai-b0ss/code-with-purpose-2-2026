import unittest

from backend.field_calibration import build_calibration, field_deviations, median_adjustment


class CalibrationTests(unittest.TestCase):
    def test_two_examples_have_small_effect(self):
        reference = build_calibration([
            {"fields": [{"field_id": "motivation", "quality_level": 1}]},
            {"fields": [{"field_id": "motivation", "quality_level": 1}]},
        ])
        delta = field_deviations([{"field_id": "motivation", "quality_level": 3}], reference)[0]
        self.assertEqual(delta["reference_median"], 1)
        self.assertEqual(delta["reference_n"], 2)
        self.assertEqual(delta["shrunk_deviation"], 0.182)

    def test_unreviewed_labels_do_not_create_reference(self):
        reference = build_calibration([{"fields": [{"field_id": "motivation", "quality_level": None}]}])
        self.assertEqual(reference["fields"], {})

    def test_median_effect_is_shrunk_and_bounded(self):
        few = build_calibration([
            {"fields": [{"field_id": "motivation", "quality_level": 1}]},
            {"fields": [{"field_id": "motivation", "quality_level": 1}]},
        ])
        many = build_calibration([
            {"fields": [{"field_id": "motivation", "quality_level": 1}]}
            for _ in range(100)
        ])
        review = [{"field_id": "motivation", "quality_level": 3}]
        self.assertLess(median_adjustment(review, calibration=few)["delta"], 0.1)
        self.assertGreater(median_adjustment(review, calibration=many)["delta"], 0.5)

    def test_duplicate_historical_id_cannot_inflate_median_sample(self):
        row = {"source_row_hash": "same", "fields": [{"field_id": "motivation", "quality_level": 3}]}
        with self.assertRaises(ValueError):
            build_calibration([row, row])

    def test_cohort_medians_are_visible_without_changing_global_reference(self):
        result = build_calibration([
            {"cohort": "fall", "fields": [{"field_id": "expectations", "quality_level": 3}]},
            {"cohort": "spring", "fields": [{"field_id": "expectations", "quality_level": 1}]},
        ])
        self.assertEqual(result["fields"]["expectations"]["median"], 2)
        self.assertEqual(result["cohorts"]["fall"]["expectations"]["median"], 3)
        self.assertEqual(result["cohorts"]["spring"]["expectations"]["median"], 1)

    def test_disputed_provisional_field_is_excluded_from_reference(self):
        rows = [
            {"source_row_hash": "a", "fields": [{"field_id": "expectations", "quality_level": 0}]},
            {"source_row_hash": "b", "fields": [{"field_id": "expectations", "quality_level": 3}]},
        ]
        result = build_calibration(rows, source="llm_historical_fields",
                                   exclude_field_keys={("a", "expectations")})
        self.assertEqual(result["fields"]["expectations"], {"n": 1, "median": 3})
        self.assertEqual(result["excluded_disputed_fields"], 1)

    def test_old_taxonomy_reference_cannot_shift_current_score(self):
        reference = build_calibration([
            {"fields": [{"field_id": "experience", "quality_level": 3}]}
            for _ in range(30)
        ])
        reference["taxonomy_version"] = "gim-field-quality-v1"
        effect = median_adjustment([{"field_id": "experience", "quality_level": 0}],
                                   calibration=reference)
        self.assertEqual(effect["status"], "stale_taxonomy")
        self.assertEqual(effect["delta"], 0)

    def test_current_taxonomy_provisional_reference_cannot_shift_score(self):
        reference = build_calibration([
            {"fields": [{"field_id": "motivation", "quality_level": 3}]}
            for _ in range(100)
        ], source="llm_historical_fields")
        effect = median_adjustment(
            [{"field_id": "motivation", "quality_level": 0}], calibration=reference
        )
        self.assertEqual(effect["status"], "unapproved_source")
        self.assertEqual(effect["delta"], 0)
        self.assertEqual(field_deviations(
            [{"field_id": "motivation", "quality_level": 0}], reference
        ), [])


if __name__ == "__main__":
    unittest.main()
