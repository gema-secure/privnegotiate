"""CP-SAT 量化实现：主目标 → 最小费用 → 候选 ID 字典序，逐阶段只在前一阶段证优后锁定。"""

from __future__ import annotations

import math
from typing import Sequence

from ortools.sat.python import cp_model

from app.constraints.compiler import ConstraintModel
from app.constraints.validation import verify_indices
from app.solver.mean_fair import (
    SolveResult,
    SolveStatus,
    SolverConfig,
    mean_fair_objective,
    validate_mu,
)

INT64_MAX = 2**63 - 1
# 字典序阶段用 2^(n-1-i) 作权重，n 项总和为 2^n-1。取 n≤40 时总和 < 2^40，
# 相对 int64 留 23 位余量，任何后续乘法都不可能溢出；设计上限 n≤30 时行为不变。
_TIE_BREAK_MAX_CANDIDATES = 40


def quantized_utilities(
    mu: Sequence[Sequence[float]], config: SolverConfig
) -> tuple[tuple[int, ...], ...]:
    scale = config.quantization_scale
    denominator = 1.0 + config.w_top
    return tuple(
        tuple(
            min(scale, max(0, math.floor(scale * float(value) / denominator)))
            for value in row
        )
        for row in mu
    )


def _build_base(
    model: ConstraintModel,
) -> tuple[cp_model.CpModel, list[cp_model.IntVar]]:
    solver_model = cp_model.CpModel()
    x = [solver_model.NewBoolVar(f"x_{index}") for index in range(model.candidate_count)]
    for index in model.excluded_indices:
        solver_model.Add(x[index] == 0)
    solver_model.Add(sum(x) == model.exact_count)
    solver_model.Add(
        sum(model.costs_cents[index] * x[index] for index in range(model.candidate_count))
        <= model.budget_cents
    )
    for row in model.rows:
        expression = sum(
            row.coefficients[index] * x[index]
            for index in range(model.candidate_count)
        )
        if row.lower is not None:
            solver_model.Add(expression >= row.lower)
        if row.upper is not None:
            solver_model.Add(expression <= row.upper)
    return solver_model, x


def _main_expr(
    solver_model: cp_model.CpModel,
    x: Sequence[cp_model.IntVar],
    b: Sequence[Sequence[int]],
    config: SolverConfig,
    exact_count: int,
) -> cp_model.LinearExpr:
    member_count = len(b)
    totals = [
        sum(b[member][index] * x[index] for index in range(len(x)))
        for member in range(member_count)
    ]
    p, q = config.lam.numerator, config.lam.denominator
    if config.lam == 0:
        return sum(totals)
    # 恰选 exact_count 项且每项 b<=Q，故 T_m<=Q*exact_count；收紧 z 上界以加强 LP 松弛。
    z = solver_model.NewIntVar(
        0, config.quantization_scale * exact_count, "z_min_member"
    )
    for total in totals:
        solver_model.Add(z <= total)
    return (q - p) * sum(totals) + p * member_count * z


def _selected(solver: cp_model.CpSolver, x: Sequence[cp_model.IntVar]) -> tuple[int, ...]:
    return tuple(index for index, var in enumerate(x) if solver.Value(var) == 1)


def _quantized_objective(
    b: Sequence[Sequence[int]], selection: Sequence[int], config: SolverConfig
) -> int:
    totals = [sum(row[index] for index in selection) for row in b]
    p, q = config.lam.numerator, config.lam.denominator
    if config.lam == 0:
        return sum(totals)
    return (q - p) * sum(totals) + p * len(totals) * min(totals)


def solve_mean_fair(
    mu: Sequence[Sequence[float]],
    model: ConstraintModel,
    config: SolverConfig,
    *,
    tie_break: bool = True,
    base_model: cp_model.CpModel | None = None,
) -> SolveResult:
    """求解量化均值公平模型；返回结果必须在发布前再经 verify_selection。

    tie_break=False 跳过"最小费用→字典序"两个同分阶段，只证主目标最优：
    用于挑战池等不参与稳定判定的子求解，可省约 2/3 子求解耗时。
    """
    try:
        validate_mu(mu, model, config.w_top)
    except ValueError as exc:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=0.0,
            message=str(exc),
        )
    if model.member_count == 0:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=0.0,
            message="没有有效成员，无法计算公平目标",
        )
    if model.candidate_count == 0:
        return SolveResult(
            status=SolveStatus.INFEASIBLE,
            config_version=config.config_version,
            wall_time=0.0,
            message="没有有效候选，可行域为空",
        )

    q = config.lam.denominator
    denominator = q * model.member_count * config.quantization_scale * model.exact_count
    if denominator > INT64_MAX // 8:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=0.0,
            message="整数目标上界可能溢出 int64",
        )

    b = quantized_utilities(mu, config)
    if base_model is None:
        solver_model, x = _build_base(model)
    else:
        solver_model = base_model.Clone()
        x = [solver_model.GetBoolVarFromProtoIndex(i) for i in range(model.candidate_count)]
    solver_model.Maximize(_main_expr(solver_model, x, b, config, model.exact_count))

    remaining = config.time_limit_seconds
    solver = _new_solver(remaining, config)
    status = solver.Solve(solver_model)
    wall_time = float(solver.WallTime())
    remaining = max(0.0, remaining - wall_time)

    if status == cp_model.INFEASIBLE:
        return SolveResult(
            status=SolveStatus.INFEASIBLE,
            config_version=config.config_version,
            wall_time=wall_time,
            quantization_bound=2.0 / config.quantization_scale,
            message="求解器已证明当前约束下无可行组合",
        )
    if status == cp_model.MODEL_INVALID:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=wall_time,
            message="CP-SAT 判定模型无效",
        )
    if not _has_solution(status):
        return SolveResult(
            status=SolveStatus.UNKNOWN,
            config_version=config.config_version,
            wall_time=wall_time,
            quantization_bound=2.0 / config.quantization_scale,
            message="求解未完成：有解与无解均未证明",
        )

    chosen = _selected(solver, x)
    main_value = int(round(solver.ObjectiveValue()))
    quantized_obj = _quantized_objective(b, chosen, config)
    # 目标是整型表达式，求解器报告值必须与复算一致；大数值超 float 精度时跳过互检。
    if main_value != quantized_obj and abs(main_value) < 2**53:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=wall_time,
            quantization_bound=2.0 / config.quantization_scale,
            message="主目标值与复算不一致，阻止发布",
        )
    best_bound = solver.BestObjectiveBound() / denominator
    normalized_obj = quantized_obj / denominator
    absolute_gap = max(0.0, best_bound - normalized_obj)
    main_proven = status == cp_model.OPTIMAL
    tie_complete = False

    if main_proven and remaining > 0 and tie_break:
        chosen, tie_complete, tie_time = _apply_tie_break(
            model, config, b, main_value, chosen, remaining
        )
        wall_time += tie_time

    verification = verify_indices(model, chosen)
    if not verification.ok:
        return SolveResult(
            status=SolveStatus.MODEL_INVALID,
            config_version=config.config_version,
            wall_time=wall_time,
            message="求解结果复核失败：" + "；".join(verification.violations),
        )

    return SolveResult(
        status=SolveStatus.OPTIMAL if main_proven else SolveStatus.FEASIBLE,
        candidate_ids=model.selection_ids(chosen),
        objective_quantized=quantized_obj,
        objective_float=mean_fair_objective(
            mu, chosen, config, exact_count=model.exact_count
        ),
        best_bound=best_bound,
        absolute_gap=absolute_gap,
        quantization_bound=2.0 / config.quantization_scale,
        wall_time=wall_time,
        tie_break_complete=tie_complete,
        config_version=config.config_version,
        message="量化模型已证最优" if main_proven else "当前解可行但未证最优",
    )


def _has_solution(status: int) -> bool:
    return status in (cp_model.OPTIMAL, cp_model.FEASIBLE)


def _new_solver(remaining_seconds: float, config: SolverConfig) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.001, remaining_seconds)
    solver.parameters.num_search_workers = config.num_search_workers
    solver.parameters.random_seed = config.random_seed
    return solver


def _apply_tie_break(
    model: ConstraintModel,
    config: SolverConfig,
    b: Sequence[Sequence[int]],
    main_value: int,
    fallback: tuple[int, ...],
    remaining: float,
) -> tuple[tuple[int, ...], bool, float]:
    cost_model, cost_x = _build_base(model)
    cost_model.Add(
        _main_expr(cost_model, cost_x, b, config, model.exact_count) == main_value
    )
    cost_model.Minimize(
        sum(
            model.costs_cents[index] * cost_x[index]
            for index in range(model.candidate_count)
        )
    )
    cost_solver = _new_solver(remaining, config)
    cost_status = cost_solver.Solve(cost_model)
    elapsed = float(cost_solver.WallTime())
    if cost_status != cp_model.OPTIMAL:
        return fallback, False, elapsed
    cost_value = int(round(cost_solver.ObjectiveValue()))
    stage_two = _selected(cost_solver, cost_x)
    remaining = max(0.0, remaining - elapsed)

    if model.candidate_count > _TIE_BREAK_MAX_CANDIDATES or remaining <= 0:
        return stage_two, False, elapsed

    lex_model, lex_x = _build_base(model)
    lex_model.Add(
        _main_expr(lex_model, lex_x, b, config, model.exact_count) == main_value
    )
    lex_model.Add(
        sum(
            model.costs_cents[index] * lex_x[index]
            for index in range(model.candidate_count)
        )
        == cost_value
    )
    weights = [
        1 << (model.candidate_count - 1 - index)
        for index in range(model.candidate_count)
    ]
    assert sum(weights) <= (1 << _TIE_BREAK_MAX_CANDIDATES) - 1
    lex_model.Maximize(
        sum(weights[index] * lex_x[index] for index in range(model.candidate_count))
    )
    lex_solver = _new_solver(remaining, config)
    lex_status = lex_solver.Solve(lex_model)
    elapsed += float(lex_solver.WallTime())
    if lex_status != cp_model.OPTIMAL:
        return stage_two, False, elapsed
    return _selected(lex_solver, lex_x), True, elapsed
