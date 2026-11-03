"""mean_fair_v1：归一化均值公平目标与不可变求解结果。"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction
from numbers import Real
from typing import Sequence

from app.constraints.compiler import ConstraintModel


class SolveStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"
    MODEL_INVALID = "MODEL_INVALID"


@dataclass(frozen=True, slots=True)
class SolverConfig:
    lam: Fraction = field(default_factory=lambda: Fraction(1, 4))
    w_top: float = 1.0
    quantization_scale: int = 1000
    time_limit_seconds: float = 1.0
    num_search_workers: int = 1
    random_seed: int = 0
    config_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.lam, Fraction):
            object.__setattr__(self, "lam", Fraction(str(self.lam)))
        if not 0 <= self.lam <= 1:
            raise ValueError("λ 必须在 [0,1] 内")
        if not math.isfinite(self.w_top) or self.w_top < 0:
            raise ValueError("w_top 必须是非负有限数")
        if self.quantization_scale < 1:
            raise ValueError("Q 必须为正整数")
        if self.time_limit_seconds <= 0:
            raise ValueError("求解时间上限必须为正")


@dataclass(frozen=True, slots=True)
class SolveResult:
    """量化求解结果。

    best_bound / absolute_gap 对应量化模型；objective_float 用未量化 mu 对返回组合复算。
    UNKNOWN 只表示有解与无解均未证明，调用方不得据此宣称无可行组合。
    """

    status: SolveStatus
    candidate_ids: tuple[str, ...] = ()
    objective_quantized: int | None = None
    objective_float: float | None = None
    best_bound: float | None = None
    absolute_gap: float | None = None
    quantization_bound: float | None = None
    wall_time: float | None = None
    tie_break_complete: bool = False
    config_version: int = 1
    message: str = ""


def validate_mu(
    mu: Sequence[Sequence[float]], model: ConstraintModel, w_top: float = 1.0
) -> None:
    upper = 1.0 + w_top
    if len(mu) != model.member_count:
        raise ValueError(
            f"mu 行数 {len(mu)} 与有效成员数 {model.member_count} 不一致"
        )
    for row_index, row in enumerate(mu):
        if len(row) != model.candidate_count:
            raise ValueError(
                f"mu 第 {row_index} 行长度 {len(row)} 与候选数 {model.candidate_count} 不一致"
            )
        for value in row:
            if not isinstance(value, Real) or not math.isfinite(float(value)):
                raise ValueError("mu 必须是有限实数")
            if value < 0 or value > upper + 1e-9:
                raise ValueError(f"mu 必须在 [0,{upper}] 内（未归一化单项效用）")


def member_utilities(
    mu: Sequence[Sequence[float]],
    selection: Sequence[int],
    *,
    exact_count: int,
    w_top: float,
) -> tuple[float, ...]:
    denominator = exact_count * (1.0 + w_top)
    return tuple(
        sum(float(row[index]) for index in selection) / denominator for row in mu
    )


def mean_fair_objective(
    mu: Sequence[Sequence[float]],
    selection: Sequence[int],
    config: SolverConfig,
    *,
    exact_count: int,
) -> float:
    utilities = member_utilities(
        mu, selection, exact_count=exact_count, w_top=config.w_top
    )
    if not utilities:
        raise ValueError("没有成员时无法计算公平目标")
    average = sum(utilities) / len(utilities)
    lam = float(config.lam)
    return (1.0 - lam) * average + lam * min(utilities)


def tie_break_key(
    model: ConstraintModel, indices: Sequence[int]
) -> tuple[int, tuple[str, ...]]:
    """确定性同分排序：最小费用优先，再按候选 ID 字典序。"""
    return (
        sum(model.costs_cents[index] for index in indices),
        model.selection_ids(indices),
    )
