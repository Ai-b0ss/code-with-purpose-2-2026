"""Read-only curator projections over the full SQLite sample."""
from collections import Counter
from datetime import date
import json
from fastapi import HTTPException
from backend.curator import stored_evaluation

CRITERIA = ('motivation', 'skills', 'availability', 'form_and_contact', 'logistics')


def select_records(con, direction=None, mode=None, score_status=None, date_from=None, date_to=None):
    for d in (date_from, date_to):
        if d:
            try: date.fromisoformat(d)
            except ValueError: raise HTTPException(422, 'Дата фильтра должна иметь формат ГГГГ-ММ-ДД.') from None
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, 'Начало периода позже конца.')
    records = []
    for row in con.execute('SELECT * FROM applications'):
        p, e = json.loads(row['payload']), stored_evaluation(row['evaluation'])
        day = row['created_at'][:10]
        if direction and p.get('desired_direction') != direction: continue
        if mode and e.get('evaluation_mode') != mode: continue
        if score_status and e.get('score_status', 'legacy_unknown') != score_status: continue
        if date_from and day < date_from: continue
        if date_to and day > date_to: continue
        records.append({'application_id': row['id'], 'created_at': row['created_at'],
                        'applicant_name': row['applicant_name'], 'answers': p, 'evaluation': e,
                        'sheet_sync_status': row['sheet_sync_status']})
    return records


def numeric_score(e):
    score = e.get('score')
    return score if type(score) in (int, float) and score in (0, 1, 2, 3) else None


def ranked(records, sort='score_desc', queue='all'):
    if queue == 'rated': records = [r for r in records if numeric_score(r['evaluation']) is not None]
    if queue == 'unrated': records = [r for r in records if numeric_score(r['evaluation']) is None]
    counts = Counter(numeric_score(r['evaluation']) for r in records)
    def key(r):
        score = numeric_score(r['evaluation'])
        return (score is None, -(score or 0) if sort == 'score_desc' else score or 0,
                r['created_at'], r['application_id'])
    if sort == 'created_at':
        records = sorted(records, key=lambda r: (r['created_at'], r['application_id']))
    else: records = sorted(records, key=key)
    for r in records:
        e = r['evaluation']
        score = numeric_score(e)
        r['score'] = score
        r['tied'] = score is not None and counts[score] > 1
        r['rated_criteria_count'] = sum(type(c.get('level')) is int and c['level'] in range(4) for c in e.get('scorecard', []))
        r.pop('answers', None)
    return records


def aggregate(records):
    n = len(records)
    def distribution(values):
        c = Counter('unknown' if v is None or v == '' else str(v) for v in values)
        return {'denominator': n, 'unknown_count': c.get('unknown', 0), 'counts': dict(sorted(c.items()))}
    result = {'total': n, 'source': 'SQLite', 'warning': 'Пилотная шкала не утверждена ГИМ. Решение принимает человек.'}
    for name, getter in {
        'daily': lambda r: r['created_at'][:10],
        'desired_direction': lambda r: r['answers'].get('desired_direction'),
        'recommended_direction': lambda r: r['evaluation'].get('recommended_direction'),
        'score': lambda r: numeric_score(r['evaluation']),
        'evaluation_mode': lambda r: r['evaluation'].get('evaluation_mode'),
        'score_status': lambda r: r['evaluation'].get('score_status'),
        'sheet_sync_status': lambda r: r['sheet_sync_status'],
        'verdict': lambda r: r['evaluation'].get('preliminary_verdict'),
    }.items(): result[name] = distribution(getter(r) for r in records)
    result['criteria'] = {key: distribution(next((c.get('level') for c in r['evaluation'].get('scorecard', []) if c.get('criterion') == key), None) for r in records) for key in CRITERIA}
    result['rated_count'] = n - result['score']['unknown_count']
    result['unrated_count'] = result['score']['unknown_count']
    return result
