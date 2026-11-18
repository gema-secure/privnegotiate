"""封轮与结果确认。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import current_member, require_negotiation
from app.db.session import get_db
from app.engine.closing import begin_round_close, commit_round, run_round_computation
from app.engine.confirmation import submit_confirmation
from app.engine.state import conflict
from app.models.business import Member, Negotiation
from app.schemas.commands import ConfirmResultCommand
from app.schemas.private_views import CloseRoundView, MyConfirmationView

router = APIRouter(prefix="/negotiations", tags=["rounds"])


@router.post("/{negotiation_id}/rounds/{round_no}/close", response_model=CloseRoundView)
def close_round(
    negotiation_id: int,
    round_no: int,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> CloseRoundView:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True, populate_existing=True)
    if negotiation is None:
        require_negotiation(db, negotiation_id)
    if round_no != negotiation.round_no:
        raise conflict("ROUND_MISMATCH", "只能关闭当前轮次")

    if negotiation.status == "CHECKING_FEASIBILITY":
        from app.engine.feasibility import start_round
        started = start_round(db, negotiation, actor_member_id=member.id)
        return CloseRoundView(round_no=round_no, negotiation_status=started.status,
                              outcome="retry" if started.status == "RETRY_OR_REVIEW" else "changed",
                              message="可行性检查已恢复")

    snapshot = begin_round_close(db, negotiation, member)
    computation = run_round_computation(snapshot)
    outcome = commit_round(db, snapshot, computation)
    return CloseRoundView(**outcome)


@router.post(
    "/{negotiation_id}/results/{result_version}/me/confirmation",
    response_model=MyConfirmationView,
)
def confirm_result(
    negotiation_id: int,
    result_version: int,
    command: ConfirmResultCommand,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> MyConfirmationView:
    row = submit_confirmation(db, negotiation_id, member, result_version, command)
    return MyConfirmationView(
        result_version=row.result_version,
        decision_config_version=row.decision_config_version,
        decision="accept" if row.accept else "reject",
        decided_at=row.decided_at,
    )
