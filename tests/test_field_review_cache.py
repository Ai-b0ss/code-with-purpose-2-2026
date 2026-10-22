"""Repeated classifications should retain one verified semantic label."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.field_quality import review_fields
from backend.field_review_cache import FieldReviewCache


class LabelAdapter:
    def __init__(self, category):
        self.category = category
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["user"])
        return json.dumps({"fields": [
            {"field_id": field_id, "category": self.category, "signals": [],
             "evidence": answer}
            for field_id, answer in payload["answers"].items()
        ]}, ensure_ascii=False)


class FieldReviewCacheTests(unittest.TestCase):
    def test_same_answer_reuses_first_verified_label_without_plaintext(self):
        answer = "Помогал в организации школьных экскурсий."
        with TemporaryDirectory() as folder:
            cache_path = Path(folder) / "reviews.sqlite3"
            cache = FieldReviewCache(cache_path)
            first_adapter = LabelAdapter("relevant_general")
            first = review_fields({"experience": answer}, first_adapter, cache=cache)
            second_adapter = LabelAdapter("specific_relevant")
            second = review_fields({"experience": answer}, second_adapter, cache=cache)
            self.assertEqual(first, second)
            self.assertEqual(first_adapter.calls, 1)
            self.assertEqual(second_adapter.calls, 0)
            self.assertNotIn(answer.encode("utf-8"), cache_path.read_bytes())

    def test_question_changes_cache_key(self):
        answer = "Помогал в организации школьных экскурсий."
        with TemporaryDirectory() as folder:
            cache = FieldReviewCache(Path(folder) / "reviews.sqlite3")
            first = review_fields({"experience": answer}, LabelAdapter("relevant_general"),
                                  {"experience": "Какие экскурсии?"}, cache=cache)
            second = review_fields({"experience": answer}, LabelAdapter("specific_relevant"),
                                   {"experience": "Был ли это музей?"}, cache=cache)
            self.assertNotEqual(first[0]["category"], second[0]["category"])

    def test_first_insert_wins_for_same_key(self):
        answer = "Помогал в организации школьных экскурсий."
        with TemporaryDirectory() as folder:
            cache = FieldReviewCache(Path(folder) / "reviews.sqlite3")
            first = {"category": "relevant_general", "signals": [], "evidence": answer}
            competing = {"category": "specific_relevant", "signals": [], "evidence": answer}
            self.assertEqual(cache.put("experience", answer, "", first)["category"],
                             "relevant_general")
            self.assertEqual(cache.put("experience", answer, "", competing)["category"],
                             "relevant_general")

    def test_prompt_change_invalidates_cached_label(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            code = root / "classifier.py"
            taxonomy = root / "taxonomy.json"
            prompt = root / "prompt.txt"
            code.write_text("classifier", encoding="utf-8")
            taxonomy.write_text('{"categories":{"explicit_none":"No experience"},"signals":{}}',
                                encoding="utf-8")
            prompt.write_text("version one", encoding="utf-8")
            with patch("backend.field_review_cache.SOURCE_FILES", (code, taxonomy, prompt)):
                first = FieldReviewCache(root / "reviews.sqlite3")
                first.put("experience", "Опыта нет.", "Опыт?", {
                    "category": "explicit_none", "signals": [], "evidence": "Опыта нет."
                })
                prompt.write_text("version two", encoding="utf-8")
                second = FieldReviewCache(root / "reviews.sqlite3")
                self.assertNotEqual(first.classifier_version, second.classifier_version)
                self.assertIsNone(second.get("experience", "Опыта нет.", "Опыт?"))


if __name__ == "__main__":
    unittest.main()
