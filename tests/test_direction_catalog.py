import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app import ApplicationInput, DIRECTIONS, app


ROOT = Path(__file__).resolve().parent.parent


class DirectionCatalogTests(unittest.TestCase):
    def test_catalog_is_shared_by_api_and_validation(self):
        catalog = TestClient(app).get("/api/v1/directions")
        self.assertEqual(catalog.status_code, 200)
        directions = catalog.json()["directions"]
        self.assertEqual(DIRECTIONS, {entry["id"]: entry["label"] for entry in directions})
        graph = json.loads((ROOT / "knowledge" / "gim_graph.json").read_text(encoding="utf-8"))
        fact_ids = {fact["id"] for fact in graph["triples"]}
        for entry in directions:
            self.assertTrue(set(entry["knowledge_fact_ids"]) <= fact_ids)

        valid = dict(
            applicant_name="Test", age_over_18=True,
            motivation="Хочу помогать музею", availability_hours_month=16,
            desired_direction=directions[0]["id"],
        )
        self.assertEqual(ApplicationInput(**valid).desired_direction, directions[0]["id"])
        with self.assertRaises(ValidationError):
            ApplicationInput(**{**valid, "desired_direction": "unknown"})

    def test_form_loads_catalog(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/v1/directions")', html)
        self.assertNotIn('value="organizational"', html)

    def test_weekdays_share_server_catalog_with_form(self):
        options = TestClient(app).get("/api/v1/weekdays")
        self.assertEqual(options.status_code, 200)
        configured = json.loads((ROOT / "knowledge" / "availability_rules.json").read_text(encoding="utf-8"))
        self.assertEqual(options.json(), [
            {"id": item["id"], "label": item["label"]}
            for item in configured["weekday_options"]
        ])
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/v1/weekdays")', html)
        self.assertNotIn('data-day="пн"', html)


if __name__ == "__main__":
    unittest.main()
