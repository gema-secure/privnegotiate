"""发起协商、加入与公共投影。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.deps import current_member, require_negotiation
from app.db.session import get_db
from app.engine.projection import build_public_view
from app.engine.setup import create_negotiation, invite_token, join_negotiation
from app.models.business import Member
from app.schemas.commands import CreateNegotiationCommand, JoinNegotiationCommand
from app.schemas.private_views import CreatedNegotiationView, JoinedMemberView
from app.schemas.public_views import PublicNegotiationView

router = APIRouter(prefix="/negotiations", tags=["negotiations"])


@router.post("", response_model=CreatedNegotiationView, status_code=201)
def create(
    command: CreateNegotiationCommand, db: Session = Depends(get_db)
) -> CreatedNegotiationView:
    negotiation, member, token = create_negotiation(db, command)
    invite = invite_token(negotiation.id)
    return CreatedNegotiationView(
        negotiation_id=negotiation.id,
        member_id=member.id,
        member_token=token,
        invite_token=invite,
        invite_path=f"/join/{negotiation.id}/{invite}",
    )


@router.post(
    "/{negotiation_id}/members", response_model=JoinedMemberView, status_code=201
)
def join(
    negotiation_id: int,
    command: JoinNegotiationCommand,
    db: Session = Depends(get_db),
) -> JoinedMemberView:
    member, token = join_negotiation(db, negotiation_id, command)
    return JoinedMemberView(
        negotiation_id=member.negotiation_id,
        member_id=member.id,
        display_name=member.display_name,
        role=member.role,
        member_token=token,
    )


@router.get("/{negotiation_id}/public", response_model=PublicNegotiationView)
def public(
    negotiation_id: int,
    response: Response,
    member: Member = Depends(current_member),
    db: Session = Depends(get_db),
) -> PublicNegotiationView:
    # 公共指成员共享的字段白名单，不表示允许匿名访问。
    response.headers["Cache-Control"] = "no-store"
    negotiation = require_negotiation(db, negotiation_id)
    return build_public_view(db, negotiation)
