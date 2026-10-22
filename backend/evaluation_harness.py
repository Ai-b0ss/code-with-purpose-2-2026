"""LangGraph evaluation stages: classify, retrieve, score, apply policy."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app import ApplicationInput, DIRECTIONS
from backend.evaluator import Evaluation, EvaluationError, LLMAdapter, _unique_object, validate_response
from backend.field_quality import review_fields, screening_outcome
from backend.field_review_cache import runtime_cache_for
from backend.field_calibration import SCORING_POLICY_PATH, median_adjustment
from backend.knowledge_graph import query_from_field_reviews, retrieve_facts
from backend.availability_policy import availability_risks, availability_signal_ids
from backend.deterministic_rubric import build_deterministic_rubric
from backend.narrative_guard import apply_narrative_guard


HARNESS_PROMPT = (Path(__file__).resolve().parent.parent / "knowledge" / "evaluation_harness_prompt.ru.txt").read_text(encoding="utf-8")


class EvaluationState(TypedDict, total=False):
    answers: dict
    field_reviews: list[dict]
    knowledge_facts: list[dict]
    outcome: str
    evaluation: dict


def evaluate_with_harness(data: ApplicationInput, adapter: LLMAdapter) -> dict:
    """Run the same explicit stages for every live application and preview."""
    answers = data.model_dump(exclude={"applicant_name", "application_id", "email", "vk_url", "birth_date", "followup_signature"})
    answers["email_provided"] = bool(data.email.strip())
    answers["vk_provided"] = bool(data.vk_url.strip())

    def classify(state: EvaluationState) -> dict:
        followup_questions = {
            "followup_answers." + item["id"]: item["question"]
            for item in data.followup_questions
            if item.get("id") and item.get("question")
        }
        try:
            reviews = review_fields(state["answers"], adapter, followup_questions,
                                    cache=runtime_cache_for(adapter))
        except EvaluationError as exc:
            raise EvaluationError(exc.code, ("stage.classify", *exc.details)) from None
        return {"field_reviews": reviews, "outcome": screening_outcome(reviews)}

    def retrieve(state: EvaluationState) -> dict:
        query = query_from_field_reviews(state["answers"], state["field_reviews"])
        facts = retrieve_facts(query, subject=data.desired_direction, limit=8,
                               signal_ids=availability_signal_ids(data),
                               field_categories={review["field_id"]: review["category"]
                                                 for review in state["field_reviews"]})
        return {"knowledge_facts": facts}

    def score(state: EvaluationState) -> dict:
        schema = Evaluation.model_json_schema()
        required = schema.setdefault("required", [])
        if "knowledge_fact_ids" not in required:
            required.append("knowledge_fact_ids")
        request = json.dumps({
            "answers": state["answers"],
            "field_reviews": state["field_reviews"],
            "knowledge_facts": state["knowledge_facts"],
            "allowed_evidence": [
                {"question_id": review["field_id"], "reason": review["evidence"]}
                for review in state["field_reviews"]
                if review["evidence"] and review["field_id"] in state["answers"]
            ],
            "output_schema": schema,
        }, ensure_ascii=False)
        available = {fact["id"] for fact in state["knowledge_facts"]}
        criterion_fields = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))["criterion_evidence_fields"]
        def parse(raw: str) -> dict:
            # The classifier has already checked these spans against the
            # source fields. Canonicalize model citations to those spans so a
            # paraphrase cannot become evidence or break an otherwise valid
            # scorecard. Unknown field IDs still fail normal validation.
            citations = {
                review["field_id"]: review["evidence"]
                for review in state["field_reviews"]
                if review["evidence"] and review["field_id"] in state["answers"]
            }
            try:
                decoded = json.loads(raw, object_pairs_hook=_unique_object)
            except (TypeError, ValueError):
                decoded = None
            corrected = 0
            if isinstance(decoded, dict) and isinstance(decoded.get("evidence"), list):
                for item in decoded["evidence"]:
                    if (isinstance(item, dict) and item.get("question_id") in citations
                            and item.get("reason") != citations[item["question_id"]]):
                        item["reason"] = citations[item["question_id"]]
                        corrected += 1
                # A null criterion has no measured level, so references to
                # missing fields are not evidence for a numeric rating.
                for item in decoded.get("scorecard", []):
                    if not isinstance(item, dict):
                        continue
                    if item.get("level") is None:
                        item["evidence_question_ids"] = []
                    elif isinstance(item.get("evidence_question_ids"), list):
                        cited_ids = {
                            evidence.get("question_id") for evidence in decoded["evidence"]
                            if isinstance(evidence, dict)
                        }
                        item["evidence_question_ids"] = [
                            field_id for field_id in item["evidence_question_ids"]
                            if field_id in cited_ids and field_id in criterion_fields.get(item.get("criterion"), [])
                        ]
                        if not item["evidence_question_ids"]:
                            item["level"] = None
                            item["rationale"] = "Нет подтверждённых ответом оснований для числовой оценки критерия."
                    if (item.get("criterion") == "availability"
                            and isinstance(item.get("level"), int)
                            and item["level"] < 2 and not availability_risks(data)):
                        item["level"] = None
                        item["evidence_question_ids"] = []
                        item["rationale"] = "Негативный вывод о доступности не подтверждён заявленным графиком."
                    if (item.get("criterion") == "form_and_contact"
                            and isinstance(item.get("level"), int)
                            and item["level"] < 2 and not (data.email.strip() or data.vk_url.strip())):
                        item["level"] = None
                        item["evidence_question_ids"] = []
                        item["rationale"] = "Отсутствие переданного контакта само по себе не является отрицательной оценкой анкеты."
                raw = json.dumps(decoded, ensure_ascii=False)
            result = validate_response(raw, state["answers"], allow_legacy=True)
            result["evidence_canonicalized_count"] = corrected
            cited = result["knowledge_fact_ids"]
            if available and (not cited or any(fact_id not in available for fact_id in cited)):
                raise EvaluationError("invalid_model_json", ("knowledge_fact_ids.invalid",))
            deterministic = build_deterministic_rubric(data, state["field_reviews"], state["answers"])
            result["score_model_raw"] = result.get("score_raw")
            result["score_model_status"] = result.get("score_status")
            result.update(deterministic)
            return result
        system = HARNESS_PROMPT
        try:
            if hasattr(adapter, "complete_validated"):
                result = adapter.complete_validated(system=system, user=request, schema=schema, validator=parse)
            else:
                result = parse(adapter.complete(system=system, user=request, schema=schema))
        except EvaluationError as exc:
            raise EvaluationError(exc.code, ("stage.score", *exc.details)) from None
        return {"evaluation": result}

    def screened(state: EvaluationState) -> dict:
        abusive = next(r for r in state["field_reviews"] if r["category"] == "abusive")
        template = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))["screened_response"]
        return {"evaluation": {
            **template,
            "score": None,
            "evaluation_mode": "llm",
            "recommended_direction_label": DIRECTIONS[template["recommended_direction"]],
            "evidence": [{"question_id": abusive["field_id"], "reason": abusive["evidence"]}],
        }}

    def apply_policy(state: EvaluationState) -> dict:
        result = dict(state["evaluation"])
        result["field_reviews"] = state["field_reviews"]
        median_effect = median_adjustment(state["field_reviews"])
        result["field_median_deviations"] = median_effect["fields"]
        result["field_median_status"] = median_effect["status"]
        result["field_median_delta"] = median_effect["delta"]
        result.setdefault("knowledge_fact_ids", [])
        cited = set(result["knowledge_fact_ids"])
        result["knowledge_facts_used"] = [
            fact for fact in state.get("knowledge_facts", []) if fact["id"] in cited
        ]
        result["screening_outcome"] = state["outcome"]
        if getattr(adapter, "last_model", None):
            result["provider_model"] = adapter.last_model
            result["provider_models_attempted"] = list(adapter.attempted_models)
            result["provider_failures"] = list(getattr(adapter, "failed_attempts", []))
        policy = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
        contributions = []
        for review in state["field_reviews"]:
            for signal in dict.fromkeys([review["category"], *review.get("signals", [])]):
                allowed_fields = policy.get("content_signal_fields", {}).get(signal)
                if allowed_fields is not None and review["field_id"] not in allowed_fields:
                    continue
                if (review.get("quality_level") is None
                        and signal not in policy["content_signals_without_quality"]):
                    continue
                delta = policy["content_deltas"].get(signal)
                if delta is not None:
                    contributions.append({
                        "field_id": review["field_id"], "signal": signal, "delta": delta,
                    })
        applicable_signals = {item["signal"] for item in contributions}
        content_delta = sum(item["delta"] for item in contributions)
        delta_limit = policy["max_abs_content_delta"]
        content_delta = max(-delta_limit, min(delta_limit, content_delta))
        result["content_delta"] = round(content_delta, 3)
        result["content_signal_contributions"] = contributions
        if isinstance(result.get("score"), int):
            original = result["score"]
            base = float(result.get("score_raw") if result.get("score_raw") is not None else original)
            detailed = max(0.0, min(3.0, base + median_effect["delta"] + content_delta))
            result["score_detailed"] = round(detailed, 3)
            result["score"] = min(3, int(math.floor(detailed + 0.5)))
            if median_effect["status"] == "pilot_adjustment":
                result["score_before_median"] = original
            if content_delta:
                result["score_before_content_rule"] = original
            if median_effect["status"] == "pilot_adjustment" or content_delta:
                result["score_status"] = "pilot_hybrid_unvalidated"
        for signal, cap in policy["content_score_caps"].items():
            if signal not in applicable_signals:
                continue
            explanation = policy["content_commentary"][signal]
            if explanation not in result.get("possible_problems", []):
                result["possible_problems"] = [*result.get("possible_problems", []), explanation]
            if isinstance(result.get("score"), int) and result["score"] > cap:
                result.setdefault("score_before_content_rule", result["score"])
                result["score"] = cap
                result["score_detailed"] = min(result.get("score_detailed", float(cap)), float(cap))
                result["score_status"] = "pilot_hybrid_unvalidated"
        if applicable_signals.intersection(policy["manual_review_signals"]):
            result["preliminary_verdict"] = "manual_review"
        return {"evaluation": apply_narrative_guard(result, data)}

    graph = StateGraph(EvaluationState)
    graph.add_node("classify", classify)
    graph.add_node("retrieve", retrieve)
    graph.add_node("score", score)
    graph.add_node("screened", screened)
    graph.add_node("policy", apply_policy)
    graph.add_edge(START, "classify")
    graph.add_conditional_edges("classify", lambda state: "screened" if state["outcome"] == "screen_out_for_curator_review" else "retrieve")
    graph.add_edge("retrieve", "score")
    graph.add_edge("score", "policy")
    graph.add_edge("screened", "policy")
    graph.add_edge("policy", END)
    result = graph.compile().invoke({"answers": answers})["evaluation"]
    return result
