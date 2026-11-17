"""底线收齐后的可行性检查与开局派题。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constraints.compiler import CompileError, compile_constraints
from app.constraints.validation import precheck
from app.engine.batches import create_round_batches
from app.engine.events import append_event
from app.engine.loading import (
    active_answers,
    answer_observations,
    candidate_dtos,
    constraint_dtos,
    member_dtos,
    attribute_state_map,
    opt_out_map,
)
from app.engine.scheduling import build_scheduling
from app.models.business import (
    Candidate,
    ConfirmedConstraint,
    Member,
    Negotiation,
)
from app.preference.acceptance import AcceptancePrior, rebuild_acceptance
from app.preference.estimates import UtilityConfig, estimate_utilities
from app.preference.relative_choice import RelativeChoicePrior, fit_relative_choice
from app.solver.cpsat_adapter import solve_mean_fair
from app.solver.mean_fair import SolveStatus

from app.engine.closing import solver_config


@dataclass(frozen=True, slots=True)
class RoundStartResult:
    """一次开局尝试的结果；no_questions 表示确实没有新的可作答题。"""

    status: str
    round_no: int
    round_issued: bool
    reason: str
    message: str = ""


def start_round(
    db: Session,
    negotiation: Negotiation,
    *,
    actor_member_id: int | None = None,
    ranking=None,
) -> RoundStartResult:
    """在已确认约束上检查可行域并开启下一轮；返回开局结果。"""
    candidates = db.scalars(
        select(Candidate)
        .where(Candidate.negotiation_id == negotiation.id)
        .order_by(Candidate.public_id)
    ).all()
    members = db.scalars(
        select(Member).where(
            Member.negotiation_id == negotiation.id, Member.active.is_(True)
        )
    ).all()
    constraints = db.scalars(
        select(ConfirmedConstraint).where(
            ConfirmedConstraint.negotiation_id == negotiation.id,
            ConfirmedConstraint.active.is_(True),
        )
    ).all()

    negotiation.status = "CHECKING_FEASIBILITY"
    attribute_map = attribute_state_map(db, negotiation.id)
    try:
        model = compile_constraints(
            constraint_dtos(constraints),
            candidate_dtos(candidates, attribute_map),
            member_dtos(members),
        )
    except CompileError as exc:
        negotiation.status = "RETRY_OR_REVIEW"
        append_event(
            db,
            negotiation,
            "FEASIBILITY_CHECKED",
            {"outcome": "model_invalid", "detail": str(exc)},
            actor_member_id=actor_member_id,
        )
        return RoundStartResult(
            negotiation.status, negotiation.round_no, False, "model_invalid", str(exc)
        )

    report = precheck(model)
    if report.definitely_infeasible:
        negotiation.status = "INFEASIBLE"
        message = "；".join(issue.detail for issue in report.issues)
        append_event(
            db,
            negotiation,
            "FEASIBILITY_CHECKED",
            {
                "outcome": "infeasible",
                "codes": [issue.code for issue in report.issues],
            },
            actor_member_id=actor_member_id,
        )
        return RoundStartResult(
            negotiation.status, negotiation.round_no, False, "infeasible", message
        )

    zero_mu = [[0.0] * model.candidate_count for _ in range(model.member_count)]
    result = solve_mean_fair(zero_mu, model, solver_config(negotiation))
    reason = "issued"
    message = ""
    if result.status is SolveStatus.INFEASIBLE:
        negotiation.status = "INFEASIBLE"
        reason = "infeasible"
        message = result.message
    elif result.status in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE):
        next_round = negotiation.round_no + 1
        candidate_tuple = candidate_dtos(candidates, attribute_map)
        member_tuple = member_dtos(members)
        constraint_tuple = constraint_dtos(constraints)
        opt_out = opt_out_map(constraint_tuple)
        answers = answer_observations(active_answers(db, negotiation.id))
        config = negotiation.config_json or {}
        beta = config.get("beta") or {}
        pl = config.get("pl") or {}
        w_top = float(config.get("w_top", 1.0))
        acceptance = rebuild_acceptance(
            answers,
            {item.candidate_id: item.category for item in candidate_tuple if item.active},
            AcceptancePrior(
                p0=float(beta.get("p0", 0.5)),
                kappa=float(beta.get("kappa", 2.0)),
            ),
        )
        relative = fit_relative_choice(
            answers, RelativeChoicePrior(sigma_v=float(pl.get("sigma_v", 1.0)))
        )
        estimate = estimate_utilities(
            acceptance,
            relative,
            opt_out,
            UtilityConfig(
                w_top=w_top,
                quadrature_nodes=int(config.get("quadrature_nodes", 15)),
            ),
            0,
            extra_member_ids=[item.member_id for item in member_tuple],
        )
        artifacts = build_scheduling(
            estimate,
            answers,
            model,
            opt_out,
            result.candidate_ids,
            solver_config(negotiation),
            config,
            round_no=next_round,
        )
        created = create_round_batches(
            db,
            negotiation,
            round_no=next_round,
            candidates=candidate_tuple,
            members=member_tuple,
            constraints=constraint_tuple,
            ranking=ranking,
            plan=artifacts.plan,
        )
        if created:
            negotiation.round_no = next_round
            negotiation.status = "ASKING"
            message = f"第 {next_round} 轮问题已发出"
        else:
            negotiation.status = "BEST_EFFORT"
            reason = "no_questions"
            message = "所有可选候选都已被成员评价，没有新的可作答题"
    else:
        negotiation.status = "RETRY_OR_REVIEW"
        reason = "solver_retry"
        message = result.message

    append_event(
        db,
        negotiation,
        "FEASIBILITY_CHECKED",
        {
            "outcome": result.status.value,
            "status": negotiation.status,
            "round_no": negotiation.round_no,
            "round_issued": reason == "issued",
            "reason": reason,
            "attribute_pending": [
                check.as_dict() for check in model.pending_checks
            ],
            "advisories": [
                issue.code for issue in report.issues if not issue.definite
            ],
        },
        actor_member_id=actor_member_id,
    )
    return RoundStartResult(
        negotiation.status,
        negotiation.round_no,
        reason == "issued",
        reason,
        message,
    )
