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
from app.engine.state import conflict, forbidden, invalid, not_found
from app.models.business import Candidate, CandidateAttribute, Member, Negotiation
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
    member_count = int(getattr(command, "member_count", settings.min_members))
    if member_count < settings.min_members:
        raise invalid("MEMBER_COUNT_TOO_SMALL", "小组至少需要 3 人")
    if member_count > settings.max_members:
        raise invalid(
            "MEMBER_COUNT_TOO_LARGE",
            f"小组成员不能超过 {settings.max_members} 人",
        )
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
            "member_count": member_count,
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
        candidate = Candidate(
            negotiation_id=negotiation.id,
            public_id=item.candidate_id,
            name=item.name,
            cost_cents=item.cost_cents,
            category=item.category,
            confirmed_attributes=dict(item.confirmed_attributes),
            metadata_version=1,
            active=True,
        )
        db.add(candidate)
        db.flush()
        for attribute in getattr(item, "attributes", []):
            db.add(
                CandidateAttribute(
                    negotiation_id=negotiation.id,
                    candidate_id=candidate.id,
                    attribute_key=attribute.key,
                    state=attribute.state.value,
                    source=attribute.source.value,
                    source_detail=attribute.source_detail,
                    updated_by_member_id=None,
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
            "member_count": member_count,
        },
        actor_member_id=owner.id,
    )
    db.commit()
    return negotiation, owner, token


def join_negotiation(db: Session, negotiation_id: int, command) -> tuple[Member, str]:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True, populate_existing=True)
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

    if (negotiation.membership_frozen or negotiation.round_no > 0
            or negotiation.status not in {"CREATED", "COLLECTING_BOTTOMLINE", "INFEASIBLE", "ADJUSTING", "RETRY_OR_REVIEW"}):
        raise conflict("MEMBERSHIP_FROZEN", "首次开轮后不能新增或替换成员")

    active = db.scalar(
        select(func.count(Member.id)).where(
            Member.negotiation_id == negotiation.id, Member.active.is_(True)
        )
    ) or 0
    if active >= settings.max_members:
        raise conflict("MEMBER_LIMIT_REACHED", "参与人数已达上限")

    token = generate_member_token()
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
    negotiation.membership_version += 1
    negotiation.decision_config_version += 1
    from app.engine.membership import expire_removals
    expire_removals(db, negotiation, reason="membership_changed")
    append_event(
        db,
        negotiation,
        "MEMBER_JOINED",
        {
            "display_name": command.display_name,
            "mid_round": False,
            "round_no": negotiation.round_no,
        },
        actor_member_id=member.id,
    )
    db.commit()
    return member, token
