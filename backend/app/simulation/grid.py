"""§15.2 固定真值网格：冻结规模与条件，50 seeds × 5 策略 × 3 人格。

结果写入 experiments/，同行比较使用同一实例、同一响应机制、同一总展示项预算
与同一求解墙钟上限。
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from app.simulation.baselines import STRATEGIES
from app.simulation.generators import InstanceParams
from app.simulation.responders import Policy, underreporting_members
from app.simulation.runner import (
    ExperimentConfig,
    ablation_names,
    apply_ablation,
    manipulation_report,
    run_instance,
    summarize,
)

FORMAL_SEED_START = 1001
FORMAL_SEEDS = 50
SOLVER_TIME = 0.25
ROUND_LIMIT = 4
BATCH_SIZE = 4
ITEMS_PER_MEMBER_BUDGET = 12


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    params: InstanceParams
    drift_round: int = 0
    drift_fraction: float = 0.2
    skip_rate: float = 0.05

    def config(self) -> ExperimentConfig:
        return ExperimentConfig(
            params=self.params,
            round_limit=ROUND_LIMIT,
            solver_time=SOLVER_TIME,
            skip_rate=self.skip_rate,
            batch_size=BATCH_SIZE,
            question_budget=ITEMS_PER_MEMBER_BUDGET * self.params.members,
            drift_round=self.drift_round,
            drift_fraction=self.drift_fraction,
        )


def frozen_grid() -> tuple[Scenario, ...]:
    """事先冻结的规模网格与条件网格；正式报告的 seed 不参与调参。"""
    base = InstanceParams(members=4, candidates=20, exact_count=2)
    sizes = tuple(
        Scenario(
            f"size_d{members}_n{candidates}",
            InstanceParams(members=members, candidates=candidates, exact_count=2),
        )
        for members in (3, 4, 6)
        for candidates in (13, 20, 30)
    )
    # 产品支持 3–15 人；补齐 10–15 人规模，验证大规模下仍可收敛。
    large_sizes = tuple(
        Scenario(
            f"size_d{members}_n20",
            InstanceParams(members=members, candidates=20, exact_count=2),
        )
        for members in (10, 12, 15)
    )
    conditions = (
        Scenario("cond_heterogeneous", replace(base, correlation="heterogeneous")),
        Scenario("cond_independent", replace(base, correlation="independent")),
        Scenario("cond_minority", replace(base, minority=True)),
        Scenario("cond_drift", base, drift_round=2, drift_fraction=0.5),
        Scenario("cond_skip_0", base, skip_rate=0.0),
        Scenario("cond_skip_30", base, skip_rate=0.30),
        Scenario("cond_k3", replace(base, exact_count=3)),
        Scenario("cond_tight_budget", replace(base, budget_slack_cents=500)),
        Scenario("cond_pair_bonus_0_2", replace(base, pair_bonus=0.2)),
    )
    return sizes + large_sizes + conditions


def _parse_seeds(raw: str) -> list[int]:
    if "-" in raw:
        start, end = raw.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(item) for item in raw.split(",") if item.strip()]


def _run_cell(
    scenario: Scenario,
    strategy: str,
    policy: Policy,
    seeds: Sequence[int],
    ablation: str = "none",
) -> dict[str, Any]:
    config = apply_ablation(scenario.config(), ablation)
    rows = [run_instance(seed, strategy, policy, config) for seed in seeds]
    summary = summarize(rows)
    statuses: dict[str, int] = {}
    for row in rows:
        statuses[row.solver_status] = statuses.get(row.solver_status, 0) + 1
    stable = [row for row in rows if row.stable]
    summary.update(
        {
            "kind": "cell",
            "scenario": scenario.name,
            "strategy": strategy,
            "policy": policy.value,
            "ablation": ablation,
            "solver_status_counts": dict(sorted(statuses.items())),
            "non_optimal_rate": round(
                sum(1 for row in rows if row.solver_status not in ("OPTIMAL", "FEASIBLE"))
                / len(rows),
                6,
            ),
            "unmet_rate": round(sum(1 for row in rows if row.regret > 0.05) / len(rows), 6),
            "stable_misjudgment_given_stable": round(
                sum(1 for row in stable if row.stable_misjudgment) / len(stable), 6
            )
            if stable
            else 0.0,
            "runs": len(rows),
        }
    )
    return summary


def run_cell(
    scenario: Scenario,
    strategy: str,
    policy: Policy,
    seeds: Sequence[int],
    ablation: str = "none",
) -> dict[str, Any]:
    """单格实验，供进程池调用。"""
    return _run_cell(scenario, strategy, policy, seeds, ablation)


def run_manipulation(
    scenario: Scenario,
    strategy: str,
    seeds: Sequence[int],
    ablation: str = "none",
) -> dict[str, Any]:
    """同一实例上诚实/选择性低报配对，产出 §6.1 的"被操纵实例比例"。"""
    config = apply_ablation(scenario.config(), ablation)
    honest = [run_instance(seed, strategy, Policy.HONEST, config) for seed in seeds]
    selective = [run_instance(seed, strategy, Policy.SELECTIVE, config) for seed in seeds]
    member_ids = tuple(range(1, scenario.params.members + 1))
    lowered = underreporting_members(Policy.SELECTIVE, member_ids)
    target = lowered[0] if lowered else 1
    report = manipulation_report(honest, selective, member_id=target)
    report.update(
        {
            "kind": "manipulation",
            "scenario": scenario.name,
            "strategy": strategy,
            "ablation": ablation,
            "policy": "selective_vs_honest",
            "underreporter": target,
        }
    )
    return report



def _payloads(
    scenarios: Sequence[Scenario],
    strategies: Sequence[str],
    policies: Sequence[Policy],
    seeds: Sequence[int],
    ablations: Sequence[str] = ("none",),
) -> list[tuple[Scenario, str, Policy, tuple[int, ...], str]]:
    return [
        (scenario, strategy, policy, tuple(seeds), ablation)
        for ablation in ablations
        for scenario in scenarios
        for strategy in strategies
        for policy in policies
    ]


def _manipulation_payloads(
    scenarios: Sequence[Scenario],
    strategies: Sequence[str],
    seeds: Sequence[int],
    ablations: Sequence[str],
) -> list[tuple[Scenario, str, tuple[int, ...], str]]:
    return [
        (scenario, strategy, tuple(seeds), ablation)
        for ablation in ablations
        for scenario in scenarios
        for strategy in strategies
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="§15.2 固定真值 50-seed 网格（含 §15.3 消融）")
    parser.add_argument("--seeds", default=f"{FORMAL_SEED_START}-{FORMAL_SEED_START + FORMAL_SEEDS - 1}")
    parser.add_argument("--strategies", default=",".join(STRATEGIES))
    parser.add_argument("--policies", default="honest,selective,blanket")
    parser.add_argument("--scenario", default="", help="只跑名称包含该子串的场景")
    parser.add_argument(
        "--scenarios", default="", help="逗号分隔的场景全名，优先于 --scenario"
    )
    parser.add_argument(
        "--ablations",
        default="",
        help=f"逗号分隔的消融项，或 all；留空只跑主网格。可选：{','.join(ablation_names())}",
    )
    parser.add_argument(
        "--manipulation",
        action="store_true",
        help="额外跑诚实/选择性低报配对，产出被操纵实例比例",
    )
    parser.add_argument("--workers", type=int, default=min(6, os.cpu_count() or 1))
    parser.add_argument(
        "--output",
        default=str(
            Path(__file__).resolve().parents[3]
            / "experiments"
            / f"p5-grid-{FORMAL_SEEDS}seeds.json"
        ),
    )
    args = parser.parse_args()

    seeds = _parse_seeds(args.seeds)
    strategies = [item.strip() for item in args.strategies.split(",") if item.strip()]
    policies = [Policy(item.strip()) for item in args.policies.split(",") if item.strip()]
    for strategy in strategies:
        if strategy not in STRATEGIES:
            parser.error(f"未知基线：{strategy}")

    if args.ablations.strip().lower() == "all":
        ablations: list[str] = ["none", *ablation_names()]
    elif args.ablations.strip():
        ablations = ["none", *[item.strip() for item in args.ablations.split(",") if item.strip()]]
    else:
        ablations = ["none"]
    known = set(ablation_names()) | {"none"}
    for ablation in ablations:
        if ablation not in known:
            parser.error(f"未知消融项：{ablation}，可选 {sorted(known)}")

    scenarios = frozen_grid()
    if args.scenarios.strip():
        wanted = [item.strip() for item in args.scenarios.split(",") if item.strip()]
        by_name = {scenario.name: scenario for scenario in scenarios}
        missing = [name for name in wanted if name not in by_name]
        if missing:
            parser.error(f"未知场景：{missing}，可选 {sorted(by_name)}")
        scenarios = tuple(by_name[name] for name in wanted)
    elif args.scenario:
        scenarios = tuple(item for item in scenarios if args.scenario in item.name)
    if not scenarios:
        parser.error("没有匹配的场景")

    jobs = _payloads(scenarios, strategies, policies, seeds, ablations)
    if args.workers <= 1:
        results = [run_cell(*job) for job in jobs]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(run_cell, *zip(*jobs)))

    manipulations: list[dict[str, Any]] = []
    if args.manipulation:
        m_jobs = _manipulation_payloads(scenarios, strategies, seeds, ablations)
        if args.workers <= 1:
            manipulations = [run_manipulation(*job) for job in m_jobs]
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                manipulations = list(pool.map(run_manipulation, *zip(*m_jobs)))

    payload = {
        "meta": {
            "protocol": "§15.2 固定真值模拟",
            "seeds": seeds,
            "seed_note": "正式报告使用 1001 起的 seed，早期演示用的 1–7 不参与",
            "scenarios": [scenario.name for scenario in scenarios],
            "strategies": strategies,
            "policies": [policy.value for policy in policies],
            "ablations": ablations,
            "manipulation": bool(args.manipulation),
            "solver_time_seconds": SOLVER_TIME,
            "round_limit": ROUND_LIMIT,
            "batch_size": BATCH_SIZE,
            "question_budget_rule": f"{ITEMS_PER_MEMBER_BUDGET} 项/成员",
        },
        "results": results,
        "manipulation": manipulations,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"{'scenario':<24}{'strategy':<20}{'policy':<10}{'ablation':<22}"
        f"{'regret':>9}{'unmet':>8}{'qs':>7}{'worst':>8}"
        f"{'stab_mis':>9}{'viol':>6}{'brier':>8}{'brier0':>8}{'skill':>7}{'secs':>7}"
    )
    for row in results:
        print(
            f"{row['scenario']:<24}{row['strategy']:<20}{row['policy']:<10}{row['ablation']:<22}"
            f"{row['mean_regret']:>9.4f}{row['unmet_rate']:>8.2f}"
            f"{row['mean_questions']:>7.1f}{row['mean_worst_utility']:>8.3f}"
            f"{row['stable_misjudgment_rate']:>9.2f}{row['violation_rate']:>6.2f}"
            f"{(row['brier_score'] if row['brier_score'] is not None else 0.0):>8.3f}"
            f"{(row['brier_constant_baseline'] if row['brier_constant_baseline'] is not None else 0.0):>8.3f}"
            f"{(row['brier_skill_score'] if row['brier_skill_score'] is not None else 0.0):>7.3f}"
            f"{row['mean_solver_seconds']:>7.3f}"
        )

    if manipulations:
        print(
            f"\n{'scenario':<24}{'strategy':<20}{'ablation':<22}"
            f"{'benefit':>8}{'med_gain':>9}{'max_gain':>9}{'d_regret':>9}{'d_avg':>8}"
        )
        for row in manipulations:
            if not row.get("runs"):
                continue
            delta_average = (
                row["mean_average_utility_selective"] - row["mean_average_utility_honest"]
            )
            print(
                f"{row['scenario']:<24}{row['strategy']:<20}{row['ablation']:<22}"
                f"{row['benefit_rate']:>8.2f}{row['median_gain']:>9.3f}"
                f"{row['max_gain']:>9.3f}{row['mean_regret_delta']:>9.4f}"
                f"{delta_average:>8.4f}"
            )
    print(f"\nwritten: {output}")


if __name__ == "__main__":
    main()
