"""Regression test: the ReformBoss probe must load backend from repo root.

The test never calls the provider and never needs a real API key.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReformBossLauncherTests(unittest.TestCase):
    def test_probe_module_imports_backend_from_repo_root(self):
        env = os.environ.copy()
        env.pop("GIM_LLM_API_KEY", None)
        completed = subprocess.run(
            [sys.executable, "-m", "scripts.check_reformboss"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(
            completed.returncode, 2,
            completed.stderr.decode("utf-8", errors="replace"),
        )
        self.assertNotIn(b"ModuleNotFoundError", completed.stderr)
        self.assertNotIn(b"Traceback", completed.stderr)

    def test_powershell_launcher_uses_module_execution(self):
        launcher = (ROOT / "start-reformboss.ps1").read_text(encoding="utf-8")
        self.assertIn(
            '$probe = @("-m", "scripts.check_reformboss", "--model", $Model)',
            launcher,
        )


if __name__ == "__main__":
    unittest.main()
