"""创建协商与邀请加入；凭证只在此处一次性返回。"""

from __future__ import annotations

import base64
import hashlib
import hmac

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import generate_member_token, hash_token
from app.core.config import settings
from app.engine.constraints_service import write_public_conditions
from app.engine.events import append_event
from app.engine.state import ANSWER_OPEN_STATUSES, conflict, forbidden, not_found
from app.models.business import Candidate, Member, Negotiation
from app.schemas.commands import NegotiationStatus

DEFAULT_CONFIG: dict = {
    "lam": 0.25,
    "w_top": 1.0,
    "beta": {"p0": 0.5, "kappa": 2.0},
    "pl": {"sigma_v": 1.0},
    "quadrature_nodes": 15,
    "batch_size": 4,
    "scheduler": {
        "eta": 0.05,
        "epsilon": 0.05,
        "rho_explore": 0.2,
        "beta": 0.5,
        "top_challengers": 3,
    },
    "challenge": {
        "max_pool": 16,
        "time_limit_seconds": 0.5,
        "include_personal": True,
        "include_upper": True,
        "include_diverse": True,
        "random_seed": 0,
    },
    "solver": {
        "quantization_scale": 1000,
        "num_search_workers": 1,
        "random_seed": 0,
    },
}


def invite_token(negotiation_id: int) -> str:
    digest = hmac.new(
        settings.invite_token_secret.encode("utf-8"),
        f"invite:{negotiation_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_negotiation(db: Session, command) -> tuple[Negotiation, Member, str]:
    negotiation = Negotiation(
        title=command.title,
        status=NegotiationStatus.COLLECTING_BOTTOMLINE.value,
        exact_count=command.exact_count,
        budget_cents=command.budget_cents,
        round_limit=command.round_limit,
        round_no=0,
        algorithm_profile=command.algorithm_profile.value,
        config_json={
            **DEFAULT_CONFIG,
            "category_limits": dict(command.category_limits),
            "solver": {
                **DEFAULT_CONFIG["solver"],
                "time_limit_seconds": settings.solver_time_limit_seconds,
            },
        },
    )
    db.add(negotiation)
    db.flush()

    for item in command.candidates:
        db.add(
            Candidate(
                negotiation_id=negotiation.id,
                public_id=item.candidate_id,
                name=item.name,
                cost_cents=item.cost_cents,
                category=item.category,
                confirmed_attributes=dict(item.confirmed_attributes),
                metadata_version=1,
                active=True,
            )
        )

    token = generate_member_token()
    owner = Member(
        negotiation_id=negotiation.id,
        display_name=command.initiator_name,
        token_hash=hash_token(token),
        role="initiator",
        active=True,
        preference_version=1,
        bottomline_status="pending",
    )
    db.add(owner)
    db.flush()
    negotiation.owner_member_id = owner.id

    write_public_conditions(
        db,
        negotiation,
        budget_cents=command.budget_cents,
        exact_count=command.exact_count,
        category_limits=dict(command.category_limits),
        version=negotiation.constraint_version,
    )
    append_event(
        db,
        negotiation,
        "NEGOTIATION_CREATED",
        {
            "title": command.title,
            "candidate_count": len(command.candidates),
            "budget_cents": command.budget_cents,
            "exact_count": command.exact_count,
            "round_limit": command.round_limit,
        },
        actor_member_id=owner.id,
    )
    db.commit()
    return negotiation, owner, token


def join_negotiation(db: Session, negotiation_id: int, command) -> tuple[Member, str]:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    if not hmac.compare_digest(invite_token(negotiation.id), command.invite_token):
        raise forbidden("INVITE_INVALID", "邀请链接无效")
    if negotiation.status in {
        NegotiationStatus.FINALIZED.value,
        NegotiationStatus.CLOSED_WITH_CONCERN.value,
        NegotiationStatus.CLOSED_UNCONFIRMED.value,
    }:
        raise conflict("NEGOTIATION_CLOSED", "协商已结束，不能加入")

    active = db.scalar(
        select(func.count(Member.id)).where(
            Member.negotiation_id == negotiation.id, Member.active.is_(True)
        )
    ) or 0
    if active >= settings.max_members:
        raise conflict("MEMBER_LIMIT_REACHED", "参与人数已达上限")

    token = generate_member_token()
    mid_round = negotiation.status in ANSWER_OPEN_STATUSES
    member = Member(
        negotiation_id=negotiation.id,
        display_name=command.display_name,
        token_hash=hash_token(token),
        role="member",
        active=True,
        preference_version=1,
        bottomline_status="pending",
    )
    db.add(member)
    db.flush()
    if mid_round:
        # 成员集合变化必须重置稳定计数；本轮题包不重发，新成员从下一轮进入。
        negotiation.decision_config_version += 1
    append_event(
        db,
        negotiation,
        "MEMBER_JOINED",
        {
            "display_name": command.display_name,
            "mid_round": mid_round,
            "round_no": negotiation.round_no,
        },
        actor_member_id=member.id,
    )
    db.commit()
    return member, token
