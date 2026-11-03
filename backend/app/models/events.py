"""事件表：有效历史与回放的唯一依据。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.models.business import utcnow


class NegotiationEvent(Base):
    __tablename__ = "negotiation_event"
    __table_args__ = (
        UniqueConstraint(
            "negotiation_id", "event_seq", name="uq_event_negotiation_seq"
        ),
        Index("ix_event_negotiation_type", "negotiation_id", "type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    actor_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("member.id"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    constraint_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
