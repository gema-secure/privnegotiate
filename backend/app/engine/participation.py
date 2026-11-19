"""参与资格校验：与封轮、成员治理解耦，供各写入路径共用。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.engine.state import EngineError
from app.models.business import Member, Negotiation


def require_participant(db: Session, negotiation: Negotiation, member: Member) -> Member:
    member = db.get(Member, member.id, with_for_update=True, populate_existing=True)
    if (member is None or member.negotiation_id != negotiation.id or not member.active
            or member.token_revoked or member.membership_status in {"LEFT", "REMOVED"}):
        raise EngineError("UNAUTHORIZED", "参与权限已失效", 401)
    return member
