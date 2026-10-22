"""Reproducible pilot scorecard from verified field categories and workload facts.

Historical cohort outcomes have not validated these levels.  The model still
writes curator-facing prose, but repeated field labels yield the same pilot
numeric components regardless of the model's free-form scorecard.
"""
from __future__ import annotations

import json
import math

from backend.app import ApplicationInput
from backend.availability_policy import availability_signal_ids, valid_availability_days
from backend.field_calibration import SCORING_POLICY_PATH


def build_deterministic_rubric(data: ApplicationInput, reviews: list[dict], answers: dict) -> dict:
    config = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
    policy = config["deterministic_rubric"]
    by_id = {review["field_id"]: review for review in reviews}
    ratings: dict[str, dict] = {}
    evidence: dict[str, dict] = {}

    def source_for(field_id: str):
        root, *path = field_id.split(".")
        source = answers.get(root)
        for key in path:
            if isinstance(source, dict):
                source = source.get(key)
            elif isinstance(source, list) and key.isdecimal() and int(key) < len(source):
                source = source[int(key)]
            else:
                return None
        return source

    def cite(field_id: str) -> bool:
        if field_id in evidence:
            return True
        source = source_for(field_id)
        review = by_id.get(field_id)
        if review and review.get("evidence") and source is not None:
            quote = review["evidence"]
            if isinstance(source, str):
                grounded = isinstance(quote, str) and quote in source
            else:
                grounded = quote == json.dumps(source, ensure_ascii=False)
            if not grounded:
                return False
            evidence[field_id] = {"question_id": field_id, "reason": quote}
            return True
        if field_id == "availability_days" and isinstance(source, list) and source:
            evidence[field_id] = {"question_id": field_id, "reason": json.dumps(source, ensure_ascii=False)}
            return True
        return False

    for criterion, group in policy["quality_groups"].items():
        supported = [
            (field_id, by_id[field_id]["quality_level"])
            for field_id in group["fields"]
            if field_id in by_id and by_id[field_id].get("quality_level") is not None
            and cite(field_id)
        ]
        if supported:
            values = [value for _, value in supported]
            aggregate = sum(values) / len(values) if group["aggregate"] == "mean" else max(values)
            level = min(3, int(math.floor(aggregate + 0.5)))
            ids = [field_id for field_id, _ in supported]
        else:
            level, ids = None, []
        ratings[criterion] = {
            "criterion": criterion, "level": level,
            "rationale": policy["rationales"][criterion] if level is not None else policy["rationales"]["unknown"],
            "evidence_question_ids": ids,
        }

    hours = data.availability_hours_month
    named_days = valid_availability_days(data.availability_days)
    signals = availability_signal_ids(data)
    levels = policy["availability_levels"]
    availability = levels["zero_hours"] if hours == 0 else (
        levels["specific_days"] if named_days else levels["positive_hours"]
    )
    explicit_schedule_fields = [
        field_id for field_id in policy["explicit_schedule_field_ids"]
        if field_id in by_id
        and by_id[field_id]["category"] in policy["explicit_schedule_categories"]
        and policy["explicit_schedule_signal"] in by_id[field_id].get("signals", [])
        and not set(by_id[field_id].get("signals", [])).intersection(
            policy["explicit_schedule_blocking_signals"])
        and cite(field_id)
    ]
    for signal in signals:
        caps = policy["availability_signal_caps"].get(signal)
        if caps:
            mode = ("explicit" if explicit_schedule_fields and "explicit" in caps else
                    "mitigated" if "mitigation" in signals else "base")
            availability = min(availability, caps[mode])
    availability_ids = ["availability_hours_month"] if cite("availability_hours_month") else []
    if named_days and cite("availability_days"):
        availability_ids.append("availability_days")
    availability_ids.extend(explicit_schedule_fields)
    ratings["availability"] = {
        "criterion": "availability", "level": availability if availability_ids else None,
        "rationale": (policy["rationales"]["availability_explicit"]
                      if explicit_schedule_fields else policy["rationales"]["availability"])
        if availability_ids else policy["rationales"]["unknown"],
        "evidence_question_ids": availability_ids,
    }

    meaningful = [
        field_id for field_id in policy["meaningful_form_fields"]
        if field_id in by_id and by_id[field_id]["category"] in policy["meaningful_form_categories"]
        and cite(field_id)
    ]
    form_level = None
    if meaningful:
        form_level = (policy["form_levels"]["answered_with_contact"]
                      if data.email.strip() or data.vk_url.strip()
                      else policy["form_levels"]["answered"])
    ratings["form_and_contact"] = {
        "criterion": "form_and_contact", "level": form_level,
        "rationale": policy["rationales"]["form_and_contact"] if form_level is not None else policy["rationales"]["unknown"],
        "evidence_question_ids": meaningful[:3],
    }

    # Days describe workload availability, not travel to the museum.  The
    # current form contains no structured travel evidence.
    logistics_ids: list[str] = []
    ratings["logistics"] = {
        "criterion": "logistics",
        "level": None,
        "rationale": policy["rationales"]["logistics"],
        "evidence_question_ids": logistics_ids,
    }

    scorecard = [ratings[criterion] for criterion in config["criterion_order"]]
    known = {item["criterion"]: item["level"] for item in scorecard if item["level"] is not None}
    if (len(known) >= policy["minimum_rated_criteria"]
            and set(policy["required_scoring_criteria"]).issubset(known)):
        weights = policy["criterion_weights"]
        raw = sum(level * weights[criterion] for criterion, level in known.items()) / sum(
            weights[criterion] for criterion in known
        )
        score = min(3, int(math.floor(raw + 0.5)))
        score_raw = round(raw, 2)
        status = "pilot_deterministic_unvalidated"
    else:
        score, score_raw, status = None, None, "insufficient_evidence"
    return {
        "scorecard": scorecard, "evidence": list(evidence.values()),
        "score": score, "score_raw": score_raw, "score_status": status,
        "score_rubric": policy["version"], "rated_criteria_count": len(known),
        "availability_signal_ids": signals,
    }
