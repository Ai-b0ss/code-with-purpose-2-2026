"""Offline regressions for repeat/pair stability (issue #56). No network, no real model."""
import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from backend.evaluator import ChatCompletionsAdapter, EvaluationError, SYSTEM_PROMPT, evaluate
from backend.stability import is_stable, pair_difference, repeat_stability
from test_evaluator import FakeAdapter, candidate, response
from test_smoke import applicant


def run(data, mutate=None, repeats=1):
    raw = response(data)
    if mutate:
        mutate(raw)
    return [evaluate(data, adapter=FakeAdapter(json.dumps(raw, ensure_ascii=False)))
            for _ in range(repeats)]


def set_level(name, level):
    def mutate(raw):
        next(r for r in raw["scorecard"] if r["criterion"] == name)["level"] = level
        if level is None:
            next(r for r in raw["scorecard"] if r["criterion"] == name)["evidence_question_ids"] = []
    return mutate


class RepeatStabilityTests(unittest.TestCase):
    def test_identical_runs_are_stable_with_unique_run_ids(self):
        report = repeat_stability(run(candidate(), repeats=5))
        self.assertEqual(report["runs"], 5)
        self.assertTrue(report["versions_consistent"])
        self.assertTrue(report["run_ids_unique"])
        self.assertEqual(report["score_range"], 0)
        self.assertTrue(is_stable(report))

    def test_flipped_criterion_and_verdict_are_detected(self):
        data = candidate()
        runs = run(data, repeats=3) + run(data, set_level("skills", 3))
        report = repeat_stability(runs)
        self.assertEqual(report["criteria"]["skills"]["agreement"], 0.75)
        self.assertEqual(report["criteria"]["skills"]["level_range"], 1)
        self.assertEqual(report["criteria"]["motivation"]["agreement"], 1.0)
        self.assertFalse(is_stable(report))
        # An explicit tolerance is opt-in, never the default.
        # Rounded score absorbs 2 -> 3 on one criterion, so only the criterion moved.
        self.assertEqual(report["score_agreement"], 1.0)
        self.assertTrue(is_stable(report, min_agreement=0.75, max_level_range=1))
        self.assertFalse(is_stable(report, min_agreement=0.75, max_level_range=0))

        changed = copy.deepcopy(run(data)[0])
        changed["preliminary_verdict"] = "suggest_interview"
        changed["recommended_direction"] = "project"
        report = repeat_stability(run(data, repeats=2) + [changed])
        self.assertAlmostEqual(report["verdict_agreement"], 2 / 3)
        self.assertAlmostEqual(report["direction_agreement"], 2 / 3)
        self.assertFalse(is_stable(report))

    def test_null_is_a_distinct_outcome_not_a_zero(self):
        data = candidate()
        report = repeat_stability(run(data) + run(data, set_level("skills", None)))
        skills = report["criteria"]["skills"]
        self.assertEqual(skills["null_runs"], 1)
        self.assertEqual(skills["agreement"], 0.5)
        self.assertEqual(skills["level_range"], 0)  # one numeric value; null is not 0
        self.assertEqual(report["criteria"]["motivation"]["null_runs"], 0)
        self.assertFalse(is_stable(report))

    def test_scoreless_runs_are_counted_not_ignored(self):
        data = candidate()

        def drop_known(raw):
            for item in raw["scorecard"]:
                if item["criterion"] in {"skills", "form_and_contact", "logistics", "availability"}:
                    item["level"], item["evidence_question_ids"] = None, []

        report = repeat_stability(run(data) + run(data, drop_known))
        self.assertEqual(report["score_null_runs"], 1)
        self.assertEqual(report["score_agreement"], 0.5)

    def test_missing_scorecard_entry_is_flagged_as_absent(self):
        data = candidate()
        legacy = copy.deepcopy(run(data)[0])
        legacy["scorecard"] = legacy["scorecard"][:4]
        report = repeat_stability([run(data)[0], legacy])
        self.assertEqual(report["criteria"]["logistics"]["absent_runs"], 1)
        self.assertFalse(is_stable(report))

    def test_mixed_prompt_model_or_temperature_is_not_comparable(self):
        data = candidate()
        runs = run(data, repeats=3)
        runs[1]["provenance"]["prompt_hash"] = "0" * 64
        runs[2]["provenance"]["model"] = "other-model"
        report = repeat_stability(runs)
        self.assertFalse(report["versions_consistent"])
        self.assertEqual(report["version_fields_differing"], ["prompt_hash", "model"])
        self.assertFalse(is_stable(report))

    def test_duplicated_or_missing_run_id_is_not_an_independent_repeat(self):
        runs = run(candidate(), repeats=2)
        runs[1]["provenance"]["run_id"] = runs[0]["provenance"]["run_id"]
        self.assertFalse(repeat_stability(runs)["run_ids_unique"])
        del runs[1]["provenance"]["run_id"]
        self.assertFalse(repeat_stability(runs)["run_ids_unique"])

    def test_single_run_is_rejected(self):
        with self.assertRaises(ValueError):
            repeat_stability(run(candidate()))


class PairStabilityTests(unittest.TestCase):
    def test_pair_reports_deltas_and_known_unknown_changes(self):
        data = candidate()
        base = run(data)[0]
        same = pair_difference(base, run(data)[0])
        self.assertTrue(same["direction_equal"] and same["verdict_equal"])
        self.assertEqual(same["score_delta"], 0)
        self.assertEqual(same["max_criterion_delta"], 0)

        shifted = pair_difference(base, run(data, set_level("skills", 0))[0])
        self.assertEqual(shifted["criterion_deltas"]["skills"], 2)
        self.assertEqual(shifted["max_criterion_delta"], 2)
        self.assertEqual(shifted["known_status_changed"], [])

        unknown = pair_difference(base, run(data, set_level("skills", None))[0])
        self.assertIsNone(unknown["criterion_deltas"]["skills"])
        self.assertEqual(unknown["known_status_changed"], ["skills"])

    def test_identity_only_changes_cannot_reach_the_model(self):
        """Names, ids and contact values are removed before the request is built."""
        first = candidate()
        second = first.model_copy(update={
            "applicant_name": "Другой Вымышленный Человек", "birth_date": "1999-12-31",
            "email": "second@example.test", "vk_url": "https://vk.com/second"})
        adapters = [FakeAdapter(json.dumps(response(d), ensure_ascii=False)) for d in (first, second)]
        for data, adapter in zip((first, second), adapters):
            evaluate(data, adapter=adapter)
        self.assertEqual(adapters[0].calls[0]["user"], adapters[1].calls[0]["user"])
        self.assertEqual(adapters[0].calls[0]["system"], adapters[1].calls[0]["system"])

    def test_identity_pasted_into_free_text_is_masked_identically(self):
        base = applicant(motivation="Меня зовут Имя Вымышленное, интересуюсь историей музея.",
                         applicant_name="Имя Вымышленное")
        other = applicant(motivation="Меня зовут Иной Человек, интересуюсь историей музея.",
                          applicant_name="Иной Человек")
        sent = []
        for data in (base, other):
            adapter = FakeAdapter(json.dumps(response(data), ensure_ascii=False))
            try:
                evaluate(data, adapter=adapter)
            except EvaluationError:
                pass  # evidence quote differs after masking; the request is what matters
            sent.append(adapter.calls[0]["user"])
        self.assertEqual(sent[0], sent[1])

    def test_nuisance_field_changes_leave_semantic_payload_unchanged(self):
        """University name alone must not alter anything but its own field."""
        a = json.loads(self._payload(candidate()))["answers"]
        b = json.loads(self._payload(candidate().model_copy(
            update={"university_other": "Совсем другой вуз"})))["answers"]
        differing = {k for k in a if a[k] != b[k]}
        self.assertEqual(differing, {"university_other"})

    @staticmethod
    def _payload(data):
        adapter = FakeAdapter(json.dumps(response(data), ensure_ascii=False))
        evaluate(data, adapter=adapter)
        return adapter.calls[0]["user"]


class RequestDeterminismTests(unittest.TestCase):
    def test_http_requests_for_repeats_are_byte_identical_at_temperature_zero(self):
        data = candidate()
        content = json.dumps(response(data), ensure_ascii=False)
        bodies = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.dumps({"choices": [{"finish_reason": "stop",
                                                "message": {"content": content}}]}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                adapter = ChatCompletionsAdapter(
                    f"http://127.0.0.1:{server.server_port}/v1/chat/completions", "local")
                results = [evaluate(data, adapter=adapter) for _ in range(3)]
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(len(set(bodies)), 1)
        self.assertEqual(json.loads(bodies[0])["temperature"], 0)
        self.assertTrue(is_stable(repeat_stability(results)))

    def test_prompt_provenance_is_pinned_to_the_actual_prompt(self):
        import hashlib
        result = run(candidate())[0]
        self.assertEqual(result["provenance"]["prompt_hash"],
                         hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest())
        self.assertEqual(result["provenance"]["temperature"], 0)
        self.assertEqual(result["provenance"]["prompt_version"], "gim-final-v2")


if __name__ == "__main__":
    unittest.main()
