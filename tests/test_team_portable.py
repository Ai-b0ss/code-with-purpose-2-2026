"""Check team package integrity, contents and key handling without network."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import hashlib
import zipfile
from scripts.make_team_portable import build, ROOT, FILES, PREFIX, source_path

class TeamPackageTests(TestCase):
    def test_manifest_and_safe_contents(self):
        with TemporaryDirectory() as tmp:
            archive_path = build(Path(tmp) / "qa.zip")
            with zipfile.ZipFile(archive_path) as z:
                self.assertIsNone(z.testzip())
                self.assertEqual(set(z.namelist()), {*(PREFIX+n for n in FILES), PREFIX+"SHA256-MANIFEST.txt"})
                manifest=z.read(PREFIX+"SHA256-MANIFEST.txt").decode("utf-8")
                for n in FILES:
                    content=z.read(PREFIX+n)
                    self.assertEqual(content, source_path(n).read_bytes())
                    self.assertIn(hashlib.sha256(content).hexdigest()+"  "+n, manifest)
                    self.assertNotIn("..", Path(n).parts)
                names=" ".join(z.namelist()).casefold()
                for forbidden in (".env", ".git/", ".venv", "data/", "reports/", "xlsx", "docx"):
                    self.assertNotIn(forbidden, names)
                self.assertIn(b"application_reservations", z.read(PREFIX+"backend/app.py"))
                self.assertIn(b"questions_before_verdict", z.read(PREFIX+"backend/clarification_agent.py"))
                self.assertIn(b"def write_row", z.read(PREFIX+"backend/google_sheets.py"))
                self.assertIn(b"preliminary_verdict", z.read(PREFIX+"backend/evaluator.py"))
                self.assertIn(b"scorecard", z.read(PREFIX+"backend/evaluator.py"))
                self.assertIn(b"pilot_unvalidated", z.read(PREFIX+"backend/evaluator.py"))
                self.assertIn(b"class PerUserDispatcher", z.read(PREFIX+"vk_bot/dispatcher.py"))

    def test_launch_safe(self):
        entry=(ROOT/"scripts/launchers/team-start.ps1").read_text(encoding="utf-8-sig")
        launcher=(ROOT/"scripts/launchers/start-reformboss.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("-SkipProbe", entry)
        self.assertIn("Read-Host", launcher)
        self.assertIn('Remove-Item Env:GIM_LLM_API_KEY', launcher)
        self.assertIn("127.0.0.1", launcher)
        self.assertNotIn("--host 0.0.0.0", launcher)
