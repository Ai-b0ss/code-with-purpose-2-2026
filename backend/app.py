"""Minimal runnable GIM hackathon prototype.

The evaluation implemented here is deliberately a rules-only *demo*.
It does NOT claim to be the museum-calibrated AI scorer.
"""
from __future__ import annotations

import csv
from contextlib import contextmanager
import io
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend" / "index.html"
DEFAULT_DB = ROOT / "data" / "gim.sqlite3"
DIRECTIONS = {
    "organizational": "Организационное",
    "project": "Проектное",
    "collections": "Фондовое",
    "inclusive": "Инклюзивное",
    "media": "Медиа",
    "undecided": "Пока не определился",
}
Direction = Literal[
    "organizational", "project", "collections",
    "inclusive", "media", "undecided",
]


class ApplicationInput(BaseModel):
    application_id: str | None = Field(default=None, max_length=90)
    applicant_name: str = Field(min_length=1, max_length=200)
    age_over_18: bool
    motivation: str = Field(min_length=10, max_length=4000)
    experience: str = Field(default="", max_length=4000)
    expectations: str = Field(default="", max_length=4000)
    availability_hours_month: int = Field(ge=0, le=400)
    other_commitments: str = Field(default="", max_length=1500)
    desired_direction: Direction
    followup_answer: str = Field(default="", max_length=2000)
    case_response: str = Field(default="", max_length=2000)

    # Расширенные поля официальной анкеты ГИМ
    email: str = Field(default="", max_length=200)
    vk_url: str = Field(default="", max_length=200)
    birth_date: str = Field(default="", max_length=30)
    consent_personal_data: bool = Field(default=False)
    status: str = Field(default="student", max_length=50)
    university: str = Field(default="", max_length=200)
    university_other: str = Field(default="", max_length=200)
    faculty: str = Field(default="", max_length=200)
    course: str = Field(default="", max_length=100)
    scientific_interests: str = Field(default="", max_length=2000)
    foreign_languages: list[str] = Field(default_factory=list)
    hobbies: str = Field(default="", max_length=2000)
    availability_schedule_type: str = Field(default="flexible", max_length=50)
    availability_days: list[str] = Field(default_factory=list)
    other_volunteer_programs: str = Field(default="", max_length=1500)
    source: str = Field(default="", max_length=200)
    followup_answers: dict[str, str] = Field(default_factory=dict)


class EligibilityInput(BaseModel):
    birth_date: str = Field(max_length=30)
    consent_personal_data: bool


@contextmanager
def db_connection():
    database = Path(os.environ.get("GIM_DB_PATH", str(DEFAULT_DB)))
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            applicant_name TEXT NOT NULL,
            payload TEXT NOT NULL,
            evaluation TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS contacts (
            application_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            FOREIGN KEY(application_id) REFERENCES applications(id)
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS assessment_runs (
            run_id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            rubric_version TEXT NOT NULL,
            kb_versions TEXT NOT NULL,
            result TEXT NOT NULL,
            FOREIGN KEY(application_id) REFERENCES applications(id)
        )"""
    )
    # Migrate records written before the contact split, preserving public API output.
    for row in connection.execute(
        "SELECT a.id, a.applicant_name, a.payload FROM applications a "
        "LEFT JOIN contacts c ON c.application_id=a.id WHERE c.application_id IS NULL"
    ).fetchall():
        payload = json.loads(row["payload"])
        contact = {key: payload.pop(key, "" if key != "consent_personal_data" else False)
                   for key in ("applicant_name", "email", "vk_url", "birth_date", "consent_personal_data")}
        contact["applicant_name"] = contact["applicant_name"] or row["applicant_name"]
        connection.execute("INSERT INTO contacts(application_id, payload) VALUES(?, ?)",
                           (row["id"], json.dumps(contact, ensure_ascii=False)))
        connection.execute("UPDATE applications SET applicant_name='', payload=? WHERE id=?",
                           (json.dumps(payload, ensure_ascii=False), row["id"]))
    connection.commit()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def clarification_for(data: ApplicationInput) -> str | None:
    if not data.expectations.strip() and not data.followup_answer.strip():
        return "Какие музейные задачи вы хотели бы выполнять?"
    if data.availability_hours_month == 0 and not data.followup_answer.strip():
        return "Когда и в каком формате вы сможете участвовать?"
    if data.desired_direction == "collections" and not data.followup_answer.strip():
        return "Готовы ли вы рассмотреть другие направления, если мест в фондах не будет?"
    return None


def clarifications_for(data: ApplicationInput) -> list[dict]:
    items: list[dict] = []

    # Мотивация
    if not data.expectations.strip() and not data.followup_answer.strip() and not data.followup_answers.get("motivation_detail"):
        items.append({
            "id": "motivation_detail",
            "category": "Мотивация",
            "question": "Что именно в работе Исторического музея вам наиболее интересно и почему?",
            "reason": "Нужна связь интереса кандидата с конкретными задачами музея.",
        })

    # Специфика выбранного направления (из официальных материалов ГИМ)
    if data.desired_direction == "collections" and not data.followup_answers.get("collections_focus"):
        items.append({
            "id": "collections_focus",
            "category": "Фондовое направление",
            "question": "Какие именно фонды вас привлекают и готовы ли вы рассмотреть другие направления, если мест в фондах будет недостаточно?",
            "reason": "Места в фондах строго ограничены; кураторам важно знать вашу гибкость.",
        })
    elif data.desired_direction == "project" and not data.followup_answers.get("project_format"):
        items.append({
            "id": "project_format",
            "category": "Проектное направление",
            "question": "Какие форматы вам ближе: мастер-классы, исторические квесты, тематические экскурсии или образовательные игры?",
            "reason": "Поможет распределить вас в проектную группу с релевантными задачами.",
        })
    elif data.desired_direction == "media" and not data.followup_answers.get("media_tasks"):
        items.append({
            "id": "media_tasks",
            "category": "Медиа-направление",
            "question": "Какая деятельность внутри направления вам интереснее: фото, видео, посты, контент-план или дизайн?",
            "reason": "Медиа-служба музея формирует команды под конкретный тип контента.",
        })
    elif data.desired_direction == "organizational" and not data.followup_answers.get("audience_type"):
        items.append({
            "id": "audience_type",
            "category": "Организационное направление",
            "question": "С какой категорией посетителей вы предпочитаете взаимодействовать: дети, молодёжь, семейная аудитория или пожилые люди?",
            "reason": "Музейные мероприятия делятся по целевым аудиториям.",
        })

    # Доступность и совмещение
    if data.availability_hours_month == 0 and not data.followup_answers.get("availability_boost"):
        items.append({
            "id": "availability_boost",
            "category": "График и занятость",
            "question": "Когда и в каком формате вы сможете участвовать в музейных задачах?",
            "reason": "Нужно уточнить фактическую доступность для выбранного направления.",
        })
    elif data.other_volunteer_programs.strip() and not data.followup_answers.get("other_volunteering"):
        items.append({
            "id": "other_volunteering",
            "category": "Совмещение волонтерства",
            "question": "Как планируете распределять нагрузку с участием в других волонтерских объединениях?",
            "reason": "Музею важно убедиться в отсутствии конфликта графиков в дни ключевых событий.",
        })

    return items


def evaluate_demo(data: ApplicationInput) -> dict:
    """Deterministic example for wiring UI/API/Sheets; not museum scoring."""
    direction = data.desired_direction
    if direction == "undecided":
        direction = "project" if "квест" in data.motivation.lower() else "organizational"

    possible_problems = []
    if data.availability_hours_month == 0:
        possible_problems.append("Уточнить, когда кандидат сможет участвовать.")
    if data.other_commitments.strip():
        possible_problems.append("Уточнить совместимость с другими обязательствами.")
    if not data.expectations.strip():
        possible_problems.append("Уточнить ожидаемые задачи.")

    strengths = ["Кандидат указал интерес к программе музея."]
    if data.experience.strip():
        strengths.append("Кандидат описал предыдущий опыт.")
    if data.availability_hours_month > 0:
        strengths.append("Кандидат указал конкретное доступное время.")

    return {
        "score": None,
        "score_status": "not_calibrated",
        "evaluation_mode": "rules_demo",
        "summary": (
            f"Интересующее направление: {DIRECTIONS[data.desired_direction]}. "
            f"Доступность: {data.availability_hours_month} ч/мес. "
            f"Мотивация: {data.motivation.strip()[:230]}"
        ),
        "strengths": strengths,
        "possible_problems": possible_problems,
        "recommended_direction": direction,
        "recommended_direction_label": DIRECTIONS[direction],
        "direction_reason": (
            "Предварительная рекомендация по заявленному интересу. "
            "Окончательная оценка будет добавлена после калибровки по анкетам кураторов."
        ),
        "fit_for_candidate": (
            "На очном собеседовании нужно сопоставить ожидания человека "
            "с реальными задачами выбранного направления."
        ),
        "interview_questions": [
            "Расскажите о конкретной задаче, которую хотели бы решить в музее.",
            "Какой график участия вы можете поддерживать на протяжении нескольких месяцев?",
        ],
        "evidence": [
            {"question_id": "motivation", "reason": data.motivation.strip()[:230]},
            {
                "question_id": "availability_hours_month",
                "reason": f"{data.availability_hours_month} часов в месяц",
            },
        ],
    }


def evaluate_application(data: ApplicationInput) -> dict:
    """Use the standalone evaluator in actual API flows, configured by environment.

    Default demo is explicit. Live LLM is opt-in via GIM_EVALUATION_MODE=llm.
    Provider failures are surfaced instead of passing simulated AI as real.
    """
    from backend.evaluator import EvaluationError, evaluate
    from backend.eligibility import check_eligibility
    eligibility = check_eligibility(data)
    if eligibility["status"] == "age_unverified":
        return {
            "score": None, "score_status": "needs_review",
            "evaluation_mode": "eligibility_rules", "eligibility": eligibility,
            "summary": "Возраст необходимо проверить вручную перед оценкой.",
            "recommended_direction": data.desired_direction,
            "recommended_direction_label": DIRECTIONS[data.desired_direction],
            "direction_reason": "Оценка ожидает подтверждения возраста.",
            "strengths": [], "possible_problems": [eligibility["reason"]],
            "interview_questions": [],
        }
    if eligibility["status"] == "ineligible":
        return {
            "score": None, "score_status": "ineligible",
            "evaluation_mode": "eligibility_rules", "eligibility": eligibility,
            "summary": "Заявка не проходит базовое условие участия: возраст 18+.",
            "recommended_direction": data.desired_direction,
            "recommended_direction_label": DIRECTIONS[data.desired_direction],
            "direction_reason": "Оценка по рубрике не запускалась.",
            "strengths": [], "possible_problems": [eligibility["reason"]],
            "interview_questions": [],
        }
    try:
        if os.getenv("GIM_EVALUATION_MODE") == "rag_llm":
            from backend.scoring import assess
            result = assess(data)
        else:
            result = evaluate(
                data,
                fallback_to_demo=os.getenv("GIM_LLM_FALLBACK_TO_DEMO", "0") == "1",
            )
        result["eligibility"] = eligibility
        return result
    except EvaluationError as exc:
        raise HTTPException(status_code=503, detail=f"evaluator_{exc.code}") from None


app = FastAPI(title="ГИМ — Музейный компас", version="0.1.0")


@app.get("/")
def home():
    return FileResponse(FRONTEND, media_type="text/html")


@app.get("/api/v1/health")
def health():
    mode = os.getenv("GIM_EVALUATION_MODE", "demo")
    return {
        "status": "ok", "version": "0.1.0",
        "evaluation_mode": mode if mode in {"llm", "rag_llm"} else "rules_demo",
        "fallback_to_demo": os.getenv("GIM_LLM_FALLBACK_TO_DEMO", "0") == "1",
    }


@app.post("/api/v1/eligibility")
def eligibility_preview(data: EligibilityInput):
    from backend.eligibility import check_eligibility
    return check_eligibility(data)


@app.post("/api/v1/clarification")
def clarification(data: ApplicationInput):
    questions = clarifications_for(data)
    legacy_question = clarification_for(data)
    return {
        "question": legacy_question,
        "questions": questions,
    }


@app.post("/api/v1/preview")
def preview(data: ApplicationInput):
    return {"evaluation": evaluate_application(data)}


@app.post("/api/v1/applications", status_code=201)
def save_application(data: ApplicationInput):
    application_id = data.application_id or uuid.uuid4().hex
    if not data.consent_personal_data:
        raise HTTPException(status_code=422, detail="Необходимо явное согласие на обработку персональных данных.")
    created_at = datetime.now(timezone.utc).isoformat()
    with db_connection() as con:
        previous = con.execute(
            "SELECT payload, evaluation, created_at FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        if previous is not None:
            return {
                "application_id": application_id,
                "created_at": previous["created_at"],
                "evaluation": json.loads(previous["evaluation"]),
                "created": False,
            }
        # Evaluate only new applications. A retry with the same ID must not
        # re-call a paid/slow/unavailable model or overwrite the original result.
        evaluation = evaluate_application(data)
        full_payload = data.model_dump()
        contact_payload = {key: full_payload.pop(key) for key in ("applicant_name", "email", "vk_url", "birth_date", "consent_personal_data")}
        con.execute(
            "INSERT INTO applications(id, created_at, applicant_name, payload, evaluation) "
            "VALUES(?, ?, ?, ?, ?)",
            (
                application_id, created_at, "",
                json.dumps(full_payload, ensure_ascii=False), json.dumps(evaluation, ensure_ascii=False),
            ),
        )
        con.execute(
            "INSERT INTO contacts(application_id, payload) VALUES(?, ?)",
            (application_id, json.dumps(contact_payload, ensure_ascii=False)),
        )
        con.execute(
            "INSERT INTO assessment_runs(run_id, application_id, created_at, rubric_version, kb_versions, result) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, application_id, created_at,
             evaluation.get("rubric_version", "legacy"),
             json.dumps(evaluation.get("kb_versions", [])), json.dumps(evaluation, ensure_ascii=False)),
        )
    return {
        "application_id": application_id,
        "created_at": created_at,
        "evaluation": evaluation,
        "created": True,
    }


@app.get("/api/v1/applications")
def list_applications():
    with db_connection() as con:
        records = con.execute(
            "SELECT a.id, a.created_at, a.applicant_name, a.evaluation, c.payload AS contact "
            "FROM applications a LEFT JOIN contacts c ON c.application_id=a.id "
            "ORDER BY a.created_at DESC LIMIT 200"
        ).fetchall()
    return {
        "items": [
            {
                "application_id": row["id"],
                "created_at": row["created_at"],
                "applicant_name": json.loads(row["contact"]).get("applicant_name", "") if row["contact"] else row["applicant_name"],
                "evaluation": json.loads(row["evaluation"]),
            }
            for row in records
        ]
    }


@app.get("/api/v1/applications/{application_id}")
def get_application(application_id: str):
    with db_connection() as con:
        row = con.execute(
            "SELECT a.id, a.created_at, a.payload, a.evaluation, c.payload AS contact "
            "FROM applications a LEFT JOIN contacts c ON c.application_id=a.id WHERE a.id=?",
            (application_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return {
        "application_id": row["id"],
        "created_at": row["created_at"],
        "answers": {**json.loads(row["payload"]), **(json.loads(row["contact"]) if row["contact"] else {})},
        "evaluation": json.loads(row["evaluation"]),
    }


def safe_csv(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@app.get("/api/v1/export.csv")
def export_csv():
    columns = [
        "ID заявки", "Дата", "Имя", "Возраст 18+", "Email", "VK",
        "Статус", "ВУЗ", "Факультет", "Курс", "Языки",
        "Мотивация", "Опыт", "Ожидания", "Часов/месяц", "Тип графика", "Дни",
        "Другие обязательства", "Другие волонтерства", "Направление", "Источник",
        "Дополнительный ответ", "Оценка 0-3", "Саммари",
        "Возможные проблемы", "Рекомендуемое направление",
        "Почему направление", "Вопросы для собеседования",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    with db_connection() as con:
        records = con.execute(
            "SELECT a.id, a.created_at, a.payload, a.evaluation, c.payload AS contact "
            "FROM applications a LEFT JOIN contacts c ON c.application_id=a.id ORDER BY a.created_at"
        ).fetchall()
    for row in records:
        entry = {**json.loads(row["payload"]), **(json.loads(row["contact"]) if row["contact"] else {})}
        e = json.loads(row["evaluation"])
        writer.writerow([
            safe_csv(row["id"]), safe_csv(row["created_at"]),
            safe_csv(entry.get("applicant_name", "")), entry.get("age_over_18", False),
            safe_csv(entry.get("email", "")), safe_csv(entry.get("vk_url", "")),
            safe_csv(entry.get("status", "")),
            safe_csv(entry.get("university_other") or entry.get("university", "")),
            safe_csv(entry.get("faculty", "")), safe_csv(entry.get("course", "")),
            safe_csv(", ".join(entry.get("foreign_languages", [])) if isinstance(entry.get("foreign_languages"), list) else str(entry.get("foreign_languages", ""))),
            safe_csv(entry.get("motivation", "")), safe_csv(entry.get("experience", "")),
            safe_csv(entry.get("expectations", "")), entry.get("availability_hours_month", 0),
            safe_csv(entry.get("availability_schedule_type", "")),
            safe_csv(", ".join(entry.get("availability_days", [])) if isinstance(entry.get("availability_days"), list) else str(entry.get("availability_days", ""))),
            safe_csv(entry.get("other_commitments", "")),
            safe_csv(entry.get("other_volunteer_programs", "")),
            safe_csv(DIRECTIONS.get(entry.get("desired_direction", ""), entry.get("desired_direction", ""))),
            safe_csv(entry.get("source", "")),
            safe_csv(entry.get("followup_answer", "")), safe_csv(e.get("score")),
            safe_csv(e.get("summary", "")), safe_csv("; ".join(e.get("possible_problems", []))),
            safe_csv(e.get("recommended_direction_label", "")),
            safe_csv(e.get("direction_reason", "")),
            safe_csv("; ".join(e.get("interview_questions", []))),
        ])
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gim-applications.csv"'},
    )


from backend.sessions import router as sessions_router
app.include_router(sessions_router, prefix="/api/v1")
