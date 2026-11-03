"""量化 §6.1 自述风险：选择性低报在本实现里到底能否占到便宜。"""
from __future__ import annotations
import sys, itertools
from fractions import Fraction
sys.path.insert(0, "backend")

from app.simulation.generators import generate_instance, InstanceParams
from app.simulation.runner import build_model
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolverConfig

W = 1.0
LAM = Fraction(1, 4)
PARAMS = InstanceParams(members=3, candidates=13, exact_count=2)


def true_util(inst, ids):
    idx = [inst.candidate_index(c) for c in ids]
    return [sum(inst.mu[m][i] for i in idx) / (inst.exact_count * (1 + W))
            for m in range(len(inst.member_ids))]


def mu_rows(inst, reports):
    rows = []
    for m, acc in enumerate(reports):
        row = []
        for j, cid in enumerate(inst.candidate_ids):
            if cid in acc:
                row.append(1.0 + W / (1.0 + 2.718281828 ** (-inst.strength[m][j])))
            else:
                row.append(0.0)
        rows.append(row)
    return rows


def solve(model, mu, lam=LAM):
    r = solve_mean_fair(mu, model, SolverConfig(lam=lam, time_limit_seconds=5))
    return r.candidate_ids


wins = {Fraction(0): 0, Fraction(1, 4): 0, Fraction(1, 2): 0}
gains = {Fraction(0): [], Fraction(1, 4): [], Fraction(1, 2): []}
base = {Fraction(0): [], Fraction(1, 4): [], Fraction(1, 2): []}
total = 0

for seed in range(3000, 3120):
    inst = generate_instance(seed, PARAMS)
    model = build_model(inst)
    cids = list(inst.candidate_ids)
    honest = [{c for j, c in enumerate(cids) if inst.acceptance[m][j]} for m in range(3)]
    for lam in wins:
        hu = true_util(inst, solve(model, mu_rows(inst, honest), lam))[0]
        base[lam].append(hu)
        acc0 = sorted(honest[0], key=lambda c: -inst.strength[0][inst.candidate_index(c)])
        best = hu
        for size in (1, 2, 3):
            for combo in itertools.combinations(acc0, size):
                reports = [set(combo), honest[1], honest[2]]
                u = true_util(inst, solve(model, mu_rows(inst, reports), lam))[0]
                best = max(best, u)
        if best > hu + 1e-9:
            wins[lam] += 1
            gains[lam].append(best - hu)
    total += 1

for lam in (Fraction(0), Fraction(1, 4), Fraction(1, 2)):
    g = sorted(gains[lam])
    med = g[len(g) // 2] if g else 0.0
    print(f"λ={float(lam):.2f}  低报获益实例 {wins[lam]}/{total} = {wins[lam]/total:5.1%}   "
          f"中位增益 {med:.3f}  最大 {g[-1] if g else 0:.3f}   "
          f"诚实基线本人效用均值 {sum(base[lam])/total:.3f}")
