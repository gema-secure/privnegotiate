"""问题包派发：首版确定性选择，每人每轮一包。"""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constraints.dsl import ConfirmedConstraint, ConstraintKind
from app.constraints.dsl import Candidate as DslCandidate
from app.constraints.dsl import Member as DslMember
from app.core.config import settings
from app.engine.loading import opt_out_map
from app.models.business import (
    Candidate,
    Negotiation,
    QuestionAnswer,
    QuestionBatch,
    utcnow,
)

MAX_QUESTIONS = 6


def _evaluated_map(db: Session, negotiation_id: int) -> dict[int, set[str]]:
    evaluated: dict[int, set[str]] = {}
    rows = db.scalars(
        select(QuestionAnswer).where(
            QuestionAnswer.negotiation_id == negotiation_id,
            QuestionAnswer.invalidated_at.is_(None),
        )
    ).all()
    for row in rows:
        evaluated.setdefault(row.member_id, set()).update(row.evaluated_ids or ())
    return evaluated


def _excluded_public(constraints: tuple[ConfirmedConstraint, ...]) -> set[str]:
    excluded: set[str] = set()
    for constraint in constraints:
        if constraint.kind is ConstraintKind.EXCLUDE_ITEM:
            excluded.update(constraint.candidate_ids)
    return excluded


def create_round_batches(
    db: Session,
    negotiation: Negotiation,
    *,
    round_no: int,
    candidates: tuple[DslCandidate, ...],
    members: tuple[DslMember, ...],
    constraints: tuple[ConfirmedConstraint, ...],
    ranking: Mapping[int, Mapping[str, float]] | None = None,
    plan=None,
) -> list[QuestionBatch]:
    """为仍有未评价候选的成员建包；无候选可问的成员本轮不发包。"""
    for row in db.scalars(
        select(QuestionBatch).where(
            QuestionBatch.negotiation_id == negotiation.id,
            QuestionBatch.status == "issued",
        )
    ).all():
        row.status = "closed"

    excluded = _excluded_public(constraints)
    opt_out = opt_out_map(constraints)
    evaluated = _evaluated_map(db, negotiation.id)
    batch_size = int((negotiation.config_json or {}).get("batch_size", 4))
    batch_size = max(1, min(MAX_QUESTIONS, batch_size))
    deadline = utcnow() + timedelta(seconds=settings.answer_deadline_seconds)

    created: list[QuestionBatch] = []
    for member in members:
        if not member.active:
            continue
        blocked = excluded | set(opt_out.get(member.member_id, frozenset()))
        pool = [
            item
            for item in candidates
            if item.active
            and item.candidate_id not in blocked
            and item.candidate_id not in evaluated.get(member.member_id, set())
        ]
        if not pool:
            continue
        if plan is not None:
            available = {item.candidate_id for item in pool}
            selected = [
                candidate_id
                for candidate_id in plan.member_items(member.member_id)
                if candidate_id in available
            ][:batch_size]
            if not selected:
                continue
        else:
            member_ranking = ranking.get(member.member_id, {}) if ranking else {}
            pool.sort(
                key=lambda item: (
                    -float(member_ranking.get(item.candidate_id, 0.0)),
                    item.candidate_id,
                )
            )
            selected = [item.candidate_id for item in pool[:batch_size]]
        batch = QuestionBatch(
            negotiation_id=negotiation.id,
            batch_public_id=f"qb{negotiation.id}r{round_no}m{member.member_id}",
            round_no=round_no,
            member_id=member.member_id,
            displayed_ids=selected,
            snapshot_version=negotiation.state_version,
            status="issued",
            deadline=deadline,
        )
        db.add(batch)
        created.append(batch)
    db.flush()
    return created


def displayed_candidates(
    db: Session, negotiation_id: int, batch: QuestionBatch
) -> list[Candidate]:
    rows = db.scalars(
        select(Candidate).where(Candidate.negotiation_id == negotiation_id)
    ).all()
    by_id = {row.public_id: row for row in rows}
    return [by_id[item] for item in (batch.displayed_ids or []) if item in by_id]


def mark_batches_closed(db: Session, negotiation_id: int, round_no: int) -> list[QuestionBatch]:
    rows = list(
        db.scalars(
            select(QuestionBatch).where(
                QuestionBatch.negotiation_id == negotiation_id,
                QuestionBatch.round_no == round_no,
                QuestionBatch.status == "issued",
            )
        ).all()
    )
    for row in rows:
        row.status = "closed"
    return rows


def reopen_batches(db: Session, negotiation_id: int, round_no: int) -> None:
    rows = db.scalars(
        select(QuestionBatch).where(
            QuestionBatch.negotiation_id == negotiation_id,
            QuestionBatch.round_no == round_no,
            QuestionBatch.status == "closed",
        )
    ).all()
    for row in rows:
        row.status = "issued"
        row.deadline = utcnow() + timedelta(seconds=settings.answer_deadline_seconds)


def collection_complete(db: Session, negotiation_id: int, round_no: int) -> bool:
    rows = db.scalars(
        select(QuestionBatch).where(
            QuestionBatch.negotiation_id == negotiation_id,
            QuestionBatch.round_no == round_no,
        )
    ).all()
    if not rows:
        return True
    answered = {
        row.member_id
        for row in rows
        if db.scalar(
            select(QuestionAnswer.id).where(
                QuestionAnswer.batch_id == row.id,
                QuestionAnswer.invalidated_at.is_(None),
            )
        )
        is not None
    }
    return answered >= {row.member_id for row in rows}


def all_deadlines_passed(db: Session, negotiation_id: int, round_no: int) -> bool:
    from app.engine.state import utc_aware

    rows = db.scalars(
        select(QuestionBatch).where(
            QuestionBatch.negotiation_id == negotiation_id,
            QuestionBatch.round_no == round_no,
        )
    ).all()
    if not rows:
        return True
    now = utcnow()
    return all(
        utc_aware(row.deadline) is not None and utc_aware(row.deadline) <= now for row in rows
    )
