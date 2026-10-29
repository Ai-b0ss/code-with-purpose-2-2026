"""Regression test: the ReformBoss probe must load backend from repo root.

The test never calls the provider and never needs a real API key.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class ReformBossLauncherTests(unittest.TestCase):
    def test_probe_uses_the_same_timeout_as_server_environment(self):
        from scripts import check_reformboss
        for configured, arguments, expected in (
            (None, [], 300.0),
            ("120", [], 120.0),
            ("120", ["--timeout", "240"], 240.0),
        ):
            with self.subTest(configured=configured, arguments=arguments):
                with patch.dict(os.environ, {"GIM_LLM_API_KEY": "test-only"}):
                    os.environ.pop("GIM_LLM_TIMEOUT", None)
                    if configured is not None:
                        os.environ["GIM_LLM_TIMEOUT"] = configured
                    with patch("sys.argv", ["probe", *arguments]), patch.object(
                        check_reformboss, "probe_model", return_value=True
                    ) as probe:
                        self.assertEqual(check_reformboss.main(), 0)
                    self.assertEqual(probe.call_args.args[-1], expected)

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

    def test_default_model_matches_authenticated_reformboss_catalog(self):
        from scripts.check_reformboss import MODELS
        self.assertEqual(MODELS[0], "deepseek-v4.1-flash")
        self.assertNotIn("deepseek-v4.1-flash-1017", MODELS)
        launcher = (ROOT / "scripts" / "launchers" / "start-reformboss.ps1").read_text(encoding="utf-8")
        self.assertIn('[string]$Model = "deepseek-v4.1-flash"', launcher)
        self.assertNotIn("deepseek-v4.1-flash-1017", launcher)

    def test_launcher_plain_input_without_securestring_conversion(self):
        launcher = (ROOT / "scripts" / "launchers" / "start-reformboss.ps1").read_text(encoding="utf-8")
        self.assertIn('(Read-Host "Enter ReformBoss API key (VISIBLE)").Trim()', launcher)
        self.assertNotIn("-AsSecureString", launcher)
        self.assertNotIn("SecureStringToBSTR(", launcher)
        self.assertIn("Remove-Item Env:GIM_LLM_API_KEY", launcher)

    def test_launcher_avoids_killing_process_on_busy_port(self):
        launcher = (ROOT / "scripts" / "launchers" / "start-reformboss.ps1").read_text(encoding="utf-8")
        self.assertIn('[int]$Port = 8765', launcher)
        self.assertIn('TcpListener', launcher)
        self.assertIn('$selectedPort', launcher)
        self.assertIn('Port $Port is busy', launcher)
        self.assertIn('--port $selectedPort', launcher)
        self.assertNotIn('taskkill', launcher.lower())

    def test_powershell_launcher_uses_module_execution(self):
        launcher = (ROOT / "scripts" / "launchers" / "start-reformboss.ps1").read_text(encoding="utf-8")
        self.assertIn(
            '$probe = @("-m", "scripts.check_reformboss", "--model", $Model)',
            launcher,
        )


if __name__ == "__main__":
    unittest.main()
