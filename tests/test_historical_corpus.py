"""Source-audit checks for the consented historical GraphRAG corpus."""
from backend.historical import prepare_corpus


def test_deduplicate_before_split_and_keep_holdout_out_of_training():
    train, holdout, manifest = prepare_corpus()
    assert manifest["source_rows"] == 168
    assert manifest["unique_applications"] == 113
    assert len(train) == 90
    assert len(holdout) == 23
    assert {row["id"] for row in train}.isdisjoint({row["id"] for row in holdout})
    assert sum(len(row["source_refs"]) for row in train + holdout) == 168
    assert sum(len(row["source_refs"]) > 1 for row in train + holdout) == 55


def test_split_is_reproducible_and_old_zero_is_separate_outcome():
    train, holdout, _ = prepare_corpus()
    _, second_holdout, _ = prepare_corpus()
    assert [row["id"] for row in holdout] == [row["id"] for row in second_holdout]
    assert all(row["historical_grade"] != 0 or row["invited"] is False
               for row in train + holdout)
    assert {row["cohort"] for row in holdout} == {"2024-autumn", "2025-spring"}


def test_graph_text_excludes_direct_identifier_columns():
    train, _, _ = prepare_corpus()
    assert all("ФИО:" not in row["text"] and "Дата рождения:" not in row["text"]
               and "ВУЗ:" not in row["text"] for row in train)
