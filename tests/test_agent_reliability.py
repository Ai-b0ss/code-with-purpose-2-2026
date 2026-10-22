"""Regressions captured from real fictional DeepSeek requests; no network."""
import json
import unittest
from pathlib import Path

from backend.evaluator import (EvaluationError, evaluate, evaluation_schema,
                               validate_response, complete_validated)
from backend.clarification_agent import questions_before_verdict
from test_evaluator import candidate, response, FakeAdapter
from test_agent_clarification import plan, question


class SequenceAdapter:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.values)


class AgentReliabilityTests(unittest.TestCase):
    def test_real_nested_evidence_is_accepted_but_fabricated_quotes_are_not(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/evaluator/agent-contract-regressions.json").read_text(encoding="utf-8"))
        for fixture in fixtures[:2]:
            result = validate_response(fixture["raw"], fixture["answers"])
            self.assertIsInstance(result["score"], int)
            corrupted = json.loads(fixture["raw"])
            item = next(e for e in corrupted["evidence"] if e["question_id"].startswith("followup_answers."))
            item["reason"] += " выдуманная добавка"
            with self.assertRaises(EvaluationError):
                validate_response(json.dumps(corrupted), fixture["answers"])
        for fixture in fixtures[2:]:
            with self.assertRaises(EvaluationError):
                validate_response(fixture["raw"], fixture["answers"])

    def test_contract_retry_repairs_once_and_does_not_hide_second_failure(self):
        data = candidate()
        good = json.dumps(response(data), ensure_ascii=False)
        adapter = SequenceAdapter("{invalid", good)
        output = evaluate(data, adapter=adapter)
        self.assertEqual(output["model_attempts"], 2)
        repaired = json.loads(adapter.calls[1]["user"])
        self.assertEqual(repaired["contract_repair"]["error_codes"], ["json_syntax"])
        self.assertEqual(repaired["answers"], json.loads(adapter.calls[0]["user"])["answers"])
        bad = SequenceAdapter("{invalid", "{invalid")
        with self.assertRaises(EvaluationError):
            evaluate(data, adapter=bad)
        self.assertEqual(len(bad.calls), 2)

    def test_named_scorecard_and_contact_presence_without_identity(self):
        data = candidate().model_copy(update={"email": "fictional@example.com", "birth_date": "2000-01-01"})
        raw = response(data)
        raw["scorecard"] = {r["criterion"]: {k: v for k, v in r.items() if k != "criterion"}
                            for r in raw["scorecard"]}
        adapter = FakeAdapter(json.dumps(raw))
        output = evaluate(data, adapter=adapter)
        self.assertEqual(len(output["scorecard"]), 5)
        sent = json.loads(adapter.calls[0]["user"])["answers"]
        self.assertTrue(sent["contact_available"])
        for private in ("applicant_name", "application_id", "email", "vk_url", "birth_date"):
            self.assertNotIn(private, sent)
        schema = evaluation_schema()
        self.assertEqual(schema["properties"]["scorecard"]["type"], "object")
        self.assertEqual(len(schema["properties"]["scorecard"]["required"]), 5)
        self.assertNotIn("recommended_direction_label", schema["properties"])

    def test_contact_marker_and_second_round_budget(self):
        data = candidate().model_copy(update={"email": "fictional@example.com"})
        adapter = FakeAdapter(plan(question()))
        first = questions_before_verdict(data, adapter=adapter)
        self.assertTrue(json.loads(adapter.calls[0]["user"])["answers"]["contact_available"])
        revised = data.model_copy(update={"clarification_round": 1,
            "followup_questions": first["questions"],
            "followup_answers": {"ai_availability": "Иногда по субботам, график зависит от смен."}})
        new = {**question(), "question": "Сможете назвать хотя бы одну регулярную смену на ближайшие три месяца?"}
        second = questions_before_verdict(revised, adapter=FakeAdapter(plan(new)))
        self.assertEqual(second["round"], 2)
        self.assertEqual(second["questions"][0]["id"], "ai_availability_2")
        unknown = revised.model_copy(update={"followup_answers": {"ai_availability": "Пока не знаю"}})
        never_called = FakeAdapter("invalid")
        self.assertEqual(questions_before_verdict(unknown, adapter=never_called)["questions"], [])
        self.assertFalse(never_called.calls)
