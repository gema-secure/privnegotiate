"""请求级鉴权：Bearer 令牌只映射到单协商/单成员。"""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import hash_token
from app.db.session import get_db
from app.engine.state import EngineError, not_found
from app.models.business import Member, Negotiation


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise EngineError("UNAUTHORIZED", "缺少成员令牌", 401)
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise EngineError("UNAUTHORIZED", "缺少成员令牌", 401)
    return token


def authenticated_member(
    negotiation_id: int,
    token: str = Depends(bearer_token),
    db: Session = Depends(get_db),
) -> Member:
    # 所有成员请求与加入/成员变更按同一协商行串行；等待锁后刷新旧 identity map。
    db.get(Negotiation, negotiation_id, with_for_update=True, populate_existing=True)
    member = db.scalars(
        select(Member).where(Member.token_hash == hash_token(token))
        .with_for_update().execution_options(populate_existing=True)
    ).one_or_none()
    if member is None or member.token_revoked or member.negotiation_id != negotiation_id:
        raise EngineError("UNAUTHORIZED", "令牌无效或不属于该协商", 401)
    return member


def current_member(member: Member = Depends(authenticated_member)) -> Member:
    if not member.active or member.membership_status in {"LEFT", "REMOVED"}:
        raise EngineError("UNAUTHORIZED", "参与权限已失效，仅可管理本人保留的整组禁忌", 401)
    return member


def retained_member(member: Member = Depends(authenticated_member)) -> Member:
    if member.membership_status not in {"LEFT", "REMOVED"}:
        raise EngineError("RETAINED_ACCESS_ONLY", "此入口仅供已退出成员管理本人保留禁忌", 403)
    return member


def require_negotiation(db: Session, negotiation_id: int) -> Negotiation:
    negotiation = db.get(Negotiation, negotiation_id)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    return negotiation
