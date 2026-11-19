"""成员治理。协商行锁保护请求、授权、成员集合与结果的一次性提交。"""
from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.constraints_service import proposer_member_id
from app.engine.events import append_event
from app.engine.participation import require_participant
from app.engine.state import TERMINAL_STATUSES, conflict, forbidden, not_found, utc_aware
from app.models.business import (
    ConfirmedConstraint, Member, Negotiation, QuestionAnswer, QuestionBatch,
    RemovalRequest, ResultConfirmation, utcnow,
)
from app.schemas.membership import (
    MembershipView, RemovalView, RetainedConstraintsView, RosterMemberView,
)
from app.schemas.private_views import MyConstraintView


def lock_negotiation(db: Session, negotiation_id: int) -> Negotiation:
    negotiation = db.get(Negotiation, negotiation_id, with_for_update=True, populate_existing=True)
    if negotiation is None:
        raise not_found("NEGOTIATION_NOT_FOUND", "协商不存在")
    return negotiation


def _mutable(negotiation: Negotiation, version: int) -> None:
    if negotiation.status in TERMINAL_STATUSES:
        raise conflict("NEGOTIATION_CLOSED", "协商已结束，不能变更成员或授权")
    if version != negotiation.membership_version:
        raise conflict("MEMBERSHIP_VERSION_STALE", "参与成员已变化，请刷新后重新授权")


def _participants(db: Session, negotiation_id: int) -> list[Member]:
    return list(db.scalars(select(Member).where(
        Member.negotiation_id == negotiation_id, Member.active.is_(True)
    ).order_by(Member.id).with_for_update().execution_options(populate_existing=True)).all())


def expire_removals(db: Session, negotiation: Negotiation, *, reason: str) -> None:
    for request in db.scalars(select(RemovalRequest).where(
        RemovalRequest.negotiation_id == negotiation.id, RemovalRequest.status == "pending"
    )).all():
        request.status = "expired"
        target = db.get(Member, request.target_member_id)
        if target.active:
            target.membership_status = "ACTIVE"
        append_event(db, negotiation, "REMOVAL_EXPIRED", {
            "request_id": request.id, "reason": reason,
            "membership_version": negotiation.membership_version,
        })


def invalidate_membership_inputs(db: Session, negotiation: Negotiation) -> None:
    # 确认和答案作为历史保留；以版本判定有效性，不删除原始异议。
    negotiation.active_result_version = None
    for batch in db.scalars(select(QuestionBatch).where(
        QuestionBatch.negotiation_id == negotiation.id,
        QuestionBatch.status.in_(["issued", "closed"]),
    )).all():
        batch.status = "invalidated"


def _reconcile(db: Session, negotiation: Negotiation, actor_id: int, *, owner_left=False) -> None:
    participants = _participants(db, negotiation.id)
    if owner_left or len(participants) < 3:
        negotiation.status = "CLOSED_UNCONFIRMED"
        append_event(db, negotiation, "MEMBERSHIP_CLOSED", {
            "reason": "initiator_left" if owner_left else "below_supported_member_count",
            "participating_count": len(participants),
        }, actor_member_id=actor_id)
        return
    if any(member.bottomline_status == "pending" for member in participants):
        negotiation.status = "COLLECTING_BOTTOMLINE"
        return
    if not negotiation.membership_frozen:
        # 尚未开轮，维持底线收集；下一次本人提交触发原有可行性检查。
        negotiation.status = "COLLECTING_BOTTOMLINE"
        return
    from app.engine.closing import begin_round_close, run_round_computation, commit_round

    # Persist invalidation and frozen inputs before releasing all row locks.
    negotiation.round_no += 1
    negotiation.status = "ASKING"
    actor = db.get(Member, negotiation.owner_member_id)
    db.flush()
    snapshot = begin_round_close(db, negotiation, actor)
    computation = replace(run_round_computation(snapshot), stable_count=0)
    commit_round(db, snapshot, computation)


def _depart(db: Session, negotiation: Negotiation, target: Member, actor: Member, status: str) -> None:
    before = target.membership_status
    previous_result = negotiation.active_result_version
    active_constraints = db.scalars(select(ConfirmedConstraint).where(
        ConfirmedConstraint.negotiation_id == negotiation.id,
        ConfirmedConstraint.active.is_(True),
    )).all()
    owned = [row for row in active_constraints if proposer_member_id(row) == target.id]
    target.membership_status = status
    target.active = False
    # 整组禁选保留。个人限制仅退出当前有效集合，原 payload 留存审计。
    for constraint in active_constraints:
        if (constraint.owner_member_id == target.id
                and constraint.kind in ("opt_out", "require_count")):
            constraint.active = False
    negotiation.membership_version += 1
    negotiation.decision_config_version += 1
    negotiation.constraint_version += 1
    invalidate_membership_inputs(db, negotiation)
    db.flush()
    expire_removals(db, negotiation, reason="membership_changed")
    append_event(db, negotiation, "MEMBER_" + status, {
        "target_member_id": target.id, "before": before, "after": status,
        "membership_version": negotiation.membership_version,
        "round_no": negotiation.round_no,
        "credential_scope": "retained_constraints_only",
        "previous_result_version": previous_result,
        "constraint_refs": [r.constraint_id for r in owned],
        "constraint_version": negotiation.constraint_version,
    }, actor_member_id=actor.id)
    db.flush()
    _reconcile(db, negotiation, actor.id, owner_left=target.id == negotiation.owner_member_id)


def leave(db: Session, negotiation_id: int, actor: Member, command) -> None:
    negotiation = lock_negotiation(db, negotiation_id)
    actor = require_participant(db, negotiation, actor)
    _mutable(negotiation, command.expected_membership_version)
    _depart(db, negotiation, actor, actor, "LEFT")
    db.commit()


def request_removal(db: Session, negotiation_id: int, actor: Member, target_id: int, command) -> RemovalRequest:
    negotiation = lock_negotiation(db, negotiation_id)
    actor = require_participant(db, negotiation, actor)
    _mutable(negotiation, command.expected_membership_version)
    if actor.id != negotiation.owner_member_id:
        raise forbidden("INITIATOR_ONLY", "只有发起者可以提出移除请求")
    target = db.get(Member, target_id, with_for_update=True, populate_existing=True)
    if target is None or target.negotiation_id != negotiation.id or not target.active:
        raise conflict("REMOVAL_NOT_ALLOWED", "该成员不可移除")
    if target.id == actor.id:
        raise forbidden("SELF_REMOVAL_NOT_ALLOWED", "本人退出请使用主动离开")
    existing = db.scalars(select(RemovalRequest).where(
        RemovalRequest.negotiation_id == negotiation.id,
        RemovalRequest.target_member_id == target.id, RemovalRequest.status == "pending",
    )).first()
    if existing:
        if existing.reason != command.reason:
            raise conflict("REMOVAL_ALREADY_PENDING", "已有待处理的移除请求")
        return existing
    # 单个发起者不能构成强制移除授权；不允许同时架空两名待移除成员的投票权。
    if db.scalars(select(RemovalRequest).where(
        RemovalRequest.negotiation_id == negotiation.id, RemovalRequest.status == "pending"
    )).first():
        raise conflict("REMOVAL_ALREADY_PENDING", "请先处理当前移除请求")
    eligible = [m.id for m in _participants(db, negotiation.id) if m.id != target.id]
    if len(eligible) < 2:
        raise conflict("REMOVAL_QUORUM_REQUIRED", "至少需要两名其余参与者逐人授权")
    request = RemovalRequest(
        negotiation_id=negotiation.id, target_member_id=target.id, requested_by=actor.id,
        membership_version=negotiation.membership_version, eligible_member_ids=eligible,
        votes={}, reason=command.reason,
    )
    db.add(request)
    target.membership_status = "REMOVAL_REQUESTED"
    db.flush()
    append_event(db, negotiation, "REMOVAL_REQUESTED", {
        "request_id": request.id, "target_member_id": target.id,
        "before": "ACTIVE", "after": "REMOVAL_REQUESTED", "reason": command.reason,
        "membership_version": negotiation.membership_version,
        "result_version": negotiation.active_result_version,
        "decision_config_version": negotiation.decision_config_version,
    }, actor_member_id=actor.id)
    db.commit()
    return request


def resolve_removal(db: Session, negotiation_id: int, actor: Member, request_id: int, command, *, cancel=False) -> RemovalRequest:
    negotiation = lock_negotiation(db, negotiation_id)
    actor = require_participant(db, negotiation, actor)
    _mutable(negotiation, command.expected_membership_version)
    request = db.get(RemovalRequest, request_id, populate_existing=True)
    if request is None or request.negotiation_id != negotiation.id:
        raise not_found("REMOVAL_NOT_FOUND", "移除请求不存在")
    if request.status != "pending" or request.membership_version != negotiation.membership_version:
        raise conflict("REMOVAL_AUTHORIZATION_EXPIRED", "本次移除授权已结束或失效")
    target = db.get(Member, request.target_member_id)
    if cancel:
        if actor.id != request.requested_by:
            raise forbidden("INITIATOR_ONLY", "只有请求发起者可以撤销")
        request.status = "cancelled"
    else:
        if actor.id not in request.eligible_member_ids:
            raise forbidden("REMOVAL_VOTER_REQUIRED", "只能由其余当前参与者逐人授权")
        votes = dict(request.votes)
        key = str(actor.id)
        if key in votes:
            if votes[key] == command.approve:
                return request
            raise conflict("REMOVAL_VOTE_FINAL", "本次授权已提交，不能更改")
        votes[key] = command.approve
        request.votes = votes
        append_event(db, negotiation, "REMOVAL_AUTHORIZED", {
            "request_id": request.id, "approve": command.approve,
            "membership_version": negotiation.membership_version,
        }, actor_member_id=actor.id)
        if not command.approve:
            request.status = "rejected"
        elif all(votes.get(str(mid)) is True for mid in request.eligible_member_ids):
            request.status = "effective"
            db.flush()
            _depart(db, negotiation, target, actor, "REMOVED")
    if request.status in {"cancelled", "rejected"}:
        target.membership_status = "ACTIVE"
        append_event(db, negotiation, "REMOVAL_RESOLVED", {
            "request_id": request.id, "status": request.status,
        }, actor_member_id=actor.id)
    db.commit()
    return request


def membership_view(db: Session, negotiation: Negotiation, actor: Member) -> MembershipView:
    requests = db.scalars(select(RemovalRequest).where(
        RemovalRequest.negotiation_id == negotiation.id
    ).order_by(RemovalRequest.id)).all()
    return MembershipView(
        membership_version=negotiation.membership_version,
        membership_status=actor.membership_status, response_status=actor.response_status,
        members=[RosterMemberView(member_id=m.id, display_name=m.display_name, role=m.role)
                 for m in _participants(db, negotiation.id)],
        removals=[RemovalView(
            request_id=r.id, target_member_id=r.target_member_id,
            membership_version=r.membership_version, status=r.status,
            my_vote=r.votes.get(str(actor.id)),
            can_vote=(r.status == "pending" and actor.id in r.eligible_member_ids
                      and negotiation.status not in TERMINAL_STATUSES),
        ) for r in requests],
    )


def mark_unresponsive(db: Session, negotiation_id: int, actor: Member, target_id: int, command) -> None:
    negotiation = lock_negotiation(db, negotiation_id)
    actor = require_participant(db, negotiation, actor)
    _mutable(negotiation, command.expected_membership_version)
    if actor.id != negotiation.owner_member_id:
        raise forbidden("INITIATOR_ONLY", "只有发起者可以提出失联标记")
    target = db.get(Member, target_id, with_for_update=True, populate_existing=True)
    if target is None or target.negotiation_id != negotiation.id or not target.active:
        raise conflict("MEMBER_NOT_PARTICIPATING", "该成员不在当前参与集合")
    batch = db.scalars(select(QuestionBatch).where(
        QuestionBatch.negotiation_id == negotiation.id, QuestionBatch.member_id == target.id,
        QuestionBatch.round_no == negotiation.round_no,
        QuestionBatch.status.in_(["issued", "closed"]),
    )).first()
    if batch is None or utc_aware(batch.deadline) > utcnow() or db.scalar(select(QuestionAnswer.id).where(
        QuestionAnswer.batch_id == batch.id, QuestionAnswer.invalidated_at.is_(None)
    )):
        raise conflict("UNRESPONSIVE_THRESHOLD_NOT_MET", "须当前题包已过明示截止时间且尚未作答")
    if target.response_status != "UNRESPONSIVE":
        target.response_status = "UNRESPONSIVE"
        append_event(db, negotiation, "MEMBER_UNRESPONSIVE", {
            "target_member_id": target.id, "threshold": "current_batch_deadline",
        }, actor_member_id=actor.id)
    db.commit()


def return_to_active(db: Session, negotiation_id: int, actor: Member) -> None:
    negotiation = lock_negotiation(db, negotiation_id)
    actor = require_participant(db, negotiation, actor)
    if negotiation.status in TERMINAL_STATUSES:
        raise conflict("NEGOTIATION_CLOSED", "协商已结束")
    if actor.response_status == "UNRESPONSIVE":
        actor.response_status = "ACTIVE"
        append_event(db, negotiation, "MEMBER_RETURNED", {}, actor_member_id=actor.id)
    db.commit()


def retained_view(db: Session, actor: Member) -> RetainedConstraintsView:
    rows = db.scalars(select(ConfirmedConstraint).where(
        ConfirmedConstraint.negotiation_id == actor.negotiation_id,
        ConfirmedConstraint.active.is_(True),
        ConfirmedConstraint.kind == "exclude_item",
    )).all()
    return RetainedConstraintsView(
        membership_status=actor.membership_status, preference_version=actor.preference_version,
        constraints=[MyConstraintView(constraint_id=r.constraint_id, kind=r.kind,
            candidate_ids=list(r.payload.get("candidate_ids", [])),
            confirmed_version=r.confirmed_version)
            for r in rows if proposer_member_id(r) == actor.id],
    )


def withdraw_retained(db: Session, negotiation_id: int, actor: Member, constraint_id: str, command) -> None:
    negotiation = lock_negotiation(db, negotiation_id)
    if negotiation.status in TERMINAL_STATUSES:
        raise conflict("NEGOTIATION_CLOSED", "协商已结束，历史结果不能修改")
    if actor.preference_version != command.expected_preference_version:
        raise conflict("PREFERENCE_VERSION_CONFLICT", "本人保留禁忌已变化，请刷新")
    rows = db.scalars(select(ConfirmedConstraint).where(
        ConfirmedConstraint.negotiation_id == negotiation.id,
        ConfirmedConstraint.constraint_id == constraint_id,
        ConfirmedConstraint.kind == "exclude_item", ConfirmedConstraint.active.is_(True),
    )).all()
    row = next((item for item in rows if proposer_member_id(item) == actor.id), None)
    if row is None:
        raise not_found("CONSTRAINT_NOT_FOUND", "本人保留的整组禁忌不存在")
    row.active = False
    actor.preference_version += 1
    negotiation.constraint_version += 1
    negotiation.decision_config_version += 1
    invalidate_membership_inputs(db, negotiation)
    append_event(db, negotiation, "RETAINED_CONSTRAINT_WITHDRAWN", {
        "constraint_id": constraint_id, "preference_version": actor.preference_version,
    }, actor_member_id=actor.id)
    db.flush()
    _reconcile(db, negotiation, actor.id)
    db.commit()
