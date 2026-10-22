"""AI diagnostic clarification before final museum curator recommendation.

The model inspects all five source criteria, identifies consequential unknowns
and asks up to two questions *of the candidate*, before submission and scoring.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app import ApplicationInput, clarifications_for
from backend.evaluator import EvaluationError, adapter_from_env, _unique_object
from backend.knowledge_graph import GRAPH_PATH, query_from_field_reviews, retrieve_facts
from backend.field_calibration import SCORING_POLICY_PATH
from backend.field_quality import review_fields, screening_outcome
from backend.field_review_cache import runtime_cache_for
from backend.availability_policy import availability_signal_ids, valid_availability_days


Criterion = Literal["motivation", "skills", "availability", "form_and_contact", "logistics"]


class CriterionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    criterion: Criterion
    state: Literal["clear", "uncertain", "risk"]
    reason: str = Field(min_length=10, max_length=700)


class FollowupQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    criterion: Criterion
    question: str = Field(min_length=12, max_length=350)
    reason: str = Field(min_length=10, max_length=700)
    decision_impact: str = Field(min_length=10, max_length=700)
    source_fact_ids: list[str] = Field(min_length=1, max_length=3)
    trigger_field_ids: list[str] = Field(min_length=1, max_length=4)


class QuestionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    criteria_review: list[CriterionReview] = Field(min_length=5, max_length=5)
    questions: list[FollowupQuestion] = Field(max_length=2)


PROMPT = (Path(__file__).resolve().parent.parent / "knowledge/clarification_prompt.ru.txt").read_text(encoding="utf-8")
VALIDATION_RULES = json.loads((Path(__file__).resolve().parent.parent / "knowledge/clarification_validation.json").read_text(encoding="utf-8"))


SCORING_POLICY = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
CRITERIA = tuple(SCORING_POLICY["criterion_order"])
LABELS = SCORING_POLICY["criterion_labels"]


def questions_before_verdict(
    data: ApplicationInput,
    *,
    mode: str | None = None,
    adapter=None,
) -> dict:
    """Safe preview: no SQLite submission and no decision/score is persisted."""
    mode = mode or ("llm" if adapter is not None else os.getenv("GIM_EVALUATION_MODE", "demo"))
    if mode == "demo":
        return {
            "evaluation_mode": "rules_demo",
            "criteria_review": [],
            "questions": clarifications_for(data),
        }
    if mode != "llm":
        raise EvaluationError("invalid_mode")
    adapter = adapter or adapter_from_env()
    # Classify the complete non-private questionnaire before graph retrieval
    # or question generation. The final evaluation repeats classification on
    # the final answers, including any new follow-up answers.
    answers = data.model_dump(exclude={"applicant_name", "application_id", "email", "vk_url", "birth_date", "followup_signature"})
    prior_questions = {
        "followup_answers." + item["id"]: item["question"]
        for item in data.followup_questions if item.get("id") and item.get("question")
    }
    reviews = review_fields(answers, adapter, prior_questions, cache=runtime_cache_for(adapter))
    outcome = screening_outcome(reviews)
    if outcome == "screen_out_for_curator_review":
        return {"evaluation_mode": "llm", "screening_outcome": outcome,
                "criteria_review": [], "questions": [], "discarded_question_codes": []}
    rejected_categories = set(VALIDATION_RULES["rejected_answer_categories"])
    by_root: dict[str, list[dict]] = {}
    for review in reviews:
        by_root.setdefault(review["field_id"].split(".", 1)[0], []).append(review)
    rejected_fields = {root for root, group in by_root.items()
                       if all(review["category"] in rejected_categories for review in group)}
    rejected_review_ids = {review["field_id"] for review in reviews
                           if review["category"] in rejected_categories}
    reviewed_field_ids = {review["field_id"] for review in reviews}
    planner_answers = json.loads(json.dumps(answers, ensure_ascii=False))
    for review in reviews:
        if review["category"] not in rejected_categories:
            continue
        root, *path = review["field_id"].split(".")
        if not path:
            planner_answers[root] = ""
        elif isinstance(planner_answers.get(root), dict):
            planner_answers[root][path[0]] = ""
        elif (isinstance(planner_answers.get(root), list) and path[0].isdigit()
              and int(path[0]) < len(planner_answers[root])):
            planner_answers[root][int(path[0])] = ""
    workload_signals = availability_signal_ids(data)
    priority_questions: list[tuple[str, FollowupQuestion]] = []
    for name, configured in VALIDATION_RULES["priority_followups"].items():
        selection = configured["selection"]
        criterion = configured["criterion"]
        if (str(data.followup_answers.get("ai_" + criterion, "")).strip()
                or selection.get("direction", data.desired_direction) != data.desired_direction
                or selection.get("availability_signal") not in (None, *workload_signals)):
            continue
        trigger = next((field_id for field_id in selection["trigger_field_candidates"]
                        if str(getattr(data, field_id, "") or "").strip()
                        and any(review["field_id"] == field_id
                                and review["category"] not in selection.get("exclude_categories", [])
                                and (not selection.get("include_categories")
                                     or review["category"] in selection["include_categories"])
                                and (not selection.get("review_signal")
                                     or selection["review_signal"] in review.get("signals", []))
                                for review in reviews)), None)
        if trigger is None:
            continue
        question = FollowupQuestion.model_validate({
            key: value for key, value in configured.items() if key != "selection"
        })
        question.trigger_field_ids = [trigger]
        priority_questions.append((name, question))
    priority_questions.sort(key=lambda pair: VALIDATION_RULES["priority_followups"][pair[0]]["selection"]["priority"])
    schema = QuestionPlan.model_json_schema()
    query = query_from_field_reviews(answers, reviews)
    facts = retrieve_facts(query, subject=data.desired_direction, limit=12,
                           signal_ids=workload_signals,
                           field_categories={review["field_id"]: review["category"]
                                             for review in reviews})
    direction_markers = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))["direction_markers"]
    answer_text = " ".join(str(value) for value in planner_answers.values()).casefold()
    criterion_fields = SCORING_POLICY["criterion_evidence_fields"]
    request = json.dumps({"answers": planner_answers,
                          "field_reviews": [{"field_id": item["field_id"],
                                             "category": item["category"],
                                             "signals": item["signals"]} for item in reviews],
                          "screening_outcome": outcome, "knowledge_facts": facts,
                          "priority_signals": [name for name, _ in priority_questions],
                          "output_schema": schema}, ensure_ascii=False)
    def parse(raw: str) -> dict:
        plan = QuestionPlan.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        labels = [r.criterion for r in plan.criteria_review]
        if len(labels) != len(CRITERIA) or set(labels) != set(CRITERIA):
            raise EvaluationError("invalid_model_json", ("clarification.criteria_invalid",))
        review = {r.criterion: r for r in plan.criteria_review}
        prior = data.followup_answers
        result = []
        discarded: list[str] = []
        seen = set()
        fact_index = {fact["id"]: fact for fact in facts}
        for q in plan.questions:
            question_lower = q.question.casefold()
            redundant = VALIDATION_RULES["redundant_known_fact_questions"]
            if (q.criterion == "availability" and valid_availability_days(data.availability_days)
                    and data.availability_hours_month > 0
                    and re.search(redundant["availability_days_and_hours"], question_lower)):
                continue
            if (q.criterion == "skills" and data.experience.strip()
                    and re.search(redundant["experience_general"], question_lower)):
                continue
            if re.search(VALIDATION_RULES["private_location_question"], question_lower):
                continue
            if (q.criterion in seen or review[q.criterion].state == "clear"
                    or str(prior.get("ai_" + q.criterion, "")).strip()):
                raise EvaluationError("invalid_model_json", ("clarification.question_invalid",))
            if any(
                fact_id not in fact_index or fact_index[fact_id]["subject"] not in
                {q.criterion, data.desired_direction}
                for fact_id in q.source_fact_ids
            ):
                discarded.append("clarification.source_invalid")
                continue
            if any(
                field_id.split(".", 1)[0] not in criterion_fields[q.criterion]
                or (field_id not in answers and field_id not in reviewed_field_ids)
                for field_id in q.trigger_field_ids
            ):
                discarded.append("clarification.trigger_invalid")
                continue
            if any(field_id in rejected_review_ids or
                   field_id.split(".", 1)[0] in rejected_fields
                   for field_id in q.trigger_field_ids):
                discarded.append("clarification.trigger_screened")
                continue
            question_context = " ".join((q.question, q.reason, q.decision_impact)).casefold()
            if any(
                direction != data.desired_direction
                and re.search(pattern, question_context)
                and not re.search(pattern, answer_text)
                for direction, pattern in direction_markers.items()
            ):
                discarded.append("clarification.direction_invalid")
                continue
            seen.add(q.criterion)
            result.append({
                "id": "ai_" + q.criterion,
                "category": LABELS[q.criterion],
                "question": q.question,
                "reason": q.reason,
                "decision_impact": q.decision_impact,
                "source_fact_ids": q.source_fact_ids,
                "trigger_field_ids": q.trigger_field_ids,
            })
        for _, priority_question in reversed(priority_questions):
            if not all(fact_id in fact_index for fact_id in priority_question.source_fact_ids):
                continue
            criterion = priority_question.criterion
            priority = {"id": "ai_" + criterion, "category": LABELS[criterion],
                        **priority_question.model_dump(exclude={"criterion"})}
            result = [priority, *(item for item in result if item["id"] != priority["id"])][:2]
        return {
            "evaluation_mode": "llm",
            "screening_outcome": outcome,
            "criteria_review": [r.model_dump() for r in plan.criteria_review],
            "questions": result,
            "discarded_question_codes": discarded,
        }

    try:
        system = PROMPT
        if hasattr(adapter, "complete_validated"):
            return adapter.complete_validated(system=system, user=request, schema=schema, validator=parse)
        return parse(adapter.complete(system=system, user=request, schema=schema))
    except EvaluationError:
        raise
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError, KeyError):
        raise EvaluationError("invalid_model_json", ("clarification.invalid_response",)) from None
