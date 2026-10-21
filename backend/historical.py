"""Import consented historical application data into the Neo4j knowledge graph.

The three supplied workbooks contain one overlapping spring cohort. The graph
stores one pseudonymous application per distinct row and keeps every source-row
reference. Direct identifiers stay in the original local workbooks.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

from backend.knowledge import _driver

ROOT = Path(__file__).resolve().parent.parent
FILES = (
    "docs/applications-2024-autumn.xlsx",
    "docs/applications-2025-spring.xlsx",
    "docs/hse-hackathon-2025-spring.xlsx",
)
VERSION = "historical-2024-2025-v2"
SPLIT_VERSION = "holdout-v1"
HOLDOUT_FRACTION = 0.20
HOLDOUT_PATH = ROOT / "evaluation" / "holdout-manifest.json"

HEADERS = {
    "grade": "Сколько баллов дали",
    "admitted": "Взяли ли в итоге",
    "duration": "Длительность пребывания",
    "active": "Еще состоит",
    "activity": "Активность в часах",
    "fio": "ФИО",
    "birth": "Дата рождения",
    "university": "ВУЗ",
    "faculty": "Факультет",
    "course": "Курс",
    "department": "Кафедра",
    "interests": "Сфера научных интересов",
    "experience": "Опыт работы",
    "languages": "Владение иностранными языками",
    "roles": "Интересующие виды волонтёрства",
    "expectations": "Чего Вы ожидаете",
    "hobbies": "Ваше хобби",
    "strength": "В чем Ваша суперсила",
    "source": "Откуда Вы узнали",
}

ROLE_WORDS = {
    "organizational": ("организац", "мероприят"),
    "project": ("проектн", "квест", "мастер-класс"),
    "collections": ("фондов", "фондовая", "архив"),
    "inclusive": ("инклюз"),
    "media": ("медиа", "фото", "видео", "контент"),
}


def _plain(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = unicodedata.normalize("NFKC", str(value))
    text = " ".join(text.split()).strip()
    return "" if text in {"-", "—", "–", "None"} else text


def _safe_text(value: str) -> str:
    """Remove direct contact strings from snippets that can enter a prompt."""
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", value)
    value = re.sub(r"(?:https?://|www\.)\S+", "[url]", value, flags=re.I)
    value = re.sub(r"(?<!\w)(?:\+?7|8)[\s()\-]*\d(?:[\s()\-]*\d){9}(?!\w)", "[phone]", value)
    return value[:1500]


def workbook_rows() -> list[dict]:
    records = []
    for relative in FILES:
        path = ROOT / relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        workbook = load_workbook(path, data_only=True, read_only=True)
        for sheet in workbook:
            iterator = sheet.iter_rows(values_only=True)
            header = next(iterator)
            positions = {key: next((i for i, name in enumerate(header)
                                    if name and marker.casefold() in str(name).casefold()), None)
                         for key, marker in HEADERS.items()}
            for row_number, row in enumerate(iterator, start=2):
                if not any(_plain(cell) for cell in row):
                    continue
                values = {key: _plain(row[index]) if index is not None and index < len(row) else ""
                          for key, index in positions.items()}
                # All fields common to the two spring exports, except their display name.
                identity = {key: value.casefold() for key, value in values.items() if key != "fio"}
                fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False,
                                                         sort_keys=True).encode("utf-8")).hexdigest()[:24]
                grade = int(float(values["grade"])) if values["grade"] in {"0", "1", "2", "3"} else None
                role_text = values["roles"].casefold()
                role_ids = [role for role, words in ROLE_WORDS.items()
                            if any(word in role_text for word in words)]
                answer_text = "\n".join(
                    f"{label}: {_safe_text(values[key])}"
                    for key, label in (
                        ("roles", "Направление"), ("interests", "Интересы"),
                        ("experience", "Опыт"), ("languages", "Языки"),
                        ("expectations", "Ожидания"), ("hobbies", "Хобби"),
                        ("strength", "Навыки")) if values[key] and values[key] not in {"-", "—"}
                )
                records.append({
                    "id": "historical:" + fingerprint,
                    "source_path": relative, "source_hash": digest,
                    "sheet": sheet.title, "row_number": row_number,
                    "text": answer_text, "role_ids": role_ids,
                    "historical_grade": grade,
                    "invited": False if grade == 0 else (True if grade in (1, 2, 3) else None),
                    "admitted_raw": values["admitted"],
                    "duration_raw": values["duration"],
                    "active_raw": values["active"],
                    "activity_raw": values["activity"],
                    "version_id": VERSION,
                })
        workbook.close()
    return records


def deduplicate_rows(rows: list[dict]) -> list[dict]:
    """Collapse identical spring exports before splitting to prevent leakage."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["id"]].append(row)
    unique = []
    for group in groups.values():
        canonical = max(group, key=lambda row: row["source_path"].startswith("docs/hse-hackathon"))
        item = dict(canonical)
        item["source_refs"] = [
            {"source_path": row["source_path"], "source_hash": row["source_hash"],
             "sheet": row["sheet"], "row_number": row["row_number"]}
            for row in group
        ]
        item["cohort"] = "2024-autumn" if "2024" in item["source_path"] else "2025-spring"
        unique.append(item)
    return sorted(unique, key=lambda item: item["id"])


def split_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Stable stratified 20% holdout over unique candidates, cohort and old grade."""
    strata: dict[tuple[str, int | None], list[dict]] = defaultdict(list)
    for row in rows:
        strata[(row["cohort"], row["historical_grade"])].append(row)
    target = round(len(rows) * HOLDOUT_FRACTION)
    quotas = {key: math.floor(len(group) * HOLDOUT_FRACTION) for key, group in strata.items()}
    order = sorted(strata, key=lambda key: (
        -(len(strata[key]) * HOLDOUT_FRACTION - quotas[key]), str(key)))
    for key in order[:target - sum(quotas.values())]:
        quotas[key] += 1
    held_ids = set()
    for key, group in strata.items():
        ranked = sorted(group, key=lambda row: hashlib.sha256(
            (SPLIT_VERSION + row["id"]).encode("utf-8")).hexdigest())
        held_ids.update(row["id"] for row in ranked[:quotas[key]])
    train = [row for row in rows if row["id"] not in held_ids]
    holdout = [row for row in rows if row["id"] in held_ids]
    return train, holdout


def prepare_corpus() -> tuple[list[dict], list[dict], dict]:
    raw = workbook_rows()
    unique = deduplicate_rows(raw)
    train, holdout = split_rows(unique)
    manifest = {
        "split_version": SPLIT_VERSION,
        "method": "normalize, deduplicate by content fingerprint, then 20% stratified holdout by cohort and historical grade",
        "source_rows": len(raw), "unique_applications": len(unique),
        "training_applications": len(train), "holdout_applications": len(holdout),
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in FILES},
        "holdout": [{"id": row["id"], "cohort": row["cohort"],
                     "historical_grade": row["historical_grade"], "source_refs": row["source_refs"]}
                    for row in holdout],
    }
    return train, holdout, manifest


def import_historical() -> dict:
    train, holdout, manifest = prepare_corpus()
    HOLDOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOLDOUT_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = [{**{key: value for key, value in item.items() if key != "source_refs"}, **source}
            for item in train for source in item["source_refs"]]
    with _driver() as driver:
        driver.execute_query(
            "CREATE CONSTRAINT gim_historical_id IF NOT EXISTS FOR (n:HistoricalApplication) REQUIRE n.id IS UNIQUE",
            database_="neo4j",
        )
        driver.execute_query(
            "CREATE FULLTEXT INDEX gim_historical_text IF NOT EXISTS FOR (n:HistoricalApplication) ON EACH [n.text]",
            database_="neo4j",
        )
        driver.execute_query(
            "MATCH (a:HistoricalApplication) WHERE NOT a.id IN $ids DETACH DELETE a",
            {"ids": [row["id"] for row in train]}, database_="neo4j",
        )
        driver.execute_query(
            "MATCH (:HistoricalApplication)-[r:FROM_SOURCE|INTERESTED_IN]->() DELETE r",
            database_="neo4j",
        )
        driver.execute_query(
            """UNWIND $rows AS row
            MERGE (application:HistoricalApplication {id: row.id})
            WITH application, row, application.text AS old_text
            SET application.text = row.text,
                application.embedding = CASE WHEN old_text = row.text THEN application.embedding ELSE null END,
                application.role_ids = row.role_ids,
                application.version_id = row.version_id,
                application.source_path = row.source_path,
                application.split_version = $split_version
            MERGE (source:HistoricalSource {path: row.source_path})
            SET source.sha256 = row.source_hash
            MERGE (application)-[:FROM_SOURCE {sheet: row.sheet, row_number: row.row_number}]->(source)
            MERGE (outcome:HistoricalOutcome {id: row.id})
            SET outcome.historical_grade = row.historical_grade,
                outcome.invited = row.invited,
                outcome.admitted_raw = row.admitted_raw,
                outcome.duration_raw = row.duration_raw,
                outcome.active_raw = row.active_raw,
                outcome.activity_raw = row.activity_raw
            MERGE (application)-[:HAS_OUTCOME]->(outcome)
            WITH application, row
            UNWIND row.role_ids AS role_id
            MATCH (role:Role {id: 'role:' + role_id})
            MERGE (application)-[:INTERESTED_IN]->(role)""",
            {"rows": rows, "split_version": SPLIT_VERSION}, database_="neo4j",
        )
        driver.execute_query(
            "MATCH (o:HistoricalOutcome) WHERE NOT ()-[:HAS_OUTCOME]->(o) DETACH DELETE o",
            database_="neo4j",
        )
        driver.execute_query(
            "MATCH (s:HistoricalSource) WHERE NOT ()-[:FROM_SOURCE]->(s) DETACH DELETE s",
            database_="neo4j",
        )
        graph_rows, _, _ = driver.execute_query(
            "MATCH (a:HistoricalApplication) RETURN count(a) AS count", database_="neo4j")
    if graph_rows[0]["count"] != len(train):
        raise RuntimeError("Historical RAG corpus count differs from split manifest")
    return {"source_rows": manifest["source_rows"],
            "unique_applications": manifest["unique_applications"],
            "training_applications": graph_rows[0]["count"],
            "holdout_applications": len(holdout)}
