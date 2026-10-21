"""Conservative, reviewable availability signals based on the GIM curator interview.

These are negative *signals of reliability*, NOT eligibility bans and not
invented curator scores. Confirm exceptions with a human interviewer.
"""
from __future__ import annotations

import re


def availability_risks(answers) -> list[str]:
    """Flag explicit schedule/retention risk while preserving individual exceptions."""
    course = str(getattr(answers, "course", "") or "").casefold()
    status = str(getattr(answers, "status", "") or "").casefold()
    obligations = str(getattr(answers, "other_commitments", "") or "").casefold()
    volunteering = str(getattr(answers, "other_volunteer_programs", "") or "").casefold()
    hours = getattr(answers, "availability_hours_month", None)
    days = getattr(answers, "availability_days", None) or []
    schedule = str(getattr(answers, "availability_schedule_type", "") or "").casefold()
    signals: list[str] = []

    is_first_year = bool(re.search(r"\b1[\s-]*(?:й|ый)?\s*курс\b|\bперв(?:ый|ом|ого|окурсник\w*)\s*курс", course))
    is_graduate_course = bool(re.search(r"магистратур|аспирантур|ординатур", course))
    # First-year *bachelor* workload risk does not apply to 1st-year masters.
    if is_first_year and not is_graduate_course:
        signals.append(
            "Первый курс бакалавриата — неблагоприятный предварительный сигнал "
            "долгосрочной доступности и устойчивости участия. Это не запрет: "
            "координатор должен проверить расписание, сессию и возможность "
            "регулярных смен в течение нескольких месяцев."
        )

    if re.search(
        r"полная\s+ставка|полную\s+ставку|полной\s+ставке|"
        r"фулл[\s-]*тайм|full[\s-]*time|\b40\s*час|\b5\s*/\s*2\b",
        obligations + " " + status,
    ):
        signals.append(
            "Работа на полной ставке — неблагоприятный предварительный сигнал "
            "совместимости и длительного участия; уточнить реальные "
            "смены и нагрузку в пиковые периоды. Гибкий график "
            "может быть индивидуальным исключением."
        )

    all_volunteering = volunteering + " " + obligations
    if re.search(
        r"(?:нескольк\w*|множеств\w*|много|двух|(?:три|тр[её]х)|"
        r"[2-9])[\s\w-]{0,45}(?:волонт\w*|проект\w*|программ\w*)|"
        r"(?:волонт\w*|проект\w*|программ\w*)[\s\w-]{0,35}"
        r"(?:нескольк\w*|множеств\w*|много|двух|(?:три|тр[её]х)|[2-9])",
        all_volunteering,
    ):
        signals.append(
            "Несколько параллельных волонтёрских проектов — неблагоприятный "
            "предварительный сигнал перегрузки и риска раннего ухода; "
            "уточнить часы, приоритет ГИМ и подтверждённую регулярность. "
            "Возможны индивидуальные исключения."
        )

    if isinstance(hours, int) and hours < 8:
        signals.append(
            "Заявлено мало часов в месяц — риск недостаточной регулярности; "
            "уточнить график и требования конкретного направления. "
            "Число 8 не является установленным музеем порогом отказа."
        )

    if signals and isinstance(hours, int) and hours >= 16 and (
        days or "выход" in obligations or "суббот" in obligations
        or "вечер" in obligations or schedule == "regular"
    ):
        signals.append(
            "Смягчающее обстоятельство: кандидат заявил доступные часы "
            "и конкретные дни/смены. Это может уменьшить риск, но "
            "не является доказанной долгосрочной доступностью."
        )
    return signals
