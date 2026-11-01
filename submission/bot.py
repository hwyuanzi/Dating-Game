#!/usr/bin/env python3
"""Ariadne 1.1: standalone P/M bot; Python standard library only."""
import json
import sys

"""Person strategy: dense hidden preferences and exact budgeted score denial.

All arithmetic controlling legality uses integer cents. The update solver finds
the cheapest legal vector for the current candidate, including the per-turn
coordinate budget and both separate normalization constraints.
"""

import random

_rng = random.Random()


def _partition(total, count, family):
    if family == "composition":
        cuts = sorted(_rng.sample(range(1, total), count - 1))
        return [b - a for a, b in zip([0] + cuts, cuts + [total])]
    low, extra = divmod(total, count)
    values = [low + (i < extra) for i in range(count)]
    if family == "mixed":
        # At n=200 every nonzero dense weight would reveal a known +/-1-cent
        # alphabet. Paired transfers introduce zeros and two-cent values while
        # adding little concentration. Normalization is unchanged exactly.
        order = list(range(count))
        _rng.shuffle(order)
        pairs = max(1, count // 5)
        for i, j in zip(order[:pairs], order[pairs:2*pairs]):
            values[i] -= 1
            values[j] += 1
    if family == "jitter" and low > 1:
        # Small balanced transfers obscure an easy fixed-magnitude alphabet
        # while keeping the mass spread over every available attribute.
        lower, upper = max(1, low - 1), low + 1 + bool(extra)
        for _ in range(count * 5):
            i, j = _rng.sample(range(count), 2) if count > 1 else (0, 0)
            if i != j and values[i] > lower and values[j] < upper:
                values[i] -= 1
                values[j] += 1
    _rng.shuffle(values)
    return values


def choose_preferences(n, family="auto"):
    """Return a fresh legal preference vector, optionally for local ablations."""
    if family == "auto":
        # Low-dimensional preferences benefit from varied magnitudes and legal
        # feedback denial. Large n instead needs a diffuse distribution; at the
        # largest sizes mix in zeros to conceal the fixed +/-1-cent alphabet.
        family = "composition" if n <= 100 else "mixed" if n >= 160 else "jitter"
    positive_count = n // 2 + (_rng.randrange(2) if n % 2 else 0)
    positive = _partition(100, positive_count, family)
    negative = [-v for v in _partition(100, n - positive_count, family)]
    cents = positive + negative
    _rng.shuffle(cents)
    weights = [v / 100.0 for v in cents]
    return {"weights": weights, "ideal": [int(v > 0) for v in cents],
            "anti_ideal": [int(v < 0) for v in cents]}


def set_preferences(state):
    return choose_preferences(state["n"])


def _side_options(indices, original, current, candidate, budget):
    # State: (changed coordinates, net cents transferred), value: objective
    # difference and a short immutable list of edits. The net transfer is zero
    # at the end of each sign group, exactly preserving its required total.
    dp = {(0, 0): (0, ())}
    for i in indices:
        radius = abs(original[i]) // 5
        if not radius:
            continue
        choices = [(v - current[i], v) for v in
                   range(original[i] - radius, original[i] + radius + 1)
                   if v != current[i]]
        old_states = tuple(dp.items())
        for (used, transfer), (cost, edits) in old_states:
            if used >= budget:
                continue
            for delta, value in choices:
                key = (used + 1, transfer + delta)
                new_cost = cost + delta * candidate[i]
                previous = dp.get(key)
                if previous is None or new_cost < previous[0]:
                    dp[key] = (new_cost, edits + ((i, value),))
    return {used: val for (used, transfer), val in dp.items() if transfer == 0}


def modify_preferences(state):
    budget = state["n"] // 20
    if budget < 2:
        # No single nonzero edit can preserve the corresponding sign sum.
        return {}
    original = [round(100 * v) for v in state["original_weights"]]
    current = [round(100 * v) for v in state["current_weights"]]
    candidate = [round(10000 * v) for v in state["candidate"]]
    plus = [i for i, value in enumerate(original) if value > 0]
    minus = [i for i, value in enumerate(original) if value < 0]
    options = [_side_options(side, original, current, candidate, budget)
               for side in (plus, minus)]
    best_key, best_edits = (0, 0), ()
    for used_p, (cost_p, edits_p) in options[0].items():
        for used_m, (cost_m, edits_m) in options[1].items():
            used = used_p + used_m
            if used <= budget:
                # Neutral transfers still make later feedback harder to fit.
                key = (cost_p + cost_m, -used)
                if key < best_key:
                    best_key, best_edits = key, edits_p + edits_m
    if not best_edits:
        return {}
    for index, value in best_edits:
        current[index] = value
    return {"weights": [v / 100.0 for v in current]}


"""Quantized active inference, using only public per-game observations.

Weights live on an integer-cent grid. For a verified constant-magnitude model,
we recover six uncertain signs in one query by coding its fractional-cent
residue. Binary coordinates outside the probe contribute whole cents, so they
cannot corrupt the code. The integer part provides another linear observation.
Other distributions use discrete Bayesian message passing and active queries.
"""
import math
import random
import time

_MEM = None


def _linear_solution(A, b, n):
    """Exact-rank Gaussian elimination, also used for the small-n endgame."""
    mat = [list(row) + [float(y)] for row, y in zip(A, b)]
    pivots = []
    rank = 0
    for col in range(n):
        p = max(range(rank, len(mat)), key=lambda j: abs(mat[j][col]), default=rank)
        if p >= len(mat) or abs(mat[p][col]) < 1e-8:
            continue
        mat[rank], mat[p] = mat[p], mat[rank]
        row = mat[rank]
        div = row[col]
        for j in range(col, n+1):
            row[j] /= div
        for i in range(len(mat)):
            if i == rank:
                continue
            factor = mat[i][col]
            if abs(factor) < 1e-12:
                continue
            other = mat[i]
            for j in range(col, n+1):
                other[j] -= factor * row[j]
        pivots.append(col)
        rank += 1
        if rank == n:
            answer = [0.] * n
            for j, c in enumerate(pivots):
                answer[c] = mat[j][-1]
            return answer
    return None


def _prior(n, square_mass, divisor=1):
    """Empirical-Bayes integer prior, calibrated by L1 and score variance."""
    t = 200. / n / divisor
    square_mass /= divisor*divisor
    # Diffuse equal weights and positive integer compositions are common
    # hard P choices. Interpolate their laws to the public sample variance.
    lo = int(math.floor(t))
    fraction = t-lo
    concentrated = {lo: 1-fraction, lo+1: fraction}
    if lo == 0:
        concentrated = {0: 1-t, 1: t}
    minimum_q = lo*lo*(1-fraction)+(lo+1)**2*fraction
    shifted = {}
    if t <= 1.00001:
        shifted = {0: max(0.,1-t), 1: min(1.,t)}
    else:
        ratio = (t-1)/t
        shifted = {k: (1-ratio)*ratio**(k-1) for k in range(1,101)}
    r = (math.sqrt(1+t*t)-1)/t
    p0 = (1-r)/(1+r)
    laplace = {0:p0}
    laplace.update({k:2*p0*r**k for k in range(1,101)})
    q_shift = sum(k*k*p for k,p in shifted.items())
    q_lap = sum(k*k*p for k,p in laplace.items())
    target = max(minimum_q, square_mass/n)
    if target <= q_shift+1e-8:
        amount = min(1.,max(0.,(target-minimum_q)/max(q_shift-minimum_q,1e-8)))
        first,second = concentrated,shifted
    elif target <= 1.5*q_lap:
        amount = min(1.,max(0.,(target-q_shift)/max(q_lap-q_shift,1e-8)))
        first,second = shifted,laplace
    else:
        # Sparse opponents have much larger squared mass. Match their active
        # count and retain long tails instead of shrinking large signs away.
        active = max(.025,min(1.,q_lap/target))
        active_t = t/active
        r = (math.sqrt(1+active_t*active_t)-1)/active_t
        p0 = (1-r)/(1+r)
        first = {0:1-active+active*p0}
        first.update({k:2*active*p0*r**k for k in range(1,101)})
        second,amount = first,0.
    # A small broad component protects against a noisy second-moment estimate.
    magnitude = {k:.96*((1-amount)*first.get(k,0)+amount*second.get(k,0))
                 +.04*laplace.get(k,0) for k in range(101)}
    result = [(0,math.log(max(magnitude[0],1e-15)))]
    for k in range(1,100//divisor+1):
        if magnitude[k] > 1e-12:
            value = math.log(magnitude[k]/2)
            result.extend(((k*divisor,value),(-k*divisor,value)))
    return result


def _gamp(A, b, n, known, fixed, prior, steps=130, warm=None):
    """Damped sum-product generalized approximate message passing."""
    m = len(A)
    cols = [[row[i] for row in A] for i in range(n)]
    cols2 = [[z*z for z in col] for col in cols]
    rows2 = [[z*z for z in row] for row in A]
    pv = fixed*fixed if fixed else sum(w*w*math.exp(lp) for w,lp in prior)
    x = [float(known.get(i, warm[i] if warm else 0.)) for i in range(n)]
    v = [0. if i in known else pv for i in range(n)]
    s = [0.] * m
    unknown = [i for i in range(n) if i not in known]
    damp = .35 if fixed else .23
    # First 20 observations are exact initial examples. Later responses can
    # contain bounded adversarial drift, but sub-five-cent weights cannot move.
    noise = [1e-4 if j <= 20 or fixed else .45 for j in range(m)]
    for iteration in range(steps):
        tp = [sum(a*t for a,t in zip(row,v))+noise[j]
              for j,row in enumerate(rows2)]
        p = [sum(a*t for a,t in zip(row,x))-z*q
             for row,z,q in zip(A,tp,s)]
        sn = [(y-z)/max(t,1e-5) for y,z,t in zip(b,p,tp)]
        sv = [1/max(t,1e-5) for t in tp]
        s = [damp*z+(1-damp)*q for z,q in zip(sn,s)]
        movement = 0.
        for i in unknown:
            tr = 1/max(sum(a*t for a,t in zip(cols2[i],sv)),1e-10)
            rr = x[i]+tr*sum(a*z for a,z in zip(cols[i],s))
            if fixed:
                u = fixed*math.tanh(fixed*rr/tr)
                vv = max(fixed*fixed-u*u,1e-6)
            else:
                terms = [(w, lp-.5*(w-rr)*(w-rr)/tr) for w,lp in prior]
                top = max(lp for w,lp in terms)
                mass = [(w, math.exp(lp-top)) for w,lp in terms if lp > top-35]
                total = sum(pp for w,pp in mass)
                u = sum(w*pp for w,pp in mass)/total
                vv = max(1e-5, sum((w-u)*(w-u)*pp for w,pp in mass)/total)
            movement += abs(u-x[i])
            x[i] = (1-damp)*x[i]+damp*u
            v[i] = (1-damp)*v[i]+damp*vv
        if iteration > 50 and movement < .0003:
            break
    return x, v


def _polish_fixed(A,b,x,k,known):
    """Exact discrete residual descent after approximate inference."""
    n=len(x)
    w=[k if z>=0 else -k for z in x]
    unknown=[i for i in range(n) if i not in known]
    cols={i:[row[i] for row in A] for i in unknown}
    norms={i:sum(a*a for a in cols[i]) for i in unknown}
    residual=[y-sum(a*z for a,z in zip(row,w)) for row,y in zip(A,b)]
    for iteration in range(4*n):
        if sum(z*z for z in residual)<.001:
            break
        delta={i:4*w[i]*sum(a*r for a,r in zip(cols[i],residual))+4*k*k*norms[i]
               for i in unknown}
        best=min(unknown,key=lambda i:delta[i]) if unknown else None
        if best is not None and delta[best]<-.001:
            changes=[best]
        else:
            pair=None
            gain=-.001
            for a,i in enumerate(unknown):
                for j in unknown[a+1:]:
                    value=delta[i]+delta[j]+8*w[i]*w[j]*sum(z*t for z,t in zip(cols[i],cols[j]))
                    if value<gain:
                        gain,pair=value,(i,j)
            if pair is None:
                break
            changes=pair
        for i in changes:
            residual=[r+2*w[i]*a for r,a in zip(residual,cols[i])]
            w[i]=-w[i]
    return w,sum(r*r for r in residual)


def _init(state):
    n = state['n']
    initial = state['history'][:20]
    A = [[2*float(z)-1 for z in h['candidate']] for h in initial]
    b = [200*float(h['score']) for h in initial]
    # Integer-cent conservation is a free exact twenty-first equation.
    A.append([1.] * n)
    b.append(0.)
    fixed = None
    if 200 % n == 0 and 200//n <= 4:
        k = 200//n
        if all(abs(100*float(h['score'])/k-round(100*float(h['score'])/k)) < 1e-7
               and (round(100*float(h['score'])/k)+sum(h['candidate'])) % 2 == 0
               for h in initial):
            fixed = k
    square_mass = sum(z*z for z in b[:20])/20
    divisor = 100
    for h in initial:
        divisor = math.gcd(divisor,abs(int(round(100*float(h['score'])))))
    return {'A': A, 'b': b, 'known': {}, 'probed': set(), 'fixed': fixed,
            'prior': _prior(n,square_mass,divisor), 'divisor': divisor,
            'ternary': (divisor if divisor in (2,4)
                        and square_mass <= 1.8*200*divisor else None),
            'ternary_checks': 0,
            'two_code': square_mass <= 2.5*n*(200./n)**2, 'seen': 20, 'pending': None,
            'rng': random.Random(0xD471+97*n), 'last': None, 'variance': None,
            'models': [], 'probe_count': 0}


def _ingest(mem, history):
    if len(history) <= mem['seen']:
        return
    # There is one response to our previous proposal. Decode against every
    # codeword and require exact agreement at the referee's reported precision.
    h = history[-1]
    pending = mem['pending']
    if pending and pending['kind'] == 'generalcode':
        score_bin = int(round(float(h['score'])*10000))
        group,coeff,B = pending['group'],pending['coeff'],pending['bound']
        matches=[]
        if len(group)==1:
            value=score_bin%100
            if value>50:value-=100
            if value!=50:
                matches=[([value if coeff[0]>0 else -value],value)]
        if pending.get('alphabet'):
            alphabet=pending['alphabet']
            for code in range(len(alphabet)**len(group)):
                values=[]
                for _ in group:
                    values.append(alphabet[code%len(alphabet)])
                    code//=len(alphabet)
                product=sum(a*w for a,w in zip(coeff,values))/100.
                if (round(product)-score_bin)%100==0:
                    matches.append((values,product))
        for w0 in range(-B,B+1) if len(group)==2 and not pending.get('alphabet') else []:
            for w1 in range(-B,B+1):
                product=(coeff[0]*w0+coeff[1]*w1)/100.
                if (round(product)-score_bin)%100==0:
                    matches.append(([w0,w1],product))
        if len(matches)==1 and mem['last'] is not None:
            weights,product=matches[0]
            if any(abs(value-mem['last'][i]) > max(5.,4*math.sqrt(max(mem['variance'][i],.001)))
                   for i,value in zip(group,weights)):
                matches=[]
        if len(matches)==1:
            weights,product=matches[0]
            if pending.get('validate_ternary'):
                if any(abs(w) not in (0,mem['ternary']) for w in weights):
                    mem['ternary']=None
                else:
                    mem['ternary_checks']+=1
            mem['probed'].update(group)
            for i,value in zip(group,weights):
                divisor=mem.get('divisor',1)
                originals=[o for o in range(-100,101,divisor)
                           if abs(value-o)<=abs(o)//5]
                if len(originals)==1:
                    # Invert the original-relative drift interval on the
                    # inferred integer lattice, rather than confusing the
                    # current reading with the original preference.
                    mem['known'][i]=originals[0]
                else:
                    # A mutable current weight is a soft observation of its
                    # original value. Scale its row to allow relative drift.
                    scale=max(1.,.3*abs(value))
                    row=[0.]*len(mem['last'])
                    row[i]=1./scale
                    mem['A'].append(row)
                    mem['b'].append(value/scale)
            outside=int(round((score_bin-product)/100.))
            mem['A'].append([2*z-1 for z in pending['binary']])
            mem['b'].append(2.*outside)
        else:
            if pending.get('validate_ternary'):
                mem['ternary']=None
            mem['A'].append([2*float(z)-1 for z in h['candidate']])
            mem['b'].append(200*float(h['score']))
        mem['seen']=len(history)
        mem['pending']=None
        return
    if pending and pending['kind'] == 'code':
        score_bin = int(round(float(h['score'])*10000))
        group, coeff, k = pending['group'], pending['coeff'], mem['fixed']
        matches = []
        for mask in range(1 << len(group)):
            weights = [k if mask >> j & 1 else -k for j in range(len(group))]
            product = sum(a*w for a,w in zip(coeff,weights))/100.
            # Whole cents do not alter the residue. Round-half-up is away
            # from zero, so use the sign of the actual full displayed score.
            rounded = math.floor(product+.5+1e-9) if score_bin >= 0 else math.ceil(product-.5-1e-9)
            if (rounded-score_bin) % 100 == 0:
                matches.append((weights,product))
        if len(matches) == 1:
            weights, product = matches[0]
            mem['known'].update(zip(group,weights))
            row = list(pending['binary'])
            outside = int(round((score_bin-product)/100.))
            mem['A'].append([2*z-1 for z in row])
            mem['b'].append(2.*outside)
        else:
            # A mistaken fixed-magnitude hypothesis must never persist.
            mem['fixed'] = None
            mem['known'].clear()
            mem['A'] = mem['A'][:21]
            mem['b'] = mem['b'][:21]
    elif pending:
        mem['A'].append([2*float(z)-1 for z in h['candidate']])
        mem['b'].append(200*float(h['score']))
    mem['seen'] = len(history)
    mem['pending'] = None


def propose_candidate(state):
    global _MEM
    n, turn, history = state['n'], state['round'], state['history']
    if turn == 1 or _MEM is None:
        _MEM = _init(state)
    mem = _MEM
    _ingest(mem, history)
    A,b = mem['A'],mem['b']
    visited = {tuple(h['candidate']) for h in history[20:]}
    if n <= len(A):
        solution = _linear_solution(A,b,n)
        if solution is not None:
            rounded = [round(z) for z in solution]
            # Exact initial consistency, cent normalization and original
            # precision make this much safer than a raw least-squares sign.
            if (sum(rounded) == 0 and sum(abs(z) for z in rounded) == 200
                and max(abs(sum(a*w for a,w in zip(row,rounded))-y)
                        for row,y in zip(A[:20],b[:20])) < .001
                and tuple(int(z>0) for z in rounded) not in visited):
                mem['pending'] = {'kind':'plain'}
                return [int(z > 0) for z in rounded]
    remaining = float(state.get('time_remaining',120.))
    if remaining < .12:
        mem['pending']={'kind':'plain'}
        return [int(z >= 0) for z in (mem['last'] or [sum(a[i]*y for a,y in zip(A,b)) for i in range(n)])]
    steps = 160 if mem['fixed'] else 100
    if remaining < 10:
        steps = max(2,min(45,int(remaining*10/max(1,21-turn))))
    x,v = _gamp(A,b,n,mem['known'],mem['fixed'],mem['prior'],steps,mem['last'])
    mem['last'],mem['variance'] = x,v
    candidate = [int(z >= 0) for z in x]
    if mem['fixed']:
        k = mem['fixed']
        signs = [k if z >= 0 else -k for z in x]
        error = max(abs(sum(a*w for a,w in zip(row,signs))-y) for row,y in zip(A,b))
        if turn==20 and error>.001 and remaining>10:
            deadline=time.monotonic()+min(12.,remaining*.55)
            x,best=_polish_fixed(A,b,x,k,mem['known'])
            for retry in range(12 if best>.001 else 0):
                if time.monotonic()>deadline:
                    break
                warm=None if retry==0 else [mem['rng'].uniform(-k,k) for _ in range(n)]
                trial,tv=_gamp(A,b,n,mem['known'],k,mem['prior'],400,warm)
                trial,residual=_polish_fixed(A,b,trial,k,mem['known'])
                if residual<best:
                    best,x,v=residual,trial,tv
                if best<.001:
                    break
            candidate=[int(z>=0) for z in x]
            mem['last'],mem['variance']=x,v
        if error < .001 or turn == 20 or len(mem['known']) == n:
            mem['pending'] = {'kind':'plain'}
            return candidate
        unknown = [i for i in range(n) if i not in mem['known']]
        group = sorted(unknown,key=lambda i: abs(x[i]))[:6]
        # Add a little variation among uncertain binary coordinates. This
        # yields an independent extra equation while keeping a high score.
        uncertain = sorted(unknown,key=lambda i: abs(x[i]))[:max(12,len(unknown)//2)]
        for i in uncertain:
            if mem['rng'].random() < .22:
                candidate[i] = mem['rng'].randrange(2)
        binary = list(candidate)
        coeff = [(1 if not binary[i] else -1)*(60*(1 << j)//k)
                 for j,i in enumerate(group)]
        for i,c in zip(group,coeff):
            candidate[i] = round(binary[i]+c/10000.,4)
        mem['pending'] = {'kind':'code','group':group,'coeff':coeff,'binary':binary}
        mem['probe_count'] += 1
        return candidate
    rounded = [round(z) for z in x]
    exact = (sum(rounded) == 0 and sum(abs(z) for z in rounded) == 200
             and max(abs(sum(a*w for a,w in zip(row,rounded))-y)
                     for row,y in zip(A[:20],b[:20])) < .001)
    if exact and tuple(candidate) not in visited:
        mem['pending'] = {'kind':'plain'}
        return candidate
    if n>=40 and turn not in (1,20):
        unknown=[i for i in range(n) if i not in mem['known'] and i not in mem['probed']]
        ternary=mem['ternary'] if mem['ternary'] in (2,4) else None
        checking=bool(ternary and mem['ternary_checks']<2)
        width=(1 if checking else 4 if ternary and len(unknown)>=4 else
               1 if n<80 or not mem['two_code'] else 2)
        # Verify a coarse ternary hypothesis with unaliased one-coordinate
        # readings of the largest likely weights before using a bounded code.
        group=(sorted(unknown,key=lambda i:-abs(x[i])) if checking else
               sorted(unknown,key=lambda i:abs(x[i])/math.sqrt(max(v[i],.001))))[:width]
        if len(group)==width:
            B=4
            coeff=([100*3**j//ternary for j in range(width)] if width==4 else
                   [100] if width==1 else [100,(2*B+1)*100])
            uncertain=sorted(unknown,key=lambda i:abs(x[i]))[:max(12,len(unknown)//2)]
            for i in uncertain:
                if mem['rng'].random()<.22:
                    candidate[i]=mem['rng'].randrange(2)
            binary=list(candidate)
            preserve=width==4
            coeff=[c if not preserve or not binary[i] else -c for i,c in zip(group,coeff)]
            for i,c in zip(group,coeff):
                if not preserve:binary[i]=0
                candidate[i]=round(binary[i]+c/10000.,4)
            mem['pending']={'kind':'generalcode','group':group,'coeff':coeff,'binary':binary,'bound':B}
            if checking:
                mem['pending']['validate_ternary']=True
            if width==4:
                mem['pending']['alphabet']=[-ternary,0,ternary]
            return candidate
    # A fully binary exploitation attempt is made first and at the end. In
    # between, randomized fractional values on uncertain coordinates give
    # high-information measurements while retaining confident good traits.
    rounded = [round(z) for z in x]
    exact = (sum(rounded) == 0 and sum(abs(z) for z in rounded) == 200
             and max(abs(sum(a*w for a,w in zip(row,rounded))-y)
                     for row,y in zip(A[:20],b[:20])) < .001)
    if turn!=20 and (not exact and turn not in (1,5,9,13,17) or tuple(candidate) in visited):
        # Tiny global cent-grid jitter supplies independent directions even
        # when a falsely confident trait stays outside the active subset.
        candidate = [round(1-.01*mem['rng'].randrange(3),2) if z else
                     round(.01*mem['rng'].randrange(3),2) for z in candidate]
        order = sorted(range(n),key=lambda i: abs(x[i])/math.sqrt(max(v[i],.001)))
        count = min(n,max(8,n//3))
        for i in order[:count]:
            if mem['rng'].random() < .7:
                candidate[i] = round(mem['rng'].random(),2)
    mem['pending'] = {'kind':'plain'}
    return candidate


def main():
    for line in sys.stdin:
        s = json.loads(line)
        kind = s['type']
        if kind == 'start':
            answer = {'ready': True}
        elif kind == 'preferences':
            answer = set_preferences(s)
        elif kind == 'modify':
            answer = modify_preferences(s)
        elif kind == 'candidate':
            answer = {'candidate': propose_candidate(s)}
        else:
            raise ValueError('Unknown message: ' + kind)
        print(json.dumps(answer, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
