"""Versioned hard eligibility gate, separate from the six-criterion score."""
from __future__ import annotations

from datetime import date

RULE_VERSION = "gim-entry-v1"


def check_eligibility(data, *, on_date: date | None = None) -> dict:
    """Only confirmed no-consent and under-18 cases are hard stops."""
    if not data.consent_personal_data:
        return {"status": "no_consent", "rule_version": RULE_VERSION,
                "reason": "Нет согласия на обработку персональных данных."}

    try:
        born = date.fromisoformat(data.birth_date)
    except (TypeError, ValueError):
        return {"status": "age_unverified", "rule_version": RULE_VERSION,
                "reason": "Не удалось проверить возраст по дате рождения."}
    today = on_date or date.today()
    if born > today:
        return {"status": "age_unverified", "rule_version": RULE_VERSION,
                "reason": "Дата рождения находится в будущем."}
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if age < 18:
        return {"status": "ineligible", "rule_version": RULE_VERSION,
                "reason": "На дату подачи кандидату меньше 18 лет.", "age_years": age}
    return {"status": "eligible", "rule_version": RULE_VERSION,
            "reason": "Возраст 18+ и согласие подтверждены.", "age_years": age}
