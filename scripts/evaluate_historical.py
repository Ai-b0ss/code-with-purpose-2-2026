"""Evaluate historical GIM applications without emitting source records.

The mock evaluator deliberately calls the real demo evaluator for plumbing, but
its reported score comes from the deterministic heuristic in
``_mock_score``.  The heuristic is an offline benchmark convenience, not a
calibrated admissions model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from backend import evaluator
from backend.app import ApplicationInput
from backend.benchmark import metrics as benchmark_metrics
from backend.historical_dataset import read_sources


VALID_SCORES = frozenset(range(4))
ANONYMIZATION_VERSION = "gim-historical-evaluation-v1"
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"https?://\S+|(?:www\.)?vk\.com/\S+", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
_FULL_NAME_RE = re.compile(r"\b[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){2}\b")


def _valid_score(value: Any) -> bool:
    return type(value) is int and value in VALID_SCORES


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_text(item) for item in value if item is not None).strip()
    return str(value).strip()


def _safe_text(value: Any) -> str:
    """Keep semantic text while removing common direct identifiers."""
    text = _text(value)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _URL_RE.sub("[REDACTED_URL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return _FULL_NAME_RE.sub("[REDACTED_NAME]", text)


def _hash_id(value: Any) -> str:
    """Return a stable, non-reversible identifier for report use."""
    raw = _text(value) or "missing-candidate-id"
    return hashlib.sha256(f"{ANONYMIZATION_VERSION}:{raw}".encode("utf-8")).hexdigest()


def discover_sources(data_dir: str | Path) -> list[Path]:
    """Find the supported 2024/2025 XLSX sources in deterministic order."""
    directory = Path(data_dir)
    if not directory.exists():
        raise FileNotFoundError(f"Historical data directory does not exist: {directory}")
    if not directory.is_dir():
        raise ValueError(f"Historical data path is not a directory: {directory}")
    sources = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".xlsx"
            and ("2024" in path.name or "2025" in path.name)
        ),
        key=lambda path: path.name.casefold(),
    )
    if not sources:
        raise FileNotFoundError(
            f"No 2024/2025 .xlsx historical sources found in {directory}"
        )
    return sources


def _feature_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    answers = record.get("answers") if isinstance(record, Mapping) else {}
    answers = answers if isinstance(answers, Mapping) else {}
    present = [_text(value) for value in answers.values() if _text(value)]
    meaningful_fields = (
        "experience",
        "scientific_interests",
        "desired_direction",
        "expectations",
        "hobbies",
        "superpower",
        "foreign_languages",
    )
    return {
        "answer_fields_present": len(present),
        "answer_characters": sum(len(value) for value in present),
        "meaningful_fields_present": sum(bool(_text(answers.get(field))) for field in meaningful_fields),
        "has_experience": bool(_text(answers.get("experience"))),
        "has_direction": bool(_text(answers.get("desired_direction"))),
        "has_expectations": bool(_text(answers.get("expectations"))),
    }


def anonymize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Create the only record representation allowed in reports.

    Names, contacts, source paths, sheet names, row numbers, raw answers and
    evaluator prose are intentionally excluded.  Missing expert labels remain
    ``None`` rather than being converted to a class.
    """
    score = record.get("expert_score")
    return {
        "candidate_id": _hash_id(record.get("candidate_id")),
        "season": _text(record.get("season")) or "unknown",
        "expert_score": score if _valid_score(score) else None,
        "features": _feature_summary(record),
    }


def _parse_hours(value: Any) -> int:
    """Convert the historical annual-activity field to a conservative month estimate."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return max(0, min(400, int(round(float(value) / 12))))
    text = _text(value).casefold().replace(",", ".")
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return 0
    if "до" in text and len(numbers) == 1:
        annual = numbers[0] / 2
    elif len(numbers) >= 2:
        annual = sum(numbers[:2]) / 2
    elif "более" in text or ">" in text:
        annual = numbers[0] * 1.25
    else:
        annual = numbers[0]
    return max(0, min(400, int(round(annual / 12))))


def _direction(value: Any) -> str:
    text = _text(value).casefold()
    if any(word in text for word in ("фонд", "архив", "коллекц")):
        return "collections"
    if any(word in text for word in ("инклюз", "особенн", "слабовид", "жестов")):
        return "inclusive"
    if any(word in text for word in ("медиа", "фото", "видео", "текст", "дизайн", "соцсет")):
        return "media"
    if any(word in text for word in ("квест", "мастер", "образоват", "экскурс")):
        return "project"
    if any(word in text for word in ("организ", "меропр", "посетител")):
        return "organizational"
    return "undecided"


def _birth_is_adult(value: Any) -> bool:
    text = _text(value)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            born = datetime.strptime(text, fmt).date()
            reference = date(2025, 12, 31)
            return (reference.year - born.year - ((reference.month, reference.day) < (born.month, born.day))) >= 18
        except ValueError:
            continue
    # Missing age is not evidence of being under 18; the evaluator still gets
    # a valid ApplicationInput while the missing source field remains absent.
    return True


def _languages(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,;\n]+", text) if part.strip()]


def build_application(record: Mapping[str, Any]) -> ApplicationInput:
    """Map a normalized historical row to a PII-minimized ApplicationInput."""
    answers = record.get("answers") if isinstance(record, Mapping) else {}
    answers = answers if isinstance(answers, Mapping) else {}
    candidate_hash = _hash_id(record.get("candidate_id"))
    motivation_parts = [
        _safe_text(answers.get("expectations")),
        _safe_text(answers.get("scientific_interests")),
        _safe_text(answers.get("experience")),
        _safe_text(answers.get("hobbies")),
        _safe_text(answers.get("superpower")),
    ]
    motivation = " ".join(part for part in motivation_parts if part).strip()
    if len(motivation) < 10:
        motivation = "Историческая анкета без развернутой мотивации."

    return ApplicationInput(
        application_id=candidate_hash,
        applicant_name="Historical candidate",
        age_over_18=_birth_is_adult(answers.get("birth_date")),
        motivation=motivation[:4000],
        experience=_safe_text(answers.get("experience"))[:4000],
        expectations=_safe_text(answers.get("expectations"))[:4000],
        availability_hours_month=_parse_hours(record.get("activity_hours_year")),
        other_commitments="",
        desired_direction=_direction(answers.get("desired_direction")),
        followup_answer="",
        email="",
        vk_url="",
        # Do not forward birth dates or direct identifiers to an external model.
        birth_date="",
        consent_personal_data=False,
        status="student",
        university="",
        university_other="",
        faculty="",
        course="",
        scientific_interests=_safe_text(answers.get("scientific_interests"))[:2000],
        foreign_languages=[_safe_text(value) for value in _languages(answers.get("foreign_languages"))],
        hobbies=_safe_text(answers.get("hobbies"))[:2000],
        availability_schedule_type="flexible",
        availability_days=[],
        other_volunteer_programs="",
        source="",
        followup_answers={},
        followup_questions=[],
        clarification_round=0,
    )


def _record_key(record: Mapping[str, Any]) -> str:
    return _text(record.get("candidate_id")) or json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)


def _fallback_split(records: Sequence[Mapping[str, Any]], random_state: int) -> dict[str, str]:
    strata: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        score = record.get("expert_score")
        label = str(score) if _valid_score(score) else "null"
        strata[label].append(record)

    assignment: dict[str, str] = {}
    for label in sorted(strata):
        group = sorted(strata[label], key=_record_key)
        # A separate seeded shuffle per stratum makes the result independent of
        # workbook row order while keeping class proportions deterministic.
        seed_material = f"{random_state}:{label}".encode("utf-8")
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        random.Random(seed).shuffle(group)
        test_count = int(round(len(group) * 0.2))
        if len(group) >= 2:
            test_count = max(1, test_count)
        test_count = min(test_count, max(0, len(group) - 1))
        for index, record in enumerate(group):
            assignment[_record_key(record)] = "test" if index < test_count else "tune"

    if len(records) >= 2 and "test" not in assignment.values():
        first = sorted(records, key=_record_key)[0]
        assignment[_record_key(first)] = "test"
    return assignment


def split_records(records: Sequence[Mapping[str, Any]], random_state: int = 42) -> dict[str, str]:
    """Return candidate-id → ``tune``/``test`` using a temporal-first split."""
    rows = list(records)
    if not rows:
        return {}
    seasons = {_text(record.get("season")) for record in rows}
    if {"2024", "2025"}.issubset(seasons):
        assignment = {}
        for record in rows:
            key = _record_key(record)
            assignment[key] = "test" if _text(record.get("season")) == "2025" else "tune"
        return assignment
    return _fallback_split(rows, random_state)


def compute_metrics(gold: Sequence[int], predictions: Sequence[int | None]) -> dict[str, Any]:
    """Compute the required null-aware 0–3 classification metrics."""
    result = benchmark_metrics(list(gold), list(predictions))
    required = {
        "confusion_matrix",
        "mae",
        "per_class",
        "macro_f1",
        "coverage",
        "null_count",
    }
    missing = required.difference(result)
    if missing:
        raise RuntimeError(f"Backend metrics missing required keys: {sorted(missing)}")
    return result


def _mock_score(application: ApplicationInput, record: Mapping[str, Any]) -> int | None:
    """Documented offline heuristic: three independent signals, else abstain.

    One point is awarded for at least 50 motivation characters, one for a
    non-empty experience/language signal, and one for at least 16 available
    hours per month.  If fewer than two signals are observable, return null to
    preserve uncertainty instead of manufacturing a score.
    """
    answers = record.get("answers") if isinstance(record, Mapping) else {}
    answers = answers if isinstance(answers, Mapping) else {}
    motivation_signal = len(application.motivation.strip()) >= 50 and not application.motivation.startswith("Историческая анкета")
    skills_signal = bool(_text(answers.get("experience")) or _text(answers.get("foreign_languages")))
    availability_signal = application.availability_hours_month >= 16
    signals = [motivation_signal, skills_signal, availability_signal]
    if sum(signals) < 2:
        return None
    return int(sum(signals))


def _prediction_for(record: Mapping[str, Any], mode: str, adapter: Any = None) -> int | None:
    application = build_application(record)
    if mode == "mock":
        # Exercise the same evaluator plumbing as the application without a
        # paid call; the deterministic benchmark score is intentionally local.
        evaluator.evaluate(application, mode="demo")
        return _mock_score(application, record)
    if mode == "live":
        result = evaluator.evaluate(
            application,
            mode="llm",
            adapter=adapter,
            fallback_to_demo=False,
        )
        score = result["score"]
        if score is not None and not _valid_score(score):
            raise ValueError("Live evaluator returned score outside 0..3 or null")
        return score
    raise ValueError("mode must be 'mock' or 'live'")


def _majority_label(records: Iterable[Mapping[str, Any]]) -> int | None:
    labels = [record.get("expert_score") for record in records if _valid_score(record.get("expert_score"))]
    if not labels:
        return None
    return min(Counter(labels), key=lambda label: (-Counter(labels)[label], label))


def _anomalies(rows: Sequence[Mapping[str, Any]], predictions: Sequence[int | None]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    for record, prediction in zip(rows, predictions):
        gold = record.get("expert_score")
        if not _valid_score(gold):
            continue
        error = abs(gold - prediction) if prediction is not None else None
        if error is not None and error >= 2:
            explanation = (
                f"absolute score error is {error} points; review the evidence and historical label"
            )
            anomalies.append({
                "candidate_id": _hash_id(record.get("candidate_id")),
                "y_true": gold,
                "y_pred": prediction,
                "abs_error": error,
                "explanation": explanation,
            })
    return anomalies


def evaluate_records(
    records: Sequence[Mapping[str, Any]],
    mode: str = "mock",
    limit: int | None = None,
    random_state: int = 42,
) -> dict[str, Any]:
    """Evaluate labelled historical records and return a report-safe result."""
    if mode not in {"mock", "live"}:
        raise ValueError("mode must be 'mock' or 'live'")
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    all_rows = list(records)
    labelled = [record for record in all_rows if _valid_score(record.get("expert_score"))]
    split = split_records(labelled, random_state=random_state)
    tune = [record for record in labelled if split.get(_record_key(record)) == "tune"]
    holdout = [record for record in labelled if split.get(_record_key(record)) == "test"]
    holdout = sorted(holdout, key=_record_key)
    evaluated = holdout if limit is None else holdout[:limit]

    adapter = evaluator.adapter_from_env() if mode == "live" and evaluated else None
    predictions = [_prediction_for(record, mode, adapter) for record in evaluated]
    gold = [record["expert_score"] for record in evaluated]
    majority = _majority_label(tune)
    model_metrics = compute_metrics(gold, predictions)
    majority_metrics = compute_metrics(gold, [majority] * len(gold))
    fixed_metrics = compute_metrics(gold, [2] * len(gold))

    strategy = (
        "temporal_2024_tune_2025_holdout"
        if {"2024", "2025"}.issubset({_text(record.get("season")) for record in labelled})
        else f"stratified_80_20_random_state_{random_state}"
    )
    return {
        "version": ANONYMIZATION_VERSION,
        "mode": mode,
        "split": {
            "strategy": strategy,
            "random_state": random_state,
            "counts": {
                "input": len(all_rows),
                "unlabelled_excluded": len(all_rows) - len(labelled),
                "labelled": len(labelled),
                "tune": len(tune),
                "holdout": len(holdout),
                "evaluated": len(evaluated),
            },
        },
        "model": {
            "metrics": model_metrics,
            "predictions": [
                {
                    "candidate_id": _hash_id(record.get("candidate_id")),
                    "y_true": record["expert_score"],
                    "y_pred": prediction,
                }
                for record, prediction in zip(evaluated, predictions)
            ],
        },
        "baselines": {
            "majority": {"label": majority, "metrics": majority_metrics},
            "fixed_2": {"label": 2, "metrics": fixed_metrics},
        },
        "anomalies": _anomalies(evaluated, predictions),
    }


def _matrix_markdown(matrix: Sequence[Sequence[int]]) -> str:
    lines = ["| gold \\ pred | 0 | 1 | 2 | 3 |", "|---:|---:|---:|---:|---:|"]
    lines.extend(
        f"| {index} | " + " | ".join(str(value) for value in row) + " |"
        for index, row in enumerate(matrix)
    )
    return "\n".join(lines)


def _metric_table(entries: Mapping[str, Mapping[str, Any]]) -> str:
    lines = ["| system | MAE | macro-F1 | coverage | null count |", "|---|---:|---:|---:|---:|"]
    for name, item in entries.items():
        metrics = item["metrics"]
        def fmt(value: Any) -> str:
            return "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
        lines.append(
            f"| {name} | {fmt(metrics.get('mae'))} | {fmt(metrics.get('macro_f1'))} | "
            f"{fmt(metrics.get('coverage'))} | {metrics.get('null_count', 0)} |"
        )
    return "\n".join(lines)


def _per_class_markdown(metrics: Mapping[str, Any]) -> str:
    lines = [
        "| class | support | precision | recall | F1 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for item in metrics.get("per_class", []):
        lines.append(
            f"| {item['class']} | {item['support']} | {item['precision']:.3f} | "
            f"{item['recall']:.3f} | {item['f1']:.3f} |"
        )
    return "\n".join(lines)


def write_reports(result: Mapping[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    """Write safe aggregate JSON and readable Markdown reports."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    metrics_path = directory / "metrics.json"
    summary_path = directory / "summary.md"
    model = result["model"]
    baselines = result["baselines"]
    entries = {
        "model": model,
        "majority baseline": baselines["majority"],
        "fixed-2 baseline": baselines["fixed_2"],
    }
    safe_payload = {
        "version": result.get("version", ANONYMIZATION_VERSION),
        "mode": result.get("mode"),
        "split": result.get("split"),
        "model": {"metrics": model["metrics"]},
        "baselines": baselines,
        "anomalies": result.get("anomalies", []),
    }
    metrics_path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts = result["split"]["counts"]
    lines = [
        "# Historical evaluation",
        "",
        f"Mode: `{result.get('mode')}`  ",
        f"Split: `{result['split']['strategy']}`  ",
        f"Random state: `{result['split']['random_state']}`",
        "",
        "## Counts",
        "",
        "| input | labelled | tune | holdout | evaluated | unlabeled excluded |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {counts['input']} | {counts['labelled']} | {counts['tune']} | {counts['holdout']} | "
        f"{counts['evaluated']} | {counts['unlabelled_excluded']} |",
        "",
        "## Aggregate metrics",
        "",
        _metric_table(entries),
        "",
        "### Model confusion matrix",
        "",
        _matrix_markdown(model["metrics"]["confusion_matrix"]),
        "",
        "### Majority-baseline confusion matrix",
        "",
        _matrix_markdown(baselines["majority"]["metrics"]["confusion_matrix"]),
        "",
        "### Fixed-2-baseline confusion matrix",
        "",
        _matrix_markdown(baselines["fixed_2"]["metrics"]["confusion_matrix"]),
        "",
        "### Model per-class metrics",
        "",
        _per_class_markdown(model["metrics"]),
        "",
        f"Anomalies listed: **{len(result.get('anomalies', []))}** (hashed candidate IDs only).",
        "",
    ]
    anomalies = result.get("anomalies", [])
    if anomalies:
        lines.extend([
            "| candidate ID (sha256) | gold | prediction | absolute error | explanation |",
            "|---|---:|---:|---:|---|",
        ])
        lines.extend(
            f"| `{item['candidate_id']}` | {item['y_true']} | {item['y_pred']} | "
            f"{item['abs_error']} | {item['explanation']} |"
            for item in anomalies
        )
        lines.append("")
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return metrics_path, summary_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="materials/gim", help="Directory containing historical XLSX files")
    parser.add_argument("--output-dir", default="reports/evaluation-local", help="Directory for metrics.json and summary.md")
    parser.add_argument("--mode", choices=("mock", "live"), default="mock", help="Offline demo plumbing or strict live LLM evaluation")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of holdout records to evaluate")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    try:
        sources = discover_sources(args.data_dir)
        records, source_summary = read_sources(sources)
        result = evaluate_records(records, mode=args.mode, limit=args.limit, random_state=42)
        result["source_summary"] = {
            "source_count": len(sources),
            "record_count": source_summary.get("total", len(records)),
            "network_calls": source_summary.get("network_calls", 0),
        }
        paths = write_reports(result, args.output_dir)
    except evaluator.EvaluationError as exc:
        parser.error(exc.code)
    except (FileNotFoundError, ImportError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"metrics": str(paths[0]), "summary": str(paths[1])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
