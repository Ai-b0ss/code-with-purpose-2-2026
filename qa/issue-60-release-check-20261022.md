# Issue #60 release check — 22 October 2026

Base: `main@816d8e6c4642a35764c30cf3ab5da5c07b487334`. The working tree was clean at takeover. The three unpushed VK changes described in #60 were not present in this workspace; the narrow behavior fix was implemented here.

## Changes

- VK sends distinct clarification and final evaluation notices. A short throttle now keys by phase, so immediate transition to final evaluation still produces a notice.
- Final submission errors retain the same application ID and draft. A backend `evaluator_*` detail is logged as a safe code and described as an AI evaluation failure; other errors get a generic submission message. Retry states require an explicit retry command.
- No provider network retry or model change was added. Historical live HTTP 503 observations have no accessible sanitized `evaluator_*` code here, so their underlying cause remains undiagnosed.

## Checks on this branch

| Check | Result | Scope |
|---|---|---|
| `python -m pytest -q` | 139 passed, 1 optional live skip, 122 subtests | Includes two notice phases, synthetic backend 503 and retry with same ID/follow-up answers. |
| `python tests/run_local_smoke.py` | 155 passed, 1 skipped, 0 failed | Isolated local demo and SQLite. |
| `python tests/real_http_recovery.py` | PASS, 11 controlled checks | Local fake provider, including 503 and process restart; no real model. |
| `git diff --check` | PASS | No whitespace errors. |
| `releases/GIM_PRODUCT_LOCAL_DEMO_20261022.zip` | 51 entries; manifest hashes and `ZipFile.testzip()` PASS | Explicit allowlist, no `.env`, keys, SQLite or reports. |
| Extracted ZIP: `team-start.ps1 -Demo -Port 59431` | PASS | Temporary isolated directory and synthetic coordinator token. |
| Extracted demo HTTP | PASS | Home, health, clarification, submit, same-ID `created=false`, one row per ID, CSV 43 columns, unauthorized list 401, retained rows after restart. |

Release ZIP SHA256: `fd1939d3f783ad0b8b3937c28107bafd09b0633ed7f72a6b8ec59a9b30115c0f`. The final archive was extracted again after the last code change and passed startup and HTTP smoke.

Real VK Long Poll, DeepSeek and Google Sheets were not run in this environment. The old live observations in #60 belong to another agent and are not attributed to this commit. The current deliverable is a verified local synthetic demo; external integration and provider error-code capture remain for the operator with authorized test credentials and the sole VK Long Poll process.
