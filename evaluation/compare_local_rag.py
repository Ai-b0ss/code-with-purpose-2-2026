"""Paired local-model ablation using the production scoring adapter and Neo4j retriever.

Run after the isolated Ollama and Neo4j services are ready. All candidates are
synthetic. Each case is assessed twice with identical model, rubric and answers;
only the retrieved knowledge changes. This is an evaluation runner, not a RAG
implementation.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.app import ApplicationInput
from backend.evaluator import ChatCompletionsAdapter, EvaluationError
from backend.knowledge import retrieve
from backend.scoring import assess

ROOT = Path(__file__).resolve().parents[1]


class CaptureAdapter:
    def __init__(self, delegate):
        self.delegate = delegate
        self.model = delegate.model
        self.responses = []

    def complete(self, *, system, user, schema):
        response = self.delegate.complete(system=system, user=user, schema=schema)
        self.responses.append(response)
        return response


CASES = [
    {"case_id": "working_project", "direction": "project", "expected_doc_ids": [
        "case:admission-flow", "condition:employment"], "data": {
        "applicant_name": "Synthetic Working Candidate", "age_over_18": True,
        "consent_personal_data": True, "status": "working",
        "motivation": "Хочу разработать музейный квест и помогать посетителям изучать историю.",
        "experience": "Организовал две образовательные игры для школьников.",
        "expectations": "Хочу участвовать в проектной работе и узнать, как музей делает программы.",
        "desired_direction": "project", "availability_hours_month": 8,
        "availability_schedule_type": "weekends", "other_commitments": "Работаю по будням до 18:00.",
        "case_response": "Если не смогу прийти, заранее предупрежу куратора и согласую замену.",
    }},
    {"case_id": "student_collections", "direction": "collections", "expected_doc_ids": [
        "case:admission-flow", "program:gim"], "data": {
        "applicant_name": "Synthetic Student Candidate", "age_over_18": True,
        "consent_personal_data": True, "status": "student",
        "motivation": "Хочу изучать музейные коллекции и помогать описывать предметы.",
        "experience": "Работал с каталогом в университетском проекте.",
        "expectations": "Надеюсь сразу работать с фондами каждый день.",
        "desired_direction": "collections", "availability_hours_month": 12,
        "availability_schedule_type": "weekdays", "other_commitments": "Учёба по утрам.",
        "case_response": "При конфликте с учёбой заранее сообщу куратору и уточню другой день.",
    }},
]


def main() -> None:
    adapter = ChatCompletionsAdapter(
        endpoint=os.getenv("GIM_LLM_ENDPOINT", "http://127.0.0.1:11434/api/chat"),
        model=os.getenv("GIM_LLM_MODEL", "qwen3:14b"),
        timeout=300,
    )
    observations = []
    for case in CASES:
        application = ApplicationInput(**case["data"])
        context = retrieve(case["direction"],
                           f"{case['direction']} {application.motivation} работа график собеседование")[:8]
        for condition, knowledge in (("without_rag", []), ("with_rag", context)):
            capture = CaptureAdapter(adapter)
            record = {"case_id": case["case_id"], "condition": condition,
                      "model": adapter.model, "retrieved_doc_ids": [item["doc_id"] for item in knowledge],
                      "expected_doc_ids": case["expected_doc_ids"]}
            started = time.monotonic()
            try:
                result = assess(application, adapter=capture, knowledge=knowledge)
                record.update({"valid": True, "score": result["score"],
                               "criteria": result["criteria"], "analysis": result["analysis"],
                               "cited_doc_ids": result["analysis"]["source_doc_ids"]})
            except EvaluationError as exc:
                record.update({"valid": False, "error": exc.code,
                               "raw_model_responses": capture.responses})
            record["duration_seconds"] = round(time.monotonic() - started, 2)
            observations.append(record)
            print(f"{case['case_id']} {condition}: "
                  f"{'valid' if record['valid'] else record['error']}", flush=True)
    summary = {}
    for condition in ("without_rag", "with_rag"):
        records = [item for item in observations if item["condition"] == condition]
        valid = [item for item in records if item["valid"]]
        summary[condition] = {
            "cases": len(records), "schema_and_evidence_valid": len(valid),
            "full_score_available": sum(item["score"] is not None for item in valid),
            "source_citations": sum(len(item["cited_doc_ids"]) for item in valid),
            "errors": [item.get("error") for item in records if not item["valid"]],
        }
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "method": "paired same-model same-rubric same-answers; only Neo4j context differs",
              "model": adapter.model, "summary": summary, "cases": observations}
    target = ROOT / "evaluation" / "local-rag-comparison.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
