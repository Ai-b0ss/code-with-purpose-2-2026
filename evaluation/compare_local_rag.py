"""Paired local-model ablation using the production scoring adapter and Neo4j retriever.

Run after the isolated Ollama and Neo4j services are ready. All candidates are
synthetic. Each case is assessed twice with identical model, rubric and answers;
only the retrieved knowledge changes. This is an evaluation runner, not a RAG
implementation.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.app import ApplicationInput
from backend.evaluator import ChatCompletionsAdapter, EvaluationError
from backend.knowledge import retrieve
from backend.scoring import assess

ROOT = Path(__file__).resolve().parents[1]
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
        endpoint=os.getenv("GIM_LLM_ENDPOINT", "http://127.0.0.1:11434/v1/chat/completions"),
        model=os.getenv("GIM_LLM_MODEL", "qwen3:4b-instruct"),
        timeout=300,
    )
    observations = []
    for case in CASES:
        application = ApplicationInput(**case["data"])
        context = retrieve(case["direction"],
                           f"{case['direction']} {application.motivation} работа график собеседование")[:8]
        for condition, knowledge in (("without_rag", []), ("with_rag", context)):
            record = {"case_id": case["case_id"], "condition": condition,
                      "model": adapter.model, "retrieved_doc_ids": [item["doc_id"] for item in knowledge],
                      "expected_doc_ids": case["expected_doc_ids"]}
            try:
                result = assess(application, adapter=adapter, knowledge=knowledge)
                record.update({"valid": True, "score": result["score"],
                               "criteria": result["criteria"], "analysis": result["analysis"],
                               "cited_doc_ids": result["analysis"]["source_doc_ids"]})
            except EvaluationError as exc:
                record.update({"valid": False, "error": exc.code})
            observations.append(record)
            print(f"{case['case_id']} {condition}: "
                  f"{'valid' if record['valid'] else record['error']}", flush=True)
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "method": "paired same-model same-rubric same-answers; only Neo4j context differs",
              "model": adapter.model, "cases": observations}
    target = ROOT / "evaluation" / "local-rag-comparison.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
