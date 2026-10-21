"""Probe the user's ReformBoss OpenAI-compatible models with SYNTHETIC GIM candidates.

No credentials are logged, persisted, or accepted on the command line.
Run via start-reformboss.ps1 or set GIM_LLM_API_KEY in the current shell.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

from backend.app import ApplicationInput
from backend.evaluator import ChatCompletionsAdapter, EvaluationError, evaluate

ENDPOINT = "https://api.reformboss.com/v1/chat/completions"
MODELS = ("deepseek-v4.1-flash", "glm-5.3", "kimi-k3")


def sample_candidate() -> ApplicationInput:
    """Only imaginary data leave the computer during this connectivity check."""
    return ApplicationInput(
        application_id="synthetic-api-probe-do-not-store",
        applicant_name="Тестовый кандидат (вымышленный)",
        age_over_18=True,
        consent_personal_data=True,
        status="student",
        course="1-й курс бакалавриата",
        motivation=(
            "Хочу помогать посетителям Исторического музея, участвовать "
            "в подготовке исторических мероприятий и регулярно приходить на смены."
        ),
        experience="Помогал в организации школьных экскурсий.",
        expectations="Хочу получить практический опыт музейной работы.",
        availability_hours_month=20,
        availability_days=["суббота"],
        availability_schedule_type="regular",
        other_commitments="Учёба на 1 курсе бакалавриата, свободен по субботам.",
        desired_direction="organizational",
    )


def check_network(endpoint: str) -> bool:
    """Check TLS/host connectivity WITHOUT sending the user's API key or answers.

    HTTP 401 means the HTTPS connection worked; this is an unauthenticated check.
    """
    models_url = endpoint.rsplit("/chat/completions", 1)[0] + "/models"
    request = urllib.request.Request(
        models_url, headers={"User-Agent": "GIM-ReformBoss-Connectivity/1.0"}
    )
    print("Проверка TLS и доступности API без ключа...")
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            print(f"[NETWORK OK] HTTPS работает, HTTP {response.status}")
        return True
    except urllib.error.HTTPError as exc:
        print(f"[NETWORK OK] HTTPS работает, HTTP {exc.code} "
              "(без ключа это может быть ожидаемая авторизация)")
        return True
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            print("[NETWORK FAIL] Ошибка доверия к TLS-сертификату. "
                  "Проверь часы Windows, HTTPS-проверку VPN/прокси и доверенные "
                  "корневые сертификаты. Не отключай проверку SSL.")
        elif isinstance(reason, ssl.SSLError):
            print("[NETWORK FAIL] Ошибка TLS-соединения.")
        elif isinstance(reason, socket.gaierror):
            print("[NETWORK FAIL] DNS не разрешает имя API.")
        else:
            print("[NETWORK FAIL] Ошибка сети:", type(reason).__name__)
        return False
    except TimeoutError:
        print("[NETWORK FAIL] Превышено время ожидания.")
        return False
    except OSError as exc:
        print("[NETWORK FAIL] Ошибка соединения:", type(exc).__name__)
        return False


def explanation_for_failure(code: str) -> str:
    if code in {"provider_http_401", "provider_http_403"}:
        return "Сервер отклонил доступ: проверь действительность ключа, право на API и лимиты аккаунта."
    if code in {"provider_http_400", "provider_http_422"}:
        return "Сервер отклонил формат запроса; попробуй повторный запуск с -DisableJsonMode."
    if code == "provider_http_404":
        return "Проверь точное имя модели и URL API: сервер вернул HTTP 404."
    if code == "provider_http_429":
        return "Достигнут лимит/частота запросов API (не путать с лимитом Telegram-бота)."
    if code == "provider_tls_certificate":
        return "Проблема с доверием к HTTPS-сертификату в Python. Не выключай SSL verification."
    if code in {"provider_http_500", "provider_http_502", "provider_http_503", "provider_http_504"}:
        return "Ошибка сервера провайдера: можно попробовать позже или другую модель."
    if code.startswith("provider_http_"):
        return "Провайдер вернул HTTP-ошибку; код выше, ответ сервера не логируется."
    if code == "provider_timeout":
        return "Таймаут; увеличь GIM_LLM_TIMEOUT при необходимости."
    if code == "invalid_model_json":
        return "Ответ модели не соответствует контракту/дословным цитатам, а не сбой сети."
    return "Проверь доступность API и совместимость формата; секреты не выводятся."


def list_models(api_key: str, endpoint: str) -> None:
    models_url = endpoint.rsplit("/chat/completions", 1)[0] + "/models"
    req = urllib.request.Request(
        models_url, headers={"Authorization": "Bearer " + api_key}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            value = json.loads(response.read(300_000))
        models = [
            str(item.get("id", "")) for item in value.get("data", [])
            if isinstance(item, dict)
        ]
        matches = [m for m in models if any(word in m.lower() for word in
                   ("deepseek", "glm", "kimi", "qwen"))]
        print("API models:", ", ".join(matches[:35]) or
              "(список моделей пуст или не предоставлен сервером)")
    except (urllib.error.URLError, ValueError, OSError) as exc:
        # No exception detail: some HTTP servers echo the request URL/body.
        print("Не удалось получить /models:", type(exc).__name__)


def probe_model(name: str, endpoint: str, key: str, json_mode: bool) -> bool:
    adapter = ChatCompletionsAdapter(
        endpoint=endpoint, model=name, api_key=key,
        timeout=60.0, json_mode=json_mode,
    )
    started = time.monotonic()
    try:
        result = evaluate(sample_candidate(), adapter=adapter)
    except EvaluationError as exc:
        print(f"[FAIL] {name}: {exc.code}")
        if exc.details:
            print("Причины по полям (без текста анкеты и ответа модели):",
                  "; ".join(exc.details[:12]))
        print("Причина/следующее действие:", explanation_for_failure(exc.code))
        return False

    seconds = time.monotonic() - started
    risk = any("Первый курс" in str(x) for x in result["possible_problems"])
    is_llm = result.get("evaluation_mode") == "llm"
    no_score = result.get("score") is None
    evidence = bool(result.get("evidence"))
    contract_ok = all((risk, is_llm, no_score, evidence))
    print(f"[{'PASS' if contract_ok else 'REVIEW'}] {name}: "
          f"{seconds:.1f} с, формат={result['evaluation_mode']}, "
          f"evidence={len(result['evidence'])}, score=null={no_score}, "
          f"риск 1 курса={risk}")
    print("Саммари:", result.get("summary", "")[:400])
    print("Соответствие музею:", result.get("fit_for_museum", "")[:600])
    print("Вопросы интервью:", " | ".join(result.get("interview_questions", [])[:3]))
    print("Рекомендация:", result.get("recommended_direction"))
    return contract_ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=MODELS[0])
    parser.add_argument("--compare", action="store_true",
                        help="One live synthetic candidate per model (3 API calls)")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--check-network", action="store_true",
                        help="Unauthenticated TLS/HTTP check; no key required")
    parser.add_argument("--disable-json-mode", action="store_true")
    parser.add_argument("--endpoint", default=ENDPOINT,
                        help="Full chat/completions URL (default ReformBoss)")
    args = parser.parse_args()

    if args.check_network:
        return 0 if check_network(args.endpoint) else 3

    key = os.environ.get("GIM_LLM_API_KEY", "")
    if not key:
        print("Ключ не задан: запустите start-reformboss.ps1 для обычного ввода.")
        return 2
    if args.list_models:
        list_models(key, args.endpoint)
    chosen = MODELS if args.compare else (args.model,)
    print(f"Проверяем {len(chosen)} реальный API-запрос(ов) на ВЫМЫШЛЕННОЙ анкете.")
    results = [
        probe_model(name, args.endpoint, key, not args.disable_json_mode)
        for name in chosen
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
