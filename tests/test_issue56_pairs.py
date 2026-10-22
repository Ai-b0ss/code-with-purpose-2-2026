"""Issue #56 synthetic pair matrix and negative evaluator contracts. Offline-only."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from backend.evaluator import ChatCompletionsAdapter, CRITERION_IDS, EvaluationError, evaluate
from backend.stability import pair_difference
from issue56_support import load_fixture, run, sent_answers, differing_keys
from test_evaluator import FakeAdapter, response
from test_smoke import applicant


class Issue56PairMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrix = load_fixture("pair-matrix.json")

    def test_required_pairs_are_present_and_synthetic(self):
        ids = {item["id"] for item in self.matrix["pairs"]}
        required = {
            "same_skills_other_university_and_faculty",
            "first_year_bachelor_vs_first_year_master",
            "full_time_with_confirmed_weekends_vs_unknown_schedule",
            "no_experience_vs_concrete_skills",
            "vk_url_open_vs_closed_profile",
            "different_names_and_birth_dates_same_answers",
            "zero_hours_vs_sixteen_hours",
            "null_level_vs_zero_level_same_answers",
            "sparse_vs_complete_questionnaire",
            "minor_motivation_paraphrase",
        }
        self.assertTrue(required <= ids)
        self.assertEqual(len(ids), len(self.matrix["pairs"]))
        self.assertTrue(all("@example.test" in x["email"]
                            for x in [self.matrix["synthetic_identity"]]))

    def test_all_pairs_declared_changes_and_server_scores(self):
        for pair in self.matrix["pairs"]:
            with self.subTest(pair=pair["id"]):
                result = []
                payload = []
                for side in ("a", "b"):
                    fields = dict(self.matrix.get("base", {}))
                    fields.update(pair[side])
                    data = applicant(**fields)
                    evaluation, adapter = run(data, levels=pair["levels_" + side])
                    result.append(evaluation)
                    payload.append(sent_answers(adapter))
                    self.assertEqual(evaluation["score"], pair["expected"]["score_" + side])
                    self.assertEqual(evaluation["score_status"],
                                     pair["expected"]["status_" + side])
                    self.assertEqual({x["criterion"] for x in evaluation["scorecard"]},
                                     set(CRITERION_IDS))
                self.assertEqual(differing_keys(*payload), set(pair["payload_diff"]))
                changed = {
                    criterion for criterion in CRITERION_IDS
                    if pair["levels_a"][criterion] != pair["levels_b"][criterion]
                }
                self.assertTrue(changed <= set(pair["may_differ"]))
                delta = pair_difference(*result)
                for criterion in set(CRITERION_IDS) - set(pair["may_differ"]):
                    self.assertEqual(delta["criterion_deltas"][criterion], 0)
                for criterion in CRITERION_IDS:
                    if pair["levels_a"][criterion] is None or pair["levels_b"][criterion] is None:
                        self.assertIsNone(delta["criterion_deltas"][criterion])
                if not pair["may_differ"]:
                    self.assertEqual(delta["score_delta"], 0)

    def test_irrelevant_identifiers_never_change_model_payload(self):
        selected = next(x for x in self.matrix["pairs"]
                        if x["id"] == "different_names_and_birth_dates_same_answers")
        a, adapter_a = run(applicant(**selected["a"]), levels=selected["levels_a"])
        b, adapter_b = run(applicant(**selected["b"]), levels=selected["levels_b"])
        self.assertEqual(adapter_a.calls[0]["user"], adapter_b.calls[0]["user"])
        self.assertEqual(a["scorecard"], b["scorecard"])
        self.assertNotEqual(a["provenance"]["run_id"], b["provenance"]["run_id"])

    def test_null_and_zero_are_not_same_score_or_status(self):
        pair = next(p for p in self.matrix["pairs"]
                    if p["id"] == "null_level_vs_zero_level_same_answers")
        a, _ = run(applicant(), levels=pair["levels_a"])
        b, _ = run(applicant(), levels=pair["levels_b"])
        self.assertIsNone(next(x["level"] for x in a["scorecard"]
                               if x["criterion"] == "skills"))
        self.assertEqual(next(x["level"] for x in b["scorecard"]
                              if x["criterion"] == "skills"), 0)
        self.assertNotEqual(a["score"], b["score"])

    def test_fake_injection_cannot_make_invalid_evidence_valid(self):
        for injection in self.matrix["injections"]:
            with self.subTest(payload=injection[:24]):
                data = applicant(motivation=injection)
                forged = response(data)
                forged["evidence"] = [{
                    "question_id": "made_up_question",
                    "reason": "нет такой исходной цитаты",
                }]
                forged["score"] = 3
                for item in forged["scorecard"]:
                    item["level"] = 3
                    item["evidence_question_ids"] = ["made_up_question"]
                with self.assertRaises(EvaluationError):
                    evaluate(data, adapter=FakeAdapter(json.dumps(forged, ensure_ascii=False)))


class Issue56TransportPrivacyTests(unittest.TestCase):
    def test_actual_mock_http_body_contains_no_identifiers_or_contacts(self):
        identity = load_fixture("pair-matrix.json")["synthetic_identity"]
        data = applicant(
            applicant_name=identity["applicant_name"],
            email=identity["email"],
            vk_url=identity["vk_url"],
            birth_date=identity["birth_date"],
            motivation=("Интересуюсь музеем; моя почта " + identity["email"] +
                        ", телефон " + identity["phone"] + ", VK " + identity["vk_url"] +
                        ", application_id " + identity["application_id"] + "."),
        )
        fake = json.dumps(response(data), ensure_ascii=False)
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests.append(body.decode("utf-8"))
                payload = json.dumps({"choices": [{"finish_reason": "stop",
                                                   "message": {"content": fake}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                adapter = ChatCompletionsAdapter(
                    f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                    "mock-issue56")
                try:
                    evaluate(data, adapter=adapter)
                except EvaluationError:
                    # A mocked answer quoting raw PII may fail strict evidence check.
                    # The HTTP request is the invariant tested here.
                    pass
            finally:
                server.shutdown()
                thread.join()
        self.assertTrue(requests)
        for raw in requests:
            request = json.loads(raw)
            self.assertEqual(request["temperature"], 0)
            self.assertEqual(request["model"], "mock-issue56")
            for forbidden in identity.values():
                self.assertNotIn(forbidden, raw)
            for field in ("applicant_name", "birth_date", "email",
                          "vk_url", "application_id"):
                self.assertNotIn('"' + field + '"', raw)


if __name__ == "__main__":
    unittest.main()
