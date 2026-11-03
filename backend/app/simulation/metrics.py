"""固定真值指标：真实福利、简单遗憾、互补加成与违规检查。"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Sequence

from app.constraints.compiler import ConstraintModel
from app.constraints.validation import verify_indices
from app.solver.mean_fair import SolveResult, SolverConfig
from app.simulation.generators import SimInstance


@dataclass(frozen=True, slots=True)
class TrueOptimum:
    candidate_ids: tuple[str, ...]
    value: float
    source: str


def _denominator(exact_count: int, w_top: float, pair_bonus: float) -> float:
    base = exact_count * (1.0 + w_top)
    if pair_bonus > 0:
        base += pair_bonus * exact_count * (exact_count - 1) / 2
    return base


def member_utilities(
    mu: Sequence[Sequence[float]],
    selection: Sequence[int],
    exact_count: int,
    w_top: float,
    *,
    pair_bonus: float = 0.0,
    categories: Sequence[str] | None = None,
) -> tuple[float, ...]:
    """按项可加效用；可选同类别成对加成用于互补/替代场景。"""
    bonus = pair_bonus if categories is not None else 0.0
    denominator = _denominator(exact_count, w_top, bonus)
    counts: Counter[str] = Counter()
    if bonus > 0:
        counts = Counter(categories[index] for index in selection)
        pairs = sum(value * (value - 1) // 2 for value in counts.values())
    else:
        pairs = 0
    return tuple(
        (sum(float(row[index]) for index in selection) + bonus * pairs) / denominator
        for row in mu
    )


def member_welfare(
    mu: Sequence[Sequence[float]],
    selection: Sequence[int],
    exact_count: int,
    w_top: float,
    *,
    pair_bonus: float = 0.0,
    categories: Sequence[str] | None = None,
) -> tuple[float, ...]:
    return member_utilities(
        mu,
        selection,
        exact_count,
        w_top,
        pair_bonus=pair_bonus,
        categories=categories,
    )


def mean_fair_value(values: Sequence[float], lam: float) -> float:
    if not values:
        return 0.0
    return (1.0 - lam) * (sum(values) / len(values)) + lam * min(values)


def true_objective(
    instance: SimInstance,
    model: ConstraintModel,
    selection: Sequence[str],
    lam: float,
) -> float:
    indices = model.selection_indices(selection)
    values = member_utilities(
        instance.mu,
        indices,
        model.exact_count,
        instance.params.w_top,
        pair_bonus=instance.pair_bonus,
        categories=model.categories,
    )
    return mean_fair_value(values, lam)


def solve_true_optimum(
    instance: SimInstance,
    model: ConstraintModel,
    solver_config: SolverConfig,
) -> SolveResult:
    from app.solver.cpsat_adapter import solve_mean_fair

    return solve_mean_fair(instance.mu, model, solver_config)


def enumerate_true_optimum(
    instance: SimInstance,
    model: ConstraintModel,
    lam: float,
    *,
    limit: int = 200_000,
) -> TrueOptimum | None:
    """组合数不大时枚举真实最优；用于可加模型失配场景的参考值。"""
    eligible = tuple(
        index
        for index in range(model.candidate_count)
        if index not in model.excluded_indices
    )
    count = model.exact_count
    if count < 1 or len(eligible) < count:
        return None
    if math.comb(len(eligible), count) > limit:
        return None

    best_indices: tuple[int, ...] = ()
    best_value = -math.inf
    for combination in combinations(eligible, count):
        if verify_indices(model, combination).violations:
            continue
        values = member_utilities(
            instance.mu,
            combination,
            count,
            instance.params.w_top,
            pair_bonus=instance.pair_bonus,
            categories=model.categories,
        )
        value = mean_fair_value(values, lam)
        if value > best_value:
            best_value = value
            best_indices = combination
    if not best_indices:
        return None
    return TrueOptimum(
        candidate_ids=model.selection_ids(best_indices),
        value=best_value,
        source="enumeration",
    )


def violations(model: ConstraintModel, selection: Sequence[str]) -> tuple[str, ...]:
    try:
        indices = model.selection_indices(selection)
    except KeyError as exc:
        return (str(exc),)
    return verify_indices(model, indices).violations
