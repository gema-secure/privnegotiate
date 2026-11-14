"""视图投影：公共页绝不出现私人字段；本人页只读本人数据。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.attributes import candidate_attribute_rows
from app.engine.loading import (
    attribute_state_map,
    candidate_dtos,
    constraint_dtos,
)
from app.engine.state import not_found
from app.constraints.compiler import pending_for_requirement
from app.llm.wording import render_question_with_llm
from app.models.business import (
    Candidate,
    CandidateAttribute,
    Concern,
    ConfirmedConstraint,
    Member,
    Negotiation,
    QuestionAnswer,
    QuestionBatch,
    ResultConfirmation,
    RoundResult,
)
from app.preference.acceptance import AnswerObservation, current_explicit_labels
from app.schemas.private_views import (
    MemberIdentityView,
    MyAcceptanceSummaryView,
    MyAnswerView,
    MyConcernView,
    MyConfirmationView,
    MyConstraintView,
    MyHistoryEntryView,
    MyPendingAttributeCheckView,
    MyQuestionBatchView,
    MyStateView,
)
from app.schemas.public_views import (
    PublicAttributeView,
    PublicCandidateView,
    PublicConditionView,
    PublicNegotiationView,
    PublicResultView,
)

COMPUTING_STATUSES = {"CHECKING_FEASIBILITY", "CLOSING", "COMPUTING", "UPDATING"}


def _conditions(negotiation: Negotiation) -> PublicConditionView:
    limits = (negotiation.config_json or {}).get("category_limits") or {}
    return PublicConditionView(
        budget_cents=negotiation.budget_cents,
        exact_count=negotiation.exact_count,
        round_limit=negotiation.round_limit,
        member_count=int((negotiation.config_json or {}).get("member_count", 3)),
        category_limits=dict(limits),
        condition_version=negotiation.constraint_version,
    )


def _candidate_view(
    row: Candidate, attributes: list[CandidateAttribute] | None = None
) -> PublicCandidateView:
    return PublicCandidateView(
        candidate_id=row.public_id,
        name=row.name,
        cost_cents=row.cost_cents,
        category=row.category,
        confirmed_attributes=dict(row.confirmed_attributes or {}),
        attributes=[
            PublicAttributeView(
                key=item.attribute_key, state=item.state, source=item.source
            )
            for item in (attributes or [])
        ],
    )


def _has_evidence(db: Session, negotiation_id: int) -> bool:
    rows = db.scalars(
        select(QuestionAnswer).where(
            QuestionAnswer.negotiation_id == negotiation_id,
            QuestionAnswer.mode == "explicit",
            QuestionAnswer.invalidated_at.is_(None),
        )
    ).all()
    return any(bool(row.evaluated_ids) for row in rows)


def _active_result(db: Session, negotiation: Negotiation) -> RoundResult | None:
    if negotiation.active_result_version is None:
        return None
    return db.scalars(
        select(RoundResult).where(
            RoundResult.negotiation_id == negotiation.id,
            RoundResult.result_version == negotiation.active_result_version,
        )
    ).one_or_none()


def build_public_view(db: Session, negotiation: Negotiation) -> PublicNegotiationView:
    candidates = db.scalars(
        select(Candidate)
        .where(Candidate.negotiation_id == negotiation.id, Candidate.active.is_(True))
        .order_by(Candidate.public_id)
    ).all()
    attribute_rows = candidate_attribute_rows(db, negotiation.id)
    result = _active_result(db, negotiation)
    evidence = _has_evidence(db, negotiation.id)

    recommendation = None
    if result is not None and result.candidate_ids:
        recommendation = PublicResultView(
            result_version=result.result_version,
            candidate_ids=list(result.candidate_ids),
            total_cost_cents=result.total_cost_cents,
            source="optimal" if result.solver_status == "OPTIMAL" else "feasible",
            is_baseline=negotiation.algorithm_profile != "mean_fair_v1",
        )

    reason = None
    if recommendation is None:
        if negotiation.status in COMPUTING_STATUSES:
            reason = "computing"
        elif negotiation.status in {"CREATED", "COLLECTING_BOTTOMLINE"}:
            reason = "collecting"
        elif negotiation.status in {"INFEASIBLE", "RETRY_OR_REVIEW", "ADJUSTING"}:
            reason = None
        elif not evidence:
            reason = "no_preference_evidence"
        else:
            reason = "collecting"
    elif not evidence:
        reason = "no_preference_evidence"

    if negotiation.status in {"PRIVATE_CONFIRM", "BEST_EFFORT"}:
        pending = "waiting_confirmation"
    elif negotiation.status == "CONCERN_COLLECTING":
        pending = "waiting_initiator"
    elif negotiation.status in {"ASKING", "ANSWERING"}:
        pending = "waiting_members"
    else:
        pending = "none"

    return PublicNegotiationView(
        negotiation_id=negotiation.id,
        title=negotiation.title,
        status=negotiation.status,
        round_no=negotiation.round_no,
        conditions=_conditions(negotiation),
        candidates=[
            _candidate_view(row, attribute_rows.get(row.id)) for row in candidates
        ],
        recommendation=recommendation,
        recommendation_pending_reason=reason,
        pending_action=pending,
    )


def _my_batch(db: Session, negotiation: Negotiation, member: Member):
    return db.scalars(
        select(QuestionBatch)
        .where(
            QuestionBatch.negotiation_id == negotiation.id,
            QuestionBatch.member_id == member.id,
            QuestionBatch.round_no == negotiation.round_no,
            QuestionBatch.status == "issued",
        )
        .order_by(QuestionBatch.id.desc())
    ).first()


def _my_answers(db: Session, negotiation: Negotiation, member: Member):
    return db.scalars(
        select(QuestionAnswer)
        .where(
            QuestionAnswer.negotiation_id == negotiation.id,
            QuestionAnswer.member_id == member.id,
            QuestionAnswer.invalidated_at.is_(None),
        )
        .order_by(QuestionAnswer.accepted_at, QuestionAnswer.id)
    ).all()


def _acceptance_summary(
    db: Session, negotiation: Negotiation, member: Member
) -> MyAcceptanceSummaryView:
    answers = _my_answers(db, negotiation, member)
    labels = current_explicit_labels(
        [
            AnswerObservation(
                member_id=row.member_id,
                evaluated_ids=tuple(row.evaluated_ids or ()),
                acceptable_ids=tuple(row.acceptable_ids or ()),
                top_id=row.top_id,
                mode=row.mode,
            )
            for row in answers
        ]
    )
    acceptable = sum(1 for value in labels.values() if value)
    unacceptable = sum(1 for value in labels.values() if not value)
    candidates = db.scalars(
        select(Candidate).where(
            Candidate.negotiation_id == negotiation.id, Candidate.active.is_(True)
        )
    ).all()
    unknown = max(0, len(candidates) - acceptable - unacceptable)
    return MyAcceptanceSummaryView(
        explicit_acceptable_count=acceptable,
        explicit_unacceptable_count=unacceptable,
        unknown_count=unknown,
        exact_count=negotiation.exact_count,
        acceptable_lt_k=acceptable < negotiation.exact_count,
    )


def build_my_view(
    db: Session,
    negotiation: Negotiation,
    member: Member,
    *,
    llm_client=None,
    wording_mode: str = "auto",
) -> MyStateView:
    batch = _my_batch(db, negotiation, member)
    question_batch = None
    if batch is not None:
        candidates = {
            row.public_id: row
            for row in db.scalars(
                select(Candidate).where(Candidate.negotiation_id == negotiation.id)
            ).all()
        }
        item_rows = [
            candidates[item]
            for item in (batch.displayed_ids or [])
            if item in candidates
        ]
        wording = None
        if item_rows:
            wording = render_question_with_llm(
                [row.name for row in item_rows],
                llm_client if wording_mode == "auto" else None,
            )
        question_batch = MyQuestionBatchView(
            question_batch_id=batch.batch_public_id,
            round_no=batch.round_no,
            items=[_candidate_view(row) for row in item_rows],
            deadline=batch.deadline,
            allow_skip=True,
            prompt_text=wording.text if wording else "",
            wording_source=wording.source if wording else "template",
            wording_degraded=(
                wording.degraded
                if wording is not None and wording_mode == "auto"
                else False
            ),
            wording_degraded_reason=(
                wording.degraded_reason
                if wording is not None and wording_mode == "auto"
                else None
            ),
        )

    constraints = db.scalars(
        select(ConfirmedConstraint).where(
            ConfirmedConstraint.negotiation_id == negotiation.id,
            ConfirmedConstraint.owner_member_id == member.id,
            ConfirmedConstraint.active.is_(True),
        )
    ).all()
    my_constraints = [
        MyConstraintView(
            constraint_id=row.constraint_id,
            kind=row.kind,
            candidate_ids=list((row.payload or {}).get("candidate_ids") or ()),
            category=(row.payload or {}).get("category"),
            attributes=dict((row.payload or {}).get("attributes") or {}),
            min_count=(row.payload or {}).get("min_count"),
            confirmed_version=row.confirmed_version,
        )
        for row in constraints
        if row.kind in {"exclude_item", "opt_out", "require_count"}
    ]

    confirmation = None
    if negotiation.active_result_version is not None:
        row = db.scalars(
            select(ResultConfirmation).where(
                ResultConfirmation.negotiation_id == negotiation.id,
                ResultConfirmation.result_version
                == negotiation.active_result_version,
                ResultConfirmation.member_id == member.id,
            )
        ).one_or_none()
        if row is not None and row.decision_config_version == negotiation.decision_config_version:
            confirmation = MyConfirmationView(
                result_version=row.result_version,
                decision_config_version=row.decision_config_version,
                decision="accept" if row.accept else "reject",
                decided_at=row.decided_at,
            )

    concerns = db.scalars(
        select(Concern)
        .where(
            Concern.negotiation_id == negotiation.id,
            Concern.member_id == member.id,
        )
        .order_by(Concern.id.desc())
    ).all()

    pending_checks: list[MyPendingAttributeCheckView] = []
    require_rows = [row for row in constraints if row.kind == "require_count"]
    if require_rows:
        candidate_rows = db.scalars(
            select(Candidate).where(
                Candidate.negotiation_id == negotiation.id,
                Candidate.active.is_(True),
            )
        ).all()
        dtos = candidate_dtos(
            candidate_rows, attribute_state_map(db, negotiation.id)
        )
        opt_out_ids: set[str] = set()
        excluded_ids: set[str] = set()
        for row in constraints:
            if row.kind == "opt_out":
                opt_out_ids.update((row.payload or {}).get("candidate_ids") or ())
            elif row.kind == "exclude_item":
                excluded_ids.update((row.payload or {}).get("candidate_ids") or ())
        eligible_ids = [
            item.candidate_id
            for item in dtos
            if item.candidate_id not in opt_out_ids
        ]
        for constraint in constraint_dtos(require_rows):
            check = pending_for_requirement(
                constraint,
                dtos,
                eligible_ids=eligible_ids,
                excluded_ids=excluded_ids,
            )
            if check is None:
                continue
            pending_checks.append(
                MyPendingAttributeCheckView(
                    constraint_id=check.constraint_id,
                    category=check.category,
                    attribute_keys=list(check.attribute_keys),
                    required_count=check.required_count,
                    confirmed_count=check.confirmed_count,
                    unconfirmed_candidate_ids=list(
                        check.unconfirmed_candidate_ids
                    ),
                )
            )

    return MyStateView(
        negotiation_id=negotiation.id,
        member=MemberIdentityView(
            member_id=member.id,
            display_name=member.display_name,
            role=member.role,
        ),
        status=negotiation.status,
        round_no=negotiation.round_no,
        conditions=_conditions(negotiation),
        decision_config_version=negotiation.decision_config_version,
        question_batch=question_batch,
        constraints=my_constraints,
        bottomline_status=member.bottomline_status,
        preference_version=member.preference_version,
        acceptance_summary=_acceptance_summary(db, negotiation, member),
        confirmation=confirmation,
        pending_attribute_checks=pending_checks,
        concerns=[
            MyConcernView(
                concern_id=row.id,
                result_version=row.result_version,
                text=row.original_text,
                summary=row.summary,
                status=row.status,
                created_at=row.created_at,
            )
            for row in concerns
        ],
    )


def build_history(
    db: Session, negotiation: Negotiation, member: Member
) -> list[MyHistoryEntryView]:
    batches = db.scalars(
        select(QuestionBatch)
        .where(
            QuestionBatch.negotiation_id == negotiation.id,
            QuestionBatch.member_id == member.id,
        )
        .order_by(QuestionBatch.round_no, QuestionBatch.id)
    ).all()
    answers = {
        row.batch_id: row
        for row in db.scalars(
            select(QuestionAnswer).where(
                QuestionAnswer.negotiation_id == negotiation.id,
                QuestionAnswer.member_id == member.id,
                QuestionAnswer.invalidated_at.is_(None),
            )
        ).all()
    }
    results = {
        row.round_no: row
        for row in db.scalars(
            select(RoundResult).where(RoundResult.negotiation_id == negotiation.id)
        ).all()
    }

    entries: list[MyHistoryEntryView] = []
    for batch in batches:
        answer = answers.get(batch.id)
        result = results.get(batch.round_no)
        entries.append(
            MyHistoryEntryView(
                round_no=batch.round_no,
                question_batch_id=batch.batch_public_id,
                displayed_ids=list(batch.displayed_ids or []),
                answer=(
                    MyAnswerView(
                        batch_id=batch.batch_public_id,
                        round_no=batch.round_no,
                        mode=answer.mode,
                        evaluated_ids=list(answer.evaluated_ids or []),
                        acceptable_ids=list(answer.acceptable_ids or []),
                        top_id=answer.top_id,
                        accepted_at=answer.accepted_at,
                    )
                    if answer is not None
                    else None
                ),
                result_version=result.result_version if result else None,
                result_candidate_ids=list(result.candidate_ids or []) if result else [],
                result_total_cost_cents=result.total_cost_cents if result else None,
            )
        )
    return entries


def load_member(db: Session, negotiation_id: int, member_id: int) -> Member:
    member = db.get(Member, member_id)
    if member is None or member.negotiation_id != negotiation_id or not member.active:
        raise not_found("MEMBER_NOT_FOUND", "成员不存在")
    return member
