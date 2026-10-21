"""Autonomous local smoke test runner for GIM application.

Executes unit tests, launches a local FastAPI server on an ephemeral port
with an isolated temporary SQLite database, verifies all required API endpoints,
safely terminates the server, and outputs a clear PASS/FAIL report.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

# Fix terminal encoding issues on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKING_DB = REPO_ROOT / "data" / "gim.sqlite3"

# Ensure repository root is on sys.path
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class Colors:
    """ANSI color codes with safe fallback if not supported."""
    enabled = sys.stdout.isatty() or os.environ.get("FORCE_COLOR") == "1"

    GREEN = "\033[92m" if enabled else ""
    RED = "\033[91m" if enabled else ""
    YELLOW = "\033[93m" if enabled else ""
    BLUE = "\033[94m" if enabled else ""
    CYAN = "\033[96m" if enabled else ""
    BOLD = "\033[1m" if enabled else ""
    RESET = "\033[0m" if enabled else ""


class TestTracker:
    def __init__(self):
        self.results: list[tuple[str, str, str | None]] = []

    def pass_test(self, name: str, details: str = ""):
        self.results.append(("PASS", name, details))
        msg = f"  {Colors.GREEN}[PASS]{Colors.RESET} {name}"
        if details:
            msg += f" {Colors.CYAN}({details}){Colors.RESET}"
        print(msg)

    def fail_test(self, name: str, error: str):
        self.results.append(("FAIL", name, error))
        print(f"  {Colors.RED}[FAIL]{Colors.RESET} {name}")
        print(f"         {Colors.RED}--> Ошибка: {error}{Colors.RESET}")

    def skip_test(self, name: str, reason: str):
        self.results.append(("SKIP", name, reason))
        print(f"  [SKIP] {name}: {reason}")

    def info(self, text: str):
        print(f"  {Colors.BLUE}[INFO]{Colors.RESET} {text}")

    @property
    def passed_count(self) -> int:
        return sum(1 for status, _, _ in self.results if status == "PASS")

    @property
    def failed_count(self) -> int:
        return sum(1 for status, _, _ in self.results if status == "FAIL")


def print_section(title: str, step: str = ""):
    header = f"[{step}] {title}" if step else title
    line = "=" * 70
    print(f"\n{Colors.BOLD}{line}{Colors.RESET}")
    print(f"{Colors.BOLD}{header}{Colors.RESET}")
    print(f"{Colors.BOLD}{line}{Colors.RESET}")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        return s.getsockname()[1]


def get_db_file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def http_request(base_url: str, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, bytes, dict]:
    raw_data = None
    headers = {}
    if payload is not None:
        raw_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    req = urllib.request.Request(
        f"{base_url}{path}",
        data=raw_data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp_headers = dict(resp.headers)
        return resp.status, resp.read(), resp_headers


def get_all_tests(test_suite: unittest.TestSuite | unittest.TestCase) -> list[unittest.TestCase]:
    cases = []
    if isinstance(test_suite, unittest.TestSuite):
        for item in test_suite:
            cases.extend(get_all_tests(item))
    else:
        cases.append(test_suite)
    return cases


def run_unit_tests(tracker: TestTracker) -> bool:
    print_section("МОДУЛЬНЫЕ ТЕСТЫ (UNIT TESTS)", step="1/5")
    tracker.info("Поиск всех тестов tests/test_*.py...")

    loader = unittest.TestLoader()
    try:
        suite = loader.discover(start_dir=str(REPO_ROOT / "tests"), pattern="test_*.py")
    except Exception as exc:
        tracker.fail_test("Импорт tests/test_smoke.py", str(exc))
        return False

    all_passed = True
    test_cases = get_all_tests(suite)

    for test in test_cases:
        test_id = test.id().split(".")[-1]
        result = unittest.TestResult()
        start = time.perf_counter()
        test.run(result)
        elapsed_ms = (time.perf_counter() - start) * 1000

        if result.skipped:
            tracker.skip_test(f"Unit: {test_id}", result.skipped[0][1])
        elif result.wasSuccessful():
            tracker.pass_test(f"Unit: {test_id}", f"{elapsed_ms:.1f}ms")
        else:
            all_passed = False
            err_text = ""
            if result.errors:
                err_text = result.errors[0][1].strip().splitlines()[-1]
            elif result.failures:
                err_text = result.failures[0][1].strip().splitlines()[-1]
            tracker.fail_test(f"Unit: {test_id}", err_text or "Ошибка выполнения теста")

    return all_passed


def run_integration_tests(tracker: TestTracker) -> bool:
    print_section("ПОДГОТОВКА ИЗОЛИРОВАННОГО ОКРУЖЕНИЯ", step="2/5")

    # 1. State of working DB before tests
    working_db_existed = DEFAULT_WORKING_DB.is_file()
    working_db_hash_before = get_db_file_hash(DEFAULT_WORKING_DB) if working_db_existed else None
    if working_db_existed:
        tracker.info(f"Рабочая база найдена: {DEFAULT_WORKING_DB} (sha256: {working_db_hash_before[:12]}...)")
    else:
        tracker.info(f"Рабочая база {DEFAULT_WORKING_DB} отсутствует (чистый репозиторий)")

    # 2. Allocate temporary directory and SQLite DB
    temp_dir = tempfile.TemporaryDirectory(prefix="gim_test_")
    temp_dir_path = Path(temp_dir.name)
    temp_db_path = temp_dir_path / "gim_isolated_test.sqlite3"
    tracker.pass_test("Создание временного каталога для SQLite", str(temp_db_path))

    # 3. Find free port
    free_port = find_free_port()
    tracker.pass_test("Выделение свободного сетевого порта", f"порт {free_port}")

    print_section("ЗАПУСК ЛОКАЛЬНОГО FASTAPI СЕРВЕРА", step="3/5")

    env = os.environ.copy()
    env["GIM_DB_PATH"] = str(temp_db_path)
    env["PYTHONUNBUFFERED"] = "1"
    # Ensure current repo root is in PYTHONPATH
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(free_port),
    ]

    base_url = f"http://127.0.0.1:{free_port}"
    tracker.info(f"Запуск команды: {' '.join(cmd)}")

    server_proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait for server readiness
        server_ready = False
        start_wait = time.time()
        while time.time() - start_wait < 12.0:
            if server_proc.poll() is not None:
                # Process terminated prematurely!
                _, stderr_data = server_proc.communicate()
                err_msg = stderr_data.decode("utf-8", errors="replace")
                tracker.fail_test("Старт сервера Uvicorn", f"Процесс завершился с кодом {server_proc.returncode}:\n{err_msg}")
                return False

            try:
                status, body, _ = http_request(base_url, "/api/v1/health")
                if status == 200:
                    server_ready = True
                    break
            except Exception:
                time.sleep(0.15)

        if not server_ready:
            tracker.fail_test("Старт сервера Uvicorn", f"Таймаут ожидания готовности сервера на {base_url}")
            return False

        tracker.pass_test(
            "Сервер FastAPI/Uvicorn успешно запущен и отвечает",
            f"PID {server_proc.pid}, адрес: {base_url}",
        )

        # 4. API Testing Phase
        print_section("ПРОВЕРКА API И ФУНКЦИОНАЛА", step="4/5")

        # 4.1 Server responds: GET / (Web interface)
        try:
            status, body, _ = http_request(base_url, "/")
            text = body.decode("utf-8", errors="replace")
            if status == 200 and ("Музейный компас" in text or "<html" in text.lower()):
                tracker.pass_test("Сервер отвечает: GET / (главная страница)", f"HTTP {status}")
            else:
                tracker.fail_test("Сервер отвечает: GET /", f"Неожиданный ответ: статус {status}")
        except Exception as exc:
            tracker.fail_test("Сервер отвечает: GET /", str(exc))

        # 4.2 Server responds: GET /api/v1/health
        try:
            status, body, _ = http_request(base_url, "/api/v1/health")
            data = json.loads(body.decode("utf-8"))
            if status == 200 and data.get("status") == "ok" and data.get("evaluation_mode") == "rules_demo":
                tracker.pass_test("Сервер отвечает: GET /api/v1/health (health check)", f"status={data['status']}, mode={data['evaluation_mode']}")
            else:
                tracker.fail_test("Сервер отвечает: GET /api/v1/health", f"Некорректный JSON: {data}")
        except Exception as exc:
            tracker.fail_test("Сервер отвечает: GET /api/v1/health", str(exc))

        # 4.3 Clarification endpoint: POST /api/v1/clarification
        sample_app = {
            "application_id": "smoke-test-candidate-001",
            "applicant_name": "Тестовый Кандидат",
            "age_over_18": True,
            "consent_personal_data": True,
            "motivation": "Мне интересно создавать исторический медиаконтент и помогать музею.",
            "experience": "Делал видео для университетского кружка.",
            "expectations": "Хочу научиться работать с архивными историями.",
            "availability_hours_month": 16,
            "other_commitments": "Учёба",
            "desired_direction": "media",
            "followup_answer": "Готов уделять вечера и выходные.",
        }

        try:
            status, body, _ = http_request(base_url, "/api/v1/clarification", method="POST", payload=sample_app)
            data = json.loads(body.decode("utf-8"))
            if status == 200 and "question" in data:
                tracker.pass_test("Эндпоинт уточнений: POST /api/v1/clarification", f"HTTP {status}")
            else:
                tracker.fail_test("Эндпоинт уточнений: POST /api/v1/clarification", f"Статус {status}, ответ: {data}")
        except Exception as exc:
            tracker.fail_test("Эндпоинт уточнений: POST /api/v1/clarification", str(exc))

        # 4.4 Save application: POST /api/v1/applications
        try:
            status, body, _ = http_request(base_url, "/api/v1/applications", method="POST", payload=sample_app)
            data = json.loads(body.decode("utf-8"))
            if (
                status == 201
                and data.get("created") is True
                and data.get("application_id") == sample_app["application_id"]
                and data.get("evaluation", {}).get("recommended_direction") == "media"
            ):
                tracker.pass_test(
                    "Анкета сохраняется: POST /api/v1/applications",
                    f"HTTP {status}, ID: {data['application_id']}, created=True",
                )
            else:
                tracker.fail_test("Анкета сохраняется: POST /api/v1/applications", f"Ответ: {status}, {data}")
        except Exception as exc:
            tracker.fail_test("Анкета сохраняется: POST /api/v1/applications", str(exc))

        # 4.5 Fetch saved application: GET /api/v1/applications/{id}
        try:
            status, body, _ = http_request(base_url, f"/api/v1/applications/{sample_app['application_id']}")
            data = json.loads(body.decode("utf-8"))
            if (
                status == 200
                and data.get("application_id") == sample_app["application_id"]
                and data.get("answers", {}).get("applicant_name") == sample_app["applicant_name"]
            ):
                tracker.pass_test("Получение сохранённой анкеты по ID", f"HTTP {status}, проверено имя кандидата")
            else:
                tracker.fail_test("Получение сохранённой анкеты по ID", f"Ответ: {status}, {data}")
        except Exception as exc:
            tracker.fail_test("Получение сохранённой анкеты по ID", str(exc))

        # 4.6 Duplicate submission idempotency check: repeat POST /api/v1/applications
        try:
            status, body, _ = http_request(base_url, "/api/v1/applications", method="POST", payload=sample_app)
            data = json.loads(body.decode("utf-8"))
            if (
                status == 201
                and data.get("created") is False
                and data.get("application_id") == sample_app["application_id"]
            ):
                tracker.pass_test(
                    "Повторная отправка не создаёт дубль (ответ created=False)",
                    f"HTTP {status}, created=False",
                )
            else:
                tracker.fail_test("Повторная отправка не создаёт дубль", f"Ответ: {status}, {data}")
        except Exception as exc:
            tracker.fail_test("Повторная отправка не создаёт дубль", str(exc))

        # Verify database list has strictly 1 item for this ID
        try:
            status, body, _ = http_request(base_url, "/api/v1/applications")
            data = json.loads(body.decode("utf-8"))
            items = data.get("items", [])
            matches = [item for item in items if item.get("application_id") == sample_app["application_id"]]
            if len(matches) == 1:
                tracker.pass_test("Проверка реестра заявок на отсутствие дубликатов", f"найдено ровно {len(matches)} запись")
            else:
                tracker.fail_test(
                    "Проверка реестра заявок на отсутствие дубликатов",
                    f"Ожидалась 1 запись, найдено {len(matches)}!",
                )
        except Exception as exc:
            tracker.fail_test("Проверка реестра заявок на отсутствие дубликатов", str(exc))

        # 4.7 CSV export check: GET /api/v1/export.csv
        try:
            status, body, headers = http_request(base_url, "/api/v1/export.csv")
            has_bom = body.startswith(b"\xef\xbb\xbf")
            content = body.decode("utf-8-sig")
            reader = list(csv.reader(io.StringIO(content)))
            has_header = len(reader) > 0 and "ID заявки" in reader[0]
            has_row = any(sample_app["applicant_name"] in row and sample_app["application_id"] in row for row in reader)

            if status == 200 and has_bom and has_header and has_row:
                tracker.pass_test(
                    "CSV корректно экспортируется: GET /api/v1/export.csv",
                    f"HTTP 200, UTF-8 BOM, строк: {len(reader)}, кандидат найден",
                )
            else:
                tracker.fail_test(
                    "CSV корректно экспортируется",
                    f"status={status}, has_bom={has_bom}, has_header={has_header}, has_row={has_row}",
                )
        except Exception as exc:
            tracker.fail_test("CSV корректно экспортируется", str(exc))

        # 4.8 Invalid request returns validation error: POST /api/v1/applications
        invalid_cases = [
            (
                "Невалидный запрос (пустое имя кандидата)",
                {"applicant_name": "", "age_over_18": True, "motivation": "Достаточно длинная строка мотивации.", "desired_direction": "media"},
            ),
            (
                "Невалидный запрос (недопустимое направление)",
                {**sample_app, "desired_direction": "invalid_unknown_direction"},
            ),
            (
                "Невалидный запрос (слишком короткая мотивация)",
                {**sample_app, "motivation": "коротко"},
            ),
        ]

        for case_name, invalid_payload in invalid_cases:
            try:
                req = urllib.request.Request(
                    f"{base_url}/api/v1/applications",
                    data=json.dumps(invalid_payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    tracker.fail_test(case_name, f"Ожидалась ошибка 422, но сервер вернул статус {resp.status}")
            except urllib.error.HTTPError as exc:
                if exc.code == 422:
                    error_data = json.loads(exc.read().decode("utf-8"))
                    tracker.pass_test(case_name, f"HTTP {exc.code} Unprocessable Entity, ошибки: {len(error_data.get('detail', []))}")
                else:
                    tracker.fail_test(case_name, f"Ожидался код 422, получен {exc.code}")
            except Exception as exc:
                tracker.fail_test(case_name, str(exc))

        # 4.9 Run existing tests/smoke_http.py against the isolated server
        try:
            from tests import smoke_http
            smoke_http.run_smoke(base_url=base_url)
            tracker.pass_test("Сквозной модуль tests/smoke_http.py", "полный цикл проверен")
        except Exception as exc:
            tracker.fail_test("Сквозной модуль tests/smoke_http.py", str(exc))

    finally:
        # 5. Clean Teardown & Isolation Verification
        print_section("ЗАВЕРШЕНИЕ СЕРВЕРА И ОЧИСТКА", step="5/5")

        # 5.1 Terminate server process
        tracker.info(f"Остановка процесса Uvicorn (PID: {server_proc.pid})...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5.0)
            tracker.pass_test("Сервер корректно остановлен (SIGTERM)", f"код возврата: {server_proc.returncode}")
        except subprocess.TimeoutExpired:
            tracker.info("Таймаут остановки, принудительное завершение (kill)...")
            server_proc.kill()
            server_proc.wait(timeout=3.0)
            tracker.pass_test("Сервер принудительно остановлен (kill)", f"код возврата: {server_proc.returncode}")

        # 5.2 Verify temporary DB exists and has test records
        if temp_db_path.is_file():
            size_bytes = temp_db_path.stat().st_size
            tracker.pass_test("Временная SQLite-база изолированно приняла тестовые данные", f"размер: {size_bytes} байт")
        else:
            tracker.fail_test("Временная SQLite-база", "Файл временной БД не найден!")

        # 5.3 Verify working database was NOT touched
        working_db_hash_after = get_db_file_hash(DEFAULT_WORKING_DB) if DEFAULT_WORKING_DB.is_file() else None
        if not working_db_existed:
            if not DEFAULT_WORKING_DB.is_file():
                tracker.pass_test(
                    "Изоляция SQLite: рабочая база data/gim.sqlite3 не создавалась во время тестов",
                    "состояние репозитория не затронуто",
                )
            else:
                tracker.fail_test(
                    "Изоляция SQLite",
                    f"Файл {DEFAULT_WORKING_DB} был непреднамеренно создан тестами!",
                )
        else:
            if working_db_hash_before == working_db_hash_after:
                tracker.pass_test(
                    "Изоляция SQLite: рабочая база data/gim.sqlite3 осталась нетронутой",
                    f"sha256 совпадает: {working_db_hash_after[:12]}...",
                )
            else:
                tracker.fail_test(
                    "Изоляция SQLite",
                    "Рабочая база data/gim.sqlite3 была изменена во время тестов!",
                )

        # 5.4 Clean up temporary directory
        try:
            temp_dir.cleanup()
            tracker.pass_test("Временный каталог и тестовая база успешно удалены")
        except Exception as exc:
            tracker.info(f"Предупреждение при очистке временного каталога: {exc}")

    return tracker.failed_count == 0


def main() -> int:
    tracker = TestTracker()
    start_total = time.perf_counter()

    print(f"\n{Colors.BOLD}{Colors.CYAN}{'#' * 70}")
    print("#  ГИМ: АВТОНОМНЫЙ ЛОКАЛЬНЫЙ SMOKE-ТЕСТИРОВЩИК (WINDOWS / LOCAL)   #")
    print(f"{'#' * 70}{Colors.RESET}\n")

    # Step 1: Unit tests
    run_unit_tests(tracker)

    # Step 2-5: Dynamic server, API smoke tests, isolation & teardown
    run_integration_tests(tracker)

    elapsed_total = time.perf_counter() - start_total

    # Summary
    print("\n" + "=" * 70)
    if tracker.failed_count == 0:
        print(f"{Colors.BOLD}{Colors.GREEN}ИТОГ: ВСЕ ТЕСТЫ УСПЕШНО ПРОЙДЕНЫ (PASS){Colors.RESET}")
        skipped_count = sum(1 for status, _, _ in tracker.results if status == "SKIP")
        print(f"Успешно: {tracker.passed_count} проверок, Пропущено: {skipped_count}, Ошибок: 0. Время: {elapsed_total:.2f}с.")
        print("=" * 70 + "\n")
        return 0
    else:
        print(f"{Colors.BOLD}{Colors.RED}ИТОГ: ОБНАРУЖЕНЫ ОШИБКИ (FAIL){Colors.RESET}")
        skipped_count = sum(1 for status, _, _ in tracker.results if status == "SKIP")
        print(f"Успешно: {tracker.passed_count}, Пропущено: {skipped_count}, Ошибок: {tracker.failed_count}. Время: {elapsed_total:.2f}с.")
        print("Список упавших проверок:")
        for status, name, err in tracker.results:
            if status == "FAIL":
                print(f"  - {name}: {err}")
        print("=" * 70 + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
