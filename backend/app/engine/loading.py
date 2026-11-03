"""ORM 记录到纯函数输入的加载与转换。"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constraints.dsl import (
    Candidate as DslCandidate,
    ConfirmedConstraint,
    ConstraintKind,
    ConstraintScope,
    Member as DslMember,
)
from app.models.business import (
    Candidate,
    ConfirmedConstraint as ConstraintRow,
    Member,
    QuestionAnswer,
)
from app.preference.acceptance import AnswerObservation


def candidate_dtos(rows: Iterable[Candidate]) -> tuple[DslCandidate, ...]:
    return tuple(
        DslCandidate(
            candidate_id=row.public_id,
            cost_cents=row.cost_cents,
            category=row.category or "unknown",
            attributes=dict(row.confirmed_attributes or {}),
            active=row.active,
        )
        for row in rows
    )


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
                owner_member_id=None if row.kind == "exclude_item" else row.owner_member_id,
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
            select(QuestionAnswer).join(Member, Member.id == QuestionAnswer.member_id)
            .where(
                Member.active.is_(True),
                QuestionAnswer.negotiation_id == negotiation_id,
                QuestionAnswer.invalidated_at.is_(None),
            )
            .order_by(QuestionAnswer.accepted_at, QuestionAnswer.id)
        ).all()
    )
