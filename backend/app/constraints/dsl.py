"""约束 DSL：只接受白名单类型与字段，禁止表达式求值。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

AttributeValue = str | int | bool


class AttributeState(str, Enum):
    """菜品属性的确定状态：已知含 / 已知不含 / 未知。"""

    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"



class DslError(ValueError):
    """约束不符合白名单或参数语义。"""


class ConstraintKind(str, Enum):
    EXCLUDE_ITEM = "exclude_item"
    OPT_OUT = "opt_out"
    REQUIRE_COUNT = "require_count"
    CATEGORY_COUNT = "category_count"
    BUDGET = "budget"
    EXACT_COUNT = "exact_count"


class ConstraintScope(str, Enum):
    PUBLIC = "public"
    MEMBER = "member"


SCOPE_BY_KIND: Mapping[ConstraintKind, ConstraintScope] = {
    ConstraintKind.EXCLUDE_ITEM: ConstraintScope.PUBLIC,
    ConstraintKind.OPT_OUT: ConstraintScope.MEMBER,
    ConstraintKind.REQUIRE_COUNT: ConstraintScope.MEMBER,
    ConstraintKind.CATEGORY_COUNT: ConstraintScope.PUBLIC,
    ConstraintKind.BUDGET: ConstraintScope.PUBLIC,
    ConstraintKind.EXACT_COUNT: ConstraintScope.PUBLIC,
}

COMMON_FIELDS = frozenset(
    {"constraint_id", "kind", "scope", "owner_member_id", "confirmed_version", "source_event_id"}
)

PAYLOAD_FIELDS: Mapping[ConstraintKind, frozenset[str]] = {
    ConstraintKind.EXCLUDE_ITEM: frozenset({"candidate_ids"}),
    ConstraintKind.OPT_OUT: frozenset({"candidate_ids"}),
    ConstraintKind.REQUIRE_COUNT: frozenset(
        {"category", "attributes", "min_count"}
    ),
    ConstraintKind.CATEGORY_COUNT: frozenset({"category", "min_count", "max_count"}),
    ConstraintKind.BUDGET: frozenset({"amount_cents"}),
    ConstraintKind.EXACT_COUNT: frozenset({"min_count"}),
}

ALLOWED_FIELDS: Mapping[ConstraintKind, frozenset[str]] = {
    kind: COMMON_FIELDS | payload
    for kind, payload in PAYLOAD_FIELDS.items()
}


@dataclass(frozen=True, slots=True)
class ConfirmedConstraint:
    constraint_id: str
    kind: ConstraintKind
    scope: ConstraintScope
    owner_member_id: int | None = None
    candidate_ids: tuple[str, ...] = ()
    category: str | None = None
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)
    min_count: int | None = None
    max_count: int | None = None
    amount_cents: int | None = None
    confirmed_version: int = 1
    source_event_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_ids", tuple(self.candidate_ids))
        object.__setattr__(
            self, "attributes", MappingProxyType(dict(self.attributes))
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    cost_cents: int
    category: str = "unknown"
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)
    attribute_states: Mapping[str, AttributeState] = field(default_factory=dict)
    active: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "attributes", MappingProxyType(dict(self.attributes))
        )
        object.__setattr__(
            self, "attribute_states", MappingProxyType(dict(self.attribute_states))
        )


@dataclass(frozen=True, slots=True)
class Member:
    member_id: int
    display_name: str = ""
    active: bool = True


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DslError(f"{name} 必须是整数")
    return value


def _as_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DslError(f"{name} 必须是非空字符串")
    return value.strip()


def _parse_attributes(value: Any) -> Mapping[str, AttributeValue]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise DslError("attributes 必须是键值映射")
    parsed: dict[str, AttributeValue] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise DslError("attributes 的键必须是非空字符串")
        if not isinstance(item, (str, int, bool)):
            raise DslError(f"attributes[{key}] 只允许字符串、整数或布尔值")
        parsed[key] = item
    return MappingProxyType(parsed)


def validate_constraint(constraint: ConfirmedConstraint) -> None:
    """复核一条已构造约束的类型、范围与角色归属。"""
    if not isinstance(constraint.kind, ConstraintKind):
        raise DslError(f"未知约束类型：{constraint.kind!r}")
    if constraint.scope is not SCOPE_BY_KIND[constraint.kind]:
        raise DslError(
            f"{constraint.kind.value} 的作用域必须是 {SCOPE_BY_KIND[constraint.kind].value}"
        )
    if not constraint.constraint_id or not constraint.constraint_id.strip():
        raise DslError("constraint_id 不能为空")
    if constraint.confirmed_version < 1:
        raise DslError("confirmed_version 必须为正整数")
    if constraint.kind is ConstraintKind.EXCLUDE_ITEM:
        if constraint.owner_member_id is not None:
            raise DslError("整组禁选属于公共约束，不能绑定成员")
        if not constraint.candidate_ids:
            raise DslError("exclude_item 必须指定 candidate_ids")
        if len(set(constraint.candidate_ids)) != len(constraint.candidate_ids):
            raise DslError("candidate_ids 不能重复")
    elif constraint.kind is ConstraintKind.OPT_OUT:
        if constraint.owner_member_id is None:
            raise DslError("opt_out 必须绑定成员")
        if not constraint.candidate_ids:
            raise DslError("opt_out 必须指定 candidate_ids")
        if len(set(constraint.candidate_ids)) != len(constraint.candidate_ids):
            raise DslError("candidate_ids 不能重复")
    elif constraint.kind is ConstraintKind.REQUIRE_COUNT:
        if constraint.owner_member_id is None:
            raise DslError("替代要求必须绑定成员")
        if constraint.min_count is None or constraint.min_count < 1:
            raise DslError("替代要求必须给出正数 min_count")
        if constraint.category is None and not constraint.attributes:
            raise DslError("替代要求必须给出类别或属性条件")
        if constraint.max_count is not None:
            raise DslError("替代要求只支持下限")
        if constraint.amount_cents is not None or constraint.candidate_ids:
            raise DslError("替代要求不接受金额或候选列表")
    elif constraint.kind is ConstraintKind.CATEGORY_COUNT:
        if constraint.owner_member_id is not None:
            raise DslError("类别上下限属于公共约束，不能绑定成员")
        if constraint.category is None:
            raise DslError("category_count 必须指定类别")
        if constraint.min_count is None and constraint.max_count is None:
            raise DslError("category_count 至少给出一条边界")
        lower = constraint.min_count
        upper = constraint.max_count
        if lower is not None and lower < 0:
            raise DslError("类别下限不能为负")
        if upper is not None and upper < 0:
            raise DslError("类别上限不能为负")
        if lower is not None and upper is not None and lower > upper:
            raise DslError("类别下限不能大于上限")
        if constraint.candidate_ids or constraint.amount_cents is not None:
            raise DslError("category_count 不接受候选列表或金额")
    elif constraint.kind is ConstraintKind.BUDGET:
        if constraint.owner_member_id is not None:
            raise DslError("预算属于公共约束，不能绑定成员")
        if constraint.amount_cents is None or constraint.amount_cents < 0:
            raise DslError("预算必须是非负整数分")
    elif constraint.kind is ConstraintKind.EXACT_COUNT:
        if constraint.owner_member_id is not None:
            raise DslError("恰选数量属于公共约束，不能绑定成员")
        if constraint.min_count is None or constraint.min_count < 1:
            raise DslError("恰选数量必须为正整数")


def parse_constraint(
    payload: Mapping[str, Any],
    *,
    confirmed_version: int | None = None,
    source_event_id: int | None = None,
) -> ConfirmedConstraint:
    """从字典构造约束；任何白名单外字段直接拒绝。"""
    if not isinstance(payload, Mapping):
        raise DslError("约束必须是键值映射")
    raw_kind = payload.get("kind")
    try:
        kind = ConstraintKind(raw_kind)
    except ValueError as exc:
        raise DslError(f"未知约束类型：{raw_kind!r}") from exc

    unknown = set(payload) - ALLOWED_FIELDS[kind]
    if unknown:
        raise DslError(f"{kind.value} 不接受字段：{sorted(unknown)}")

    raw_scope = payload.get("scope")
    if raw_scope is None:
        scope = SCOPE_BY_KIND[kind]
    else:
        try:
            scope = ConstraintScope(raw_scope)
        except ValueError as exc:
            raise DslError(f"未知作用域：{raw_scope!r}") from exc

    owner = payload.get("owner_member_id")
    if owner is not None:
        owner = _as_int(owner, "owner_member_id")

    ids = payload.get("candidate_ids") or ()
    if not isinstance(ids, (list, tuple)):
        raise DslError("candidate_ids 必须是列表")
    candidate_ids = tuple(_as_str(item, "candidate_id") for item in ids)

    category = payload.get("category")
    if category is not None:
        category = _as_str(category, "category")

    min_count = payload.get("min_count")
    if min_count is not None:
        min_count = _as_int(min_count, "min_count")

    max_count = payload.get("max_count")
    if max_count is not None:
        max_count = _as_int(max_count, "max_count")

    amount = payload.get("amount_cents")
    if amount is not None:
        amount = _as_int(amount, "amount_cents")

    version = payload.get("confirmed_version", confirmed_version)
    version = 1 if version is None else _as_int(version, "confirmed_version")
    event_id = payload.get("source_event_id", source_event_id)
    if event_id is not None:
        event_id = _as_int(event_id, "source_event_id")

    constraint = ConfirmedConstraint(
        constraint_id=_as_str(payload.get("constraint_id"), "constraint_id"),
        kind=kind,
        scope=scope,
        owner_member_id=owner,
        candidate_ids=candidate_ids,
        category=category,
        attributes=_parse_attributes(payload.get("attributes")),
        min_count=min_count,
        max_count=max_count,
        amount_cents=amount,
        confirmed_version=version,
        source_event_id=event_id,
    )
    validate_constraint(constraint)
    return constraint


def parse_constraints(
    payloads: Iterable[Mapping[str, Any]],
) -> tuple[ConfirmedConstraint, ...]:
    parsed = tuple(parse_constraint(item) for item in payloads)
    ids = [item.constraint_id for item in parsed]
    if len(ids) != len(set(ids)):
        raise DslError("constraint_id 必须唯一")
    return parsed
