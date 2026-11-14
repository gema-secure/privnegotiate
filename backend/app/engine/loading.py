"""ORM 记录到纯函数输入的加载与转换。"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constraints.dsl import (
    AttributeState,
    Candidate as DslCandidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member as DslMember,
)
from app.models.business import (
    Candidate,
    CandidateAttribute,
    ConfirmedConstraint as ConstraintRow,
    Member,
    QuestionAnswer,
)
from app.preference.acceptance import AnswerObservation


def candidate_dtos(
    rows: Iterable[Candidate],
    attribute_states: dict[int, dict[str, AttributeState]] | None = None,
) -> tuple[DslCandidate, ...]:
    states = attribute_states or {}
    return tuple(
        DslCandidate(
            candidate_id=row.public_id,
            cost_cents=row.cost_cents,
            category=row.category or "unknown",
            attributes=dict(row.confirmed_attributes or {}),
            attribute_states=states.get(row.id, {}),
            active=row.active,
        )
        for row in rows
    )


def attribute_state_map(
    db: Session, negotiation_id: int
) -> dict[int, dict[str, AttributeState]]:
    """读取候选菜品的三态属性；非法旧值按“未知”处理，绝不猜测为不含。"""
    result: dict[int, dict[str, AttributeState]] = defaultdict(dict)
    records = db.scalars(
        select(CandidateAttribute).where(
            CandidateAttribute.negotiation_id == negotiation_id
        )
    ).all()
    for record in records:
        try:
            state = AttributeState(record.state)
        except ValueError:
            state = AttributeState.UNKNOWN
        result[record.candidate_id][record.attribute_key] = state
    return dict(result)


def member_dtos(rows: Iterable[Member]) -> tuple[DslMember, ...]:
    return tuple(
        DslMember(
            member_id=row.id,
            display_name=row.display_name,
            active=row.active,
        )
        for row in rows
    )


def constraint_dtos(rows: Iterable[ConstraintRow]) -> tuple[ConfirmedConstraint, ...]:
    parsed: list[ConfirmedConstraint] = []
    for row in rows:
        payload = row.payload or {}
        parsed.append(
            ConfirmedConstraint(
                constraint_id=row.constraint_id,
                kind=ConstraintKind(row.kind),
                scope=ConstraintScope(row.scope),
                owner_member_id=row.owner_member_id,
                candidate_ids=tuple(payload.get("candidate_ids") or ()),
                category=payload.get("category"),
                attributes=dict(payload.get("attributes") or {}),
                min_count=payload.get("min_count"),
                max_count=payload.get("max_count"),
                amount_cents=payload.get("amount_cents"),
                confirmed_version=row.confirmed_version,
                source_event_id=row.source_event_id,
            )
        )
    return tuple(parsed)


def opt_out_map(
    constraints: Iterable[ConfirmedConstraint],
) -> dict[int, frozenset[str]]:
    result: dict[int, set[str]] = defaultdict(set)
    for constraint in constraints:
        if constraint.kind is ConstraintKind.OPT_OUT and constraint.owner_member_id:
            result[constraint.owner_member_id].update(constraint.candidate_ids)
    return {member_id: frozenset(items) for member_id, items in result.items()}


def answer_observations(rows: Iterable[QuestionAnswer]) -> tuple[AnswerObservation, ...]:
    return tuple(
        AnswerObservation(
            member_id=row.member_id,
            evaluated_ids=tuple(row.evaluated_ids or ()),
            acceptable_ids=tuple(row.acceptable_ids or ()),
            top_id=row.top_id,
            mode=row.mode,
        )
        for row in rows
    )


def active_answers(db: Session, negotiation_id: int) -> list[QuestionAnswer]:
    return list(
        db.scalars(
            select(QuestionAnswer)
            .where(
                QuestionAnswer.negotiation_id == negotiation_id,
                QuestionAnswer.invalidated_at.is_(None),
            )
            .order_by(QuestionAnswer.accepted_at, QuestionAnswer.id)
        ).all()
    )
