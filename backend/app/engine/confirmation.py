"""私下确认、顾虑转交与发起者继续/停止。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.constraints_service import (
    invalidate_confirmations,
    write_public_conditions,
)
from app.engine.events import append_event
from app.engine.feasibility import start_round
from app.engine.state import conflict, forbidden, invalid, not_found
from app.models.business import (
    Concern,
    InitiatorDecision,
    Member,
    Negotiation,
    ResultConfirmation,
)

CONFIRM_STATUSES = {"PRIVATE_CONFIRM", "BEST_EFFORT"}


def submit_confirmation(
    db: Session, negotiation_id: int, member: Member, result_version: int, command
) -> ResultConfirmation:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    if negotiation.status not in CONFIRM_STATUSES:
        raise conflict("NOT_IN_CONFIRMATION", f"当前状态 {negotiation.status} 不接受确认")
    if result_version != negotiation.active_result_version:
        raise conflict("RESULT_VERSION_STALE", "该结果版本已不是最新")
    if command.decision_config_version != negotiation.decision_config_version:
        raise conflict("RULE_VERSION_STALE", "决策规则已变化，请刷新后重新确认")

    existing = db.scalars(
        select(ResultConfirmation).where(
            ResultConfirmation.negotiation_id == negotiation.id,
            ResultConfirmation.result_version == result_version,
            ResultConfirmation.member_id == member.id,
        )
    ).one_or_none()
    if existing is not None:
        if existing.accept == command.accept:
            return existing
        raise conflict("CONFIRMATION_FINAL", "确认已提交，不能在同一版本内更改")

    confirmation = ResultConfirmation(
        negotiation_id=negotiation.id,
        result_version=result_version,
        member_id=member.id,
        decision_config_version=negotiation.decision_config_version,
        accept=command.accept,
        reason=command.reason,
    )
    db.add(confirmation)
    db.flush()
    event = append_event(
        db,
        negotiation,
        "CONFIRMATION_SUBMITTED",
        {"result_version": result_version, "accept": command.accept},
        actor_member_id=member.id,
    )
    confirmation.event_id = event.id

    rows = db.scalars(
        select(ResultConfirmation).where(
            ResultConfirmation.negotiation_id == negotiation.id,
            ResultConfirmation.result_version == result_version,
            ResultConfirmation.decision_config_version
            == negotiation.decision_config_version,
        )
    ).all()
    member_total = db.scalar(
        select(func.count(Member.id)).where(
            Member.negotiation_id == negotiation.id, Member.active.is_(True)
        )
    ) or 0
    if any(not row.accept for row in rows):
        negotiation.status = "CONCERN_COLLECTING"
        append_event(
            db,
            negotiation,
            "CONCERN_WINDOW_OPENED",
            {"result_version": result_version},
            actor_member_id=member.id,
        )
    elif len({row.member_id for row in rows if row.accept}) >= member_total:
        negotiation.status = "FINALIZED"
        append_event(
            db,
            negotiation,
            "NEGOTIATION_FINALIZED",
            {"result_version": result_version},
            actor_member_id=member.id,
        )
    db.commit()
    return confirmation


def submit_concern(
    db: Session,
    negotiation_id: int,
    member: Member,
    command,
    summary: str | None = None,
    summary_source: str | None = None,
) -> Concern:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    if negotiation.status != "CONCERN_COLLECTING":
        raise conflict("CONCERN_WINDOW_CLOSED", "当前不在顾虑收集阶段")
    if command.result_version != negotiation.active_result_version:
        raise conflict("RESULT_VERSION_STALE", "该结果版本已不是最新")

    concern = Concern(
        negotiation_id=negotiation.id,
        member_id=member.id,
        result_version=command.result_version,
        original_text=command.text,
        summary=summary,
        summary_source=summary_source,
        share_notice_version="v1",
        status="open",
    )
    db.add(concern)
    db.flush()
    append_event(
        db,
        negotiation,
        "CONCERN_SUBMITTED",
        {"concern_id": concern.id, "result_version": command.result_version},
        actor_member_id=member.id,
    )
    db.commit()
    return concern


def correct_concern_summary(
    db: Session, negotiation_id: int, concern_id: int, member: Member, summary: str
) -> Concern:
    """发起者人工修正摘要；原文保持不变，修正后仍可继续或停止。"""
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    if member.role != "initiator":
        raise forbidden("INITIATOR_ONLY", "只有发起者可以修正顾虑摘要")
    concern = db.get(Concern, concern_id)
    if concern is None or concern.negotiation_id != negotiation.id:
        raise not_found("CONCERN_NOT_FOUND", "顾虑不存在")
    concern.summary = summary
    concern.summary_source = "manual"
    append_event(
        db,
        negotiation,
        "CONCERN_SUMMARY_CORRECTED",
        {"concern_id": concern.id},
        actor_member_id=member.id,
    )
    db.commit()
    return concern


def list_concerns(
    db: Session, negotiation_id: int, member: Member
) -> list[Concern]:
    negotiation = db.get(Negotiation, negotiation_id)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    if member.role != "initiator":
        raise forbidden("INITIATOR_ONLY", "只有发起者可以读取顾虑")
    return list(
        db.scalars(
            select(Concern)
            .where(Concern.negotiation_id == negotiation_id)
            .order_by(Concern.id)
        ).all()
    )


def organizer_decision(
    db: Session, negotiation_id: int, member: Member, command
) -> dict:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    if member.role != "initiator":
        raise forbidden("INITIATOR_ONLY", "只有发起者可以执行该操作")
    if negotiation.status not in {
        "CONCERN_COLLECTING",
        "PRIVATE_CONFIRM",
        "BEST_EFFORT",
        "INFEASIBLE",
        "RETRY_OR_REVIEW",
        "ADJUSTING",
    }:
        raise conflict("DECISION_NOT_ALLOWED", f"当前状态 {negotiation.status} 不接受发起者决策")

    concern = None
    if command.concern_id is not None:
        concern = db.get(Concern, command.concern_id)
        if concern is None or concern.negotiation_id != negotiation.id:
            raise not_found("CONCERN_NOT_FOUND", "顾虑不存在")

    if command.action.value == "continue":
        if negotiation.status in {"INFEASIBLE", "RETRY_OR_REVIEW"} and command.public_adjustment is None:
            raise invalid(
                "PUBLIC_ADJUSTMENT_REQUIRED",
                "无解或待复查时继续协商必须调整公共条件",
            )
        adjusted = command.public_adjustment is not None
        _apply_public_adjustment(db, negotiation, command)
        invalidate_confirmations(db, negotiation, reason="initiator_continue")
        started = start_round(db, negotiation, actor_member_id=member.id)
        if started.reason == "no_questions" and not adjusted:
            # 只追加轮数、又没有可问的题：明确失败，不留下“已继续”的假成功。
            raise conflict(
                "NO_QUESTIONS_TO_ASK",
                "所有可选候选都已被成员评价，追加轮数不会产生新的可作答题；"
                "请调整公共条件后再继续。",
            )
        applied_extra_rounds = command.extra_rounds if started.round_issued else None
        if applied_extra_rounds:
            negotiation.round_limit += applied_extra_rounds
        if concern is not None:
            concern.status = "resolved"
        else:
            for row in db.scalars(
                select(Concern).where(
                    Concern.negotiation_id == negotiation.id,
                    Concern.status == "open",
                )
            ).all():
                row.status = "resolved"
        decision = InitiatorDecision(
            negotiation_id=negotiation.id,
            concern_id=concern.id if concern else None,
            result_version=negotiation.active_result_version,
            action="continue",
            extra_rounds=applied_extra_rounds,
            adjustments=(
                command.public_adjustment.model_dump(exclude_none=True)
                if command.public_adjustment
                else {}
            ),
            decided_by_member_id=member.id,
        )
        db.add(decision)
        db.flush()
        event = append_event(
            db,
            negotiation,
            "INITIATOR_DECISION",
            {
                "action": "continue",
                "requested_extra_rounds": command.extra_rounds,
                "applied_extra_rounds": applied_extra_rounds,
                "adjusted": adjusted,
                "round_issued": started.round_issued,
                "reason": started.reason,
                "round_no": negotiation.round_no,
            },
            actor_member_id=member.id,
        )
        decision.event_id = event.id
        db.commit()
        return {
            "negotiation_status": negotiation.status,
            "action": "continue",
            "concern_id": decision.concern_id,
            "round_no": negotiation.round_no,
            "round_issued": started.round_issued,
            "outcome": _continue_outcome(started),
            "message": started.message,
        }

    open_concerns = list(
        db.scalars(
            select(Concern).where(
                Concern.negotiation_id == negotiation.id, Concern.status == "open"
            )
        ).all()
    )
    for row in open_concerns:
        row.status = "closed"
    negotiation.status = "CLOSED_WITH_CONCERN" if open_concerns else "CLOSED_UNCONFIRMED"

    decision = InitiatorDecision(
        negotiation_id=negotiation.id,
        concern_id=concern.id if concern else None,
        result_version=negotiation.active_result_version,
        action="stop",
        decided_by_member_id=member.id,
    )
    db.add(decision)
    db.flush()
    event = append_event(
        db,
        negotiation,
        "INITIATOR_DECISION",
        {"action": "stop", "status": negotiation.status},
        actor_member_id=member.id,
    )
    decision.event_id = event.id
    db.commit()
    return {
        "negotiation_status": negotiation.status,
        "action": "stop",
        "concern_id": decision.concern_id,
        "round_no": negotiation.round_no,
        "round_issued": False,
        "outcome": "stopped",
        "message": "协商已停止",
    }


def _continue_outcome(started) -> str:
    if started.round_issued:
        return "round_issued"
    if started.reason == "no_questions":
        return "no_new_questions"
    if started.reason == "infeasible":
        return "infeasible"
    return "retry"


def _apply_public_adjustment(db: Session, negotiation: Negotiation, command) -> None:
    """只落公共条件；追加轮数在确认确实发出新轮后再加。"""
    adjustment = command.public_adjustment
    if adjustment is None:
        return

    if adjustment.budget_cents is not None:
        negotiation.budget_cents = adjustment.budget_cents
    if adjustment.exact_count is not None:
        negotiation.exact_count = adjustment.exact_count
    config = dict(negotiation.config_json or {})
    limits = dict(config.get("category_limits") or {})
    if adjustment.category_limits is not None:
        limits.update(adjustment.category_limits)
    config["category_limits"] = limits
    negotiation.config_json = config
    negotiation.constraint_version += 1
    negotiation.decision_config_version += 1
    write_public_conditions(
        db,
        negotiation,
        budget_cents=negotiation.budget_cents,
        exact_count=negotiation.exact_count,
        category_limits=limits,
        version=negotiation.constraint_version,
    )
