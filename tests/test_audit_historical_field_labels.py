import unittest

from scripts.audit_historical_field_labels import audit, disputed_field_keys, field_payload, targets


class HistoricalFieldAuditTests(unittest.TestCase):
    def test_targets_reject_stale_or_duplicate_source(self):
        labels = [{"source_row_hash": "a", "cohort": "season", "fields": [
            {"field_id": "expectations", "category": "nonsense"}]}]
        source = {"a": {"answers": {"expectations": "Ответ"}}}
        self.assertEqual(len(targets(labels, source)), 1)
        with self.assertRaises(ValueError):
            targets(labels, {"b": source["a"]})
        with self.assertRaises(ValueError):
            targets(labels * 2, source)

    def test_nested_payload_keeps_field_id_and_audit_does_not_export_answer(self):
        self.assertEqual(field_payload({"followup_answers": {"superpower": "Ответ"}},
                                       "followup_answers.superpower"),
                         {"followup_answers": {"superpower": "Ответ"}})
        self.assertEqual(field_payload({"items": ["a", "b", "Ответ"]}, "items.2"),
                         {"items": ["", "", "Ответ"]})

        class FakeAdapter:
            last_model = "cloud-test"

        import scripts.audit_historical_field_labels as module
        original = module.review_fields
        module.review_fields = lambda payload, adapter: [{
            "field_id": "expectations", "category": "relevant_general"}]
        try:
            labels = [{"source_row_hash": "a", "cohort": "season", "fields": [
                {"field_id": "expectations", "category": "nonsense"}]}]
            source = {"a": {"answers": {"expectations": "Секретный ответ"}}}
            result = audit(labels, source, FakeAdapter())
            self.assertTrue(result[0]["disagrees"])
            self.assertNotIn("Секретный ответ", str(result))
        finally:
            module.review_fields = original

    def test_disputed_keys_require_matching_current_labels(self):
        labels = [{"source_row_hash": "a", "fields": [
            {"field_id": "expectations", "category": "nonsense"}]}]
        audit_rows = [{"source_row_hash": "a", "field_id": "expectations",
                       "provisional_category": "nonsense", "disagrees": True}]
        self.assertEqual(disputed_field_keys(labels, audit_rows), {("a", "expectations")})
        with self.assertRaises(ValueError):
            disputed_field_keys(labels, [{**audit_rows[0], "provisional_category": "empty"}])

    def test_positive_sample_is_stable_and_outcome_blind(self):
        labels = [
            {"source_row_hash": row_hash, "cohort": "fall", "historical_score": score,
             "fields": [{"field_id": "expectations", "category": "specific_relevant",
                         "quality_level": 3}]}
            for row_hash, score in (("a", 0), ("b", 3), ("c", 1))
        ]
        source = {row["source_row_hash"]: {"answers": {"expectations": "Ответ"}} for row in labels}
        first = targets(labels, source, mode="positive", per_stratum=1)
        reversed_scores = [{**row, "historical_score": 3 - row["historical_score"]} for row in labels]
        second = targets(reversed_scores, source, mode="positive", per_stratum=1)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0]["source_row_hash"], second[0][0]["source_row_hash"])


if __name__ == "__main__":
    unittest.main()
