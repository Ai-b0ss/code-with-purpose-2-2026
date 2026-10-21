"""Versioned application sessions following the PDF dialogue contract."""
from __future__ import annotations

import json
import hashlib
import csv
import io
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from fastapi.responses import Response
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic import ValidationError

from backend.app import ApplicationInput, ROOT, evaluate_application
from backend.eligibility import check_eligibility
from backend.evaluator import EvaluationError

router = APIRouter()
QUESTIONNAIRE_VERSION = "gim-questionnaire-2026-v1"
CONTACT_FIELDS = {"applicant_name", "email", "vk_url", "birth_date"}
REQUIRED = (
    ("consent_personal_data", "CONSENT"),
    ("applicant_name", "CONTACT"), ("email", "CONTACT"),
    ("vk_url", "CONTACT"), ("birth_date", "CONTACT"),
    ("status", "PROFILE"), ("university", "PROFILE"),
    ("university_other", "PROFILE"), ("faculty", "PROFILE"), ("course", "PROFILE"),
    ("scientific_interests", "PROFILE"), ("experience", "PROFILE"),
    ("foreign_languages", "PROFILE"), ("hobbies", "PROFILE"),
    ("source", "PROFILE"),
    ("motivation", "MOTIVATION"), ("expectations", "MOTIVATION"),
    ("desired_direction", "ROLE"),
    ("availability_hours_month", "TIME"), ("availability_schedule_type", "TIME"),
    ("other_commitments", "TIME"),
    ("case_response", "CASE"),
)
ALLOWED = set(ApplicationInput.model_fields) - {"application_id"}


def _paths() -> tuple[Path, Path]:
    base = Path(os.getenv("GIM_SESSIONS_DB_PATH", str(ROOT / "data" / "sessions.sqlite3")))
    pii = Path(os.getenv("GIM_PII_DB_PATH", str(ROOT / "data" / "session-contacts.sqlite3")))
    return base, pii


class _PostgresConnection:
    """Keep the session SQL shared while psycopg uses its native placeholders."""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, query: str, params=()):
        return self.raw.execute(query.replace("?", "%s"), params)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


@contextmanager
def _connection():
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("GIM_DATABASE_URL", "")
    if database_url:
        import psycopg
        from psycopg.rows import dict_row

        con = _PostgresConnection(psycopg.connect(
            database_url, connect_timeout=5, row_factory=dict_row))
        con.execute("CREATE SCHEMA IF NOT EXISTS pii")
    else:
        main, pii = _paths()
        main.parent.mkdir(parents=True, exist_ok=True)
        pii.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(main, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("ATTACH DATABASE ? AS pii", (str(pii),))
    con.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, state TEXT NOT NULL,
        questionnaire_version TEXT NOT NULL, answer_revision INTEGER NOT NULL DEFAULT 0,
        consent_at TEXT, active_run_id TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS answers (
        session_id TEXT NOT NULL, question_id TEXT NOT NULL,
        answer_id TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL,
        confirmed_at TEXT NOT NULL, PRIMARY KEY(session_id, question_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS pii.contacts (
        session_id TEXT NOT NULL, question_id TEXT NOT NULL,
        answer_id TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL,
        PRIMARY KEY(session_id, question_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS request_keys (
        session_id TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL,
        response TEXT NOT NULL, PRIMARY KEY(session_id, key))""")
    con.execute("""CREATE TABLE IF NOT EXISTS clarification_counts (
        session_id TEXT NOT NULL, question_id TEXT NOT NULL,
        attempts INTEGER NOT NULL, PRIMARY KEY(session_id, question_id))""")
    con.execute("""CREATE TABLE IF NOT EXISTS session_runs (
        run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, answer_revision INTEGER NOT NULL,
        created_at TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
        parent_run_id TEXT, error TEXT)""")
    # Earlier pilot databases enforced one run per answer revision. Coordinator
    # corrections need an immutable child run at the same revision.
    if not database_url:
        indexes = con.execute("PRAGMA index_list('session_runs')").fetchall()
        if any(index[2] and [part[2] for part in con.execute(f"PRAGMA index_info('{index[1]}')")]
               == ["session_id", "answer_revision"] for index in indexes):
            con.execute("""CREATE TABLE session_runs_v2 (
                run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, answer_revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                parent_run_id TEXT, error TEXT)""")
            con.execute("INSERT INTO session_runs_v2 SELECT * FROM session_runs")
            con.execute("DROP TABLE session_runs")
            con.execute("ALTER TABLE session_runs_v2 RENAME TO session_runs")
    con.execute("""CREATE TABLE IF NOT EXISTS run_inputs (
        run_id TEXT PRIMARY KEY, answers TEXT NOT NULL, answer_ids TEXT NOT NULL)""")
    con.commit()
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


class AnswerInput(BaseModel):
    answer_id: str = Field(min_length=1, max_length=90)
    question_id: str = Field(min_length=1, max_length=90)
    text: str = Field(max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class SubmitInput(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=120)


class ReviewInput(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    criteria: list[dict] = Field(min_length=6, max_length=6)


def _coordinator_auth(authorization: str | None) -> None:
    load_dotenv(ROOT / ".env")
    expected = os.getenv("GIM_COORDINATOR_TOKEN", "")
    if not expected or expected.startswith("replace-"):
        raise HTTPException(status_code=503, detail="coordinator_token_not_configured")
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="coordinator_auth_required")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session(con, session_id: str):
    row = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    return row


def _values(con, session_id: str) -> tuple[dict[str, str], dict[str, str]]:
    values = {}
    ids = {}
    for table in ("answers", "pii.contacts"):
        for row in con.execute(f"SELECT question_id, answer_id, text FROM {table} WHERE session_id=?", (session_id,)):
            values[row["question_id"]] = row["text"]
            ids[row["question_id"]] = row["answer_id"]
    return values, ids


def _next_question(values: dict[str, str]) -> tuple[str, str] | None:
    for field, state in REQUIRED:
        if field in {"university", "faculty", "course"} and values.get("status") != "student":
            continue
        if field == "university_other" and values.get("university") != "Другое":
            continue
        if field == "other_commitments" and values.get("status") != "working":
            continue
        if field == "availability_hours_month":
            present = field in values and values[field].strip() != ""
        else:
            present = bool(values.get(field, "").strip())
        if not present:
            return field, state
    return None


def _request_response(con, session_id: str, key: str, payload: dict) -> dict | None:
    row = con.execute("SELECT payload, response FROM request_keys WHERE session_id=? AND key=?",
                      (session_id, _key_digest(key))).fetchone()
    if row is None:
        return None
    if row["payload"] != _payload_digest(payload):
        raise HTTPException(status_code=409, detail="idempotency_key_reused_with_different_payload")
    return json.loads(row["response"])


def _save_request(con, session_id: str, key: str, payload: dict, response: dict) -> None:
    con.execute("INSERT INTO request_keys(session_id, key, payload, response) VALUES(?,?,?,?)",
                (session_id, _key_digest(key), _payload_digest(payload),
                 json.dumps(response, ensure_ascii=False)))


def _payload_digest(payload: dict) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _key_digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@router.post("/sessions", status_code=201)
def create_session():
    session_id = uuid.uuid4().hex
    with _connection() as con:
        con.execute("INSERT INTO sessions(id, created_at, state, questionnaire_version) VALUES(?,?,?,?)",
                    (session_id, _now(), "CONSENT", QUESTIONNAIRE_VERSION))
    return {"session_id": session_id, "state": "CONSENT",
            "questionnaire_version": QUESTIONNAIRE_VERSION, "answer_revision": 0,
            "next_question": "consent_personal_data"}


@router.post("/sessions/{session_id}/answers")
def save_answer(session_id: str, data: AnswerInput):
    if data.question_id not in ALLOWED:
        raise HTTPException(status_code=422, detail="unknown_question_id")
    payload = data.model_dump(exclude={"idempotency_key"})
    with _connection() as con:
        session = _session(con, session_id)
        previous = _request_response(con, session_id, data.idempotency_key, payload)
        if previous is not None:
            return previous
        if session["state"] == "CLOSED":
            raise HTTPException(status_code=409, detail="session_closed")
        if data.question_id != "consent_personal_data" and session["consent_at"] is None:
            raise HTTPException(status_code=409, detail="consent_required_first")
        if data.question_id == "consent_personal_data" and data.text.strip().lower() != "true":
            con.execute("DELETE FROM answers WHERE session_id=?", (session_id,))
            con.execute("DELETE FROM pii.contacts WHERE session_id=?", (session_id,))
            con.execute("DELETE FROM run_inputs WHERE run_id IN (SELECT run_id FROM session_runs WHERE session_id=?)",
                        (session_id,))
            con.execute("DELETE FROM session_runs WHERE session_id=?", (session_id,))
            con.execute("DELETE FROM clarification_counts WHERE session_id=?", (session_id,))
            con.execute("DELETE FROM request_keys WHERE session_id=?", (session_id,))
            con.execute("UPDATE sessions SET state='CLOSED', consent_at=NULL, active_run_id=NULL WHERE id=?", (session_id,))
            response = {"session_id": session_id, "state": "CLOSED", "answer_revision": session["answer_revision"]}
            _save_request(con, session_id, data.idempotency_key, payload, response)
            return response

        existing_id = con.execute("SELECT question_id FROM answers WHERE session_id=? AND answer_id=? UNION ALL "
                                  "SELECT question_id FROM pii.contacts WHERE session_id=? AND answer_id=?",
                                  (session_id, data.answer_id, session_id, data.answer_id)).fetchone()
        if existing_id and existing_id["question_id"] != data.question_id:
            raise HTTPException(status_code=409, detail="answer_id_already_used")

        table = "pii.contacts" if data.question_id in CONTACT_FIELDS else "answers"
        old = con.execute(f"SELECT text, answer_id FROM {table} WHERE session_id=? AND question_id=?",
                          (session_id, data.question_id)).fetchone()
        changed = old is None or old["text"] != data.text
        revision = session["answer_revision"] + int(changed)
        if changed:
            if table == "answers":
                con.execute("""INSERT INTO answers(session_id, question_id, answer_id, text, revision, confirmed_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(session_id, question_id) DO UPDATE SET
                    answer_id=excluded.answer_id, text=excluded.text, revision=excluded.revision,
                    confirmed_at=excluded.confirmed_at""",
                    (session_id, data.question_id, data.answer_id, data.text, revision, _now()))
            else:
                con.execute("""INSERT INTO pii.contacts(session_id, question_id, answer_id, text, revision)
                    VALUES(?,?,?,?,?) ON CONFLICT(session_id, question_id) DO UPDATE SET
                    answer_id=excluded.answer_id, text=excluded.text, revision=excluded.revision""",
                    (session_id, data.question_id, data.answer_id, data.text, revision))
        if data.question_id == "consent_personal_data":
            con.execute("UPDATE sessions SET consent_at=COALESCE(consent_at, ?) WHERE id=?", (_now(), session_id))
        values, _ = _values(con, session_id)
        next_item = _next_question(values)
        state = next_item[1] if next_item else "REVIEW"
        con.execute("UPDATE sessions SET state=?, answer_revision=?, active_run_id=NULL WHERE id=?",
                    (state, revision, session_id))
        response = {"session_id": session_id, "state": state,
                    "answer_revision": revision,
                    "next_question": next_item[0] if next_item else None}
        _save_request(con, session_id, data.idempotency_key, payload, response)
    return response


def _application(values: dict[str, str], session_id: str) -> ApplicationInput:
    parsed: dict = {}
    for key, value in values.items():
        if key in {"foreign_languages", "availability_days"}:
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError:
                parsed[key] = []
        elif key == "followup_answers":
            try:
                parsed[key] = json.loads(value)
            except json.JSONDecodeError:
                parsed[key] = {}
        elif key == "availability_hours_month":
            parsed[key] = int(value)
        elif key in {"age_over_18", "consent_personal_data"}:
            parsed[key] = value.strip().lower() == "true"
        else:
            parsed[key] = value
    parsed["application_id"] = session_id
    if "age_over_18" not in parsed:
        try:
            born = date.fromisoformat(parsed["birth_date"])
            today = date.today()
            parsed["age_over_18"] = today.year - born.year - ((today.month, today.day) < (born.month, born.day)) >= 18
        except (KeyError, ValueError):
            parsed["age_over_18"] = False
    return ApplicationInput(**parsed)


def _evaluate_session(session_id: str, run_id: str, revision: int,
                      values: dict[str, str], ids: dict[str, str]) -> None:
    try:
        application = _application(values, session_id)
        with _connection() as con:
            peers = _peer_results(con, session_id)
        if os.getenv("GIM_EVALUATION_MODE") == "rag_llm":
            gate = check_eligibility(application)
            if gate["status"] in {"ineligible", "no_consent", "age_unverified"}:
                result = evaluate_application(application)
            else:
                from backend.agent_workflow import run_assessment
                result = run_assessment(application, answer_ids=ids,
                                        eligibility=gate, peer_results=peers)
        else:
            result = evaluate_application(application)
        from backend.decision import route_candidate
        if "decision" not in result:
            result["decision"] = route_candidate(result, peers)
        if any(key.startswith("assessment_followup_") and str(value).strip()
               for key, value in application.followup_answers.items()) and result["decision"]["needs_additional_questions"]:
            result["decision"].update({
                "status": "coordinator_review_after_followup", "may_proceed": None,
                "needs_additional_questions": False,
                "reason": "Дополнительные ответы получены; координатору нужно проверить обновлённую оценку.",
            })
        result["additional_questions"] = (result.get("interview_questions", [])[:3]
                                          if result["decision"]["needs_additional_questions"] else [])
        status = "NEEDS_REVIEW" if result.get("score") is None else (
            "FOLLOWUP_REQUIRED" if result["decision"]["needs_additional_questions"] else "SCORED")
        if result.get("score_status") == "ineligible":
            status = "INELIGIBLE"
        with _connection() as con:
            con.execute("UPDATE session_runs SET status=?, result=? WHERE run_id=?",
                        (status, json.dumps(result, ensure_ascii=False), run_id))
            current = _session(con, session_id)
            if current["answer_revision"] == revision:
                con.execute("UPDATE sessions SET state=? WHERE id=?", (status, session_id))
    except Exception as exc:
        with _connection() as con:
            con.execute("UPDATE session_runs SET status='ERROR', error=? WHERE run_id=?",
                        (exc.code if isinstance(exc, EvaluationError) else type(exc).__name__, run_id))
            current = _session(con, session_id)
            if current["answer_revision"] == revision:
                con.execute("UPDATE sessions SET state='NEEDS_REVIEW' WHERE id=?", (session_id,))


def _peer_results(con, session_id: str) -> list[dict]:
    rows = con.execute("""SELECT r.result FROM sessions s JOIN session_runs r ON r.run_id=s.active_run_id
        WHERE s.id<>? AND r.result IS NOT NULL""", (session_id,)).fetchall()
    return [json.loads(row["result"]) for row in rows]


@router.post("/sessions/{session_id}/submit", status_code=202)
def submit_session(session_id: str, data: SubmitInput, background: BackgroundTasks):
    payload = data.model_dump(exclude={"idempotency_key"})
    with _connection() as con:
        session = _session(con, session_id)
        previous = _request_response(con, session_id, data.idempotency_key, payload)
        if previous is not None:
            return previous
        if session["consent_at"] is None or session["state"] == "CLOSED":
            raise HTTPException(status_code=409, detail="consent_required")
        values, ids = _values(con, session_id)
        next_item = _next_question(values)
        if next_item:
            question_id = next_item[0]
            old = con.execute("SELECT attempts FROM clarification_counts WHERE session_id=? AND question_id=?",
                              (session_id, question_id)).fetchone()
            attempts = min(2, (old["attempts"] if old else 0) + 1)
            con.execute("""INSERT INTO clarification_counts(session_id,question_id,attempts) VALUES(?,?,?)
                ON CONFLICT(session_id,question_id) DO UPDATE SET attempts=excluded.attempts""",
                (session_id, question_id, attempts))
            state = "NEEDS_REVIEW" if attempts == 2 else next_item[1]
            con.execute("UPDATE sessions SET state=? WHERE id=?", (state, session_id))
            response = {"session_id": session_id, "status": "NEEDS_REVIEW" if attempts == 2 else "INCOMPLETE",
                        "next_question": question_id, "clarification_attempts": attempts,
                        "answer_revision": session["answer_revision"]}
            _save_request(con, session_id, data.idempotency_key, payload, response)
            return response
        try:
            application = _application(values, session_id)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid_answer_values") from exc
        existing = con.execute("SELECT run_id,status FROM session_runs WHERE run_id=? AND answer_revision=?",
                               (session["active_run_id"], session["answer_revision"])).fetchone()
        if existing:
            response = {"session_id": session_id, "run_id": existing["run_id"],
                        "status": existing["status"], "answer_revision": session["answer_revision"]}
        else:
            run_id = uuid.uuid4().hex
            parent = con.execute("SELECT run_id FROM session_runs WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
                                 (session_id,)).fetchone()
            con.execute("""INSERT INTO session_runs(run_id,session_id,answer_revision,created_at,status,parent_run_id)
                VALUES(?,?,?,?,?,?)""",
                (run_id, session_id, session["answer_revision"], _now(), "EVALUATING",
                 parent["run_id"] if parent else None))
            from backend.scoring import _answers
            model_answers = _answers(application)
            model_ids = {ids.get(key, key): key for key in model_answers}
            con.execute("INSERT INTO run_inputs(run_id,answers,answer_ids) VALUES(?,?,?)",
                        (run_id, json.dumps({ids.get(key, key): value for key, value in model_answers.items()}, ensure_ascii=False),
                         json.dumps(model_ids, ensure_ascii=False)))
            con.execute("UPDATE sessions SET state='EVALUATE', active_run_id=? WHERE id=?", (run_id, session_id))
            response = {"session_id": session_id, "run_id": run_id,
                        "status": "EVALUATING", "answer_revision": session["answer_revision"]}
            background.add_task(_evaluate_session, session_id, run_id, session["answer_revision"], values, ids)
        _save_request(con, session_id, data.idempotency_key, payload, response)
    return response


@router.get("/sessions/{session_id}/result")
def session_result(session_id: str):
    with _connection() as con:
        session = _session(con, session_id)
        run = con.execute("SELECT * FROM session_runs WHERE run_id=?", (session["active_run_id"],)).fetchone()
        if run is None:
            return {"session_id": session_id, "state": session["state"],
                    "answer_revision": session["answer_revision"], "score": None,
                    "next_question": (_next_question(_values(con, session_id)[0]) or (None,))[0]}
        return {"session_id": session_id, "state": session["state"],
                "run_id": run["run_id"], "status": run["status"],
                "answer_revision": run["answer_revision"],
                "evaluation": json.loads(run["result"]) if run["result"] else None,
                "error": run["error"]}


@router.get("/admin/runs")
def list_runs(authorization: str | None = Header(default=None)):
    _coordinator_auth(authorization)
    with _connection() as con:
        rows = con.execute("""SELECT r.*, s.active_run_id FROM session_runs r
            JOIN sessions s ON s.id=r.session_id ORDER BY r.created_at DESC""").fetchall()
    return {"runs": [{
        "run_id": row["run_id"], "session_id": row["session_id"],
        "created_at": row["created_at"], "answer_revision": row["answer_revision"],
        "status": row["status"], "active": row["run_id"] == row["active_run_id"],
        "score": (json.loads(row["result"]).get("score") if row["result"] else None),
        "parent_run_id": row["parent_run_id"],
    } for row in rows]}


@router.get("/admin/analytics")
def cohort_analytics(authorization: str | None = Header(default=None)):
    """Aggregate only current, non-withdrawn applications."""
    _coordinator_auth(authorization)
    with _connection() as con:
        rows = con.execute("""SELECT s.id, s.state, r.status, r.result FROM sessions s
            LEFT JOIN session_runs r ON r.run_id=s.active_run_id WHERE s.state<>'CLOSED'""").fetchall()
        roles: dict[str, int] = {}
        statuses: dict[str, int] = {}
        decisions: dict[str, int] = {}
        criterion_grades: dict[str, list[int]] = {}
        scores: list[float] = []
        for row in rows:
            values, _ = _values(con, row["id"])
            role = values.get("desired_direction") or "не указано"
            roles[role] = roles.get(role, 0) + 1
            status = row["status"] or row["state"]
            statuses[status] = statuses.get(status, 0) + 1
            if not row["result"]:
                continue
            result = json.loads(row["result"])
            decision = result.get("decision", {}).get("status")
            if decision:
                decisions[decision] = decisions.get(decision, 0) + 1
            if isinstance(result.get("score"), (int, float)):
                scores.append(float(result["score"]))
            for item in result.get("criteria", []):
                if isinstance(item.get("grade"), int) and item.get("id"):
                    criterion_grades.setdefault(item["id"], []).append(item["grade"])
    return {
        "total": len(rows), "scored": len(scores),
        "mean_score": round(sum(scores) / len(scores), 2) if scores else None,
        "by_status": statuses, "by_direction": roles, "by_decision": decisions,
        "mean_criteria": {key: round(sum(grades) / len(grades), 2)
                          for key, grades in criterion_grades.items()},
    }


@router.get("/admin/export.csv")
def export_session_csv(authorization: str | None = Header(default=None)):
    _coordinator_auth(authorization)
    from backend.app import safe_csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["session_id", "created_at", "state", "name", "email", "vk_url",
                     "birth_date", "status", "desired_direction", "motivation", "expectations",
                     "experience", "availability_hours_month", "score", "decision",
                     "candidate_summary", "program_fit", "interview_questions"])
    with _connection() as con:
        rows = con.execute("""SELECT s.id, s.created_at, s.state, r.result FROM sessions s
            LEFT JOIN session_runs r ON r.run_id=s.active_run_id ORDER BY s.created_at""").fetchall()
        for row in rows:
            if row["state"] == "CLOSED":
                continue
            values, _ = _values(con, row["id"])
            result = json.loads(row["result"]) if row["result"] else {}
            decision = result.get("decision", {})
            writer.writerow([safe_csv(value) for value in (
                row["id"], row["created_at"], row["state"], values.get("applicant_name", ""),
                values.get("email", ""), values.get("vk_url", ""), values.get("birth_date", ""),
                values.get("status", ""), values.get("desired_direction", ""),
                values.get("motivation", ""), values.get("expectations", ""),
                values.get("experience", ""), values.get("availability_hours_month", ""),
                result.get("score"), decision.get("status", ""), result.get("summary", ""),
                result.get("fit_for_candidate", ""), "; ".join(result.get("interview_questions", [])),
            )])
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="gim-session-results.csv"'})


@router.get("/admin/runs/{run_id}")
def run_detail(run_id: str, authorization: str | None = Header(default=None)):
    _coordinator_auth(authorization)
    with _connection() as con:
        row = con.execute("""SELECT r.*, s.active_run_id FROM session_runs r
            JOIN sessions s ON s.id=r.session_id WHERE r.run_id=?""", (run_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        snapshot = con.execute("SELECT answers, answer_ids FROM run_inputs WHERE run_id=?", (run_id,)).fetchone()
        # Contact data is kept outside the model snapshot. Show the current
        # verified values only for the active run, never mislabel old revisions.
        current_values, _ = _values(con, row["session_id"]) if row["run_id"] == row["active_run_id"] else ({}, {})
    return {
        "run_id": row["run_id"], "session_id": row["session_id"],
        "created_at": row["created_at"], "status": row["status"],
        "active": row["run_id"] == row["active_run_id"],
        "answer_revision": row["answer_revision"], "parent_run_id": row["parent_run_id"],
        "evaluation": json.loads(row["result"]) if row["result"] else None,
        "error": row["error"],
        "answers": json.loads(snapshot["answers"]) if snapshot else {},
        "question_ids": json.loads(snapshot["answer_ids"]) if snapshot else {},
        "submitted_answers": current_values if row["run_id"] == row["active_run_id"] else None,
    }


@router.patch("/admin/runs/{run_id}", status_code=201)
def review_run(run_id: str, data: ReviewInput, authorization: str | None = Header(default=None)):
    _coordinator_auth(authorization)
    from backend.evaluator import EvaluationError
    from backend.scoring import RUBRIC, RUBRIC_VERSION, _validate, score_criteria
    with _connection() as con:
        row = con.execute("SELECT * FROM session_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        session = _session(con, row["session_id"])
        if session["active_run_id"] != run_id or session["answer_revision"] != row["answer_revision"]:
            raise HTTPException(status_code=409, detail="review_requires_current_run")
        if row["status"] == "EVALUATING":
            raise HTTPException(status_code=409, detail="evaluation_still_running")
        current_values, _ = _values(con, row["session_id"])
        if check_eligibility(_application(current_values, row["session_id"]))["status"] != "eligible":
            raise HTTPException(status_code=409, detail="eligibility_must_be_resolved_first")
        snapshot = con.execute("SELECT * FROM run_inputs WHERE run_id=?", (run_id,)).fetchone()
        if snapshot is None:
            raise HTTPException(status_code=409, detail="run_inputs_unavailable")
        answers = json.loads(snapshot["answers"])
        question_ids = json.loads(snapshot["answer_ids"])
        case_id = next((aid for aid, qid in question_ids.items() if qid == "case_response"), "case_response")
        try:
            criteria = _validate(json.dumps({"rubric_version": RUBRIC_VERSION,
                "criteria": data.criteria, "flags": []}, ensure_ascii=False), answers,
                responsibility_id=case_id)
        except EvaluationError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from None
        numerator, score = score_criteria(criteria)
        previous = json.loads(row["result"]) if row["result"] else {}
        if previous.get("rubric_version") not in (None, RUBRIC_VERSION):
            raise HTTPException(status_code=409, detail="rubric_version_changed")
        result = {**previous,
            "score": score, "score_numerator": numerator,
            "score_status": "pilot_scored" if score is not None else "needs_review",
            "evaluation_mode": "coordinator_review", "rubric_version": RUBRIC_VERSION,
            "rubric_snapshot": [{"id": cid, "label": label, "weight_percent": weight, "anchors": anchors}
                                for cid, label, weight, anchors in RUBRIC],
            "criteria": [{**item, "label": label, "weight_percent": weight,
                          "reviewer_override": True}
                         for item, (_, label, weight, _) in zip(criteria, RUBRIC)],
            "review_reason": data.reason.strip(), "reviewed_at": _now(),
            "parent_run_id": run_id,
        }
        child_id = uuid.uuid4().hex
        from backend.decision import route_candidate
        result["decision"] = route_candidate(result, _peer_results(con, row["session_id"]))
        result["additional_questions"] = (result.get("interview_questions", [])[:3]
                                          if result["decision"]["needs_additional_questions"] else [])
        status = "NEEDS_REVIEW" if score is None else (
            "FOLLOWUP_REQUIRED" if result["decision"]["needs_additional_questions"] else "SCORED")
        con.execute("""INSERT INTO session_runs(run_id,session_id,answer_revision,created_at,status,result,parent_run_id)
            VALUES(?,?,?,?,?,?,?)""", (child_id, row["session_id"], row["answer_revision"],
            _now(), status, json.dumps(result, ensure_ascii=False), run_id))
        con.execute("INSERT INTO run_inputs(run_id,answers,answer_ids) VALUES(?,?,?)",
                    (child_id, snapshot["answers"], snapshot["answer_ids"]))
        con.execute("UPDATE sessions SET state=?, active_run_id=? WHERE id=?",
                    (status, child_id, row["session_id"]))
    return {"run_id": child_id, "parent_run_id": run_id, "session_id": row["session_id"],
            "status": status, "evaluation": result}
