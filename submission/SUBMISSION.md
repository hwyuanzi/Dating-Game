# Ariadne bot handoff

- Author / NetID: hy2821
- Contact: hy2821@nyu.edu
- Bot name: Ariadne
- Version / date: 1.1 / November 1, 2026
- Replaces: local 1.0-compact development build
- Referee base: 93e57b0; 5% of coordinates per response, 20% original-weight bounds
- Language: Python; standard library only
- Tested runtime / OS: CPython 3.14.7, macOS arm64
- Intended runtime: Python 3.8+; source checked with Python 3.8 grammar
- Entry command from this folder: `python3 bot.py`
- Build / installation commands: none
- Additional dependencies / resources / network services: none
- Both P and M implementations and the NDJSON adapter: `bot.py`
- Process / thread count: one / one
- Expected RAM: 32 MiB; allow 64 MiB
- Startup: immediate ready response; inference starts on the first candidate request
- Storage: in-process state within one game only; no files or cross-game state

`bot.py` is self-contained and imports only Python standard-library modules.
It accepts the official `start`, `preferences`, `modify`, and `candidate`
messages, emits one flushed JSON reply per line, and handles either role.
The protocol dispatch matches the supplied Python launcher, with its strategy
functions embedded in the same file. The submission checklist requests an
architect adapter check for a custom entry. Please check this standalone entry
before the cutoff; local tests do not constitute tournament-host approval.

## Validation and reproduction

From the repository root:

```sh
python3 -m unittest discover -s tests
python3 tools/package_compact_bot.py
python3 tools/verify_submission.py --compact --archive dist/compact/dating-hy2821.zip --n 20 40 60 100 200 --seeds 3 --output results/ariadne-1.1-package.json
```

The archive contains only `hy2821/bot.py` and `hy2821/SUBMISSION.md`.
The verifier extracts the actual ZIP into a clean directory and runs fresh
processes for both roles against both supplied examples. See
`docs/ARIADNE.md` in the repository for the measured results and scope.
No external repository file is needed to run the submitted bot.

## Strategy and limitations

P generates randomized cent-grid preferences and uses an integer dynamic
program to minimize the current score while preserving the coordinate budget,
separate sign sums and original-relative bounds. M combines discrete Bayesian
inference with fractional-residue probes, exact small-system solves, and a
fixed-magnitude residual search. Version 1.1 adds four-coordinate ternary probes
for likely coarse-lattice opponents, score-preserving signed probes for the
fixed/ternary models, and inversion of drift bounds on the inferred lattice.

General bounded decoding and the constant/ternary model choices are statistical
hypotheses. Out-of-alphabet weights can alias to valid codewords; inference does
not guarantee recovery against every legal P. Source-grammar checks do not
replace a runtime test on the final host. Competition dimensions, clock, host,
submission cutoff and acceptance of a lone Python attachment must follow the
architect's latest announcements.
