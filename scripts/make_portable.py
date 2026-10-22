"""Build a source-portable Windows kit from an explicit allowlist.

Requires Python 3.11+ on receiving Windows PC and Internet for first pip install.
Never includes private case materials, Git history, local databases or API keys.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent.parent
FILES=(
    "START-ILYA.cmd",
    "QA-4-CASES.cmd",
    "CURATOR-REPORT.cmd",
    "portable-start.ps1",
    "start-reformboss.ps1",
    "requirements.txt", "docs/privacy-and-security.md", "docs/operations.md", "docs/gim-question-coverage.md",
    "README-ILYA.md",
    "backend/__init__.py",
    "backend/app.py",
    "backend/evaluator.py",
    "backend/availability_policy.py",
    "backend/clarification_agent.py",
    "backend/google_sheets.py",
    "backend/curator.py", "backend/security.py", "backend/candidate_feedback.py", "backend/eligibility.py", "backend/analytics.py", "frontend/admin.html",
    "frontend/index.html",
    "scripts/check_reformboss.py",
    "scripts/qa_four_live.py",
    "scripts/local_curator_report.py",
    "qa/four_cases.json",
    "docs/08-synthetic-candidates.md",
)
ZIP_NAME="GIM_ILYA_PORTABLE_20261021.zip"


def build(destination: Path | None = None) -> Path:
    destination=destination or ROOT/"dist"/ZIP_NAME
    destination.parent.mkdir(parents=True,exist_ok=True)
    manifest=[]
    with zipfile.ZipFile(destination,"w",compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for name in FILES:
            src=ROOT/name
            if not src.is_file() or not src.resolve().is_relative_to(ROOT.resolve()):
                raise FileNotFoundError(f"Missing or invalid packaged file: {name}")
            data=src.read_bytes()
            # Archive does not include generated reports, raw XLSX, .env or DB.
            archive.writestr("GIM_ILYA_PORTABLE/"+name,data)
            manifest.append(hashlib.sha256(data).hexdigest()+"  "+name)
        archive.writestr("GIM_ILYA_PORTABLE/SHA256-MANIFEST.txt",
                         "\n".join(manifest)+"\n")
    return destination


if __name__=="__main__":
    path=build()
    print("PORTABLE KIT:",path)
    print("SHA256:",hashlib.sha256(path.read_bytes()).hexdigest())
    with zipfile.ZipFile(path) as archive:
        print("FILE COUNT:",len(archive.namelist()))
        print("ZIP VALID:",archive.testzip() is None)

