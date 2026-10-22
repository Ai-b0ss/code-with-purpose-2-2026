"""Exploratory within-score acceptance associations with multiplicity control.

Only deidentified provisional field labels and observed outcomes are used.
This is a diagnostic audit, not a causal or deployable scoring model.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from math import comb
from pathlib import Path

from scripts.analyze_acceptance_anomalies import outcomes
from scripts.classify_historical_fields import ROOT, SOURCE_FILES


def fisher_two_sided(group_yes: int, group_n: int, total_yes: int, total_n: int) -> float:
    """Exact two-sided hypergeometric test for one category versus its peers."""
    low = max(0, group_n - (total_n - total_yes))
    high = min(group_n, total_yes)
    denominator = comb(total_n, group_n)
    probability = lambda yes: comb(total_yes, yes) * comb(total_n - total_yes, group_n - yes) / denominator
    observed = probability(group_yes)
    return min(1.0, sum(probability(yes) for yes in range(low, high + 1)
                        if probability(yes) <= observed + 1e-12))


def audit(queue_path: Path, *, cohort: str, score: int, minimum_group: int = 5) -> dict:
    records = list(outcomes())
    labels = {}
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        digest = row["source_row_hash"]
        if digest in labels:
            raise ValueError("duplicate historical label row")
        labels[digest] = row
    known_hashes = {digest for digest, *_ in records}
    cohort_hashes = {digest for digest, season, *_ in records if season == cohort}
    if not cohort_hashes or not cohort_hashes <= set(labels) or not set(labels) <= known_hashes:
        raise ValueError("field labels must cover the selected cohort and match current workbooks")

    selected = [(digest, accepted) for digest, season, historical_score, accepted, _ in records
                if season == cohort and historical_score == score and accepted is not None]
    total_n = len(selected)
    total_yes = sum(accepted for _, accepted in selected)
    if total_n < 2 * minimum_group:
        raise ValueError("not enough observed outcomes in this stratum")
    by_field: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    for digest, accepted in selected:
        seen_fields = set()
        for review in labels[digest]["fields"]:
            if review["field_id"] in seen_fields:
                raise ValueError("duplicate field label in a historical row")
            seen_fields.add(review["field_id"])
            if review["category"] != "unclassified_model_error":
                by_field[review["field_id"]][review["category"]].append(accepted)

    tests = []
    for field_id, categories in sorted(by_field.items()):
        field_n = sum(len(items) for items in categories.values())
        field_yes = sum(sum(items) for items in categories.values())
        for category, items in sorted(categories.items()):
            group_n, group_yes = len(items), sum(items)
            other_n = field_n - group_n
            if min(group_n, other_n) < minimum_group:
                continue
            other_yes = field_yes - group_yes
            tests.append({
                "field_id": field_id, "category": category,
                "category_n": group_n, "other_n": other_n,
                "acceptance_rate_difference": round(group_yes / group_n - other_yes / other_n, 3),
                "fisher_two_sided_p": fisher_two_sided(group_yes, group_n, field_yes, field_n),
            })
    tests.sort(key=lambda row: (row["fisher_two_sided_p"], row["field_id"], row["category"]))
    adjusted = 1.0
    for rank in range(len(tests), 0, -1):
        row = tests[rank - 1]
        adjusted = min(adjusted, row["fisher_two_sided_p"] * len(tests) / rank)
        row["bh_fdr_q"] = round(adjusted, 4)
        row["fisher_two_sided_p"] = round(row["fisher_two_sided_p"], 4)
    return {"cohort": cohort, "historical_score": score,
            "observed_n": total_n, "accepted_n": total_yes,
            "tested_category_count": len(tests), "minimum_group": minimum_group,
            "labels": "supplied_field_labels_not_validated_by_this_script", "tests": tests}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path,
                        default=ROOT / "data" / "historical_field_review_queue_v7.jsonl")
    parser.add_argument("--cohort", default=SOURCE_FILES[0].stem)
    parser.add_argument("--score", type=int, default=2)
    parser.add_argument("--minimum-group", type=int, default=5)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "data" / "acceptance_factor_audit.json")
    args = parser.parse_args()
    if args.minimum_group < 1 or args.score not in (0, 1, 2, 3):
        parser.error("Invalid score or minimum group size")
    result = audit(args.queue, cohort=args.cohort, score=args.score,
                   minimum_group=args.minimum_group)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("observed_n", result["observed_n"], "tested", result["tested_category_count"],
          "minimum_q", min((row["bh_fdr_q"] for row in result["tests"]), default=None),
          "out", args.out)


if __name__ == "__main__":
    main()
