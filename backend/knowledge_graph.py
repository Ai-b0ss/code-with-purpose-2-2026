"""Small source-attributed graph retrieval for the museum evaluation harness."""
from __future__ import annotations

import json
import re
from pathlib import Path


GRAPH_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "gim_graph.json"


def query_from_field_reviews(answers: dict, reviews: list[dict]) -> str:
    """Use classified answer content; rejected text cannot steer graph retrieval."""
    policy = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))["retrieval_policy"]
    parts = []
    for review in reviews:
        field_id = review["field_id"]
        root, *path = field_id.split(".")
        value = answers.get(root)
        for key in path:
            if isinstance(value, dict):
                value = value.get(key)
            elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
                value = value[int(key)]
            else:
                value = None
                break
        if review["category"] in policy["answer_categories"] and isinstance(value, str):
            parts.append(value)
        hint = policy["field_query_hints"].get(root)
        if hint and value not in (None, "", [], {}):
            parts.append(hint)
        allowed_signals = (review.get("signals", [])
                           if review["category"] in policy["answer_categories"] else
                           policy["signal_hint_exceptions"].get(review["category"], []))
        for signal in review.get("signals", []):
            if signal not in allowed_signals:
                continue
            hint = policy["signal_query_hints"].get(signal)
            if hint:
                parts.append(hint)
    return " ".join(dict.fromkeys(parts))


def retrieve_facts(query: str, *, subject: str | None = None, limit: int = 7,
                   signal_ids: list[str] | None = None,
                   field_categories: dict[str, str] | None = None) -> list[dict]:
    graph = json.loads(GRAPH_PATH.read_text(encoding="utf-8"))
    policy = graph["retrieval_policy"]
    directions = {fact["subject"] for fact in graph["triples"] if fact["predicate"] == "task"}
    words = {w for w in re.findall(r"[\wёЁ]+", query.casefold())
             if len(w) >= policy["min_query_token_length"]}
    ranked = []
    active_signals = set(signal_ids or [])
    field_categories = field_categories or {}
    for fact in graph["triples"]:
        if (fact.get("requires_any_signal")
                and not active_signals.intersection(fact["requires_any_signal"])):
            continue
        field_requirement = fact.get("requires_field_category")
        if (field_requirement and field_categories.get(field_requirement["field_id"])
                not in field_requirement["categories"]):
            continue
        if fact["subject"] in directions and fact["subject"] != subject:
            # A mention of another volunteer *project* or a visitor does not
            # make that museum direction relevant to this application.
            continue
        topics = set(fact["topics"])
        prefix = policy["topic_prefix_length"]
        overlap = sum(any(word.startswith(topic[:prefix]) or topic.startswith(word[:prefix])
                          for topic in topics) for word in words)
        score = overlap + (policy["selected_direction_boost"]
                           if subject and fact["subject"] == subject else 0)
        if score:
            ranked.append((score, fact))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [fact for _, fact in ranked[:limit]]
