"""Default dry run. Only --include-personal-data writes local raw records."""
import argparse
import json
import os
from pathlib import Path
from backend.historical_dataset import read_sources


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', action='append', required=True)
    p.add_argument('--out', default='reports/historical-local/dataset.ndjson')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--include-personal-data', action='store_true')
    args = p.parse_args()
    records, summary = read_sources(args.source)
    if args.include_personal_data and not args.dry_run:
        target = Path(args.out).resolve()
        allowed = (Path(__file__).resolve().parents[1] / 'reports' / 'historical-local').resolve()
        if not target.is_relative_to(allowed): p.error('Output must be in reports/historical-local')
        target.parent.mkdir(parents=True, exist_ok=True)
        # Refuse overwrite/symlinks and create private to the current OS user.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            for r in records: stream.write(json.dumps(r, ensure_ascii=False) + '\n')
        summary['written'] = len(records)
    else: summary['written'] = 0
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
