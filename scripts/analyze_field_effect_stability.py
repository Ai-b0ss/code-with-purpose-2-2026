"""Check whether proposed semantic field weights keep their sign across cohorts.

Uses only aggregate preliminary labels and historic overall scores. A positive
association here is not a causal field coefficient or an approval signal.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random

from scripts.classify_historical_fields import historical_records


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = ROOT / "data" / "historical_field_review_queue_v7.jsonl"
MIN_GROUP = 10
BOOTSTRAP_RUNS = 2000


def bootstrap_interval(two: list[int], three: list[int], seed: str) -> tuple[float, float]:
    rng = random.Random(seed)
    differences = []
    for _ in range(BOOTSTRAP_RUNS):
        sampled_two = rng.choices(two, k=len(two))
        sampled_three = rng.choices(three, k=len(three))
        differences.append(sum(sampled_three) / len(three) - sum(sampled_two) / len(two))
    differences.sort()
    return differences[int(0.025 * BOOTSTRAP_RUNS)], differences[int(0.975 * BOOTSTRAP_RUNS)]


def analyze(rows: list[dict], *, min_group: int = MIN_GROUP) -> dict:
    groups: dict[str, dict[str, dict[int, list[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    seen = set()
    for row in rows:
        row_hash = row["source_row_hash"]
        if row_hash in seen:
            raise ValueError("duplicate historical application")
        seen.add(row_hash)
        score = row["historical_score"]
        if score not in (0, 1, 2, 3):
            raise ValueError("invalid historical score")
        for review in row["fields"]:
            if review.get("quality_level") in (2, 3):
                groups[review["field_id"]][row["cohort"]][review["quality_level"]].append(score)

    fields = {}
    for field_id, cohorts in sorted(groups.items()):
        cohort_results = {}
        eligible_deltas = []
        eligible_lower_bounds = []
        for cohort, levels in sorted(cohorts.items()):
            two, three = levels.get(2, []), levels.get(3, [])
            if not two or not three:
                continue
            delta = sum(three) / len(three) - sum(two) / len(two)
            eligible = len(two) >= min_group and len(three) >= min_group
            lower, upper = bootstrap_interval(two, three, f"{field_id}:{cohort}")
            cohort_results[cohort] = {
                "quality_2_n": len(two), "quality_3_n": len(three),
                "quality_3_minus_2_mean_score": round(delta, 3),
                "bootstrap_95_percent_interval": [round(lower, 3), round(upper, 3)],
                "eligible_for_sign_check": eligible,
            }
            if eligible:
                eligible_deltas.append(delta)
                eligible_lower_bounds.append(lower)
        if cohort_results:
            fields[field_id] = {
                "cohorts": cohort_results,
                "same_positive_point_estimate": (
                    len(eligible_deltas) >= 2 and all(delta > 0 for delta in eligible_deltas)
                ),
                "positive_interval_in_every_cohort": (
                    len(eligible_lower_bounds) >= 2 and all(lower > 0 for lower in eligible_lower_bounds)
                ),
            }
    return {
        "unique_applications": len(seen), "minimum_per_level_per_cohort": min_group,
        "label_source": "preliminary_llm_or_supplied_review_file",
        "fields": fields,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels_jsonl", type=Path, nargs="?", default=DEFAULT_LABELS)
    parser.add_argument("--min-group", type=int, default=MIN_GROUP)
    args = parser.parse_args()
    if args.min_group < 1:
        raise SystemExit("--min-group must be positive")
    source = {row["source_row_hash"]: row for row in historical_records()}
    rows = []
    for line in args.labels_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        label = json.loads(line)
        matched = source.get(label["source_row_hash"])
        if matched is None:
            raise ValueError("field labels refer to an unknown or changed historical row")
        rows.append({**label, "cohort": matched["cohort"],
                     "historical_score": matched["historical_score"]})
    print(json.dumps(analyze(rows, min_group=args.min_group), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
