#!/usr/bin/env python3
"""Build a two-file handoff without changing the tested strategy algorithms."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path,
                        help='Optional development modules to merge; otherwise package submission/bot.py')
    args = parser.parse_args()
    canonical = ROOT / 'submission'
    canonical.mkdir(parents=True, exist_ok=True)
    output = ROOT / 'dist' / 'compact'
    folder = output / 'hy2821'
    folder.mkdir(parents=True, exist_ok=True)
    if args.source_dir:
        source = args.source_dir
        wrapper = (source / 'bot.py').read_text()
        dispatch = wrapper[wrapper.index('for line in sys.stdin:'):]
        code = ('#!/usr/bin/env python3\n'
                '"""Ariadne 1.1: standalone P/M bot; Python standard library only."""\n'
                'import json\nimport sys\n\n'
                + (source / 'person.py').read_text() + '\n\n'
                + (source / 'matchmaker.py').read_text() + '\n\n'
                + 'def main():\n'
                + ''.join('    ' + line + '\n' for line in dispatch.splitlines())
                + '\n\nif __name__ == "__main__":\n    main()\n')
    else:
        code = (canonical / 'bot.py').read_text()
    ast.parse(code, feature_version=(3, 8))
    (canonical / 'bot.py').write_text(code)
    (folder / 'bot.py').write_text(code)
    submission = (canonical / 'SUBMISSION.md').read_text()
    (folder / 'SUBMISSION.md').write_text(submission)
    archive = output / 'dating-hy2821.zip'
    contents = {name: (folder / name).read_bytes()
                for name in ('bot.py', 'SUBMISSION.md')}
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in contents.items():
            item = zipfile.ZipInfo('hy2821/' + name, (2026, 11, 1, 0, 0, 0))
            item.external_attr = 0o100644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(item, data)
    manifest = {
        'archive': str(archive),
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'files': {name: hashlib.sha256(data).hexdigest()
                  for name, data in contents.items()},
    }
    if args.source_dir:
        manifest['embedded_sources'] = {
            name: hashlib.sha256((args.source_dir / name).read_bytes()).hexdigest()
            for name in ('person.py', 'matchmaker.py')}
    archive.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
