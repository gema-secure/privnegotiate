"""公共条件与成员底线的写入；全量替换，历史保留。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constraints.dsl import ConstraintKind, ConstraintScope, DslError, validate_constraint
from app.constraints.dsl import ConfirmedConstraint as DslConstraint
from app.engine.state import conflict, invalid
from app.models.business import (
    ConfirmedConstraint,
    Member,
    Negotiation,
    QuestionAnswer,
    ResultConfirmation,
)

PUBLIC_BUDGET_ID = "pub_budget"
PUBLIC_EXACT_ID = "pub_exact"


def _deactivate(db: Session, negotiation_id: int, *, owner_member_id: int | None) -> None:
    stmt = select(ConfirmedConstraint).where(
        ConfirmedConstraint.negotiation_id == negotiation_id,
        ConfirmedConstraint.active.is_(True),
    )
    if owner_member_id is not None:
        stmt = stmt.where(ConfirmedConstraint.owner_member_id == owner_member_id)
    else:
        stmt = stmt.where(ConfirmedConstraint.owner_member_id.is_(None))
    for row in db.scalars(stmt).all():
        row.active = False


def _insert(
    db: Session,
    negotiation: Negotiation,
    *,
    constraint_id: str,
    kind: ConstraintKind,
    scope: ConstraintScope,
    owner_member_id: int | None,
    payload: dict,
    version: int,
) -> None:
    constraint = DslConstraint(
        constraint_id=constraint_id,
        kind=kind,
        scope=scope,
        # DSL 的 public 表示作用范围；数据库另保留提出者用于本人撤回权限。
        owner_member_id=None if kind is ConstraintKind.EXCLUDE_ITEM else owner_member_id,
        confirmed_version=version,
        **payload,
    )
    try:
        validate_constraint(constraint)
    except DslError as exc:
        raise invalid("CONSTRAINT_INVALID", str(exc)) from exc
    db.add(
        ConfirmedConstraint(
            negotiation_id=negotiation.id,
            constraint_id=constraint_id,
            owner_member_id=owner_member_id,
            scope=scope.value,
            kind=kind.value,
            payload=payload,
            confirmed_version=version,
            active=True,
        )
    )


def write_public_conditions(
    db: Session,
    negotiation: Negotiation,
    *,
    budget_cents: int,
    exact_count: int,
    category_limits: dict[str, int],
    version: int,
) -> None:
    _deactivate(db, negotiation.id, owner_member_id=None)
    _insert(
        db,
        negotiation,
        constraint_id=PUBLIC_BUDGET_ID,
        kind=ConstraintKind.BUDGET,
        scope=ConstraintScope.PUBLIC,
        owner_member_id=None,
        payload={"amount_cents": budget_cents},
        version=version,
    )
    _insert(
        db,
        negotiation,
        constraint_id=PUBLIC_EXACT_ID,
        kind=ConstraintKind.EXACT_COUNT,
        scope=ConstraintScope.PUBLIC,
        owner_member_id=None,
        payload={"min_count": exact_count},
        version=version,
    )
    for index, (category, limit) in enumerate(sorted(category_limits.items())):
        _insert(
            db,
            negotiation,
            constraint_id=f"pub_cat_{index}",
            kind=ConstraintKind.CATEGORY_COUNT,
            scope=ConstraintScope.PUBLIC,
            owner_member_id=None,
            payload={"category": category, "min_count": None, "max_count": limit},
            version=version,
        )


def confirm_member_constraints(
    db: Session, negotiation: Negotiation, member: Member, command
) -> list[str]:
    """全量替换本人有效底线；未提交的旧底线视为撤销。"""
    if (
        command.expected_preference_version is not None
        and command.expected_preference_version != member.preference_version
    ):
        raise conflict(
            "PREFERENCE_VERSION_CONFLICT",
            "偏好在其他页面已更新，请刷新后重试",
        )
    if not command.constraints and not command.no_extra_bottomline:
        raise invalid(
            "BOTTOMLINE_REQUIRED",
            "请提交底线，或明确选择本次无额外底线",
        )

    next_version = negotiation.constraint_version + 1
    _deactivate(db, negotiation.id, owner_member_id=member.id)

    seen: set[str] = set()
    written: list[str] = []
    payloads: list[dict] = []
    for index, draft in enumerate(command.constraints):
        constraint_id = draft.constraint_id or f"m{member.id}_{draft.kind.value}_{index}"
        if constraint_id in seen:
            raise invalid("CONSTRAINT_ID_DUPLICATE", "constraint_id 不能重复")
        seen.add(constraint_id)
        collision = db.scalars(select(ConfirmedConstraint).where(
            ConfirmedConstraint.negotiation_id == negotiation.id,
            ConfirmedConstraint.constraint_id == constraint_id,
        )).first()
        if collision is not None and collision.owner_member_id != member.id:
            raise invalid("CONSTRAINT_ID_INVALID", "请使用本人底线标识")
        payload: dict = {}
        if draft.kind in (
            ConstraintKind.EXCLUDE_ITEM,
            ConstraintKind.OPT_OUT,
        ):
            payload["candidate_ids"] = list(draft.candidate_ids)
        else:
            payload = {
                "category": draft.category,
                "attributes": dict(draft.attributes),
                "min_count": draft.min_count,
            }
        _insert(
            db,
            negotiation,
            constraint_id=constraint_id,
            kind=ConstraintKind(draft.kind.value),
            scope=(ConstraintScope.PUBLIC if draft.kind.value == "exclude_item" else ConstraintScope.MEMBER),
            owner_member_id=member.id,
            payload=payload,
            version=next_version,
        )
        written.append(constraint_id)
        payloads.append({"constraint_id": constraint_id, "kind": draft.kind.value})

    member.bottomline_status = "declared" if written else "no_extra"
    member.preference_version += 1
    negotiation.constraint_version = next_version
    negotiation.decision_config_version += 1
    invalidate_confirmations(db, negotiation, reason="bottomline_confirmed")
    return written


def invalidate_confirmations(
    db: Session, negotiation: Negotiation, *, reason: str
) -> None:
    """实质变化后旧确认全部失效；事件保留历史。"""
    rows = db.scalars(
        select(ResultConfirmation).where(
            ResultConfirmation.negotiation_id == negotiation.id
        )
    ).all()
    # 不删除确认历史；有效性由 decision_config_version 与 result_version 决定。
    rows = [row for row in rows if row.decision_config_version != negotiation.decision_config_version]
    if rows:
        from app.engine.events import append_event

        append_event(
            db,
            negotiation,
            "CONFIRMATIONS_INVALIDATED",
            {"count": len(rows), "reason": reason},
        )


def revise_preference(
    db: Session,
    negotiation: Negotiation,
    member: Member,
    command,
    *,
    restart=None,
) -> None:
    """修改一条已答标签；旧证据由事件记录，投影就地更新。"""
    from app.engine.events import append_event
    from app.engine.state import require_status

    require_status(
        negotiation,
        {
            "ASKING",
            "ANSWERING",
            "PRIVATE_CONFIRM",
            "BEST_EFFORT",
            "RETRY_OR_REVIEW",
        },
        "REVISION_NOT_ALLOWED",
    )
    if command.acceptable is None:
        raise invalid("REVISION_LABEL_REQUIRED", "修订必须给出接受或不接受")

    answers = db.scalars(
        select(QuestionAnswer)
        .where(
            QuestionAnswer.negotiation_id == negotiation.id,
            QuestionAnswer.member_id == member.id,
            QuestionAnswer.invalidated_at.is_(None),
        )
        .order_by(QuestionAnswer.id.desc())
    ).all()
    target = next(
        (row for row in answers if command.candidate_id in (row.evaluated_ids or [])),
        None,
    )
    if target is None:
        raise conflict("NO_EXISTING_LABEL", "本人尚未评价过该候选，请等待下一轮问题")

    acceptable = set(target.acceptable_ids or [])
    before = command.candidate_id in acceptable
    if command.acceptable:
        acceptable.add(command.candidate_id)
        top_id = target.top_id or command.candidate_id
    else:
        acceptable.discard(command.candidate_id)
        top_id = target.top_id
        if top_id == command.candidate_id:
            remaining = sorted(acceptable)
            top_id = remaining[0] if remaining else None
    if before == command.acceptable:
        return

    target.acceptable_ids = sorted(acceptable)
    target.top_id = top_id
    member.preference_version += 1
    negotiation.decision_config_version += 1
    invalidate_confirmations(db, negotiation, reason="preference_revised")
    append_event(
        db,
        negotiation,
        "PREFERENCE_REVISED",
        {
            "candidate_id": command.candidate_id,
            "acceptable": command.acceptable,
            "preference_version": member.preference_version,
        },
        actor_member_id=member.id,
        request_id=command.request_id,
    )
    if restart is not None and negotiation.status in {"PRIVATE_CONFIRM", "BEST_EFFORT"}:
        restart()
