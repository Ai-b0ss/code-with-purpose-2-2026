import unittest
from pathlib import Path

from backend.knowledge_graph import GRAPH_PATH, query_from_field_reviews, retrieve_facts


class KnowledgeGraphTests(unittest.TestCase):
    def test_rejected_answer_text_cannot_steer_retrieval(self):
        answers = {
            "motivation": "Бессмыслица про фотосъёмку и фонды",
            "experience": "Проводил экскурсии для посетителей",
            "availability_hours_month": 12,
        }
        reviews = [
            {"field_id": "motivation", "category": "nonsense", "signals": []},
            {"field_id": "experience", "category": "specific_relevant", "signals": []},
            {"field_id": "availability_hours_month", "category": "structured_value", "signals": []},
        ]
        query = query_from_field_reviews(answers, reviews)
        self.assertNotIn("фотосъёмку", query)
        self.assertNotIn("фонды", query)
        self.assertIn("экскурсии", query)
        self.assertIn("график", query)

    def test_content_signal_contributes_a_graph_hint_without_raw_off_topic_text(self):
        query = query_from_field_reviews(
            {"motivation": "Несвязанный рекламный текст про смартфоны"},
            [{"field_id": "motivation", "category": "off_topic",
              "signals": ["museum_mismatch"]}],
        )
        self.assertNotIn("смартфоны", query)
        self.assertIn("музейные задачи", query)

    def test_nonsense_signal_cannot_steer_graph_to_collection_work(self):
        query = query_from_field_reviews(
            {"motivation": "Бессмысленный набор слов про фонды"},
            [{"field_id": "motivation", "category": "nonsense",
              "signals": ["exclusive_unavailable_role"]}],
        )
        self.assertNotIn("фонды", query)
        self.assertNotIn("альтернативные направления", query)

    def test_media_question_has_transcript_source(self):
        facts = retrieve_facts("Хочу снимать фото и делать дизайн для музея", subject="media")
        self.assertTrue(any(f["id"] == "k19" for f in facts))

    def test_selected_direction_includes_organizer_followup(self):
        facts = retrieve_facts("Помогал проводить мероприятия и встречать посетителей", subject="organizational")
        self.assertIn("k23", [fact["id"] for fact in facts])
        self.assertNotIn("k22", [fact["id"] for fact in facts])

    def test_unrelated_direction_is_not_injected_by_generic_word(self):
        facts = retrieve_facts(
            "Первый курс, полная ставка, несколько волонтерских проектов и мало времени",
            subject="organizational", signal_ids=["first_year", "full_time"],
        )
        self.assertIn("k07", [fact["id"] for fact in facts])
        self.assertNotIn("k12", [fact["id"] for fact in facts])

    def test_workload_risk_fact_requires_observed_risk(self):
        query = "Учёба и график регулярных смен"
        without_risk = retrieve_facts(query, subject="project", signal_ids=[])
        with_risk = retrieve_facts(query, subject="project", signal_ids=["first_year"])
        self.assertNotIn("k07", [fact["id"] for fact in without_risk])
        self.assertIn("k07", [fact["id"] for fact in with_risk])

    def test_no_experience_fact_requires_absent_experience(self):
        query = "опыт навыки образование"
        present = retrieve_facts(query, field_categories={"experience": "specific_relevant"})
        absent = retrieve_facts(query, field_categories={"experience": "explicit_none"})
        self.assertNotIn("k06", [fact["id"] for fact in present])
        self.assertIn("k06", [fact["id"] for fact in absent])

    def test_every_fact_has_existing_local_source(self):
        import json
        root = GRAPH_PATH.parent.parent
        for fact in json.loads(GRAPH_PATH.read_text(encoding="utf-8"))["triples"]:
            with self.subTest(fact=fact["id"]):
                self.assertTrue(fact["topics"])
                for source in fact["source"].split(";"):
                    path = source.split("#", 1)[0]
                    self.assertTrue((root / path).is_file(), path)

    def test_graph_sources_match_reviewed_fingerprints(self):
        import json
        from scripts.verify_knowledge_sources import MANIFEST, current_fingerprints
        expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["sha256"]
        self.assertEqual(current_fingerprints(), expected)

    def test_demo_questions_reference_known_facts_or_answer_fields(self):
        import json
        root = GRAPH_PATH.parent.parent
        graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
        facts = {fact["id"] for fact in graph["triples"]}
        rules = json.loads((root / "knowledge/demo_clarification_rules.json").read_text(encoding="utf-8"))
        for rule in rules["rules"]:
            with self.subTest(rule=rule["id"]):
                self.assertTrue(rule.get("source_fact_id") in facts or rule.get("source_field_id"))


if __name__ == "__main__":
    unittest.main()
