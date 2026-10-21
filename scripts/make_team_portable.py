"""Safe reproducible team QA source package, without credentials or real candidate data."""
from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
ZIP_NAME = "GIM_TEAM_QA_AGENT_20261022.zip"
PREFIX = "GIM_TEAM_QA/"
FILES = (
    "START-TEAM.cmd", "team-start.ps1", "README-TEAM.md",
    "QA-4-CASES.cmd", "CURATOR-REPORT.cmd",
    "portable-start.ps1", "start-reformboss.ps1", "requirements.txt",
    "backend/__init__.py", "backend/app.py", "backend/evaluator.py",
    "backend/availability_policy.py", "backend/clarification_agent.py", "frontend/index.html",
    "scripts/check_reformboss.py", "scripts/qa_four_live.py",
    "scripts/local_curator_report.py", "qa/four_cases.json",
    "docs/04-gim-requirements.md", "docs/06-gim-stakeholder-answers.md",
    "docs/09-delivery-roadmap.md", "docs/18-criteria-source-audit-20261021.md",
    "docs/19-sqlite-reservation-20261021.md",
    "docs/20-gim-scoring-pilot-20261022.md",
    "docs/21-ai-clarification-before-verdict-20261022.md",
)
FORBIDDEN = {".git", ".venv", ".env", "data", "reports", "secret", "credentials"}

def build(destination=None):
    destination = Path(destination) if destination is not None else ROOT / "dist" / ZIP_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = []
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in FILES:
            path = ROOT / name
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
