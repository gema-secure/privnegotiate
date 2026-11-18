from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


from app.models.business import (  # noqa: E402
    Candidate,
    CandidateAttribute,
    CandidateAttributeRevision,
    Concern,
    ConfirmedConstraint,
    InitiatorDecision,
    Member,
    Negotiation,
    QuestionAnswer,
    QuestionBatch,
    ResultConfirmation,
    RoundResult,
    RemovalRequest,
)
from app.models.events import NegotiationEvent  # noqa: E402
from app.models.outbox import Outbox  # noqa: E402

__all__ = [
    "Base",
    "Candidate",
    "CandidateAttribute",
    "CandidateAttributeRevision",
    "Concern",
    "ConfirmedConstraint",
    "InitiatorDecision",
    "Member",
    "Negotiation",
    "NegotiationEvent",
    "Outbox",
    "QuestionAnswer",
    "QuestionBatch",
    "ResultConfirmation",
    "RoundResult",
    "RemovalRequest",
]
