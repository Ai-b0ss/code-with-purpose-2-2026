"""AI diagnostic clarification before final museum curator recommendation.

The model inspects all five source criteria, identifies consequential unknowns
and asks up to two questions *of the candidate*, before submission and scoring.
"""
from __future__ import annotations

import json
import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app import ApplicationInput, clarifications_for
from backend.evaluator import EvaluationError, adapter_from_env, _unique_object


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


class QuestionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    criteria_review: list[CriterionReview] = Field(min_length=5, max_length=5)
    questions: list[FollowupQuestion] = Field(max_length=2)


PROMPT = """Ты аналитик анкет кандидатов-волонтёров Государственного исторического музея.
ЭТО ПЕРВЫЙ ЭТАП: после полного заполнения исходной анкеты и ДО итогового вердикта.
Не выставляй итоговую оценку и не принимай решение о приёме.
Действуй по пяти исходным критериям: motivation (мотивация и осознанность),
skills (практические навыки и релевантный опыт),
availability (учёба, работа, график и регулярная доступность),
form_and_contact (содержательность заполнения и возможность связи),
logistics (реальная возможность посещать музей/участвовать в нужных задачах).
Для каждой из РОВНО пяти групп дай state clear/uncertain/risk и
причину, опираясь только на анкету. Не домысливай возраст, вуз,
место жительства, расписание фондов и причины отсутствия данных.
Не делай штрафом закрытый VK, непрофильный диплом или отсутствие опыта.
Первый курс бакалавриата, полная ставка и множество других проектов —
факторы риска регулярности, но индивидуальный график может снять риск.

Затем сформируй ноль, один или максимум два коротких вопроса
НЕ ПО УМОЛЧАНИЮ, а только при существенной неопределённости, на ответ
которой действительно зависит рекомендация направления или понимание
регулярной доступности. Сначала availability/логистика, затем мотивы/навыки.
НЕ переспрашивай уже известное; НЕ спрашивай об одном факте дважды;
не предлагай обсуждать фонды без реального интереса к ним.
Если достаточно фактов — questions=[].
В question спрашивай НАПРЯМУЮ кандидата, в reason объясни зачем,
в decision_impact — как разные ответы повлияют на итог.
Если в followup_answers есть осмысленный ответ по соответствующему
критерию, не создавай новый вопрос по этому же критерию.
Важнейшее: эти вопросы задаются сейчас, ПЕРЕД итоговым анализом;
не выдавай вопросы 'для собеседования' вместо них.
Ответ только один JSON-объект со схемой output_schema."""


CRITERIA = ("motivation", "skills", "availability", "form_and_contact", "logistics")
LABELS = {
    "motivation": "Мотивация",
    "skills": "Опыт и навыки",
    "availability": "График и занятость",
    "form_and_contact": "Полнота анкеты и связь",
    "logistics": "Логистика",
}


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
    # Keep applicant identity and contact details out of preliminary prompting.
    answers = data.model_dump(exclude={"applicant_name", "application_id", "email", "vk_url", "birth_date"})
    schema = QuestionPlan.model_json_schema()
    request = json.dumps({"answers": answers, "output_schema": schema}, ensure_ascii=False)
    try:
        raw = adapter.complete(system=PROMPT, user=request, schema=schema)
        plan = QuestionPlan.model_validate(json.loads(raw, object_pairs_hook=_unique_object))
        labels = [r.criterion for r in plan.criteria_review]
        if len(labels) != 5 or set(labels) != set(CRITERIA):
            raise EvaluationError("invalid_model_json", ("clarification.criteria_invalid",))
        review = {r.criterion: r for r in plan.criteria_review}
        prior = data.followup_answers
        result = []
        seen = set()
        for q in plan.questions:
            if (q.criterion in seen or review[q.criterion].state == "clear"
                    or str(prior.get("ai_" + q.criterion, "")).strip()):
                raise EvaluationError("invalid_model_json", ("clarification.question_invalid",))
            seen.add(q.criterion)
            result.append({
                "id": "ai_" + q.criterion,
                "category": LABELS[q.criterion],
                "question": q.question,
                "reason": q.reason,
                "decision_impact": q.decision_impact,
            })
        return {
            "evaluation_mode": "llm",
            "criteria_review": [r.model_dump() for r in plan.criteria_review],
            "questions": result,
        }
    except EvaluationError:
        raise
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError, KeyError):
        raise EvaluationError("invalid_model_json", ("clarification.invalid_response",)) from None
