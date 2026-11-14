"""把已确认约束编译成求解器无关的 ConstraintModel。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from app.constraints.dsl import (
    AttributeState,
    AttributeValue,
    Candidate,
    ConfirmedConstraint,
    ConstraintKind,
    DslError,
    Member,
    validate_constraint,
)

MatchOutcome = str  # "match" | "miss" | "uncertain"


class CompileError(ValueError):
    """约束、候选或成员数据无法编译。"""


@dataclass(frozen=True, slots=True)
class LinearRow:
    """一条 sum(coeff_i * x_i) 的上下限约束。"""

    label: str
    coefficients: tuple[int, ...]
    lower: int | None
    upper: int | None
    uncertain_indices: tuple[int, ...] = ()
    """属性未知但未被排除的候选下标；它们计入可行域，但需要待确认。"""

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
class PendingAttributeCheck:
    """一条替代要求只能靠未知属性满足时生成的待确认项。"""

    constraint_id: str
    owner_member_id: int
    category: str | None
    attribute_keys: tuple[str, ...]
    required_count: int
    confirmed_count: int
    unconfirmed_candidate_ids: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "constraint_id": self.constraint_id,
            "category": self.category,
            "attribute_keys": list(self.attribute_keys),
            "required_count": self.required_count,
            "confirmed_count": self.confirmed_count,
            "unconfirmed_candidate_ids": list(self.unconfirmed_candidate_ids),
        }


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
    pending_checks: tuple[PendingAttributeCheck, ...] = ()

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


def attribute_match(
    expected: Mapping[str, AttributeValue], candidate: Candidate
) -> MatchOutcome:
    """三态匹配：返回 match / miss / uncertain。

    规则：显式 UNKNOWN 一律 uncertain；有已知状态时按布尔期望判定；
    没有任何记录也表示未知（未标注 ≠ 不含），绝不静默当成 miss。
    """
    uncertain = False
    for key, value in expected.items():
        state = candidate.attribute_states.get(key)
        if state is AttributeState.UNKNOWN:
            uncertain = True
            continue
        if state is not None:
            if isinstance(value, bool):
                matched = (state is AttributeState.PRESENT) == value
                if not matched:
                    return "miss"
                continue
            # 三态只能表达“有无”，无法核对字符串/数字取值。
            uncertain = True
            continue
        if key in candidate.attributes:
            actual = candidate.attributes[key]
            if type(actual) is not type(value) or actual != value:
                return "miss"
            continue
        uncertain = True
    return "uncertain" if uncertain else "match"


def pending_for_requirement(
    constraint: ConfirmedConstraint,
    candidates: Sequence[Candidate],
    *,
    eligible_ids: Iterable[str] | None = None,
    excluded_ids: Iterable[str] | None = None,
) -> PendingAttributeCheck | None:
    """计算一条替代要求的待确认项；已确认匹配足够时返回 None。"""
    if constraint.kind is not ConstraintKind.REQUIRE_COUNT:
        return None
    if constraint.owner_member_id is None or constraint.min_count is None:
        return None
    allowed = None if eligible_ids is None else frozenset(eligible_ids)
    banned = frozenset(excluded_ids or ())
    confirmed = 0
    unconfirmed: list[str] = []
    for candidate in candidates:
        if not candidate.active:
            continue
        if candidate.candidate_id in banned:
            continue
        if allowed is not None and candidate.candidate_id not in allowed:
            continue
        if constraint.category is not None and candidate.category != constraint.category:
            continue
        outcome = attribute_match(constraint.attributes, candidate)
        if outcome == "match":
            confirmed += 1
        elif outcome == "uncertain":
            unconfirmed.append(candidate.candidate_id)
    if not unconfirmed or confirmed >= constraint.min_count:
        return None
    return PendingAttributeCheck(
        constraint_id=constraint.constraint_id,
        owner_member_id=constraint.owner_member_id,
        category=constraint.category,
        attribute_keys=tuple(sorted(constraint.attributes)),
        required_count=constraint.min_count,
        confirmed_count=confirmed,
        unconfirmed_candidate_ids=tuple(sorted(unconfirmed)),
    )


def _row_coefficients(
    model_candidates: Sequence[Candidate],
    *,
    eligible: Sequence[int],
    category: str | None,
    attributes: Mapping[str, AttributeValue],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    coefficients: list[int] = []
    uncertain: list[int] = []
    for index, candidate in enumerate(model_candidates):
        if eligible[index] != 1:
            coefficients.append(0)
            continue
        if category is not None and candidate.category != category:
            coefficients.append(0)
            continue
        if attributes:
            outcome = attribute_match(attributes, candidate)
            if outcome == "miss":
                coefficients.append(0)
                continue
            if outcome == "uncertain":
                uncertain.append(index)
        coefficients.append(1)
    return tuple(coefficients), tuple(uncertain)


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
    pending: list[PendingAttributeCheck] = []
    for constraint in constraints:
        if constraint.kind is ConstraintKind.REQUIRE_COUNT:
            if constraint.owner_member_id not in member_slot:
                raise CompileError(
                    f"替代要求引用未知或已停用成员：{constraint.owner_member_id}"
                )
            slot = member_slot[constraint.owner_member_id]
            coefficients, uncertain = _row_coefficients(
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
                    uncertain_indices=uncertain,
                )
            )
            check = pending_for_requirement(
                constraint,
                active_candidates,
                eligible_ids=(
                    candidate_id
                    for candidate_id, eligible in zip(candidate_ids, e[slot])
                    if eligible == 1
                ),
                excluded_ids=(candidate_ids[index] for index in excluded),
            )
            if check is not None:
                pending.append(check)
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
        pending_checks=tuple(pending),
    )
