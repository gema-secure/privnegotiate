"""补跑 §15.3 要求的消融：λ 档位、去类别共享、去顶项似然、固定未知基线。"""
from __future__ import annotations
import sys, json
from dataclasses import replace
from fractions import Fraction
sys.path.insert(0, "backend")

from app.simulation.grid import frozen_grid
from app.simulation.runner import ExperimentConfig, run_instance, summarize
from app.simulation.responders import Policy

SEEDS = list(range(1001, 1021))
SCEN = {s.name: s for s in frozen_grid()}
TARGETS = ["size_d4_n20", "size_d3_n13", "cond_minority", "cond_heterogeneous", "cond_skip_30"]

VARIANTS = {
    "baseline(λ=.25)": {},
    "λ=0": {"lam": Fraction(0)},
    "λ=0.5": {"lam": Fraction(1, 2)},
    "去类别共享": {"category_sharing": False},
    "去顶项似然": {"top_likelihood": False},
    "固定未知(1/2分)": {"fixed_unknown": True},
}

print(f"{'scenario':22s} {'variant':18s} {'regret':>8s} {'avg':>7s} {'worst':>7s} {'eps':>7s} {'cov':>6s}")
print("-" * 84)
for sc in TARGETS:
    base_cfg = SCEN[sc].config()
    for name, override in VARIANTS.items():
        cfg = replace(base_cfg, **override) if override else base_cfg
        rows = [run_instance(seed, "heuristic", Policy.HONEST, cfg) for seed in SEEDS]
        s = summarize(rows)
        print(f"{sc:22s} {name:18s} {s['mean_regret']:8.4f} {s['mean_average_utility']:7.4f} "
              f"{s['mean_worst_utility']:7.4f} {s['mean_epsilon_safe']:7.4f} {s['mean_coverage']:6.3f}")
    print("-" * 84)
