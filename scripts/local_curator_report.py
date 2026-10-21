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

ROOT = Path(__file__).resolve().parent.parent


def build(db: Path, destination: Path, limit: int = 40) -> int:
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
        def obj(value: object) -> str:
            return escape(json.dumps(value, indent=2, ensure_ascii=False))
        fields = [
            ("Режим", evaluation.get("evaluation_mode", "?")),
            ("Рекомендованное направление", evaluation.get("recommended_direction_label", "?")),
            ("Выбранное кандидатом", answers.get("desired_direction", "?")),
            ("score / статус", str(evaluation.get("score"))+" / "+str(evaluation.get("score_status"))),
            ("Предварительный вердикт (не решение о приёме)", evaluation.get("preliminary_verdict") or "нет"),
            ("Почему этот вердикт", evaluation.get("verdict_comment") or "нет данных"),
            ("Саммари", evaluation.get("summary", "")),
            ("Соответствие музею", evaluation.get("fit_for_museum", "")),
            ("Соответствие кандидату", evaluation.get("fit_for_candidate", "")),
        ]
        tags = ''.join("<dt>"+escape(k)+"</dt><dd>"+escape(str(v))+"</dd>" for k,v in fields)
        cards.append("<article><h2>"+escape(row["applicant_name"])+
                     "</h2><p><code>"+escape(row["id"])+
                     "</code> | "+escape(row["created_at"])+
                     "</p><dl>"+tags+"</dl><h3>Сильные стороны</h3><pre>"+
                     obj(evaluation.get("strengths",[]))+
                     "</pre><h3>Возможные проблемы</h3><pre>"+
                     obj(evaluation.get("possible_problems",[]))+
                     "</pre><h3>Пилотные оценки пяти критериев (не откалиброваны)</h3><pre>"+
                     obj(evaluation.get("scorecard",[]))+
                     "</pre><h3>Уточняющие вопросы и ответы ДО заключения</h3><pre>"+
                     obj([{"question": q.get("question", ""),
                           "answer": answers.get("followup_answers", {}).get(q.get("id", ""), "")}
                          for q in answers.get("followup_questions", [])])+
                     "</pre><h3>Оставшиеся вопросы к куратору, если есть</h3><pre>"+
                     obj(evaluation.get("interview_questions",[]))+
                     "</pre><h3>Доказательства (evidence)</h3><pre>"+
                     obj(evaluation.get("evidence",[]))+
                     "</pre><details><summary>Исходная анкета (персональные данные)</summary><pre>"+
                     obj(answers)+"</pre></details></article>")
    destination.parent.mkdir(parents=True, exist_ok=True)
    css=("body{font:15px/1.5 system-ui;color:#222;background:#faf8f5;max-width:1050px;"
         "padding:20px;margin:0 auto}article{background:white;margin:20px 0;"
         "padding:22px;border:1px solid #ddd;border-radius:10px}"
         "h1,h2{color:#8e181b}dl{display:grid;grid-template-columns:210px 1fr;"
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
    destination.write_text(html, encoding="utf-8")
    return len(cards)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",type=Path,default=ROOT/"data"/"gim.sqlite3")
    p.add_argument("--out",type=Path,default=ROOT/"reports"/"curator_local.html")
    p.add_argument("--limit",type=int,default=40)
    p.add_argument("--no-open",action="store_true")
    args=p.parse_args()
    count=build(args.db,args.out,args.limit)
    print("LOCAL curator report:",args.out,"| applications:",count)
    if not args.no_open:
        webbrowser.open(args.out.resolve().as_uri())


if __name__=="__main__":
    main()
