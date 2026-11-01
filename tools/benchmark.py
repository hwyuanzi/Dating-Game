#!/usr/bin/env python3
"""Reproducible local practice; every vector/score uses the real referee engine.

Examples:
  python3 tools/benchmark.py --n 20 100 200 --seeds 3 --role m
  python3 tools/benchmark.py --n 40 100 200 --seeds 2 --role both --adaptive

This harness never gives M access to weights. In-process module instances are
fresh for every game, just as referee/runner.py starts a fresh bot process.
"""
import argparse
from collections import defaultdict
from decimal import Decimal
import importlib.util
import hashlib
import json
from pathlib import Path
import random
import shutil
import statistics
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from referee.engine import candidate, display, dot, setup, update

D = Decimal


def load_module(path):
    """Load a strategy with fresh globals; no state is carried between games."""
    path = Path(path)
    if not path.exists() and path.name in ('person.py', 'matchmaker.py'):
        path = path.with_name('bot.py')
    spec = importlib.util.spec_from_file_location('_practice_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cents_preferences(n, seed, kind='dense'):
    rng = random.Random(seed)
    order = list(range(n))
    rng.shuffle(order)
    if kind == 'pair':
        k = 1
    elif kind == 'mixed':
        k = max(1, min(n // 2, 100) * 4 // 5)
    elif kind == 'sparse':
        k = min(n // 2, 6)
    elif kind == 'sample':
        k = min(n // 2, 50)
    elif kind == 'mutable':
        k = min(n // 2, 12)
    elif kind == 'coarse':
        k = min(n // 2, 35)
    else:
        k = min(n // 2, 100)
    out = [0] * n
    for sign in (1, -1):
        if kind == 'coarse':
            # Nonuniform multiples of two, including movable weights: a
            # counterexample to blindly assuming every coarse lattice is ternary.
            amounts = [2] * k
            for _ in range(50-k):
                amounts[rng.randrange(k)] += 2
        elif kind in ('variable', 'sparse', 'tail'):
            if kind == 'tail':
                raw = [1 / (j + 1) ** 1.5 for j in range(k)]
            else:
                raw = [rng.expovariate(1) for _ in range(k)]
            # Give each active criterion at least one cent, distribute the rest.
            amounts = [1] * k
            for _ in range(100 - k):
                point = rng.random() * sum(raw)
                for j, amount in enumerate(raw):
                    point -= amount
                    if point <= 0:
                        amounts[j] += 1
                        break
        else:
            amounts = [100 // k + (j < 100 % k) for j in range(k)]
        for amount in amounts:
            out[order.pop()] = sign * amount
    w = [x / 100 for x in out]
    return {'weights': w, 'ideal': [int(x > 0) for x in out],
            'anti_ideal': [int(x < 0) for x in out]}


def greedy_update(state):
    """Legal score-minimizing, disjoint within-sign weight transfers.

    This is a deliberately adversarial practice P, not a claim of optimal play.
    It tests feedback contamination when the original equations remain exact.
    """
    original = [round(x * 100) for x in state['original_weights']]
    current = [round(x * 100) for x in state['current_weights']]
    x = state['candidate']
    remaining = state['n'] // 20
    used = set()
    while remaining >= 2:
        best = None
        for i, wi in enumerate(original):
            if not wi or i in used:
                continue
            low_i, high_i = wi - abs(wi) // 5, wi + abs(wi) // 5
            # Transfer integer cents from i to j, preserving the sign total.
            give = current[i] - low_i
            if give <= 0:
                continue
            for j, wj in enumerate(original):
                if j == i or j in used or wi * wj <= 0:
                    continue
                high_j = wj + abs(wj) // 5
                amount = min(give, high_j - current[j])
                gain = amount * (x[i] - x[j])
                if amount > 0 and gain > 1e-12 and (best is None or gain > best[0]):
                    best = gain, i, j, amount
        if best is None:
            break
        _, i, j, amount = best
        current[i] -= amount
        current[j] += amount
        used.update((i, j))
        remaining -= 2
    return {'weights': [v / 100 for v in current]}


def simulate(n, seed, propose, preferences, modify=None, clock=120):
    """Run one legal game, preserving referee rounding and early-stop semantics."""
    rng = random.Random(seed)
    original = setup(preferences, n)
    current = original
    training = []
    for _ in range(20):
        c = [D(rng.randrange(2)) for _ in range(n)]
        training.append({'candidate': [float(v) for v in c], 'score': display(dot(original, c))})
    history = list(training)
    previous = []
    best, success, m_time, p_time = D(-1), False, 0.0, 0.0
    trace = []
    for turn in range(1, 21):
        begin = time.perf_counter()
        c = candidate(propose({'type': 'candidate', 'n': n, 'round': turn,
                              'history': history, 'time_remaining': max(0, clock - m_time)}), n)
        m_time += time.perf_counter() - begin
        if m_time > clock:
            raise TimeoutError('M exceeded practice clock %.3fs' % m_time)
        if modify is not None:
            begin = time.perf_counter()
            reply = modify({'type': 'modify', 'n': n, 'round': turn,
                            'candidate': [float(v) for v in c], 'history': previous,
                            'original_weights': [float(v) for v in original],
                            'current_weights': [float(v) for v in current],
                            'time_remaining': max(0, clock - p_time)})
            current = update(reply, original, current)
            p_time += time.perf_counter() - begin
            if p_time > clock:
                raise TimeoutError('P exceeded practice clock %.3fs' % p_time)
        value = dot(current, c)
        score = display(value)
        success = dot(original, c) == 1
        best = max(best, D(score))
        row = {'candidate': [float(v) for v in c], 'score': score}
        previous.append(row)
        history.append(row)
        trace.append({'round': turn, 'score': score, 'original_score': display(dot(original, c))})
        if success:
            best = D(1)
            break
    return {'n': n, 'seed': seed, 'score': float(best), 'perfect': success,
            'turns': turn, 'm_seconds': m_time, 'p_seconds': p_time, 'trace': trace}


def solve(matrix, target):
    """Small positive-definite dense solve for a portable ridge baseline."""
    n = len(target)
    a = [list(row) + [target[i]] for i, row in enumerate(matrix)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(a[j][i]))
        a[i], a[pivot] = a[pivot], a[i]
        value = a[i][i]
        if abs(value) < 1e-12:
            continue
        for k in range(i, n + 1):
            a[i][k] /= value
        for j in range(i + 1, n):
            ratio = a[j][i]
            for k in range(i, n + 1):
                a[j][k] -= ratio * a[i][k]
    out = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(a[i][i]) > 1e-12:
            out[i] = a[i][-1] - sum(a[i][j] * out[j] for j in range(i + 1, n))
    return out


def regression_candidate(state):
    """Full-history ridge/sign learner for comparison with the supplied sample."""
    n, history = state['n'], state['history']
    rows = [[float(v) - .5 for v in h['candidate']] for h in history]
    # Legal weights sum to zero, so also project every row off the constant axis.
    rows = [[v - sum(row) / n for v in row] for row in rows]
    gram = [[sum(a * b for a, b in zip(x, y)) + (.015 if i == j else 0)
             for j, y in enumerate(rows)] for i, x in enumerate(rows)]
    coefficients = solve(gram, [float(h['score']) for h in history])
    estimate = [sum(coefficients[j] * rows[j][i] for j in range(len(rows))) for i in range(n)]
    candidate = [int(v >= 0) for v in estimate]
    visited = {tuple(h['candidate']) for h in history[20:]}
    if tuple(candidate) in visited:
        # Probe fresh uncertain signs, preserving most of the current estimate.
        for i in sorted(range(n), key=lambda j: abs(estimate[j])):
            fresh = candidate.copy()
            fresh[i] = 1 - fresh[i]
            if tuple(fresh) not in visited:
                return fresh
    return candidate


def summarize(rows):
    return {'games': len(rows), 'mean_score': round(statistics.mean(r['score'] for r in rows), 5),
            'min_score': min(r['score'] for r in rows),
            'perfect_rate': round(sum(r['perfect'] for r in rows) / len(rows), 4),
            'mean_turns': round(statistics.mean(r['turns'] for r in rows), 2),
            'mean_m_seconds': round(statistics.mean(r['m_seconds'] for r in rows), 4),
            'max_m_seconds': round(max(r['m_seconds'] for r in rows), 4),
            'max_p_seconds': round(max(r['p_seconds'] for r in rows), 4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--n', nargs='+', type=int, default=[20, 40, 60, 100, 200])
    parser.add_argument('--seeds', type=int, default=3, help='number of reproducible seeds, beginning at 0')
    parser.add_argument('--role', choices=['m', 'p', 'both'], default='both')
    parser.add_argument('--distributions', nargs='+', default=['dense', 'variable', 'sparse', 'pair', 'mutable', 'tail'])
    parser.add_argument('--adaptive', action='store_true', help='include a legal adversarial moving P')
    parser.add_argument('--matchmaker', choices=['competitive', 'regression', 'sample-model'], default='competitive')
    parser.add_argument('--person-family', choices=['default', 'equal', 'jitter', 'composition', 'mixed'], default='default')
    parser.add_argument('--bot-dir', type=Path, default=ROOT / 'submission')
    parser.add_argument('--clock', type=float, default=120)
    parser.add_argument('--output', type=Path, help='write inspectable full results as JSON')
    args = parser.parse_args()
    # Freeze source before the sweep: another agent can continue development
    # without silently mixing implementations between freshly imported games.
    source_dir = args.bot_dir.resolve()
    snapshot = tempfile.TemporaryDirectory(prefix='dating-benchmark-')
    args.bot_dir = Path(snapshot.name) / 'bot'
    shutil.copytree(source_dir, args.bot_dir, ignore=shutil.ignore_patterns('__pycache__'))
    source_files = {str(p.relative_to(args.bot_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(args.bot_dir.rglob('*.py'))}
    source_snapshot = None
    if args.output:
        combined_hash = hashlib.sha256(json.dumps(source_files, sort_keys=True).encode()).hexdigest()
        source_snapshot = args.output.parent / 'benchmark-snapshots' / combined_hash[:12]
        if not source_snapshot.exists():
            source_snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(args.bot_dir, source_snapshot, dirs_exist_ok=True)
    config = {'n': args.n, 'seeds': args.seeds, 'role': args.role,
              'distributions': args.distributions, 'adaptive': args.adaptive,
              'matchmaker': args.matchmaker, 'person_family': args.person_family,
              'clock': args.clock, 'bot_dir': str(source_dir),
              'source_snapshot': str(source_snapshot) if source_snapshot else None}
    all_rows, groups = [], defaultdict(list)

    def save_progress():
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps({'config': config, 'source_sha256': source_files,
                'summary': {key: summarize(rows) for key, rows in groups.items()},
                'games': all_rows}, indent=2) + '\n')
    for n in args.n:
        if args.role in ('m', 'both'):
            for kind in args.distributions:
                for adaptive in ([False, True] if args.adaptive else [False]):
                    key = 'M/n=%d/%s/%s' % (n, kind, 'adaptive' if adaptive else 'static')
                    for seed in range(args.seeds):
                        if args.matchmaker == 'competitive':
                            module = load_module(args.bot_dir / 'matchmaker.py')
                            propose = module.propose_candidate
                        elif args.matchmaker == 'regression':
                            propose = regression_candidate
                        else:
                            from bots.examples.bot import propose as sample_propose
                            propose = lambda state: sample_propose(state, 'model')
                        if kind.startswith('person-'):
                            person = load_module(args.bot_dir / 'person.py')
                            person._rng.seed(seed + 1367 * n)
                            family = kind.partition('-')[2]
                            preferences = (person.set_preferences({'n': n}) if family == 'default'
                                           else person.choose_preferences(n, family))
                            modify = person.modify_preferences if adaptive else None
                        else:
                            preferences = cents_preferences(n, seed + 4729, kind)
                            modify = greedy_update if adaptive else None
                        row = simulate(n, seed, propose, preferences, modify, args.clock)
                        row.update(role='M', method=args.matchmaker, opponent=kind, adaptive=adaptive)
                        all_rows.append(row)
                        groups[key].append(row)
                    print(json.dumps({'case': key, **summarize(groups[key])}), flush=True)
                    save_progress()
        if args.role in ('p', 'both'):
            for method in ('sample-model', 'regression'):
                key = 'P/n=%d/vs-%s' % (n, method)
                for seed in range(args.seeds):
                    module = load_module(args.bot_dir / 'person.py')
                    if hasattr(module, '_rng'):
                        module._rng.seed(seed + 1367 * n)
                    if method == 'regression':
                        propose = regression_candidate
                    else:
                        from bots.examples.bot import propose as sample_propose
                        propose = lambda state: sample_propose(state, 'model')
                    preferences = (module.set_preferences({'type': 'preferences', 'n': n}) if args.person_family == 'default'
                                   else module.choose_preferences(n, args.person_family))
                    row = simulate(n, seed, propose, preferences, module.modify_preferences, args.clock)
                    row.update(role='P', opponent=method)
                    all_rows.append(row)
                    groups[key].append(row)
                print(json.dumps({'case': key, **summarize(groups[key])}), flush=True)
                save_progress()
    save_progress()


if __name__ == '__main__':
    main()
