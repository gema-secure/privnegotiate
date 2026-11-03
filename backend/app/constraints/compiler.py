"""把已确认约束编译成求解器无关的 ConstraintModel。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.constraints.dsl import (
    AttributeValue,
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    DslError,
    Member,
    validate_constraint,
)


class CompileError(ValueError):
    """约束、候选或成员数据无法编译。"""


@dataclass(frozen=True, slots=True)
class LinearRow:
    """一条 sum(coeff_i * x_i) 的上下限约束。"""

    label: str
    coefficients: tuple[int, ...]
    lower: int | None
    upper: int | None

    def count(self, indices: Iterable[int]) -> int:
        return sum(self.coefficients[index] for index in indices)

    def satisfied(self, indices: Iterable[int]) -> bool:
        value = self.count(indices)
        if self.lower is not None and value < self.lower:
            return False
        if self.upper is not None and value > self.upper:
            return False
        return True


@dataclass(frozen=True, slots=True)
class ConstraintModel:
    candidate_ids: tuple[str, ...]
    member_ids: tuple[int, ...]
    costs_cents: tuple[int, ...]
    categories: tuple[str, ...]
    exact_count: int
    budget_cents: int
    excluded_indices: frozenset[int]
    e: tuple[tuple[int, ...], ...]
    rows: tuple[LinearRow, ...]
    constraint_ids: tuple[str, ...] = ()

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_ids)

    @property
    def member_count(self) -> int:
        return len(self.member_ids)

    def candidate_index(self, candidate_id: str) -> int:
        try:
            return self.candidate_ids.index(candidate_id)
        except ValueError as exc:
            raise KeyError(f"未知候选：{candidate_id}") from exc

    def selection_indices(self, candidate_ids: Iterable[str]) -> tuple[int, ...]:
        return tuple(sorted(self.candidate_index(item) for item in candidate_ids))

    def selection_ids(self, indices: Iterable[int]) -> tuple[str, ...]:
        return tuple(sorted(self.candidate_ids[index] for index in indices))


def _attribute_matches(
    expected: Mapping[str, AttributeValue], actual: Mapping[str, AttributeValue]
) -> bool:
    for key, value in expected.items():
        if key not in actual:
            return False
        observed = actual[key]
        if type(observed) is not type(value) or observed != value:
            return False
    return True


def _row_coefficients(
    model_candidates: Sequence[Candidate],
    *,
    eligible: Sequence[int],
    category: str | None,
    attributes: Mapping[str, AttributeValue],
) -> tuple[int, ...]:
    coefficients = []
    for index, candidate in enumerate(model_candidates):
        matches = eligible[index] == 1
        if matches and category is not None:
            matches = candidate.category == category
        if matches and attributes:
            matches = _attribute_matches(attributes, candidate.attributes)
        coefficients.append(1 if matches else 0)
    return tuple(coefficients)


def compile_constraints(
    confirmed_constraints: Iterable[ConfirmedConstraint],
    candidates: Iterable[Candidate],
    members: Iterable[Member],
) -> ConstraintModel:
    """编译完整硬约束；输入只读，输出不含 ORM 或求解器对象。"""
    constraints = tuple(sorted(confirmed_constraints, key=lambda item: item.constraint_id))
    for constraint in constraints:
        try:
            validate_constraint(constraint)
        except DslError as exc:
            raise CompileError(f"约束 {constraint.constraint_id} 非法：{exc}") from exc
    ids = [constraint.constraint_id for constraint in constraints]
    if len(ids) != len(set(ids)):
        raise CompileError("constraint_id 必须唯一")

    active_candidates = sorted(
        (item for item in candidates if item.active), key=lambda item: item.candidate_id
    )
    candidate_ids = tuple(item.candidate_id for item in active_candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise CompileError("candidate_id 必须唯一")
    position = {candidate_id: index for index, candidate_id in enumerate(candidate_ids)}

    active_members = sorted(
        (item for item in members if item.active), key=lambda item: item.member_id
    )
    member_ids = tuple(item.member_id for item in active_members)
    if len(member_ids) != len(set(member_ids)):
        raise CompileError("member_id 必须唯一")
    member_slot = {member_id: index for index, member_id in enumerate(member_ids)}

    budgets = [item for item in constraints if item.kind is ConstraintKind.BUDGET]
    exact_counts = [
        item for item in constraints if item.kind is ConstraintKind.EXACT_COUNT
    ]
    if len(budgets) != 1:
        raise CompileError("必须恰好一条已确认预算约束")
    if len(exact_counts) != 1:
        raise CompileError("必须恰好一条已确认恰选数量约束")
    budget_cents = budgets[0].amount_cents
    exact_count = exact_counts[0].min_count
    assert budget_cents is not None and exact_count is not None

    excluded: set[int] = set()
    for constraint in constraints:
        if constraint.kind is ConstraintKind.EXCLUDE_ITEM:
            for candidate_id in constraint.candidate_ids:
                if candidate_id not in position:
                    raise CompileError(
                        f"整组禁选引用未知或已停用候选：{candidate_id}"
                    )
                excluded.add(position[candidate_id])

    e = [[1] * len(candidate_ids) for _ in member_ids]
    for constraint in constraints:
        if constraint.kind is ConstraintKind.OPT_OUT:
            if constraint.owner_member_id not in member_slot:
                raise CompileError(
                    f"个人退出引用未知或已停用成员：{constraint.owner_member_id}"
                )
            slot = member_slot[constraint.owner_member_id]
            for candidate_id in constraint.candidate_ids:
                if candidate_id not in position:
                    raise CompileError(
                        f"个人退出引用未知或已停用候选：{candidate_id}"
                    )
                e[slot][position[candidate_id]] = 0

    rows: list[LinearRow] = []
    for constraint in constraints:
        if constraint.kind is ConstraintKind.REQUIRE_COUNT:
            if constraint.owner_member_id not in member_slot:
                raise CompileError(
                    f"替代要求引用未知或已停用成员：{constraint.owner_member_id}"
                )
            slot = member_slot[constraint.owner_member_id]
            coefficients = _row_coefficients(
                active_candidates,
                eligible=tuple(e[slot]),
                category=constraint.category,
                attributes=constraint.attributes,
            )
            rows.append(
                LinearRow(
                    label=f"require:{constraint.constraint_id}",
                    coefficients=coefficients,
                    lower=constraint.min_count,
                    upper=None,
                )
            )
        elif constraint.kind is ConstraintKind.CATEGORY_COUNT:
            coefficients = tuple(
                1 if candidate.category == constraint.category else 0
                for candidate in active_candidates
            )
            rows.append(
                LinearRow(
                    label=f"category:{constraint.category}:{constraint.constraint_id}",
                    coefficients=coefficients,
                    lower=constraint.min_count,
                    upper=constraint.max_count,
                )
            )

    return ConstraintModel(
        candidate_ids=candidate_ids,
        member_ids=member_ids,
        costs_cents=tuple(item.cost_cents for item in active_candidates),
        categories=tuple(item.category for item in active_candidates),
        exact_count=exact_count,
        budget_cents=budget_cents,
        excluded_indices=frozenset(excluded),
        e=tuple(tuple(row) for row in e),
        rows=tuple(rows),
        constraint_ids=tuple(ids),
    )
