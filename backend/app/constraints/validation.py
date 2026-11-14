"""独立复核：预检查只报确定性无解，复核不依赖求解器内部状态。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.constraints.compiler import ConstraintModel


@dataclass(frozen=True, slots=True)
class PrecheckIssue:
    code: str
    detail: str
    definite: bool = True
    """True 表示该条已足以证明无解；False 仅为告警，不得触发 INFEASIBLE。"""


@dataclass(frozen=True, slots=True)
class PrecheckReport:
    """definitely_infeasible 为真才代表已确定无解；为假不代表有解。"""

    issues: tuple[PrecheckIssue, ...]
    definitely_infeasible: bool


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    violations: tuple[str, ...]
    total_cost_cents: int


def _eligible_indices(model: ConstraintModel) -> tuple[int, ...]:
    return tuple(
        index
        for index in range(model.candidate_count)
        if index not in model.excluded_indices
    )


def precheck(model: ConstraintModel) -> PrecheckReport:
    issues: list[PrecheckIssue] = []
    eligible = _eligible_indices(model)

    if not eligible:
        issues.append(PrecheckIssue("NO_ELIGIBLE_CANDIDATE", "整组禁选后没有候选"))
    if model.exact_count < 1:
        issues.append(PrecheckIssue("EXACT_COUNT_INVALID", "恰选数量必须为正"))
    elif len(eligible) < model.exact_count:
        issues.append(
            PrecheckIssue(
                "NOT_ENOUGH_ELIGIBLE",
                f"有效候选 {len(eligible)} 个，少于恰选数量 {model.exact_count}",
            )
        )

    cheapest = sorted(model.costs_cents[index] for index in eligible)
    if len(cheapest) >= model.exact_count:
        cheapest_sum = sum(cheapest[: model.exact_count])
        if cheapest_sum > model.budget_cents:
            issues.append(
                PrecheckIssue(
                    "BUDGET_TOO_LOW",
                    f"最便宜的 {model.exact_count} 项合计 {cheapest_sum} 分，"
                    f"超过预算 {model.budget_cents} 分",
                )
            )

    for row in model.rows:
        available = sum(row.coefficients[index] for index in eligible)
        if row.lower is not None and row.lower > available:
            issues.append(
                PrecheckIssue(
                    "ROW_LOWER_IMPOSSIBLE",
                    f"{row.label} 要求至少 {row.lower}，但最多只有 {available}",
                )
            )
            continue
        uncertain = set(row.uncertain_indices)
        confirmed = sum(
            row.coefficients[index] for index in eligible if index not in uncertain
        )
        if row.lower is not None and row.lower > confirmed:
            issues.append(
                PrecheckIssue(
                    "ATTRIBUTE_UNKNOWN_PENDING",
                    f"{row.label} 要求至少 {row.lower}，已确认 {confirmed}，"
                    f"其余 {available - confirmed} 项属性未知，进入待确认",
                    definite=False,
                )
            )

    return PrecheckReport(
        issues=tuple(issues),
        definitely_infeasible=any(issue.definite for issue in issues),
    )


def verify_indices(
    model: ConstraintModel, indices: Iterable[int]
) -> VerificationResult:
    chosen = tuple(indices)
    violations: list[str] = []

    if len(set(chosen)) != len(chosen):
        violations.append("候选重复")
    if any(index < 0 or index >= model.candidate_count for index in chosen):
        violations.append("候选下标越界")
    if len(chosen) != model.exact_count:
        violations.append(
            f"恰选数量不符：实际 {len(chosen)}，要求 {model.exact_count}"
        )
    if any(index in model.excluded_indices for index in chosen):
        violations.append("包含整组禁选候选")

    valid = tuple(
        index
        for index in chosen
        if 0 <= index < model.candidate_count and index not in model.excluded_indices
    )
    total_cost = sum(model.costs_cents[index] for index in valid)
    if total_cost > model.budget_cents:
        violations.append(f"费用 {total_cost} 分超过预算 {model.budget_cents} 分")

    for row in model.rows:
        if not row.satisfied(valid):
            value = row.count(valid)
            violations.append(f"{row.label} 不满足：实际 {value}")

    return VerificationResult(
        ok=not violations, violations=tuple(violations), total_cost_cents=total_cost
    )


def verify_selection(
    model: ConstraintModel, candidate_ids: Iterable[str]
) -> VerificationResult:
    chosen_ids = tuple(candidate_ids)
    unknown = [item for item in chosen_ids if item not in model.candidate_ids]
    if unknown:
        return VerificationResult(
            ok=False,
            violations=tuple(f"未知候选：{item}" for item in sorted(set(unknown))),
            total_cost_cents=0,
        )
    indices = tuple(model.candidate_ids.index(item) for item in chosen_ids)
    return verify_indices(model, indices)
