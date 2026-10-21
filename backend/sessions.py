"""Versioned application sessions following the PDF dialogue contract."""
from __future__ import annotations

import json
import hashlib
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic import ValidationError

from backend.app import ApplicationInput, ROOT, evaluate_application
from backend.eligibility import check_eligibility

router = APIRouter()
QUESTIONNAIRE_VERSION = "gim-questionnaire-2026-v1"
CONTACT_FIELDS = {"applicant_name", "email", "vk_url", "birth_date"}
REQUIRED = (
    ("consent_personal_data", "CONSENT"),
    ("applicant_name", "CONTACT"), ("email", "CONTACT"),
    ("vk_url", "CONTACT"), ("birth_date", "CONTACT"),
    ("motivation", "MOTIVATION"), ("expectations", "MOTIVATION"),
    ("desired_direction", "ROLE"),
    ("availability_hours_month", "TIME"),
    ("case_response", "CASE"),
)
ALLOWED = set(ApplicationInput.model_fields) - {"application_id"}


def _paths() -> tuple[Path, Path]:
    base = Path(os.getenv("GIM_SESSIONS_DB_PATH", str(ROOT / "data" / "sessions.sqlite3")))
    pii = Path(os.getenv("GIM_PII_DB_PATH", str(ROOT / "data" / "session-contacts.sqlite3")))
    return base, pii


@contextmanager
def _connection():
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
        if os.getenv("GIM_EVALUATION_MODE") == "rag_llm":
            from backend.scoring import assess
            gate = check_eligibility(application)
            if gate["status"] in {"ineligible", "age_unverified"}:
                result = evaluate_application(application)
            else:
                result = assess(application, answer_ids=ids)
                result["eligibility"] = gate
        else:
            result = evaluate_application(application)
        status = "NEEDS_REVIEW" if result.get("score") is None else "SCORED"
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
                        (type(exc).__name__, run_id))
            current = _session(con, session_id)
            if current["answer_revision"] == revision:
                con.execute("UPDATE sessions SET state='NEEDS_REVIEW' WHERE id=?", (session_id,))


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
