"""成员治理与退出后的受限本人权限。"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import authenticated_member, current_member, require_negotiation, retained_member
from app.db.session import get_db
from app.engine import membership as service
from app.engine.events import append_event
from app.models.business import Member
from app.schemas.membership import (
    LeaveCommand, MembershipCommand, MembershipView, RemovalCommand,
    RemovalView, RemovalVoteCommand, RetainedConstraintsView, RetainedWithdrawalCommand,
)

router = APIRouter(prefix="/negotiations", tags=["membership"])


@router.get("/{negotiation_id}/me/membership", response_model=MembershipView)
def membership(negotiation_id: int, member: Member = Depends(current_member), db: Session = Depends(get_db)):
    return service.membership_view(db, require_negotiation(db, negotiation_id), member)


@router.post("/{negotiation_id}/me/leave", status_code=204)
def leave(negotiation_id: int, command: LeaveCommand, member: Member = Depends(current_member), db: Session = Depends(get_db)):
    service.leave(db, negotiation_id, member, command)
    return Response(status_code=204)


@router.post("/{negotiation_id}/members/{target_id}/removal-requests", response_model=RemovalView)
def request_removal(negotiation_id: int, target_id: int, command: RemovalCommand, member: Member = Depends(current_member), db: Session = Depends(get_db)):
    row = service.request_removal(db, negotiation_id, member, target_id, command)
    return RemovalView(request_id=row.id, target_member_id=row.target_member_id,
                       membership_version=row.membership_version, status=row.status)


@router.post("/{negotiation_id}/removal-requests/{request_id}/me/authorization", response_model=RemovalView)
def authorize_removal(negotiation_id: int, request_id: int, command: RemovalVoteCommand, member: Member = Depends(current_member), db: Session = Depends(get_db)):
    row = service.resolve_removal(db, negotiation_id, member, request_id, command)
    return RemovalView(request_id=row.id, target_member_id=row.target_member_id,
                       membership_version=row.membership_version, status=row.status,
                       my_vote=row.votes.get(str(member.id)))


@router.post("/{negotiation_id}/removal-requests/{request_id}/cancel", status_code=204)
def cancel_removal(negotiation_id: int, request_id: int, command: MembershipCommand, member: Member = Depends(current_member), db: Session = Depends(get_db)):
    service.resolve_removal(db, negotiation_id, member, request_id, command, cancel=True)
    return Response(status_code=204)


@router.post("/{negotiation_id}/members/{target_id}/unresponsive", status_code=204)
def unresponsive(negotiation_id: int, target_id: int, command: MembershipCommand, member: Member = Depends(current_member), db: Session = Depends(get_db)):
    service.mark_unresponsive(db, negotiation_id, member, target_id, command)
    return Response(status_code=204)


@router.post("/{negotiation_id}/me/return", status_code=204)
def return_to_active(negotiation_id: int, member: Member = Depends(current_member), db: Session = Depends(get_db)):
    service.return_to_active(db, negotiation_id, member)
    return Response(status_code=204)


@router.get("/{negotiation_id}/me/retained-constraints", response_model=RetainedConstraintsView)
def retained(negotiation_id: int, response: Response, member: Member = Depends(retained_member), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.retained_view(db, member)


@router.post("/{negotiation_id}/me/retained-constraints/{constraint_id}/withdraw", response_model=RetainedConstraintsView)
def withdraw(negotiation_id: int, constraint_id: str, command: RetainedWithdrawalCommand, member: Member = Depends(retained_member), db: Session = Depends(get_db)):
    service.withdraw_retained(db, negotiation_id, member, constraint_id, command)
    return service.retained_view(db, member)


@router.post("/{negotiation_id}/me/revoke-token", status_code=204)
def revoke_token(negotiation_id: int, member: Member = Depends(authenticated_member), db: Session = Depends(get_db)):
    negotiation = service.lock_negotiation(db, negotiation_id)
    member.token_revoked = True
    append_event(db, negotiation, "MEMBER_TOKEN_REVOKED", {}, actor_member_id=member.id)
    db.commit()
    return Response(status_code=204)
