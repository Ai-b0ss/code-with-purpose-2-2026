"""Static checks for web wait state."""
from pathlib import Path
import unittest
PAGE=(Path(__file__).resolve().parents[1]/"frontend/index.html").read_text(encoding="utf-8")
class FrontendWaitStateTests(unittest.TestCase):
    def test_accessible_status(self):
        self.assertIn('id="submitLoading" hidden role="status" aria-live="polite"',PAGE)
        self.assertIn(".submit-loading[hidden] { display: none; }",PAGE)
    def test_lock_unlock(self):
        for phrase in ("prevBtn.disabled = true;","prevBtn.disabled = false;","if (submitBtn.disabled) return;",
                       "if (submitLoading) submitLoading.hidden = false;",
                       "if (submitLoading) submitLoading.hidden = true;"):
            self.assertIn(phrase,PAGE)
    def test_duration_disclaimer(self):
        self.assertIn("Обработка может занять несколько минут",PAGE)
if __name__=="__main__":
    unittest.main()
