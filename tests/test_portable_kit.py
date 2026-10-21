"""Offline regression checks for Ilya's no-secret portable QA bundle."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from scripts.make_portable import FILES, build as build_zip
from scripts.local_curator_report import build as curator_build
from scripts.qa_four_live import report_html

ROOT=Path(__file__).resolve().parent.parent


class PortableKitTests(unittest.TestCase):
    def test_cases_are_all_synthetic_and_have_expected_contract(self):
        cases=json.loads((ROOT/"qa/four_cases.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual([x["case_id"] for x in cases],
                         ["CAND-01", "CAND-05", "CAND-10", "CAND-06"])
        for case in cases:
            a=case["answers"]
            self.assertTrue(a["consent_personal_data"])
            self.assertTrue(a["age_over_18"])
            self.assertTrue(a["email"].endswith("@example.invalid"))
            self.assertGreaterEqual(len(a["motivation"]),10)
        self.assertIn("магистратур",cases[-1]["answers"]["course"])

    def test_zip_whitelist_never_contains_secrets_or_raw_private_materials(self):
        with tempfile.TemporaryDirectory() as temp:
            archive=build_zip(Path(temp)/"kit.zip")
            with zipfile.ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                names=z.namelist()
                self.assertIn("GIM_ILYA_PORTABLE/START-ILYA.cmd",names)
                self.assertIn("GIM_ILYA_PORTABLE/qa/four_cases.json",names)
                self.assertEqual(len(names),len(FILES)+1)
                for name in names:
                    self.assertNotIn(".env",name)
                    self.assertNotIn("materials/",name)
                    self.assertNotIn(".sqlite",name)
                    self.assertNotIn("pdf_files/",name)
                    self.assertNotIn("reports/",name)
                    self.assertNotIn(".git/",name)

    def test_qa_report_escapes_model_and_questionnaire_html(self):
        rows=[{"case_id":"CAND-01", "goal":"<script>alert(1)</script>",
               "input":{"motivation":"<img src=x onerror=alert(1)>"},
               "result":{"evaluation":{"evaluation_mode":"llm",
                                       "summary":"<script>evil()</script>",
                                       "evidence":[{"question_id":"motivation","reason":"test"}],
                                       "score":None}},
               "model":"deepseek-v4.1-flash",
               "seconds":10.3,"http_status":201,
               "technical_pass":True,"duplicate_status":"PASS"}]
        html=report_html(rows,"http://127.0.0.1:8876","2026-10-21")
        self.assertNotIn("<script>alert(1)</script>",html)
        self.assertNotIn("<img src=x",html)
        self.assertIn("&lt;script&gt;evil()",html)
        self.assertIn("Экспертная оценка Евы",html)

    def test_private_curator_report_contains_real_evaluation_only_from_sqlite(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"gim.sqlite3"
            with closing(sqlite3.connect(db)) as c, c:
                c.execute("CREATE TABLE applications (id TEXT PRIMARY KEY, "
                          "created_at TEXT, applicant_name TEXT, payload TEXT, evaluation TEXT)")
                c.execute("INSERT INTO applications VALUES(?,?,?,?,?)",
                          ("qa27-1","2026-10-21","<img src=x>",
                           json.dumps({"motivation":"<body>"}),
                           json.dumps({"evaluation_mode":"llm",
                                       "recommended_direction_label":"Организационное",
                                       "summary":"LLM действительный",
                                       "possible_problems":["уточнить график"],
                                       "evidence":[{"question_id":"motivation","reason":"<body>"}]})))
            path=Path(d)/"report.html"
            self.assertEqual(curator_build(db,path),1)
            html=path.read_text(encoding="utf-8")
            self.assertIn("Режим",html)
            self.assertIn("llm",html)
            self.assertNotIn("<img src=x>",html)
            self.assertIn("&lt;img src=x&gt;",html)


if __name__=="__main__":
    unittest.main()
