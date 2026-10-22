"""Fast, offline contract tests for the historical evaluation script.

The script is intentionally imported lazily: this branch may be checked out before
the script implementation is installed, but the contract tests run as soon as the
public module is present.  No real workbook, provider, network, or paid API is used.
"""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


try:
    from scripts.evaluate_historical import (
        anonymize_record,
        build_application,
        compute_metrics,
        evaluate_records,
        split_records,
        write_reports,
    )
    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # The implementation may land after this contract.
    _IMPORT_ERROR = exc


def _call_records(function, records, **kwargs):
    """Pass only supported keyword arguments while keeping the public call readable."""
    parameters = inspect.signature(function).parameters
    if any(parameter.kind is parameter.VAR_KEYWORD for parameter in parameters.values()):
        accepted = kwargs
    else:
        accepted = {key: value for key, value in kwargs.items() if key in parameters}
    return function(records, **accepted)


def _as_plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _text(value):
    return json.dumps(_as_plain(value), ensure_ascii=False, sort_keys=True, default=str)


def _partition(split):
    """Normalise the two common split return shapes for contract assertions."""
    if isinstance(split, (tuple, list)) and len(split) == 2:
        return list(split[0]), list(split[1])
    if isinstance(split, dict):
        for left, right in (("train", "holdout"), ("tune", "test"), ("tune", "holdout")):
            if left in split and right in split:
                return list(split[left]), list(split[right])
        labels = split
        if labels and all(label in {"train", "tune", "holdout", "test"} for label in labels.values()):
            return (
                [candidate_id for candidate_id, label in labels.items() if label in {"train", "tune"}],
                [candidate_id for candidate_id, label in labels.items() if label in {"holdout", "test"}],
            )
    raise AssertionError(f"Unsupported split shape: {type(split)!r}")


def _mapping(value):
    value = _as_plain(value)
    if not isinstance(value, dict):
        raise AssertionError(f"Expected a mapping, got {type(value)!r}")
    return value


def _records():
    return [
        {
            "candidate_id": "cand-2024-a",
            "season": "2024",
            "expert_score": 1,
            "fingerprint": "fp-a",
            "applicant_name": "Анна Секретова",
            "email": "anna@example.test",
            "vk_url": "https://vk.example/id1",
            "raw_text": "RAW_ANSWER_A",
            "answers": {"motivation": "RAW_ANSWER_A", "expectations": "хочу помогать"},
        },
        {
            "candidate_id": "cand-2024-b",
            "season": "2024",
            "expert_score": 2,
            "fingerprint": "fp-b",
            "applicant_name": "Борис Секретов",
            "email": "boris@example.test",
            "vk_url": "https://vk.example/id2",
            "raw_text": "RAW_ANSWER_B",
            "answers": {"motivation": "RAW_ANSWER_B", "expectations": "интересны проекты"},
        },
        {
            "candidate_id": "cand-2025-a",
            "season": "2025",
            "expert_score": 2,
            "fingerprint": "fp-c",
            "applicant_name": "Вера Секретова",
            "email": "vera@example.test",
            "vk_url": "https://vk.example/id3",
            "raw_text": "RAW_ANSWER_C",
            "answers": {"motivation": "RAW_ANSWER_C", "expectations": "люблю архивы"},
        },
        {
            "candidate_id": "cand-2025-b",
            "season": "2025",
            "expert_score": 3,
            "fingerprint": "fp-d",
            "applicant_name": "Глеб Секретов",
            "email": "gleb@example.test",
            "vk_url": "https://vk.example/id4",
            "raw_text": "RAW_ANSWER_D",
            "answers": {"motivation": "RAW_ANSWER_D", "expectations": "медиа"},
        },
    ]


@unittest.skipIf(_IMPORT_ERROR is not None, f"scripts.evaluate_historical unavailable: {_IMPORT_ERROR}")
class EvaluationBenchmarkTests(unittest.TestCase):
    def test_metrics_have_four_by_four_matrix_mae_coverage_and_class_scores(self):
        result = _mapping(compute_metrics([0, 1, 2, 3], [0, None, 3, 3]))

        self.assertEqual(result["confusion_matrix"], [[1, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1], [0, 0, 0, 1]])
        self.assertEqual(result["numeric_pairs"], 3)
        self.assertEqual(result["null_count"], 1)
        self.assertEqual(result["coverage"], 0.75)
        self.assertAlmostEqual(result["mae"], 1 / 3)
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(len(result["per_class"]), 4)
        self.assertEqual(result["per_class"][1]["recall"], 0)
        self.assertAlmostEqual(result["per_class"][3]["f1"], 2 / 3)
        self.assertEqual(result["large_errors"], 0)

    def test_split_is_deterministic_and_keeps_2025_as_holdout(self):
        records = _records()
        first = _partition(split_records(records, random_state=42))
        second = _partition(split_records(list(reversed(records)), random_state=42))

        self.assertEqual(set(first[0]), set(second[0]))
        self.assertEqual(set(first[1]), set(second[1]))
        self.assertTrue(first[1])
        self.assertTrue(set(first[0]).isdisjoint(first[1]))
        holdout = {record["candidate_id"] for record in records if record["season"] == "2025"}
        self.assertEqual(set(first[1]), holdout)

    def test_baselines_are_reported_and_use_tune_only(self):
        records = _records()
        split = split_records(records, random_state=42)
        kwargs = {"mode": "mock", "random_state": 42, "split": split}
        result = _mapping(_call_records(evaluate_records, records, **kwargs))
        text = _text(result).lower()

        self.assertIn("majority", text)
        self.assertIn("fixed", text)
        self.assertIn("2", text)
        self.assertNotIn("anna@example.test", text)
        self.assertNotIn("RAW_ANSWER_A", text)

    def test_anonymization_removes_name_contact_vk_and_builds_local_application(self):
        record = _records()[0]
        anonymized = _mapping(anonymize_record(record))
        anonymized_text = _text(anonymized)
        for secret in ("Анна Секретова", "anna@example.test", "https://vk.example/id1", "RAW_ANSWER_A"):
            self.assertNotIn(secret, anonymized_text)
        for key in ("applicant_name", "name", "email", "phone", "vk_url", "contact"):
            self.assertNotIn(key, anonymized)

        application = _as_plain(build_application(record))
        self.assertTrue(application)
        self.assertIn("motivation", application)

    def test_mock_results_and_reports_are_id_only_and_contain_metrics_confusion(self):
        records = _records()
        split = split_records(records, random_state=42)
        result = _mapping(_call_records(evaluate_records, records, mode="mock", random_state=42, split=split))
        result_text = _text(result)
        for secret in ("Анна Секретова", "anna@example.test", "https://vk.example/id1", "RAW_ANSWER_A"):
            self.assertNotIn(secret, result_text)

        anomalies = result.get("anomalies", result.get("flags", []))
        self.assertIsInstance(anomalies, (list, tuple))
        candidate_ids = {
            _mapping(anonymize_record(record))["candidate_id"] for record in records
        }
        for anomaly in anomalies:
            if isinstance(anomaly, dict):
                self.assertEqual(
                    set(anomaly),
                    {"candidate_id", "y_true", "y_pred", "abs_error", "explanation"},
                )
                self.assertIn(anomaly["candidate_id"], candidate_ids)
            else:
                self.assertIn(anomaly, candidate_ids)

        with tempfile.TemporaryDirectory() as directory:
            output = write_reports(result, Path(directory))
            paths = [Path(path) for path in output] if isinstance(output, (list, tuple, set)) else list(Path(directory).rglob("*"))
            files = [path for path in paths if path.is_file()]
            self.assertTrue(files)
            report_text = "\n".join(path.read_text(encoding="utf-8") for path in files)
            self.assertRegex(report_text.lower(), r"metrics|mae")
            self.assertRegex(report_text.lower(), r"confusion")
            for secret in ("Анна Секретова", "anna@example.test", "https://vk.example/id1", "RAW_ANSWER_A"):
                self.assertNotIn(secret, report_text)


if __name__ == "__main__":
    unittest.main()
