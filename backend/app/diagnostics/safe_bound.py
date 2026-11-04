"""全空间保守遗憾上界：风险仪表，不是证书，不参与停止判定。"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Sequence

from app.constraints.compiler import ConstraintModel
from app.constraints.validation import verify_indices
from app.preference.bounds import UtilityBounds
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.budget import SolveBudget
from app.solver.mean_fair import (
    SolveResult,
    SolveStatus,
    SolverConfig,
    member_utilities,
)


@dataclass(frozen=True, slots=True)
class SafeBoundConfig:
    w_top: float = 1.0
    quantization_scale: int = 1000
    time_limit_seconds: float = 0.5
    num_search_workers: int = 1
    random_seed: int = 0
    config_version: int = 1

    def __post_init__(self) -> None:
        if not math.isfinite(self.w_top) or self.w_top < 0:
            raise ValueError("w_top 必须是非负有限数")
        if self.quantization_scale < 1:
            raise ValueError("Q 必须为正整数")
        if not math.isfinite(self.time_limit_seconds) or self.time_limit_seconds <= 0:
            raise ValueError("求解时间上限必须为正")


@dataclass(frozen=True, slots=True)
class BoundReport:
    epsilon_safe: float
    upper_bound_h: float
    lower_bound_incumbent: float
    bound_source: str
    solver_status: str
    quantization_bound: float | None
    absolute_gap: float | None
    coverage_ratio: float
    unknown_pairs: int
    member_lower: tuple[float, ...]
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "epsilon_safe": round(self.epsilon_safe, 6),
            "upper_bound_h": round(self.upper_bound_h, 6),
            "lower_bound_incumbent": round(self.lower_bound_incumbent, 6),
            "bound_source": self.bound_source,
            "solver_status": self.solver_status,
            "quantization_bound": self.quantization_bound,
            "absolute_gap": self.absolute_gap,
            "coverage_ratio": round(self.coverage_ratio, 6),
            "unknown_pairs": self.unknown_pairs,
            "member_lower": [round(value, 6) for value in self.member_lower],
            "message": self.message,
        }


def _check_alignment(bounds: UtilityBounds, model: ConstraintModel) -> None:
    if bounds.member_ids != model.member_ids:
        raise ValueError("保守区间成员顺序与约束模型不一致")
    if bounds.candidate_ids != model.candidate_ids:
        raise ValueError("保守区间候选顺序与约束模型不一致")


def _normalized_member_utilities(
    rows: Sequence[Sequence[float]],
    selection: Sequence[int],
    model: ConstraintModel,
    w_top: float,
) -> tuple[float, ...]:
    return member_utilities(
        rows, selection, exact_count=model.exact_count, w_top=w_top
    )


def _mean_fair(values: Sequence[float], lam: float) -> float:
    if not values:
        return 0.0
    return (1.0 - lam) * (sum(values) / len(values)) + lam * min(values)


def compute_safe_bound(
    bounds: UtilityBounds,
    incumbent: Sequence[str],
    model: ConstraintModel,
    solver_config: SolverConfig,
    config: SafeBoundConfig | None = None,
    *,
    upper_bound: float | None = None,
    upper_status: str | None = None,
    upper_quantization_bound: float | None = None,
    budget: SolveBudget | None = None,
) -> BoundReport:
    """epsilon_safe=min(1,max(0,UB_h-H_l))，UB_h 必须覆盖整个可行域。"""
    settings = config or SafeBoundConfig()
    _check_alignment(bounds, model)
    incumbent_indices = model.selection_indices(incumbent)
    verification = verify_indices(model, incumbent_indices)
    if not verification.ok:
        return _universal_report(bounds, "当前推荐不满足硬约束，使用通用上界")

    lam = float(solver_config.lam)
    lower_values = _normalized_member_utilities(
        bounds.lower, incumbent_indices, model, settings.w_top
    )
    lower_h = _mean_fair(lower_values, lam)

    source = "solver"
    status = upper_status or ""
    quantization = upper_quantization_bound
    gap: float | None = None
    top: float | None = upper_bound
    message = ""

    if top is None:
        if budget is not None and budget.remaining() <= 0:
            return _universal_report(bounds, "整轮计算预算已用尽，使用通用上界")
        result = solve_mean_fair(
            bounds.upper,
            model,
            replace(
                solver_config,
                quantization_scale=settings.quantization_scale,
                time_limit_seconds=min(settings.time_limit_seconds, budget.remaining()) if budget else settings.time_limit_seconds,
                num_search_workers=settings.num_search_workers,
                random_seed=settings.random_seed,
            ),
        )
        if budget is not None:
            budget.consume(result.wall_time)
        status = result.status.value
        gap = result.absolute_gap
        if result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE) and result.best_bound is not None:
            quantization = (
                result.quantization_bound
                if result.quantization_bound is not None
                else 1.0 / settings.quantization_scale
            )
            top = float(result.best_bound) + float(quantization) + 1e-9
        elif result.status is SolveStatus.INFEASIBLE:
            return _universal_report(bounds, "上界模型无解，无法给出全空间界")
        else:
            source = "universal"
            top = 1.0
            message = "求解器未给出可用界，退回通用上界 1"

    if top is None:
        source = "universal"
        top = 1.0
        message = "缺少求解器界，退回通用上界 1"

    upper_h = min(1.0, max(0.0, float(top)))
    epsilon = min(1.0, max(0.0, upper_h + 1e-9 - lower_h))
    total_pairs = model.member_count * model.candidate_count
    unknown = sum(
        1
        for row_lower, row_upper in zip(bounds.lower, bounds.upper)
        for low, high in zip(row_lower, row_upper)
        if low <= 0.0 and high > 0.0
    )
    coverage = 1.0 if total_pairs <= 0 else 1.0 - unknown / total_pairs
    if not message:
        message = "上界随未评价项覆盖变化；不称证书"
    return BoundReport(
        epsilon_safe=epsilon,
        upper_bound_h=upper_h,
        lower_bound_incumbent=lower_h,
        bound_source=source,
        solver_status=status,
        quantization_bound=quantization,
        absolute_gap=gap,
        coverage_ratio=coverage,
        unknown_pairs=unknown,
        member_lower=tuple(lower_values),
        message=message,
    )


def _universal_report(bounds: UtilityBounds, message: str) -> BoundReport:
    total_pairs = len(bounds.member_ids) * len(bounds.candidate_ids)
    unknown = sum(
        1
        for row_lower, row_upper in zip(bounds.lower, bounds.upper)
        for low, high in zip(row_lower, row_upper)
        if low <= 0.0 and high > 0.0
    )
    coverage = 1.0 if total_pairs <= 0 else 1.0 - unknown / total_pairs
    return BoundReport(
        epsilon_safe=1.0,
        upper_bound_h=1.0,
        lower_bound_incumbent=0.0,
        bound_source="universal",
        solver_status="UNKNOWN",
        quantization_bound=None,
        absolute_gap=None,
        coverage_ratio=coverage,
        unknown_pairs=unknown,
        member_lower=tuple(0.0 for _ in bounds.member_ids),
        message=message,
    )
