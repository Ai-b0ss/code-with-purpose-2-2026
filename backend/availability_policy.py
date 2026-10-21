"""Neutral schedule questions until the museum approves role requirements."""
from __future__ import annotations


def availability_risks(answers) -> list[str]:
    """Return factual review prompts, never proxy-based rejection signals."""
    prompts: list[str] = []
    hours = getattr(answers, "availability_hours_month", None)
    obligations = str(getattr(answers, "other_commitments", "") or "").strip()
    volunteering = str(getattr(answers, "other_volunteer_programs", "") or "").strip()
    if hours == 0:
        prompts.append("Кандидат указал 0 часов в месяц: уточнить, когда участие станет возможным.")
    if obligations or volunteering:
        prompts.append("Уточнить совместимость заявленного графика с другими обязательствами.")
    return prompts
