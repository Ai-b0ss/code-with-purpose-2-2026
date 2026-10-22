"""Offline/loopback verification of the exact portable source ZIP (Issue #57).

Usage: python -m scripts.check_portable_boot
On Windows it extracts the package into a disposable directory and starts
the public START-TEAM demo path without requiring live credentials. Nothing
is sent to VK, Google Sheets, or an LLM.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from scripts.make_team_portable import PREFIX, build


def request(base, path, payload=None, token=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def verify_zip(archive):
    forbidden = {".git", ".venv", ".env", "data", "reports", "secret", "credentials"}
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise AssertionError("ZIP CRC check failed")
        names = set(package.namelist())
        name = PREFIX + "SHA256-MANIFEST.txt"
        if name not in names:
            raise AssertionError("Missing SHA256 manifest")
        seen = set()
        for entry in package.read(name).decode("utf-8").splitlines():
            digest, relative = entry.split("  ", 1)
            item = PREFIX + relative
            assert item in names, relative
            assert not (set(Path(relative).parts) & forbidden), relative
            assert hashlib.sha256(package.read(item)).hexdigest() == digest, relative
            seen.add(item)
        assert names == seen | {name}, "Unknown/unmanifested ZIP entry"
        assert PREFIX + "vk_bot/dispatcher.py" in names
        return len(names)


def synthetic():
    return {
        "application_id": "issue57-offline-restore",
        "applicant_name": "Вымышленный кандидат",
        "age_over_18": True,
        "birth_date": "2000-01-01",
        "consent_personal_data": True,
        "motivation": "Хочу помогать музею проводить интересные образовательные мероприятия.",
        "experience": "Учебный видеопроект",
        "expectations": "Получить опыт работы в музее.",
        "availability_hours_month": 16,
        "other_commitments": "Нет",
        "desired_direction": "media",
        "email": "qa57@example.test",
        "vk_url": "https://vk.com/qa57",
        "status": "student",
        "university": "Другое",
        "university_other": "Вымышленный вуз",
        "faculty": "История",
        "course": "2",
        "scientific_interests": "История",
        "foreign_languages": ["Нет B1+"],
        "hobbies": "Чтение",
        "source": "Другое",
    }


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows-only clean portable boot check")
    checks = []
    archive = build()
    entries = verify_zip(archive)
    checks.append("ZIP CRC, manifest, dispatcher and forbidden-path gate PASS")

    with tempfile.TemporaryDirectory(prefix="gim-issue57-clean-") as directory:
        work = Path(directory)
        with zipfile.ZipFile(archive) as package:
            package.extractall(work / "unpacked")
        root = work / "unpacked" / PREFIX.rstrip("/")
        database = work / "recovery.sqlite3"
        coordinator = "synthetic-coordinator-" + secrets.token_hex(20)
        port = available_port()
        base = f"http://127.0.0.1:{port}"

        env = os.environ.copy()
        for name in ("GIM_LLM_API_KEY", "OPENAI_API_KEY", "VK_GROUP_TOKEN",
                     "GIM_GOOGLE_CREDENTIALS_FILE", "GIM_GOOGLE_SPREADSHEET_ID"):
            env.pop(name, None)
        env.update({
            "GIM_DB_PATH": str(database), "GIM_EVALUATION_MODE": "demo",
            "GIM_ALLOW_EXTERNAL_LLM": "0", "GIM_COORDINATOR_TOKEN": coordinator,
            "GIM_LLM_FALLBACK_TO_DEMO": "0",
        })

        def boot():
            logfile = (work / "boot.log").open("ab")
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(root / "team-start.ps1"),
                 "-Demo", "-Port", str(port)],
                cwd=root, env=env, stdout=logfile, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 110
            try:
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise AssertionError(f"Portable boot exited early, code {proc.returncode}")
                    try:
                        status, body, _ = request(base, "/api/v1/health")
                        if status == 200 and json.loads(body)["evaluation_mode"] == "rules_demo":
                            return proc, logfile
                    except (urllib.error.URLError, TimeoutError, OSError):
                        pass
                    time.sleep(0.25)
                raise AssertionError("Portable boot health did not respond within timeout")
            except Exception:
                stop(proc, logfile)
                raise

        def stop(proc, logfile):
            if proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=15, check=False)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    raise AssertionError("Test launcher process failed to stop")
            logfile.close()
            # Windows may keep SQLite file handles briefly after a forced stop.
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    request(base, "/api/v1/health")
                except (urllib.error.URLError, TimeoutError, OSError):
                    break
                time.sleep(0.25)
            time.sleep(0.5)

        proc, logfile = boot()
        try:
            status, body, _ = request(base, "/")
            assert status == 200 and b"<html" in body.lower()
            status, body, _ = request(base, "/api/v1/clarification", synthetic())
            assert status == 200, (status, body[:200])
            status, body, _ = request(base, "/api/v1/applications", synthetic())
            assert status == 201, (status, body[:200])
            first = json.loads(body)
            assert first["application_id"] == "issue57-offline-restore"
            assert first.get("created") is True
            assert "evaluation" not in first
            checks.append("fresh extracted Windows launcher, web, clarification, public receipt PASS")
            status, body, _ = request(base, "/api/v1/applications/issue57-offline-restore")
            assert status in (401, 403)
            checks.append("private records denied without coordinator token PASS")
        finally:
            stop(proc, logfile)

        proc, logfile = boot()
        try:
            status, body, _ = request(base, "/api/v1/applications", synthetic())
            assert status == 201, (status, body[:200])
            assert json.loads(body)["created"] is False
            changed = synthetic()
            changed["motivation"] = "Вымышленный другой мотив, отличающийся от первоначального."
            status, _, _ = request(base, "/api/v1/applications", changed)
            assert status == 409
            status, body, _ = request(base, "/api/v1/applications/issue57-offline-restore", token=coordinator)
            assert status == 200, (status, body[:200])
            status, body, _ = request(base, "/api/v1/export.csv", token=coordinator)
            assert status == 200 and b"issue57-offline-restore" in body
            with closing(sqlite3.connect(database)) as con:
                assert con.execute("SELECT COUNT(*) FROM applications").fetchone()[0] == 1
            checks.append("restart, same-ID receipt, conflicting payload 409, protected CSV, one SQLite record PASS")
        finally:
            stop(proc, logfile)

    print(json.dumps({
        "result": "PASS", "checks": checks, "zip_entries": entries,
        "zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "external_api_calls": 0, "candidate_data": "synthetic only",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
