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

# 公共作用域的整组避开仍由某位成员提交；归属只认该标记，不绑定数据库 owner。
SOURCE_MEMBER_KEY = "_source_member_id"


def proposer_member_id(row: ConfirmedConstraint) -> int | None:
    """返回约束提出者；兼容早期把提出者写在 owner 列的历史行。"""
    if row.kind == ConstraintKind.EXCLUDE_ITEM.value:
        source = (row.payload or {}).get(SOURCE_MEMBER_KEY)
        if source is not None:
            return source
    return row.owner_member_id


_PUBLIC_CONDITION_KINDS = frozenset(
    {
        ConstraintKind.BUDGET.value,
        ConstraintKind.EXACT_COUNT.value,
        ConstraintKind.CATEGORY_COUNT.value,
    }
)


def _deactivate_public_conditions(db: Session, negotiation_id: int) -> None:
    """只替换公共条件，不触碰成员提交的整组避开。"""
    stmt = select(ConfirmedConstraint).where(
        ConfirmedConstraint.negotiation_id == negotiation_id,
        ConfirmedConstraint.active.is_(True),
        ConfirmedConstraint.owner_member_id.is_(None),
        ConfirmedConstraint.kind.in_(tuple(_PUBLIC_CONDITION_KINDS)),
    )
    for row in db.scalars(stmt).all():
        row.active = False


def _deactivate_member_constraints(db: Session, negotiation_id: int, member_id: int) -> None:
    """停用本人旧底线：成员级按 owner 停用，公共整组避开设提交来源停用。"""
    stmt = select(ConfirmedConstraint).where(
        ConfirmedConstraint.negotiation_id == negotiation_id,
        ConfirmedConstraint.active.is_(True),
    )
    for row in db.scalars(stmt).all():
        if row.owner_member_id == member_id:
            row.active = False
            continue
        if row.owner_member_id is not None or row.kind != ConstraintKind.EXCLUDE_ITEM.value:
            continue
        source = (row.payload or {}).get(SOURCE_MEMBER_KEY)
        if source == member_id:
            row.active = False
        elif source is None:
            # 兼容修复前写入的历史行：只认服务端生成、带本人前缀的默认 ID。
            if row.constraint_id.startswith(f"m{member_id}_"):
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
    source_member_id: int | None = None,
) -> None:
    constraint = DslConstraint(
        constraint_id=constraint_id,
        kind=kind,
        scope=scope,
        owner_member_id=owner_member_id,
        confirmed_version=version,
        **payload,
    )
    try:
        validate_constraint(constraint)
    except DslError as exc:
        raise invalid("CONSTRAINT_INVALID", str(exc)) from exc
    stored = dict(payload)
    if source_member_id is not None:
        stored[SOURCE_MEMBER_KEY] = source_member_id
    db.add(
        ConfirmedConstraint(
            negotiation_id=negotiation.id,
            constraint_id=constraint_id,
            # 公共整组避开不绑定 owner，提出者只写入 payload 的 _source_member_id。
            owner_member_id=owner_member_id,
            scope=scope.value,
            kind=kind.value,
            payload=stored,
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
    _deactivate_public_conditions(db, negotiation.id)
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
    _deactivate_member_constraints(db, negotiation.id, member.id)

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
        if collision is not None and proposer_member_id(collision) != member.id:
            raise invalid("CONSTRAINT_ID_INVALID", "请使用本人底线标识")
        kind = ConstraintKind(draft.kind.value)
        # 整组避开是公共约束，必须整组生效且不得绑定成员；
        # 本人不参与与替代要求才是成员级底线，两条路径互不替代。
        is_group_exclude = kind is ConstraintKind.EXCLUDE_ITEM
        payload: dict = {}
        if kind in (
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
            kind=kind,
            scope=ConstraintScope.PUBLIC if is_group_exclude else ConstraintScope.MEMBER,
            owner_member_id=None if is_group_exclude else member.id,
            payload=payload,
            version=next_version,
            source_member_id=member.id if is_group_exclude else None,
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
