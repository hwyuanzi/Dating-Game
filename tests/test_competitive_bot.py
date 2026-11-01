"""Rule and protocol checks for the private competitive submission."""
from pathlib import Path
from decimal import Decimal
import random
import sys
import unittest

from referee.engine import candidate, display, dot, setup, update
from referee.runner import game
from tools.benchmark import cents_preferences, greedy_update, load_module, simulate

ROOT = Path(__file__).resolve().parents[1]
BOT = ROOT / 'submission'


@unittest.skipUnless((BOT / 'bot.py').exists(), 'standalone competitive bot not present')
class CompetitiveStrategy(unittest.TestCase):
    def test_person_legality_and_adversarial_update_sequence(self):
        for n in (2, 3, 19, 20, 39, 40, 60, 100, 199, 200):
            with self.subTest(n=n):
                person = load_module(BOT / 'person.py')
                original = setup(person.set_preferences({'type': 'preferences', 'n': n}), n)
                current = original
                rng = random.Random(918 + n)
                history = []
                for turn in range(1, 21):
                    # Alternate broad binary attacks and high-precision candidates.
                    c = [rng.randrange(2) if turn % 2 else rng.randrange(10001) / 10000 for _ in range(n)]
                    reply = person.modify_preferences({'type': 'modify', 'n': n, 'round': turn,
                        'candidate': c, 'history': history, 'time_remaining': 120,
                        'original_weights': [float(x) for x in original],
                        'current_weights': [float(x) for x in current]})
                    current = update(reply, original, current)
                    history.append({'candidate': c, 'score': '0.0000'})

    def test_person_update_finds_exact_best_legal_transfer(self):
        n = 40
        person = load_module(BOT / 'person.py')
        original = setup({'weights': [.5, .5, -.5, -.5] + [0] * 36,
                          'ideal': [1, 1, 0, 0] + [0] * 36,
                          'anti_ideal': [0, 0, 1, 1] + [0] * 36}, n)
        current = original
        attacks = ([.1, .9, .7, .3], [.8, .2, .1, .9], [.4, .6, .9, .1])
        for turn, attack in enumerate(attacks, 1):
            c = candidate(attack + [0] * 36, n)
            alternatives = [current]
            for left, right in ((0, 1), (2, 3)):
                for delta in range(-20, 21):
                    trial = list(current)
                    trial[left] += type(current[0])(delta) / 100
                    trial[right] -= type(current[0])(delta) / 100
                    try:
                        alternatives.append(update({'weights': [float(v) for v in trial]}, original, current))
                    except ValueError:
                        pass
            response = person.modify_preferences({'type': 'modify', 'n': n, 'round': turn,
                'candidate': [float(v) for v in c], 'history': [], 'time_remaining': 120,
                'original_weights': [float(v) for v in original],
                'current_weights': [float(v) for v in current]})
            current = update(response, original, current)
            self.assertEqual(dot(current, c), min(dot(w, c) for w in alternatives))

    def test_matchmaker_solves_small_identifiable_games(self):
        for n in (2, 3, 8, 19, 20):
            with self.subTest(n=n):
                mm = load_module(BOT / 'matchmaker.py')
                result = simulate(n, 54, mm.propose_candidate, cents_preferences(n, 191, 'variable'))
                self.assertTrue(result['perfect'], result)

    def test_fractional_code_decodes_all_signs_and_rounding_directions(self):
        mm = load_module(BOT / 'matchmaker.py')
        for magnitude in (1, 2, 4):
            n = 200 // magnitude
            initial = [{'candidate': [0] * n, 'score': '0.0000'} for _ in range(20)]
            group = list(range(6))
            coefficients = [(60 * (1 << j)) // magnitude for j in group]
            for mask in range(64):
                weights = [magnitude if mask >> j & 1 else -magnitude for j in group]
                for outside in (-5 * magnitude, 0, 5 * magnitude):
                    with self.subTest(magnitude=magnitude, mask=mask, outside=outside):
                        memory = mm._init({'n': n, 'history': initial})
                        self.assertEqual(memory['fixed'], magnitude)
                        memory['pending'] = {'kind': 'code', 'group': group,
                            'coeff': coefficients, 'binary': [0] * n}
                        # Outside binary traits contribute whole cents; the
                        # six probes add fractional-cent residues. Decimal
                        # exercises both signs of referee round-half-up.
                        score = Decimal(outside) / 100 + sum(
                            Decimal(a * w) for a, w in zip(coefficients, weights)) / 1000000
                        mm._ingest(memory, initial + [{'candidate': [0] * n, 'score': display(score)}])
                        self.assertEqual(memory['known'], dict(zip(group, weights)))
                        self.assertEqual(memory['fixed'], magnitude)
                        self.assertEqual(memory['b'][-1], 2 * outside)

    def test_general_code_grid_and_mutable_weight_observations(self):
        mm = load_module(BOT / 'matchmaker.py')
        n = 100
        initial = [{'candidate': [0] * n, 'score': '0.0000'} for _ in range(20)]
        value_sets = [(w,) for w in range(-49, 50)]
        value_sets += [(a, b) for a in range(-4, 5) for b in range(-4, 5)]
        for values in value_sets:
            group = list(range(len(values)))
            coefficients = [100] if len(values) == 1 else [100, 900]
            for outside in (-20, 0, 20):
                with self.subTest(values=values, outside=outside):
                    last = list(values) + [0.] * (n - len(values))
                    memory = {'A': [[0.] * n for _ in range(21)], 'b': [0.] * 21,
                        'known': {}, 'probed': set(), 'last': last, 'variance': [.25] * n,
                        'seen': 20, 'pending': {'kind': 'generalcode', 'group': group,
                            'coeff': coefficients, 'binary': [0] * n, 'bound': 4}}
                    score = Decimal(outside) / 100 + sum(
                        Decimal(a * w) for a, w in zip(coefficients, values)) / 1000000
                    mm._ingest(memory, initial + [{'candidate': [0] * n, 'score': display(score)}])
                    # Only immutable readings can pin an original coordinate.
                    self.assertEqual(memory['known'], {i: w for i, w in zip(group, values) if abs(w) <= 3})
                    self.assertEqual(memory['probed'], set(group))
                    soft = [(i, w) for i, w in zip(group, values) if abs(w) >= 4]
                    self.assertEqual(len(memory['A']), 22 + len(soft))
                    for row, target, (i, w) in zip(memory['A'][21:], memory['b'][21:], soft):
                        scale = max(1., .3 * abs(w))
                        self.assertAlmostEqual(row[i], 1 / scale)
                        self.assertAlmostEqual(target, w / scale)
                    self.assertEqual(memory['b'][-1], 2 * outside)
        # A half-period single-coordinate residue has two possible signs.
        memory = {'A': [], 'b': [], 'known': {}, 'probed': set(), 'last': [50.] + [0.] * 99,
            'variance': [.25] * n, 'seen': 20, 'pending': {'kind': 'generalcode',
                'group': [0], 'coeff': [100], 'binary': [0] * n, 'bound': 4}}
        mm._ingest(memory, initial + [{'candidate': [.01] + [0] * 99, 'score': '.0050'}])
        self.assertEqual(memory['known'], {})
        self.assertEqual(memory['probed'], set())

    def test_matchmaker_handles_dense_sparse_and_moving_preferences(self):
        for n, kind in ((40, 'mutable'), (100, 'sparse'), (200, 'dense')):
            with self.subTest(n=n, kind=kind):
                mm = load_module(BOT / 'matchmaker.py')
                result = simulate(n, 29, mm.propose_candidate,
                                  cents_preferences(n, 190, kind), greedy_update)
                self.assertTrue(-1 <= result['score'] <= 1)
                self.assertTrue(1 <= result['turns'] <= 20)
                self.assertLess(result['m_seconds'], 120)

    def test_four_coordinate_codes_with_signed_binary_baselines(self):
        mm = load_module(BOT / 'matchmaker.py')
        n = 100
        initial = [{'candidate': [0] * n, 'score': '0.0000'} for _ in range(20)]
        for magnitude in (2, 4):
            alphabet = [-magnitude, 0, magnitude]
            for mask in range(16):
                binary = [(mask >> i) & 1 for i in range(4)] + [0] * (n - 4)
                coeff = [(1 if not binary[i] else -1) * 100 * 3**i // magnitude
                         for i in range(4)]
                for code in range(81):
                    values = [alphabet[(code // 3**i) % 3] for i in range(4)]
                    for outside in (-20, 0, 20):
                        baseline = outside + sum(a*w for a,w in zip(binary, values))
                        score = Decimal(baseline) / 100 + sum(
                            Decimal(a*w) for a,w in zip(coeff,values)) / 1000000
                        memory = {'A': [], 'b': [], 'known': {}, 'probed': set(),
                            'last': values + [0] * (n-4), 'variance': [.25] * n,
                            'seen': 20, 'pending': {'kind': 'generalcode',
                                'group': [0,1,2,3], 'coeff': coeff, 'binary': binary,
                                'bound': 4, 'alphabet': alphabet}}
                        mm._ingest(memory, initial + [{'candidate': binary, 'score': display(score)}])
                        self.assertEqual(memory['known'],
                                         {i:w for i,w in enumerate(values) if abs(w)<=3})
                        self.assertEqual(memory['probed'], {0,1,2,3})
                        self.assertEqual(memory['b'][-1], 2 * baseline)
                        self.assertEqual(memory['A'][-1], [2*z-1 for z in binary])

    def test_single_coordinate_probe_can_perturb_down_from_one(self):
        mm = load_module(BOT / 'matchmaker.py')
        initial = [{'candidate': [0] * 40, 'score': '0.0000'} for _ in range(20)]
        for value in range(-49, 50):
            memory = {'A': [], 'b': [], 'known': {}, 'probed': set(),
                'last': [value] + [0] * 39, 'variance': [.25] * 40,
                'seen': 20, 'pending': {'kind': 'generalcode', 'group': [0],
                    'coeff': [-100], 'binary': [1] + [0] * 39, 'bound': 4}}
            score = Decimal(value) / 100 - Decimal(value) / 10000
            mm._ingest(memory, initial + [{'candidate': [.99]+[0]*39, 'score': display(score)}])
            self.assertEqual(memory['known'], {0:value} if abs(value)<=3 else {})
            self.assertEqual(memory['b'][-1], 2*value)
            self.assertEqual(memory['probed'], {0})

    def test_original_weight_is_recovered_from_drift_on_coarse_lattice(self):
        mm = load_module(BOT / 'matchmaker.py')
        initial = [{'candidate': [0] * 40, 'score': '0.0000'} for _ in range(20)]
        for observed, original in ((4,4), (7,8), (9,8), (10,12), (13,None)):
            for sign in (-1,1):
                value=sign*observed
                memory = {'A': [], 'b': [], 'known': {}, 'probed': set(),
                    'divisor': 4, 'last': [value] + [0] * 39, 'variance': [.25] * 40,
                    'seen': 20, 'pending': {'kind': 'generalcode', 'group': [0],
                        'coeff': [100], 'binary': [0] * 40, 'bound': 4}}
                score=Decimal(value)/10000
                mm._ingest(memory, initial + [{'candidate': [.01]+[0]*39, 'score': display(score)}])
                self.assertEqual(memory['known'], {0:sign*original} if original is not None else {})

    def test_ternary_model_is_checked_before_bounded_decoding(self):
        mm = load_module(BOT / 'matchmaker.py')
        initial = [{'candidate': [0] * 100, 'score': '0.0000'} for _ in range(20)]
        for value, expected, checks in ((2,2,1), (0,2,1), (4,None,0), (-6,None,0)):
            memory = {'A': [], 'b': [], 'known': {}, 'probed': set(),
                'divisor': 2, 'ternary': 2, 'ternary_checks': 0,
                'last': [value] + [0] * 99, 'variance': [.25] * 100,
                'seen': 20, 'pending': {'kind': 'generalcode', 'group': [0],
                    'coeff': [100], 'binary': [0] * 100, 'bound': 4,
                    'validate_ternary': True}}
            mm._ingest(memory, initial + [{'candidate': [.01]+[0]*99,
                                         'score': display(Decimal(value)/10000)}])
            self.assertEqual(memory['ternary'], expected)
            self.assertEqual(memory['ternary_checks'], checks)

    def test_fixed_sign_codes_preserve_a_signed_binary_baseline(self):
        mm = load_module(BOT / 'matchmaker.py')
        for magnitude in (1,2,4):
            n=200//magnitude
            initial=[{'candidate':[0]*n,'score':'0.0000'} for _ in range(20)]
            for baseline_mask in (0,21,42,63):
                binary=[(baseline_mask>>i)&1 for i in range(6)]+[0]*(n-6)
                coeff=[(1 if not binary[i] else -1)*(60*2**i//magnitude) for i in range(6)]
                for mask in range(64):
                    values=[magnitude if mask>>i&1 else -magnitude for i in range(6)]
                    for outside in (-5,0,5):
                        baseline=outside+sum(a*w for a,w in zip(binary,values))
                        memory=mm._init({'n':n,'history':initial})
                        memory['pending']={'kind':'code','group':list(range(6)),
                                           'coeff':coeff,'binary':binary}
                        score=Decimal(baseline)/100+sum(
                            Decimal(a*w) for a,w in zip(coeff,values))/1000000
                        mm._ingest(memory,initial+[{'candidate':binary,'score':display(score)}])
                        self.assertEqual(memory['known'],dict(enumerate(values)))
                        self.assertEqual(memory['b'][-1],2*baseline)

    def test_actual_wrapper_and_persistent_both_role_protocol(self):
        if not (BOT / 'bot.py').exists():
            self.skipTest('wrapper not present')
        ours = {'name': 'Competitive', 'cwd': str(BOT), 'command': [sys.executable, 'bot.py']}
        sample = {'name': 'Sample-model', 'cwd': str(ROOT / 'bots' / 'examples'),
                  'command': [sys.executable, 'bot.py', 'model']}
        for n in (20, 100, 200):
            for p, m in ((ours, sample), (sample, ours)):
                with self.subTest(n=n, person=p['name']):
                    result = game(p, m, n=n, clock=120, seed=192 + n)
                    self.assertIsNone(result['failure'], result['failure'])
                    self.assertEqual(len(result['training']), 20)
                    self.assertTrue(1 <= result['candidates_used'] <= 20)


if __name__ == '__main__':
    unittest.main()
