"""Freeze a split first; evaluate externally supplied predictions without API calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from backend.benchmark import frozen_split, report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset', required=True);p.add_argument('--split', required=True)
    p.add_argument('--freeze', action='store_true');p.add_argument('--predictions')
    args=p.parse_args()
    data=Path(args.dataset).read_bytes(); records=[json.loads(l) for l in data.decode().splitlines() if l.strip()]
    digest=hashlib.sha256(data).hexdigest(); path=Path(args.split)
    if args.freeze:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump({'dataset_sha256':digest,'seed':'gim-holdout-v1','membership':frozen_split(records)},f)
        print('Split frozen; no predictions evaluated.');return
    split=json.loads(path.read_text())
    if split['dataset_sha256']!=digest:p.error('Dataset hash changed')
    if not args.predictions:p.error('--predictions required for evaluation')
    predictions=json.loads(Path(args.predictions).read_text())
    print(json.dumps(report(records,predictions,split['membership']),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
