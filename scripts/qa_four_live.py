"""Run four REAL local GIM API scenarios and produce a reviewer-ready report.

Only fictional cases from qa/four_cases.json. The server's model provider is
configured separately; no API key is passed to this script or written to reports.
Do not count syntactic/model-contract PASS as expert-quality approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import sys
from time import perf_counter
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
CASE_PATH = ROOT / "qa" / "four_cases.json"
REPORTS = ROOT / "reports"


def request(base: str, route: str, payload: dict | None = None, timeout: int = 240):
    url = base.rstrip("/") + route
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read(4_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read(2048).decode("utf-8")).get("detail", "http_error")
        except (ValueError, UnicodeError, AttributeError):
            detail = "http_error"
        return exc.code, {"detail": str(detail)[:200]}


def pair(label: str, value: object) -> str:
    return "<dt>" + escape(label) + "</dt><dd>" + escape(str(value)) + "</dd>"


def pretty(data: object) -> str:
    return escape(json.dumps(data, ensure_ascii=False, indent=2))


def report_html(rows: list[dict], base: str, created: str) -> str:
    cards = []
    for entry in rows:
        output = entry.get("result") or {}
        evaluation = output.get("evaluation") if isinstance(output, dict) else {}
        if not isinstance(evaluation, dict):
            evaluation = {}
        good = entry.get("technical_pass", False)
        info = ("ТЕХНИЧЕСКИЙ PASS — требуется оценка Евы"
                if good else "ТЕХНИЧЕСКАЯ ОШИБКА — нужна диагностика")
        fields = (
            pair("HTTP / время", f'{entry.get("http_status")} / {entry.get("seconds")} с')
            + pair("Режим / модель", f'{evaluation.get("evaluation_mode")} / {entry.get("model")}')
            + pair("Evidence / score", f'{len(evaluation.get("evidence") or [])} / {evaluation.get("score")}')
            + pair("Повтор ID", entry.get("duplicate_status"))
            + pair("Результат", info)
        )
        cards.append(
            '<section><h2>' + escape(entry["case_id"]) + ': ' +
            escape(entry["goal"]) + '</h2><dl>' + fields + '</dl>' +
            '<h3>Входная вымышленная анкета</h3><pre>' + pretty(entry["input"]) + '</pre>' +
            '<h3>Фактический ответ модели / ошибки</h3><pre>' + pretty(output) + '</pre>' +
            '<h3>Экспертная оценка Евы</h3>' +
            '<p>PASS / FAIL: ______ &nbsp; Причины: ___________________________</p></section>'
        )
    style = """
    body{font:15px/1.55 system-ui,sans-serif;background:#faf8f5;color:#28241f;
    max-width:1050px;margin:36px auto;padding:0 18px}
    section{background:white;border:1px solid #ddd1c3;border-radius:12px;
    padding:22px;margin:22px 0;box-shadow:0 2px 8px #00000008}
    h1,h2{color:#852327}h2{font-size:19px}h3{font-size:16px}
    pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f3ef;padding:15px;
    border-radius:8px;font:12px/1.55 Consolas,monospace}
    dl{display:grid;grid-template-columns:170px 1fr;gap:8px 16px}
    dt{color:#625d57;font-weight:600}dd{margin:0}
    .warning{padding:14px;border:1px solid #c48d40;background:#fff8e8}
    """
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>ГИМ — QA #27: DeepSeek</title><style>' + style +
            '</style></head><body><h1>ГИМ · техническая приёмка #27</h1>' +
            '<p>Дата: ' + escape(created) + '; локальный сервер ' + escape(base) + '</p>' +
            '<p class="warning">Только вымышленные данные. Технический PASS не означает '
            'экспертное одобрение. Баллы 0–3 из синтетических карточек не являются '
            'калибровкой. Файл хранится локально, не публикуйте отчёты с реальными ПД.</p>' +
            ''.join(cards) + '</body></html>')


def main() -> int:
    parser = argparse.ArgumentParser(description="Four REAL DeepSeek assessments via local GIM server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8876")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--only", choices=["CAND-01", "CAND-05", "CAND-10", "CAND-06"])
    args = parser.parse_args()
    u = urllib.parse.urlsplit(args.base_url)
    if u.scheme != "http" or u.hostname not in {"127.0.0.1", "localhost"} or not u.port or u.path not in {"", "/"}:
        print("ERROR: only http://127.0.0.1:PORT is allowed", file=sys.stderr)
        return 2

    try:
        status, health = request(args.base_url, "/api/v1/health", timeout=6)
    except (OSError, ValueError) as exc:
        print(f"ERROR: GIM server unreachable ({type(exc).__name__}). Start START-ILYA.cmd.", file=sys.stderr)
        return 2
    if (status != 200 or health.get("evaluation_mode") != "llm"
            or health.get("fallback_to_demo") is not False):
        print("ERROR: server must be real LLM mode, fallback_to_demo=false", file=sys.stderr)
        return 2

    cases = json.loads(CASE_PATH.read_text(encoding="utf-8"))["cases"]
    if args.only:
        cases = [case for case in cases if case["case_id"] == args.only]
    rows = []
    print(f"REAL GIM server: {args.base_url} | model: deepseek-v4.1-flash")
    for case in cases:
        payload = dict(case["answers"])
        payload["application_id"] = "qa27-" + case["case_id"].lower() + "-" + uuid.uuid4().hex[:12]
        print("START", case["case_id"], flush=True)
        begin = perf_counter()
        try:
            status, output = request(args.base_url, "/api/v1/applications", payload, args.timeout)
            elapsed = round(perf_counter() - begin, 2)
            result = output.get("evaluation", {}) if isinstance(output, dict) else {}
            passed = (status == 201 and output.get("created") is True
                      and isinstance(result, dict)
                      and result.get("evaluation_mode") == "llm"
                      and isinstance(result.get("score"), int)
                      and 0 <= result["score"] <= 3
                      and result.get("score_status") == "pilot_unvalidated"
                      and len(result.get("scorecard") or []) == 5
                      and len(result.get("evidence") or []) > 0)
            duplicate_status = "not_checked"
            if passed:
                restatus, again = request(args.base_url, "/api/v1/applications", payload, 12)
                duplicate_status = ("PASS: created=false, identical evaluation"
                                    if restatus == 201 and again.get("created") is False
                                    and again.get("evaluation") == result
                                    else f"FAIL: repeated POST HTTP {restatus}")
                passed = passed and duplicate_status.startswith("PASS")
            row = {
                "case_id": case["case_id"], "goal": case["goal"],
                "expected_signals": case.get("expected_signals", []),
                "model": "deepseek-v4.1-flash", "seconds": elapsed,
                "http_status": status, "technical_pass": passed,
                "duplicate_status": duplicate_status,
                "input": payload, "result": output,
                "expert_review": {"pass": None, "comment": "", "reviewer": ""}
            }
        except (TimeoutError, OSError, ValueError) as exc:
            row = {
                "case_id": case["case_id"], "goal": case["goal"],
                "model": "deepseek-v4.1-flash",
                "seconds": round(perf_counter() - begin, 2),
                "http_status": None, "technical_pass": False,
                "duplicate_status": "not_checked", "input": payload,
                "result": {"error_type": type(exc).__name__},
                "expert_review": {"pass": None, "comment": "", "reviewer": ""}
            }
        rows.append(row)
        print(("TECH PASS" if row["technical_pass"] else "TECH FAIL"),
              case["case_id"], f'{row["seconds"]}s', flush=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    payload = {"created_at_utc": stamp, "base_url": args.base_url, "cases": rows,
               "note": "Provisional numeric score only; expert evaluation and statistical calibration not performed"}
    json_path = REPORTS / f"issue27_{stamp}.json"
    html_path = REPORTS / f"issue27_{stamp}.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(report_html(rows, args.base_url, stamp), encoding="utf-8")
    print("JSON:", json_path)
    print("HTML:", html_path)
    print("Expert PASS/FAIL is still required from Eva.")
    return 0 if rows and all(row["technical_pass"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
