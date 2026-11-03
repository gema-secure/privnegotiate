"""作答提交：统一锁顺序、request_id 幂等与封轮边界。"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.events import append_event
from app.engine.state import conflict, invalid, not_found
from app.models.business import Member, Negotiation, QuestionAnswer, QuestionBatch, utcnow


def payload_hash(command) -> str:
    canonical = json.dumps(
        {
            "mode": command.mode.value,
            "evaluated_ids": sorted(command.evaluated_ids),
            "acceptable_ids": sorted(command.acceptable_ids),
            "top_id": command.top_id,
            "reason": command.reason,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _existing(db: Session, member_id: int, request_id: str) -> QuestionAnswer | None:
    return db.scalars(
        select(QuestionAnswer).where(
            QuestionAnswer.member_id == member_id,
            QuestionAnswer.request_id == request_id,
        )
    ).one_or_none()


def submit_answer(
    db: Session, negotiation_id: int, member: Member, command
) -> tuple[QuestionAnswer, bool]:
    """返回 (回答, 是否幂等重放)；同轮多成员可依次成功。"""
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    batch = db.scalars(
        select(QuestionBatch)
        .where(
            QuestionBatch.negotiation_id == negotiation_id,
            QuestionBatch.batch_public_id == command.question_batch_id,
            QuestionBatch.member_id == member.id,
        )
        .with_for_update()
    ).one_or_none()
    if batch is None:
        raise conflict("BATCH_NOT_FOUND", "题包不存在或不属于本人")

    digest = payload_hash(command)
    existing = _existing(db, member.id, command.request_id)
    if existing is not None:
        if existing.payload_hash != digest:
            raise conflict("REQUEST_ID_REUSED", "同一 request_id 不能提交不同内容")
        return existing, True

    if negotiation.status not in {"ASKING", "ANSWERING", "RETRY_OR_REVIEW"}:
        raise conflict("ANSWER_WINDOW_CLOSED", "本轮已封轮或协商已结束")
    if batch.status != "issued":
        raise conflict("BATCH_CLOSED", "该题包已关闭，请刷新获取最新题包")

    displayed = set(batch.displayed_ids or [])
    unknown = set(command.evaluated_ids) - displayed
    if unknown:
        raise invalid("EVALUATED_NOT_DISPLAYED", "只能评价本包展示的候选项")

    existing_batch = db.scalars(
        select(QuestionAnswer).where(
            QuestionAnswer.batch_id == batch.id,
            QuestionAnswer.invalidated_at.is_(None),
        )
    ).one_or_none()
    if existing_batch is not None:
        raise conflict("BATCH_ALREADY_ANSWERED", "该题包已提交过回答")

    answer = QuestionAnswer(
        negotiation_id=negotiation_id,
        batch_id=batch.id,
        member_id=member.id,
        request_id=command.request_id,
        payload_hash=digest,
        mode=command.mode.value,
        evaluated_ids=list(command.evaluated_ids),
        acceptable_ids=list(command.acceptable_ids),
        top_id=command.top_id,
        reason=command.reason,
        accepted_at=utcnow(),
    )
    db.add(answer)
    batch.status = "answered"
    if negotiation.status == "ASKING":
        negotiation.status = "ANSWERING"
    append_event(
        db,
        negotiation,
        "ANSWER_SUBMITTED",
        {
            "batch_id": batch.batch_public_id,
            "round_no": batch.round_no,
            "mode": command.mode.value,
            "request_id": command.request_id,
        },
        actor_member_id=member.id,
        request_id=command.request_id,
    )
    db.flush()
    return answer, False
