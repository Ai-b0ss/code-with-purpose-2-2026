"""Keep unsupported model prose out of curator-facing conclusions."""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.app import ApplicationInput, DIRECTIONS
from backend.availability_policy import availability_risks, valid_availability_days
from backend.field_calibration import SCORING_POLICY_PATH
from backend.knowledge_graph import GRAPH_PATH


RULES_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "narrative_rules.json"


def apply_narrative_guard(result: dict, data: ApplicationInput) -> dict:
    if result.get("score_status") == "screened_out_for_curator_review":
        return result
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    policy = json.loads(SCORING_POLICY_PATH.read_text(encoding="utf-8"))
    markers = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))["direction_markers"]
    answer_text = " ".join(str(value) for value in data.model_dump(exclude={
        "applicant_name", "application_id", "email", "vk_url", "birth_date"
    }).values()).casefold()
    prose_fields = (
        "summary", "verdict_comment", "fit_for_museum", "fit_for_candidate", "direction_reason"
    )
    prose = " ".join(
        [str(result.get(key, "")) for key in prose_fields]
        + [str(value) for value in result.get("possible_problems", [])]
    ).casefold()
    unsupported = [
        name for name, pattern in rules["unsupported_patterns"].items()
        if re.search(pattern, prose) and (name != "unsupported_hour_minimum" or data.availability_hours_month > 0)
    ]
    if any(
        direction != data.desired_direction and re.search(pattern, prose)
        and not re.search(pattern, answer_text)
        for direction, pattern in markers.items()
    ):
        unsupported.append("unmentioned_direction")
    problems = availability_risks(data)
    for contribution in result.get("content_signal_contributions", []):
        warning = policy["content_commentary"].get(contribution["signal"])
        if warning and warning not in problems:
            problems.append(warning)
    result["possible_problems"] = problems
    reviews = {item["field_id"]: item for item in result.get("field_reviews", [])}
    strengths = []
    if reviews.get("motivation", {}).get("category") == "specific_relevant":
        strengths.append(rules["verified_strengths"]["specific_motivation"])
    if reviews.get("experience", {}).get("category") in {"specific_relevant", "relevant_general"}:
        strengths.append(rules["verified_strengths"]["described_experience"])
    if valid_availability_days(data.availability_days):
        strengths.append(rules["verified_strengths"]["specific_days"])
    result["strengths"] = strengths
    result["narrative_guard_reasons"] = unsupported
    score = result.get("score")
    manual_signals = [item["signal"] for item in result.get("content_signal_contributions", [])
                      if item["signal"] in policy["manual_review_signals"]]
    serious = bool(manual_signals)
    if score is None:
        result["preliminary_verdict"] = "insufficient_information"
    elif score <= policy["verdict_policy"]["manual_review_max_score"] or serious:
        result["preliminary_verdict"] = "manual_review"
    else:
        result["preliminary_verdict"] = "suggest_interview"
    verdict = result["preliminary_verdict"]
    result["verdict_comment"] = rules["verdict_comments"][verdict]
    if unsupported or verdict != "suggest_interview":
        direction = DIRECTIONS[data.desired_direction]
        if verdict == "manual_review":
            reason = next((policy["content_commentary"][signal] for signal in manual_signals
                           if signal in policy["content_commentary"]), None)
            result["summary"] = rules["manual_review_summary"].format(
                score=score,
                reason=reason or (problems[0] if problems else rules["manual_review_reason_fallback"]),
            )
        elif verdict == "insufficient_information":
            result["summary"] = rules["insufficient_summary"]
        else:
            result["summary"] = rules["fallback_summary"].format(
                direction=direction, hours=data.availability_hours_month,
            )
        for field, template_key in (
            ("fit_for_museum", "fallback_fit_for_museum"),
            ("fit_for_candidate", "fallback_fit_for_candidate"),
            ("direction_reason", "fallback_direction_reason"),
        ):
            result[field] = rules[template_key].format(direction=direction)
        result["recommended_direction"] = data.desired_direction
        result["recommended_direction_label"] = direction
    return result
