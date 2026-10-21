"""Every curator transcript fact in the graph has a literal source quote."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_case_facts_quote_updated_source():
    source = (ROOT / "docs" / "case.txt").read_text(encoding="utf-8")
    seed = (ROOT / "knowledge" / "seed.cypher").read_text(encoding="utf-8")
    rows = [line for line in seed.splitlines() if 'source_path: "docs/case.txt"' in line]
    assert len(rows) >= 6
    for row in rows:
        quote = re.search(r'quote: "([^"]+)"', row).group(1)
        assert quote in source
        assert 'version_id: "gim-graph-v3"' in row
