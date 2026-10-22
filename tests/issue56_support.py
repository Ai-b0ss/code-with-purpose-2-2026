"""Shared offline helpers for the issue #56 regressions. No network, no real model."""
import json
from pathlib import Path

from backend.evaluator import CRITERION_IDS, evaluate
from test_evaluator import FakeAdapter, response
from test_smoke import applicant

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "evaluator"


def load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def scripted(data, levels):
    """A rubric-compliant model answer for `data` with exact quotes and chosen levels.

    Unknown (None) criteria carry no references; every known one cites a real,
    non-empty field of this very applicant.
    """
    raw = response(data)
    sources = {
        "motivation": ("motivation", data.motivation),
        "experience": ("experience", data.experience),
        "availability_hours_month": ("availability_hours_month", str(data.availability_hours_month)),
    }
    cited = {
        "motivation": "motivation",
        "skills": "experience" if data.experience.strip() else "motivation",
        "availability": "availability_hours_month",
        "form_and_contact": "motivation",
        "logistics": "availability_hours_month",
    }
    needed = {cited[name] for name in CRITERION_IDS if levels.get(name) is not None}
    raw["evidence"] = [{"question_id": q, "reason": sources[q][1]} for q in sources if q in needed]
    if not raw["evidence"]:  # schema requires at least one quote
        raw["evidence"] = [{"question_id": "motivation", "reason": data.motivation}]
    raw["scorecard"] = [
        {"criterion": name, "level": levels.get(name),
         "rationale": "Оценка по прямым ответам анкеты, проверяется куратором.",
         "evidence_question_ids": [cited[name]] if levels.get(name) is not None else []}
        for name in CRITERION_IDS
    ]
    return raw


def run(data, raw=None, levels=None):
    """Evaluate through the real pipeline with a recording fake adapter."""
    if raw is None:
        raw = scripted(data, levels or {name: 2 for name in CRITERION_IDS})
    adapter = FakeAdapter(json.dumps(raw, ensure_ascii=False))
    return evaluate(data, adapter=adapter), adapter


def sent_answers(adapter, call=0):
    return json.loads(adapter.calls[call]["user"])["answers"]


def differing_keys(first, second):
    return {key for key in set(first) | set(second) if first.get(key) != second.get(key)}
