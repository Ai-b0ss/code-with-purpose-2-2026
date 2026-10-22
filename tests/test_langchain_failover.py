import unittest

from backend.evaluator import EvaluationError
from backend.langchain_adapter import FailoverChatAdapter
from backend.field_quality import review_fields


class Stub:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def complete(self, **kwargs):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FailoverTests(unittest.TestCase):
    def test_cloud_error_switches_to_next_model(self):
        first = Stub(EvaluationError("provider_http_503"))
        second = Stub('{"ok":true}')
        first.model = "cloud-a"
        second.model = "cloud-b"
        adapter = FailoverChatAdapter(primary=first, backups=(second,))
        self.assertEqual(adapter.complete(system="s", user="u", schema={"type":"object","required":["ok"]}), '{"ok":true}')
        self.assertEqual((first.calls, second.calls), (1, 1))
        self.assertEqual(adapter.last_model, "cloud-b")
        self.assertEqual(adapter.attempted_models, ["cloud-a", "cloud-b"])
        self.assertEqual(adapter.failed_attempts, [
            {"model": "cloud-a", "code": "provider_http_503", "count": 1}
        ])

    def test_invalid_json_switches_to_local(self):
        cloud = Stub("garbled")
        local = Stub('{"ok":true}')
        adapter = FailoverChatAdapter(primary=cloud, local=local)
        self.assertEqual(adapter.complete(system="s", user="u", schema={"type":"object","required":["ok"]}), '{"ok":true}')
        self.assertEqual(local.calls, 1)
        self.assertEqual(adapter.failed_attempts, [
            {"model": "test_adapter", "code": "invalid_model_json", "count": 1}
        ])

    def test_semantic_validation_switches_model(self):
        first = Stub('{"ok":true,"source":"invented"}')
        second = Stub('{"ok":true,"source":"verified"}')
        adapter = FailoverChatAdapter(primary=first, backups=(second,))
        def validate(raw):
            value = __import__("json").loads(raw)
            if value["source"] != "verified":
                raise EvaluationError("invalid_model_json")
            return value
        result = adapter.complete_validated(
            system="s", user="u", schema={"type":"object","required":["ok","source"]},
            validator=validate,
        )
        self.assertEqual(result["source"], "verified")
        self.assertEqual((first.calls, second.calls), (1, 1))

    def test_field_validator_repairs_compatible_format_before_raw_schema(self):
        answer = "Пошли вы все, тупой музей."
        local = Stub(__import__("json").dumps({"fields": [{
            "field_id": "motivation", "category": "abusive",
            "signals": ["abusive"], "evidence": answer,
        }]}, ensure_ascii=False))
        adapter = FailoverChatAdapter(primary=local)
        reviews = review_fields({"motivation": answer}, adapter)
        self.assertEqual(reviews[0]["category"], "abusive")
        self.assertEqual(reviews[0]["signals"], [])
        self.assertEqual(local.calls, 1)


if __name__ == "__main__":
    unittest.main()
