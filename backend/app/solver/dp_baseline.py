"""DP 可加基线：仅用于 λ=0 且全部约束都能被正确编码的实例。"""

from __future__ import annotations

from typing import Sequence

from app.constraints.compiler import ConstraintModel
from app.constraints.validation import verify_indices
from app.solver.mean_fair import (
    SolveResult,
    SolveStatus,
    SolverConfig,
    mean_fair_objective,
    validate_mu,
)

_EPS = 1e-12


class DpUnsupportedError(ValueError):
    """实例超出 DP 的可正确编码范围。"""


def solve_mean_fair_dp(
    mu: Sequence[Sequence[float]],
    model: ConstraintModel,
    config: SolverConfig,
) -> SolveResult:
    if config.lam != 0:
        raise DpUnsupportedError("DP 基线只支持 λ=0 的可加目标")
    if model.rows:
        raise DpUnsupportedError("存在替代要求或类别约束，DP 不能正确编码")
    validate_mu(mu, model, config.w_top)
    if model.member_count == 0:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=None,
            message="没有有效成员",
        )

    items = [
        index
        for index in range(model.candidate_count)
        if index not in model.excluded_indices
    ]
    exact_count = model.exact_count
    if len(items) < exact_count:
        return SolveResult(
            status=SolveStatus.INFEASIBLE,
            config_version=config.config_version,
            wall_time=None,
            message="有效候选不足恰选数量，可行域为空",
        )

    cheapest = sorted(model.costs_cents[index] for index in items)
    if sum(cheapest[:exact_count]) > model.budget_cents:
        return SolveResult(
            status=SolveStatus.INFEASIBLE,
            config_version=config.config_version,
            wall_time=None,
            message="最便宜组合已超预算，可行域为空",
        )
    cap = min(model.budget_cents, sum(sorted(cheapest, reverse=True)[:exact_count]))
    member_count = model.member_count

    layers: list[dict[int, tuple[float, tuple[int, ...]]]] = [
        {} for _ in range(exact_count + 1)
    ]
    layers[0][0] = (0.0, ())
    processed = 0
    for item in items:
        upper = min(exact_count - 1, processed)
        for count in range(upper, -1, -1):
            for cost, (value, indices) in list(layers[count].items()):
                new_cost = cost + model.costs_cents[item]
                if new_cost > cap:
                    continue
                new_value = value + sum(
                    float(mu[member][item]) for member in range(member_count)
                )
                new_indices = indices + (item,)
                current = layers[count + 1].get(new_cost)
                if current is None or _better(new_value, new_indices, current):
                    layers[count + 1][new_cost] = (new_value, new_indices)
        processed += 1

    best: tuple[float, tuple[int, ...]] | None = None
    best_cost: int | None = None
    for cost, state in layers[exact_count].items():
        if best is None:
            best, best_cost = state, cost
            continue
        if state[0] > best[0] + _EPS:
            best, best_cost = state, cost
        elif state[0] >= best[0] - _EPS and (
            cost < best_cost or (cost == best_cost and state[1] < best[1])
        ):
            best, best_cost = state, cost

    if best is None:
        return SolveResult(
            status=SolveStatus.INFEASIBLE,
            config_version=config.config_version,
            wall_time=None,
            message="DP 穷尽可行域后无可行组合",
        )

    chosen = best[1]
    verification = verify_indices(model, chosen)
    if not verification.ok:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=None,
            message="DP 结果复核失败：" + "；".join(verification.violations),
        )
    objective = mean_fair_objective(mu, chosen, config, exact_count=exact_count)
    return SolveResult(
        status=SolveStatus.OPTIMAL,
        candidate_ids=model.selection_ids(chosen),
        objective_quantized=None,
        objective_float=objective,
        best_bound=objective,
        absolute_gap=0.0,
        quantization_bound=0.0,
        wall_time=None,
        tie_break_complete=True,
        config_version=config.config_version,
        message="DP 在可编码模型上穷尽最优",
    )


def _better(
    value: float,
    indices: tuple[int, ...],
    current: tuple[float, tuple[int, ...]],
) -> bool:
    if value > current[0] + _EPS:
        return True
    if value >= current[0] - _EPS:
        return indices < current[1]
    return False
