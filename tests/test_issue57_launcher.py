"""Issue #57: the launcher must use the operator-selected port in both modes."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class LauncherPortTests(unittest.TestCase):
    def test_live_launcher_forwards_requested_port(self):
        script = (ROOT / "scripts" / "launchers" / "team-start.ps1").read_text(encoding="utf-8")
        self.assertIn("param([switch]$Demo, [int]$Port = 8876)", script)
        self.assertIn("-SkipProbe -Port $Port -TimeoutSeconds 180", script)
        self.assertNotIn("-SkipProbe -Port 8876 -TimeoutSeconds 180", script)

    def test_demo_launcher_uses_same_requested_port(self):
        script = (ROOT / "scripts" / "launchers" / "team-start.ps1").read_text(encoding="utf-8")
        self.assertIn("--port $Port", script)


if __name__ == "__main__":
    unittest.main()
