"""End-to-end contract: full questionnaire -> AI asks candidate -> final verdict."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from fastapi import HTTPException

from backend.app import clarification, ApplicationInput
from backend.clarification_agent import questions_before_verdict
from backend.evaluator import EvaluationError, evaluate
from test_evaluator import candidate, response, FakeAdapter


def review(state="clear"):
    return [{"criterion": name, "state": state,
             "reason": "Сведения по критерию требуют проверки в конкретных ответах."}
            for name in ("motivation", "skills", "availability", "form_and_contact", "logistics")]


def plan(*questions, state="uncertain"):
    return json.dumps({"criteria_review": review(state), "questions": list(questions)}, ensure_ascii=False)


def question(criterion="availability"):
    return {"criterion": criterion,
            "question": "Какие конкретно дни доступны для регулярных смен в музее?",
            "reason": "Доступность по дням не указана, возможен конфликт регулярных смен.",
            "decision_impact": "Если доступны регулярные дни, риск нагрузки может уменьшиться."}


class ClarificationFlowTests(unittest.TestCase):
    def test_conditional_question_is_asked_before_verdict(self):
        data = candidate().model_copy(update={"availability_days": []})
        adapter = FakeAdapter(plan(question()))
        result = questions_before_verdict(data, adapter=adapter)
        self.assertEqual(result["evaluation_mode"], "llm")
        self.assertEqual(len(result["criteria_review"]), 5)
        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(result["questions"][0]["id"], "ai_availability")
        self.assertIn("максимум два", adapter.calls[0]["system"])
        sent = json.loads(adapter.calls[0]["user"])["answers"]
        self.assertNotIn("applicant_name", sent)
        self.assertNotIn("email", sent)
        self.assertNotIn("vk_url", sent)

        updated = data.model_copy(update={
            "followup_answers": {
                "ai_availability": "Могу стабильно каждую субботу с 10:00."
            }
        })
        verdict = response(updated)
        verdict["preliminary_verdict"] = "suggest_interview"
        verdict["verdict_comment"] = "Уточнены регулярные субботы; есть шанс согласовать задачи с куратором."
        verdict["interview_questions"] = []
        evaluation_adapter = FakeAdapter(json.dumps(verdict, ensure_ascii=False))
        evaluated = evaluate(updated, adapter=evaluation_adapter)
        self.assertEqual(evaluated["interview_questions"], [])
        self.assertEqual(evaluated["preliminary_verdict"], "suggest_interview")
        self.assertEqual(evaluated["score_status"], "pilot_unvalidated")
        self.assertIn("ai_availability", json.loads(evaluation_adapter.calls[0]["user"])["answers"]["followup_answers"])

    def test_no_questions_if_profile_is_clear(self):
        result = questions_before_verdict(candidate(), adapter=FakeAdapter(plan(state="clear")))
        self.assertEqual(result["questions"], [])

    def test_do_not_reask_answered_criterion(self):
        data = candidate().model_copy(update={"followup_answers": {
            "ai_availability": "Суббота и воскресенье, регулярно."
        }})
        with self.assertRaises(EvaluationError):
            questions_before_verdict(data, adapter=FakeAdapter(plan(question())))

    def test_invalid_criteria_and_duplicate_questions_fail_closed(self):
        for raw in (
            json.dumps({"criteria_review": review()[:-1], "questions": []}),
            plan(question(), question()),
            plan(question(), state="clear"),
            "{not-json",
        ):
            with self.subTest(raw=raw[:30]), self.assertRaises(EvaluationError):
                questions_before_verdict(candidate(), adapter=FakeAdapter(raw))

    def test_demo_is_explicit_and_does_not_need_key(self):
        result = questions_before_verdict(candidate(), mode="demo")
        self.assertEqual(result["evaluation_mode"], "rules_demo")
        self.assertEqual(result["criteria_review"], [])
        self.assertIsInstance(result["questions"], list)

    def test_endpoint_uses_llm_and_does_not_fallback_silently(self):
        mock = FakeAdapter(plan(question()))
        with patch("backend.clarification_agent.adapter_from_env", return_value=mock), patch.dict(
                "os.environ", {"GIM_EVALUATION_MODE": "llm"}):
            result = clarification(candidate())
            self.assertIsNone(result["question"])
            self.assertEqual(result["evaluation_mode"], "llm")
            self.assertEqual(len(result["questions"]), 1)
        with patch("backend.clarification_agent.adapter_from_env", return_value=FakeAdapter("INVALID")), patch.dict(
                "os.environ", {"GIM_EVALUATION_MODE": "llm"}):
            with self.assertRaises(HTTPException) as ctx:
                clarification(candidate())
            self.assertEqual(ctx.exception.status_code, 503)

    def test_frontend_has_qa_gate_and_clear_final_sequencing(self):
        from pathlib import Path
        html = (Path(__file__).resolve().parents[1] / "frontend/index.html").read_text(encoding="utf-8")
        self.assertIn('await api("/api/v1/clarification", payload)', html)
        self.assertIn('await api("/api/v1/applications", payload)', html)
        self.assertIn("for (const q of dynamicClarifications)", html)
        self.assertIn("ИИ изучает анкету", html)
        self.assertNotIn("res.question && (!dynamicClarifications", html)


if __name__ == "__main__":
    unittest.main()
