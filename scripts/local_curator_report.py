"""Private, on-disk curator report from local SQLite; never an HTTP endpoint.

The applicant never sees this report. It may include personal information:
use only fictional test data for demonstrations, never upload real records.
"""
from __future__ import annotations
import argparse
from datetime import datetime
from html import escape
import json
from pathlib import Path
import sqlite3
import webbrowser
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.curator import render_card


def build(db: Path, destination: Path, limit: int = -1) -> int:
    if not db.is_file():
        raise FileNotFoundError(f"No local SQLite database: {db}")
    connection = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        items = connection.execute(
            "SELECT id,created_at,applicant_name,payload,evaluation "
            "FROM applications ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        connection.close()
    cards = []
    for row in items:
        try:
            answers = json.loads(row["payload"])
            evaluation = json.loads(row["evaluation"])
        except (ValueError, TypeError):
            continue
        cards.append(render_card(row["id"], row["created_at"], {**answers, "applicant_name": row["applicant_name"]}, evaluation))
    destination.parent.mkdir(parents=True, exist_ok=True)
    css=("body{font:15px/1.5 system-ui;color:#222;background:#faf8f5;max-width:1050px;"
         "padding:20px;margin:0 auto}article{background:white;margin:20px 0;"
         "padding:22px;border:1px solid #ddd;border-radius:10px}"
         "table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:10px;text-align:left;vertical-align:top}h1,h2{color:#8e181b}dl{display:grid;grid-template-columns:210px 1fr;"
         "gap:10px}dt{font-weight:700}dd{margin:0}pre{background:#f4f3f1;padding:14px;"
         "white-space:pre-wrap;overflow-wrap:anywhere}details{margin-top:20px}"
         ".warn{border:2px solid #b77c38;padding:12px;background:#fff4df}")
    html=("<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
          "<title>ГИМ — локальная панель куратора</title><style>"+css+"</style></head>"
          "<body><h1>ГИМ · локальный отчёт куратора</h1>"
          "<p class='warn'>Локальный файл может содержать персональные данные. "
          "Только для тестового ноутбука, не публиковать. "
          "Публичный сайт не показывает внутренние оценки кандидату.</p>"
          "<p>Создано "+escape(datetime.now().isoformat(timespec="seconds"))+
          "; заявок "+str(len(cards))+"</p>"+''.join(cards)+"</body></html>")
    import os
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(html)
    destination.chmod(0o600)
    return len(cards)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,default=ROOT/"data"/"gim.sqlite3")
    p.add_argument("--out",type=Path,default=ROOT/"reports"/"curator_local.html")
    p.add_argument("--limit",type=int,default=-1)
    p.add_argument("--no-open",action="store_true")
    args=p.parse_args()
    count=build(args.db,args.out,args.limit)
    print("LOCAL curator report:",args.out,"| applications:",count)
    if not args.no_open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__=="__main__":
    main()

