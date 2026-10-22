# Independent post-merge Windows QA and release verification — 2026-10-22

## Provenance and isolation
- Source baseline: `main@31c36a91ae1ff9b2b841cc1a7a563fc354035588` (PR #63 and #64 already merged).
- Fresh separate Windows clone, Python 3.13.9 and local venv (`requirements-dev.txt` and `requirements-vk.txt`); no tests on the teammates' existing working directories.
- No production VK Long Poll, external LLM, real Google Sheets or real candidate data accessed. FastAPI smoke used a temporary SQLite and loopback-only port.

## Actual commands and results
1. `python -m pytest -q` on source: **152 passed, 1 skipped, 146 subtests passed** (exit 0; one Starlette/httpx deprecation warning).
2. `python tests/run_local_smoke.py`: **168 PASS, 1 SKIP, 0 FAIL** (exit 0). Synthetic submit, no-duplicate repeat, protected data/CSV, clarification endpoint, isolated FastAPI on 127.0.0.1 and temporary SQLite; server stopped.
3. Initial extracted package exposed missing `CAND-01`/`CAND-06` evaluator test fixtures. Fixed the `scripts/make_team_portable.py` allowlist to add those two **synthetic regression fixtures**, and rebuilt from the same runtime source.
4. `python -m pytest -q` after packaging fix on source: **152 passed, 1 skipped, 146 subtests passed** (exit 0).
5. Extracted NEW rebuilt archive to a sibling folder outside the git checkout and ran `python -m pytest -q tests/test_smoke.py tests/test_evaluator.py`: **26 passed, 1 skipped, 50 subtests passed** (exit 0).
6. Executed `python tests/real_http_recovery.py` FROM that extracted package: **11 controlled HTTP recovery checks PASS** (exit 0), including 401/429/503/timeout/bad JSON, same-ID concurrent idempotency, restart and simulated hard crash; 11 mock-provider calls. This is local stubbed provider testing, not a live DeepSeek run.
7. Build script validates ZIP CRC and the internal `SHA256-MANIFEST.txt`, including `vk_bot/dispatcher.py`. `git diff --check` PASS.

## Release artifact
- Repository path: `releases/GIM_PRODUCT_LOCAL_DEMO_20261022.zip`.
- Archive root: `GIM_TEAM_QA/`.
- **54 entries**, **193752 bytes**.
- ZIP SHA256: `113ef6ec8172bec5c68c2c54d5bd9996b1f4d6434e04079a3e30ba1e5965e674`.
- `vk_bot/dispatcher.py` and worker-aware `vk_bot/bot.py` are included; the previous release ZIP from PR #61 is superseded by this artifact.

## Limitations
- No new live VK, DeepSeek or Google Sheets E2E test for the final SHA; earlier teammate live results cannot be transposed to this archive.
- The package contains a portable local demo plus synthetic QA inputs; it does not establish authorization to collect real personal data (see #28), historical scoring accuracy (#55) or complete question relevance (#49).
- The package's long-running startup launcher was not manually driven interactively. The extracted FastAPI app DID start on loopback for the controlled recovery suite.
- Some tests are intentionally not shipped with the allowlisted artifact; the complete suite was run on the full source checkout.
