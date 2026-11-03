"""事件与 outbox：RDS 先落事实，投影 worker 后写 Redis。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.business import Negotiation
from app.models.events import NegotiationEvent
from app.models.outbox import Outbox


def append_event(
    db: Session,
    negotiation: Negotiation,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    actor_member_id: int | None = None,
    request_id: str | None = None,
) -> NegotiationEvent:
    """每个事件推进一次 state_version，并写入同版本 outbox。"""
    negotiation.state_version += 1
    last_seq = db.scalar(
        select(func.max(NegotiationEvent.event_seq)).where(
            NegotiationEvent.negotiation_id == negotiation.id
        )
    )
    event = NegotiationEvent(
        negotiation_id=negotiation.id,
        event_seq=(last_seq or 0) + 1,
        type=event_type,
        actor_member_id=actor_member_id,
        payload=payload or {},
        request_id=request_id,
        state_version=negotiation.state_version,
        constraint_version=negotiation.constraint_version,
        decision_config_version=negotiation.decision_config_version,
    )
    db.add(event)
    db.flush()
    db.add(
        Outbox(
            negotiation_id=negotiation.id,
            state_version=negotiation.state_version,
            event_type=event_type,
            payload_ref=f"event:{event.id}",
            payload=payload or {},
            status="pending",
        )
    )
    return event
