"""Offline metrics and a frozen duplicate-safe split. Does not run a model."""
from collections import Counter, defaultdict
import hashlib
import math
import random


def frozen_split(records, seed='gim-holdout-v1'):
    # One semantic fingerprint can only belong to one split, even across seasons.
    groups = defaultdict(list)
    for r in records: groups[r['fingerprint']].append(r)
    strata = defaultdict(list)
    for fp, group in groups.items():
        # Mixed-label duplicates remain together, in an explicitly mixed stratum.
        label = tuple(sorted({str(r.get('expert_score')) for r in group}))
        season = tuple(sorted({r.get('season', 'unknown') for r in group}))
        strata[(label, season)].append(fp)
    assignment = {}
    for group in strata.values():
        order = sorted(group, key=lambda fp: hashlib.sha256((seed + fp).encode()).hexdigest())
        n = len(order)
        test_count = max(1, round(n * .25)) if n >= 4 else 0
        for index, fp in enumerate(order): assignment[fp] = 'test' if index < test_count else 'tune'
    return {r['candidate_id']: assignment[r['fingerprint']] for r in records}


def metrics(gold, predictions):
    if len(gold) != len(predictions): raise ValueError('Metric lengths differ')
    if any(type(y) is not int or y not in range(4) for y in gold): raise ValueError('Gold must be 0..3')
    if any(p is not None and (type(p) is not int or p not in range(4)) for p in predictions): raise ValueError('Prediction must be 0..3 or null')
    pairs = [(g, p) for g, p in zip(gold, predictions) if p is not None]
    matrix = [[sum(g == i and p == j for g, p in pairs) for j in range(4)] for i in range(4)]
    classes = []
    for i in range(4):
        tp = matrix[i][i]; predicted = sum(matrix[r][i] for r in range(4)); support = sum(g == i for g in gold)
        precision = tp / predicted if predicted else 0
        # Abstention is not silently discarded from class recall.
        recall = tp / support if support else 0
        classes.append({'class': i, 'support': support, 'precision': precision, 'recall': recall,
                        'f1': 2*precision*recall/(precision+recall) if precision+recall else 0})
    n = len(gold); covered = len(pairs)
    observed = sum((g-p)**2 for g,p in pairs) / covered if covered else None
    true_counts = Counter(g for g,p in pairs); pred_counts = Counter(p for g,p in pairs)
    expected = sum(true_counts[i]*pred_counts[j]*(i-j)**2 for i in range(4) for j in range(4))/covered**2 if covered else 0
    return {'n': n, 'numeric_pairs': covered, 'null_count': n-covered,
            'coverage': covered/n if n else None, 'confusion_matrix': matrix, 'per_class': classes,
            'macro_f1': sum(c['f1'] for c in classes)/4 if n else None,
            'mae': sum(abs(g-p) for g,p in pairs)/covered if covered else None,
            'accuracy': sum(g == p for g,p in pairs)/n if n else None,
            'quadratic_weighted_kappa': 1-observed/expected if expected else None,
            'large_errors': sum(abs(g-p)>=2 for g,p in pairs)}


def bootstrap_mae(gold, predictions, seed=20261022, repeats=1000):
    pairs = [(g,p) for g,p in zip(gold,predictions) if p is not None]
    if not pairs: return None
    rng = random.Random(seed)
    values = sorted(sum(abs(g-p) for g,p in rng.choices(pairs,k=len(pairs)))/len(pairs) for _ in range(repeats))
    return [values[int(repeats*.025)], values[min(repeats-1,int(repeats*.975))]]


def report(records, predictions, split):
    if set(split) != {r['candidate_id'] for r in records}: raise ValueError('Split membership mismatch')
    if len({r['candidate_id'] for r in records}) != len(records): raise ValueError('Duplicate candidate ID')
    groups = defaultdict(set)
    for r in records: groups[r['fingerprint']].add(split[r['candidate_id']])
    if any(len(v)>1 for v in groups.values()): raise ValueError('Fingerprint leaks across split')
    tune = [r for r in records if split[r['candidate_id']] == 'tune' and r['expert_score'] is not None]
    test = [r for r in records if split[r['candidate_id']] == 'test' and r['expert_score'] is not None]
    if not tune or not test: raise ValueError('Need labelled tune and test sets')
    expected = {r['candidate_id'] for r in test}
    if set(predictions) != expected: raise ValueError('Test predictions missing or extraneous')
    majority = sorted(Counter(r['expert_score'] for r in tune).items(), key=lambda p:(-p[1],p[0]))[0][0]
    def section(rows):
        gold = [r['expert_score'] for r in rows]; pred = [predictions[r['candidate_id']] for r in rows]
        return {'model': metrics(gold,pred), 'mae_95pct_bootstrap': bootstrap_mae(gold,pred),
                'majority_baseline': metrics(gold,[majority]*len(gold)), 'fixed_2_baseline': metrics(gold,[2]*len(gold))}
    return {'test': section(test), 'by_season': {s: section([r for r in test if r['season']==s]) for s in sorted({r['season'] for r in test})},
            'majority_label_from_tune_only': majority, 'limitation': 'Small historical sample; scores differ from admission and activity. No quality claim without real predictions and expert protocol approval.'}
