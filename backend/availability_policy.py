"""Configurable availability signals supported by the museum interview."""
from __future__ import annotations

import json
import re
from pathlib import Path


RULES = json.loads((Path(__file__).resolve().parent.parent / "knowledge" / "availability_rules.json").read_text(encoding="utf-8"))


def valid_availability_days(days) -> list[str]:
    """Use only actual weekdays as structured evidence of a concrete schedule."""
    if not isinstance(days, list):
        return []
    allowed = {
        value.strip().casefold()
        for option in RULES["weekday_options"]
        for value in (option["id"], *option.get("aliases", []))
    }
    return list(dict.fromkeys(day.strip().casefold() for day in days
                              if isinstance(day, str) and day.strip().casefold() in allowed))


def _matches(name: str, value: str) -> bool:
    return bool(re.search(RULES["patterns"][name], value))


def availability_signal_ids(answers) -> list[str]:
    """Return source-backed workload signal IDs for deterministic scoring."""
    course = str(getattr(answers, "course", "") or "").casefold()
    status = str(getattr(answers, "status", "") or "").casefold()
    obligations = str(getattr(answers, "other_commitments", "") or "").casefold()
    volunteering = str(getattr(answers, "other_volunteer_programs", "") or "").casefold()
    hours = getattr(answers, "availability_hours_month", None)
    days = valid_availability_days(getattr(answers, "availability_days", None))
    followups = getattr(answers, "followup_answers", None) or {}
    clarified = str(followups.get("ai_availability", "") or "").casefold().strip()
    signals: list[str] = []
    if _matches("first_year", course) and not _matches("graduate_course", course):
        signals.append("first_year")
    if _matches("full_time", obligations + " " + status):
        signals.append("full_time")
    if _matches("multiple_projects", volunteering + " " + obligations):
        signals.append("multiple_projects")
    if isinstance(hours, int) and not isinstance(hours, bool) and hours == 0:
        signals.append("zero_hours")

    has_specific_followup = (
        bool(clarified) and _matches("specific_schedule", clarified)
        and not _matches("uncertain_schedule", clarified)
    )
    if signals and isinstance(hours, int) and hours > 0 and (
        has_specific_followup or bool(days)
    ):
        signals.append("mitigation")
    return signals


def availability_risks(answers) -> list[str]:
    """Explain explicit workload signals without deciding eligibility."""
    return [RULES["messages"][signal] for signal in availability_signal_ids(answers)]
