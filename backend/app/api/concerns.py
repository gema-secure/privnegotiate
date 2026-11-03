"""顾虑提交与发起者处理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import current_member, require_negotiation
from app.db.session import get_db
from app.engine.confirmation import list_concerns, organizer_decision, submit_concern
from app.models.business import Member
from app.schemas.commands import OrganizerDecisionCommand, SubmitConcernCommand
from app.schemas.private_views import (
    MyConcernView,
    OrganizerConcernView,
    OrganizerDecisionView,
)

router = APIRouter(prefix="/negotiations", tags=["concerns"])


@router.post("/{negotiation_id}/me/concerns", response_model=MyConcernView)
def create_concern(
    negotiation_id: int,
    command: SubmitConcernCommand,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> MyConcernView:
    require_negotiation(db, negotiation_id)
    concern = submit_concern(db, negotiation_id, member, command)
    return MyConcernView(
        concern_id=concern.id,
        result_version=concern.result_version,
        text=concern.original_text,
        summary=concern.summary,
        status=concern.status,
        created_at=concern.created_at,
    )


@router.get(
    "/{negotiation_id}/organizer/concerns",
    response_model=list[OrganizerConcernView],
)
def organizer_concerns(
    negotiation_id: int,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> list[OrganizerConcernView]:
    rows = list_concerns(db, negotiation_id, member)
    return [
        OrganizerConcernView(
            concern_id=row.id,
            result_version=row.result_version,
            text=row.original_text,
            summary=row.summary,
            status=row.status,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.post(
    "/{negotiation_id}/organizer/decision", response_model=OrganizerDecisionView
)
def decide(
    negotiation_id: int,
    command: OrganizerDecisionCommand,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> OrganizerDecisionView:
    result = organizer_decision(db, negotiation_id, member, command)
    return OrganizerDecisionView(**result)
