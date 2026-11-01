#!/usr/bin/env python3
"""Verify the exact private submission ZIP and play it in fresh bot processes.

Example:
  python3 tools/verify_submission.py --archive dist/compact/dating-hy2821.zip \
      --n 20 40 60 100 200 --seeds 3 --clock 120 \
      --output results/ariadne-package-matches.json

The JSON report is replaced atomically after each completed pairing. Any game
failure, packaging error, or incomplete run produces a nonzero exit status.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import stat
import sys
from tempfile import TemporaryDirectory
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from referee.runner import pairing


EXPECTED_FILES = frozenset((
    'bot.py', 'strategy.py', 'person.py', 'matchmaker.py',
    'README.md', 'SUBMISSION.md',
))
BOT_NAME = 'Ariadne'


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_report(output, report):
    """Persist the latest complete report without leaving a partial JSON file."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.tmp')
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n',
                         encoding='utf-8')
    temporary.replace(output)


def validate_and_extract(archive, destination, compact=False):
    """Return (bot directory, validation metadata), rejecting any extra entry."""
    archive, destination = Path(archive), Path(destination)
    selected_files = frozenset(('bot.py', 'SUBMISSION.md')) if compact else EXPECTED_FILES
    expected = {'hy2821/' + name for name in selected_files}
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError('ZIP contains duplicate paths')
        files = set()
        for entry in entries:
            if entry.is_dir():
                if entry.filename != 'hy2821/':
                    raise ValueError('Unexpected ZIP directory: ' + entry.filename)
                continue
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError('ZIP contains a symbolic link: ' + entry.filename)
            files.add(entry.filename)
        if files != expected:
            raise ValueError('ZIP file list mismatch; missing=%r, extra=%r' %
                             (sorted(expected - files), sorted(files - expected)))
        wrapper = source.read('hy2821/bot.py')
        template = (ROOT / 'bots/templates/python/bot.py').read_bytes()
        if not compact and wrapper != template:
            raise ValueError('Packaged bot.py differs from the supplied Python wrapper')
        bot_directory = destination / 'hy2821'
        bot_directory.mkdir(parents=True)
        for name in sorted(selected_files):
            (bot_directory / name).write_bytes(source.read('hy2821/' + name))
    return bot_directory, {
        'passed': True,
        'folder': 'hy2821',
        'expected_files': sorted(selected_files),
        'actual_files': sorted(path.name for path in bot_directory.iterdir()),
        'wrapper_identical_to_template': wrapper == template,
        'standalone_compact_entry': compact,
        'wrapper_sha256': hashlib.sha256(wrapper).hexdigest(),
    }


def summarize(matches):
    summary = {'pairings': len(matches), 'games': 0, 'wins': 0, 'ties': 0,
               'losses': 0, 'failures': [], 'by_opponent': {},
               'max_clock_seconds': {'Ariadne_P': 0., 'Ariadne_M': 0.,
                                     'opponent_P': 0., 'opponent_M': 0.}}
    for index, match in enumerate(matches):
        opponent = match['opponent']
        group = summary['by_opponent'].setdefault(
            opponent, {'pairings': 0, 'wins': 0, 'ties': 0, 'losses': 0})
        outcome = 'wins' if match['winner'] == BOT_NAME else (
            'ties' if match['winner'] == 'Tie' else 'losses')
        summary[outcome] += 1
        group[outcome] += 1
        group['pairings'] += 1
        for game_index, game in enumerate(match['games']):
            summary['games'] += 1
            if game['failure'] is not None:
                summary['failures'].append({
                    'pairing_index': index, 'game_index': game_index,
                    'n': match['n'], 'seed': match['seed'],
                    'person': game['person'], 'matchmaker': game['matchmaker'],
                    'failure': game['failure'],
                })
            for role, field in (('P', 'person'), ('M', 'matchmaker')):
                key = ('Ariadne_' if game[field] == BOT_NAME else 'opponent_') + role
                summary['max_clock_seconds'][key] = max(
                    summary['max_clock_seconds'][key],
                    game.get('clocks_used', {}).get(role, 0.))
    summary['failure_count'] = len(summary['failures'])
    return summary


def run_verification(archive, sizes, seeds, clock, output, compact=False):
    """Run package validation and both-role pairings, returning the report."""
    archive = Path(archive).resolve()
    begin = time.perf_counter()
    report = {
        'schema_version': 1, 'status': 'running', 'started_at': utc_now(),
        'archive': str(archive), 'archive_sha256': None,
        'python': sys.version, 'platform': platform.platform(),
        'configuration': {'attribute_counts': list(sizes),
                          'seeds': list(range(seeds)), 'clock_seconds': clock,
                          'planned_pairings': len(sizes) * seeds * 2,
                          'fresh_processes_for_each_game': True,
                          'seed_scope': 'referee initial examples; bot preference randomness remains private'},
        'archive_validation': {'passed': False}, 'matches': [],
    }
    report['summary'] = summarize([])
    write_report(output, report)
    try:
        report['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        with TemporaryDirectory(prefix='ariadne-submission-') as directory:
            bot_directory, validation = validate_and_extract(archive, directory, compact=compact)
            report['archive_validation'] = validation
            write_report(output, report)
            bot = {'name': BOT_NAME, 'cwd': str(bot_directory),
                   'command': [sys.executable, 'bot.py']}
            opponents = [
                {'name': 'Sample A · Local search', 'cwd': str(ROOT / 'bots/examples'),
                 'command': [sys.executable, 'bot.py', 'coordinate']},
                {'name': 'Sample B · Model learner', 'cwd': str(ROOT / 'bots/examples'),
                 'command': [sys.executable, 'bot.py', 'model']},
            ]
            for n in sizes:
                for opponent in opponents:
                    for seed in range(seeds):
                        result = pairing(bot, opponent, n=n, clock=clock, seed=seed)
                        result.update({'n': n, 'seed': seed, 'opponent': opponent['name']})
                        report['matches'].append(result)
                        report['summary'] = summarize(report['matches'])
                        report['updated_at'] = utc_now()
                        report['elapsed_seconds'] = round(time.perf_counter() - begin, 3)
                        write_report(output, report)
                        print('%d/%d n=%d seed=%d vs %s: %s; failures=%d' % (
                            len(report['matches']), report['configuration']['planned_pairings'],
                            n, seed, opponent['name'], result['winner'],
                            report['summary']['failure_count']), flush=True)
            report['status'] = 'failed' if report['summary']['failure_count'] else 'passed'
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        report['error'] = 'Interrupted before all planned pairings completed'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = '%s: %s' % (type(error).__name__, error)
    report['finished_at'] = utc_now()
    report['elapsed_seconds'] = round(time.perf_counter() - begin, 3)
    write_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=Path('dist/compact/dating-hy2821.zip'))
    parser.add_argument('--n', nargs='+', type=int, default=[20, 40, 60, 100, 200])
    parser.add_argument('--seeds', type=int, default=3)
    parser.add_argument('--clock', type=float, default=120.)
    parser.add_argument('--compact', action='store_true',
                        help='Validate standalone bot.py plus SUBMISSION.md (custom entry)')
    parser.add_argument('--standard-template', dest='compact', action='store_false',
                        help='Validate the older six-file template package instead')
    parser.set_defaults(compact=True)
    parser.add_argument('--output', type=Path, default=Path('results/ariadne-package-matches.json'))
    args = parser.parse_args()
    if args.seeds < 1 or args.clock <= 0 or any(n < 2 or n > 200 for n in args.n):
        parser.error('Require seeds >= 1, clock > 0, and attribute counts in [2, 200]')
    report = run_verification(args.archive, args.n, args.seeds, args.clock, args.output,
                              compact=args.compact)
    print(json.dumps({'status': report['status'], 'summary': report['summary'],
                      'output': str(args.output.resolve()), 'error': report.get('error')},
                     ensure_ascii=False), flush=True)
    return 0 if report['status'] == 'passed' else 130 if report['status'] == 'interrupted' else 1


if __name__ == '__main__':
    raise SystemExit(main())
