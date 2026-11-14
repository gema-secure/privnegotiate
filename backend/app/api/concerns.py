"""顾虑提交与发起者处理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import current_member, require_negotiation
from app.db.session import get_db
from app.engine.confirmation import (
    correct_concern_summary,
    list_concerns,
    organizer_decision,
    submit_concern,
)
from app.llm.concerns import summarize_concern
from app.llm.maas_client import TextLlmClient, get_text_llm_client
from app.models.business import Member
from app.schemas.commands import (
    CorrectConcernSummaryCommand,
    OrganizerDecisionCommand,
    SubmitConcernCommand,
)
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
    client: TextLlmClient = Depends(get_text_llm_client),
    db: Session = Depends(get_db),
) -> MyConcernView:
    require_negotiation(db, negotiation_id)
    assisted = summarize_concern(command.text, client)
    concern = submit_concern(
        db,
        negotiation_id,
        member,
        command,
        summary=assisted.summary,
        summary_source=assisted.source if assisted.summary else None,
    )
    return MyConcernView(
        concern_id=concern.id,
        result_version=concern.result_version,
        text=concern.original_text,
        summary=concern.summary,
        summary_source=assisted.source,
        summary_degraded=assisted.degraded_reason is not None,
        summary_degraded_reason=assisted.degraded_reason,
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
            summary_source=row.summary_source or "none",
            status=row.status,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.patch(
    "/{negotiation_id}/organizer/concerns/{concern_id}/summary",
    response_model=OrganizerConcernView,
)
def correct_summary(
    negotiation_id: int,
    concern_id: int,
    command: CorrectConcernSummaryCommand,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> OrganizerConcernView:
    concern = correct_concern_summary(
        db, negotiation_id, concern_id, member, command.summary
    )
    return OrganizerConcernView(
        concern_id=concern.id,
        result_version=concern.result_version,
        text=concern.original_text,
        summary=concern.summary,
        summary_source=concern.summary_source or "none",
        status=concern.status,
        created_at=concern.created_at,
    )


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
