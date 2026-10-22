"""Detect source-document changes that require graph and calibration review."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "knowledge" / "gim_graph.json"
MANIFEST = ROOT / "knowledge" / "source_fingerprints.json"


def current_fingerprints() -> dict[str, str]:
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    paths = {
        source.split("#", 1)[0]
        for fact in graph["triples"]
        for source in fact["source"].split(";")
    }
    result = {}
    for name in sorted(paths):
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise FileNotFoundError(f"Missing graph source: {name}")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        result[name] = digest.hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true",
                        help="Record fingerprints after reviewing changed source facts")
    args = parser.parse_args()
    current = current_fingerprints()
    if args.refresh:
        MANIFEST.write_text(json.dumps({"sha256": current}, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        print("source_fingerprints_refreshed", len(current))
        return
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["sha256"]
    changed = sorted(name for name in current.keys() | expected.keys()
                     if current.get(name) != expected.get(name))
    if changed:
        for name in changed:
            print("SOURCE_REVIEW_REQUIRED", name)
        raise SystemExit(1)
    print("source_fingerprints_current", len(current))


if __name__ == "__main__":
    main()
