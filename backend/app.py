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


app = FastAPI(title="ГИМ — Музейный компас", version="0.1.0")


@app.get("/")
def home():
    return FileResponse(FRONTEND, media_type="text/html")


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": "0.1.0", "evaluation_mode": "rules_demo"}


@app.post("/api/v1/clarification")
def clarification(data: ApplicationInput):
    return {"question": clarification_for(data)}


@app.post("/api/v1/preview")
def preview(data: ApplicationInput):
    return {"evaluation": evaluate_demo(data)}


@app.post("/api/v1/applications", status_code=201)
def save_application(data: ApplicationInput):
    application_id = data.application_id or uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    evaluation = evaluate_demo(data)
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
        con.execute(
            "INSERT INTO applications(id, created_at, applicant_name, payload, evaluation) "
            "VALUES(?, ?, ?, ?, ?)",
            (
                application_id, created_at, data.applicant_name,
                data.model_dump_json(), json.dumps(evaluation, ensure_ascii=False),
            ),
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
            "SELECT id, created_at, applicant_name, evaluation "
            "FROM applications ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    return {
        "items": [
            {
                "application_id": row["id"],
                "created_at": row["created_at"],
                "applicant_name": row["applicant_name"],
                "evaluation": json.loads(row["evaluation"]),
            }
            for row in records
        ]
    }


@app.get("/api/v1/applications/{application_id}")
def get_application(application_id: str):
    with db_connection() as con:
        row = con.execute(
            "SELECT id, created_at, payload, evaluation FROM applications WHERE id=?",
            (application_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    return {
        "application_id": row["id"],
        "created_at": row["created_at"],
        "answers": json.loads(row["payload"]),
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
        "ID заявки", "Дата", "Имя", "Возраст 18+", "Мотивация", "Опыт",
        "Ожидания", "Часов/месяц", "Другие обязательства", "Направление",
        "Дополнительный ответ", "Оценка 0-3", "Саммари",
        "Возможные проблемы", "Рекомендуемое направление",
        "Почему направление", "Вопросы для собеседования",
    ]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    with db_connection() as con:
        records = con.execute(
            "SELECT id, created_at, payload, evaluation "
            "FROM applications ORDER BY created_at"
        ).fetchall()
    for row in records:
        entry = json.loads(row["payload"])
        e = json.loads(row["evaluation"])
        writer.writerow([
            safe_csv(row["id"]), safe_csv(row["created_at"]),
            safe_csv(entry["applicant_name"]), entry["age_over_18"],
            safe_csv(entry["motivation"]), safe_csv(entry["experience"]),
            safe_csv(entry["expectations"]), entry["availability_hours_month"],
            safe_csv(entry["other_commitments"]),
            safe_csv(DIRECTIONS[entry["desired_direction"]]),
            safe_csv(entry["followup_answer"]), safe_csv(e["score"]),
            safe_csv(e["summary"]), safe_csv("; ".join(e["possible_problems"])),
            safe_csv(e["recommended_direction_label"]),
            safe_csv(e["direction_reason"]),
            safe_csv("; ".join(e["interview_questions"])),
        ])
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gim-applications.csv"'},
    )
