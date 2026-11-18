"""本人状态、底线、作答、修订与选择链。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import current_member, require_negotiation
from app.db.session import get_db
from app.engine.answers import submit_answer
from app.engine.constraints_service import confirm_member_constraints, revise_preference
from app.engine.feasibility import start_round
from app.engine.projection import build_history, build_my_view
from app.engine.state import ANSWER_OPEN_STATUSES, conflict
from app.models.business import Member, Negotiation
from app.schemas.commands import (
    AnswerCommand,
    ConfirmMemberConstraintsCommand,
    PreferenceRevisionCommand,
)
from app.schemas.private_views import (
    MyHistoryEntryView,
    MyStateView,
    SubmitAnswerResultView,
)

router = APIRouter(prefix="/negotiations", tags=["me"])


@router.get("/{negotiation_id}/me", response_model=MyStateView)
def my_state(
    negotiation_id: int,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> MyStateView:
    negotiation = require_negotiation(db, negotiation_id)
    return build_my_view(db, negotiation, member)


@router.post("/{negotiation_id}/me/constraints", response_model=MyStateView)
def confirm_constraints(
    negotiation_id: int,
    command: ConfirmMemberConstraintsCommand,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> MyStateView:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True, populate_existing=True)
    if negotiation is None:
        require_negotiation(db, negotiation_id)
    if negotiation.status not in {
        "COLLECTING_BOTTOMLINE",
        "CHECKING_FEASIBILITY",
        "ASKING",
        "ANSWERING",
        "INFEASIBLE",
        "RETRY_OR_REVIEW",
        "ADJUSTING",
    }:
        raise conflict("CONSTRAINTS_LOCKED", f"当前状态 {negotiation.status} 不能修改底线")

    confirm_member_constraints(db, negotiation, member, command)
    db.flush()

    members = db.scalars(
        select(Member).where(
            Member.negotiation_id == negotiation.id, Member.active.is_(True)
        )
    ).all()
    open_round = negotiation.status in ANSWER_OPEN_STATUSES
    if all(row.bottomline_status != "pending" for row in members) and not open_round:
        start_round(db, negotiation, actor_member_id=member.id)
    db.commit()
    return build_my_view(db, negotiation, member)


@router.post("/{negotiation_id}/me/answers", response_model=SubmitAnswerResultView)
def submit(
    negotiation_id: int,
    command: AnswerCommand,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> SubmitAnswerResultView:
    answer, duplicate = submit_answer(db, negotiation_id, member, command)
    round_no = _round_of(db, answer)
    db.commit()
    negotiation = require_negotiation(db, negotiation_id)
    return SubmitAnswerResultView(
        batch_id=command.question_batch_id,
        round_no=round_no,
        mode=answer.mode,
        accepted_at=answer.accepted_at,
        duplicate=duplicate,
        negotiation_status=negotiation.status,
    )


def _round_of(db: Session, answer) -> int:
    from app.models.business import QuestionBatch

    batch = db.get(QuestionBatch, answer.batch_id)
    return batch.round_no if batch is not None else 0


@router.post("/{negotiation_id}/me/revisions", response_model=MyStateView)
def revise(
    negotiation_id: int,
    command: PreferenceRevisionCommand,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> MyStateView:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True, populate_existing=True)
    if negotiation is None:
        require_negotiation(db, negotiation_id)

    def restart() -> None:
        start_round(db, negotiation, actor_member_id=member.id)

    revise_preference(db, negotiation, member, command, restart=restart)
    db.commit()
    return build_my_view(db, negotiation, member)


@router.get("/{negotiation_id}/me/history", response_model=list[MyHistoryEntryView])
def history(
    negotiation_id: int,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> list[MyHistoryEntryView]:
    negotiation = require_negotiation(db, negotiation_id)
    return build_history(db, negotiation, member)
