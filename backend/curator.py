"""Shared human-facing labels and Q/A projection for curator outputs."""
import json
from html import escape
CRITERION_LABELS = {
    "motivation": "Мотивация", "skills": "Опыт и навыки",
    "availability": "Доступность", "form_and_contact": "Анкета и связь",
    "logistics": "Логистика",
}
VERDICT_LABELS = {
    "suggest_interview": "Рекомендовать собеседование",
    "manual_review": "Ручная проверка куратора",
    "not_recommended_for_interview": "Предварительно не рекомендован к собеседованию",
    "insufficient_information": "Недостаточно сведений",
}


def followup_pairs(answers: dict) -> list[dict]:
    return [{"question": q.get("question", ""),
             "answer": answers.get("followup_answers", {}).get(q.get("id", ""), "")}
            for q in answers.get("followup_questions", [])]


def interview_topics(evaluation: dict) -> list[str]:
    return evaluation.get("interview_focus") or evaluation.get("interview_questions") or []


def render_card(application_id: str, created_at: str, answers: dict, evaluation: dict) -> str:
    def text(value):
        return escape(str(value if value is not None else "Недостаточно сведений"))
    def bullets(values):
        return "<ul>" + "".join("<li>" + text(v) + "</li>" for v in values) + "</ul>" if values else "<p>Не отмечены.</p>"
    e = evaluation
    score = "Не рассчитан" if e.get("score") is None else text(e["score"]) + " / 3"
    criteria = "".join("<tr><th>" + text(CRITERION_LABELS.get(r["criterion"], r["criterion"])) +
                       "</th><td>" + text(r.get("level")) + "</td><td>" + text(r.get("rationale", "")) + "</td></tr>"
                       for r in e.get("scorecard", []))
    qa = "".join("<li><strong>" + text(p["question"]) + "</strong><p>" + text(p["answer"]) + "</p></li>"
                 for p in followup_pairs(answers))
    return ("<article><h2>" + text(answers.get("applicant_name")) + "</h2><p><code>" + text(application_id) +
            "</code> · " + text(created_at) + "</p><h3>" +
            text(VERDICT_LABELS.get(e.get("preliminary_verdict"), "Требуется проверка")) +
            "</h3><p>" + text(e.get("verdict_comment", "")) + "</p><p><strong>Оценка: " + score +
            "</strong> · " + text(e.get("score_explanation", "Пилотная шкала, не откалибрована")) +
            "</p><p><small>Режим: " + text(e.get("evaluation_mode")) + " · " + text(e.get("score_status")) +
            "</small></p><h3>Рекомендуемое направление: " + text(e.get("recommended_direction_label")) +
            "</h3><p>" + text(e.get("direction_reason", "")) + "</p><p>" + text(e.get("summary", "")) +
            "</p><h3>Конкретные риски</h3>" + bullets(e.get("possible_problems", [])) +
            "<h3>Темы очного собеседования для куратора</h3>" + bullets(interview_topics(e)) +
            "<h3>Пилотные оценки пяти критериев</h3><table><tr><th>Критерий</th><th>0–3</th><th>Обоснование</th></tr>" + criteria +
            "</table><h3>Сильные стороны</h3>" + bullets(e.get("strengths", [])) +
            "<h3>Уточнения до заключения</h3>" + ("<ol>" + qa + "</ol>" if qa else "<p>Не потребовались.</p>") +
            "<h3>Соответствие музею</h3><p>" + text(e.get("fit_for_museum", "")) +
            "</p><h3>Соответствие ожиданиям кандидата</h3><p>" + text(e.get("fit_for_candidate", "")) +
            "</p><details><summary>Полная анкета и проверяемые цитаты</summary><pre>" +
            text(json.dumps({"answers": answers, "evidence": e.get("evidence", [])}, ensure_ascii=False, indent=2)) +
            "</pre></details></article>")



def stored_evaluation(raw):
    """Annotate unversioned legacy records on read without changing their scores."""
    import json
    value = json.loads(raw)
    if 'provenance' not in value:
        value['provenance'] = {'evaluation_schema_version': 'legacy_unknown',
                               'prompt_version': 'legacy_unknown', 'model': 'legacy_unknown'}
    return value
