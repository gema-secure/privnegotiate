"""菜品三态属性的持久化与人工修正留痕。"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.events import append_event
from app.engine.state import conflict, forbidden, not_found
from app.models.business import (
    Candidate,
    CandidateAttribute,
    CandidateAttributeRevision,
    Member,
    Negotiation,
)

CLOSED_STATUSES = frozenset({"FINALIZED", "CLOSED_WITH_CONCERN", "CLOSED_UNCONFIRMED"})


def candidate_attribute_rows(
    db: Session, negotiation_id: int
) -> dict[int, list[CandidateAttribute]]:
    grouped: dict[int, list[CandidateAttribute]] = defaultdict(list)
    rows = db.scalars(
        select(CandidateAttribute)
        .where(CandidateAttribute.negotiation_id == negotiation_id)
        .order_by(CandidateAttribute.attribute_key)
    ).all()
    for row in rows:
        grouped[row.candidate_id].append(row)
    return dict(grouped)


def correct_candidate_attributes(
    db: Session,
    negotiation: Negotiation,
    candidate_public_id: str,
    command,
    actor: Member,
) -> Candidate:
    """发起者修正菜品属性：只改事实，不触碰任何成员底线。"""
    if actor.role != "initiator":
        raise forbidden("INITIATOR_ONLY", "只有发起者可以修正菜品属性")
    if negotiation.status in CLOSED_STATUSES:
        raise conflict("NEGOTIATION_CLOSED", "协商已结束，不能再修正菜品属性")
    candidate = db.scalars(
        select(Candidate).where(
            Candidate.negotiation_id == negotiation.id,
            Candidate.public_id == candidate_public_id,
        )
    ).one_or_none()
    if candidate is None:
        raise not_found("CANDIDATE_NOT_FOUND", "候选不存在")

    existing = {
        row.attribute_key: row
        for row in db.scalars(
            select(CandidateAttribute).where(
                CandidateAttribute.candidate_id == candidate.id
            )
        ).all()
    }
    changed: list[dict] = []
    for item in command.attributes:
        row = existing.get(item.key)
        new_state = item.state.value
        if row is None:
            row = CandidateAttribute(
                negotiation_id=negotiation.id,
                candidate_id=candidate.id,
                attribute_key=item.key,
                state=new_state,
                source="correction",
                source_detail=item.source_detail,
                updated_by_member_id=actor.id,
            )
            db.add(row)
            db.flush()
            db.add(
                CandidateAttributeRevision(
                    negotiation_id=negotiation.id,
                    attribute_id=row.id,
                    candidate_id=candidate.id,
                    attribute_key=item.key,
                    previous_state=None,
                    new_state=new_state,
                    previous_source=None,
                    new_source="correction",
                    reason=command.reason,
                    changed_by_member_id=actor.id,
                )
            )
            changed.append({"key": item.key, "from": None, "to": new_state})
            continue

        previous_state = row.state
        previous_source = row.source
        row.source_detail = item.source_detail
        row.updated_by_member_id = actor.id
        if previous_state == new_state and previous_source == "correction":
            continue
        row.state = new_state
        row.source = "correction"
        db.add(
            CandidateAttributeRevision(
                negotiation_id=negotiation.id,
                attribute_id=row.id,
                candidate_id=candidate.id,
                attribute_key=item.key,
                previous_state=previous_state,
                new_state=new_state,
                previous_source=previous_source,
                new_source="correction",
                reason=command.reason,
                changed_by_member_id=actor.id,
            )
        )
        changed.append({"key": item.key, "from": previous_state, "to": new_state})

    candidate.metadata_version = (candidate.metadata_version or 1) + 1
    negotiation.state_version += 1
    # 属性事实变化使既有推荐确认与稳定计数失效，需重新计算。
    negotiation.decision_config_version += 1
    append_event(
        db,
        negotiation,
        "CANDIDATE_ATTRIBUTES_UPDATED",
        {"candidate_id": candidate.public_id, "changes": changed},
        actor_member_id=actor.id,
    )
    db.commit()
    return candidate
