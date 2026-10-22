"""Offline stability analysis of repeated evaluator runs (issue #56).

Pure functions over already produced `evaluate()` results: no network, no
provider, no candidate text. They answer "did the same input give the same
curator-visible outcome?" and "which fields moved between two inputs?".
A real repeated series with a live model still has to be run separately.
"""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from backend.evaluator import CRITERION_IDS

VERSION_KEYS = ("evaluation_schema_version", "score_rubric", "prompt_hash",
                "prompt_version", "model", "field_interpretation_version", "temperature")


def _levels(result: dict) -> dict:
    return {r["criterion"]: r["level"] for r in result.get("scorecard", [])}


def _agreement(values: list) -> float:
    """Share of runs equal to the most common value (1.0 = fully stable)."""
    return Counter(map(repr, values)).most_common(1)[0][1] / len(values)


def _numeric_range(values: list) -> int | None:
    numbers = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return max(numbers) - min(numbers) if numbers else None


def repeat_stability(results: Iterable[dict]) -> dict:
    """Summarize N runs of the SAME input. Null is a value, not a missing run."""
    runs = list(results)
    if len(runs) < 2:
        raise ValueError("at least two runs are required")
    provenance = [r.get("provenance") or {} for r in runs]
    run_ids = [p.get("run_id") for p in provenance]
    criteria = {}
    for name in CRITERION_IDS:
        values = [_levels(r).get(name, "absent") for r in runs]
        criteria[name] = {
            "agreement": _agreement(values),
            "level_range": _numeric_range(values),
            "null_runs": sum(v is None for v in values),
            "absent_runs": sum(v == "absent" for v in values),
        }
    scores = [r.get("score") for r in runs]
    return {
        "runs": len(runs),
        "direction_agreement": _agreement([r.get("recommended_direction") for r in runs]),
        "verdict_agreement": _agreement([r.get("preliminary_verdict") for r in runs]),
        "score_agreement": _agreement(scores),
        "score_range": _numeric_range(scores),
        "score_null_runs": sum(s is None for s in scores),
        "criteria": criteria,
        "versions_consistent": all(
            len({repr(p.get(key)) for p in provenance}) == 1 for key in VERSION_KEYS),
        "version_fields_differing": [
            key for key in VERSION_KEYS if len({repr(p.get(key)) for p in provenance}) > 1],
        "run_ids_unique": None not in run_ids and len(set(run_ids)) == len(run_ids),
    }


def is_stable(report: dict, *, min_agreement: float = 1.0, max_level_range: int = 0) -> bool:
    """Strict default: identical direction, verdict, score and every criterion."""
    return (
        report["versions_consistent"] and report["run_ids_unique"]
        and report["direction_agreement"] >= min_agreement
        and report["verdict_agreement"] >= min_agreement
        and report["score_agreement"] >= min_agreement
        and all(c["agreement"] >= min_agreement and c["absent_runs"] == 0
                and (c["level_range"] or 0) <= max_level_range
                for c in report["criteria"].values())
    )


def pair_difference(first: dict, second: dict) -> dict:
    """Compare two results, e.g. for inputs that differ only in an irrelevant detail."""
    a, b = _levels(first), _levels(second)
    deltas = {}
    for name in CRITERION_IDS:
        x, y = a.get(name), b.get(name)
        deltas[name] = None if x is None or y is None else abs(x - y)
    comparable = [d for d in deltas.values() if d is not None]
    return {
        "direction_equal": first.get("recommended_direction") == second.get("recommended_direction"),
        "verdict_equal": first.get("preliminary_verdict") == second.get("preliminary_verdict"),
        "score_delta": (abs(first["score"] - second["score"])
                        if first.get("score") is not None and second.get("score") is not None else None),
        "criterion_deltas": deltas,
        "max_criterion_delta": max(comparable) if comparable else None,
        "known_status_changed": [n for n in CRITERION_IDS if (a.get(n) is None) != (b.get(n) is None)],
    }
