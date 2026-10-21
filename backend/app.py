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
import math
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from backend import google_sheets

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
    followup_questions: list[dict[str, str]] = Field(default_factory=list, max_length=3)


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
        """CREATE TABLE IF NOT EXISTS application_reservations (
            id TEXT PRIMARY KEY,
            token TEXT NOT NULL,
            started_at REAL NOT NULL
        )"""
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(applications)")}
    for name, definition in {
        "sheet_sync_status": "TEXT NOT NULL DEFAULT 'pending'",
        "sheet_sync_error": "TEXT",
        "sheet_row_url": "TEXT",
        "sheet_synced_at": "TEXT",
        "sheet_sync_token": "TEXT",
        "sheet_sync_started_at": "REAL",
    }.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE applications ADD COLUMN {name} {definition}")
    connection.commit()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def clarification_for(data: ApplicationInput) -> str | None:
    if not data.age_over_18:
        return "Участие предполагает возраст 18+. Вы хотите уточнить условия у координатора?"
    if len(data.motivation.strip()) < 65 and not data.followup_answer.strip():
        return "Что именно в работе Исторического музея вам интересно и почему?"
    if data.availability_hours_month < 8 and not data.followup_answer.strip():
        return "При каких условиях вы могли бы выделять больше времени для участия?"
    if data.desired_direction == "collections" and not data.followup_answer.strip():
        return "Готовы ли вы рассмотреть другие направления, если мест в фондах не будет?"
    return None


def clarifications_for(data: ApplicationInput) -> list[dict]:
    items: list[dict] = []

    # Возраст
    if not data.age_over_18:
        items.append({
            "id": "age_terms",
            "category": "Возраст и регламент",
            "question": "Участие предполагает возраст 18+. Вы хотите уточнить условия у координатора?",
            "reason": "По правилам музея большинство программ рассчитаны на совершеннолетних волонтёров.",
        })

    # Мотивация
    if len(data.motivation.strip()) < 65 and not data.followup_answer.strip() and not data.followup_answers.get("motivation_detail"):
        items.append({
            "id": "motivation_detail",
            "category": "Мотивация",
            "question": "Что именно в работе Исторического музея вам наиболее интересно и почему?",
            "reason": "Кураторам важно понимать конкретные причины выбора ГИМ среди других музеев.",
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
    if data.availability_hours_month < 8 and not data.followup_answers.get("availability_boost"):
        items.append({
            "id": "availability_boost",
            "category": "График и занятость",
            "question": "При каких условиях вы могли бы выделять больше времени для участия (например, в выходные или на спецпроектах)?",
            "reason": "Необходимо уточнить, подходит ли заявленный график для выбранного направления. Числовой минимум не подтверждён.",
        })
    elif data.other_volunteer_programs.strip() and not data.followup_answers.get("other_volunteering"):
        items.append({
            "id": "other_volunteering",
            "category": "Совмещение волонтерства",
            "question": "Как планируете распределять нагрузку с участием в других волонтерских объединениях?",
            "reason": "Музею важно убедиться в отсутствии конфликта графиков в дни ключевых событий.",
        })

    # At most three actionable form questions; risk/eligibility before role detail.
    priorities = {
        "age_terms": 0,
        "availability_boost": 1,
        "other_volunteering": 2,
        "motivation_detail": 3,
        "collections_focus": 4,
        "project_format": 4,
        "media_tasks": 4,
        "audience_type": 4,
    }
    return sorted(items, key=lambda item: priorities.get(item["id"], 5))[:3]


def evaluate_demo(data: ApplicationInput) -> dict:
    """Deterministic example for wiring UI/API/Sheets; not museum scoring."""
    direction = data.desired_direction
    if direction == "undecided":
        direction = "project" if "квест" in data.motivation.lower() else "organizational"

    possible_problems = []
    if data.availability_hours_month < 8:
        possible_problems.append("Уточнить регулярность и достаточность доступного времени.")
    if data.other_commitments.strip():
        possible_problems.append("Уточнить совместимость с другими обязательствами.")
    if not data.age_over_18:
        possible_problems.append("Нужно уточнить формальное требование возраста 18+.")
    if len(data.motivation.strip()) < 65:
        possible_problems.append("Мотивация требует более конкретного примера.")

    strengths = ["Кандидат указал интерес к программе музея."]
    if data.experience.strip():
        strengths.append("Кандидат описал предыдущий опыт.")
    if data.availability_hours_month >= 8:
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
    try:
        return evaluate(
            data,
            fallback_to_demo=os.getenv("GIM_LLM_FALLBACK_TO_DEMO", "0") == "1",
        )
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
        "evaluation_mode": "llm" if mode == "llm" else "rules_demo",
        "fallback_to_demo": os.getenv("GIM_LLM_FALLBACK_TO_DEMO", "0") == "1",
    }


@app.post("/api/v1/clarification")
def clarification(data: ApplicationInput):
    # In live mode, ask the model to review five criteria BEFORE a final
    # evaluation. Do not confuse questions asked now with interview notes.
    from backend.clarification_agent import questions_before_verdict
    from backend.evaluator import EvaluationError
    try:
        result = questions_before_verdict(data)
    except EvaluationError as exc:
        # Do not silently switch from LLM to heuristic questions in live mode.
        raise HTTPException(status_code=503, detail=f"clarification_{exc.code}") from None
    return {"question": None, **result}


@app.post("/api/v1/preview")
def preview(data: ApplicationInput):
    return {"evaluation": evaluate_application(data)}


@app.post("/api/v1/applications", status_code=201)
def save_application(data: ApplicationInput):
    """Reserve one ID in SQLite before a slow evaluator call.

    Duplicate requests wait for the winning result and never evaluate again
    while a live reservation exists. A crashed worker's reservation may be
    claimed after 10 minutes; exactly-once external LLM calls across crashes
    cannot be guaranteed without provider-side idempotency.
    """
    application_id = data.application_id or uuid.uuid4().hex
    if not data.consent_personal_data:
        raise HTTPException(status_code=422, detail="Необходимо явное согласие на обработку персональных данных.")

    token = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        max_wait = float(os.getenv("GIM_LLM_TIMEOUT", "180")) + 30
        if not math.isfinite(max_wait):
            max_wait = 210
    except ValueError:
        max_wait = 210
    deadline = time.monotonic() + min(360, max(30, max_wait))

    def duplicate(row):
        return {
            "application_id": application_id,
            "created_at": row["created_at"],
            "evaluation": json.loads(row["evaluation"]),
            "created": False,
            "sheet_sync": sheet_sync_view(row),
        }

    # Keep each SQLite write transaction short; no DB-wide lock during LLM.
    while True:
        owner = False
        with db_connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM applications WHERE id = ?",
                (application_id,),
            ).fetchone()
            if row is not None:
                return duplicate(row)

            reservation = con.execute(
                "SELECT token, started_at FROM application_reservations WHERE id = ?",
                (application_id,),
            ).fetchone()
            if reservation is None:
                con.execute(
                    "INSERT INTO application_reservations(id, token, started_at) VALUES(?, ?, ?)",
                    (application_id, token, time.time()),
                )
                owner = True
            elif time.time() - reservation["started_at"] >= 600:
                # Safe claim after a crash; the old token cannot commit results.
                con.execute(
                    "UPDATE application_reservations SET token = ?, started_at = ? WHERE id = ?",
                    (token, time.time(), application_id),
                )
                owner = True
        if owner:
            break
        if time.monotonic() >= deadline:
            raise HTTPException(
                status_code=409,
                detail="Анкета с этим ID сейчас обрабатывается. Повторите запрос с тем же ID.",
                headers={"Retry-After": "3"},
            )
        time.sleep(0.15)

    try:
        evaluation = evaluate_application(data)
        with db_connection() as con:
            con.execute("BEGIN IMMEDIATE")
            reservation = con.execute(
                "SELECT token FROM application_reservations WHERE id = ?",
                (application_id,),
            ).fetchone()
            if reservation is None or reservation["token"] != token:
                existing = con.execute(
                    "SELECT * FROM applications WHERE id = ?",
                    (application_id,),
                ).fetchone()
                if existing is not None:
                    return duplicate(existing)
                raise HTTPException(
                    status_code=409,
                    detail="Резервирование анкеты истекло, результат не сохранён. Повторите запрос.",
                )
            con.execute(
                "INSERT INTO applications(id, created_at, applicant_name, payload, evaluation) "
                "VALUES(?, ?, ?, ?, ?)",
                (application_id, created_at, data.applicant_name,
                 data.model_dump_json(), json.dumps(evaluation, ensure_ascii=False)),
            )
            con.execute(
                "DELETE FROM application_reservations WHERE id = ? AND token = ?",
                (application_id, token),
            )
    except Exception:
        try:
            with db_connection() as con:
                con.execute(
                    "DELETE FROM application_reservations WHERE id = ? AND token = ?",
                    (application_id, token),
                )
        except sqlite3.Error:
            pass
        raise

    sync = sync_application(application_id)
    return {
        "application_id": application_id,
        "created_at": created_at,
        "evaluation": evaluation,
        "created": True,
        "sheet_sync": sync,
    }


@app.get("/api/v1/applications")
def list_applications():
    with db_connection() as con:
        records = con.execute(
            "SELECT * "
            "FROM applications ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    return {
        "items": [
            {
                "application_id": row["id"],
                "created_at": row["created_at"],
                "applicant_name": row["applicant_name"],
                "evaluation": json.loads(row["evaluation"]),
                "sheet_sync": sheet_sync_view(row),
            }
            for row in records
        ]
    }


@app.get("/api/v1/applications/{application_id}")
def get_application(application_id: str):
    with db_connection() as con:
        row = con.execute(
            "SELECT * FROM applications WHERE id=?",
            (application_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return {
        "application_id": row["id"],
        "created_at": row["created_at"],
        "answers": json.loads(row["payload"]),
        "evaluation": json.loads(row["evaluation"]),
        "sheet_sync": sheet_sync_view(row),
    }


def safe_csv(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = " ".join(text.splitlines())
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def sheet_sync_view(row) -> dict:
    return {
        "status": row["sheet_sync_status"],
        "error": row["sheet_sync_error"],
        "row_url": row["sheet_row_url"],
        "synced_at": row["sheet_synced_at"],
    }


def sheet_values(row) -> list:
    entry = json.loads(row["payload"])
    e = json.loads(row["evaluation"])
    clean = safe_csv
    joined = lambda key: ", ".join(entry.get(key, []))
    return [
        clean(row["id"]), clean(row["created_at"]), clean(entry.get("applicant_name")),
        entry.get("age_over_18", False), entry.get("consent_personal_data", False),
        clean(entry.get("birth_date")), clean(entry.get("email")), clean(entry.get("vk_url")),
        clean(entry.get("status")), clean(entry.get("university_other") or entry.get("university")),
        clean(entry.get("faculty")), clean(entry.get("course")), clean(entry.get("scientific_interests")),
        clean(joined("foreign_languages")), clean(entry.get("motivation")), clean(entry.get("experience")),
        clean(entry.get("expectations")), clean(entry.get("hobbies")), entry.get("availability_hours_month", 0),
        clean(entry.get("availability_schedule_type")), clean(joined("availability_days")),
        clean(entry.get("other_commitments")), clean(entry.get("other_volunteer_programs")),
        clean(DIRECTIONS.get(entry.get("desired_direction"), entry.get("desired_direction"))),
        clean(entry.get("source")), clean(entry.get("followup_answer")), clean(e.get("score")),
        clean(e.get("summary")), clean("; ".join(e.get("possible_problems", []))),
        clean(e.get("recommended_direction_label")), clean(e.get("direction_reason")),
        clean("; ".join(e.get("interview_questions", []))),
    ]


def sync_application(application_id: str) -> dict:
    """Claim a short-lived delivery lease; never hold a SQLite transaction over HTTP."""
    token = uuid.uuid4().hex
    with db_connection() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Заявка не найдена")
        if row["sheet_sync_status"] == "synced":
            return sheet_sync_view(row)
        if row["sheet_sync_status"] == "syncing" and time.time() - (row["sheet_sync_started_at"] or 0) < 180:
            return sheet_sync_view(row)
        con.execute("UPDATE applications SET sheet_sync_status='syncing', sheet_sync_error=NULL, "
                    "sheet_sync_token=?, sheet_sync_started_at=? WHERE id=?",
                    (token, time.time(), application_id))
    try:
        if not google_sheets.configured():
            raise google_sheets.SheetSyncError("google_not_configured")
        row_url = google_sheets.write_row(application_id, sheet_values(row))
        status, error = "synced", None
    except Exception as exc:
        # Never store provider bodies, credentials, or applicant data in errors.
        status = "failed"
        error = str(exc) if isinstance(exc, google_sheets.SheetSyncError) else "google_sync_error"
        row_url = None
    with db_connection() as con:
        con.execute("UPDATE applications SET sheet_sync_status=?, sheet_sync_error=?, "
                    "sheet_row_url=COALESCE(?, sheet_row_url), sheet_synced_at=?, "
                    "sheet_sync_token=NULL, sheet_sync_started_at=NULL "
                    "WHERE id=? AND sheet_sync_token=?",
                    (status, error, row_url, datetime.now(timezone.utc).isoformat() if status == "synced" else None,
                     application_id, token))
        result = con.execute("SELECT * FROM applications WHERE id=?", (application_id,)).fetchone()
    return sheet_sync_view(result)


@app.post("/api/v1/applications/{application_id}/sync")
def retry_sheet_sync(application_id: str):
    return {"application_id": application_id, "sheet_sync": sync_application(application_id)}


@app.get("/api/v1/export.csv")
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(google_sheets.HEADERS)
    with db_connection() as con:
        records = con.execute(
            "SELECT id, created_at, payload, evaluation "
            "FROM applications ORDER BY created_at"
        ).fetchall()
    for row in records:
        writer.writerow(sheet_values(row))
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gim-applications.csv"'},
    )
