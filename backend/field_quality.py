"""Validated, field-level semantic screening before any LLM scorecard."""
from __future__ import annotations

import json
from difflib import SequenceMatcher
import logging
from pathlib import Path
import re
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.evaluator import EvaluationError, LLMAdapter, _unique_object


TAXONOMY = json.loads(Path(__file__).with_name("field_taxonomy.json").read_text(encoding="utf-8"))
PROMPT_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "field_quality_prompt.ru.txt"
PRIVATE_FIELDS = {"applicant_name", "application_id", "email", "vk_url", "birth_date", "followup_signature"}
STRUCTURED_FIELDS = {"age_over_18", "consent_personal_data", "availability_hours_month", "desired_direction"}
METADATA_FIELDS = {"followup_questions"}
Category = Literal[
    "specific_relevant", "relevant_general", "explicit_none", "empty", "unclear", "off_topic",
    "nonsense", "abusive", "instruction_attempt",
]
Signal = Literal[
    "concrete_example", "generic_claim", "ornate_without_substance",
    "museum_mismatch", "exclusive_unavailable_role", "unrealistic_expectation",
    "schedule_commitment", "long_term_schedule_commitment",
    "schedule_conflict", "self_contradiction",
]


class FieldReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field_id: str
    category: Category
    signals: list[Signal] = Field(max_length=5)
    evidence: str = Field(max_length=300)


class FieldReviews(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fields: list[FieldReview]


def quality_level_for(field_id: str, category: str) -> int | None:
    """Keep school, course, contact and provenance out of numeric quality."""
    eligible = (
        field_id in TAXONOMY["quality_field_ids"]
        or any(field_id.startswith(prefix) for prefix in TAXONOMY["quality_field_prefixes"])
    )
    return TAXONOMY["pilot_field_quality_levels"][category] if eligible else None


def exact_evidence_span(answer: str, proposed: str) -> str | None:
    """Anchor a near-identical model quote to source bytes, never to a paraphrase."""
    limits = TAXONOMY["evidence_validation"]
    protected = set(limits["protected_words"])
    def preceding_negation(start: int) -> bool:
        context = re.split(r"[.!?;,:\n]", answer[max(0, start - 24):start])[-1]
        words = re.findall(r"[\wёЁ]+", context.casefold())
        return bool(words and words[-1] in protected)
    if proposed:
        start = answer.find(proposed)
        while start >= 0:
            if not preceding_negation(start):
                return proposed
            start = answer.find(proposed, start + 1)
    if len(proposed) < limits["minimum_approximate_length"]:
        return None
    needle = proposed.casefold()
    haystack = answer.casefold()
    proposed_words = re.findall(r"[\wёЁ]+", needle)
    match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match()
    expected_start = max(0, match.b - match.a)
    best: tuple[float, str] = (0.0, "")
    for shift in range(-8, 9):
        start = max(0, expected_start + shift)
        for length_delta in (-8, -2, 0, 2, 8):
            length = len(proposed) + length_delta
            if length <= 0 or length > 300:
                continue
            candidate = answer[start:start + length]
            if not candidate:
                continue
            candidate_words = re.findall(r"[\wёЁ]+", candidate.casefold())
            if not candidate_words or candidate_words != proposed_words:
                continue
            end = start + len(candidate)
            if ((start > 0 and answer[start - 1].isalnum() and candidate[0].isalnum())
                    or (end < len(answer) and answer[end].isalnum() and candidate[-1].isalnum())):
                continue
            if preceding_negation(start):
                continue
            similarity = SequenceMatcher(None, needle, candidate.casefold(), autojunk=False).ratio()
            if similarity > best[0]:
                best = similarity, candidate
    return best[1] if best[0] >= limits["minimum_similarity"] else None


def review_fields(answers: dict, adapter: LLMAdapter,
                  field_questions: dict[str, str] | None = None,
                  cache=None) -> list[dict]:
    """Classify every supplied answer field; verify exact source spans.

    Identity/contact fields remain local and are never sent for classification.
    Structured values are labeled deterministically. Free text is model reviewed.
    """
    text_answers: dict[str, str] = {}
    structured = []
    for key, value in answers.items():
        if key in PRIVATE_FIELDS or key in METADATA_FIELDS:
            continue
        if key in STRUCTURED_FIELDS or isinstance(value, (int, float, bool)) or value is None:
            structured.append({"field_id": key, "category": "structured_value", "signals": [],
                               "evidence": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
                               "quality_level": None})
        elif isinstance(value, str):
            text_answers[key] = value
        elif isinstance(value, dict):
            if not value:
                structured.append({"field_id": key, "category": "structured_value", "signals": [],
                                   "evidence": "{}", "quality_level": None})
            for subkey, item in value.items():
                if isinstance(item, str):
                    text_answers[f"{key}.{subkey}"] = item
        elif isinstance(value, list):
            if not value:
                structured.append({"field_id": key, "category": "structured_value", "signals": [],
                                   "evidence": "[]", "quality_level": None})
            for index, item in enumerate(value):
                if isinstance(item, str):
                    text_answers[f"{key}.{index}"] = item
    # An explicit empty field needs no model inference and cannot be mistaken
    # for a negative trait. Only nonempty text incurs a provider call.
    nonempty = {key: value for key, value in text_answers.items() if str(value).strip(" -—.\t\n")}
    empty = [
        {"field_id": key, "category": "empty", "signals": [], "evidence": "", "quality_level": None}
        for key in text_answers if key not in nonempty
    ]
    field_questions = field_questions or {}
    questions = {}
    for key in nonempty:
        question = (field_questions.get(key)
                    or TAXONOMY["field_questions"].get(key)
                    or TAXONOMY["field_questions"].get(key.split(".", 1)[0]))
        if not isinstance(question, str) or not question.strip():
            raise EvaluationError("unmapped_field_question", (key,))
        questions[key] = question.strip()[:350]
    cached = []
    if cache is not None:
        pending = {}
        for key, value in nonempty.items():
            try:
                hit = cache.get(key, value, questions[key])
            except sqlite3.Error:
                logging.getLogger(__name__).warning("field_review_cache_read_failed")
                hit = None
            if hit is None:
                pending[key] = value
            else:
                cached.append({"field_id": key, **hit,
                               "quality_level": quality_level_for(key, hit["category"])})
        nonempty = pending
        questions = {key: questions[key] for key in nonempty}
    if not nonempty:
        return [*empty, *structured, *cached]
    prompt = PROMPT_PATH.read_text(encoding="utf-8").strip().replace(
        "{{TAXONOMY_JSON}}", json.dumps(TAXONOMY, ensure_ascii=False)
    )
    schema = FieldReviews.model_json_schema()
    schema["$defs"]["FieldReview"]["properties"]["evidence"].pop("maxLength", None)
    request = json.dumps({"answers": nonempty, "questions": questions,
                          "output_schema": schema}, ensure_ascii=False)
    def normalize_review_item(item: dict) -> None:
        category = item.get("category")
        category_fallback = TAXONOMY["signal_category_fallback"].get(category)
        if category_fallback and isinstance(item.get("signals"), list):
            item["category"] = category_fallback
            item["signals"] = [*item.get("signals", []), category]
        if isinstance(item.get("signals"), list):
            # Some compatible models duplicate the primary category in
            # signals. This carries no new claim.
            item["signals"] = list(dict.fromkeys(
                s for s in item["signals"] if s != item.get("category")
            ))
        evidence = item.get("evidence")
        if isinstance(evidence, str) and len(evidence) > 300:
            # A long exact span is still source evidence. Bound its length
            # before schema validation; checked() verifies the shortened
            # span remains present in the same answer.
            item["evidence"] = evidence[:300]

    def normalized_object(raw: str) -> dict:
        obj = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(obj, dict) or not isinstance(obj.get("fields"), list):
            raise EvaluationError("invalid_model_json", ("field_quality.invalid_shape",))
        for item in obj["fields"]:
            if isinstance(item, dict):
                normalize_review_item(item)
        return obj

    def checked(review: FieldReview, key: str, value: str) -> dict:
        quote = exact_evidence_span(value, review.evidence)
        if review.field_id != key or not quote:
            raise EvaluationError("invalid_model_json", ("field_quality.evidence",))
        return {**review.model_dump(), "evidence": quote,
                "quality_level": quality_level_for(key, review.category)}

    def parse_batch(raw: str) -> list[dict]:
        parsed = FieldReviews.model_validate(normalized_object(raw))
        ids = [r.field_id for r in parsed.fields]
        if len(ids) != len(set(ids)) or any(field_id not in nonempty for field_id in ids):
            raise EvaluationError("invalid_model_json", ("field_quality.field_set",))
        return [
            *(checked(r, r.field_id, nonempty[r.field_id]) for r in parsed.fields),
        ]

    def classify_one(key: str, value: str) -> dict:
        single_schema = FieldReview.model_json_schema()
        single_schema["properties"]["evidence"].pop("maxLength", None)
        single_prompt = prompt + " Верни ОДИН JSON-объект FieldReview для единственного поля из запроса."
        single_request = json.dumps({"field_id": key, "question": questions[key],
                                     "answer": value, "output_schema": single_schema}, ensure_ascii=False)
        def parse_one(raw: str) -> dict:
            obj = json.loads(raw, object_pairs_hook=_unique_object)
            if isinstance(obj, dict):
                normalize_review_item(obj)
            review = FieldReview.model_validate(obj)
            return checked(review, key, value)
        if hasattr(adapter, "complete_validated"):
            return adapter.complete_validated(
                system=single_prompt, user=single_request,
                schema=single_schema, validator=parse_one,
                validate_raw_schema=False,
            )
        return parse_one(adapter.complete(system=single_prompt, user=single_request, schema=single_schema))

    batch = []
    try:
        if hasattr(adapter, "complete_validated"):
            batch = adapter.complete_validated(
                system=prompt, user=request, schema=schema,
                validator=parse_batch, validate_raw_schema=False,
            )
        else:
            batch = parse_batch(adapter.complete(system=prompt, user=request, schema=schema))
    except EvaluationError as exc:
        if exc.code != "invalid_model_json":
            raise
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        pass
    present = {review["field_id"] for review in batch}
    try:
        fresh = [*batch, *(classify_one(key, value) for key, value in nonempty.items()
                           if key not in present)]
        if cache is not None:
            for review in fresh:
                key = review["field_id"]
                try:
                    canonical = cache.put(key, nonempty[key], questions[key], review)
                except sqlite3.Error:
                    logging.getLogger(__name__).warning("field_review_cache_write_failed")
                    continue
                review.update(canonical)
                review["quality_level"] = quality_level_for(key, review["category"])
        return [
            *empty, *structured, *cached, *fresh,
        ]
    except EvaluationError:
        raise
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError):
        raise EvaluationError("invalid_model_json", ("field_quality.invalid_response",)) from None


def screening_outcome(reviews: list[dict]) -> str:
    if any(review["category"] == "abusive" for review in reviews):
        return "screen_out_for_curator_review"
    if any(review["category"] == "nonsense" for review in reviews):
        return "strong_negative_signal"
    return "continue"
