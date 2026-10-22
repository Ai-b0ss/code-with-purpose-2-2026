"""End-to-end contract: full questionnaire -> AI asks candidate -> final verdict."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from fastapi import HTTPException

from backend.app import clarification, ApplicationInput
from backend.clarification_agent import questions_before_verdict
from backend.evaluator import EvaluationError, evaluate_legacy_contract
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
            "decision_impact": "Если доступны регулярные дни, риск нагрузки может уменьшиться.",
            "source_fact_ids": ["k07"], "trigger_field_ids": ["availability_hours_month"]}


class ClarificationAdapter(FakeAdapter):
    def __init__(self, result, *, categories=None, signals=None):
        super().__init__(result)
        self.categories = categories or {}
        self.signals = signals or {}

    def complete(self, **kwargs):
        payload = json.loads(kwargs["user"])
        if "knowledge_facts" in payload:
            return super().complete(**kwargs)
        self.calls.append(kwargs)
        return json.dumps({"fields": [
            {"field_id": field_id, "category": self.categories.get(field_id, "relevant_general"),
             "signals": self.signals.get(field_id, []), "evidence": value[:300]}
            for field_id, value in payload["answers"].items()
        ]}, ensure_ascii=False)


class ClarificationFlowTests(unittest.TestCase):
    def test_conditional_question_is_asked_before_verdict(self):
        data = candidate().model_copy(update={"availability_days": []})
        adapter = ClarificationAdapter(plan(question()))
        result = questions_before_verdict(data, adapter=adapter)
        self.assertEqual(result["evaluation_mode"], "llm")
        self.assertEqual(len(result["criteria_review"]), 5)
        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(result["questions"][0]["id"], "ai_availability")
        self.assertIn("максимум два", adapter.calls[1]["system"])
        sent = json.loads(adapter.calls[1]["user"])["answers"]
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
        evaluated = evaluate_legacy_contract(updated, adapter=evaluation_adapter)
        self.assertEqual(evaluated["interview_questions"], [])
        self.assertEqual(evaluated["preliminary_verdict"], "suggest_interview")
        self.assertEqual(evaluated["score_status"], "pilot_unvalidated")
        self.assertIn("ai_availability", json.loads(evaluation_adapter.calls[0]["user"])["answers"]["followup_answers"])

    def test_no_questions_if_profile_is_clear(self):
        result = questions_before_verdict(candidate(), adapter=ClarificationAdapter(plan(state="clear")))
        self.assertEqual(result["questions"], [])

    def test_previous_signed_question_is_classifier_context(self):
        asked = "Что именно вы делали при подготовке экскурсии?"
        data = candidate().model_copy(update={
            "followup_questions": [{"id": "ai_skills", "question": asked}],
            "followup_answers": {"ai_skills": "Составил маршрут и провёл группу."},
            "followup_signature": "f" * 64,
        })
        adapter = ClarificationAdapter(plan(state="clear"))
        questions_before_verdict(data, adapter=adapter)
        classified = json.loads(adapter.calls[0]["user"])
        self.assertEqual(classified["questions"]["followup_answers.ai_skills"], asked)
        self.assertNotIn("followup_signature", adapter.calls[0]["user"])

    def test_abusive_answer_stops_question_generation(self):
        data = candidate().model_copy(update={"motivation": "Вы все тупые идиоты, идите к черту."})
        adapter = ClarificationAdapter(plan(question()), categories={"motivation": "abusive"})
        result = questions_before_verdict(data, adapter=adapter)
        self.assertEqual(result["screening_outcome"], "screen_out_for_curator_review")
        self.assertEqual(result["questions"], [])
        self.assertEqual(len(adapter.calls), 1)

    def test_nonsense_answer_is_hidden_from_question_planner(self):
        data = candidate().model_copy(update={"motivation": "Кабачок и фотосъёмка на луне без смысла."})
        q = {"criterion": "motivation", "question": "Какая музейная задача вам интересна?",
             "reason": "Мотивацию нужно уточнить перед предварительным решением.",
             "decision_impact": "Конкретный интерес позволит обсудить подходящее направление.",
             "source_fact_ids": ["k02"], "trigger_field_ids": ["motivation"]}
        adapter = ClarificationAdapter(plan(q), categories={"motivation": "nonsense"})
        result = questions_before_verdict(data, adapter=adapter)
        self.assertEqual(result["screening_outcome"], "strong_negative_signal")
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["discarded_question_codes"], ["clarification.trigger_screened"])
        planner_request = json.loads(adapter.calls[1]["user"])
        self.assertEqual(planner_request["answers"]["motivation"], "")
        self.assertNotIn("фотосъёмка", json.dumps(planner_request["knowledge_facts"], ensure_ascii=False))

    def test_one_bad_followup_does_not_hide_other_followup_answers(self):
        data = candidate().model_copy(update={"followup_answers": {
            "ai_skills": "Случайные слова без ответа на вопрос.",
            "ai_availability": "Каждую субботу могу помогать регулярно.",
        }})
        adapter = ClarificationAdapter(plan(state="clear"), categories={
            "followup_answers.ai_skills": "nonsense",
        })
        questions_before_verdict(data, adapter=adapter)
        planner_answers = json.loads(adapter.calls[1]["user"])["answers"]["followup_answers"]
        self.assertEqual(planner_answers["ai_skills"], "")
        self.assertIn("субботу", planner_answers["ai_availability"])

    def test_question_can_cite_valid_nested_answer_but_not_rejected_one(self):
        data = candidate().model_copy(update={"followup_answers": {
            "ai_skills": "Помогал готовить историческую экскурсию для школьников."
        }})
        q = {"criterion": "motivation",
             "question": "Почему опыт экскурсии привёл вас именно в ГИМ?",
             "reason": "Опыт экскурсии указан, но выбор музейной программы не объяснён.",
             "decision_impact": "Ответ покажет связь интереса с задачами музея.",
             "source_fact_ids": ["k02"],
             "trigger_field_ids": ["followup_answers.ai_skills"]}
        accepted = questions_before_verdict(data, adapter=ClarificationAdapter(plan(q)))
        self.assertEqual(len(accepted["questions"]), 1)
        self.assertEqual(accepted["questions"][0]["trigger_field_ids"],
                         ["followup_answers.ai_skills"])
        rejected = questions_before_verdict(data, adapter=ClarificationAdapter(
            plan(q), categories={"followup_answers.ai_skills": "off_topic"}))
        self.assertEqual(rejected["questions"], [])
        self.assertEqual(rejected["discarded_question_codes"],
                         ["clarification.trigger_screened"])

    def test_redundant_known_days_and_hours_are_filtered(self):
        data = candidate().model_copy(update={
            "availability_days": ["суббота"], "availability_hours_month": 20,
        })
        q = {**question(), "question": "Какие конкретные дни и часы вы можете посвятить волонтерству?"}
        result = questions_before_verdict(data, adapter=ClarificationAdapter(plan(q)))
        self.assertEqual(result["questions"], [])

    def test_exclusive_collections_gets_organizer_question(self):
        data = candidate().model_copy(update={
            "desired_direction": "collections",
            "motivation": "Хочу работать исключительно в фондах, другие направления не рассматриваю.",
        })
        result = questions_before_verdict(data, adapter=ClarificationAdapter(
            plan(state="clear"), signals={"motivation": ["exclusive_unavailable_role"]}))
        self.assertEqual(result["questions"][0]["id"], "ai_motivation")
        self.assertEqual(result["questions"][0]["source_fact_ids"], ["k21"])

    def test_rejected_motivation_cannot_trigger_collections_priority(self):
        data = candidate().model_copy(update={
            "desired_direction": "collections",
            "motivation": "Бессмысленный набор слов без ответа на вопрос.",
        })
        result = questions_before_verdict(data, adapter=ClarificationAdapter(
            plan(state="clear"), categories={"motivation": "nonsense"},
            signals={"motivation": ["exclusive_unavailable_role"]}))
        self.assertEqual(result["questions"], [])

    def test_multiple_projects_gets_priority_question_about_conflicting_shifts(self):
        data = candidate().model_copy(update={
            "other_volunteer_programs": "Два других волонтёрских проекта: галерея и музей.",
        })
        result = questions_before_verdict(data, adapter=ClarificationAdapter(plan(state="clear")))
        self.assertEqual(result["questions"][0]["id"], "ai_availability")
        self.assertEqual(result["questions"][0]["trigger_field_ids"], ["other_volunteer_programs"])
        self.assertEqual(result["questions"][0]["source_fact_ids"], ["k07"])
        self.assertIn("приоритет", result["questions"][0]["question"])

    def test_multiple_projects_question_not_repeated_after_answer(self):
        data = candidate().model_copy(update={
            "other_volunteer_programs": "Два других волонтёрских проекта: галерея и музей.",
            "followup_answers": {"ai_availability": "При совпадении смен приоритет у ГИМ."},
        })
        result = questions_before_verdict(data, adapter=ClarificationAdapter(plan(state="clear")))
        self.assertEqual(result["questions"], [])

    def test_priority_questions_follow_configured_order(self):
        data = candidate().model_copy(update={
            "desired_direction": "collections",
            "motivation": "Хочу только в фонды, другие направления не рассматриваю.",
            "other_volunteer_programs": "Два других волонтёрских проекта: галерея и музей.",
        })
        result = questions_before_verdict(data, adapter=ClarificationAdapter(
            plan(state="clear"), signals={"motivation": ["exclusive_unavailable_role"]}
        ))
        self.assertEqual([q["id"] for q in result["questions"]],
                         ["ai_availability", "ai_motivation"])

    def test_rejected_project_text_cannot_trigger_priority_question(self):
        data = candidate().model_copy(update={
            "other_volunteer_programs": "Два других волонтёрских проекта: галерея и музей.",
            "other_commitments": "",
        })
        result = questions_before_verdict(data, adapter=ClarificationAdapter(
            plan(state="clear"), categories={"other_volunteer_programs": "off_topic"}
        ))
        self.assertEqual(result["questions"], [])

    def test_do_not_reask_answered_criterion(self):
        data = candidate().model_copy(update={"followup_answers": {
            "ai_availability": "Суббота и воскресенье, регулярно."
        }})
        with self.assertRaises(EvaluationError):
            questions_before_verdict(data, adapter=ClarificationAdapter(plan(question())))

    def test_generic_process_fact_cannot_ground_schedule_question(self):
        generic = {"id": "k20", "subject": "program", "topics": ["анкета"],
                   "source": "materials/gim/case.txt#05:51", "object": "Собеседование после анкеты."}
        q = {**question(), "source_fact_ids": ["k20"]}
        with patch("backend.clarification_agent.retrieve_facts", return_value=[generic]):
            result = questions_before_verdict(candidate(), adapter=ClarificationAdapter(plan(q)))
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["discarded_question_codes"], ["clarification.source_invalid"])

    def test_bachelor_risk_fact_cannot_ground_masters_schedule_question(self):
        data = candidate().model_copy(update={
            "course": "1 курс магистратуры",
            "other_commitments": "Вечерняя магистратура, занятия три раза в неделю.",
        })
        result = questions_before_verdict(data, adapter=ClarificationAdapter(plan(question())))
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["discarded_question_codes"], ["clarification.source_invalid"])

    def test_valid_question_survives_unrelated_second_citation(self):
        valid = question()
        unrelated = {**question("form_and_contact"),
                     "source_fact_ids": ["k07"],
                     "trigger_field_ids": ["university"]}
        result = questions_before_verdict(candidate(), adapter=ClarificationAdapter(plan(valid, unrelated)))
        self.assertEqual([q["id"] for q in result["questions"]], ["ai_availability"])
        self.assertEqual(result["discarded_question_codes"], ["clarification.source_invalid"])

    def test_question_cannot_use_unrelated_answer_field(self):
        q = {**question(), "trigger_field_ids": ["scientific_interests"]}
        result = questions_before_verdict(candidate(), adapter=ClarificationAdapter(plan(q)))
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["discarded_question_codes"], ["clarification.trigger_invalid"])

    def test_question_cannot_switch_to_unmentioned_direction(self):
        data = candidate().model_copy(update={
            "desired_direction": "organizational", "motivation": "Хочу помогать на мероприятиях музея."
        })
        q = {**question(), "decision_impact": "Ответ поможет оценить доступность для фондов ГИМ."}
        result = questions_before_verdict(data, adapter=ClarificationAdapter(plan(q)))
        self.assertEqual(result["questions"], [])
        self.assertEqual(result["discarded_question_codes"], ["clarification.direction_invalid"])

    def test_invalid_criteria_and_duplicate_questions_fail_closed(self):
        for raw in (
            json.dumps({"criteria_review": review()[:-1], "questions": []}),
            plan(question(), question()),
            plan(question(), state="clear"),
            "{not-json",
        ):
            with self.subTest(raw=raw[:30]), self.assertRaises(EvaluationError):
                questions_before_verdict(candidate(), adapter=ClarificationAdapter(raw))

    def test_demo_is_explicit_and_does_not_need_key(self):
        result = questions_before_verdict(candidate(), mode="demo")
        self.assertEqual(result["evaluation_mode"], "rules_demo")
        self.assertEqual(result["criteria_review"], [])
        self.assertIsInstance(result["questions"], list)

    def test_endpoint_uses_llm_and_does_not_fallback_silently(self):
        mock = ClarificationAdapter(plan(question()))
        with patch("backend.clarification_agent.adapter_from_env", return_value=mock), patch.dict(
                "os.environ", {"GIM_EVALUATION_MODE": "llm"}):
            result = clarification(candidate())
            self.assertIsNone(result["question"])
            self.assertEqual(result["evaluation_mode"], "llm")
            self.assertEqual(len(result["questions"]), 1)
        with patch("backend.clarification_agent.adapter_from_env", return_value=ClarificationAdapter("INVALID")), patch.dict(
                "os.environ", {"GIM_EVALUATION_MODE": "llm"}):
            with self.assertRaises(HTTPException) as ctx:
                clarification(candidate())
            self.assertEqual(ctx.exception.status_code, 503)

    def test_preliminary_llm_cannot_receive_data_without_consent(self):
        with patch.dict("os.environ", {"GIM_EVALUATION_MODE": "llm"}):
            with self.assertRaises(HTTPException) as ctx:
                clarification(candidate().model_copy(update={"consent_personal_data": False}))
            self.assertEqual(ctx.exception.status_code, 422)

    def test_schedule_response_mitigates_risk_without_claiming_acceptance(self):
        from backend.availability_policy import availability_risks
        constrained = candidate().model_copy(update={
            "course": "1-й курс бакалавриата",
            "availability_hours_month": 10,
            "followup_answers": {
                "ai_availability": "Каждую субботу могу участвовать стабильно."
            },
        })
        risks = availability_risks(constrained)
        self.assertTrue(any("Первый курс" in value for value in risks))
        self.assertTrue(any("Смягчающее" in value for value in risks))
        self.assertFalse(any("отклон" in value for value in risks))

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
