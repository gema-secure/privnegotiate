"""业务事实表：RDS 是权威来源。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Negotiation(Base):
    __tablename__ = "negotiation"
    __table_args__ = (
        CheckConstraint("exact_count BETWEEN 1 AND 8", name="exact_count_range"),
        CheckConstraint("budget_cents >= 0", name="budget_non_negative"),
        CheckConstraint("round_limit >= 1", name="round_limit_positive"),
        CheckConstraint("round_no >= 0", name="round_no_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    exact_count: Mapped[int] = mapped_column(Integer, nullable=False)
    budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    round_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    constraint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decision_config_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    active_result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    algorithm_profile: Mapped[str] = mapped_column(
        String(32), nullable=False, default="mean_fair_v1"
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class Candidate(Base):
    __tablename__ = "candidate"
    __table_args__ = (
        UniqueConstraint(
            "negotiation_id", "public_id", name="uq_candidate_negotiation_public_id"
        ),
        CheckConstraint("cost_cents >= 0", name="cost_non_negative"),
        Index("ix_candidate_negotiation_active", "negotiation_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    public_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, default="unknown"
    )
    confirmed_attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    metadata_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class CandidateAttribute(Base):
    """菜品属性的三态事实：present/absent/unknown，未知不得当作 absent。"""

    __tablename__ = "candidate_attribute"
    __table_args__ = (
        UniqueConstraint("candidate_id", "attribute_key", name="uq_attribute_candidate_key"),
        Index("ix_attribute_negotiation_key", "negotiation_id", "attribute_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(
        ForeignKey("candidate.id"), nullable=False
    )
    attribute_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="manual")
    source_detail: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_by_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class CandidateAttributeRevision(Base):
    """属性修正留痕：每次人工修正写一条，保留修正前状态与原因。"""

    __tablename__ = "candidate_attribute_revision"
    __table_args__ = (
        Index("ix_attribute_revision_candidate", "candidate_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    attribute_id: Mapped[int] = mapped_column(
        ForeignKey("candidate_attribute.id"), nullable=False
    )
    candidate_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attribute_key: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    new_state: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_source: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Member(Base):
    __tablename__ = "member"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_member_token_hash"),
        Index("ix_member_negotiation_active", "negotiation_id", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preference_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    bottomline_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ConfirmedConstraint(Base):
    __tablename__ = "confirmed_constraint"
    __table_args__ = (
        UniqueConstraint(
            "negotiation_id",
            "constraint_id",
            "confirmed_version",
            name="uq_constraint_negotiation_version",
        ),
        Index(
            "ix_constraint_negotiation_owner_active",
            "negotiation_id",
            "owner_member_id",
            "active",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    constraint_id: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    source_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class QuestionBatch(Base):
    __tablename__ = "question_batch"
    __table_args__ = (
        UniqueConstraint(
            "negotiation_id", "batch_public_id", name="uq_batch_negotiation_public_id"
        ),
        UniqueConstraint(
            "negotiation_id",
            "round_no",
            "member_id",
            name="uq_batch_member_round",
        ),
        Index("ix_batch_member_status", "member_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    batch_public_id: Mapped[str] = mapped_column(String(40), nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), nullable=False)
    displayed_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="issued")
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class QuestionAnswer(Base):
    __tablename__ = "question_answer"
    __table_args__ = (
        UniqueConstraint("batch_id", "member_id", name="uq_answer_batch_member"),
        UniqueConstraint("member_id", "request_id", name="uq_answer_member_request"),
        Index("ix_answer_member_accepted", "member_id", "accepted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    batch_id: Mapped[int] = mapped_column(ForeignKey("question_batch.id"), nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    evaluated_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    acceptable_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    top_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RoundResult(Base):
    __tablename__ = "round_result"
    __table_args__ = (
        UniqueConstraint("negotiation_id", "round_no", name="uq_result_round"),
        UniqueConstraint(
            "negotiation_id", "result_version", name="uq_result_negotiation_version"
        ),
        CheckConstraint("total_cost_cents >= 0", name="total_cost_non_negative"),
        CheckConstraint("stable_count BETWEEN 0 AND 2", name="stable_count_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_event_cutoff: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    constraint_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    total_cost_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    solver_status: Mapped[str] = mapped_column(String(16), nullable=False)
    objective_quantized: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    objective_float: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    absolute_gap: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantization_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    tie_break_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    wall_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    stable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audit_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class ResultConfirmation(Base):
    __tablename__ = "result_confirmation"
    __table_args__ = (
        UniqueConstraint(
            "negotiation_id",
            "result_version",
            "member_id",
            name="uq_confirmation_member_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), nullable=False)
    decision_config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    accept: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Concern(Base):
    __tablename__ = "concern"
    __table_args__ = (
        Index("ix_concern_negotiation_status", "negotiation_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), nullable=False)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    share_notice_version: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class InitiatorDecision(Base):
    __tablename__ = "initiator_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    negotiation_id: Mapped[int] = mapped_column(
        ForeignKey("negotiation.id"), nullable=False
    )
    concern_id: Mapped[int | None] = mapped_column(
        ForeignKey("concern.id"), nullable=True
    )
    result_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    extra_rounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adjustments: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    decided_by_member_id: Mapped[int] = mapped_column(
        ForeignKey("member.id"), nullable=False
    )
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
