"""Check the saved, real development model response against production validators."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from backend.app import app
from backend.scoring import _validate, _validate_analysis, score_criteria


ROOT = Path(__file__).resolve().parent.parent / "evaluation"


def test_development_model_response_is_accepted_without_invented_score():
    request = json.loads((ROOT / "dev-probe-request.json").read_text(encoding="utf-8"))
    knowledge = json.loads((ROOT / "dev-rag-context.json").read_text(encoding="utf-8"))
    response = (ROOT / "dev-model-response.json").read_text(encoding="utf-8")

    criteria = _validate(response, request["answers"])
    analysis = _validate_analysis(response, knowledge)

    assert len(criteria) == 6
    assert [item["id"] for item in criteria if item["grade"] is None] == ["availability"]
    assert score_criteria(criteria) == (None, None)
    assert set(analysis["source_doc_ids"]) <= {item["doc_id"] for item in knowledge}


def test_saved_model_response_through_fake_key_and_full_session(monkeypatch, tmp_path):
    request = json.loads((ROOT / "dev-probe-request.json").read_text(encoding="utf-8"))
    knowledge = json.loads((ROOT / "dev-rag-context.json").read_text(encoding="utf-8"))
    response = (ROOT / "dev-model-response.json").read_text(encoding="utf-8")
    calls = []

    class ModelEndpoint(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"authorization": self.headers.get("Authorization"), "body": body})
            payload = json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "content": response}}]}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelEndpoint)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        monkeypatch.setenv("GIM_EVALUATION_MODE", "rag_llm")
        monkeypatch.setenv("GIM_SESSIONS_DB_PATH", str(tmp_path / "sessions.sqlite3"))
        monkeypatch.setenv("GIM_PII_DB_PATH", str(tmp_path / "contacts.sqlite3"))
        monkeypatch.setenv("GIM_LLM_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
        monkeypatch.setenv("GIM_LLM_MODEL", "development-model")
        monkeypatch.setenv("GIM_LLM_API_KEY", "fake-development-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        if os.getenv("GIM_TEST_LIVE_GRAPH") != "1":
            monkeypatch.setattr("backend.scoring.retrieve", lambda role, query: knowledge)

        client = TestClient(app)
        session_id = client.post("/api/v1/sessions").json()["session_id"]
        fields = {
            "consent_personal_data": "true", "applicant_name": "Synthetic Candidate",
            "email": "synthetic@example.test", "vk_url": "https://vk.com/synthetic",
            "birth_date": "1990-01-01", "scientific_interests": "История",
            "foreign_languages": "[]", "hobbies": "Чтение", "source": "Сайт музея",
            **{key: value for key, value in request["answers"].items() if not key.startswith("followup.")},
            "followup_answers": json.dumps({"employment_schedule": request["answers"]["followup.employment_schedule"]}, ensure_ascii=False),
        }
        for key, value in fields.items():
            saved = client.post(f"/api/v1/sessions/{session_id}/answers", json={
                "question_id": key, "answer_id": key,
                "text": value, "idempotency_key": f"answer:{key}"})
            assert saved.status_code == 200, saved.text
        submitted = client.post(f"/api/v1/sessions/{session_id}/submit",
                                json={"idempotency_key": "synthetic-submit"})
        assert submitted.status_code == 202, submitted.text
        result = client.get(f"/api/v1/sessions/{session_id}/result").json()
        assert result["status"] == "NEEDS_REVIEW"
        assert result["evaluation"]["score"] is None
        assert result["evaluation"]["workflow_version"] == "gim-langgraph-v1"
        assert result["evaluation"]["analysis"]["source_doc_ids"]
        assert len(calls) == 1
        assert calls[0]["authorization"] == "Bearer fake-development-key"
        assert "Synthetic Candidate" not in json.dumps(calls[0]["body"])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
