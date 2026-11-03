"""挑战组合池：当前推荐、个人方向、保守上界方向与多样化挑战者。"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Sequence

from app.constraints.compiler import ConstraintModel, LinearRow
from app.constraints.validation import verify_indices
from app.preference.bounds import UtilityBounds
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import (
    SolveResult,
    SolveStatus,
    SolverConfig,
    mean_fair_objective,
)


@dataclass(frozen=True, slots=True)
class ChallengeConfig:
    max_pool: int = 16
    include_personal: bool = True
    include_upper: bool = True
    include_diverse: bool = True
    diversity_replacements: tuple[int, ...] = (2, 3, 1)
    time_limit_seconds: float = 0.5
    quantization_scale: int = 1000
    num_search_workers: int = 1
    random_seed: int = 0

    def __post_init__(self) -> None:
        if self.max_pool < 2:
            raise ValueError("挑战池上限至少为 2")
        if not math.isfinite(self.time_limit_seconds) or self.time_limit_seconds <= 0:
            raise ValueError("子求解时间上限必须为正")
        if self.quantization_scale < 1:
            raise ValueError("Q 必须为正整数")


@dataclass(frozen=True, slots=True)
class Challenger:
    role: str
    candidate_ids: tuple[str, ...]
    objective: float | None
    excess: float | None
    solver_status: str
    member_id: int | None = None
    tie_break_complete: bool = False
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "member_id": self.member_id,
            "candidate_ids": list(self.candidate_ids),
            "objective": self.objective,
            "excess": self.excess,
            "solver_status": self.solver_status,
            "tie_break_complete": self.tie_break_complete,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ChallengePool:
    incumbent_ids: tuple[str, ...]
    previous_ids: tuple[str, ...]
    member_ids: tuple[int, ...]
    challengers: tuple[Challenger, ...]
    requested: int
    complete: bool
    wall_time: float
    lam: float = 0.25
    upper_bound: float | None = None
    upper_status: str = ""
    upper_quantization_bound: float | None = None
    personal_skipped: int = 0
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "incumbent_ids": list(self.incumbent_ids),
            "previous_ids": list(self.previous_ids),
            "member_ids": list(self.member_ids),
            "challengers": [item.as_dict() for item in self.challengers],
            "requested": self.requested,
            "complete": self.complete,
            "wall_time": self.wall_time,
            "lam": self.lam,
            "upper_bound": self.upper_bound,
            "upper_status": self.upper_status,
            "upper_quantization_bound": self.upper_quantization_bound,
            "personal_skipped": self.personal_skipped,
            "message": self.message,
        }


def _aligned_upper(
    bounds: UtilityBounds, model: ConstraintModel
) -> tuple[tuple[float, ...], ...]:
    if bounds.member_ids != model.member_ids:
        raise ValueError("上界区间成员顺序与约束模型不一致")
    if bounds.candidate_ids != model.candidate_ids:
        raise ValueError("上界区间候选顺序与约束模型不一致")
    return bounds.upper


def _solver_config(base: SolverConfig, config: ChallengeConfig, lam: Fraction) -> SolverConfig:
    return replace(
        base,
        lam=lam,
        quantization_scale=config.quantization_scale,
        time_limit_seconds=config.time_limit_seconds,
        num_search_workers=config.num_search_workers,
        random_seed=config.random_seed,
    )


def _diversity_row(
    model: ConstraintModel, incumbent: Sequence[int], upper: int
) -> LinearRow:
    chosen = set(incumbent)
    coefficients = tuple(1 if index in chosen else 0 for index in range(model.candidate_count))
    return LinearRow(
        label="challenge:diversity",
        coefficients=coefficients,
        lower=None,
        upper=upper,
    )


def _objective(
    mu: Sequence[Sequence[float]],
    indices: Sequence[int],
    config: SolverConfig,
    exact_count: int,
) -> float:
    return mean_fair_objective(mu, indices, config, exact_count=exact_count)


def build_challengers(
    mu: Sequence[Sequence[float]],
    bounds: UtilityBounds,
    model: ConstraintModel,
    incumbent: Sequence[str],
    solver_config: SolverConfig,
    *,
    challenge_config: ChallengeConfig | None = None,
    previous_incumbent: Sequence[str] | None = None,
) -> ChallengePool:
    """在同一完整可行域内构造挑战池；池不完整时如实标记。"""
    config = challenge_config or ChallengeConfig()
    upper_rows = _aligned_upper(bounds, model)

    incumbent_indices = model.selection_indices(incumbent)
    if not verify_indices(model, incumbent_indices).ok:
        raise ValueError("当前推荐不满足硬约束，无法构造挑战池")

    challengers: list[Challenger] = []
    seen: set[frozenset[str]] = set()
    solve_times: list[float] = []
    incomplete = False
    skipped_pool_full = False
    personal_skipped = 0
    upper_bound: float | None = None
    upper_status = ""
    upper_quantization: float | None = None
    incumbent_value = _objective(
        mu, incumbent_indices, solver_config, model.exact_count
    )

    def add(role: str, result: SolveResult, member_id: int | None = None) -> None:
        nonlocal incomplete
        if result.wall_time is not None:
            solve_times.append(result.wall_time)
        if result.status not in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE):
            incomplete = True
            return
        indices = model.selection_indices(result.candidate_ids)
        key = frozenset(result.candidate_ids)
        if key in seen:
            return
        if len(challengers) >= config.max_pool:
            return
        seen.add(key)
        value = _objective(mu, indices, solver_config, model.exact_count)
        challengers.append(
            Challenger(
                role=role,
                candidate_ids=tuple(result.candidate_ids),
                objective=value,
                excess=max(0.0, incumbent_value - value),
                solver_status=result.status.value,
                member_id=member_id,
                tie_break_complete=result.tie_break_complete,
                detail=result.message,
            )
        )
        if result.status is not SolveStatus.OPTIMAL:
            incomplete = True

    current = SolveResult(
        status=SolveStatus.OPTIMAL,
        candidate_ids=tuple(sorted(incumbent)),
        objective_float=_objective(mu, incumbent_indices, solver_config, model.exact_count),
        tie_break_complete=True,
        message="当前推荐",
    )
    add("incumbent", current)

    if previous_incumbent:
        try:
            previous_indices = model.selection_indices(previous_incumbent)
        except KeyError:
            previous_indices = ()
        if previous_indices and verify_indices(model, previous_indices).ok:
            previous = SolveResult(
                status=SolveStatus.OPTIMAL,
                candidate_ids=tuple(sorted(previous_incumbent)),
                objective_float=_objective(
                    mu, previous_indices, solver_config, model.exact_count
                ),
                tie_break_complete=True,
                message="上一轮推荐",
            )
            add("previous", previous)

    requested = 2 + (model.member_count if config.include_personal else 0)

    if config.include_personal:
        for member_index, member_id in enumerate(model.member_ids):
            if len(challengers) >= config.max_pool:
                skipped_pool_full = True
                break
            own = [float(value) for value in mu[member_index]]
            if not any(value > 0.0 for value in own):
                # 该成员在可行域内没有任何正效用信号：λ=0 的目标恒为 0，求解器返回的
                # 是任意可行解，当作"个人方向"塞进池会污染 d_i。跳过并如实计数。
                personal_skipped += 1
                continue
            member_mu = [
                own if index == member_index else [0.0] * model.candidate_count
                for index in range(len(mu))
            ]
            result = solve_mean_fair(
                member_mu,
                model,
                _solver_config(solver_config, config, Fraction(0)),
                tie_break=False,
            )
            add("personal", result, member_id=member_id)

    if config.include_upper:
        requested += 1
        if len(challengers) >= config.max_pool:
            skipped_pool_full = True
        else:
            upper_result = solve_mean_fair(
                upper_rows,
                model,
                _solver_config(solver_config, config, solver_config.lam),
                tie_break=False,
            )
            upper_status = upper_result.status.value
            if upper_result.wall_time is not None:
                solve_times.append(upper_result.wall_time)
            if (
                upper_result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)
                and upper_result.best_bound is not None
            ):
                quantization = (
                    upper_result.quantization_bound
                    if upper_result.quantization_bound is not None
                    else 1.0 / config.quantization_scale
                )
                upper_quantization = quantization
                upper_bound = min(
                    1.0,
                    max(0.0, float(upper_result.best_bound) + quantization + 1e-9),
                )
            add("upper", upper_result)

    if config.include_diverse:
        for replacement in config.diversity_replacements:
            if len(challengers) >= config.max_pool:
                skipped_pool_full = True
                break
            upper = model.exact_count - replacement
            if upper < 1:
                continue
            requested += 1
            target = replace(
                model, rows=model.rows + (_diversity_row(model, incumbent_indices, upper),)
            )
            result = solve_mean_fair(
                mu,
                target,
                _solver_config(solver_config, config, solver_config.lam),
                tie_break=False,
            )
            add("diverse", result)

    message = ""
    if not challengers:
        message = "挑战池为空"
    elif incomplete:
        message = "部分子求解未证最优，池可能不完整"
    elif skipped_pool_full:
        message = "池已满，跳过低优先级子求解"
    if personal_skipped:
        suffix = f"{personal_skipped} 个成员无正效用信号，跳过个人方向"
        message = f"{message}；{suffix}" if message else suffix
    return ChallengePool(
        incumbent_ids=tuple(sorted(incumbent)),
        previous_ids=tuple(sorted(previous_incumbent or ())),
        member_ids=tuple(model.member_ids),
        challengers=tuple(challengers),
        requested=requested,
        complete=not incomplete,
        wall_time=sum(solve_times),
        lam=float(solver_config.lam),
        upper_bound=upper_bound,
        upper_status=upper_status,
        upper_quantization_bound=upper_quantization,
        personal_skipped=personal_skipped,
        message=message,
    )
