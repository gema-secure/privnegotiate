"""运行固定种子实验：python -m app.simulation.run --strategies heuristic,random"""

from __future__ import annotations

import argparse
import json
from fractions import Fraction

from app.simulation.baselines import STRATEGIES
from app.simulation.generators import InstanceParams
from app.simulation.responders import Policy
from app.simulation.runner import (
    ExperimentConfig,
    apply_ablation,
    run_instance,
    summarize,
)


def _parse_seeds(raw: str) -> list[int]:
    if "-" in raw:
        start, end = raw.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(item) for item in raw.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="私密协商器 P5 可复现实验")
    parser.add_argument("--strategies", default="heuristic,random,uncertainty,top2,symmetric_difference")
    parser.add_argument("--policies", default="honest,selective,blanket")
    parser.add_argument("--seeds", default="1-5")
    parser.add_argument("--members", type=int, default=4)
    parser.add_argument("--candidates", type=int, default=20)
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--ablation", default="none")
    parser.add_argument("--solver-time", type=float, default=0.5)
    parser.add_argument("--drift-round", type=int, default=0, help="0 表示不模拟偏好修改")
    parser.add_argument("--drift-fraction", type=float, default=0.2)
    parser.add_argument("--pair-bonus", type=float, default=0.0, help="同类别成对加成（轻度互补）")
    parser.add_argument("--question-budget", type=int, default=0, help="0 表示不限总展示项")
    args = parser.parse_args()

    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    policies = [Policy(item.strip()) for item in args.policies.split(",") if item.strip()]
    for strategy in strategies:
        if strategy not in STRATEGIES:
            parser.error(f"未知基线：{strategy}")
    seeds = _parse_seeds(args.seeds)

    base = ExperimentConfig(
        params=InstanceParams(
            members=args.members,
            candidates=args.candidates,
            exact_count=args.k,
            pair_bonus=args.pair_bonus,
        ),
        round_limit=args.rounds,
        batch_size=args.batch_size,
        solver_time=args.solver_time,
        lam=Fraction(1, 4),
        drift_round=args.drift_round,
        drift_fraction=args.drift_fraction,
        question_budget=args.question_budget,
    )
    config = apply_ablation(base, args.ablation)

    summary: dict[str, dict[str, dict]] = {}
    for strategy in strategies:
        summary[strategy] = {}
        for policy in policies:
            rows = [run_instance(seed, strategy, policy, config) for seed in seeds]
            summary[strategy][policy.value] = summarize(rows)

    print(
        json.dumps(
            {
                "config": {
                    "members": args.members,
                    "candidates": args.candidates,
                    "k": args.k,
                    "rounds": args.rounds,
                    "seeds": seeds,
                    "ablation": args.ablation,
                    "lambda": float(config.lam),
                    "drift_round": args.drift_round,
                    "pair_bonus": args.pair_bonus,
                    "question_budget": args.question_budget,
                },
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
