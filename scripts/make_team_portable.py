"""Safe reproducible team QA source package, without credentials or real candidate data."""
from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parent.parent

# Launchers and kit READMEs live outside the repo root; archive names stay flat.
_MOVED={**{n:"scripts/launchers/"+n for n in ("CURATOR-REPORT.cmd","QA-4-CASES.cmd","START-ILYA.cmd","START-TEAM.cmd","portable-start.ps1","start-reformboss.ps1","team-start.ps1","run_vk_bot.bat")},
        **{n:"docs/team/"+n for n in ("README-ILYA.md","README-TEAM.md")}}
def source_path(name): return ROOT/_MOVED.get(name,name)
ZIP_NAME = "GIM_TEAM_QA_AGENT_FIXED_20261022.zip"
PREFIX = "GIM_TEAM_QA/"
FILES = (
    "START-TEAM.cmd", "team-start.ps1", "README-TEAM.md",
    "QA-4-CASES.cmd", "CURATOR-REPORT.cmd",
    "portable-start.ps1", "start-reformboss.ps1", "requirements.txt",
    "docs/privacy-and-security.md", "docs/operations.md", "docs/gim-question-coverage.md",
    "backend/__init__.py", "backend/app.py", "backend/evaluator.py",
    "backend/availability_policy.py", "backend/clarification_agent.py",
    "backend/google_sheets.py", "backend/curator.py", "backend/security.py", "backend/candidate_feedback.py", "backend/eligibility.py", "backend/analytics.py", "frontend/admin.html", "frontend/index.html",
    "vk_bot/__init__.py", "vk_bot/session_store.py", "vk_bot/dispatcher.py", "vk_bot/bot.py",
    "vk_bot/config.py", "vk_bot/api_client.py", "vk_bot/keyboards.py", "vk_bot/fsm.py", "vk_bot/dialog_steps.py", "run_vk_bot.bat", "requirements-vk.txt",
    "scripts/check_reformboss.py", "scripts/check_google_sheets_live.py", "scripts/qa_four_live.py",
    "scripts/qa_agent_live.py",
    "qa/autonomous-checks-20261022.md", "qa/autonomous-source-sha256.txt",
    "tests/real_http_recovery.py", "tests/test_smoke.py", "tests/test_evaluator.py",
    "tests/fixtures/evaluator/CAND-01-native-evidence.json",
    "tests/fixtures/evaluator/CAND-06-native-evidence.json",
    "scripts/local_curator_report.py", "qa/four_cases.json",
    "docs/04-gim-requirements.md", "docs/06-gim-stakeholder-answers.md",
    "docs/09-delivery-roadmap.md", "docs/18-criteria-source-audit-20261021.md",
    "docs/19-sqlite-reservation-20261021.md",
    "docs/20-gim-scoring-pilot-20261022.md",
    "docs/21-ai-clarification-before-verdict-20261022.md",
    "docs/23-agent-contract-and-vk-fix-20261022.md",
    "docs/24-google-sheets-llm-vk-20261022.md",
    "docs/25-ilya-ux-release-check-20261022.md",
)
FORBIDDEN = {".git", ".venv", ".env", "data", "reports", "secret", "credentials"}

def build(destination=None):
    destination = Path(destination) if destination is not None else ROOT / "dist" / ZIP_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in FILES:
            path = source_path(name)
            if not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()):
                raise FileNotFoundError(name)
            if any(x.lower() in FORBIDDEN for x in Path(name).parts):
                raise ValueError("Forbidden path in release")
            data = path.read_bytes()
            z.writestr(PREFIX + name, data)
            manifest.append(hashlib.sha256(data).hexdigest() + "  " + name)
        z.writestr(PREFIX + "SHA256-MANIFEST.txt", "\n".join(manifest)+"\n")
    with zipfile.ZipFile(destination) as z:
        assert z.testzip() is None
    return destination

if __name__ == "__main__":
    p=build()
    print("ZIP=", p)
    print("SHA256=", hashlib.sha256(p.read_bytes()).hexdigest())
    with zipfile.ZipFile(p) as z: print("FILES=", len(z.namelist()), "ZIP_OK=", z.testzip() is None)
